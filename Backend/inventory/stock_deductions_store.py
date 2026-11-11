from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from db import db
from .settings_store import get_effective_inventory_role, get_inventory_user_doc


GHANA = ZoneInfo("Africa/Accra")
MAX_RANGE_DAYS = 366

packages_col = db["packages"]
customers_col = db["customers"]
users_col = db["users"]
products_col = db["products"]
inventory_products_col = db["inventory_products"]
locations_col = db["inventory_branch_locations"]
outflows_col = db["inventory_products_outflow"]
deductions_col = db["inventory_delivery_deductions"]
movements_col = db["inventory_delivery_movements"]
stock_sessions_col = db["inventory_stock_update_sessions"]
activity_logs_col = db["activity_logs"]


class StockDeductionError(ValueError):
    def __init__(self, message: str, code: str = "validation_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def ensure_stock_deduction_indexes() -> None:
    deductions_col.create_index([("package_id", ASCENDING)], unique=True, name="uniq_delivery_deduction_package")
    deductions_col.create_index([("idempotency_key", ASCENDING)], unique=True, name="uniq_delivery_deduction_key")
    deductions_col.create_index([("confirmed_at", -1)], name="delivery_deduction_confirmed_at")
    movements_col.create_index([("deduction_id", ASCENDING), ("product_id", ASCENDING)], name="delivery_movement_deduction")
    packages_col.create_index([("created_at", -1), ("_id", -1)], name="stock_deduction_created_at")
    packages_col.create_index([("submitted_at", -1), ("_id", -1)], name="stock_deduction_submitted_at")


def _oid(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000 if value > 10**12 else value)
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def parse_ghana_range(from_date: str, to_date: str) -> tuple[datetime, datetime]:
    try:
        start_day = date.fromisoformat(str(from_date or ""))
        end_day = date.fromisoformat(str(to_date or ""))
    except ValueError as exc:
        raise StockDeductionError("From Date and To Date are required in YYYY-MM-DD format.", "invalid_date") from exc
    if start_day > end_day:
        raise StockDeductionError("From Date cannot be later than To Date.", "invalid_range")
    if (end_day - start_day).days + 1 > MAX_RANGE_DAYS:
        raise StockDeductionError(f"Date range cannot exceed {MAX_RANGE_DAYS} days.", "range_too_large")
    start_aware = datetime.combine(start_day, time.min, tzinfo=GHANA)
    end_aware = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=GHANA)
    # Mongo legacy dates are naive UTC. Ghana is UTC+0, so stripping timezone preserves boundaries.
    return start_aware.astimezone(timezone.utc).replace(tzinfo=None), end_aware.astimezone(timezone.utc).replace(tzinfo=None)


def _date_query(start: datetime, end_exclusive: datetime) -> dict:
    return {
        "$or": [
            {"created_at": {"$gte": start, "$lt": end_exclusive}},
            {
                "created_at": {"$exists": False},
                "submitted_at": {"$gte": start, "$lt": end_exclusive},
            },
        ]
    }


def _submission_time(package: dict) -> datetime | None:
    return _dt(package.get("created_at")) or _dt(package.get("submitted_at"))


def _normalize_component_rows(rows: list[dict], card_quantity: int, source: str, frozen_at: datetime | None = None) -> list[dict]:
    normalized: list[dict] = []
    for row in rows or []:
        product_id = _oid(row.get("inventory_product_id") or row.get("_id") or row.get("inventory_id"))
        if not product_id:
            continue
        per_card = max(0, _integer(row.get("quantity_per_card"), _integer(row.get("quantity"), 0)))
        if per_card <= 0:
            continue
        normalized.append(
            {
                "inventory_product_id": product_id,
                "sku": str(row.get("sku") or ""),
                "component_name": str(row.get("component_name") or row.get("name") or row.get("inventory_name") or ""),
                "quantity_per_card": per_card,
                "source_collection": str(row.get("source_collection") or "inventory_products"),
                "card_quantity": card_quantity,
                "required_quantity": per_card * card_quantity,
                "recipe_source": source,
                "snapshot_at": frozen_at or datetime.utcnow(),
            }
        )
    return normalized


def build_submission_recipe_snapshot(product_doc: dict | None, card_quantity: int, *, session=None) -> dict | None:
    if not product_doc:
        return None
    card_quantity = max(1, _integer(card_quantity, 1))
    raw_components = product_doc.get("components") or []
    ids = [_oid(row.get("_id")) for row in raw_components if isinstance(row, dict)]
    ids = [value for value in ids if value]
    product_map = {
        row["_id"]: row
        for row in inventory_products_col.find(
            {"_id": {"$in": ids}},
            {"sku": 1, "name": 1},
            session=session,
        )
    } if ids else {}
    component_rows = []
    for row in raw_components:
        if not isinstance(row, dict):
            continue
        product_id = _oid(row.get("_id"))
        catalog = product_map.get(product_id, {})
        component_rows.append(
            {
                "_id": product_id,
                "quantity": row.get("quantity"),
                "source_collection": row.get("source_collection") or "inventory",
                "sku": catalog.get("sku") or "",
                "name": catalog.get("name") or "",
            }
        )
    now = datetime.utcnow()
    result = {
        "card_id": product_doc.get("_id"),
        "recipe_version": now.isoformat(timespec="microseconds") + "Z",
        "snapshot_at": now,
        "source": "submission_card_recipe",
        "approved": True,
        "card_quantity": card_quantity,
        "components": _normalize_component_rows(component_rows, card_quantity, "submission_card_recipe", now),
    }
    return result


def _valid_outflow_snapshot(package: dict, outflow: dict | None = None) -> tuple[dict | None, bool]:
    if outflow is None:
        outflow = outflows_col.find_one(
            {
                "customer_id": package.get("customer_id"),
                "packaged_product_index": package.get("product_index"),
                "source": "Agent_deliveries",
            },
            {"product_def": 1, "components_deducted": 1, "created_at": 1, "package_def_id": 1},
            sort=[("created_at", -1), ("_id", -1)],
        )
    if not outflow:
        return None, False
    results = outflow.get("components_deducted") or []
    result_statuses = [str(row.get("status") or "") for row in results]
    successful_results = sum(status == "deducted" for status in result_statuses)
    legacy_failed = bool(results) and successful_results == 0
    legacy_proven = bool(results) and successful_results == len(result_statuses)
    legacy_partial = successful_results > 0 and not legacy_proven
    product_def = outflow.get("product_def") or {}
    components = product_def.get("components") or []
    if not components:
        return None, legacy_failed
    quantity = max(1, _integer(package.get("qty"), _integer((package.get("product") or {}).get("quantity"), 1)))
    ids = [_oid(row.get("_id")) for row in components if isinstance(row, dict)]
    catalog = {
        row["_id"]: row
        for row in inventory_products_col.find({"_id": {"$in": [x for x in ids if x]}}, {"sku": 1, "name": 1})
    }
    enriched = []
    for row in components:
        product_id = _oid(row.get("_id"))
        found = catalog.get(product_id, {})
        enriched.append({**row, "sku": found.get("sku") or "", "name": found.get("name") or ""})
    snapshot_at = _dt(outflow.get("created_at")) or datetime.utcnow()
    return {
        "card_id": _oid(outflow.get("package_def_id")) or product_def.get("_id"),
        "recipe_version": f"outflow:{outflow.get('_id')}",
        "snapshot_at": snapshot_at,
        "source": "historical_outflow_snapshot",
        "approved": True,
        "legacy_outflow_id": outflow.get("_id"),
        "legacy_deduction_proven": legacy_proven,
        "legacy_partial_deduction": legacy_partial,
        "card_quantity": quantity,
        "components": _normalize_component_rows(enriched, quantity, "historical_outflow_snapshot", snapshot_at),
    }, legacy_failed


