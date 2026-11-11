"""Packaging-time stock allocation, settlement and auditable reporting."""
from collections import defaultdict
from datetime import datetime
import re
from uuid import UUID, uuid4

from bson import ObjectId
from db import db
from . import stock_deductions_store as store

Error = store.StockDeductionError
FLOW = ["pending", "packaging", "delivering", "delivered"]
attempts = db["inventory_deduction_attempts"]


def ensure_indexes():
    store.ensure_stock_deduction_indexes()
    attempts.create_index([("package_id", 1), ("request_id", 1)], unique=True)
    attempts.create_index([("created_at", -1)])
    store.movements_col.create_index([("created_at", -1), ("branch", 1)])


def allocate_component(product, locations, required):
    """Deterministic allocation; quantities may span warehouses in one branch."""
    candidates = []
    for location in locations:
        available, cost = store._location_stock(product, str(location["_id"]))
        candidates.append((not bool(location.get("is_default") or location.get("default_delivery")),
                           -available, str(location.get("code") or location["_id"]), location, available, cost))
    allocations, remaining = [], required
    unknown_cost = False
    for _, _, _, location, available, cost in sorted(candidates, key=lambda row: row[:3]):
        if remaining <= 0:
            break
        if available and (cost is None or cost <= 0):
            unknown_cost = True
            continue
        take = min(remaining, available)
        if take > 0:
            allocations.append({"location": location, "quantity": take, "before": available, "unit_cost": cost})
            remaining -= take
    reason = ("cost_unavailable" if unknown_cost else "insufficient_stock") if remaining else ""
    if not locations:
        reason = "no_active_branch_warehouse"
    return allocations, remaining, reason


def _recipe(package, existing):
    recipe = (existing or {}).get("recipe")
    if not recipe:
        recipe, _ = store.resolve_recipe(package)
    if not recipe or not recipe.get("components"):
        raise Error("No component recipe is available. Review the card before packaging.", "missing_recipe")
    if recipe.get("legacy_partial_deduction"):
        raise Error("Historical partial deduction requires reconciliation before processing.", "legacy_partial_deduction")
    if recipe.get("source") == "current_card_recipe" and not recipe.get("approved"):
        raise Error("Review and freeze the current card recipe in Stock Deduction first.", "recipe_review_required")
    return recipe


