from __future__ import annotations

from datetime import datetime, timedelta
import math
import re
import time
from typing import Any

from bson.objectid import ObjectId
from flask import Blueprint, jsonify, request

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity, role_required
from routes.executive_archive_customers import _archive_one, _unarchive_one, _pick_code, _date_str

customer_support_customers_bp = Blueprint(
    "customer_support_customers",
    __name__,
    url_prefix="/api/customer-support/customers",
)

customers_col = db.customers
payments_col = db.payments
followups_col = db.customer_support_followups
users_col = db.users
archived_customers_col = db["Archived_customers"]

_FILTERS_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_FILTERS_TTL_SECONDS = 300


def _archive_customer_row(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or ""),
        "name": doc.get("name") or "N/A",
        "phone": doc.get("phone_number") or doc.get("phone") or "N/A",
        "branch": doc.get("agent_branch") or doc.get("branch") or "",
        "code": _pick_code(doc),
        "status": doc.get("status") or "",
        "archivedAt": _date_str(doc.get("archived_at")),
        "archivedReason": doc.get("archived_reason") or "",
    }


def _archive_search_query(term: str) -> dict[str, Any]:
    escaped = re.escape(term)
    clauses = [{field: {"$regex": escaped, "$options": "i"}} for field in
               ("name", "phone_number", "customer_code", "account_number", "account_no", "code", "customer_id")]
    if ObjectId.is_valid(term):
        clauses.append({"_id": ObjectId(term)})
    return {"$or": clauses}


