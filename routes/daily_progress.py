"""Daily cash snapshot and per-login dismissal for dashboard welcome dialogs."""
import secrets
from datetime import datetime, timezone
from bson import ObjectId
from flask import Blueprint, abort, jsonify, request, session, url_for
from db import db
from routes.executive_target import _aggregate_payments_by_agent, _agent_id_sets

daily_progress_bp = Blueprint("daily_progress", __name__)
DASHBOARDS = {
    "executive_dashboard.executive_dashboard": "executive",
    "manager_dashboard.manager_dashboard_view": "manager",
    "dashboard_agent.agent_dashboard": "agent",
}


def _identity():
    role = session.get("role")
    if role not in ("executive", "manager", "agent"):
        abort(403)
    uid = session.get(role + "_id")
    if not uid or not ObjectId.is_valid(uid):
        abort(403)
    user = db["users"].find_one({"_id": ObjectId(uid), "role": role})
    if not user:
        abort(403)
    return role, user


@daily_progress_bp.app_context_processor
def dashboard_progress_context():
    role = DASHBOARDS.get(request.endpoint)
    if not role or session.get("role") != role or session.get("daily_progress_seen"):
        return {}
    if not session.get(role + "_id"):
        return {}
    if not session.get("daily_progress_token"):
        session["daily_progress_token"] = secrets.token_urlsafe(32)
    return {"daily_progress_token": session["daily_progress_token"]}


def cash_snapshot(role, user):
    query = {"period": "daily", "is_active": True}
    if role == "manager":
        query["manager_id"] = user["_id"]
    elif role == "agent":
        mid = user.get("manager_id")
        query["manager_id"] = ObjectId(str(mid)) if ObjectId.is_valid(str(mid)) else None
    targets = list(db["executive_targets"].find(query))
    if role == "agent":
        quota = sum(float(a.get("cash_quota") or 0) for t in targets
                    for a in (t.get("agent_allocations") or []) if str(a.get("agent_id")) == str(user["_id"]))
        ids = [str(user["_id"])]
        href = url_for("dashboard_agent.agent_dashboard") + "#targets"
        scope = "your daily cash target"
    else:
        quota = sum(float(t.get("cash_target") or 0) for t in targets)
        managers = [t["manager_id"] for t in targets]
        variants = list(managers) + [str(mid) for mid in managers]
        agents = db["users"].find({"role": "agent", "manager_id": {"$in": variants}}, {"_id": 1})
        ids = list({str(a["_id"]) for a in agents})
        href = url_for("manager_target.manager_targets") if role == "manager" else url_for("executive_targets_analytics.executive_targets_analytics_page", period="daily")
        scope = "your team's daily cash target" if role == "manager" else "the combined daily cash target"
    now = datetime.now(timezone.utc)
    strings, oids = _agent_id_sets(ids)
    achieved = round(sum(_aggregate_payments_by_agent(strings, oids, now.date().isoformat(), now.date().isoformat()).values()), 2)
    quota = round(quota, 2)
    return {"target": quota, "collected": achieved, "percent": round(achieved / quota * 100, 1) if quota > 0 else 0,
            "remaining": round(max(quota - achieved, 0), 2), "exceeded": round(max(achieved - quota, 0), 2),
            "as_of": now.strftime("%d %b %Y ? %H:%M UTC"), "scope": scope, "targets_url": href,
            "empty_message": "Your daily cash target has not been assigned yet." if role == "agent" else "No daily cash targets set."}


@daily_progress_bp.get("/dashboard/daily-progress")
def daily_progress():
    role, user = _identity()
    response = jsonify(cash_snapshot(role, user))
    response.headers["Cache-Control"] = "no-store"
    return response


@daily_progress_bp.post("/dashboard/daily-progress/dismiss")
def dismiss_daily_progress():
    _identity()
    token = (request.get_json(silent=True) or {}).get("token")
    expected = session.get("daily_progress_token")
    if not isinstance(token, str) or not expected or not secrets.compare_digest(token, expected):
        abort(403)
    session["daily_progress_seen"] = True
    return jsonify(ok=True)
