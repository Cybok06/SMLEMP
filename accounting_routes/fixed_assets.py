#!/usr/bin/env python3
"""
Fixed Assets Register routes for TRUEtype Services.

When registered in app.py as:
    app.register_blueprint(fixed_assets_bp, url_prefix="/accounting/fixed-assets")

Routes become:
    /accounting/fixed-assets/                        -> register()
    /accounting/fixed-assets/export                  -> export_assets()
    /accounting/fixed-assets/add                     -> add_asset_form()
    /accounting/fixed-assets/compute-depreciation    -> compute_depreciation()
    /accounting/fixed-assets/post-depreciation       -> post_depreciation()
    /accounting/fixed-assets/dispose/<asset_id>      -> dispose_asset()
    /accounting/fixed-assets/update-status/<asset_id>-> update_status()
"""

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, Response, jsonify
)
from datetime import datetime, date, timedelta
from db import db
import csv
import io
from bson import ObjectId
from accounting_services import post_withdrawal

fixed_assets_col = db["fixed_assets"]
bank_accounts_col = db["bank_accounts"]

# NOTE: no url_prefix here – it will be applied in app.py
fixed_assets_bp = Blueprint(
    "fixed_assets",
    __name__,
)

# -------------------------------------------------------------------
# Template filter: money (thousand separator, 2dp)
# -------------------------------------------------------------------

