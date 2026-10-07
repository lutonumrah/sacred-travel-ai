"""Booking lifecycle and payment orchestration."""

import logging
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify, notify_many, notify_managers
from core.services import log_audit

from . import emails, payments
from .models import Booking, BookingStatus, Payment, PaymentStatus, WebhookEvent

logger = logging.getLogger(__name__)


class BookingError(Exception):
    """A booking request that cannot be honoured; the message is safe to show customers."""


def generate_booking_number():
    """`STA-YYYYMMDD-0007`, sequential within the day."""
    today = timezone.localdate()
    prefix = f"STA-{today:%Y%m%d}"
    todays_count = Booking.objects.filter(booking_number__startswith=prefix).count()
    for offset in range(1, 1000):
        candidate = f"{prefix}-{todays_count + offset:04d}"
        if not Booking.objects.filter(booking_number=candidate).exists():
            return candidate
    raise RuntimeError("Could not allocate a booking number for today.")


def price_booking(subtotal, *, tax_percent=None):
    """Return (subtotal, tax, total) rounded to paise."""
    subtotal = Decimal(str(subtotal or 0))
    percent = Decimal(str(
        tax_percent if tax_percent is not None else getattr(settings, "BOOKING_TAX_PERCENT", 5)
    ))
    tax = (subtotal * percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return subtotal, tax, subtotal + tax


@transaction.atomic
def create_booking(*, booking, actor=None, request=None):
    """Finalise pricing, allocate a number and save a pending booking."""
    if not booking.booking_number:
        booking.booking_number = generate_booking_number()
    subtotal, tax, total = price_booking(booking.subtotal)
    booking.subtotal, booking.tax_amount, booking.total_amount = subtotal, tax, total
    if actor is not None and getattr(actor, "is_authenticated", False):
        booking.created_by = actor
    if not booking.status:
        booking.status = BookingStatus.PENDING
    booking.save()

    log_audit(
        actor=actor,
        action="booking.create",
        entity=booking,
        metadata={"number": booking.booking_number, "total": str(total)},
        request=request,
    )
    _advance_lead(booking, actor=actor, request=request)
    notify_managers(
        notification_type="payment",
        title=f"New booking {booking.booking_number}",
        body=f"{booking.product_name} · {booking.currency} {total}",
        link=reverse("bookings:detail", args=[booking.pk]),
        metadata={"booking_id": booking.pk},
        exclude=actor if (actor and getattr(actor, "is_authenticated", False)) else None,
    )
    return booking


def _advance_lead(booking, *, status=None, actor=None, request=None):
    """Keep the CRM pipeline in step with the booking."""
    from crm import services as crm_services
    from crm.models import LeadStatus

    if not booking.lead_id:
        return
    if status is None:
        # A new booking only moves the pipeline forward; a converted or lost
        # lead keeps its status until someone changes it by hand.
        crm_services.advance_status(
            lead=booking.lead,
            status=LeadStatus.PAYMENT_PENDING,
            note=f"Booking {booking.booking_number} raised",
            actor=actor,
            request=request,
        )
        return
    crm_services.change_status(
        lead=booking.lead, status=status, actor=actor, request=request
    )


@transaction.atomic
def create_payment_order(*, booking, actor=None, request=None):
    """Open (or reuse) a Razorpay order for a booking."""
    existing = booking.payments.filter(
        status__in=[PaymentStatus.CREATED, PaymentStatus.PENDING]
    ).first()
    if existing and existing.razorpay_order_id:
        return existing

    order = payments.create_order(
        amount=booking.total_amount,
        currency=booking.currency,
        receipt=booking.booking_number,
        notes={"booking": booking.booking_number, "product": booking.product_name},
    )
    payment = Payment.objects.create(
        booking=booking,
        razorpay_order_id=order.get("id", ""),
        amount=booking.total_amount,
        currency=booking.currency,
        status=PaymentStatus.CREATED,
        raw_response=order,
    )
    log_audit(
        actor=actor,
        action="payment.order_create",
        entity=booking,
        metadata={"order_id": payment.razorpay_order_id, "simulated": not payments.is_live()},
        request=request,
    )
    return payment


# Payments that already hold the customer's money; never settle or fail these again.
SETTLED_PAYMENT_STATUSES = (PaymentStatus.SUCCESS, PaymentStatus.REFUNDED)
OPEN_PAYMENT_STATUSES = (PaymentStatus.CREATED, PaymentStatus.PENDING)


def _booking_link(booking):
    return reverse("bookings:detail", args=[booking.pk])


def _notify_booking_people(*, booking, title, body="", actor=None):
    """Managers, whoever raised the booking and the lead owner — minus the actor."""
    from django.contrib.auth import get_user_model

    people = {
        user.pk: user
        for user in get_user_model().objects.filter(
            is_active=True, is_active_employee=True, role__in=["admin", "manager"]
        )
    }
    for user in (booking.created_by, booking.lead.assigned_to if booking.lead_id else None):
        if user is not None:
            people[user.pk] = user
    if actor is not None and getattr(actor, "is_authenticated", False):
        people.pop(actor.pk, None)
    notify_many(
        recipients=list(people.values()),
        notification_type="payment",
        title=title,
        body=body,
        link=_booking_link(booking),
        metadata={"booking_id": booking.pk},
    )


def _amount_matches(payment, entity):
    """Gateway amount (paise) and currency equal what this payment asked for."""
    amount = entity.get("amount")
    currency = (entity.get("currency") or "").upper()
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False
    return amount == payments.to_paise(payment.amount) and currency == payment.currency.upper()


def _flag_amount_mismatch(*, payment, entity, source, actor=None, request=None):
    booking = payment.booking
    details = {
        "order_id": payment.razorpay_order_id,
        "payment_id": entity.get("id", ""),
        "expected_paise": payments.to_paise(payment.amount),
        "expected_currency": payment.currency,
        "received_paise": entity.get("amount"),
        "received_currency": entity.get("currency"),
        "source": source,
    }
    logger.error("Razorpay amount mismatch on %s: %s", booking.booking_number, details)
    log_audit(
        actor=actor,
        action="payment.amount_mismatch",
        entity=booking,
        metadata=details,
        request=request,
    )
    notify_managers(
        notification_type="payment",
        title=f"Payment amount mismatch on {booking.booking_number}",
        body=(
            f"Expected {payment.currency} {payment.amount}, Razorpay reported "
            f"{entity.get('currency')} {entity.get('amount')} (paise). Booking not confirmed."
        ),
        link=_booking_link(booking),
        metadata={"booking_id": booking.pk},
    )


@transaction.atomic
def _settle_payment(*, payment, payment_id, signature="", actor=None, request=None):
    """Record captured money and confirm the booking if it is still open."""
    payment.razorpay_payment_id = payment_id or payment.razorpay_payment_id
    if signature:
        payment.razorpay_signature = signature
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = timezone.now()
    payment.save(
        update_fields=[
            "razorpay_payment_id",
            "razorpay_signature",
            "status",
            "paid_at",
            "updated_at",
        ]
    )
    booking = payment.booking
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED):
        # The customer paid an order we had already closed: someone must refund it.
        log_audit(
            actor=actor,
            action="payment.after_cancel",
            entity=booking,
            metadata={"order_id": payment.razorpay_order_id, "payment_id": payment_id},
            request=request,
        )
        notify_managers(
            notification_type="payment",
            title=f"Payment received for {booking.get_status_display().lower()} booking "
            f"{booking.booking_number}",
            body=f"{payment.currency} {payment.amount} — refund it in Razorpay.",
            link=_booking_link(booking),
            metadata={"booking_id": booking.pk},
        )
        return payment
    if booking.status != BookingStatus.CONFIRMED:
        mark_booking_paid(booking=booking, actor=actor, request=request)
    return payment


