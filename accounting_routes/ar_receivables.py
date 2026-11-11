from __future__ import annotations

from datetime import datetime, date
from calendar import month_abbr
import io
import csv
import math
import re
from typing import Any, Dict, List

from flask import Blueprint, render_template, request, url_for, Response, jsonify, redirect
from db import db

ar_receivables_bp = Blueprint("ar_receivables", __name__, template_folder="../templates")

clients_col = db["clients"]
inv_col = db["ar_invoices"]
rec_col = db["ar_receipts"]


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _safe_str(v: Any) -> str:
    return (v or "").strip()


def _parse_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val).date()
        except Exception:
            return None
    return None


def _invoice_due_date(inv: Dict[str, Any]) -> date | None:
    for key in ("due_dt", "due", "due_date", "issue_dt", "issue"):
        d = _parse_date(inv.get(key))
        if d:
            return d
    return None


def _receipt_amount(rec: Dict[str, Any]) -> float:
    allocated = _safe_float(rec.get("allocated"))
    if allocated > 0:
        return allocated
    return _safe_float(rec.get("amount"))


def _next_receipt_no() -> str:
    last = rec_col.find_one(
        {"no": {"$regex": r"^REC-\d+$"}},
        sort=[("date_dt", -1), ("_id", -1)],
    )
    if not last:
        return "REC-0001"
    m = re.search(r"(\d+)$", last.get("no", ""))
    if not m:
        return "REC-0001"
    num = int(m.group(1)) + 1
    return f"REC-{num:04d}"


def _compute_aging_days(oldest_due: date | None, today: date) -> int:
    if not oldest_due:
        return 0
    delta = (today - oldest_due).days
    return max(delta, 0)


def _aging_class(days: int) -> str:
    if days <= 30:
        return "bg-emerald-100 text-emerald-800"
    if days <= 60:
        return "bg-amber-100 text-amber-800"
    if days <= 90:
        return "bg-orange-100 text-orange-800"
    return "bg-rose-100 text-rose-800"


