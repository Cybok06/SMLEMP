from flask import Blueprint, render_template, session, redirect, url_for, flash, jsonify
from bson.objectid import ObjectId
from datetime import datetime, timedelta, date
from db import db
from routes.executive_target import compute_manager_target_progress
from services.target_cash import CHANNELS, extend_daily_progress, extend_daily_totals

manager_dashboard_bp = Blueprint('manager_dashboard', __name__, url_prefix='/manager')

# MongoDB collections
targets_collection   = db["targets"]
users_collection     = db["users"]
customers_collection = db["customers"]
payments_collection  = db["payments"]
manager_expenses_col = db["manager_expenses"]   # holds manager expenses with status
executive_targets_col = db["executive_targets"]

# ---------------------------- Indexes (idempotent, safe) ----------------------------
def _ensure_indexes():
    try:
        users_collection.create_index([("manager_id", 1), ("role", 1)])
        payments_collection.create_index([("agent_id", 1), ("date", 1), ("payment_type", 1)])
        customers_collection.create_index([("manager_id", 1)])
        executive_targets_col.create_index([("manager_id", 1), ("period", 1), ("is_active", 1)])
        manager_expenses_col.create_index([("manager_id", 1), ("created_at", -1)])
        manager_expenses_col.create_index([("manager_id", 1), ("status", 1), ("created_at", -1)])
    except Exception:
        # Index creation failures shouldn't break the app
        pass

_ensure_indexes()

# ---------------------------- Date helpers ----------------------------
def get_monthly_range():
    today = datetime.utcnow().date()
    start = today.replace(day=1)
    # last day of month: go to 28th, add a few days, then back to 1st of next month - 1
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()

