# routes/executive_pricing.py
from __future__ import annotations
from flask import Blueprint, render_template, request, jsonify
from bson import ObjectId
from bson.decimal128 import Decimal128
from decimal import Decimal
from typing import Any, Dict, List
from datetime import datetime

from db import db
from services.activity_audit import audit_action
from login import get_current_identity

customers_col = db["customers"]
users_col = db["users"]
products_col = db["products"]
payments_col = db["payments"]
price_logs_col = db["price_change_logs"]   # NEW

executive_pricing_bp = Blueprint("executive_pricing", __name__, url_prefix="/exec/pricing")


def _oid(x: str):
    try:
        return ObjectId(x)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    """Convert Mongo values inside nested docs into Flask JSON-safe values."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, Decimal128):
            value = value.to_decimal()
        return float(value)
    except Exception:
        return default


def _pricing_purchase_view(purchase: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return only the fields the pricing UI needs from a customer purchase.
    Raw purchase docs can contain package/component ObjectIds and other BSON data.
    """
    prod_obj = purchase.get("product") or {}
    qty = _num(prod_obj.get("quantity", 1), 1.0)
    price = _num(prod_obj.get("price", 0), 0.0)
    total = prod_obj.get("total")
    if total is None:
        total = price * qty
    total_num = _num(total, price * qty)

    return {
        "product": {
            "name": prod_obj.get("name") or "",
            "price": price,
            "quantity": qty,
            "total": total_num,
        }
    }


# ---------------- Page ----------------
@executive_pricing_bp.route("/", methods=["GET"])
def pricing_page():
    managers = list(
        users_col.find({"role": "manager"}, {"name": 1, "branch": 1})
        .sort([("branch", 1), ("name", 1)])
    )
    return render_template("executive_pricing.html", managers=managers)


# ------------- Products for branch (with price shown) -------------
@executive_pricing_bp.route("/products", methods=["GET"])
def products_for_branch():
    """
    Used by the UI to load products for ONE "primary" branch (selected manager).
    Even though logically-same products exist per-branch with different _id,
    we still load per-manager here. The multi-branch logic is handled in stats/update.
    """
    manager_id = (request.args.get("manager_id") or "").strip()
    mo = _oid(manager_id)
    if not mo:
        return jsonify({"ok": False, "error": "invalid manager_id"}), 400

    prods = list(
        products_col.find(
            {"manager_id": mo},
            {"name": 1, "image_url": 1, "price": 1, "cash_price": 1},
        ).sort([("name", 1)])
    )
    out: List[Dict[str, Any]] = []
    for p in prods:
        out.append(
            {
                "_id": str(p["_id"]),
                "name": p.get("name"),
                "image_url": p.get("image_url"),
                "price": p.get("price"),
                "cash_price": p.get("cash_price"),
            }
        )
    return jsonify({"ok": True, "products": out})


