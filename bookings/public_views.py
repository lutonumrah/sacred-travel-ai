"""Customer-facing payment page and its API, reached by a token instead of a login.

Everything here is scoped by the booking's `payment_token`. A wrong or expired
token is a 404 either way (the expired page just says so kindly and shows no
price or personal details). Staff can issue a fresh link from the booking page.
"""

from django.http import Http404
from django.shortcuts import render
from django.views import View
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from core.api import ErrorResponse, SuccessResponse
from core.cors import allow_cors_for
from core.emails import brand_for
from core.throttling import PublicPayThrottle

from . import payments, selectors, services
from .models import BookingStatus
from .serializers import PaymentVerifySerializer


def _brand(booking):
    return brand_for(booking.website)


def _guest(booking):
    return selectors.booking_contact(booking)


def _no_store(response):
    # The URL is the credential: keep it out of caches and Referer headers.
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


class PaymentPageView(View):
    def get(self, request, token):
        booking = selectors.booking_for_payment_token(token)
        if booking is None:
            raise Http404("Unknown payment link.")
        brand, color = _brand(booking)
        ctx = {"booking": booking, "brand": brand, "brand_color": color}
        if not services.has_live_payment_link(booking):
            return _no_store(render(request, "public/pay_expired.html", ctx, status=404))

        guest = _guest(booking)
        ctx.update(
            {
                "guest": guest,
                "pricing": (booking.summary or {}).get("pricing", {}),
                "is_paid": booking.status in (BookingStatus.CONFIRMED, BookingStatus.PAID),
                "is_payable": booking.is_payable,
                "is_live_gateway": payments.is_live(),
                "token": token,
                # Rendered with `json_script`; the order id is added once it exists.
                "checkout_payload": {
                    "key": payments.key_id(),
                    "name": brand,
                    "description": booking.product_name,
                    "prefill": {
                        "name": guest["name"],
                        "email": guest["email"],
                        "contact": guest["phone"],
                    },
                    "theme": {"color": color},
                },
            }
        )
        return _no_store(render(request, "public/pay.html", ctx))


class PublicPaymentAPI(APIView):
    """Base for the token-scoped payment calls made from the pay page."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PublicPayThrottle]

    def booking_or_error(self, request, token):
        booking = selectors.booking_for_payment_token(token)
        if booking is None:
            return None, ErrorResponse("Unknown payment link.", status_code=404)
        if booking.website is not None:
            allow_cors_for(request, booking.website)
        if not services.has_live_payment_link(booking):
            return None, ErrorResponse(
                "This payment link has expired. Please ask us for a new one.", status_code=404
            )
        return booking, None


class PublicOrderAPI(PublicPaymentAPI):
    """Open (or reuse) the Razorpay order and return Checkout options."""

    def post(self, request, token):
        booking, error = self.booking_or_error(request, token)
        if error is not None:
            return error
        try:
            payment = services.open_checkout(booking=booking, request=request)
        except services.BookingError as exc:
            return ErrorResponse(str(exc), status_code=409)
        return SuccessResponse(
            {
                "order_id": payment.razorpay_order_id,
                "amount": payments.to_paise(payment.amount),
                "currency": payment.currency,
                "simulated": not payments.is_live(),
            },
            message="Payment order ready.",
        )


class PublicVerifyAPI(PublicPaymentAPI):
    """Razorpay Checkout's success callback, checked through the normal verify path."""

    def post(self, request, token):
        booking, error = self.booking_or_error(request, token)
        if error is not None:
            return error
        serializer = PaymentVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return ErrorResponse("Invalid payment details.", detail=serializer.errors)
        data = serializer.validated_data
        # Only this booking's own orders: a token never verifies another booking.
        if not booking.payments.filter(razorpay_order_id=data["razorpay_order_id"]).exists():
            return ErrorResponse("No payment matches that order.", status_code=404)
        _payment, error_message = services.verify_payment(
            order_id=data["razorpay_order_id"],
            payment_id=data["razorpay_payment_id"],
            signature=data["razorpay_signature"],
            request=request,
        )
        if error_message:
            return ErrorResponse(error_message)
        booking.refresh_from_db()
        return SuccessResponse(
            {"status": booking.status, "booking": booking.booking_number},
            message="Payment received — booking confirmed.",
        )


class PublicSimulateAPI(PublicPaymentAPI):
    """Test mode only: complete a payment without Razorpay, through the same verify path."""

    def post(self, request, token):
        booking, error = self.booking_or_error(request, token)
        if error is not None:
            return error
        if payments.is_live():
            return ErrorResponse("Simulated payments are disabled.", status_code=403)
        try:
            _payment, error_message = services.simulate_checkout(
                booking=booking, request=request
            )
        except services.BookingError as exc:
            return ErrorResponse(str(exc), status_code=409)
        if error_message:
            return ErrorResponse(error_message)
        booking.refresh_from_db()
        return SuccessResponse(
            {"status": booking.status, "booking": booking.booking_number},
            message="Simulated payment captured — booking confirmed.",
        )
