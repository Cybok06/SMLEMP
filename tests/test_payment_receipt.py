"""Receipt tests use a fake database and never connect to production MongoDB."""
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId


@pytest.fixture
def receipt_module():
    fake_db = types.ModuleType('db')
    fake_db.db = MagicMock()
    spec = importlib.util.spec_from_file_location(
        'receipt_payment_under_test', Path(__file__).resolve().parents[1] / 'payment.py'
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake_db}):
        spec.loader.exec_module(module)
    module.customers_collection = MagicMock()
    module.users_collection = MagicMock()
    module.customers_collection.find_one.return_value = {'name': 'Ama', 'phone_number': '0240000000'}
    module.users_collection.find_one.return_value = {'name': 'Receipt Agent'}
    return module


@pytest.mark.parametrize('amount,identifier,display', [
    (20, 'SM1707202620', '20.00'),
    (20.50, 'SM1707202620.5', '20.50'),
    (0.05, 'SM170720260.05', '0.05'),
])
def test_receipt_uses_saved_payment_date_amount_and_recording_agent(receipt_module, amount, identifier, display):
    agent_id = ObjectId()
    receipt = receipt_module._payment_receipt({
        'customer_id': ObjectId(), 'recorded_by_agent_id': str(agent_id),
        'agent_id': str(ObjectId()), 'date': '2026-07-17', 'amount': amount,
        'payment_type': 'SUSU', 'method': 'Cash',
    })
    assert receipt['payment_id'] == identifier
    assert receipt['amount'] == display
    assert receipt['date'] == '17/07/2026'
    assert receipt['customer'] == 'Ama'
    assert receipt['agent'] == 'Receipt Agent'
    assert receipt['description'] == 'SUSU'
    receipt_module.users_collection.find_one.assert_called_once_with({'_id': agent_id})


def test_pending_payment_has_no_receipt(receipt_module):
    assert receipt_module._payment_receipt(None) is None
    receipt_module.customers_collection.find_one.assert_not_called()


def test_short_receipt_print_layout():
    from playwright.sync_api import sync_playwright

    source = (Path(__file__).resolve().parents[1] / 'templates/add_payment.html').read_text(encoding='utf-8')
    handler = source.split("  printReceiptBtn.addEventListener('click', () => {", 1)[1]
    handler = handler.split("  document.getElementById('addAnotherBtn')", 1)[0]
    handler = "printReceiptBtn.addEventListener('click', () => {" + handler
    # Suppress the native print dialog while exercising the actual receipt builder.
    handler = handler.replace('receiptFrame.contentWindow.print();', 'receiptFrame.contentWindow.__printed = true;')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page()
        page.set_content('<button id="printReceiptBtn">Print</button>')
        page.evaluate("""() => {
            window.printReceiptBtn = document.getElementById('printReceiptBtn');
            window.savedReceipt = {customer: '<script>unsafe</script>', phone: '0240000000',
                description: 'SUSU', method: 'Cash', agent: 'Receipt Agent', date: '17/07/2026',
                payment_id: 'SM1707202620', amount: '20.00'};
        }""")
        page.evaluate(handler)
        page.get_by_role('button', name='Print').click()
        receipt = page.frames[1]
        assert receipt.locator('#customer').inner_text() == '<script>unsafe</script>'
        assert receipt.locator('script').count() == 0
        assert receipt.locator('header').inner_text().endswith('0556064611 / 05099639836 / 0598948132')
        assert receipt.locator('#payment_id').inner_text() == 'SM1707202620'
        assert receipt.evaluate("getComputedStyle(document.querySelector('hr')).borderTopStyle") == 'dashed'
        assert receipt.evaluate("document.querySelector('.amount').nextElementSibling.tagName") == 'HR'
        assert receipt.locator('#agent').bounding_box()['y'] < receipt.locator('#amount').bounding_box()['y']
        assert receipt.locator('body').bounding_box()['width'] < 220
        assert receipt.evaluate('window.__printed') is True
        browser.close()
