# routes/packages.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, session, Response, jsonify
from bson import ObjectId
from datetime import datetime
from io import BytesIO
from db import db
from Backend.inventory.packaging_stock import transition as transition_packaging_stock
from Backend.inventory.stock_deductions_store import StockDeductionError

# PDF (pure-Python)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

packages_bp = Blueprint("packages", __name__, template_folder="templates")

users_collection     = db["users"]        # managers, agents, inventory users
packages_collection  = db["packages"]     # submit-for-packaging saved here
customers_collection = db["customers"]
undelivered_items_col = db["undelivered_items"]

PACKAGE_STATUS_FLOW = ["pending", "packaging", "delivering", "delivered"]
PACKAGE_STATUS_LABELS = {
    "pending": "Submitted",
    "packaging": "Packaging",
    "delivering": "Delivering",
    "delivered": "Delivered",
}

# ---------- helpers ----------
def _oid(v):
    try:
        return ObjectId(str(v))
    except Exception:
        return None

def _as_dt(v):
    """Return datetime from possible inputs (datetime, ms/s epoch, or ISO str)."""
    if isinstance(v, datetime):
        return v
    try:
        # ints: ms vs s
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            iv = int(v)
            # treat values > 10^12 as milliseconds
            if iv > 10**12:
                return datetime.fromtimestamp(iv / 1000.0)
            return datetime.fromtimestamp(iv)
        if isinstance(v, str):
            # try plain date
            try:
                return datetime.strptime(v, "%Y-%m-%d")
            except:
                # last resort: fromisoformat (may raise)
                return datetime.fromisoformat(v)
    except:
        pass
    return None

def _current_user():
    """Resolve logged-in user from typical session keys."""
    user_id = session.get("user_id") or session.get("manager_id") or session.get("agent_id") or session.get("inventory_id")
    if not user_id:
        return None
    return users_collection.find_one({"_id": _oid(user_id)}, {"password": 0})


def _status_label(value):
    return PACKAGE_STATUS_LABELS.get(str(value or "").strip().lower(), "Submitted")


def _can_manage_package_status(role):
    return str(role or "").strip().lower() in ("inventory", "manager", "admin")


def _set_customer_purchase_status(customer_id, product_index, package_status, now, actor_id=None):
    if customer_id is None or product_index is None:
        return

    customer_status = "submitted_for_packaging"
    packaging_status = package_status
    if package_status == "packaging":
        customer_status = "packaged"
    elif package_status == "delivering":
        customer_status = "delivering"
    elif package_status == "delivered":
        customer_status = "delivered"

    update_doc = {
        f"purchases.{product_index}.product.status": customer_status,
        f"purchases.{product_index}.product.packaging_status": packaging_status,
        f"purchases.{product_index}.status": customer_status,
        f"purchases.{product_index}.package_status_updated_at": now,
        "updated_at": now,
    }
    if actor_id is not None:
        update_doc[f"purchases.{product_index}.package_status_updated_by"] = str(actor_id)
    if package_status == "delivered":
        update_doc[f"purchases.{product_index}.delivered_at"] = now

    customers_collection.update_one({"_id": customer_id}, {"$set": update_doc})


def _build_package_status_update(package_doc, target_status, now, user, role):
    current_status = str(package_doc.get("status") or "pending").strip().lower() or "pending"
    if current_status not in PACKAGE_STATUS_FLOW:
        current_status = "pending"

    status_history = list(package_doc.get("status_history") or [])
    status_history.append({
        "from": current_status,
        "to": target_status,
        "at": now,
        "by": str(user.get("_id") or ""),
        "role": role,
    })

    update_doc = {
        "status": target_status,
        "updated_at": now,
        "status_updated_by": str(user.get("_id") or ""),
        "status_updated_role": role,
        "status_history": status_history,
    }
    if target_status == "packaging":
        update_doc["packaging_started_at"] = package_doc.get("packaging_started_at") or now
    elif target_status == "delivering":
        update_doc["delivering_started_at"] = package_doc.get("delivering_started_at") or now
    elif target_status == "delivered":
        update_doc["delivered_at"] = package_doc.get("delivered_at") or now
        update_doc["delivered_by"] = user.get("_id")
    return update_doc


