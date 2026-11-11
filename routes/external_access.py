from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from io import BytesIO
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Dict, Optional

from bson import ObjectId
from flask import Blueprint, Response, jsonify, render_template, request, send_file
from pymongo.errors import ExecutionTimeout
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db import db
from login import get_current_identity, role_required

external_access_bp = Blueprint(
    "external_access",
    __name__,
    url_prefix="/executive/external-access",
    template_folder="../templates",
)

external_api_public_bp = Blueprint(
    "external_api_public",
    __name__,
    url_prefix="/external-api/v1",
)

customers_col = db["customers"]
payments_col = db["payments"]
card_closures_col = db["card_closures"]
packages_col = db["packages"]
outflow_col = db["inventory_products_outflow"]
users_col = db["users"]
products_col = db["products"]
api_keys_col = db["external_api_keys"]
api_logs_col = db["external_api_request_logs"]

API_KEY_SCOPES = [
    "closed_customers",
    "payments",
    "closed_cards",
    "completed_cards",
    "customers",
    "users",
    "products",
]
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
DEFAULT_DB_TIMEOUT_MS = 12000
MAX_LIMIT = 100
PUBLISHED_EXTERNAL_BASE_URL = "https://smartliving-u2rf.onrender.com/external-api/v1"

API_ENDPOINT_DOCS = [
    {"scope": "closed_customers", "path": "/closed-customers", "date_field": "status_updated_at", "response_fields": "Full matching customer document plus api_key_prefix", "description": "Customers whose account or purchase is closed."},
    {"scope": "payments", "path": "/payments", "date_field": "date", "response_fields": "Payment document plus joined customer", "description": "Payments belonging to closed customer accounts."},
    {"scope": "closed_cards", "path": "/closed-cards", "date_field": "at", "response_fields": "Closure event plus joined customer", "description": "Card-closing events, including the related customer."},
    {"scope": "completed_cards", "path": "/completed-cards", "date_field": "created_at", "response_fields": "Package document plus joined customer", "description": "Completed-card package and delivery records."},
    {"scope": "customers", "path": "/customers", "date_field": "date_registered", "response_fields": "Customer ID, profile, ownership, registration, purchases, status and related stored fields", "description": "Customer records, purchases, ownership, and registration details."},
    {"scope": "users", "path": "/users", "date_field": "date_registered", "response_fields": "User ID, username, role, contact/profile, branch, status, manager and timestamps (never password)", "description": "Safe user profile records. Password hashes are never returned."},
    {"scope": "products", "path": "/products", "date_field": "created_at", "response_fields": "Product ID, name, pricing, description, images, type/category, components, manager and timestamps", "description": "Product catalogue, pricing, components, and manager ownership."},
]


class ExternalApiValidationError(ValueError):
    """A client request error that should be returned as HTTP 400."""


def _ensure_indexes() -> None:
    try:
        api_keys_col.create_index([("key_prefix", 1)], unique=True)
        api_keys_col.create_index([("user_name", 1), ("created_at", -1)])
        api_keys_col.create_index([("active", 1), ("created_at", -1)])
        api_logs_col.create_index([("api_key_id", 1), ("at", -1)])
        api_logs_col.create_index([("status_code", 1), ("at", -1)])
        api_logs_col.create_index([("route", 1), ("at", -1)])
        customers_col.create_index([("status", 1), ("status_updated_at", -1)])
        payments_col.create_index([("card_closed_at", -1)])
        payments_col.create_index([("date", -1)])
        card_closures_col.create_index([("action", 1), ("at", -1)])
        packages_col.create_index([("status", 1), ("created_at", -1)])
        customers_col.create_index([("date_registered", -1)])
        users_col.create_index([("date_registered", -1)])
        users_col.create_index([("created_at", -1)])
        products_col.create_index([("created_at", -1)])
        # Keys in this module intentionally grant every published read-only scope.
        # Keep already-issued active keys aligned when new scopes are introduced.
        api_keys_col.update_many({}, {"$addToSet": {"scopes": {"$each": list(API_KEY_SCOPES)}}})
    except Exception:
        pass


_ensure_indexes()


def _safe_oid(raw: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def _utcnow() -> datetime:
    return datetime.utcnow()


def _serialize(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    return value


def _normalize_api_user_name(raw: Any) -> str:
    name = " ".join(str(raw or "").strip().split())
    return name[:80]


def _client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()[:120]
    return (request.remote_addr or "")[:120]


def _request_headers_safe() -> dict[str, str]:
    safe = {}
    for key in ("User-Agent", "X-Request-Id", "Accept", "Host"):
        value = request.headers.get(key)
        if value:
            safe[key] = value[:300]
    return safe


def _api_response(payload: dict[str, Any], status: int = 200) -> Response:
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _generate_api_key() -> tuple[str, str]:
    prefix = f"slive_{secrets.token_hex(6)}"
    secret = secrets.token_urlsafe(24)
    return prefix, f"{prefix}.{secret}"


def _extract_api_key() -> str:
    header_value = (request.headers.get("X-API-Key") or "").strip()
    if header_value:
        return header_value
    auth_value = (request.headers.get("Authorization") or "").strip()
    if auth_value.lower().startswith("bearer "):
        return auth_value[7:].strip()
    return ""


def _resolve_api_key(raw_key: str) -> Optional[dict[str, Any]]:
    if "." not in raw_key:
        return None
    prefix = raw_key.split(".", 1)[0].strip()
    if not prefix:
        return None
    doc = api_keys_col.find_one({"key_prefix": prefix, "active": True}, max_time_ms=DEFAULT_DB_TIMEOUT_MS)
    if not doc:
        return None
    expected = doc.get("key_hash") or ""
    computed = _hash_key(raw_key)
    if not hmac.compare_digest(expected, computed):
        return None
    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at <= _utcnow():
        return None
    return doc


def _log_api_request(
    *,
    key_doc: Optional[dict[str, Any]],
    status_code: int,
    route_name: str,
    scope: str,
    duration_ms: int,
    rows_returned: int = 0,
    error_code: str = "",
) -> None:
    try:
        api_logs_col.insert_one(
            {
                "at": _utcnow(),
                "api_key_id": key_doc.get("_id") if key_doc else None,
                "api_key_prefix": key_doc.get("key_prefix") if key_doc else "",
                "api_user_name": key_doc.get("user_name") if key_doc else "",
                "route": route_name,
                "scope": scope,
                "method": request.method,
                "path": request.path,
                "query": {k: str(v)[:200] for k, v in request.args.items()},
                "status_code": int(status_code),
                "duration_ms": int(duration_ms),
                "rows_returned": int(rows_returned),
                "error_code": error_code,
                "ip_address": _client_ip(),
                "headers": _request_headers_safe(),
            }
        )
    except Exception:
        pass


def _is_rate_limited(key_doc: dict[str, Any]) -> bool:
    limit = int(key_doc.get("rate_limit_per_minute") or DEFAULT_RATE_LIMIT_PER_MINUTE)
    if limit <= 0:
        return False
    one_minute_ago = _utcnow() - timedelta(minutes=1)
    try:
        count = api_logs_col.count_documents(
            {"api_key_id": key_doc["_id"], "at": {"$gte": one_minute_ago}},
            maxTimeMS=DEFAULT_DB_TIMEOUT_MS,
        )
        return int(count) >= limit
    except Exception:
        return False


def _touch_key_usage(key_doc: dict[str, Any]) -> None:
    try:
        api_keys_col.update_one(
            {"_id": key_doc["_id"]},
            {"$set": {"last_used_at": _utcnow(), "last_used_ip": _client_ip()}},
        )
    except Exception:
        pass


def _parse_paging() -> tuple[int, int, int]:
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except Exception:
        page = 1
    try:
        limit = max(1, min(int(request.args.get("limit", 25) or 25), MAX_LIMIT))
    except Exception:
        limit = 25
    skip = (page - 1) * limit
    return page, limit, skip


def _parse_date_range() -> tuple[Optional[datetime], Optional[datetime], dict[str, str]]:
    """Parse inclusive YYYY-MM-DD start/end values; return an exclusive end datetime."""
    start_raw = (request.args.get("start_date") or "").strip()
    end_raw = (request.args.get("end_date") or "").strip()

    def parse(raw: str, name: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError as exc:
            raise ExternalApiValidationError(f"{name} must use YYYY-MM-DD format.") from exc

    start = parse(start_raw, "start_date")
    end_inclusive = parse(end_raw, "end_date")
    if start and end_inclusive and start > end_inclusive:
        raise ExternalApiValidationError("start_date cannot be after end_date.")
    end_exclusive = end_inclusive + timedelta(days=1) if end_inclusive else None
    return start, end_exclusive, {"start_date": start_raw, "end_date": end_raw}


def _date_match(field: str, start: Optional[datetime], end: Optional[datetime]) -> dict[str, Any]:
    bounds: dict[str, datetime] = {}
    if start:
        bounds["$gte"] = start
    if end:
        bounds["$lt"] = end
    return {field: bounds} if bounds else {}


def _combine_match(*parts: dict[str, Any]) -> dict[str, Any]:
    usable = [part for part in parts if part]
    if not usable:
        return {}
    if len(usable) == 1:
        return usable[0]
    return {"$and": usable}


def _filters_payload(search: str, dates: dict[str, str], date_field: str) -> dict[str, str]:
    return {"search": search, **dates, "date_field": date_field}


def _pagination_payload(total: int, page: int, limit: int) -> dict[str, Any]:
    total_pages = max(1, (int(total) + limit - 1) // limit) if total else 1
    return {
        "page": page,
        "limit": limit,
        "total": int(total),
        "total_pages": total_pages,
    }


def _closed_customer_match(search: str) -> dict[str, Any]:
    base = {
        "$or": [
            {"status": "closed"},
            {"purchases": {"$elemMatch": {"status": "closed"}}},
            {"purchases": {"$elemMatch": {"product.status": "closed"}}},
        ]
    }
    if not search:
        return base
    return {
        "$and": [
            base,
            {
                "$or": [
                    {"name": {"$regex": search, "$options": "i"}},
                    {"phone_number": {"$regex": search, "$options": "i"}},
                ]
            },
        ]
    }


def _external_api_protected(scope: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            raw_key = _extract_api_key()
            if not raw_key:
                _log_api_request(
                    key_doc=None,
                    status_code=401,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code="missing_api_key",
                )
                return _api_response({"ok": False, "error": "Missing API key."}, 401)

            key_doc = _resolve_api_key(raw_key)
            if not key_doc:
                _log_api_request(
                    key_doc=None,
                    status_code=401,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code="invalid_api_key",
                )
                return _api_response({"ok": False, "error": "Invalid API key."}, 401)

            scopes = key_doc.get("scopes") or []
            if scope not in scopes:
                _log_api_request(
                    key_doc=key_doc,
                    status_code=403,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code="scope_denied",
                )
                return _api_response({"ok": False, "error": "Scope not allowed."}, 403)

            if _is_rate_limited(key_doc):
                _log_api_request(
                    key_doc=key_doc,
                    status_code=429,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_code="rate_limited",
                )
                return _api_response({"ok": False, "error": "Rate limit exceeded."}, 429)

            try:
                payload, status_code, rows_returned = fn(key_doc=key_doc, *args, **kwargs)
            except ExternalApiValidationError as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                _log_api_request(
                    key_doc=key_doc,
                    status_code=400,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=duration_ms,
                    error_code="invalid_query",
                )
                return _api_response({"ok": False, "error": str(exc)}, 400)
            except ExecutionTimeout:
                duration_ms = int((time.perf_counter() - started) * 1000)
                _log_api_request(
                    key_doc=key_doc,
                    status_code=504,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=duration_ms,
                    error_code="database_timeout",
                )
                return _api_response({"ok": False, "error": "Database timeout."}, 504)
            except Exception:
                duration_ms = int((time.perf_counter() - started) * 1000)
                _log_api_request(
                    key_doc=key_doc,
                    status_code=500,
                    route_name=request.path,
                    scope=scope,
                    duration_ms=duration_ms,
                    error_code="server_error",
                )
                return _api_response({"ok": False, "error": "Server error."}, 500)

            duration_ms = int((time.perf_counter() - started) * 1000)
            _touch_key_usage(key_doc)
            _log_api_request(
                key_doc=key_doc,
                status_code=status_code,
                route_name=request.path,
                scope=scope,
                duration_ms=duration_ms,
                rows_returned=rows_returned,
            )
            return _api_response(payload, status_code)

        return wrapper

    return decorator


def _api_key_summary(doc: dict[str, Any]) -> dict[str, Any]:
    created_by_name = ""
    creator_id = _safe_oid(doc.get("created_by"))
    if creator_id:
        creator = users_col.find_one({"_id": creator_id}, {"name": 1}, max_time_ms=DEFAULT_DB_TIMEOUT_MS)
        created_by_name = (creator or {}).get("name") or ""
    return {
        "id": str(doc.get("_id")),
        "user_name": doc.get("user_name") or "",
        "key_prefix": doc.get("key_prefix") or "",
        "active": bool(doc.get("active", True)),
        "scopes": doc.get("scopes") or [],
        "created_at": _serialize(doc.get("created_at")),
        "created_by": doc.get("created_by") or "",
        "created_by_name": created_by_name,
        "last_used_at": _serialize(doc.get("last_used_at")),
        "last_used_ip": doc.get("last_used_ip") or "",
        "rate_limit_per_minute": int(doc.get("rate_limit_per_minute") or DEFAULT_RATE_LIMIT_PER_MINUTE),
    }


def _replace_api_key(key_doc: dict[str, Any], *, user_name: Optional[str] = None, created_by: str = "") -> tuple[dict[str, Any], str]:
    prefix, raw_key = _generate_api_key()
    now = _utcnow()
    api_keys_col.update_one(
        {"_id": key_doc["_id"]},
        {
            "$set": {
                "user_name": _normalize_api_user_name(user_name or key_doc.get("user_name") or ""),
                "key_prefix": prefix,
                "key_hash": _hash_key(raw_key),
                "active": True,
                "created_at": now,
                "created_by": created_by or key_doc.get("created_by") or "",
                "last_used_at": None,
                "last_used_ip": "",
                "revoked_at": None,
            }
        },
    )
    refreshed = api_keys_col.find_one({"_id": key_doc["_id"]}, {"key_hash": 0}, max_time_ms=DEFAULT_DB_TIMEOUT_MS) or key_doc
    return refreshed, raw_key


def _build_docs_text(user_name: str, api_key: str) -> list[str]:
    lines = [
        f"Base URL: {PUBLISHED_EXTERNAL_BASE_URL}",
        f"User Name: {user_name}",
        f"API Key: {api_key}",
        "",
        "Required Headers:",
        "X-API-Key: <api_key>",
        "Accept: application/json",
        "",
        "Request Format:",
        f"GET {PUBLISHED_EXTERNAL_BASE_URL}/<endpoint>?page=1&limit=25&search=ama&start_date=2026-08-01&end_date=2026-08-08",
        "",
        "Query Parameters:",
        "page (default 1), limit (default 25, max 100), search, start_date, end_date",
        "Dates use YYYY-MM-DD. Both boundaries are inclusive; either date may be supplied alone.",
        "",
        "Response Format:",
        '{"ok":true,"scope":"<scope>","rows":[...],"pagination":{"page":1,"limit":25,"total":1,"total_pages":1},"filters":{"search":"","start_date":"2026-08-01","end_date":"2026-08-08","date_field":"<field>"}}',
        "",
        "Available Endpoints:",
    ]
    for index, endpoint in enumerate(API_ENDPOINT_DOCS, start=1):
        lines.append(
            f"{index}. {PUBLISHED_EXTERNAL_BASE_URL}{endpoint['path']} (scope: {endpoint['scope']}; period field: {endpoint['date_field']})"
        )
    lines.extend([
        "",
        "Security Notes:",
        "- Keep the API key private.",
        "- Requests are rate-limited to 120 per minute by default.",
        "- Responses are read-only and no-store.",
        "- Server-side database timeout protection is enabled.",
        "- User password hashes are never included in responses.",
    ])
    return lines


def _export_docs_pdf(user_name: str, api_key: str) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DocTitleX", parent=styles["Title"], fontSize=18, leading=22, textColor=colors.HexColor("#0f172a")))
    styles.add(ParagraphStyle(name="DocBodyX", parent=styles["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor("#334155")))
    styles.add(ParagraphStyle(name="DocMonoX", parent=styles["BodyText"], fontName="Courier", fontSize=9, leading=12, textColor=colors.HexColor("#0f172a")))

    story = [
        Paragraph("SmartLiving External API Documentation", styles["DocTitleX"]),
        Spacer(1, 6 * mm),
        Paragraph("Issued integration guide for external client access.", styles["DocBodyX"]),
        Spacer(1, 8 * mm),
    ]

    summary_table = Table(
        [
            ["User Name", user_name],
            ["Base URL", PUBLISHED_EXTERNAL_BASE_URL],
            ["API Key", api_key],
            ["Generated At", _utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")],
        ],
        colWidths=[38 * mm, 134 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 2), (1, 2), "Courier"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([summary_table, Spacer(1, 8 * mm)])

    for line in _build_docs_text(user_name, api_key):
      style = styles["DocMonoX"] if line.startswith("http") or "API Key:" in line or "/closed-" in line or "/payments" in line or "/completed-" in line else styles["DocBodyX"]
      story.append(Paragraph((line or "&nbsp;").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style))
      story.append(Spacer(1, 1.5 * mm))

    doc.build(story)
    buffer.seek(0)
    return buffer


@external_access_bp.route("/", methods=["GET"])
@role_required("executive", "admin")
def external_access_page():
    ident = get_current_identity()
    return render_template(
        "executive_external_access.html",
        identity=ident,
        scopes=API_KEY_SCOPES,
        endpoint_docs=API_ENDPOINT_DOCS,
        published_base_url=PUBLISHED_EXTERNAL_BASE_URL,
    )


@external_access_bp.route("/api-keys", methods=["GET"])
@role_required("executive", "admin")
def list_api_keys():
    docs = list(api_keys_col.find({}, {"key_hash": 0}).sort("created_at", -1).limit(100).max_time_ms(DEFAULT_DB_TIMEOUT_MS))
    return _api_response({"ok": True, "keys": [_api_key_summary(doc) for doc in docs]})


@external_access_bp.route("/api-keys", methods=["POST"])
@role_required("executive", "admin")
def create_api_key():
    ident = get_current_identity()
    payload = request.get_json(silent=True) or {}
    user_name = _normalize_api_user_name(payload.get("user_name"))
    if len(user_name) < 3:
        return _api_response({"ok": False, "error": "User name must be at least 3 characters."}, 400)

    prefix, raw_key = _generate_api_key()
    now = _utcnow()
    doc = {
        "user_name": user_name,
        "key_prefix": prefix,
        "key_hash": _hash_key(raw_key),
        "active": True,
        "scopes": list(API_KEY_SCOPES),
        "created_at": now,
        "created_by": ident.get("user_id") or "",
        "rate_limit_per_minute": DEFAULT_RATE_LIMIT_PER_MINUTE,
        "last_used_at": None,
        "last_used_ip": "",
    }
    api_keys_col.insert_one(doc)
    stored = api_keys_col.find_one({"key_prefix": prefix}, {"key_hash": 0}, max_time_ms=DEFAULT_DB_TIMEOUT_MS) or doc
    return _api_response(
        {
            "ok": True,
            "message": "API key generated. Copy it now; the secret will not be shown again.",
            "api_key": raw_key,
            "key": _api_key_summary(stored),
        },
        201,
    )


@external_access_bp.route("/api-keys/<key_id>/export-docs", methods=["GET"])
@role_required("executive", "admin")
def export_api_docs(key_id: str):
    ident = get_current_identity()
    oid = _safe_oid(key_id)
    if not oid:
        return _api_response({"ok": False, "error": "Invalid key id."}, 400)

    key_doc = api_keys_col.find_one({"_id": oid}, max_time_ms=DEFAULT_DB_TIMEOUT_MS)
    if not key_doc:
        return _api_response({"ok": False, "error": "API key not found."}, 404)

    refreshed_doc, fresh_key = _replace_api_key(
        key_doc,
        user_name=key_doc.get("user_name") or "",
        created_by=ident.get("user_id") or "",
    )
    user_name = refreshed_doc.get("user_name") or "External Client"
    pdf_buffer = _export_docs_pdf(user_name, fresh_key)
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in user_name).strip("_") or "external_client"
    filename = f"external_api_docs_{safe_name}_{_utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@external_access_bp.route("/api-keys/<key_id>/revoke", methods=["POST"])
@role_required("executive", "admin")
def revoke_api_key(key_id: str):
    oid = _safe_oid(key_id)
    if not oid:
        return _api_response({"ok": False, "error": "Invalid key id."}, 400)
    res = api_keys_col.update_one({"_id": oid}, {"$set": {"active": False, "revoked_at": _utcnow()}})
    if not res.matched_count:
        return _api_response({"ok": False, "error": "API key not found."}, 404)
    return _api_response({"ok": True, "message": "API key revoked."})


@external_access_bp.route("/request-logs", methods=["GET"])
@role_required("executive", "admin")
def request_logs():
    page, limit, skip = _parse_paging()
    query: dict[str, Any] = {}
    key_prefix = (request.args.get("key_prefix") or "").strip()
    api_user_name = (request.args.get("user_name") or "").strip()
    if key_prefix:
        query["api_key_prefix"] = key_prefix
    if api_user_name:
        query["api_user_name"] = {"$regex": api_user_name, "$options": "i"}

    total = api_logs_col.count_documents(query, maxTimeMS=DEFAULT_DB_TIMEOUT_MS)
    rows = list(
        api_logs_col.find(query)
        .sort("at", -1)
        .skip(skip)
        .limit(limit)
        .max_time_ms(DEFAULT_DB_TIMEOUT_MS)
    )
    return _api_response(
        {
            "ok": True,
            "rows": [_serialize(row) for row in rows],
            "pagination": _pagination_payload(total, page, limit),
        }
    )


@external_api_public_bp.route("/closed-customers", methods=["GET"])
@_external_api_protected("closed_customers")
def external_closed_customers(*, key_doc: dict[str, Any]):
    page, limit, skip = _parse_paging()
    search = (request.args.get("search") or "").strip()
    start, end, dates = _parse_date_range()
    query = _combine_match(
        _closed_customer_match(re.escape(search[:120])),
        _date_match("status_updated_at", start, end),
    )
    total = customers_col.count_documents(query, maxTimeMS=DEFAULT_DB_TIMEOUT_MS)
    rows = list(
        customers_col.find(query)
        .sort("status_updated_at", -1)
        .skip(skip)
        .limit(limit)
        .max_time_ms(DEFAULT_DB_TIMEOUT_MS)
    )
    serialized = []
    for row in rows:
        row = dict(row)
        row["api_key_prefix"] = key_doc.get("key_prefix") or ""
        serialized.append(_serialize(row))
    return (
        {
            "ok": True,
            "scope": "closed_customers",
            "rows": serialized,
            "pagination": _pagination_payload(total, page, limit),
            "filters": _filters_payload(search, dates, "status_updated_at"),
        },
        200,
        len(serialized),
    )


@external_api_public_bp.route("/payments", methods=["GET"])
@_external_api_protected("payments")
def external_payments(*, key_doc: dict[str, Any]):
    page, limit, skip = _parse_paging()
    search = (request.args.get("search") or "").strip()
    start, end, dates = _parse_date_range()
    pipeline = [
        {
            "$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "_id",
                "as": "customer",
            }
        },
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {
            "$addFields": {
                "__api_period_date": {
                    "$convert": {
                        "input": {"$ifNull": ["$date", "$created_at"]},
                        "to": "date",
                        "onError": "$created_at",
                        "onNull": "$created_at",
                    }
                }
            }
        },
        {
            "$match": {
                "$or": [
                    {"closed_amount": {"$exists": True}},
                    {"card_closed_at": {"$exists": True}},
                    {"customer.status": "closed"},
                ]
            }
        },
    ]
    period_match = _date_match("__api_period_date", start, end)
    if period_match:
        pipeline.append({"$match": period_match})
    if search:
        safe_search = re.escape(search[:120])
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"customer.name": {"$regex": safe_search, "$options": "i"}},
                        {"customer.phone_number": {"$regex": safe_search, "$options": "i"}},
                    ]
                }
            }
        )
    facet = pipeline + [
        {
            "$facet": {
                "rows": [
                    {"$sort": {"card_closed_at": -1, "date": -1, "_id": -1}},
                    {"$skip": skip},
                    {"$limit": limit},
                ],
                "total": [{"$count": "count"}],
            }
        }
    ]
    result = list(payments_col.aggregate(facet, maxTimeMS=DEFAULT_DB_TIMEOUT_MS))
    data = result[0] if result else {}
    raw_rows = data.get("rows") or []
    for row in raw_rows:
        row.pop("__api_period_date", None)
    rows = [_serialize(row) for row in raw_rows]
    total = int((data.get("total") or [{}])[0].get("count") or 0)
    return (
        {
            "ok": True,
            "scope": "payments",
            "rows": rows,
            "pagination": _pagination_payload(total, page, limit),
            "filters": _filters_payload(search, dates, "date"),
        },
        200,
        len(rows),
    )


@external_api_public_bp.route("/closed-cards", methods=["GET"])
@_external_api_protected("closed_cards")
def external_closed_cards(*, key_doc: dict[str, Any]):
    page, limit, skip = _parse_paging()
    search = (request.args.get("search") or "").strip()
    start, end, dates = _parse_date_range()
    pipeline = [
        {"$match": _combine_match({"action": "close_card"}, _date_match("at", start, end))},
        {
            "$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "_id",
                "as": "customer",
            }
        },
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
    ]
    if search:
        safe_search = re.escape(search[:120])
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"customer.name": {"$regex": safe_search, "$options": "i"}},
                        {"customer.phone_number": {"$regex": safe_search, "$options": "i"}},
                    ]
                }
            }
        )
    facet = pipeline + [
        {
            "$facet": {
                "rows": [
                    {"$sort": {"at": -1}},
                    {"$skip": skip},
                    {"$limit": limit},
                ],
                "total": [{"$count": "count"}],
            }
        }
    ]
    result = list(card_closures_col.aggregate(facet, maxTimeMS=DEFAULT_DB_TIMEOUT_MS))
    data = result[0] if result else {}
    rows = [_serialize(row) for row in (data.get("rows") or [])]
    total = int((data.get("total") or [{}])[0].get("count") or 0)
    return (
        {
            "ok": True,
            "scope": "closed_cards",
            "rows": rows,
            "pagination": _pagination_payload(total, page, limit),
            "filters": _filters_payload(search, dates, "at"),
        },
        200,
        len(rows),
    )


