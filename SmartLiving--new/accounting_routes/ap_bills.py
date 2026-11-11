# accounting_routes/ap_bills.py
from __future__ import annotations

from flask import Blueprint, render_template, request, url_for, Response, jsonify
from datetime import datetime, timedelta
import io, csv, math, re
from typing import Any, Dict, List, Optional

from bson import ObjectId
from db import db
from services.activity_audit import audit_action
from login import get_current_identity
from .ap_payment_service import list_payment_accounts, pay_ap_bill

ap_bills_bp = Blueprint("ap_bills", __name__, template_folder="../templates")

# Mongo collection for AP bills
bills_col = db["ap_bills"]


def _iso(d: str | None):
    """Parse YYYY-MM-DD into datetime or return None."""
    if not d:
        return None
    try:
        return datetime.fromisoformat(d)
    except Exception:
        return None


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _safe_str(v: Any) -> str:
    return (v or "").strip()


def _parse_date_any(val: Any) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return None
    return None


def _paginate_url(page: int, per: int) -> str:
    args = request.args.to_dict()
    args["page"] = str(page)
    args["per"] = str(per)
    return url_for("ap_bills.bills", **args)


def _currency_symbol(currency: str, doc: Dict[str, Any]) -> str:
    if "symbol" in doc:
        return doc.get("symbol") or doc.get("currency_symbol", "") or ""
    currency = (currency or "GHS").strip().upper()
    if currency in ("GHS", "GH₵"):
        return "GH₵"
    if currency == "USD":
        return "$"
    return ""


def _recalc_status(balance: float, existing_status: str | None) -> str:
    s = (existing_status or "draft").lower()
    if balance <= 0:
        return "paid"
    # if already paid becomes unpaid again (shouldn't happen), fallback
    if s == "paid":
        return "approved"
    return s or "approved"


def _append_amount_history_entry(delta_amount: float, note: str, date_dt: datetime) -> Dict[str, Any]:
    return {
        "type": "add_amount",
        "amount": float(delta_amount),
        "note": (note or "").strip(),
        "date": date_dt,
        "created_at": datetime.utcnow(),
    }


