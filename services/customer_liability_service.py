from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId
from openpyxl import Workbook
from pymongo import DESCENDING
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cache_ext import cache
from db import db

customers_col = db["customers"]
payments_col = db["payments"]
packages_col = db["packages"]
undelivered_items_col = db["undelivered_items"]
users_col = db["users"]
inventory_col = db["inventory"]
liability_settings_col = db["liability_settings"]
activity_logs_col = db["activity_logs"]


def ensure_liability_indexes() -> None:
    try:
        payments_col.create_index([("customer_id", 1), ("product_index", 1), ("payment_type", 1), ("date", -1)])
        payments_col.create_index([("manager_id", 1), ("payment_type", 1), ("date", -1), ("customer_id", 1)])
        payments_col.create_index([("agent_id", 1), ("payment_type", 1), ("date", -1), ("customer_id", 1)])
        packages_col.create_index([("customer_id", 1), ("product_index", 1), ("status", 1), ("delivered_at", -1)])
        undelivered_items_col.create_index([("customer_id", 1), ("product_index", 1), ("status", 1), ("updated_at", -1)])
        liability_settings_col.create_index([("key", 1)], unique=True)
    except Exception:
        pass


ensure_liability_indexes()


def _round_money(value: Any) -> float:
    try:
        return round(float(value or 0.0) + 1e-9, 2)
    except Exception:
        return 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return default
        try:
            return float(cleaned)
        except Exception:
            return default
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _parse_date(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            if value > 10**12:
                value = value / 1000.0
            return datetime.utcfromtimestamp(value)
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


def _date_to_key(value: Any) -> str:
    parsed = _parse_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _datetime_to_json(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d") if isinstance(value, datetime) else ""


def _money_label(value: float) -> str:
    return f"GHS {value:,.2f}"


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _serialize_filters(filters: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    serializable = []
    for key, value in sorted(filters.items()):
        if isinstance(value, list):
            value = tuple(value)
        serializable.append((key, value))
    return tuple(serializable)


def _empty_exclusion_stats() -> Dict[str, Dict[str, float]]:
    return {
        "susu": {"count": 0, "amount": 0.0},
        "completed": {"count": 0, "amount": 0.0},
        "closed": {"count": 0, "amount": 0.0},
        "ambiguous_legacy": {"count": 0, "amount": 0.0},
    }


def _month_range(as_of_dt: datetime) -> Tuple[str, str]:
    start = as_of_dt.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def get_liability_settings() -> Dict[str, Any]:
    doc = liability_settings_col.find_one({"key": "default"}) or {}
    sla_days = _safe_int(doc.get("delivery_sla_days"), 7)
    inactive_days = _safe_int(doc.get("inactive_customer_days"), 21)
    manual_adjustment_threshold = _safe_float(doc.get("manual_adjustment_threshold"), 1000.0)
    return {
        "delivery_sla_days": max(sla_days, 1),
        "inactive_customer_days": max(inactive_days, 1),
        "manual_adjustment_threshold": max(manual_adjustment_threshold, 0.0),
    }


def normalize_filters(args) -> Dict[str, Any]:
    today = datetime.utcnow().date()
    as_of_raw = (args.get("as_of_date") or "").strip()
    start_raw = (args.get("start_date") or "").strip()
    end_raw = (args.get("end_date") or "").strip()

    as_of_dt = _parse_date(as_of_raw) or datetime.combine(today, datetime.min.time())
    start_dt = _parse_date(start_raw)
    end_dt = _parse_date(end_raw)
    if end_dt:
        end_dt = end_dt + timedelta(days=1)

    page = max(_safe_int(args.get("page"), 1), 1)
    page_size = min(max(_safe_int(args.get("page_size"), 25), 1), 100)

    return {
        "as_of_date": as_of_dt.strftime("%Y-%m-%d"),
        "as_of_dt": as_of_dt,
        "start_date": start_dt.strftime("%Y-%m-%d") if start_dt else "",
        "start_dt": start_dt,
        "end_date": (end_dt - timedelta(days=1)).strftime("%Y-%m-%d") if end_dt else "",
        "end_dt": end_dt,
        "branch": (args.get("branch") or "").strip(),
        "agent_id": (args.get("agent_id") or "").strip(),
        "product_query": (args.get("product") or "").strip(),
        "search": (args.get("search") or "").strip(),
        "liability_category": (args.get("liability_category") or "").strip().lower(),
        "payment_stage": (args.get("payment_stage") or "").strip().lower(),
        "delivery_stage": (args.get("delivery_stage") or "").strip().lower(),
        "risk_level": (args.get("risk_level") or "").strip().lower(),
        "sla_status": (args.get("sla_status") or "").strip().lower(),
        "page": page,
        "page_size": page_size,
        "skip": (page - 1) * page_size,
    }


def _payment_match(filters: Dict[str, Any]) -> Dict[str, Any]:
    clauses: List[Dict[str, Any]] = [
        {"product_index": {"$ne": None}},
        {"payment_type": {"$in": ["PRODUCT", "WITHDRAWAL", None]}},
    ]
    if filters.get("agent_id"):
        clauses.append({"agent_id": filters["agent_id"]})
    if filters.get("branch"):
        branch = filters["branch"]
        manager_ids = [doc["_id"] for doc in users_col.find({"role": "manager", "branch": branch}, {"_id": 1})]
        manager_variants: List[Any] = [branch]
        manager_variants.extend(manager_ids)
        manager_variants.extend(str(mid) for mid in manager_ids)
        clauses.append(
            {
                "$or": [
                    {"branch_name": branch},
                    {"branch_id": branch},
                    {"manager_id": {"$in": manager_variants}},
                ]
            }
        )
    if filters.get("start_dt") and filters.get("end_dt"):
        clauses.append({"date": {"$gte": filters["start_date"], "$lte": filters["end_date"]}})
    else:
        clauses.append({"date": {"$lte": filters["as_of_date"]}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _payments_group_pipeline(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    match_stage = _payment_match(filters)
    return [
        {"$match": match_stage},
        {
            "$addFields": {
                "amount_num": {"$convert": {"input": "$amount", "to": "double", "onError": 0.0, "onNull": 0.0}},
                "date_key": {"$substrBytes": [{"$ifNull": ["$date", ""]}, 0, 10]},
            }
        },
        {
            "$group": {
                "_id": {"customer_id": "$customer_id", "product_index": "$product_index"},
                "verified_payments": {
                    "$sum": {
                        "$cond": [
                            {"$or": [{"$eq": ["$payment_type", "PRODUCT"]}, {"$eq": ["$payment_type", None]}]},
                            "$amount_num",
                            0.0,
                        ]
                    }
                },
                "reversals": {
                    "$sum": {
                        "$cond": [{"$eq": ["$payment_type", "WITHDRAWAL"]}, "$amount_num", 0.0]
                    }
                },
                "first_payment_date": {"$min": "$date_key"},
                "last_payment_date": {"$max": "$date_key"},
                "payment_count": {"$sum": 1},
                "product_name": {"$last": "$product_name"},
                "product_total": {
                    "$max": {"$convert": {"input": "$product_total", "to": "double", "onError": 0.0, "onNull": 0.0}}
                },
                "agent_id": {"$last": "$agent_id"},
                "manager_id": {"$last": "$manager_id"},
                "payment_types": {"$addToSet": "$payment_type"},
            }
        },
    ]


def _payment_exclusion_stats_pipeline(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    match_stage = _payment_match(filters)
    return [
        {"$match": match_stage},
        {
            "$addFields": {
                "amount_num": {"$convert": {"input": "$amount", "to": "double", "onError": 0.0, "onNull": 0.0}},
            }
        },
        {
            "$group": {
                "_id": {"payment_type": "$payment_type", "customer_id": "$customer_id", "product_index": "$product_index"},
                "amount": {"$sum": "$amount_num"},
                "count": {"$sum": 1},
            }
        },
    ]


def _susu_stats(filters: Dict[str, Any]) -> Dict[str, float]:
    match: Dict[str, Any] = {
        "payment_type": {"$in": ["SUSU", "SUS"]},
        "product_index": {"$ne": None},
    }
    if filters.get("agent_id"):
        match["agent_id"] = filters["agent_id"]
    if filters.get("branch"):
        branch = filters["branch"]
        manager_ids = [doc["_id"] for doc in users_col.find({"role": "manager", "branch": branch}, {"_id": 1})]
        match["$or"] = [
            {"branch_name": branch},
            {"branch_id": branch},
            {"manager_id": {"$in": manager_ids + [str(mid) for mid in manager_ids]}},
        ]
    if filters.get("start_dt") and filters.get("end_dt"):
        match["date"] = {"$gte": filters["start_date"], "$lte": filters["end_date"]}
    else:
        match["date"] = {"$lte": filters["as_of_date"]}
    row = next(
        payments_col.aggregate(
            [
                {"$match": match},
                {"$group": {"_id": None, "count": {"$sum": 1}, "amount": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
            ]
        ),
        None,
    )
    return {"count": int((row or {}).get("count") or 0), "amount": _round_money((row or {}).get("amount") or 0.0)}


def _load_user_maps(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    agent_ids = {str(row.get("agent_id") or "") for row in rows if row.get("agent_id")}
    manager_ids = {str(row.get("manager_id") or "") for row in rows if row.get("manager_id")}
    agent_oids = [_safe_oid(value) for value in agent_ids]
    manager_oids = [_safe_oid(value) for value in manager_ids]
    user_docs = list(
        users_col.find(
            {"_id": {"$in": [oid for oid in agent_oids + manager_oids if oid]}},
            {"name": 1, "branch": 1, "role": 1},
        )
    )
    user_map = {str(doc["_id"]): doc for doc in user_docs}
    return user_map, user_map


def _load_customers(customer_ids: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    customer_id_list = list(customer_ids)
    oids = [cid for cid in customer_id_list if isinstance(cid, ObjectId)]
    docs = customers_col.find(
        {"_id": {"$in": oids}},
        {"name": 1, "phone_number": 1, "purchases": 1, "agent_id": 1, "manager_id": 1, "status": 1, "location": 1},
    )
    return {str(doc["_id"]): doc for doc in docs}


def _load_packages(customer_ids: Iterable[ObjectId]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    pipeline = [
        {"$match": {"customer_id": {"$in": list(customer_ids)}}},
        {"$sort": {"delivered_at": -1, "submitted_at": -1, "_id": -1}},
        {
            "$group": {
                "_id": {"customer_id": "$customer_id", "product_index": "$product_index"},
                "doc": {"$first": "$$ROOT"},
            }
        },
    ]
    result: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in packages_col.aggregate(pipeline, allowDiskUse=True):
        key = (str(row["_id"]["customer_id"]), _safe_int(row["_id"]["product_index"]))
        result[key] = row["doc"]
    return result


def _load_undelivered(customer_ids: Iterable[ObjectId]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    pipeline = [
        {"$match": {"customer_id": {"$in": list(customer_ids)}}},
        {"$sort": {"updated_at": -1, "_id": -1}},
        {
            "$group": {
                "_id": {"customer_id": "$customer_id", "product_index": "$product_index"},
                "doc": {"$first": "$$ROOT"},
            }
        },
    ]
    result: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in undelivered_items_col.aggregate(pipeline, allowDiskUse=True):
        key = (str(row["_id"]["customer_id"]), _safe_int(row["_id"]["product_index"]))
        result[key] = row["doc"]
    return result


def _load_page_ledgers(customer_ids: Iterable[ObjectId], as_of_date: str) -> Dict[Tuple[str, int], List[Dict[str, Any]]]:
    docs = payments_col.find(
        {
            "customer_id": {"$in": list(customer_ids)},
            "product_index": {"$ne": None},
            "date": {"$lte": as_of_date},
            "payment_type": {"$in": ["PRODUCT", "WITHDRAWAL", None]},
        },
        {
            "customer_id": 1,
            "product_index": 1,
            "amount": 1,
            "date": 1,
            "time": 1,
            "method": 1,
            "note": 1,
            "payment_type": 1,
        },
    ).sort([("date", 1), ("_id", 1)])
    grouped: Dict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        key = (str(doc.get("customer_id")), _safe_int(doc.get("product_index")))
        grouped[key].append(doc)
    return grouped


def _purchase_ref(customer_id: str, product_index: int, purchase: Dict[str, Any]) -> str:
    for key in ("purchase_id", "order_id", "_id", "id"):
        value = purchase.get(key)
        if value:
            return str(value)
    return f"{customer_id}-{product_index}"


def _purchase_status_details(purchase: Dict[str, Any]) -> Dict[str, Any]:
    product = (purchase or {}).get("product") or {}
    purchase_status = _normalized_status((purchase or {}).get("status"))
    product_status = _normalized_status(product.get("status"))
    packaging_status = _normalized_status(product.get("packaging_status"))
    statuses = [status for status in [purchase_status, product_status, packaging_status] if status]
    is_closed = "closed" in statuses
    is_completed = "completed" in statuses
    effective_status = purchase_status or product_status or packaging_status or "active"
    return {
        "purchase_status_field": purchase_status,
        "product_status_field": product_status,
        "packaging_status_field": packaging_status,
        "effective_status": effective_status,
        "is_closed": is_closed,
        "is_completed": is_completed,
        "is_active_liability_eligible": not is_closed and not is_completed,
    }


def _delivery_state(purchase: Dict[str, Any], package_doc: Optional[Dict[str, Any]]) -> Tuple[bool, str, Optional[datetime]]:
    product = (purchase or {}).get("product") or {}
    status_values = [
        str((purchase or {}).get("status") or "").strip().lower(),
        str(product.get("status") or "").strip().lower(),
        str(product.get("packaging_status") or "").strip().lower(),
        str((package_doc or {}).get("status") or "").strip().lower(),
    ]
    delivered_at = _parse_date((purchase or {}).get("delivered_at")) or _parse_date(product.get("delivered_at")) or _parse_date((package_doc or {}).get("delivered_at"))
    delivered = bool(delivered_at) or "delivered" in status_values
    stage = "delivered" if delivered else "awaiting-delivery"
    if not delivered and any(status in {"packaging", "delivering"} for status in status_values):
        stage = next(status for status in status_values if status in {"packaging", "delivering"})
    return delivered, stage, delivered_at


def _derive_fully_paid_at(ledger: List[Dict[str, Any]], order_total: float) -> str:
    if order_total <= 0:
        return ""
    running = 0.0
    for entry in ledger:
        payment_type = str(entry.get("payment_type") or "PRODUCT").upper()
        amount = _safe_float(entry.get("amount"))
        running += amount if payment_type != "WITHDRAWAL" else -amount
        if running + 1e-9 >= order_total:
            return str(entry.get("date") or "")[:10]
    return ""


def _load_inventory_map(product_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    oids = [_safe_oid(value) for value in set(product_ids) if value]
    docs = inventory_col.find({"_id": {"$in": [oid for oid in oids if oid]}}, {"qty": 1, "name": 1, "cost_price": 1, "price": 1})
    return {str(doc["_id"]): doc for doc in docs}


def _stock_summary(product: Dict[str, Any], inventory_map: Dict[str, Dict[str, Any]]) -> Tuple[str, float, float, float]:
    stable_id = product.get("_id") or product.get("product_id")
    if stable_id:
        inv = inventory_map.get(str(stable_id))
        if inv:
            qty = _safe_float(inv.get("qty"))
            cost_price = _safe_float(inv.get("cost_price"))
            price = _safe_float(inv.get("price"))
            return "matched", qty, cost_price, price
    return "legacy-unmatched", 0.0, 0.0, _safe_float(product.get("price"))


def _build_row(
    payment_row: Dict[str, Any],
    customer_doc: Dict[str, Any],
    package_doc: Optional[Dict[str, Any]],
    undelivered_doc: Optional[Dict[str, Any]],
    agent_doc: Dict[str, Any],
    manager_doc: Dict[str, Any],
    settings: Dict[str, Any],
    inventory_map: Dict[str, Dict[str, Any]],
    ledger: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    customer_id = str(customer_doc.get("_id") or "")
    product_index = _safe_int((payment_row.get("_id") or {}).get("product_index"))
    purchases = customer_doc.get("purchases") or []
    if product_index < 0 or product_index >= len(purchases):
        return None
    purchase = purchases[product_index] or {}
    product = purchase.get("product") or {}
    status_details = _purchase_status_details(purchase)
    order_total = _safe_float(product.get("total")) or _safe_float(payment_row.get("product_total")) or (_safe_float(product.get("price")) * max(_safe_float(product.get("quantity"), 1.0), 1.0))
    verified_payments = _round_money(payment_row.get("verified_payments"))
    reversals = _round_money(payment_row.get("reversals"))
    net_verified_payment = _round_money(verified_payments - reversals)
    overpayment = _round_money(max(net_verified_payment - order_total, 0.0))
    remaining_customer_balance = _round_money(max(order_total - net_verified_payment, 0.0))
    delivered, delivery_stage, delivered_at = _delivery_state(purchase, package_doc)
    active_undelivered_liability = 0.0
    if status_details["is_active_liability_eligible"] and not delivered:
        active_undelivered_liability = _round_money(min(net_verified_payment, order_total))
    current_liability = _round_money(active_undelivered_liability)
    if current_liability <= 0:
        return None

    fully_paid = order_total > 0 and net_verified_payment + 1e-9 >= order_total
    payment_stage = "fully-paid" if fully_paid else "partially-paid"
    liability_category = "fully-paid-awaiting-delivery" if fully_paid and active_undelivered_liability > 0 and not delivered else "partially-paid"
    first_payment_date = str(payment_row.get("first_payment_date") or "")
    last_payment_date = str(payment_row.get("last_payment_date") or "")
    if ledger is None:
        ledger = []
    fully_paid_at = _derive_fully_paid_at(ledger, order_total)

    reference_date = _parse_date(fully_paid_at or first_payment_date or purchase.get("purchase_date"))
    days_waiting = max((datetime.utcnow() - reference_date).days, 0) if reference_date else 0
    sla_days = settings["delivery_sla_days"]
    sla_status = "breached" if (fully_paid and not delivered and days_waiting > sla_days) else "within-sla"
    if not fully_paid:
        sla_status = "not-applicable"

    risk_level = "low"
    if sla_status == "breached":
        risk_level = "high"
    elif fully_paid and not delivered:
        risk_level = "medium"
    if row_last_payment_stale := _parse_date(last_payment_date):
        if ((datetime.utcnow() - row_last_payment_stale).days > settings["inactive_customer_days"]) and active_undelivered_liability > 0:
            risk_level = "high"

    stock_status, available_qty, cost_price, selling_price = _stock_summary(product, inventory_map)
    quantity = max(_safe_float(product.get("quantity"), 1.0), 1.0)
    estimated_cost_to_fulfil = _round_money(max(quantity, 0.0) * cost_price)
    stock_coverage = 100.0 if available_qty >= quantity else round((available_qty / quantity) * 100.0, 1)
    margin_at_risk = _round_money(max((selling_price - cost_price) * quantity, 0.0)) if selling_price and cost_price else 0.0

    branch = str(manager_doc.get("branch") or agent_doc.get("branch") or "")
    agent_name = str(agent_doc.get("name") or "Unknown")
    manager_name = str(manager_doc.get("name") or "")
    customer_phone = str(customer_doc.get("phone_number") or "")
    purchase_ref = _purchase_ref(customer_id, product_index, purchase)
    payment_completion_pct = round((min(net_verified_payment, order_total) / order_total) * 100.0, 1) if order_total > 0 else 0.0

    return {
        "customer_id": customer_id,
        "customer_name": customer_doc.get("name") or "Customer",
        "customer_phone": customer_phone,
        "customer_status": customer_doc.get("status") or "",
        "purchase_index": product_index,
        "purchase_ref": purchase_ref,
        "branch": branch,
        "agent_id": str(payment_row.get("agent_id") or ""),
        "agent_name": agent_name,
        "manager_id": str(payment_row.get("manager_id") or ""),
        "manager_name": manager_name,
        "product_name": product.get("name") or payment_row.get("product_name") or "Unknown Product",
        "product_id": str(product.get("_id") or ""),
        "sku": str(product.get("_id") or product.get("product_id") or ""),
        "quantity": quantity,
        "agreed_order_value": _round_money(order_total),
        "verified_amount_paid": net_verified_payment,
        "remaining_balance": remaining_customer_balance,
        "current_liability": current_liability,
        "active_undelivered_liability": active_undelivered_liability,
        "refund_liability": 0.0,
        "overpayment": overpayment,
        "payment_completion_pct": payment_completion_pct,
        "first_payment_date": first_payment_date,
        "last_payment_date": last_payment_date,
        "fully_paid_at": fully_paid_at,
        "days_awaiting_delivery": days_waiting if not delivered else 0,
        "stock_availability": available_qty,
        "stock_status": stock_status,
        "delivery_status": "Delivered" if delivered else delivery_stage.replace("-", " ").title(),
        "refund_status": "None",
        "risk_level": risk_level,
        "sla_status": sla_status,
        "liability_category": liability_category,
        "payment_stage": payment_stage,
        "delivery_stage": delivery_stage,
        "responsible_staff_member": manager_name or agent_name,
        "delivered_at": _datetime_to_json(delivered_at),
        "purchase_date": str(purchase.get("purchase_date") or ""),
        "purchase_type": str(purchase.get("purchase_type") or ""),
        "product_status": status_details["product_status_field"],
        "purchase_status": status_details["purchase_status_field"],
        "effective_purchase_status": status_details["effective_status"],
        "expected_delivery_date": str((undelivered_doc or {}).get("expected_delivery_date") or ""),
        "estimated_cost_to_fulfil": estimated_cost_to_fulfil,
        "stock_coverage_pct": round(max(min(stock_coverage, 100.0), 0.0), 1),
        "margin_at_risk": margin_at_risk,
        "ledger_count": len(ledger),
        "is_long_inactive": bool(row_last_payment_stale and ((datetime.utcnow() - row_last_payment_stale).days > settings["inactive_customer_days"])),
        "has_stock_shortage": available_qty < quantity,
    }


def _apply_row_filters(rows: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    filtered = []
    search = filters.get("search", "").lower()
    product_query = filters.get("product_query", "").lower()
    for row in rows:
        if search:
            hay = " ".join(
                [
                    str(row.get("customer_name") or ""),
                    str(row.get("customer_phone") or ""),
                    str(row.get("purchase_ref") or ""),
                ]
            ).lower()
            if search not in hay:
                continue
        if product_query:
            hay = " ".join([str(row.get("product_name") or ""), str(row.get("sku") or "")]).lower()
            if product_query not in hay:
                continue
        if filters.get("liability_category") and row.get("liability_category") != filters["liability_category"]:
            continue
        if filters.get("payment_stage") and row.get("payment_stage") != filters["payment_stage"]:
            continue
        if filters.get("delivery_stage") and row.get("delivery_stage") != filters["delivery_stage"]:
            continue
        if filters.get("risk_level") and row.get("risk_level") != filters["risk_level"]:
            continue
        if filters.get("sla_status") and row.get("sla_status") != filters["sla_status"]:
            continue
        filtered.append(row)
    return filtered


def _classify_payment_group(
    payment_row: Dict[str, Any],
    customer_doc: Optional[Dict[str, Any]],
    exclusion_stats: Dict[str, Dict[str, float]],
) -> Tuple[str, Optional[Dict[str, Any]], int, Dict[str, Any]]:
    key = payment_row.get("_id") or {}
    customer_id = str(key.get("customer_id") or "")
    product_index = _safe_int(key.get("product_index"), -1)
    payment_types = {(_normalized_status(item) or "__none__") for item in (payment_row.get("payment_types") or [])}
    amount = _round_money(payment_row.get("verified_payments")) if "product" in payment_types or "__none__" in payment_types else 0.0

    if not customer_doc:
        if "__none__" in payment_types:
            exclusion_stats["ambiguous_legacy"]["count"] += int(payment_row.get("payment_count") or 0)
            exclusion_stats["ambiguous_legacy"]["amount"] = _round_money(exclusion_stats["ambiguous_legacy"]["amount"] + amount)
        return "missing_customer", None, product_index, {}

    purchases = customer_doc.get("purchases") or []
    if product_index < 0 or product_index >= len(purchases):
        if "__none__" in payment_types:
            exclusion_stats["ambiguous_legacy"]["count"] += int(payment_row.get("payment_count") or 0)
            exclusion_stats["ambiguous_legacy"]["amount"] = _round_money(exclusion_stats["ambiguous_legacy"]["amount"] + amount)
        return "ambiguous_legacy", None, product_index, {}

    purchase = purchases[product_index] or {}
    status_details = _purchase_status_details(purchase)
    if status_details["is_completed"]:
        exclusion_stats["completed"]["count"] += int(payment_row.get("payment_count") or 0)
        exclusion_stats["completed"]["amount"] = _round_money(exclusion_stats["completed"]["amount"] + amount)
        return "excluded_completed", purchase, product_index, status_details
    if status_details["is_closed"]:
        exclusion_stats["closed"]["count"] += int(payment_row.get("payment_count") or 0)
        exclusion_stats["closed"]["amount"] = _round_money(exclusion_stats["closed"]["amount"] + amount)
        return "excluded_closed", purchase, product_index, status_details
    return "eligible", purchase, product_index, status_details


def _month_product_payment_total(filters: Dict[str, Any], eligible_keys: set[Tuple[str, int]]) -> float:
    if not eligible_keys:
        return 0.0
    customer_ids = [_safe_oid(cid) for cid, _ in eligible_keys]
    customer_ids = [cid for cid in customer_ids if cid]
    start_date, end_date = _month_range(filters["as_of_dt"])
    total = 0.0
    cursor = payments_col.find(
        {
            "customer_id": {"$in": customer_ids},
            "payment_type": "PRODUCT",
            "product_index": {"$ne": None},
            "date": {"$gte": start_date, "$lte": end_date},
        },
        {"customer_id": 1, "product_index": 1, "amount": 1},
    )
    for doc in cursor:
        key = (str(doc.get("customer_id")), _safe_int(doc.get("product_index")))
        if key in eligible_keys:
            total += _safe_float(doc.get("amount"))
    return _round_money(total)


def _build_liability_rows(filters: Dict[str, Any], include_ledger_for_page: bool = False) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, float]], List[Dict[str, Any]]]:
    grouped_rows = list(payments_col.aggregate(_payments_group_pipeline(filters), allowDiskUse=True))
    customer_ids = [row["_id"]["customer_id"] for row in grouped_rows if isinstance((row.get("_id") or {}).get("customer_id"), ObjectId)]
    customer_map = _load_customers(customer_ids)
    package_map = _load_packages(customer_ids)
    undelivered_map = _load_undelivered(customer_ids)
    user_map, _ = _load_user_maps(grouped_rows)
    product_ids = []
    for customer_doc in customer_map.values():
        for purchase in customer_doc.get("purchases") or []:
            product = (purchase or {}).get("product") or {}
            stable_id = product.get("_id") or product.get("product_id")
            if stable_id:
                product_ids.append(str(stable_id))
    inventory_map = _load_inventory_map(product_ids)

    ledgers: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    if include_ledger_for_page:
        ledgers = _load_page_ledgers(customer_ids, filters["as_of_date"])

    settings = get_liability_settings()
    rows: List[Dict[str, Any]] = []
    exclusion_stats = _empty_exclusion_stats()
    exclusion_stats["susu"] = _susu_stats(filters)
    reconciliation_exceptions: List[Dict[str, Any]] = []
    for payment_row in grouped_rows:
        key = payment_row.get("_id") or {}
        customer_id = str(key.get("customer_id") or "")
        customer_doc = customer_map.get(customer_id)
        classification, purchase, product_index, status_details = _classify_payment_group(payment_row, customer_doc, exclusion_stats)
        tuple_key = (customer_id, product_index)
        if classification in {"excluded_completed", "excluded_closed"}:
            continue
        if classification != "eligible":
            if classification in {"ambiguous_legacy", "missing_customer"}:
                reconciliation_exceptions.append(
                    {
                        "customer_id": customer_id,
                        "product_index": product_index,
                        "issue": classification,
                        "payment_types": payment_row.get("payment_types") or [],
                        "payment_count": int(payment_row.get("payment_count") or 0),
                        "verified_payment_amount": _round_money(payment_row.get("verified_payments")),
                    }
                )
            continue
        ledger = ledgers.get(tuple_key, [])
        agent_doc = user_map.get(str(payment_row.get("agent_id") or customer_doc.get("agent_id") or ""), {})
        manager_doc = user_map.get(str(payment_row.get("manager_id") or customer_doc.get("manager_id") or ""), {})
        row = _build_row(
            payment_row=payment_row,
            customer_doc=customer_doc,
            package_doc=package_map.get(tuple_key),
            undelivered_doc=undelivered_map.get(tuple_key),
            agent_doc=agent_doc,
            manager_doc=manager_doc,
            settings=settings,
            inventory_map=inventory_map,
            ledger=ledger,
        )
        if row:
            rows.append(row)
    rows = _apply_row_filters(rows, filters)
    rows.sort(key=lambda item: (item.get("risk_level") != "high", -item.get("current_liability", 0.0), item.get("customer_name") or ""))
    return rows, grouped_rows, exclusion_stats, reconciliation_exceptions


@cache.memoize(timeout=90)
def get_liability_summary_cached(serialized_filters: Tuple[Tuple[str, Any], ...]) -> Dict[str, Any]:
    filters = {key: value for key, value in serialized_filters}
    filters["as_of_dt"] = _parse_date(filters.get("as_of_date"))
    filters["start_dt"] = _parse_date(filters.get("start_date"))
    filters["end_dt"] = _parse_date(filters.get("end_date"))
    rows, _, exclusion_stats, reconciliation_exceptions = _build_liability_rows(filters, include_ledger_for_page=False)
    settings = get_liability_settings()

    total_liability = _round_money(sum(row["current_liability"] for row in rows))
    partially_paid_liability = _round_money(sum(row["current_liability"] for row in rows if row["liability_category"] == "partially-paid"))
    fully_paid_awaiting_delivery = _round_money(sum(row["current_liability"] for row in rows if row["liability_category"] == "fully-paid-awaiting-delivery"))
    overdue_delivery_liability = _round_money(sum(row["current_liability"] for row in rows if row["sla_status"] == "breached"))
    payments_collected_this_month = _month_product_payment_total(filters, {(row["customer_id"], row["purchase_index"]) for row in rows})
    estimated_cost_to_fulfil = _round_money(sum(row["estimated_cost_to_fulfil"] for row in rows))
    fulfilled_rows = [row for row in rows if row["delivery_status"] == "Delivered"]
    liability_cleared_this_month = _round_money(sum(min(row["verified_amount_paid"], row["agreed_order_value"]) for row in fulfilled_rows if str(row.get("delivered_at") or "")[:7] == datetime.utcnow().strftime("%Y-%m")))
    required_qty = sum(row["quantity"] for row in rows if row["delivery_status"] != "Delivered")
    covered_qty = sum(min(row["quantity"], row["stock_availability"]) for row in rows if row["delivery_status"] != "Delivered")
    stock_coverage_pct = round((covered_qty / required_qty) * 100.0, 1) if required_qty > 0 else 100.0

    trend_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"partially-paid": 0.0, "fully-paid-awaiting-delivery": 0.0})
    for row in rows:
        day = row.get("first_payment_date") or row.get("purchase_date") or filters["as_of_date"]
        bucket = trend_map[day]
        bucket[row["liability_category"]] = _round_money(bucket.get(row["liability_category"], 0.0) + row["current_liability"])

    trend = [
        {
            "date": day,
            "partially_paid": _round_money(values["partially-paid"]),
            "fully_paid": _round_money(values["fully-paid-awaiting-delivery"]),
        }
        for day, values in sorted(trend_map.items())[-16:]
    ]

    aging_deposits = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "91-180": 0.0, "180+": 0.0}
    aging_fully_paid = {"0-2": 0.0, "3-7": 0.0, "8-14": 0.0, "15-30": 0.0, "30+": 0.0}
    branch_exposure: Dict[str, Dict[str, float]] = defaultdict(lambda: {"liability": 0.0, "eligible_count": 0.0, "fully_paid_amount": 0.0})
    product_exposure: Dict[str, Dict[str, float]] = defaultdict(lambda: {"liability": 0.0, "qty": 0.0, "available": 0.0, "cost": 0.0, "margin_at_risk": 0.0, "fully_paid_count": 0.0})

    for row in rows:
        branch_bucket = branch_exposure[row["branch"] or "Unassigned"]
        branch_bucket["liability"] += row["current_liability"]
        branch_bucket["eligible_count"] += 1
        if row["liability_category"] == "fully-paid-awaiting-delivery":
            branch_bucket["fully_paid_amount"] += row["current_liability"]
        waiting_days = row.get("days_awaiting_delivery") or 0
        if waiting_days <= 30:
            aging_deposits["0-30"] += row["current_liability"]
        elif waiting_days <= 60:
            aging_deposits["31-60"] += row["current_liability"]
        elif waiting_days <= 90:
            aging_deposits["61-90"] += row["current_liability"]
        elif waiting_days <= 180:
            aging_deposits["91-180"] += row["current_liability"]
        else:
            aging_deposits["180+"] += row["current_liability"]

        if row["liability_category"] == "fully-paid-awaiting-delivery":
            if waiting_days <= 2:
                aging_fully_paid["0-2"] += row["current_liability"]
            elif waiting_days <= 7:
                aging_fully_paid["3-7"] += row["current_liability"]
            elif waiting_days <= 14:
                aging_fully_paid["8-14"] += row["current_liability"]
            elif waiting_days <= 30:
                aging_fully_paid["15-30"] += row["current_liability"]
            else:
                aging_fully_paid["30+"] += row["current_liability"]

        product_key = row["product_name"]
        product_bucket = product_exposure[product_key]
        product_bucket["liability"] += row["current_liability"]
        product_bucket["qty"] += row["quantity"]
        product_bucket["available"] = max(product_bucket["available"], row["stock_availability"])
        product_bucket["cost"] += row["estimated_cost_to_fulfil"]
        product_bucket["margin_at_risk"] += row["margin_at_risk"]
        if row["liability_category"] == "fully-paid-awaiting-delivery":
            product_bucket["fully_paid_count"] += 1

    branch_rows = [
        {
            "branch": key,
            "liability": _round_money(value["liability"]),
            "eligible_count": int(value["eligible_count"]),
            "fully_paid_amount": _round_money(value["fully_paid_amount"]),
        }
        for key, value in sorted(branch_exposure.items(), key=lambda item: item[1]["liability"], reverse=True)[:8]
    ]
    product_rows = []
    for key, value in sorted(product_exposure.items(), key=lambda item: item[1]["liability"], reverse=True)[:8]:
        shortage = max(value["qty"] - value["available"], 0.0)
        coverage = round((min(value["available"], value["qty"]) / value["qty"]) * 100.0, 1) if value["qty"] > 0 else 100.0
        product_rows.append(
            {
                "product": key,
                "liability": _round_money(value["liability"]),
                "fully_paid_customer_count": int(value["fully_paid_count"]),
                "quantity_required": _round_money(value["qty"]),
                "available_quantity": _round_money(value["available"]),
                "reserved_quantity": 0.0,
                "shortage": _round_money(shortage),
                "estimated_fulfilment_cost": _round_money(value["cost"]),
                "stock_coverage_pct": coverage,
                "margin_at_risk": _round_money(value["margin_at_risk"]),
            }
        )

    urgent_rows = [
        {
            "customer_name": row["customer_name"],
            "product_name": row["product_name"],
            "branch": row["branch"],
            "liability": _round_money(row["current_liability"]),
            "risk_level": row["risk_level"],
            "sla_status": row["sla_status"],
            "days_awaiting_delivery": row["days_awaiting_delivery"],
            "purchase_ref": row["purchase_ref"],
            "stock_shortage": row.get("has_stock_shortage", False),
            "long_inactive": row.get("is_long_inactive", False),
        }
        for row in rows
        if row.get("risk_level") == "high" or row.get("has_stock_shortage") or row.get("is_long_inactive")
    ][:12]

    payment_stage_counts = {
        "partial": sum(1 for row in rows if row["payment_stage"] == "partially-paid"),
        "fully_paid": sum(1 for row in rows if row["payment_stage"] == "fully-paid"),
        "delivered": sum(1 for row in rows if row["delivery_status"] == "Delivered"),
    }

    return {
        "filters": {
            "as_of_date": filters["as_of_date"],
            "start_date": filters.get("start_date") or "",
            "end_date": filters.get("end_date") or "",
        },
        "cards": [
            {
                "id": "total-customer-liability",
                "label": "Ongoing Undelivered Payments",
                "amount": total_liability,
                "amount_label": _money_label(total_liability),
                "count": len(rows),
                "risk_color": "danger" if overdue_delivery_liability > 0 else "primary",
                "tooltip": "Product payments received for active customer products that have not yet been delivered.",
            },
            {
                "id": "partially-paid-liability",
                "label": "Partially Paid Liability",
                "amount": partially_paid_liability,
                "amount_label": _money_label(partially_paid_liability),
                "count": sum(1 for row in rows if row["liability_category"] == "partially-paid"),
                "risk_color": "info",
                "tooltip": "Verified customer cash held on undelivered products that are not yet fully paid.",
            },
            {
                "id": "fully-paid-awaiting-delivery",
                "label": "Fully Paid Awaiting Delivery",
                "amount": fully_paid_awaiting_delivery,
                "amount_label": _money_label(fully_paid_awaiting_delivery),
                "count": sum(1 for row in rows if row["liability_category"] == "fully-paid-awaiting-delivery"),
                "risk_color": "warning",
                "tooltip": "Fully paid orders whose delivery is not yet verified.",
            },
            {
                "id": "overdue-delivery-liability",
                "label": "Overdue Delivery Liability",
                "amount": overdue_delivery_liability,
                "amount_label": _money_label(overdue_delivery_liability),
                "count": sum(1 for row in rows if row["sla_status"] == "breached"),
                "risk_color": "danger",
                "tooltip": f"Fully paid orders waiting beyond the configured delivery SLA of {settings['delivery_sla_days']} days.",
            },
            {
                "id": "payments-collected-this-month",
                "label": "Payments Collected This Month",
                "amount": payments_collected_this_month,
                "amount_label": _money_label(payments_collected_this_month),
                "count": len(rows),
                "risk_color": "success",
                "tooltip": "Eligible PRODUCT payments collected this month for active undelivered products only.",
            },
            {
                "id": "liability-cleared-this-month",
                "label": "Liability Cleared This Month",
                "amount": liability_cleared_this_month,
                "amount_label": _money_label(liability_cleared_this_month),
                "count": len(fulfilled_rows),
                "risk_color": "success",
                "tooltip": "Delivered orders in the current month valued at verified paid amount up to the order total.",
            },
            {
                "id": "estimated-cost-to-fulfil",
                "label": "Estimated Cost to Fulfil",
                "amount": estimated_cost_to_fulfil,
                "amount_label": _money_label(estimated_cost_to_fulfil),
                "count": sum(1 for row in rows if row["delivery_status"] != "Delivered"),
                "risk_color": "secondary",
                "tooltip": "Estimated cost price required to fulfil currently open liabilities where inventory matched a stable product id.",
            },
            {
                "id": "stock-coverage-percentage",
                "label": "Stock Coverage Percentage",
                "amount": stock_coverage_pct,
                "amount_label": f"{stock_coverage_pct:.1f}%",
                "count": sum(1 for row in rows if row["delivery_status"] != "Delivered"),
                "risk_color": "success" if stock_coverage_pct >= 90 else "warning" if stock_coverage_pct >= 60 else "danger",
                "tooltip": "Available stock divided by quantity required for undelivered liable purchases.",
            },
        ],
        "trend": trend,
        "aging": {
            "all_undelivered_deposits": [{"bucket": key, "amount": _round_money(value)} for key, value in aging_deposits.items()],
            "fully_paid_awaiting_delivery": [{"bucket": key, "amount": _round_money(value)} for key, value in aging_fully_paid.items()],
        },
        "branch_exposure": branch_rows,
        "product_exposure": product_rows,
        "urgent_risk": urgent_rows,
        "funnel": payment_stage_counts,
        "exclusions": exclusion_stats,
        "status_fields": {
            "primary": "customers.purchases[].product.status",
            "secondary": "customers.purchases[].status",
                "delivery_auxiliary": "customers.purchases[].product.packaging_status",
        },
    }


def get_liability_summary(filters: Dict[str, Any]) -> Dict[str, Any]:
    serialized = _serialize_filters(filters)
    try:
        return get_liability_summary_cached(serialized)
    except Exception:
        return get_liability_summary_cached.uncached(*[serialized])


def get_liability_register(filters: Dict[str, Any]) -> Dict[str, Any]:
    rows, _, exclusion_stats, reconciliation_exceptions = _build_liability_rows(filters, include_ledger_for_page=False)
    total = len(rows)
    page_rows = rows[filters["skip"]: filters["skip"] + filters["page_size"]]
    return {
        "rows": page_rows,
        "exclusions": exclusion_stats,
        "reconciliation_exceptions": reconciliation_exceptions[:50],
        "pagination": {
            "page": filters["page"],
            "page_size": filters["page_size"],
            "total": total,
            "pages": max(math.ceil(total / filters["page_size"]), 1),
        },
    }


def get_liability_detail(customer_id: str, product_index: int, as_of_date: str) -> Dict[str, Any]:
    filters = normalize_filters({"as_of_date": as_of_date, "page": 1, "page_size": 200})
    rows, grouped_rows, _, _ = _build_liability_rows(filters, include_ledger_for_page=False)
    for row in rows:
        if row["customer_id"] == customer_id and row["purchase_index"] == product_index:
            cust_oid = _safe_oid(customer_id)
            customer_doc = customers_col.find_one({"_id": cust_oid}, {"name": 1, "phone_number": 1, "location": 1, "occupation": 1, "comment": 1, "purchases": 1})
            ledger_docs = _load_page_ledgers([cust_oid], as_of_date).get((customer_id, product_index), [])
            package_doc = _load_packages([cust_oid]).get((customer_id, product_index), {})
            undelivered_doc = _load_undelivered([cust_oid]).get((customer_id, product_index), {})
            audits = list(
                activity_logs_col.find(
                    {
                        "$or": [
                            {"entity_id": customer_id},
                            {"meta.reference": row["purchase_ref"]},
                            {"meta.customer": row["customer_name"]},
                        ]
                    },
                    {"action_label": 1, "action": 1, "timestamp": 1, "role": 1, "username": 1, "meta": 1},
                ).sort([("timestamp", DESCENDING)]).limit(40)
            )
            return {
                "summary": row,
                "customer": {
                    "name": customer_doc.get("name") if customer_doc else row["customer_name"],
                    "phone_number": customer_doc.get("phone_number") if customer_doc else row["customer_phone"],
                    "location": (customer_doc or {}).get("location") or "",
                    "occupation": (customer_doc or {}).get("occupation") or "",
                    "comment": (customer_doc or {}).get("comment") or "",
                },
                "purchase": ((customer_doc or {}).get("purchases") or [])[product_index] if customer_doc else {},
                "payment_ledger": [
                    {
                        "date": str(doc.get("date") or "")[:10],
                        "time": doc.get("time") or "",
                        "method": doc.get("method") or "",
                        "note": doc.get("note") or "",
                        "payment_type": doc.get("payment_type") or "PRODUCT",
                        "amount": _round_money(doc.get("amount")),
                    }
                    for doc in ledger_docs
                ],
                "delivery_history": {
                    "package": package_doc,
                    "undelivered": undelivered_doc,
                },
                "audit_trail": [
                    {
                        "action": doc.get("action") or "",
                        "label": doc.get("action_label") or "",
                        "actor": doc.get("username") or "",
                        "role": doc.get("role") or "",
                        "timestamp": _datetime_to_json(_parse_date(doc.get("timestamp"))),
                        "meta": doc.get("meta") or {},
                    }
                    for doc in audits
                ],
            }
    return {"error": "Record not found"}


def resolve_liability(customer_id: str, product_index: int, resolution: str, actor: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
    customer_oid = _safe_oid(customer_id)
    resolution = str(resolution or "").strip().lower()
    if not customer_oid or resolution not in {"closed", "delivered"}:
        return {"error": "A valid customer, product and resolution are required."}
    customer = customers_col.find_one({"_id": customer_oid}, {"purchases": 1, "name": 1})
    purchases = (customer or {}).get("purchases") or []
    if not customer or product_index < 0 or product_index >= len(purchases) or not isinstance(purchases[product_index], dict):
        return {"error": "Customer product was not found."}

    purchase = purchases[product_index]
    product = purchase.get("product") if isinstance(purchase.get("product"), dict) else {}
    previous = {
        "purchase_status": purchase.get("status"),
        "product_status": product.get("status"),
        "packaging_status": product.get("packaging_status"),
    }
    now = datetime.utcnow()
    actor_snapshot = {
        "user_id": str(actor.get("user_id") or ""),
        "name": actor.get("name") or "",
        "role": actor.get("role") or "",
    }
    set_fields: Dict[str, Any] = {
        f"purchases.{product_index}.status": resolution,
        f"purchases.{product_index}.product.status": resolution,
        f"purchases.{product_index}.liability_resolution": resolution,
        f"purchases.{product_index}.liability_resolved_at": now,
        f"purchases.{product_index}.liability_resolved_by": actor_snapshot,
        f"purchases.{product_index}.liability_resolution_reason": reason,
        "updated_at": now,
    }
    if resolution == "delivered":
        set_fields.update({
            f"purchases.{product_index}.delivered_at": now,
            f"purchases.{product_index}.product.delivered_at": now,
            f"purchases.{product_index}.product.packaging_status": "delivered",
        })
    result = customers_col.update_one(
        {"_id": customer_oid, f"purchases.{product_index}": {"$exists": True}},
        {"$set": set_fields},
    )
    if result.modified_count != 1:
        return {"error": "The customer product could not be updated."}

    if resolution == "delivered":
        packages_col.update_many(
            {
                "customer_id": {"$in": [customer_oid, str(customer_oid)]},
                "product_index": product_index,
                "status": {"$ne": "cancelled"},
            },
            {"$set": {"status": "delivered", "delivered_at": now, "updated_at": now}},
        )
        undelivered_items_col.update_many(
            {
                "customer_id": {"$in": [customer_oid, str(customer_oid)]},
                "product_index": product_index,
            },
            {"$set": {"status": "delivered", "resolved_at": now, "updated_at": now}},
        )

    cache.delete_memoized(get_liability_summary_cached)
    return {
        "customer_id": str(customer_oid),
        "customer_name": customer.get("name") or "Customer",
        "product_index": product_index,
        "product_name": product.get("name") or "Product",
        "resolution": resolution,
        "resolved_at": now.isoformat(),
        "previous": previous,
    }


def build_liability_export_rows(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows, _, _, _ = _build_liability_rows(filters, include_ledger_for_page=False)
    return rows


def build_liability_csv(filters: Dict[str, Any]) -> str:
    rows = build_liability_export_rows(filters)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Customer Liability & Fulfilment Control"])
    writer.writerow(["As-of date", filters["as_of_date"]])
    writer.writerow(["Generated at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow([])
    writer.writerow(
        [
            "Customer",
            "Phone",
            "Purchase Ref",
            "Branch",
            "Agent",
            "Product",
            "Order Value",
            "Verified Paid",
            "Remaining Balance",
            "Current Liability",
            "Payment Stage",
            "Delivery Status",
            "Risk",
            "SLA",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["customer_name"],
                row["customer_phone"],
                row["purchase_ref"],
                row["branch"],
                row["agent_name"],
                row["product_name"],
                row["agreed_order_value"],
                row["verified_amount_paid"],
                row["remaining_balance"],
                row["current_liability"],
                row["payment_stage"],
                row["delivery_status"],
                row["risk_level"],
                row["sla_status"],
            ]
        )
    return output.getvalue()


def build_liability_excel(filters: Dict[str, Any]) -> bytes:
    rows = build_liability_export_rows(filters)
    wb = Workbook()
    ws = wb.active
    ws.title = "Customer Liabilities"
    ws.append(["Customer Liability & Fulfilment Control"])
    ws.append(["As-of date", filters["as_of_date"]])
    ws.append(["Generated at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")])
    ws.append([])
    headers = [
        "Customer",
        "Phone",
        "Purchase Ref",
        "Branch",
        "Agent",
        "Product",
        "Order Value",
        "Verified Paid",
        "Remaining Balance",
        "Current Liability",
        "Payment Stage",
        "Delivery Status",
        "Risk",
        "SLA",
    ]
    ws.append(headers)
    for row in rows:
        ws.append(
            [
                row["customer_name"],
                row["customer_phone"],
                row["purchase_ref"],
                row["branch"],
                row["agent_name"],
                row["product_name"],
                row["agreed_order_value"],
                row["verified_amount_paid"],
                row["remaining_balance"],
                row["current_liability"],
                row["payment_stage"],
                row["delivery_status"],
                row["risk_level"],
                row["sla_status"],
            ]
        )
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_liability_pdf(filters: Dict[str, Any]) -> bytes:
    rows = build_liability_export_rows(filters)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Customer Liability & Fulfilment Control", styles["Title"]),
        Spacer(1, 6),
        Paragraph(f"As-of date: {filters['as_of_date']}", styles["Normal"]),
        Paragraph(f"Generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", styles["Normal"]),
        Spacer(1, 10),
    ]
    data = [["Customer", "Phone", "Purchase Ref", "Branch", "Product", "Paid", "Liability", "Delivery", "Risk"]]
    for row in rows[:250]:
        data.append(
            [
                row["customer_name"],
                row["customer_phone"],
                row["purchase_ref"],
                row["branch"],
                row["product_name"],
                f"{row['verified_amount_paid']:.2f}",
                f"{row['current_liability']:.2f}",
                row["delivery_status"],
                row["risk_level"],
            ]
        )
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buffer.getvalue()
