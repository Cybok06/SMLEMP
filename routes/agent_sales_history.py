from datetime import datetime, timedelta
from threading import Lock
from time import time

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request, session
from flask_login import current_user

from db import db

agent_sales_history_bp = Blueprint(
    "agent_sales_history",
    __name__,
    url_prefix="/agent/sales-history",
)

payments_col = db["payments"]
customers_col = db["customers"]
users_col = db["users"]

_CACHE_LOCK = Lock()
_CACHE = {}
_CACHE_TTL_SECONDS = 30
_DETAIL_ROWS_LIMIT = 300


def _ensure_indexes():
    try:
        payments_col.create_index(
            [("agent_id", 1), ("date", 1), ("payment_type", 1), ("customer_id", 1)],
            name="idx_agent_date_type_customer",
        )
        users_col.create_index([("role", 1)], name="idx_users_role_only")
    except Exception:
        pass


_ensure_indexes()


def _safe_oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _cache_get(key):
    now = time()
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if not row:
            return None
        if row["expires_at"] < now:
            _CACHE.pop(key, None)
            return None
        return row["value"]


def _cache_set(key, value, ttl=_CACHE_TTL_SECONDS):
    with _CACHE_LOCK:
        _CACHE[key] = {
            "value": value,
            "expires_at": time() + max(int(ttl), 1),
        }


def _agent_id_from_auth():
    role = str(getattr(current_user, "role", "") or "").strip().lower()
    if getattr(current_user, "is_authenticated", False) and role == "agent":
        return str(current_user.id)
    aid = session.get("agent_id")
    if aid:
        return str(aid)
    return None


def _agent_doc(agent_id):
    cache_key = ("agent_doc", str(agent_id))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    agent_oid = _safe_oid(agent_id)
    if agent_oid:
        doc = users_col.find_one(
            {"_id": agent_oid, "role": "agent"},
            {"name": 1, "branch": 1, "phone": 1},
        )
        if doc:
            _cache_set(cache_key, doc, ttl=120)
            return doc
    doc = users_col.find_one(
        {"_id": str(agent_id), "role": "agent"},
        {"name": 1, "branch": 1, "phone": 1},
    )
    if doc:
        _cache_set(cache_key, doc, ttl=120)
    return doc


