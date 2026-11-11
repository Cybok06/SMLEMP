# routes/close_card.py
from flask import Blueprint, render_template, request, jsonify, abort, session
from bson import ObjectId
from datetime import datetime
from db import db

close_card_bp = Blueprint("close_card", __name__, template_folder="templates")

customers_col                  = db["customers"]
payments_col                   = db["payments"]
users_col                      = db["users"]
inventory_col                  = db["inventory"]  # inventory catalog
inventory_products_col         = db["inventory_products"]
card_closures_col              = db["card_closures"]       # audit log for closures (not transfers)
inventory_products_outflow_col = db["inventory_products_outflow"]

# ---------- helpers: ids / roles / scope (session-only) ----------

def _oid(v):
    try:
        return ObjectId(str(v))
    except Exception:
        return None

def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def _session_actor():
    key_role_map = [
        ("agent_id", "agent"),
        ("manager_id", "manager"),
        ("inventory_id", "inventory"),
        ("admin_id", "admin"),
        ("executive_id", "executive"),
    ]
    for key, role in key_role_map:
        uid = session.get(key)
        if uid:
            oid = _oid(uid)
            if not oid:
                break
            doc = users_col.find_one({"_id": oid})
            if doc:
                return doc, role
    return None, ""

def _scoped_filter(base=None, branch=None):
    """
    Merge the caller's filter with role/branch scope using $and so we never
    overwrite the existing text/phone $or filter.
    Also tolerate agent_id/manager_id stored as string or ObjectId.
    """
    base = dict(base or {})
    actor, role = _session_actor()

    scope_clauses = []
    if role == "agent":
        aid_str = str(actor["_id"])
        scope_clauses.append({"$or": [{"agent_id": aid_str}, {"agent_id": actor["_id"]}]})
    elif role == "manager":
        mid = actor["_id"]
        scope_clauses.append({"$or": [{"manager_id": mid}, {"manager_id": str(mid)}]})
    else:
        if branch:
            scope_clauses.append({"branch": branch})

    if scope_clauses:
        base = {"$and": [base] + scope_clauses}

    return base, actor, role

def _ensure_customer_in_scope(customer_id: str):
    cid = _oid(customer_id)
    if not cid:
        abort(400, description="Invalid customer id.")
    filt, _actor, _role = _scoped_filter({"_id": cid})
    customer = customers_col.find_one(filt)
    if not customer:
        abort(403, description="Unauthorized or customer not found.")
    return customer

def _sum_paid_for_product(customer_oid: ObjectId, product_index: int) -> float:
    """
    Sum net product payments for this customer's selected product.
    Deposits increase the balance, product withdrawals reduce it, and susu rows
    are ignored. Also tolerate legacy string/ObjectId customer and product ids.
    """
    idx = int(product_index)
    query = {
        "customer_id": {"$in": [customer_oid, str(customer_oid)]},
        "$or": [{"product_index": idx}, {"product_index": str(idx)}],
        "payment_type": {"$nin": ["SUSU"]},
    }
    total = 0.0
    for p in payments_col.find(query, {"amount": 1, "payment_type": 1}):
        try:
            amount = float(p.get("amount", 0) or 0)
            if p.get("payment_type") == "WITHDRAWAL":
                amount *= -1
            total += amount
        except Exception:
            pass
    return round(total, 2)

def _inventory_branch_scope(customer: dict, actor: dict | None) -> str:
    actor_branch = str((actor or {}).get("branch") or "").strip()
    customer_branch = str((customer or {}).get("branch") or "").strip()
    return actor_branch or customer_branch

def _normalize_inventory_entry(entry: dict) -> dict:
    return {
        "branch": str(entry.get("branch") or "").strip(),
        "quantity": int(entry.get("quantity") or 0),
        "selling_price": float(entry.get("selling_price") or 0),
        "cost_price": float(entry.get("cost_price") or 0),
        "updated_at": entry.get("updated_at"),
        "created_at": entry.get("created_at"),
    }

def _scoped_inventory_entries(doc: dict, branch: str) -> list[dict]:
    entries = [_normalize_inventory_entry(entry) for entry in (doc.get("entries") or []) if isinstance(entry, dict)]
    if not branch:
        return entries
    return [entry for entry in entries if entry.get("branch") == branch]

