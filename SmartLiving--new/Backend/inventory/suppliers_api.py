from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from bson import ObjectId
from pymongo.errors import PyMongoError
from flask import Blueprint, jsonify, request, current_app

from db import db
from login import get_current_identity, role_required
from .purchase_funding import create_funded_order, funding_options
from .products_store import list_inventory_products
from .settings_store import get_branches_payload, inventory_locations_col


inventory_suppliers_api_bp = Blueprint("inventory_suppliers_api", __name__, url_prefix="/api/inventory")

inventory_suppliers_col = db["inventory_suppliers"]
inventory_purchase_orders_col = db["inventory_purchase_orders"]
inventory_procurement_requests_col = db["inventory_procurement_requests"]
inventory_cost_updates_col = db["inventory_cost_updates"]
supplier_deliveries_col = db["supplier_deliveries"]
inventory_products_col = db["inventory_products"]

_indexes_ready = False


def _ensure_indexes() -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    try:
        inventory_suppliers_col.create_index([("name_key", 1)], unique=True)
        inventory_suppliers_col.create_index([("code", 1)], unique=True)
        inventory_purchase_orders_col.create_index([("po_number", 1)], unique=True)
        inventory_purchase_orders_col.create_index([("status", 1), ("expected_delivery", 1)])
        inventory_procurement_requests_col.create_index([("request_number", 1)], unique=True)
        inventory_procurement_requests_col.create_index([("status", 1), ("created_at", -1)])
        inventory_cost_updates_col.create_index([("update_number", 1)], unique=True)
        inventory_cost_updates_col.create_index([("effective_date", -1)])
        supplier_deliveries_col.create_index([("po_id", 1)])
    except Exception:
        pass
    _indexes_ready = True


def _safe_object_id(value: str | None) -> ObjectId | None:
    if value and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


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


def _supplier_name_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _parse_dateish(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _display_date(value: Any) -> str:
    parsed = _parse_dateish(value)
    if parsed is None:
        return "-"
    return parsed.strftime("%Y-%m-%d")


def _next_sequence(collection, field: str, prefix: str) -> str:
    pattern = f"^{re.escape(prefix)}"
    highest = 0
    for row in collection.find({field: {"$regex": pattern}}, {field: 1}):
        current = str(row.get(field) or "")
        try:
            highest = max(highest, int(current.split("-")[-1]))
        except Exception:
            continue
    return f"{prefix}{highest + 1:05d}"


def _next_supplier_code() -> str:
    highest = 0
    for row in inventory_suppliers_col.find({}, {"code": 1}):
        code = str(row.get("code") or "")
        if code.startswith("SUP-"):
            try:
                highest = max(highest, int(code.split("-")[-1]))
            except Exception:
                continue
    return f"SUP-{highest + 1:03d}"


def _product_lookup() -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for product in list_inventory_products():
        lookup[str(product.get("id") or "")] = product
    return lookup


def _serialize_product_option(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(product.get("id") or ""),
        "sku": product.get("sku") or "",
        "name": product.get("name") or "",
        "category": product.get("category") or "",
        "brand": product.get("brand") or "",
        "image": product.get("image") or product.get("image_url") or "",
        "unitCost": _safe_float(product.get("unitCost")),
        "available": _safe_int(product.get("available")),
    }


def _identity_summary(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": identity.get("user_id"),
        "name": identity.get("name"),
        "role": identity.get("role"),
        "username": identity.get("username"),
    }


def _serialize_supplier_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("code") or f"SUP-{str(doc.get('_id') or '')[-3:]}",
        "name": (doc.get("name") or "").strip(),
        "contact": (doc.get("contact") or "").strip(),
        "phone": (doc.get("phone") or "").strip(),
        "email": (doc.get("email") or "").strip(),
        "location": (doc.get("location") or "").strip(),
        "notes": (doc.get("notes") or "").strip(),
        "status": "inactive" if str(doc.get("status") or "").strip().lower() == "inactive" else "active",
    }