@transaction.atomic
def verify_payment(*, order_id, payment_id, signature, actor=None, request=None):
    """Verify a checkout callback and settle the booking.

    Returns `(payment, error)`. `error` is None on success.
    """
    payment = Payment.objects.select_related("booking").filter(
        razorpay_order_id=order_id
    ).first()
    if payment is None:
        return None, "No payment matches that order."

    if payment.status in SETTLED_PAYMENT_STATUSES:
        # Razorpay retries callbacks; settling twice must be a no-op.
        return payment, None

    if not payments.verify_payment_signature(
        order_id=order_id, payment_id=payment_id, signature=signature
    ):
        # Leave the payment untouched: anyone can post garbage here, and failing
        # the order would block the customer's genuine payment.
        log_audit(
            actor=actor,
            action="payment.verify_failed",
            entity=payment.booking,
            metadata={"order_id": order_id},
            request=request,
        )
        return payment, "Signature verification failed."

    try:
        gateway = payments.fetch_payment(payment_id)
    except Exception:
        logger.exception("Could not fetch Razorpay payment %s.", payment_id)
        return payment, (
            "Could not confirm the payment with Razorpay yet. "
            "It will update automatically once Razorpay notifies us."
        )
    if gateway is not None:
        if gateway.get("order_id") != order_id or not _amount_matches(payment, gateway):
            _flag_amount_mismatch(
                payment=payment,
                entity={"id": payment_id, **gateway},
                source="checkout",
                actor=actor,
                request=request,
            )
            return payment, (
                "The paid amount does not match this booking. A manager has been notified."
            )
        if gateway.get("status") != "captured":
            return payment, (
                "The payment is not captured yet. It will update once Razorpay confirms it."
            )

    _settle_payment(
        payment=payment, payment_id=payment_id, signature=signature, actor=actor, request=request
    )
    return payment, None


