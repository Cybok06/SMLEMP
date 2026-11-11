from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify
from bson.objectid import ObjectId

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity, role_required
from services.login_audit import get_login_logs_for_user, get_login_stats_for_user

customer_support_profile_bp = Blueprint(
    "customer_support_profile",
    __name__,
    url_prefix="/api/customer-support",
)

users_col = db.users


def _fmt_dt(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    return ""


def _user_doc(user_id: str) -> dict:
    if not user_id:
        return {}
    if ObjectId.is_valid(user_id):
        return users_col.find_one({"_id": ObjectId(user_id)}) or {}
    return users_col.find_one({"_id": user_id}) or {}


def _display_role(doc: dict, ident: dict) -> str:
    role = (doc.get("role") or ident.get("role") or "").replace("_", " ").strip()
    return role.title() if role else "User"


def _display_branch(doc: dict) -> str:
    return (
        doc.get("branch")
        or doc.get("store_name")
        or doc.get("store")
        or doc.get("location")
        or "Unassigned"
    )


@customer_support_profile_bp.get("/me")
@role_required("customer_support")
def current_customer_support_profile():
    ident = get_current_identity()
    doc = _user_doc(str(ident.get("user_id") or ""))
    if not doc:
        return jsonify({"ok": False, "message": "User profile not found."}), 404

    stats = get_login_stats_for_user(str(ident.get("user_id") or ""), days=30)
    login_logs = []
    for row in get_login_logs_for_user(str(ident.get("user_id") or ""), limit=10):
        device = row.get("device") or {}
        browser = (device.get("browser") or "").strip()
        os_name = (device.get("os") or "").strip()
        device_name = " • ".join([part for part in [browser, os_name] if part]) or "Unknown Device"
        ip_loc = row.get("ip_location") or {}
        location_name = ", ".join(
            [part for part in [ip_loc.get("city"), ip_loc.get("region"), ip_loc.get("country")] if part]
        ) or "Unknown location"

        status = "Success"
        if row.get("switch_event"):
            status = "Role Switched"

        login_logs.append(
            {
                "id": row.get("_id") or "",
                "time": _fmt_dt(row.get("timestamp")),
                "device": device_name,
                "ip": row.get("ip") or "",
                "location": location_name,
                "status": status,
            }
        )

    payload = {
        "ok": True,
        "profile": {
            "user_id": str(doc.get("_id") or ident.get("user_id") or ""),
            "name": doc.get("name") or doc.get("username") or ident.get("name") or "User",
            "username": doc.get("username") or ident.get("username") or "",
            "role": _display_role(doc, ident),
            "can_view_activity_logs": ident.get('role') in {'customer_support', 'admin', 'executive'},
            "email": doc.get("email") or "",
            "phone": doc.get("phone") or doc.get("phone_number") or "",
            "branch": _display_branch(doc),
            "location": doc.get("location") or _display_branch(doc),
            "employee_id": doc.get("employee_id") or doc.get("staff_id") or str(doc.get("_id") or ""),
            "joined": _fmt_dt(doc.get("date_registered") or doc.get("created_at")),
            "status": (doc.get("status") or "Active").title(),
            "avatar_initials": "".join(
                [part[:1].upper() for part in (doc.get("name") or doc.get("username") or "User").split()[:2]]
            ) or "U",
        },
        "login_stats": {
            "last_login": _fmt_dt(stats.get("last_login")),
            "total_logins": int(stats.get("total_logins") or 0),
            "unique_ips": int(stats.get("unique_ips") or 0),
            "unique_devices": int(stats.get("unique_devices") or 0),
        },
        "login_logs": login_logs,
    }
    return jsonify(payload)
