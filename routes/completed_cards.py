from __future__ import annotations

import csv
from datetime import datetime, timedelta
from io import StringIO
from math import ceil
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for

from Backend.inventory.stock_deductions_store import StockDeductionError, deliver_package_and_deduct
from db import db
from login import get_current_identity

completed_cards_bp = Blueprint("completed_cards", __name__)

outflow_col = db["inventory_products_outflow"]
customers_col = db["customers"]
packages_col = db["packages"]
users_col = db["users"]


def _safe_oid(val: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(val))
    except Exception:
        return None


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
    end_exclusive = end + timedelta(days=1)
    return start, end_exclusive, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _ensure_indexes() -> None:
    try:
        packages_col.create_index([("manager_id", 1), ("created_at", -1)])
        packages_col.create_index([("agent_id", 1), ("created_at", -1)])
        packages_col.create_index([("agent_branch", 1), ("created_at", -1)])
        packages_col.create_index([("customer_id", 1), ("product_index", 1)])
        outflow_col.create_index([("customer_id", 1), ("packaged_product_index", 1)])
    except Exception:
        pass


_ensure_indexes()


def _role_match(ident: Dict[str, Any]) -> Dict[str, Any]:
    role = ident.get("role")
    if role == "agent":
        agent_id = session.get("agent_id") or ident.get("user_id")
        return {"agent_id": str(agent_id)} if agent_id else {"agent_id": "__none__"}
    if role == "manager":
        manager_id = session.get("manager_id") or ident.get("user_id")
        if not manager_id:
            return {"manager_id": "__none__"}
        mid = _safe_oid(manager_id) or str(manager_id)
        return {"$or": [{"manager_id": mid}, {"manager_id": str(mid)}]}
    return {}


