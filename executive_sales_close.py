# executive_sales_close.py
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from bson.objectid import ObjectId
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from io import BytesIO
import calendar
import uuid

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)
from db import db

executive_sales_close_bp = Blueprint(
    "executive_sales_close",
    __name__,
    url_prefix="/executive-close"
)

users_col             = db["users"]
sales_close_col       = db["sales_close"]
manager_expenses_col  = db["manager_expenses"]

# ---------- optional: indexes (safe to run repeatedly) ----------
def _ensure_indexes():
    try:
        sales_close_col.create_index([("agent_id", 1), ("date", -1)])
        sales_close_col.create_index([("agent_id", 1), ("updated_at", -1)])
        manager_expenses_col.create_index([("manager_id", 1), ("date", -1), ("status", 1)])
    except Exception:
        pass

_ensure_indexes()

# -------------------------------------------------------------------
# Example helpful indexes (for reference, not executed here):
# db.sales_close.createIndex({ agent_id: 1, date: -1 })
# db.sales_close.createIndex({ agent_id: 1, total_amount: 1 })
# db.sales_close.createIndex({ date: -1, updated_at: -1 })
# db.users.createIndex({ role: 1 })
# db.manager_expenses.createIndex({ manager_id: 1, date: -1, status: 1 })
# -------------------------------------------------------------------

# ---------- basic helpers ----------

def _is_ajax(req) -> bool:
    return req.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"

def _today_str() -> str:
    # Use UTC "today" for daily docs; adjust later if you localize.
    return datetime.utcnow().strftime("%Y-%m-%d")

def _money(value: float) -> str:
    return f"GHS {float(value or 0):,.2f}"

def _ensure_executive_or_redirect():
    """
    Require an Executive session (not flask_login).
    Returns (exec_id_str, exec_doc) or a redirect to /login.
    """
    exec_id = session.get("executive_id")
    if not exec_id:
        return redirect(url_for("login.login"))

    try:
        exec_doc = users_col.find_one({"_id": ObjectId(exec_id)})
    except Exception:
        exec_doc = users_col.find_one({"_id": exec_id})

    if not exec_doc:
        return redirect(url_for("login.login"))

    role = (exec_doc.get("role") or "").lower()
    if role != "executive":
        return redirect(url_for("login.login"))

    return str(exec_doc["_id"]), exec_doc

def _sum_ledger_all_dates(owner_id_str: str) -> float:
    """
    Sum total_amount across ALL sales_close docs for the given owner_id (agent_id in ledger).
    (Handles string/number types safely.)
    """
    pipeline = [
        {"$match": {"agent_id": owner_id_str}},
        {"$group": {"_id": None, "sum_amount": {"$sum": {
            "$toDouble": {"$ifNull": ["$total_amount", 0]}
        }}}}
    ]
    agg = list(sales_close_col.aggregate(pipeline))
    if not agg:
        return 0.0
    try:
        return float(agg[0].get("sum_amount", 0.0))
    except Exception:
        return 0.0

# ---------- date & expense / gross helpers (mirrors manager logic) ----------