@transaction.atomic
def mark_booking_paid(*, booking, actor=None, request=None):
    from crm.models import LeadStatus

    booking.status = BookingStatus.CONFIRMED
    booking.confirmed_at = timezone.now()
    booking.save(update_fields=["status", "confirmed_at", "updated_at"])

    log_audit(
        actor=actor,
        action="booking.confirmed",
        entity=booking,
        metadata={"number": booking.booking_number},
        request=request,
    )
    _advance_lead(booking, status=LeadStatus.CONVERTED, actor=actor, request=request)
    if booking.conversation_id:
        # The widget shows system messages, so an open chat sees this too.
        from conversations.models import MessageSender
        from conversations.services import post_message

        post_message(
            conversation=booking.conversation,
            sender_type=MessageSender.SYSTEM,
            content=f"Payment received — booking {booking.booking_number} confirmed.",
            metadata={"booking_id": booking.pk, "event": "booking_confirmed"},
        )

    link = _booking_link(booking)
    if booking.created_by:
        notify(
            recipient=booking.created_by,
            notification_type="payment",
            title=f"Payment received for {booking.booking_number}",
            body=f"{booking.currency} {booking.total_amount}",
            link=link,
            metadata={"booking_id": booking.pk},
        )
    notify_managers(
        notification_type="payment",
        title=f"Booking confirmed: {booking.booking_number}",
        body=f"{booking.product_name} · {booking.currency} {booking.total_amount}",
        link=link,
        metadata={"booking_id": booking.pk},
        exclude=booking.created_by,
    )
    emails.send_confirmation(booking=booking, actor=actor)
    return booking


@transaction.atomic
def mark_payment_failed(*, payment, reason="", actor=None, request=None):
    booking = payment.booking
    if payment.status not in OPEN_PAYMENT_STATUSES:
        # Razorpay allows retries on one order, so a failed attempt can arrive after
        # the successful one (or after we closed the order). Nothing to downgrade.
        log_audit(
            actor=actor,
            action="payment.failed_ignored",
            entity=booking,
            metadata={"reason": reason, "payment_status": payment.status},
            request=request,
        )
        return payment

    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason[:255]
    payment.save(update_fields=["status", "failure_reason", "updated_at"])
    if booking.status == BookingStatus.PENDING:
        booking.status = BookingStatus.FAILED
        booking.save(update_fields=["status", "updated_at"])
    log_audit(
        actor=actor,
        action="payment.failed",
        entity=booking,
        metadata={"reason": reason},
        request=request,
    )
    notify_managers(
        notification_type="payment",
        title=f"Payment failed for {booking.booking_number}",
        body=reason,
        link=_booking_link(booking),
        metadata={"booking_id": booking.pk},
    )
    return payment


def _lead_has_other_bookings(booking, statuses):
    return (
        Booking.objects.filter(lead_id=booking.lead_id, status__in=statuses)
        .exclude(pk=booking.pk)
        .exists()
    )