def _normalize_request_items(raw_items: Any, products_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        raise ValueError("Add at least one item.")

    normalized: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        product_id = str(raw_item.get("productId") or "").strip()
        quantity = _safe_int(raw_item.get("quantity"))
        if not product_id or quantity <= 0:
            continue
        product = products_by_id.get(product_id)
        if not product:
            raise ValueError("One of the selected inventory items no longer exists.")
        normalized.append(
            {
                "product_id": product_id,
                "product_name": product.get("name") or "",
                "sku": product.get("sku") or "",
                "quantity": quantity,
                "unit_cost": _safe_float(raw_item.get("unitCost"), _safe_float(product.get("unitCost"))),
            }
        )

    if not normalized:
        raise ValueError("Add at least one valid item.")
    return normalized


def _serialize_procurement_request(doc: dict[str, Any]) -> dict[str, Any]:
    items = doc.get("items") or []
    return {
        "id": str(doc.get("_id") or ""),
        "requestNumber": doc.get("request_number") or "",
        "supplierId": doc.get("supplier_id") or "",
        "supplier": doc.get("supplier_name") or "",
        "requestedBy": ((doc.get("requested_by") or {}).get("name") or "-"),
        "purpose": doc.get("purpose") or "",
        "notes": doc.get("notes") or "",
        "status": doc.get("status") or "pending",
        "createdAt": _display_date(doc.get("created_at")),
        "approvedBy": ((doc.get("approved_by") or {}).get("name") or ""),
        "rejectedBy": ((doc.get("rejected_by") or {}).get("name") or ""),
        "purchaseOrderNumber": doc.get("purchase_order_number") or "",
        "items": [
            {
                "productId": item.get("product_id") or "",
                "product": item.get("product_name") or "",
                "sku": item.get("sku") or "",
                "quantity": _safe_int(item.get("quantity")),
                "unitCost": _safe_float(item.get("unit_cost")),
            }
            for item in items
        ],
    }


def _serialize_purchase_order(doc: dict[str, Any]) -> dict[str, Any]:
    items = doc.get("items") or []
    ordered_qty = sum(_safe_int(item.get("quantity_ordered")) for item in items)
    received_qty = sum(_safe_int(item.get("quantity_received")) for item in items)
    total_cost = sum(_safe_int(item.get("quantity_ordered")) * _safe_float(item.get("unit_cost")) for item in items)
    return {
        "id": str(doc.get("_id") or ""),
        "poNumber": doc.get("po_number") or "",
        "supplierId": doc.get("supplier_id") or "",
        "supplier": doc.get("supplier_name") or "",
        "expectedDelivery": doc.get("expected_delivery") or "",
        "status": doc.get("status") or "draft",
        "trigger": doc.get("trigger") or "",
        "notes": doc.get("notes") or "",
        "createdAt": _display_date(doc.get("created_at")),
        "createdBy": ((doc.get("created_by") or {}).get("name") or "-"),
        "approvedBy": ((doc.get("approved_by") or {}).get("name") or ""),
        "sentBy": ((doc.get("sent_by") or {}).get("name") or ""),
        "receivedQty": received_qty,
        "itemsCount": len(items),
        "totalQuantity": ordered_qty,
        "expectedCost": doc.get("total_amount", round(total_cost, 2)),
        "funding": doc.get("funding"),
        "branch": doc.get("branch") or "",
        "locationId": doc.get("location_id") or "",
        "procurementRequestNumber": doc.get("procurement_request_number") or "",
        "items": [
            {
                "productId": item.get("product_id") or "",
                "product": item.get("product_name") or "",
                "sku": item.get("sku") or "",
                "quantityOrdered": _safe_int(item.get("quantity_ordered")),
                "quantityReceived": _safe_int(item.get("quantity_received")),
                "quantityRejected": _safe_int(item.get("quantity_rejected")),
                "unitCost": _safe_float(item.get("unit_cost")),
                "lineTotal": round(_safe_int(item.get("quantity_ordered")) * _safe_float(item.get("unit_cost")), 2),
            }
            for item in items
        ],
        "receipts": [
            {
                "receivedAt": receipt.get("received_at") or "",
                "receivedBy": ((receipt.get("received_by") or {}).get("name") or "-"),
                "deliveryNoteNo": receipt.get("delivery_note_no") or "",
                "locationName": receipt.get("location_name") or "",
                "locations": receipt.get("locations") or [],
                "comment": receipt.get("comment") or "",
                "items": receipt.get("items") or [],
            }
            for receipt in (doc.get("receipts") or [])
        ],
    }


def _serialize_cost_update(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or ""),
        "updateNumber": doc.get("update_number") or "",
        "productId": doc.get("product_id") or "",
        "product": doc.get("product_name") or "",
        "sku": doc.get("sku") or "",
        "supplierId": doc.get("supplier_id") or "",
        "supplier": doc.get("supplier_name") or "",
        "oldCost": _safe_float(doc.get("old_cost")),
        "newCost": _safe_float(doc.get("new_cost")),
        "reason": doc.get("reason") or "",
        "effectiveDate": doc.get("effective_date") or "",
        "changedBy": ((doc.get("changed_by") or {}).get("name") or "-"),
        "createdAt": _display_date(doc.get("created_at")),
    }


def _delivery_record(doc: dict[str, Any]) -> dict[str, Any]:
    items = doc.get("items") or []
    line_items: list[dict[str, Any]] = []
    discrepancy_detected = False

    for item in items:
        expected_qty = _safe_int(item.get("qty_requested"))
        received_qty = _safe_int(item.get("qty_delivered_total"))
        damaged_qty = _safe_int(item.get("qty_rejected_total"))
        variance = max(expected_qty - received_qty, 0)
        if variance > 0 or damaged_qty > 0:
            discrepancy_detected = True
        line_items.append(
            {
                "productId": item.get("product_id") or "",
                "product": item.get("product_name_snapshot") or "Unknown item",
                "sku": item.get("sku") or item.get("product_id") or "",
                "expectedQty": expected_qty,
                "receivedQty": received_qty,
                "damagedQty": damaged_qty,
                "variance": variance,
                "unitCost": _safe_float(item.get("unit_cost")),
                "status": item.get("status") or "",
            }
        )

    total_expected = sum(item["expectedQty"] for item in line_items)
    total_received = sum(item["receivedQty"] for item in line_items)
    total_damaged = sum(item["damagedQty"] for item in line_items)

    status = "partial"
    if line_items and total_received >= total_expected and total_damaged == 0:
        status = "complete"
    elif discrepancy_detected:
        status = "discrepancy"

    receipts = doc.get("receipts") or []
    latest_receipt = receipts[-1] if receipts else {}
    received_by = (latest_receipt.get("received_by") or {}).get("name") or (doc.get("created_by") or {}).get("name") or "-"
    received_date = latest_receipt.get("received_at") or doc.get("expected_date") or doc.get("created_date") or doc.get("created_at")

    return {
        "id": doc.get("ref_no") or str(doc.get("_id") or ""),
        "poId": doc.get("po_id") or "",
        "linkedType": "po",
        "linkedRef": doc.get("ref_no") or str(doc.get("_id") or ""),
        "supplier": ((doc.get("supplier") or {}).get("name") or "").strip(),
        "receivedBy": received_by,
        "receivedDate": _display_date(received_date),
        "status": status,
        "lineItems": line_items,
        "auditTriggered": discrepancy_detected,
        "auditRef": f"AUD-{(doc.get('ref_no') or 'SUP').split('-')[-1]}" if discrepancy_detected else None,
        "notes": doc.get("notes") or "",
        "receipts": [
            {
                "receivedAt": receipt.get("received_at") or "",
                "receivedBy": ((receipt.get("received_by") or {}).get("name") or "-"),
                "deliveryNoteNo": receipt.get("delivery_note_no") or "",
                "locationName": receipt.get("location_name") or "",
                "locations": receipt.get("locations") or [],
                "comment": receipt.get("comment") or "",
                "items": receipt.get("items") or [],
            }
            for receipt in receipts
        ],
        "createdAt": _display_date(doc.get("created_at") or received_date),
    }


