"""One atomic dispatch per receipt format. Claims never automatically expire."""
from datetime import datetime
import secrets
from pymongo import ReturnDocument
from db import db
from services.payment_receipt_tracking import FORMATS, receipt_payment_id


def owner_query(payment_id, agent_id):
    return {'_id': payment_id, 'recorded_by_agent_id': str(agent_id)}


def claim_print(payment_id, agent_id, receipt_format='short'):
    if receipt_format not in FORMATS:
        return None
    path = 'receipt_prints.' + receipt_format
    query = dict(owner_query(payment_id, agent_id), printer_feature_enabled=True)
    query['$or'] = [
        {path + '.status': {'$in': ['pending', 'skipped', 'failed']}},
        {path: {'$exists': False}, 'receipt_prints': {'$exists': True}},
        {'receipt_prints': {'$exists': False}, 'printed': False,
         'print_status': {'$in': ['pending', 'skipped', 'failed']}},
    ]
    now = datetime.utcnow()
    record = {'status': 'printing', 'printed': False,
              'token': secrets.token_urlsafe(32), 'started_at': now,
              'printed_at': None, 'print_delay_seconds': None}
    return db.payments.find_one_and_update(
        query, {'$set': {path: record, 'print_status': 'printing', 'print_started_at': now}},
        return_document=ReturnDocument.AFTER)


def _sync_first_print(payment):
    completed = [r for r in (payment.get('receipt_prints') or {}).values() if r.get('printed_at')]
    if completed:
        first = min(completed, key=lambda r: r['printed_at'])
        db.payments.update_one({'_id': payment['_id'], '$or': [
            {'printed_at': None}, {'printed_at': {'$gt': first['printed_at']}}]}, {'$set': {
            'printed_at': first['printed_at'], 'print_delay_seconds': first['print_delay_seconds']}})
    return db.payments.find_one({'_id': payment['_id']})


def finish_print(payment_id, agent_id, token, outcome, receipt_format='short'):
    if receipt_format not in FORMATS:
        return None
    path = 'receipt_prints.' + receipt_format
    query = dict(owner_query(payment_id, agent_id), **{path + '.token': token})
    payment = db.payments.find_one(query)
    if not payment:
        # Acknowledge an outstanding dispatch started by the older release.
        legacy = dict(owner_query(payment_id, agent_id), print_token=token, receipt_prints={'$exists': False})
        payment = db.payments.find_one(legacy)
        if not payment:
            return None
        if payment.get('printed'):
            return payment
        query = legacy
        status_path = 'print_status'
    else:
        if payment['receipt_prints'][receipt_format].get('printed'):
            return _sync_first_print(payment)
        status_path = path + '.status'
    now = datetime.utcnow()
    changes = {status_path: outcome, 'print_status': outcome, 'print_finished_at': now,
               'payment_id': receipt_payment_id(payment)}
    if outcome == 'printed':
        delay = max(0, (now - payment['payment_recorded_at']).total_seconds())
        changes.update(printed=True, print_transport='web-serial-bluetooth')
        if status_path == 'print_status':
            changes.update(printed_at=now, print_delay_seconds=delay)
        else:
            changes.update({path + '.printed': True, path + '.printed_at': now,
                            path + '.print_delay_seconds': delay})
    query[status_path] = {'$in': ['printing', 'uncertain']}
    result = db.payments.find_one_and_update(query, {'$set': changes}, return_document=ReturnDocument.AFTER)
    if not result:
        result = db.payments.find_one(query if status_path == 'print_status' else {
            **owner_query(payment_id, agent_id), path + '.token': token, path + '.printed': True})
    return _sync_first_print(result) if result else None