def transition(package_id, target, identity, *, settle=False, request_id=None, delivery_evidence=None):
    """Routes enforce their existing user/branch scope before calling this service."""
    oid = store._oid(package_id)
    if not oid or target not in FLOW:
        raise Error("Invalid package or status.", "invalid_reference")
    if identity.get('role') == 'agent' and target == 'delivered' and not (
        delivery_evidence and delivery_evidence.get('image_id') and delivery_evidence.get('url')
        and delivery_evidence.get('uploaded_by') == str(identity.get('user_id'))
    ):
        raise Error('A delivery camera photo is required.', 'delivery_photo_required')
    if settle:
        try:
            request_id = str(UUID(str(request_id)))
        except (ValueError, TypeError):
            raise Error("A settlement request reference is required.", "invalid_request")
    else:
        request_id = f"status:{target}"
    ensure_indexes()

    def commit(session):
        package = store.packages_col.find_one({"_id": oid}, session=session)
        if not package:
            raise Error("Submitted card not found.", "not_found", 404)
        current = package.get("status") or "pending"
        existing = store.deductions_col.find_one({"package_id": oid}, session=session)
        previous = attempts.find_one({"package_id": oid, "request_id": request_id}, session=session)
        if previous:
            if identity.get("stock_branch_scope") and str(previous.get("branch") or "").casefold() != str(identity["stock_branch_scope"]).casefold():
                raise Error("This deduction belongs to another branch.", "branch_forbidden", 403)
            return {**store._json_value(previous), "replayed": True}
        if current not in FLOW:
            raise Error("This card is not eligible for stock processing.", "status_blocked")
        if settle and (current not in {"packaging", "delivering"} or not existing):
            raise Error("Only outstanding packaged cards can be settled.", "not_packaged")
        if not settle and target == current:
            # Legacy packaged cards without a deduction can enter the new workflow.
            if target != "packaging" or existing:
                return {"packageId": str(oid), "status": (existing or {}).get("status", "unchanged"), "components": (store._json_value(existing) or {}).get("components", []), "replayed": True}
        if not settle and target != current and (target == "pending" or FLOW.index(target) < FLOW.index(current)):
            raise Error("A processed card cannot be moved backwards. Review stock movements first.", "backward_status")
        if target in {"delivering", "delivered"} and current == "pending":
            raise Error("Move this card to Packaging before continuing.", "packaging_required")
        if store.packages_col.count_documents({"customer_id": package.get("customer_id"), "product_index": package.get("product_index")}, limit=2, session=session) > 1:
            raise Error("Duplicate submitted cards require review.", "duplicate_submission")
        customer = store.customers_col.find_one({"_id": package.get("customer_id")}, session=session)
        index = store._integer(package.get("product_index"), -1)
        purchases = (customer or {}).get("purchases") or []
        if index < 0 or index >= len(purchases):
            raise Error("Customer purchase was not found.", "purchase_missing")
        purchase = purchases[index]
        if str((purchase.get("product") or {}).get("status") or purchase.get("status") or "").lower() in {"closed", "cancelled", "canceled", "withdrawn"}:
            raise Error("Closed or cancelled purchases cannot be processed.", "purchase_blocked")
        if identity.get("role") == "agent" and (target != "delivered" or store._oid(customer.get("agent_id")) != store._oid(identity.get("user_id"))):
            raise Error("You can only deliver purchases for your assigned customers.", "forbidden", 403)
        now = datetime.utcnow()
        recipe = _recipe(package, existing)
        complete = (existing or {}).get("status") in {"deducted", "fully_deducted"} or recipe.get("legacy_deduction_proven")
        if target == "delivered" and not complete:
            raise Error("Settle all outstanding components in Stock Deduction before marking Delivered.", "incomplete_delivery_stock", 409)
        if target == "delivering" and not existing and not complete:
            raise Error("Process the Packaging stock deduction first.", "packaging_required", 409)
        agent_id = store._oid(customer.get("agent_id"))
        agent = store.users_col.find_one({"_id": agent_id}, session=session) if agent_id else {}
        branch = str((existing or {}).get("branch") or (agent or {}).get("branch") or "").strip()
        allowed_branch = str(identity.get("stock_branch_scope") or "").strip()
        if allowed_branch and branch.casefold() != allowed_branch.casefold():
            raise Error("This deduction belongs to another branch.", "branch_forbidden", 403)
        if not branch:
            raise Error("The assigned agent has no branch. Correct it before packaging.", "branch_required")
        movements, component_results = [], list((existing or {}).get("components") or [])
        new_units, new_cost = 0, 0.0
        deduction_id = (existing or {}).get("_id") or ObjectId()
        attempt_id = ObjectId()
        if (target == "packaging" or settle) and not complete:
            locations = list(store.locations_col.find({"branch": {"$regex": f"^{re.escape(branch)}$", "$options": "i"}, "status": {"$regex": "^active$", "$options": "i"}}, session=session))
            prior = {str(row.get("inventory_product_id")): row for row in component_results}
            requirements = {}
            for row in recipe["components"]:
                key = str(row.get("inventory_product_id") or "")
                required = store._integer(row.get("required_quantity"), 0)
                if required <= 0:
                    raise Error("Component quantities must be positive. Review the recipe.", "invalid_recipe")
                if key not in requirements:
                    requirements[key] = {**row, "required_quantity": 0}
                requirements[key]["required_quantity"] += required
            component_results = []
            units_by_location = defaultdict(int)
            for key, spec in requirements.items():
                product_id = store._oid(key)
                previous_component = prior.get(key) or {}
                required = spec["required_quantity"]
                already = min(required, store._integer(previous_component.get("deducted_quantity"), 0))
                remaining = required - already
                product = store.inventory_products_col.find_one({"_id": product_id}, session=session) if product_id else None
                allocations, left, reason = allocate_component(product, locations, remaining) if product else ([], remaining, "missing_inventory_link")
                row_cost = float(previous_component.get("total_cost") or 0)
                row_allocations = list(previous_component.get("allocations") or [])
                for allocation in allocations:
                    location = allocation["location"]
                    qty, cost = allocation["quantity"], allocation["unit_cost"]
                    movement = {"_id": ObjectId(), "deduction_id": deduction_id, "attempt_id": attempt_id,
                                "package_id": oid, "customer_id": customer["_id"], "product_index": index,
                                "agent_id": agent_id, "agent_name": (agent or {}).get("name", ""),
                                "product_id": product_id, "product_name": product.get("name", ""), "sku": product.get("sku", ""),
                                "branch": branch, "location_id": location["_id"], "location_name": location.get("name", ""),
                                "before_quantity": allocation["before"], "after_quantity": allocation["before"] - qty,
                                "deducted_quantity": qty, "unit_cost": cost, "total_cost": round(qty * cost, 2),
                                "movement_type": "packaging_stock_deduction", "created_at": now,
                                "actor": {k: identity.get(k) for k in ("user_id", "name", "role")}}
                    entry = {"branch": branch, "location_id": str(location["_id"]), "location_name": location.get("name", ""),
                             "location_code": location.get("code", ""), "quantity": -qty, "cost_price": cost,
                             "selling_price": 0, "created_at": now, "updated_at": now, "movement_type": "packaging_stock_deduction",
                             "deduction_id": str(deduction_id), "package_id": str(oid), "attempt_id": str(attempt_id)}
                    result = store.inventory_products_col.update_one({"_id": product_id}, {"$push": {"entries": entry}, "$set": {"updated_at": now}}, session=session)
                    if result.modified_count != 1:
                        raise Error("Stock changed. Retry this operation.", "stock_conflict", 409)
                    store.movements_col.insert_one(movement, session=session)
                    movements.append(movement)
                    row_allocations.append({"location_id": str(location["_id"]), "location_name": location.get("name", ""), "quantity": qty, "unit_cost": cost, "attempt_id": str(attempt_id)})
                    units_by_location[location["_id"]] += qty
                    new_units += qty
                    new_cost += qty * cost
                    row_cost += qty * cost
                component_results.append({"inventory_product_id": product_id or key,
                                          "name": (product or {}).get("name") or spec.get("component_name") or spec.get("name") or "Unlinked component",
                                          "sku": (product or {}).get("sku") or spec.get("sku", ""),
                                          "required_quantity": required, "deducted_quantity": required - left,
                                          "remaining_quantity": left, "deducted_now": remaining - left,
                                          "status": "deducted" if left == 0 else "undeducted", "reason": reason if left else "",
                                          "total_cost": round(row_cost, 2), "allocations": row_allocations})
            for location_id, qty in units_by_location.items():
                updated = store.locations_col.update_one({"_id": location_id, "stock_units": {"$gte": qty}}, {"$inc": {"stock_units": -qty}, "$set": {"updated_at": now}}, session=session)
                if updated.modified_count != 1:
                    raise Error("Warehouse totals changed or need reconciliation. No deduction was saved; refresh and retry.", "stock_conflict", 409)
            total_units = sum(row["deducted_quantity"] for row in component_results)
            outstanding = sum(row["remaining_quantity"] for row in component_results)
            status = "deducted" if not outstanding else ("partially_deducted" if total_units else "not_deducted")
            existing = {**(existing or {}), "_id": deduction_id, "package_id": oid,
                        "customer_id": customer["_id"], "customer_name": customer.get("name") or customer.get("customer_name") or customer.get("full_name", ""),
                        "agent_id": (existing or {}).get("agent_id") or agent_id, "agent_name": (existing or {}).get("agent_name") or (agent or {}).get("name", ""),
                        "product_card": package.get("product_name") or (package.get("product") or {}).get("name") or (purchase.get("product") or {}).get("name") or "Product card",
                        "product_index_snapshot": index, "branch": branch, "workflow": "packaging",
                        "idempotency_key": f"delivery-confirmation:{oid}", "deduction_batch_id": f"PKG-{str(oid)[-8:].upper()}",
                        "recipe": recipe, "recipe_source": recipe.get("source"), "components": component_results,
                        "total_units": total_units, "remaining_units": outstanding,
                        "total_cost_value": round(sum(row["total_cost"] for row in component_results), 2),
                        "movement_ids": list((existing or {}).get("movement_ids") or []) + [m["_id"] for m in movements],
                        "first_attempt_at": (existing or {}).get("first_attempt_at") or now,
                        "confirmed_at": now, "confirmed_by": {k: identity.get(k) for k in ("user_id", "name", "role")}, "status": status}
            store.deductions_col.replace_one({"package_id": oid}, existing, upsert=True, session=session)
            if movements:
                store.stock_sessions_col.insert_one({"session_number": f"PKG-{attempt_id}", "branch": branch,
                    "reason": f"Packaging deduction for {oid}", "status": "closed", "source": "stock_deduction",
                    "package_id": oid, "deduction_id": deduction_id, "created_at": now, "closed_at": now,
                    "locations": [{"location_id": str(location["_id"]), "location_name": location.get("name", ""),
                                   "location_code": location.get("code", ""), "total_units": units_by_location[location["_id"]]}
                                  for location in locations if units_by_location.get(location["_id"])],
                    "created_by": existing["confirmed_by"], "summary": {"updates": len(movements), "totalUnits": new_units, "totalValue": round(new_cost, 2)}}, session=session)
        final_status = current if settle else target
        package_updates = {"status": final_status, "updated_at": now, "updated_by": identity.get("user_id")}
        if existing:
            package_updates.update(stock_deduction_id=existing["_id"], stock_deduction_status=existing["status"], stock_deduction_branch=branch)
        if final_status == "packaging":
            package_updates["packaging_started_at"] = package.get("packaging_started_at") or now
        if final_status == "delivered":
            package_updates.update(delivered_at=now, delivered_by=identity.get("user_id"))
            if delivery_evidence:
                package_updates['delivery_evidence'] = delivery_evidence
        update = {"$set": package_updates}
        if current != final_status:
            update["$push"] = {"status_history": {"status": final_status, "timestamp": now, "actor_id": identity.get("user_id"), "actor_name": identity.get("name"), "actor_role": identity.get("role"), "source": "packaging_stock"}}
        store.packages_col.update_one({"_id": oid}, update, session=session)
        if final_status == "delivered":
            from services.delivery_followups import ensure_delivery_followup
            ensure_delivery_followup(db, package=package, customer=customer,
                trigger="delivered", event_at=now, session=session)
        mapped = {"pending": "submitted_for_packaging", "packaging": "packaged", "delivering": "delivering", "delivered": "delivered"}[final_status]
        changes = {f"purchases.{index}.product.status": mapped, f"purchases.{index}.status": mapped,
                   f"purchases.{index}.product.packaging_status": final_status, "updated_at": now}
        if final_status == "delivered":
            changes.update({f"purchases.{index}.delivered_at": now, f"purchases.{index}.product.delivered_at": now, f"purchases.{index}.delivered_by": identity.get("user_id")})
            if delivery_evidence:
                changes[f'purchases.{index}.delivery_evidence'] = delivery_evidence
        store.customers_col.update_one({"_id": customer["_id"]}, {"$set": changes}, session=session)
        if existing:
            outflow_fields = {"components_deducted": component_results, "components_status": existing["status"],
                              "deduction_deferred": False, "stock_deduction_id": existing["_id"], "delivery_branch": branch}
            if final_status == "delivered":
                outflow_fields["delivered_at"] = now
            store.outflows_col.update_one({"customer_id": customer["_id"], "packaged_product_index": index, "source": "Agent_deliveries"},
                                         {"$set": outflow_fields}, session=session)
        report = {"_id": attempt_id, "package_id": oid, "packageId": str(oid), "request_id": request_id,
                  "deduction_id": (existing or {}).get("_id"), "branch": branch,
                  "agent_name": (existing or {}).get("agent_name") or (agent or {}).get("name", ""),
                  "customer_name": customer.get("name", ""), "created_at": now,
                  "kind": "settlement" if settle else target, "status": (existing or {}).get("status", "deducted" if complete else "unchanged"),
                  "deducted_now": new_units, "value_now": round(new_cost, 2), "components": component_results,
                  "actor": {k: identity.get(k) for k in ("user_id", "name", "role")}}
        if delivery_evidence:
            report['delivery_evidence'] = delivery_evidence
        attempts.insert_one(report, session=session)
        store.activity_logs_col.insert_one({"action": "packaging_stock_" + report["kind"], "entity_type": "package", "entity_id": str(oid), "timestamp": now,
            "user_id": str(identity.get("user_id") or ""), "role": identity.get("role"), "meta": {"attempt_id": str(attempt_id), "units": new_units, "branch": branch}}, session=session)
        return store._json_value(report)

    try:
        with db.client.start_session() as session:
            return session.with_transaction(commit)
    except Exception as exc:
        raise store._transaction_error(exc) from exc


