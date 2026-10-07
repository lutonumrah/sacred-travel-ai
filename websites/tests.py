from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Website
from .selectors import active_key_for, list_websites
from .services import issue_api_key, revoke_api_key, soft_delete_website


class WebsiteSelectorTests(TestCase):
    def setUp(self):
        self.live = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.off = Website.objects.create(
            name="Old", domain="old.com", source_identifier="old", is_active=False
        )

    def test_soft_deleted_websites_disappear_from_the_list(self):
        soft_delete_website(website=self.off)
        self.assertEqual(list_websites().count(), 1)

    def test_status_filter(self):
        self.assertEqual(list_websites(status="active").count(), 1)
        self.assertEqual(list_websites(status="inactive").count(), 1)


class APIKeyTests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )

    def test_issued_keys_are_unique_and_prefixed(self):
        first = issue_api_key(website=self.website)
        second = issue_api_key(website=self.website)
        self.assertNotEqual(first.public_key, second.public_key)
        self.assertTrue(first.public_key.startswith("pk_"))
        self.assertTrue(first.secret_key.startswith("sk_"))

    def test_active_key_lookup_respects_revocation(self):
        key = issue_api_key(website=self.website)
        self.assertIsNotNone(active_key_for(key.public_key))
        revoke_api_key(api_key=key)
        self.assertIsNone(active_key_for(key.public_key))

    def test_key_lookup_ignores_inactive_websites(self):
        key = issue_api_key(website=self.website)
        self.website.is_active = False
        self.website.save()
        self.assertIsNone(active_key_for(key.public_key))


class WebsiteViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.client.force_login(self.manager)

    def test_creating_a_website_issues_a_widget_key(self):
        response = self.client.post(
            reverse("websites:create"),
            {
                "name": "Brand",
                "brand_name": "Brand",
                "domain": "https://brand.com/",
                "source_identifier": "brand",
                "primary_color": "#0F766E",
                "widget_enabled": "on",
                "is_active": "on",
                "notes": "",
            },
        )
        website = Website.objects.get(source_identifier="brand")
        self.assertRedirects(response, reverse("websites:detail", args=[website.pk]))
        # The scheme and trailing slash are stripped from the domain.
        self.assertEqual(website.domain, "brand.com")
        self.assertEqual(website.api_keys.filter(is_active=True).count(), 1)

    def test_detail_page_shows_the_embed_snippet(self):
        website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        key = issue_api_key(website=website)
        response = self.client.get(reverse("websites:detail", args=[website.pk]))
        self.assertContains(response, key.public_key)
        self.assertContains(response, "widget.js")

    def test_employees_cannot_create_websites(self):
        self.client.force_login(User.objects.create_user("emp", password="pw", role="employee"))
        response = self.client.get(reverse("websites:create"))
        self.assertRedirects(response, reverse("dashboard:overview"))


class WebsiteAPITests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.key = issue_api_key(website=self.website)

    def test_only_managers_can_list_websites(self):
        employee = User.objects.create_user("emp", password="pw", role="employee")
        self.client.force_login(employee)
        self.assertEqual(self.client.get(reverse("api_websites:list")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("api_websites:detail", args=[self.website.pk])).status_code,
            403,
        )

    def test_the_secret_key_is_never_serialised(self):
        manager = User.objects.create_user("mgr", password="pw", role="manager")
        self.client.force_login(manager)
        response = self.client.get(reverse("api_websites:list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret_key", response.content.decode())
        self.assertNotIn(self.key.secret_key, response.content.decode())



class WebsiteAccessTests(TestCase):
    """Widget keys and snippets are for managers and admins only."""

    def setUp(self):
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )
        self.key = issue_api_key(website=self.website)

    def test_employees_and_inventory_cannot_view_websites(self):
        for role in ("employee", "inventory"):
            self.client.force_login(User.objects.create_user(role, password="pw", role=role))
            for url in (
                reverse("websites:list"),
                reverse("websites:detail", args=[self.website.pk]),
                reverse("websites:edit", args=[self.website.pk]),
            ):
                self.assertRedirects(self.client.get(url), reverse("dashboard:overview"))
            self.client.post(reverse("websites:key_create", args=[self.website.pk]))
            self.assertEqual(self.website.api_keys.count(), 1)

    def test_managers_and_admins_can(self):
        for role in ("manager", "admin"):
            self.client.force_login(User.objects.create_user(role, password="pw", role=role))
            self.assertContains(self.client.get(reverse("websites:list")), "Main")
            self.assertContains(
                self.client.get(reverse("websites:detail", args=[self.website.pk])),
                self.key.public_key,
            )

    def test_the_widget_preview_hides_website_links_from_employees(self):
        self.client.force_login(User.objects.create_user("emp", password="pw", role="employee"))
        page = self.client.get(reverse("conversations:widget_preview"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse("websites:detail", args=[self.website.pk]))
