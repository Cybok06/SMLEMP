from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any

from bson import ObjectId

from cache_ext import cache
from db import db


products_col = db["products"]
customers_col = db["customers"]
users_col = db["users"]
inventory_col = db["inventory"]
inventory_products_col = db["inventory_products"]
payments_col = db["payments"]

PRODUCT_CARD_CACHE_TIMEOUT = 120


def _safe_object_id(value: Any) -> ObjectId | None:
    try:
        if value and ObjectId.is_valid(str(value)):
            return ObjectId(str(value))
    except Exception:
        return None
    return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


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


def _norm_str(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _profit_margin(cost_price: float, selling_price: float) -> float:
    if cost_price <= 0:
        return 0.0
    return round(((selling_price - cost_price) / cost_price) * 100.0, 2)


def _product_identity_key(doc: dict[str, Any]) -> str:
    components: list[dict[str, Any]] = []
    for component in (doc.get("components") or []):
        component_id = component.get("_id") or component.get("id") or component.get("product_id")
        if component_id is None:
            continue
        components.append(
            {
                "_id": str(component_id),
                "quantity": _safe_int(component.get("quantity", 0), 0),
            }
        )
    components.sort(key=lambda item: item["_id"])

    payload = {
        "name": _norm_str(doc.get("name")),
        "image_url": _norm_str(doc.get("image_url")),
        "description": _norm_str(doc.get("description")),
        "price": round(_safe_float(doc.get("price"), 0.0), 2),
        "cash_price": round(_safe_float(doc.get("cash_price"), 0.0), 2),
        "payment_rate": round(_safe_float(doc.get("payment_rate"), 0.0), 2),
        "cost_price": round(_safe_float(doc.get("cost_price"), 0.0), 2),
        "product_type": _norm_str(doc.get("product_type")),
        "category": _norm_str(doc.get("category")),
        "package_name": _norm_str(doc.get("package_name")),
        "default_term_months": _safe_int(doc.get("default_term_months"), 0),
        "components": components,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _product_display_group_key(doc: dict[str, Any]) -> str:
    name = str(doc.get("name") or "").strip().lower()
    return name or f"unnamed::{str(doc.get('_id') or '')}"


def _serialize_manager(doc: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(doc.get("_id") or ""),
        "name": str(doc.get("name") or doc.get("username") or "Manager").strip(),
        "branch": str(doc.get("branch") or "").strip(),
    }


def _serialize_component(doc: dict[str, Any]) -> dict[str, Any]:
    key_payload = {
        "name": doc.get("name"),
        "image_url": doc.get("image_url"),
        "price": doc.get("price"),
        "description": doc.get("description"),
    }
    return {
        "key": json.dumps(key_payload, separators=(",", ":")),
        "name": str(doc.get("name") or "").strip(),
        "price": _safe_float(doc.get("price")),
        "description": str(doc.get("description") or "").strip(),
        "imageUrl": str(doc.get("image_url") or "").strip(),
    }


def _latest_inventory_product_prices(doc: dict[str, Any]) -> tuple[float, float]:
    entries = doc.get("entries") or []
    latest = None
    latest_marker = datetime.min
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        marker = _coerce_datetime(entry.get("updated_at") or entry.get("created_at"))
        if latest is None or marker > latest_marker:
            latest = entry
            latest_marker = marker
    if not latest:
        return (_safe_float(doc.get("unit_cost")), 0.0)
    return (_safe_float(latest.get("cost_price")), _safe_float(latest.get("selling_price")))


def _serialize_inventory_product_component(doc: dict[str, Any]) -> dict[str, Any]:
    cost_price, selling_price = _latest_inventory_product_prices(doc)
    total_stock = 0
    for entry in (doc.get("entries") or []):
        if isinstance(entry, dict):
            total_stock += max(_safe_int(entry.get("quantity"), 0), 0)
    return {
        "key": f"inventory_product:{str(doc.get('_id') or '')}",
        "id": str(doc.get("_id") or ""),
        "name": str(doc.get("name") or "").strip(),
        "price": selling_price or cost_price,
        "description": str(doc.get("description") or "").strip(),
        "imageUrl": str(doc.get("image_url") or "").strip(),
        "availableQty": total_stock,
        "category": str(doc.get("category") or "").strip(),
        "brand": str(doc.get("brand") or "").strip(),
    }


def _resolve_component_refs(component_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_components: list[dict[str, Any]] = []
    inventory_product_ids: list[ObjectId] = []

    for component in component_rows:
        if not isinstance(component, dict):
            continue
        quantity = _safe_int(component.get("quantity"), 0)
        raw_key = str(component.get("key") or "").strip()
        if quantity <= 0 or not raw_key:
            continue

        if raw_key.startswith("inventory_product:"):
            product_id = raw_key.split(":", 1)[1].strip()
            product_oid = _safe_object_id(product_id)
            if product_oid is None:
                raise ValueError("A selected inventory product is invalid.")
            inventory_product_ids.append(product_oid)
            normalized_components.append(
                {
                    "_id": product_oid,
                    "quantity": quantity,
                    "source_collection": "inventory_products",
                }
            )
            continue

        try:
            decoded = json.loads(raw_key)
        except json.JSONDecodeError as exc:
            raise ValueError("A selected component could not be read.") from exc
        normalized_components.append({"legacy_key": decoded, "quantity": quantity})

    if not normalized_components:
        raise ValueError("Select at least one inventory component.")

    if inventory_product_ids:
        found_ids = {
            str(doc.get("_id"))
            for doc in inventory_products_col.find({"_id": {"$in": inventory_product_ids}}, {"_id": 1})
        }
        for component in normalized_components:
            if component.get("source_collection") == "inventory_products":
                if str(component["_id"]) not in found_ids:
                    raise ValueError("One or more selected inventory products no longer exist.")

    return normalized_components


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
        start_date = date.fromisoformat(str(start_raw)[:10])
        end_date = date.fromisoformat(str(end_raw)[:10])
    except Exception:
        return 0

    total_days = max((end_date - start_date).days, 0)
    if total_days == 0:
        return 100 if datetime.utcnow().date() >= end_date else 0

    elapsed_days = max((min(datetime.utcnow().date(), end_date) - start_date).days, 0)
    return max(0, min(100, round((elapsed_days / total_days) * 100)))


def _profitability_label(margin: float) -> str:
    if margin >= 30:
        return "high"
    if margin >= 15:
        return "medium"
    return "low"


def _status_for_stock_ready(stock_ready: int) -> str:
    return "active" if stock_ready >= 70 else "low-stock"


def invalidate_product_card_cache() -> None:
    cache.delete_memoized(get_product_card_bootstrap)
    cache.delete_memoized(list_product_cards)


@cache.memoize(timeout=PRODUCT_CARD_CACHE_TIMEOUT)
def get_product_card_bootstrap() -> dict[str, Any]:
    manager_docs = list(
        users_col.find({"role": "manager"}, {"name": 1, "username": 1, "branch": 1}).sort([("name", 1)])
    )
    inventory_product_docs = list(
        inventory_products_col.find(
            {},
            {
                "name": 1,
                "category": 1,
                "brand": 1,
                "description": 1,
                "image_url": 1,
                "entries": 1,
                "unit_cost": 1,
            },
        ).sort([("updated_at", -1), ("created_at", -1)]).limit(5000)
    )

    return {
        "managers": [_serialize_manager(doc) for doc in manager_docs],
        "inventoryItems": sorted(
            [_serialize_inventory_product_component(doc) for doc in inventory_product_docs],
            key=lambda item: item["name"].lower(),
        ),
        "productTypes": sorted(
            [str(value).strip() for value in products_col.distinct("product_type") if str(value or "").strip()],
            key=str.lower,
        ),
        "categories": sorted(
            [str(value).strip() for value in products_col.distinct("category") if str(value or "").strip()],
            key=str.lower,
        ),
        "packageNames": sorted(
            [str(value).strip() for value in products_col.distinct("package_name") if str(value or "").strip()],
            key=str.lower,
        ),
        "installmentTerms": [3, 6, 9, 12, 18, 24],
    }


def _build_product_card_records(include_detail: bool = True) -> list[dict[str, Any]]:
    projection = {
        "name": 1,
        "price": 1,
        "cash_price": 1,
        "payment_rate": 1,
        "cost_price": 1,
        "profit_margin_price": 1,
        "description": 1,
        "image_url": 1,
        "cf_image_id": 1,
        "product_type": 1,
        "category": 1,
        "package_name": 1,
        "default_term_months": 1,
        "components": 1,
        "manager_id": 1,
        "created_at": 1,
        "updated_at": 1,
    }
    product_docs = list(products_col.find({}, projection).sort([("updated_at", -1), ("created_at", -1)]).limit(5000))
    if not product_docs:
        return []
    product_object_ids = [doc.get("_id") for doc in product_docs if doc.get("_id")]

    manager_ids: set[ObjectId] = set()
    inventory_ids: set[ObjectId] = set()
    inventory_product_ids: set[ObjectId] = set()
    for doc in product_docs:
        manager_oid = _safe_object_id(doc.get("manager_id"))
        if manager_oid:
            manager_ids.add(manager_oid)
        for component in (doc.get("components") or []):
            component_oid = _safe_object_id(component.get("_id"))
            if component_oid:
                if str(component.get("source_collection") or "").strip() == "inventory_products":
                    inventory_product_ids.add(component_oid)
                else:
                    inventory_ids.add(component_oid)

    manager_map = {
        str(doc["_id"]): doc
        for doc in users_col.find({"_id": {"$in": list(manager_ids)}}, {"name": 1, "username": 1, "branch": 1})
    }
    inventory_map = {
        str(doc["_id"]): doc
        for doc in inventory_col.find({"_id": {"$in": list(inventory_ids)}}, {"name": 1, "price": 1, "description": 1, "image_url": 1, "qty": 1})
    }
    inventory_products_map = {
        str(doc["_id"]): doc
        for doc in inventory_products_col.find(
            {"_id": {"$in": list(inventory_product_ids)}},
            {"name": 1, "category": 1, "brand": 1, "description": 1, "image_url": 1, "entries": 1, "unit_cost": 1},
        )
    }

    grouped: dict[str, dict[str, Any]] = {}
    product_to_group: dict[str, str] = {}

    for doc in product_docs:
        group_key = _product_display_group_key(doc)
        product_id = str(doc.get("_id") or "")
        product_to_group[product_id] = group_key
        group = grouped.setdefault(
            group_key,
            {
                "canonical": doc,
                "productIds": [],
                "managers": [],
                "managerIds": set(),
                "branches": set(),
                "componentSummary": {},
                "requiredUnits": 0,
                "supportedUnits": 0,
                "purchaseCount": 0,
                "customerIds": set(),
                "completion70": 0,
                "completion80": 0,
                "completion90": 0,
                "salesValue": 0.0,
                "lastPurchaseDate": "",
                "managerPricing": [],
                "customerRows": [],
            },
        )
        group["productIds"].append(product_id)

        manager_id = str(doc.get("manager_id") or "")
        if manager_id and manager_id not in group["managerIds"]:
            group["managerIds"].add(manager_id)
            manager_doc = manager_map.get(manager_id)
            manager_payload = {
                "id": manager_id,
                "name": str((manager_doc or {}).get("name") or (manager_doc or {}).get("username") or "Manager").strip(),
                "branch": str((manager_doc or {}).get("branch") or "").strip(),
            }
            group["managers"].append(manager_payload)
            if manager_payload["branch"]:
                group["branches"].add(manager_payload["branch"])
        if include_detail:
            group["managerPricing"].append(
                {
                    "id": manager_id,
                    "name": str((manager_map.get(manager_id) or {}).get("name") or (manager_map.get(manager_id) or {}).get("username") or "Manager").strip(),
                    "branch": str((manager_map.get(manager_id) or {}).get("branch") or "").strip(),
                    "price": _safe_float(doc.get("price")),
                    "cashPrice": _safe_float(doc.get("cash_price")),
                    "paymentRate": _safe_float(doc.get("payment_rate")),
                    "costPrice": _safe_float(doc.get("cost_price")),
                    "productDocumentId": product_id,
                    "updatedAt": doc.get("updated_at").strftime("%Y-%m-%d") if isinstance(doc.get("updated_at"), datetime) else "",
                }
            )

        for component in (doc.get("components") or []):
            component_id = str(component.get("_id") or "")
            quantity = max(_safe_int(component.get("quantity"), 0), 0)
            source_collection = str(component.get("source_collection") or "").strip()
            inventory_doc = inventory_map.get(component_id, {})
            inventory_product_doc = inventory_products_map.get(component_id, {})
            if source_collection == "inventory_products":
                component_doc = inventory_product_doc
                available_qty = sum(
                    max(_safe_int(entry.get("quantity"), 0), 0)
                    for entry in (inventory_product_doc.get("entries") or [])
                    if isinstance(entry, dict)
                )
                component_price = _serialize_inventory_product_component(inventory_product_doc).get("price") if inventory_product_doc else 0
                component_image = str(component_doc.get("image_url") or "").strip()
                component_description = str(component_doc.get("description") or "").strip()
                component_name = str(component_doc.get("name") or "Unknown Item").strip()
            else:
                component_doc = inventory_doc
                available_qty = max(_safe_int(inventory_doc.get("qty"), 0), 0)
                component_price = _safe_float(inventory_doc.get("price"))
                component_image = str(component_doc.get("image_url") or "").strip()
                component_description = str(component_doc.get("description") or "").strip()
                component_name = str(component_doc.get("name") or "Unknown Item").strip()

            summary = group["componentSummary"].setdefault(
                f"{source_collection or 'inventory'}::{component_id or f'missing::{len(group['componentSummary'])}'}",
                {
                    "id": component_id,
                    "name": component_name,
                    "description": component_description,
                    "imageUrl": component_image,
                    "unitPrice": component_price,
                    "quantity": 0,
                    "availableQty": 0,
                    "sourceCollection": source_collection or "inventory",
                    "key": f"inventory_product:{component_id}" if source_collection == "inventory_products" else "",
                },
            )
            summary["quantity"] += quantity
            summary["availableQty"] += available_qty
            group["requiredUnits"] += quantity
            group["supportedUnits"] += min(available_qty, quantity)

    customer_projection = {
        "name": 1,
        "phone_number": 1,
        "location": 1,
        "date_registered": 1,
        "manager_id": 1,
        "purchases": 1,
    }
    customer_query = {"purchases.product._id": {"$in": product_object_ids}} if product_object_ids else {"_id": {"$in": []}}
    customers = list(customers_col.find(customer_query, customer_projection).limit(5000))
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

    for customer in customers:
        customer_id = str(customer.get("_id") or "")
        matched_groups: set[str] = set()
        purchases = customer.get("purchases") or []
        for index, purchase in enumerate(purchases):
            if not isinstance(purchase, dict):
                continue
            product = purchase.get("product") or {}
            if not isinstance(product, dict):
                product = {}
            purchased_product_id = str(product.get("_id") or "").strip()
            group_key = product_to_group.get(purchased_product_id)
            if not group_key:
                continue

            matched_groups.add(group_key)
            group = grouped[group_key]
            completion_pct = _purchase_completion_pct(purchase)
            quantity = max(_safe_int(product.get("quantity"), 1), 1)
            total_value = _safe_float(product.get("total"), _safe_float(product.get("price")) * quantity)

            group["purchaseCount"] += 1
            if completion_pct >= 70:
                group["completion70"] += 1
            if completion_pct >= 80:
                group["completion80"] += 1
            if completion_pct >= 90:
                group["completion90"] += 1
            group["salesValue"] += total_value

            purchase_date = str(purchase.get("purchase_date") or "")[:10]
            if purchase_date and purchase_date > group["lastPurchaseDate"]:
                group["lastPurchaseDate"] = purchase_date

            customer_manager_id = str(customer.get("manager_id") or "").strip()
            customer_manager = manager_map.get(customer_manager_id) or {}
            if include_detail:
                group["customerRows"].append(
                    {
                        "id": f"{customer_id}:{index}",
                        "customerId": customer_id,
                        "name": str(customer.get("name") or "").strip(),
                        "phone": str(customer.get("phone_number") or "").strip(),
                        "location": str(customer.get("location") or "").strip(),
                        "branch": str(customer_manager.get("branch") or "").strip(),
                        "dateRegistered": str(customer.get("date_registered") or "")[:10],
                        "purchaseDate": purchase_date,
                        "amountPaid": max(0.0, round(payment_map.get((customer_id, index), 0.0), 2)),
                        "completion": completion_pct,
                    }
                )

        for group_key in matched_groups:
            grouped[group_key]["customerIds"].add(customer_id)

    cards: list[dict[str, Any]] = []
    for group_key, group in grouped.items():
        canonical = group["canonical"]
        price = _safe_float(canonical.get("price"))
        cash_price = _safe_float(canonical.get("cash_price"))
        payment_rate = _safe_float(canonical.get("payment_rate"))
        cost_price = _safe_float(canonical.get("cost_price"))
        margin = _safe_float(canonical.get("profit_margin_price"), _profit_margin(cost_price, price))
        required_units = group["requiredUnits"]
        stock_ready = round((group["supportedUnits"] / required_units) * 100) if required_units > 0 else 0

        components = sorted(group["componentSummary"].values(), key=lambda item: item["name"].lower())
        created_at = canonical.get("created_at")
        updated_at = canonical.get("updated_at")
        card_record = {
            "id": group_key,
            "name": str(canonical.get("name") or "").strip(),
            "description": str(canonical.get("description") or "").strip(),
            "price": price,
            "cashPrice": cash_price,
            "paymentRate": payment_rate,
            "costPrice": cost_price,
            "profitMarginPrice": margin,
            "itemsCount": sum(max(_safe_int(component.get("quantity"), 0), 0) for component in components),
            "componentTypes": len(components),
            "customers": len(group["customerIds"]),
            "completion70": group["completion70"],
            "completion80": group["completion80"],
            "completion90": group["completion90"],
            "stockReady": stock_ready,
            "status": _status_for_stock_ready(stock_ready),
            "profitability": _profitability_label(margin),
            "image": str(canonical.get("image_url") or "").strip(),
            "cfImageId": str(canonical.get("cf_image_id") or "").strip(),
            "productType": str(canonical.get("product_type") or "").strip(),
            "category": str(canonical.get("category") or "").strip(),
            "packageName": str(canonical.get("package_name") or "").strip(),
            "defaultTermMonths": _safe_int(canonical.get("default_term_months"), 0),
            "managerCount": len(group["managers"]),
            "branchCount": len(group["branches"]),
            "branches": sorted(group["branches"]),
            "totalSalesValue": round(group["salesValue"], 2),
            "purchaseCount": group["purchaseCount"],
            "lastPurchaseDate": group["lastPurchaseDate"],
            "createdAt": created_at.strftime("%Y-%m-%d") if isinstance(created_at, datetime) else "",
            "updatedAt": updated_at.strftime("%Y-%m-%d") if isinstance(updated_at, datetime) else "",
            "changeHistory": [
                {
                    "id": str(entry.get("id") or ""),
                    "changedAt": entry.get("changed_at").isoformat() + "Z" if isinstance(entry.get("changed_at"), datetime) else "",
                    "changedBy": entry.get("changed_by") or {},
                    "changes": entry.get("changes") or [],
                }
                for entry in reversed((canonical.get("change_history") or [])[-100:])
            ],
        }
        if include_detail:
            card_record.update(
                {
                    "managers": sorted(group["managerPricing"], key=lambda item: (item["name"].lower(), item["branch"].lower())),
                    "sourceProductIds": group["productIds"],
                    "components": components,
                    "customerRows": sorted(
                        group["customerRows"],
                        key=lambda item: (
                            (item.get("name") or "").lower(),
                            item.get("purchaseDate") or "",
                        ),
                    ),
                }
            )
        cards.append(card_record)

    cards.sort(key=lambda item: ((item.get("updatedAt") or ""), item.get("name") or ""), reverse=True)
    return cards


@cache.memoize(timeout=PRODUCT_CARD_CACHE_TIMEOUT)
def list_product_cards() -> list[dict[str, Any]]:
    return _build_product_card_records(include_detail=True)


def create_product_card(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    price = _safe_float(payload.get("price"))
    cash_price = _safe_float(payload.get("cashPrice"))
    payment_rate = _safe_float(payload.get("paymentRate"))
    cost_price = _safe_float(payload.get("costPrice"))
    description = str(payload.get("description") or "").strip()
    image_url = str(payload.get("imageUrl") or "").strip()
    image_id = str(payload.get("imageId") or "").strip() or None
    product_type = str(payload.get("productType") or "").strip()
    category = str(payload.get("category") or "").strip()
    package_name = str(payload.get("packageName") or "").strip()
    default_term_months = _safe_int(payload.get("defaultTermMonths"), 0)
    manager_ids = [str(value).strip() for value in (payload.get("managerIds") or []) if str(value or "").strip()]
    component_rows = payload.get("components") or []

    if not name:
        raise ValueError("Product name is required.")
    if not image_url:
        raise ValueError("Product image is required.")
    if price <= 0 and cash_price <= 0:
        raise ValueError("Enter at least one selling price.")
    if cost_price <= 0:
        raise ValueError("Cost price is required.")
    if payment_rate <= 0:
        raise ValueError("Payment rate must be greater than zero.")
    if not manager_ids:
        raise ValueError("Select at least one manager.")
    if not isinstance(component_rows, list) or not component_rows:
        raise ValueError("Select at least one inventory component.")

    managers_map = {
        str(doc.get("_id")): doc
        for doc in users_col.find({"_id": {"$in": [_safe_object_id(value) for value in manager_ids if _safe_object_id(value)]}})
    }
    if not managers_map:
        raise ValueError("Selected managers were not found.")

    normalized_components = _resolve_component_refs(component_rows)

    now = datetime.utcnow()
    created_count = 0
    skipped: list[dict[str, str]] = []

    for manager_id in manager_ids:
        manager_oid = _safe_object_id(manager_id)
        manager_doc = managers_map.get(manager_id)
        if manager_oid is None or not manager_doc:
            skipped.append({"managerId": manager_id, "reason": "Manager not found."})
            continue

        candidate_doc = {
            "name": name,
            "price": price,
            "cash_price": cash_price,
            "payment_rate": payment_rate,
            "cost_price": cost_price,
            "description": description,
            "image_url": image_url,
            "product_type": product_type,
            "category": category,
            "package_name": package_name,
            "default_term_months": default_term_months,
            "components": normalized_components,
        }
        candidate_key = _product_display_group_key(candidate_doc)
        manager_existing = list(
            products_col.find(
                {"manager_id": manager_oid},
                {
                    "name": 1,
                    "price": 1,
                    "cash_price": 1,
                    "payment_rate": 1,
                    "cost_price": 1,
                    "description": 1,
                    "image_url": 1,
                    "product_type": 1,
                    "category": 1,
                    "package_name": 1,
                    "default_term_months": 1,
                    "components": 1,
                },
            ).limit(2000)
        )
        if any(_product_display_group_key(existing) == candidate_key for existing in manager_existing):
            skipped.append(
                {
                    "managerId": manager_id,
                    "reason": f"{manager_doc.get('name') or 'Manager'} already has this product card.",
                }
            )
            continue

        profit_price = round(price - cost_price, 2)
        profit_cash = round(cash_price - cost_price, 2)
        products_col.insert_one(
            {
                **candidate_doc,
                "cf_image_id": image_id,
                "profit_price": profit_price,
                "profit_cash": profit_cash,
                "profit_margin_price": _profit_margin(cost_price, price),
                "profit_margin_cash": _profit_margin(cost_price, cash_price),
                "manager_id": manager_oid,
                "created_at": now,
                "updated_at": now,
            }
        )
        created_count += 1

    if created_count == 0:
        if skipped:
            raise ValueError(skipped[0]["reason"])
        raise ValueError("No product card was created.")

    invalidate_product_card_cache()
    created_card = None
    for card in list_product_cards():
        if (
            str(card.get("name") or "").strip().lower() == name.lower()
            and str(card.get("image") or "").strip() == image_url
            and round(_safe_float(card.get("price")), 2) == round(price, 2)
            and round(_safe_float(card.get("cashPrice")), 2) == round(cash_price, 2)
        ):
            created_card = card
            break

    return {
        "createdCount": created_count,
        "skipped": skipped,
        "card": created_card,
    }


def update_product_card_components(card_id: str, payload: dict[str, Any], identity: dict[str, Any] | None = None) -> dict[str, Any]:
    component_rows = payload.get("components") or []
    if not isinstance(component_rows, list) or not component_rows:
        raise ValueError("Select at least one inventory product for this card.")

    normalized_components = _resolve_component_refs(component_rows)
    product_docs = list(
        products_col.find(
            {},
            {
                "name": 1,
                "components": 1,
            },
        ).limit(5000)
    )
    target_docs = [doc for doc in product_docs if _product_display_group_key(doc) == card_id]
    if not target_docs:
        raise ValueError("Product card not found.")

    now = datetime.utcnow()
    actor = identity or {}
    operations = []
    for doc in target_docs:
        operations.append((
            doc["_id"],
            {
                "$set": {
                    "components": normalized_components,
                    "updated_at": now,
                },
                "$push": {
                    "change_history": {
                        "$each": [{
                            "id": str(ObjectId()),
                            "changed_at": now,
                            "changed_by": {
                                "user_id": actor.get("user_id"),
                                "name": actor.get("name") or actor.get("username") or "Inventory User",
                                "username": actor.get("username"),
                                "role": actor.get("role"),
                            },
                            "changes": [{
                                "field": "components",
                                "label": "Inventory components",
                                "before": sum(_safe_int(row.get("quantity"), 0) for row in (doc.get("components") or [])),
                                "after": sum(_safe_int(row.get("quantity"), 0) for row in normalized_components),
                            }],
                        }],
                        "$slice": -100,
                    }
                },
            },
        ))

    from .card_recipe_sync import update_cards_with_recipes
    updated_packages = update_cards_with_recipes(operations, actor)
    invalidate_product_card_cache()
    updated_card = next((card for card in list_product_cards() if card.get("id") == card_id), None)
    return {
        "updatedCount": len(target_docs),
        "updatedPackages": updated_packages,
        "card": updated_card,
    }


def update_product_card(card_id: str, payload: dict[str, Any], identity: dict[str, Any] | None = None) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    price = _safe_float(payload.get("price"))
    cash_price = _safe_float(payload.get("cashPrice"))
    payment_rate = _safe_float(payload.get("paymentRate"))
    cost_price = _safe_float(payload.get("costPrice"))
    component_rows = payload.get("components") or []

    if not name:
        raise ValueError("Product name is required.")
    if price <= 0 and cash_price <= 0:
        raise ValueError("Enter at least one selling price.")
    if cost_price <= 0:
        raise ValueError("Cost price is required.")
    if payment_rate <= 0:
        raise ValueError("Payment rate must be greater than zero.")
    if not isinstance(component_rows, list) or not component_rows:
        raise ValueError("Select at least one inventory component.")

    target_docs = [
        doc for doc in products_col.find({}).limit(5000)
        if _product_display_group_key(doc) == card_id
    ]
    if not target_docs:
        raise ValueError("Product card not found.")

    normalized_components = _resolve_component_refs(component_rows)
    image_url = str(payload.get("imageUrl") or "").strip()
    image_id = str(payload.get("imageId") or "").strip()
    update_fields = {
        "name": name,
        "price": price,
        "cash_price": cash_price,
        "payment_rate": payment_rate,
        "cost_price": cost_price,
        "description": str(payload.get("description") or "").strip(),
        "product_type": str(payload.get("productType") or "").strip(),
        "category": str(payload.get("category") or "").strip(),
        "package_name": str(payload.get("packageName") or "").strip(),
        "default_term_months": _safe_int(payload.get("defaultTermMonths"), 0),
        "components": normalized_components,
        "profit_price": round(price - cost_price, 2),
        "profit_cash": round(cash_price - cost_price, 2),
        "profit_margin_price": _profit_margin(cost_price, price),
        "profit_margin_cash": _profit_margin(cost_price, cash_price),
        "updated_at": datetime.utcnow(),
    }
    if image_url:
        update_fields["image_url"] = image_url
    if image_id:
        update_fields["cf_image_id"] = image_id

    field_labels = {
        "name": "Product name", "price": "Installment price", "cash_price": "Cash price",
        "payment_rate": "Payment rate",
        "cost_price": "Cost price", "description": "Description", "product_type": "Product type",
        "category": "Category", "package_name": "Package name",
        "default_term_months": "Installment term", "image_url": "Product image",
    }
    canonical = target_docs[0]
    changes = []
    for field, label in field_labels.items():
        if field not in update_fields:
            continue
        before = canonical.get(field)
        after = update_fields[field]
        if before != after:
            changes.append({"field": field, "label": label, "before": before, "after": after})
    old_components = canonical.get("components") or []
    if old_components != normalized_components:
        changes.append({
            "field": "components", "label": "Inventory components",
            "before": sum(_safe_int(row.get("quantity"), 0) for row in old_components),
            "after": sum(_safe_int(row.get("quantity"), 0) for row in normalized_components),
        })

    update_operation: dict[str, Any] = {"$set": update_fields}
    if changes:
        actor = identity or {}
        update_operation["$push"] = {
            "change_history": {
                "$each": [{
                    "id": str(ObjectId()),
                    "changed_at": update_fields["updated_at"],
                    "changed_by": {
                        "user_id": actor.get("user_id"),
                        "name": actor.get("name") or actor.get("username") or "Inventory User",
                        "username": actor.get("username"),
                        "role": actor.get("role"),
                    },
                    "changes": changes,
                }],
                "$slice": -100,
            }
        }
    from .card_recipe_sync import update_cards_with_recipes
    updated_packages = update_cards_with_recipes(
        [(doc['_id'], update_operation) for doc in target_docs], identity or {})
    invalidate_product_card_cache()

    updated_card = None
    target_ids = {str(doc["_id"]) for doc in target_docs}
    for card in list_product_cards():
        if target_ids.intersection(card.get("sourceProductIds") or []):
            updated_card = card
            break
    return {"updatedCount": len(target_docs), "updatedPackages": updated_packages, "card": updated_card}
