from django.test import TestCase
from django.urls import reverse

from .models import Team, User
from .selectors import assignable_users, list_users


class UserModelTests(TestCase):
    def test_role_properties(self):
        admin = User.objects.create_user("a", password="x", role="admin")
        manager = User.objects.create_user("m", password="x", role="manager")
        employee = User.objects.create_user("e", password="x", role="employee")

        self.assertTrue(admin.is_admin)
        self.assertTrue(admin.is_manager)
        self.assertFalse(manager.is_admin)
        self.assertTrue(manager.is_manager)
        self.assertFalse(employee.is_manager)


class SelectorTests(TestCase):
    def setUp(self):
        User.objects.create_user("alice", password="x", role="manager", email="a@x.com")
        User.objects.create_user("bob", password="x", role="inventory")
        User.objects.create_user("carol", password="x", role="employee", is_active_employee=False)

    def test_filter_by_role_and_search(self):
        self.assertEqual(list_users(role="manager").count(), 1)
        self.assertEqual(list_users(q="a@x.com").count(), 1)

    def test_status_filter_covers_both_active_flags(self):
        self.assertEqual(list_users(status="inactive").count(), 1)

    def test_assignable_users_excludes_inventory_and_inactive_staff(self):
        usernames = set(assignable_users().values_list("username", flat=True))
        self.assertEqual(usernames, {"alice"})


class AccessControlTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", password="pw", role="admin")
        self.employee = User.objects.create_user("emp", password="pw", role="employee")

    def test_user_admin_pages_require_the_admin_role(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("accounts:users"))
        self.assertRedirects(response, reverse("dashboard:overview"))

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:users")).status_code, 200)

    def test_anonymous_users_are_sent_to_the_login_page(self):
        response = self.client.get(reverse("dashboard:overview"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_creating_a_user_writes_an_audit_entry(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("accounts:user_create"),
            {
                "username": "newbie",
                "first_name": "New",
                "last_name": "Bie",
                "email": "n@x.com",
                "phone": "",
                "role": "employee",
                "is_active_employee": "on",
                "password1": "sup3rSecret!23",
                "password2": "sup3rSecret!23",
            },
        )
        self.assertRedirects(response, reverse("accounts:users"))
        self.assertTrue(User.objects.filter(username="newbie").exists())
        from accounts.models import AuditLog

        self.assertTrue(AuditLog.objects.filter(action="user.create").exists())


class TeamTests(TestCase):
    def test_team_page_lists_members(self):
        admin = User.objects.create_user("admin", password="pw", role="admin")
        team = Team.objects.create(name="Sales")
        team.members.add(admin)
        self.client.force_login(admin)
        response = self.client.get(reverse("accounts:teams"))
        self.assertContains(response, "Sales")


class UserAPITests(TestCase):
    def test_only_admins_can_list_users(self):
        manager = User.objects.create_user("mgr", password="pw", role="manager")
        admin = User.objects.create_user("adm", password="pw", role="admin")
        for user, expected in ((manager, 403), (admin, 200)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("api_accounts:users")).status_code, expected)
            self.assertEqual(
                self.client.get(reverse("api_accounts:user_detail", args=[admin.pk])).status_code,
                expected,
            )



# --------------------------------------------------------------------------
# Batch 3: password reset and notification preferences
# --------------------------------------------------------------------------

import re  # noqa: E402

from django.core import mail  # noqa: E402
from django.test import override_settings  # noqa: E402

from .models import AuditLog  # noqa: E402


@override_settings(SITE_URL="https://ops.example")
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "agent", password="old-pass-1234", email="agent@x.com", role="employee"
        )

    def test_login_page_links_to_the_reset_form(self):
        page = self.client.get(reverse("accounts:login")).content.decode()
        self.assertIn(reverse("accounts:password_reset"), page)
        self.assertIn("Forgot password?", page)

    def test_full_reset_flow(self):
        response = self.client.post(
            reverse("accounts:password_reset"), {"email": "AGENT@x.com"}
        )
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["agent@x.com"])
        self.assertIn("Reset your", message.subject)
        # Built from SITE_URL, not the Host header of the request.
        match = re.search(r"https://ops\.example(/auth/password/reset/[^/\s]+/[^/\s]+/)", message.body)
        self.assertIsNotNone(match, message.body)
        self.assertIn(match.group(0), message.alternatives[0][0])

        # Django swaps the token for a session marker on the first visit.
        response = self.client.get(match.group(1), follow=True)
        self.assertContains(response, "Choose a new password")
        response = self.client.post(
            response.redirect_chain[-1][0],
            {"new_password1": "fresh-Secret-987", "new_password2": "fresh-Secret-987"},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("fresh-Secret-987"))
        self.assertTrue(AuditLog.objects.filter(action="user.password_reset").exists())

        # The link is single use.
        response = self.client.get(match.group(1), follow=True)
        self.assertContains(response, "Link no longer valid")

    def test_unknown_addresses_look_the_same_and_send_nothing(self):
        response = self.client.post(
            reverse("accounts:password_reset"), {"email": "nobody@x.com"}
        )
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(mail.outbox, [])

    def test_inactive_users_get_no_reset_mail(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(reverse("accounts:password_reset"), {"email": "agent@x.com"})
        self.assertEqual(mail.outbox, [])

    def test_a_mail_failure_still_shows_the_done_page(self):
        from unittest import mock

        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP down")
        ), self.assertLogs("core.emails", "ERROR"):
            response = self.client.post(
                reverse("accounts:password_reset"), {"email": "agent@x.com"}
            )
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertTrue(
            AuditLog.objects.filter(action="user.password_reset_email_failed").exists()
        )


class NotificationPreferenceTests(TestCase):
    def test_users_can_turn_email_notifications_off_for_themselves(self):
        user = User.objects.create_user("e", password="pw", role="employee", email="e@x.com")
        self.assertTrue(user.email_notifications)
        self.client.force_login(user)
        page = self.client.get(reverse("accounts:password_change")).content.decode()
        self.assertIn("Email notifications", page)

        response = self.client.post(
            reverse("accounts:preferences"), {"email": "new@x.com"}
        )
        self.assertRedirects(response, reverse("accounts:password_change"))
        user.refresh_from_db()
        self.assertFalse(user.email_notifications)
        self.assertEqual(user.email, "new@x.com")

    def test_admins_set_it_on_the_user_form(self):
        admin = User.objects.create_user("a", password="pw", role="admin")
        target = User.objects.create_user("t", password="pw", role="employee")
        self.client.force_login(admin)
        page = self.client.get(reverse("accounts:user_edit", args=[target.pk])).content.decode()
        self.assertIn('name="email_notifications"', page)
