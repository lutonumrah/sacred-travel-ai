"""Booking lifecycle and payment orchestration."""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.notifications import notify, notify_managers
from core.services import log_audit

from . import payments
from .models import Booking, BookingStatus, Payment, PaymentStatus

logger = logging.getLogger(__name__)


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
    target = status or LeadStatus.PAYMENT_PENDING
    crm_services.change_status(
        lead=booking.lead, status=target, actor=actor, request=request
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

    if payment.status == PaymentStatus.SUCCESS:
        # Razorpay retries callbacks; settling twice must be a no-op.
        return payment, None

    if not payments.verify_payment_signature(
        order_id=order_id, payment_id=payment_id, signature=signature
    ):
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Signature verification failed"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        log_audit(
            actor=actor,
            action="payment.verify_failed",
            entity=payment.booking,
            metadata={"order_id": order_id},
            request=request,
        )
        return payment, "Signature verification failed."

    payment.razorpay_payment_id = payment_id
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
    mark_booking_paid(booking=payment.booking, actor=actor, request=request)
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

    link = reverse("bookings:detail", args=[booking.pk])
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
    return booking


@transaction.atomic
def mark_payment_failed(*, payment, reason="", actor=None, request=None):
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason[:255]
    payment.save(update_fields=["status", "failure_reason", "updated_at"])
    booking = payment.booking
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
        link=reverse("bookings:detail", args=[booking.pk]),
        metadata={"booking_id": booking.pk},
    )
    return payment


@transaction.atomic
def cancel_booking(*, booking, actor=None, request=None, reason=""):
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = timezone.now()
    booking.save(update_fields=["status", "cancelled_at", "updated_at"])
    log_audit(
        actor=actor,
        action="booking.cancel",
        entity=booking,
        metadata={"reason": reason},
        request=request,
    )
    return booking


def handle_webhook(*, event, payload, actor=None, request=None):
    """Apply a verified Razorpay webhook event. Returns a short status string."""
    entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
        if isinstance(payload, dict)
        else {}
    )
    order_id = entity.get("order_id", "")
    payment_id = entity.get("id", "")
    if not order_id:
        return "ignored: no order id"

    payment = Payment.objects.select_related("booking").filter(
        razorpay_order_id=order_id
    ).first()
    if payment is None:
        return "ignored: unknown order"

    payment.raw_response = payload
    payment.method = entity.get("method", "") or payment.method
    payment.save(update_fields=["raw_response", "method", "updated_at"])

    if event == "payment.captured":
        if payment.status != PaymentStatus.SUCCESS:
            payment.razorpay_payment_id = payment_id or payment.razorpay_payment_id
            payment.status = PaymentStatus.SUCCESS
            payment.paid_at = timezone.now()
            payment.save(
                update_fields=[
                    "razorpay_payment_id",
                    "status",
                    "paid_at",
                    "updated_at",
                ]
            )
            mark_booking_paid(booking=payment.booking, actor=actor, request=request)
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
        payment.status = PaymentStatus.REFUNDED
        payment.save(update_fields=["status", "updated_at"])
        return "refund recorded"

    return f"ignored: {event}"


def booking_from_recommendation(*, recommendation, customer, actor=None, request=None):
    """Turn an AI recommendation the customer accepted into a pending booking."""
    conversation = recommendation.conversation
    booking = Booking(
        website=conversation.website,
        customer=customer,
        lead=conversation.lead,
        conversation=conversation,
        recommendation=recommendation,
        product_type=recommendation.inventory_type,
        product_id=recommendation.object_id,
        product_name=recommendation.title,
        currency=recommendation.currency,
        subtotal=recommendation.price or 0,
        summary=recommendation.payload or {},
    )
    recommendation.is_selected = True
    recommendation.save(update_fields=["is_selected", "updated_at"])
    return create_booking(booking=booking, actor=actor, request=request)
