"""Run after building Frontend/Inventory_V2; APIs are isolated from production."""
import os
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, send_from_directory
from werkzeug.serving import make_server

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


def test_inventory_role_navigation_and_session_isolation():
    app = Flask(__name__)

    @app.get('/inventory/app/')
    @app.get('/inventory/app/<path:filename>')
    def frontend(filename='index.html'):
        return send_from_directory(ROOT / 'Frontend/Inventory_V2/dist', filename)

    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    pages = ['dashboard', 'inventory', 'submitted-cards', 'reports', 'product-cards', 'warehouse', 'audit']
    role = {'id': 'inventory-user', 'name': 'Inventory User', 'permissions': {
        page: {action: page in pages[:3] for action in ['visible', 'view', 'edit']}
        for page in pages
    }}
    state = {'role': role, 'fail': False}
    errors = []
    requests = []

    def api(route):
        path = route.request.url.split(base)[-1]
        requests.append(path)
        if path == '/api/inventory/session':
            if state['fail']:
                return route.fulfill(status=503, json={'ok': False})
            return route.fulfill(json={'ok': True, 'user': {'id': 'test', 'name': 'Test User'},
                                      'effective_role_id': state['role']['id'], 'roles': [state['role']]})
        if path == '/api/inventory/dashboard':
            return route.fulfill(json={'ok': True, 'metrics': {'customers90Plus': 7}})
        return route.fulfill(json={'ok': True, 'products': [], 'counts': {}})

    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://**', lambda route: route.abort())
            page.route('**/api/**', api)
            page.route('**/attendance/**', lambda route: route.fulfill(json={'ok': True, 'summary': {}}))
            page.goto(base + '/inventory/app/')
            playwright.expect(page.get_by_role('heading', name='Dashboard', exact=True)).to_be_visible()
            playwright.expect(page.get_by_role('button', name='View Reports', exact=True)).to_be_disabled()
            playwright.expect(page.get_by_role('button', name='Create Request', exact=True)).to_be_disabled()
            page.locator('nav button').filter(has_text='Inventory').click()
            playwright.expect(page.get_by_role('button', name='Stock Taking', exact=True)).to_have_count(0)
            page.get_by_role('button', name='Dashboard', exact=True).click()
            page.evaluate("localStorage.setItem('inventory_v2_dashboard_cache_v1', JSON.stringify({savedAt:Date.now(),payload:{ok:true,metrics:{customers90Plus:999}}}))")
            page.reload()
            playwright.expect(page.get_by_text('999', exact=True)).to_have_count(0)
            state['role'] = {'id': 'admin', 'name': 'Main Admin', 'permissions': {
                key: {action: True for action in ['visible', 'view', 'create', 'edit']} for key in pages}}
            page.reload()
            playwright.expect(page.get_by_role('button', name='View Reports', exact=True)).to_be_enabled()
            page.locator('nav button').filter(has_text='Inventory').click()
            playwright.expect(page.get_by_role('button', name='Stock Taking', exact=True)).to_be_visible()
            state['fail'] = True
            requests.clear()
            page.reload()
            playwright.expect(page.get_by_role('button', name='Retry', exact=True)).to_be_visible()
            assert '/api/inventory/dashboard' not in requests
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
