"""Seed or remove the isolated MongoDB image-storage test collection.

Usage:
    python scripts/mongodb_image_storage_test.py seed
    python scripts/mongodb_image_storage_test.py cleanup --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson.binary import Binary
from pymongo.errors import OperationFailure


# Allow this script to import the application's root-level db module when run
# directly from the scripts directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Random, incompressible 10 MiB payloads can take longer than the application's
# normal request timeout to upload. This affects this utility process only.
os.environ.setdefault("MONGO_SOCKET_TIMEOUT_MS", "120000")

from db import db  # noqa: E402  (reuse the application's configured database)


COLLECTION_NAME = "images_upload"
IMAGE_SIZE_BYTES = 10 * 1024 * 1024
IMAGE_NAMES = ("test_image_1.jpg", "test_image_2.jpg")


def _mb(byte_count: int | float) -> float:
    return byte_count / (1024 * 1024)


def _collection_stats() -> dict[str, Any] | None:
    """Return collection statistics when the MongoDB deployment permits it."""
    try:
        return db.command("collStats", COLLECTION_NAME)
    except OperationFailure as exc:
        print(f"Collection statistics unavailable: {exc}")
        return None


def seed_images() -> None:
    """Create the test collection and insert exactly two random 10 MiB images."""
    collection = db[COLLECTION_NAME]
    existing_count = collection.count_documents({}, limit=1)
    if existing_count:
        raise RuntimeError(
            f"Refusing to insert: '{COLLECTION_NAME}' is not empty. "
            "Run cleanup with --confirm before reseeding."
        )

    documents = [
        {
            "image_name": image_name,
            "image_type": "image/jpeg",
            "description": "Large MongoDB image storage test",
            "image_data": Binary(os.urandom(IMAGE_SIZE_BYTES)),
            "size_bytes": IMAGE_SIZE_BYTES,
            "created_at": datetime.now(timezone.utc),
        }
        for image_name in IMAGE_NAMES
    ]

    inserted_ids = []
    try:
        # Upload separately so a slow connection does not have to transmit the
        # full 20 MiB BSON command within one socket operation.
        for document in documents:
            inserted_ids.append(collection.insert_one(document).inserted_id)
    except Exception:
        # Preserve the requirement that this test collection never contains a
        # partial one-document result if the second insert fails.
        collection.drop()
        raise

    inserted_count = len(inserted_ids)
    total_bytes = sum(document["size_bytes"] for document in documents)
    print(f"Collection name: {COLLECTION_NAME}")
    print(f"Documents inserted: {inserted_count}")
    for document in documents:
        print(f"{document['image_name']}: {_mb(document['size_bytes']):.2f} MB")
    print(f"Total binary data inserted: {_mb(total_bytes):.2f} MB ({total_bytes} bytes)")

    stats = _collection_stats()
    if stats:
        print("MongoDB collection statistics:")
        print(f"  document count: {stats.get('count', 'n/a')}")
        print(f"  logical size: {_mb(stats.get('size', 0)):.2f} MB")
        print(f"  storage size: {_mb(stats.get('storageSize', 0)):.2f} MB")
        print(f"  total index size: {_mb(stats.get('totalIndexSize', 0)):.2f} MB")


def cleanup_images_upload() -> None:
    """Delete only the temporary images_upload collection."""
    if COLLECTION_NAME not in db.list_collection_names():
        print(f"Collection '{COLLECTION_NAME}' does not exist; nothing to delete.")
        return
    db.drop_collection(COLLECTION_NAME)
    print(f"Deleted test collection: {COLLECTION_NAME}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="Insert two random 10 MiB BSON images")
    cleanup_parser = subparsers.add_parser("cleanup", help="Drop the test collection")
    cleanup_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required confirmation that images_upload may be dropped",
    )
    args = parser.parse_args()

    if args.command == "seed":
        seed_images()
    elif not args.confirm:
        parser.error("cleanup requires --confirm")
    else:
        cleanup_images_upload()


if __name__ == "__main__":
    main()
