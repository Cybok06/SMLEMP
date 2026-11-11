"""Approve a manager expense and debit their sales-close ledger exactly once."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from bson import ObjectId
from db import db


class ExpenseApprovalError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _money(value):
    try:
        result = Decimal(str(value or 0))
        if not result.is_finite():
            raise InvalidOperation()
        return result.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ExpenseApprovalError("The expense or ledger contains an invalid amount.")


def approve_manager_expense(expense_id, actor_id, actor):
    try:
        expense_oid = ObjectId(str(expense_id))
    except (ValueError, TypeError):
        raise ExpenseApprovalError("Invalid expense id.")
    role = (actor.get("role") or "").lower()
    if role not in ("executive", "admin"):
        raise ExpenseApprovalError("Only executives or admins can approve expenses.", 403)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.strftime("%Y-%m-%d")
    transaction_id = f"EXP-{expense_oid}"

    def post(session):
        expense = db.manager_expenses.find_one({"_id": expense_oid}, session=session)
        if not expense:
            raise ExpenseApprovalError("Expense not found.", 404)
        if expense.get("status") == "Approved":
            # Older approvals are intentionally not charged retroactively.
            return {"already_approved": True, "deducted": False,
                    "transaction_id": expense.get("sales_close_transaction_id")}
        if expense.get("sales_close_transaction_id"):
            raise ExpenseApprovalError("This expense already has a deduction. Review its approval record.", 409)
        amount = _money(expense.get("amount"))
        if amount <= 0:
            raise ExpenseApprovalError("Expense amount must be greater than zero.")
        manager_id = str(expense.get("manager_id") or "")
        ids = [manager_id]
        if ObjectId.is_valid(manager_id):
            ids.append(ObjectId(manager_id))
        manager = db.users.find_one({"_id": {"$in": ids}, "role": "manager"}, session=session)
        if not manager:
            raise ExpenseApprovalError("The expense manager was not found.", 404)
        docs = list(db.sales_close.find({"agent_id": manager_id}, session=session))
        docs.sort(key=lambda row: (row.get("date") == today, str(row.get("date") or ""), str(row["_id"])), reverse=True)
        balances = [(row, _money(row.get("total_amount"))) for row in docs]
        available = sum((balance for _, balance in balances), Decimal("0"))
        if available < amount:
            raise ExpenseApprovalError(
                f"Insufficient manager sales-close balance. Available: GHS {available:,.2f}; expense: GHS {amount:,.2f}. Expense remains unapproved.", 409,
            )
        reason = str(expense.get("description") or "").strip() or str(expense.get("category") or "Expense").strip()
        note = f"Expense deduction - {reason}"
        remaining = amount
        debits = []
        for row, balance in balances:
            if remaining <= 0:
                break
            if balance <= 0:
                continue
            take = min(balance, remaining)
            withdrawal = {
                "amount": float(take), "transaction_total": float(amount),
                "transaction_id": transaction_id, "source": "manager_expense",
                "expense_id": str(expense_oid), "expense_category": expense.get("category", ""),
                "by_role": role, "by_name": actor.get("name") or actor.get("username") or role.title(),
                f"by_{role}_id": str(actor_id),
                f"by_{role}_name": actor.get("name") or actor.get("username") or role.title(),
                "withdrawal_date": today, "date": today, "source_date": row.get("date", ""),
                "time": now.strftime("%H:%M:%S"), "at": now, "note": note,
            }
            result = db.sales_close.update_one(
                {"_id": row["_id"], "agent_id": manager_id, "total_amount": row.get("total_amount")},
                {"$set": {"total_amount": float(balance - take), "updated_at": now, "last_withdrawal_at": now},
                 "$push": {"withdrawals": withdrawal}}, session=session,
            )
            if result.modified_count != 1:
                raise ExpenseApprovalError("The manager balance changed. Retry approval; no partial deduction will be saved.", 409)
            debits.append({"ledger_id": str(row["_id"]), "date": row.get("date", ""), "amount": float(take)})
            remaining -= take
        if remaining:
            raise ExpenseApprovalError("Unable to deduct the full expense. Expense remains unapproved.", 409)
        result = db.manager_expenses.update_one(
            {"_id": expense_oid, "status": {"$ne": "Approved"}, "sales_close_transaction_id": {"$exists": False}},
            {"$set": {"status": "Approved", "approved_by": str(actor_id), "approved_at": now,
                      "updated_at": now, "sales_close_transaction_id": transaction_id,
                      "sales_close_deducted_amount": float(amount), "sales_close_deducted_at": now,
                      "sales_close_debits": debits}}, session=session,
        )
        if result.modified_count != 1:
            raise ExpenseApprovalError("The expense changed. Retry approval; no partial deduction will be saved.", 409)
        return {"already_approved": False, "deducted": True, "amount": float(amount),
                "transaction_id": transaction_id, "available": float(available - amount)}

    with db.client.start_session() as session:
        return session.with_transaction(post)
