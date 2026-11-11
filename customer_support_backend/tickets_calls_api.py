from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import csv
import io
import math
import re
import requests
import time
from urllib.parse import quote

from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument
from flask import Blueprint, Response, current_app, jsonify, request

from db import db
from services.support_record_audit import attribution
from services.payment_receipt_tracking import receipt_tracking
from services.manager_suite_scope import scoped_db

db = scoped_db(db)
from login import get_current_identity, role_required
from services.phone_numbers import normalize_ghana_phone


customer_support_operations_bp = Blueprint(
    "customer_support_operations", __name__, url_prefix="/api/customer-support"
)

customers_col = db.customers
users_col = db.users
complaints_col = db.complaints
calls_col = db.customer_support_calls
payments_col = db.payments
documents_col = db.customer_support_documents
tasks_col = db.tasks
packages_col = db.packages
products_col = db.products
notification_state_col = db.customer_support_notification_state
followups_col = db.customer_support_followups
messages_col = db.customer_support_messages
manual_activities_col = db.customer_support_manual_activities
customer_detail_edits_col = db.customer_support_customer_detail_edits
ARKESEL_API_KEY = "RGZSU1ZDTWF4am1SQnNkZEZubkc"

_DASHBOARD_CACHE = {"expires_at": 0.0, "payload": None}
_DASHBOARD_CACHE_SECONDS = 15

def _normalize_ghana_phone(raw):
    return normalize_ghana_phone(raw)


