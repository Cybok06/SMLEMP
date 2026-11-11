from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, Response, session, url_for
from datetime import datetime, date, time
from typing import Any, Dict
import io
import csv
from bson import ObjectId

from db import db
from accounting_services import post_accrual

accruals_bp = Blueprint("accruals", __name__, template_folder="../templates")

accruals_col = db["accruals"]


def _require_accounting_role():
    role = (session.get("role") or "").lower()
    if session.get("admin_id") or session.get("executive_id"):
        return True
    return role == "accounting"


def _safe_float(v):
    try:
        s = str(v or "0").replace(",", "").strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _norm_status(v):
    s = (v or "owing")
    return str(s).strip().lower()


def _parse_date_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _effective_dt(doc: dict) -> datetime | None:
    dt = doc.get("date_dt")
    if isinstance(dt, datetime):
        return dt
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day)
    if isinstance(dt, str):
        parsed = _parse_date_iso(dt)
        if parsed:
            return parsed
    created = doc.get("created_at")
    if isinstance(created, datetime):
        return created
    if isinstance(created, date):
        return datetime(created.year, created.month, created.day)
    if isinstance(created, str):
        return _parse_date_iso(created)
    return None


def _base_query() -> dict:
    return {"$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}


def _paginate_url(page: int, per: int) -> str:
    args = request.args.to_dict()
    args.pop("export", None)
    args["page"] = str(page)
    args["per"] = str(per)
    return url_for("accruals.accruals_page", **args)


@accruals_bp.get("/accruals")
def accruals_page():
    if not _require_accounting_role():
        return render_template("accounting/accruals.html", denied=True)

    export = request.args.get("export") == "1"
    qtxt = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "all").strip().lower()
    dfrom = _parse_date_iso(request.args.get("from"))
    dto = _parse_date_iso(request.args.get("to"))
    page = max(1, int(request.args.get("page", 1) or 1))
    per = min(100, max(10, int(request.args.get("per", 25) or 25)))

    query: Dict[str, Any] = {"$and": [_base_query()]}
    if qtxt:
        query["$and"].append({
            "$or": [
                {"vendor": {"$regex": qtxt, "$options": "i"}},
                {"category": {"$regex": qtxt, "$options": "i"}},
            ]
        })

    if status_filter == "owing":
        query["$and"].append({
            "$or": [
                {"status": {"$regex": r"^owing$", "$options": "i"}},
                {"status": {"$exists": False}},
                {"status": ""},
            ]
        })
    elif status_filter == "paid":
        query["$and"].append({"status": {"$regex": r"^paid$", "$options": "i"}})

    if dfrom or dto:
        date_range = {}
        if dfrom:
            date_range["$gte"] = datetime(dfrom.year, dfrom.month, dfrom.day)
        if dto:
            date_range["$lte"] = datetime(dto.year, dto.month, dto.day, 23, 59, 59, 999999)
        query["date_dt"] = date_range

    rows = list(accruals_col.find(query))

    # Python-level effective date sort & filter for stability / older records
    if dfrom or dto:
        filtered = []
        for r in rows:
            eff = _effective_dt(r)
            if eff is None:
                continue
            if dfrom and eff < datetime(dfrom.year, dfrom.month, dfrom.day):
                continue
            if dto and eff > datetime(dto.year, dto.month, dto.day, 23, 59, 59, 999999):
                continue
            filtered.append(r)
        rows = filtered

    rows.sort(key=lambda r: _effective_dt(r) or datetime.min, reverse=True)
    for r in rows:
        eff = _effective_dt(r)
        r["_display_date"] = eff.strftime("%Y-%m-%d") if isinstance(eff, datetime) else ""
        due = r.get("due_date")
        due_dt = due if isinstance(due, datetime) else _parse_date_iso(due) if isinstance(due, str) else None
        r["_display_due"] = due_dt.strftime("%Y-%m-%d") if isinstance(due_dt, datetime) else ""

    if export:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Incurred Date", "Category", "Vendor", "Amount", "Due Date", "Status"])
        for r in rows:
            dt = _effective_dt(r)
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else ""
            due = r.get("due_date")
            due_dt = due if isinstance(due, datetime) else _parse_date_iso(due) if isinstance(due, str) else None
            due_str = due_dt.strftime("%Y-%m-%d") if isinstance(due_dt, datetime) else ""
            w.writerow([
                dt_str,
                r.get("category", ""),
                r.get("vendor", ""),
                f"{_safe_float(r.get('amount')):0.2f}",
                due_str,
                _norm_status(r.get("status")),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="accruals.csv"'},
        )

    # Totals should be global (not filtered), excluding deleted rows
    all_rows = list(accruals_col.find(_base_query()))
    outstanding_total = sum(
        _safe_float(r.get("amount"))
        for r in all_rows if _norm_status(r.get("status")) == "owing"
    )
    paid_total = sum(
        _safe_float(r.get("amount"))
        for r in all_rows if _norm_status(r.get("status")) == "paid"
    )
    all_total = sum(_safe_float(r.get("amount")) for r in all_rows)
    count_owing = sum(1 for r in all_rows if _norm_status(r.get("status")) == "owing")
    count_paid = sum(1 for r in all_rows if _norm_status(r.get("status")) == "paid")

    total = len(rows)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    start = (page - 1) * per
    end = start + per
    page_rows = rows[start:end]
    pager = {
        "total": total,
        "page": page,
        "pages": pages,
        "prev_url": _paginate_url(page - 1, per) if page > 1 else None,
        "next_url": _paginate_url(page + 1, per) if page < pages else None,
    }
    export_args = request.args.to_dict()
    export_args["export"] = "1"
    export_url = url_for("accruals.accruals_page", **export_args)

    return render_template(
        "accounting/accruals.html",
        rows=page_rows,
        outstanding_total=outstanding_total,
        paid_total=paid_total,
        all_total=all_total,
        count_owing=count_owing,
        count_paid=count_paid,
        pager=pager,
        q=qtxt,
        status_filter=status_filter,
        date_from=request.args.get("from") or "",
        date_to=request.args.get("to") or "",
        per=per,
        export_url=export_url,
        denied=False,
    )


@accruals_bp.post("/accruals/create")
def create_accrual():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401

    date_str = (request.form.get("date") or "").strip()
    due_str = (request.form.get("due_date") or "").strip()
    date_dt = _parse_date_iso(date_str)
    due_dt = _parse_date_iso(due_str)

    payload = {
        "date_dt": date_dt or date_str,
        "category": request.form.get("category"),
        "vendor": request.form.get("vendor"),
        "amount": request.form.get("amount"),
        "due_date": due_dt or due_str,
        "status": "owing",
        "created_by": session.get("user_id") or session.get("admin_id") or session.get("executive_id"),
    }
    result = post_accrual(payload)
    if not result.get("ok"):
        return jsonify(ok=False, message=result.get("message") or "Failed"), 400
    accrual_id = result.get("id")
    if accrual_id:
        try:
            oid = ObjectId(accrual_id)
        except Exception:
            oid = None
        if oid:
            doc = accruals_col.find_one({"_id": oid})
            if doc:
                update = {}
                if _norm_status(doc.get("status")) != "owing":
                    update["status"] = "owing"
                if not isinstance(doc.get("date_dt"), datetime):
                    update["date_dt"] = date_dt or datetime.utcnow()
                if not isinstance(doc.get("created_at"), datetime):
                    update["created_at"] = datetime.utcnow()
                if update:
                    update["updated_at"] = datetime.utcnow()
                    accruals_col.update_one({"_id": oid}, {"$set": update})
    return jsonify(ok=True, id=accrual_id)


@accruals_bp.post("/accruals/<accrual_id>/mark-paid")
def mark_paid(accrual_id: str):
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    try:
        oid = ObjectId(accrual_id)
    except Exception:
        return jsonify(ok=False, message="Invalid accrual id."), 400

    linked_payment_id = request.form.get("linked_payment_id") or None
    accruals_col.update_one(
        {"_id": oid, "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]},
        {"$set": {
            "status": "paid",
            "linked_payment_id": linked_payment_id,
            "updated_at": datetime.utcnow(),
        }},
    )
    return jsonify(ok=True)


@accruals_bp.get("/accruals/summary")
def accruals_summary():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    rows = list(accruals_col.find(_base_query()))
    outstanding_total = sum(
        _safe_float(r.get("amount"))
        for r in rows if _norm_status(r.get("status")) == "owing"
    )
    paid_total = sum(
        _safe_float(r.get("amount"))
        for r in rows if _norm_status(r.get("status")) == "paid"
    )
    all_total = sum(_safe_float(r.get("amount")) for r in rows)
    count_owing = sum(1 for r in rows if _norm_status(r.get("status")) == "owing")
    count_paid = sum(1 for r in rows if _norm_status(r.get("status")) == "paid")
    return jsonify(
        ok=True,
        outstanding_total=round(outstanding_total, 2),
        paid_total=round(paid_total, 2),
        all_total=round(all_total, 2),
        count_owing=count_owing,
        count_paid=count_paid,
    )


@accruals_bp.post("/accruals/<accrual_id>/delete")
def delete_accrual(accrual_id: str):
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    try:
        oid = ObjectId(accrual_id)
    except Exception:
        return jsonify(ok=False, message="Invalid accrual id."), 400
    accruals_col.update_one(
        {"_id": oid},
        {"$set": {"deleted_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )
    return jsonify(ok=True)
