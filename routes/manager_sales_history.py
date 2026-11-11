from datetime import datetime, timedelta
from threading import Lock
from time import time

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request, session
from flask_login import current_user

from db import db

manager_sales_history_bp = Blueprint(
    "manager_sales_history",
    __name__,
    url_prefix="",
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
            [("manager_id", 1), ("date", 1), ("payment_type", 1), ("agent_id", 1)],
            name="idx_mgr_date_type_agent",
        )
        payments_col.create_index(
            [("manager_id", 1), ("agent_id", 1), ("date", 1), ("payment_type", 1), ("customer_id", 1)],
            name="idx_mgr_agent_date_type_customer",
        )
        users_col.create_index([("role", 1), ("manager_id", 1)], name="idx_users_role_manager")
    except Exception:
        pass


_ensure_indexes()


def _safe_oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _auth_context():
    role = ""
    user_id = ""

    if getattr(current_user, "is_authenticated", False):
        role = str(getattr(current_user, "role", "") or "").strip().lower()
        user_id = str(getattr(current_user, "id", "") or "").strip()

    if not role:
        role = str(session.get("role", "") or "").strip().lower()
        user_id = str(session.get("user_id", "") or "").strip()

    if role not in {"manager", "executive", "admin"}:
        if session.get("manager_id"):
            role = "manager"
            user_id = str(session.get("manager_id"))
        elif session.get("executive_id"):
            role = "executive"
            user_id = str(session.get("executive_id"))
        elif session.get("admin_id"):
            role = "admin"
            user_id = str(session.get("admin_id"))

    if role not in {"manager", "executive", "admin"} or not user_id:
        return None

    return {"role": role, "user_id": user_id}


def _manager_identity_values(manager_id):
    manager_str_values = {str(manager_id)}
    manager_oid_values = []

    manager_oid = _safe_oid(manager_id)
    if manager_oid:
        manager_oid_values.append(manager_oid)
        manager_doc = users_col.find_one({"_id": manager_oid, "role": "manager"}, {"_id": 1})
        if manager_doc:
            manager_str_values.add(str(manager_doc["_id"]))
    else:
        manager_doc = users_col.find_one({"_id": str(manager_id), "role": "manager"}, {"_id": 1})
        if manager_doc:
            manager_str_values.add(str(manager_doc["_id"]))
            if isinstance(manager_doc["_id"], ObjectId):
                manager_oid_values.append(manager_doc["_id"])

    return manager_str_values, manager_oid_values


def _manager_doc(manager_id):
    cache_key = ("manager_doc", str(manager_id))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    manager_oid = _safe_oid(manager_id)
    if manager_oid:
        doc = users_col.find_one(
            {"_id": manager_oid, "role": "manager"},
            {"name": 1, "branch": 1, "phone": 1},
        )
        if doc:
            _cache_set(cache_key, doc, ttl=120)
            return doc
    doc = users_col.find_one(
        {"_id": str(manager_id), "role": "manager"},
        {"name": 1, "branch": 1, "phone": 1},
    )
    if doc:
        _cache_set(cache_key, doc, ttl=120)
    return doc


def _manager_agents(manager_id):
    cache_key = ("manager_agents", str(manager_id))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    manager_str_values, manager_oid_values = _manager_identity_values(manager_id)
    clauses = [{"manager_id": {"$in": list(manager_str_values)}}]
    if manager_oid_values:
        clauses.append({"manager_id": {"$in": manager_oid_values}})

    query = {"role": "agent", "$or": clauses}
    agents = list(
        users_col.find(
            query,
            {"name": 1, "branch": 1, "phone": 1},
        ).sort("name", 1)
    )
    result = [
        {
            "id": str(a["_id"]),
            "name": a.get("name", "Unknown"),
            "branch": a.get("branch", ""),
            "phone": a.get("phone", ""),
        }
        for a in agents
    ]
    _cache_set(cache_key, result, ttl=120)
    return result


def _all_agents():
    cache_key = ("all_agents",)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    agents = list(
        users_col.find(
            {"role": "agent"},
            {"name": 1, "branch": 1, "phone": 1},
        ).sort("name", 1)
    )
    result = [
        {
            "id": str(a["_id"]),
            "name": a.get("name", "Unknown"),
            "branch": a.get("branch", ""),
            "phone": a.get("phone", ""),
        }
        for a in agents
    ]
    _cache_set(cache_key, result, ttl=120)
    return result


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
        "start_date": start_d,
        "end_date": end_d,
        "start": start_d.strftime("%Y-%m-%d"),
        "end": end_d.strftime("%Y-%m-%d"),
        "days": max(days, 1),
        "label": label_map.get(mode, "This Month"),
    }


