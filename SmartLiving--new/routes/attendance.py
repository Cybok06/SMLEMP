from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request

from db import db
from services.manager_suite_scope import scoped_db
db = scoped_db(db)
from login import get_current_identity, role_required
from pymongo.errors import DuplicateKeyError
from services.work_time import ROLES, local_now, location, schedule_snapshot, settings, range_dates, summary, leave_rows


attendance_bp = Blueprint("attendance", __name__)
attendance_col = db["attendance_workdays"]
users_col = db["users"]

ATTENDANCE_ACTIONS = {
    "report_to_work": "Report to Work",
    "lunch_start": "Lunch Start",
    "lunch_return": "Lunch Return",
    "leave_office": "Leave Office",
}

ALLOWED_ROLES = set(ROLES)


def _ensure_indexes() -> None:
    try:
        attendance_col.create_index([("user_id", 1), ("attendance_date", 1)], unique=True)
        attendance_col.create_index([("attendance_date", -1), ("role", 1)])
        attendance_col.create_index([("updated_at", -1)])
    except Exception:
        pass


_ensure_indexes()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_str() -> str:
    return local_now(db, _utc_now()).date().isoformat()


def _safe_object_id(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _fetch_user_doc(user_id: str) -> Dict[str, Any]:
    oid = _safe_object_id(user_id)
    if oid is not None:
        doc = users_col.find_one({"_id": oid}) or {}
        if doc:
            return doc
    return users_col.find_one({"_id": user_id}) or {}


def _sidebar_template_for_role(role: str) -> str:
    return {
        "agent": "side_bar.html",
        "manager": "manager_sidebar.html",
        "inventory": "inventory_sidebar.html",
        "admin": "admin_sidebar.html",
        "executive": "executive_sidebar.html",
        "hr": "hr_pages/hr_shell.html",
    }.get(role, "side_bar.html")


def _compute_status(doc: Dict[str, Any]) -> Dict[str, Any]:
    report = doc.get("report_to_work_at")
    lunch_start = doc.get("lunch_start_at")
    lunch_return = doc.get("lunch_return_at")
    leave = doc.get("leave_office_at")

    if leave:
        label, tone = "Closed for Day", "success"
    elif lunch_start and not lunch_return:
        label, tone = "At Lunch", "warning"
    elif report:
        label, tone = "At Work", "primary"
    else:
        label, tone = "Not Reported", "muted"

    next_action = None
    if not report:
        next_action = "report_to_work"
    elif lunch_start and not lunch_return:
        next_action = "lunch_return"
    elif not lunch_start:
        next_action = "lunch_start"
    elif not leave:
        next_action = "leave_office"

    return {
        "label": label,
        "tone": tone,
        "next_action": next_action,
        "is_closed": bool(leave),
    }


def _serialize_doc(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not doc:
        fallback = {
            "attendance_date": _today_str(),
            "report_to_work_at": "",
            "lunch_start_at": "",
            "lunch_return_at": "",
            "leave_office_at": "",
            "events": [],
        }
        fallback["status"] = _compute_status(fallback)
        return fallback

    data = {
        "id": str(doc.get("_id") or ""),
        "attendance_date": doc.get("attendance_date") or "",
        "report_to_work_at": doc.get("report_to_work_at") or "",
        "lunch_start_at": doc.get("lunch_start_at") or "",
        "lunch_return_at": doc.get("lunch_return_at") or "",
        "leave_office_at": doc.get("leave_office_at") or "",
        "events": doc.get("events") or [],
        "role": doc.get("role") or "",
        "branch": doc.get("branch") or "",
        "schedule": doc.get("schedule") or {},
    }
    data["status"] = _compute_status(data)
    return data


def _validate_transition(doc: Dict[str, Any], action: str) -> Optional[str]:
    if action == "report_to_work":
        if doc.get("report_to_work_at"):
            return "You have already reported to work today."
        return None

    if action == "lunch_start":
        if not doc.get("report_to_work_at"):
            return "Report to work first."
        if doc.get("leave_office_at"):
            return "Your workday is already closed."
        if doc.get("lunch_start_at") and not doc.get("lunch_return_at"):
            return "Lunch has already started."
        if doc.get("lunch_return_at"):
            return "Lunch has already been completed for today."
        return None

    if action == "lunch_return":
        if not doc.get("lunch_start_at"):
            return "Lunch has not started yet."
        if doc.get("lunch_return_at"):
            return "Lunch return has already been logged."
        if doc.get("leave_office_at"):
            return "Your workday is already closed."
        return None

    if action == "leave_office":
        if not doc.get("report_to_work_at"):
            return "Report to work first."
        if doc.get("leave_office_at"):
            return "Leave office has already been logged today."
        return None

    return "Unsupported attendance action."


def _base_today_doc(ident: Dict[str, Any], user_doc: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now()
    return {
        "user_id": str(ident.get("user_id") or ""),
        "name": user_doc.get("name") or ident.get("name") or ident.get("username") or "User",
        "username": user_doc.get("username") or ident.get("username") or "",
        "role": str(ident.get("role") or "").strip().lower(),
        "branch": user_doc.get("branch") or user_doc.get("store_name") or user_doc.get("store") or "",
        "attendance_date": _today_str(),
        "report_to_work_at": "",
        "lunch_start_at": "",
        "lunch_return_at": "",
        "leave_office_at": "",
        "events": [],
        "created_at": now,
        "updated_at": now,
    }


@attendance_bp.route("/attendance", methods=["GET"])
@attendance_bp.route("/log-time", methods=["GET"])
@role_required(*ROLES)
def attendance_page():
    ident = get_current_identity()
    role = str(ident.get("role") or "").strip().lower()
    if role not in ALLOWED_ROLES:
        return "Forbidden", 403

    user_doc = _fetch_user_doc(str(ident.get("user_id") or ""))
    return render_template(
        "attendance_page.html",
        attendance_role=role,
        sidebar_template=_sidebar_template_for_role(role),
        page_title="Log Time",
        active_page="attendance",
        user_name=user_doc.get("name") or ident.get("name") or ident.get("username") or "User",
        user_branch=user_doc.get("branch") or user_doc.get("store_name") or user_doc.get("store") or "",
        user_position=user_doc.get("position") or user_doc.get("department") or "",
        today_date=_today_str(),
        attendance_actions=ATTENDANCE_ACTIONS,
    )


@attendance_bp.route("/attendance/api/today", methods=["GET"])
@role_required(*ROLES)
def attendance_today():
    ident = get_current_identity()
    doc = attendance_col.find_one(
        {"user_id": str(ident.get("user_id") or ""), "attendance_date": _today_str()}
    )
    return jsonify(ok=True, attendance=_serialize_doc(doc))


@attendance_bp.route("/attendance/api/history", methods=["GET"])
@role_required(*ROLES)
def attendance_history():
    ident = get_current_identity()
    try:
        limit = max(1, min(31, int(request.args.get("limit", "10"))))
    except Exception:
        limit = 10

    cursor = attendance_col.find(
        {"user_id": str(ident.get("user_id") or "")},
        {
            "attendance_date": 1,
            "report_to_work_at": 1,
            "lunch_start_at": 1,
            "lunch_return_at": 1,
            "leave_office_at": 1,
            "events": 1,
            "schedule": 1,
            "role": 1,
            "branch": 1,
        },
    ).sort("attendance_date", -1).limit(limit)
    return jsonify(ok=True, history=[_serialize_doc(row) for row in cursor])


@attendance_bp.route("/attendance/api/action", methods=["POST"])
@role_required(*ROLES)
def attendance_action():
    ident = get_current_identity()
    role = str(ident.get("role") or "").strip().lower()
    if role not in ALLOWED_ROLES:
        return jsonify(ok=False, message="Forbidden"), 403

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(ok=False, message="Invalid attendance record."), 400
    action = str(payload.get("action") or "").strip().lower()
    if action not in ATTENDANCE_ACTIONS:
        return jsonify(ok=False, message="Invalid attendance action."), 400
    user_id = str(ident.get("user_id") or "")
    user_doc = _fetch_user_doc(user_id)
    now = _utc_now()
    today = local_now(db, now).date().isoformat()
    stamp = now.isoformat().replace("+00:00", "Z")
    try:
        geo = location(payload, stamp)
    except ValueError as error:
        return jsonify(ok=False, message=str(error)), 400
    # Unique daily identity and atomic version checks prevent repeated/concurrent logs.
    attendance_col.create_index([("user_id",1),("attendance_date",1)],unique=True)
    existing = attendance_col.find_one({"user_id":user_id,"attendance_date":today})
    working = dict(existing or _base_today_doc(ident,user_doc))
    error = _validate_transition(working,action)
    if error:
        return jsonify(ok=False,message=error,attendance=_serialize_doc(working)),409
    event={"action":action,"label":ATTENDANCE_ACTIONS[action],"at":stamp,"location":geo}
    fields={f"{action}_at":stamp,"updated_at":now,"role":role,"branch":user_doc.get("branch") or user_doc.get("store_name") or user_doc.get("store") or ""}
    if action == "report_to_work":
        fields["schedule"] = schedule_snapshot(db,now)
    if existing:
        version=existing.get("version")
        query={"_id":existing["_id"],"version":version if version is not None else {"$exists":False}}
        result=attendance_col.update_one(query,{"$set":fields,"$inc":{"version":1},"$push":{"events":{"$each":[event],"$position":0}}})
        if not result.modified_count:
            return jsonify(ok=False,message="Attendance changed. Refresh before logging again."),409
    else:
        working.update(fields);working["attendance_date"]=today;working["version"]=1;working["events"]=[event]
        try:
            attendance_col.insert_one(working)
        except DuplicateKeyError:
            return jsonify(ok=False,message="This workday was just logged. Refresh to see it."),409
    saved=attendance_col.find_one({"user_id":user_id,"attendance_date":today})
    return jsonify(ok=True,message=f"{ATTENDANCE_ACTIONS[action]} logged successfully.",attendance=_serialize_doc(saved))


@attendance_bp.get("/attendance/api/summary")
@role_required(*ROLES)
def personal_summary():
    ident=get_current_identity();user=_fetch_user_doc(str(ident["user_id"]))
    if not user:return jsonify(ok=False,message="Employee not found."),404
    try:
        start,end=range_dates(db,request.args)
    except ValueError as error:
        return jsonify(ok=False,message=str(error)),400
    return jsonify(ok=True,summary=summary(db,user,start,end),settings=settings(db),leaves=leave_rows(user))

@attendance_bp.post("/attendance/api/leave")
@role_required(*ROLES)
def request_personal_leave():
    from hr_backend.hr_attendance import create_leave_request
    return create_leave_request(self_service=True)

@attendance_bp.get("/hr/work-time")
@attendance_bp.get("/admin/work-time")
@attendance_bp.get("/executive/work-time")
@role_required("hr","admin","executive","manager")
def work_time_page():
    ident=get_current_identity()
    suite_manager = ident["role"] == "manager" and request.path == "/hr/work-time"
    if request.path.split("/")[1] != ident["role"] and not suite_manager:return "Forbidden",403
    if ident["role"] == "hr" or suite_manager:
        from services.manager_suite_scope import manager_scope
        manager_scope(db)
        return render_template("hr_pages/hr_shell.html",hr_content_template="work_time_management.html",work_time_role="hr",active_page="work_time")
    return render_template("work_time_page.html",work_time_role=ident["role"])

@attendance_bp.route("/attendance/api/settings",methods=["GET","POST"])
@role_required(*ROLES)
def work_time_settings():
    ident=get_current_identity()
    if request.method=="GET":return jsonify(ok=True,settings=settings(db))
    if ident["role"] not in {"admin","executive"}:return jsonify(ok=False,message="Only Admin and Executive can configure work hours."),403
    payload=request.get_json(silent=True) or {}
    try:
        if not isinstance(payload,dict):raise ValueError("Invalid settings.")
        from datetime import time
        start,end=payload.get("start_time"),payload.get("end_time")
        if not isinstance(start,str) or not isinstance(end,str) or len(start)!=5 or len(end)!=5 or time.fromisoformat(end)<=time.fromisoformat(start):raise ValueError("Use valid start and end times. End time must be after start time.")
        grace=payload.get("grace_minutes")
        if isinstance(grace,bool) or not isinstance(grace,int) or not 0<=grace<=120:raise ValueError("Grace period must be 0 to 120 minutes.")
        days=payload.get("workdays")
        if not isinstance(days,list) or not days or any(isinstance(d,bool) or not isinstance(d,int) or d not in range(7) for d in days):raise ValueError("Select at least one valid workday.")
    except (ValueError,TypeError) as error:
        return jsonify(ok=False,message=str(error)),400
    old=db.work_time_settings.find_one({"_id":"organization"})
    now=_utc_now();today=local_now(db,now).date().isoformat()
    policy={"start_time":start,"end_time":end,"grace_minutes":grace,"workdays":sorted(set(days)),"timezone":"Africa/Accra","tracking_from":(old or {}).get("tracking_from") or today}
    audit={**policy,"effective_date":today,"created_at":now.isoformat(),"updated_by":{"user_id":ident["user_id"],"name":ident.get("name"),"role":ident["role"]}}
    query={"_id":"organization","version":old.get("version",0) if old else {"$exists":False}}
    try:
        result=db.work_time_settings.update_one(query,{"$set":policy,"$push":{"history":audit},"$inc":{"version":1}},upsert=old is None)
    except DuplicateKeyError:
        return jsonify(ok=False,message="Settings changed. Refresh and try again."),409
    if old and not result.modified_count:return jsonify(ok=False,message="Settings changed. Refresh and try again."),409
    return jsonify(ok=True,settings=settings(db))

@attendance_bp.get("/attendance/api/analytics")
@role_required("hr","admin","executive","manager")
def work_time_analytics():
    try:start,end=range_dates(db,request.args)
    except ValueError as error:return jsonify(ok=False,message=str(error)),400
    query={"attendance_date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}
    records=list(attendance_col.find(query).sort("attendance_date",-1));by_user={}
    for doc in records:by_user.setdefault(doc["user_id"],[]).append(doc)
    rows=[];leaves=[]
    policy_document=db.work_time_settings.find_one({"_id":"organization"}) or {}
    for user in users_col.find({"is_deleted":{"$ne":True},"is_active":{"$ne":False},"account_locked":{"$ne":True},"role":{"$in":list(ROLES)}}):
        rows.append({"user_id":str(user["_id"]),"name":user.get("name") or user.get("username","Staff"),"role":user.get("role"),"branch":str(user.get("branch") or user.get("store_name") or ""),**summary(db,user,start,end,by_user.get(str(user["_id"]),[]),policy_document)})
        leaves.extend(l for l in leave_rows(user) if l.get("status")=="Pending")
    totals={key:sum(row[key] for row in rows) for key in ("reported_days","closed_days","late_days","approved_absence_days","approved_absence_occasions","missing_clock_in_days","pending_leave_requests")}
    if request.args.get('summary_only') == '1':
        return jsonify(ok=True, totals=totals, report_from=start.isoformat(), report_to=end.isoformat())
    serialized=[]
    for doc in records:
        serialized.append({**_serialize_doc(doc),"name":doc.get("name","Staff"),"user_id":doc["user_id"]})
    totals={key:sum(row[key] for row in rows) for key in ("reported_days","closed_days","late_days","approved_absence_days","approved_absence_occasions","missing_clock_in_days","pending_leave_requests")}
    return jsonify(ok=True,employees=rows,records=serialized,totals=totals,leaves=leaves,settings=settings(db),report_from=start.isoformat(),report_to=end.isoformat())
