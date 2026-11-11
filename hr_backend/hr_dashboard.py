# hr_backend/hr_dashboard.py
from __future__ import annotations

from datetime import datetime, date
import logging
from typing import Dict, Any, List, Tuple
from bson import ObjectId

from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)

hr_bp = Blueprint("hr", __name__, url_prefix="/hr")
logger = logging.getLogger(__name__)

# Collections
users_col = db["users"]
cases_col = db["hr_cases"]     # ✅ ensure you create/use this collection
exits_col = db["hr_exits"]     # ✅ ensure you create/use this collection
exited_users_col = db["Exited"]


def _is_ajax(req) -> bool:
    return req.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"


def _hr_access_guard() -> bool:
    role = (session.get("role") or "").lower().strip()
    from services.manager_suite_scope import manager_scope
    manager_scope(db)  # Reject unassigned managers before rendering any suite page.
    if session.get("hr_id") or role == "hr":
        return True
    if session.get("executive_id") or session.get("admin_id"):
        return True
    if session.get('manager_id') or role == 'manager':
        from login import get_current_identity
        identity = get_current_identity()
        return bool(identity.get('is_authenticated') and identity.get('role') == 'manager')
    return False


# -----------------------------
# Helpers
# -----------------------------
def _safe_str(x) -> str:
    return (x or "").strip()


def _today() -> date:
    # Server-side date (good enough); if you later want Ghana TZ exact, we can use zoneinfo.
    return datetime.utcnow().date()


def _next_birthday_info(dob: datetime, today: date) -> Tuple[int, date]:
    """
    Returns (days_remaining, next_bday_date).
    """
    mm = dob.month
    dd = dob.day
    year = today.year

    try:
        candidate = date(year, mm, dd)
    except ValueError:
        # Handle Feb 29 -> fallback to Feb 28 (common HR handling)
        if mm == 2 and dd == 29:
            candidate = date(year, 2, 28)
        else:
            candidate = date(year, mm, min(dd, 28))

    if candidate < today:
        try:
            candidate = date(year + 1, mm, dd)
        except ValueError:
            if mm == 2 and dd == 29:
                candidate = date(year + 1, 2, 28)
            else:
                candidate = date(year + 1, mm, min(dd, 28))

    return (candidate - today).days, candidate


def _build_user_filter(branch: str = "", role: str = "", status: str = "") -> Dict[str, Any]:
    """
    Common filtering for users.
    - branch: exact match
    - role: exact match
    - status: matches employment_status OR status if provided
    """
    q: Dict[str, Any] = {}

    if branch:
        q["branch"] = branch

    if role:
        q["role"] = role

    if status:
        # status filter matches employment_status primarily
        q["$or"] = [{"employment_status": status}, {"status": status}]

    return q