@transaction.atomic
def cancel_booking(*, booking, actor=None, request=None, reason=""):
    from crm import services as crm_services
    from crm.models import LeadStatus

    if booking.status == BookingStatus.CANCELLED:
        return booking
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = timezone.now()
    booking.save(update_fields=["status", "cancelled_at", "updated_at"])

    # Close open orders so a late checkout can't confirm a cancelled booking.
    closed = booking.payments.filter(status__in=OPEN_PAYMENT_STATUSES).update(
        status=PaymentStatus.CANCELLED,
        failure_reason=f"Booking cancelled{': ' + reason if reason else ''}"[:255],
        updated_at=timezone.now(),
    )
    log_audit(
        actor=actor,
        action="booking.cancel",
        entity=booking,
        metadata={"reason": reason, "payments_closed": closed},
        request=request,
    )

    lead = booking.lead
    if (
        lead is not None
        and lead.status == LeadStatus.PAYMENT_PENDING
        and not _lead_has_other_bookings(booking, [BookingStatus.PENDING, BookingStatus.DRAFT])
    ):
        crm_services.change_status(
            lead=lead, status=LeadStatus.INTERESTED, actor=actor, request=request
        )

    body = reason or booking.product_name
    if booking.payments.filter(status=PaymentStatus.SUCCESS).exists():
        body += " · A captured payment exists — refund it in Razorpay."
    _notify_booking_people(
        booking=booking,
        title=f"Booking cancelled: {booking.booking_number}",
        body=body,
        actor=actor,
    )
    emails.send_cancellation(booking=booking, reason=reason, actor=actor)
    return booking


def _is_full_refund(payment, refund, payment_entity):
    refund_status = payment_entity.get("refund_status")
    if refund_status:
        return refund_status == "full"
    try:
        return int(refund["amount"]) >= payments.to_paise(payment.amount)
    except (KeyError, TypeError, ValueError):
        return True


@transaction.atomic
def record_refund(*, payment, refund=None, payment_entity=None, actor=None, request=None):
    """Apply a processed Razorpay refund to the payment, booking and lead."""
    from crm import services as crm_services
    from crm.models import LeadStatus

    refund = refund or {}
    booking = payment.booking
    if not _is_full_refund(payment, refund, payment_entity or {}):
        log_audit(
            actor=actor,
            action="payment.partial_refund",
            entity=booking,
            metadata={"refund_id": refund.get("id", ""), "amount_paise": refund.get("amount")},
            request=request,
        )
        notify_managers(
            notification_type="payment",
            title=f"Partial refund on {booking.booking_number}",
            body=f"{payment.currency} {int(refund.get('amount') or 0) / 100:.2f} refunded.",
            link=_booking_link(booking),
            metadata={"booking_id": booking.pk},
        )
        emails.send_refund(
            booking=booking,
            amount=Decimal(int(refund.get("amount") or 0)) / 100,
            currency=payment.currency,
            payment_reference=payment.razorpay_payment_id,
            actor=actor,
        )
        return "partial refund recorded"

    if payment.status == PaymentStatus.REFUNDED:
        return "refund recorded"
    payment.status = PaymentStatus.REFUNDED
    payment.save(update_fields=["status", "updated_at"])
    emails.send_refund(
        booking=booking,
        amount=payment.amount,
        currency=payment.currency,
        payment_reference=payment.razorpay_payment_id,
        actor=actor,
    )

    # Another settled payment on the same booking still pays for it.
    if booking.payments.filter(status=PaymentStatus.SUCCESS).exists():
        return "refund recorded"

    booking.status = BookingStatus.REFUNDED
    booking.cancelled_at = booking.cancelled_at or timezone.now()
    booking.save(update_fields=["status", "cancelled_at", "updated_at"])
    log_audit(
        actor=actor,
        action="booking.refunded",
        entity=booking,
        metadata={"refund_id": refund.get("id", ""), "order_id": payment.razorpay_order_id},
        request=request,
    )

    lead = booking.lead
    if (
        lead is not None
        and lead.status in (LeadStatus.CONVERTED, LeadStatus.PAYMENT_PENDING)
        and not _lead_has_other_bookings(booking, [BookingStatus.CONFIRMED, BookingStatus.PAID])
    ):
        crm_services.change_status(
            lead=lead,
            status=LeadStatus.LOST,
            lost_reason=f"Booking {booking.booking_number} refunded",
            actor=actor,
            request=request,
        )

    _notify_booking_people(
        booking=booking,
        title=f"Booking refunded: {booking.booking_number}",
        body=f"{payment.currency} {payment.amount} returned to the customer.",
        actor=actor,
    )
    return "refund recorded"


