from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict
from io import BytesIO
import calendar
import uuid

from flask import (
    Blueprint, render_template, session, redirect,
    url_for, request, jsonify, send_file
)
from bson.objectid import ObjectId
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db import db

executive_susu_bp = Blueprint("executive_susu", __name__)

# Collections
users_col     = db.users
customers_col = db.customers
payments_col  = db.payments


# ---------- Helpers ----------

def _require_executive_or_admin() -> bool:
    """
    Ensure only EXECUTIVE / ADMIN can view this page.
    Returns True if allowed, else False.
    """
    return bool(session.get("executive_id") or session.get("admin_id"))


def _authorized_user() -> Optional[Dict[str, Any]]:
    """Return the verified Executive/Admin user represented by the session."""
    raw_id = session.get("executive_id") or session.get("admin_id")
    if not raw_id:
        return None
    variants: List[Any] = [str(raw_id)]
    if ObjectId.is_valid(str(raw_id)):
        variants.append(ObjectId(str(raw_id)))
    user = users_col.find_one({"_id": {"$in": variants}})
    if not user or str(user.get("role") or "").lower() not in {"executive", "admin"}:
        return None
    return user


def _safe_date_from_payment(p: Dict[str, Any]) -> Optional[date]:
    """
    Try to get a date from payment doc:
      - prefer `timestamp` (datetime)
      - fallback to parsing `date` (YYYY-MM-DD)
    """
    ts = p.get("timestamp")
    if isinstance(ts, datetime):
        return ts.date()

    date_str = p.get("date")
    if isinstance(date_str, str):
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def _normalize_id(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, ObjectId):
        return str(v)
    try:
        return str(v)
    except Exception:
        return None


def _classify_susu_withdraw(p: Dict[str, Any]) -> Optional[str]:
    """
    Classify a WITHDRAWAL payment as:
      - "cash"   -> money paid to customer
      - "profit" -> company SUSU profit
      - None     -> not SUSU-related (ignore)
    """
    if p.get("payment_type") != "WITHDRAWAL":
        return None

    method_raw = (p.get("method") or "").strip()
    method_lc = method_raw.lower()
    note_lc = (p.get("note") or "").strip().lower()

    is_cash = False
    is_profit = False

    # --- Cash to customer variants ---
    if method_lc in ("susu withdrawal", "manual", "cash", "withdrawal", "susu cash"):
        is_cash = True

    # --- Profit / deduction variants ---
    if method_lc in ("susu profit", "deduction", "susu deduction"):
        is_profit = True

    # --- Infer from note text for old data ---
    if "susu" in note_lc:
        if "profit" in note_lc or "deduction" in note_lc:
            is_profit = True
        if "withdraw" in note_lc or "cash" in note_lc or "payout" in note_lc:
            is_cash = True

    if is_profit and not is_cash:
        return "profit"
    if is_cash and not is_profit:
        return "cash"
    if is_cash and is_profit:
        # If both signs appear, prioritise profit if strong signals
        if ("profit" in method_lc or "deduction" in method_lc or
                "profit" in note_lc or "deduction" in note_lc):
            return "profit"
        return "cash"

    return None


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# ---------- Executive SUSU Overview ----------

