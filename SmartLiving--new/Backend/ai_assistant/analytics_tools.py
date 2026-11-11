from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from bson import ObjectId

from db import db

users_col = db["users"]
customers_col = db["customers"]
payments_col = db["payments"]
products_col = db["products"]
packages_col = db["packages"]


def _collection_exists(name: str) -> bool:
    try:
        return name in db.list_collection_names()
    except Exception:
        return False


def _get_collection(name: str):
    return db[name] if _collection_exists(name) else None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _normalize_key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, ObjectId):
        return str(value)
    return str(value).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    candidates = [
        text,
        text.replace("Z", "+00:00"),
        text.replace("/", "-"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _doc_datetime(doc: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = _parse_datetime(doc.get(key))
        if parsed is not None:
            return parsed
    return None


def _period_bounds(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

    return {
        "today": (today_start, today_start + timedelta(days=1)),
        "yesterday": (yesterday_start, today_start),
        "week": (week_start, week_start + timedelta(days=7)),
        "month": (month_start, next_month),
    }


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _recent_payments_cursor(start_dt: datetime):
    start_naive = _utc_naive(start_dt)
    start_iso = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return payments_col.find(
        {
            "$or": [
                {"created_at": {"$gte": start_naive}},
                {"date": {"$gte": start_iso}},
            ]
        },
        {
            "amount": 1,
            "payment_type": 1,
            "agent_id": 1,
            "manager_id": 1,
            "product_name": 1,
            "date": 1,
            "created_at": 1,
            "time": 1,
            "customer_id": 1,
        },
    )


def _period_payment_match(start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    return {
        "$or": [
            {"created_at": {"$gte": _utc_naive(start_dt), "$lt": _utc_naive(end_dt)}},
            {
                "date": {
                    "$gte": start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                    "$lt": end_dt.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                }
            },
        ]
    }


def _aggregate_payment_totals(start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    pipeline = [
        {"$match": _period_payment_match(start_dt, end_dt)},
        {
            "$group": {
                "_id": {"payment_type": "$payment_type"},
                "total": {"$sum": {"$convert": {"input": "$amount", "to": "double", "onError": 0, "onNull": 0}}},
                "transactions": {"$sum": 1},
            }
        },
    ]
    rows = list(payments_col.aggregate(pipeline))
    total = 0.0
    transactions = 0
    by_type: dict[str, float] = {}
    for row in rows:
        payment_type = (_normalize_key((row.get("_id") or {}).get("payment_type")) or "UNKNOWN").upper()
        amount = round(_safe_float(row.get("total")), 2)
        count = _safe_int(row.get("transactions"))
        total += amount
        transactions += count
        by_type[payment_type] = by_type.get(payment_type, 0.0) + amount
    return {"total": round(total, 2), "transactions": transactions, "byType": by_type}


def _aggregate_top_people(field_name: str, start_dt: datetime, end_dt: datetime, *, exclude_withdrawals: bool = False) -> list[dict[str, Any]]:
    match_stage = _period_payment_match(start_dt, end_dt)
    if exclude_withdrawals:
        match_stage["payment_type"] = {"$ne": "WITHDRAWAL"}
    pipeline = [
        {"$match": match_stage},
        {
            "$group": {
                "_id": f"${field_name}",
                "amount": {"$sum": {"$convert": {"input": "$amount", "to": "double", "onError": 0, "onNull": 0}}},
            }
        },
        {"$match": {"_id": {"$ne": None}}},
        {"$sort": {"amount": -1}},
        {"$limit": 5},
    ]
    rows = list(payments_col.aggregate(pipeline))
    ids = {_normalize_key(row.get("_id")) for row in rows if _normalize_key(row.get("_id"))}
    name_map = _lookup_user_name_map(ids)
    return [
        {
            "id": _normalize_key(row.get("_id")),
            "name": name_map.get(_normalize_key(row.get("_id"))) or "Unknown",
            "amount": round(_safe_float(row.get("amount")), 2),
        }
        for row in rows
        if _normalize_key(row.get("_id"))
    ]


def _in_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return bool(dt and start <= dt < end)


def _lookup_user_name_map(ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    query_values: list[Any] = []
    for value in ids:
        query_values.append(value)
        if ObjectId.is_valid(value):
            query_values.append(ObjectId(value))
    docs = users_col.find({"_id": {"$in": query_values}}, {"name": 1, "username": 1})
    mapping: dict[str, str] = {}
    for doc in docs:
        name = (doc.get("name") or doc.get("username") or "").strip()
        if name:
            mapping[str(doc.get("_id"))] = name
    return mapping


def _top_rows(score_map: dict[str, float], label_map: dict[str, str], *, limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    for key, amount in score_map.items():
        rows.append(
            {
                "id": key,
                "name": label_map.get(key) or "Unknown",
                "amount": round(float(amount or 0.0), 2),
            }
        )
    rows.sort(key=lambda item: (-item["amount"], item["name"].lower()))
    return rows[:limit]


def get_sales_summary(date_range: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    bounds = _period_bounds(now)
    response_periods: dict[str, Any] = {}
    for period_name, (start, end) in bounds.items():
        bucket = _aggregate_payment_totals(start, end)
        response_periods[period_name] = {
            "total": round(bucket["total"], 2),
            "transactions": int(bucket["transactions"]),
            "byType": {key: round(value, 2) for key, value in sorted((bucket.get("byType") or {}).items())},
            "topAgents": _aggregate_top_people("agent_id", start, end),
            "topManagers": _aggregate_top_people("manager_id", start, end),
        }

    return {
        "currency": "GHS",
        "generatedAt": now.isoformat(),
        "dateRange": date_range or "default",
        "periods": response_periods,
    }


def get_customer_summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    bounds = _period_bounds(now)
    archived_col = _get_collection("Archived_customers")
    stopped_col = _get_collection("stopped_customers")

    customers = list(customers_col.find({}, {"date_registered": 1, "lead_registered_at": 1, "lead_stage": 1, "purchases": 1}))
    payments = list(payments_col.find({}, {"customer_id": 1, "amount": 1, "payment_type": 1}))

    payment_totals: dict[str, float] = defaultdict(float)
    for payment in payments:
        customer_id = _normalize_key(payment.get("customer_id"))
        if not customer_id:
            continue
        payment_type = (_normalize_key(payment.get("payment_type")) or "").upper()
        amount = _safe_float(payment.get("amount"))
        if payment_type == "WITHDRAWAL":
            payment_totals[customer_id] -= amount
        else:
            payment_totals[customer_id] += amount

    outstanding_count = 0
    new_today = 0
    new_week = 0
    lead_count = 0

    for customer in customers:
        customer_id = _normalize_key(customer.get("_id"))
        registered_dt = _doc_datetime(customer, "date_registered", "lead_registered_at")
        if _in_range(registered_dt, *bounds["today"]):
            new_today += 1
        if _in_range(registered_dt, *bounds["week"]):
            new_week += 1
        if (_normalize_key(customer.get("lead_stage")) or "").lower() == "lead":
            lead_count += 1

        purchase_total = 0.0
        for purchase in customer.get("purchases") or []:
            product = purchase.get("product") or {}
            quantity = max(1, _safe_int(product.get("quantity"), 1))
            total = _safe_float(product.get("total"))
            if total <= 0:
                total = _safe_float(product.get("price")) * quantity
            purchase_total += total
        if purchase_total - payment_totals.get(customer_id, 0.0) > 0.01:
            outstanding_count += 1

    return {
        "generatedAt": now.isoformat(),
        "totalCustomers": len(customers),
        "newCustomersToday": new_today,
        "newCustomersThisWeek": new_week,
        "activeCustomers": len(customers),
        "leadCustomers": lead_count,
        "archivedCustomers": archived_col.count_documents({}) if archived_col is not None else 0,
        "stoppedCustomers": stopped_col.count_documents({}) if stopped_col is not None else 0,
        "customersWithOutstandingBalances": outstanding_count,
    }


def get_product_summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    products = list(products_col.find({}, {"name": 1, "components": 1, "package_name": 1}))
    payments = list(payments_col.find({}, {"product_name": 1, "amount": 1, "payment_type": 1}))

    sales_counts: dict[str, int] = defaultdict(int)
    payment_activity: dict[str, float] = defaultdict(float)

    for payment in payments:
        product_name = _normalize_key(payment.get("product_name"))
        if not product_name:
            continue
        payment_type = (_normalize_key(payment.get("payment_type")) or "").upper()
        amount = _safe_float(payment.get("amount"))
        if payment_type != "WITHDRAWAL":
            payment_activity[product_name] += amount
            sales_counts[product_name] += 1

    top_selling = [
        {"name": name, "transactions": count}
        for name, count in sorted(sales_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:5]
    ]
    highest_payment_activity = [
        {"name": name, "amount": round(amount, 2)}
        for name, amount in sorted(payment_activity.items(), key=lambda item: (-item[1], item[0].lower()))[:5]
    ]

    connected_components = sum(1 for product in products if product.get("components"))
    packaged_products = sum(1 for product in products if str(product.get("package_name") or "").strip())

    return {
        "generatedAt": now.isoformat(),
        "totalProducts": len(products),
        "topSellingProducts": top_selling,
        "highestPaymentActivityProducts": highest_payment_activity,
        "productsWithComponents": connected_components,
        "productsWithPackages": packaged_products,
    }


def get_agent_manager_summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    bounds = _period_bounds(now)
    users = list(users_col.find({"role": {"$in": ["agent", "manager"]}}, {"name": 1, "username": 1, "role": 1}))
    agent_ids = {str(doc["_id"]) for doc in users if doc.get("role") == "agent"}
    manager_ids = {str(doc["_id"]) for doc in users if doc.get("role") == "manager"}
    name_map = {
        str(doc["_id"]): (doc.get("name") or doc.get("username") or "Unknown").strip() or "Unknown"
        for doc in users
    }
    recent_payments = list(_recent_payments_cursor(bounds["month"][0]))
    today_scores: dict[str, float] = defaultdict(float)
    week_scores: dict[str, float] = defaultdict(float)
    month_scores: dict[str, float] = defaultdict(float)
    for payment in recent_payments:
        amount = _safe_float(payment.get("amount"))
        if amount <= 0 or (_normalize_key(payment.get("payment_type")) or "").upper() == "WITHDRAWAL":
            continue
        dt = _doc_datetime(payment, "created_at", "date")
        agent_id = _normalize_key(payment.get("agent_id"))
        if not agent_id or agent_id not in agent_ids:
            continue
        if _in_range(dt, *bounds["today"]):
            today_scores[agent_id] += amount
        if _in_range(dt, *bounds["week"]):
            week_scores[agent_id] += amount
        if _in_range(dt, *bounds["month"]):
            month_scores[agent_id] += amount

    comparison = [
        {
            "agentId": agent_id,
            "agentName": name_map.get(agent_id) or "Unknown",
            "todayAmount": round(today_scores.get(agent_id, 0.0), 2),
            "weekAmount": round(amount, 2),
            "monthAmount": round(month_scores.get(agent_id, 0.0), 2),
        }
        for agent_id, amount in sorted(week_scores.items(), key=lambda item: (-item[1], name_map.get(item[0], "").lower()))[:8]
    ]

    return {
        "generatedAt": now.isoformat(),
        "totalAgents": len(agent_ids),
        "totalManagers": len(manager_ids),
        "topCollectingAgentsToday": _top_rows(today_scores, name_map),
        "topCollectingAgentsThisWeek": _top_rows(week_scores, name_map),
        "topCollectingAgentsThisMonth": _top_rows(month_scores, name_map),
        "agentPerformanceComparison": comparison,
    }


def get_inventory_summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    inventory_products_col = _get_collection("inventory_products")
    legacy_inventory_col = _get_collection("inventory")
    locations_col = _get_collection("inventory_branch_locations")
    stock_sessions_col = _get_collection("inventory_stock_update_sessions")
    outflow_col = _get_collection("inventory_products_outflow")
    suppliers_col = _get_collection("inventory_suppliers")
    purchase_orders_col = _get_collection("inventory_purchase_orders")
    procurement_requests_col = _get_collection("inventory_procurement_requests")

    products = list(
        (inventory_products_col or legacy_inventory_col).find(
            {},
            {
                "name": 1,
                "sku": 1,
                "reorder_point": 1,
                "status": 1,
                "quantity": 1,
                "qty": 1,
                "stock_entries": 1,
            },
        )
    ) if (inventory_products_col or legacy_inventory_col) is not None else []

    total_products = len(products)
    low_stock: list[dict[str, Any]] = []
    branch_totals: dict[str, int] = defaultdict(int)
    total_units = 0

    for product in products:
        entries = product.get("stock_entries") or []
        if entries:
            quantity = sum(_safe_int(entry.get("quantity")) for entry in entries)
            for entry in entries:
                branch_name = _normalize_key(entry.get("branch")) or "Unassigned"
                branch_totals[branch_name] += _safe_int(entry.get("quantity"))
        else:
            quantity = max(_safe_int(product.get("quantity")), _safe_int(product.get("qty")))
        total_units += quantity
        reorder_point = max(5, _safe_int(product.get("reorder_point"), 10))
        if quantity <= reorder_point:
            low_stock.append(
                {
                    "name": _normalize_key(product.get("name")) or "Unknown",
                    "quantity": quantity,
                    "reorderPoint": reorder_point,
                }
            )

    low_stock.sort(key=lambda item: (item["quantity"], item["name"].lower()))

    stock_movements = {"updateSessionsLast7Days": 0, "outflowRecordsLast7Days": 0}
    seven_days_ago = now - timedelta(days=7)
    if stock_sessions_col is not None:
        for session in stock_sessions_col.find({}, {"created_at": 1, "updated_at": 1}):
            dt = _doc_datetime(session, "updated_at", "created_at")
            if dt and dt >= seven_days_ago:
                stock_movements["updateSessionsLast7Days"] += 1
    if outflow_col is not None:
        for movement in outflow_col.find({}, {"created_at": 1, "date": 1}):
            dt = _doc_datetime(movement, "created_at", "date")
            if dt and dt >= seven_days_ago:
                stock_movements["outflowRecordsLast7Days"] += 1

    warehouse_summary = []
    if locations_col is not None:
        for row in locations_col.find({}, {"name": 1, "branch": 1, "stock_units": 1, "capacity": 1, "status": 1}):
            warehouse_summary.append(
                {
                    "name": _normalize_key(row.get("name")) or "Location",
                    "branch": _normalize_key(row.get("branch")) or "Unassigned",
                    "stockUnits": _safe_int(row.get("stock_units")),
                    "capacity": _safe_int(row.get("capacity")),
                    "status": _normalize_key(row.get("status")) or "unknown",
                }
            )
        warehouse_summary.sort(key=lambda item: (item["branch"].lower(), item["name"].lower()))

    return {
        "generatedAt": now.isoformat(),
        "totalInventoryProducts": total_products,
        "totalInventoryUnits": total_units,
        "lowStockProducts": low_stock[:10],
        "stockMovements": stock_movements,
        "branchStockSummary": [
            {"branch": branch, "quantity": qty}
            for branch, qty in sorted(branch_totals.items(), key=lambda item: (-item[1], item[0].lower()))
        ][:10],
        "warehouseStockSummary": warehouse_summary[:10],
        "supplierCount": suppliers_col.count_documents({}) if suppliers_col is not None else 0,
        "purchaseOrderCount": purchase_orders_col.count_documents({}) if purchase_orders_col is not None else 0,
        "procurementRequestCount": procurement_requests_col.count_documents({}) if procurement_requests_col is not None else 0,
    }


def get_fulfillment_summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if not _collection_exists("packages"):
        return {
            "generatedAt": now.isoformat(),
            "pendingPackages": 0,
            "deliveredPackages": 0,
            "inTransitPackages": 0,
            "packagingPackages": 0,
            "readyForFulfillment": 0,
        }

    status_counts: dict[str, int] = defaultdict(int)
    ready_for_fulfillment = 0
    for package in packages_col.find({}, {"status": 1}):
        status = (_normalize_key(package.get("status")) or "unknown").lower()
        status_counts[status] += 1
        if status in {"pending", "ready", "queued"}:
            ready_for_fulfillment += 1

    return {
        "generatedAt": now.isoformat(),
        "pendingPackages": status_counts.get("pending", 0),
        "deliveredPackages": status_counts.get("delivered", 0),
        "inTransitPackages": status_counts.get("delivering", 0) + status_counts.get("in-transit", 0),
        "packagingPackages": status_counts.get("packaging", 0),
        "readyForFulfillment": ready_for_fulfillment,
        "statusBreakdown": dict(sorted(status_counts.items())),
    }


def forecast_sales() -> dict[str, Any]:
    sales = get_sales_summary(date_range="forecast")
    periods = sales.get("periods") or {}
    today = _safe_float(((periods.get("today") or {}).get("total")))
    yesterday = _safe_float(((periods.get("yesterday") or {}).get("total")))
    week_total = _safe_float(((periods.get("week") or {}).get("total")))
    week_average = round(week_total / 7.0, 2) if week_total else 0.0

    weekday_samples: list[float] = []
    now = datetime.now(timezone.utc)
    target_weekday = now.weekday()
    recent_start = now - timedelta(days=56)
    for payment in _recent_payments_cursor(recent_start):
        if (_normalize_key(payment.get("payment_type")) or "").upper() == "WITHDRAWAL":
            continue
        dt = _doc_datetime(payment, "created_at", "date")
        if not dt or dt.weekday() != target_weekday:
            continue
        weekday_samples.append(_safe_float(payment.get("amount")))

    same_weekday_average = round(sum(weekday_samples) / len(weekday_samples), 2) if weekday_samples else 0.0
    baseline_values = [value for value in [yesterday, week_average, same_weekday_average] if value > 0]
    projected_tomorrow = round(sum(baseline_values) / len(baseline_values), 2) if baseline_values else round(today, 2)

    return {
        "generatedAt": now.isoformat(),
        "estimateOnly": True,
        "todaySoFar": round(today, 2),
        "yesterday": round(yesterday, 2),
        "last7DaysAverage": week_average,
        "sameWeekdayAverage": same_weekday_average,
        "projectedTomorrow": projected_tomorrow,
        "confidence": "low-to-medium",
        "note": "Forecasts are estimates based on recent payment patterns and should not be treated as guaranteed results.",
    }


def select_analytics_for_question(message: str) -> tuple[list[str], dict[str, Any]]:
    text = (message or "").lower()
    selected: list[tuple[str, Any]] = []

    def compute(name: str, fn):
        started = time.perf_counter()
        print(f"[EXEC_AI] analytics_start={name}")
        data = fn()
        print(f"[EXEC_AI] analytics_done={name} duration_ms={round((time.perf_counter() - started) * 1000, 2)}")
        return data

    def add(name: str, fn) -> None:
        if name not in [item[0] for item in selected]:
            selected.append((name, compute(name, fn)))

    if any(keyword in text for keyword in ["sale", "payment", "collect", "revenue", "today", "yesterday", "week", "month"]):
        add("sales_summary", get_sales_summary)
    if any(keyword in text for keyword in ["customer", "balance", "debt", "lead", "archive", "stopped"]):
        add("customer_summary", get_customer_summary)
    if any(keyword in text for keyword in ["product", "package", "component", "selling"]):
        add("product_summary", get_product_summary)
    if any(keyword in text for keyword in ["agent", "manager", "team", "performance", "collector"]):
        add("agent_manager_summary", get_agent_manager_summary)
    if any(keyword in text for keyword in ["inventory", "stock", "warehouse", "supplier", "procurement", "low stock"]):
        add("inventory_summary", get_inventory_summary)
    if any(keyword in text for keyword in ["fulfillment", "delivery", "package", "transit", "packaging"]):
        add("fulfillment_summary", get_fulfillment_summary)
    if any(keyword in text for keyword in ["forecast", "expect", "tomorrow", "projection", "project"]):
        add("forecast_sales", forecast_sales)
        if "sales_summary" not in [item[0] for item in selected]:
            add("sales_summary", get_sales_summary)

    if not selected:
        add("sales_summary", get_sales_summary)
        add("customer_summary", get_customer_summary)

    return [name for name, _ in selected], {name: data for name, data in selected}
