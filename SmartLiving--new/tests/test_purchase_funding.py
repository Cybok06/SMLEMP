import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from bson import ObjectId


class PurchaseFundingTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        fake_db = types.ModuleType("db")
        fake_db.db = self.db
        accounting = types.ModuleType("accounting_routes.ap_payment_service")
        accounting.account_live_balance = MagicMock(return_value=500)
        accounting.account_label = MagicMock(return_value="Main account")
        accounting.list_payment_accounts = MagicMock(return_value=[])
        spec = importlib.util.spec_from_file_location("funding_under_test", Path(__file__).resolve().parents[1] / "Backend/inventory/purchase_funding.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"db": fake_db, "accounting_routes.ap_payment_service": accounting}):
            spec.loader.exec_module(self.module)
        self.db.inventory_purchase_orders.find_one.return_value = None
        self.session = MagicMock()
        self.db.client.start_session.return_value.__enter__.return_value = self.session
        self.session.with_transaction.side_effect = lambda callback: callback(self.session)
        self.account_id = ObjectId()
        self.db.bank_accounts.find_one_and_update.return_value = {"_id": self.account_id, "account_type": "bank", "currency": "GHS"}
        self.doc = {"po_number": "PO-2026-0001", "supplier_name": "Supplier"}
        self.payload = {"totalAmount": 150, "paymentType": "bank", "paymentSourceId": str(self.account_id), "requestId": str(uuid4())}

    def post(self):
        return self.module.create_funded_order(self.doc, self.payload, {"user_id": "test-user"})

    def test_paid_purchase_posts_linked_withdrawal_in_same_transaction(self):
        result = self.post()
        withdrawal = self.db.withdrawals.insert_one.call_args.args[0]
        self.assertEqual(withdrawal["amount"], 150)
        self.assertEqual(withdrawal["account_id"], self.account_id)
        self.assertEqual(withdrawal["purchase_order_id"], result["_id"])
        self.assertEqual(withdrawal["purpose"], "stocks")
        self.assertEqual(self.db.withdrawals.insert_one.call_args.kwargs["session"], self.session)
        self.assertEqual(self.db.inventory_purchase_orders.insert_one.call_args.kwargs["session"], self.session)
        self.db.ap_bills.insert_one.assert_not_called()

    def test_cash_and_momo(self):
        for mode in ("cash", "mobile_money"):
            self.payload["paymentType"] = mode
            self.db.bank_accounts.find_one_and_update.return_value["account_type"] = mode
            self.assertEqual(self.post()["funding"]["type"], mode)

    def test_insufficient_funds_posts_nothing(self):
        self.payload["totalAmount"] = 501
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            self.post()
        self.db.withdrawals.insert_one.assert_not_called()
        self.db.inventory_purchase_orders.insert_one.assert_not_called()

    def test_credit_creates_unpaid_bill_for_selected_vendor(self):
        self.payload.update(paymentType="credit", paymentDueDate="2026-12-01")
        self.db.ap_bills.find_one.return_value = {"vendor": "V-1", "vendor_name": "Creditor", "currency": "GHS"}
        result = self.post()
        bill = self.db.ap_bills.insert_one.call_args.args[0]
        self.assertEqual((bill["vendor"], bill["amount"], bill["paid"], bill["balance"]), ("V-1", 150, 0, 150))
        self.assertEqual(bill["purchase_order_id"], result["_id"])
        self.assertEqual(bill["due_date"], "2026-12-01")
        self.db.withdrawals.insert_one.assert_not_called()

    def test_repeated_request_returns_existing_without_posting(self):
        existing = {"_id": ObjectId(), "funding": {"amount": 150}}
        self.db.inventory_purchase_orders.find_one.return_value = existing
        self.assertEqual(self.post(), existing)
        self.db.withdrawals.insert_one.assert_not_called()
        self.db.ap_bills.insert_one.assert_not_called()
        self.db.inventory_purchase_orders.insert_one.assert_not_called()

    def test_missing_account_or_creditor_posts_nothing(self):
        self.db.bank_accounts.find_one_and_update.return_value = None
        with self.assertRaisesRegex(ValueError, "account was not found"):
            self.post()
        self.payload.update(paymentType="credit", paymentDueDate="2026-12-01")
        self.db.ap_bills.find_one.return_value = None
        with self.assertRaisesRegex(ValueError, "creditor was not found"):
            self.post()
        self.db.withdrawals.insert_one.assert_not_called()
        self.db.ap_bills.insert_one.assert_not_called()

    def test_credit_requires_valid_due_date(self):
        self.payload.update(paymentType="credit", paymentDueDate="bad")
        with self.assertRaisesRegex(ValueError, "due date"):
            self.post()

    def test_delivery_sync_uses_same_transaction(self):
        sync = MagicMock()
        result = self.module.create_funded_order(self.doc, self.payload, {"user_id": "test-user"}, sync_delivery=sync)
        sync.assert_called_once_with(result, session=self.session)

    def test_invalid_amounts(self):
        for value in (0, -1, "NaN", "Infinity", "bad", 0.001):
            self.payload["totalAmount"] = value
            with self.assertRaises(ValueError):
                self.post()
        self.db.withdrawals.insert_one.assert_not_called()

    def test_wrong_account_type_and_currency(self):
        self.db.bank_accounts.find_one_and_update.return_value["account_type"] = "cash"
        with self.assertRaisesRegex(ValueError, "payment type"):
            self.post()
        self.db.bank_accounts.find_one_and_update.return_value.update(account_type="bank", currency="USD")
        with self.assertRaisesRegex(ValueError, "GHS"):
            self.post()

    def test_creditor_outstanding_is_grouped_and_paid_bills_still_selectable(self):
        self.db.ap_bills.find.return_value = [
            {"_id": ObjectId(), "vendor": "A", "vendor_name": "Vendor", "amount": 100, "paid": 25},
            {"_id": ObjectId(), "vendor": "A", "vendor_name": "Vendor", "balance": 20},
            {"_id": ObjectId(), "vendor": "B", "vendor_name": "Paid vendor", "balance": 0},
        ]
        rows = self.module.funding_options()["creditors"]
        self.assertEqual({r["label"]: r["balance"] for r in rows}, {"Vendor": 95, "Paid vendor": 0})


class AccountBalanceSessionTests(unittest.TestCase):
    def test_purchase_withdrawal_reduces_live_balance_with_session(self):
        fake_db = types.ModuleType("db")
        fake_db.db = MagicMock()
        collections = {}
        def collection(name):
            if name not in collections:
                value = MagicMock()
                value.aggregate.side_effect = lambda *a, **kw: iter([{"total": 150 if name == "withdrawals" else 0}])
                collections[name] = value
            return collections[name]
        fake_db.db.__getitem__.side_effect = collection
        services = types.ModuleType("accounting_services")
        services.post_withdrawal = MagicMock()
        routing = types.ModuleType("services.payment_account_routing")
        routing.confirmed_payment_match = MagicMock(return_value={})
        spec = importlib.util.spec_from_file_location("balance_under_test", Path(__file__).resolve().parents[1] / "accounting_routes/ap_payment_service.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"db": fake_db, "accounting_services": services, "services.payment_account_routing": routing}):
            spec.loader.exec_module(module)
        session = MagicMock()
        self.assertEqual(module.account_live_balance({"_id": ObjectId(), "opening_balance": 500}, session=session), 350)
        for value in collections.values():
            for call in value.aggregate.call_args_list:
                self.assertIs(call.kwargs["session"], session)


if __name__ == "__main__":
    unittest.main()
