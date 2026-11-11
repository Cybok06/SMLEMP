import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId
from flask import Flask
from pymongo.errors import OperationFailure

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path, overrides):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, overrides):
        spec.loader.exec_module(module)
    return module


class ManagerExpenseApprovalTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.fake_db = types.ModuleType("db")
        self.fake_db.db = self.db
        self.module = load_module("expense_service_test", "services/manager_expense_approval.py", {"db": self.fake_db})
        self.mid, self.eid = ObjectId(), ObjectId()
        self.expense = {"_id": self.eid, "manager_id": str(self.mid), "amount": 200,
                        "status": "Unapproved", "description": "Transport to warehouse", "category": "Transport"}
        self.ledgers = [{"_id": ObjectId(), "agent_id": str(self.mid), "date": "2026-01-02", "total_amount": 500}]
        self.db.manager_expenses.find_one.side_effect = lambda *a, **k: copy.deepcopy(self.expense)
        self.db.users.find_one.return_value = {"_id": self.mid, "role": "manager"}
        self.db.sales_close.find.side_effect = lambda *a, **k: copy.deepcopy(self.ledgers)
        self.db.sales_close.update_one.side_effect = self.update_ledger
        self.db.manager_expenses.update_one.side_effect = self.update_expense
        self.session = MagicMock()
        self.db.client.start_session.return_value.__enter__.return_value = self.session
        self.session.with_transaction.side_effect = self.transaction
        self.actor = {"role": "executive", "name": "Executive"}

    def transaction(self, callback):
        old_expense, old_ledgers = copy.deepcopy(self.expense), copy.deepcopy(self.ledgers)
        try:
            return callback(self.session)
        except Exception:
            self.expense, self.ledgers = old_expense, old_ledgers
            raise

    def update_ledger(self, query, update, **kwargs):
        self.assertIs(kwargs["session"], self.session)
        row = next(r for r in self.ledgers if r["_id"] == query["_id"])
        self.assertEqual(query["total_amount"], row["total_amount"])
        row.update(update["$set"])
        row.setdefault("withdrawals", []).append(update["$push"]["withdrawals"])
        return MagicMock(modified_count=1)

    def update_expense(self, query, update, **kwargs):
        self.assertIs(kwargs["session"], self.session)
        self.expense.update(update["$set"])
        return MagicMock(modified_count=1)

    def approve(self):
        return self.module.approve_manager_expense(str(self.eid), "exec-1", self.actor)

    def test_approval_deducts_200_and_records_reason(self):
        result = self.approve()
        self.assertEqual(self.ledgers[0]["total_amount"], 300)
        entry = self.ledgers[0]["withdrawals"][0]
        self.assertEqual(entry["note"], "Expense deduction - Transport to warehouse")
        self.assertEqual(entry["expense_id"], str(self.eid))
        self.assertEqual(entry["by_executive_id"], "exec-1")
        self.assertEqual(self.expense["status"], "Approved")
        self.assertEqual(self.expense["sales_close_transaction_id"], result["transaction_id"])
        # This is an expense, so there is no credit to an executive ledger.
        self.assertEqual(len(self.ledgers), 1)
        self.assertEqual(self.db.sales_close.update_one.call_count, 1)

    def test_repeat_approval_does_not_deduct_twice(self):
        self.approve()
        result = self.approve()
        self.assertTrue(result["already_approved"])
        self.assertEqual(self.ledgers[0]["total_amount"], 300)
        self.assertEqual(len(self.ledgers[0]["withdrawals"]), 1)

    def test_legacy_approved_expense_is_not_charged_retroactively(self):
        self.expense["status"] = "Approved"
        self.assertTrue(self.approve()["already_approved"])
        self.db.sales_close.update_one.assert_not_called()

    def test_split_across_days_has_one_transaction_and_preserves_collection_total(self):
        self.ledgers[0]["total_amount"] = 80
        self.ledgers.append({"_id": ObjectId(), "agent_id": str(self.mid), "date": "2026-01-01", "total_amount": "150.00"})
        self.approve()
        entries = [r["withdrawals"][0] for r in self.ledgers]
        self.assertEqual([r["total_amount"] for r in self.ledgers], [0, 30])
        self.assertEqual([e["amount"] for e in entries], [80, 120])
        self.assertEqual(len({e["transaction_id"] for e in entries}), 1)
        self.assertEqual(sum(r["total_amount"] for r in self.ledgers) + sum(e["amount"] for e in entries), 230)

    def test_insufficient_balance_leaves_expense_and_ledger_unchanged(self):
        self.ledgers[0]["total_amount"] = 199
        with self.assertRaisesRegex(self.module.ExpenseApprovalError, "Insufficient"):
            self.approve()
        self.assertEqual(self.expense["status"], "Unapproved")
        self.db.sales_close.update_one.assert_not_called()

    def test_failure_after_first_debit_rolls_back_transaction(self):
        self.ledgers[0]["total_amount"] = 100
        self.ledgers.append({"_id": ObjectId(), "agent_id": str(self.mid), "date": "2026-01-01", "total_amount": 150})
        def fail_second(query, update, **kwargs):
            if query["_id"] == self.ledgers[1]["_id"]:
                raise OperationFailure("Simulated database failure")
            return self.update_ledger(query, update, **kwargs)
        self.db.sales_close.update_one.side_effect = fail_second
        with self.assertRaises(OperationFailure):
            self.approve()
        self.assertEqual([r["total_amount"] for r in self.ledgers], [100, 150])
        self.assertEqual(self.expense["status"], "Unapproved")
        self.assertTrue(all(not r.get("withdrawals") for r in self.ledgers))

    def test_concurrent_expense_change_rolls_back_debit(self):
        self.db.manager_expenses.update_one.side_effect = lambda *a, **k: MagicMock(modified_count=0)
        with self.assertRaisesRegex(self.module.ExpenseApprovalError, "expense changed"):
            self.approve()
        self.assertEqual(self.ledgers[0]["total_amount"], 500)
        self.assertFalse(self.ledgers[0].get("withdrawals"))

    def test_invalid_amounts_do_not_post(self):
        for amount in (0, -1, "NaN", "Infinity", "bad"):
            self.expense["amount"] = amount
            with self.assertRaises(self.module.ExpenseApprovalError):
                self.approve()
        self.db.sales_close.update_one.assert_not_called()

    def test_admin_approval_and_category_fallback(self):
        self.actor = {"role": "admin", "name": "Admin"}
        self.expense["description"] = ""
        self.approve()
        entry = self.ledgers[0]["withdrawals"][0]
        self.assertEqual(entry["note"], "Expense deduction - Transport")
        self.assertEqual(entry["by_admin_id"], "exec-1")
        self.assertNotIn("by_executive_id", entry)

    def test_unauthorized_actor_cannot_post(self):
        self.actor["role"] = "manager"
        with self.assertRaises(self.module.ExpenseApprovalError) as error:
            self.approve()
        self.assertEqual(error.exception.status, 403)
        self.db.sales_close.update_one.assert_not_called()

    def test_missing_manager_or_expense(self):
        self.db.users.find_one.return_value = None
        with self.assertRaisesRegex(self.module.ExpenseApprovalError, "manager was not found"):
            self.approve()
        self.expense = None
        with self.assertRaisesRegex(self.module.ExpenseApprovalError, "Expense not found"):
            self.approve()

    def test_manager_history_consolidates_split_expense(self):
        self.ledgers[0]["total_amount"] = 80
        self.ledgers.append({"_id": ObjectId(), "agent_id": str(self.mid), "date": "2026-01-01", "total_amount": 150})
        self.approve()
        self.db.__getitem__.side_effect = lambda name: getattr(self.db, name)
        module = load_module("manager_history_test", "manager_sales_close.py", {"db": self.fake_db})
        app = Flask(__name__)
        with app.test_request_context("/manager-close/manager-withdrawals"), patch.object(module, "_ensure_manager_scope_or_redirect", return_value=(str(self.mid), {"name": "Manager A"})):
            data = module.manager_withdrawals_history().get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["withdrawals"][0]["amount"], 200)
        self.assertEqual(data["withdrawals"][0]["note"], "Expense deduction - Transport to warehouse")

    def test_approval_route_and_delete_guard(self):
        self.db.__getitem__.side_effect = lambda name: getattr(self.db, name)
        module = load_module("executive_expense_test", "executive_expense.py", {
            "db": self.fake_db, "services.manager_expense_approval": self.module,
        })
        app = Flask(__name__)
        app.register_blueprint(module.executive_expense_bp)
        with patch.object(module, "_ensure_exec_or_redirect", return_value=("exec-1", self.actor)):
            client = app.test_client()
            response = client.post("/executive-expense/approve", data={"id": str(self.eid)})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json["deducted"])
            self.db.manager_expenses.delete_one.return_value = MagicMock(deleted_count=0)
            response = client.post("/executive-expense/delete", data={"id": str(self.eid)})
            self.assertEqual(response.status_code, 409)
            query = self.db.manager_expenses.delete_one.call_args.args[0]
            self.assertEqual(query["status"], {"$ne": "Approved"})
            self.assertEqual(query["sales_close_transaction_id"], {"$exists": False})


if __name__ == "__main__":
    unittest.main()
