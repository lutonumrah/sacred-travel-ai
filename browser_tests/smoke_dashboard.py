"""Staff dashboard: login, styling, live inbox, pipeline board."""

import re

import pytest
from playwright.sync_api import expect

from conftest import APP_URL, WIDGET_KEY, unique, widget_chat

SIDEBAR_BG = "rgb(16, 35, 30)"  # --sidebar in static/css/app.css


def test_staff_login(page, shot):
    page.goto("/")
    page.wait_for_url(re.compile(r"/auth/login/"))
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    shot(page, "login-form")

    page.locator("#id_username").fill("admin")
    page.locator("#id_password").fill("wrong-password")
    page.get_by_role("button", name="Login").click()
    expect(page.locator(".form-error")).to_contain_text("did not match")

    page.locator("#id_username").fill("admin")
    page.locator("#id_password").fill("travel1234")
    page.get_by_role("button", name="Login").click()
    page.wait_for_url(re.compile(r"/dashboard/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Dashboard Overview")
    expect(page.locator(".sidebar-footer")).to_contain_text("admin")
    shot(page, "dashboard")


def test_dashboard_is_styled(page, login, shot, is_mobile):
    stylesheet = []
    page.on(
        "response",
        lambda r: stylesheet.append(r) if r.url.endswith("/static/css/app.css") else None,
    )
    login()
    assert stylesheet, "app.css was never requested"
    assert stylesheet[0].status in (200, 304)
    assert "text/css" in stylesheet[0].headers.get("content-type", "")

    sidebar = page.locator(".sidebar")
    expect(sidebar).to_have_css("background-color", SIDEBAR_BG)
    expect(sidebar).to_have_css("display", "flex")
    expect(page.locator(".brand-mark")).to_have_css("background-color", "rgb(15, 118, 110)")
    panel = page.locator(".panel").first
    expect(panel).to_have_css("background-color", "rgb(255, 255, 255)")
    expect(panel).to_have_css("border-top-left-radius", "14px")
    if is_mobile:
        # Below 900px the sidebar stops being a sticky full-height column.
        expect(sidebar).to_have_css("position", "static")
    else:
        expect(sidebar).to_have_css("position", "sticky")
        expect(page.locator(".app-shell")).to_have_css("display", "grid")
    shot(page, "styled", full_page=True)


def test_inbox_refreshes_with_new_widget_chat(page, login, api, shot):
    login()
    page.goto("/conversations/inbox/")
    live = page.locator("#inbox-live")
    expect(live).to_be_visible()
    # A marker that a full page load would wipe out.
    page.evaluate("window.__noReload = true")
    shot(page, "before")

    data = widget_chat(api, f"Hi, I'd like a hotel in Goa ({unique()})")
    session_prefix = data["session"][:10]
    expect(live).not_to_contain_text(session_prefix)

    # live.js polls every 10s; allow two cycles.
    expect(live.get_by_role("link", name=session_prefix)).to_be_visible(timeout=25_000)
    assert page.evaluate("window.__noReload === true"), "the inbox reloaded instead of refreshing in place"
    shot(page, "after-refresh")


def _intake_lead(api, name):
    response = api.post(
        "/api/v1/crm/intake/",
        data={"key": WIDGET_KEY, "name": name, "email": f"{unique()}@example.com", "destination": "Goa"},
    )
    assert response.status == 201, response.text()
    reference = response.json()["lead_reference"]
    return int(re.sub(r"\D", "", reference))


@pytest.mark.desktop_only(reason="HTML5 drag and drop needs a mouse; phones use the status menu")
def test_pipeline_drag_and_drop(page, login, api, shot):
    lead_id = _intake_lead(api, f"Drag {unique()}")
    login()
    page.goto("/crm/leads/pipeline/")
    card = page.locator(f'.kanban-card[data-lead="{lead_id}"]')
    new_col = page.locator('.kanban-col[data-status="new"]')
    target = page.locator('.kanban-col[data-status="interested"]')
    expect(new_col.locator(f'[data-lead="{lead_id}"]')).to_be_visible()
    count_before = int(target.locator("[data-count]").inner_text())
    shot(page, "before")

    with page.expect_response(re.compile(rf"/crm/leads/{lead_id}/move/")) as moved:
        card.drag_to(target.locator("[data-dropzone]"))
    assert moved.value.ok
    assert moved.value.json()["status"] == "interested"
    expect(target.locator(f'[data-lead="{lead_id}"]')).to_be_visible()
    expect(target.locator("[data-count]")).to_have_text(str(count_before + 1))
    expect(page.locator("#pipeline-status")).to_have_text("Moved to Interested.")
    shot(page, "dropped")

    page.reload()
    expect(target.locator(f'[data-lead="{lead_id}"]')).to_be_visible()
    expect(target.locator(f'[data-lead="{lead_id}"] select[name=status]')).to_have_value("interested")
    shot(page, "after-reload")


@pytest.mark.mobile_only(reason="the status menu is the touch-friendly way to move a card")
def test_pipeline_status_select(page, login, api, shot):
    lead_id = _intake_lead(api, f"Select {unique()}")
    login()
    page.goto("/crm/leads/pipeline/")
    card = page.locator(f'.kanban-card[data-lead="{lead_id}"]')
    expect(card).to_have_attribute("data-status", "new")
    card.scroll_into_view_if_needed()
    shot(page, "before")

    with page.expect_response(re.compile(rf"/crm/leads/{lead_id}/move/")) as moved:
        card.locator("select[name=status]").select_option("qualified")
    assert moved.value.ok
    target = page.locator('.kanban-col[data-status="qualified"]')
    expect(target.locator(f'[data-lead="{lead_id}"]')).to_be_visible()
    expect(page.locator("#pipeline-status")).to_have_text("Moved to Qualified.")
    shot(page, "moved")

    page.reload()
    expect(target.locator(f'[data-lead="{lead_id}"]')).to_have_attribute("data-status", "qualified")
    target.locator(f'[data-lead="{lead_id}"]').scroll_into_view_if_needed()
    shot(page, "after-reload")