@executive_susu_bp.route("/executive/susu")
def executive_susu_dashboard():
    """
    Executive SUSU overview:

    - Global SUSU metrics (all branches, filtered by date range)
    - Branch-level SUSU breakdown
    - Snapshot metrics (Today / This Week / This Month)
    - Customer search (respecting date range + branch filter)
    - Date range filters:
        range = today | week | month | all | custom
        For custom: start_date=YYYY-MM-DD & end_date=YYYY-MM-DD
    """
    if not _require_executive_or_admin():
        return redirect(url_for("login.login"))

    # -------- Query params --------
    branch_filter = (request.args.get("branch") or "all").strip()
    customer_search_term = (request.args.get("search") or "").strip()

    range_key = (request.args.get("range") or "month").lower()
    start_param = (request.args.get("start_date") or "").strip()
    end_param = (request.args.get("end_date") or "").strip()

    # -------- Load managers (for branch mapping) --------
    manager_map: Dict[str, Dict[str, Any]] = {}
    branches: set[str] = set()
    report_managers: List[Dict[str, str]] = []

    for u in users_col.find({"role": "manager"}, {"_id": 1, "name": 1, "branch": 1}):
        mid_str = str(u["_id"])
        branch = (u.get("branch") or "Unassigned").strip() or "Unassigned"
        manager_map[mid_str] = {
            "name": u.get("name", "Manager"),
            "branch": branch,
        }
        report_managers.append({
            "id": mid_str,
            "name": u.get("name", "Manager"),
            "branch": branch,
        })
        branches.add(branch)

    sorted_branches = sorted(branches)
    report_managers.sort(key=lambda row: (row["branch"].lower(), row["name"].lower()))

    # -------- Date setup --------
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    start_of_month = today.replace(day=1)

    # Range for filtering totals / branch / customers
    start_range: Optional[date] = None
    end_range: Optional[date] = None
    filter_label = ""

    if range_key == "today":
        start_range = today
        end_range = today
        filter_label = "Today"
    elif range_key == "week":
        start_range = start_of_week
        end_range = today
        filter_label = "This Week"
    elif range_key == "month":
        start_range = start_of_month
        end_range = today
        filter_label = "This Month"
    elif range_key == "custom":
        sd = _parse_date(start_param)
        ed = _parse_date(end_param)
        if sd and ed and sd <= ed:
            start_range = sd
            end_range = ed
            filter_label = f"{sd.isoformat()} to {ed.isoformat()}"
        else:
            # Fallback to month if invalid
            start_range = start_of_month
            end_range = today
            filter_label = "This Month (invalid custom dates)"
            range_key = "month"
    else:
        # "all" or unknown
        start_range = None
        end_range = None
        filter_label = "All Time"

    def _in_selected_range(d: Optional[date]) -> bool:
        if start_range is None or end_range is None:
            return True  # no filter (all time)
        if not d:
            return False
        return start_range <= d <= end_range

    # -------- Global & branch metrics --------
    global_totals = {
        "total_susu": 0.0,
        "total_withdrawals": 0.0,
        "total_profit": 0.0,
    }

    branch_totals: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {
            "total_susu": 0.0,
            "total_withdrawals": 0.0,
            "total_profit": 0.0,
        }
    )

    # Snapshot metrics (NOT affected by date filter; true picture)
    time_metrics = {
        "today": {"susu": 0.0, "withdraw": 0.0, "profit": 0.0, "net": 0.0},
        "week":  {"susu": 0.0, "withdraw": 0.0, "profit": 0.0, "net": 0.0},
        "month": {"susu": 0.0, "withdraw": 0.0, "profit": 0.0, "net": 0.0},
    }

    today_withdrawals_count = 0  # for snapshot

    # -------- Scan all SUSU-related payments --------
    payments_cursor = payments_col.find({
        "payment_type": {"$in": ["SUSU", "WITHDRAWAL"]}
    })

    for p in payments_cursor:
        p_type = p.get("payment_type")
        amt = float(p.get("amount", 0) or 0)

        # Manager / branch mapping
        mid_norm = _normalize_id(p.get("manager_id"))
        manager_info = manager_map.get(mid_norm, {})
        branch_name = manager_info.get("branch", "Unassigned")

        # Date
        d = _safe_date_from_payment(p)

        # ---------- Snapshot (today/week/month) ----------
        if p_type == "SUSU":
            if d:
                # today
                if d == today:
                    time_metrics["today"]["susu"] += amt
                    time_metrics["today"]["net"]  += amt
                # week
                if d >= start_of_week:
                    time_metrics["week"]["susu"] += amt
                    time_metrics["week"]["net"]  += amt
                # month
                if d >= start_of_month:
                    time_metrics["month"]["susu"] += amt
                    time_metrics["month"]["net"]  += amt

        elif p_type == "WITHDRAWAL":
            kind = _classify_susu_withdraw(p)
            if not kind:
                # Not SUSU-related
                continue

            if d:
                # today
                if d == today:
                    if kind == "profit":
                        time_metrics["today"]["profit"] += amt
                    else:
                        time_metrics["today"]["withdraw"] += amt
                        today_withdrawals_count += 1
                    time_metrics["today"]["net"] -= amt

                # week
                if d >= start_of_week:
                    if kind == "profit":
                        time_metrics["week"]["profit"] += amt
                    else:
                        time_metrics["week"]["withdraw"] += amt
                    time_metrics["week"]["net"] -= amt

                # month
                if d >= start_of_month:
                    if kind == "profit":
                        time_metrics["month"]["profit"] += amt
                    else:
                        time_metrics["month"]["withdraw"] += amt
                    time_metrics["month"]["net"] -= amt

        # ---------- Date-filtered totals (global + branch) ----------
        if not _in_selected_range(d):
            continue  # outside selected range for totals

        if p_type == "SUSU":
            global_totals["total_susu"] += amt
            branch_totals[branch_name]["total_susu"] += amt

        elif p_type == "WITHDRAWAL":
            kind = _classify_susu_withdraw(p)
            if not kind:
                continue

            if kind == "profit":
                global_totals["total_profit"] += amt
                branch_totals[branch_name]["total_profit"] += amt
            else:
                global_totals["total_withdrawals"] += amt
                branch_totals[branch_name]["total_withdrawals"] += amt

    # Compute global available (for selected range)
    global_available = (
        global_totals["total_susu"]
        - global_totals["total_withdrawals"]
        - global_totals["total_profit"]
    )

    # Compute per-branch available & build ordered list
    branch_rows: List[Dict[str, Any]] = []
    for branch_name, bt in branch_totals.items():
        available = bt["total_susu"] - bt["total_withdrawals"] - bt["total_profit"]
        branch_rows.append({
            "branch": branch_name,
            "total_susu": round(bt["total_susu"], 2),
            "total_withdrawals": round(bt["total_withdrawals"], 2),
            "total_profit": round(bt["total_profit"], 2),
            "available": round(available, 2),
        })

    branch_rows.sort(key=lambda x: x["branch"].lower())

    # Selected branch summary (if filter applied)
    selected_branch_stats = None
    if branch_filter != "all":
        for row in branch_rows:
            if row["branch"] == branch_filter:
                selected_branch_stats = row
                break

    # -------- Customer search (by name / phone, date-filtered) --------
    customer_results: List[Dict[str, Any]] = []
    if customer_search_term:
        customer_filter: Dict[str, Any] = {
            "$or": [
                {"name": {"$regex": customer_search_term, "$options": "i"}},
                {"phone_number": {"$regex": customer_search_term, "$options": "i"}},
            ]
        }
        customers_cursor = customers_col.find(
            customer_filter,
            {
                "name": 1,
                "phone_number": 1,
                "location": 1,
                "image_url": 1,
            }
        ).limit(50)

        for cust in customers_cursor:
            cid = cust["_id"]
            name = cust.get("name", "Customer")
            phone = cust.get("phone_number", "N/A")
            location = cust.get("location", "")
            image_url = cust.get("image_url", "")

            payments_for_cust = list(payments_col.find({"customer_id": cid}))

            total_susu = 0.0
            total_withdraw = 0.0
            total_profit = 0.0

            # Last known branch (overall, not limited by date)
            last_branch = "Unknown"

            for p in payments_for_cust:
                p_type = p.get("payment_type")
                amt = float(p.get("amount", 0) or 0)

                mid_norm = _normalize_id(p.get("manager_id"))
                manager_info = manager_map.get(mid_norm, {})
                if manager_info.get("branch"):
                    last_branch = manager_info["branch"]

                d = _safe_date_from_payment(p)
                if not _in_selected_range(d):
                    # Do not count this payment in totals if outside selected period
                    continue

                if p_type == "SUSU":
                    total_susu += amt
                elif p_type == "WITHDRAWAL":
                    kind = _classify_susu_withdraw(p)
                    if not kind:
                        continue
                    if kind == "profit":
                        total_profit += amt
                    else:
                        total_withdraw += amt

            available = total_susu - total_withdraw - total_profit
            if available < 0:
                available = 0.0

            # If branch filter is applied, only show matching customers
            if branch_filter != "all" and last_branch != branch_filter:
                continue

            customer_results.append({
                "id": str(cid),
                "name": name,
                "phone": phone,
                "location": location,
                "image_url": image_url,
                "branch": last_branch,
                "total_susu": round(total_susu, 2),
                "total_withdraw": round(total_withdraw, 2),
                "total_profit": round(total_profit, 2),
                "available": round(available, 2),
            })

    # -------- Summary object for template --------
    summary = {
        "global": {
            "total_susu": round(global_totals["total_susu"], 2),
            "total_withdrawals": round(global_totals["total_withdrawals"], 2),
            "total_profit": round(global_totals["total_profit"], 2),
            "available": round(global_available, 2),
            "filter_label": filter_label,
        },
        "time": {
            "today": {k: round(v, 2) for k, v in time_metrics["today"].items()},
            "week": {k: round(v, 2) for k, v in time_metrics["week"].items()},
            "month": {k: round(v, 2) for k, v in time_metrics["month"].items()},
        },
        "today_withdrawals_count": today_withdrawals_count,
    }

    start_date_str = start_range.isoformat() if start_range else ""
    end_date_str = end_range.isoformat() if end_range else ""

    return render_template(
        "executive_susu.html",
        summary=summary,
        branch_rows=branch_rows,
        branches=sorted_branches,
        branch_filter=branch_filter,
        selected_branch_stats=selected_branch_stats,
        customer_search_term=customer_search_term,
        customer_results=customer_results,
        range_key=range_key,
        start_date=start_date_str,
        end_date=end_date_str,
        report_managers=report_managers,
    )