def _payments_match(manager_id, agent_ids, start_str, end_str, single_agent_id=None):
    match = {
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": start_str, "$lte": end_str},
    }

    if manager_id:
        manager_str_values, manager_oid_values = _manager_identity_values(manager_id)
        manager_clauses = [{"manager_id": {"$in": list(manager_str_values)}}]
        if manager_oid_values:
            manager_clauses.append({"manager_id": {"$in": manager_oid_values}})
        match["$or"] = manager_clauses

    if single_agent_id:
        agent_clauses = [{"agent_id": str(single_agent_id)}]
        agent_oid = _safe_oid(single_agent_id)
        if agent_oid:
            agent_clauses.append({"agent_id": agent_oid})
        if len(agent_clauses) == 1:
            match["agent_id"] = str(single_agent_id)
        else:
            match["$and"] = [{"$or": agent_clauses}]
    else:
        # Keep manager/admin/executive scope aligned with today-live:
        # - manager: scoped by manager_id variants (already applied above)
        # - executive/admin: no hard agent_id restriction unless explicitly filtered
        # This prevents dropping valid payments whose agent_id datatype/value
        # doesn't match the current users list.
        pass
    return match


def _flatten_nested_sets(nested):
    out = set()
    for block in nested or []:
        if isinstance(block, list):
            for val in block:
                sval = str(val or "").strip()
                if sval and sval.lower() != "none":
                    out.add(sval)
        else:
            sval = str(block or "").strip()
            if sval and sval.lower() != "none":
                out.add(sval)
    return out


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


@manager_sales_history_bp.route("/manager/sales-history/", methods=["GET"])
@manager_sales_history_bp.route("/executive/sales-history/", methods=["GET"])
@manager_sales_history_bp.route("/admin/sales-history/", methods=["GET"])
def manager_sales_history_page():
    auth = _auth_context()
    if not auth:
        return "Forbidden", 403

    role = auth["role"]
    sidebar_template = {
        "manager": "manager_sidebar.html",
        "executive": "executive_sidebar.html",
        "admin": "admin_sidebar.html",
    }.get(role, "manager_sidebar.html")

    page_title = "Sales History"
    viewer_label = "Viewer"
    viewer_value = role.title()
    branch_label = "Scope"
    branch_value = "All branches"

    if role == "manager":
        manager = _manager_doc(auth["user_id"])
        if not manager:
            return "Forbidden", 403
        page_title = "Manager Sales History"
        viewer_label = "Manager"
        viewer_value = manager.get("name", "Manager")
        branch_label = "Branch"
        branch_value = manager.get("branch", "") or "-"
    elif role == "executive":
        page_title = "Executive Sales History"
        viewer_label = "Executive"
    elif role == "admin":
        page_title = "Admin Sales History"
        viewer_label = "Admin"
    return render_template(
        "manager_sales_history.html",
        page_title=page_title,
        viewer_label=viewer_label,
        viewer_value=viewer_value,
        branch_label=branch_label,
        branch_value=branch_value,
        sidebar_template=sidebar_template,
        api_base="/manager/sales-history" if role == "manager" else ("/executive/sales-history" if role == "executive" else "/admin/sales-history"),
        default_start=datetime.utcnow().replace(day=1).strftime("%Y-%m-%d"),
        default_end=datetime.utcnow().strftime("%Y-%m-%d"),
    )


