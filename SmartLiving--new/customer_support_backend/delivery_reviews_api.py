from datetime import datetime
import math

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo import ReturnDocument

from db import db
from services.manager_suite_scope import scoped_db
from services.support_record_audit import attribution

db = scoped_db(db)
from login import get_current_identity, role_required
from customer_support_backend.repeat_purchases_api import build_opportunity, serialize_opportunity

delivery_reviews_bp = Blueprint("delivery_reviews", __name__, url_prefix="/api/customer-support")


@delivery_reviews_bp.record_once
def ensure_indexes(_state):
    try:
        db.tasks.create_index([("kind", 1), ("status", 1), ("due_date", 1)])
        db.tasks.create_index([("package_id", 1), ("kind", 1)])
        db.packages.create_index([("status", 1), ("support_followup_task_id", 1)])
        db.card_closures.create_index([("action", 1), ("support_followup_task_id", 1)])
    except Exception:
        current_app.logger.exception("Unable to ensure delivery follow-up indexes")


def serialize_review(review):
    if not review:
        return None
    return {**review, "recorded_at": review["recorded_at"].isoformat() + "Z"}


def serialize_followup(row):
    return {**attribution(row), "id": str(row["_id"]), "task_no": row["task_no"], "customer_name": row.get("customer_name") or "Customer",
        "customer_phone": row.get("customer_phone") or "", "product_name": row.get("product_name") or "Item",
        "package_id": str(row.get("package_id") or ""), "branch": row.get("branch_name") or "",
        "status": row.get("status") or "pending", "due_date": row["due_date"].date().isoformat(),
        "triggers": row.get("delivery_triggers") or [], "version": row.get("review_version", 0),
        "review": serialize_review(row.get("latest_review")),
        "history": [serialize_review(review) for review in row.get("delivery_reviews") or []],
        "repeat_purchase": serialize_opportunity(row) if row.get("repeat_purchase") else None}


@delivery_reviews_bp.get("/delivery-review-summary")
@role_required("customer_support")
def review_summary():
    rows = list(db.tasks.find({"kind": "delivery_confirmation"}, {"status": 1, "latest_review": 1}))
    ratings = [row["latest_review"]["rating"] for row in rows if (row.get("latest_review") or {}).get("rating")]
    return jsonify(ok=True, summary={"rated": len(ratings), "average_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "confirmed": sum(row.get("status") == "completed" for row in rows),
        "open": sum(row.get("status") != "completed" for row in rows),
        "outstanding_issues": sum(bool((row.get("latest_review") or {}).get("issue")) and row.get("status") != "completed" for row in rows)})


@delivery_reviews_bp.get("/delivery-followups")
@role_required("customer_support")
def followup_queue():
    query = {"kind": "delivery_confirmation"}
    state = request.args.get("status", "open")
    if state == "open":
        query["status"] = {"$ne": "completed"}
    elif state == "completed":
        query["status"] = "completed"
    try:
        page = max(int(request.args.get("page") or 1), 1)
        per_page = max(1, min(int(request.args.get("per_page") or 25), 100))
    except ValueError:
        return jsonify(ok=False, message="Select a valid follow-up page."), 400
    total = db.tasks.count_documents(query)
    total_pages = max(math.ceil(total / per_page), 1)
    page = min(page, total_pages)
    rows = db.tasks.find(query).sort([("due_date", 1), ("_id", 1)]).skip((page - 1) * per_page).limit(per_page)
    return jsonify(ok=True, followups=[serialize_followup(row) for row in rows],
        pagination={"page": page, "total": total, "total_pages": total_pages},
        open_count=db.tasks.count_documents({"kind": "delivery_confirmation", "status": {"$ne": "completed"}}))


@delivery_reviews_bp.route("/delivery-followups/<task_id>", methods=["GET", "POST"])
@role_required("customer_support")
def review_delivery(task_id):
    try:
        task_oid = ObjectId(task_id)
    except Exception:
        return jsonify(ok=False, message="Invalid follow-up task."), 400
    task = db.tasks.find_one({"_id": task_oid, "kind": "delivery_confirmation"})
    if not task:
        return jsonify(ok=False, message="Follow-up task not found."), 404
    if request.method == "GET":
        return jsonify(ok=True, followup=serialize_followup(task))
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, message="Enter a valid review."), 400
    contacted = data.get("contacted")
    if not isinstance(contacted, bool):
        return jsonify(ok=False, message="Record whether the customer was reached."), 400
    receipt = str(data.get("receipt") or "") if contacted else "unknown"
    satisfaction = str(data.get("satisfaction") or "") if contacted else "unknown"
    rating = data.get("rating") if contacted else None
    issue, notes = str(data.get("issue") or "").strip(), str(data.get("notes") or "").strip()
    if contacted and (receipt not in {"full", "partial", "none"} or satisfaction not in {"satisfied", "issue"}):
        return jsonify(ok=False, message="Select receipt confirmation and customer satisfaction."), 400
    if contacted and (type(rating) is not int or not 1 <= rating <= 5):
        return jsonify(ok=False, message="Select a star rating from 1 to 5."), 400
    if not notes or len(notes) > 2000 or len(issue) > 2000:
        return jsonify(ok=False, message="Enter follow-up notes (up to 2,000 characters)."), 400
    if contacted and (receipt != "full" or satisfaction == "issue") and not issue:
        return jsonify(ok=False, message="Describe the outstanding issue or missing item."), 400
    completed = contacted and receipt == "full" and satisfaction == "satisfied" and not issue
    next_date = None
    if not completed:
        try:
            next_date = datetime.strptime(str(data.get("next_followup_date") or ""), "%Y-%m-%d")
            if next_date.date() < datetime.utcnow().date():
                raise ValueError()
        except ValueError:
            return jsonify(ok=False, message="Select a next follow-up date of today or later."), 400
    identity = get_current_identity()
    now = datetime.utcnow()
    review = {"contacted": contacted, "receipt": receipt, "satisfaction": satisfaction, "rating": rating,
        "issue": issue, "notes": notes, "next_followup_date": next_date.date().isoformat() if next_date else "",
        "recorded_at": now, "officer_id": str(identity.get("user_id") or ""), "officer_name": identity.get("name") or "Customer Support"}
    version = data.get("version")
    if type(version) is not int or version != task.get("review_version", 0):
        return jsonify(ok=False, message="This follow-up has changed. Close and reopen it before saving."), 409
    fields = {"latest_review": review, "status": "completed" if completed else "in progress", "updated_at": now,
        "completed_at": now if completed else None, "assignee_id": str(identity.get("user_id") or ""), "assignee_name": review["officer_name"]}
    if next_date:
        fields["due_date"] = next_date
    if completed and not task.get("repeat_purchase"):
        interest = data.get("repeat_interest")
        if not isinstance(interest, str) or interest not in {"yes", "no"}:
            return jsonify(ok=False, message="Ask whether the customer needs another item and record their answer."), 400
        review["repeat_interest"] = interest
        if interest == "yes":
            try:
                fields["repeat_purchase"] = build_opportunity(data, identity, now)
            except ValueError as error:
                return jsonify(ok=False, message=str(error)), 400
    updated = db.tasks.find_one_and_update({"_id": task_oid, "review_version": version},
        {"$set": fields, "$push": {"delivery_reviews": review}, "$inc": {"review_version": 1}}, return_document=ReturnDocument.AFTER)
    if not updated:
        return jsonify(ok=False, message="Another officer updated this follow-up. Close and reopen it."), 409
    return jsonify(ok=True, followup=serialize_followup(updated), message="Receipt confirmed and follow-up completed." if completed else "Review saved. Follow-up remains open.")
