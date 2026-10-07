"""Shared fixtures for the browser smoke suite.

Every test runs once per engine (pytest-playwright's ``--browser`` option,
which run.sh sets to chromium, firefox and webkit) and once per device profile
below, so a test id reads ``test_name[chromium-iphone]``.

Environment (set by run.sh):
    APP_URL      the Django app as the browser sees it, e.g. http://travel-os:8000
    SITE_URL     the separate-origin test page that embeds the widget
    WIDGET_KEY   public widget key of the website whose domain is SITE_URL's host
    WIDGET_TITLE the website's name (the widget's dialog label)
"""

import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

APP_URL = os.environ.get("APP_URL", "http://travel-os:8000").rstrip("/")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
WIDGET_KEY = os.environ.get("WIDGET_KEY", "")
WIDGET_TITLE = os.environ.get("WIDGET_TITLE", "Scared Travel")
USERNAME = os.environ.get("STAFF_USERNAME", "admin")
PASSWORD = os.environ.get("STAFF_PASSWORD", "travel1234")
ARTIFACTS = Path(os.environ.get("ARTIFACTS_DIR", Path(__file__).parent / "artifacts"))

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
# Playwright device descriptors; "desktop" is a plain 1280x800 window.
PROFILES = {"desktop": None, "iphone": "iPhone 15", "pixel": "Pixel 7"}


def pytest_configure(config):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    missing = [name for name in ("SITE_URL", "WIDGET_KEY") if not os.environ.get(name)]
    if missing:
        raise pytest.UsageError(
            f"{', '.join(missing)} not set — run the suite through browser_tests/run.sh"
        )


@pytest.fixture(params=list(PROFILES))
def profile(request):
    """Device profile name: desktop, iphone or pixel."""
    marker_mobile = request.node.get_closest_marker("mobile_only")
    marker_desktop = request.node.get_closest_marker("desktop_only")
    if marker_mobile and request.param == "desktop":
        pytest.skip(f"mobile only: {marker_mobile.kwargs.get('reason', '')}".strip())
    if marker_desktop and request.param != "desktop":
        pytest.skip(f"desktop only: {marker_desktop.kwargs.get('reason', '')}".strip())
    return request.param


@pytest.fixture
def is_mobile(profile):
    return profile != "desktop"


@pytest.fixture
def browser_context_args(browser_context_args, playwright, browser_name, profile):
    args = {**browser_context_args, "base_url": APP_URL}
    if profile == "desktop":
        return {**args, "viewport": DESKTOP_VIEWPORT}
    descriptor = dict(playwright.devices[PROFILES[profile]])
    descriptor.pop("default_browser_type", None)
    if browser_name == "firefox":
        # Firefox has no `isMobile` (Playwright raises on it): emulate the phone
        # by viewport, DPR, touch and user agent only. Layout is driven by the
        # viewport width, which is what these tests check.
        descriptor.pop("is_mobile", None)
    return {**args, **descriptor}


@pytest.fixture
def shot(request, browser_name, profile):
    """`shot(page, "step")` saves artifacts/<engine>-<profile>-<scenario>-<nn>-<step>.png."""
    scenario = re.sub(r"^test_", "", request.node.originalname)
    counter = {"n": 0}

    def take(page, step, full_page=False):
        counter["n"] += 1
        name = f"{browser_name}-{profile}-{scenario}-{counter['n']:02d}-{step}.png"
        page.screenshot(path=str(ARTIFACTS / name), full_page=full_page)

    return take


@pytest.fixture
def login(page):
    """Sign in as the demo admin; leaves the page on the dashboard."""

    def do_login(username=USERNAME, password=PASSWORD):
        page.goto("/auth/login/")
        page.locator("#id_username").fill(username)
        page.locator("#id_password").fill(password)
        page.get_by_role("button", name="Login").click()
        page.wait_for_url(re.compile(r"/dashboard/"))

    return do_login


@pytest.fixture
def api(playwright):
    """An anonymous API client for the public widget endpoints (no browser)."""
    context = playwright.request.new_context(base_url=APP_URL)
    yield context
    context.dispose()


def unique(prefix="bt"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def future_dates(offset=30, nights=2):
    start = date.today() + timedelta(days=offset)
    return start.isoformat(), (start + timedelta(days=nights)).isoformat()


def widget_chat(api, message, session=""):
    """POST a widget chat message the way the widget does; returns the envelope's data."""
    response = api.post(
        "/api/v1/conversations/widget/chat/",
        data={"key": WIDGET_KEY, "message": message, "session": session},
    )
    assert response.ok, f"widget chat failed: {response.status} {response.text()}"
    body = response.json()
    assert body["success"], body
    return body["data"]


def widget_booking(api, text="hotel in Goa for 2 people"):
    """Chat, then book the first bookable recommendation. Returns the payment URL."""
    data = widget_chat(api, text)
    session = data["session"]
    cards = [card for message in data.get("messages") or [] for card in message.get("cards") or []]
    cards = cards or data.get("recommendations") or []
    bookable = [card for card in cards if card.get("bookable") and card.get("recommendation_id")]
    assert bookable, f"no bookable recommendation for {text!r}: {data}"
    start, end = future_dates()
    tag = unique("pay")
    response = api.post(
        "/api/v1/conversations/widget/book/",
        data={
            "key": WIDGET_KEY,
            "session": session,
            "recommendation_id": bookable[0]["recommendation_id"],
            "name": "Mobile Payer",
            "email": f"{tag}@example.com",
            "phone": "98" + str(uuid.uuid4().int)[:8],
            "travel_start": start,
            "travel_end": end,
            "travelers": 2,
        },
    )
    assert response.ok, f"widget booking failed: {response.status} {response.text()}"
    body = response.json()
    assert body["success"], body
    return body["data"]["payment_url"]
