from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional

from bson import ObjectId
from flask import Blueprint, jsonify, redirect, render_template, request, url_for, current_app

from cache_ext import cache
from db import db
from login import get_current_identity
from services.activation_groups import (
    get_activation_team_members,
    get_activation_group_context,
    is_activation_ended,
    is_activation_running,
    is_activation_started,
    safe_object_id,
)

activations_bp = Blueprint("activations", __name__)

activations_col = db["activations"]
rsvps_col = db["activation_rsvps"]
users_col = db["users"]
customers_col = db["customers"]
payments_col = db["payments"]
leader_locations_col = db["activation_leader_locations"]

ALLOWED_ACTIVATION_STATUSES = {"upcoming", "closed", "cancelled"}
ALLOWED_RSVP_STATUSES = {"pending", "approved", "rejected"}


def _ensure_indexes() -> None:
    try:
        activations_col.create_index([("status", 1), ("activationDateTime", 1)])
        activations_col.create_index([("createdAt", -1)])
        rsvps_col.create_index([("activationId", 1), ("userId", 1)], unique=True)
        rsvps_col.create_index([("activationId", 1), ("status", 1), ("requestedAt", -1)])
        leader_locations_col.create_index([("activationId", 1), ("leaderId", 1), ("recordedAt", -1)])
    except Exception:
        pass


_ensure_indexes()


def _safe_object_id(raw: Any) -> Optional[ObjectId]:
    return safe_object_id(raw)


def _parse_activation_datetime(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "message": message}), code


def _require_roles(*roles: str):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            ident = get_current_identity()
            if not ident.get("is_authenticated"):
                if request.path.startswith("/api/"):
                    return _json_error("Unauthorized", 401)
                return redirect(url_for("login.login", next=request.path))

            role = (ident.get("role") or "").lower()
            if roles and role not in roles:
                if request.path.startswith("/api/"):
                    return _json_error("Forbidden", 403)
                return "Forbidden", 403
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def _resolve_identity():
    ident = get_current_identity()
    return ident, _safe_object_id(ident.get("user_id")), (ident.get("role") or "").lower()


