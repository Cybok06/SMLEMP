from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from accounting_services import _parse_month


def _month_range(year: int, start_y: int, start_m: int, end_y: int, end_m: int) -> Tuple[int, int]:
    if year < start_y or year > end_y:
        return (0, -1)
    sm = 1
    em = 12
    if year == start_y:
        sm = start_m
    if year == end_y:
        em = end_m
    return (sm, em)


def _months_in_year(start_y: int, start_m: int, end_y: int, end_m: int, year: int) -> int:
    sm, em = _month_range(year, start_y, start_m, end_y, end_m)
    if em < sm:
        return 0
    return em - sm + 1


def _year_candidates(prep: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    sp = (prep.get("start_period") or "").strip()
    ep = (prep.get("end_period") or "").strip()
    start = _parse_month(sp)
    end = _parse_month(ep)
    if not start or not end:
        return []
    start_y, start_m = start
    end_y, end_m = end
    max_year = min(end_y, now.year)
    if start_y > max_year:
        return []
    monthly = float(prep.get("monthly_expense_amount") or 0)
    out: List[Dict[str, Any]] = []
    for y in range(start_y, max_year + 1):
        months = _months_in_year(start_y, start_m, end_y, end_m, y)
        if months <= 0:
            continue
        amount = round(months * monthly, 2)
        out.append({"year": y, "months": months, "amount": amount})
    return out


def generate_candidates(db, now: datetime, user_id: str | None) -> Dict[str, int]:
    prepayments_col = db["prepayments"]
    queue_col = db["prepayment_recognition_queue"]

    generated = 0
    skipped_existing = 0
    skipped_posted = 0

    for prep in prepayments_col.find({"status": {"$ne": "archived"}}):
        prep_id = prep.get("_id")
        if not prep_id:
            continue
        posted_years = prep.get("posted_years") or []
        for cand in _year_candidates(prep, now):
            year = cand["year"]
            if year in posted_years:
                skipped_posted += 1
                continue
            key = f"prepayment:{prep_id}:year:{year}"
            existing = queue_col.find_one({
                "idempotency_key": key,
                "status": {"$in": ["pending", "confirmed", "posted"]},
            })
            if existing:
                skipped_existing += 1
                continue

            category = (prep.get("category") or "Prepayment").strip()
            expense_category = f"{category} Expense" if "expense" not in category.lower() else category
            description = f"Auto from Prepayments ({category}) - entered on prepayments page"

            queue_col.insert_one({
                "prepayment_id": prep_id,
                "year": year,
                "category": category,
                "vendor": (prep.get("vendor") or "").strip(),
                "recognition_date": datetime(year, 12, 31, 0, 0, 0),
                "amount": cand["amount"],
                "currency": prep.get("currency") or "GHS",
                "description": description,
                "expense_category": expense_category,
                "payment_method": "Bank Transfer",
                "ledger_entry_type": "prepayment_expense",
                "status": "pending",
                "idempotency_key": key,
                "created_at": datetime.utcnow(),
                "created_by": user_id,
            })
            generated += 1

    return {
        "generated": generated,
        "skipped_existing": skipped_existing,
        "skipped_posted": skipped_posted,
    }


def confirm_candidates(db, ids: List[str], user_id: str | None) -> Dict[str, int]:
    queue_col = db["prepayment_recognition_queue"]
    obj_ids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    res = queue_col.update_many(
        {"_id": {"$in": obj_ids}, "status": "pending"},
        {"$set": {"status": "confirmed", "confirmed_at": datetime.utcnow(), "confirmed_by": user_id}},
    )
    return {"confirmed": int(res.modified_count)}


def reject_candidates(db, ids: List[str], user_id: str | None, note: str | None = None) -> Dict[str, int]:
    queue_col = db["prepayment_recognition_queue"]
    obj_ids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    res = queue_col.update_many(
        {"_id": {"$in": obj_ids}, "status": "pending"},
        {"$set": {"status": "rejected", "rejected_at": datetime.utcnow(), "rejected_by": user_id, "note": (note or "").strip()}},
    )
    return {"rejected": int(res.modified_count)}


def _compute_recognition_totals(prep: Dict[str, Any], posted_years: List[int]) -> Tuple[float, float]:
    sp = (prep.get("start_period") or "").strip()
    ep = (prep.get("end_period") or "").strip()
    start = _parse_month(sp)
    end = _parse_month(ep)
    if not start or not end:
        return (0.0, float(prep.get("amount_total") or 0))
    start_y, start_m = start
    end_y, end_m = end
    monthly = float(prep.get("monthly_expense_amount") or 0)
    recognized = 0.0
    for y in posted_years:
        months = _months_in_year(start_y, start_m, end_y, end_m, y)
        recognized += months * monthly
    recognized = round(recognized, 2)
    total = float(prep.get("amount_total") or 0)
    remaining = round(max(total - recognized, 0.0), 2)
    return (recognized, remaining)


def post_confirmed(db, ids: Optional[List[str]], user_id: str | None) -> Dict[str, Any]:
    queue_col = db["prepayment_recognition_queue"]
    prepayments_col = db["prepayments"]
    expenses_col = db["expenses"]
    ledger_col = db["private_ledger_entries"]

    q = {"status": "confirmed"}
    if ids:
        obj_ids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
        q["_id"] = {"$in": obj_ids}

    posted = 0
    skipped = 0
    errors: List[str] = []

    for item in queue_col.find(q):
        if item.get("status") == "posted":
            skipped += 1
            continue
        prepayment_id = item.get("prepayment_id")
        year = int(item.get("year") or 0)
        id_key = item.get("idempotency_key") or ""
        if not prepayment_id or not year or not id_key:
            errors.append(str(item.get("_id")))
            continue

        prep = prepayments_col.find_one({"_id": prepayment_id}) or {}
        posted_years = prep.get("posted_years") or []
        if year in posted_years:
            queue_col.update_one(
                {"_id": item["_id"]},
                {"$set": {"status": "posted", "posted_at": datetime.utcnow(), "posted_by": user_id}},
            )
            skipped += 1
            continue

        if expenses_col.find_one({"source_ref": id_key}) or ledger_col.find_one({"idempotency_key": id_key}):
            queue_col.update_one(
                {"_id": item["_id"]},
                {"$set": {"status": "posted", "posted_at": datetime.utcnow(), "posted_by": user_id}},
            )
            posted_years = list(set(posted_years + [year]))
            rec, rem = _compute_recognition_totals(prep, posted_years)
            prepayments_col.update_one(
                {"_id": prepayment_id},
                {"$set": {"recognized_amount": rec, "remaining_amount": rem, "status": "active" if rem > 0 else "closed"},
                 "$addToSet": {"posted_years": year}},
            )
            skipped += 1
            continue

        now = datetime.utcnow()
        try:
            expenses_col.insert_one({
                "date": item.get("recognition_date") or now,
                "amount": float(item.get("amount") or 0),
                "category": item.get("expense_category") or "Prepayment Expense",
                "payment_method": "Bank Transfer",
                "description": item.get("description") or "Prepayment recognition",
                "source_ref": id_key,
                "created_at": now,
                "updated_at": now,
            })
            ledger_col.insert_one({
                "entry_type": "prepayment_expense",
                "source_account_type": "bank",
                "source_account_id": None,
                "date_dt": item.get("recognition_date") or now,
                "amount": float(item.get("amount") or 0),
                "purpose_text": item.get("description") or "Prepayment recognition",
                "idempotency_key": id_key,
                "created_by": user_id,
                "status": "posted",
                "created_at": now,
            })
        except Exception:
            errors.append(str(item.get("_id")))
            continue

        posted_years = list(set(posted_years + [year]))
        rec, rem = _compute_recognition_totals(prep, posted_years)
        prepayments_col.update_one(
            {"_id": prepayment_id},
            {"$set": {"recognized_amount": rec, "remaining_amount": rem, "status": "active" if rem > 0 else "closed"},
             "$addToSet": {"posted_years": year}},
        )
        queue_col.update_one(
            {"_id": item["_id"]},
            {"$set": {"status": "posted", "posted_at": now, "posted_by": user_id}},
        )
        posted += 1

    return {"posted": posted, "skipped": skipped, "errors": errors}
