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

