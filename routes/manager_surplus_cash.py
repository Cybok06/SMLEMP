from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import re

from db import db

manager_surplus_cash_bp = Blueprint("manager_surplus_cash", __name__, url_prefix="/manager/surplus-cash")

users_col = db["users"]
surplus_col = db["surplus_cash"]


def _ensure_indexes():
    try:
        surplus_col.create_index([("manager_id", 1), ("date", -1)])
        surplus_col.create_index([("agent_id", 1), ("date", -1)])
        surplus_col.create_index([("branch", 1), ("date", -1)])
    except Exception:
        pass


_ensure_indexes()


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _current_manager_session() -> Tuple[Optional[str], Optional[str]]:
    if session.get("manager_id"):
        return "manager_id", session["manager_id"]
    return None, None


def _ensure_manager_scope_or_redirect():
    key, uid = _current_manager_session()
    if not uid:
        return redirect(url_for("login.login"))

    try:
        manager_doc = users_col.find_one({"_id": ObjectId(uid)})
    except Exception:
        manager_doc = users_col.find_one({"_id": uid})

    if not manager_doc:
        return redirect(url_for("login.login"))

    role = (manager_doc.get("role") or "").lower()
    if role != "manager":
        return redirect(url_for("login.login"))

    return str(manager_doc["_id"]), manager_doc


def _agents_under_manager(manager_id_str: str) -> List[Dict[str, Any]]:
    try:
        m_oid = ObjectId(manager_id_str)
        match = {"role": "agent", "$or": [{"manager_id": m_oid}, {"manager_id": manager_id_str}]}
    except Exception:
        match = {"role": "agent", "manager_id": manager_id_str}

    agents = list(users_col.find(match, {"name": 1, "username": 1, "phone": 1, "branch": 1}))
    out = []
    for a in agents:
        out.append({
            "id": str(a.get("_id")),
            "name": a.get("name") or a.get("username") or "Agent",
            "phone": a.get("phone", ""),
            "branch": a.get("branch", "")
        })
    return out


def _parse_date_range(args) -> Tuple[datetime, datetime, str, str]:
    start_str = (args.get("start") or "").strip()
    end_str = (args.get("end") or "").strip()
    now = datetime.utcnow()
    start = now - timedelta(days=30)
    end = now
    if start_str:
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d")
        except Exception:
            pass
    if end_str:
        try:
            end = datetime.strptime(end_str, "%Y-%m-%d")
        except Exception:
            pass
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _sum_amount(match: Dict[str, Any]) -> float:
    rows = list(surplus_col.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}}
    ]))
    if not rows:
        return 0.0
    try:
        return float(rows[0].get("total", 0.0))
    except Exception:
        return 0.0


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_sort(sort_key: str) -> List[Tuple[str, int]]:
    sort_key = (sort_key or "newest").lower()
    if sort_key == "oldest":
        return [("created_at", 1)]
    if sort_key == "amount_desc":
        return [("amount", -1), ("created_at", -1)]
    if sort_key == "amount_asc":
        return [("amount", 1), ("created_at", -1)]
    return [("created_at", -1)]


@manager_surplus_cash_bp.route("/", methods=["GET"])
def manager_surplus_cash_page():
    scope = _ensure_manager_scope_or_redirect()
    if not isinstance(scope, tuple):
        return scope
    manager_id, manager_doc = scope

    agents = _agents_under_manager(manager_id)
    today = _today_str()

    return render_template(
        "manager_surplus_cash.html",
        agents=agents,
        today=today,
        manager_name=manager_doc.get("name", "Manager"),
    )


@manager_surplus_cash_bp.route("/record", methods=["POST"])
def manager_surplus_cash_record():
    scope = _ensure_manager_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    manager_id, manager_doc = scope

    agent_id = (request.form.get("agent_id") or (request.json or {}).get("agent_id") or "").strip()
    amount_raw = request.form.get("amount") or (request.json or {}).get("amount")
    note = (request.form.get("note") or (request.json or {}).get("note") or "").strip()
    date_str = (request.form.get("date") or (request.json or {}).get("date") or _today_str()).strip()

    try:
        amount = float(amount_raw)
    except Exception:
        amount = 0.0

    if not agent_id or amount <= 0:
        return jsonify(ok=False, message="Agent and amount are required."), 400

    # validate agent belongs to manager
    try:
        m_oid = ObjectId(manager_id)
        agent_doc = users_col.find_one({"_id": ObjectId(agent_id), "role": "agent", "$or": [{"manager_id": m_oid}, {"manager_id": manager_id}]})
    except Exception:
        agent_doc = users_col.find_one({"_id": agent_id, "role": "agent", "manager_id": manager_id})

    if not agent_doc:
        return jsonify(ok=False, message="Agent not found or not in your team."), 404

    now_utc = datetime.utcnow()
    time_str = now_utc.strftime("%H:%M:%S")

    surplus_doc = {
        "amount": float(amount),
        "note": note,
        "agent_id": str(agent_doc.get("_id")),
        "agent_name": agent_doc.get("name") or agent_doc.get("username") or "Agent",
        "agent_phone": agent_doc.get("phone") or "",
        "manager_id": str(manager_doc.get("_id")),
        "manager_name": manager_doc.get("name") or "Manager",
        "branch": agent_doc.get("branch") or "",
        "recorded_by_role": "manager",
        "recorded_by_id": str(manager_doc.get("_id")),
        "recorded_by_name": manager_doc.get("name") or "Manager",
        "date": date_str,
        "time": time_str,
        "created_at": now_utc,
        "updated_at": now_utc,
    }
    surplus_col.insert_one(surplus_doc)

    return jsonify(ok=True, message="Surplus recorded.")