def _entity(body, name):
    entity = (body.get(name) or {}).get("entity") or {}
    return entity if isinstance(entity, dict) else {}


def handle_webhook(*, event, payload, actor=None, request=None):
    """Apply a verified Razorpay webhook event. Returns a short status string."""
    body = (payload.get("payload") or {}) if isinstance(payload, dict) else {}
    entity = _entity(body, "payment")
    order = _entity(body, "order")
    refund = _entity(body, "refund")
    order_id = entity.get("order_id") or order.get("id", "")
    payment_id = entity.get("id") or refund.get("payment_id", "")
    if not (order_id or payment_id):
        return "ignored: no order id"

    payment = None
    if order_id:
        payment = Payment.objects.select_related("booking").filter(
            razorpay_order_id=order_id
        ).first()
    if payment is None and payment_id:
        # Refund events may only carry the payment id.
        payment = Payment.objects.select_related("booking").filter(
            razorpay_payment_id=payment_id
        ).first()
    if payment is None:
        return "ignored: unknown order"

    payment.raw_response = payload
    payment.method = entity.get("method", "") or payment.method
    payment.save(update_fields=["raw_response", "method", "updated_at"])

    if event in ("payment.captured", "order.paid"):
        if payment.status in SETTLED_PAYMENT_STATUSES:
            return "payment captured"
        if not entity and order:
            # order.paid without a payment entity: fall back to the order totals.
            entity = {"amount": order.get("amount_paid"), "currency": order.get("currency")}
        if not _amount_matches(payment, entity):
            _flag_amount_mismatch(
                payment=payment, entity=entity, source=event, actor=actor, request=request
            )
            return "ignored: amount mismatch"
        _settle_payment(payment=payment, payment_id=payment_id, actor=actor, request=request)
        return "payment captured"

    if event == "payment.failed":
        mark_payment_failed(
            payment=payment,
            reason=entity.get("error_description", "Reported failed by Razorpay"),
            actor=actor,
            request=request,
        )
        return "payment failed"

    if event == "refund.processed":
        return record_refund(
            payment=payment, refund=refund, payment_entity=entity, actor=actor, request=request
        )

    return f"ignored: {event}"


def process_webhook(*, event_id, event, payload, request=None):
    """Apply a webhook once per Razorpay event id; repeats are acknowledged and ignored."""
    with transaction.atomic():
        record = None
        if event_id:
            record, created = WebhookEvent.objects.get_or_create(
                event_id=event_id[:100], defaults={"event": event[:60]}
            )
            if not created:
                return "ignored: duplicate event"
        # An exception rolls back the event row too, so Razorpay's retry is processed.
        result = handle_webhook(event=event, payload=payload, request=request)
        if record is not None:
            record.result = result[:120]
            record.save(update_fields=["result", "updated_at"])
    return result


# --------------------------------------------------------------------------
# Pricing an inventory item
# --------------------------------------------------------------------------
#
# The subtotal is always computed here from the inventory row, never taken from
# the client, in the same units the chat quotes (`serialize_item` price labels):
#
#   hotel    base_price is the quoted "per night" rate: price × nights,
#            nights = check-out − check-in (at least one). Room allocation for
#            big parties is left to the consultant, as in the chat quote.
#   car      daily_price × rental days, counting the pick-up and drop-off day
#            (12th → 14th is 3 days); the car must seat every traveller
#   package  base_price is per person: price × travellers; the end date follows
#            from the package length, whatever the customer typed
#
# Tax is added on top by `create_booking` via `price_booking`.

MAX_TRAVELERS = 50
MAX_STAY_DAYS = 60


def _money_text(currency, amount):
    return f"{currency} {Decimal(amount):,.0f}"


