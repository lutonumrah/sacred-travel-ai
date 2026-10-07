from django.test import TestCase

from accounts.models import AuditLog, User
from bookings.models import Notification
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



# --------------------------------------------------------------------------
# Batch 3: email, scheduler, backups
# --------------------------------------------------------------------------

import os  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime  # noqa: E402
from io import StringIO  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from django.conf import settings  # noqa: E402
from django.core import mail  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import SimpleTestCase, override_settings  # noqa: E402

from core import backups, emails, scheduler  # noqa: E402


@override_settings(SITE_URL="https://ops.example/")
class EmailHelperTests(TestCase):
    def test_absolute_url_uses_site_url(self):
        self.assertEqual(emails.absolute_url("/pay/abc/"), "https://ops.example/pay/abc/")
        self.assertEqual(emails.absolute_url("https://x.com/a"), "https://x.com/a")

    def test_send_email_renders_text_and_html(self):
        user = User.objects.create_user("u", password="x", email="u@x.com")
        ok = emails.send_email(
            to="u@x.com",
            template="staff_notification",
            context={"title": "Hi\nthere", "body": "B & C", "type_label": "Lead", "user": user},
        )
        self.assertTrue(ok)
        message = mail.outbox[0]
        # Subjects are always one line.
        self.assertEqual(message.subject, "[Lead] Hi there")
        self.assertIn("B & C", message.body)
        self.assertIn("B &amp; C", message.alternatives[0][0])

    def test_send_email_never_raises(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("down")
        ), self.assertLogs("core.emails", "ERROR"):
            self.assertFalse(
                emails.send_email(to="u@x.com", template="staff_notification", context={})
            )
        self.assertFalse(emails.send_email(to="", template="staff_notification"))
        with self.assertLogs("core.emails", "ERROR"):
            self.assertFalse(emails.send_email(to="u@x.com", template="no_such_template"))


class StaffNotificationEmailTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            "mgr", password="x", role="manager", email="mgr@x.com"
        )
        self.quiet = User.objects.create_user(
            "quiet", password="x", role="manager", email="quiet@x.com", email_notifications=False
        )
        self.no_address = User.objects.create_user("noaddr", password="x", role="admin")

    @override_settings(SITE_URL="https://ops.example")
    def test_only_opted_in_users_with_an_address_get_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_managers(notification_type="lead", title="New lead: Goa", link="/crm/leads/1/")
        self.assertEqual([m.to for m in mail.outbox], [["mgr@x.com"]])
        self.assertIn("https://ops.example/crm/leads/1/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].subject, "[Lead] New lead: Goa")
        # Everyone still gets the in-app row.
        self.assertEqual(Notification.objects.count(), 3)

    def test_system_and_explicitly_quiet_notifications_are_not_mailed(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify(recipient=self.manager, notification_type="system", title="Housekeeping")
            notify(
                recipient=self.manager, notification_type="handoff", title="Msg", email=False
            )
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Notification.objects.count(), 2)

    def test_a_mail_failure_does_not_reach_the_caller(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("down")
        ), self.assertLogs("core.emails", "ERROR"), self.captureOnCommitCallbacks(execute=True):
            row = notify(recipient=self.manager, notification_type="payment", title="Paid")
        self.assertIsNotNone(row.pk)

    def test_new_chat_messages_stay_in_app(self):
        from conversations import services as chat_services
        from conversations.models import ConversationStatus

        conversation, _ = chat_services.start_conversation()
        conversation.status = ConversationStatus.HUMAN_ACTIVE
        conversation.assigned_to = self.manager
        conversation.save()
        with self.captureOnCommitCallbacks(execute=True):
            chat_services.handle_customer_message(conversation=conversation, text="hello?")
        self.assertTrue(Notification.objects.filter(title="New customer message").exists())
        self.assertEqual(mail.outbox, [])


class SchedulerTests(TestCase):
    def test_every_job_runs_and_one_failure_does_not_stop_the_rest(self):
        calls = []

        def ok_job(now):
            calls.append("ok")
            return 2

        def bad_job(now):
            calls.append("bad")
            raise RuntimeError("kaput")

        def last_job(now):
            calls.append("last")
            return "done"

        jobs = [("first", ok_job), ("broken", bad_job), ("last", last_job)]
        with self.assertLogs("core.scheduler", "ERROR"):
            results = scheduler.run_jobs(jobs=jobs)

        self.assertEqual(calls, ["ok", "bad", "last"])
        self.assertEqual([r[1] for r in results], [True, False, True])
        line = scheduler.summarise(results)
        self.assertIn("first=2", line)
        self.assertIn("broken=FAILED(RuntimeError: kaput)", line)
        self.assertIn("last=done", line)
        self.assertTrue(scheduler.did_something(results))

    def test_command_runs_the_real_jobs_once(self):
        out = StringIO()
        with mock.patch(
            "core.backups.run_nightly_backup", return_value="not due"
        ) as backup, mock.patch(
            "crm.services.send_due_reminders", return_value=1
        ) as reminders, mock.patch(
            "conversations.services.auto_resume_stale_conversations",
            side_effect=RuntimeError("boom"),
        ) as resume, self.assertLogs("core.scheduler", "ERROR"):
            call_command("run_scheduled_jobs", stdout=out)
        self.assertEqual((backup.call_count, reminders.call_count, resume.call_count), (1, 1, 1))
        line = out.getvalue().strip()
        self.assertTrue(line.startswith("scheduled jobs: reminders=1"), line)
        self.assertIn("auto_resume=FAILED(RuntimeError: boom)", line)
        self.assertIn("backup=not due", line)

    def test_quiet_runs_are_not_logged_in_loop_mode(self):
        quiet = [("a", True, 0, 0.0), ("b", True, "not due", 0.0)]
        self.assertFalse(scheduler.did_something(quiet))

    def test_loop_mode_stops_on_sigterm(self):
        from core.management.commands import run_scheduled_jobs as command

        out = StringIO()
        handlers = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        def fake_run_jobs():
            # SIGTERM arrives while the first run is in progress.
            handlers[command.signal.SIGTERM](command.signal.SIGTERM, None)
            return [("reminders", True, 0, 0.0)]

        with mock.patch.object(command.signal, "signal", fake_signal), mock.patch.object(
            command.scheduler, "run_jobs", side_effect=fake_run_jobs
        ) as run_jobs, mock.patch.object(command, "HEARTBEAT", Path(tempfile.mktemp())):
            call_command("run_scheduled_jobs", "--loop", "--interval", "60", stdout=out)
        self.assertEqual(run_jobs.call_count, 1)
        self.assertIn("Scheduler stopped.", out.getvalue())


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "db.sqlite3"
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t (v TEXT)")
        connection.execute("INSERT INTO t VALUES ('hello')")
        connection.commit()
        # Kept open: the backup must work while another connection is live.
        self.addCleanup(connection.close)
        self.backup_dir = self.tmp / "backups"

    def at(self, *args):
        return datetime(*args, tzinfo=ZoneInfo(settings.TIME_ZONE))

    def run_backup(self, now, **kwargs):
        with self.settings(BACKUP_DIR=str(self.backup_dir), BACKUP_HOUR=3, BACKUP_KEEP_DAYS=3):
            return backups.run_nightly_backup(now=now, source=self.db, **kwargs)

    def test_creates_a_readable_snapshot_once_per_day(self):
        self.assertEqual(self.run_backup(self.at(2026, 10, 7, 2, 59)), "not due")
        self.assertEqual(self.run_backup(self.at(2026, 10, 7, 3, 1)), "saved db-2026-10-07.sqlite3")
        snapshot = self.backup_dir / "db-2026-10-07.sqlite3"
        check = sqlite3.connect(snapshot)
        self.assertEqual(check.execute("SELECT v FROM t").fetchone(), ("hello",))
        check.close()
        # A restart later that day does not take another copy.
        self.assertEqual(self.run_backup(self.at(2026, 10, 7, 22, 0)), "done today")
        self.assertEqual(self.run_backup(self.at(2026, 10, 8, 3, 0)), "saved db-2026-10-08.sqlite3")

    def test_keeps_only_the_newest_snapshots(self):
        self.backup_dir.mkdir()
        (self.backup_dir / "db-manual-copy.sqlite3").write_text("keep me")
        for day in range(1, 6):
            self.run_backup(self.at(2026, 10, day, 4, 0))
        names = sorted(p.name for p in self.backup_dir.glob("db-*.sqlite3"))
        self.assertEqual(
            names,
            [
                "db-2026-10-03.sqlite3",
                "db-2026-10-04.sqlite3",
                "db-2026-10-05.sqlite3",
                "db-manual-copy.sqlite3",
            ],
        )

    def test_defaults_to_a_folder_beside_the_database(self):
        with self.settings(BACKUP_DIR=""):
            self.assertEqual(backups.backup_dir(self.db), self.tmp / "backups")

    def test_skips_databases_that_are_not_sqlite(self):
        with mock.patch.object(backups, "database_path", return_value=None):
            self.assertEqual(backups.run_nightly_backup(), "skipped: not sqlite")

    def test_disabled_by_setting(self):
        with self.settings(BACKUP_ENABLED=False):
            self.assertEqual(backups.run_nightly_backup(source=self.db), "disabled")


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class EntrypointTests(SimpleTestCase):
    """docker/entrypoint.sh, with `python` stubbed to record what it was asked to run."""

    def run_entrypoint(self, **env):
        stub_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, stub_dir, ignore_errors=True)
        log = stub_dir / "calls.log"
        stub = stub_dir / "python"
        stub.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
        stub.chmod(0o755)
        script = Path(settings.BASE_DIR) / "docker" / "entrypoint.sh"
        result = subprocess.run(
            ["sh", str(script), "echo", "started"],
            env={"PATH": f"{stub_dir}:{os.environ.get('PATH', '')}", **env},
            capture_output=True,
            text=True,
            check=True,
        )
        calls = log.read_text() if log.exists() else ""
        return calls, result.stdout

    def test_web_runs_migrations_and_collectstatic(self):
        calls, stdout = self.run_entrypoint()
        self.assertIn("manage.py migrate --noinput", calls)
        self.assertIn("manage.py collectstatic", calls)
        self.assertIn("started", stdout)

    def test_skip_flag_goes_straight_to_the_command(self):
        calls, stdout = self.run_entrypoint(SKIP_BOOT_TASKS="1", SEED_DEMO="true")
        self.assertEqual(calls, "")
        self.assertIn("started", stdout)
