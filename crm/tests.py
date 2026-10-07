from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bookings.models import Notification
from websites.models import Website

from . import services
from .models import Customer, FollowUpTask, Lead, LeadActivity, LeadStatus
from .selectors import follow_up_buckets, list_leads, pipeline_columns


class LeadFixture(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.agent = User.objects.create_user("agent", password="pw", role="employee")
        self.other = User.objects.create_user("other", password="pw", role="employee")
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.customer = Customer.objects.create(
            first_name="Ana", last_name="S", email="ana@x.com", phone="9810000000"
        )

    def make_lead(self, **kwargs):
        defaults = {
            "customer": self.customer,
            "website": self.website,
            "title": "Goa trip",
            "destination": "Goa",
        }
        defaults.update(kwargs)
        lead = Lead(**defaults)
        return services.create_lead(lead=lead, actor=self.manager)


class ScoringTests(LeadFixture):
    def test_a_bare_lead_scores_low(self):
        lead = self.make_lead(customer=None, destination="")
        self.assertLess(lead.score, 20)

    def test_contact_dates_and_budget_raise_the_score(self):
        lead = self.make_lead(
            travel_start=timezone.localdate() + timedelta(days=10),
            budget_max=Decimal("50000"),
            travelers_count=4,
        )
        self.assertGreaterEqual(lead.score, 70)

    def test_the_score_never_exceeds_one_hundred(self):
        lead = self.make_lead(
            travel_start=timezone.localdate(),
            budget_max=Decimal("1"),
            travelers_count=8,
            status=LeadStatus.PAYMENT_PENDING,
        )
        self.assertLessEqual(lead.score, 100)


class StatusTests(LeadFixture):
    def test_status_change_records_activity_and_stamps_conversion(self):
        lead = self.make_lead()
        services.change_status(lead=lead, status=LeadStatus.CONVERTED, actor=self.agent)

        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.CONVERTED)
        self.assertIsNotNone(lead.converted_at)
        self.assertTrue(
            LeadActivity.objects.filter(lead=lead, activity_type="status_change").exists()
        )

    def test_moving_to_the_same_status_is_a_no_op(self):
        lead = self.make_lead()
        before = LeadActivity.objects.count()
        services.change_status(lead=lead, status=lead.status, actor=self.agent)
        self.assertEqual(LeadActivity.objects.count(), before)

    def test_lost_reason_is_kept_only_while_the_lead_is_lost(self):
        lead = self.make_lead()
        services.change_status(
            lead=lead, status=LeadStatus.LOST, lost_reason="Too expensive", actor=self.agent
        )
        lead.refresh_from_db()
        self.assertEqual(lead.lost_reason, "Too expensive")

        services.change_status(lead=lead, status=LeadStatus.QUALIFIED, actor=self.agent)
        lead.refresh_from_db()
        self.assertEqual(lead.lost_reason, "")


class AssignmentTests(LeadFixture):
    def test_assigning_notifies_the_new_owner(self):
        lead = self.make_lead()
        Notification.objects.all().delete()
        services.assign_lead(lead=lead, user=self.agent, actor=self.manager)

        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.agent)
        self.assertTrue(Notification.objects.filter(recipient=self.agent).exists())

    def test_assigning_to_yourself_does_not_notify_you(self):
        lead = self.make_lead()
        Notification.objects.all().delete()
        services.assign_lead(lead=lead, user=self.agent, actor=self.agent)
        self.assertFalse(Notification.objects.filter(recipient=self.agent).exists())


class VisibilityTests(LeadFixture):
    def test_employees_only_see_their_own_and_unassigned_leads(self):
        self.make_lead(title="Mine", assigned_to=self.agent)
        self.make_lead(title="Theirs", assigned_to=self.other)
        self.make_lead(title="Unowned")

        titles = set(list_leads(user=self.agent).values_list("title", flat=True))
        self.assertEqual(titles, {"Mine", "Unowned"})

    def test_managers_see_everything(self):
        self.make_lead(title="Mine", assigned_to=self.agent)
        self.make_lead(title="Theirs", assigned_to=self.other)
        self.assertEqual(list_leads(user=self.manager).count(), 2)


class PipelineTests(LeadFixture):
    def test_columns_cover_every_stage_in_order(self):
        self.make_lead(status=LeadStatus.NEW)
        self.make_lead(status=LeadStatus.CONVERTED)
        columns = pipeline_columns(user=self.manager)

        self.assertEqual(columns[0]["status"], LeadStatus.NEW)
        self.assertEqual(columns[0]["count"], 1)
        self.assertEqual(sum(column["count"] for column in columns), 2)


