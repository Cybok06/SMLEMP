"""Staff debt register. Original figures and repayment entries are immutable."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID
from bson import ObjectId
from flask import Blueprint, jsonify, request, render_template, abort
from pymongo.errors import DuplicateKeyError
from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity

staff_loans_bp = Blueprint("staff_loans", __name__)
MANAGEMENT = {"hr", "admin", "executive"}

def suite_manager(user):
    return user.get('role') == 'manager' and (
        request.path.startswith('/hr/') or request.headers.get('X-Manager-Suite') == 'hr'
    )

def identity(management=False):
    user = get_current_identity()
    if not user.get("is_authenticated") or not user.get("user_id"):
        abort(401)
    if user.get("role") not in (MANAGEMENT if management else MANAGEMENT | {"agent", "manager"}) and not suite_manager(user):
        abort(403)
    if suite_manager(user):
        from services.manager_suite_scope import manager_scope
        manager_scope(db)
    return user

def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("100000000") or amount != amount.quantize(Decimal("0.01")):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a non-negative amount with at most two decimal places.")

def text(value, label, required=False, limit=1000):
    if not isinstance(value, str):
        raise ValueError(label + " must be text.")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(label + " is required and must be within " + str(limit) + " characters.")
    return value

def day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise ValueError("Enter a valid date.")

def oid(value):
    try:
        return ObjectId(value)
    except Exception:
        raise ValueError("Invalid employee or loan identifier.")

def token(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid request identifier is required.")

def actor(user):
    return {"user_id": user["user_id"], "name": user.get("name", "Staff"), "role": user["role"]}

def public(doc):
    result = dict(doc)
    result["id"] = str(result.pop("_id"))
    for key in ("principal_cents", "total_cents", "company_cents", "remaining_cents"):
        result[key.replace("_cents", "")] = result.pop(key) / 100
    result["paid"] = result["principal"] - result["remaining"]
    result["status"] = "Settled" if result["remaining"] == 0 else "Overdue" if result["due_date"] < date.today().isoformat() else "Open"
    result["payments"] = [{**p, "amount": p["amount_cents"] / 100} for p in result.get("payments", [])]
    result.pop("request_id", None)
    return result

@staff_loans_bp.errorhandler(ValueError)
def bad_request(error):
    return jsonify(error=str(error)), 400

@staff_loans_bp.route("/hr/staff-loans")
@staff_loans_bp.route("/admin/staff-loans")
@staff_loans_bp.route("/executive/staff-loans")
def management_page():
    user = identity(True)
    if request.path.split("/")[1] != user["role"] and not (request.path.startswith('/hr/') and suite_manager(user)):
        abort(403)
    if user["role"] == "hr" or suite_manager(user):
        return render_template("hr_pages/hr_shell.html", hr_content_template="staff_loans_content.html", loan_management=True)
    return render_template("staff_loans_page.html", loan_role=user["role"], loan_management=True)

@staff_loans_bp.get("/staff-loans")
def employee_page():
    user = identity()
    return render_template("staff_loans_page.html", loan_role=user["role"], loan_management=False)

@staff_loans_bp.get("/api/staff-loans/employees")
def employees():
    identity(True)
    people = []
    for collection, manual in (("users", False), ("Exited", False), ("staff_loan_manual_staff", True)):
        for user in db[collection].find({}, {"name": 1, "username": 1, "role": 1, "phone": 1, "branch": 1, "staff_code": 1}):
            people.append({"id": str(user["_id"]), "name": user.get("name") or user.get("username") or "Unnamed staff", "role": user.get("role", "Manual staff"), "manual": manual, "staff_code": user.get("staff_code", "")})
    return jsonify(employees=sorted(people, key=lambda x: x["name"].lower()))

@staff_loans_bp.get("/api/staff-loans")
@staff_loans_bp.get("/api/staff-loans/mine")
def listing():
    user = identity()
    own = request.path.endswith("/mine") or (user["role"] not in MANAGEMENT and not suite_manager(user))
    query = {"employee_id": user["user_id"], "manual": False} if own else {}
    if not own and request.args.get("employee_id"):
        query["employee_id"] = str(oid(request.args["employee_id"]))
        query["manual"] = request.args.get("manual") == "true"
    loans = [public(d) for d in db.staff_loans.find(query).sort("created_at", -1)]
    summary = {key: round(sum(d[key] for d in loans), 2) for key in ("principal", "paid", "remaining")}
    summary["overdue"] = sum(d["remaining"] for d in loans if d["status"] == "Overdue")
    summary["next_due"] = min((d["due_date"] for d in loans if d["remaining"] > 0), default=None)
    return jsonify(loans=loans, summary=summary)

@staff_loans_bp.post("/api/staff-loans")
def create():
    user = identity(True)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise ValueError("Enter a valid loan or repayment record.")
    request_id = token(data.get("request_id"))
    db.staff_loans.create_index("request_id", unique=True)
    existing = db.staff_loans.find_one({"request_id": request_id})
    if existing:
        return jsonify(loan=public(existing))
    kind = data.get("kind")
    if kind not in {"Loan", "Advance", "Shared expense"}:
        raise ValueError("Choose loan, advance or shared expense.")
    total, company = money(data.get("total")), money(data.get("company", 0))
    if total <= 0 or company > total:
        raise ValueError("Total must be positive; company contribution cannot exceed it.")
    reason = text(data.get("reason", ""), "Reason", True)
    issued, due = day(data.get("issued_date")), day(data.get("due_date"))
    if issued > date.today().isoformat() or due < issued:
        raise ValueError("Issue date cannot be in the future; due date must be on or after issue date.")
    manual = data.get("manual") is True
    if data.get("employee_id"):
        employee_id = oid(data["employee_id"])
        employee = db["staff_loan_manual_staff" if manual else "users"].find_one({"_id": employee_id})
        if not employee and not manual:
            employee = db["Exited"].find_one({"_id": employee_id})
        if not employee:
            raise ValueError("Staff member was not found.")
    elif manual:
        details = data.get("staff") or {}
        if not isinstance(details, dict):
            raise ValueError("Enter valid staff details.")
        employee = {"name": text(details.get("name", ""), "Staff name", True, 150), "phone": text(details.get("phone", ""), "Phone", True, 60), "staff_code": text(details.get("staff_code", ""), "Staff reference", True, 100), "branch": text(details.get("branch", ""), "Branch", False, 150), "role": "Manual staff"}
        # Stable reference prevents duplicate manual identities across loans and retries.
        db.staff_loan_manual_staff.create_index("staff_code", unique=True)
        try:
            db.staff_loan_manual_staff.update_one({"staff_code": employee["staff_code"]}, {"$setOnInsert": employee}, upsert=True)
        except DuplicateKeyError:
            pass  # A concurrent request created the same stable manual identity.
        entered = employee
        employee = db.staff_loan_manual_staff.find_one({"staff_code": employee["staff_code"]})
        if any(employee.get(k) != entered[k] for k in ("name", "phone")):
            raise ValueError("That staff reference already belongs to a different saved identity. Select the existing staff member or use their correct reference.")
        employee_id = employee["_id"]
    else:
        raise ValueError("Select an employee or enter manual staff details.")
    doc = {"request_id": request_id, "employee_id": str(employee_id), "manual": manual, "employee": {k: str(employee.get(k) or "") for k in ("name", "role", "phone", "branch", "staff_code")}, "kind": kind, "reason": reason, "issued_date": issued, "due_date": due, "total_cents": total, "company_cents": company, "principal_cents": total-company, "remaining_cents": total-company, "payments": [], "version": 0, "created_at": datetime.now(timezone.utc).isoformat(), "created_by": actor(user)}
    if not doc["employee"]["name"]:
        doc["employee"]["name"] = employee.get("username", "Staff")
    try:
        doc["_id"] = db.staff_loans.insert_one(doc).inserted_id
    except DuplicateKeyError:
        doc = db.staff_loans.find_one({"request_id": request_id})
    return jsonify(loan=public(doc)), 201

@staff_loans_bp.post("/api/staff-loans/<loan_id>/payments")
def payment(loan_id):
    user = identity(True)
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise ValueError("Enter a valid loan or repayment record.")
    payment_id = token(data.get("request_id"))
    doc = db.staff_loans.find_one({"_id": oid(loan_id)})
    if not doc:
        abort(404)
    if any(p["id"] == payment_id for p in doc["payments"]):
        return jsonify(loan=public(doc))
    amount = money(data.get("amount"))
    when = day(data.get("date"))
    if amount <= 0 or amount > doc["remaining_cents"]:
        raise ValueError("Payment must be positive and cannot exceed the remaining debt.")
    if when < doc["issued_date"] or when > date.today().isoformat():
        raise ValueError("Payment date must be between the issue date and today.")
    method = data.get("method")
    if method not in {"Repayment", "Payroll deduction"}:
        raise ValueError("Choose repayment or payroll deduction.")
    entry = {"id": payment_id, "amount_cents": amount, "date": when, "method": method, "reference": text(data.get("reference", ""), "Reference", True, 200), "notes": text(data.get("notes", ""), "Notes"), "recorded_by": actor(user), "recorded_at": datetime.now(timezone.utc).isoformat()}
    result = db.staff_loans.update_one({"_id": doc["_id"], "version": doc["version"], "remaining_cents": {"$gte": amount}}, {"$inc": {"remaining_cents": -amount, "version": 1}, "$push": {"payments": entry}})
    if not result.modified_count:
        latest = db.staff_loans.find_one({"_id": doc["_id"]})
        if any(p["id"] == payment_id for p in latest["payments"]):
            return jsonify(loan=public(latest))
        return jsonify(error="Balance changed. Refresh before recording this payment."), 409
    return jsonify(loan=public(db.staff_loans.find_one({"_id": doc["_id"]})))
