from __future__ import annotations

import csv
import io
from datetime import datetime
from functools import wraps
from typing import Any, Dict, List, Optional

from bson import ObjectId
from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for

from db import db
from login import get_current_identity

activation_leads_bp = Blueprint("activation_leads", __name__)

customers_col = db["customers"]
users_col = db["users"]
activations_col = db["activations"]
rsvps_col = db["activation_rsvps"]
payments_col = db["payments"]


def _ensure_indexes() -> None:
    try:
        customers_col.create_index([("activation", 1), ("activation_id", 1), ("date_registered", -1)])
        customers_col.create_index([("activation", 1), ("agent_id", 1), ("date_registered", -1)])
        customers_col.create_index([("activation", 1), ("manager_id", 1), ("date_registered", -1)])
        customers_col.create_index([("activation", 1), ("activation_registered_by_id", 1), ("date_registered", -1)])
        customers_col.create_index([("activation", 1), ("registered_by_agent_id", 1), ("date_registered", -1)])
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
        or doc.get("registered_by_id")
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
            {"agent_id": {"$in": variants}},
        ]
    }


def _activation_id_clause(activation_id: str) -> Dict[str, Any]:
    variants = _id_variants(activation_id)
    if not variants:
        return {"activation_id": "__none__"}
    return {"activation_id": {"$in": variants}}


def _activation_day(activation_id: str) -> Optional[datetime]:
    activation = _find_activation(activation_id)
    dt = (activation or {}).get("activationDateTime")
    if isinstance(dt, datetime):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


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


def _json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "message": message}), code


def _require_roles(*roles: str):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            ident = get_current_identity()
            if not ident.get("is_authenticated"):
                if request.path.startswith("/activation/") or request.path.startswith("/admin/"):
                    return redirect(url_for("login.login", next=request.path))
                return _json_error("Unauthorized", 401)
            role = (ident.get("role") or "").lower()
            if roles and role not in roles:
                if request.path.startswith("/api/"):
                    return _json_error("Forbidden", 403)
                if request.path.startswith("/activation/") or request.path.startswith("/admin/"):
                    return "Forbidden", 403
                return "Forbidden", 403
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def _find_activation(activation_id: str):
    oid = _safe_oid(activation_id)
    if not oid:
        return None
    return activations_col.find_one({"_id": oid})


