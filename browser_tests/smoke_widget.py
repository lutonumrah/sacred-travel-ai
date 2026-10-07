"""The embeddable widget on a separate-origin site, and the public pay page."""

import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from conftest import APP_URL, SITE_URL, WIDGET_TITLE, future_dates, unique, widget_booking


def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _open_widget(page):
    page.goto(SITE_URL + "/?utm_source=browser-tests&utm_campaign=smoke")
    assert _origin(page.url) != _origin(APP_URL), "the test page must be on another origin"
    page.get_by_role("button", name="Open chat").click()
    panel = page.get_by_role("dialog", name=WIDGET_TITLE)
    expect(panel).to_be_visible()
    expect(panel.locator(".scared-msg.them").first).to_contain_text("Where would you like to travel?")
    return panel


def _no_horizontal_scroll(page):
    sizes = page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, inner: window.innerWidth,"
        " body: document.body.scrollWidth})"
    )
    assert sizes["scroll"] <= sizes["inner"] and sizes["body"] <= sizes["inner"], sizes


def test_widget_chat_book_and_pay(page, context, shot):
    panel = _open_widget(page)
    shot(page, "open")

    with page.expect_response(
        lambda r: "/api/v1/conversations/widget/chat/" in r.url and r.request.method == "POST"
    ) as chat:
        panel.get_by_label("Your message").fill("hotel in Goa for 2 people")
        panel.get_by_role("button", name="Send").click()
    # A genuine cross-origin call, allowed by CORS for this site only.
    assert chat.value.ok
    assert chat.value.headers.get("access-control-allow-origin") == _origin(SITE_URL)

    expect(panel.locator(".scared-msg.me").last).to_have_text("hotel in Goa for 2 people")
    cards = panel.locator(".scared-card").filter(has=page.get_by_role("button", name="Book this"))
    expect(cards.first).to_be_visible()
    # Seeded Goa hotels only (seed_demo): the rule engine matched the destination.
    expect(cards.first).to_contain_text(re.compile("Sunset Beach Resort|Palolem Palm Stay"))
    expect(cards.first).to_contain_text("INR")
    shot(page, "recommendations")

    card = cards.first
    card.get_by_role("button", name="Book this").click()
    form = card.locator("form.scared-book")
    expect(form).to_be_visible()
    start, end = future_dates(offset=40)
    form.get_by_label("Your name").fill("Widget Tester")
    form.get_by_label("Email").fill(f"{unique()}@example.com")
    form.get_by_label("Phone").fill("9876543210")
    form.get_by_label("Check-in").fill(start)
    form.get_by_label("Check-out").fill(end)
    expect(form.get_by_label("Travellers")).to_have_value("2")
    form.scroll_into_view_if_needed()
    shot(page, "booking-form")
    form.get_by_role("button", name="Confirm and get payment link").click()

    pay_link = panel.get_by_role("link", name="Pay now")
    expect(pay_link).to_be_visible()
    expect(pay_link.locator("xpath=..")).to_contain_text("Total INR")
    shot(page, "payment-link")

    with context.expect_page() as popup:
        pay_link.click()
    pay = popup.value
    pay.wait_for_load_state()
    assert pay.url.startswith(APP_URL + "/pay/")
    expect(pay.get_by_role("heading", level=1)).to_be_visible()
    simulate = pay.get_by_role("button", name="Simulate payment (test mode)")
    expect(simulate).to_be_visible()
    _no_horizontal_scroll(pay)
    shot(pay, "pay-page", full_page=True)

    booking_number = pay.locator("dd.mono").first.inner_text().strip()
    assert re.fullmatch(r"STA-\d{8}-\d{4}", booking_number), booking_number
    simulate.click()
    expect(pay.locator(".public-banner.ok")).to_contain_text(
        "Payment received — your booking is confirmed.", timeout=15_000
    )
    expect(pay.locator(".badge")).to_have_text("Confirmed")
    shot(pay, "paid", full_page=True)

    # The widget is polling for the outstanding payment: the confirmation shows up
    # in the chat without the visitor doing anything.
    page.bring_to_front()
    expect(panel.locator(".scared-msg.note").last).to_have_text(
        f"Payment received — booking {booking_number} confirmed.", timeout=45_000
    )
    shot(page, "widget-confirmed")


def test_widget_layout(page, shot, is_mobile):
    panel = _open_widget(page)
    launcher = page.locator(".scared-launcher")
    viewport = page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
    box = panel.bounding_box()
    if is_mobile:
        # Phones: the open chat covers the whole screen and the launcher hides.
        expect(launcher).to_be_hidden()
        assert box["x"] == 0 and box["y"] == 0, box
        assert abs(box["width"] - viewport["width"]) <= 1, (box, viewport)
        assert abs(box["height"] - viewport["height"]) <= 1, (box, viewport)
        input_box = panel.get_by_label("Your message").bounding_box()
        assert input_box["y"] + input_box["height"] <= viewport["height"], input_box
        assert input_box["height"] >= 44, "tap target too small"
        shot(page, "fullscreen")
        panel.get_by_role("button", name="Close chat").click()
        expect(panel).to_be_hidden()
        expect(launcher).to_be_visible()
    else:
        # Desktop: a floating 360px panel above the launcher.
        expect(launcher).to_be_visible()
        assert box["width"] == pytest.approx(360, abs=1), box
        assert box["x"] > viewport["width"] / 2, box
        shot(page, "floating")
    _no_horizontal_scroll(page)


def test_pay_page_fits_screen(page, api, shot, is_mobile):
    payment_url = widget_booking(api)
    assert payment_url.startswith(APP_URL + "/pay/")
    page.goto(payment_url)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    expect(page.locator(".badge")).to_have_text("Pending Payment")
    button = page.get_by_role("button", name="Simulate payment (test mode)")
    expect(button).to_be_visible()
    expect(button).to_have_css("min-height", "48px")
    _no_horizontal_scroll(page)

    viewport_width = page.evaluate("window.innerWidth")
    for locator in (button, page.locator(".public-price"), page.locator(".panel").first):
        box = locator.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= viewport_width + 0.5, (locator, box)
    if is_mobile:
        # The primary action is reachable and wide on a phone.
        assert button.bounding_box()["width"] >= viewport_width * 0.7
    shot(page, "pay-page", full_page=True)
