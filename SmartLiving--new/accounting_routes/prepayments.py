from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, Response, session, url_for, redirect
from datetime import datetime
import io
import csv

from db import db
from accounting_services import post_prepayment, prepayments_outstanding, _parse_month, _month_count
from services.prepayment_recognition import (
    generate_candidates,
    confirm_candidates,
    post_confirmed,
    reject_candidates,
)
from bson import ObjectId

prepayments_bp = Blueprint("prepayments", __name__, template_folder="../templates")

prepayments_col = db["prepayments"]


def _require_accounting_role():
    role = (session.get("role") or "").lower()
    if session.get("admin_id") or session.get("executive_id"):
        return True
    return role == "accounting"


@prepayments_bp.get("/prepayments")
def prepayments_page():
    if not _require_accounting_role():
        return render_template("accounting/prepayments.html", denied=True)

    export = request.args.get("export") == "1"
    rows = list(prepayments_col.find({}).sort("date_dt", -1))

    if export:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Category", "Vendor", "Amount", "Start", "End", "Monthly", "Status"])
        for r in rows:
            dt = r.get("date_dt")
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else ""
            w.writerow([
                dt_str,
                r.get("category", ""),
                r.get("vendor", ""),
                f"{float(r.get('amount_total', 0) or 0):0.2f}",
                r.get("start_period", ""),
                r.get("end_period", ""),
                f"{float(r.get('monthly_expense_amount', 0) or 0):0.2f}",
                r.get("status", ""),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="prepayments.csv"'},
        )

    total_outstanding = prepayments_outstanding(datetime.utcnow())
    pending_count = db["prepayment_recognition_queue"].count_documents({"status": "pending"})
    active_count = prepayments_col.count_documents({"status": "active"})

    # compute remaining per row for display
    for r in rows:
        sp = r.get("start_period") or ""
        ep = r.get("end_period") or ""
        months = _month_count(sp, ep)
        monthly = float(r.get("monthly_expense_amount", 0) or 0)
        r["months_total"] = months
        r["monthly_display"] = f"{monthly:,.2f}"

    return render_template(
        "accounting/prepayments.html",
        rows=rows,
        total_outstanding=total_outstanding,
        pending_count=pending_count,
        active_count=active_count,
        denied=False,
    )


@prepayments_bp.post("/prepayments/create")
def create_prepayment():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401

    payload = {
        "date_dt": request.form.get("date"),
        "category": request.form.get("category"),
        "vendor": request.form.get("vendor"),
        "amount_total": request.form.get("amount_total"),
        "start_period": request.form.get("start_period"),
        "end_period": request.form.get("end_period"),
        "created_by": session.get("user_id") or session.get("admin_id") or session.get("executive_id"),
    }
    result = post_prepayment(payload)
    if not result.get("ok"):
        return jsonify(ok=False, message=result.get("message") or "Failed"), 400
    return jsonify(ok=True)


@prepayments_bp.get("/prepayments/recognition")
def recognition_page():
    if not _require_accounting_role():
        return render_template("accounting/prepayment_recognition.html", denied=True)

    queue_col = db["prepayment_recognition_queue"]
    rows = list(queue_col.find({"status": {"$in": ["pending", "confirmed"]}}).sort("year", -1))
    total_pending = sum(float(r.get("amount") or 0) for r in rows if r.get("status") == "pending")
    return render_template(
        "accounting/prepayment_recognition.html",
        rows=rows,
        total_pending=total_pending,
        denied=False,
    )


@prepayments_bp.post("/prepayments/recognition/generate")
def recognition_generate():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    counts = generate_candidates(db, datetime.utcnow(), session.get("user_id") or session.get("admin_id") or session.get("executive_id"))
    return jsonify(ok=True, **counts)


def _get_ids_from_request() -> list[str]:
    if request.is_json:
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or []
    else:
        ids = request.form.getlist("ids")
    return [i for i in ids if isinstance(i, str) and ObjectId.is_valid(i)]


@prepayments_bp.post("/prepayments/recognition/confirm")
def recognition_confirm():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    ids = _get_ids_from_request()
    if not ids:
        return jsonify(ok=False, message="No items selected."), 400
    counts = confirm_candidates(db, ids, session.get("user_id") or session.get("admin_id") or session.get("executive_id"))
    return jsonify(ok=True, **counts)


@prepayments_bp.post("/prepayments/recognition/reject")
def recognition_reject():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    ids = _get_ids_from_request()
    note = (request.form.get("note") or "").strip() if not request.is_json else (request.get_json(silent=True) or {}).get("note") or ""
    if not ids:
        return jsonify(ok=False, message="No items selected."), 400
    counts = reject_candidates(db, ids, session.get("user_id") or session.get("admin_id") or session.get("executive_id"), note=note)
    return jsonify(ok=True, **counts)


@prepayments_bp.post("/prepayments/recognition/post")
def recognition_post():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    ids = _get_ids_from_request()
    counts = post_confirmed(db, ids or None, session.get("user_id") or session.get("admin_id") or session.get("executive_id"))
    return jsonify(ok=True, **counts)
