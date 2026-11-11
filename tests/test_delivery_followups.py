import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from bson import ObjectId

from test_customer_support_targets import flow
from services.delivery_followups import ensure_delivery_followup, reconcile_delivery_followups


@pytest.fixture
def delivery_flow(flow, monkeypatch):
    client, database, _, login, officer, _, customer = flow
    repeat_name = "customer_support_backend.repeat_purchases_api"
    repeat_spec = importlib.util.spec_from_file_location(repeat_name, Path(__file__).resolve().parents[1] / "customer_support_backend/repeat_purchases_api.py")
    repeat_module = importlib.util.module_from_spec(repeat_spec)
    monkeypatch.setitem(sys.modules, repeat_name, repeat_module)
    repeat_spec.loader.exec_module(repeat_module)
    client.application.register_blueprint(repeat_module.repeat_purchase_bp)
    name = "customer_support_backend.delivery_reviews_api"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "customer_support_backend/delivery_reviews_api.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    client.application.register_blueprint(module.delivery_reviews_bp)
    database.customers.update_one({"_id": ObjectId(customer)}, {"$set": {"phone_number": "0241234567", "purchases": [{"product": {"name": "Fridge", "status": "active"}}]}})
    package = {"_id": ObjectId(), "customer_id": ObjectId(customer), "customer_name": "Customer", "customer_phone": "0241234567", "product_index": 0,
        "product": {"name": "Fridge", "quantity": 1}, "status": "delivered", "created_at": datetime.utcnow(), "delivered_at": datetime.utcnow()}
    database.packages.insert_one(package)
    task_id = ensure_delivery_followup(database, package=package)
    return client, database, login, officer, customer, package, str(task_id)


def review(**changes):
    return {"repeat_interest": "no", "contacted": True, "receipt": "full", "satisfaction": "satisfied", "rating": 5,
        "issue": "", "notes": "Customer confirmed receipt of the full item in good condition.", "version": 0, **changes}


def test_events_share_one_task_and_do_not_reset_reviews(delivery_flow):
    client, database, _, _, customer_id, package, task_id = delivery_flow
    assert client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review()).status_code == 200
    customer = database.customers.find_one({"_id": ObjectId(customer_id)})
    for _ in range(3):
        assert str(ensure_delivery_followup(database, customer=customer, product_index=0, trigger="card_closed")) == task_id
        ensure_delivery_followup(database, package=package)
    assert database.tasks.count_documents({"kind": "delivery_confirmation"}) == 1
    task = database.tasks.find_one({"_id": ObjectId(task_id)})
    assert task["status"] == "completed"
    assert task["latest_review"]["rating"] == 5
    assert sorted(task["delivery_triggers"]) == ["card_closed", "delivered"]


def test_full_receipt_satisfied_completes_task(delivery_flow):
    client, database, _, officer, _, package, task_id = delivery_flow
    response = client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review())
    assert response.status_code == 200
    item = response.json["followup"]
    assert item["status"] == "completed" and item["review"]["officer_id"] == officer
    assert item["version"] == 1 and len(item["history"]) == 1
    task = next(row for row in client.get("/api/customer-support/tasks").json["tasks"] if row["_id"] == task_id)
    assert task["status"] == "Completed" and task["deliveryFollowupId"] == task_id
    delivery = client.get("/api/customer-support/deliveries").json["deliveries"][0]
    assert delivery["satisfaction"]["taskId"] == task_id
    assert delivery["satisfaction"]["rating"] == 5
    assert client.get("/api/customer-support/delivery-review-summary").json["summary"]["confirmed"] == 1


def test_issue_stays_open_then_resolution_keeps_history(delivery_flow):
    client, database, _, _, _, _, task_id = delivery_flow
    next_date = (datetime.utcnow() + timedelta(days=2)).date().isoformat()
    response = client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review(receipt="partial", satisfaction="issue", rating=2, issue="Missing power cable", next_followup_date=next_date))
    assert response.status_code == 200 and response.json["followup"]["status"] == "in progress"
    assert response.json["followup"]["due_date"] == next_date
    assert client.get("/api/customer-support/delivery-review-summary").json["summary"]["outstanding_issues"] == 1
    resolved = client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review(version=1, notes="Cable delivered; customer now confirms full receipt."))
    assert resolved.status_code == 200
    assert resolved.json["followup"]["status"] == "completed"
    assert resolved.json["followup"]["history"][0]["issue"] == "Missing power cable"
    summary = client.get("/api/customer-support/delivery-review-summary").json["summary"]
    assert summary["rated"] == 1 and summary["average_rating"] == 5 and summary["outstanding_issues"] == 0


