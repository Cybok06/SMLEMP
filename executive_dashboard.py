from flask import Blueprint, render_template, jsonify, request
from bson import ObjectId
from db import db
from datetime import datetime, timedelta, timezone
from login import role_required
from services.target_cash import CHANNELS, extend_daily_progress, extend_daily_totals
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback for older envs
    ZoneInfo = None

executive_bp = Blueprint('executive_dashboard', __name__)

# Collections
customers_col = db["customers"]
payments_col = db["payments"]
users_col = db["users"]
manager_expenses_col = db["manager_expenses"]
executive_targets_col = db["executive_targets"]


def _safe_amount(val):
    try:
        return float(val)
    except Exception:
        return 0.0


def _id_variants(val):
    if val is None:
        return []
    if isinstance(val, ObjectId):
        return [val, str(val)]
    sval = str(val).strip()
    if ObjectId.is_valid(sval):
        return [ObjectId(sval), sval]
    return [sval]


def _ensure_exec_indexes():
    try:
        payments_col.create_index([("date", 1), ("payment_type", 1)])
        payments_col.create_index([("date", 1), ("payment_type", 1), ("created_at", 1), ("agent_id", 1)])
        payments_col.create_index([("created_at", 1)])
        payments_col.create_index([("manager_id", 1), ("date", 1)])
        payments_col.create_index([("agent_id", 1), ("date", 1)])
        customers_col.create_index([("date_registered", 1), ("agent_id", 1)])
        customers_col.create_index([("lead_stage", 1), ("date_registered", 1)])
        customers_col.create_index([("lead_stage", 1), ("lead_converted_at", 1)])
        customers_col.create_index([("lead_registered_at", 1)])
        customers_col.create_index([("lead_converted_at", 1)])
        customers_col.create_index([("lead_registered_at", 1), ("lead_converted_at", 1)])
        manager_expenses_col.create_index([("created_at", 1)])
        manager_expenses_col.create_index([("status", 1), ("created_at", 1)])
        manager_expenses_col.create_index([("category", 1), ("created_at", 1)])
        executive_targets_col.create_index([("manager_id", 1), ("period", 1), ("is_active", 1)])
        executive_targets_col.create_index([("created_at", -1)])
    except Exception:
        pass


_ensure_exec_indexes()


def _today_range_utc(base_date):
    start = datetime(base_date.year, base_date.month, base_date.day)
    end = start + timedelta(days=1)
    return start, end


def _yesterday_range_utc(base_date):
    y = base_date - timedelta(days=1)
    return _today_range_utc(y)


def _week_range_utc(base_date):
    start = datetime(base_date.year, base_date.month, base_date.day) - timedelta(days=base_date.weekday())
    end = start + timedelta(days=7)
    return start, end


def _month_range_utc(base_date):
    start = datetime(base_date.year, base_date.month, 1)
    if base_date.month == 12:
        end = datetime(base_date.year + 1, 1, 1)
    else:
        end = datetime(base_date.year, base_date.month + 1, 1)
    return start, end


def _lookup_name_map(role, ids):
    variants = []
    for val in ids:
        variants.extend(_id_variants(val))
    if not variants:
        return {}
    query = {"_id": {"$in": variants}}
    if isinstance(role, (list, tuple, set)):
        query["role"] = {"$in": [str(item) for item in role if item]}
    elif role:
        query["role"] = role
    docs = list(users_col.find(query, {"name": 1, "username": 1}))
    name_map = {}
    for doc in docs:
        name = (doc.get("name") or doc.get("username") or "").strip()
        if not name:
            continue
        key = str(doc.get("_id"))
        name_map[key] = name
    return name_map


def _lookup_manager_name(manager_id):
    for variant in _id_variants(manager_id):
        doc = users_col.find_one({"_id": variant}, {"name": 1})
        if doc and doc.get("name"):
            return doc.get("name")
    return ""


def _get_accra_tz():
    if ZoneInfo:
        try:
            return ZoneInfo("Africa/Accra")
        except Exception:
            pass
    try:
        import pytz
        return pytz.timezone("Africa/Accra")
    except Exception:
        return timezone.utc


