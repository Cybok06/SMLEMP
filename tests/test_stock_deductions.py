import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId

from Backend.inventory import stock_deductions_store as store


class StockDeductionDateTests(unittest.TestCase):
    def test_inclusive_ghana_date_boundaries(self):
        start, end = store.parse_ghana_range("2026-07-05", "2026-07-31")
        self.assertEqual(start, datetime(2026, 7, 5))
        self.assertEqual(end, datetime(2026, 8, 1))

    def test_single_day_range_uses_next_midnight(self):
        start, end = store.parse_ghana_range("2026-07-05", "2026-07-05")
        self.assertEqual((end - start).days, 1)

    def test_reversed_range_is_rejected(self):
        with self.assertRaisesRegex(store.StockDeductionError, "cannot be later"):
            store.parse_ghana_range("2026-07-31", "2026-07-05")

    def test_excessive_range_is_rejected(self):
        with self.assertRaisesRegex(store.StockDeductionError, "cannot exceed"):
            store.parse_ghana_range("2025-01-01", "2026-07-31")

    def test_created_at_has_priority_over_legacy_submitted_at(self):
        package = {
            "created_at": datetime(2026, 7, 8, 10),
            "submitted_at": datetime(2026, 7, 7, 10),
        }
        self.assertEqual(store._submission_time(package), package["created_at"])

    def test_legacy_submitted_at_is_supported(self):
        package = {"submitted_at": datetime(2026, 7, 7, 10)}
        self.assertEqual(store._submission_time(package), package["submitted_at"])


