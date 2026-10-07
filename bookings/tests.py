import hashlib
import hmac
import json
from decimal import Decimal

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from crm.models import Customer, Lead, LeadStatus
from inventory.models import Destination, TourPackage
from websites.models import Website

from . import payments, services
from .models import Booking, BookingStatus, Notification, Payment, PaymentStatus, WebhookEvent


class BookingFixture(TestCase):
    def setUp(self):
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.customer = Customer.objects.create(
            first_name="Ana", email="ana@x.com", phone="9810000000"
        )
        destination = Destination.objects.create(name="Goa", code="goa")
        self.package = TourPackage.objects.create(
            name="Goa Escape", destination=destination, base_price=Decimal("24000")
        )
        self.lead = Lead.objects.create(
            customer=self.customer, website=self.website, title="Goa trip"
        )

    def make_booking(self, subtotal="24000", **kwargs):
        booking = Booking(
            website=self.website,
            customer=self.customer,
            lead=kwargs.pop("lead", self.lead),
            product_type="package",
            product_id=self.package.pk,
            product_name=self.package.name,
            subtotal=Decimal(subtotal),
            **kwargs,
        )
        return services.create_booking(booking=booking, actor=self.agent)


class PricingTests(BookingFixture):
    def test_tax_is_added_on_top_of_the_subtotal(self):
        with self.settings(BOOKING_TAX_PERCENT=Decimal("5")):
            booking = self.make_booking("10000")
        self.assertEqual(booking.tax_amount, Decimal("500.00"))
        self.assertEqual(booking.total_amount, Decimal("10500.00"))

    def test_tax_rounds_to_paise(self):
        subtotal, tax, total = services.price_booking(Decimal("333.33"), tax_percent=Decimal("18"))
        self.assertEqual(tax, Decimal("60.00"))
        self.assertEqual(total, Decimal("393.33"))

    def test_a_zero_subtotal_produces_no_tax(self):
        _subtotal, tax, total = services.price_booking(0)
        self.assertEqual(tax, Decimal("0.00"))
        self.assertEqual(total, Decimal("0"))


class BookingNumberTests(BookingFixture):
    def test_numbers_are_unique_and_dated(self):
        first = self.make_booking()
        second = self.make_booking()
        self.assertNotEqual(first.booking_number, second.booking_number)
        self.assertTrue(first.booking_number.startswith("STA-"))
        self.assertTrue(second.booking_number.endswith("0002"))


class SignatureTests(TestCase):
    def test_a_correct_signature_verifies(self):
        signature = payments.expected_signature("order_1", "pay_1")
        self.assertTrue(
            payments.verify_payment_signature(
                order_id="order_1", payment_id="pay_1", signature=signature
            )
        )

    def test_a_tampered_payment_id_fails(self):
        signature = payments.expected_signature("order_1", "pay_1")
        self.assertFalse(
            payments.verify_payment_signature(
                order_id="order_1", payment_id="pay_2", signature=signature
            )
        )

    def test_missing_values_fail_closed(self):
        self.assertFalse(
            payments.verify_payment_signature(order_id="", payment_id="", signature="")
        )

    def test_webhook_signature_is_hmac_of_the_raw_body(self):
        body = b'{"event":"payment.captured"}'
        digest = hmac.new(
            payments.SIMULATION_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        self.assertTrue(payments.verify_webhook_signature(body=body, signature=digest))
        self.assertFalse(payments.verify_webhook_signature(body=body, signature="deadbeef"))

    def test_simulation_mode_when_no_keys_are_set(self):
        with self.settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            self.assertFalse(payments.is_live())
            order = payments.create_order(amount=100)
            self.assertTrue(order["simulated"])
            self.assertEqual(order["amount"], 10000)  # paise


class PaymentFlowTests(BookingFixture):
    def test_creating_an_order_reuses_an_open_one(self):
        booking = self.make_booking()
        first = services.create_payment_order(booking=booking, actor=self.agent)
        second = services.create_payment_order(booking=booking, actor=self.agent)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(booking.payments.count(), 1)

    def test_a_verified_payment_confirms_the_booking_and_converts_the_lead(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        payment_id, signature = payments.simulate_payment(payment.razorpay_order_id)

        settled, error = services.verify_payment(
            order_id=payment.razorpay_order_id,
            payment_id=payment_id,
            signature=signature,
            actor=self.agent,
        )

        self.assertIsNone(error)
        self.assertEqual(settled.status, PaymentStatus.SUCCESS)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertIsNotNone(booking.confirmed_at)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)

    def test_a_bad_signature_is_rejected_without_touching_the_payment(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)

        settled, error = services.verify_payment(
            order_id=payment.razorpay_order_id,
            payment_id="pay_forged",
            signature="not-a-real-signature",
            actor=self.agent,
        )

        self.assertIsNotNone(error)
        # A forged callback must not kill the customer's real order.
        settled.refresh_from_db()
        self.assertEqual(settled.status, PaymentStatus.CREATED)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.PENDING)

    def test_verifying_twice_is_idempotent(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        payment_id, signature = payments.simulate_payment(payment.razorpay_order_id)
        kwargs = {
            "order_id": payment.razorpay_order_id,
            "payment_id": payment_id,
            "signature": signature,
        }
        services.verify_payment(**kwargs)
        confirmed_at = Booking.objects.get(pk=booking.pk).confirmed_at

        _settled, error = services.verify_payment(**kwargs)
        self.assertIsNone(error)
        self.assertEqual(Booking.objects.get(pk=booking.pk).confirmed_at, confirmed_at)

    def test_an_unknown_order_is_reported_not_crashed(self):
        payment, error = services.verify_payment(
            order_id="order_missing", payment_id="p", signature="s"
        )
        self.assertIsNone(payment)
        self.assertIsNotNone(error)

    def test_creating_a_booking_moves_the_lead_to_payment_pending(self):
        self.make_booking()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.PAYMENT_PENDING)

    def test_managers_are_notified_about_a_new_booking(self):
        Notification.objects.all().delete()
        self.make_booking()
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, notification_type="payment"
            ).exists()
        )


