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



class HotelOfferEditTests(InventoryFixture):
    def setUp(self):
        super().setUp()
        from .models import HotelOffer

        self.hotel = self.cheap
        self.stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.employee = User.objects.create_user("emp", password="pw", role="employee")

        self.offer = HotelOffer.objects.create(
            hotel=self.hotel, title="Monsoon deal", room_type="Deluxe", price=Decimal("4000")
        )
        self.url = reverse("inventory:offer_edit", args=[self.hotel.pk, self.offer.pk])

    def post(self, **fields):
        data = {
            "title": "Monsoon deal",
            "room_type": "Deluxe",
            "price": "4000",
            "currency": "INR",
            "valid_from": "",
            "valid_to": "",
            "inclusions": "",
            "is_active": "on",
        }
        data.update(fields)
        return self.client.post(self.url, data)

    def test_inventory_editors_can_edit_an_offer(self):
        self.client.force_login(self.stock)
        self.assertContains(self.client.get(self.url), "Monsoon deal")
        response = self.post(title="Monsoon saver", price="3500", inclusions="Breakfast")
        self.assertRedirects(response, reverse("inventory:hotel_edit", args=[self.hotel.pk]))
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.title, "Monsoon saver")
        self.assertEqual(self.offer.price, Decimal("3500"))
        self.assertEqual(self.offer.inclusions, "Breakfast")
        page = self.client.get(reverse("inventory:hotel_edit", args=[self.hotel.pk]))
        self.assertContains(page, self.url)

    def test_the_offer_must_belong_to_the_hotel_in_the_url(self):
        other = Hotel.objects.create(
            name="Other", destination=self.hotel.destination, base_price=Decimal("1")
        )
        self.client.force_login(self.stock)
        url = reverse("inventory:offer_edit", args=[other.pk, self.offer.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_employees_cannot_edit_offers(self):
        self.client.force_login(self.employee)
        self.post(title="Hacked")
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.title, "Monsoon deal")


# --------------------------------------------------------------------------
# Batch 6: room offers drive the hotel price; archive / restore / delete
# --------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402

from .models import HotelOffer, InventoryWebsiteVisibility  # noqa: E402
from .selectors import hotel_nightly_price  # noqa: E402


class OfferPricingTests(InventoryFixture):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.trip = self.today + timedelta(days=30)

    def offer(self, hotel, price, **kwargs):
        return HotelOffer.objects.create(
            hotel=hotel, title=kwargs.pop("title", f"Offer {price}"), price=Decimal(price), **kwargs
        )

    def hotels(self, **kwargs):
        return {
            item["name"]: item
            for item in search_inventory(inventory_type=InventoryType.HOTEL, **kwargs)
        }

    def test_without_offers_the_base_price_is_used(self):
        item = self.hotels()["Palm Stay"]
        self.assertEqual(item["price"], Decimal("3000"))
        self.assertIsNone(item["offer"])

    def test_the_cheapest_active_offer_valid_for_the_dates_wins(self):
        self.offer(self.pricey, "6000", room_type="Deluxe", valid_from=self.trip)
        self.offer(self.pricey, "5000", title="Off season", is_active=False)
        self.offer(self.pricey, "4500", valid_to=self.trip - timedelta(days=1))
        item = self.hotels(check_in=self.trip, check_out=self.trip + timedelta(days=2))[
            "Beach Resort"
        ]
        self.assertEqual(item["price"], Decimal("6000"))
        self.assertEqual(item["offer"]["room_type"], "Deluxe")
        self.assertIn("Deluxe", item["detail"])

    def test_an_offer_must_cover_every_night_of_the_stay(self):
        self.offer(self.pricey, "4000", valid_from=self.trip, valid_to=self.trip + timedelta(days=1))
        short = hotel_nightly_price(self.pricey, self.trip, self.trip + timedelta(days=2))
        long = hotel_nightly_price(self.pricey, self.trip, self.trip + timedelta(days=3))
        self.assertEqual(short[0], Decimal("4000"))
        self.assertEqual(long[0], Decimal("9000"))
        self.assertIsNone(long[2])

    def test_with_no_dates_the_offer_valid_today_applies(self):
        self.offer(self.pricey, "7000", valid_from=self.trip)
        self.assertEqual(self.hotels()["Beach Resort"]["price"], Decimal("9000"))
        self.offer(self.pricey, "8000", valid_from=self.today, valid_to=self.today)
        self.assertEqual(self.hotels()["Beach Resort"]["price"], Decimal("8000"))

    def test_max_price_filters_on_the_effective_price(self):
        # Base 9000 but a 4000 offer: inside a 5000 budget.
        self.offer(self.pricey, "4000")
        # Base 3000 but its only valid offer is 6000: outside it.
        self.offer(self.cheap, "6000")
        names = set(self.hotels(max_price=5000))
        self.assertEqual(names, {"Beach Resort"})
        self.assertEqual(
            {hotel.name for hotel in list_hotels(max_price=5000)}, {"Beach Resort", "Closed Inn"}
        )

    def test_the_ai_quotes_the_offer_for_the_customer_dates(self):
        from conversations.ai import _inventory_block, match_inventory

        self.offer(
            self.pricey, "4200", title="Monsoon saver", room_type="Sea view",
            inclusions="Breakfast", valid_from=self.trip,
        )
        items = match_inventory(
            {
                "destination": "Goa",
                "product_type": "hotel",
                "travel_start": self.trip.isoformat(),
                "travel_end": (self.trip + timedelta(days=2)).isoformat(),
            }
        )
        resort = next(item for item in items if item["name"] == "Beach Resort")
        self.assertEqual(resort["price"], Decimal("4200"))
        block = _inventory_block(items)
        self.assertIn("INR 4,200 per night", block)
        self.assertIn("offer: Monsoon saver (includes Breakfast)", block)

    def test_the_search_api_takes_dates_and_returns_the_offer(self):
        self.offer(self.pricey, "4200", title="Saver", valid_from=self.trip)
        self.client.force_login(User.objects.create_user("emp", password="pw", role="employee"))
        rows = self.client.get(
            reverse("api_inventory:search"),
            {"inventory_type": "hotel", "check_in": self.trip.isoformat()},
        ).json()["data"]["results"]
        resort = next(row for row in rows if row["name"] == "Beach Resort")
        self.assertEqual(resort["price"], "4200.00")
        self.assertEqual(resort["offer"]["title"], "Saver")


class ArchiveTests(InventoryFixture):
    def setUp(self):
        super().setUp()
        self.stock = User.objects.create_user("stock", password="pw", role="inventory")
        self.employee = User.objects.create_user("emp", password="pw", role="employee")
        self.client.force_login(self.stock)

    def post(self, action, kind, obj):
        return self.client.post(reverse(f"inventory:{action}", args=[kind, obj.pk]))

    def test_archived_items_leave_lists_search_and_the_ai(self):
        from conversations.ai import match_inventory

        self.post("archive", "hotel", self.cheap)
        self.cheap.refresh_from_db()
        self.assertTrue(self.cheap.is_deleted)
        self.assertFalse(self.cheap.is_active)
        listed = self.client.get(reverse("inventory:hotels")).context["page_obj"]
        self.assertNotIn("Palm Stay", [hotel.name for hotel in listed])
        self.assertNotIn("Palm Stay", [item["name"] for item in search_inventory()])
        self.assertNotIn(
            "Palm Stay", [item["name"] for item in search_inventory(active_only=False)]
        )
        ai = match_inventory({"destination": "Goa", "product_type": "hotel"})
        self.assertNotIn("Palm Stay", [item["name"] for item in ai])
        archived = self.client.get(reverse("inventory:hotels"), {"archived": "on"})
        self.assertContains(archived, "Palm Stay")
        self.assertContains(archived, "Archived")
        self.assertContains(archived, reverse("inventory:restore", args=["hotel", self.cheap.pk]))

    def test_restore_brings_an_item_back_inactive(self):
        self.post("archive", "car", self.car)
        self.post("restore", "car", self.car)
        self.car.refresh_from_db()
        self.assertFalse(self.car.is_deleted)
        self.assertFalse(self.car.is_active)
        self.assertContains(self.client.get(reverse("inventory:cars")), "Goa Swift")

    def test_a_booked_item_can_never_be_hard_deleted(self):
        from bookings.models import Booking
        from crm.models import Customer

        Booking.objects.create(
            booking_number="STA-1", customer=Customer.objects.create(first_name="A"),
            product_type="package", product_id=self.package.pk, product_name="Manali Week",
        )
        self.post("archive", "package", self.package)
        response = self.post("delete", "package", self.package)
        self.assertTrue(TourPackage.objects.filter(pk=self.package.pk).exists())
        self.assertContains(self.client.get(response.url), "cannot be deleted")

    def test_hard_delete_needs_archiving_first_and_cleans_up(self):
        set_visibility(
            website=self.website, inventory_type="hotel", object_id=self.pricey.pk,
            is_visible=False,
        )
        self.post("delete", "hotel", self.pricey)
        self.assertTrue(Hotel.objects.filter(pk=self.pricey.pk).exists())
        self.post("archive", "hotel", self.pricey)
        self.post("delete", "hotel", self.pricey)
        self.assertFalse(Hotel.objects.filter(pk=self.pricey.pk).exists())
        self.assertFalse(
            InventoryWebsiteVisibility.objects.filter(object_id=self.pricey.pk).exists()
        )

    def test_a_destination_in_use_cannot_be_archived_or_deleted(self):
        self.post("archive", "destination", self.manali)
        self.manali.refresh_from_db()
        self.assertFalse(self.manali.is_deleted)

        self.post("archive", "package", self.package)
        self.post("archive", "destination", self.manali)
        self.manali.refresh_from_db()
        self.assertTrue(self.manali.is_deleted)
        # Archived destinations leave the pickers and the AI's place list.
        from conversations.ai import _known_destinations

        from .forms import HotelForm

        self.assertNotIn(self.manali, HotelForm().fields["destination"].queryset)
        self.assertNotIn("Manali", [row[0] for row in _known_destinations()])
        # The archived package still points at it.
        self.post("delete", "destination", self.manali)
        self.assertTrue(Destination.objects.filter(pk=self.manali.pk).exists())

    def test_employees_cannot_archive_and_see_no_buttons(self):
        self.client.force_login(self.employee)
        response = self.post("archive", "hotel", self.cheap)
        self.assertRedirects(response, reverse("dashboard:overview"))
        self.cheap.refresh_from_db()
        self.assertFalse(self.cheap.is_deleted)
        page = self.client.get(reverse("inventory:hotels"))
        self.assertNotContains(page, reverse("inventory:archive", args=["hotel", self.cheap.pk]))

    def test_archived_items_cannot_be_booked(self):
        from bookings.services import BookingError, booking_from_recommendation
        from conversations.models import Conversation, Recommendation
        from crm.models import Customer

        conversation = Conversation.objects.create(website=self.website, session_key="s1")
        rec = Recommendation.objects.create(
            conversation=conversation, inventory_type="package", object_id=self.package.pk,
            title="Manali Week", price=Decimal("38000"),
        )
        self.post("archive", "package", self.package)
        with self.assertRaises(BookingError):
            booking_from_recommendation(
                recommendation=rec, customer=Customer.objects.create(first_name="A"),
                travel_start=timezone.localdate() + timedelta(days=5),
            )
