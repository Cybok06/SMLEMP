from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from bson import ObjectId

from db import db

activations_col = db["activations"]
activation_rsvps_col = db["activation_rsvps"]
users_col = db["users"]


def safe_object_id(raw: Any) -> Optional[ObjectId]:
    if raw is None:
        return None
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def _variants(raw: Any) -> List[Any]:
    vals: List[Any] = []
    if raw is None:
        return vals
    vals.append(raw)
    raw_str = str(raw)
    if raw_str not in vals:
        vals.append(raw_str)
    oid = safe_object_id(raw)
    if oid is not None and oid not in vals:
        vals.append(oid)
        oid_str = str(oid)
        if oid_str not in vals:
            vals.append(oid_str)
    return vals


def _user_approved_activation_ids(user_id: Any) -> List[ObjectId]:
    rows = list(
        activation_rsvps_col.find(
            {
                "status": "approved",
                "userId": {"$in": _variants(user_id)},
            },
            {"activationId": 1},
        )
    )
    return [r.get("activationId") for r in rows if isinstance(r.get("activationId"), ObjectId)]


def is_activation_started(activation_doc: Optional[Dict[str, Any]]) -> bool:
    return bool(activation_doc) and isinstance((activation_doc or {}).get("startedAt"), datetime)


def is_activation_ended(activation_doc: Optional[Dict[str, Any]]) -> bool:
    return bool(activation_doc) and isinstance((activation_doc or {}).get("endedAt"), datetime)


def is_activation_running(activation_doc: Optional[Dict[str, Any]]) -> bool:
    if not activation_doc:
        return False
    return (activation_doc.get("status") or "").lower() == "upcoming" and is_activation_started(activation_doc) and not is_activation_ended(activation_doc)


def get_next_approved_activation_for_user(user_id: Any) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    activation_ids = _user_approved_activation_ids(user_id)
    if not activation_ids:
        return None
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    running = activations_col.find_one(
        {
            "_id": {"$in": activation_ids},
            "status": "upcoming",
            "startedAt": {"$type": "date"},
            "endedAt": {"$exists": False},
        },
        sort=[("startedAt", -1), ("activationDateTime", 1)],
    )
    if running:
        return running
    return activations_col.find_one(
        {
            "_id": {"$in": activation_ids},
            "status": "upcoming",
            "endedAt": {"$exists": False},
            "activationDateTime": {"$gte": today_start},
        },
        sort=[("activationDateTime", 1)],
    )


def get_activation_team_members(activation_id: Any) -> List[Dict[str, Any]]:
    aid = safe_object_id(activation_id)
    if not aid:
        return []
    approved_rows = list(
        activation_rsvps_col.find(
            {"activationId": aid, "status": "approved"},
            {"userId": 1, "role": 1},
        )
    )
    member_oids = [row.get("userId") for row in approved_rows if isinstance(row.get("userId"), ObjectId)]
    user_map: Dict[str, Dict[str, Any]] = {}
    if member_oids:
        for user in users_col.find({"_id": {"$in": member_oids}}, {"name": 1, "username": 1, "branch": 1, "role": 1}):
            user_map[str(user["_id"])] = user

    members: List[Dict[str, Any]] = []
    for row in approved_rows:
        uid = row.get("userId")
        if not isinstance(uid, ObjectId):
            continue
        user = user_map.get(str(uid), {})
        members.append(
            {
                "userId": str(uid),
                "name": user.get("name") or user.get("username") or "Unknown User",
                "branch": user.get("branch") or "",
                "role": (user.get("role") or row.get("role") or "").lower(),
            }
        )
    return members


def get_activation_group_context(user_id: Any, activation_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    activation = activation_doc or get_next_approved_activation_for_user(user_id)
    running = is_activation_running(activation)
    leader_selected = bool((activation or {}).get("teamLeaderId"))
    leader_state = "running" if running else ("ended" if is_activation_ended(activation) else ("not_started" if leader_selected else "not_selected"))
    context: Dict[str, Any] = {
        "activation": activation,
        "activation_id": str(activation.get("_id")) if activation else None,
        "group": None,
        "group_id": None,
        "group_name": None,
        "leader_id": str(user_id) if user_id else None,
        "leader_name": None,
        "member_ids": [str(user_id)] if user_id else [],
        "members": [],
        "is_grouped": False,
        "is_leader": True,
        "group_state": leader_state,
        "leader_selected": leader_selected,
        "ownership_active": False,
        "owner_agent_id": str(user_id) if user_id else None,
    }
    if not activation or not user_id:
        return context

    members = get_activation_team_members(activation.get("_id"))
    leader_id = str(activation.get("teamLeaderId") or "")
    leader_row = next((m for m in members if m.get("userId") == leader_id), None)

    if not leader_id:
        context.update(
            {
                "member_ids": [m.get("userId") for m in members if m.get("userId")],
                "members": members,
                "is_leader": False,
                "leader_selected": False,
            }
        )
        return context

    members = [{**m, "isLeader": m.get("userId") == leader_id} for m in members]

    context.update(
        {
            "group": None,
            "group_id": None,
            "group_name": activation.get("teamName") or "Activation Team",
            "leader_id": leader_id,
            "leader_name": (leader_row or {}).get("name") or None,
            "member_ids": [m.get("userId") for m in members if m.get("userId")],
            "members": members,
            "is_grouped": True,
            "is_leader": str(user_id) == leader_id,
            "group_state": leader_state,
            "leader_selected": True,
            "ownership_active": running,
            "owner_agent_id": leader_id if running else str(user_id),
        }
    )
    return context


def get_accessible_agent_ids(user_id: Any) -> List[str]:
    ids: Set[str] = set()
    if user_id:
        ids.add(str(user_id))
    ctx = get_activation_group_context(user_id)
    leader_id = ctx.get("leader_id")
    if leader_id and ctx.get("ownership_active"):
        ids.add(str(leader_id))
    return [x for x in ids if x]
