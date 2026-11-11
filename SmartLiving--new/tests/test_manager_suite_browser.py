import os
from pathlib import Path
from threading import Thread

import pytest
from flask import redirect, session
from werkzeug.serving import make_server

from test_manager_suite_access import manager_suite_app
from test_login_performance import login_app
from bson import ObjectId
from services.manager_suite_scope import scoped_db

playwright = pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('viewport', [{'width': 1440, 'height': 900}, {'width': 390, 'height': 844}])
def test_suit_opens_hr_and_customer_support_in_new_tabs_with_manager_session(manager_suite_app, viewport):
    app, _, _, _, manager, *_ = manager_suite_app

    @app.get('/browser/signin-manager')
    def signin():
        session.update(user_id=str(manager), manager_id=str(manager), role='manager')
        return redirect('/manager')

    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            context = browser.new_context(viewport=viewport)
            context.route('https://**', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/browser/signin-manager')
            page.locator('#menuToggle').click()
            toggle = page.locator('[aria-controls=managerSuiteMenu]')
            playwright.expect(toggle).to_have_attribute('aria-expanded', 'true')
            menu = page.locator('#managerSuiteMenu')
            for name in ('HR', 'Customer Support'):
                link = menu.get_by_role('link', name=name, exact=True)
                playwright.expect(link).to_be_in_viewport()
            # A manager can collapse Suit; their preference survives reloads.
            toggle.click()
            playwright.expect(toggle).to_have_attribute('aria-expanded', 'false')
            page.reload()
            page.locator('#menuToggle').click()
            playwright.expect(toggle).to_have_attribute('aria-expanded', 'false')
            toggle.click()
            playwright.expect(toggle).to_have_attribute('aria-expanded', 'true')
            page.locator('#sidebarSearch').fill('HR')
            page.locator('#sidebarSearchClear').click()
            playwright.expect(toggle).to_have_attribute('aria-expanded', 'true')
            with page.expect_popup() as popup:
                menu.get_by_role('link', name='HR', exact=True).click()
            hr = popup.value
            playwright.expect(hr).to_have_url(base + '/hr/dashboard')
            playwright.expect(hr.get_by_role('heading', name='HR Control Center')).to_be_visible()
            playwright.expect(hr.get_by_text('Branch: Accra', exact=True)).to_be_visible()
            with page.expect_popup() as popup:
                menu.get_by_role('link', name='Customer Support', exact=True).click()
            support = popup.value
            playwright.expect(support).to_have_url(base + '/customer-support/app')
            playwright.expect(support.get_by_role('heading', name='Customer Support Command Center')).to_be_visible(timeout=20000)
            profile = support.request.get(base + '/api/customer-support/me').json()
            assert profile['profile']['user_id'] == str(manager)
            assert profile['profile']['branch'] == 'Accra'
            customers = support.request.get(base + '/api/customer-support/customers').json()
            assert [row['name'] for row in customers['customers']] == ['Own Customer']
            page.reload()
            playwright.expect(page.get_by_role('heading', name='Manager Dashboard')).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


def test_main_hr_sees_manager_badges_and_can_filter_activity_in_browser(manager_suite_app):
    app, _, database, _, manager, other_manager, *_ = manager_suite_app
    hr_id = ObjectId()
    database.users.insert_one({'_id': hr_id, 'username': 'central-hr', 'name': 'Central HR', 'role': 'hr'})

    @app.get('/browser/hr-audit/signin/<role>')
    def audit_signin(role):
        key = hr_id if role == 'hr' else manager if role == 'manager' else other_manager
        actual_role = 'hr' if role == 'hr' else 'manager'
        session.clear()
        session.update(user_id=str(key), role=actual_role, **{actual_role + '_id': str(key)})
        return redirect('/hr/activity-logs' if role == 'hr' else '/manager')

    @app.post('/hr/browser/create-reminder')
    def create_reminder():
        scoped_db(database).hr_reminders.insert_one({'title': 'Branch training record'})
        return {'ok': True}

    @app.get('/hr/browser/record-card')
    def record_card():
        from flask import render_template_string
        return render_template_string("{% from 'hr_pages/partials/hr_record_badge.html' import record_badge %}<h1>Employee record</h1>{{ record_badge(record) }}", record=database.hr_reminders.find_one({}))

    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel='msedge')
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.route('https://**', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for role in ('manager', 'other_manager'):
                page.goto(base + '/browser/hr-audit/signin/' + role)
                assert page.request.post(base + '/hr/browser/create-reminder').status == 200
            page.goto(base + '/browser/hr-audit/signin/hr')
            playwright.expect(page.get_by_role('heading', name='Activity logs', exact=True)).to_be_visible()
            playwright.expect(page.locator('tbody tr')).to_have_count(2)
            playwright.expect(page.locator('tbody').get_by_text('Manager', exact=True)).to_have_count(2)
            page.get_by_label('Manager', exact=True).select_option(str(manager))
            page.get_by_role('button', name='Apply filters').click()
            playwright.expect(page.locator('tbody tr')).to_have_count(1)
            playwright.expect(page.locator('tbody')).to_contain_text('Accra Manager')
            playwright.expect(page.locator('tbody')).not_to_contain_text('Kumasi Manager')
            page.goto(base + '/hr/browser/record-card')
            playwright.expect(page.get_by_text('Recorded by Manager · Accra Manager', exact=True)).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
