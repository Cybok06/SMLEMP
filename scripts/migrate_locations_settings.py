"""Copy approved location, permission, and business configuration records.

Preview by default; --apply inserts missing records without overwriting data.
Source connections come from the two db.py defaults, without importing the apps.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from pymongo import IndexModel, MongoClient, UpdateOne
from pymongo.server_api import ServerApi

from migrate_active_users import APP_ROOT, ROOT, config
from migrate_products_inventory import comparable_indexes, digest, snapshot


SELECTIONS = {
    "inventory_branch_locations": {},
    "inventory_roles": {},
    "hr_roles": {},
    "inventory_settings": {},
    "printer_access": {},
    "salary_settings": {},
    "expense_categories": {},
    "private_ledger_settings": {"key": "passcode"},
    "liability_settings": {},
    "payroll_settings": {},
    "hr_leave_settings": {},
    "hr_critical_days": {},
}
PROTECTED = ("users", "products", "inventory_products")


def read_selected(db):
    return {
        name: {row["_id"]: row for row in db[name].find(query)}
        for name, query in SELECTIONS.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    old, new = config(ROOT / "SmartLiving-old-app"), config(APP_ROOT)
    if old["DEFAULT_URI"] == new["DEFAULT_URI"]:
        raise RuntimeError("Source and destination must be separate deployments")
    with MongoClient(old["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000) as old_client, MongoClient(
        new["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000
    ) as new_client:
        old_client.admin.command("ping")
        new_client.admin.command("ping")
        source, target = old_client[old["DEFAULT_DB_NAME"]], new_client[new["DEFAULT_DB_NAME"]]
        data = read_selected(source)
        before = {name: snapshot(target[name]) for name in SELECTIONS}
        protected = {name: digest(snapshot(target[name])) for name in PROTECTED}
        source_indexes = {name: source[name].index_information() for name in SELECTIONS}
        user_ids = {str(row["_id"]) for row in target.users.find({}, {"_id": 1})}
        for name in ("inventory_settings", "salary_settings"):
            if any(row.get("manager_id") and str(row["manager_id"]) not in user_ids for row in data[name].values()):
                raise RuntimeError(f"Unmigrated manager referenced by {name}")
        for row in data["printer_access"].values():
            if row.get("scope") == "agent" and str(row.get("target")) not in user_ids:
                raise RuntimeError("Printer grant references an unmigrated user")
        missing = {}
        for name in SELECTIONS:
            for uid, doc in data[name].items():
                if uid in before[name] and before[name][uid] != doc:
                    raise RuntimeError(f"Destination conflict in {name}; no records overwritten")
            missing[name] = [doc for uid, doc in data[name].items() if uid not in before[name]]
        source_manager_branches = {
            str(row["_id"]): row.get("branch")
            for row in source.users.find({"role": "manager"}, {"branch": 1})
            if str(row["_id"]) in user_ids
        }
        target_manager_branches = {
            str(row["_id"]): row.get("branch")
            for row in target.users.find({"role": "manager"}, {"branch": 1})
        }
        if source_manager_branches != target_manager_branches:
            raise RuntimeError("Migrated branch manager profiles do not match source")
        location_ids = {str(uid) for uid in data["inventory_branch_locations"]}
        unresolved_locations = sorted({
            str(entry["location_id"])
            for product in target.inventory_products.find({}, {"entries.location_id": 1})
            for entry in product.get("entries") or []
            if entry.get("location_id") and str(entry["location_id"]) not in location_ids
        })
        if args.apply:
            if read_selected(source) != data:
                raise RuntimeError("Source configuration changed during preflight; rerun")
            for name in SELECTIONS:
                models = [IndexModel(info["key"], name=index_name, **{
                    key: value for key, value in info.items() if key not in {"key", "v", "ns", "background"}
                }) for index_name, info in source_indexes[name].items() if index_name != "_id_"]
                if models:
                    target[name].create_indexes(models)
                if missing[name]:
                    target[name].bulk_write([
                        UpdateOne({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
                        for doc in missing[name]
                    ], ordered=True)
        report = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "applied": args.apply,
            "collections": {},
            "branch_manager_profiles_verified": len(target_manager_branches),
            "location_types": dict(Counter(row.get("type") for row in data["inventory_branch_locations"].values())),
            "unresolved_stock_location_ids": unresolved_locations,
            "excluded_temporary_lockout_records": source.private_ledger_settings.count_documents({"key": {"$regex": "^lockout:"}}),
            "deferred": {"accounting_payment_routes": "References bank_accounts, which have not been approved for migration"},
        }
        for name in SELECTIONS:
            after = snapshot(target[name])
            if args.apply:
                if any(after.get(uid) != doc for uid, doc in data[name].items()):
                    raise RuntimeError(f"Document verification failed for {name}")
                if any(after.get(uid) != doc for uid, doc in before[name].items()):
                    raise RuntimeError(f"Existing destination record changed in {name}")
                expected_indexes = comparable_indexes(source_indexes[name])
                actual_indexes = comparable_indexes(target[name].index_information())
                if any(actual_indexes.get(key) != value for key, value in expected_indexes.items()):
                    raise RuntimeError(f"Index verification failed for {name}")
            report["collections"][name] = {
                "selected_source_records": len(data[name]),
                "inserted": len(missing[name]) if args.apply else 0,
                "already_matching": len(data[name]) - len(missing[name]),
                "destination_records": len(after),
                "source_sha256": digest(data[name]),
                "copied_records_sha256": digest({uid: after[uid] for uid in data[name] if uid in after}),
                "verified_exact_documents": args.apply,
            }
        if args.apply and read_selected(source) != data:
            raise RuntimeError("Source configuration changed during migration; rerun to reconcile")
        report["previously_transferred_collections_unchanged"] = {
            name: digest(snapshot(target[name])) == fingerprint for name, fingerprint in protected.items()
        }
        if not all(report["previously_transferred_collections_unchanged"].values()):
            raise RuntimeError("Previously migrated data changed during configuration transfer")
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