def _week_range_utc(d: date):
    # Monday as start of week (UTC)
    start = datetime(d.year, d.month, d.day) - timedelta(days=d.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end

def _month_range_utc(d: date):
    start = datetime(d.year, d.month, 1).replace(hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end

def _today_range_utc(d: date):
    start = datetime(d.year, d.month, d.day).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)

def _agent_id_variants(agent_ids: list[str]) -> list[ObjectId]:
    obj_ids = []
    for aid in agent_ids:
        try:
            obj_ids.append(ObjectId(aid))
        except Exception:
            continue
    return obj_ids

def _daily_target_context(manager_id_str: str):
    try:
        manager_oid = ObjectId(manager_id_str)
    except Exception:
        return {}, {}

    target = executive_targets_col.find_one({
        "manager_id": manager_oid,
        "period": "daily",
        "is_active": True,
    })

    agents = list(users_collection.find(
        {"role": "agent", "manager_id": manager_oid},
        {"_id": 1, "name": 1, "image_url": 1}
    ))
    agent_ids = [str(a.get("_id")) for a in agents if a.get("_id") is not None]

    target_map = {}
    if target:
        allocs = target.get("agent_allocations") or []
        if allocs:
            for a in allocs:
                aid = str(a.get("agent_id") or "")
                if aid not in agent_ids:
                    continue
                target_map[aid] = {
                    **{c + '_target_daily': float(a.get(c + '_quota') or 0) for c in CHANNELS},
                    "cash_target_daily": float(a.get("cash_quota") or 0.0),
                    "product_target_daily": int(a.get("product_quota") or 0),
                    "customer_target_daily": int(a.get("customer_quota") or 0),
                }
    today = datetime.utcnow().date()
    today_str = today.isoformat()
    start_utc, end_utc = _today_range_utc(today)

    agent_obj_ids = _agent_id_variants(agent_ids)
    id_filters = []
    if agent_ids:
        id_filters.append({"agent_id": {"$in": agent_ids}})
    if agent_obj_ids:
        id_filters.append({"agent_id": {"$in": agent_obj_ids}})
    id_match = {"$or": id_filters} if id_filters else {"agent_id": {"$in": []}}

    payments_rows = list(
        payments_collection.aggregate([
            {"$match": {**id_match, "payment_type": {"$ne": "WITHDRAWAL"}, "date": today_str}},
            {"$group": {"_id": {"$toString": "$agent_id"}, "total": {"$sum": "$amount"}}},
        ])
    )
    cash_map = {str(r.get("_id")): float(r.get("total") or 0.0) for r in payments_rows}

    product_rows = list(
        customers_collection.aggregate([
            {"$match": {**id_match, "purchases.purchase_date": today_str}},
            {"$unwind": "$purchases"},
            {"$match": {
                "purchases.purchase_date": today_str,
                "$or": [
                    {"purchases.agent_id": {"$in": agent_ids}},
                    {"purchases.agent_id": {"$in": agent_obj_ids}},
                    {"purchases.agent_id": {"$exists": False}},
                ],
            }},
            {"$group": {"_id": {"$toString": "$agent_id"}, "units": {"$sum": {"$ifNull": ["$purchases.product.quantity", 1]}}}},
        ])
    )
    product_map = {str(r.get("_id")): int(r.get("units") or 0) for r in product_rows}

    customer_rows = list(
        customers_collection.aggregate([
            {"$match": {**id_match, "date_registered": {"$gte": start_utc, "$lt": end_utc}}},
            {"$group": {"_id": {"$toString": "$agent_id"}, "count": {"$sum": 1}}},
        ])
    )
    customer_map = {str(r.get("_id")): int(r.get("count") or 0) for r in customer_rows}

    def _clamp_pct(val):
        try:
            val = float(val)
        except Exception:
            val = 0.0
        return max(0.0, min(200.0, round(val, 2)))

    agent_progress = {}
    hit_count = 0
    with_targets = 0
    for aid in agent_ids:
        tgt = target_map.get(aid, {"cash_target_daily": 0.0, "product_target_daily": 0, "customer_target_daily": 0})
        cash_target = float(tgt.get("cash_target_daily") or 0.0)
        product_target = int(tgt.get("product_target_daily") or 0)
        customer_target = int(tgt.get("customer_target_daily") or 0)
        cash_ach = float(cash_map.get(aid, 0.0))
        prod_ach = int(product_map.get(aid, 0))
        cust_ach = int(customer_map.get(aid, 0))

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

        if cash_target or product_target or customer_target:
            with_targets += 1
        if target_hit:
            hit_count += 1

        agent_progress[aid] = {
            "cash_target_daily": round(cash_target, 2),
            "product_target_daily": product_target,
            "customer_target_daily": customer_target,
            "cash_achieved_today": round(cash_ach, 2),
            "products_achieved_today": prod_ach,
            "customers_gained_today": cust_ach,
            "cash_progress_pct": cash_pct,
            "product_progress_pct": prod_pct,
            "customer_progress_pct": cust_pct,
            "overall_progress_pct": overall,
            "target_hit": target_hit,
        }

    extend_daily_progress(agent_progress, target_map, payments_collection, today_str, today_str)
    totals = {
        "total_cash_target_today": round(sum(v.get("cash_target_daily", 0.0) for v in agent_progress.values()), 2),
        "total_cash_achieved_today": round(sum(v.get("cash_achieved_today", 0.0) for v in agent_progress.values()), 2),
        "total_products_target_today": int(sum(v.get("product_target_daily", 0) for v in agent_progress.values())),
        "total_products_achieved_today": int(sum(v.get("products_achieved_today", 0) for v in agent_progress.values())),
        "total_customers_target_today": int(sum(v.get("customer_target_daily", 0) for v in agent_progress.values())),
        "total_customers_gained_today": int(sum(v.get("customers_gained_today", 0) for v in agent_progress.values())),
        "agents_with_targets_count": with_targets,
        "agents_hit_target_count": hit_count,
    }

    extend_daily_totals(totals, agent_progress)
    cov_parts = []
    for c in CHANNELS:
        if totals[f'total_{c}_target_today']:
            cov_parts.append(totals[f'total_{c}_achieved_today'] / totals[f'total_{c}_target_today'] * 100)
    if totals["total_cash_target_today"]:
        cov_parts.append(totals["total_cash_achieved_today"] / totals["total_cash_target_today"] * 100)
    if totals["total_products_target_today"]:
        cov_parts.append(totals["total_products_achieved_today"] / totals["total_products_target_today"] * 100)
    if totals["total_customers_target_today"]:
        cov_parts.append(totals["total_customers_gained_today"] / totals["total_customers_target_today"] * 100)
    totals["overall_coverage_pct_today"] = round(sum(cov_parts) / len(cov_parts), 2) if cov_parts else 0.0

    return agent_progress, totals

# ---------------------------- Data helpers ----------------------------
def _agent_string_ids_under_manager(manager_id: ObjectId) -> list[str]:
    agents = users_collection.find(
        {"role": "agent", "manager_id": ObjectId(manager_id)},
        {"_id": 1}
    )
    return [str(a["_id"]) for a in agents]

def _sum_manager_expenses(
    manager_id_str: str,
    start_dt: datetime,
    end_dt: datetime,
    status: str | None = "Approved"
) -> float:
    match = {"manager_id": manager_id_str, "created_at": {"$gte": start_dt, "$lt": end_dt}}
    if status:
        match["status"] = status

    pipeline = [
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}}
    ]
    agg = list(manager_expenses_col.aggregate(pipeline))
    return float(agg[0]["total"]) if agg else 0.0

