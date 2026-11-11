"""Offline transaction tests; never connect to the configured application database."""
import copy
from datetime import datetime
import re
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4
from bson import ObjectId
from pymongo.errors import OperationFailure

fake_import_db = types.ModuleType("db")
fake_import_db.db = MagicMock()
with patch.dict(sys.modules, {"db": fake_import_db}):
    from Backend.inventory import stock_deductions_store as store
    from Backend.inventory import packaging_stock as service


def matches(row, query):
    for key, value in query.items():
        if key == "$and":
            if not all(matches(row, q) for q in value): return False
            continue
        if key == "$or":
            if not any(matches(row, q) for q in value): return False
            continue
        current = row.get(key)
        if isinstance(value, dict):
            if "$in" in value and current not in value["$in"]: return False
            if "$ne" in value and current == value["$ne"]: return False
            if "$gte" in value and (current is None or current < value["$gte"]): return False
            if "$regex" in value and not re.search(value["$regex"], str(current or ""), re.I): return False
        elif current != value: return False
    return True


def set_path(row, path, value):
    parts = path.split(".")
    current = row
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current.setdefault(part, {})
    current[parts[-1]] = value


class Collection:
    def __init__(self): self.rows = []
    def create_index(self, *args, **kwargs): pass
    def find(self, query=None, *args, **kwargs): return copy.deepcopy([r for r in self.rows if matches(r, query or {})])
    def find_one(self, query, *args, **kwargs): return next(iter(self.find(query)), None)
    def count_documents(self, query, **kwargs): return len(self.find(query))
    def insert_one(self, row, **kwargs):
        row = copy.deepcopy(row)
        row.setdefault("_id", ObjectId())
        self.rows.append(row)
        return types.SimpleNamespace(inserted_id=row["_id"])
    def replace_one(self, query, row, upsert=False, **kwargs):
        for i, old in enumerate(self.rows):
            if matches(old, query): self.rows[i] = copy.deepcopy(row); return
        if upsert: self.insert_one(row)
    def update_one(self, query, update, **kwargs):
        for row in self.rows:
            if matches(row, query):
                for key, value in update.get("$set", {}).items(): set_path(row, key, copy.deepcopy(value))
                for key, value in update.get("$push", {}).items(): row.setdefault(key, []).append(copy.deepcopy(value))
                for key, value in update.get("$inc", {}).items(): row[key] = row.get(key, 0) + value
                return types.SimpleNamespace(modified_count=1)
        return types.SimpleNamespace(modified_count=0)


