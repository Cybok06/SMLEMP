# orders.py  (manager_orders_bp)
from datetime import datetime, timedelta
from uuid import uuid4

from bson import ObjectId
from flask import Blueprint, abort, jsonify, render_template, request, session

from db import db
from services.activity_audit import audit_action

manager_orders_bp = Blueprint(
    "manager_orders",
    __name__,
    template_folder="../../templates/manager",
    url_prefix="/manager/orders",
)

orders_col = db["orders"]
catalog_col = db["catalog_items"]
order_events_col = db["order_events"]
users_col = db["users"]
inventory_products_col = db["inventory_products"]
inventory_locations_col = db["inventory_branch_locations"]

# --------- indexes ----------
try:
    inventory_products_col.create_index([("name", 1), ("category", 1), ("brand", 1)], background=True)
    inventory_products_col.create_index([("sku", 1)], background=True)
    orders_col.create_index([("manager_id", 1), ("updated_at", -1)], background=True)
except Exception:
    pass


def _oid(v):
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _require_manager():
    mid = session.get("manager_id")
    if not mid:
        abort(401, "Sign in as manager.")
    oid = _oid(mid)
    mgr = users_col.find_one({"_id": oid, "role": "manager"})
    if not mgr:
        abort(403, "Unauthorized.")
    return mgr


