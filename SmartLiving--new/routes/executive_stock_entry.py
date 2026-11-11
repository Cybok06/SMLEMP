# routes/executive_stock_entry.py
from __future__ import annotations

from flask import (
    Blueprint, render_template, request,
    jsonify, session, redirect, url_for
)
from bson.objectid import ObjectId
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any, List
import re

from db import db
from services.activity_audit import audit_action

executive_stock_entry_bp = Blueprint(
    "executive_stock_entry",
    __name__,
    url_prefix="/executive-stock"
)

users_col  = db["users"]
stock_col  = db["stock_entries"]   # new collection for stock purchases
purchase_orders_col = db["inventory_purchase_orders"]


# ----------------- Helpers -----------------
def _current_exec_session() -> Tuple[Optional[str], Optional[str]]:
    """
    Allow executive or admin to access this page.
    Returns (session_key, user_id) or (None, None).
    """
    if session.get("executive_id"):
        return "executive_id", session["executive_id"]
    if session.get("admin_id"):
        return "admin_id", session["admin_id"]
    return None, None


def _ensure_exec_or_redirect():
    """
    Return (user_id_str, user_doc) if valid executive/admin.
    Else redirect to login.
    """
    _, uid = _current_exec_session()
    if not uid:
        return redirect(url_for("login.login"))

    # Handle ObjectId and string id
    user = None
    try:
        user = users_col.find_one({"_id": ObjectId(uid)})
    except Exception:
        user = users_col.find_one({"_id": uid})

    if not user:
        return redirect(url_for("login.login"))

    role = (user.get("role") or "").lower()
    if role not in ("executive", "admin"):
        return redirect(url_for("login.login"))

    return str(user["_id"]), user


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        # date-only input: YYYY-MM-DD
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None


def _build_match_from_request(args) -> Dict[str, Any]:
    """
    Build a MongoDB match filter from query args.
    Filters: start, end, name
    - If no start/end, default is last 30 days.
    """
    now = datetime.utcnow()

    start = _parse_date(args.get("start"))
    end   = _parse_date(args.get("end"))

    if not start and not end:
        # default last 30 days
        end = now
        start = now - timedelta(days=30)
    elif start and not end:
        # if only start → up to now
        end = now
    elif end and not start:
        # if only end → 30 days before end
        start = end - timedelta(days=30)

    # make end inclusive by going to next day
    end = end + timedelta(days=1)

    match: Dict[str, Any] = {
        "purchased_at": {"$gte": start, "$lt": end}
    }

    name = (args.get("name") or "").strip()
    if name:
        match["name"] = name

    return match