def _build_match_clauses(ident: Dict[str, Any], args, include_date: bool, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    clauses: List[Dict[str, Any]] = []
    if include_date:
        clauses.append({"created_at": {"$gte": start, "$lt": end}})

    role_match = _role_match(ident)
    if role_match:
        clauses.append(role_match)

    role = ident.get("role")
    manager_id = (args.get("manager_id") or "").strip()
    branch = (args.get("branch") or "").strip()
    agent_id = (args.get("agent_id") or "").strip()

    if role in ("executive", "admin") and manager_id:
        mid = _safe_oid(manager_id) or str(manager_id)
        clauses.append({"$or": [{"manager_id": mid}, {"manager_id": str(mid)}]})

    if role in ("manager", "executive", "admin") and branch:
        clauses.append({"agent_branch": branch})

    if role in ("manager", "executive", "admin") and agent_id:
        clauses.append({"agent_id": agent_id})

    return clauses


def _merge_match(clauses: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _date_quick_ranges() -> Dict[str, Tuple[str, str]]:
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    week_start = today - timedelta(days=today.weekday())
    month_start = datetime(now.year, now.month, 1)
    return {
        "today": (today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
        "this_week": (week_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
        "this_month": (month_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
        "last_30": ((today - timedelta(days=29)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
    }


def _status_code_expr() -> Dict[str, Any]:
    return {
        "$switch": {
            "branches": [
                {"case": {"$in": ["$status_lc", ["delivered", "delivery_complete", "completed", "complete"]]}, "then": "delivered"},
                {"case": {"$in": ["$status_lc", ["delivering", "out_for_delivery", "in_transit", "on_the_way"]]}, "then": "delivering"},
                {"case": {"$in": ["$status_lc", ["packaged", "packaging", "pending", "submitted_for_packaging", "submitted", "queued"]]}, "then": "pending"},
                {"case": {"$eq": ["$status_lc", "closed"]}, "then": "closed"},
            ],
            "default": "unknown",
        }
    }


def _status_label_expr() -> Dict[str, Any]:
    return {
        "$switch": {
            "branches": [
                {"case": {"$eq": ["$delivery_status_code", "delivered"]}, "then": "Delivered"},
                {"case": {"$eq": ["$delivery_status_code", "delivering"]}, "then": "Delivering"},
                {"case": {"$eq": ["$delivery_status_code", "pending"]}, "then": "Pending"},
                {"case": {"$eq": ["$delivery_status_code", "closed"]}, "then": "Closed"},
            ],
            "default": "Unknown",
        }
    }


def _base_pipeline(match: Dict[str, Any], delivery_status: str | None) -> List[Dict[str, Any]]:
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$addFields": {
            "packaged_index_int": {
                "$convert": {"input": "$packaged_product_index", "to": "int", "onError": None, "onNull": None}
            },
            "manager_oid": {"$convert": {"input": "$manager_id", "to": "objectId", "onError": None, "onNull": None}},
        }},
        {"$lookup": {
            "from": "customers",
            "localField": "customer_id",
            "foreignField": "_id",
            "as": "customer",
        }},
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "customer_purchase": {"$arrayElemAt": ["$customer.purchases", "$packaged_index_int"]},
        }},
        {"$lookup": {
            "from": "packages",
            "let": {"cid": "$customer_id", "pidx": "$packaged_index_int"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$customer_id", "$$cid"]},
                    {"$eq": ["$product_index", "$$pidx"]},
                ]}}},
                {"$project": {"status": 1, "created_at": 1}},
                {"$sort": {"created_at": -1}},
                {"$limit": 1},
            ],
            "as": "package_doc",
        }},
        {"$addFields": {
            "package_doc": {"$arrayElemAt": ["$package_doc", 0]},
        }},
        {"$addFields": {
            "status_raw": {
                "$ifNull": [
                    "$packaged_product.status",
                    {"$ifNull": [
                        "$customer_purchase.product.status",
                        "$package_doc.status"
                    ]}
                ]
            },
            "status_lc": {"$toLower": {"$ifNull": ["$status_raw", ""]}},
        }},
        {"$addFields": {
            "delivery_status_code": _status_code_expr(),
        }},
        {"$addFields": {
            "delivery_status_label": _status_label_expr(),
        }},
        {"$addFields": {
            "package_qty_num": {"$convert": {"input": "$package_qty", "to": "double", "onError": 0.0, "onNull": 0.0}},
            "total_paid_num": {"$convert": {"input": "$total_paid_selected_product", "to": "double", "onError": 0.0, "onNull": 0.0}},
            "product_total_num": {"$convert": {"input": "$product_total", "to": "double", "onError": 0.0, "onNull": 0.0}},
            "total_profit_num": {"$convert": {"input": "$total_profit", "to": "double", "onError": 0.0, "onNull": 0.0}},
            "product_name": {
                "$ifNull": [
                    "$package_def_name",
                    {"$ifNull": ["$product_def.name", {"$ifNull": ["$packaged_product.name", "Unknown"]}]}
                ]
            },
        }},
        {"$lookup": {
            "from": "users",
            "let": {"mid": "$manager_oid"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$mid"]},
                    {"$eq": ["$role", "manager"]},
                ]}}},
                {"$project": {"name": 1}},
            ],
            "as": "manager_doc",
        }},
        {"$addFields": {"manager_doc": {"$arrayElemAt": ["$manager_doc", 0]}}},
        {"$addFields": {"manager_name": {"$ifNull": ["$manager_doc.name", ""]}}},
    ]

    if delivery_status:
        pipeline.append({"$match": {"delivery_status_code": delivery_status}})

    return pipeline


def _count_for_range(match_base: Dict[str, Any], start: datetime, end: datetime) -> int:
    try:
        return int(outflow_col.count_documents({"$and": [match_base, {"created_at": {"$gte": start, "$lt": end}}]}))
    except Exception:
        return 0


def _count_with_status(ident: Dict[str, Any], args, start: datetime, end: datetime, delivery_status: Optional[str]) -> int:
    clauses = _build_match_clauses(ident, args, True, start, end)
    match = _merge_match(clauses)
    pipeline = _packages_pipeline(match, delivery_status)
    pipeline.append({"$count": "count"})
    res = list(packages_col.aggregate(pipeline))
    return int((res[0] or {}).get("count") or 0) if res else 0


def _list_options_for_role(ident: Dict[str, Any], manager_id: str | None, branch: str | None) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    role = ident.get("role")
    managers: List[Dict[str, Any]] = []

    if role in ("executive", "admin"):
        managers = list(users_col.find({"role": "manager"}, {"name": 1}))

    manager_scope = None
    if role == "manager":
        manager_scope = session.get("manager_id") or ident.get("user_id")
    elif role in ("executive", "admin"):
        manager_scope = manager_id or None

    agents_filter: Dict[str, Any] = {"role": "agent"}
    if manager_scope:
        mid = _safe_oid(manager_scope) or str(manager_scope)
        agents_filter["$or"] = [{"manager_id": mid}, {"manager_id": str(mid)}]
    if branch:
        agents_filter["branch"] = branch

    agents = list(users_col.find(agents_filter, {"name": 1, "branch": 1}))
    branches = sorted({a.get("branch") for a in agents if a.get("branch")})

    return managers, branches, agents


