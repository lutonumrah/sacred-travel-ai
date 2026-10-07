"""Shared world and HTTP helpers for the end-to-end journeys."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, Team, User
from bookings import payments
from bookings.models import Booking, Notification
from conversations.models import Conversation, KnowledgeArticle
from inventory.models import (
    CarRental,
    Destination,
    Hotel,
    InventoryType,
    InventoryWebsiteVisibility,
    TourPackage,
)
from websites.models import Website
from websites.services import issue_api_key

PASSWORD = "e2e-pass-1234"
SITE_ORIGIN = "https://www.goa-escapes.example"


@override_settings(
    # Rules engine unless a test configures a model; simulation-mode payments.
    ANTHROPIC_API_KEY="",
    GEMINI_API_KEY="",
    AI_ENABLED=True,
    RAZORPAY_KEY_ID="",
    RAZORPAY_KEY_SECRET="",
    RAZORPAY_WEBHOOK_SECRET="",
    BOOKING_TAX_PERCENT=Decimal("5"),
    SITE_URL="https://crm.scared-travel.example",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    BACKUP_ENABLED=False,
)
class JourneyTestCase(TestCase):
    """A small agency: one website with a widget key, inventory, staff of every role."""

    def setUp(self):
        # Throttle counters live in the cache.
        cache.clear()
        self.website = Website.objects.create(
            name="Goa Escapes",
            brand_name="Goa Escapes",
            domain="goa-escapes.example",
            source_identifier="goa-escapes",
        )
        self.other_website = Website.objects.create(
            name="Hill Trails", domain="hill-trails.example", source_identifier="hill-trails"
        )
        self.key = issue_api_key(website=self.website).public_key

        self.goa = Destination.objects.create(name="Goa", code="goa")
        self.hotel = Hotel.objects.create(
            name="Palm Stay",
            destination=self.goa,
            base_price=Decimal("3000"),
            star_rating=4,
            amenities=["pool", "breakfast"],
        )
        self.hotel_two = Hotel.objects.create(
            name="Sea Breeze Inn", destination=self.goa, base_price=Decimal("4500"), star_rating=3
        )
        # Exists, but must never be offered on this website.
        self.hidden_hotel = Hotel.objects.create(
            name="Secret Cove Resort", destination=self.goa, base_price=Decimal("2000")
        )
        InventoryWebsiteVisibility.objects.create(
            website=self.website,
            inventory_type=InventoryType.HOTEL,
            object_id=self.hidden_hotel.pk,
            is_visible=False,
        )
        self.car = CarRental.objects.create(
            name="Innova Crysta",
            destination=self.goa,
            vehicle_type="SUV",
            seats=7,
            daily_price=Decimal("3500"),
        )
        self.package = TourPackage.objects.create(
            name="Goa Beach Week",
            destination=self.goa,
            duration_days=6,
            duration_nights=5,
            base_price=Decimal("25000"),
        )

        self.admin = self.make_user("admin1", "admin", first_name="Asha")
        self.manager = self.make_user("manager1", "manager", first_name="Meera")
        self.agent = self.make_user("agent1", "employee", first_name="Ravi")
        self.agent_two = self.make_user("agent2", "employee", first_name="Sunil")
        self.stock = self.make_user("stock1", "inventory", first_name="Ishaan")
        self.team = Team.objects.create(name="North Desk")
        self.team.members.add(self.agent)

        self.policy = KnowledgeArticle.objects.create(
            title="Cancellation policy",
            category="cancellation",
            content="Free cancellation up to 14 days before arrival; 50% refund after that.",
            keywords="cancel, refund",
            website=self.website,
        )

        # Separate browsers: the customer on the brand's site, and staff.
        self.customer_browser = Client()
        self.staff = {}

    # ----------------------------------------------------------------- people

    def make_user(self, username, role, **extra):
        return User.objects.create_user(
            username,
            email=f"{username}@scared-travel.example",
            password=PASSWORD,
            role=role,
            **extra,
        )

    def browser_for(self, user):
        """A logged-in staff browser (real login form POST, kept per user)."""
        if user.pk not in self.staff:
            browser = Client()
            response = browser.post(
                reverse("accounts:login"),
                {"username": user.username, "password": PASSWORD},
            )
            self.assertEqual(response.status_code, 302, f"login failed for {user}")
            self.staff[user.pk] = browser
        return self.staff[user.pk]

    # -------------------------------------------------------------- transport

    def call(self, browser, method, url, data=None, *, json_body=True, **headers):
        """One HTTP request; on_commit work (emails) runs as it would after the response."""
        kwargs = dict(headers)
        if data is not None:
            if json_body and method != "get":
                kwargs["data"] = json.dumps(data)
                kwargs["content_type"] = "application/json"
            else:
                kwargs["data"] = data
        with self.captureOnCommitCallbacks(execute=True):
            return getattr(browser, method)(url, **kwargs)

    def staff_get(self, user, url, data=None, **headers):
        return self.call(self.browser_for(user), "get", url, data, **headers)

    def staff_post(self, user, url, data=None, **headers):
        """A dashboard form POST (form-encoded, like the browser sends)."""
        return self.call(self.browser_for(user), "post", url, data or {}, json_body=False, **headers)

    def staff_api_post(self, user, url, data=None):
        return self.call(self.browser_for(user), "post", url, data or {})

    # ----------------------------------------------------------- the customer

    def widget_chat(self, message, session="", **extra):
        response = self.call(
            self.customer_browser,
            "post",
            reverse("api_conversations:widget_chat"),
            {"key": self.key, "message": message, "session": session, **extra},
            HTTP_ORIGIN=SITE_ORIGIN,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Access-Control-Allow-Origin"], SITE_ORIGIN)
        return response.json()["data"]

    def widget_history(self, session, since=None):
        params = {"key": self.key, "session": session}
        if since is not None:
            params["since"] = since
        response = self.call(
            self.customer_browser,
            "get",
            reverse("api_conversations:widget_history"),
            params,
            HTTP_ORIGIN=SITE_ORIGIN,
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def widget_book(self, session, recommendation_id, **overrides):
        start = self.trip_start()
        payload = {
            "key": self.key,
            "session": session,
            "recommendation_id": recommendation_id,
            "name": "Ana Rao",
            "email": "ana@example.com",
            "phone": "9810000000",
            "travel_start": start.isoformat(),
            "travel_end": (start + timedelta(days=3)).isoformat(),
            "travelers": 2,
        }
        payload.update(overrides)
        return self.call(
            self.customer_browser,
            "post",
            reverse("api_conversations:widget_book"),
            payload,
            HTTP_ORIGIN=SITE_ORIGIN,
        )

    def pay_api(self, action, token, data=None):
        return self.call(
            self.customer_browser,
            "post",
            reverse(f"api_pay:{action}", args=[token]),
            data or {},
            HTTP_ORIGIN=SITE_ORIGIN,
        )

    def trip_start(self):
        return timezone.localdate() + timedelta(days=30)

    def chat_to_pending_booking(self):
        """Customer chats, picks the first hotel card and books it. Returns (session, booking)."""
        data = self.widget_chat("We need a hotel in Goa for 2 people")
        cards = data["recommendations"]
        hotel_card = next(card for card in cards if card["type"] == "hotel")
        response = self.widget_book(data["session"], hotel_card["recommendation_id"])
        self.assertEqual(response.status_code, 201, response.content)
        number = response.json()["data"]["booking"]["number"]
        return data["session"], Booking.objects.get(booking_number=number)

    # ---------------------------------------------------------------- Razorpay

    def webhook(self, event, *, event_id, payment=None, order=None, refund=None, secret=None):
        """POST a Razorpay webhook signed the way Razorpay signs it (raw body HMAC)."""
        body = {"event": event, "payload": {}}
        for name, entity in (("payment", payment), ("order", order), ("refund", refund)):
            if entity is not None:
                body["payload"][name] = {"entity": entity}
        raw = json.dumps(body).encode()
        signature = hmac.new(
            (secret or payments.SIMULATION_SECRET).encode(), raw, hashlib.sha256
        ).hexdigest()
        with self.captureOnCommitCallbacks(execute=True):
            return Client().post(
                reverse("api_bookings:razorpay_webhook"),
                data=raw,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=signature,
                HTTP_X_RAZORPAY_EVENT_ID=event_id,
            )

    # -------------------------------------------------------------- inspection

    def outbox_to(self, address, subject_contains=""):
        return [
            message
            for message in mail.outbox
            if address in message.to and subject_contains in message.subject
        ]

    def notifications_for(self, user, **filters):
        return Notification.objects.filter(recipient=user, **filters)

    def audit_actions(self, entity=None):
        rows = AuditLog.objects.all()
        if entity is not None:
            rows = rows.filter(entity_type=type(entity).__name__, entity_id=str(entity.pk))
        return set(rows.values_list("action", flat=True))

    def conversation(self, session):
        return Conversation.objects.get(session_key=session)
