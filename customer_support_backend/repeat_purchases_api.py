"""Repeat-purchase opportunities live on their originating delivery task.

Creating the opportunity and completing the delivery review is one atomic write;
agent updates touch only opportunity fields and retain the review history.
"""
from datetime import datetime, timezone
import math
import re

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, current_app, jsonify, request
from pymongo import ReturnDocument

from db import db
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity, role_required

repeat_purchase_bp = Blueprint("repeat_purchases", __name__)
OPEN_STATUSES = {"Assigned", "In Progress", "Waiting for Customer"}
STATUSES = OPEN_STATUSES | {"Purchased", "Closed"}


@repeat_purchase_bp.record_once
def ensure_indexes(_state):
    try:
        db.tasks.create_index([("repeat_purchase.agent_id", 1), ("repeat_purchase.status", 1), ("repeat_purchase.next_followup_date", 1)])
        db.tasks.create_index([("repeat_purchase.created_at", -1)])
    except Exception:
        current_app.logger.exception("Unable to ensure repeat-purchase indexes")


def parse_date(value):
    try:
        result = datetime.strptime(str(value), "%Y-%m-%d")
        if result.date() < datetime.utcnow().date():
            raise ValueError()
        return result.date().isoformat()
    except ValueError:
        raise ValueError("Choose a follow-up date of today or later.")


def build_opportunity(data, identity, now):
    product = str(data.get("requested_product") or "").strip()
    notes = str(data.get("repeat_notes") or "").strip()
    if not product or len(product) > 200 or len(notes) > 2000:
        raise ValueError("Enter the requested product (up to 200 characters).")
    try:
        agent_id = ObjectId(str(data.get("repeat_agent_id") or ""))
    except Exception:
        raise ValueError("Select an agent to follow up on this opportunity.")
    agent = db.users.find_one({"_id": agent_id, "role": "agent"})
    if not agent:
        raise ValueError("Select a valid agent for this opportunity.")
    due = parse_date(data.get("repeat_followup_date"))
    return {"requested_product": product, "agent_id": str(agent_id), "agent_name": agent.get("name") or agent.get("username") or "Agent",
        "status": "Assigned", "next_followup_date": due, "notes": notes, "version": 0,
        "created_at": now, "created_by": str(identity.get("user_id") or ""), "created_by_name": identity.get("name") or "Customer Support",
        "updated_at": now, "history": [{"status": "Assigned", "notes": notes or "Customer requested another item after delivery confirmation.",
            "actor_name": identity.get("name") or "Customer Support", "actor_id": str(identity.get("user_id") or ""), "actor_role": identity.get('role') or "customer_support", "at": now}]}


def serialize_opportunity(task):
    opportunity = task["repeat_purchase"]
    return {**opportunity, "id": str(task["_id"]), "customer_id": str(task.get("customer_id") or ""),
        "customer_name": task.get("customer_name") or "Customer", "customer_phone": task.get("customer_phone") or "",
        "original_product": task.get("product_name") or "", "branch": task.get("branch_name") or "",
        "created_at": opportunity["created_at"].isoformat() + "Z", "updated_at": opportunity["updated_at"].isoformat() + "Z",
        "history": [{**event, "at": event["at"].isoformat() + "Z"} for event in opportunity.get("history") or []]}


def scoped_query():
    identity = get_current_identity()
    is_agent = identity.get("role") == "agent"
    if request.path.startswith("/api/agent/") != is_agent:
        return None
    query = {"kind": "delivery_confirmation", "repeat_purchase": {"$exists": True}}
    if is_agent:
        query["repeat_purchase.agent_id"] = str(identity.get("user_id") or "")
    return query


def find_opportunity(opportunity_id):
    query = scoped_query()
    if query is None:
        return None
    try:
        query["_id"] = ObjectId(opportunity_id)
    except Exception:
        return None
    return db.tasks.find_one(query)


