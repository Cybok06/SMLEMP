from datetime import datetime, timedelta
from threading import Thread

import pytest
from flask import jsonify, send_from_directory, session
from werkzeug.serving import make_server

from test_customer_support_targets import flow
from test_delivery_followups import delivery_flow
from test_customer_support_targets_ui import DIST, playwright


@pytest.mark.parametrize("repeat_interest", ["no", "yes"])
@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="Build the frontend first")
def test_delivery_issue_and_confirmation_browser_workflow(delivery_flow, tmp_path, repeat_interest):
    client, database, _, officer, _, _, task_id = delivery_flow
    from bson import ObjectId
    agent = ObjectId()
    database.users.insert_one({"_id": agent, "role": "agent", "name": "Sales Agent"})
    app = client.application

    @app.get("/_test/login")
    def test_login():
        session.update(role="customer_support", user_id=officer)
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

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    errors = []
    try:
        with playwright.sync_playwright() as p:
            import os
            from pathlib import Path
            channel = os.environ.get("PLAYWRIGHT_CHANNEL") or ("msedge" if Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe").exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/_test/login")
            page.goto(base + "/customer-support/app/#deliveries")
            page.get_by_role("button", name="Follow up", exact=True).first.click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("Item received", exact=False).select_option("partial")
            dialog.get_by_label("Customer satisfaction", exact=False).select_option("issue")
            dialog.get_by_role("radio", name="2 stars", exact=True).click()
            dialog.get_by_label("Outstanding issue", exact=False).fill("Power cable is missing")
            dialog.get_by_label("Follow-up notes", exact=False).fill("Customer received the fridge without its cable; arranging replacement.")
            dialog.get_by_label("Next follow-up date", exact=False).fill((datetime.utcnow()+timedelta(days=1)).date().isoformat())
            dialog.get_by_role("button", name="Save follow-up", exact=True).click()
            playwright.expect(dialog).to_have_count(0)
            playwright.expect(page.get_by_text("Issue / follow-up open", exact=True)).to_be_visible()
            page.screenshot(path=str(tmp_path / "delivery-issue.png"), full_page=True)
            page.goto(base + "/customer-support/app/#tasks")
            page.get_by_role("button", name="In Progress", exact=True).click()
            dialog = page.get_by_role("dialog")
            playwright.expect(dialog.get_by_role("heading", name="Review history", exact=True)).to_be_visible()
            dialog.get_by_label("Item received", exact=False).select_option("full")
            dialog.get_by_label("Customer satisfaction", exact=False).select_option("satisfied")
            dialog.get_by_role("radio", name="5 stars", exact=True).click()
            dialog.get_by_label("Outstanding issue", exact=False).fill("")
            dialog.get_by_label("Follow-up notes", exact=False).fill("Replacement cable received. Customer confirms full receipt and satisfaction.")
            dialog.get_by_label("Does the customer need another item?", exact=False).select_option(repeat_interest)
            if repeat_interest == "yes":
                dialog.get_by_label("Requested product", exact=False).fill("Gas cooker")
                dialog.get_by_placeholder("Search agent by name or branch").fill("Sales")
                dialog.get_by_role("button", name="Sales Agent", exact=False).click()
                dialog.get_by_label("Agent follow-up date", exact=False).fill((datetime.utcnow()+timedelta(days=2)).date().isoformat())
            dialog.get_by_role("button", name="Confirm & complete", exact=True).click()
            playwright.expect(dialog).to_have_count(0)
            playwright.expect(page.get_by_role("button", name="Completed", exact=True)).to_be_visible()
            page.goto(base + "/customer-support/app/#deliveries")
            playwright.expect(page.get_by_text("Receipt confirmed", exact=True)).to_be_visible()
            playwright.expect(page.get_by_label("5 out of 5 stars").first).to_be_visible()
            page.get_by_label("Follow-up status", exact=True).select_option("completed")
            playwright.expect(page.get_by_text("Fully received", exact=True)).to_be_visible()
            page.screenshot(path=str(tmp_path / "delivery-confirmed.png"), full_page=True)
            page.goto(base + "/customer-support/app/#satisfaction")
            playwright.expect(page.get_by_text("5 / 5", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            playwright.expect(page.locator("aside")).to_have_css("width", "68px")
            page.screenshot(path=str(tmp_path / "delivery-satisfaction-mobile.png"), full_page=True)
            assert not errors, errors
            assert database.tasks.find_one({"kind": "delivery_confirmation"})["review_version"] == 2
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
