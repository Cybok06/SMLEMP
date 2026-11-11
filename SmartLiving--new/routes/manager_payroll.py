from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from bson import ObjectId
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from db import db

manager_payroll_bp = Blueprint("manager_payroll", __name__, url_prefix="/manager/payroll")

users_col = db["users"]
payrolls_col = db["payrolls"]

EDITABLE_STATUSES = {"Draft", "Rejected", "Sent Back"}


def _ensure_indexes() -> None:
    try:
        payrolls_col.create_index([("manager_id", 1), ("payroll_month", 1)], unique=True)
        payrolls_col.create_index([("payroll_month", 1), ("status", 1), ("updated_at", -1)])
        users_col.create_index([("manager_id", 1), ("role", 1)])
    except Exception:
        pass


_ensure_indexes()


def _now() -> datetime:
    return datetime.utcnow()


def _money(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return round(max(0.0, float(value)), 2)
    except Exception:
        return 0.0


def _oid(value: Any) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _normalize_month(month: str) -> str:
    month = (month or "").strip()[:7]
    if len(month) != 7 or month[4] != "-":
        raise ValueError("Invalid month format. Use YYYY-MM.")
    year = int(month[:4])
    mon = int(month[5:7])
    if year < 2000 or year > 2100 or mon < 1 or mon > 12:
        raise ValueError("Invalid month.")
    return month


def _month_label(month: str) -> str:
    try:
        y, m = month.split("-")
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{names[int(m) - 1]} {y}"
    except Exception:
        return month or ""


def _require_manager():
    manager_id = session.get("manager_id")
    oid = _oid(manager_id)
    if not oid:
        flash("Access denied. Please log in as a manager.", "danger")
        return None
    manager = users_col.find_one({"_id": oid})
    if not manager or (manager.get("role") or "").lower() != "manager":
        flash("Access denied. Manager not found.", "danger")
        return None
    return manager


def _agents_under_manager(manager_oid: ObjectId) -> List[Dict[str, Any]]:
    return list(
        users_col.find(
            {"role": "agent", "manager_id": manager_oid, "status": {"$ne": "Inactive"}},
            {"_id": 1, "name": 1, "branch": 1, "image_url": 1, "status": 1},
        ).sort("name", 1)
    )


def _gross(item: Dict[str, Any]) -> float:
    return _money(
        _money(item.get("base_salary"))
        + _money(item.get("commission"))
        + _money(item.get("bonus"))
        + _money(item.get("allowance"))
        + _money(item.get("other_pay"))
        + _money(item.get("tips_amount"))
    )


def _item_with_totals(item: Dict[str, Any]) -> Dict[str, Any]:
    clean = dict(item)
    clean["base_salary"] = _money(clean.get("base_salary"))
    clean["commission"] = _money(clean.get("commission"))
    clean["bonus"] = _money(clean.get("bonus"))
    clean["allowance"] = _money(clean.get("allowance"))
    clean["other_pay"] = _money(clean.get("other_pay"))
    clean["tips_amount"] = _money(clean.get("tips_amount"))
    clean["gross_pay"] = _gross(clean)
    clean["deduction_amount"] = _money(clean.get("deduction_amount"))
    clean["deduction_reason"] = (clean.get("deduction_reason") or "").strip()
    clean["net_pay"] = _money(clean["gross_pay"] - clean["deduction_amount"])
    clean["manager_note"] = (clean.get("manager_note") or "").strip()
    clean["hr_note"] = (clean.get("hr_note") or "").strip()
    clean["status"] = clean.get("status") or "Draft"
    return clean


def _legacy_to_item(agent: Dict[str, Any], previous: Dict[str, Any] | None = None) -> Dict[str, Any]:
    previous = previous or {}
    gross = _money(previous.get("manager_amount") if "manager_amount" in previous else previous.get("gross_pay"))
    return _item_with_totals(
        {
            "agent_id": str(agent.get("_id") or previous.get("agent_id") or ""),
            "agent_name": previous.get("agent_name") or agent.get("name") or "Agent",
            "branch_name": previous.get("branch_name") or previous.get("agent_branch") or agent.get("branch") or "",
            "base_salary": previous.get("base_salary", gross),
            "commission": previous.get("commission", 0),
            "bonus": previous.get("bonus", 0),
            "allowance": previous.get("allowance", 0),
            "other_pay": previous.get("other_pay", 0),
            "tips_amount": previous.get("tips_amount", 0),
            "deduction_amount": previous.get("deduction_amount", 0),
            "deduction_reason": previous.get("deduction_reason", ""),
            "manager_note": previous.get("manager_note", ""),
            "hr_note": previous.get("hr_note", ""),
            "status": previous.get("status", "Draft"),
            "approved_at": previous.get("approved_at"),
        }
    )


def _totals(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_gross = sum(_money(i.get("gross_pay")) for i in items)
    total_deductions = sum(_money(i.get("deduction_amount")) for i in items)
    total_net = sum(_money(i.get("net_pay")) for i in items)
    return {
        "submitted_total": _money(total_gross),
        "final_total": _money(total_gross),
        "net_final_total": _money(total_net),
        "total_gross_pay": _money(total_gross),
        "total_deductions": _money(total_deductions),
        "total_net_pay": _money(total_net),
        "agent_count": len(items),
        "edited_count": sum(1 for i in items if _money(i.get("deduction_amount")) > 0 or i.get("hr_note")),
    }


def _batch_fields(manager: Dict[str, Any], month: str, items: List[Dict[str, Any]], status: str) -> Dict[str, Any]:
    totals = _totals(items)
    return {
        "manager_id": str(manager["_id"]),
        "manager_name": manager.get("name") or "Manager",
        "branch_id": str(manager.get("branch_id") or ""),
        "branch_name": manager.get("branch") or "",
        "payroll_month": month,
        "total_agents": totals["agent_count"],
        "total_gross_pay": totals["total_gross_pay"],
        "total_deductions": totals["total_deductions"],
        "total_net_pay": totals["total_net_pay"],
        "totals": totals,
        "status": status,
    }


def _serialize_payroll(payroll: Dict[str, Any] | None, month: str) -> Dict[str, Any]:
    if not payroll:
        return {
            "id": None,
            "status": "Draft",
            "payroll_month": month,
            "month_label": _month_label(month),
            "submitted_at": "",
            "reviewed_at": "",
            "hr_comment": "",
            "totals": _totals([]),
        }
    return {
        "id": str(payroll["_id"]),
        "status": payroll.get("status") or "Draft",
        "payroll_month": payroll.get("payroll_month") or month,
        "month_label": _month_label(payroll.get("payroll_month") or month),
        "submitted_at": _iso(payroll.get("submitted_at")),
        "reviewed_at": _iso(payroll.get("reviewed_at") or payroll.get("hr_action_at")),
        "hr_comment": payroll.get("hr_comment") or "",
        "totals": payroll.get("totals") or _totals(payroll.get("items") or []),
    }


@manager_payroll_bp.route("", methods=["GET"])
def payroll_home():
    manager = _require_manager()
    if not manager:
        return redirect(url_for("login.login"))
    return render_template(
        "manager_payroll.html",
        manager_name=manager.get("name", "Manager"),
        manager_id=str(manager["_id"]),
        today_iso=_now().date().isoformat(),
    )


@manager_payroll_bp.route("/month/<month>", methods=["GET"])
def payroll_month_payload(month):
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401
    try:
        month = _normalize_month(month)
    except Exception as exc:
        return jsonify(ok=False, message=str(exc)), 400

    payroll = payrolls_col.find_one({"manager_id": str(manager["_id"]), "payroll_month": month})
    previous_by_agent = {str(i.get("agent_id")): i for i in (payroll or {}).get("items", [])}
    rows = []
    for agent in _agents_under_manager(manager["_id"]):
        row = _legacy_to_item(agent, previous_by_agent.get(str(agent["_id"])))
        rows.append(row)

    status = (payroll or {}).get("status") or "Draft"
    editable = not payroll or status in EDITABLE_STATUSES
    return jsonify(
        ok=True,
        month=month,
        month_label=_month_label(month),
        payroll=_serialize_payroll(payroll, month),
        editable=editable,
        rows=rows,
    )


@manager_payroll_bp.route("/save", methods=["POST"])
def payroll_save():
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401
    data = request.get_json(silent=True) or {}
    try:
        month = _normalize_month(data.get("month") or "")
    except Exception as exc:
        return jsonify(ok=False, message=str(exc)), 400

    payroll = payrolls_col.find_one({"manager_id": str(manager["_id"]), "payroll_month": month})
    if payroll and payroll.get("status") not in EDITABLE_STATUSES:
        return jsonify(ok=False, message="Payroll is locked because it has been submitted to HR."), 409

    allowed_agents = {str(a["_id"]): a for a in _agents_under_manager(manager["_id"])}
    previous_by_agent = {str(i.get("agent_id")): i for i in (payroll or {}).get("items", [])}
    items = []
    for raw in data.get("items") or []:
        agent_id = str(raw.get("agent_id") or "")
        agent = allowed_agents.get(agent_id)
        if not agent:
            continue
        previous = previous_by_agent.get(agent_id, {})
        item = _item_with_totals(
            {
                "agent_id": agent_id,
                "agent_name": agent.get("name") or raw.get("agent_name") or "Agent",
                "branch_name": agent.get("branch") or raw.get("branch_name") or "",
                "base_salary": raw.get("base_salary"),
                "commission": raw.get("commission"),
                "bonus": raw.get("bonus"),
                "allowance": raw.get("allowance"),
                "other_pay": raw.get("other_pay"),
                # HR-owned values remain intact if a rejected/sent-back batch
                # becomes manager-editable again.
                "tips_amount": previous.get("tips_amount", 0),
                "deduction_amount": previous.get("deduction_amount", 0),
                "deduction_reason": previous.get("deduction_reason", ""),
                "manager_note": raw.get("manager_note"),
                "hr_note": previous.get("hr_note", ""),
                "status": previous.get("status") if previous.get("status") in {"Approved", "Rejected"} else "Draft",
                "approved_at": previous.get("approved_at"),
            }
        )
        items.append(item)

    now = _now()
    update = _batch_fields(manager, month, items, (payroll or {}).get("status", "Draft"))
    update.update({"items": items, "updated_at": now})

    if payroll:
        payrolls_col.update_one(
            {"_id": payroll["_id"]},
            {
                "$set": update,
                "$push": {"activity_log": {"action": "manager_saved", "by": str(manager["_id"]), "at": now}},
            },
        )
        payroll_id = str(payroll["_id"])
        status = payroll.get("status", "Draft")
    else:
        doc = dict(update)
        doc.update(
            {
                "status": "Draft",
                "submitted_at": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "created_at": now,
                "activity_log": [{"action": "created", "by": str(manager["_id"]), "at": now}],
            }
        )
        res = payrolls_col.insert_one(doc)
        payroll_id = str(res.inserted_id)
        status = "Draft"

    return jsonify(ok=True, message="Draft saved.", payroll_id=payroll_id, status=status, totals=_totals(items))


@manager_payroll_bp.route("/submit", methods=["POST"])
def payroll_submit():
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401
    data = request.get_json(silent=True) or {}
    try:
        month = _normalize_month(data.get("month") or "")
    except Exception as exc:
        return jsonify(ok=False, message=str(exc)), 400

    payroll = payrolls_col.find_one({"manager_id": str(manager["_id"]), "payroll_month": month})
    if not payroll:
        return jsonify(ok=False, message="Save this payroll as draft before submitting."), 404
    if payroll.get("status") not in EDITABLE_STATUSES:
        return jsonify(ok=False, message="Payroll is already submitted or processed."), 409

    items = [_item_with_totals(i) for i in payroll.get("items") or []]
    if not items:
        return jsonify(ok=False, message="No agents found on this payroll."), 400
    if all(_money(i.get("gross_pay")) <= 0 for i in items):
        return jsonify(ok=False, message="Enter at least one payroll amount before submitting."), 400

    for item in items:
        item["status"] = "Pending HR Review"

    now = _now()
    update = _batch_fields(manager, month, items, "Pending HR Review")
    update.update({"items": items, "submitted_at": now, "updated_at": now})
    payrolls_col.update_one(
        {"_id": payroll["_id"]},
        {
            "$set": update,
            "$push": {"activity_log": {"action": "submitted", "by": str(manager["_id"]), "at": now}},
        },
    )
    return jsonify(ok=True, message="Payroll submitted to HR.", status="Pending HR Review")


@manager_payroll_bp.route("/history", methods=["GET"])
def payroll_history():
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401

    rows = []
    cursor = payrolls_col.find(
        {"manager_id": str(manager["_id"])},
        {"payroll_month": 1, "status": 1, "totals": 1, "total_net_pay": 1, "updated_at": 1, "submitted_at": 1},
    ).sort("payroll_month", -1).limit(48)
    for payroll in cursor:
        totals = payroll.get("totals") or {}
        rows.append(
            {
                "id": str(payroll["_id"]),
                "month": payroll.get("payroll_month") or "",
                "month_label": _month_label(payroll.get("payroll_month") or ""),
                "status": payroll.get("status") or "Draft",
                "updated_at": _iso(payroll.get("updated_at")),
                "submitted_at": _iso(payroll.get("submitted_at")),
                "total_gross_pay": _money(totals.get("total_gross_pay") or totals.get("final_total")),
                "total_net_pay": _money(totals.get("total_net_pay") or payroll.get("total_net_pay")),
                "total_agents": int(totals.get("agent_count") or payroll.get("total_agents") or 0),
            }
        )
    return jsonify(ok=True, rows=rows)


@manager_payroll_bp.route("/attention-count", methods=["GET"])
def payroll_attention_count():
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401
    count = payrolls_col.count_documents(
        {"manager_id": str(manager["_id"]), "status": {"$in": ["Draft", "Rejected", "Sent Back"]}}
    )
    return jsonify(ok=True, count=count)


@manager_payroll_bp.route("/details/<payroll_id>", methods=["GET"])
def payroll_details(payroll_id):
    manager = _require_manager()
    if not manager:
        return jsonify(ok=False, message="Not authorized."), 401
    oid = _oid(payroll_id)
    if not oid:
        return jsonify(ok=False, message="Invalid payroll id."), 400
    payroll = payrolls_col.find_one({"_id": oid, "manager_id": str(manager["_id"])})
    if not payroll:
        return jsonify(ok=False, message="Payroll not found."), 404
    items = [_item_with_totals(i) for i in payroll.get("items") or []]
    return jsonify(ok=True, payroll={**_serialize_payroll(payroll, payroll.get("payroll_month") or ""), "items": items})
