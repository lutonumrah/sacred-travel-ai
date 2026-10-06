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


class PermissionTests(TestCase):
    def _request(self, user):
        from types import SimpleNamespace

        return SimpleNamespace(user=user)

    def test_role_permissions_mirror_the_mixins(self):
        from core.permissions import IsAdmin, IsInventoryEditor, IsManager, IsSalesTeam

        users = {
            role: User.objects.create_user(role, password="x", role=role)
            for role in ("admin", "manager", "employee", "inventory")
        }
        expected = {
            IsAdmin: {"admin"},
            IsManager: {"admin", "manager"},
            IsInventoryEditor: {"admin", "manager", "inventory"},
            IsSalesTeam: {"admin", "manager", "employee"},
        }
        for permission, allowed in expected.items():
            for role, user in users.items():
                self.assertEqual(
                    permission().has_permission(self._request(user), None),
                    role in allowed,
                    f"{permission.__name__} / {role}",
                )

    def test_superusers_always_pass(self):
        from core.permissions import IsAdmin

        root = User.objects.create_superuser("root", password="x", role="inventory")
        self.assertTrue(IsAdmin().has_permission(self._request(root), None))


class ErrorResponseTests(TestCase):
    def test_matches_the_exception_handler_envelope(self):
        from core.api import ErrorResponse

        response = ErrorResponse("Nope.", status_code=404)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.data,
            {"success": False, "message": "Nope.", "error": {"status_code": 404, "detail": "Nope."}},
        )


class SettingsGuardTests(TestCase):
    def _load_settings(self, env):
        import importlib.util
        import os
        from pathlib import Path
        from unittest import mock

        path = Path(__file__).resolve().parent.parent / "config" / "settings.py"
        spec = importlib.util.spec_from_file_location("settings_probe", path)
        module = importlib.util.module_from_spec(spec)
        clean = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DEBUG")}
        clean.update(env)
        # Keep a developer's .env out of the probe.
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch(
            "dotenv.load_dotenv", return_value=False
        ):
            spec.loader.exec_module(module)
        return module

    def test_debug_defaults_to_off_and_then_needs_a_secret_key(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            self._load_settings({})

    def test_debug_mode_falls_back_to_a_dev_key(self):
        module = self._load_settings({"DEBUG": "True"})
        self.assertTrue(module.DEBUG)
        self.assertTrue(module.SECRET_KEY)

    def test_an_explicit_key_is_used(self):
        module = self._load_settings({"SECRET_KEY": "k" * 50})
        self.assertFalse(module.DEBUG)
        self.assertEqual(module.SECRET_KEY, "k" * 50)