def _pending_month_expenses(manager_id_str: str, month_start: datetime, month_end: datetime):
    match = {
        "manager_id": manager_id_str,
        "created_at": {"$gte": month_start, "$lt": month_end},
        "status": "Unapproved"
    }
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}
        }}
    ]
    agg = list(manager_expenses_col.aggregate(pipeline))
    if agg:
        return int(agg[0]["count"]), float(agg[0]["total"])
    return 0, 0.0

# ---------------------------- Monthly target progress (current month) ----------------------------
def calculate_monthly_targets(manager_id: str):
    # latest monthly targets for this manager (you can limit if needed)
    monthly_targets = list(
        targets_collection.find(
            {"manager_id": str(manager_id), "duration_type": "monthly"}
        ).sort("created_at", -1)
    )
    if not monthly_targets:
        return []

    start_str, end_str = get_monthly_range()
    agent_id_list = _agent_string_ids_under_manager(ObjectId(manager_id))
    results = []

    # CASH (month)
    pay_sum = list(payments_collection.aggregate([
        {"$match": {
            "agent_id": {"$in": agent_id_list},
            "payment_type": {"$ne": "WITHDRAWAL"},
            "date": {"$gte": start_str, "$lte": end_str}
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]))
    month_cash = float(pay_sum[0]["total"]) if pay_sum else 0.0

    # PRODUCT units (month)
    prod_agg = list(customers_collection.aggregate([
        {"$match": {
            "manager_id": ObjectId(manager_id),
            "purchases.purchase_date": {"$gte": start_str, "$lte": end_str}
        }},
        {"$unwind": "$purchases"},
        {"$match": {"purchases.purchase_date": {"$gte": start_str, "$lte": end_str}}},
        {"$group": {
            "_id": None,
            "units": {"$sum": {"$ifNull": ["$purchases.product.quantity", 1]}}
        }}
    ]))
    month_units = int(prod_agg[0]["units"]) if prod_agg else 0

    # DISTINCT customers (month) – more efficient count
    distinct_cust_agg = customers_collection.aggregate([
        {"$match": {
            "manager_id": ObjectId(manager_id),
            "purchases.purchase_date": {"$gte": start_str, "$lte": end_str}
        }},
        {"$group": {"_id": "$_id"}},
        {"$count": "count"}
    ])
    month_customers = next(distinct_cust_agg, {}).get("count", 0)

    for target in monthly_targets:
        pt  = int(target.get("product_target") or 0)
        ct  = float(target.get("cash_target") or 0)
        cut = int(target.get("customer_target") or 0)

        product_pct  = round((month_units     / pt  * 100) if pt  else 0.0, 2)
        payment_pct  = round((month_cash      / ct  * 100) if ct  else 0.0, 2)
        customer_pct = round((month_customers / cut * 100) if cut else 0.0, 2)

        parts, total_pct = 0, 0.0
        for pct, cap in ((product_pct, pt), (payment_pct, ct), (customer_pct, cut)):
            if cap:
                parts += 1
                total_pct += pct
        overall = round((total_pct / parts) if parts else 0.0, 2)

        results.append({
            "title": target.get("title"),
            "duration": "monthly",
            "start_date": start_str,
            "end_date": end_str,
            "product_target": pt,
            "product_achieved": month_units,
            "product_pct": product_pct,
            "cash_target": ct,
            "cash_achieved": round(month_cash, 2),
            "payment_pct": payment_pct,
            "customer_target": cut,
            "customer_achieved": month_customers,
            "customer_pct": customer_pct,
            "overall": overall
        })

    return results

# ---------------------------- Views ----------------------------
@manager_dashboard_bp.route('/dashboard')
def manager_dashboard_view():
    """
    Manager dashboard:
    - Page shell + key KPIs render quickly.
    - Expense KPIs are lazy-loaded via /manager/dashboard/expenses (JSON) for speed.
    """
    if 'manager_id' not in session:
        flash("Access denied. Please log in as a manager.", "danger")
        return redirect(url_for('login.login'))

    manager_id = session['manager_id']
    manager = users_collection.find_one({"_id": ObjectId(manager_id)})
    if not manager:
        flash("Manager not found.", "error")
        return redirect(url_for('login.login'))

    manager_oid = ObjectId(manager_id)

    agent_progress_map, daily_totals = _daily_target_context(manager_id)

    # Executive targets summary (daily/weekly/monthly/yearly)
    manager_targets_summary = []
    for period in ("daily", "weekly", "monthly", "yearly"):
        summary = compute_manager_target_progress(manager_oid, period)
        if summary:
            manager_targets_summary.append(summary)

    # Monthly targets (still server-side; not as heavy as expense aggregation)
    results = calculate_monthly_targets(manager_id)

    # Payments & Attendance (Today) — relatively lightweight
    today = datetime.utcnow().date()
    today_str = today.isoformat()
    total_today_payment = 0.0
    attendance_data = []
    attended_customers_set = set()

    agents = list(users_collection.find(
        {"manager_id": ObjectId(manager_id), "role": "agent"},
        {"_id": 1, "name": 1, "image_url": 1}
    ))

    # Group once, keeping each agent's original payment order and float additions.
    # This avoids changing financial rounding while removing one query per agent.
    payment_groups = {row['_id']: row for row in payments_collection.aggregate([
        {'$match': {'agent_id': {'$in': [str(agent['_id']) for agent in agents]}, 'date': today_str, 'payment_type': {'$ne': 'WITHDRAWAL'}}},
        {'$group': {'_id': '$agent_id', 'payments': {'$push': {'amount': '$amount', 'customer_id': '$customer_id'}}}}
    ])}

    for agent in agents:
        agent_id = str(agent["_id"])
        target_info = agent_progress_map.get(agent_id, {})
        agent_payments = payment_groups.get(agent_id, {}).get('payments', [])
        agent_payment_count = 0
        for p in agent_payments:
            total_today_payment += float(p.get("amount", 0))
            agent_payment_count += 1
            cid = p.get("customer_id")
            if cid:
                attended_customers_set.add(str(cid))

        attendance_data.append({
            "name": agent.get("name", "Agent"),
            "image_url": agent.get("image_url", "https://via.placeholder.com/80"),
            "payment_count": agent_payment_count,
            "status": "Worked" if agent_payment_count >= 1 else "Absent",
            **target_info,
        })

    attended_customers_count = len(attended_customers_set)
    total_customer_count = customers_collection.count_documents({"manager_id": ObjectId(manager_id)})

    return render_template(
        "manager_dashboard.html",
        manager_name=manager.get("name", "Manager"),
        results=results,

        # payments
        today_total_payment=round(total_today_payment, 2),

        # attendance
        attended_customers_count=attended_customers_count,
        total_customer_count=total_customer_count,
        attendance_data=attendance_data,

        today=today_str,
        daily_target_totals=daily_totals,
        manager_targets_summary=manager_targets_summary
    )

@manager_dashboard_bp.route('/dashboard/expenses', methods=['GET'])
def manager_dashboard_expense_totals():
    """
    JSON endpoint to load expense KPIs after the dashboard renders.
    Returns Approved totals for today/week/month and Unapproved (pending) for the current month.
    """
    if 'manager_id' not in session:
        return jsonify(ok=False, message="Not authorized."), 401

    manager_id = session['manager_id']
    try:
        manager = users_collection.find_one({"_id": ObjectId(manager_id)})
    except Exception:
        manager = users_collection.find_one({"_id": manager_id})
    if not manager:
        return jsonify(ok=False, message="Manager not found."), 404

    today = datetime.utcnow().date()
    manager_id_str = str(manager["_id"])

    t_start, t_end = _today_range_utc(today)
    w_start, w_end = _week_range_utc(today)
    m_start, m_end = _month_range_utc(today)

    expense_today = _sum_manager_expenses(manager_id_str, t_start, t_end, status="Approved")
    expense_week  = _sum_manager_expenses(manager_id_str, w_start, w_end, status="Approved")
    expense_month = _sum_manager_expenses(manager_id_str, m_start, m_end, status="Approved")
    pending_count, pending_total = _pending_month_expenses(manager_id_str, m_start, m_end)

    return jsonify(
        ok=True,
        expense_today=round(expense_today, 2),
        expense_week=round(expense_week, 2),
        expense_month=round(expense_month, 2),
        expense_pending_count=int(pending_count),
        expense_pending_total=round(pending_total, 2)
    )