class FollowUpTests(LeadFixture):
    def test_buckets_split_overdue_today_and_upcoming(self):
        lead = self.make_lead()
        # Pinned to local midday so "now + 1 minute" never crosses midnight.
        now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        patcher = mock.patch("django.utils.timezone.now", return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)
        for offset in (-1, 0, 5):
            services.create_follow_up(
                task=FollowUpTask(
                    lead=lead,
                    assigned_to=self.agent,
                    title=f"Call {offset}",
                    due_at=now + timedelta(days=offset, minutes=1),
                ),
                actor=self.manager,
            )
        buckets = follow_up_buckets(user=self.manager)
        self.assertEqual(buckets["overdue"].count(), 1)
        self.assertEqual(buckets["today"].count(), 1)
        self.assertEqual(buckets["upcoming"].count(), 1)

    def test_completing_a_task_stamps_it_and_logs_activity(self):
        lead = self.make_lead()
        task = services.create_follow_up(
            task=FollowUpTask(
                lead=lead, title="Call", due_at=timezone.now() + timedelta(days=1)
            ),
            actor=self.manager,
        )
        services.complete_follow_up(task=task, actor=self.agent)
        task.refresh_from_db()
        self.assertTrue(task.is_completed)
        self.assertIsNotNone(task.completed_at)


class CustomerDeduplicationTests(LeadFixture):
    def test_an_existing_email_is_reused_rather_than_duplicated(self):
        customer, created = services.get_or_create_customer(
            first_name="Ana", email="ana@x.com"
        )
        self.assertFalse(created)
        self.assertEqual(customer.pk, self.customer.pk)

    def test_a_matching_phone_also_reuses_the_customer(self):
        customer, created = services.get_or_create_customer(
            first_name="Someone", phone="9810000000"
        )
        self.assertFalse(created)
        self.assertEqual(customer.pk, self.customer.pk)

    def test_a_new_contact_creates_a_new_customer(self):
        customer, created = services.get_or_create_customer(
            first_name="Bo", email="bo@x.com"
        )
        self.assertTrue(created)
        self.assertEqual(Customer.objects.count(), 2)

    def test_a_missing_field_is_filled_in_from_the_new_details(self):
        services.get_or_create_customer(
            first_name="Ana", email="ana@x.com", phone="9899999999"
        )
        self.customer.refresh_from_db()
        # The stored phone already existed, so it is left alone.
        self.assertEqual(self.customer.phone, "9810000000")


class CRMViewTests(LeadFixture):
    def test_lead_detail_shows_notes_and_activity(self):
        lead = self.make_lead()
        services.add_note(lead=lead, body="Called the customer", author=self.agent)
        self.client.force_login(self.agent)
        response = self.client.get(reverse("crm:lead_detail", args=[lead.pk]))
        self.assertContains(response, "Called the customer")

    def test_status_can_be_changed_from_the_detail_page(self):
        lead = self.make_lead()
        self.client.force_login(self.agent)
        self.client.post(
            reverse("crm:lead_status", args=[lead.pk]),
            {"status": LeadStatus.QUALIFIED, "lost_reason": ""},
        )
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)

    def test_the_pipeline_board_renders(self):
        self.make_lead()
        self.client.force_login(self.manager)
        response = self.client.get(reverse("crm:pipeline"))
        self.assertContains(response, "Goa trip")

    def test_follow_up_next_only_accepts_a_local_path(self):
        lead = self.make_lead()
        self.client.force_login(self.agent)
        task = services.create_follow_up(
            task=FollowUpTask(
                lead=lead, title="Call", due_at=timezone.now() + timedelta(days=1)
            ),
            actor=self.manager,
        )
        response = self.client.post(
            reverse("crm:follow_up_complete", args=[task.pk]),
            {"next": "https://evil.example/steal"},
        )
        self.assertRedirects(response, reverse("crm:follow_ups"))