def _accra_day_bounds():
    tz = _get_accra_tz()
    now = datetime.now(tz)
    if hasattr(tz, "localize"):
        start = tz.localize(datetime(now.year, now.month, now.day))
    else:
        start = datetime(now.year, now.month, now.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return now, start, end


def _accra_week_bounds():
    tz = _get_accra_tz()
    now = datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    if hasattr(tz, "localize"):
        start = tz.localize(datetime(monday.year, monday.month, monday.day))
    else:
        start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    end = start + timedelta(days=7)
    return now, start, end


def _accra_month_bounds():
    tz = _get_accra_tz()
    now = datetime.now(tz)
    if hasattr(tz, "localize"):
        start = tz.localize(datetime(now.year, now.month, 1))
    else:
        start = datetime(now.year, now.month, 1, tzinfo=tz)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    if hasattr(tz, "localize"):
        end = tz.localize(next_month)
    else:
        end = next_month.replace(tzinfo=tz)
    return now, start, end


def _utc_naive(dt):
    if not dt:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _dt_iso(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dt_time_local(dt):
    if not dt:
        return ""
    tz = _get_accra_tz()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%H:%M")


def _parse_cursor(raw):
    if not raw:
        return None, None
    raw = str(raw).strip()
    if ObjectId.is_valid(raw):
        try:
            return None, ObjectId(raw)
        except Exception:
            return None, None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _utc_naive(dt), None


def _list_managers():
    rows = list(users_col.find({"role": "manager"}, {"name": 1, "branch": 1}))
    return [
        {
            "id": str(r.get("_id")),
            "name": r.get("name") or "Manager",
            "branch": r.get("branch") or "-",
        }
        for r in rows
    ]


def _list_branches():
    branches = users_col.distinct("branch", {"role": {"$in": ["agent", "manager"]}})
    return sorted([b for b in branches if b])


def _agent_ids_for_filters(manager_id, branch):
    manager_variants = _id_variants(manager_id) if manager_id else []
    if not manager_variants and not branch:
        return [], {}, manager_variants

    query = {"role": "agent"}
    if manager_variants:
        query["manager_id"] = {"$in": manager_variants}
    if branch:
        query["branch"] = branch
    agents = list(users_col.find(query, {"_id": 1, "name": 1, "branch": 1}))
    agent_ids = [str(a.get("_id")) for a in agents if a.get("_id") is not None]
    agent_map = {str(a.get("_id")): a for a in agents if a.get("_id") is not None}
    return agent_ids, agent_map, manager_variants


def _user_map_by_ids(ids):
    variants = []
    for val in ids:
        variants.extend(_id_variants(val))
    if not variants:
        return {}
    docs = list(users_col.find({"_id": {"$in": variants}}, {"name": 1, "branch": 1}))
    return {str(d.get("_id")): d for d in docs if d.get("_id") is not None}


def _manager_map_by_ids(ids):
    variants = []
    for val in ids:
        variants.extend(_id_variants(val))
    if not variants:
        return {}
    docs = list(users_col.find({"role": "manager", "_id": {"$in": variants}}, {"name": 1, "branch": 1}))
    return {str(d.get("_id")): d for d in docs if d.get("_id") is not None}


def _customer_name_map(ids):
    variants = []
    for val in ids:
        variants.extend(_id_variants(val))
    if not variants:
        return {}
    docs = list(customers_col.find({"_id": {"$in": variants}}, {"name": 1}))
    return {str(d.get("_id")): d.get("name") for d in docs if d.get("_id") is not None}


def _payment_dt_expr():
    return {
        "$ifNull": [
            "$created_at",
            {
                "$dateFromString": {
                    "dateString": {
                        "$concat": [
                            {"$ifNull": ["$date", ""]},
                            " ",
                            {"$ifNull": ["$time", "00:00:00"]},
                        ]
                    },
                    "format": "%Y-%m-%d %H:%M:%S",
                    "onError": None,
                    "onNull": None,
                }
            },
        ]
    }


def _daily_targets_for_agents(manager_variants, branch, today_str, start_utc, end_utc):
    targets = list(executive_targets_col.find({"period": "daily", "is_active": True}))
    if manager_variants:
        manager_keys = {str(m) for m in manager_variants}
        targets = [t for t in targets if str(t.get("manager_id")) in manager_keys]

    manager_ids = [t.get("manager_id") for t in targets if t.get("manager_id") is not None]
    manager_id_variants = []
    for mid in manager_ids:
        manager_id_variants.extend(_id_variants(mid))
    manager_id_variants = list({m for m in manager_id_variants})

    agent_query = {"role": "agent"}
    if manager_id_variants:
        agent_query["manager_id"] = {"$in": manager_id_variants}
    if branch:
        agent_query["branch"] = branch

    agents = list(users_col.find(agent_query, {"_id": 1, "name": 1, "branch": 1, "manager_id": 1}))
    agent_map = {str(a.get("_id")): a for a in agents if a.get("_id") is not None}

    agent_targets = {}
    for t in targets:
        manager_id = t.get("manager_id")
        allocs = t.get("agent_allocations") or []
        if allocs:
            for a in allocs:
                aid = str(a.get("agent_id") or "")
                if not aid or aid not in agent_map:
                    continue
                entry = agent_targets.setdefault(aid, {
                    "cash_target_daily": 0.0,
                    "product_target_daily": 0,
                    "customer_target_daily": 0,
                })
                entry["cash_target_daily"] += float(a.get("cash_quota") or 0.0)
                entry["product_target_daily"] += int(a.get("product_quota") or 0)
                entry["customer_target_daily"] += int(a.get("customer_quota") or 0)
                for c in CHANNELS:
                    entry[c + '_target_daily'] = entry.get(c + '_target_daily', 0) + float(a.get(c + '_quota') or 0)
    agent_ids = list(agent_targets.keys())
    agent_ids_obj = [ObjectId(aid) for aid in agent_ids if ObjectId.is_valid(str(aid))]
    agent_id_match = []
    if agent_ids:
        agent_id_match.append({"agent_id": {"$in": agent_ids}})
    if agent_ids_obj:
        agent_id_match.append({"agent_id": {"$in": agent_ids_obj}})
    id_filter = {"$or": agent_id_match} if agent_id_match else {"agent_id": {"$in": []}}

    payments_match = {
        **id_filter,
        "date": today_str,
        "payment_type": {"$ne": "WITHDRAWAL"},
    }
    pay_rows = list(
        payments_col.aggregate([
            {"$match": payments_match},
            {"$group": {"_id": {"$toString": "$agent_id"}, "total": {"$sum": "$amount"}}},
        ])
    )
    pay_map = {str(r.get("_id")): round(_safe_amount(r.get("total")), 2) for r in pay_rows}

    prod_rows = list(
        customers_col.aggregate([
            {"$match": {**id_filter, "purchases.purchase_date": today_str}},
            {"$unwind": "$purchases"},
            {"$match": {"purchases.purchase_date": today_str}},
            {"$group": {"_id": {"$toString": "$agent_id"}, "units": {"$sum": {"$ifNull": ["$purchases.product.quantity", 1]}}}},
        ])
    )
    prod_map = {str(r.get("_id")): int(r.get("units") or 0) for r in prod_rows}

    cust_rows = list(
        customers_col.aggregate([
            {"$match": {**id_filter, "date_registered": {"$gte": start_utc, "$lt": end_utc}}},
            {"$group": {"_id": {"$toString": "$agent_id"}, "count": {"$sum": 1}}},
        ])
    )
    cust_map = {str(r.get("_id")): int(r.get("count") or 0) for r in cust_rows}

    def _clamp_pct(val):
        try:
            val = float(val)
        except Exception:
            val = 0.0
        return max(0.0, min(200.0, round(val, 2)))

    agent_progress = {}
    for aid, tgt in agent_targets.items():
        cash_target = float(tgt.get("cash_target_daily") or 0.0)
        product_target = int(tgt.get("product_target_daily") or 0)
        customer_target = int(tgt.get("customer_target_daily") or 0)
        cash_ach = round(pay_map.get(aid, 0.0), 2)
        prod_ach = int(prod_map.get(aid, 0))
        cust_ach = int(cust_map.get(aid, 0))

        cash_pct = _clamp_pct((cash_ach / cash_target * 100) if cash_target else 0.0)
        prod_pct = _clamp_pct((prod_ach / product_target * 100) if product_target else 0.0)
        cust_pct = _clamp_pct((cust_ach / customer_target * 100) if customer_target else 0.0)

        parts = []
        if cash_target:
            parts.append(cash_pct)
        if product_target:
            parts.append(prod_pct)
        if customer_target:
            parts.append(cust_pct)
        overall = round(sum(parts) / len(parts), 2) if parts else 0.0

        hit_metrics = sum(1 for v in (cash_pct, prod_pct, cust_pct) if v >= 100)
        target_hit = overall >= 100 or hit_metrics >= 2

        agent_progress[aid] = {
            "cash_target_daily": round(cash_target, 2),
            "product_target_daily": product_target,
            "customer_target_daily": customer_target,
            "cash_achieved_today": cash_ach,
            "products_achieved_today": prod_ach,
            "customers_gained_today": cust_ach,
            "cash_progress_pct": cash_pct,
            "product_progress_pct": prod_pct,
            "customer_progress_pct": cust_pct,
            "overall_progress_pct": overall,
            "target_hit": target_hit,
        }

    extend_daily_progress(agent_progress, agent_targets, payments_col, today_str, today_str)
    totals = {
        "total_cash_target_today": round(sum(v.get("cash_target_daily", 0.0) for v in agent_targets.values()), 2),
        "total_product_target_today": int(sum(v.get("product_target_daily", 0) for v in agent_targets.values())),
        "total_customer_target_today": int(sum(v.get("customer_target_daily", 0) for v in agent_targets.values())),
        "total_cash_achieved_today": round(sum(pay_map.values()), 2),
        "total_products_achieved_today": int(sum(prod_map.values())),
        "total_customers_gained_today": int(sum(cust_map.values())),
    }
    overall_coverage = 0.0
    extend_daily_totals(totals, agent_progress)
    cov_parts = []
    for c in CHANNELS:
        if totals[f'total_{c}_target_today']:
            cov_parts.append(totals[f'total_{c}_achieved_today'] / totals[f'total_{c}_target_today'] * 100)
    if totals["total_cash_target_today"]:
        cov_parts.append(totals["total_cash_achieved_today"] / totals["total_cash_target_today"] * 100)
    if totals["total_product_target_today"]:
        cov_parts.append(totals["total_products_achieved_today"] / totals["total_product_target_today"] * 100)
    if totals["total_customer_target_today"]:
        cov_parts.append(totals["total_customers_gained_today"] / totals["total_customer_target_today"] * 100)
    if cov_parts:
        overall_coverage = round(sum(cov_parts) / len(cov_parts), 2)

    target_hit_agents = [
        {"agent_id": aid, **agent_progress.get(aid, {})}
        for aid in agent_progress.keys()
        if agent_progress.get(aid, {}).get("target_hit")
    ]

    return {
        "agent_targets": agent_targets,
        "agent_progress": agent_progress,
        "agent_map": agent_map,
        "agents_with_targets": len(agent_targets),
        "agents_hit_target_count": len(target_hit_agents),
        "overall_coverage_pct_today": overall_coverage,
        "totals": totals,
        "target_hit_agents": target_hit_agents,
    }


@executive_bp.route("/executive/dashboard")
def executive_dashboard():
    """
    Lightweight initial render:
    - Summary metrics only
    - Heavy chart data is loaded via AJAX
    """
    # Total customers (fast, approximate but OK for dashboard)
    total_customers = customers_col.estimated_document_count()

    # Total products sold
    sold_result = customers_col.aggregate([
        {"$match": {"purchases": {"$exists": True, "$ne": []}}},
        {"$group": {"_id": None, "total": {"$sum": {"$size": "$purchases"}}}}
    ])
    total_products_sold = next(sold_result, {}).get("total", 0)

    return render_template("executive_dashboard.html", data={
        "total_customers": total_customers,
        "total_products_sold": total_products_sold
    })


@executive_bp.route("/executive/today-live")
@role_required("executive", "admin")
def executive_today_live():
    context = {
        "managers": _list_managers(),
        "branches": _list_branches(),
    }
    return render_template("executive_today_live.html", **context)


@executive_bp.route("/executive/api/today-live")
@role_required("executive", "admin")
def executive_today_live_data():
    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()

    now_accra, start_accra, end_accra = _accra_day_bounds()
    start_utc = _utc_naive(start_accra.astimezone(timezone.utc))
    end_utc = _utc_naive(end_accra.astimezone(timezone.utc))
    today_str = start_accra.strftime("%Y-%m-%d")

    agent_ids, agent_map, manager_variants = _agent_ids_for_filters(manager_id, branch)

    targets_context = _daily_targets_for_agents(manager_variants, branch, today_str, start_utc, end_utc)
    yesterday_str = (start_accra - timedelta(days=1)).strftime("%Y-%m-%d")
    y_start_utc = _utc_naive((start_accra - timedelta(days=1)).astimezone(timezone.utc))
    y_end_utc = _utc_naive((end_accra - timedelta(days=1)).astimezone(timezone.utc))
    targets_context_yday = _daily_targets_for_agents(manager_variants, branch, yesterday_str, y_start_utc, y_end_utc)
    agent_progress_map = targets_context.get("agent_progress") or {}
    target_hit_agents = targets_context.get("target_hit_agents") or []

    payment_match = {"date": today_str, "payment_type": {"$ne": "WITHDRAWAL"}}
    if manager_variants:
        payment_match["manager_id"] = {"$in": manager_variants}
    if branch:
        if agent_ids:
            payment_match["agent_id"] = {"$in": agent_ids}
        else:
            payment_match["agent_id"] = {"$in": []}

    customer_match = {"date_registered": {"$gte": start_utc, "$lt": end_utc}}
    if manager_variants:
        customer_match["manager_id"] = {"$in": manager_variants}
    if branch:
        if agent_ids:
            customer_match["agent_id"] = {"$in": agent_ids}
        else:
            customer_match["agent_id"] = {"$in": []}

    totals_rows = list(
        payments_col.aggregate(
            [
                {"$match": payment_match},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
            ]
        )
    )
    total_sales_today = round(_safe_amount(totals_rows[0].get("total")) if totals_rows else 0.0, 2)
    payment_count_today = int(totals_rows[0].get("count", 0) if totals_rows else 0)

    customers_registered_today = customers_col.count_documents(customer_match)

    def _lead_match_base():
        match = {}
        if manager_variants:
            match["manager_id"] = {"$in": manager_variants}
        if branch:
            if agent_ids:
                match["agent_id"] = {"$in": agent_ids}
            else:
                match["agent_id"] = {"$in": []}
        return match

    def _count_leads_in_range(start_dt, end_dt, extra_match=None):
        match = extra_match or {}
        pipeline = [
            {"$addFields": {
                "lead_reg_dt": {
                    "$convert": {
                        "input": {"$ifNull": ["$lead_registered_at", "$date_registered"]},
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }},
            {"$match": {**match, "lead_reg_dt": {"$gte": start_dt, "$lt": end_dt}}},
            {"$count": "count"},
        ]
        rows = list(customers_col.aggregate(pipeline))
        return int(rows[0].get("count", 0) if rows else 0)

    def _count_conversions_in_range(start_dt, end_dt, extra_match=None):
        match = extra_match or {}
        pipeline = [
            {"$addFields": {
                "lead_conv_dt": {
                    "$convert": {
                        "input": "$lead_converted_at",
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }},
            {"$match": {**match, "lead_conv_dt": {"$gte": start_dt, "$lt": end_dt}}},
            {"$count": "count"},
        ]
        rows = list(customers_col.aggregate(pipeline))
        return int(rows[0].get("count", 0) if rows else 0)

    lead_scope_match = _lead_match_base()
    leads_today = _count_leads_in_range(start_utc, end_utc, lead_scope_match)
    conversions_today = _count_conversions_in_range(start_utc, end_utc, lead_scope_match)
    conversion_ratio_today = round((conversions_today / leads_today) * 100, 1) if leads_today else 0.0
    active_agents_today = len(payments_col.distinct("agent_id", payment_match)) if payment_count_today else 0
    avg_payment_today = round((total_sales_today / payment_count_today), 2) if payment_count_today else 0.0

    leaderboard_rows = list(
        payments_col.aggregate(
            [
                {"$match": payment_match},
                {"$group": {"_id": "$agent_id", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
                {"$sort": {"total": -1}},
                {"$limit": 10},
            ]
        )
    )
    leaderboard_agent_ids = [str(r.get("_id")) for r in leaderboard_rows if r.get("_id") is not None]
    agent_map.update(_user_map_by_ids(leaderboard_agent_ids))

    lead_agent_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_reg_dt": {
                        "$convert": {
                            "input": {"$ifNull": ["$lead_registered_at", "$date_registered"]},
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_reg_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$agent_id", "leads_registered": {"$sum": 1}}},
            ]
        )
    )
    lead_agent_map = {str(r.get("_id")): int(r.get("leads_registered", 0) or 0) for r in lead_agent_rows if r.get("_id") is not None}

    conv_agent_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_conv_dt": {
                        "$convert": {
                            "input": "$lead_converted_at",
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_conv_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$agent_id", "conversions": {"$sum": 1}}},
            ]
        )
    )
    conv_agent_map = {str(r.get("_id")): int(r.get("conversions", 0) or 0) for r in conv_agent_rows if r.get("_id") is not None}

    leaderboard_agents = []
    for row in leaderboard_rows:
        aid = row.get("_id")
        if aid is None:
            continue
        target_info = agent_progress_map.get(str(aid), {})
        agent_doc = agent_map.get(str(aid)) or {}
        total_amount = round(_safe_amount(row.get("total")), 2)
        count = int(row.get("count", 0) or 0)
        leads_registered = int(lead_agent_map.get(str(aid), 0))
        conversions = int(conv_agent_map.get(str(aid), 0))
        conv_ratio = round((conversions / leads_registered) * 100, 1) if leads_registered else 0.0
        leaderboard_agents.append({
            "agent_id": str(aid),
            "agent_name": agent_doc.get("name") or "Agent",
            "branch": agent_doc.get("branch") or "-",
            "payments_count": count,
            "total_amount": total_amount,
            "avg_payment": round((total_amount / count), 2) if count else 0.0,
            "leads_registered": leads_registered,
            "conversion_ratio": conv_ratio,
            "cash_target_daily": target_info.get("cash_target_daily"),
            **{c + '_' + suffix: target_info.get(c + '_' + suffix, 0) for c in CHANNELS for suffix in ('target_daily', 'achieved_today', 'progress_pct')},
            "product_target_daily": target_info.get("product_target_daily"),
            "customer_target_daily": target_info.get("customer_target_daily"),
            "cash_achieved_today": target_info.get("cash_achieved_today"),
            "products_achieved_today": target_info.get("products_achieved_today"),
            "customers_gained_today": target_info.get("customers_gained_today"),
            "cash_progress_pct": target_info.get("cash_progress_pct"),
            "product_progress_pct": target_info.get("product_progress_pct"),
            "customer_progress_pct": target_info.get("customer_progress_pct"),
            "overall_progress_pct": target_info.get("overall_progress_pct"),
            "target_hit": bool(target_info.get("target_hit")),
        })

    manager_rows = list(
        payments_col.aggregate(
            [
                {"$match": payment_match},
                {"$group": {"_id": "$manager_id", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
                {"$sort": {"total": -1}},
                {"$limit": 10},
            ]
        )
    )
    manager_ids = [str(r.get("_id")) for r in manager_rows if r.get("_id") is not None]
    manager_map = _manager_map_by_ids(manager_ids)
    lead_manager_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_reg_dt": {
                        "$convert": {
                            "input": {"$ifNull": ["$lead_registered_at", "$date_registered"]},
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_reg_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$manager_id", "leads_registered": {"$sum": 1}}},
            ]
        )
    )
    lead_manager_map = {str(r.get("_id")): int(r.get("leads_registered", 0) or 0) for r in lead_manager_rows if r.get("_id") is not None}

    conv_manager_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_conv_dt": {
                        "$convert": {
                            "input": "$lead_converted_at",
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_conv_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$manager_id", "conversions": {"$sum": 1}}},
            ]
        )
    )
    conv_manager_map = {str(r.get("_id")): int(r.get("conversions", 0) or 0) for r in conv_manager_rows if r.get("_id") is not None}

    leaderboard_managers_top10 = []
    for row in manager_rows:
        mid = row.get("_id")
        if mid is None:
            continue
        doc = manager_map.get(str(mid)) or {}
        total_amount = round(_safe_amount(row.get("total")), 2)
        count = int(row.get("count", 0) or 0)
        leads_registered = int(lead_manager_map.get(str(mid), 0))
        conversions = int(conv_manager_map.get(str(mid), 0))
        conv_ratio = round((conversions / leads_registered) * 100, 1) if leads_registered else 0.0
        manager_target_total = {
            **{c + '_' + suffix: 0.0 for c in CHANNELS for suffix in ('target_daily', 'achieved_today')},
            "cash_target_daily": 0.0,
            "product_target_daily": 0,
            "customer_target_daily": 0,
            "cash_achieved_today": 0.0,
            "products_achieved_today": 0,
            "customers_gained_today": 0,
        }
        for aid, agent_doc in (targets_context.get("agent_map") or {}).items():
            if str(agent_doc.get("manager_id")) != str(mid):
                continue
            tinfo = agent_progress_map.get(str(aid), {})
            manager_target_total["cash_target_daily"] += float(tinfo.get("cash_target_daily") or 0.0)
            manager_target_total["product_target_daily"] += int(tinfo.get("product_target_daily") or 0)
            manager_target_total["customer_target_daily"] += int(tinfo.get("customer_target_daily") or 0)
            manager_target_total["cash_achieved_today"] += float(tinfo.get("cash_achieved_today") or 0.0)
            manager_target_total["products_achieved_today"] += int(tinfo.get("products_achieved_today") or 0)
            manager_target_total["customers_gained_today"] += int(tinfo.get("customers_gained_today") or 0)
            for c in CHANNELS:
                for suffix in ('target_daily', 'achieved_today'):
                    key = c + '_' + suffix
                    manager_target_total[key] += float(tinfo.get(key) or 0)
        coverage_parts = []
        for c in CHANNELS:
            if manager_target_total[c + '_target_daily']:
                coverage_parts.append(manager_target_total[c + '_achieved_today'] / manager_target_total[c + '_target_daily'] * 100)
        if manager_target_total["cash_target_daily"]:
            coverage_parts.append(manager_target_total["cash_achieved_today"] / manager_target_total["cash_target_daily"] * 100)
        if manager_target_total["product_target_daily"]:
            coverage_parts.append(manager_target_total["products_achieved_today"] / manager_target_total["product_target_daily"] * 100)
        if manager_target_total["customer_target_daily"]:
            coverage_parts.append(manager_target_total["customers_gained_today"] / manager_target_total["customer_target_daily"] * 100)
        overall_progress_pct = round(sum(coverage_parts) / len(coverage_parts), 2) if coverage_parts else 0.0
        target_hit = overall_progress_pct >= 100 or sum(1 for v in coverage_parts if v >= 100) >= 2
        if any(manager_target_total[c + '_target_daily'] for c in CHANNELS):
            target_hit = bool(coverage_parts) and all(value >= 100 for value in coverage_parts)
        leaderboard_managers_top10.append({
            "manager_id": str(mid),
            "manager_name": doc.get("name") or "Manager",
            "branch": doc.get("branch") or "-",
            "payments_count": count,
            "total_amount": total_amount,
            "avg_payment": round((total_amount / count), 2) if count else 0.0,
            "leads_registered": leads_registered,
            "conversion_ratio": conv_ratio,
            **{c + '_' + suffix: round(manager_target_total[c + '_' + suffix], 2) for c in CHANNELS for suffix in ('target_daily', 'achieved_today')},
            "cash_target_daily": round(manager_target_total["cash_target_daily"], 2),
            "product_target_daily": manager_target_total["product_target_daily"],
            "customer_target_daily": manager_target_total["customer_target_daily"],
            "cash_achieved_today": round(manager_target_total["cash_achieved_today"], 2),
            "products_achieved_today": manager_target_total["products_achieved_today"],
            "customers_gained_today": manager_target_total["customers_gained_today"],
            "overall_progress_pct": overall_progress_pct,
            "target_hit": target_hit,
            "cash_target_today": round(manager_target_total["cash_target_daily"], 2),
            "product_target_today": manager_target_total["product_target_daily"],
            "leads_target_today": manager_target_total["customer_target_daily"],
            "cash_achieved_today": round(manager_target_total["cash_achieved_today"], 2),
            "product_achieved_today": manager_target_total["products_achieved_today"],
            "leads_achieved_today": manager_target_total["customers_gained_today"],
            "overall_target_pct": overall_progress_pct,
        })

    branch_rows = list(
        payments_col.find(
            payment_match,
            {"amount": 1, "agent_id": 1}
        )
    )
    branch_agent_ids = [str(p.get("agent_id")) for p in branch_rows if p.get("agent_id") is not None]
    branch_agent_map = _user_map_by_ids(branch_agent_ids)
    branch_totals = {}
    for p in branch_rows:
        agent_id = str(p.get("agent_id") or "")
        agent_doc = branch_agent_map.get(agent_id) or {}
        branch_name = agent_doc.get("branch") or "-"
        entry = branch_totals.setdefault(branch_name, {"total": 0.0, "count": 0})
        entry["total"] += _safe_amount(p.get("amount"))
        entry["count"] += 1
    lead_branch_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_reg_dt": {
                        "$convert": {
                            "input": {"$ifNull": ["$lead_registered_at", "$date_registered"]},
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_reg_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$agent_id", "leads_registered": {"$sum": 1}}},
            ]
        )
    )
    conv_branch_rows = list(
        customers_col.aggregate(
            [
                {"$addFields": {
                    "lead_conv_dt": {
                        "$convert": {
                            "input": "$lead_converted_at",
                            "to": "date",
                            "onError": None,
                            "onNull": None,
                        }
                    }
                }},
                {"$match": {**lead_scope_match, "lead_conv_dt": {"$gte": start_utc, "$lt": end_utc}}},
                {"$group": {"_id": "$agent_id", "conversions": {"$sum": 1}}},
            ]
        )
    )
    lead_agent_branch_map = {str(r.get("_id")): int(r.get("leads_registered", 0) or 0) for r in lead_branch_rows if r.get("_id") is not None}
    conv_agent_branch_map = {str(r.get("_id")): int(r.get("conversions", 0) or 0) for r in conv_branch_rows if r.get("_id") is not None}

    leaderboard_branches_top10 = []
    for branch_name, agg in sorted(branch_totals.items(), key=lambda kv: kv[1]["total"], reverse=True)[:10]:
        total_amount = round(agg["total"], 2)
        count = int(agg["count"] or 0)
        branch_leads = 0
        branch_conversions = 0
        for aid, agent_doc in branch_agent_map.items():
            if (agent_doc.get("branch") or "-") != branch_name:
                continue
            branch_leads += int(lead_agent_branch_map.get(str(aid), 0))
            branch_conversions += int(conv_agent_branch_map.get(str(aid), 0))
        branch_conv_ratio = round((branch_conversions / branch_leads) * 100, 1) if branch_leads else 0.0
        branch_target_total = {
            **{c + '_' + suffix: 0.0 for c in CHANNELS for suffix in ('target_daily', 'achieved_today')},
            "cash_target_daily": 0.0,
            "product_target_daily": 0,
            "customer_target_daily": 0,
            "cash_achieved_today": 0.0,
            "products_achieved_today": 0,
            "customers_gained_today": 0,
        }
        for aid, agent_doc in (targets_context.get("agent_map") or {}).items():
            if (agent_doc.get("branch") or "-") != branch_name:
                continue
            tinfo = agent_progress_map.get(str(aid), {})
            branch_target_total["cash_target_daily"] += float(tinfo.get("cash_target_daily") or 0.0)
            branch_target_total["product_target_daily"] += int(tinfo.get("product_target_daily") or 0)
            branch_target_total["customer_target_daily"] += int(tinfo.get("customer_target_daily") or 0)
            branch_target_total["cash_achieved_today"] += float(tinfo.get("cash_achieved_today") or 0.0)
            branch_target_total["products_achieved_today"] += int(tinfo.get("products_achieved_today") or 0)
            branch_target_total["customers_gained_today"] += int(tinfo.get("customers_gained_today") or 0)
            for c in CHANNELS:
                for suffix in ('target_daily', 'achieved_today'):
                    key = c + '_' + suffix
                    branch_target_total[key] += float(tinfo.get(key) or 0)
        branch_parts = []
        for c in CHANNELS:
            if branch_target_total[c + '_target_daily']:
                branch_parts.append(branch_target_total[c + '_achieved_today'] / branch_target_total[c + '_target_daily'] * 100)
        if branch_target_total["cash_target_daily"]:
            branch_parts.append(branch_target_total["cash_achieved_today"] / branch_target_total["cash_target_daily"] * 100)
        if branch_target_total["product_target_daily"]:
            branch_parts.append(branch_target_total["products_achieved_today"] / branch_target_total["product_target_daily"] * 100)
        if branch_target_total["customer_target_daily"]:
            branch_parts.append(branch_target_total["customers_gained_today"] / branch_target_total["customer_target_daily"] * 100)
        branch_overall = round(sum(branch_parts) / len(branch_parts), 2) if branch_parts else 0.0
        branch_hit = branch_overall >= 100 or sum(1 for v in branch_parts if v >= 100) >= 2
        leaderboard_branches_top10.append({
            "branch": branch_name,
            "payments_count": count,
            "total_amount": total_amount,
            "avg_payment": round((total_amount / count), 2) if count else 0.0,
            "leads_registered": branch_leads,
            "conversion_ratio": branch_conv_ratio,
            **{c + '_' + suffix: round(branch_target_total[c + '_' + suffix], 2) for c in CHANNELS for suffix in ('target_daily', 'achieved_today')},
            "cash_target_daily": round(branch_target_total["cash_target_daily"], 2),
            "product_target_daily": branch_target_total["product_target_daily"],
            "customer_target_daily": branch_target_total["customer_target_daily"],
            "cash_achieved_today": round(branch_target_total["cash_achieved_today"], 2),
            "products_achieved_today": branch_target_total["products_achieved_today"],
            "customers_gained_today": branch_target_total["customers_gained_today"],
            "overall_progress_pct": branch_overall,
            "target_hit": branch_hit,
        })

    recent_payments = list(
        payments_col.find(payment_match)
        .sort([("created_at", -1), ("_id", -1)])
        .limit(20)
    )
    recent_payment_customer_ids = [p.get("customer_id") for p in recent_payments if p.get("customer_id") is not None]
    recent_payment_agent_ids = [p.get("agent_id") for p in recent_payments if p.get("agent_id") is not None]

    recent_payments_out = []
    recent_customers = list(
        customers_col.find(customer_match)
        .sort([("date_registered", -1), ("_id", -1)])
        .limit(20)
    )
    recent_customer_agent_ids = [c.get("agent_id") for c in recent_customers if c.get("agent_id") is not None]

    agent_ids_union = set([str(a) for a in recent_payment_agent_ids + recent_customer_agent_ids + leaderboard_agent_ids if a is not None])
    if agent_map:
        agent_ids_union.update(agent_map.keys())
    agent_map.update(_user_map_by_ids(list(agent_ids_union)))
    customer_map = _customer_name_map(recent_payment_customer_ids)

    for p in recent_payments:
        created_at = p.get("created_at")
        if not isinstance(created_at, datetime):
            try:
                created_at = p.get("_id").generation_time
            except Exception:
                created_at = None
        recent_payments_out.append({
            "id": str(p.get("_id")),
            "created_at": _dt_iso(created_at),
            "time": _dt_time_local(created_at),
            "agent_name": (agent_map.get(str(p.get("agent_id"))) or {}).get("name") or "Agent",
            "customer_name": customer_map.get(str(p.get("customer_id")), "Customer"),
            "amount": round(_safe_amount(p.get("amount")), 2),
            "type": p.get("payment_type") or "",
        })

    recent_customers_out = []
    for c in recent_customers:
        created_at = c.get("date_registered")
        if not isinstance(created_at, datetime):
            try:
                created_at = c.get("_id").generation_time
            except Exception:
                created_at = None
        recent_customers_out.append({
            "id": str(c.get("_id")),
            "created_at": _dt_iso(created_at),
            "time": _dt_time_local(created_at),
            "agent_name": (agent_map.get(str(c.get("agent_id"))) or {}).get("name") or "Agent",
            "customer_name": c.get("name") or "Customer",
            "phone": c.get("phone_number") or "",
            "location": c.get("location") or "",
        })

    today_totals = targets_context.get("totals", {})
    yday_totals = targets_context_yday.get("totals", {})
    return jsonify(
        ok=True,
        today=today_str,
        last_updated=_dt_iso(datetime.utcnow()),
        kpis={
            "total_sales_today": total_sales_today,
            "payment_count_today": payment_count_today,
            "customers_registered_today": int(customers_registered_today),
            "active_agents_today": int(active_agents_today),
            "avg_payment_today": avg_payment_today,
            "leads_today": int(leads_today),
            "conversions_today": int(conversions_today),
            "conversion_ratio_today": conversion_ratio_today,
            "agents_with_targets": int(targets_context.get("agents_with_targets") or 0),
            "agents_hit_target_count": int(targets_context.get("agents_hit_target_count") or 0),
            "total_cash_target_today": targets_context.get("totals", {}).get("total_cash_target_today", 0),
            "total_cash_achieved_today": targets_context.get("totals", {}).get("total_cash_achieved_today", 0),
            "total_product_target_today": targets_context.get("totals", {}).get("total_product_target_today", 0),
            "total_products_achieved_today": targets_context.get("totals", {}).get("total_products_achieved_today", 0),
            "total_customer_target_today": targets_context.get("totals", {}).get("total_customer_target_today", 0),
            "total_customers_gained_today": targets_context.get("totals", {}).get("total_customers_gained_today", 0),
            "overall_coverage_pct_today": targets_context.get("overall_coverage_pct_today", 0),
            "daily_targets_all": {
                "cash_target_total": today_totals.get("total_cash_target_today", 0),
                "product_target_total": today_totals.get("total_product_target_today", 0),
                "leads_target_total": today_totals.get("total_customer_target_today", 0),
            },
            "covered_all_today": {
                "cash_achieved_total": today_totals.get("total_cash_achieved_today", 0),
                "product_achieved_total": today_totals.get("total_products_achieved_today", 0),
                "leads_achieved_total": today_totals.get("total_customers_gained_today", 0),
                "overall_coverage_pct": targets_context.get("overall_coverage_pct_today", 0),
            },
            "covered_change_vs_yesterday": {
                "cash_today": today_totals.get("total_cash_achieved_today", 0),
                "cash_yesterday": yday_totals.get("total_cash_achieved_today", 0),
                "cash_delta": round((today_totals.get("total_cash_achieved_today", 0) - yday_totals.get("total_cash_achieved_today", 0)), 2),
                "cash_pct_change": round(((today_totals.get("total_cash_achieved_today", 0) - yday_totals.get("total_cash_achieved_today", 0)) / max(yday_totals.get("total_cash_achieved_today", 0), 1)) * 100, 1),
                "products_today": today_totals.get("total_products_achieved_today", 0),
                "products_yesterday": yday_totals.get("total_products_achieved_today", 0),
                "products_delta": int(today_totals.get("total_products_achieved_today", 0) - yday_totals.get("total_products_achieved_today", 0)),
                "leads_today": today_totals.get("total_customers_gained_today", 0),
                "leads_yesterday": yday_totals.get("total_customers_gained_today", 0),
                "leads_delta": int(today_totals.get("total_customers_gained_today", 0) - yday_totals.get("total_customers_gained_today", 0)),
            },
        },
        leaderboard_agents=leaderboard_agents,
        leaderboard_managers_top=leaderboard_managers_top10,
        leaderboard_branches_top=leaderboard_branches_top10,
        target_hit_spotlight=sorted(
            [
                {
                    "agent_id": aid,
                    "agent_name": (targets_context.get("agent_map") or {}).get(aid, {}).get("name") or "Agent",
                    "branch": (targets_context.get("agent_map") or {}).get(aid, {}).get("branch") or "-",
                    "overall_progress_pct": info.get("overall_progress_pct", 0),
                    "cash_progress_pct": info.get("cash_progress_pct", 0),
                    "product_progress_pct": info.get("product_progress_pct", 0),
                    "customer_progress_pct": info.get("customer_progress_pct", 0),
                }
                for aid, info in agent_progress_map.items()
                if info.get("target_hit")
            ],
            key=lambda r: r.get("overall_progress_pct", 0),
            reverse=True,
        )[:5],
        recent={
            "payments": recent_payments_out,
            "customers": recent_customers_out,
        },
    )