# ---------- Executive SUSU reports and customer drill-down ----------

def _id_variants(value: Any) -> List[Any]:
    if value is None:
        return []
    text = str(value)
    values: List[Any] = [text]
    if ObjectId.is_valid(text):
        values.append(ObjectId(text))
    return values


def _report_date_range(range_key: str, start_raw: str, end_raw: str) -> tuple[Optional[date], Optional[date], str]:
    today = datetime.utcnow().date()
    key = (range_key or "month").lower()
    if key == "today":
        return today, today, "Today"
    if key == "week":
        return today - timedelta(days=today.weekday()), today, "This Week"
    if key == "month":
        return today.replace(day=1), today, today.strftime("%B %Y")
    if key == "previous_month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, start.strftime("%B %Y")
    if key == "custom":
        start, end = _parse_date(start_raw), _parse_date(end_raw)
        if start and end and start <= end:
            return start, end, f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"
        return today.replace(day=1), today, today.strftime("%B %Y")
    return None, None, "All Time"


def _report_scope(scope_type: str, scope_id: str) -> Optional[Dict[str, Any]]:
    scope_type = (scope_type or "").lower()
    scope_id = (scope_id or "").strip()
    if scope_type == "manager":
        manager = users_col.find_one({
            "_id": {"$in": _id_variants(scope_id)},
            "role": "manager",
        }, {"name": 1, "branch": 1})
        if not manager:
            return None
        return {
            "type": "manager",
            "label": manager.get("name") or "Manager",
            "sub_label": manager.get("branch") or "Unassigned",
            "manager_ids": [str(manager["_id"])],
        }
    if scope_type == "branch":
        managers = list(users_col.find(
            {"role": "manager", "branch": scope_id},
            {"name": 1, "branch": 1},
        ))
        if not managers:
            return None
        return {
            "type": "branch",
            "label": scope_id,
            "sub_label": f"{len(managers)} manager(s)",
            "manager_ids": [str(row["_id"]) for row in managers],
        }
    if scope_type == "all":
        managers = list(users_col.find({"role": "manager"}, {"_id": 1}))
        return {
            "type": "all",
            "label": "All Branches",
            "sub_label": f"{len(managers)} manager(s)",
            "manager_ids": [str(row["_id"]) for row in managers],
        }
    return None


