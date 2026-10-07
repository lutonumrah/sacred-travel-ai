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

    def test_reports_and_analytics_apis_are_manager_only(self):
        for name in ("api_dashboard:reports", "api_dashboard:analytics"):
            self.client.force_login(self.agent)
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403, name)
            self.assertFalse(response.json()["success"])
            self.client.force_login(self.manager)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_the_sidebar_matches_each_role(self):
        self.client.force_login(self.agent)
        page = self.client.get(reverse("dashboard:overview"))
        self.assertNotContains(page, reverse("dashboard:reports"))
        self.assertContains(page, reverse("crm:leads"))

        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(stock)
        page = self.client.get(reverse("dashboard:overview"))
        for name in ("crm:leads", "conversations:inbox", "bookings:list"):
            self.assertNotContains(page, f'href="{reverse(name)}"')

        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("dashboard:overview")), reverse("dashboard:reports"))


# --------------------------------------------------------------------------
# Batch 4: report filters, CSV exports, funnel, employee & website analytics
# --------------------------------------------------------------------------

import csv  # noqa: E402
import io  # noqa: E402

from conversations.models import (  # noqa: E402
    Conversation,
    ConversationHandoff,
    Message,
    MessageSender,
)
from crm.models import LeadSource  # noqa: E402


def read_csv(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


class ReportFilterTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.other_site = Website.objects.create(
            name="Other", domain="other.com", source_identifier="other"
        )
        self.booking = self.settle_a_booking()

    def test_the_revenue_kpi_honours_the_website(self):
        self.assertEqual(
            selectors.overview_kpis(website=self.website)["revenue"], Decimal("21000.00")
        )
        self.assertEqual(selectors.overview_kpis(website=self.other_site)["revenue"], 0)

    def test_an_explicit_date_range_wins_over_days(self):
        old = self.make_lead()
        Lead.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=200))
        start = timezone.localdate() - timedelta(days=210)
        end = timezone.localdate() - timedelta(days=190)
        kpis = selectors.overview_kpis(days=7, start=start, end=end)
        self.assertEqual(kpis["leads_total"], 1)
        self.assertEqual(kpis["revenue"], 0)
        self.assertEqual(selectors.period(7, None, None)[1], timezone.localdate())

    def test_the_reports_page_applies_website_and_range(self):
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("dashboard:reports"), {"website": self.other_site.pk, "days": "90"}
        )
        self.assertEqual(response.context["kpis"]["leads_total"], 0)
        self.assertEqual(response.context["kpis"]["revenue"], 0)
        # Export links carry the same filters.
        self.assertContains(response, f"website={self.other_site.pk}")
        response = self.client.get(reverse("dashboard:reports"), {"website": self.website.pk})
        self.assertEqual(response.context["kpis"]["revenue"], Decimal("21000.00"))
        self.assertTrue(response.context["funnel"])
        self.assertContains(response, "By campaign")

    def test_revenue_csv(self):
        self.client.force_login(self.manager)
        rows = read_csv(self.client.get(reverse("dashboard:report_export", args=["revenue"])))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Website"], "Main")
        self.assertEqual(rows[0]["Product type"], "package")
        self.assertEqual(rows[0]["Payments"], "1")
        self.assertEqual(rows[0]["Amount"], "21000.00")
        self.assertEqual(rows[0]["Date"], timezone.localdate().isoformat())
        rows = read_csv(
            self.client.get(
                reverse("dashboard:report_export", args=["revenue"]),
                {"website": self.other_site.pk},
            )
        )
        self.assertEqual(rows, [])

    def test_conversion_csv_has_the_funnel_by_website_source_and_campaign(self):
        Lead.objects.filter(pk=self.booking.lead_id).update(
            utm_source="google", utm_medium="cpc", utm_campaign="goa"
        )
        lost = self.make_lead(source=LeadSource.PHONE)
        crm_services.change_status(lead=lost, status=LeadStatus.QUALIFIED, actor=self.agent)
        crm_services.change_status(lead=lost, status=LeadStatus.LOST, actor=self.agent)
        Conversation.objects.create(session_key="c1", website=self.website, utm_campaign="goa",
                                    utm_source="google", utm_medium="cpc")
        Conversation.objects.create(session_key="c2", website=self.website)

        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:report_export", args=["conversion"]))
        rows = {
            (row["Source"], row["utm_campaign"]): row for row in read_csv(response)
        }
        paid = rows[("AI Chat", "goa")]
        self.assertEqual(paid["Website"], "Main")
        self.assertEqual(paid["Chats"], "1")
        # c1 never produced a lead of its own.
        self.assertEqual(paid["Chats with a lead"], "0")
        self.assertEqual(paid["Chat to lead %"], "0.0")
        self.assertEqual(paid["Leads"], "1")
        self.assertEqual(paid["Bookings"], "1")
        self.assertEqual(paid["Paid bookings"], "1")
        self.assertEqual(paid["Revenue"], "21000.00")
        self.assertEqual(paid["Lead to paid %"], "100.0")
        # Was qualified before it was lost: still counts as qualified.
        phone = rows[("Phone", "")]
        self.assertEqual(phone["Qualified"], "1")
        self.assertEqual(phone["Chats"], "")
        self.assertEqual(rows[("AI Chat", "")]["Chats"], "1")

    def test_campaign_breakdown(self):
        Lead.objects.filter(pk=self.booking.lead_id).update(
            utm_source="google", utm_medium="cpc", utm_campaign="goa"
        )
        rows = selectors.conversion_funnel(group_by=("campaign",))
        self.assertEqual(rows[0]["campaign"], "google / cpc / goa")
        self.assertEqual(rows[0]["revenue"], Decimal("21000.00"))

    def test_lead_and_booking_exports_honour_the_filters(self):
        other = Lead(customer=self.customer, website=self.other_site, title="Elsewhere")
        crm_services.create_lead(lead=other, actor=self.manager)
        self.client.force_login(self.manager)
        url = reverse("dashboard:report_export", args=["leads"])
        titles = [row["Title"] for row in read_csv(self.client.get(url))]
        self.assertIn("Elsewhere", titles)
        titles = [
            row["Title"]
            for row in read_csv(self.client.get(url, {"website": self.website.pk}))
        ]
        self.assertNotIn("Elsewhere", titles)
        self.assertIn("Goa trip", titles)

        future = (timezone.localdate() + timedelta(days=5)).isoformat()
        url = reverse("dashboard:report_export", args=["bookings"])
        self.assertEqual(len(read_csv(self.client.get(url))), 1)
        self.assertEqual(
            read_csv(self.client.get(url, {"date_from": future, "date_to": future})), []
        )
        rows = read_csv(self.client.get(url, {"website": self.website.pk}))
        self.assertEqual(rows[0]["Number"], self.booking.booking_number)

    def test_exports_are_manager_only(self):
        self.client.force_login(self.agent)
        for kind in ("leads", "bookings", "revenue", "conversion"):
            response = self.client.get(reverse("dashboard:report_export", args=[kind]))
            self.assertRedirects(response, reverse("dashboard:overview"))
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:report_export", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_the_reports_api_takes_the_same_filters(self):
        self.client.force_login(self.manager)
        body = self.client.get(
            reverse("api_dashboard:reports"), {"website": self.other_site.pk}
        ).json()["data"]
        self.assertEqual(body["website"], self.other_site.pk)
        self.assertEqual(body["kpis"]["revenue"], 0)
        self.assertIn("campaigns", body)


