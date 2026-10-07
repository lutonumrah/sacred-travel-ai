"""The headline journey: a visitor chats on a brand's site and ends up a paying customer."""

import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.urls import reverse

from bookings.models import Booking, BookingStatus, PaymentStatus
from conversations.models import MessageSender
from crm.models import Customer, Lead, LeadSource, LeadStatus

from .base import SITE_ORIGIN, JourneyTestCase


class WidgetBookingJourneyTests(JourneyTestCase):
    def test_chat_to_confirmed_booking_end_to_end(self):
        start = self.trip_start()
        end = start + timedelta(days=3)

        # 1. First message from the brand's site: recommendations, and a lead.
        first = self.widget_chat(
            "Hi! We want a hotel in Goa for 2 people",
            attribution={"utm_source": "instagram", "utm_campaign": "monsoon"},
        )
        session = first["session"]
        self.assertTrue(first["is_new_session"])
        self.assertEqual(first["status"], "ai_active")
        self.assertIsNotNone(first["reply"])
        titles = {card["title"] for card in first["recommendations"]}
        self.assertIn("Palm Stay", titles)
        # Only this website's visible inventory is ever offered.
        self.assertNotIn("Secret Cove Resort", titles)
        self.assertTrue(titles <= {"Palm Stay", "Sea Breeze Inn"})
        self.assertTrue(all(card["bookable"] for card in first["recommendations"]))

        conversation = self.conversation(session)
        lead = conversation.lead
        self.assertIsNotNone(lead, "a destination is enough to open a lead")
        self.assertEqual(lead.source, LeadSource.AI_CHAT)
        self.assertEqual(lead.website, self.website)
        self.assertEqual(lead.destination, "Goa")
        self.assertEqual(lead.status, LeadStatus.NEW)
        self.assertEqual(lead.utm_campaign, "monsoon")
        self.assertTrue(
            self.notifications_for(self.manager, notification_type="lead").filter(
                metadata__lead_id=lead.pk
            )
        )
        self.assertTrue(self.outbox_to(self.manager.email, "[Lead]"))

        # 2. Dates and contact details: the lead qualifies itself.
        second = self.widget_chat(
            f"From {start.isoformat()} to {end.isoformat()}. "
            "I'm Ana, ana@example.com, 9810000000",
            session=session,
        )
        self.assertFalse(second["is_new_session"])
        self.assertEqual(second["known"]["email"], "ana@example.com")
        self.assertEqual(second["known"]["travel_start"], start.isoformat())
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)
        self.assertEqual(lead.travel_start, start)
        self.assertEqual(lead.travelers_count, 2)
        customer = lead.customer
        self.assertEqual(customer.email, "ana@example.com")
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Lead.objects.count(), 1, "later turns enrich the same lead")

        # 3. "Book this" on the Palm Stay card: details already given aren't retyped.
        card = next(c for c in first["recommendations"] if c["title"] == "Palm Stay")
        booked = self.widget_book(
            session,
            card["recommendation_id"],
            email="",
            phone="",
            travel_start=start.isoformat(),
            travel_end=end.isoformat(),
            # The browser cannot set the price.
            total="1",
            subtotal="1",
        )
        self.assertEqual(booked.status_code, 201, booked.content)
        payload = booked.json()["data"]
        booking = Booking.objects.get(booking_number=payload["booking"]["number"])
        self.assertEqual(booking.status, BookingStatus.PENDING)
        self.assertEqual(booking.subtotal, Decimal("9000.00"))  # 3 nights x 3,000
        self.assertEqual(booking.total_amount, Decimal("9450.00"))  # + 5% tax
        self.assertEqual(booking.lead, lead)
        self.assertEqual(booking.conversation, conversation)
        self.assertEqual(booking.utm_campaign, "monsoon")
        token = booking.payment_token
        self.assertIn(f"/pay/{token}/", payload["payment_url"])
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.PAYMENT_PENDING)

        link_emails = self.outbox_to("ana@example.com", "complete your payment")
        self.assertEqual(len(link_emails), 1)
        self.assertIn(
            f"https://crm.scared-travel.example/pay/{token}/", link_emails[0].body
        )
        self.assertIn(booking.booking_number, link_emails[0].body)

        # 4. The customer opens the link from the email: no login needed.
        page = self.call(self.customer_browser, "get", reverse("public:pay", args=[token]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Cache-Control"], "no-store")
        self.assertContains(page, booking.booking_number)
        self.assertContains(page, "Palm Stay")
        self.assertContains(page, "9450.00")

        # 5. Order + simulated checkout through the public pay API.
        order = self.pay_api("order", token)
        self.assertEqual(order.status_code, 200, order.content)
        self.assertEqual(order["Access-Control-Allow-Origin"], SITE_ORIGIN)
        order_data = order.json()["data"]
        self.assertTrue(order_data["simulated"])
        self.assertEqual(order_data["amount"], 945000)
        paid = self.pay_api("simulate", token)
        self.assertEqual(paid.status_code, 200, paid.content)
        self.assertEqual(paid.json()["data"]["status"], BookingStatus.CONFIRMED)

        # 6. Everything downstream of the payment.
        booking.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertIsNotNone(booking.confirmed_at)
        payment = booking.payments.get()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)
        self.assertEqual(payment.razorpay_order_id, order_data["order_id"])
        self.assertEqual(lead.status, LeadStatus.CONVERTED)

        confirmations = self.outbox_to("ana@example.com", "Booking confirmed")
        self.assertEqual(len(confirmations), 1)
        self.assertIn(payment.razorpay_payment_id, confirmations[0].body)

        events = list(
            conversation.messages.filter(sender_type=MessageSender.SYSTEM).values_list(
                "metadata__event", flat=True
            )
        )
        self.assertIn("booking_created", events)
        self.assertIn("booking_confirmed", events)

        self.assertTrue(
            self.notifications_for(self.manager, notification_type="payment").filter(
                title__contains=booking.booking_number, metadata__booking_id=booking.pk
            )
        )
        self.assertTrue(self.outbox_to(self.manager.email, f"Booking confirmed: {booking.booking_number}"))
        self.assertLessEqual(
            {
                "booking.create",
                "booking.payment_link_issue",
                "payment.order_create",
                "booking.confirmed",
                "booking.email_sent",
            },
            self.audit_actions(booking),
        )
        self.assertIn("lead.create", self.audit_actions(lead))
        # The lead timeline tells the whole story.
        activity = set(lead.activities.values_list("activity_type", flat=True))
        self.assertLessEqual({"created", "status_change", "email"}, activity)

        # 7. The widget sees the confirmation when it polls.
        history = self.widget_history(session)
        self.assertEqual(history["bookings"][0]["status"], BookingStatus.CONFIRMED)
        self.assertEqual(history["bookings"][0]["payment_url"], "", "no pay link once paid")
        self.assertIn("booking_confirmed", [m["event"] for m in history["messages"]])
        # Paying twice is not possible.
        self.assertEqual(self.pay_api("order", token).status_code, 409)
        repaid = self.call(self.customer_browser, "get", reverse("public:pay", args=[token]))
        self.assertNotContains(repaid, 'id="pay-now"')
        self.assertNotContains(repaid, 'id="simulate-pay"')

        # 8. Reports and analytics count it.
        reports = self.staff_get(self.manager, reverse("api_dashboard:reports"), {"days": 7})
        self.assertEqual(reports.status_code, 200)
        report = reports.json()["data"]
        self.assertEqual(report["kpis"]["leads_total"], 1)
        self.assertEqual(report["kpis"]["leads_converted"], 1)
        self.assertEqual(report["kpis"]["bookings_confirmed"], 1)
        self.assertEqual(Decimal(str(report["kpis"]["revenue"])), Decimal("9450"))
        self.assertEqual(
            {row["source"]: row["total"] for row in report["leads_by_source"]}.get("ai_chat"), 1
        )
        funnel = report["conversion"]
        site_row = next(row for row in funnel if row["website"] == self.website.name)
        self.assertEqual(
            (site_row["chats"], site_row["leads"], site_row["qualified"], site_row["bookings"], site_row["paid"]),
            (1, 1, 1, 1, 1),
        )
        campaign_row = next(
            row for row in report["campaigns"] if row["utm_campaign"] == "monsoon"
        )
        self.assertEqual(campaign_row["paid"], 1)

        analytics = self.staff_get(self.manager, reverse("api_dashboard:analytics")).json()["data"]
        site = next(row for row in analytics["websites"] if row["id"] == self.website.pk)
        self.assertEqual(
            (site["conversations"], site["chats_with_lead"], site["leads"], site["converted"],
             site["bookings"], site["paid_bookings"]),
            (1, 1, 1, 1, 1, 1),
        )
        self.assertEqual(Decimal(str(site["revenue"])), Decimal("9450"))

        export = self.staff_get(
            self.manager, reverse("dashboard:report_export", args=["bookings"])
        )
        self.assertEqual(export.status_code, 200)
        rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
        self.assertIn(booking.booking_number, [cell for row in rows for cell in row])

        overview = self.staff_get(self.manager, reverse("dashboard:overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.context["kpis"]["bookings_confirmed"], 1)

        # The staff side shows the same booking, chat and lead.
        detail = self.staff_get(self.manager, reverse("bookings:detail", args=[booking.pk]))
        self.assertContains(detail, booking.booking_number)
        self.assertContains(detail, "Confirmed")
        chat = self.staff_get(self.manager, reverse("conversations:detail", args=[conversation.pk]))
        self.assertContains(chat, "Palm Stay")
        self.assertContains(chat, booking.booking_number)
        lead_page = self.staff_get(self.manager, reverse("crm:lead_detail", args=[lead.pk]))
        self.assertContains(lead_page, booking.booking_number)
        self.assertContains(lead_page, "monsoon")

    def test_a_foreign_origin_gets_no_cors_grant(self):
        response = self.call(
            self.customer_browser,
            "post",
            reverse("api_conversations:widget_chat"),
            {"key": self.key, "message": "hotel in Goa"},
            HTTP_ORIGIN="https://hill-trails.example",
        )
        # The API answers, but the browser is not allowed to read it.
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_staff_can_book_for_the_customer_from_the_conversation_page(self):
        data = self.widget_chat("hotel in Goa for 2 people, I'm ana@example.com")
        conversation = self.conversation(data["session"])
        card = data["recommendations"][0]
        start = self.trip_start()
        response = self.staff_post(
            self.agent,
            reverse("conversations:book", args=[conversation.pk]),
            {
                "recommendation_id": card["recommendation_id"],
                "travel_start": start.isoformat(),
                "travel_end": (start + timedelta(days=2)).isoformat(),
                "travelers": 2,
            },
        )
        booking = Booking.objects.get()
        self.assertRedirects(response, reverse("bookings:detail", args=[booking.pk]))
        self.assertEqual(booking.created_by, self.agent)
        self.assertEqual(len(self.outbox_to("ana@example.com", "complete your payment")), 1)

        # The customer is told in the chat, without staff login names.
        history = self.widget_history(data["session"])
        created = [m for m in history["messages"] if m["event"] == "booking_created"]
        self.assertEqual(len(created), 1)
        self.assertIn(booking.booking_number, created[0]["content"])
        self.assertNotIn(self.agent.username, str(history))

        # Reissue the link and email it: the old one dies.
        old = booking.payment_token
        self.staff_post(
            self.agent, reverse("bookings:payment_link", args=[booking.pk]), {"send_email": "1"}
        )
        booking.refresh_from_db()
        self.assertNotEqual(booking.payment_token, old)
        self.assertEqual(
            self.call(self.customer_browser, "get", reverse("public:pay", args=[old])).status_code,
            404,
        )
        self.assertEqual(len(self.outbox_to("ana@example.com", "complete your payment")), 2)
        self.assertEqual(len(mail.outbox[-1].to), 1)


class SuperuserAlertTests(JourneyTestCase):
    def test_a_createsuperuser_account_gets_the_manager_alerts(self):
        from accounts.models import User

        # `createsuperuser` leaves role at its default ("employee").
        owner = User.objects.create_superuser("owner", "owner@scared-travel.example", "pw-12345")
        self.assertEqual(owner.role, "employee")
        _session, booking = self.chat_to_pending_booking()
        self.assertTrue(self.notifications_for(owner, notification_type="lead"))
        self.pay_api("simulate", booking.payment_token)
        self.assertTrue(
            self.notifications_for(owner, title=f"Booking confirmed: {booking.booking_number}")
        )
        self.assertTrue(self.outbox_to(owner.email, "[Lead]"))
        # Booking-level alerts (e.g. cancellation) reach them as well.
        self.staff_post(self.manager, reverse("bookings:cancel", args=[booking.pk]))
        self.assertTrue(self.notifications_for(owner, title__startswith="Booking cancelled"))