def _build_suppliers_payload() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    suppliers_by_key: dict[str, dict[str, Any]] = {}
    for doc in inventory_suppliers_col.find({}).sort("name", 1):
        serialized = _serialize_supplier_doc(doc)
        suppliers_by_key[_supplier_name_key(serialized["name"])] = {
            **serialized,
            "totalDeliveries": 0,
            "lastDelivery": "-",
            "totalSupplied": 0.0,
            "avgCostTrend": 0.0,
            "recentDeliveries": [],
            "_lastDeliveryDt": None,
            "_costSamples": [],
        }

    grn_records: list[dict[str, Any]] = []
    for doc in supplier_deliveries_col.find({}).sort("created_at", -1):
        record = _delivery_record(doc)
        grn_records.append(record)
        supplier_name = record.get("supplier") or "Unknown supplier"
        key = _supplier_name_key(supplier_name)
        supplier = suppliers_by_key.setdefault(
            key,
            {
                "id": f"SUP-AUTO-{len(suppliers_by_key) + 1:03d}",
                "name": supplier_name,
                "contact": "",
                "phone": ((doc.get("supplier") or {}).get("phone") or "").strip(),
                "email": "",
                "location": ((doc.get("supplier") or {}).get("location") or "").strip(),
                "notes": "",
                "status": "active",
                "totalDeliveries": 0,
                "lastDelivery": "-",
                "totalSupplied": 0.0,
                "avgCostTrend": 0.0,
                "recentDeliveries": [],
                "_lastDeliveryDt": None,
                "_costSamples": [],
            },
        )
        supplier["totalDeliveries"] += 1
        delivery_dt = _parse_dateish(record.get("receivedDate"))
        if delivery_dt and (supplier["_lastDeliveryDt"] is None or delivery_dt > supplier["_lastDeliveryDt"]):
            supplier["_lastDeliveryDt"] = delivery_dt
            supplier["lastDelivery"] = record.get("receivedDate") or "-"

        delivered_value = 0.0
        delivered_units = 0
        for item in record.get("lineItems") or []:
            qty = _safe_int(item.get("receivedQty"))
            unit_cost = _safe_float(item.get("unitCost"))
            delivered_units += qty
            delivered_value += qty * unit_cost
        supplier["totalSupplied"] += delivered_value
        if delivered_units > 0:
            supplier["_costSamples"].append(delivered_value / delivered_units)

        supplier["recentDeliveries"].append(
            {
                "id": record.get("id") or "-",
                "item": ((record.get("lineItems") or [{}])[0].get("product") or "-"),
                "qty": sum(_safe_int(item.get("receivedQty")) for item in (record.get("lineItems") or [])),
                "date": record.get("receivedDate") or "-",
            }
        )

    suppliers = list(suppliers_by_key.values())
    for supplier in suppliers:
        samples = supplier.pop("_costSamples", [])
        supplier.pop("_lastDeliveryDt", None)
        if len(samples) >= 2 and samples[0] > 0:
            supplier["avgCostTrend"] = round(((samples[-1] - samples[0]) / samples[0]) * 100, 1)
        else:
            supplier["avgCostTrend"] = 0.0
        supplier["totalSupplied"] = round(_safe_float(supplier.get("totalSupplied")), 2)
        supplier["recentDeliveries"] = sorted(
            supplier.get("recentDeliveries") or [],
            key=lambda item: _parse_dateish(item.get("date")) or datetime.min,
            reverse=True,
        )[:5]

    suppliers.sort(key=lambda row: ((row.get("name") or "").lower(), row.get("id") or ""))
    grn_records.sort(key=lambda row: _parse_dateish(row.get("receivedDate")) or datetime.min, reverse=True)
    return suppliers, grn_records


