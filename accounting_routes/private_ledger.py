from __future__ import annotations

from flask import Blueprint, render_template, request, url_for, Response, jsonify, session, redirect
from datetime import datetime, timedelta
import io
import csv
import re
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash

from db import db
from accounting_services import post_withdrawal, post_goods_drawn

private_ledger_bp = Blueprint("private_ledger", __name__, template_folder="../templates")

private_ledger_col = db["private_ledger_entries"]
bank_accounts_col = db["bank_accounts"]
inventory_col = db["inventory"]
expenses_col = db["expenses"]
private_ledger_settings_col = db["private_ledger_settings"]
private_ledger_people_col = db["private_ledger_people"]

DEFAULT_PASSCODE = "503860"


def _require_accounting_role():
    role = (session.get("role") or "").lower()
    if session.get("admin_id") or session.get("executive_id"):
        return True
    return role == "accounting"


def _parse_date(val: str | None):
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d")
    except Exception:
        return None


def _principal_id() -> str:
    return (
        str(session.get("user_id") or session.get("admin_id") or session.get("executive_id") or "")
        or (request.remote_addr or "unknown")
    )


def _ensure_passcode_doc():
    doc = private_ledger_settings_col.find_one({"key": "passcode"})
    if doc:
        return doc
    now = datetime.utcnow()
    private_ledger_settings_col.insert_one({
        "key": "passcode",
        "value_hash": generate_password_hash(DEFAULT_PASSCODE),
        "updated_at": now,
    })
    return private_ledger_settings_col.find_one({"key": "passcode"})


def _get_lockout(principal: str):
    return private_ledger_settings_col.find_one({"key": f"lockout:{principal}"}) or {}


