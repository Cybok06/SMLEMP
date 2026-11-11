from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from bson import ObjectId
from datetime import datetime, timedelta
from db import db
from login import get_current_identity
from pymongo.errors import DuplicateKeyError
import re

undelivered_items_bp = Blueprint("undelivered_items", __name__, template_folder="templates")

customers_col = db["customers"]
users_col = db["users"]
inventory_col = db["inventory"]
undelivered_items_col = db["undelivered_items"]


def _oid(v):
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _session_actor():
    identity = get_current_identity()
    role = identity.get('role')
    if not identity.get('is_authenticated') or role not in {'agent', 'manager', 'admin', 'inventory', 'executive'}:
        return None, ''
    actor = users_col.find_one({'_id': _oid(identity.get('user_id')), 'role': role})
    return (actor, role) if actor else (None, '')


def _safe_json_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {k: _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json_value(v) for v in value]
    return value


def _due_flags(expected_date_str):
    if not expected_date_str:
        return False, False
    try:
        d = datetime.strptime(expected_date_str, "%Y-%m-%d").date()
    except Exception:
        return False, False
    today = datetime.utcnow().date()
    is_overdue = d < today
    is_due_soon = (d - today).days <= 2 and d >= today
    return is_due_soon, is_overdue


def _scoped_filter(base=None, branch=None, manager_id=None):
    base = dict(base or {})
    actor, role = _session_actor()
    scope_clauses = []

    if not actor:
        scope_clauses.append({"_id": {"$exists": False}})
    elif role == "agent":
        aid = str(actor["_id"])
        scope_clauses.append({"$or": [{"agent_id": aid}, {"agent_id": actor["_id"]}]})
    elif role == "manager":
        mid = actor["_id"]
        scope_clauses.append({"$or": [{"manager_id": mid}, {"manager_id": str(mid)}]})
    else:
        if role == "inventory":
            scope_clauses.append({"inventory_sent_at": {"$exists": True}})
        if manager_id:
            scope_clauses.append({"$or": [{"manager_id": manager_id}, {"manager_id": str(manager_id)}]})
        elif branch:
            scope_clauses.append({"manager_branch": branch})

    if scope_clauses:
        base = {"$and": [base] + scope_clauses}

    return base, actor, role


STATUSES = {'pending': 'Undelivered', 'preparing': 'Preparing', 'products_in': 'Products In', 'delivered': 'Delivered'}


def _variants(value):
    return [str(value), _oid(value)] if _oid(value) else [str(value)]


def _inventory_scope(manager_id):
    return {'$or': [{'manager_id': {'$in': _variants(manager_id)}}, {'manager_id': {'$in': [None, '']}}]}


def _customer_card(customer_id, index):
    actor, role = _session_actor()
    if role != 'agent':
        return None, None, actor, (jsonify(ok=False, message='Agent access required.'), 403)
    customer = customers_col.find_one({'_id': _oid(customer_id), 'agent_id': {'$in': _variants(actor['_id'])}})
    if not customer:
        return None, None, actor, (jsonify(ok=False, message='Customer not found.'), 404)
    purchases = customer.get('purchases') or []
    if index < 0 or index >= len(purchases):
        return None, None, actor, (jsonify(ok=False, message='Product card not found.'), 404)
    return customer, purchases[index], actor, None


def _quantity(value):
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]{0,3}', str(value)):
        raise ValueError('Use a whole quantity from 1 to 9999.')
    return int(value)


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'Enter a valid {label} (up to {limit} characters).')
    return value.strip()


@undelivered_items_bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(ok=False, message=str(error)), 400


@undelivered_items_bp.get('/customer/<customer_id>/undelivered/<int:product_index>/inventory')
def inventory_options(customer_id, product_index):
    customer, purchase, actor, error = _customer_card(customer_id, product_index)
    if error:return error
    if not actor.get('manager_id'):
        return jsonify(ok=False, message='Your account needs an assigned manager.'), 403
    filters = [_inventory_scope(actor['manager_id'])]
    term = request.args.get('q', '').strip()[:100]
    if term:filters.append({'name': {'$regex': re.escape(term), '$options': 'i'}})
    rows = inventory_col.find({'$and': filters}, {'name': 1}).sort('name', 1).limit(50)
    return jsonify(ok=True, products=[{'id': str(row['_id']), 'name': row.get('name') or 'Inventory item'} for row in rows])


