from datetime import datetime, timedelta
from bson import ObjectId
from test_customer_support_targets import flow


def test_customer_support_payment_history_contains_public_receipt_audit(flow):
    client, db, _, _, _, _, customer = flow
    recorded = datetime(2026, 10, 28, 10, 0)
    printed = recorded + timedelta(seconds=8)
    db.payments.insert_one({
        'customer_id': customer, 'amount': 25, 'date': '2026-10-28', 'created_at': recorded,
        'payment_recorded_at': recorded, 'payment_type': 'PRODUCT', 'printed': True,
        'printed_at': printed, 'print_delay_seconds': 8, 'print_status': 'printed',
        'receipt_prints': {'short': {'status': 'printed', 'printed': True,
            'printed_at': printed, 'print_delay_seconds': 8, 'token': 'PRIVATE-TOKEN'}}})
    response = client.get(f'/api/customer-support/customers/{customer}/activity')
    assert response.status_code == 200
    receipt = response.json['payments'][0]
    assert receipt['printed'] is True
    assert receipt['printed_at'] == '2026-10-28T10:00:08Z'
    assert receipt['payment_recorded_at'] == '2026-10-28T10:00:00Z'
    assert receipt['print_delay_seconds'] == 8
    assert receipt['payment_id'] == 'SM2810202625'
    assert receipt['receipt_prints']['short']['printed'] is True
    assert receipt['receipt_prints']['full']['printed'] is False
    assert 'PRIVATE-TOKEN' not in response.text
