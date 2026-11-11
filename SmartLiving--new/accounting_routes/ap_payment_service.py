from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from accounting_services import post_withdrawal
from db import db
from services.payment_account_routing import confirmed_payment_match


accounts_col = db["bank_accounts"]
bills_col = db["ap_bills"]
payments_col = db["payments"]
tax_col = db["tax_records"]
sbdc_col = db["s_bdc_payment"]
manager_deposits_col = db["manager_deposits"]
withdrawals_col = db["withdrawals"]
transfers_col = db["account_transfers"]


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _sum(collection, pipeline: list[dict[str, Any]], session=None) -> float:
    row = next(collection.aggregate(pipeline + [{"$group": {"_id": None, "total": {"$sum": "$amount"}}}], **({"session": session} if session is not None else {})), None)
    return _number((row or {}).get("total"))


def account_live_balance(account: dict[str, Any], session=None) -> float:
    def sum_in_session(collection, pipeline):
        return _sum(collection, pipeline, session=session)

    account_id = account["_id"]
    confirmed_in = sum_in_session(payments_col, [{"$match": confirmed_payment_match(account)}])
    manager_in = sum_in_session(manager_deposits_col, [{"$match": {"bank_account_id": str(account_id), "status": {"$in": ["submitted", "approved"]}}}])
    transfer_in = sum_in_session(transfers_col, [{"$match": {"to_account_id": account_id}}])
    ptax_out = sum_in_session(tax_col, [{"$match": {"source_bank_id": account_id, "type": {"$regex": r"^p[\s_-]*tax$", "$options": "i"}}}])
    withdrawal_out = sum_in_session(withdrawals_col, [{"$match": {"account_id": account_id, "source": {"$ne": "account_transfer"}}}])
    transfer_out = sum_in_session(transfers_col, [{"$match": {"from_account_id": account_id}}])
    bdc_pipeline = [
        {"$match": {"bank_paid_history": {"$exists": True, "$ne": []}}},
        {"$unwind": "$bank_paid_history"},
        {"$match": {"bank_paid_history.bank_id": account_id}},
        {"$group": {"_id": None, "total": {"$sum": "$bank_paid_history.amount"}}},
    ]
    bdc_row = next(sbdc_col.aggregate(bdc_pipeline, **({"session": session} if session is not None else {})), None)
    bdc_out = _number((bdc_row or {}).get("total"))
    return _number(account.get("opening_balance")) + confirmed_in + manager_in + transfer_in - ptax_out - withdrawal_out - transfer_out - bdc_out


def account_label(account: dict[str, Any]) -> str:
    account_type = (account.get("account_type") or "bank").lower()
    type_label = "MoMo" if account_type == "mobile_money" else ("Cash" if account_type == "cash" else "Bank")
    name = account.get("account_name") or account.get("bank_name") or type_label
    number = str(account.get("account_no") or account.get("account_number") or "")
    return f"{name} · {type_label}" + (f" · {number[-4:]}" if number else "")


def list_payment_accounts() -> list[dict[str, Any]]:
    rows = []
    for account in accounts_col.find({}).sort([("account_type", 1), ("bank_name", 1), ("account_name", 1)]):
        rows.append({
            "id": str(account["_id"]),
            "label": account_label(account),
            "type": account.get("account_type") or "bank",
            "currency": (account.get("currency") or "GHS").upper(),
            "balance": round(account_live_balance(account), 2),
        })
    return rows


def pay_ap_bill(*, bill_id: str, account_id: str, amount: float, payment_date: datetime, method: str, note: str, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        bill_oid = ObjectId(bill_id)
        account_oid = ObjectId(account_id)
    except Exception:
        raise ValueError("Invalid bill or payment account selection.")
    bill = bills_col.find_one({"_id": bill_oid})
    account = accounts_col.find_one({"_id": account_oid})
    if not bill:
        raise ValueError("Bill not found.")
    if not account:
        raise ValueError("Selected payment account was not found.")
    amount = round(_number(amount), 2)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    bill_balance = max(_number(bill.get("balance", _number(bill.get("amount")) - _number(bill.get("paid")))), 0.0)
    if amount > bill_balance + 0.005:
        raise ValueError(f"Payment cannot exceed the bill balance of {bill_balance:,.2f}.")
    account_balance = account_live_balance(account)
    if amount > account_balance + 0.005:
        raise ValueError(f"Insufficient account balance. Available: {account_balance:,.2f}.")
    bill_currency = (bill.get("currency") or "GHS").upper().replace("GHC", "GHS")
    account_currency = (account.get("currency") or "GHS").upper().replace("GHC", "GHS")
    if bill_currency != account_currency:
        raise ValueError(f"Currency mismatch: bill is {bill_currency}, account is {account_currency}.")

    actor = identity or {}
    account_name = account_label(account)
    withdrawal = post_withdrawal({
        "amount": amount,
        "account_type": account.get("account_type") or "bank",
        "account_id": str(account_oid),
        "purpose": "creditors",
        "purpose_note": note or f"AP bill payment: {bill.get('bill_no') or bill.get('no') or ''}",
        "counterparty": bill.get("vendor_name") or bill.get("vendor") or "Vendor",
        "date_dt": payment_date,
        "created_by": actor.get("user_id"),
    })
    if not withdrawal.get("ok"):
        raise ValueError(withdrawal.get("message") or "Account withdrawal could not be posted.")
    withdrawal_oid = ObjectId(withdrawal["withdrawal_id"])
    new_paid = _number(bill.get("paid")) + amount
    new_balance = max(_number(bill.get("amount")) - new_paid, 0.0)
    new_status = "paid" if new_balance <= 0.005 else "partial"
    payment_entry = {
        "amount": amount,
        "method": method or account_name,
        "note": note,
        "date": payment_date,
        "created_at": datetime.utcnow(),
        "account_id": account_oid,
        "account_name": account_name,
        "account_type": account.get("account_type") or "bank",
        "withdrawal_id": withdrawal_oid,
        "created_by": {"user_id": actor.get("user_id"), "name": actor.get("name"), "role": actor.get("role")},
    }
    result = bills_col.update_one(
        {"_id": bill_oid, "paid": bill.get("paid")},
        {"$set": {"paid": new_paid, "balance": new_balance, "status": new_status, "updated_at": datetime.utcnow()}, "$push": {"payment_history": payment_entry}},
    )
    if not result.modified_count:
        withdrawals_col.delete_one({"_id": withdrawal_oid})
        raise ValueError("The bill changed while the payment was posting. Please try again.")
    withdrawals_col.update_one({"_id": withdrawal_oid}, {"$set": {
        "source": "ap_bill_payment", "ap_bill_id": bill_oid,
        "ap_bill_no": bill.get("bill_no") or bill.get("no") or "", "payment_account_name": account_name,
    }})
    return {"paid": round(new_paid, 2), "balance": round(new_balance, 2), "status": new_status, "withdrawal_id": str(withdrawal_oid)}
