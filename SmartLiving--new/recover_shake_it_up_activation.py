from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId

from db import db


ACTIVATIONS = db["activations"]
DEFAULT_TITLE = "SHAKE IT UP"


def safe_oid(raw: str) -> Optional[ObjectId]:
    try:
        return ObjectId(str(raw))
    except Exception:
        return None


def fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "--")


def print_activation(doc: Dict[str, Any]) -> None:
    print("Found activation:")
    print(f"  id: {doc.get('_id')}")
    print(f"  title: {doc.get('title') or '--'}")
    print(f"  location: {doc.get('location') or '--'}")
    print(f"  status: {doc.get('status') or '--'}")
    print(f"  activationDateTime: {fmt_dt(doc.get('activationDateTime'))}")
    print(f"  updatedAt: {fmt_dt(doc.get('updatedAt'))}")


def find_activation(title: str, activation_id: str = "") -> Optional[Dict[str, Any]]:
    if activation_id:
        oid = safe_oid(activation_id)
        if not oid:
            raise SystemExit(f"Invalid activation id: {activation_id}")
        return ACTIVATIONS.find_one({"_id": oid})

    return ACTIVATIONS.find_one(
        {"title": {"$regex": f"^{title}$", "$options": "i"}, "status": "cancelled"},
        sort=[("updatedAt", -1), ("createdAt", -1)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover a cancelled activation by changing its status back to upcoming."
    )
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Activation title to recover.")
    parser.add_argument("--activation-id", default="", help="Recover this exact activation id instead of searching by title.")
    parser.add_argument("--apply", action="store_true", help="Actually update the activation. Without this, the script only previews.")
    args = parser.parse_args()

    activation = find_activation(args.title.strip(), args.activation_id.strip())
    if not activation:
        print(f"No cancelled activation found for title: {args.title!r}")
        print("Tip: pass --activation-id <id> if the title was edited or there are duplicates.")
        return

    print_activation(activation)
    if (activation.get("status") or "").lower() != "cancelled":
        print("\nThis activation is not cancelled, so no recovery is needed.")
        return

    if not args.apply:
        print("\nDry run only. Run again with --apply to recover it.")
        return

    now = datetime.utcnow()
    result = ACTIVATIONS.update_one(
        {"_id": activation["_id"], "status": "cancelled"},
        {
            "$set": {
                "status": "upcoming",
                "updatedAt": now,
                "recoveredAt": now,
                "recoveryNote": "Recovered from cancelled status using scripts/recover_shake_it_up_activation.py",
            }
        },
    )

    if result.modified_count:
        print("\nRecovered successfully. Status is now upcoming.")
    else:
        print("\nNothing changed. The activation may have already been recovered.")


if __name__ == "__main__":
    main()
