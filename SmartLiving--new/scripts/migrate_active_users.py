"""Copy active identities from the old app without changing the source users.

Run without arguments to preview; use --apply to insert missing identities.
Existing destination records must match exactly and are never overwritten.
Connection defaults are read without importing db.py or starting the app.
"""

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.server_api import ServerApi


ROOT = Path(__file__).resolve().parents[2]


def config(folder):
    tree = ast.parse((folder / "db.py").read_text(encoding="utf-8"))
    defaults = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"DEFAULT_URI", "DEFAULT_DB_NAME"}
    }
    # Read each app's own .env without changing process-wide variables or
    # accidentally using the new app's connection for the old source.
    local = dotenv_values(folder / ".env")
    uri = local.get("MONGODB_URI") or local.get("MONGO_URI") or defaults.get("DEFAULT_URI")
    name = local.get("MONGODB_DB") or local.get("MONGO_DB_NAME") or defaults.get("DEFAULT_DB_NAME")
    if not uri or not name:
        raise RuntimeError(f"Missing MongoDB configuration in {folder.name}")
    return {"DEFAULT_URI": uri, "DEFAULT_DB_NAME": name}


def normalized(value):
    return str(value or "").strip().lower()


def exclusion_reason(user, archived_ids, deleted_ids, exit_ids):
    uid = str(user["_id"])
    if uid in archived_ids or uid in exit_ids:
        return "exited_or_archived"
    if uid in deleted_ids or user.get("is_deleted") or user.get("deleted") or user.get("deleted_at"):
        return "deleted"
    if user.get("exit_ref_id") or normalized(user.get("employment_status")) in {
        "exited", "terminated", "resigned", "dismissed", "deleted", "inactive", "not active", "disabled"
    }:
        return "exited_or_inactive_employment"
    if normalized(user.get("status")) not in {"", "active"}:
        return "inactive_status"
    if user.get("is_active") is False or user.get("account_locked") or user.get("login_blocked"):
        return "disabled_or_locked"
    if normalized(user.get("role")) == "agent" and normalized(user.get("agent_approval_status")) in {"pending", "rejected"}:
        return "unapproved_agent"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    old = config(ROOT / "SmartLiving-old-app")
    new = config(ROOT / "SmartLiving--new")
    if old["DEFAULT_URI"] == new["DEFAULT_URI"]:
        raise RuntimeError("Source and destination must be separate deployments")
    with MongoClient(old["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000) as source_client, MongoClient(
        new["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000
    ) as target_client:
        source_client.admin.command("ping")
        target_client.admin.command("ping")
        source = source_client[old["DEFAULT_DB_NAME"]]
        target = target_client[new["DEFAULT_DB_NAME"]]
        users = list(source.users.find({}))
        archived_ids = {str(row["_id"]) for row in source.Exited.find({}, {"_id": 1})}
        deleted_ids = set()
        for row in source.deleted.find({"type": "user"}, {"original_user_id": 1, "original._id": 1}):
            deleted_ids.update(str(value) for value in (row.get("original_user_id"), row.get("original", {}).get("_id")) if value is not None)
        exit_ids = {
            str(row.get("employee", {}).get("user_id"))
            for row in source.hr_exits.find({"is_deleted": {"$ne": True}}, {"employee.user_id": 1})
        }
        eligible, excluded = [], {}
        for user in users:
            reason = exclusion_reason(user, archived_ids, deleted_ids, exit_ids)
            if reason:
                excluded[str(user["_id"])] = reason
            else:
                eligible.append(user)
        existing = list(target.users.find({}))
        by_id = {row["_id"]: row for row in existing}
        missing = []
        for user in eligible:
            if user["_id"] in by_id:
                if by_id[user["_id"]] != user:
                    raise RuntimeError("Destination identity differs; refusing to overwrite existing data")
            else:
                # Duplicate usernames are valid for role profiles in the source.
                if any(normalized(row.get("username")) == normalized(user.get("username")) for row in existing):
                    raise RuntimeError("Destination username collision; resolve before migration")
                missing.append(user)
        eligible_ids = {str(row["_id"]) for row in eligible}
        references = {
            key: sum(bool(row.get(key)) and str(row[key]) not in eligible_ids for row in eligible)
            for key in ("manager_id", "primary_user_id")
        }
        if any(references.values()):
            raise RuntimeError("Eligible users reference excluded managers or primary identities")
        if args.apply:
            # Recheck eligibility immediately before the first destination write.
            for user in eligible:
                if source.users.find_one({"_id": user["_id"]}) != user:
                    raise RuntimeError("Source changed during preflight; rerun migration")
            for user in missing:
                target.users.update_one({"_id": user["_id"]}, {"$setOnInsert": user}, upsert=True)
            for user in eligible:
                if target.users.find_one({"_id": user["_id"]}) != user:
                    raise RuntimeError("Destination verification failed")
            if target.users.count_documents({"_id": {"$in": [row["_id"] for row in users if str(row["_id"]) in excluded]}}):
                raise RuntimeError("Excluded source identities found in destination")
        report = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "applied": args.apply,
            "source_users": len(users),
            "eligible_users": len(eligible),
            "excluded_users": len(excluded),
            "exclusion_reasons": dict(Counter(excluded.values())),
            "inserted_users": len(missing) if args.apply else 0,
            "already_matching_users": len(eligible) - len(missing),
            "destination_users": target.users.count_documents({}),
            "roles": dict(Counter(row.get("role") for row in eligible)),
            "missing_identity_references": references,
            "eligible_ids": sorted(eligible_ids),
            "excluded_ids": excluded,
            "verified_exact_documents": args.apply,
        }
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key not in {"eligible_ids", "excluded_ids"}}, indent=2))


if __name__ == "__main__":
    main()
