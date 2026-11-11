from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from db import db


products_col = db["products"]
users_col = db["users"]

cards_col = db["product_cards_v2"]
balances_col = db["product_card_balances_v2"]
transfers_col = db["product_card_transfers_v2"]
consumptions_col = db["product_card_consumptions_v2"]


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _sort_dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.min


def _str_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _safe_oid(value: str | None):
    if value and ObjectId.is_valid(str(value)):
        return ObjectId(str(value))
    return None


def ensure_indexes() -> None:
    try:
        cards_col.create_index([("product_id", 1)], unique=True)
        cards_col.create_index([("product_key", 1)])
        balances_col.create_index([("holder_role", 1), ("holder_user_id", 1), ("product_id", 1)], unique=True)
        balances_col.create_index([("manager_id", 1), ("holder_role", 1)])
        transfers_col.create_index([("product_id", 1), ("created_at", -1)])
        transfers_col.create_index([("from_role", 1), ("from_user_id", 1), ("created_at", -1)])
        transfers_col.create_index([("to_role", 1), ("to_user_id", 1), ("created_at", -1)])
        consumptions_col.create_index([("agent_id", 1), ("product_id", 1), ("created_at", -1)])
        consumptions_col.create_index([("sale_ref", 1)], unique=True)
    except Exception:
        pass


ensure_indexes()


def product_doc(product_id: str) -> dict | None:
    oid = _safe_oid(product_id)
    if not oid:
        return None
    return products_col.find_one({"_id": oid}, {"name": 1, "image_url": 1})


def product_options() -> list[dict]:
    rows = list(products_col.find({}, {"name": 1, "image_url": 1, "created_at": 1}).sort("name", 1).limit(5000))
    dedup: dict[str, dict] = {}
    for row in rows:
        name = row.get("name") or "Unnamed Product"
        key = _normalize_name(name)
        if not key:
            continue
        current = dedup.get(key)
        if not current:
            dedup[key] = row
            continue
        row_has_image = bool((row.get("image_url") or "").strip())
        current_has_image = bool((current.get("image_url") or "").strip())
        if row_has_image and not current_has_image:
            dedup[key] = row
            continue
        if row_has_image == current_has_image and _sort_dt(row.get("created_at")) > _sort_dt(current.get("created_at")):
            dedup[key] = row

    out = []
    for row in dedup.values():
        pid = _str_id(row.get("_id"))
        if not pid:
            continue
        out.append(
            {
                "_id": pid,
                "name": row.get("name") or "Unnamed Product",
                "image_url": row.get("image_url") or "",
            }
        )
    out.sort(key=lambda item: (item.get("name") or "").lower())
    return out


def product_card_doc(product_id: str) -> dict | None:
    return cards_col.find_one({"product_id": str(product_id)})


def product_has_card(product_id: str) -> bool:
    row = product_card_doc(product_id)
    return bool(row and row.get("is_active", True))


def create_or_update_product_card(product_id: str, qty: int) -> tuple[bool, str]:
    prod = product_doc(product_id)
    if not prod:
        return False, "Product not found."
    if qty < 0:
        return False, "Quantity cannot be negative."
    now = _utcnow()
    existing = product_card_doc(product_id)
    sold_total = consumed_total_for_product(product_id)
    manager_total = current_balance_total("manager", product_id)
    agent_total = current_balance_total("agent", product_id)
    minimum_exec_available = 0
    if existing and qty < minimum_exec_available:
        return False, "Quantity is too low."
    payload = {
        "product_id": str(prod["_id"]),
        "product_key": _normalize_name(prod.get("name")),
        "product_name": prod.get("name") or "Unnamed Product",
        "image_url": prod.get("image_url") or "",
        "executive_available_qty": qty,
        "is_active": True,
        "stats_cached": {
            "sold_total": sold_total,
            "manager_total": manager_total,
            "agent_total": agent_total,
        },
        "updated_at": now,
    }
    if existing:
        cards_col.update_one({"_id": existing["_id"]}, {"$set": payload})
    else:
        payload["created_at"] = now
        cards_col.insert_one(payload)
    return True, "Product card saved."