@undelivered_items_bp.post('/customer/<customer_id>/undelivered/<int:product_index>')
def create_undelivered_record(customer_id, product_index):
    customer, purchase, actor, error = _customer_card(customer_id, product_index)
    if error:return error
    product = purchase.get('product') or {}
    if str(product.get('status') or '').lower() in {'delivered', 'closed', 'cancelled', 'canceled'} or product.get('transfer_status') == 'transferred_out':
        return jsonify(ok=False, message='This card is closed, delivered, or transferred.'), 409
    manager = users_col.find_one({'_id': {'$in': _variants(actor.get('manager_id'))}, 'role': 'manager'})
    if not manager:return jsonify(ok=False, message='Your account needs an assigned manager.'), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):raise ValueError('Enter valid missing products.')
    expected = _text(data.get('expected_delivery_date', ''), 'expected date', 10)
    if expected:
        try:datetime.strptime(expected, '%Y-%m-%d')
        except ValueError:raise ValueError('Enter a valid expected delivery date.')
    note = _text(data.get('note', ''), 'note', 2000)
    selected, manual = data.get('components', []), data.get('manual_items', [])
    if not isinstance(selected, list) or not isinstance(manual, list) or not 1 <= len(selected) + len(manual) <= 50:
        raise ValueError('Select or add at least one product (maximum 50).')
    items, used = [], set()
    for row in selected:
        if not isinstance(row, dict):raise ValueError('Invalid inventory product.')
        key = _oid(row.get('inventory_id'))
        inventory = inventory_col.find_one({'$and': [{'_id': key}, _inventory_scope(manager['_id'])]}) if key else None
        if not inventory:raise ValueError('Choose an inventory product available to your branch.')
        if str(key) in used:raise ValueError('Select each inventory product once.')
        used.add(str(key))
        quantity = _quantity(row.get('undelivered_qty', 1))
        items.append({'inventory_id': key, 'name': inventory.get('name') or 'Inventory item', 'undelivered_qty': quantity, 'required_qty': quantity, 'source': 'inventory'})
    for row in manual:
        if not isinstance(row, dict):raise ValueError('Invalid manual product.')
        quantity = _quantity(row.get('undelivered_qty', 1))
        items.append({'inventory_id': None, 'name': _text(row.get('name', ''), 'product name', 200, True), 'undelivered_qty': quantity, 'required_qty': quantity, 'source': 'manual'})
    existing = undelivered_items_col.find_one({'customer_id': customer['_id'], 'product_index': product_index, 'status': {'$ne': 'delivered'}})
    if existing:
        return jsonify(ok=False, message='This card already has an open record. View it on Undelivered products.'), 409
    now = datetime.utcnow()
    doc = {'created_at': now, 'updated_at': now, 'status': 'pending', 'version': 0,
        'customer_id': customer['_id'], 'customer_name': customer.get('name'), 'customer_phone': customer.get('phone_number'),
        'agent_id': str(actor['_id']), 'agent_name': actor.get('name'), 'agent_branch': actor.get('branch'),
        'manager_id': manager['_id'], 'manager_branch': manager.get('branch'), 'product_index': product_index,
        'packaged_product': {'name': product.get('name') or 'Product card'}, 'expected_delivery_date': expected,
        'note': note, 'undelivered_items': items, 'open_card_key': f"{customer['_id']}:{product_index}",
        'history': [{'status': 'pending', 'at': now.isoformat(), 'by': str(actor['_id']), 'name': actor.get('name'), 'role': 'agent'}]}
    try:result = undelivered_items_col.insert_one(doc)
    except DuplicateKeyError:return jsonify(ok=False, message='An open record already exists. Refresh the page.'), 409
    return jsonify(ok=True, message='Undelivered products saved.', id=str(result.inserted_id)), 201


@undelivered_items_bp.route("/undelivered_items", methods=["GET"])
@undelivered_items_bp.get("/undelivered-products")
def undelivered_items_page():
    actor, role = _session_actor()
    if role not in ("agent", "manager", "admin", "inventory", "executive"):
        return redirect(url_for("login.login"))

    branches = []
    if role in ("admin", "inventory", "executive"):
        branches = sorted([b for b in users_col.distinct("branch", {"role": "manager"}) if b])
    return render_template("undelivered_items.html", role=role, branches=branches)