def _recent_item_names(limit: int = 15) -> List[str]:
    """
    Last N distinct item names for the datalist suggestions.
    """
    cursor = stock_col.find(
        {"name": {"$exists": True, "$ne": ""}},
        {"name": 1, "created_at": 1}
    ).sort("created_at", -1).limit(limit * 3)  # fetch a bit more, dedupe in Python

    seen = set()
    names: List[str] = []
    for doc in cursor:
        nm = (doc.get("name") or "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
        if len(names) >= limit:
            break
    return names


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _whole_number(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _purchase_order_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    items = []
    receipts = doc.get("receipts") or []
    warehouses_by_product: Dict[str, set[str]] = {}
    for receipt in receipts:
        legacy_location_name = receipt.get("location_name") or ""
        for receipt_line in receipt.get("items") or []:
            product_id = str(receipt_line.get("product_id") or "")
            location_name = receipt_line.get("location_name") or legacy_location_name
            branch = receipt_line.get("branch") or receipt.get("branch") or ""
            if location_name:
                label = f"{branch} - {location_name}" if branch else str(location_name)
                warehouses_by_product.setdefault(product_id, set()).add(label)
    expected_cost = 0.0
    received_value = 0.0
    remaining_value = 0.0
    ordered_quantity = 0
    received_quantity = 0
    rejected_quantity = 0
    for line in doc.get("items") or []:
        ordered = _whole_number(line.get("quantity_ordered"))
        received = _whole_number(line.get("quantity_received"))
        rejected = _whole_number(line.get("quantity_rejected"))
        unit_cost = _number(line.get("unit_cost"))
        remaining = max(ordered - received - rejected, 0)
        expected_cost += ordered * unit_cost
        received_value += received * unit_cost
        remaining_value += remaining * unit_cost
        ordered_quantity += ordered
        received_quantity += received
        rejected_quantity += rejected
        items.append({
            "product_id": str(line.get("product_id") or ""),
            "product": line.get("product_name") or "Unknown item",
            "sku": line.get("sku") or "",
            "ordered": ordered,
            "received": received,
            "rejected": rejected,
            "remaining": remaining,
            "unit_cost": round(unit_cost, 2),
            "line_total": round(ordered * unit_cost, 2),
            "received_value": round(received * unit_cost, 2),
            "status": line.get("status") or "Not Delivered",
            "warehouses": sorted(warehouses_by_product.get(str(line.get("product_id") or ""), set())),
        })

    created_at = doc.get("created_at")
    created_date = created_at.strftime("%Y-%m-%d") if isinstance(created_at, datetime) else ""
    created_time = created_at.strftime("%H:%M") if isinstance(created_at, datetime) else ""
    created_by = doc.get("created_by") or {}
    approved_by = doc.get("approved_by") or {}
    sent_by = doc.get("sent_by") or {}
    latest_receipt = receipts[-1] if receipts else {}
    received_by = latest_receipt.get("received_by") or {}
    return {
        "_id": str(doc.get("_id") or ""),
        "po_number": doc.get("po_number") or "",
        "supplier": doc.get("supplier_name") or "",
        "supplier_phone": doc.get("supplier_phone") or "",
        "supplier_location": doc.get("supplier_location") or "",
        "status": str(doc.get("status") or "draft").lower(),
        "branch": doc.get("branch") or "",
        "expected_delivery": doc.get("expected_delivery") or "",
        "created_date": created_date,
        "created_time": created_time,
        "created_by": created_by.get("name") or "-",
        "approved_by": approved_by.get("name") or "",
        "sent_by": sent_by.get("name") or "",
        "received_by": received_by.get("name") or "",
        "expected_cost": round(expected_cost, 2),
        "received_value": round(received_value, 2),
        "remaining_value": round(remaining_value, 2),
        "ordered_quantity": ordered_quantity,
        "received_quantity": received_quantity,
        "rejected_quantity": rejected_quantity,
        "remaining_quantity": max(ordered_quantity - received_quantity - rejected_quantity, 0),
        "items_count": len(items),
        "receipts_count": len(receipts),
        "trigger": doc.get("trigger") or "",
        "notes": doc.get("notes") or "",
        "items": items,
    }


# ----------------- Page -----------------
@executive_stock_entry_bp.route("/", methods=["GET"])
def stock_entry_page():
    """
    Executive Stock Entry dashboard:
      - Stock entry form
      - Summary KPIs
      - Charts (loaded via AJAX)
      - Recent entries table
    """
    scope = _ensure_exec_or_redirect()
    if not isinstance(scope, tuple):
        return scope
    exec_id, exec_doc = scope

    # Recent entries for default table (last 30 days)
    now = datetime.utcnow()
    start_30 = now - timedelta(days=30)
    recent_docs = list(
        stock_col.find(
            {"purchased_at": {"$gte": start_30, "$lt": now + timedelta(days=1)}}
        )
        .sort("purchased_at", -1)
        .limit(100)
    )

    rows = []
    total_30 = 0.0
    total_all_time = 0.0

    # All-time total
    agg_all = list(
        stock_col.aggregate([
            {"$group": {"_id": None, "sum_total": {"$sum": {"$ifNull": ["$total_cost", 0]}}}}
        ])
    )
    if agg_all:
        total_all_time = float(agg_all[0].get("sum_total", 0.0) or 0.0)

    for d in recent_docs:
        qty = float(d.get("quantity", 0) or 0)
        unit_price = float(d.get("unit_price", 0) or 0)
        total = float(d.get("total_cost", qty * unit_price) or 0)
        total_30 += total

        dt = d.get("purchased_at") or d.get("created_at")
        if isinstance(dt, datetime):
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")
        else:
            date_str = ""
            time_str = ""

        rows.append({
            "_id": str(d["_id"]),
            "name": d.get("name", ""),
            "quantity": qty,
            "unit_price": unit_price,
            "total_cost": total,
            "description": d.get("description", ""),
            "date": date_str,
            "time": time_str,
        })

    recent_names = _recent_item_names()

    return render_template(
        "executive_stock_entry.html",
        executive_name=exec_doc.get("name", "Executive"),
        today=datetime.utcnow().strftime("%Y-%m-%d"),
        rows=rows,
        total_30=f"{total_30:,.2f}",
        total_all_time=f"{total_all_time:,.2f}",
        recent_names=recent_names,
    )


# ----------------- Add Entry -----------------
@executive_stock_entry_bp.route("/add", methods=["POST"])
@audit_action("stock_entry.created", "Created Stock Entry", entity_type="inventory")
def add_stock_entry():
    scope = _ensure_exec_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Please log in."), 401
    exec_id, _ = scope

    form = request.form
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    qty_str = form.get("quantity") or "0"
    unit_price_str = form.get("unit_price") or "0"
    total_cost_str = form.get("total_cost") or ""

    date_str = form.get("date") or ""
    dt = _parse_date(date_str) or datetime.utcnow()

    try:
        quantity = float(qty_str)
    except Exception:
        quantity = 0.0

    try:
        unit_price = float(unit_price_str)
    except Exception:
        unit_price = 0.0

    if total_cost_str:
        try:
            total_cost = float(total_cost_str)
        except Exception:
            total_cost = quantity * unit_price
    else:
        total_cost = quantity * unit_price

    if not name:
        return jsonify(ok=False, message="Item name is required."), 400

    doc = {
        "name": name,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_cost": total_cost,
        "purchased_at": dt,
        "created_at": datetime.utcnow(),
        "created_by": exec_id,
    }

    stock_col.insert_one(doc)
    return jsonify(ok=True, message="Stock entry saved.")


# ----------------- Inventory Purchase Orders (read-only executive view) -----------------
@executive_stock_entry_bp.route("/purchase-orders", methods=["GET"])
def list_purchase_orders():
    scope = _ensure_exec_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Please log in."), 401

    match: Dict[str, Any] = {}
    status = (request.args.get("status") or "").strip().lower()
    supplier = (request.args.get("supplier") or "").strip()
    branch = (request.args.get("branch") or "").strip()
    search = (request.args.get("q") or "").strip()
    start = _parse_date(request.args.get("start"))
    end = _parse_date(request.args.get("end"))

    if status and status != "all":
        match["status"] = status
    if supplier:
        match["supplier_name"] = {"$regex": re.escape(supplier), "$options": "i"}
    if branch:
        match["branch"] = branch
    if search:
        escaped = re.escape(search)
        match["$or"] = [
            {"po_number": {"$regex": escaped, "$options": "i"}},
            {"supplier_name": {"$regex": escaped, "$options": "i"}},
            {"items.product_name": {"$regex": escaped, "$options": "i"}},
            {"items.sku": {"$regex": escaped, "$options": "i"}},
        ]
    if start or end:
        created_range: Dict[str, Any] = {}
        if start:
            created_range["$gte"] = start
        if end:
            created_range["$lt"] = end + timedelta(days=1)
        match["created_at"] = created_range

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 10

    all_docs = list(purchase_orders_col.find(match).sort("created_at", -1))
    all_rows = [_purchase_order_row(doc) for doc in all_docs]
    total = len(all_rows)
    start_index = (page - 1) * per_page
    rows = all_rows[start_index:start_index + per_page]

    status_counts: Dict[str, int] = {}
    for row in all_rows:
        row_status = row["status"]
        status_counts[row_status] = status_counts.get(row_status, 0) + 1

    expected_value = sum(row["expected_cost"] for row in all_rows if row["status"] != "cancelled")
    received_value = sum(row["received_value"] for row in all_rows if row["status"] != "cancelled")
    # Rejected units are not still awaiting delivery, so use line-level
    # remaining quantities rather than ordered value minus received value.
    pending_value = sum(row["remaining_value"] for row in all_rows if row["status"] != "cancelled")
    suppliers = sorted({str(doc.get("supplier_name") or "") for doc in purchase_orders_col.find({}, {"supplier_name": 1}) if doc.get("supplier_name")})
    branches = sorted({str(doc.get("branch") or "") for doc in purchase_orders_col.find({}, {"branch": 1}) if doc.get("branch")})

    response = jsonify(
        ok=True,
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        has_more=start_index + len(rows) < total,
        summary={
            "orders": total,
            "expected_value": round(expected_value, 2),
            "received_value": round(received_value, 2),
            "pending_value": round(pending_value, 2),
            "status_counts": status_counts,
        },
        options={"suppliers": suppliers, "branches": branches},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ----------------- List Entries (with filters) -----------------
@executive_stock_entry_bp.route("/list", methods=["GET"])
def list_stock_entries():
    scope = _ensure_exec_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Please log in."), 401

    match = _build_match_from_request(request.args)
    docs = list(
        stock_col.find(match)
        .sort("purchased_at", -1)
        .limit(300)
    )

    rows = []
    for d in docs:
        qty = float(d.get("quantity", 0) or 0)
        unit_price = float(d.get("unit_price", 0) or 0)
        total_cost = float(d.get("total_cost", qty * unit_price) or 0)

        dt = d.get("purchased_at") or d.get("created_at")
        if isinstance(dt, datetime):
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")
        else:
            date_str = ""
            time_str = ""

        rows.append({
            "_id": str(d["_id"]),
            "name": d.get("name", ""),
            "quantity": qty,
            "unit_price": unit_price,
            "total_cost": f"{total_cost:,.2f}",
            "description": d.get("description", ""),
            "date": date_str,
            "time": time_str,
        })

    return jsonify(ok=True, rows=rows)


# ----------------- Stats (for charts) -----------------
@executive_stock_entry_bp.route("/stats", methods=["GET"])
def stock_stats():
    """
    kind=daily   → total per day (for line chart)
    kind=items   → top items by total amount (for bar chart)
    Filters: start, end, name (same as /list)
    """
    scope = _ensure_exec_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Please log in."), 401

    args = request.args
    kind = (args.get("kind") or "daily").lower()
    if kind not in ("daily", "items"):
        kind = "daily"

    match = _build_match_from_request(args)
    pipeline: List[Dict[str, Any]] = [{"$match": match}]

    if kind == "items":
        pipeline += [
            {
                "$group": {
                    "_id": "$name",
                    "sum_total": {"$sum": {"$ifNull": ["$total_cost", 0]}},
                }
            },
            {"$sort": {"sum_total": -1}},
            {"$limit": 10},
        ]
        agg = list(stock_col.aggregate(pipeline))
        labels = [a["_id"] for a in agg]
        values = [float(a["sum_total"] or 0) for a in agg]
        total = sum(values)
        return jsonify(ok=True, labels=labels, values=values, total=round(total, 2))

    # daily
    pipeline += [
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$purchased_at",
                    }
                },
                "sum_total": {"$sum": {"$ifNull": ["$total_cost", 0]}},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    agg = list(stock_col.aggregate(pipeline))
    labels = [a["_id"] for a in agg]
    values = [float(a["sum_total"] or 0) for a in agg]
    total = sum(values)
    return jsonify(ok=True, labels=labels, values=values, total=round(total, 2))
