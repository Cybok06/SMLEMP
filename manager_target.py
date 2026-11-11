from datetime import datetime
from decimal import Decimal
from functools import wraps

from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from db import db
from routes.executive_target import compute_manager_target_progress, parse_target_value
from services.target_cash import CHANNELS

manager_target_bp = Blueprint("manager_target", __name__, url_prefix="/manager/targets")
targets_collection = db["executive_targets"]
users_collection = db["users"]


def manager_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        manager_id = session.get("manager_id")
        if not manager_id or not ObjectId.is_valid(manager_id):
            abort(401)
        if not users_collection.find_one({"_id": ObjectId(manager_id), "role": "manager"}):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _owned_target(target_id):
    if not ObjectId.is_valid(target_id):
        abort(404)
    query = {"_id": ObjectId(target_id), "manager_id": ObjectId(session["manager_id"]), "is_active": True}
    target = targets_collection.find_one(query)
    if not target:
        abort(404)
    return target, query


def _agents():
    return list(users_collection.find({"role": "agent", "manager_id": ObjectId(session["manager_id"])}).sort("name", 1))


@manager_target_bp.get("/")
@manager_required
def manager_targets():
    summaries = []
    for period in ("daily", "weekly", "monthly", "yearly"):
        summary = compute_manager_target_progress(ObjectId(session["manager_id"]), period)
        if summary:
            summaries.append(summary)
    return render_template("manager_target.html", results=summaries)


@manager_target_bp.route("/distribute/<target_id>", methods=["GET", "POST"])
@manager_required
def distribute_target_get(target_id):
    target, query = _owned_target(target_id)
    agents = _agents()
    existing = {str(a.get("agent_id")): a for a in target.get("agent_allocations", [])}
    error = None
    status = 200
    if request.method == "POST":
        ids = request.form.getlist("agent_id[]")
        fields = ("product_quota", "cash_quota", "customer_quota") + tuple(c + '_quota' for c in CHANNELS)
        values = {key: request.form.getlist(key + "[]") for key in fields}
        for c in CHANNELS:
            if not values[c + '_quota'] and not target.get(c + '_target'):
                values[c + '_quota'] = ['0'] * len(ids)
        try:
            if not ids or len(ids) != len(set(ids)):
                raise ValueError("Choose agents once each.")
            team = {str(a["_id"]): a for a in agents}
            if any(aid not in team for aid in ids):
                raise ValueError("Allocate only to agents currently on your team. Reload the page if your team changed.")
            if any(len(v) != len(ids) for v in values.values()):
                raise ValueError("Enter all quotas for each agent.")
            totals = {key: Decimal(0) for key in fields}
            allocations = []
            for i, aid in enumerate(ids):
                row = {"agent_id": aid, "agent_name": team[aid].get("name") or "Agent"}
                for key in fields:
                    amount = parse_target_value(values[key][i], cash='cash' in key)
                    totals[key] += amount
                    row[key] = float(amount) if 'cash' in key else int(amount)
                if any(row[key] for key in fields):
                    allocations.append(row)
            for key in fields:
                expected = Decimal(str(target.get(key.replace("quota", "target")) or 0))
                if totals[key] != expected:
                    raise ValueError("Allocated totals must exactly match each manager target, including Product Cash and SUSU Cash.")
            if target.get('updated_at') is not None:
                query['updated_at'] = target['updated_at']
            result = targets_collection.update_one(query, {"$set": {
                "agent_allocations": allocations, "distribution_mode": "manual",
                "alloc_updated_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
                "allocated_by": session["manager_id"],
            }})
            if not result.matched_count:
                abort(409, description="This target changed. Reload your targets before distributing.")
            flash("Agent allocations saved. Agents can now see their quotas on their dashboards.", "success")
            return redirect(url_for("manager_target.manager_targets"))
        except ValueError as exc:
            error, status = str(exc), 400
            # Preserve submitted values so validation errors do not erase manual work.
            existing = {aid: {key: values[key][i] if i < len(values[key]) else "" for key in fields}
                        for i, aid in enumerate(ids)}
    return render_template("manager_target_distribute.html", target=target, agents=agents,
                           allocations=existing, error=error), status
