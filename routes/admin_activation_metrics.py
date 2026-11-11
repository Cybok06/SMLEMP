from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, List, Optional

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request, redirect, url_for

from db import db
from login import get_current_identity

admin_activation_metrics_bp = Blueprint("admin_activation_metrics", __name__)

customers_col = db["customers"]
users_col = db["users"]
activations_col = db["activations"]
payments_col = db["payments"]
rsvps_col = db["activation_rsvps"]


def _ensure_indexes() -> None:
    try:
        customers_col.create_index([("activation", 1), ("date_registered", -1), ("agent_id", 1), ("lead_stage", 1)])
        customers_col.create_index([("activation_id", 1)])
        customers_col.create_index([("activation_id", 1), ("activation_registered_by_id", 1), ("date_registered", -1)])
        customers_col.create_index([("activation_id", 1), ("registered_by_agent_id", 1), ("date_registered", -1)])
        payments_col.create_index([("activation_id", 1), ("recorded_by_agent_id", 1), ("date", -1)])
        rsvps_col.create_index([("activationId", 1), ("status", 1)])
    except Exception:
        pass


_ensure_indexes()


def _safe_oid(raw: Any) -> Optional[ObjectId]:
    if raw is None:
        return None
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def _parse_date(text: str) -> Optional[datetime]:
    t = (text or "").strip()
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d")
    except Exception:
        return None


def _date_key(dt: Any) -> Optional[str]:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if isinstance(dt, str) and len(dt) >= 10:
        return dt[:10]
    return None


