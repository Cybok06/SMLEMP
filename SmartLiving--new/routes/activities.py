from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request

from db import db
from login import get_current_identity, role_required
from services.activity_audit import activity_logs_col


activities_bp = Blueprint("activities", __name__)
activities_col = db["user_daily_activities"]
users_col = db["users"]

ALLOWED_ROLES = {"agent", "manager", "inventory", "hr", "admin", "executive"}
ACTIVITY_CATEGORIES = [
    "Customer Follow-up",
    "Sales",
    "Collections",
    "Field Visit",
    "Meeting",
    "Training",
    "Stock / Inventory",
    "Admin Work",
    "Audit / Review",
    "Planning",
    "Branch Operations",
    "Other",
]
ACTIVITY_PRIORITIES = ["Normal", "Important", "Urgent"]


def _ensure_indexes() -> None:
    try:
        activities_col.create_index([("user_id", 1), ("activity_date", -1), ("created_at", 1)])
        activities_col.create_index([("activity_date", -1), ("role", 1)])
        activities_col.create_index([("updated_at", -1)])
    except Exception:
        pass


_ensure_indexes()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_str() -> str:
    return _now().date().isoformat()


def _safe_oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _parse_date(raw: str) -> str:
    value = (raw or "").strip() or _today_str()
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        return _today_str()


def _fetch_user_doc(user_id: str) -> Dict[str, Any]:
    oid = _safe_oid(user_id)
    if oid is not None:
        doc = users_col.find_one({"_id": oid}) or {}
        if doc:
            return doc
    return users_col.find_one({"_id": user_id}) or {}


def _role_sidebar_template(role: str) -> str:
    return {
        "agent": "side_bar.html",
        "manager": "manager_sidebar.html",
        "inventory": "inventory_sidebar.html",
        "admin": "admin_sidebar.html",
        "executive": "executive_sidebar.html",
    }.get(role, "side_bar.html")


def _serialize_activity(doc: Dict[str, Any]) -> Dict[str, Any]:
    created_at = doc.get("created_at")
    updated_at = doc.get("updated_at")
    return {
        "id": str(doc.get("_id") or ""),
        "activity_date": doc.get("activity_date") or "",
        "category": doc.get("category") or "Other",
        "title": doc.get("title") or "",
        "details": doc.get("details") or "",
        "location": doc.get("location") or "",
        "outcome": doc.get("outcome") or "",
        "priority": doc.get("priority") or "Normal",
        "sequence": int(doc.get("sequence") or 0),
        "created_at": created_at.isoformat().replace("+00:00", "Z") if isinstance(created_at, datetime) else "",
        "updated_at": updated_at.isoformat().replace("+00:00", "Z") if isinstance(updated_at, datetime) else "",
    }


def _day_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    urgent = sum(1 for r in rows if (r.get("priority") or "") == "Urgent")
    important = sum(1 for r in rows if (r.get("priority") or "") == "Important")
    latest = rows[-1]["created_at"] if rows else ""
    return {
        "count": len(rows),
        "urgent": urgent,
        "important": important,
        "latest": latest,
    }