def _inventory_product_snapshot(doc: dict, branch: str) -> dict:
    branch_entries = _scoped_inventory_entries(doc, branch)
    all_entries = _scoped_inventory_entries(doc, "")
    entries = branch_entries or all_entries
    total_qty = max(0, sum(int(entry.get("quantity") or 0) for entry in entries))

    def _positive_prices(rows: list[dict], key: str) -> list[float]:
        values = []
        for row in rows:
            try:
                value = float(row.get(key) or 0)
            except Exception:
                value = 0.0
            if value > 0:
                values.append(value)
        return values

    scoped_selling = _positive_prices(branch_entries, "selling_price")
    fallback_selling = _positive_prices(all_entries, "selling_price")
    scoped_cost = _positive_prices(branch_entries, "cost_price")
    fallback_cost = _positive_prices(all_entries, "cost_price")

    selling_price = min(scoped_selling or fallback_selling or [0.0])
    cost_price = min(scoped_cost or fallback_cost or [0.0])
    return {
        "_id": doc.get("_id"),
        "name": doc.get("name", "Unnamed"),
        "category": doc.get("category", ""),
        "image_url": doc.get("image_url", ""),
        "qty": total_qty,
        "selling_price": selling_price,
        "price": selling_price or cost_price,
        "entries": entries,
        "sku": doc.get("sku", ""),
        "branch_scope": branch,
        "has_branch_entry": bool(branch_entries),
        "source_collection": "inventory_products",
    }


def _product_unit_price(product: dict) -> float:
    try:
        selling = float(product.get("selling_price") or 0)
    except Exception:
        selling = 0.0
    if selling > 0:
        return selling
    try:
        fallback = float(product.get("price") or 0)
    except Exception:
        fallback = 0.0
    return fallback if fallback > 0 else 0.0

# ---------- routes ----------

@close_card_bp.route("/close_card", methods=["GET"])
def close_card_page():
    # Page shell; data via AJAX
    return render_template("close_card.html")

@close_card_bp.route("/close_card/search_customers", methods=["GET"])
def close_card_search_customers():
    raw_q = (request.args.get("q") or "").strip()
    if not raw_q:
        return jsonify(ok=True, results=[])

    # Normalize phone digits when the query looks like a phone
    q_digits = _digits_only(raw_q)

    # Base filter: name matches OR phone matches (digits if present, else raw)
    phone_regex = q_digits if q_digits else raw_q
    base_filter = {
        "$or": [
            {"name": {"$regex": raw_q, "$options": "i"}},
            {"phone_number": {"$regex": phone_regex}}
        ]
    }

    try:
        limit = max(1, min(int(request.args.get("limit", 8)), 25))
    except Exception:
        limit = 8
    try:
        page = max(1, int(request.args.get("page", 1)))
    except Exception:
        page = 1
    skip = (page - 1) * limit

    branch = (request.args.get("branch") or "").strip() or None
    filt, _actor, _role = _scoped_filter(base_filter, branch=branch)

    projection = {"name": 1, "phone_number": 1, "image_url": 1, "purchases": 1}
    cursor = customers_col.find(filt, projection).skip(skip).limit(limit)

    results = []
    for c in cursor:
        cid = c["_id"]
        purchases = c.get("purchases", []) or []
        enriched = []
        for idx, pur in enumerate(purchases):
            prod = (pur or {}).get("product", {}) or {}
            pname = prod.get("name", "Unnamed Product")
            ptotal = float(prod.get("total", 0) or 0)
            paid = _sum_paid_for_product(cid, idx)
            enriched.append({
                "index": idx,
                "name": pname,
                "total": round(ptotal, 2),
                "paid": paid,
                "outstanding": round(max(ptotal - paid, 0.0), 2),
                "status": prod.get("status", ""),
                "purchase_type": (pur or {}).get("purchase_type", ""),
                "purchase_date": (pur or {}).get("purchase_date", ""),
            })

        results.append({
            "customer_id": str(cid),
            "name": c.get("name", "Unknown"),
            "phone_number": c.get("phone_number", ""),
            "image_url": c.get("image_url", ""),
            "purchases": enriched
        })

    return jsonify(ok=True, results=results, page=page, limit=limit)