def _day_bounds(day_key: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(day_key, "%Y-%m-%d")
    return start, start + timedelta(days=1)


def _id_variants(raw: Any) -> List[Any]:
    vals: List[Any] = []
    if raw is None:
        return vals
    raw_str = str(raw)
    vals.append(raw_str)
    oid = _safe_oid(raw)
    if oid:
        vals.append(oid)
    return vals


def _actor_id(doc: Dict[str, Any]) -> str:
    return str(
        doc.get("activation_registered_by_id")
        or doc.get("registered_by_agent_id")
        or doc.get("recorded_by_agent_id")
        or doc.get("assigned_by_agent_id")
        or doc.get("agent_id")
        or ""
    )


def _actor_clause(agent_ids: List[str]) -> Dict[str, Any]:
    variants: List[Any] = []
    for agent_id in agent_ids:
        variants.extend(_id_variants(agent_id))
    if not variants:
        return {"_id": "__none__"}
    return {
        "$or": [
            {"activation_registered_by_id": {"$in": variants}},
            {"registered_by_agent_id": {"$in": variants}},
            {"registered_by_id": {"$in": variants}},
            {"assigned_by_agent_id": {"$in": variants}},
            {"recorded_by_agent_id": {"$in": variants}},
            {"agent_id": {"$in": variants}},
        ]
    }


def _activation_id_clause(field: str, activation_id: str) -> Dict[str, Any]:
    variants = _id_variants(activation_id)
    if not variants:
        return {field: "__none__"}
    return {field: {"$in": variants}}


def _admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        ident = get_current_identity()
        if not ident.get("is_authenticated"):
            if request.path.startswith("/admin/"):
                return redirect(url_for("login.login", next=request.path))
            return jsonify({"ok": False, "message": "Unauthorized"}), 401
        if (ident.get("role") or "").lower() != "admin":
            if request.path.startswith("/admin/"):
                return "Forbidden", 403
            return jsonify({"ok": False, "message": "Forbidden"}), 403
        return fn(*args, **kwargs)

    return wrapped


def _activation_day(activation_id: str) -> Optional[str]:
    aoid = _safe_oid(activation_id)
    if not aoid:
        return None
    activation = activations_col.find_one({"_id": aoid}, {"activationDateTime": 1})
    return _date_key((activation or {}).get("activationDateTime"))


def _build_base_query(activation_id: str, agent_id: str, start_date: str, end_date: str) -> Dict[str, Any]:
    q: Dict[str, Any] = {} if activation_id else {"activation": True}
    and_clauses: List[Dict[str, Any]] = []
    approved_agent_ids: List[str] = []

    if activation_id:
        approved_agent_ids = _approved_agent_ids(activation_id)
        if approved_agent_ids:
            and_clauses.append(_actor_clause(approved_agent_ids))

    if agent_id:
        and_clauses.append(_actor_clause([agent_id]))

    sdt = _parse_date(start_date)
    edt = _parse_date(end_date)
    if not sdt and not edt and activation_id:
        activation_day = _activation_day(activation_id)
        if activation_day:
            sdt, edt = _day_bounds(activation_day)
            edt = edt - timedelta(days=1)

    if sdt or edt:
        dr: Dict[str, Any] = {}
        if sdt:
            dr["$gte"] = sdt
        if edt:
            dr["$lt"] = edt + timedelta(days=1)
        q["date_registered"] = dr

    if and_clauses:
        q["$and"] = and_clauses

    return q


def _agent_name_map(agent_ids: List[str]) -> Dict[str, str]:
    oids = [x for x in (_safe_oid(i) for i in agent_ids) if x]
    out: Dict[str, str] = {}
    if not oids:
        return out
    for u in users_col.find({"_id": {"$in": oids}}, {"name": 1}):
        out[str(u.get("_id"))] = u.get("name") or "Unknown"
    return out


def _qty(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _date_in_range(day: Optional[str], start_date: str, end_date: str, activation_day: Optional[str]) -> bool:
    if not day:
        return False
    if activation_day and not start_date and not end_date:
        return day == activation_day
    if start_date and day < start_date:
        return False
    if end_date and day > end_date:
        return False
    return True


def _maybe_datetime(value: Any) -> Optional[datetime]:
    return value if isinstance(value, datetime) else None


def _activation_runtime_bounds(activation_doc: Dict[str, Any]) -> tuple[Optional[datetime], Optional[datetime]]:
    return _maybe_datetime((activation_doc or {}).get("startedAt")), _maybe_datetime((activation_doc or {}).get("endedAt"))


def _leader_runtime_periods(activation_doc: Dict[str, Any], leader_id: str) -> list[tuple[datetime, Optional[datetime]]]:
    if not leader_id:
        return []

    runtime_start, runtime_end = _activation_runtime_bounds(activation_doc)
    if not runtime_start:
        return []

    periods: list[tuple[datetime, Optional[datetime]]] = []
    history = list((activation_doc or {}).get("teamLeaderHistory") or [])
    seen_assigned: set[datetime] = set()

    for entry in history:
        if str(entry.get("leaderId") or "") != leader_id:
            continue
        assigned_at = _maybe_datetime(entry.get("assignedAt"))
        if not assigned_at:
            continue
        period_start = max(assigned_at, runtime_start)
        period_end = _maybe_datetime(entry.get("endedAt"))
        if runtime_end and (period_end is None or runtime_end < period_end):
            period_end = runtime_end
        if period_end and period_end < period_start:
            continue
        periods.append((period_start, period_end))
        seen_assigned.add(assigned_at)

    current_leader_id = str((activation_doc or {}).get("teamLeaderId") or "")
    current_assigned_at = _maybe_datetime((activation_doc or {}).get("teamLeaderAssignedAt"))
    if current_leader_id == leader_id and current_assigned_at and current_assigned_at not in seen_assigned:
        period_start = max(current_assigned_at, runtime_start)
        period_end = runtime_end
        if not period_end or period_end >= period_start:
            periods.append((period_start, period_end))

    return sorted(periods, key=lambda item: item[0])


def _ts_in_periods(ts: Optional[datetime], periods: list[tuple[datetime, Optional[datetime]]]) -> bool:
    if not ts:
        return False
    for start, end in periods:
        if ts < start:
            continue
        if end is None or ts <= end:
            return True
    return False


def _metric_window_bounds(start_date: str, end_date: str, activation_day: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    sdt = _parse_date(start_date)
    edt = _parse_date(end_date)
    if not sdt and not edt and activation_day:
        return _day_bounds(activation_day)
    start_bound = sdt
    end_bound = edt + timedelta(days=1) if edt else None
    return start_bound, end_bound


def _datetime_in_window(dt: Optional[datetime], start_bound: Optional[datetime], end_bound: Optional[datetime]) -> bool:
    if not dt:
        return False
    if start_bound and dt < start_bound:
        return False
    if end_bound and dt >= end_bound:
        return False
    return True


def _period_seconds_in_window(
    start: datetime,
    end: Optional[datetime],
    window_start: Optional[datetime],
    window_end: Optional[datetime],
) -> float:
    current_end = end or datetime.utcnow()
    actual_start = max(start, window_start) if window_start else start
    actual_end = min(current_end, window_end) if window_end else current_end
    if actual_end <= actual_start:
        return 0.0
    return max((actual_end - actual_start).total_seconds(), 0.0)


def _format_runtime(minutes: float) -> str:
    total_minutes = max(int(round(minutes or 0)), 0)
    hours, mins = divmod(total_minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _build_top_lead_agents(by_agent_rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    ranked = sorted(
        by_agent_rows,
        key=lambda row: (-int(row.get("leads") or 0), -int(row.get("converted") or 0), row.get("agent_name") or ""),
    )
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(ranked[:limit], start=1):
        leads = int(row.get("leads") or 0)
        converted = int(row.get("converted") or 0)
        out.append(
            {
                "rank": index,
                "agent_id": row.get("agent_id") or "",
                "agent_name": row.get("agent_name") or "Unknown",
                "leads": leads,
                "converted": converted,
                "conversionRate": round((converted / leads) * 100, 2) if leads else 0.0,
            }
        )
    return out


def _build_leader_performance(activation_id: str, start_date: str, end_date: str, activation_day: Optional[str]) -> Dict[str, Any]:
    if not activation_id:
        return {"hasActivation": False, "activationTitle": "", "topLeader": None, "rows": []}

    activation = activations_col.find_one({"_id": _safe_oid(activation_id)})
    if not activation:
        return {"hasActivation": False, "activationTitle": "", "topLeader": None, "rows": []}

    leader_name_hints: Dict[str, str] = {}
    leader_ids: List[str] = []

    for entry in activation.get("teamLeaderHistory") or []:
        leader_id = str(entry.get("leaderId") or "")
        if not leader_id:
            continue
        if leader_id not in leader_ids:
            leader_ids.append(leader_id)
        if entry.get("leaderName"):
            leader_name_hints[leader_id] = entry.get("leaderName") or ""

    current_leader_id = str(activation.get("teamLeaderId") or "")
    if current_leader_id and current_leader_id not in leader_ids:
        leader_ids.append(current_leader_id)
    if current_leader_id and activation.get("teamLeaderName"):
        leader_name_hints[current_leader_id] = activation.get("teamLeaderName") or ""

    if not leader_ids:
        return {
            "hasActivation": True,
            "activationTitle": activation.get("title") or "",
            "topLeader": None,
            "rows": [],
        }

    user_name_map = _agent_name_map(leader_ids)
    window_start, window_end = _metric_window_bounds(start_date, end_date, activation_day)
    customers = list(
        customers_col.find(
            _activation_id_clause("activation_id", activation_id),
            {"date_registered": 1, "activation_leader_id": 1},
        )
    )

    rows: List[Dict[str, Any]] = []
    for leader_id in leader_ids:
        periods = _leader_runtime_periods(activation, leader_id)
        runtime_seconds = sum(_period_seconds_in_window(start, end, window_start, window_end) for start, end in periods)
        customers_gained = 0
        for customer in customers:
            registered_at = _maybe_datetime(customer.get("date_registered"))
            if not _datetime_in_window(registered_at, window_start, window_end):
                continue
            matches_period = _ts_in_periods(registered_at, periods) if periods else False
            if matches_period or str(customer.get("activation_leader_id") or "") == leader_id:
                customers_gained += 1

        runtime_minutes = round(runtime_seconds / 60, 2)
        rows.append(
            {
                "leader_id": leader_id,
                "leader_name": user_name_map.get(leader_id) or leader_name_hints.get(leader_id) or "Unknown Leader",
                "customersGained": customers_gained,
                "runtimeMinutes": runtime_minutes,
                "runtimeLabel": _format_runtime(runtime_minutes),
                "stints": len(periods),
                "isCurrent": leader_id == current_leader_id,
            }
        )

    rows.sort(key=lambda row: (-int(row.get("customersGained") or 0), -float(row.get("runtimeMinutes") or 0), row.get("leader_name") or ""))
    top_leader = rows[0] if rows else None
    return {
        "hasActivation": True,
        "activationTitle": activation.get("title") or "",
        "topLeader": top_leader,
        "rows": rows,
    }


def _build_products_metrics(customer_query: Dict[str, Any], start_date: str, end_date: str, activation_day: Optional[str], allowed_agent_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    allowed = set(allowed_agent_ids or [])
    docs = customers_col.find(
        customer_query,
        {
            "activation_registered_by_id": 1,
            "registered_by_agent_id": 1,
            "registered_by_id": 1,
            "agent_id": 1,
            "date_registered": 1,
            "purchases": 1,
        },
    )
    total_products = 0
    total_product_value = 0.0
    products_by_agent: Dict[str, int] = {}
    products_by_day: Dict[str, int] = {}

    for doc in docs:
        fallback_agent = _actor_id(doc)
        fallback_day = _date_key(doc.get("date_registered"))
        for purchase in doc.get("purchases") or []:
            day = _date_key(purchase.get("purchase_date")) or fallback_day
            if not _date_in_range(day, start_date, end_date, activation_day):
                continue
            product = purchase.get("product") or {}
            qty = _qty(purchase.get("quantity") or product.get("quantity") or 1) or 1
            aid = str(purchase.get("assigned_by_agent_id") or fallback_agent or "")
            if allowed and aid not in allowed:
                continue
            total_products += qty
            total_product_value += _money(product.get("total") or (_money(product.get("price")) * qty))
            if aid:
                products_by_agent[aid] = products_by_agent.get(aid, 0) + qty
            if day:
                products_by_day[day] = products_by_day.get(day, 0) + qty

    return {
        "totalProducts": total_products,
        "totalProductValue": round(total_product_value, 2),
        "productsByAgent": products_by_agent,
        "productsByDay": products_by_day,
    }


def _build_payment_query(activation_id: str, agent_id: str, start_date: str, end_date: str, activation_day: Optional[str], customer_ids: Optional[List[Any]] = None) -> Dict[str, Any]:
    q: Dict[str, Any] = {}
    id_clauses: List[Dict[str, Any]] = []
    if activation_id:
        id_clauses.append(_activation_id_clause("activation_id", activation_id))
    else:
        q["activation_id"] = {"$exists": True}
    if customer_ids:
        id_clauses.append({"customer_id": {"$in": customer_ids}})
    if id_clauses:
        q["$or"] = id_clauses
    allowed_agent_ids = _approved_agent_ids(activation_id) if activation_id else []
    if agent_id:
        allowed_agent_ids = [agent_id] if not allowed_agent_ids or agent_id in allowed_agent_ids else []
    if allowed_agent_ids:
        variants: List[Any] = []
        for allowed_id in allowed_agent_ids:
            variants.extend(_id_variants(allowed_id))
        q["recorded_by_agent_id"] = {"$in": variants}
    elif activation_id and agent_id:
        q["recorded_by_agent_id"] = "__none__"

    if activation_day and not start_date and not end_date:
        q["date"] = activation_day
    elif start_date or end_date:
        dr: Dict[str, Any] = {}
        if start_date:
            dr["$gte"] = start_date
        if end_date:
            dr["$lte"] = end_date
        q["date"] = dr
    return q


def _build_payments_metrics(payment_query: Dict[str, Any]) -> Dict[str, Any]:
    total_payments = 0
    total_amount = 0.0
    payments_by_agent: Dict[str, float] = {}
    payment_count_by_agent: Dict[str, int] = {}
    payments_by_day: Dict[str, float] = {}

    for payment in payments_col.find(payment_query, {"amount": 1, "date": 1, "recorded_by_agent_id": 1, "agent_id": 1}):
        amount = _money(payment.get("amount"))
        total_payments += 1
        total_amount += amount
        aid = str(payment.get("recorded_by_agent_id") or payment.get("agent_id") or "")
        if aid:
            payments_by_agent[aid] = payments_by_agent.get(aid, 0.0) + amount
            payment_count_by_agent[aid] = payment_count_by_agent.get(aid, 0) + 1
        day = _date_key(payment.get("date"))
        if day:
            payments_by_day[day] = payments_by_day.get(day, 0.0) + amount

    return {
        "totalPayments": total_payments,
        "totalPaymentAmount": round(total_amount, 2),
        "paymentsByAgent": {k: round(v, 2) for k, v in payments_by_agent.items()},
        "paymentCountByAgent": payment_count_by_agent,
        "paymentsByDay": {k: round(v, 2) for k, v in payments_by_day.items()},
    }


def _approved_agent_ids(activation_id: str) -> List[str]:
    activation_variants = _id_variants(activation_id)
    if not activation_variants:
        return []
    return [
        str(row.get("userId"))
        for row in rsvps_col.find(
            {
                "activationId": {"$in": activation_variants},
                "status": {"$regex": "^approved$", "$options": "i"},
            },
            {"userId": 1},
        )
        if row.get("userId")
    ]


def _going_people(activation_id: str) -> Dict[str, str]:
    approved_ids = _approved_agent_ids(activation_id)
    if not approved_ids:
        return {}
    return {uid: "approved" for uid in approved_ids}


@admin_activation_metrics_bp.get("/admin/activations/metrics")
@_admin_required
def activation_metrics_page():
    activation_options = list(
        activations_col.find({}, {"title": 1, "activationDateTime": 1}).sort([("activationDateTime", -1)]).limit(200)
    )
    activation_options = [
        {
            "id": str(a.get("_id")),
            "title": a.get("title") or "",
            "date": a.get("activationDateTime").strftime("%Y-%m-%d %H:%M") if isinstance(a.get("activationDateTime"), datetime) else "--",
        }
        for a in activation_options
    ]

    agent_options = list(users_col.find({"role": {"$in": ["agent", "manager"]}}, {"name": 1, "role": 1, "branch": 1}).sort([("name", 1)]).limit(500))
    agent_options = [
        {"id": str(a.get("_id")), "name": a.get("name") or "", "role": a.get("role") or "", "branch": a.get("branch") or ""}
        for a in agent_options
    ]

    return render_template(
        "admin_activation_metrics.html",
        activation_options=activation_options,
        agent_options=agent_options,
        filters={
            "activation_id": (request.args.get("activation_id") or "").strip(),
            "agent_id": (request.args.get("agent_id") or "").strip(),
            "start_date": (request.args.get("start_date") or "").strip(),
            "end_date": (request.args.get("end_date") or "").strip(),
        },
    )


@admin_activation_metrics_bp.get("/admin/activations/metrics/data")
@_admin_required
def activation_metrics_data():
    activation_id = (request.args.get("activation_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()

    activation_day = _activation_day(activation_id)
    base_query = _build_base_query(activation_id, agent_id, start_date, end_date)

    total_leads = customers_col.count_documents(base_query)

    converted_query = dict(base_query)
    converted_query["lead_stage"] = "customer"
    total_converted = customers_col.count_documents(converted_query)

    conversion_rate = 0.0
    if total_leads > 0:
        conversion_rate = round((total_converted / total_leads) * 100, 2)

    actor_projection = {
        "$ifNull": [
            "$activation_registered_by_id",
            {"$ifNull": ["$registered_by_agent_id", {"$ifNull": ["$registered_by_id", {"$ifNull": ["$recorded_by_agent_id", "$agent_id"]}]}]},
        ]
    }
    by_agent_leads_rows = list(
        customers_col.aggregate([
            {"$match": base_query},
            {"$project": {"actor_id": actor_projection}},
            {"$group": {"_id": "$actor_id", "leads": {"$sum": 1}}},
        ])
    )
    by_agent_converted_rows = list(
        customers_col.aggregate([
            {"$match": converted_query},
            {"$project": {"actor_id": actor_projection}},
            {"$group": {"_id": "$actor_id", "converted": {"$sum": 1}}},
        ])
    )

    by_day_leads_rows = list(
        customers_col.aggregate([
            {"$match": base_query},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_registered"}}, "leads": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ])
    )
    by_day_conv_rows = list(
        customers_col.aggregate([
            {"$match": converted_query},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_registered"}}, "converted": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ])
    )

    approved_agent_ids = _approved_agent_ids(activation_id) if activation_id else []
    product_agent_ids = approved_agent_ids
    if agent_id:
        product_agent_ids = [agent_id] if not approved_agent_ids or agent_id in approved_agent_ids else []
    products_metrics = _build_products_metrics(base_query, start_date, end_date, activation_day, product_agent_ids)
    customer_ids_for_payments = [row.get("_id") for row in customers_col.find(base_query, {"_id": 1}) if row.get("_id")]
    payment_query = _build_payment_query(activation_id, agent_id, start_date, end_date, activation_day, customer_ids_for_payments)
    payments_metrics = _build_payments_metrics(payment_query)
    total_products = products_metrics["totalProducts"]
    total_product_value = products_metrics["totalProductValue"]
    total_payments = payments_metrics["totalPayments"]
    total_payment_amount = payments_metrics["totalPaymentAmount"]

    leads_by_agent: Dict[str, int] = {str(r.get("_id") or ""): int(r.get("leads") or 0) for r in by_agent_leads_rows}
    converted_by_agent: Dict[str, int] = {str(r.get("_id") or ""): int(r.get("converted") or 0) for r in by_agent_converted_rows}
    products_by_agent: Dict[str, int] = products_metrics["productsByAgent"]
    payments_by_agent: Dict[str, float] = payments_metrics["paymentsByAgent"]
    payment_count_by_agent: Dict[str, int] = payments_metrics["paymentCountByAgent"]
    going_status_by_agent = _going_people(activation_id)

    all_agent_ids = sorted(
        set(
            [k for k in going_status_by_agent.keys() if k]
            + [k for k in leads_by_agent.keys() if k]
            + [k for k in converted_by_agent.keys() if k]
            + [k for k in products_by_agent.keys() if k]
            + [k for k in payments_by_agent.keys() if k]
        )
    )
    agent_names = _agent_name_map(all_agent_ids)

    by_agent = []
    for aid in all_agent_ids:
        leads_n = leads_by_agent.get(aid, 0)
        conv_n = converted_by_agent.get(aid, 0)
        by_agent.append(
            {
                "agent_id": aid,
                "agent_name": agent_names.get(aid, "Unknown"),
                "going_status": going_status_by_agent.get(aid, "activity"),
                "leads": leads_n,
                "converted": conv_n,
                "products": products_by_agent.get(aid, 0),
                "payments": payment_count_by_agent.get(aid, 0),
                "payment_amount": payments_by_agent.get(aid, 0.0),
            }
        )
    status_rank = {"approved": 0, "pending": 1, "activity": 2, "rejected": 3}
    by_agent.sort(key=lambda x: (status_rank.get(x.get("going_status"), 9), -x.get("leads", 0), x.get("agent_name", "")))
    top_lead_agents = _build_top_lead_agents(by_agent)
    leader_performance = _build_leader_performance(activation_id, start_date, end_date, activation_day)

    leads_by_day: Dict[str, int] = {str(r.get("_id") or ""): int(r.get("leads") or 0) for r in by_day_leads_rows if r.get("_id")}
    conv_by_day: Dict[str, int] = {str(r.get("_id") or ""): int(r.get("converted") or 0) for r in by_day_conv_rows if r.get("_id")}
    products_by_day: Dict[str, int] = products_metrics["productsByDay"]
    payments_by_day: Dict[str, float] = payments_metrics["paymentsByDay"]

    all_days = sorted(set(list(leads_by_day.keys()) + list(conv_by_day.keys()) + list(products_by_day.keys()) + list(payments_by_day.keys())))
    by_day = [
        {
            "date": d,
            "leads": leads_by_day.get(d, 0),
            "converted": conv_by_day.get(d, 0),
            "products": products_by_day.get(d, 0),
            "payments": payments_by_day.get(d, 0.0),
        }
        for d in all_days
    ]

    top_docs = list(customers_col.find(base_query, {"name": 1, "phone_number": 1, "location": 1, "agent_id": 1, "activation_registered_by_id": 1, "registered_by_agent_id": 1, "registered_by_id": 1, "lead_stage": 1, "date_registered": 1, "purchases": 1}).sort([("date_registered", -1)]).limit(20))
    top_ids = [_actor_id(d) for d in top_docs if _actor_id(d)]
    top_name_map = _agent_name_map(top_ids)
    top_customer_ids = [d.get("_id") for d in top_docs if d.get("_id")]
    top_payment_totals: Dict[str, float] = {}
    if top_customer_ids:
        for payment in payments_col.find({"customer_id": {"$in": top_customer_ids}, **payment_query}, {"customer_id": 1, "amount": 1}):
            cid = str(payment.get("customer_id") or "")
            top_payment_totals[cid] = top_payment_totals.get(cid, 0.0) + _money(payment.get("amount"))

    top_leads = []
    for d in top_docs:
        products_count = 0
        for purchase in (d.get("purchases") if isinstance(d.get("purchases"), list) else []):
            day = _date_key(purchase.get("purchase_date")) or _date_key(d.get("date_registered"))
            if not _date_in_range(day, start_date, end_date, activation_day):
                continue
            product = purchase.get("product") or {}
            products_count += _qty(purchase.get("quantity") or product.get("quantity") or 1) or 1
        actor_id = _actor_id(d)
        top_leads.append(
            {
                "customer_id": str(d.get("_id")),
                "name": d.get("name") or "",
                "phone_number": d.get("phone_number") or "",
                "location": d.get("location") or "",
                "agent_name": top_name_map.get(actor_id, "--"),
                "lead_stage": d.get("lead_stage") or "lead",
                "date_registered": d.get("date_registered").strftime("%Y-%m-%d") if isinstance(d.get("date_registered"), datetime) else "--",
                "products": products_count,
                "payments": round(top_payment_totals.get(str(d.get("_id")), 0.0), 2),
            }
        )

    return jsonify(
        {
            "ok": True,
            "kpis": {
                "totalActivationLeads": total_leads,
                "totalConverted": total_converted,
                "conversionRate": conversion_rate,
                "totalProducts": total_products,
                "totalPurchases": total_products,
                "totalProductValue": total_product_value,
                "totalPayments": total_payments,
                "totalPaymentAmount": total_payment_amount,
                "totalRevenue": total_payment_amount,
            },
            "breakdown": {
                "byAgent": by_agent,
                "byDay": by_day,
            },
            "leaderboards": {
                "topLeadAgents": top_lead_agents,
                "leaderPerformance": leader_performance,
            },
            "topLeads": top_leads,
        }
    )