@executive_bp.route("/executive/api/today-live/events")
@role_required("executive", "admin")
def executive_today_live_events():
    after = request.args.get("after") or ""
    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()

    after_ts, after_oid = _parse_cursor(after)

    now_accra, start_accra, end_accra = _accra_day_bounds()
    start_utc = _utc_naive(start_accra.astimezone(timezone.utc))
    end_utc = _utc_naive(end_accra.astimezone(timezone.utc))
    today_str = start_accra.strftime("%Y-%m-%d")

    agent_ids, agent_map, manager_variants = _agent_ids_for_filters(manager_id, branch)

    payment_match = {"date": today_str, "payment_type": {"$ne": "WITHDRAWAL"}}
    if manager_variants:
        payment_match["manager_id"] = {"$in": manager_variants}
    if branch:
        if agent_ids:
            payment_match["agent_id"] = {"$in": agent_ids}
        else:
            payment_match["agent_id"] = {"$in": []}

    customer_match = {"date_registered": {"$gte": start_utc, "$lt": end_utc}}
    if manager_variants:
        customer_match["manager_id"] = {"$in": manager_variants}
    if branch:
        if agent_ids:
            customer_match["agent_id"] = {"$in": agent_ids}
        else:
            customer_match["agent_id"] = {"$in": []}

    if after_ts:
        payment_match["created_at"] = {"$gt": max(after_ts, start_utc), "$lt": end_utc}
        customer_match["date_registered"] = {"$gt": max(after_ts, start_utc), "$lt": end_utc}
    elif after_oid:
        payment_match["_id"] = {"$gt": after_oid}
        customer_match["_id"] = {"$gt": after_oid}

    new_payments = list(
        payments_col.find(payment_match)
        .sort([("created_at", 1), ("_id", 1)])
        .limit(50)
    )
    new_customers = list(
        customers_col.find(customer_match)
        .sort([("date_registered", 1), ("_id", 1)])
        .limit(50)
    )

    customer_ids = [p.get("customer_id") for p in new_payments if p.get("customer_id") is not None]
    customer_map = _customer_name_map(customer_ids)

    agent_ids_union = set()
    agent_ids_union.update([str(p.get("agent_id")) for p in new_payments if p.get("agent_id") is not None])
    agent_ids_union.update([str(c.get("agent_id")) for c in new_customers if c.get("agent_id") is not None])
    if agent_map:
        agent_ids_union.update(agent_map.keys())
    agent_map.update(_user_map_by_ids(list(agent_ids_union)))

    events = []
    max_ts = None
    max_oid = None

    for c in new_customers:
        created_at = c.get("date_registered")
        if not isinstance(created_at, datetime):
            try:
                created_at = c.get("_id").generation_time
            except Exception:
                created_at = None
        if created_at and (max_ts is None or created_at > max_ts):
            max_ts = created_at
        if c.get("_id") and (max_oid is None or c.get("_id") > max_oid):
            max_oid = c.get("_id")
        agent_doc = agent_map.get(str(c.get("agent_id"))) or {}
        events.append({
            "event_id": f"cust:{str(c.get('_id'))}",
            "type": "customer_registered",
            "ts": _dt_iso(created_at),
            "agent_id": str(c.get("agent_id") or ""),
            "customer_id": str(c.get("_id")),
            "customer_name": c.get("name") or "Customer",
            "agent_name": agent_doc.get("name") or "Agent",
            "branch": agent_doc.get("branch") or "",
        })

    for p in new_payments:
        created_at = p.get("created_at")
        if not isinstance(created_at, datetime):
            try:
                created_at = p.get("_id").generation_time
            except Exception:
                created_at = None
        if created_at and (max_ts is None or created_at > max_ts):
            max_ts = created_at
        if p.get("_id") and (max_oid is None or p.get("_id") > max_oid):
            max_oid = p.get("_id")
        agent_doc = agent_map.get(str(p.get("agent_id"))) or {}
        events.append({
            "event_id": f"pay:{str(p.get('_id'))}",
            "type": "payment_added",
            "ts": _dt_iso(created_at),
            "agent_id": str(p.get("agent_id") or ""),
            "payment_id": str(p.get("_id")),
            "amount": round(_safe_amount(p.get("amount")), 2),
            "agent_name": agent_doc.get("name") or "Agent",
            "branch": agent_doc.get("branch") or "",
            "customer_name": customer_map.get(str(p.get("customer_id")), "Customer"),
            "payment_type": p.get("payment_type") or "",
        })

    events.sort(key=lambda e: e.get("ts") or "")

    if max_ts:
        next_cursor = _dt_iso(max_ts)
    elif max_oid:
        next_cursor = str(max_oid)
    else:
        next_cursor = after or _dt_iso(datetime.utcnow())

    return jsonify(ok=True, next_cursor=next_cursor, events=events)