def _delivery_status_filter_value(args) -> str | None:
    raw = (args.get("delivery_status") or "").strip().lower()
    if raw == "not_delivered":
        return "pending"
    if raw in ("delivered", "delivering", "pending", "closed", "unknown", "packaging", "cancelled"):
        return raw
    return None


def _package_status_code_expr() -> Dict[str, Any]:
    return {
        "$switch": {
            "branches": [
                {"case": {"$eq": ["$status_lc", "pending"]}, "then": "pending"},
                {"case": {"$in": ["$status_lc", ["packaging", "packaged"]]}, "then": "packaging"},
                {"case": {"$eq": ["$status_lc", "delivering"]}, "then": "delivering"},
                {"case": {"$eq": ["$status_lc", "delivered"]}, "then": "delivered"},
                {"case": {"$eq": ["$status_lc", "cancelled"]}, "then": "cancelled"},
            ],
            "default": "unknown",
        }
    }


def _package_status_label_expr() -> Dict[str, Any]:
    return {
        "$switch": {
            "branches": [
                {"case": {"$eq": ["$status_code", "pending"]}, "then": "Not delivered"},
                {"case": {"$eq": ["$status_code", "packaging"]}, "then": "Packaging"},
                {"case": {"$eq": ["$status_code", "delivering"]}, "then": "Delivering"},
                {"case": {"$eq": ["$status_code", "delivered"]}, "then": "Delivered"},
                {"case": {"$eq": ["$status_code", "cancelled"]}, "then": "Cancelled"},
            ],
            "default": "Unknown",
        }
    }


