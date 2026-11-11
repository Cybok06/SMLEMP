"""Browser coverage for lazy printer-access listing and grant controls."""
import os
from pathlib import Path
from threading import Thread

import pytest
from bson import ObjectId
from werkzeug.serving import make_server
from test_payment_printing import printer_app

playwright = pytest.importorskip('playwright.sync_api')


def test_executive_printer_access_paging_search_and_grants(printer_app):
    app, _, db, ids, _, _, _ = printer_app
    db.users.insert_many([
        {'_id': ObjectId(), 'name': f'Printer Agent {i:03}', 'username': f'print{i:03}',
         'role': 'agent', 'branch': 'Accra', 'manager_id': ids['manager']}
        for i in range(45)
    ])
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as playwright_api:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or (
                'msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = playwright_api.chromium.launch(headless=True, channel=channel)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/_signin/executive')
            page.goto(base + '/executive/printer-access')
            playwright.expect(page.locator('#agentRows tr')).to_have_count(25)
            playwright.expect(page.locator('#loadMoreAgents')).to_be_visible()
            page.locator('#loadMoreAgents').click()
            playwright.expect(page.locator('#agentRows tr')).to_have_count(47)
            page.locator('#agentSearch').fill('Printer Agent 04')
            playwright.expect(page.locator('#agentRows tr')).to_have_count(5)
            page.locator('#agentRows tr').first.get_by_role('button', name='Grant').click()
            playwright.expect(page.locator('#printerAccessMessage')).to_have_text('Printer access updated.')
            playwright.expect(page.locator('#agentRows tr').first).to_contain_text('Individual grant')
            page.locator('#agentRows tr').first.get_by_role('button', name='Use branch setting').click()
            playwright.expect(page.locator('#agentRows tr').first).to_contain_text('Branch setting')
            page.locator('#branchSection summary').click()
            playwright.expect(page.locator('#branchRows tr')).to_have_count(1)
            page.locator('#branchRows tr').first.get_by_role('button', name='Grant').click()
            playwright.expect(page.locator('#branchRows tr').first).to_contain_text('Enabled')
            page.locator('#agentSearch').fill('')
            playwright.expect(page.locator('#agentRows tr')).to_have_count(25)
            playwright.expect(page.locator('#agentRows tr').first).to_contain_text('Enabled')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
