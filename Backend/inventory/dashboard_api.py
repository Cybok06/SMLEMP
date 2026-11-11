import csv
import re
from datetime import datetime
from io import BytesIO, StringIO

from bson import ObjectId
from flask import Blueprint, jsonify, request, send_file
from pymongo.errors import NetworkTimeout, ServerSelectionTimeoutError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from login import get_current_identity, role_required
from .branch_requests_store import (
    approve_branch_request,
    delete_branch_request,
    delete_branch_request_line,
    list_branch_requests,
)
from .product_cards_store import (
    create_product_card,
    get_product_card_bootstrap,
    list_product_cards,
    update_product_card,
    update_product_card_components,
)
from .products_store import (
    approve_stock_taking_session,
    create_inventory_product,
    create_stock_taking_session,
    create_stock_update_session,
    delete_inventory_product,
    get_stock_taking_dashboard,
    get_stock_taking_session_detail,
    get_inventory_distribution_payload,
    get_inventory_product_detail,
    get_inventory_products_for_location,
    list_inventory_products,
    submit_stock_taking_session,
    update_inventory_product,
    update_stock_taking_counts,
)
from .settings_store import get_effective_inventory_role, get_inventory_role_map, get_inventory_roles, get_inventory_user_doc
from .packaging_stock import transition as transition_packaging_stock, attempts as packaging_attempts
from .stock_deductions_store import (
    StockDeductionError,
    confirm_stock_deductions,
    deduction_detail,
    ensure_stock_deduction_indexes,
    export_stock_deductions_csv,
    export_stock_deductions_pdf,
    export_stock_deductions_xlsx,
    freeze_package_recipe,
    list_deduction_history,
    preview_stock_deductions,
)
from services.activity_audit import log_activity
from db import db


inventory_api_bp = Blueprint("inventory_api", __name__, url_prefix="/api/inventory")
packages_collection = db["packages"]
customers_collection = db["customers"]
products_collection = db["products"]

SUBMITTED_CARD_STATUS_FLOW = ["pending", "packaging", "delivering", "delivered"]
SUBMITTED_CARD_STATUS_LABELS = {
    "pending": "Submitted",
    "packaging": "Packaging",
    "delivering": "Delivering",
    "delivered": "Delivered",
}

try:
    ensure_stock_deduction_indexes()
except Exception as exc:
    # Startup must remain available; endpoints surface transaction/index errors explicitly.
    print("Stock deduction index setup failed:", exc)


