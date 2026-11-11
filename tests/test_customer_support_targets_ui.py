"""Browser workflow checks against isolated MongoDB fixtures and the built UI."""
from pathlib import Path
import os
from threading import Thread

import pytest
from flask import jsonify, send_from_directory, session
from werkzeug.serving import make_server

from test_customer_support_targets import flow, visit_payload

playwright = pytest.importorskip("playwright.sync_api")
DIST = Path(__file__).resolve().parents[1] / "Frontend" / "Customer_Support" / "dist"


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="Build the customer support frontend first")
@pytest.mark.parametrize("management_role", ["executive", "admin"])
def test_executive_and_support_workflow_in_browser(flow, tmp_path, management_role):
    client, database, _, _, officer, _, customer = flow
    app = client.application

    @app.get("/_test/login/<role>")
    def test_login(role):
        session.update(role=role, user_id=officer)
        return "ok"

    @app.get("/customer-support/app/")
    def spa():
        return send_from_directory(DIST, "index.html")

    @app.get("/customer-support/app/<path:filename>")
    def assets(filename):
        return send_from_directory(DIST, filename)

    @app.get("/api/customer-support/me")
    def identity():
        return jsonify(ok=True, profile={"name": "Officer", "avatar_initials": "OF", "role": "customer_support", "branch": "HQ"})

    @app.get("/api/customer-support/customers")
    def customers():
        row = {"id": customer, "name": "Customer", "phone": "0241234567", "email": "", "branch": "HQ", "agent": "Agent", "agentInitials": "AG", "products": [], "productCount": 0, "balance": 0, "balanceFormatted": "GHS 0.00", "status": "Active", "segment": "Standard", "joinDate": "2026-01-01", "lastInteraction": "None", "lastInteractionRaw": "", "tickets": 0, "csat": 0, "accountType": "Standard", "city": "Accra", "ic": "", "dob": "", "gender": ""}
        return jsonify(ok=True, customers=[row], stats={"total_customers": 1, "active_count": 1, "inactive_count": 0, "overdue_count": 0, "followup_count": 0}, filters={"branches": [], "agents": [], "statuses": []}, pagination={"page": 1, "per_page": 20, "total": 1, "total_pages": 1, "has_prev": False, "has_next": False})

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    errors = []
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get("PLAYWRIGHT_CHANNEL") or ("msedge" if Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe").exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + f"/_test/login/{management_role}")
            page.goto(base + f"/{management_role}/customer-support")
            page.locator("#officer").select_option(officer)
            page.locator("#metric").select_option("interactions")
            page.locator("#period").select_option("per_trip")
            page.locator("#amount").fill("70")
            page.get_by_role("button", name="Save target").click()
            playwright.expect(page.locator("#message")).to_have_text("Target saved successfully.")
            playwright.expect(page.locator("#progress")).to_contain_text("70 interactions required per trip")
            page.locator("#metric").select_option("field_trips")
            playwright.expect(page.locator("#period option")).to_have_count(1)
            page.locator("#amount").fill("2")
            page.get_by_role("button", name="Save target").click()
            playwright.expect(page.locator("#history")).to_contain_text("Field trips")
            playwright.expect(page.get_by_role("region", name="Recorded performance")).to_be_visible()
            playwright.expect(page.get_by_text("Resulting purchases", exact=True)).to_be_visible()
            page.locator("#performance-period").select_option("daily")
            page.screenshot(path=str(tmp_path / "executive-targets.png"), full_page=True)
            page.goto(base + "/_test/login/customer_support")
            page.goto(base + "/customer-support/app/#tasks")
            page.get_by_role("button", name="Visit to field", exact=True).click()
            form = page.locator("form").filter(has=page.get_by_role("button", name="Log visit", exact=True))
            payload = visit_payload()
            form.get_by_label("Location visited").fill(payload["location"])
            form.get_by_label("Starting time").fill(payload["started_at"][:16])
            form.get_by_label("Ending time").fill(payload["ended_at"][:16])
            form.get_by_label("Visit notes").fill(payload["notes"])
            form.get_by_role("button", name="Log visit", exact=True).click()
            playwright.expect(page.get_by_role("cell", name="Madina market", exact=True)).to_be_visible()
            page.goto(base + "/customer-support/app/#customers")
            page.get_by_text("Customer", exact=True).first.click()
            page.get_by_role("button", name="Activities").click()
            page.get_by_role("button", name="Log Activity", exact=True).click()
            page.get_by_label("Category", exact=False).select_option("Interaction")
            visit_id = str(database.customer_support_field_visits.find_one()["_id"])
            page.get_by_label("Field visit", exact=False).select_option(visit_id)
            page.get_by_label("Title", exact=False).fill("Field conversation")
            page.get_by_label("Branch *", exact=True).fill("HQ")
            page.get_by_label("Customer feedback *", exact=True).fill("Customer is satisfied")
            page.get_by_label("Action taken *", exact=True).fill("Confirmed delivery")
            page.get_by_label("Next follow-up / no follow-up reason", exact=True).fill("No follow-up needed")
            page.get_by_label("Description", exact=False).fill("Discussed customer delivery during visit")
            page.get_by_role("button", name="Log Activity", exact=True).last.click()
            playwright.expect(page.get_by_role("heading", name="Field conversation", exact=True)).to_be_visible()
            assert database.customer_support_manual_activities.count_documents({"field_visit_id": database.customer_support_field_visits.find_one()["_id"]}) == 1
            page.goto(base + "/customer-support/app/#calls")
            page.get_by_role("button", name="Log Outgoing Call", exact=True).click()
            page.get_by_placeholder("Type customer name or phone...").fill("Customer")
            page.get_by_role("dialog", name="Call log").get_by_role("button", name="Customer", exact=False).click()
            page.get_by_placeholder("Brief description of call reason...").fill("Delivery confirmation")
            page.get_by_role("button", name="Continue", exact=False).click()
            page.get_by_label("Branch *", exact=True).fill("HQ")
            page.get_by_label("Customer feedback *", exact=True).fill("Satisfied with delivery")
            page.get_by_label("Action taken *", exact=True).fill("Confirmed receipt")
            page.get_by_label("Next follow-up / no follow-up reason", exact=True).fill("No further follow-up required")
            page.get_by_role("button", name="Save Call Log", exact=True).click()
            playwright.expect(page.get_by_role("dialog", name="Call log")).to_have_count(0)
            assert database.customer_support_calls.find_one()["feedback"] == "Satisfied with delivery"
            page.goto(base + "/customer-support/app/#customers")
            page.get_by_text("Customer", exact=True).first.click()
            page.get_by_role("button", name="Activities").click()
            playwright.expect(page.get_by_text("Performed by Officer", exact=False).first).to_be_visible()
            page.screenshot(path=str(tmp_path / "interaction-timeline.png"), full_page=True)
            page.goto(base + "/customer-support/app/#targets")
            playwright.expect(page.get_by_role("main").get_by_role("heading", name="My Targets", exact=True)).to_be_visible()
            playwright.expect(page.get_by_role("cell", name="70 · 69 remaining", exact=True)).to_be_visible()
            page.screenshot(path=str(tmp_path / "support-targets.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            playwright.expect(page.locator("aside")).to_have_css("width", "68px")
            page.screenshot(path=str(tmp_path / "support-targets-mobile.png"), full_page=True)
            page.set_viewport_size({"width":1440,"height":1000})
            page.goto(base + "/customer-support/app/#dashboard")
            playwright.expect(page.get_by_role("region", name="My target progress")).to_be_visible()
            playwright.expect(page.get_by_role("region", name="My target progress").get_by_text("0 / 1", exact=False)).to_be_visible()
            playwright.expect(page.get_by_role("region", name="Recorded performance")).to_be_visible()
            page.get_by_label("Performance period", exact=True).select_option("daily")
            page.screenshot(path=str(tmp_path / "dashboard-personal-performance.png"), full_page=True)

            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