def _date_range_strings(key: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (start_str, end_str) as 'YYYY-MM-DD' for:
      - 'today' : today only
      - 'week'  : Monday -> today
      - 'month' : 1st of month -> today
    If key is unrecognised, returns (None, None) meaning "all time".
    """
    today = datetime.utcnow().date()

    if key == "today":
        s = today.strftime("%Y-%m-%d")
        return s, s

    if key == "week":
        start = today - timedelta(days=today.weekday())
        return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    if key == "month":
        start = today.replace(day=1)
        return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    return None, None  # all time

def _manager_approved_expenses_total(manager_id_str: Optional[str], range_key: Optional[str]) -> float:
    """
    Sum of APPROVED expenses:
      - If manager_id_str is given: only that manager's expenses.
      - If manager_id_str is None: all managers' expenses.
    range_key: None/'total' -> all time, otherwise 'today'|'week'|'month'.
    """
    match: Dict[str, Any] = {
        "status": "Approved",
    }
    if manager_id_str is not None:
        match["manager_id"] = manager_id_str

    if range_key and range_key != "total":
        start_str, end_str = _date_range_strings(range_key)
        if start_str:
            match["date"] = {"$gte": start_str, "$lte": end_str}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "sum_amount": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}
        }}
    ]
    agg = list(manager_expenses_col.aggregate(pipeline))
    if not agg:
        return 0.0
    try:
        return float(agg[0].get("sum_amount", 0.0))
    except Exception:
        return 0.0

def _owner_ledger_flow_for_range(owner_id_str: Optional[str], range_key: Optional[str]) -> Dict[str, float]:
    """
    For sales_close docs:
      - If owner_id_str is given: only that owner's ledger (agent_id == owner_id_str).
      - If owner_id_str is None: ALL docs (all owners).

    For a given period:
      available = sum of current total_amount (remaining)   in docs in that date range
      withdrawn = sum of withdrawals[].amount              in docs in that date range
      gross_before_expense = available + withdrawn
    """
    match: Dict[str, Any] = {}
    if owner_id_str is not None:
        match["agent_id"] = owner_id_str

    if range_key and range_key != "total":
        start_str, end_str = _date_range_strings(range_key)
        if start_str:
            match["date"] = {"$gte": start_str, "$lte": end_str}

    pipeline = [
        {"$match": match},
        {
            "$project": {
                "bal_num": {
                    "$toDouble": {"$ifNull": ["$total_amount", 0]}
                },
                "withdrawals_amounts": {
                    "$map": {
                        "input": {"$ifNull": ["$withdrawals", []]},
                        "as": "w",
                        "in": {
                            "$toDouble": {"$ifNull": ["$$w.amount", 0]}
                        },
                    }
                },
            }
        },
        {
            "$project": {
                "bal_num": 1,
                "withdrawals_sum": {"$sum": "$withdrawals_amounts"},
            }
        },
        {
            "$group": {
                "_id": None,
                "sum_bal": {"$sum": "$bal_num"},
                "sum_withdrawn": {"$sum": "$withdrawals_sum"},
            }
        },
    ]

    agg = list(sales_close_col.aggregate(pipeline))
    if not agg:
        return {"available": 0.0, "withdrawn": 0.0, "gross": 0.0}

    doc = agg[0]
    available = float(doc.get("sum_bal", 0.0))
    withdrawn = float(doc.get("sum_withdrawn", 0.0))
    gross = available + withdrawn

    return {"available": available, "withdrawn": withdrawn, "gross": gross}

def _manager_balance_breakdown(manager_id_str: str) -> Dict[str, Dict[str, float]]:
    """
    Per-manager / per-branch breakdown for:
      - today, week, month, total

    For each period:
      col   = Σ(total_amount + withdrawals.amount) in that manager's ledger
      exp   = approved expenses for that manager in that period
      gross = col − exp
    """
    periods = ["total", "month", "week", "today"]
    out: Dict[str, Dict[str, float]] = {}

    for p in periods:
        rng = None if p == "total" else p
        flow = _owner_ledger_flow_for_range(manager_id_str, rng)
        col = flow["gross"]
        exp = _manager_approved_expenses_total(manager_id_str, rng)
        gross_after = col - exp
        out[p] = {"col": col, "exp": exp, "gross": gross_after}

    return out

def _all_branches_balance_breakdown(manager_ids: List[str]) -> Dict[str, Dict[str, float]]:
    """
    Sum of all manager branches:
      For each period, we sum:
        - col (collections)
        - exp (expenses)
        - gross (col - exp)
    """
    periods = ["total", "month", "week", "today"]
    agg_out: Dict[str, Dict[str, float]] = {
        p: {"col": 0.0, "exp": 0.0, "gross": 0.0} for p in periods
    }

    for mid in manager_ids:
        mb = _manager_balance_breakdown(mid)
        for p in periods:
            agg_out[p]["col"]   += mb[p]["col"]
            agg_out[p]["exp"]   += mb[p]["exp"]
            agg_out[p]["gross"] += mb[p]["gross"]

    return agg_out

# ---------- role grouping helper (existing) ----------

def _group_totals_for_roles(roles: List[str]) -> List[Dict[str, Any]]:
    """
    FAST path: one aggregation to get TOTAL (all dates) per user for the given roles.
      - Group sales_close by agent_id
      - $lookup users by stringified _id, filter by roles (lowercased)
      - Return: { user_id(str), total(float), name, phone, role(lower) }, sorted DESC.
    """
    roles = [r.lower() for r in roles]
    pipeline = [
        {"$group": {"_id": "$agent_id", "total": {"$sum": {
            "$toDouble": {"$ifNull": ["$total_amount", 0]}
        }}}},
        {"$lookup": {
            "from": "users",
            "let": {"aid": "$_id"},
            "pipeline": [
                {"$addFields": {"_id_str": {"$toString": "$_id"}}},
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id_str", "$$aid"]},
                    {"$in": [{"$toLower": "$role"}, roles]}
                ]}}},
                {"$project": {
                    "name": 1,
                    "username": 1,
                    "phone": 1,
                    "role": 1,
                    "branch": 1,
                    "branch_name": 1,
                    "manager_id": 1,
                }}
            ],
            "as": "user"
        }},
        {"$unwind": "$user"},
        {"$project": {
            "_id": 0,
            "user_id": "$_id",
            "total": 1,
            "name": {"$ifNull": ["$user.name", "$user.username"]},
            "phone": "$user.phone",
            "role": {"$toLower": "$user.role"},
            "branch": {"$ifNull": ["$user.branch_name", "$user.branch"]},
            "manager_id": "$user.manager_id",
        }},
        {"$sort": {"total": -1}}
    ]
    return list(sales_close_col.aggregate(pipeline))

def _format_user_total_row(row: Dict[str, Any]) -> Dict[str, Any]:
    total = float(row.get("total", 0.0))
    manager_id = row.get("manager_id")
    if isinstance(manager_id, ObjectId):
        manager_id = str(manager_id)
    return {
        "_id": row["user_id"],
        "name": row.get("name") or "User",
        "phone": row.get("phone", ""),
        "role": row["role"],
        "branch": row.get("branch") or "",
        "manager_id": manager_id or "",
        "available": f"{total:,.2f}",
        "available_num": total,
    }

def _hydrate_agent_branches(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve agent branch from the assigned manager only.
    Do not trust users.branch on agent records because branch ownership is defined by managers.
    """
    manager_ids = {
        str(a.get("manager_id"))
        for a in agents
        if a.get("manager_id") and ObjectId.is_valid(str(a.get("manager_id")))
    }
    manager_oids = [ObjectId(mid) for mid in manager_ids]
    manager_by_id: Dict[str, Dict[str, Any]] = {}
    if manager_oids:
        for m in users_col.find(
            {"_id": {"$in": manager_oids}},
            {"branch": 1, "branch_name": 1, "name": 1, "username": 1},
        ):
            manager_by_id[str(m["_id"])] = m

    for agent in agents:
        manager_doc = manager_by_id.get(str(agent.get("manager_id"))) or {}
        branch = (
            manager_doc.get("branch_name")
            or manager_doc.get("branch")
            or ""
        ).strip()
        agent["branch"] = branch or "Unassigned"
    return agents

def _sum_totals_for_roles(roles: List[str]) -> float:
    roles = [r.lower() for r in roles]
    pipeline = [
        {"$group": {"_id": "$agent_id", "total": {"$sum": {
            "$toDouble": {"$ifNull": ["$total_amount", 0]}
        }}}},
        {"$lookup": {
            "from": "users",
            "let": {"aid": "$_id"},
            "pipeline": [
                {"$addFields": {"_id_str": {"$toString": "$_id"}}},
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id_str", "$$aid"]},
                    {"$in": [{"$toLower": "$role"}, roles]}
                ]}}}
            ],
            "as": "user"
        }},
        {"$match": {"user.0": {"$exists": True}}},
        {"$group": {"_id": None, "sum_total": {"$sum": "$total"}}},
    ]
    agg = list(sales_close_col.aggregate(pipeline))
    if not agg:
        return 0.0
    try:
        return float(agg[0].get("sum_total", 0.0))
    except Exception:
        return 0.0

