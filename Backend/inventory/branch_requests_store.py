from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from db import db
from .settings_store import inventory_locations_col

orders_col = db["orders"]
order_events_col = db["order_events"]
users_col = db["users"]
inventory_products_col = db["inventory_products"]


try:
    orders_col.create_index([("updated_at", -1)], background=True)
    orders_col.create_index([("manager_id", 1), ("updated_at", -1)], background=True)
    inventory_products_col.create_index([("sku", 1)], background=True)
    inventory_locations_col.create_index([("branch", 1), ("status", 1), ("name", 1)], background=True)
except Exception:
    pass


def _safe_object_id(value: Any) -> ObjectId | None:
    try:
        if value and ObjectId.is_valid(str(value)):
            return ObjectId(str(value))
    except Exception:
        return None
    return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _remaining_qty(line: dict[str, Any]) -> int:
    return max(0, _safe_int(line.get("qty")) - _safe_int(line.get("delivered_qty")))


def _line_is_inventory_product(line: dict[str, Any]) -> bool:
    source = str(line.get("source_collection") or "").strip()
    if source == "inventory_products":
        return True
    return source == "" and _safe_object_id(line.get("product_id")) is not None


def _latest_prices(product: dict[str, Any]) -> dict[str, float | None]:
    latest = None
    for entry in product.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        marker = entry.get("updated_at") or entry.get("created_at") or datetime.min
        if latest is None or marker > (latest.get("updated_at") or latest.get("created_at") or datetime.min):
            latest = entry
    latest = latest or {}
    return {
        "cost_price": float(latest.get("cost_price") or 0),
        "selling_price": float(latest.get("selling_price") or 0),
        "installment_price": latest.get("installment_price"),
        "wholesale_price": latest.get("wholesale_price"),
    }


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
        "installment_price": entry.get("installment_price"),
        "wholesale_price": entry.get("wholesale_price"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }


def _first_branch_location(branch: str) -> dict[str, Any] | None:
    branch = (branch or "").strip()
    if not branch:
        return None
    return inventory_locations_col.find_one(
        {"branch": branch, "status": {"$regex": r"^active$", "$options": "i"}},
        sort=[("created_at", 1), ("name", 1), ("_id", 1)],
    ) or inventory_locations_col.find_one(
        {"branch": branch},
        sort=[("created_at", 1), ("name", 1), ("_id", 1)],
    )


def _location_stock_for_product(product: dict[str, Any], location_id: str) -> int:
    cached_totals = product.get("__location_stock_totals")
    if isinstance(cached_totals, dict):
        return max(0, int(cached_totals.get(location_id) or 0))

    total = 0
    for entry in product.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("location_id") or "") != location_id:
            continue
        total += int(entry.get("quantity") or 0)
    return max(0, total)


