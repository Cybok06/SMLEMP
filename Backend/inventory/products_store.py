from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from bson import ObjectId

from db import db
from .settings_store import get_branches_payload, inventory_locations_col

inventory_products_col = db["inventory_products"]
inventory_stock_update_sessions_col = db["inventory_stock_update_sessions"]
inventory_stock_taking_sessions_col = db["inventory_stock_taking_sessions"]
inventory_logs_col = db["inventory_logs"]
products_col = db["products"]
users_col = db["users"]
customers_col = db["customers"]
payments_col = db["payments"]


def _safe_object_id(value: str | None) -> ObjectId | None:
    if value and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value / 1000.0 if value > 10**12 else value)
        except Exception:
            return datetime.min
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return datetime.min
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.min
    return datetime.min


def _slug_piece(value: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    return (cleaned[:4] or fallback).ljust(3, "X")


def _build_sku(category: str, name: str, product_id: ObjectId) -> str:
    return f"{_slug_piece(category, 'PRD')}-{_slug_piece(name, 'ITEM')}-{str(product_id)[-4:].upper()}"


def _status_for_quantity(quantity: int, reorder_point: int) -> str:
    if quantity <= max(5, reorder_point // 2):
        return "critical"
    if quantity <= max(20, reorder_point):
        return "warning"
    return "good"


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "branch": (entry.get("branch") or "").strip(),
        "location_id": str(entry.get("location_id") or "").strip(),
        "location_name": (entry.get("location_name") or "").strip(),
        "location_code": (entry.get("location_code") or "").strip(),
        "quantity": int(entry.get("quantity") or 0),
        "expiry_date": (entry.get("expiry_date") or "").strip(),
        "reminder_days": int(entry.get("reminder_days") or 0),
        "cost_price": float(entry.get("cost_price") or 0),
        "selling_price": float(entry.get("selling_price") or 0),
        "installment_price": float(entry["installment_price"]) if entry.get("installment_price") is not None else None,
        "wholesale_price": float(entry["wholesale_price"]) if entry.get("wholesale_price") is not None else None,
        "source": (entry.get("source") or "").strip(),
        "order_id": str(entry.get("order_id") or "").strip(),
        "line_id": str(entry.get("line_id") or "").strip(),
        "created_at": _coerce_datetime(entry.get("created_at")),
        "updated_at": _coerce_datetime(entry.get("updated_at")),
    }


def _product_query(name: str, category: str, brand: str) -> dict[str, Any]:
    return {
        "name_key": name.strip().lower(),
        "category_key": category.strip().lower(),
        "brand_key": brand.strip().lower(),
    }


def _sum_entry_quantities(entries: list[dict[str, Any]]) -> int:
    return max(0, sum(int(entry.get("quantity") or 0) for entry in entries))


def serialize_inventory_product(doc: dict[str, Any]) -> dict[str, Any]:
    entries = [_normalize_entry(entry) for entry in (doc.get("entries") or [])]
    total_stock = _sum_entry_quantities(entries)
    reorder_point = int(doc.get("reorder_point") or 10)
    unit_cost = 0.0
    selling_price = 0.0
    if entries:
        latest = max(entries, key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min)
        unit_cost = float(latest.get("cost_price") or 0)
        selling_price = float(latest.get("selling_price") or 0)

    branch_names = sorted({str(entry.get("branch")).strip() for entry in entries if str(entry.get("branch") or "").strip()}, key=str.lower)
    return {
        "id": str(doc.get("_id") or ""),
        "sku": doc.get("sku") or "",
        "name": doc.get("name") or "",
        "category": doc.get("category") or "",
        "brand": doc.get("brand") or "",
        "description": doc.get("description") or "",
        "image": doc.get("image_url") or "",
        "cfImageId": doc.get("cf_image_id") or "",
        "totalStock": total_stock,
        "available": total_stock,
        "reserved": 0,
        "forecastDemand": 0,
        "safeAvailable": total_stock,
        "reorderPoint": reorder_point,
        "reorderQuantity": int(doc.get("reorder_quantity") or 20),
        "unitCost": unit_cost,
        "sellingPrice": selling_price,
        "status": _status_for_quantity(total_stock, reorder_point),
        "branches": branch_names,
        "entries": [
            {
        "branch": entry.get("branch") or "",
        "locationId": entry.get("location_id") or "",
        "locationName": entry.get("location_name") or "",
        "locationCode": entry.get("location_code") or "",
        "quantity": int(entry.get("quantity") or 0),
        "expiryDate": entry.get("expiry_date") or "",
                "reminderDays": int(entry.get("reminder_days") or 0),
                "costPrice": float(entry.get("cost_price") or 0),
                "sellingPrice": float(entry.get("selling_price") or 0),
                "installmentPrice": entry.get("installment_price"),
                "wholesalePrice": entry.get("wholesale_price"),
            }
            for entry in entries
        ],
    }


def _location_quantity(entries: list[dict[str, Any]], location_id: str) -> int:
    return max(0, sum(int(entry.get("quantity") or 0) for entry in entries if str(entry.get("location_id") or "") == location_id))


def _latest_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    return max(entries, key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min)


def _average_price(current_value: float | None, requested_value: float) -> float:
    if current_value is None or current_value <= 0:
        return round(requested_value, 2)
    return round((float(current_value) + float(requested_value)) / 2.0, 2)


def get_inventory_products_for_location(branch: str, location_id: str) -> list[dict[str, Any]]:
    rows = list(inventory_products_col.find({}).sort([("name", 1), ("updated_at", -1)]))
    location_products: list[dict[str, Any]] = []
    for row in rows:
        entries = [_normalize_entry(entry) for entry in (row.get("entries") or [])]
        current_qty = _location_quantity(entries, location_id)
        latest = max(entries, key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min) if entries else None
        if branch and not any((entry.get("branch") or "") == branch for entry in entries):
            current_qty = 0
        location_products.append(
            {
                "id": str(row.get("_id") or ""),
                "name": row.get("name") or "",
                "sku": row.get("sku") or "",
                "category": row.get("category") or "",
                "brand": row.get("brand") or "",
                "quantity": current_qty,
                "unitCost": float((latest or {}).get("cost_price") or 0),
            }
        )
    return location_products


def _build_stock_session_number(now: datetime) -> str:
    return f"SUS-{now.strftime('%Y%m%d-%H%M%S')}"


def _build_stock_taking_session_number(now: datetime) -> str:
    return f"STC-{now.strftime('%Y%m%d-%H%M%S')}"


def create_stock_update_session(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    branch = str(payload.get("branch") or "").strip()
    location_id = str(payload.get("locationId") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    updates = payload.get("updates") or []

    if not branch:
        raise ValueError("Branch is required.")
    if not location_id:
        raise ValueError("Warehouse or room is required.")
    if not reason:
        raise ValueError("Update reason is required.")
    if not isinstance(updates, list) or not updates:
        raise ValueError("Add at least one stock update before closing the session.")

    location_oid = _safe_object_id(location_id)
    if location_oid is None:
        raise ValueError("Invalid location selected.")

    location_doc = inventory_locations_col.find_one({"_id": location_oid})
    if not location_doc:
        raise ValueError("Selected location was not found.")
    if (location_doc.get("branch") or "").strip() != branch:
        raise ValueError("Selected location does not belong to the chosen branch.")
    if (location_doc.get("status") or "").strip().lower() != "active":
        raise ValueError("Selected location is inactive.")

    now = datetime.utcnow()
    session_number = _build_stock_session_number(now)
    current_location_units = int(location_doc.get("stock_units") or 0)
    capacity = int(location_doc.get("capacity") or 0)

    pending_by_product: dict[str, int] = {}
    session_updates: list[dict[str, Any]] = []
    entry_payloads: list[dict[str, Any]] = []
    net_location_change = 0

    for index, raw_update in enumerate(updates, start=1):
        if not isinstance(raw_update, dict):
            raise ValueError(f"Invalid update row at position {index}.")

        product_id = str(raw_update.get("productId") or "").strip()
        update_type = str(raw_update.get("updateType") or "").strip().lower()
        quantity_changed = int(raw_update.get("quantityChanged") or 0)
        notes = str(raw_update.get("notes") or "").strip()

        if update_type not in {"add", "subtract"}:
            raise ValueError("Update type must be add or subtract.")
        if quantity_changed <= 0:
            raise ValueError("Each stock update quantity must be greater than 0.")

        product_oid = _safe_object_id(product_id)
        if product_oid is None:
            raise ValueError("Invalid product selected for stock update.")

        product_doc = inventory_products_col.find_one({"_id": product_oid})
        if not product_doc:
            raise ValueError("One of the selected products no longer exists.")

        entries = [_normalize_entry(entry) for entry in (product_doc.get("entries") or [])]
        base_quantity = _location_quantity(entries, location_id)
        current_quantity = max(0, base_quantity + pending_by_product.get(product_id, 0))
        signed_quantity = quantity_changed if update_type == "add" else -quantity_changed
        new_quantity = current_quantity + signed_quantity
        if new_quantity < 0:
            raise ValueError(f"{product_doc.get('name') or 'Selected product'} does not have enough stock in this location.")

        latest = max(entries, key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min) if entries else {}
        unit_cost = float((latest or {}).get("cost_price") or 0)
        selling_price = float((latest or {}).get("selling_price") or 0)
        installment_price = latest.get("installment_price") if latest else None
        wholesale_price = latest.get("wholesale_price") if latest else None
        reminder_days = int((latest or {}).get("reminder_days") or 0)
        expiry_date = str((latest or {}).get("expiry_date") or "").strip()

        pending_by_product[product_id] = pending_by_product.get(product_id, 0) + signed_quantity
        net_location_change += signed_quantity
        session_updates.append(
            {
                "productId": product_id,
                "productName": product_doc.get("name") or "",
                "sku": product_doc.get("sku") or "",
                "category": product_doc.get("category") or "",
                "brand": product_doc.get("brand") or "",
                "currentQuantity": current_quantity,
                "updateType": update_type,
                "quantityChanged": quantity_changed,
                "newQuantity": new_quantity,
                "unitCost": unit_cost,
                "valueImpact": signed_quantity * unit_cost,
                "notes": notes,
            }
        )
        entry_payloads.append(
            {
                "product_oid": product_oid,
                "entry": {
                    "branch": branch,
                    "location_id": location_id,
                    "location_name": (location_doc.get("name") or "").strip(),
                    "location_code": (location_doc.get("code") or "").strip(),
                    "quantity": signed_quantity,
                    "expiry_date": expiry_date,
                    "reminder_days": reminder_days,
                    "cost_price": unit_cost,
                    "selling_price": selling_price,
                    "installment_price": installment_price,
                    "wholesale_price": wholesale_price,
                    "created_at": now,
                    "updated_at": now,
                    "movement_type": "stock_update_session",
                    "session_number": session_number,
                    "reason": reason,
                    "notes": notes,
                },
            }
        )

    projected_location_units = current_location_units + net_location_change
    if projected_location_units < 0:
        raise ValueError("This session would reduce the location stock below zero.")
    if capacity > 0 and projected_location_units > capacity:
        raise ValueError(
            f"{location_doc.get('name') or 'Selected location'} exceeds capacity. "
            f"Capacity: {capacity}, current: {current_location_units}, requested net: {net_location_change}."
        )

    for row in entry_payloads:
        inventory_products_col.update_one(
            {"_id": row["product_oid"]},
            {
                "$push": {"entries": row["entry"]},
                "$set": {"updated_at": now},
            },
        )

    inventory_locations_col.update_one(
        {"_id": location_oid},
        {
            "$inc": {"stock_units": net_location_change},
            "$set": {"updated_at": now},
        },
    )

    session_doc = {
        "session_number": session_number,
        "branch": branch,
        "location_id": location_id,
        "location_name": (location_doc.get("name") or "").strip(),
        "location_code": (location_doc.get("code") or "").strip(),
        "reason": reason,
        "status": "closed",
        "created_by": {
            "user_id": identity.get("user_id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
        },
        "created_at": now,
        "closed_at": now,
        "updates": session_updates,
        "summary": {
            "totalProductsUpdated": len(session_updates),
            "totalQuantityAdded": sum(item["quantityChanged"] for item in session_updates if item["updateType"] == "add"),
            "totalQuantitySubtracted": sum(item["quantityChanged"] for item in session_updates if item["updateType"] == "subtract"),
            "netQuantityChange": net_location_change,
            "totalValueImpact": sum(float(item.get("valueImpact") or 0) for item in session_updates),
        },
    }
    inventory_stock_update_sessions_col.insert_one(session_doc)

    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()

    return {
        "id": str(session_doc.get("_id") or ""),
        "sessionNumber": session_number,
        "branch": branch,
        "warehouse": session_doc["location_name"],
        "warehouseCode": session_doc["location_code"],
        "reason": reason,
        "status": "closed",
        "createdBy": identity.get("name") or identity.get("username") or "Inventory User",
        "createdAt": now.strftime("%Y-%m-%d %H:%M"),
        "closedAt": now.strftime("%Y-%m-%d %H:%M"),
        "updates": session_updates,
        "summary": session_doc["summary"],
    }


def _serialize_stock_taking_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "productId": str(item.get("product_id") or ""),
        "productName": item.get("product_name") or "",
        "sku": item.get("sku") or "",
        "category": item.get("category") or "",
        "brand": item.get("brand") or "",
        "systemQuantity": int(item.get("system_quantity") or 0),
        "actualCount": item.get("actual_count"),
        "damagedQuantity": int(item.get("damaged_quantity") or 0),
        "variance": int(item.get("variance") or 0),
        "varianceValue": float(item.get("variance_value") or 0),
        "unitCost": float(item.get("unit_cost") or 0),
        "notes": item.get("notes") or "",
        "counted": bool(item.get("counted")),
        "discrepancyReason": item.get("discrepancy_reason") or "",
        "investigationRequired": bool(item.get("investigation_required")),
    }


def _stock_taking_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    counted_items = [item for item in items if item.get("counted")]
    discrepancies = [item for item in counted_items if int(item.get("variance") or 0) != 0]
    return {
        "totalItems": len(items),
        "countedItems": len(counted_items),
        "discrepancies": len(discrepancies),
        "totalVariance": round(sum(float(item.get("variance_value") or 0) for item in counted_items), 2),
    }


def _serialize_stock_taking_session(doc: dict[str, Any]) -> dict[str, Any]:
    items = doc.get("items") or []
    summary = _stock_taking_summary(items)
    return {
        "id": str(doc.get("_id") or ""),
        "sessionNumber": doc.get("session_number") or "",
        "branch": doc.get("branch") or "",
        "subWarehouse": doc.get("location_name") or "",
        "locationId": doc.get("location_id") or "",
        "locationCode": doc.get("location_code") or "",
        "auditor": ((doc.get("created_by") or {}).get("name") or (doc.get("created_by") or {}).get("username") or "Inventory User"),
        "date": doc.get("count_date") or "",
        "status": doc.get("status") or "draft",
        "totalItems": summary["totalItems"],
        "countedItems": summary["countedItems"],
        "discrepancies": summary["discrepancies"],
        "totalVariance": summary["totalVariance"],
        "createdDate": doc.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if isinstance(doc.get("created_at"), datetime) else "",
        "submittedDate": doc.get("submitted_at").strftime("%Y-%m-%d %H:%M:%S") if isinstance(doc.get("submitted_at"), datetime) else "",
        "approvedDate": doc.get("approved_at").strftime("%Y-%m-%d %H:%M:%S") if isinstance(doc.get("approved_at"), datetime) else "",
    }


def list_stock_taking_sessions() -> list[dict[str, Any]]:
    rows = list(inventory_stock_taking_sessions_col.find({}).sort([("created_at", -1)]).limit(200))
    return [_serialize_stock_taking_session(row) for row in rows]


def get_stock_taking_dashboard() -> dict[str, Any]:
    sessions = list_stock_taking_sessions()
    discrepancy_by_reason: dict[str, int] = {}
    recent_alerts: list[dict[str, Any]] = []
    monthly_variance: dict[str, float] = {}

    raw_rows = list(inventory_stock_taking_sessions_col.find({}).sort([("created_at", -1)]).limit(200))
    for row in raw_rows:
        created_at = row.get("created_at")
        month_key = created_at.strftime("%Y-%m") if isinstance(created_at, datetime) else "unknown"
        summary = _stock_taking_summary(row.get("items") or [])
        monthly_variance[month_key] = monthly_variance.get(month_key, 0.0) + float(summary["totalVariance"])
        for item in row.get("items") or []:
          reason = str(item.get("discrepancy_reason") or "").strip()
          if reason:
              discrepancy_by_reason[reason] = discrepancy_by_reason.get(reason, 0) + 1
          if bool(item.get("investigation_required")):
              recent_alerts.append(
                  {
                      "sessionNumber": row.get("session_number") or "",
                      "productName": item.get("product_name") or "",
                      "variance": int(item.get("variance") or 0),
                      "reason": reason or "unresolved",
                      "branch": row.get("branch") or "",
                      "locationName": row.get("location_name") or "",
                  }
              )

    metrics = {
        "activeSessions": sum(1 for session in sessions if session["status"] in {"draft", "counting"}),
        "pendingApproval": sum(1 for session in sessions if session["status"] == "submitted"),
        "completedThisMonth": sum(1 for session in sessions if session["status"] in {"approved", "closed"}),
        "totalDiscrepancies": sum(int(session["discrepancies"]) for session in sessions),
        "totalVarianceValue": round(sum(abs(float(session["totalVariance"])) for session in sessions), 2),
        "shrinkageRate": round(
            (
                sum(abs(float(session["totalVariance"])) for session in sessions if float(session["totalVariance"]) < 0)
                / max(1.0, sum(abs(float(session["totalVariance"])) for session in sessions))
            )
            * 100,
            1,
        ) if sessions else 0.0,
        "locationsNotCounted": max(0, len(get_branches_payload().get("locations") or {}) - len({session["locationId"] for session in sessions if session["locationId"]})),
        "highVarianceAlerts": len(recent_alerts),
    }

    variance_trend = [
        {"month": month, "variance": round(value, 2)}
        for month, value in sorted(monthly_variance.items())
    ][-6:]
    reason_breakdown = [
        {"reason": reason, "count": count}
        for reason, count in sorted(discrepancy_by_reason.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "sessions": sessions,
        "metrics": metrics,
        "varianceTrend": variance_trend,
        "reasonBreakdown": reason_breakdown,
        "alerts": recent_alerts[:20],
    }


def create_stock_taking_session(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    branch = str(payload.get("branch") or "").strip()
    location_id = str(payload.get("locationId") or "").strip()
    count_date = str(payload.get("date") or "").strip()

    if not branch:
        raise ValueError("Branch is required.")
    if not location_id:
        raise ValueError("Warehouse or room is required.")

    location_oid = _safe_object_id(location_id)
    if location_oid is None:
        raise ValueError("Invalid location selected.")

    location_doc = inventory_locations_col.find_one({"_id": location_oid})
    if not location_doc:
        raise ValueError("Selected location was not found.")
    if (location_doc.get("branch") or "").strip() != branch:
        raise ValueError("Selected location does not belong to the chosen branch.")

    products = get_inventory_products_for_location(branch, location_id)
    now = datetime.utcnow()
    items = [
        {
            "id": f"SCI-{index + 1}",
            "product_id": product["id"],
            "product_name": product["name"],
            "sku": product["sku"],
            "category": product["category"],
            "brand": product["brand"],
            "system_quantity": int(product["quantity"] or 0),
            "actual_count": None,
            "damaged_quantity": 0,
            "variance": 0,
            "variance_value": 0.0,
            "unit_cost": float(product["unitCost"] or 0),
            "notes": "",
            "counted": False,
            "discrepancy_reason": "",
            "investigation_required": False,
        }
        for index, product in enumerate(products)
    ]

    doc = {
        "session_number": _build_stock_taking_session_number(now),
        "branch": branch,
        "location_id": location_id,
        "location_name": (location_doc.get("name") or "").strip(),
        "location_code": (location_doc.get("code") or "").strip(),
        "count_date": count_date or now.strftime("%Y-%m-%d"),
        "status": "counting",
        "created_by": {
            "user_id": identity.get("user_id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
        },
        "created_at": now,
        "updated_at": now,
        "items": items,
    }
    result = inventory_stock_taking_sessions_col.insert_one(doc)
    created = inventory_stock_taking_sessions_col.find_one({"_id": result.inserted_id}) or doc
    return _serialize_stock_taking_session(created)


def get_stock_taking_session_detail(session_id: str) -> dict[str, Any] | None:
    oid = _safe_object_id(session_id)
    if oid is None:
        return None
    doc = inventory_stock_taking_sessions_col.find_one({"_id": oid})
    if not doc:
        return None
    summary = _serialize_stock_taking_session(doc)
    return {
        **summary,
        "items": [_serialize_stock_taking_item(item) for item in (doc.get("items") or [])],
    }


def update_stock_taking_counts(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    oid = _safe_object_id(session_id)
    if oid is None:
        raise ValueError("Invalid session selected.")
    doc = inventory_stock_taking_sessions_col.find_one({"_id": oid})
    if not doc:
        raise ValueError("Stock taking session not found.")
    if (doc.get("status") or "") not in {"draft", "counting"}:
        raise ValueError("Only draft or counting sessions can be edited.")

    incoming_items = payload.get("items") or []
    if not isinstance(incoming_items, list):
        raise ValueError("Count items payload is invalid.")

    incoming_map = {
        str(item.get("id") or ""): item
        for item in incoming_items
        if isinstance(item, dict) and str(item.get("id") or "")
    }

    next_items: list[dict[str, Any]] = []
    for item in doc.get("items") or []:
        current = dict(item)
        incoming = incoming_map.get(str(item.get("id") or ""))
        if incoming is not None:
            actual_count_raw = incoming.get("actualCount")
            damaged_quantity = max(0, int(incoming.get("damagedQuantity") or 0))
            actual_count = None if actual_count_raw in {None, ""} else max(0, int(actual_count_raw))
            counted = actual_count is not None
            effective_count = max(0, (actual_count or 0) - damaged_quantity)
            variance = effective_count - int(item.get("system_quantity") or 0) if counted else 0
            variance_value = round(variance * float(item.get("unit_cost") or 0), 2)
            reason = str(incoming.get("discrepancyReason") or "").strip()
            notes = str(incoming.get("notes") or "").strip()
            current.update(
                {
                    "actual_count": actual_count,
                    "damaged_quantity": damaged_quantity,
                    "counted": counted,
                    "variance": variance,
                    "variance_value": variance_value,
                    "discrepancy_reason": reason if variance != 0 else "",
                    "notes": notes,
                    "investigation_required": variance != 0 and (abs(variance) >= 3 or reason in {"theft", "missing-item", "unrecorded-movement"}),
                }
            )
        next_items.append(current)

    next_status = "counting" if any(item.get("counted") for item in next_items) else "draft"
    inventory_stock_taking_sessions_col.update_one(
        {"_id": oid},
        {"$set": {"items": next_items, "status": next_status, "updated_at": datetime.utcnow()}},
    )
    return get_stock_taking_session_detail(session_id) or {}


def submit_stock_taking_session(session_id: str) -> dict[str, Any]:
    oid = _safe_object_id(session_id)
    if oid is None:
        raise ValueError("Invalid session selected.")
    doc = inventory_stock_taking_sessions_col.find_one({"_id": oid})
    if not doc:
        raise ValueError("Stock taking session not found.")
    items = doc.get("items") or []
    if any(not item.get("counted") for item in items):
        raise ValueError("All items must be counted before submission.")
    missing_reasons = [item.get("product_name") or "Item" for item in items if int(item.get("variance") or 0) != 0 and not str(item.get("discrepancy_reason") or "").strip()]
    if missing_reasons:
        raise ValueError("Each discrepancy must include a reason before submission.")
    now = datetime.utcnow()
    inventory_stock_taking_sessions_col.update_one(
        {"_id": oid},
        {"$set": {"status": "submitted", "submitted_at": now, "updated_at": now}},
    )
    return get_stock_taking_session_detail(session_id) or {}


def approve_stock_taking_session(session_id: str, identity: dict[str, Any]) -> dict[str, Any]:
    oid = _safe_object_id(session_id)
    if oid is None:
        raise ValueError("Invalid session selected.")
    doc = inventory_stock_taking_sessions_col.find_one({"_id": oid})
    if not doc:
        raise ValueError("Stock taking session not found.")
    if (doc.get("status") or "") not in {"submitted", "reviewed"}:
        raise ValueError("Only submitted sessions can be approved.")

    location_id = str(doc.get("location_id") or "")
    location_oid = _safe_object_id(location_id)
    if location_oid is None:
        raise ValueError("Session location is invalid.")
    location_doc = inventory_locations_col.find_one({"_id": location_oid})
    if not location_doc:
        raise ValueError("Session location no longer exists.")

    now = datetime.utcnow()
    net_location_change = 0
    for item in doc.get("items") or []:
        variance = int(item.get("variance") or 0)
        if variance == 0:
            continue
        product_oid = _safe_object_id(str(item.get("product_id") or ""))
        if product_oid is None:
            raise ValueError(f"Invalid product in session {doc.get('session_number') or ''}.")
        product_doc = inventory_products_col.find_one({"_id": product_oid})
        if not product_doc:
            raise ValueError(f"Product {item.get('product_name') or 'Unknown'} no longer exists.")
        entries = [_normalize_entry(entry) for entry in (product_doc.get("entries") or [])]
        current_quantity = _location_quantity(entries, location_id)
        if current_quantity + variance < 0:
            raise ValueError(f"Approving this session would reduce {item.get('product_name') or 'a product'} below zero at this location.")
        inventory_products_col.update_one(
            {"_id": product_oid},
            {
                "$push": {
                    "entries": {
                        "branch": doc.get("branch") or "",
                        "location_id": location_id,
                        "location_name": doc.get("location_name") or "",
                        "location_code": doc.get("location_code") or "",
                        "quantity": variance,
                        "expiry_date": "",
                        "reminder_days": 0,
                        "cost_price": float(item.get("unit_cost") or 0),
                        "selling_price": float(item.get("unit_cost") or 0),
                        "installment_price": None,
                        "wholesale_price": None,
                        "created_at": now,
                        "updated_at": now,
                        "movement_type": "stock_taking_approval",
                        "session_number": doc.get("session_number") or "",
                        "reason": item.get("discrepancy_reason") or "stock-taking",
                        "notes": item.get("notes") or "",
                    }
                },
                "$set": {"updated_at": now},
            },
        )
        net_location_change += variance

    inventory_locations_col.update_one(
        {"_id": location_oid},
        {"$inc": {"stock_units": net_location_change}, "$set": {"updated_at": now}},
    )
    inventory_stock_taking_sessions_col.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "approved",
                "approved_at": now,
                "closed_at": now,
                "approved_by": {
                    "user_id": identity.get("user_id"),
                    "username": identity.get("username"),
                    "name": identity.get("name"),
                },
                "updated_at": now,
            }
        },
    )
    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()
    return get_stock_taking_session_detail(session_id) or {}


def _product_display_group_key(doc: dict[str, Any]) -> str:
    name = str(doc.get("name") or "").strip().lower()
    return name or f"unnamed::{str(doc.get('_id') or '')}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return int(default)


def _parse_date_key(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except Exception:
        return None


def _purchase_completion_pct(purchase: dict[str, Any]) -> int:
    product = purchase.get("product") or {}
    status = str(product.get("status") or purchase.get("status") or "").strip().lower()
    if status in {"completed", "closed", "paid", "fully_paid"}:
        return 100

    start_raw = purchase.get("purchase_date")
    end_raw = purchase.get("end_date")
    if not start_raw or not end_raw:
        return 0

    try:
        start_date = datetime.fromisoformat(str(start_raw)[:10]).date()
        end_date = datetime.fromisoformat(str(end_raw)[:10]).date()
    except Exception:
        return 0

    total_days = max((end_date - start_date).days, 0)
    if total_days == 0:
        return 100 if datetime.utcnow().date() >= end_date else 0

    elapsed_days = max((min(datetime.utcnow().date(), end_date) - start_date).days, 0)
    return max(0, min(100, round((elapsed_days / total_days) * 100)))


def _component_quantity_for_inventory_product(doc: dict[str, Any], inventory_product_id: ObjectId) -> int:
    total = 0
    for component in doc.get("components") or []:
        if not isinstance(component, dict):
            continue
        if str(component.get("source_collection") or "").strip() != "inventory_products":
            continue
        if component.get("_id") == inventory_product_id:
            total += int(component.get("quantity") or 0)
    return total


def _get_inventory_product_card_usage(product_id: ObjectId, total_stock: int) -> dict[str, Any]:
    projection = {
        "name": 1,
        "description": 1,
        "image_url": 1,
        "price": 1,
        "cash_price": 1,
        "cost_price": 1,
        "product_type": 1,
        "category": 1,
        "package_name": 1,
        "components": 1,
        "manager_id": 1,
        "created_at": 1,
        "updated_at": 1,
    }
    product_docs = list(
        products_col.find(
            {
                "components": {
                    "$elemMatch": {
                        "_id": product_id,
                        "source_collection": "inventory_products",
                    }
                }
            },
            projection,
        )
        .sort([("updated_at", -1), ("created_at", -1)])
        .limit(5000)
    )
    if not product_docs:
        return {
            "summary": {
                "cardCount": 0,
                "managerCopyCount": 0,
                "totalRequiredUnits": 0,
                "availableStock": total_stock,
                "coveragePct": 0,
                "customerCount": 0,
                "purchaseCount": 0,
                "salesValue": 0.0,
            },
            "cards": [],
        }

    manager_ids = {
        doc.get("manager_id")
        for doc in product_docs
        if doc.get("manager_id")
    }
    manager_map = {
        str(doc.get("_id")): doc
        for doc in users_col.find({"_id": {"$in": list(manager_ids)}}, {"name": 1, "username": 1, "branch": 1})
    }

    grouped: dict[str, dict[str, Any]] = {}
    product_to_group: dict[str, str] = {}
    for doc in product_docs:
        group_key = _product_display_group_key(doc)
        product_id_str = str(doc.get("_id") or "")
        product_to_group[product_id_str] = group_key
        quantity_per_card = _component_quantity_for_inventory_product(doc, product_id)
        manager_id = str(doc.get("manager_id") or "")
        manager_doc = manager_map.get(manager_id) or {}
        manager_payload = {
            "id": manager_id,
            "name": str(manager_doc.get("name") or manager_doc.get("username") or "Manager").strip(),
            "branch": str(manager_doc.get("branch") or "").strip(),
            "price": _safe_float(doc.get("price")),
            "cashPrice": _safe_float(doc.get("cash_price")),
            "costPrice": _safe_float(doc.get("cost_price")),
            "quantityPerCard": quantity_per_card,
            "productDocumentId": product_id_str,
            "updatedAt": doc.get("updated_at").strftime("%Y-%m-%d") if isinstance(doc.get("updated_at"), datetime) else "",
        }
        group = grouped.setdefault(
            group_key,
            {
                "canonical": doc,
                "sourceProductIds": [],
                "managers": [],
                "branches": set(),
                "requiredUnits": 0,
                "customerIds": set(),
                "purchaseCount": 0,
                "salesValue": 0.0,
                "completion70": 0,
                "completion80": 0,
                "completion90": 0,
                "lastPurchaseDate": "",
            },
        )
        group["sourceProductIds"].append(product_id_str)
        group["managers"].append(manager_payload)
        group["requiredUnits"] += quantity_per_card
        if manager_payload["branch"]:
            group["branches"].add(manager_payload["branch"])

    for customer in customers_col.find({}, {"purchases": 1}).limit(10000):
        customer_id = str(customer.get("_id") or "")
        matched_groups: set[str] = set()
        for purchase in customer.get("purchases") or []:
            if not isinstance(purchase, dict):
                continue
            purchase_product = purchase.get("product") or {}
            if not isinstance(purchase_product, dict):
                continue
            group_key = product_to_group.get(str(purchase_product.get("_id") or "").strip())
            if not group_key:
                continue

            matched_groups.add(group_key)
            group = grouped[group_key]
            completion_pct = _purchase_completion_pct(purchase)
            quantity = max(_safe_int(purchase_product.get("quantity"), 1), 1)
            group["purchaseCount"] += 1
            group["salesValue"] += _safe_float(purchase_product.get("total"), _safe_float(purchase_product.get("price")) * quantity)
            if completion_pct >= 70:
                group["completion70"] += 1
            if completion_pct >= 80:
                group["completion80"] += 1
            if completion_pct >= 90:
                group["completion90"] += 1
            purchase_date = str(purchase.get("purchase_date") or "")[:10]
            if purchase_date and purchase_date > group["lastPurchaseDate"]:
                group["lastPurchaseDate"] = purchase_date

        for group_key in matched_groups:
            grouped[group_key]["customerIds"].add(customer_id)

    cards: list[dict[str, Any]] = []
    total_required_units = 0
    customer_ids: set[str] = set()
    total_purchase_count = 0
    total_sales_value = 0.0
    for group_key, group in grouped.items():
        canonical = group["canonical"]
        required_units = int(group["requiredUnits"] or 0)
        total_required_units += required_units
        customer_ids.update(group["customerIds"])
        total_purchase_count += int(group["purchaseCount"] or 0)
        total_sales_value += float(group["salesValue"] or 0)
        cards.append(
            {
                "id": group_key,
                "name": str(canonical.get("name") or "").strip(),
                "description": str(canonical.get("description") or "").strip(),
                "image": str(canonical.get("image_url") or "").strip(),
                "productType": str(canonical.get("product_type") or "").strip(),
                "category": str(canonical.get("category") or "").strip(),
                "packageName": str(canonical.get("package_name") or "").strip(),
                "price": _safe_float(canonical.get("price")),
                "cashPrice": _safe_float(canonical.get("cash_price")),
                "costPrice": _safe_float(canonical.get("cost_price")),
                "managerCount": len(group["managers"]),
                "branchCount": len(group["branches"]),
                "branches": sorted(group["branches"]),
                "quantityPerCard": max((row.get("quantityPerCard") or 0 for row in group["managers"]), default=0),
                "requiredUnits": required_units,
                "coveragePct": round((total_stock / required_units) * 100) if required_units > 0 else 0,
                "customers": len(group["customerIds"]),
                "purchaseCount": group["purchaseCount"],
                "salesValue": round(group["salesValue"], 2),
                "completion70": group["completion70"],
                "completion80": group["completion80"],
                "completion90": group["completion90"],
                "lastPurchaseDate": group["lastPurchaseDate"],
                "sourceProductIds": group["sourceProductIds"],
                "managers": sorted(group["managers"], key=lambda item: (item["name"].lower(), item["branch"].lower())),
            }
        )

    cards.sort(key=lambda item: (item.get("purchaseCount") or 0, item.get("name") or ""), reverse=True)
    return {
        "summary": {
            "cardCount": len(cards),
            "managerCopyCount": len(product_docs),
            "totalRequiredUnits": total_required_units,
            "availableStock": total_stock,
            "coveragePct": round((total_stock / total_required_units) * 100) if total_required_units > 0 else 0,
            "customerCount": len(customer_ids),
            "purchaseCount": total_purchase_count,
            "salesValue": round(total_sales_value, 2),
        },
        "cards": cards,
    }


def _get_inventory_product_customers(product_id: ObjectId) -> dict[str, Any]:
    projection = {
        "name": 1,
        "package_name": 1,
        "manager_id": 1,
        "components": 1,
    }
    product_docs = list(
        products_col.find(
            {
                "components": {
                    "$elemMatch": {
                        "_id": product_id,
                        "source_collection": "inventory_products",
                    }
                }
            },
            projection,
        ).limit(5000)
    )
    if not product_docs:
        return {
            "summary": {
                "customerCount": 0,
                "purchaseCount": 0,
                "totalPaid": 0.0,
            },
            "branches": [],
            "customers": [],
        }

    product_lookup = {
        str(doc.get("_id") or ""): {
            "cardName": str(doc.get("name") or doc.get("package_name") or "Product").strip(),
            "managerId": str(doc.get("manager_id") or "").strip(),
        }
        for doc in product_docs
        if doc.get("_id")
    }

    manager_lookup_ids = [_safe_object_id(meta["managerId"]) for meta in product_lookup.values() if meta.get("managerId")]
    manager_lookup_ids = [oid for oid in manager_lookup_ids if oid]
    manager_map = {
        str(doc.get("_id") or ""): doc
        for doc in users_col.find({"_id": {"$in": manager_lookup_ids}}, {"name": 1, "branch": 1})
    } if manager_lookup_ids else {}

    customer_projection = {
        "name": 1,
        "phone_number": 1,
        "location": 1,
        "date_registered": 1,
        "purchases": 1,
    }
    purchase_product_ids = [doc.get("_id") for doc in product_docs if doc.get("_id")] + list(product_lookup.keys())
    customers = list(
        customers_col.find(
            {"purchases.product._id": {"$in": purchase_product_ids}},
            customer_projection,
        ).limit(10000)
    )
    customer_ids = [customer.get("_id") for customer in customers if customer.get("_id")]

    payment_rows = list(
        payments_col.find(
            {
                "customer_id": {"$in": customer_ids},
                "payment_type": {"$ne": "WITHDRAWAL"},
            },
            {"customer_id": 1, "product_index": 1, "amount": 1, "payment_type": 1},
        )
    ) if customer_ids else []

    payment_map: dict[tuple[str, int], float] = {}
    for payment in payment_rows:
        customer_id = str(payment.get("customer_id") or "")
        product_index = _safe_int(payment.get("product_index"), -1)
        amount = _safe_float(payment.get("amount"), 0.0)
        key = (customer_id, product_index)
        if str(payment.get("payment_type") or "").strip().upper() == "WITHDRAWAL":
            payment_map[key] = payment_map.get(key, 0.0) - amount
        else:
            payment_map[key] = payment_map.get(key, 0.0) + amount

    rows: list[dict[str, Any]] = []
    seen_customer_ids: set[str] = set()
    branch_set: set[str] = set()
    total_paid = 0.0

    for customer in customers:
        customer_id = str(customer.get("_id") or "")
        purchases = customer.get("purchases") or []
        if not isinstance(purchases, list):
            continue

        for index, purchase in enumerate(purchases):
            if not isinstance(purchase, dict):
                continue
            product = purchase.get("product") or {}
            if not isinstance(product, dict):
                continue

            source_product_id = str(product.get("_id") or "").strip()
            source_meta = product_lookup.get(source_product_id)
            if not source_meta:
                continue

            manager_doc = manager_map.get(source_meta.get("managerId") or "", {})
            branch = str(manager_doc.get("branch") or "").strip()
            amount_paid = max(0.0, round(payment_map.get((customer_id, index), 0.0), 2))
            date_registered = str(customer.get("date_registered") or customer.get("created_at") or "")[:10]
            purchase_date = str(purchase.get("purchase_date") or "")[:10]

            rows.append(
                {
                    "id": f"{customer_id}:{index}",
                    "customerId": customer_id,
                    "customerName": str(customer.get("name") or "").strip(),
                    "customerPhone": str(customer.get("phone_number") or "").strip(),
                    "branch": branch,
                    "location": str(customer.get("location") or "").strip(),
                    "dateRegistered": date_registered,
                    "purchaseDate": purchase_date,
                    "amountPaid": amount_paid,
                    "productCard": source_meta.get("cardName") or "Product",
                    "profileUrl": f"/customer/{customer_id}",
                }
            )
            seen_customer_ids.add(customer_id)
            total_paid += amount_paid
            if branch:
                branch_set.add(branch)

    rows.sort(
        key=lambda row: (
            (row.get("branch") or "").lower(),
            (row.get("customerName") or "").lower(),
            row.get("purchaseDate") or "",
        )
    )

    return {
        "summary": {
            "customerCount": len(seen_customer_ids),
            "purchaseCount": len(rows),
            "totalPaid": round(total_paid, 2),
        },
        "branches": sorted(branch_set),
        "customers": rows,
    }


def _empty_forecast(total_stock: int) -> dict[str, Any]:
    return {
        "summary": {
            "last7DaysUnits": 0,
            "last30DaysUnits": 0,
            "last90DaysUnits": 0,
            "projected30DaysUnits": 0,
            "dailyRunRate": 0.0,
            "coverageDays": None,
            "recommendedReorderUnits": 0,
            "riskLevel": "no-demand",
            "basis": "No linked customer purchases found for this inventory item.",
            "availableStock": total_stock,
        },
        "byCard": [],
        "weeklyTrend": [],
        "recentDemand": [],
    }


def _risk_level(coverage_days: int | None, projected_30_days: int, total_stock: int) -> str:
    if projected_30_days <= 0:
        return "no-demand"
    if total_stock <= 0 or (coverage_days is not None and coverage_days <= 14):
        return "critical"
    if coverage_days is not None and coverage_days <= 30:
        return "warning"
    return "healthy"


def _get_inventory_product_forecast(product_id: ObjectId, total_stock: int, reorder_point: int) -> dict[str, Any]:
    projection = {
        "name": 1,
        "image_url": 1,
        "components": 1,
        "manager_id": 1,
    }
    product_docs = list(
        products_col.find(
            {
                "components": {
                    "$elemMatch": {
                        "_id": product_id,
                        "source_collection": "inventory_products",
                    }
                }
            },
            projection,
        ).limit(5000)
    )
    if not product_docs:
        return _empty_forecast(total_stock)

    product_lookup: dict[str, dict[str, Any]] = {}
    for doc in product_docs:
        quantity_per_card = _component_quantity_for_inventory_product(doc, product_id)
        if quantity_per_card <= 0:
            continue
        product_lookup[str(doc.get("_id") or "")] = {
            "cardId": _product_display_group_key(doc),
            "cardName": str(doc.get("name") or "Product Card").strip(),
            "image": str(doc.get("image_url") or "").strip(),
            "quantityPerCard": quantity_per_card,
        }

    if not product_lookup:
        return _empty_forecast(total_stock)

    today = datetime.utcnow().date()
    events: list[dict[str, Any]] = []
    by_card: dict[str, dict[str, Any]] = {}
    for customer in customers_col.find({}, {"name": 1, "purchases": 1}).limit(10000):
        customer_name = str(customer.get("name") or "Customer").strip()
        for purchase in customer.get("purchases") or []:
            if not isinstance(purchase, dict):
                continue
            purchase_product = purchase.get("product") or {}
            if not isinstance(purchase_product, dict):
                continue
            product_doc_id = str(purchase_product.get("_id") or "").strip()
            card_info = product_lookup.get(product_doc_id)
            if not card_info:
                continue
            purchase_day = _parse_date_key(purchase.get("purchase_date"))
            if purchase_day is None or purchase_day > today:
                continue
            card_quantity = max(_safe_int(purchase_product.get("quantity"), 1), 1)
            consumed_units = card_quantity * int(card_info.get("quantityPerCard") or 0)
            if consumed_units <= 0:
                continue
            event = {
                "date": purchase_day.isoformat(),
                "cardId": card_info["cardId"],
                "cardName": card_info["cardName"],
                "customerName": customer_name,
                "cardQuantity": card_quantity,
                "unitsConsumed": consumed_units,
                "salesValue": _safe_float(purchase_product.get("total"), _safe_float(purchase_product.get("price")) * card_quantity),
            }
            events.append(event)

            card_bucket = by_card.setdefault(
                card_info["cardId"],
                {
                    "cardId": card_info["cardId"],
                    "cardName": card_info["cardName"],
                    "image": card_info["image"],
                    "quantityPerCard": card_info["quantityPerCard"],
                    "last7DaysUnits": 0,
                    "last30DaysUnits": 0,
                    "last90DaysUnits": 0,
                    "purchaseCount": 0,
                    "salesValue": 0.0,
                    "lastPurchaseDate": "",
                },
            )
            card_bucket["purchaseCount"] += 1
            card_bucket["salesValue"] += event["salesValue"]
            if event["date"] > card_bucket["lastPurchaseDate"]:
                card_bucket["lastPurchaseDate"] = event["date"]

            days_ago = (today - purchase_day).days
            if days_ago < 7:
                card_bucket["last7DaysUnits"] += consumed_units
            if days_ago < 30:
                card_bucket["last30DaysUnits"] += consumed_units
            if days_ago < 90:
                card_bucket["last90DaysUnits"] += consumed_units

    if not events:
        return _empty_forecast(total_stock)

    def units_in_window(days: int) -> int:
        cutoff = today - timedelta(days=days - 1)
        return sum(int(event["unitsConsumed"] or 0) for event in events if _parse_date_key(event["date"]) and _parse_date_key(event["date"]) >= cutoff)

    last_7 = units_in_window(7)
    last_30 = units_in_window(30)
    last_90 = units_in_window(90)
    weekly_projection = (last_7 / 7) * 30 if last_7 > 0 else 0
    monthly_projection = float(last_30)
    quarterly_projection = (last_90 / 90) * 30 if last_90 > 0 else 0
    projected_30 = round((weekly_projection * 0.25) + (monthly_projection * 0.55) + (quarterly_projection * 0.20))
    daily_run_rate = round(projected_30 / 30, 2) if projected_30 > 0 else 0.0
    coverage_days = int(total_stock / daily_run_rate) if daily_run_rate > 0 else None
    recommended_reorder = max(0, int(projected_30 + reorder_point - total_stock))

    weekly_trend: list[dict[str, Any]] = []
    for index in range(7, -1, -1):
        start = today - timedelta(days=(index * 7) + 6)
        end = today - timedelta(days=index * 7)
        units = sum(
            int(event["unitsConsumed"] or 0)
            for event in events
            if (event_day := _parse_date_key(event["date"])) and start <= event_day <= end
        )
        weekly_trend.append(
            {
                "label": f"{start.strftime('%b %d')} - {end.strftime('%b %d')}",
                "units": units,
            }
        )

    recent_demand = sorted(events, key=lambda item: item["date"], reverse=True)[:10]
    by_card_rows = sorted(
        [
            {
                **row,
                "salesValue": round(float(row.get("salesValue") or 0), 2),
                "sharePct": round((int(row.get("last90DaysUnits") or 0) / last_90) * 100) if last_90 > 0 else 0,
            }
            for row in by_card.values()
        ],
        key=lambda item: (item["last30DaysUnits"], item["last90DaysUnits"], item["cardName"]),
        reverse=True,
    )

    return {
        "summary": {
            "last7DaysUnits": last_7,
            "last30DaysUnits": last_30,
            "last90DaysUnits": last_90,
            "projected30DaysUnits": projected_30,
            "dailyRunRate": daily_run_rate,
            "coverageDays": coverage_days,
            "recommendedReorderUnits": recommended_reorder,
            "riskLevel": _risk_level(coverage_days, projected_30, total_stock),
            "basis": "Forecast uses customer purchases of product cards linked to this inventory item.",
            "availableStock": total_stock,
        },
        "byCard": by_card_rows,
        "weeklyTrend": weekly_trend,
        "recentDemand": recent_demand,
    }


def list_inventory_products() -> list[dict[str, Any]]:
    rows = list(inventory_products_col.find({}).sort([("updated_at", -1), ("created_at", -1)]))
    return [serialize_inventory_product(row) for row in rows]


def inventory_product_page(args):
    import re
    try:
        page = max(1, int(args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    query = {}
    term = (args.get('search') or '').strip()
    if term:
        query['$or'] = [{field: {'$regex': re.escape(term), '$options': 'i'}} for field in ('name', 'sku', 'category', 'brand')]
    for field in ('category', 'brand'):
        if args.get(field) and args[field] != 'all':
            query[field] = args[field]
    total = inventory_products_col.count_documents(query)
    pages = max(1, (total + 24) // 25)
    page = min(page, pages)
    rows = inventory_products_col.find(query).sort([('updated_at', -1), ('created_at', -1), ('_id', -1)]).skip((page - 1) * 25).limit(25)
    # Global metrics keep legacy integer/clamping semantics. No entry histories,
    # prices, descriptions or images are loaded for the summary traversal.
    metrics = dict.fromkeys(('totalItems', 'totalStock', 'availableStock', 'reservedStock', 'forecastDemand', 'criticalItems', 'lowStockItems'), 0)
    categories, brands = set(), set()
    for doc in inventory_products_col.find({}, {'entries.quantity': 1, 'reorder_point': 1, 'category': 1, 'brand': 1}):
        quantity = _sum_entry_quantities(doc.get('entries') or [])
        status = _status_for_quantity(quantity, int(doc.get('reorder_point') or 10))
        metrics['totalItems'] += 1
        metrics['totalStock'] += quantity
        metrics['availableStock'] += quantity
        metrics['criticalItems'] += status == 'critical'
        metrics['lowStockItems'] += status == 'warning'
        if doc.get('category'): categories.add(doc['category'])
        if doc.get('brand'): brands.add(doc['brand'])
    return {'products': [serialize_inventory_product(row) for row in rows], 'summary': metrics,
            'categories': sorted(categories), 'brands': sorted(brands),
            'pagination': {'page': page, 'total': total, 'totalPages': pages, 'perPage': 25}}


def get_inventory_product_detail(product_id: str) -> dict[str, Any] | None:
    oid = _safe_object_id(product_id)
    if oid is None:
        return None

    doc = inventory_products_col.find_one({"_id": oid})
    if not doc:
        return None

    entries = [_normalize_entry(entry) for entry in (doc.get("entries") or [])]
    serialized = serialize_inventory_product(doc)
    latest_entry_dt = max(
        (entry.get("updated_at") or entry.get("created_at") or datetime.min for entry in entries),
        default=datetime.min,
    )

    location_totals: dict[str, dict[str, Any]] = {}
    for entry in entries:
        location_id = entry.get("location_id") or ""
        branch_name = entry.get("branch") or ""
        key = location_id or f"branch::{branch_name}::{entry.get('location_code') or entry.get('location_name') or 'unknown'}"
        bucket = location_totals.setdefault(
            key,
            {
                "locationId": location_id,
                "locationName": entry.get("location_name") or "Unknown Location",
                "locationCode": entry.get("location_code") or "",
                "branch": branch_name,
                "quantity": 0,
                "latestExpiryDate": "",
                "latestCostPrice": 0.0,
            },
        )
        bucket["quantity"] += int(entry.get("quantity") or 0)
        if entry.get("expiry_date"):
            bucket["latestExpiryDate"] = max(bucket.get("latestExpiryDate") or "", entry.get("expiry_date") or "")
        bucket["latestCostPrice"] = float(entry.get("cost_price") or bucket["latestCostPrice"] or 0)

    detail_locations: list[dict[str, Any]] = []
    for bucket in location_totals.values():
        location_doc = None
        if bucket["locationId"]:
          location_doc = inventory_locations_col.find_one({"_id": _safe_object_id(bucket["locationId"])})

        stock_units = int(location_doc.get("stock_units") or 0) if location_doc else int(bucket["quantity"] or 0)
        capacity = int(location_doc.get("capacity") or 0) if location_doc else 0
        detail_locations.append(
            {
                "locationId": bucket["locationId"],
                "locationName": bucket["locationName"],
                "locationCode": bucket["locationCode"],
                "branch": bucket["branch"],
                "type": (location_doc.get("type") or "room") if location_doc else "room",
                "status": (
                    "active"
                    if (((location_doc.get("status") or "Active").strip().lower() == "active") if location_doc else True)
                    else "inactive"
                ),
                "responsibleUser": (location_doc.get("responsible_user") or "") if location_doc else "",
                "productStock": int(bucket["quantity"] or 0),
                "locationTotalStock": stock_units,
                "capacity": capacity,
                "utilizationPct": round((stock_units / capacity) * 100) if capacity > 0 else 0,
                "latestExpiryDate": bucket["latestExpiryDate"] or "",
                "latestCostPrice": float(bucket["latestCostPrice"] or 0),
            }
        )

    detail_locations.sort(key=lambda item: (item.get("branch") or "", item.get("locationName") or ""))

    def _movement_type(source: str, quantity: int) -> str:
        source_key = (source or "").strip().lower()
        if source_key == "manager_branch_request_transfer_out":
            return "Branch Request Transfer Out"
        if source_key == "manager_branch_request_transfer_in":
            return "Branch Request Transfer In"
        if source_key == "stock_update_session":
            return "Stock Update Session"
        if source_key == "stock_taking_adjustment":
            return "Stock Taking Adjustment"
        if source_key == "warehouse_transfer":
            return "Warehouse Transfer"
        if source_key == "product_create":
            return "Initial Stock Load"
        if quantity >= 0:
            return "Stock In"
        return "Stock Out"

    movement_history = []
    for index, entry in enumerate(
        sorted(
            entries,
            key=lambda item: item.get("updated_at") or item.get("created_at") or datetime.min,
            reverse=True,
        )
    ):
        quantity = int(entry.get("quantity") or 0)
        movement_at = entry.get("updated_at") or entry.get("created_at")
        source = entry.get("source") or ""
        movement_history.append(
            {
                "id": f"{serialized.get('id')}-{index}",
                "type": _movement_type(source, quantity),
                "source": source,
                "quantity": quantity,
                "direction": "in" if quantity >= 0 else "out",
                "branch": entry.get("branch") or "",
                "locationId": entry.get("location_id") or "",
                "locationName": entry.get("location_name") or "",
                "locationCode": entry.get("location_code") or "",
                "orderId": entry.get("order_id") or "",
                "lineId": entry.get("line_id") or "",
                "costPrice": float(entry.get("cost_price") or 0),
                "sellingPrice": float(entry.get("selling_price") or 0),
                "movedAt": movement_at.isoformat() if isinstance(movement_at, datetime) else "",
            }
        )

    return {
        **serialized,
        "description": doc.get("description") or "",
        "brand": doc.get("brand") or "",
        "lastRestocked": latest_entry_dt.strftime("%Y-%m-%d") if latest_entry_dt != datetime.min else "",
        "createdAt": doc.get("created_at").strftime("%Y-%m-%d") if isinstance(doc.get("created_at"), datetime) else "",
        "locations": detail_locations,
        "movementHistory": movement_history,
        "productCards": _get_inventory_product_card_usage(oid, int(serialized.get("totalStock") or 0)),
        "customers": _get_inventory_product_customers(oid),
        "forecast": _get_inventory_product_forecast(
            oid,
            int(serialized.get("totalStock") or 0),
            int(serialized.get("reorderPoint") or 0),
        ),
    }


def get_inventory_branch_names() -> list[str]:
    payload = get_branches_payload()
    return [branch.get("name") or "" for branch in payload.get("branches") or [] if branch.get("name")]


def get_inventory_distribution_payload() -> dict[str, Any]:
    return get_branches_payload()


def _build_entry_rows(
    payload: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[ObjectId, int]]:
    quantity = int(payload.get("quantity") or 0)
    reminder_days = int(payload.get("reminderDays") or 0)
    cost_price = float(payload.get("costPrice") or 0)
    selling_price = float(payload.get("sellingPrice") or 0)
    installment_price = payload.get("installmentPrice")
    wholesale_price = payload.get("wholesalePrice")
    expiry_date = (payload.get("expiryDate") or "").strip()
    stock_assignments = payload.get("stockAssignments") or []

    if not isinstance(stock_assignments, list) or not stock_assignments:
        raise ValueError("Select at least one stock location.")

    location_increments: dict[ObjectId, int] = {}
    entry_rows: list[dict[str, Any]] = []

    for assignment in stock_assignments:
        if not isinstance(assignment, dict):
            continue
        location_id = str(assignment.get("locationId") or "").strip()
        branch = str(assignment.get("branch") or "").strip()
        if not location_id or not branch:
            raise ValueError("Each stock assignment must include a branch and location.")

        location_oid = _safe_object_id(location_id)
        if location_oid is None:
            raise ValueError("Invalid location selected.")

        location_doc = inventory_locations_col.find_one({"_id": location_oid})
        if not location_doc:
            raise ValueError("Selected location was not found.")

        location_branch = (location_doc.get("branch") or "").strip()
        if location_branch != branch:
            raise ValueError("Selected location does not belong to the chosen branch.")

        if (location_doc.get("status") or "").strip().lower() != "active":
            raise ValueError(f"{location_doc.get('name') or 'Selected location'} is inactive and cannot receive stock.")

        current_stock_units = int(location_doc.get("stock_units") or 0)
        capacity = int(location_doc.get("capacity") or 0)
        next_stock_units = current_stock_units + location_increments.get(location_oid, 0) + quantity
        if capacity > 0 and next_stock_units > capacity:
            raise ValueError(
                f"{location_doc.get('name') or 'Selected location'} exceeds capacity. "
                f"Capacity: {capacity}, current: {current_stock_units}, requested: {quantity}."
            )

        location_increments[location_oid] = location_increments.get(location_oid, 0) + quantity
        entry_rows.append(
            {
                "branch": location_branch,
                "location_id": str(location_doc["_id"]),
                "location_name": (location_doc.get("name") or "").strip(),
                "location_code": (location_doc.get("code") or "").strip(),
                "quantity": quantity,
                "expiry_date": expiry_date,
                "reminder_days": reminder_days,
                "cost_price": cost_price,
                "selling_price": selling_price,
                "installment_price": float(installment_price) if installment_price is not None else None,
                "wholesale_price": float(wholesale_price) if wholesale_price is not None else None,
                "created_at": now,
                "updated_at": now,
            }
        )

    if not entry_rows:
        raise ValueError("Select at least one stock location.")

    return entry_rows, location_increments


def create_inventory_product(payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip()
    category = (payload.get("category") or "").strip()
    brand = (payload.get("brand") or "").strip()
    description = (payload.get("description") or "").strip()
    image_url = (payload.get("imageUrl") or "").strip()
    image_id = (payload.get("imageId") or "").strip()
    now = datetime.utcnow()
    entry_rows, location_increments = _build_entry_rows(payload, now)

    product_doc = inventory_products_col.find_one(_product_query(name, category, brand))
    if product_doc:
        inventory_products_col.update_one(
            {"_id": product_doc["_id"]},
            {
                "$set": {
                    "description": description or product_doc.get("description") or "",
                    "image_url": image_url or product_doc.get("image_url") or "",
                    "cf_image_id": image_id or product_doc.get("cf_image_id") or "",
                    "updated_at": now,
                },
                "$push": {"entries": {"$each": entry_rows}},
            },
        )
        updated = inventory_products_col.find_one({"_id": product_doc["_id"]})
        for location_oid, increment in location_increments.items():
            inventory_locations_col.update_one(
                {"_id": location_oid},
                {"$inc": {"stock_units": increment}, "$set": {"updated_at": now}},
            )
        from .product_cards_store import invalidate_product_card_cache

        invalidate_product_card_cache()
        return serialize_inventory_product(updated or product_doc)

    doc = {
        "name": name,
        "name_key": name.lower(),
        "category": category,
        "category_key": category.lower(),
        "brand": brand,
        "brand_key": brand.lower(),
        "description": description,
        "image_url": image_url,
        "cf_image_id": image_id or None,
        "entries": entry_rows,
        "price_history": [
            {
                "old_selling_price": None,
                "requested_selling_price": float(payload.get("sellingPrice") or 0),
                "applied_selling_price": float(payload.get("sellingPrice") or 0),
                "changed_at": now,
                "changed_by": {
                    "user_id": identity.get("user_id"),
                    "username": identity.get("username"),
                    "name": identity.get("name"),
                },
                "reason": "initial_create",
            }
        ],
        "reorder_point": 10,
        "reorder_quantity": 20,
        "created_at": now,
        "updated_at": now,
        "created_by": {
            "user_id": identity.get("user_id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
        },
    }
    result = inventory_products_col.insert_one(doc)
    inventory_products_col.update_one(
        {"_id": result.inserted_id},
        {"$set": {"sku": _build_sku(category, name, result.inserted_id)}},
    )
    for location_oid, increment in location_increments.items():
        inventory_locations_col.update_one(
            {"_id": location_oid},
            {"$inc": {"stock_units": increment}, "$set": {"updated_at": now}},
        )
    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()
    created = inventory_products_col.find_one({"_id": result.inserted_id})
    return serialize_inventory_product(created or doc)


def update_inventory_product(product_id: str, payload: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    product_oid = _safe_object_id(product_id)
    if product_oid is None:
        raise ValueError("Invalid product ID.")

    product_doc = inventory_products_col.find_one({"_id": product_oid})
    if not product_doc:
        raise ValueError("Inventory product not found.")

    name = (payload.get("name") or product_doc.get("name") or "").strip()
    category = (payload.get("category") or product_doc.get("category") or "").strip()
    brand = (payload.get("brand") or product_doc.get("brand") or "").strip()
    description = (payload.get("description") or "").strip()
    image_url = (payload.get("imageUrl") or product_doc.get("image_url") or "").strip()
    image_id = (payload.get("imageId") or product_doc.get("cf_image_id") or "").strip()
    reminder_days = int(payload.get("reminderDays") or 0)
    expiry_date = (payload.get("expiryDate") or "").strip()
    cost_price = float(payload.get("costPrice") or 0)
    requested_selling_price = float(payload.get("sellingPrice") or 0)
    installment_price = payload.get("installmentPrice")
    wholesale_price = payload.get("wholesalePrice")

    if not name:
        raise ValueError("Product name is required.")
    if not category:
        raise ValueError("Category is required.")
    if not image_url:
        raise ValueError("Product image is required.")
    if cost_price <= 0:
        raise ValueError("Cost price must be greater than 0.")
    if requested_selling_price <= 0:
        raise ValueError("Selling price must be greater than 0.")

    entries = [_normalize_entry(entry) for entry in (product_doc.get("entries") or [])]
    latest = _latest_entry(entries)
    current_selling_price = float((latest or {}).get("selling_price") or 0)
    applied_selling_price = _average_price(current_selling_price or None, requested_selling_price)
    now = datetime.utcnow()

    updated_entries = []
    for entry in entries:
        updated_entries.append(
            {
                **entry,
                "expiry_date": expiry_date,
                "reminder_days": reminder_days,
                "cost_price": cost_price,
                "selling_price": applied_selling_price,
                "installment_price": float(installment_price) if installment_price is not None else None,
                "wholesale_price": float(wholesale_price) if wholesale_price is not None else None,
                "updated_at": now,
            }
        )

    price_history_entry = {
        "old_selling_price": round(current_selling_price, 2) if current_selling_price else None,
        "requested_selling_price": round(requested_selling_price, 2),
        "applied_selling_price": applied_selling_price,
        "changed_at": now,
        "changed_by": {
            "user_id": identity.get("user_id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
        },
        "reason": "inventory_edit",
    }

    inventory_products_col.update_one(
        {"_id": product_oid},
        {
            "$set": {
                "name": name,
                "name_key": name.lower(),
                "category": category,
                "category_key": category.lower(),
                "brand": brand,
                "brand_key": brand.lower(),
                "description": description,
                "image_url": image_url,
                "cf_image_id": image_id or None,
                "entries": updated_entries,
                "updated_at": now,
                "sku": product_doc.get("sku") or _build_sku(category, name, product_oid),
            },
            "$push": {"price_history": price_history_entry},
        },
    )

    inventory_logs_col.insert_one(
        {
            "product_id": product_oid,
            "product_name": name,
            "log_type": "price_update",
            "old_name": product_doc.get("name"),
            "new_name": name,
            "old_cost_price": float((latest or {}).get("cost_price") or 0),
            "new_cost_price": round(cost_price, 2),
            "old_selling_price": round(current_selling_price, 2) if current_selling_price else None,
            "requested_selling_price": round(requested_selling_price, 2),
            "new_selling_price": applied_selling_price,
            "price_formula": "average(old_selling_price, requested_selling_price)",
            "updated_by": identity.get("username") or identity.get("name") or "Unknown",
            "updated_at": now,
        }
    )

    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()
    updated = inventory_products_col.find_one({"_id": product_oid})
    return serialize_inventory_product(updated or product_doc)


def delete_inventory_product(product_id: str, confirm_name: str, identity: dict[str, Any]) -> None:
    product_oid = _safe_object_id(product_id)
    if product_oid is None:
        raise ValueError("Invalid product ID.")

    product_doc = inventory_products_col.find_one({"_id": product_oid})
    if not product_doc:
        raise ValueError("Inventory product not found.")

    product_name = (product_doc.get("name") or "").strip()
    if (confirm_name or "").strip() != product_name:
        raise ValueError("Enter the exact product name to delete this item.")

    entries = [_normalize_entry(entry) for entry in (product_doc.get("entries") or [])]
    location_adjustments: dict[ObjectId, int] = {}
    for entry in entries:
        location_id = str(entry.get("location_id") or "").strip()
        location_oid = _safe_object_id(location_id)
        if location_oid is None:
            continue
        location_adjustments[location_oid] = location_adjustments.get(location_oid, 0) + int(entry.get("quantity") or 0)

    now = datetime.utcnow()
    for location_oid, quantity in location_adjustments.items():
        inventory_locations_col.update_one(
            {"_id": location_oid},
            {"$inc": {"stock_units": -quantity}, "$set": {"updated_at": now}},
        )

    inventory_products_col.delete_one({"_id": product_oid})

    inventory_logs_col.insert_one(
        {
            "product_id": product_oid,
            "product_name": product_name,
            "log_type": "delete",
            "deleted_by": identity.get("username") or identity.get("name") or "Unknown",
            "deleted_at": now,
            "stock_removed": sum(location_adjustments.values()),
            "entries_removed": len(entries),
        }
    )

    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()