def _build_pending_deliveries(purchase_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending_rows: list[dict[str, Any]] = []
    today = datetime.utcnow().date()
    for po in purchase_orders:
        if po.get("status") not in {"approved", "sent", "partial"}:
            continue
        pending_qty = max(_safe_int(po.get("totalQuantity")) - _safe_int(po.get("receivedQty")), 0)
        expected_date = po.get("expectedDelivery") or ""
        delay_days = 0
        expected_dt = _parse_dateish(expected_date)
        if expected_dt:
            delay_days = max((today - expected_dt.date()).days, 0)
        pending_rows.append(
            {
                "id": po.get("id") or "",
                "poNumber": po.get("poNumber") or "",
                "supplier": po.get("supplier") or "",
                "itemsPending": max(_safe_int(po.get("itemsCount")), 0),
                "expected": _safe_int(po.get("totalQuantity")),
                "received": _safe_int(po.get("receivedQty")),
                "pending": pending_qty,
                "expectedDate": expected_date,
                "delayDays": delay_days,
                "status": "delayed" if delay_days > 0 and pending_qty > 0 else ("partial" if _safe_int(po.get("receivedQty")) > 0 else "on-track"),
            }
        )
    pending_rows.sort(key=lambda row: (_parse_dateish(row.get("expectedDate")) or datetime.max, row.get("poNumber") or ""))
    return pending_rows


def _default_branch_and_location() -> tuple[str, dict[str, Any] | None]:
    payload = get_branches_payload()
    branches = payload.get("branches") or []
    locations = payload.get("locations") or {}
    for branch in branches:
        branch_name = branch.get("name") or ""
        branch_locations = locations.get(branch_name) or []
        active_location = next((row for row in branch_locations if row.get("status") == "active"), None)
        if branch_name and active_location:
            return branch_name, active_location
    return "", None


def _append_inventory_receipt_entry(product_id: str, location_doc: dict[str, Any], quantity: int, unit_cost: float, identity: dict[str, Any], source: str) -> None:
    if quantity <= 0:
        return
    product_oid = _safe_object_id(product_id)
    location_oid = _safe_object_id(str(location_doc.get("id") or location_doc.get("_id") or ""))
    if product_oid is None or location_oid is None:
        return

    product_doc = inventory_products_col.find_one({"_id": product_oid})
    if not product_doc:
        return

    entries = product_doc.get("entries") or []
    latest = None
    if entries:
        latest = max(entries, key=lambda item: _parse_dateish(item.get("updated_at") or item.get("created_at")) or datetime.min)
    now = datetime.utcnow()
    new_entry = {
        "branch": location_doc.get("branchId") or location_doc.get("branch") or "",
        "location_id": str(location_oid),
        "location_name": location_doc.get("name") or "",
        "location_code": location_doc.get("code") or "",
        "quantity": quantity,
        "expiry_date": (latest or {}).get("expiry_date") or "",
        "reminder_days": _safe_int((latest or {}).get("reminder_days"), 0),
        "cost_price": unit_cost,
        "selling_price": _safe_float((latest or {}).get("selling_price"), unit_cost),
        "installment_price": (latest or {}).get("installment_price"),
        "wholesale_price": (latest or {}).get("wholesale_price"),
        "source": source,
        "order_id": "",
        "line_id": "",
        "created_at": now,
        "updated_at": now,
        "created_by": _identity_summary(identity),
    }
    inventory_products_col.update_one(
        {"_id": product_oid},
        {
            "$push": {"entries": new_entry},
            "$set": {"updated_at": now},
        },
    )
    inventory_locations_col.update_one(
        {"_id": location_oid},
        {"$inc": {"stock_units": quantity}, "$set": {"updated_at": now}},
    )


def _sync_delivery_doc_from_po(po_doc: dict[str, Any], session=None) -> None:
    session_args = {"session": session} if session is not None else {}
    po_id = str(po_doc.get("_id") or "")
    ref_no = po_doc.get("po_number") or ""
    existing = supplier_deliveries_col.find_one(
        {
            "$or": [
                {"po_id": po_id},
                {"ref_no": ref_no},
            ]
        }, **session_args
    )
    items = []
    for line in po_doc.get("items") or []:
        quantity_ordered = _safe_int(line.get("quantity_ordered"))
        quantity_received = _safe_int(line.get("quantity_received"))
        quantity_rejected = _safe_int(line.get("quantity_rejected"))
        items.append(
            {
                "product_id": line.get("product_id") or "",
                "inventory_id": line.get("product_id") or "",
                "product_name_snapshot": line.get("product_name") or "",
                "product_image_snapshot": "",
                "sku": line.get("sku") or "",
                "unit_cost": _safe_float(line.get("unit_cost")),
                "qty_requested": quantity_ordered,
                "qty_delivered_total": quantity_received,
                "qty_rejected_total": quantity_rejected,
                "qty_missing": max(quantity_ordered - quantity_received - quantity_rejected, 0),
                "status": line.get("status") or "Not Delivered",
                "item_note": line.get("item_note") or "",
            }
        )

    supplier_block = {
        "name": po_doc.get("supplier_name") or "",
        "phone": po_doc.get("supplier_phone") or "",
        "location": po_doc.get("supplier_location") or "",
    }
    update_doc = {
        "ref_no": ref_no,
        "po_id": po_id,
        "supplier": supplier_block,
        "status": "completed" if po_doc.get("status") == "completed" else ("partial" if po_doc.get("status") == "partial" else "pending"),
        "created_at": po_doc.get("created_at") or datetime.utcnow(),
        "created_date": _display_date(po_doc.get("created_at")),
        "created_by": po_doc.get("created_by") or {},
        "expected_date": po_doc.get("expected_delivery") or "",
        "notes": po_doc.get("notes") or "",
        "branch": po_doc.get("branch") or "",
        "items": items,
        "receipts": po_doc.get("receipts") or [],
        "updated_at": datetime.utcnow(),
    }
    if existing:
        supplier_deliveries_col.update_one({"_id": existing["_id"]}, {"$set": update_doc}, **session_args)
    else:
        supplier_deliveries_col.insert_one(update_doc, **session_args)


def _bootstrap_payload() -> dict[str, Any]:
    # Keep bootstrap lightweight: only form options/settings. Tab records are
    # fetched independently in pages of ten from /suppliers/tab/<tab_id>.
    supplier_options = [_serialize_supplier_doc(doc) for doc in inventory_suppliers_col.find({}).sort("name", 1)]
    receivable_purchase_orders = [
        _serialize_purchase_order(doc)
        for doc in inventory_purchase_orders_col.find({"status": {"$in": ["approved", "sent", "partial"]}}).sort("created_at", -1).limit(50)
    ]
    inventory_products = [_serialize_product_option(product) for product in list_inventory_products()]
    branches_payload = get_branches_payload()
    all_pending = _build_pending_deliveries([
        _serialize_purchase_order(doc)
        for doc in inventory_purchase_orders_col.find({"status": {"$in": ["approved", "sent", "partial"]}})
    ])
    return {
        "ok": True,
        "supplierOptions": supplier_options,
        "receivablePurchaseOrders": receivable_purchase_orders,
        "inventoryProducts": inventory_products,
        "branches": branches_payload.get("branches") or [],
        "locations": branches_payload.get("locations") or {},
        "counts": {
            "suppliers": inventory_suppliers_col.count_documents({}),
            "procurement-requests": inventory_procurement_requests_col.count_documents({}),
            "purchase-orders": inventory_purchase_orders_col.count_documents({}),
            "supplier-deliveries": supplier_deliveries_col.count_documents({}),
            "pending-deliveries": len(all_pending),
            "cost-updates": inventory_cost_updates_col.count_documents({}),
        },
    }


def _tab_records(tab_id: str) -> list[dict[str, Any]]:
    if tab_id == "suppliers":
        suppliers, _ = _build_suppliers_payload()
        return suppliers
    if tab_id == "procurement-requests":
        return [_serialize_procurement_request(doc) for doc in inventory_procurement_requests_col.find({}).sort("created_at", -1)]
    if tab_id == "purchase-orders":
        return [_serialize_purchase_order(doc) for doc in inventory_purchase_orders_col.find({}).sort("created_at", -1)]
    if tab_id == "supplier-deliveries":
        return [_delivery_record(doc) for doc in supplier_deliveries_col.find({}).sort("created_at", -1)]
    if tab_id == "pending-deliveries":
        purchase_orders = [_serialize_purchase_order(doc) for doc in inventory_purchase_orders_col.find({"status": {"$in": ["approved", "sent", "partial"]}}).sort("created_at", -1)]
        return _build_pending_deliveries(purchase_orders)
    if tab_id == "cost-updates":
        return [_serialize_cost_update(doc) for doc in inventory_cost_updates_col.find({}).sort([("effective_date", -1), ("created_at", -1)])]
    raise ValueError("Unknown supplier tab.")


@inventory_suppliers_api_bp.route("/suppliers/tab/<tab_id>", methods=["GET"])
@role_required("inventory")
def suppliers_tab_page(tab_id: str):
    _ensure_indexes()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    query = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "all").strip().lower()
    direct = {
        'procurement-requests': (inventory_procurement_requests_col, _serialize_procurement_request, [('created_at', -1), ('_id', -1)], 'pending'),
        'purchase-orders': (inventory_purchase_orders_col, _serialize_purchase_order, [('created_at', -1), ('_id', -1)], 'draft'),
        'cost-updates': (inventory_cost_updates_col, _serialize_cost_update, [('effective_date', -1), ('created_at', -1), ('_id', -1)], ''),
    }
    if tab_id in direct and not query:
        collection, serialize, ordering, default_status = direct[tab_id]
        match = {}
        if status != 'all':
            if tab_id == 'cost-updates':
                match = {'_id': {'$in': []}}
            else:
                match = {'$expr': {'$eq': [{'$toLower': {'$cond': [{'$in': [{'$ifNull': ['$status', '']}, ['', None]]}, default_status, '$status']}}, status]}}
        total = collection.count_documents(match)
        offset = (page - 1) * 10
        rows = [serialize(doc) for doc in collection.find(match).sort(ordering).skip(offset).limit(10)]
        return jsonify(ok=True, tab=tab_id, rows=rows, page=page, perPage=10, total=total, hasMore=offset + len(rows) < total)
    try:
        records = _tab_records(tab_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404

    def searchable_text(row: dict[str, Any]) -> str:
        # Stringifying also covers nested PO/receipt line items, allowing product
        # names and SKUs to be searched without returning the full tab first.
        return str(row).lower()

    if status != "all":
        records = [row for row in records if str(row.get("status") or "").lower() == status]
    if query:
        records = [row for row in records if query in searchable_text(row)]

    per_page = 10
    total = len(records)
    offset = (page - 1) * per_page
    page_rows = records[offset:offset + per_page]
    return jsonify({
        "ok": True,
        "tab": tab_id,
        "rows": page_rows,
        "page": page,
        "perPage": per_page,
        "total": total,
        "hasMore": offset + len(page_rows) < total,
    })


@inventory_suppliers_api_bp.route("/suppliers/bootstrap", methods=["GET"])
@role_required("inventory")
def suppliers_bootstrap():
    _ensure_indexes()
    return jsonify(_bootstrap_payload())


@inventory_suppliers_api_bp.route("/suppliers", methods=["GET"])
@role_required("inventory")
def list_inventory_suppliers():
    _ensure_indexes()
    suppliers, grn_records = _build_suppliers_payload()
    return jsonify({"ok": True, "suppliers": suppliers, "grnRecords": grn_records})


@inventory_suppliers_api_bp.route("/suppliers", methods=["POST"])
@role_required("inventory")
def create_inventory_supplier():
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    contact = (payload.get("contact") or "").strip()
    phone = (payload.get("phone") or "").strip()
    email = (payload.get("email") or "").strip()
    location = (payload.get("location") or "").strip()
    notes = (payload.get("notes") or "").strip()
    status = "inactive" if str(payload.get("status") or "").strip().lower() == "inactive" else "active"

    if not name:
        return jsonify({"ok": False, "error": "Supplier name is required."}), 400

    name_key = _supplier_name_key(name)
    if inventory_suppliers_col.find_one({"name_key": name_key}) or supplier_deliveries_col.find_one({"supplier.name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}):
        return jsonify({"ok": False, "error": "A supplier with that name already exists."}), 409

    now = datetime.utcnow()
    doc = {
        "code": _next_supplier_code(),
        "name": name,
        "name_key": name_key,
        "contact": contact,
        "phone": phone,
        "email": email,
        "location": location,
        "notes": notes,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    inventory_suppliers_col.insert_one(doc)
    return jsonify({"ok": True, "supplier": _serialize_supplier_doc(doc)}), 201


@inventory_suppliers_api_bp.route("/suppliers/<supplier_id>", methods=["PATCH"])
@role_required("inventory")
def update_inventory_supplier(supplier_id: str):
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    current_name = (payload.get("currentName") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "Supplier name is required."}), 400

    supplier = inventory_suppliers_col.find_one({"code": supplier_id})
    if not supplier and _safe_object_id(supplier_id):
        supplier = inventory_suppliers_col.find_one({"_id": _safe_object_id(supplier_id)})

    old_name = (supplier or {}).get("name") or current_name
    old_key = _supplier_name_key(old_name)
    new_key = _supplier_name_key(name)

    if not old_name:
        return jsonify({"ok": False, "error": "Original supplier name is required."}), 400

    duplicate_supplier = inventory_suppliers_col.find_one({"name_key": new_key})
    if duplicate_supplier and (not supplier or duplicate_supplier.get("_id") != supplier.get("_id")):
        return jsonify({"ok": False, "error": "A supplier with that name already exists."}), 409

    duplicate_delivery = supplier_deliveries_col.find_one(
        {"supplier.name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}},
        {"supplier.name": 1},
    )
    if duplicate_delivery and _supplier_name_key((duplicate_delivery.get("supplier") or {}).get("name") or "") != old_key:
        return jsonify({"ok": False, "error": "A supplier with that name already exists in receiving records."}), 409

    now = datetime.utcnow()
    if supplier:
        inventory_suppliers_col.update_one(
            {"_id": supplier["_id"]},
            {"$set": {"name": name, "name_key": new_key, "updated_at": now}},
        )
    else:
        inventory_suppliers_col.insert_one(
            {
                "code": _next_supplier_code(),
                "name": name,
                "name_key": new_key,
                "contact": (payload.get("contact") or "").strip(),
                "phone": (payload.get("phone") or "").strip(),
                "email": (payload.get("email") or "").strip(),
                "location": (payload.get("location") or "").strip(),
                "notes": (payload.get("notes") or "").strip(),
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )

    supplier_deliveries_col.update_many(
        {"supplier.name": {"$regex": f"^{re.escape(old_name)}$", "$options": "i"}},
        {"$set": {"supplier.name": name, "updated_at": now}},
    )
    inventory_purchase_orders_col.update_many(
        {"supplier_name": {"$regex": f"^{re.escape(old_name)}$", "$options": "i"}},
        {"$set": {"supplier_name": name, "updated_at": now}},
    )
    inventory_procurement_requests_col.update_many(
        {"supplier_name": {"$regex": f"^{re.escape(old_name)}$", "$options": "i"}},
        {"$set": {"supplier_name": name, "updated_at": now}},
    )
    inventory_cost_updates_col.update_many(
        {"supplier_name": {"$regex": f"^{re.escape(old_name)}$", "$options": "i"}},
        {"$set": {"supplier_name": name, "updated_at": now}},
    )

    suppliers, grn_records = _build_suppliers_payload()
    updated = next((row for row in suppliers if _supplier_name_key(row.get("name") or "") == new_key), None)
    return jsonify({"ok": True, "supplier": updated, "suppliers": suppliers, "grnRecords": grn_records})


@inventory_suppliers_api_bp.route("/suppliers/procurement-requests", methods=["POST"])
@role_required("inventory")
def create_procurement_request():
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    identity = get_current_identity()
    products_by_id = _product_lookup()

    supplier_id = str(payload.get("supplierId") or "").strip()
    supplier_name = (payload.get("supplier") or "").strip()
    purpose = (payload.get("purpose") or "").strip()
    notes = (payload.get("notes") or "").strip()
    items = _normalize_request_items(payload.get("items"), products_by_id)

    if not supplier_name:
        return jsonify({"ok": False, "error": "Supplier is required."}), 400
    if not purpose:
        return jsonify({"ok": False, "error": "Purpose is required."}), 400

    now = datetime.utcnow()
    request_number = _next_sequence(inventory_procurement_requests_col, "request_number", f"PR-{now.year}-")
    doc = {
        "request_number": request_number,
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "purpose": purpose,
        "notes": notes,
        "status": "pending",
        "items": items,
        "requested_by": _identity_summary(identity),
        "created_at": now,
        "updated_at": now,
    }
    inventory_procurement_requests_col.insert_one(doc)
    doc["_id"] = doc.get("_id") or ""
    created = inventory_procurement_requests_col.find_one({"request_number": request_number}) or doc
    return jsonify({"ok": True, "procurementRequest": _serialize_procurement_request(created)}), 201


@inventory_suppliers_api_bp.route("/suppliers/procurement-requests/<request_id>/action", methods=["POST"])
@role_required("inventory")
def action_procurement_request(request_id: str):
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    identity = get_current_identity()
    oid = _safe_object_id(request_id)
    if oid is None:
        return jsonify({"ok": False, "error": "Invalid procurement request."}), 400

    doc = inventory_procurement_requests_col.find_one({"_id": oid})
    if not doc:
        return jsonify({"ok": False, "error": "Procurement request not found."}), 404

    now = datetime.utcnow()
    if action == "approve":
        inventory_procurement_requests_col.update_one(
            {"_id": oid},
            {"$set": {"status": "approved", "approved_by": _identity_summary(identity), "updated_at": now}},
        )
    elif action == "reject":
        inventory_procurement_requests_col.update_one(
            {"_id": oid},
            {"$set": {"status": "rejected", "rejected_by": _identity_summary(identity), "updated_at": now, "rejection_note": (payload.get("note") or "").strip()}},
        )
    elif action == "convert":
        if (doc.get("status") or "") not in {"approved", "pending"}:
            return jsonify({"ok": False, "error": "Only pending or approved requests can be converted."}), 400
        expected_delivery = (payload.get("expectedDelivery") or "").strip() or _display_date(now)
        trigger = (payload.get("trigger") or "Converted from procurement request").strip()
        po_number = _next_sequence(inventory_purchase_orders_col, "po_number", f"PO-{now.year}-")
        supplier_doc = inventory_suppliers_col.find_one({"name_key": _supplier_name_key(doc.get("supplier_name") or "")}) or {}
        po_items = []
        for item in doc.get("items") or []:
            po_items.append(
                {
                    "product_id": item.get("product_id") or "",
                    "product_name": item.get("product_name") or "",
                    "sku": item.get("sku") or "",
                    "quantity_ordered": _safe_int(item.get("quantity")),
                    "quantity_received": 0,
                    "quantity_rejected": 0,
                    "unit_cost": _safe_float(item.get("unit_cost")),
                    "status": "Not Delivered",
                }
            )
        branch_name, location_doc = _default_branch_and_location()
        po_doc = {
            "po_number": po_number,
            "supplier_id": supplier_doc.get("code") or doc.get("supplier_id") or "",
            "supplier_name": doc.get("supplier_name") or "",
            "supplier_phone": supplier_doc.get("phone") or "",
            "supplier_location": supplier_doc.get("location") or "",
            "expected_delivery": expected_delivery,
            "status": "approved",
            "trigger": trigger,
            "notes": doc.get("notes") or "",
            "branch": branch_name,
            "location_id": (location_doc or {}).get("id") or "",
            "procurement_request_id": str(doc.get("_id") or ""),
            "procurement_request_number": doc.get("request_number") or "",
            "items": po_items,
            "created_by": _identity_summary(identity),
            "approved_by": _identity_summary(identity),
            "created_at": now,
            "updated_at": now,
            "receipts": [],
        }
        insert_result = inventory_purchase_orders_col.insert_one(po_doc)
        po_doc["_id"] = insert_result.inserted_id
        _sync_delivery_doc_from_po(po_doc)
        inventory_procurement_requests_col.update_one(
            {"_id": oid},
            {"$set": {"status": "converted-to-po", "purchase_order_id": str(insert_result.inserted_id), "purchase_order_number": po_number, "updated_at": now}},
        )
    else:
        return jsonify({"ok": False, "error": "Unsupported action."}), 400

    updated = inventory_procurement_requests_col.find_one({"_id": oid})
    return jsonify({"ok": True, "procurementRequest": _serialize_procurement_request(updated or doc)})


@inventory_suppliers_api_bp.route("/suppliers/funding-options", methods=["GET"])
@role_required("inventory")
def purchase_funding_options():
    return jsonify({"ok": True, **funding_options()})


@inventory_suppliers_api_bp.route("/suppliers/purchase-orders", methods=["POST"])
@role_required("inventory")
def create_purchase_order():
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    identity = get_current_identity()
    products_by_id = _product_lookup()

    supplier_id = str(payload.get("supplierId") or "").strip()
    supplier_name = (payload.get("supplier") or "").strip()
    expected_delivery = (payload.get("expectedDelivery") or "").strip()
    trigger = (payload.get("trigger") or "").strip()
    notes = (payload.get("notes") or "").strip()
    status = str(payload.get("status") or "draft").strip().lower()
    try:
        items = _normalize_request_items(payload.get("items"), products_by_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not items:
        return jsonify({"ok": False, "error": "Add at least one product."}), 400

    if not supplier_name:
        return jsonify({"ok": False, "error": "Supplier is required."}), 400
    if not expected_delivery:
        return jsonify({"ok": False, "error": "Expected delivery date is required."}), 400
    if status not in {"draft", "approved", "sent"}:
        status = "draft"

    supplier_doc = inventory_suppliers_col.find_one({"$or": [{"code": supplier_id}, {"name_key": _supplier_name_key(supplier_name)}]}) or {}
    now = datetime.utcnow()
    po_number = _next_sequence(inventory_purchase_orders_col, "po_number", f"PO-{now.year}-")
    branch_name, location_doc = _default_branch_and_location()

    po_doc = {
        "po_number": po_number,
        "supplier_id": supplier_doc.get("code") or supplier_id,
        "supplier_name": supplier_name,
        "supplier_phone": supplier_doc.get("phone") or "",
        "supplier_location": supplier_doc.get("location") or "",
        "expected_delivery": expected_delivery,
        "status": status,
        "trigger": trigger,
        "notes": notes,
        "branch": branch_name,
        "location_id": (location_doc or {}).get("id") or "",
        "items": [
            {
                "product_id": item.get("product_id") or "",
                "product_name": item.get("product_name") or "",
                "sku": item.get("sku") or "",
                "quantity_ordered": _safe_int(item.get("quantity")),
                "quantity_received": 0,
                "quantity_rejected": 0,
                "unit_cost": _safe_float(item.get("unit_cost")),
                "status": "Not Delivered",
            }
            for item in items
        ],
        "created_by": _identity_summary(identity),
        "approved_by": _identity_summary(identity) if status in {"approved", "sent"} else {},
        "sent_by": _identity_summary(identity) if status == "sent" else {},
        "created_at": now,
        "updated_at": now,
        "receipts": [],
    }
    try:
        po_doc = create_funded_order(po_doc, payload, identity, sync_delivery=_sync_delivery_doc_from_po)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except PyMongoError:
        current_app.logger.exception("Purchase order accounting posting failed")
        return jsonify({"ok": False, "error": "Unable to confirm the accounting posting. Retry from this form; the same request will not be charged twice."}), 503
    return jsonify({"ok": True, "purchaseOrder": _serialize_purchase_order(po_doc)}), 201


@inventory_suppliers_api_bp.route("/suppliers/purchase-orders/<po_id>/action", methods=["POST"])
@role_required("inventory")
def action_purchase_order(po_id: str):
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    identity = get_current_identity()
    oid = _safe_object_id(po_id)
    if oid is None:
        return jsonify({"ok": False, "error": "Invalid purchase order."}), 400
    doc = inventory_purchase_orders_col.find_one({"_id": oid})
    if not doc:
        return jsonify({"ok": False, "error": "Purchase order not found."}), 404

    now = datetime.utcnow()
    updates: dict[str, Any] = {"updated_at": now}
    if action == "approve":
        updates["status"] = "approved"
        updates["approved_by"] = _identity_summary(identity)
    elif action == "send":
        updates["status"] = "sent"
        updates["sent_by"] = _identity_summary(identity)
        if not doc.get("approved_by"):
            updates["approved_by"] = _identity_summary(identity)
    elif action == "cancel":
        if doc.get("funding"):
            return jsonify({"ok": False, "error": "This order has an accounting posting. Arrange its accounting reversal before cancellation."}), 400
        updates["status"] = "cancelled"
    else:
        return jsonify({"ok": False, "error": "Unsupported action."}), 400

    inventory_purchase_orders_col.update_one({"_id": oid}, {"$set": updates})
    updated = inventory_purchase_orders_col.find_one({"_id": oid}) or doc
    _sync_delivery_doc_from_po(updated)
    return jsonify({"ok": True, "purchaseOrder": _serialize_purchase_order(updated)})


@inventory_suppliers_api_bp.route("/suppliers/purchase-orders/<po_id>/receive", methods=["POST"])
@role_required("inventory")
def receive_purchase_order(po_id: str):
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    identity = get_current_identity()
    oid = _safe_object_id(po_id)
    if oid is None:
        return jsonify({"ok": False, "error": "Invalid purchase order."}), 400
    po_doc = inventory_purchase_orders_col.find_one({"_id": oid})
    if not po_doc:
        return jsonify({"ok": False, "error": "Purchase order not found."}), 404

    # Keep accepting the old top-level locationId as a fallback for older
    # clients, but new receipts provide locationId on every received line.
    legacy_location_id = str(payload.get("locationId") or po_doc.get("location_id") or "").strip()
    received_at = (payload.get("receivedAt") or "").strip() or _display_date(datetime.utcnow())
    delivery_note_no = (payload.get("deliveryNoteNo") or "").strip()
    comment = (payload.get("comment") or "").strip()
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        return jsonify({"ok": False, "error": "Add at least one received line."}), 400

    # Validate the full receipt before any inventory quantity is posted. This
    # prevents a later invalid line/location from leaving a partial receipt.
    resolved_lines: dict[str, dict[str, Any]] = {}
    for line in po_doc.get("items") or []:
        product_id = str(line.get("product_id") or "")
        raw_match = next((item for item in raw_items if str(item.get("productId") or "") == product_id), None)
        if not raw_match:
            continue
        receive_qty = _safe_int(raw_match.get("receivedQty"))
        reject_qty = _safe_int(raw_match.get("rejectedQty"))
        remaining = max(_safe_int(line.get("quantity_ordered")) - _safe_int(line.get("quantity_received")) - _safe_int(line.get("quantity_rejected")), 0)
        if receive_qty < 0 or reject_qty < 0:
            return jsonify({"ok": False, "error": "Received and rejected quantities must be zero or greater."}), 400
        if receive_qty + reject_qty > remaining:
            return jsonify({"ok": False, "error": f"Received quantity exceeds remaining quantity for {line.get('product_name') or 'an item'}."}), 400

        location_doc = None
        if receive_qty > 0:
            location_id = str(raw_match.get("locationId") or legacy_location_id).strip()
            location_oid = _safe_object_id(location_id)
            if location_oid is None:
                return jsonify({"ok": False, "error": f"Select a receiving warehouse for {line.get('product_name') or 'each received product'}."}), 400
            location_row = inventory_locations_col.find_one({"_id": location_oid, "status": {"$ne": "inactive"}})
            if not location_row:
                return jsonify({"ok": False, "error": f"The receiving warehouse selected for {line.get('product_name') or 'a product'} was not found or is inactive."}), 404
            location_doc = {
                "id": str(location_row.get("_id") or ""),
                "branchId": location_row.get("branch") or "",
                "name": location_row.get("name") or "",
                "code": location_row.get("code") or "",
            }
        resolved_lines[product_id] = {
            "raw": raw_match,
            "received": receive_qty,
            "rejected": reject_qty,
            "location": location_doc,
        }

    receipt_items = []
    updated_items = []
    changed_any = False
    for line in po_doc.get("items") or []:
        updated_line = dict(line)
        resolved = resolved_lines.get(str(line.get("product_id") or ""))
        if resolved:
            raw_match = resolved["raw"]
            receive_qty = resolved["received"]
            reject_qty = resolved["rejected"]
            location_doc = resolved["location"]
            if receive_qty > 0 or reject_qty > 0:
                changed_any = True
                updated_line["quantity_received"] = _safe_int(line.get("quantity_received")) + receive_qty
                updated_line["quantity_rejected"] = _safe_int(line.get("quantity_rejected")) + reject_qty
                if updated_line["quantity_received"] >= _safe_int(line.get("quantity_ordered")):
                    updated_line["status"] = "Delivered"
                elif updated_line["quantity_received"] > 0:
                    updated_line["status"] = "Part Delivered"
                else:
                    updated_line["status"] = "Not Delivered"
                receipt_items.append(
                    {
                        "product_id": line.get("product_id") or "",
                        "product_name": line.get("product_name") or "",
                        "sku": line.get("sku") or "",
                        "qty_delivered": receive_qty,
                        "qty_rejected": reject_qty,
                        "unit_cost": _safe_float(line.get("unit_cost")),
                        "discrepancy_reason": (raw_match.get("discrepancyReason") or "").strip(),
                        "discrepancy_notes": (raw_match.get("discrepancyNotes") or "").strip(),
                        "location_id": (location_doc or {}).get("id") or "",
                        "location_name": (location_doc or {}).get("name") or "",
                        "location_code": (location_doc or {}).get("code") or "",
                        "branch": (location_doc or {}).get("branchId") or "",
                    }
                )
                if receive_qty > 0:
                    _append_inventory_receipt_entry(
                        str(line.get("product_id") or ""),
                        location_doc,
                        receive_qty,
                        _safe_float(line.get("unit_cost")),
                        identity,
                        f"purchase_order:{po_doc.get('po_number') or ''}",
                    )
        updated_items.append(updated_line)

    if not changed_any:
        return jsonify({"ok": False, "error": "Enter at least one delivered or rejected quantity."}), 400

    overall_status = "approved"
    if all(_safe_int(item.get("quantity_received")) >= _safe_int(item.get("quantity_ordered")) for item in updated_items):
        overall_status = "completed"
    elif any(_safe_int(item.get("quantity_received")) > 0 for item in updated_items):
        overall_status = "partial"

    receipt_doc = {
        "received_at": received_at,
        "received_by": _identity_summary(identity),
        "delivery_note_no": delivery_note_no,
        "comment": comment,
        "items": receipt_items,
    }
    receipt_locations = {
        item["location_id"]: {
            "id": item["location_id"],
            "name": item["location_name"],
            "code": item["location_code"],
            "branch": item["branch"],
        }
        for item in receipt_items if item.get("location_id")
    }
    receipt_doc["locations"] = list(receipt_locations.values())
    if len(receipt_locations) == 1:
        only_location = next(iter(receipt_locations.values()))
        # Retain legacy summary fields when every received product went to the
        # same warehouse so older receipt displays continue to work.
        receipt_doc.update({
            "location_id": only_location["id"],
            "location_name": only_location["name"],
            "branch": only_location["branch"],
        })
    elif len(receipt_locations) > 1:
        receipt_doc["location_name"] = "Multiple warehouses"
    inventory_purchase_orders_col.update_one(
        {"_id": oid},
        {
            "$set": {
                "items": updated_items,
                "status": overall_status,
                "receiving_locations": list(receipt_locations.values()),
                "updated_at": datetime.utcnow(),
            },
            "$push": {"receipts": receipt_doc},
        },
    )
    updated = inventory_purchase_orders_col.find_one({"_id": oid}) or po_doc
    _sync_delivery_doc_from_po(updated)
    return jsonify({"ok": True, "purchaseOrder": _serialize_purchase_order(updated)})


@inventory_suppliers_api_bp.route("/suppliers/cost-updates", methods=["POST"])
@role_required("inventory")
def create_cost_update():
    _ensure_indexes()
    payload = request.get_json(silent=True) or {}
    identity = get_current_identity()
    product_id = str(payload.get("productId") or "").strip()
    supplier_id = str(payload.get("supplierId") or "").strip()
    supplier_name = (payload.get("supplier") or "").strip()
    old_cost = _safe_float(payload.get("oldCost"))
    new_cost = _safe_float(payload.get("newCost"))
    reason = (payload.get("reason") or "").strip()
    effective_date = (payload.get("effectiveDate") or "").strip() or _display_date(datetime.utcnow())

    if not product_id:
        return jsonify({"ok": False, "error": "Product is required."}), 400
    if not supplier_name:
        return jsonify({"ok": False, "error": "Supplier is required."}), 400
    if new_cost <= 0:
        return jsonify({"ok": False, "error": "New cost must be greater than zero."}), 400
    if not reason:
        return jsonify({"ok": False, "error": "Reason is required."}), 400

    product = next((row for row in list_inventory_products() if str(row.get("id") or "") == product_id), None)
    if not product:
        return jsonify({"ok": False, "error": "Selected product no longer exists."}), 404

    now = datetime.utcnow()
    update_number = _next_sequence(inventory_cost_updates_col, "update_number", f"CU-{now.year}-")
    doc = {
        "update_number": update_number,
        "product_id": product_id,
        "product_name": product.get("name") or "",
        "sku": product.get("sku") or "",
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "old_cost": old_cost,
        "new_cost": new_cost,
        "reason": reason,
        "effective_date": effective_date,
        "changed_by": _identity_summary(identity),
        "created_at": now,
        "updated_at": now,
    }
    inventory_cost_updates_col.insert_one(doc)
    return jsonify({"ok": True, "costUpdate": _serialize_cost_update(doc)}), 201
