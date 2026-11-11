# accounting_routes/balance_sheet.py
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, Response, current_app
from datetime import datetime, date, time
from typing import Any, Dict, List
import io
import csv
import json

from bson import ObjectId
from db import db
from services.payment_account_routing import confirmed_payment_match
from accounting_routes.loans import get_loans_outstanding

acc_balance_sheet = Blueprint(
    "acc_balance_sheet",
    __name__,
    template_folder="../templates",
)

balance_sheets_col = db["balance_sheets"]
fixed_assets_col = db["fixed_assets"]
stock_closings_col = db["stock_closings"]
ar_invoices_col = db["ar_invoices"]
ap_bills_col = db["ap_bills"]
accruals_col = db["accruals"]
bank_accounts_col = db["bank_accounts"]
payments_col = db["payments"]
manager_deposits_col = db["manager_deposits"]
tax_col = db["tax_records"]
sbdc_col = db["s_bdc_payment"]
withdrawals_col = db["withdrawals"]
inventory_col = db["inventory"]
financed_by_col = db["financed_by"]
FIXED_ASSET_CATEGORIES = [
    "Land and Building",
    "Furniture and Fittings",
    "Motor Vehicles",
    "Plant and Machinery",
]

CURRENT_ASSET_LINES = ["Stock", "Debtors", "Bank", "Cash"]
CURRENT_LIAB_LINES = ["Creditors", "Expenses Creditors"]
EQUITY_LINES = ["Capital", "Add Net profit", "Less Drawings"]
LT_LIAB_LINES = ["Loan"]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _last4(acc_number: str | None) -> str:
    s = str(acc_number or "")
    return s[-4:] if len(s) >= 4 else s


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def _fixed_assets_query(as_of: date | None = None) -> Dict[str, Any]:
    query: Dict[str, Any] = {
        "status": {"$ne": "Disposed"},
        "$or": [
            {"entry_type": "asset"},
            {"entry_type": {"$exists": False}},
            {"entry_type": None},
        ],
    }
    if as_of:
        cutoff = datetime.combine(as_of, time.max)
        query["acquisition_date"] = {"$lte": cutoff}
    return query


def _get_fixed_asset_totals(as_of: date | None = None) -> Dict[str, float]:
    query = _fixed_assets_query(as_of)

    totals: Dict[str, float] = {category: 0.0 for category in FIXED_ASSET_CATEGORIES}
    other_total = 0.0

    cursor = fixed_assets_col.find(
        query,
        {"category": 1, "cost": 1, "accum_depr": 1},
    )
    for doc in cursor:
        category = doc.get("category") or ""
        cost = _safe_float(doc.get("cost"), 0.0)
        accum = _safe_float(doc.get("accum_depr"), 0.0)
        nbv = cost - accum
        if nbv < 0:
            nbv = 0.0
        if category in totals:
            totals[category] += nbv
        else:
            other_total += nbv

    rounded = {category: round(totals[category], 2) for category in FIXED_ASSET_CATEGORIES}
    if other_total > 0:
        rounded["Other"] = round(other_total, 2)
    return rounded


def _get_fixed_asset_report(as_of: date | None = None) -> Dict[str, Any]:
    query = _fixed_assets_query(as_of)

    categories: Dict[str, Dict[str, Any]] = {
        category: {"nbv_total": 0.0, "count": 0} for category in FIXED_ASSET_CATEGORIES
    }
    other = {"nbv_total": 0.0, "count": 0}

    cursor = fixed_assets_col.find(
        query,
        {"category": 1, "cost": 1, "accum_depr": 1},
    )
    for doc in cursor:
        category = doc.get("category") or ""
        cost = _safe_float(doc.get("cost"), 0.0)
        accum = _safe_float(doc.get("accum_depr"), 0.0)
        nbv = cost - accum
        if nbv < 0:
            nbv = 0.0

        if category in categories:
            categories[category]["nbv_total"] += nbv
            categories[category]["count"] += 1
        else:
            other["nbv_total"] += nbv
            other["count"] += 1

    if other["count"] > 0:
        categories["Other"] = other

    grand_total = 0.0
    total_count = 0
    for cat in categories.values():
        cat["nbv_total"] = round(_safe_float(cat.get("nbv_total"), 0.0), 2)
        cat["count"] = int(cat.get("count") or 0)
        grand_total += cat["nbv_total"]
        total_count += cat["count"]

    return {
        "categories": categories,
        "grand_total": round(grand_total, 2),
        "has_assets": total_count > 0,
    }