# ---------- views ----------

@executive_sales_close_bp.route("/", methods=["GET"])
def executive_close_page():
    """
    Executive dashboard:
      - All Branches Gross Today / Week / Month / Total:
           Gross = (Σ total_amount + Σ withdrawals.amount into manager ledgers) − Σ Approved manager expenses
      - Branch overview per manager (Gross per period + available)
      - Close Total (Executive ledger, all dates)
      - Unclose Total (sum of Admin+Manager+Agent balances, all dates)
      - Grids for Admins, Managers, Agents (same as before for withdrawals).
    """
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return scope
    exec_id, exec_doc = scope

    today = _today_str()
    return render_template(
        "executive_sales_close.html",
        executive_name=exec_doc.get("name", "Executive"),
        today=today,
        branches=[],
        admins=[],
        managers=[],
        agents=[]
    )

def _build_executive_close_payload(exec_id: str, exec_doc: dict, include_agents: bool = True) -> Dict[str, Any]:
    today = _today_str()

    if include_agents:
        grouped = _group_totals_for_roles(["admin", "manager", "agent"])
    else:
        grouped = _group_totals_for_roles(["admin", "manager"])

    admins   = [_format_user_total_row(r) for r in grouped if r["role"] == "admin"   and r["user_id"] != exec_id]
    managers = [_format_user_total_row(r) for r in grouped if r["role"] == "manager"]
    agents   = _hydrate_agent_branches([_format_user_total_row(r) for r in grouped if r["role"] == "agent"])

    manager_ids = [m["_id"] for m in managers]
    all_branches = _all_branches_balance_breakdown(manager_ids)

    def _fmt_val(v: float) -> Dict[str, Any]:
        return {"value": float(v), "formatted": f"{float(v):,.2f}"}

    all_gross_total = _fmt_val(all_branches["total"]["gross"])
    all_gross_month = _fmt_val(all_branches["month"]["gross"])
    all_gross_week  = _fmt_val(all_branches["week"]["gross"])
    all_gross_today = _fmt_val(all_branches["today"]["gross"])

    all_col_total = _fmt_val(all_branches["total"]["col"])
    all_col_month = _fmt_val(all_branches["month"]["col"])
    all_col_week  = _fmt_val(all_branches["week"]["col"])
    all_col_today = _fmt_val(all_branches["today"]["col"])

    all_exp_total = _fmt_val(all_branches["total"]["exp"])
    all_exp_month = _fmt_val(all_branches["month"]["exp"])
    all_exp_week  = _fmt_val(all_branches["week"]["exp"])
    all_exp_today = _fmt_val(all_branches["today"]["exp"])

    branches: List[Dict[str, Any]] = []
    for m in managers:
        mid = m["_id"]
        mb  = _manager_balance_breakdown(mid)

        try:
            m_doc = users_col.find_one(
                {"_id": ObjectId(mid)},
                {"branch": 1, "branch_name": 1, "name": 1, "username": 1}
            )
        except Exception:
            m_doc = users_col.find_one(
                {"_id": mid},
                {"branch": 1, "branch_name": 1, "name": 1, "username": 1}
            )

        branch_label = (
            (m_doc or {}).get("branch_name")
            or (m_doc or {}).get("branch")
            or m["name"]
        )

        branches.append({
            "manager_id": mid,
            "branch": branch_label,
            "name": m["name"],
            "phone": m["phone"],
            "available": m["available"],
            "available_num": m["available_num"],

            "gross_today": f"{mb['today']['gross']:,.2f}",
            "gross_week":  f"{mb['week']['gross']:,.2f}",
            "gross_month": f"{mb['month']['gross']:,.2f}",
            "gross_total": f"{mb['total']['gross']:,.2f}",

            "col_today": f"{mb['today']['col']:,.2f}",
            "col_week":  f"{mb['week']['col']:,.2f}",
            "col_month": f"{mb['month']['col']:,.2f}",
            "col_total": f"{mb['total']['col']:,.2f}",

            "exp_today": f"{mb['today']['exp']:,.2f}",
            "exp_week":  f"{mb['week']['exp']:,.2f}",
            "exp_month": f"{mb['month']['exp']:,.2f}",
            "exp_total": f"{mb['total']['exp']:,.2f}",
        })

    close_total_val   = _sum_ledger_all_dates(exec_id)
    if include_agents:
        unclose_total_val = float(sum(r["available_num"] for r in admins + managers + agents))
    else:
        unclose_total_val = _sum_totals_for_roles(["admin", "manager", "agent"])

    payload = {
        "ok": True,
        "executive_name": exec_doc.get("name", "Executive"),
        "today": today,
        "all_gross_total": all_gross_total,
        "all_gross_month": all_gross_month,
        "all_gross_week": all_gross_week,
        "all_gross_today": all_gross_today,
        "all_col_total": all_col_total,
        "all_col_month": all_col_month,
        "all_col_week": all_col_week,
        "all_col_today": all_col_today,
        "all_exp_total": all_exp_total,
        "all_exp_month": all_exp_month,
        "all_exp_week": all_exp_week,
        "all_exp_today": all_exp_today,
        "close_total": _fmt_val(close_total_val),
        "unclose_total": _fmt_val(unclose_total_val),
        "branches": branches,
        "admins": admins,
        "managers": managers,
    }
    if include_agents:
        payload["agents"] = agents
    return payload

