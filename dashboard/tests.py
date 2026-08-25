from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings import payments as gateway
from bookings import services as booking_services
from bookings.models import Booking, BookingStatus, Notification
from conversations import services as conversation_services
from crm import services as crm_services
from crm.models import Customer, Lead, LeadStatus
from inventory.models import Destination, Hotel, TourPackage
from websites.models import Website

from . import selectors, services


class DashboardFixture(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.customer = Customer.objects.create(first_name="Ana", email="ana@x.com")
        destination = Destination.objects.create(name="Goa", code="goa")
        Hotel.objects.create(name="Palm Stay", destination=destination, base_price=Decimal("3000"))
        self.package = TourPackage.objects.create(
            name="Goa Escape", destination=destination, base_price=Decimal("20000")
        )

    def make_lead(self, status=LeadStatus.NEW, **kwargs):
        lead = Lead(
            customer=self.customer,
            website=self.website,
            title="Goa trip",
            destination=kwargs.pop("destination", "Goa"),
            status=status,
            assigned_to=kwargs.pop("assigned_to", self.agent),
            **kwargs,
        )
        return crm_services.create_lead(lead=lead, actor=self.manager)

    def settle_a_booking(self):
        lead = self.make_lead()
        booking = booking_services.create_booking(
            booking=Booking(
                website=self.website,
                customer=self.customer,
                lead=lead,
                product_type="package",
                product_id=self.package.pk,
                product_name=self.package.name,
                subtotal=Decimal("20000"),
                status=BookingStatus.PENDING,
            ),
            actor=self.agent,
        )
        payment = booking_services.create_payment_order(booking=booking, actor=self.agent)
        payment_id, signature = gateway.simulate_payment(payment.razorpay_order_id)
        booking_services.verify_payment(
            order_id=payment.razorpay_order_id,
            payment_id=payment_id,
            signature=signature,
            actor=self.agent,
        )
        return booking


class KPITests(DashboardFixture):
    def test_counts_leads_and_conversion_rate(self):
        self.make_lead()
        self.make_lead(status=LeadStatus.CONVERTED)

        kpis = selectors.overview_kpis()
        self.assertEqual(kpis["leads_total"], 2)
        self.assertEqual(kpis["leads_converted"], 1)
        self.assertEqual(kpis["conversion_rate"], 50.0)

    def test_conversion_rate_is_zero_rather_than_dividing_by_zero(self):
        self.assertEqual(selectors.overview_kpis()["conversion_rate"], 0.0)

    def test_revenue_counts_only_settled_payments(self):
        self.settle_a_booking()
        kpis = selectors.overview_kpis()
        self.assertEqual(kpis["revenue"], Decimal("21000.00"))

    def test_website_filter_narrows_the_numbers(self):
        other = Website.objects.create(
            name="Other", domain="other.com", source_identifier="other"
        )
        self.make_lead()
        self.assertEqual(selectors.overview_kpis(website=self.website)["leads_total"], 1)
        self.assertEqual(selectors.overview_kpis(website=other)["leads_total"], 0)

    def test_inventory_counts_are_reported(self):
        counts = selectors.overview_kpis()["inventory_counts"]
        self.assertEqual(counts["hotels"], 1)
        self.assertEqual(counts["packages"], 1)

    def test_live_conversation_counts(self):
        conversation, _ = conversation_services.start_conversation(website=self.website)
        conversation_services.request_handoff(conversation=conversation, reason="test")
        kpis = selectors.overview_kpis()
        self.assertEqual(kpis["conversations_live"], 1)
        self.assertEqual(kpis["conversations_waiting"], 1)


class ReportTests(DashboardFixture):
    def test_leads_group_by_source_and_status(self):
        self.make_lead(source="phone")
        self.make_lead(source="phone", status=LeadStatus.CONVERTED)

        by_source = selectors.leads_by_source()
        self.assertEqual(by_source[0]["source"], "phone")
        self.assertEqual(by_source[0]["total"], 2)
        self.assertEqual(len(selectors.leads_by_status()), 2)

    def test_top_destinations_ignores_blank_destinations(self):
        self.make_lead()
        self.make_lead(destination="")
        rows = selectors.top_destinations()
        self.assertEqual(rows, [{"destination": "Goa", "total": 1}])

    def test_website_performance_rolls_up_revenue(self):
        self.settle_a_booking()
        row = selectors.website_performance()[0]
        self.assertEqual(row["website"], self.website)
        self.assertEqual(row["revenue"], Decimal("21000.00"))
        self.assertEqual(row["bookings"], 1)

    def test_employee_performance_tracks_the_assignee(self):
        self.make_lead(status=LeadStatus.CONVERTED)
        rows = {row["user"].username: row for row in selectors.employee_performance()}
        self.assertEqual(rows["agent"]["converted"], 1)
        self.assertEqual(rows["mgr"]["leads"], 0)

    def test_booking_report_groups_by_product_type(self):
        self.settle_a_booking()
        report = selectors.booking_report()
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["by_product"][0]["product_type"], "package")