def _receivable_totals(invoices: List[Dict[str, Any]], receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate AR from ledger activity instead of stale invoice balance snapshots."""
    invoiced = sum(max(_safe_float(inv.get("amount")), 0.0) for inv in invoices)
    received = sum(max(_receipt_amount(rec), 0.0) for rec in receipts)
    balance = max(invoiced - received, 0.0)

    # Apply receipts to the oldest-due invoices first for accurate aging/next due.
    unapplied = received
    open_invoices: List[Dict[str, Any]] = []
    ordered = sorted(invoices, key=lambda inv: (_invoice_due_date(inv) or date.max, str(inv.get("_id") or "")))
    for inv in ordered:
        invoice_amount = max(_safe_float(inv.get("amount")), 0.0)
        applied = min(unapplied, invoice_amount)
        unapplied -= applied
        remaining = max(invoice_amount - applied, 0.0)
        if remaining > 0.005:
            open_invoices.append({"invoice": inv, "remaining": remaining})

    return {
        "invoiced": invoiced,
        "received": received,
        "balance": balance,
        "open_invoices": open_invoices,
    }


@ar_receivables_bp.get("/ar")
def ar_redirect():
    return redirect(url_for("ar_receivables.ar_receivables_home"))


@ar_receivables_bp.get("/ar/receivables")
def ar_receivables_home():
    qtxt = _safe_str(request.args.get("q"))
    page = max(1, int(request.args.get("page", 1)))
    per = min(60, max(12, int(request.args.get("per", 24))))
    export = request.args.get("export") == "1"

    client_docs: List[Dict[str, Any]] = []
    if qtxt:
        rx = re.compile(re.escape(qtxt), re.IGNORECASE)
        codes: set[str] = set()

        for c in clients_col.find({"$or": [{"code": rx}, {"name": rx}, {"phone": rx}, {"email": rx}]}, {"code": 1}):
            if c.get("code"):
                codes.add(c.get("code"))

        for inv in inv_col.find({"no": rx}, {"customer": 1}):
            if inv.get("customer"):
                codes.add(inv.get("customer"))

        for rec in rec_col.find({"$or": [{"no": rx}, {"reference": rx}]}, {"customer": 1}):
            if rec.get("customer"):
                codes.add(rec.get("customer"))

        if codes:
            client_docs = list(clients_col.find({"code": {"$in": list(codes)}}).sort([("name", 1), ("_id", 1)]))
        else:
            client_docs = []
    else:
        client_docs = list(clients_col.find({}).sort([("name", 1), ("_id", 1)]))

    client_codes = [c.get("code") for c in client_docs if c.get("code")]

    inv_docs = list(inv_col.find({"customer": {"$in": client_codes}})) if client_codes else []
    rec_docs = list(rec_col.find({"customer": {"$in": client_codes}})) if client_codes else []

    per_client: Dict[str, Dict[str, Any]] = {c.get("code"): {"client": c, "invoices": [], "receipts": []} for c in client_docs if c.get("code")}

    for inv in inv_docs:
        code = inv.get("customer")
        if code in per_client:
            per_client[code]["invoices"].append(inv)

    for rec in rec_docs:
        code = rec.get("customer")
        if code in per_client:
            per_client[code]["receipts"].append(rec)

    today = datetime.utcnow().date()

    total_invoiced = 0.0
    total_received = 0.0
    total_outstanding = 0.0

    rows: List[Dict[str, Any]] = []
    for code, block in per_client.items():
        client = block["client"]
        invoices = block["invoices"]
        receipts = block["receipts"]

        totals = _receivable_totals(invoices, receipts)
        invoiced_sum = totals["invoiced"]
        received_sum = totals["received"]
        outstanding_sum = totals["balance"]

        total_invoiced += invoiced_sum
        total_received += received_sum
        total_outstanding += outstanding_sum

        unpaid_invoices = [row["invoice"] for row in totals["open_invoices"]]
        due_dates = [d for d in (_invoice_due_date(i) for i in unpaid_invoices) if d]
        next_due = min(due_dates) if due_dates else None
        oldest_due = min(due_dates) if due_dates else None
        aging_days = _compute_aging_days(oldest_due, today)

        rows.append({
            "code": code,
            "name": client.get("name", ""),
            "phone": client.get("phone", ""),
            "email": client.get("email", ""),
            "status": client.get("status", "active"),
            "total_invoiced": invoiced_sum,
            "total_received": received_sum,
            "balance": outstanding_sum,
            "coverage_pct": min(100, int(round((received_sum / invoiced_sum * 100.0) if invoiced_sum > 0 else 0))),
            "payment_status": "Paid" if outstanding_sum <= 0.005 and invoiced_sum > 0 else ("Part paid" if received_sum > 0 else "Unpaid"),
            "next_due_date": next_due.isoformat() if next_due else "--",
            "aging_days": aging_days,
            "aging_class": _aging_class(aging_days),
        })

    coverage_pct = (total_received / total_invoiced * 100.0) if total_invoiced > 0 else 0.0
    summary = {
        "total_invoiced": total_invoiced,
        "total_paid": total_received,
        "total_outstanding": total_outstanding,
        "coverage_pct": int(round(coverage_pct)),
    }

    if export:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([
            "Client Code",
            "Client Name",
            "Total Invoiced",
            "Total Received",
            "Balance",
            "Aging Days",
            "Next Due Date",
        ])
        for r in rows:
            w.writerow([
                r.get("code", ""),
                r.get("name", ""),
                f"{_safe_float(r.get('total_invoiced')):0.2f}",
                f"{_safe_float(r.get('total_received')):0.2f}",
                f"{_safe_float(r.get('balance')):0.2f}",
                r.get("aging_days", 0),
                r.get("next_due_date", ""),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="ar_receivables.csv"'},
        )

    total = len(rows)
    pages = max(1, math.ceil(total / per))
    page = max(1, min(page, pages))
    start = (page - 1) * per
    end = start + per

    pager = {
        "total": total,
        "page": page,
        "pages": pages,
        "prev_url": _paginate_url(page - 1, per) if page > 1 else None,
        "next_url": _paginate_url(page + 1, per) if page < pages else None,
    }

    rows_page = rows[start:end]

    export_args = request.args.to_dict(flat=True)
    export_args["export"] = "1"
    export_url = url_for("ar_receivables.ar_receivables_home", **export_args)

    customers_list = [
        {"code": c.get("code", ""), "name": c.get("name", "")}
        for c in clients_col.find({}, {"code": 1, "name": 1}).sort([("name", 1)])
    ]

    return render_template(
        "accounting/ar_receivables.html",
        rows=rows_page,
        pager=pager,
        export_url=export_url,
        today=today.isoformat(),
        summary=summary,
        customers_list=customers_list,
    )


def _paginate_url(page: int, per: int) -> str:
    args = request.args.to_dict()
    args["page"] = str(page)
    args["per"] = str(per)
    return url_for("ar_receivables.ar_receivables_home", **args)


@ar_receivables_bp.get("/ar/receivables/metrics")
def ar_receivables_metrics():
    clients = list(clients_col.find({}, {"code": 1, "name": 1}))
    codes = [client.get("code") for client in clients if client.get("code")]
    invoices = list(inv_col.find({"customer": {"$in": codes}})) if codes else []
    receipts = list(rec_col.find({"customer": {"$in": codes}})) if codes else []
    invoices_by_client: Dict[str, List[Dict[str, Any]]] = {code: [] for code in codes}
    receipts_by_client: Dict[str, List[Dict[str, Any]]] = {code: [] for code in codes}
    client_names = {client.get("code"): client.get("name") or client.get("code") for client in clients}
    for invoice in invoices:
        if invoice.get("customer") in invoices_by_client:
            invoices_by_client[invoice["customer"]].append(invoice)
    for receipt in receipts:
        if receipt.get("customer") in receipts_by_client:
            receipts_by_client[receipt["customer"]].append(receipt)

    today = datetime.utcnow().date()
    accounts = []
    aging = {"current": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}
    for code in codes:
        totals = _receivable_totals(invoices_by_client[code], receipts_by_client[code])
        due_dates = [d for d in (_invoice_due_date(row["invoice"]) for row in totals["open_invoices"]) if d]
        days = _compute_aging_days(min(due_dates) if due_dates else None, today)
        balance = totals["balance"]
        if balance > 0:
            bucket = "current" if days <= 30 else ("31_60" if days <= 60 else ("61_90" if days <= 90 else "90_plus"))
            aging[bucket] += balance
        accounts.append({
            "code": code,
            "name": client_names.get(code) or code,
            "invoiced": totals["invoiced"],
            "received": totals["received"],
            "balance": balance,
            "aging_days": days,
            "has_paid": bool(receipts_by_client[code]),
        })

    total_invoiced = sum(row["invoiced"] for row in accounts)
    total_received = sum(row["received"] for row in accounts)
    total_outstanding = sum(row["balance"] for row in accounts)
    outstanding_accounts = [row for row in accounts if row["balance"] > 0.005]
    oldest = max(outstanding_accounts, key=lambda row: row["aging_days"], default=None)

    dated_receipts = []
    method_totals: Dict[str, float] = {}
    monthly_totals: Dict[tuple[int, int], float] = {}
    for receipt in receipts:
        amount = max(_receipt_amount(receipt), 0.0)
        receipt_date = _parse_date(receipt.get("date_dt") or receipt.get("date"))
        method = _safe_str(receipt.get("method")) or "Unspecified"
        method_totals[method] = method_totals.get(method, 0.0) + amount
        if receipt_date:
            dated_receipts.append((receipt_date, receipt, amount))
            monthly_totals[(receipt_date.year, receipt_date.month)] = monthly_totals.get((receipt_date.year, receipt_date.month), 0.0) + amount

    month_keys = []
    year, month = today.year, today.month
    for offset in range(5, -1, -1):
        absolute = year * 12 + month - 1 - offset
        month_keys.append((absolute // 12, absolute % 12 + 1))
    trend = [{"label": f"{month_abbr[m]} {str(y)[-2:]}", "amount": round(monthly_totals.get((y, m), 0.0), 2)} for y, m in month_keys]

    dated_receipts.sort(key=lambda row: (row[0], str(row[1].get("_id") or "")), reverse=True)
    recent = [{
        "client": client_names.get(row[1].get("customer")) or row[1].get("customer") or "Client",
        "code": row[1].get("customer") or "",
        "amount": round(row[2], 2),
        "date": row[0].isoformat(),
        "method": _safe_str(row[1].get("method")) or "Unspecified",
        "receipt": row[1].get("no") or "",
    } for row in dated_receipts[:8]]

    return jsonify(ok=True, metrics={
        "total_invoiced": round(total_invoiced, 2),
        "total_received": round(total_received, 2),
        "total_outstanding": round(total_outstanding, 2),
        "collection_rate": round((total_received / total_invoiced * 100) if total_invoiced else 0, 1),
        "active_receivables": len(outstanding_accounts),
        "never_paid": sum(1 for row in outstanding_accounts if not row["has_paid"]),
        "overdue_accounts": sum(1 for row in outstanding_accounts if row["aging_days"] > 0),
        "last_payment": recent[0] if recent else None,
        "oldest_account": oldest,
        "aging": {key: round(value, 2) for key, value in aging.items()},
        "trend": trend,
        "methods": [{"name": key, "amount": round(value, 2)} for key, value in sorted(method_totals.items(), key=lambda item: item[1], reverse=True)],
        "top_balances": sorted(outstanding_accounts, key=lambda row: row["balance"], reverse=True)[:6],
        "recent_payments": recent,
    })


@ar_receivables_bp.get("/ar/receivables/<client_code>/history")
def ar_receivables_history(client_code: str):
    code = _safe_str(client_code)
    if not code:
        return jsonify(ok=False, message="Invalid client code."), 400

    client = clients_col.find_one({"code": code}) or {}
    invs = list(inv_col.find({"customer": code}))
    recs = list(rec_col.find({"customer": code}))

    totals = _receivable_totals(invs, recs)
    total_invoiced = totals["invoiced"]
    total_received = totals["received"]
    total_balance = totals["balance"]

    items: List[Dict[str, Any]] = []

    for inv in invs:
        issue_date = _parse_date(inv.get("issue_dt") or inv.get("issue"))
        due_date = _parse_date(inv.get("due_dt") or inv.get("due") or inv.get("due_date"))
        date_str = issue_date.isoformat() if issue_date else (due_date.isoformat() if due_date else "")
        note = f"Due {due_date.isoformat()}" if due_date else ""
        items.append({
            "kind": "invoice",
            "date": date_str,
            "amount": _safe_float(inv.get("amount")),
            "label": inv.get("no", ""),
            "note": note,
        })

    for rec in recs:
        pay_date = _parse_date(rec.get("date_dt") or rec.get("date"))
        date_str = pay_date.isoformat() if pay_date else ""
        method = (rec.get("method") or "").strip()
        reference = (rec.get("reference") or "").strip()
        note = method
        if reference:
            note = f"{method} - ref {reference}" if method else f"ref {reference}"
        items.append({
            "kind": "payment",
            "date": date_str,
            "amount": _receipt_amount(rec),
            "label": rec.get("no", ""),
            "note": note,
        })

    def _sort_key(x: Dict[str, Any]):
        d = _parse_date(x.get("date"))
        return d or date.min

    items.sort(key=_sort_key, reverse=True)

    return jsonify(
        ok=True,
        client={"code": client.get("code", code), "name": client.get("name", "")},
        totals={
            "invoiced": total_invoiced,
            "received": total_received,
            "balance": total_balance,
        },
        items=items,
    )