def _parse_ymd(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _resolve_range(args):
    today = datetime.utcnow().date()
    mode = (args.get("range") or "month").strip().lower()

    if mode == "today":
        start_d = today
        end_d = today
    elif mode == "week":
        start_d = today - timedelta(days=today.weekday())
        end_d = today
    elif mode == "custom":
        start_d = _parse_ymd(args.get("start") or "")
        end_d = _parse_ymd(args.get("end") or "")
        if not start_d and not end_d:
            start_d = today.replace(day=1)
            end_d = today
            mode = "month"
        else:
            start_d = start_d or end_d
            end_d = end_d or start_d
    else:
        start_d = today.replace(day=1)
        end_d = today
        mode = "month"

    if end_d < start_d:
        start_d, end_d = end_d, start_d

    days = (end_d - start_d).days + 1
    label_map = {
        "today": "Today",
        "week": "This Week",
        "month": "This Month",
        "custom": "Custom Range",
    }
    return {
        "mode": mode,
        "start": start_d.strftime("%Y-%m-%d"),
        "end": end_d.strftime("%Y-%m-%d"),
        "days": max(days, 1),
        "label": label_map.get(mode, "This Month"),
    }


def _to_amount_and_core_fields_stage():
    return {
        "$project": {
            "date": 1,
            "agent_id": {"$toString": {"$ifNull": ["$agent_id", ""]}},
            "customer_key": {"$toString": {"$ifNull": ["$customer_id", ""]}},
            "method_norm": {"$ifNull": ["$method", "Unknown"]},
            "amount_num": {
                "$convert": {
                    "input": {"$ifNull": ["$amount", 0]},
                    "to": "double",
                    "onError": 0,
                    "onNull": 0,
                }
            },
        }
    }


def _payments_match(agent_id, start_str, end_str):
    return {
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": start_str, "$lte": end_str},
        "agent_id": str(agent_id),
    }


@agent_sales_history_bp.route("/", methods=["GET"])
def agent_sales_history_page():
    agent_id = _agent_id_from_auth()
    if not agent_id:
        return "Forbidden", 403

    agent = _agent_doc(agent_id)
    if not agent:
        return "Forbidden", 403

    return render_template(
        "agent_sales_history.html",
        agent_name=agent.get("name", "Agent"),
        agent_branch=agent.get("branch", ""),
        default_start=datetime.utcnow().replace(day=1).strftime("%Y-%m-%d"),
        default_end=datetime.utcnow().strftime("%Y-%m-%d"),
    )


@agent_sales_history_bp.route("/data", methods=["GET"])
def agent_sales_history_data():
    agent_id = _agent_id_from_auth()
    if not agent_id:
        return jsonify(ok=False, message="Unauthorized"), 403

    agent = _agent_doc(agent_id)
    if not agent:
        return jsonify(ok=False, message="Agent not found"), 404

    r = _resolve_range(request.args)
    cache_key = (
        "data",
        str(agent_id),
        r["mode"],
        r["start"],
        r["end"],
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    match = _payments_match(agent_id, r["start"], r["end"])
    pipeline = [
        {"$match": match},
        _to_amount_and_core_fields_stage(),
        {
            "$facet": {
                "summary": [
                    {
                        "$group": {
                            "_id": None,
                            "total_sales": {"$sum": "$amount_num"},
                            "payments_count": {"$sum": 1},
                        }
                    }
                ],
                "customers_count": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {"$group": {"_id": "$customer_key"}},
                    {"$count": "count"},
                ],
                "daily_sales": [
                    {"$group": {"_id": "$date", "sales": {"$sum": "$amount_num"}, "payments_count": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "daily_customers": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {"$group": {"_id": {"date": "$date", "customer_key": "$customer_key"}}},
                    {"$group": {"_id": "$_id.date", "customers_paid": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "agent_sales": [
                    {"$group": {"_id": "$date", "sales": {"$sum": "$amount_num"}, "payments_count": {"$sum": 1}}},
                    {
                        "$group": {
                            "_id": None,
                            "total_sales": {"$sum": "$sales"},
                            "payments_count": {"$sum": "$payments_count"},
                            "work_days": {"$sum": {"$cond": [{"$gt": ["$payments_count", 10]}, 1, 0]}},
                        }
                    },
                ],
                "methods": [
                    {"$group": {"_id": "$method_norm", "count": {"$sum": 1}, "amount": {"$sum": "$amount_num"}}},
                    {"$sort": {"amount": -1}},
                ],
            }
        },
    ]

    agg = list(payments_col.aggregate(pipeline, allowDiskUse=True))
    payload = agg[0] if agg else {
        "summary": [],
        "customers_count": [],
        "daily_sales": [],
        "daily_customers": [],
        "agent_sales": [],
        "methods": [],
    }

    summary_doc = (payload.get("summary") or [{}])[0]
    agent_doc = (payload.get("agent_sales") or [{}])[0]
    total_sales = float(summary_doc.get("total_sales", 0) or 0)
    total_payments = int(summary_doc.get("payments_count", 0) or 0)
    customers_paid_count = int(((payload.get("customers_count") or [{}])[0]).get("count", 0) or 0)
    daily_avg_sales = total_sales / r["days"] if r["days"] else 0.0
    work_days = int(agent_doc.get("work_days", 0) or 0)

    daily_customers_map = {
        str(d.get("_id")): int(d.get("customers_paid", 0) or 0)
        for d in (payload.get("daily_customers") or [])
    }
    daily_trend = [
        {
            "date": d.get("_id"),
            "sales": float(d.get("sales", 0) or 0),
            "payments_count": int(d.get("payments_count", 0) or 0),
            "customers_paid": daily_customers_map.get(str(d.get("_id") or ""), 0),
        }
        for d in (payload.get("daily_sales") or [])
    ]

    methods = []
    cash_amount = 0.0
    other_amount = 0.0
    for m in payload.get("methods") or []:
        name = str(m.get("_id") or "Unknown")
        amount = float(m.get("amount", 0) or 0)
        count = int(m.get("count", 0) or 0)
        methods.append({"method": name, "amount": round(amount, 2), "count": count})
        if name.lower() == "cash":
            cash_amount += amount
        else:
            other_amount += amount

    table_row = {
        "agent_id": str(agent_id),
        "agent_name": agent.get("name", "Agent"),
        "branch": agent.get("branch", ""),
        "total_sales": round(total_sales, 2),
        "payments_count": total_payments,
        "customers_paid": customers_paid_count,
        "work_days": work_days,
        "avg_per_day": round(daily_avg_sales, 2),
        "performance_pct": 100.0 if total_sales > 0 else 0.0,
    }

    result = dict(
        ok=True,
        range={
            "mode": r["mode"],
            "label": r["label"],
            "start": r["start"],
            "end": r["end"],
            "days": r["days"],
        },
        summary={
            "total_sales": round(total_sales, 2),
            "total_payments_count": total_payments,
            "total_customers_paid": customers_paid_count,
            "total_active_agents": 1 if total_payments > 0 else 0,
            "daily_average_sales": round(daily_avg_sales, 2),
            "best_performing_agent": agent.get("name", "Agent"),
            "best_performing_agent_sales": round(total_sales, 2),
            "total_attendance": customers_paid_count,
            "total_work_days": work_days,
            "total_agents": 1,
            "inactive_agents": 0 if total_payments > 0 else 1,
        },
        table=[table_row],
        charts={
            "daily_sales_trend": daily_trend,
            "sales_per_agent": [{"agent_name": agent.get("name", "Agent"), "sales": round(total_sales, 2)}],
            "active_vs_inactive_agents": {"active": 1 if total_payments > 0 else 0, "inactive": 0 if total_payments > 0 else 1},
        },
        payment_methods=methods,
        payment_methods_cash_vs_others={
            "cash": round(cash_amount, 2),
            "others": round(other_amount, 2),
        },
    )
    _cache_set(cache_key, result)
    return jsonify(result)


@agent_sales_history_bp.route("/agent/<agent_id>/details", methods=["GET"])
def agent_sales_history_agent_details(agent_id):
    auth_agent_id = _agent_id_from_auth()
    if not auth_agent_id:
        return jsonify(ok=False, message="Unauthorized"), 403
    if str(agent_id) != str(auth_agent_id):
        return jsonify(ok=False, message="Forbidden: you can only view your own details"), 403

    agent = _agent_doc(auth_agent_id)
    if not agent:
        return jsonify(ok=False, message="Agent not found"), 404

    r = _resolve_range(request.args)
    cache_key = ("detail", str(auth_agent_id), str(agent_id), r["mode"], r["start"], r["end"])
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    pipeline = [
        {"$match": _payments_match(auth_agent_id, r["start"], r["end"])},
        _to_amount_and_core_fields_stage(),
        {
            "$facet": {
                "daily_sales": [
                    {"$group": {"_id": "$date", "sales": {"$sum": "$amount_num"}, "payments_count": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "daily_customers": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {"$group": {"_id": {"date": "$date", "customer_key": "$customer_key"}}},
                    {"$group": {"_id": "$_id.date", "customers_paid": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "customer_totals": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {
                        "$group": {
                            "_id": "$customer_key",
                            "total_sales": {"$sum": "$amount_num"},
                            "payments_count": {"$sum": 1},
                            "methods": {"$addToSet": "$method_norm"},
                        }
                    },
                    {"$sort": {"total_sales": -1}},
                ],
                "methods": [
                    {"$group": {"_id": "$method_norm", "amount": {"$sum": "$amount_num"}, "count": {"$sum": 1}}},
                    {"$sort": {"amount": -1}},
                ],
            }
        },
    ]

    agg = list(payments_col.aggregate(pipeline, allowDiskUse=True))
    payload = agg[0] if agg else {"daily_sales": [], "daily_customers": [], "customer_totals": [], "methods": []}

    customer_rows = payload.get("customer_totals") or []
    total_customers_paid = len(customer_rows)
    if len(customer_rows) > _DETAIL_ROWS_LIMIT:
        customer_rows = customer_rows[:_DETAIL_ROWS_LIMIT]

    customer_oid_map = {}
    for row in customer_rows:
        key = str(row.get("_id") or "").strip()
        oid = _safe_oid(key)
        if oid:
            customer_oid_map[key] = oid

    customer_docs = {}
    if customer_oid_map:
        for c in customers_col.find({"_id": {"$in": list(customer_oid_map.values())}}, {"name": 1, "phone_number": 1}):
            customer_docs[str(c["_id"])] = c

    customers_paid = []
    for row in customer_rows:
        key = str(row.get("_id") or "").strip()
        if not key:
            continue
        cdoc = customer_docs.get(key, {})
        customers_paid.append(
            {
                "customer_id": key,
                "name": cdoc.get("name", "Unknown"),
                "phone": cdoc.get("phone_number", ""),
                "total_sales": round(float(row.get("total_sales", 0) or 0), 2),
                "payments_count": int(row.get("payments_count", 0) or 0),
                "methods": sorted(list(set(row.get("methods") or []))),
            }
        )

    daily_customers_map = {str(d.get("_id")): int(d.get("customers_paid", 0) or 0) for d in (payload.get("daily_customers") or [])}
    daily_breakdown = [
        {
            "date": d.get("_id"),
            "sales": round(float(d.get("sales", 0) or 0), 2),
            "payments_count": int(d.get("payments_count", 0) or 0),
            "customers_paid": daily_customers_map.get(str(d.get("_id") or ""), 0),
        }
        for d in (payload.get("daily_sales") or [])
    ]

    methods = []
    cash_amount = 0.0
    other_amount = 0.0
    for row in payload.get("methods") or []:
        method = str(row.get("_id") or "Unknown")
        amount = float(row.get("amount", 0) or 0)
        count = int(row.get("count", 0) or 0)
        methods.append({"method": method, "amount": round(amount, 2), "count": count})
        if method.lower() == "cash":
            cash_amount += amount
        else:
            other_amount += amount

    result = dict(
        ok=True,
        agent={"id": str(auth_agent_id), "name": agent.get("name", "Agent"), "branch": agent.get("branch", ""), "phone": agent.get("phone", "")},
        range={"mode": r["mode"], "start": r["start"], "end": r["end"], "days": r["days"]},
        daily_breakdown=daily_breakdown,
        customers_paid=customers_paid,
        customers_paid_total_count=total_customers_paid,
        customers_paid_truncated=total_customers_paid > _DETAIL_ROWS_LIMIT,
        top_customers=customers_paid[:5],
        payment_methods=methods,
        payment_methods_cash_vs_others={"cash": round(cash_amount, 2), "others": round(other_amount, 2)},
    )
    _cache_set(cache_key, result)
    return jsonify(result)
