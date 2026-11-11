from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from db import db


accounts_col = db["bank_accounts"]
routes_col = db["accounting_payment_routes"]
activity_logs_col = db["activity_logs"]

MOMO_ROUTE_ID = "momo"


def _oid(value: Any) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def account_label(account: dict | None) -> str:
    account = account or {}
    return account.get("account_name") or account.get("bank_name") or "Payment account"


def get_momo_payout_account() -> dict | None:
    route = routes_col.find_one({"_id": MOMO_ROUTE_ID, "enabled": True})
    account_id = _oid((route or {}).get("account_id"))
    if not account_id:
        return None
    account = accounts_col.find_one({"_id": account_id})
    if not account or (account.get("currency") or "GHS").upper() != "GHS":
        return None
    return account


def set_momo_payout_account(account_id: str, enabled: bool, identity: dict) -> dict | None:
    oid = _oid(account_id)
    account = accounts_col.find_one({"_id": oid}) if oid else None
    if not account:
        raise ValueError("Account not found.")
    if (account.get("currency") or "GHS").upper() != "GHS":
        raise ValueError("The MoMo payout account must use GHS.")

    now = datetime.utcnow()
    current = routes_col.find_one({"_id": MOMO_ROUTE_ID}) or {}
    if enabled:
        routes_col.update_one(
            {"_id": MOMO_ROUTE_ID},
            {"$set": {
                "account_id": oid,
                "enabled": True,
                "updated_at": now,
                "updated_by": identity.get("user_id"),
                "updated_by_name": identity.get("name"),
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    elif _oid(current.get("account_id")) == oid:
        routes_col.update_one(
            {"_id": MOMO_ROUTE_ID},
            {"$set": {"enabled": False, "updated_at": now, "updated_by": identity.get("user_id"), "updated_by_name": identity.get("name")}},
            upsert=True,
        )

    activity_logs_col.insert_one({
        "user_id": str(identity.get("user_id") or ""),
        "username": identity.get("name") or "",
        "role": identity.get("role") or "",
        "action": "momo_payout_account_enabled" if enabled else "momo_payout_account_disabled",
        "action_label": ("Enabled" if enabled else "Disabled") + " MoMo payout routing",
        "entity_type": "bank_account",
        "entity_id": str(oid),
        "meta": {"account_name": account_label(account), "previous_account_id": str(current.get("account_id") or "")},
        "timestamp": now,
        "day": now.strftime("%Y-%m-%d"),
        "month": now.strftime("%Y-%m"),
    })
    return get_momo_payout_account()


def payout_snapshot(account: dict) -> dict:
    oid = account["_id"]
    account_no = str(account.get("account_no") or account.get("account_number") or "")
    return {
        "payout_account_id": oid,
        "payout_account_name": account_label(account),
        "payout_account_type": account.get("account_type") or "bank",
        "payout_bank_name": account.get("bank_name") or "",
        "payout_account_last4": account_no[-4:] if len(account_no) >= 4 else account_no,
    }


def confirmed_payment_match(account: dict) -> dict:
    """Match new immutable account links plus unlinked legacy name/last-four records."""
    oid = account.get("_id")
    account_no = str(account.get("account_no") or account.get("account_number") or "")
    last4 = account_no[-4:] if len(account_no) >= 4 else account_no
    legacy = {
        "bank_name": account.get("bank_name") or "",
        "account_last4": last4,
        "status": "confirmed",
        "account_id": {"$exists": False},
    }
    return {
        "$or": [
            {"account_id": {"$in": [oid, str(oid)]}, "status": "confirmed"},
            legacy,
        ]
    }
