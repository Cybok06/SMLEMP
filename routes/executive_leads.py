from flask import Blueprint, render_template, request, redirect, url_for, jsonify, Response
from bson import ObjectId
from datetime import datetime
import re

from db import db
from login import role_required

executive_leads_bp = Blueprint("executive_leads", __name__, url_prefix="/executive/leads")

customers_col = db["customers"]
users_col = db["users"]


def _safe_int(val, default=0):
    try:
        return int(val)
    except Exception:
        return default


def _safe_oid(val):
    try:
        return ObjectId(str(val))
    except Exception:
        return None


def _normalize_start(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _normalize_end(dt):
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def _month_range(year, month):
    start = datetime(year, month, 1, 0, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, 0)
    end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _parse_date_range(args):
    now = datetime.utcnow()
    default_start, default_end = _month_range(now.year, now.month)

    month_param = args.get("month")
    year_param = args.get("year")
    start_str = (args.get("start") or "").strip()
    end_str = (args.get("end") or "").strip()

    start_dt = default_start
    end_dt = default_end

    if month_param and year_param:
        try:
            y = int(year_param)
            m = int(month_param)
            if 1 <= m <= 12:
                start_dt, end_dt = _month_range(y, m)
        except Exception:
            start_dt, end_dt = default_start, default_end
    elif start_str or end_str:
        try:
            if start_str:
                start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            if end_str:
                end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        except Exception:
            start_dt, end_dt = default_start, default_end
        start_dt = _normalize_start(start_dt)
        end_dt = _normalize_end(end_dt)

    if end_dt < start_dt:
        end_dt = start_dt

    return start_dt, end_dt, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def _escape_regex(val):
    try:
        return re.escape(val)
    except Exception:
        return ""


def _agent_ids_for_filters(branch, manager_id, agent_id):
    if agent_id:
        return [agent_id]

    query = {"role": "agent"}
    if manager_id:
        mid = _safe_oid(manager_id)
        if mid is not None:
            query["$or"] = [{"manager_id": mid}, {"manager_id": str(manager_id)}]
        else:
            query["manager_id"] = str(manager_id)
    if branch:
        query["branch"] = branch

    agents = list(users_col.find(query, {"_id": 1}))
    return [str(a.get("_id")) for a in agents if a.get("_id") is not None]


def _user_maps():
    managers = list(users_col.find({"role": "manager"}, {"name": 1, "branch": 1}))
    agents = list(users_col.find({"role": "agent"}, {"name": 1, "branch": 1, "manager_id": 1}))

    manager_map = {str(m.get("_id")): m for m in managers if m.get("_id") is not None}
    agent_map = {str(a.get("_id")): a for a in agents if a.get("_id") is not None}
    branches = sorted({a.get("branch") for a in agents if a.get("branch")})

    return managers, agents, manager_map, agent_map, branches


def _build_match(args, start_dt, end_dt, agent_ids, include_text_search=True):
    match = {
        "lead_stage": {"$in": ["lead", "customer"]},
        "lead_registered_at": {"$gte": start_dt, "$lte": end_dt},
    }

    if agent_ids is not None:
        match["agent_id"] = {"$in": agent_ids} if agent_ids else {"$in": []}

    if include_text_search:
        q = (args.get("q") or "").strip()
        if q:
            esc = _escape_regex(q)
            match["$or"] = [
                {"name": {"$regex": esc, "$options": "i"}},
                {"phone_number": {"$regex": esc, "$options": "i"}},
            ]

    return match


def _converted_expr():
    return {
        "$or": [
            {"$eq": ["$lead_stage", "customer"]},
            {"$ne": ["$lead_converted_at", None]},
        ]
    }


def _build_table_match(base_match, start_dt, end_dt):
    base = dict(base_match)
    created_range = {"lead_registered_at": {"$gte": start_dt, "$lte": end_dt}}
    base.pop("lead_registered_at", None)
    return {"$and": [base, created_range]}


def _rate(total, converted):
    return round((converted / total) * 100, 1) if total else 0.0


def _merge_counts(counts):
    merged = {}
    for row in counts:
        key = row.get("_id")
        key_str = str(key) if key is not None else ""
        if not key_str:
            continue
        agg = merged.setdefault(key_str, {"total": 0, "converted": 0})
        agg["total"] += int(row.get("total", 0) or 0)
        agg["converted"] += int(row.get("converted", 0) or 0)
    return merged


def _json_safe(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@executive_leads_bp.route("/")
@role_required("executive", "admin")
def executive_leads_page():
    managers, agents, _manager_map, _agent_map, branches = _user_maps()

    today = datetime.utcnow()
    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)

    return render_template(
        "executive_leads.html",
        branches=branches,
        managers=[{"id": str(m.get("_id")), "name": m.get("name") or "Manager", "branch": m.get("branch") or "-"} for m in managers],
        agents=[{
            "id": str(a.get("_id")),
            "name": a.get("name") or "Agent",
            "branch": a.get("branch") or "",
            "manager_id": str(a.get("manager_id")) if a.get("manager_id") is not None else "",
        } for a in agents],
        current_year=today.year,
        current_month=today.month,
        default_start=start_str,
        default_end=end_str,
    )


@executive_leads_bp.route("/metrics")
@role_required("executive", "admin")
def executive_leads_metrics():
    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)

    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()

    agent_ids = _agent_ids_for_filters(branch, manager_id, agent_id)

    base_match = _build_match(request.args, start_dt, end_dt, agent_ids, include_text_search=True)
    converted_expr = _converted_expr()

    # KPIs
    kpi_rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$addFields": {"converted": converted_expr}},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "converted": {"$sum": {"$cond": ["$converted", 1, 0]}}
        }}
    ]))
    kpi_row = kpi_rows[0] if kpi_rows else {}
    leads_total = int(kpi_row.get("total", 0) or 0)
    leads_converted = int(kpi_row.get("converted", 0) or 0)
    conversion_rate = _rate(leads_total, leads_converted)

    # by branch
    by_branch_rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$addFields": {
            "agent_oid": {"$convert": {"input": "$agent_id", "to": "objectId", "onError": None, "onNull": None}},
            "converted": converted_expr,
        }},
        {"$lookup": {"from": "users", "localField": "agent_oid", "foreignField": "_id", "as": "agent_doc"}},
        {"$addFields": {"agent_doc": {"$arrayElemAt": ["$agent_doc", 0]}}},
        {"$addFields": {"branch_final": {"$ifNull": ["$agent_doc.branch", "-"]}}},
        {"$group": {
            "_id": "$branch_final",
            "total": {"$sum": 1},
            "converted": {"$sum": {"$cond": ["$converted", 1, 0]}}
        }},
        {"$sort": {"total": -1}},
    ]))

    # by manager
    by_manager_rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$addFields": {
            "agent_oid": {"$convert": {"input": "$agent_id", "to": "objectId", "onError": None, "onNull": None}},
            "converted": converted_expr,
        }},
        {"$lookup": {"from": "users", "localField": "agent_oid", "foreignField": "_id", "as": "agent_doc"}},
        {"$addFields": {"agent_doc": {"$arrayElemAt": ["$agent_doc", 0]}}},
        {"$addFields": {"manager_final": {"$ifNull": ["$agent_doc.manager_id", ""]}}},
        {"$addFields": {"manager_oid": {"$convert": {"input": "$manager_final", "to": "objectId", "onError": None, "onNull": None}}}},
        {"$lookup": {"from": "users", "localField": "manager_oid", "foreignField": "_id", "as": "manager_doc"}},
        {"$addFields": {"manager_doc": {"$arrayElemAt": ["$manager_doc", 0]}}},
        {"$addFields": {"manager_name": {"$ifNull": ["$manager_doc.name", "Unknown"]}, "branch_final": {"$ifNull": ["$manager_doc.branch", "-"]}}},
        {"$group": {
            "_id": {"manager_id": "$manager_final", "manager_name": "$manager_name", "branch": "$branch_final"},
            "total": {"$sum": 1},
            "converted": {"$sum": {"$cond": ["$converted", 1, 0]}}
        }},
        {"$sort": {"total": -1}},
    ]))

    # by agent
    by_agent_rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$addFields": {
            "agent_oid": {"$convert": {"input": "$agent_id", "to": "objectId", "onError": None, "onNull": None}},
            "converted": converted_expr,
        }},
        {"$lookup": {"from": "users", "localField": "agent_oid", "foreignField": "_id", "as": "agent_doc"}},
        {"$addFields": {"agent_doc": {"$arrayElemAt": ["$agent_doc", 0]}}},
        {"$group": {
            "_id": {"agent_id": "$agent_id", "agent_name": {"$ifNull": ["$agent_doc.name", "Agent"]}, "branch": {"$ifNull": ["$agent_doc.branch", "-"]}},
            "total": {"$sum": 1},
            "converted": {"$sum": {"$cond": ["$converted", 1, 0]}}
        }},
        {"$sort": {"total": -1}},
    ]))

    # recent leads
    recent_rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$sort": {"lead_registered_at": -1}},
        {"$limit": 8},
        {"$addFields": {"agent_oid": {"$convert": {"input": "$agent_id", "to": "objectId", "onError": None, "onNull": None}}}},
        {"$lookup": {"from": "users", "localField": "agent_oid", "foreignField": "_id", "as": "agent_doc"}},
        {"$addFields": {"agent_doc": {"$arrayElemAt": ["$agent_doc", 0]}}},
        {"$project": {
            "name": 1,
            "phone_number": 1,
            "location": 1,
            "lead_stage": 1,
            "lead_registered_at": 1,
            "agent_name": {"$ifNull": ["$agent_doc.name", "Agent"]},
            "branch": {"$ifNull": ["$agent_doc.branch", "-"]},
        }}
    ]))

    payload = {
        "ok": True,
        "range": {"start": start_str, "end": end_str},
        "totals": {
            "leads_total": leads_total,
            "converted": leads_converted,
            "conversion_rate": conversion_rate,
        },
        "by_branch": [
            {
                "branch": r.get("_id"),
                "leads": int(r.get("total", 0) or 0),
                "converted": int(r.get("converted", 0) or 0),
                "conversion_rate": _rate(int(r.get("total", 0) or 0), int(r.get("converted", 0) or 0)),
            }
            for r in by_branch_rows
        ],
        "by_manager": [
            {
                "manager_id": str((r.get("_id") or {}).get("manager_id") or ""),
                "manager_name": (r.get("_id") or {}).get("manager_name") or "Unknown",
                "branch": (r.get("_id") or {}).get("branch") or "-",
                "leads": int(r.get("total", 0) or 0),
                "converted": int(r.get("converted", 0) or 0),
                "conversion_rate": _rate(int(r.get("total", 0) or 0), int(r.get("converted", 0) or 0)),
            }
            for r in by_manager_rows
        ],
        "by_agent": [
            {
                "agent_id": str((r.get("_id") or {}).get("agent_id") or ""),
                "agent_name": (r.get("_id") or {}).get("agent_name") or "Agent",
                "branch": (r.get("_id") or {}).get("branch") or "-",
                "leads": int(r.get("total", 0) or 0),
                "converted": int(r.get("converted", 0) or 0),
                "conversion_rate": _rate(int(r.get("total", 0) or 0), int(r.get("converted", 0) or 0)),
            }
            for r in by_agent_rows
        ],
        "recent_leads": [
            {
                "name": r.get("name") or "",
                "phone_number": r.get("phone_number") or "",
                "location": r.get("location") or "",
                "lead_stage": r.get("lead_stage") or "",
                "lead_registered_at": r.get("lead_registered_at"),
                "agent_name": r.get("agent_name") or "Agent",
                "branch": r.get("branch") or "-",
            }
            for r in recent_rows
        ]
    }
    return jsonify(_json_safe(payload))