class WebhookFixture(BookingFixture):
    def setUp(self):
        super().setUp()
        self.booking = self.make_booking()
        self.payment = services.create_payment_order(booking=self.booking, actor=self.agent)
        self.url = reverse("api_bookings:razorpay_webhook")

    def _post(self, payload, signature=None, event_id=None):
        body = json.dumps(payload)
        if signature is None:
            signature = hmac.new(
                payments.SIMULATION_SECRET.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
        headers = {"HTTP_X_RAZORPAY_SIGNATURE": signature}
        if event_id:
            headers["HTTP_X_RAZORPAY_EVENT_ID"] = event_id
        return self.client.post(self.url, body, content_type="application/json", **headers)

    def _captured(self):
        return {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_webhook",
                        "order_id": self.payment.razorpay_order_id,
                        "method": "upi",
                        "amount": payments.to_paise(self.payment.amount),
                        "currency": self.payment.currency,
                    }
                }
            },
        }


class WebhookTests(WebhookFixture):
    def test_an_unsigned_webhook_is_rejected(self):
        response = self._post(self._captured(), signature="wrong")
        self.assertEqual(response.status_code, 400)
        self.booking.refresh_from_db()
        self.assertNotEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_a_captured_event_confirms_the_booking(self):
        response = self._post(self._captured())
        self.assertEqual(response.status_code, 200)

        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.payment.method, "upi")
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_a_repeated_captured_event_does_not_double_confirm(self):
        self._post(self._captured())
        confirmed_at = Booking.objects.get(pk=self.booking.pk).confirmed_at
        self._post(self._captured())
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).confirmed_at, confirmed_at)

    def test_a_failed_event_marks_the_booking_failed(self):
        payload = self._captured()
        payload["event"] = "payment.failed"
        payload["payload"]["payment"]["entity"]["error_description"] = "Card declined"
        self._post(payload)

        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.FAILED)
        self.assertEqual(self.payment.failure_reason, "Card declined")
        self.assertEqual(self.booking.status, BookingStatus.FAILED)

    def test_an_unknown_order_is_ignored_gracefully(self):
        payload = self._captured()
        payload["payload"]["payment"]["entity"]["order_id"] = "order_nope"
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("unknown order", response.json()["data"]["result"])

    def test_the_webhook_needs_no_login(self):
        self.assertEqual(self._post(self._captured()).status_code, 200)


class BookingViewTests(BookingFixture):
    def test_the_form_rejects_an_inventory_id_that_does_not_exist(self):
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("bookings:create"),
            {
                "customer": self.customer.pk,
                "website": self.website.pk,
                "product_type": "package",
                "product_id": 999999,
                "product_name": "Ghost package",
                "travelers_count": 2,
                "currency": "INR",
                "subtotal": "1000",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)

    def test_a_valid_form_creates_a_pending_booking(self):
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("bookings:create"),
            {
                "customer": self.customer.pk,
                "website": self.website.pk,
                "lead": self.lead.pk,
                "product_type": "package",
                "product_id": self.package.pk,
                "product_name": self.package.name,
                "travelers_count": 2,
                "currency": "INR",
                "subtotal": "24000",
            },
        )
        booking = Booking.objects.get()
        self.assertRedirects(response, reverse("bookings:detail", args=[booking.pk]))
        self.assertEqual(booking.status, BookingStatus.PENDING)

    def test_simulated_checkout_confirms_the_booking(self):
        booking = self.make_booking()
        services.create_payment_order(booking=booking, actor=self.agent)
        self.client.force_login(self.manager)
        self.client.post(reverse("bookings:payment_simulate", args=[booking.pk]))

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    def test_employees_cannot_run_the_simulated_checkout(self):
        booking = self.make_booking()
        self.client.force_login(self.agent)
        response = self.client.post(reverse("bookings:payment_simulate", args=[booking.pk]))
        self.assertRedirects(response, reverse("dashboard:overview"))

    def test_the_payment_verify_api_rejects_a_forged_signature(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("api_bookings:payment_verify"),
            {
                "razorpay_order_id": payment.razorpay_order_id,
                "razorpay_payment_id": "pay_x",
                "razorpay_signature": "forged",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_verify_api_error_uses_the_error_envelope(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        self.client.force_login(self.agent)
        body = self.client.post(
            reverse("api_bookings:payment_verify"),
            {
                "razorpay_order_id": payment.razorpay_order_id,
                "razorpay_payment_id": "pay_x",
                "razorpay_signature": "forged",
            },
            content_type="application/json",
        ).json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"]["status_code"], 400)

    def test_live_checkout_options_are_rendered_with_json_script(self):
        self.customer.first_name = "</script><script>alert(1)</script>"
        self.customer.save()
        booking = self.make_booking()
        services.create_payment_order(booking=booking, actor=self.agent)
        self.client.force_login(self.agent)
        with mock.patch.object(payments, "is_live", return_value=True):
            response = self.client.get(reverse("bookings:detail", args=[booking.pk]))
        self.assertContains(response, 'id="checkout-options"')
        self.assertNotContains(response, "</script><script>alert(1)")


class LiveModeSecretTests(TestCase):
    def _sign(self, body, secret):
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    @override_settings(
        RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="live-secret", RAZORPAY_WEBHOOK_SECRET=""
    )
    def test_live_keys_without_a_webhook_secret_reject_every_webhook(self):
        body = b'{"event":"payment.captured"}'
        with self.assertLogs("bookings.payments", level="ERROR"):
            self.assertFalse(
                payments.verify_webhook_signature(
                    body=body, signature=self._sign(body, payments.SIMULATION_SECRET)
                )
            )
        with self.assertLogs("bookings.payments", level="ERROR"):
            response = self.client.post(
                reverse("api_bookings:razorpay_webhook"),
                body,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=self._sign(body, payments.SIMULATION_SECRET),
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @override_settings(
        RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="live-secret", RAZORPAY_WEBHOOK_SECRET="wh"
    )
    def test_a_configured_webhook_secret_is_used(self):
        body = b'{"event":"payment.captured"}'
        self.assertTrue(
            payments.verify_webhook_signature(body=body, signature=self._sign(body, "wh"))
        )
        self.assertFalse(
            payments.verify_webhook_signature(
                body=body, signature=self._sign(body, payments.SIMULATION_SECRET)
            )
        )

    @override_settings(RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="")
    def test_a_key_id_without_its_secret_never_accepts_the_simulation_signature(self):
        signature = hmac.new(
            payments.SIMULATION_SECRET.encode(), b"order_1|pay_1", hashlib.sha256
        ).hexdigest()
        with self.assertLogs("bookings.payments", level="ERROR"):
            self.assertFalse(
                payments.verify_payment_signature(
                    order_id="order_1", payment_id="pay_1", signature=signature
                )
            )


class PaymentHardeningTests(WebhookFixture):
    def _event(self, name, **entity):
        payload = self._captured()
        payload["event"] = name
        payload["payload"]["payment"]["entity"].update(entity)
        return payload

    def test_a_captured_amount_mismatch_does_not_confirm(self):
        Notification.objects.all().delete()
        response = self._post(self._event("payment.captured", amount=100))
        self.assertIn("amount mismatch", response.json()["data"]["result"])
        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertNotEqual(self.payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, title__icontains="mismatch"
            ).exists()
        )

    def test_a_currency_mismatch_does_not_confirm(self):
        self._post(self._event("payment.captured", currency="USD"))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    def test_order_paid_confirms_like_captured_and_is_idempotent(self):
        self._post(self._event("order.paid"))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        confirmed_at = self.booking.confirmed_at
        self._post(self._captured())
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).confirmed_at, confirmed_at)

    def test_a_late_failed_event_does_not_downgrade_a_confirmed_booking(self):
        self._post(self._captured())
        self._post(self._event("payment.failed", id="pay_retry", error_description="Declined"))
        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_a_repeated_event_id_is_processed_once(self):
        self._post(self._captured(), event_id="evt_1")
        # Same id, different body: Razorpay re-delivering must not be re-applied.
        response = self._post(
            self._event("payment.failed", error_description="Declined"), event_id="evt_1"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("duplicate", response.json()["data"]["result"])
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_1").count(), 1)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def _refund(self, amount=None, refund_status="full"):
        payload = self._event("refund.processed", refund_status=refund_status)
        payload["payload"]["refund"] = {
            "entity": {
                "id": "rfnd_1",
                "payment_id": "pay_webhook",
                "amount": amount if amount is not None else payments.to_paise(self.payment.amount),
            }
        }
        return payload

    def test_a_full_refund_refunds_the_booking_and_loses_the_lead(self):
        self._post(self._captured())
        Notification.objects.all().delete()
        response = self._post(self._refund())
        self.assertEqual(response.json()["data"]["result"], "refund recorded")
        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.REFUNDED)
        self.assertEqual(self.booking.status, BookingStatus.REFUNDED)
        self.assertEqual(self.lead.status, LeadStatus.LOST)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, title__startswith="Booking refunded"
            ).exists()
        )

    def test_a_partial_refund_keeps_the_booking_confirmed(self):
        self._post(self._captured())
        self._post(self._refund(amount=100, refund_status="partial"))
        self.payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_a_refund_event_found_by_payment_id_alone(self):
        self._post(self._captured())
        payload = self._refund()
        del payload["payload"]["payment"]
        self._post(payload)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.REFUNDED)


