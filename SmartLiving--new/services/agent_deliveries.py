"""Agent delivery queue and badge share the same ownership and counting rules."""
from datetime import datetime

from bson import ObjectId

from db import db


FILTERS = {'undelivered', 'pending', 'packaging', 'delivering', 'delivered', 'all'}
LABELS = {'pending': 'Submitted', 'packaging': 'Packaging', 'delivering': 'Delivering', 'delivered': 'Delivered'}


def _scope_pipeline(agent_id):
    agent_id = str(agent_id or '')
    identifiers = [agent_id]
    if ObjectId.is_valid(agent_id):
        identifiers.append(ObjectId(agent_id))
    return [
        {'$match': {'agent_id': {'$in': identifiers}}},
        {'$project': {'name': 1, 'phone_number': 1}},
        {'$lookup': {'from': 'packages', 'localField': '_id', 'foreignField': 'customer_id', 'as': 'package'}},
        {'$unwind': '$package'},
        {'$addFields': {'status_code': {'$toLower': {'$ifNull': ['$package.status', 'pending']}}}},
        {'$match': {'status_code': {'$nin': ['cancelled', 'canceled', 'closed']}}},
        {'$sort': {'package.created_at': -1, 'package._id': -1}},
        # A product card is one delivery, even if a legacy submission was duplicated.
        {'$group': {'_id': {'customer': '$_id', 'product': '$package.product_index'}, 'row': {'$first': '$$ROOT'}}},
        {'$replaceRoot': {'newRoot': '$row'}},
        {'$addFields': {'status_code': {'$cond': [{'$eq': ['$status_code', 'packaged']}, 'packaging', '$status_code']}}},
    ]


def _status_match(status):
    if status == 'all':
        return []
    value = {'$ne': 'delivered'} if status == 'undelivered' else status
    return [{'$match': {'status_code': value}}]


def undelivered_count(agent_id):
    result = list(db.customers.aggregate(_scope_pipeline(agent_id) + _status_match('undelivered') + [{'$count': 'count'}]))
    return int(result[0]['count']) if result else 0


def delivery_page(agent_id, status='undelivered', page=1, per_page=30):
    status = status if status in FILTERS else 'undelivered'
    pipeline = _scope_pipeline(agent_id) + [{'$facet': {
        'rows': _status_match(status) + [
            {'$sort': {'package.created_at': -1, 'package._id': -1}},
            {'$skip': (page - 1) * per_page}, {'$limit': per_page},
            {'$project': {'name': 1, 'phone_number': 1, 'status_code': 1,
                          'package.product': 1, 'package.product_name': 1, 'package.product_index': 1,
                          'package.created_at': 1, 'package.submitted_at': 1, 'package.delivery_evidence': 1}},
        ],
        'total': _status_match(status) + [{'$count': 'count'}],
        'undelivered': _status_match('undelivered') + [{'$count': 'count'}],
    }}]
    result = next(iter(db.customers.aggregate(pipeline)), {})
    rows = []
    for row in result.get('rows', []):
        package = row['package']
        product = package.get('product') or {}
        try:
            index = int(package.get('product_index'))
        except (TypeError, ValueError):
            index = -1
        submitted = package.get('created_at') or package.get('submitted_at')
        if isinstance(submitted, str):
            try:
                submitted = datetime.fromisoformat(submitted.replace('Z', '+00:00'))
            except ValueError:
                submitted = None
        rows.append({
            'customer_id': str(row['_id']), 'customer_name': row.get('name') or 'Customer',
            'customer_phone': row.get('phone_number') or '',
            'product_name': product.get('name') or package.get('product_name') or 'Product card',
            'product_index': index, 'submitted_at': submitted,
            'status': row['status_code'], 'status_label': LABELS.get(row['status_code'], 'Awaiting delivery'),
            'can_deliver': index >= 0 and row['status_code'] in {'packaging', 'delivering'},
            'evidence': package.get('delivery_evidence') or {},
        })
    total = int((result.get('total') or [{}])[0].get('count', 0))
    count = int((result.get('undelivered') or [{}])[0].get('count', 0))
    return {'rows': rows, 'total': total, 'undelivered_count': count,
            'status': status, 'page': page, 'total_pages': max(1, (total + per_page - 1) // per_page)}
