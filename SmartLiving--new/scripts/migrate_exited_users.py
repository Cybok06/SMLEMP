"""Move disabled exited employees from users into the Exited archive.

Dry-run is the default. Pass --apply to perform the guarded copy/verify/delete.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import db  # noqa: E402


EXITED_QUERY = {
    "$or": [
        {"employment_status": {"$regex": "^Exited$", "$options": "i"}},
        {"status": {"$regex": "^Exited$", "$options": "i"}},
    ]
}


def migrate(apply: bool = False) -> dict:
    users = db["users"]
    exited = db["Exited"]
    exit_files = db["hr_exits"]
    candidates = list(users.find(EXITED_QUERY))
    result = {"candidates": len(candidates), "moved": 0, "skipped": 0, "failed": 0}

    if not apply:
        return result

    for user in candidates:
        user_id = user.get("_id")
        try:
            exit_file = exit_files.find_one(
                {"employee.user_id": str(user_id), "is_deleted": {"$ne": True}},
                {"_id": 1},
                sort=[("created_at", -1)],
            )
            now = datetime.utcnow()
            archived = dict(user)
            archived.update({
                "is_active": False,
                "account_locked": True,
                "employment_status": "Exited",
                "status": "Exited",
                "archived_from": "users",
                "archived_at": archived.get("archived_at") or now,
                "updated_at": now,
            })
            if exit_file and not archived.get("exit_ref_id"):
                archived["exit_ref_id"] = str(exit_file["_id"])

            exited.replace_one({"_id": user_id}, archived, upsert=True)
            verified = exited.find_one(
                {"_id": user_id, "is_active": False, "account_locked": True, "employment_status": "Exited"},
                {"_id": 1},
            )
            if not verified:
                result["failed"] += 1
                continue

            deleted = users.delete_one({"_id": user_id, **EXITED_QUERY})
            if deleted.deleted_count == 1:
                result["moved"] += 1
            elif not users.find_one({"_id": user_id}):
                result["skipped"] += 1
            else:
                result["failed"] += 1
        except Exception:
            result["failed"] += 1

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Perform the migration; default is dry-run.")
    args = parser.parse_args()
    summary = migrate(apply=args.apply)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{mode}: candidates={summary['candidates']} moved={summary['moved']} skipped={summary['skipped']} failed={summary['failed']}")