class CheckoutVerifyLiveTests(BookingFixture):
    def setUp(self):
        super().setUp()
        self.booking = self.make_booking()
        self.payment = services.create_payment_order(booking=self.booking, actor=self.agent)
        self.payment_id, self.signature = payments.simulate_payment(
            self.payment.razorpay_order_id
        )

    def _verify(self, gateway):
        with mock.patch.object(payments, "fetch_payment", return_value=gateway):
            return services.verify_payment(
                order_id=self.payment.razorpay_order_id,
                payment_id=self.payment_id,
                signature=self.signature,
            )

    def test_a_gateway_amount_mismatch_does_not_confirm(self):
        _payment, error = self._verify(
            {
                "order_id": self.payment.razorpay_order_id,
                "amount": 100,
                "currency": "INR",
                "status": "captured",
            }
        )
        self.assertIsNotNone(error)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    def test_a_matching_captured_gateway_payment_confirms(self):
        _payment, error = self._verify(
            {
                "order_id": self.payment.razorpay_order_id,
                "amount": payments.to_paise(self.payment.amount),
                "currency": "INR",
                "status": "captured",
            }
        )
        self.assertIsNone(error)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)


class CancelBookingTests(BookingFixture):
    def test_cancelling_closes_open_payments_and_reopens_the_lead(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        Notification.objects.all().delete()

        services.cancel_booking(booking=booking, actor=self.manager, reason="Changed plans")

        payment.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.CANCELLED)
        self.assertEqual(self.lead.status, LeadStatus.INTERESTED)
        # The agent who raised it hears about it; the cancelling manager does not.
        self.assertTrue(Notification.objects.filter(recipient=self.agent).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.manager).exists())

    def test_a_payment_after_cancellation_does_not_revive_the_booking(self):
        booking = self.make_booking()
        payment = services.create_payment_order(booking=booking, actor=self.agent)
        services.cancel_booking(booking=booking, actor=self.manager)
        payment_id, signature = payments.simulate_payment(payment.razorpay_order_id)

        services.verify_payment(
            order_id=payment.razorpay_order_id, payment_id=payment_id, signature=signature
        )

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, title__startswith="Payment received for cancelled"
            ).exists()
        )


