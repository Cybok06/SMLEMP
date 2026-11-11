from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from db import db

inventory_roles_col = db["inventory_roles"]
users_col = db["users"]
login_logs_col = db["login_logs"]
inventory_locations_col = db["inventory_branch_locations"]

SYSTEM_PAGES = [
    {"id": "dashboard", "label": "Dashboard"},
    {"id": "product-cards", "label": "Product Cards"},
    {"id": "customers", "label": "Customers & Completion"},
    {"id": "inventory", "label": "Inventory"},
    {"id": "warehouse", "label": "Warehouse Operations"},
    {"id": "submitted-cards", "label": "Submitted Cards"},
    {"id": "undelivered-items", "label": "Undelivered Items"},
    {"id": "fulfillment", "label": "Fulfillment & Delivery"},
    {"id": "suppliers", "label": "Suppliers & Purchases"},
    {"id": "audit", "label": "Audit & Accountability"},
    {"id": "reports", "label": "Reports & Analytics"},
    {"id": "settings", "label": "Settings"},
]

MANAGED_ROLE_IDS = {"admin", "warehouse-manager", "inventory-user"}


def _all_on() -> dict[str, bool]:
    return {
        "visible": True,
        "view": True,
        "create": True,
        "edit": True,
        "delete": True,
        "approve": True,
    }


def _all_off() -> dict[str, bool]:
    return {
        "visible": False,
        "view": False,
        "create": False,
        "edit": False,
        "delete": False,
        "approve": False,
    }


def _only_view() -> dict[str, bool]:
    return {
        "visible": True,
        "view": True,
        "create": False,
        "edit": False,
        "delete": False,
        "approve": False,
    }


def build_perms(enabled: dict[str, dict[str, bool]]) -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    for page in SYSTEM_PAGES:
        page_id = page["id"]
        out[page_id] = {**_all_off(), **(enabled.get(page_id) or {})}
    return out


def default_role_docs() -> list[dict[str, Any]]:
    all_pages_on = {page["id"]: _all_on() for page in SYSTEM_PAGES}
    return [
        {
            "id": "admin",
            "name": "Main Admin",
            "description": "Full access to all modules and configuration",
            "template": True,
            "permissions": all_pages_on,
        },
        {
            "id": "inventory-user",
            "name": "Inventory User",
            "description": "Read-only dashboard and inventory access",
            "template": True,
            "permissions": build_perms(
                {
                    "undelivered-items": {**_only_view(), "edit": True},
                    "dashboard": _only_view(),
                    "inventory": {**_only_view(), "edit": True},
                    "submitted-cards": {
                        "visible": True,
                        "view": True,
                        "create": False,
                        "edit": True,
                        "delete": False,
                        "approve": False,
                    },
                }
            ),
        },
        {
            "id": "warehouse-manager",
            "name": "Warehouse Manager",
            "description": "Manages warehouse operations and inventory movements",
            "template": True,
            "permissions": build_perms(
                {
                    "undelivered-items": {**_only_view(), "edit": True},
                    "dashboard": _only_view(),
                    "inventory": {
                        "visible": True,
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": False,
                        "approve": True,
                    },
                    "warehouse": _all_on(),
                    "submitted-cards": {
                        "visible": True,
                        "view": True,
                        "create": False,
                        "edit": True,
                        "delete": False,
                        "approve": True,
                    },
                    "fulfillment": {
                        "visible": True,
                        "view": True,
                        "create": True,
                        "edit": True,
                        "delete": False,
                        "approve": False,
                    },
                }
            ),
        },
    ]