def quote_item(*, inventory_type, item, travel_start, travel_end=None, travelers=1):
    """Price an inventory item for given dates and party size.

    Returns a dict with `subtotal`, the effective dates and a readable breakdown.
    Raises BookingError when the request does not make sense for this product.
    """
    from inventory.models import InventoryType

    if not travel_start:
        raise BookingError("Choose a travel date.")
    if travel_start < timezone.localdate():
        raise BookingError("The travel date is in the past.")
    if not 1 <= int(travelers or 0) <= MAX_TRAVELERS:
        raise BookingError(f"Travellers must be between 1 and {MAX_TRAVELERS}.")
    travelers = int(travelers)
    currency = item.currency

    if inventory_type == InventoryType.HOTEL:
        if not travel_end or travel_end <= travel_start:
            raise BookingError("Check-out must be after check-in.")
        nights = (travel_end - travel_start).days
        if nights > MAX_STAY_DAYS:
            raise BookingError(f"Stays longer than {MAX_STAY_DAYS} nights need a consultant.")
        unit_price = item.base_price
        quantity = nights
        description = (
            f"{nights} night{'s' if nights > 1 else ''} × {_money_text(currency, unit_price)}"
        )
    elif inventory_type == InventoryType.CAR:
        travel_end = travel_end or travel_start
        if travel_end < travel_start:
            raise BookingError("The drop-off date cannot be before the pick-up date.")
        if item.seats < travelers:
            raise BookingError(
                f"{item.name} seats {item.seats}; please pick a larger vehicle for {travelers}."
            )
        days = (travel_end - travel_start).days + 1
        if days > MAX_STAY_DAYS:
            raise BookingError(f"Rentals longer than {MAX_STAY_DAYS} days need a consultant.")
        unit_price = item.daily_price
        quantity = days
        description = f"{days} day{'s' if days > 1 else ''} × {_money_text(currency, unit_price)}"
    elif inventory_type == InventoryType.PACKAGE:
        travel_end = travel_start + timedelta(days=max(item.duration_days - 1, 0))
        unit_price = item.base_price
        quantity = travelers
        description = (
            f"{travelers} traveller{'s' if travelers > 1 else ''}"
            f" × {_money_text(currency, unit_price)} per person"
        )
    else:
        raise BookingError("This item cannot be booked online.")

    if not unit_price or unit_price <= 0:
        raise BookingError("This option is priced on request — a consultant will quote it.")
    return {
        "subtotal": (Decimal(unit_price) * quantity).quantize(Decimal("0.01")),
        "currency": currency,
        "travel_start": travel_start,
        "travel_end": travel_end,
        "travelers": travelers,
        "unit_price": str(unit_price),
        "quantity": quantity,
        "description": description,
    }


# --------------------------------------------------------------------------
# Customer payment links
# --------------------------------------------------------------------------


def payment_link_ttl():
    return timedelta(days=getattr(settings, "PAYMENT_LINK_TTL_DAYS", 7))


def has_live_payment_link(booking):
    return bool(
        booking.payment_token
        and booking.payment_token_expires_at
        and booking.payment_token_expires_at > timezone.now()
    )


def issue_payment_link(*, booking, actor=None, request=None):
    """Mint a fresh no-login payment token. The previous link stops working."""
    booking.payment_token = secrets.token_urlsafe(32)
    booking.payment_token_expires_at = timezone.now() + payment_link_ttl()
    booking.save(update_fields=["payment_token", "payment_token_expires_at", "updated_at"])
    # The token itself is a credential: never write it to the audit log.
    log_audit(
        actor=actor,
        action="booking.payment_link_issue",
        entity=booking,
        metadata={"expires_at": booking.payment_token_expires_at.isoformat()},
        request=request,
    )
    return booking


def payment_url(booking, request=None):
    path = reverse("public:pay", args=[booking.payment_token])
    return request.build_absolute_uri(path) if request is not None else path


def open_checkout(*, booking, request=None):
    """Create (or reuse) the gateway order for a customer paying by link."""
    if not booking.is_payable:
        raise BookingError(
            f"This booking is {booking.get_status_display().lower()} and cannot be paid."
        )
    return create_payment_order(booking=booking, request=request)


def simulate_checkout(*, booking, request=None):
    """Test-mode payment through the real verify path. Returns `(payment, error)`."""
    if payments.is_live():
        raise BookingError("Simulated payments are disabled while live keys are configured.")
    payment = open_checkout(booking=booking, request=request)
    payment_id, signature = payments.simulate_payment(payment.razorpay_order_id)
    return verify_payment(
        order_id=payment.razorpay_order_id,
        payment_id=payment_id,
        signature=signature,
        request=request,
    )