class LeadAccessTests(LeadFixture):
    """Employees only reach leads assigned to them or unassigned; others are a 404."""

    def setUp(self):
        super().setUp()
        self.theirs = self.make_lead(title="Other's lead", assigned_to=self.other)
        self.mine = self.make_lead(title="My lead", assigned_to=self.agent)
        self.open_lead = self.make_lead(title="Unassigned lead")
        self.client.force_login(self.agent)

    def test_every_lead_page_and_action_404s_on_another_employees_lead(self):
        pk = self.theirs.pk
        self.assertEqual(self.client.get(reverse("crm:lead_detail", args=[pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("crm:lead_edit", args=[pk])).status_code, 404)
        for name, data in (
            ("crm:lead_status", {"status": LeadStatus.LOST}),
            ("crm:lead_assign", {"assigned_to": self.agent.pk}),
            ("crm:lead_note", {"body": "hi", "is_internal": "on"}),
        ):
            response = self.client.post(reverse(name, args=[pk]), data)
            self.assertEqual(response.status_code, 404, name)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.assigned_to, self.other)
        self.assertNotEqual(self.theirs.status, LeadStatus.LOST)

    def test_own_and_unassigned_leads_open(self):
        for lead in (self.mine, self.open_lead):
            response = self.client.get(reverse("crm:lead_detail", args=[lead.pk]))
            self.assertEqual(response.status_code, 200)

    def test_managers_open_everything(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("crm:lead_detail", args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 200)

    def test_handing_a_lead_away_redirects_to_the_list(self):
        response = self.client.post(
            reverse("crm:lead_assign", args=[self.mine.pk]), {"assigned_to": self.other.pk}
        )
        self.assertRedirects(response, reverse("crm:leads"))

    def test_the_lead_detail_api_is_scoped(self):
        response = self.client.get(reverse("api_crm:lead_detail", args=[self.theirs.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_the_inventory_role_cannot_reach_crm(self):
        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(stock)
        self.assertRedirects(self.client.get(reverse("crm:leads")), reverse("dashboard:overview"))
        self.assertEqual(self.client.get(reverse("api_crm:leads")).status_code, 403)


class CustomerAccessTests(LeadFixture):
    def test_a_customer_only_known_through_another_employees_lead_is_hidden(self):
        self.make_lead(assigned_to=self.other)
        self.client.force_login(self.agent)
        for name in ("crm:customer_detail", "crm:customer_edit"):
            response = self.client.get(reverse(name, args=[self.customer.pk]))
            self.assertEqual(response.status_code, 404, name)
        response = self.client.get(reverse("api_crm:customer_detail", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(reverse("crm:customers")), "ana@x.com")

    def test_a_customer_with_a_visible_lead_or_no_history_is_shown(self):
        self.make_lead(assigned_to=self.agent)
        fresh = Customer.objects.create(first_name="New", email="new@x.com")
        self.client.force_login(self.agent)
        for customer in (self.customer, fresh):
            response = self.client.get(reverse("crm:customer_detail", args=[customer.pk]))
            self.assertEqual(response.status_code, 200)


class FollowUpAccessTests(LeadFixture):
    def make_task(self, lead, assigned_to, **kwargs):
        return services.create_follow_up(
            task=FollowUpTask(
                lead=lead,
                assigned_to=assigned_to,
                title=kwargs.pop("title", "Call"),
                due_at=kwargs.pop("due_at", timezone.now() + timedelta(days=2)),
                **kwargs,
            ),
            actor=self.manager,
        )

    def test_an_employee_cannot_complete_another_employees_follow_up(self):
        task = self.make_task(self.make_lead(assigned_to=self.other), self.other)
        self.client.force_login(self.agent)
        response = self.client.post(reverse("crm:follow_up_complete", args=[task.pk]))
        self.assertEqual(response.status_code, 404)
        task.refresh_from_db()
        self.assertFalse(task.is_completed)

    def test_a_task_assigned_to_me_on_someone_elses_lead_is_mine(self):
        task = self.make_task(self.make_lead(assigned_to=self.other), self.agent)
        self.client.force_login(self.agent)
        self.client.post(reverse("crm:follow_up_complete", args=[task.pk]))
        task.refresh_from_db()
        self.assertTrue(task.is_completed)

    def test_the_follow_up_api_without_a_bucket_is_scoped(self):
        self.make_task(self.make_lead(assigned_to=self.other), self.other, title="Theirs")
        self.make_task(self.make_lead(assigned_to=self.agent), self.agent, title="Mine")
        self.client.force_login(self.agent)
        body = self.client.get(reverse("api_crm:follow_ups")).json()
        self.assertEqual([row["title"] for row in body["data"]["results"]], ["Mine"])

    def test_the_completed_bucket_respects_the_user(self):
        theirs = self.make_task(self.make_lead(assigned_to=self.other), self.other)
        mine = self.make_task(self.make_lead(assigned_to=self.agent), self.agent)
        for task in (theirs, mine):
            services.complete_follow_up(task=task, actor=self.manager)
        completed = follow_up_buckets(user=self.agent, include_completed=True)["completed"]
        self.assertEqual([task.pk for task in completed], [mine.pk])

    def test_scheduling_on_another_employees_lead_is_refused(self):
        self.client.force_login(self.agent)
        for title, owner in (("Sneaky", self.other), ("Allowed", self.agent)):
            self.client.post(
                reverse("crm:follow_up_create"),
                {
                    "lead": self.make_lead(assigned_to=owner).pk,
                    "assigned_to": self.agent.pk,
                    "title": title,
                    "due_at": "2030-01-01T10:00",
                },
            )
        self.assertFalse(FollowUpTask.objects.filter(title="Sneaky").exists())
        self.assertTrue(FollowUpTask.objects.filter(title="Allowed").exists())

    @override_settings(TIME_ZONE="Asia/Kolkata")
    def test_today_means_the_local_calendar_day(self):
        ist = ZoneInfo("Asia/Kolkata")
        # 01:30 IST on the 11th is still the 10th in UTC.
        now = datetime(2026, 1, 11, 1, 30, tzinfo=ist)
        task = self.make_task(
            self.make_lead(), self.agent, due_at=datetime(2026, 1, 11, 10, 0, tzinfo=ist)
        )
        with mock.patch("django.utils.timezone.now", return_value=now):
            buckets = follow_up_buckets(user=self.manager)
            self.assertEqual(list(buckets["today"]), [task])
            self.assertEqual(buckets["upcoming"].count(), 0)


class LeadAPIFilterTests(LeadFixture):
    def setUp(self):
        super().setUp()
        self.other_site = Website.objects.create(
            name="Other", domain="other.com", source_identifier="other"
        )
        self.a = self.make_lead(title="Alpha", assigned_to=self.agent)
        self.b = self.make_lead(title="Beta", website=self.other_site)
        Lead.objects.filter(pk=self.b.pk).update(
            created_at=timezone.now() - timedelta(days=40)
        )
        self.client.force_login(self.manager)

    def titles(self, **params):
        body = self.client.get(reverse("api_crm:leads"), params).json()
        return sorted(row["title"] for row in body["data"]["results"])

    def test_filters_by_assignee_website_and_created_range(self):
        self.assertEqual(self.titles(assigned_to=self.agent.pk), ["Alpha"])
        self.assertEqual(self.titles(website=self.other_site.pk), ["Beta"])
        recent = (timezone.localdate() - timedelta(days=7)).isoformat()
        self.assertEqual(self.titles(created_from=recent), ["Alpha"])
        self.assertEqual(self.titles(created_to=recent), ["Beta"])

    def test_a_bad_filter_value_is_a_400_envelope(self):
        response = self.client.get(reverse("api_crm:leads"), {"created_from": "not-a-date"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])



# --------------------------------------------------------------------------
# Batch 3: follow-up reminders
# --------------------------------------------------------------------------

from django.core import mail  # noqa: E402


class FollowUpReminderTests(LeadFixture):
    def setUp(self):
        super().setUp()
        self.agent.email = "agent@x.com"
        self.agent.save()
        self.manager.email = "mgr@x.com"
        self.manager.save()
        self.lead = self.make_lead()
        self.now = timezone.now()
        Notification.objects.all().delete()

    def task(self, **kwargs):
        defaults = {
            "lead": self.lead,
            "title": "Call back",
            "due_at": self.now + timedelta(hours=2),
            "reminder_at": self.now - timedelta(minutes=1),
        }
        defaults.update(kwargs)
        return FollowUpTask.objects.create(**defaults)

    def fire(self, now=None):
        with self.captureOnCommitCallbacks(execute=True):
            return services.send_due_reminders(now=now or self.now)

    def reminders_for(self, user):
        return Notification.objects.filter(
            recipient=user, notification_type="follow_up", metadata__event="reminder"
        )

    def test_reminder_goes_to_the_assignee_in_app_and_by_email_once(self):
        task = self.task(assigned_to=self.agent)

        self.assertEqual(self.fire(), 1)
        self.assertEqual(self.fire(), 0)
        self.assertEqual(self.fire(self.now + timedelta(days=1)), 0)

        task.refresh_from_db()
        self.assertEqual(task.reminded_at, self.now)
        note = self.reminders_for(self.agent).get()
        self.assertEqual(note.title, "Reminder: Call back")
        self.assertEqual(note.link, reverse("crm:lead_detail", args=[self.lead.pk]))
        self.assertFalse(self.reminders_for(self.manager).exists())
        self.assertEqual([m.to for m in mail.outbox], [["agent@x.com"]])
        self.assertIn("Reminder: Call back", mail.outbox[0].subject)
        self.assertTrue(
            LeadActivity.objects.filter(lead=self.lead, activity_type="follow_up_reminder")
        )

    def test_not_due_yet_and_completed_tasks_are_left_alone(self):
        self.task(assigned_to=self.agent, reminder_at=self.now + timedelta(minutes=5))
        self.task(assigned_to=self.agent, is_completed=True)
        self.assertEqual(self.fire(), 0)
        self.assertEqual(self.fire(self.now + timedelta(minutes=6)), 1)

    def test_without_a_reminder_time_it_fires_at_the_due_time_as_overdue(self):
        self.task(assigned_to=self.agent, reminder_at=None, due_at=self.now + timedelta(hours=1))
        self.assertEqual(self.fire(), 0)
        self.assertEqual(self.fire(self.now + timedelta(hours=1)), 1)
        self.assertEqual(self.reminders_for(self.agent).get().title, "Overdue: Call back")

    def test_unassigned_task_goes_to_the_lead_owner(self):
        self.lead.assigned_to = self.other
        self.lead.save()
        self.task()
        self.fire()
        self.assertTrue(self.reminders_for(self.other).exists())
        self.assertFalse(self.reminders_for(self.manager).exists())

    def test_with_nobody_responsible_managers_are_reminded(self):
        self.task()
        self.fire()
        self.assertTrue(self.reminders_for(self.manager).exists())
        self.assertEqual([m.to for m in mail.outbox], [["mgr@x.com"]])

    def test_an_inactive_assignee_falls_through_to_the_lead_owner(self):
        self.agent.is_active = False
        self.agent.save()
        self.lead.assigned_to = self.other
        self.lead.save()
        self.task(assigned_to=self.agent)
        self.fire()
        self.assertFalse(self.reminders_for(self.agent).exists())
        self.assertTrue(self.reminders_for(self.other).exists())

    def test_opted_out_assignee_still_gets_the_in_app_reminder(self):
        self.agent.email_notifications = False
        self.agent.save()
        self.task(assigned_to=self.agent)
        self.fire()
        self.assertTrue(self.reminders_for(self.agent).exists())
        self.assertEqual(mail.outbox, [])


# --------------------------------------------------------------------------
# Batch 4: pipeline board, website-form intake, archive, follow-up edit, teams
# --------------------------------------------------------------------------

from django.core.cache import cache  # noqa: E402
from django.test import Client  # noqa: E402

from accounts.models import AuditLog, Team  # noqa: E402
from websites.services import issue_api_key  # noqa: E402

from .models import LeadSource  # noqa: E402


class PipelineBoardTests(LeadFixture):
    def move(self, lead, status, **extra):
        return self.client.post(
            reverse("crm:lead_move", args=[lead.pk]), {"status": status, **extra}
        )

    def test_moving_a_card_uses_the_status_service(self):
        lead = self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.agent)
        response = self.move(lead, LeadStatus.QUALIFIED)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "qualified")
        self.assertEqual(body["from"], "new")
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)
        self.assertTrue(
            LeadActivity.objects.filter(lead=lead, activity_type="status_change").exists()
        )

    def test_lost_keeps_the_reason(self):
        lead = self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.agent)
        self.move(lead, LeadStatus.LOST, lost_reason="Went elsewhere")
        lead.refresh_from_db()
        self.assertEqual(lead.lost_reason, "Went elsewhere")

    def test_an_invalid_status_is_rejected(self):
        lead = self.make_lead(assigned_to=self.agent)
        self.client.force_login(self.agent)
        response = self.move(lead, "teleported")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)

    def test_someone_elses_lead_cannot_be_moved(self):
        lead = self.make_lead(assigned_to=self.other)
        self.client.force_login(self.agent)
        self.assertEqual(self.move(lead, LeadStatus.QUALIFIED).status_code, 404)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)

    def test_csrf_is_enforced(self):
        lead = self.make_lead(assigned_to=self.agent)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.agent)
        url = reverse("crm:lead_move", args=[lead.pk])
        self.assertEqual(client.post(url, {"status": "qualified"}).status_code, 403)
        page = client.get(reverse("crm:pipeline"))
        token = page.cookies["csrftoken"].value
        response = client.post(url, {"status": "qualified"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_inventory_users_are_turned_away(self):
        lead = self.make_lead()
        stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.client.force_login(stock)
        self.assertEqual(self.move(lead, LeadStatus.QUALIFIED).status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)

    def test_the_board_links_overflow_to_the_filtered_list(self):
        for index in range(27):
            self.make_lead(title=f"Lead {index}")
        self.client.force_login(self.manager)
        page = self.client.get(reverse("crm:pipeline"), {"website": self.website.pk})
        self.assertContains(page, "+2 more")
        self.assertContains(
            page, f'href="/crm/leads/?status=new&amp;website={self.website.pk}"'
        )
        self.assertContains(page, 'draggable="true"', count=25)

    def test_the_board_filters_like_the_leads_list(self):
        team = Team.objects.create(name="North")
        self.make_lead(title="Phone lead", source=LeadSource.PHONE)
        self.make_lead(title="Team lead", assigned_team=team)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("crm:pipeline"), {"source": "phone"})
        self.assertContains(page, "Phone lead")
        self.assertNotContains(page, "Team lead")
        page = self.client.get(reverse("crm:pipeline"), {"team": team.pk})
        self.assertContains(page, "Team lead")
        self.assertNotContains(page, "Phone lead")


class IntakeFixture(LeadFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.key = issue_api_key(website=self.website)
        self.url = reverse("api_crm:intake")

    def submit(self, as_json=False, headers=None, **fields):
        payload = {
            "key": self.key.public_key,
            "name": "Ravi Kumar",
            "email": "ravi@example.com",
            "phone": "+91 98100 22222",
            "destination": "Manali",
            "travel_start": "2027-05-01",
            "travel_end": "2027-05-06",
            "travellers": "3",
            "budget": "60000",
            "message": "Honeymoon, quiet hotel please.",
        }
        payload.update(fields)
        payload = {key: value for key, value in payload.items() if value is not None}
        if as_json:
            return self.client.post(
                self.url, payload, content_type="application/json", **(headers or {})
            )
        return self.client.post(self.url, payload, **(headers or {}))


class IntakeTests(IntakeFixture):
    def test_a_form_submission_creates_a_website_form_lead(self):
        response = self.submit(utm_source="facebook", utm_campaign="summer")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        lead = Lead.objects.get()
        self.assertEqual(body["lead_reference"], f"ENQ-{lead.pk:06d}")
        # Nothing internal leaks back to the website.
        self.assertEqual(set(body), {"success", "message", "lead_reference"})
        self.assertEqual(lead.source, LeadSource.WEBSITE_FORM)
        self.assertEqual(lead.website, self.website)
        self.assertEqual(lead.destination, "Manali")
        self.assertEqual(lead.travelers_count, 3)
        self.assertEqual(lead.budget_max, Decimal("60000"))
        self.assertEqual(lead.utm_source, "facebook")
        self.assertEqual(lead.utm_campaign, "summer")
        self.assertEqual(lead.customer.phone, "919810022222")
        self.assertIn("Honeymoon", lead.notes.get().body)
        # Destination, dates, party size and contact: qualified straight away.
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, notification_type="lead"
            ).exists()
        )

    def test_json_is_accepted_and_the_reference_finds_the_lead(self):
        response = self.submit(as_json=True, travellers=None, travelers=2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Lead.objects.get().travelers_count, 2)
        self.assertEqual(
            list_leads(q=response.json()["lead_reference"]).get(), Lead.objects.get()
        )

    def test_an_existing_customer_is_reused(self):
        response = self.submit(email="ANA@x.com", phone="")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Lead.objects.get().customer, self.customer)
        self.assertEqual(Customer.objects.count(), 1)

    def test_a_bad_or_revoked_key_is_refused(self):
        self.assertEqual(self.submit(key="pk_nope").status_code, 403)
        self.assertEqual(self.submit(key=None).status_code, 403)
        self.key.is_active = False
        self.key.save()
        response = self.submit()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])
        self.assertFalse(Lead.objects.exists())

    def test_contact_details_are_required(self):
        response = self.submit(email="", phone="")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Lead.objects.exists())
        self.assertEqual(self.submit(email="not-an-email", phone="").status_code, 400)

    def test_cors_answers_only_the_keys_own_website(self):
        response = self.submit(headers={"HTTP_ORIGIN": "https://www.main.com"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://www.main.com")
        other = self.submit(headers={"HTTP_ORIGIN": "https://evil.com"}, email="e@example.com")
        self.assertNotIn("Access-Control-Allow-Origin", other)

    def test_the_preflight_is_answered_for_registered_sites(self):
        response = self.client.options(
            self.url,
            HTTP_ORIGIN="https://main.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://main.com")

    def test_the_form_works_with_the_chat_widget_switched_off(self):
        self.website.widget_enabled = False
        self.website.save()
        self.assertEqual(self.submit().status_code, 201)

    def test_the_staff_crm_api_still_has_no_cors(self):
        response = self.client.get(reverse("api_crm:leads"), HTTP_ORIGIN="https://main.com")
        self.assertNotIn("Access-Control-Allow-Origin", response)

    @override_settings(PUBLIC_API_THROTTLE_RATES={"public_intake": "2/hour"})
    def test_submissions_are_rate_limited(self):
        codes = [self.submit(email=f"p{n}@example.com").status_code for n in range(3)]
        self.assertEqual(codes, [201, 201, 429])
        self.assertEqual(Lead.objects.count(), 2)


class CustomerArchiveTests(LeadFixture):
    def test_managers_archive_and_restore(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("crm:customer_archive", args=[self.customer.pk]))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_deleted)
        self.assertIsNotNone(self.customer.deleted_at)
        self.assertTrue(AuditLog.objects.filter(action="customer.archive").exists())

        listing = self.client.get(reverse("crm:customers"))
        self.assertNotContains(listing, "ana@x.com")
        listing = self.client.get(reverse("crm:customers"), {"archived": "on"})
        self.assertContains(listing, "ana@x.com")
        self.assertContains(listing, "Archived")
        # Still reachable by a manager, to restore it.
        page = self.client.get(reverse("crm:customer_detail", args=[self.customer.pk]))
        self.assertContains(page, "Restore customer")

        self.client.post(reverse("crm:customer_restore", args=[self.customer.pk]))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_deleted)
        self.assertContains(self.client.get(reverse("crm:customers")), "ana@x.com")

    def test_archived_customers_leave_the_pickers(self):
        from crm.forms import LeadForm

        services.archive_customer(customer=self.customer)
        self.assertNotIn(self.customer, LeadForm(user=self.manager).fields["customer"].queryset)
        self.assertNotIn(self.customer, list_customers_for(self.agent))

    def test_employees_cannot_archive(self):
        self.client.force_login(self.agent)
        response = self.client.post(reverse("crm:customer_archive", args=[self.customer.pk]))
        self.assertRedirects(response, reverse("dashboard:overview"))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_deleted)
        # Nor can they see archived ones by asking.
        services.archive_customer(customer=self.customer)
        listing = self.client.get(reverse("crm:customers"), {"archived": "on"})
        self.assertNotContains(listing, "ana@x.com")


