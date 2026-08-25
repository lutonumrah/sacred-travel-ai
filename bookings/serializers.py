from rest_framework import serializers

from .models import Booking, Notification, Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "razorpay_order_id",
            "razorpay_payment_id",
            "amount",
            "currency",
            "status",
            "method",
            "paid_at",
            "failure_reason",
            "created_at",
        )


class BookingSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    website = serializers.CharField(
        source="website.source_identifier", read_only=True, default=None
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "booking_number",
            "website",
            "customer",
            "customer_name",
            "lead",
            "product_type",
            "product_id",
            "product_name",
            "travel_start",
            "travel_end",
            "travelers_count",
            "currency",
            "subtotal",
            "tax_amount",
            "total_amount",
            "status",
            "status_display",
            "confirmed_at",
            "created_at",
            "payments",
        )


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "title",
            "body",
            "link",
            "is_read",
            "metadata",
            "created_at",
        )


class PaymentCreateSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()


class PaymentVerifySerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()