def summarize_metrics(records, movements, initial_attempts, warehouses):
    """Flows use posting dates; shortage demand is counted only on first packaging."""
    branches = {}
    def branch_row(name):
        return branches.setdefault(name, {"branch": name, "required": 0, "deductedInitially": 0,
                                         "shortage": 0, "deducted": 0, "value": 0, "outstanding": 0})
    warehouse_rows = {str(row["_id"]): {"warehouse": row.get("name", ""), "branch": row.get("branch", ""),
                                       "units": 0, "value": 0} for row in warehouses}
    shortages = defaultdict(int)
    for attempt in initial_attempts:
        row = branch_row(attempt.get("branch") or "Unknown")
        for component in attempt.get("components") or []:
            required = int(component.get("required_quantity") or 0)
            remaining = int(component.get("remaining_quantity") or 0)
            row["required"] += required
            row["deductedInitially"] += required - remaining
            row["shortage"] += remaining
            shortages[component.get("name") or "Unknown"] += remaining
    for movement in movements:
        units = int(movement.get("deducted_quantity") or 0)
        value = float(movement.get("total_cost") or 0)
        row = branch_row(movement.get("branch") or "Unknown")
        row["deducted"] += units
        row["value"] += value
        key = str(movement.get("location_id") or "")
        warehouse = warehouse_rows.setdefault(key, {"warehouse": movement.get("location_name") or key,
            "branch": movement.get("branch", ""), "units": 0, "value": 0})
        warehouse["units"] += units
        warehouse["value"] += value
    pending = []
    for record in records:
        left = sum(int(c.get("remaining_quantity") or 0) for c in record.get("components") or [])
        if left:
            branch_row(record.get("branch") or "Unknown")["outstanding"] += left
            pending.append(record)
    for row in branches.values():
        row["fulfilmentRate"] = round(100 * row["deductedInitially"] / row["required"], 1) if row["required"] else None
        row["value"] = round(row["value"], 2)
    return {"deductedUnits": sum(int(m.get("deducted_quantity") or 0) for m in movements),
            "deductedValue": round(sum(float(m.get("total_cost") or 0) for m in movements), 2),
            "newShortageUnits": sum(shortages.values()),
            "fullyDeductedCards": sum(a.get("status") == "deducted" for a in initial_attempts),
            "partiallyDeductedCards": sum(a.get("status") == "partially_deducted" for a in initial_attempts),
            "undeductedCards": sum(a.get("status") == "not_deducted" for a in initial_attempts),
            "outstandingCards": len(pending),
            "outstandingUnits": sum(r["outstanding"] for r in branches.values()),
            "oldestOutstandingDays": max([max(0, (datetime.utcnow() - (r.get("first_attempt_at") or r.get("confirmed_at") or datetime.utcnow())).days) for r in pending] or [0]),
            "branches": sorted(branches.values(), key=lambda r: (r["fulfilmentRate"] is None, r["fulfilmentRate"] or 0)),
            "warehouses": sorted(warehouse_rows.values(), key=lambda r: -r["units"]),
            "shortageProducts": [{"name": k, "quantity": v} for k, v in sorted(shortages.items(), key=lambda row: -row[1]) if v]}
