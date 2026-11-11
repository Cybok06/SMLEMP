from __future__ import annotations

from datetime import datetime
from io import BytesIO

import bcrypt as bcrypt_lib
from bson import ObjectId
from flask import Blueprint, jsonify, request, send_file
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from login import get_current_identity, role_required
from .settings_store import (
    get_branches_payload,
    get_effective_inventory_role,
    get_inventory_role_map,
    get_inventory_roles,
    get_inventory_user_doc,
    inventory_locations_col,
    inventory_roles_col,
    is_truthy,
    normalize_permissions,
    serialize_role_doc,
    serialize_inventory_user,
    users_col,
)

inventory_settings_api_bp = Blueprint(
    "inventory_settings_api",
    __name__,
    url_prefix="/api/inventory/settings",
)


def _forbidden_json():
    return jsonify({"ok": False, "error": "Forbidden"}), 403


def _main_admin_guard():
    ident = get_current_identity()
    if not ident.get("is_authenticated"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if ident.get("role") in {"executive", "inventory"}:
        ident["is_main_admin"] = True
    if ident.get("role") not in {"inventory", "executive"}:
        return _forbidden_json()
    if not ident.get("is_main_admin"):
        return _forbidden_json()
    return None


def inventory_main_admin_required(fn):
    def wrapper(*args, **kwargs):
        blocked = _main_admin_guard()
        if blocked is not None:
            return blocked
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


def _json_payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _inventory_users_payload():
    role_map = get_inventory_role_map()
    rows = list(users_col.find({"role": "inventory"}).sort([("name", 1), ("username", 1)]))
    return [serialize_inventory_user(row, role_map=role_map) for row in rows]


def _assignable_role(role_id: str, role_map: dict[str, dict]):
    cleaned = (role_id or "").strip()
    if cleaned not in {"warehouse-manager", "inventory-user"}:
        cleaned = "inventory-user"
    return role_map.get(cleaned) or role_map.get("inventory-user")


@inventory_settings_api_bp.route("/bootstrap")
@inventory_main_admin_required
def bootstrap():
    return jsonify(
        {
            "ok": True,
            "roles": get_inventory_roles(),
            "users": _inventory_users_payload(),
        }
    )


@inventory_settings_api_bp.route("/branches-warehouses")
@inventory_main_admin_required
def branches_warehouses():
    payload = get_branches_payload()
    return jsonify({"ok": True, **payload})


def _build_branches_warehouses_pdf(branches: list[dict], locations_map: dict[str, list[dict]]) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InventoryTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1E1B4B"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "InventorySubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "InventorySection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#312E81"),
        spaceBefore=8,
        spaceAfter=6,
    )
    small_label = ParagraphStyle(
        "InventorySmallLabel",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )
    value_style = ParagraphStyle(
        "InventoryValue",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )
    right_value_style = ParagraphStyle(
        "InventoryRightValue",
        parent=value_style,
        alignment=TA_RIGHT,
    )
    center_value_style = ParagraphStyle(
        "InventoryCenterValue",
        parent=value_style,
        alignment=TA_CENTER,
    )

    story = [
        Paragraph("SmartLiving Inventory Branches & Warehouses", title_style),
        Paragraph(
            f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}<br/>Comprehensive branch and warehouse/location structure report.",
            subtitle_style,
        ),
    ]

    for branch in branches:
        branch_locations = locations_map.get(branch.get("id") or "", [])
        story.append(Paragraph(branch.get("name") or "Unnamed Branch", section_style))

        summary_data = [
            [
                Paragraph("Branch Code", small_label),
                Paragraph("Manager", small_label),
                Paragraph("Manager Location", small_label),
                Paragraph("Phone", small_label),
            ],
            [
                Paragraph(branch.get("code") or "-", value_style),
                Paragraph(branch.get("manager") or "-", value_style),
                Paragraph(branch.get("location") or "-", value_style),
                Paragraph(branch.get("phone") or "-", value_style),
            ],
            [
                Paragraph("Status", small_label),
                Paragraph("Total Locations", small_label),
                Paragraph("Total Stock Units", small_label),
                Paragraph("Branch Source", small_label),
            ],
            [
                Paragraph((branch.get("status") or "-").title(), value_style),
                Paragraph(str(branch.get("totalWarehouses") or 0), value_style),
                Paragraph(f"{int(branch.get('totalStockUnits') or 0):,}", value_style),
                Paragraph("Manager record in users collection", value_style),
            ],
        ]
        summary_table = Table(summary_data, colWidths=[42 * mm, 48 * mm, 48 * mm, 38 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                    ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 6))

        location_header = [
            Paragraph("Location / Warehouse", small_label),
            Paragraph("Type", small_label),
            Paragraph("Responsible User", small_label),
            Paragraph("Stock Units", small_label),
            Paragraph("Capacity", small_label),
            Paragraph("Status", small_label),
            Paragraph("Notes", small_label),
        ]
        location_rows = [location_header]

        if branch_locations:
            for location in branch_locations:
                location_rows.append(
                    [
                        Paragraph(
                            f"<b>{location.get('name') or '-'}</b><br/><font color='#64748B'>{location.get('code') or '-'}</font>",
                            value_style,
                        ),
                        Paragraph((location.get("type") or "-").replace("-", " ").title(), value_style),
                        Paragraph(location.get("responsibleUser") or "-", value_style),
                        Paragraph(f"{int(location.get('stockUnits') or 0):,}", right_value_style),
                        Paragraph(f"{int(location.get('capacity') or 0):,}", right_value_style),
                        Paragraph((location.get("status") or "-").title(), center_value_style),
                        Paragraph(location.get("notes") or "-", value_style),
                    ]
                )
        else:
            location_rows.append(
                [
                    Paragraph("No locations configured for this branch yet.", value_style),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )

        locations_table = Table(
            location_rows,
            colWidths=[38 * mm, 24 * mm, 34 * mm, 18 * mm, 18 * mm, 18 * mm, 34 * mm],
            repeatRows=1,
        )
        table_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#312E81")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if len(location_rows) > 1:
            for row_idx in range(1, len(location_rows)):
                if row_idx % 2 == 1:
                    table_style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#F8FAFC")))
        locations_table.setStyle(TableStyle(table_style))
        story.append(locations_table)
        story.append(Spacer(1, 10))

    doc.build(story)
    return buffer.getvalue()


@inventory_settings_api_bp.route("/branches-warehouses/export.pdf")
@inventory_main_admin_required
def export_branches_warehouses_pdf():
    payload = get_branches_payload()
    pdf_bytes = _build_branches_warehouses_pdf(payload.get("branches") or [], payload.get("locations") or {})
    filename = f"inventory_branches_warehouses_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@inventory_settings_api_bp.route("/branches/<path:branch_name>/locations", methods=["POST"])
@inventory_main_admin_required
def create_branch_location(branch_name: str):
    payload = _json_payload()
    branch_name = (branch_name or "").strip()
    if not branch_name:
        return jsonify({"ok": False, "error": "Branch is required."}), 400

    manager_exists = users_col.find_one({"role": "manager", "branch": branch_name}, {"_id": 1})
    if not manager_exists:
        return jsonify({"ok": False, "error": "Unknown branch."}), 404

    name = (payload.get("name") or "").strip()
    code = (payload.get("code") or "").strip()
    if not name or not code:
        return jsonify({"ok": False, "error": "Location name and code are required."}), 400

    existing = inventory_locations_col.find_one({"branch": branch_name, "code": code})
    if existing:
        return jsonify({"ok": False, "error": "A location with this code already exists for the branch."}), 400

    now = datetime.utcnow()
    doc = {
        "branch": branch_name,
        "name": name,
        "code": code,
        "type": (payload.get("type") or "room").strip(),
        "responsible_user": (payload.get("responsibleUser") or "").strip(),
        "stock_units": 0,
        "capacity": int(payload.get("capacity") or 0),
        "status": "Active" if (payload.get("status") or "active") == "active" else "Inactive",
        "notes": (payload.get("notes") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    inventory_locations_col.insert_one(doc)
    return jsonify({"ok": True})


@inventory_settings_api_bp.route("/users", methods=["POST"])
@inventory_main_admin_required
def create_user():
    payload = _json_payload()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    name = (payload.get("name") or "").strip()

    if not username or not password or not name:
        return jsonify({"ok": False, "error": "Username, password, and name are required."}), 400

    existing = users_col.find_one({"username": username})
    if existing:
        return jsonify({"ok": False, "error": "Username already exists."}), 400

    now = datetime.utcnow()
    role_map = get_inventory_role_map()
    selected_role = _assignable_role(payload.get("roleId") or "inventory-user", role_map)

    doc = {
        "username": username,
        "password": bcrypt_lib.hashpw(password.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8"),
        "role": "inventory",
        "name": name,
        "phone": (payload.get("phone") or "").strip(),
        "email": (payload.get("email") or "").strip(),
        "gender": (payload.get("gender") or "").strip(),
        "branch": (payload.get("branch") or "").strip(),
        "position": (payload.get("position") or "").strip(),
        "location": (payload.get("location") or "").strip(),
        "start_date": (payload.get("startDate") or "").strip(),
        "image_url": (payload.get("imageUrl") or "").strip(),
        "status": "Active" if payload.get("status") == "active" else "Disabled",
        "date_registered": now,
        "created_at": now,
        "updated_at": now,
        "main_admin": False,
        "inventory_role_id": selected_role.get("id") if selected_role else "inventory-user",
        "inventory_role_name": selected_role.get("name") if selected_role else "Inventory User",
    }

    result = users_col.insert_one(doc)
    created = users_col.find_one({"_id": result.inserted_id})
    return jsonify({"ok": True, "user": serialize_inventory_user(created, role_map=role_map)})


@inventory_settings_api_bp.route("/users/<user_id>", methods=["PATCH"])
@inventory_main_admin_required
def update_user(user_id: str):
    user_doc = get_inventory_user_doc(user_id)
    if not user_doc:
        return jsonify({"ok": False, "error": "User not found."}), 404

    payload = _json_payload()
    updates = {
        "name": (payload.get("name") or "").strip(),
        "phone": (payload.get("phone") or "").strip(),
        "email": (payload.get("email") or "").strip(),
        "gender": (payload.get("gender") or "").strip(),
        "branch": (payload.get("branch") or "").strip(),
        "position": (payload.get("position") or "").strip(),
        "location": (payload.get("location") or "").strip(),
        "start_date": (payload.get("startDate") or "").strip(),
        "image_url": (payload.get("imageUrl") or "").strip(),
        "status": "Active" if payload.get("status") == "active" else "Disabled",
        "updated_at": datetime.utcnow(),
    }

    username = (payload.get("username") or "").strip()
    if username and username != (user_doc.get("username") or ""):
        if users_col.find_one({"username": username, "_id": {"$ne": user_doc["_id"]}}):
            return jsonify({"ok": False, "error": "Username already exists."}), 400
        updates["username"] = username

    role_map = get_inventory_role_map()
    if not is_truthy(user_doc.get("main_admin")):
        selected_role = _assignable_role(payload.get("roleId") or "inventory-user", role_map)
        updates["inventory_role_id"] = selected_role.get("id") if selected_role else "inventory-user"
        updates["inventory_role_name"] = selected_role.get("name") if selected_role else "Inventory User"

    users_col.update_one({"_id": user_doc["_id"]}, {"$set": updates})
    updated = users_col.find_one({"_id": user_doc["_id"]})
    return jsonify({"ok": True, "user": serialize_inventory_user(updated, role_map=role_map)})


@inventory_settings_api_bp.route("/users/<user_id>/assign-role", methods=["POST"])
@inventory_main_admin_required
def assign_user_role(user_id: str):
    user_doc = get_inventory_user_doc(user_id)
    if not user_doc:
        return jsonify({"ok": False, "error": "User not found."}), 404
    if is_truthy(user_doc.get("main_admin")):
        return jsonify({"ok": False, "error": "Main admin role cannot be reassigned here."}), 400

    payload = _json_payload()
    role_map = get_inventory_role_map()
    selected_role = _assignable_role(payload.get("roleId") or "inventory-user", role_map)
    users_col.update_one(
        {"_id": user_doc["_id"]},
        {
            "$set": {
                "inventory_role_id": selected_role.get("id") if selected_role else "inventory-user",
                "inventory_role_name": selected_role.get("name") if selected_role else "Inventory User",
                "updated_at": datetime.utcnow(),
            }
        },
    )
    updated = users_col.find_one({"_id": user_doc["_id"]})
    return jsonify({"ok": True, "user": serialize_inventory_user(updated, role_map=role_map)})


@inventory_settings_api_bp.route("/users/<user_id>/toggle-status", methods=["POST"])
@inventory_main_admin_required
def toggle_user_status(user_id: str):
    user_doc = get_inventory_user_doc(user_id)
    if not user_doc:
        return jsonify({"ok": False, "error": "User not found."}), 404

    current_active = ((user_doc.get("status") or "").strip().lower() == "active")
    new_status = "Disabled" if current_active else "Active"
    users_col.update_one(
        {"_id": user_doc["_id"]},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow()}},
    )
    updated = users_col.find_one({"_id": user_doc["_id"]})
    return jsonify({"ok": True, "user": serialize_inventory_user(updated, role_map=get_inventory_role_map())})


@inventory_settings_api_bp.route("/users/<user_id>/reset-password", methods=["POST"])
@inventory_main_admin_required
def reset_user_password(user_id: str):
    user_doc = get_inventory_user_doc(user_id)
    if not user_doc:
        return jsonify({"ok": False, "error": "User not found."}), 404

    payload = _json_payload()
    password = payload.get("password") or ""
    if not password:
        return jsonify({"ok": False, "error": "Password is required."}), 400

    new_hash = bcrypt_lib.hashpw(password.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")
    users_col.update_one(
        {"_id": user_doc["_id"]},
        {"$set": {"password": new_hash, "updated_at": datetime.utcnow()}},
    )
    return jsonify({"ok": True})


@inventory_settings_api_bp.route("/roles", methods=["POST"])
@inventory_main_admin_required
def create_role():
    return jsonify({"ok": False, "error": "Inventory roles are fixed to Main Admin, Warehouse Manager, and Inventory User."}), 400


@inventory_settings_api_bp.route("/roles/<role_id>", methods=["PUT"])
@inventory_main_admin_required
def update_role(role_id: str):
    role_doc = inventory_roles_col.find_one({"id": role_id})
    if not role_doc:
        return jsonify({"ok": False, "error": "Role not found."}), 404

    payload = _json_payload()
    updates = {
        "name": (payload.get("name") or role_doc.get("name") or "").strip(),
        "description": (payload.get("description") or role_doc.get("description") or "").strip(),
        "permissions": normalize_permissions(payload.get("permissions") or role_doc.get("permissions")),
        "updated_at": datetime.utcnow(),
    }
    inventory_roles_col.update_one({"_id": role_doc["_id"]}, {"$set": updates})
    updated = inventory_roles_col.find_one({"_id": role_doc["_id"]})
    return jsonify({"ok": True, "role": serialize_role_doc(updated or {})})


@inventory_settings_api_bp.route("/roles/<role_id>", methods=["DELETE"])
@inventory_main_admin_required
def delete_role(role_id: str):
    return jsonify({"ok": False, "error": "Inventory roles are fixed and cannot be deleted."}), 400