def _current_recipe(package: dict, product_doc: dict | None = None) -> dict | None:
    product = package.get("product") or {}
    product_id = _oid(product.get("_id") or package.get("package_def_id"))
    if not product_id:
        return None
    product_doc = product_doc or products_col.find_one({"_id": product_id}, {"components": 1})
    snapshot = build_submission_recipe_snapshot(product_doc, package.get("qty") or product.get("quantity") or 1)
    if snapshot:
        snapshot["source"] = "current_card_recipe"
        snapshot["approved"] = False
        for component in snapshot["components"]:
            component["recipe_source"] = "current_card_recipe"
    return snapshot


def resolve_recipe(package: dict, outflow: dict | None = None, product_doc: dict | None = None) -> tuple[dict | None, bool]:
    updated = package.get('inventory_recipe_snapshot') or {}
    if updated.get('source') == 'updated_card_recipe' and updated.get('components'):
        return updated, False
    historical, legacy_failed = _valid_outflow_snapshot(package, outflow)
    if historical:
        return historical, legacy_failed
    package_snapshot = package.get("inventory_recipe_snapshot")
    if isinstance(package_snapshot, dict) and package_snapshot.get("components"):
        return package_snapshot, legacy_failed
    return _current_recipe(package, product_doc), legacy_failed


def _active_locations(branch: str) -> list[dict]:
    return list(
        locations_col.find(
            {"branch": branch, "status": {"$regex": "^active$", "$options": "i"}},
            {"name": 1, "code": 1, "branch": 1, "stock_units": 1},
        ).sort([("code", 1), ("name", 1)])
    )


def _location_stock(product: dict, location_id: str) -> tuple[int, float | None]:
    entries = [
        row for row in (product.get("entries") or [])
        if isinstance(row, dict) and str(row.get("location_id") or "") == str(location_id)
    ]
    quantity = sum(_integer(row.get("quantity"), 0) for row in entries)
    valid_costs = [
        row for row in entries
        if _number(row.get("cost_price"), 0) > 0 and (_dt(row.get("updated_at")) or _dt(row.get("created_at")))
    ]
    latest = max(valid_costs, key=lambda row: _dt(row.get("updated_at")) or _dt(row.get("created_at"))) if valid_costs else None
    return max(0, quantity), _number(latest.get("cost_price")) if latest else None


def _can_confirm(identity: dict) -> bool:
    if identity.get("is_main_admin") or identity.get("role") in {"admin", "executive"}:
        return True
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    role = get_effective_inventory_role(user_doc)
    return bool(((role.get("permissions") or {}).get("audit") or {}).get("approve"))


def _serialize_location(row: dict) -> dict:
    return {
        "id": str(row.get("_id") or ""),
        "name": row.get("name") or row.get("code") or "Stock location",
        "code": row.get("code") or "",
        "branch": row.get("branch") or "",
    }


def _package_duplicate_keys(package_ids: list[ObjectId]) -> set[tuple[str, int]]:
    if not package_ids:
        return set()
    relevant = list(packages_col.find({"_id": {"$in": package_ids}}, {"customer_id": 1, "product_index": 1}))
    keys = {(row.get("customer_id"), row.get("product_index")) for row in relevant}
    duplicates: set[tuple[str, int]] = set()
    for customer_id, product_index in keys:
        count = packages_col.count_documents({"customer_id": customer_id, "product_index": product_index}, limit=2)
        if count > 1:
            duplicates.add((str(customer_id), _integer(product_index, -1)))
    return duplicates