def _latest_payment_dt(doc: Dict[str, Any]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for h in (doc.get("payment_history") or []):
        dt = _parse_date_any(h.get("date"))
        if dt and (latest is None or dt > latest):
            latest = dt
    return latest


# -------------------------------
# LISTING
# -------------------------------
@ap_bills_bp.get("/ap/bills")
def bills():
    """
    Accounts Payable Bills listing.

    - Supports text search (?q=)
    - Optional status filter (?status=)
    - Optional date range (?from=YYYY-MM-DD&to=YYYY-MM-DD)
    - Pagination (?page=&per=)
    - CSV export (?export=1)
    """
    qtxt   = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    dfrom  = _iso(request.args.get("from"))
    dto    = _iso(request.args.get("to"))
    sort_by = (request.args.get("sort") or "recent").strip().lower()
    page   = max(1, int(request.args.get("page", 1)))
    per    = min(60, max(12, int(request.args.get("per", 24))))
    export = request.args.get("export") == "1"

    # ------------------------
    # Build Mongo query
    # ------------------------
    q: Dict[str, Any] = {}

    if qtxt:
        rx = re.compile(re.escape(qtxt), re.IGNORECASE)
        q["$or"] = [
            {"no": rx},
            {"bill_no": rx},
            {"vendor": rx},
            {"vendor_name": rx},
            {"reference": rx},
        ]

    if status:
        q["status"] = status

    if dfrom or dto:
        q["bill_date_dt"] = {}
        if dfrom:
            q["bill_date_dt"]["$gte"] = datetime(dfrom.year, dfrom.month, dfrom.day)
        if dto:
            q["bill_date_dt"]["$lte"] = datetime(dto.year, dto.month, dto.day, 23, 59, 59, 999999)

    cur = bills_col.find(q).sort([("bill_date_dt", -1), ("_id", -1)])
    docs = list(cur)

    # ------------------------
    # In-memory sort options
    # ------------------------
    def _amount_of(d: Dict[str, Any]) -> float:
        return _safe_float(d.get("amount"))

    def _paid_of(d: Dict[str, Any]) -> float:
        return _safe_float(d.get("paid"))

    def _balance_of(d: Dict[str, Any]) -> float:
        return _safe_float(d.get("balance", _amount_of(d) - _paid_of(d)))

    def _bill_dt_of(d: Dict[str, Any]) -> datetime:
        return _parse_date_any(d.get("bill_date_dt")) or _parse_date_any(d.get("bill_date")) or datetime.min

    if sort_by == "outstanding_desc" or sort_by == "most_owed":
        docs.sort(key=lambda d: (_balance_of(d), _bill_dt_of(d)), reverse=True)
    elif sort_by == "outstanding_asc":
        docs.sort(key=lambda d: (_balance_of(d), _bill_dt_of(d)))
    elif sort_by == "recent_paid":
        docs.sort(
            key=lambda d: (
                1 if _latest_payment_dt(d) else 0,
                _latest_payment_dt(d) or datetime.min,
                _bill_dt_of(d),
            ),
            reverse=True,
        )
    else:
        # default: recent bills
        docs.sort(key=lambda d: (_bill_dt_of(d), str(d.get("_id") or "")), reverse=True)

    # ------------------------
    # Summary totals
    # ------------------------
    total_amount = 0.0
    total_paid = 0.0
    total_balance = 0.0
    total_bills = len(docs)
    unpaid_bills_count = 0
    overdue_bills_count = 0
    recent_paid_7d = 0.0
    paid_this_week = 0.0
    summary_today = datetime.utcnow().date()
    week_start = summary_today - timedelta(days=summary_today.weekday())

    for d in docs:
        amt = _safe_float(d.get("amount"))
        paid = _safe_float(d.get("paid"))
        bal = _safe_float(d.get("balance", amt - paid))
        st = (d.get("status") or "").strip().lower()
        total_amount += amt
        total_paid += paid
        total_balance += bal
        if bal > 0:
            unpaid_bills_count += 1
        if st == "overdue":
            overdue_bills_count += 1
        for payment in d.get("payment_history") or []:
            paid_dt = _parse_date_any(payment.get("date") or payment.get("created_at"))
            if not paid_dt:
                continue
            payment_amount = max(_safe_float(payment.get("amount")), 0.0)
            payment_day = paid_dt.date()
            days_ago = (summary_today - payment_day).days
            if 0 <= days_ago <= 7:
                recent_paid_7d += payment_amount
            if week_start <= payment_day <= summary_today:
                paid_this_week += payment_amount

    paid_ratio = (total_paid / total_amount * 100.0) if total_amount > 0 else 0.0
    ap_summary = {
        "total_amount": total_amount,
        "total_paid": total_paid,
        "total_balance": total_balance,
        "paid_pct": int(round(paid_ratio)),
        "total_bills": total_bills,
        "unpaid_bills_count": unpaid_bills_count,
        "overdue_bills_count": overdue_bills_count,
        "recent_paid_7d": recent_paid_7d,
        "paid_this_week": paid_this_week,
        "week_start": week_start.isoformat(),
    }

    # ------------------------
    # Metrics (for modal)
    # ------------------------
    today_dt = datetime.utcnow()
    status_amounts: Dict[str, float] = {"paid": 0.0, "partial": 0.0, "approved": 0.0, "draft": 0.0, "overdue": 0.0}
    status_counts: Dict[str, int] = {"paid": 0, "partial": 0, "approved": 0, "draft": 0, "overdue": 0}

    aging = {"b0_30": 0.0, "b31_60": 0.0, "b61_90": 0.0, "b90_plus": 0.0}
    overdue_amount = 0.0
    due_7 = 0.0
    due_30 = 0.0

    vendor_outstanding: Dict[str, float] = {}
    vendor_billed: Dict[str, float] = {}

    pay_days: List[int] = []

    for d in docs:
        amt = _safe_float(d.get("amount"))
        paid = _safe_float(d.get("paid"))
        bal = _safe_float(d.get("balance", amt - paid))
        status = (d.get("status") or "draft").lower()
        if status not in status_amounts:
            status = "draft"

        status_amounts[status] += amt
        status_counts[status] += 1

        vendor_key = _safe_str(d.get("vendor_name")) or _safe_str(d.get("vendor")) or "Unknown"
        vendor_outstanding[vendor_key] = vendor_outstanding.get(vendor_key, 0.0) + max(bal, 0.0)
        vendor_billed[vendor_key] = vendor_billed.get(vendor_key, 0.0) + amt

        if bal > 0:
            due_dt = _parse_date_any(d.get("due_date_dt")) or _parse_date_any(d.get("due_date"))
            if due_dt:
                delta_days = (today_dt.date() - due_dt.date()).days
                delta_days = max(delta_days, 0)
                if delta_days <= 30:
                    aging["b0_30"] += bal
                elif delta_days <= 60:
                    aging["b31_60"] += bal
                elif delta_days <= 90:
                    aging["b61_90"] += bal
                else:
                    aging["b90_plus"] += bal

                if due_dt.date() < today_dt.date():
                    overdue_amount += bal
                else:
                    days_ahead = (due_dt.date() - today_dt.date()).days
                    if days_ahead <= 7:
                        due_7 += bal
                    if days_ahead <= 30:
                        due_30 += bal

        if (d.get("status") or "").lower() == "paid":
            bill_dt = _parse_date_any(d.get("bill_date_dt")) or _parse_date_any(d.get("bill_date"))
            paid_dt = None
            hist = d.get("payment_history") or []
            for h in hist:
                dt = _parse_date_any(h.get("date"))
                if dt and (paid_dt is None or dt > paid_dt):
                    paid_dt = dt
            if bill_dt and paid_dt:
                pay_days.append(max((paid_dt.date() - bill_dt.date()).days, 0))

    def _top_items(src: Dict[str, float], n: int = 5) -> List[Dict[str, Any]]:
        items = [{"name": k, "value": v} for k, v in src.items()]
        items.sort(key=lambda x: x["value"], reverse=True)
        return items[:n]

    avg_days = round(sum(pay_days) / len(pay_days), 1) if pay_days else None
    med_days = None
    if pay_days:
        s = sorted(pay_days)
        mid = len(s) // 2
        med_days = float(s[mid]) if len(s) % 2 else round((s[mid - 1] + s[mid]) / 2, 1)

    ap_metrics = {
        "totals": {
            "total_outstanding": total_balance,
            "total_billed": total_amount,
            "total_paid": total_paid,
            "coverage_pct": int(round(paid_ratio)),
        },
        "status_breakdown": {
            "amounts": status_amounts,
            "counts": status_counts,
        },
        "aging": aging,
        "top_vendors": {
            "by_outstanding": _top_items(vendor_outstanding),
            "by_billed": _top_items(vendor_billed),
        },
        "due_soon": {
            "overdue": overdue_amount,
            "next_7": due_7,
            "next_30": due_30,
        },
        "payment_velocity": {
            "avg_days": avg_days,
            "median_days": med_days,
            "count": len(pay_days),
        },
    }

    # ------------------------
    # CSV export
    # ------------------------
    if export and docs:
        out = io.StringIO()
        w   = csv.writer(out)
        w.writerow([
            "Bill No",
            "Vendor",
            "Bill Date",
            "Due Date",
            "Currency",
            "Amount",
            "Paid",
            "Balance",
            "Status",
        ])
        for d in docs:
            amt  = _safe_float(d.get("amount"))
            paid = _safe_float(d.get("paid"))
            bal  = _safe_float(d.get("balance", amt - paid))
            w.writerow([
                d.get("no") or d.get("bill_no", ""),
                d.get("vendor_name") or d.get("vendor", ""),
                d.get("bill_date", ""),
                d.get("due_date", ""),
                d.get("currency", "GHS"),
                f"{amt:0.2f}",
                f"{paid:0.2f}",
                f"{bal:0.2f}",
                (d.get("status") or "draft").title(),
            ])

        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="ap_bills.csv"'},
        )

    # ------------------------
    # Pagination
    # ------------------------
    total = len(docs)
    pages = max(1, math.ceil(total / per))
    page  = max(1, min(page, pages))
    start = (page - 1) * per
    end   = start + per

    pager = {
        "total": total,
        "page": page,
        "pages": pages,
        "prev_url": _paginate_url(page - 1, per) if page > 1 else None,
        "next_url": _paginate_url(page + 1, per) if page < pages else None,
    }

    export_args = request.args.to_dict(flat=True)
    export_args["export"] = "1"
    export_url = url_for("ap_bills.bills", **export_args)

    # ------------------------
    # Map docs -> rows for template (cards)
    # ------------------------
    rows: List[Dict[str, Any]] = []
    for d in docs[start:end]:
        amt  = _safe_float(d.get("amount"))
        paid = _safe_float(d.get("paid"))
        bal  = _safe_float(d.get("balance", amt - paid))
        currency = d.get("currency", "GHS")
        sym = _currency_symbol(currency, d)

        rows.append({
            "_id": str(d.get("_id")),
            "no": d.get("no") or d.get("bill_no", ""),
            "bill_no": d.get("bill_no", ""),
            "reference": d.get("reference", ""),
            "vendor": d.get("vendor", ""),
            "vendor_name": d.get("vendor_name", ""),
            "bill_date": d.get("bill_date", ""),
            "due_date": d.get("due_date", ""),
            "currency": currency,
            "currency_symbol": sym,
            "amount": amt,
            "paid": paid,
            "balance": bal,
            "status": (d.get("status") or "draft").lower(),
        })

    today = datetime.utcnow().date().isoformat()

    return render_template(
        "accounting/ap_bills.html",
        rows=rows,
        pager=pager,
        export_url=export_url,
        today=today,
        ap_summary=ap_summary,
        ap_metrics=ap_metrics,
        sort_by=sort_by,
        payment_accounts=list_payment_accounts(),
    )


