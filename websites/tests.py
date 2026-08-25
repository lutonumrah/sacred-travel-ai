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