def _summary_payload(exec_doc: dict, manager_ids: List[str]) -> Dict[str, Any]:
    today = _today_str()
    all_branches = _all_branches_balance_breakdown(manager_ids)

    def _fmt_val(v: float) -> Dict[str, Any]:
        return {"value": float(v), "formatted": f"{float(v):,.2f}"}

    close_total_val = _sum_ledger_all_dates(str(exec_doc.get("_id")))
    unclose_total_val = _sum_totals_for_roles(["admin", "manager", "agent"])

    return {
        "ok": True,
        "executive_name": exec_doc.get("name", "Executive"),
        "today": today,
        "all_gross_total": _fmt_val(all_branches["total"]["gross"]),
        "all_gross_month": _fmt_val(all_branches["month"]["gross"]),
        "all_gross_week": _fmt_val(all_branches["week"]["gross"]),
        "all_gross_today": _fmt_val(all_branches["today"]["gross"]),
        "all_col_total": _fmt_val(all_branches["total"]["col"]),
        "all_col_month": _fmt_val(all_branches["month"]["col"]),
        "all_col_week": _fmt_val(all_branches["week"]["col"]),
        "all_col_today": _fmt_val(all_branches["today"]["col"]),
        "all_exp_total": _fmt_val(all_branches["total"]["exp"]),
        "all_exp_month": _fmt_val(all_branches["month"]["exp"]),
        "all_exp_week": _fmt_val(all_branches["week"]["exp"]),
        "all_exp_today": _fmt_val(all_branches["today"]["exp"]),
        "close_total": _fmt_val(close_total_val),
        "unclose_total": _fmt_val(unclose_total_val),
    }

@executive_sales_close_bp.route("/api/summary", methods=["GET"])
def executive_close_summary():
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    _, exec_doc = scope
    grouped_managers = _group_totals_for_roles(["manager"])
    manager_ids = [r["user_id"] for r in grouped_managers if r.get("user_id")]
    payload = _summary_payload(exec_doc, manager_ids)
    return jsonify(payload)

@executive_sales_close_bp.route("/api/admins", methods=["GET"])
def executive_close_admins():
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    exec_id, _ = scope
    grouped_admins = _group_totals_for_roles(["admin"])
    admins = [
        _format_user_total_row(r)
        for r in grouped_admins
        if r["role"] == "admin" and r["user_id"] != exec_id
    ]
    return jsonify(ok=True, admins=admins)

@executive_sales_close_bp.route("/api/managers", methods=["GET"])
def executive_close_managers():
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    grouped_managers = _group_totals_for_roles(["manager"])
    managers = [_format_user_total_row(r) for r in grouped_managers if r["role"] == "manager"]
    return jsonify(ok=True, managers=managers)

@executive_sales_close_bp.route("/api/branches", methods=["GET"])
def executive_close_branches():
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    grouped_managers = _group_totals_for_roles(["manager"])
    managers = [_format_user_total_row(r) for r in grouped_managers if r["role"] == "manager"]
    branches: List[Dict[str, Any]] = []
    for m in managers:
        mid = m["_id"]
        mb = _manager_balance_breakdown(mid)

        try:
            m_doc = users_col.find_one(
                {"_id": ObjectId(mid)},
                {"branch": 1, "branch_name": 1, "name": 1, "username": 1}
            )
        except Exception:
            m_doc = users_col.find_one(
                {"_id": mid},
                {"branch": 1, "branch_name": 1, "name": 1, "username": 1}
            )

        branch_label = (
            (m_doc or {}).get("branch_name")
            or (m_doc or {}).get("branch")
            or m["name"]
        )

        branches.append({
            "manager_id": mid,
            "branch": branch_label,
            "name": m["name"],
            "phone": m["phone"],
            "available": m["available"],
            "available_num": m["available_num"],
            "gross_today": f"{mb['today']['gross']:,.2f}",
            "gross_week":  f"{mb['week']['gross']:,.2f}",
            "gross_month": f"{mb['month']['gross']:,.2f}",
            "gross_total": f"{mb['total']['gross']:,.2f}",
            "col_today": f"{mb['today']['col']:,.2f}",
            "col_week":  f"{mb['week']['col']:,.2f}",
            "col_month": f"{mb['month']['col']:,.2f}",
            "col_total": f"{mb['total']['col']:,.2f}",
            "exp_today": f"{mb['today']['exp']:,.2f}",
            "exp_week":  f"{mb['week']['exp']:,.2f}",
            "exp_month": f"{mb['month']['exp']:,.2f}",
            "exp_total": f"{mb['total']['exp']:,.2f}",
        })
    return jsonify(ok=True, branches=branches)

@executive_sales_close_bp.route("/api/agents", methods=["GET"])
def executive_close_agents():
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Unauthorized"), 401
    grouped = _group_totals_for_roles(["agent"])
    agents = _hydrate_agent_branches([_format_user_total_row(r) for r in grouped if r["role"] == "agent"])
    return jsonify(ok=True, agents=agents)