# -------------------------------
# QUICK CREATE
# -------------------------------
@ap_bills_bp.post("/ap/bills/quick")
@audit_action("bill.created", "Created Bill", entity_type="bill")
def quick_create():
    """
    Quick-create endpoint for the slide-over form on AP Bills.

    Create one new bill. Existing bills are updated only through the dedicated
    Add New Bill Amount action on their card.
    """
    def _f(x) -> float:
        try:
            return float(str(x).replace(",", ""))
        except Exception:
            return 0.0

    bill_no     = _safe_str(request.form.get("bill_no"))
    reference   = _safe_str(request.form.get("reference"))
    vendor_name = _safe_str(request.form.get("vendor_name"))
    vendor_code = _safe_str(request.form.get("vendor"))
    bill_date_s = _safe_str(request.form.get("bill_date"))
    due_date_s  = _safe_str(request.form.get("due_date"))
    currency    = (_safe_str(request.form.get("currency")) or "GHS").upper()
    status      = (_safe_str(request.form.get("status")) or "draft").lower()
    amount      = _f(request.form.get("amount"))
    paid        = _f(request.form.get("paid"))
    notes       = _safe_str(request.form.get("notes"))

    if not vendor_name or not bill_date_s or not due_date_s or amount <= 0:
        return jsonify(ok=False, message="Vendor, Bill Date, Due Date and Amount are required."), 400

    # Parse dates
    try:
        bill_date_dt = datetime.fromisoformat(bill_date_s)
    except Exception:
        return jsonify(ok=False, message="Invalid Bill Date."), 400

    try:
        due_date_dt = datetime.fromisoformat(due_date_s)
    except Exception:
        return jsonify(ok=False, message="Invalid Due Date."), 400

    # A create action must never silently alter an existing bill.
    if bill_no:
        existing = bills_col.find_one({"$or": [{"bill_no": bill_no}, {"no": bill_no}]}, {"_id": 1})
        if existing:
            return jsonify(ok=False, message="This bill number already exists. Use its Add New Bill action to add another amount."), 409

    # ---- Create new bill ----
    balance = max(amount - paid, 0.0)

    payment_history: List[Dict[str, Any]] = []
    if paid > 0:
        payment_history.append({
            "amount": paid,
            "method": "Initial",
            "note": "Initial amount at bill creation",
            "date": datetime.utcnow(),
            "created_at": datetime.utcnow(),
        })

    amount_history: List[Dict[str, Any]] = []
    # log initial amount as baseline (useful for audit)
    amount_history.append({
        "type": "initial_amount",
        "amount": amount,
        "note": (notes or reference or "Bill created").strip(),
        "date": datetime.utcnow(),
        "created_at": datetime.utcnow(),
    })

    doc = {
        "bill_no": bill_no,
        "no": bill_no,  # later you can switch to auto-number
        "reference": reference,
        "vendor": vendor_code or vendor_name,
        "vendor_name": vendor_name,
        "bill_date": bill_date_s,
        "bill_date_dt": bill_date_dt,
        "due_date": due_date_s,
        "due_date_dt": due_date_dt,
        "currency": currency,
        "amount": amount,
        "paid": paid,
        "balance": balance,
        "status": status,
        "notes": notes,
        "payment_history": payment_history,
        "amount_history": amount_history,
        "created_at": datetime.utcnow(),
    }

    res = bills_col.insert_one(doc)
    return jsonify(ok=True, created=True, bill_id=str(res.inserted_id), bill_no=bill_no or "")


