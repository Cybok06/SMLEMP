from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from bson import ObjectId
from datetime import datetime, date, time, timezone, timedelta
from typing import Any

from db import db

executive_targets_analytics_bp = Blueprint("executive_targets_analytics", __name__)

executive_targets_col = db["executive_targets"]
users_col = db["users"]
payments_col = db["payments"]
customers_col = db["customers"]


def _ensure_indexes():
    try:
        executive_targets_col.create_index([("manager_id", 1), ("period", 1), ("is_active", 1)])
        executive_targets_col.create_index([("created_at", -1)])
        payments_col.create_index([("agent_id", 1), ("date", 1), ("payment_type", 1)])
        customers_col.create_index([("agent_id", 1), ("date_registered", 1)])
        customers_col.create_index([("purchases.purchase_date", 1)])
    except Exception:
        pass


_ensure_indexes()


def _require_executive() -> bool:
    role = (session.get("role") or "").lower()
    return bool(session.get("executive_id") or role == "executive")


def _utc_today_date() -> date:
    return datetime.now(timezone.utc).date()


def _period_window(period: str, base_date: date | None = None) -> dict[str, Any]:
    period = (period or "daily").lower()
    today = base_date or _utc_today_date()

    if period == "weekly":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "monthly":
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    elif period == "yearly":
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)
    else:
        start = today
        end = today

    return {
        "period": period,
        "start_date": start,
        "end_date": end,
        "start_str": start.isoformat(),
        "end_str": end.isoformat(),
        "start_dt": datetime.combine(start, time.min),
        "end_dt": datetime.combine(end, time.max),
    }


def _agent_id_sets(agent_ids: list[str]) -> tuple[list[str], list[ObjectId]]:
    oid_list: list[ObjectId] = []
    for aid in agent_ids:
        try:
            oid_list.append(ObjectId(aid))
        except Exception:
            continue
    return agent_ids, oid_list


def _agent_match(agent_ids: list[str], agent_oids: list[ObjectId]) -> dict[str, Any]:
    ors = []
    if agent_ids:
        ors.append({"agent_id": {"$in": agent_ids}})
    if agent_oids:
        ors.append({"agent_id": {"$in": agent_oids}})
    return {"$or": ors} if ors else {"agent_id": {"$in": []}}


