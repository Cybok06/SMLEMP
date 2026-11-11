from flask import Blueprint, render_template, request, jsonify, session
from flask_login import current_user
from bson import ObjectId
from datetime import datetime, timezone, timedelta, time
import random
import string
import re

from db import db
from card_sales_helper import sold_counts_by_name

cards_tracker_bp = Blueprint('cards_tracker', __name__)

# Collections
products_col = db["products"]
users_col = db["users"]
customers_col = db["customers"]
instant_sales_col = db["instant_sales"]

stock_col = db["product_cards_stock"]
balances_col = db["product_cards_balances"]
transfers_col = db["product_cards_transfers"]
manual_sales_col = db["product_cards_sales"]

# New cards tracker collections (name-based)
exec_stock_col = db["cards_stock_exec"]
manager_stock_col = db["cards_stock_manager"]
agent_stock_col = db["cards_stock_agent"]
cards_transfers_col = db["cards_transfers"]
cards_adjustments_col = db["cards_adjustments"]


# -------------------------
# Helpers
# -------------------------

def _now_utc():
    return datetime.now(timezone.utc)


def normalize_product_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _safe_oid(val):
    try:
        return ObjectId(str(val))
    except Exception:
        return None


def _id_variants(val):
    if val is None:
        return []
    if isinstance(val, ObjectId):
        return [val, str(val)]
    sval = str(val).strip()
    if ObjectId.is_valid(sval):
        return [ObjectId(sval), sval]
    return [sval]


def _actor_from_session():
    if session.get("executive_id"):
        return "executive", str(session.get("executive_id"))
    if session.get("manager_id"):
        return "manager", str(session.get("manager_id"))
    if session.get("agent_id"):
        return "agent", str(session.get("agent_id"))
    if current_user and getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "role", ""), str(getattr(current_user, "id", ""))
    return "", ""


def _require_role(role):
    role_from_session, _ = _actor_from_session()
    if role_from_session == role:
        return True
    if current_user and getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "role", "") == role
    return False


def _ensure_indexes():
    try:
        stock_col.create_index([("product_key", 1)], unique=True)
        stock_col.create_index([("product_id", 1)])
        balances_col.create_index([("owner_type", 1), ("owner_id", 1), ("product_key", 1)], unique=True)
        balances_col.create_index([("owner_type", 1), ("owner_id", 1), ("product_id", 1)])
        transfers_col.create_index([("product_id", 1), ("created_at", -1)])
        transfers_col.create_index([("product_key", 1), ("created_at", -1)])
        transfers_col.create_index([("from_id", 1), ("to_id", 1)])
        manual_sales_col.create_index([("product_id", 1), ("agent_id", 1), ("created_at", -1)])
        exec_stock_col.create_index([("product_key", 1)], unique=True)
        manager_stock_col.create_index([("manager_id", 1), ("product_key", 1)], unique=True)
        agent_stock_col.create_index([("agent_id", 1), ("product_key", 1)], unique=True)
        cards_transfers_col.create_index([("product_key", 1), ("created_at", -1)])
        cards_transfers_col.create_index([("from_id", 1), ("to_id", 1)])
        cards_adjustments_col.create_index([("product_key", 1), ("created_at", -1)])
    except Exception:
        pass


_ensure_indexes()


def _transfer_id():
    stamp = _now_utc().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"CARD-TRF-{stamp}-{suffix}"


def _product_list_dedup():
    rows = list(products_col.find({}, {"name": 1, "image_url": 1, "created_at": 1}).limit(6000))
    dedup = {}
    for r in rows:
        name = r.get("name") or ""
        key = normalize_product_name(name)
        if not key:
            continue
        current = dedup.get(key)
        has_image = bool((r.get("image_url") or "").strip())
        if not current:
            dedup[key] = r
            continue
        cur_has_image = bool((current.get("image_url") or "").strip())
        if has_image and not cur_has_image:
            dedup[key] = r
            continue
        if has_image == cur_has_image:
            if (r.get("created_at") or 0) > (current.get("created_at") or 0):
                dedup[key] = r

    out = []
    for key, r in dedup.items():
        out.append({
            "product_key": key,
            "name": r.get("name") or "Unnamed",
            "image_url": r.get("image_url") or "",
            "product_id": str(r.get("_id")) if r.get("_id") else "",
        })
    out.sort(key=lambda x: x.get("name") or "")
    return out


def _product_by_key(product_key: str):
    if not product_key:
        return None
    norm = normalize_product_name(product_key)
    safe = re.escape(product_key)
    rows = list(products_col.find({"name": {"$regex": f"^{safe}$", "$options": "i"}}, {"name": 1, "image_url": 1, "created_at": 1}).limit(20))
    if not rows:
        rows = list(products_col.find({"name": {"$regex": f"{safe}", "$options": "i"}}, {"name": 1, "image_url": 1, "created_at": 1}).limit(50))
    best = None
    for r in rows:
        if normalize_product_name(r.get("name") or "") != norm:
            continue
        if not best:
            best = r
            continue
        has_image = bool((r.get("image_url") or "").strip())
        best_has_image = bool((best.get("image_url") or "").strip())
        if has_image and not best_has_image:
            best = r
            continue
        if has_image == best_has_image and (r.get("created_at") or 0) > (best.get("created_at") or 0):
            best = r
    return best


def _exec_stock_for_key(product_key: str):
    row = exec_stock_col.find_one({"product_key": product_key})
    if row:
        return row
    fallback = stock_col.find_one({"product_key": product_key})
    if not fallback:
        fallback = stock_col.find_one({"product_name": {"$regex": f"^{re.escape(product_key)}$", "$options": "i"}})
    if fallback:
        return {
            "product_key": product_key,
            "product_name": fallback.get("product_name") or fallback.get("product_name_snapshot") or "",
            "image_url": fallback.get("image_url") or "",
            "total_qty": int(fallback.get("stock_total", 0) or 0),
            "available_qty": int(fallback.get("stock_available", 0) or 0),
        }
    return None