def _zero_totals(lines: List[str]) -> Dict[str, float]:
    return {line: 0.0 for line in lines}


def _ensure_liability_line(lines: List[Dict[str, Any]], section: str, label: str, amount: float | None):
    if amount is None:
        amount = 0.0
    amt = _safe_float(amount)
    sec_key = section.strip().lower()
    lbl_key = label.strip().lower()
    for line in lines:
        if (
            (line.get("section") or "").strip().lower() == sec_key
            and (line.get("label") or "").strip().lower() == lbl_key
        ):
            line["amount"] = amt
            return
    lines.append({"type": "liability", "section": section, "label": label, "amount": amt})


def _get_current_asset_totals(as_of: date | None = None) -> Dict[str, float]:
    totals = _zero_totals(CURRENT_ASSET_LINES)
    totals["Stock"] = _get_closing_stock_value(as_of)
    totals["Debtors"] = _get_debtors_value(as_of)
    bank_cash_totals = _get_bank_and_cash_totals(as_of)
    totals["Bank"] = bank_cash_totals.get("bank", 0.0)
    totals["Cash"] = bank_cash_totals.get("cash", 0.0)
    return totals


def _get_closing_stock_value(as_of: date | None = None) -> float:
    if as_of is None:
        as_of = date.today()
    cutoff = datetime.combine(as_of, time.max)
    doc = stock_closings_col.find_one(
        {"status": "completed", "closed_at": {"$lte": cutoff}},
        sort=[("closed_at", -1), ("created_at", -1)],
    )
    if doc:
        try:
            val = float(doc.get("total_closing_cost_value") or 0.0)
        except Exception:
            val = 0.0
        return round(val, 2)
    return _get_inventory_stock_value(as_of)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None
    return None


def _coerce_datetime_loose(value: Any) -> datetime | None:
    dt = _coerce_datetime(value)
    if dt is not None:
        return dt
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            if "/" in s and len(s.split("/")) == 3:
                y, m, d = s.split("/")
                return datetime(int(y), int(m), int(d))
        except Exception:
            return None
    return None


def _get_inventory_stock_value(as_of: date | None = None) -> float:
    cutoff = None
    if as_of is not None:
        cutoff = datetime.combine(as_of, time.max)

    total = 0.0
    cursor = inventory_col.find(
        {},
        {"qty": 1, "cost_price": 1, "initial_price": 1, "price": 1, "expiry_date": 1},
    )
    for doc in cursor:
        qty = _safe_float(doc.get("qty"), None)
        if qty is None or qty <= 0:
            continue

        if cutoff is not None:
            expiry_dt = _coerce_datetime(doc.get("expiry_date"))
            if expiry_dt is not None and expiry_dt < cutoff:
                continue

        unit_cost = _safe_float(doc.get("cost_price"), None)
        if unit_cost is None:
            unit_cost = _safe_float(doc.get("initial_price"), None)
        if unit_cost is None:
            unit_cost = _safe_float(doc.get("price"), None)
        if unit_cost is None:
            unit_cost = 0.0

        total += qty * unit_cost

    return round(total, 2)