@executive_sales_close_bp.route("/withdraw", methods=["POST"])
def executive_withdraw():
    """
    POST: target_id, amount, note (optional)

    Behaviour:
      - Debits across multiple sales_close docs of the TARGET (today first, then most recent -> older),
        using $expr/$toDouble so both numeric and string balances are handled.
      - Credits the EXECUTIVE'S TODAY doc with the total actually withdrawn.
      - Returns refreshed totals (all dates) + per-date debit breakdown.
    """
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        if _is_ajax(request):
            return jsonify(ok=False, message="Please log in."), 401
        return scope
    exec_id, exec_doc = scope

    target_id = (request.form.get("target_id") or (request.json.get("target_id") if request.is_json else "")) or ""
    amount_in = request.form.get("amount") or (request.json.get("amount") if request.is_json else None)
    note      = (request.form.get("note") or (request.json.get("note") if request.is_json else "")) or ""

    try:
        amount = float(amount_in)
    except Exception:
        amount = 0.0

    if not target_id or amount <= 0:
        msg = "Target and a positive amount are required."
        return (jsonify(ok=False, message=msg), 400) if _is_ajax(request) else (msg, 400)

    # Load target user (any role: agent/manager/admin)
    try:
        tgt_doc = users_col.find_one({"_id": ObjectId(target_id)})
    except Exception:
        tgt_doc = users_col.find_one({"_id": target_id})
    if not tgt_doc:
        msg = "Target user not found."
        return (jsonify(ok=False, message=msg), 404) if _is_ajax(request) else (msg, 404)

    tgt_role = (tgt_doc.get("role") or "").lower()
    if tgt_role not in ("agent", "manager", "admin"):
        msg = "You can only withdraw from agents, managers, or admins."
        return (jsonify(ok=False, message=msg), 403) if _is_ajax(request) else (msg, 403)

    # --- Build candidate docs to debit: today first, then recent->older; only with positive balance ---
    today   = _today_str()
    now_utc = datetime.utcnow()
    time_str = now_utc.strftime("%H:%M:%S")
    transaction_id = f"EXW-{now_utc.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"

    pipeline = [
        {"$match": {"agent_id": str(target_id)}},
        {"$addFields": {
            "bal_num": {"$toDouble": {"$ifNull": ["$total_amount", 0]}},
            "is_today": {"$cond": [{"$eq": ["$date", today]}, 1, 0]}
        }},
        {"$match": {"bal_num": {"$gt": 0}}},
        {"$sort": {"is_today": -1, "date": -1, "updated_at": -1}}
    ]
    docs = list(sales_close_col.aggregate(pipeline))

    # Quick total across all dates
    total_all = float(sum(float(d.get("bal_num", 0.0)) for d in docs))
    if total_all + 1e-9 < amount:
        msg = f"Insufficient balance. Target total across all days: GHS {total_all:,.2f}"
        return (jsonify(ok=False, message=msg, available=f"{total_all:,.2f}"), 409) if _is_ajax(request) else (msg, 409)

    remaining = amount
    debits: List[Dict[str, Any]] = []  # breakdown: [{date, debited}]

    # --- Debit across multiple docs until covered ---
    for d in docs:
        if remaining <= 1e-9:
            break
        doc_id   = d["_id"]
        date_str = d.get("date", "")
        available = float(d.get("bal_num", 0.0))
        if available <= 0:
            continue

        take = min(available, remaining)

        # Safe compare even if total_amount is string
        filter_q = {
            "_id": doc_id,
            "$expr": {"$gte": [
                {"$toDouble": {"$ifNull": ["$total_amount", 0]}},
                take
            ]}
        }
        update_q = {
            "$inc": {"total_amount": -take},
            "$set": {"updated_at": now_utc, "last_withdrawal_at": now_utc},
            "$push": {"withdrawals": {
                "amount": float(round(take, 2)),
                "transaction_id": transaction_id,
                "transaction_total": float(round(amount, 2)),
                "by_executive_id": exec_id,
                "by_executive_name": exec_doc.get("name", ""),
                "by_role": "executive",
                "withdrawal_date": today,
                "source_date": date_str,
                "date": date_str,
                "time": time_str,
                "at": now_utc,
                "note": note
            }}
        }
        res = sales_close_col.update_one(filter_q, update_q)
        if res.modified_count == 1:
            debits.append({"date": date_str, "debited": take})
            remaining -= take
        # Else: concurrent change; try next doc.

    actually_debited = amount - remaining
    if actually_debited <= 0:
        # Concurrency edge-case: recompute and respond
        current_total = _sum_ledger_all_dates(str(target_id))
        msg = f"Insufficient balance due to concurrent changes. Current total: GHS {current_total:,.2f}"
        return (jsonify(ok=False, message=msg, available=f"{current_total:,.2f}"), 409) if _is_ajax(request) else (msg, 409)

    # --- Credit EXECUTIVE TODAY doc by the amount actually debited ---
    exec_filter = {"agent_id": exec_id, "date": today}
    exec_update = {
        "$setOnInsert": {"agent_id": exec_id, "manager_id": exec_id, "date": today, "created_at": now_utc},
        "$inc": {"total_amount": actually_debited, "count": 1},
        "$set": {"updated_at": now_utc, "last_payment_at": now_utc}
    }
    sales_close_col.update_one(exec_filter, exec_update, upsert=True)

    # --- Recompute refreshed totals (ALL dates) — minimal roundtrips ---
    target_total  = _sum_ledger_all_dates(str(target_id))                # one agg
    grouped       = _group_totals_for_roles(["admin", "manager", "agent"])  # one agg for unclose
    unclose_total = float(sum(float(r.get("total", 0.0)) for r in grouped))
    close_total   = _sum_ledger_all_dates(exec_id)                       # one agg

    payload = {
        "ok": True,
        "message": (
            f"Withdrew GHS {actually_debited:,.2f} across {len(debits)} day(s) "
            f"from {tgt_role} and credited executive account."
        ),
        "requested": f"{amount:,.2f}",
        "transaction_id": transaction_id,
        "debited_breakdown": [{"date": x["date"], "amount": f"{x['debited']:,.2f}"} for x in debits],
        "target_id": str(target_id),
        "target_role": tgt_role,
        "available": f"{target_total:,.2f}",     # target TOTAL (all dates)
        "unclose_total": f"{unclose_total:,.2f}",
        "close_total": f"{close_total:,.2f}"
    }
    return jsonify(payload) if _is_ajax(request) else (
        f"OK. Debited: {payload['requested']} | "
        f"Target total: {payload['available']} | "
        f"Unclose Total: {payload['unclose_total']} | "
        f"Close Total: {payload['close_total']}"
    )