@repeat_purchase_bp.get("/api/customer-support/repeat-purchases")
@repeat_purchase_bp.get("/api/agent/repeat-purchases")
@role_required("customer_support", "agent")
def list_opportunities():
    query = scoped_query()
    if query is None:
        return jsonify(ok=False, message="Forbidden"), 403
    base = dict(query)
    status = request.args.get("status", "open")
    if status == "open":
        query["repeat_purchase.status"] = {"$in": list(OPEN_STATUSES)}
    elif status in STATUSES:
        query["repeat_purchase.status"] = status
    elif status != "all":
        return jsonify(ok=False, message="Choose a valid opportunity status."), 400
    term = (request.args.get("q") or "").strip()
    if term:
        rx = {"$regex": re.escape(term), "$options": "i"}
        query["$or"] = [{"customer_name": rx}, {"customer_phone": rx}, {"repeat_purchase.requested_product": rx}, {"repeat_purchase.agent_name": rx}]
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        return jsonify(ok=False, message="Choose a valid page."), 400
    total = db.tasks.count_documents(query)
    pages = max(1, math.ceil(total / 25)); page = min(page, pages)
    rows = db.tasks.find(query).sort([("repeat_purchase.next_followup_date", 1), ("_id", -1)]).skip((page-1)*25).limit(25)
    return jsonify(ok=True, opportunities=[serialize_opportunity(row) for row in rows],
        pagination={"page": page, "total": total, "total_pages": pages},
        counts={status: db.tasks.count_documents({**base, "repeat_purchase.status": status}) for status in sorted(STATUSES)})


@repeat_purchase_bp.get("/api/customer-support/repeat-purchases/<opportunity_id>")
@repeat_purchase_bp.get("/api/agent/repeat-purchases/<opportunity_id>")
@role_required("customer_support", "agent")
def opportunity_detail(opportunity_id):
    task = find_opportunity(opportunity_id)
    if not task:
        return jsonify(ok=False, message="Opportunity not found or not assigned to you."), 404
    return jsonify(ok=True, opportunity=serialize_opportunity(task))


def eligible_purchases(task):
    try:
        customer_id = ObjectId(str(task.get("customer_id")))
    except Exception:
        return []
    customer = db.customers.find_one({"_id": customer_id}) or {}
    result = []
    for index, purchase in enumerate(customer.get("purchases") or []):
        product = purchase.get("product") or {}
        if index == task.get("product_index") or str(product.get("status") or purchase.get("status") or "").lower() in {"closed", "cancelled", "canceled", "withdrawn"}:
            continue
        value = purchase.get("created_at") or purchase.get("purchase_date")
        try:
            when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if when.tzinfo:
                when = when.astimezone(timezone.utc).replace(tzinfo=None)
            if when.date() < task["repeat_purchase"]["created_at"].date():
                continue
        except (TypeError, ValueError):
            continue
        result.append({"index": index, "product_name": product.get("name") or "Product", "purchase_date": when.date().isoformat(), "quantity": product.get("quantity") or 1})
    return result


@repeat_purchase_bp.get("/api/agent/repeat-purchases/<opportunity_id>/purchases")
@role_required("agent")
def purchase_options(opportunity_id):
    task = find_opportunity(opportunity_id)
    if not task:
        return jsonify(ok=False, message="Opportunity not found or not assigned to you."), 404
    return jsonify(ok=True, purchases=eligible_purchases(task))


