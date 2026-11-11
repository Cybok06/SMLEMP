from collections import defaultdict
from datetime import datetime

from bson.objectid import ObjectId
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from cache_ext import cache
from db import db

manager_inventory_bp = Blueprint("manager_inventory", __name__)

# Collections
inventory_products_col = db["inventory_products"]
users_col = db.users
history_col = db.inventory_history
settings_col = db.inventory_settings

DEFAULT_REORDER_LEVEL = 20


def _ensure_settings_indexes():
    try:
        settings_col.create_index([("manager_id", 1)])
        settings_col.create_index([("updated_at", -1)])
    except Exception:
        pass


_ensure_settings_indexes()


def _is_ajax_request():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_reorder_level(manager_id):
    """
    Resolve reorder level for a manager.
    Priority: manager-specific -> global (manager_id=None) -> DEFAULT_REORDER_LEVEL.
    """
    doc = None
    if manager_id:
        doc = settings_col.find_one({"manager_id": manager_id}, sort=[("updated_at", -1)])
    if not doc:
        doc = settings_col.find_one({"manager_id": None}, sort=[("updated_at", -1)])
    level = doc.get("reorder_level") if doc else None
    level_int = _safe_int(level, DEFAULT_REORDER_LEVEL)
    if level_int < 0:
        level_int = DEFAULT_REORDER_LEVEL
    return level_int


def _get_manager_oid():
    if "manager_id" not in session:
        return None
    try:
        return ObjectId(session["manager_id"])
    except Exception:
        return None


def _get_manager_doc():
    manager_id = _get_manager_oid()
    if not manager_id:
        return None
    return users_col.find_one(
        {"_id": manager_id, "role": "manager"},
        {"name": 1, "branch": 1, "location": 1, "username": 1},
    )


def _normalize_entry(entry):
    return {
        "branch": str((entry or {}).get("branch") or "").strip(),
        "location_id": str((entry or {}).get("location_id") or "").strip(),
        "location_name": str((entry or {}).get("location_name") or "").strip(),
        "location_code": str((entry or {}).get("location_code") or "").strip(),
        "quantity": _safe_int((entry or {}).get("quantity"), 0),
        "selling_price": _safe_float((entry or {}).get("selling_price"), 0),
        "updated_at": (entry or {}).get("updated_at"),
        "created_at": (entry or {}).get("created_at"),
    }


def _entry_sort_ts(entry):
    return entry.get("updated_at") or entry.get("created_at") or datetime.min


def _location_label(location_name, location_code):
    name = str(location_name or "").strip() or "Warehouse"
    code = str(location_code or "").strip()
    if code and code.lower() != name.lower():
        return f"{name} ({code})"
    return name


def _build_location_summary(location_rows):
    labels = []
    for row in location_rows:
        qty = _safe_int(row.get("quantity"), 0)
        labels.append(f"{row.get('label')}: {qty}")
    if not labels:
        return "No warehouse location"
    if len(labels) <= 2:
        return " | ".join(labels)
    return f"{' | '.join(labels[:2])} | +{len(labels) - 2} more"


def _pick_primary_location(location_rows, manager_doc):
    if not location_rows:
        return None

    manager_location = str((manager_doc or {}).get("location") or "").strip().lower()
    if manager_location:
        for row in location_rows:
            label = str(row.get("label") or "").lower()
            name = str(row.get("location_name") or "").lower()
            code = str(row.get("location_code") or "").lower()
            if manager_location in {label, name, code}:
                return row

    return location_rows[0]


def _manager_scope(manager_doc):
    return {
        "branch": str((manager_doc or {}).get("branch") or "").strip(),
    }


def _entry_matches_manager_scope(entry, manager_doc):
    scope = _manager_scope(manager_doc)
    manager_branch = scope["branch"].lower()

    entry_branch = str(entry.get("branch") or "").strip().lower()
    if manager_branch:
        return manager_branch == entry_branch
    return True