@executive_sales_close_bp.route("/user/<user_id>/withdrawals", methods=["GET"])
def user_withdrawals(user_id):
    """
    Return one history row per withdrawal action across all source-ledger dates.

    A withdrawal may debit several daily sales_close documents. Those stored
    portions are grouped at read time by transaction_id (or matching legacy
    action metadata), without changing any MongoDB documents.
    """
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return jsonify(ok=False, message="Please log in."), 401

    # Ensure target user exists & is allowed
    try:
        tgt_doc = users_col.find_one({"_id": ObjectId(user_id)})
    except Exception:
        tgt_doc = users_col.find_one({"_id": user_id})
    if not tgt_doc:
        return jsonify(ok=False, message="User not found."), 404

    tgt_role = (tgt_doc.get("role") or "").lower()
    if tgt_role not in ("agent", "manager", "admin"):
        return jsonify(ok=False, message="History available only for agent/manager/admin."), 403

    # Project withdrawals only (lightweight)
    cursor = sales_close_col.find({"agent_id": str(user_id)}, {"date": 1, "withdrawals": 1})
    grouped: Dict[str, Dict[str, Any]] = {}
    for d in cursor:
        for w in (d.get("withdrawals") or []):
            by_name = (
                w.get("by_name")
                or w.get("by_executive_name")
                or w.get("by_admin_name")
                or w.get("by_manager_name")
                or ""
            )
            by_role = (
                w.get("by_role")
                or ("executive" if w.get("by_executive_id") else
                    ("admin" if w.get("by_admin_id") else
                     ("manager" if w.get("by_manager_id") else "")))
            )
            actor_id = str(
                w.get("by_executive_id")
                or w.get("by_admin_id")
                or w.get("by_manager_id")
                or ""
            )
            action_at = _as_datetime(
                w.get("at"),
                w.get("withdrawal_date") or w.get("date") or "",
                w.get("time") or "",
            )
            transaction_id = str(w.get("transaction_id") or "").strip()
            note = str(w.get("note") or "").strip()
            if transaction_id:
                group_key = f"id:{transaction_id}"
            else:
                # Split legacy debits share the same action timestamp, actor and
                # note even though they live inside different daily documents.
                group_key = (
                    f"legacy:{by_role}:{actor_id}:"
                    f"{action_at.isoformat(timespec='microseconds')}:{note}"
                )

            action_date = str(w.get("withdrawal_date") or "").strip()
            if not action_date and action_at != datetime.min:
                action_date = action_at.strftime("%Y-%m-%d")
            if not action_date:
                action_date = str(w.get("date") or "")

            action_time = str(w.get("time") or "").strip()
            if action_at != datetime.min:
                action_time = action_at.strftime("%H:%M:%S")

            row = grouped.setdefault(group_key, {
                "transaction_id": transaction_id,
                "amount": 0.0,
                "date": action_date,
                "time": action_time,
                "note": note,
                "by_name": by_name,
                "by_role": by_role,
                "at_iso": action_at.isoformat() if action_at != datetime.min else f"{action_date}T{action_time}",
                "sources": [],
            })
            amount = float(w.get("amount", 0.0) or 0.0)
            row["amount"] += amount
            row["sources"].append({
                "date": w.get("source_date") or w.get("date") or d.get("date") or "",
                "amount": amount,
            })

    items = list(grouped.values())
    for item in items:
        sources_by_date: Dict[str, float] = defaultdict(float)
        for source in item["sources"]:
            sources_by_date[str(source.get("date") or "Unknown")] += float(source.get("amount", 0) or 0)
        item["sources"] = [
            {"date": date, "amount": amount}
            for date, amount in sorted(sources_by_date.items(), reverse=True)
        ]
    items.sort(key=lambda x: x.get("at_iso", ""), reverse=True)

    # Return history incrementally so opening the modal does not transfer every
    # transaction at once. Keep the batch size capped at 10 for this UI.
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    page_size = 10
    total = len(items)
    page_items = items[offset:offset + page_size]
    next_offset = offset + len(page_items)

    response = jsonify(
        ok=True,
        user={
            "_id": str(tgt_doc["_id"]),
            "name": tgt_doc.get("name") or tgt_doc.get("username") or "User",
            "phone": tgt_doc.get("phone", ""),
            "role": tgt_role,
        },
        withdrawals=page_items,
        offset=offset,
        limit=page_size,
        total=total,
        next_offset=next_offset,
        has_more=next_offset < total,
    )
    # History is mutable and is transformed at read time. Prevent a browser or
    # intermediary from reusing the older, ungrouped response after deployment.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ---------- Manager withdrawal PDF reporting ----------

def _as_datetime(value: Any, fallback_date: str = "", fallback_time: str = "") -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
    for candidate in (
        f"{fallback_date} {fallback_time}".strip(),
        fallback_date,
    ):
        if not candidate:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                pass
    return datetime.min