def _set_lockout(principal: str, failures: int, locked_until: datetime | None):
    private_ledger_settings_col.update_one(
        {"key": f"lockout:{principal}"},
        {"$set": {
            "principal": principal,
            "failures": failures,
            "locked_until": locked_until,
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


def _upsert_person(kind: str, name: str):
    k = (kind or "").strip().lower()
    n = (name or "").strip()
    if not k or not n:
        return
    private_ledger_people_col.update_one(
        {"kind": k, "name": n},
        {"$setOnInsert": {"kind": k, "name": n, "created_at": datetime.utcnow()}},
        upsert=True,
    )


@private_ledger_bp.get("/private-ledger")
def private_ledger():
    if not _require_accounting_role():
        return render_template("accounting/private_ledger.html", denied=True)

    passcode_doc = _ensure_passcode_doc()
    principal = _principal_id()
    lock = _get_lockout(principal)
    locked_until = lock.get("locked_until")
    now = datetime.utcnow()
    lockout_message = None
    locked = False
    locked_until_epoch_ms = None
    attempts_left = None
    if isinstance(locked_until, datetime) and locked_until > now:
        remaining = int((locked_until - now).total_seconds())
        mins = max(1, int((remaining + 59) // 60))
        lockout_message = f"Too many attempts. Try again in {mins} minute(s)."
        locked = True
        locked_until_epoch_ms = int(locked_until.timestamp() * 1000)

    failures = int(lock.get("failures") or 0)
    if not locked:
        attempts_left = max(0, 3 - failures)

    unlocked = bool(session.get("private_ledger_unlocked"))
    if not unlocked:
        return render_template(
            "accounting/private_ledger.html",
            denied=False,
            passcode_required=True,
            lockout_message=lockout_message,
            locked=locked,
            locked_until_epoch_ms=locked_until_epoch_ms,
            attempts_left=attempts_left,
        )

    start_str = (request.args.get("from") or "").strip()
    end_str = (request.args.get("to") or "").strip()
    entry_type = (request.args.get("entry_type") or "").strip()
    source_type = (request.args.get("source_type") or "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = 10
    export = request.args.get("export") == "1"

    start_dt = _parse_date(start_str)
    end_dt = _parse_date(end_str)
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    q = {"status": "posted"}
    if start_dt or end_dt:
        q["date_dt"] = {}
        if start_dt:
            q["date_dt"]["$gte"] = start_dt
        if end_dt:
            q["date_dt"]["$lte"] = end_dt
    if entry_type in ("cash_drawing", "goods_drawn", "owner_contribution", "salary"):
        q["entry_type"] = entry_type
    if source_type in ("cash", "bank", "momo", "mobile_money"):
        q["source_account_type"] = "mobile_money" if source_type == "momo" else source_type

    total_count = private_ledger_col.count_documents(q)
    rows = list(
        private_ledger_col.find(q)
        .sort("date_dt", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    all_rows = list(private_ledger_col.find(q).sort("date_dt", -1))

    total_drawings = sum(
        float(r.get("amount", 0) or 0) for r in all_rows if r.get("entry_type") == "cash_drawing"
    )
    total_goods_drawn = sum(
        float(r.get("amount", 0) or 0) for r in all_rows if r.get("entry_type") == "goods_drawn"
    )
    total_salary = sum(
        float(r.get("amount", 0) or 0) for r in all_rows if r.get("entry_type") == "salary"
    )

    if export:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Entry Type", "Source", "Amount", "Memo", "Recorded By", "Authorized By"])
        for r in all_rows:
            dt = r.get("date_dt")
            dt_str = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else ""
            w.writerow([
                dt_str,
                r.get("entry_type", ""),
                r.get("source_account_type", ""),
                f"{float(r.get('amount', 0) or 0):0.2f}",
                r.get("purpose_text", ""),
                r.get("recorded_by", ""),
                r.get("authorized_by", ""),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="private_ledger.csv"'},
        )

    bank_accounts = list(
        bank_accounts_col.find(
            {},
            {"_id": 1, "bank_name": 1, "account_name": 1, "account_type": 1, "account_no": 1},
        ).sort("bank_name", 1)
    )
    accounts_for_select = []
    for a in bank_accounts:
        aid = a.get("_id")
        if not isinstance(aid, ObjectId):
            continue
        label = a.get("bank_name") or a.get("account_name") or "Account"
        if a.get("account_name") and a.get("bank_name"):
            label = f"{a.get('bank_name')} - {a.get('account_name')}"
        accounts_for_select.append({
            "id": str(aid),
            "label": label,
            "account_type": (a.get("account_type") or "bank").lower(),
        })

    inventory_items = list(
        inventory_col.find({}, {"_id": 1, "name": 1, "qty": 1, "cost_price": 1})
        .sort("name", 1)
        .limit(2000)
    )

    recorded_by_list = list(
        private_ledger_people_col.find({"kind": "recorded_by"}, {"name": 1})
        .sort("name", 1)
    )
    authorized_by_list = list(
        private_ledger_people_col.find({"kind": "authorized_by"}, {"name": 1})
        .sort("name", 1)
    )

    auth_pipeline = [
        {"$match": {**q, "authorized_by": {"$exists": True, "$ne": ""}}},
        {"$group": {"_id": "$authorized_by", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"total": -1}},
        {"$limit": 12},
    ]
    authorized_by_breakdown = []
    for row in private_ledger_col.aggregate(auth_pipeline):
        authorized_by_breakdown.append({
            "name": row.get("_id") or "Unknown",
            "total": float(row.get("total") or 0),
            "count": int(row.get("count") or 0),
        })

    page_count = max(1, (total_count + per_page - 1) // per_page)

    return render_template(
        "accounting/private_ledger.html",
        rows=rows,
        total_drawings=total_drawings,
        total_goods_drawn=total_goods_drawn,
        total_salary=total_salary,
        total_entries=total_count,
        start_str=start_str,
        end_str=end_str,
        entry_type=entry_type,
        source_type=source_type,
        page=page,
        per_page=per_page,
        page_count=page_count,
        accounts=accounts_for_select,
        inventory_items=inventory_items,
        recorded_by_list=recorded_by_list,
        authorized_by_list=authorized_by_list,
        authorized_by_breakdown=authorized_by_breakdown,
        denied=False,
        passcode_required=False,
    )


@private_ledger_bp.post("/private-ledger/unlock")
def private_ledger_unlock():
    if not _require_accounting_role():
        return render_template("accounting/private_ledger.html", denied=True)

    passcode_doc = _ensure_passcode_doc()
    principal = _principal_id()
    lock = _get_lockout(principal)
    locked_until = lock.get("locked_until")
    now = datetime.utcnow()
    if isinstance(locked_until, datetime) and locked_until > now:
        remaining = int((locked_until - now).total_seconds())
        mins = max(1, int((remaining + 59) // 60))
        return render_template(
            "accounting/private_ledger.html",
            denied=False,
            passcode_required=True,
            lockout_message=f"Too many attempts. Try again in {mins} minute(s).",
            locked=True,
            locked_until_epoch_ms=int(locked_until.timestamp() * 1000),
            attempts_left=0,
        )

    passcode = (request.form.get("passcode") or "").strip()
    if not passcode or not check_password_hash(passcode_doc.get("value_hash") or "", passcode):
        failures = int(lock.get("failures") or 0) + 1
        if failures <= 3:
            _set_lockout(principal, failures, None)
            attempts_left = max(0, 3 - failures)
            return render_template(
                "accounting/private_ledger.html",
                denied=False,
                passcode_required=True,
                lockout_message=f"Incorrect passcode. Attempts left: {attempts_left}.",
                locked=False,
                attempts_left=attempts_left,
            )

        lock_round = max(0, failures - 3)
        lock_minutes = 3 * (2 ** (lock_round - 1)) if lock_round > 0 else 3
        locked_until = now + timedelta(minutes=lock_minutes)
        _set_lockout(principal, failures, locked_until)
        return render_template(
            "accounting/private_ledger.html",
            denied=False,
            passcode_required=True,
            lockout_message=f"Too many attempts. Locked for {lock_minutes} minute(s).",
            locked=True,
            locked_until_epoch_ms=int(locked_until.timestamp() * 1000),
            attempts_left=0,
        )

    session["private_ledger_unlocked"] = True
    _set_lockout(principal, 0, None)
    return redirect(url_for("private_ledger.private_ledger"))


@private_ledger_bp.post("/private-ledger/change-passcode")
def private_ledger_change_passcode():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    if not session.get("private_ledger_unlocked"):
        return jsonify(ok=False, message="Passcode not unlocked."), 403

    old_code = (request.form.get("old_passcode") or "").strip()
    new_code = (request.form.get("new_passcode") or "").strip()
    confirm_code = (request.form.get("confirm_passcode") or "").strip()

    if not re.fullmatch(r"\d{6}", new_code or ""):
        return jsonify(ok=False, message="Passcode must be 6 digits."), 400
    if new_code != confirm_code:
        return jsonify(ok=False, message="New passcode does not match confirm."), 400

    passcode_doc = _ensure_passcode_doc()
    if not check_password_hash(passcode_doc.get("value_hash") or "", old_code):
        return jsonify(ok=False, message="Old passcode is incorrect."), 400

    private_ledger_settings_col.update_one(
        {"key": "passcode"},
        {"$set": {"value_hash": generate_password_hash(new_code), "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    private_ledger_settings_col.update_many(
        {"key": {"$regex": r"^lockout:"}},
        {"$set": {"failures": 0, "locked_until": None, "updated_at": datetime.utcnow()}},
    )
    return jsonify(ok=True)


@private_ledger_bp.post("/private-ledger/cash-drawing")
def create_cash_drawing():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    if not session.get("private_ledger_unlocked"):
        return jsonify(ok=False, message="Passcode not unlocked."), 403

    amount = request.form.get("amount")
    account_type = request.form.get("account_type")
    account_id = request.form.get("account_id") or None
    date_str = request.form.get("date") or ""
    memo = request.form.get("memo") or ""
    recorded_by = (request.form.get("recorded_by") or "").strip()
    authorized_by = (request.form.get("authorized_by") or "").strip()
    client_token = (request.form.get("client_token") or "").strip()

    if not recorded_by or not authorized_by:
        return jsonify(ok=False, message="Recorded by and Authorized by are required."), 400

    try:
        date_dt = datetime.fromisoformat(date_str) if date_str else datetime.utcnow()
    except Exception:
        date_dt = datetime.utcnow()

    if client_token:
        exists = private_ledger_col.find_one({"client_token": client_token, "entry_type": "cash_drawing"})
        if exists:
            return jsonify(ok=True, duplicate=True)

    result = post_withdrawal({
        "amount": amount,
        "account_type": account_type,
        "account_id": account_id,
        "purpose": "drawings",
        "purpose_note": memo,
        "date_dt": date_dt,
        "client_token": client_token,
        "recorded_by": recorded_by,
        "authorized_by": authorized_by,
        "created_by": session.get("user_id") or session.get("admin_id") or session.get("executive_id"),
    })
    if not result.get("ok"):
        return jsonify(ok=False, message=result.get("message") or "Failed"), 400
    _upsert_person("recorded_by", recorded_by)
    _upsert_person("authorized_by", authorized_by)
    return jsonify(ok=True)


@private_ledger_bp.post("/private-ledger/goods-drawn")
def create_goods_drawn():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    if not session.get("private_ledger_unlocked"):
        return jsonify(ok=False, message="Passcode not unlocked."), 403

    product_id = request.form.get("product_id")
    qty = request.form.get("quantity")
    unit_cost = request.form.get("unit_cost")
    date_str = request.form.get("date") or ""
    memo = request.form.get("memo") or ""
    recorded_by = (request.form.get("recorded_by") or "").strip()
    authorized_by = (request.form.get("authorized_by") or "").strip()
    client_token = (request.form.get("client_token") or "").strip()

    if not recorded_by or not authorized_by:
        return jsonify(ok=False, message="Recorded by and Authorized by are required."), 400

    try:
        date_dt = datetime.fromisoformat(date_str) if date_str else datetime.utcnow()
    except Exception:
        date_dt = datetime.utcnow()

    if client_token:
        exists = private_ledger_col.find_one({"client_token": client_token, "entry_type": "goods_drawn"})
        if exists:
            return jsonify(ok=True, duplicate=True)

    result = post_goods_drawn({
        "product_id": product_id,
        "quantity": qty,
        "unit_cost": unit_cost,
        "date_dt": date_dt,
        "memo": memo,
        "client_token": client_token,
        "recorded_by": recorded_by,
        "authorized_by": authorized_by,
        "created_by": session.get("user_id") or session.get("admin_id") or session.get("executive_id"),
    })
    if not result.get("ok"):
        return jsonify(ok=False, message=result.get("message") or "Failed"), 400
    _upsert_person("recorded_by", recorded_by)
    _upsert_person("authorized_by", authorized_by)
    return jsonify(ok=True)


@private_ledger_bp.post("/private-ledger/salary")
def create_salary_entry():
    if not _require_accounting_role():
        return jsonify(ok=False, message="Unauthorized"), 401
    if not session.get("private_ledger_unlocked"):
        return jsonify(ok=False, message="Passcode not unlocked."), 403

    date_str = (request.form.get("date") or "").strip()
    amount_str = (request.form.get("amount") or "").strip()
    memo = (request.form.get("memo") or "").strip()
    recorded_by = (request.form.get("recorded_by") or "").strip()
    authorized_by = (request.form.get("authorized_by") or "").strip()
    account_id = (request.form.get("account_id") or "").strip()
    client_token = (request.form.get("client_token") or "").strip()

    if not recorded_by or not authorized_by:
        return jsonify(ok=False, message="Recorded by and Authorized by are required."), 400

    try:
        amount = float(amount_str or 0)
    except Exception:
        amount = 0.0
    if amount <= 0:
        return jsonify(ok=False, message="Amount must be greater than zero."), 400

    dt = _parse_date(date_str) or datetime.utcnow()
    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    description = "entered on private ledger page"
    if memo:
        description = f"{description} - {memo}"

    account_oid = None
    if account_id:
        try:
            account_oid = ObjectId(account_id)
        except Exception:
            account_oid = None

    if client_token:
        exists = private_ledger_col.find_one({"client_token": client_token, "entry_type": "salary"})
        if exists:
            return jsonify(ok=True, duplicate=True)

    now = datetime.utcnow()
    expense_doc = {
        "date": dt,
        "amount": amount,
        "category": "executives Salary",
        "payment_method": "Bank Transfer",
        "description": description,
        "created_at": now,
        "updated_at": now,
    }

    try:
        exp_res = expenses_col.insert_one(expense_doc)
    except Exception:
        return jsonify(ok=False, message="Failed to record salary expense."), 500

    try:
        private_ledger_col.insert_one({
            "entry_type": "salary",
            "source_account_type": "bank",
            "source_account_id": account_oid,
            "date_dt": dt,
            "amount": amount,
            "purpose_text": description,
            "client_token": client_token,
            "recorded_by": recorded_by,
            "authorized_by": authorized_by,
            "created_by": session.get("user_id") or session.get("admin_id") or session.get("executive_id"),
            "status": "posted",
            "created_at": now,
            "link": {
                "related_collection": "expenses",
                "related_id": exp_res.inserted_id,
            },
        })
    except Exception:
        expenses_col.delete_one({"_id": exp_res.inserted_id})
        return jsonify(ok=False, message="Salary entry failed. Expense was rolled back."), 500

    _upsert_person("recorded_by", recorded_by)
    _upsert_person("authorized_by", authorized_by)
    return jsonify(ok=True)
