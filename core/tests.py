from django.test import TestCase

from accounts.models import AuditLog, User
from core.notifications import notify, notify_managers
from core.selectors import paginate
from core.services import log_audit


class AuditLogTests(TestCase):
    def test_logs_model_instance_type_and_id(self):
        actor = User.objects.create_user("bob", password="x", role="manager")
        log_audit(actor=actor, action="thing.done", entity=actor, metadata={"a": 1})

        entry = AuditLog.objects.get()
        self.assertEqual(entry.action, "thing.done")
        self.assertEqual(entry.entity_type, "User")
        self.assertEqual(entry.entity_id, str(actor.pk))
        self.assertEqual(entry.metadata, {"a": 1})

    def test_accepts_a_plain_string_entity(self):
        log_audit(action="import.run", entity="Nightly", entity_id="42")
        entry = AuditLog.objects.get()
        self.assertEqual(entry.entity_type, "Nightly")
        self.assertIsNone(entry.actor)


class NotificationTests(TestCase):
    def test_notify_managers_reaches_admins_and_managers_only(self):
        User.objects.create_user("adm", password="x", role="admin")
        User.objects.create_user("mgr", password="x", role="manager")
        User.objects.create_user("emp", password="x", role="employee")

        notify_managers(notification_type="lead", title="New lead")

        from bookings.models import Notification

        recipients = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertEqual(recipients, {"adm", "mgr"})

    def test_notify_ignores_a_missing_recipient(self):
        self.assertIsNone(notify(recipient=None, notification_type="lead", title="x"))


class PaginateTests(TestCase):
    def test_bad_page_numbers_fall_back_instead_of_raising(self):
        User.objects.create_user("a", password="x")
        page = paginate(User.objects.all(), "not-a-number")
        self.assertEqual(page.number, 1)

    def test_page_beyond_the_end_returns_the_last_page(self):
        User.objects.create_user("a", password="x")
        page = paginate(User.objects.all(), 99)
        self.assertEqual(page.number, 1)
