from django.contrib import admin

from .models import Booking, Notification, Payment, WebhookEvent


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "booking_number",
        "product_name",
        "customer",
        "total_amount",
        "status",
        "created_at",
    )
    list_filter = ("status", "product_type", "website")
    search_fields = ("booking_number", "product_name", "customer__email", "customer__phone")
    inlines = [PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "booking",
        "razorpay_order_id",
        "amount",
        "status",
        "paid_at",
    )
    list_filter = ("status",)
    search_fields = ("razorpay_order_id", "razorpay_payment_id")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "notification_type", "is_read", "created_at")
    list_filter = ("notification_type", "is_read")
    search_fields = ("title", "body")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event", "result", "created_at")
    search_fields = ("event_id", "event")
    readonly_fields = ("event_id", "event", "result", "created_at", "updated_at")