def _packages_pipeline(match: Dict[str, Any], status_filter: Optional[str]) -> List[Dict[str, Any]]:
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {
            "$addFields": {
                "status_lc": {"$toLower": {"$ifNull": ["$status", ""]}},
                "product_index_int": {"$convert": {"input": "$product_index", "to": "int", "onError": None, "onNull": None}},
                "manager_oid": {"$convert": {"input": "$manager_id", "to": "objectId", "onError": None, "onNull": None}},
            }
        },
        {"$addFields": {"status_code": _package_status_code_expr()}},
        {"$addFields": {"status_label": _package_status_label_expr()}},
        {"$addFields": {"delivery_sort": {"$cond": [{"$eq": ["$status_code", "delivered"]}, 1, 0]}}},
        {
            "$addFields": {
                "product_name": {"$ifNull": ["$product.name", "Unknown"]},
                "product_image": {"$ifNull": ["$product.image_url", ""]},
                "qty_resolved": {"$ifNull": ["$qty", "$package_qty"]},
                "qty_num": {
                    "$convert": {
                        "input": {"$ifNull": ["$qty", "$package_qty"]},
                        "to": "double",
                        "onError": 0.0,
                        "onNull": 0.0,
                    }
                },
                "product_total_resolved": {"$ifNull": ["$product_total", "$product.total"]},
                "product_total_num": {
                    "$convert": {
                        "input": {"$ifNull": ["$product_total", "$product.total"]},
                        "to": "double",
                        "onError": 0.0,
                        "onNull": 0.0,
                    }
                },
                "total_paid_num": {
                    "$convert": {
                        "input": "$total_paid_selected_product",
                        "to": "double",
                        "onError": 0.0,
                        "onNull": 0.0,
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "inventory_products_outflow",
                "let": {"cid": "$customer_id", "pidx": "$product_index_int"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$customer_id", "$$cid"]},
                                    {"$eq": ["$packaged_product_index", "$$pidx"]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "components_status": 1,
                            "components_deducted": 1,
                            "total_profit": 1,
                            "profit_type": 1,
                        }
                    },
                ],
                "as": "outflow_doc",
            }
        },
        {"$addFields": {"outflow_doc": {"$arrayElemAt": ["$outflow_doc", 0]}}},
        {
            "$addFields": {
                "total_profit_num": {
                    "$convert": {
                        "input": {"$ifNull": ["$outflow_doc.total_profit", 0]},
                        "to": "double",
                        "onError": 0.0,
                        "onNull": 0.0,
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "users",
                "let": {"mid": "$manager_oid"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$_id", "$$mid"]},
                                    {"$eq": ["$role", "manager"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"name": 1}},
                ],
                "as": "manager_doc",
            }
        },
        {"$addFields": {"manager_doc": {"$arrayElemAt": ["$manager_doc", 0]}}},
        {"$addFields": {"manager_name": {"$ifNull": ["$manager_doc.name", ""]}}},
        {"$addFields": {"customer_id_str": {"$toString": "$customer_id"}}},
    ]

    if status_filter:
        pipeline.append({"$match": {"status_code": status_filter}})

    return pipeline


def _resolve_match_and_pipeline(ident: Dict[str, Any], args, include_date: bool, start: datetime, end: datetime) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[str]]:
    clauses = _build_match_clauses(ident, args, include_date, start, end)
    match = _merge_match(clauses)
    delivery_status = _delivery_status_filter_value(args)
    pipeline = _packages_pipeline(match, delivery_status)
    return match, pipeline, delivery_status


@completed_cards_bp.route("/completed-cards", methods=["GET"])
def completed_cards_page():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))
    role = ident.get("role")
    if role not in ("agent", "manager", "executive", "admin"):
        return "Forbidden", 403

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except Exception:
        page = 1
    per_page = 30

    clauses = _build_match_clauses(ident, request.args, True, start_dt, end_dt)
    match = _merge_match(clauses)
    status_filter = _delivery_status_filter_value(request.args)
    pipeline = _packages_pipeline(match, status_filter)

    facets = {
        "rows": [
            {"$sort": {"delivery_sort": 1, "created_at": -1}},
            {"$skip": (page - 1) * per_page},
            {"$limit": per_page},
            {"$project": {
                "created_at": 1,
                "customer_id": 1,
                "customer_id_str": 1,
                "customer_name": 1,
                "customer_phone": 1,
                "product_index": 1,
                "product_name": 1,
                "product_image": 1,
                "qty_resolved": 1,
                "total_paid_selected_product": 1,
                "product_total_resolved": 1,
                "agent_id": 1,
                "agent_name": 1,
                "agent_branch": 1,
                "manager_id": 1,
                "manager_name": 1,
                "purchase_type": 1,
                "source": 1,
                "status": 1,
                "status_code": 1,
                "status_label": 1,
                "outflow_doc": 1,
            }},
        ],
        "total_count": [
            {"$count": "count"},
        ],
    }

    pipeline_with_facets = pipeline + [{"$facet": facets}]
    result = list(packages_col.aggregate(pipeline_with_facets))
    facet = result[0] if result else {}

    rows = facet.get("rows") or []
    total_count = int((facet.get("total_count") or [{}])[0].get("count") or 0)
    total_pages = max(1, int(ceil(total_count / float(per_page)))) if total_count else 1
    if page > total_pages and total_pages > 0:
        return redirect(url_for("completed_cards.completed_cards_page", start=start_str, end=end_str, page=total_pages))

    manager_id = (request.args.get("manager_id") or "").strip() if role in ("executive", "admin") else ""
    branch = (request.args.get("branch") or "").strip() if role in ("manager", "executive", "admin") else ""
    agent_id = (request.args.get("agent_id") or "").strip() if role in ("manager", "executive", "admin") else ""

    managers, branches, agents = _list_options_for_role(ident, manager_id, branch)

    return render_template(
        "completed_cards.html",
        rows=rows,
        start=start_str,
        end=end_str,
        page=page,
        total_pages=total_pages,
        can_mark_delivered=role == "agent",
        managers=managers,
        branches=branches,
        agents=agents,
        selected_manager=manager_id,
        selected_branch=branch,
        selected_agent=agent_id,
        selected_delivery_status=status_filter or "",
        quick_ranges=_date_quick_ranges(),
    )