@fixed_assets_bp.app_template_filter("money")
def money_filter(value):
    """Format a numeric value with thousand separator and 2 decimals."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _safe_float(doc, key, default=0.0):
    try:
        return float(doc.get(key, default) or 0)
    except (TypeError, ValueError):
        return float(default)


def _parse_date(value):
    """
    For display/formatting only – returns datetime.date or None.
    Never write this back to Mongo (use datetime.datetime when saving).
    """
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


def _format_date(d):
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return ""


def _auto_asset_id():
    """
    Generate next asset_id like FA-00001.
    Looks at existing records and increments highest numeric part.
    """
    last = fixed_assets_col.find_one(
        {"asset_id": {"$regex": r"^FA-\d+$"}},
        sort=[("asset_id", -1)]
    )
    if not last:
        return "FA-00001"
    try:
        num = int(str(last["asset_id"]).split("-")[1])
    except Exception:
        num = 0
    return f"FA-{num + 1:05d}"


def _compute_net_book_value(asset):
    # For RENT entries, we don't do NBV logic – just show 0.00
    if (asset.get("entry_type") or "asset").lower() == "rent":
        return 0.0

    cost = _safe_float(asset, "cost", 0)
    accum = _safe_float(asset, "accum_depr", 0)
    nbv = cost - accum
    return nbv if nbv > 0 else 0.0


def _compute_progress(start_date, end_date, today=None):
    """
    Generic progress % between two dates, clamped 0–100.
    Returns (percent, label_str) or (None, "") if not applicable.
    """
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


def _asset_to_view(doc):
    """
    Convert Mongo document to view dict used by template and JSON.
    Includes:
      - formatted money strings
      - life span progress for assets
      - rent period progress for rent entries
    """
    entry_type = (doc.get("entry_type") or "asset").lower()
    status = doc.get("status", "Active")

    cost = _safe_float(doc, "cost", 0)
    accum = _safe_float(doc, "accum_depr", 0)
    nbv = _compute_net_book_value(doc)

    acq_date = _parse_date(doc.get("acquisition_date"))
    rent_due = _parse_date(doc.get("rent_due_date"))

    # Life span progress (assets only)
    life_progress_pct = None
    life_progress_label = ""
    useful_life_years = int(doc.get("useful_life_years") or 0)
    if entry_type != "rent" and acq_date and useful_life_years > 0:
        life_end = acq_date + timedelta(days=useful_life_years * 365)
        life_progress_pct, life_progress_label = _compute_progress(acq_date, life_end)

    # Rent period progress (rent only)
    rent_progress_pct = None
    rent_progress_label = ""
    if entry_type == "rent" and acq_date and rent_due:
        rent_progress_pct, rent_progress_label = _compute_progress(acq_date, rent_due)

    advance = doc.get("advance") or {}
    advance_amount = _safe_float(advance, "amount", 0)
    advance_years = int(advance.get("years") or 0)
    advance_note = advance.get("note", "")

    return {
        "_id": str(doc.get("_id")),
        "asset_id": doc.get("asset_id"),
        "name": doc.get("name"),
        "category": doc.get("category"),
        "entry_type": entry_type,
        "method": doc.get("method", "SL"),
        "useful_life_years": useful_life_years,
        "status": status,

        "cost": cost,
        "accum_depr": 0.0 if entry_type == "rent" else accum,
        "net_book_value": 0.0 if entry_type == "rent" else nbv,

        "cost_display": f"{cost:,.2f}",
        "accum_depr_display": "-" if entry_type == "rent" else f"{accum:,.2f}",
        "nbv_display": "-" if entry_type == "rent" else f"{nbv:,.2f}",

        "acquisition_date_str": _format_date(acq_date),

        "rent_place": doc.get("rent_place"),
        "rent_type": doc.get("rent_type"),
        "rent_due_date_str": _format_date(rent_due),

        "advance_amount": advance_amount,
        "advance_years": advance_years,
        "advance_note": advance_note,

        "life_progress_pct": life_progress_pct,
        "life_progress_label": life_progress_label,
        "rent_progress_pct": rent_progress_pct,
        "rent_progress_label": rent_progress_label,
    }


def _load_payment_accounts():
    out = {"bank": [], "cash": [], "momo": []}
    cursor = bank_accounts_col.find(
        {},
        {"_id": 1, "account_type": 1, "account_name": 1, "bank_name": 1, "account_no": 1, "account_number": 1},
    ).sort("bank_name", 1)
    for doc in cursor:
        oid = doc.get("_id")
        if not isinstance(oid, ObjectId):
            continue
        raw_type = (doc.get("account_type") or "bank").strip().lower()
        if raw_type == "mobile_money":
            key = "momo"
            label_type = "MOMO"
        elif raw_type == "cash":
            key = "cash"
            label_type = "Cash"
        else:
            key = "bank"
            label_type = "Bank"
        acct_name = (doc.get("account_name") or "").strip()
        bank_name = (doc.get("bank_name") or "").strip()
        raw_no = (doc.get("account_no") or doc.get("account_number") or "").strip()
        last4 = raw_no[-4:] if len(raw_no) >= 4 else raw_no
        title = acct_name or bank_name or f"{label_type} Account"
        number_hint = f"...{last4}" if last4 else ""
        if bank_name and bank_name != title and number_hint:
            label = f"{title} ({bank_name} {number_hint})"
        elif bank_name and bank_name != title:
            label = f"{title} ({bank_name})"
        elif number_hint:
            label = f"{title} ({number_hint})"
        else:
            label = title
        out[key].append({"id": str(oid), "label": label, "type": key})
    return out


# -------------------------------------------------------------------
# Main register view
# -------------------------------------------------------------------

@fixed_assets_bp.route("/", methods=["GET"])
def register():
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    status = (request.args.get("status") or "").strip()

    query = {"entry_type": {"$ne": "rent"}}
    if q:
        query["$or"] = [
            {"asset_id": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]
    if category:
        query["category"] = category
    if status:
        query["status"] = status

    docs = list(
        fixed_assets_col.find(query).sort("acquisition_date", -1)
    )

    assets = [_asset_to_view(doc) for doc in docs]

    categories = sorted([c for c in fixed_assets_col.distinct("category", query) if c])
    statuses = ["Active", "Fully Depreciated", "Disposed"]

    return render_template(
        "accounting/fixed_assets_register.html",
        assets=assets,
        categories=categories,
        statuses=statuses,
        currency_symbol="GHS ",
        payment_accounts=_load_payment_accounts(),
    )


# -------------------------------------------------------------------
# Export CSV
# -------------------------------------------------------------------

@fixed_assets_bp.route("/export", methods=["GET"])
def export_assets():
    docs = list(
        fixed_assets_col.find({"entry_type": {"$ne": "rent"}}).sort("acquisition_date", -1)
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Asset ID",
        "Name",
        "Category",
        "Acquisition Date",
        "Cost",
        "Accumulated Depreciation",
        "Net Book Value",
        "Method",
        "Useful Life (Years)",
        "Status",
        "Notes",
    ])

    for doc in docs:
        cost = _safe_float(doc, "cost", 0)
        accum = _safe_float(doc, "accum_depr", 0)
        nbv = _compute_net_book_value(doc)

        writer.writerow([
            doc.get("asset_id", ""),
            doc.get("name", ""),
            doc.get("category", ""),
            _format_date(_parse_date(doc.get("acquisition_date"))),
            f"{cost:,.2f}",
            f"{accum:,.2f}",
            f"{nbv:,.2f}",
            doc.get("method", "SL"),
            doc.get("useful_life_years", 0),
            doc.get("status", "Active"),
            doc.get("notes", ""),
        ])

    output.seek(0)
    filename = f"fixed_assets_{date.today().isoformat()}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


# -------------------------------------------------------------------
# Add Asset – supports normal POST and AJAX JSON
# -------------------------------------------------------------------

@fixed_assets_bp.route("/add", methods=["POST"])
def add_asset_form():
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Type selector: "asset"
    entry_type = (request.form.get("entry_type") or "asset").strip().lower()
    if entry_type == "rent":
        if is_ajax:
            return jsonify({"ok": False, "message": "Use the Rent Register to add rent records."}), 400
        flash("Use the Rent Register to add rent records.", "error")
        return redirect(url_for("fixed_assets.register"))
    entry_type = "asset"

    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip()
    method = (request.form.get("method") or "SL").strip()
    life_years = request.form.get("useful_life_years") or "0"
    cost_raw = request.form.get("cost") or "0"
    acq_date_raw = request.form.get("acquisition_date") or ""
    notes = (request.form.get("notes") or "").strip()
    payment_method = (request.form.get("payment_method") or "").strip().lower()
    payment_account_id = (request.form.get("payment_account_id") or "").strip()

    if not name:
        if is_ajax:
            return jsonify({"ok": False, "message": "Asset name is required."}), 400
        flash("Asset name is required.", "error")
        return redirect(url_for("fixed_assets.register"))

    # Auto ID
    asset_id = _auto_asset_id()

    # Acquisition date (for rent, this is Date Rented)
    if acq_date_raw:
        try:
            acquisition_datetime = datetime.strptime(acq_date_raw, "%Y-%m-%d")
        except Exception:
            acquisition_datetime = datetime.utcnow()
    else:
        acquisition_datetime = datetime.utcnow()

    # Amounts
    cost = _safe_float({"cost": cost_raw}, "cost", 0)
    try:
        useful_life_years = int(life_years)
    except ValueError:
        useful_life_years = 0

    # Ensure asset entries always have a class, defaulting to Land and Building
    if not category:
        category = "Land and Building"

    doc = {
        "asset_id": asset_id,
        "name": name,
        "category": category,
        "entry_type": "asset",

        # Asset-related fields (for rent we still store them, but depreciation will ignore)
        "method": method or "SL",
        "useful_life_years": useful_life_years,
        "acquisition_date": acquisition_datetime,
        "cost": cost,
        "accum_depr": 0.0,
        "status": "Active",
        "notes": notes,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    doc["advance"] = {
        "amount": 0.0,
        "years": 0,
        "note": "",
    }

    method_map = {
        "bank": {"label": "Bank", "account_type": "bank"},
        "cash": {"label": "Cash", "account_type": "cash"},
        "momo": {"label": "MOMO", "account_type": "mobile_money"},
    }
    use_funding = bool(payment_method or payment_account_id)
    selected_account = None
    selected_account_type = ""
    if use_funding:
        if payment_method not in method_map:
            msg = "Funding method must be Bank, Cash or MOMO."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))
        if not payment_account_id:
            msg = "Please select a funding account."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))
        if cost <= 0:
            msg = "Cost must be greater than zero to auto-withdraw from an account."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))
        try:
            account_oid = ObjectId(payment_account_id)
        except Exception:
            msg = "Invalid funding account selected."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))
        selected_account = bank_accounts_col.find_one({"_id": account_oid})
        if not selected_account:
            msg = "Funding account not found."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 404
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))
        selected_account_type = (selected_account.get("account_type") or "bank").strip().lower()
        if selected_account_type not in ("bank", "cash", "mobile_money"):
            selected_account_type = "bank"
        if selected_account_type != method_map[payment_method]["account_type"]:
            msg = "Selected account does not match funding method."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))

    result = fixed_assets_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    if use_funding:
        withdraw_result = post_withdrawal({
            "amount": cost,
            "account_type": selected_account_type,
            "account_id": payment_account_id,
            "purpose": "asset",
            "purpose_note": notes or f"Asset funding for {name}",
            "asset_category": category,
            "asset_description": notes or name,
            "date_dt": acquisition_datetime,
            "created_by": None,
        })
        if not withdraw_result.get("ok"):
            fixed_assets_col.delete_one({"_id": result.inserted_id})
            msg = withdraw_result.get("message") or "Failed to auto-withdraw from selected funding account."
            if is_ajax:
                return jsonify({"ok": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("fixed_assets.register"))

        fixed_assets_col.update_one(
            {"_id": result.inserted_id},
            {"$set": {
                "funding_method": method_map[payment_method]["label"],
                "funding_account_id": payment_account_id,
                "funding_account_type": selected_account_type,
                "funding_withdrawal_id": withdraw_result.get("withdrawal_id"),
                "updated_at": datetime.utcnow(),
            }},
        )
        doc["funding_method"] = method_map[payment_method]["label"]
        doc["funding_account_id"] = payment_account_id
        doc["funding_account_type"] = selected_account_type
        doc["funding_withdrawal_id"] = withdraw_result.get("withdrawal_id")

    if is_ajax:
        # Return JSON with a ready-to-render view dict
        asset_view = _asset_to_view(doc)
        return jsonify({
            "ok": True,
            "message": f"Record {asset_id} (Asset) created.",
            "asset": asset_view,
        })

    flash(f"Record {asset_id} (Asset) created.", "success")
    return redirect(url_for("fixed_assets.register"))


# -------------------------------------------------------------------
# Compute & Post Depreciation
# -------------------------------------------------------------------

def _monthly_depreciation_amount(doc):
    """
    Very simple depreciation logic:
      - Straight Line: cost / (useful_life_years * 12)
      - DB: 2 * SL rate * remaining NBV
    Assumes zero salvage value.

    RENT entries are ignored (no depreciation).
    """
    if (doc.get("entry_type") or "asset").lower() == "rent":
        return 0.0

    method = (doc.get("method") or "SL").upper()
    useful_life_years = int(doc.get("useful_life_years") or 0)
    if useful_life_years <= 0:
        return 0.0

    cost = _safe_float(doc, "cost", 0)
    accum = _safe_float(doc, "accum_depr", 0)
    nbv = cost - accum
    if nbv <= 0:
        return 0.0

    months = useful_life_years * 12

    if method == "DB":
        annual_rate = 2.0 / useful_life_years
        monthly_rate = annual_rate / 12.0
        dep = nbv * monthly_rate
    else:
        dep = cost / months

    if dep > nbv:
        dep = nbv
    return dep


@fixed_assets_bp.route("/compute-depreciation", methods=["POST"])
def compute_depreciation():
    active = list(
        fixed_assets_col.find({
            "status": {"$in": ["Active", "Fully Depreciated"]},
            "entry_type": {"$ne": "rent"},
        })
    )

    count_eligible = 0
    total_dep = 0.0

    for doc in active:
        dep = _monthly_depreciation_amount(doc)
        if dep > 0:
            count_eligible += 1
            total_dep += dep

    flash(
        f"Computed depreciation for {count_eligible} asset(s). "
        f"Estimated total for this month: GHS {total_dep:,.2f}.",
        "info",
    )
    return redirect(url_for("fixed_assets.register"))


@fixed_assets_bp.route("/post-depreciation", methods=["POST"])
def post_depreciation():
    active = list(
        fixed_assets_col.find({
            "status": {"$in": ["Active", "Fully Depreciated"]},
            "entry_type": {"$ne": "rent"},
        })
    )

    updated_count = 0
    for doc in active:
        dep = _monthly_depreciation_amount(doc)
        if dep <= 0:
            continue
        new_accum = _safe_float(doc, "accum_depr", 0) + dep
        cost = _safe_float(doc, "cost", 0)

        status = doc.get("status", "Active")
        if new_accum >= cost:
            new_accum = cost
            status = "Fully Depreciated"

        fixed_assets_col.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "accum_depr": new_accum,
                    "status": status,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        updated_count += 1

    flash(f"Posted monthly depreciation for {updated_count} asset(s).", "success")
    return redirect(url_for("fixed_assets.register"))


# -------------------------------------------------------------------
# Dispose asset (legacy non-AJAX)
# -------------------------------------------------------------------

@fixed_assets_bp.route("/dispose/<asset_id>", methods=["POST"])
def dispose_asset(asset_id):
    doc = fixed_assets_col.find_one({"asset_id": asset_id, "entry_type": {"$ne": "rent"}})
    if not doc:
        flash("Asset not found.", "error")
        return redirect(url_for("fixed_assets.register"))

    fixed_assets_col.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "Disposed",
                "updated_at": datetime.utcnow(),
            }
        },
    )
    flash(f"Asset {asset_id} marked as disposed.", "success")
    return redirect(url_for("fixed_assets.register"))


# -------------------------------------------------------------------
# Update status (AJAX: Active / Disposed / Fully Depreciated)
# -------------------------------------------------------------------

@fixed_assets_bp.route("/update-status/<asset_id>", methods=["POST"])
def update_status(asset_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    new_status = (request.form.get("status") or "").strip() or (request.json.get("status") if request.is_json else "")

    if new_status not in ["Active", "Disposed", "Fully Depreciated"]:
        if is_ajax:
            return jsonify({"ok": False, "message": "Invalid status."}), 400
        flash("Invalid status.", "error")
        return redirect(url_for("fixed_assets.register"))

    doc = fixed_assets_col.find_one({"asset_id": asset_id, "entry_type": {"$ne": "rent"}})
    if not doc:
        if is_ajax:
            return jsonify({"ok": False, "message": "Asset not found."}), 404
        flash("Asset not found.", "error")
        return redirect(url_for("fixed_assets.register"))

    fixed_assets_col.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow()}}
    )

    if is_ajax:
        return jsonify({"ok": True, "asset_id": asset_id, "new_status": new_status})

    flash(f"Asset {asset_id} status updated to {new_status}.", "success")
    return redirect(url_for("fixed_assets.register"))
