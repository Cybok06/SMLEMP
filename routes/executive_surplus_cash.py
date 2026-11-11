from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, Response
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import csv
import io
import re

from db import db

executive_surplus_cash_bp = Blueprint("executive_surplus_cash", __name__, url_prefix="/executive/surplus-cash")

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


def _current_exec_session() -> Tuple[Optional[str], Optional[str]]:
    if session.get("executive_id"):
        return "executive_id", session["executive_id"]
    if session.get("admin_id"):
        return "admin_id", session["admin_id"]
    return None, None


def _ensure_exec_scope_or_redirect():
    key, uid = _current_exec_session()
    if not uid:
        return redirect(url_for("login.login"))

    try:
        user_doc = users_col.find_one({"_id": ObjectId(uid)})
    except Exception:
        user_doc = users_col.find_one({"_id": uid})

    if not user_doc:
        return redirect(url_for("login.login"))

    role = (user_doc.get("role") or "").lower()
    if role not in ("executive", "admin"):
        return redirect(url_for("login.login"))

    return str(user_doc["_id"]), user_doc


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


def _json_safe(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    return obj


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


def _manager_agent_match(manager_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
    try:
        m_oid = ObjectId(manager_id)
        a_oid = ObjectId(agent_id)
        match = {
            "_id": a_oid,
            "role": "agent",
            "$or": [{"manager_id": m_oid}, {"manager_id": manager_id}]
        }
        return match
    except Exception:
        return {"_id": agent_id, "role": "agent", "manager_id": manager_id}


@executive_surplus_cash_bp.route("/", methods=["GET"])
def executive_surplus_cash_page():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return scope

    today = _today_str()

    return render_template(
        "executive_surplus_cash.html",
        today=today,
    )


@executive_surplus_cash_bp.route("/api/filters", methods=["GET"])
def executive_surplus_cash_filters():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401

    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()

    mgr_match: Dict[str, Any] = {"role": "manager"}
    if branch:
        mgr_match["branch"] = branch

    managers = list(users_col.find(mgr_match, {"name": 1, "branch": 1}))
    manager_rows = [
        {
            "id": str(m.get("_id")),
            "name": m.get("name") or "Manager",
            "branch": m.get("branch") or "",
        }
        for m in managers
    ]

    agent_match: Dict[str, Any] = {"role": "agent"}
    if manager_id:
        try:
            m_oid = ObjectId(manager_id)
            agent_match["$or"] = [{"manager_id": m_oid}, {"manager_id": manager_id}]
        except Exception:
            agent_match["manager_id"] = manager_id
    if branch:
        agent_match["branch"] = branch

    agents = list(users_col.find(agent_match, {"name": 1, "branch": 1, "manager_id": 1}))
    agent_rows = [
        {
            "id": str(a.get("_id")),
            "name": a.get("name") or "Agent",
            "branch": a.get("branch") or "",
            "manager_id": str(a.get("manager_id")) if a.get("manager_id") is not None else "",
        }
        for a in agents
    ]

    branches = sorted({m.get("branch") for m in managers if m.get("branch")} | {a.get("branch") for a in agents if a.get("branch")})

    return jsonify(ok=True, branches=branches, managers=manager_rows, agents=agent_rows)


@executive_surplus_cash_bp.route("/record", methods=["POST"])
def executive_surplus_cash_record():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    user_id, user_doc = scope

    manager_id = (request.form.get("manager_id") or (request.json or {}).get("manager_id") or "").strip()
    agent_id = (request.form.get("agent_id") or (request.json or {}).get("agent_id") or "").strip()
    amount_raw = request.form.get("amount") or (request.json or {}).get("amount")
    note = (request.form.get("note") or (request.json or {}).get("note") or "").strip()
    date_str = (request.form.get("date") or (request.json or {}).get("date") or _today_str()).strip()

    try:
        amount = float(amount_raw)
    except Exception:
        amount = 0.0

    if not manager_id or not agent_id or amount <= 0:
        return jsonify(ok=False, message="Manager, agent and amount are required."), 400

    # validate manager
    try:
        mgr_doc = users_col.find_one({"_id": ObjectId(manager_id), "role": "manager"})
    except Exception:
        mgr_doc = users_col.find_one({"_id": manager_id, "role": "manager"})
    if not mgr_doc:
        return jsonify(ok=False, message="Manager not found."), 404

    agent_match = _manager_agent_match(manager_id, agent_id)
    agent_doc = users_col.find_one(agent_match)
    if not agent_doc:
        return jsonify(ok=False, message="Agent not found or not under manager."), 404

    now_utc = datetime.utcnow()
    time_str = now_utc.strftime("%H:%M:%S")

    surplus_doc = {
        "amount": float(amount),
        "note": note,
        "agent_id": str(agent_doc.get("_id")),
        "agent_name": agent_doc.get("name") or agent_doc.get("username") or "Agent",
        "agent_phone": agent_doc.get("phone") or "",
        "manager_id": str(mgr_doc.get("_id")),
        "manager_name": mgr_doc.get("name") or "Manager",
        "branch": agent_doc.get("branch") or mgr_doc.get("branch") or "",
        "recorded_by_role": (user_doc.get("role") or "").lower() or "executive",
        "recorded_by_id": str(user_doc.get("_id")),
        "recorded_by_name": user_doc.get("name") or "",
        "date": date_str,
        "time": time_str,
        "created_at": now_utc,
        "updated_at": now_utc,
    }
    surplus_col.insert_one(surplus_doc)

    return jsonify(ok=True, message="Surplus recorded.")


@executive_surplus_cash_bp.route("/api/metrics", methods=["GET"])
def executive_surplus_cash_metrics():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()

    base_match: Dict[str, Any] = {}
    if branch:
        base_match["branch"] = branch
    if manager_id:
        base_match["manager_id"] = manager_id
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

    range_match = {**base_match, "date": {"$gte": start_str, "$lte": end_str}}

    series_rows = list(surplus_col.aggregate([
        {"$match": range_match},
        {"$group": {"_id": "$date", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
        {"$sort": {"_id": 1}},
    ]))

    by_manager = list(surplus_col.aggregate([
        {"$match": range_match},
        {"$group": {"_id": "$manager_id", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}, "manager_name": {"$first": "$manager_name"}}},
        {"$sort": {"total": -1}},
    ]))

    by_branch = list(surplus_col.aggregate([
        {"$match": range_match},
        {"$group": {"_id": "$branch", "total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
        {"$sort": {"total": -1}},
    ]))

    return jsonify(_json_safe({
        "ok": True,
        "range": {"start": start_str, "end": end_str},
        "totals": totals,
        "series": [{"date": r.get("_id"), "total": float(r.get("total", 0))} for r in series_rows],
        "by_manager": [{"manager_id": str(r.get("_id")), "manager_name": r.get("manager_name") or "Manager", "total": float(r.get("total", 0))} for r in by_manager],
        "by_branch": [{"branch": r.get("_id") or "Unknown", "total": float(r.get("total", 0))} for r in by_branch],
    }))


@executive_surplus_cash_bp.route("/api/history", methods=["GET"])
def executive_surplus_cash_history():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()
    q = (request.args.get("q") or "").strip()
    sort_key = (request.args.get("sort") or "newest").strip().lower()
    page = max(1, _safe_int(request.args.get("page"), 1))
    limit = min(max(_safe_int(request.args.get("limit"), 25), 1), 100)
    skip = (page - 1) * limit

    match: Dict[str, Any] = {"date": {"$gte": start_str, "$lte": end_str}}
    if branch:
        match["branch"] = branch
    if manager_id:
        match["manager_id"] = manager_id
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
    manager_ids = {r.get("manager_id") for r in rows if r.get("manager_id")}
    agent_map: Dict[str, Dict[str, Any]] = {}
    manager_map: Dict[str, Dict[str, Any]] = {}
    if agent_ids:
        agent_oids = [ObjectId(a) for a in agent_ids if ObjectId.is_valid(a)]
        for u in users_col.find({"_id": {"$in": agent_oids}}, {"name": 1, "phone": 1, "branch": 1}):
            agent_map[str(u.get("_id"))] = {"name": u.get("name") or "Agent", "phone": u.get("phone") or "", "branch": u.get("branch") or ""}
    if manager_ids:
        manager_oids = [ObjectId(m) for m in manager_ids if ObjectId.is_valid(m)]
        for u in users_col.find({"_id": {"$in": manager_oids}}, {"name": 1, "branch": 1}):
            manager_map[str(u.get("_id"))] = {"name": u.get("name") or "Manager", "branch": u.get("branch") or ""}

    out = []
    for r in rows:
        agent_id_val = r.get("agent_id", "")
        manager_id_val = r.get("manager_id", "")
        agent_info = agent_map.get(agent_id_val, {})
        manager_info = manager_map.get(manager_id_val, {})
        out.append({
            "id": str(r.get("_id")),
            "amount": float(r.get("amount", 0)),
            "note": r.get("note", ""),
            "agent": {
                "id": agent_id_val,
                "name": r.get("agent_name") or agent_info.get("name") or "Agent",
                "phone": r.get("agent_phone") or agent_info.get("phone") or "",
            },
            "manager": {
                "id": manager_id_val,
                "name": r.get("manager_name") or manager_info.get("name") or "Manager",
                "branch": r.get("branch") or manager_info.get("branch") or "",
            },
            "date": r.get("date", ""),
            "time": r.get("time", ""),
            "recorded_by_role": r.get("recorded_by_role", ""),
            "created_at": r.get("created_at").isoformat() if isinstance(r.get("created_at"), datetime) else "",
        })

    pages = max(1, (total_rows + limit - 1) // limit)
    return jsonify(ok=True, items=out, total=total_rows, page=page, limit=limit, pages=pages)


@executive_surplus_cash_bp.route("/export.csv", methods=["GET"])
def executive_surplus_cash_export_csv():
    scope = _ensure_exec_scope_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()

    match: Dict[str, Any] = {"date": {"$gte": start_str, "$lte": end_str}}
    if branch:
        match["branch"] = branch
    if manager_id:
        match["manager_id"] = manager_id
    if agent_id:
        match["agent_id"] = agent_id

    rows = list(surplus_col.find(match).sort("created_at", -1).limit(5000))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date", "time", "amount", "note", "agent_name", "manager_name", "branch", "recorded_by_name", "recorded_by_role"
    ])
    for r in rows:
        writer.writerow([
            r.get("date", ""),
            r.get("time", ""),
            r.get("amount", ""),
            r.get("note", ""),
            r.get("agent_name", ""),
            r.get("manager_name", ""),
            r.get("branch", ""),
            r.get("recorded_by_name", ""),
            r.get("recorded_by_role", ""),
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=surplus_cash.csv"}
    )
