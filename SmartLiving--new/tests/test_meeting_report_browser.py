from pathlib import Path
from threading import Thread
from datetime import datetime

import pytest
from werkzeug.serving import make_server

from test_meeting_report_breakdowns import report_app, seed_portfolio

playwright = pytest.importorskip('playwright.sync_api')


def test_report_displays_collection_and_current_customer_breakdowns(report_app):
    app, _, database, _, manager, agent, *_ = report_app
    customer, *_ = seed_portfolio(database, agent)
    stamp = datetime(2026, 10, 21, 12)
    database.packages.insert_one({'customer_id': customer, 'product_index': 0, 'status': 'delivered', 'delivered_at': stamp})
    database.customer_support_calls.insert_one({'officer_id': str(manager), 'type': 'Outbound', 'started_at': stamp})
    database.customer_support_field_visits.insert_one({'officer_id': str(manager), 'started_at': stamp})
    database.customer_support_manual_activities.insert_one({'created_by': str(manager), 'category': 'Interaction', 'occurred_at': stamp})
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel='msedge')
            context = browser.new_context(viewport={'width': 1440, 'height': 1050})
            # Keep this offline UI test independent of the external chart/CDN
            # services; the actual Flask APIs and report script run unchanged.
            context.route('https://**', lambda route: route.abort())
            context.add_init_script('window.Chart = class { constructor() {} destroy() {} update() {} };')
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/test/signin/manager')
            page.add_style_tag(content='.d-none {display:none!important;} .row{display:flex;flex-wrap:wrap;gap:16px}.col-md-3{width:22%}.card{border:1px solid #ddd;padding:12px;margin-bottom:12px}.small{font-size:13px}.d-flex{display:flex;justify-content:space-between}.border-top{border-top:1px solid #ddd;padding-top:8px}table{width:100%}h5{font-size:24px;margin:8px 0}')
            page.locator('#agentSelect').select_option(str(agent))
            page.locator('#filterYear').select_option('2026')
            page.locator('#filterMonth').select_option('10')
            page.locator('button[onclick="loadSelectedAgentMetrics()"]' ).click()
            for selector, value in [('#tileSales', '50.00'), ('#tileSalesSusu', '20.00'),
                ('#tileSalesProducts', '30.00'), ('#tileAttendance', '2 / 5'),
                ('#tileAttendanceRate', '40.0%'), ('#tileCustomersActive', '2'), ('#tileCustomersCompleted', '3')]:
                playwright.expect(page.locator(selector)).to_have_text(value)
            playwright.expect(page.locator('#tileDeliveriesDone')).to_have_text('1')
            playwright.expect(page.locator('[data-manager-support]').first).to_be_hidden()
            page.locator('label[for="scopeManager"]').click()
            page.locator('button[onclick="loadSelectedAgentMetrics()"]' ).click()
            for selector in ('#tileManagerCalls', '#tileManagerVisits', '#tileManagerInteractions', '#tileDeliveriesDone'):
                playwright.expect(page.locator(selector)).to_have_text('1')
            playwright.expect(page.locator('[data-manager-support]').first).to_be_visible()
            playwright.expect(page.locator('#tileSalesSusu')).to_have_text('20.00')
            page.locator('label[for="scopeAgent"]').click()
            page.locator('#agentSelect').select_option(str(agent))
            page.locator('#filterMonth').select_option('9')
            playwright.expect(page.locator('#tileSales')).to_have_text('100.00')
            playwright.expect(page.locator('#tileSalesSusu')).to_have_text('0.00')
            playwright.expect(page.locator('#tileSalesProducts')).to_have_text('100.00')
            playwright.expect(page.locator('#tileCustomersActive')).to_have_text('2')
            playwright.expect(page.locator('#tileCustomersCompleted')).to_have_text('3')
            playwright.expect(page.locator('#tileDeliveriesDone')).to_have_text('0')
            page.locator('#filterMonth').select_option('10')
            playwright.expect(page.locator('#tileSales')).to_have_text('50.00')
            screenshot = Path(__file__).resolve().parents[1] / '.test-tools/meeting-report-breakdowns.png'
            screenshot.parent.mkdir(exist_ok=True)
            page.locator('#reportContent').screenshot(path=str(screenshot))
            page.goto(base + '/meeting-report/agent-performance')
            page.locator('#managerFilter').select_option(str(manager))
            page.locator('#filterYear').select_option('2026')
            page.locator('#filterMonth').select_option('10')
            page.locator('button[onclick="loadTeamMetrics()"]' ).click()
            playwright.expect(page.locator('#sumSalesSusu')).to_have_text('20.00')
            playwright.expect(page.locator('#sumSalesProducts')).to_have_text('30.00')
            playwright.expect(page.locator('#sumDeliveriesDone')).to_have_text('1')
            playwright.expect(page.locator('#teamBody')).to_contain_text('Active: 2')
            playwright.expect(page.locator('#teamBody')).to_contain_text('Completed: 3')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