def test_unreachable_has_no_rating_and_remains_open(delivery_flow):
    client, _, _, _, _, _, task_id = delivery_flow
    response = client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review(contacted=False, notes="No answer; will retry tomorrow.", next_followup_date=(datetime.utcnow()+timedelta(days=1)).date().isoformat()))
    assert response.status_code == 200
    assert response.json["followup"]["review"]["rating"] is None
    assert response.json["followup"]["review"]["receipt"] == "unknown"
    assert response.json["followup"]["status"] == "in progress"


@pytest.mark.parametrize("changes", [{"rating": 0}, {"rating": 6}, {"rating": True}, {"rating": 2.5}, {"receipt": "invalid"}, {"satisfaction": "invalid"}, {"notes": ""}, {"contacted": "false"}, {"receipt": "partial"}, {"satisfaction": "issue"}, {"issue": "Damage"}, {"contacted": False}])
def test_review_validation(delivery_flow, changes):
    client, database, _, _, _, _, task_id = delivery_flow
    assert client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review(**changes)).status_code == 400
    assert database.tasks.find_one({"_id": ObjectId(task_id)})["review_version"] == 0


def test_stale_review_and_generic_task_bypass_rejected(delivery_flow):
    client, database, _, _, _, _, task_id = delivery_flow
    assert client.patch(f"/api/customer-support/tasks/{task_id}", json={"status": "Completed"}).status_code == 409
    assert client.delete(f"/api/customer-support/tasks/{task_id}").status_code == 409
    assert client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review()).status_code == 200
    assert client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review(rating=1)).status_code == 409
    assert len(database.tasks.find_one({"_id": ObjectId(task_id)})["delivery_reviews"]) == 1


def test_backfill_includes_closed_card_without_package(delivery_flow):
    client, database, _, _, customer_id, _, _ = delivery_flow
    customer = database.customers.find_one({"_id": ObjectId(customer_id)})
    database.customers.update_one({"_id": customer["_id"]}, {"$push": {"purchases": {"product": {"name": "TV", "status": "closed"}}}})
    closure_id = database.card_closures.insert_one({"action": "close_card", "customer_id": customer["_id"], "at": datetime.utcnow(), "payload": {"selected_product_index": 1}}).inserted_id
    for _ in range(2):
        reconcile_delivery_followups(database)
        result = client.get("/api/customer-support/delivery-followups").json
    assert result["open_count"] == 2
    closed_card = next(row for row in result["followups"] if row["product_name"] == "TV")
    assert closed_card["package_id"] == "" and closed_card["triggers"] == ["card_closed"]
    assert database.card_closures.find_one({"_id": closure_id})["support_followup_task_id"]
    first = client.get("/api/customer-support/delivery-followups?per_page=1&page=1").json
    second = client.get("/api/customer-support/delivery-followups?per_page=1&page=2").json
    assert first["pagination"]["total_pages"] == 2
    assert first["followups"][0]["id"] != second["followups"][0]["id"]


def test_card_closure_route_creates_followup_immediately(delivery_flow, monkeypatch):
    client, database, login, officer, customer_id, _, task_id = delivery_flow
    name = "close_card_under_test"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "close_card.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client.application.register_blueprint(module.close_card_bp)
    database.users.update_one({"_id": ObjectId(officer)}, {"$set": {"role": "admin"}})
    with client.session_transaction() as state:
        state["admin_id"] = officer
    response = client.post("/close_card/execute", json={"customer_id": customer_id, "product_index": 0, "note": "Close card"})
    assert response.status_code == 200
    assert "card_closed" in database.tasks.find_one({"_id": ObjectId(task_id)})["delivery_triggers"]
    assert database.card_closures.find_one()["support_followup_task_id"] == ObjectId(task_id)


@pytest.mark.parametrize("role", ["agent", "admin", "executive", "manager"])
def test_only_support_can_record_review(delivery_flow, role):
    client, _, login, _, _, _, task_id = delivery_flow
    login(role)
    assert client.post(f"/api/customer-support/delivery-followups/{task_id}", json=review()).status_code == 403
    assert client.get("/api/customer-support/delivery-followups").status_code == 403
