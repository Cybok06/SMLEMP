import io
import os
from pathlib import Path
from threading import Thread

import pytest
from flask import jsonify, redirect, request, session
from PIL import Image
from werkzeug.serving import make_server

from test_agent_deliveries import deliveries_flow

playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def deliveries_browser(deliveries_flow):
    client, database, _, submit, agent, _, customer, _ = deliveries_flow
    submit(0, 'pending', days_ago=100)
    submit(1, 'packaging')
    submit(2, 'delivering')
    app = client.application
    uploads = []
    state = {'fail': False}

    @app.get('/browser-signin')
    def signin():
        session.update(user_id=str(agent), role='agent')
        return redirect('/agent/deliveries')

    def deliver(customer_id, product_index):
        photo = request.files.get('delivery_photo')
        if not photo:
            return jsonify(ok=False, message='A camera photo is required.'), 400
        raw = photo.read()
        uploads.append(raw)
        if state['fail']:
            return jsonify(ok=False, message='Photo upload failed. Please retry.'), 502
        with Image.open(io.BytesIO(raw)) as image:
            assert image.format == 'JPEG'
        assert customer_id == str(customer)
        database.packages.update_one({'customer_id': customer, 'product_index': product_index}, {'$set': {'status': 'delivered'}})
        return jsonify(ok=True, message='Product marked Delivered.')

    app.view_functions['completed_cards.completed_card_mark_delivered'] = deliver
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel, args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://**', lambda route: route.abort())
            page.route('**/api/activations/alerts', lambda route: route.fulfill(json={'ok': True, 'pendingCount': 0}))
            page.route('**/fixture/agent_tasks.pending_task_count', lambda route: route.fulfill(json={'count': 0}))
            page.goto(f'http://127.0.0.1:{server.server_port}/browser-signin')
            yield page, uploads, state
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


def test_deliveries_page_camera_confirmation_updates_queue_and_sidebar(deliveries_browser):
    page, uploads, _ = deliveries_browser
    playwright.expect(page.get_by_role('heading', name='Deliveries', exact=True)).to_be_visible()
    playwright.expect(page.locator('[data-undelivered-total]')).to_have_text('3')
    page.get_by_role('button', name='Open sidebar', exact=True).click()
    link = page.locator('[data-deliveries-link]')
    playwright.expect(link).to_contain_text('Deliveries')
    playwright.expect(link.locator('#agentDeliverySidebarBadge')).to_have_text('3')
    page.get_by_role('button', name='Close sidebar').click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    row = page.locator('[data-delivery-row]').filter(has_text='Product 1')
    row.get_by_role('button', name='Mark Delivered').click()
    dialog = page.get_by_role('dialog', name='Delivery camera')
    playwright.expect(dialog).to_be_visible()
    playwright.expect(dialog.locator('input[type=file]')).to_have_count(0)
    assert not uploads
    dialog.get_by_role('button', name='Take photo', exact=True).click()
    dialog.get_by_role('button', name='Use photo & mark delivered').click()
    playwright.expect(page.locator('[data-undelivered-total]')).to_have_text('2')
    playwright.expect(page.locator('#agentDeliverySidebarBadge')).to_have_text('2')
    playwright.expect(page.locator('[data-delivery-row]')).to_have_count(2)
    assert len(uploads) == 1
    page.locator('select[name=status]').select_option('delivered')
    page.get_by_role('button', name='Apply filter').click()
    playwright.expect(page.locator('[data-delivery-row]')).to_have_count(1)
    playwright.expect(page.locator('[data-delivery-status]')).to_have_text('Delivered')
    playwright.expect(page.locator('[data-undelivered-total]')).to_have_text('2')


def test_cancelled_photo_and_failed_upload_do_not_reduce_count(deliveries_browser):
    page, uploads, state = deliveries_browser
    page.evaluate('window.DeliveryCamera.capture = async () => null')
    page.get_by_role('button', name='Mark Delivered').first.click()
    playwright.expect(page.get_by_role('button', name='Mark Delivered').first).to_be_enabled()
    assert not uploads
    # Isolate the page's upload-error handling; the other browser test uses a real synthetic camera.
    page.evaluate("window.DeliveryCamera.capture = async () => new Blob(['snapshot'], {type:'image/jpeg'})")
    state['fail'] = True
    page.get_by_role('button', name='Mark Delivered').first.click()
    playwright.expect(page.locator('[data-delivery-feedback]')).to_contain_text('Photo upload failed')
    playwright.expect(page.locator('[data-undelivered-total]')).to_have_text('3')
    playwright.expect(page.locator('#agentDeliverySidebarBadge')).to_have_text('3')
    playwright.expect(page.get_by_role('button', name='Mark Delivered')).to_have_count(2)