def _prepare_order(
    package: dict,
    location_mapping: dict[str, str],
    duplicate_keys: set[tuple[str, int]],
    product_cache: dict[ObjectId, dict] | None = None,
    locations_cache: dict[str, list[dict]] | None = None,
    customer_cache: dict[ObjectId, dict] | None = None,
    deduction_cache: dict[ObjectId, dict] | None = None,
    outflow_cache: dict[tuple[str, int], dict] | None = None,
    card_cache: dict[ObjectId, dict] | None = None,
) -> dict:
    package_id = str(package["_id"])
    branch = str(
        package.get("authoritative_customer_branch")
        or package.get("manager_branch")
        or package.get("agent_branch")
        or ""
    ).strip()
    locations = (
        (locations_cache or {}).get(branch.casefold())
        or (locations_cache or {}).get(branch)
    ) if branch and locations_cache is not None else None
    if locations is None:
        locations = _active_locations(branch) if branch else []
    stored_component_locations = package.get("stock_deduction_component_locations")
    if not isinstance(stored_component_locations, dict):
        stored_component_locations = {}
    product_id = _oid((package.get("product") or {}).get("_id") or package.get("package_def_id"))
    outflow = (outflow_cache or {}).get((str(package.get("customer_id")), _integer(package.get("product_index"), -1)), {}) if outflow_cache is not None else None
    recipe, legacy_failed = resolve_recipe(package, outflow, (card_cache or {}).get(product_id))
    existing = (deduction_cache or {}).get(package["_id"]) if deduction_cache is not None else deductions_col.find_one({"package_id": package["_id"]})
    existing_status = str((existing or {}).get("status") or "")
    fully_deducted = existing_status in {"deducted", "fully_deducted"}
    prior_components = {
        str(row.get("inventory_product_id") or ""): row
        for row in (existing or {}).get("components") or []
        if isinstance(row, dict)
    }
    legacy_proven = bool((recipe or {}).get("legacy_deduction_proven"))
    legacy_partial = bool((recipe or {}).get("legacy_partial_deduction"))
    duplicate = (str(package.get("customer_id")), _integer(package.get("product_index"), -1)) in duplicate_keys
    customer = (customer_cache or {}).get(package.get("customer_id")) if customer_cache is not None else customers_col.find_one({"_id": package.get("customer_id")}, {"purchases": 1})
    purchases = (customer or {}).get("purchases") or []
    index = _integer(package.get("product_index"), -1)
    purchase = purchases[index] if 0 <= index < len(purchases) and isinstance(purchases[index], dict) else {}
    purchase_status = str((purchase.get("product") or {}).get("status") or purchase.get("status") or "").lower()

    exceptions: list[str] = []
    if fully_deducted or legacy_proven:
        status = "Already deducted"
    elif legacy_partial:
        status = "Legacy partial deduction — blocked"
        exceptions.append("Historical outflow shows a mixture of successful and failed component deductions.")
    elif duplicate:
        status = "Duplicate submission"
        exceptions.append("Another package uses the same customer purchase reference.")
    elif purchase_status in {"closed", "cancelled", "canceled", "withdrawn"}:
        status = "Closed/cancelled — blocked"
        exceptions.append("The linked purchase is closed or cancelled.")
    elif not branch or not locations:
        status = "Location required"
        exceptions.append("The customer agent's branch has no active stock location.")
    elif not recipe or not recipe.get("components"):
        status = "Missing inventory link"
        exceptions.append("No valid component recipe could be resolved.")
    elif recipe.get("source") == "current_card_recipe" and not recipe.get("approved"):
        status = "Recipe review required"
        exceptions.append("Historical recipe is unavailable; review and freeze the current card recipe.")
    else:
        status = "Ready to deduct"

    component_rows = []
    aggregate: dict[ObjectId, dict] = {}
    for component in (recipe or {}).get("components") or []:
        product_id = _oid(component.get("inventory_product_id"))
        if not product_id:
            exceptions.append("A recipe component has no valid inventory product ID.")
            continue
        bucket = aggregate.setdefault(product_id, {**component, "required_quantity": 0})
        bucket["required_quantity"] += _integer(component.get("required_quantity"), 0)

    product_ids = list(aggregate)
    if product_cache is None:
        product_cache = {
            row["_id"]: row
            for row in inventory_products_col.find(
                {"_id": {"$in": product_ids}},
                {"sku": 1, "name": 1, "entries": 1},
            )
        }
    total_units = 0
    total_value = 0.0
    shortage_units = 0
    deducted_units = 0
    ready_component_count = 0
    outstanding_component_count = 0
    missing_component_location = False
    selected_location_names: set[str] = set()
    component_location_mappings: dict[str, str] = {}
    for product_id, component in aggregate.items():
        inventory_product = product_cache.get(product_id)
        required = _integer(component.get("required_quantity"), 0)
        prior = prior_components.get(str(product_id)) or {}
        already_deducted = min(required, _integer(prior.get("deducted_quantity"), 0))
        remaining = max(0, required - already_deducted)
        total_units += remaining
        deducted_units += already_deducted
        if remaining:
            outstanding_component_count += 1
        if not inventory_product:
            exceptions.append(f"Missing inventory product {product_id}.")
            component_rows.append({"inventoryProductId": str(product_id), "requiredQuantity": required, "deductedQuantity": already_deducted, "remainingQuantity": remaining, "componentStatus": "Undeducted - missing inventory link", "exception": "Missing inventory link"})
            continue
        component_key = f"{package_id}:{product_id}"
        selected_location_id = str(
            location_mapping.get(component_key)
            or stored_component_locations.get(str(product_id))
            or str(prior.get("location_id") or "")
            or (locations[0]["_id"] if len(locations) == 1 else "")
        )
        selected_location = next((row for row in locations if str(row["_id"]) == selected_location_id), None)
        if not selected_location:
            missing_component_location = True
            exceptions.append(f"Select a {branch} warehouse for {inventory_product.get('name') or product_id}.")
        else:
            component_location_mappings[str(product_id)] = selected_location_id
            selected_location_names.add(
                selected_location.get("name") or selected_location.get("code") or selected_location_id
            )
        available, unit_cost = _location_stock(inventory_product, selected_location_id) if selected_location else (0, None)
        shortage = max(0, remaining - available)
        shortage_units += shortage
        value = remaining * unit_cost if unit_cost is not None else None
        if remaining and unit_cost is None:
            exceptions.append(f"Cost unavailable for {inventory_product.get('name') or product_id}.")
        if remaining and shortage:
            exceptions.append(f"Insufficient stock for {inventory_product.get('name') or product_id}.")
        component_ready = bool(remaining and selected_location and unit_cost is not None and available >= remaining)
        if component_ready:
            ready_component_count += 1
        if component_ready and value is not None:
            total_value += value
        component_rows.append(
            {
                "inventoryProductId": str(product_id),
                "name": inventory_product.get("name") or component.get("component_name") or "Inventory product",
                "sku": inventory_product.get("sku") or component.get("sku") or "",
                "quantityPerCard": component.get("quantity_per_card"),
                "cardQuantity": component.get("card_quantity"),
                "requiredQuantity": required,
                "deductedQuantity": already_deducted,
                "remainingQuantity": remaining,
                "availableQuantity": available,
                "afterQuantity": available - remaining,
                "shortage": shortage,
                "unitCost": unit_cost,
                "totalCost": value,
                "locationId": selected_location_id,
                "locationName": (selected_location or {}).get("name") or (selected_location or {}).get("code") or "",
                "locations": [_serialize_location(row) for row in locations],
                "componentStatus": (
                    "Deducted"
                    if remaining == 0
                    else "Ready to deduct"
                    if component_ready
                    else "Undeducted - warehouse required"
                    if not selected_location
                    else "Undeducted - cost unavailable"
                    if unit_cost is None
                    else "Undeducted - insufficient stock"
                ),
            }
        )

    base_processable = status == "Ready to deduct" or existing_status == "partially_deducted"
    if base_processable:
        if outstanding_component_count == 0:
            status = "Already deducted"
        elif ready_component_count and ready_component_count < outstanding_component_count:
            status = "Ready for partial deduction"
        elif ready_component_count == outstanding_component_count:
            status = "Ready to deduct" if not existing else "Ready to deduct remaining items"
        elif missing_component_location:
            status = "Location required"
        elif any("Missing inventory" in value for value in exceptions):
            status = "Missing inventory link"
        elif any("Cost unavailable" in value for value in exceptions):
            status = "Cost unavailable"
        elif shortage_units:
            status = "Insufficient stock"
    if legacy_failed and not fully_deducted and ready_component_count:
        # Preserve the warning while allowing an otherwise valid order to proceed.
        exceptions.append("Legacy submission-time attempt failed and did not deduct stock.")

    submitted = _submission_time(package)
    product = package.get("product") or {}
    result = {
        "id": package_id,
        "packageReference": f"PKG-{package_id[-8:].upper()}",
        "customerId": str(package.get("customer_id") or ""),
        "customerName": package.get("customer_name") or "Customer",
        "productIndex": package.get("product_index"),
        "productCardId": str(product.get("_id") or package.get("package_def_id") or ""),
        "productCard": product.get("name") or product.get("package_name") or "Product card",
        "cardQuantity": max(1, _integer(package.get("qty"), _integer(product.get("quantity"), 1))),
        "submittedAt": submitted.isoformat() if submitted else "",
        "deliveryStatus": package.get("status") or "pending",
        "branch": branch,
        "locations": [_serialize_location(row) for row in locations],
        "locationId": (
            next(iter(set(component_location_mappings.values())))
            if len(set(component_location_mappings.values())) == 1
            else ""
        ),
        "locationName": ", ".join(sorted(selected_location_names)),
        "componentLocationMappings": component_location_mappings,
        "deductionStatus": status,
        "eligibilityStatus": status,
        "selectable": ready_component_count > 0 and not bool(legacy_proven or legacy_partial or duplicate),
        "legacyFailedAttempt": legacy_failed,
        "recipeSource": (recipe or {}).get("source") or "",
        "recipeReviewRequired": bool(recipe and recipe.get("source") == "current_card_recipe" and not recipe.get("approved")),
        "componentCount": len(component_rows),
        "requiredUnits": total_units,
        "deductedUnits": deducted_units,
        "remainingUnits": total_units,
        "totalCost": round(total_value, 2),
        "shortageUnits": shortage_units,
        "exceptions": exceptions,
        "components": component_rows,
        "deductionReference": (existing or {}).get("deduction_batch_id") or (f"LEGACY-{str((recipe or {}).get('legacy_outflow_id') or '')[-8:].upper()}" if legacy_proven else ""),
        "deductionId": str((existing or {}).get("_id") or ((recipe or {}).get("legacy_outflow_id") if legacy_proven else "") or ""),
        "canConfirm": not bool(fully_deducted or legacy_proven or legacy_partial),
        "statusHistory": _json_value(package.get("status_history") or []),
    }
    if legacy_failed and not existing:
        result["deductionStatus"] = "Legacy failed attempt - not deducted"
    return result