def _oid(value):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _safe_dt(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000.0 if value > 10**12 else value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _status_label(status: str | None) -> str:
    key = str(status or "pending").strip().lower() or "pending"
    return SUBMITTED_CARD_STATUS_LABELS.get(key, "Submitted")


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return default
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def _matches_completion_tab(row: dict, tab: str) -> bool:
    completion = _safe_int(row.get("completion"), 0)
    remaining_amount = float(row.get("remainingAmount") or 0)
    delivery_status = str(row.get("deliveryStatus") or "").strip().lower()

    if tab == "70plus":
        return 70 <= completion < 80
    if tab == "80plus":
        return 80 <= completion < 90
    if tab == "90plus":
        return 90 <= completion < 100
    if tab == "completed":
        return completion == 100 or remaining_amount <= 0
    if tab == "awaiting-stock":
        return delivery_status == "awaiting-stock"
    if tab == "awaiting-delivery":
        return delivery_status in {"awaiting-delivery", "completed"}
    return True


def _inventory_branch_scope(identity: dict, user_doc: dict | None) -> dict:
    if identity.get("is_main_admin"):
        return {}
    effective_role = get_effective_inventory_role(user_doc)
    if effective_role.get("id") == "admin":
        return {}
    branch = str((user_doc or {}).get("branch") or "").strip()
    return {"branch": branch} if branch else {"branch": "__none__"}


def _submitted_card_scope(identity: dict, user_doc: dict | None) -> dict:
    if identity.get("is_main_admin"):
        return {}
    effective_role = get_effective_inventory_role(user_doc)
    if effective_role.get("id") == "admin":
        return {}
    branch = str((user_doc or {}).get("branch") or "").strip()
    if branch:
        manager_ids = [
            manager["_id"]
            for manager in db["users"].find({"role": "manager", "branch": branch}, {"_id": 1})
            if manager.get("_id")
        ]
        manager_id_values = manager_ids + [str(manager_id) for manager_id in manager_ids]
        scope_parts = [{"manager_branch": branch}]
        if manager_id_values:
            scope_parts.append({"manager_id": {"$in": manager_id_values}})
        return {"$or": scope_parts}
    return {}


def _set_customer_purchase_status(customer_id, product_index, package_status: str, now: datetime, actor_id=None):
    if customer_id is None or product_index is None:
        return

    customer_status = "submitted_for_packaging"
    if package_status == "packaging":
        customer_status = "packaged"
    elif package_status == "delivering":
        customer_status = "delivering"
    elif package_status == "delivered":
        customer_status = "delivered"

    update_doc = {
        f"purchases.{product_index}.product.status": customer_status,
        f"purchases.{product_index}.product.packaging_status": package_status,
        f"purchases.{product_index}.status": customer_status,
        f"purchases.{product_index}.package_status_updated_at": now,
        "updated_at": now,
    }
    if actor_id is not None:
        update_doc[f"purchases.{product_index}.package_status_updated_by"] = actor_id
    if package_status == "delivered":
        update_doc[f"purchases.{product_index}.delivered_at"] = now
        update_doc[f"purchases.{product_index}.product.delivered_at"] = now

    customers_collection.update_one({"_id": customer_id}, {"$set": update_doc})


def _attach_branch_metadata(rows: list[dict]) -> None:
    manager_ids = set()
    agent_ids = set()
    for row in rows:
        manager_id = row.get("manager_id")
        if manager_id:
            manager_ids.add(str(manager_id))
        agent_id = row.get("agent_id")
        if agent_id:
            agent_ids.add(str(agent_id))

    user_oids = [_oid(value) for value in {*(manager_ids), *(agent_ids)}]
    user_oids = [oid for oid in user_oids if oid]
    if not user_oids:
        return

    users_map = {
        str(doc["_id"]): doc
        for doc in db["users"].find({"_id": {"$in": user_oids}}, {"branch": 1, "name": 1})
    }

    for row in rows:
        manager_branch = ""
        manager_name = ""
        manager_id = row.get("manager_id")
        if manager_id:
            manager_doc = users_map.get(str(manager_id), {})
            manager_branch = str(manager_doc.get("branch") or "").strip()
            manager_name = str(manager_doc.get("name") or "").strip()

        agent_id = row.get("agent_id")
        agent_doc = users_map.get(str(agent_id), {}) if agent_id else {}
        agent_name = str(row.get("agent_name") or "").strip() or str(agent_doc.get("name") or "").strip()
        resolved_branch = str(row.get("manager_branch") or "").strip() or manager_branch
        if agent_name:
            row["agent_name"] = agent_name
        if resolved_branch:
            row["manager_branch"] = resolved_branch
        if manager_name and not row.get("manager_name"):
            row["manager_name"] = manager_name


def _prefetch_submitted_card_images(rows: list[dict]) -> dict[tuple[str, str, str], str]:
    manager_cf_ids: dict[ObjectId, set[str]] = {}
    manager_names: dict[ObjectId, set[str]] = {}

    for row in rows:
        product = row.get("product") or {}
        direct_image = str(product.get("image_url") or product.get("image") or "").strip()
        if direct_image:
            continue

        manager_id = _oid(row.get("manager_id"))
        if not manager_id:
            continue

        cf_image_id = str(product.get("cf_image_id") or "").strip()
        name = str(product.get("name") or "").strip()

        if cf_image_id:
            manager_cf_ids.setdefault(manager_id, set()).add(cf_image_id)
        if name:
            manager_names.setdefault(manager_id, set()).add(name)

    or_clauses = []
    for manager_id, cf_ids in manager_cf_ids.items():
        if cf_ids:
            or_clauses.append({"manager_id": manager_id, "cf_image_id": {"$in": list(cf_ids)}})
    for manager_id, names in manager_names.items():
        if names:
            or_clauses.append({"manager_id": manager_id, "name": {"$in": list(names)}})

    if not or_clauses:
        return {}

    image_lookup: dict[tuple[str, str, str], str] = {}
    cursor = products_collection.find(
        {"$or": or_clauses},
        {"manager_id": 1, "cf_image_id": 1, "name": 1, "image_url": 1},
    ).sort([("created_at", -1), ("_id", -1)])

    for product_doc in cursor:
        image_url = str(product_doc.get("image_url") or "").strip()
        manager_id = str(product_doc.get("manager_id") or "").strip()
        if not image_url or not manager_id:
            continue

        cf_image_id = str(product_doc.get("cf_image_id") or "").strip()
        if cf_image_id:
            image_lookup.setdefault((manager_id, "cf", cf_image_id), image_url)

        name = str(product_doc.get("name") or "").strip()
        if name:
            image_lookup.setdefault((manager_id, "name", name), image_url)

    return image_lookup


def _resolve_submitted_card_image(doc: dict, image_lookup: dict[tuple[str, str, str], str] | None = None) -> str:
    product = doc.get("product") or {}
    direct_image = str(product.get("image_url") or product.get("image") or "").strip()
    if direct_image:
        return direct_image

    manager_id = _oid(doc.get("manager_id"))
    if not manager_id:
        return ""

    manager_id_str = str(manager_id)

    cf_image_id = str(product.get("cf_image_id") or "").strip()
    name = str(product.get("name") or "").strip()

    if image_lookup:
        if cf_image_id:
            cached = image_lookup.get((manager_id_str, "cf", cf_image_id))
            if cached:
                return cached
        if name:
            cached = image_lookup.get((manager_id_str, "name", name))
            if cached:
                return cached

    if cf_image_id:
        product_doc = products_collection.find_one(
            {"manager_id": manager_id, "cf_image_id": cf_image_id},
            {"image_url": 1},
            sort=[("created_at", -1)],
        )
        image_url = str((product_doc or {}).get("image_url") or "").strip()
        if image_url:
            return image_url

    if name:
        product_doc = products_collection.find_one(
            {"manager_id": manager_id, "name": name},
            {"image_url": 1},
            sort=[("created_at", -1)],
        )
        image_url = str((product_doc or {}).get("image_url") or "").strip()
        if image_url:
            return image_url

    return ""


def _parse_date_filter(value: str | None, end_of_day: bool = False):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        if end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed
    except ValueError:
        return None


def _summarize_submitted_cards(cards: list[dict]) -> dict:
    summary = {
        "total": len(cards),
        "open": 0,
        "pending": 0,
        "packaging": 0,
        "delivering": 0,
        "delivered": 0,
    }
    for card in cards:
        status = str(card.get("status") or "pending").strip().lower() or "pending"
        if status in summary:
            summary[status] += 1
        if status != "delivered":
            summary["open"] += 1
    return summary


def _filter_submitted_card_rows(rows: list[dict]) -> list[dict]:
    search = str(request.args.get("search") or "").strip().lower()
    status_filter = str(request.args.get("status") or "all").strip().lower()
    branch_filter = str(request.args.get("branch") or "all").strip()
    agent_filter = str(request.args.get("agent") or "all").strip()
    date_from = _parse_date_filter(request.args.get("dateFrom"))
    date_to = _parse_date_filter(request.args.get("dateTo"), end_of_day=True)

    filtered = []
    for row in rows:
        status = str(row.get("status") or "pending").strip().lower() or "pending"
        submitted_at = _safe_dt(row.get("created_at"))
        manager_branch = str(row.get("manager_branch") or "").strip()
        agent_name = str(row.get("agent_name") or "").strip()
        customer_name = str(row.get("customer_name") or "").strip()
        customer_phone = str(row.get("customer_phone") or "").strip()
        product = row.get("product") or {}
        product_name = str(product.get("name") or product.get("package_name") or "").strip()

        if status_filter != "all" and status != status_filter:
            continue
        if branch_filter != "all" and manager_branch != branch_filter:
            continue
        if agent_filter != "all" and agent_name != agent_filter:
            continue
        if date_from and (not submitted_at or submitted_at < date_from):
            continue
        if date_to and (not submitted_at or submitted_at > date_to):
            continue
        if search:
            haystacks = [customer_name.lower(), customer_phone.lower(), product_name.lower(), agent_name.lower()]
            if not any(search in value for value in haystacks):
                continue
        filtered.append(row)

    return filtered


def _submitted_cards_payload(identity: dict, user_doc: dict | None, *, include_all_cards: bool = False) -> dict:
    scope = _submitted_card_scope(identity, user_doc)
    projection = {
        "customer_id": 1,
        "customer_name": 1,
        "customer_phone": 1,
        "product_index": 1,
        "product": 1,
        "purchase_type": 1,
        "qty": 1,
        "product_total": 1,
        "total_paid_selected_product": 1,
        "manager_id": 1,
        "manager_branch": 1,
        "manager_name": 1,
        "agent_id": 1,
        "agent_name": 1,
        "status": 1,
        "created_at": 1,
        "updated_at": 1,
        "source": 1,
        "by_role": 1,
        "status_history": 1,
    }
    rows = list(packages_collection.find(scope, projection).sort([("created_at", -1), ("_id", -1)]))
    _attach_branch_metadata(rows)
    filtered_rows = _filter_submitted_card_rows(rows)
    counts = _summarize_submitted_cards(filtered_rows)

    if identity.get("is_main_admin") or get_effective_inventory_role(user_doc).get("id") == "admin":
        branches = sorted(
            branch
            for branch in db["users"].distinct("branch", {"role": "manager"})
            if isinstance(branch, str) and branch.strip()
        )
    else:
        current_branch = str((user_doc or {}).get("branch") or "").strip()
        branches = [current_branch] if current_branch else []

    agents = sorted({str(row.get("agent_name") or "").strip() for row in filtered_rows if str(row.get("agent_name") or "").strip()})
    per_page = max(1, min(_safe_int(request.args.get("perPage"), 20), 100))
    page = max(1, _safe_int(request.args.get("page"), 1))
    total = len(filtered_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    paged_rows = filtered_rows[start:start + per_page]
    image_lookup = _prefetch_submitted_card_images(filtered_rows)
    paged_cards = [_serialize_submitted_card(row, image_lookup) for row in paged_rows]
    all_cards = [_serialize_submitted_card(row, image_lookup) for row in filtered_rows] if include_all_cards else []

    return {
        "scope": scope,
        "cards": paged_cards,
        "all_cards": all_cards,
        "counts": counts,
        "branches": branches,
        "agents": agents,
        "pagination": {
            "page": page,
            "perPage": per_page,
            "total": total,
            "totalPages": total_pages,
        },
    }


def _build_submitted_cards_pdf(cards: list[dict], filters: dict) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SubmittedCardsTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#111827"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "SubmittedCardsSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#6B7280"),
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "SubmittedCardsBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1F2937"),
    )

    applied_filters = ", ".join(
        f"{label}: {value}"
        for label, value in filters.items()
        if str(value or "").strip() and str(value) != "all"
    ) or "None"

    story = [
        Paragraph("Submitted Cards Report", title_style),
        Paragraph(
            f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}<br/>Filters: {applied_filters}",
            subtitle_style,
        ),
    ]

    table_rows = [[
        Paragraph("Customer", body_style),
        Paragraph("Product", body_style),
        Paragraph("Agent", body_style),
        Paragraph("Branch", body_style),
        Paragraph("Submitted", body_style),
        Paragraph("Status", body_style),
    ]]
    for card in cards:
        table_rows.append([
            Paragraph(card.get("customerName") or "-", body_style),
            Paragraph(f"{card.get('productName') or '-'} (Qty {card.get('quantity') or 0})", body_style),
            Paragraph(card.get("agentName") or "-", body_style),
            Paragraph(card.get("branch") or "-", body_style),
            Paragraph(card.get("submittedAt")[:10] if card.get("submittedAt") else "-", body_style),
            Paragraph(card.get("statusLabel") or "-", body_style),
        ])

    table = Table(table_rows, colWidths=[35 * mm, 42 * mm, 30 * mm, 24 * mm, 25 * mm, 22 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


def _customer_completion_rows(
    identity: dict,
    user_doc: dict | None,
    *,
    branch_filter: str = "all",
    agent_filter: str = "all",
    search: str = "",
    tab: str = "all",
    page: int = 1,
    per_page: int = 20,
) -> dict:
    customer_limit = 3000
    scope = _submitted_card_scope(identity, user_doc)
    branch = str((user_doc or {}).get("branch") or "").strip()
    if identity.get("is_main_admin") or get_effective_inventory_role(user_doc).get("id") == "admin":
        customer_query = {}
    elif branch:
        manager_ids = [
            manager["_id"]
            for manager in db["users"].find({"role": "manager", "branch": branch}, {"_id": 1})
            if manager.get("_id")
        ]
        customer_query = {"manager_id": {"$in": manager_ids + [str(manager_id) for manager_id in manager_ids]}}
    else:
        customer_query = {"_id": {"$in": []}}

    customers = list(
        db["customers"].find(
            customer_query,
            {
                "name": 1,
                "phone_number": 1,
                "image_url": 1,
                "purchases": 1,
                "manager_id": 1,
                "agent_id": 1,
            },
        ).limit(customer_limit)
    )
    customer_ids = [customer["_id"] for customer in customers if customer.get("_id")]
    manager_ids = {
        str(customer.get("manager_id"))
        for customer in customers
        if customer.get("manager_id") is not None
    }
    agent_ids = {
        str(customer.get("agent_id"))
        for customer in customers
        if customer.get("agent_id") is not None
    }
    user_lookup_ids = [_oid(value) for value in {*(manager_ids), *(agent_ids)} if _oid(value)]
    users_map = {
        str(doc["_id"]): doc
        for doc in db["users"].find({"_id": {"$in": user_lookup_ids}}, {"name": 1, "branch": 1, "role": 1})
    } if user_lookup_ids else {}

    payment_rows = list(
        db["payments"].find(
            {
                "customer_id": {"$in": customer_ids},
                "payment_type": {"$ne": "WITHDRAWAL"},
            },
            {"customer_id": 1, "product_index": 1, "amount": 1, "payment_type": 1},
        )
    ) if customer_ids else []

    payment_map: dict[tuple[str, int], float] = {}
    for payment in payment_rows:
        customer_id = str(payment.get("customer_id") or "")
        try:
            product_index = int(payment.get("product_index") if payment.get("product_index") is not None else -1)
        except Exception:
            product_index = -1
        key = (customer_id, product_index)
        amount = float(payment.get("amount") or 0)
        if str(payment.get("payment_type") or "").upper() == "WITHDRAWAL":
            payment_map[key] = payment_map.get(key, 0.0) - amount
        else:
            payment_map[key] = payment_map.get(key, 0.0) + amount

    package_query = dict(scope)
    if customer_ids:
        package_query["customer_id"] = {
            "$in": customer_ids + [str(customer_id) for customer_id in customer_ids]
        }
    package_rows = (
        list(packages_collection.find(package_query, {"customer_id": 1, "product_index": 1, "status": 1}).limit(customer_limit * 4))
        if customer_ids
        else []
    )
    package_status_map = {
        (str(row.get("customer_id") or ""), int(row.get("product_index") or 0)): str(row.get("status") or "pending").strip().lower() or "pending"
        for row in package_rows
        if row.get("customer_id") is not None and row.get("product_index") is not None
    }

    rows: list[dict] = []
    for customer in customers:
        customer_id = str(customer.get("_id") or "")
        manager_doc = users_map.get(str(customer.get("manager_id")), {})
        agent_doc = users_map.get(str(customer.get("agent_id")), {})
        row_branch = str(manager_doc.get("branch") or "").strip()
        row_agent_id = str(customer.get("agent_id") or "")
        row_agent_name = str(agent_doc.get("name") or "").strip()
        purchases = customer.get("purchases") or []
        if not isinstance(purchases, list):
            purchases = []

        if not purchases:
            rows.append(
                {
                    "id": f"{customer_id}:none",
                    "customerId": customer_id,
                    "customerName": customer.get("name") or "",
                    "customerPhone": customer.get("phone_number") or "",
                    "customerImage": customer.get("image_url") or "",
                    "branch": row_branch,
                    "agentId": row_agent_id,
                    "agentName": row_agent_name,
                    "productCard": "No Product Card",
                    "completion": 0,
                    "paidAmount": 0.0,
                    "totalAmount": 0.0,
                    "remainingAmount": 0.0,
                    "stockReady": 0,
                    "deliveryStatus": "not-ready",
                    "purchaseStatus": "no-purchase",
                    "enrollmentDate": "",
                    "estimatedFinish": "",
                    "profileUrl": f"/customer/{customer_id}",
                }
            )
            continue

        for index, purchase in enumerate(purchases):
            if not isinstance(purchase, dict):
                continue
            product = purchase.get("product") or {}
            if not isinstance(product, dict):
                product = {}
            total_amount = float(product.get("total") or 0)
            if total_amount <= 0:
                continue
            paid_amount = max(0.0, round(payment_map.get((customer_id, index), 0.0), 2))
            completion = int(min(100, round((paid_amount / total_amount) * 100))) if total_amount > 0 else 0
            remaining_amount = max(0.0, round(total_amount - paid_amount, 2))
            package_status = package_status_map.get((customer_id, index), "")
            purchase_status = str(product.get("status") or purchase.get("status") or "").strip().lower()

            delivery_status = "not-ready"
            if remaining_amount <= 0:
                if package_status == "delivered" or purchase_status == "delivered":
                    delivery_status = "delivered"
                elif package_status == "delivering" or purchase_status == "delivering":
                    delivery_status = "awaiting-delivery"
                elif package_status == "packaging" or purchase_status == "packaged":
                    delivery_status = "awaiting-delivery"
                elif package_status == "pending" or purchase_status == "submitted_for_packaging":
                    delivery_status = "awaiting-stock"
                else:
                    delivery_status = "completed"
            elif completion >= 90:
                delivery_status = "awaiting-stock"

            rows.append(
                {
                    "id": f"{customer_id}:{index}",
                    "customerId": customer_id,
                    "customerName": customer.get("name") or "",
                    "customerPhone": customer.get("phone_number") or "",
                    "customerImage": customer.get("image_url") or "",
                    "branch": row_branch,
                    "agentId": row_agent_id,
                    "agentName": row_agent_name,
                    "productCard": product.get("name") or product.get("package_name") or "Product",
                    "completion": completion,
                    "paidAmount": paid_amount,
                    "totalAmount": total_amount,
                    "remainingAmount": remaining_amount,
                    "stockReady": 100 if remaining_amount <= 0 else 0,
                    "deliveryStatus": delivery_status,
                    "purchaseStatus": purchase_status or package_status or "active",
                    "enrollmentDate": purchase.get("purchase_date") or "",
                    "estimatedFinish": purchase.get("estimated_end_date") or "",
                    "profileUrl": f"/customer/{customer_id}",
                }
            )

    base_rows = rows
    normalized_search = str(search or "").strip().lower()
    branch_filter = str(branch_filter or "all").strip()
    agent_filter = str(agent_filter or "all").strip()
    tab = str(tab or "all").strip().lower() or "all"

    filtered_rows = []
    for row in base_rows:
        if branch_filter != "all" and str(row.get("branch") or "").strip() != branch_filter:
            continue
        if agent_filter != "all" and str(row.get("agentId") or "").strip() != agent_filter:
            continue
        if normalized_search:
            haystacks = [
                str(row.get("customerName") or "").lower(),
                str(row.get("customerPhone") or "").lower(),
                str(row.get("productCard") or "").lower(),
                str(row.get("agentName") or "").lower(),
                str(row.get("branch") or "").lower(),
            ]
            if not any(normalized_search in value for value in haystacks):
                continue
        if not _matches_completion_tab(row, tab):
            continue
        filtered_rows.append(row)

    counts_source = []
    for row in base_rows:
        if branch_filter != "all" and str(row.get("branch") or "").strip() != branch_filter:
            continue
        if agent_filter != "all" and str(row.get("agentId") or "").strip() != agent_filter:
            continue
        if normalized_search:
            haystacks = [
                str(row.get("customerName") or "").lower(),
                str(row.get("customerPhone") or "").lower(),
                str(row.get("productCard") or "").lower(),
                str(row.get("agentName") or "").lower(),
                str(row.get("branch") or "").lower(),
            ]
            if not any(normalized_search in value for value in haystacks):
                continue
        counts_source.append(row)

    branch_options = sorted({str(row.get("branch") or "").strip() for row in base_rows if str(row.get("branch") or "").strip()})
    agent_options = sorted(
        [
            {
                "id": agent_id,
                "name": str((users_map.get(agent_id) or {}).get("name") or "Agent").strip(),
                "branch": str((users_map.get(agent_id) or {}).get("branch") or "").strip(),
            }
            for agent_id in {str(row.get("agentId") or "").strip() for row in base_rows if str(row.get("agentId") or "").strip()}
        ],
        key=lambda item: ((item.get("branch") or "").lower(), (item.get("name") or "").lower()),
    )

    rows.sort(key=lambda row: (-int(row["completion"]), float(row["remainingAmount"]), row["customerName"]))
    filtered_rows.sort(key=lambda row: (-int(row["completion"]), float(row["remainingAmount"]), row["customerName"]))

    total_items = len(filtered_rows)
    page = max(1, _safe_int(page, 1))
    per_page = max(1, min(100, _safe_int(per_page, 20)))
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page

    return {
        "customers": filtered_rows[start:end],
        "branches": branch_options,
        "agents": agent_options,
        "counts": {
            "all": len(counts_source),
            "70plus": sum(1 for row in counts_source if _matches_completion_tab(row, "70plus")),
            "80plus": sum(1 for row in counts_source if _matches_completion_tab(row, "80plus")),
            "90plus": sum(1 for row in counts_source if _matches_completion_tab(row, "90plus")),
            "completed": sum(1 for row in counts_source if _matches_completion_tab(row, "completed")),
            "awaiting-stock": sum(1 for row in counts_source if _matches_completion_tab(row, "awaiting-stock")),
            "awaiting-delivery": sum(1 for row in counts_source if _matches_completion_tab(row, "awaiting-delivery")),
        },
        "pagination": {
            "page": page,
            "perPage": per_page,
            "totalItems": total_items,
            "totalPages": total_pages,
        },
    }


def _stock_taking_session_summary(items: list[dict]) -> dict:
    total_items = len(items)
    counted_items = sum(1 for item in items if item.get("counted"))
    discrepancies = [item for item in items if int(item.get("variance") or 0) != 0]
    total_variance = round(sum(float(item.get("variance_value") or 0) for item in items if item.get("counted")), 2)
    return {
        "totalItems": total_items,
        "countedItems": counted_items,
        "discrepancies": len(discrepancies),
        "totalVariance": total_variance,
    }


def _build_audit_accountability_payload(identity: dict, user_doc: dict | None) -> dict:
    branch_scope = _inventory_branch_scope(identity, user_doc)
    sessions_raw = list(
        db["inventory_stock_taking_sessions"]
        .find(branch_scope)
        .sort([("created_at", -1)])
        .limit(250)
    )

    audits: list[dict] = []
    investigations: list[dict] = []
    resolutions: list[dict] = []
    alerts: list[dict] = []
    branch_map: dict[str, dict] = {}
    product_map: dict[str, dict] = {}
    category_map: dict[str, dict] = {}
    staff_map: dict[str, dict] = {}
    variance_trend_map: dict[str, float] = {}
    loss_trend_map: dict[str, dict] = {}
    reason_breakdown_map: dict[str, int] = {}

    total_discrepancies = 0
    total_loss_value = 0.0
    resolved_this_month = 0
    open_investigations = 0
    now = datetime.utcnow()

    for row in sessions_raw:
        items = row.get("items") or []
        summary = _stock_taking_session_summary(items)
        status = str(row.get("status") or "draft").strip().lower() or "draft"
        created_at = _safe_dt(row.get("created_at"))
        submitted_at = _safe_dt(row.get("submitted_at"))
        approved_at = _safe_dt(row.get("approved_at"))
        month_key = created_at.strftime("%b") if created_at else "Unknown"
        variance_trend_map[month_key] = variance_trend_map.get(month_key, 0.0) + float(summary["totalVariance"])
        loss_bucket = loss_trend_map.setdefault(month_key, {"month": month_key, "losses": 0, "value": 0.0})

        audit_record = {
            "id": str(row.get("session_number") or str(row.get("_id") or "")),
            "type": "scheduled",
            "location": row.get("location_name") or "",
            "branch": row.get("branch") or "",
            "scheduledDate": row.get("count_date") or "",
            "completedDate": approved_at.isoformat() if approved_at else "",
            "auditor": ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory User"),
            "status": status,
            "itemsToAudit": summary["totalItems"],
            "itemsCompleted": summary["countedItems"],
            "discrepanciesFound": summary["discrepancies"],
            "progress": int(round((summary["countedItems"] / max(1, summary["totalItems"])) * 100)),
            "submittedDate": submitted_at.isoformat() if submitted_at else "",
        }
        audits.append(audit_record)

        if approved_at and approved_at.year == now.year and approved_at.month == now.month:
            resolved_this_month += 1

        for item in items:
            variance = int(item.get("variance") or 0)
            if variance == 0:
                continue

            total_discrepancies += 1
            variance_value = round(float(item.get("variance_value") or 0), 2)
            loss_value = abs(variance_value)
            total_loss_value += loss_value
            loss_bucket["losses"] += 1
            loss_bucket["value"] += loss_value

            reason = str(item.get("discrepancy_reason") or "").strip() or "unresolved"
            reason_breakdown_map[reason] = reason_breakdown_map.get(reason, 0) + 1
            investigation_required = bool(item.get("investigation_required"))
            priority = "high" if investigation_required or abs(variance) >= 3 or loss_value >= 1000 else "medium" if abs(variance) >= 2 or loss_value >= 250 else "low"
            inv_status = "resolved" if status == "approved" else "escalated" if status == "submitted" and priority == "high" else "investigating"
            if inv_status != "resolved":
                open_investigations += 1

            investigation_id = f"INV-{str(row.get('session_number') or row.get('_id') or '')}-{str(item.get('id') or '')}"
            assigned_to = (
                ((row.get("approved_by") or {}).get("name"))
                if inv_status == "resolved"
                else ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory Team")
            )
            timeline = [
                {
                    "date": created_at.isoformat() if created_at else "",
                    "action": "Stock-taking session created",
                    "user": ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory User"),
                    "type": "session",
                }
            ]
            if submitted_at:
                timeline.append(
                    {
                        "date": submitted_at.isoformat(),
                        "action": "Session submitted for review",
                        "user": ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory User"),
                        "type": "submission",
                    }
                )
            if approved_at:
                timeline.append(
                    {
                        "date": approved_at.isoformat(),
                        "action": "Variance approved and stock adjusted",
                        "user": ((row.get("approved_by") or {}).get("name") or "Inventory Approver"),
                        "type": "approval",
                    }
                )

            investigations.append(
                {
                    "id": investigation_id,
                    "discrepancyId": str(item.get("id") or ""),
                    "auditId": audit_record["id"],
                    "item": item.get("product_name") or "Item",
                    "sku": item.get("sku") or "",
                    "category": item.get("category") or "Uncategorized",
                    "location": row.get("location_name") or "",
                    "branch": row.get("branch") or "",
                    "systemStock": int(item.get("system_quantity") or 0),
                    "physicalStock": max(0, int(item.get("actual_count") or 0) - int(item.get("damaged_quantity") or 0)),
                    "variance": variance,
                    "varianceValue": variance_value,
                    "reportedDate": submitted_at.isoformat() if submitted_at else created_at.isoformat() if created_at else "",
                    "status": inv_status,
                    "priority": priority,
                    "assignedTo": assigned_to,
                    "lastHandler": ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory User"),
                    "reason": reason,
                    "notes": item.get("notes") or "",
                    "timeline": timeline,
                }
            )

            if inv_status == "resolved":
                resolutions.append(
                    {
                        "id": f"RES-{str(row.get('session_number') or row.get('_id') or '')}-{str(item.get('id') or '')}",
                        "investigationId": investigation_id,
                        "item": item.get("product_name") or "Item",
                        "branch": row.get("branch") or "",
                        "variance": variance,
                        "resolvedDate": approved_at.isoformat() if approved_at else "",
                        "resolvedBy": ((row.get("approved_by") or {}).get("name") or "Inventory Approver"),
                        "action": "adjustment",
                        "actionDetails": f"Stock adjusted after discrepancy reason '{reason}'.",
                        "status": "resolved",
                        "staffAction": "none",
                        "cost": loss_value,
                    }
                )
            else:
                alerts.append(
                    {
                        "id": f"ALT-{str(row.get('session_number') or row.get('_id') or '')}-{str(item.get('id') or '')}",
                        "type": "variance",
                        "severity": "critical" if priority == "high" else "warning" if priority == "medium" else "info",
                        "message": f"{item.get('product_name') or 'Item'} variance detected",
                        "details": f"{row.get('branch') or ''} / {row.get('location_name') or ''} | variance {variance} | reason: {reason}",
                        "createdDate": submitted_at.isoformat() if submitted_at else created_at.isoformat() if created_at else "",
                        "status": "active",
                    }
                )

            branch_key = str(row.get("branch") or "").strip() or "Unknown"
            branch_bucket = branch_map.setdefault(branch_key, {"branch": branch_key, "incidents": 0, "lossValue": 0.0, "shrinkage": 0.0})
            branch_bucket["incidents"] += 1
            branch_bucket["lossValue"] += loss_value

            product_key = str(item.get("product_name") or "Item").strip() or "Item"
            product_bucket = product_map.setdefault(
                product_key,
                {
                    "item": product_key,
                    "sku": item.get("sku") or "",
                    "category": item.get("category") or "Uncategorized",
                    "incidents": 0,
                    "lossValue": 0.0,
                    "riskScore": 0,
                },
            )
            product_bucket["incidents"] += 1
            product_bucket["lossValue"] += loss_value

            category_key = str(item.get("category") or "Uncategorized").strip() or "Uncategorized"
            category_bucket = category_map.setdefault(category_key, {"category": category_key, "shrinkage": 0.0, "value": 0.0})
            if variance < 0:
                category_bucket["shrinkage"] += abs(variance)
            category_bucket["value"] += loss_value

            staff_key = ((row.get("created_by") or {}).get("name") or (row.get("created_by") or {}).get("username") or "Inventory User")
            staff_bucket = staff_map.setdefault(
                staff_key,
                {
                    "id": staff_key,
                    "staffName": staff_key,
                    "role": "Inventory User",
                    "location": branch_key,
                    "incidents": 0,
                    "lastIncident": submitted_at.isoformat() if submitted_at else created_at.isoformat() if created_at else "",
                    "riskLevel": "low",
                    "details": "Repeated variance involvement across stock-taking sessions.",
                },
            )
            staff_bucket["incidents"] += 1
            latest_incident = submitted_at.isoformat() if submitted_at else created_at.isoformat() if created_at else ""
            if latest_incident > str(staff_bucket.get("lastIncident") or ""):
                staff_bucket["lastIncident"] = latest_incident

    for entry in branch_map.values():
        entry["lossValue"] = round(float(entry["lossValue"]), 2)
        entry["shrinkage"] = round((entry["lossValue"] / max(1.0, total_loss_value)) * 100, 1) if total_loss_value else 0.0
    for entry in product_map.values():
        entry["lossValue"] = round(float(entry["lossValue"]), 2)
        entry["riskScore"] = min(100, int(entry["incidents"] * 15 + min(entry["lossValue"] / 100, 40)))
    for entry in category_map.values():
        entry["value"] = round(float(entry["value"]), 2)
        entry["shrinkage"] = round(float(entry["shrinkage"]), 1)
    for entry in staff_map.values():
        incidents = int(entry["incidents"])
        entry["riskLevel"] = "high" if incidents >= 4 else "medium" if incidents >= 2 else "low"

    audits.sort(key=lambda row: (row.get("scheduledDate") or "", row.get("id") or ""), reverse=True)
    investigations.sort(key=lambda row: (row.get("reportedDate") or "", row.get("id") or ""), reverse=True)
    resolutions.sort(key=lambda row: (row.get("resolvedDate") or "", row.get("id") or ""), reverse=True)
    alerts.sort(key=lambda row: (row.get("createdDate") or "", row.get("id") or ""), reverse=True)

    metrics = {
        "totalAudits": len(audits),
        "activeAudits": sum(1 for audit in audits if audit["status"] in {"draft", "counting", "submitted", "reviewed"}),
        "totalLosses": total_discrepancies,
        "totalLossValue": round(total_loss_value, 2),
        "shrinkagePercent": round((sum(abs(float(item.get("variance") or 0)) for item in investigations if float(item.get("variance") or 0) < 0) / max(1, total_discrepancies)) * 100, 1) if total_discrepancies else 0.0,
        "highRiskProducts": sum(1 for row in product_map.values() if int(row.get("riskScore") or 0) >= 70),
        "highRiskBranches": sum(1 for row in branch_map.values() if int(row.get("incidents") or 0) >= 2),
        "staffRiskAlerts": sum(1 for row in staff_map.values() if row.get("riskLevel") in {"high", "medium"}),
        "openInvestigations": open_investigations,
        "resolvedThisMonth": resolved_this_month,
    }

    return {
        "metrics": metrics,
        "audits": audits[:100],
        "investigations": investigations[:200],
        "resolutions": resolutions[:200],
        "alerts": alerts[:100],
        "analytics": {
            "lossTrends": list(loss_trend_map.values())[-6:],
            "varianceTrend": [{"month": key, "variance": round(value, 2)} for key, value in list(variance_trend_map.items())[-6:]],
            "shrinkageByCategory": sorted(category_map.values(), key=lambda row: float(row.get("value") or 0), reverse=True)[:8],
            "highRiskProducts": sorted(product_map.values(), key=lambda row: (int(row.get("riskScore") or 0), float(row.get("lossValue") or 0)), reverse=True)[:10],
            "highRiskBranches": sorted(branch_map.values(), key=lambda row: (int(row.get("incidents") or 0), float(row.get("lossValue") or 0)), reverse=True)[:10],
            "staffRiskAlerts": sorted(staff_map.values(), key=lambda row: (int(row.get("incidents") or 0), row.get("staffName") or ""), reverse=True)[:10],
            "reasonBreakdown": [{"reason": key, "count": value} for key, value in sorted(reason_breakdown_map.items(), key=lambda item: item[1], reverse=True)[:10]],
        },
    }


def _build_reports_analytics_payload(identity: dict, user_doc: dict | None) -> dict:
    inventory_scope = _inventory_branch_scope(identity, user_doc)
    products = list_inventory_products()
    stock_taking = get_stock_taking_dashboard()
    branch_requests = list_branch_requests()
    submitted_scope = _submitted_card_scope(identity, user_doc)
    packages = list(packages_collection.find(submitted_scope).sort([("created_at", -1)]).limit(1000))
    purchase_orders = list(db["inventory_purchase_orders"].find({}).sort([("created_at", -1)]).limit(500))
    deliveries = list(db["supplier_deliveries"].find({}).sort([("updated_at", -1), ("created_at", -1)]).limit(500))
    suppliers = list(db["inventory_suppliers"].find({}).limit(500))
    locations = list(db["inventory_branch_locations"].find(inventory_scope).limit(500))
    users = list(db["users"].find({"role": {"$in": ["agent", "manager"]}}, {"name": 1, "branch": 1, "role": 1}).limit(2000))
    users_map = {str(doc.get("_id")): doc for doc in users if doc.get("_id")}

    inventory_metrics = {
        "totalProducts": len(products),
        "totalStock": sum(int(item.get("totalStock") or 0) for item in products),
        "totalValue": round(sum(int(item.get("totalStock") or 0) * float(item.get("unitCost") or 0) for item in products), 2),
        "criticalCount": sum(1 for item in products if str(item.get("status") or "") == "critical"),
    }
    inventory_value_by_category_map: dict[str, float] = {}
    inventory_value_by_branch_map: dict[str, dict] = {}
    inventory_movement_trend_map: dict[str, dict] = {}
    stockout_risk_rows = []
    for item in products:
        category = str(item.get("category") or "Uncategorized").strip() or "Uncategorized"
        total_stock = int(item.get("totalStock") or 0)
        unit_cost = float(item.get("unitCost") or 0)
        inventory_value_by_category_map[category] = inventory_value_by_category_map.get(category, 0.0) + (total_stock * unit_cost)
        reorder_point = int(item.get("reorderPoint") or 0)
        forecast_demand = int(item.get("forecastDemand") or 0)
        if reorder_point > 0 or forecast_demand > 0:
            days_until_stockout = 0
            if forecast_demand > 0:
                days_until_stockout = max(0, int(round((total_stock / max(1, forecast_demand)) * 30)))
            risk_score = min(100, int((max(0, reorder_point - total_stock) / max(1, reorder_point or 1)) * 70 + (forecast_demand > total_stock) * 30))
            stockout_risk_rows.append(
                {
                    "item": item.get("name") or "Product",
                    "currentStock": total_stock,
                    "forecast": forecast_demand,
                    "riskScore": risk_score,
                    "daysUntilStockout": days_until_stockout,
                }
            )
        for entry in item.get("entries") or []:
            branch_name = str(entry.get("branch") or "").strip() or "Unassigned"
            entry_qty = int(entry.get("quantity") or 0)
            entry_unit_cost = float(entry.get("costPrice") or item.get("unitCost") or 0)
            branch_bucket = inventory_value_by_branch_map.setdefault(
                branch_name,
                {
                    "branch": branch_name,
                    "manager": "",
                    "products": set(),
                    "stockUnits": 0,
                    "inventoryValue": 0.0,
                },
            )
            branch_bucket["products"].add(str(item.get("id") or item.get("name") or branch_name))
            branch_bucket["stockUnits"] += max(0, entry_qty)
            branch_bucket["inventoryValue"] += max(0, entry_qty) * entry_unit_cost

            date_key = str(entry.get("updatedAt") or "")[:10] or str(entry.get("expiryDate") or "")[:10]
            if not date_key:
                continue
            bucket = inventory_movement_trend_map.setdefault(date_key, {"date": date_key[5:] if len(date_key) >= 10 else date_key, "inbound": 0, "outbound": 0, "adjustments": 0})
            qty = entry_qty
            if qty >= 0:
                bucket["inbound"] += qty
            else:
                bucket["outbound"] += abs(qty)
            source = str(entry.get("source") or "").strip().lower()
            if "stock_taking" in source or "adjust" in source:
                bucket["adjustments"] += qty

    inventory_value_by_category = [
        {"category": category, "value": round(value, 2)}
        for category, value in sorted(inventory_value_by_category_map.items(), key=lambda item: item[1], reverse=True)
    ]
    total_inventory_value = sum(row["value"] for row in inventory_value_by_category) or 1.0
    for row in inventory_value_by_category:
        row["percentage"] = round((row["value"] / total_inventory_value) * 100, 1)

    managers_by_branch: dict[str, list[str]] = {}
    for user in users:
        if str(user.get("role") or "").strip().lower() != "manager":
            continue
        branch_name = str(user.get("branch") or "").strip()
        manager_name = str(user.get("name") or "").strip()
        if not branch_name or not manager_name:
            continue
        managers_by_branch.setdefault(branch_name, []).append(manager_name)

    inventory_value_by_branch = []
    for branch_name, row in inventory_value_by_branch_map.items():
        manager_names = sorted(set(managers_by_branch.get(branch_name, [])))
        inventory_value_by_branch.append(
            {
                "branch": branch_name,
                "manager": ", ".join(manager_names) if manager_names else "Unassigned",
                "products": len(row["products"]),
                "stockUnits": int(row["stockUnits"] or 0),
                "inventoryValue": round(float(row["inventoryValue"] or 0), 2),
            }
        )
    inventory_value_by_branch.sort(
        key=lambda row: (-float(row.get("inventoryValue") or 0), row.get("branch") or "")
    )

    inventory_movement_trend = list(sorted(inventory_movement_trend_map.values(), key=lambda row: row["date"]))[-12:]
    stockout_risk = sorted(stockout_risk_rows, key=lambda row: (row["riskScore"], -row["currentStock"]), reverse=True)[:10]

    warehouse_utilization = []
    for location in locations:
        capacity = int(location.get("capacity") or 0)
        stock_units = int(location.get("stock_units") or 0)
        warehouse_utilization.append(
            {
                "location": location.get("name") or "Location",
                "branch": location.get("branch") or "",
                "capacity": capacity,
                "current": stock_units,
                "utilization": round((stock_units / capacity) * 100) if capacity > 0 else 0,
            }
        )
    recent_transfers = []
    branch_request_trends_map: dict[str, dict] = {}
    for req in branch_requests:
        request_date = str(req.get("requestDate") or "")[:10]
        month_key = request_date[:7] if request_date else "unknown"
        bucket = branch_request_trends_map.setdefault(month_key, {"month": month_key, "requests": 0, "approved": 0, "rejected": 0})
        bucket["requests"] += 1
        status = str(req.get("status") or "").strip().lower()
        if status in {"approved", "closed"}:
            bucket["approved"] += 1
        elif status == "rejected":
            bucket["rejected"] += 1
        recent_transfers.append(
            {
                "date": request_date,
                "from": "Source Warehouse",
                "to": req.get("branch") or "",
                "items": int(req.get("totalQuantity") or 0),
                "status": req.get("status") or "",
            }
        )
    branch_request_trends = list(sorted(branch_request_trends_map.values(), key=lambda row: row["month"]))[-12:]

    fulfillment_trend_map: dict[str, dict] = {}
    fulfillment_by_status_map: dict[str, int] = {}
    delivery_route_map: dict[str, dict] = {}
    for pkg in packages:
        created_at = _safe_dt(pkg.get("created_at"))
        day_key = created_at.strftime("%m-%d") if created_at else "unknown"
        bucket = fulfillment_trend_map.setdefault(day_key, {"date": day_key, "orders": 0, "delivered": 0, "pending": 0, "cancelled": 0})
        bucket["orders"] += 1
        status = str(pkg.get("status") or "pending").strip().lower()
        if status == "delivered":
            bucket["delivered"] += 1
        elif status in {"cancelled", "rejected"}:
            bucket["cancelled"] += 1
        else:
            bucket["pending"] += 1
        fulfillment_by_status_map[status] = fulfillment_by_status_map.get(status, 0) + 1
        route = f"{pkg.get('manager_branch') or 'Unknown'} -> Customer"
        route_bucket = delivery_route_map.setdefault(route, {"route": route, "deliveries": 0, "accurate": 0, "discrepancies": 0, "onTimePct": 0, "avgDelayMin": 0})
        route_bucket["deliveries"] += 1
        if status == "delivered":
            route_bucket["accurate"] += 1
        else:
            route_bucket["discrepancies"] += 1
    fulfillment_metrics = list(sorted(fulfillment_trend_map.values(), key=lambda row: row["date"]))[-14:]
    fulfillment_by_status = [{"status": key.title(), "count": value} for key, value in fulfillment_by_status_map.items()]
    for row in delivery_route_map.values():
        row["onTimePct"] = round((row["accurate"] / max(1, row["deliveries"])) * 100, 1)
    delivery_by_route = list(sorted(delivery_route_map.values(), key=lambda row: row["deliveries"], reverse=True))[:10]

    demand_forecast_map: dict[str, dict] = {}
    completion_forecast_weeks: dict[str, int] = {}
    for customer in customers_collection.find({}, {"purchases": 1}).limit(5000):
        for purchase in customer.get("purchases") or []:
            if not isinstance(purchase, dict):
                continue
            purchase_date = str(purchase.get("purchase_date") or "")[:7]
            if purchase_date:
                bucket = demand_forecast_map.setdefault(purchase_date, {"month": purchase_date, "actual": 0, "forecast": 0, "variance": 0})
                bucket["actual"] += 1
            end_date = _safe_dt(purchase.get("end_date"))
            if end_date:
                week_key = f"W{max(1, ((end_date.date() - datetime.utcnow().date()).days // 7) + 1)}"
                completion_forecast_weeks[week_key] = completion_forecast_weeks.get(week_key, 0) + 1
    demand_forecast = list(sorted(demand_forecast_map.values(), key=lambda row: row["month"]))[-6:]
    for row in demand_forecast:
        row["forecast"] = row["actual"]
        row["variance"] = 0
    completion_forecast = []
    for week_key, expected in sorted(completion_forecast_weeks.items())[:4]:
        completion_forecast.append({"week": week_key, "expected": expected, "optimistic": int(round(expected * 1.2)), "pessimistic": max(0, int(round(expected * 0.8)))})

    audit_payload = _build_audit_accountability_payload(identity, user_doc)

    procurement_trend_map: dict[str, dict] = {}
    po_accuracy_detail = []
    variance_by_category_map: dict[str, dict] = {}
    supplier_score_map: dict[str, dict] = {}
    delivery_accuracy_trend_map: dict[str, dict] = {}
    for po in purchase_orders:
        po_number = str(po.get("po_number") or po.get("ref_no") or "")
        created_at = _safe_dt(po.get("created_at"))
        expected_delivery = str(po.get("expected_delivery") or "")[:10]
        supplier_name = str(po.get("supplier_name") or po.get("supplier") or "Supplier").strip()
        month_key = created_at.strftime("%Y-%m") if created_at else "unknown"
        po_items = po.get("items") or []
        ordered_qty = sum(int(item.get("qty") or item.get("quantity") or 0) for item in po_items if isinstance(item, dict))
        delivered_qty = sum(int(item.get("received_qty") or item.get("delivered_qty") or 0) for item in po_items if isinstance(item, dict))
        if delivered_qty == 0:
            matched_delivery = next((row for row in deliveries if str(row.get("po_id") or "") == str(po.get("_id") or "")), None)
            if matched_delivery:
                delivered_qty = sum(int(line.get("receivedQty") or line.get("received_qty") or line.get("delivered_qty") or 0) for line in matched_delivery.get("lines") or [] if isinstance(line, dict))
        accuracy = round((delivered_qty / max(1, ordered_qty)) * 100, 1) if ordered_qty else 0.0
        variance = delivered_qty - ordered_qty
        discrepancy_count = 0 if variance == 0 else 1
        trend_bucket = procurement_trend_map.setdefault(month_key, {"month": month_key, "ordered": 0, "received": 0, "accuracy": 0.0, "discrepancies": 0, "count": 0})
        trend_bucket["ordered"] += ordered_qty
        trend_bucket["received"] += delivered_qty
        trend_bucket["discrepancies"] += discrepancy_count
        trend_bucket["count"] += 1
        po_accuracy_detail.append(
            {
                "po": po_number,
                "supplier": supplier_name,
                "date": expected_delivery,
                "ordered": ordered_qty,
                "received": delivered_qty,
                "variance": variance,
                "accuracyPct": accuracy,
                "status": "accurate" if accuracy >= 99 else "minor-variance" if accuracy >= 95 else "variance" if accuracy >= 90 else "major-variance",
                "leadDays": max(0, int((( _safe_dt(po.get("expected_delivery")) or datetime.utcnow()) - (created_at or datetime.utcnow())).days)),
            }
        )
        score_bucket = supplier_score_map.setdefault(
            supplier_name,
            {"supplier": supplier_name, "orders": 0, "fulfilledOnTime": 0, "avgLeadDays": 0.0, "accuracyPct": 0.0, "discrepancyRate": 0.0, "qualityRejectionPct": 0.0, "score": 0, "trend": 0, "tier": "Bronze"},
        )
        score_bucket["orders"] += 1
        score_bucket["avgLeadDays"] += max(0, int((((_safe_dt(po.get("expected_delivery")) or datetime.utcnow()) - (created_at or datetime.utcnow())).days)))
        score_bucket["accuracyPct"] += accuracy
        if accuracy >= 95:
            score_bucket["fulfilledOnTime"] += 1

        for item in po_items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "Uncategorized").strip() or "Uncategorized"
            bucket = variance_by_category_map.setdefault(category, {"category": category, "ordered": 0, "received": 0, "variance": 0, "accuracyPct": 0.0, "count": 0})
            item_ordered = int(item.get("qty") or item.get("quantity") or 0)
            item_received = int(item.get("received_qty") or item.get("delivered_qty") or item.get("qty") or 0)
            bucket["ordered"] += item_ordered
            bucket["received"] += item_received
            bucket["variance"] += max(0, item_ordered - item_received)
            bucket["count"] += 1

    procurement_accuracy_trend = []
    for row in sorted(procurement_trend_map.values(), key=lambda item: item["month"])[-12:]:
        row["accuracy"] = round((row["received"] / max(1, row["ordered"])) * 100, 1) if row["ordered"] else 0.0
        procurement_accuracy_trend.append({key: value for key, value in row.items() if key != "count"})
    variance_by_category = []
    for row in sorted(variance_by_category_map.values(), key=lambda item: item["ordered"], reverse=True):
        row["accuracyPct"] = round((row["received"] / max(1, row["ordered"])) * 100, 1) if row["ordered"] else 0.0
        variance_by_category.append({key: value for key, value in row.items() if key != "count"})

    for pkg in packages:
        created_at = _safe_dt(pkg.get("created_at"))
        day_key = created_at.strftime("%m-%d") if created_at else "unknown"
        bucket = delivery_accuracy_trend_map.setdefault(day_key, {"date": day_key, "total": 0, "accurate": 0, "discrepancies": 0, "onTime": 0, "delayed": 0})
        bucket["total"] += 1
        delivered_at = _safe_dt(pkg.get("updated_at"))
        lead_days = ((delivered_at or datetime.utcnow()) - (created_at or datetime.utcnow())).days
        status = str(pkg.get("status") or "pending").strip().lower()
        on_time = status == "delivered" and lead_days <= 7
        accurate = status == "delivered"
        if accurate:
            bucket["accurate"] += 1
        else:
            bucket["discrepancies"] += 1
        if on_time:
            bucket["onTime"] += 1
        else:
            bucket["delayed"] += 1
    delivery_accuracy_trend = []
    for row in sorted(delivery_accuracy_trend_map.values(), key=lambda item: item["date"])[-14:]:
        total = row["total"] or 1
        row["accuracyPct"] = round((row["accurate"] / total) * 100, 1)
        row["onTimePct"] = round((row["onTime"] / total) * 100, 1)
        delivery_accuracy_trend.append(row)

    delivery_discrepancy_breakdown = [
        {"type": "Pending Delivery", "count": fulfillment_by_status_map.get("pending", 0), "pct": 0},
        {"type": "Packaging Delay", "count": fulfillment_by_status_map.get("packaging", 0), "pct": 0},
        {"type": "In Transit", "count": fulfillment_by_status_map.get("delivering", 0), "pct": 0},
        {"type": "Delivered", "count": fulfillment_by_status_map.get("delivered", 0), "pct": 0},
    ]
    total_delivery_breakdown = sum(item["count"] for item in delivery_discrepancy_breakdown) or 1
    for item in delivery_discrepancy_breakdown:
        item["pct"] = round((item["count"] / total_delivery_breakdown) * 100, 1)

    supplier_scorecard = []
    for row in supplier_score_map.values():
        orders = row["orders"] or 1
        row["avgLeadDays"] = round(row["avgLeadDays"] / orders, 1)
        row["accuracyPct"] = round(row["accuracyPct"] / orders, 1)
        row["discrepancyRate"] = round(((orders - row["fulfilledOnTime"]) / orders) * 100, 1)
        row["qualityRejectionPct"] = 0.0
        row["score"] = max(
            0,
            min(
                100,
                int(
                    (row["accuracyPct"] * 0.5)
                    + ((100 - (row["discrepancyRate"] * 3)) * 0.3)
                    + ((10 - min(row["avgLeadDays"], 10)) * 2)
                ),
            ),
        )
        row["trend"] = 0
        row["tier"] = "Platinum" if row["score"] >= 95 else "Gold" if row["score"] >= 90 else "Silver" if row["score"] >= 80 else "Bronze"
        supplier_scorecard.append(row)
    supplier_scorecard.sort(key=lambda item: item["score"], reverse=True)

    staff_handling_map: dict[str, dict] = {}
    for pkg in packages:
        agent_id = str(pkg.get("agent_id") or "")
        agent_doc = users_map.get(agent_id, {})
        name = str(pkg.get("agent_name") or agent_doc.get("name") or "Unknown Staff").strip()
        bucket = staff_handling_map.setdefault(name, {"staffName": name, "role": agent_doc.get("role") or "agent", "branch": pkg.get("manager_branch") or agent_doc.get("branch") or "", "handled": 0, "delivered": 0, "pending": 0, "accuracyPct": 0.0})
        bucket["handled"] += 1
        status = str(pkg.get("status") or "pending").strip().lower()
        if status == "delivered":
            bucket["delivered"] += 1
        else:
            bucket["pending"] += 1
    for row in staff_handling_map.values():
        row["accuracyPct"] = round((row["delivered"] / max(1, row["handled"])) * 100, 1)
    staff_handling = list(sorted(staff_handling_map.values(), key=lambda item: item["handled"], reverse=True))[:20]

    return {
        "inventory": {
            "metrics": inventory_metrics,
            "stockLevels": products[:20],
            "movementTrend": inventory_movement_trend,
            "valueByCategory": inventory_value_by_category,
            "valueByBranch": inventory_value_by_branch,
        },
        "warehouse": {
            "transfers": recent_transfers[:20],
            "utilization": warehouse_utilization,
            "requestTrends": branch_request_trends,
        },
        "forecast": {
            "demandForecast": demand_forecast,
            "completionForecast": completion_forecast,
            "stockoutRisk": stockout_risk,
        },
        "fulfillment": {
            "metrics": fulfillment_metrics,
            "statusBreakdown": fulfillment_by_status,
            "routes": delivery_by_route,
        },
        "audit": audit_payload,
        "procurement": {
            "trend": procurement_accuracy_trend,
            "detail": po_accuracy_detail[:50],
            "varianceByCategory": variance_by_category,
        },
        "deliveryAccuracy": {
            "trend": delivery_accuracy_trend,
            "breakdown": delivery_discrepancy_breakdown,
            "routes": delivery_by_route,
        },
        "supplierPerformance": {
            "scorecard": supplier_scorecard[:20],
            "suppliersCount": len(suppliers),
            "costUpdates": db["inventory_cost_updates"].count_documents({}),
        },
        "staffHandling": {
            "rows": staff_handling,
        },
    }


def _scoped_customer_query(identity: dict, user_doc: dict | None) -> dict:
    branch = str((user_doc or {}).get("branch") or "").strip()
    if identity.get("is_main_admin") or get_effective_inventory_role(user_doc).get("id") == "admin":
        return {}
    if branch:
        branch_user_ids = [
            row["_id"]
            for row in db["users"].find(
                {"branch": {"$regex": f"^{re.escape(branch)}$", "$options": "i"}},
                {"_id": 1},
            )
            if row.get("_id")
        ]
        scoped_ids = branch_user_ids + [str(user_id) for user_id in branch_user_ids]
        return {
            "$or": [
                {"manager_id": {"$in": scoped_ids}},
                {"agent_id": {"$in": scoped_ids}},
            ]
        }
    return {"_id": {"$in": []}}


def _customer_profile_payload(customer_id: str, identity: dict, user_doc: dict | None) -> dict | None:
    customer_oid = _oid(customer_id)
    if not customer_oid:
        return None
    scope = _scoped_customer_query(identity, user_doc)
    query = {"$and": [scope, {"_id": customer_oid}]} if scope else {"_id": customer_oid}
    customer = db["customers"].find_one(
        query,
        {
            "name": 1, "phone_number": 1, "image_url": 1, "location": 1,
            "address": 1, "digital_address": 1, "status": 1, "created_at": 1,
            "manager_id": 1, "agent_id": 1, "purchases": 1,
        },
    )
    if not customer:
        return None

    user_ids = [_oid(customer.get("manager_id")), _oid(customer.get("agent_id"))]
    user_map = {
        str(row["_id"]): row
        for row in db["users"].find(
            {"_id": {"$in": [value for value in user_ids if value]}},
            {"name": 1, "branch": 1, "role": 1},
        )
    }
    agent = user_map.get(str(customer.get("agent_id")), {})
    manager = user_map.get(str(customer.get("manager_id")), {})
    branch = str(agent.get("branch") or manager.get("branch") or "").strip()

    payment_query = {
        "customer_id": {"$in": [customer_oid, str(customer_oid)]},
    }
    payments = list(
        db["payments"].find(
            payment_query,
            {
                "amount": 1, "payment_type": 1, "product_index": 1, "date": 1,
                "date_dt": 1, "created_at": 1, "reference": 1, "receipt_number": 1,
                "status": 1, "agent_id": 1,
            },
        ).sort([("date_dt", -1), ("date", -1), ("created_at", -1)]).limit(1000)
    )
    payments_by_product: dict[int, float] = {}
    payment_rows = []
    for payment in payments:
        index = _safe_int(payment.get("product_index"), -1)
        amount = float(payment.get("amount") or 0)
        payment_type = str(payment.get("payment_type") or "PAYMENT").upper()
        signed_amount = -amount if payment_type == "WITHDRAWAL" else amount
        if index >= 0:
            payments_by_product[index] = payments_by_product.get(index, 0.0) + signed_amount
        event_date = _safe_dt(payment.get("date_dt")) or _safe_dt(payment.get("date")) or _safe_dt(payment.get("created_at"))
        payment_rows.append({
            "id": str(payment.get("_id") or ""),
            "productIndex": index,
            "amount": amount,
            "signedAmount": signed_amount,
            "paymentType": payment_type,
            "date": event_date.isoformat() if event_date else str(payment.get("date") or ""),
            "reference": payment.get("reference") or payment.get("receipt_number") or "",
            "status": payment.get("status") or "",
        })

    products = []
    for index, purchase in enumerate(customer.get("purchases") or []):
        if not isinstance(purchase, dict):
            continue
        product = purchase.get("product") if isinstance(purchase.get("product"), dict) else {}
        total = float(product.get("total") or purchase.get("total") or 0)
        paid = max(0.0, round(payments_by_product.get(index, 0.0), 2))
        products.append({
            "index": index,
            "name": product.get("name") or product.get("package_name") or purchase.get("product_name") or "Product",
            "image": product.get("image_url") or product.get("image") or "",
            "quantity": _safe_int(product.get("quantity") or purchase.get("quantity"), 1),
            "total": total,
            "paid": paid,
            "remaining": max(0.0, round(total - paid, 2)),
            "completion": int(min(100, round((paid / total) * 100))) if total > 0 else 0,
            "status": product.get("status") or purchase.get("status") or "active",
            "purchaseDate": str(purchase.get("purchase_date") or ""),
            "estimatedEndDate": str(purchase.get("estimated_end_date") or ""),
        })

    created_at = _safe_dt(customer.get("created_at"))
    return {
        "customer": {
            "id": str(customer["_id"]),
            "name": customer.get("name") or "Customer",
            "phone": customer.get("phone_number") or "",
            "image": customer.get("image_url") or "",
            "location": customer.get("location") or customer.get("address") or "",
            "digitalAddress": customer.get("digital_address") or "",
            "status": customer.get("status") or "",
            "createdAt": created_at.isoformat() if created_at else "",
            "branch": branch,
            "agentName": agent.get("name") or "",
            "managerName": manager.get("name") or "",
        },
        "products": products,
        "payments": payment_rows,
        "summary": {
            "productCount": len(products),
            "totalValue": round(sum(row["total"] for row in products), 2),
            "totalPaid": round(sum(row["paid"] for row in products), 2),
            "totalRemaining": round(sum(row["remaining"] for row in products), 2),
        },
    }


def _build_inventory_dashboard_payload(identity: dict, user_doc: dict | None) -> dict:
    audit_payload = _build_audit_accountability_payload(identity, user_doc)
    completion_payload = _customer_completion_rows(identity, user_doc, page=1, per_page=20)
    products = list_inventory_products()
    submitted_scope = _submitted_card_scope(identity, user_doc)
    packages = list(packages_collection.find(submitted_scope).sort([("updated_at", -1), ("created_at", -1)]).limit(500))
    branch_requests = list_branch_requests()
    if not identity.get("is_main_admin") and get_effective_inventory_role(user_doc).get("id") != "admin":
        branch = str((user_doc or {}).get("branch") or "").strip()
        branch_requests = [row for row in branch_requests if str(row.get("branch") or "").strip() == branch]
    stock_sessions = list(db["inventory_stock_taking_sessions"].find(_inventory_branch_scope(identity, user_doc)).sort([("updated_at", -1)]).limit(200))
    purchase_orders = list(db["inventory_purchase_orders"].find({}).sort([("created_at", -1)]).limit(50))
    locations = list(db["inventory_branch_locations"].find(_inventory_branch_scope(identity, user_doc)).limit(500))

    deliveries_by_branch_map: dict[str, int] = {}
    for pkg in packages:
        branch = str(pkg.get("manager_branch") or "").strip() or "Unknown"
        if str(pkg.get("status") or "").strip().lower() == "delivered":
            deliveries_by_branch_map[branch] = deliveries_by_branch_map.get(branch, 0) + 1
    deliveries_by_branch = [{"branch": branch, "deliveries": count} for branch, count in sorted(deliveries_by_branch_map.items())]

    inventory_metrics = {
        "totalStock": sum(int(item.get("totalStock") or 0) for item in products),
        "totalValue": round(sum(int(item.get("totalStock") or 0) * float(item.get("unitCost") or 0) for item in products), 2),
        "criticalCount": sum(1 for item in products if str(item.get("status") or "") == "critical"),
    }
    inventory_movement_map: dict[str, dict[str, int | str]] = {}
    for product in products:
        for entry in product.get("entries") or []:
            date_key = str(entry.get("updatedAt") or "")[:10] or str(entry.get("expiryDate") or "")[:10]
            if not date_key:
                continue
            bucket = inventory_movement_map.setdefault(
                date_key,
                {"date": date_key[5:] if len(date_key) >= 10 else date_key, "inbound": 0, "outbound": 0, "adjustments": 0},
            )
            qty = int(entry.get("quantity") or 0)
            if qty >= 0:
                bucket["inbound"] = int(bucket["inbound"]) + qty
            else:
                bucket["outbound"] = int(bucket["outbound"]) + abs(qty)
            source = str(entry.get("source") or "").strip().lower()
            if "stock_taking" in source or "adjust" in source:
                bucket["adjustments"] = int(bucket["adjustments"]) + qty
    movement_rows = list(sorted(inventory_movement_map.values(), key=lambda row: str(row["date"])))[-12:]
    outflow_this_week = [{"day": row.get("date") or "-", "units": int(row.get("outbound") or 0)} for row in movement_rows[-7:]]

    lost_stock_map: dict[str, int] = {}
    for session in stock_sessions:
        updated_at = _safe_dt(session.get("updated_at")) or _safe_dt(session.get("created_at"))
        if not updated_at:
            continue
        if (datetime.utcnow() - updated_at).days > 7:
            continue
        day_key = updated_at.strftime("%a")
        for item in session.get("items") or []:
            variance = int(item.get("variance") or 0)
            if variance < 0:
                lost_stock_map[day_key] = lost_stock_map.get(day_key, 0) + abs(variance)
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lost_stock_this_week = [{"day": day, "units": lost_stock_map.get(day, 0)} for day in day_order]

    completion_trend: list[dict[str, int | str]] = []

    branch_inventory_totals: dict[str, int] = {}
    for location in locations:
        branch = str(location.get("branch") or "").strip() or "Unknown"
        branch_inventory_totals[branch] = branch_inventory_totals.get(branch, 0) + int(location.get("stock_units") or 0)
    package_branch_totals: dict[str, dict] = {}
    for pkg in packages:
        branch = str(pkg.get("manager_branch") or "").strip() or "Unknown"
        bucket = package_branch_totals.setdefault(branch, {"total": 0, "delivered": 0})
        bucket["total"] += 1
        if str(pkg.get("status") or "").strip().lower() == "delivered":
            bucket["delivered"] += 1
    branch_performance = []
    for branch in sorted({*branch_inventory_totals.keys(), *package_branch_totals.keys()}):
        totals = package_branch_totals.get(branch, {"total": 0, "delivered": 0})
        branch_performance.append(
            {
                "branch": branch,
                "fulfillment": round((totals["delivered"] / max(1, totals["total"])) * 100),
                "inventory": branch_inventory_totals.get(branch, 0),
            }
        )

    total_stock = int(inventory_metrics.get("totalStock") or 0)
    pending_units = sum(int(pkg.get("qty") or 0) for pkg in packages if str(pkg.get("status") or "").strip().lower() != "delivered")
    low_stock_items = int(inventory_metrics.get("criticalCount") or 0)
    inventory_health = [
        {"status": "Available", "count": max(0, total_stock - pending_units), "value": max(0, float(inventory_metrics.get("totalValue") or 0) * 0.72), "color": "bg-green-500"},
        {"status": "Reserved", "count": pending_units, "value": max(0, float(inventory_metrics.get("totalValue") or 0) * 0.18), "color": "bg-blue-500"},
        {"status": "Shortage", "count": low_stock_items, "value": max(0, float(inventory_metrics.get("totalValue") or 0) * 0.10), "color": "bg-red-500"},
    ]
    stock_shortage = sorted(
        [
            {
                "item": item.get("name") or "Product",
                "currentStock": int(item.get("totalStock") or 0),
                "forecast": int(item.get("forecastDemand") or 0),
                "riskScore": min(
                    100,
                    max(
                        0,
                        int(
                            (
                                max(
                                    0,
                                    int(item.get("reorderPoint") or 0) - int(item.get("totalStock") or 0),
                                )
                                / max(1, int(item.get("reorderPoint") or 0) or 1)
                            )
                            * 100
                        ),
                    ),
                ),
                "daysUntilStockout": 0,
            }
            for item in products
            if int(item.get("reorderPoint") or 0) > 0 and int(item.get("totalStock") or 0) <= int(item.get("reorderPoint") or 0)
        ],
        key=lambda row: (int(row.get("riskScore") or 0), -int(row.get("currentStock") or 0)),
        reverse=True,
    )[:6]

    forecast_alerts = [
        {
            "level": "70-80%",
            "customers": int((completion_payload.get("counts") or {}).get("70plus") or 0),
            "estimatedCompletion": "2-3 months",
            "requiredStock": int((completion_payload.get("counts") or {}).get("70plus") or 0),
            "status": "warning",
        },
        {
            "level": "80-90%",
            "customers": int((completion_payload.get("counts") or {}).get("80plus") or 0),
            "estimatedCompletion": "1-2 months",
            "requiredStock": int((completion_payload.get("counts") or {}).get("80plus") or 0),
            "status": "attention",
        },
        {
            "level": "90-100%",
            "customers": int((completion_payload.get("counts") or {}).get("90plus") or 0),
            "estimatedCompletion": "< 1 month",
            "requiredStock": int((completion_payload.get("counts") or {}).get("90plus") or 0),
            "status": "critical",
        },
    ]

    recent_activities: list[dict] = []
    for pkg in packages[:4]:
        recent_activities.append(
            {
                "id": f"pkg-{pkg.get('_id')}",
                "type": "delivery",
                "message": f"{pkg.get('customer_name') or 'Customer'} package is {str(pkg.get('status') or 'pending').replace('_', ' ')}",
                "time": (_safe_dt(pkg.get("updated_at")) or _safe_dt(pkg.get("created_at")) or datetime.utcnow()).isoformat(),
                "icon": "delivery",
                "color": "text-green-600" if str(pkg.get("status") or "").strip().lower() == "delivered" else "text-blue-600",
            }
        )
    for req in branch_requests[:3]:
        recent_activities.append(
            {
                "id": f"req-{req.get('id')}",
                "type": "request",
                "message": f"{req.get('branch') or 'Branch'} requested {req.get('totalQuantity') or 0} inventory units",
                "time": req.get("updatedAt") or req.get("requestDate") or datetime.utcnow().isoformat(),
                "icon": "request",
                "color": "text-blue-600",
            }
        )
    for session in stock_sessions[:3]:
        recent_activities.append(
            {
                "id": f"stk-{session.get('_id')}",
                "type": "audit",
                "message": f"Stock taking {session.get('session_number') or ''} is {session.get('status') or 'draft'}",
                "time": (_safe_dt(session.get("updated_at")) or _safe_dt(session.get("created_at")) or datetime.utcnow()).isoformat(),
                "icon": "alert",
                "color": "text-orange-600",
            }
        )
    for po in purchase_orders[:2]:
        recent_activities.append(
            {
                "id": f"po-{po.get('_id')}",
                "type": "purchase",
                "message": f"Purchase order {po.get('po_number') or po.get('ref_no') or ''} for {po.get('supplier_name') or 'supplier'} is {po.get('status') or 'open'}",
                "time": (_safe_dt(po.get("created_at")) or datetime.utcnow()).isoformat(),
                "icon": "purchase",
                "color": "text-indigo-600",
            }
        )
    recent_activities.sort(key=lambda row: row.get("time") or "", reverse=True)

    metrics = {
        "customers90Plus": int((completion_payload.get("counts") or {}).get("90plus") or 0),
        "completedCustomers": int((completion_payload.get("counts") or {}).get("completed") or 0),
        "inventoryValue": float(inventory_metrics.get("totalValue") or 0),
        "lowStockItems": low_stock_items,
        "pendingBranchRequests": sum(1 for req in branch_requests if str(req.get("status") or "").strip().lower() in {"pending", "open"}),
        "undeliveredCustomers": sum(1 for pkg in packages if str(pkg.get("status") or "").strip().lower() != "delivered"),
        "auditLossValue": float((audit_payload.get("metrics") or {}).get("totalLossValue") or 0),
        "openInvestigations": int((audit_payload.get("metrics") or {}).get("openInvestigations") or 0),
    }

    return {
        "metrics": metrics,
        "deliveriesByBranch": deliveries_by_branch,
        "outflowThisWeek": outflow_this_week,
        "lostStockThisWeek": lost_stock_this_week,
        "completionTrend": completion_trend,
        "inventoryMovement": movement_rows[-4:],
        "stockShortage": stock_shortage,
        "branchPerformance": branch_performance,
        "inventoryHealth": inventory_health,
        "forecastAlerts": forecast_alerts,
        "recentActivities": recent_activities[:8],
    }


def _serialize_submitted_card(doc: dict, image_lookup: dict[tuple[str, str, str], str] | None = None) -> dict:
    product = doc.get("product") or {}
    created_at = _safe_dt(doc.get("created_at"))
    updated_at = _safe_dt(doc.get("updated_at")) or created_at
    status = str(doc.get("status") or "pending").strip().lower() or "pending"
    if status not in SUBMITTED_CARD_STATUS_FLOW:
        status = "pending"

    quantity = _safe_int(doc.get("qty"), _safe_int(product.get("quantity"), 0))
    product_total = _safe_float(doc.get("product_total"), _safe_float(product.get("total"), 0.0))
    amount_paid = _safe_float(doc.get("total_paid_selected_product"), 0.0)
    amount_left = max(0.0, round(product_total - amount_paid, 2))
    days_waiting = 0
    if created_at:
        days_waiting = max((datetime.utcnow() - created_at).days, 0)

    next_status = None
    if status != "delivered":
        next_index = SUBMITTED_CARD_STATUS_FLOW.index(status) + 1
        if next_index < len(SUBMITTED_CARD_STATUS_FLOW):
            next_status = SUBMITTED_CARD_STATUS_FLOW[next_index]

    history = []
    for event in doc.get("status_history") or []:
        event_status = str(event.get("status") or event.get("to") or "").strip().lower()
        event_time = _safe_dt(event.get("timestamp") or event.get("at"))
        history.append(
            {
                "status": event_status,
                "label": _status_label(event_status),
                "actorName": event.get("actor_name") or event.get("by") or "",
                "actorRole": event.get("actor_role") or event.get("role") or "",
                "timestamp": event_time.isoformat() if event_time else "",
                "notes": event.get("notes") or "",
            }
        )
    if not history and created_at:
        history.append(
            {
                "status": "pending",
                "label": "Submitted",
                "actorName": doc.get("agent_name") or "",
                "actorRole": doc.get("by_role") or "agent",
                "timestamp": created_at.isoformat(),
                "notes": "Submitted from customer profile after full payment.",
            }
        )

    return {
        "id": str(doc.get("_id") or ""),
        "customerId": str(doc.get("customer_id") or ""),
        "customerName": doc.get("customer_name") or "",
        "customerPhone": doc.get("customer_phone") or "",
        "productIndex": doc.get("product_index"),
        "productName": product.get("name") or product.get("package_name") or "Product",
        "productImage": _resolve_submitted_card_image(doc, image_lookup),
        "purchaseType": doc.get("purchase_type") or "",
        "quantity": quantity,
        "productTotal": product_total,
        "amountPaid": amount_paid,
        "amountLeft": amount_left,
        "branch": doc.get("manager_branch") or "",
        "agentName": doc.get("agent_name") or "",
        "status": status,
        "statusLabel": _status_label(status),
        "nextStatus": next_status,
        "nextStatusLabel": _status_label(next_status) if next_status else "",
        "submittedAt": created_at.isoformat() if created_at else "",
        "updatedAt": updated_at.isoformat() if updated_at else "",
        "daysWaiting": days_waiting,
        "source": doc.get("source") or "",
        "history": history,
    }


def _submitted_card_counts(scope: dict) -> dict:
    return {
        "total": packages_collection.count_documents(scope),
        "open": packages_collection.count_documents({**scope, "status": {"$ne": "delivered"}}),
        "pending": packages_collection.count_documents({**scope, "status": "pending"}),
        "packaging": packages_collection.count_documents({**scope, "status": "packaging"}),
        "delivering": packages_collection.count_documents({**scope, "status": "delivering"}),
        "delivered": packages_collection.count_documents({**scope, "status": "delivered"}),
    }


def _build_product_detail_pdf(product: dict, tab: str) -> bytes:
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
        "ProductTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=23,
        textColor=colors.HexColor("#1E1B4B"),
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "ProductSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "ProductSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#312E81"),
        spaceAfter=5,
        spaceBefore=8,
    )
    label_style = ParagraphStyle(
        "ProductLabel",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )
    value_style = ParagraphStyle(
        "ProductValue",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )

    story = [
        Paragraph(f"{product.get('name') or 'Inventory Product'}", title_style),
        Paragraph(
            f"{product.get('sku') or '-'} | {product.get('category') or '-'} | Tab: {tab.title()}<br/>"
            f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle_style,
        ),
    ]

    if tab == "overview":
        summary_rows = [
            [
                Paragraph("Total Stock", label_style),
                Paragraph("Available", label_style),
                Paragraph("Locations", label_style),
                Paragraph("Status", label_style),
            ],
            [
                Paragraph(str(product.get("totalStock") or 0), value_style),
                Paragraph(str(product.get("available") or 0), value_style),
                Paragraph(str(len(product.get("locations") or [])), value_style),
                Paragraph((product.get("status") or "-").title(), value_style),
            ],
            [
                Paragraph("Unit Cost", label_style),
                Paragraph("Reorder Point", label_style),
                Paragraph("Reorder Quantity", label_style),
                Paragraph("Safe Available", label_style),
            ],
            [
                Paragraph(f"GHS {float(product.get('unitCost') or 0):,.2f}", value_style),
                Paragraph(str(product.get("reorderPoint") or 0), value_style),
                Paragraph(str(product.get("reorderQuantity") or 0), value_style),
                Paragraph(str(product.get("safeAvailable") or 0), value_style),
            ],
        ]
        summary_table = Table(summary_rows, colWidths=[42 * mm, 42 * mm, 42 * mm, 42 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                    ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 8))

        details_rows = [
            [Paragraph("Brand", label_style), Paragraph(product.get("brand") or "-", value_style)],
            [Paragraph("Last Restocked", label_style), Paragraph(product.get("lastRestocked") or "-", value_style)],
            [Paragraph("Created On", label_style), Paragraph(product.get("createdAt") or "-", value_style)],
            [Paragraph("Description", label_style), Paragraph(product.get("description") or "-", value_style)],
        ]
        detail_table = Table(details_rows, colWidths=[36 * mm, 132 * mm])
        detail_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(Paragraph("Overview", section_style))
        story.append(detail_table)

    elif tab == "locations":
        story.append(Paragraph("Stock by Location", section_style))
        location_rows = [[
            Paragraph("Branch", label_style),
            Paragraph("Location", label_style),
            Paragraph("Type", label_style),
            Paragraph("Product Stock", label_style),
            Paragraph("Location Total", label_style),
            Paragraph("Capacity", label_style),
            Paragraph("Utilization", label_style),
        ]]
        for location in product.get("locations") or []:
            location_rows.append(
                [
                    Paragraph(location.get("branch") or "-", value_style),
                    Paragraph(
                        f"<b>{location.get('locationName') or '-'}</b><br/><font color='#64748B'>{location.get('locationCode') or '-'}</font>",
                        value_style,
                    ),
                    Paragraph(location.get("type") or "-", value_style),
                    Paragraph(str(location.get("productStock") or 0), value_style),
                    Paragraph(str(location.get("locationTotalStock") or 0), value_style),
                    Paragraph(str(location.get("capacity") or 0), value_style),
                    Paragraph(f"{int(location.get('utilizationPct') or 0)}%", value_style),
                ]
            )
        if len(location_rows) == 1:
            location_rows.append(
                [
                    Paragraph("No location allocations found.", value_style),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
        location_table = Table(
            location_rows,
            colWidths=[28 * mm, 48 * mm, 25 * mm, 22 * mm, 22 * mm, 18 * mm, 20 * mm],
            repeatRows=1,
        )
        styles_list = [
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
        for row_idx in range(1, len(location_rows)):
            if row_idx % 2 == 1:
                styles_list.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#F8FAFC")))
        location_table.setStyle(TableStyle(styles_list))
        story.append(location_table)
    elif tab == "product-cards":
        story.append(Paragraph("Product Cards", section_style))
        product_cards = product.get("productCards") or {}
        summary = product_cards.get("summary") or {}
        summary_rows = [
            [
                Paragraph("Linked Cards", label_style),
                Paragraph("Manager Copies", label_style),
                Paragraph("Coverage", label_style),
                Paragraph("Customers", label_style),
            ],
            [
                Paragraph(str(summary.get("cardCount") or 0), value_style),
                Paragraph(str(summary.get("managerCopyCount") or 0), value_style),
                Paragraph(f"{int(summary.get('coveragePct') or 0)}%", value_style),
                Paragraph(str(summary.get("customerCount") or 0), value_style),
            ],
        ]
        summary_table = Table(summary_rows, colWidths=[42 * mm, 42 * mm, 42 * mm, 42 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 8))

        card_rows = [[
            Paragraph("Card", label_style),
            Paragraph("Managers", label_style),
            Paragraph("Units Required", label_style),
            Paragraph("Coverage", label_style),
            Paragraph("Customers", label_style),
            Paragraph("Sales Value", label_style),
        ]]
        for card in product_cards.get("cards") or []:
            card_rows.append(
                [
                    Paragraph(card.get("name") or "-", value_style),
                    Paragraph(str(card.get("managerCount") or 0), value_style),
                    Paragraph(str(card.get("requiredUnits") or 0), value_style),
                    Paragraph(f"{int(card.get('coveragePct') or 0)}%", value_style),
                    Paragraph(str(card.get("customers") or 0), value_style),
                    Paragraph(f"GHS {float(card.get('salesValue') or 0):,.2f}", value_style),
                ]
            )
        if len(card_rows) == 1:
            card_rows.append([Paragraph("This inventory item is not used inside any product card yet.", value_style), "", "", "", "", ""])
        cards_table = Table(card_rows, colWidths=[58 * mm, 24 * mm, 28 * mm, 24 * mm, 24 * mm, 34 * mm], repeatRows=1)
        cards_table.setStyle(
            TableStyle(
                [
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
            )
        )
        story.append(cards_table)
    elif tab == "forecast":
        story.append(Paragraph("Forecast Demand", section_style))
        forecast = product.get("forecast") or {}
        summary = forecast.get("summary") or {}
        summary_rows = [
            [
                Paragraph("Projected 30 Days", label_style),
                Paragraph("Available Stock", label_style),
                Paragraph("Coverage Days", label_style),
                Paragraph("Risk Level", label_style),
            ],
            [
                Paragraph(str(summary.get("projected30DaysUnits") or 0), value_style),
                Paragraph(str(summary.get("availableStock") or 0), value_style),
                Paragraph(str(summary.get("coverageDays") if summary.get("coverageDays") is not None else "-"), value_style),
                Paragraph(str(summary.get("riskLevel") or "-").replace("-", " ").title(), value_style),
            ],
            [
                Paragraph("Last 7 Days", label_style),
                Paragraph("Last 30 Days", label_style),
                Paragraph("Last 90 Days", label_style),
                Paragraph("Recommended Reorder", label_style),
            ],
            [
                Paragraph(str(summary.get("last7DaysUnits") or 0), value_style),
                Paragraph(str(summary.get("last30DaysUnits") or 0), value_style),
                Paragraph(str(summary.get("last90DaysUnits") or 0), value_style),
                Paragraph(str(summary.get("recommendedReorderUnits") or 0), value_style),
            ],
        ]
        summary_table = Table(summary_rows, colWidths=[42 * mm, 42 * mm, 42 * mm, 42 * mm])
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
                    ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 8))
        story.append(Paragraph(summary.get("basis") or "Forecast uses linked product-card customer purchases.", subtitle_style))

        demand_rows = [[
            Paragraph("Card", label_style),
            Paragraph("7 Days", label_style),
            Paragraph("30 Days", label_style),
            Paragraph("90 Days", label_style),
            Paragraph("Share", label_style),
            Paragraph("Last Purchase", label_style),
        ]]
        for card in forecast.get("byCard") or []:
            demand_rows.append(
                [
                    Paragraph(card.get("cardName") or "-", value_style),
                    Paragraph(str(card.get("last7DaysUnits") or 0), value_style),
                    Paragraph(str(card.get("last30DaysUnits") or 0), value_style),
                    Paragraph(str(card.get("last90DaysUnits") or 0), value_style),
                    Paragraph(f"{int(card.get('sharePct') or 0)}%", value_style),
                    Paragraph(card.get("lastPurchaseDate") or "-", value_style),
                ]
            )
        if len(demand_rows) == 1:
            demand_rows.append([Paragraph("No customer demand has been recorded for linked cards yet.", value_style), "", "", "", "", ""])
        demand_table = Table(demand_rows, colWidths=[58 * mm, 24 * mm, 24 * mm, 24 * mm, 22 * mm, 36 * mm], repeatRows=1)
        demand_table.setStyle(
            TableStyle(
                [
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
            )
        )
        story.append(demand_table)
    else:
        story.append(Paragraph("This tab is not yet connected to the live export service.", value_style))

    doc.build(story)
    return buffer.getvalue()


def _build_inventory_export_pdf(
    products: list[dict],
    distribution: dict,
    stock_taking_dashboard: dict,
    filters: dict[str, str],
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InventoryExportTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#111827"),
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "InventoryExportSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "InventoryExportSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=8,
        spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "InventoryExportBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )
    label_style = ParagraphStyle(
        "InventoryExportLabel",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )

    total_items = len(products)
    total_stock = sum(int(item.get("totalStock") or 0) for item in products)
    available_stock = sum(int(item.get("available") or 0) for item in products)
    reserved_stock = sum(int(item.get("reserved") or 0) for item in products)
    forecast_demand = sum(int(item.get("forecastDemand") or 0) for item in products)
    low_stock_items = sum(1 for item in products if (item.get("status") or "") == "warning")
    critical_items = sum(1 for item in products if (item.get("status") or "") == "critical")
    total_inventory_cost = sum(float(item.get("unitCost") or 0) * int(item.get("totalStock") or 0) for item in products)

    filter_lines = []
    if filters.get("search"):
        filter_lines.append(f"Search: {filters['search']}")
    if filters.get("category") and filters["category"] != "all":
        filter_lines.append(f"Category: {filters['category']}")
    if filters.get("brand") and filters["brand"] != "all":
        filter_lines.append(f"Brand: {filters['brand']}")
    filter_text = " | ".join(filter_lines) if filter_lines else "Scope: all inventory products"

    story = [
        Paragraph("Inventory Physical Stock & Availability Report", title_style),
        Paragraph(
            f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}<br/>{filter_text}",
            subtitle_style,
        ),
    ]

    metric_rows = [
        [
            Paragraph("Total Items", label_style),
            Paragraph("Total Stock", label_style),
            Paragraph("Available Stock", label_style),
            Paragraph("Reserved Stock", label_style),
        ],
        [
            Paragraph(str(total_items), body_style),
            Paragraph(f"{total_stock:,}", body_style),
            Paragraph(f"{available_stock:,}", body_style),
            Paragraph(f"{reserved_stock:,}", body_style),
        ],
        [
            Paragraph("Forecast Demand", label_style),
            Paragraph("Low Stock Items", label_style),
            Paragraph("Critical Items", label_style),
            Paragraph("Inventory Cost Base", label_style),
        ],
        [
            Paragraph(f"{forecast_demand:,}", body_style),
            Paragraph(str(low_stock_items), body_style),
            Paragraph(str(critical_items), body_style),
            Paragraph(f"GHS {total_inventory_cost:,.2f}", body_style),
        ],
    ]
    metric_table = Table(metric_rows, colWidths=[45 * mm, 45 * mm, 45 * mm, 45 * mm])
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E0E7FF")),
                ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#EFF6FF")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(metric_table)
    story.append(Spacer(1, 8))

    stock_taking_metrics = stock_taking_dashboard.get("metrics") or {}
    overview_rows = [
        [Paragraph("Operational Overview", section_style), ""],
        [
            Paragraph("Branches / Warehouses", label_style),
            Paragraph(
                f"{len(distribution.get('branches') or [])} branches | "
                f"{sum(len(rows) for rows in (distribution.get('locations') or {}).values())} location records",
                body_style,
            ),
        ],
        [
            Paragraph("Stock Taking Exposure", label_style),
            Paragraph(
                f"Pending approvals: {int(stock_taking_metrics.get('pendingApproval') or 0)} | "
                f"High variance alerts: {int(stock_taking_metrics.get('highVarianceAlerts') or 0)} | "
                f"Shrinkage rate: {float(stock_taking_metrics.get('shrinkageRate') or 0):.1f}%",
                body_style,
            ),
        ],
        [
            Paragraph("Availability Summary", label_style),
            Paragraph(
                f"Availability ratio: {((available_stock / total_stock) * 100 if total_stock else 0):.1f}% | "
                f"Critical product ratio: {((critical_items / total_items) * 100 if total_items else 0):.1f}%",
                body_style,
            ),
        ],
    ]
    overview_table = Table(overview_rows, colWidths=[45 * mm, 135 * mm])
    overview_table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (-1, 0)),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 1), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(overview_table)
    story.append(Spacer(1, 8))

    branch_rows = [[
        Paragraph("Branch", label_style),
        Paragraph("Manager", label_style),
        Paragraph("Warehouses", label_style),
        Paragraph("Reported Stock Units", label_style),
        Paragraph("Status", label_style),
    ]]
    for branch in distribution.get("branches") or []:
        branch_rows.append(
            [
                Paragraph(branch.get("name") or "-", body_style),
                Paragraph(branch.get("manager") or "-", body_style),
                Paragraph(str(branch.get("totalWarehouses") or 0), body_style),
                Paragraph(f"{int(branch.get('totalStockUnits') or 0):,}", body_style),
                Paragraph((branch.get("status") or "-").title(), body_style),
            ]
        )
    if len(branch_rows) > 1:
        branch_table = Table(branch_rows, colWidths=[50 * mm, 45 * mm, 25 * mm, 40 * mm, 25 * mm], repeatRows=1)
        branch_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D4ED8")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(Paragraph("Branch Coverage", section_style))
        story.append(branch_table)
        story.append(Spacer(1, 8))

    priority_rows = [[
        Paragraph("Priority Item", label_style),
        Paragraph("SKU", label_style),
        Paragraph("Category", label_style),
        Paragraph("Stock", label_style),
        Paragraph("Reorder Point", label_style),
        Paragraph("Status", label_style),
    ]]
    for item in [row for row in products if (row.get("status") or "") in {"critical", "warning"}][:12]:
        priority_rows.append(
            [
                Paragraph(item.get("name") or "-", body_style),
                Paragraph(item.get("sku") or "-", body_style),
                Paragraph(item.get("category") or "-", body_style),
                Paragraph(str(item.get("totalStock") or 0), body_style),
                Paragraph(str(item.get("reorderPoint") or 0), body_style),
                Paragraph((item.get("status") or "-").title(), body_style),
            ]
        )
    if len(priority_rows) > 1:
        priority_table = Table(priority_rows, colWidths=[58 * mm, 28 * mm, 35 * mm, 18 * mm, 25 * mm, 26 * mm], repeatRows=1)
        priority_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F97316")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(Paragraph("Low Stock & Critical Focus", section_style))
        story.append(priority_table)
        story.append(Spacer(1, 8))

    product_rows = [[
        Paragraph("Product", label_style),
        Paragraph("SKU", label_style),
        Paragraph("Category / Brand", label_style),
        Paragraph("Available", label_style),
        Paragraph("Reserved", label_style),
        Paragraph("Forecast", label_style),
        Paragraph("Unit Cost", label_style),
        Paragraph("Status", label_style),
    ]]
    for item in products:
        product_rows.append(
            [
                Paragraph(item.get("name") or "-", body_style),
                Paragraph(item.get("sku") or "-", body_style),
                Paragraph(f"{item.get('category') or '-'}<br/>{item.get('brand') or '-'}", body_style),
                Paragraph(str(item.get("available") or 0), body_style),
                Paragraph(str(item.get("reserved") or 0), body_style),
                Paragraph(str(item.get("forecastDemand") or 0), body_style),
                Paragraph(f"GHS {float(item.get('unitCost') or 0):,.2f}", body_style),
                Paragraph((item.get("status") or "-").title(), body_style),
            ]
        )
    if len(product_rows) == 1:
        product_rows.append([Paragraph("No inventory products matched the selected filters.", body_style), "", "", "", "", "", "", ""])
    product_table = Table(
        product_rows,
        colWidths=[42 * mm, 22 * mm, 36 * mm, 18 * mm, 18 * mm, 18 * mm, 22 * mm, 20 * mm],
        repeatRows=1,
    )
    product_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for row_idx in range(1, len(product_rows)):
        if row_idx % 2 == 1:
            product_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#F8FAFC")))
    product_table.setStyle(TableStyle(product_styles))
    story.append(Paragraph("Detailed Stock Register", section_style))
    story.append(product_table)

    doc.build(story)
    return buffer.getvalue()


@inventory_api_bp.route("/dashboard")
@role_required("inventory")
def dashboard():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    try:
        return jsonify({"ok": True, **_build_inventory_dashboard_payload(identity, user_doc)})
    except (NetworkTimeout, ServerSelectionTimeoutError):
        return jsonify({
            "ok": False,
            "error": "The database took too long to return dashboard data. Please retry.",
            "code": "database_timeout",
        }), 503


@inventory_api_bp.route("/branch-requests", methods=["GET"])
@role_required("inventory")
def branch_requests():
    return jsonify({"ok": True, "requests": list_branch_requests()})


@inventory_api_bp.route("/branch-requests/<request_id>/approve", methods=["POST"])
@role_required("inventory")
def approve_request(request_id: str):
    payload = request.get_json(silent=True) or {}
    line_sources = payload.get("lineSources") or {}
    line_approvals = payload.get("lineApprovals") or {}
    try:
        result = approve_branch_request(
            request_id,
            get_current_identity(),
            line_sources if isinstance(line_sources, dict) else {},
            line_approvals if isinstance(line_approvals, dict) else {},
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@inventory_api_bp.route("/branch-requests/<request_id>", methods=["DELETE"])
@role_required("inventory")
def remove_branch_request(request_id: str):
    try:
        result = delete_branch_request(request_id, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@inventory_api_bp.route("/branch-requests/<request_id>/lines/<line_id>", methods=["DELETE"])
@role_required("inventory")
def remove_branch_request_line(request_id: str, line_id: str):
    try:
        result = delete_branch_request_line(request_id, line_id, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@inventory_api_bp.route("/session")
@role_required("inventory")
def session_info():
    ident = get_current_identity()
    user_doc = get_inventory_user_doc(ident.get("user_id"))
    if ident.get("is_main_admin") or ident.get("role") == "executive":
        effective_role = get_inventory_role_map().get("admin") or {"id": "admin", "name": "Main Admin"}
    else:
        effective_role = get_effective_inventory_role(user_doc)
    return jsonify({
        "ok": True,
        "user": {
            "id": ident.get("user_id"),
            "name": ident.get("name"),
            "username": ident.get("username"),
            "role": ident.get("role"),
            "is_main_admin": bool(ident.get("is_main_admin")),
            "inventory_role_id": effective_role.get("id"),
            "inventory_role_name": effective_role.get("name"),
        },
        "effective_role_id": effective_role.get("id"),
        "effective_role_name": effective_role.get("name"),
        "roles": get_inventory_roles(),
    })


@inventory_api_bp.route("/products", methods=["GET"])
@role_required("inventory")
def inventory_products():
    distribution = get_inventory_distribution_payload()
    if request.args.get('paginated') == '1':
        from .products_store import inventory_product_page
        return jsonify(ok=True, **inventory_product_page(request.args),
                       branches=[branch.get('name') for branch in distribution.get('branches') or [] if branch.get('name')],
                       locations=distribution.get('locations') or {})
    return jsonify(
        {
            "ok": True,
            "products": list_inventory_products(),
            "branches": [branch.get("name") or "" for branch in distribution.get("branches") or [] if branch.get("name")],
            "locations": distribution.get("locations") or {},
        }
    )


@inventory_api_bp.route("/products/export.pdf", methods=["GET"])
@role_required("inventory")
def inventory_products_export():
    search = (request.args.get("search") or "").strip().lower()
    category = (request.args.get("category") or "all").strip()
    brand = (request.args.get("brand") or "all").strip()

    products = list_inventory_products()
    filtered_products = []
    for item in products:
        matches_search = (
            not search
            or search in str(item.get("name") or "").lower()
            or search in str(item.get("sku") or "").lower()
            or search in str(item.get("category") or "").lower()
            or search in str(item.get("brand") or "").lower()
        )
        matches_category = category == "all" or (item.get("category") or "") == category
        matches_brand = brand == "all" or (item.get("brand") or "") == brand
        if matches_search and matches_category and matches_brand:
            filtered_products.append(item)

    pdf_bytes = _build_inventory_export_pdf(
        filtered_products,
        get_inventory_distribution_payload(),
        get_stock_taking_dashboard(),
        {
            "search": request.args.get("search") or "",
            "category": category,
            "brand": brand,
        },
    )
    filename = f"inventory_stock_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@inventory_api_bp.route("/products", methods=["POST"])
@role_required("inventory")
def create_product():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}

    name = (payload.get("name") or "").strip()
    category = (payload.get("category") or "").strip()
    image_url = (payload.get("imageUrl") or "").strip()
    stock_assignments = payload.get("stockAssignments") or []
    quantity = int(payload.get("quantity") or 0)
    cost_price = float(payload.get("costPrice") or 0)
    selling_price = float(payload.get("sellingPrice") or 0)

    if not name or not category:
        return jsonify({"ok": False, "error": "Product name and category are required."}), 400
    if not image_url:
        return jsonify({"ok": False, "error": "Product image is required."}), 400
    if not isinstance(stock_assignments, list) or not stock_assignments:
        return jsonify({"ok": False, "error": "Select at least one stock location."}), 400
    if quantity < 0:
        return jsonify({"ok": False, "error": "Quantity cannot be less than 0."}), 400
    if cost_price <= 0 or selling_price <= 0:
        return jsonify({"ok": False, "error": "Cost and selling prices must be greater than 0."}), 400

    try:
        product = create_inventory_product(payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "product": product}), 201


@inventory_api_bp.route("/products/<product_id>", methods=["PATCH"])
@role_required("inventory")
def update_product(product_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}

    try:
        product = update_inventory_product(product_id, payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "product": product})


@inventory_api_bp.route("/products/<product_id>", methods=["DELETE"])
@role_required("inventory")
def delete_product(product_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    confirm_name = str(payload.get("confirmName") or "")

    try:
        delete_inventory_product(product_id, confirm_name, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True})


@inventory_api_bp.route("/stock-update/bootstrap", methods=["GET"])
@role_required("inventory")
def stock_update_bootstrap():
    branch = (request.args.get("branch") or "").strip()
    location_id = (request.args.get("locationId") or "").strip()
    products = get_inventory_products_for_location(branch, location_id) if location_id else []
    return jsonify(
        {
            "ok": True,
            "branches": get_inventory_distribution_payload().get("branches") or [],
            "locations": get_inventory_distribution_payload().get("locations") or {},
            "products": products,
        }
    )


@inventory_api_bp.route("/stock-update-sessions", methods=["POST"])
@role_required("inventory")
def create_stock_update():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}

    try:
        session_data = create_stock_update_session(payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "session": session_data}), 201


