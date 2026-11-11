from __future__ import annotations

from services.request_memo import request_memo

from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
from bson import ObjectId
from datetime import datetime, date, time, timezone, timedelta
from typing import Any
from decimal import Decimal, InvalidOperation
from services.target_cash import CHANNELS, collections_by_agent, extend_summary
import secrets

from db import db

executive_target_bp = Blueprint("executive_target", __name__)

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
        customers_col.create_index([("purchases.agent_id", 1)])
    except Exception:
        pass


_ensure_indexes()


def _require_executive() -> bool:
    role = (session.get("role") or "").lower()
    return bool(session.get("executive_id") or role == "executive")


def _utc_today_date() -> date:
    return datetime.now(timezone.utc).date()


def _period_window(period: str) -> dict[str, Any]:
    period = (period or "").lower()
    today = _utc_today_date()

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

    start_str = start.isoformat()
    end_str = end.isoformat()
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)

    return {
        "period": period or "daily",
        "start_date": start,
        "end_date": end,
        "start_str": start_str,
        "end_str": end_str,
        "start_dt": start_dt,
        "end_dt": end_dt,
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


def parse_target_value(value, *, cash=False):
    try:
        number = Decimal(str(value))
        precision = Decimal("0.01") if cash else Decimal("1")
        if not number.is_finite() or number < 0 or number != number.quantize(precision):
            raise ValueError("Targets must be nonnegative whole units or cash with at most two decimals.")
        return number
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid target amount.")


def _allocation_pending(target):
    return any(
        sum(Decimal(str(a.get(c + '_quota') or 0)) for a in target.get('agent_allocations') or [])
        != Decimal(str(target.get(c + '_target') or 0))
        for c in ('product', 'cash', 'customer') + CHANNELS
    )


def _get_manager_agents(manager_oid: ObjectId) -> list[dict[str, str]]:
    cursor = users_col.find(
        {"role": "agent", "manager_id": manager_oid},
        {"_id": 1, "name": 1},
    )
    agents: list[dict[str, str]] = []
    for doc in cursor:
        agents.append({"id": str(doc.get("_id")), "name": doc.get("name") or "Agent"})
    return agents


@request_memo
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


@request_memo
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


@request_memo
def _aggregate_customers_by_agent(agent_ids: list[str], agent_oids: list[ObjectId], start_dt: datetime, end_dt: datetime):
    match = _agent_match(agent_ids, agent_oids)
    match["date_registered"] = {"$gte": start_dt, "$lte": end_dt}

    pipe = [
        {"$match": match},
        {"$group": {"_id": {"$toString": "$agent_id"}, "count": {"$sum": 1}}},
    ]
    rows = customers_col.aggregate(pipe)
    return {str(r["_id"]): int(r.get("count") or 0) for r in rows}


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


@request_memo
def _get_active_target(manager_oid: ObjectId, period: str) -> dict[str, Any] | None:
    return executive_targets_col.find_one({
        "manager_id": manager_oid,
        "period": period,
        "is_active": True,
    })


@request_memo
def compute_manager_target_progress(manager_oid: ObjectId, period: str) -> dict[str, Any] | None:
    target = _get_active_target(manager_oid, period)
    if not target:
        return None

    agents = _get_manager_agents(manager_oid)
    agent_ids = [a["id"] for a in agents]
    agent_ids_str, agent_ids_oid = _agent_id_sets(agent_ids)

    win = _period_window(period)

    cash_channels = collections_by_agent(payments_col, agent_ids_str, win['start_str'], win['end_str'])
    payments_by_agent = _aggregate_payments_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    products_by_agent = _aggregate_products_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    customers_by_agent = _aggregate_customers_by_agent(agent_ids_str, agent_ids_oid, win["start_dt"], win["end_dt"])

    allocations = target.get("agent_allocations") or []
    per_agent = []
    for alloc in allocations:
        aid = str(alloc.get("agent_id") or "")
        product_quota = int(alloc.get("product_quota") or 0)
        cash_quota = float(alloc.get("cash_quota") or 0.0)
        customer_quota = int(alloc.get("customer_quota") or 0)
        product_achieved = int(products_by_agent.get(aid, 0))
        cash_achieved = round(float(payments_by_agent.get(aid, 0.0)), 2)
        customer_achieved = int(customers_by_agent.get(aid, 0))
        per_agent.append({
            "agent_id": aid,
            "agent_name": alloc.get("agent_name") or "Agent",
            "product_quota": product_quota,
            "cash_quota": cash_quota,
            "customer_quota": customer_quota,
            "product_achieved": product_achieved,
            "cash_achieved": cash_achieved,
            "customer_achieved": customer_achieved,
            "product_pct": _percent(product_achieved, product_quota),
            "cash_pct": _percent(cash_achieved, cash_quota),
            "customer_pct": _percent(customer_achieved, customer_quota),
            "overall_pct": _overall_pct([
                (product_achieved, product_quota),
                (cash_achieved, cash_quota),
                (customer_achieved, customer_quota),
            ]),
        })

    product_target = int(target.get("product_target") or 0)
    cash_target = float(target.get("cash_target") or 0.0)
    customer_target = int(target.get("customer_target") or 0)

    product_achieved = sum(products_by_agent.values())
    cash_achieved = round(sum(payments_by_agent.values()), 2)
    customer_achieved = sum(customers_by_agent.values())

    summary = {
        "period": period,
        "start_date": win["start_str"],
        "end_date": win["end_str"],
        "targets": {
            "product_target": product_target,
            "cash_target": cash_target,
            "customer_target": customer_target,
        },
        "achieved": {
            "product_achieved": product_achieved,
            "cash_achieved": cash_achieved,
            "customer_achieved": customer_achieved,
        },
        "percents": {
            "product_pct": _percent(product_achieved, product_target),
            "cash_pct": _percent(cash_achieved, cash_target),
            "customer_pct": _percent(customer_achieved, customer_target),
            "overall_pct": _overall_pct([
                (product_achieved, product_target),
                (cash_achieved, cash_target),
                (customer_achieved, customer_target),
            ]),
        },
        "agents": per_agent,
        "allocation_pending": _allocation_pending(target),
        "target_id": str(target.get("_id")),
        "is_active": bool(target.get("is_active")),
    }
    for row, alloc in zip(per_agent, allocations):
        extra = extend_summary({'quota': {}, 'achieved': {}, 'percents': {}}, alloc, cash_channels.get(row['agent_id'], {}), quota=True)
        row.update(extra['quota'])
        row.update(extra['achieved'])
        row.update({key: value for key, value in extra['percents'].items() if key != 'overall_pct'})
        row['overall_pct'] = _overall_pct([(row.get(c + '_achieved', 0), row.get(c + '_quota', 0)) for c in ('product', 'cash', 'customer') + CHANNELS])
    collected = {channel: sum(row.get(channel, 0) for row in cash_channels.values()) for channel in CHANNELS}
    return extend_summary(summary, target, collected)


@request_memo
def compute_agent_target_progress(agent_id: str, manager_oid: ObjectId, period: str) -> dict[str, Any] | None:
    target = _get_active_target(manager_oid, period)
    if not target:
        return None

    alloc = None
    for row in (target.get("agent_allocations") or []):
        if str(row.get("agent_id")) == str(agent_id):
            alloc = row
            break

    if not alloc:
        return None

    win = _period_window(period)
    agent_ids_str, agent_ids_oid = _agent_id_sets([agent_id])

    payments_by_agent = _aggregate_payments_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    products_by_agent = _aggregate_products_by_agent(agent_ids_str, agent_ids_oid, win["start_str"], win["end_str"])
    customers_by_agent = _aggregate_customers_by_agent(agent_ids_str, agent_ids_oid, win["start_dt"], win["end_dt"])

    product_quota = int(alloc.get("product_quota") or 0)
    cash_quota = float(alloc.get("cash_quota") or 0.0)
    customer_quota = int(alloc.get("customer_quota") or 0)

    product_achieved = int(products_by_agent.get(agent_id, 0))
    cash_achieved = round(float(payments_by_agent.get(agent_id, 0.0)), 2)
    customer_achieved = int(customers_by_agent.get(agent_id, 0))

    summary = {
        "period": period,
        "start_date": win["start_str"],
        "end_date": win["end_str"],
        "quota": {
            "product_quota": product_quota,
            "cash_quota": cash_quota,
            "customer_quota": customer_quota,
        },
        "achieved": {
            "product_achieved": product_achieved,
            "cash_achieved": cash_achieved,
            "customer_achieved": customer_achieved,
        },
        "percents": {
            "product_pct": _percent(product_achieved, product_quota),
            "cash_pct": _percent(cash_achieved, cash_quota),
            "customer_pct": _percent(customer_achieved, customer_quota),
            "overall_pct": _overall_pct([
                (product_achieved, product_quota),
                (cash_achieved, cash_quota),
                (customer_achieved, customer_quota),
            ]),
        },
        "agent_name": alloc.get("agent_name") or "Agent",
        "target_id": str(target.get("_id")),
        "is_active": bool(target.get("is_active")),
    }
    channels = collections_by_agent(payments_col, [agent_id], win['start_str'], win['end_str'])
    return extend_summary(summary, alloc, channels.get(agent_id, {}), quota=True)


@executive_target_bp.get("/executive/targets")
def executive_targets_page():
    if not _require_executive():
        flash("Unauthorized", "danger")
        return redirect(url_for("login.login"))

    session.setdefault('executive_targets_csrf', secrets.token_urlsafe(32))
    managers = list(users_col.find({"role": "manager"}, {"_id": 1, "name": 1}).sort("name", 1))
    manager_map = {str(m["_id"]): (m.get("name") or "Manager") for m in managers}

    targets = list(executive_targets_col.find({}).sort("created_at", -1).limit(200))
    for t in targets:
        t["id"] = str(t.get("_id"))
        mid = str(t.get("manager_id") or "")
        t["manager_name"] = manager_map.get(mid, "Manager")
        t["agent_count"] = len(t.get("agent_allocations") or [])
        t['allocation_pending'] = _allocation_pending(t)
        created_at = t.get("created_at")
        if isinstance(created_at, datetime):
            t["created_at_str"] = created_at.strftime("%Y-%m-%d %H:%M")
        else:
            t["created_at_str"] = ""

    return render_template(
        "executive/targets.html",
        managers=managers,
        targets=targets,
    )


@executive_target_bp.post("/executive/targets/create")
def executive_targets_create():
    if not _require_executive():
        return jsonify(ok=False, message="Unauthorized"), 401

    manager_id = (request.form.get("manager_id") or "").strip()
    period = (request.form.get("period") or "daily").strip().lower()
    try:
        manager_oid = ObjectId(manager_id)
    except Exception:
        return jsonify(ok=False, message="Invalid manager id"), 400

    manager = users_col.find_one({"_id": manager_oid, "role": "manager"})
    if not manager:
        return jsonify(ok=False, message="Manager not found"), 404

    if period not in ("daily", "weekly", "monthly", "yearly"):
        return jsonify(ok=False, message="Invalid period"), 400
    try:
        product_target = int(parse_target_value(request.form.get("product_target")))
        cash_target = float(parse_target_value(request.form.get("cash_target"), cash=True))
        customer_target = int(parse_target_value(request.form.get("customer_target")))
        channel_targets = {c + '_target': float(parse_target_value(request.form.get(c + '_target', '0'), cash=True)) for c in CHANNELS}
        if not (product_target or cash_target or customer_target or any(channel_targets.values())):
            raise ValueError("Set at least one positive target.")
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400

    now = datetime.utcnow()

    executive_id = str(session.get("executive_id") or session.get("admin_id") or "")

    executive_targets_col.update_many(
        {"manager_id": manager_oid, "period": period, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now}},
    )

    doc = {
        "manager_id": manager_oid,
        "period": period,
        "product_target": product_target,
        "cash_target": cash_target,
        "customer_target": customer_target,
        **channel_targets,
        "distribution_mode": "manual",
        "agent_allocations": [],
        "created_by": executive_id,
        "created_at": now,
        "updated_at": now,
        "is_active": True,
    }

    executive_targets_col.insert_one(doc)
    return redirect(url_for("executive_target.executive_targets_page"))