def _user_pipeline(
    branch: str = "",
    role: str = "",
    status: str = "",
    extra: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """
    Build aggregation pipeline that derives effective_branch:
    - managers use their own branch
    - other roles use their manager's branch when available
    """
    match_q: Dict[str, Any] = _build_user_filter(branch="", role=role, status=status)
    if extra:
        if match_q:
            match_q = {"$and": [match_q, extra]}
        else:
            match_q = extra

    live_user_q = {
        "is_deleted": {"$ne": True},
        "employment_status": {"$ne": "Exited"},
        "status": {"$ne": "Exited"},
    }
    match_q = {"$and": [match_q, live_user_q]} if match_q else live_user_q

    pipeline: List[Dict[str, Any]] = [
        {"$match": match_q},
        {
            "$addFields": {
                "manager_id_str": {"$toString": {"$ifNull": ["$manager_id", ""]}},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "let": {"mgr_id_str": "$manager_id_str"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$role", "manager"]},
                                    {"$eq": [{"$toString": "$_id"}, "$$mgr_id_str"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"branch": 1}},
                ],
                "as": "manager_doc",
            }
        },
        {
            "$addFields": {
                "manager_branch": {"$arrayElemAt": ["$manager_doc.branch", 0]},
            }
        },
        {
            "$addFields": {
                "effective_branch": {
                    "$cond": [
                        {"$eq": ["$role", "manager"]},
                        {"$ifNull": ["$branch", "Unassigned"]},
                        {"$ifNull": ["$manager_branch", "$branch"]},
                    ]
                },
            }
        },
        {
            "$addFields": {
                "effective_branch": {
                    "$let": {
                        "vars": {"b": {"$trim": {"input": {"$ifNull": ["$effective_branch", ""]}}}},
                        "in": {
                            "$cond": [
                                {
                                    "$or": [
                                        {"$eq": ["$$b", ""]},
                                        {"$in": [{"$toLower": "$$b"}, ["branch", "select branch", "select a branch"]]},
                                    ]
                                },
                                "Unassigned",
                                "$$b",
                            ]
                        },
                    }
                }
            }
        },
    ]

    if branch:
        pipeline.append({"$match": {"effective_branch": branch}})

    return pipeline


def _count_from_pipeline(pipeline: List[Dict[str, Any]]) -> int:
    rows = list(users_col.aggregate([*pipeline, {"$count": "count"}]))
    return int(rows[0]["count"]) if rows else 0


def _case_filter(branch: str = "") -> Dict[str, Any]:
    q: Dict[str, Any] = {}
    if branch:
        q["branch"] = branch
    return q


def _exit_filter(branch: str = "") -> Dict[str, Any]:
    q: Dict[str, Any] = {}
    if branch:
        q["employee.branch"] = branch  # if you embed employee snapshot in exit docs
        # If not embedded, remove the line above and store branch in exit doc root.
    return q


# -------------------------------------------------------------------
# HR DASHBOARD
# -------------------------------------------------------------------
@hr_bp.route("/dashboard")
def dashboard():
    if not _hr_access_guard():
        return redirect(url_for("login.login"))

    # Branch list for selector (from managers only)
    branch_list = users_col.distinct("branch", {"role": "manager", "branch": {"$ne": ""}})
    branch_list = sorted([b for b in branch_list if b])

    # Default branch: if manager has one, use it; else "All"
    current_branch = request.args.get("branch", "").strip()
    from services.manager_suite_scope import manager_scope
    branch_scope = manager_scope(db)
    if branch_scope:
        current_branch = branch_scope.branch
        branch_list = [branch_scope.branch]
    if not current_branch and session.get("manager_id"):
        # optional: infer from manager account
        manager_id = str(session.get('manager_id'))
        manager_key = ObjectId(manager_id) if ObjectId.is_valid(manager_id) else manager_id
        mgr = users_col.find_one({"_id": manager_key})
        if mgr and mgr.get("branch"):
            current_branch = mgr.get("branch")

    if _is_ajax(request):
        return render_template(
            "hr_pages/partials/hr_dashboard_inner.html",
            hr_branches=branch_list,
            current_branch=current_branch,
        )

    return render_template(
        "hr_pages/hr_dashboard.html",
        hr_branches=branch_list,
        current_branch=current_branch,
    )


@hr_bp.route("/dashboard/data")
def dashboard_data():
    if not _hr_access_guard():
        return jsonify({"ok": False, "message": "Not authorized"}), 401

    branch = _safe_str(request.args.get("branch"))
    role = _safe_str(request.args.get("role"))
    status = _safe_str(request.args.get("status"))

    today = _today()

    # ---------------- Users KPIs ----------------
    # Branch is derived from managers: managers use their own branch, and agents
    # inherit the branch of the manager referenced by manager_id.
    total_employees = _count_from_pipeline(_user_pipeline(branch=branch, role=role, status=status))

    active_employees = _count_from_pipeline(
        _user_pipeline(branch=branch, role=role, status=status, extra={"$or": [{"employment_status": "Active"}, {"status": "Active"}]})
    )

    on_probation = _count_from_pipeline(
        _user_pipeline(
            branch=branch,
            role=role,
            status=status,
            extra={"$or": [{"status": "Probation"}, {"employment_status": "Probation"}, {"probation.status": "Active"}]},
        )
    )

    recruitment_in_progress = _count_from_pipeline(
        _user_pipeline(
            branch=branch,
            role=role,
            status=status,
            extra={"recruitment_stage": {"$exists": True, "$nin": ["Documentation Approved", "Completed"]}},
        )
    )

    # ---------------- Cases ----------------
    c_q = _case_filter(branch=branch)
    # open cases = not Closed/Resolved
    open_cases_q = dict(c_q)
    open_cases_q["status"] = {"$nin": ["Closed", "Resolved"]}
    open_cases = cases_col.count_documents(open_cases_q)

    # Top 10 open cases
    open_cases_list: List[Dict[str, Any]] = []
    for d in cases_col.find(open_cases_q).sort("created_at", -1).limit(10):
        open_cases_list.append(
            {
                "case_id": str(d.get("_id")),
                "title": d.get("title") or d.get("subject") or "Case",
                "employee": d.get("employee_name") or d.get("employee") or "—",
                "branch": d.get("branch") or "—",
                "priority": d.get("priority") or "Normal",
                "status": d.get("status") or "Open",
                "created_at": (d.get("created_at") or "").isoformat() if isinstance(d.get("created_at"), datetime) else str(d.get("created_at") or ""),
            }
        )

    # cases by status
    cases_by_status: Dict[str, int] = {}
    for row in cases_col.aggregate(
        [
            {"$match": c_q},
            {"$group": {"_id": {"$ifNull": ["$status", "Unknown"]}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        cases_by_status[str(row["_id"])] = int(row["count"])

    # ---------------- Exits ----------------
    e_q = {}
    if branch:
        # safer: store branch on exit root in future; for now try both
        e_q["$or"] = [{"branch": branch}, {"employee.branch": branch}]

    exits_in_progress_q = dict(e_q)
    exits_in_progress_q["status"] = {"$nin": ["Closed"]}
    exits_in_progress = exits_col.count_documents(exits_in_progress_q)

    exited_q: Dict[str, Any] = {"is_deleted": {"$ne": True}}
    if branch:
        exited_q["$or"] = [{"branch": branch}, {"store_name": branch}, {"store": branch}]
    if role:
        exited_q["role"] = role
    exited_employees = exited_users_col.count_documents(exited_q)

    # pending clearance / pending transfer (best-effort fields)
    pending_clearance = exits_col.count_documents({
        "$and": [
            exits_in_progress_q,
            {"$or": [{f"clearance.checks.{key}": {"$ne": True}} for key in (
                "cases_checked", "debts_checked", "assets_returned", "access_disabled",
                "documents_archived", "customers_transferred",
            )]},
        ]
    })
    pending_transfer = exits_col.count_documents(
        {**exits_in_progress_q, "status": {"$nin": ["Closed"]}, "transfers.pending_count": {"$gt": 0}}
    )

    # ---------------- Charts ----------------
    employees_by_branch: List[Dict[str, Any]] = []
    branch_pipeline = [
        *_user_pipeline(branch=branch, role=role, status=status),
        {"$match": {"effective_branch": {"$ne": "Unassigned"}}},
        {"$group": {"_id": "$effective_branch", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    try:
        for row in users_col.aggregate(branch_pipeline):
            employees_by_branch.append({"branch": str(row["_id"]), "count": int(row["count"])})
    except Exception:
        logger.exception("HR dashboard branch aggregation failed")
        return jsonify(ok=False, message="Dashboard branch aggregation failed."), 500

    employees_by_role: List[Dict[str, Any]] = []
    role_pipeline = [
        *_user_pipeline(branch=branch, role=role, status=status),
        {
            "$addFields": {
                "role_norm": {
                    "$let": {
                        "vars": {
                            "r": {"$toLower": {"$trim": {"input": {"$ifNull": ["$role", ""]}}}}
                        },
                        "in": {
                            "$let": {
                                "vars": {
                                    "parts": {
                                        "$filter": {
                                            "input": {"$split": ["$$r", " "]},
                                            "as": "p",
                                            "cond": {"$ne": ["$$p", ""]},
                                        }
                                    }
                                },
                                "in": {
                                    "$cond": [
                                        {"$gt": [{"$size": "$$parts"}, 0]},
                                        {
                                            "$reduce": {
                                                "input": "$$parts",
                                                "initialValue": "",
                                                "in": {
                                                    "$cond": [
                                                        {"$eq": ["$$value", ""]},
                                                        "$$this",
                                                        {"$concat": ["$$value", "_", "$$this"]},
                                                    ]
                                                },
                                            }
                                        },
                                        "unassigned",
                                    ]
                                },
                            }
                        },
                    }
                }
            }
        },
        {"$group": {"_id": "$role_norm", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    try:
        for row in users_col.aggregate(role_pipeline):
            employees_by_role.append({"role": str(row["_id"]), "count": int(row["count"])})
    except Exception:
        logger.exception("HR dashboard role aggregation failed")
        return jsonify(ok=False, message="Dashboard role aggregation failed."), 500

    recruitment_pipeline: Dict[str, int] = {}
    for row in users_col.aggregate(
        [
            *_user_pipeline(branch=branch, role=role, status=status, extra={"recruitment_stage": {"$exists": True}}),
            {"$group": {"_id": {"$ifNull": ["$recruitment_stage", "Unknown"]}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
    ):
        recruitment_pipeline[str(row["_id"])] = int(row["count"])

    # ---------------- Birthdays Top 10 ----------------
    upcoming_birthdays: List[Dict[str, Any]] = []
    # dob is stored as datetime
    candidates = list(
        users_col.aggregate(
            [
                *_user_pipeline(branch=branch, role=role, status=status, extra={"dob": {"$type": "date"}}),
                {
                    "$project": {
                        "name": 1,
                        "branch": "$effective_branch",
                        "role": 1,
                        "dob": 1,
                        "employee_code": 1,
                        "image_url": 1,
                    }
                },
                {"$limit": 800},
            ]
        )
    )

    rows = []
    for u in candidates:
        dob = u.get("dob")
        if not isinstance(dob, datetime):
            continue
        days_remaining, next_date = _next_birthday_info(dob, today)
        rows.append((days_remaining, next_date, u))

    rows.sort(key=lambda x: x[0])
    for days_remaining, next_date, u in rows[:10]:
        upcoming_birthdays.append(
            {
                "user_id": str(u.get("_id")),
                "name": u.get("name") or "—",
                "branch": u.get("branch") or "—",
                "role": (u.get("role") or "—").title(),
                "birthday": next_date.strftime("%b %d"),
                "days_remaining": int(days_remaining),
                "employee_code": u.get("employee_code") or "",
                "image_url": u.get("image_url") or "",
            }
        )

    return jsonify(
        {
            "ok": True,
            "kpis": {
                "total_employees": total_employees,
                "active_employees": active_employees,
                "on_probation": on_probation,
                "recruitment_in_progress": recruitment_in_progress,
                "open_cases": open_cases,
                "exits_in_progress": exits_in_progress,
                "exited_employees": exited_employees,
            },
            "charts": {
                "employees_by_branch": employees_by_branch,
                "employees_by_role": employees_by_role,
                "recruitment_pipeline": recruitment_pipeline,
                "cases_by_status": cases_by_status,
            },
            "open_cases": open_cases_list,
            "upcoming_birthdays": upcoming_birthdays,
            "queues": {
                "probation_due": 0,  # (we can wire this when probation end_date exists reliably)
                "exit_pending_clearance": pending_clearance,
                "exit_pending_transfer": pending_transfer,
            },
        }
    )