def _manager_executive_transactions(manager_id: str) -> List[Dict[str, Any]]:
    """
    Consolidate split ledger debits into the single Executive action that caused them.
    New rows use transaction_id. Legacy rows are grouped by manager, executive,
    exact action timestamp and note; their source-ledger dates are never used as
    the reporting date.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    cursor = sales_close_col.find(
        {"agent_id": str(manager_id)},
        {"withdrawals": 1},
    )
    for ledger_doc in cursor:
        for withdrawal in ledger_doc.get("withdrawals") or []:
            by_role = (withdrawal.get("by_role") or "").lower()
            executive_id = str(withdrawal.get("by_executive_id") or "")
            if by_role != "executive" and not executive_id:
                continue

            action_at = _as_datetime(
                withdrawal.get("at"),
                withdrawal.get("withdrawal_date") or withdrawal.get("date") or "",
                withdrawal.get("time") or "",
            )
            transaction_id = str(withdrawal.get("transaction_id") or "").strip()
            if transaction_id:
                group_key = f"id:{transaction_id}"
                reference = transaction_id
            else:
                timestamp_key = action_at.isoformat(timespec="microseconds")
                note_key = str(withdrawal.get("note") or "").strip()
                group_key = f"legacy:{executive_id}:{timestamp_key}:{note_key}"
                reference = f"LEG-{action_at.strftime('%Y%m%d%H%M%S')}"

            row = grouped.setdefault(group_key, {
                "reference": reference,
                "at": action_at,
                "executive_id": executive_id,
                "executive_name": withdrawal.get("by_executive_name")
                    or withdrawal.get("by_name") or "Executive",
                "note": str(withdrawal.get("note") or "").strip(),
                "amount": 0.0,
                "sources": [],
            })
            amount = float(withdrawal.get("amount", 0) or 0)
            row["amount"] += amount
            row["sources"].append({
                "date": withdrawal.get("source_date") or withdrawal.get("date") or "",
                "amount": amount,
            })

    transactions = list(grouped.values())
    transactions.sort(key=lambda item: item["at"], reverse=True)
    return transactions


def _report_period_transactions(
    transactions: List[Dict[str, Any]],
    year_value: str,
) -> Tuple[List[Dict[str, Any]], str]:
    year_value = (year_value or "").strip()
    if year_value and year_value.lower() != "all":
        try:
            selected_year = int(year_value)
        except ValueError:
            selected_year = datetime.utcnow().year
        return (
            [row for row in transactions if row["at"] != datetime.min and row["at"].year == selected_year],
            str(selected_year),
        )
    return transactions, "All recorded months"


def _pdf_header_footer(canvas, doc, report_reference: str):
    canvas.saveState()
    width, _ = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#D7DEE8"))
    canvas.line(15 * mm, 12 * mm, width - 15 * mm, 12 * mm)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(15 * mm, 7.5 * mm, f"Confidential - Smart Living | {report_reference}")
    canvas.drawRightString(
        width - 15 * mm,
        7.5 * mm,
        f"Page {doc.page} | Generated {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}",
    )
    canvas.restoreState()


@executive_sales_close_bp.route("/manager/<manager_id>/report.pdf", methods=["GET"])
def manager_withdrawal_report_pdf(manager_id: str):
    scope = _ensure_executive_or_redirect()
    if not isinstance(scope, tuple):
        return scope
    exec_id, exec_doc = scope

    try:
        manager_doc = users_col.find_one({"_id": ObjectId(manager_id), "role": "manager"})
    except Exception:
        manager_doc = users_col.find_one({"_id": manager_id, "role": "manager"})
    if not manager_doc:
        return jsonify(ok=False, message="Manager not found."), 404

    transactions, period_label = _report_period_transactions(
        _manager_executive_transactions(str(manager_doc["_id"])),
        request.args.get("year", "all"),
    )
    now = datetime.utcnow()
    report_reference = f"MWR-{str(manager_doc['_id'])[-6:].upper()}-{now.strftime('%Y%m%d%H%M%S')}"
    current_balance = _sum_ledger_all_dates(str(manager_doc["_id"]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=f"Manager Withdrawal Report - {manager_doc.get('name', 'Manager')}",
        author="Smart Living",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=21, leading=25, textColor=colors.HexColor("#123B69"), alignment=TA_LEFT,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SectionTitle", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=14, leading=17, textColor=colors.HexColor("#123B69"), spaceBefore=8, spaceAfter=7,
    ))
    styles.add(ParagraphStyle(
        name="SmallMuted", parent=styles["BodyText"], fontSize=8, leading=10,
        textColor=colors.HexColor("#667085"),
    ))
    styles.add(ParagraphStyle(
        name="Cell", parent=styles["BodyText"], fontSize=7.5, leading=9,
        textColor=colors.HexColor("#263238"),
    ))
    styles.add(ParagraphStyle(
        name="CellRight", parent=styles["Cell"], alignment=TA_RIGHT,
    ))

    story = [
        Paragraph("SMART LIVING", styles["Heading3"]),
        Paragraph("Manager Executive Withdrawal Report", styles["ReportTitle"]),
        Paragraph(
            "A consolidated record of withdrawals performed by Executives from the Manager's sales-close account.",
            styles["SmallMuted"],
        ),
        Spacer(1, 6 * mm),
    ]

    manager_name = manager_doc.get("name") or manager_doc.get("username") or "Manager"
    manager_meta = [
        ["MANAGER", manager_name, "BRANCH", manager_doc.get("branch") or "Not assigned"],
        ["MANAGER ID", str(manager_doc["_id"]), "PHONE", manager_doc.get("phone") or manager_doc.get("phone_number") or "N/A"],
        ["REPORTING PERIOD", period_label, "GENERATED BY", exec_doc.get("name") or "Executive"],
        ["REPORT REFERENCE", report_reference, "CURRENT AVAILABLE BALANCE", _money(current_balance)],
    ]
    meta_table = Table(manager_meta, colWidths=[33 * mm, 77 * mm, 48 * mm, 92 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF1F8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EAF1F8")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#37556F")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#37556F")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTNAME", (3, 0), (3, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D7DEE8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([meta_table, Spacer(1, 7 * mm)])

    amounts = [row["amount"] for row in transactions]
    overall_total = sum(amounts)
    overall_count = len(transactions)
    overall_stats = [
        ["TOTAL WITHDRAWN", "TRANSACTIONS", "AVERAGE", "HIGHEST SINGLE", "ACTIVE DAYS"],
        [
            _money(overall_total),
            f"{overall_count:,}",
            _money(overall_total / overall_count if overall_count else 0),
            _money(max(amounts) if amounts else 0),
            f"{len({row['at'].date() for row in transactions if row['at'] != datetime.min}):,}",
        ],
    ]
    summary = Table(overall_stats, colWidths=[52 * mm] * 5)
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123B69")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F5F8FC")),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#123B69")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    dated_transactions = [row for row in transactions if row["at"] != datetime.min]
    detail_summary = Table([
        ["LOWEST SINGLE", "FIRST WITHDRAWAL", "MOST RECENT WITHDRAWAL"],
        [
            _money(min(amounts) if amounts else 0),
            min((row["at"] for row in dated_transactions), default=datetime.min).strftime("%d %b %Y")
                if dated_transactions else "N/A",
            max((row["at"] for row in dated_transactions), default=datetime.min).strftime("%d %b %Y")
                if dated_transactions else "N/A",
        ],
    ], colWidths=[86.7 * mm] * 3)
    detail_summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1F8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#37556F")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([summary, Spacer(1, 2 * mm), detail_summary, Spacer(1, 7 * mm)])

    monthly: Dict[Tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in transactions:
        if row["at"] != datetime.min:
            monthly[(row["at"].year, row["at"].month)].append(row)

    if not transactions:
        story.extend([
            Paragraph("Withdrawal history", styles["SectionTitle"]),
            Paragraph("No Executive withdrawals were recorded for this reporting period.", styles["BodyText"]),
        ])
    else:
        comparison_data = [["MONTH", "TOTAL WITHDRAWN", "TRANSACTIONS", "AVERAGE", "CHANGE"]]
        ordered_months = sorted(monthly.keys(), reverse=True)
        for index, month_key in enumerate(ordered_months):
            rows = monthly[month_key]
            total = sum(row["amount"] for row in rows)
            previous_total = None
            older_key = (month_key[0] - 1, 12) if month_key[1] == 1 else (month_key[0], month_key[1] - 1)
            if older_key in monthly:
                previous_total = sum(row["amount"] for row in monthly[older_key])
            change = "N/A"
            if previous_total:
                change = f"{((total - previous_total) / previous_total) * 100:+.1f}%"
            comparison_data.append([
                f"{calendar.month_name[month_key[1]]} {month_key[0]}",
                _money(total), str(len(rows)), _money(total / len(rows)), change,
            ])

        story.append(Paragraph("Monthly comparison", styles["SectionTitle"]))
        comparison = Table(comparison_data, colWidths=[55 * mm, 58 * mm, 43 * mm, 58 * mm, 38 * mm], repeatRows=1)
        comparison.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FB")]),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7DEE8")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([comparison, PageBreak()])

        for month_index, month_key in enumerate(ordered_months):
            rows = monthly[month_key]
            month_title = f"{calendar.month_name[month_key[1]]} {month_key[0]}"
            month_total = sum(row["amount"] for row in rows)
            daily_totals: Dict[Any, float] = defaultdict(float)
            for row in rows:
                daily_totals[row["at"].date()] += row["amount"]
            highest_day, highest_day_total = max(daily_totals.items(), key=lambda item: item[1])
            month_amounts = [row["amount"] for row in rows]
            older_key = (month_key[0] - 1, 12) if month_key[1] == 1 else (month_key[0], month_key[1] - 1)
            older_total = sum(row["amount"] for row in monthly.get(older_key, []))
            month_change = f"{((month_total - older_total) / older_total) * 100:+.1f}%" if older_total else "N/A"
            month_stats = [
                ["MONTH TOTAL", "TRANSACTIONS", "AVERAGE", "HIGHEST", "LOWEST", "ACTIVE DAYS", "CHANGE"],
                [
                    _money(month_total), str(len(rows)), _money(month_total / len(rows)),
                    _money(max(month_amounts)),
                    _money(min(month_amounts)), str(len(daily_totals)), month_change,
                ],
            ]
            story.append(Paragraph(month_title, styles["ReportTitle"]))
            stats_table = Table(month_stats, colWidths=[37.1 * mm] * 7)
            stats_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123B69")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EEF4FA")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("FONTSIZE", (0, 1), (-1, 1), 10),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7DEE8")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            highest_day_box = Table([
                ["HIGHEST WITHDRAWAL DAY", highest_day.strftime("%A, %d %B %Y"),
                 "DAY TOTAL", _money(highest_day_total),
                 "TRANSACTIONS", str(sum(1 for row in rows if row["at"].date() == highest_day))],
            ], colWidths=[43 * mm, 63 * mm, 28 * mm, 45 * mm, 34 * mm, 47 * mm])
            highest_day_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8E6")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#694D00")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E7CF8B")),
                ("ALIGN", (2, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.extend([stats_table, Spacer(1, 2 * mm), highest_day_box, Spacer(1, 5 * mm)])

            transaction_data = [["DATE", "TIME", "REFERENCE", "EXECUTIVE", "AMOUNT", "SOURCE DAYS", "NOTE"]]
            for row in sorted(rows, key=lambda item: item["at"]):
                transaction_data.append([
                    row["at"].strftime("%d %b %Y"),
                    row["at"].strftime("%I:%M %p"),
                    Paragraph(row["reference"], styles["Cell"]),
                    Paragraph(row["executive_name"], styles["Cell"]),
                    _money(row["amount"]),
                    str(len({source["date"] for source in row["sources"]})),
                    Paragraph(row["note"] or "-", styles["Cell"]),
                ])
            tx_table = Table(
                transaction_data,
                colWidths=[29 * mm, 23 * mm, 51 * mm, 43 * mm, 34 * mm, 24 * mm, 56 * mm],
                repeatRows=1,
            )
            tx_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("ALIGN", (4, 1), (5, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D7DEE8")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(tx_table)
            if month_index < len(ordered_months) - 1:
                story.append(PageBreak())

        story.extend([PageBreak(), Paragraph("Audit details", styles["ReportTitle"])])
        story.append(Paragraph(
            "The source rows below explain which dated ledger balances funded each consolidated Executive withdrawal.",
            styles["SmallMuted"],
        ))
        story.append(Spacer(1, 4 * mm))
        audit_data = [["TRANSACTION", "ACTION DATE", "SOURCE LEDGER DATE", "SOURCE AMOUNT"]]
        for row in sorted(transactions, key=lambda item: item["at"], reverse=True):
            for source in row["sources"]:
                audit_data.append([
                    Paragraph(row["reference"], styles["Cell"]),
                    row["at"].strftime("%d %b %Y %I:%M %p"),
                    source["date"] or "Unknown",
                    _money(source["amount"]),
                ])
        audit_table = Table(audit_data, colWidths=[75 * mm, 65 * mm, 60 * mm, 55 * mm], repeatRows=1)
        audit_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (3, 1), (3, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D7DEE8")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(audit_table)

    doc.build(
        story,
        onFirstPage=lambda canvas, pdf_doc: _pdf_header_footer(canvas, pdf_doc, report_reference),
        onLaterPages=lambda canvas, pdf_doc: _pdf_header_footer(canvas, pdf_doc, report_reference),
    )
    buffer.seek(0)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in manager_name).strip("_") or "manager"
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{safe_name}_withdrawal_report_{now.strftime('%Y%m%d')}.pdf",
    )
