from __future__ import annotations

from datetime import datetime
from io import BytesIO

from flask import Blueprint, Response, jsonify, redirect, render_template, request, send_file, url_for
from pymongo.errors import NetworkTimeout, ServerSelectionTimeoutError

from login import get_current_identity, role_required
from services.activity_audit import log_activity
from services.customer_liability_service import (
    build_liability_csv,
    build_liability_excel,
    build_liability_pdf,
    get_liability_detail,
    get_liability_register,
    get_liability_settings,
    get_liability_summary,
    normalize_filters,
    resolve_liability,
)

executive_customer_liabilities_bp = Blueprint(
    "executive_customer_liabilities",
    __name__,
    url_prefix="/executive/customer-liabilities",
)


@executive_customer_liabilities_bp.get("/")
@role_required("executive", "accounting", "admin")
def page():
    filters = normalize_filters(request.args)
    identity = get_current_identity()
    register_only = str(request.args.get("view") or "").strip().lower() == "register"
    return render_template(
        "executive_customer_liabilities.html",
        page_title="Customer Liability & Fulfilment Control",
        page_subtitle="Real-time customer funds held against products not yet delivered.",
        summary=None,
        filters=filters,
        settings=get_liability_settings(),
        identity=identity,
        today=datetime.utcnow().strftime("%Y-%m-%d"),
        register_only=register_only,
    )


@executive_customer_liabilities_bp.get("/register")
@role_required("executive", "accounting", "admin")
def register_page():
    query = request.args.to_dict(flat=True)
    query["view"] = "register"
    return redirect(url_for("executive_customer_liabilities.page", **query))


@executive_customer_liabilities_bp.get("/api/summary")
@role_required("executive", "accounting", "admin")
def api_summary():
    filters = normalize_filters(request.args)
    return jsonify(ok=True, data=get_liability_summary(filters))


@executive_customer_liabilities_bp.get("/api/register")
@role_required("executive", "accounting", "admin")
def api_register():
    filters = normalize_filters(request.args)
    try:
        return jsonify(ok=True, data=get_liability_register(filters))
    except (NetworkTimeout, ServerSelectionTimeoutError):
        return jsonify(
            ok=False,
            error="The database took too long to load the customer register. Please retry.",
            code="database_timeout",
        ), 503


@executive_customer_liabilities_bp.get("/api/detail/<customer_id>/<int:product_index>")
@role_required("executive", "accounting", "admin")
def api_detail(customer_id: str, product_index: int):
    as_of_date = (request.args.get("as_of_date") or datetime.utcnow().strftime("%Y-%m-%d")).strip()
    payload = get_liability_detail(customer_id, product_index, as_of_date)
    if payload.get("error"):
        return jsonify(ok=False, error=payload["error"]), 404
    return jsonify(ok=True, data=payload)


@executive_customer_liabilities_bp.post("/api/detail/<customer_id>/<int:product_index>/resolve")
@role_required("executive", "accounting", "admin")
def api_resolve(customer_id: str, product_index: int):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    resolution = str(payload.get("resolution") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()
    if resolution not in {"closed", "delivered"}:
        return jsonify(ok=False, error="Resolution must be closed or delivered."), 400
    result = resolve_liability(
        customer_id,
        product_index,
        resolution,
        get_current_identity(),
        reason,
    )
    if result.get("error"):
        return jsonify(ok=False, error=result["error"]), 404
    log_activity(
        action=f"liability.marked_{resolution}",
        action_label=f"Marked Customer Liability as {resolution.title()}",
        entity_type="customer_purchase",
        entity_id=f"{customer_id}:{product_index}",
        meta={
            "customer": result.get("customer_name"),
            "reference": f"{customer_id}:{product_index}",
            "status": resolution,
            "note": reason,
        },
    )
    return jsonify(ok=True, data=result)


@executive_customer_liabilities_bp.get("/export.csv")
@role_required("executive", "accounting", "admin")
def export_csv():
    filters = normalize_filters(request.args)
    content = build_liability_csv(filters)
    log_activity(
        action="liability.exported",
        action_label="Exported Customer Liability CSV",
        entity_type="report",
        entity_id=None,
        meta={"format": "csv", "as_of_date": filters["as_of_date"]},
    )
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=customer_liabilities_{filters['as_of_date']}.csv"},
    )


@executive_customer_liabilities_bp.get("/export.xlsx")
@role_required("executive", "accounting", "admin")
def export_xlsx():
    filters = normalize_filters(request.args)
    content = build_liability_excel(filters)
    log_activity(
        action="liability.exported",
        action_label="Exported Customer Liability Excel",
        entity_type="report",
        entity_id=None,
        meta={"format": "xlsx", "as_of_date": filters["as_of_date"]},
    )
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"customer_liabilities_{filters['as_of_date']}.xlsx",
    )


@executive_customer_liabilities_bp.get("/export.pdf")
@role_required("executive", "accounting", "admin")
def export_pdf():
    filters = normalize_filters(request.args)
    content = build_liability_pdf(filters)
    log_activity(
        action="liability.exported",
        action_label="Exported Customer Liability PDF",
        entity_type="report",
        entity_id=None,
        meta={"format": "pdf", "as_of_date": filters["as_of_date"]},
    )
    return send_file(
        BytesIO(content),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"customer_liabilities_{filters['as_of_date']}.pdf",
    )
