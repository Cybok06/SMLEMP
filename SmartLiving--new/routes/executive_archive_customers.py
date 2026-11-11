from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Tuple

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import role_required, get_current_identity
from services.activity_audit import log_activity

executive_archive_customers_bp = Blueprint(
    "executive_archive_customers",
    __name__,
    url_prefix="/executive/archive-customers",
    template_folder="../templates",
)

customers_col = db["customers"]
archived_customers_col = db["Archived_customers"]
payments_col = db["payments"]
users_col = db["users"]
archive_logs_col = db["customer_archive_logs"]


def _to_object_id(raw: str) -> ObjectId | None:
    try:
        return ObjectId(raw)
    except Exception:
        return None


def _normalize_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        s = (val or "").strip()[:10]
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def _date_str(val: Any) -> str:
    d = _normalize_date(val)
    return d.strftime("%Y-%m-%d") if d else ""


def _pick_code(doc: Dict[str, Any]) -> str:
    for key in ("customer_code", "account_number", "account_no", "code", "customer_id"):
        v = doc.get(key)
        if v:
            return str(v)
    return ""


def _agent_branch_map(agent_ids: List[Any]) -> Dict[str, str]:
    if not agent_ids:
        return {}
    oids = []
    for aid in agent_ids:
        if isinstance(aid, ObjectId):
            oids.append(aid)
        elif ObjectId.is_valid(str(aid)):
            oids.append(ObjectId(str(aid)))
    if not oids:
        return {}
    rows = users_col.find({"_id": {"$in": oids}}, {"_id": 1, "branch": 1})
    return {str(r["_id"]): (r.get("branch") or "") for r in rows}


def _last_payment_map(customer_ids: List[Any]) -> Dict[str, Any]:
    if not customer_ids:
        return {}
    ids = []
    for cid in customer_ids:
        if isinstance(cid, ObjectId):
            ids.append(cid)
        elif ObjectId.is_valid(str(cid)):
            ids.append(ObjectId(str(cid)))
    if not ids:
        return {}

    pipeline = [
        {"$match": {"customer_id": {"$in": ids}, "payment_type": {"$ne": "WITHDRAWAL"}}},
        {"$group": {"_id": "$customer_id", "last_payment": {"$max": "$date"}}},
    ]
    rows = list(payments_col.aggregate(pipeline))
    return {str(r["_id"]): r.get("last_payment") for r in rows}


def _archive_one(customer_id_raw: str, reason: str, action_type: str, actor_id: str, actor_role: str) -> Tuple[str, str]:
    """
    Returns (status, message) for a single customer id.
    status: archived | skipped | failed
    """
    candidates: List[Any] = []
    oid = _to_object_id(customer_id_raw)
    if oid is not None:
        candidates.append(oid)
        candidates.append(customer_id_raw)
    else:
        candidates.append(customer_id_raw)

    doc = customers_col.find_one({"_id": {"$in": candidates}})
    if not doc:
        return "failed", "Customer not found"

    existing = archived_customers_col.find_one({"_id": doc["_id"]})
    if existing:
        return "skipped", "Already archived"

    now = datetime.utcnow()
    archived_doc = dict(doc)
    archived_doc.update(
        {
            "archived_at": now,
            "archived_reason": reason,
            "archived_by": actor_id,
            "archived_by_role": actor_role,
            "archived_from": "customers",
            "archived_action": action_type,
        }
    )

    try:
        archived_customers_col.insert_one(archived_doc)
    except Exception:
        return "failed", "Archive insert failed"

    delete_res = customers_col.delete_one({"_id": doc["_id"]})
    if not delete_res or delete_res.deleted_count != 1:
        return "failed", "Archive insert ok, delete failed"

    log_activity(
        "customer.archived",
        "Archived Customer",
        entity_type="customer",
        entity_id=str(doc.get("_id")),
        meta={
            "customer_name": doc.get("name", ""),
            "reason": reason,
            "action_type": action_type,
            "archived_from": "customers",
        },
    )
    try:
        archive_logs_col.insert_one(
            {
                "customer_id": doc.get("_id"),
                "customer_name": doc.get("name", ""),
                "archived_at": now,
                "archived_by": actor_id,
                "archived_by_role": actor_role,
                "reason": reason,
                "action_type": action_type,
                "archived_from": "customers",
            }
        )
    except Exception:
        pass

    return "archived", "Archived successfully"


