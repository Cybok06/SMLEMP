"""
Delete a budget entry from expense_budgets by year + kind + category.

Examples:
  python scripts/delete_budget.py --year 2026 --kind expense --category "Fuel"
  python scripts/delete_budget.py --year 2026 --kind income --category "Customer Payments" --yes
  python scripts/delete_budget.py --year 2026 --kind expense --category "Fuel" --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict

from db import db


budgets_col = db["expense_budgets"]


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _build_query(year: int, kind: str, category: str, ignore_case: bool) -> Dict[str, Any]:
    cat = category.strip()
    if ignore_case:
        cat_match: Any = {"$regex": f"^{re.escape(cat)}$", "$options": "i"}
    else:
        cat_match = cat

    if kind == "income":
        return {"year": year, "kind": "income", "category": cat_match}

    # Expense budgets also include legacy rows where kind is missing.
    return {
        "year": year,
        "category": cat_match,
        "$or": [{"kind": "expense"}, {"kind": {"$exists": False}}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete a set budget entry.")
    parser.add_argument("--year", type=int, required=True, help="Budget year, e.g. 2026")
    parser.add_argument("--kind", choices=["income", "expense"], required=True, help="Budget kind")
    parser.add_argument("--category", required=True, help="Budget category name")
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        default=True,
        help="Match category case-insensitively (default: on)",
    )
    parser.add_argument(
        "--exact-case",
        action="store_true",
        help="Match category with exact case",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only; do not delete")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation")

    args = parser.parse_args()
    ignore_case = False if args.exact_case else args.ignore_case

    query = _build_query(args.year, args.kind, args.category, ignore_case)
    matches = list(
        budgets_col.find(
            query,
            {"_id": 1, "year": 1, "kind": 1, "category": 1, "amount": 1, "updated_at": 1},
        )
    )

    if not matches:
        print("No matching budget records found. Nothing to delete.")
        return 0

    print(f"Found {len(matches)} matching record(s):")
    for d in matches:
        print(
            f"- _id={d.get('_id')} | year={d.get('year')} | kind={d.get('kind', 'expense')}"
            f" | category={d.get('category')} | amount={_safe_float(d.get('amount')):.2f}"
        )

    if args.dry_run:
        print("Dry-run mode enabled. No records were deleted.")
        return 0

    if not args.yes:
        confirm = input("Type DELETE to confirm deletion: ").strip()
        if confirm != "DELETE":
            print("Cancelled. No records were deleted.")
            return 1

    result = budgets_col.delete_many(query)
    print(f"Deleted {result.deleted_count} record(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
