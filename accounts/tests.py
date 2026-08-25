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