@inventory_api_bp.route("/stock-taking/bootstrap", methods=["GET"])
@role_required("inventory")
def stock_taking_bootstrap():
    payload = get_inventory_distribution_payload()
    dashboard = get_stock_taking_dashboard()
    return jsonify(
        {
            "ok": True,
            "branches": [branch.get("name") or "" for branch in payload.get("branches") or [] if branch.get("name")],
            "locations": payload.get("locations") or {},
            "sessions": dashboard.get("sessions") or [],
            "metrics": dashboard.get("metrics") or {},
            "varianceTrend": dashboard.get("varianceTrend") or [],
            "reasonBreakdown": dashboard.get("reasonBreakdown") or [],
            "alerts": dashboard.get("alerts") or [],
        }
    )


@inventory_api_bp.route("/audit/bootstrap", methods=["GET"])
@role_required("inventory")
def audit_bootstrap():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    return jsonify({"ok": True, **_build_audit_accountability_payload(identity, user_doc)})


@inventory_api_bp.route("/audit/stock-deductions/preview", methods=["POST"])
@role_required("inventory")
def stock_deductions_preview():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        result = preview_stock_deductions(payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    except (NetworkTimeout, ServerSelectionTimeoutError):
        return jsonify({
            "ok": False,
            "error": "The database took too long to load stock deductions. Please retry.",
            "code": "database_timeout",
        }), 503
    return jsonify({"ok": True, **result})


@inventory_api_bp.route("/audit/stock-deductions/<package_id>", methods=["GET"])
@role_required("inventory")
def stock_deductions_detail(package_id: str):
    try:
        result = deduction_detail(package_id)
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": True, **result})


@inventory_api_bp.route("/audit/stock-deductions/<package_id>/freeze-recipe", methods=["POST"])
@role_required("inventory")
def stock_deductions_freeze_recipe(package_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        recipe = freeze_package_recipe(package_id, payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    return jsonify({"ok": True, "recipe": recipe})


@inventory_api_bp.route("/audit/stock-deductions/confirm", methods=["POST"])
@role_required("inventory")
def stock_deductions_confirm():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        result = confirm_stock_deductions(payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    status = 207 if result.get("blocked") else 200
    return jsonify({"ok": True, **result}), status


@inventory_api_bp.route("/audit/stock-deductions/export.csv", methods=["POST"])
@role_required("inventory")
def stock_deductions_export_csv():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        csv_bytes = export_stock_deductions_csv(payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    log_activity("inventory_stock_deduction_export", "Exported stock deduction CSV", "stock_deduction_report", meta={"from": payload.get("fromDate"), "to": payload.get("toDate")})
    return send_file(
        BytesIO(csv_bytes),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"stock_deductions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
    )


@inventory_api_bp.route("/audit/stock-deductions/export.xlsx", methods=["POST"])
@role_required("inventory")
def stock_deductions_export_xlsx():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        content = export_stock_deductions_xlsx(payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    log_activity("inventory_stock_deduction_export", "Exported stock deduction Excel report", "stock_deduction_report", meta={"from": payload.get("fromDate"), "to": payload.get("toDate")})
    return send_file(BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=f"stock_deductions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx")


@inventory_api_bp.route("/audit/stock-deductions/export.pdf", methods=["POST"])
@role_required("inventory")
def stock_deductions_export_pdf():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        content = export_stock_deductions_pdf(payload, get_current_identity())
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    log_activity("inventory_stock_deduction_export", "Exported stock deduction PDF", "stock_deduction_report", meta={"from": payload.get("fromDate"), "to": payload.get("toDate")})
    return send_file(BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=f"stock_deductions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf")


@inventory_api_bp.route("/audit/stock-deductions/history", methods=["GET"])
@role_required("inventory")
def stock_deductions_history():
    return jsonify({"ok": True, "history": list_deduction_history(_safe_int(request.args.get("limit"), 100))})


@inventory_api_bp.route("/reports/bootstrap", methods=["GET"])
@role_required("inventory")
def reports_bootstrap():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    return jsonify({"ok": True, **_build_reports_analytics_payload(identity, user_doc)})


@inventory_api_bp.route("/stock-taking-sessions", methods=["POST"])
@role_required("inventory")
def create_stock_taking():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        session_data = create_stock_taking_session(payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "session": session_data}), 201


@inventory_api_bp.route("/stock-taking-sessions/<session_id>", methods=["GET"])
@role_required("inventory")
def stock_taking_detail(session_id: str):
    detail = get_stock_taking_session_detail(session_id)
    if not detail:
        return jsonify({"ok": False, "error": "Stock taking session not found."}), 404
    return jsonify({"ok": True, "session": detail})


@inventory_api_bp.route("/stock-taking-sessions/<session_id>/counts", methods=["POST"])
@role_required("inventory")
def stock_taking_update_counts(session_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        detail = update_stock_taking_counts(session_id, payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "session": detail})


@inventory_api_bp.route("/stock-taking-sessions/<session_id>/submit", methods=["POST"])
@role_required("inventory")
def stock_taking_submit(session_id: str):
    try:
        detail = submit_stock_taking_session(session_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "session": detail})


@inventory_api_bp.route("/stock-taking-sessions/<session_id>/approve", methods=["POST"])
@role_required("inventory")
def stock_taking_approve(session_id: str):
    try:
        detail = approve_stock_taking_session(session_id, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "session": detail})


@inventory_api_bp.route("/products/<product_id>", methods=["GET"])
@role_required("inventory")
def inventory_product_detail(product_id: str):
    product = get_inventory_product_detail(product_id)
    if not product:
        return jsonify({"ok": False, "error": "Product not found."}), 404
    return jsonify({"ok": True, "product": product})


@inventory_api_bp.route("/products/<product_id>/export.pdf", methods=["GET"])
@role_required("inventory")
def inventory_product_detail_export(product_id: str):
    product = get_inventory_product_detail(product_id)
    if not product:
        return jsonify({"ok": False, "error": "Product not found."}), 404

    tab = (request.args.get("tab") or "overview").strip().lower()
    if tab not in {"overview", "locations", "product-cards", "forecast"}:
        return jsonify({"ok": False, "error": "Only live product tabs can be exported right now."}), 400

    pdf_bytes = _build_product_detail_pdf(product, tab)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in (product.get("name") or "inventory_product")).strip("_") or "inventory_product"
    filename = f"{safe_name}_{tab}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@inventory_api_bp.route("/submitted-cards/bootstrap", methods=["GET"])
@role_required("inventory")
def submitted_cards_bootstrap():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    payload = _submitted_cards_payload(identity, user_doc)

    return jsonify(
        {
            "ok": True,
            "cards": payload["cards"],
            "counts": payload["counts"],
            "branches": payload["branches"],
            "agents": payload["agents"],
            "pagination": payload["pagination"],
        }
    )


@inventory_api_bp.route("/submitted-cards/counts", methods=["GET"])
@role_required("inventory")
def submitted_cards_counts():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    scope = _submitted_card_scope(identity, user_doc)
    return jsonify({"ok": True, "counts": _submitted_card_counts(scope)})


@inventory_api_bp.route("/submitted-cards/export.pdf", methods=["GET"])
@role_required("inventory")
def submitted_cards_export_pdf():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    payload = _submitted_cards_payload(identity, user_doc, include_all_cards=True)
    pdf_bytes = _build_submitted_cards_pdf(
        payload["all_cards"],
        {
            "Search": request.args.get("search") or "",
            "Status": request.args.get("status") or "all",
            "Branch": request.args.get("branch") or "all",
            "Agent": request.args.get("agent") or "all",
            "From": request.args.get("dateFrom") or "",
            "To": request.args.get("dateTo") or "",
        },
    )
    filename = f"submitted_cards_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@inventory_api_bp.route("/submitted-cards/export.csv", methods=["GET"])
@role_required("inventory")
def submitted_cards_export_csv():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    payload = _submitted_cards_payload(identity, user_doc, include_all_cards=True)

    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Customer", "Phone", "Product", "Quantity", "Agent", "Branch", "Submitted", "Status", "Paid", "Balance"])
    for card in payload["all_cards"]:
        writer.writerow([
            card.get("customerName") or "",
            card.get("customerPhone") or "",
            card.get("productName") or "",
            card.get("quantity") or 0,
            card.get("agentName") or "",
            card.get("branch") or "",
            card.get("submittedAt") or "",
            card.get("statusLabel") or "",
            card.get("amountPaid") or 0,
            card.get("amountLeft") or 0,
        ])

    filename = f"submitted_cards_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        BytesIO(stream.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


def _stock_record_scope(identity):
    return _inventory_branch_scope(identity, get_inventory_user_doc(identity.get("user_id")))


def _stock_identity(identity):
    return {**identity, "stock_branch_scope": _stock_record_scope(identity).get("branch", "")}


@inventory_api_bp.route("/audit/packaging-stock", methods=["GET"])
@role_required("inventory")
def packaging_stock_records():
    from .packaging_stock import store
    query = _stock_record_scope(get_current_identity())
    if request.args.get("outstanding") == "1":
        query["status"] = {"$in": ["partially_deducted", "not_deducted"]}
    if request.args.get("branch"):
        query["$and"] = [{"branch": request.args["branch"]}]
    if request.args.get("warehouse"):
        rx_location = {"$regex": re.escape(request.args["warehouse"].strip()), "$options": "i"}
        query.setdefault("$and", []).append({"$or": [{"components.allocations.location_name": rx_location}, {"locations.location_name": rx_location}]})
    if request.args.get("search"):
        rx = {"$regex": re.escape(request.args["search"].strip()), "$options": "i"}
        query["$or"] = [{field: rx} for field in ("agent_name", "customer_name", "product_card", "deduction_batch_id")]
    if request.args.get("fromDate") and request.args.get("toDate"):
        try:
            start, end = store.parse_ghana_range(request.args["fromDate"], request.args["toDate"])
        except StockDeductionError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        query["first_attempt_at"] = {"$gte": start, "$lt": end}
    page = max(1, min(store._integer(request.args.get("page"), 1), 100000))
    count = store.deductions_col.count_documents(query)
    rows = list(store.deductions_col.find(query).sort("confirmed_at", -1).skip((page - 1) * 25).limit(25))
    return jsonify(ok=True, rows=store._json_value(rows), total=count, page=page)


@inventory_api_bp.route("/audit/packaging-stock/<package_id>/attempts", methods=["GET"])
@role_required("inventory")
def packaging_stock_attempt_history(package_id):
    from .packaging_stock import store
    scope = _stock_record_scope(get_current_identity())
    query = {"$and": [scope, {"package_id": _oid(package_id)}]}
    rows = list(packaging_attempts.find(query).sort("created_at", -1).limit(100))
    return jsonify(ok=True, rows=store._json_value(rows))


@inventory_api_bp.route("/audit/packaging-stock/<package_id>/settle", methods=["POST"])
@role_required("inventory")
def packaging_stock_settle(package_id):
    identity = get_current_identity()
    if not db["inventory_delivery_deductions"].find_one({**_stock_record_scope(identity), "package_id": _oid(package_id)}):
        return jsonify(ok=False, error="Deduction not found or outside your branch."), 404
    from .stock_deductions_store import _can_confirm
    if not _can_confirm(identity):
        return jsonify(ok=False, error="Stock deduction approval permission is required to settle."), 403
    try:
        result = transition_packaging_stock(package_id, "packaging", _stock_identity(identity), settle=True,
            request_id=(request.get_json(silent=True) or {}).get("requestId"))
        return jsonify(ok=True, deduction=result)
    except StockDeductionError as exc:
        return jsonify(ok=False, error=str(exc), code=exc.code), exc.status


@inventory_api_bp.route("/audit/packaging-stock/metrics", methods=["GET"])
@role_required("inventory")
def packaging_stock_metrics():
    from .packaging_stock import store, summarize_metrics
    from datetime import timedelta
    identity = get_current_identity()
    scope = _stock_record_scope(identity)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    period = request.args.get("period", "today")
    start, end = (today.replace(day=1), today + timedelta(days=1)) if period == "month" else (today, today + timedelta(days=1))
    if period == "custom":
        try:
            start, end = store.parse_ghana_range(request.args.get("fromDate", ""), request.args.get("toDate", ""))
        except StockDeductionError as exc:
            return jsonify(ok=False, error=str(exc)), 400
    if request.args.get("branch"):
        scope["$and"] = [{"branch": request.args["branch"]}]
    records = list(store.deductions_col.find(scope))
    flows = list(store.movements_col.find({**scope, "created_at": {"$gte": start, "$lt": end}}))
    initial = list(packaging_attempts.find({**scope, "kind": "packaging", "created_at": {"$gte": start, "$lt": end}}))
    # Warehouse visibility follows the same branch scope as the underlying cards.
    branches = {r.get("branch") for r in records if r.get("branch")}
    warehouses = list(store.locations_col.find({"branch": {"$in": list(branches)}} if scope else {}))
    metrics = summarize_metrics(records, flows, initial, warehouses)
    unknown, estimate = 0, 0.0
    for record in records:
        for component in record.get("components") or []:
            remaining = int(component.get("remaining_quantity") or 0)
            if not remaining:
                continue
            costs = [float(a["unit_cost"]) for a in component.get("allocations") or [] if a.get("unit_cost")]
            if not costs:
                product = store.inventory_products_col.find_one({"_id": _oid(component.get("inventory_product_id"))})
                for location in warehouses:
                    if location.get("branch") == record.get("branch") and product:
                        _, cost = store._location_stock(product, str(location["_id"]))
                        if cost:
                            costs.append(cost)
            if costs:
                estimate += remaining * costs[-1]
            else:
                unknown += remaining
    metrics.update(outstandingEstimatedValue=round(estimate, 2), unvaluedOutstandingUnits=unknown)
    return jsonify(ok=True, **metrics)


@inventory_api_bp.route("/submitted-cards/<card_id>/status", methods=["POST"])
@role_required("inventory")
def submitted_cards_update_status(card_id: str):
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    scope = _submitted_card_scope(identity, user_doc)

    card_oid = _oid(card_id)
    if not card_oid:
        return jsonify({"ok": False, "error": "Invalid submitted card ID."}), 400

    card_doc = packages_collection.find_one({**scope, "_id": card_oid})
    if not card_doc:
        return jsonify({"ok": False, "error": "Submitted card not found."}), 404

    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    requested_status = str(payload.get("status") or "").strip().lower()

    current_status = str(card_doc.get("status") or "pending").strip().lower() or "pending"
    if current_status not in SUBMITTED_CARD_STATUS_FLOW:
        current_status = "pending"

    if not requested_status:
        current_index = SUBMITTED_CARD_STATUS_FLOW.index(current_status)
        requested_status = SUBMITTED_CARD_STATUS_FLOW[min(current_index + 1, len(SUBMITTED_CARD_STATUS_FLOW) - 1)]

    if requested_status not in SUBMITTED_CARD_STATUS_FLOW:
        return jsonify({"ok": False, "error": "Unsupported submitted card status."}), 400
    try:
        report = transition_packaging_stock(card_id, requested_status, _stock_identity(identity))
    except StockDeductionError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    refreshed = packages_collection.find_one({"_id": card_oid}) or card_doc
    _attach_branch_metadata([refreshed])
    return jsonify({"ok": True, "card": _serialize_submitted_card(refreshed), "deduction": report, "counts": _submitted_card_counts(scope)})


@inventory_api_bp.route("/submitted-cards/bulk-status", methods=["POST"])
@role_required("inventory")
def submitted_cards_bulk_update_status():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    scope = _submitted_card_scope(identity, user_doc)

    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    requested_status = str(payload.get("status") or "").strip().lower()
    if requested_status not in SUBMITTED_CARD_STATUS_FLOW:
        return jsonify({"ok": False, "error": "Choose a supported submitted card status."}), 400

    raw_ids = payload.get("cardIds") or payload.get("ids") or []
    if not isinstance(raw_ids, list):
        return jsonify({"ok": False, "error": "Card IDs must be a list."}), 400
    card_oids = [_oid(card_id) for card_id in raw_ids]
    card_oids = [oid for oid in card_oids if oid]
    if not card_oids:
        return jsonify({"ok": False, "error": "Select at least one submitted card."}), 400

    rows = list(packages_collection.find({**scope, "_id": {"$in": card_oids}}))
    if not rows:
        return jsonify({"ok": False, "error": "No eligible submitted cards found."}), 404

    results = []
    for card_doc in rows:
        try:
            report = transition_packaging_stock(str(card_doc["_id"]), requested_status, _stock_identity(identity))
            results.append({"ok": True, "packageId": str(card_doc["_id"]), "deduction": report})
        except StockDeductionError as exc:
            results.append({"ok": False, "packageId": str(card_doc["_id"]), "error": str(exc)})
    return jsonify({"ok": True, "updated": sum(row["ok"] for row in results),
                    "skipped": sum(not row["ok"] for row in results), "results": results,
                    "status": requested_status, "statusLabel": _status_label(requested_status),
                    "counts": _submitted_card_counts(scope)})


@inventory_api_bp.route("/customers-completion", methods=["GET"])
@role_required("inventory")
def customers_completion():
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    payload = _customer_completion_rows(
        identity,
        user_doc,
        branch_filter=request.args.get("branch") or "all",
        agent_filter=request.args.get("agent") or "all",
        search=request.args.get("search") or "",
        tab=request.args.get("tab") or "all",
        page=request.args.get("page") or 1,
        per_page=request.args.get("perPage") or 20,
    )
    return jsonify({"ok": True, **payload})


@inventory_api_bp.route("/customers/<customer_id>/profile", methods=["GET"])
@role_required("inventory")
def inventory_customer_profile(customer_id: str):
    identity = get_current_identity()
    user_doc = get_inventory_user_doc(identity.get("user_id"))
    payload = _customer_profile_payload(customer_id, identity, user_doc)
    if not payload:
        return jsonify({"ok": False, "error": "Customer not found or unavailable for your branch."}), 404
    return jsonify({"ok": True, **payload})


@inventory_api_bp.route("/product-cards", methods=["GET"])
@role_required("inventory")
def product_cards():
    return jsonify({"ok": True, "cards": list_product_cards()})


@inventory_api_bp.route("/product-cards/bootstrap", methods=["GET"])
@role_required("inventory")
def product_cards_bootstrap():
    return jsonify({"ok": True, **get_product_card_bootstrap()})


@inventory_api_bp.route("/product-cards", methods=["POST"])
@role_required("inventory")
def create_card():
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}

    try:
        result = create_product_card(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    response = {
        "ok": True,
        "createdCount": result.get("createdCount") or 0,
        "skipped": result.get("skipped") or [],
        "card": result.get("card"),
    }
    return jsonify(response), 201


@inventory_api_bp.route("/product-cards/<card_id>/components", methods=["PATCH"])
@role_required("inventory")
def update_card_components(card_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}

    try:
        result = update_product_card_components(card_id, payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "updatedCount": result.get("updatedCount") or 0,
            "updatedPackages": result.get("updatedPackages") or 0,
            "card": result.get("card"),
        }
    )


@inventory_api_bp.route("/product-cards/<card_id>", methods=["PATCH"])
@role_required("inventory")
def update_card(card_id: str):
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    try:
        result = update_product_card(card_id, payload, get_current_identity())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})