# -------------------------------
# ADD PAYMENT (unchanged logic, kept)
# -------------------------------
@ap_bills_bp.post("/ap/bills/<bill_id>/add-payment")
@audit_action("bill.payment_recorded", "Recorded Bill Payment", entity_type="bill", entity_id_from="bill_id")
def add_payment(bill_id: str):
    """
    Add a payment against a single bill.

    - Increments `paid`
    - Recalculates `balance`
    - Appends to `payment_history`
    """
    amount = _safe_float(request.form.get("amount"))
    payment_date_s = _safe_str(request.form.get("payment_date"))
    method = _safe_str(request.form.get("method"))
    note = _safe_str(request.form.get("note"))
    account_id = _safe_str(request.form.get("account_id"))

    pay_dt = datetime.utcnow()
    if payment_date_s:
        try:
            pay_dt = datetime.fromisoformat(payment_date_s)
        except Exception:
            pass

    try:
        result = pay_ap_bill(
            bill_id=bill_id, account_id=account_id, amount=amount,
            payment_date=pay_dt, method=method, note=note,
            identity=get_current_identity(),
        )
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    return jsonify(ok=True, **result)


@ap_bills_bp.get("/ap/bills/<bill_id>/payments")
def get_payments(bill_id: str):
    """Return a bill's payment history as JSON."""
    try:
        oid = ObjectId(bill_id)
    except Exception:
        return jsonify(ok=False, message="Invalid bill ID."), 400

    bill = bills_col.find_one({"_id": oid}, {"payment_history": 1, "currency": 1})
    if not bill:
        return jsonify(ok=False, message="Bill not found."), 404

    hist = bill.get("payment_history", []) or []
    results: List[Dict[str, Any]] = []

    for p in hist:
        dt = p.get("date")
        if isinstance(dt, datetime):
            date_str = dt.strftime("%Y-%m-%d")
        else:
            date_str = str(dt or "")
        results.append({
            "amount": _safe_float(p.get("amount")),
            "method": p.get("method") or "",
            "note": p.get("note") or "",
            "date": date_str,
            "account_id": str(p.get("account_id") or ""),
            "account_name": p.get("account_name") or "",
            "account_type": p.get("account_type") or "",
        })

    return jsonify(ok=True, currency=bill.get("currency", "GHS"), payments=results)