def ensure_default_inventory_roles() -> None:
    now = datetime.utcnow()
    defaults = default_role_docs()
    default_map = {role["id"]: role for role in defaults}

    inventory_roles_col.delete_many({"id": {"$nin": list(MANAGED_ROLE_IDS)}})

    for role in defaults:
        inventory_roles_col.update_one(
            {"id": role["id"]},
            {
                "$setOnInsert": {
                    **role,
                    "created_at": now,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    users_col.update_many(
        {
            "role": "inventory",
            "$or": [
                {"inventory_role_id": {"$exists": False}},
                {"inventory_role_id": {"$nin": ["warehouse-manager", "inventory-user"]}},
            ],
            "main_admin": {"$ne": True},
        },
        {
            "$set": {
                "inventory_role_id": "inventory-user",
                "inventory_role_name": default_map["inventory-user"]["name"],
                "updated_at": now,
            }
        },
    )


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return False


def normalize_permissions(permissions: dict[str, Any] | None) -> dict[str, dict[str, bool]]:
    normalized: dict[str, dict[str, bool]] = {}
    permissions = permissions or {}
    for page in SYSTEM_PAGES:
        page_id = page["id"]
        current = permissions.get(page_id) or {}
        if page_id == "undelivered-items" and page_id not in permissions:
            current = {"visible": True, "view": True, "edit": True}
        normalized[page_id] = {
            "visible": bool(current.get("visible")),
            "view": bool(current.get("view")),
            "create": bool(current.get("create")),
            "edit": bool(current.get("edit")),
            "delete": bool(current.get("delete")),
            "approve": bool(current.get("approve")),
        }
    return normalized


def serialize_role_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id") or str(doc.get("_id") or ""),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "template": bool(doc.get("template")),
        "permissions": normalize_permissions(doc.get("permissions")),
    }


def get_inventory_roles() -> list[dict[str, Any]]:
    # Reads must not provision roles or rewrite users. Keep the same safe defaults.
    roles = {doc['id']: doc for doc in default_role_docs()}
    roles.update({doc['id']: doc for doc in inventory_roles_col.find({'id': {'$in': list(MANAGED_ROLE_IDS)}})})
    docs = sorted(roles.values(), key=lambda doc: (not doc.get('template'), doc.get('name', '')))
    return [serialize_role_doc(doc) for doc in docs]


def get_inventory_role_map() -> dict[str, dict[str, Any]]:
    return {role["id"]: role for role in get_inventory_roles()}


def _safe_object_id(value: str | None) -> ObjectId | None:
    if value and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def get_inventory_user_doc(user_id: str | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    oid = _safe_object_id(user_id)
    if oid is not None:
        return users_col.find_one({"_id": oid, "role": "inventory"})
    return users_col.find_one({"_id": user_id, "role": "inventory"})


def get_effective_inventory_role(user_doc: dict[str, Any] | None, role_map: dict[str, Any] | None = None) -> dict[str, Any]:
    role_map = role_map or get_inventory_role_map()
    if is_truthy((user_doc or {}).get("main_admin")):
        return role_map.get("admin") or serialize_role_doc(default_role_docs()[0])

    role_id = (user_doc or {}).get("inventory_role_id") or "inventory-user"
    return role_map.get(role_id) or role_map.get("inventory-user") or serialize_role_doc(default_role_docs()[1])


def _format_dt(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if isinstance(value, datetime):
        return value.strftime(fmt)
    return ""


def _format_date_only(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value
    return ""


def get_last_login_text(user_id: str) -> str:
    row = login_logs_col.find_one({"user_id": str(user_id)}, sort=[("timestamp", -1)])
    if not row:
        return "-"
    return _format_dt(row.get("timestamp")) or "-"


def serialize_inventory_user(user_doc: dict[str, Any], role_map: dict[str, Any] | None = None) -> dict[str, Any]:
    role = get_effective_inventory_role(user_doc, role_map=role_map)
    user_id = str(user_doc.get("_id") or "")
    status_raw = (user_doc.get("status") or "").strip().lower()
    status = "active" if status_raw == "active" else "disabled"
    return {
        "id": user_id,
        "username": user_doc.get("username") or "",
        "name": user_doc.get("name") or "",
        "email": user_doc.get("email") or "",
        "phone": user_doc.get("phone") or "",
        "roleId": role.get("id") or "inventory-user",
        "roleName": role.get("name") or "Inventory User",
        "branch": user_doc.get("branch") or "",
        "status": status,
        "lastLogin": get_last_login_text(user_id),
        "position": user_doc.get("position") or "",
        "location": user_doc.get("location") or "",
        "gender": user_doc.get("gender") or "",
        "startDate": _format_date_only(user_doc.get("start_date")),
        "mainAdmin": is_truthy(user_doc.get("main_admin")),
        "imageUrl": user_doc.get("image_url") or "",
    }


def _branch_code(branch_name: str) -> str:
    cleaned = "".join(ch for ch in (branch_name or "").upper() if ch.isalnum())
    return cleaned[:6] or "BRANCH"


def serialize_branch_doc(manager_doc: dict[str, Any], locations: list[dict[str, Any]]) -> dict[str, Any]:
    branch_name = (manager_doc.get("branch") or "").strip()
    return {
        "id": branch_name,
        "name": branch_name,
        "code": _branch_code(branch_name),
        "manager": manager_doc.get("name") or manager_doc.get("username") or "Manager",
        "location": manager_doc.get("location") or "",
        "phone": manager_doc.get("phone") or "",
        "status": "active" if (manager_doc.get("status") or "").strip().lower() == "active" else "inactive",
        "totalWarehouses": len(locations),
        "totalStockUnits": sum(int(item.get("stockUnits") or 0) for item in locations),
    }


def serialize_location_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or ""),
        "branchId": doc.get("branch") or "",
        "name": doc.get("name") or "",
        "code": doc.get("code") or "",
        "type": doc.get("type") or "room",
        "responsibleUser": doc.get("responsible_user") or "",
        "stockUnits": int(doc.get("stock_units") or 0),
        "capacity": int(doc.get("capacity") or 0),
        "status": "active" if (doc.get("status") or "").strip().lower() == "active" else "inactive",
        "notes": doc.get("notes") or "",
    }


def get_branch_manager_docs() -> list[dict[str, Any]]:
    rows = list(
        users_col.find(
            {"role": "manager", "branch": {"$exists": True, "$ne": ""}},
            {"name": 1, "username": 1, "branch": 1, "location": 1, "phone": 1, "status": 1},
        ).sort([("branch", 1), ("name", 1)])
    )
    branch_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        branch_name = (row.get("branch") or "").strip()
        if branch_name and branch_name not in branch_map:
            branch_map[branch_name] = row
    return list(branch_map.values())


def get_branch_locations_map() -> dict[str, list[dict[str, Any]]]:
    rows = list(inventory_locations_col.find({}).sort([("branch", 1), ("name", 1)]))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        branch_name = (row.get("branch") or "").strip()
        if not branch_name:
            continue
        grouped.setdefault(branch_name, []).append(serialize_location_doc(row))
    return grouped


def get_branches_payload() -> dict[str, Any]:
    locations_map = get_branch_locations_map()
    managers = get_branch_manager_docs()
    branches = [serialize_branch_doc(manager, locations_map.get((manager.get("branch") or "").strip(), [])) for manager in managers]
    return {
        "branches": branches,
        "locations": locations_map,
    }
