import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId

from services import customer_liability_service as svc


def _payment_row(customer_id=None, product_index=0, verified=200.0, reversals=0.0, product_total=500.0):
    customer_id = customer_id or ObjectId("6852e7e6e564fbc5cca14104")
    return {
        "_id": {"customer_id": customer_id, "product_index": product_index},
        "verified_payments": verified,
        "reversals": reversals,
        "first_payment_date": "2026-07-01",
        "last_payment_date": "2026-07-10",
        "product_total": product_total,
        "product_name": "E-lite",
        "agent_id": "agent-1",
        "manager_id": "manager-1",
        "payment_count": 2,
        "payment_types": ["PRODUCT"],
    }


def _customer_with_products(product_statuses, customer_status="completed"):
    purchases = []
    for idx, status in enumerate(product_statuses):
        purchases.append(
            {
                "purchase_date": f"2026-07-0{idx + 1}",
                "purchase_type": "Installment",
                "status": status if status == "closed" else None,
                "product": {
                    "_id": f"product-{idx + 1}",
                    "name": f"Product {idx + 1}",
                    "quantity": 1,
                    "price": 500.0,
                    "total": 500.0,
                    "status": status,
                },
            }
        )
    return {
        "_id": ObjectId("6852e7e6e564fbc5cca14104"),
        "name": "Test Customer",
        "phone_number": "0500000000",
        "agent_id": "agent-1",
        "manager_id": "manager-1",
        "status": customer_status,
        "purchases": purchases,
    }