# -------------------------------
# NEW: ADD AMOUNT TO EXISTING BILL
# -------------------------------
@ap_bills_bp.post("/ap/bills/<bill_id>/add-amount")
@audit_action("bill.amount_added", "Added Bill Amount", entity_type="bill", entity_id_from="bill_id")
def add_amount(bill_id: str):
    """
    Add more AMOUNT (charges) to an existing bill:
    - increments `amount`
    - recalculates balance = amount - paid
    - pushes to `amount_history` with optional note
    """
    try:
        oid = ObjectId(bill_id)
    except Exception:
        return jsonify(ok=False, message="Invalid bill ID."), 400

    bill = bills_col.find_one({"_id": oid})
    if not bill:
        return jsonify(ok=False, message="Bill not found."), 404

    delta = _safe_float(request.form.get("amount"))
    if delta <= 0:
        return jsonify(ok=False, message="Added amount must be greater than zero."), 400

    note = _safe_str(request.form.get("note"))
    date_s = _safe_str(request.form.get("date"))

    dt = datetime.utcnow()
    if date_s:
        try:
            dt = datetime.fromisoformat(date_s)
        except Exception:
            pass

    current_amount = _safe_float(bill.get("amount"))
    current_paid   = _safe_float(bill.get("paid"))

    new_amount  = current_amount + delta
    new_balance = max(new_amount - current_paid, 0.0)
    new_status  = _recalc_status(new_balance, bill.get("status"))

    entry = _append_amount_history_entry(delta_amount=delta, note=note, date_dt=dt)

    bills_col.update_one(
        {"_id": oid},
        {
            "$set": {
                "amount": new_amount,
                "balance": new_balance,
                "status": new_status,
                "updated_at": datetime.utcnow(),
            },
            "$push": {"amount_history": entry},
        },
    )

    return jsonify(ok=True, amount=new_amount, balance=new_balance, status=new_status)


@ap_bills_bp.get("/ap/bills/<bill_id>/amount-history")
def get_amount_history(bill_id: str):
    """Return a bill's AMOUNT additions history as JSON (amount_history)."""
    try:
        oid = ObjectId(bill_id)
    except Exception:
        return jsonify(ok=False, message="Invalid bill ID."), 400

    bill = bills_col.find_one({"_id": oid}, {"amount_history": 1, "currency": 1})
    if not bill:
        return jsonify(ok=False, message="Bill not found."), 404

    hist = bill.get("amount_history", []) or []
    results: List[Dict[str, Any]] = []

    for p in hist:
        dt = p.get("date")
        if isinstance(dt, datetime):
            date_str = dt.strftime("%Y-%m-%d")
        else:
            date_str = str(dt or "")
        results.append({
            "type": p.get("type") or "",
            "amount": _safe_float(p.get("amount")),
            "note": p.get("note") or "",
            "date": date_str,
        })

    # latest first
    results.sort(key=lambda x: (x.get("date") or ""), reverse=True)

    return jsonify(ok=True, currency=bill.get("currency", "GHS"), items=results)