class AnalyticsTests(DashboardFixture):
    def take_over_and_reply(self, wait, reply_after):
        conversation = Conversation.objects.create(
            session_key=f"s{Conversation.objects.count()}", website=self.website
        )
        conversation_services.post_message(
            conversation=conversation, sender_type=MessageSender.CUSTOMER, content="Need help"
        )
        asked = Message.objects.filter(conversation=conversation).latest("created_at")
        start = timezone.now() - timedelta(hours=1)
        Message.objects.filter(pk=asked.pk).update(created_at=start)
        handoff = conversation_services.take_over(conversation=conversation, user=self.agent)
        ConversationHandoff.objects.filter(pk=handoff.pk).update(
            created_at=start + timedelta(seconds=wait)
        )
        reply = conversation_services.agent_reply(
            conversation=conversation, user=self.agent, text="Hi, I'm here"
        )
        Message.objects.filter(pk=reply.pk).update(
            created_at=start + timedelta(seconds=wait + reply_after)
        )
        return conversation

    def test_employee_rows_have_handoffs_response_time_and_chats(self):
        self.take_over_and_reply(wait=60, reply_after=60)  # waited 120s
        self.take_over_and_reply(wait=200, reply_after=40)  # waited 240s
        # Taken over but never answered: not part of the average.
        silent = Conversation.objects.create(session_key="silent", website=self.website)
        conversation_services.take_over(conversation=silent, user=self.agent)

        rows = {row["user"].username: row for row in selectors.employee_performance()}
        agent = rows["agent"]
        self.assertEqual(agent["handoffs"], 3)
        self.assertEqual(agent["responded_handoffs"], 2)
        self.assertEqual(agent["avg_first_response_seconds"], 180)
        self.assertEqual(agent["conversations"], 3)
        self.assertIsNone(rows["mgr"]["avg_first_response_seconds"])

    def test_the_wait_starts_at_the_first_unanswered_message(self):
        conversation = Conversation.objects.create(session_key="w", website=self.website)
        base = timezone.now() - timedelta(hours=2)
        for offset, sender in ((0, MessageSender.CUSTOMER), (10, MessageSender.AI),
                               (500, MessageSender.CUSTOMER)):
            message = conversation_services.post_message(
                conversation=conversation, sender_type=sender, content="x"
            )
            Message.objects.filter(pk=message.pk).update(
                created_at=base + timedelta(seconds=offset)
            )
        handoff = conversation_services.take_over(conversation=conversation, user=self.agent)
        ConversationHandoff.objects.filter(pk=handoff.pk).update(
            created_at=base + timedelta(seconds=600)
        )
        reply = conversation_services.agent_reply(
            conversation=conversation, user=self.agent, text="Hello"
        )
        Message.objects.filter(pk=reply.pk).update(created_at=base + timedelta(seconds=620))
        start, end = selectors.period(30)
        times = selectors.first_response_seconds(self.agent, start=start, end=end)
        self.assertEqual(times, [120.0])

    def test_the_analytics_api_returns_every_employee_and_website_field(self):
        self.take_over_and_reply(wait=30, reply_after=30)
        self.make_lead(status=LeadStatus.CONVERTED)
        self.settle_a_booking()
        self.client.force_login(self.manager)
        data = self.client.get(reverse("api_dashboard:analytics")).json()["data"]
        agent = next(row for row in data["employees"] if row["username"] == "agent")
        for field in (
            "conversations", "handoffs", "responded_handoffs", "avg_first_response_seconds",
            "conversion_rate", "revenue",
        ):
            self.assertIn(field, agent)
        self.assertEqual(agent["conversations"], 1)
        self.assertEqual(agent["handoffs"], 1)
        self.assertEqual(agent["avg_first_response_seconds"], 60)
        site = data["websites"][0]
        self.assertEqual(site["conversations"], 1)
        self.assertEqual(site["leads"], 2)
        self.assertEqual(site["bookings"], 1)
        self.assertEqual(site["paid_bookings"], 1)
        self.assertEqual(Decimal(str(site["revenue"])), Decimal("21000.00"))
        self.assertIn("avg_first_response_seconds", data["definitions"])

    def test_the_analytics_page_renders_the_new_columns(self):
        self.take_over_and_reply(wait=30, reply_after=95)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("dashboard:analytics"))
        self.assertContains(page, "Handoffs taken")
        self.assertContains(page, "2m 5s")