class CustomerLiabilityServiceTests(unittest.TestCase):
    def setUp(self):
        self.agent_doc = {"name": "Agent One", "branch": "HQ"}
        self.manager_doc = {"name": "Manager One", "branch": "HQ"}
        self.settings = {"delivery_sla_days": 7, "inactive_customer_days": 21, "manual_adjustment_threshold": 1000.0}
        self.inventory_map = {
            "product-1": {"qty": 3, "cost_price": 100.0, "price": 150.0},
            "product-2": {"qty": 2, "cost_price": 100.0, "price": 150.0},
        }

    def _build(self, payment_row=None, customer_doc=None, package_doc=None, undelivered_doc=None, returns_docs=None, ledger=None):
        payment_row = payment_row or _payment_row()
        customer_doc = customer_doc or _customer_with_products(["payment_ongoing"], customer_status="active")
        return svc._build_row(
            payment_row=payment_row,
            customer_doc=customer_doc,
            package_doc=package_doc,
            undelivered_doc=undelivered_doc,
            agent_doc=self.agent_doc,
            manager_doc=self.manager_doc,
            settings=self.settings,
            inventory_map=self.inventory_map,
            ledger=ledger or [],
        )

    def test_active_product_with_product_payments_is_included(self):
        row = self._build(payment_row=_payment_row(verified=200.0))
        self.assertEqual(row["current_liability"], 200.0)
        self.assertEqual(row["liability_category"], "partially-paid")

    def test_completed_product_with_product_payments_is_excluded(self):
        customer = _customer_with_products(["completed"], customer_status="active")
        classification, _, _, _ = svc._classify_payment_group(_payment_row(verified=300.0), customer, svc._empty_exclusion_stats())
        self.assertEqual(classification, "excluded_completed")

    def test_closed_product_with_product_payments_is_excluded_from_active_liability(self):
        customer = _customer_with_products(["closed"], customer_status="active")
        classification, _, _, _ = svc._classify_payment_group(_payment_row(verified=300.0), customer, svc._empty_exclusion_stats())
        self.assertEqual(classification, "excluded_closed")

    def test_customer_with_active_and_completed_product_only_keeps_active_purchase(self):
        customer = _customer_with_products(["payment_ongoing", "completed"])
        active = svc._classify_payment_group(_payment_row(product_index=0), customer, svc._empty_exclusion_stats())[0]
        completed = svc._classify_payment_group(_payment_row(product_index=1), customer, svc._empty_exclusion_stats())[0]
        self.assertEqual(active, "eligible")
        self.assertEqual(completed, "excluded_completed")

    def test_customer_with_active_and_closed_product_only_keeps_active_purchase(self):
        customer = _customer_with_products(["payment_ongoing", "closed"])
        active = svc._classify_payment_group(_payment_row(product_index=0), customer, svc._empty_exclusion_stats())[0]
        closed = svc._classify_payment_group(_payment_row(product_index=1), customer, svc._empty_exclusion_stats())[0]
        self.assertEqual(active, "eligible")
        self.assertEqual(closed, "excluded_closed")

    def test_susu_payment_is_excluded_from_product_pipeline(self):
        pipeline = svc._payments_group_pipeline({"agent_id": "", "branch": "", "start_dt": None, "end_dt": None, "as_of_date": "2026-07-27"})
        self.assertEqual(pipeline[0]["$match"]["$and"][1]["payment_type"]["$in"], ["PRODUCT", "WITHDRAWAL", None])

    def test_customer_with_susu_and_product_payments_only_uses_product_amount(self):
        row = self._build(payment_row=_payment_row(verified=200.0))
        self.assertEqual(row["verified_amount_paid"], 200.0)

    def test_product_payment_plus_withdrawal_subtracts_withdrawal(self):
        row = self._build(payment_row=_payment_row(verified=500.0, reversals=150.0))
        self.assertEqual(row["verified_amount_paid"], 350.0)
        self.assertEqual(row["current_liability"], 350.0)

    def test_completed_customer_with_another_active_purchase_uses_purchase_not_customer_status(self):
        customer = _customer_with_products(["payment_ongoing"], customer_status="completed")
        row = self._build(payment_row=_payment_row(verified=250.0), customer_doc=customer)
        self.assertEqual(row["current_liability"], 250.0)

    def test_legacy_payment_with_valid_product_link_is_allowed(self):
        payment = _payment_row(verified=180.0)
        payment["payment_types"] = [None]
        customer = _customer_with_products(["payment_ongoing"], customer_status="active")
        classification, _, _, _ = svc._classify_payment_group(payment, customer, svc._empty_exclusion_stats())
        self.assertEqual(classification, "eligible")

    def test_ambiguous_legacy_payment_is_excluded_and_reported(self):
        payment = _payment_row(product_index=4, verified=180.0)
        payment["payment_types"] = [None]
        stats = svc._empty_exclusion_stats()
        classification, _, _, _ = svc._classify_payment_group(payment, _customer_with_products(["payment_ongoing"]), stats)
        self.assertEqual(classification, "ambiguous_legacy")
        self.assertEqual(stats["ambiguous_legacy"]["amount"], 180.0)

    def test_cards_register_charts_and_exports_can_share_matching_totals(self):
        rows = [
            {"customer_id": "cust-1", "purchase_index": 0, "current_liability": 100.0, "liability_category": "partially-paid", "sla_status": "within-sla", "estimated_cost_to_fulfil": 50.0, "delivery_status": "Awaiting Delivery", "quantity": 1.0, "stock_availability": 1.0, "first_payment_date": "2026-07-01", "purchase_date": "2026-07-01", "risk_level": "low", "branch": "HQ", "days_awaiting_delivery": 2, "product_name": "A", "margin_at_risk": 10.0, "fully_paid_customer_count": 0, "agent_name": "Agent", "customer_name": "Customer", "customer_phone": "0", "purchase_ref": "1", "agreed_order_value": 200.0, "verified_amount_paid": 100.0, "remaining_balance": 100.0, "payment_stage": "partially-paid", "overpayment": 0.0, "stock_status": "matched"},
            {"customer_id": "cust-2", "purchase_index": 1, "current_liability": 200.0, "liability_category": "fully-paid-awaiting-delivery", "sla_status": "breached", "estimated_cost_to_fulfil": 80.0, "delivery_status": "Awaiting Delivery", "quantity": 1.0, "stock_availability": 0.0, "first_payment_date": "2026-07-02", "purchase_date": "2026-07-02", "risk_level": "high", "branch": "HQ", "days_awaiting_delivery": 10, "product_name": "B", "margin_at_risk": 20.0, "fully_paid_customer_count": 1, "agent_name": "Agent", "customer_name": "Customer 2", "customer_phone": "1", "purchase_ref": "2", "agreed_order_value": 200.0, "verified_amount_paid": 200.0, "remaining_balance": 0.0, "payment_stage": "fully-paid", "overpayment": 0.0, "stock_status": "legacy-unmatched"},
        ]
        with (
            patch.object(svc, "_build_liability_rows", return_value=(rows, [], svc._empty_exclusion_stats(), [])),
            patch.object(svc, "get_liability_settings", return_value=self.settings),
        ):
            summary = svc.get_liability_summary_cached.uncached((("as_of_date", "2026-07-27"), ("start_date", ""), ("end_date", ""), ("agent_id", ""), ("branch", ""), ("product_query", ""), ("search", ""), ("liability_category", ""), ("payment_stage", ""), ("delivery_stage", ""), ("risk_level", ""), ("sla_status", ""), ("page", 1), ("page_size", 25), ("skip", 0)))
            register = svc.get_liability_register({"as_of_date": "2026-07-27", "page": 1, "page_size": 25, "skip": 0, "search": "", "product_query": "", "liability_category": "", "payment_stage": "", "delivery_stage": "", "risk_level": "", "sla_status": ""})
            csv_text = svc.build_liability_csv({"as_of_date": "2026-07-27", "page": 1, "page_size": 25, "skip": 0, "search": "", "product_query": "", "liability_category": "", "payment_stage": "", "delivery_stage": "", "risk_level": "", "sla_status": ""})
        self.assertEqual(summary["cards"][0]["amount"], 300.0)
        self.assertEqual(sum(row["current_liability"] for row in register["rows"]), 300.0)
        self.assertIn("Customer Liability & Fulfilment Control", csv_text)
        self.assertIn("100.0", csv_text)
        self.assertIn("200.0", csv_text)

    def test_verified_delivered_product_is_excluded(self):
        row = self._build(
            payment_row=_payment_row(verified=300.0),
            package_doc={"status": "delivered", "delivered_at": datetime(2026, 7, 3)},
        )
        self.assertIsNone(row)

    def test_fully_paid_date_derives_from_product_ledger(self):
        ledger = [
            {"date": "2026-07-01", "payment_type": "PRODUCT", "amount": 100},
            {"date": "2026-07-03", "payment_type": "PRODUCT", "amount": 400},
        ]
        row = self._build(payment_row=_payment_row(verified=500.0), ledger=ledger)
        self.assertEqual(row["fully_paid_at"], "2026-07-03")

    def test_overpayment_is_capped_at_agreed_product_total(self):
        row = self._build(payment_row=_payment_row(verified=650.0, product_total=500.0))
        self.assertEqual(row["current_liability"], 500.0)

    def test_mark_delivered_updates_purchase_and_delivery_records(self):
        customer = _customer_with_products(["payment_ongoing"], customer_status="active")
        customer_collection = MagicMock()
        customer_collection.find_one.return_value = customer
        customer_collection.update_one.return_value.modified_count = 1
        package_collection = MagicMock()
        undelivered_collection = MagicMock()
        with (
            patch.object(svc, "customers_col", customer_collection),
            patch.object(svc, "packages_col", package_collection),
            patch.object(svc, "undelivered_items_col", undelivered_collection),
            patch.object(svc.cache, "delete_memoized"),
        ):
            result = svc.resolve_liability(
                str(customer["_id"]), 0, "delivered",
                {"user_id": "admin-1", "name": "Admin", "role": "admin"},
                "Confirmed received",
            )
        self.assertEqual(result["resolution"], "delivered")
        update = customer_collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["purchases.0.status"], "delivered")
        self.assertEqual(update["purchases.0.product.status"], "delivered")
        package_collection.update_many.assert_called_once()
        undelivered_collection.update_many.assert_called_once()

    def test_mark_closed_excludes_purchase_without_changing_delivery_records(self):
        customer = _customer_with_products(["payment_ongoing"], customer_status="active")
        customer_collection = MagicMock()
        customer_collection.find_one.return_value = customer
        customer_collection.update_one.return_value.modified_count = 1
        package_collection = MagicMock()
        undelivered_collection = MagicMock()
        with (
            patch.object(svc, "customers_col", customer_collection),
            patch.object(svc, "packages_col", package_collection),
            patch.object(svc, "undelivered_items_col", undelivered_collection),
            patch.object(svc.cache, "delete_memoized"),
        ):
            result = svc.resolve_liability(
                str(customer["_id"]), 0, "closed",
                {"user_id": "admin-1", "name": "Admin", "role": "admin"},
            )
        self.assertEqual(result["resolution"], "closed")
        update = customer_collection.update_one.call_args.args[1]["$set"]
        self.assertEqual(update["purchases.0.status"], "closed")
        package_collection.update_many.assert_not_called()
        undelivered_collection.update_many.assert_not_called()


if __name__ == "__main__":
    unittest.main()
