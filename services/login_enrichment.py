"""Durable optional enrichment: the login audit itself is the queue/outbox."""
from datetime import datetime, timedelta
from ipaddress import ip_address
from uuid import uuid4
import requests
from pymongo import ReturnDocument


def enrich_pending(collection, limit=100, lookup=None):
    """Bounded worker; expired leases retry after crashes, terminal failures are visible."""
    def locate(ip):
        if not ip_address(ip).is_global:
            return {}
        response = requests.get(f'http://ip-api.com/json/{ip}', timeout=(2, 5))
        response.raise_for_status()
        data = response.json()
        if data.get('status') != 'success':
            raise ValueError('Location provider did not return success')
        return dict(country=data.get('country'), region=data.get('regionName'), city=data.get('city'), isp=data.get('isp'))

    lookup = lookup or locate
    result = {'processed': 0, 'completed': 0, 'retry': 0, 'failed': 0}
    for _ in range(max(0, min(int(limit), 1000))):
        now, token = datetime.utcnow(), uuid4().hex
        row = collection.find_one_and_update(
            {'ip_enrichment.state': {'$in': ['pending', 'retry', 'running']},
             'ip_enrichment.next_attempt': {'$lte': now}},
            {'$set': {'ip_enrichment.state': 'running', 'ip_enrichment.lease': token,
                      'ip_enrichment.next_attempt': now + timedelta(minutes=5)},
             '$inc': {'ip_enrichment.attempts': 1}},
            sort=[('ip_enrichment.next_attempt', 1)], return_document=ReturnDocument.AFTER)
        if not row:
            break
        result['processed'] += 1
        try:
            values = {'ip_location': lookup(row.get('ip') or ''), 'ip_enrichment.state': 'complete',
                      'ip_enrichment.completed_at': datetime.utcnow()}
            result['completed'] += 1
        except Exception as error:
            attempts = row['ip_enrichment']['attempts']
            state = 'failed' if attempts >= 5 else 'retry'
            values = {'ip_enrichment.state': state, 'ip_enrichment.error_type': type(error).__name__,
                      'ip_enrichment.next_attempt': now + timedelta(minutes=min(60, 2 ** attempts))}
            result[state] += 1
        collection.update_one({'_id': row['_id'], 'ip_enrichment.lease': token},
                              {'$set': values, '$unset': {'ip_enrichment.lease': ''}})
    return result