def executive_transfer(product_id: str, manager_id: str, qty: int, actor_id: str) -> tuple[bool, str]:
    if qty <= 0:
        return False, "Quantity must be greater than zero."
    card = product_card_doc(product_id)
    manager = user_doc(manager_id, "manager")
    if not card:
        return False, "Product card not found."
    if not manager:
        return False, "Manager not found."
    available = int(card.get("executive_available_qty", 0) or 0)
    if qty > available:
        return False, "Executive does not have enough cards available."
    now = _utcnow()
    cards_col.update_one(
        {"_id": card["_id"]},
        {"$inc": {"executive_available_qty": -qty}, "$set": {"updated_at": now}},
    )
    balance_upsert(
        holder_role="manager",
        holder_user=manager,
        product_card=card,
        qty_delta=qty,
        manager_id=str(manager["_id"]),
        received_delta=qty,
    )
    transfers_col.insert_one(
        {
            "product_id": card["product_id"],
            "product_name": card["product_name"],
            "product_key": card["product_key"],
            "qty": qty,
            "from_role": "executive",
            "from_user_id": actor_id,
            "to_role": "manager",
            "to_user_id": str(manager["_id"]),
            "to_name": manager.get("name") or manager.get("username") or "Manager",
            "to_branch": manager.get("branch") or "",
            "created_at": now,
        }
    )
    return True, "Cards transferred to manager."


def manager_transfer(product_id: str, manager_id: str, agent_id: str, qty: int) -> tuple[bool, str]:
    if qty <= 0:
        return False, "Quantity must be greater than zero."
    card = product_card_doc(product_id)
    manager = user_doc(manager_id, "manager")
    agent = user_doc(agent_id, "agent")
    if not card:
        return False, "Product card not found."
    if not manager:
        return False, "Manager not found."
    if not agent:
        return False, "Agent not found."
    manager_balance = balance_doc("manager", manager_id, product_id)
    if not manager_balance or int(manager_balance.get("available_qty", 0) or 0) < qty:
        return False, "Manager does not have enough cards available."
    now = _utcnow()
    balances_col.update_one(
        {"_id": manager_balance["_id"]},
        {
            "$inc": {"available_qty": -qty, "transferred_out_qty": qty},
            "$set": {"updated_at": now},
        },
    )
    balance_upsert(
        holder_role="agent",
        holder_user=agent,
        product_card=card,
        qty_delta=qty,
        manager_id=manager_id,
        received_delta=qty,
    )
    transfers_col.insert_one(
        {
            "product_id": card["product_id"],
            "product_name": card["product_name"],
            "product_key": card["product_key"],
            "qty": qty,
            "from_role": "manager",
            "from_user_id": manager_id,
            "to_role": "agent",
            "to_user_id": str(agent["_id"]),
            "to_name": agent.get("name") or agent.get("username") or "Agent",
            "to_branch": agent.get("branch") or "",
            "created_at": now,
        }
    )
    return True, "Cards transferred to agent."


