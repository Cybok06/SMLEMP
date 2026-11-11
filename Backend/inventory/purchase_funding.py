"""Atomically link purchase orders to accounting withdrawals or creditor bills."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from bson import ObjectId
from db import db
from accounting_routes.ap_payment_service import account_live_balance, account_label, list_payment_accounts


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            raise ValueError()
        return float(amount.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid total purchase amount greater than zero.")


def funding_options():
    creditors = {}
    for bill in db.ap_bills.find({}):
        name = bill.get("vendor_name") or bill.get("vendor")
        if not name:
            continue
        currency = (bill.get("currency") or "GHS").upper().replace("GHC", "GHS")
        key = (bill.get("vendor") or name, name, currency)
        row = creditors.setdefault(key, {
            "id": str(bill["_id"]), "label": name, "currency": currency, "balance": 0.0,
        })
        if bill.get("status") not in ("cancelled", "void"):
            row["balance"] += max(float(bill.get("balance", float(bill.get("amount") or 0) - float(bill.get("paid") or 0)) or 0), 0)
    return {"accounts": list_payment_accounts(), "creditors": [
        {**row, "balance": round(row["balance"], 2)}
        for row in sorted(creditors.values(), key=lambda row: row["label"].lower())
    ]}


def create_funded_order(doc, payload, identity, sync_delivery=None):
    amount = money(payload.get("totalAmount"))
    if amount <= 0:
        raise ValueError("Total amount must be at least 0.01.")
    mode = payload.get("paymentType")
    if mode not in ("bank", "mobile_money", "cash", "credit"):
        raise ValueError("Select bank, MoMo, cash, or a creditor.")
    try:
        selection = ObjectId(str(payload.get("paymentSourceId") or ""))
        token = str(UUID(str(payload.get("requestId") or "")))
    except (ValueError, TypeError):
        raise ValueError("Select a payment source and submit a valid purchase request.")
    due = None
    if mode == "credit":
        try:
            due = datetime.strptime(str(payload.get("paymentDueDate") or ""), "%Y-%m-%d")
        except ValueError:
            raise ValueError("Select a payment due date for the creditor bill.")
    actor = str(identity.get("user_id") or identity.get("id") or "")
    request_key = f"{actor}:{token}"
    orders = db.inventory_purchase_orders
    orders.create_index("funding_request_key", unique=True, sparse=True)
    doc = {**doc, "_id": ObjectId(), "total_amount": amount, "funding_request_key": request_key}
    now = datetime.utcnow()

    def post(session):
        existing = orders.find_one({"funding_request_key": request_key}, session=session)
        if existing:
            return existing
        funding = {"type": mode, "sourceId": str(selection), "amount": amount, "currency": "GHS"}
        if mode == "credit":
            creditor = db.ap_bills.find_one({"_id": selection}, session=session)
            if not creditor:
                raise ValueError("Selected creditor was not found.")
            if (creditor.get("currency") or "GHS").upper().replace("GHC", "GHS") != "GHS":
                raise ValueError("Select a GHS creditor for this purchase.")
            name = creditor.get("vendor_name") or creditor.get("vendor")
            bill_id = ObjectId()
            bill_no = f"PO-{doc['_id']}"
            db.ap_bills.insert_one({
                "_id": bill_id, "bill_no": bill_no, "no": bill_no,
                "vendor": creditor.get("vendor") or name, "vendor_name": name,
                "reference": doc["po_number"], "purchase_order_id": doc["_id"],
                "source": "inventory_purchase_order", "currency": "GHS",
                "bill_date": now.strftime("%Y-%m-%d"), "bill_date_dt": now,
                "due_date": due.strftime("%Y-%m-%d"), "due_date_dt": due,
                "amount": amount, "paid": 0.0, "balance": amount, "status": "approved",
                "notes": doc.get("notes") or "", "payment_history": [],
                "amount_history": [{"type": "initial_amount", "amount": amount,
                                    "note": doc["po_number"], "date": now, "created_at": now}],
                "created_at": now, "created_by": actor,
            }, session=session)
            funding.update(label=name, billId=str(bill_id))
        else:
            # Writing the account serializes concurrent purchase postings before
            # checking the balance in the same transaction snapshot.
            account = db.bank_accounts.find_one_and_update(
                {"_id": selection}, {"$inc": {"purchase_posting_version": 1}}, session=session,
            )
            if not account:
                raise ValueError("Selected account was not found.")
            if (account.get("account_type") or "bank") != mode:
                raise ValueError("Selected account does not match the payment type.")
            if (account.get("currency") or "GHS").upper().replace("GHC", "GHS") != "GHS":
                raise ValueError("Select a GHS account for this purchase.")
            balance = account_live_balance(account, session=session)
            if amount > balance + 0.005:
                raise ValueError(f"Insufficient account balance. Available: GHS {balance:,.2f}.")
            withdrawal_id = ObjectId()
            label = account_label(account)
            db.withdrawals.insert_one({
                "_id": withdrawal_id, "date_dt": now, "amount": amount,
                "account_type": mode, "account_id": selection, "purpose": "stocks",
                "purpose_note": f"Purchase order {doc['po_number']}",
                "counterparty": doc["supplier_name"], "posted": True,
                "journal_ref": None, "source": "inventory_purchase_order",
                "purchase_order_id": doc["_id"], "purchase_order_no": doc["po_number"],
                "created_by": actor, "created_at": now,
            }, session=session)
            funding.update(label=label, withdrawalId=str(withdrawal_id))
        result = {**doc, "funding": funding}
        orders.insert_one(result, session=session)
        if sync_delivery is not None:
            sync_delivery(result, session=session)
        return result

    with db.client.start_session() as session:
        return session.with_transaction(post)