@executive_bp.route("/executive/dashboard/charts")
def dashboard_charts():
    """
    Heavier aggregations for charts.
    Called asynchronously from the frontend so the main page loads faster.
    """
    # Top Products Sold
    top_products_cursor = customers_col.aggregate([
        {"$unwind": "$purchases"},
        {"$group": {"_id": "$purchases.product.name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5}
    ])
    top_products = {"labels": [], "values": []}
    for item in top_products_cursor:
        top_products["labels"].append(item["_id"] or "Unnamed")
        top_products["values"].append(item["count"])

    # Top Managers by Payments
    top_managers_cursor = payments_col.aggregate([
        {"$match": {"payment_type": {"$ne": "WITHDRAWAL"}}},
        {"$lookup": {
            "from": "users",
            "localField": "manager_id",
            "foreignField": "_id",
            "as": "manager"
        }},
        {"$unwind": "$manager"},
        {"$group": {
            "_id": {
                "manager_id": "$manager._id",
                "name": "$manager.name",
                "branch": "$manager.branch"
            },
            "total": {"$sum": "$amount"}
        }},
        {"$sort": {"total": -1}},
        {"$limit": 5}
    ])
    top_managers = {"labels": [], "values": []}
    for item in top_managers_cursor:
        label = f"{item['_id']['name']} ({item['_id']['branch']})"
        top_managers["labels"].append(label)
        top_managers["values"].append(round(item["total"], 2))

    return jsonify({
        "ok": True,
        "top_products": top_products,
        "top_managers": top_managers
    })


@executive_bp.route("/executive/dashboard/insights")
def dashboard_insights():
    range_key = (request.args.get("range") or "today").strip().lower()
    if range_key not in ("today", "week", "month"):
        range_key = "today"

    now = datetime.utcnow()
    today = now.date()
    today_str = today.strftime("%Y-%m-%d")
    yesterday = today - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    now_accra, start_accra_day, end_accra_day = _accra_day_bounds()
    _, start_accra_week, end_accra_week = _accra_week_bounds()
    _, start_accra_month, end_accra_month = _accra_month_bounds()

    start_today = _utc_naive(start_accra_day.astimezone(timezone.utc))
    end_today = _utc_naive(end_accra_day.astimezone(timezone.utc))
    start_week = _utc_naive(start_accra_week.astimezone(timezone.utc))
    end_week = _utc_naive(end_accra_week.astimezone(timezone.utc))
    start_month = _utc_naive(start_accra_month.astimezone(timezone.utc))
    end_month = _utc_naive(end_accra_month.astimezone(timezone.utc))

    start_yesterday, end_yesterday = _today_range_utc(yesterday)

    if range_key == "week":
        range_start = start_week.date().strftime("%Y-%m-%d")
        range_end = (end_week.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    elif range_key == "month":
        range_start = start_month.date().strftime("%Y-%m-%d")
        range_end = today.strftime("%Y-%m-%d")
    else:
        range_start = today_str
        range_end = today_str

    def _sum_and_count(date_str):
        rows = list(
            payments_col.aggregate(
                [
                    {"$match": {"date": date_str, "payment_type": {"$ne": "WITHDRAWAL"}}},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
                ]
            )
        )
        if not rows:
            return 0.0, 0
        return round(_safe_amount(rows[0].get("total")), 2), int(rows[0].get("count", 0) or 0)

    sales_today, payments_count_today = _sum_and_count(today_str)
    sales_yesterday, _ = _sum_and_count(yesterday_str)

    customer_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": today_str, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$customer_id"}},
                {"$count": "count"},
            ]
        )
    )
    customers_paid_today = int(customer_rows[0].get("count", 0) if customer_rows else 0)

    if sales_yesterday == 0 and sales_today > 0:
        sales_change_pct = 100.0
        sales_change_dir = "up"
    elif sales_yesterday == 0 and sales_today == 0:
        sales_change_pct = 0.0
        sales_change_dir = "no_change"
    else:
        sales_change_pct = round(((sales_today - sales_yesterday) / sales_yesterday) * 100, 1)
        sales_change_dir = "up" if sales_change_pct >= 0 else "down"

    avg_payment_today = round((sales_today / payments_count_today), 2) if payments_count_today else 0.0

    expense_today_rows = list(
        manager_expenses_col.aggregate(
            [
                {"$match": {"created_at": {"$gte": start_today, "$lt": end_today}, "status": "Approved"}},
                {"$group": {"_id": None, "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
            ]
        )
    )
    expense_today_approved = round(_safe_amount(expense_today_rows[0].get("total")) if expense_today_rows else 0.0, 2)

    expense_week_rows = list(
        manager_expenses_col.aggregate(
            [
                {"$match": {"created_at": {"$gte": start_week, "$lt": end_week}, "status": "Approved"}},
                {"$group": {"_id": "$category", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
                {"$sort": {"total": -1}},
                {"$limit": 10},
            ]
        )
    )
    expense_week_categories = []
    expense_week_approved_total = 0.0
    for row in expense_week_rows:
        total_val = _safe_amount(row.get("total"))
        expense_week_categories.append({"category": row.get("_id") or "Uncategorized", "total": round(total_val, 2)})
        expense_week_approved_total += total_val

    sales_mtd_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": {"$gte": start_month.strftime("%Y-%m-%d"), "$lte": today_str}, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
            ]
        )
    )
    sales_mtd = _safe_amount(sales_mtd_rows[0].get("total")) if sales_mtd_rows else 0.0
    expense_mtd_rows = list(
        manager_expenses_col.aggregate(
            [
                {"$match": {"created_at": {"$gte": start_month, "$lt": end_month}, "status": "Approved"}},
                {"$group": {"_id": None, "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
            ]
        )
    )
    expense_mtd = _safe_amount(expense_mtd_rows[0].get("total")) if expense_mtd_rows else 0.0
    expense_vs_sales_pct_mtd = round((expense_mtd / sales_mtd) * 100, 1) if sales_mtd else 0.0

    def _count_leads_in_range(start_dt, end_dt):
        pipeline = [
            {"$addFields": {
                "lead_reg_dt": {
                    "$convert": {
                        "input": {"$ifNull": ["$lead_registered_at", "$date_registered"]},
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }},
            {"$match": {"lead_reg_dt": {"$gte": start_dt, "$lt": end_dt}}},
            {"$count": "count"},
        ]
        rows = list(customers_col.aggregate(pipeline))
        return int(rows[0].get("count", 0) if rows else 0)

    def _count_conversions_in_range(start_dt, end_dt):
        pipeline = [
            {"$addFields": {
                "lead_conv_dt": {
                    "$convert": {
                        "input": "$lead_converted_at",
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }},
            {"$match": {"lead_conv_dt": {"$gte": start_dt, "$lt": end_dt}}},
            {"$count": "count"},
        ]
        rows = list(customers_col.aggregate(pipeline))
        return int(rows[0].get("count", 0) if rows else 0)

    leads_today = _count_leads_in_range(start_today, end_today)
    leads_week = _count_leads_in_range(start_week, end_week)
    leads_month = _count_leads_in_range(start_month, end_month)

    conversions_today = _count_conversions_in_range(start_today, end_today)
    conversions_week = _count_conversions_in_range(start_week, end_week)
    conversions_month = _count_conversions_in_range(start_month, end_month)

    def _ratio(conv, leads):
        return round((conv / leads) * 100, 1) if leads else 0.0

    conv_ratio_today = _ratio(conversions_today, leads_today)
    conv_ratio_week = _ratio(conversions_week, leads_week)
    conv_ratio_month = _ratio(conversions_month, leads_month)

    pay_dt_expr = {
        "$ifNull": [
            "$created_at",
            {
                "$dateFromString": {
                    "dateString": {
                        "$concat": [
                            {"$ifNull": ["$date", ""]},
                            " ",
                            {"$ifNull": ["$time", "00:00:00"]},
                        ]
                    },
                    "format": "%Y-%m-%d %H:%M:%S",
                    "onError": None,
                    "onNull": None,
                }
            },
        ]
    }

    def _hourly_series(start_dt, end_dt):
        rows = list(
            payments_col.aggregate(
                [
                    {"$match": {"payment_type": {"$ne": "WITHDRAWAL"}}},
                    {"$addFields": {"pay_dt": pay_dt_expr}},
                    {"$match": {"pay_dt": {"$ne": None, "$gte": start_dt, "$lt": end_dt}}},
                    {"$group": {"_id": {"$hour": "$pay_dt"}, "total": {"$sum": "$amount"}}},
                ]
            )
        )
        hourly = {int(row["_id"]): round(_safe_amount(row.get("total")), 2) for row in rows if row.get("_id") is not None}
        return [hourly.get(h, 0) for h in range(24)]

    hourly_today = _hourly_series(start_today, end_today)
    hourly_yesterday = _hourly_series(start_yesterday, end_yesterday)
    hourly_labels = [f"{h:02d}:00" for h in range(24)]

    month_days = []
    cur = start_month.date()
    while cur <= today:
        month_days.append(cur.strftime("%Y-%m-%d"))
        cur = cur + timedelta(days=1)

    sales_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": {"$gte": start_month.strftime("%Y-%m-%d"), "$lte": today_str}, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$date", "total": {"$sum": "$amount"}}},
            ]
        )
    )
    sales_map = {row.get("_id"): round(_safe_amount(row.get("total")), 2) for row in sales_rows}
    sales_month = [sales_map.get(day, 0) for day in month_days]

    expense_rows = list(
        manager_expenses_col.aggregate(
            [
                {"$match": {"created_at": {"$gte": start_month, "$lt": end_month}, "status": "Approved"}},
                {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
            ]
        )
    )
    expense_map = {row.get("_id"): round(_safe_amount(row.get("total")), 2) for row in expense_rows}
    expense_month = [expense_map.get(day, 0) for day in month_days]

    manager_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": {"$gte": range_start, "$lte": range_end}, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$manager_id", "amount": {"$sum": "$amount"}, "count": {"$sum": 1}}},
                {"$sort": {"amount": -1}},
                {"$limit": 10},
            ]
        )
    )
    manager_ids = [row.get("_id") for row in manager_rows if row.get("_id") is not None]
    manager_name_map = _lookup_name_map("manager", manager_ids)
    managers = []
    for row in manager_rows:
        mid = row.get("_id")
        managers.append(
            {
                "manager_id": str(mid) if mid is not None else "",
                "manager_name": manager_name_map.get(str(mid), "Unknown"),
                "amount": round(_safe_amount(row.get("amount")), 2),
                "count": int(row.get("count", 0) or 0),
            }
        )

    agent_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": {"$gte": range_start, "$lte": range_end}, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$agent_id", "amount": {"$sum": "$amount"}, "count": {"$sum": 1}, "customers": {"$addToSet": "$customer_id"}}},
                {"$sort": {"amount": -1}},
                {"$limit": 10},
            ]
        )
    )
    agent_ids = [row.get("_id") for row in agent_rows if row.get("_id") is not None]
    agent_name_map = _lookup_name_map(("agent", "manager"), agent_ids)
    agents = []
    for row in agent_rows:
        aid = row.get("_id")
        customers = row.get("customers") or []
        agents.append(
            {
                "agent_id": str(aid) if aid is not None else "",
                "agent_name": agent_name_map.get(str(aid), "Unknown"),
                "amount": round(_safe_amount(row.get("amount")), 2),
                "count": int(row.get("count", 0) or 0),
                "customers": int(len(customers)),
            }
        )

    return jsonify(
        ok=True,
        kpis={
            "sales_today": sales_today,
            "sales_yesterday": sales_yesterday,
            "sales_change_pct": round(sales_change_pct, 1),
            "sales_change_dir": sales_change_dir,
            "customers_paid_today": customers_paid_today,
            "avg_payment_today": avg_payment_today,
            "payments_count_today": payments_count_today,
            "expense_today_approved": expense_today_approved,
            "expense_week_approved_total": round(expense_week_approved_total, 2),
            "expense_vs_sales_pct_mtd": round(expense_vs_sales_pct_mtd, 1),
        },
        leads={
            "today": int(leads_today),
            "week": int(leads_week),
            "month": int(leads_month),
        },
        conversions={
            "today": int(conversions_today),
            "week": int(conversions_week),
            "month": int(conversions_month),
        },
        conversion_ratio={
            "today": conv_ratio_today,
            "week": conv_ratio_week,
            "month": conv_ratio_month,
        },
        charts={
            "hourly": {"labels": hourly_labels, "today": hourly_today, "yesterday": hourly_yesterday},
            "sales_month": {"labels": month_days, "values": sales_month},
            "expense_month": {"labels": month_days, "values": expense_month},
            "expense_week_by_cat": {
                "labels": [row["category"] for row in expense_week_categories],
                "values": [row["total"] for row in expense_week_categories],
            },
        },
        leaderboards={"managers": managers, "agents": agents},
        expense_week_categories=expense_week_categories,
    )


