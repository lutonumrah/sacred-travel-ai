"""Emails to the customer about their booking.

Each helper queues one message for after the current transaction commits and
returns True if there was an address to send to. Every attempt, sent or failed,
is written to the audit log (and the lead's timeline) so staff can see it on
the booking page. A mail failure never undoes the booking action itself.
"""

from django.urls import reverse

from core.emails import absolute_url, brand_for, send_email_on_commit
from core.services import log_audit

from .models import PaymentStatus
from .selectors import booking_contact

LABELS = {
    "payment_link": "Payment link",
    "confirmed": "Booking confirmation",
    "cancelled": "Cancellation notice",
    "refunded": "Refund notice",
}


def _record(*, booking, kind, to, actor, ok):
    from crm.services import record_activity

    label = LABELS.get(kind, kind)
    log_audit(
        actor=actor,
        action="booking.email_sent" if ok else "booking.email_failed",
        entity=booking,
        metadata={"kind": kind, "label": label, "to": to, "number": booking.booking_number},
    )
    if booking.lead_id:
        record_activity(
            lead=booking.lead,
            actor=actor,
            activity_type="email",
            summary=f"{label} emailed to {to}" if ok else f"{label} email to {to} failed",
            details={"booking_id": booking.pk, "kind": kind, "sent": ok},
        )


def _send(*, booking, kind, template, actor=None, **extra):
    contact = booking_contact(booking)
    to = (contact["email"] or "").strip()
    if not to:
        return False
    brand, color = brand_for(booking.website)
    context = {
        "booking": booking,
        "name": contact["name"] or "there",
        "brand": brand,
        "brand_color": color,
        **extra,
    }
    send_email_on_commit(
        to=to,
        template=template,
        context=context,
        brand=brand,
        on_sent=lambda ok: _record(booking=booking, kind=kind, to=to, actor=actor, ok=ok),
    )
    return True


def _payment_reference(booking):
    payment = (
        booking.payments.filter(status__in=[PaymentStatus.SUCCESS, PaymentStatus.REFUNDED])
        .exclude(razorpay_payment_id="")
        .order_by("-paid_at", "-pk")
        .first()
    )
    return payment.razorpay_payment_id if payment else ""


def send_payment_link(*, booking, actor=None):
    """"Complete your payment" with the booking's current /pay/<token>/ link."""
    if not booking.payment_token:
        return False
    pay_url = absolute_url(reverse("public:pay", args=[booking.payment_token]))
    return _send(
        booking=booking,
        kind="payment_link",
        template="booking_payment_link",
        actor=actor,
        pay_url=pay_url,
        preheader=f"Booking {booking.booking_number}: pay securely online.",
    )


def send_confirmation(*, booking, actor=None):
    return _send(
        booking=booking,
        kind="confirmed",
        template="booking_confirmed",
        actor=actor,
        payment_reference=_payment_reference(booking),
        preheader=f"Payment received for {booking.booking_number}.",
    )


def send_cancellation(*, booking, reason="", actor=None):
    return _send(
        booking=booking,
        kind="cancelled",
        template="booking_cancelled",
        actor=actor,
        reason=reason,
        was_paid=booking.payments.filter(status=PaymentStatus.SUCCESS).exists(),
    )


def send_refund(*, booking, amount, currency, payment_reference="", actor=None):
    return _send(
        booking=booking,
        kind="refunded",
        template="booking_refunded",
        actor=actor,
        amount=f"{currency} {amount:,.2f}",
        payment_reference=payment_reference,
    )