@repeat_purchase_bp.patch("/api/agent/repeat-purchases/<opportunity_id>")
@role_required("agent")
def update_opportunity(opportunity_id):
    task = find_opportunity(opportunity_id)
    if not task:
        return jsonify(ok=False, message="Opportunity not found or not assigned to you."), 404
    opportunity = task["repeat_purchase"]
    if opportunity["status"] in {"Purchased", "Closed"}:
        return jsonify(ok=False, message="This opportunity is already finished."), 409
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, message="Enter a valid opportunity update."), 400
    status = str(data.get("status") or "")
    notes = str(data.get("notes") or "").strip()
    if status not in STATUSES or not notes or len(notes) > 2000:
        return jsonify(ok=False, message="Select a status and enter follow-up notes (up to 2,000 characters)."), 400
    now = datetime.utcnow()
    fields = {"repeat_purchase.status": status, "repeat_purchase.notes": notes, "repeat_purchase.updated_at": now}
    if status in OPEN_STATUSES:
        try:
            fields["repeat_purchase.next_followup_date"] = parse_date(data.get("next_followup_date"))
        except ValueError as error:
            return jsonify(ok=False, message=str(error)), 400
    elif status == "Purchased":
        index = data.get("purchase_index")
        purchase = next((row for row in eligible_purchases(task) if type(index) is int and row["index"] == index), None)
        if not purchase:
            return jsonify(ok=False, message="Link an actual new purchase on this customer's account."), 400
        fields["repeat_purchase.purchase"] = purchase
        fields["repeat_purchase.finished_at"] = now.isoformat() + "Z"
    else:
        reason = str(data.get("closure_reason") or "").strip()
        if not reason or len(reason) > 1000:
            return jsonify(ok=False, message="Enter the reason this opportunity is being closed."), 400
        fields["repeat_purchase.closure_reason"] = reason
        fields["repeat_purchase.finished_at"] = now.isoformat() + "Z"
    if status not in OPEN_STATUSES:
        fields["repeat_purchase.next_followup_date"] = ""
    version = data.get("version")
    if type(version) is not int or version != opportunity["version"]:
        return jsonify(ok=False, message="This opportunity has changed. Refresh and retry."), 409
    identity = get_current_identity()
    event = {"status": status, "notes": notes, "actor_name": identity.get("name") or "Agent", "actor_id": str(identity.get("user_id") or ""), "actor_role": "agent", "at": now}
    if status == "Purchased": event["purchase"] = fields["repeat_purchase.purchase"]
    if status == "Closed": event["closure_reason"] = fields["repeat_purchase.closure_reason"]
    updated = db.tasks.find_one_and_update({"_id": task["_id"], "repeat_purchase.agent_id": str(identity.get("user_id")), "repeat_purchase.version": version},
        {"$set": fields, "$inc": {"repeat_purchase.version": 1}, "$push": {"repeat_purchase.history": event}}, return_document=ReturnDocument.AFTER)
    if not updated:
        return jsonify(ok=False, message="This opportunity has changed. Refresh and retry."), 409
    return jsonify(ok=True, opportunity=serialize_opportunity(updated))


@repeat_purchase_bp.patch("/api/customer-support/repeat-purchases/<opportunity_id>/assignment")
@role_required("customer_support")
def reassign_opportunity(opportunity_id):
    task = find_opportunity(opportunity_id)
    if not task:
        return jsonify(ok=False, message="Opportunity not found."), 404
    current = task["repeat_purchase"]
    if current["status"] not in OPEN_STATUSES:
        return jsonify(ok=False, message="Finished opportunities cannot be reassigned."), 409
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, message="Enter a valid assignment."), 400
    if type(data.get("version")) is not int or data["version"] != current["version"]:
        return jsonify(ok=False, message="This opportunity has changed. Refresh and retry."), 409
    try:
        agent = db.users.find_one({"_id": ObjectId(str(data.get("agent_id"))), "role": "agent"})
        due = parse_date(data.get("next_followup_date"))
    except (ValueError, TypeError, InvalidId):
        return jsonify(ok=False, message="Select an agent and a valid follow-up date."), 400
    notes = str(data.get("notes") or "").strip()
    if not agent or not notes or len(notes) > 2000:
        return jsonify(ok=False, message="Select a valid agent and enter assignment notes."), 400
    identity = get_current_identity(); now = datetime.utcnow()
    agent_name = agent.get("name") or agent.get("username") or "Agent"
    event = {"status": "Assigned", "notes": notes, "agent_name": agent_name, "agent_id": str(agent["_id"]),
        "actor_name": identity.get("name") or "Customer Support", "actor_id": str(identity.get("user_id") or ""), "actor_role": "customer_support", "at": now}
    updated = db.tasks.find_one_and_update({"_id": task["_id"], "repeat_purchase.version": current["version"]},
        {"$set": {"repeat_purchase.agent_id": str(agent["_id"]), "repeat_purchase.agent_name": agent_name, "repeat_purchase.status": "Assigned",
            "repeat_purchase.next_followup_date": due, "repeat_purchase.notes": notes, "repeat_purchase.updated_at": now},
         "$inc": {"repeat_purchase.version": 1}, "$push": {"repeat_purchase.history": event}}, return_document=ReturnDocument.AFTER)
    if not updated:
        return jsonify(ok=False, message="This opportunity has changed. Refresh and retry."), 409
    return jsonify(ok=True, opportunity=serialize_opportunity(updated))
