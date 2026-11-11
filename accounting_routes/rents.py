#!/usr/bin/env python3
"""
Rent Register routes.

When registered in app.py as:
    app.register_blueprint(rents_bp, url_prefix="/accounting/rents")
"""

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, Response, jsonify
)
from datetime import datetime, date
from db import db
import csv
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

rents_col = db["fixed_assets"]

rents_bp = Blueprint(
    "rents",
    __name__,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _safe_float(doc, key, default=0.0):
    try:
        return float(doc.get(key, default) or 0)
    except (TypeError, ValueError):
        return float(default)


def _parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _safe_int(value, default=0):
    try:
        return int(str(value or default).strip())
    except Exception:
        return int(default)


def _format_date(d):
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return ""


def _compute_progress(start_date, end_date, today=None):
    if not start_date or not end_date:
        return None, ""
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    if today is None:
        today = date.today()
    total_days = (end_date - start_date).days
    if total_days <= 0:
        return None, ""
    elapsed_days = (today - start_date).days
    if elapsed_days < 0:
        elapsed_days = 0
    if elapsed_days > total_days:
        elapsed_days = total_days
    pct = round((elapsed_days / total_days) * 100, 1)
    label = f"{elapsed_days} / {total_days} days ({pct}%)"
    return pct, label


def _auto_rent_id():
    """
    Generate next rent ID like RE-00001.
    """
    last = rents_col.find_one(
        {"asset_id": {"$regex": r"^RE-\d+$"}},
        sort=[("asset_id", -1)]
    )
    if not last:
        return "RE-00001"
    try:
        num = int(str(last["asset_id"]).split("-")[1])
    except Exception:
        num = 0
    return f"RE-{num + 1:05d}"


def _rent_to_view(doc):
    status = doc.get("status", "Active")
    cost = _safe_float(doc, "cost", 0)

    acq_date = _parse_date(doc.get("acquisition_date"))
    rent_due = _parse_date(doc.get("rent_due_date"))

    rent_progress_pct, rent_progress_label = _compute_progress(acq_date, rent_due)

    advance = doc.get("advance") or {}
    advance_amount = _safe_float(advance, "amount", 0)
    advance_years = int(advance.get("years") or 0)
    advance_note = advance.get("note", "")

    return {
        "_id": str(doc.get("_id")),
        "asset_id": doc.get("asset_id"),
        "name": doc.get("name"),
        "entry_type": (doc.get("entry_type") or "rent").lower(),
        "rent_type": doc.get("rent_type"),
        "rent_place": doc.get("rent_place"),
        "status": status,
        "notes": doc.get("notes", ""),

        "cost": cost,
        "cost_display": f"{cost:,.2f}",

        "acquisition_date_str": _format_date(acq_date),
        "rent_due_date_str": _format_date(rent_due),

        "advance_amount": advance_amount,
        "advance_years": advance_years,
        "advance_note": advance_note,
        "billing_mode": (doc.get("billing_mode") or "manual").lower(),
        "billing_amount": _safe_float(doc, "billing_amount", 0),
        "billing_years": _safe_int(doc.get("billing_years"), 0),
        "billing_label": doc.get("billing_label", ""),

        "rent_progress_pct": rent_progress_pct,
        "rent_progress_label": rent_progress_label,
    }


# -------------------------------------------------------------------
# Main register view
# -------------------------------------------------------------------

@rents_bp.route("/", methods=["GET"])
def register():
    q = (request.args.get("q") or "").strip()
    rent_type = (request.args.get("rent_type") or "").strip()
    status = (request.args.get("status") or "").strip()

    query = {"entry_type": "rent"}
    if q:
        query["$or"] = [
            {"asset_id": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]
    if rent_type:
        query["rent_type"] = rent_type
    if status:
        query["status"] = status

    docs = list(
        rents_col.find(query).sort("acquisition_date", -1)
    )

    rents = [_rent_to_view(doc) for doc in docs]

    rent_types = sorted([t for t in rents_col.distinct("rent_type", {"entry_type": "rent"}) if t])
    if not rent_types:
        rent_types = ["Office", "Warehouse", "Room"]
    statuses = ["Active", "Disposed"]

    return render_template(
        "accounting/rents_register.html",
        rents=rents,
        rent_types=rent_types,
        statuses=statuses,
        currency_symbol="GHS ",
    )


# -------------------------------------------------------------------
# Export CSV
# -------------------------------------------------------------------

@rents_bp.route("/export", methods=["GET"])
def export_rents():
    docs = list(
        rents_col.find({"entry_type": "rent"}).sort("acquisition_date", -1)
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rent ID",
        "Name",
        "Rent Type",
        "Place of Rent",
        "Date Rented",
        "Due Date",
        "Billing Mode",
        "Unit Amount",
        "Duration (Years)",
        "Rent Amount",
        "Advance Amount",
        "Advance Years",
        "Advance Note",
        "Status",
        "Notes",
    ])

    for doc in docs:
        cost = _safe_float(doc, "cost", 0)
        advance = doc.get("advance") or {}
        advance_amount = _safe_float(advance, "amount", 0)
        advance_years = int(advance.get("years") or 0)
        advance_note = advance.get("note", "")

        writer.writerow([
            doc.get("asset_id", ""),
            doc.get("name", ""),
            doc.get("rent_type", ""),
            doc.get("rent_place", ""),
            _format_date(_parse_date(doc.get("acquisition_date"))),
            _format_date(_parse_date(doc.get("rent_due_date"))),
            doc.get("billing_mode", "manual"),
            f"{_safe_float(doc, 'billing_amount', 0):,.2f}",
            _safe_int(doc.get("billing_years"), 0),
            f"{cost:,.2f}",
            f"{advance_amount:,.2f}",
            advance_years,
            advance_note,
            doc.get("status", "Active"),
            doc.get("notes", ""),
        ])

    output.seek(0)
    filename = f"rents_{date.today().isoformat()}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


@rents_bp.route("/export/pdf", methods=["GET"])
def export_rents_pdf():
    docs = list(rents_col.find({"entry_type": "rent"}).sort("acquisition_date", -1))
    rents = [_rent_to_view(d) for d in docs]

    total_rent = round(sum(_safe_float(r, "cost", 0) for r in rents), 2)
    active_count = sum(1 for r in rents if (r.get("status") or "Active") == "Active")
    disposed_count = sum(1 for r in rents if (r.get("status") or "") == "Disposed")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    elems = []

    elems.append(Paragraph("Rent Register Report", styles["Title"]))
    elems.append(Spacer(1, 6))
    elems.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", styles["Normal"]))
    elems.append(Spacer(1, 10))

    summary = [
        ["Metric", "Value"],
        ["Total Rent Value", f"{total_rent:,.2f}"],
        ["Total Records", str(len(rents))],
        ["Active", str(active_count)],
        ["Disposed", str(disposed_count)],
    ]
    sum_tbl = Table(summary, colWidths=[240, 220])
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    elems.append(sum_tbl)
    elems.append(Spacer(1, 12))

    data = [[
        "Rent ID", "Name", "Type", "Place", "Date Rented", "Due Date",
        "Billing", "Unit Amt", "Years", "Total Rent", "Status"
    ]]
    for r in rents:
        data.append([
            r.get("asset_id", ""),
            r.get("name", ""),
            r.get("rent_type", ""),
            r.get("rent_place", ""),
            r.get("acquisition_date_str", ""),
            r.get("rent_due_date_str", ""),
            r.get("billing_mode", "manual"),
            f"{_safe_float(r, 'billing_amount', 0):,.2f}",
            str(_safe_int(r.get("billing_years"), 0)),
            f"{_safe_float(r, 'cost', 0):,.2f}",
            r.get("status", "Active"),
        ])

    tbl = Table(
        data,
        colWidths=[58, 110, 70, 90, 70, 70, 60, 60, 45, 70, 60],
        repeatRows=1,
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16a34a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (7, 1), (9, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    elems.append(tbl)

    doc.build(elems)
    filename = f"rents_{date.today().isoformat()}.pdf"
    return Response(
        buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# -------------------------------------------------------------------
# Add Rent (supports normal POST and AJAX JSON)
# -------------------------------------------------------------------

@rents_bp.route("/add", methods=["POST"])
def add_rent_form():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    name = (request.form.get("name") or "").strip()
    rent_place = (request.form.get("rent_place") or "").strip()
    rent_type = (request.form.get("rent_type") or "").strip()
    rent_due_raw = request.form.get("rent_due_date") or ""
    acq_date_raw = request.form.get("acquisition_date") or ""
    cost_raw = request.form.get("cost") or "0"
    billing_mode = (request.form.get("billing_mode") or "manual").strip().lower()
    billing_amount_raw = request.form.get("billing_amount") or "0"
    billing_years_raw = request.form.get("billing_years") or "0"
    notes = (request.form.get("notes") or "").strip()

    advance_amount_raw = request.form.get("advance_amount") or "0"
    advance_years_raw = request.form.get("advance_years") or "0"
    advance_note = (request.form.get("advance_note") or "").strip()

    if not name:
        if is_ajax:
            return jsonify({"ok": False, "message": "Rent name is required."}), 400
        flash("Rent name is required.", "error")
        return redirect(url_for("rents.register"))

    rent_id = _auto_rent_id()

    if acq_date_raw:
        try:
            acquisition_datetime = datetime.strptime(acq_date_raw, "%Y-%m-%d")
        except Exception:
            acquisition_datetime = datetime.utcnow()
    else:
        acquisition_datetime = datetime.utcnow()

    rent_due_dt = None
    if rent_due_raw:
        try:
            rent_due_dt = datetime.strptime(rent_due_raw, "%Y-%m-%d")
        except Exception:
            rent_due_dt = None

    billing_amount = _safe_float({"billing_amount": billing_amount_raw}, "billing_amount", 0)
    billing_years = _safe_int(billing_years_raw, 0)

    if billing_mode not in ("monthly", "yearly", "manual"):
        billing_mode = "manual"

    if billing_mode == "monthly":
        cost = max(0.0, round(billing_amount * 12 * max(0, billing_years), 2))
        billing_label = f"Monthly x {billing_years} year(s)"
    elif billing_mode == "yearly":
        cost = max(0.0, round(billing_amount * max(0, billing_years), 2))
        billing_label = f"Yearly x {billing_years} year(s)"
    else:
        cost = max(0.0, round(_safe_float({"cost": cost_raw}, "cost", 0), 2))
        billing_label = "Manual total"
    advance_amount = _safe_float({"amount": advance_amount_raw}, "amount", 0)
    try:
        advance_years = int(advance_years_raw)
    except ValueError:
        advance_years = 0

    doc = {
        "asset_id": rent_id,
        "name": name,
        "category": "Rent",
        "entry_type": "rent",
        "method": "N/A",
        "useful_life_years": 0,
        "acquisition_date": acquisition_datetime,
        "cost": cost,
        "accum_depr": 0.0,
        "status": "Active",
        "notes": notes,
        "rent_place": rent_place,
        "rent_type": rent_type,
        "rent_due_date": rent_due_dt,
        "billing_mode": billing_mode,
        "billing_amount": float(round(billing_amount, 2)),
        "billing_years": billing_years,
        "billing_label": billing_label,
        "advance": {
            "amount": advance_amount,
            "years": advance_years,
            "note": advance_note,
        },
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = rents_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    if is_ajax:
        rent_view = _rent_to_view(doc)
        return jsonify({
            "ok": True,
            "message": f"Rent record {rent_id} created.",
            "rent": rent_view,
        })

    flash(f"Rent record {rent_id} created.", "success")
    return redirect(url_for("rents.register"))


# -------------------------------------------------------------------
# Update status (AJAX: Active / Disposed)
# -------------------------------------------------------------------

@rents_bp.route("/update-status/<rent_id>", methods=["POST"])
def update_status(rent_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    new_status = (request.form.get("status") or "").strip() or (request.json.get("status") if request.is_json else "")

    if new_status not in ["Active", "Disposed"]:
        if is_ajax:
            return jsonify({"ok": False, "message": "Invalid status."}), 400
        flash("Invalid status.", "error")
        return redirect(url_for("rents.register"))

    doc = rents_col.find_one({"asset_id": rent_id, "entry_type": "rent"})
    if not doc:
        if is_ajax:
            return jsonify({"ok": False, "message": "Rent record not found."}), 404
        flash("Rent record not found.", "error")
        return redirect(url_for("rents.register"))

    rents_col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow()}}
    )

    if is_ajax:
        return jsonify({"ok": True, "rent_id": rent_id, "new_status": new_status})

    flash(f"Rent {rent_id} status updated to {new_status}.", "success")
    return redirect(url_for("rents.register"))
