import json
import logging

from django.urls import path
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.api import EnvelopeMixin, ErrorResponse, SuccessResponse
from core.permissions import IsSalesTeam

from . import payments, selectors, services
from .serializers import (
    BookingSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    PaymentVerifySerializer,
)

logger = logging.getLogger(__name__)

app_name = "api_bookings"


class BookingListAPI(EnvelopeMixin, generics.ListAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = BookingSerializer

    def get_queryset(self):
        params = self.request.query_params
        return selectors.list_bookings(
            q=params.get("q", ""),
            status=params.get("status", ""),
            user=self.request.user,
        ).prefetch_related("payments")


class BookingDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    permission_classes = [IsSalesTeam]
    serializer_class = BookingSerializer

    def get_queryset(self):
        return selectors.list_bookings(user=self.request.user).prefetch_related("payments")


class PaymentCreateAPI(APIView):
    """Open a Razorpay order for a booking and return the checkout parameters."""

    permission_classes = [IsSalesTeam]

    def post(self, request):
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = generics.get_object_or_404(
            selectors.visible_bookings(request.user), pk=serializer.validated_data["booking_id"]
        )
        try:
            payment = services.open_payment_order(
                booking=booking, actor=request.user, request=request
            )
        except services.BookingError as exc:
            return ErrorResponse(str(exc), status_code=409)
        return SuccessResponse(
            {
                "payment": PaymentSerializer(payment).data,
                "checkout": {
                    "key": payments.key_id(),
                    "order_id": payment.razorpay_order_id,
                    "amount": payments.to_paise(payment.amount),
                    "currency": payment.currency,
                    "name": booking.website.name if booking.website else "Scared Travel",
                    "description": booking.product_name,
                    "simulated": not payments.is_live(),
                },
            },
            message="Payment order created.",
        )


class PaymentVerifyAPI(APIView):
    """Verify the signature Razorpay Checkout returns to the browser."""

    permission_classes = [IsSalesTeam]

    def post(self, request):
        serializer = PaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        visible = selectors.visible_bookings(request.user).filter(
            payments__razorpay_order_id=data["razorpay_order_id"]
        )
        if not visible.exists():
            return ErrorResponse("No payment matches that order.", status_code=404)
        payment, error = services.verify_payment(
            order_id=data["razorpay_order_id"],
            payment_id=data["razorpay_payment_id"],
            signature=data["razorpay_signature"],
            actor=request.user,
            request=request,
        )
        if error:
            return ErrorResponse(
                error,
                detail={
                    "message": error,
                    "payment": PaymentSerializer(payment).data if payment else None,
                },
            )
        return SuccessResponse(
            {
                "payment": PaymentSerializer(payment).data,
                "booking": BookingSerializer(payment.booking).data,
            },
            message="Payment verified.",
        )


class RazorpayWebhookAPI(APIView):
    """Razorpay server-to-server callback. Authenticated by HMAC, not by session."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not payments.verify_webhook_signature(body=request.body, signature=signature):
            logger.warning("Rejected a Razorpay webhook with an invalid signature.")
            return ErrorResponse("Invalid signature.")

        try:
            payload = json.loads(request.body.decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ErrorResponse("Malformed payload.")
        if not isinstance(payload, dict):
            return ErrorResponse("Malformed payload.")

        result = services.process_webhook(
            event_id=request.headers.get("X-Razorpay-Event-Id", ""),
            event=str(payload.get("event", "")),
            payload=payload,
            request=request,
        )
        return SuccessResponse({"result": result}, message="Webhook processed.")


urlpatterns = [
    path("", BookingListAPI.as_view(), name="list"),
    path("payments/create/", PaymentCreateAPI.as_view(), name="payment_create"),
    path("payments/verify/", PaymentVerifyAPI.as_view(), name="payment_verify"),
    path("payments/webhook/", RazorpayWebhookAPI.as_view(), name="razorpay_webhook"),
    path("<int:pk>/", BookingDetailAPI.as_view(), name="detail"),
]
