from django.conf import settings
from django.db import models

from conversations.models import Conversation, Recommendation
from core.models import TimeStampedModel
from crm.models import Customer, Lead
from websites.models import Website


class BookingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending Payment"
    PAID = "paid", "Paid"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class PaymentStatus(models.TextChoices):
    CREATED = "created", "Created"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"
    # The booking was cancelled before the customer paid this order.
    CANCELLED = "cancelled", "Cancelled"


class Booking(TimeStampedModel):
    booking_number = models.CharField(max_length=40, unique=True)
    website = models.ForeignKey(
        Website,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    recommendation = models.ForeignKey(
        Recommendation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
    )
    product_type = models.CharField(max_length=20)
    product_id = models.PositiveIntegerField()
    product_name = models.CharField(max_length=200)
    travel_start = models.DateField(null=True, blank=True)
    travel_end = models.DateField(null=True, blank=True)
    travelers_count = models.PositiveSmallIntegerField(default=1)
    currency = models.CharField(max_length=3, default="INR")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True,
    )
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_bookings",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.booking_number} · {self.product_name}"


class Payment(TimeStampedModel):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="payments")
    razorpay_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, db_index=True)
    razorpay_signature = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.CREATED,
        db_index=True,
    )
    method = models.CharField(max_length=50, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.razorpay_order_id or self.pk} [{self.status}]"


class WebhookEvent(TimeStampedModel):
    """Razorpay event ids already applied. Razorpay re-delivers on any non-2xx or timeout."""

    event_id = models.CharField(max_length=100, unique=True)
    event = models.CharField(max_length=60, blank=True)
    result = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} {self.event_id}"


class NotificationType(models.TextChoices):
    LEAD = "lead", "Lead"
    HANDOFF = "handoff", "Handoff"
    PAYMENT = "payment", "Payment"
    FOLLOW_UP = "follow_up", "Follow-up"
    SYSTEM = "system", "System"


class Notification(TimeStampedModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