@completed_cards_bp.route("/completed-cards/<customer_id>/<int:product_index>/mark-delivered", methods=["POST"])
def completed_card_mark_delivered(customer_id: str, product_index: int):
    """Confirm delivery after the card reaches Packaging and stock is settled."""
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return jsonify(ok=False, message="Please sign in as an agent."), 401
    if ident.get("role") != "agent":
        return jsonify(ok=False, message="Only agents can mark their product cards delivered."), 403

    try:
        result = deliver_package_and_deduct(customer_id, product_index, ident, photo=request.files.get('delivery_photo'))
        message = "Product marked Delivered. Packaging stock deductions are complete."
        return jsonify(ok=True, message=message, result=result)
    except StockDeductionError as exc:
        return jsonify(ok=False, message=str(exc), code=exc.code), exc.status
    except Exception:
        return jsonify(ok=False, message="Delivery could not be completed. No stock was changed."), 500


@completed_cards_bp.route("/completed-cards/analytics", methods=["GET"])
def completed_cards_analytics():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))
    role = ident.get("role")
    if role not in ("agent", "manager", "executive", "admin"):
        return "Forbidden", 403

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)

    match, pipeline, delivery_status = _resolve_match_and_pipeline(ident, request.args, True, start_dt, end_dt)
    range_days = max(1, (end_dt - start_dt).days)
    use_week = range_days > 60

    time_group = {
        "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
    }
    if use_week:
        time_group = {
            "$concat": [
                {"$toString": {"$isoWeekYear": "$created_at"}},
                "-W",
                {"$toString": {"$isoWeek": "$created_at"}},
            ]
        }

    facets = {
        "totals": [
            {"$group": {
                "_id": None,
                "total_completed": {"$sum": 1},
                "total_qty": {"$sum": "$qty_num"},
                "total_paid": {"$sum": "$total_paid_num"},
                "total_value": {"$sum": "$product_total_num"},
                "total_profit": {"$sum": "$total_profit_num"},
            }},
        ],
        "delivery_counts": [
            {"$group": {"_id": "$status_code", "count": {"$sum": 1}}},
        ],
        "trend": [
            {"$group": {"_id": time_group, "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
        "top_branches": [
            {"$group": {"_id": "$agent_branch", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ],
        "top_agents": [
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}, "agent_name": {"$first": "$agent_name"}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ],
        "top_products": [
            {"$group": {"_id": "$product_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ],
    }

    pipeline_with_facets = pipeline + [{"$facet": facets}]
    result = list(packages_col.aggregate(pipeline_with_facets))
    facet = result[0] if result else {}

    totals_raw = (facet.get("totals") or [{}])[0] or {}
    delivery_raw = {d.get("_id"): int(d.get("count") or 0) for d in (facet.get("delivery_counts") or [])}

    total_completed = int(totals_raw.get("total_completed") or 0)
    delivered = int(delivery_raw.get("delivered") or 0)
    delivered_pct = round((delivered / total_completed) * 100, 1) if total_completed else 0.0
    avg_value = round((float(totals_raw.get("total_value") or 0) / total_completed), 2) if total_completed else 0.0

    manager_id = (request.args.get("manager_id") or "").strip() if role in ("executive", "admin") else ""
    branch = (request.args.get("branch") or "").strip() if role in ("manager", "executive", "admin") else ""
    agent_id = (request.args.get("agent_id") or "").strip() if role in ("manager", "executive", "admin") else ""

    managers, branches, agents = _list_options_for_role(ident, manager_id, branch)

    return render_template(
        "completed_cards_analytics.html",
        start=start_str,
        end=end_str,
        managers=managers,
        branches=branches,
        agents=agents,
        selected_manager=manager_id,
        selected_branch=branch,
        selected_agent=agent_id,
        selected_delivery_status=delivery_status or "",
        totals={
            "total_completed": total_completed,
            "total_qty": float(totals_raw.get("total_qty") or 0),
            "total_value": float(totals_raw.get("total_value") or 0),
            "total_paid": float(totals_raw.get("total_paid") or 0),
            "total_profit": float(totals_raw.get("total_profit") or 0),
            "delivered_pct": delivered_pct,
            "avg_value": avg_value,
        },
        trend=facet.get("trend") or [],
        top_branches=facet.get("top_branches") or [],
        top_agents=facet.get("top_agents") or [],
        top_products=facet.get("top_products") or [],
        quick_ranges=_date_quick_ranges(),
        use_week=use_week,
    )


@completed_cards_bp.route("/completed-cards/options/managers", methods=["GET"])
def completed_cards_options_managers():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))
    role = ident.get("role")
    if role not in ("executive", "admin"):
        return "Forbidden", 403

    managers = list(users_col.find({"role": "manager"}, {"name": 1}))
    return {
        "ok": True,
        "managers": [{"id": str(m.get("_id")), "name": m.get("name") or "Manager"} for m in managers],
    }


@completed_cards_bp.route("/completed-cards/options/branches", methods=["GET"])
def completed_cards_options_branches():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))

    role = ident.get("role")
    manager_id = (request.args.get("manager_id") or "").strip()

    if role == "manager":
        manager_id = session.get("manager_id") or ident.get("user_id") or ""

    agents_filter: Dict[str, Any] = {"role": "agent"}
    if manager_id:
        mid = _safe_oid(manager_id) or str(manager_id)
        agents_filter["$or"] = [{"manager_id": mid}, {"manager_id": str(mid)}]

    branches = users_col.distinct("branch", agents_filter) if agents_filter else []
    branches = sorted([b for b in branches if b])

    return {"ok": True, "branches": branches}


@completed_cards_bp.route("/completed-cards/options/agents", methods=["GET"])
def completed_cards_options_agents():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))

    role = ident.get("role")
    manager_id = (request.args.get("manager_id") or "").strip()
    branch = (request.args.get("branch") or "").strip()

    if role == "agent":
        agent_id = session.get("agent_id") or ident.get("user_id")
        agent_doc = users_col.find_one({"_id": _safe_oid(agent_id) or agent_id}, {"name": 1, "branch": 1}) if agent_id else None
        if not agent_doc:
            return {"ok": True, "agents": []}
        return {"ok": True, "agents": [{"id": str(agent_doc.get("_id")), "name": agent_doc.get("name") or "Agent", "branch": agent_doc.get("branch") or ""}]}

    if role == "manager":
        manager_id = session.get("manager_id") or ident.get("user_id") or ""

    agents_filter: Dict[str, Any] = {"role": "agent"}
    if manager_id:
        mid = _safe_oid(manager_id) or str(manager_id)
        agents_filter["$or"] = [{"manager_id": mid}, {"manager_id": str(mid)}]
    if branch:
        agents_filter["branch"] = branch

    agents = list(users_col.find(agents_filter, {"name": 1, "branch": 1}))

    return {
        "ok": True,
        "agents": [{"id": str(a.get("_id")), "name": a.get("name") or "Agent", "branch": a.get("branch") or ""} for a in agents],
    }


@completed_cards_bp.route("/completed-cards/export.csv", methods=["GET"])
def completed_cards_export_csv():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))

    role = ident.get("role")
    if role not in ("agent", "manager", "executive", "admin"):
        return "Forbidden", 403

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)
    clauses = _build_match_clauses(ident, request.args, True, start_dt, end_dt)
    match = _merge_match(clauses)
    status_filter = _delivery_status_filter_value(request.args)

    pipeline = _packages_pipeline(match, status_filter) + [{"$project": {
        "created_at": 1,
        "customer_id": 1,
        "customer_name": 1,
        "customer_phone": 1,
        "product_name": 1,
        "qty": 1,
        "total_paid_selected_product": 1,
        "product_total": 1,
        "agent_name": 1,
        "agent_branch": 1,
        "manager_name": 1,
        "status_label": 1,
    }}]

    rows = list(packages_col.aggregate(pipeline))

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "created_at",
        "manager",
        "branch",
        "agent",
        "customer",
        "phone",
        "product",
        "qty",
        "product_total",
        "paid",
        "status",
    ])
    for r in rows:
        created_at = r.get("created_at")
        created_str = created_at.strftime("%Y-%m-%d %H:%M") if created_at else ""
        writer.writerow([
            created_str,
            r.get("manager_name") or "",
            r.get("agent_branch") or "",
            r.get("agent_name") or "",
            r.get("customer_name") or "",
            r.get("customer_phone") or "",
            r.get("product_name") or "",
            r.get("qty") or 0,
            r.get("product_total") or 0,
            r.get("total_paid_selected_product") or 0,
            r.get("status_label") or "",
        ])

    filename = f"completed_cards_{start_str}_to_{end_str}.csv"
    resp = Response(output.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp
