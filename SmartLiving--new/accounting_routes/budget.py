from __future__ import annotations

from datetime import date, datetime, timedelta
import csv
import io
from typing import Any, Dict, List
from pymongo import DeleteMany, UpdateOne
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for

from db import db
from manager_expense import ALLOWED_CATEGORIES as MANAGER_EXPENSE_CATEGORIES

acc_budget = Blueprint("acc_budget", __name__, template_folder="../templates")

budgets_col = db["expense_budgets"]
manager_expenses_col = db["manager_expenses"]
users_col = db["users"]
payments_col = db["payments"]


def _ensure_indexes() -> None:
    try:
        budgets_col.create_index([("year", 1), ("kind", 1), ("category", 1)])
        manager_expenses_col.create_index([("status", 1), ("created_at", 1), ("category", 1)])
        manager_expenses_col.create_index([("status", 1), ("created_at", 1), ("manager_id", 1)])
        users_col.create_index([("role", 1)])
    except Exception:
        pass


_ensure_indexes()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace(",", "").strip())
    except Exception:
        return default


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.strptime(raw.strip(), "%Y-%m-%d")
        return datetime(d.year, d.month, d.day)
    except Exception:
        return None


def _get_doc_date(doc: Dict[str, Any], keys: List[str]) -> datetime | None:
    for k in keys:
        val = doc.get(k)
        if isinstance(val, datetime):
            return val
        if isinstance(val, date):
            return datetime.combine(val, datetime.min.time())
        if isinstance(val, str):
            s = val.strip()
            if not s:
                continue
            try:
                return datetime.fromisoformat(s)
            except Exception:
                try:
                    return datetime.strptime(s[:10], "%Y-%m-%d")
                except Exception:
                    continue
    return None


def _normalize_category(raw: Any) -> str:
    return str(raw or "").strip()


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59, 59, 999999)
    return start, end


def _resolve_period(year: int, start_raw: str, end_raw: str) -> tuple[datetime, datetime, str, str]:
    year_start, year_end = _year_bounds(year)
    start_dt = _parse_date(start_raw)
    end_dt = _parse_date(end_raw)
    if not start_dt or not end_dt:
        start_dt, end_dt = year_start, year_end
        return start_dt, end_dt, start_dt.date().isoformat(), end_dt.date().isoformat()

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Always keep "used" metrics inside the selected budget year.
    if start_dt < year_start:
        start_dt = year_start
    if end_dt > year_end:
        end_dt = year_end

    # If user-picked custom range falls completely outside the selected year,
    # fallback to the full selected year.
    if start_dt > end_dt:
        start_dt, end_dt = year_start, year_end

    return start_dt, end_dt, start_dt.date().isoformat(), end_dt.date().isoformat()


def _load_budget_by_category(year: int) -> Dict[str, float]:
    pipeline = [
        {
            "$match": {
                "year": year,
                "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}],
            }
        },
        {
            "$group": {
                "_id": "$category",
                "budget_total": {
                    "$sum": {
                        "$toDouble": {"$ifNull": ["$amount", 0]},
                    }
                },
            }
        },
    ]

    out: Dict[str, float] = {}
    for row in budgets_col.aggregate(pipeline):
        cat = _normalize_category(row.get("_id"))
        if cat:
            out[cat] = _safe_float(row.get("budget_total"))
    return out


def _load_income_budget_amount(year: int) -> float:
    pipeline = [
        {"$match": {"year": year, "kind": "income"}},
        {"$group": {"_id": None, "amount": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}}}},
    ]
    row = next(budgets_col.aggregate(pipeline), None)
    return round(_safe_float((row or {}).get("amount"), 0.0), 2)


