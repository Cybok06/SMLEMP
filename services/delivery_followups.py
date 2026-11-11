"""Create one durable customer-support confirmation task per customer card."""
import hashlib
from datetime import datetime, timedelta, timezone

from bson import ObjectId


def ensure_delivery_followup(database, *, package=None, customer=None, product_index=None,
                             trigger="delivered", event_at=None, session=None):
    package, customer = package or {}, customer or {}
    customer_id = package.get("customer_id") or customer.get("_id")
    index = package.get("product_index") if product_index is None else product_index
    if not customer_id:
        raise ValueError("A customer is required for a delivery follow-up.")
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = None
    # Package identity is a fallback for legacy records without a card index.
    key = f"{customer_id}:{index}" if index is not None else f"package:{package.get('_id')}"
    task_id = ObjectId(hashlib.sha256(f"support-delivery:{key}".encode()).hexdigest()[:24])
    purchases = customer.get("purchases") or []
    purchase = purchases[index] if index is not None and 0 <= index < len(purchases) else {}
    product = package.get("product") or purchase.get("product") or {}
    now = event_at or datetime.utcnow()
    if not isinstance(now, datetime):
        try:
            now = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        except ValueError:
            now = datetime.utcnow()
    if now.tzinfo:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    options = {"session": session} if session is not None else {}
    row = {"task_no": f"DCF-{str(task_id).upper()}", "kind": "delivery_confirmation",
        "title": f"Confirm receipt: {product.get('name') or 'Customer item'}",
        "message": "Confirm full receipt and customer satisfaction; record outstanding issues.",
        "description": "Contact the customer to confirm the full item was received, record a 1–5 star rating, and follow up on any outstanding issue.",
        "customer_id": customer_id, "customer_name": package.get("customer_name") or customer.get("name") or "Customer",
        "customer_phone": package.get("customer_phone") or customer.get("phone_number") or "",
        "product_name": product.get("name") or "Customer item", "product_index": index,
        "branch_name": package.get("manager_branch") or customer.get("branch") or customer.get("agent_branch") or "",
        "target_type": "customer_support", "assignee_name": "Customer Support", "status": "pending", "category": "Delivery",
        "priority": "High", "related_to": f"Customer card {index + 1}" if index is not None else str(package.get("_id") or ""),
        "due_date": now + timedelta(days=1), "timestamp": now, "created_at": now, "updated_at": now,
        "created_by_role": "system", "review_version": 0}
    update = {"$setOnInsert": row, "$addToSet": {"delivery_triggers": trigger}}
    if package.get("_id"):
        update["$set"] = {"package_id": package["_id"]}
    database.tasks.update_one({"_id": task_id}, update, upsert=True, **options)
    if package.get("_id"):
        database.packages.update_one({"_id": package["_id"]}, {"$set": {"support_followup_task_id": task_id}}, **options)
    return task_id


def reconcile_delivery_followups(database, limit=200):
    """Bounded, idempotent repair with persistent retry scheduling and progress.

    Each source is scanned for at most `limit` eligible events. Invalid events
    back off instead of permanently starving later events. No review is reset.
    """
    limit = max(1, min(int(limit), 1000))
    now = datetime.utcnow()
    progress = {'processed': 0, 'repaired': 0, 'retry': 0}
    for name, condition in [('packages', {'status': 'delivered'}), ('card_closures', {'action': 'close_card'})]:
        collection = database[name]
        query = {**condition, 'support_followup_task_id': {'$exists': False},
                 '$or': [{'support_repair.next_attempt': {'$exists': False}}, {'support_repair.next_attempt': {'$lte': now}}]}
        for source in collection.find(query).sort('_id', 1).limit(limit):
            progress['processed'] += 1
            try:
                if name == 'packages':
                    ensure_delivery_followup(database, package=source, event_at=source.get('delivered_at') or source.get('updated_at') or source.get('created_at'))
                else:
                    raw_id = source.get('customer_id')
                    variants = [raw_id]
                    if ObjectId.is_valid(str(raw_id)):
                        variants += [ObjectId(str(raw_id)), str(raw_id)]
                    customer = database.customers.find_one({'_id': {'$in': variants}})
                    index = (source.get('payload') or {}).get('selected_product_index')
                    if not customer or index is None:
                        raise ValueError('Missing closure customer or card index')
                    customer_id = customer['_id']
                    package = database.packages.find_one({'customer_id': {'$in': [customer_id, str(customer_id)]}, 'product_index': {'$in': [index, str(index)]}})
                    task_id = ensure_delivery_followup(database, customer=customer, package=package, product_index=index, trigger='card_closed', event_at=source.get('at') or source.get('created_at'))
                    collection.update_one({'_id': source['_id']}, {'$set': {'support_followup_task_id': task_id}})
                collection.update_one({'_id': source['_id']}, {'$unset': {'support_repair': ''}})
                progress['repaired'] += 1
            except Exception as error:
                attempts = int((source.get('support_repair') or {}).get('attempts', 0)) + 1
                collection.update_one({'_id': source['_id']}, {'$set': {'support_repair': {
                    'attempts': attempts, 'error_type': type(error).__name__,
                    'next_attempt': now + timedelta(minutes=min(1440, 2 ** min(attempts, 11)))}}})
                progress['retry'] += 1
    return progress
