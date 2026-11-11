"""Permanent per-account snapshots for employee exit handovers.

Only acknowledgement, HR confirmation and transfer evidence may change after
capture. Financial and issue figures are never recomputed for historic records.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from bson import ObjectId
from pymongo.errors import DuplicateKeyError


def oid(value):
    try: return ObjectId(str(value))
    except Exception: return None


def ownership_query(user_id):
    values=[str(user_id),oid(user_id)]
    return {'$or':[{'agent_id':{'$in':values}}, {'$and':[{'agent_id':{'$in':[None,'']}},
        {'$or':[{field:{'$in':values}} for field in ('assigned_agent_id','assigned_to','assigned_to_id','user_id')]}]}]}


def money(value):
    try:
        amount = Decimal(str(value or 0))
        if not amount.is_finite(): raise ValueError()
        return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError('An account has an invalid financial amount. Correct it before handover.')


def account_snapshot(database, customer, session=None):
    options = {"session":session} if session is not None else {}
    customer_id = customer['_id']; values = [customer_id, str(customer_id)]
    paid = {}
    for payment in database.payments.find({'customer_id': {'$in': values}}, **options):
        index = payment.get('product_index')
        if index is None: continue
        try: index = int(index)
        except (TypeError, ValueError): continue
        kind = str(payment.get('payment_type') or 'PRODUCT').upper()
        if kind not in {'PRODUCT', 'WITHDRAWAL'}: continue
        amount = money(payment.get('amount'))
        paid[index] = paid.get(index, Decimal(0)) + (amount if kind == 'PRODUCT' else -amount)
    packages = {}
    for package in database.packages.find({'customer_id': {'$in': values}}, **options).sort('_id', -1):
        try: packages.setdefault(int(package.get('product_index')), package)
        except (TypeError, ValueError): pass
    followups = {task.get('product_index'): task for task in database.tasks.find({'customer_id': {'$in': values}, 'kind':'delivery_confirmation'}, **options)}
    cards = []; pending = []; debt = Decimal(0)
    for index, purchase in enumerate(customer.get('purchases') or []):
        product = purchase.get('product') or {}; package = packages.get(index) or {}; review = (followups.get(index) or {}).get('latest_review') or {}
        statuses = {str(value or '').lower() for value in [purchase.get('status'), product.get('status'), product.get('packaging_status'), package.get('status')]}
        inactive = bool(statuses & {'closed', 'cancelled', 'canceled', 'withdrawn'})
        total = money(product.get('total')) if product.get('total') is not None else money(money(product.get('price')) * money(product.get('quantity') or 1))
        if total < 0: raise ValueError('An order total cannot be negative at handover.')
        net = money(paid.get(index)); balance = max(total-net, Decimal(0)) if not inactive else Decimal(0)
        delivered = 'delivered' in statuses or bool(purchase.get('delivered_at') or product.get('delivered_at') or package.get('delivered_at'))
        if review.get('receipt') == 'full' and review.get('contacted') is True: delivered = True
        elif review.get('receipt') in {'partial', 'none'}: delivered = False
        stage = 'Closed/cancelled' if inactive else 'Delivered' if delivered else 'Partly received' if review.get('receipt') == 'partial' else 'Pending'
        card = {'product_index':index, 'product_name':product.get('name') or 'Product', 'quantity':product.get('quantity') or 1,
            'order_value':float(total), 'net_payments':float(net), 'outstanding_balance':float(balance), 'delivery_status':stage,
            'package_id':str(package.get('_id') or ''), 'product_status':product.get('status') or purchase.get('status') or ''}
        cards.append(card); debt += balance
        if not inactive and not delivered: pending.append(dict(card))
    issues = []
    for complaint in database.complaints.find({'customer_id': {'$in': values}, 'status': {'$nin':['Resolved','Closed','resolved','closed']}}, **options):
        issues.append({'id':str(complaint['_id']), 'source':'Complaint', 'reference':complaint.get('ticket_no') or str(complaint['_id']),
            'status':complaint.get('status') or 'Open', 'description':complaint.get('subject') or complaint.get('description') or 'Unresolved customer issue',
            'assigned_to':complaint.get('assigned_to_name') or ''})
    for task in followups.values():
        review = task.get('latest_review') or {}
        if review.get('issue') and str(task.get('status') or '').lower() != 'completed':
            issues.append({'id':str(task['_id']), 'source':'Delivery review', 'reference':task.get('task_no') or str(task['_id']),
                'status':task.get('status') or 'Open', 'description':review['issue'], 'assigned_to':task.get('assignee_name') or ''})
    return {'customer_id':str(customer_id), 'customer_name':customer.get('name') or 'Customer', 'customer_phone':customer.get('phone_number') or '',
        'branch':customer.get('agent_branch') or customer.get('branch') or '', 'cards':cards, 'customer_debt':float(money(debt)),
        'pending_deliveries':pending, 'pending_delivery_count':len(pending), 'unresolved_issues':issues, 'unresolved_issue_count':len(issues),
        'basis':'ERP product card totals minus recorded PRODUCT payments plus WITHDRAWAL reversals; closed/cancelled cards excluded from collectible debt.'}


def ensure_indexes(database):
    database.hr_customer_handovers.create_index([('exit_id',1),('customer_id',1)],unique=True)
    database.hr_customer_handovers.create_index([('from_user_id',1),('captured_at',-1)])
    database.hr_customer_handovers.create_index([('to_user_id',1),('captured_at',-1)])


def capture_handover(database, exit_doc, customer, receiver, actor, note='', session=None):
    options = {"session":session} if session is not None else {}
    existing = database.hr_customer_handovers.find_one({'exit_id':str(exit_doc['_id']),'customer_id':str(customer['_id'])}, **options)
    if existing:
        if existing['to_user_id'] != str(receiver['_id']): raise ValueError('This account already has a permanent handover to another salesperson.')
        return existing
    employee = exit_doc['employee']; now = datetime.utcnow()
    snapshot = account_snapshot(database,customer,session=session)
    record = {'_id':ObjectId(), 'exit_id':str(exit_doc['_id']), 'customer_id':str(customer['_id']),
        'from_user_id':employee['user_id'], 'from_name':employee.get('name') or 'Exiting salesperson',
        'to_user_id':str(receiver['_id']), 'to_name':receiver.get('name') or receiver.get('username') or 'Receiving salesperson',
        'captured_at':now, 'captured_by':actor, 'note':note, 'snapshot':snapshot,
        'acknowledgement':None, 'hr_confirmation':None, 'ownership_transferred_at':None, 'version':0}
    try: database.hr_customer_handovers.insert_one(record, **options)
    except DuplicateKeyError:
        if session is not None: raise ValueError('Another request captured this account. Refresh and retry.')
        return capture_handover(database,exit_doc,customer,receiver,actor,note,session=session)
    return record


def exit_handover_blocker(database, exit_doc):
    rows = list(database.hr_customer_handovers.find({'exit_id':str(exit_doc['_id'])}))
    required=set(exit_doc.get('handover_customer_ids') or [])
    captured={row['customer_id'] for row in rows}
    if required - captured:
        return 'Capture a permanent handover for every original customer account before clearance.'
    if exit_doc.get('handover_required') and len(rows) < int((exit_doc.get('transfers') or {}).get('total_customers') or 0):
        return 'Capture a permanent handover for every account before clearance.'
    if any(not row.get('acknowledgement') or not row.get('hr_confirmation') for row in rows):
        return 'Every handover requires receiving salesperson acknowledgement and HR confirmation.'
    return ''


def transfer_exit_accounts(database, exit_doc, match_filter, updates, receiver, actor):
    """Capture evidence and change ownership in one MongoDB transaction."""
    def work(session):
        options = {"session":session} if session is not None else {}
        customers = list(database.customers.find(match_filter, **options))
        records = [capture_handover(database,exit_doc,customer,receiver,actor,session=session) for customer in customers]
        now = datetime.utcnow()
        for customer in customers:
            changes = dict(updates["$set"])
            for field in ("assigned_agent_id", "assigned_to", "assigned_to_id", "user_id"):
                if str(customer.get(field) or '') == exit_doc['employee']['user_id']:
                    changes[field] = str(receiver['_id'])
            database.customers.update_one({'_id':customer['_id']},{'$set':changes}, **options)
        if records:
            database.hr_customer_handovers.update_many({'_id':{'$in':[row['_id'] for row in records]}},
                {'$set':{'ownership_transferred_at':now}}, **options)
            database.hr_exits.update_one({'_id':exit_doc['_id']},{'$set':{'handover_required':True}}, **options)
        return len(customers), [str(row['_id']) for row in records]
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    with database.client.start_session() as session:
        return session.with_transaction(work, read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority'))
