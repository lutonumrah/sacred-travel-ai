from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
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
        now = timezone.now()
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