def _percent(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round((part / whole) * 100, 2)


def _overall_pct(items: list[tuple[float, float]]) -> float:
    parts = 0
    total = 0.0
    for achieved, target in items:
        if target:
            parts += 1
            total += _percent(achieved, target)
    return round((total / parts) if parts else 0.0, 2)


def _aggregate_payments_by_agent(agent_ids: list[str], agent_oids: list[ObjectId], start_str: str, end_str: str):
    match = {
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": start_str, "$lte": end_str},
    }
    match.update(_agent_match(agent_ids, agent_oids))
    pipe = [
        {"$match": match},
        {"$group": {
            "_id": {"$toString": "$agent_id"},
            "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
        }},
    ]
    rows = payments_col.aggregate(pipe)
    return {str(r["_id"]): float(r.get("total") or 0.0) for r in rows}


def _aggregate_products_by_agent(agent_ids: list[str], agent_oids: list[ObjectId], start_str: str, end_str: str):
    match = _agent_match(agent_ids, agent_oids)
    match["purchases.purchase_date"] = {"$gte": start_str, "$lte": end_str}
    pipe = [
        {"$match": match},
        {"$unwind": "$purchases"},
        {"$match": {"purchases.purchase_date": {"$gte": start_str, "$lte": end_str}}},
        {"$group": {
            "_id": {"$toString": "$agent_id"},
            "units": {"$sum": {"$ifNull": ["$purchases.product.quantity", 1]}},
        }},
    ]
    rows = customers_col.aggregate(pipe)
    return {str(r["_id"]): int(r.get("units") or 0) for r in rows}


def _aggregate_customers_by_agent(agent_ids: list[str], agent_oids: list[ObjectId], start_dt: datetime, end_dt: datetime):
    match = _agent_match(agent_ids, agent_oids)
    match["date_registered"] = {"$gte": start_dt, "$lte": end_dt}
    pipe = [
        {"$match": match},
        {"$group": {"_id": {"$toString": "$agent_id"}, "count": {"$sum": 1}}},
    ]
    rows = customers_col.aggregate(pipe)
    return {str(r["_id"]): int(r.get("count") or 0) for r in rows}


def _status_badge(overall: float) -> str:
    if overall >= 80:
        return "On Track"
    if overall >= 50:
        return "At Risk"
    return "Off Track"


def _collect_active_targets(period: str):
    return list(executive_targets_col.find({"period": period, "is_active": True}))


def _manager_and_agent_maps(manager_ids: list[ObjectId]):
    managers = list(users_col.find({"_id": {"$in": manager_ids}}, {"name": 1, "branch": 1}))
    manager_map = {str(m["_id"]): m for m in managers}
    agents = list(users_col.find(
        {"role": "agent", "manager_id": {"$in": manager_ids}},
        {"_id": 1, "name": 1, "branch": 1, "manager_id": 1},
    ))
    agent_map = {str(a["_id"]): a for a in agents}
    return manager_map, agent_map, agents


def _compute_period_analytics(period: str, hall_window_days: int = 365) -> dict[str, Any]:
    win = _period_window(period)
    active_targets = _collect_active_targets(period)
    manager_ids = [t.get("manager_id") for t in active_targets if isinstance(t.get("manager_id"), ObjectId)]
    manager_ids = list({m for m in manager_ids})

    manager_map, agent_map, agents = _manager_and_agent_maps(manager_ids)
    agent_ids = [str(a["_id"]) for a in agents]
    agent_ids_str, agent_ids_oid = _agent_id_sets(agent_ids)

    payments_by_agent = _aggregate_payments_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    products_by_agent = _aggregate_products_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    customers_by_agent = _aggregate_customers_by_agent(agent_ids_str, agent_ids_oid, win["start_dt"], win["end_dt"])

    # KPI totals
    product_target_total = sum(int(t.get("product_target") or 0) for t in active_targets)
    cash_target_total = sum(float(t.get("cash_target") or 0.0) for t in active_targets)
    customer_target_total = sum(int(t.get("customer_target") or 0) for t in active_targets)

    products_achieved_total = sum(products_by_agent.values())
    cash_achieved_total = round(sum(payments_by_agent.values()), 2)
    customers_gained_total = sum(customers_by_agent.values())

    product_coverage_pct = _percent(products_achieved_total, product_target_total)
    cash_coverage_pct = _percent(cash_achieved_total, cash_target_total)
    customer_coverage_pct = _percent(customers_gained_total, customer_target_total)
    overall_coverage_pct = _overall_pct([
        (products_achieved_total, product_target_total),
        (cash_achieved_total, cash_target_total),
        (customers_gained_total, customer_target_total),
    ])

    # Managers breakdown
    managers_breakdown = []
    manager_totals_map: dict[str, dict[str, Any]] = {}
    for t in active_targets:
        mid = str(t.get("manager_id") or "")
        manager_doc = manager_map.get(mid) or {}
        manager_name = manager_doc.get("name") or "Manager"
        branch = manager_doc.get("branch") or "Unassigned"

        m_agents = [a for a in agents if str(a.get("manager_id")) == mid]
        m_agent_ids = [str(a["_id"]) for a in m_agents]

        prod_ach = sum(products_by_agent.get(aid, 0) for aid in m_agent_ids)
        cash_ach = round(sum(payments_by_agent.get(aid, 0.0) for aid in m_agent_ids), 2)
        cust_ach = sum(customers_by_agent.get(aid, 0) for aid in m_agent_ids)

        pt = int(t.get("product_target") or 0)
        ct = float(t.get("cash_target") or 0.0)
        cut = int(t.get("customer_target") or 0)

        overall = _overall_pct([(prod_ach, pt), (cash_ach, ct), (cust_ach, cut)])
        manager_row = {
            "manager_id": mid,
            "manager_name": manager_name,
            "branch": branch,
            "targets": {"product": pt, "cash": ct, "customer": cut},
            "achieved": {"product": prod_ach, "cash": cash_ach, "customer": cust_ach},
            "coverage": {
                "product_pct": _percent(prod_ach, pt),
                "cash_pct": _percent(cash_ach, ct),
                "customer_pct": _percent(cust_ach, cut),
                "overall_pct": overall,
            },
            "status": _status_badge(overall),
        }
        managers_breakdown.append(manager_row)
        manager_totals_map[mid] = manager_row

    # Branch breakdown
    branch_map: dict[str, dict[str, Any]] = {}
    for row in managers_breakdown:
        b = row["branch"] or "Unassigned"
        entry = branch_map.setdefault(b, {
            "branch": b,
            "targets": {"product": 0, "cash": 0.0, "customer": 0},
            "achieved": {"product": 0, "cash": 0.0, "customer": 0},
        })
        entry["targets"]["product"] += row["targets"]["product"]
        entry["targets"]["cash"] += row["targets"]["cash"]
        entry["targets"]["customer"] += row["targets"]["customer"]
        entry["achieved"]["product"] += row["achieved"]["product"]
        entry["achieved"]["cash"] += row["achieved"]["cash"]
        entry["achieved"]["customer"] += row["achieved"]["customer"]

    branches_breakdown = []
    for b, entry in branch_map.items():
        overall = _overall_pct([
            (entry["achieved"]["product"], entry["targets"]["product"]),
            (entry["achieved"]["cash"], entry["targets"]["cash"]),
            (entry["achieved"]["customer"], entry["targets"]["customer"]),
        ])
        branches_breakdown.append({
            "branch": b,
            "targets": entry["targets"],
            "achieved": entry["achieved"],
            "overall_pct": overall,
        })
    branches_breakdown.sort(key=lambda r: r["overall_pct"], reverse=True)

    # Agents leaderboard
    agents_from_allocs = {}
    for t in active_targets:
        for a in (t.get("agent_allocations") or []):
            aid = str(a.get("agent_id") or "")
            if not aid:
                continue
            agents_from_allocs[aid] = a

    agents_rows = []
    for aid, alloc in agents_from_allocs.items():
        agent_doc = agent_map.get(aid, {})
        branch = agent_doc.get("branch") or "Unassigned"
        prod_quota = int(alloc.get("product_quota") or 0)
        cash_quota = float(alloc.get("cash_quota") or 0.0)
        cust_quota = int(alloc.get("customer_quota") or 0)
        prod_ach = int(products_by_agent.get(aid, 0))
        cash_ach = round(float(payments_by_agent.get(aid, 0.0)), 2)
        cust_ach = int(customers_by_agent.get(aid, 0))
        overall = _overall_pct([(prod_ach, prod_quota), (cash_ach, cash_quota), (cust_ach, cust_quota)])
        agents_rows.append({
            "agent_id": aid,
            "agent_name": alloc.get("agent_name") or agent_doc.get("name") or "Agent",
            "branch": branch,
            "quota": {"product": prod_quota, "cash": cash_quota, "customer": cust_quota},
            "achieved": {"product": prod_ach, "cash": cash_ach, "customer": cust_ach},
            "coverage": {
                "product_pct": _percent(prod_ach, prod_quota),
                "cash_pct": _percent(cash_ach, cash_quota),
                "customer_pct": _percent(cust_ach, cust_quota),
                "overall_pct": overall,
            },
        })

    agents_rows.sort(key=lambda r: r["coverage"]["overall_pct"], reverse=True)
    agents_top = agents_rows[:10]

    # Hall of Fame (history)
    hall_window_days = max(30, min(1095, int(hall_window_days or 365)))
    since_dt = datetime.now(timezone.utc) - timedelta(days=hall_window_days)

    hist_targets = list(executive_targets_col.find(
        {"created_at": {"$gte": since_dt}}
    ).sort("created_at", -1).limit(200))

    hall_hits: dict[str, dict[str, Any]] = {}
    hall_recent: list[dict[str, Any]] = []

    for t in hist_targets:
        created_at = t.get("created_at")
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        base_date = created_at.date()
        period_doc = t.get("period") or "daily"
        doc_win = _period_window(period_doc, base_date=base_date)

        allocs = t.get("agent_allocations") or []
        doc_agent_ids = [str(a.get("agent_id")) for a in allocs if a.get("agent_id")]
        doc_agent_ids_str, doc_agent_ids_oid = _agent_id_sets(doc_agent_ids)

        doc_pay = _aggregate_payments_by_agent(doc_agent_ids_str, doc_agent_ids_oid, doc_win["start_str"], doc_win["end_str"])
        doc_prod = _aggregate_products_by_agent(doc_agent_ids_str, doc_agent_ids_oid, doc_win["start_str"], doc_win["end_str"])
        doc_cust = _aggregate_customers_by_agent(doc_agent_ids_str, doc_agent_ids_oid, doc_win["start_dt"], doc_win["end_dt"])

        date_context = doc_win["start_str"]
        if period_doc == "monthly":
            date_context = doc_win["start_str"][:7]
        elif period_doc == "yearly":
            date_context = doc_win["start_str"][:4]

        for alloc in allocs:
            aid = str(alloc.get("agent_id") or "")
            if not aid:
                continue
            prod_quota = int(alloc.get("product_quota") or 0)
            cash_quota = float(alloc.get("cash_quota") or 0.0)
            cust_quota = int(alloc.get("customer_quota") or 0)
            prod_ach = int(doc_prod.get(aid, 0))
            cash_ach = round(float(doc_pay.get(aid, 0.0)), 2)
            cust_ach = int(doc_cust.get(aid, 0))
            prod_pct = _percent(prod_ach, prod_quota)
            cash_pct = _percent(cash_ach, cash_quota)
            cust_pct = _percent(cust_ach, cust_quota)
            overall = _overall_pct([(prod_ach, prod_quota), (cash_ach, cash_quota), (cust_ach, cust_quota)])

            hit_metrics = sum(1 for v in (prod_pct, cash_pct, cust_pct) if v >= 100)
            hit = overall >= 100 or hit_metrics >= 2
            if not hit:
                continue

            agent_doc = agent_map.get(aid) or {}
            branch = agent_doc.get("branch") or "Unassigned"
            record = hall_hits.setdefault(aid, {
                "agent_id": aid,
                "agent_name": alloc.get("agent_name") or agent_doc.get("name") or "Agent",
                "branch": branch,
                "count_hits": 0,
                "best_overall_pct": 0.0,
                "period": period_doc,
                "last_context": "",
            })
            record["count_hits"] += 1
            record["best_overall_pct"] = max(record["best_overall_pct"], overall)
            record["period"] = period_doc
            record["last_context"] = date_context

            hall_recent.append({
                "agent_id": aid,
                "agent_name": record["agent_name"],
                "branch": branch,
                "period": period_doc,
                "date_context": date_context,
                "overall_pct": overall,
            })

    hall_of_fame = sorted(hall_hits.values(), key=lambda r: (r["count_hits"], r["best_overall_pct"]), reverse=True)
    hall_recent = hall_recent[:10]

    # Insights
    insights = []
    if branches_breakdown:
        best_branch = branches_breakdown[0]
        insights.append({
            "title": "Best performing branch",
            "value": best_branch["branch"],
            "detail": f"{best_branch['overall_pct']}% overall coverage",
        })

    # Most improved manager (simple delta vs previous period)
    if managers_breakdown:
        prev_period = None
        base_date = _utc_today_date()
        if period == "daily":
            prev_period = _period_window("daily", base_date=base_date - timedelta(days=1))
        elif period == "weekly":
            prev_period = _period_window("weekly", base_date=base_date - timedelta(days=7))
        elif period == "monthly":
            prev_month = (base_date.replace(day=1) - timedelta(days=1)).replace(day=1)
            prev_period = _period_window("monthly", base_date=prev_month)
        elif period == "yearly":
            prev_year = base_date.replace(year=base_date.year - 1, month=1, day=1)
            prev_period = _period_window("yearly", base_date=prev_year)

        if prev_period:
            prev_pay = _aggregate_payments_by_agent(agent_ids_str, agent_ids_oid, prev_period["start_str"], prev_period["end_str"])
            prev_prod = _aggregate_products_by_agent(agent_ids_str, agent_ids_oid, prev_period["start_str"], prev_period["end_str"])
            prev_cust = _aggregate_customers_by_agent(agent_ids_str, agent_ids_oid, prev_period["start_dt"], prev_period["end_dt"])

            best_delta = None
            best_mgr = None
            for row in managers_breakdown:
                mid = row["manager_id"]
                m_agents = [a for a in agents if str(a.get("manager_id")) == mid]
                m_agent_ids = [str(a["_id"]) for a in m_agents]
                prev_prod_ach = sum(prev_prod.get(aid, 0) for aid in m_agent_ids)
                prev_cash_ach = sum(prev_pay.get(aid, 0.0) for aid in m_agent_ids)
                prev_cust_ach = sum(prev_cust.get(aid, 0) for aid in m_agent_ids)

                prev_overall = _overall_pct([
                    (prev_prod_ach, row["targets"]["product"]),
                    (prev_cash_ach, row["targets"]["cash"]),
                    (prev_cust_ach, row["targets"]["customer"]),
                ])
                delta = row["coverage"]["overall_pct"] - prev_overall
                if best_delta is None or delta > best_delta:
                    best_delta = delta
                    best_mgr = (row["manager_name"], delta)

            if best_mgr and best_delta is not None:
                insights.append({
                    "title": "Most improved manager",
                    "value": best_mgr[0],
                    "detail": f"+{round(best_delta, 2)} pts vs previous period",
                })

    # Missed target risk
    if period in ("weekly", "monthly", "yearly"):
        today = _utc_today_date()
        total_days = (win["end_date"] - win["start_date"]).days + 1
        elapsed = (today - win["start_date"]).days + 1
        elapsed_pct = (elapsed / total_days * 100) if total_days else 0
        if elapsed_pct >= 70:
            risky = [
                r for r in managers_breakdown
                if r["coverage"]["cash_pct"] < 50
            ]
            if risky:
                insights.append({
                    "title": "Missed target risk",
                    "value": f"{len(risky)} managers",
                    "detail": "Cash coverage < 50% with >70% of period elapsed",
                })

    # Consistency score (daily cash)
    if period == "daily":
        daily_targets = _collect_active_targets("daily")
        daily_agents = {}
        for t in daily_targets:
            for a in (t.get("agent_allocations") or []):
                aid = str(a.get("agent_id") or "")
                if aid:
                    daily_agents[aid] = a

        if daily_agents:
            days = [(win["start_date"] - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            day_scores = {aid: 0 for aid in daily_agents.keys()}
            for day_str in days:
                day_pay = _aggregate_payments_by_agent(list(daily_agents.keys()), [], day_str, day_str)
                for aid, alloc in daily_agents.items():
                    quota = float(alloc.get("cash_quota") or 0.0)
                    if quota <= 0:
                        continue
                    achieved = float(day_pay.get(aid, 0.0))
                    if achieved / quota >= 0.8:
                        day_scores[aid] += 1

            if day_scores:
                top_agent_id = max(day_scores.keys(), key=lambda k: day_scores[k])
                agent_doc = agent_map.get(top_agent_id) or {}
                insights.append({
                    "title": "Consistency leader (last 7 days)",
                    "value": agent_doc.get("name") or "Agent",
                    "detail": f"{day_scores[top_agent_id]} / 7 days above 80% cash quota",
                })

    return {
        "period": period,
        "window": {"start": win["start_str"], "end": win["end_str"]},
        "kpis": {
            "managers_with_targets": len(manager_ids),
            "agents_total": len(agent_ids),
            "overall_coverage_pct": overall_coverage_pct,
            "product_target_total": product_target_total,
            "cash_target_total": cash_target_total,
            "customer_target_total": customer_target_total,
            "products_achieved_total": products_achieved_total,
            "cash_achieved_total": cash_achieved_total,
            "customers_gained_total": customers_gained_total,
            "product_coverage_pct": product_coverage_pct,
            "cash_coverage_pct": cash_coverage_pct,
            "customer_coverage_pct": customer_coverage_pct,
        },
        "managers": managers_breakdown,
        "branches": branches_breakdown,
        "agents_top": agents_top,
        "agents_all": agents_rows,
        "agents_all_count": len(agents_rows),
        "hall_of_fame": hall_of_fame[:20],
        "hall_recent": hall_recent,
        "insights": insights,
    }


@executive_targets_analytics_bp.get("/executive/targets/analytics")
def executive_targets_analytics_page():
    if not _require_executive():
        return redirect(url_for("login.login"))

    period = (request.args.get("period") or "daily").lower()
    if period not in ("daily", "weekly", "monthly", "yearly"):
        period = "daily"
    hall_window = request.args.get("hall_window") or "365"

    data = _compute_period_analytics(period, hall_window_days=int(hall_window))
    return render_template(
        "executive/targets_analytics.html",
        data=data,
        period=period,
        hall_window=hall_window,
    )


@executive_targets_analytics_bp.get("/executive/targets/analytics/data")
def executive_targets_analytics_data():
    if not _require_executive():
        return jsonify(ok=False, message="Unauthorized"), 401

    period = (request.args.get("period") or "daily").lower()
    if period not in ("daily", "weekly", "monthly", "yearly"):
        period = "daily"
    hall_window = request.args.get("hall_window") or "365"

    data = _compute_period_analytics(period, hall_window_days=int(hall_window))
    return jsonify(ok=True, data=data)