def _sum_confirmed_in_asof(account: dict, cutoff: datetime) -> float:
    try:
        # payments use their 'date' field if provided; otherwise fallback to 'created_at'
        match = {"$and": [
            confirmed_payment_match(account),
            {"$or": [
                {"date": {"$lte": cutoff}},
                {"date": {"$exists": False}, "created_at": {"$lte": cutoff}},
            ]},
        ]}
        pipe = [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
        row = next(payments_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_manager_deposits_in_asof(bank_oid: ObjectId, cutoff: datetime) -> float:
    try:
        # manager deposits always use 'created_at' for cutoff filtering
        bank_id_str = str(bank_oid)
        pipe = [
            {
                "$match": {
                    "bank_account_id": bank_id_str,
                    "status": {"$in": ["submitted", "approved"]},
                    "created_at": {"$lte": cutoff},
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(manager_deposits_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_ptax_out_asof(bank_oid: ObjectId, cutoff: datetime) -> float:
    try:
        # tax records prefer their 'date' field; fallback to 'created_at'
        pipe = [
            {
                "$match": {
                    "source_bank_id": bank_oid,
                    "type": {"$regex": r"^p[\s_-]*tax$", "$options": "i"},
                    "$or": [
                        {"date": {"$lte": cutoff}},
                        {"date": {"$exists": False}, "created_at": {"$lte": cutoff}},
                    ],
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(tax_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_bdc_out_asof(bank_oid: ObjectId, cutoff: datetime) -> float:
    try:
        # BDC histories use bank_paid_history.date when available; else use the parent created_at
        pipe = [
            {"$match": {"bank_paid_history": {"$exists": True, "$ne": []}}},
            {"$unwind": "$bank_paid_history"},
            {
                "$match": {
                    "bank_paid_history.bank_id": bank_oid,
                    "$or": [
                        {"bank_paid_history.date": {"$lte": cutoff}},
                        {
                            "bank_paid_history.date": {"$exists": False},
                            "created_at": {"$lte": cutoff},
                        },
                    ],
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$bank_paid_history.amount"}}},
        ]
        row = next(sbdc_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_withdrawals_out_asof(bank_oid: ObjectId, cutoff: datetime) -> float:
    try:
        # withdrawals use 'date_dt' when present; fallback to 'created_at'
        pipe = [
            {
                "$match": {
                    "account_id": bank_oid,
                    "$or": [
                        {"date_dt": {"$lte": cutoff}},
                        {"date_dt": {"$exists": False}, "created_at": {"$lte": cutoff}},
                    ],
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(withdrawals_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _get_bank_and_cash_totals(as_of: date | None = None) -> Dict[str, float]:
    cutoff = datetime.combine(as_of or date.today(), time.max)
    bank_total = 0.0
    cash_total = 0.0
    for doc in bank_accounts_col.find({}):
        bank_oid = doc.get("_id")
        if not bank_oid:
            continue

        acc_type = (doc.get("account_type") or "bank").lower().strip()
        if acc_type not in ("bank", "cash"):
            acc_type = "bank"

        bank_name = doc.get("bank_name") or ""
        raw_acc_no = doc.get("account_number") or doc.get("account_no") or ""
        last4 = _last4(raw_acc_no)
        opening = _safe_float(doc.get("opening_balance"))

        confirmed_in = _sum_confirmed_in_asof(doc, cutoff)
        manager_in = _sum_manager_deposits_in_asof(bank_oid, cutoff)
        ptax_out = _sum_ptax_out_asof(bank_oid, cutoff)
        bdc_out = _sum_bdc_out_asof(bank_oid, cutoff)
        withdraw_out = _sum_withdrawals_out_asof(bank_oid, cutoff)

        live_balance = opening + confirmed_in + manager_in - (
            ptax_out + bdc_out + withdraw_out
        )

        if acc_type == "cash":
            cash_total += live_balance
        else:
            bank_total += live_balance

    return {"bank": round(bank_total, 2), "cash": round(cash_total, 2)}


def _invoice_effective_date(inv: Dict[str, Any]) -> datetime | None:
    for key in ("invoice_date", "issue_dt", "issue", "created_at"):
        val = inv.get(key)
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime.combine(val, time.min)
        if isinstance(val, str) and val.strip():
            try:
                return datetime.fromisoformat(val.strip())
            except Exception:
                continue
    return None


def _invoice_outstanding(inv: Dict[str, Any]) -> float:
    bal = inv.get("balance")
    if bal is not None:
        outstanding = _safe_float(bal, 0.0)
    else:
        outstanding = _safe_float(inv.get("amount"), 0.0)
    return max(outstanding, 0.0)


def _get_debtors_value(as_of: date | None = None) -> float:
    cutoff = datetime.combine(as_of or date.today(), time.max)
    match = {
        "$or": [
            {"invoice_date": {"$lte": cutoff}},
            {"invoice_date": {"$exists": False}, "issue_dt": {"$lte": cutoff}},
            {
                "invoice_date": {"$exists": False},
                "issue_dt": {"$exists": False},
                "issue": {"$lte": cutoff},
            },
            {
                "invoice_date": {"$exists": False},
                "issue_dt": {"$exists": False},
                "issue": {"$exists": False},
                "created_at": {"$lte": cutoff},
            },
        ]
    }
    total = 0.0
    counted = 0
    for inv in ar_invoices_col.find(match):
        eff_date = _invoice_effective_date(inv)
        if eff_date is None or eff_date > cutoff:
            continue
        status = (inv.get("status") or "").strip().lower()
        if status == "paid":
            continue
        outstanding = _invoice_outstanding(inv)
        if outstanding <= 0:
            continue
        total += outstanding
        counted += 1

    if current_app.debug:
        current_app.logger.info(
            "balance-sheet debtors count=%s total=%0.2f cutoff=%s",
            counted,
            total,
            cutoff.isoformat(),
        )

    return round(total, 2)


def _get_ap_creditors_outstanding(as_of: date | None = None) -> float:
    match: Dict[str, Any] = {"$and": []}
    cutoff: datetime | None = None
    if as_of is not None:
        cutoff = datetime.combine(as_of, time.max)
        match["$and"].append(
            {
                "$or": [
                    {"bill_date_dt": {"$lte": cutoff}},
                    {"bill_date_dt": {"$exists": False}, "bill_date": {"$exists": True}},
                    {
                        "bill_date_dt": {"$exists": False},
                        "bill_date": {"$exists": False},
                        "created_at": {"$lte": cutoff},
                    },
                ]
            }
        )

    total = 0.0
    counted = 0
    neg_count = 0
    neg_sum = 0.0
    query = match if match["$and"] else {}
    for doc in ap_bills_col.find(
        query,
        {"amount": 1, "paid": 1, "balance": 1, "bill_date_dt": 1, "bill_date": 1, "created_at": 1, "status": 1},
    ):
        effective_dt = _coerce_datetime(doc.get("bill_date_dt"))
        if effective_dt is None:
            effective_dt = _coerce_datetime(doc.get("bill_date"))
        if effective_dt is None:
            effective_dt = _coerce_datetime(doc.get("created_at"))

        if cutoff is not None and (effective_dt is None or effective_dt > cutoff):
            continue

        amount = _safe_float(doc.get("amount"))
        paid = _safe_float(doc.get("paid"))
        default_balance = amount - paid
        bal = _safe_float(doc.get("balance", default_balance))
        total += bal
        if bal < 0:
            neg_count += 1
            neg_sum += bal
        counted += 1

    if current_app.debug:
        current_app.logger.info(
            "balance-sheet creditors count=%s total=%0.2f cutoff=%s neg_count=%s neg_sum=%0.2f",
            counted,
            total,
            cutoff.isoformat() if cutoff else "none",
            neg_count,
            neg_sum,
        )

    return round(total, 2)


def _get_expense_creditors_outstanding(as_of: date | None = None) -> float:
    total = 0.0
    counted = 0
    missing_date_count = 0
    skipped_future_count = 0
    # Accruals (Outstanding Owings) should ignore "as at" date.
    cutoff: datetime | None = None

    def _safe_float_loose(v: Any) -> float:
        try:
            s = str(v or "0").replace(",", "").strip()
            return float(s) if s else 0.0
        except Exception:
            return 0.0

    def _is_owing_status(v: Any) -> bool:
        s = str(v or "owing").strip().lower()
        if not s:
            return True
        if s in ("owing", "outstanding", "outstanding owing", "outstanding owings", "owed", "unpaid"):
            return True
        return ("owing" in s) or ("outstanding" in s)

    for doc in accruals_col.find(
        {"$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]},
        {"amount": 1, "status": 1, "date_dt": 1, "date": 1, "created_at": 1},
    ):
        if not _is_owing_status(doc.get("status")):
            continue

        effective_dt = _coerce_datetime_loose(doc.get("date_dt"))
        if effective_dt is None:
            effective_dt = _coerce_datetime_loose(doc.get("date"))
        if effective_dt is None:
            effective_dt = _coerce_datetime_loose(doc.get("created_at"))

        if effective_dt is None:
            missing_date_count += 1
        total += _safe_float_loose(doc.get("amount"))
        counted += 1

    if current_app.debug:
        current_app.logger.info(
            "balance-sheet accruals count=%s total=%0.2f cutoff=%s missing_date=%s skipped_future=%s",
            counted,
            total,
            cutoff.isoformat() if cutoff else "none",
            missing_date_count,
            skipped_future_count,
        )

    return round(total, 2)


def _get_current_liability_totals(creditors: float, expenses_creditors: float) -> Dict[str, float]:
    totals = _zero_totals(CURRENT_LIAB_LINES)
    totals["Creditors"] = creditors
    totals["Expenses Creditors"] = expenses_creditors
    return totals


def _get_equity_totals(as_of: date | None = None) -> Dict[str, float]:
    # TODO: replace with actual equity totals (e.g. capital accounts + retained earnings)
    return _zero_totals(EQUITY_LINES)


def _apply_equity_overrides_from_lines(
    equity_totals: Dict[str, float],
    lines: List[Dict[str, Any]] | None,
) -> Dict[str, float]:
    """
    If a saved sheet has equity lines for financed-by rows, use them as overrides.
    """
    if not isinstance(lines, list):
        return equity_totals

    out = dict(equity_totals or {})
    valid_labels = {lbl.strip().lower(): lbl for lbl in EQUITY_LINES}

    for line in lines:
        if not isinstance(line, dict):
            continue
        if (line.get("type") or "").strip().lower() != "equity":
            continue
        raw_label = (line.get("label") or "").strip()
        key = valid_labels.get(raw_label.lower())
        if not key:
            continue
        out[key] = _safe_float(line.get("amount"), 0.0)

    return out


def _load_financed_by_totals(as_of: date | None = None) -> Dict[str, float]:
    """
    Read financed-by values from dedicated collection.
    Prefers matching as_of_date, then falls back to latest saved row.
    """
    defaults = _zero_totals(EQUITY_LINES)
    doc = None
    as_of_str = as_of.strftime("%Y-%m-%d") if isinstance(as_of, date) else ""

    if as_of_str:
        doc = financed_by_col.find_one({"as_of_date": as_of_str}, sort=[("updated_at", -1)])
    if not doc:
        doc = financed_by_col.find_one({}, sort=[("updated_at", -1)])
    if not isinstance(doc, dict):
        return defaults

    values = doc.get("values") if isinstance(doc.get("values"), dict) else {}
    out = dict(defaults)
    for label in EQUITY_LINES:
        out[label] = _safe_float(values.get(label), out[label])
    return out


def _get_long_term_liability_totals(loans_total: float) -> Dict[str, float]:
    totals = {line: 0.0 for line in LT_LIAB_LINES}
    totals["Loan"] = loans_total
    return totals


@acc_balance_sheet.route("/balance-sheet", methods=["GET"])
def balance_sheet_page():
    sheet_id_str = request.args.get("sheet_id") or ""
    sheet_doc: Dict[str, Any] | None = None

    if sheet_id_str:
        try:
            oid = ObjectId(sheet_id_str)
            sheet_doc = balance_sheets_col.find_one({"_id": oid})
        except Exception:
            sheet_doc = None

    if sheet_doc is None:
        sheet_doc = balance_sheets_col.find_one(
            {},
            sort=[("as_of_date", -1), ("created_at", -1)],
        )

    today = date.today().strftime("%Y-%m-%d")

    # If no saved sheet exists, start with empty sheet (no demo data)
    if not sheet_doc:
        sheet = {
            "id": "",
            "name": "",
            "as_of_date": today,
            "currency": "GHS",
            "lines": [],
            "totals": {"assets": 0, "liabilities": 0, "equity": 0, "liab_plus_equity": 0},
            "is_demo": False,
        }
    else:
        sheet = dict(sheet_doc)
        sheet["id"] = str(sheet.pop("_id", ""))

        as_of = sheet.get("as_of_date")
        if isinstance(as_of, datetime):
            sheet["as_of_date"] = as_of.strftime("%Y-%m-%d")
        elif isinstance(as_of, date):
            sheet["as_of_date"] = as_of.strftime("%Y-%m-%d")
        else:
            sheet["as_of_date"] = ""

        sheet["is_demo"] = False

        if "totals" not in sheet or not isinstance(sheet["totals"], dict):
            sheet["totals"] = {"assets": 0, "liabilities": 0, "equity": 0, "liab_plus_equity": 0}

        if "lines" not in sheet or not isinstance(sheet["lines"], list):
            sheet["lines"] = []

    sheet_as_of_date = _parse_iso_date(sheet.get("as_of_date"))
    fixed_asset_totals = _get_fixed_asset_totals(sheet_as_of_date)
    fixed_asset_total = round(sum(fixed_asset_totals.values()), 2)
    fixed_asset_report = _get_fixed_asset_report(sheet_as_of_date)

    current_asset_totals = _get_current_asset_totals(sheet_as_of_date)
    current_asset_total = round(sum(current_asset_totals.values()), 2)

    creditors_total = _get_ap_creditors_outstanding(sheet_as_of_date)
    expenses_creditors_total = _get_expense_creditors_outstanding(sheet_as_of_date)
    current_liab_totals = _get_current_liability_totals(creditors_total, expenses_creditors_total)
    current_liab_total = round(sum(current_liab_totals.values()), 2)

    sheet_lines = sheet.get("lines") if isinstance(sheet.get("lines"), list) else []
    _ensure_liability_line(sheet_lines, "Current Liabilities", "Creditors", creditors_total)
    _ensure_liability_line(sheet_lines, "Current Liabilities", "Expenses Creditors", expenses_creditors_total)
    sheet["lines"] = sheet_lines

    equity_totals = _get_equity_totals(sheet_as_of_date)
    equity_totals = _apply_equity_overrides_from_lines(equity_totals, sheet.get("lines"))
    financed_by_totals = _load_financed_by_totals(sheet_as_of_date)
    for label in EQUITY_LINES:
        equity_totals[label] = _safe_float(financed_by_totals.get(label), equity_totals.get(label, 0.0))
    equity_total = round(sum(equity_totals.values()), 2)

    loans_total = get_loans_outstanding(sheet_as_of_date)
    lt_liab_totals = _get_long_term_liability_totals(loans_total)
    lt_liab_total = loans_total

    working_capital = round(current_asset_total - current_liab_total, 2)
    net_total_assets = round(fixed_asset_total + working_capital, 2)
    capital_employed = round(equity_total + lt_liab_total, 2)

    # Build options
    options: List[Dict[str, Any]] = []
    cursor = balance_sheets_col.find({}, {"name": 1, "as_of_date": 1}).sort("as_of_date", -1)

    for d in cursor:
        oid = d.get("_id")
        name = d.get("name") or ""
        as_of = d.get("as_of_date")

        if isinstance(as_of, datetime):
            as_of_str = as_of.strftime("%Y-%m-%d")
        elif isinstance(as_of, date):
            as_of_str = as_of.strftime("%Y-%m-%d")
        else:
            as_of_str = ""

        label_parts = []
        if name:
            label_parts.append(name)
        if as_of_str:
            label_parts.append(f"As at {as_of_str}")
        label = " • ".join(label_parts) if label_parts else "Unnamed Sheet"

        options.append({"id": str(oid), "name": name, "as_of_date": as_of_str, "label": label})

    return render_template(
        "accounting/balance_sheet_vertical.html",
        sheet=sheet,
        sheet_options=options,
        today=today,
        fixed_asset_categories=FIXED_ASSET_CATEGORIES,
        fixed_asset_totals=fixed_asset_totals,
        fixed_asset_total=fixed_asset_total,
        fixed_asset_report=fixed_asset_report,
        current_asset_lines=CURRENT_ASSET_LINES,
        current_asset_totals=current_asset_totals,
        current_asset_total=current_asset_total,
        current_liab_lines=CURRENT_LIAB_LINES,
        current_liab_totals=current_liab_totals,
        current_liab_total=current_liab_total,
        equity_lines=EQUITY_LINES,
        equity_totals=equity_totals,
        financed_by_totals=financed_by_totals,
        equity_total=equity_total,
        lt_liab_lines=LT_LIAB_LINES,
        lt_liab_totals=lt_liab_totals,
        lt_liab_total=lt_liab_total,
        working_capital=working_capital,
        net_total_assets=net_total_assets,
        capital_employed=capital_employed,
        debug_creditors_source="ap_bills(balance sum)",
        debug_expense_creditors_source="accruals(status=owing)",
    )


@acc_balance_sheet.get("/balance-sheet/debug-liabs")
def balance_sheet_debug_liabs():
    as_of_str = (request.args.get("as_of") or "").strip()
    as_of_date = _parse_iso_date(as_of_str) or date.today()
    cutoff = datetime.combine(as_of_date, time.max)

    def _fmt_dt(val: Any) -> Any:
        if isinstance(val, datetime):
            return val.isoformat()
        if isinstance(val, date):
            return datetime.combine(val, time.min).isoformat()
        return val

    creditors_balance_sheet_total = _get_ap_creditors_outstanding(as_of_date)

    creditors_ap_style_total = 0.0
    negative_balance_bills: List[Dict[str, Any]] = []
    missing_bill_dates: List[Dict[str, Any]] = []

    for doc in ap_bills_col.find(
        {},
        {
            "amount": 1,
            "paid": 1,
            "balance": 1,
            "bill_date_dt": 1,
            "bill_date": 1,
            "created_at": 1,
            "bill_no": 1,
            "no": 1,
            "vendor_name": 1,
            "vendor": 1,
            "status": 1,
        },
    ):
        effective_dt = _coerce_datetime(doc.get("bill_date_dt"))
        if effective_dt is None:
            effective_dt = _coerce_datetime(doc.get("bill_date"))
        if effective_dt is None:
            effective_dt = _coerce_datetime(doc.get("created_at"))

        if effective_dt is None:
            if len(missing_bill_dates) < 10:
                missing_bill_dates.append({
                    "id": str(doc.get("_id")),
                    "bill_no": doc.get("bill_no") or doc.get("no") or "",
                    "vendor_name": doc.get("vendor_name") or doc.get("vendor") or "",
                    "bill_date_dt": _fmt_dt(doc.get("bill_date_dt")),
                    "bill_date": _fmt_dt(doc.get("bill_date")),
                    "created_at": _fmt_dt(doc.get("created_at")),
                })
            continue

        if effective_dt > cutoff:
            continue

        amount = _safe_float(doc.get("amount"))
        paid = _safe_float(doc.get("paid"))
        bal = _safe_float(doc.get("balance", amount - paid))
        creditors_ap_style_total += bal

        if bal < 0 and len(negative_balance_bills) < 10:
            negative_balance_bills.append({
                "id": str(doc.get("_id")),
                "bill_no": doc.get("bill_no") or doc.get("no") or "",
                "vendor_name": doc.get("vendor_name") or doc.get("vendor") or "",
                "balance": bal,
            })

    expenses_creditors_total = 0.0
    missing_accrual_dates: List[Dict[str, Any]] = []
    bad_date_format_samples: List[Dict[str, Any]] = []

    for doc in accruals_col.find(
        {},
        {"amount": 1, "status": 1, "date_dt": 1, "date": 1, "created_at": 1},
    ):
        status_norm = (doc.get("status") or "owing").strip().lower()
        if not (
            status_norm in ("owing", "outstanding", "outstanding owing", "outstanding owings", "owed", "unpaid")
            or ("owing" in status_norm)
            or ("outstanding" in status_norm)
        ):
            continue

        raw_date_dt = doc.get("date_dt")
        raw_date = doc.get("date")
        raw_created = doc.get("created_at")

        effective_dt = _coerce_datetime_loose(raw_date_dt)
        if effective_dt is None:
            effective_dt = _coerce_datetime_loose(raw_date)
        if effective_dt is None:
            effective_dt = _coerce_datetime_loose(raw_created)

        if effective_dt is None:
            if len(missing_accrual_dates) < 10:
                missing_accrual_dates.append({
                    "id": str(doc.get("_id")),
                    "date_dt": _fmt_dt(raw_date_dt),
                    "date": _fmt_dt(raw_date),
                    "created_at": _fmt_dt(raw_created),
                })
            has_any_date = any(v not in (None, "") for v in (raw_date_dt, raw_date, raw_created))
            if has_any_date and len(bad_date_format_samples) < 10:
                bad_date_format_samples.append({
                    "id": str(doc.get("_id")),
                    "date_dt": _fmt_dt(raw_date_dt),
                    "date": _fmt_dt(raw_date),
                    "created_at": _fmt_dt(raw_created),
                })
            continue

        expenses_creditors_total += _safe_float(doc.get("amount"))

    return jsonify(
        as_of=as_of_date.strftime("%Y-%m-%d"),
        cutoff=cutoff.isoformat(),
        creditors_balance_sheet_total=round(creditors_balance_sheet_total, 2),
        creditors_ap_style_total=round(creditors_ap_style_total, 2),
        diff=round(creditors_balance_sheet_total - creditors_ap_style_total, 2),
        negative_balance_bills=negative_balance_bills,
        missing_bill_dates=missing_bill_dates,
        expenses_creditors_total=round(expenses_creditors_total, 2),
        missing_accrual_dates=missing_accrual_dates,
        skipped_due_to_bad_date_format=bad_date_format_samples,
    )


@acc_balance_sheet.route("/balance-sheet/save", methods=["POST"])
def balance_sheet_save():
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify(ok=False, message="Invalid JSON body"), 400

    if not isinstance(data, dict):
        return jsonify(ok=False, message="Invalid payload."), 400

    sheet_id_str = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    as_of_date_str = (data.get("as_of_date") or "").strip()
    currency = (data.get("currency") or "GHS").upper()
    lines = data.get("lines") or []

    if not lines:
        return jsonify(ok=False, message="No balance sheet lines to save."), 400

    as_of_date_only = _parse_iso_date(as_of_date_str)
    as_of_dt: datetime | None = None
    if as_of_date_only:
        as_of_dt = datetime.combine(as_of_date_only, time.min)

    now = datetime.utcnow()

    norm_lines: List[Dict[str, Any]] = []
    total_assets = 0.0
    total_liab = 0.0
    total_equity = 0.0

    for line in lines:
        if not isinstance(line, dict):
            continue

        l_type = (line.get("type") or "").lower()
        if l_type not in ("asset", "liability", "equity"):
            continue

        label = (line.get("label") or "").strip()
        if not label:
            continue

        section = (line.get("section") or "").strip()
        amount = _safe_float(line.get("amount"), 0.0)

        if l_type == "asset":
            total_assets += amount
        elif l_type == "liability":
            total_liab += amount
        elif l_type == "equity":
            total_equity += amount

        norm_lines.append({"type": l_type, "section": section, "label": label, "amount": amount})

    if not norm_lines:
        return jsonify(ok=False, message="All rows are empty or invalid."), 400

    totals = {
        "assets": round(total_assets, 2),
        "liabilities": round(total_liab, 2),
        "equity": round(total_equity, 2),
        "liab_plus_equity": round(total_liab + total_equity, 2),
    }

    doc: Dict[str, Any] = {
        "name": name,
        "as_of_date": as_of_dt,
        "currency": currency,
        "lines": norm_lines,
        "totals": totals,
        "updated_at": now,
    }

    if sheet_id_str:
        try:
            oid = ObjectId(sheet_id_str)
        except Exception:
            return jsonify(ok=False, message="Invalid sheet id."), 400

        balance_sheets_col.update_one(
            {"_id": oid},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        sheet_id = sheet_id_str
    else:
        doc["created_at"] = now
        res = balance_sheets_col.insert_one(doc)
        sheet_id = str(res.inserted_id)

    return jsonify(ok=True, id=sheet_id, totals=totals), 200


@acc_balance_sheet.route("/balance-sheet/financed-by/save", methods=["POST"])
def balance_sheet_financed_by_save():
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify(ok=False, message="Invalid JSON body"), 400

    if not isinstance(data, dict):
        return jsonify(ok=False, message="Invalid payload."), 400

    as_of_date_str = (data.get("as_of_date") or "").strip()
    currency = (data.get("currency") or "GHS").upper()
    values = data.get("values") if isinstance(data.get("values"), dict) else {}

    as_of_date_only = _parse_iso_date(as_of_date_str)
    if as_of_date_only:
        as_of_date_str = as_of_date_only.strftime("%Y-%m-%d")
    elif not as_of_date_str:
        as_of_date_str = date.today().strftime("%Y-%m-%d")

    norm_values = {label: _safe_float(values.get(label), 0.0) for label in EQUITY_LINES}
    now = datetime.utcnow()

    financed_by_col.update_one(
        {"as_of_date": as_of_date_str},
        {
            "$set": {
                "as_of_date": as_of_date_str,
                "currency": currency,
                "values": norm_values,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    return jsonify(ok=True, values=norm_values, as_of_date=as_of_date_str), 200


@acc_balance_sheet.route("/balance-sheet/export/csv", methods=["POST"])
def balance_sheet_export_csv():
    payload = request.form.get("payload")
    if not payload:
        return jsonify(ok=False, message="No data to export"), 400

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return jsonify(ok=False, message="Invalid JSON payload"), 400

    name = (data.get("name") or "").strip()
    as_of_date_str = (data.get("as_of_date") or "").strip()
    currency = (data.get("currency") or "GHS").upper()
    lines = data.get("lines") or []

    out = io.StringIO()
    w = csv.writer(out)

    title = "Balance Sheet"
    if name:
        title += f" - {name}"
    if as_of_date_str:
        title += f" (As at {as_of_date_str})"

    w.writerow([title])
    w.writerow([])
    w.writerow(["Type", "Section", "Account", f"Amount ({currency})"])

    for line in lines:
        t = (line.get("type") or "").lower()
        sec = line.get("section") or ""
        lab = line.get("label") or ""
        amt = _safe_float(line.get("amount"), 0.0)
        w.writerow([t, sec, lab, f"{amt:0.2f}"])

    filename_date = (as_of_date_str or date.today().strftime("%Y-%m-%d")).replace("-", "")
    filename = f"balance_sheet_{filename_date}.csv"

    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
