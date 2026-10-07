"""Website enquiry form → lead → team → follow-up → scheduled reminder."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from crm.models import FollowUpTask, Lead, LeadSource, LeadStatus

from .base import SITE_ORIGIN, JourneyTestCase


def local_input(moment):
    """A datetime as the dashboard's datetime-local inputs send it."""
    return timezone.localtime(moment).strftime("%Y-%m-%dT%H:%M")


class IntakeFollowUpJourneyTests(JourneyTestCase):
    def submit_form(self, **overrides):
        start = self.trip_start()
        data = {
            "key": self.key,
            "name": "Kiran Shah",
            "email": "kiran@example.com",
            "phone": "+91 98100 11111",
            "destination": "Goa",
            "travel_start": start.isoformat(),
            "travel_end": (start + timedelta(days=4)).isoformat(),
            "travellers": "3",
            "budget": "60000",
            "message": "Anniversary trip, sea view please.",
            "utm_source": "google",
            "utm_campaign": "goa-search",
        }
        data.update(overrides)
        # The ready-made snippet posts FormData: a simple CORS request.
        return self.call(
            self.customer_browser,
            "post",
            reverse("api_crm:intake"),
            data,
            json_body=False,
            HTTP_ORIGIN=SITE_ORIGIN,
        )

    def run_jobs(self):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("run_scheduled_jobs", stdout=out)
        return out.getvalue()

    def test_enquiry_to_team_follow_up_and_reminder(self):
        # 1. The site's enquiry form creates a qualified website-form lead.
        response = self.submit_form()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response["Access-Control-Allow-Origin"], SITE_ORIGIN)
        body = response.json()
        reference = body["lead_reference"]
        self.assertRegex(reference, r"^ENQ-\d{6}$")
        self.assertEqual(set(body), {"success", "message", "lead_reference"}, "nothing internal leaks")

        lead = Lead.objects.get()
        self.assertEqual(lead.reference, reference)
        self.assertEqual(lead.source, LeadSource.WEBSITE_FORM)
        self.assertEqual(lead.website, self.website)
        self.assertEqual(lead.status, LeadStatus.QUALIFIED)
        self.assertEqual(lead.travelers_count, 3)
        self.assertEqual(lead.utm_campaign, "goa-search")
        self.assertEqual(lead.customer.phone, "919810011111")
        self.assertIn("Anniversary trip", lead.notes.get().body)
        self.assertTrue(self.notifications_for(self.manager, notification_type="lead"))
        self.assertTrue(self.outbox_to(self.manager.email, "[Lead]"))

        # A second enquiry from the same person reuses the customer.
        self.submit_form(name="Kiran S", phone="")
        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(Lead.objects.values("customer").distinct().count(), 1)

        # 2. The manager finds it by its reference and hands it to the North Desk team.
        found = self.staff_get(self.manager, reverse("crm:leads"), {"q": reference})
        self.assertContains(found, reverse("crm:lead_detail", args=[lead.pk]))
        response = self.staff_post(
            self.manager,
            reverse("crm:lead_assign", args=[lead.pk]),
            {"assigned_to": "", "assigned_team": self.team.pk},
        )
        self.assertEqual(response.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_team, self.team)
        self.assertTrue(self.notifications_for(self.agent, title__contains=lead.title))

        # Team members see it; employees outside the team do not.
        detail_url = reverse("crm:lead_detail", args=[lead.pk])
        self.assertEqual(self.staff_get(self.agent, detail_url).status_code, 200)
        self.assertEqual(self.staff_get(self.agent_two, detail_url).status_code, 404)
        self.assertNotContains(
            self.staff_get(self.agent_two, reverse("crm:leads")), detail_url
        )

        # 3. The team member adds a note and schedules a follow-up with a reminder.
        self.staff_post(
            self.agent, reverse("crm:lead_note", args=[lead.pk]), {"body": "Called, wants sea view."}
        )
        due = timezone.now() + timedelta(days=1)
        reminder = timezone.now() + timedelta(hours=2)
        response = self.staff_post(
            self.agent,
            reverse("crm:follow_up_create"),
            {
                "lead": lead.pk,
                "assigned_to": self.agent.pk,
                "title": "Send Goa quote",
                "due_at": local_input(due),
                "reminder_at": local_input(reminder),
                "notes": "Include sea-view rooms",
                "next": detail_url,
            },
        )
        self.assertRedirects(response, detail_url)
        task = FollowUpTask.objects.get()
        self.assertEqual(task.assigned_to, self.agent)

        # 4. The scheduler leaves it alone until the reminder time...
        self.run_jobs()
        task.refresh_from_db()
        self.assertIsNone(task.reminded_at)
        self.assertFalse(self.notifications_for(self.agent, notification_type="follow_up"))

        # ...the agent moves the reminder into the past (edit page)...
        response = self.staff_post(
            self.agent,
            reverse("crm:follow_up_edit", args=[task.pk]),
            {
                "title": "Send Goa quote",
                "assigned_to": self.agent.pk,
                "due_at": local_input(due),
                "reminder_at": local_input(timezone.now() - timedelta(minutes=5)),
                "notes": "Include sea-view rooms",
            },
        )
        self.assertEqual(response.status_code, 302)

        # ...and the next scheduler run reminds them once, in the app and by email.
        output = self.run_jobs()
        self.assertIn("reminders=1", output)
        reminders = self.notifications_for(self.agent, notification_type="follow_up")
        self.assertEqual(reminders.count(), 1)
        self.assertEqual(reminders.get().title, "Reminder: Send Goa quote")
        self.assertEqual(len(self.outbox_to(self.agent.email, "Send Goa quote")), 1)
        self.assertTrue(lead.activities.filter(activity_type="follow_up_reminder").exists())
        self.assertIn("reminders=0", self.run_jobs())
        self.assertEqual(reminders.count(), 1)

        # The dashboard surfaces it, and completing it closes it.
        self.assertContains(self.staff_get(self.agent, reverse("crm:follow_ups")), "Send Goa quote")
        self.staff_post(
            self.agent, reverse("crm:follow_up_complete", args=[task.pk]), {"next": detail_url}
        )
        task.refresh_from_db()
        self.assertTrue(task.is_completed)

        # Activity timeline records the whole story.
        kinds = set(lead.activities.values_list("activity_type", flat=True))
        self.assertLessEqual({"created", "assignment", "note", "follow_up_reminder"}, kinds)

    def test_invalid_or_foreign_submissions_are_refused(self):
        self.assertEqual(self.submit_form(key="pk_wrong").status_code, 403)
        missing = self.submit_form(email="", phone="")
        self.assertEqual(missing.status_code, 400)
        self.assertFalse(Lead.objects.exists())