def _unarchive_one(customer_id_raw: str, reason: str, actor_id: str, actor_role: str) -> Tuple[str, str]:
    """
    Move from Archived_customers back to customers.
    Returns (status, message) for a single customer id.
    status: unarchived | skipped | failed
    """
    candidates: List[Any] = []
    oid = _to_object_id(customer_id_raw)
    if oid is not None:
        candidates.append(oid)
        candidates.append(customer_id_raw)
    else:
        candidates.append(customer_id_raw)

    doc = archived_customers_col.find_one({"_id": {"$in": candidates}})
    if not doc:
        return "failed", "Archived customer not found"

    existing = customers_col.find_one({"_id": doc["_id"]})
    if existing:
        return "skipped", "Already active in customers"

    now = datetime.utcnow()
    restored_doc = dict(doc)
    restored_doc.update(
        {
            "restored_at": now,
            "restored_reason": reason,
            "restored_by": actor_id,
            "restored_by_role": actor_role,
            "restored_from": "Archived_customers",
        }
    )

    try:
        customers_col.insert_one(restored_doc)
    except Exception:
        return "failed", "Restore insert failed"

    delete_res = archived_customers_col.delete_one({"_id": doc["_id"]})
    if not delete_res or delete_res.deleted_count != 1:
        return "failed", "Restore insert ok, delete failed"

    log_activity(
        "customer.unarchived",
        "Restored Customer",
        entity_type="customer",
        entity_id=str(doc.get("_id")),
        meta={
            "customer_name": doc.get("name", ""),
            "reason": reason,
            "restored_from": "Archived_customers",
        },
    )
    try:
        archive_logs_col.insert_one(
            {
                "customer_id": doc.get("_id"),
                "customer_name": doc.get("name", ""),
                "archived_at": doc.get("archived_at"),
                "archived_by": doc.get("archived_by"),
                "restored_at": now,
                "restored_by": actor_id,
                "restored_by_role": actor_role,
                "reason": reason,
                "action_type": "unarchive",
                "restored_from": "Archived_customers",
            }
        )
    except Exception:
        pass

    return "unarchived", "Restored successfully"


@executive_archive_customers_bp.get("/")
@role_required("executive", "admin")
def archive_customers_page():
    ident = get_current_identity()
    return render_template(
        "executive/archive_customers.html",
        identity=ident,
    )