@customer_support_customers_bp.get("/archive/active")
@role_required("customer_support")
def support_archive_active_customers():
    term = str(request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify(ok=True, customers=[])
    rows = customers_col.find(_archive_search_query(term)).sort("name", 1).limit(50)
    return jsonify(ok=True, customers=[_archive_customer_row(row) for row in rows])


@customer_support_customers_bp.get("/archive/archived")
@role_required("customer_support")
def support_archived_customers():
    term = str(request.args.get("q") or "").strip()
    try: page = max(int(request.args.get("page") or 1), 1)
    except ValueError: page = 1
    query = _archive_search_query(term) if term else {}
    per_page = 12
    total = archived_customers_col.count_documents(query)
    rows = archived_customers_col.find(query).sort("archived_at", -1).skip((page - 1) * per_page).limit(per_page)
    return jsonify(ok=True, customers=[_archive_customer_row(row) for row in rows], page=page,
                   total=total, totalPages=max(math.ceil(total / per_page), 1))


@customer_support_customers_bp.post("/archive")
@role_required("customer_support")
def support_archive_customers():
    data = request.get_json(silent=True) or {}; ids = data.get("customer_ids") or []
    if not ids: return jsonify(ok=False, message="Select at least one customer."), 400
    identity = get_current_identity(); counts = {"archived": 0, "skipped": 0, "failed": 0}; results = []
    reason = str(data.get("reason") or "Archived by Customer Support").strip()
    for raw_id in ids:
        status, message = _archive_one(str(raw_id), reason, "customer_support_manual_archive", str(identity.get("user_id") or ""), "customer_support")
        counts[status] += 1; results.append({"id": str(raw_id), "status": status, "message": message})
    return jsonify(ok=True, counts=counts, results=results)


@customer_support_customers_bp.post("/archive/unarchive")
@role_required("customer_support")
def support_unarchive_customers():
    data = request.get_json(silent=True) or {}; ids = data.get("customer_ids") or []
    if not ids: return jsonify(ok=False, message="Select at least one archived customer."), 400
    identity = get_current_identity(); counts = {"unarchived": 0, "skipped": 0, "failed": 0}; results = []
    reason = str(data.get("reason") or "Restored by Customer Support").strip()
    for raw_id in ids:
        status, message = _unarchive_one(str(raw_id), reason, str(identity.get("user_id") or ""), "customer_support")
        counts[status] += 1; results.append({"id": str(raw_id), "status": status, "message": message})
    return jsonify(ok=True, counts=counts, results=results)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(text[:26], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_short_dt(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d %b %Y") if dt else ""


def _variants(value: Any) -> list[Any]:
    values: list[Any] = []
    if value is None:
        return values
    values.append(value)
    text = str(value).strip()
    if text:
        values.append(text)
        if ObjectId.is_valid(text):
            try:
                values.append(ObjectId(text))
            except Exception:
                pass
    return values


def _segment_for(balance: float, product_count: int) -> str:
    if balance >= 3000 or product_count >= 3:
        return "VIP"
    if balance >= 1000 or product_count >= 2:
        return "Premium"
    return "Standard"


def _status_for(raw_status: Any, balance: float, last_payment_at: Any) -> str:
    status = str(raw_status or "").strip().lower()
    mapped = {
        "active": "Active",
        "inactive": "Inactive",
        "not active": "Inactive",
        "suspended": "Suspended",
        "overdue": "Overdue",
    }.get(status)
    if mapped:
        return mapped

    dt = _parse_dt(last_payment_at)
    if balance > 0 and (not dt or dt < datetime.utcnow() - timedelta(days=45)):
        return "Overdue"
    if dt:
        return "Active"
    return "Inactive"


def _account_type(purchases: list[dict], balance: float) -> str:
    if balance > 0:
        return "Instalment"
    if purchases:
        return "Cash"
    return "Unassigned"


def _initials(name: str) -> str:
    parts = [part[:1].upper() for part in str(name or "").split()[:2] if part]
    return "".join(parts) or "CU"


def _base_query(q: str, branch: str, agent: str, status: str) -> dict[str, Any]:
    query: dict[str, Any] = {}
    and_clauses: list[dict[str, Any]] = []
    if q:
        and_clauses.append(
            {
                "$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"phone_number": {"$regex": q, "$options": "i"}},
                    {"email": {"$regex": q, "$options": "i"}},
                ]
            }
        )
    if branch:
        and_clauses.append(
            {
                "$or": [
                    {"agent_branch": {"$regex": f"^{branch}$", "$options": "i"}},
                    {"branch": {"$regex": f"^{branch}$", "$options": "i"}},
                ]
            }
        )
    if agent:
        and_clauses.append({"agent_name": {"$regex": f"^{agent}$", "$options": "i"}})
    if status:
        if status.lower() == "inactive":
            and_clauses.append({"status": {"$in": ["inactive", "Inactive", "not active", "Not Active"]}})
        else:
            and_clauses.append({"status": {"$regex": f"^{status}$", "$options": "i"}})
    if and_clauses:
        query["$and"] = and_clauses
    return query


def _cached_filter_options() -> dict[str, Any]:
    from services.manager_suite_scope import manager_scope
    restricted = manager_scope(db) is not None
    now = time.time()
    if not restricted and _FILTERS_CACHE["payload"] and _FILTERS_CACHE["expires_at"] > now:
        return _FILTERS_CACHE["payload"]

    payload = {
        "branches": sorted(
            {
                str(value).strip()
                for value in (
                    list(users_col.distinct("branch", {"role": "manager"}))
                    + list(customers_col.distinct("agent_branch"))
                    + list(customers_col.distinct("branch"))
                )
                if str(value or "").strip()
            }
        ),
        "agents": sorted(
            {
                str(value).strip()
                for value in (
                    list(users_col.distinct("name", {"role": "agent"}))
                    + list(customers_col.distinct("agent_name"))
                )
                if str(value or "").strip()
            }
        ),
    }
    if not restricted:
        _FILTERS_CACHE["payload"] = payload
        _FILTERS_CACHE["expires_at"] = now + _FILTERS_TTL_SECONDS
    return payload


def _payment_summary(customer_ids: list[Any]) -> dict[str, dict[str, Any]]:
    if not customer_ids:
        return {}

    pipeline = [
        {
            "$match": {
                "customer_id": {"$in": customer_ids},
                "payment_type": {"$nin": ["WITHDRAWAL", "SUSU"]},
            }
        },
        {
            "$project": {
                "customer_id": 1,
                "amount_value": {"$toDouble": {"$ifNull": ["$amount", 0]}},
                "payment_date": {"$ifNull": ["$timestamp", "$date"]},
            }
        },
        {
            "$group": {
                "_id": "$customer_id",
                "paid": {"$sum": "$amount_value"},
                "last_payment_at": {"$max": "$payment_date"},
            }
        },
    ]

    summary: dict[str, dict[str, Any]] = {}
    for row in payments_col.aggregate(pipeline, allowDiskUse=False):
        summary[str(row.get("_id") or "")] = {
            "paid": round(_to_float(row.get("paid")), 2),
            "last_payment_at": row.get("last_payment_at"),
        }
    return summary


def _agent_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agent_oids: list[ObjectId] = []
    for row in rows:
        agent_id = row.get("agent_id")
        if not agent_id:
            continue
        try:
            agent_oids.append(agent_id if isinstance(agent_id, ObjectId) else ObjectId(str(agent_id)))
        except Exception:
            continue

    if not agent_oids:
        return {}

    return {
        str(doc.get("_id")): {
            "name": doc.get("name") or "Agent",
            "branch": doc.get("branch") or "",
        }
        for doc in users_col.find(
            {"_id": {"$in": list(set(agent_oids))}},
            {"_id": 1, "name": 1, "branch": 1},
        )
    }


@customer_support_customers_bp.get("")
@role_required("customer_support")
def customer_support_customers():
    q = (request.args.get("q") or "").strip()
    branch = (request.args.get("branch") or "").strip()
    agent = (request.args.get("agent") or "").strip()
    status = (request.args.get("status") or "").strip()
    page = max(int(request.args.get("page") or 1), 1)
    per_page = max(min(int(request.args.get("per_page") or 20), 24), 1)

    query = _base_query(q, branch, agent, status)
    projection = {
        "name": 1,
        "phone_number": 1,
        "email": 1,
        "image_url": 1,
        "status": 1,
        "lead_stage": 1,
        "location": 1,
        "occupation": 1,
        "date_registered": 1,
        "created_at": 1,
        "dob": 1,
        "gender": 1,
        "ghana_card_number": 1,
        "ic": 1,
        "branch": 1,
        "agent_branch": 1,
        "agent_name": 1,
        "agent_id": 1,
        "purchases": 1,
    }

    total_count = customers_col.count_documents(query)
    total_pages = max(math.ceil(total_count / per_page), 1)

    rows = list(
        customers_col.find(query, projection)
        .sort([("created_at", -1), ("name", 1)])
        .skip((page - 1) * per_page)
        .limit(per_page)
    )

    customer_ids: list[Any] = []
    for row in rows:
        if row.get("_id") is not None:
            customer_ids.extend(_variants(row.get("_id")))

    payment_summary = _payment_summary(customer_ids)
    followup_counts: dict[str, int] = {}
    for item in followups_col.aggregate([{"$match": {"customer_id": {"$in": customer_ids}, "status": {"$ne": "done"}}}, {"$group": {"_id": "$customer_id", "count": {"$sum": 1}}}]):
        followup_counts[str(item.get("_id"))] = int(item.get("count") or 0)
    agent_lookup = _agent_lookup(rows)
    filters = _cached_filter_options()

    customers: list[dict[str, Any]] = []
    active_count = 0
    inactive_count = 0
    overdue_count = 0
    vip_count = 0

    for row in rows:
        customer_id = str(row.get("_id") or "")
        purchases = row.get("purchases") or []
        if not isinstance(purchases, list):
            purchases = []
        agent_meta = agent_lookup.get(str(row.get("agent_id") or ""), {})

        product_names: list[str] = []
        total_debt = 0.0
        for purchase in purchases:
            if not isinstance(purchase, dict):
                continue
            product = purchase.get("product") or {}
            if not isinstance(product, dict):
                product = {}
            name = str(product.get("name") or purchase.get("product_name") or "").strip()
            if name:
                product_names.append(name)
            total_debt += _to_float(product.get("total"))

        payment_info = payment_summary.get(customer_id, {})
        total_paid = round(_to_float(payment_info.get("paid")), 2)
        balance = max(round(total_debt - total_paid, 2), 0.0)
        segment_name = _segment_for(balance, len(product_names))
        status_name = _status_for(row.get("status"), balance, payment_info.get("last_payment_at"))
        account_type = _account_type(purchases, balance)
        join_dt = row.get("date_registered") or row.get("created_at")
        last_dt = payment_info.get("last_payment_at") or join_dt

        if status_name == "Active":
            active_count += 1
        elif status_name == "Overdue":
            overdue_count += 1
        else:
            inactive_count += 1
        if segment_name == "VIP":
            vip_count += 1

        customers.append(
            {
                "id": customer_id,
                "name": row.get("name") or "Unnamed Customer",
                "phone": row.get("phone_number") or "",
                "email": row.get("email") or "",
                "branch": agent_meta.get("branch") or row.get("agent_branch") or row.get("branch") or "Unassigned",
                "agent": agent_meta.get("name") or row.get("agent_name") or "Unassigned",
                "agentInitials": _initials(agent_meta.get("name") or row.get("agent_name") or ""),
                "products": product_names,
                "productCount": len(product_names),
                "balance": balance,
                "balanceFormatted": f"GHS {balance:,.2f}",
                "status": status_name,
                "segment": segment_name,
                "joinDate": _format_short_dt(join_dt),
                "lastInteraction": _format_short_dt(last_dt) or "No activity yet",
                "lastInteractionRaw": _parse_dt(last_dt).isoformat() if _parse_dt(last_dt) else "",
                "tickets": 0,
                "csat": 0,
                "accountType": account_type,
                "city": row.get("location") or "",
                "ic": row.get("ghana_card_number") or row.get("ic") or "",
                "dob": _format_short_dt(row.get("dob")),
                "gender": row.get("gender") or "",
                "imageUrl": row.get("image_url") or "",
                "leadStage": row.get("lead_stage") or "customer",
                "totalPaid": total_paid,
                "occupation": row.get("occupation") or "",
                "followUpCount": followup_counts.get(customer_id, 0),
            }
        )

    return jsonify(
        {
            "ok": True,
            "customers": customers,
            "stats": {
                "total_customers": total_count,
                "active_count": active_count,
                "inactive_count": inactive_count,
                "overdue_count": overdue_count,
                "vip_count": vip_count,
                "followup_count": sum(followup_counts.values()),
            },
            "filters": {
                "branches": filters["branches"],
                "agents": filters["agents"],
                "statuses": ["Active", "Inactive", "Suspended", "Overdue"],
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total_count,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }
    )
