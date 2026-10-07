"""Payment testing: Razorpay webhooks against a booking made in the widget.

Simulation mode signs webhooks with the public simulation secret, exactly as
Razorpay signs them with the configured webhook secret in live mode (HMAC of
the raw body), so the real verification path runs.
"""

from django.test import override_settings
from django.urls import reverse

from bookings.models import BookingStatus, Payment, PaymentStatus, WebhookEvent
from crm.models import LeadStatus

from .base import JourneyTestCase


class PaymentWebhookScenarioTests(JourneyTestCase):
    def setUp(self):
        super().setUp()
        self.session, self.booking = self.chat_to_pending_booking()
        order = self.pay_api("order", self.booking.payment_token)
        self.assertEqual(order.status_code, 200, order.content)
        self.order_id = order.json()["data"]["order_id"]
        self.paise = order.json()["data"]["amount"]
        self.lead = self.booking.lead

    def payment_entity(self, payment_id="pay_test001", **overrides):
        entity = {
            "id": payment_id,
            "order_id": self.order_id,
            "amount": self.paise,
            "currency": "INR",
            "status": "captured",
            "method": "upi",
        }
        entity.update(overrides)
        return entity

    def captured(self, event_id="evt_cap_1", **overrides):
        return self.webhook(
            "payment.captured", event_id=event_id, payment=self.payment_entity(**overrides)
        )

    def refresh(self):
        self.booking.refresh_from_db()
        self.lead.refresh_from_db()
        return self.booking.payments.order_by("pk").last()

    # -------------------------------------------------------------- success

    def test_captured_webhook_confirms_the_booking(self):
        response = self.captured()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["result"], "payment captured")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(payment.razorpay_payment_id, "pay_test001")
        self.assertEqual(payment.method, "upi")
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)
        self.assertEqual(len(self.outbox_to("ana@example.com", "Booking confirmed")), 1)
        self.assertTrue(
            self.notifications_for(self.manager, title=f"Booking confirmed: {self.booking.booking_number}")
        )
        # The customer's open widget learns about it.
        history = self.widget_history(self.session)
        self.assertEqual(history["bookings"][0]["status"], BookingStatus.CONFIRMED)

    def test_order_paid_without_a_payment_entity_also_confirms(self):
        response = self.webhook(
            "order.paid",
            event_id="evt_order_1",
            order={"id": self.order_id, "amount_paid": self.paise, "currency": "INR"},
        )
        self.assertEqual(response.json()["data"]["result"], "payment captured")
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_duplicate_event_ids_are_applied_once(self):
        self.captured(event_id="evt_dup")
        notifications = self.notifications_for(self.manager).count()
        again = self.captured(event_id="evt_dup", amount=1)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["data"]["result"], "ignored: duplicate event")
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_dup").count(), 1)
        self.assertEqual(len(self.outbox_to("ana@example.com", "Booking confirmed")), 1)
        self.assertEqual(self.notifications_for(self.manager).count(), notifications)
        # A different event id for the same capture is still a no-op.
        self.assertEqual(self.captured(event_id="evt_dup_2").json()["data"]["result"], "payment captured")
        self.assertEqual(len(self.outbox_to("ana@example.com", "Booking confirmed")), 1)

    def test_a_bad_signature_is_rejected_and_changes_nothing(self):
        response = self.webhook(
            "payment.captured", event_id="evt_forged", payment=self.payment_entity(), secret="guess"
        )
        self.assertEqual(response.status_code, 400)
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertFalse(WebhookEvent.objects.filter(event_id="evt_forged").exists())

    @override_settings(RAZORPAY_KEY_ID="rzp_live_x", RAZORPAY_KEY_SECRET="live-secret")
    def test_live_keys_without_a_webhook_secret_reject_simulation_signatures(self):
        response = self.captured(event_id="evt_live")
        self.assertEqual(response.status_code, 400)
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

    # -------------------------------------------------------------- failure

    def test_a_failed_payment_can_be_retried_from_the_same_link(self):
        response = self.webhook(
            "payment.failed",
            event_id="evt_fail_1",
            payment=self.payment_entity(status="failed", error_description="Card declined"),
        )
        self.assertEqual(response.json()["data"]["result"], "payment failed")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.FAILED)
        self.assertEqual(payment.failure_reason, "Card declined")
        self.assertEqual(self.booking.status, BookingStatus.FAILED)
        self.assertTrue(self.notifications_for(self.manager, title__startswith="Payment failed"))
        self.assertIn("payment.failed", self.audit_actions(self.booking))

        # The pay page still works and a new order is opened.
        page = self.call(
            self.customer_browser, "get", reverse("public:pay", args=[self.booking.payment_token])
        )
        self.assertContains(page, 'id="simulate-pay"')
        retry = self.pay_api("order", self.booking.payment_token).json()["data"]
        self.assertNotEqual(retry["order_id"], self.order_id)
        paid = self.pay_api("simulate", self.booking.payment_token)
        self.assertEqual(paid.json()["data"]["status"], BookingStatus.CONFIRMED)
        self.refresh()
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)

    def test_a_late_failure_never_downgrades_a_confirmed_booking(self):
        self.captured()
        response = self.webhook(
            "payment.failed",
            event_id="evt_fail_late",
            payment=self.payment_entity(payment_id="pay_other", status="failed"),
        )
        self.assertEqual(response.status_code, 200)
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)
        self.assertIn("payment.failed_ignored", self.audit_actions(self.booking))

    # -------------------------------------------------------- amount checks

    def test_an_amount_mismatch_is_flagged_not_confirmed(self):
        response = self.captured(amount=self.paise - 100)
        self.assertEqual(response.json()["data"]["result"], "ignored: amount mismatch")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.CREATED)
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertIn("payment.amount_mismatch", self.audit_actions(self.booking))
        self.assertTrue(self.notifications_for(self.manager, title__contains="mismatch"))
        self.assertFalse(self.outbox_to("ana@example.com", "Booking confirmed"))
        # A wrong currency is just as wrong.
        self.assertEqual(
            self.captured(event_id="evt_usd", currency="USD").json()["data"]["result"],
            "ignored: amount mismatch",
        )

    # ------------------------------------------------------- cancellation

    def test_staff_cancellation_closes_the_link_and_flags_a_late_payment(self):
        # Employees cannot cancel; managers can.
        self.staff_post(self.agent, reverse("bookings:cancel", args=[self.booking.pk]), {"reason": "x"})
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)

        response = self.staff_post(
            self.manager,
            reverse("bookings:cancel", args=[self.booking.pk]),
            {"reason": "Customer changed plans"},
        )
        self.assertRedirects(response, reverse("bookings:detail", args=[self.booking.pk]))
        payment = self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.CANCELLED)
        self.assertEqual(payment.status, PaymentStatus.CANCELLED)
        self.assertEqual(self.lead.status, LeadStatus.INTERESTED)
        self.assertEqual(len(self.outbox_to("ana@example.com", "cancelled")), 1)
        self.assertIn("booking.cancel", self.audit_actions(self.booking))

        # The customer can no longer pay through the link.
        self.assertEqual(self.pay_api("order", self.booking.payment_token).status_code, 409)
        self.assertEqual(self.pay_api("simulate", self.booking.payment_token).status_code, 409)

        # Razorpay reports a capture on the closed order anyway: recorded, not confirmed.
        self.captured(event_id="evt_after_cancel")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.CANCELLED)
        self.assertIn("payment.after_cancel", self.audit_actions(self.booking))
        self.assertTrue(self.notifications_for(self.manager, body__contains="refund it in Razorpay"))
        self.assertFalse(self.outbox_to("ana@example.com", "Booking confirmed"))

    # -------------------------------------------------------------- refunds

    def test_a_full_refund_marks_the_booking_refunded_and_the_lead_lost(self):
        self.captured()
        response = self.webhook(
            "refund.processed",
            event_id="evt_refund_1",
            refund={"id": "rfnd_1", "payment_id": "pay_test001", "amount": self.paise},
            payment=self.payment_entity(refund_status="full"),
        )
        self.assertEqual(response.json()["data"]["result"], "refund recorded")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.REFUNDED)
        self.assertEqual(self.booking.status, BookingStatus.REFUNDED)
        self.assertEqual(self.lead.status, LeadStatus.LOST)
        self.assertEqual(len(self.outbox_to("ana@example.com", "Refund processed")), 1)
        self.assertIn("booking.refunded", self.audit_actions(self.booking))
        # Refunded money is no longer revenue.
        report = self.staff_get(self.manager, reverse("api_dashboard:reports")).json()["data"]
        self.assertEqual(report["kpis"]["bookings_confirmed"], 0)
        self.assertEqual(float(report["kpis"]["revenue"]), 0)

    def test_a_refund_event_carrying_only_the_payment_id_is_matched(self):
        self.captured()
        response = self.webhook(
            "refund.processed",
            event_id="evt_refund_2",
            refund={"id": "rfnd_2", "payment_id": "pay_test001", "amount": self.paise},
        )
        self.assertEqual(response.json()["data"]["result"], "refund recorded")
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.REFUNDED)

    def test_a_partial_refund_keeps_the_booking_confirmed(self):
        self.captured()
        response = self.webhook(
            "refund.processed",
            event_id="evt_refund_part",
            refund={"id": "rfnd_3", "payment_id": "pay_test001", "amount": 1000},
            payment=self.payment_entity(refund_status="partial"),
        )
        self.assertEqual(response.json()["data"]["result"], "partial refund recorded")
        payment = self.refresh()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(self.lead.status, LeadStatus.CONVERTED)
        self.assertTrue(self.notifications_for(self.manager, title__startswith="Partial refund"))

    # ------------------------------------------------ checkout verification

    def test_a_forged_checkout_signature_is_refused(self):
        response = self.pay_api(
            "verify",
            self.booking.payment_token,
            {
                "razorpay_order_id": self.order_id,
                "razorpay_payment_id": "pay_forged",
                "razorpay_signature": "0" * 64,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.refresh()
        self.assertEqual(self.booking.status, BookingStatus.PENDING)
        self.assertIn("payment.verify_failed", self.audit_actions(self.booking))

    def test_a_link_cannot_verify_another_bookings_order(self):
        _session, other = self.chat_to_pending_booking_for("bob@example.com")
        other_order = self.pay_api("order", other.payment_token).json()["data"]["order_id"]
        from bookings import payments

        payment_id, signature = payments.simulate_payment(other_order)
        response = self.pay_api(
            "verify",
            self.booking.payment_token,
            {
                "razorpay_order_id": other_order,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Payment.objects.filter(status=PaymentStatus.SUCCESS).count(), 0)

    def chat_to_pending_booking_for(self, email):
        data = self.widget_chat("hotel in Goa for 2 people")
        card = data["recommendations"][0]
        response = self.widget_book(
            data["session"], card["recommendation_id"], email=email, phone="9822222222", name="Bob"
        )
        self.assertEqual(response.status_code, 201, response.content)
        return data["session"], self.conversation(data["session"]).bookings.get()