def _iso_timestamp(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _manager_inventory_filter(mgr):
    return {}


def _manager_scope(mgr):
    return {
        "branch": str((mgr or {}).get("branch") or "").strip(),
    }


def _entry_matches_manager_scope(entry, mgr):
    scope = _manager_scope(mgr)
    manager_branch = scope["branch"].lower()

    entry_branch = str((entry or {}).get("branch") or "").strip().lower()
    if manager_branch:
        return manager_branch == entry_branch
    return True


def _manager_destination_locations(mgr):
    branch = str((mgr or {}).get("branch") or "").strip()
    if not branch:
        return []
    rows = list(
        inventory_locations_col.find(
            {"branch": branch},
            {"name": 1, "code": 1, "branch": 1, "status": 1, "type": 1},
        ).sort([("name", 1), ("_id", 1)])
    )
    return [
        {
            "id": str(row.get("_id") or ""),
            "name": row.get("name") or "Warehouse",
            "code": row.get("code") or "",
            "branch": row.get("branch") or branch,
            "status": row.get("status") or "",
            "type": row.get("type") or "",
            "label": f"{row.get('name') or 'Warehouse'} ({row.get('code')})"
            if row.get("code")
            else (row.get("name") or "Warehouse"),
        }
        for row in rows
    ]


def _map_inventory_product(doc, mgr):
    entries = [entry for entry in (doc.get("entries") or []) if isinstance(entry, dict)]
    network_total_stock = sum(max(0, int((entry or {}).get("quantity") or 0)) for entry in entries)
    scoped_entries = [entry for entry in entries if _entry_matches_manager_scope(entry, mgr)]
    total_stock = sum(max(0, int((entry or {}).get("quantity") or 0)) for entry in scoped_entries)

    locations = []
    seen_locations = set()
    source_location_map = {}
    for entry in scoped_entries:
        qty = max(0, int((entry or {}).get("quantity") or 0))
        if qty <= 0:
            continue
        location_name = str(entry.get("location_name") or "").strip()
        location_code = str(entry.get("location_code") or "").strip()
        location_id = str(entry.get("location_id") or "").strip()
        branch = str(entry.get("branch") or "").strip()
        label = location_name or "Warehouse"
        if location_code and location_code.lower() != label.lower():
            label = f"{label} ({location_code})"
        if label not in seen_locations:
            seen_locations.add(label)
            locations.append(label)
        if location_id:
            bucket = source_location_map.setdefault(
                location_id,
                {
                    "id": location_id,
                    "branch": branch,
                    "name": location_name or "Warehouse",
                    "code": location_code,
                    "label": label,
                    "availableQty": 0,
                },
            )
            bucket["availableQty"] += qty

    tag_parts = [value for value in [doc.get("category"), doc.get("brand")] if value]
    if locations:
        suffix = f" +{len(locations) - 2}" if len(locations) > 2 else ""
        tag_parts.append(f"Warehouse: {', '.join(locations[:2])}{suffix}")
    source_locations = list(source_location_map.values())
    source_locations.sort(key=lambda item: (-int(item.get("availableQty") or 0), str(item.get("label") or "")))

    return {
        "_id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "qty_available": total_stock,
        "network_qty_available": network_total_stock,
        "image_url": doc.get("image_url"),
        "sku": doc.get("sku") or None,
        "tag": " | ".join(tag_parts) or "Inventory Product",
        "source_locations": source_locations,
    }


@manager_orders_bp.route("/products", methods=["GET"])
def products_search():
    """
    Products for creating orders are fetched from the shared inventory_products collection.
    Supports:
      - q empty => returns top items
      - q provided => regex search on name/sku/category/brand
    """
    mgr = _require_manager()
    q = (request.args.get("q") or "").strip()

    base = _manager_inventory_filter(mgr)

    try:
        limit = min(80, max(10, int(request.args.get("limit", 30))))
    except Exception:
        limit = 30

    projection = {
        "name": 1,
        "image_url": 1,
        "sku": 1,
        "category": 1,
        "brand": 1,
        "entries": 1,
    }

    if q:
        search_filters = [
            {"name": {"$regex": q, "$options": "i"}},
            {"sku": {"$regex": q, "$options": "i"}},
            {"category": {"$regex": q, "$options": "i"}},
            {"brand": {"$regex": q, "$options": "i"}},
        ]
        match = {"$and": [base, {"$or": search_filters}]}
    else:
        match = base

    docs = list(inventory_products_col.find(match, projection).sort([("name", 1), ("_id", 1)]).limit(limit))
    return jsonify(ok=True, results=[_map_inventory_product(d, mgr) for d in docs])


@manager_orders_bp.route("/products_prefetch", methods=["GET"])
def products_prefetch():
    mgr = _require_manager()
    try:
        limit = min(500, max(50, int(request.args.get("limit", 250))))
    except Exception:
        limit = 250
    try:
        skip = max(0, int(request.args.get("skip", 0)))
    except Exception:
        skip = 0

    projection = {
        "name": 1,
        "image_url": 1,
        "sku": 1,
        "category": 1,
        "brand": 1,
        "entries": 1,
    }

    base_filter = _manager_inventory_filter(mgr)
    total = inventory_products_col.count_documents(base_filter)

    docs = list(
        inventory_products_col.find(base_filter, projection).sort([("name", 1), ("_id", 1)]).skip(skip).limit(limit)
    )
    return jsonify(
        ok=True,
        results=[_map_inventory_product(d, mgr) for d in docs],
        total=total,
        skip=skip,
        limit=limit,
    )


@manager_orders_bp.route("/", methods=["GET"])
def orders_page():
    _require_manager()
    return render_template("orders_list.html")


@manager_orders_bp.route("/list", methods=["GET"])
def list_orders():
    mgr = _require_manager()

    status = (request.args.get("status") or "").strip().lower() or None
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    sort = (request.args.get("sort") or "desc").lower()

    q = {"manager_id": mgr["_id"]}
    if status:
        q["status"] = status

    if date_from or date_to:
        dr = {}
        if date_from:
            try:
                dr["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
            except Exception:
                pass
        if date_to:
            try:
                dr["$lt"] = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            except Exception:
                pass
        if dr:
            q["updated_at"] = dr

    sort_spec = [("updated_at", 1 if sort == "asc" else -1)]
    docs = list(orders_col.find(q).sort(sort_spec).limit(300))

    res = []
    for d in docs:
        items = []
        for it in d.get("items", []) or []:
            qty = int(it.get("qty", 0) or 0)
            delivered_qty = int(it.get("delivered_qty", 0) or 0)
            rejected_qty = int(it.get("rejected_qty", 0) or 0)
            remaining_qty = max(0, qty - delivered_qty)
            items.append(
                {
                    "line_id": it.get("line_id"),
                    "product_id": str(it.get("product_id")) if it.get("product_id") else None,
                    "name": it.get("name"),
                    "sku": it.get("sku") or it.get("code"),
                    "qty": qty,
                    "delivered_qty": delivered_qty,
                    "rejected_qty": rejected_qty,
                    "remaining_qty": remaining_qty,
                    "status": it.get("status"),
                    "expected_date": it.get("expected_date"),
                    "notes": it.get("notes"),
                    "decision_note": it.get("decision_note") or "",
                }
            )

        res.append(
            {
                "_id": str(d["_id"]),
                "status": d.get("status", "open"),
                "notes": d.get("notes", ""),
                "created_at": _iso_timestamp(d.get("created_at")),
                "updated_at": _iso_timestamp(d.get("updated_at")),
                "items": items,
            }
        )

    return jsonify(ok=True, results=res)


@manager_orders_bp.route("/catalog", methods=["GET"])
def catalog_suggest():
    _require_manager()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(ok=True, results=[])
    results = list(catalog_col.find({"name": {"$regex": q, "$options": "i"}}).limit(20))
    return jsonify(ok=True, results=[r["name"] for r in results])


@manager_orders_bp.route("/create", methods=["GET"])
def create_page():
    mgr = _require_manager()
    return render_template(
        "order_create.html",
        destination_locations=_manager_destination_locations(mgr),
        manager_id=str(mgr.get("_id") or ""),
    )


@manager_orders_bp.route("/create", methods=["POST"])
@audit_action("order.created", "Created Order", entity_type="order")
def create_order():
    mgr = _require_manager()
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    manual_items = data.get("manual_items") or []
    notes = (data.get("notes") or "").strip()
    branch = (data.get("branch") or mgr.get("branch") or "").strip()

    if not items and not manual_items:
        return jsonify(ok=False, message="Add at least one item."), 400

    shaped = []
    for raw in items:
        product_id = (raw.get("product_id") or "").strip()
        try:
            qty = int(raw.get("qty") or 0)
        except Exception:
            qty = 0

        exp = (raw.get("expected_date") or "").strip()
        line_notes = (raw.get("notes") or "").strip()
        destination_location_id = (raw.get("destination_location_id") or "").strip()

        if not product_id or qty <= 0:
            return jsonify(ok=False, message="Each line must include a product and quantity."), 400

        pid = _oid(product_id)
        if not pid:
            return jsonify(ok=False, message="Invalid product reference provided."), 400

        inv_doc = inventory_products_col.find_one(
            {"_id": pid},
            {"name": 1, "sku": 1, "entries": 1},
        )
        if not inv_doc:
            return jsonify(ok=False, message="Selected product was not found in inventory products."), 400

        destination_location_doc = None
        if destination_location_id:
            destination_location_doc = inventory_locations_col.find_one(
                {"_id": _oid(destination_location_id)},
                {"name": 1, "code": 1, "branch": 1, "status": 1, "type": 1},
            )
        if not destination_location_id or not destination_location_doc:
            return jsonify(ok=False, message=f"Select a destination warehouse for {inv_doc.get('name') or 'this product'}."), 400
        destination_branch = str(destination_location_doc.get("branch") or "").strip()
        if destination_branch != branch:
            return jsonify(ok=False, message="Selected destination warehouse must belong to your branch."), 400

        shaped.append(
            {
                "line_id": str(uuid4()),
                "product_id": pid,
                "source_collection": "inventory_products",
                "destination_location_id": destination_location_id,
                "destination_location_name": destination_location_doc.get("name") or "",
                "destination_location_code": destination_location_doc.get("code") or "",
                "destination_branch": destination_branch,
                "name": inv_doc.get("name"),
                "sku": inv_doc.get("sku"),
                "qty": qty,
                "delivered_qty": 0,
                "remaining_qty": qty,
                "status": "pending",
                "expected_date": exp or None,
                "delivered_at": None,
                "postponements": [],
                "notes": line_notes,
            }
        )

    manual_shaped = []
    for raw in manual_items:
        name = (raw.get("name") or "").strip()
        try:
            qty = int(raw.get("qty") or 0)
        except Exception:
            qty = 0
        line_notes = (raw.get("notes") or "").strip()

        if not name:
            return jsonify(ok=False, message="Manual item name is required."), 400
        if qty <= 0:
            return jsonify(ok=False, message="Manual item quantity must be at least 1."), 400

        manual_shaped.append({"line_id": str(uuid4()), "name": name, "qty": qty, "notes": line_notes})

    now = datetime.utcnow()
    doc = {
        "manager_id": mgr["_id"],
        "branch": branch,
        "status": "open",
        "notes": notes,
        "items": shaped,
        "manual_items": manual_shaped,
        "created_at": now,
        "updated_at": now,
    }
    ins = orders_col.insert_one(doc)

    event_items = []
    for line in shaped:
        event_items.append(
            {
                "line_id": line.get("line_id"),
                "product_id": str(line.get("product_id")) if line.get("product_id") else None,
                "name": line.get("name"),
                "qty": line.get("qty"),
                "expected_date": line.get("expected_date"),
                "notes": line.get("notes"),
            }
        )

    manual_event_items = []
    for line in manual_shaped:
        manual_event_items.append(
            {"line_id": line.get("line_id"), "name": line.get("name"), "qty": line.get("qty"), "notes": line.get("notes")}
        )

    try:
        order_events_col.insert_one(
            {
                "order_id": ins.inserted_id,
                "type": "create",
                "by": str(mgr["_id"]),
                "role": "manager",
                "payload": {
                    "branch": branch,
                    "notes": notes,
                    "items": event_items,
                    "manual_items": manual_event_items,
                },
                "at": now,
            }
        )
    except Exception:
        pass

    return jsonify(ok=True, order_id=str(ins.inserted_id))


@manager_orders_bp.route("/debug/products_count", methods=["GET"])
def debug_products_count():
    mgr = _require_manager()
    branch = str((mgr or {}).get("branch") or "").strip()
    query = _manager_inventory_filter(mgr)
    total = inventory_products_col.count_documents(query)
    sample = list(inventory_products_col.find(query, {"name": 1, "entries": 1, "image_url": 1}).limit(5))
    for s in sample:
        s["_id"] = str(s["_id"])
    return jsonify(ok=True, branch=branch, manager_total=total, manager_available=total, sample=sample)