ISSUE_TYPES = ["Payment", "Security/Compliance", "General Enquiry", "Delivery", "Product Fault", "Collection/Agent"]
CHANNELS = ["Walk-In", "Call", "WhatsApp", "Email", "Facebook", "Instagram", "Other"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
STATUS_TO_UI = {"Unassigned": "New", "Waiting for Customer": "Pending"}
STATUS_FROM_UI = {"New": "Unassigned", "Pending": "Waiting for Customer"}


@customer_support_operations_bp.record_once
def _ensure_mobile_call_indexes(_state):
    try:
        calls_col.create_index([("device_id", 1), ("external_call_id", 1)], unique=True,
            partialFilterExpression={"source": "android", "device_id": {"$type": "string"}, "external_call_id": {"$type": "string"}},
            name="uniq_android_device_call")
    except Exception:
        current_app.logger.exception("Unable to ensure Android call-sync indexes")


def _oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _dt(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _ticket(doc, customer=None):
    customer = customer or {}
    created = _dt(doc.get("date_reported") or doc.get("created_at")) or datetime.utcnow()
    due = _dt(doc.get("sla_due"))
    resolved = doc.get("status") in {"Resolved", "Closed"}
    breached = bool(due and not resolved and datetime.utcnow() > due)
    if resolved:
        sla_status, remaining = "Resolved", "-"
    elif breached:
        sla_status, remaining = "Breached", "BREACH"
    elif due:
        hours = max(int((due - datetime.utcnow()).total_seconds() // 3600), 0)
        sla_status = "At Risk" if hours < 12 else "On Track"
        remaining = f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{hours}h"
    else:
        sla_status, remaining = "On Track", "-"
    return {**attribution(doc), 
        "_id": str(doc.get("_id", "")), "id": doc.get("ticket_no") or str(doc.get("_id", "")),
        "subject": doc.get("subject") or doc.get("description") or "Customer issue",
        "description": doc.get("description") or "", "customer": customer.get("name") or doc.get("customer_name") or "Unknown Customer",
        "customerId": str(doc.get("customer_id") or ""), "phone": customer.get("phone_number") or doc.get("customer_phone") or "",
        "location": customer.get("location") or "", "imageUrl": customer.get("image_url") or "",
        "customerBranch": customer.get("agent_branch") or customer.get("branch") or doc.get("branch") or "Unassigned",
        "issueType": doc.get("issue_type") or "General Enquiry", "priority": doc.get("priority") or "Medium",
        "status": STATUS_TO_UI.get(doc.get("status"), doc.get("status") or "New"), "owner": doc.get("assigned_to_name") or "Unassigned",
        "branch": doc.get("branch") or "Unassigned", "channel": doc.get("channel") or "Call",
        "created": created.strftime("%d %b %Y"), "createdTime": created.strftime("%I:%M %p"),
        "dueDate": due.strftime("%d %b %Y, %I:%M %p") if due else "Not set",
        "slaStatus": sla_status, "slaRemaining": remaining, "responses": len(doc.get("updates") or []), "tags": doc.get("tags") or [],
        "rootCause": doc.get("root_cause") or "", "resolutionNotes": doc.get("resolution_notes") or "",
        "closureNotes": doc.get("closure_notes") or "", "updates": [
            {**update, "created_at": (_dt(update.get("created_at")) or datetime.utcnow()).isoformat()}
            for update in (doc.get("updates") or []) if isinstance(update, dict)
        ],
    }


def _ticket_query(value):
    oid = _oid(value)
    return {"$or": ([{"_id": oid}] if oid else []) + [{"ticket_no": value}]}


def _ticket_filters():
    query = {}
    term = (request.args.get("q") or "").strip()
    if term:
        rx = re.escape(term)
        query["$or"] = [{"ticket_no": {"$regex": rx, "$options": "i"}}, {"customer_name": {"$regex": rx, "$options": "i"}}, {"customer_phone": {"$regex": rx, "$options": "i"}}]
    for param, field in (("status", "status"), ("priority", "priority"), ("branch", "branch"), ("owner", "assigned_to_name"), ("issue", "issue_type")):
        value = (request.args.get(param) or "").strip()
        if value:
            if param == "status" and value in STATUS_FROM_UI:
                query[field] = {"$in": [value, STATUS_FROM_UI[value]]}
            else:
                query[field] = value
    return query


def _customer_map(rows):
    ids = [row.get("customer_id") for row in rows if row.get("customer_id")]
    variants = []
    for value in ids:
        variants.append(value)
        oid = _oid(value)
        if oid: variants.append(oid)
    return {str(row["_id"]): row for row in customers_col.find({"_id": {"$in": variants}}, {"name": 1, "phone_number": 1, "location": 1, "image_url": 1, "agent_branch": 1, "branch": 1})} if variants else {}


def _call(doc):
    created = _dt(doc.get("started_at") or doc.get("created_at")) or datetime.utcnow()
    duration_seconds = int(doc.get("duration_seconds") or 0)
    duration = str(doc.get("duration") or (f"{duration_seconds // 60}m {duration_seconds % 60}s" if duration_seconds else "0m"))
    numbers = [int(n) for n in re.findall(r"\d+", duration)]
    seconds = (numbers[0] * 60 + numbers[1]) if len(numbers) > 1 else (numbers[0] if numbers else 0)
    return {**attribution(doc), "_id": str(doc.get("_id", "")), "id": doc.get("call_no") or str(doc.get("_id", "")), "type": doc.get("type") or "Inbound", "customer": doc.get("customer_name") or "Unknown Customer",
            "customerId": str(doc.get("customer_id") or ""), "phone": doc.get("customer_phone") or "", "officer": doc.get("officer_name") or "Customer Support",
            "officerInitials": "CS", "department": doc.get("department") or "Customer Support", "purpose": doc.get("purpose") or "",
            "duration": duration, "durationSecs": seconds, "outcome": doc.get("outcome") or "Pending", "followUp": bool(doc.get("follow_up")),
            "followUpDate": doc.get("follow_up_date") or "", "date": created.strftime("%d %b %Y"), "time": created.strftime("%I:%M %p"),
            "recorded": bool(doc.get("recorded")), "linkedTicket": doc.get("linked_ticket") or "", "linkedTask": "", "branch": doc.get("branch") or "",
            "notes": doc.get("notes") or "", "followUpAgent": doc.get("follow_up_agent_name") or "", "followUpAgentBranch": doc.get("follow_up_agent_branch") or "",
            "source": doc.get("source") or "manual", "enrichmentStatus": doc.get("enrichment_status") or "complete",
            "customerMatch": doc.get("customer_match") or ("matched" if doc.get("customer_id") else "not_customer"), "deviceName": doc.get("device_name") or doc.get("device_id") or "",
            "interaction": {**{key: doc.get(key) or "" for key in INTERACTION_FIELDS}, "next_followup_at": (_dt(doc.get("next_followup_at"))).isoformat() if _dt(doc.get("next_followup_at")) else ""},
            "fromNumber": doc.get("from_number") or "",
            "callbackStatus": doc.get("callback_status") or ("Pending" if str(doc.get("type") or "").lower() == "missed" else ""),
            "calledBackAt": (_dt(doc.get("called_back_at")).isoformat() if _dt(doc.get("called_back_at")) else "")}


def _reference_prefix(kind, now):
    from services.manager_suite_scope import manager_scope
    scope = manager_scope(db)
    if scope:
        branch_key = hashlib.sha256(scope.branch.casefold().encode()).hexdigest()[:8].upper()
        return f'{kind}-BR-{branch_key}-{now:%Y%m%d}-'
    return now.strftime(f'{kind}-%Y%m%d-')


def _dashboard_change(current, previous):
    return current - previous


@customer_support_operations_bp.get("/dashboard")
@role_required("customer_support")
def support_dashboard():
    """Small, cached command-centre payload used for the first screen after login."""
    now_ts = time.monotonic()
    from services.manager_suite_scope import manager_scope
    restricted = manager_scope(db) is not None
    cached = None if restricted else _DASHBOARD_CACHE.get("payload")
    if cached is not None and now_ts < float(_DASHBOARD_CACHE.get("expires_at") or 0):
        return jsonify(cached)

    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)
    this_week = today - timedelta(days=today.weekday())
    last_week = this_week - timedelta(days=7)
    resolved_statuses = ["Resolved", "Closed"]
    open_query = {"status": {"$nin": resolved_statuses}}

    open_tickets = complaints_col.count_documents(open_query)
    active_deliveries = packages_col.count_documents({"status": {"$ne": "delivered"}})
    calls_today = calls_col.count_documents({"$or": [{"created_at": {"$gte": today}}, {"started_at": {"$gte": today}}]})
    current_opened = complaints_col.count_documents({"$or": [{"date_reported": {"$gte": this_week}}, {"created_at": {"$gte": this_week}}]})
    previous_opened = complaints_col.count_documents({"$or": [{"date_reported": {"$gte": last_week, "$lt": this_week}}, {"created_at": {"$gte": last_week, "$lt": this_week}}]})
    current_deliveries = packages_col.count_documents({"status": {"$ne": "delivered"}, "created_at": {"$gte": this_week}})
    previous_deliveries = packages_col.count_documents({"status": {"$ne": "delivered"}, "created_at": {"$gte": last_week, "$lt": this_week}})
    calls_this_week = calls_col.count_documents({"$or": [{"created_at": {"$gte": this_week}}, {"started_at": {"$gte": this_week}}]})
    calls_last_week = calls_col.count_documents({"$or": [{"created_at": {"$gte": last_week, "$lt": this_week}}, {"started_at": {"$gte": last_week, "$lt": this_week}}]})

    total_tickets = complaints_col.count_documents({})
    breached = complaints_col.count_documents({"$or": [{"sla_breached": True}, {"sla_due": {"$lt": now}, "status": {"$nin": resolved_statuses}}]})
    total_customers = customers_col.count_documents({})
    new_customers = customers_col.count_documents({"$or": [{"created_at": {"$gte": datetime(now.year, now.month, 1)}}, {"date_registered": {"$gte": datetime(now.year, now.month, 1)}}]})

    resolution_pipeline = [
        {"$match": {"status": {"$in": resolved_statuses}, "date_closed": {"$type": "date"}}},
        {"$project": {"hours": {"$divide": [{"$subtract": ["$date_closed", {"$ifNull": ["$date_reported", "$created_at"]}]}, 3600000]}}},
        {"$match": {"hours": {"$gte": 0}}}, {"$group": {"_id": None, "average": {"$avg": "$hours"}}},
    ]
    resolution_row = next(complaints_col.aggregate(resolution_pipeline), None)

    trend = []
    for offset in range(7):
        start = today - timedelta(days=6 - offset); end = start + timedelta(days=1)
        opened = complaints_col.count_documents({"$or": [{"date_reported": {"$gte": start, "$lt": end}}, {"created_at": {"$gte": start, "$lt": end}}]})
        resolved = complaints_col.count_documents({"status": {"$in": resolved_statuses}, "date_closed": {"$gte": start, "$lt": end}})
        trend.append({"day": start.strftime("%a"), "opened": opened, "resolved": resolved})

    ticket_rows = list(complaints_col.find({}).sort([("date_reported", -1), ("created_at", -1)]).limit(5))
    customers = _customer_map(ticket_rows)
    recent_tickets = [_ticket(row, customers.get(str(row.get("customer_id")))) for row in ticket_rows]
    recent_calls = [_call(row) for row in calls_col.find({}).sort([("created_at", -1), ("started_at", -1)]).limit(5)]

    activity = []
    for row in ticket_rows:
        occurred = _dt(row.get("updated_at") or row.get("date_reported") or row.get("created_at")) or now
        activity.append({"id": f"ticket:{row.get('_id')}", "text": f"{row.get('ticket_no') or 'Ticket'} · {STATUS_TO_UI.get(row.get('status'), row.get('status') or 'New')}", "sub": row.get("subject") or row.get("customer_name") or "Customer issue", "type": "ticket", "occurredAt": occurred.isoformat()})
    for row in list(calls_col.find({}).sort([("created_at", -1), ("started_at", -1)]).limit(5)):
        occurred = _dt(row.get("created_at") or row.get("started_at")) or now
        activity.append({"id": f"call:{row.get('_id')}", "text": f"{row.get('type') or 'Call'} call · {row.get('outcome') or 'Pending'}", "sub": row.get("customer_name") or row.get("purpose") or "Customer call", "type": "call", "occurredAt": occurred.isoformat()})
    activity.sort(key=lambda item: item["occurredAt"], reverse=True)

    payload = {"ok": True, "metrics": {
        "openTickets": open_tickets, "openTicketsDelta": _dashboard_change(current_opened, previous_opened),
        "activeDeliveries": active_deliveries, "activeDeliveriesDelta": _dashboard_change(current_deliveries, previous_deliveries),
        "callsToday": calls_today, "callsDelta": _dashboard_change(calls_this_week, calls_last_week),
        "csatScore": 0, "surveyCount": 0,
        "slaCompliance": round((total_tickets - breached) * 100 / total_tickets, 1) if total_tickets else 0,
        "avgResolutionHours": round(float((resolution_row or {}).get("average") or 0), 1),
        "totalCustomers": total_customers, "newCustomersThisMonth": new_customers,
    }, "ticketTrend": trend, "recentTickets": recent_tickets, "recentCalls": recent_calls, "activity": activity[:8], "generatedAt": now.isoformat()}
    if not restricted:
        _DASHBOARD_CACHE.update(payload=payload, expires_at=now_ts + _DASHBOARD_CACHE_SECONDS)
    return jsonify(payload)


def _payment(doc):
    created = _dt(doc.get("created_at") or doc.get("timestamp") or doc.get("date"))
    raw_date = doc.get("date")
    date = created.strftime("%d %b %Y") if created else str(raw_date or "-")
    tracking = receipt_tracking(doc)
    for key in ("payment_recorded_at", "printed_at"):
        if isinstance(tracking.get(key), datetime):
            tracking[key] = tracking[key].isoformat() + "Z"
    for record in tracking["receipt_prints"].values():
        if isinstance(record.get("printed_at"), datetime):
            record["printed_at"] = record["printed_at"].isoformat() + "Z"
    return {
        **tracking,
        "id": doc.get("receipt_no") or doc.get("payment_no") or f"PAY-{str(doc.get('_id', ''))[-8:].upper()}",
        "amount": round(float(doc.get("amount") or 0), 2),
        "method": doc.get("method") or "Not specified",
        "date": date,
        "time": doc.get("time") or (created.strftime("%I:%M %p") if created else ""),
        "type": str(doc.get("payment_type") or "PRODUCT").title(),
        "product": doc.get("product_name") or "",
        "status": "Paid",
    }


def _document(doc):
    created = _dt(doc.get("created_at")) or datetime.utcnow()
    return {**attribution(doc), "id": str(doc.get("_id", "")), "name": doc.get("name") or "Customer document",
            "type": doc.get("type") or "Image", "url": doc.get("url") or "", "size": doc.get("size") or "",
            "uploadedBy": doc.get("uploaded_by_name") or "Customer Support", "date": created.strftime("%d %b %Y")}


def _task(doc, customer=None, assignee=None):
    customer, assignee = customer or {}, assignee or {}
    due = _dt(doc.get("due_date"))
    raw_status = str(doc.get("status") or "pending").strip().lower().replace("_", " ")
    status = {"in progress": "In Progress", "completed": "Completed", "overdue": "Overdue"}.get(raw_status, "Pending")
    if status not in {"Completed", "Overdue"} and due and due.date() < datetime.utcnow().date():
        status = "Overdue"
    name = "All Agents" if doc.get("user_id") == "all" else assignee.get("name") or assignee.get("username") or doc.get("assignee_name") or doc.get("target_name") or "Unassigned"
    initials = "".join(part[:1] for part in name.split()[:2]).upper() or "UA"
    return {**attribution(doc), "_id": str(doc.get("_id", "")), "id": doc.get("task_no") or f"TSK-{str(doc.get('_id', ''))[-6:].upper()}",
            "title": doc.get("title") or doc.get("message") or "Assigned task", "description": doc.get("description") or doc.get("message") or "",
            "customer": customer.get("name") or doc.get("customer_name") or "—", "customerId": str(doc.get("customer_id") or ""),
            "assignee": name, "assigneeId": str(doc.get("assignee_id") or (doc.get("user_id") if doc.get("user_id") != "all" else "") or ""),
            "assigneeInitials": initials, "dueDate": due.strftime("%Y-%m-%d") if due else "",
            "priority": str(doc.get("priority") or "Medium").title(), "status": status,
            "category": doc.get("category") or "Support", "relatedTo": doc.get("related_to") or "",
            "kind": doc.get("kind") or "", "deliveryFollowupId": str(doc["_id"]) if doc.get("kind") == "delivery_confirmation" else ""}


@customer_support_operations_bp.get('/tasks/counts')
@role_required('customer_support')
def support_task_counts():
    from services.support_task_queries import task_counts
    return jsonify(ok=True, counts=task_counts(tasks_col))


def _task_rows():
    return list(tasks_col.find({}).sort([("timestamp", -1), ("created_at", -1)]).limit(1000))


def _hydrate_tasks(rows):
    customer_ids, user_ids = [], []
    for row in rows:
        if _oid(row.get("customer_id")): customer_ids.append(_oid(row.get("customer_id")))
        candidate = row.get("assignee_id") or row.get("user_id")
        if _oid(candidate): user_ids.append(_oid(candidate))
    customers = {str(item["_id"]): item for item in customers_col.find({"_id": {"$in": customer_ids}}, {"name": 1})} if customer_ids else {}
    users = {str(item["_id"]): item for item in users_col.find({"_id": {"$in": user_ids}}, {"name": 1, "username": 1})} if user_ids else {}
    return [_task(row, customers.get(str(row.get("customer_id"))), users.get(str(row.get("assignee_id") or row.get("user_id")))) for row in rows]


def _submitted_card(doc, product_images=None):
    product = doc.get("product") or {}
    created = _dt(doc.get("created_at")); updated = _dt(doc.get("updated_at")) or created
    status = str(doc.get("status") or "pending").lower()
    if status not in {"pending", "packaging", "delivering", "delivered"}: status = "pending"
    labels = {"pending": "Submitted", "packaging": "Packaging", "delivering": "Delivering", "delivered": "Delivered"}
    image = str(product.get("image_url") or product.get("image") or "")
    if not image and product.get("name"):
        found = (product_images.get(product.get("name"), {}) if product_images is not None else products_col.find_one({"name": product.get("name")}, {"image_url": 1}, sort=[("created_at", -1)])) or {}
        image = found.get("image_url") or ""
    try: total = float(str(doc.get("product_total") or product.get("total") or 0).replace(",", ""))
    except Exception: total = 0.0
    try: paid = float(str(doc.get("total_paid_selected_product") or 0).replace(",", ""))
    except Exception: paid = 0.0
    try: quantity = int(doc.get("qty") or product.get("quantity") or 1)
    except Exception: quantity = 1
    history = []
    for event in doc.get("status_history") or []:
        when = _dt(event.get("timestamp") or event.get("at"))
        event_status = str(event.get("status") or event.get("to") or "pending").lower()
        history.append({"status": event_status, "label": labels.get(event_status, event_status.title()), "actorName": event.get("actor_name") or event.get("by") or "",
                        "actorRole": event.get("actor_role") or event.get("role") or "", "timestamp": when.isoformat() if when else "", "notes": event.get("notes") or ""})
    return {**attribution(doc), "id": str(doc.get("_id") or ""), "customerId": str(doc.get("customer_id") or ""), "customerName": doc.get("customer_name") or "Unknown Customer",
            "customerPhone": doc.get("customer_phone") or "", "productName": product.get("name") or product.get("package_name") or "Product", "productImage": image,
            "purchaseType": doc.get("purchase_type") or "", "quantity": quantity, "productTotal": total,
            "amountPaid": paid, "amountLeft": max(round(total - paid, 2), 0), "branch": doc.get("manager_branch") or "", "agentName": doc.get("agent_name") or "",
            "status": status, "statusLabel": labels[status], "submittedAt": created.isoformat() if created else "", "updatedAt": updated.isoformat() if updated else "",
            "daysWaiting": max((datetime.utcnow() - created).days, 0) if created else 0, "source": doc.get("source") or "", "history": history}


@customer_support_operations_bp.get("/directory/customers")
@role_required("customer_support")
def search_customers():
    term = (request.args.get("q") or "").strip()
    query = {}
    if term:
        rx = re.escape(term)
        query = {"$or": [{"name": {"$regex": rx, "$options": "i"}}, {"phone_number": {"$regex": rx, "$options": "i"}}]}
    rows = customers_col.find(query, {"name": 1, "phone_number": 1, "location": 1, "image_url": 1, "agent_branch": 1, "branch": 1}).sort("name", 1).limit(15)
    return jsonify(ok=True, customers=[{"id": str(r["_id"]), "name": r.get("name", ""), "phone": r.get("phone_number", ""), "location": r.get("location", ""), "imageUrl": r.get("image_url", ""), "branch": r.get("agent_branch") or r.get("branch") or ""} for r in rows])


@customer_support_operations_bp.get("/directory/agents")
@role_required("customer_support")
def search_agents():
    term = (request.args.get("q") or "").strip()
    query = {"role": "agent"}
    if term:
        rx = re.escape(term)
        query["$or"] = [{"name": {"$regex": rx, "$options": "i"}}, {"branch": {"$regex": rx, "$options": "i"}}]
    rows = users_col.find(query, {"name": 1, "branch": 1, "image_url": 1}).sort([("branch", 1), ("name", 1)]).limit(30)
    return jsonify(ok=True, agents=[{"id": str(r["_id"]), "name": r.get("name", ""), "branch": r.get("branch", ""), "imageUrl": r.get("image_url", "")} for r in rows])


@customer_support_operations_bp.route("/tickets", methods=["GET", "POST"])
@role_required("customer_support")
def tickets():
    if request.method == "GET":
        query = _ticket_filters()
        try: page = max(int(request.args.get("page") or 1), 1)
        except ValueError: page = 1
        try: per_page = max(1, min(int(request.args.get("per_page") or 10), 100))
        except ValueError: per_page = 10
        total = complaints_col.count_documents(query)
        rows = list(complaints_col.find(query).sort([("date_reported", -1), ("created_at", -1)]).skip((page - 1) * per_page).limit(per_page))
        customers = _customer_map(rows)
        all_scope = list(complaints_col.find({}, {"status": 1, "priority": 1, "branch": 1, "assigned_to_name": 1, "issue_type": 1, "sla_due": 1}))
        counts = {}
        for row in all_scope:
            status = STATUS_TO_UI.get(row.get("status"), row.get("status") or "New")
            counts[status] = counts.get(status, 0) + 1
        return jsonify(ok=True, tickets=[_ticket(row, customers.get(str(row.get("customer_id")))) for row in rows], pagination={"page": page, "per_page": per_page, "total": total, "total_pages": max(math.ceil(total / per_page), 1)}, counts=counts,
                       sla_breached=sum(1 for row in all_scope if _dt(row.get("sla_due")) and _dt(row.get("sla_due")) < datetime.utcnow() and row.get("status") not in {"Resolved", "Closed"}),
                       filters={"branches": sorted({r.get("branch") for r in all_scope if r.get("branch")}), "owners": sorted({r.get("assigned_to_name") for r in all_scope if r.get("assigned_to_name")}), "issues": sorted({r.get("issue_type") for r in all_scope if r.get("issue_type")})})
    data = request.get_json(silent=True) or {}
    customer = customers_col.find_one({"_id": _oid(data.get("customer_id"))})
    agent = users_col.find_one({"_id": _oid(data.get("agent_id")), "role": "agent"})
    if not customer or not agent:
        return jsonify(ok=False, message="Select a valid customer and agent."), 400
    subject, description = str(data.get("subject") or "").strip(), str(data.get("description") or "").strip()
    if not subject or not description:
        return jsonify(ok=False, message="Subject and description are required."), 400
    try:
        sla_hours = max(1, min(int(data.get("sla_hours") or 24), 720))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="SLA must be a valid number of hours."), 400
    now = datetime.utcnow()
    prefix = _reference_prefix('INC', now)
    last = complaints_col.find_one({"ticket_no": {"$regex": f"^{prefix}"}}, sort=[("ticket_no", -1)])
    try: seq = int(last["ticket_no"].split("-")[-1]) + 1 if last else 1
    except Exception: seq = 1
    doc = {"ticket_no": f"{prefix}{seq:03d}", "date_reported": now, "created_at": now, "updated_at": now,
           "subject": subject, "description": description, "customer_id": customer["_id"], "customer_name": customer.get("name", ""),
           "customer_phone": customer.get("phone_number", ""), "branch": agent.get("branch") or customer.get("agent_branch") or customer.get("branch") or "",
           "assigned_to_id": agent["_id"], "assigned_to_id_str": str(agent["_id"]), "assigned_to_name": agent.get("name", ""),
           "status": "Assigned", "issue_type": data.get("issue_type") if data.get("issue_type") in ISSUE_TYPES else "General Enquiry",
           "channel": data.get("channel") if data.get("channel") in CHANNELS else "Call", "priority": data.get("priority") if data.get("priority") in PRIORITIES else "Medium",
           "sla_hours": sla_hours, "sla_days": max(sla_hours / 24, 1 / 24), "sla_due": now + timedelta(hours=sla_hours), "sla_breached": False,
           "created_by": str(get_current_identity().get("user_id") or ""), "created_by_role": "customer_support", "sms_events": []}
    result = complaints_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return jsonify(ok=True, ticket=_ticket(doc)), 201


@customer_support_operations_bp.get("/tickets/export")
@role_required("customer_support")
def export_tickets():
    rows = list(complaints_col.find(_ticket_filters()).sort([("date_reported", -1), ("created_at", -1)]))
    customers = _customer_map(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ticket ID", "Customer", "Phone", "Location", "Customer Branch", "Issue Type", "Priority", "Status", "Assigned Agent", "SLA Due"])
    for row in rows:
        item = _ticket(row, customers.get(str(row.get("customer_id"))))
        writer.writerow([item["id"], item["customer"], item["phone"], item["location"], item["customerBranch"], item["issueType"], item["priority"], item["status"], item["owner"], item["dueDate"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=tickets-{datetime.utcnow():%Y%m%d}.csv"})


@customer_support_operations_bp.route("/tickets/<ticket_id>", methods=["GET", "PATCH"])
@role_required("customer_support")
def ticket_detail(ticket_id):
    query = _ticket_query(ticket_id)
    doc = complaints_col.find_one(query)
    if not doc:
        return jsonify(ok=False, message="Ticket not found."), 404
    if request.method == "GET":
        customer = customers_col.find_one({"_id": {"$in": [doc.get("customer_id"), _oid(doc.get("customer_id"))]}}) if doc.get("customer_id") else None
        return jsonify(ok=True, ticket=_ticket(doc, customer))
    data = request.get_json(silent=True) or {}
    fields = {}
    if "status" in data:
        allowed = {"New", "Open", "Assigned", "In Progress", "Pending", "Resolved", "Closed"}
        if data["status"] not in allowed:
            return jsonify(ok=False, message="Invalid ticket status."), 400
        fields["status"] = STATUS_FROM_UI.get(data["status"], data["status"])
        if data["status"] in {"Resolved", "Closed"}:
            fields["date_closed"] = datetime.utcnow()
    for key in ("root_cause", "resolution_notes", "closure_notes"):
        if key in data:
            fields[key] = str(data[key] or "").strip()
    if "agent_id" in data:
        agent = users_col.find_one({"_id": _oid(data.get("agent_id")), "role": "agent"})
        if not agent:
            return jsonify(ok=False, message="Select a valid agent."), 400
        fields.update({"assigned_to_id": agent["_id"], "assigned_to_id_str": str(agent["_id"]), "assigned_to_name": agent.get("name", ""), "branch": agent.get("branch") or doc.get("branch") or "", "status": "Assigned"})
    fields["updated_at"] = datetime.utcnow()
    mutation = {"$set": fields}
    if fields.get("status") in {"Resolved", "Closed"} and doc.get("status") not in {"Resolved", "Closed"}:
        identity = get_current_identity()
        mutation["$push"] = {"resolution_history": {"actor_id": str(identity.get("user_id") or ""),
            "actor_name": identity.get("name") or "Customer Support", "actor_role": identity.get('role') or "customer_support",
            "at": fields["updated_at"], "status": fields["status"], "notes": fields.get("resolution_notes") or fields.get("closure_notes") or ""}}
    complaints_col.update_one(query, mutation)
    return jsonify(ok=True, ticket=_ticket(complaints_col.find_one(query)))


@customer_support_operations_bp.post("/tickets/<ticket_id>/updates")
@role_required("customer_support")
def add_ticket_update(ticket_id):
    query = _ticket_query(ticket_id)
    if not complaints_col.find_one(query, {"_id": 1}):
        return jsonify(ok=False, message="Ticket not found."), 404
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    kind = data.get("kind") if data.get("kind") in {"reply", "note"} else "note"
    if not text:
        return jsonify(ok=False, message="Enter a message."), 400
    identity = get_current_identity()
    user = users_col.find_one({"_id": _oid(identity.get("user_id"))}, {"name": 1}) or {}
    update = {"kind": kind, "text": text, "author": user.get("name") or identity.get("name") or "Customer Support", "created_at": datetime.utcnow()}
    complaints_col.update_one(query, {"$push": {"updates": update}, "$set": {"updated_at": datetime.utcnow()}})
    update["created_at"] = update["created_at"].isoformat()
    return jsonify(ok=True, update=update), 201


@customer_support_operations_bp.get("/tickets/<ticket_id>/calls")
@role_required("customer_support")
def ticket_calls(ticket_id):
    ticket = complaints_col.find_one(_ticket_query(ticket_id), {"ticket_no": 1})
    if not ticket:
        return jsonify(ok=False, message="Ticket not found."), 404
    values = [ticket.get("ticket_no"), ticket_id]
    rows = calls_col.find({"linked_ticket": {"$in": [value for value in values if value]}}).sort("created_at", -1)
    return jsonify(ok=True, calls=[_call(row) for row in rows])


@customer_support_operations_bp.get("/customers/<customer_id>/tickets")
@role_required("customer_support")
def customer_ticket_options(customer_id):
    """Return a small list for customer-dependent ticket selectors."""
    oid = _oid(customer_id)
    values = [customer_id] + ([oid, str(oid)] if oid else [])
    rows = complaints_col.find(
        {"customer_id": {"$in": values}},
        {"ticket_no": 1, "subject": 1, "description": 1, "status": 1, "date_reported": 1, "created_at": 1},
    ).sort([("date_reported", -1), ("created_at", -1)]).limit(100)
    return jsonify(ok=True, tickets=[{
        "id": row.get("ticket_no") or str(row.get("_id") or ""),
        "subject": row.get("subject") or row.get("description") or "Customer issue",
        "status": STATUS_TO_UI.get(row.get("status"), row.get("status") or "New"),
    } for row in rows])


@customer_support_operations_bp.get("/customers/<customer_id>/activity")
@role_required("customer_support")
def customer_support_activity(customer_id):
    oid = _oid(customer_id)
    values = [customer_id] + ([oid, str(oid)] if oid else [])
    ticket_rows = list(complaints_col.find({"customer_id": {"$in": values}}).sort("date_reported", -1).limit(200))
    call_rows = list(calls_col.find({"customer_id": {"$in": values}}).sort("created_at", -1).limit(200))
    payment_rows = list(payments_col.find({"customer_id": {"$in": values}, "payment_type": {"$nin": ["WITHDRAWAL", "SUSU"]}}).sort("created_at", -1).limit(500))
    document_rows = list(documents_col.find({"customer_id": {"$in": values}}).sort("created_at", -1).limit(200))
    delivery_rows = list(packages_col.find({"customer_id": {"$in": values}}).sort("created_at", -1).limit(200))
    followup_rows = list(followups_col.find({"customer_id": {"$in": values}}).sort("scheduled_at", -1).limit(200))
    message_rows = list(messages_col.find({"customer_id": {"$in": values}}).sort("created_at", -1).limit(200))
    manual_rows = list(manual_activities_col.find({"customer_id": {"$in": values}}).sort("occurred_at", -1).limit(200))
    edit_rows = list(customer_detail_edits_col.find({"customer_id": {"$in": values}}).sort("created_at", -1).limit(10))
    customer = customers_col.find_one({"_id": oid}) if oid else None
    payments = [_payment(row) for row in payment_rows]
    activities = []
    for row, item in zip(payment_rows, payments): activities.append({**attribution(row), "id": f"payment:{row['_id']}", "type": "payment", "title": "Payment received", "detail": f"GHS {item['amount']:,.2f} via {item['method']}", "status": "Completed", "occurredAt": (_dt(row.get("created_at") or row.get("date")) or datetime.utcnow()).isoformat()})
    for row in delivery_rows:
        item = _submitted_card(row); activities.append({**attribution(row), "id": f"delivery:{row['_id']}:{item['status']}", "type": "delivery", "title": f"Card {item['statusLabel'].lower()}", "detail": f"{item['productName']} · {item['branch'] or 'Branch not assigned'}", "status": item["statusLabel"], "occurredAt": item["updatedAt"] or item["submittedAt"]})
    for row in ticket_rows:
        item = _ticket(row, customer); activities.append({**attribution(row), "id": f"ticket:{row['_id']}", "type": "ticket", "title": f"Ticket {item['status'].lower()}", "detail": f"{item['id']} · {item['subject']}", "status": item["status"], "occurredAt": (_dt(row.get("updated_at") or row.get("date_reported")) or datetime.utcnow()).isoformat()})
    for row in call_rows:
        item = _call(row); activities.append({**attribution(row), "id": f"call:{row['_id']}", "type": "call", "title": f"{item['type']} call logged", "detail": f"{item['id']} - {item['purpose']}\n{interaction_summary({**row, 'channel': 'Call'})}\nPerformed by {row.get('officer_name') or 'Not recorded'}", "performedBy": {"id": str(row.get("officer_id") or ""), "name": row.get("officer_name") or "Not recorded", "role": (row.get("support_recorded_by") or {}).get("role") or "customer_support"}, "status": item["outcome"], "occurredAt": (_dt(row.get("started_at") or row.get("created_at")) or datetime.utcnow()).isoformat()})
    for row in followup_rows:
        when = _dt(row.get("scheduled_at")) or datetime.utcnow(); activities.append({**attribution(row), "id": f"followup:{row['_id']}", "recordId": str(row["_id"]), "type": "followup", "title": "Follow-up completed" if row.get("status") == "done" else "Follow-up scheduled", "detail": f"{row.get('purpose') or 'Customer follow-up'} · {row.get('notes') or 'No notes'}", "status": "Done" if row.get("status") == "done" else "Pending", "occurredAt": when.isoformat(), "canComplete": row.get("status") != "done"})
    for row in message_rows:
        channel = str(row.get("channel") or "SMS").upper(); activities.append({**attribution(row), "id": f"message:{row['_id']}", "type": "message", "title": f"{channel} reminder {row.get('status') or 'sent'}", "detail": row.get("message") or "", "status": str(row.get("status") or "Sent").title(), "occurredAt": (_dt(row.get("created_at")) or datetime.utcnow()).isoformat()})
    for row in manual_rows:
        occurred = _dt(row.get("occurred_at") or row.get("created_at")) or datetime.utcnow()
        detail = str(row.get("description") or "")
        detail += "\n" + interaction_summary(row) if row.get("channel") else ""
        officer = str(row.get("created_by_name") or "Customer Support")
        activities.append({**attribution(row), "id": f"manual:{row['_id']}", "type": "manual", "title": row.get("title") or "Manual activity", "detail": f"{detail}\nPerformed by {officer} ({row.get('created_by_role') or 'customer_support'})", "performedBy": {"id": str(row.get("created_by") or ""), "name": officer, "role": row.get("created_by_role") or "customer_support"}, "interaction": {**{key: row.get(key) or "" for key in INTERACTION_FIELDS}, "channel": row.get("channel") or "", "next_followup_at": (_dt(row.get("next_followup_at"))).isoformat() if _dt(row.get("next_followup_at")) else ""}, "status": row.get("category") or "Note", "occurredAt": occurred.isoformat()})
    for row in edit_rows:
        fields = ", ".join(str(change.get("label") or change.get("field") or "detail") for change in (row.get("changes") or []))
        officer = str(row.get("edited_by_name") or "Customer Support")
        activities.append({**attribution(row), "id": f"customer-edit:{row['_id']}", "type": "customer_edit", "title": "Customer details updated", "detail": f"Updated {fields} · Edited by {officer}", "status": f"Edit {row.get('edit_number') or ''}/2", "occurredAt": (_dt(row.get("created_at")) or datetime.utcnow()).isoformat()})
    for row in payment_rows:
        for index, event in enumerate(row.get("sms_events") or []): activities.append({**attribution(row), "id": f"payment-sms:{row['_id']}:{index}", "type": "message", "title": f"Payment SMS {event.get('status') or 'sent'}", "detail": event.get("message") or "Payment confirmation message", "status": str(event.get("status") or "Sent").title(), "occurredAt": (_dt(event.get("created_at")) or _dt(row.get("created_at")) or datetime.utcnow()).isoformat()})
    if customer and str(customer.get("status") or "").lower() in {"closed", "inactive", "archived"}:
        when = _dt(customer.get("closed_at") or customer.get("updated_at")) or datetime.utcnow(); activities.append({**attribution(row), "id": f"customer:{customer['_id']}:closed", "type": "account", "title": "Customer account closed", "detail": "Customer status changed to closed", "status": "Closed", "occurredAt": when.isoformat()})
    activities.sort(key=lambda item: item.get("occurredAt") or "", reverse=True)
    return jsonify(ok=True, tickets=[_ticket(row, customer) for row in ticket_rows], calls=[_call(row) for row in call_rows],
                   payments=payments, paymentSummary={"totalPaid": round(sum(row["amount"] for row in payments), 2), "count": len(payments)},
                   documents=[_document(row) for row in document_rows], activities=activities, pendingFollowups=sum(1 for row in followup_rows if row.get("status") != "done"),
                   editCount=min(int((customer or {}).get("customer_support_edit_count") or 0), 2))


@customer_support_operations_bp.patch("/customers/<customer_id>/details")
@role_required("customer_support")
def edit_customer_details(customer_id):
    oid = _oid(customer_id)
    current = customers_col.find_one({"_id": oid}) if oid else None
    if not current: return jsonify(ok=False, message="Customer not found."), 404
    if int(current.get("customer_support_edit_count") or 0) >= 2:
        return jsonify(ok=False, message="This customer's details have already been edited twice."), 409
    data = request.get_json(silent=True) or {}
    specs = {"phone_number": ("Phone", 30), "email": ("Email", 160), "location": ("Location", 200), "occupation": ("Occupation", 120)}
    updates = {}; changes = []
    for field, (label, limit) in specs.items():
        if field not in data: continue
        value = str(data.get(field) or "").strip()
        if len(value) > limit: return jsonify(ok=False, message=f"{label} is too long."), 400
        old = str(current.get(field) or "").strip()
        if value != old: updates[field] = value; changes.append({"field": field, "label": label, "before": old, "after": value})
    if updates.get("email") and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", updates["email"]):
        return jsonify(ok=False, message="Enter a valid email address."), 400
    if updates.get("phone_number") and len(re.sub(r"\D", "", updates["phone_number"])) < 9:
        return jsonify(ok=False, message="Enter a valid phone number."), 400
    if not changes: return jsonify(ok=False, message="No customer details were changed."), 400
    updated = customers_col.find_one_and_update(
        {"_id": oid, "$or": [{"customer_support_edit_count": {"$lt": 2}}, {"customer_support_edit_count": {"$exists": False}}]},
        {"$set": {**updates, "updated_at": datetime.utcnow()}, "$inc": {"customer_support_edit_count": 1}}, return_document=ReturnDocument.AFTER)
    if not updated: return jsonify(ok=False, message="This customer's details have already been edited twice."), 409
    identity = get_current_identity(); officer = users_col.find_one({"_id": _oid(identity.get("user_id"))}, {"name": 1}) or {}
    edit_number = int(updated.get("customer_support_edit_count") or 0)
    customer_detail_edits_col.insert_one({"customer_id": oid, "customer_name": current.get("name") or "", "changes": changes,
        "edit_number": edit_number, "created_at": datetime.utcnow(), "edited_by": str(identity.get("user_id") or ""),
        "edited_by_name": officer.get("name") or identity.get("name") or "Customer Support", "edited_by_role": "customer_support"})
    return jsonify(ok=True, message="Customer details updated successfully.", editCount=edit_number,
                   customer={"phone": updated.get("phone_number") or "", "email": updated.get("email") or "", "location": updated.get("location") or "", "occupation": updated.get("occupation") or ""})


INTERACTION_FIELDS = ("branch", "salesperson", "delivery_status", "feedback", "issues", "requested_products", "action_taken", "next_followup")


def interaction_details(data, occurred):
    channel = str(data.get("channel") or "Field visit").strip()
    if channel not in {"Call", "Field visit", "Branch visit", "Email", "WhatsApp", "SMS", "Other"}:
        raise ValueError("Select a valid interaction channel.")
    details = {key: str(data.get(key) or "").strip() for key in INTERACTION_FIELDS}
    if any(len(value) > 2000 for value in details.values()):
        raise ValueError("Interaction fields must be 2,000 characters or fewer.")
    if data.get("interaction_record"):
        for key in ("branch", "delivery_status", "feedback", "action_taken", "next_followup"):
            if not details[key]:
                raise ValueError("Branch, delivery status, feedback, action taken and next follow-up (or a reason none is needed) are required.")
    if details["delivery_status"] and details["delivery_status"] not in {"Not ordered", "Pending", "Partly delivered", "Delivered", "Issue", "Unknown"}:
        raise ValueError("Select a valid delivery status.")
    next_at = None
    if data.get("next_followup_at"):
        from customer_support_backend.targets_api import parse_time
        next_at = parse_time(data["next_followup_at"])
        if next_at <= occurred or next_at < datetime.utcnow():
            raise ValueError("Next follow-up must be after the interaction and in the future.")
    return {**details, "channel": channel, "next_followup_at": next_at}


def interaction_summary(row):
    labels = {"channel": "Channel", "branch": "Branch", "salesperson": "Salesperson", "delivery_status": "Delivery", "feedback": "Feedback", "issues": "Issues", "requested_products": "Requested products", "action_taken": "Action taken", "next_followup": "Next follow-up"}
    details = [f"{label}: {row[key]}" for key, label in labels.items() if row.get(key)]
    when = _dt(row.get("next_followup_at"))
    if when: details.append("Next follow-up date: " + when.isoformat())
    return "\n".join(details)


@customer_support_operations_bp.post("/customers/<customer_id>/activities")
@role_required("customer_support")
def log_customer_activity(customer_id):
    oid = _oid(customer_id)
    customer = customers_col.find_one({"_id": oid}, {"name": 1}) if oid else None
    if not customer: return jsonify(ok=False, message="Customer not found."), 404
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip(); description = str(data.get("description") or "").strip()
    category = str(data.get("category") or "Note").strip().title()
    allowed_categories = {"Note", "Interaction", "Complaint", "Visit", "Email", "Other"}
    if not title or not description: return jsonify(ok=False, message="Title and description are required."), 400
    if len(title) > 120 or len(description) > 2000: return jsonify(ok=False, message="Activity text is too long."), 400
    if category not in allowed_categories: return jsonify(ok=False, message="Select a valid activity category."), 400
    from customer_support_backend.targets_api import parse_time
    try:
        occurred = parse_time(data["occurred_at"]) if data.get("occurred_at") else datetime.utcnow()
    except ValueError as error:
        return jsonify(ok=False, message=str(error)), 400
    if occurred > datetime.utcnow() + timedelta(minutes=5): return jsonify(ok=False, message="Activity time cannot be in the future."), 400
    identity = get_current_identity()
    officer = users_col.find_one({"_id": _oid(identity.get("user_id"))}, {"name": 1}) or {}
    doc = {"customer_id": oid, "customer_name": customer.get("name") or "", "title": title, "description": description,
           "category": category, "occurred_at": occurred, "created_at": datetime.utcnow(), "created_by": str(identity.get("user_id") or ""),
           "created_by_name": officer.get("name") or identity.get("name") or "Customer Support", "created_by_role": "customer_support"}
    if category == "Interaction":
        try:
            doc.update(interaction_details(data, occurred))
        except ValueError as error:
            return jsonify(ok=False, message=str(error)), 400
    if category == "Interaction" and doc["channel"] == "Field visit":
        from customer_support_backend.targets_api import visits_col, user_values
        visit = visits_col.find_one({"_id": _oid(data.get("field_visit_id")), "officer_id": {"$in": user_values(str(identity.get("user_id") or ""))}})
        if not visit:
            return jsonify(ok=False, message="Select one of your logged field visits for this interaction."), 400
        if not visit["started_at"] <= occurred <= visit["ended_at"]:
            return jsonify(ok=False, message="Interaction time must fall within the selected field visit."), 400
        doc["field_visit_id"] = visit["_id"]
    result = manual_activities_col.insert_one(doc)
    return jsonify(ok=True, id=str(result.inserted_id), message="Activity logged successfully."), 201

@customer_support_operations_bp.post("/customers/<customer_id>/followups")
@role_required("customer_support")
def schedule_customer_followup(customer_id):
    oid = _oid(customer_id); customer = customers_col.find_one({"_id": oid}) if oid else None; data = request.get_json(silent=True) or {}
    if not customer: return jsonify(ok=False, message="Customer not found."), 404
    try: scheduled = datetime.fromisoformat(str(data.get("scheduled_at") or ""))
    except ValueError: return jsonify(ok=False, message="Select a valid follow-up date and time."), 400
    identity = get_current_identity(); doc = {"customer_id": oid, "customer_name": customer.get("name") or "", "scheduled_at": scheduled, "purpose": str(data.get("purpose") or "Customer follow-up"), "notes": str(data.get("notes") or "").strip(), "status": "pending", "created_at": datetime.utcnow(), "created_by": str(identity.get("user_id") or "")}
    result = followups_col.insert_one(doc); return jsonify(ok=True, id=str(result.inserted_id)), 201

@customer_support_operations_bp.patch("/followups/<followup_id>")
@role_required("customer_support")
def complete_customer_followup(followup_id):
    oid = _oid(followup_id)
    if not oid or not followups_col.find_one({"_id": oid}): return jsonify(ok=False, message="Follow-up not found."), 404
    followups_col.update_one({"_id": oid}, {"$set": {"status": "done", "completed_at": datetime.utcnow(), "updated_at": datetime.utcnow()}}); return jsonify(ok=True)

@customer_support_operations_bp.get("/followups/count")
@role_required("customer_support")
def customer_followup_count():
    return jsonify(ok=True, count=followups_col.count_documents({"status": {"$ne": "done"}}))

@customer_support_operations_bp.post("/customers/<customer_id>/reminders")
@role_required("customer_support")
def send_customer_reminder(customer_id):
    oid = _oid(customer_id); customer = customers_col.find_one({"_id": oid}) if oid else None; data = request.get_json(silent=True) or {}
    if not customer: return jsonify(ok=False, message="Customer not found."), 404
    channel, message = str(data.get("channel") or "").lower(), str(data.get("message") or "").strip()
    phone = _normalize_ghana_phone(customer.get("phone_number"))
    if channel not in {"sms", "whatsapp"} or not message or not phone: return jsonify(ok=False, message="A valid phone number, channel, and message are required."), 400
    identity = get_current_identity(); base = {"customer_id": oid, "customer_name": customer.get("name") or "", "phone": phone, "channel": channel, "message": message, "created_at": datetime.utcnow(), "sent_by": str(identity.get("user_id") or "")}
    if channel == "whatsapp":
        messages_col.insert_one({**base, "status": "opened"})
        return jsonify(ok=True, message="WhatsApp reminder prepared.", redirect=f"https://wa.me/{phone}?text={quote(message)}")
    url = f"https://sms.arkesel.com/sms/api?action=send-sms&api_key={ARKESEL_API_KEY}&to={phone}&from=SMARTLIVING&sms={quote(message)}"
    try: response = requests.get(url, timeout=12); sent = response.status_code == 200 and '"code":"ok"' in response.text
    except requests.RequestException: sent = False
    messages_col.insert_one({**base, "status": "sent" if sent else "failed"})
    if not sent: return jsonify(ok=False, message="SMS delivery failed. Please try again."), 502
    return jsonify(ok=True, message="SMS reminder sent.")


@customer_support_operations_bp.post("/customers/<customer_id>/documents")
@role_required("customer_support")
def add_customer_document(customer_id):
    oid = _oid(customer_id)
    if not oid or not customers_col.find_one({"_id": oid}, {"_id": 1}):
        return jsonify(ok=False, message="Customer not found."), 404
    data = request.get_json(silent=True) or {}
    name, url = str(data.get("name") or "").strip(), str(data.get("url") or "").strip()
    if not name or not url.startswith("https://imagedelivery.net/"):
        return jsonify(ok=False, message="A document name and valid uploaded image are required."), 400
    identity = get_current_identity()
    user = users_col.find_one({"_id": _oid(identity.get("user_id"))}, {"name": 1}) or {}
    doc = {"customer_id": oid, "name": name, "type": str(data.get("type") or "Image").strip(), "url": url,
           "size": str(data.get("size") or ""), "cloudflare_image_id": str(data.get("image_id") or ""),
           "uploaded_by": str(identity.get("user_id") or ""), "uploaded_by_name": user.get("name") or identity.get("name") or "Customer Support",
           "created_at": datetime.utcnow()}
    result = documents_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return jsonify(ok=True, document=_document(doc)), 201


@customer_support_operations_bp.route("/tasks", methods=["GET", "POST"])
@role_required("customer_support")
def support_tasks():
    if request.method == "GET":
        if request.args.get('paginated') == '1':
            from services.support_task_queries import task_page
            rows, counts, pagination = task_page(tasks_col, request.args)
            return jsonify(ok=True, tasks=_hydrate_tasks(rows), counts=counts, pagination=pagination)
        items = _hydrate_tasks(_task_rows())
        return jsonify(ok=True, tasks=items, counts={status: sum(1 for item in items if item["status"] == status)
                                                     for status in ("Pending", "In Progress", "Completed", "Overdue")})
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    assignee = users_col.find_one({"_id": _oid(data.get("assignee_id"))}) if data.get("assignee_id") else None
    customer = customers_col.find_one({"_id": _oid(data.get("customer_id"))}) if data.get("customer_id") else None
    if not title or (data.get("assignee_id") and not assignee) or (data.get("customer_id") and not customer):
        return jsonify(ok=False, message="A title and valid selected records are required."), 400
    try:
        due = datetime.strptime(str(data.get("due_date") or ""), "%Y-%m-%d")
    except ValueError:
        return jsonify(ok=False, message="A valid due date is required."), 400
    now = datetime.utcnow()
    prefix = _reference_prefix('TSK', now)
    last = tasks_col.find_one({"task_no": {"$regex": f"^{prefix}"}}, sort=[("task_no", -1)])
    try: sequence = int(last["task_no"].split("-")[-1]) + 1 if last else 1
    except Exception: sequence = 1
    identity = get_current_identity()
    doc = {"task_no": f"{prefix}{sequence:03d}", "title": title, "message": title,
           "description": str(data.get("description") or "").strip(), "customer_id": customer["_id"] if customer else None,
           "customer_name": customer.get("name", "") if customer else "", "assignee_id": assignee["_id"] if assignee else None, "user_id": assignee["_id"] if assignee else None,
           "assignee_name": (assignee.get("name") or assignee.get("username") or "") if assignee else "", "target_type": (assignee.get("role") or "agent") if assignee else "customer_support",
           "branch_name": (assignee.get("branch") or "") if assignee else "", "due_date": due, "priority": data.get("priority") if data.get("priority") in PRIORITIES else "Medium",
           "status": str(data.get("status") or "Pending").lower(), "category": str(data.get("category") or "Support"), "related_to": str(data.get("related_to") or "").strip(),
           "created_by": str(identity.get("user_id") or ""), "created_by_role": identity.get("role") or "customer_support", "timestamp": now, "created_at": now, "updated_at": now}
    result = tasks_col.insert_one(doc); doc["_id"] = result.inserted_id
    return jsonify(ok=True, task=_task(doc, customer, assignee)), 201


@customer_support_operations_bp.route("/tasks/<task_id>", methods=["PATCH", "DELETE"])
@role_required("customer_support")
def support_task_detail(task_id):
    oid = _oid(task_id)
    task = tasks_col.find_one({"_id": oid}) if oid else tasks_col.find_one({"task_no": task_id})
    if not task:
        return jsonify(ok=False, message="Task not found."), 404
    if task.get("kind") == "delivery_confirmation":
        return jsonify(ok=False, message="Use the delivery follow-up review to update this task. Receipt confirmation tasks cannot be deleted or completed manually."), 409
    query = {"_id": task["_id"]}
    if request.method == "DELETE":
        tasks_col.delete_one(query)
        return jsonify(ok=True)
    data = request.get_json(silent=True) or {}; fields = {"updated_at": datetime.utcnow()}
    for source, target in (("title", "title"), ("description", "description"), ("priority", "priority"), ("category", "category"), ("related_to", "related_to")):
        if source in data: fields[target] = str(data[source] or "").strip()
    if "status" in data:
        if data["status"] not in {"Pending", "In Progress", "Completed", "Overdue"}: return jsonify(ok=False, message="Invalid status."), 400
        fields["status"] = data["status"].lower()
        if data["status"] == "Completed": fields["completed_at"] = datetime.utcnow()
    if "due_date" in data:
        try: fields["due_date"] = datetime.strptime(str(data["due_date"]), "%Y-%m-%d")
        except ValueError: return jsonify(ok=False, message="Invalid due date."), 400
    if "assignee_id" in data:
        assignee = users_col.find_one({"_id": _oid(data["assignee_id"])}) if data["assignee_id"] else None
        if data["assignee_id"] and not assignee: return jsonify(ok=False, message="Invalid assigned user."), 400
        fields.update({"assignee_id": assignee["_id"] if assignee else None, "user_id": assignee["_id"] if assignee else None, "assignee_name": (assignee.get("name") or assignee.get("username") or "") if assignee else "", "target_type": (assignee.get("role") or "agent") if assignee else "customer_support", "branch_name": (assignee.get("branch") or "") if assignee else ""})
    if "customer_id" in data:
        customer = customers_col.find_one({"_id": _oid(data["customer_id"])}) if data["customer_id"] else None
        if data["customer_id"] and not customer: return jsonify(ok=False, message="Invalid customer."), 400
        fields.update({"customer_id": customer["_id"] if customer else None, "customer_name": customer.get("name", "") if customer else ""})
    tasks_col.update_one(query, {"$set": fields})
    return jsonify(ok=True, task=_hydrate_tasks([tasks_col.find_one(query)])[0])


@customer_support_operations_bp.get("/tasks/export")
@role_required("customer_support")
def export_support_tasks():
    from flask import stream_with_context
    from itertools import islice
    def generate():
        output = io.StringIO(); writer = csv.writer(output)
        writer.writerow(["Task ID", "Title", "Customer", "Assigned User", "Priority", "Category", "Due Date", "Status", "Related To"])
        yield output.getvalue()
        cursor = tasks_col.find({}).sort([('timestamp', -1), ('created_at', -1), ('_id', -1)]).batch_size(100)
        try:
            while True:
                batch = list(islice(cursor, 100))
                if not batch: break
                output.seek(0); output.truncate(0)
                for item in _hydrate_tasks(batch):
                    writer.writerow([item[key] for key in ('id', 'title', 'customer', 'assignee', 'priority', 'category', 'dueDate', 'status', 'relatedTo')])
                yield output.getvalue()
        finally:
            cursor.close()
    return Response(stream_with_context(generate()), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=tasks-{datetime.utcnow():%Y%m%d}.csv'})



@customer_support_operations_bp.get("/deliveries")
@role_required("customer_support")
def support_deliveries():
    query = {}
    status = str(request.args.get("status") or "all").lower()
    if status in {"pending", "packaging", "delivering", "delivered"}: query["status"] = status
    branch = str(request.args.get("branch") or "all").strip()
    if branch != "all": query["manager_branch"] = branch
    search = str(request.args.get("search") or "").strip()
    if search:
        rx = re.escape(search); query["$or"] = [{"customer_name": {"$regex": rx, "$options": "i"}}, {"customer_phone": {"$regex": rx, "$options": "i"}},
                                                  {"agent_name": {"$regex": rx, "$options": "i"}}, {"product.name": {"$regex": rx, "$options": "i"}}]
    try: page = max(int(request.args.get("page") or 1), 1)
    except ValueError: page = 1
    try: per_page = max(1, min(int(request.args.get("per_page") or 20), 100))
    except ValueError: per_page = 20
    total = packages_col.count_documents(query); pages = max(math.ceil(total / per_page), 1); page = min(page, pages)
    rows = list(packages_col.find(query).sort([("created_at", -1), ("_id", -1)]).skip((page - 1) * per_page).limit(per_page))
    followups = {str(row["package_id"]): row for row in tasks_col.find({"kind": "delivery_confirmation", "package_id": {"$in": [row["_id"] for row in rows]}})}
    names = {(row.get('product') or {}).get('name') for row in rows if not ((row.get('product') or {}).get('image_url') or (row.get('product') or {}).get('image'))}
    images = {}
    for product in products_col.find({'name': {'$in': [name for name in names if name]}}, {'name': 1, 'image_url': 1}).sort('created_at', -1):
        images.setdefault(product['name'], product)
    items = []
    for row in rows:
        item = _submitted_card(row, images)
        task = followups.get(str(row["_id"]))
        review = (task or {}).get("latest_review") or {}
        item["satisfaction"] = {"taskId": str(task["_id"]) if task else "", "status": (task or {}).get("status") or "",
            "rating": review.get("rating"), "receipt": review.get("receipt") or "", "issue": review.get("issue") or ""}
        items.append(item)
    counts = dict.fromkeys(('pending', 'packaging', 'delivering', 'delivered'), 0)
    overall, branches = 0, set()
    for group in packages_col.aggregate([{'$group': {'_id': {'status': '$status', 'branch': '$manager_branch'}, 'count': {'$sum': 1}}}]):
        overall += group['count']
        status_key = str(group['_id'].get('status') or 'pending').lower()
        if status_key in counts:
            counts[status_key] += group['count']
        branch_name = str(group['_id'].get('branch') or '').strip()
        if branch_name:
            branches.add(branch_name)
    return jsonify(ok=True, deliveries=items, counts={'total': overall, 'open': overall - counts['delivered'], **counts},
                   branches=sorted(branches), pagination={'page': page, 'perPage': per_page, 'total': total, 'totalPages': pages})


@customer_support_operations_bp.get("/deliveries/export")
@role_required("customer_support")
def export_support_deliveries():
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Customer", "Phone", "Product", "Quantity", "Branch", "Agent", "Status", "Submitted", "Product Total", "Amount Paid", "Amount Left"])
    for row in packages_col.find({}).sort("created_at", -1):
        item = _submitted_card(row); writer.writerow([item[key] for key in ("customerName", "customerPhone", "productName", "quantity", "branch", "agentName", "statusLabel", "submittedAt", "productTotal", "amountPaid", "amountLeft")])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=deliveries-{datetime.utcnow():%Y%m%d}.csv"})


@customer_support_operations_bp.get("/satisfaction")
@role_required("customer_support")
def satisfaction_analytics():
    now = datetime.utcnow(); start = datetime(now.year, now.month, 1)
    months = []
    for _ in range(5): start = (start - timedelta(days=1)).replace(day=1)
    for offset in range(6):
        month = start
        for _ in range(offset): month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        ticket_rows = list(complaints_col.find({"$or": [{"date_reported": {"$gte": month, "$lt": next_month}}, {"created_at": {"$gte": month, "$lt": next_month}}]}))
        call_rows = list(calls_col.find({"created_at": {"$gte": month, "$lt": next_month}}))
        resolved = sum(1 for row in ticket_rows if str(row.get("status") or "").lower() in {"resolved", "closed"})
        compliant = sum(1 for row in ticket_rows if not row.get("sla_breached") and (not _dt(row.get("sla_due")) or _dt(row.get("sla_due")) >= (_dt(row.get("date_closed")) or now)))
        resolved_calls = sum(1 for row in call_rows if str(row.get("outcome") or "").lower() in {"resolved", "satisfied", "completed", "acknowledged"})
        months.append({"month": month.strftime("%b %Y"), "tickets": len(ticket_rows), "resolved": resolved,
                       "resolutionRate": round(resolved * 100 / len(ticket_rows), 1) if ticket_rows else 0,
                       "slaRate": round(compliant * 100 / len(ticket_rows), 1) if ticket_rows else 0,
                       "calls": len(call_rows), "callResolutionRate": round(resolved_calls * 100 / len(call_rows), 1) if call_rows else 0})
    tickets = list(complaints_col.find({})); calls = list(calls_col.find({})); deliveries = list(packages_col.find({}))
    resolved_tickets = [row for row in tickets if str(row.get("status") or "").lower() in {"resolved", "closed"}]
    breached = sum(1 for row in tickets if row.get("sla_breached") or (_dt(row.get("sla_due")) and _dt(row.get("sla_due")) < (_dt(row.get("date_closed")) or now) and row not in resolved_tickets))
    resolved_calls = sum(1 for row in calls if str(row.get("outcome") or "").lower() in {"resolved", "satisfied", "completed", "acknowledged"})
    delivered = sum(1 for row in deliveries if str(row.get("status") or "").lower() == "delivered")
    branch_data = {}
    for row in tickets:
        name = str(row.get("branch") or "Unassigned"); item = branch_data.setdefault(name, {"branch": name, "tickets": 0, "resolved": 0, "breached": 0})
        item["tickets"] += 1; item["resolved"] += str(row.get("status") or "").lower() in {"resolved", "closed"}
        due, closed = _dt(row.get("sla_due")), _dt(row.get("date_closed"))
        item["breached"] += bool(row.get("sla_breached") or (due and due < (closed or now) and str(row.get("status") or "").lower() not in {"resolved", "closed"}))
    branches = [{**item, "resolutionRate": round(item["resolved"] * 100 / item["tickets"], 1), "slaRate": round((item["tickets"] - item["breached"]) * 100 / item["tickets"], 1)} for item in branch_data.values()]
    branches.sort(key=lambda item: (item["resolutionRate"] + item["slaRate"]), reverse=True)
    agents = {}
    for row in tickets:
        name = str(row.get("assigned_to_name") or "Unassigned"); item = agents.setdefault(name, {"agent": name, "assigned": 0, "resolved": 0, "open": 0})
        item["assigned"] += 1
        if str(row.get("status") or "").lower() in {"resolved", "closed"}: item["resolved"] += 1
        else: item["open"] += 1
    agent_rows = [{**item, "resolutionRate": round(item["resolved"] * 100 / item["assigned"], 1)} for item in agents.values()]
    agent_rows.sort(key=lambda item: (item["resolutionRate"], item["resolved"]), reverse=True)
    issue_counts = {}
    for row in tickets:
        issue = str(row.get("issue_type") or "General Enquiry"); issue_counts[issue] = issue_counts.get(issue, 0) + 1
    return jsonify(ok=True, metrics={"totalTickets": len(tickets), "openTickets": len(tickets) - len(resolved_tickets),
                                      "resolutionRate": round(len(resolved_tickets) * 100 / len(tickets), 1) if tickets else 0,
                                      "slaCompliance": round((len(tickets) - breached) * 100 / len(tickets), 1) if tickets else 0,
                                      "callResolutionRate": round(resolved_calls * 100 / len(calls), 1) if calls else 0,
                                      "deliveryCompletionRate": round(delivered * 100 / len(deliveries), 1) if deliveries else 0},
                   monthly=months, branches=branches, agents=agent_rows, issues=[{"issue": key, "count": value} for key, value in sorted(issue_counts.items(), key=lambda pair: pair[1], reverse=True)])


def _is_resolved_ticket(row):
    return str(row.get("status") or "").lower() in {"resolved", "closed"}


def _is_sla_breached(row, now):
    due, closed = _dt(row.get("sla_due")), _dt(row.get("date_closed"))
    return bool(row.get("sla_breached") or (due and due < (closed or now) and not _is_resolved_ticket(row)))


def _notification_events():
    now = datetime.utcnow(); events = []
    for row in complaints_col.find({}).sort([("updated_at", -1), ("created_at", -1)]).limit(150):
        created = _dt(row.get("updated_at") or row.get("date_reported") or row.get("created_at")) or now
        ticket_no = row.get("ticket_no") or str(row.get("_id")); status = STATUS_TO_UI.get(row.get("status"), row.get("status") or "New")
        breached = _is_sla_breached(row, now); critical = str(row.get("priority") or "").lower() == "critical"
        events.append({"id": f"ticket:{row.get('_id')}:{status}", "type": "ticket", "title": f"{ticket_no} · {status}",
            "message": f"{row.get('subject') or row.get('description') or 'Customer issue'} · {row.get('customer_name') or 'Unknown customer'}",
            "priority": "Critical" if breached else "High" if critical else "Normal", "createdAt": created, "reference": ticket_no, "actionPage": "tickets"})
    for row in tasks_col.find({}).sort([("updated_at", -1), ("timestamp", -1)]).limit(150):
        due = _dt(row.get("due_date")); status = str(row.get("status") or "pending").lower(); overdue = status != "completed" and bool(due and due.date() < now.date())
        if status == "completed": continue
        created = _dt(row.get("updated_at") or row.get("timestamp") or row.get("created_at")) or due or now
        task_no = row.get("task_no") or f"TSK-{str(row.get('_id'))[-6:].upper()}"
        events.append({"id": f"task:{row.get('_id')}:{'overdue' if overdue else status}", "type": "task", "title": f"{task_no} · {'Overdue' if overdue else 'Pending task'}",
            "message": str(row.get("title") or row.get("message") or "Assigned task"), "priority": "High" if overdue else str(row.get("priority") or "Normal").title(),
            "createdAt": created, "reference": task_no, "actionPage": "tasks"})
    for row in calls_col.find({"follow_up": True}).sort("created_at", -1).limit(100):
        follow_date = _dt(row.get("follow_up_date")); created = _dt(row.get("created_at")) or now
        call_no = row.get("call_no") or str(row.get("_id")); due = bool(follow_date and follow_date.date() <= now.date())
        events.append({"id": f"call:{row.get('_id')}:followup", "type": "call", "title": f"Follow-up {'due' if due else 'scheduled'} · {call_no}",
            "message": f"{row.get('customer_name') or 'Customer'} · {row.get('purpose') or 'Call follow-up'}", "priority": "High" if due else "Normal",
            "createdAt": created, "reference": call_no, "actionPage": "calls"})
    for row in packages_col.find({}).sort([("updated_at", -1), ("created_at", -1)]).limit(150):
        created = _dt(row.get("updated_at") or row.get("created_at")) or now; status = str(row.get("status") or "pending").lower()
        labels = {"pending": "Submitted", "packaging": "Packaging", "delivering": "Delivering", "delivered": "Delivered"}; product = row.get("product") or {}
        events.append({"id": f"delivery:{row.get('_id')}:{status}", "type": "delivery", "title": f"Submitted card · {labels.get(status, status.title())}",
            "message": f"{row.get('customer_name') or 'Customer'} · {product.get('name') or product.get('package_name') or 'Product'}",
            "priority": "Normal", "createdAt": created, "reference": str(row.get("_id")), "actionPage": "deliveries"})
    events.sort(key=lambda item: item["createdAt"], reverse=True)
    return events


@customer_support_operations_bp.get("/search")
@role_required("customer_support")
def global_support_search():
    term = str(request.args.get("q") or "").strip()
    if len(term) < 2: return jsonify(ok=True, results=[])
    rx = {"$regex": re.escape(term), "$options": "i"}; results = []
    pages = [("dashboard", "Dashboard"), ("customers", "Customers"), ("tickets", "Tickets"), ("calls", "Calls"), ("tasks", "Tasks"),
             ("deliveries", "Submitted Cards / Deliveries"), ("satisfaction", "Customer Experience Health"), ("reports", "Support Reports"),
             ("notifications", "Notifications"), ("profile", "My Profile")]
    for page, title in pages:
        if term.lower() in f"{title} {page}".lower(): results.append({"type": "page", "title": title, "subtitle": "Open application page", "page": page, "id": page, "query": ""})
    customer_query = {"$or": [{"name": rx}, {"phone_number": rx}, {"customer_no": rx}, {"customer_id": rx}, {"ic": rx}]}
    for row in customers_col.find(customer_query, {"name": 1, "phone_number": 1, "customer_no": 1, "location": 1, "agent_branch": 1, "branch": 1}).limit(6):
        results.append({"type": "customer", "title": row.get("name") or "Customer", "subtitle": " · ".join(filter(None, [row.get("phone_number"), row.get("location"), row.get("agent_branch") or row.get("branch")])), "page": "customers", "id": str(row["_id"]), "query": row.get("phone_number") or row.get("name") or ""})
    for row in complaints_col.find({"$or": [{"ticket_no": rx}, {"subject": rx}, {"customer_name": rx}, {"customer_phone": rx}]}, {"ticket_no": 1, "subject": 1, "customer_name": 1, "status": 1}).limit(6):
        results.append({"type": "ticket", "title": row.get("ticket_no") or "Ticket", "subtitle": " · ".join(filter(None, [row.get("subject"), row.get("customer_name"), STATUS_TO_UI.get(row.get("status"), row.get("status"))])), "page": "tickets", "id": str(row["_id"]), "query": row.get("ticket_no") or row.get("subject") or ""})
    for row in tasks_col.find({"$or": [{"task_no": rx}, {"title": rx}, {"message": rx}, {"customer_name": rx}, {"assignee_name": rx}]}, {"task_no": 1, "title": 1, "message": 1, "assignee_name": 1, "status": 1}).limit(6):
        results.append({"type": "task", "title": row.get("task_no") or f"TSK-{str(row.get('_id'))[-6:].upper()}", "subtitle": " · ".join(filter(None, [row.get("title") or row.get("message"), row.get("assignee_name"), str(row.get("status") or "").title()])), "page": "tasks", "id": str(row["_id"]), "query": row.get("task_no") or row.get("title") or row.get("message") or ""})
    for row in calls_col.find({"$or": [{"call_no": rx}, {"purpose": rx}, {"customer_name": rx}, {"customer_phone": rx}, {"officer_name": rx}]}, {"call_no": 1, "purpose": 1, "customer_name": 1, "outcome": 1}).limit(6):
        results.append({"type": "call", "title": row.get("call_no") or "Call", "subtitle": " · ".join(filter(None, [row.get("purpose"), row.get("customer_name"), row.get("outcome")])), "page": "calls", "id": str(row["_id"]), "query": row.get("call_no") or row.get("purpose") or ""})
    delivery_query = {"$or": [{"customer_name": rx}, {"customer_phone": rx}, {"agent_name": rx}, {"product.name": rx}, {"product.package_name": rx}]}
    for row in packages_col.find(delivery_query, {"customer_name": 1, "customer_phone": 1, "product": 1, "status": 1}).limit(6):
        product = row.get("product") or {}; title = product.get("name") or product.get("package_name") or "Submitted card"
        results.append({"type": "delivery", "title": title, "subtitle": " · ".join(filter(None, [row.get("customer_name"), row.get("customer_phone"), str(row.get("status") or "pending").title()])), "page": "deliveries", "id": str(row["_id"]), "query": row.get("customer_phone") or row.get("customer_name") or title})
    return jsonify(ok=True, results=results[:30])


@customer_support_operations_bp.get("/reports")
@role_required("customer_support")
def support_reports():
    now = datetime.utcnow(); tickets = list(complaints_col.find({})); calls = list(calls_col.find({})); deliveries = list(packages_col.find({}))
    resolved = [row for row in tickets if _is_resolved_ticket(row)]; breached = [row for row in tickets if _is_sla_breached(row, now)]
    resolution_hours = []
    for row in resolved:
        opened, closed = _dt(row.get("date_reported") or row.get("created_at")), _dt(row.get("date_closed") or row.get("updated_at"))
        if opened and closed and closed >= opened: resolution_hours.append((closed - opened).total_seconds() / 3600)
    resolved_calls = sum(1 for row in calls if str(row.get("outcome") or "").lower() in {"resolved", "satisfied", "completed", "acknowledged"})
    delivered = sum(1 for row in deliveries if str(row.get("status") or "").lower() == "delivered")

    def performance_rows(field, label):
        grouped = {}
        for row in tickets:
            name = str(row.get(field) or "Unassigned").strip() or "Unassigned"
            item = grouped.setdefault(name, {label: name, "tickets": 0, "resolved": 0, "open": 0, "breached": 0, "critical": 0, "resolutionHours": []})
            item["tickets"] += 1; item["critical"] += str(row.get("priority") or "").lower() == "critical"
            if _is_resolved_ticket(row):
                item["resolved"] += 1
                opened, closed = _dt(row.get("date_reported") or row.get("created_at")), _dt(row.get("date_closed") or row.get("updated_at"))
                if opened and closed and closed >= opened: item["resolutionHours"].append((closed - opened).total_seconds() / 3600)
            else: item["open"] += 1
            item["breached"] += _is_sla_breached(row, now)
        result = []
        for item in grouped.values():
            hours = item.pop("resolutionHours")
            item.update({"resolutionRate": round(item["resolved"] * 100 / item["tickets"], 1),
                         "slaRate": round((item["tickets"] - item["breached"]) * 100 / item["tickets"], 1),
                         "avgResolutionHours": round(sum(hours) / len(hours), 1) if hours else 0})
            item["score"] = round(item["resolutionRate"] * .55 + item["slaRate"] * .45, 1)
            result.append(item)
        return result

    branches = performance_rows("branch", "branch"); agents = performance_rows("assigned_to_name", "agent")
    top_branches = sorted(branches, key=lambda row: (row["score"], row["resolved"]), reverse=True)[:5]
    top_agents = sorted([row for row in agents if row["agent"] != "Unassigned"], key=lambda row: (row["score"], row["resolved"]), reverse=True)[:5]
    poor_branches = sorted(branches, key=lambda row: (-row["breached"], -row["open"], row["score"]))[:5]
    poor_agents = sorted([row for row in agents if row["agent"] != "Unassigned"], key=lambda row: (-row["breached"], -row["open"], row["score"]))[:5]

    start = datetime(now.year, now.month, 1)
    for _ in range(5): start = (start - timedelta(days=1)).replace(day=1)
    monthly = []
    month = start
    for _ in range(6):
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = [row for row in tickets if (lambda dt: bool(dt and month <= dt < end))(_dt(row.get("date_reported") or row.get("created_at")))]
        month_resolved = sum(1 for row in rows if _is_resolved_ticket(row)); month_breached = sum(1 for row in rows if _is_sla_breached(row, now))
        monthly.append({"month": month.strftime("%b %Y"), "tickets": len(rows), "resolved": month_resolved, "open": len(rows) - month_resolved,
                        "slaRate": round((len(rows) - month_breached) * 100 / len(rows), 1) if rows else 0})
        month = end
    issue_counts = {}; status_counts = {}
    for row in tickets:
        issue = str(row.get("issue_type") or "General Enquiry"); issue_counts[issue] = issue_counts.get(issue, 0) + 1
        status = str(STATUS_TO_UI.get(row.get("status"), row.get("status") or "New")); status_counts[status] = status_counts.get(status, 0) + 1
    alerts = []
    for row in poor_branches:
        if row["breached"] or row["open"] >= 5: alerts.append({"level": "critical" if row["slaRate"] < 70 else "warning", "title": f"{row['branch']} requires attention", "detail": f"{row['open']} open tickets, {row['breached']} SLA breaches, {row['resolutionRate']}% resolution rate."})
    return jsonify(ok=True, metrics={"totalTickets": len(tickets), "openTickets": len(tickets) - len(resolved), "resolvedTickets": len(resolved),
        "resolutionRate": round(len(resolved) * 100 / len(tickets), 1) if tickets else 0, "slaCompliance": round((len(tickets) - len(breached)) * 100 / len(tickets), 1) if tickets else 0,
        "avgResolutionHours": round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else 0, "totalCalls": len(calls),
        "callResolutionRate": round(resolved_calls * 100 / len(calls), 1) if calls else 0, "deliveryCompletionRate": round(delivered * 100 / len(deliveries), 1) if deliveries else 0},
        monthly=monthly, topBranches=top_branches, topAgents=top_agents, poorBranches=poor_branches, poorAgents=poor_agents,
        issues=[{"name": key, "value": value} for key, value in sorted(issue_counts.items(), key=lambda pair: pair[1], reverse=True)],
        statuses=[{"name": key, "value": value} for key, value in status_counts.items()], alerts=alerts[:5])


@customer_support_operations_bp.route("/notifications", methods=["GET", "PATCH"])
@role_required("customer_support")
def support_notifications():
    identity = get_current_identity(); user_id = str(identity.get("user_id") or "")
    state = notification_state_col.find_one({"user_id": user_id}) or {}; read_ids = set(state.get("read_ids") or []); read_all_before = _dt(state.get("read_all_before"))
    if request.method == "PATCH":
        data = request.get_json(silent=True) or {}
        if data.get("all"):
            notification_state_col.update_one({"user_id": user_id}, {"$set": {"read_all_before": datetime.utcnow(), "updated_at": datetime.utcnow()}, "$setOnInsert": {"read_ids": []}}, upsert=True)
        else:
            event_id = str(data.get("id") or "").strip()
            if not event_id: return jsonify(ok=False, message="Notification ID is required."), 400
            notification_state_col.update_one({"user_id": user_id}, {"$addToSet": {"read_ids": event_id}, "$set": {"updated_at": datetime.utcnow()}}, upsert=True)
        return jsonify(ok=True)
    events = _notification_events()
    for event in events:
        event["read"] = event["id"] in read_ids or bool(read_all_before and event["createdAt"] <= read_all_before)
        event["createdAt"] = event["createdAt"].isoformat()
    all_events = events
    event_type = str(request.args.get("type") or "all").lower()
    if event_type != "all": events = [event for event in events if event["type"] == event_type]
    unread_only = str(request.args.get("unread") or "").lower() in {"1", "true", "yes"}
    if unread_only: events = [event for event in events if not event["read"]]
    try: page = max(int(request.args.get("page") or 1), 1)
    except ValueError: page = 1
    try: per_page = max(1, min(int(request.args.get("per_page") or 25), 100))
    except ValueError: per_page = 25
    unread_count = sum(not event['read'] for event in all_events)
    total = len(events); pages = max(math.ceil(total / per_page), 1); page = min(page, pages); start = (page - 1) * per_page
    return jsonify(ok=True, notifications=events[start:start + per_page], unreadCount=unread_count,
                   counts={kind: sum(1 for event in all_events if event["type"] == kind) for kind in ("ticket", "task", "call", "delivery")},
                   pagination={"page": page, "perPage": per_page, "total": total, "totalPages": pages})


@customer_support_operations_bp.route("/calls", methods=["GET", "POST"])
@role_required("customer_support")
def calls():
    if request.method == "GET":
        rows = calls_col.find({}).sort("created_at", -1).limit(500)
        return jsonify(ok=True, calls=[_call(row) for row in rows])
    data = request.get_json(silent=True) or {}
    customer = customers_col.find_one({"_id": _oid(data.get("customer_id"))})
    if not customer:
        return jsonify(ok=False, message="Select a valid customer."), 400
    linked_ticket = str(data.get("linked_ticket") or "").strip()
    if linked_ticket:
        customer_values = [customer["_id"], str(customer["_id"])]
        if not complaints_col.find_one({"customer_id": {"$in": customer_values}, "$or": [{"ticket_no": linked_ticket}, {"_id": _oid(linked_ticket)}]}):
            return jsonify(ok=False, message="The selected ticket does not belong to this customer."), 400
    now = datetime.utcnow()
    prefix = _reference_prefix('CALL', now)
    last = calls_col.find_one({"call_no": {"$regex": f"^{prefix}"}}, sort=[("call_no", -1)])
    try: sequence = int(last["call_no"].split("-")[-1]) + 1 if last else 1
    except Exception: sequence = 1
    follow_up = bool(data.get("follow_up"))
    follow_up_agent = None
    if follow_up:
        follow_up_agent = users_col.find_one({"_id": _oid(data.get("follow_up_agent_id")), "role": "agent"})
        if not follow_up_agent:
            return jsonify(ok=False, message="Select an agent for the follow-up."), 400
        if not data.get("follow_up_date"):
            return jsonify(ok=False, message="Select a follow-up date."), 400
    doc = {k: data.get(k) for k in ("type", "department", "purpose", "duration", "outcome", "notes", "follow_up", "follow_up_date")}
    doc["linked_ticket"] = linked_ticket
    identity = get_current_identity()
    officer = users_col.find_one({"_id": _oid(identity.get("user_id"))}, {"name": 1}) or {}
    doc.update({"call_no": f"{prefix}{sequence:03d}", "customer_id": customer["_id"], "customer_name": customer.get("name", ""), "customer_phone": customer.get("phone_number", ""), "branch": customer.get("agent_branch") or customer.get("branch") or "", "created_at": now, "started_at": now, "source": "manual", "enrichment_status": "complete", "customer_match": "matched", "normalized_phone_number": normalize_ghana_phone(customer.get("phone_number")), "officer_id": str(identity.get("user_id") or ""), "officer_name": officer.get("name") or identity.get("name") or "Customer Support",
                "follow_up": follow_up, "follow_up_agent_id": follow_up_agent["_id"] if follow_up_agent else None, "follow_up_agent_name": follow_up_agent.get("name", "") if follow_up_agent else "", "follow_up_agent_branch": follow_up_agent.get("branch", "") if follow_up_agent else ""})
    if data.get("interaction_record"):
        try: doc.update(interaction_details({**data, "channel": "Call"}, now))
        except ValueError as error: return jsonify(ok=False, message=str(error)), 400
    result = calls_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return jsonify(ok=True, call=_call(doc)), 201


def _parse_mobile_timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000 if value > 10_000_000_000 else value)
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _mobile_customer_map():
    result = {}
    projection = {"name": 1, "phone_number": 1, "agent_branch": 1, "branch": 1}
    for customer in customers_col.find({"phone_number": {"$exists": True, "$ne": ""}}, projection):
        normalized = normalize_ghana_phone(customer.get("phone_number"))
        if normalized and normalized not in result:
            result[normalized] = customer
    return result


@customer_support_operations_bp.post("/mobile/calls/sync")
def sync_mobile_calls():
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id") or "").strip()
    if not device_id or len(device_id) > 160:
        return jsonify(success=False, error="A valid device_id is required."), 400
    rows = data.get("calls")
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        return jsonify(success=False, error="calls must contain between 1 and 500 records."), 400
    customers = _mobile_customer_map()
    created = duplicates = failed = 0
    results = []
    for index, item in enumerate(rows):
        try:
            if not isinstance(item, dict):
                raise ValueError("Call must be an object.")
            external_id = str(item.get("external_call_id") or "").strip()
            normalized = normalize_ghana_phone(item.get("phone_number"))
            call_type = str(item.get("call_type") or "").strip().lower()
            started_at = _parse_mobile_timestamp(item.get("started_at"))
            duration_seconds = int(item.get("duration_seconds") or 0)
            if not external_id or len(external_id) > 160: raise ValueError("Invalid external_call_id.")
            if not normalized: raise ValueError("Invalid Ghana phone number.")
            if call_type not in {"inbound", "outbound", "missed"}: raise ValueError("Invalid call_type.")
            if duration_seconds < 0 or duration_seconds > 86400: raise ValueError("Invalid duration_seconds.")
            existing = calls_col.find_one({"device_id": device_id, "external_call_id": external_id}, {"call_no": 1})
            if existing:
                duplicates += 1; results.append({"index": index, "external_call_id": external_id, "status": "duplicate", "call_id": existing.get("call_no")}); continue
            customer = customers.get(normalized)
            call_no = "CALL-ANDROID-" + hashlib.sha256(f"{device_id}:{external_id}".encode()).hexdigest()[:12].upper()
            now = datetime.utcnow()
            doc = {"call_no": call_no, "external_call_id": external_id, "source": "android", "device_id": device_id,
                "device_name": device_id, "customer_id": customer.get("_id") if customer else None,
                "customer_name": customer.get("name") if customer else None, "customer_phone": customer.get("phone_number") if customer else str(item.get("phone_number") or ""),
                "normalized_phone_number": normalized, "customer_match": "matched" if customer else "not_customer",
                "type": call_type.title(), "started_at": started_at, "created_at": now, "updated_at": now,
                "duration_seconds": duration_seconds, "duration": f"{duration_seconds // 60}m {duration_seconds % 60}s",
                "sim_account": str(item.get("sim_account") or "")[:160], "from_number": str(item.get("from_number") or "")[:40], "purpose": "", "department": "Customer Support",
                "outcome": "Missed" if call_type == "missed" else "Pending", "notes": "", "follow_up": call_type == "missed",
                "enrichment_status": "needs_update", "officer_id": "", "officer_name": device_id}
            calls_col.insert_one(doc)
            created += 1; results.append({"index": index, "external_call_id": external_id, "status": "created", "call_id": call_no})
        except DuplicateKeyError:
            duplicates += 1; results.append({"index": index, "external_call_id": str(item.get("external_call_id") or ""), "status": "duplicate"})
        except (TypeError, ValueError, OverflowError) as exc:
            failed += 1; results.append({"index": index, "external_call_id": str(item.get("external_call_id") or "") if isinstance(item, dict) else "", "status": "invalid", "error": str(exc)})
        except Exception:
            failed += 1; current_app.logger.exception("Mobile call sync failed for item %s", index)
            results.append({"index": index, "status": "rejected", "error": "Unable to store call."})
    return jsonify(success=failed == 0, created=created, duplicates=duplicates, failed=failed, results=results), (200 if created or duplicates else 400)


@customer_support_operations_bp.patch("/calls/<call_id>")
@role_required("customer_support")
def update_call(call_id):
    query = {"$or": [{"_id": _oid(call_id)}, {"call_no": call_id}]} if _oid(call_id) else {"call_no": call_id}
    row = calls_col.find_one(query)
    if not row: return jsonify(ok=False, message="Call not found."), 404
    data = request.get_json(silent=True) or {}
    purpose = str(data.get("purpose") or "").strip()
    outcome = str(data.get("outcome") or "").strip()
    department = str(data.get("department") or "").strip()
    if not purpose or not outcome or not department:
        return jsonify(ok=False, message="Purpose, outcome, and department are required."), 400
    customer = customers_col.find_one({"_id": _oid(data.get("customer_id"))}) if data.get("customer_id") else None
    if data.get("customer_id") and not customer: return jsonify(ok=False, message="Invalid customer."), 400
    follow_up = bool(data.get("follow_up"))
    follow_up_agent = users_col.find_one({"_id": _oid(data.get("follow_up_agent_id")), "role": "agent"}) if data.get("follow_up_agent_id") else None
    if data.get("follow_up_agent_id") and not follow_up_agent:
        return jsonify(ok=False, message="Invalid follow-up agent."), 400
    updates = {"purpose": purpose, "outcome": outcome, "department": department, "notes": str(data.get("notes") or "").strip(),
        "linked_ticket": str(data.get("linked_ticket") or "").strip(), "follow_up": follow_up,
        "follow_up_date": str(data.get("follow_up_date") or "") if follow_up else "",
        "follow_up_agent_id": follow_up_agent["_id"] if follow_up and follow_up_agent else None,
        "follow_up_agent_name": (follow_up_agent.get("name") or follow_up_agent.get("username") or "") if follow_up and follow_up_agent else "",
        "follow_up_agent_branch": (follow_up_agent.get("branch") or "") if follow_up and follow_up_agent else "",
        "enrichment_status": "complete", "updated_at": datetime.utcnow()}
    if customer:
        updates.update({"customer_id": customer["_id"], "customer_name": customer.get("name"), "customer_phone": customer.get("phone_number"),
            "normalized_phone_number": normalize_ghana_phone(customer.get("phone_number")), "customer_match": "matched"})
    # Unassigned synced calls gain an officer when their support log is completed.
    # Subsequent edits preserve the original attribution and count the same record.
    if not row.get("officer_id"):
        identity = get_current_identity()
        updates.update({"officer_id": str(identity.get("user_id") or ""), "officer_name": identity.get("name") or "Customer Support"})
    if data.get("interaction_record"):
        try: updates.update(interaction_details({**data, "channel": "Call"}, _dt(row.get("started_at") or row.get("created_at")) or datetime.utcnow()))
        except ValueError as error: return jsonify(ok=False, message=str(error)), 400
    claim_query = {"_id": row["_id"]}
    if not row.get("officer_id"):
        claim_query["$or"] = [{"officer_id": ""}, {"officer_id": None}, {"officer_id": {"$exists": False}}]
    result = calls_col.update_one(claim_query, {"$set": updates})
    if not result.matched_count:
        return jsonify(ok=False, message="Another officer has already logged this call. Refresh the calls list."), 409
    return jsonify(ok=True, call=_call(calls_col.find_one({'_id': row['_id']})))


@customer_support_operations_bp.get("/mobile/calls/logs")
@role_required("customer_support")
def mobile_call_logs():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 25, type=int), 5), 100)
    query = {"source": "android"}
    total = calls_col.count_documents(query)
    rows = calls_col.find(query).sort("started_at", -1).skip((page - 1) * per_page).limit(per_page)
    return jsonify(ok=True, calls=[_call(row) for row in rows], pagination={"page": page, "perPage": per_page,
        "total": total, "totalPages": max(math.ceil(total / per_page), 1)})


@customer_support_operations_bp.get("/calls/missed-count")
@role_required("customer_support")
def missed_calls_count():
    count = calls_col.count_documents({"type": {"$regex": "^missed$", "$options": "i"}, "callback_status": {"$ne": "Called Back"}})
    return jsonify(ok=True, count=count)


@customer_support_operations_bp.patch("/calls/<call_id>/called-back")
@role_required("customer_support")
def mark_call_called_back(call_id):
    query = {"$or": [{"_id": _oid(call_id)}, {"call_no": call_id}]} if _oid(call_id) else {"call_no": call_id}
    row = calls_col.find_one(query)
    if not row:
        return jsonify(ok=False, message="Call not found."), 404
    if str(row.get("type") or "").lower() != "missed":
        return jsonify(ok=False, message="Only missed calls can be marked as called back."), 400
    now = datetime.utcnow()
    identity = get_current_identity()
    calls_col.update_one({"_id": row["_id"]}, {"$set": {"callback_status": "Called Back", "called_back_at": now,
        "called_back_by": str(identity.get("user_id") or ""), "updated_at": now}})
    return jsonify(ok=True, call=_call(calls_col.find_one({'_id': row['_id']})))
