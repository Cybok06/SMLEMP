"""Collection categories and current customer progress for meeting reports."""
from collections import defaultdict
from math import isfinite
from datetime import datetime, timedelta, timezone

from bson import ObjectId


def manager_support_metrics(database, manager_id, start, end):
    """Personal work recorded by this manager, using Support's event dates."""
    values = id_values([manager_id])
    window = {'$gte': start, '$lt': datetime.combine(end.date() + timedelta(days=1), datetime.min.time())}
    return {
        'calls': database.customer_support_calls.count_documents({
            'officer_id': {'$in': values}, 'enrichment_status': {'$ne': 'needs_update'},
            'type': {'$nin': ['Missed', 'missed']}, '$or': [
                {'started_at': window}, {'started_at': None, 'created_at': window}]}),
        'field_visits': database.customer_support_field_visits.count_documents({
            'officer_id': {'$in': values}, 'started_at': window}),
        'interactions': database.customer_support_manual_activities.count_documents({
            'created_by': {'$in': values}, 'category': 'Interaction', 'occurred_at': window}),
    }


def deliveries_by_agent(database, agent_ids, start, end):
    """A delivered product card counts once, using its delivery completion date.

    Ownership follows the same customer/agent mapping as the agent Deliveries
    page. A later pending/cancelled submission supersedes a legacy duplicate.
    """
    customers = list(database.customers.find({'agent_id': {'$in': id_values(agent_ids)}}, {'agent_id': 1}))
    owners = {str(row['_id']): str(row['agent_id']) for row in customers}
    counts = defaultdict(int)
    if not customers:
        return counts
    rows = database.packages.find({'customer_id': {'$in': id_values(row['_id'] for row in customers)}},
        {'customer_id': 1, 'product_index': 1, 'status': 1, 'created_at': 1, 'delivered_at': 1,
         'delivery_evidence': 1, 'status_history': 1}).sort([('created_at', -1), ('_id', -1)])
    seen = set()
    for row in rows:
        try:
            index = int(row.get('product_index'))
        except (ValueError, TypeError):
            index = str(row['_id'])
        key = (str(row['customer_id']), index)
        if key in seen:
            continue
        seen.add(key)
        if str(row.get('status') or '').strip().lower() != 'delivered':
            continue
        stamp = row.get('delivered_at') or (row.get('delivery_evidence') or {}).get('captured_at')
        if not stamp:
            stamp = next((event.get('at') for event in reversed(row.get('status_history') or []) if event.get('to') == 'delivered'), None)
        if not isinstance(stamp, datetime):
            try:
                stamp = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            except (ValueError, TypeError):
                continue
        if stamp.tzinfo:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        if start.date() <= stamp.date() <= end.date():
            counts[owners[str(row['customer_id'])]] += 1
    return counts


def payment_category(payment):
    kind = str(payment.get('payment_type') or '').strip().upper()
    if kind in {'SUSU', 'SUS'} or (not kind and payment.get('is_susu') is True):
        return 'susu'
    return 'products'


def number(value, default=0.0):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (ValueError, TypeError):
        return default


def id_values(values):
    variants = set()
    for value in values:
        variants.add(value)
        variants.add(str(value))
        if ObjectId.is_valid(str(value)):
            variants.add(ObjectId(str(value)))
    return list(variants)


def customer_breakdowns(customers_col, payments_col, customer_query):
    """Classify the current portfolio, independently of the attendance period.

    All-time customer/card payments follow the customer through agent transfers.
    SUSU participants remain Active even when they have no unpaid product card.
    This matches the SUSU pages' membership rule (contributions or a saved rate).
    """
    customers = list(customers_col.find(customer_query, {'agent_id': 1, 'purchases': 1, 'susu_default_rate': 1}))
    result = defaultdict(lambda: {'active': 0, 'completed': 0})
    if not customers:
        return result
    customer_ids = id_values(customer['_id'] for customer in customers)
    ledger = payments_col.aggregate([
        {'$match': {'customer_id': {'$in': customer_ids}}},
        {'$group': {'_id': {'customer_id': '$customer_id', 'product_index': {'$ifNull': ['$product_index', None]},
                          'payment_type': {'$ifNull': ['$payment_type', '']}, 'is_susu': {'$ifNull': ['$is_susu', False]}},
                    'amount': {'$sum': '$amount'}}},
    ])
    paid = defaultdict(float)
    susu_customers = set()
    for entry in ledger:
        key = entry['_id']
        customer_id = str(key['customer_id'])
        kind = str(key['payment_type']).strip().upper()
        amount = number(entry['amount'])
        if payment_category(key) == 'susu':
            if amount > 0:
                susu_customers.add(customer_id)
            continue
        try:
            index = int(key['product_index'])
        except (ValueError, TypeError):
            continue
        # SUSU withdrawals have no product_index. Product withdrawals/reversals
        # reduce the amount paid on that particular card.
        paid[(customer_id, index)] += -amount if kind == 'WITHDRAWAL' else amount

    terminal = {'closed', 'cancelled', 'canceled', 'refunded', 'completed', 'complete', 'paid', 'fully paid', 'inactive', 'transferred out'}
    for customer in customers:
        customer_id = str(customer['_id'])
        active = customer_id in susu_customers or number(customer.get('susu_default_rate')) > 0
        for index, purchase in enumerate(customer.get('purchases') or []):
            if active:
                break
            if not isinstance(purchase, dict):
                continue
            product = purchase.get('product') or {}
            if not isinstance(product, dict) or not product:
                continue
            statuses = [str(value or '').strip().lower().replace('_', ' ').replace('-', ' ')
                        for value in (product.get('status') or purchase.get('status'), product.get('transfer_status'), purchase.get('transfer_status'))]
            if any(status in terminal for status in statuses):
                continue
            total = number(product.get('total'), None)
            if total is None:
                price = number(product.get('price'), None)
                if price is not None:
                    total = price * number(product.get('quantity'), 1)
            # Missing legacy pricing must not mark an open card as completed.
            if total is None or round(total - paid[(customer_id, index)], 2) > 0:
                active = True
        result[str(customer.get('agent_id') or '')]['active' if active else 'completed'] += 1
    return result
