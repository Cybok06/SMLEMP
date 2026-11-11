from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
import csv
import io
import re

from bson import ObjectId
from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    Response,
    session,
    url_for,
)

from db import db

loans_bp = Blueprint("acc_loans", __name__, template_folder="../templates")

loans_col = db["loans"]
loan_schedules_col = db["loan_schedules"]
loan_postings_col = db["loan_postings"]
journal_entries_col = db["journal_entries"]
# Optional: consider a unique index on loans.client_request_id to enforce idempotency.

LOAN_LIABILITY_ACCOUNT = {"code": "LL-001", "name": "Loan Liability"}
INTEREST_EXPENSE_ACCOUNT = {"code": "EXP-INT", "name": "Interest Expense"}
BANK_CASH_ACCOUNT = {"code": "BANK-001", "name": "Bank / Cash"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        normalized = str(value).replace(",", "").strip()
        if normalized == "":
            return default
        return float(normalized)
    except Exception:
        return default


def _format_currency(value: float) -> str:
    return f"{value:,.2f}"


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return datetime(year, month, day, dt.hour, dt.minute, dt.second, dt.microsecond)


def _parse_date(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def _day_count_basis_denominator(basis: str) -> int:
    b = (basis or "30/360").lower()
    if b == "actual/360":
        return 360
    if b == "actual/365":
        return 365
    return 360


def _period_days(prev_date: datetime, due_date: datetime, basis: str, frequency: str) -> int:
    if (basis or "").lower() == "30/360" and frequency == "monthly":
        return 30
    delta = (due_date.date() - prev_date.date()).days
    return max(1, delta)


def _schedule_dates(start_date: datetime, periods: int, frequency: str, interval_days: int) -> list[datetime]:
    dates: list[datetime] = []
    for idx in range(periods):
        if frequency == "monthly":
            due = _add_months(start_date, idx + 1)
        else:
            due = start_date + timedelta(days=interval_days * (idx + 1))
        dates.append(due)
    return dates


def _require_accounting_role() -> bool:
    role = (session.get("role") or "").lower()
    if session.get("admin_id") or session.get("executive_id"):
        return True
    return role == "accounting"


def _is_admin_role() -> bool:
    role = (session.get("role") or "").lower()
    return bool(session.get("admin_id") or session.get("executive_id") or role == "accounting")


def _generate_loan_no() -> str:
    last = loans_col.find_one({}, sort=[("created_at", -1)])
    if not last:
        return "LN-0001"
    last_no = last.get("loan_no", "")
    match = re.search(r"(\d+)$", last_no)
    if not match:
        return "LN-0001"
    next_num = int(match.group(1)) + 1
    return f"LN-{next_num:04d}"


def _build_amortization_schedule(
    principal: float,
    annual_rate: float,
    term_months: int,
    start_date: datetime,
    amortization_method: str = "reducing_balance",
    payment_frequency: str = "monthly",
    custom_days_interval: int | None = None,
    interest_compounding: str = "monthly",
    day_count_basis: str = "30/360",
) -> tuple[list[Dict[str, Any]], float, float, float]:
    if term_months <= 0:
        raise ValueError("Term must be at least one month.")

    frequency = (payment_frequency or "monthly").lower()
    amort = (amortization_method or "reducing_balance").lower()
    basis = day_count_basis or "30/360"
    interval_days = 30
    if frequency == "weekly":
        interval_days = 7
    elif frequency == "biweekly":
        interval_days = 14
    elif frequency == "daily":
        interval_days = 1
    elif frequency == "custom":
        interval_days = max(1, int(custom_days_interval or 30))

    end_date = _add_months(start_date, term_months) if frequency == "monthly" else _add_months(start_date, term_months)
    if frequency == "monthly":
        periods = term_months
    else:
        total_days = max(1, (end_date.date() - start_date.date()).days)
        periods = max(1, int(round(total_days / interval_days)))

    dates = _schedule_dates(start_date, periods, "monthly" if frequency == "monthly" else frequency, interval_days)
    outstanding = principal
    total_interest = 0.0
    total_payment = 0.0
    entries: list[Dict[str, Any]] = []
    basis_den = _day_count_basis_denominator(basis)

    rate_for_payment = annual_rate / 100 / 12 if frequency == "monthly" and interest_compounding == "monthly" else (annual_rate / 100) * (interval_days / basis_den)
    if rate_for_payment < 0:
        rate_for_payment = 0.0

    if amort == "declining_principal":
        principal_each = round(principal / periods, 2) if periods else 0.0
    else:
        principal_each = 0.0

    fixed_payment = 0.0
    if amort == "reducing_balance":
        if rate_for_payment == 0:
            fixed_payment = round(principal / periods, 2)
        else:
            factor = (1 + rate_for_payment) ** periods
            fixed_payment = round(principal * rate_for_payment * factor / (factor - 1), 2)

    for period in range(1, periods + 1):
        due_date = dates[period - 1]
        prev_date = start_date if period == 1 else dates[period - 2]
        days_in_period = _period_days(prev_date, due_date, basis, frequency)
        rate_per_period = (annual_rate / 100) * (days_in_period / basis_den)

        interest = round(outstanding * rate_per_period, 2)
        principal_paid = 0.0
        payment = 0.0

        if amort == "flat":
            interest = round(principal * rate_per_period, 2)
            principal_paid = round(principal / periods, 2)
            payment = round(interest + principal_paid, 2)
        elif amort == "interest_only":
            payment = round(interest, 2)
            if period == periods:
                principal_paid = round(outstanding, 2)
                payment = round(payment + principal_paid, 2)
        elif amort == "declining_principal":
            principal_paid = principal_each
            if period == periods:
                principal_paid = round(outstanding, 2)
            payment = round(interest + principal_paid, 2)
        else:
            payment = fixed_payment
            principal_paid = round(payment - interest, 2)

        if period == periods:
            principal_paid = round(outstanding, 2)
            payment = round(interest + principal_paid, 2)

        opening = outstanding
        outstanding = round(max(outstanding - principal_paid, 0.0), 2)
        total_interest += interest
        total_payment += payment

        period_key = f"{due_date.year}-{due_date.month:02d}" if frequency == "monthly" else f"{due_date:%Y%m%d}-{period}"
        entries.append(
            {
                "period_no": period,
                "period_key": period_key,
                "period_date_dt": due_date,
                "opening_balance": round(opening, 2),
                "payment": round(payment, 2),
                "interest": round(interest, 2),
                "principal": round(principal_paid, 2),
                "closing_balance": round(outstanding, 2),
                "due_interest": round(interest, 2),
                "due_principal": round(principal_paid, 2),
                "paid_interest": 0.0,
                "paid_principal": 0.0,
                "paid_total": 0.0,
                "remaining_interest": round(interest, 2),
                "remaining_principal": round(principal_paid, 2),
                "days_in_period": days_in_period,
                "rate_per_period": round(rate_per_period, 6),
                "method_used": amort,
                "frequency_used": frequency,
                "status": "due",
                "created_at": datetime.utcnow(),
            }
        )

    period_payment = entries[0]["payment"] if entries else 0.0
    return entries, round(period_payment, 2), round(total_interest, 2), round(total_payment, 2)


def _ensure_schedule_created(loan_id: ObjectId, schedule_entries: list[Dict[str, Any]]) -> None:
    if not schedule_entries:
        return
    for entry in schedule_entries:
        entry["loan_id"] = loan_id
    loan_schedules_col.insert_many(schedule_entries)


def _format_loan_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "loan_no": doc.get("loan_no", ""),
        "lender_name": doc.get("lender_name", ""),
        "lender_type": doc.get("lender_type", ""),
        "reference": doc.get("reference", ""),
        "principal": _safe_float(doc.get("principal")),
        "outstanding": _safe_float(doc.get("outstanding_principal")),
        "monthly_payment": _safe_float(doc.get("monthly_payment")),
        "annual_interest_rate": _safe_float(doc.get("annual_interest_rate")),
        "term_months": int(doc.get("term_months") or 0),
        "status": (doc.get("status") or "active").lower(),
        "start_date_dt": doc.get("start_date_dt"),
        "maturity_date_dt": doc.get("maturity_date_dt"),
        "total_repaid": _safe_float(doc.get("total_repaid")),
        "last_posted_period": doc.get("last_posted_period"),
        "notes": doc.get("notes", ""),
        "currency": doc.get("currency", "GHS").upper(),
        "monthly_payment_display": _format_currency(_safe_float(doc.get("monthly_payment"))),
    }


def _next_due_schedule(loan_id: ObjectId, as_of: datetime) -> Optional[Dict[str, Any]]:
    return loan_schedules_col.find_one(
        {"loan_id": loan_id, "status": "due", "period_date_dt": {"$gte": as_of}},
        sort=[("period_date_dt", 1)],
    )


def _get_interest_due_for_month(as_of: date) -> float:
    start = datetime(as_of.year, as_of.month, 1)
    end = _add_months(start, 1)
    match = {
        "status": "due",
        "period_date_dt": {"$gte": start, "$lt": end},
    }
    pipeline = [
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": "$interest"}}},
    ]
    row = next(loan_schedules_col.aggregate(pipeline), None)
    return round(_safe_float(row["total"]) if row else 0.0, 2)


def _create_journal_entry(
    ref: str,
    memo: str,
    date_dt: datetime,
    lines: List[Dict[str, Any]],
) -> ObjectId:
    entry = {
        "date_dt": date_dt,
        "ref": ref,
        "memo": memo,
        "lines": lines,
        "created_at": datetime.utcnow(),
    }
    result = journal_entries_col.insert_one(entry)
    return result.inserted_id


def get_loans_outstanding(as_of: date | None = None) -> float:
    cutoff = datetime.combine(as_of or date.today(), datetime.max.time())
    pipeline = [
        {"$match": {"status": {"$in": ["active", "closed"]}}},
        {"$project": {"outstanding_principal": 1}},
    ]
    total = 0.0
    for doc in loans_col.aggregate(pipeline):
        total += _safe_float(doc.get("outstanding_principal"))
    return round(total, 2)


@loans_bp.get("/loans")
def loans_page():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 403

    qtxt = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    as_of_str = (request.args.get("as_of") or "").strip()
    as_of_date = None
    if as_of_str:
        try:
            as_of_date = datetime.combine(datetime.fromisoformat(as_of_str).date(), datetime.max.time())
        except Exception:
            as_of_date = None

    query: Dict[str, Any] = {}
    if qtxt:
        regex = {"$regex": qtxt, "$options": "i"}
        query["$or"] = [
            {"loan_no": regex},
            {"lender_name": regex},
            {"reference": regex},
        ]
    if status in ("active", "closed"):
        query["status"] = status

    docs = list(loans_col.find(query).sort("created_at", -1))
    loans = [_format_loan_summary(doc) for doc in docs]

    loan_ids = [doc.get("_id") for doc in docs]
    postings_by_loan: Dict[ObjectId, Dict[str, float]] = {}
    schedule_stats_by_loan: Dict[ObjectId, Dict[str, Any]] = {}

    if loan_ids:
        postings_pipeline = [
            {"$match": {"loan_id": {"$in": loan_ids}}},
            {
                "$group": {
                    "_id": "$loan_id",
                    "interest_paid": {"$sum": "$amount_interest"},
                    "principal_paid": {"$sum": "$amount_principal"},
                }
            },
        ]
        for row in loan_postings_col.aggregate(postings_pipeline):
            postings_by_loan[row["_id"]] = {
                "interest_paid": _safe_float(row.get("interest_paid")),
                "principal_paid": _safe_float(row.get("principal_paid")),
            }

        today = datetime.utcnow()
        month_start = datetime(today.year, today.month, 1)
        month_end = _add_months(month_start, 1)
        far_future = datetime(today.year + 100, 1, 1)

        schedule_pipeline = [
            {"$match": {"loan_id": {"$in": loan_ids}}},
            {
                "$project": {
                    "loan_id": 1,
                    "status": 1,
                    "period_date_dt": 1,
                    "payment": 1,
                    "unpaid": {"$cond": [{"$ne": ["$status", "paid"]}, 1, 0]},
                    "overdue": {
                        "$cond": [
                            {"$and": [{"$ne": ["$status", "paid"]}, {"$lt": ["$period_date_dt", today]}]},
                            1,
                            0,
                        ]
                    },
                    "due_month": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$status", "paid"]},
                                    {"$gte": ["$period_date_dt", month_start]},
                                    {"$lt": ["$period_date_dt", month_end]},
                                ]
                            },
                            1,
                            0,
                        ]
                    },
                    "next_due": {
                        "$cond": [
                            {"$ne": ["$status", "paid"]},
                            "$period_date_dt",
                            far_future,
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": "$loan_id",
                    "term_remaining": {"$sum": "$unpaid"},
                    "overdue_count": {"$sum": "$overdue"},
                    "due_this_month_total": {
                        "$sum": {
                            "$cond": [{"$eq": ["$due_month", 1]}, "$payment", 0]
                        }
                    },
                    "next_due_date": {"$min": "$next_due"},
                }
            },
        ]

        for row in loan_schedules_col.aggregate(schedule_pipeline):
            next_due = row.get("next_due_date")
            if next_due and next_due >= far_future:
                next_due = None
            schedule_stats_by_loan[row["_id"]] = {
                "term_remaining": int(row.get("term_remaining") or 0),
                "overdue_count": int(row.get("overdue_count") or 0),
                "due_this_month_total": _safe_float(row.get("due_this_month_total")),
                "next_due_date": next_due,
            }

    for loan, raw in zip(loans, docs):
        oid = raw.get("_id")
        post = postings_by_loan.get(oid, {})
        sched = schedule_stats_by_loan.get(oid, {})

        total_interest_est = _safe_float(raw.get("total_interest_estimate"))
        total_payable = _safe_float(raw.get("principal")) + total_interest_est

        interest_paid = _safe_float(post.get("interest_paid"))
        principal_paid = _safe_float(post.get("principal_paid"))
        total_paid = _safe_float(raw.get("total_repaid")) or (interest_paid + principal_paid)

        paid_percent = (total_paid / total_payable * 100.0) if total_payable > 0 else 0.0

        loan["interest_paid"] = round(interest_paid, 2)
        loan["principal_paid"] = round(principal_paid, 2)
        loan["total_paid"] = round(total_paid, 2)
        loan["amount_left"] = round(_safe_float(raw.get("outstanding_principal")), 2)
        loan["paid_percent"] = int(round(paid_percent))
        loan["next_due_date"] = sched.get("next_due_date")
        loan["overdue_count"] = sched.get("overdue_count", 0)
        loan["due_this_month_total"] = sched.get("due_this_month_total", 0.0)
        loan["term_remaining"] = sched.get("term_remaining", 0)
        loan["payment_frequency"] = raw.get("payment_frequency", "monthly")
        loan["amortization_method"] = raw.get("amortization_method", "reducing_balance")

    active_loans = [loan for loan in loans if loan["status"] == "active"]
    active_count = len(active_loans)
    total_outstanding = round(sum(loan["outstanding"] for loan in loans), 2)
    bonding_date = as_of_date or datetime.utcnow()
    interest_due_month = _get_interest_due_for_month(bonding_date.date())
    next_due_doc = loan_schedules_col.find_one(
        {"status": "due", "period_date_dt": {"$gte": bonding_date}},
        sort=[("period_date_dt", 1)],
    )
    next_due_date = next_due_doc.get("period_date_dt") if next_due_doc else None

    if request.args.get("export") == "1":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "Loan No",
                "Lender Name",
                "Type",
                "Principal",
                "Outstanding",
                "Monthly Payment",
                "Rate (%)",
                "Term (months)",
                "Status",
                "Start Date",
            ]
        )
        for loan in loans:
            writer.writerow(
                [
                    loan["loan_no"],
                    loan["lender_name"],
                    loan["lender_type"],
                    f"{loan['principal']:0.2f}",
                    f"{loan['outstanding']:0.2f}",
                    f"{loan['monthly_payment']:0.2f}",
                    f"{loan['annual_interest_rate']:0.2f}",
                    loan["term_months"],
                    loan["status"],
                    loan["start_date_dt"].strftime("%Y-%m-%d") if loan["start_date_dt"] else "",
                ]
            )
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="loans.csv"'},
        )

    return render_template(
        "accounting/loans.html",
        loans=loans,
        active_count=active_count,
        total_outstanding=total_outstanding,
        total_interest_due=interest_due_month,
        next_due_date=next_due_date,
        denied=False,
        as_of=as_of_str,
    )