@external_api_public_bp.route("/completed-cards", methods=["GET"])
@_external_api_protected("completed_cards")
def external_completed_cards(*, key_doc: dict[str, Any]):
    page, limit, skip = _parse_paging()
    search = (request.args.get("search") or "").strip()
    start, end, dates = _parse_date_range()
    pipeline = [
        {
            "$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "_id",
                "as": "customer",
            }
        },
        {"$unwind": {"path": "$customer", "preserveNullAndEmptyArrays": True}},
        {
            "$match": {
                "status": {
                    "$in": ["pending", "packaging", "packaged", "delivering", "delivered", "cancelled"]
                }
            }
        },
    ]
    period_match = _date_match("created_at", start, end)
    if period_match:
        pipeline.append({"$match": period_match})
    if search:
        safe_search = re.escape(search[:120])
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"customer.name": {"$regex": safe_search, "$options": "i"}},
                        {"customer.phone_number": {"$regex": safe_search, "$options": "i"}},
                        {"customer_name": {"$regex": safe_search, "$options": "i"}},
                    ]
                }
            }
        )
    facet = pipeline + [
        {
            "$facet": {
                "rows": [
                    {"$sort": {"created_at": -1}},
                    {"$skip": skip},
                    {"$limit": limit},
                ],
                "total": [{"$count": "count"}],
            }
        }
    ]
    result = list(packages_col.aggregate(facet, maxTimeMS=DEFAULT_DB_TIMEOUT_MS))
    data = result[0] if result else {}
    rows = [_serialize(row) for row in (data.get("rows") or [])]
    total = int((data.get("total") or [{}])[0].get("count") or 0)
    return (
        {
            "ok": True,
            "scope": "completed_cards",
            "rows": rows,
            "pagination": _pagination_payload(total, page, limit),
            "filters": _filters_payload(search, dates, "created_at"),
        },
        200,
        len(rows),
    )


