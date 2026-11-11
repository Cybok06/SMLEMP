"""Preview or copy the inventory and sales catalog to the new deployment.

Only inventory_products and products are written. Existing records are never
overwritten; a conflicting document aborts preflight before any writes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from bson import json_util
from pymongo import IndexModel, MongoClient, UpdateOne
from pymongo.server_api import ServerApi

from migrate_active_users import ROOT, config


COLLECTIONS = ("inventory_products", "products")


def snapshot(collection):
    return {doc["_id"]: doc for doc in collection.find({})}


def digest(documents):
    text = json_util.dumps(
        sorted(documents.values(), key=lambda doc: str(doc["_id"])),
        json_options=json_util.CANONICAL_JSON_OPTIONS,
        sort_keys=True,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def comparable_indexes(indexes):
    # Build-time flags and server index versions do not alter index behavior.
    return {
        name: {key: value for key, value in info.items() if key not in {"v", "ns", "background"}}
        for name, info in indexes.items()
    }


def component_references(data):
    inventory_ids = {str(uid) for uid in data["inventory_products"]}
    missing = set()
    count = 0
    explicit_missing = set()
    explicit_count = 0
    for product in data["products"].values():
        for component in product.get("components") or []:
            uid = component.get("_id") or component.get("id") or component.get("product_id")
            if uid is not None:
                count += 1
                if str(uid) not in inventory_ids:
                    missing.add(str(uid))
                if component.get("source_collection") == "inventory_products":
                    explicit_count += 1
                    if str(uid) not in inventory_ids:
                        explicit_missing.add(str(uid))
    return {
        "component_references": count,
        "explicit_inventory_product_references": explicit_count,
        "explicit_inventory_product_missing_ids": sorted(explicit_missing),
        "references_absent_from_inventory_products_count": len(missing),
        "references_absent_from_inventory_products": sorted(missing),
        "note": "Legacy references can target the separate inventory collection, which is empty in the source. Original references are preserved without remapping.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    old = config(ROOT / "SmartLiving-old-app")
    new = config(ROOT / "SmartLiving--new")
    if old["DEFAULT_URI"] == new["DEFAULT_URI"]:
        raise RuntimeError("Source and destination must be separate deployments")
    with MongoClient(old["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000) as old_client, MongoClient(
        new["DEFAULT_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000
    ) as new_client:
        old_client.admin.command("ping")
        new_client.admin.command("ping")
        source = old_client[old["DEFAULT_DB_NAME"]]
        target = new_client[new["DEFAULT_DB_NAME"]]
        data = {name: snapshot(source[name]) for name in COLLECTIONS}
        before = {name: snapshot(target[name]) for name in COLLECTIONS}
        indexes = {name: source[name].index_information() for name in COLLECTIONS}
        missing = {}
        for name in COLLECTIONS:
            for uid, doc in data[name].items():
                if uid in before[name] and before[name][uid] != doc:
                    raise RuntimeError(f"Conflicting destination record in {name}; nothing overwritten")
            missing[name] = [doc for uid, doc in data[name].items() if uid not in before[name]]
        report = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "applied": args.apply,
            "collections": {},
            "source_component_reference_audit": component_references(data),
        }
        if args.apply:
            for name in COLLECTIONS:
                if snapshot(source[name]) != data[name]:
                    raise RuntimeError(f"Source {name} changed during preflight; rerun")
            for name in COLLECTIONS:
                models = [
                    IndexModel(info["key"], name=index_name, **{
                        key: value for key, value in info.items()
                        if key not in {"key", "v", "ns", "background"}
                    })
                    for index_name, info in indexes[name].items() if index_name != "_id_"
                ]
                if models:
                    target[name].create_indexes(models)
                operations = [UpdateOne({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True) for doc in missing[name]]
                if operations:
                    target[name].bulk_write(operations, ordered=True)
        for name in COLLECTIONS:
            after = snapshot(target[name])
            if args.apply:
                if any(after.get(uid) != doc for uid, doc in data[name].items()):
                    raise RuntimeError(f"Exact document verification failed for {name}")
                if any(after.get(uid) != doc for uid, doc in before[name].items()):
                    raise RuntimeError(f"Existing destination document changed in {name}")
                if snapshot(source[name]) != data[name]:
                    raise RuntimeError(f"Source {name} changed during migration; rerun to reconcile")
                if comparable_indexes(target[name].index_information()) != comparable_indexes(indexes[name]):
                    raise RuntimeError(f"Index verification failed for {name}")
            report["collections"][name] = {
                "source_records": len(data[name]),
                "destination_before": len(before[name]),
                "inserted": len(missing[name]) if args.apply else 0,
                "already_matching": len(data[name]) - len(missing[name]),
                "destination_after": len(after),
                "source_sha256": digest(data[name]),
                "copied_records_sha256": digest({uid: after[uid] for uid in data[name] if uid in after}),
                "verified_exact_documents": args.apply,
                "source_unchanged": args.apply,
                "indexes": list(indexes[name]),
            }
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        summary = {**report, "source_component_reference_audit": {
            key: value for key, value in report["source_component_reference_audit"].items()
            if key != "references_absent_from_inventory_products"
        }}
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