@loans_bp.post("/loans/create")
def create_loan():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401

    def _q(key: str) -> str:
        return (request.form.get(key) or "").strip()

    try:
        client_request_id = _q("client_request_id")
        lender_name = _q("lender_name")
        principal = _safe_float(request.form.get("principal"))
        annual_rate = _safe_float(request.form.get("interest_rate"))
        term_months = int(_safe_float(request.form.get("term_months"), 0))
        start_date = _q("start_date")
        lender_type = _q("lender_type") or "bank"
        reference = _q("reference")
        notes = _q("notes")
        currency = (request.form.get("currency") or "GHS").upper()
        amortization_method = _q("amortization_method") or "reducing_balance"
        repayment_type = _q("repayment_type") or "equal_installment"
        payment_frequency = _q("payment_frequency") or "monthly"
        custom_days_interval = int(_safe_float(request.form.get("custom_days_interval"), 0)) or None
        interest_compounding = _q("interest_compounding") or "monthly"
        day_count_basis = _q("day_count_basis") or "30/360"
        processing_fee = _safe_float(request.form.get("processing_fee"))
        insurance_fee = _safe_float(request.form.get("insurance_fee"))

        if not lender_name or principal <= 0 or term_months <= 0 or not start_date:
            return jsonify(ok=False, message="Please provide lender, principal, term and start date."), 400

        start_dt = _parse_date(start_date)
        if not start_dt:
            return jsonify(ok=False, message="Start date is invalid."), 400

        if client_request_id:
            existing = loans_col.find_one({"client_request_id": client_request_id})
            if existing:
                existing_id = str(existing.get("_id"))
                return jsonify(
                    ok=True,
                    loan_id=existing_id,
                    redirect_url=url_for("acc_loans.loan_detail", loan_id=existing_id),
                )

        schedule, payment_amount, total_interest, total_payable = _build_amortization_schedule(
            principal=principal,
            annual_rate=annual_rate,
            term_months=term_months,
            start_date=start_dt,
            amortization_method=amortization_method,
            payment_frequency=payment_frequency,
            custom_days_interval=custom_days_interval,
            interest_compounding=interest_compounding,
            day_count_basis=day_count_basis,
        )
        if not schedule:
            return jsonify(ok=False, message="Schedule could not be generated. Check loan inputs."), 400

        loan_doc = {
            "loan_no": _generate_loan_no(),
            "lender_name": lender_name,
            "lender_type": lender_type,
            "reference": reference,
            "principal": principal,
            "annual_interest_rate": annual_rate,
            "term_months": term_months,
            "start_date_dt": start_dt,
            "payment_frequency": payment_frequency,
            "custom_days_interval": custom_days_interval,
            "amortization_method": amortization_method,
            "repayment_type": repayment_type,
            "interest_compounding": interest_compounding,
            "day_count_basis": day_count_basis,
            "processing_fee": processing_fee,
            "insurance_fee": insurance_fee,
            "currency": currency,
            "status": "active",
            "notes": notes,
            "monthly_payment": payment_amount,
            "total_interest_estimate": total_interest,
            "total_payable_estimate": total_payable,
            "outstanding_principal": principal,
            "total_repaid": 0.0,
            "last_posted_period": None,
            "maturity_date_dt": schedule[-1]["period_date_dt"] if schedule else start_dt,
            "client_request_id": client_request_id or None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        result = loans_col.insert_one(loan_doc)
        loan_id = result.inserted_id
        try:
            _ensure_schedule_created(loan_id, schedule)
        except Exception as exc:
            print({"evt": "loan_schedule_insert_failed", "loan_id": str(loan_id), "error": str(exc)})
            loans_col.delete_one({"_id": loan_id})
            loan_schedules_col.delete_many({"loan_id": loan_id})
            return jsonify(ok=False, message="Failed to create schedule. Loan not saved."), 400

        return jsonify(
            ok=True,
            loan_id=str(loan_id),
            redirect_url=url_for("acc_loans.loan_detail", loan_id=str(loan_id)),
        )
    except Exception as exc:
        print({"evt": "loan_create_failed", "error": str(exc), "payload": dict(request.form)})
        return jsonify(ok=False, message="Failed to create loan. Please check inputs."), 400


@loans_bp.post("/loans/preview-schedule")
def preview_schedule():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    try:
        principal = _safe_float(request.form.get("principal"))
        annual_rate = _safe_float(request.form.get("interest_rate"))
        term_months = int(_safe_float(request.form.get("term_months"), 0))
        start_date = (request.form.get("start_date") or "").strip()
        amortization_method = (request.form.get("amortization_method") or "reducing_balance").strip()
        payment_frequency = (request.form.get("payment_frequency") or "monthly").strip()
        custom_days_interval = int(_safe_float(request.form.get("custom_days_interval"), 0)) or None
        interest_compounding = (request.form.get("interest_compounding") or "monthly").strip()
        day_count_basis = (request.form.get("day_count_basis") or "30/360").strip()

        if principal <= 0 or term_months <= 0:
            return jsonify(ok=False, message="Principal and term are required."), 400
        start_dt = _parse_date(start_date)
        if not start_dt:
            return jsonify(ok=False, message="Start date is invalid."), 400

        schedule, payment_amount, total_interest, total_payable = _build_amortization_schedule(
            principal=principal,
            annual_rate=annual_rate,
            term_months=term_months,
            start_date=start_dt,
            amortization_method=amortization_method,
            payment_frequency=payment_frequency,
            custom_days_interval=custom_days_interval,
            interest_compounding=interest_compounding,
            day_count_basis=day_count_basis,
        )
        preview_rows: list[Dict[str, Any]] = []
        for row in schedule[:6]:
            row_copy = dict(row)
            dt = row_copy.get("period_date_dt")
            if isinstance(dt, datetime):
                row_copy["period_date_dt"] = dt.isoformat()
            preview_rows.append(row_copy)

        last_row = dict(schedule[-1]) if schedule else {}
        if isinstance(last_row.get("period_date_dt"), datetime):
            last_row["period_date_dt"] = last_row["period_date_dt"].isoformat()
        return jsonify(
            ok=True,
            payment_amount=payment_amount,
            total_interest=total_interest,
            total_payable=total_payable,
            preview=preview_rows,
            last=last_row,
        )
    except Exception as exc:
        print({"evt": "loan_preview_failed", "error": str(exc)})
        return jsonify(ok=False, message="Could not build preview."), 400


@loans_bp.get("/loans/<loan_id>")
def loan_detail(loan_id: str):
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 403

    try:
        oid = ObjectId(loan_id)
    except Exception:
        return jsonify(ok=False, message="Invalid loan id."), 400

    loan = loans_col.find_one({"_id": oid})
    if not loan:
        return jsonify(ok=False, message="Loan not found."), 404

    status_filter = (request.args.get("schedule_status") or "all").lower()
    schedule_query = {"loan_id": oid}
    if status_filter in {"due", "posted", "paid"}:
        schedule_query["status"] = status_filter
    schedule_cursor = loan_schedules_col.find(schedule_query).sort("period_no", 1)
    schedule = list(schedule_cursor)

    selected_period_key = request.args.get("period_key") or (schedule[0]["period_key"] if schedule else "")
    selected_schedule = next((row for row in schedule if row["period_key"] == selected_period_key), schedule[0] if schedule else {})

    postings = list(
        loan_postings_col.find({"loan_id": oid}).sort("posted_at", -1).limit(10)
    )

    payment_page = max(1, int(request.args.get("payment_page", 1)))
    payment_per = 10
    payment_filter = {
        "loan_id": oid,
        "$or": [{"amount_interest": {"$gt": 0}}, {"amount_principal": {"$gt": 0}}],
    }
    payment_total = loan_postings_col.count_documents(payment_filter)
    payment_pages = max(1, (payment_total + payment_per - 1) // payment_per)
    payment_page = max(1, min(payment_page, payment_pages))
    payment_skip = (payment_page - 1) * payment_per
    payment_rows = list(
        loan_postings_col.find(payment_filter)
        .sort("posted_at", -1)
        .skip(payment_skip)
        .limit(payment_per)
    )

    next_due_doc = loan_schedules_col.find_one(
        {"loan_id": oid, "status": "due"}, sort=[("period_date_dt", 1)]
    )
    auto_next_period = next_due_doc.get("period_key") if next_due_doc else None
    last_posted = loan.get("last_posted_period")

    interest_due_month = _get_interest_due_for_month(datetime.utcnow().date())

    return render_template(
        "accounting/loan_detail.html",
        loan=_format_loan_summary(loan),
        schedule=schedule,
        schedule_status=status_filter,
        selected_period=selected_schedule,
        postings=postings,
        payment_rows=payment_rows,
        payment_total=payment_total,
        payment_page=payment_page,
        payment_pages=payment_pages,
        auto_next_period=auto_next_period,
        last_posted_period=last_posted,
        interest_due_month=interest_due_month,
        denied=False,
    )


@loans_bp.post("/loans/<loan_id>/post-interest")
def post_interest(loan_id: str):
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401

    period_key = (request.form.get("period_key") or "").strip()
    if not period_key:
        return jsonify(ok=False, message="Period key required."), 400

    try:
        oid = ObjectId(loan_id)
    except Exception:
        return jsonify(ok=False, message="Invalid loan id."), 400

    loan = loans_col.find_one({"_id": oid})
    if not loan:
        return jsonify(ok=False, message="Loan not found."), 404

    schedule_row = loan_schedules_col.find_one(
        {"loan_id": oid, "period_key": period_key}
    )
    if not schedule_row or schedule_row.get("status") != "due":
        return jsonify(ok=False, message="Period already posted or invalid."), 400

    interest = _safe_float(schedule_row.get("interest"))
    ref = f"{loan.get('loan_no')}-INT-{period_key}"
    journal_id = _create_journal_entry(
        ref=ref,
        memo=f"Interest posting for {period_key}",
        date_dt=datetime.utcnow(),
        lines=[
            {
                "account_code": INTEREST_EXPENSE_ACCOUNT["code"],
                "account_name": INTEREST_EXPENSE_ACCOUNT["name"],
                "debit": interest,
                "credit": 0.0,
            },
            {
                "account_code": LOAN_LIABILITY_ACCOUNT["code"],
                "account_name": LOAN_LIABILITY_ACCOUNT["name"],
                "debit": 0.0,
                "credit": interest,
            },
        ],
    )

    loan_postings_col.insert_one(
        {
            "loan_id": oid,
            "period_key": period_key,
            "posted_at": datetime.utcnow(),
            "amount_interest": interest,
            "amount_principal": 0.0,
            "journal_ref": str(journal_id),
            "created_by": session.get("admin_id") or session.get("executive_id") or session.get("user_id"),
        }
    )

    loan_schedules_col.update_one(
        {"_id": schedule_row["_id"]},
        {"$set": {"status": "posted"}},
    )
    loans_col.update_one(
        {"_id": oid},
        {"$set": {"last_posted_period": period_key, "updated_at": datetime.utcnow()}},
    )

    return jsonify(ok=True, journal_ref=str(journal_id), next_period=period_key)


@loans_bp.post("/loans/<loan_id>/record-payment")
def record_payment(loan_id: str):
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401

    try:
        amount = _safe_float(request.form.get("amount"))
        period_key = (request.form.get("period_key") or "").strip()
        account_name = (request.form.get("account_name") or "Bank / Cash").strip()
        account_code = (request.form.get("account_code") or BANK_CASH_ACCOUNT["code"]).strip()

        print(
            {
                "evt": "loan_payment_attempt",
                "loan_id": loan_id,
                "amount": amount,
                "period_key": period_key,
                "account_code": account_code,
            }
        )

        if amount <= 0:
            return jsonify(ok=False, message="Payment amount required."), 400

        try:
            oid = ObjectId(loan_id)
        except Exception:
            return jsonify(ok=False, message="Invalid loan id."), 400

        loan = loans_col.find_one({"_id": oid})
        if not loan:
            return jsonify(ok=False, message="Loan not found."), 404

        schedule_rows = list(
            loan_schedules_col.find(
                {"loan_id": oid, "status": {"$in": ["due", "posted", "partial"]}}
            ).sort("period_date_dt", 1)
        )
        if not schedule_rows:
            return jsonify(ok=False, message="No unpaid schedule rows found."), 400

        if not period_key:
            period_key = schedule_rows[0].get("period_key") or ""

        start_idx = next((i for i, r in enumerate(schedule_rows) if r.get("period_key") == period_key), 0)
        schedule_rows = schedule_rows[start_idx:]

        remaining_amount = amount
        interest_paid = 0.0
        principal_paid = 0.0
        allocation_breakdown: list[Dict[str, Any]] = []

        for row in schedule_rows:
            if remaining_amount <= 0:
                break

            due_interest = _safe_float(row.get("due_interest", row.get("interest")))
            due_principal = _safe_float(row.get("due_principal", row.get("principal")))
            paid_interest = _safe_float(row.get("paid_interest"))
            paid_principal = _safe_float(row.get("paid_principal"))

            remaining_interest = _safe_float(row.get("remaining_interest", due_interest - paid_interest))
            remaining_principal = _safe_float(row.get("remaining_principal", due_principal - paid_principal))

            alloc_interest = min(remaining_amount, max(remaining_interest, 0.0))
            remaining_amount -= alloc_interest
            alloc_principal = min(remaining_amount, max(remaining_principal, 0.0))
            remaining_amount -= alloc_principal

            if alloc_interest <= 0 and alloc_principal <= 0:
                continue

            paid_interest = round(paid_interest + alloc_interest, 2)
            paid_principal = round(paid_principal + alloc_principal, 2)
            remaining_interest = round(max(due_interest - paid_interest, 0.0), 2)
            remaining_principal = round(max(due_principal - paid_principal, 0.0), 2)
            paid_total = round(paid_interest + paid_principal, 2)

            interest_paid += alloc_interest
            principal_paid += alloc_principal

            allocation_breakdown.append(
                {
                    "period_key": row.get("period_key"),
                    "interest": round(alloc_interest, 2),
                    "principal": round(alloc_principal, 2),
                }
            )

            new_status = row.get("status") if row.get("status") in ("due", "posted") else "due"
            if remaining_interest <= 0 and remaining_principal <= 0:
                new_status = "paid"

            loan_schedules_col.update_one(
                {"_id": row["_id"]},
                {
                    "$set": {
                        "due_interest": round(due_interest, 2),
                        "due_principal": round(due_principal, 2),
                        "paid_interest": paid_interest,
                        "paid_principal": paid_principal,
                        "paid_total": paid_total,
                        "remaining_interest": remaining_interest,
                        "remaining_principal": remaining_principal,
                        "status": new_status,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

            if new_status != "paid":
                break

        total_debit = round(interest_paid + principal_paid, 2)
        if total_debit <= 0:
            return jsonify(ok=False, message="Payment could not be allocated."), 400

        ref = f"{loan.get('loan_no')}-PAY-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        journal_id = _create_journal_entry(
            ref=ref,
            memo=f"Payment for {loan.get('loan_no')}",
            date_dt=datetime.utcnow(),
            lines=[
                {
                    "account_code": LOAN_LIABILITY_ACCOUNT["code"],
                    "account_name": LOAN_LIABILITY_ACCOUNT["name"],
                    "debit": total_debit,
                    "credit": 0.0,
                },
                {
                    "account_code": account_code,
                    "account_name": account_name,
                    "debit": 0.0,
                    "credit": total_debit,
                },
            ],
        )

        loan_postings_col.insert_one(
            {
                "loan_id": oid,
                "period_key": period_key or "",
                "posted_at": datetime.utcnow(),
                "amount_interest": round(interest_paid, 2),
                "amount_principal": round(principal_paid, 2),
                "journal_ref": str(journal_id),
                "created_by": session.get("admin_id") or session.get("executive_id") or session.get("user_id"),
                "allocation_breakdown": allocation_breakdown,
            }
        )

        outstanding = max(_safe_float(loan.get("outstanding_principal")) - principal_paid, 0.0)
        total_repaid = _safe_float(loan.get("total_repaid")) + total_debit
        status = "closed" if outstanding <= 0 else "active"

        loans_col.update_one(
            {"_id": oid},
            {
                "$set": {
                    "outstanding_principal": outstanding,
                    "total_repaid": total_repaid,
                    "status": status,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        return jsonify(
            ok=True,
            journal_ref=str(journal_id),
            outstanding=round(outstanding, 2),
            status=status,
        )
    except Exception as exc:
        print({"evt": "loan_payment_failed", "error": str(exc), "loan_id": loan_id})
        return jsonify(ok=False, message="Payment could not be recorded."), 500