def _upsert_exec_stock(product_key: str, product_name: str, image_url: str, delta_qty: int, set_only=False):
    now = _now_utc()
    if set_only:
        exec_stock_col.update_one(
            {"product_key": product_key},
            {"$set": {
                "product_name": product_name,
                "image_url": image_url,
                "total_qty": max(delta_qty, 0),
                "available_qty": max(delta_qty, 0),
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return
    exec_stock_col.update_one(
        {"product_key": product_key},
        {"$inc": {"total_qty": delta_qty, "available_qty": delta_qty},
         "$set": {"product_name": product_name, "image_url": image_url, "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


def _manager_stock_row(manager_id: str, product_key: str):
    return manager_stock_col.find_one({"manager_id": manager_id, "product_key": product_key})


def _agent_stock_row(agent_id: str, product_key: str):
    return agent_stock_col.find_one({"agent_id": agent_id, "product_key": product_key})


def _safe_date_str(val: str) -> str:
    if val and len(val) == 10:
        return val
    return _now_utc().strftime("%Y-%m-%d")


def _latest_transfers_map(product_keys, from_role=None, from_id=None):
    if not product_keys:
        return {}
    query = {"product_key": {"$in": list(set(product_keys))}}
    if from_role:
        query["from_role"] = from_role
    if from_id:
        query["from_id"] = from_id
    rows = list(cards_transfers_col.find(query).sort("created_at", -1).limit(500))
    out = {}
    for row in rows:
        key = row.get("product_key") or ""
        if not key or key in out:
            continue
        out[key] = {
            "transfer_date": row.get("transfer_date") or "",
            "qty": int(row.get("qty", 0) or 0),
            "to_label": row.get("manager_name") or row.get("agent_name") or row.get("to_id") or "",
        }
    return out


def _exec_transfer_to_manager(data):
    product_key = normalize_product_name(data.get("product_key") or "")
    product_name = (data.get("product_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    manager_id = (data.get("manager_id") or "").strip()
    note = (data.get("note") or "").strip()
    transfer_date = _safe_date_str((data.get("transfer_date") or "").strip())
    qty_raw = data.get("qty")

    try:
        qty = int(qty_raw)
    except Exception:
        return False, "Invalid quantity"
    if qty <= 0:
        return False, "Quantity must be positive"
    if not manager_id:
        return False, "Manager is required"

    if not product_key:
        product = _product_by_key(product_name) if product_name else None
        if product:
            product_key = normalize_product_name(product.get("name") or "")
            product_name = product.get("name") or product_name
            image_url = product.get("image_url") or image_url
    if not product_key:
        return False, "Product key is required"

    manager_doc = _manager_doc(manager_id)
    if not manager_doc:
        return False, "Manager not found"

    now = _now_utc()

    # Ensure exec stock exists
    exec_row = exec_stock_col.find_one({"product_key": product_key})
    if not exec_row:
        legacy = stock_col.find_one({"product_key": product_key}) or {}
        total_qty = int(legacy.get("stock_total", 0) or 0)
        avail_qty = int(legacy.get("stock_available", 0) or 0)
        exec_stock_col.insert_one({
            "product_key": product_key,
            "product_name": product_name,
            "image_url": image_url,
            "total_qty": total_qty,
            "available_qty": avail_qty,
            "created_at": now,
            "updated_at": now,
        })

    # Atomic decrement from executive pool
    update_res = exec_stock_col.update_one(
        {"product_key": product_key, "available_qty": {"$gte": qty}},
        {"$inc": {"available_qty": -qty}, "$set": {"updated_at": now}},
    )
    if update_res.modified_count == 0:
        return False, "Not enough stock"

    stock_col.update_one(
        {"product_key": product_key, "stock_available": {"$gte": qty}},
        {"$inc": {"stock_available": -qty}, "$set": {"updated_at": now}},
    )

    # Update manager stock
    manager_stock_col.update_one(
        {"manager_id": manager_id, "product_key": product_key},
        {"$inc": {"received_qty": qty, "available_qty": qty},
         "$set": {
             "product_name": product_name,
             "image_url": image_url,
             "manager_name": manager_doc.get("name") or "Manager",
             "manager_branch": manager_doc.get("branch") or "",
             "updated_at": now,
         },
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    # Backward compatibility balances
    balances_col.update_one(
        {"owner_type": "manager", "owner_id": manager_id, "product_key": product_key},
        {"$inc": {"given_in_total": qty, "available": qty}, "$set": {"updated_at": now}},
        upsert=True,
    )

    role, actor_id = _actor_from_session()
    transfer_doc = {
        "transfer_id": _transfer_id(),
        "product_key": product_key,
        "product_name": product_name,
        "image_url": image_url,
        "manager_id": manager_id,
        "manager_name": manager_doc.get("name") or "Manager",
        "manager_branch": manager_doc.get("branch") or "",
        "qty": qty,
        "transfer_date": transfer_date,
        "from_role": "executive",
        "from_id": actor_id,
        "to_role": "manager",
        "to_id": manager_id,
        "note": note,
        "created_at": now,
        "by_role": role,
        "by_user_id": actor_id,
        "meta": {
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": request.headers.get("User-Agent"),
        },
    }
    cards_transfers_col.insert_one(transfer_doc)

    exec_row = exec_stock_col.find_one({"product_key": product_key}) or {}
    manager_row = manager_stock_col.find_one({"manager_id": manager_id, "product_key": product_key}) or {}
    return True, {
        "message": "Transfer successful",
        "product_key": product_key,
        "manager_id": manager_id,
        "agent_id": "",
        "qty": qty,
        "new_exec_qty": int(exec_row.get("available_qty", 0) or 0),
        "new_manager_qty": int(manager_row.get("available_qty", 0) or 0),
        "new_agent_qty": 0,
    }


def _manager_transfer_to_agent(data, manager_id):
    product_key = normalize_product_name(data.get("product_key") or "")
    product_name = (data.get("product_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    agent_id = (data.get("agent_id") or "").strip()
    note = (data.get("note") or "").strip()
    transfer_date = _safe_date_str((data.get("transfer_date") or "").strip())
    qty_raw = data.get("qty")

    try:
        qty = int(qty_raw)
    except Exception:
        return False, "Invalid quantity"
    if qty <= 0:
        return False, "Quantity must be positive"
    if not agent_id:
        return False, "Agent is required"

    if not product_key:
        product = _product_by_key(product_name) if product_name else None
        if product:
            product_key = normalize_product_name(product.get("name") or "")
            product_name = product.get("name") or product_name
            image_url = product.get("image_url") or image_url
    if not product_key:
        return False, "Product key is required"

    agent_doc = _agent_doc(agent_id)
    if not agent_doc:
        return False, "Agent not found"
    agent_mgr = agent_doc.get("manager_id")
    if str(agent_mgr) not in {manager_id, str(_safe_oid(manager_id))}:
        return False, "Agent not under this manager"

    now = _now_utc()

    # Decrement manager available
    update_res = manager_stock_col.update_one(
        {"manager_id": manager_id, "product_key": product_key, "available_qty": {"$gte": qty}},
        {"$inc": {"available_qty": -qty}, "$set": {"updated_at": now}},
    )
    if update_res.modified_count == 0:
        # fallback to legacy balance
        legacy = balances_col.update_one(
            {"owner_type": "manager", "owner_id": manager_id, "product_key": product_key, "available": {"$gte": qty}},
            {"$inc": {"available": -qty, "given_out_total": qty}, "$set": {"updated_at": now}},
        )
        if legacy.modified_count == 0:
            return False, "Not enough available cards"

    # Update agent stock
    agent_stock_col.update_one(
        {"agent_id": agent_id, "product_key": product_key},
        {"$inc": {"received_qty": qty, "available_qty": qty},
         "$set": {
             "product_name": product_name,
             "image_url": image_url,
             "manager_id": manager_id,
             "agent_name": agent_doc.get("name") or "Agent",
             "branch": agent_doc.get("branch") or "",
             "updated_at": now,
             "last_transfer_date": transfer_date,
         },
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    # Backward compatibility balances
    balances_col.update_one(
        {"owner_type": "agent", "owner_id": agent_id, "product_key": product_key},
        {"$inc": {"given_in_total": qty, "available": qty}, "$set": {"updated_at": now}},
        upsert=True,
    )

    role, actor_id = _actor_from_session()
    transfer_doc = {
        "transfer_id": _transfer_id(),
        "product_key": product_key,
        "product_name": product_name,
        "image_url": image_url,
        "manager_id": manager_id,
        "qty": qty,
        "transfer_date": transfer_date,
        "from_role": "manager",
        "from_id": manager_id,
        "to_role": "agent",
        "to_id": agent_id,
        "agent_name": agent_doc.get("name") or "Agent",
        "agent_branch": agent_doc.get("branch") or "",
        "note": note,
        "created_at": now,
        "by_role": role,
        "by_user_id": actor_id,
    }
    cards_transfers_col.insert_one(transfer_doc)

    manager_row = manager_stock_col.find_one({"manager_id": manager_id, "product_key": product_key}) or {}
    agent_row = agent_stock_col.find_one({"agent_id": agent_id, "product_key": product_key}) or {}
    return True, {
        "message": "Transfer successful",
        "product_key": product_key,
        "manager_id": manager_id,
        "agent_id": agent_id,
        "qty": qty,
        "new_exec_qty": 0,
        "new_manager_qty": int(manager_row.get("available_qty", 0) or 0),
        "new_agent_qty": int(agent_row.get("available_qty", 0) or 0),
    }


def _adjust_stock(owner_role: str, owner_id: str, product_key: str, product_name: str, image_url: str, delta_qty: int, note: str, date_str: str):
    now = _now_utc()
    if owner_role == "executive":
        row = exec_stock_col.find_one({"product_key": product_key}) or {"total_qty": 0, "available_qty": 0}
        current = int(row.get("available_qty", 0) or 0)
        total = int(row.get("total_qty", 0) or 0)
        if delta_qty < 0 and current + delta_qty < 0:
            return False, "Insufficient stock"
        new_total = max(total + delta_qty, 0)
        new_available = max(current + delta_qty, 0)
        exec_stock_col.update_one(
            {"product_key": product_key},
            {"$set": {
                "product_name": product_name,
                "image_url": image_url,
                "total_qty": new_total,
                "available_qty": new_available,
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        stock_col.update_one(
            {"product_key": product_key},
            {"$set": {
                "product_name": product_name,
                "image_url": image_url,
                "stock_total": new_total,
                "stock_available": new_available,
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        resulting = new_available
    elif owner_role == "manager":
        row = manager_stock_col.find_one({"manager_id": owner_id, "product_key": product_key}) or {"available_qty": 0, "received_qty": 0}
        current = int(row.get("available_qty", 0) or 0)
        received = int(row.get("received_qty", 0) or 0)
        if delta_qty < 0 and current + delta_qty < 0:
            return False, "Insufficient stock"
        new_available = max(current + delta_qty, 0)
        new_received = max(received + delta_qty, 0)
        manager_stock_col.update_one(
            {"manager_id": owner_id, "product_key": product_key},
            {"$set": {
                "product_name": product_name,
                "image_url": image_url,
                "available_qty": new_available,
                "received_qty": new_received,
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        balances_col.update_one(
            {"owner_type": "manager", "owner_id": owner_id, "product_key": product_key},
            {"$set": {"available": new_available, "given_in_total": new_received, "updated_at": now}},
            upsert=True,
        )
        resulting = new_available
    else:
        return False, "Invalid owner"

    cards_adjustments_col.insert_one({
        "product_key": product_key,
        "product_name": product_name,
        "image_url": image_url,
        "delta_qty": delta_qty,
        "resulting_qty": resulting,
        "actor_id": owner_id,
        "actor_role": owner_role,
        "date": date_str,
        "created_at": now,
        "note": note,
    })
    return True, ""


def _utc_day_bounds():
    today = _now_utc().date()
    start_dt = datetime.combine(today, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(today + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt


def _sold_total_by_key_today(manager_id=None):
    start_dt, end_dt = _utc_day_bounds()
    base = sold_counts_by_name(
        customers_col,
        instant_sales_col,
        agent_id=None,
        manager_id=manager_id,
        product_name=None,
        start_dt=start_dt,
        end_dt=end_dt,
        group_by_agent=False,
    ).get("total", {})
    out = {}
    for _, entry in base.items():
        key = normalize_product_name(entry.get("name") or "")
        if not key:
            continue
        out[key] = int(out.get(key, 0) or 0) + int(entry.get("count", 0) or 0)
    return out


def _sold_total_by_key_all(manager_id=None):
    base = sold_counts_by_name(
        customers_col,
        instant_sales_col,
        agent_id=None,
        manager_id=manager_id,
        product_name=None,
        start_dt=None,
        end_dt=None,
        group_by_agent=False,
    ).get("total", {})
    out = {}
    for _, entry in base.items():
        key = normalize_product_name(entry.get("name") or "")
        if not key:
            continue
        out[key] = int(out.get(key, 0) or 0) + int(entry.get("count", 0) or 0)
    return out


def _product_doc(product_id):
    oid = _safe_oid(product_id)
    if not oid:
        return None
    return products_col.find_one({"_id": oid}, {"name": 1, "image_url": 1, "created_at": 1})


def _manager_agent_query(manager_id_str):
    mid = _safe_oid(manager_id_str)
    if mid:
        return {"role": "agent", "$or": [{"manager_id": mid}, {"manager_id": manager_id_str}]}
    return {"role": "agent", "manager_id": manager_id_str}


def _agent_docs_for_manager(manager_id_str):
    return list(users_col.find(_manager_agent_query(manager_id_str), {"name": 1, "branch": 1, "manager_id": 1}))


def _manager_doc(manager_id_str):
    mo = _safe_oid(manager_id_str)
    if mo:
        return users_col.find_one({"_id": mo}, {"name": 1, "branch": 1})
    return users_col.find_one({"_id": manager_id_str}, {"name": 1, "branch": 1})


def _agent_doc(agent_id_str):
    ao = _safe_oid(agent_id_str)
    if ao:
        return users_col.find_one({"_id": ao}, {"name": 1, "branch": 1, "manager_id": 1})
    return users_col.find_one({"_id": agent_id_str}, {"name": 1, "branch": 1, "manager_id": 1})


def _sold_counts_for_product(product_name, manager_id=None, agent_id=None):
    base = sold_counts_by_name(
        customers_col,
        instant_sales_col,
        agent_id=agent_id,
        manager_id=manager_id,
        product_name=product_name,
        start_dt=None,
        end_dt=None,
        group_by_agent=True,
    )
    sold_map = {}
    product_key = normalize_product_name(product_name)
    for key, entry in base.get("total", {}).items():
        if not isinstance(key, tuple):
            continue
        agent_key, name_key = key
        if product_key and normalize_product_name(name_key) != product_key:
            continue
        sold_map[str(agent_key)] = int(entry.get("count", 0) or 0)
    return sold_map


def _sold_total_by_key(manager_id=None):
    base = sold_counts_by_name(
        customers_col,
        instant_sales_col,
        agent_id=None,
        manager_id=manager_id,
        product_name=None,
        start_dt=None,
        end_dt=None,
        group_by_agent=False,
    ).get("total", {})
    out = {}
    for _, entry in base.items():
        key = normalize_product_name(entry.get("name") or "")
        if not key:
            continue
        out[key] = int(out.get(key, 0) or 0) + int(entry.get("count", 0) or 0)
    # Merge manual sales if present
    manual_match = {}
    if manager_id:
        manual_match["manager_id"] = manager_id
    manual_rows = list(manual_sales_col.aggregate([
        {"$match": manual_match},
        {"$group": {"_id": "$product_id", "total": {"$sum": "$qty_used"}}},
    ]))
    for row in manual_rows:
        pid = row.get("_id")
        prod = _product_doc(pid)
        if not prod:
            continue
        key = normalize_product_name(prod.get("name") or "")
        if not key:
            continue
        out[key] = int(out.get(key, 0) or 0) + int(row.get("total", 0) or 0)

    return out


def _manual_sales_map(product_id, agent_ids=None):
    if not product_id:
        return {}
    match = {"product_id": product_id}
    if agent_ids:
        match["agent_id"] = {"$in": agent_ids}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$agent_id", "total": {"$sum": "$qty_used"}}},
    ]
    rows = list(manual_sales_col.aggregate(pipeline))
    return {str(r.get("_id")): int(r.get("total", 0) or 0) for r in rows}


def _merge_sold_maps(primary, extra):
    merged = dict(primary or {})
    for k, v in (extra or {}).items():
        merged[k] = int(merged.get(k, 0) or 0) + int(v or 0)
    return merged


# -------------------------
# Executive Routes
# -------------------------


@cards_tracker_bp.route("/executive/cards-tracker", methods=["GET"])
def executive_cards_tracker():
    if not _require_role("executive"):
        return "Unauthorized", 403

    products = _product_list_dedup()
    managers = list(users_col.find({"role": "manager"}, {"name": 1, "branch": 1}).sort("name", 1))
    return render_template(
        "executive/cards_tracker.html",
        products=products,
        managers=managers,
    )


@cards_tracker_bp.route("/executive/cards-tracker/data", methods=["GET"])
def executive_cards_tracker_data():
    if not _require_role("executive"):
        return jsonify(ok=False, error="Unauthorized"), 403

    product_key = normalize_product_name(request.args.get("product_key") or "")
    product_id = (request.args.get("product_id") or "").strip()

    products = _product_list_dedup()
    if not products:
        return jsonify(ok=True, product=None, products=[], stock=None, managers=[], ledger=[], kpis={})

    if not product_key and product_id:
        product = _product_doc(product_id)
        if product:
            product_key = normalize_product_name(product.get("name") or "")

    if not product_key:
        product_key = normalize_product_name(products[0]["name"])

    product = _product_by_key(product_key)
    if not product:
        return jsonify(ok=False, error="Product not found"), 404

    pid = product.get("_id")
    stock = _exec_stock_for_key(product_key) or {}

    # Per-card summary for grid
    sold_totals_all = _sold_total_by_key(manager_id=None)
    sold_today_all = _sold_total_by_key_today(manager_id=None)

    manager_rows_all = list(manager_stock_col.find({}))
    manager_received_map = {}
    for row in manager_rows_all:
        key = row.get("product_key") or ""
        if not key:
            continue
        manager_received_map[key] = int(manager_received_map.get(key, 0) or 0) + int(row.get("received_qty", 0) or 0)

    stock_rows = list(exec_stock_col.find({}))
    stock_map = {r.get("product_key"): r for r in stock_rows if r.get("product_key")}
    if not stock_map:
        legacy_rows = list(stock_col.find({}))
        for r in legacy_rows:
            key = normalize_product_name(r.get("product_name") or "") or r.get("product_key")
            if not key:
                continue
            stock_map[key] = {
                "product_key": key,
                "product_name": r.get("product_name") or r.get("product_name_snapshot") or "",
                "image_url": r.get("image_url") or "",
                "total_qty": int(r.get("stock_total", 0) or 0),
                "available_qty": int(r.get("stock_available", 0) or 0),
            }

    product_keys = [p.get("product_key") for p in products if p.get("product_key")]
    latest_transfer_map = _latest_transfers_map(product_keys, from_role="executive")
    products_with_metrics = []
    for p in products:
        key = p.get("product_key")
        stock_row = stock_map.get(key, {}) if key else {}
        last_transfer = latest_transfer_map.get(key, {})
        products_with_metrics.append({
            **p,
            "stock_total": int(stock_row.get("total_qty", stock_row.get("stock_total", 0)) or 0),
            "stock_available": int(stock_row.get("available_qty", stock_row.get("stock_available", 0)) or 0),
            "transferred_total": int(manager_received_map.get(key, 0) or 0),
            "sold_total": int(sold_totals_all.get(key, 0) or 0),
            "sold_today": int(sold_today_all.get(key, 0) or 0),
            "last_transfer": last_transfer,
        })

    manager_balances = list(manager_stock_col.find({"product_key": product_key}))
    if not manager_balances:
        manager_balances = list(balances_col.find({
            "owner_type": "manager",
            "$or": [
                {"product_key": product_key},
                {"product_id": pid},
            ]
        }))
    manager_ids = [str(b.get("owner_id") or "") for b in manager_balances if b.get("owner_id")]
    manager_ids = list({m for m in manager_ids if m})

    # Managers lookup
    manager_variants = []
    for mid in manager_ids:
        manager_variants.extend(_id_variants(mid))
    manager_variants = list({m for m in manager_variants})

    managers = list(users_col.find({"role": "manager", "_id": {"$in": manager_variants}}, {"name": 1, "branch": 1}))
    manager_map = {str(m.get("_id")): m for m in managers}

    # Agents under those managers
    manager_oids = [m for m in manager_variants if isinstance(m, ObjectId)]
    manager_strs = [str(m) for m in manager_variants if not isinstance(m, ObjectId)]
    agent_query = {"role": "agent"}
    if manager_oids or manager_strs:
        agent_query["$or"] = []
        if manager_oids:
            agent_query["$or"].append({"manager_id": {"$in": manager_oids}})
        if manager_strs:
            agent_query["$or"].append({"manager_id": {"$in": manager_strs}})
    agents = list(users_col.find(agent_query, {"name": 1, "branch": 1, "manager_id": 1}))
    agent_map = {str(a.get("_id")): a for a in agents}

    # Sold counts by agent (sales-derived + manual)
    sold_by_agent = _sold_counts_for_product(product.get("name") or "", manager_id=None, agent_id=None)
    sold_manual = _manual_sales_map(pid)
    sold_by_agent = _merge_sold_maps(sold_by_agent, sold_manual)

    # Agent balances
    agent_balance_map = {}
    agent_balances = list(agent_stock_col.find({"product_key": product_key}))
    for a in agent_balances:
        agent_balance_map[str(a.get("agent_id") or "")] = a
    legacy_agent_balances = list(balances_col.find({
        "owner_type": "agent",
        "$or": [
            {"product_key": product_key},
            {"product_id": pid},
        ]
    }))
    for a in legacy_agent_balances:
        key = str(a.get("owner_id") or "")
        if key not in agent_balance_map:
            agent_balance_map[key] = a

    # Manager breakdown
    manager_rows = []
    total_transferred = 0
    total_sold = 0
    for mb in manager_balances:
        mid = str(mb.get("manager_id") or mb.get("owner_id") or "")
        manager_doc = manager_map.get(mid) or _manager_doc(mid) or {}
        received = int(mb.get("received_qty", mb.get("given_in_total", 0)) or 0)
        given_to_agents = int(mb.get("given_out_total", 0) or 0)
        manager_available = int(mb.get("available_qty", mb.get("available", 0)) or 0)

        # Agents under this manager
        mgr_agents = [a for a in agents if str(a.get("manager_id")) == mid or str(a.get("manager_id")) == str(_safe_oid(mid))]
        sold_total = 0
        agents_left_total = 0
        for ag in mgr_agents:
            aid = str(ag.get("_id"))
            sold = int(sold_by_agent.get(aid, 0) or 0)
            bal = agent_balance_map.get(aid, {})
            given = int(bal.get("received_qty", bal.get("given_in_total", 0)) or 0)
            left = int(bal.get("available_qty", bal.get("available", max(given - sold, 0))) or 0)
            sold_total += sold
            agents_left_total += left

        total_transferred += received
        total_sold += sold_total

        progress_pct = round((sold_total / received * 100) if received else 0, 2)
        manager_rows.append({
            "manager_id": mid,
            "manager_name": manager_doc.get("name") or "Manager",
            "branch": manager_doc.get("branch") or "",
            "received": received,
            "given_to_agents": given_to_agents,
            "sold_by_agents": sold_total,
            "remaining": manager_available + agents_left_total,
            "progress_pct": min(200, max(0, progress_pct)),
            "target_hit": sold_total >= received and received > 0,
        })

    # Executive KPIs
    stock_total = int(stock.get("stock_total", 0) or 0)
    stock_available = int(stock.get("stock_available", 0) or 0)

    # Ledger (recent)
    ledger = list(cards_transfers_col.find({"product_key": product_key}).sort("created_at", -1).limit(200))
    if not ledger:
        ledger = list(transfers_col.find({
            "$or": [
                {"product_key": product_key},
                {"product_id": pid},
            ]
        }).sort("created_at", -1).limit(200))
    ledger_rows = []
    for row in ledger:
        ledger_rows.append({
            "transfer_id": row.get("transfer_id"),
            "qty": int(row.get("qty", 0) or 0),
            "from_type": row.get("from_type") or row.get("from_role"),
            "from_id": str(row.get("from_id") or ""),
            "to_type": row.get("to_type") or row.get("to_role"),
            "to_id": str(row.get("to_id") or ""),
            "note": row.get("note") or "",
            "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
            "created_by_role": row.get("created_by_role"),
            "transfer_date": row.get("transfer_date") or "",
        })

    total_exec_available = 0
    total_exec_stock = 0
    for row in exec_stock_col.find({}):
        total_exec_available += int(row.get("available_qty", 0) or 0)
        total_exec_stock += int(row.get("total_qty", 0) or 0)
    if not total_exec_stock:
        total_exec_available = sum(int(p.get("stock_available", 0) or 0) for p in products_with_metrics)
        total_exec_stock = sum(int(p.get("stock_total", 0) or 0) for p in products_with_metrics)

    total_transferred_all = sum(int(r.get("received_qty", 0) or 0) for r in manager_stock_col.find({}))
    total_sold_today = sum(int(v or 0) for v in sold_today_all.values())

    return jsonify(
        ok=True,
        product={"product_key": product_key, "name": product.get("name") or "", "image_url": product.get("image_url") or ""},
        products=products_with_metrics,
        stock={"stock_total": int(stock.get("total_qty", stock.get("stock_total", 0)) or 0), "stock_available": int(stock.get("available_qty", stock.get("stock_available", 0)) or 0)},
        kpis={
            "total_stock": total_exec_stock,
            "transferred_to_managers": total_transferred_all,
            "sold_total_today": total_sold_today,
            "sold_total": total_sold_today,
            "remaining_exec": total_exec_available,
        },
        managers=manager_rows,
        ledger=ledger_rows,
    )


@cards_tracker_bp.route("/executive/cards-tracker/stock/set", methods=["POST"])
def executive_cards_tracker_stock_set():
    if not _require_role("executive"):
        return jsonify(ok=False, error="Unauthorized"), 403

    data = request.json or request.form
    product_key = normalize_product_name(data.get("product_key") or "")
    product_id = (data.get("product_id") or "").strip()
    mode = (data.get("mode") or "set").strip().lower()
    qty_raw = data.get("stock_total")
    try:
        qty = int(qty_raw)
    except Exception:
        return jsonify(ok=False, error="Invalid quantity"), 400
    if qty < 0:
        return jsonify(ok=False, error="Quantity must be non-negative"), 400

    product = _product_by_key(product_key) if product_key else _product_doc(product_id)
    if not product:
        return jsonify(ok=False, error="Product not found"), 404
    pid = product.get("_id")
    product_key = normalize_product_name(product.get("name") or "")

    now = _now_utc()
    existing = stock_col.find_one({"product_key": product_key}) or {}
    if not existing and pid:
        existing = stock_col.find_one({"product_id": pid}) or {}

    if mode == "add":
        stock_col.update_one(
            {"product_key": product_key},
            {
                "$inc": {"stock_total": qty, "stock_available": qty},
                "$set": {
                    "product_name": product.get("name") or "",
                    "image_url": product.get("image_url") or "",
                    "product_key": product_key,
                    "product_id": pid,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now, "created_by": str(session.get("executive_id") or "")},
            },
            upsert=True,
        )
        _upsert_exec_stock(product_key, product.get("name") or "", product.get("image_url") or "", qty, set_only=False)
        return jsonify(ok=True)

    # mode == set
    transferred_out = int(existing.get("stock_total", 0) or 0) - int(existing.get("stock_available", 0) or 0)
    if qty < transferred_out:
        return jsonify(ok=False, error="Cannot set below already transferred out"), 400
    new_available = qty - transferred_out

    stock_col.update_one(
        {"product_key": product_key},
        {
            "$set": {
                "stock_total": qty,
                "stock_available": new_available,
                "product_name": product.get("name") or "",
                "image_url": product.get("image_url") or "",
                "product_key": product_key,
                "product_id": pid,
                "updated_at": now,
                "created_by": str(session.get("executive_id") or ""),
                "created_at": existing.get("created_at") or now,
            }
        },
        upsert=True,
    )
    _upsert_exec_stock(product_key, product.get("name") or "", product.get("image_url") or "", qty, set_only=True)
    return jsonify(ok=True)


@cards_tracker_bp.route("/executive/cards/manager/<manager_id>/grid", methods=["GET"])
def executive_manager_cards_grid(manager_id):
    if not _require_role("executive"):
        return jsonify(ok=False, message="Unauthorized"), 403

    manager_doc = _manager_doc(manager_id)
    if not manager_doc:
        return jsonify(ok=False, message="Manager not found"), 404

    rows = list(manager_stock_col.find({"manager_id": manager_id}))
    if not rows:
        return jsonify(ok=True, manager={"id": manager_id, "name": manager_doc.get("name") or "Manager", "branch": manager_doc.get("branch") or ""}, cards=[], totals={"received": 0, "sold": 0, "available": 0})

    sold_totals = _sold_total_by_key_all(manager_id=manager_id)
    cards = []
    totals = {"received": 0, "sold": 0, "available": 0}
    for row in rows:
        key = row.get("product_key") or ""
        if not key:
            continue
        product_name = row.get("product_name") or ""
        image_url = row.get("image_url") or ""
        sold = int(sold_totals.get(key, 0) or 0)
        received = int(row.get("received_qty", 0) or 0)
        available = int(row.get("available_qty", 0) or 0)
        sold_pct = round((sold / received * 100) if received else 0, 2)
        cards.append({
            "product_key": key,
            "product_name": product_name or key.title(),
            "image_url": image_url,
            "received_qty": received,
            "sold_qty": sold,
            "available_qty": available,
            "sold_pct": min(200, max(0, sold_pct)),
        })
        totals["received"] += received
        totals["sold"] += sold
        totals["available"] += available

    return jsonify(
        ok=True,
        manager={"id": manager_id, "name": manager_doc.get("name") or "Manager", "branch": manager_doc.get("branch") or ""},
        cards=cards,
        totals=totals,
    )


@cards_tracker_bp.route("/executive/cards-tracker/transfer-to-manager", methods=["POST"])
@cards_tracker_bp.route("/executive/cards/transfer", methods=["POST"])
def executive_cards_tracker_transfer_to_manager():
    if not _require_role("executive"):
        return jsonify(ok=False, error="Unauthorized"), 403

    data = request.json or request.form
    ok, result = _exec_transfer_to_manager(data)
    if not ok:
        return jsonify(ok=False, message=result), 400
    return jsonify(ok=True, **result)


# -------------------------
# Manager Routes
# -------------------------


@cards_tracker_bp.route("/manager/cards-tracker", methods=["GET"])
def manager_cards_tracker():
    if not _require_role("manager"):
        return "Unauthorized", 403

    products = _product_list_dedup()
    _, manager_id = _actor_from_session()
    agents = _agent_docs_for_manager(manager_id) if manager_id else []
    return render_template(
        "manager/cards_tracker.html",
        products=products,
        agents=agents,
    )


@cards_tracker_bp.route("/manager/cards-tracker/data", methods=["GET"])
def manager_cards_tracker_data():
    if not _require_role("manager"):
        return jsonify(ok=False, error="Unauthorized"), 403

    _, manager_id = _actor_from_session()
    if not manager_id:
        return jsonify(ok=False, error="Manager not found"), 404

    product_key = normalize_product_name(request.args.get("product_key") or "")
    product_id = (request.args.get("product_id") or "").strip()

    products = _product_list_dedup()
    if not products:
        return jsonify(ok=True, product=None, products=[], summary={}, agents=[], ledger=[])

    if not product_key and product_id:
        product = _product_doc(product_id)
        if product:
            product_key = normalize_product_name(product.get("name") or "")

    if not product_key:
        product_key = normalize_product_name(products[0]["name"])

    product = _product_by_key(product_key)
    if not product:
        return jsonify(ok=False, error="Product not found"), 404

    pid = product.get("_id")

    agents = _agent_docs_for_manager(manager_id)
    agent_ids = [str(a.get("_id")) for a in agents]

    manager_balance = manager_stock_col.find_one({"manager_id": manager_id, "product_key": product_key}) or {}
    if not manager_balance:
        manager_balance = balances_col.find_one({
            "owner_type": "manager",
            "owner_id": manager_id,
            "$or": [
                {"product_key": product_key},
                {"product_id": pid},
            ]
        }) or {}

    # Per-card summary for grid
    sold_totals_all = _sold_total_by_key(manager_id=manager_id)
    manager_rows_all = list(manager_stock_col.find({"manager_id": manager_id}))
    manager_received_map = {}
    for row in manager_rows_all:
        key = row.get("product_key") or ""
        if not key:
            continue
        manager_received_map[key] = int(row.get("received_qty", 0) or 0)

    agent_rows_all = list(agent_stock_col.find({"manager_id": manager_id}))
    agent_received_map = {}
    agent_available_map = {}
    for row in agent_rows_all:
        key = row.get("product_key") or ""
        if not key:
            continue
        agent_received_map[key] = int(agent_received_map.get(key, 0) or 0) + int(row.get("received_qty", 0) or 0)
        agent_available_map[key] = int(agent_available_map.get(key, 0) or 0) + int(row.get("available_qty", 0) or 0)

    product_keys = [p.get("product_key") for p in products if p.get("product_key")]
    latest_transfer_map = _latest_transfers_map(product_keys, from_role="manager", from_id=manager_id)
    products_with_metrics = []
    for p in products:
        key = p.get("product_key")
        received = int(manager_received_map.get(key, 0) or 0)
        given_out = int(agent_received_map.get(key, 0) or 0)
        sold = int(sold_totals_all.get(key, 0) or 0)
        manager_pool = int((manager_stock_col.find_one({"manager_id": manager_id, "product_key": key}) or {}).get("available_qty", max(received - given_out, 0)) or 0)
        remaining = manager_pool + int(agent_available_map.get(key, 0) or 0)
        last_transfer = latest_transfer_map.get(key, {})
        products_with_metrics.append({
            **p,
            "received": received,
            "transferred_to_agents": given_out,
            "sold_total": sold,
            "remaining": remaining,
            "available_qty": manager_pool,
            "last_transfer": last_transfer,
        })

    sold_by_agent = _sold_counts_for_product(product.get("name") or "", manager_id=manager_id, agent_id=None)
    sold_manual = _manual_sales_map(pid, agent_ids)
    sold_by_agent = _merge_sold_maps(sold_by_agent, sold_manual)

    agent_balance_map = {}
    agent_rows = list(agent_stock_col.find({"product_key": product_key, "agent_id": {"$in": agent_ids}}))
    for row in agent_rows:
        agent_balance_map[str(row.get("agent_id") or "")] = row
    legacy_agent_balances = list(balances_col.find({
        "owner_type": "agent",
        "owner_id": {"$in": agent_ids},
        "$or": [
            {"product_key": product_key},
            {"product_id": pid},
        ]
    }))
    for a in legacy_agent_balances:
        key = str(a.get("owner_id") or "")
        if key not in agent_balance_map:
            agent_balance_map[key] = a

    rows = []
    total_received = sum(int(r.get("received_qty", 0) or 0) for r in manager_rows_all)
    total_transferred = sum(int(r.get("received_qty", 0) or 0) for r in agent_rows_all)
    total_sold = sum(int(v or 0) for v in sold_totals_all.values())
    total_left = sum(int(r.get("available_qty", 0) or 0) for r in manager_rows_all) + sum(int(r.get("available_qty", 0) or 0) for r in agent_rows_all)

    for ag in agents:
        aid = str(ag.get("_id"))
        bal = agent_balance_map.get(aid, {})
        given = int(bal.get("received_qty", bal.get("given_in_total", 0)) or 0)
        sold = int(sold_by_agent.get(aid, 0) or 0)
        left = int(bal.get("available_qty", max(given - sold, 0)) or 0)
        progress_pct = round((sold / given * 100) if given else 0, 2)
        rows.append({
            "agent_id": aid,
            "agent_name": ag.get("name") or "Agent",
            "branch": ag.get("branch") or "",
            "given": given,
            "sold": sold,
            "left": left,
            "progress_pct": min(200, max(0, progress_pct)),
            "target_hit": sold >= given and given > 0,
        })

    summary = {
        "received_total": total_received,
        "transferred_total": total_transferred,
        "sold_total": total_sold,
        "remaining_total": max(total_received - total_transferred, 0) + total_left,
    }

    ledger = list(cards_transfers_col.find({
        "product_key": product_key,
        "from_role": "manager",
        "from_id": manager_id,
    }).sort("created_at", -1).limit(200))
    if not ledger:
        ledger = list(transfers_col.find({
            "$or": [
                {"product_key": product_key},
                {"product_id": pid},
            ],
            "from_type": "manager",
            "from_id": manager_id,
        }).sort("created_at", -1).limit(200))
    ledger_rows = []
    for row in ledger:
        ledger_rows.append({
            "transfer_id": row.get("transfer_id"),
            "qty": int(row.get("qty", 0) or 0),
            "from_type": row.get("from_type") or row.get("from_role"),
            "from_id": str(row.get("from_id") or ""),
            "to_type": row.get("to_type") or row.get("to_role"),
            "to_id": str(row.get("to_id") or ""),
            "note": row.get("note") or "",
            "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
            "created_by_role": row.get("created_by_role"),
            "transfer_date": row.get("transfer_date") or "",
        })

    return jsonify(
        ok=True,
        product={"product_key": product_key, "name": product.get("name") or "", "image_url": product.get("image_url") or ""},
        products=products_with_metrics,
        summary=summary,
        agents=rows,
        ledger=ledger_rows,
    )


@cards_tracker_bp.route("/manager/cards-tracker/transfer-to-agent", methods=["POST"])
@cards_tracker_bp.route("/manager/cards/transfer", methods=["POST"])
def manager_cards_tracker_transfer_to_agent():
    if not _require_role("manager"):
        return jsonify(ok=False, error="Unauthorized"), 403

    _, manager_id = _actor_from_session()
    if not manager_id:
        return jsonify(ok=False, error="Manager not found"), 404

    data = request.json or request.form
    ok, result = _manager_transfer_to_agent(data, manager_id)
    if not ok:
        return jsonify(ok=False, message=result), 400
    return jsonify(ok=True, **result)


@cards_tracker_bp.route("/executive/cards/adjust", methods=["POST"])
def executive_cards_tracker_adjust():
    if not _require_role("executive"):
        return jsonify(ok=False, error="Unauthorized"), 403

    data = request.json or request.form
    product_key = normalize_product_name(data.get("product_key") or "")
    product_name = (data.get("product_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    note = (data.get("note") or "").strip()
    date_str = _safe_date_str((data.get("date") or "").strip())
    delta_raw = data.get("delta_qty")
    try:
        delta = int(delta_raw)
    except Exception:
        return jsonify(ok=False, error="Invalid quantity"), 400

    if not product_key:
        product = _product_by_key(product_name) if product_name else None
        if product:
            product_key = normalize_product_name(product.get("name") or "")
            product_name = product.get("name") or product_name
            image_url = product.get("image_url") or image_url
    if not product_key:
        return jsonify(ok=False, error="Card key required"), 400

    _, exec_id = _actor_from_session()
    ok, msg = _adjust_stock("executive", exec_id, product_key, product_name, image_url, delta, note, date_str)
    if not ok:
        return jsonify(ok=False, message=msg), 400
    return jsonify(ok=True, message="Adjustment saved")


@cards_tracker_bp.route("/manager/cards/adjust", methods=["POST"])
def manager_cards_tracker_adjust():
    if not _require_role("manager"):
        return jsonify(ok=False, error="Unauthorized"), 403

    data = request.json or request.form
    product_key = normalize_product_name(data.get("product_key") or "")
    product_name = (data.get("product_name") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    note = (data.get("note") or "").strip()
    date_str = _safe_date_str((data.get("date") or "").strip())
    delta_raw = data.get("delta_qty")
    try:
        delta = int(delta_raw)
    except Exception:
        return jsonify(ok=False, error="Invalid quantity"), 400

    if not product_key:
        product = _product_by_key(product_name) if product_name else None
        if product:
            product_key = normalize_product_name(product.get("name") or "")
            product_name = product.get("name") or product_name
            image_url = product.get("image_url") or image_url
    if not product_key:
        return jsonify(ok=False, error="Card key required"), 400

    _, manager_id = _actor_from_session()
    ok, msg = _adjust_stock("manager", manager_id, product_key, product_name, image_url, delta, note, date_str)
    if not ok:
        return jsonify(ok=False, message=msg), 400
    return jsonify(ok=True, message="Adjustment saved")


# -------------------------
# Agent Routes (Read-only)
# -------------------------


@cards_tracker_bp.route("/agent/cards-tracker", methods=["GET"])
def agent_cards_tracker():
    if not _require_role("agent"):
        return "Unauthorized", 403

    return render_template("agent/cards_tracker.html")


@cards_tracker_bp.route("/agent/cards-tracker/data", methods=["GET"])
def agent_cards_tracker_data():
    if not _require_role("agent"):
        return jsonify(ok=False, error="Unauthorized"), 403

    _, agent_id = _actor_from_session()
    if not agent_id:
        return jsonify(ok=False, error="Agent not found"), 404

    stock_rows = list(agent_stock_col.find({"agent_id": agent_id}))
    balances = list(balances_col.find({"owner_type": "agent", "owner_id": agent_id})) if not stock_rows else []

    # Sold counts by name (all products)
    sold_by_name = sold_counts_by_name(
        customers_col,
        instant_sales_col,
        agent_id=agent_id,
        manager_id=None,
        product_name=None,
        start_dt=None,
        end_dt=None,
        group_by_agent=False,
    ).get("total", {})

    sold_name_map = {}
    for _, entry in sold_by_name.items():
        key = normalize_product_name(entry.get("name") or "")
        if not key:
            continue
        sold_name_map[key] = int(sold_name_map.get(key, 0) or 0) + int(entry.get("count", 0) or 0)

    # Manual sales (legacy by product_id)
    manual_rows = list(manual_sales_col.aggregate([
        {"$match": {"agent_id": agent_id}},
        {"$group": {"_id": "$product_id", "total": {"$sum": "$qty_used"}}},
    ]))
    manual_map = {str(r.get("_id")): int(r.get("total", 0) or 0) for r in manual_rows}

    rows = []
    if stock_rows:
        for row in stock_rows:
            key = normalize_product_name(row.get("product_key") or "")
            name = row.get("product_name") or "Card"
            sold = int(sold_name_map.get(key, 0) or 0)
            given = int(row.get("received_qty", 0) or 0)
            left = int(row.get("available_qty", max(given - sold, 0)) or 0)
            progress_pct = round((sold / given * 100) if given else 0, 2)
            rows.append({
                "product_id": "",
                "product_name": name,
                "given": given,
                "sold": sold,
                "left": left,
                "progress_pct": min(200, max(0, progress_pct)),
            })
    else:
        product_ids = [b.get("product_id") for b in balances if b.get("product_id")]
        product_docs = list(products_col.find({"_id": {"$in": product_ids}}, {"name": 1})) if product_ids else []
        product_map = {str(p.get("_id")): p for p in product_docs}
        for bal in balances:
            pid = bal.get("product_id")
            pid_str = str(pid)
            product = product_map.get(pid_str, {})
            name = product.get("name") or "Card"
            name_key = normalize_product_name(name)
            sold = int(sold_name_map.get(name_key, 0) or 0)
            sold += int(manual_map.get(pid_str, 0) or 0)

            given = int(bal.get("given_in_total", 0) or 0)
            left = max(given - sold, 0)
            progress_pct = round((sold / given * 100) if given else 0, 2)

            rows.append({
                "product_id": pid_str,
                "product_name": name,
                "given": given,
                "sold": sold,
                "left": left,
                "progress_pct": min(200, max(0, progress_pct)),
            })

    return jsonify(ok=True, products=rows)


@cards_tracker_bp.route("/agent/cards-tracker/consume", methods=["POST"])
def agent_cards_tracker_consume():
    # Optional manual consume endpoint (fallback)
    if not _require_role("agent"):
        return jsonify(ok=False, error="Unauthorized"), 403

    _, agent_id = _actor_from_session()
    if not agent_id:
        return jsonify(ok=False, error="Agent not found"), 404

    data = request.json or request.form
    product_id = (data.get("product_id") or "").strip()
    qty_raw = data.get("qty_used") or 1
    note = (data.get("note") or "").strip()

    try:
        qty = int(qty_raw)
    except Exception:
        return jsonify(ok=False, error="Invalid quantity"), 400
    if qty <= 0:
        return jsonify(ok=False, error="Quantity must be positive"), 400

    pid = _safe_oid(product_id)
    if not pid:
        return jsonify(ok=False, error="Invalid product"), 400

    agent_doc = _agent_doc(agent_id) or {}
    manager_id = str(agent_doc.get("manager_id") or "")

    now = _now_utc()

    manual_sales_col.insert_one({
        "event_id": _transfer_id(),
        "product_id": pid,
        "agent_id": agent_id,
        "manager_id": manager_id,
        "customer_id": (data.get("customer_id") or "").strip(),
        "qty_used": qty,
        "source": "manual",
        "note": note,
        "created_at": now,
    })

    return jsonify(ok=True)
