"""Officer targets and evidence-based progress. Dates use Ghana time (UTC)."""
from datetime import date, datetime, time, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from flask import Blueprint, current_app, jsonify, render_template, request

from db import db
from services.manager_suite_scope import scoped_db
from services.support_record_audit import attribution

db = scoped_db(db)
from login import get_current_identity, role_required

support_targets_bp = Blueprint("support_targets", __name__)
targets_col = db.customer_support_targets
visits_col = db.customer_support_field_visits
users_col = db.users
calls_col = db.customer_support_calls
activities_col = db.customer_support_manual_activities
TARGET_TYPES = {
    ("calls", "daily"), ("calls", "weekly"), ("field_trips", "weekly"),
    ("interactions", "daily"), ("interactions", "weekly"), ("interactions", "per_trip"),
}


@support_targets_bp.record_once
def ensure_indexes(_state):
    try:
        targets_col.create_index([("officer_id", 1), ("effective_from", -1), ("created_at", -1)])
        visits_col.create_index([("officer_id", 1), ("started_at", -1)])
        visits_col.create_index([("officer_id", 1), ("started_at", 1), ("ended_at", 1)], unique=True)
        calls_col.create_index([("officer_id", 1), ("started_at", -1)])
        activities_col.create_index([("created_by", 1), ("category", 1), ("occurred_at", -1)])
        activities_col.create_index([("field_visit_id", 1), ("category", 1)])
    except Exception:
        current_app.logger.exception("Unable to ensure support target indexes")


def oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, TypeError):
        raise ValueError("Enter a valid date and time.")


def period_window(day, period):
    start = day - timedelta(days=day.weekday()) if period == "weekly" else day
    return datetime.combine(start, time.min), datetime.combine(start + timedelta(days=7 if period == "weekly" else 1), time.min)


def user_values(user_id):
    return [user_id, oid(user_id)] if oid(user_id) else [user_id]


def serialize_visit(row, interaction_count=None):
    return {**attribution(row), "id": str(row["_id"]), "location": row["location"], "notes": row["notes"],
            "started_at": row["started_at"].isoformat() + "Z", "ended_at": row["ended_at"].isoformat() + "Z",
            "duration_minutes": round((row["ended_at"] - row["started_at"]).total_seconds() / 60),
            "interactions": interaction_count if interaction_count is not None else activities_col.count_documents({"field_visit_id": row["_id"], "category": "Interaction", "created_by": {"$in": user_values(str(row["officer_id"]))}})}


def serialize_visits(rows, user_id):
    rows = list(rows)
    if not rows:
        return []
    counts = {row["_id"]: row["count"] for row in activities_col.aggregate([
        {"$match": {"field_visit_id": {"$in": [row["_id"] for row in rows]}, "category": "Interaction", "created_by": {"$in": user_values(user_id)}}},
        {"$group": {"_id": "$field_visit_id", "count": {"$sum": 1}}},
    ])}
    return [serialize_visit(row, counts.get(row["_id"], 0)) for row in rows]


def active_targets(user_id, day):
    # Each change is a dated version: prior reporting dates retain their targets.
    rows = targets_col.find({"officer_id": user_id, "effective_from": {"$lte": day.isoformat()}}).sort([("effective_from", -1), ("created_at", -1), ("_id", -1)])
    latest = {}
    for row in rows:
        latest.setdefault((row["metric"], row["period"]), row)
    return list(latest.values())


def completed_calls_query(user_id, start, end):
    return {"officer_id": {"$in": user_values(user_id)}, "enrichment_status": {"$ne": "needs_update"},
        "type": {"$nin": ["Missed", "missed"]}, "$or": [
            {"started_at": {"$gte": start, "$lt": end}},
            {"started_at": None, "created_at": {"$gte": start, "$lt": end}},
        ]}