def _manager_lookup() -> Dict[str, Dict[str, str]]:
    lookup: Dict[str, Dict[str, str]] = {}
    for manager in users_col.find({"role": "manager"}, {"name": 1, "branch": 1}):
        lookup[str(manager["_id"])] = {
            "name": manager.get("name") or "Manager",
            "branch": manager.get("branch") or "Unassigned",
        }
    return lookup


def _customer_is_doing_product(customer: Dict[str, Any], has_product_payment: bool = False) -> tuple[bool, str]:
    active_names: List[str] = []
    for purchase in customer.get("purchases") or []:
        product = purchase.get("product") or {}
        status = str(product.get("status") or purchase.get("status") or "active").lower()
        transfer_status = str(product.get("transfer_status") or "").lower()
        if status not in {"cancelled", "canceled", "closed", "refunded"} and transfer_status != "transferred_out":
            active_names.append(product.get("name") or "Product")
    doing = bool(active_names or has_product_payment)
    return doing, ", ".join(active_names) if active_names else ("Product payment recorded" if has_product_payment else "")


def _build_susu_report_data(
    scope: Dict[str, Any],
    start_date: Optional[date],
    end_date: Optional[date],
) -> Dict[str, Any]:
    manager_values: List[Any] = []
    for manager_id in scope["manager_ids"]:
        manager_values.extend(_id_variants(manager_id))

    payments = list(payments_col.find({
        "manager_id": {"$in": manager_values},
        "payment_type": {"$in": ["SUSU", "WITHDRAWAL"]},
    }))
    filtered: List[Dict[str, Any]] = []
    for payment in payments:
        payment_date = _safe_date_from_payment(payment)
        if start_date and (not payment_date or payment_date < start_date):
            continue
        if end_date and (not payment_date or payment_date > end_date):
            continue
        if payment.get("payment_type") == "WITHDRAWAL" and not _classify_susu_withdraw(payment):
            continue
        payment["_report_date"] = payment_date
        filtered.append(payment)

    customer_ids: Dict[str, Any] = {}
    for payment in filtered:
        customer_id = payment.get("customer_id")
        if customer_id is not None:
            customer_ids[str(customer_id)] = customer_id
    customer_query_values: List[Any] = []
    for value in customer_ids.values():
        customer_query_values.extend(_id_variants(value))
    customers = list(customers_col.find(
        {"_id": {"$in": customer_query_values}},
        {"name": 1, "phone_number": 1, "location": 1, "manager_id": 1, "purchases": 1},
    )) if customer_query_values else []
    customer_map = {str(customer["_id"]): customer for customer in customers}

    product_customer_ids = set()
    if customer_query_values:
        product_customer_ids = {
            str(value) for value in payments_col.distinct(
                "customer_id",
                {
                    "customer_id": {"$in": customer_query_values},
                    "payment_type": {"$nin": ["SUSU", "WITHDRAWAL"]},
                },
            )
        }

    manager_map = _manager_lookup()
    per_customer: Dict[str, Dict[str, Any]] = {}
    cash_withdrawals: List[Dict[str, Any]] = []
    profit_rows: List[Dict[str, Any]] = []

    for payment in filtered:
        customer_id = str(payment.get("customer_id") or "")
        if not customer_id:
            continue
        customer = customer_map.get(customer_id) or {}
        manager_id = str(payment.get("manager_id") or customer.get("manager_id") or "")
        manager = manager_map.get(manager_id, {"name": "Unknown", "branch": "Unassigned"})
        row = per_customer.setdefault(customer_id, {
            "id": customer_id,
            "name": customer.get("name") or "Unknown Customer",
            "phone": customer.get("phone_number") or "N/A",
            "location": customer.get("location") or "N/A",
            "manager": manager["name"],
            "branch": manager["branch"],
            "susu_total": 0.0,
            "withdraw_total": 0.0,
            "profit_total": 0.0,
            "payments": [],
            "withdrawals": [],
            "profits": [],
            "first_payment": None,
            "first_payment_amount": 0.0,
        })
        amount = float(payment.get("amount", 0) or 0)
        if payment.get("payment_type") == "SUSU":
            row["susu_total"] += amount
            row["payments"].append(payment)
            if row["first_payment"] is None or (
                payment["_report_date"] and payment["_report_date"] < row["first_payment"]
            ):
                row["first_payment"] = payment["_report_date"]
                row["first_payment_amount"] = amount
        else:
            kind = _classify_susu_withdraw(payment)
            history_row = {
                "date": payment["_report_date"],
                "customer": row["name"],
                "phone": row["phone"],
                "location": row["location"],
                "manager": manager["name"],
                "branch": manager["branch"],
                "amount": amount,
                "note": payment.get("note") or payment.get("method") or "",
                "kind": kind,
            }
            if kind == "cash":
                row["withdraw_total"] += amount
                row["withdrawals"].append(payment)
                cash_withdrawals.append(history_row)
            elif kind == "profit":
                row["profit_total"] += amount
                row["profits"].append(payment)
                profit_rows.append(history_row)

    # First SUSU payment is an all-time customer fact, not merely the first
    # contribution inside the selected report period.
    if customer_query_values:
        first_payment_map: Dict[str, Dict[str, Any]] = {}
        all_susu_cursor = payments_col.find({
            "customer_id": {"$in": customer_query_values},
            "payment_type": "SUSU",
        })
        for payment in all_susu_cursor:
            customer_key = str(payment.get("customer_id") or "")
            payment_date = _safe_date_from_payment(payment)
            current = first_payment_map.get(customer_key)
            if payment_date and (not current or payment_date < current["date"]):
                first_payment_map[customer_key] = {
                    "date": payment_date,
                    "amount": float(payment.get("amount", 0) or 0),
                }
        for customer_key, first in first_payment_map.items():
            if customer_key in per_customer:
                per_customer[customer_key]["first_payment"] = first["date"]
                per_customer[customer_key]["first_payment_amount"] = first["amount"]

    customer_rows = []
    for customer_id, row in per_customer.items():
        customer = customer_map.get(customer_id) or {}
        doing_product, product_names = _customer_is_doing_product(
            customer, customer_id in product_customer_ids
        )
        row["doing_product"] = doing_product
        row["product_names"] = product_names
        row["available"] = max(0.0, row["susu_total"] - row["withdraw_total"] - row["profit_total"])
        row["payments"].sort(key=lambda item: _safe_date_from_payment(item) or date.min)
        row["withdrawals"].sort(key=lambda item: _safe_date_from_payment(item) or date.min)
        row["profits"].sort(key=lambda item: _safe_date_from_payment(item) or date.min)
        customer_rows.append(row)
    customer_rows.sort(key=lambda row: row["name"].lower())
    cash_withdrawals.sort(key=lambda row: row["date"] or date.min)
    profit_rows.sort(key=lambda row: row["date"] or date.min)

    return {
        "customers": customer_rows,
        "withdrawals": cash_withdrawals,
        "profits": profit_rows,
        "total_susu": sum(row["susu_total"] for row in customer_rows),
        "total_withdrawals": sum(row["withdraw_total"] for row in customer_rows),
        "total_profit": sum(row["profit_total"] for row in customer_rows),
    }