def preview_stock_deductions(payload: dict, identity: dict) -> dict:
    start, end = parse_ghana_range(payload.get("fromDate"), payload.get("toDate"))
    page = max(1, _integer(payload.get("page"), 1))
    per_page = min(100, max(1, _integer(payload.get("perPage"), 25)))
    location_mapping = payload.get("locationMappings") if isinstance(payload.get("locationMappings"), dict) else {}
    query: dict[str, Any] = _date_query(start, end)
    branch = str(payload.get("branch") or "").strip()
    delivery_status = str(payload.get("deliveryStatus") or "").strip()
    customer_search = str(payload.get("customer") or "").strip()
    product_search = str(payload.get("productCard") or "").strip()
    if delivery_status:
        query = {"$and": [query, {"status": delivery_status}]}
    if customer_search:
        query = {"$and": [query, {"customer_name": {"$regex": customer_search, "$options": "i"}}]}
    if product_search:
        query = {"$and": [query, {"$or": [{"product.name": {"$regex": product_search, "$options": "i"}}, {"product.package_name": {"$regex": product_search, "$options": "i"}}]}]}

    projection = {
        "customer_id": 1, "customer_name": 1, "product_index": 1, "product": 1,
        "package_def_id": 1, "qty": 1, "status": 1, "created_at": 1, "submitted_at": 1,
        "manager_branch": 1, "agent_branch": 1, "manager_id": 1, "status_history": 1,
        "inventory_recipe_snapshot": 1, "stock_deduction_location_id": 1,
        "stock_deduction_component_locations": 1,
    }
    all_rows = list(packages_col.find(query, projection).sort([("created_at", -1), ("submitted_at", -1), ("_id", -1)]).limit(5000))
    duplicate_keys = _package_duplicate_keys([row["_id"] for row in all_rows])
    customer_ids = list({row.get("customer_id") for row in all_rows if row.get("customer_id")})
    customer_cache = {
        row["_id"]: row
        for row in customers_col.find(
            {"_id": {"$in": customer_ids}},
            {"purchases": 1, "agent_id": 1},
        )
    }
    agent_ids = {
        _oid(row.get("agent_id"))
        for row in customer_cache.values()
        if _oid(row.get("agent_id"))
    }
    agent_cache = {
        row["_id"]: row
        for row in users_col.find(
            {"_id": {"$in": list(agent_ids)}},
            {"branch": 1, "role": 1, "name": 1},
        )
    } if agent_ids else {}
    for package in all_rows:
        customer = customer_cache.get(package.get("customer_id")) or {}
        agent = agent_cache.get(_oid(customer.get("agent_id"))) or {}
        package["authoritative_customer_branch"] = str(agent.get("branch") or "").strip()
    if branch:
        all_rows = [
            row for row in all_rows
            if str(
                row.get("authoritative_customer_branch")
                or row.get("manager_branch")
                or row.get("agent_branch")
                or ""
            ).strip().casefold() == branch.casefold()
        ]
    deduction_cache = {
        row["package_id"]: row
        for row in deductions_col.find({"package_id": {"$in": [item["_id"] for item in all_rows]}})
    }
    branches_in_range = {
        str(
            row.get("authoritative_customer_branch")
            or row.get("manager_branch")
            or row.get("agent_branch")
            or ""
        ).strip()
        for row in all_rows
    }
    locations_cache: dict[str, list[dict]] = defaultdict(list)
    eligible_location_ids: set[str] = set()
    for location in locations_col.find(
        {"branch": {"$in": [value for value in branches_in_range if value]}, "status": {"$regex": "^active$", "$options": "i"}},
        {"name": 1, "code": 1, "branch": 1, "stock_units": 1},
    ).sort([("code", 1), ("name", 1)]):
        locations_cache[str(location.get("branch") or "").strip().casefold()].append(location)
        eligible_location_ids.add(str(location["_id"]))
    outflow_cache: dict[tuple[str, int], dict] = {}
    for outflow in outflows_col.find(
        {"customer_id": {"$in": customer_ids}, "source": "Agent_deliveries"},
        {"customer_id": 1, "packaged_product_index": 1, "product_def": 1, "components_deducted": 1, "created_at": 1, "package_def_id": 1},
    ).sort([("created_at", -1), ("_id", -1)]):
        outflow_cache.setdefault((str(outflow.get("customer_id")), _integer(outflow.get("packaged_product_index"), -1)), outflow)
    card_ids = [_oid((row.get("product") or {}).get("_id") or row.get("package_def_id")) for row in all_rows]
    card_cache = {row["_id"]: row for row in products_col.find({"_id": {"$in": [value for value in card_ids if value]}}, {"components": 1})}
    required_inventory_ids: set[ObjectId] = set()
    for package in all_rows:
        package_key = (
            str(package.get("customer_id")),
            _integer(package.get("product_index"), -1),
        )
        candidate_recipes = [
            (outflow_cache.get(package_key) or {}).get("product_def"),
            package.get("inventory_recipe_snapshot"),
            card_cache.get(_oid((package.get("product") or {}).get("_id") or package.get("package_def_id"))),
        ]
        for recipe in candidate_recipes:
            if not isinstance(recipe, dict):
                continue
            for component in recipe.get("components") or []:
                if not isinstance(component, dict):
                    continue
                component_id = _oid(
                    component.get("inventory_product_id")
                    or component.get("_id")
                    or component.get("inventory_id")
                )
                if component_id:
                    required_inventory_ids.add(component_id)
    inventory_product_cache = {
        row["_id"]: row
        for row in inventory_products_col.aggregate(
            [
                {"$match": {"_id": {"$in": list(required_inventory_ids)}}},
                {
                    "$project": {
                        "sku": 1,
                        "name": 1,
                        "entries": {
                            "$filter": {
                                "input": {"$ifNull": ["$entries", []]},
                                "as": "entry",
                                "cond": {
                                    "$in": [
                                        {"$toString": {"$ifNull": ["$$entry.location_id", ""]}},
                                        list(eligible_location_ids),
                                    ]
                                },
                            }
                        },
                    }
                },
            ]
        )
    } if required_inventory_ids else {}
    orders = [
        _prepare_order(
            row,
            location_mapping,
            duplicate_keys,
            inventory_product_cache,
            locations_cache,
            customer_cache,
            deduction_cache,
            outflow_cache,
            card_cache,
        )
        for row in all_rows
    ]
    deduction_status = str(payload.get("deductionStatus") or "").strip()
    component_search = str(payload.get("inventoryComponent") or "").strip().lower()
    location_filter = str(payload.get("location") or "").strip()
    if deduction_status:
        orders = [row for row in orders if row["eligibilityStatus"] == deduction_status or row["deductionStatus"] == deduction_status]
    if component_search:
        orders = [row for row in orders if any(component_search in f"{c.get('name','')} {c.get('sku','')}".lower() for c in row["components"])]
    if location_filter:
        orders = [row for row in orders if row["locationId"] == location_filter]

    component_summary: dict[tuple[str, str], dict] = {}
    for order in orders:
        for component in order["components"]:
            key = (component["inventoryProductId"], component.get("locationId") or "")
            bucket = component_summary.setdefault(
                key,
                {
                    **component,
                    "name": component.get("name") or "Missing inventory product",
                    "sku": component.get("sku") or "",
                    "availableQuantity": component.get("availableQuantity") or 0,
                    "unitCost": component.get("unitCost"),
                    "requiredQuantity": 0,
                    "totalCost": 0.0,
                    "orderIds": [],
                    "affectedOrders": 0,
                },
            )
            bucket["requiredQuantity"] += component["requiredQuantity"]
            bucket["totalCost"] += component.get("totalCost") or 0
            bucket["orderIds"].append(order["id"])
            bucket["affectedOrders"] += 1
    for component in component_summary.values():
        component["afterQuantity"] = component["availableQuantity"] - component["requiredQuantity"]
        component["shortage"] = max(0, component["requiredQuantity"] - component["availableQuantity"])
        component["totalCost"] = round(component["totalCost"], 2)

    summary = {
        "totalSubmittedOrders": len(orders),
        "awaitingDeduction": sum(row["deductionStatus"] not in {"Already deducted"} for row in orders),
        "readyToDeduct": sum(row["selectable"] for row in orders),
        "alreadyDeducted": sum(row["deductionStatus"] == "Already deducted" for row in orders),
        "partiallyDeducted": sum("partial" in row["deductionStatus"].lower() or (row.get("deductedUnits", 0) > 0 and row["deductionStatus"] != "Already deducted") for row in orders),
        "distinctProducts": len({component["inventoryProductId"] for row in orders for component in row["components"]}),
        "totalComponentUnits": sum(row["requiredUnits"] for row in orders),
        "readyCostValue": round(sum(row["totalCost"] for row in orders if row["selectable"]), 2),
        "insufficientProducts": sum(1 for component in component_summary.values() if component["shortage"] > 0),
        "shortageUnits": sum(component["shortage"] for component in component_summary.values()),
        "exceptions": sum(bool(row["exceptions"]) and not row["deductionId"] for row in orders),
    }
    total = len(orders)
    start_index = (page - 1) * per_page
    branches = sorted({row["branch"] for row in orders if row["branch"]})
    return {
        "summary": summary,
        "orders": orders[start_index:start_index + per_page],
        "allReadyOrderIds": [row["id"] for row in orders if row["selectable"]],
        "components": sorted(component_summary.values(), key=lambda row: (row.get("name") or "").lower()),
        "branches": branches,
        "pagination": {"page": page, "perPage": per_page, "total": total, "pages": max(1, (total + per_page - 1) // per_page)},
        "canConfirm": _can_confirm(identity),
        "dateRange": {"from": payload.get("fromDate"), "to": payload.get("toDate")},
    }


def freeze_package_recipe(package_id: str, payload: dict, identity: dict) -> dict:
    package_oid = _oid(package_id)
    package = packages_col.find_one({"_id": package_oid}) if package_oid else None
    if not package:
        raise StockDeductionError("Package not found.", "not_found", 404)
    if not _can_confirm(identity):
        raise StockDeductionError("You do not have approval permission for stock deductions.", "forbidden", 403)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise StockDeductionError("A review reason is required before freezing a current recipe.", "reason_required")
    recipe = _current_recipe(package)
    if not recipe or not recipe.get("components"):
        raise StockDeductionError("The current product card has no valid inventory recipe.", "missing_recipe")
    now = datetime.utcnow()
    recipe.update(
        {
            "approved": True,
            "approved_at": now,
            "approved_by": {
                "user_id": identity.get("user_id"),
                "name": identity.get("name"),
                "role": identity.get("role"),
            },
            "approval_reason": reason,
            "source": "reviewed_current_card_recipe",
        }
    )
    packages_col.update_one({"_id": package["_id"]}, {"$set": {"inventory_recipe_snapshot": recipe, "updated_at": now}})
    return recipe


def _transaction_error(exc: Exception) -> StockDeductionError:
    message = str(exc)
    if isinstance(exc, OperationFailure) and (
        "Transaction numbers are only allowed" in message
        or "does not support transactions" in message.lower()
        or exc.code in {20, 251}
    ):
        return StockDeductionError(
            "MongoDB transactions are unavailable. Stock deduction was not attempted.",
            "transactions_unavailable",
            503,
        )
    if isinstance(exc, StockDeductionError):
        return exc
    return StockDeductionError(message or "Stock deduction failed.", "deduction_failed", 409)


def _deduct_one(
    package_id: str,
    component_locations: dict,
    batch_id: str,
    payload: dict,
    identity: dict,
    *,
    mark_delivered: bool = False,
) -> dict:
    workflow_doc = deductions_col.find_one({"package_id": _oid(package_id), "workflow": "packaging"})
    if workflow_doc:
        raise StockDeductionError("Use Settle in Partially Delivered for this card.", "use_settlement", 409)
    package_oid = _oid(package_id)
    if not package_oid or not isinstance(component_locations, dict):
        raise StockDeductionError("A valid package and component warehouse selections are required.", "invalid_reference")
    idempotency_key = f"delivery-confirmation:{package_oid}"
    existing = deductions_col.find_one({"idempotency_key": idempotency_key, "status": "deducted"})
    if existing:
        return {"packageId": package_id, "status": "existing", "deductionId": str(existing["_id"]), "batchId": existing.get("deduction_batch_id")}

    client = db.client
    try:
        with client.start_session() as mongo_session:
            def transaction_body(session):
                package = packages_col.find_one({"_id": package_oid}, session=session)
                if not package:
                    raise StockDeductionError("Package no longer exists.", "package_missing", 404)
                duplicate = packages_col.count_documents(
                    {"customer_id": package.get("customer_id"), "product_index": package.get("product_index")},
                    limit=2,
                    session=session,
                )
                if duplicate > 1:
                    raise StockDeductionError("Duplicate package records require manual review.", "duplicate_submission")
                existing_tx = deductions_col.find_one({"idempotency_key": idempotency_key}, session=session)
                if existing_tx and existing_tx.get("status") in {"deducted", "fully_deducted"}:
                    return existing_tx

                customer = customers_col.find_one(
                    {"_id": package.get("customer_id")},
                    {"purchases": 1, "agent_id": 1},
                    session=session,
                )
                agent_id = _oid((customer or {}).get("agent_id"))
                agent = users_col.find_one(
                    {"_id": agent_id},
                    {"branch": 1, "role": 1},
                    session=session,
                ) if agent_id else None
                if mark_delivered:
                    actor_oid = _oid(identity.get("user_id"))
                    if not actor_oid or actor_oid != agent_id or str(identity.get("role") or "").lower() != "agent":
                        raise StockDeductionError(
                            "Only the agent responsible for this customer can mark the product delivered.",
                            "delivery_forbidden",
                            403,
                        )
                branch = str(
                    (agent or {}).get("branch")
                    or package.get("manager_branch")
                    or package.get("agent_branch")
                    or ""
                ).strip()
                if not branch:
                    raise StockDeductionError("The customer's agent branch could not be resolved.", "branch_required")
                index = _integer(package.get("product_index"), -1)
                purchases = (customer or {}).get("purchases") or []
                purchase = purchases[index] if 0 <= index < len(purchases) else {}
                purchase_status = str((purchase.get("product") or {}).get("status") or purchase.get("status") or "").lower()
                if purchase_status in {"closed", "cancelled", "canceled", "withdrawn"}:
                    raise StockDeductionError("Closed or cancelled purchases cannot be deducted.", "purchase_blocked")

                recipe, _ = resolve_recipe(package)
                if not recipe or not recipe.get("components"):
                    raise StockDeductionError("No frozen component recipe is available.", "missing_recipe")
                if recipe.get("legacy_deduction_proven"):
                    raise StockDeductionError("Historical stock movements prove this package was already deducted.", "already_deducted")
                if recipe.get("legacy_partial_deduction"):
                    raise StockDeductionError("Historical partial deduction requires manual reconciliation.", "legacy_partial_deduction")
                if recipe.get("source") == "current_card_recipe" and not recipe.get("approved"):
                    raise StockDeductionError("Review and freeze the current card recipe before deduction.", "recipe_review_required")

                required_by_product: dict[ObjectId, int] = defaultdict(int)
                recipe_by_product: dict[ObjectId, dict] = {}
                for component in recipe["components"]:
                    product_id = _oid(component.get("inventory_product_id"))
                    if not product_id:
                        raise StockDeductionError("Recipe contains a missing inventory link.", "missing_inventory_link")
                    required_by_product[product_id] += _integer(component.get("required_quantity"), 0)
                    recipe_by_product[product_id] = component

                prior_component_map = {
                    str(row.get("inventory_product_id") or ""): row
                    for row in (existing_tx or {}).get("components") or []
                    if isinstance(row, dict)
                }
                location_ids_by_product: dict[ObjectId, ObjectId] = {}
                for product_id in required_by_product:
                    location_oid = _oid(component_locations.get(str(product_id)))
                    if location_oid:
                        location_ids_by_product[product_id] = location_oid
                selected_location_ids = set(location_ids_by_product.values())
                location_map = {
                    row["_id"]: row
                    for row in locations_col.find(
                        {
                            "_id": {"$in": list(selected_location_ids)},
                            "status": {"$regex": "^active$", "$options": "i"},
                        },
                        session=session,
                    )
                }
                if selected_location_ids and len(location_map) != len(selected_location_ids):
                    raise StockDeductionError("One or more selected warehouses are not active.", "location_required")
                if any(str(location.get("branch") or "").strip().casefold() != branch.casefold() for location in location_map.values()):
                    raise StockDeductionError(
                        "Every component warehouse must belong to the customer's agent branch.",
                        "cross_branch_location",
                    )

                now = datetime.utcnow()
                deduction_id = (existing_tx or {}).get("_id") or ObjectId()
                movement_rows = []
                component_results = []
                total_units = 0
                total_cost = 0.0
                units_by_location: dict[ObjectId, int] = defaultdict(int)
                for product_id, required in required_by_product.items():
                    prior = prior_component_map.get(str(product_id)) or {}
                    prior_deducted = min(required, _integer(prior.get("deducted_quantity"), 0))
                    remaining = max(0, required - prior_deducted)
                    if remaining == 0:
                        component_results.append({**prior, "inventory_product_id": product_id, "required_quantity": required, "deducted_quantity": required, "remaining_quantity": 0, "status": "deducted"})
                        continue
                    location_oid = location_ids_by_product.get(product_id)
                    location = location_map.get(location_oid) if location_oid else None
                    if not location:
                        component_results.append({**prior, "inventory_product_id": product_id, "required_quantity": required, "deducted_quantity": prior_deducted, "remaining_quantity": remaining, "status": "undeducted", "reason": "warehouse_required"})
                        continue
                    product = inventory_products_col.find_one({"_id": product_id}, session=session)
                    if not product:
                        component_results.append({**prior, "inventory_product_id": product_id, "required_quantity": required, "deducted_quantity": prior_deducted, "remaining_quantity": remaining, "status": "undeducted", "reason": "missing_inventory_link", "location_id": location_oid})
                        continue
                    before, unit_cost = _location_stock(product, str(location_oid))
                    if unit_cost is None:
                        component_results.append({**prior, "inventory_product_id": product_id, "required_quantity": required, "deducted_quantity": prior_deducted, "remaining_quantity": remaining, "status": "undeducted", "reason": "cost_unavailable", "location_id": location_oid})
                        continue
                    if remaining <= 0 or before < remaining:
                        component_results.append({**prior, "inventory_product_id": product_id, "required_quantity": required, "deducted_quantity": prior_deducted, "remaining_quantity": remaining, "status": "undeducted", "reason": "insufficient_stock", "location_id": location_oid, "available_quantity": before})
                        continue
                    after = before - remaining
                    entry = {
                        "branch": location.get("branch") or branch,
                        "location_id": str(location_oid),
                        "location_name": location.get("name") or "",
                        "location_code": location.get("code") or "",
                        "quantity": -remaining,
                        "cost_price": unit_cost,
                        "selling_price": 0,
                        "created_at": now,
                        "updated_at": now,
                        "movement_type": "delivery_stock_deduction",
                        "deduction_id": str(deduction_id),
                        "package_id": str(package_oid),
                        "batch_id": batch_id,
                        "idempotency_key": idempotency_key,
                    }
                    inventory_products_col.update_one(
                        {"_id": product_id},
                        {"$push": {"entries": entry}, "$set": {"updated_at": now}},
                        session=session,
                    )
                    movement_id = ObjectId()
                    movement = {
                        "_id": movement_id,
                        "movement_type": "delivery_stock_deduction",
                        "deduction_id": deduction_id,
                        "package_id": package_oid,
                        "customer_id": package.get("customer_id"),
                        "product_index": package.get("product_index"),
                        "product_id": product_id,
                        "sku": product.get("sku") or recipe_by_product[product_id].get("sku") or "",
                        "product_name": product.get("name") or recipe_by_product[product_id].get("component_name") or "",
                        "branch": location.get("branch") or branch,
                        "location_id": location_oid,
                        "before_quantity": before,
                        "deducted_quantity": remaining,
                        "after_quantity": after,
                        "unit_cost": unit_cost,
                        "total_cost": round(remaining * unit_cost, 2),
                        "actor": {"user_id": identity.get("user_id"), "name": identity.get("name"), "role": identity.get("role")},
                        "created_at": now,
                    }
                    movements_col.insert_one(movement, session=session)
                    movement_rows.append(movement)
                    total_units += remaining
                    total_cost += remaining * unit_cost
                    units_by_location[location_oid] += remaining
                    component_results.append({
                        "inventory_product_id": product_id,
                        "sku": movement["sku"],
                        "name": movement["product_name"],
                        "required_quantity": required,
                        "deducted_quantity": required,
                        "remaining_quantity": 0,
                        "unit_cost": unit_cost,
                        "total_cost": round(required * unit_cost, 2),
                        "before_quantity": before,
                        "after_quantity": after,
                        "location_id": location_oid,
                        "movement_id": movement_id,
                        "status": "deducted",
                    })

                if not movement_rows:
                    raise StockDeductionError(
                        "No outstanding component currently has sufficient stock, cost and warehouse selection.",
                        "no_ready_components",
                    )

                if mark_delivered and any(_integer(row.get("remaining_quantity"), 0) > 0 for row in component_results):
                    raise StockDeductionError(
                        "All product-card components must be available before delivery can be completed.",
                        "incomplete_delivery_stock",
                    )

                for location_oid, location_units in units_by_location.items():
                    location_result = locations_col.update_one(
                        {"_id": location_oid, "stock_units": {"$gte": location_units}},
                        {"$inc": {"stock_units": -location_units}, "$set": {"updated_at": now}},
                        session=session,
                    )
                    if location_result.modified_count != 1:
                        raise StockDeductionError(
                            "Warehouse stock changed during confirmation. Refresh and retry.",
                            "stock_conflict",
                        )

                session_number = f"SD-{now.strftime('%Y%m%d-%H%M%S')}-{str(package_oid)[-6:].upper()}"
                stock_sessions_col.insert_one(
                    {
                        "session_number": session_number,
                        "branch": branch,
                        "locations": [
                            {
                                "location_id": str(location_id),
                                "location_name": location_map[location_id].get("name") or "",
                                "location_code": location_map[location_id].get("code") or "",
                                "total_units": units,
                            }
                            for location_id, units in units_by_location.items()
                        ],
                        "reason": f"Delivery stock deduction for package {package_oid}",
                        "status": "closed",
                        "source": "stock_deduction",
                        "package_id": package_oid,
                        "deduction_id": deduction_id,
                        "created_at": now,
                        "closed_at": now,
                        "created_by": {"user_id": identity.get("user_id"), "name": identity.get("name"), "role": identity.get("role")},
                        "summary": {"updates": len(movement_rows), "totalUnits": total_units, "totalValue": round(total_cost, 2)},
                    },
                    session=session,
                )
                fully_complete = all(_integer(row.get("remaining_quantity"), 0) == 0 for row in component_results)
                deduction_doc = {
                    "_id": deduction_id,
                    "package_id": package_oid,
                    "customer_id": package.get("customer_id"),
                    "product_index_snapshot": package.get("product_index"),
                    "idempotency_key": idempotency_key,
                    "deduction_batch_id": batch_id,
                    "branch": branch,
                    "component_locations": {
                        str(product_id): location_id
                        for product_id, location_id in location_ids_by_product.items()
                    },
                    "locations": [
                        {
                            "location_id": location_id,
                            "location_name": location_map[location_id].get("name") or "",
                            "location_code": location_map[location_id].get("code") or "",
                            "total_units": units,
                        }
                        for location_id, units in units_by_location.items()
                    ],
                    "recipe": recipe,
                    "recipe_source": recipe.get("source"),
                    "components": component_results,
                    "total_units": _integer((existing_tx or {}).get("total_units"), 0) + total_units,
                    "total_cost_value": round(_number((existing_tx or {}).get("total_cost_value"), 0) + total_cost, 2),
                    "movement_ids": list((existing_tx or {}).get("movement_ids") or []) + [row["_id"] for row in movement_rows],
                    "confirmed_by": {"user_id": identity.get("user_id"), "name": identity.get("name"), "role": identity.get("role")},
                    "confirmed_at": now,
                    "date_range": {"from": payload.get("fromDate"), "to": payload.get("toDate")},
                    "status": "deducted" if fully_complete else "partially_deducted",
                }
                if existing_tx:
                    deductions_col.replace_one({"_id": deduction_id}, deduction_doc, session=session)
                else:
                    deductions_col.insert_one(deduction_doc, session=session)
                package_updates = {
                    "stock_deduction_id": deduction_id,
                    "stock_deduction_status": "deducted" if fully_complete else "partially_deducted",
                    "stock_deduction_component_locations": {
                        str(product_id): str(location_id)
                        for product_id, location_id in location_ids_by_product.items()
                    },
                    "stock_deducted_at": now,
                    "stock_deducted_by": identity.get("user_id"),
                }
                if mark_delivered:
                    package_updates.update({
                        "status": "delivered",
                        "delivered_at": now,
                        "delivered_by": identity.get("user_id"),
                        "delivery_branch": branch,
                        "delivery_location_ids": [str(value) for value in units_by_location],
                        "updated_at": now,
                    })
                package_update_doc: dict[str, Any] = {"$set": package_updates}
                if mark_delivered:
                    package_update_doc["$push"] = {"status_history": {
                        "from": package.get("status") or "pending",
                        "to": "delivered",
                        "at": now,
                        "by": identity.get("user_id"),
                        "role": "agent",
                        "source": "customer_profile_mark_delivered",
                    }}
                packages_col.update_one(
                    {"_id": package_oid},
                    package_update_doc,
                    session=session,
                )
                if mark_delivered:
                    from services.delivery_followups import ensure_delivery_followup
                    ensure_delivery_followup(db, package=package, customer=customer,
                        trigger="delivered", event_at=now, session=session)
                    customer_result = customers_col.update_one(
                        {
                            "_id": package.get("customer_id"),
                            f"purchases.{index}.product.status": {"$nin": ["delivered", "closed", "cancelled", "canceled"]},
                        },
                        {"$set": {
                            f"purchases.{index}.status": "delivered",
                            f"purchases.{index}.product.status": "delivered",
                            f"purchases.{index}.product.packaging_status": "delivered",
                            f"purchases.{index}.delivered_at": now,
                            f"purchases.{index}.product.delivered_at": now,
                            f"purchases.{index}.delivered_by": identity.get("user_id"),
                            f"purchases.{index}.delivery_package_id": package_oid,
                            f"purchases.{index}.stock_deduction_id": deduction_id,
                            "updated_at": now,
                        }},
                        session=session,
                    )
                    if customer_result.modified_count != 1:
                        raise StockDeductionError(
                            "The customer product changed while delivery was being confirmed. Refresh and retry.",
                            "delivery_conflict",
                            409,
                        )
                    outflows_col.update_one(
                        {
                            "customer_id": package.get("customer_id"),
                            "packaged_product_index": package.get("product_index"),
                            "source": "Agent_deliveries",
                        },
                        {"$set": {
                            "components_deducted": component_results,
                            "components_status": "deducted",
                            "deduction_deferred": False,
                            "stock_deduction_id": deduction_id,
                            "delivered_at": now,
                            "delivery_branch": branch,
                        }},
                        session=session,
                    )
                activity_logs_col.insert_one(
                    {
                        "user_id": str(identity.get("user_id") or ""),
                        "username": identity.get("name") or "",
                        "role": identity.get("role") or "",
                        "action": "inventory_delivery_stock_deducted",
                        "action_label": "Confirmed and deducted delivery stock",
                        "entity_type": "package",
                        "entity_id": str(package_oid),
                        "meta": {
                            "deduction_id": str(deduction_id),
                            "batch_id": batch_id,
                            "branch": branch,
                            "component_locations": {
                                str(product_id): str(location_id)
                                for product_id, location_id in location_ids_by_product.items()
                            },
                            "total_units": total_units,
                            "total_cost": round(total_cost, 2),
                        },
                        "timestamp": now,
                        "day": now.strftime("%Y-%m-%d"),
                        "month": now.strftime("%Y-%m"),
                    },
                    session=session,
                )
                return deduction_doc

            result = mongo_session.with_transaction(transaction_body)
            return {
                "packageId": package_id,
                "status": "deducted" if result.get("status") == "deducted" else "partially_deducted",
                "deductionId": str(result["_id"]),
                "batchId": batch_id,
            }
    except DuplicateKeyError:
        existing = deductions_col.find_one({"idempotency_key": idempotency_key, "status": "deducted"})
        if existing:
            return {"packageId": package_id, "status": "existing", "deductionId": str(existing["_id"]), "batchId": existing.get("deduction_batch_id")}
        raise StockDeductionError("A concurrent deduction is still being finalized. Refresh before retrying.", "deduction_conflict", 409)
    except Exception as exc:
        raise _transaction_error(exc) from exc


def deliver_package_and_deduct(customer_id: str, product_index: int, identity: dict, photo=None) -> dict:
    """Complete delivery only after Packaging stock deductions are fully settled."""
    if str(identity.get("role") or "").lower() != "agent" or not _oid(identity.get("user_id")):
        raise StockDeductionError("Only an authenticated agent can mark a product delivered.", "forbidden", 403)
    customer_oid = _oid(customer_id)
    if not customer_oid or product_index < 0:
        raise StockDeductionError("Invalid customer product reference.", "invalid_reference")

    package = packages_col.find_one(
        {
            "customer_id": customer_oid,
            "product_index": product_index,
            "status": {"$ne": "cancelled"},
        },
        sort=[("created_at", -1), ("_id", -1)],
    )
    if not package:
        raise StockDeductionError("Submit this product for packaging before marking it delivered.", "package_missing", 404)

    customer = customers_col.find_one({"_id": customer_oid}, {"agent_id": 1, "purchases": 1})
    if not customer or _oid(customer.get("agent_id")) != _oid(identity.get("user_id")):
        raise StockDeductionError("You can only deliver products for your assigned customers.", "delivery_forbidden", 403)
    purchases = customer.get("purchases") or []
    if product_index >= len(purchases):
        raise StockDeductionError("Customer product no longer exists.", "purchase_missing", 404)
    from services.delivery_evidence import upload_delivery_photo
    evidence = upload_delivery_photo(photo, identity)
    from .packaging_stock import transition
    return transition(str(package["_id"]), "delivered", identity, delivery_evidence=evidence)


def confirm_stock_deductions(payload: dict, identity: dict) -> dict:
    if not _can_confirm(identity):
        raise StockDeductionError("You do not have approval permission for stock deductions.", "forbidden", 403)
    parse_ghana_range(payload.get("fromDate"), payload.get("toDate"))
    orders = payload.get("orders") or []
    if not isinstance(orders, list) or not orders:
        raise StockDeductionError("Select at least one ready order.", "empty_selection")
    batch_id = f"SDB-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
    results = []
    for row in orders:
        try:
            results.append(
                _deduct_one(
                    str(row.get("packageId") or ""),
                    row.get("componentLocations") or {},
                    batch_id,
                    payload,
                    identity,
                )
            )
        except StockDeductionError as exc:
            results.append({"packageId": str(row.get("packageId") or ""), "status": "blocked", "code": exc.code, "error": str(exc)})
    return {
        "batchId": batch_id,
        "results": results,
        "deducted": sum(row["status"] == "deducted" for row in results),
        "partiallyDeducted": sum(row["status"] == "partially_deducted" for row in results),
        "existing": sum(row["status"] == "existing" for row in results),
        "blocked": sum(row["status"] == "blocked" for row in results),
    }


def deduction_detail(package_id: str) -> dict:
    package_oid = _oid(package_id)
    package = packages_col.find_one({"_id": package_oid}, {"customer_phone": 0}) if package_oid else None
    if not package:
        raise StockDeductionError("Package not found.", "not_found", 404)
    deduction = deductions_col.find_one({"package_id": package_oid})
    audit_events = list(activity_logs_col.find({"entity_type": "package", "entity_id": str(package_oid)}, {"ip": 0, "user_agent": 0}).sort("timestamp", -1).limit(50))
    return {
        "package": _json_value(package),
        "deduction": _json_value(deduction),
        "auditEvents": _json_value(audit_events),
    }


def export_stock_deductions_csv(payload: dict, identity: dict) -> bytes:
    payload = {**payload, "page": 1, "perPage": 100}
    preview = preview_stock_deductions(payload, identity)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Stock Deduction Report"])
    writer.writerow(["From", payload.get("fromDate"), "To", payload.get("toDate"), "Generated UTC", datetime.utcnow().isoformat()])
    writer.writerow([])
    writer.writerow(["Package", "Customer", "Product Card", "Quantity", "Submitted", "Delivery Status", "Branch", "Location", "Deduction Status", "Component Units", "Cost Value", "Shortage", "Batch"])
    for row in preview["orders"]:
        writer.writerow([
            row["packageReference"], row["customerName"], row["productCard"], row["cardQuantity"],
            row["submittedAt"], row["deliveryStatus"], row["branch"], row["locationName"],
            row["deductionStatus"], row["requiredUnits"], row["totalCost"], row["shortageUnits"],
            row["deductionReference"],
        ])
    writer.writerow([])
    writer.writerow(["Component", "SKU", "Location", "Required", "Available", "After", "Shortage", "Unit Cost", "Total Cost", "Orders"])
    for row in preview["components"]:
        writer.writerow([row.get("name"), row.get("sku"), row.get("locationId"), row.get("requiredQuantity"), row.get("availableQuantity"), row.get("afterQuantity"), row.get("shortage"), row.get("unitCost"), row.get("totalCost"), row.get("affectedOrders")])
    return stream.getvalue().encode("utf-8-sig")


def export_stock_deductions_xlsx(payload: dict, identity: dict) -> bytes:
    payload = {**payload, "page": 1, "perPage": 100}
    preview = preview_stock_deductions(payload, identity)
    workbook = Workbook()
    orders_sheet = workbook.active
    orders_sheet.title = "Orders"
    orders_sheet.append(["Stock Deduction Report", payload.get("fromDate"), payload.get("toDate"), datetime.utcnow().isoformat()])
    orders_sheet.append([])
    orders_sheet.append(["Package", "Customer", "Product Card", "Quantity", "Submitted", "Delivery Status", "Branch", "Location", "Deduction Status", "Component Units", "Cost Value", "Shortage", "Batch"])
    for row in preview["orders"]:
        orders_sheet.append([
            row["packageReference"], row["customerName"], row["productCard"], row["cardQuantity"],
            row["submittedAt"], row["deliveryStatus"], row["branch"], row["locationName"],
            row["deductionStatus"], row["requiredUnits"], row["totalCost"], row["shortageUnits"],
            row["deductionReference"],
        ])
    component_sheet = workbook.create_sheet("Components")
    component_sheet.append(["Component", "SKU", "Location", "Required", "Available", "After", "Shortage", "Unit Cost", "Total Cost", "Orders"])
    for row in preview["components"]:
        component_sheet.append([row.get("name"), row.get("sku"), row.get("locationId"), row.get("requiredQuantity"), row.get("availableQuantity"), row.get("afterQuantity"), row.get("shortage"), row.get("unitCost"), row.get("totalCost"), row.get("affectedOrders")])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_stock_deductions_pdf(payload: dict, identity: dict) -> bytes:
    payload = {**payload, "page": 1, "perPage": 100}
    preview = preview_stock_deductions(payload, identity)
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Stock Deduction Report", styles["Title"]),
        Paragraph(f"Inclusive range: {payload.get('fromDate')} to {payload.get('toDate')} · Generated UTC: {datetime.utcnow().isoformat()}", styles["BodyText"]),
        Spacer(1, 10),
    ]
    summary = preview["summary"]
    story.append(Table([
        ["Submitted", "Awaiting", "Ready", "Deducted", "Units", "Ready value", "Shortage", "Exceptions"],
        [summary["totalSubmittedOrders"], summary["awaitingDeduction"], summary["readyToDeduct"], summary["alreadyDeducted"], summary["totalComponentUnits"], f"GHS {summary['readyCostValue']:,.2f}", summary["shortageUnits"], summary["exceptions"]],
    ], style=[("GRID", (0, 0), (-1, -1), .5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    story.append(Spacer(1, 12))
    order_data = [["Package", "Customer", "Product", "Qty", "Submitted", "Branch / location", "Deduction status", "Units", "Cost", "Shortage"]]
    for row in preview["orders"]:
        order_data.append([row["packageReference"], row["customerName"], row["productCard"], row["cardQuantity"], row["submittedAt"][:16], f"{row['branch']} / {row['locationName']}", row["deductionStatus"], row["requiredUnits"], f"{row['totalCost']:,.2f}", row["shortageUnits"]])
    story.append(Table(order_data, repeatRows=1, colWidths=[62, 90, 100, 28, 85, 100, 105, 34, 48, 44], style=[("GRID", (0, 0), (-1, -1), .35, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("FONTSIZE", (0, 0), (-1, -1), 6.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    document.build(story)
    return output.getvalue()


def list_deduction_history(limit: int = 100) -> list[dict]:
    rows = deductions_col.find(
        {},
        {
            "package_id": 1, "deduction_batch_id": 1, "branch": 1, "location_name": 1,
            "total_units": 1, "total_cost_value": 1, "confirmed_by": 1, "confirmed_at": 1, "status": 1,
        },
    ).sort("confirmed_at", -1).limit(min(500, max(1, limit)))
    return [
        {
            "id": str(row.get("_id") or ""),
            "packageId": str(row.get("package_id") or ""),
            "batchId": row.get("deduction_batch_id") or "",
            "branch": row.get("branch") or "",
            "location": row.get("location_name") or "",
            "totalUnits": row.get("total_units") or 0,
            "totalCost": row.get("total_cost_value") or 0,
            "confirmedBy": (row.get("confirmed_by") or {}).get("name") or "",
            "confirmedAt": (_dt(row.get("confirmed_at")) or datetime.min).isoformat(),
            "status": row.get("status") or "",
        }
        for row in rows
    ]