def list_customers_for(user):
    from .selectors import list_customers

    return list(list_customers(user=user))


class FollowUpEditTests(LeadFixture):
    def setUp(self):
        super().setUp()
        self.lead = self.make_lead(assigned_to=self.agent)
        self.due = timezone.now() + timedelta(days=1)
        self.task = FollowUpTask.objects.create(
            lead=self.lead,
            assigned_to=self.agent,
            title="Call back",
            due_at=self.due,
            reminder_at=self.due - timedelta(hours=1),
            reminded_at=timezone.now(),
        )
        self.client.force_login(self.agent)
        self.url = reverse("crm:follow_up_edit", args=[self.task.pk])

    def edit(self, **fields):
        data = {
            "title": "Call back",
            "assigned_to": self.agent.pk,
            "due_at": timezone.localtime(self.due).strftime("%Y-%m-%dT%H:%M"),
            "reminder_at": timezone.localtime(self.due - timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            "notes": "",
        }
        data.update(fields)
        return self.client.post(self.url, data)

    def test_moving_the_reminder_later_rearms_it(self):
        later = timezone.localtime(self.due + timedelta(days=1))
        response = self.edit(
            title="Call back (rescheduled)",
            due_at=later.strftime("%Y-%m-%dT%H:%M"),
            reminder_at=(later - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            notes="Customer asked for Friday",
        )
        self.assertRedirects(response, reverse("crm:lead_detail", args=[self.lead.pk]))
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Call back (rescheduled)")
        self.assertEqual(self.task.notes, "Customer asked for Friday")
        self.assertIsNone(self.task.reminded_at)
        self.assertTrue(
            LeadActivity.objects.filter(lead=self.lead, activity_type="follow_up_updated").exists()
        )

    def test_clearing_the_reminder_falls_back_to_the_later_due_time(self):
        self.edit(reminder_at="")
        self.task.refresh_from_db()
        self.assertIsNone(self.task.reminder_at)
        self.assertIsNone(self.task.reminded_at)

    def test_moving_it_earlier_keeps_the_sent_reminder(self):
        self.edit(
            reminder_at=timezone.localtime(self.due - timedelta(hours=5)).strftime(
                "%Y-%m-%dT%H:%M"
            )
        )
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.reminded_at)

    def test_a_reminder_after_the_due_time_is_rejected(self):
        response = self.edit(
            reminder_at=timezone.localtime(self.due + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M"
            )
        )
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.reminded_at)

    def test_reassigning_notifies_the_new_assignee(self):
        Notification.objects.all().delete()
        self.edit(assigned_to=self.manager.pk)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.manager, notification_type="follow_up"
            ).exists()
        )

    def test_someone_elses_task_cannot_be_edited(self):
        theirs = FollowUpTask.objects.create(
            lead=self.make_lead(assigned_to=self.other),
            assigned_to=self.other,
            title="Theirs",
            due_at=self.due,
        )
        response = self.client.get(reverse("crm:follow_up_edit", args=[theirs.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_next_url_is_followed_only_inside_the_site(self):
        response = self.edit(next="/crm/follow-ups/")
        self.assertRedirects(response, reverse("crm:follow_ups"))
        response = self.edit(next="https://evil.com/")
        self.assertRedirects(response, reverse("crm:lead_detail", args=[self.lead.pk]))


class TeamAssignmentTests(LeadFixture):
    def setUp(self):
        super().setUp()
        self.team = Team.objects.create(name="North")
        self.team.members.add(self.agent, self.other)
        self.outsider = User.objects.create_user("outsider", password="pw", role="employee")

    def test_team_members_see_the_teams_leads_and_their_follow_ups(self):
        lead = self.make_lead(assigned_to=self.manager)
        services.assign_lead(lead=lead, user=self.manager, team=self.team, actor=self.manager)
        task = FollowUpTask.objects.create(
            lead=lead, assigned_to=self.manager, title="Quote", due_at=timezone.now()
        )
        for member in (self.agent, self.other):
            self.assertIn(lead, list_leads(user=member))
        self.assertNotIn(lead, list_leads(user=self.outsider))
        from .selectors import visible_follow_ups

        self.assertIn(task, visible_follow_ups(self.agent))
        self.assertNotIn(task, visible_follow_ups(self.outsider))
        self.client.force_login(self.agent)
        self.assertEqual(
            self.client.get(reverse("crm:lead_detail", args=[lead.pk])).status_code, 200
        )

    def test_assigning_to_a_team_notifies_its_members(self):
        lead = self.make_lead()
        Notification.objects.all().delete()
        services.assign_lead(lead=lead, team=self.team, actor=self.agent)
        recipients = set(Notification.objects.values_list("recipient__username", flat=True))
        # The member who did it is not told about their own action.
        self.assertEqual(recipients, {"other"})
        # Re-saving the same team is not news.
        Notification.objects.all().delete()
        services.assign_lead(lead=lead, team=self.team, actor=self.manager)
        self.assertFalse(Notification.objects.exists())

    def test_a_new_lead_for_a_team_notifies_its_members(self):
        Notification.objects.all().delete()
        self.make_lead(assigned_team=self.team)
        recipients = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertIn("agent", recipients)
        self.assertIn("other", recipients)

    def test_the_leads_list_filters_by_team(self):
        self.make_lead(title="Team lead", assigned_team=self.team)
        self.make_lead(title="Loose lead")
        self.client.force_login(self.manager)
        page = self.client.get(reverse("crm:leads"), {"team": self.team.pk})
        self.assertContains(page, "Team lead")
        self.assertNotContains(page, "Loose lead")
        body = self.client.get(reverse("api_crm:leads"), {"team": self.team.pk}).json()
        self.assertEqual([row["title"] for row in body["data"]["results"]], ["Team lead"])

    def test_manual_leads_can_use_any_configured_source(self):
        from crm.forms import LeadForm

        self.assertEqual(
            {value for value, _label in LeadForm(user=self.manager).fields["source"].choices if value},
            {value for value, _label in LeadSource.choices},
        )


class PickerScopeTests(LeadFixture):
    """Lead and follow-up forms only offer records the user can open."""

    def setUp(self):
        super().setUp()
        self.secret = Customer.objects.create(first_name="Secret", email="secret@x.com")
        self.hidden = self.make_lead(customer=self.secret, title="Hidden", assigned_to=self.other)
        self.mine = self.make_lead(title="Mine", assigned_to=self.agent)

    def test_the_lead_form_lists_only_visible_customers(self):
        from crm.forms import LeadForm

        self.assertIn(self.customer, LeadForm(user=self.agent).fields["customer"].queryset)
        self.assertNotIn(self.secret, LeadForm(user=self.agent).fields["customer"].queryset)
        self.assertIn(self.secret, LeadForm(user=self.manager).fields["customer"].queryset)
        self.client.force_login(self.agent)
        self.assertNotContains(self.client.get(reverse("crm:lead_create")), "secret@x.com")

    def test_an_employee_cannot_attach_a_lead_to_a_hidden_customer(self):
        self.client.force_login(self.agent)
        self.client.post(
            reverse("crm:lead_create"),
            {"title": "Sneaky", "customer": self.secret.pk, "status": "new", "source": "manual",
             "travelers_count": 1},
        )
        self.assertFalse(Lead.objects.filter(title="Sneaky").exists())

    def test_the_follow_up_form_lists_only_visible_leads(self):
        from crm.forms import FollowUpTaskForm

        leads = FollowUpTaskForm(user=self.agent).fields["lead"].queryset
        self.assertIn(self.mine, leads)
        self.assertNotIn(self.hidden, leads)
        self.client.force_login(self.agent)
        page = self.client.get(reverse("crm:follow_ups"))
        self.assertNotContains(page, ">Hidden<")
        self.client.post(
            reverse("crm:follow_up_create"),
            {"lead": self.hidden.pk, "title": "Call", "due_at": "2030-01-01T10:00"},
        )
        self.assertFalse(self.hidden.follow_ups.exists())