def _collection_response(
    *,
    collection,
    scope: str,
    date_field: str,
    search_fields: list[str],
    projection: Optional[dict[str, int]] = None,
    fallback_date_field: Optional[str] = None,
) -> tuple[dict[str, Any], int, int]:
    page, limit, skip = _parse_paging()
    search = (request.args.get("search") or "").strip()
    start, end, dates = _parse_date_range()
    safe_search = re.escape(search[:120])

    search_match = (
        {"$or": [{field: {"$regex": safe_search, "$options": "i"}} for field in search_fields]}
        if safe_search
        else {}
    )

    if fallback_date_field:
        pipeline: list[dict[str, Any]] = [
            {"$addFields": {"__api_period_date": {"$ifNull": [f"${date_field}", f"${fallback_date_field}"]}}},
            {"$match": _combine_match(search_match, _date_match("__api_period_date", start, end))},
            {
                "$facet": {
                    "rows": [
                        {"$sort": {"__api_period_date": -1, "_id": -1}},
                        {"$skip": skip},
                        {"$limit": limit},
                        {"$project": projection if projection else {"__api_period_date": 0}},
                    ],
                    "total": [{"$count": "count"}],
                }
            },
        ]
        data = (list(collection.aggregate(pipeline, maxTimeMS=DEFAULT_DB_TIMEOUT_MS)) or [{}])[0]
        raw_rows = data.get("rows") or []
        total = int((data.get("total") or [{}])[0].get("count") or 0)
    else:
        query = _combine_match(search_match, _date_match(date_field, start, end))
        total = collection.count_documents(query, maxTimeMS=DEFAULT_DB_TIMEOUT_MS)
        raw_rows = list(
            collection.find(query, projection)
            .sort([(date_field, -1), ("_id", -1)])
            .skip(skip)
            .limit(limit)
            .max_time_ms(DEFAULT_DB_TIMEOUT_MS)
        )

    rows = [_serialize(row) for row in raw_rows]
    return (
        {
            "ok": True,
            "scope": scope,
            "rows": rows,
            "pagination": _pagination_payload(total, page, limit),
            "filters": _filters_payload(search, dates, date_field),
        },
        200,
        len(rows),
    )