def _get_manager_inventory_payload(manager_doc, selected_category="all"):
    scope = _manager_scope(manager_doc)
    branch_name = scope["branch"]
    query = {}
    if branch_name:
        query["entries.branch"] = branch_name
    if selected_category and selected_category != "all":
        query["category"] = selected_category

    projection = {
        "name": 1,
        "category": 1,
        "brand": 1,
        "description": 1,
        "image_url": 1,
        "sku": 1,
        "entries": 1,
    }

    docs = list(inventory_products_col.find(query, projection).sort([("name", 1), ("_id", 1)]))

    items = []
    categories = set()
    for doc in docs:
        entries = [_normalize_entry(entry) for entry in (doc.get("entries") or [])]
        scoped_entries = [entry for entry in entries if _entry_matches_manager_scope(entry, manager_doc)]
        if not scoped_entries:
            continue

        category = str(doc.get("category") or "").strip()
        if category:
            categories.add(category)

        total_qty = sum(entry.get("quantity", 0) for entry in scoped_entries)
        latest_entry = max(scoped_entries, key=_entry_sort_ts)
        price = _safe_float(latest_entry.get("selling_price"), 0)

        location_buckets = defaultdict(
            lambda: {
                "location_id": "",
                "location_name": "",
                "location_code": "",
                "label": "",
                "quantity": 0,
            }
        )

        for entry in scoped_entries:
            key = entry.get("location_id") or f"{entry.get('location_name')}::{entry.get('location_code')}"
            bucket = location_buckets[key]
            bucket["location_id"] = entry.get("location_id") or bucket["location_id"]
            bucket["location_name"] = entry.get("location_name") or bucket["location_name"]
            bucket["location_code"] = entry.get("location_code") or bucket["location_code"]
            bucket["label"] = _location_label(bucket["location_name"], bucket["location_code"])
            bucket["quantity"] += entry.get("quantity", 0)

        locations = [row for row in location_buckets.values() if row.get("label")]
        locations.sort(key=lambda row: (-_safe_int(row.get("quantity"), 0), str(row.get("label") or "").lower()))
        primary_location = _pick_primary_location(locations, manager_doc)

        items.append(
            {
                "_id": str(doc.get("_id") or ""),
                "name": doc.get("name") or "",
                "category": category or "Product",
                "brand": doc.get("brand") or "",
                "description": doc.get("description") or "",
                "price": price if price > 0 else None,
                "qty": total_qty,
                "image_url": doc.get("image_url") or "https://placehold.co/640x480?text=Inventory",
                "sku": doc.get("sku") or "",
                "branch": branch_name,
                "location_summary": _build_location_summary(locations),
                "warehouse_location": (primary_location or {}).get("label") or "No warehouse location",
                "location_count": len(locations),
                "locations": locations,
            }
        )

    return {
        "items": items,
        "categories": sorted(categories),
        "branch_name": branch_name,
        "scope_label": branch_name,
    }


def _render_inventory_page(manager_doc, selected_category="all", low_stock_only=False):
    manager_id = manager_doc["_id"]
    reorder_level = get_reorder_level(manager_id)
    payload = _get_manager_inventory_payload(manager_doc, selected_category)
    inventory_items = payload["items"]
    if low_stock_only:
        inventory_items = [item for item in inventory_items if _safe_int(item.get("qty"), 0) <= reorder_level]

    low_stock_ids = [item.get("_id") for item in inventory_items if _safe_int(item.get("qty"), 0) <= reorder_level]

    return render_template(
        "manager_inventory.html",
        inventory_items=inventory_items,
        categories=payload["categories"],
        selected_category=selected_category,
        managers=[],
        reorder_level=reorder_level,
        low_stock_count=len([item for item in payload["items"] if _safe_int(item.get("qty"), 0) <= reorder_level]),
        low_stock_ids=low_stock_ids,
        page_title="Low Stock Products" if low_stock_only else "Branch Warehouse Inventory",
        page_subtitle=(
            f"Qty at or below {reorder_level} across {payload['scope_label'] or 'your branch warehouses'}."
            if low_stock_only
            else f"Live stock from inventory_products for {payload['scope_label'] or 'your branch'}, grouped across warehouses linked to your branch."
        ),
        low_stock_only=low_stock_only,
        allow_transfer=False,
        branch_name=payload["branch_name"],
    )


@manager_inventory_bp.route("/manager/inventory")
def view_manager_inventory():
    manager_doc = _get_manager_doc()
    if not manager_doc:
        return redirect(url_for("login.login"))

    selected_category = request.args.get("category", "all")
    return _render_inventory_page(manager_doc, selected_category=selected_category, low_stock_only=False)


@manager_inventory_bp.route("/manager/inventory/settings")
def manager_inventory_settings():
    manager_id = _get_manager_oid()
    if not manager_id:
        return redirect(url_for("login.login"))

    reorder_level = get_reorder_level(manager_id)
    return jsonify(ok=True, reorder_level=reorder_level)


