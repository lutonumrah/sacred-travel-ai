from django.db.models import Q, Sum
from django.utils import timezone

from .models import Booking, BookingStatus, Notification, Payment, PaymentStatus


def list_bookings(*, q="", status="", website=None, date_from=None, date_to=None, user=None):
    queryset = Booking.objects.select_related("customer", "website", "lead", "created_by")
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
    if user is not None and not (user.is_superuser or user.is_manager):
        queryset = queryset.filter(Q(created_by=user) | Q(lead__assigned_to=user))
    return queryset


def list_payments(*, status="", q=""):
    queryset = Payment.objects.select_related("booking", "booking__customer")
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


def booking_status_counts(*, website=None):
    queryset = Booking.objects.all()
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