@executive_susu_bp.route("/executive/susu/customer/<customer_id>/history")
def executive_susu_customer_history(customer_id: str):
    if not _authorized_user():
        return jsonify(ok=False, message="Unauthorized"), 401
    customer = customers_col.find_one({"_id": {"$in": _id_variants(customer_id)}})
    if not customer:
        return jsonify(ok=False, message="Customer not found"), 404

    payments = list(payments_col.find({
        "customer_id": {"$in": _id_variants(customer_id)},
        "payment_type": {"$in": ["SUSU", "WITHDRAWAL"]},
    }))
    payments.sort(key=lambda item: _safe_date_from_payment(item) or date.min)
    contributions, withdrawals, profits = [], [], []
    total_susu = total_withdraw = total_profit = 0.0
    for payment in payments:
        item = {
            "date": (_safe_date_from_payment(payment) or date.min).isoformat()
                if _safe_date_from_payment(payment) else "",
            "amount": float(payment.get("amount", 0) or 0),
            "method": payment.get("method") or "",
            "note": payment.get("note") or "",
        }
        if payment.get("payment_type") == "SUSU":
            contributions.append(item)
            total_susu += item["amount"]
        else:
            kind = _classify_susu_withdraw(payment)
            if kind == "cash":
                withdrawals.append(item)
                total_withdraw += item["amount"]
            elif kind == "profit":
                profits.append(item)
                total_profit += item["amount"]

    has_product_payment = payments_col.count_documents({
        "customer_id": {"$in": _id_variants(customer_id)},
        "payment_type": {"$nin": ["SUSU", "WITHDRAWAL"]},
    }, limit=1) > 0
    doing_product, product_names = _customer_is_doing_product(customer, has_product_payment)
    manager_id = str(customer.get("manager_id") or "")
    manager = _manager_lookup().get(manager_id, {"name": "Unknown", "branch": "Unassigned"})
    first = contributions[0] if contributions else None
    return jsonify(ok=True, customer={
        "id": str(customer["_id"]),
        "name": customer.get("name") or "Customer",
        "phone": customer.get("phone_number") or "N/A",
        "location": customer.get("location") or "N/A",
        "manager": manager["name"],
        "branch": manager["branch"],
        "doing_product": doing_product,
        "product_names": product_names,
        "first_payment": first,
        "latest_payment": contributions[-1] if contributions else None,
        "total_susu": round(total_susu, 2),
        "total_withdraw": round(total_withdraw, 2),
        "total_profit": round(total_profit, 2),
        "available": round(max(0, total_susu - total_withdraw - total_profit), 2),
        "contributions": contributions,
        "withdrawals": withdrawals,
        "profits": profits,
    })