class BookingAccessTests(BookingFixture):
    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user("other", password="pw", role="employee")
        self.lead.assigned_to = self.other
        self.lead.save()
        # Raised by the other employee for their own lead.
        booking = Booking(
            website=self.website,
            customer=self.customer,
            lead=self.lead,
            product_type="package",
            product_id=self.package.pk,
            product_name=self.package.name,
            subtotal=Decimal("1000"),
        )
        self.booking = services.create_booking(booking=booking, actor=self.other)
        self.payment = services.create_payment_order(booking=self.booking, actor=self.other)

    def test_an_employee_gets_404_on_someone_elses_booking(self):
        self.client.force_login(self.agent)
        self.assertEqual(
            self.client.get(reverse("bookings:detail", args=[self.booking.pk])).status_code, 404
        )
        self.assertEqual(
            self.client.post(reverse("bookings:payment_create", args=[self.booking.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("api_bookings:detail", args=[self.booking.pk])).status_code,
            404,
        )

    def test_the_owner_and_managers_can_open_it(self):
        for user in (self.other, self.manager):
            self.client.force_login(user)
            response = self.client.get(reverse("bookings:detail", args=[self.booking.pk]))
            self.assertEqual(response.status_code, 200)

    def test_payment_apis_hide_other_employees_bookings(self):
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("api_bookings:payment_create"),
            {"booking_id": self.booking.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        payment_id, signature = payments.simulate_payment(self.payment.razorpay_order_id)
        response = self.client.post(
            reverse("api_bookings:payment_verify"),
            {
                "razorpay_order_id": self.payment.razorpay_order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    def test_the_inventory_role_cannot_reach_bookings(self):
        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(stock)
        self.assertRedirects(
            self.client.get(reverse("bookings:list")), reverse("dashboard:overview")
        )
        self.assertEqual(self.client.get(reverse("api_bookings:list")).status_code, 403)



# --------------------------------------------------------------------------
# Batch 2: pricing chat recommendations and the customer payment link
# --------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.utils import timezone  # noqa: E402

from conversations import services as chat_services  # noqa: E402
from conversations.models import Message, MessageSender, Recommendation  # noqa: E402
from inventory.models import CarRental, Hotel  # noqa: E402


class QuoteTests(BookingFixture):
    def setUp(self):
        super().setUp()
        self.start = timezone.localdate() + timedelta(days=7)
        self.hotel = Hotel.objects.create(name="Palm Stay", base_price=Decimal("3000"))
        self.car = CarRental.objects.create(
            name="Swift", vehicle_type="Hatchback", seats=4, daily_price=Decimal("1500")
        )
        self.package.duration_days = 4
        self.package.save()

    def quote(self, kind, item, days=None, travelers=2):
        end = self.start + timedelta(days=days) if days is not None else None
        return services.quote_item(
            inventory_type=kind, item=item, travel_start=self.start, travel_end=end,
            travelers=travelers,
        )

    def test_hotel_is_price_times_nights(self):
        quote = self.quote("hotel", self.hotel, days=3, travelers=4)
        self.assertEqual(quote["subtotal"], Decimal("9000.00"))
        self.assertEqual(quote["description"], "3 nights × INR 3,000")

    def test_hotel_needs_at_least_one_night(self):
        with self.assertRaises(services.BookingError):
            self.quote("hotel", self.hotel, days=0)

    def test_car_is_daily_price_times_days_including_both_ends(self):
        self.assertEqual(self.quote("car", self.car, days=2)["subtotal"], Decimal("4500.00"))
        self.assertEqual(self.quote("car", self.car)["subtotal"], Decimal("1500.00"))

    def test_a_car_must_seat_everyone(self):
        with self.assertRaises(services.BookingError):
            self.quote("car", self.car, days=1, travelers=5)

    def test_package_is_per_person_and_sets_its_own_end_date(self):
        quote = self.quote("package", self.package, days=30, travelers=3)
        self.assertEqual(quote["subtotal"], Decimal("72000.00"))
        self.assertEqual(quote["travel_end"], self.start + timedelta(days=3))

    def test_price_on_request_and_past_dates_are_refused(self):
        self.hotel.base_price = 0
        with self.assertRaises(services.BookingError):
            self.quote("hotel", self.hotel, days=2)
        with self.assertRaises(services.BookingError):
            services.quote_item(
                inventory_type="package", item=self.package,
                travel_start=timezone.localdate() - timedelta(days=1),
            )

    def test_a_booking_never_moves_a_converted_lead_backwards(self):
        Lead.objects.filter(pk=self.lead.pk).update(status=LeadStatus.CONVERTED)
        self.lead.refresh_from_db()
        self.make_booking()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)


class PaymentLinkFixture(BookingFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.conversation, _ = chat_services.start_conversation(website=self.website)
        self.conversation.lead = self.lead
        self.conversation.customer = self.customer
        self.conversation.save()
        self.recommendation = Recommendation.objects.create(
            conversation=self.conversation,
            inventory_type="package",
            object_id=self.package.pk,
            title=self.package.name,
            price=self.package.base_price,
        )
        self.booking, _ = services.booking_from_recommendation(
            recommendation=self.recommendation,
            customer=self.customer,
            travel_start=timezone.localdate() + timedelta(days=20),
            travelers=1,
        )
        self.token = self.booking.payment_token

    def api(self, name, token=None, data=None):
        return self.client.post(
            reverse(f"api_pay:{name}", args=[token or self.token]),
            data or {},
            content_type="application/json",
        )


class PaymentPageTests(PaymentLinkFixture):
    def test_a_valid_link_shows_the_booking_summary_without_login(self):
        response = self.client.get(reverse("public:pay", args=[self.token]))
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for text in (
            self.booking.booking_number, "Goa Escape", "Ana", "24,000.00", "1,200.00",
            "25,200.00", "Simulate payment (test mode)", "Pending Payment",
        ):
            self.assertIn(text.replace(",", ""), page.replace(",", ""), text)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_the_link_expires_after_the_configured_days(self):
        self.assertAlmostEqual(
            self.booking.payment_token_expires_at,
            timezone.now() + timedelta(days=7),
            delta=timedelta(minutes=1),
        )
        with self.settings(PAYMENT_LINK_TTL_DAYS=2):
            services.issue_payment_link(booking=self.booking)
        self.assertLess(self.booking.payment_token_expires_at, timezone.now() + timedelta(days=3))

    def test_expired_and_unknown_tokens_are_404(self):
        Booking.objects.filter(pk=self.booking.pk).update(
            payment_token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = self.client.get(reverse("public:pay", args=[self.token]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "expired", status_code=404)
        self.assertNotContains(response, "24,000", status_code=404)
        self.assertEqual(self.client.get(reverse("public:pay", args=["nope"])).status_code, 404)
        self.assertEqual(self.api("order").status_code, 404)
        self.assertEqual(self.api("order", token="nope").status_code, 404)

    def test_issuing_a_new_link_kills_the_old_one(self):
        old = self.token
        self.client.force_login(self.agent)
        Booking.objects.filter(pk=self.booking.pk).update(created_by=self.agent)
        detail = self.client.get(reverse("bookings:detail", args=[self.booking.pk]))
        self.assertContains(detail, "Copy customer payment link")
        self.assertContains(detail, f"/pay/{old}/")
        self.client.post(reverse("bookings:payment_link", args=[self.booking.pk]))
        self.booking.refresh_from_db()
        self.assertNotEqual(self.booking.payment_token, old)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("public:pay", args=[old])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("public:pay", args=[self.booking.payment_token])).status_code,
            200,
        )

    def test_the_token_never_reaches_the_audit_log(self):
        from accounts.models import AuditLog

        for entry in AuditLog.objects.all():
            self.assertNotIn(self.token, json.dumps(entry.metadata))


class PublicPaymentAPITests(PaymentLinkFixture):
    def test_order_then_verify_confirms_the_booking(self):
        order = self.api("order").json()["data"]
        self.assertTrue(order["simulated"])
        self.assertEqual(order["amount"], 2520000)
        payment_id, signature = payments.simulate_payment(order["order_id"])
        response = self.api("verify", data={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_a_bad_signature_changes_nothing(self):
        order = self.api("order").json()["data"]
        response = self.api("verify", data={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": "pay_fake",
            "razorpay_signature": "0" * 64,
        })
        self.assertEqual(response.status_code, 400)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertEqual(self.booking.payments.get().status, PaymentStatus.CREATED)

    def test_a_token_cannot_verify_another_bookings_order(self):
        other = self.make_booking()
        other_payment = services.create_payment_order(booking=other)
        payment_id, signature = payments.simulate_payment(other_payment.razorpay_order_id)
        response = self.api("verify", data={
            "razorpay_order_id": other_payment.razorpay_order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        self.assertEqual(response.status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.status, BookingStatus.PENDING)

    def test_simulated_payment_confirms_and_tells_the_chat(self):
        response = self.api("simulate")
        self.assertEqual(response.status_code, 200, response.content)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)
        note = Message.objects.filter(
            conversation=self.conversation, sender_type=MessageSender.SYSTEM
        ).last()
        self.assertEqual(note.metadata["event"], "booking_confirmed")
        page = self.client.get(reverse("public:pay", args=[self.token])).content.decode()
        self.assertIn("your booking is confirmed", page)
        self.assertNotIn("Simulate payment", page)
        # Paying twice is refused, not double-charged.
        self.assertEqual(self.api("simulate").status_code, 409)

    def test_simulation_is_off_with_live_keys(self):
        with mock.patch.object(payments, "is_live", return_value=True):
            self.assertEqual(self.api("simulate").status_code, 403)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    def test_a_cancelled_booking_cannot_be_paid_by_link(self):
        services.cancel_booking(booking=self.booking, actor=self.manager)
        self.assertEqual(self.api("order").status_code, 409)

    @override_settings(PUBLIC_API_THROTTLE_RATES={"public_pay": "2/minute"})
    def test_pay_endpoints_are_throttled(self):
        codes = [self.api("order").status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class BookingFormScopeTests(BookingFixture):
    def test_the_customer_and_lead_dropdowns_only_list_visible_records(self):
        from .forms import BookingForm

        hidden_customer = Customer.objects.create(first_name="Secret", email="s@x.com")
        Lead.objects.create(
            customer=hidden_customer, title="Secret trip", assigned_to=self.manager
        )
        Lead.objects.filter(pk=self.lead.pk).update(assigned_to=self.agent)

        form = BookingForm(user=self.agent)
        self.assertIn(self.customer, form.fields["customer"].queryset)
        self.assertNotIn(hidden_customer, form.fields["customer"].queryset)
        self.assertEqual(list(form.fields["lead"].queryset), [self.lead])

        manager_form = BookingForm(user=self.manager)
        self.assertIn(hidden_customer, manager_form.fields["customer"].queryset)


# --------------------------------------------------------------------------
# Batch 3: customer emails
# --------------------------------------------------------------------------

from django.core import mail  # noqa: E402

from accounts.models import AuditLog  # noqa: E402
from crm.models import LeadActivity  # noqa: E402


@override_settings(SITE_URL="https://umrah.example", DEFAULT_FROM_EMAIL="bookings@umrah.example")
class CustomerEmailTests(BookingFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.website.brand_name = "Umrah Co"
        self.website.save()
        self.conversation, _ = chat_services.start_conversation(website=self.website)
        self.conversation.lead = self.lead
        self.conversation.customer = self.customer
        self.conversation.save()
        self.recommendation = Recommendation.objects.create(
            conversation=self.conversation,
            inventory_type="package",
            object_id=self.package.pk,
            title=self.package.name,
            price=self.package.base_price,
        )

    def book_from_chat(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            booking, created = services.booking_from_recommendation(
                recommendation=self.recommendation,
                customer=self.customer,
                travel_start=timezone.localdate() + timedelta(days=20),
                travelers=1,
                **kwargs,
            )
        return booking

    def pay(self, booking):
        with self.captureOnCommitCallbacks(execute=True):
            services.simulate_checkout(booking=booking)
        booking.refresh_from_db()
        return booking

    def test_a_chat_booking_emails_the_absolute_pay_link(self):
        booking = self.book_from_chat()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ana@x.com"])
        self.assertEqual(
            message.subject, f"Your booking {booking.booking_number} — complete your payment"
        )
        link = f"https://umrah.example/pay/{booking.payment_token}/"
        self.assertIn(link, message.body)
        html = message.alternatives[0][0]
        self.assertIn(link, html)
        self.assertIn("Umrah Co", html)
        self.assertIn("Goa Escape", message.body)
        self.assertEqual(message.from_email, "Umrah Co <bookings@umrah.example>")
        # Recorded where staff can see it.
        sent = AuditLog.objects.get(action="booking.email_sent")
        self.assertEqual(sent.metadata["kind"], "payment_link")
        self.assertEqual(sent.entity_id, str(booking.pk))
        self.assertTrue(LeadActivity.objects.filter(lead=self.lead, activity_type="email"))

    def test_the_guest_email_typed_in_the_chat_wins(self):
        self.book_from_chat(guest={"name": "Ana R", "email": "ana.typed@x.com", "phone": ""})
        self.assertEqual(mail.outbox[0].to, ["ana.typed@x.com"])
        self.assertIn("Hello Ana R", mail.outbox[0].body)

    def test_asking_twice_does_not_email_twice(self):
        self.book_from_chat()
        self.book_from_chat()
        self.assertEqual(len(mail.outbox), 1)

    def test_no_email_when_the_customer_has_none(self):
        self.customer.email = ""
        self.customer.save()
        booking = self.book_from_chat()
        self.assertEqual(mail.outbox, [])
        self.assertEqual(booking.status, BookingStatus.PENDING)
        self.assertFalse(AuditLog.objects.filter(action__startswith="booking.email"))

    def test_payment_sends_a_confirmation_with_the_payment_reference(self):
        booking = self.pay(self.book_from_chat())

        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        confirmation = mail.outbox[-1]
        self.assertEqual(confirmation.subject, f"Booking confirmed: {booking.booking_number}")
        reference = booking.payments.get(status=PaymentStatus.SUCCESS).razorpay_payment_id
        self.assertTrue(reference)
        self.assertIn(reference, confirmation.body)
        self.assertIn(reference, confirmation.alternatives[0][0])

    def test_smtp_failure_never_breaks_booking_or_payment(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP down")
        ), self.assertLogs("core.emails", "ERROR"):
            booking = self.book_from_chat()
            booking = self.pay(booking)

        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(mail.outbox, [])
        failed = AuditLog.objects.filter(action="booking.email_failed")
        self.assertEqual(
            sorted(failed.values_list("metadata__kind", flat=True)), ["confirmed", "payment_link"]
        )

    def test_nothing_is_mailed_when_the_booking_rolls_back(self):
        # Fails after the email was queued: the rollback must drop it too.
        with mock.patch(
            "conversations.services.post_message", side_effect=RuntimeError("boom")
        ), self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                services.booking_from_recommendation(
                    recommendation=self.recommendation,
                    customer=self.customer,
                    travel_start=timezone.localdate() + timedelta(days=20),
                )
        self.assertEqual(mail.outbox, [])

    def test_cancellation_and_refund_notices(self):
        booking = self.pay(self.book_from_chat())
        payment = booking.payments.get(status=PaymentStatus.SUCCESS)
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            services.cancel_booking(booking=booking, actor=self.manager, reason="Visa refused")
        self.assertEqual(mail.outbox[0].subject, f"Booking {booking.booking_number} cancelled")
        self.assertIn("Visa refused", mail.outbox[0].body)
        self.assertIn("refunded", mail.outbox[0].body)

        with self.captureOnCommitCallbacks(execute=True):
            services.record_refund(
                payment=payment, refund={"id": "rfnd_1", "amount": 2520000},
                payment_entity={"refund_status": "full"},
            )
        self.assertEqual(
            mail.outbox[1].subject, f"Refund processed for booking {booking.booking_number}"
        )
        self.assertIn("INR 25,200.00", mail.outbox[1].body)

    def test_staff_booking_issues_a_link_and_emails_it_when_ticked(self):
        self.client.force_login(self.agent)
        data = {
            "customer": self.customer.pk,
            "website": self.website.pk,
            "product_type": "package",
            "product_id": self.package.pk,
            "product_name": "Goa Escape",
            "travelers_count": 2,
            "currency": "INR",
            "subtotal": "1000",
        }
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("bookings:create"), dict(data, email_customer="on"))
        booking = Booking.objects.get()
        self.assertTrue(services.has_live_payment_link(booking))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"https://umrah.example/pay/{booking.payment_token}/", mail.outbox[0].body)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("bookings:create"), data)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_issue_new_link_can_email_the_customer(self):
        booking = self.book_from_chat()
        old_token = booking.payment_token
        mail.outbox.clear()
        self.client.force_login(self.manager)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("bookings:payment_link", args=[booking.pk]))
        self.assertEqual(mail.outbox, [])

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("bookings:payment_link", args=[booking.pk]), {"send_email": "1"}
            )
        booking.refresh_from_db()
        self.assertNotEqual(booking.payment_token, old_token)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(booking.payment_token, mail.outbox[0].body)

        page = self.client.get(reverse("bookings:detail", args=[booking.pk])).content.decode()
        self.assertIn("Customer emails", page)
        self.assertIn("Payment link → ana@x.com", page)


# --------------------------------------------------------------------------
# Batch 6: hotel room offers in pricing, payment retry, role-aware buttons
# --------------------------------------------------------------------------

from inventory.models import HotelOffer  # noqa: E402


class OfferFixture(BookingFixture):
    def setUp(self):
        super().setUp()
        self.start = timezone.localdate() + timedelta(days=10)
        self.end = self.start + timedelta(days=3)
        self.hotel = Hotel.objects.create(name="Sea View", base_price=Decimal("5000"))
        # Cheapest, but its season ends before the last night of the stay.
        self.short = HotelOffer.objects.create(
            hotel=self.hotel, title="Flash sale", room_type="Standard", price=Decimal("2000"),
            valid_to=self.start + timedelta(days=1),
        )
        self.deluxe = HotelOffer.objects.create(
            hotel=self.hotel, title="Monsoon saver", room_type="Deluxe", price=Decimal("4000"),
            valid_from=self.start, valid_to=self.end, inclusions="Breakfast",
        )
        self.suite = HotelOffer.objects.create(
            hotel=self.hotel, title="Suite deal", room_type="Suite", price=Decimal("7000"),
        )

    def quote(self, **kwargs):
        return services.quote_item(
            inventory_type="hotel", item=self.hotel, travel_start=self.start,
            travel_end=self.end, travelers=2, **kwargs,
        )


class OfferQuoteTests(OfferFixture):
    def test_the_cheapest_offer_valid_for_every_night_prices_the_stay(self):
        quote = self.quote()
        self.assertEqual(quote["subtotal"], Decimal("12000.00"))
        self.assertEqual(quote["offer"]["id"], self.deluxe.pk)
        self.assertEqual(quote["description"], "3 nights × INR 4,000 (Deluxe)")

    def test_inactive_offers_are_ignored(self):
        HotelOffer.objects.filter(pk=self.deluxe.pk).update(is_active=False)
        self.assertEqual(self.quote()["offer"]["id"], self.suite.pk)

    def test_with_no_valid_offer_the_base_price_applies(self):
        HotelOffer.objects.filter(hotel=self.hotel).update(is_active=False)
        quote = self.quote()
        self.assertIsNone(quote["offer"])
        self.assertEqual(quote["subtotal"], Decimal("15000.00"))

    def test_a_chosen_offer_is_used_even_when_dearer(self):
        quote = self.quote(offer=self.suite)
        self.assertEqual(quote["subtotal"], Decimal("21000.00"))
        self.assertEqual(quote["offer"]["room_type"], "Suite")

    def test_a_chosen_offer_must_cover_the_dates_and_the_hotel(self):
        with self.assertRaises(services.BookingError):
            self.quote(offer=self.short)
        other = Hotel.objects.create(name="Other", base_price=Decimal("1"))
        foreign = HotelOffer.objects.create(hotel=other, title="X", price=Decimal("10"))
        with self.assertRaises(services.BookingError):
            self.quote(offer=foreign)


class OfferBookingTests(OfferFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.conversation, _ = chat_services.start_conversation(website=self.website)
        self.conversation.lead = self.lead
        self.conversation.customer = self.customer
        self.conversation.save()
        self.recommendation = Recommendation.objects.create(
            conversation=self.conversation, inventory_type="hotel", object_id=self.hotel.pk,
            title=self.hotel.name, price=Decimal("7000"),
            payload={"offer": {"id": self.suite.pk, "title": "Stale"}},
        )

    def test_a_chat_booking_is_priced_with_the_offer_and_keeps_it(self):
        with self.captureOnCommitCallbacks(execute=True):
            booking, _ = services.booking_from_recommendation(
                recommendation=self.recommendation, customer=self.customer,
                travel_start=self.start, travel_end=self.end, travelers=2,
            )
        self.assertEqual(booking.subtotal, Decimal("12000.00"))
        self.assertEqual(booking.total_amount, Decimal("12600.00"))
        self.assertEqual(booking.summary["offer"]["title"], "Monsoon saver")
        self.assertEqual(booking.room_label, "Deluxe")

        page = self.client.get(reverse("public:pay", args=[booking.payment_token]))
        self.assertContains(page, "Deluxe")
        self.assertContains(page, "Includes Breakfast")
        to_customer = [m.body for m in mail.outbox if "ana@x.com" in m.to]
        self.assertTrue(to_customer and "Room:       Deluxe" in to_customer[-1])

        self.client.force_login(self.manager)
        detail = self.client.get(reverse("bookings:detail", args=[booking.pk]))
        self.assertContains(detail, "Monsoon saver")

    def test_an_offer_edited_later_does_not_rewrite_the_booking(self):
        booking, _ = services.booking_from_recommendation(
            recommendation=self.recommendation, customer=self.customer,
            travel_start=self.start, travel_end=self.end, travelers=2,
        )
        HotelOffer.objects.filter(pk=self.deluxe.pk).update(room_type="Renamed")
        booking.refresh_from_db()
        self.assertEqual(booking.room_label, "Deluxe")


class StaffOfferBookingTests(OfferFixture):
    def post(self, **fields):
        data = {
            "customer": self.customer.pk,
            "website": self.website.pk,
            "product_type": "hotel",
            "product_id": self.hotel.pk,
            "product_name": "",
            "travel_start": self.start.isoformat(),
            "travel_end": self.end.isoformat(),
            "travelers_count": 2,
            "currency": "INR",
            "subtotal": "",
        }
        data.update(fields)
        self.client.force_login(self.agent)
        return self.client.post(reverse("bookings:create"), data)

    def test_the_form_offers_a_room_choice(self):
        self.client.force_login(self.agent)
        page = self.client.get(reverse("bookings:create"))
        self.assertContains(page, "Sea View (#%d) · Suite" % self.hotel.pk)

    def test_a_blank_subtotal_is_priced_from_the_chosen_offer(self):
        self.post(hotel_offer=self.suite.pk)
        booking = Booking.objects.get()
        self.assertEqual(booking.subtotal, Decimal("21000.00"))
        self.assertEqual(booking.product_name, "Sea View")
        self.assertEqual(booking.room_label, "Suite")

    def test_without_a_choice_the_rule_picks_the_offer(self):
        self.post()
        booking = Booking.objects.get()
        self.assertEqual(booking.subtotal, Decimal("12000.00"))
        self.assertEqual(booking.room_offer["id"], self.deluxe.pk)

    def test_a_typed_subtotal_wins_but_the_offer_is_recorded(self):
        self.post(hotel_offer=self.suite.pk, subtotal="15000")
        booking = Booking.objects.get()
        self.assertEqual(booking.subtotal, Decimal("15000.00"))
        self.assertEqual(booking.room_label, "Suite")

    def test_an_offer_from_another_hotel_or_dates_it_does_not_cover_is_refused(self):
        other = Hotel.objects.create(name="Other", base_price=Decimal("100"))
        response = self.post(product_id=other.pk, hotel_offer=self.suite.pk)
        self.assertEqual(response.status_code, 200)
        response = self.post(hotel_offer=self.short.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not available for these dates")
        self.assertEqual(Booking.objects.count(), 0)