def _build_packages_badge_filter():
    flt, role, branch = _manager_scope_filter()
    if flt.get("_id") == {"$in": []}:
        return {"_id": {"$in": []}}, role, branch
    flt["status"] = {"$ne": "delivered"}
    return flt, role, branch

def _manager_scope_filter():
    """
    Returns (flt, role, branch) for packages query:
      - Manager: packages where agent_id in agents managed by user._id
      - Inventory/Admin: all, optionally restricted by ?branch=
      - Agent: only their own agent_id
    NOTE: packages.agent_id is stored as STRING hex, not ObjectId.
    """
    user = _current_user()
    if not user:
        return {"_id": {"$in": []}}, None, None

    role   = (user.get("role") or "").lower()
    my_id  = user.get("_id")
    branch = user.get("branch")

    if role == "manager":
        ags = list(users_collection.find({"role": "agent", "manager_id": my_id}, {"_id": 1}))
        ids = [str(a["_id"]) for a in ags]
        return {"agent_id": {"$in": ids}}, role, branch

    if role in ("inventory", "admin"):
        req_branch = (request.args.get("branch") or "").strip()
        if req_branch:
            managers = list(users_collection.find({"role": "manager", "branch": req_branch}, {"_id": 1}))
            manager_ids = [m["_id"] for m in managers if m.get("_id")]
            ags = list(users_collection.find({"role": "agent", "manager_id": {"$in": manager_ids}}, {"_id": 1})) if manager_ids else []
            ids = [str(a["_id"]) for a in ags]
            if ids:
                return ({"agent_id": {"$in": ids}}, role, req_branch)
            manager_scope_ids = manager_ids + [str(mid) for mid in manager_ids]
            return ({"$or": [{"agent_branch": req_branch}, {"manager_branch": req_branch}, {"manager_id": {"$in": manager_scope_ids}}]}, role, req_branch)
        return ({}, role, None)

    if role == "agent":
        return {"agent_id": str(my_id)}, role, branch

    return {"_id": {"$in": []}}, role, branch

def _attach_agent_meta(rows):
    """Attach agent_name and manager-driven branch to rows (not persisted)."""
    # collect distinct agent_id strings
    ids = sorted({str(r.get("agent_id")) for r in rows if r.get("agent_id")})
    manager_ids = sorted({str(r.get("manager_id")) for r in rows if r.get("manager_id")})
    oid_map = {}
    for s in set(ids + manager_ids):
        try:
            oid_map[s] = ObjectId(s)
        except:
            continue
    if not oid_map:
        return
    users = users_collection.find({"_id": {"$in": list(oid_map.values())}}, {"name": 1, "branch": 1})
    rev = {str(a["_id"]): {"name": a.get("name"), "branch": a.get("branch")} for a in users}
    for r in rows:
        agent_meta = rev.get(str(r.get("agent_id")) or "")
        manager_meta = rev.get(str(r.get("manager_id")) or "")
        if agent_meta and not r.get("agent_name"):
            r["agent_name"] = agent_meta.get("name")
        resolved_branch = (
            r.get("manager_branch")
            or (manager_meta or {}).get("branch")
            or r.get("agent_branch")
            or (agent_meta or {}).get("branch")
        )
        if resolved_branch:
            r["agent_branch"] = resolved_branch
            r["manager_branch"] = resolved_branch