@manager_surplus_cash_bp.route("/api/metrics", methods=["GET"])
def manager_surplus_cash_metrics():
    scope = _ensure_manager_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    manager_id, _ = scope

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    agent_id = (request.args.get("agent_id") or "").strip()

    base_match = {"manager_id": manager_id}
    if agent_id:
        base_match["agent_id"] = agent_id

    total_all = _sum_amount(base_match)

    today = _today_str()
    start_week = (datetime.utcnow().date() - timedelta(days=datetime.utcnow().date().weekday())).strftime("%Y-%m-%d")
    start_month = datetime.utcnow().date().replace(day=1).strftime("%Y-%m-%d")

    def _sum_range(start_s, end_s):
        m = dict(base_match)
        m["date"] = {"$gte": start_s, "$lte": end_s}
        return _sum_amount(m)

    totals = {
        "total": total_all,
        "today": _sum_range(today, today),
        "week": _sum_range(start_week, today),
        "month": _sum_range(start_month, today),
        "range": _sum_range(start_str, end_str),
    }

    # daily series
    series_rows = list(surplus_col.aggregate([
        {"$match": {**base_match, "date": {"$gte": start_str, "$lte": end_str}}},
        {"$group": {"_id": "$date", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
        {"$sort": {"_id": 1}},
    ]))

    # by agent
    agent_rows = list(surplus_col.aggregate([
        {"$match": {**base_match, "date": {"$gte": start_str, "$lte": end_str}}},
        {"$group": {"_id": "$agent_id", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}, "agent_name": {"$first": "$agent_name"}}},
        {"$sort": {"total": -1}},
    ]))

    return jsonify(
        ok=True,
        totals=totals,
        series=[{"date": r.get("_id"), "total": float(r.get("total", 0))} for r in series_rows],
        by_agent=[{"agent_id": str(r.get("_id")), "agent_name": r.get("agent_name") or "Agent", "total": float(r.get("total", 0))} for r in agent_rows],
    )


@manager_surplus_cash_bp.route("/api/history", methods=["GET"])
def manager_surplus_cash_history():
    scope = _ensure_manager_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    manager_id, _ = scope

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    agent_id = (request.args.get("agent_id") or "").strip()
    q = (request.args.get("q") or "").strip()
    sort_key = (request.args.get("sort") or "newest").strip().lower()
    page = max(1, _safe_int(request.args.get("page"), 1))
    limit = min(max(_safe_int(request.args.get("limit"), 25), 1), 100)
    skip = (page - 1) * limit

    match = {"manager_id": manager_id, "date": {"$gte": start_str, "$lte": end_str}}
    if agent_id:
        match["agent_id"] = agent_id
    if q:
        safe = re.escape(q)
        match["$or"] = [
            {"agent_name": {"$regex": safe, "$options": "i"}},
            {"agent_phone": {"$regex": safe, "$options": "i"}},
            {"manager_name": {"$regex": safe, "$options": "i"}},
            {"note": {"$regex": safe, "$options": "i"}},
            {"branch": {"$regex": safe, "$options": "i"}},
        ]

    total_rows = surplus_col.count_documents(match)
    rows = list(surplus_col.find(match).sort(_build_sort(sort_key)).skip(skip).limit(limit))

    agent_ids = {r.get("agent_id") for r in rows if r.get("agent_id")}
    agent_map: Dict[str, Dict[str, Any]] = {}
    if agent_ids:
        for u in users_col.find({"_id": {"$in": [ObjectId(a) for a in agent_ids if ObjectId.is_valid(a)]}}, {"name": 1, "phone": 1}):
            agent_map[str(u.get("_id"))] = {"name": u.get("name") or "Agent", "phone": u.get("phone") or ""}

    out = []
    for r in rows:
        agent_id_val = r.get("agent_id", "")
        agent_info = agent_map.get(agent_id_val, {})
        out.append({
            "id": str(r.get("_id")),
            "amount": float(r.get("amount", 0)),
            "note": r.get("note", ""),
            "agent": {
                "id": agent_id_val,
                "name": r.get("agent_name") or agent_info.get("name") or "Agent",
                "phone": r.get("agent_phone") or agent_info.get("phone") or "",
            },
            "date": r.get("date", ""),
            "time": r.get("time", ""),
            "created_at": r.get("created_at").isoformat() if isinstance(r.get("created_at"), datetime) else "",
            "manager": {"id": manager_id, "name": r.get("manager_name", "Manager"), "branch": r.get("branch", "")},
            "recorded_by_role": r.get("recorded_by_role", ""),
        })

    pages = max(1, (total_rows + limit - 1) // limit)
    return jsonify(ok=True, items=out, total=total_rows, page=page, limit=limit, pages=pages)
