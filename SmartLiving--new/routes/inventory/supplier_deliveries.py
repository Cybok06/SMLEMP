from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, Response, flash

from db import db
from login import get_current_identity, role_required

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import io


supplier_deliveries_bp = Blueprint(
    "supplier_deliveries",
    __name__,
    template_folder="../../templates/inventory",
    url_prefix="/inventory"
)

supplier_deliveries_col = db["supplier_deliveries"]
inventory_col = db["inventory"]
users_col = db["users"]

LOGO_URL = "https://res.cloudinary.com/dljpgzbto/image/upload/v1743993646/company-logo_dksb23.jpg"
MANAGER_OID = ObjectId("68433eda05a08a53aa506250")
MANAGER_STR = "68433eda05a08a53aa506250"


def _oid(val: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(val))
    except Exception:
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except Exception:
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _get_accra_tz():
    if ZoneInfo:
        try:
            return ZoneInfo("Africa/Accra")
        except Exception:
            pass
    try:
        import pytz
        return pytz.timezone("Africa/Accra")
    except Exception:
        return timezone.utc


def _accra_today_str() -> str:
    tz = _get_accra_tz()
    return datetime.now(tz).strftime("%Y-%m-%d")


def _parse_date_range(args) -> Tuple[Optional[str], Optional[str]]:
    start = (args.get("start") or "").strip()
    end = (args.get("end") or "").strip()
    return start or None, end or None