@executive_archive_customers_bp.get("/preview-cutoff")
@role_required("executive", "admin")
def preview_by_cutoff():
    cutoff_str = (request.args.get("cutoff_date") or "").strip()
    if not cutoff_str:
        return jsonify(ok=False, message="Missing cutoff date."), 400
    try:
        cutoff_date = datetime.strptime(cutoff_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify(ok=False, message="Invalid cutoff date."), 400

    pipeline = [
        {"$match": {"payment_type": {"$ne": "WITHDRAWAL"}}},
        {"$group": {"_id": "$customer_id", "last_payment": {"$max": "$date"}}},
    ]
    rows = list(payments_col.aggregate(pipeline))
    eligible_ids: List[Any] = []
    last_map: Dict[str, Any] = {}
    for r in rows:
        last_val = r.get("last_payment")
        last_dt = _normalize_date(last_val)
        if last_dt and last_dt < cutoff_date:
            eligible_ids.append(r["_id"])
            last_map[str(r["_id"])] = last_val

    if not eligible_ids:
        return jsonify(ok=True, customers=[])

    customers = list(customers_col.find({"_id": {"$in": eligible_ids}}))
    agent_ids = [c.get("agent_id") for c in customers if c.get("agent_id")]
    agent_map = _agent_branch_map(agent_ids)
    results = []
    for c in customers:
        c_agent_id = c.get("agent_id")
        branch = agent_map.get(str(c_agent_id), "")
        results.append(
            {
                "id": str(c.get("_id")),
                "name": c.get("name") or "N/A",
                "phone": c.get("phone_number") or c.get("phone") or "N/A",
                "branch": branch or c.get("agent_branch") or "",
                "code": _pick_code(c),
                "last_payment_date": _date_str(last_map.get(str(c.get("_id")))),
                "status": c.get("status") or "",
            }
        )

    return jsonify(ok=True, customers=results)


@executive_archive_customers_bp.get("/search")
@role_required("executive", "admin")
def search_customers():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(ok=True, customers=[])

    or_clauses: List[Dict[str, Any]] = [
        {"name": {"$regex": q, "$options": "i"}},
        {"phone_number": {"$regex": q, "$options": "i"}},
        {"customer_code": {"$regex": q, "$options": "i"}},
        {"account_number": {"$regex": q, "$options": "i"}},
        {"account_no": {"$regex": q, "$options": "i"}},
        {"code": {"$regex": q, "$options": "i"}},
        {"customer_id": {"$regex": q, "$options": "i"}},
    ]
    oid = _to_object_id(q)
    if oid is not None:
        or_clauses.append({"_id": oid})

    customers = list(customers_col.find({"$or": or_clauses}).limit(200))
    last_map = _last_payment_map([c.get("_id") for c in customers])
    agent_ids = [c.get("agent_id") for c in customers if c.get("agent_id")]
    agent_map = _agent_branch_map(agent_ids)

    results = []
    for c in customers:
        c_agent_id = c.get("agent_id")
        branch = agent_map.get(str(c_agent_id), "")
        results.append(
            {
                "id": str(c.get("_id")),
                "name": c.get("name") or "N/A",
                "phone": c.get("phone_number") or c.get("phone") or "N/A",
                "branch": branch or c.get("agent_branch") or "",
                "code": _pick_code(c),
                "last_payment_date": _date_str(last_map.get(str(c.get("_id")))),
                "status": c.get("status") or "",
            }
        )

    return jsonify(ok=True, customers=results)


@executive_archive_customers_bp.post("/archive")
@role_required("executive", "admin")
def archive_customers():
    data = request.get_json(silent=True) or {}
    ids = data.get("customer_ids") or []
    reason = (data.get("reason") or "").strip() or "Manual archive by executive"
    action_type = (data.get("action_type") or "manual_search_archive").strip()

    if not ids:
        return jsonify(ok=False, message="No customers selected."), 400

    ident = get_current_identity()
    actor_id = str(ident.get("user_id") or "")
    actor_role = ident.get("role") or "executive"

    results: List[Dict[str, str]] = []
    counts = {"archived": 0, "skipped": 0, "failed": 0}

    for raw_id in ids:
        status, msg = _archive_one(str(raw_id), reason, action_type, actor_id, actor_role)
        counts[status] += 1
        results.append({"id": str(raw_id), "status": status, "message": msg})

    return jsonify(ok=True, counts=counts, results=results)


@executive_archive_customers_bp.get("/archived")
@role_required("executive", "admin")
def archived_list():
    q = (request.args.get("q") or "").strip()
    page = int(request.args.get("page") or 1)
    per_page = min(max(int(request.args.get("per_page") or 12), 1), 50)
    skip = (page - 1) * per_page

    query: Dict[str, Any] = {}
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"phone_number": {"$regex": q, "$options": "i"}},
            {"customer_code": {"$regex": q, "$options": "i"}},
            {"account_number": {"$regex": q, "$options": "i"}},
            {"account_no": {"$regex": q, "$options": "i"}},
            {"code": {"$regex": q, "$options": "i"}},
            {"customer_id": {"$regex": q, "$options": "i"}},
        ]
        oid = _to_object_id(q)
        if oid is not None:
            query["$or"].append({"_id": oid})

    total = archived_customers_col.count_documents(query)
    rows = list(
        archived_customers_col.find(query)
        .sort("archived_at", -1)
        .skip(skip)
        .limit(per_page)
    )

    agent_ids = [r.get("agent_id") for r in rows if r.get("agent_id")]
    agent_map = _agent_branch_map(agent_ids)

    results = []
    for c in rows:
        c_agent_id = c.get("agent_id")
        branch = agent_map.get(str(c_agent_id), "")
        results.append(
            {
                "id": str(c.get("_id")),
                "name": c.get("name") or "N/A",
                "phone": c.get("phone_number") or c.get("phone") or "N/A",
                "branch": branch or c.get("agent_branch") or "",
                "code": _pick_code(c),
                "archived_at": _date_str(c.get("archived_at")),
                "archived_reason": c.get("archived_reason") or "",
            }
        )

    total_pages = (total + per_page - 1) // per_page
    return jsonify(
        ok=True,
        customers=results,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@executive_archive_customers_bp.post("/unarchive")
@role_required("executive", "admin")
def unarchive_customers():
    data = request.get_json(silent=True) or {}
    ids = data.get("customer_ids") or []
    reason = (data.get("reason") or "").strip() or "Restored by executive"

    if not ids:
        return jsonify(ok=False, message="No customers selected."), 400

    ident = get_current_identity()
    actor_id = str(ident.get("user_id") or "")
    actor_role = ident.get("role") or "executive"

    results: List[Dict[str, str]] = []
    counts = {"unarchived": 0, "skipped": 0, "failed": 0}

    for raw_id in ids:
        status, msg = _unarchive_one(str(raw_id), reason, actor_id, actor_role)
        counts[status] += 1
        results.append({"id": str(raw_id), "status": status, "message": msg})

    return jsonify(ok=True, counts=counts, results=results)