@manager_sales_history_bp.route("/manager/sales-history/data", methods=["GET"])
@manager_sales_history_bp.route("/executive/sales-history/data", methods=["GET"])
@manager_sales_history_bp.route("/admin/sales-history/data", methods=["GET"])
def manager_sales_history_data():
    auth = _auth_context()
    if not auth:
        return jsonify(ok=False, message="Unauthorized"), 403

    role = auth["role"]
    manager_id = auth["user_id"] if role == "manager" else None

    cache_key = (
        "data",
        role,
        str(manager_id or "__all__"),
        (request.args.get("range") or "").strip().lower(),
        (request.args.get("start") or "").strip(),
        (request.args.get("end") or "").strip(),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    if role == "manager":
        manager = _manager_doc(manager_id)
        if not manager:
            return jsonify(ok=False, message="Manager not found"), 404
        agents = _manager_agents(manager_id)
    else:
        agents = _all_agents()

    agent_map = {a["id"]: a for a in agents}
    agent_ids = list(agent_map.keys())

    r = _resolve_range(request.args)
    match = _payments_match(manager_id, agent_ids, r["start"], r["end"])

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
                "active_agents_count": [
                    {"$match": {"agent_id": {"$ne": ""}}},
                    {"$group": {"_id": "$agent_id"}},
                    {"$count": "count"},
                ],
                "daily_sales": [
                    {
                        "$group": {
                            "_id": "$date",
                            "sales": {"$sum": "$amount_num"},
                            "payments_count": {"$sum": 1},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "daily_customers": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {"$group": {"_id": {"date": "$date", "customer_key": "$customer_key"}}},
                    {"$group": {"_id": "$_id.date", "customers_paid": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "agent_sales": [
                    {
                        "$group": {
                            "_id": {"agent_id": "$agent_id", "date": "$date"},
                            "sales": {"$sum": "$amount_num"},
                            "payments_count": {"$sum": 1},
                        }
                    },
                    {
                        "$group": {
                            "_id": "$_id.agent_id",
                            "total_sales": {"$sum": "$sales"},
                            "payments_count": {"$sum": "$payments_count"},
                            "work_days": {
                                "$sum": {
                                    "$cond": [{"$gt": ["$payments_count", 10]}, 1, 0]
                                }
                            },
                        }
                    },
                ],
                "agent_customers": [
                    {"$match": {"customer_key": {"$ne": ""}}},
                    {"$group": {"_id": {"agent_id": "$agent_id", "customer_key": "$customer_key"}}},
                    {"$group": {"_id": "$_id.agent_id", "customers_paid": {"$sum": 1}}},
                ],
                "methods": [
                    {
                        "$group": {
                            "_id": "$method_norm",
                            "count": {"$sum": 1},
                            "amount": {"$sum": "$amount_num"},
                        }
                    },
                    {"$sort": {"amount": -1}},
                ],
            }
        },
    ]

    agg = list(payments_col.aggregate(pipeline, allowDiskUse=True))
    payload = agg[0] if agg else {
        "summary": [],
        "customers_count": [],
        "active_agents_count": [],
        "daily_sales": [],
        "daily_customers": [],
        "agent_sales": [],
        "agent_customers": [],
        "methods": [],
    }

    summary_doc = (payload.get("summary") or [{}])[0]
    total_sales = float(summary_doc.get("total_sales", 0) or 0)
    total_payments = int(summary_doc.get("payments_count", 0) or 0)
    customers_paid_count = int(((payload.get("customers_count") or [{}])[0]).get("count", 0) or 0)
    active_agents_count = int(((payload.get("active_agents_count") or [{}])[0]).get("count", 0) or 0)
    total_agents_count = len(agent_ids)
    inactive_agents_count = max(total_agents_count - active_agents_count, 0)
    daily_avg_sales = total_sales / r["days"] if r["days"] else 0.0

    daily_customers_map = {
        str(d.get("_id")): int(d.get("customers_paid", 0) or 0)
        for d in (payload.get("daily_customers") or [])
    }
    daily_trend = []
    for d in payload.get("daily_sales") or []:
        day_key = str(d.get("_id") or "")
        daily_trend.append(
            {
                "date": d.get("_id"),
                "sales": float(d.get("sales", 0) or 0),
                "payments_count": int(d.get("payments_count", 0) or 0),
                "customers_paid": daily_customers_map.get(day_key, 0),
            }
        )

    agent_customers_map = {
        str(row.get("_id") or ""): int(row.get("customers_paid", 0) or 0)
        for row in (payload.get("agent_customers") or [])
    }
    agent_rows = []
    for row in payload.get("agent_sales") or []:
        aid = str(row.get("_id") or "")
        if not aid:
            continue
        meta = agent_map.get(aid, {"name": "Unknown", "branch": ""})
        customer_count = agent_customers_map.get(aid, 0)
        total = float(row.get("total_sales", 0) or 0)
        payments_count = int(row.get("payments_count", 0) or 0)
        work_days = int(row.get("work_days", 0) or 0)
        avg_per_day = total / r["days"] if r["days"] else 0.0
        agent_rows.append(
            {
                "agent_id": aid,
                "agent_name": meta.get("name", "Unknown"),
                "branch": meta.get("branch", ""),
                "total_sales": round(total, 2),
                "payments_count": payments_count,
                "customers_paid": customer_count,
                "work_days": work_days,
                "avg_per_day": round(avg_per_day, 2),
                "performance_pct": 0.0,
            }
        )

    agent_rows.sort(key=lambda x: x["total_sales"], reverse=True)
    best_agent = agent_rows[0] if agent_rows else None
    best_sales = float(best_agent["total_sales"]) if best_agent else 0.0
    for row in agent_rows:
        if best_sales > 0:
            row["performance_pct"] = round((row["total_sales"] / best_sales) * 100.0, 1)
        else:
            row["performance_pct"] = 0.0

    total_work_days = int(sum(rw["work_days"] for rw in agent_rows))

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
            "total_active_agents": active_agents_count,
            "daily_average_sales": round(daily_avg_sales, 2),
            "best_performing_agent": best_agent["agent_name"] if best_agent else "-",
            "best_performing_agent_sales": round(best_sales, 2),
            "total_attendance": customers_paid_count,
            "total_work_days": total_work_days,
            "total_agents": total_agents_count,
            "inactive_agents": inactive_agents_count,
        },
        table=agent_rows,
        charts={
            "daily_sales_trend": daily_trend,
            "sales_per_agent": [
                {"agent_name": r["agent_name"], "sales": r["total_sales"]} for r in agent_rows
            ],
            "active_vs_inactive_agents": {
                "active": active_agents_count,
                "inactive": inactive_agents_count,
            },
        },
        payment_methods=methods,
        payment_methods_cash_vs_others={
            "cash": round(cash_amount, 2),
            "others": round(other_amount, 2),
        },
    )
    _cache_set(cache_key, result)
    return jsonify(result)


@manager_sales_history_bp.route("/manager/sales-history/agent/<agent_id>/details", methods=["GET"])
@manager_sales_history_bp.route("/executive/sales-history/agent/<agent_id>/details", methods=["GET"])
@manager_sales_history_bp.route("/admin/sales-history/agent/<agent_id>/details", methods=["GET"])
def manager_sales_history_agent_details(agent_id):
    auth = _auth_context()
    if not auth:
        return jsonify(ok=False, message="Unauthorized"), 403

    role = auth["role"]
    manager_id = auth["user_id"] if role == "manager" else None

    if role == "manager":
        manager = _manager_doc(manager_id)
        if not manager:
            return jsonify(ok=False, message="Manager not found"), 404

    cache_key = (
        "detail",
        role,
        str(manager_id or "__all__"),
        str(agent_id),
        (request.args.get("range") or "").strip().lower(),
        (request.args.get("start") or "").strip(),
        (request.args.get("end") or "").strip(),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    agents = _manager_agents(manager_id) if role == "manager" else _all_agents()
    agent_map = {a["id"]: a for a in agents}
    if str(agent_id) not in agent_map:
        msg = "Forbidden: agent not under this manager" if role == "manager" else "Agent not found"
        return jsonify(ok=False, message=msg), 403

    r = _resolve_range(request.args)
    match = _payments_match(
        manager_id,
        agent_map.keys(),
        r["start"],
        r["end"],
        single_agent_id=agent_id,
    )

    pipeline = [
        {"$match": match},
        _to_amount_and_core_fields_stage(),
        {
            "$facet": {
                "daily_sales": [
                    {
                        "$group": {
                            "_id": "$date",
                            "sales": {"$sum": "$amount_num"},
                            "payments_count": {"$sum": 1},
                        }
                    },
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
                    {
                        "$group": {
                            "_id": "$method_norm",
                            "amount": {"$sum": "$amount_num"},
                            "count": {"$sum": 1},
                        }
                    },
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
        if not key:
            continue
        oid = _safe_oid(key)
        if oid:
            customer_oid_map[key] = oid

    customer_docs = {}
    if customer_oid_map:
        for c in customers_col.find(
            {"_id": {"$in": list(customer_oid_map.values())}},
            {"name": 1, "phone_number": 1},
        ):
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

    daily_customers_map = {
        str(d.get("_id")): int(d.get("customers_paid", 0) or 0)
        for d in (payload.get("daily_customers") or [])
    }
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
        agent=agent_map[str(agent_id)],
        range={"mode": r["mode"], "start": r["start"], "end": r["end"], "days": r["days"]},
        daily_breakdown=daily_breakdown,
        customers_paid=customers_paid,
        customers_paid_total_count=total_customers_paid,
        customers_paid_truncated=total_customers_paid > _DETAIL_ROWS_LIMIT,
        top_customers=customers_paid[:5],
        payment_methods=methods,
        payment_methods_cash_vs_others={
            "cash": round(cash_amount, 2),
            "others": round(other_amount, 2),
        },
    )
    _cache_set(cache_key, result)
    return jsonify(result)
