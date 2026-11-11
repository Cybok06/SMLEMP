from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import csv
import io

from bson import ObjectId
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for, Response

from db import db
from login import get_current_identity

closed_cards_history_bp = Blueprint(
    "closed_cards_history", __name__, url_prefix="/closed_cards_history", template_folder="../templates"
)

card_closures_col = db["card_closures"]
customers_col = db["customers"]
users_col = db["users"]
inventory_outflow_col = db["inventory_products_outflow"]


def _ensure_indexes() -> None:
    try:
        card_closures_col.create_index([("at", -1)])
        card_closures_col.create_index([("by_user", 1), ("at", -1)])
        card_closures_col.create_index([("customer_id", 1), ("at", -1)])
        customers_col.create_index([("manager_id", 1)])
        customers_col.create_index([("agent_id", 1)])
        customers_col.create_index([("branch", 1)])
        users_col.create_index([("role", 1), ("manager_id", 1)])
        inventory_outflow_col.create_index([("source", 1), ("created_at", -1)])
        inventory_outflow_col.create_index([("by_user", 1), ("created_at", -1)])
    except Exception:
        pass


_ensure_indexes()


def _safe_oid(val: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(val))
    except Exception:
        return None


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _parse_date_range(args) -> Tuple[datetime, datetime]:
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
    end_exclusive = end + timedelta(days=1)
    return start, end_exclusive


def _require_role() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return None, "auth"
    role = ident.get("role")
    if role not in ("admin", "executive", "manager"):
        return None, "forbidden"
    return ident, None


def _manager_scope_match(manager_id: Any) -> Dict[str, Any]:
    mid = _safe_oid(manager_id) or str(manager_id)
    return {"$or": [{"customer.manager_id": mid}, {"customer.manager_id": str(mid)}]}


def _agent_scope_match(agent_id: Any) -> Dict[str, Any]:
    aid = str(agent_id)
    return {"$or": [{"agent_id_str": aid}, {"agent_id_str": str(aid)}]}