# ---------------- Stats (MULTI-BRANCH, MATCH BY PRODUCT NAME) ----------------
@executive_pricing_bp.route("/stats", methods=["GET"])
def stats():
    """
    Aggregated stats for one logical product across one or many branches.

    Query params:
      - product_id: str (required; from analytics branch)
      - manager_ids: can appear multiple times, e.g. ?manager_ids=...&manager_ids=...
                     (preferred for multi-branch)
      - manager_id:  single id (fallback / backward compatible)

    Internally we:
      1. Resolve product_id -> product document.
      2. Use product_name (and not the product _id) to match purchases across
         all selected branches, because each branch has its own product _id.
    """
    product_id = (request.args.get("product_id") or "").strip()
    if not product_id:
        return jsonify({"ok": False, "error": "product_id required"}), 400

    # --- collect manager ids (multi-branch aware) ---
    manager_ids_multi = request.args.getlist("manager_ids") or []
    manager_id_single = (request.args.get("manager_id") or "").strip()

    manager_oids: List[ObjectId] = []

    for mid in manager_ids_multi:
        mid_str = (mid or "").strip()
        if not mid_str:
            continue
        mo = _oid(mid_str)
        if mo:
            manager_oids.append(mo)

    # Backward-compatible single-manager mode
    if not manager_oids and manager_id_single:
        mo = _oid(manager_id_single)
        if mo:
            manager_oids.append(mo)

    if not manager_oids:
        return jsonify({"ok": False, "error": "At least one manager_id / branch is required"}), 400

    # Get selected product (from analytics branch) and use its NAME as the logical key
    try:
        prod = products_col.find_one(
            {"_id": ObjectId(product_id)},
            {"name": 1, "price": 1, "cash_price": 1},
        )
    except Exception:
        prod = None

    if not prod:
        return jsonify({"ok": False, "error": "product not found"}), 404

    product_name: str = prod.get("name", "")

    # Customers in selected branches having THIS PRODUCT NAME in their purchases
    match: Dict[str, Any] = {
        "manager_id": {"$in": manager_oids},
        "purchases.product.name": product_name,
    }

    # Total customers (branches+product)
    total_customers = customers_col.count_documents(match)

    # Pull customers (trim) and keep only selected product-name purchases
    customers = list(
        customers_col.find(
            match,
            {
                "name": 1,
                "phone_number": 1,
                "location": 1,
                "agent_id": 1,
                "purchases": 1,
                "manager_id": 1,
            },
        ).limit(2000)
    )

    cust_ids: List[ObjectId] = []
    per_customer_purchase_total: Dict[ObjectId, float] = {}

    for c in customers:
        cid = c["_id"]
        cust_ids.append(cid)
        filtered = []
        for p in (c.get("purchases") or []):
            prod_obj = p.get("product") or {}
            # match by NAME, not by embedded product _id
            if prod_obj.get("name") == product_name:
                qty = prod_obj.get("quantity", 1) or 1
                price = prod_obj.get("price", 0) or 0
                total = prod_obj.get("total")
                if total is None:
                    total = price * qty
                    prod_obj["total"] = total
                filtered.append(p)
                cur = per_customer_purchase_total.get(cid, 0.0)
                if float(total) > float(cur):
                    per_customer_purchase_total[cid] = float(total)
        c["purchases"] = filtered

    # Payments by these customers for this product name (multi-branch-scoped)
    pay_match: Dict[str, Any] = {
        "manager_id": {"$in": manager_oids},
        "customer_id": {"$in": cust_ids},
        "product_name": product_name,
    }
    pays = list(
        payments_col.aggregate(
            [
                {"$match": pay_match},
                {
                    "$group": {
                        "_id": "$customer_id",
                        "paid": {"$sum": "$amount"},
                        "txns": {"$sum": 1},
                    }
                },
            ]
        )
    )

    paid_map: Dict[ObjectId, Dict[str, Any]] = {
        p["_id"]: {"paid": float(p["paid"]), "txns": int(p["txns"])} for p in pays
    }

    # Decorate customers with paid info and band
    def band_for_ratio(ratio: float) -> str:
        if ratio < 0.33:
            return "low"
        if ratio < 0.66:
            return "avg"
        return "high"

    bands_count = {"low": 0, "avg": 0, "high": 0}
    enriched_customers: List[Dict[str, Any]] = []
    for c in customers:
        cid = c["_id"]
        pc_total = float(per_customer_purchase_total.get(cid, 0.0))
        pm = paid_map.get(cid, {"paid": 0.0, "txns": 0})
        paid = float(pm["paid"])
        ratio = (paid / pc_total) if pc_total > 0 else 0.0
        band = band_for_ratio(ratio)
        bands_count[band] += 1

        enriched_customers.append(
            {
                "_id": str(cid),
                "name": c.get("name"),
                "phone_number": c.get("phone_number"),
                "location": c.get("location"),
                "purchase_total": pc_total,
                "paid": round(paid, 2),
                "paid_ratio": round(ratio, 4),
                "band": band,
                "purchases": [_pricing_purchase_view(p) for p in c.get("purchases", [])],
            }
        )

    # Branch label(s) + manager label
    mgrs = list(
        users_col.find(
            {"_id": {"$in": manager_oids}},
            {"branch": 1, "name": 1},
        )
    )
    if len(mgrs) == 1:
        branch_label = (mgrs[0].get("branch") or "Unknown").strip() or "Unknown"
        manager_label = mgrs[0].get("name") or "Unknown"
    else:
        branches = sorted(
            {(m.get("branch") or "Unknown").strip() or "Unknown" for m in mgrs}
        )
        branch_label = ", ".join(branches)
        manager_label = f"{len(mgrs)} managers"

    payload = {
        "ok": True,
        "branch": branch_label,
        "manager_name": manager_label,
        "count": total_customers,
        "product": {
            "_id": str(prod["_id"]),
            "name": product_name,
            "price": prod.get("price"),
            "cash_price": prod.get("cash_price"),
        },
        "bands": bands_count,
        "customers": enriched_customers,
    }
    return jsonify(_json_safe(payload))