def _build_location_stock_totals(product: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in product.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        location_id = str(entry.get("location_id") or "").strip()
        if not location_id:
            continue
        totals[location_id] = totals.get(location_id, 0) + int(entry.get("quantity") or 0)
    return totals


def _prefetch_locations(product_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    location_ids: set[ObjectId] = set()
    for product in product_map.values():
        totals = _build_location_stock_totals(product)
        product["__location_stock_totals"] = totals
        for location_id in totals:
            oid = _safe_object_id(location_id)
            if oid is not None:
                location_ids.add(oid)

    if not location_ids:
        return {}

    return {
        str(doc.get("_id")): doc
        for doc in inventory_locations_col.find(
            {"_id": {"$in": list(location_ids)}},
            {"branch": 1, "status": 1, "name": 1, "code": 1, "type": 1},
        )
    }


def _find_source_location(
    product: dict[str, Any],
    destination_branch: str,
    required_qty: int,
    location_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in product.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        location_id = str(entry.get("location_id") or "").strip()
        if not location_id or location_id in seen:
            continue
        seen.add(location_id)
        available_qty = _location_stock_for_product(product, location_id)
        if available_qty <= 0:
            continue
        location = (location_map or {}).get(location_id)
        if location is None:
            location = inventory_locations_col.find_one({"_id": _safe_object_id(location_id)})
        if not location:
            continue
        if (location.get("status") or "").strip().lower() != "active":
            continue
        branch = (location.get("branch") or "").strip()
        if branch == destination_branch:
            continue
        candidates.append(
            {
                "location": location,
                "available_qty": available_qty,
            }
        )

    candidates.sort(
        key=lambda row: (
            0 if str((row["location"].get("type") or "")).strip().lower() == "warehouse" else 1,
            -int(row["available_qty"]),
            str(row["location"].get("name") or ""),
        )
    )
    return candidates[0]["location"] if candidates else None


def _source_location_options(
    product: dict[str, Any],
    destination_branch: str,
    required_qty: int,
    location_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in product.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        location_id = str(entry.get("location_id") or "").strip()
        if not location_id or location_id in seen:
            continue
        seen.add(location_id)
        available_qty = _location_stock_for_product(product, location_id)
        if available_qty < required_qty:
            continue
        location = (location_map or {}).get(location_id)
        if location is None:
            location = inventory_locations_col.find_one({"_id": _safe_object_id(location_id)})
        if not location:
            continue
        if (location.get("status") or "").strip().lower() != "active":
            continue
        branch = (location.get("branch") or "").strip()
        if branch == destination_branch:
            continue
        name = (location.get("name") or "").strip() or "Warehouse"
        code = (location.get("code") or "").strip()
        label = f"{name} ({code})" if code and code.lower() != name.lower() else name
        candidates.append(
            {
                "id": str(location.get("_id") or ""),
                "branch": branch,
                "name": name,
                "code": code,
                "label": label,
                "availableQty": available_qty,
                "type": location.get("type") or "",
            }
        )
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("type") or "").strip().lower() == "warehouse" else 1,
            -int(row.get("availableQty") or 0),
            str(row.get("label") or ""),
        )
    )
    return candidates


def _serialize_item(
    line: dict[str, Any],
    product_map: dict[str, dict[str, Any]],
    location_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    product_id = str(line.get("product_id") or "")
    product = product_map.get(product_id) or {}
    qty = _safe_int(line.get("qty"))
    delivered = _safe_int(line.get("delivered_qty"))
    destination_branch = str(line.get("destination_branch") or "")
    return {
        "lineId": str(line.get("line_id") or ""),
        "productId": product_id,
        "name": line.get("name") or product.get("name") or "",
        "sku": line.get("sku") or product.get("sku") or "",
        "imageUrl": product.get("image_url") or "",
        "destinationLocationId": str(line.get("destination_location_id") or ""),
        "destinationLocationName": str(line.get("destination_location_name") or ""),
        "destinationLocationCode": str(line.get("destination_location_code") or ""),
        "destinationBranch": destination_branch,
        "sourceOptions": _source_location_options(product, destination_branch, max(0, qty - delivered), location_map),
        "requestedQty": qty,
        "deliveredQty": delivered,
        "remainingQty": max(0, qty - delivered),
        "rejectedQty": _safe_int(line.get("rejected_qty")),
        "status": line.get("status") or "pending",
        "notes": line.get("notes") or "",
        "decisionNote": line.get("decision_note") or "",
    }


def _serialize_request(
    order: dict[str, Any],
    manager_map: dict[str, dict[str, Any]],
    product_map: dict[str, dict[str, Any]],
    location_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    manager_id = str(order.get("manager_id") or "")
    manager = manager_map.get(manager_id) or {}
    inventory_lines = []
    for line in order.get("items") or []:
        if not _line_is_inventory_product(line):
            continue
        product_id = str(line.get("product_id") or "")
        source = str(line.get("source_collection") or "").strip()
        if source == "inventory_products" or product_id in product_map:
            inventory_lines.append(line)

    items = [_serialize_item(line, product_map, location_map) for line in inventory_lines]
    remaining_items = [item for item in items if item["remainingQty"] > 0]
    visible_items = [item for item in items if item["remainingQty"] > 0 or str(item.get("status") or "").lower() == "rejected"]
    total_remaining = sum(item["remainingQty"] for item in remaining_items)
    total_requested = sum(item["requestedQty"] for item in items)
    status = order.get("status") or ("closed" if total_remaining == 0 else "open")
    if status == "open" and total_remaining > 0:
        status = "pending"
    return {
        "id": str(order.get("_id") or ""),
        "branch": order.get("branch") or manager.get("branch") or "",
        "requestedBy": manager.get("name") or manager.get("username") or "Manager",
        "managerId": manager_id,
        "requestDate": _iso(order.get("created_at")),
        "updatedAt": _iso(order.get("updated_at")),
        "status": status,
        "priority": "high" if total_remaining >= 50 else "medium" if total_remaining >= 15 else "low",
        "reason": order.get("notes") or "Manager product request",
        "itemsCount": len(remaining_items),
        "totalQuantity": total_remaining,
        "requestedQuantity": total_requested,
        "items": visible_items,
    }


def list_branch_requests() -> list[dict[str, Any]]:
    orders = list(
        orders_col.find(
            {"items.0": {"$exists": True}},
            {"manager_id": 1, "branch": 1, "created_at": 1, "updated_at": 1, "status": 1, "notes": 1, "items": 1},
        ).sort([("updated_at", -1)]).limit(500)
    )
    manager_ids = [_safe_object_id(order.get("manager_id")) for order in orders if _safe_object_id(order.get("manager_id"))]
    manager_map = {
        str(doc["_id"]): doc
        for doc in users_col.find({"_id": {"$in": manager_ids}}, {"name": 1, "username": 1, "branch": 1})
    } if manager_ids else {}

    product_ids: set[ObjectId] = set()
    for order in orders:
        for line in order.get("items") or []:
            if not _line_is_inventory_product(line):
                continue
            oid = _safe_object_id(line.get("product_id"))
            if oid:
                product_ids.add(oid)
    product_map = {
        str(doc["_id"]): doc
        for doc in inventory_products_col.find(
            {"_id": {"$in": list(product_ids)}},
            {"name": 1, "sku": 1, "image_url": 1, "entries": 1},
        )
    } if product_ids else {}
    location_map = _prefetch_locations(product_map)

    rows = []
    for order in orders:
        row = _serialize_request(order, manager_map, product_map, location_map)
        if row["itemsCount"] > 0 or row["status"] in {"closed", "approved"}:
            rows.append(row)
    return rows


def approve_branch_request(
    order_id: str,
    actor: dict[str, Any],
    selections: dict[str, Any] | None = None,
    approvals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    oid = _safe_object_id(order_id)
    if oid is None:
        raise ValueError("Invalid request id.")

    order = orders_col.find_one({"_id": oid})
    if not order:
        raise ValueError("Branch request not found.")

    branch = (order.get("branch") or "").strip()
    line_source_map = selections or {}
    line_approval_map = approvals or {}

    now = datetime.utcnow()
    items = order.get("items") or []
    approved_lines = []
    for line in items:
        remaining = _remaining_qty(line)
        product_oid = _safe_object_id(line.get("product_id"))
        if remaining <= 0 or product_oid is None:
            continue
        product = inventory_products_col.find_one({"_id": product_oid})
        if not product:
            raise ValueError(f"{line.get('name') or 'One item'} no longer exists in inventory products.")
        destination_location_id = str(line.get("destination_location_id") or "").strip()
        destination_location = inventory_locations_col.find_one({"_id": _safe_object_id(destination_location_id)})
        if not destination_location:
            raise ValueError(
                f"Destination warehouse for {line.get('name') or product.get('name') or 'this item'} no longer exists."
            )
        if (destination_location.get("branch") or "").strip() != branch:
            raise ValueError("Destination warehouse must belong to the requesting branch.")

        line_id = str(line.get("line_id") or "")
        approval_payload = line_approval_map.get(line_id) if isinstance(line_approval_map, dict) else None
        approval_payload = approval_payload if isinstance(approval_payload, dict) else {}
        approve_qty = _safe_int(approval_payload.get("approvedQty"), remaining) if approval_payload else remaining
        remaining_action = str(approval_payload.get("remainingAction") or "postponed").strip().lower()
        if remaining_action not in {"postponed", "rejected"}:
            remaining_action = "postponed"
        decision_note = str(approval_payload.get("note") or "").strip()
        if approve_qty <= 0:
            continue
        if approve_qty > remaining:
            raise ValueError(
                f"Approved quantity for {line.get('name') or product.get('name') or 'this item'} cannot exceed the remaining {remaining} units."
            )

        source_location_id = str(
            approval_payload.get("sourceLocationId")
            or line_source_map.get(line_id)
            or ""
        ).strip()
        source_location = None
        if source_location_id:
            source_location = inventory_locations_col.find_one({"_id": _safe_object_id(source_location_id)})
            if not source_location:
                raise ValueError(
                    f"Selected source warehouse for {line.get('name') or product.get('name') or 'this item'} no longer exists."
                )
            if _location_stock_for_product(product, source_location_id) < approve_qty:
                raise ValueError(
                    f"Selected source warehouse for {line.get('name') or product.get('name') or 'this item'} "
                    f"does not have enough stock for {approve_qty} units."
                )
            if (source_location.get("status") or "").strip().lower() != "active":
                raise ValueError(
                    f"Selected source warehouse for {line.get('name') or product.get('name') or 'this item'} is inactive."
                )
            if (source_location.get("branch") or "").strip() == branch:
                raise ValueError(
                    f"Selected source warehouse for {line.get('name') or product.get('name') or 'this item'} "
                    "must be different from the destination branch."
                )
        if not source_location:
            raise ValueError(f"Select a source warehouse for {line.get('name') or product.get('name') or 'this item'} before approval.")

        prices = _latest_prices(product)
        source_entry = {
            "branch": (source_location.get("branch") or "").strip(),
            "location_id": str(source_location["_id"]),
            "location_name": (source_location.get("name") or "").strip(),
            "location_code": (source_location.get("code") or "").strip(),
            "quantity": -approve_qty,
            "expiry_date": "",
            "reminder_days": 0,
            "cost_price": prices["cost_price"] or 0,
            "selling_price": prices["selling_price"] or 0,
            "installment_price": prices["installment_price"],
            "wholesale_price": prices["wholesale_price"],
            "created_at": now,
            "updated_at": now,
            "source": "manager_branch_request_transfer_out",
            "order_id": oid,
            "line_id": line.get("line_id"),
        }
        destination_entry = {
            "branch": branch,
            "location_id": str(destination_location["_id"]),
            "location_name": (destination_location.get("name") or "").strip(),
            "location_code": (destination_location.get("code") or "").strip(),
            "quantity": approve_qty,
            "expiry_date": "",
            "reminder_days": 0,
            "cost_price": prices["cost_price"] or 0,
            "selling_price": prices["selling_price"] or 0,
            "installment_price": prices["installment_price"],
            "wholesale_price": prices["wholesale_price"],
            "created_at": now,
            "updated_at": now,
            "source": "manager_branch_request_transfer_in",
            "order_id": oid,
            "line_id": line.get("line_id"),
        }
        inventory_products_col.update_one(
            {"_id": product_oid},
            {"$push": {"entries": {"$each": [source_entry, destination_entry]}}, "$set": {"updated_at": now}},
        )
        product.setdefault("entries", []).extend([source_entry, destination_entry])
        inventory_locations_col.update_one(
            {"_id": source_location["_id"]},
            {"$inc": {"stock_units": -approve_qty}, "$set": {"updated_at": now}},
        )
        inventory_locations_col.update_one(
            {"_id": destination_location["_id"]},
            {"$inc": {"stock_units": approve_qty}, "$set": {"updated_at": now}},
        )

        line["delivered_qty"] = _safe_int(line.get("delivered_qty")) + approve_qty
        line_remaining = _remaining_qty(line)
        line["remaining_qty"] = line_remaining
        if line_remaining <= 0:
            line["status"] = "delivered"
            line["delivered_at"] = now
        else:
            if remaining_action == "rejected":
                line["rejected_qty"] = _safe_int(line.get("rejected_qty")) + line_remaining
                line["remaining_qty"] = 0
                line["status"] = "rejected"
                line["rejected_at"] = now
                line["decision_note"] = decision_note
                line.setdefault("rejections", [])
                line["rejections"].append(
                    {
                        "qty_rejected": line_remaining,
                        "approved_qty": approve_qty,
                        "note": decision_note,
                        "at": now,
                        "by": str(actor.get("user_id") or actor.get("_id") or ""),
                    }
                )
                line_remaining = 0
            else:
                line["status"] = "postponed"
                line["decision_note"] = decision_note
                line.setdefault("postponements", [])
                line["postponements"].append(
                    {
                        "qty_postponed": line_remaining,
                        "approved_qty": approve_qty,
                        "reason": "Partial warehouse approval",
                        "note": decision_note,
                        "at": now,
                        "by": str(actor.get("user_id") or actor.get("_id") or ""),
                    }
                )
        approved_lines.append(
            {
                "line_id": line.get("line_id"),
                "product_id": str(product_oid),
                "name": line.get("name") or product.get("name"),
                "qty": approve_qty,
                "requested_qty": remaining,
                "postponed_qty": line_remaining,
                "remaining_action": remaining_action,
                "note": decision_note,
                "from_location_name": source_location.get("name") or "",
                "to_location_name": destination_location.get("name") or "",
            }
        )

    if not approved_lines:
        raise ValueError("No pending inventory product lines remain on this request.")

    inventory_lines = [line for line in items if _line_is_inventory_product(line)]
    closed_count = sum(1 for line in inventory_lines if (line.get("status") or "") in {"delivered", "rejected"})
    status = "closed" if closed_count >= len(inventory_lines) else "partially_delivered"

    orders_col.update_one(
        {"_id": oid},
        {"$set": {"items": items, "status": status, "updated_at": now}},
    )
    order_events_col.insert_one(
        {
            "order_id": oid,
            "type": "branch_request_approved",
            "payload": {
                "branch": branch,
                "items": approved_lines,
            },
            "by": str(actor.get("user_id") or actor.get("_id") or ""),
            "role": actor.get("role") or "inventory",
            "at": now,
        }
    )

    from .product_cards_store import invalidate_product_card_cache

    invalidate_product_card_cache()
    return {"approvedCount": len(approved_lines), "status": status}


def delete_branch_request_line(order_id: str, line_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    oid = _safe_object_id(order_id)
    if oid is None:
        raise ValueError("Invalid request id.")
    order = orders_col.find_one({"_id": oid})
    if not order:
        raise ValueError("Branch request not found.")
    items = order.get("items") or []
    kept = [line for line in items if str(line.get("line_id") or "") != str(line_id)]
    if len(kept) == len(items):
        raise ValueError("Product line not found.")
    now = datetime.utcnow()
    status = "closed" if not kept else order.get("status") or "open"
    orders_col.update_one({"_id": oid}, {"$set": {"items": kept, "status": status, "updated_at": now}})
    order_events_col.insert_one(
        {
            "order_id": oid,
            "type": "branch_request_line_deleted",
            "payload": {"line_id": line_id},
            "by": str(actor.get("user_id") or actor.get("_id") or ""),
            "role": actor.get("role") or "inventory",
            "at": now,
        }
    )
    return {"deleted": True, "status": status}


def delete_branch_request(order_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    oid = _safe_object_id(order_id)
    if oid is None:
        raise ValueError("Invalid request id.")
    order = orders_col.find_one({"_id": oid})
    if not order:
        raise ValueError("Branch request not found.")
    now = datetime.utcnow()
    orders_col.update_one(
        {"_id": oid},
        {"$set": {"items": [], "manual_items": [], "status": "cancelled", "deleted_at": now, "updated_at": now}},
    )
    order_events_col.insert_one(
        {
            "order_id": oid,
            "type": "branch_request_deleted",
            "payload": {"request_id": order_id},
            "by": str(actor.get("user_id") or actor.get("_id") or ""),
            "role": actor.get("role") or "inventory",
            "at": now,
        }
    )
    return {"deleted": True, "status": "cancelled"}