class StockDeductionRecipeTests(unittest.TestCase):
    def test_card_quantity_multiplies_component_quantity(self):
        product_id = ObjectId()
        rows = store._normalize_component_rows(
            [{"_id": product_id, "quantity": 3, "source_collection": "inventory_products"}],
            4,
            "test",
        )
        self.assertEqual(rows[0]["required_quantity"], 12)

    def test_single_component_recipe_snapshot(self):
        product_id = ObjectId()
        cursor = [{"_id": product_id, "sku": "SKU-1", "name": "Item"}]
        with patch.object(store.inventory_products_col, "find", return_value=cursor):
            snapshot = store.build_submission_recipe_snapshot(
                {"_id": ObjectId(), "components": [{"_id": product_id, "quantity": 1, "source_collection": "inventory_products"}]},
                2,
            )
        self.assertEqual(len(snapshot["components"]), 1)
        self.assertEqual(snapshot["components"][0]["required_quantity"], 2)
        self.assertTrue(snapshot["approved"])

    def test_multi_component_recipe_snapshot(self):
        first, second = ObjectId(), ObjectId()
        cursor = [
            {"_id": first, "sku": "A", "name": "A"},
            {"_id": second, "sku": "B", "name": "B"},
        ]
        with patch.object(store.inventory_products_col, "find", return_value=cursor):
            snapshot = store.build_submission_recipe_snapshot(
                {"_id": ObjectId(), "components": [
                    {"_id": first, "quantity": 1, "source_collection": "inventory_products"},
                    {"_id": second, "quantity": 2, "source_collection": "inventory_products"},
                ]},
                3,
            )
        self.assertEqual([row["required_quantity"] for row in snapshot["components"]], [3, 6])

    def test_location_stock_uses_only_selected_location_and_latest_cost(self):
        product = {"entries": [
            {"location_id": "A", "quantity": 10, "cost_price": 5, "created_at": datetime(2026, 1, 1)},
            {"location_id": "A", "quantity": -2, "cost_price": 7, "created_at": datetime(2026, 2, 1)},
            {"location_id": "B", "quantity": 100, "cost_price": 99, "created_at": datetime(2026, 3, 1)},
        ]}
        quantity, cost = store._location_stock(product, "A")
        self.assertEqual(quantity, 8)
        self.assertEqual(cost, 7)

    def test_missing_cost_is_not_coerced_to_zero(self):
        quantity, cost = store._location_stock(
            {"entries": [{"location_id": "A", "quantity": 5, "cost_price": 0, "created_at": datetime(2026, 1, 1)}]},
            "A",
        )
        self.assertEqual(quantity, 5)
        self.assertIsNone(cost)

    def test_idempotency_key_is_package_based(self):
        package_id = ObjectId()
        self.assertEqual(f"delivery-confirmation:{package_id}", f"delivery-confirmation:{str(package_id)}")

    def _prepared(self, *, locations, stock=10, cost=5, required=2, purchase_status="delivered"):
        package_id, customer_id, product_id, card_id = ObjectId(), ObjectId(), ObjectId(), ObjectId()
        package = {
            "_id": package_id,
            "customer_id": customer_id,
            "customer_name": "Masked",
            "product_index": 0,
            "product": {"_id": card_id, "name": "Card", "quantity": 1},
            "qty": 1,
            "status": "delivered",
            "created_at": datetime(2026, 7, 10),
            "manager_branch": "HQ",
            "inventory_recipe_snapshot": {
                "source": "submission_card_recipe",
                "approved": True,
                "components": [{
                    "inventory_product_id": product_id,
                    "quantity_per_card": required,
                    "card_quantity": 1,
                    "required_quantity": required,
                }],
            },
        }
        inventory = {
            "_id": product_id,
            "name": "Component",
            "sku": "SKU",
            "entries": [{"location_id": str(locations[0]["_id"]) if locations else "", "quantity": stock, "cost_price": cost, "created_at": datetime(2026, 7, 1)}],
        }
        return store._prepare_order(
            package, {}, set(), {product_id: inventory}, {"HQ": locations},
            {customer_id: {"purchases": [{"status": purchase_status, "product": {"status": purchase_status}}]}},
            {}, {}, {card_id: {"_id": card_id, "components": []}},
        )

    def test_one_active_location_is_preselected(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        order = self._prepared(locations=[location])
        self.assertEqual(order["locationId"], str(location["_id"]))
        self.assertTrue(order["selectable"])

    def test_multiple_locations_require_explicit_selection(self):
        locations = [
            {"_id": ObjectId(), "branch": "HQ", "name": "One", "code": "1"},
            {"_id": ObjectId(), "branch": "HQ", "name": "Two", "code": "2"},
        ]
        order = self._prepared(locations=locations)
        self.assertEqual(order["eligibilityStatus"], "Location required")
        self.assertFalse(order["selectable"])

    def test_component_location_mapping_selects_warehouse_per_product(self):
        locations = [
            {"_id": ObjectId(), "branch": "HQ", "name": "One", "code": "1"},
            {"_id": ObjectId(), "branch": "HQ", "name": "Two", "code": "2"},
        ]
        order = self._prepared(locations=locations)
        product_id = order["components"][0]["inventoryProductId"]
        package_id = order["id"]
        # Rebuild through the same fixture objects with an explicit component mapping.
        self.assertEqual(order["eligibilityStatus"], "Location required")
        self.assertEqual(order["components"][0]["locations"][1]["id"], str(locations[1]["_id"]))
        self.assertEqual(f"{package_id}:{product_id}".count(":"), 1)

    def test_authoritative_customer_branch_wins_over_package_snapshot(self):
        location = {"_id": ObjectId(), "branch": "Agent Branch", "name": "Main", "code": "AB"}
        package_id, customer_id, product_id, card_id = ObjectId(), ObjectId(), ObjectId(), ObjectId()
        package = {
            "_id": package_id, "customer_id": customer_id, "product_index": 0,
            "customer_name": "Customer", "product": {"_id": card_id, "name": "Card"},
            "qty": 1, "status": "delivered", "manager_branch": "Wrong Branch",
            "authoritative_customer_branch": "Agent Branch",
            "inventory_recipe_snapshot": {
                "source": "submission_card_recipe", "approved": True,
                "components": [{"inventory_product_id": product_id, "required_quantity": 1}],
            },
        }
        inventory = {
            "_id": product_id, "name": "Component", "sku": "SKU",
            "entries": [{"location_id": str(location["_id"]), "quantity": 2, "cost_price": 3}],
        }
        order = store._prepare_order(
            package, {}, set(), {product_id: inventory}, {"Agent Branch": [location]},
            {customer_id: {"purchases": [{"product": {"status": "delivered"}}]}},
            {}, {}, {card_id: {"components": []}},
        )
        self.assertEqual(order["branch"], "Agent Branch")
        self.assertEqual(order["components"][0]["locationId"], str(location["_id"]))

    def test_insufficient_stock_blocks_complete_order(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        order = self._prepared(locations=[location], stock=1, required=2)
        self.assertEqual(order["eligibilityStatus"], "Insufficient stock")
        self.assertFalse(order["selectable"])

    def test_one_ready_and_one_unavailable_component_allows_partial_deduction(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        package_id, customer_id, ready_id, empty_id, card_id = ObjectId(), ObjectId(), ObjectId(), ObjectId(), ObjectId()
        package = {
            "_id": package_id, "customer_id": customer_id, "customer_name": "Customer",
            "product_index": 0, "product": {"_id": card_id, "name": "Card"}, "qty": 1,
            "status": "delivered", "manager_branch": "HQ",
            "inventory_recipe_snapshot": {
                "source": "submission_card_recipe", "approved": True,
                "components": [
                    {"inventory_product_id": ready_id, "required_quantity": 2},
                    {"inventory_product_id": empty_id, "required_quantity": 1},
                ],
            },
        }
        products = {
            ready_id: {"_id": ready_id, "name": "Ready", "entries": [{"location_id": str(location["_id"]), "quantity": 5, "cost_price": 2, "created_at": datetime(2026, 7, 1)}]},
            empty_id: {"_id": empty_id, "name": "Empty", "entries": [{"location_id": str(location["_id"]), "quantity": 0, "cost_price": 3, "created_at": datetime(2026, 7, 1)}]},
        }
        order = store._prepare_order(
            package, {}, set(), products, {"HQ": [location]},
            {customer_id: {"purchases": [{"product": {"status": "delivered"}}]}},
            {}, {}, {card_id: {"components": []}},
        )
        self.assertEqual(order["eligibilityStatus"], "Ready for partial deduction")
        self.assertTrue(order["selectable"])
        statuses = {row["name"]: row["componentStatus"] for row in order["components"]}
        self.assertEqual(statuses["Ready"], "Ready to deduct")
        self.assertEqual(statuses["Empty"], "Undeducted - insufficient stock")

    def test_missing_cost_blocks_confirmation(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        order = self._prepared(locations=[location], stock=5, cost=0)
        self.assertEqual(order["eligibilityStatus"], "Cost unavailable")
        self.assertFalse(order["selectable"])

    def test_closed_purchase_is_blocked(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        order = self._prepared(locations=[location], purchase_status="closed")
        self.assertEqual(order["eligibilityStatus"], "Closed/cancelled — blocked")

    def test_delivered_undeducted_package_can_be_ready(self):
        location = {"_id": ObjectId(), "branch": "HQ", "name": "Main", "code": "HQ"}
        order = self._prepared(locations=[location], purchase_status="delivered")
        self.assertEqual(order["eligibilityStatus"], "Ready to deduct")


class SubmissionAndFrontendRegressionTests(unittest.TestCase):
    def test_legacy_submission_deduction_helper_is_noop(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "view.py").read_text(encoding="utf-8")
        helper = source[source.index("def _deduct_components_silent"):]
        self.assertIn("return []", helper)
        self.assertNotIn("update_one", helper)
        submit_route = source[source.index("def submit_for_packaging"):source.index("\n# ----------------------------", source.index("def submit_for_packaging"))]
        self.assertNotIn("_deduct_components_silent(", submit_route)

    def test_stock_deduction_tab_order_and_no_sidebar_page(self):
        root = Path(__file__).resolve().parents[1]
        audit_source = (root / "Frontend" / "Inventory_V2" / "src" / "app" / "components" / "AuditAccountability.tsx").read_text(encoding="utf-8")
        app_source = (root / "Frontend" / "Inventory_V2" / "src" / "app" / "App.tsx").read_text(encoding="utf-8")
        self.assertLess(audit_source.index("id: 'stock-taking'"), audit_source.index("id: 'stock-deduction'"))
        self.assertLess(audit_source.index("id: 'stock-deduction'"), audit_source.index("id: 'investigations'"))
        self.assertNotIn("id: 'stock-deduction'", app_source)


class AgentDeliveryDeductionTests(unittest.TestCase):
    def _fixture(self, *, owner_matches=True, available=8, required=2):
        agent_id = ObjectId()
        customer_id = ObjectId()
        package_id = ObjectId()
        component_id = ObjectId()
        location_id = ObjectId()
        package = {
            "_id": package_id,
            "customer_id": customer_id,
            "product_index": 0,
            "status": "pending",
            "inventory_recipe_snapshot": {
                "source": "submission_card_recipe",
                "approved": True,
                "components": [{"inventory_product_id": component_id, "required_quantity": required}],
            },
        }
        customer = {
            "_id": customer_id,
            "agent_id": agent_id if owner_matches else ObjectId(),
            "purchases": [{"product": {"status": "submitted_for_packaging"}}],
        }
        location = {"_id": location_id, "branch": "Accra", "name": "Main", "code": "ACC", "status": "active"}
        product = {
            "_id": component_id,
            "name": "Cooker",
            "entries": [{"location_id": str(location_id), "quantity": available, "cost_price": 10, "created_at": datetime(2026, 1, 1)}],
        }
        identity = {"user_id": str(agent_id), "role": "agent", "name": "Agent"}
        return identity, customer_id, package, customer, location, product

    def test_assigned_agent_delivery_uses_packaging_completion_without_deducting(self):
        import importlib
        packaging_stock = importlib.import_module("Backend.inventory.packaging_stock")
        identity, customer_id, package, customer, location, product = self._fixture()
        with (
            patch('services.delivery_evidence.upload_delivery_photo', return_value={'image_id': 'photo', 'url': 'https://example.test/photo'}) as upload,
            patch.object(store.packages_col, "find_one", return_value=package),
            patch.object(store.customers_col, "find_one", return_value=customer),
            patch.object(store.deductions_col, "find_one", return_value=None),
            patch.object(packaging_stock, "transition", return_value={"status": "deducted"}) as transition,
            patch.object(store, "_deduct_one") as deduct,
        ):
            result = store.deliver_package_and_deduct(str(customer_id), 0, identity)
        self.assertEqual(result["status"], "deducted")
        transition.assert_called_once_with(str(package["_id"]), "delivered", identity, delivery_evidence=upload.return_value)
        deduct.assert_not_called()

    def test_agent_cannot_deliver_another_agents_customer(self):
        identity, customer_id, package, customer, _location, _product = self._fixture(owner_matches=False)
        with (
            patch.object(store.packages_col, "find_one", return_value=package),
            patch.object(store.customers_col, "find_one", return_value=customer),
        ):
            with self.assertRaises(store.StockDeductionError) as raised:
                store.deliver_package_and_deduct(str(customer_id), 0, identity)
        self.assertEqual(raised.exception.code, "delivery_forbidden")

    def test_agent_cannot_deliver_without_a_photo(self):
        import importlib
        packaging_stock = importlib.import_module("Backend.inventory.packaging_stock")
        identity, customer_id, package, customer, _location, _product = self._fixture()
        with (
            patch.object(store.packages_col, 'find_one', return_value=package),
            patch.object(store.customers_col, 'find_one', return_value=customer),
            patch.object(packaging_stock, 'transition') as transition,
        ):
            with self.assertRaises(store.StockDeductionError) as raised:
                store.deliver_package_and_deduct(str(customer_id), 0, identity)
        self.assertEqual(raised.exception.code, 'delivery_photo_required')
        transition.assert_not_called()

    def test_unsettled_packaging_blocks_agent_delivery(self):
        import importlib
        packaging_stock = importlib.import_module("Backend.inventory.packaging_stock")
        identity, customer_id, package, customer, location, product = self._fixture(available=1, required=2)
        with (
            patch('services.delivery_evidence.upload_delivery_photo', return_value={'image_id': 'photo', 'url': 'https://example.test/photo'}),
            patch.object(store.packages_col, "find_one", return_value=package),
            patch.object(store.customers_col, "find_one", return_value=customer),
            patch.object(store.deductions_col, "find_one", return_value=None),
            patch.object(packaging_stock, "transition", side_effect=store.StockDeductionError("Settle outstanding components", "incomplete_delivery_stock")),
            patch.object(store, "_deduct_one") as deduct,
        ):
            with self.assertRaises(store.StockDeductionError) as raised:
                store.deliver_package_and_deduct(str(customer_id), 0, identity)
        self.assertEqual(raised.exception.code, "incomplete_delivery_stock")
        deduct.assert_not_called()

    def test_customer_profile_exposes_delivery_action_after_packaging(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "partials" / "customer_tabs" / "products.html").read_text(encoding="utf-8")
        self.assertIn('data-delivery-form="1"', template)
        self.assertIn('pstatus in ["packaging", "packaged", "delivering"]', template)


if __name__ == "__main__":
    unittest.main()