def performance(user_id, day, period):
    start, end = period_window(day, period)
    values = user_values(user_id)
    calls = calls_col.count_documents(completed_calls_query(user_id, start, end))
    trips = visits_col.count_documents({"officer_id": {"$in": values}, "started_at": {"$gte": start, "$lt": end}})
    interactions = activities_col.count_documents({"created_by": {"$in": values}, "category": "Interaction",
        "field_visit_id": {"$type": "objectId"}, "occurred_at": {"$gte": start, "$lt": end}})
    recorded_interactions = activities_col.count_documents({"created_by": {"$in": values}, "category": "Interaction",
        "occurred_at": {"$gte": start, "$lt": end}})
    resolved = db.complaints.count_documents({"resolution_history": {"$elemMatch": {
        "actor_id": {"$in": values}, "actor_role": "customer_support", "at": {"$gte": start, "$lt": end}}}})
    # Delivery issues count once per card, only where a prior recorded issue was resolved.
    for task in db.tasks.find({"kind": "delivery_confirmation", "delivery_reviews": {"$elemMatch": {
        "officer_id": {"$in": values}, "recorded_at": {"$gte": start, "$lt": end}}}}, {"delivery_reviews": 1}):
        outstanding = False
        for review in task.get("delivery_reviews") or []:
            if review.get("issue"):
                outstanding = True
            elif outstanding and review.get("receipt") == "full" and review.get("satisfaction") == "satisfied":
                if str(review.get("officer_id")) == user_id and start <= review["recorded_at"] < end:
                    resolved += 1
                    break
                outstanding = False
    opportunities = 0
    linked_purchases = set()
    for task in db.tasks.find({"repeat_purchase.created_by": {"$in": values}}):
        opportunity = task["repeat_purchase"]
        if start <= opportunity["created_at"] < end:
            opportunities += 1
        if opportunity.get("status") == "Purchased" and opportunity.get("purchase"):
            try:
                finished = parse_time(opportunity.get("finished_at"))
                if start <= finished < end:
                    linked_purchases.add((str(task.get("customer_id")), opportunity["purchase"]["index"]))
            except (ValueError, KeyError):
                pass
    call_assignments = calls_col.count_documents({"officer_id": {"$in": values}, "follow_up": True,
        "follow_up_agent_id": {"$exists": True, "$nin": [None, ""]}, "created_at": {"$gte": start, "$lt": end}})
    task_assignments = db.tasks.count_documents({"created_by": {"$in": values}, "created_by_role": "customer_support",
        "assignee_id": {"$exists": True, "$nin": [None, ""]}, "created_at": {"$gte": start, "$lt": end}, "kind": {"$ne": "delivery_confirmation"}})
    return {"period": period, "start": start.date().isoformat(), "end": (end-timedelta(days=1)).date().isoformat(),
        "completed_calls": calls, "field_trips": trips, "field_interactions": interactions, "recorded_interactions": recorded_interactions,
        "issues_resolved": resolved, "repeat_opportunities": opportunities,
        "assigned_followups": call_assignments + task_assignments + opportunities, "resulting_purchases": len(linked_purchases),
        "assignment_sources": {"calls": call_assignments, "tasks": task_assignments, "repeat_opportunities": opportunities}}


def progress(user_id, day):
    values = user_values(user_id)
    weekly_start, weekly_end = period_window(day, "weekly")
    visit_rows = list(visits_col.find({"officer_id": {"$in": values}, "started_at": {"$gte": weekly_start, "$lt": weekly_end}}).sort("started_at", -1))
    visits = serialize_visits(visit_rows, user_id)
    cards = []
    for target in active_targets(user_id, day):
        metric, period, amount = target["metric"], target["period"], target["amount"]
        if amount == 0:
            continue
        start, end = period_window(day, "weekly" if period == "per_trip" else period)
        if metric == "calls":
            actual = calls_col.count_documents(completed_calls_query(user_id, start, end))
        elif metric == "field_trips":
            actual = len(visits)
        elif period == "per_trip":
            for visit in visits:
                visit["interaction_target"] = amount
                visit["remaining"] = max(amount - visit["interactions"], 0)
            actual = sum(visit["interactions"] >= amount for visit in visits)
        else:
            actual = activities_col.count_documents({"created_by": {"$in": values}, "category": "Interaction",
                "field_visit_id": {"$type": "objectId"}, "occurred_at": {"$gte": start, "$lt": end}})
        goal = len(visits) if period == "per_trip" else amount
        cards.append({"metric": metric, "period": period, "amount": amount, "actual": actual, "goal": goal,
            "percent": round(actual / goal * 100, 1) if goal else 0, "remaining": max(goal - actual, 0),
            "status": "No trips logged" if period == "per_trip" and not goal else "Achieved" if actual >= goal else "In progress",
            "start": start.date().isoformat(), "end": (end - timedelta(days=1)).date().isoformat(), "effective_from": target["effective_from"]})
    return {"officer_id": user_id, "date": day.isoformat(), "targets": cards, "visits": visits, "performance": {period: performance(user_id, day, period) for period in ("daily", "weekly")}}


@support_targets_bp.get("/executive/customer-support")
@support_targets_bp.get("/admin/customer-support")
@role_required("executive", "admin")
def executive_page():
    is_admin = get_current_identity().get("role") == "admin"
    return render_template("executive/customer_support.html",
        management_role="Admin" if is_admin else "Executive",
        management_sidebar="admin_sidebar.html" if is_admin else "executive_sidebar.html",
        management_api_prefix="/api/admin/customer-support" if is_admin else "/api/executive/customer-support")