def _serialize_app_activity(doc: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = doc.get("timestamp")
    meta = doc.get("meta") or {}
    return {
        "id": str(doc.get("_id") or ""),
        "timestamp": timestamp.isoformat() + "Z" if isinstance(timestamp, datetime) else "",
        "action": doc.get("action") or "",
        "action_label": doc.get("action_label") or "",
        "entity_type": doc.get("entity_type") or "",
        "entity_id": doc.get("entity_id") or "",
        "path": meta.get("path") or "",
        "method": meta.get("method") or "",
        "amount": meta.get("amount") or "",
        "customer_name": meta.get("customer_name") or meta.get("customer") or "",
        "reference": meta.get("reference") or "",
        "payment_type": meta.get("payment_type") or "",
    }


@activities_bp.route("/activities", methods=["GET"])
@role_required("agent", "manager", "inventory", "hr", "admin", "executive")
def activities_page():
    ident = get_current_identity()
    role = str(ident.get("role") or "").strip().lower()
    if role not in ALLOWED_ROLES:
        return "Forbidden", 403

    user_doc = _fetch_user_doc(str(ident.get("user_id") or ""))
    context = {
        "page_title": "Activities",
        "active_page": "activities",
        "activities_role": role,
        "categories": ACTIVITY_CATEGORIES,
        "priorities": ACTIVITY_PRIORITIES,
        "today_date": _today_str(),
        "user_name": user_doc.get("name") or ident.get("name") or ident.get("username") or "User",
        "user_branch": user_doc.get("branch") or user_doc.get("store_name") or user_doc.get("store") or "",
        "user_role_label": role.replace("_", " ").title(),
        "sidebar_template": _role_sidebar_template(role),
    }
    if role == "hr":
        return render_template("hr_pages/hr_activities_page.html", **context)
    return render_template("activities_page.html", **context)


@activities_bp.route("/activities/api/list", methods=["GET"])
@role_required("agent", "manager", "inventory", "hr", "admin", "executive")
def activities_list():
    ident = get_current_identity()
    user_id = str(ident.get("user_id") or "")
    activity_date = _parse_date(request.args.get("date") or "")

    cursor = activities_col.find(
        {"user_id": user_id, "activity_date": activity_date, "is_deleted": {"$ne": True}},
        {
            "activity_date": 1,
            "category": 1,
            "title": 1,
            "details": 1,
            "location": 1,
            "outcome": 1,
            "priority": 1,
            "sequence": 1,
            "created_at": 1,
            "updated_at": 1,
        },
    ).sort([("sequence", 1), ("created_at", 1)])

    rows = [_serialize_activity(doc) for doc in cursor]
    return jsonify(ok=True, date=activity_date, records=rows, summary=_day_summary(rows))


@activities_bp.route("/activities/api/employee/<employee_id>", methods=["GET"])
@role_required("hr", "admin", "executive")
def activities_employee_list(employee_id: str):
    oid = _safe_oid(employee_id)
    if oid is None:
        return jsonify(ok=False, message="Invalid employee ID."), 400

    employee = users_col.find_one(
        {"_id": oid},
        {"name": 1, "username": 1, "branch": 1, "store_name": 1, "store": 1, "role": 1},
    )
    if not employee:
        return jsonify(ok=False, message="Employee not found."), 404

    activity_date = _parse_date(request.args.get("date") or "")
    cursor = activities_col.find(
        {"user_id": str(oid), "activity_date": activity_date, "is_deleted": {"$ne": True}},
        {
            "activity_date": 1,
            "category": 1,
            "title": 1,
            "details": 1,
            "location": 1,
            "outcome": 1,
            "priority": 1,
            "sequence": 1,
            "created_at": 1,
            "updated_at": 1,
        },
    ).sort([("sequence", 1), ("created_at", 1)])

    rows = [_serialize_activity(doc) for doc in cursor]
    return jsonify(
        ok=True,
        date=activity_date,
        employee={
            "id": str(oid),
            "name": employee.get("name") or employee.get("username") or "Employee",
            "role": employee.get("role") or "",
            "branch": employee.get("branch") or employee.get("store_name") or employee.get("store") or "",
        },
        records=rows,
        summary=_day_summary(rows),
    )


@activities_bp.route("/activities/api/employee/<employee_id>/app", methods=["GET"])
@role_required("hr", "admin", "executive")
def activities_employee_app_list(employee_id: str):
    oid = _safe_oid(employee_id)
    if oid is None:
        return jsonify(ok=False, message="Invalid employee ID."), 400

    employee = users_col.find_one(
        {"_id": oid},
        {"name": 1, "username": 1, "branch": 1, "store_name": 1, "store": 1, "role": 1},
    )
    if not employee:
        return jsonify(ok=False, message="Employee not found."), 404

    activity_date = _parse_date(request.args.get("date") or "")
    cursor = activity_logs_col.find(
        {
            "user_id": str(oid),
            "day": activity_date,
        },
        {
            "action": 1,
            "action_label": 1,
            "entity_type": 1,
            "entity_id": 1,
            "timestamp": 1,
            "meta": 1,
        },
    ).sort("timestamp", -1).limit(100)

    rows = [_serialize_app_activity(doc) for doc in cursor]
    return jsonify(
        ok=True,
        date=activity_date,
        employee={
            "id": str(oid),
            "name": employee.get("name") or employee.get("username") or "Employee",
            "role": employee.get("role") or "",
            "branch": employee.get("branch") or employee.get("store_name") or employee.get("store") or "",
        },
        records=rows,
        summary={
            "count": len(rows),
            "page_views": sum(1 for r in rows if r.get("action") == "page.opened"),
            "actions": sum(1 for r in rows if r.get("action") != "page.opened"),
            "latest": rows[0]["timestamp"] if rows else "",
        },
    )


@activities_bp.route("/activities/api/add", methods=["POST"])
@role_required("agent", "manager", "inventory", "hr", "admin", "executive")
def activities_add():
    ident = get_current_identity()
    role = str(ident.get("role") or "").strip().lower()
    user_id = str(ident.get("user_id") or "")
    user_doc = _fetch_user_doc(user_id)
    payload = request.get_json(silent=True) or {}

    activity_date = _parse_date(payload.get("activity_date") or "")
    title = (payload.get("title") or "").strip()
    category = (payload.get("category") or "Other").strip()
    details = (payload.get("details") or "").strip()
    location = (payload.get("location") or "").strip()
    outcome = (payload.get("outcome") or "").strip()
    priority = (payload.get("priority") or "Normal").strip()

    if not title:
        return jsonify(ok=False, message="Activity title is required."), 400
    if category not in ACTIVITY_CATEGORIES:
        category = "Other"
    if priority not in ACTIVITY_PRIORITIES:
        priority = "Normal"

    existing_count = activities_col.count_documents(
        {"user_id": user_id, "activity_date": activity_date, "is_deleted": {"$ne": True}}
    )
    now = _now()
    doc = {
        "user_id": user_id,
        "user_name": user_doc.get("name") or ident.get("name") or ident.get("username") or "User",
        "activity_date": activity_date,
        "role": role,
        "branch": user_doc.get("branch") or user_doc.get("store_name") or user_doc.get("store") or "",
        "category": category,
        "title": title,
        "details": details,
        "location": location,
        "outcome": outcome,
        "priority": priority,
        "sequence": existing_count + 1,
        "is_deleted": False,
        "created_at": now,
        "updated_at": now,
    }
    res = activities_col.insert_one(doc)
    saved = activities_col.find_one({"_id": res.inserted_id}) or doc
    return jsonify(ok=True, message="Activity logged.", record=_serialize_activity(saved))


@activities_bp.route("/activities/api/delete/<activity_id>", methods=["POST"])
@role_required("agent", "manager", "inventory", "hr", "admin", "executive")
def activities_delete(activity_id: str):
    ident = get_current_identity()
    oid = _safe_oid(activity_id)
    if oid is None:
        return jsonify(ok=False, message="Invalid activity ID."), 400

    res = activities_col.update_one(
        {"_id": oid, "user_id": str(ident.get("user_id") or ""), "is_deleted": {"$ne": True}},
        {"$set": {"is_deleted": True, "updated_at": _now()}},
    )
    if res.matched_count == 0:
        return jsonify(ok=False, message="Activity not found."), 404
    return jsonify(ok=True, message="Activity removed.")
