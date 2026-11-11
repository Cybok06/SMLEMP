# routes/meeting_report.py

from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from bson import ObjectId
from datetime import datetime, timedelta, date
import calendar

from db import db
from services.meeting_report_metrics import customer_breakdowns, payment_category, manager_support_metrics, deliveries_by_agent

meeting_report_bp = Blueprint("meeting_report", __name__, url_prefix="/meeting-report")

# Collections
users_col       = db["users"]
customers_col   = db["customers"]
payments_col    = db["payments"]
login_logs_col  = db["login_logs"]   # still available if needed elsewhere
sales_close_col = db["sales_close"]  # not strictly required here, but available
instant_sales_col = db["instant_sales"]


# ---------- Helpers ----------

def _oid(x):
    try:
        return ObjectId(x)
    except Exception:
        return None


def _normalize_manager_id(v):
    if v is None:
        return None, ""
    if isinstance(v, ObjectId):
        return v, str(v)
    try:
        s = str(v).strip()
    except Exception:
        return None, ""
    if not s:
        return None, ""
    return _oid(s), s


def _json_safe(val):
    if isinstance(val, ObjectId):
        return str(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    return val


def _get_current_role():
    """
    Detect logged-in role from session keys set in login.py.
    Returns ('admin'|'manager'|'executive'|None, user_id_str|None)
    """
    if "admin_id" in session:
        return "admin", session["admin_id"]
    if "executive_id" in session:
        return "executive", session["executive_id"]
    if "manager_id" in session:
        return "manager", session["manager_id"]
    return None, None


def _manager_doc_by_id(manager_id):
    mgr_oid = _oid(manager_id)
    if mgr_oid:
        doc = users_col.find_one(
            {"_id": mgr_oid, "role": "manager"},
            {"name": 1, "branch": 1, "phone": 1, "image_url": 1},
        )
        if doc:
            return doc
    return users_col.find_one(
        {"_id": manager_id, "role": "manager"},
        {"name": 1, "branch": 1, "phone": 1, "image_url": 1},
    )


def _manager_id_for_request(role, user_id, requested_manager_id=None):
    if role == "manager":
        return str(user_id)
    return (requested_manager_id or "").strip()


def _manager_agent_query(manager_id):
    manager_doc = _manager_doc_by_id(manager_id)
    refs = {str(manager_id)}
    oids = []
    if manager_doc:
        refs.add(str(manager_doc.get("_id")))
        if isinstance(manager_doc.get("_id"), ObjectId):
            oids.append(manager_doc["_id"])
    else:
        mgr_oid = _oid(manager_id)
        if mgr_oid:
            oids.append(mgr_oid)

    clauses = [{"manager_id": r} for r in refs if r]
    clauses.extend({"manager_id": oid} for oid in oids)
    if not clauses:
        return {"role": "agent", "manager_id": "__no_manager__"}
    return {"role": "agent", "$or": clauses}


def _parse_date(s, default_dt):
    if not s:
        return default_dt
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return default_dt


def _date_to_str(dt):
    return dt.strftime("%Y-%m-%d")


def _this_month_range():
    """
    Returns (start_dt, end_dt) for THIS MONTH (1st–last day, full days).
    """
    today = datetime.utcnow()
    start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if today.month == 12:
        next_month = today.replace(
            year=today.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        next_month = today.replace(
            month=today.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    end = next_month - timedelta(microseconds=1)
    return start, end


def _month_range(year, month):
    """
    Returns (start_dt, end_dt) for a GIVEN MONTH (1st–last day, full days).
    """
    start = datetime(year, month, 1, 0, 0, 0, 0)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, 0, 0, 0, 0)
    else:
        next_month = datetime(year, month + 1, 1, 0, 0, 0, 0)
    end = next_month - timedelta(microseconds=1)
    return start, end


def _year_month_str(year, month, day):
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_date_range(args, default_start, default_end):
    """
    Shared date-range resolver used by both agent_metrics and team_metrics.

    Frontend behaviour:
      - MONTH MODE:
          - Sends year + month + start + end
          - Backend should use year/month for the main range (full month),
            start/end still sent but only for clarity / labels.
      - CUSTOM RANGE MODE:
          - Sends ONLY start + end
          - Backend should ignore year/month and rely purely on start/end.

    Rules:
      - If both year and month are present and valid => month range wins
      - Else => use start/end or default to this month.
    """
    month_param = args.get("month")
    year_param = args.get("year")

    start_dt = default_start
    end_dt = default_end

    if year_param and month_param:
        try:
            y = int(year_param)
            m = int(month_param)
            if 1 <= m <= 12:
                start_dt, end_dt = _month_range(y, m)
            else:
                start_dt, end_dt = default_start, default_end
        except Exception:
            start_dt, end_dt = default_start, default_end
    else:
        # Custom range (or fallback) – use explicit start/end
        start_str_in = args.get("start") or _date_to_str(default_start)
        end_str_in   = args.get("end")   or _date_to_str(default_end)

        start_dt = _parse_date(start_str_in, default_start)
        end_dt   = _parse_date(end_str_in, default_end)
        if end_dt < start_dt:
            end_dt = start_dt

    return start_dt, end_dt


def _build_products_sold(agent_ids, start_str, end_str):
    if not agent_ids:
        return {
            "products_sold_count": 0,
            "products_sold_amount": 0.0,
            "top_products": [],
            "by_purchase_type": {},
            "recent_sales": [],
        }

    qty_expr = {
        "$convert": {
            "input": "$$qty",
            "to": "double",
            "onError": 1,
            "onNull": 1,
        }
    }
    price_expr = {
        "$convert": {
            "input": "$$price",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    total_expr = {
        "$convert": {
            "input": "$$total",
            "to": "double",
            "onError": None,
            "onNull": None,
        }
    }

    def _sum_expr(q_field, price_field, total_field):
        return {
            "$let": {
                "vars": {
                    "qty": q_field,
                    "price": price_field,
                    "total": total_field,
                },
                "in": {
                    "$ifNull": [
                        total_expr,
                        {"$multiply": [qty_expr, price_expr]},
                    ]
                },
            }
        }

    def _qty_expr(q_field):
        return {
            "$let": {
                "vars": {"qty": q_field},
                "in": qty_expr,
            }
        }

    # ---- Purchases from customers (installment/hire/savings) ----
    purchases_match = {
        "agent_id": {"$in": agent_ids},
    }
    purchases_date_match = {
        "purchases.purchase_date": {"$gte": start_str, "$lte": end_str}
    }

    purchases_pipeline = [
        {"$match": purchases_match},
        {"$unwind": "$purchases"},
        {"$match": purchases_date_match},
        {
            "$group": {
                "_id": "$purchases.product.name",
                "quantity_sold": {
                    "$sum": _qty_expr("$purchases.product.quantity")
                },
                "total_amount": {
                    "$sum": _sum_expr(
                        "$purchases.product.quantity",
                        "$purchases.product.price",
                        "$purchases.product.total",
                    )
                },
            }
        },
    ]

    instant_pipeline = [
        {
            "$match": {
                "agent_id": {"$in": agent_ids},
                "purchase_date": {"$gte": start_str, "$lte": end_str},
            }
        },
        {
            "$group": {
                "_id": "$product.name",
                "quantity_sold": {
                    "$sum": _qty_expr("$product.quantity")
                },
                "total_amount": {
                    "$sum": _sum_expr(
                        "$product.quantity",
                        "$product.price",
                        "$product.total",
                    )
                },
            }
        },
    ]

    purchase_type_pipeline = [
        {"$match": purchases_match},
        {"$unwind": "$purchases"},
        {"$match": purchases_date_match},
        {
            "$group": {
                "_id": {
                    "$ifNull": ["$purchases.purchase_type", "Installment"]
                },
                "quantity_sold": {
                    "$sum": _qty_expr("$purchases.product.quantity")
                },
                "total_amount": {
                    "$sum": _sum_expr(
                        "$purchases.product.quantity",
                        "$purchases.product.price",
                        "$purchases.product.total",
                    )
                },
            }
        },
    ]

    instant_type_pipeline = [
        {
            "$match": {
                "agent_id": {"$in": agent_ids},
                "purchase_date": {"$gte": start_str, "$lte": end_str},
            }
        },
        {
            "$group": {
                "_id": "Instant Sale",
                "quantity_sold": {
                    "$sum": _qty_expr("$product.quantity")
                },
                "total_amount": {
                    "$sum": _sum_expr(
                        "$product.quantity",
                        "$product.price",
                        "$product.total",
                    )
                },
            }
        },
    ]

    purchases_recent_pipeline = [
        {"$match": purchases_match},
        {"$unwind": "$purchases"},
        {"$match": purchases_date_match},
        {
            "$project": {
                "_id": 0,
                "date": "$purchases.purchase_date",
                "customer_name": "$name",
                "product_name": "$purchases.product.name",
                "qty": _qty_expr("$purchases.product.quantity"),
                "total": _sum_expr(
                    "$purchases.product.quantity",
                    "$purchases.product.price",
                    "$purchases.product.total",
                ),
                "purchase_type": {
                    "$ifNull": ["$purchases.purchase_type", "Installment"]
                },
                "agent_id": "$agent_id",
            }
        },
        {"$sort": {"date": -1}},
        {"$limit": 10},
    ]

    instant_recent_pipeline = [
        {
            "$match": {
                "agent_id": {"$in": agent_ids},
                "purchase_date": {"$gte": start_str, "$lte": end_str},
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$purchase_date",
                "customer_name": "$customer_name",
                "product_name": "$product.name",
                "qty": _qty_expr("$product.quantity"),
                "total": _sum_expr(
                    "$product.quantity",
                    "$product.price",
                    "$product.total",
                ),
                "purchase_type": "Instant Sale",
                "agent_id": "$agent_id",
            }
        },
        {"$sort": {"date": -1}},
        {"$limit": 10},
    ]

    purchase_products = list(
        customers_col.aggregate(purchases_pipeline, allowDiskUse=True)
    )
    instant_products = list(
        instant_sales_col.aggregate(instant_pipeline, allowDiskUse=True)
    )
    purchase_types = list(
        customers_col.aggregate(purchase_type_pipeline, allowDiskUse=True)
    )
    instant_types = list(
        instant_sales_col.aggregate(instant_type_pipeline, allowDiskUse=True)
    )
    recent_purchases = list(
        customers_col.aggregate(purchases_recent_pipeline, allowDiskUse=True)
    )
    recent_instant = list(
        instant_sales_col.aggregate(instant_recent_pipeline, allowDiskUse=True)
    )

    products_map = {}
    def _merge_prod(name, qty, amount):
        if not name:
            return
        key = str(name)
        cur = products_map.get(key, {"quantity": 0.0, "amount": 0.0})
        cur["quantity"] += float(qty or 0)
        cur["amount"] += float(amount or 0)
        products_map[key] = cur

    for doc in purchase_products:
        _merge_prod(doc.get("_id"), doc.get("quantity_sold"), doc.get("total_amount"))
    for doc in instant_products:
        _merge_prod(doc.get("_id"), doc.get("quantity_sold"), doc.get("total_amount"))

    top_products = []
    for name, info in products_map.items():
        qty = float(info.get("quantity", 0))
        amount = float(info.get("amount", 0))
        avg = (amount / qty) if qty > 0 else 0.0
        top_products.append({
            "name": name,
            "quantity_sold": round(qty, 2),
            "total_amount": round(amount, 2),
            "avg_unit_price": round(avg, 2),
        })
    top_products.sort(key=lambda x: x["total_amount"], reverse=True)
    top_products = top_products[:10]

    by_type = {}
    def _merge_type(label, qty, amount):
        if not label:
            return
        key = str(label)
        cur = by_type.get(key, {"quantity_sold": 0.0, "total_amount": 0.0})
        cur["quantity_sold"] += float(qty or 0)
        cur["total_amount"] += float(amount or 0)
        by_type[key] = cur

    for doc in purchase_types:
        _merge_type(doc.get("_id"), doc.get("quantity_sold"), doc.get("total_amount"))
    for doc in instant_types:
        _merge_type(doc.get("_id"), doc.get("quantity_sold"), doc.get("total_amount"))

    by_purchase_type = {
        k: {
            "quantity_sold": round(v["quantity_sold"], 2),
            "total_amount": round(v["total_amount"], 2),
        }
        for k, v in by_type.items()
    }

    recent_sales = recent_purchases + recent_instant
    recent_sales.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    recent_sales = recent_sales[:10]

    total_qty = sum(float(v["quantity_sold"]) for v in by_type.values()) if by_type else 0.0
    total_amount = sum(float(v["total_amount"]) for v in by_type.values()) if by_type else 0.0

    return {
        "products_sold_count": round(total_qty, 2),
        "products_sold_amount": round(total_amount, 2),
        "top_products": top_products,
        "by_purchase_type": by_purchase_type,
        "recent_sales": recent_sales,
    }


# ---------- MAIN PAGE (filters only) ----------

@meeting_report_bp.route("/", methods=["GET"])
def overview():
    role, user_id = _get_current_role()
    if role is None:
        return redirect(url_for("login.login"))

    if role == "manager":
        manager_doc = _manager_doc_by_id(user_id)
        if not manager_doc:
            return redirect(url_for("login.logout"))
        managers_raw = [manager_doc]
    else:
        managers_raw = list(
            users_col.find(
                {"role": "manager"},
                {"name": 1, "branch": 1, "phone": 1, "image_url": 1}
            ).sort("name", 1)
        )

    manager_map = {
        str(m["_id"]): {
            "name": m.get("name", "Unknown"),
            "branch": m.get("branch", ""),
        }
        for m in managers_raw
    }


    # Manager filter options
    manager_options = [
        {
            "id": str(m["_id"]),
            "name": m.get("name", "Unknown"),
            "branch": m.get("branch", "")
        }
        for m in managers_raw
    ]

    # Agents list (single query)
    if role == "manager":
        agents_raw = list(
            users_col.find(
                _manager_agent_query(user_id),
                {"name": 1, "branch": 1, "phone": 1, "image_url": 1, "manager_id": 1},
            ).sort("name", 1)
        )
    else:
        agents_raw = list(
            users_col.find(
                {"role": "agent"},
                {"name": 1, "branch": 1, "phone": 1, "image_url": 1, "manager_id": 1},
            ).sort("name", 1)
        )

    agents_data = []
    for ag in agents_raw:
        mid_val = ag.get("manager_id")
        _, mid_str = _normalize_manager_id(mid_val)
        mgr = manager_map.get(mid_str) if mid_str else None
        manager_name = mgr.get("name") if mgr else ""
        manager_branch = mgr.get("branch") if mgr else ""

        agents_data.append({
            "id": str(ag["_id"]),
            "name": ag.get("name", "Unknown"),
            "branch": ag.get("branch", ""),
            "phone": ag.get("phone", ""),
            "image_url": ag.get("image_url", ""),
            "manager_id": mid_str,
            "manager_name": manager_name,
            "manager_branch": manager_branch,
        })

    # Default date range = THIS MONTH
    default_start, default_end = _this_month_range()

    # Current year/month (for top month filter)
    today = datetime.utcnow()
    current_year = today.year
    current_month = today.month

    return render_template(
        "meeting_report_overview.html",
        role=role,
        managers=manager_options,
        agents=agents_data,
        default_start=_date_to_str(default_start),
        default_end=_date_to_str(default_end),
        current_year=current_year,
        current_month=current_month,
        agents_count=len(agents_data),
        managers_count=len(managers_raw),
    )


@meeting_report_bp.route("/agents-json", methods=["GET"])
def agents_json():
    role, user_id = _get_current_role()
    if role is None:
        return jsonify(ok=False, message="Unauthorized"), 401

    manager_id = _manager_id_for_request(role, user_id, request.args.get("manager_id"))
    agent_query = {"role": "agent"}
    if manager_id:
        try:
            mgr_oid = ObjectId(manager_id)
            agent_query["$or"] = [
                {"manager_id": mgr_oid},
                {"manager_id": manager_id},
            ]
        except Exception:
            agent_query["manager_id"] = manager_id

    if role == "manager":
        manager_doc = _manager_doc_by_id(user_id)
        if not manager_doc:
            return jsonify(ok=False, message="Manager not found"), 404
        managers_raw = [manager_doc]
    else:
        managers_raw = list(
            users_col.find(
                {"role": "manager"},
                {"name": 1, "branch": 1}
            )
        )
    manager_map = {
        str(m["_id"]): {
            "name": m.get("name", "Unknown"),
            "branch": m.get("branch", ""),
        }
        for m in managers_raw
    }

    agents_raw = list(
        users_col.find(
            agent_query,
            {"name": 1, "branch": 1, "phone": 1, "image_url": 1, "manager_id": 1},
        ).sort("name", 1)
    )

    agents_data = []
    for ag in agents_raw:
        mid_val = ag.get("manager_id")
        _, mid_str = _normalize_manager_id(mid_val)
        mgr = manager_map.get(mid_str) if mid_str else None
        manager_name = mgr.get("name") if mgr else ""
        manager_branch = mgr.get("branch") if mgr else ""

        agents_data.append({
            "id": str(ag["_id"]),
            "name": ag.get("name", "Unknown"),
            "branch": ag.get("branch", ""),
            "phone": ag.get("phone", ""),
            "image_url": ag.get("image_url", ""),
            "manager_id": mid_str,
            "manager_name": manager_name,
            "manager_branch": manager_branch,
        })

    return jsonify(_json_safe(agents_data))


# ---------- PAGE: full agent performance (separate page) ----------

@meeting_report_bp.route("/agent-performance", methods=["GET"])
def agent_performance_page():
    """
    Separate page for a deeper agent performance / leaderboard view.
    Uses /meeting-report/team-metrics JSON API on the front-end.
    """
    role, user_id = _get_current_role()
    if role is None:
        return redirect(url_for("login.login"))

    if role == "manager":
        manager_doc = _manager_doc_by_id(user_id)
        if not manager_doc:
            return redirect(url_for("login.logout"))
        managers_raw = [manager_doc]
    else:
        managers_raw = list(
            users_col.find(
                {"role": "manager"},
                {"name": 1, "branch": 1}
            ).sort("name", 1)
        )

    manager_options = [
        {
            "id": str(m["_id"]),
            "name": m.get("name", "Unknown"),
            "branch": m.get("branch", "")
        }
        for m in managers_raw
    ]

    today = datetime.utcnow()
    current_year = today.year
    current_month = today.month

    return render_template(
        "agent_performance.html",
        role=role,
        managers=manager_options,
        current_year=current_year,
        current_month=current_month,
    )


# ---------- JSON API: metrics for AGENT / MANAGER / BRANCH ----------

@meeting_report_bp.route("/agent-metrics", methods=["GET"])
def agent_metrics():
    """
    Metrics endpoint used by meeting_report_overview page.

    Supports 2 scopes:
      - scope=agent   + agent_id   (default / backwards compatible)
      - scope=manager + manager_id (aggregates all agents under that manager)
    """
    role, user_id = _get_current_role()
    if role is None:
        return jsonify(ok=False, message="Unauthorized"), 401

    scope = (request.args.get("scope") or "agent").strip().lower()
    if scope == "branch":
        manager_id_str = (request.args.get("manager_id") or "").strip()
        if manager_id_str:
            scope = "manager"
        else:
            return jsonify(ok=False, message="branch scope removed"), 400
    if scope not in ("agent", "manager"):
        scope = "agent"

    # ----- Determine main range from month/year OR explicit start/end (month vs custom mode) -----
    default_start, default_end = _this_month_range()
    start_dt, end_dt = _resolve_date_range(request.args, default_start, default_end)

    # Raw year/month params (for yearly-trend logic)
    year_param = request.args.get("year")
    month_param = request.args.get("month")

    start_str = _date_to_str(start_dt)
    end_str   = _date_to_str(end_dt)

    # ----- Year for MONTHLY trend -----
    try:
        trend_year = int(year_param) if year_param else start_dt.year
    except Exception:
        trend_year = start_dt.year

    # Optional comparison range (still supported; frontend only sends this in month mode)
    compare_start_str = request.args.get("compare_start") or ""
    compare_end_str   = request.args.get("compare_end") or ""
    compare = None
    if compare_start_str and compare_end_str:
        cs_dt = _parse_date(compare_start_str, start_dt)
        ce_dt = _parse_date(compare_end_str, cs_dt)
        if ce_dt < cs_dt:
            ce_dt = cs_dt
        compare = {
            "start_dt": cs_dt,
            "end_dt": ce_dt,
            "start_str": _date_to_str(cs_dt),
            "end_str": _date_to_str(ce_dt),
        }

    # ---------- Resolve SUBJECT (agent / manager) & agent_ids ----------

    subject = {}
    agent_ids = []  # list of agent_id strings to include in all calculations

    if scope == "agent":
        agent_id = (request.args.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify(ok=False, message="agent_id is required for scope=agent"), 400

        try:
            ag_oid = ObjectId(agent_id)
        except Exception:
            return jsonify(ok=False, message="Invalid agent_id"), 400

        agent = users_col.find_one(
            {"_id": ag_oid, "role": "agent"},
            {"name": 1, "branch": 1, "phone": 1, "image_url": 1, "manager_id": 1}
        )
        if not agent:
            return jsonify(ok=False, message="Agent not found"), 404

        if role == "manager":
            owner_query = _manager_agent_query(user_id)
            owner_query["_id"] = ag_oid
            if not users_col.find_one(owner_query, {"_id": 1}):
                return jsonify(ok=False, message="Forbidden: agent not under this manager"), 403

        mgr_name = ""
        mgr_branch = ""
        manager_id = agent.get("manager_id")
        if manager_id:
            # manager_id may be ObjectId or string; try ObjectId first
            mgr_filter = {"_id": manager_id}
            if not isinstance(manager_id, ObjectId):
                try:
                    mgr_filter = {"_id": ObjectId(manager_id)}
                except Exception:
                    mgr_filter = {"_id": manager_id}
            mgr_doc = users_col.find_one(mgr_filter, {"name": 1, "branch": 1})
            if mgr_doc:
                mgr_name = mgr_doc.get("name", "")
                mgr_branch = mgr_doc.get("branch", "")

        subject = {
            "type": "agent",
            "id": agent_id,
            "name": agent.get("name", "Unknown"),
            "branch": agent.get("branch", ""),
            "phone": agent.get("phone", ""),
            "image_url": agent.get("image_url", ""),
            "manager_name": mgr_name,
            "manager_branch": mgr_branch,
        }
        agent_ids = [agent_id]

    elif scope == "manager":
        manager_id_str = _manager_id_for_request(role, user_id, request.args.get("manager_id"))
        if not manager_id_str:
            return jsonify(ok=False, message="manager_id is required for scope=manager"), 400

        # Load manager
        mgr_filter = None
        try:
            mgr_oid = ObjectId(manager_id_str)
            mgr_filter = {"_id": mgr_oid}
        except Exception:
            mgr_filter = {"_id": manager_id_str}

        manager = users_col.find_one(
            {**mgr_filter, "role": "manager"},
            {"name": 1, "branch": 1, "phone": 1, "image_url": 1}
        )
        if not manager:
            return jsonify(ok=False, message="Manager not found"), 404

        mgr_id_str = str(manager["_id"])
        agent_query = _manager_agent_query(mgr_id_str)
        agents = list(
            users_col.find(agent_query, {"_id": 1})
        )
        agent_ids = [str(a["_id"]) for a in agents]

        subject = {
            "type": "manager",
            "id": mgr_id_str,
            "name": manager.get("name", "Unknown Manager"),
            "branch": manager.get("branch", ""),
            "phone": manager.get("phone", ""),
            "image_url": manager.get("image_url", ""),
            "manager_name": "",   # no "manager of manager" for now
            "manager_branch": manager.get("branch", ""),
        }

    support_metrics = manager_support_metrics(db, subject['id'], start_dt, end_dt) if scope == 'manager' else None
    delivery_counts = deliveries_by_agent(db, agent_ids, start_dt, end_dt)
    # If manager has no agents, return an empty but OK payload
    if scope == "manager" and not agent_ids:
        empty_data = {
            "ok": True,
            "agent": {**subject, 'scope': subject['type']},
            "range": {"start": start_str, "end": end_str},
            "summary": {
                "total_sales": 0.0,
                "support_activity": support_metrics,
                "deliveries_done": 0,
                "sales_breakdown": {"susu": 0.0, "products": 0.0},
                "customer_breakdown": {"active": 0, "completed": 0},
                "payments_count": 0,
                "total_customers": 0,
                "active_customers": 0,
                "inactive_customers": 0,
                "attendance_rate": 0.0,
                "present_days": 0,
                "working_days": (end_dt.date() - start_dt.date()).days + 1,
                "leads_total": 0,
                "leads_converted": 0,
                "conversion_rate": 0.0,
                "products_sold_count": 0,
                "products_sold_amount": 0.0,
            },
            "payments_by_date": [],
            "customers": {
                "top_active": [],
                "inactive": [],
            },
            "leads": {"recent": []},
            "products_sold": {
                "products_sold_count": 0,
                "products_sold_amount": 0.0,
                "top_products": [],
                "by_purchase_type": {},
                "recent_sales": [],
            },
            "attendance": {
                "present_days": 0,
                "working_days": (end_dt.date() - start_dt.date()).days + 1,
            },
            "compare": None,
            "yearly_trend": {
                "year": trend_year,
                "months": [
                    {
                        "month": m,
                        "label": calendar.month_abbr[m],
                        "total_amount": 0.0,
                        "payments_count": 0,
                        "worked_days": 0,
                        "total_days": calendar.monthrange(trend_year, m)[1],
                    }
                    for m in range(1, 13)
                ]
            },
        }
        return jsonify(_json_safe(empty_data))

    # ---------- MAIN RANGE METRICS (DATE RANGE) ----------

    # Payments (sales) in main range
    if scope == "agent":
        pay_agent_filter = subject["id"]
    else:
        pay_agent_filter = {"$in": agent_ids}

    pay_q = {
        "agent_id": pay_agent_filter,
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": start_str, "$lte": end_str}
    }
    payments = list(
        payments_col.find(
            pay_q,
            {"amount": 1, "date": 1, "customer_id": 1, "payment_type": 1, "is_susu": 1}
        )
    )

    total_sales = 0.0
    sales_breakdown = {"susu": 0.0, "products": 0.0}
    payments_count = len(payments)
    payments_by_date = {}
    totals_by_customer = {}

    for p in payments:
        try:
            amt = float(p.get("amount", 0.0))
        except Exception:
            amt = 0.0
        total_sales += amt
        sales_breakdown[payment_category(p)] += amt

        d = p.get("date") or start_str  # 'YYYY-MM-DD'
        payments_by_date.setdefault(d, {"date": d, "amount": 0.0, "count": 0})
        payments_by_date[d]["amount"] += amt
        payments_by_date[d]["count"] += 1

        cust_id = p.get("customer_id")
        if cust_id:
            key = str(cust_id)
            totals_by_customer.setdefault(key, 0.0)
            totals_by_customer[key] += amt

    payments_by_date_list = sorted(
        payments_by_date.values(), key=lambda x: x["date"]
    )

    # Customers under this agent/manager (ALL TIME)
    if scope == "agent":
        cust_q = {"agent_id": subject["id"]}
    else:
        cust_q = {"agent_id": {"$in": agent_ids}}

    portfolio = customer_breakdowns(customers_col, payments_col, cust_q)
    customer_breakdown = {key: sum(row[key] for row in portfolio.values()) for key in ('active', 'completed')}
    total_customers = sum(customer_breakdown.values())

    # Active customers IN RANGE (based on payments)
    active_customer_ids = payments_col.distinct("customer_id", pay_q)
    active_customers = len(active_customer_ids) if active_customer_ids else 0
    attendance_rate = (active_customers / total_customers * 100) if total_customers > 0 else 0.0
    inactive_customers_count = max(total_customers - active_customers, 0)

    # Top active customers (by amount in main range)
    top_active = []
    if totals_by_customer:
        sorted_items = sorted(
            totals_by_customer.items(),
            key=lambda kv: kv[1],
            reverse=True
        )[:5]
        top_ids = [
            ObjectId(cid_str)
            for cid_str, _ in sorted_items
            if ObjectId.is_valid(cid_str)
        ]
        # pre-fetch customer docs in one query for speed
        cust_docs_map = {
            str(c["_id"]): c
            for c in customers_col.find(
                {"_id": {"$in": top_ids}},
                {"name": 1, "phone_number": 1}
            )
        }

        for cid_str, tot_amt in sorted_items:
            cdoc = cust_docs_map.get(cid_str)
            if not cdoc:
                continue
            top_active.append({
                "name": cdoc.get("name", "Unknown"),
                "phone": cdoc.get("phone_number", "N/A"),
                "amount_paid": round(tot_amt, 2),
            })

    # Inactive customers (no payment IN RANGE) – sample list (up to 5)
    inactive = []
    if total_customers > 0:
        all_customers = customers_col.find(
            cust_q,
            {"name": 1, "phone_number": 1}
        )

        active_id_set = {str(cid) for cid in active_customer_ids}

        for c in all_customers:
            if len(inactive) >= 5:
                break
            cid_str = str(c["_id"])
            if cid_str in active_id_set:
                continue

            # Only fetch last payment if we actually use this customer
            last_pay = payments_col.find_one(
                {"customer_id": c["_id"], "payment_type": {"$ne": "WITHDRAWAL"}},
                sort=[("date", -1)],
                projection={"date": 1},
            )
            last_date = last_pay.get("date") if last_pay else None
            inactive.append({
                "name": c.get("name", "Unknown"),
                "phone": c.get("phone_number", "N/A"),
                "last_payment_date": last_date
            })

    # ---------- ATTENDANCE BASED ON PAYMENTS (>= 10 PAYMENTS = WORKED) ----------

    # Present days = number of days in the range where there were >= 10 payments
    present_days = sum(1 for row in payments_by_date_list if row["count"] >= 10)

    # Working days in range = total calendar days in the selected date range
    working_days = (end_dt.date() - start_dt.date()).days + 1
    working_days = working_days if working_days > 0 else 0

    # Leads in main range
    leads_total = 0
    leads_converted = 0
    leads_recent = []
    try:
        leads_filter = {
            "lead_registered_at": {"$gte": start_dt, "$lte": end_dt}
        }
        converted_filter = {
            "lead_converted_at": {"$gte": start_dt, "$lte": end_dt}
        }
        if scope == "agent":
            leads_filter["agent_id"] = subject["id"]
            converted_filter["agent_id"] = subject["id"]
        else:
            leads_filter["agent_id"] = {"$in": agent_ids}
            converted_filter["agent_id"] = {"$in": agent_ids}

        leads_total = customers_col.count_documents(leads_filter)
        leads_converted = customers_col.count_documents(converted_filter)

        leads_recent = list(
            customers_col.find(
                leads_filter,
                {
                    "_id": 0,
                    "name": 1,
                    "phone_number": 1,
                    "lead_stage": 1,
                    "lead_registered_at": 1,
                    "lead_converted_at": 1,
                },
            )
            .sort([("lead_registered_at", -1)])
            .limit(5)
        )

        def _fmt_dt(dt_val):
            if not dt_val:
                return None
            if isinstance(dt_val, datetime):
                return dt_val.strftime("%Y-%m-%d")
            try:
                return str(dt_val)
            except Exception:
                return None

        for ld in leads_recent:
            ld["lead_registered_at"] = _fmt_dt(ld.get("lead_registered_at"))
            ld["lead_converted_at"] = _fmt_dt(ld.get("lead_converted_at"))
            ld["lead_stage"] = ld.get("lead_stage") or ""
            ld["phone_number"] = ld.get("phone_number") or ""
            ld["name"] = ld.get("name", "Unknown")
    except Exception:
        leads_total = 0
        leads_converted = 0
        leads_recent = []

    conv_rate = (leads_converted / leads_total * 100) if leads_total > 0 else 0.0

    # ---------- PRODUCTS SOLD (installment + instant) ----------
    products_sold = _build_products_sold(agent_ids, start_str, end_str)

    # ---------- COMPARISON RANGE (optional) ----------

    compare_summary = None
    if compare:
        c_pay_q = {
            "agent_id": pay_agent_filter,
            "payment_type": {"$ne": "WITHDRAWAL"},
            "date": {"$gte": compare["start_str"], "$lte": compare["end_str"]}
        }
        c_payments = payments_col.find(
            c_pay_q,
            {"amount": 1, "payment_type": 1, "is_susu": 1}
        )
        c_total_sales = 0.0
        c_sales_breakdown = {'susu': 0.0, 'products': 0.0}
        c_payments_count = 0
        for p in c_payments:
            c_payments_count += 1
            try:
                amount = float(p.get("amount", 0.0))
                c_total_sales += amount
                c_sales_breakdown[payment_category(p)] += amount
            except Exception:
                pass

        c_active_ids = payments_col.distinct("customer_id", c_pay_q)
        c_active_customers = len(c_active_ids) if c_active_ids else 0

        def _pct_change(new, old):
            if old == 0:
                return None
            return (new - old) / old * 100.0

        compare_summary = {
            "start": compare["start_str"],
            "end": compare["end_str"],
            "total_sales": round(c_total_sales, 2),
            "sales_breakdown": {key: round(value, 2) for key, value in c_sales_breakdown.items()},
            "payments_count": c_payments_count,
            "active_customers": c_active_customers,
            "delta_sales_pct": _pct_change(total_sales, c_total_sales),
            "delta_payments_pct": _pct_change(payments_count, c_payments_count),
            "delta_active_customers_pct": _pct_change(active_customers, c_active_customers),
        }

    # ---------- YEARLY MONTH-BY-MONTH TREND (payments + work days) ----------

    months_info = []
    for m in range(1, 13):
        total_days_in_month = calendar.monthrange(trend_year, m)[1]
        months_info.append({
            "month": m,
            "label": calendar.month_abbr[m],
            "total_amount": 0.0,
            "payments_count": 0,
            "worked_days": 0,
            "total_days": total_days_in_month,
            "_day_counts": {}  # internal, will be removed before return
        })

    year_start_str = _year_month_str(trend_year, 1, 1)
    year_end_str   = _year_month_str(trend_year, 12, 31)

    year_pay_q = {
        "agent_id": pay_agent_filter,
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": year_start_str, "$lte": year_end_str}
    }
    year_payments = payments_col.find(
        year_pay_q,
        {"amount": 1, "date": 1}
    )

    for p in year_payments:
        d_str = p.get("date")
        if not d_str:
            continue
        try:
            y, m, d = [int(x) for x in d_str.split("-")]
        except Exception:
            continue
        if y != trend_year or m < 1 or m > 12:
            continue

        idx = m - 1
        try:
            amt = float(p.get("amount", 0.0))
        except Exception:
            amt = 0.0

        m_info = months_info[idx]
        m_info["total_amount"] += amt
        m_info["payments_count"] += 1

        day_counts = m_info["_day_counts"]
        day_counts[d_str] = day_counts.get(d_str, 0) + 1

    for m_info in months_info:
        day_counts = m_info.get("_day_counts", {})
        # >=10 payments = "worked"
        worked_days = sum(1 for _, cnt in day_counts.items() if cnt >= 10)
        m_info["worked_days"] = worked_days
        m_info["total_amount"] = round(m_info["total_amount"], 2)
        if "_day_counts" in m_info:
            del m_info["_day_counts"]

    yearly_trend = {
        "year": trend_year,
        "months": months_info
    }

    # ---------- Build response ----------

    data = {
        "ok": True,
        # For backwards compatibility the frontend still uses data.agent
        "agent": {
            "id": subject["id"],
            "name": subject["name"],
            "branch": subject["branch"],
            "phone": subject["phone"],
            "image_url": subject["image_url"],
            "manager_name": subject.get("manager_name", ""),
            "manager_branch": subject.get("manager_branch", ""),
            "scope": subject["type"],  # extra field if frontend wants to know what is being viewed
        },
        "range": {
            "start": start_str,
            "end": end_str,
        },
        "summary": {
            "total_sales": round(total_sales, 2),
            "support_activity": support_metrics,
            "deliveries_done": sum(delivery_counts.values()),
            "sales_breakdown": {key: round(value, 2) for key, value in sales_breakdown.items()},
            "customer_breakdown": customer_breakdown,
            "payments_count": payments_count,
            "total_customers": total_customers,
            "active_customers": active_customers,
            "inactive_customers": inactive_customers_count,
            "attendance_rate": round(attendance_rate, 1),
            "present_days": present_days,     # payment-based
            "working_days": working_days,     # calendar days in range
            "leads_total": leads_total,
            "leads_converted": leads_converted,
            "conversion_rate": round(conv_rate, 1),
            "products_sold_count": products_sold.get("products_sold_count", 0),
            "products_sold_amount": products_sold.get("products_sold_amount", 0.0),
        },
        "payments_by_date": [
            {
                "date": row["date"],
                "amount": round(row["amount"], 2),
                "count": row["count"],
            }
            for row in payments_by_date_list
        ],
        "customers": {
            "top_active": top_active,
            "inactive": inactive,
        },
        "leads": {
            "recent": leads_recent,
        },
        "products_sold": {
            "products_sold_count": products_sold.get("products_sold_count", 0),
            "products_sold_amount": products_sold.get("products_sold_amount", 0.0),
            "top_products": products_sold.get("top_products", []),
            "by_purchase_type": products_sold.get("by_purchase_type", {}),
            "recent_sales": products_sold.get("recent_sales", []),
        },
        "attendance": {
            "present_days": present_days,
            "working_days": working_days,
        },
        "compare": compare_summary,
        "yearly_trend": yearly_trend,
    }

    return jsonify(_json_safe(data))


# ---------- JSON API: TEAM / LEADERBOARD METRICS (ALL AGENTS) ----------

@meeting_report_bp.route("/team-metrics", methods=["GET"])
def team_metrics():
    """
    Aggregated metrics for ALL agents under a selected manager.
    Used for the agent performance page leaderboard and charts.

    Query params:
      - manager_id (required)
      - month, year (optional; if present, override start/end with full month)
      - start, end (date range; defaults = this month)
    """
    role, user_id = _get_current_role()
    if role is None:
        return jsonify(ok=False, message="Unauthorized"), 401

    manager_id_str = _manager_id_for_request(role, user_id, request.args.get("manager_id"))
    branch = (request.args.get("branch") or "").strip()

    if branch and not manager_id_str:
        return jsonify(ok=False, message="branch filtering removed; use manager filter"), 400
    if not manager_id_str:
        return jsonify(ok=False, message="manager_id is required"), 400

    # ----- Date range (month/year first, then start/end) -----
    default_start, default_end = _this_month_range()
    start_dt, end_dt = _resolve_date_range(request.args, default_start, default_end)

    start_str = _date_to_str(start_dt)
    end_str   = _date_to_str(end_dt)

    # ----- Pick agents under manager -----
    agent_query = _manager_agent_query(manager_id_str)

    agents = list(
        users_col.find(
            agent_query,
            {"name": 1, "branch": 1, "phone": 1, "image_url": 1}
        )
    )
    if not agents:
        return jsonify(_json_safe({"ok": True, "agents": [], "range": {"start": start_str, "end": end_str}}))

    agent_ids_str = [str(a["_id"]) for a in agents]
    delivery_counts = deliveries_by_agent(db, agent_ids_str, start_dt, end_dt)

    # ----- Payments aggregation (one query for all agents) -----
    pay_match = {
        "agent_id": {"$in": agent_ids_str},
        "payment_type": {"$ne": "WITHDRAWAL"},
        "date": {"$gte": start_str, "$lte": end_str}
    }

    pay_pipeline = [
        {"$match": pay_match},
        {
            "$group": {
                "_id": {
                    "agent_id": "$agent_id",
                    "date": "$date"
                },
                "total_amount_day": {"$sum": "$amount"},
                "payments_count_day": {"$sum": 1},
                "customers_in_day": {"$addToSet": "$customer_id"},
            }
        },
        {
            "$group": {
                "_id": "$_id.agent_id",
                "total_amount": {"$sum": "$total_amount_day"},
                "payments_count": {"$sum": "$payments_count_day"},
                "customer_ids": {"$addToSet": "$customers_in_day"},
                "work_days": {
                    "$sum": {
                        # >=10 payments in a day counts as a "work" day
                        "$cond": [
                            {"$gte": ["$payments_count_day", 10]},
                            1,
                            0
                        ]
                    }
                }
            }
        }
    ]

    pay_stats = list(payments_col.aggregate(pay_pipeline, allowDiskUse=True))
    payments_by_agent = {}
    for doc in pay_stats:
        agent_key = doc["_id"]
        # customer_ids is a list of sets, flatten
        raw_sets = doc.get("customer_ids", [])
        flat_customers = set()
        for s in raw_sets:
            for cid in s:
                flat_customers.add(cid)

        payments_by_agent[agent_key] = {
            "total_amount": float(doc.get("total_amount", 0.0)),
            "payments_count": int(doc.get("payments_count", 0)),
            "active_customers": len(flat_customers),
            "work_days": int(doc.get("work_days", 0)),
        }

    collections_by_agent = {}
    for entry in payments_col.aggregate([
        {'$match': pay_match},
        {'$group': {'_id': {'agent_id': '$agent_id', 'payment_type': {'$ifNull': ['$payment_type', '']},
                           'is_susu': {'$ifNull': ['$is_susu', False]}}, 'amount': {'$sum': '$amount'}}},
    ]):
        amounts = collections_by_agent.setdefault(str(entry['_id']['agent_id']), {'susu': 0.0, 'products': 0.0})
        amounts[payment_category(entry['_id'])] += float(entry.get('amount') or 0)

    # ----- Total customers per agent (all time) -----
    portfolio_by_agent = customer_breakdowns(customers_col, payments_col, {"agent_id": {"$in": agent_ids_str}})
    customers_by_agent = {key: sum(value.values()) for key, value in portfolio_by_agent.items()}

    # ----- Leads per agent in date range (from customers) -----
    leads_match = {
        "agent_id": {"$in": agent_ids_str},
        "lead_registered_at": {"$gte": start_dt, "$lte": end_dt},
    }
    leads_pipeline = [
        {"$match": leads_match},
        {
            "$group": {
                "_id": "$agent_id",
                "total_leads": {"$sum": 1},
                "converted_leads": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$lead_converted_at", None]},
                                    {"$gte": ["$lead_converted_at", start_dt]},
                                    {"$lte": ["$lead_converted_at", end_dt]},
                                ]
                            },
                            1,
                            0
                        ]
                    }
                }
            }
        }
    ]
    leads_stats = list(customers_col.aggregate(leads_pipeline, allowDiskUse=True))
    leads_by_agent = {
        doc["_id"]: {
            "total_leads": int(doc.get("total_leads", 0)),
            "converted_leads": int(doc.get("converted_leads", 0)),
        }
        for doc in leads_stats
    }

    # ----- Products sold per agent in date range -----
    qty_expr = {
        "$convert": {
            "input": "$$qty",
            "to": "double",
            "onError": 1,
            "onNull": 1,
        }
    }
    price_expr = {
        "$convert": {
            "input": "$$price",
            "to": "double",
            "onError": 0,
            "onNull": 0,
        }
    }
    total_expr = {
        "$convert": {
            "input": "$$total",
            "to": "double",
            "onError": None,
            "onNull": None,
        }
    }

    def _sum_expr(q_field, price_field, total_field):
        return {
            "$let": {
                "vars": {
                    "qty": q_field,
                    "price": price_field,
                    "total": total_field,
                },
                "in": {
                    "$ifNull": [
                        total_expr,
                        {"$multiply": [qty_expr, price_expr]},
                    ]
                },
            }
        }

    def _qty_expr(q_field):
        return {
            "$let": {
                "vars": {"qty": q_field},
                "in": qty_expr,
            }
        }

    sales_pipeline = [
        {"$match": {"agent_id": {"$in": agent_ids_str}}},
        {"$unwind": "$purchases"},
        {"$match": {"purchases.purchase_date": {"$gte": start_str, "$lte": end_str}}},
        {
            "$group": {
                "_id": "$agent_id",
                "quantity_sold": {"$sum": _qty_expr("$purchases.product.quantity")},
                "total_amount": {
                    "$sum": _sum_expr(
                        "$purchases.product.quantity",
                        "$purchases.product.price",
                        "$purchases.product.total",
                    )
                },
            }
        },
    ]
    instant_sales_pipeline = [
        {
            "$match": {
                "agent_id": {"$in": agent_ids_str},
                "purchase_date": {"$gte": start_str, "$lte": end_str},
            }
        },
        {
            "$group": {
                "_id": "$agent_id",
                "quantity_sold": {"$sum": _qty_expr("$product.quantity")},
                "total_amount": {
                    "$sum": _sum_expr(
                        "$product.quantity",
                        "$product.price",
                        "$product.total",
                    )
                },
            }
        },
    ]

    sales_stats = list(customers_col.aggregate(sales_pipeline, allowDiskUse=True))
    instant_stats = list(instant_sales_col.aggregate(instant_sales_pipeline, allowDiskUse=True))

    sales_by_agent = {}
    for doc in sales_stats:
        sales_by_agent[doc["_id"]] = {
            "quantity_sold": float(doc.get("quantity_sold", 0) or 0),
            "total_amount": float(doc.get("total_amount", 0) or 0),
        }
    for doc in instant_stats:
        cur = sales_by_agent.get(doc["_id"], {"quantity_sold": 0.0, "total_amount": 0.0})
        cur["quantity_sold"] += float(doc.get("quantity_sold", 0) or 0)
        cur["total_amount"] += float(doc.get("total_amount", 0) or 0)
        sales_by_agent[doc["_id"]] = cur

    # ----- Build per-agent summaries -----
    total_days_range = (end_dt.date() - start_dt.date()).days + 1
    if total_days_range < 0:
        total_days_range = 0

    agent_rows = []
    for a in agents:
        aid_str = str(a["_id"])

        pay_info = payments_by_agent.get(aid_str, {})
        cust_total = customers_by_agent.get(aid_str, 0)
        lead_info = leads_by_agent.get(aid_str, {})
        sales_info = sales_by_agent.get(aid_str, {})

        total_amount = float(pay_info.get("total_amount", 0.0))
        payments_count = int(pay_info.get("payments_count", 0))
        active_customers = int(pay_info.get("active_customers", 0))
        work_days = int(pay_info.get("work_days", 0))

        total_leads = int(lead_info.get("total_leads", 0))
        converted_leads = int(lead_info.get("converted_leads", 0))
        conv_rate = (converted_leads / total_leads * 100.0) if total_leads > 0 else 0.0
        products_sold_count = float(sales_info.get("quantity_sold", 0) or 0)
        products_sold_amount = float(sales_info.get("total_amount", 0) or 0)

        agent_rows.append({
            "id": aid_str,
            "name": a.get("name", "Unknown"),
            "branch": a.get("branch", ""),
            "phone": a.get("phone", ""),
            "image_url": a.get("image_url", ""),
            "total_sales": round(total_amount, 2),
            "deliveries_done": delivery_counts.get(aid_str, 0),
            "sales_breakdown": {key: round(value, 2) for key, value in collections_by_agent.get(aid_str, {'susu': 0.0, 'products': 0.0}).items()},
            "customer_breakdown": portfolio_by_agent.get(aid_str, {'active': 0, 'completed': 0}),
            "payments_count": payments_count,
            "total_customers": cust_total,
            "active_customers": active_customers,
            "inactive_customers": max(cust_total - active_customers, 0),
            "work_days": work_days,
            "calendar_days": total_days_range,
            "leads_total": total_leads,
            "leads_converted": converted_leads,
            "conversion_rate": round(conv_rate, 1),
            "products_sold_count": round(products_sold_count, 2),
            "products_sold_amount": round(products_sold_amount, 2),
        })

    return jsonify(_json_safe({
        "ok": True,
        "range": {"start": start_str, "end": end_str},
        "agents": agent_rows
    }))
