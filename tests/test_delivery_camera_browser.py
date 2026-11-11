"""Camera UI checks use a synthetic browser camera, never a real photo or Cloudflare."""
import os
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, Response, send_from_directory
from werkzeug.serving import make_server

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def camera_page():
    app = Flask(__name__)
    source = (ROOT / 'templates/customer_profile.html').read_text(encoding='utf-8')
    handler = source[source.index('  function bindDeliveryForms(scope)'):source.index('  function addManualRow')]

    @app.get('/')
    def index():
        return Response('''<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
          <div class="product-card"><span data-status-badge="1">Packaging</span>
          <form data-delivery-form="1" action="/deliver"><button type="submit">Mark Delivered</button></form></div>
          <p id="feedback" role="alert"></p>
          <script src="/static/js/delivery_camera.js"></script><script>
          function showPageAlert(message) { document.getElementById('feedback').textContent=message; }
          ''' + handler + '''bindDeliveryForms(document);</script>''', mimetype='text/html')

    @app.get('/static/js/delivery_camera.js')
    def script():
        return send_from_directory(ROOT / 'static/js', 'delivery_camera.js')

    @app.get('/cards')
    def cards():
        source = (ROOT / 'templates/completed_cards.html').read_text(encoding='utf-8')
        script = source.rsplit('<script>', 1)[1].split('</script>', 1)[0]
        return Response('''<!doctype html><button data-delivery-url="/deliver" data-product-name="Item">Mark Delivered</button>
            <div id="deliveryFeedback" role="alert"></div><script src="/static/js/delivery_camera.js"></script><script>'''
            + script + '</script>', mimetype='text/html')

    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel, args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            page = context.new_page()
            page.set_default_timeout(10000)
            page.add_init_script('''(() => {
              window.cameraDebug = [];
              const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
              navigator.mediaDevices.getUserMedia = async (...args) => {
                try { return await original(...args); }
                catch (error) { window.cameraDebug.push(error.name + ': ' + error.message); throw error; }
              };
              const play = HTMLMediaElement.prototype.play;
              HTMLMediaElement.prototype.play = async function() {
                try { return await play.call(this); }
                catch (error) { window.cameraDebug.push('play: ' + error.name + ': ' + error.message); throw error; }
              };
            })()''')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            yield page
            if page.locator('dialog').count():
                print('Camera dialog at test end:', page.locator('dialog').inner_text())
                print('Camera errors:', page.evaluate('window.cameraDebug'))
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


def test_camera_capture_retake_cancel_and_upload_retry(camera_page):
    page = camera_page
    posts = []

    def deliver(route):
        posts.append(route.request.post_data_buffer)
        if len(posts) == 1:
            return route.fulfill(status=502, json={'ok': False, 'message': 'Photo upload failed. Please retry.'})
        return route.fulfill(json={'ok': True, 'message': 'Product marked Delivered.'})

    page.route('**/deliver', deliver)
    page.get_by_role('button', name='Mark Delivered', exact=True).click()
    dialog = page.get_by_role('dialog', name='Delivery camera')
    playwright.expect(dialog).to_be_visible()
    bounds = dialog.bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    playwright.expect(dialog.locator('input[type=file]')).to_have_count(0)
    playwright.expect(dialog.get_by_role('button', name='Use photo & mark delivered')).to_be_hidden()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    assert not posts
    page.get_by_role('button', name='Mark Delivered', exact=True).click()
    dialog.get_by_role('button', name='Take photo', exact=True).click()
    playwright.expect(dialog.get_by_alt_text('Delivery photo preview')).to_be_visible()
    dialog.get_by_role('button', name='Retake photo', exact=True).click()
    playwright.expect(dialog.get_by_alt_text('Delivery photo preview')).to_be_hidden()
    dialog.get_by_role('button', name='Take photo', exact=True).click()
    dialog.get_by_role('button', name='Use photo & mark delivered').click()
    playwright.expect(page.get_by_role('alert')).to_contain_text('Photo upload failed')
    playwright.expect(page.locator('[data-status-badge]')).to_have_text('Packaging')
    page.get_by_role('button', name='Mark Delivered', exact=True).click()
    dialog.get_by_role('button', name='Take photo', exact=True).click()
    dialog.get_by_role('button', name='Use photo & mark delivered').click()
    playwright.expect(page.locator('[data-status-badge]')).to_have_text('Delivered')
    assert len(posts) == 2
    assert all(b'name="delivery_photo"' in post and b'Content-Type: image/jpeg' in post for post in posts)
    assert page.evaluate("document.querySelector('video') === null")


def test_camera_permission_denied_cannot_confirm(camera_page):
    page = camera_page
    page.evaluate("() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied', 'NotAllowedError'); }; }")
    posts = []
    page.route('**/deliver', lambda route: (posts.append(True), route.abort()))
    page.get_by_role('button', name='Mark Delivered', exact=True).click()
    dialog = page.get_by_role('dialog', name='Delivery camera')
    playwright.expect(dialog.get_by_role('status')).to_contain_text('Camera permission was denied')
    playwright.expect(dialog.get_by_role('button', name='Use photo & mark delivered')).to_be_hidden()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    playwright.expect(page.locator('[data-status-badge]')).to_have_text('Packaging')
    assert not posts


def test_completed_cards_also_require_camera_photo(camera_page):
    page = camera_page
    page.goto(page.url + 'cards')
    posts = []

    def deliver(route):
        posts.append(route.request.post_data_buffer)
        route.fulfill(json={'ok': True, 'message': 'Product marked delivered.'})

    page.route('**/deliver', deliver)
    page.get_by_role('button', name='Mark Delivered', exact=True).click()
    dialog = page.get_by_role('dialog', name='Delivery camera')
    assert not posts
    dialog.get_by_role('button', name='Take photo', exact=True).click()
    dialog.get_by_role('button', name='Use photo & mark delivered').click()
    playwright.expect(page.get_by_role('alert')).to_contain_text('Product marked delivered')
    assert len(posts) == 1 and b'name="delivery_photo"' in posts[0]