def _all_expense_categories(year: int, start_dt: datetime, end_dt: datetime) -> List[str]:
    base = set(MANAGER_EXPENSE_CATEGORIES)

    for b in budgets_col.find(
        {"year": year, "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}]},
        {"category": 1},
    ):
        cat = _normalize_category(b.get("category"))
        if cat:
            base.add(cat)

    for r in manager_expenses_col.find(
        {"status": "Approved", "created_at": {"$gte": start_dt, "$lte": end_dt}},
        {"category": 1},
    ):
        cat = _normalize_category(r.get("category"))
        if cat:
            base.add(cat)

    return sorted(base)


def _sales_snapshot(start_dt, end_dt):
    from flask import g, has_request_context
    cache = getattr(g, '_budget_sales', {}) if has_request_context() else {}
    key = (start_dt, end_dt)
    if key not in cache:
        total, months = 0.0, {}
        for p in payments_col.find({"payment_type": {"$ne": "WITHDRAWAL"}}, {"amount": 1, "date_dt": 1, "date": 1, "created_at": 1}):
            dt = _get_doc_date(p, ["date_dt", "date", "created_at"])
            if not dt or dt < start_dt or dt > end_dt:
                continue
            amount = _safe_float(p.get("amount"))
            total += amount
            month = dt.strftime("%Y-%m")
            months[month] = months.get(month, 0.0) + amount
        cache[key] = (round(total, 2), {k: round(v, 2) for k, v in months.items()})
        if has_request_context():
            g._budget_sales = cache
    return cache[key]


def _sales_actual_period(start_dt: datetime, end_dt: datetime) -> float:
    return _sales_snapshot(start_dt, end_dt)[0]


def _monthly_sales_series(start_dt: datetime, end_dt: datetime) -> Dict[str, float]:
    return dict(_sales_snapshot(start_dt, end_dt)[1])


def _load_used_by_category(start_dt: datetime, end_dt: datetime) -> Dict[str, float]:
    pipeline = [
        {
            "$match": {
                "status": "Approved",
                "created_at": {"$gte": start_dt, "$lte": end_dt},
            }
        },
        {
            "$group": {
                "_id": "$category",
                "used_total": {
                    "$sum": {
                        "$toDouble": {"$ifNull": ["$amount", 0]},
                    }
                },
            }
        },
    ]

    out: Dict[str, float] = {}
    for row in manager_expenses_col.aggregate(pipeline):
        cat = _normalize_category(row.get("_id"))
        if cat:
            out[cat] = _safe_float(row.get("used_total"))
    return out


def _build_expense_budget_view(year: int, start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    budget_map = _load_budget_by_category(year)
    used_map = _load_used_by_category(start_dt, end_dt)

    categories = sorted(set(MANAGER_EXPENSE_CATEGORIES) | set(budget_map.keys()) | set(used_map.keys()))

    rows: List[Dict[str, Any]] = []
    total_budget = 0.0
    total_used = 0.0

    for cat in categories:
        budget_amount = _safe_float(budget_map.get(cat, 0.0))
        used_amount = _safe_float(used_map.get(cat, 0.0))
        remaining = budget_amount - used_amount
        used_pct = (used_amount / budget_amount * 100.0) if budget_amount > 0 else 0.0

        total_budget += budget_amount
        total_used += used_amount

        rows.append(
            {
                "category": cat,
                "budget_amount": round(budget_amount, 2),
                "used_amount": round(used_amount, 2),
                "remaining": round(remaining, 2),
                "used_pct": round(used_pct, 2),
                "over_budget": remaining < 0,
            }
        )

    total_remaining = total_budget - total_used
    total_used_pct = (total_used / total_budget * 100.0) if total_budget > 0 else 0.0

    chart_rows = [r for r in rows if r["budget_amount"] > 0 or r["used_amount"] > 0]
    chart_data = {
        "labels": [r["category"] for r in chart_rows],
        "budget": [r["budget_amount"] for r in chart_rows],
        "used": [r["used_amount"] for r in chart_rows],
    }

    return {
        "rows": rows,
        "totals": {
            "total_budget": round(total_budget, 2),
            "total_used": round(total_used, 2),
            "total_remaining": round(total_remaining, 2),
            "total_used_pct": round(total_used_pct, 2),
        },
        "chart": chart_data,
    }


def _month_labels_for_period(start_dt: datetime, end_dt: datetime) -> List[str]:
    labels: List[str] = []
    cursor = datetime(start_dt.year, start_dt.month, 1)
    last = datetime(end_dt.year, end_dt.month, 1)
    while cursor <= last:
        labels.append(cursor.strftime("%Y-%m"))
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1)
    return labels


def _monthly_used_series(start_dt: datetime, end_dt: datetime) -> Dict[str, float]:
    pipeline = [
        {"$match": {"status": "Approved", "created_at": {"$gte": start_dt, "$lte": end_dt}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}},
                "used_total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    out: Dict[str, float] = {}
    for row in manager_expenses_col.aggregate(pipeline):
        out[str(row.get("_id"))] = _safe_float(row.get("used_total"))
    return out


def _daily_used_series(start_dt: datetime, end_dt: datetime) -> Dict[str, float]:
    pipeline = [
        {"$match": {"status": "Approved", "created_at": {"$gte": start_dt, "$lte": end_dt}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "used_total": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    out: Dict[str, float] = {}
    for row in manager_expenses_col.aggregate(pipeline):
        out[str(row.get("_id"))] = _safe_float(row.get("used_total"))
    return out


def _category_monthly_sparklines(start_dt: datetime, end_dt: datetime, top_n: int = 6) -> List[Dict[str, Any]]:
    pipeline = [
        {"$match": {"status": "Approved", "created_at": {"$gte": start_dt, "$lte": end_dt}}},
        {
            "$group": {
                "_id": {
                    "category": "$category",
                    "month": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}},
                },
                "amount": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
            }
        },
    ]
    raw = list(manager_expenses_col.aggregate(pipeline))
    month_labels = _month_labels_for_period(start_dt, end_dt)
    month_idx = {m: i for i, m in enumerate(month_labels)}

    temp: Dict[str, List[float]] = {}
    totals: Dict[str, float] = {}
    for row in raw:
        ident = row.get("_id") or {}
        cat = _normalize_category(ident.get("category"))
        mon = str(ident.get("month") or "")
        if not cat or mon not in month_idx:
            continue
        if cat not in temp:
            temp[cat] = [0.0] * len(month_labels)
            totals[cat] = 0.0
        val = _safe_float(row.get("amount"))
        temp[cat][month_idx[mon]] += val
        totals[cat] += val

    top_cats = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"category": cat, "values": [round(v, 2) for v in temp[cat]]} for cat, _ in top_cats]


def _manager_performance(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    pipeline = [
        {"$match": {"status": "Approved", "created_at": {"$gte": start_dt, "$lte": end_dt}}},
        {
            "$group": {
                "_id": "$manager_id",
                "total_used": {"$sum": {"$toDouble": {"$ifNull": ["$amount", 0]}}},
                "tx_count": {"$sum": 1},
            }
        },
        {"$sort": {"total_used": -1}},
    ]
    grouped = list(manager_expenses_col.aggregate(pipeline))
    manager_ids = [g.get("_id") for g in grouped if g.get("_id") is not None]

    user_docs = list(users_col.find({"_id": {"$in": manager_ids}}, {"name": 1, "branch": 1}))
    user_map = {str(u.get("_id")): u for u in user_docs}

    managers: List[Dict[str, Any]] = []
    branch_totals: Dict[str, float] = {}

    for g in grouped:
        mid = str(g.get("_id"))
        user = user_map.get(mid, {})
        name = user.get("name") or f"Manager {mid[:6]}"
        branch = user.get("branch") or "Unassigned"
        total = round(_safe_float(g.get("total_used")), 2)
        tx_count = int(g.get("tx_count") or 0)
        managers.append({"manager_id": mid, "name": name, "branch": branch, "total_used": total, "tx_count": tx_count})
        branch_totals[branch] = round(branch_totals.get(branch, 0.0) + total, 2)

    top5 = managers[:5]
    branch_split = [{"branch": b, "total_used": v} for b, v in sorted(branch_totals.items(), key=lambda x: x[1], reverse=True)]
    return {"managers": managers, "top5": top5, "branch_split": branch_split}


def _variance_intelligence(rows: List[Dict[str, Any]], totals: Dict[str, Any]) -> Dict[str, Any]:
    overspend = [r for r in rows if r["remaining"] < 0]
    savings = [r for r in rows if r["budget_amount"] > 0 and r["remaining"] > 0]
    overspend.sort(key=lambda r: r["remaining"])  # most negative first
    savings.sort(key=lambda r: r["remaining"], reverse=True)

    util = _safe_float(totals.get("total_used_pct"))
    over_count = len(overspend)
    if util >= 100 or over_count >= 5:
        risk = "High"
    elif util >= 85 or over_count >= 2:
        risk = "Medium"
    else:
        risk = "Low"

    return {
        "risk_score": risk,
        "overspend_count": over_count,
        "top_overspending": overspend[:5],
        "top_savings": savings[:5],
    }


def _trend_analytics(start_dt: datetime, end_dt: datetime, totals: Dict[str, Any]) -> Dict[str, Any]:
    month_labels = _month_labels_for_period(start_dt, end_dt)
    monthly_used_map = _monthly_used_series(start_dt, end_dt)
    monthly_used = [round(_safe_float(monthly_used_map.get(m, 0.0)), 2) for m in month_labels]

    month_count = max(len(month_labels), 1)
    monthly_budget_target = round(_safe_float(totals.get("total_budget")) / month_count, 2)
    monthly_budget = [monthly_budget_target] * len(month_labels)

    daily_map = _daily_used_series(start_dt, end_dt)
    day_labels: List[str] = []
    day_values: List[float] = []
    cursor = start_dt
    while cursor.date() <= end_dt.date():
        key = cursor.date().isoformat()
        day_labels.append(key)
        day_values.append(round(_safe_float(daily_map.get(key, 0.0)), 2))
        cursor = cursor + timedelta(days=1)

    last30 = day_values[-30:] if len(day_values) >= 30 else day_values[:]
    last7 = day_values[-7:] if len(day_values) >= 7 else day_values[:]
    burn_30 = round(sum(last30), 2)
    burn_7 = round(sum(last7), 2)
    avg_daily_30 = round((burn_30 / len(last30)) if last30 else 0.0, 2)

    sparklines = _category_monthly_sparklines(start_dt, end_dt, top_n=6)

    return {
        "monthly_labels": month_labels,
        "monthly_used": monthly_used,
        "monthly_budget": monthly_budget,
        "daily_labels": day_labels[-30:],
        "daily_used": day_values[-30:],
        "burn_7": burn_7,
        "burn_30": burn_30,
        "avg_daily_30": avg_daily_30,
        "category_sparklines": sparklines,
    }


def _build_budget_payload(year: int, start_dt: datetime, end_dt: datetime, start_str: str, end_str: str) -> Dict[str, Any]:
    view = _build_expense_budget_view(year, start_dt, end_dt)
    variance = _variance_intelligence(view["rows"], view["totals"])
    trends = _trend_analytics(start_dt, end_dt, view["totals"])
    manager_perf = _manager_performance(start_dt, end_dt)
    income_budget_amount = _load_income_budget_amount(year)
    sales_actual = _sales_actual_period(start_dt, end_dt)
    income_remaining = round(income_budget_amount - sales_actual, 2)
    income_achieved_pct = round((sales_actual / income_budget_amount * 100.0), 2) if income_budget_amount > 0 else 0.0

    month_labels = trends.get("monthly_labels", [])
    month_count = max(len(month_labels), 1)
    exp_monthly_target = round(_safe_float(view["totals"].get("total_budget")) / month_count, 2)
    inc_monthly_target = round(income_budget_amount / month_count, 2)
    sales_monthly_map = _monthly_sales_series(start_dt, end_dt)
    expense_used_monthly = trends.get("monthly_used", [])
    income_vs_expense = {
        "labels": month_labels,
        "income_budget": [inc_monthly_target for _ in month_labels],
        "income_actual_sales": [round(_safe_float(sales_monthly_map.get(m, 0.0)), 2) for m in month_labels],
        "expense_budget": [exp_monthly_target for _ in month_labels],
        "expense_actual": [round(_safe_float(v), 2) for v in expense_used_monthly],
    }
    return {
        "year": year,
        "start": start_str,
        "end": end_str,
        "rows": view["rows"],
        "totals": view["totals"],
        "chart": view["chart"],
        "variance": variance,
        "trends": trends,
        "manager_performance": manager_perf,
        "income_budget": {
            "budget": round(income_budget_amount, 2),
            "actual_sales": round(sales_actual, 2),
            "remaining": income_remaining,
            "achieved_pct": income_achieved_pct,
        },
        "income_vs_expense": income_vs_expense,
    }


def _build_budget_pdf(view: Dict[str, Any], year: int, start_str: str, end_str: str) -> bytes:
    totals = view["totals"]
    rows = view["rows"]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()

    elements: List[Any] = []
    elements.append(Paragraph("Expense Budget Report", styles["Title"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"Year: <b>{year}</b>", styles["Normal"]))
    elements.append(Paragraph(f"Period (Used Metrics): <b>{start_str}</b> to <b>{end_str}</b>", styles["Normal"]))
    elements.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    summary_data = [
        ["Metric", "Value"],
        ["Total Budget", f"{totals['total_budget']:.2f}"],
        ["Total Used (Approved)", f"{totals['total_used']:.2f}"],
        ["Remaining", f"{totals['total_remaining']:.2f}"],
        ["Utilization (%)", f"{totals['total_used_pct']:.2f}%"],
    ]
    summary_tbl = Table(summary_data, colWidths=[280, 220])
    summary_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(summary_tbl)
    elements.append(Spacer(1, 14))

    detail_data = [["Category", "Budget", "Used (Approved)", "Remaining", "Used %"]]
    for r in rows:
        detail_data.append(
            [
                r["category"],
                f"{r['budget_amount']:.2f}",
                f"{r['used_amount']:.2f}",
                f"{r['remaining']:.2f}",
                f"{r['used_pct']:.2f}%",
            ]
        )

    detail_tbl = Table(detail_data, colWidths=[260, 120, 150, 120, 90], repeatRows=1)
    detail_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16a34a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(Paragraph("Category Breakdown", styles["Heading3"]))
    elements.append(detail_tbl)

    doc.build(elements)
    return buf.getvalue()


def _year_has_budget(year: int) -> bool:
    return (
        budgets_col.find_one(
            {
                "year": year,
                "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}],
            },
            {"_id": 1},
        )
        is not None
    )


@acc_budget.route("/budget", methods=["GET"])
def budget_page():
    year_raw = (request.args.get("year") or "").strip()
    try:
        year = int(year_raw) if year_raw else date.today().year
    except Exception:
        year = date.today().year

    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()
    start_dt, end_dt, start_str, end_str = _resolve_period(year, start_raw, end_raw)
    payload = _build_budget_payload(year, start_dt, end_dt, start_str, end_str)

    current_year = date.today().year
    year_options = list(range(current_year - 4, current_year + 3))

    return render_template(
        "accounting/budget.html",
        year=year,
        year_options=year_options,
        start_str=start_str,
        end_str=end_str,
        categories_all=_all_expense_categories(year, start_dt, end_dt),
        rows=payload["rows"],
        totals=payload["totals"],
        chart_data=payload["chart"],
        variance=payload["variance"],
        trends=payload["trends"],
        manager_performance=payload["manager_performance"],
        income_budget=payload["income_budget"],
        income_vs_expense=payload["income_vs_expense"],
        year_has_budget=_year_has_budget(year),
        last_refresh_utc=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


@acc_budget.route("/budget/live", methods=["GET"])
def budget_live():
    year_raw = (request.args.get("year") or "").strip()
    try:
        year = int(year_raw) if year_raw else date.today().year
    except Exception:
        year = date.today().year

    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()
    start_dt, end_dt, start_str, end_str = _resolve_period(year, start_raw, end_raw)
    payload = _build_budget_payload(year, start_dt, end_dt, start_str, end_str)

    return jsonify(
        ok=True,
        **payload,
        refreshed_at=datetime.utcnow().isoformat() + "Z",
    )


@acc_budget.route("/budget/create", methods=["POST"])
def budget_create():
    year_raw = (request.form.get("year") or "").strip()
    category = _normalize_category(request.form.get("category"))
    amount = _safe_float(request.form.get("amount"))

    try:
        year = int(year_raw)
    except Exception:
        return jsonify(ok=False, message="Invalid year."), 400

    if not category:
        return jsonify(ok=False, message="Category is required."), 400
    if amount < 0:
        return jsonify(ok=False, message="Amount cannot be negative."), 400

    now = datetime.utcnow()

    if amount == 0:
        delete_res = budgets_col.delete_many(
            {
                "year": year,
                "category": category,
                "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}],
            }
        )
        return jsonify(ok=True, message="Budget cleared for category.", deleted=delete_res.deleted_count)

    budgets_col.update_one(
        {"year": year, "kind": "expense", "category": category},
        {
            "$set": {
                "year": year,
                "kind": "expense",
                "category": category,
                "amount": round(amount, 2),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return jsonify(ok=True, message="Budget saved.", year=year, category=category, amount=round(amount, 2))


@acc_budget.route("/budget/create-yearly", methods=["POST"])
def budget_create_yearly():
    """
    Create or replace the yearly expense budget in one action.
    Payload:
      {
        "year": 2026,
        "entries": [{"category":"Fuel","amount":1200}, ...]
      }
    """
    payload = request.get_json(silent=True) or {}
    try:
        year = int(payload.get("year"))
    except Exception:
        return jsonify(ok=False, message="Invalid year."), 400

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return jsonify(ok=False, message="Entries must be a list."), 400

    cleaned: Dict[str, float] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        category = _normalize_category(item.get("category"))
        amount = _safe_float(item.get("amount"))
        if not category:
            continue
        if amount < 0:
            return jsonify(ok=False, message=f"Amount cannot be negative: {category}"), 400
        cleaned[category] = round(amount, 2)

    now = datetime.utcnow()
    ops: List[Any] = []

    # Upsert or clear submitted expense categories (including custom categories).
    for category, amount in cleaned.items():
        if amount > 0:
            ops.append(
                UpdateOne(
                    {"year": year, "kind": "expense", "category": category},
                    {
                        "$set": {
                            "year": year,
                            "kind": "expense",
                            "category": category,
                            "amount": amount,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            )
        else:
            ops.append(
                DeleteMany(
                    {
                        "year": year,
                        "category": category,
                        "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}],
                    }
                )
            )

    if ops:
        budgets_col.bulk_write(ops, ordered=False)

    total_budget = round(sum(cleaned.values()), 2)
    return jsonify(ok=True, message="Yearly budget saved.", year=year, total_budget=total_budget)


@acc_budget.route("/budget/income/set", methods=["POST"])
def budget_income_set():
    payload = request.get_json(silent=True) or {}
    try:
        year = int(payload.get("year"))
    except Exception:
        return jsonify(ok=False, message="Invalid year."), 400

    amount = _safe_float(payload.get("amount"))
    if amount < 0:
        return jsonify(ok=False, message="Income budget cannot be negative."), 400

    now = datetime.utcnow()
    if amount == 0:
        budgets_col.delete_many({"year": year, "kind": "income"})
        return jsonify(ok=True, message="Income budget cleared.", year=year)

    budgets_col.update_one(
        {"year": year, "kind": "income", "category": "Sales"},
        {
            "$set": {
                "year": year,
                "kind": "income",
                "category": "Sales",
                "amount": round(amount, 2),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return jsonify(ok=True, message="Income budget saved.", year=year, amount=round(amount, 2))


@acc_budget.route("/budget/export", methods=["GET"])
def budget_export():
    year_raw = (request.args.get("year") or "").strip()
    try:
        year = int(year_raw) if year_raw else date.today().year
    except Exception:
        year = date.today().year

    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()
    start_dt, end_dt, start_str, end_str = _resolve_period(year, start_raw, end_raw)
    view = _build_expense_budget_view(year, start_dt, end_dt)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Expense Budget Report"])
    writer.writerow(["Year", year])
    writer.writerow(["Period Start", start_str])
    writer.writerow(["Period End", end_str])
    writer.writerow([])
    writer.writerow(["Summary Metrics"])
    writer.writerow(["Total Budget", f"{view['totals']['total_budget']:.2f}"])
    writer.writerow(["Total Used (Approved)", f"{view['totals']['total_used']:.2f}"])
    writer.writerow(["Remaining", f"{view['totals']['total_remaining']:.2f}"])
    writer.writerow(["Utilization (%)", f"{view['totals']['total_used_pct']:.2f}"])
    writer.writerow([])
    writer.writerow(["Category Breakdown"])
    writer.writerow(
        [
            "Year",
            "Start Date",
            "End Date",
            "Category",
            "Budget Amount",
            "Used Amount (Approved)",
            "Remaining",
            "Used %",
        ]
    )

    for row in view["rows"]:
        writer.writerow(
            [
                year,
                start_str,
                end_str,
                row["category"],
                f"{row['budget_amount']:.2f}",
                f"{row['used_amount']:.2f}",
                f"{row['remaining']:.2f}",
                f"{row['used_pct']:.2f}",
            ]
        )

    filename = f"expense_budget_{year}_{start_str}_to_{end_str}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@acc_budget.route("/budget/export/pdf", methods=["GET"])
def budget_export_pdf():
    year_raw = (request.args.get("year") or "").strip()
    try:
        year = int(year_raw) if year_raw else date.today().year
    except Exception:
        year = date.today().year

    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()
    start_dt, end_dt, start_str, end_str = _resolve_period(year, start_raw, end_raw)
    view = _build_expense_budget_view(year, start_dt, end_dt)
    pdf_bytes = _build_budget_pdf(view, year, start_str, end_str)

    filename = f"expense_budget_{year}_{start_str}_to_{end_str}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