# ---------------- Logs (recent) ----------------
@executive_pricing_bp.route("/logs", methods=["GET"])
def logs():
    product_id = (request.args.get("product_id") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    limit = int(request.args.get("limit", 20))
    q: Dict[str, Any] = {}
    if product_id:
        q["product_id"] = product_id
    if manager_id:
        mo = _oid(manager_id)
        if mo:
            q["manager_id"] = mo

    items = list(
        price_logs_col.find(q)
        .sort([("ts", -1)])
        .limit(max(1, min(limit, 100)))
    )
    for it in items:
        it["_id"] = str(it["_id"])
        if isinstance(it.get("manager_id"), ObjectId):
            it["manager_id"] = str(it["manager_id"])
    return jsonify({"ok": True, "logs": _json_safe(items)})


# --------------- Bulk update (MULTI-BRANCH, MATCH BY NAME, TOGGLE SCOPE) ---------------
@executive_pricing_bp.route("/update", methods=["POST"])
@audit_action("pricing.updated", "Updated Pricing", entity_type="product")
def bulk_update():
    """
    Bulk price update for one logical product across one or many branches.

    Body:
      - product_id: str (required; from analytics branch)
      - manager_ids: [str, ...]  (optional, preferred for multi-branch)
      - manager_id:  str         (fallback for single branch, backward compatible)
      - mode: "set" | "percent"
      - value: number
      - scope / apply_scope (optional): "customers" | "products" | "both"
          * default: "customers" (backward compatible)
      - include_customer_ids (optional): [str, ...]
          * when provided, ONLY customers with these _id values will be updated.

    Internally we:
      1. Resolve product_id -> product document.
      2. Use product_name (not product _id) to match purchases & products across branches,
         because each branch has its own product _id for the same logical product.
    """
    data = request.get_json(silent=True) or {}
    product_id = (data.get("product_id") or "").strip()
    manager_id_single = (data.get("manager_id") or "").strip()
    mode = (data.get("mode") or "set").strip().lower()
    price_type = (data.get("price_type") or "price").strip().lower()
    value = data.get("value")

    # --- scope: customers/products/both ---
    scope_raw = (data.get("scope") or data.get("apply_scope") or "customers").strip().lower()
    if scope_raw not in ("customers", "products", "both"):
        return jsonify({"ok": False, "error": "scope must be 'customers', 'products', or 'both'"}), 400
    scope_customers = scope_raw in ("customers", "both")
    scope_products = scope_raw in ("products", "both")

    # --- product validation ---
    if not product_id:
        return jsonify({"ok": False, "error": "product_id required"}), 400

    # --- gather manager ids (multi-branch support) ---
    raw_manager_ids = data.get("manager_ids")
    manager_oids: List[ObjectId] = []

    if isinstance(raw_manager_ids, list):
        for mid in raw_manager_ids:
            mid_str = (str(mid) or "").strip()
            if not mid_str:
                continue
            mo = _oid(mid_str)
            if mo:
                manager_oids.append(mo)

    # Backward-compatible single manager_id if no valid list given
    if not manager_oids and manager_id_single:
        mo = _oid(manager_id_single)
        if mo:
            manager_oids.append(mo)

    if not manager_oids:
        return jsonify({"ok": False, "error": "At least one manager_id / branch is required"}), 400

    if mode not in ("set", "percent"):
        return jsonify({"ok": False, "error": "mode must be 'set' or 'percent'"}), 400
    if price_type not in ("price", "cash_price", "both"):
        return jsonify({"ok": False, "error": "price_type must be 'price', 'cash_price', or 'both'"}), 400
    if price_type == "cash_price" and scope_customers:
        return jsonify({"ok": False, "error": "Cash Price can only be applied to products."}), 400
    try:
        num = float(value)
    except Exception:
        return jsonify({"ok": False, "error": "value must be numeric"}), 400

    # Fetch product doc (from analytics branch) to get the logical key: NAME
    try:
        prod = products_col.find_one(
            {"_id": ObjectId(product_id)}, {"name": 1, "price": 1}
        )
    except Exception:
        prod = None

    if not prod:
        return jsonify({"ok": False, "error": "product not found"}), 404

    product_name: str = prod.get("name", "")
    prod_name_for_log = product_name or (prod.get("name") or "")

    # --- included customers only (optional) ---
    include_raw = data.get("include_customer_ids") or []
    included_customer_oids: List[ObjectId] = []
    if isinstance(include_raw, list):
        for cid in include_raw:
            cid_str = (str(cid) or "").strip()
            if not cid_str:
                continue
            co = _oid(cid_str)
            if co:
                included_customer_oids.append(co)

    # --- build customer update pipeline once (reused per branch), MATCH BY NAME ---
    update_pipeline = None
    applied_value = None

    if scope_customers:
        if mode == "set":
            new_price = int(round(num))
            update_pipeline = [
                {
                    "$set": {
                        "purchases": {
                            "$map": {
                                "input": "$purchases",
                                "as": "p",
                                "in": {
                                    "$cond": [
                                        # match by product.name, NOT product._id
                                        {"$eq": ["$$p.product.name", product_name]},
                                        {
                                            "$mergeObjects": [
                                                "$$p",
                                                {
                                                    "product": {
                                                        "$mergeObjects": [
                                                            "$$p.product",
                                                            {
                                                                "price": new_price,
                                                                "total": {
                                                                    "$multiply": [
                                                                        new_price,
                                                                        {
                                                                            "$ifNull": [
                                                                                "$$p.product.quantity",
                                                                                1,
                                                                            ]
                                                                        },
                                                                    ]
                                                                },
                                                            },
                                                        ]
                                                    }
                                                },
                                            ]
                                        },
                                        "$$p",
                                    ]
                                },
                            }
                        }
                    }
                }
            ]
            applied_value = new_price
        else:
            factor = 1 - (num / 100.0)
            update_pipeline = [
                {
                    "$set": {
                        "purchases": {
                            "$map": {
                                "input": "$purchases",
                                "as": "p",
                                "in": {
                                    "$cond": [
                                        # match by product.name, NOT product._id
                                        {"$eq": ["$$p.product.name", product_name]},
                                        {
                                            "$mergeObjects": [
                                                "$$p",
                                                {
                                                    "product": {
                                                        "$mergeObjects": [
                                                            "$$p.product",
                                                            {
                                                                "price": {
                                                                    "$toInt": {
                                                                        "$round": [
                                                                            {
                                                                                "$multiply": [
                                                                                    "$$p.product.price",
                                                                                    factor,
                                                                                ]
                                                                            },
                                                                            0,
                                                                        ]
                                                                    }
                                                                },
                                                                "total": {
                                                                    "$toInt": {
                                                                        "$round": [
                                                                            {
                                                                                "$multiply": [
                                                                                    {
                                                                                        "$multiply": [
                                                                                            "$$p.product.price",
                                                                                            factor,
                                                                                        ]
                                                                                    },
                                                                                    {
                                                                                        "$ifNull": [
                                                                                            "$$p.product.quantity",
                                                                                            1,
                                                                                        ]
                                                                                    },
                                                                                ]
                                                                            },
                                                                            0,
                                                                        ]
                                                                    }
                                                                },
                                                            },
                                                        ]
                                                    }
                                                },
                                            ]
                                        },
                                        "$$p",
                                    ]
                                },
                            }
                        }
                    }
                }
            ]
            applied_value = num  # percent value
    else:
        # scope excludes customer updates; but we still want applied_value for logging
        applied_value = int(round(num)) if mode == "set" else num

    # --- product update settings (products_col) ---
    factor = None
    if mode == "percent":
        factor = 1 - (num / 100.0)

    # --- apply to each branch (manager) separately ---
    total_matched_customers = 0
    total_modified_customers = 0
    total_matched_products = 0
    total_modified_products = 0

    log_docs: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    request_identity = get_current_identity() or {}
    actor = request_identity.get("name") or request_identity.get("username") or request.headers.get("X-Forwarded-For", request.remote_addr) or "public"

    for mo in manager_oids:
        # ---------- CUSTOMERS ----------
        branch_customer_matched = 0
        branch_customer_modified = 0
        customer_old_prices = []

        if scope_customers and price_type in ("price", "both") and update_pipeline is not None:
            base_match: Dict[str, Any] = {
                "manager_id": mo,
                # match by product.name across branches
                "purchases.product.name": product_name,
            }
            if included_customer_oids:
                base_match["_id"] = {"$in": included_customer_oids}

            # sample old prices per branch (lightweight)
            sample = list(
                customers_col.aggregate(
                    [
                        {"$match": base_match},
                        {"$unwind": "$purchases"},
                        {"$match": {"purchases.product.name": product_name}},
                        {"$limit": 10},
                        {"$project": {"_id": 0, "old_price": "$purchases.product.price"}},
                    ]
                )
            )
            customer_old_prices = [
                s.get("old_price") for s in sample if s.get("old_price") is not None
            ]

            res_cust = customers_col.update_many(base_match, update_pipeline)
            branch_customer_matched = res_cust.matched_count
            branch_customer_modified = res_cust.modified_count

            total_matched_customers += branch_customer_matched
            total_modified_customers += branch_customer_modified

        # ---------- PRODUCTS ----------
        branch_product_matched = 0
        branch_product_modified = 0
        product_old_prices = []

        if scope_products:
            prod_match: Dict[str, Any] = {
                "manager_id": mo,
                "name": product_name,
            }

            # sample existing product prices for log
            before_product_docs = list(
                products_col.find(prod_match, {"price": 1, "cash_price": 1, "cost_price": 1}).limit(2000)
            )
            product_old_prices = [
                sp.get("price") for sp in before_product_docs[:10] if sp.get("price") is not None
            ]

            if mode == "set":
                set_fields = {}
                if price_type in ("price", "both"):
                    set_fields["price"] = int(round(num))
                if price_type in ("cash_price", "both"):
                    set_fields["cash_price"] = int(round(num))
                res_prod = products_col.update_many(
                    prod_match,
                    {"$set": set_fields},
                )
            else:
                # percent discount with rounding similar to customers
                # use pipeline update for proper rounding
                percent_fields = {}
                if price_type in ("price", "both"):
                    percent_fields["price"] = {"$toInt": {"$round": [{"$multiply": ["$price", factor]}, 0]}}
                if price_type in ("cash_price", "both"):
                    percent_fields["cash_price"] = {"$toInt": {"$round": [{"$multiply": [{"$ifNull": ["$cash_price", 0]}, factor]}, 0]}}
                res_prod = products_col.update_many(
                    prod_match,
                    [{"$set": percent_fields}],
                )

            identity = request_identity
            for before_doc in before_product_docs:
                after_doc = products_col.find_one({"_id": before_doc["_id"]}, {"price": 1, "cash_price": 1, "cost_price": 1}) or {}
                changes = []
                if _num(before_doc.get("price")) != _num(after_doc.get("price")):
                    changes.append({"field": "price", "label": "Installment price", "before": _num(before_doc.get("price")), "after": _num(after_doc.get("price"))})
                if _num(before_doc.get("cash_price")) != _num(after_doc.get("cash_price")):
                    changes.append({"field": "cash_price", "label": "Cash price", "before": _num(before_doc.get("cash_price")), "after": _num(after_doc.get("cash_price"))})
                if changes:
                    cost = _num(after_doc.get("cost_price"))
                    installment = _num(after_doc.get("price"))
                    cash = _num(after_doc.get("cash_price"))
                    margin_price = round(((installment - cost) / cost) * 100, 2) if cost > 0 else 0
                    margin_cash = round(((cash - cost) / cost) * 100, 2) if cost > 0 else 0
                    products_col.update_one(
                        {"_id": before_doc["_id"]},
                        {
                            "$set": {
                                "profit_price": round(installment - cost, 2),
                                "profit_cash": round(cash - cost, 2),
                                "profit_margin_price": margin_price,
                                "profit_margin_cash": margin_cash,
                                "updated_at": now,
                            },
                            "$push": {"change_history": {"$each": [{
                                "id": str(ObjectId()),
                                "changed_at": now,
                                "changed_by": {
                                    "user_id": identity.get("user_id"),
                                    "name": identity.get("name") or identity.get("username") or "Executive User",
                                    "username": identity.get("username"),
                                    "role": identity.get("role"),
                                },
                                "changes": changes,
                            }], "$slice": -100}},
                        },
                    )

            branch_product_matched = res_prod.matched_count
            branch_product_modified = res_prod.modified_count

            total_matched_products += branch_product_matched
            total_modified_products += branch_product_modified

        # ---------- LOG PER BRANCH ----------
        log_docs.append(
            {
                "ts": now,
                "manager_id": mo,
                "product_id": product_id,          # reference product (analytics branch)
                "product_name": prod_name_for_log,
                "mode": mode,                      # "set" | "percent"
                "value": applied_value,            # number (price or %)
                "price_type": price_type,
                "scope": scope_raw,                # "customers" | "products" | "both"
                # customers
                "matched": branch_customer_matched,     # backward compat (customers)
                "modified": branch_customer_modified,   # backward compat (customers)
                "matched_customers": branch_customer_matched,
                "modified_customers": branch_customer_modified,
                "old_price_samples_customers": customer_old_prices,
                # products
                "matched_products": branch_product_matched,
                "modified_products": branch_product_modified,
                "old_price_samples_products": product_old_prices,
                # meta
                "included_customers": [
                    str(cid) for cid in included_customer_oids
                ] if included_customer_oids else [],
                "actor": actor,
            }
        )

    if log_docs:
        price_logs_col.insert_many(log_docs)
    if total_modified_products:
        from Backend.inventory.product_cards_store import invalidate_product_card_cache
        invalidate_product_card_cache()

    return jsonify(
        {
            "ok": True,
            "scope": scope_raw,
            "matched": total_matched_customers,          # backward compatible
            "modified": total_modified_customers,        # backward compatible
            "matched_customers": total_matched_customers,
            "modified_customers": total_modified_customers,
            "matched_products": total_matched_products,
            "modified_products": total_modified_products,
        }
    )