# --------------------------------------------------------------------------
# Booking straight from a chat recommendation
# --------------------------------------------------------------------------


@transaction.atomic
def booking_from_recommendation(
    *,
    recommendation,
    customer,
    travel_start,
    travel_end=None,
    travelers=1,
    guest=None,
    actor=None,
    request=None,
):
    """Turn a recommendation the customer (or an agent for them) picked into a pending booking.

    Returns `(booking, created)`. Asking twice for the same thing returns the
    open booking instead of raising a duplicate. `guest` is the name / email /
    phone typed in the chat, used on the pay page and for the customer email.
    A new booking's payment link is emailed to the customer.
    """
    from crm import services as crm_services
    from crm.models import LeadStatus
    from conversations.models import MessageSender
    from conversations.services import post_message
    from inventory.selectors import get_inventory_object, is_hidden_on

    conversation = recommendation.conversation
    item = get_inventory_object(recommendation.inventory_type, recommendation.object_id)
    if (
        item is None
        or not item.is_active
        or is_hidden_on(conversation.website, recommendation.inventory_type, item.pk)
    ):
        raise BookingError("Sorry, this option is no longer available.")

    quote = quote_item(
        inventory_type=recommendation.inventory_type,
        item=item,
        travel_start=travel_start,
        travel_end=travel_end,
        travelers=travelers,
    )

    existing = Booking.objects.filter(
        recommendation=recommendation,
        customer=customer,
        status=BookingStatus.PENDING,
        travel_start=quote["travel_start"],
        travel_end=quote["travel_end"],
        travelers_count=quote["travelers"],
    ).first()
    if existing is not None:
        if not has_live_payment_link(existing):
            issue_payment_link(booking=existing, actor=actor, request=request)
        return existing, False

    if not recommendation.is_selected:
        recommendation.is_selected = True
        recommendation.save(update_fields=["is_selected", "updated_at"])
    if conversation.lead_id:
        crm_services.advance_status(
            lead=conversation.lead,
            status=LeadStatus.INTERESTED,
            note=f"Selected {item.name} in the chat",
            actor=actor,
            request=request,
        )

    booking = Booking(
        website=conversation.website,
        customer=customer,
        lead=conversation.lead,
        conversation=conversation,
        recommendation=recommendation,
        product_type=recommendation.inventory_type,
        product_id=item.pk,
        product_name=item.name,
        travel_start=quote["travel_start"],
        travel_end=quote["travel_end"],
        travelers_count=quote["travelers"],
        currency=quote["currency"],
        subtotal=quote["subtotal"],
        status=BookingStatus.PENDING,
        summary={
            **(recommendation.payload or {}),
            "pricing": {
                "unit_price": quote["unit_price"],
                "quantity": quote["quantity"],
                "description": quote["description"],
            },
            **({"guest": guest} if guest else {}),
        },
    )
    create_booking(booking=booking, actor=actor, request=request)
    issue_payment_link(booking=booking, actor=actor, request=request)
    emails.send_payment_link(booking=booking, actor=actor)

    staff = actor if (actor and getattr(actor, "is_authenticated", False)) else None
    who = f"{staff.get_username()} booked {item.name} for the customer" if staff else (
        f"Customer selected {item.name}"
    )
    post_message(
        conversation=conversation,
        sender_type=MessageSender.SYSTEM,
        content=f"{who} — booking {booking.booking_number} created.",
        metadata={"booking_id": booking.pk, "event": "booking_created"},
    )
    # Managers already heard about it from `create_booking`; tell whoever owns the chat.
    owners = {
        user.pk: user
        for user in (conversation.assigned_to, booking.lead.assigned_to if booking.lead_id else None)
        if user is not None and user != staff
    }
    for user in owners.values():
        notify(
            recipient=user,
            notification_type="payment",
            title=f"Chat booking {booking.booking_number}",
            body=f"{item.name} · {booking.currency} {booking.total_amount}",
            link=_booking_link(booking),
            metadata={"booking_id": booking.pk},
        )
    return booking, True
