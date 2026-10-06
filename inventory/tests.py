from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from websites.models import Website

from .models import CarRental, Destination, Hotel, InventoryType, TourPackage
from .selectors import get_inventory_object, list_hotels, search_inventory
from .services import set_visibility, soft_delete, toggle_active


class InventoryFixture(TestCase):
    def setUp(self):
        self.goa = Destination.objects.create(name="Goa", code="goa", city="Panaji")
        self.manali = Destination.objects.create(name="Manali", code="manali", city="Manali")
        self.cheap = Hotel.objects.create(
            name="Palm Stay", destination=self.goa, base_price=Decimal("3000")
        )
        self.pricey = Hotel.objects.create(
            name="Beach Resort", destination=self.goa, base_price=Decimal("9000")
        )
        self.hidden = Hotel.objects.create(
            name="Closed Inn", destination=self.goa, base_price=Decimal("1000"), is_active=False
        )
        self.car = CarRental.objects.create(
            name="Goa Swift",
            destination=self.goa,
            vehicle_type="Hatchback",
            daily_price=Decimal("1400"),
        )
        self.package = TourPackage.objects.create(
            name="Manali Week", destination=self.manali, base_price=Decimal("38000")
        )
        self.website = Website.objects.create(
            name="Main", domain="main.com", source_identifier="main"
        )


class SearchTests(InventoryFixture):
    def test_price_filter_excludes_items_over_budget(self):
        results = search_inventory(inventory_type=InventoryType.HOTEL, max_price=5000)
        self.assertEqual([item["name"] for item in results], ["Palm Stay"])

    def test_inactive_inventory_is_never_returned(self):
        names = [item["name"] for item in search_inventory()]
        self.assertNotIn("Closed Inn", names)

    def test_destination_matching_spans_all_three_kinds(self):
        results = search_inventory(destination="Goa")
        kinds = {item["inventory_type"] for item in results}
        self.assertEqual(kinds, {InventoryType.HOTEL, InventoryType.CAR})

    def test_a_hide_rule_removes_an_item_from_that_website_only(self):
        set_visibility(
            website=self.website,
            inventory_type=InventoryType.HOTEL,
            object_id=self.pricey.pk,
            is_visible=False,
        )
        without = [item["name"] for item in search_inventory(website=self.website)]
        with_all = [item["name"] for item in search_inventory()]
        self.assertNotIn("Beach Resort", without)
        self.assertIn("Beach Resort", with_all)

    def test_priority_pushes_an_item_to_the_front(self):
        set_visibility(
            website=self.website,
            inventory_type=InventoryType.PACKAGE,
            object_id=self.package.pk,
            is_visible=True,
            priority=10,
        )
        results = search_inventory(website=self.website)
        self.assertEqual(results[0]["name"], "Manali Week")

    def test_results_are_normalised_across_kinds(self):
        item = search_inventory(inventory_type=InventoryType.CAR)[0]
        self.assertEqual(item["price"], Decimal("1400"))
        self.assertEqual(item["price_label"], "per day")
        self.assertIn("Hatchback", item["detail"])


class LifecycleTests(InventoryFixture):
    def test_soft_delete_hides_the_item_everywhere(self):
        soft_delete(obj=self.cheap, kind="hotel")
        self.assertEqual(list_hotels().filter(pk=self.cheap.pk).count(), 0)
        self.assertIsNone(get_inventory_object(InventoryType.HOTEL, self.cheap.pk))

    def test_toggle_active_flips_the_flag(self):
        toggle_active(obj=self.cheap, kind="hotel")
        self.cheap.refresh_from_db()
        self.assertFalse(self.cheap.is_active)


class InventoryViewTests(InventoryFixture):
    def setUp(self):
        super().setUp()
        self.stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.employee = User.objects.create_user("emp", password="pw", role="employee")

    def test_inventory_role_can_create_a_hotel(self):
        self.client.force_login(self.stock)
        response = self.client.post(
            reverse("inventory:hotel_create"),
            {
                "name": "New Hotel",
                "destination": self.goa.pk,
                "star_rating": 4,
                "address": "",
                "description": "",
                "amenities": "Wi-Fi, Pool",
                "base_price": "4500",
                "currency": "INR",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("inventory:hotels"))
        hotel = Hotel.objects.get(name="New Hotel")
        self.assertEqual(hotel.amenities, ["Wi-Fi", "Pool"])

    def test_employees_cannot_reach_the_hotel_form(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("inventory:hotel_create"))
        self.assertRedirects(response, reverse("dashboard:overview"))

    def test_search_page_renders_matches(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("inventory:search"), {"destination": "Goa"})
        self.assertContains(response, "Palm Stay")

    def test_visibility_form_rejects_an_unknown_item_id(self):
        self.client.force_login(self.stock)
        self.client.post(
            reverse("inventory:visibility_save"),
            {
                "website": self.website.pk,
                "inventory_type": InventoryType.HOTEL,
                "object_id": 99999,
                "is_visible": "on",
                "priority": 0,
            },
        )
        from .models import InventoryWebsiteVisibility

        self.assertEqual(InventoryWebsiteVisibility.objects.count(), 0)

    def test_toggle_ignores_an_off_site_referer(self):
        self.client.force_login(self.stock)
        response = self.client.post(
            reverse("inventory:toggle", args=["hotel", self.cheap.pk]),
            HTTP_REFERER="https://evil.example.com/phish",
        )
        self.assertRedirects(response, reverse("inventory:hotels"))

    def test_toggle_returns_to_a_same_site_referer(self):
        self.client.force_login(self.stock)
        back = reverse("inventory:hotels") + "?q=palm"
        response = self.client.post(
            reverse("inventory:toggle", args=["hotel", self.cheap.pk]),
            HTTP_REFERER=f"http://testserver{back}",
        )
        self.assertRedirects(response, f"http://testserver{back}", fetch_redirect_response=False)

    def test_the_search_api_clamps_a_bad_limit(self):
        self.client.force_login(self.employee)
        url = reverse("api_inventory:search")
        for limit in ("abc", "-5", "0"):
            response = self.client.get(url, {"limit": limit})
            self.assertEqual(response.status_code, 200, limit)
        body = self.client.get(url, {"limit": "1"}).json()
        self.assertEqual(body["data"]["count"], 1)
        with mock.patch("inventory.selectors.search_inventory", return_value=[]) as search:
            self.client.get(url, {"limit": "5000"})
            self.assertEqual(search.call_args.kwargs["limit"], 50)
            self.client.get(url, {"limit": "abc", "website": "x"})
            self.assertEqual(search.call_args.kwargs["limit"], 30)