def _parse_date(s: str) -> Optional[datetime]:
    text = (s or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except Exception:
        return None


def _parse_time(s: str) -> Optional[tuple[int, int, int]]:
    text = (s or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour, parsed.minute, parsed.second
        except Exception:
            continue
    return None


def _apply_time(dt: datetime, time_text: str, default_end: bool = False) -> datetime:
    parsed = _parse_time(time_text)
    if parsed:
        hour, minute, second = parsed
        return dt.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if default_end:
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _date_label(dt: Optional[datetime]) -> str:
    if not isinstance(dt, datetime):
        return "--"
    return dt.strftime("%Y-%m-%d %H:%M")


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _qty(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _build_leads_query(filters: Dict[str, str]) -> Dict[str, Any]:
    activation_id = filters.get("activation_id", "")
    agent_id = filters.get("agent_id", "")
    q = filters.get("q", "")
    start_date = filters.get("start_date", "")
    end_date = filters.get("end_date", "")
    start_time = filters.get("start_time", "")
    end_time = filters.get("end_time", "")

    query: Dict[str, Any] = {} if activation_id else {"activation": True}
    and_clauses: List[Dict[str, Any]] = []

    if activation_id:
        approved_agent_ids = _approved_agent_ids(activation_id)
        if approved_agent_ids:
            and_clauses.append(_actor_clause(approved_agent_ids))

    if agent_id:
        and_clauses.append(_actor_clause([agent_id]))

    if q:
        and_clauses.append({
            "$or": [
                {"name": {"$regex": q, "$options": "i"}},
                {"phone_number": {"$regex": q, "$options": "i"}},
                {"location": {"$regex": q, "$options": "i"}},
            ]
        })

    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)
    if not start_dt and not end_dt and activation_id:
        start_dt = _activation_day(activation_id)
        end_dt = start_dt
    if not start_dt and not end_dt and (start_time or end_time):
        start_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt
    if start_dt or end_dt:
        dr: Dict[str, Any] = {}
        if start_dt:
            dr["$gte"] = _apply_time(start_dt, start_time)
        if end_dt:
            dr["$lte"] = _apply_time(end_dt, end_time, default_end=True)
        query["date_registered"] = dr

    if and_clauses:
        query["$and"] = and_clauses

    return query


def _load_user_map(user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    oids = [_safe_oid(i) for i in user_ids if _safe_oid(i)]
    if not oids:
        return {}
    return {
        str(u.get("_id")): u
        for u in users_col.find({"_id": {"$in": oids}}, {"name": 1, "role": 1, "branch": 1})
    }


def _product_summary(customer: Dict[str, Any]) -> tuple[str, int, float]:
    parts: List[str] = []
    total_qty = 0
    total_value = 0.0
    for purchase in customer.get("purchases") or []:
        product = purchase.get("product") or {}
        name = product.get("name") or "Unnamed Product"
        qty = _qty(purchase.get("quantity") or product.get("quantity") or 1) or 1
        value = _money(product.get("total") or (_money(product.get("price")) * qty))
        purchase_date = purchase.get("purchase_date") or ""
        total_qty += qty
        total_value += value
        parts.append(f"{name} x{qty} GHS {value:.2f} {purchase_date}".strip())
    return "; ".join(parts), total_qty, round(total_value, 2)


def _payment_summary(customer_ids: List[ObjectId]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not customer_ids:
        return out
    for payment in payments_col.find({"customer_id": {"$in": customer_ids}}, {"amount": 1, "date": 1, "time": 1, "method": 1, "payment_type": 1, "product_name": 1, "customer_id": 1}):
        cid = str(payment.get("customer_id") or "")
        bucket = out.setdefault(cid, {"count": 0, "total": 0.0, "details": []})
        amount = _money(payment.get("amount"))
        bucket["count"] += 1
        bucket["total"] += amount
        bucket["details"].append(
            f"{payment.get('date') or ''} {payment.get('time') or ''} {payment.get('payment_type') or ''} {payment.get('method') or ''} {payment.get('product_name') or ''} GHS {amount:.2f}".strip()
        )
    for bucket in out.values():
        bucket["total"] = round(bucket["total"], 2)
        bucket["details"] = "; ".join(bucket["details"])
    return out


def _export_leads_csv(query: Dict[str, Any], filters: Dict[str, str]) -> Response:
    rows = list(customers_col.find(query).sort([("date_registered", -1)]).limit(5000))
    user_map = _load_user_map([_actor_id(r) for r in rows if _actor_id(r)])
    payment_map = _payment_summary([r.get("_id") for r in rows if isinstance(r.get("_id"), ObjectId)])

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Customer ID",
        "Name",
        "Phone",
        "Location",
        "Occupation",
        "Comment",
        "Date Registered",
        "Time Registered",
        "Registered By",
        "Registered Role",
        "Branch",
        "Lead Stage",
        "Activation ID",
        "Products",
        "Product Quantity",
        "Product Value",
        "Payment Count",
        "Payment Total",
        "Payment Details",
    ])

    for row in rows:
        actor = user_map.get(_actor_id(row), {})
        registered = row.get("date_registered")
        products, product_qty, product_value = _product_summary(row)
        payment = payment_map.get(str(row.get("_id")), {})
        writer.writerow([
            str(row.get("_id") or ""),
            row.get("name") or "",
            row.get("phone_number") or "",
            row.get("location") or "",
            row.get("occupation") or "",
            row.get("comment") or "",
            registered.strftime("%Y-%m-%d") if isinstance(registered, datetime) else "",
            registered.strftime("%H:%M:%S") if isinstance(registered, datetime) else "",
            actor.get("name") or _actor_id(row) or "",
            actor.get("role") or row.get("registered_by_role") or "",
            actor.get("branch") or "",
            row.get("lead_stage") or "lead",
            str(row.get("activation_id") or filters.get("activation_id") or ""),
            products,
            product_qty,
            product_value,
            payment.get("count", 0),
            payment.get("total", 0.0),
            payment.get("details", ""),
        ])

    filename_bits = ["activation_leads"]
    if filters.get("agent_id"):
        filename_bits.append(filters["agent_id"])
    if filters.get("start_date") or filters.get("end_date"):
        filename_bits.append((filters.get("start_date") or "start") + "_to_" + (filters.get("end_date") or "end"))
    filename = "_".join(filename_bits) + ".csv"

    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@activation_leads_bp.get("/activation/<activation_id>/leads/register")
@_require_roles("agent", "manager")
def activation_lead_register_page(activation_id: str):
    return redirect(url_for("customer.register_customer"))


@activation_leads_bp.post("/activation/leads/add")
@_require_roles("agent", "manager")
def add_activation_lead():
    return _json_error("Use /customer/add for lead registration flow.", 400)


@activation_leads_bp.get("/admin/activation-leads")
@_require_roles("admin")
def admin_activation_leads_page():
    activation_id = (request.args.get("activation_id") or "").strip()
    agent_id = (request.args.get("agent_id") or "").strip()
    q = (request.args.get("q") or "").strip()
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    start_time = (request.args.get("start_time") or "").strip()
    end_time = (request.args.get("end_time") or "").strip()
    page = max(int(request.args.get("page") or 1), 1)
    per_page = 25
    filters = {
        "activation_id": activation_id,
        "agent_id": agent_id,
        "q": q,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
    }
    query = _build_leads_query(filters)

    if (request.args.get("export") or "").lower() == "csv":
        return _export_leads_csv(query, filters)

    total = customers_col.count_documents(query)
    skip = (page - 1) * per_page
    rows = list(customers_col.find(query).sort([("date_registered", -1)]).skip(skip).limit(per_page))

    ids = [_actor_id(r) for r in rows if _actor_id(r)]
    act_ids = [(_safe_oid(r.get("activation_id")) if r.get("activation_id") else None) for r in rows]
    act_ids = [x for x in act_ids if x]

    user_map = _load_user_map(ids)

    activation_map: Dict[str, Dict[str, Any]] = {}
    if act_ids:
        for a in activations_col.find({"_id": {"$in": act_ids}}, {"title": 1, "location": 1, "activationDateTime": 1}):
            activation_map[str(a.get("_id"))] = a

    table_rows = []
    for r in rows:
        rid = str(r.get("_id"))
        reg_id = _actor_id(r)
        reg_user = user_map.get(reg_id, {})
        aid = str(r.get("activation_id")) if r.get("activation_id") else ""
        a = activation_map.get(aid, {})
        table_rows.append(
            {
                "id": rid,
                "date": _date_label(r.get("date_registered")),
                "name": r.get("name") or "",
                "phone": r.get("phone_number") or "",
                "location": r.get("location") or "",
                "registered_by": reg_user.get("name") or reg_id or "--",
                "registered_role": reg_user.get("role") or (r.get("registered_by_role") or ""),
                "branch": reg_user.get("branch") or "",
                "activation_id": aid,
                "activation_name": a.get("title") or r.get("activation_title") or r.get("activation_name") or "",
                "image_url": r.get("image_url") or "",
                "view_activation_id": activation_id or aid,
            }
        )

    leads_today_q = dict(query)
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    leads_today_q["date_registered"] = {"$gte": day_start}
    leads_today = customers_col.count_documents(leads_today_q)

    top_agent_name = "--"
    actor_projection = {
        "$ifNull": [
            "$activation_registered_by_id",
            {"$ifNull": ["$registered_by_agent_id", {"$ifNull": ["$registered_by_id", "$agent_id"]}]},
        ]
    }
    top_pipeline = [
        {"$match": query},
        {"$project": {"actor_id": actor_projection}},
        {"$group": {"_id": "$actor_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 1},
    ]
    top_rows = list(customers_col.aggregate(top_pipeline))
    if top_rows:
        top_id = str(top_rows[0].get("_id") or "")
        udoc = users_col.find_one({"_id": _safe_oid(top_id)}, {"name": 1}) if _safe_oid(top_id) else None
        top_agent_name = (udoc or {}).get("name") or top_id or "--"

    activation_options = list(activations_col.find({}, {"title": 1, "activationDateTime": 1}).sort([("activationDateTime", -1)]).limit(200))
    activation_options = [
        {"id": str(a.get("_id")), "name": a.get("title") or "", "when": _date_label(a.get("activationDateTime"))}
        for a in activation_options
    ]

    agent_options = list(users_col.find({"role": {"$in": ["agent", "manager"]}}, {"name": 1, "role": 1, "branch": 1}).sort([("name", 1)]).limit(400))
    agent_options = [
        {"id": str(u.get("_id")), "name": u.get("name") or "", "role": u.get("role") or "", "branch": u.get("branch") or ""}
        for u in agent_options
    ]

    total_pages = max((total + per_page - 1) // per_page, 1)

    return render_template(
        "admin_activation_leads.html",
        rows=table_rows,
        total=total,
        leads_today=leads_today,
        top_agent_name=top_agent_name,
        activation_options=activation_options,
        agent_options=agent_options,
        filters={
            "activation_id": activation_id,
            "agent_id": agent_id,
            "q": q,
            "start_date": start_date,
            "end_date": end_date,
            "start_time": start_time,
            "end_time": end_time,
        },
        page=page,
        total_pages=total_pages,
    )


@activation_leads_bp.get("/admin/activation-leads/<customer_id>")
@_require_roles("admin")
def admin_activation_lead_detail(customer_id: str):
    coid = _safe_oid(customer_id)
    if not coid:
        return "Lead not found", 404

    row = customers_col.find_one({"_id": coid})
    if not row:
        return "Lead not found", 404

    act = None
    act_oid = _safe_oid(row.get("activation_id")) or _safe_oid(request.args.get("activation_id"))
    if act_oid:
        act = activations_col.find_one({"_id": act_oid})

    reg_user = None
    rid = _actor_id(row)
    if rid:
        reg_user = users_col.find_one({"_id": _safe_oid(rid)}) if _safe_oid(rid) else users_col.find_one({"_id": rid})

    return render_template(
        "admin_activation_lead_detail.html",
        lead=row,
        activation=act,
        registered_user=reg_user,
        date_label=_date_label,
    )