@executive_bp.route("/executive/dashboard/kpis-today")
def dashboard_kpis_today():
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    def _sum_for_date(date_str):
        rows = list(
            payments_col.aggregate(
                [
                    {"$match": {"date": date_str, "payment_type": {"$ne": "WITHDRAWAL"}}},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
                ]
            )
        )
        return round(_safe_amount(rows[0].get("total")) if rows else 0.0, 2)

    today_sales = _sum_for_date(today_str)
    yesterday_sales = _sum_for_date(yesterday_str)

    if yesterday_sales == 0 and today_sales > 0:
        pct_change = 100.0
        change_label = "New"
    elif yesterday_sales == 0 and today_sales == 0:
        pct_change = 0.0
        change_label = "No change"
    else:
        pct_change = round(((today_sales - yesterday_sales) / yesterday_sales) * 100, 1)
        change_label = "Up" if pct_change >= 0 else "Down"

    top_manager = None
    top_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": today_str, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$manager_id", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
                {"$sort": {"total": -1}},
                {"$limit": 1},
            ]
        )
    )
    if top_rows:
        row = top_rows[0]
        manager_id = row.get("_id")
        top_manager = {
            "manager_id": str(manager_id) if manager_id is not None else "",
            "manager_name": _lookup_manager_name(manager_id),
            "total_sales": round(_safe_amount(row.get("total")), 2),
            "payment_count": int(row.get("count", 0) or 0),
        }

    return jsonify(
        {
            "ok": True,
            "today_sales": today_sales,
            "yesterday_sales": yesterday_sales,
            "pct_change": pct_change,
            "change_label": change_label,
            "top_manager": top_manager,
        }
    )


