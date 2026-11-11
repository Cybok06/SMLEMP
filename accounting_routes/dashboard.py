# accounting_routes/dashboard.py
from __future__ import annotations

from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify, current_app
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Tuple
from collections import defaultdict
import time
from cache_ext import cache
from services.singleflight import singleflight

from db import db
from services.payment_account_routing import confirmed_payment_match
from services.profit_loss_service import compute_profit_loss

acc_dashboard = Blueprint(
    "acc_dashboard",
    __name__,
    template_folder="../templates",
)

# --- Collections (aligned to accounting_routes) ---
ar_invoices_col = db["ar_invoices"]
ar_receipts_col = db["ar_receipts"]
ap_bills_col = db["ap_bills"]
expenses_col = db["expenses"]
manager_expenses_col = db["manager_expenses"]
inventory_col = db["inventory"]
inventory_outflow_col = db["inventory_products_outflow"]
customers_col = db["customers"]
income_entries_col = db["income_entries"]

bank_accounts_col = db["bank_accounts"]
payments_col = db["payments"]
tax_col = db["tax_records"]
sbdc_col = db["s_bdc_payment"]
manager_deposits_col = db["manager_deposits"]
withdrawals_col = db["withdrawals"]
transfers_col = db["account_transfers"]

journals_col = db["journals"]
fixed_assets_col = db["fixed_assets"]
bank_lines_col = db["bank_statement_lines"]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _get_doc_date(doc: Dict[str, Any], keys: List[str]) -> datetime | None:
    for k in keys:
        val = doc.get(k)
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime.combine(val, datetime.min.time())
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val)
            except Exception:
                try:
                    return datetime.strptime(val, "%Y-%m-%d")
                except Exception:
                    continue
    return None


def _last4(acc_number: str | None) -> str:
    s = str(acc_number or "")
    return s[-4:] if len(s) >= 4 else s