class PackagingStockTests(unittest.TestCase):
    def setUp(self):
        self.collections = {}
        names = {"packages_col": "packages", "customers_col": "customers", "users_col": "users", "inventory_products_col": "inventory_products", "locations_col": "inventory_branch_locations", "deductions_col": "inventory_delivery_deductions", "movements_col": "inventory_delivery_movements", "stock_sessions_col": "inventory_stock_update_sessions", "activity_logs_col": "activity_logs", "outflows_col": "inventory_products_outflow"}
        for attr, name in names.items():
            self.collections[name] = Collection()
            patcher = patch.object(store, attr, self.collections[name]); patcher.start(); self.addCleanup(patcher.stop)
        self.attempts = Collection()
        patcher = patch.object(service, "attempts", self.attempts); patcher.start(); self.addCleanup(patcher.stop)
        session = MagicMock()
        def transaction(callback):
            old = {k: copy.deepcopy(v.rows) for k, v in self.collections.items()}
            old_attempts = copy.deepcopy(self.attempts.rows)
            try: return callback(session)
            except Exception:
                for k, rows in old.items(): self.collections[k].rows = rows
                self.attempts.rows = old_attempts
                raise
        session.with_transaction.side_effect = transaction
        database = MagicMock()
        database.client.start_session.return_value.__enter__.return_value = session
        patcher = patch.object(service, "db", database); patcher.start(); self.addCleanup(patcher.stop)
        self.agent, self.customer, self.package = ObjectId(), ObjectId(), ObjectId()
        self.rice, self.tv, self.loc1, self.loc2, self.other = [ObjectId() for _ in range(5)]
        self.identity = {"user_id": str(ObjectId()), "role": "inventory", "name": "Inventory", "stock_branch_scope": "Accra"}
        self.put("users", {"_id": self.agent, "branch": "Accra", "name": "Agent A"})
        self.put("customers", {"_id": self.customer, "name": "Customer A", "agent_id": self.agent, "purchases": [{"product": {"name": "Max A", "status": "submitted_for_packaging"}}]})
        self.put("packages", {"_id": self.package, "customer_id": self.customer, "product_index": 0, "status": "pending", "inventory_recipe_snapshot": {"approved": True, "source": "submission_card_recipe", "components": [ {"inventory_product_id": self.rice, "component_name": "Rice", "required_quantity": 1}, {"inventory_product_id": self.tv, "component_name": "TV", "required_quantity": 1}]}})
        for oid, branch, name in [(self.loc1, "Accra", "Main"), (self.loc2, "Accra", "Second"), (self.other, "Kumasi", "Other")]:
            self.put("inventory_branch_locations", {"_id": oid, "branch": branch, "name": name, "code": name, "status": "active", "stock_units": 100})
        self.put("inventory_products", {"_id": self.rice, "name": "Rice", "entries": [self.entry(self.loc1, 5)]})
        self.put("inventory_products", {"_id": self.tv, "name": "TV", "entries": [self.entry(self.other, 100)]})

    def put(self, name, row): self.collections[name].insert_one(row)
    def entry(self, location, quantity, cost=10): return {"location_id": str(location), "quantity": quantity, "cost_price": cost, "created_at": datetime(2026, 1, 1)}
    def run_stock(self, target="packaging", **kwargs): return service.transition(str(self.package), target, self.identity, **kwargs)
    def restock(self, product, location, quantity):
        self.collections["inventory_products"].update_one({"_id": product}, {"$push": {"entries": self.entry(location, quantity)}})
    def record(self): return self.collections["inventory_delivery_deductions"].find_one({"package_id": self.package})

    def test_partial_packaging_deducts_rice_and_leaves_tv(self):
        result = self.run_stock()
        self.assertEqual(result["status"], "partially_deducted")
        self.assertEqual(result["deducted_now"], 1)
        self.assertEqual(self.record()["remaining_units"], 1)
        self.assertEqual(self.collections["packages"].find_one({"_id": self.package})["status"], "packaging")
        self.assertEqual(len(self.collections["inventory_delivery_movements"].rows), 1)
        self.assertEqual(self.collections["inventory_delivery_movements"].rows[0]["location_id"], self.loc1)

    def test_zero_stock_still_enters_packaging_and_logs_shortages(self):
        self.collections["inventory_products"].rows = []
        result = self.run_stock()
        self.assertEqual(result["status"], "not_deducted")
        self.assertEqual(len(result["components"]), 2)
        self.assertEqual(len(self.attempts.rows), 1)

    def test_settlement_deducts_only_remainder_and_replay_is_safe(self):
        self.run_stock(); self.restock(self.tv, self.loc2, 1)
        token = str(uuid4())
        result = self.run_stock(settle=True, request_id=token)
        self.assertEqual(result["status"], "deducted")
        self.assertEqual(result["deducted_now"], 1)
        self.assertEqual(self.record()["total_units"], 2)
        self.assertTrue(self.run_stock(settle=True, request_id=token)["replayed"])
        self.assertEqual(len(self.collections["inventory_delivery_movements"].rows), 2)

    def test_packaging_retry_cannot_deduct_again(self):
        self.run_stock(); self.restock(self.tv, self.loc2, 1)
        self.assertTrue(self.run_stock()["replayed"])
        self.assertEqual(self.record()["remaining_units"], 1)

    def test_branch_is_frozen_after_agent_transfer(self):
        self.run_stock()
        self.collections["users"].update_one({"_id": self.agent}, {"$set": {"branch": "Kumasi"}})
        result = self.run_stock(settle=True, request_id=str(uuid4()))
        self.assertEqual(result["deducted_now"], 0)
        self.assertEqual(result["branch"], "Accra")
        self.assertEqual(self.record()["remaining_units"], 1)

    def test_multiple_warehouses_and_partial_quantity(self):
        package = self.collections["packages"].rows[0]
        package["inventory_recipe_snapshot"]["components"][0]["required_quantity"] = 8
        self.restock(self.rice, self.loc2, 2)
        result = self.run_stock()
        rice = result["components"][0]
        self.assertEqual((rice["deducted_quantity"], rice["remaining_quantity"]), (7, 1))
        self.assertEqual(len(rice["allocations"]), 2)

    def test_inactive_warehouse_is_not_used(self):
        self.collections["inventory_branch_locations"].update_one({"_id": self.loc1}, {"$set": {"status": "inactive"}})
        self.assertEqual(self.run_stock()["deducted_now"], 0)

    def test_missing_cost_is_logged_without_stock_mutation(self):
        self.collections["inventory_products"].rows[0]["entries"][0]["cost_price"] = 0
        result = self.run_stock()
        self.assertEqual(result["components"][0]["reason"], "cost_unavailable")
        self.assertEqual(result["deducted_now"], 0)

    def test_delivery_blocked_until_settled_and_never_deducts_again(self):
        self.run_stock()
        with self.assertRaises(service.Error) as error: self.run_stock("delivered")
        self.assertEqual(error.exception.code, "incomplete_delivery_stock")
        self.restock(self.tv, self.loc2, 1)
        self.run_stock(settle=True, request_id=str(uuid4()))
        self.run_stock("delivered")
        self.assertEqual(len(self.collections["inventory_delivery_movements"].rows), 2)
        self.assertEqual(self.collections["packages"].rows[0]["status"], "delivered")
        self.assertEqual(self.collections["customers"].rows[0]["purchases"][0]["product"]["status"], "delivered")

    def test_agent_photo_is_required_even_when_bypassing_the_profile_route(self):
        actor = {'user_id': str(self.agent), 'role': 'agent'}
        with self.assertRaises(service.Error) as error:
            service.transition(str(self.package), 'delivered', actor)
        self.assertEqual(error.exception.code, 'delivery_photo_required')
        self.assertEqual(self.collections['packages'].rows[0]['status'], 'pending')

    def test_agent_delivery_saves_photo_with_package_purchase_and_audit(self):
        self.run_stock()
        self.restock(self.tv, self.loc2, 1)
        self.run_stock(settle=True, request_id=str(uuid4()))
        actor = {'user_id': str(self.agent), 'role': 'agent'}
        photo = {'image_id': 'cloud-photo', 'url': 'https://example.test/photo', 'uploaded_by': str(self.agent)}
        with patch('services.delivery_followups.ensure_delivery_followup'):
            service.transition(str(self.package), 'delivered', actor, delivery_evidence=photo)
            service.transition(str(self.package), 'delivered', actor, delivery_evidence=photo)
        self.assertEqual(self.collections['packages'].rows[0]['delivery_evidence'], photo)
        self.assertEqual(self.collections['customers'].rows[0]['purchases'][0]['delivery_evidence'], photo)
        self.assertEqual(self.attempts.rows[-1]['delivery_evidence'], photo)
        self.assertEqual(len(self.collections['inventory_delivery_movements'].rows), 2)

    def test_failure_after_first_stock_write_rolls_back_everything(self):
        with patch.object(self.collections["inventory_delivery_movements"], "insert_one", side_effect=OperationFailure("simulated failure")):
            with self.assertRaises(service.Error): self.run_stock()
        self.assertIsNone(self.record())
        self.assertEqual(len(self.collections["inventory_products"].rows[0]["entries"]), 1)
        self.assertEqual(self.collections["packages"].rows[0]["status"], "pending")
        self.assertEqual(self.attempts.rows, [])

    def test_delivered_creates_followup_inside_the_same_transaction(self):
        self.run_stock()
        self.restock(self.tv, self.loc2, 1)
        self.run_stock(settle=True, request_id=str(uuid4()))
        with patch("services.delivery_followups.ensure_delivery_followup") as followup:
            self.run_stock("delivered")
            self.run_stock("delivered")
        followup.assert_called_once()
        self.assertEqual(followup.call_args.kwargs["package"]["_id"], self.package)
        self.assertEqual(followup.call_args.kwargs["trigger"], "delivered")
        self.assertIsNotNone(followup.call_args.kwargs["session"])

    def test_followup_write_failure_rolls_back_delivery_transition(self):
        self.run_stock()
        self.restock(self.tv, self.loc2, 1)
        self.run_stock(settle=True, request_id=str(uuid4()))
        previous_attempts = copy.deepcopy(self.attempts.rows)
        with patch("services.delivery_followups.ensure_delivery_followup", side_effect=OperationFailure("follow-up write failed")):
            with self.assertRaises(service.Error):
                self.run_stock("delivered")
        self.assertEqual(self.collections["packages"].rows[0]["status"], "packaging")
        self.assertEqual(self.attempts.rows, previous_attempts)

    def test_warehouse_total_conflict_rolls_back(self):
        self.collections["inventory_branch_locations"].update_one({"_id": self.loc1}, {"$set": {"stock_units": 0}})
        with self.assertRaises(service.Error): self.run_stock()
        self.assertEqual(len(self.collections["inventory_delivery_movements"].rows), 0)

    def test_wrong_branch_user_cannot_process(self):
        self.identity["stock_branch_scope"] = "Kumasi"
        with self.assertRaises(service.Error) as error: self.run_stock()
        self.assertEqual(error.exception.code, "branch_forbidden")

    def test_metrics_count_initial_shortage_once_and_actual_settlement_movements(self):
        self.run_stock()
        self.run_stock(settle=True, request_id=str(uuid4()))
        self.restock(self.tv, self.loc2, 1)
        self.run_stock(settle=True, request_id=str(uuid4()))
        result = service.summarize_metrics([self.record()], self.collections["inventory_delivery_movements"].rows,
            [a for a in self.attempts.rows if a["kind"] == "packaging"], self.collections["inventory_branch_locations"].rows)
        self.assertEqual(result["deductedUnits"], 2)
        self.assertEqual(result["newShortageUnits"], 1)
        self.assertEqual(result["outstandingUnits"], 0)
        self.assertEqual(result["deductedValue"], 20)
        self.assertEqual(result["branches"][0]["fulfilmentRate"], 50)

    def test_backward_status_and_skipping_packaging_are_blocked(self):
        with self.assertRaises(service.Error): self.run_stock("delivered")
        self.run_stock()
        with self.assertRaises(service.Error): self.run_stock("pending")


if __name__ == "__main__": unittest.main()