def _match_date_range(field: str, start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
    if not start and not end:
        return {}
    match: Dict[str, Any] = {}
    if start:
        match.setdefault(field, {})["$gte"] = start
    if end:
        match.setdefault(field, {})["$lte"] = end
    return match


def _next_ref_no() -> str:
    year = datetime.utcnow().year
    prefix = f"PO-{year}-"
    last = supplier_deliveries_col.find_one(
        {"ref_no": {"$regex": f"^{prefix}"}},
        sort=[("ref_no", -1)]
    )
    if last and last.get("ref_no"):
        try:
            seq = int(str(last["ref_no"]).split("-")[-1])
        except Exception:
            seq = 0
    else:
        seq = 0
    return f"{prefix}{seq + 1:05d}"


def _ensure_indexes():
    try:
        supplier_deliveries_col.create_index([("ref_no", 1)], unique=True)
        supplier_deliveries_col.create_index([("status", 1), ("created_at", -1)])
        supplier_deliveries_col.create_index([("supplier.name", 1), ("created_at", -1)])
        supplier_deliveries_col.create_index([("items.product_id", 1)])
        supplier_deliveries_col.create_index([("expected_date", 1)])
    except Exception:
        pass


_ensure_indexes()


def _status_badge(status: str) -> str:
    status = (status or "pending").lower()
    if status == "completed":
        return "success"
    if status == "partial":
        return "warning"
    if status == "closed":
        return "secondary"
    return "danger"


def _compute_totals(items: List[Dict[str, Any]]) -> Dict[str, int]:
    req = sum(_safe_int(i.get("qty_requested")) for i in items)
    delivered = sum(_safe_int(i.get("qty_delivered_total")) for i in items)
    rejected = sum(_safe_int(i.get("qty_rejected_total")) for i in items)
    missing = sum(_safe_int(i.get("qty_missing")) for i in items)
    return {
        "requested": req,
        "delivered": delivered,
        "rejected": rejected,
        "missing": missing,
    }


def _update_inventory_qty(product_id: str, qty: int) -> bool:
    if qty <= 0:
        return False
    oid = _oid(product_id)
    if not oid:
        return False
    now = datetime.utcnow()
    res = inventory_col.update_one(
        {"_id": oid, "manager_id": {"$in": [MANAGER_OID, MANAGER_STR]}},
        {"$inc": {"qty": qty}, "$set": {"updated_at": now, "is_out_of_stock": False}},
    )
    return bool(res.matched_count)


@supplier_deliveries_bp.route("/supplier-deliveries", methods=["GET"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_list():
    page = max(_safe_int(request.args.get("page"), 1), 1)
    per_page = min(max(_safe_int(request.args.get("per_page"), 20), 5), 100)
    skip = (page - 1) * per_page

    supplier = (request.args.get("supplier") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    branch = (request.args.get("branch") or "").strip()
    q = (request.args.get("q") or "").strip()
    start, end = _parse_date_range(request.args)

    match: Dict[str, Any] = {}
    if supplier:
        match["supplier.name"] = {"$regex": supplier, "$options": "i"}
    if status:
        match["status"] = status
    if q:
        match["ref_no"] = {"$regex": q, "$options": "i"}
    if branch:
        match["branch"] = branch
    match.update(_match_date_range("created_date", start, end))

    total = supplier_deliveries_col.count_documents(match)
    rows = list(
        supplier_deliveries_col.find(match)
        .sort([("updated_at", -1)])
        .skip(skip)
        .limit(per_page)
    )

    summary_match = dict(match)
    summary_match["status"] = {"$in": ["pending", "partial"]}
    summary_rows = list(
        supplier_deliveries_col.aggregate([
            {"$match": summary_match},
            {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": True}},
            {"$group": {
                "_id": "$status",
                "count": {"$addToSet": "$_id"},
                "missing_units": {"$sum": {"$toInt": {"$ifNull": ["$items.qty_missing", 0]}}},
            }},
        ])
    )
    pending_count = 0
    partial_count = 0
    missing_units_total = 0
    for row in summary_rows:
        if row.get("_id") == "pending":
            pending_count = len(row.get("count") or [])
        if row.get("_id") == "partial":
            partial_count = len(row.get("count") or [])
        missing_units_total += int(row.get("missing_units") or 0)

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "inventory/supplier_deliveries_list.html",
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        pending_count=pending_count,
        partial_count=partial_count,
        missing_units_total=missing_units_total,
        filters={
            "supplier": supplier,
            "status": status,
            "branch": branch,
            "q": q,
            "start": start or "",
            "end": end or "",
        },
        status_badge=_status_badge,
    )


@supplier_deliveries_bp.route("/supplier-deliveries/new", methods=["GET"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_new():
    docs = list(
        inventory_col.find(
            {"manager_id": {"$in": [MANAGER_OID, MANAGER_STR]}},
            {"name": 1, "qty": 1, "image_url": 1},
        ).sort("name", 1)
    )
    inventory_items = [{
        "_id": str(d.get("_id")),
        "name": d.get("name") or "",
        "qty": int(d.get("qty") or 0),
        "image_url": d.get("image_url"),
    } for d in docs]
    print("[supplier_deliveries_new] manager:", MANAGER_STR, "items:", len(inventory_items))
    if inventory_items:
        print("[supplier_deliveries_new] sample:", inventory_items[0])
    return render_template(
        "inventory/supplier_deliveries_new.html",
        inventory_items=inventory_items,
        today=_accra_today_str(),
        manager_id=MANAGER_STR,
    )


@supplier_deliveries_bp.route("/supplier-deliveries", methods=["POST"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_create():
    ident = get_current_identity()
    user_doc = None
    user_id = ident.get("user_id")
    if user_id:
        oid = _oid(user_id)
        user_doc = users_col.find_one({"_id": oid}) if oid else users_col.find_one({"_id": user_id})
    branch_name = (user_doc or {}).get("branch") or ""

    supplier_name = (request.form.get("supplier_name") or "").strip()
    supplier_phone = (request.form.get("supplier_phone") or "").strip()
    supplier_location = (request.form.get("supplier_location") or "").strip()
    expected_date = (request.form.get("expected_date") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    product_ids = request.form.getlist("product_id[]")
    product_names = request.form.getlist("product_name[]")
    qtys = request.form.getlist("qty_requested[]")
    unit_costs = request.form.getlist("unit_cost[]")
    item_notes = request.form.getlist("item_note[]")

    items: List[Dict[str, Any]] = []
    for idx, pid in enumerate(product_ids):
        pid = (pid or "").strip()
        qty = _safe_int(qtys[idx] if idx < len(qtys) else 0)
        if not pid or qty <= 0:
            continue
        oid = _oid(pid)
        if not oid:
            continue
        inv = inventory_col.find_one(
            {"_id": oid, "manager_id": {"$in": [MANAGER_OID, MANAGER_STR]}},
            {"name": 1, "image_url": 1}
        )
        if not inv:
            continue
        pname = (product_names[idx] if idx < len(product_names) else "") or inv.get("name") or "Unknown"
        items.append({
            "product_id": str(inv.get("_id")),
            "inventory_id": str(inv.get("_id")),
            "product_name_snapshot": inv.get("name") or pname,
            "product_image_snapshot": inv.get("image_url"),
            "unit_cost": _safe_float(unit_costs[idx]) if idx < len(unit_costs) and unit_costs[idx] else None,
            "qty_requested": qty,
            "qty_delivered_total": 0,
            "qty_rejected_total": 0,
            "qty_missing": qty,
            "status": "Not Delivered",
            "item_note": (item_notes[idx] if idx < len(item_notes) else ""),
        })

    if not supplier_name or not items:
        return redirect(url_for("supplier_deliveries.supplier_deliveries_new"))

    ref_no = _next_ref_no()
    now = datetime.utcnow()
    created_date = _accra_today_str()
    doc = {
        "ref_no": ref_no,
        "supplier": {
            "name": supplier_name,
            "phone": supplier_phone,
            "location": supplier_location,
        },
        "status": "pending",
        "created_at": now,
        "created_date": created_date,
        "created_by": {
            "id": ident.get("user_id"),
            "name": ident.get("name"),
            "role": ident.get("role"),
        },
        "expected_date": expected_date,
        "notes": notes,
        "branch": branch_name,
        "items": items,
        "receipts": [],
        "updated_at": now,
    }
    try:
        supplier_deliveries_col.insert_one(doc)
    except Exception:
        return redirect(url_for("supplier_deliveries.supplier_deliveries_new"))

    print("[supplier_deliveries_create] ref_no:", ref_no, "items:", len(items))
    return redirect(url_for("supplier_deliveries.supplier_deliveries_view", id=str(doc.get("_id"))))


@supplier_deliveries_bp.route("/supplier-deliveries/<id>", methods=["GET"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_view(id: str):
    oid = _oid(id)
    doc = supplier_deliveries_col.find_one({"_id": oid}) if oid else supplier_deliveries_col.find_one({"_id": id})
    if not doc:
        return redirect(url_for("supplier_deliveries.supplier_deliveries_list"))

    items = doc.get("items") or []
    totals = _compute_totals(items)
    progress = 0
    if totals["requested"] > 0:
        progress = round((totals["delivered"] / totals["requested"]) * 100, 1)

    return render_template(
        "inventory/supplier_deliveries_view.html",
        row=doc,
        items=items,
        receipts=doc.get("receipts") or [],
        totals=totals,
        progress=progress,
        status_badge=_status_badge,
        today=_accra_today_str(),
    )


@supplier_deliveries_bp.route("/supplier-deliveries/<id>/receive", methods=["POST"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_receive(id: str):
    ident = get_current_identity()
    oid = _oid(id)
    doc = supplier_deliveries_col.find_one({"_id": oid}) if oid else supplier_deliveries_col.find_one({"_id": id})
    if not doc:
        return jsonify(ok=False, message="Not found"), 404

    received_at = request.form.get("received_at") or ""
    if not received_at:
        received_at = _accra_today_str()
    delivery_note_no = (request.form.get("delivery_note_no") or "").strip()
    comment = (request.form.get("comment") or "").strip()

    update_inventory = (request.form.get("update_inventory") or "yes").strip().lower() == "yes"

    receipt_items: List[Dict[str, Any]] = []
    items = doc.get("items") or []
    item_map = {str(i.get("product_id")): i for i in items}

    product_ids = request.form.getlist("receipt_product_id[]")
    qty_delivered_list = request.form.getlist("qty_delivered[]")
    qty_rejected_list = request.form.getlist("qty_rejected[]")

    for idx, pid in enumerate(product_ids):
        pid = str(pid)
        item = item_map.get(pid)
        if not item:
            continue
        qty_delivered = _safe_int(qty_delivered_list[idx] if idx < len(qty_delivered_list) else 0)
        qty_rejected = _safe_int(qty_rejected_list[idx] if idx < len(qty_rejected_list) else 0)
        if qty_delivered < 0 or qty_rejected < 0:
            continue

        if qty_delivered > 0 or qty_rejected > 0:
            receipt_items.append({
                "product_id": pid,
                "qty_delivered": qty_delivered,
                "qty_rejected": qty_rejected,
            })

        item["qty_delivered_total"] = _safe_int(item.get("qty_delivered_total")) + qty_delivered
        item["qty_rejected_total"] = _safe_int(item.get("qty_rejected_total")) + qty_rejected
        requested = _safe_int(item.get("qty_requested"))
        delivered_total = _safe_int(item.get("qty_delivered_total"))
        rejected_total = _safe_int(item.get("qty_rejected_total"))
        missing = max(requested - delivered_total - rejected_total, 0)
        item["qty_missing"] = missing

        if delivered_total > requested:
            item["status"] = "Over Delivered"
        elif delivered_total == requested:
            item["status"] = "Delivered"
        elif delivered_total == 0:
            item["status"] = "Not Delivered"
        else:
            item["status"] = "Part Delivered"

        if update_inventory:
            _update_inventory_qty(pid, qty_delivered)

    if not receipt_items:
        flash("Enter delivered or rejected quantities.", "warning")
        return redirect(url_for("supplier_deliveries.supplier_deliveries_view", id=str(doc["_id"])))

    print("[supplier_deliveries_receive] receipt_items:", len(receipt_items), "update_inventory:", update_inventory)
    status = "pending"
    if items and all(_safe_int(i.get("qty_delivered_total")) >= _safe_int(i.get("qty_requested")) for i in items):
        status = "completed"
    elif any(_safe_int(i.get("qty_delivered_total")) > 0 for i in items):
        status = "partial"

    receipt_doc = {
        "received_at": received_at,
        "received_by": {"id": ident.get("user_id"), "name": ident.get("name"), "role": ident.get("role")},
        "delivery_note_no": delivery_note_no,
        "comment": comment,
        "items": receipt_items,
    }

    supplier_deliveries_col.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "items": items,
                "status": status,
                "updated_at": datetime.utcnow(),
            },
            "$push": {"receipts": receipt_doc},
        },
    )
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if wants_json:
        return jsonify(ok=True, status=status)
    return redirect(url_for("supplier_deliveries.supplier_deliveries_view", id=str(doc["_id"])))


@supplier_deliveries_bp.route("/supplier-deliveries/<id>/pdf", methods=["GET"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_pdf(id: str):
    oid = _oid(id)
    doc = supplier_deliveries_col.find_one({"_id": oid}) if oid else supplier_deliveries_col.find_one({"_id": id})
    if not doc:
        return Response("Not found", status=404)

    items = doc.get("items") or []
    totals = _compute_totals(items)

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=36)
    styles = getSampleStyleSheet()

    try:
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_LEFT
        from reportlab.pdfgen import canvas
    except Exception:
        ParagraphStyle = None
        TA_RIGHT = TA_LEFT = None

    title_style = ParagraphStyle(
        "titleStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=colors.HexColor("#0f172a"),
        leading=18,
    )
    subtitle_style = ParagraphStyle(
        "subtitleStyle",
        parent=styles["Normal"],
        fontSize=10.5,
        textColor=colors.HexColor("#475569"),
        leading=14,
    )
    meta_style = ParagraphStyle(
        "metaStyle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#334155"),
        leading=12,
        alignment=TA_RIGHT,
    )
    label_style = ParagraphStyle(
        "labelStyle",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#64748b"),
        leading=12,
    )
    value_style = ParagraphStyle(
        "valueStyle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#0f172a"),
        leading=14,
    )

    status = (doc.get("status") or "pending").lower()
    status_color = {
        "pending": colors.HexColor("#dc2626"),
        "partial": colors.HexColor("#f59e0b"),
        "completed": colors.HexColor("#16a34a"),
        "closed": colors.HexColor("#64748b"),
    }.get(status, colors.HexColor("#0f172a"))

    logo_img = None
    try:
        import requests
        resp = requests.get(LOGO_URL, timeout=10)
        if resp.status_code == 200:
            logo_img = Image(io.BytesIO(resp.content), width=100, height=40)
    except Exception:
        logo_img = None

    tz = _get_accra_tz()
    printed_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    supplier = doc.get("supplier") or {}
    branch = doc.get("branch") or "-"

    left_block = []
    if logo_img:
        left_block.append(logo_img)
        left_block.append(Spacer(1, 6))
    left_block.append(Paragraph("Smart Living Emporium Plus", title_style))
    left_block.append(Paragraph("Supplier Deliveries Reconciliation", subtitle_style))
    left_block.append(Paragraph(f"Branch: {branch}", label_style))

    status_pill = Table(
        [[Paragraph(f"<font color='white'>{status.capitalize()}</font>", styles["Normal"])]],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), status_color),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ],
    )

    right_block = [
        Paragraph(f"Ref No: <b>{doc.get('ref_no','')}</b>", meta_style),
        Paragraph(f"Expected Date: {doc.get('expected_date','-')}", meta_style),
        Paragraph("Status:", meta_style),
        status_pill,
        Paragraph(f"Printed: {printed_at}", meta_style),
    ]

    header_table = Table(
        [[left_block, right_block]],
        colWidths=[330, 170],
        style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ],
    )

    elements: List[Any] = []
    elements.append(header_table)
    elements.append(Spacer(1, 12))

    supplier_info = [
        [Paragraph("Supplier Name", label_style), Paragraph(supplier.get("name") or "-", value_style)],
        [Paragraph("Phone", label_style), Paragraph(supplier.get("phone") or "-", value_style)],
        [Paragraph("Location", label_style), Paragraph(supplier.get("location") or "-", value_style)],
    ]
    if doc.get("notes"):
        supplier_info.append([Paragraph("Notes", label_style), Paragraph(doc.get("notes"), value_style)])

    supplier_box = Table(
        supplier_info,
        colWidths=[100, 400],
        style=[
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#e2e8f0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ],
    )
    elements.append(supplier_box)
    elements.append(Spacer(1, 12))

    header = ["Product", "Requested", "Delivered", "Rejected", "Missing", "Status"]
    rows = [header]
    for it in items:
        rows.append([
            it.get("product_name_snapshot") or "Unknown",
            _safe_int(it.get("qty_requested")),
            _safe_int(it.get("qty_delivered_total")),
            _safe_int(it.get("qty_rejected_total")),
            _safe_int(it.get("qty_missing")),
            it.get("status") or "",
        ])

    table = Table(rows, repeatRows=1, colWidths=[200, 60, 60, 60, 60, 80])
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("ALIGN", (1, 1), (-2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            table_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc")))
        missing_val = rows[i][4]
        if _safe_int(missing_val) > 0:
            table_style.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#dc2626")))
    table.setStyle(TableStyle(table_style))
    elements.append(table)
    elements.append(Spacer(1, 10))

    totals_table = Table(
        [[
            Paragraph(f"<b>Requested</b><br/>{totals['requested']}", value_style),
            Paragraph(f"<b>Delivered</b><br/>{totals['delivered']}", value_style),
            Paragraph(f"<b>Rejected</b><br/>{totals['rejected']}", value_style),
            Paragraph(f"<b>Missing</b><br/>{totals['missing']}", value_style),
        ]],
        colWidths=[125, 125, 125, 125],
        style=[
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ],
    )
    elements.append(totals_table)
    elements.append(Spacer(1, 14))

    signatures = Table(
        [[
            "Supplier Signature: ____________________   Date: ________",
            "Receiver Signature: ____________________   Date: ________",
            "Manager Signature: _____________________   Date: ________",
        ]],
        colWidths=[520],
        style=[
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ],
    )
    elements.append(signatures)

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_footer(num_pages)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def draw_footer(self, page_count):
            self.setStrokeColor(colors.HexColor("#e2e8f0"))
            self.line(28, 26, 567, 26)
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748b"))
            self.drawString(28, 14, "Generated by Smart Living Emporium Plus")
            page_num = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(567, 14, page_num)

    pdf.build(elements, canvasmaker=NumberedCanvas)
    buf.seek(0)
    filename = f"{doc.get('ref_no','supplier_delivery')}.pdf"
    return Response(
        buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@supplier_deliveries_bp.route("/api/supplier-deliveries/badge", methods=["GET"])
@role_required("inventory", "admin", "manager", "executive")
def supplier_deliveries_badge():
    pending = supplier_deliveries_col.count_documents({"status": "pending"})
    partial = supplier_deliveries_col.count_documents({"status": "partial"})
    total_open = int(pending) + int(partial)
    return jsonify(ok=True, pending_count=pending, partial_count=partial, total_open=total_open)