def _sum_confirmed_in(account: dict) -> float:
    try:
        pipe = [
            {
                "$match": confirmed_payment_match(account)
            },
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(payments_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_manager_deposits_in(bank_oid) -> float:
    try:
        bank_id_str = str(bank_oid)
        pipe = [
            {"$match": {"bank_account_id": bank_id_str, "status": {"$in": ["submitted", "approved"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(manager_deposits_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_ptax_out(bank_oid) -> float:
    try:
        pipe = [
            {"$match": {"source_bank_id": bank_oid, "type": {"$regex": r"^p[\\s_-]*tax$", "$options": "i"}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(tax_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_bdc_out(bank_oid) -> float:
    try:
        pipe = [
            {"$match": {"bank_paid_history": {"$exists": True, "$ne": []}}},
            {"$unwind": "$bank_paid_history"},
            {"$match": {"bank_paid_history.bank_id": bank_oid}},
            {"$group": {"_id": None, "total": {"$sum": "$bank_paid_history.amount"}}},
        ]
        row = next(sbdc_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_withdrawals_out(bank_oid) -> float:
    try:
        pipe = [
            {
                "$match": {
                    "account_id": bank_oid,
                    # account-to-account transfers are mirrored as withdrawals;
                    # they are counted separately via _sum_transfers_out.
                    "source": {"$ne": "account_transfer"},
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(withdrawals_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_transfers_out(bank_oid) -> float:
    try:
        pipe = [
            {"$match": {"from_account_id": bank_oid}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(transfers_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _sum_transfers_in(bank_oid) -> float:
    try:
        pipe = [
            {"$match": {"to_account_id": bank_oid}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        row = next(transfers_col.aggregate(pipe), None)
        return _safe_float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def _period_range_from_key(key: str) -> Tuple[datetime, datetime, str]:
    """
    Convert a simple range key into (start_dt, end_dt, human_label).
    end_dt is exclusive.
    """
    today = date.today()
    now = datetime.utcnow()

    if key == "last_30":
        start = now - timedelta(days=30)
        label = "Last 30 days"
    elif key == "this_year":
        start = datetime(today.year, 1, 1)
        label = f"Year to date ({today.year})"
    elif key == "last_90":
        start = now - timedelta(days=90)
        label = "Last 90 days"
    else:
        # default: this month
        start = datetime(today.year, today.month, 1)
        label = "This month"

    end = now + timedelta(seconds=1)
    return start, end, label


def _month_key(dt: datetime) -> str:
    """Return YYYY-MM label used on charts."""
    return dt.strftime("%Y-%m")


def _month_start(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1)


def _shift_months(dt: datetime, delta: int) -> datetime:
    """
    Shift a datetime by whole months while keeping day at month start.
    """
    base = _month_start(dt)
    total = (base.year * 12 + (base.month - 1)) + delta
    year = total // 12
    month = (total % 12) + 1
    return datetime(year, month, 1)


def _status_allows_liability(status_raw: Any) -> bool:
    status = (status_raw or "payment_ongoing").strip().lower()
    return status not in {"completed", "approved", "packaging", "delivering", "delivered", "closed"}


def _product_status_allows_liability(status_raw: Any) -> bool:
    status = (status_raw or "active").strip().lower()
    return status not in {"completed", "closed", "packaged", "delivered"}


def _accounting_access_guard() -> bool:
    role = (session.get("role") or "").lower().strip()
    if session.get("accounting_id") or role == "accounting":
        return True
    if session.get("executive_id") or session.get("admin_id"):
        return True
    return False


def _read_range_from_request() -> Tuple[str, datetime, datetime, str]:
    range_key = request.args.get("range", "this_month")
    start_dt, end_dt, range_label = _period_range_from_key(range_key)
    return range_key, start_dt, end_dt, range_label


def _build_ok(range_key: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "range_key": range_key, "data": data}


def _json_ok(range_key: str, data: Dict[str, Any]):
    return jsonify(_build_ok(range_key, data))


def _json_err(message: str):
    return jsonify({"ok": False, "error": message})


def _cache_key(section: str, range_key: str) -> str:
    return f"acc_dashboard:{section}:{range_key}"


def _cache_get(section: str, range_key: str):
    key = _cache_key(section, range_key)
    cached = cache.get(key)
    if cached is not None:
        print(f"[CACHE HIT] {section} {range_key}")
    else:
        print(f"[CACHE MISS] {section} {range_key}")
    return cached


def _cache_set(section: str, range_key: str, payload: Dict[str, Any], timeout: int):
    key = _cache_key(section, range_key)
    cache.set(key, payload, timeout=timeout)


def _compute_bank_cash() -> Dict[str, Any]:
    cash_balance = 0.0
    bank_breakdown = {"bank": 0.0, "mobile_money": 0.0, "cash": 0.0}
    bank_accounts_list: List[Dict[str, Any]] = []
    try:
        for acc in bank_accounts_col.find({}):
            bank_oid = acc.get("_id")
            opening = _safe_float(acc.get("opening_balance"))

            acc_type = (acc.get("account_type") or "bank").lower().strip()
            if acc_type not in ("bank", "mobile_money", "cash"):
                acc_type = "bank"

            bank_name = acc.get("bank_name") or ""
            raw_acc_no = acc.get("account_no") or acc.get("account_number") or ""
            last4 = _last4(raw_acc_no)

            confirmed_in = _sum_confirmed_in(acc)
            manager_in = _sum_manager_deposits_in(bank_oid)
            transfer_in = _sum_transfers_in(bank_oid)
            total_in = confirmed_in + manager_in + transfer_in

            ptax_out = _sum_ptax_out(bank_oid)
            bdc_out = _sum_bdc_out(bank_oid)
            withdraw_out = _sum_withdrawals_out(bank_oid)
            transfer_out = _sum_transfers_out(bank_oid)
            total_out = ptax_out + bdc_out + withdraw_out + transfer_out

            live_balance = opening + total_in - total_out
            cash_balance += live_balance

            if acc_type == "mobile_money":
                type_key = "mobile_money"
                bank_breakdown["mobile_money"] += live_balance
                type_label = "Mobile Money"
            elif acc_type == "cash":
                type_key = "cash"
                bank_breakdown["cash"] += live_balance
                type_label = "Cash"
            else:
                type_key = "bank"
                bank_breakdown["bank"] += live_balance
                type_label = "Bank"

            name = acc.get("account_name") or acc.get("bank_name") or "Account"
            number = raw_acc_no
            provider = acc.get("bank_name") or acc.get("network") or ""

            bank_accounts_list.append(
                {
                    "name": name,
                    "number": number,
                    "provider": provider,
                    "type_key": type_key,
                    "type_label": type_label,
                    "balance": round(live_balance, 2),
                }
            )
    except Exception:
        pass

    return {
        "breakdown": {
            "bank": round(bank_breakdown["bank"], 2),
            "mobile_money": round(bank_breakdown["mobile_money"], 2),
            "cash": round(bank_breakdown["cash"], 2),
        },
        "accounts": bank_accounts_list,
        "cash_balance": round(cash_balance, 2),
    }


def _compute_period_sales_expenses(start_dt: datetime, end_dt: datetime) -> Tuple[float, float]:
    net_sales_period = 0.0
    total_expenses_period = 0.0
    try:
        for pay in payments_col.find({"payment_type": {"$ne": "WITHDRAWAL"}}):
            amt = _safe_float(pay.get("amount"), 0.0)
            if amt <= 0:
                continue
            pay_dt = (
                pay.get("date_dt")
                if isinstance(pay.get("date_dt"), datetime)
                else _get_doc_date(pay, ["date_dt", "date", "created_at"])
            )
            if not pay_dt:
                continue
            if start_dt <= pay_dt <= end_dt:
                net_sales_period += amt
    except Exception:
        pass

    try:
        for exp in expenses_col.find({}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = _get_doc_date(exp, ["date"])
            if not exp_dt:
                continue
            if start_dt <= exp_dt <= end_dt:
                total_expenses_period += amt

        for exp in manager_expenses_col.find({"status": "Approved"}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = exp.get("created_at") if isinstance(exp.get("created_at"), datetime) else _get_doc_date(exp, ["created_at"])
            if not exp_dt:
                continue
            if start_dt <= exp_dt <= end_dt:
                total_expenses_period += amt
    except Exception:
        pass

    return net_sales_period, total_expenses_period


def _compute_ar_totals_and_customers() -> Tuple[float, float, Dict[str, float]]:
    ar_total = 0.0
    ar_overdue_total = 0.0
    customer_outstanding: Dict[str, float] = defaultdict(float)
    try:
        for inv in ar_invoices_col.find({}):
            status = (inv.get("status") or "draft").lower()
            balance = _safe_float(inv.get("balance"), 0.0)
            if status == "overdue":
                ar_overdue_total += balance
            if status in ("sent", "part", "overdue"):
                ar_total += balance
            cust_name = inv.get("customer_name") or inv.get("customer") or "Unknown"
            if balance > 0:
                customer_outstanding[cust_name] += balance
    except Exception:
        pass
    return ar_total, ar_overdue_total, customer_outstanding


def _compute_ap_totals_and_suppliers() -> Tuple[float, Dict[str, float], Dict[str, float]]:
    ap_total = 0.0
    supplier_outstanding: Dict[str, float] = defaultdict(float)
    ap_due_buckets = {"due_today": 0.0, "next_7": 0.0, "next_30": 0.0, "overdue": 0.0}
    today = date.today()
    try:
        for bill in ap_bills_col.find({}):
            amount = _safe_float(bill.get("amount"), 0.0)
            paid = _safe_float(bill.get("paid"), 0.0)
            balance = _safe_float(bill.get("balance", amount - paid), 0.0)
            if balance > 0:
                ap_total += balance
                due_dt = bill.get("due_date_dt") if isinstance(bill.get("due_date_dt"), datetime) else _get_doc_date(bill, ["due_date"])
                if due_dt:
                    days_diff = (due_dt.date() - today).days
                    if days_diff < 0:
                        ap_due_buckets["overdue"] += balance
                    elif days_diff == 0:
                        ap_due_buckets["due_today"] += balance
                    elif days_diff <= 7:
                        ap_due_buckets["next_7"] += balance
                    elif days_diff <= 30:
                        ap_due_buckets["next_30"] += balance

            supp_name = bill.get("vendor_name") or bill.get("vendor") or "Unknown"
            if balance > 0:
                supplier_outstanding[supp_name] += balance
    except Exception:
        pass
    return ap_total, ap_due_buckets, supplier_outstanding


def _compute_net_book_value() -> float:
    net_book_value = 0.0
    try:
        for fa in fixed_assets_col.find({}):
            entry_type = (fa.get("entry_type") or "asset").lower()
            if entry_type == "rent":
                continue
            cost = _safe_float(fa.get("cost"), 0.0)
            acc_dep = _safe_float(fa.get("accum_depr"), 0.0)
            nbv = max(cost - acc_dep, 0.0)
            net_book_value += nbv
    except Exception:
        pass
    return net_book_value


def _compute_unreconciled_count() -> int:
    try:
        return int(
            bank_lines_col.count_documents(
                {"$or": [{"matched": False}, {"matched": {"$exists": False}}]}
            )
        )
    except Exception:
        return 0


def _compute_draft_journals() -> int:
    try:
        return int(
            journals_col.count_documents(
                {"status": {"$in": ["draft", "pending_review"]}}
            )
        )
    except Exception:
        return 0


def _build_month_labels() -> Tuple[List[str], Dict[str, str]]:
    today_dt = datetime.utcnow()
    current_month = _month_start(today_dt)
    months_labels: List[str] = [
        _shift_months(current_month, -i).strftime("%b %Y")
        for i in range(5, -1, -1)
    ]
    key_by_label = {
        label: datetime.strptime(label, "%b %Y").strftime("%Y-%m")
        for label in months_labels
    }
    return months_labels, key_by_label


def _compute_sales_expense_chart() -> Dict[str, Any]:
    sales_by_month: Dict[str, float] = defaultdict(float)
    exp_by_month: Dict[str, float] = defaultdict(float)
    profit_by_month: Dict[str, float] = defaultdict(float)

    try:
        for pay in payments_col.find({"payment_type": {"$ne": "WITHDRAWAL"}}):
            amt = _safe_float(pay.get("amount"), 0.0)
            if amt <= 0:
                continue
            pay_dt = (
                pay.get("date_dt")
                if isinstance(pay.get("date_dt"), datetime)
                else _get_doc_date(pay, ["date_dt", "date", "created_at"])
            )
            if not pay_dt:
                continue
            month_key = _month_key(pay_dt)
            sales_by_month[month_key] += amt
    except Exception:
        pass

    try:
        for exp in expenses_col.find({}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = _get_doc_date(exp, ["date"])
            if not exp_dt:
                continue
            month_key = _month_key(exp_dt)
            exp_by_month[month_key] += amt

        for exp in manager_expenses_col.find({"status": "Approved"}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = exp.get("created_at") if isinstance(exp.get("created_at"), datetime) else _get_doc_date(exp, ["created_at"])
            if not exp_dt:
                continue
            month_key = _month_key(exp_dt)
            exp_by_month[month_key] += amt
    except Exception:
        pass

    try:
        for row in inventory_outflow_col.find({}, {"created_at": 1, "total_profit": 1}):
            created = row.get("created_at")
            if not isinstance(created, datetime):
                continue
            profit_amt = _safe_float(row.get("total_profit"), 0.0)
            if profit_amt <= 0:
                continue
            month_key = _month_key(created)
            profit_by_month[month_key] += profit_amt
    except Exception:
        pass

    current_stock_total = 0.0
    try:
        for item in inventory_col.find({}, {"qty": 1, "cost_price": 1, "initial_price": 1, "price": 1}):
            qty = _safe_float(item.get("qty"), 0.0)
            if qty <= 0:
                continue
            unit_cost = _safe_float(item.get("cost_price"), None)
            if unit_cost is None:
                unit_cost = _safe_float(item.get("initial_price"), None)
            if unit_cost is None:
                unit_cost = _safe_float(item.get("price"), 0.0)
            current_stock_total += qty * (unit_cost or 0.0)
    except Exception:
        current_stock_total = 0.0

    active_liability_by_label: Dict[str, float] = {}
    try:
        liability_customers = list(
            customers_col.find(
                {},
                {"purchases": 1, "status": 1},
            )
        )

        liability_payments = []
        pay_cursor = payments_col.find(
            {
                "$or": [
                    {"payment_type": "PRODUCT"},
                    {"payment_type": "WITHDRAWAL", "product_index": {"$ne": None}},
                ]
            },
            {"customer_id": 1, "product_index": 1, "payment_type": 1, "amount": 1, "date_dt": 1, "date": 1, "created_at": 1},
        )
        for p in pay_cursor:
            p_dt = p.get("date_dt") if isinstance(p.get("date_dt"), datetime) else _get_doc_date(p, ["date_dt", "date", "created_at"])
            if not p_dt:
                continue
            cid = p.get("customer_id")
            if not cid:
                continue
            liability_payments.append(
                {
                    "dt": p_dt,
                    "customer_id": str(cid),
                    "product_index": str(p.get("product_index")),
                    "payment_type": p.get("payment_type"),
                    "amount": _safe_float(p.get("amount"), 0.0),
                }
            )
        liability_payments.sort(key=lambda x: x["dt"])

        months_labels, _ = _build_month_labels()
        anchors: List[Tuple[str, datetime]] = []
        for label in months_labels:
            m_start = datetime.strptime(label, "%b %Y")
            if m_start.month == 12:
                next_month = datetime(m_start.year + 1, 1, 1)
            else:
                next_month = datetime(m_start.year, m_start.month + 1, 1)
            anchors.append((label, next_month - timedelta(seconds=1)))
        anchors.sort(key=lambda x: x[1])

        cum_paid_by_purchase: Dict[Tuple[str, str], float] = {}
        pay_idx = 0

        for label, anchor_dt in anchors:
            while pay_idx < len(liability_payments) and liability_payments[pay_idx]["dt"] <= anchor_dt:
                e = liability_payments[pay_idx]
                key = (e["customer_id"], e["product_index"])
                amt = e["amount"]
                if e["payment_type"] == "PRODUCT":
                    cum_paid_by_purchase[key] = cum_paid_by_purchase.get(key, 0.0) + amt
                elif e["payment_type"] == "WITHDRAWAL":
                    cum_paid_by_purchase[key] = cum_paid_by_purchase.get(key, 0.0) - amt
                pay_idx += 1

            active_start = anchor_dt - timedelta(days=21)
            active_customer_ids = {
                e["customer_id"]
                for e in liability_payments
                if e["payment_type"] == "PRODUCT" and active_start <= e["dt"] <= anchor_dt
            }

            active_liability_total = 0.0
            for cust in liability_customers:
                cust_id = str(cust.get("_id") or "")
                if not cust_id or cust_id not in active_customer_ids:
                    continue
                if not _status_allows_liability(cust.get("status")):
                    continue

                purchases = cust.get("purchases") or []
                for p_index, purchase in enumerate(purchases):
                    product = purchase.get("product") or {}
                    status = product.get("status") or purchase.get("status")
                    if not _product_status_allows_liability(status):
                        continue

                    product_total = _safe_float(product.get("total"), 0.0)
                    paid = cum_paid_by_purchase.get((cust_id, str(p_index)), 0.0)
                    outstanding = max(0.0, product_total - paid)
                    if outstanding > 0:
                        active_liability_total += outstanding

            active_liability_by_label[label] = round(active_liability_total, 2)
    except Exception:
        active_liability_by_label = {}

    months_labels, key_by_label = _build_month_labels()
    sales_series: List[float] = []
    expense_series: List[float] = []
    stock_series: List[float] = []
    profit_series: List[float] = []
    active_liability_series: List[float] = []

    for label in months_labels:
        key = key_by_label[label]
        sales_series.append(round(sales_by_month.get(key, 0.0), 2))
        expense_series.append(round(exp_by_month.get(key, 0.0), 2))
        stock_series.append(round(current_stock_total, 2))
        profit_series.append(round(profit_by_month.get(key, 0.0), 2))
        active_liability_series.append(round(active_liability_by_label.get(label, 0.0), 2))

    return {
        "labels": months_labels,
        "sales": sales_series,
        "expenses": expense_series,
        "stock": stock_series,
        "profit": profit_series,
        "active_liability": active_liability_series,
    }


def _compute_cash_flow_chart() -> Dict[str, Any]:
    cash_in_by_month: Dict[str, float] = defaultdict(float)
    cash_out_by_month: Dict[str, float] = defaultdict(float)
    cash_in_sales_by_month: Dict[str, float] = defaultdict(float)
    cash_in_income_by_month: Dict[str, float] = defaultdict(float)
    cash_out_expense_by_month: Dict[str, float] = defaultdict(float)
    cash_out_withdrawal_by_month: Dict[str, float] = defaultdict(float)

    try:
        for pay in payments_col.find({"payment_type": {"$ne": "WITHDRAWAL"}}):
            amt = _safe_float(pay.get("amount"), 0.0)
            if amt <= 0:
                continue
            pay_dt = (
                pay.get("date_dt")
                if isinstance(pay.get("date_dt"), datetime)
                else _get_doc_date(pay, ["date_dt", "date", "created_at"])
            )
            if not pay_dt:
                continue
            month_key = _month_key(pay_dt)
            cash_in_by_month[month_key] += amt
            cash_in_sales_by_month[month_key] += amt
    except Exception:
        pass

    try:
        for inc in income_entries_col.find({}):
            amt = _safe_float(inc.get("amount"), 0.0)
            if amt <= 0:
                continue
            status = (inc.get("status") or "posted").strip().lower()
            if status in {"void", "voided", "cancelled"}:
                continue
            cat = (inc.get("category") or "").strip().lower()
            if cat not in {"discount received", "investment income"}:
                continue
            inc_dt = (
                inc.get("date_dt")
                if isinstance(inc.get("date_dt"), datetime)
                else _get_doc_date(inc, ["date_dt", "date", "created_at"])
            )
            if not inc_dt:
                continue
            month_key = _month_key(inc_dt)
            cash_in_by_month[month_key] += amt
            cash_in_income_by_month[month_key] += amt
    except Exception:
        pass

    try:
        for exp in expenses_col.find({}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = _get_doc_date(exp, ["date"])
            if not exp_dt:
                continue
            month_key = _month_key(exp_dt)
            cash_out_by_month[month_key] += amt
            cash_out_expense_by_month[month_key] += amt

        for exp in manager_expenses_col.find({"status": "Approved"}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = exp.get("created_at") if isinstance(exp.get("created_at"), datetime) else _get_doc_date(exp, ["created_at"])
            if not exp_dt:
                continue
            month_key = _month_key(exp_dt)
            cash_out_by_month[month_key] += amt
            cash_out_expense_by_month[month_key] += amt
    except Exception:
        pass

    try:
        for wd in withdrawals_col.find({}):
            amt = _safe_float(wd.get("amount"), 0.0)
            if amt <= 0:
                continue
            wd_dt = (
                wd.get("date_dt")
                if isinstance(wd.get("date_dt"), datetime)
                else _get_doc_date(wd, ["date_dt", "date", "created_at"])
            )
            if not wd_dt:
                continue
            month_key = _month_key(wd_dt)
            cash_out_by_month[month_key] += amt
            cash_out_withdrawal_by_month[month_key] += amt
    except Exception:
        pass

    months_labels, key_by_label = _build_month_labels()
    cash_in_series: List[float] = []
    cash_out_series: List[float] = []
    cash_net_series: List[float] = []
    cash_in_sales_series: List[float] = []
    cash_in_income_series: List[float] = []
    cash_out_expense_series: List[float] = []
    cash_out_withdrawal_series: List[float] = []

    for label in months_labels:
        key = key_by_label[label]
        cash_in_series.append(round(cash_in_by_month.get(key, 0.0), 2))
        cash_out_series.append(round(cash_out_by_month.get(key, 0.0), 2))
        cash_net_series.append(round(cash_in_by_month.get(key, 0.0) - cash_out_by_month.get(key, 0.0), 2))
        cash_in_sales_series.append(round(cash_in_sales_by_month.get(key, 0.0), 2))
        cash_in_income_series.append(round(cash_in_income_by_month.get(key, 0.0), 2))
        cash_out_expense_series.append(round(cash_out_expense_by_month.get(key, 0.0), 2))
        cash_out_withdrawal_series.append(round(cash_out_withdrawal_by_month.get(key, 0.0), 2))

    return {
        "labels": months_labels,
        "cash_in": cash_in_series,
        "cash_out": cash_out_series,
        "net": cash_net_series,
        "cash_in_sources": {
            "sales": cash_in_sales_series,
            "income": cash_in_income_series,
        },
        "cash_out_sources": {
            "expenses": cash_out_expense_series,
            "withdrawals": cash_out_withdrawal_series,
        },
    }


def _compute_ar_aging() -> Dict[str, float]:
    ar_aging_buckets = {"b0_30": 0.0, "b31_60": 0.0, "b61_90": 0.0, "b90_plus": 0.0}
    try:
        as_of = date.today()
        invoices_by_cust: Dict[str, Dict[str, Any]] = {}
        for inv in ar_invoices_col.find({}):
            code = (inv.get("customer") or "").strip() or "UNKNOWN"
            name = (inv.get("customer_name") or code).strip() or code
            amount = _safe_float(inv.get("amount"), 0.0)
            if amount <= 0:
                continue
            raw_due = inv.get("due") or inv.get("due_date")
            due_date = None
            if isinstance(raw_due, datetime):
                due_date = raw_due.date()
            elif isinstance(raw_due, date):
                due_date = raw_due
            elif isinstance(raw_due, str):
                try:
                    due_date = datetime.fromisoformat(raw_due).date()
                except Exception:
                    due_date = as_of
            if not due_date:
                due_date = as_of

            cust_block = invoices_by_cust.setdefault(code, {"name": name, "invoices": []})
            cust_block["invoices"].append({"amount": amount, "due_date": due_date})

        payments_by_cust: Dict[str, float] = {}
        rec_q = {
            "date_dt": {
                "$lte": datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, 999999)
            }
        }
        for r in ar_receipts_col.find(rec_q):
            cust = (r.get("customer") or "").strip()
            if not cust:
                continue
            paid_val = _safe_float(r.get("allocated", r.get("amount")), 0.0)
            if paid_val <= 0:
                continue
            payments_by_cust[cust] = payments_by_cust.get(cust, 0.0) + paid_val

        for code, data in invoices_by_cust.items():
            invs = data["invoices"]
            invs.sort(key=lambda x: x["due_date"])
            remaining_pay = payments_by_cust.get(code, 0.0)
            for inv in invs:
                amt = inv["amount"]
                applied = min(remaining_pay, amt)
                remaining_pay -= applied
                outstanding = amt - applied
                if outstanding <= 0:
                    continue
                age_days = (as_of - inv["due_date"]).days
                if age_days < 0:
                    age_days = 0
                if age_days <= 30:
                    ar_aging_buckets["b0_30"] += outstanding
                elif age_days <= 60:
                    ar_aging_buckets["b31_60"] += outstanding
                elif age_days <= 90:
                    ar_aging_buckets["b61_90"] += outstanding
                else:
                    ar_aging_buckets["b90_plus"] += outstanding
    except Exception:
        ar_aging_buckets = ar_aging_buckets
    return ar_aging_buckets


def _compute_recent_activity() -> List[Dict[str, Any]]:
    recent_events: List[Dict[str, Any]] = []
    try:
        for inv in ar_invoices_col.find({}):
            amount = _safe_float(inv.get("amount"), 0.0)
            cust_name = inv.get("customer_name") or inv.get("customer") or "Unknown"
            ev_created = _get_doc_date(inv, ["issue_dt", "created_at"])
            if ev_created:
                recent_events.append(
                    {
                        "ts": ev_created,
                        "type": "invoice",
                        "label": f"Invoice for {cust_name}",
                        "amount": amount,
                        "link": None,
                    }
                )
    except Exception:
        pass

    try:
        for pay in ar_receipts_col.find({}):
            amt = _safe_float(pay.get("amount"), 0.0)
            if amt <= 0:
                continue
            pay_dt = pay.get("date_dt") if isinstance(pay.get("date_dt"), datetime) else _get_doc_date(pay, ["date", "created_at"])
            if not pay_dt:
                continue
            recent_events.append(
                {
                    "ts": pay_dt,
                    "type": "payment",
                    "label": "Receipt recorded",
                    "amount": amt,
                    "link": None,
                }
            )
    except Exception:
        pass

    try:
        for inc in income_entries_col.find({}):
            amt = _safe_float(inc.get("amount"), 0.0)
            if amt <= 0:
                continue
            status = (inc.get("status") or "posted").strip().lower()
            if status in {"void", "voided", "cancelled"}:
                continue
            cat = (inc.get("category") or "").strip().lower()
            if cat not in {"discount received", "investment income"}:
                continue
            inc_dt = (
                inc.get("date_dt")
                if isinstance(inc.get("date_dt"), datetime)
                else _get_doc_date(inc, ["date_dt", "date", "created_at"])
            )
            if not inc_dt:
                continue
            recent_events.append(
                {
                    "ts": inc_dt,
                    "type": "income",
                    "label": inc.get("description") or (inc.get("category") or "Income recorded"),
                    "amount": amt,
                    "link": None,
                }
            )
    except Exception:
        pass

    try:
        for bill in ap_bills_col.find({}):
            amount = _safe_float(bill.get("amount"), 0.0)
            supp_name = bill.get("vendor_name") or bill.get("vendor") or "Unknown"
            ev_created = bill.get("bill_date_dt") if isinstance(bill.get("bill_date_dt"), datetime) else _get_doc_date(bill, ["bill_date", "created_at"])
            if ev_created:
                recent_events.append(
                    {
                        "ts": ev_created,
                        "type": "bill",
                        "label": f"Bill from {supp_name}",
                        "amount": amount,
                        "link": None,
                    }
                )
    except Exception:
        pass

    try:
        for exp in expenses_col.find({}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = _get_doc_date(exp, ["date"])
            if not exp_dt:
                continue
            recent_events.append(
                {
                    "ts": exp_dt,
                    "type": "expense",
                    "label": exp.get("description") or "Expense recorded",
                    "amount": amt,
                    "link": None,
                }
            )

        for exp in manager_expenses_col.find({"status": "Approved"}):
            amt = _safe_float(exp.get("amount"), 0.0)
            if amt <= 0:
                continue
            exp_dt = exp.get("created_at") if isinstance(exp.get("created_at"), datetime) else _get_doc_date(exp, ["created_at"])
            if not exp_dt:
                continue
            recent_events.append(
                {
                    "ts": exp_dt,
                    "type": "expense",
                    "label": exp.get("description") or "Manager expense approved",
                    "amount": amt,
                    "link": None,
                }
            )
    except Exception:
        pass

    recent_events_sorted = sorted(recent_events, key=lambda e: e["ts"], reverse=True)[:20]
    return [
        {
            "type": e["type"],
            "label": e["label"],
            "amount": _safe_float(e.get("amount"), 0.0),
            "ts": e["ts"].isoformat(),
        }
        for e in recent_events_sorted
    ]


def _compute_top_customers(customer_outstanding: Dict[str, float]) -> List[Dict[str, Any]]:
    return sorted(
        [{"name": name, "outstanding": amt} for name, amt in customer_outstanding.items()],
        key=lambda x: x["outstanding"],
        reverse=True,
    )[:5]


def _compute_top_suppliers(supplier_outstanding: Dict[str, float]) -> List[Dict[str, Any]]:
    return sorted(
        [{"name": name, "outstanding": amt} for name, amt in supplier_outstanding.items()],
        key=lambda x: x["outstanding"],
        reverse=True,
    )[:5]


def _compute_kpis(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    bank_cash = _compute_bank_cash()
    ar_total, ar_overdue_total, _ = _compute_ar_totals_and_customers()
    ap_total, _, _ = _compute_ap_totals_and_suppliers()
    net_sales_period, total_expenses_period = _compute_period_sales_expenses(start_dt, end_dt)

    try:
        current_year = date.today().year
        y_start = datetime(current_year, 1, 1, 0, 0, 0, 0)
        y_end = datetime(current_year, 12, 31, 23, 59, 59, 999999)
        pl_ctx = compute_profit_loss(y_start, y_end, branch_id=None, debug=False)
        net_profit_period = _safe_float(pl_ctx.get("net_profit"), 0.0)
    except Exception:
        net_profit_period = net_sales_period - total_expenses_period

    ar_overdue_pct = 0.0
    if ar_total > 0 and ar_overdue_total > 0:
        ar_overdue_pct = round((ar_overdue_total / ar_total) * 100.0, 1)

    return {
        "cash_balance": round(bank_cash.get("cash_balance", 0.0), 2),
        "ar_total": round(ar_total, 2),
        "ap_total": round(ap_total, 2),
        "net_profit": round(net_profit_period, 2),
        "expenses_total": round(total_expenses_period, 2),
        "ar_overdue_pct": ar_overdue_pct,
        "unreconciled_count": _compute_unreconciled_count(),
        "draft_journals": _compute_draft_journals(),
        "net_book_value": round(_compute_net_book_value(), 2),
    }


# AJAX endpoints for section-by-section dashboard loading.
@acc_dashboard.route("/dashboard/api/kpis", methods=["GET"])
@singleflight
def accounting_dashboard_kpis():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, start_dt, end_dt, range_label = _read_range_from_request()
    try:
        cached = _cache_get("kpis", range_key)
        if cached is not None:
            return jsonify(cached)
        data = {"kpis": _compute_kpis(start_dt, end_dt), "range_label": range_label}
        payload = _build_ok(range_key, data)
        _cache_set("kpis", range_key, payload, timeout=60)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.kpis %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/bank-cash", methods=["GET"])
@singleflight
def accounting_dashboard_bank_cash():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("bank-cash", range_key)
        if cached is not None:
            return jsonify(cached)
        data = _compute_bank_cash()
        payload = _build_ok(range_key, data)
        _cache_set("bank-cash", range_key, payload, timeout=30)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.bank_cash %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/sales-expense", methods=["GET"])
@singleflight
def accounting_dashboard_sales_expense():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("sales-expense", range_key)
        if cached is not None:
            return jsonify(cached)
        data = _compute_sales_expense_chart()
        payload = _build_ok(range_key, data)
        _cache_set("sales-expense", range_key, payload, timeout=120)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.sales_expense %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/cash-flow", methods=["GET"])
@singleflight
def accounting_dashboard_cash_flow():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("cash-flow", range_key)
        if cached is not None:
            return jsonify(cached)
        data = _compute_cash_flow_chart()
        payload = _build_ok(range_key, data)
        _cache_set("cash-flow", range_key, payload, timeout=120)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.cash_flow %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/ar-aging", methods=["GET"])
@singleflight
def accounting_dashboard_ar_aging():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("ar-aging", range_key)
        if cached is not None:
            return jsonify(cached)
        data = _compute_ar_aging()
        payload = _build_ok(range_key, data)
        _cache_set("ar-aging", range_key, payload, timeout=180)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.ar_aging %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/ap-due", methods=["GET"])
@singleflight
def accounting_dashboard_ap_due():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("ap-due", range_key)
        if cached is not None:
            return jsonify(cached)
        _, ap_due_buckets, _ = _compute_ap_totals_and_suppliers()
        payload = _build_ok(range_key, ap_due_buckets)
        _cache_set("ap-due", range_key, payload, timeout=180)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.ap_due %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/top-customers", methods=["GET"])
@singleflight
def accounting_dashboard_top_customers():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("top-customers", range_key)
        if cached is not None:
            return jsonify(cached)
        _, _, customer_outstanding = _compute_ar_totals_and_customers()
        data = _compute_top_customers(customer_outstanding)
        payload = _build_ok(range_key, {"items": data})
        _cache_set("top-customers", range_key, payload, timeout=180)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.top_customers %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/top-suppliers", methods=["GET"])
@singleflight
def accounting_dashboard_top_suppliers():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("top-suppliers", range_key)
        if cached is not None:
            return jsonify(cached)
        _, _, supplier_outstanding = _compute_ap_totals_and_suppliers()
        data = _compute_top_suppliers(supplier_outstanding)
        payload = _build_ok(range_key, {"items": data})
        _cache_set("top-suppliers", range_key, payload, timeout=180)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.top_suppliers %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard/api/recent-activity", methods=["GET"])
@singleflight
def accounting_dashboard_recent_activity():
    if not _accounting_access_guard():
        return _json_err("Unauthorized"), 403
    started = time.perf_counter()
    range_key, _, _, _ = _read_range_from_request()
    try:
        cached = _cache_get("recent-activity", range_key)
        if cached is not None:
            return jsonify(cached)
        data = _compute_recent_activity()
        payload = _build_ok(range_key, {"items": data})
        _cache_set("recent-activity", range_key, payload, timeout=30)
        return jsonify(payload)
    finally:
        current_app.logger.info("dashboard.api.recent_activity %.3fs", time.perf_counter() - started)


@acc_dashboard.route("/dashboard", methods=["GET"])
def accounting_dashboard() -> str:
    """
    Accounting overview dashboard.
    Aggregates key data from AR, AP, expenses, bank, fixed assets, etc.
    """
    if not _accounting_access_guard():
        return redirect(url_for("login.login"))
    range_key, _, _, range_label = _read_range_from_request()
    dashboard_data: Dict[str, Any] = {
        "range_key": range_key,
        "range_label": range_label,
    }

    return render_template(
        "accounting/dashboard.html",
        dashboard_data=dashboard_data,
    )
