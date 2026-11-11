import os
import re
from pathlib import Path
from threading import Thread

import pytest
from flask import redirect, session
from werkzeug.serving import make_server

from test_login_performance import login_app
from test_manager_suite_access import manager_suite_app
from test_support_manager_audit import log_call, add_task

playwright = pytest.importorskip('playwright.sync_api')


def test_support_badges_activity_filters_and_manager_access_in_real_frontend(manager_suite_app, monkeypatch):
    app, client, _, signin, manager, other_manager, _, _, officer, customer, other_customer = manager_suite_app
    # The in-memory database cannot evaluate Mongo's string-normalization
    # aggregation operators. Use the service's fixture path, keeping the real
    # query adapter, permissions, serializers and React application in use.
    from services import support_task_queries
    from services.manager_suite_scope import SuiteCollection
    original = support_task_queries._is_fixture_collection
    monkeypatch.setattr(support_task_queries, '_is_fixture_collection',
        lambda collection: original(collection.raw if isinstance(collection, SuiteCollection) else collection))

    @app.get('/browser/support-audit/signin/<role>')
    def login(role):
        key = manager if role == 'manager' else officer
        actual_role = 'manager' if role == 'manager' else 'customer_support'
        session.clear()
        session.update(user_id=str(key), role=actual_role, **{actual_role + '_id': str(key)})
        return redirect('/customer-support/app#calls')

    assert log_call(client, customer).status_code == 201
    assert add_task(client, customer).status_code == 201
    signin('manager', other_manager)
    assert log_call(client, other_customer).status_code == 201
    assert add_task(client, other_customer).status_code == 201

    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.route('https://**', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/browser/support-audit/signin/support')
            playwright.expect(page.get_by_text('Recorded by Manager · Accra Manager', exact=True)).to_be_visible()
            playwright.expect(page.get_by_text('Recorded by Manager · Kumasi Manager', exact=True)).to_be_visible()
            page.get_by_role('button', name=re.compile(r'^Tasks\b')).click()
            playwright.expect(page.get_by_text('Recorded by Manager · Accra Manager', exact=True)).to_be_visible()
            playwright.expect(page.get_by_text('Recorded by Manager · Kumasi Manager', exact=True)).to_be_visible()
            page.get_by_role('button', name='Activity logs', exact=True).click()
            playwright.expect(page.get_by_role('heading', name='Activity logs', exact=True)).to_be_visible()
            playwright.expect(page.locator('tbody tr')).to_have_count(4)
            page.get_by_label('Manager', exact=True).select_option(str(manager))
            page.get_by_label('Record type', exact=True).select_option('calls')
            page.get_by_role('button', name='Apply filters', exact=True).click()
            playwright.expect(page.locator('tbody tr')).to_have_count(1)
            playwright.expect(page.locator('tbody')).to_contain_text('Accra Manager')
            playwright.expect(page.locator('tbody')).not_to_contain_text('Kumasi Manager')
            screenshot = Path(__file__).resolve().parents[1] / '.test-tools/support-activity-logs.png'
            screenshot.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            page.goto(base + '/browser/support-audit/signin/manager')
            playwright.expect(page.get_by_role('button', name='Activity logs', exact=True)).to_have_count(0)
            page.goto(base + '/customer-support/app#activitylogs')
            playwright.expect(page.get_by_text('Activity logs are available to the main Customer Support team.', exact=True)).to_be_visible()
            assert page.request.get(base + '/api/customer-support/activity-logs').status == 403
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