def consume_agent_card(
    *,
    product_id: str,
    agent_id: str,
    manager_id: str | None,
    sale_ref: str,
    customer_id: str | None,
    qty_sold: int,
    source: str,
) -> tuple[bool, str]:
    if qty_sold <= 0:
        return True, "Nothing to consume."
    card = product_card_doc(product_id)
    if not card or not card.get("is_active", True):
        return True, "No linked product card."
    existing = consumptions_col.find_one({"sale_ref": sale_ref})
    if existing:
        return True, "Card consumption already recorded."
    agent = user_doc(agent_id, "agent")
    if not agent:
        return False, "Agent not found."
    balance = balance_doc("agent", agent_id, product_id)
    available = int((balance or {}).get("available_qty", 0) or 0)
    used = min(available, qty_sold)
    shortage = max(qty_sold - used, 0)
    now = _utcnow()
    if balance:
        balances_col.update_one(
            {"_id": balance["_id"]},
            {
                "$inc": {"available_qty": -used, "consumed_qty": qty_sold},
                "$set": {"updated_at": now},
            },
        )
    else:
        balances_col.insert_one(
            {
                "holder_role": "agent",
                "holder_user_id": agent_id,
                "holder_name": agent.get("name") or agent.get("username") or "Agent",
                "branch": agent.get("branch") or "",
                "manager_id": manager_id or _str_id(agent.get("manager_id")),
                "product_id": card["product_id"],
                "product_name": card["product_name"],
                "product_key": card["product_key"],
                "available_qty": 0,
                "received_qty": 0,
                "consumed_qty": qty_sold,
                "transferred_out_qty": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
    consumptions_col.insert_one(
        {
            "sale_ref": sale_ref,
            "product_id": card["product_id"],
            "product_name": card["product_name"],
            "product_key": card["product_key"],
            "agent_id": agent_id,
            "agent_name": agent.get("name") or agent.get("username") or "Agent",
            "manager_id": manager_id or _str_id(agent.get("manager_id")),
            "customer_id": customer_id or "",
            "qty_used": qty_sold,
            "qty_from_stock": used,
            "shortage_qty": shortage,
            "source": source,
            "created_at": now,
        }
    )
    cards_col.update_one({"_id": card["_id"]}, {"$set": {"updated_at": now}})
    return True, "Product card consumption recorded."


def user_doc(user_id: str, role: str | None = None) -> dict | None:
    oid = _safe_oid(user_id)
    query: dict[str, Any] = {"_id": oid or user_id}
    if role:
        query["role"] = role
    return users_col.find_one(query, {"name": 1, "username": 1, "branch": 1, "manager_id": 1, "role": 1})


def balance_doc(holder_role: str, holder_user_id: str, product_id: str) -> dict | None:
    return balances_col.find_one(
        {"holder_role": holder_role, "holder_user_id": str(holder_user_id), "product_id": str(product_id)}
    )


def balance_upsert(
    *,
    holder_role: str,
    holder_user: dict,
    product_card: dict,
    qty_delta: int,
    manager_id: str | None,
    received_delta: int = 0,
) -> None:
    now = _utcnow()
    balances_col.update_one(
        {
            "holder_role": holder_role,
            "holder_user_id": str(holder_user.get("_id")),
            "product_id": product_card["product_id"],
        },
        {
            "$inc": {"available_qty": qty_delta, "received_qty": received_delta},
            "$set": {
                "holder_name": holder_user.get("name") or holder_user.get("username") or holder_role.title(),
                "branch": holder_user.get("branch") or "",
                "manager_id": manager_id or _str_id(holder_user.get("manager_id")),
                "product_name": product_card["product_name"],
                "product_key": product_card["product_key"],
                "updated_at": now,
            },
            "$setOnInsert": {
                "transferred_out_qty": 0,
                "consumed_qty": 0,
                "created_at": now,
            },
        },
        upsert=True,
    )


def current_balance_total(holder_role: str, product_id: str) -> int:
    pipeline = [
        {"$match": {"holder_role": holder_role, "product_id": str(product_id)}},
        {"$group": {"_id": None, "qty": {"$sum": "$available_qty"}}},
    ]
    rows = list(balances_col.aggregate(pipeline))
    return int((rows[0] or {}).get("qty", 0) or 0) if rows else 0


def consumed_total_for_product(product_id: str) -> int:
    pipeline = [
        {"$match": {"product_id": str(product_id)}},
        {"$group": {"_id": None, "qty": {"$sum": "$qty_used"}}},
    ]
    rows = list(consumptions_col.aggregate(pipeline))
    return int((rows[0] or {}).get("qty", 0) or 0) if rows else 0


def all_product_cards() -> list[dict]:
    rows = list(cards_col.find({"is_active": {"$ne": False}}).sort("product_name", 1))
    sold_map = sold_map_all()
    manager_available = balance_map_by_role("manager")
    agent_available = balance_map_by_role("agent")
    for row in rows:
        pid = row["product_id"]
        row["manager_total"] = manager_available.get(pid, 0)
        row["agent_total"] = agent_available.get(pid, 0)
        row["sold_total"] = sold_map.get(pid, 0)
        row["network_total"] = int(row.get("executive_available_qty", 0) or 0) + row["manager_total"] + row["agent_total"]
    return [
        {
            "product_id": row.get("product_id") or "",
            "product_key": row.get("product_key") or "",
            "product_name": row.get("product_name") or "Product",
            "image_url": row.get("image_url") or "",
            "executive_available_qty": int(row.get("executive_available_qty", 0) or 0),
            "manager_total": int(row.get("manager_total", 0) or 0),
            "agent_total": int(row.get("agent_total", 0) or 0),
            "sold_total": int(row.get("sold_total", 0) or 0),
            "network_total": int(row.get("network_total", 0) or 0),
        }
        for row in rows
    ]


def sold_map_all() -> dict[str, int]:
    rows = list(consumptions_col.aggregate([{"$group": {"_id": "$product_id", "qty": {"$sum": "$qty_used"}}}]))
    return {str(r["_id"]): int(r.get("qty", 0) or 0) for r in rows}


def balance_map_by_role(holder_role: str) -> dict[str, int]:
    rows = list(
        balances_col.aggregate(
            [
                {"$match": {"holder_role": holder_role}},
                {"$group": {"_id": "$product_id", "qty": {"$sum": "$available_qty"}}},
            ]
        )
    )
    return {str(r["_id"]): int(r.get("qty", 0) or 0) for r in rows}


def executive_overview() -> dict:
    cards = all_product_cards()
    managers = list(users_col.find({"role": "manager"}, {"name": 1, "branch": 1}).sort("name", 1))
    manager_map = {str(m["_id"]): m for m in managers}
    agents = list(users_col.find({"role": "agent"}, {"name": 1, "branch": 1, "manager_id": 1}).sort("name", 1))
    agent_map = {str(a["_id"]): a for a in agents}
    manager_balances = list(balances_col.find({"holder_role": "manager"}))
    agent_balances = list(balances_col.find({"holder_role": "agent"}))
    sold_by_agent_rows = list(
        consumptions_col.aggregate(
            [{"$group": {"_id": {"product_id": "$product_id", "agent_id": "$agent_id"}, "qty": {"$sum": "$qty_used"}}}]
        )
    )
    sold_by_agent = {
        (str(r["_id"]["product_id"]), str(r["_id"]["agent_id"])): int(r.get("qty", 0) or 0)
        for r in sold_by_agent_rows
    }

    card_details = {}
    for card in cards:
        pid = card["product_id"]
        manager_rows = []
        related_manager_balances = [b for b in manager_balances if str(b.get("product_id")) == pid]
        related_agent_balances = [b for b in agent_balances if str(b.get("product_id")) == pid]
        for mb in related_manager_balances:
            manager_id = str(mb.get("holder_user_id") or "")
            manager_doc = manager_map.get(manager_id, {})
            manager_agents = []
            for ab in related_agent_balances:
                if str(ab.get("manager_id") or "") != manager_id:
                    continue
                agent_id = str(ab.get("holder_user_id") or "")
                agent_doc = agent_map.get(agent_id, {})
                manager_agents.append(
                    {
                        "agent_id": agent_id,
                        "agent_name": agent_doc.get("name") or agent_doc.get("username") or ab.get("holder_name") or "Agent",
                        "branch": agent_doc.get("branch") or ab.get("branch") or "",
                        "available_qty": int(ab.get("available_qty", 0) or 0),
                        "received_qty": int(ab.get("received_qty", 0) or 0),
                        "sold_qty": sold_by_agent.get((pid, agent_id), int(ab.get("consumed_qty", 0) or 0)),
                    }
                )
            manager_rows.append(
                {
                    "manager_id": manager_id,
                    "manager_name": manager_doc.get("name") or manager_doc.get("username") or mb.get("holder_name") or "Manager",
                    "branch": manager_doc.get("branch") or mb.get("branch") or "",
                    "available_qty": int(mb.get("available_qty", 0) or 0),
                    "received_qty": int(mb.get("received_qty", 0) or 0),
                    "distributed_qty": int(mb.get("transferred_out_qty", 0) or 0),
                    "agents": sorted(manager_agents, key=lambda x: x["agent_name"].lower()),
                }
            )
        card_details[pid] = sorted(manager_rows, key=lambda x: x["manager_name"].lower())
    return {
        "cards": cards,
        "details": card_details,
        "managers": [{"_id": _str_id(m.get("_id")), "name": m.get("name") or m.get("username") or "Manager", "branch": m.get("branch") or ""} for m in managers],
        "product_options": product_options(),
    }


def manager_overview(manager_id: str) -> dict:
    cards = all_product_cards()
    agents = list(users_col.find({"role": "agent", "manager_id": {"$in": [_safe_oid(manager_id), manager_id]}}, {"name": 1, "branch": 1}).sort("name", 1))
    agent_map = {str(a["_id"]): a for a in agents}
    manager_balances = {str(b.get("product_id")): b for b in balances_col.find({"holder_role": "manager", "holder_user_id": manager_id})}
    agent_balances = list(balances_col.find({"holder_role": "agent", "manager_id": manager_id}))
    sold_by_agent_rows = list(
        consumptions_col.aggregate(
            [
                {"$match": {"manager_id": manager_id}},
                {"$group": {"_id": {"product_id": "$product_id", "agent_id": "$agent_id"}, "qty": {"$sum": "$qty_used"}}},
            ]
        )
    )
    sold_by_agent = {
        (str(r["_id"]["product_id"]), str(r["_id"]["agent_id"])): int(r.get("qty", 0) or 0)
        for r in sold_by_agent_rows
    }
    cards_out = []
    for card in cards:
        pid = card["product_id"]
        mb = manager_balances.get(pid)
        if not mb:
            continue
        related_agents = []
        for ab in agent_balances:
            if str(ab.get("product_id")) != pid:
                continue
            agent_id = str(ab.get("holder_user_id") or "")
            agent_doc = agent_map.get(agent_id, {})
            related_agents.append(
                {
                    "agent_id": agent_id,
                    "agent_name": agent_doc.get("name") or agent_doc.get("username") or ab.get("holder_name") or "Agent",
                    "branch": agent_doc.get("branch") or ab.get("branch") or "",
                    "available_qty": int(ab.get("available_qty", 0) or 0),
                    "received_qty": int(ab.get("received_qty", 0) or 0),
                    "sold_qty": sold_by_agent.get((pid, agent_id), int(ab.get("consumed_qty", 0) or 0)),
                }
            )
        cards_out.append(
            {
                **card,
                "manager_available_qty": int(mb.get("available_qty", 0) or 0),
                "manager_received_qty": int(mb.get("received_qty", 0) or 0),
                "manager_distributed_qty": int(mb.get("transferred_out_qty", 0) or 0),
                "agents": sorted(related_agents, key=lambda x: x["agent_name"].lower()),
            }
        )
    return {
        "cards": cards_out,
        "agents": [{"_id": _str_id(a.get("_id")), "name": a.get("name") or a.get("username") or "Agent", "branch": a.get("branch") or ""} for a in agents],
    }


def agent_overview(agent_id: str) -> dict:
    balances = list(balances_col.find({"holder_role": "agent", "holder_user_id": agent_id}).sort("product_name", 1))
    card_docs = {row.get("product_id"): row for row in cards_col.find({"product_id": {"$in": [str(b.get("product_id") or "") for b in balances if b.get("product_id")]}})}
    sold_rows = list(
        consumptions_col.aggregate(
            [
                {"$match": {"agent_id": agent_id}},
                {"$group": {"_id": "$product_id", "qty": {"$sum": "$qty_used"}, "last_used_at": {"$max": "$created_at"}}},
            ]
        )
    )
    sold_map = {str(r["_id"]): {"qty": int(r.get("qty", 0) or 0), "last_used_at": r.get("last_used_at")} for r in sold_rows}
    cards = []
    for bal in balances:
        pid = str(bal.get("product_id"))
        sold = sold_map.get(pid, {})
        card = card_docs.get(pid) or {}
        cards.append(
            {
                "product_id": pid,
                "product_name": bal.get("product_name") or "Product",
                "product_key": bal.get("product_key") or "",
                "image_url": card.get("image_url") or "",
                "available_qty": int(bal.get("available_qty", 0) or 0),
                "received_qty": int(bal.get("received_qty", 0) or 0),
                "sold_qty": int(sold.get("qty", bal.get("consumed_qty", 0)) or 0),
                "last_used_at": sold.get("last_used_at"),
            }
        )
    return {"cards": cards}


def recent_activity(limit: int = 120) -> list[dict]:
    rows = list(transfers_col.find({}).sort("created_at", -1).limit(limit))
    out = []
    for row in rows:
        out.append(
            {
                "type": "transfer",
                "product_name": row.get("product_name") or "Product",
                "qty": int(row.get("qty", 0) or 0),
                "from_role": row.get("from_role") or "",
                "to_role": row.get("to_role") or "",
                "to_name": row.get("to_name") or "",
                "created_at": row.get("created_at"),
            }
        )
    return out