def _is_activation_today(doc: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    activation_dt = doc.get("activationDateTime")
    current = now or datetime.utcnow()
    return isinstance(activation_dt, datetime) and activation_dt.date() == current.date()


def _activation_runtime_state(doc: Dict[str, Any]) -> str:
    if is_activation_running(doc):
        return "running"
    if is_activation_ended(doc):
        return "ended"
    if is_activation_started(doc):
        return "started"
    return "not_started"


def _activation_runtime_seconds(doc: Dict[str, Any], now: Optional[datetime] = None) -> int:
    started_at = doc.get("startedAt")
    if not isinstance(started_at, datetime):
        return 0
    current = now or datetime.utcnow()
    ended_at = doc.get("endedAt") if isinstance(doc.get("endedAt"), datetime) else None
    finish_at = ended_at or current
    return max(0, int((finish_at - started_at).total_seconds()))


def _serialize_activation(doc: Dict[str, Any]) -> Dict[str, Any]:
    leader_id = doc.get("teamLeaderId")
    now = datetime.utcnow()
    return {
        "id": str(doc.get("_id")),
        "title": doc.get("title") or "",
        "location": doc.get("location") or "",
        "activationDateTime": doc.get("activationDateTime").isoformat() if isinstance(doc.get("activationDateTime"), datetime) else None,
        "notes": doc.get("notes") or "",
        "status": doc.get("status") or "upcoming",
        "teamLeaderId": str(leader_id) if leader_id else "",
        "teamLeaderName": doc.get("teamLeaderName") or "",
        "teamLeaderAssignedAt": doc.get("teamLeaderAssignedAt").isoformat() if isinstance(doc.get("teamLeaderAssignedAt"), datetime) else None,
        "teamLeaderLocationRequired": bool(doc.get("teamLeaderLocationRequired")),
        "startedAt": doc.get("startedAt").isoformat() if isinstance(doc.get("startedAt"), datetime) else None,
        "endedAt": doc.get("endedAt").isoformat() if isinstance(doc.get("endedAt"), datetime) else None,
        "runtimeState": _activation_runtime_state(doc),
        "runtimeSeconds": _activation_runtime_seconds(doc, now),
        "isToday": _is_activation_today(doc, now),
        "canStart": bool((doc.get("status") or "").lower() == "upcoming" and not is_activation_started(doc) and _is_activation_today(doc, now)),
        "canEnd": bool((doc.get("status") or "").lower() == "upcoming" and is_activation_started(doc) and not is_activation_ended(doc)),
        "createdBy": {
            "id": str((doc.get("createdBy") or {}).get("id") or ""),
            "role": (doc.get("createdBy") or {}).get("role") or "",
        },
        "createdAt": doc.get("createdAt").isoformat() if isinstance(doc.get("createdAt"), datetime) else None,
        "updatedAt": doc.get("updatedAt").isoformat() if isinstance(doc.get("updatedAt"), datetime) else None,
    }


def _attach_counts(items: list[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    ids = [x.get("_id") for x in items if x.get("_id") is not None]
    out: Dict[str, Dict[str, int]] = {str(i): {"approvedCount": 0, "pendingCount": 0, "rejectedCount": 0} for i in ids}
    if not ids:
        return out

    pipeline = [
        {"$match": {"activationId": {"$in": ids}}},
        {"$group": {"_id": {"aid": "$activationId", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    for row in rsvps_col.aggregate(pipeline):
        key = str((row.get("_id") or {}).get("aid"))
        st = ((row.get("_id") or {}).get("status") or "").lower()
        if key not in out:
            out[key] = {"approvedCount": 0, "pendingCount": 0, "rejectedCount": 0}
        if st == "approved":
            out[key]["approvedCount"] = int(row.get("count") or 0)
        elif st == "pending":
            out[key]["pendingCount"] = int(row.get("count") or 0)
        elif st == "rejected":
            out[key]["rejectedCount"] = int(row.get("count") or 0)
    return out


def _my_rsvp_map(user_id: Optional[ObjectId], activation_ids: list[ObjectId]) -> Dict[str, str]:
    if not user_id or not activation_ids:
        return {}
    out: Dict[str, str] = {}
    cur = rsvps_col.find({"activationId": {"$in": activation_ids}, "userId": user_id}, {"activationId": 1, "status": 1})
    for r in cur:
        aid = r.get("activationId")
        if aid is None:
            continue
        out[str(aid)] = (r.get("status") or "pending").lower()
    return out


def _upcoming_query(now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.utcnow()
    today_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "status": "upcoming",
        "endedAt": {"$exists": False},
        "$or": [
            {"startedAt": {"$type": "date"}},
            {"activationDateTime": {"$gte": today_start}},
        ],
    }


def _build_people_rows(rsvps: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    user_ids = [r.get("userId") for r in rsvps if isinstance(r.get("userId"), ObjectId)]
    user_map: Dict[ObjectId, Dict[str, Any]] = {}
    if user_ids:
        for u in users_col.find({"_id": {"$in": user_ids}}, {"name": 1, "username": 1, "branch": 1, "role": 1}):
            user_map[u["_id"]] = u

    rows = []
    for r in rsvps:
        uid = r.get("userId")
        u = user_map.get(uid, {}) if isinstance(uid, ObjectId) else {}
        rows.append(
            {
                "userId": str(uid) if uid is not None else "",
                "name": u.get("name") or u.get("username") or "Unknown User",
                "role": (u.get("role") or r.get("role") or "").lower(),
                "branch": u.get("branch") or "",
                "status": (r.get("status") or "pending").lower(),
                "requestedAt": r.get("requestedAt").isoformat() if isinstance(r.get("requestedAt"), datetime) else None,
                "reviewedAt": r.get("reviewedAt").isoformat() if isinstance(r.get("reviewedAt"), datetime) else None,
            }
        )
    return rows


def _maybe_datetime(value: Any) -> Optional[datetime]:
    return value if isinstance(value, datetime) else None


def _activation_runtime_bounds(activation_doc: Dict[str, Any]) -> tuple[Optional[datetime], Optional[datetime]]:
    return _maybe_datetime((activation_doc or {}).get("startedAt")), _maybe_datetime((activation_doc or {}).get("endedAt"))


def _leader_runtime_periods(activation_doc: Dict[str, Any], leader_id: str) -> list[tuple[datetime, Optional[datetime]]]:
    if not leader_id:
        return []

    runtime_start, runtime_end = _activation_runtime_bounds(activation_doc)
    if not runtime_start:
        return []

    periods: list[tuple[datetime, Optional[datetime]]] = []
    history = list((activation_doc or {}).get("teamLeaderHistory") or [])
    seen_assigned: set[datetime] = set()

    for entry in history:
        if str(entry.get("leaderId") or "") != leader_id:
            continue
        assigned_at = _maybe_datetime(entry.get("assignedAt"))
        if not assigned_at:
            continue
        period_start = max(assigned_at, runtime_start)
        period_end = _maybe_datetime(entry.get("endedAt"))
        if runtime_end and (period_end is None or runtime_end < period_end):
            period_end = runtime_end
        if period_end and period_end < period_start:
            continue
        periods.append((period_start, period_end))
        seen_assigned.add(assigned_at)

    current_leader_id = str((activation_doc or {}).get("teamLeaderId") or "")
    current_assigned_at = _maybe_datetime((activation_doc or {}).get("teamLeaderAssignedAt"))
    if current_leader_id == leader_id and current_assigned_at and current_assigned_at not in seen_assigned:
        period_start = max(current_assigned_at, runtime_start)
        period_end = runtime_end
        if not period_end or period_end >= period_start:
            periods.append((period_start, period_end))

    return sorted(periods, key=lambda item: item[0])


def _ts_in_periods(ts: Optional[datetime], periods: list[tuple[datetime, Optional[datetime]]]) -> bool:
    if not ts:
        return False
    for start, end in periods:
        if ts < start:
            continue
        if end is None or ts <= end:
            return True
    return False


def _customer_matches_leader_scope(customer: Dict[str, Any], activation_doc: Dict[str, Any], leader_id: str) -> bool:
    if not leader_id:
        return False
    if str(customer.get("activation_leader_id") or "") == leader_id:
        return True

    customer_ts = _maybe_datetime(customer.get("date_registered"))
    leader_periods = _leader_runtime_periods(activation_doc or {}, leader_id)
    matches_leader = _ts_in_periods(customer_ts, leader_periods) if leader_periods else False
    if matches_leader:
        return True

    # Older activation leads only kept a date (midnight) and ending the activation
    # restored ownership by clearing activation_leader_id. For those records, keep
    # the historical leader report useful by falling back to the activation itself.
    if not customer.get("activation_leader_id") and _user_was_activation_leader(activation_doc or {}, leader_id):
        return True
    return False


def _user_was_activation_leader(activation_doc: Dict[str, Any], user_id: str) -> bool:
    if not user_id:
        return False
    if str((activation_doc or {}).get("teamLeaderId") or "") == user_id:
        return True
    return any(str(entry.get("leaderId") or "") == user_id for entry in ((activation_doc or {}).get("teamLeaderHistory") or []))


def _serialize_leader_history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "leaderId": str(entry.get("leaderId") or ""),
        "leaderName": entry.get("leaderName") or "",
        "assignedAt": entry.get("assignedAt").isoformat() if isinstance(entry.get("assignedAt"), datetime) else None,
        "endedAt": entry.get("endedAt").isoformat() if isinstance(entry.get("endedAt"), datetime) else None,
        "durationMinutes": _money(entry.get("durationMinutes")),
        "closeReason": entry.get("closeReason") or "",
    }


def _build_registered_customer_rows(activation_doc: Dict[str, Any], user_id: str, limit: int = 8) -> Dict[str, Any]:
    aid = _safe_object_id((activation_doc or {}).get("_id"))
    if not aid or not user_id:
        return {"count": 0, "paymentTotal": 0.0, "items": []}

    actor_report = _build_activation_actor_report(activation_doc, user_id)
    report_rows = actor_report.get("items") or []
    report_by_id = {row.get("customerId"): row for row in report_rows if row.get("customerId")}
    customer_rows = list(
        customers_col.find(
            {"_id": {"$in": [_safe_object_id(row.get("customerId")) for row in report_rows if _safe_object_id(row.get("customerId"))]}},
            {
                "name": 1,
                "phone_number": 1,
                "location": 1,
                "date_registered": 1,
                "status": 1,
                "activation_leader_name": 1,
            },
        ).sort([("date_registered", -1)]).limit(limit)
    )
    payment_total = _money((actor_report.get("totals") or {}).get("amount"))
    total_count = int((actor_report.get("totals") or {}).get("customers") or 0)
    items = [
        {
            "id": str(row.get("_id")),
            "name": row.get("name") or "Unnamed Customer",
            "phoneNumber": row.get("phone_number") or "",
            "location": row.get("location") or "",
            "dateRegistered": row.get("date_registered").isoformat() if isinstance(row.get("date_registered"), datetime) else None,
            "status": row.get("status") or "",
            "leaderName": row.get("activation_leader_name") or "",
            "amount": _money((report_by_id.get(str(row.get("_id"))) or {}).get("amountPaid")),
            "productsLabel": (report_by_id.get(str(row.get("_id"))) or {}).get("productsLabel") or "",
        }
        for row in customer_rows
    ]
    return {"count": total_count, "paymentTotal": payment_total, "items": items}


def _build_activation_team_summary(activation_doc: Any, leader_id: str, members: list[Dict[str, Any]]) -> Dict[str, Any]:
    doc = activation_doc
    if isinstance(activation_doc, ObjectId):
        doc = activations_col.find_one({"_id": activation_doc})
    elif activation_doc is not None and not isinstance(activation_doc, dict):
        oid = _safe_object_id(activation_doc)
        doc = activations_col.find_one({"_id": oid}) if oid else None

    aid = _safe_object_id((doc or {}).get("_id"))
    if not aid or not leader_id:
        return {
            "totalCustomers": 0,
            "totalAmount": 0.0,
            "totalProducts": 0,
            "topProducts": [],
            "agentBreakdown": [],
            "customerRows": [],
            "agents": [],
        }

    leader_periods = _leader_runtime_periods(doc or {}, leader_id)
    customers = list(
        customers_col.find(
            {"activation_id": aid},
            {
                "name": 1,
                "phone_number": 1,
                "activation_registered_by_id": 1,
                "registered_by_agent_id": 1,
                "purchases": 1,
                "date_registered": 1,
                "activation_leader_id": 1,
                "leader_money_taken": 1,
                "leader_money_taken_at": 1,
                "leader_money_taken_by_id": 1,
            },
        ).sort([("date_registered", -1)])
    )
    payments = list(
        payments_col.find(
            {"activation_id": aid},
            {"customer_id": 1, "amount": 1, "recorded_by_agent_id": 1, "created_at": 1, "activation_leader_id": 1},
        )
    )

    matched_customers: list[Dict[str, Any]] = []
    matched_payments: list[Dict[str, Any]] = []
    product_counts: Dict[str, int] = {}
    total_products = 0
    member_map = {m.get("userId"): m for m in members if m.get("userId")}
    agent_stats: Dict[str, Dict[str, Any]] = {}
    payment_totals_by_customer: Dict[str, float] = {}

    for customer in customers:
        if not _customer_matches_leader_scope(customer, doc or {}, leader_id):
            continue
        matched_customers.append(customer)
        registered_by = str(customer.get("activation_registered_by_id") or customer.get("registered_by_agent_id") or "")
        stat = agent_stats.setdefault(
            registered_by,
            {
                "userId": registered_by,
                "name": (member_map.get(registered_by) or {}).get("name") or "Unknown Agent",
                "customerCount": 0,
                "paymentTotal": 0.0,
                "productTotal": 0,
            },
        )
        stat["customerCount"] += 1
        for purchase in customer.get("purchases") or []:
            product = purchase.get("product") or {}
            name = product.get("name") or "Unnamed Product"
            qty = purchase.get("quantity") or product.get("quantity") or 1
            try:
                qty_int = int(qty)
            except Exception:
                qty_int = 1
            total_products += qty_int
            stat["productTotal"] += qty_int
            product_counts[name] = product_counts.get(name, 0) + qty_int

    for payment in payments:
        payment_ts = _maybe_datetime(payment.get("created_at"))
        matches_leader = _ts_in_periods(payment_ts, leader_periods) if leader_periods else False
        if not matches_leader and str(payment.get("activation_leader_id") or "") != leader_id:
            continue
        matched_payments.append(payment)
        recorded_by = str(payment.get("recorded_by_agent_id") or "")
        stat = agent_stats.setdefault(
            recorded_by,
            {
                "userId": recorded_by,
                "name": (member_map.get(recorded_by) or {}).get("name") or "Unknown Agent",
                "customerCount": 0,
                "paymentTotal": 0.0,
                "productTotal": 0,
            },
        )
        amount = _money(payment.get("amount"))
        stat["paymentTotal"] += amount
        customer_key = str(payment.get("customer_id") or "")
        if customer_key:
            payment_totals_by_customer[customer_key] = payment_totals_by_customer.get(customer_key, 0.0) + amount

    unknown_agent_ids = [agent_id for agent_id in agent_stats.keys() if agent_id and agent_id not in member_map]
    if unknown_agent_ids:
        unknown_oids = [_safe_object_id(agent_id) for agent_id in unknown_agent_ids]
        for user in users_col.find({"_id": {"$in": [oid for oid in unknown_oids if oid is not None]}}, {"name": 1, "username": 1}):
            member_map[str(user.get("_id"))] = {
                "userId": str(user.get("_id")),
                "name": user.get("name") or user.get("username") or "Unknown Agent",
            }
    for agent_id, stat in agent_stats.items():
        if agent_id in member_map:
            stat["name"] = member_map[agent_id].get("name") or stat.get("name") or "Unknown Agent"

    top_products = [{"name": name, "quantity": qty} for name, qty in sorted(product_counts.items(), key=lambda item: (-item[1], item[0]))[:6]]
    agent_breakdown = sorted(agent_stats.values(), key=lambda item: (-item["customerCount"], item["name"]))
    customer_rows: list[Dict[str, Any]] = []
    agent_ids_in_rows: set[str] = set()

    for customer in matched_customers:
        registered_by = str(customer.get("activation_registered_by_id") or customer.get("registered_by_agent_id") or "")
        agent_name = (member_map.get(registered_by) or {}).get("name") or "Unknown Agent"
        purchases = customer.get("purchases") or []
        product_total = 0
        product_value = 0.0
        product_labels: list[str] = []
        for purchase in purchases:
            product = purchase.get("product") or {}
            product_name = product.get("name") or purchase.get("product_name") or "Unnamed Product"
            qty = purchase.get("quantity") or product.get("quantity") or 1
            try:
                qty_int = int(qty)
            except Exception:
                qty_int = 1
            if qty_int < 1:
                qty_int = 1
            product_total += qty_int
            line_total = _money(product.get("total") or purchase.get("total"))
            if line_total <= 0:
                line_total = _money(product.get("price") or purchase.get("price")) * qty_int
            product_value += line_total
            product_labels.append(f"{product_name} x{qty_int}" if qty_int != 1 else product_name)

        customer_payment_total = round(payment_totals_by_customer.get(str(customer.get("_id") or ""), 0.0), 2)
        row_amount = customer_payment_total if customer_payment_total > 0 else round(product_value, 2)
        customer_rows.append(
            {
                "customerId": str(customer.get("_id") or ""),
                "customerName": customer.get("name") or "Unnamed Customer",
                "phoneNumber": customer.get("phone_number") or "",
                "productsLabel": ", ".join(product_labels) if product_labels else "No product assigned",
                "productTotal": product_total,
                "amountPaid": row_amount,
                "paymentTotal": customer_payment_total,
                "productValue": round(product_value, 2),
                "agentId": registered_by,
                "agentName": agent_name,
                "moneyTaken": bool(customer.get("leader_money_taken")),
                "moneyTakenAt": customer.get("leader_money_taken_at").isoformat() if isinstance(customer.get("leader_money_taken_at"), datetime) else None,
                "moneyTakenById": str(customer.get("leader_money_taken_by_id") or ""),
            }
        )
        if registered_by:
            agent_ids_in_rows.add(registered_by)

    agent_options = [
        {
            "userId": agent_id,
            "name": (member_map.get(agent_id) or {}).get("name") or next(
                (row.get("agentName") for row in customer_rows if row.get("agentId") == agent_id),
                "Unknown Agent",
            ),
        }
        for agent_id in sorted(agent_ids_in_rows, key=lambda value: ((member_map.get(value) or {}).get("name") or "").lower())
    ]
    total_amount = round(sum(_money(row.get("amountPaid")) for row in customer_rows), 2)
    return {
        "totalCustomers": len(matched_customers),
        "totalAmount": total_amount,
        "totalProducts": total_products,
        "topProducts": top_products,
        "agentBreakdown": agent_breakdown,
        "customerRows": customer_rows,
        "agents": agent_options,
    }


def _build_activation_runtime_report(activation_doc: Dict[str, Any], leader_id: str) -> Dict[str, Any]:
    members = get_activation_team_members(activation_doc.get("_id"))
    summary = _build_activation_team_summary(activation_doc, leader_id, members)
    return {
        "activation": _serialize_activation(activation_doc),
        "kind": "leader",
        "leaderId": leader_id,
        "leaderName": activation_doc.get("teamLeaderName") or "",
        "totals": {
            "customers": summary.get("totalCustomers", 0),
            "products": summary.get("totalProducts", 0),
            "amount": summary.get("totalAmount", 0.0),
        },
        "agentBreakdown": summary.get("agentBreakdown", []),
        "items": summary.get("customerRows", []),
        "agents": summary.get("agents", []),
    }


def _build_activation_actor_report(activation_doc: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    aid = _safe_object_id((activation_doc or {}).get("_id"))
    if not aid or not actor_id:
        return {
            "activation": _serialize_activation(activation_doc),
            "kind": "personal",
            "leaderId": "",
            "leaderName": "",
            "totals": {"customers": 0, "products": 0, "amount": 0.0},
            "agentBreakdown": [],
            "items": [],
            "agents": [],
        }

    actor_variants = [actor_id]
    actor_oid = _safe_object_id(actor_id)
    if actor_oid:
        actor_variants.append(actor_oid)

    customers = list(
        customers_col.find(
            {
                "activation_id": aid,
                "$or": [
                    {"activation_registered_by_id": {"$in": actor_variants}},
                    {"registered_by_agent_id": {"$in": actor_variants}},
                    {"registered_by_id": {"$in": actor_variants}},
                    {"agent_id": {"$in": actor_variants}},
                ],
            },
            {
                "name": 1,
                "phone_number": 1,
                "purchases": 1,
                "activation_registered_by_id": 1,
                "registered_by_agent_id": 1,
                "registered_by_id": 1,
                "agent_id": 1,
                "date_registered": 1,
            },
        ).sort([("date_registered", -1)])
    )

    payment_totals_by_customer: Dict[str, float] = {}
    for payment in payments_col.find(
        {
            "activation_id": aid,
            "recorded_by_agent_id": {"$in": actor_variants},
        },
        {"customer_id": 1, "amount": 1},
    ):
        customer_key = str(payment.get("customer_id") or "")
        if customer_key:
            payment_totals_by_customer[customer_key] = payment_totals_by_customer.get(customer_key, 0.0) + _money(payment.get("amount"))

    user_ids = {
        str(row.get("activation_registered_by_id") or row.get("registered_by_agent_id") or row.get("registered_by_id") or row.get("agent_id") or "")
        for row in customers
    }
    user_oids = [_safe_object_id(uid) for uid in user_ids if uid]
    user_map = {
        str(user.get("_id")): user.get("name") or user.get("username") or "Unknown Agent"
        for user in users_col.find({"_id": {"$in": [oid for oid in user_oids if oid is not None]}}, {"name": 1, "username": 1})
    }

    rows: list[Dict[str, Any]] = []
    total_products = 0
    for customer in customers:
        registered_by = str(customer.get("activation_registered_by_id") or customer.get("registered_by_agent_id") or customer.get("registered_by_id") or customer.get("agent_id") or "")
        product_total = 0
        product_value = 0.0
        product_labels: list[str] = []
        for purchase in customer.get("purchases") or []:
            product = purchase.get("product") or {}
            product_name = product.get("name") or purchase.get("product_name") or "Unnamed Product"
            try:
                qty_int = int(purchase.get("quantity") or product.get("quantity") or 1)
            except Exception:
                qty_int = 1
            qty_int = max(qty_int, 1)
            product_total += qty_int
            total_products += qty_int
            line_total = _money(product.get("total") or purchase.get("total"))
            if line_total <= 0:
                line_total = _money(product.get("price") or purchase.get("price")) * qty_int
            product_value += line_total
            product_labels.append(f"{product_name} x{qty_int}" if qty_int != 1 else product_name)

        payment_total = round(payment_totals_by_customer.get(str(customer.get("_id") or ""), 0.0), 2)
        row_amount = payment_total if payment_total > 0 else round(product_value, 2)
        rows.append(
            {
                "customerId": str(customer.get("_id") or ""),
                "customerName": customer.get("name") or "Unnamed Customer",
                "phoneNumber": customer.get("phone_number") or "",
                "productsLabel": ", ".join(product_labels) if product_labels else "No product assigned",
                "productTotal": product_total,
                "amountPaid": row_amount,
                "paymentTotal": payment_total,
                "productValue": round(product_value, 2),
                "agentId": registered_by,
                "agentName": user_map.get(registered_by) or "Unknown Agent",
            }
        )

    total_amount = round(sum(_money(row.get("amountPaid")) for row in rows), 2)
    agents = sorted(
        [{"userId": uid, "name": name} for uid, name in user_map.items()],
        key=lambda item: item["name"].lower(),
    )
    return {
        "activation": _serialize_activation(activation_doc),
        "kind": "personal",
        "leaderId": str(activation_doc.get("teamLeaderId") or ""),
        "leaderName": activation_doc.get("teamLeaderName") or "",
        "totals": {"customers": len(rows), "products": total_products, "amount": total_amount},
        "agentBreakdown": [
            {
                "userId": actor_id,
                "name": user_map.get(actor_id) or "Current User",
                "customerCount": len(rows),
                "paymentTotal": total_amount,
                "productTotal": total_products,
            }
        ],
        "items": rows,
        "agents": agents,
    }


def _user_is_approved_for_activation(activation_id: ObjectId, user_id: Optional[ObjectId]) -> bool:
    if not activation_id or not user_id:
        return False
    return bool(rsvps_col.find_one({"activationId": activation_id, "userId": user_id, "status": "approved"}, {"_id": 1}))


def _build_viewer_leader_summary(activation_doc: Dict[str, Any], user_id: str, members: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not user_id or not _user_was_activation_leader(activation_doc, user_id):
        return None
    member_map = {m.get("userId"): m for m in members if m.get("userId")}
    current_name = (member_map.get(user_id) or {}).get("name") or (activation_doc.get("teamLeaderName") or "Leader")
    history_rows = [
        _serialize_leader_history_entry(entry)
        for entry in (activation_doc.get("teamLeaderHistory") or [])
        if str(entry.get("leaderId") or "") == user_id
    ]
    if str(activation_doc.get("teamLeaderId") or "") == user_id and not any(row.get("endedAt") is None for row in history_rows):
        history_rows.append(
            {
                "leaderId": user_id,
                "leaderName": current_name,
                "assignedAt": activation_doc.get("teamLeaderAssignedAt").isoformat() if isinstance(activation_doc.get("teamLeaderAssignedAt"), datetime) else None,
                "endedAt": activation_doc.get("endedAt").isoformat() if isinstance(activation_doc.get("endedAt"), datetime) else None,
                "durationMinutes": 0.0,
                "closeReason": "",
            }
        )
    return {
        "leaderId": user_id,
        "leaderName": current_name,
        "history": history_rows,
        "summary": _build_activation_team_summary(activation_doc, user_id, members),
    }


def _leader_payload_for_user(activation_doc: Dict[str, Any], user_id: Optional[ObjectId]) -> Dict[str, Any]:
    ctx = get_activation_group_context(user_id, activation_doc)
    members = ctx.get("members") or get_activation_team_members(activation_doc.get("_id"))
    viewer_id = str(user_id) if user_id else ""
    viewer_activity = _build_registered_customer_rows(activation_doc, viewer_id)
    viewer_leader_summary = _build_viewer_leader_summary(activation_doc, viewer_id, members)
    has_leader = bool(ctx.get("leader_selected") and ctx.get("leader_id"))
    leader_id = str(ctx.get("leader_id") or "") if has_leader else ""
    payload = {
        "hasLeader": has_leader,
        "isLeader": bool(ctx.get("is_leader")),
        "leaderId": leader_id,
        "leaderName": ctx.get("leader_name") or "",
        "teamName": activation_doc.get("teamName") or "Activation Team",
        "leaderState": ctx.get("group_state") or ("not_started" if has_leader else "not_selected"),
        "ownershipActive": bool(ctx.get("ownership_active")),
        "members": members,
        "summary": _build_activation_team_summary(activation_doc, leader_id, members) if has_leader else {
            "totalCustomers": 0,
            "totalAmount": 0.0,
            "totalProducts": 0,
            "topProducts": [],
            "agentBreakdown": [],
            "customerRows": [],
            "agents": [],
        },
        "viewerWasLeader": bool(viewer_leader_summary),
        "viewerLeaderSummary": viewer_leader_summary,
        "viewerCustomers": viewer_activity.get("items") or [],
        "viewerCustomerCount": int(viewer_activity.get("count") or 0),
        "viewerRecordedAmount": _money(viewer_activity.get("paymentTotal")),
    }
    if not has_leader:
        payload["leaderName"] = ""
    return payload


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _owner_manager_id(user_doc: Optional[Dict[str, Any]]) -> Any:
    if not user_doc:
        return None
    role = (user_doc.get("role") or "").lower()
    if role == "manager":
        return user_doc.get("_id")
    return user_doc.get("manager_id")


def _leader_history_update(
    activation: Dict[str, Any],
    now: datetime,
    changed_by: Dict[str, Any],
    close_reason: str,
    new_leader_id: str = "",
    new_leader_name: str = "",
) -> list[Dict[str, Any]]:
    history = list(activation.get("teamLeaderHistory") or [])
    current_leader_id = str(activation.get("teamLeaderId") or "")
    current_leader_name = activation.get("teamLeaderName") or ""
    current_assigned_at = activation.get("teamLeaderAssignedAt")

    if current_leader_id:
        closed = False
        for entry in reversed(history):
            if str(entry.get("leaderId") or "") == current_leader_id and not entry.get("endedAt"):
                entry["endedAt"] = now
                entry["endedBy"] = changed_by
                assigned_at = entry.get("assignedAt") if isinstance(entry.get("assignedAt"), datetime) else current_assigned_at
                if isinstance(assigned_at, datetime):
                    entry["durationMinutes"] = round(max(0.0, (now - assigned_at).total_seconds() / 60.0), 2)
                entry["closeReason"] = close_reason
                closed = True
                break
        if not closed and isinstance(current_assigned_at, datetime):
            history.append(
                {
                    "leaderId": current_leader_id,
                    "leaderName": current_leader_name,
                    "assignedAt": current_assigned_at,
                    "endedAt": now,
                    "endedBy": changed_by,
                    "durationMinutes": round(max(0.0, (now - current_assigned_at).total_seconds() / 60.0), 2),
                    "closeReason": close_reason,
                }
            )

    if new_leader_id:
        history.append(
            {
                "leaderId": new_leader_id,
                "leaderName": new_leader_name,
                "assignedAt": now,
                "assignedBy": changed_by,
            }
        )
    return history


def _apply_activation_leader_ownership(aid: ObjectId, leader_id: str, leader_name: str, leader_manager_id: Any) -> None:
    customers_col.update_many(
        {"activation_id": aid, "activation": True},
        {
            "$set": {
                "agent_id": leader_id,
                "manager_id": leader_manager_id,
                "activation_leader_id": leader_id,
                "activation_leader_name": leader_name,
                "activation_leader_ownership_active": True,
            }
        },
    )


def _restore_activation_customer_ownership(aid: ObjectId) -> None:
    customer_rows = list(
        customers_col.find(
            {"activation_id": aid, "activation": True},
            {"_id": 1, "activation_registered_by_id": 1, "registered_by_agent_id": 1},
        )
    )
    if not customer_rows:
        return

    owner_ids = []
    for row in customer_rows:
        owner_id = str(row.get("activation_registered_by_id") or row.get("registered_by_agent_id") or "").strip()
        if owner_id:
            owner_ids.append(owner_id)

    owner_oids = [_safe_object_id(owner_id) for owner_id in owner_ids]
    user_map = {
        str(user.get("_id")): user
        for user in users_col.find(
            {"_id": {"$in": [oid for oid in owner_oids if oid is not None]}},
            {"role": 1, "manager_id": 1},
        )
    }

    for row in customer_rows:
        owner_id = str(row.get("activation_registered_by_id") or row.get("registered_by_agent_id") or "").strip()
        update_set: Dict[str, Any] = {
            "activation_leader_ownership_active": False,
        }
        if owner_id:
            update_set["agent_id"] = owner_id
            owner_doc = user_map.get(owner_id)
            manager_id = _owner_manager_id(owner_doc)
            if manager_id is not None:
                update_set["manager_id"] = manager_id
        customers_col.update_one({"_id": row.get("_id")}, {"$set": update_set})


def _serialize_leader_location(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "activationId": str(doc.get("activationId") or ""),
        "leaderId": str(doc.get("leaderId") or ""),
        "leaderName": doc.get("leaderName") or "",
        "latitude": _money(doc.get("latitude")),
        "longitude": _money(doc.get("longitude")),
        "accuracy": _money(doc.get("accuracy")),
        "source": doc.get("source") or "browser",
        "recordedAt": doc.get("recordedAt").isoformat() if isinstance(doc.get("recordedAt"), datetime) else None,
    }


def _build_leader_location_payload(activation: Dict[str, Any], aid: ObjectId) -> Dict[str, Any]:
    rows = list(leader_locations_col.find({"activationId": aid}).sort([("recordedAt", -1)]).limit(300))
    latest = rows[0] if rows else None
    return {
        "ok": True,
        "leaderId": str(activation.get("teamLeaderId") or ""),
        "leaderName": activation.get("teamLeaderName") or "",
        "trackingRequired": bool(activation.get("teamLeaderLocationRequired")),
        "latest": _serialize_leader_location(latest) if latest else None,
        "items": [_serialize_leader_location(row) for row in rows],
    }


@activations_bp.post("/api/activations")
@_require_roles("admin")
def api_create_activation():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    location = (payload.get("location") or "").strip()
    notes = (payload.get("notes") or "").strip()
    activation_dt = _parse_activation_datetime(payload.get("activationDateTime") or "")

    if not title:
        return _json_error("Title is required.")
    if not location:
        return _json_error("Location is required.")
    if not activation_dt:
        return _json_error("Activation date and time is required.")

    ident, user_id, role = _resolve_identity()
    now = datetime.utcnow()

    doc = {
        "title": title,
        "location": location,
        "activationDateTime": activation_dt,
        "notes": notes,
        "status": "upcoming",
        "createdBy": {"id": user_id, "role": role},
        "createdAt": now,
        "updatedAt": now,
    }
    ins = activations_col.insert_one(doc)
    created = activations_col.find_one({"_id": ins.inserted_id})
    return jsonify({"ok": True, "activation": _serialize_activation(created), "message": "Activation created."})


@activations_bp.get("/api/activations")
@_require_roles("admin", "manager", "agent")
def api_list_activations():
    ident, user_id, role = _resolve_identity()
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    include_led = (request.args.get("include_led") or "").strip().lower() in {"1", "true", "yes"}
    latest_only = (request.args.get("latest") or "").strip().lower() in {"1", "true", "yes"}
    view = (request.args.get("view") or "").strip().lower()
    if view and view not in {"upcoming", "past"}:
        return _json_error("Invalid activation view.")

    mongo_q: Dict[str, Any] = {}
    if status and not latest_only:
        if status not in ALLOWED_ACTIVATION_STATUSES:
            return _json_error("Invalid status filter.")
        mongo_q["status"] = status
    if q:
        mongo_q["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"location": {"$regex": q, "$options": "i"}},
            {"notes": {"$regex": q, "$options": "i"}},
        ]

    if view and role in {"agent", "manager"} and user_id:
        if view == "upcoming":
            # Upcoming events are visible to everybody so they can request to go.
            docs = list(
                activations_col.find(_upcoming_query())
                .sort([("activationDateTime", 1), ("createdAt", -1)])
                .limit(500)
            )
        else:
            approved_ids = [
                r.get("activationId")
                for r in rsvps_col.find({"userId": user_id, "status": "approved"}, {"activationId": 1})
                if isinstance(r.get("activationId"), ObjectId)
            ]
            membership_clause = {
                "$or": [
                    {"_id": {"$in": approved_ids}},
                    {"teamLeaderId": user_id},
                    {"teamLeaderId": str(user_id)},
                    {"teamLeaderHistory.leaderId": str(user_id)},
                ]
            }
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            past_clause = {
                "$or": [
                    {"status": "closed"},
                    {"endedAt": {"$type": "date"}},
                    {"activationDateTime": {"$lt": today_start}},
                ]
            }
            docs = list(
                activations_col.find({"$and": [membership_clause, past_clause, {"status": {"$ne": "cancelled"}}]})
                .sort([("activationDateTime", -1), ("createdAt", -1)])
                .limit(500)
            )
    elif latest_only and role in {"agent", "manager"} and user_id:
        approved_ids = [
            r.get("activationId")
            for r in rsvps_col.find({"userId": user_id, "status": "approved"}, {"activationId": 1})
            if isinstance(r.get("activationId"), ObjectId)
        ]
        membership_clause = {
            "$or": [
                {"_id": {"$in": approved_ids}},
                {"teamLeaderId": user_id},
                {"teamLeaderId": str(user_id)},
                {"teamLeaderHistory.leaderId": str(user_id)},
            ]
        }
        went_on_clause = {
            "$or": [
                {"startedAt": {"$type": "date"}},
                {"activationDateTime": {"$lte": datetime.utcnow()}},
            ]
        }
        latest_q: Dict[str, Any] = {"$and": [membership_clause, went_on_clause, {"status": {"$ne": "cancelled"}}]}
        if q:
            latest_q["$and"].append(
                {
                    "$or": [
                        {"title": {"$regex": q, "$options": "i"}},
                        {"location": {"$regex": q, "$options": "i"}},
                        {"notes": {"$regex": q, "$options": "i"}},
                    ]
                }
            )
        latest_doc = activations_col.find_one(
            latest_q,
            sort=[("startedAt", -1), ("activationDateTime", -1), ("createdAt", -1)],
        )
        docs = [latest_doc] if latest_doc else []
    else:
        docs = list(activations_col.find(mongo_q).sort([("activationDateTime", 1), ("createdAt", -1)]).limit(500))
    if include_led and not latest_only and role in {"agent", "manager"} and user_id:
        approved_ids = [
            r.get("activationId")
            for r in rsvps_col.find({"userId": user_id, "status": "approved"}, {"activationId": 1})
            if isinstance(r.get("activationId"), ObjectId)
        ]
        led_q = {
            "$or": [
                {"_id": {"$in": approved_ids}},
                {"teamLeaderId": user_id},
                {"teamLeaderId": str(user_id)},
                {"teamLeaderHistory.leaderId": str(user_id)},
            ]
        }
        if q:
            led_q["$and"] = [
                {
                    "$or": [
                        {"title": {"$regex": q, "$options": "i"}},
                        {"location": {"$regex": q, "$options": "i"}},
                        {"notes": {"$regex": q, "$options": "i"}},
                    ]
                }
            ]
        existing_ids = {str(d.get("_id")) for d in docs}
        for led_doc in activations_col.find(led_q).sort([("activationDateTime", -1), ("createdAt", -1)]).limit(100):
            if str(led_doc.get("_id")) not in existing_ids:
                docs.append(led_doc)
                existing_ids.add(str(led_doc.get("_id")))
    if latest_only:
        docs = docs[:1]
    counts = _attach_counts(docs)
    ids = [d.get("_id") for d in docs if isinstance(d.get("_id"), ObjectId)]
    my_map = _my_rsvp_map(user_id, ids) if role in {"agent", "manager"} else {}

    items = []
    for d in docs:
        s = _serialize_activation(d)
        c = counts.get(str(d.get("_id")), {"approvedCount": 0, "pendingCount": 0, "rejectedCount": 0})
        s.update(c)
        if role in {"agent", "manager"}:
            s["myRsvpStatus"] = my_map.get(s["id"], "none")
            s["leaderInfo"] = _leader_payload_for_user(d, user_id)
        items.append(s)

    return jsonify({"ok": True, "items": items})


@activations_bp.get("/api/activations/alerts")
@_require_roles("admin", "manager", "agent")
def api_activations_alerts():
    ident, user_id, role = _resolve_identity()
    cache_key = f"activation_alerts:{role}:{user_id or 'anon'}"
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    next_activation = activations_col.find_one(_upcoming_query(), sort=[("activationDateTime", 1)])
    has_upcoming = bool(next_activation)

    pending_count = 0
    if role in {"manager", "agent"} and user_id:
        upcoming_ids = [x.get("_id") for x in activations_col.find(_upcoming_query(), {"_id": 1}) if isinstance(x.get("_id"), ObjectId)]
        if upcoming_ids:
            mine = {
                r.get("activationId"): (r.get("status") or "pending").lower()
                for r in rsvps_col.find({"activationId": {"$in": upcoming_ids}, "userId": user_id}, {"activationId": 1, "status": 1})
                if isinstance(r.get("activationId"), ObjectId)
            }
            for aid in upcoming_ids:
                st = mine.get(aid, "none")
                if st in {"none", "pending"}:
                    pending_count += 1

    payload = {
        "ok": True,
        "hasUpcoming": has_upcoming,
        "pendingCount": pending_count,
        "nextActivation": _serialize_activation(next_activation) if next_activation else None,
    }
    cache.set(cache_key, payload, timeout=30)
    return jsonify(payload)


@activations_bp.get("/api/activations/<activation_id>")
@_require_roles("admin", "manager", "agent")
def api_activation_details(activation_id: str):
    ident, user_id, role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    doc = activations_col.find_one({"_id": oid})
    if not doc:
        return _json_error("Activation not found.", 404)

    rsvps = list(rsvps_col.find({"activationId": oid}))
    approved_count = len([x for x in rsvps if (x.get("status") or "").lower() == "approved"])
    pending_count = len([x for x in rsvps if (x.get("status") or "").lower() == "pending"])
    rejected_count = len([x for x in rsvps if (x.get("status") or "").lower() == "rejected"])

    my_status = "none"
    if role in {"agent", "manager"} and user_id:
        mine = rsvps_col.find_one({"activationId": oid, "userId": user_id}, {"status": 1})
        if mine:
            my_status = (mine.get("status") or "pending").lower()

    return jsonify(
        {
            "ok": True,
            "activation": _serialize_activation(doc),
            "approvedCount": approved_count,
            "pendingCount": pending_count,
            "rejectedCount": rejected_count,
            "totalResponded": approved_count + pending_count + rejected_count,
            "myRsvpStatus": my_status,
            "leaderInfo": _leader_payload_for_user(doc, user_id) if role in {"agent", "manager"} else None,
        }
    )


@activations_bp.patch("/api/activations/<activation_id>")
@_require_roles("admin")
def api_edit_activation(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    payload = request.get_json(silent=True) or {}
    update: Dict[str, Any] = {}

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            return _json_error("Title is required.")
        update["title"] = title

    if "location" in payload:
        location = (payload.get("location") or "").strip()
        if not location:
            return _json_error("Location is required.")
        update["location"] = location

    if "activationDateTime" in payload:
        parsed = _parse_activation_datetime(payload.get("activationDateTime") or "")
        if not parsed:
            return _json_error("Activation date and time is required.")
        update["activationDateTime"] = parsed

    if "notes" in payload:
        update["notes"] = (payload.get("notes") or "").strip()

    if "status" in payload:
        st = (payload.get("status") or "").strip().lower()
        if st not in ALLOWED_ACTIVATION_STATUSES:
            return _json_error("Invalid status.")
        update["status"] = st

    if not update:
        return _json_error("No changes supplied.")

    update["updatedAt"] = datetime.utcnow()
    res = activations_col.update_one({"_id": oid}, {"$set": update})
    if res.matched_count == 0:
        return _json_error("Activation not found.", 404)

    doc = activations_col.find_one({"_id": oid})
    return jsonify({"ok": True, "activation": _serialize_activation(doc), "message": "Activation updated."})


@activations_bp.patch("/api/activations/<activation_id>/close")
@_require_roles("admin")
def api_close_activation(activation_id: str):
    _ident, admin_id, _role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": oid})
    if not activation:
        return _json_error("Activation not found.", 404)
    now = datetime.utcnow()
    update_set: Dict[str, Any] = {"status": "closed", "updatedAt": now}
    if is_activation_started(activation) and not is_activation_ended(activation):
        update_set["endedAt"] = now
        if activation.get("teamLeaderId"):
            update_set["teamLeaderHistory"] = _leader_history_update(
                activation,
                now,
                {"id": str(admin_id) if admin_id else "", "role": "admin"},
                "activation_closed",
            )
        _restore_activation_customer_ownership(oid)
    activations_col.update_one({"_id": oid}, {"$set": update_set})
    return jsonify({"ok": True, "message": "Activation closed."})


@activations_bp.patch("/api/activations/<activation_id>/cancel")
@_require_roles("admin")
def api_cancel_activation(activation_id: str):
    _ident, admin_id, _role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": oid})
    if not activation:
        return _json_error("Activation not found.", 404)
    now = datetime.utcnow()
    update_set: Dict[str, Any] = {"status": "cancelled", "updatedAt": now}
    if is_activation_started(activation) and not is_activation_ended(activation):
        update_set["endedAt"] = now
        if activation.get("teamLeaderId"):
            update_set["teamLeaderHistory"] = _leader_history_update(
                activation,
                now,
                {"id": str(admin_id) if admin_id else "", "role": "admin"},
                "activation_cancelled",
            )
        _restore_activation_customer_ownership(oid)
    activations_col.update_one({"_id": oid}, {"$set": update_set})
    return jsonify({"ok": True, "message": "Activation cancelled."})


@activations_bp.patch("/api/activations/<activation_id>/start")
@_require_roles("admin")
def api_start_activation(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": oid})
    if not activation:
        return _json_error("Activation not found.", 404)
    if (activation.get("status") or "").lower() != "upcoming":
        return _json_error("Only upcoming activations can be started.", 400)
    if is_activation_started(activation):
        return _json_error("Activation has already been started.", 400)
    if not _is_activation_today(activation):
        return _json_error("Activation can only be started on its scheduled date.", 400)

    now = datetime.utcnow()
    activations_col.update_one({"_id": oid}, {"$set": {"startedAt": now, "updatedAt": now}})
    leader_id = str(activation.get("teamLeaderId") or "")
    if leader_id:
        leader_doc = users_col.find_one({"_id": _safe_object_id(leader_id)}, {"role": 1, "manager_id": 1})
        leader_manager_id = _owner_manager_id(leader_doc)
        if leader_manager_id is not None:
            _apply_activation_leader_ownership(oid, leader_id, activation.get("teamLeaderName") or "", leader_manager_id)
    return jsonify({"ok": True, "message": "Activation started."})


@activations_bp.patch("/api/activations/<activation_id>/end")
@_require_roles("admin")
def api_end_activation(activation_id: str):
    _ident, admin_id, _role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": oid})
    if not activation:
        return _json_error("Activation not found.", 404)
    if (activation.get("status") or "").lower() != "upcoming":
        return _json_error("Only upcoming activations can be ended here.", 400)
    if not is_activation_started(activation):
        return _json_error("Activation has not been started yet.", 400)
    if is_activation_ended(activation):
        return _json_error("Activation has already been ended.", 400)

    now = datetime.utcnow()
    update_set: Dict[str, Any] = {"endedAt": now, "updatedAt": now}
    if activation.get("teamLeaderId"):
        update_set["teamLeaderHistory"] = _leader_history_update(
            activation,
            now,
            {"id": str(admin_id) if admin_id else "", "role": "admin"},
            "activation_ended",
        )
    activations_col.update_one({"_id": oid}, {"$set": update_set})
    _restore_activation_customer_ownership(oid)
    return jsonify({"ok": True, "message": "Activation ended. New work will stay with each registering user."})


@activations_bp.delete("/api/activations/<activation_id>")
@_require_roles("admin")
def api_delete_activation(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    rsvps_col.delete_many({"activationId": oid})
    res = activations_col.delete_one({"_id": oid})
    if res.deleted_count == 0:
        return _json_error("Activation not found.", 404)
    return jsonify({"ok": True, "message": "Activation deleted."})


@activations_bp.post("/api/activations/<activation_id>/request-going")
@_require_roles("agent", "manager")
def api_request_going(activation_id: str):
    ident, user_id, role = _resolve_identity()
    if not user_id:
        return _json_error("Invalid user session.", 401)

    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    activation = activations_col.find_one({"_id": oid})
    if not activation:
        return _json_error("Activation not found.", 404)

    if (activation.get("status") or "").lower() in {"closed", "cancelled"}:
        return _json_error("Cannot request for closed or cancelled activation.", 400)
    if is_activation_ended(activation):
        return _json_error("This activation has already ended.", 400)

    now = datetime.utcnow()
    rsvps_col.update_one(
        {"activationId": oid, "userId": user_id},
        {
            "$set": {
                "activationId": oid,
                "userId": user_id,
                "role": role,
                "status": "pending",
                "requestedAt": now,
            },
            "$unset": {"reviewedAt": "", "reviewedBy": ""},
        },
        upsert=True,
    )

    return jsonify({"ok": True, "status": "pending", "message": "Request sent and pending approval."})


@activations_bp.get("/api/activations/<activation_id>/my-rsvp")
@_require_roles("agent", "manager")
def api_my_rsvp(activation_id: str):
    ident, user_id, role = _resolve_identity()
    if not user_id:
        return _json_error("Invalid user session.", 401)

    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    rec = rsvps_col.find_one({"activationId": oid, "userId": user_id}, {"status": 1})
    st = (rec.get("status") if rec else "none") or "none"
    return jsonify({"ok": True, "status": st})


@activations_bp.get("/api/activations/<activation_id>/requests")
@_require_roles("admin")
def api_requests_list(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    status = (request.args.get("status") or "").strip().lower()
    q: Dict[str, Any] = {"activationId": oid}
    if status:
        if status not in ALLOWED_RSVP_STATUSES:
            return _json_error("Invalid request status.")
        q["status"] = status

    rows = _build_people_rows(list(rsvps_col.find(q).sort([("requestedAt", -1)])))
    return jsonify({"ok": True, "items": rows})


@activations_bp.get("/api/activations/<activation_id>/leader")
@_require_roles("admin")
def api_activation_leader_get(activation_id: str):
    aid = _safe_object_id(activation_id)
    if not aid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)
    approved_people = _build_people_rows(list(rsvps_col.find({"activationId": aid, "status": "approved"}).sort([("requestedAt", -1)])))
    members = get_activation_team_members(aid)
    leader_id = str(activation.get("teamLeaderId") or "")
    leader_state = "running" if leader_id and is_activation_running(activation) else ("ended" if leader_id and is_activation_ended(activation) else ("not_started" if leader_id else "not_selected"))
    return jsonify(
        {
            "ok": True,
            "leaderId": leader_id,
            "leaderName": activation.get("teamLeaderName") or "",
            "leaderState": leader_state,
            "ownershipActive": bool(leader_id and is_activation_running(activation)),
            "approvedPeople": approved_people,
            "summary": _build_activation_team_summary(activation, leader_id, members),
        }
    )


@activations_bp.get("/api/activations/<activation_id>/leader-runtime")
@_require_roles("admin", "manager", "agent")
def api_activation_leader_runtime(activation_id: str):
    _ident, user_id, role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    if not aid:
        return _json_error("Invalid activation id.")

    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)

    requested_leader_id = (request.args.get("leader_id") or "").strip()
    leader_id = requested_leader_id or str(activation.get("teamLeaderId") or "")
    if role != "admin":
        viewer_id = str(user_id or "")
        if requested_leader_id and requested_leader_id != viewer_id:
            return _json_error("You can only view your own leader runtime report.", 403)
        if _user_was_activation_leader(activation, viewer_id):
            return jsonify({"ok": True, "report": _build_activation_runtime_report(activation, viewer_id)})
        if _user_is_approved_for_activation(aid, user_id):
            return jsonify({"ok": True, "report": _build_activation_actor_report(activation, viewer_id)})
        return _json_error("Only approved activation members can view this customer report.", 403)

    if not leader_id:
        return _json_error("No activation leader selected.", 400)

    return jsonify({"ok": True, "report": _build_activation_runtime_report(activation, leader_id)})


@activations_bp.patch("/api/activations/<activation_id>/leader")
@_require_roles("admin")
def api_activation_leader_set(activation_id: str):
    _ident, admin_id, _role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    if not aid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)
    if (activation.get("status") or "").lower() in {"closed", "cancelled"}:
        return _json_error("You cannot change the leader for a closed or cancelled activation.", 400)
    if is_activation_ended(activation):
        return _json_error("This activation has already ended.", 400)

    payload = request.get_json(silent=True) or {}
    raw_leader_id = (payload.get("leaderId") or "").strip()
    now = datetime.utcnow()
    changed_by = {"id": str(admin_id) if admin_id else "", "role": "admin"}
    current_leader_id = str(activation.get("teamLeaderId") or "")
    running = is_activation_running(activation)

    if not raw_leader_id:
        history = _leader_history_update(activation, now, changed_by, "cleared") if current_leader_id else list(activation.get("teamLeaderHistory") or [])
        activations_col.update_one(
            {"_id": aid},
            {
                "$set": {
                    "updatedAt": now,
                    "teamLeaderLocationRequired": False,
                    "teamLeaderHistory": history,
                },
                "$unset": {"teamLeaderId": "", "teamLeaderName": "", "teamLeaderAssignedAt": "", "teamLeaderAssignedBy": ""},
            },
        )
        _restore_activation_customer_ownership(aid)
        message = "Activation leader cleared."
        if running:
            message = "Activation leader cleared. New work will stay with each registering user."
        return jsonify({"ok": True, "message": message})

    leader_oid = _safe_object_id(raw_leader_id)
    if not leader_oid:
        return _json_error("Invalid leader.")

    approved_ids = {
        str(r.get("userId"))
        for r in rsvps_col.find({"activationId": aid, "status": "approved"}, {"userId": 1})
        if r.get("userId")
    }
    if str(leader_oid) not in approved_ids:
        return _json_error("Selected leader must be an approved activation member.")

    leader_doc = users_col.find_one({"_id": leader_oid}, {"name": 1, "username": 1, "role": 1, "manager_id": 1})
    leader_name = (leader_doc or {}).get("name") or (leader_doc or {}).get("username") or "Leader"
    leader_manager_id = _owner_manager_id(leader_doc)
    if leader_manager_id is None:
        return _json_error("Selected leader does not have a manager assignment.")

    same_leader = str(leader_oid) == current_leader_id
    history = list(activation.get("teamLeaderHistory") or [])
    if not same_leader:
        history = _leader_history_update(activation, now, changed_by, "reassigned", str(leader_oid), leader_name)

    activations_col.update_one(
        {"_id": aid},
        {
            "$set": {
                "teamLeaderId": leader_oid,
                "teamLeaderName": leader_name,
                "teamLeaderAssignedAt": activation.get("teamLeaderAssignedAt") if same_leader and activation.get("teamLeaderAssignedAt") else now,
                "teamLeaderAssignedBy": activation.get("teamLeaderAssignedBy") if same_leader and activation.get("teamLeaderAssignedBy") else changed_by,
                "teamLeaderLocationRequired": True,
                "teamLeaderHistory": history,
                "updatedAt": now,
            }
        },
    )

    if running:
        _apply_activation_leader_ownership(aid, str(leader_oid), leader_name, leader_manager_id)

    if same_leader:
        if running:
            return jsonify({"ok": True, "message": f"Activation leader remains {leader_name}. New activity will continue under this leader."})
        return jsonify({"ok": True, "message": f"Activation leader remains {leader_name}. Leader routing will start when the activation starts."})
    if running:
        return jsonify({"ok": True, "message": f"Activation leader set to {leader_name}. New activity will route to this leader."})
    return jsonify({"ok": True, "message": f"Activation leader set to {leader_name}. Leader routing will start when the activation starts."})


@activations_bp.post("/api/activations/<activation_id>/leader-location")
@_require_roles("agent", "manager")
def api_activation_leader_location_post(activation_id: str):
    _ident, user_id, _role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    if not aid or not user_id:
        return _json_error("Invalid activation or session.", 400)
    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)

    leader_id = str(activation.get("teamLeaderId") or "")
    if str(user_id) != leader_id:
        return _json_error("Only the selected activation leader can share location.", 403)
    if not is_activation_running(activation):
        return _json_error("Leader location sharing starts only after the activation has started.", 400)
    if not activation.get("teamLeaderLocationRequired"):
        return _json_error("Leader location tracking is not enabled for this activation.", 400)

    payload = request.get_json(silent=True) or {}
    try:
        latitude = float(payload.get("latitude"))
        longitude = float(payload.get("longitude"))
    except Exception:
        return _json_error("Valid latitude and longitude are required.")

    accuracy = _money(payload.get("accuracy"))
    now = datetime.utcnow()
    leader_locations_col.insert_one(
        {
            "activationId": aid,
            "leaderId": user_id,
            "leaderName": activation.get("teamLeaderName") or "",
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "source": (payload.get("source") or "browser").strip() or "browser",
            "recordedAt": now,
        }
    )
    activations_col.update_one(
        {"_id": aid},
        {"$set": {"leaderLastLocationAt": now, "leaderLastLatitude": latitude, "leaderLastLongitude": longitude, "leaderLastAccuracy": accuracy}},
    )
    return jsonify({"ok": True, "message": "Location updated."})


@activations_bp.get("/api/activations/<activation_id>/leader-location")
@_require_roles("admin")
def api_activation_leader_location_get(activation_id: str):
    aid = _safe_object_id(activation_id)
    if not aid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)
    return jsonify(_build_leader_location_payload(activation, aid))


@activations_bp.get("/admin/activations/<activation_id>/leader-location-data")
@_require_roles("admin")
def admin_activation_leader_location_data(activation_id: str):
    aid = _safe_object_id(activation_id)
    if not aid:
        return _json_error("Invalid activation id.")
    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)
    return jsonify(_build_leader_location_payload(activation, aid))


@activations_bp.post("/api/activations/<activation_id>/leader/customers/<customer_id>/mark-taken")
@_require_roles("agent", "manager")
def api_activation_leader_mark_customer_taken(activation_id: str, customer_id: str):
    ident, user_id, _role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    cid = _safe_object_id(customer_id)
    if not aid or not cid or not user_id:
        return _json_error("Invalid activation, customer, or session.", 400)

    activation = activations_col.find_one({"_id": aid})
    if not activation:
        return _json_error("Activation not found.", 404)

    leader_id = str(activation.get("teamLeaderId") or "")
    if not leader_id or str(user_id) != leader_id:
        return _json_error("Only the selected activation leader can mark money as taken.", 403)

    customer = customers_col.find_one(
        {"_id": cid, "activation_id": aid},
        {
            "name": 1,
            "date_registered": 1,
            "activation_leader_id": 1,
            "leader_money_taken": 1,
        },
    )
    if not customer:
        return _json_error("Customer not found for this activation.", 404)
    if not _customer_matches_leader_scope(customer, activation, leader_id):
        return _json_error("This customer is not in your leader summary.", 403)
    if customer.get("leader_money_taken"):
        return jsonify(
            {
                "ok": True,
                "message": "Money already marked as taken.",
                "customerId": str(cid),
                "moneyTaken": True,
            }
        )

    taken_by_name = ident.get("name") or ident.get("username") or activation.get("teamLeaderName") or "Leader"
    now = datetime.utcnow()
    customers_col.update_one(
        {"_id": cid},
        {
            "$set": {
                "leader_money_taken": True,
                "leader_money_taken_at": now,
                "leader_money_taken_by_id": str(user_id),
                "leader_money_taken_by_name": taken_by_name,
            }
        },
    )
    return jsonify(
        {
            "ok": True,
            "message": "Money marked as taken.",
            "customerId": str(cid),
            "moneyTaken": True,
            "moneyTakenAt": now.isoformat(),
        }
    )


@activations_bp.patch("/api/activations/<activation_id>/requests/<user_id>/approve")
@_require_roles("admin")
def api_request_approve(activation_id: str, user_id: str):
    ident, admin_id, role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    uid = _safe_object_id(user_id)
    if not aid or not uid:
        return _json_error("Invalid ids.")

    res = rsvps_col.update_one(
        {"activationId": aid, "userId": uid},
        {
            "$set": {
                "status": "approved",
                "reviewedAt": datetime.utcnow(),
                "reviewedBy": {"id": admin_id, "role": "admin"},
            }
        },
    )
    if res.matched_count == 0:
        return _json_error("Request not found.", 404)
    return jsonify({"ok": True, "message": "Request approved."})


@activations_bp.patch("/api/activations/<activation_id>/requests/<user_id>/reject")
@_require_roles("admin")
def api_request_reject(activation_id: str, user_id: str):
    ident, admin_id, role = _resolve_identity()
    aid = _safe_object_id(activation_id)
    uid = _safe_object_id(user_id)
    if not aid or not uid:
        return _json_error("Invalid ids.")

    res = rsvps_col.update_one(
        {"activationId": aid, "userId": uid},
        {
            "$set": {
                "status": "rejected",
                "reviewedAt": datetime.utcnow(),
                "reviewedBy": {"id": admin_id, "role": "admin"},
            }
        },
    )
    if res.matched_count == 0:
        return _json_error("Request not found.", 404)
    return jsonify({"ok": True, "message": "Request rejected."})


@activations_bp.get("/api/activations/<activation_id>/going")
@_require_roles("admin", "manager", "agent")
def api_going_list(activation_id: str):
    ident, user_id, role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return _json_error("Invalid activation id.")

    include = (request.args.get("include") or "").strip().lower()

    approved_rows = _build_people_rows(list(rsvps_col.find({"activationId": oid, "status": "approved"}).sort([("requestedAt", -1)])))

    if role != "admin" or include != "pending":
        return jsonify({"ok": True, "approved": approved_rows, "approvedCount": len(approved_rows)})

    pending_rows = _build_people_rows(list(rsvps_col.find({"activationId": oid, "status": "pending"}).sort([("requestedAt", -1)])))
    return jsonify(
        {
            "ok": True,
            "approved": approved_rows,
            "pending": pending_rows,
            "approvedCount": len(approved_rows),
            "pendingCount": len(pending_rows),
        }
    )


# Backward-compat alias for previous UI/button
@activations_bp.post("/api/activations/<activation_id>/rsvp")
@_require_roles("agent", "manager")
def api_rsvp_alias(activation_id: str):
    return api_request_going(activation_id)


# ---------------- Page routes ----------------

@activations_bp.get("/admin/activations")
@_require_roles("admin")
def admin_activations_page():
    return render_template("activations_admin_list.html")


@activations_bp.get("/admin/activations/new")
@_require_roles("admin")
def admin_activations_new_page():
    return render_template("activations_admin_new.html", debug_mode=bool(current_app.debug))


@activations_bp.get("/admin/activations/<activation_id>")
@_require_roles("admin")
def admin_activations_detail_page(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return redirect(url_for("activations.admin_activations_page"))

    doc = activations_col.find_one({"_id": oid})
    if not doc:
        return redirect(url_for("activations.admin_activations_page"))

    return render_template("activations_admin_detail.html", activation=_serialize_activation(doc))


@activations_bp.get("/admin/activations/<activation_id>/leader-tracking")
@_require_roles("admin")
def admin_activation_leader_tracking_page(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return redirect(url_for("activations.admin_activations_page"))
    doc = activations_col.find_one({"_id": oid})
    if not doc:
        return redirect(url_for("activations.admin_activations_page"))
    return render_template("activations_admin_leader_tracking.html", activation=_serialize_activation(doc))


@activations_bp.get("/admin/activations/<activation_id>/leader-runtime")
@_require_roles("admin")
def admin_activation_leader_runtime_page(activation_id: str):
    oid = _safe_object_id(activation_id)
    if not oid:
        return redirect(url_for("activations.admin_activations_page"))
    doc = activations_col.find_one({"_id": oid})
    if not doc:
        return redirect(url_for("activations.admin_activations_page"))
    return render_template(
        "activations_leader_runtime.html",
        activation=_serialize_activation(doc),
        role_base="admin",
        role_label="Admin",
    )


@activations_bp.get("/agent/activations")
@_require_roles("agent")
def agent_activations_page():
    return render_template("activations_role_list.html", role_label="Agent", role_base="agent")


@activations_bp.get("/agent/activations/my-group")
@_require_roles("agent")
def agent_my_group_page():
    return redirect(url_for("activations.agent_activations_page"))


@activations_bp.get("/manager/activations")
@_require_roles("manager")
def manager_activations_page():
    return render_template("activations_role_list.html", role_label="Manager", role_base="manager")


@activations_bp.get("/activation/<activation_id>/leader-runtime")
@_require_roles("agent", "manager")
def activation_leader_runtime_page(activation_id: str):
    _ident, user_id, role = _resolve_identity()
    oid = _safe_object_id(activation_id)
    if not oid:
        return redirect(url_for("activations.agent_activations_page" if role == "agent" else "activations.manager_activations_page"))
    doc = activations_col.find_one({"_id": oid})
    if not doc:
        return redirect(url_for("activations.agent_activations_page" if role == "agent" else "activations.manager_activations_page"))
    if not _user_was_activation_leader(doc, str(user_id or "")) and not _user_is_approved_for_activation(oid, user_id):
        return "Forbidden", 403
    return render_template(
        "activations_leader_runtime.html",
        activation=_serialize_activation(doc),
        role_base=role,
        role_label=role.title(),
    )
