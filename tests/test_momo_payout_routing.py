import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

import payment
from services import payment_account_routing as routing


class MomoPayoutRoutingTests(unittest.TestCase):
    def test_confirmed_match_prefers_account_id_and_limits_legacy_fallback(self):
        account = {"_id": ObjectId(), "bank_name": "Smartliving Bank", "account_no": "1234567890"}
        match = routing.confirmed_payment_match(account)
        self.assertEqual(match["$or"][0]["account_id"]["$in"], [account["_id"], str(account["_id"])])
        self.assertEqual(match["$or"][1]["account_last4"], "7890")
        self.assertEqual(match["$or"][1]["account_id"], {"$exists": False})

    def test_payout_snapshot_uses_immutable_account_id(self):
        account = {
            "_id": ObjectId(), "account_name": "Smartliving Bank Account",
            "account_type": "bank", "bank_name": "Smartliving Bank", "account_no": "1234567890",
        }
        snapshot = routing.payout_snapshot(account)
        self.assertEqual(snapshot["payout_account_id"], account["_id"])
        self.assertEqual(snapshot["payout_account_last4"], "7890")

    def test_verified_momo_payment_is_confirmed_and_credited_to_snapshotted_account(self):
        attempt_id, customer_id, account_id = ObjectId(), ObjectId(), ObjectId()
        claimed = {
            "_id": attempt_id, "reference": "SLMOMO1", "status": "pending",
            "customer_id": customer_id, "agent_id": "agent", "recorded_by_agent_id": "agent",
            "manager_id": ObjectId(), "amount": 125.5, "amount_pesewas": 12550,
            "currency": "GHS", "date": "2026-09-03", "payment_type": "PRODUCT",
            "product_index": 0, "product_name": "Card", "product_total": 500,
            "email": "payer@example.com", "momo_phone": "0530000000", "momo_provider": "mtn",
            "payout_account_id": account_id, "payout_account_name": "Smartliving Bank Account",
            "payout_account_type": "bank", "payout_bank_name": "Smartliving Bank",
            "payout_account_last4": "7890",
        }
        paystack_data = {
            "status": "success", "reference": "SLMOMO1", "amount": 12550,
            "currency": "GHS", "channel": "mobile_money", "id": 42,
        }
        inserted = MagicMock(inserted_id=ObjectId())
        with (
            patch.object(payment.payments_collection, "find_one", return_value=None),
            patch.object(payment.paystack_attempts_collection, "find_one", return_value=claimed),
            patch.object(payment.paystack_attempts_collection, "find_one_and_update", return_value=claimed),
            patch.object(payment.payments_collection, "insert_one", return_value=inserted) as insert_payment,
            patch.object(payment.customers_collection, "update_one"),
            patch.object(payment.paystack_attempts_collection, "update_one"),
        ):
            _doc, created = payment._finalize_momo_attempt("SLMOMO1", paystack_data)
        self.assertTrue(created)
        saved = insert_payment.call_args.args[0]
        self.assertEqual(saved["status"], "confirmed")
        self.assertEqual(saved["account_id"], account_id)
        self.assertEqual(saved["accounting_credit_source"], "paystack_momo")


if __name__ == "__main__":
    unittest.main()