def _render_packages(flt, role, branch, route_name):
    user = _current_user()
    # Search by customer/product (agent name applied after we attach meta)
    q = (request.args.get("q") or "").strip()
    if q:
        flt["$or"] = [
            {"customer_name": {"$regex": q, "$options": "i"}},
            {"product.name":  {"$regex": q, "$options": "i"}},
            {"product_snapshot.name":  {"$regex": q, "$options": "i"}},
        ]

    # Date range on submitted_at
    date_from = (request.args.get("from") or "").strip()
    date_to   = (request.args.get("to")   or "").strip()
    if date_from or date_to:
        dtflt = {}
        if date_from:
            try:
                dtflt["$gte"] = datetime.strptime(date_from, "%Y-%m-%d")
            except:
                pass
        if date_to:
            try:
                dtflt["$lte"] = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            except:
                pass
        if dtflt:
            flt["submitted_at"] = dtflt

    base_flt = dict(flt)

    # Exclude delivered by default
    include_delivered = request.args.get("include_delivered") == "1"
    if not include_delivered:
        flt["status"] = {"$ne": "delivered"}
    status_filter = (request.args.get("status") or "").strip().lower()
    if status_filter in PACKAGE_STATUS_FLOW:
        flt["status"] = status_filter

    projection = {
        "_id": 1,
        "customer_name": 1,
        "customer_phone": 1,
        "customer_id": 1,
        "product_index": 1,
        "product": 1,
        "product_snapshot": 1,
        "submitted_at": 1,
        "created_at": 1,
        "status": 1,
        "agent_id": 1,
        "manager_id": 1,
        "qty": 1,
        "updated_at": 1,
        "delivered_at": 1,
        # schema you provided doesn't store agent_name/branch; we attach at runtime
    }
    rows = list(packages_collection.find(flt, projection).sort("submitted_at", 1))

    # Normalize submitted_at and attach agent meta
    for r in rows:
        r["submitted_at"] = _as_dt(r.get("submitted_at") or r.get("created_at"))
        r["updated_at"] = _as_dt(r.get("updated_at"))
        r["delivered_at"] = _as_dt(r.get("delivered_at"))
        r["status"] = str(r.get("status") or "pending").strip().lower() or "pending"
        r["status_label"] = _status_label(r.get("status"))
    _attach_agent_meta(rows)

    # If user searched agent name, apply in-memory filter now that we have meta
    if q:
        q_low = q.lower()
        rows = [r for r in rows if
                (r.get("agent_name") and q_low in r["agent_name"].lower()) or
                (r.get("customer_name") and q_low in r["customer_name"].lower()) or
                ((r.get("product") or {}).get("name") and q_low in r["product"]["name"].lower()) or
                ((r.get("product_snapshot") or {}).get("name") and q_low in r["product_snapshot"]["name"].lower())
        ]

    for r in rows:
        r["product_display"] = r.get("product_snapshot") or r.get("product") or {}
        current_status = str(r.get("status") or "pending").strip().lower() or "pending"
        if current_status not in PACKAGE_STATUS_FLOW:
            current_status = "pending"
        next_status = None
        if current_status != "delivered":
            next_index = PACKAGE_STATUS_FLOW.index(current_status) + 1
            if next_index < len(PACKAGE_STATUS_FLOW):
                next_status = PACKAGE_STATUS_FLOW[next_index]
        r["next_status"] = next_status
        r["next_status_label"] = _status_label(next_status) if next_status else ""

    # CSV export
    if request.args.get("export") == "1":
        def _gen():
            yield "Agent,Branch,Customer,Product,Amount,Qty,Date Submitted,Status\n"
            for p in rows:
                prod = p.get("product_display") or {}
                amt = prod.get("total", "")
                qty = p.get("qty", "") or ""
                ds  = p.get("submitted_at").strftime("%Y-%m-%d") if isinstance(p.get("submitted_at"), datetime) else ""
                yield f"{p.get('agent_name','')},{p.get('agent_branch','')},{p.get('customer_name','')},{prod.get('name','')},{amt},{qty},{ds},{p.get('status_label','')}\n"
        return Response(_gen(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=packages.csv"})

    # Stats: total submitted + confirmed + top agents (within date/search scope)
    try:
        total_submitted = packages_collection.count_documents(base_flt)
        total_confirmed = packages_collection.count_documents({**base_flt, "status": "delivered"})
        total_packaging = packages_collection.count_documents({**base_flt, "status": "packaging"})
        total_delivering = packages_collection.count_documents({**base_flt, "status": "delivering"})
        total_open = packages_collection.count_documents({**base_flt, "status": {"$ne": "delivered"}})
    except Exception:
        total_submitted = 0
        total_confirmed = 0
        total_packaging = 0
        total_delivering = 0
        total_open = 0

    top_agents = []
    try:
        pipe = [
            {"$match": base_flt},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]
        agg = list(packages_collection.aggregate(pipe))
        agent_ids = [a["_id"] for a in agg if a.get("_id")]
        agents = list(users_collection.find({"_id": {"$in": [_oid(i) for i in agent_ids if _oid(i)]}}, {"name": 1, "branch": 1, "manager_id": 1}))
        manager_oids = []
        for agent in agents:
            manager_id = agent.get("manager_id")
            if isinstance(manager_id, ObjectId):
                manager_oids.append(manager_id)
            elif manager_id:
                mid = _oid(manager_id)
                if mid:
                    manager_oids.append(mid)
        manager_map = {
            str(manager["_id"]): manager
            for manager in users_collection.find({"_id": {"$in": manager_oids}}, {"branch": 1})
        } if manager_oids else {}
        agent_map = {str(a["_id"]): a for a in agents}
        for a in agg:
            aid = str(a.get("_id"))
            meta = agent_map.get(aid, {})
            manager_meta = manager_map.get(str(meta.get("manager_id")) or "", {})
            top_agents.append({
                "agent_id": aid,
                "name": meta.get("name", "Unknown"),
                "branch": manager_meta.get("branch") or meta.get("branch", ""),
                "count": a.get("count", 0)
            })
    except Exception:
        top_agents = []

    # Undelivered items modal list (pending, scoped)
    undelivered_rows = []
    try:
        undelivered_flt = {"status": "pending"}
        if role == "manager" and user:
            mid = user.get("_id")
            undelivered_flt["$or"] = [{"manager_id": mid}, {"manager_id": str(mid)}]
        elif role == "agent" and user:
            undelivered_flt["agent_id"] = str(user.get("_id"))
        elif role in ("inventory", "admin"):
            if branch:
                undelivered_flt["agent_branch"] = branch
        undelivered_rows = list(
            undelivered_items_col.find(undelivered_flt).sort("created_at", -1).limit(50)
        )
        for u in undelivered_rows:
            ts = u.get("created_at")
            if isinstance(ts, datetime):
                u["created_at"] = ts.strftime("%Y-%m-%d")
    except Exception:
        undelivered_rows = []

    # Branch list (inventory/admin convenience)
    branches = users_collection.distinct("branch", {"role": "manager"})
    branches = [b for b in branches if b]
    return render_template("packages.html",
                           packages=rows,
                           role=role,
                           current_branch=branch,
                           branches=branches,
                           packages_route=route_name,
                           package_stats={
                               "total_submitted": total_submitted,
                               "total_confirmed": total_confirmed,
                               "total_packaging": total_packaging,
                               "total_delivering": total_delivering,
                               "total_open": total_open,
                           },
                           top_agents=top_agents,
                           undelivered_rows=undelivered_rows,
                           can_manage_status=_can_manage_package_status(role),
                           active_status_filter=status_filter)

# ---------- pages ----------
@packages_bp.route("/packages", methods=["GET"])
def list_packages():
    """
    Branch-based list:
      - Manager => agents under them
      - Inventory/Admin => optional branch filter
      - Agent => self
    Supports: search, date range, include_delivered, CSV export
    """
    flt, role, branch = _manager_scope_filter()
    if flt.get("_id") == {"$in": []}:
        flash("Please log in to view packages.", "warning")
        return redirect(url_for("login.login"))

    return _render_packages(flt, role, branch, "packages.list_packages")


@packages_bp.route("/packages/manager", methods=["GET"])
def list_packages_manager():
    user = _current_user()
    if not user:
        flash("Please log in to view packages.", "warning")
        return redirect(url_for("login.login"))
    if (user.get("role") or "").lower() != "manager":
        flash("Unauthorized access.", "warning")
        return redirect(url_for("packages.list_packages"))

    flt, role, branch = _manager_scope_filter()
    if flt.get("_id") == {"$in": []}:
        flash("Please log in to view packages.", "warning")
        return redirect(url_for("login.login"))
    return _render_packages(flt, role, branch, "packages.list_packages_manager")


@packages_bp.route("/packages/admin", methods=["GET"])
def list_packages_admin():
    user = _current_user()
    if not user:
        flash("Please log in to view packages.", "warning")
        return redirect(url_for("login.login"))
    if (user.get("role") or "").lower() != "admin":
        flash("Unauthorized access.", "warning")
        return redirect(url_for("packages.list_packages"))

    flt = {}
    role = "admin"
    branch = None
    return _render_packages(flt, role, branch, "packages.list_packages_admin")

@packages_bp.route("/packages/generate_pdf", methods=["POST"])
def generate_packages_pdf():
    """
    Requires: selected package IDs + confirm_paid.
    Generates PDF; afterwards sets status='delivered' on those docs.
    """
    user = _current_user()
    if not user:
        flash("Please log in.", "warning")
        return redirect(url_for("login.login"))

    selected_ids = request.form.getlist("package_id")
    confirm_paid = request.form.get("confirm_paid") == "on"

    if not selected_ids:
        flash("No packages selected.", "warning")
        return redirect(url_for("packages.list_packages"))
    if not confirm_paid:
        flash("Please confirm that the items are fully paid before generating.", "warning")
        return redirect(url_for("packages.list_packages"))

    # Scope-limited selection; exclude already delivered
    flt, role, branch = _manager_scope_filter()
    object_ids = [_oid(i) for i in selected_ids if _oid(i)]
    flt.update({"_id": {"$in": object_ids}, "status": {"$ne": "delivered"}})

    rows = list(packages_collection.find(flt))
    if not rows:
        flash("No eligible packages found (maybe already delivered or out of scope).", "warning")
        return redirect(url_for("packages.list_packages"))

    # Normalize + meta
    for r in rows:
        r["submitted_at"] = _as_dt(r.get("submitted_at"))
    _attach_agent_meta(rows)

    # ---- Build PDF ----
    buf   = BytesIO()
    doc   = SimpleDocTemplate(buf, pagesize=landscape(A4),
                              leftMargin=1.0*cm, rightMargin=1.0*cm,
                              topMargin=1.0*cm, bottomMargin=1.0*cm)
    styles = getSampleStyleSheet()
    story  = []

    title_branch = branch or request.args.get("branch") or "All Branches"
    title = Paragraph(
        f"<b>SMART LIVING — Packaging Dispatch List</b>"
        f"<br/><font size=10>Branch: {title_branch}"
        f"&nbsp;&nbsp; Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}</font>",
        styles["Title"]
    )
    story.append(title)
    story.append(Spacer(1, 0.3*cm))

    # Header
    data = [["#", "Agent", "Branch", "Customer", "Product", "Amount (GH₵)", "Date Submitted", "Status"]]

    # Sort by date
    rows.sort(key=lambda r: r.get("submitted_at") or datetime.min)

    total_amount = 0
    for i, r in enumerate(rows, start=1):
        prod = r.get("product_snapshot") or r.get("product") or {}
        amt  = prod.get("total", 0) or 0
        total_amount += float(amt)
        sub  = r.get("submitted_at")
        data.append([
            i,
            r.get("agent_name", "") or "",
            r.get("agent_branch", "") or "",
            r.get("customer_name", "") or "",
            prod.get("name", "") or "",
            amt,
            sub.strftime("%Y-%m-%d") if isinstance(sub, datetime) else "",
            r.get("status", "") or "",
        ])

    # Totals row
    data.append(["", "", "", "", "TOTAL", round(total_amount, 2), "", ""])

    table = Table(
        data,
        colWidths=[1.0*cm, 4.0*cm, 3.0*cm, 4.5*cm, 6.0*cm, 3.0*cm, 3.2*cm, 3.0*cm]
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0d6efd")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("ALIGN",      (0,0), (-1,-1), "LEFT"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.grey),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME",   (0,1), (-1,-2), "Helvetica"),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.whitesmoke, colors.lightgrey]),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("FONTNAME",   (0,-1), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#e9ecef")),
    ]))
    story.append(table)
    doc.build(story)

    pdf_bytes = buf.getvalue()
    buf.close()

    # ---- After successful build, mark delivered ----
    now = datetime.utcnow()
    packages_collection.update_many(
        {"_id": {"$in": [r["_id"] for r in rows]}},
        {"$set": {"status": "delivered", "delivered_at": now, "delivered_by": user.get("_id")}}
    )

    for r in rows:
        from services.delivery_followups import ensure_delivery_followup
        ensure_delivery_followup(db, package=r, trigger="delivered", event_at=now)
        _set_customer_purchase_status(
            r.get("customer_id"),
            r.get("product_index"),
            "delivered",
            now,
            user.get("_id"),
        )

    filename = f"packages_{(branch or 'all').lower()}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return send_file(BytesIO(pdf_bytes), as_attachment=True, download_name=filename, mimetype="application/pdf")


