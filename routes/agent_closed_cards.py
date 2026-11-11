from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple

from flask import Blueprint, redirect, render_template, request, url_for

from db import db
from login import get_current_identity

agent_closed_cards_bp = Blueprint("agent_closed_cards", __name__, url_prefix="/agent")

card_closures_col = db["card_closures"]


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


def _summary_counts(agent_id_str: str, start: datetime, end: datetime) -> Dict[str, int]:
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)
    month_start = datetime(now.year, now.month, 1)
    next_month = datetime(now.year + (1 if now.month == 12 else 0), (1 if now.month == 12 else now.month + 1), 1)

    pipeline = [
        {"$match": {"action": "close_card"}},
        {"$addFields": {"closed_at": {"$ifNull": ["$at", "$created_at"]}}},
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
            "agent_id_raw": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]},
            "agent_id_str_norm": {"$toString": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]}},
        }},
        {"$match": {"agent_id_str_norm": agent_id_str}},
        {"$facet": {
            "range_total": [
                {"$match": {"closed_at": {"$gte": start, "$lt": end}}},
                {"$count": "count"},
            ],
            "closed_today": [
                {"$match": {"closed_at": {"$gte": today_start, "$lt": tomorrow_start}}},
                {"$count": "count"},
            ],
            "closed_week": [
                {"$match": {"closed_at": {"$gte": week_start, "$lt": week_end}}},
                {"$count": "count"},
            ],
            "closed_month": [
                {"$match": {"closed_at": {"$gte": month_start, "$lt": next_month}}},
                {"$count": "count"},
            ],
        }},
    ]

    result = list(card_closures_col.aggregate(pipeline))
    facet = result[0] if result else {}

    def _count(key: str) -> int:
        return int((facet.get(key) or [{}])[0].get("count") or 0)

    return {
        "total_closed": _count("range_total"),
        "closed_today": _count("closed_today"),
        "closed_week": _count("closed_week"),
        "closed_month": _count("closed_month"),
    }


@agent_closed_cards_bp.route("/closed-cards", methods=["GET"])
def my_closed_cards_page():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return redirect(url_for("login.login", next=request.path))
    if ident.get("role") != "agent":
        return "Forbidden", 403

    agent_id = ident.get("user_id") or ""
    agent_id = str(agent_id)
    if not agent_id:
        return "Forbidden", 403

    start_dt, end_dt, start_str, end_str = _parse_date_range(request.args)

    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except Exception:
        page = 1
    page_size = 25
    skip = (page - 1) * page_size

    pipeline = [
        {"$match": {"action": "close_card"}},
        {"$addFields": {"closed_at": {"$ifNull": ["$at", "$created_at"]}}},
        {"$match": {"closed_at": {"$gte": start_dt, "$lt": end_dt}}},
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
            "agent_id_raw": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]},
            "agent_id_str_norm": {"$toString": {"$ifNull": ["$selected_purchase.agent_id", "$customer.agent_id"]}},
        }},
        {"$match": {"agent_id_str_norm": agent_id}},
        {"$addFields": {
            "kept_amount_num": {
                "$convert": {"input": "$payload.kept_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
            "forfeited_amount_num": {
                "$convert": {"input": "$payload.forfeited_amount", "to": "double", "onError": 0.0, "onNull": 0.0}
            },
        }},
        {"$facet": {
            "rows": [
                {"$sort": {"closed_at": -1}},
                {"$skip": skip},
                {"$limit": page_size},
                {"$project": {
                    "_id": 1,
                    "closed_at": 1,
                    "payload": 1,
                    "kept_amount_num": 1,
                    "forfeited_amount_num": 1,
                    "selected_purchase.product.name": 1,
                    "customer.name": 1,
                    "customer.phone_number": 1,
                    "customer.branch": 1,
                }},
            ],
            "total_count": [{"$count": "count"}],
        }},
    ]

    result = list(card_closures_col.aggregate(pipeline))
    facet = result[0] if result else {}
    rows = facet.get("rows") or []
    total = int((facet.get("total_count") or [{}])[0].get("count") or 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages and total_pages > 0:
        return redirect(url_for("agent_closed_cards.my_closed_cards_page", start=start_str, end=end_str, page=total_pages))

    counts = _summary_counts(agent_id, start_dt, end_dt)

    return render_template(
        "agent_closed_cards.html",
        rows=rows,
        page=page,
        total_pages=total_pages,
        start=start_str,
        end=end_str,
        counts=counts,
    )