def _build_pipeline(args, ident: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int, int]:
    role = ident.get("role")
    page = max(1, int(args.get("page", 1) or 1))
    limit = max(5, min(int(args.get("limit", 20) or 20), 100))
    skip = (page - 1) * limit

    start_dt, end_dt = _parse_date_range(args)
    base_match: Dict[str, Any] = {
        "action": "close_card",
        "at": {"$gte": start_dt, "$lt": end_dt},
    }

    pipeline: List[Dict[str, Any]] = [
        {"$match": base_match},
        {"$lookup": {
            "from": "customers",
            "localField": "customer_id",
            "foreignField": "_id",
            "as": "customer",
        }},
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "selected_index_int": {
                "$convert": {"input": "$payload.selected_product_index", "to": "int", "onError": None, "onNull": None}
            },
            "selected_purchase": {"$arrayElemAt": ["$customer.purchases", "$selected_index_int"]},
        }},
        {"$addFields": {
            "agent_id_str": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]},
        }},
    ]

    scoped_matches: List[Dict[str, Any]] = []

    # Manager scope enforcement
    if role == "manager":
        manager_id = session.get("manager_id") or ident.get("user_id")
        if manager_id:
            scoped_matches.append(_manager_scope_match(manager_id))

    # Admin/Executive filters
    if role in ("admin", "executive"):
        manager_filter = (args.get("manager_id") or "").strip()
        if manager_filter:
            scoped_matches.append(_manager_scope_match(manager_filter))

    agent_filter = (args.get("agent_id") or "").strip()
    if agent_filter:
        scoped_matches.append(_agent_scope_match(agent_filter))

    branch_filter = (args.get("branch") or "").strip()
    missing_images = (args.get("missing_images") or "").strip() == "1"

    search = (args.get("search") or "").strip()
    if search:
        phone_regex = _digits_only(search) or search
        scoped_matches.append({
            "$or": [
                {"customer.name": {"$regex": search, "$options": "i"}},
                {"customer.phone_number": {"$regex": phone_regex}},
            ]
        })

    if scoped_matches:
        pipeline.append({"$match": {"$and": scoped_matches}})

    pipeline.extend([
        {"$addFields": {
            "kept_amount_num": {
                "$convert": {"input": "$payload.kept_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "forfeited_amount_num": {
                "$convert": {"input": "$payload.forfeited_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "agent_oid": {
                "$convert": {"input": "$agent_id_str", "to": "objectId", "onError": None, "onNull": None}
            },
            "by_user_oid": {
                "$convert": {"input": "$by_user", "to": "objectId", "onError": None, "onNull": None}
            },
        }},
        {"$lookup": {
            "from": "users",
            "let": {"aid": "$agent_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$aid"]},
                    {"$eq": ["$role", "agent"]},
                ]}}},
                {"$project": {"name": 1, "image_url": 1, "branch": 1}},
            ],
            "as": "agent",
        }},
        {"$unwind": {"path": "$agent", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {
            "from": "users",
            "let": {"cid": "$by_user_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$_id", "$$cid"]}}},
                {"$project": {"name": 1, "image_url": 1, "role": 1}},
            ],
            "as": "closed_by",
        }},
        {"$unwind": {"path": "$closed_by", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "agent_branch": {"$ifNull": ["$agent.branch", "$customer.branch"]},
            "agent_image_url": {"$ifNull": ["$agent.image_url", ""]},
            "agent_image_lc": {"$toLower": {"$ifNull": ["$agent.image_url", ""]}},
        }},
        {"$addFields": {
            "missing_agent_image": {"$or": [
                {"$eq": ["$agent_image_url", ""]},
                {"$regexMatch": {"input": "$agent_image_lc", "regex": "via.placeholder.com", "options": "i"}},
                {"$and": [
                    {"$ne": ["$agent_image_url", ""]},
                    {"$not": {"$regexMatch": {"input": "$agent_image_lc", "regex": "^https?://", "options": "i"}}},
                    {"$not": {"$regexMatch": {"input": "$agent_image_lc", "regex": "^/uploads/", "options": "i"}}},
                ]},
            ]},
        }},
    ])

    if branch_filter:
        pipeline.append({"$match": {"$or": [
            {"agent_branch": branch_filter},
            {"customer.branch": branch_filter},
        ]}})

    if missing_images:
        pipeline.append({"$match": {"missing_agent_image": True}})

    facet = {
        "rows": [
            {"$sort": {"at": -1}},
            {"$skip": skip},
            {"$limit": limit},
            {"$lookup": {
                "from": "inventory_products_outflow",
                "let": {"cid": "$customer_id", "pidx": "$selected_index_int"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [
                        {"$eq": ["$source", "close_card"]},
                        {"$eq": ["$customer_id", "$$cid"]},
                        {"$eq": ["$closed_product_index", "$$pidx"]},
                    ]}}},
                    {"$project": {
                        "selected_product_id": 1,
                        "selected_qty": 1,
                        "selected_total_price": 1,
                        "selected_product.name": 1,
                        "selected_product.price": 1,
                        "selected_product.selling_price": 1,
                    }},
                ],
                "as": "replacement_outflows",
            }},
            {"$project": {
                "_id": 1,
                "at": 1,
                "by_user": 1,
                "by_role": 1,
                "payload": 1,
                "kept_amount_num": 1,
                "forfeited_amount_num": 1,
                "selected_index_int": 1,
                "selected_purchase": 1,
                "agent_id_str": 1,
                "replacement_outflows": 1,
                "customer._id": 1,
                "customer.name": 1,
                "customer.phone_number": 1,
                "customer.image_url": 1,
                "customer.branch": 1,
                "customer.manager_id": 1,
                "customer.agent_id": 1,
                "agent._id": 1,
                "agent.name": 1,
                "agent.image_url": 1,
                "agent.branch": 1,
                "closed_by._id": 1,
                "closed_by.name": 1,
                "closed_by.image_url": 1,
                "closed_by.role": 1,
            }},
        ],
        "metrics": [
            {"$group": {
                "_id": None,
                "total_closed": {"$sum": 1},
                "total_kept": {"$sum": "$kept_amount_num"},
                "total_forfeited": {"$sum": "$forfeited_amount_num"},
            }}
        ],
        "trend_daily": [
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$at"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ],
        "trend_weekly": [
            {"$group": {
                "_id": {
                    "$concat": [
                        {"$toString": {"$isoWeekYear": "$at"}},
                        "-W",
                        {"$toString": {"$isoWeek": "$at"}}
                    ]
                },
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ],
        "kept_forfeited_daily": [
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$at"}},
                "kept": {"$sum": "$kept_amount_num"},
                "forfeited": {"$sum": "$forfeited_amount_num"},
            }},
            {"$sort": {"_id": 1}},
        ],
        "top_agents": [
            {"$group": {
                "_id": "$agent_id_str",
                "count": {"$sum": 1},
                "agent_id": {"$first": "$agent._id"},
                "agent_name": {"$first": "$agent.name"},
                "agent_image": {"$first": "$agent.image_url"},
            }},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ],
        "agent_dist": [
            {"$group": {
                "_id": "$agent_id_str",
                "count": {"$sum": 1},
                "agent_id": {"$first": "$agent._id"},
                "agent_name": {"$first": "$agent.name"},
            }},
            {"$sort": {"count": -1}},
        ],
        "branch_dist": [
            {"$group": {
                "_id": "$agent_branch",
                "count": {"$sum": 1},
            }},
            {"$sort": {"count": -1}},
        ],
        "total_count": [{"$count": "count"}],
    }

    pipeline.append({"$facet": facet})
    return pipeline, page, limit


def _build_export_pipeline(args, ident: Dict[str, Any], include_all_branches: bool = False) -> List[Dict[str, Any]]:
    start_dt, end_dt = _parse_date_range(args)
    role = ident.get("role")

    base_match: Dict[str, Any] = {
        "action": "close_card",
        "at": {"$gte": start_dt, "$lt": end_dt},
    }

    pipeline: List[Dict[str, Any]] = [
        {"$match": base_match},
        {"$lookup": {
            "from": "customers",
            "localField": "customer_id",
            "foreignField": "_id",
            "as": "customer",
        }},
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "selected_index_int": {
                "$convert": {"input": "$payload.selected_product_index", "to": "int", "onError": None, "onNull": None}
            },
            "selected_purchase": {"$arrayElemAt": ["$customer.purchases", "$selected_index_int"]},
            "agent_id_str": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]},
        }},
    ]

    scoped_matches: List[Dict[str, Any]] = []

    if role == "manager":
        manager_id = session.get("manager_id") or ident.get("user_id")
        if manager_id:
            scoped_matches.append(_manager_scope_match(manager_id))

    if role in ("admin", "executive"):
        manager_filter = (args.get("manager_id") or "").strip()
        if manager_filter:
            scoped_matches.append(_manager_scope_match(manager_filter))

    agent_filter = (args.get("agent_id") or "").strip()
    if agent_filter:
        scoped_matches.append(_agent_scope_match(agent_filter))

    search = (args.get("search") or "").strip()
    if search:
        phone_regex = _digits_only(search) or search
        scoped_matches.append({
            "$or": [
                {"customer.name": {"$regex": search, "$options": "i"}},
                {"customer.phone_number": {"$regex": phone_regex}},
            ]
        })

    if scoped_matches:
        pipeline.append({"$match": {"$and": scoped_matches}})

    pipeline.extend([
        {"$addFields": {
            "kept_amount_num": {
                "$convert": {"input": "$payload.kept_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "forfeited_amount_num": {
                "$convert": {"input": "$payload.forfeited_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "agent_oid": {
                "$convert": {"input": "$agent_id_str", "to": "objectId", "onError": None, "onNull": None}
            },
            "by_user_oid": {
                "$convert": {"input": "$by_user", "to": "objectId", "onError": None, "onNull": None}
            },
        }},
        {"$lookup": {
            "from": "users",
            "let": {"aid": "$agent_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$aid"]},
                    {"$eq": ["$role", "agent"]},
                ]}}},
                {"$project": {"name": 1, "branch": 1, "image_url": 1}},
            ],
            "as": "agent",
        }},
        {"$unwind": {"path": "$agent", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {
            "from": "users",
            "let": {"cid": "$by_user_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$_id", "$$cid"]}}},
                {"$project": {"name": 1, "role": 1}},
            ],
            "as": "closed_by",
        }},
        {"$unwind": {"path": "$closed_by", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "agent_branch": {"$ifNull": ["$agent.branch", "$customer.branch"]},
        }},
    ])

    # For "all branches" export, ignore selected branch filter.
    branch_filter = (args.get("branch") or "").strip()
    if branch_filter and not include_all_branches:
        pipeline.append({"$match": {"$or": [
            {"agent_branch": branch_filter},
            {"customer.branch": branch_filter},
        ]}})

    missing_images = (args.get("missing_images") or "").strip() == "1"
    if missing_images:
        pipeline.extend([
            {"$addFields": {
                "agent_image_url": {"$ifNull": ["$agent.image_url", ""]},
                "agent_image_lc": {"$toLower": {"$ifNull": ["$agent.image_url", ""]}},
            }},
            {"$addFields": {
                "missing_agent_image": {"$or": [
                    {"$eq": ["$agent_image_url", ""]},
                    {"$regexMatch": {"input": "$agent_image_lc", "regex": "via.placeholder.com", "options": "i"}},
                ]},
            }},
            {"$match": {"missing_agent_image": True}},
        ])

    pipeline.extend([
        {"$sort": {"at": -1}},
        {"$project": {
            "_id": 1,
            "at": 1,
            "kept_amount_num": 1,
            "forfeited_amount_num": 1,
            "payload.note": 1,
            "payload.target_products": 1,
            "selected_purchase.product.name": 1,
            "customer.name": 1,
            "customer.phone_number": 1,
            "customer.branch": 1,
            "agent.name": 1,
            "agent.branch": 1,
            "closed_by.name": 1,
            "closed_by.role": 1,
            "agent_branch": 1,
        }},
    ])
    return pipeline


def _serialize_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    payload = doc.get("payload") or {}
    customer = doc.get("customer") or {}
    agent = doc.get("agent") or {}
    closed_by = doc.get("closed_by") or {}
    selected_purchase = doc.get("selected_purchase") or {}
    selected_product = (selected_purchase.get("product") or {})

    product_name = (
        payload.get("selected_product_name")
        or selected_product.get("name")
        or "Unknown Product"
    )

    replacements = []
    for outflow in (doc.get("replacement_outflows") or []):
        prod = outflow.get("selected_product") or {}
        name = prod.get("name") or "Unknown"
        price = prod.get("selling_price") or prod.get("price") or 0
        replacements.append({
            "product_id": outflow.get("selected_product_id"),
            "name": name,
            "qty": int(outflow.get("selected_qty") or 0),
            "price": float(price or 0),
            "total_price": float(outflow.get("selected_total_price") or 0),
        })

    at = doc.get("at")
    at_iso = at.isoformat() if isinstance(at, datetime) else ""

    return {
        "id": str(doc.get("_id")),
        "at": at_iso,
        "by_role": doc.get("by_role") or "",
        "kept_amount": float(doc.get("kept_amount_num") or 0),
        "forfeited_amount": float(doc.get("forfeited_amount_num") or 0),
        "payload": {
            "note": payload.get("note") or "",
            "selected_product_index": payload.get("selected_product_index"),
            "target_products": payload.get("target_products") or [],
        },
        "agent": {
            "id": str(agent.get("_id")) if agent.get("_id") else (doc.get("agent_id_str") or ""),
            "name": agent.get("name") or "Unknown Agent",
            "image_url": agent.get("image_url") or "",
            "branch": agent.get("branch") or customer.get("branch") or "",
        },
        "closed_by": {
            "id": str(closed_by.get("_id")) if closed_by.get("_id") else (doc.get("by_user") or ""),
            "name": closed_by.get("name") or "Unknown User",
            "image_url": closed_by.get("image_url") or "",
            "role": closed_by.get("role") or doc.get("by_role") or "",
        },
        "customer": {
            "id": str(customer.get("_id")) if customer.get("_id") else "",
            "name": customer.get("name") or "Unknown Customer",
            "phone_number": customer.get("phone_number") or "",
            "image_url": customer.get("image_url") or "",
            "branch": customer.get("branch") or "",
            "manager_id": str(customer.get("manager_id") or ""),
            "agent_id": str(customer.get("agent_id") or ""),
        },
        "product": {
            "name": product_name,
            "total": selected_product.get("total"),
            "purchase_date": selected_purchase.get("purchase_date"),
            "end_date": selected_purchase.get("end_date"),
            "transfer_total": selected_purchase.get("transfer_total") or selected_purchase.get("transfers_total"),
            "purchase_type": selected_purchase.get("purchase_type"),
            "status": selected_product.get("status") or selected_purchase.get("status"),
        },
        "replacements": replacements,
    }


def _metrics_pipeline(ident: Dict[str, Any], start: datetime, end: datetime) -> List[Dict[str, Any]]:
    role = ident.get("role")
    manager_id = session.get("manager_id") or ident.get("user_id")
    now = datetime.utcnow()

    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)
    month_start = datetime(now.year, now.month, 1)
    next_month = datetime(now.year + (1 if now.month == 12 else 0), (1 if now.month == 12 else now.month + 1), 1)

    base: List[Dict[str, Any]] = [
        {"$match": {"action": "close_card"}},
        {"$lookup": {
            "from": "customers",
            "localField": "customer_id",
            "foreignField": "_id",
            "as": "customer",
        }},
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "selected_index_int": {
                "$convert": {"input": "$payload.selected_product_index", "to": "int", "onError": None, "onNull": None}
            },
            "selected_purchase": {"$arrayElemAt": ["$customer.purchases", "$selected_index_int"]},
        }},
        {"$addFields": {
            "agent_id_str": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]},
        }},
    ]

    if role == "manager" and manager_id:
        base.append({"$match": _manager_scope_match(manager_id)})

    base.extend([
        {"$addFields": {
            "kept_amount_num": {
                "$convert": {"input": "$payload.kept_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "forfeited_amount_num": {
                "$convert": {"input": "$payload.forfeited_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "agent_oid": {
                "$convert": {"input": "$agent_id_str", "to": "objectId", "onError": None, "onNull": None}
            },
        }},
        {"$lookup": {
            "from": "users",
            "let": {"aid": "$agent_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$aid"]},
                    {"$eq": ["$role", "agent"]},
                ]}}},
                {"$project": {"name": 1, "image_url": 1, "branch": 1}},
            ],
            "as": "agent",
        }},
        {"$unwind": {"path": "$agent", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "agent_image_url": {"$ifNull": ["$agent.image_url", ""]},
            "agent_image_lc": {"$toLower": {"$ifNull": ["$agent.image_url", ""]}},
        }},
        {"$addFields": {
            "missing_agent_image": {"$or": [
                {"$eq": ["$agent_image_url", ""]},
                {"$regexMatch": {"input": "$agent_image_lc", "regex": "via.placeholder.com", "options": "i"}},
                {"$and": [
                    {"$ne": ["$agent_image_url", ""]},
                    {"$not": {"$regexMatch": {"input": "$agent_image_lc", "regex": "^https?://", "options": "i"}}},
                    {"$not": {"$regexMatch": {"input": "$agent_image_lc", "regex": "^/uploads/", "options": "i"}}},
                ]},
            ]},
        }},
        {"$facet": {
            "range_totals": [
                {"$match": {"at": {"$gte": start, "$lt": end}}},
                {"$group": {
                    "_id": None,
                    "total_closed": {"$sum": 1},
                    "total_kept": {"$sum": "$kept_amount_num"},
                    "total_forfeited": {"$sum": "$forfeited_amount_num"},
                }},
            ],
            "top_agents": [
                {"$match": {"at": {"$gte": start, "$lt": end}}},
                {"$group": {
                    "_id": "$agent_id_str",
                    "count": {"$sum": 1},
                    "agent_id": {"$first": "$agent._id"},
                    "agent_name": {"$first": "$agent.name"},
                    "agent_image": {"$first": "$agent.image_url"},
                    "agent_branch": {"$first": "$agent.branch"},
                }},
                {"$sort": {"count": -1}},
                {"$limit": 5},
            ],
            "closed_today": [
                {"$match": {"at": {"$gte": today_start, "$lt": tomorrow_start}}},
                {"$count": "count"},
            ],
            "closed_week": [
                {"$match": {"at": {"$gte": week_start, "$lt": week_end}}},
                {"$count": "count"},
            ],
            "closed_month": [
                {"$match": {"at": {"$gte": month_start, "$lt": next_month}}},
                {"$count": "count"},
            ],
            "missing_agents": [
                {"$match": {"at": {"$gte": start, "$lt": end}}},
                {"$match": {"missing_agent_image": True}},
                {"$group": {"_id": "$agent_id_str"}},
                {"$count": "count"},
            ],
        }},
    ])

    return base


@closed_cards_history_bp.route("/", methods=["GET"])
def closed_cards_history_page():
    ident, err = _require_role()
    if err == "auth":
        return redirect(url_for("login.login", next=request.path))
    if err == "forbidden":
        return "Forbidden", 403
    return render_template("closed_cards_history.html", identity=ident)


@closed_cards_history_bp.route("/api", methods=["GET"])
def closed_cards_history_api():
    ident, err = _require_role()
    if err == "auth":
        return jsonify(ok=False, message="Unauthorized"), 401
    if err == "forbidden":
        return jsonify(ok=False, message="Forbidden"), 403

    pipeline, page, limit = _build_pipeline(request.args, ident)
    results = list(card_closures_col.aggregate(pipeline))
    facet = results[0] if results else {}

    rows_raw = facet.get("rows") or []
    rows = [_serialize_row(doc) for doc in rows_raw]

    metrics_raw = (facet.get("metrics") or [{}])[0] or {}
    total_closed = int(metrics_raw.get("total_closed") or 0)
    total_kept = float(metrics_raw.get("total_kept") or 0)
    total_forfeited = float(metrics_raw.get("total_forfeited") or 0)
    avg_kept = round(total_kept / total_closed, 2) if total_closed else 0.0
    avg_forfeited = round(total_forfeited / total_closed, 2) if total_closed else 0.0

    top_agents = []
    for agent_doc in (facet.get("top_agents") or []):
        top_agents.append({
            "agent_id": str(agent_doc.get("agent_id") or agent_doc.get("_id") or ""),
            "name": agent_doc.get("agent_name") or "Unknown Agent",
            "image_url": agent_doc.get("agent_image") or "",
            "count": int(agent_doc.get("count") or 0),
        })

    trend_daily = [{"date": t["_id"], "count": int(t["count"])} for t in (facet.get("trend_daily") or [])]
    trend_weekly = [{"week": t["_id"], "count": int(t["count"])} for t in (facet.get("trend_weekly") or [])]
    kept_forfeited = [{
        "date": t["_id"],
        "kept": float(t.get("kept") or 0),
        "forfeited": float(t.get("forfeited") or 0),
    } for t in (facet.get("kept_forfeited_daily") or [])]
    total_count = int((facet.get("total_count") or [{}])[0].get("count") or 0)
    total_pages = max(1, (total_count + limit - 1) // limit)

    agent_dist = [{
        "agent_id": str(a.get("agent_id") or a.get("_id") or ""),
        "name": a.get("agent_name") or "Unknown Agent",
        "count": int(a.get("count") or 0),
    } for a in (facet.get("agent_dist") or [])]
    branch_dist = [{
        "branch": b.get("_id") or "Unknown",
        "count": int(b.get("count") or 0),
    } for b in (facet.get("branch_dist") or []) if b.get("_id")]

    return jsonify(
        ok=True,
        page=page,
        limit=limit,
        total=total_count,
        total_pages=total_pages,
        metrics={
            "total_closed": total_closed,
            "total_kept": total_kept,
            "total_forfeited": total_forfeited,
            "avg_kept": avg_kept,
            "avg_forfeited": avg_forfeited,
            "top_agents": top_agents,
        },
        charts={
            "agent_dist": agent_dist,
            "branch_dist": branch_dist,
            "trend_daily": trend_daily,
            "trend_weekly": trend_weekly,
            "kept_forfeited": kept_forfeited,
        },
        rows=rows,
    )


@closed_cards_history_bp.route("/metrics", methods=["GET"])
def closed_cards_history_metrics():
    ident, err = _require_role()
    if err == "auth":
        return jsonify(ok=False, message="Unauthorized"), 401
    if err == "forbidden":
        return jsonify(ok=False, message="Forbidden"), 403

    start_dt, end_dt = _parse_date_range(request.args)
    pipeline = _metrics_pipeline(ident, start_dt, end_dt)
    results = list(card_closures_col.aggregate(pipeline))
    facet_wrap = results[0] if results else {}

    totals = (facet_wrap.get("range_totals") or [{}])[0] or {}
    total_closed = int(totals.get("total_closed") or 0)
    total_kept = float(totals.get("total_kept") or 0)
    total_forfeited = float(totals.get("total_forfeited") or 0)
    avg_kept = round(total_kept / total_closed, 2) if total_closed else 0.0
    avg_forfeited = round(total_forfeited / total_closed, 2) if total_closed else 0.0

    top_agents = [{
        "agent_id": str(a.get("agent_id") or a.get("_id") or ""),
        "name": a.get("agent_name") or "Unknown Agent",
        "image_url": a.get("agent_image") or "",
        "branch": a.get("agent_branch") or "",
        "count": int(a.get("count") or 0),
    } for a in (facet_wrap.get("top_agents") or [])]

    missing_count = int((facet_wrap.get("missing_agents") or [{}])[0].get("count") or 0)

    return jsonify(
        ok=True,
        range={
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": (end_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        },
        totals={
            "total_closed": total_closed,
            "total_kept": total_kept,
            "total_forfeited": total_forfeited,
            "avg_kept": avg_kept,
            "avg_forfeited": avg_forfeited,
        },
        counts={
            "closed_today": int((facet_wrap.get("closed_today") or [{}])[0].get("count") or 0),
            "closed_week": int((facet_wrap.get("closed_week") or [{}])[0].get("count") or 0),
            "closed_month": int((facet_wrap.get("closed_month") or [{}])[0].get("count") or 0),
        },
        top_agents=top_agents,
        missing_images_count=missing_count,
        charts={},
    )


@closed_cards_history_bp.route("/filters", methods=["GET"])
def closed_cards_history_filters():
    ident, err = _require_role()
    if err == "auth":
        return jsonify(ok=False, message="Unauthorized"), 401
    if err == "forbidden":
        return jsonify(ok=False, message="Forbidden"), 403

    role = ident.get("role")
    manager_id = session.get("manager_id") or ident.get("user_id")
    manager_filter = (request.args.get("manager_id") or "").strip()
    manager_scope = manager_filter if role in ("admin", "executive") and manager_filter else manager_id

    agents_filter: Dict[str, Any] = {"role": "agent"}
    branches_filter: Dict[str, Any] = {}

    if manager_scope:
        agents_filter["$or"] = [
            {"manager_id": _safe_oid(manager_scope) or manager_scope},
            {"manager_id": str(manager_scope)},
        ]
        branches_filter["$or"] = [
            {"manager_id": _safe_oid(manager_scope) or manager_scope},
            {"manager_id": str(manager_scope)},
        ]

    agents = list(users_col.find(agents_filter, {"name": 1, "image_url": 1, "branch": 1}))
    agent_branches = users_col.distinct("branch", agents_filter) if agents_filter else []
    customer_branches = customers_col.distinct("branch", branches_filter) if branches_filter else customers_col.distinct("branch")
    branches = sorted({b for b in (agent_branches or []) + (customer_branches or []) if b})

    managers: List[Dict[str, Any]] = []
    if role in ("admin", "executive"):
        managers = list(users_col.find({"role": "manager"}, {"name": 1, "branch": 1}))

    return jsonify(
        ok=True,
        agents=[{
            "id": str(a["_id"]),
            "name": a.get("name") or "Agent",
            "image_url": a.get("image_url") or "",
            "branch": a.get("branch") or "",
        } for a in agents],
        managers=[{"id": str(m["_id"]), "name": m.get("name") or "Manager", "branch": m.get("branch") or ""} for m in managers],
        branches=branches,
    )


@closed_cards_history_bp.route("/export", methods=["GET"])
def closed_cards_history_export():
    ident, err = _require_role()
    if err == "auth":
        return redirect(url_for("login.login", next=request.path))
    if err == "forbidden":
        return "Forbidden", 403

    export_scope = (request.args.get("export_scope") or "current").strip().lower()
    include_all_branches = export_scope == "all"
    pipeline = _build_export_pipeline(request.args, ident, include_all_branches=include_all_branches)
    rows = list(card_closures_col.aggregate(pipeline))

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Closed Date",
        "Branch",
        "Agent",
        "Customer",
        "Customer Phone",
        "Product",
        "Kept Amount",
        "Forfeited Amount",
        "Closed By",
        "Closed By Role",
        "Replacement Count",
        "Note",
    ])

    for r in rows:
        at = r.get("at")
        at_str = at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(at, datetime) else ""
        customer = r.get("customer") or {}
        agent = r.get("agent") or {}
        closed_by = r.get("closed_by") or {}
        payload = r.get("payload") or {}
        selected_purchase = r.get("selected_purchase") or {}
        selected_product = selected_purchase.get("product") or {}
        replacements = payload.get("target_products") or []

        w.writerow([
            at_str,
            r.get("agent_branch") or customer.get("branch") or "",
            agent.get("name") or "Unknown Agent",
            customer.get("name") or "Unknown Customer",
            customer.get("phone_number") or "",
            selected_product.get("name") or "",
            f"{float(r.get('kept_amount_num') or 0):.2f}",
            f"{float(r.get('forfeited_amount_num') or 0):.2f}",
            closed_by.get("name") or "",
            closed_by.get("role") or "",
            len(replacements),
            payload.get("note") or "",
        ])

    out.seek(0)
    branch_label = "all_branches" if include_all_branches else ((request.args.get("branch") or "").strip() or "filtered")
    today = datetime.utcnow().strftime("%Y%m%d")
    filename = f"closed_cards_{branch_label}_{today}.csv"
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
