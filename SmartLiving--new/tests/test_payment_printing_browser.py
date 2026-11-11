from pathlib import Path
from threading import Thread
import os

import pytest
from werkzeug.serving import make_server
from test_payment_printing import printer_app, save_payment

playwright = pytest.importorskip('playwright.sync_api')


def test_receipt_connect_print_once_cancel_and_partial_failure(printer_app, tmp_path):
    app, client, db, ids, customer, access, _ = printer_app
    receipt = save_payment(printer_app)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    errors = []
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('''
              window.printerWrites=0; window.printerBytes=0; window.failWrite=false; window.cancelChooser=false;
              const port={writable:null, async open(){this.writable={getWriter:()=>({
                async write(bytes){window.printerWrites++;if(window.failWrite)throw new Error('Simulated disconnect');window.printerBytes+=bytes.length;},
                releaseLock(){}
              })};}};
              Object.defineProperty(navigator,'serial',{value:{getPorts:async()=>[],requestPort:async()=>{
                if(window.cancelChooser)throw new DOMException('Cancelled','NotFoundError');return port;
              },addEventListener(){}}});
            ''')
            page.goto(base+'/_signin/agent')
            page.goto(base+'/_printer')
            page.wait_for_function('Boolean(window.PaymentPrinter)')
            page.evaluate('r=>window.PaymentPrinter.open(r)', receipt)
            playwright.expect(page.locator('#rCustomerName')).to_have_text('Ama <unsafe>')
            assert page.locator('#paymentReceiptFormat').input_value() == 'short'
            assert 'short-receipt' in page.locator('#thermalReceipt').get_attribute('class')
            page.locator('#paymentReceiptFormat').select_option('full')
            assert 'short-receipt' not in page.locator('#thermalReceipt').get_attribute('class')
            playwright.expect(page.locator('#rTotalPaid')).to_be_visible()
            page.locator('#paymentReceiptFormat').select_option('short')
            playwright.expect(page.locator('#rTotalPaid')).not_to_be_visible()
            assert page.locator('#paymentReceiptPreview script').count() == 0
            playwright.expect(page.locator('#receiptConnectAndPrint')).to_be_visible()
            # HTML error pages must never be parsed as receipt JSON or send bytes.
            page.route('**/payment-receipts/*/claim', lambda route: route.fulfill(
                status=502, content_type='text/html', body='<!doctype html><h1>Bad gateway</h1>'))
            page.locator('#receiptConnectAndPrint').click()
            playwright.expect(page.locator('#receiptConnectionPrompt')).to_contain_text('unexpected response (502)')
            assert page.evaluate('window.printerBytes') == 0
            assert db.payments.find_one({})['print_status'] == 'pending'
            page.unroute('**/payment-receipts/*/claim')
            acknowledgements = []
            def lose_first_ack(route):
                acknowledgements.append(True)
                if len(acknowledgements) == 1:
                    route.fetch()  # Server saves the result; simulate a lost response.
                    route.abort()
                else:
                    route.continue_()
            page.route('**/payment-receipts/*/finish', lose_first_ack)
            page.locator('#receiptConnectAndPrint').click()
            playwright.expect(page.locator('#receiptPrintState')).to_have_text('Receipt printed successfully', timeout=15000)
            assert db.payments.find_one({})['printed'] is True
            assert page.evaluate('window.printerBytes') > 1000
            assert page.locator('#receiptConnectAndPrint').is_hidden()
            assert len(acknowledgements) >= 2
            paper = page.locator('#paymentMiniReceipt').bounding_box()
            bar = page.locator('#receiptPrintProgress').bounding_box()
            assert paper['y'] + paper['height'] <= bar['y']
            writes = page.evaluate('window.printerWrites')
            page.screenshot(path=str(tmp_path/'payment-receipt-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            # Printing one format must leave the other selectable and printable once.
            assert page.locator('#paymentReceiptFormat').is_enabled()
            page.locator('#paymentReceiptFormat').select_option('full')
            assert page.locator('#receiptConnectAndPrint').is_visible()
            page.locator('#receiptConnectAndPrint').click()
            playwright.expect(page.locator('#receiptPrintState')).to_have_text('Receipt printed successfully')
            record = db.payments.find_one({})
            assert record['receipt_prints']['short']['printed'] is True
            assert record['receipt_prints']['full']['printed'] is True
            assert record['receipt_prints']['full']['printed_at'] >= record['receipt_prints']['short']['printed_at']
            assert page.evaluate('window.printerWrites') > writes
            writes = page.evaluate('window.printerWrites')
            assert page.locator('#receiptPrintDetail').count() == 0
            assert page.locator('#receiptPrintBytes').count() == 0
            assert 'All receipt data sent.' not in page.locator('#receiptPrinterModal').inner_text()
            page.locator('#paymentReceiptFormat').select_option('short')
            assert page.locator('#receiptConnectAndPrint').is_hidden()
            page.locator('#receiptPrintDone').click()
            playwright.expect(page.locator('#receiptPrinterModal')).not_to_be_visible()
            page.evaluate('r=>window.PaymentPrinter.open(r)', receipt)
            # A stale receipt in another tab cannot dispatch again.
            playwright.expect(page.locator('#receiptConnectionPrompt')).to_contain_text('already claimed', timeout=10000)
            assert page.evaluate('window.printerWrites') == writes
            page.locator('#receiptPrintDone').click()
            playwright.expect(page.locator('#receiptPrinterModal')).not_to_be_visible()
            # A new page has no connection, so cancellation leaves the saved payment unprinted.
            page.reload(); page.wait_for_function('Boolean(window.PaymentPrinter)')
            result = client.post('/payment/add_payment', data={'customer_id': str(customer), 'amount': '12', 'is_susu': 'yes', 'force': 'yes'}, headers={'X-Requested-With': 'XMLHttpRequest'})
            second = result.json['receipt']
            page.evaluate('window.cancelChooser=true')
            page.evaluate('r=>window.PaymentPrinter.open(r)', second)
            page.locator('#receiptConnectAndPrint').click()
            playwright.expect(page.locator('#receiptConnectionPrompt')).to_contain_text('selection cancelled')
            assert page.locator('#receiptPrintSkip').count() == 0
            assert db.payments.find_one({'amount': 12})['print_status'] == 'pending'
            # A transport error after claiming locks the receipt, with printed=false.
            page.evaluate('window.cancelChooser=false;window.failWrite=true')
            page.locator('#receiptConnectAndPrint').click()
            playwright.expect(page.locator('#receiptPrintState')).to_have_text('Print result uncertain')
            assert db.payments.find_one({'amount': 12})['printed'] is False
            assert db.payments.find_one({'amount': 12})['print_status'] == 'uncertain'
            assert page.locator('#receiptConnectAndPrint').is_hidden()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


def test_add_payment_opens_receipt_instead_of_success_modal(printer_app):
    app, _, db, ids, _, access, _ = printer_app
    access.set_access('branch', 'Accra', True, str(ids['executive']))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with playwright.sync_playwright() as p:
            channel = os.environ.get('PLAYWRIGHT_CHANNEL') or ('msedge' if Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists() else None)
            browser = p.chromium.launch(headless=True, channel=channel)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base+'/_signin/agent')
            page.goto(base+'/payment/add_payment')
            page.wait_for_function('Boolean(window.PaymentPrinter) && Boolean(window.jQuery)')
            page.locator('#toggleSusuBtn').click()
            page.locator('input[name="amount"]').fill('30')
            page.locator('#submitBtn').click()
            page.get_by_role('button', name='Yes, Submit').click()
            playwright.expect(page.locator('#receiptPrinterModal')).to_be_visible()
            playwright.expect(page.locator('#successModal')).not_to_be_visible()
            assert db.payments.count_documents({}) == 1
            assert db.payments.find_one({})['printed'] is False
            assert page.locator('#receiptPrintSkip').count() == 0
            assert db.payments.find_one({})['print_status'] == 'pending'
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