@executive_leads_bp.route("/export.csv")
@role_required("executive", "admin")
def executive_leads_export_csv():
    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)

    branch = (request.args.get("branch") or "").strip()
    manager_id = (request.args.get("manager_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()

    agent_ids = _agent_ids_for_filters(branch, manager_id, agent_id)

    base_match = _build_match(request.args, start_dt, end_dt, agent_ids, include_text_search=True)

    rows = list(customers_col.aggregate([
        {"$match": base_match},
        {"$addFields": {
            "agent_oid": {"$convert": {"input": "$agent_id", "to": "objectId", "onError": None, "onNull": None}},
            "converted": _converted_expr(),
        }},
        {"$lookup": {"from": "users", "localField": "agent_oid", "foreignField": "_id", "as": "agent_doc"}},
        {"$addFields": {"agent_doc": {"$arrayElemAt": ["$agent_doc", 0]}}},
        {"$addFields": {"manager_final": {"$ifNull": ["$agent_doc.manager_id", ""]}}},
        {"$addFields": {"manager_oid": {"$convert": {"input": "$manager_final", "to": "objectId", "onError": None, "onNull": None}}}},
        {"$lookup": {"from": "users", "localField": "manager_oid", "foreignField": "_id", "as": "manager_doc"}},
        {"$addFields": {"manager_doc": {"$arrayElemAt": ["$manager_doc", 0]}}},
        {"$sort": {"lead_registered_at": -1, "_id": -1}},
        {"$limit": 5000},
        {"$project": {
            "lead_registered_at": 1,
            "name": 1,
            "phone_number": 1,
            "lead_stage": 1,
            "agent_name": {"$ifNull": ["$agent_doc.name", "Agent"]},
            "manager_name": {"$ifNull": ["$manager_doc.name", "Manager"]},
            "branch": {"$ifNull": ["$agent_doc.branch", "-"]},
            "converted": 1,
        }}
    ]))

    csv_rows = []
    csv_rows.append(["lead_registered_at", "name", "phone", "lead_stage", "agent_name", "manager_name", "branch", "converted"])
    for r in rows:
        created_at = r.get("lead_registered_at")
        created_str = created_at.strftime("%Y-%m-%d %H:%M") if isinstance(created_at, datetime) else ""
        csv_rows.append([
            created_str,
            r.get("name") or "",
            r.get("phone_number") or "",
            r.get("lead_stage") or "",
            r.get("agent_name") or "Agent",
            r.get("manager_name") or "Manager",
            r.get("branch") or "-",
            "yes" if r.get("converted") else "no",
        ])

    csv_buf = []
    for row in csv_rows:
        csv_buf.append(",".join('"' + str(x).replace('"', '""') + '"' for x in row))

    filename = f"executive_leads_{start_str}_to_{end_str}.csv"
    return Response("\n".join(csv_buf), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })
