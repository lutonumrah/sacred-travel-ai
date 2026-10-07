"""Security & permission sweep over every URL the project serves.

The URL patterns are enumerated from the resolver, so an endpoint added later
is covered automatically: it is either on the public allowlist below, or it
must refuse anonymous visitors; if it takes an object id it must be
classified in OBJECT_URLS (or be role-restricted), or the coverage test fails.
"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.contrib.admin import site as admin_site
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from bookings.models import Booking, BookingStatus, Notification
from conversations.models import Conversation, ConversationStatus
from core.mixins import RoleRequiredMixin
from core.permissions import HasRole
from crm.models import Customer, FollowUpTask, Lead, LeadStatus

from .base import JourneyTestCase

# Reachable without a staff login (each has its own credential or none by design).
PUBLIC = {
    "home",  # redirects to the dashboard, which then asks for a login
    "accounts:login",
    "accounts:logout",
    "accounts:password_reset",
    "accounts:password_reset_done",
    "accounts:password_reset_confirm",
    "accounts:password_reset_complete",
    "public:pay",  # payment-link token
    "api_pay:order",
    "api_pay:verify",
    "api_pay:simulate",
    "api_conversations:widget_chat",  # website widget key
    "api_conversations:widget_history",
    "api_conversations:widget_book",
    "api_crm:intake",
    "api_bookings:razorpay_webhook",  # HMAC signature
    "api_accounts:health",
}

# URLs that act on one object an employee may or may not be allowed to see.
OBJECT_URLS = {
    "crm:customer_detail": "customer",
    "crm:customer_edit": "customer",
    "crm:lead_detail": "lead",
    "crm:lead_edit": "lead",
    "crm:lead_status": "lead",
    "crm:lead_move": "lead",
    "crm:lead_assign": "lead",
    "crm:lead_note": "lead",
    "crm:follow_up_edit": "follow_up",
    "crm:follow_up_complete": "follow_up",
    "conversations:detail": "conversation",
    "conversations:live": "conversation",
    "conversations:reply": "conversation",
    "conversations:take_over": "conversation",
    "conversations:resume_ai": "conversation",
    "conversations:close": "conversation",
    "conversations:book": "conversation",
    "bookings:detail": "booking",
    "bookings:payment_create": "booking",
    "bookings:payment_link": "booking",
    "api_crm:customer_detail": "customer",
    "api_crm:lead_detail": "lead",
    "api_conversations:detail": "conversation",
    "api_conversations:handoff": "conversation",
    "api_bookings:detail": "booking",
}

# Object URLs that are scoped to the requesting user in another way, or are
# shared reference data every signed-in role may read.
SELF_SCOPED = {
    # Brand registry and embed snippet (public widget key only; no secrets).
    "websites:detail",
    # Marks only the caller's own notification read; anyone else's is untouched.
    "dashboard:notification_read",
}

STRING_ARGS = {
    "kind": {"inventory": "hotel", "dashboard": "leads"},
    "token": "not-a-real-token",
    "uidb64": "MQ",
}


def all_patterns(resolver=None, prefix="", namespace=None):
    """(name, namespace-qualified name, URLPattern) for every route, admin excluded."""
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            if entry.urlconf_name is admin_site.urls[0] or entry.app_name == "admin":
                continue
            ns = entry.namespace or namespace
            yield from all_patterns(entry, prefix + str(entry.pattern), ns)
        elif isinstance(entry, URLPattern) and entry.name:
            qualified = f"{namespace}:{entry.name}" if namespace else entry.name
            yield qualified, entry


def view_class(pattern):
    callback = pattern.callback
    return getattr(callback, "view_class", None) or getattr(callback, "cls", None)


def allowed_roles(pattern):
    """Roles the view admits, or None when any signed-in user may use it."""
    cls = view_class(pattern)
    if cls is None:
        return None
    if issubclass(cls, RoleRequiredMixin) and cls.allowed_roles:
        return set(cls.allowed_roles)
    roles = None
    for permission in getattr(cls, "permission_classes", []) or []:
        if isinstance(permission, type) and issubclass(permission, HasRole) and permission.allowed_roles:
            roles = set(permission.allowed_roles)
    return roles


def is_api(name):
    return name.split(":")[0].startswith("api_")


def build_url(name, pattern, pk=999999):
    kwargs = {}
    for key, converter in pattern.pattern.converters.items():
        if type(converter).__name__ == "IntConverter":
            kwargs[key] = pk
        else:
            value = STRING_ARGS.get(key, "x")
            if isinstance(value, dict):
                value = value.get(name.split(":")[0], "x")
            kwargs[key] = value
    return reverse(name, kwargs=kwargs)


def object_id_args(pattern):
    return [
        key
        for key, converter in pattern.pattern.converters.items()
        if type(converter).__name__ == "IntConverter"
    ]


class SecuritySweepTests(JourneyTestCase):
    def setUp(self):
        super().setUp()
        # Everything below belongs to agent2.
        self.customer = Customer.objects.create(
            first_name="Private", last_name="Person", email="private@example.com"
        )
        self.lead = Lead.objects.create(
            title="agent2's Goa lead",
            customer=self.customer,
            website=self.website,
            destination="Goa",
            assigned_to=self.agent_two,
            status=LeadStatus.QUALIFIED,
        )
        self.conversation = Conversation.objects.create(
            website=self.website,
            session_key="agent2-chat",
            customer=self.customer,
            lead=self.lead,
            assigned_to=self.agent_two,
            status=ConversationStatus.HUMAN_ACTIVE,
        )
        self.booking = Booking.objects.create(
            booking_number="STA-TEST-0001",
            website=self.website,
            customer=self.customer,
            lead=self.lead,
            conversation=self.conversation,
            created_by=self.agent_two,
            product_type="hotel",
            product_id=self.hotel.pk,
            product_name="Palm Stay",
            travel_start=timezone.localdate() + timedelta(days=20),
            subtotal=Decimal("3000"),
            total_amount=Decimal("3150"),
            status=BookingStatus.PENDING,
        )
        self.follow_up = FollowUpTask.objects.create(
            lead=self.lead,
            assigned_to=self.agent_two,
            title="Call back",
            due_at=timezone.now() + timedelta(days=1),
        )
        self.objects = {
            "customer": self.customer,
            "lead": self.lead,
            "conversation": self.conversation,
            "booking": self.booking,
            "follow_up": self.follow_up,
        }

    def snapshot(self):
        """Every field a hostile request could have changed."""
        state = {}
        for key, obj in self.objects.items():
            fresh = type(obj).objects.get(pk=obj.pk)
            state[key] = {
                field.attname: getattr(fresh, field.attname)
                for field in fresh._meta.concrete_fields
                if field.attname not in ("updated_at",)
            }
        state["messages"] = self.conversation.messages.count()
        state["notes"] = self.lead.notes.count()
        state["payments"] = self.booking.payments.count()
        return state

    # ---------------------------------------------------------- coverage

    def test_every_url_is_classified(self):
        unclassified = []
        for name, pattern in all_patterns():
            if name in PUBLIC or not object_id_args(pattern):
                continue
            if name in OBJECT_URLS or name in SELF_SCOPED:
                continue
            roles = allowed_roles(pattern)
            if roles is not None and "employee" not in roles:
                continue  # role-restricted; checked by the role sweep
            unclassified.append(name)
        self.assertEqual(
            unclassified,
            [],
            "New object URLs must be added to OBJECT_URLS (and protected) in e2e/test_security.py",
        )
        names = {name for name, _pattern in all_patterns()}
        self.assertEqual(PUBLIC - names, set(), "stale names in the public allowlist")
        self.assertEqual(set(OBJECT_URLS) - names, set(), "stale names in OBJECT_URLS")

    # ---------------------------------------------------------- anonymous

    def test_anonymous_visitors_are_turned_away_from_every_staff_url(self):
        checked = 0
        login = reverse("accounts:login")
        for name, pattern in all_patterns():
            if name in PUBLIC:
                continue
            url = build_url(name, pattern, pk=self.lead.pk)
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.customer_browser, method)(url)
                    if is_api(name):
                        self.assertIn(response.status_code, (401, 403), response.content[:200])
                    else:
                        self.assertEqual(response.status_code, 302)
                        self.assertTrue(response["Location"].startswith(login), response["Location"])
                    checked += 1
        self.assertGreater(checked, 100)
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.PENDING)

    def test_public_endpoints_still_need_their_own_credential(self):
        for name in ("api_pay:order", "api_pay:simulate", "api_pay:verify"):
            response = self.call(self.customer_browser, "post", reverse(name, args=["nope"]), {})
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(
            self.call(self.customer_browser, "get", reverse("public:pay", args=["nope"])).status_code,
            404,
        )
        for name in ("api_conversations:widget_chat", "api_crm:intake"):
            response = self.call(
                self.customer_browser, "post", reverse(name), {"key": "pk_nope", "message": "hi", "name": "x", "email": "a@b.co"}
            )
            self.assertEqual(response.status_code, 403, name)
        response = self.call(
            self.customer_browser, "get", reverse("api_conversations:widget_history"),
            {"key": self.key, "session": "agent2-chat-wrong"},
        )
        self.assertEqual(response.status_code, 404)
        # Unsigned webhook.
        response = self.customer_browser.post(
            reverse("api_bookings:razorpay_webhook"), data="{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    # ----------------------------------------------------------- roles

    def test_each_role_is_kept_out_of_views_it_may_not_use(self):
        users = {
            "employee": self.agent,
            "inventory": self.stock,
            "manager": self.manager,
        }
        checked = 0
        for name, pattern in all_patterns():
            if name in PUBLIC:
                continue
            roles = allowed_roles(pattern)
            if roles is None:
                continue
            url = build_url(name, pattern)
            for role, user in users.items():
                if role in roles:
                    continue
                for method in ("get", "post"):
                    with self.subTest(url=url, role=role, method=method):
                        response = self.call(self.browser_for(user), method, url, {}, json_body=False)
                        if is_api(name):
                            self.assertEqual(response.status_code, 403)
                        else:
                            self.assertRedirects(
                                response, reverse("dashboard:overview"), fetch_redirect_response=False
                            )
                        checked += 1
        self.assertGreater(checked, 100)

    def test_the_inventory_role_is_blocked_from_crm_chats_and_bookings(self):
        for name, pattern in all_patterns():
            app = name.split(":")[0].removeprefix("api_")
            if name in PUBLIC or app not in ("crm", "conversations", "bookings"):
                continue
            if name in ("conversations:ai_settings", "conversations:ai_settings_test"):
                continue  # admin-only, covered by the role sweep
            roles = allowed_roles(pattern)
            with self.subTest(name=name):
                self.assertIsNotNone(roles, f"{name} is open to every signed-in role")
                self.assertNotIn("inventory", roles)
        browser = self.browser_for(self.stock)
        for url in (
            reverse("crm:lead_detail", args=[self.lead.pk]),
            reverse("conversations:inbox"),
            reverse("bookings:detail", args=[self.booking.pk]),
        ):
            self.assertRedirects(browser.get(url), reverse("dashboard:overview"), fetch_redirect_response=False)

    # ------------------------------------------- another employee's objects

    def test_an_employee_gets_404_on_every_url_for_another_employees_objects(self):
        before = self.snapshot()
        checked = 0
        for name, pattern in all_patterns():
            kind = OBJECT_URLS.get(name)
            if kind is None:
                continue
            roles = allowed_roles(pattern)
            if roles is not None and "employee" not in roles:
                continue
            url = build_url(name, pattern, pk=self.objects[kind].pk)
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = self.call(
                        self.browser_for(self.agent),
                        method,
                        url,
                        {
                            "status": "lost",
                            "content": "hijack",
                            "body": "hijack",
                            "assigned_to": self.agent.pk,
                            "action": "take_over",
                            "recommendation_id": 1,
                        },
                        json_body=is_api(name),
                    )
                    if not hasattr(view_class(pattern), method):
                        # e.g. GET on a POST-only action: refused before any lookup.
                        self.assertIn(response.status_code, (404, 405))
                    else:
                        self.assertEqual(response.status_code, 404, response.content[:300])
                    checked += 1
        self.assertGreater(checked, 40)
        self.assertEqual(self.snapshot(), before, "a refused request still changed something")

        # Payment order for someone else's booking through the API.
        response = self.staff_api_post(
            self.agent, reverse("api_bookings:payment_create"), {"booking_id": self.booking.pk}
        )
        self.assertEqual(response.status_code, 404)
        # And none of it shows up in the agent's lists.
        for list_url, marker in (
            (reverse("crm:leads"), reverse("crm:lead_detail", args=[self.lead.pk])),
            (reverse("crm:customers"), reverse("crm:customer_detail", args=[self.customer.pk])),
            (reverse("conversations:history"), reverse("conversations:detail", args=[self.conversation.pk])),
            (reverse("bookings:list"), reverse("bookings:detail", args=[self.booking.pk])),
            (reverse("crm:follow_ups"), "Call back"),
            (reverse("crm:pipeline"), "agent2&#x27;s Goa lead"),
        ):
            self.assertNotContains(self.staff_get(self.agent, list_url), marker, msg_prefix=list_url)
        for api_name in ("api_crm:leads", "api_crm:customers", "api_bookings:list", "api_crm:follow_ups"):
            body = self.staff_get(self.agent, reverse(api_name)).json()["data"]
            self.assertEqual(body["count"], 0, api_name)
        # The owner, and managers, do see it.
        for user in (self.agent_two, self.manager):
            self.assertEqual(
                self.staff_get(user, reverse("crm:lead_detail", args=[self.lead.pk])).status_code, 200
            )

    def test_notifications_are_private_to_their_recipient(self):
        note = Notification.objects.create(
            recipient=self.agent_two, notification_type="lead", title="Secret lead"
        )
        self.staff_post(self.agent, reverse("dashboard:notification_read", args=[note.pk]))
        note.refresh_from_db()
        self.assertFalse(note.is_read)
        self.assertNotContains(
            self.staff_get(self.agent, reverse("dashboard:notifications")), "Secret lead"
        )
        body = self.staff_get(self.agent, reverse("api_dashboard:notifications")).json()["data"]
        self.assertNotIn("Secret lead", str(body))

    def test_redirect_targets_stay_on_this_site(self):
        task = FollowUpTask.objects.create(
            lead=Lead.objects.create(title="Mine", assigned_to=self.agent),
            assigned_to=self.agent,
            title="Mine",
            due_at=timezone.now() + timedelta(days=1),
        )
        for target in ("https://evil.example/", "//evil.example/", "/\\evil.example/", "\\\\evil.example"):
            with self.subTest(target=target):
                response = self.staff_post(
                    self.agent, reverse("crm:follow_up_complete", args=[task.pk]), {"next": target}
                )
                self.assertEqual(response.status_code, 302)
                location = response["Location"]
                # Browsers read a literal backslash as a slash; an escaped one is a path.
                self.assertEqual(urlsplit(location.replace("\\", "/")).netloc, "", location)

    def test_the_live_login_page_gives_no_credentials_away(self):
        page = self.customer_browser.get(reverse("accounts:login"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "travel1234")
        with self.settings(DEBUG=True):
            self.assertContains(self.customer_browser.get(reverse("accounts:login")), "travel1234")

    def test_sign_in_forms_keep_an_origin_csrf_can_check(self):
        # "no-referrer" made browsers send `Origin: null` on the login POST,
        # which Django's CSRF check rejects (caught by browser_tests/).
        for name in ("accounts:login", "accounts:password_reset"):
            page = self.customer_browser.get(reverse(name))
            self.assertNotContains(page, 'content="no-referrer"')
            self.assertContains(page, 'name="referrer" content="same-origin"')
        # A browser login with a real Origin header passes CSRF.
        from django.test import Client

        browser = Client(enforce_csrf_checks=True)
        page = browser.get(reverse("accounts:login"))
        token = page.cookies["csrftoken"].value
        response = browser.post(
            reverse("accounts:login"),
            {"username": "agent1", "password": "e2e-pass-1234", "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="http://testserver",
        )
        self.assertEqual(response.status_code, 302)