@close_card_bp.route("/close_card/suggest_products", methods=["GET"])
def close_card_suggest_products():
    """
    Given customer_id & product_index, compute:
      - net total_paid on selected product
      - two_thirds = 2/3 * total_paid
      - one_third  = 1/3 * total_paid (forfeited)
    Return a list of products that two_thirds can fully purchase.
    Optional filters: ?category=&limit=
    """
    customer_id = request.args.get("customer_id", "").strip()
    product_index = request.args.get("product_index", "").strip()
    if not customer_id or product_index == "":
        return jsonify(ok=False, message="Missing fields."), 400

    customer = _ensure_customer_in_scope(customer_id)
    actor, _actor_role = _session_actor()
    try:
        pidx = int(product_index)
    except Exception:
        return jsonify(ok=False, message="Invalid product index."), 400

    purchases = customer.get("purchases", []) or []
    if pidx < 0 or pidx >= len(purchases):
        return jsonify(ok=False, message="Product not found for this customer."), 404

    total_paid = _sum_paid_for_product(customer["_id"], pidx)
    two_thirds = round((2.0/3.0) * total_paid, 2)
    one_third  = round((1.0/3.0) * total_paid, 2)

    # Optional override when user manually adjusts kept amount
    budget_override = request.args.get("budget")
    if budget_override is not None:
        try:
            budget_override = float(budget_override)
        except Exception:
            budget_override = None
    if budget_override is not None:
        if budget_override < 0:
            budget_override = 0
        if total_paid > 0 and budget_override > (total_paid + 0.01):
            budget_override = total_paid
        two_thirds = round(budget_override, 2)
        one_third = round(max(total_paid - two_thirds, 0.0), 2)

    category = (request.args.get("category") or "").strip() or None
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 50))
    except Exception:
        limit = 20
    try:
        page = max(1, int(request.args.get("page", 1)))
    except Exception:
        page = 1

    branch = _inventory_branch_scope(customer, actor)
    base_filter = {}
    if category:
        base_filter = {"$and": [base_filter, {"category": category}]} if base_filter else {"category": category}

    docs = list(inventory_products_col.find(
        base_filter,
        {"name": 1, "image_url": 1, "category": 1, "entries": 1, "sku": 1}
    ))
    docs = [_inventory_product_snapshot(doc, branch) for doc in docs]

    def _to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    def price_candidates(d):
        vals = []
        sp = _to_float(d.get("selling_price"))
        lp = _to_float(d.get("price"))
        if sp is not None:
            vals.append(sp)
        if lp is not None:
            vals.append(lp)
        return vals

    def eff_price(d):
        candidates = price_candidates(d)
        return max(candidates) if candidates else 0.0

    def display_price(d, budget):
        sp = _to_float(d.get("selling_price"))
        lp = _to_float(d.get("price"))
        if sp is not None and sp <= budget:
            return sp
        if lp is not None and lp <= budget:
            return lp
        if sp is not None:
            return sp
        if lp is not None:
            return lp
        return 0.0

    # Show all products with a valid selling price and sort low to high.
    docs = [d for d in docs if float(d.get("selling_price") or 0) > 0]
    docs.sort(key=lambda d: float(d.get("selling_price") or d.get("price") or 0))
    total = len(docs)
    total_pages = max(1, (total + limit - 1) // limit)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * limit
    end = start + limit
    docs = docs[start:end]
    docs = docs[:limit]

    results = [{
        "_id": str(d["_id"]),
        "name": d.get("name", "Unnamed"),
        "price": float(display_price(d, two_thirds)),
        "qty": d.get("qty", 0),
        "image_url": d.get("image_url", ""),
        "category": d.get("category", "")
    } for d in docs]

    return jsonify(
        ok=True,
        budget=two_thirds,
        forfeited=one_third,
        total_paid=total_paid,
        suggestions=results,
        page=page,
        limit=limit,
        total=total,
        total_pages=total_pages
    )

@close_card_bp.route("/close_card/execute", methods=["POST"])
def close_card_execute():
    """
    Close a card for a customer:
      - compute 2/3 of net total paid on the selected product
      - (only suggest product; no new purchase is created here)
      - keep customer + payments intact
      - mark the selected purchase as closed (leave other purchases untouched)
      - set customer status to closed
      - AUDIT to `card_closures`
      - if a replacement product is chosen, log it in inventory_products_outflow
    """
    actor, actor_role = _session_actor()
    if not actor:
        return jsonify(ok=False, message="Unauthorized: please sign in to close cards."), 401

    data = request.get_json(silent=True) or {}
    customer_id = data.get("customer_id")
    product_index = data.get("product_index")
    note = (data.get("note") or "").strip()
    target_product_id = data.get("target_product_id")  # optional: single product pick
    target_product_ids = data.get("target_product_ids")  # optional: list of picks
    target_products_payload = data.get("target_products")  # optional: list of {id, qty}
    kept_amount = data.get("two_thirds_budget")
    forfeited_amount = data.get("one_third_forfeited")

    if customer_id is None or product_index is None:
        return jsonify(ok=False, message="Missing required fields."), 400
    try:
        pidx = int(product_index)
    except Exception:
        return jsonify(ok=False, message="Invalid product selection."), 400

    # Fetch customer & compute totals
    customer = _ensure_customer_in_scope(customer_id)
    cust_oid = customer["_id"]
    purchases = customer.get("purchases", []) or []
    if pidx < 0 or pidx >= len(purchases):
        return jsonify(ok=False, message="Selected product not found."), 404

    from_purchase = purchases[pidx] or {}
    from_prod = (from_purchase.get("product") or {})

    total_paid = _sum_paid_for_product(cust_oid, pidx)
    two_thirds = round((2.0/3.0) * total_paid, 2)
    one_third  = round((1.0/3.0) * total_paid, 2)

    now_utc = datetime.utcnow()

    # Validate kept/forfeited amounts (fallback to defaults if missing)
    try:
        kept_amount = float(kept_amount) if kept_amount is not None else two_thirds
    except Exception:
        kept_amount = two_thirds
    try:
        forfeited_amount = float(forfeited_amount) if forfeited_amount is not None else one_third
    except Exception:
        forfeited_amount = one_third

    if kept_amount < 0 or forfeited_amount < 0:
        return jsonify(ok=False, message="Kept and forfeited amounts must be non-negative."), 400
    if total_paid > 0 and (kept_amount + forfeited_amount) > (total_paid + 0.01):
        return jsonify(ok=False, message="Kept + forfeited cannot exceed total paid on this card."), 400

    # Prevent double-close
    if (from_prod or {}).get("status") == "closed":
        return jsonify(ok=False, message="This product is already closed."), 400

    branch = _inventory_branch_scope(customer, actor)

    # Optional: resolve the chosen target product for logging
    target_products = []
    if target_products_payload:
        for item in target_products_payload:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            qty = item.get("qty", 1)
            try:
                qty = int(qty)
            except Exception:
                qty = 1
            if qty < 1:
                qty = 1
            t_oid = _oid(pid)
            if not t_oid:
                continue
            prod = inventory_products_col.find_one({"_id": t_oid})
            if prod:
                snap = _inventory_product_snapshot(prod, branch)
                target_products.append({"product": snap, "qty": qty})
    elif target_product_ids:
        for pid in target_product_ids:
            t_oid = _oid(pid)
            if not t_oid:
                continue
            prod = inventory_products_col.find_one({"_id": t_oid})
            if prod:
                snap = _inventory_product_snapshot(prod, branch)
                target_products.append({"product": snap, "qty": 1})
    elif target_product_id:
        t_oid = _oid(target_product_id)
        if t_oid:
            prod = inventory_products_col.find_one({"_id": t_oid})
            if prod:
                snap = _inventory_product_snapshot(prod, branch)
                target_products.append({"product": snap, "qty": 1})

    normalized_target_products = []
    selected_total_price = 0.0
    seen_ids = set()
    for entry in target_products:
        target_product = entry.get("product") or {}
        pid = str(target_product.get("_id") or "").strip()
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)

        try:
            qty = int(entry.get("qty", 1))
        except Exception:
            qty = 1
        if qty < 1:
            return jsonify(ok=False, message="Selected quantities must be at least 1."), 400

        unit_price = _product_unit_price(target_product)
        if unit_price <= 0:
            return jsonify(
                ok=False,
                message=f"Selected product '{target_product.get('name') or 'product'}' does not have a valid price.",
            ), 400

        line_total = round(unit_price * qty, 2)
        selected_total_price = round(selected_total_price + line_total, 2)
        normalized_target_products.append({
            "product": target_product,
            "qty": qty,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    target_products = normalized_target_products

    if selected_total_price > (kept_amount + 0.01):
        return jsonify(
            ok=False,
            message=f"Selected products total ({selected_total_price:.2f}) cannot exceed Amount Kept ({kept_amount:.2f}).",
        ), 400

    # 1) Mark the purchase as closed and close the customer
    customers_col.update_one(
        {"_id": cust_oid},
        {"$set": {
            "status": "closed",
            "status_updated_at": now_utc,
            f"purchases.{pidx}.product.status": "closed",
            f"purchases.{pidx}.status": "closed",
            f"purchases.{pidx}.closed_at": now_utc,
            f"purchases.{pidx}.closed_by": str(actor["_id"]),
            f"purchases.{pidx}.closed_by_role": actor_role,
            f"purchases.{pidx}.closed_note": note
        }}
    )

    # 2) Preserve original payment amounts on this closed card and zero active amount
    payment_query = {
        "customer_id": {"$in": [cust_oid, str(cust_oid)]},
        "$or": [{"product_index": pidx}, {"product_index": str(pidx)}],
        "payment_type": {"$in": ["PRODUCT", "WITHDRAWAL"]},
    }
    payment_projection = {"amount": 1}
    for payment_doc in payments_col.find(payment_query, payment_projection):
        original_amount = payment_doc.get("amount", 0)
        try:
            original_amount = float(original_amount or 0)
        except Exception:
            original_amount = 0.0

        payments_col.update_one(
            {"_id": payment_doc["_id"]},
            {
                "$set": {
                    "amount": 0.0,
                    "closed_amount": original_amount,
                    "card_closed_at": now_utc,
                    "card_closed_by": str(actor["_id"]),
                    "card_closed_by_role": actor_role,
                    "card_closed_product_index": pidx,
                }
            }
        )

    # 3) Log inventory outflow if a replacement product was picked
    for entry in target_products:
        target_product = entry["product"]
        qty = entry.get("qty", 1)
        unit_price = float(entry.get("unit_price") or _product_unit_price(target_product) or 0)
        line_total = float(entry.get("line_total") or (qty or 0) * unit_price)
        inventory_products_outflow_col.insert_one({
            "created_at": now_utc,
            "source": "close_card",
            "customer_id": cust_oid,
            "customer_name": customer.get("name"),
            "customer_phone": customer.get("phone_number"),
            "closed_product_index": pidx,
            "closed_product": from_prod,
            "budget": {
                "total_paid_selected_product": total_paid,
                "kept_amount": kept_amount,
                "forfeited_amount": forfeited_amount
            },
            "selected_product_id": str(target_product.get("_id")),
            "selected_product": target_product,
            "selected_qty": int(qty),
            "selected_unit_price": unit_price,
            "selected_total_price": line_total,
            "by_user": str(actor["_id"]),
            "by_role": actor_role
        })

    # 4) Audit log
    closure_result = card_closures_col.insert_one({
        "customer_id": cust_oid,
        "at": now_utc,
        "action": "close_card",
        "by_user": str(actor["_id"]),
        "by_role": actor_role,
        "payload": {
            "selected_product_index": pidx,
            "selected_product_name": from_prod.get("name"),
            "kept_amount": float(kept_amount),
            "forfeited_amount": float(forfeited_amount),
            "selected_total_price": float(selected_total_price),
            "target_products": [{
                "id": str(p["product"]["_id"]),
                "qty": int(p.get("qty", 1)),
                "unit_price": float(p.get("unit_price") or 0),
                "line_total": float(p.get("line_total") or 0),
            } for p in target_products],
            "note": note
        }
    })

    from services.delivery_followups import ensure_delivery_followup
    matching_package = db.packages.find_one({"customer_id": {"$in": [cust_oid, str(cust_oid)]}, "product_index": {"$in": [pidx, str(pidx)]}})
    followup_id = ensure_delivery_followup(db, customer=customer, package=matching_package,
        product_index=pidx, trigger="card_closed", event_at=now_utc)
    card_closures_col.update_one({"_id": closure_result.inserted_id}, {"$set": {"support_followup_task_id": followup_id}})

    return jsonify(
        ok=True,
        message="Card closed. Customer and payments retained.",
        data={
            "counted_for_two_thirds": float(total_paid),  # explicit, for clarity
            "two_thirds_budget": float(kept_amount),
            "one_third_forfeited": float(forfeited_amount),
            "selected_total_price": float(selected_total_price),
            "payments_zeroed": payments_col.count_documents({
                "customer_id": {"$in": [cust_oid, str(cust_oid)]},
                "$or": [{"product_index": pidx}, {"product_index": str(pidx)}],
                "amount": 0.0,
                "closed_amount": {"$exists": True}
            }),
            "target_products": [{
                "id": str(p["product"]["_id"]),
                "qty": int(p.get("qty", 1)),
                "unit_price": float(p.get("unit_price") or 0),
                "line_total": float(p.get("line_total") or 0),
            } for p in target_products]
        }
    )


@close_card_bp.route("/closed_cards/metrics", methods=["GET"])
def closed_cards_metrics_alias():
    from routes.closed_cards_history import closed_cards_history_metrics
    return closed_cards_history_metrics()


@close_card_bp.route("/api/closed-cards/summary", methods=["GET"])
def closed_cards_summary():
    from routes.closed_cards_history import closed_cards_history_metrics
    return closed_cards_history_metrics()