@support_targets_bp.get("/api/executive/customer-support/officers")
@support_targets_bp.get("/api/admin/customer-support/officers")
@role_required("executive", "admin")
def officers():
    rows = users_col.find({"role": {"$in": ["customer_support", "operations_management"]}}).sort("name", 1)
    return jsonify(ok=True, officers=[{"id": str(row["_id"]), "name": row.get("name") or row.get("username") or "Support Officer", "branch": row.get("branch") or ""} for row in rows])


@support_targets_bp.post("/api/executive/customer-support/targets")
@support_targets_bp.post("/api/admin/customer-support/targets")
@role_required("executive", "admin")
def save_target():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, message="Enter a valid target."), 400
    user_id = str(data.get("officer_id") or "")
    if not users_col.find_one({"_id": oid(user_id), "role": {"$in": ["customer_support", "operations_management"]}}):
        return jsonify(ok=False, message="Select a valid customer support officer."), 400
    metric, period = str(data.get("metric") or ""), str(data.get("period") or "")
    if (metric, period) not in TARGET_TYPES:
        return jsonify(ok=False, message="Select a valid target and period."), 400
    try:
        raw = data.get("amount")
        if isinstance(raw, bool) or not str(raw).isdigit():
            raise ValueError()
        amount = int(raw)
        if not 0 <= amount <= 100000:
            raise ValueError()
        effective = date.fromisoformat(data.get("effective_from") or datetime.utcnow().date().isoformat())
        if effective < datetime.utcnow().date():
            return jsonify(ok=False, message="Target changes must take effect today or later."), 400
    except (ValueError, TypeError):
        return jsonify(ok=False, message="Enter a whole target from 0 to 100,000 and a valid effective date."), 400
    targets_col.insert_one({"officer_id": user_id, "metric": metric, "period": period, "amount": amount,
        "effective_from": effective.isoformat(), "created_at": datetime.utcnow(), "created_by": str(get_current_identity().get("user_id") or "")})
    return jsonify(ok=True, message="Target saved. Use 0 to disable a target.")


@support_targets_bp.get("/api/customer-support/targets")
@support_targets_bp.get("/api/executive/customer-support/progress")
@support_targets_bp.get("/api/admin/customer-support/progress")
@role_required("customer_support", "executive", "admin")
def target_progress():
    identity = get_current_identity()
    is_management = identity.get("role") in {"executive", "admin"}
    if request.path.startswith(("/api/executive/", "/api/admin/")) and not is_management:
        return jsonify(ok=False, message="Forbidden"), 403
    user_id = str(request.args.get("officer_id") or "") if is_management else str(identity.get("user_id") or "")
    if not user_id:
        return jsonify(ok=False, message="Select an officer."), 400
    try:
        day = date.fromisoformat(request.args.get("date") or datetime.utcnow().date().isoformat())
    except ValueError:
        return jsonify(ok=False, message="Select a valid reporting date."), 400
    result = progress(user_id, day)
    if is_management:
        result["history"] = [{"metric": row["metric"], "period": row["period"], "amount": row["amount"],
            "effective_from": row["effective_from"], "created_at": row["created_at"].isoformat() + "Z"}
            for row in targets_col.find({"officer_id": user_id}).sort([("created_at", -1), ("_id", -1)]).limit(50)]
    return jsonify(ok=True, **result)


@support_targets_bp.route("/api/customer-support/field-visits", methods=["GET", "POST"])
@role_required("customer_support")
def field_visits():
    user_id = str(get_current_identity().get("user_id") or "")
    if request.method == "GET":
        rows = visits_col.find({"officer_id": {"$in": user_values(user_id)}}).sort("started_at", -1).limit(200)
        return jsonify(ok=True, visits=serialize_visits(rows, user_id))
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, message="Enter a valid field visit."), 400
    location, notes = str(data.get("location") or "").strip(), str(data.get("notes") or "").strip()
    if not location or not notes or len(location) > 200 or len(notes) > 2000:
        return jsonify(ok=False, message="Enter a location (up to 200 characters) and visit notes (up to 2,000 characters)."), 400
    try:
        start, end = parse_time(data.get("started_at")), parse_time(data.get("ended_at"))
        if end <= start or end > datetime.utcnow():
            raise ValueError("End time must be after start time and cannot be in the future.")
    except ValueError as error:
        return jsonify(ok=False, message=str(error)), 400
    if visits_col.find_one({"officer_id": {"$in": user_values(user_id)}, "started_at": {"$lt": end}, "ended_at": {"$gt": start}}):
        return jsonify(ok=False, message="This visit overlaps a visit you already logged."), 409
    row = {"officer_id": user_id, "location": location, "notes": notes, "started_at": start, "ended_at": end, "created_at": datetime.utcnow()}
    try:
        row["_id"] = visits_col.insert_one(row).inserted_id
    except DuplicateKeyError:
        return jsonify(ok=False, message="This visit has already been logged."), 409
    return jsonify(ok=True, visit=serialize_visit(row)), 201