def _susu_pdf_footer(canvas, doc, reference: str):
    canvas.saveState()
    width, _ = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#D7DEE8"))
    canvas.line(15 * mm, 12 * mm, width - 15 * mm, 12 * mm)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(15 * mm, 7.5 * mm, f"Confidential - Smart Living | {reference}")
    canvas.drawRightString(width - 15 * mm, 7.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


@executive_susu_bp.route("/executive/susu/report.pdf")
def executive_susu_report_pdf():
    actor = _authorized_user()
    if not actor:
        return redirect(url_for("login.login"))
    scope = _report_scope(request.args.get("scope_type", ""), request.args.get("scope_id", ""))
    if not scope:
        return jsonify(ok=False, message="Select a valid Manager, branch, or all branches."), 400
    start, end, period_label = _report_date_range(
        request.args.get("range", "month"),
        request.args.get("start_date", ""),
        request.args.get("end_date", ""),
    )
    report = _build_susu_report_data(scope, start, end)
    now = datetime.utcnow()
    reference = f"SSR-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=18 * mm,
        title=f"SUSU Report - {scope['label']}", author="Smart Living",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SusuTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=20, leading=24, textColor=colors.HexColor("#123B69"), alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        name="SusuSection", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, leading=16, textColor=colors.HexColor("#123B69"), spaceBefore=6, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="SusuCell", parent=styles["BodyText"], fontSize=7.2, leading=8.5,
    ))
    story = [
        Paragraph("SMART LIVING", styles["Heading3"]),
        Paragraph("SUSU Withdrawal and Customer Report", styles["SusuTitle"]),
        Spacer(1, 3 * mm),
    ]
    meta = Table([
        ["REPORT SCOPE", scope["label"], "BRANCH / DETAILS", scope["sub_label"]],
        ["REPORTING PERIOD", period_label, "GENERATED BY", actor.get("name") or actor.get("username") or "Executive"],
        ["REPORT REFERENCE", reference, "GENERATED", now.strftime("%d %b %Y %H:%M UTC")],
    ], colWidths=[38 * mm, 78 * mm, 44 * mm, 100 * mm])
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF1F8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EAF1F8")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([meta, Spacer(1, 6 * mm)])

    withdrawals = report["withdrawals"]
    amounts = [row["amount"] for row in withdrawals]
    daily: Dict[date, float] = defaultdict(float)
    for row in withdrawals:
        if row["date"]:
            daily[row["date"]] += row["amount"]
    highest_day = max(daily.items(), key=lambda item: item[1]) if daily else None
    customer_count = len(report["customers"])
    product_count = sum(1 for row in report["customers"] if row["doing_product"])
    available = max(0, report["total_susu"] - report["total_withdrawals"] - report["total_profit"])
    summary = Table([
        ["SUSU COLLECTED", "CASH WITHDRAWN", "COMPANY PROFIT", "NET AVAILABLE", "WITHDRAWALS", "CUSTOMERS"],
        [
            f"GHS {report['total_susu']:,.2f}", f"GHS {report['total_withdrawals']:,.2f}",
            f"GHS {report['total_profit']:,.2f}", f"GHS {available:,.2f}",
            str(len(withdrawals)), str(customer_count),
        ],
    ], colWidths=[43.3 * mm] * 6)
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123B69")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F2F6FA")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, 1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    detail = Table([
        ["AVERAGE WITHDRAWAL", "HIGHEST WITHDRAWAL", "HIGHEST DAY", "CUSTOMERS DOING PRODUCT", "SUSU ONLY"],
        [
            f"GHS {(sum(amounts) / len(amounts) if amounts else 0):,.2f}",
            f"GHS {(max(amounts) if amounts else 0):,.2f}",
            f"{highest_day[0].strftime('%d %b %Y')} - GHS {highest_day[1]:,.2f}" if highest_day else "N/A",
            str(product_count), str(max(0, customer_count - product_count)),
        ],
    ], colWidths=[52 * mm] * 5)
    detail.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1F8")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([summary, Spacer(1, 2 * mm), detail, Spacer(1, 6 * mm)])

    monthly: Dict[tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    for row in withdrawals:
        if row["date"]:
            monthly[(row["date"].year, row["date"].month)].append(row)
    if monthly:
        story.append(Paragraph("Monthly withdrawal history", styles["SusuSection"]))
        for month_index, month_key in enumerate(sorted(monthly.keys(), reverse=True)):
            rows = monthly[month_key]
            month_total = sum(row["amount"] for row in rows)
            month_daily: Dict[date, float] = defaultdict(float)
            for row in rows:
                month_daily[row["date"]] += row["amount"]
            month_high = max(month_daily.items(), key=lambda item: item[1])
            story.append(Paragraph(
                f"{calendar.month_name[month_key[1]]} {month_key[0]} - "
                f"GHS {month_total:,.2f} across {len(rows)} withdrawal(s); "
                f"highest day {month_high[0].strftime('%d %b')} at GHS {month_high[1]:,.2f}",
                styles["SusuSection"],
            ))
            table_data = [["DATE", "CUSTOMER", "PHONE", "LOCATION", "MANAGER", "BRANCH", "AMOUNT", "NOTE"]]
            for row in rows:
                table_data.append([
                    row["date"].strftime("%d %b %Y"), Paragraph(row["customer"], styles["SusuCell"]),
                    row["phone"], Paragraph(row["location"], styles["SusuCell"]),
                    Paragraph(row["manager"], styles["SusuCell"]), row["branch"],
                    f"GHS {row['amount']:,.2f}", Paragraph(row["note"] or "-", styles["SusuCell"]),
                ])
            table = Table(table_data, colWidths=[27*mm, 43*mm, 28*mm, 35*mm, 35*mm, 28*mm, 30*mm, 34*mm], repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (6, 1), (6, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D7DEE8")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
            if month_index < len(monthly) - 1:
                story.append(Spacer(1, 5 * mm))
    else:
        story.append(Paragraph("No cash withdrawals were recorded for this scope and period.", styles["BodyText"]))

    story.extend([PageBreak(), Paragraph("Customer summary", styles["SusuTitle"])])
    customer_data = [[
        "CUSTOMER", "PHONE", "LOCATION", "MANAGER / BRANCH", "FIRST SUSU PAYMENT",
        "TOTAL SAVED", "WITHDRAWN", "BALANCE", "DOING PRODUCT",
    ]]
    for row in report["customers"]:
        first_label = (
            f"{row['first_payment'].strftime('%d %b %Y')} / GHS {row['first_payment_amount']:,.2f}"
            if row["first_payment"] else "N/A"
        )
        customer_data.append([
            Paragraph(row["name"], styles["SusuCell"]), row["phone"],
            Paragraph(row["location"], styles["SusuCell"]),
            Paragraph(f"{row['manager']} / {row['branch']}", styles["SusuCell"]),
            first_label, f"GHS {row['susu_total']:,.2f}", f"GHS {row['withdraw_total']:,.2f}",
            f"GHS {row['available']:,.2f}", "Yes" if row["doing_product"] else "No",
        ])
    customer_table = Table(
        customer_data,
        colWidths=[37*mm, 27*mm, 31*mm, 43*mm, 42*mm, 26*mm, 25*mm, 25*mm, 20*mm],
        repeatRows=1,
    )
    customer_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.8),
        ("ALIGN", (5, 1), (8, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
        ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D7DEE8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(customer_table)

    for row in report["customers"]:
        story.extend([
            PageBreak(),
            Paragraph(row["name"], styles["SusuTitle"]),
            Paragraph(
                f"{row['phone']} | {row['location']} | {row['manager']} / {row['branch']} | "
                f"Doing Product: {'Yes' if row['doing_product'] else 'No'}"
                + (f" ({row['product_names']})" if row["product_names"] else ""),
                styles["BodyText"],
            ),
            Spacer(1, 4 * mm),
        ])
        history_data = [["DATE", "TYPE", "AMOUNT", "METHOD", "NOTE"]]
        history_rows = []
        for payment in row["payments"]:
            history_rows.append((_safe_date_from_payment(payment), "SUSU Payment", payment))
        for payment in row["withdrawals"]:
            history_rows.append((_safe_date_from_payment(payment), "Cash Withdrawal", payment))
        for payment in row["profits"]:
            history_rows.append((_safe_date_from_payment(payment), "Company Profit", payment))
        history_rows.sort(key=lambda item: item[0] or date.min)
        for payment_date, row_type, payment in history_rows:
            history_data.append([
                payment_date.strftime("%d %b %Y") if payment_date else "Unknown",
                row_type, f"GHS {float(payment.get('amount', 0) or 0):,.2f}",
                payment.get("method") or "-", Paragraph(payment.get("note") or "-", styles["SusuCell"]),
            ])
        history_table = Table(history_data, colWidths=[35*mm, 42*mm, 34*mm, 48*mm, 101*mm], repeatRows=1)
        history_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244F78")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
            ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#D7DEE8")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(history_table)

    doc.build(
        story,
        onFirstPage=lambda canvas, pdf_doc: _susu_pdf_footer(canvas, pdf_doc, reference),
        onLaterPages=lambda canvas, pdf_doc: _susu_pdf_footer(canvas, pdf_doc, reference),
    )
    buffer.seek(0)
    safe_scope = "".join(ch if ch.isalnum() else "_" for ch in scope["label"]).strip("_") or "susu"
    return send_file(
        buffer, mimetype="application/pdf", as_attachment=True,
        download_name=f"{safe_scope}_susu_report_{now.strftime('%Y%m%d')}.pdf",
    )