@undelivered_items_bp.route("/undelivered_items/data", methods=["GET"])
def undelivered_items_data():
    status = (request.args.get("status") or "open").strip().lower()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    manager_id_param = (request.args.get("manager_id") or "").strip()
    branch = (request.args.get("branch") or "").strip()

    base = {}
    if request.args.get('inventory_queue') == '1':base['inventory_sent_at'] = {'$exists': True}
    if status == 'open':base['status'] = {'$ne': 'delivered'}
    elif status in STATUSES:base['status'] = status
    elif status != 'all':raise ValueError('Choose a valid status.')

    if date_from or date_to:
        dtflt = {}
        if date_from:
            try:
                dtflt["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
            except ValueError:
                raise ValueError('Enter a valid start date.')
        if date_to:
            try:
                dtflt["$lt"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                raise ValueError('Enter a valid end date.')
        if dtflt:
            base["created_at"] = dtflt

    manager_oid = _oid(manager_id_param) if manager_id_param else None
    flt, _actor, _role = _scoped_filter(base, branch=branch, manager_id=manager_oid)

    if not _actor:return jsonify(ok=False, message='Unauthorized'), 403
    try:page = max(1, int(request.args.get('page', 1)))
    except ValueError:raise ValueError('Invalid page.')
    total = undelivered_items_col.count_documents(flt)
    pages = max(1, (total + 29) // 30)
    page = min(page, pages)
    rows = list(undelivered_items_col.find(flt).sort([('created_at', -1), ('_id', -1)]).skip((page - 1) * 30).limit(30))
    out = []
    for r in rows:
        expected = r.get("expected_delivery_date")
        is_due_soon, is_overdue = _due_flags(expected)
        created_at = r.get("created_at")
        created_str = created_at.isoformat() + 'Z' if hasattr(created_at, "isoformat") else None
        out.append({
            "id": str(r.get("_id")),
            "version": r.get('version', 0), "customer_id": str(r.get('customer_id') or ''),
            "product_index": r.get('product_index'), "note": r.get('note') or '',
            "inventory_sent_at": _safe_json_value(r.get("inventory_sent_at")),
            "inventory_reason": r.get("inventory_reason") or "",
            "inventory_updated_by_name": r.get("inventory_updated_by_name") or "",
            "inventory_updated_at": _safe_json_value(r.get("inventory_updated_at")),
            "status_label": STATUSES.get(r.get('status'), 'Undelivered'),
            "history": _safe_json_value(r.get('history') or []),
            "created_at": created_str,
            "expected_delivery_date": expected,
            "status": r.get("status"),
            "customer_name": r.get("customer_name"),
            "agent_name": r.get("agent_name"),
            "agent_branch": r.get("agent_branch"),
            "manager_branch": r.get("manager_branch"),
            "product_name": (r.get("packaged_product") or {}).get("name"),
            "undelivered_items": _safe_json_value(r.get("undelivered_items") or []),
            "is_due_soon": is_due_soon,
            "is_overdue": is_overdue
        })
    return jsonify(ok=True, rows=out, total=total, page=page, pages=pages)


def _locked_record(query, row):
    return {'$and': [query, {'version': row.get('version', 0) if 'version' in row else {'$exists': False}}]}


@undelivered_items_bp.post('/undelivered_items/<record_id>/send-to-inventory')
def send_to_inventory(record_id):
    query, actor, role = _scoped_filter({'_id': _oid(record_id)})
    if role != 'manager':return jsonify(ok=False, message='Manager access required.'), 403
    row = undelivered_items_col.find_one(query)
    if not row:return jsonify(ok=False, message='Record not found.'), 404
    if row.get('status') == 'delivered':return jsonify(ok=False, message='This record is already delivered.'), 409
    if row.get('inventory_sent_at'):return jsonify(ok=True, message='Already sent to inventory.')
    now = datetime.utcnow()
    result = undelivered_items_col.update_one(_locked_record(query, row), {
        '$set': {'inventory_sent_at': now, 'inventory_sent_by': str(actor['_id']), 'updated_at': now},
        '$inc': {'version': 1}, '$push': {'history': {'event': 'sent_to_inventory', 'status': row.get('status'),
        'at': now.isoformat(), 'by': str(actor['_id']), 'name': actor.get('name'), 'role': role}}})
    if not result.modified_count:return jsonify(ok=False, message='This record changed. Refresh before sending.'), 409
    return jsonify(ok=True, message='Sent to inventory.')


@undelivered_items_bp.post('/undelivered_items/<record_id>/inventory-update')
def inventory_update(record_id):
    query, actor, role = _scoped_filter({'_id': _oid(record_id), 'inventory_sent_at': {'$exists': True}})
    if role not in {'inventory', 'executive', 'admin'}:return jsonify(ok=False, message='Inventory access required.'), 403
    row = undelivered_items_col.find_one(query)
    if not row:return jsonify(ok=False, message='Record not found.'), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):raise ValueError('Enter a valid inventory update.')
    reason = _text(data.get('reason', ''), 'reason', 2000, True)
    status = data.get('status', row.get('status'))
    if not isinstance(status, str) or status not in {'pending', 'preparing', 'products_in'}:raise ValueError('Choose Undelivered, Preparing, or Products In.')
    if row.get('status') == 'delivered':return jsonify(ok=False, message='Delivered records cannot be changed.'), 409
    if str(data.get('version')) != str(row.get('version', 0)):
        return jsonify(ok=False, message='This record changed. Refresh before updating.'), 409
    now = datetime.utcnow()
    result = undelivered_items_col.update_one(_locked_record(query, row), {
        '$set': {'status': status, 'inventory_reason': reason, 'inventory_updated_at': now,
        'inventory_updated_by': str(actor['_id']), 'inventory_updated_by_name': actor.get('name'), 'updated_at': now},
        '$inc': {'version': 1}, '$push': {'history': {'event': 'inventory_update', 'reason': reason, 'status': status,
        'at': now.isoformat(), 'by': str(actor['_id']), 'name': actor.get('name'), 'role': role}}})
    if not result.modified_count:return jsonify(ok=False, message='This record changed. Refresh before updating.'), 409
    return jsonify(ok=True, message='Inventory reason and status saved.')


@undelivered_items_bp.post('/undelivered_items/<record_id>/status')
@undelivered_items_bp.post('/undelivered_items/<record_id>/mark_delivered')
def mark_undelivered_delivered(record_id):
    query, actor, role = _scoped_filter({'_id': _oid(record_id)})
    if not actor:return jsonify(ok=False, message='Unauthorized'), 403
    if role == 'inventory':return jsonify(ok=False, message='Use the inventory update form and enter a reason.'), 403
    row = undelivered_items_col.find_one(query)
    if not row:return jsonify(ok=False, message='Record not found.'), 404
    data = request.get_json(silent=True) or request.form
    if not hasattr(data, 'get'):raise ValueError('Enter a valid status update.')
    status = 'delivered' if request.path.endswith('/mark_delivered') else data.get('status')
    if not isinstance(status, str) or status not in STATUSES:raise ValueError('Choose a valid status.')
    if status == row.get('status'):return jsonify(ok=True, message='Already saved.')
    if row.get('status') == 'delivered':return jsonify(ok=False, message='Delivered records cannot be reopened.'), 409
    version = row.get('version', 0)
    if str(data.get('version', version)) != str(version):
        return jsonify(ok=False, message='This record changed. Refresh before updating.'), 409
    now = datetime.utcnow()
    fields = {'status': status, 'updated_at': now}
    if status == 'delivered':
        fields.update(delivered_at=now, delivered_by=str(actor['_id']), delivered_by_role=role)
        if role == 'agent':
            from services.delivery_evidence import upload_delivery_photo
            from Backend.inventory.stock_deductions_store import StockDeductionError
            try:fields['delivery_evidence'] = upload_delivery_photo(request.files.get('delivery_photo'), {'user_id': str(actor['_id']), 'role': role})
            except StockDeductionError as error:return jsonify(ok=False, message=str(error)), error.status
    mutation = {'$set': fields, '$inc': {'version': 1}, '$push': {'history': {
        'status': status, 'at': now.isoformat(), 'by': str(actor['_id']), 'name': actor.get('name'), 'role': role}}}
    if status == 'delivered':mutation['$unset'] = {'open_card_key': ''}
    locked = {'$and': [query, {'status': row.get('status'), 'version': version if 'version' in row else {'$exists': False}}]}
    result = undelivered_items_col.update_one(locked, mutation)
    if not result.modified_count:return jsonify(ok=False, message='This record changed. Refresh before updating.'), 409
    return jsonify(ok=True, message='Status updated.')


@undelivered_items_bp.route("/undelivered_items/count_due", methods=["GET"])
def undelivered_items_count_due():
    base = {"status": {"$ne": "delivered"}}
    flt, _actor, _role = _scoped_filter(base)

    if not _actor:return jsonify(ok=False, message='Unauthorized'), 403
    today = datetime.utcnow().date()
    today_str = today.strftime("%Y-%m-%d")
    due_soon_end = (today + timedelta(days=2)).strftime("%Y-%m-%d")

    pending_total = undelivered_items_col.count_documents(flt)
    due_soon = undelivered_items_col.count_documents({
        **flt,
        "expected_delivery_date": {"$gte": today_str, "$lte": due_soon_end},
    })
    overdue = undelivered_items_col.count_documents({
        **flt,
        "expected_delivery_date": {"$nin": [None, ""], "$lt": today_str},
    })
    return jsonify(ok=True, pending_total=pending_total, due_soon=due_soon, overdue=overdue)


@undelivered_items_bp.record_once
def indexes(state):
    try:
        undelivered_items_col.create_index([('inventory_sent_at', -1), ('status', 1)])
        undelivered_items_col.create_index('open_card_key', unique=True, sparse=True)
        undelivered_items_col.create_index([('agent_id', 1), ('status', 1), ('created_at', -1)])
        undelivered_items_col.create_index([('manager_id', 1), ('status', 1), ('created_at', -1)])
    except Exception:
        state.app.logger.exception('Unable to create undelivered product indexes')