@packages_bp.route("/packages/<package_id>/status", methods=["POST"])
def update_package_status(package_id):
    user = _current_user()
    if not user:
        flash("Please log in.", "warning")
        return redirect(url_for("login.login"))

    role = (user.get("role") or "").lower()
    if not _can_manage_package_status(role):
        flash("Unauthorized access.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    package_oid = _oid(package_id)
    if not package_oid:
        flash("Invalid submitted card.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    next_status = str(request.form.get("next_status") or "").strip().lower()
    if next_status not in PACKAGE_STATUS_FLOW:
        flash("Invalid status update.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    flt, _, _ = _manager_scope_filter()
    flt["_id"] = package_oid
    package_doc = packages_collection.find_one(flt)
    if not package_doc:
        flash("Submitted card not found or out of scope.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    current_status = str(package_doc.get("status") or "pending").strip().lower() or "pending"
    if current_status not in PACKAGE_STATUS_FLOW:
        current_status = "pending"

    if current_status == "delivered":
        flash("This submitted card is already delivered.", "info")
        return redirect(request.referrer or url_for("packages.list_packages"))

    current_index = PACKAGE_STATUS_FLOW.index(current_status)
    target_index = PACKAGE_STATUS_FLOW.index(next_status)
    if target_index != current_index + 1:
        flash("Status updates must follow the packaging flow.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    try:
        transition_packaging_stock(str(package_oid), next_status, {"user_id": str(user["_id"]), "name": user.get("name", ""), "role": role})
    except StockDeductionError as exc:
        flash(str(exc), "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    flash(f"Submitted card moved to {_status_label(next_status)}.", "success")
    return redirect(request.referrer or url_for("packages.list_packages"))


@packages_bp.route("/packages/bulk-status", methods=["POST"])
def bulk_update_package_status():
    user = _current_user()
    if not user:
        flash("Please log in.", "warning")
        return redirect(url_for("login.login"))

    role = (user.get("role") or "").lower()
    if not _can_manage_package_status(role):
        flash("Unauthorized access.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    target_status = str(request.form.get("bulk_status") or "").strip().lower()
    if target_status not in PACKAGE_STATUS_FLOW:
        flash("Choose a valid bulk status.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    object_ids = [_oid(i) for i in request.form.getlist("package_id") if _oid(i)]
    if not object_ids:
        flash("Select at least one submitted card.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    flt, _, _ = _manager_scope_filter()
    flt["_id"] = {"$in": object_ids}
    rows = list(packages_collection.find(flt))
    if not rows:
        flash("No eligible submitted cards found for your scope.", "warning")
        return redirect(request.referrer or url_for("packages.list_packages"))

    now = datetime.utcnow()
    updated = 0
    skipped = 0
    for package_doc in rows:
        current_status = str(package_doc.get("status") or "pending").strip().lower() or "pending"
        if current_status not in PACKAGE_STATUS_FLOW:
            current_status = "pending"
        if current_status == target_status:
            skipped += 1
            continue
        if current_status == "delivered" and target_status != "delivered":
            skipped += 1
            continue

        try:
            transition_packaging_stock(str(package_doc["_id"]), target_status, {"user_id": str(user["_id"]), "name": user.get("name", ""), "role": role})
            updated += 1
        except StockDeductionError as exc:
            skipped += 1
            flash(str(exc), "warning")

    label = _status_label(target_status)
    if updated:
        flash(f"Bulk updated {updated} submitted card(s) to {label}.", "success")
    else:
        flash(f"No submitted cards needed updating to {label}.", "info")
    if skipped:
        flash(f"Skipped {skipped} card(s) already at that status or not eligible.", "info")
    return redirect(request.referrer or url_for("packages.list_packages"))


@packages_bp.route("/packages/counts", methods=["GET"])
def packages_counts():
    flt, _, _ = _build_packages_badge_filter()
    if flt.get("_id") == {"$in": []}:
        return jsonify(ok=False, message="Unauthorized"), 401

    try:
        rows = list(packages_collection.aggregate([
            {"$match": flt},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]))
        counts = {str(row.get("_id") or "").lower(): int(row.get("count") or 0) for row in rows}
        pending = counts.get("pending", 0)
        packaging = counts.get("packaging", 0)
        delivering = counts.get("delivering", 0)
        open_count = sum(counts.values())
        return jsonify(ok=True, open=open_count, pending=pending, packaging=packaging, delivering=delivering)
    except Exception as exc:
        return jsonify(ok=False, message=str(exc)), 500

# ---------- (optional) index hints ----------
# In Mongo shell / migration:
# db.packages.createIndex({ status: 1, agent_id: 1, submitted_at: 1 })
# db.users.createIndex({ role: 1, manager_id: 1, branch: 1 })