class NotificationTests(DashboardFixture):
    def test_mark_one_read(self):
        Notification.objects.create(
            recipient=self.agent, notification_type="lead", title="One"
        )
        other = Notification.objects.create(
            recipient=self.agent, notification_type="lead", title="Two"
        )
        services.mark_read(user=self.agent, notification_id=other.pk)

        other.refresh_from_db()
        self.assertTrue(other.is_read)
        self.assertEqual(
            Notification.objects.filter(recipient=self.agent, is_read=False).count(), 1
        )

    def test_mark_all_read(self):
        for index in range(3):
            Notification.objects.create(
                recipient=self.agent, notification_type="lead", title=str(index)
            )
        services.mark_read(user=self.agent)
        self.assertEqual(
            Notification.objects.filter(recipient=self.agent, is_read=False).count(), 0
        )

    def test_you_cannot_mark_someone_elses_notification_read(self):
        theirs = Notification.objects.create(
            recipient=self.manager, notification_type="lead", title="Private"
        )
        services.mark_read(user=self.agent, notification_id=theirs.pk)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_due_reminders_are_surfaced(self):
        from crm.models import FollowUpTask

        lead = self.make_lead()
        FollowUpTask.objects.create(
            lead=lead,
            assigned_to=self.agent,
            title="Call",
            due_at=timezone.now() + timedelta(days=1),
            reminder_at=timezone.now() - timedelta(minutes=5),
        )
        self.assertEqual(services.follow_up_reminders_due(user=self.agent).count(), 1)


class DashboardViewTests(DashboardFixture):
    def test_the_overview_renders_for_an_employee(self):
        self.make_lead()
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leads")

    def test_reports_are_manager_only(self):
        self.client.force_login(self.agent)
        self.assertRedirects(
            self.client.get(reverse("dashboard:reports")), reverse("dashboard:overview")
        )
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("dashboard:reports")).status_code, 200)

    def test_analytics_are_manager_only(self):
        self.client.force_login(self.agent)
        self.assertRedirects(
            self.client.get(reverse("dashboard:analytics")), reverse("dashboard:overview")
        )

    def test_csv_export_returns_a_downloadable_file(self):
        self.make_lead()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:report_export", args=["leads"]))
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("Goa trip", response.content.decode())

    def test_the_sidebar_shows_an_unread_badge(self):
        Notification.objects.create(
            recipient=self.agent, notification_type="lead", title="Ping"
        )
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.context["unread_notifications"], 1)

    def test_a_bad_days_parameter_falls_back_to_the_default(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:overview"), {"days": "abc"})
        self.assertEqual(response.context["days"], 30)

    def test_days_is_clamped_to_a_sane_range(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:overview"), {"days": "9999"})
        self.assertEqual(response.context["days"], 365)


class APITests(DashboardFixture):
    def test_health_check_is_public(self):
        response = self.client.get(reverse("api_accounts:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "ok")

    def test_api_endpoints_require_authentication(self):
        self.assertEqual(self.client.get(reverse("api_dashboard:overview")).status_code, 403)

    def test_the_overview_api_returns_the_kpi_envelope(self):
        self.make_lead()
        self.client.force_login(self.manager)
        body = self.client.get(reverse("api_dashboard:overview")).json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["leads_total"], 1)

    def test_list_endpoints_are_paginated_inside_the_envelope(self):
        self.make_lead()
        self.client.force_login(self.manager)
        body = self.client.get(reverse("api_crm:leads")).json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["count"], 1)
        self.assertEqual(body["data"]["results"][0]["title"], "Goa trip")

    def test_the_inventory_search_api_matches_the_dashboard_search(self):
        self.client.force_login(self.agent)
        body = self.client.get(
            reverse("api_inventory:search"), {"destination": "Goa"}
        ).json()
        names = [row["name"] for row in body["data"]["results"]]
        self.assertIn("Palm Stay", names)

    def test_a_missing_record_returns_the_error_envelope(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("api_crm:lead_detail", args=[99999]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