@external_api_public_bp.route("/customers", methods=["GET"])
@_external_api_protected("customers")
def external_customers(*, key_doc: dict[str, Any]):
    return _collection_response(
        collection=customers_col,
        scope="customers",
        date_field="date_registered",
        search_fields=["name", "phone_number", "location", "occupation"],
    )


@external_api_public_bp.route("/users", methods=["GET"])
@_external_api_protected("users")
def external_users(*, key_doc: dict[str, Any]):
    # Explicit allow-list ensures credentials and future authentication fields do
    # not accidentally become part of the public contract.
    safe_user_projection = {
        "_id": 1,
        "username": 1,
        "role": 1,
        "name": 1,
        "phone": 1,
        "email": 1,
        "gender": 1,
        "branch": 1,
        "position": 1,
        "location": 1,
        "start_date": 1,
        "image_url": 1,
        "status": 1,
        "assets": 1,
        "manager_id": 1,
        "date_registered": 1,
        "created_at": 1,
        "updated_at": 1,
    }
    return _collection_response(
        collection=users_col,
        scope="users",
        date_field="date_registered",
        fallback_date_field="created_at",
        search_fields=["name", "username", "phone", "email", "branch", "role"],
        projection=safe_user_projection,
    )


@external_api_public_bp.route("/products", methods=["GET"])
@_external_api_protected("products")
def external_products(*, key_doc: dict[str, Any]):
    return _collection_response(
        collection=products_col,
        scope="products",
        date_field="created_at",
        search_fields=["name", "description", "product_type", "category", "package_name"],
    )