@executive_bp.route("/executive/dashboard/products-insights")
def dashboard_products_insights():
    range_key = (request.args.get("range") or "today").strip().lower()
    if range_key not in ("today", "week", "month"):
        range_key = "today"

    now_accra, start_accra_day, _ = _accra_day_bounds()
    _, start_accra_week, end_accra_week = _accra_week_bounds()
    _, start_accra_month, _ = _accra_month_bounds()

    today_str = start_accra_day.strftime("%Y-%m-%d")
    if range_key == "week":
        range_start = start_accra_week.date().strftime("%Y-%m-%d")
        range_end = (end_accra_week.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    elif range_key == "month":
        range_start = start_accra_month.date().strftime("%Y-%m-%d")
        range_end = today_str
    else:
        range_start = today_str
        range_end = today_str

    base_match = {
        "purchases": {"$exists": True, "$ne": []}
    }
    purchase_match = {
        "purchases.purchase_date": {"$gte": range_start, "$lte": range_end}
    }
    qty_expr = {
        "$convert": {
            "input": {"$ifNull": ["$purchases.product.quantity", 1]},
            "to": "double",
            "onError": 1,
            "onNull": 1,
        }
    }
    price_expr = {
        "$convert": {
            "input": {"$ifNull": ["$purchases.product.price", 0]},
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    total_expr = {
        "$convert": {
            "input": "$purchases.product.total",
            "to": "double",
            "onError": None,
            "onNull": None,
        }
    }
    revenue_expr = {"$ifNull": [total_expr, {"$multiply": [price_expr, qty_expr]}]}

    summary_rows = list(
        customers_col.aggregate(
            [
                {"$match": base_match},
                {"$unwind": "$purchases"},
                {"$match": purchase_match},
                {"$project": {"customer_id": "$_id", "qty": qty_expr, "revenue": revenue_expr}},
                {"$group": {
                    "_id": None,
                    "units": {"$sum": "$qty"},
                    "revenue": {"$sum": "$revenue"},
                    "purchases_count": {"$sum": 1},
                    "customers": {"$addToSet": "$customer_id"},
                }},
                {"$project": {
                    "units": 1,
                    "revenue": 1,
                    "purchases_count": 1,
                    "unique_customers": {"$size": "$customers"},
                }},
            ]
        )
    )
    summary = summary_rows[0] if summary_rows else {}

    top_rows = list(
        customers_col.aggregate(
            [
                {"$match": base_match},
                {"$unwind": "$purchases"},
                {"$match": purchase_match},
                {"$project": {
                    "product_name": {"$ifNull": ["$purchases.product.name", "Unknown Product"]},
                    "qty": qty_expr,
                    "revenue": revenue_expr,
                }},
                {"$group": {
                    "_id": "$product_name",
                    "units": {"$sum": "$qty"},
                    "revenue": {"$sum": "$revenue"},
                }},
                {"$sort": {"units": -1}},
                {"$limit": 8},
            ]
        )
    )

    recent_rows = list(
        customers_col.aggregate(
            [
                {"$match": base_match},
                {"$unwind": "$purchases"},
                {"$match": purchase_match},
                {"$project": {
                    "customer_name": "$name",
                    "agent_id": "$agent_id",
                    "purchase_date": "$purchases.purchase_date",
                    "product_name": {"$ifNull": ["$purchases.product.name", "Unknown Product"]},
                    "qty": qty_expr,
                    "revenue": revenue_expr,
                }},
                {"$sort": {"purchase_date": -1, "_id": -1}},
                {"$limit": 10},
            ]
        )
    )

    agent_ids = [r.get("agent_id") for r in recent_rows if r.get("agent_id") is not None]
    agent_name_map = _lookup_name_map("agent", agent_ids)

    top_products = [
        {
            "name": row.get("_id") or "Unknown Product",
            "units": round(_safe_amount(row.get("units")), 2),
            "revenue": round(_safe_amount(row.get("revenue")), 2),
        }
        for row in top_rows
    ]
    recent_products = []
    for row in recent_rows:
        aid = row.get("agent_id")
        recent_products.append({
            "customer_name": row.get("customer_name") or "Customer",
            "agent_name": agent_name_map.get(str(aid), "Agent") if aid is not None else "Agent",
            "product_name": row.get("product_name") or "Unknown Product",
            "units": round(_safe_amount(row.get("qty")), 2),
            "total": round(_safe_amount(row.get("revenue")), 2),
            "purchase_date": row.get("purchase_date") or "",
        })

    chart = {
        "labels": [p["name"] for p in top_products],
        "units": [p["units"] for p in top_products],
        "revenue": [p["revenue"] for p in top_products],
    }

    return jsonify(
        ok=True,
        range=range_key,
        summary={
            "new_products_sold_count": int(round(_safe_amount(summary.get("units")))),
            "purchases_count": int(summary.get("purchases_count", 0) or 0),
            "unique_customers": int(summary.get("unique_customers", 0) or 0),
            "revenue_total": round(_safe_amount(summary.get("revenue")), 2),
        },
        top_products=top_products,
        recent_products=recent_products,
        chart=chart,
    )


@executive_bp.route("/executive/dashboard/week-expenses-by-category")
def dashboard_week_expenses_by_category():
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())
    start_dt = datetime.combine(start_of_week, datetime.min.time())
    end_dt = datetime.utcnow()

    pipeline = [
        {"$match": {"created_at": {"$gte": start_dt, "$lte": end_dt}, "status": "Approved"}},
        {"$group": {"_id": "$category", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
        {"$sort": {"total": -1}},
        {"$limit": 5},
    ]
    rows = list(manager_expenses_col.aggregate(pipeline))

    top_categories = []
    total_week = 0.0
    for row in rows:
        total = _safe_amount(row.get("total"))
        top_categories.append({"category": row.get("_id") or "Uncategorized", "total": round(total, 2)})
        total_week += total

    return jsonify(ok=True, total_week=round(total_week, 2), top_categories=top_categories)


@executive_bp.route("/executive/dashboard/trends")
def dashboard_trends():
    today = datetime.utcnow().date()
    start_of_month = datetime(today.year, today.month, 1)
    if today.month == 12:
        next_month = datetime(today.year + 1, 1, 1)
    else:
        next_month = datetime(today.year, today.month + 1, 1)

    end_of_month = (next_month - timedelta(days=1)).date()
    start_str = start_of_month.strftime("%Y-%m-%d")
    end_str = end_of_month.strftime("%Y-%m-%d")

    days = []
    cur = start_of_month.date()
    while cur <= end_of_month:
        days.append(cur.strftime("%Y-%m-%d"))
        cur = cur + timedelta(days=1)

    sales_rows = list(
        payments_col.aggregate(
            [
                {"$match": {"date": {"$gte": start_str, "$lte": end_str}, "payment_type": {"$ne": "WITHDRAWAL"}}},
                {"$group": {"_id": "$date", "total": {"$sum": "$amount"}}},
            ]
        )
    )
    sales_map = {row.get("_id"): round(_safe_amount(row.get("total")), 2) for row in sales_rows}

    expense_rows = list(
        manager_expenses_col.aggregate(
            [
                {"$match": {"created_at": {"$gte": start_of_month, "$lt": next_month}, "status": "Approved"}},
                {
                    "$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                        "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
                    }
                },
            ]
        )
    )
    expense_map = {row.get("_id"): round(_safe_amount(row.get("total")), 2) for row in expense_rows}

    sales_daily = [sales_map.get(day, 0) for day in days]
    expense_daily = [expense_map.get(day, 0) for day in days]

    return jsonify(ok=True, days=days, sales_daily=sales_daily, expense_daily=expense_daily)