@manager_inventory_bp.route("/manager/inventory/settings/reorder-level", methods=["POST"])
def manager_inventory_settings_reorder():
    manager_id = _get_manager_oid()
    if not manager_id:
        return jsonify(ok=False, message="Session expired. Please login again."), 401

    level_raw = request.form.get("reorder_level")
    if level_raw is None and request.is_json:
        payload = request.get_json(silent=True) or {}
        level_raw = payload.get("reorder_level")

    try:
        level = int(level_raw)
    except (TypeError, ValueError):
        return jsonify(ok=False, message="Reorder level must be an integer."), 400

    if level < 0:
        return jsonify(ok=False, message="Reorder level must be 0 or greater."), 400

    manager_doc = users_col.find_one({"_id": manager_id}, {"name": 1, "branch": 1}) or {}

    settings_col.update_one(
        {"manager_id": manager_id},
        {
            "$set": {
                "manager_id": manager_id,
                "branch_name": manager_doc.get("branch"),
                "reorder_level": level,
                "updated_by": session.get("manager_name") or str(manager_id),
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )

    return jsonify(ok=True, reorder_level=level)


@manager_inventory_bp.route("/manager/inventory/low-stocks")
def manager_low_stocks():
    manager_doc = _get_manager_doc()
    if not manager_doc:
        return redirect(url_for("login.login"))
    return _render_inventory_page(manager_doc, selected_category="all", low_stock_only=True)


@manager_inventory_bp.route("/manager/inventory/low-stocks/count")
def manager_low_stocks_count():
    manager_doc = _get_manager_doc()
    if not manager_doc:
        return jsonify(ok=False, message="Session expired. Please login again."), 401

    reorder_level = get_reorder_level(manager_doc["_id"])
    branch_name = _manager_scope(manager_doc)["branch"]
    cache_key = f"manager_low_stocks_count:{manager_doc['_id']}:{branch_name}:{reorder_level}"
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(ok=True, reorder_level=reorder_level, low_stock_count=cached)

    qty_expr = {
        "$sum": {
            "$map": {
                "input": {
                    "$filter": {
                        "input": {"$ifNull": ["$entries", []]},
                        "as": "entry",
                        "cond": {"$eq": ["$$entry.branch", branch_name]},
                    }
                },
                "as": "entry",
                "in": {"$convert": {"input": "$$entry.quantity", "to": "int", "onError": 0, "onNull": 0}},
            }
        }
    } if branch_name else {
        "$sum": {
            "$map": {
                "input": {"$ifNull": ["$entries", []]},
                "as": "entry",
                "in": {"$convert": {"input": "$$entry.quantity", "to": "int", "onError": 0, "onNull": 0}},
            }
        }
    }

    try:
        query = {"entries.branch": branch_name} if branch_name else {"entries.0": {"$exists": True}}
        rows = list(inventory_products_col.aggregate([
            {"$match": query},
            {"$project": {"qty": qty_expr}},
            {"$match": {"qty": {"$lte": reorder_level}}},
            {"$count": "count"},
        ]))
        count = int(rows[0]["count"]) if rows else 0
    except Exception:
        payload = _get_manager_inventory_payload(manager_doc, "all")
        count = len([item for item in payload["items"] if _safe_int(item.get("qty"), 0) <= reorder_level])

    cache.set(cache_key, count, timeout=20)

    return jsonify(ok=True, reorder_level=reorder_level, low_stock_count=count)


@manager_inventory_bp.route("/manager/inventory/transfer", methods=["POST"])
def transfer_manager_inventory():
    is_ajax = _is_ajax_request()
    message = "Direct manager transfers are disabled on shared warehouse stock. Use the order request workflow instead."
    if is_ajax:
        return jsonify({"success": False, "message": message}), 409
    flash(message, "warning")
    return redirect(url_for("manager_inventory.view_manager_inventory"))


@manager_inventory_bp.route("/manager/inventory/transfer_history")
def transfer_history():
    if "manager_id" not in session:
        return jsonify({"success": False, "message": "Session expired. Please login again."}), 401

    try:
        manager_id = ObjectId(session["manager_id"])
    except Exception:
        return jsonify({"success": False, "message": "Invalid session."}), 400

    logs = list(
        history_col.find(
            {
                "log_type": "TRANSFER",
                "$or": [{"from_manager_id": manager_id}, {"to_manager_id": manager_id}],
            }
        ).sort("created_at", -1)
    )

    payload = []
    for log in logs:
        direction = "OUT" if log.get("from_manager_id") == manager_id else "IN"
        payload.append(
            {
                "log_type": log.get("log_type", ""),
                "product_name": log.get("product_name", ""),
                "direction": direction,
                "qty_moved": log.get("qty_moved", 0),
                "from_manager_name": log.get("from_manager_name", ""),
                "from_branch": log.get("from_branch", ""),
                "to_manager_name": log.get("to_manager_name", ""),
                "to_branch": log.get("to_branch", ""),
                "source_before_qty": log.get("source_before_qty", 0),
                "source_after_qty": log.get("source_after_qty", 0),
                "dest_before_qty": log.get("dest_before_qty", 0),
                "dest_after_qty": log.get("dest_after_qty", 0),
                "created_at": log.get("created_at").isoformat() if log.get("created_at") else "",
            }
        )

    return jsonify(payload)
