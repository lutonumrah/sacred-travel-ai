from django.db.models import Q, Sum
from django.utils import timezone

from .models import Booking, BookingStatus, Notification, Payment, PaymentStatus


def visible_bookings(user):
    """Managers see every booking; employees ones they raised or whose lead they can see."""
    from crm.selectors import sees_everything, visible_leads

    queryset = Booking.objects.all()
    if not sees_everything(user):
        queryset = queryset.filter(
            Q(created_by=user) | Q(lead__in=visible_leads(user).values("pk"))
        )
    return queryset


def booking_for_payment_token(token):
    """The booking behind a customer payment link, expired or not; None if unknown."""
    if not token or len(token) > 64:
        return None
    return (
        Booking.objects.select_related("customer", "website", "conversation")
        .filter(payment_token=token)
        .first()
    )


def booking_contact(booking):
    """The contact typed when booking; the customer record only as a fallback."""
    guest = (booking.summary or {}).get("guest") or {}
    customer = booking.customer
    return {
        "name": guest.get("name") or customer.full_name,
        "email": guest.get("email") or customer.email,
        "phone": guest.get("phone") or customer.phone,
    }


# Audit actions written by bookings.emails for each customer email attempt.
EMAIL_AUDIT_ACTIONS = ("booking.email_sent", "booking.email_failed")


def customer_emails(booking):
    """Emails sent (or attempted) to this booking's customer, newest first."""
    from accounts.models import AuditLog

    return AuditLog.objects.filter(
        entity_type="Booking", entity_id=str(booking.pk), action__in=EMAIL_AUDIT_ACTIONS
    ).select_related("actor")


def list_bookings(*, q="", status="", website=None, date_from=None, date_to=None, user=None):
    base = visible_bookings(user) if user is not None else Booking.objects.all()
    queryset = base.select_related("customer", "website", "lead", "created_by")
    if q:
        queryset = queryset.filter(
            Q(booking_number__icontains=q)
            | Q(product_name__icontains=q)
            | Q(customer__first_name__icontains=q)
            | Q(customer__last_name__icontains=q)
            | Q(customer__email__icontains=q)
        )
    if status:
        queryset = queryset.filter(status=status)
    if website:
        queryset = queryset.filter(website=website)
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def list_payments(*, status="", q="", user=None):
    queryset = Payment.objects.select_related("booking", "booking__customer")
    if user is not None:
        queryset = queryset.filter(booking__in=visible_bookings(user).values("pk"))
    if status:
        queryset = queryset.filter(status=status)
    if q:
        queryset = queryset.filter(
            Q(razorpay_order_id__icontains=q)
            | Q(razorpay_payment_id__icontains=q)
            | Q(booking__booking_number__icontains=q)
        )
    return queryset


def revenue_total(*, date_from=None, date_to=None, website=None):
    queryset = Payment.objects.filter(status=PaymentStatus.SUCCESS)
    if date_from:
        queryset = queryset.filter(paid_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(paid_at__date__lte=date_to)
    if website:
        queryset = queryset.filter(booking__website=website)
    return queryset.aggregate(total=Sum("amount"))["total"] or 0


def booking_status_counts(*, website=None, user=None):
    """Bookings per status; with `user`, only the bookings that user can see."""
    queryset = visible_bookings(user) if user is not None else Booking.objects.all()
    if website:
        queryset = queryset.filter(website=website)
    from django.db.models import Count

    counts = dict(
        queryset.values_list("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status, _label in BookingStatus.choices}


def unread_notifications(user):
    if not user or not user.is_authenticated:
        return Notification.objects.none()
    return Notification.objects.filter(recipient=user, is_read=False)


def list_notifications(user, *, unread_only=False, notification_type=""):
    queryset = Notification.objects.filter(recipient=user)
    if unread_only:
        queryset = queryset.filter(is_read=False)
    if notification_type:
        queryset = queryset.filter(notification_type=notification_type)
    return queryset


def todays_bookings():
    return Booking.objects.filter(created_at__date=timezone.localdate())