@executive_target_bp.post('/executive/targets/update/<target_id>')
def executive_targets_update(target_id):
    if not _require_executive():
        return jsonify(ok=False, message='Unauthorized'), 401
    token = session.get('executive_targets_csrf', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf_token', '')):
        return jsonify(ok=False, message='Invalid form token. Reload the targets page.'), 400
    if not ObjectId.is_valid(target_id):
        return jsonify(ok=False, message='Invalid target id'), 400
    target = executive_targets_col.find_one({'_id': ObjectId(target_id)})
    if not target:
        return jsonify(ok=False, message='Target not found'), 404
    try:
        fields = ('product', 'cash', 'customer') + CHANNELS
        reset_allocations = request.form.get('reset_allocations') == 'yes'
        values = {c + '_target': parse_target_value(request.form.get(c + '_target', target.get(c + '_target', 0)), cash=c in ('cash',) + CHANNELS) for c in fields}
        if not any(values.values()):
            raise ValueError('Set at least one positive target.')
        for channel in fields:
            allocated = sum(Decimal(str(a.get(channel + '_quota') or 0)) for a in target.get('agent_allocations') or [])
            if allocated > values[channel + '_target'] and not reset_allocations:
                raise ValueError('This amount is below existing agent allocations. Select Clear agent allocations so the manager can redistribute the revised targets.')
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    changes = {key: float(value) if key in ('cash_target', 'product_cash_target', 'susu_cash_target') else int(value) for key, value in values.items()}
    if reset_allocations:
        changes['agent_allocations'] = []
    now = datetime.utcnow()
    executive_targets_col.update_one({'_id': target['_id']}, {'$set': {**changes, 'updated_at': now}, '$push': {
        'change_history': {'changed_at': now, 'changed_by': str(session.get('executive_id') or session.get('user_id') or ''),
                           'before': {key: target.get(key, [] if key == 'agent_allocations' else 0) for key in changes}, 'after': changes}}})
    flash('Target updated. Managers can update agent allocations for the revised amounts.', 'success')
    return redirect(url_for('executive_target.executive_targets_page'))


@executive_target_bp.get('/executive/targets/cash-progress')
def executive_cash_target_progress():
    if not _require_executive():
        return jsonify(ok=False, message='Unauthorized'), 401
    period = request.args.get('period', 'daily')
    if period not in ('daily', 'weekly', 'monthly', 'yearly'):
        return jsonify(ok=False, message='Invalid period'), 400
    totals = {key: 0.0 for c in ('cash',) + CHANNELS for key in (c + '_target', c + '_achieved')}
    for target in executive_targets_col.find({'period': period, 'is_active': True}):
        summary = compute_manager_target_progress(target['manager_id'], period)
        if not summary:
            continue
        for c in ('cash',) + CHANNELS:
            totals[c + '_target'] += float(summary['targets'].get(c + '_target') or 0)
            totals[c + '_achieved'] += float(summary['achieved'].get(c + '_achieved') or 0)
    return jsonify(ok=True, period=period, totals={key: round(value, 2) for key, value in totals.items()})


@executive_target_bp.post("/executive/targets/rebuild/<target_id>")
def executive_targets_rebuild(target_id: str):
    if not _require_executive():
        return jsonify(ok=False, message="Unauthorized"), 401

    return jsonify(ok=False, message="Managers must distribute targets manually."), 409


@executive_target_bp.post("/executive/targets/toggle/<target_id>")
def executive_targets_toggle(target_id: str):
    if not _require_executive():
        return jsonify(ok=False, message="Unauthorized"), 401

    try:
        target_oid = ObjectId(target_id)
    except Exception:
        return jsonify(ok=False, message="Invalid target id"), 400

    target = executive_targets_col.find_one({"_id": target_oid})
    if not target:
        return jsonify(ok=False, message="Target not found"), 404

    is_active = bool(target.get("is_active"))
    manager_oid = target.get("manager_id")
    period = target.get("period")

    if not is_active:
        executive_targets_col.update_many(
            {"manager_id": manager_oid, "period": period, "is_active": True},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )

    executive_targets_col.update_one(
        {"_id": target_oid},
        {"$set": {"is_active": (not is_active), "updated_at": datetime.utcnow()}},
    )
    return redirect(url_for("executive_target.executive_targets_page"))


@executive_target_bp.get("/executive/targets/progress")
def executive_targets_progress():
    if not _require_executive():
        return jsonify(ok=False, message="Unauthorized"), 401

    manager_id = (request.args.get("manager_id") or "").strip()
    period = (request.args.get("period") or "daily").strip().lower()

    try:
        manager_oid = ObjectId(manager_id)
    except Exception:
        return jsonify(ok=False, message="Invalid manager id"), 400

    summary = compute_manager_target_progress(manager_oid, period)
    if not summary:
        return jsonify(ok=False, message="No active target found.")

    return jsonify(ok=True, summary=summary)