# --------------------------------------------------------------------------
# Batch 6: the overview per role
# --------------------------------------------------------------------------


class OverviewScopeTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.make_lead()  # the agent's
        self.make_lead(assigned_to=self.manager)

    def test_managers_see_business_wide_figures(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.context["scope"], "business")
        self.assertEqual(response.context["kpis"]["leads_total"], 2)
        self.assertNotContains(response, "My figures")

    def test_employees_see_their_own_figures_labelled_as_such(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.context["scope"], "mine")
        self.assertEqual(response.context["kpis"]["leads_total"], 1)
        self.assertEqual(sum(response.context["pipeline"].values()), 1)
        self.assertContains(response, "My figures")
        self.assertContains(response, "My revenue")

    def test_employee_revenue_counts_only_their_bookings(self):
        self.settle_a_booking()  # lead assigned to the agent
        other = User.objects.create_user("other", password="pw", role="employee")
        self.assertEqual(selectors.overview_kpis(user=other)["revenue"], 0)
        self.assertEqual(selectors.overview_kpis(user=self.agent)["revenue"], Decimal("21000"))

    def test_the_inventory_role_gets_an_inventory_overview_without_crm_or_revenue(self):
        Hotel.objects.create(name="Off Inn", base_price=Decimal("1"), is_active=False)
        self.client.force_login(self.stock)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertTemplateUsed(response, "dashboard/overview_inventory.html")
        inventory = response.context["inventory"]
        self.assertEqual(inventory["counts"]["hotel"], {"active": 1, "inactive": 1, "archived": 0})
        self.assertEqual(inventory["inactive_total"], 1)
        self.assertEqual(inventory["recently_updated"][0]["name"], "Off Inn")
        self.assertNotIn("kpis", response.context)
        for text in ("Revenue", "Leads", "Recent bookings"):
            self.assertNotContains(response, text)

    def test_the_api_scopes_figures_and_refuses_more_than_the_role_allows(self):
        url = reverse("api_dashboard:overview")
        self.client.force_login(self.agent)
        body = self.client.get(url).json()["data"]
        self.assertEqual((body["scope"], body["leads_total"]), ("mine", 1))
        self.assertEqual(self.client.get(url, {"scope": "business"}).status_code, 403)
        self.assertEqual(self.client.get(url, {"scope": "inventory"}).status_code, 200)

        self.client.force_login(self.stock)
        body = self.client.get(url).json()["data"]
        self.assertEqual(body["scope"], "inventory")
        self.assertNotIn("revenue", body)
        self.assertEqual(body["counts"]["package"]["active"], 1)
        for scope in ("mine", "business", "nonsense"):
            response = self.client.get(url, {"scope": scope})
            self.assertEqual(response.status_code, 403, scope)
            self.assertFalse(response.json()["success"])

        self.client.force_login(self.manager)
        body = self.client.get(url, {"scope": "business"}).json()["data"]
        self.assertEqual((body["scope"], body["leads_total"]), ("business", 2))

    def test_the_sidebar_hides_websites_from_employees_and_inventory(self):
        for user in (self.agent, self.stock):
            self.client.force_login(user)
            page = self.client.get(reverse("dashboard:overview"))
            self.assertNotContains(page, f'href="{reverse("websites:list")}"')
        self.client.force_login(self.manager)
        page = self.client.get(reverse("dashboard:overview"))
        self.assertContains(page, f'href="{reverse("websites:list")}"')
