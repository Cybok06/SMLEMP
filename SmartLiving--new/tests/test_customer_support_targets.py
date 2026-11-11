import importlib.util
import sys
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from types import ModuleType

import mongomock
import pytest
from bson import ObjectId
from flask import Flask, jsonify, session
from jinja2 import ChoiceLoader, DictLoader


@pytest.fixture
def flow(monkeypatch):
    database = mongomock.MongoClient().test
    fake_db, fake_login = ModuleType("db"), ModuleType("login")
    fake_db.db = database
    fake_login.get_current_identity = lambda: {"user_id": session.get("user_id"), "role": session.get("role"), "name": "Officer"}
    def role_required(*roles):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not session.get("user_id"):
                    return jsonify(ok=False), 401
                if session.get("role") not in roles:
                    return jsonify(ok=False), 403
                return fn(*args, **kwargs)
            return wrapped
        return decorate
    fake_login.role_required = role_required
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setitem(sys.modules, "login", fake_login)
    root = Path(__file__).resolve().parents[1]
    loaded = []
    for name in ("targets_api", "tickets_calls_api"):
        full_name = f"customer_support_backend.{name}"
        spec = importlib.util.spec_from_file_location(full_name, root / "customer_support_backend" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full_name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    targets, operations = loaded
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.secret_key = "test"
    app.jinja_loader = ChoiceLoader([DictLoader({"executive_sidebar.html": "Executive navigation", "admin_sidebar.html": "Admin navigation"}), app.jinja_loader])
    app.register_blueprint(targets.support_targets_bp)
    app.register_blueprint(operations.customer_support_operations_bp)
    client = app.test_client()
    officer, other, customer = (ObjectId() for _ in range(3))
    database.users.insert_many([{"_id": officer, "role": "customer_support", "name": "Officer"}, {"_id": other, "role": "customer_support", "name": "Other"}])
    database.customers.insert_one({"_id": customer, "name": "Customer"})
    def login(role="customer_support", user_id=officer):
        with client.session_transaction() as state:
            state.clear(); state.update(role=role, user_id=str(user_id))
    login()
    return client, database, targets, login, str(officer), str(other), str(customer)


def target(flow, metric, period, amount):
    client, _, _, login, officer, *_ = flow
    login("executive")
    response = client.post("/api/executive/customer-support/targets", json={"officer_id": officer, "metric": metric, "period": period, "amount": amount})
    assert response.status_code == 200
    login()


def visit_payload():
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return {"location": "Madina market", "notes": "Visited shops and spoke to customers", "started_at": start.isoformat(), "ended_at": (start + timedelta(hours=2)).isoformat()}


def activity(flow, visit_id, occurred_at=None):
    client, _, _, _, _, _, customer = flow
    return client.post(f"/api/customer-support/customers/{customer}/activities", json={"title": "Field conversation", "description": "Discussed customer's delivery", "category": "Interaction", "field_visit_id": visit_id, "occurred_at": occurred_at or visit_payload()["started_at"]})


def test_call_logging_and_edit_count_once_for_officer(flow):
    client, database, _, _, officer, other, customer = flow
    target(flow, "calls", "daily", 2)
    response = client.post("/api/customer-support/calls", json={"customer_id": customer, "type": "Outbound", "purpose": "Follow-up", "outcome": "Resolved", "department": "Support"})
    assert response.status_code == 201
    call_id = response.json["call"]["_id"]
    assert client.patch(f"/api/customer-support/calls/{call_id}", json={"purpose": "Updated", "outcome": "Resolved", "department": "Support"}).status_code == 200
    database.customer_support_calls.insert_one({"officer_id": other, "started_at": datetime.utcnow()})
    card = client.get("/api/customer-support/targets").json["targets"][0]
    assert (card["actual"], card["remaining"], card["percent"]) == (1, 1, 50)
    assert database.customer_support_calls.find_one({"_id": ObjectId(call_id)})["officer_id"] == officer


def test_synced_call_attributed_once_when_logged(flow):
    client, database, _, _, officer, _, _ = flow
    target(flow, "calls", "daily", 1)
    call_id = database.customer_support_calls.insert_one({"source": "android", "officer_id": "", "started_at": datetime.utcnow(), "enrichment_status": "needs_update"}).inserted_id
    body = {"purpose": "Follow-up", "outcome": "Resolved", "department": "Support"}
    for _ in range(2):
        assert client.patch(f"/api/customer-support/calls/{call_id}", json=body).status_code == 200
    assert client.get("/api/customer-support/targets").json["targets"][0]["actual"] == 1
    assert database.customer_support_calls.find_one({"_id": call_id})["officer_id"] == officer


def test_field_trip_and_interactions_progress(flow):
    client, database, module, _, officer, other, _ = flow
    target(flow, "field_trips", "weekly", 2)
    target(flow, "interactions", "daily", 2)
    target(flow, "interactions", "weekly", 2)
    target(flow, "interactions", "per_trip", 2)
    response = client.post("/api/customer-support/field-visits", json=visit_payload())
    assert response.status_code == 201
    visit_id = response.json["visit"]["id"]
    assert response.json["visit"]["duration_minutes"] == 120
    for _ in range(2):
        assert activity(flow, visit_id).status_code == 201
    database.customer_support_manual_activities.insert_one({"created_by": other, "category": "Interaction", "occurred_at": datetime.fromisoformat(visit_payload()["started_at"]), "field_visit_id": ObjectId(visit_id)})
    day = datetime.fromisoformat(visit_payload()["started_at"]).date()
    # Make settings effective on the reporting day without changing application clock.
    database.customer_support_targets.update_many({}, {"$set": {"effective_from": day.isoformat()}})
    result = client.get(f"/api/customer-support/targets?date={day}").json
    cards = {(c["metric"], c["period"]): c for c in result["targets"]}
    assert cards["field_trips", "weekly"]["actual"] == 1
    assert cards["interactions", "daily"]["actual"] == 2
    assert cards["interactions", "weekly"]["actual"] == 2
    assert cards["interactions", "per_trip"]["actual"] == 1
    assert result["visits"][0]["remaining"] == 0
    assert result["visits"][0]["interactions"] == 2


def test_interaction_requires_own_visit_and_valid_time(flow):
    client, _, _, login, _, other, _ = flow
    visit_id = client.post("/api/customer-support/field-visits", json=visit_payload()).json["visit"]["id"]
    assert activity(flow, "").status_code == 400
    assert activity(flow, visit_id, (datetime.fromisoformat(visit_payload()["ended_at"]) + timedelta(minutes=1)).isoformat()).status_code == 400
    login(user_id=other)
    assert activity(flow, visit_id).status_code == 400


@pytest.mark.parametrize("patch", [{"ended_at": "invalid"}, {"started_at": "invalid"}, {"notes": ""}, {"location": ""}, {"ended_at": (datetime.utcnow() + timedelta(days=1)).isoformat()}])
def test_visit_validation(flow, patch):
    client, database, *_ = flow
    assert client.post("/api/customer-support/field-visits", json={**visit_payload(), **patch}).status_code == 400
    assert database.customer_support_field_visits.count_documents({}) == 0


def test_duplicate_or_overlapping_visits_rejected(flow):
    client, database, *_ = flow
    assert client.post("/api/customer-support/field-visits", json=visit_payload()).status_code == 201
    assert client.post("/api/customer-support/field-visits", json=visit_payload()).status_code == 409
    assert database.customer_support_field_visits.count_documents({}) == 1


@pytest.mark.parametrize("metric,period,amount", [("calls", "per_trip", 70), ("field_trips", "daily", 2), ("interactions", "weekly", -1), ("calls", "weekly", 1.5), ("calls", "daily", True), ({}, "daily", 1)])
def test_invalid_targets_rejected(flow, metric, period, amount):
    client, database, _, login, officer, *_ = flow
    login("executive")
    assert client.post("/api/executive/customer-support/targets", json={"officer_id": officer, "metric": metric, "period": period, "amount": amount}).status_code == 400
    assert database.customer_support_targets.count_documents({}) == 0


def test_history_future_settings_and_disabling(flow):
    client, database, module, login, officer, *_ = flow
    target(flow, "calls", "daily", 70)
    tomorrow = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
    login("executive")
    assert client.post("/api/executive/customer-support/targets", json={"officer_id": officer, "metric": "calls", "period": "daily", "amount": 120, "effective_from": tomorrow}).status_code == 200
    today = datetime.utcnow().date()
    assert module.active_targets(officer, today)[0]["amount"] == 70
    assert module.active_targets(officer, today + timedelta(days=1))[0]["amount"] == 120
    assert len(client.get(f"/api/executive/customer-support/progress?officer_id={officer}").json["history"]) == 2
    target(flow, "calls", "daily", 0)
    assert client.get("/api/customer-support/targets").json["targets"] == []


def test_support_cannot_set_targets_or_read_other_officers(flow):
    client, _, _, login, _, other, _ = flow
    assert client.post("/api/executive/customer-support/targets", json={}).status_code == 403
    assert client.get(f"/api/executive/customer-support/progress?officer_id={other}").status_code == 403
    assert client.get(f"/api/customer-support/targets?officer_id={other}").json["officer_id"] != other
    login("executive")
    assert client.post("/api/customer-support/field-visits", json=visit_payload()).status_code == 403


def test_period_boundaries_and_executive_page(flow):
    client, _, module, login, *_ = flow
    start, end = module.period_window(date(2026, 10, 11), "weekly")
    assert start == datetime(2026, 10, 5) and end == datetime(2026, 10, 12)
    login("executive")
    response = client.get("/executive/customer-support")
    assert response.status_code == 200
    assert b"Configure target" in response.data


def test_weekly_calls_use_call_time_and_exact_boundaries(flow):
    client, database, module, _, officer, *_ = flow
    target(flow, "calls", "weekly", 80)
    start, end = module.period_window(datetime.utcnow().date(), "weekly")
    database.customer_support_calls.insert_many([
        {"officer_id": officer, "started_at": start, "created_at": end},
        {"officer_id": ObjectId(officer), "started_at": end - timedelta(seconds=1)},
        {"officer_id": officer, "started_at": end},
        {"officer_id": officer, "started_at": start - timedelta(seconds=1), "created_at": start},
        {"officer_id": officer, "started_at": None, "created_at": start},
    ])
    assert client.get("/api/customer-support/targets").json["targets"][0]["actual"] == 3


def test_invalid_interaction_time_does_not_create_activity(flow):
    client, database, *_ = flow
    visit_id = client.post("/api/customer-support/field-visits", json=visit_payload()).json["visit"]["id"]
    assert activity(flow, visit_id, "invalid").status_code == 400
    assert database.customer_support_manual_activities.count_documents({}) == 0


def test_admin_can_manage_same_targets_as_executive(flow):
    client, database, _, login, officer, *_ = flow
    login("admin")
    page = client.get("/admin/customer-support")
    assert page.status_code == 200
    assert b"Admin navigation" in page.data
    assert b'data-api-prefix="/api/admin/customer-support"' in page.data
    assert client.get("/api/admin/customer-support/officers").status_code == 200
    for metric, period in [("calls", "daily"), ("field_trips", "weekly"), ("interactions", "per_trip")]:
        assert client.post("/api/admin/customer-support/targets", json={"officer_id": officer, "metric": metric, "period": period, "amount": 80}).status_code == 200
    result = client.get(f"/api/admin/customer-support/progress?officer_id={officer}").json
    assert len(result["targets"]) == 3
    assert len(result["history"]) == 3
    login("executive")
    assert client.get(f"/api/executive/customer-support/progress?officer_id={officer}").json["targets"] == result["targets"]
    login()
    assert len(client.get("/api/customer-support/targets").json["targets"]) == 3


@pytest.mark.parametrize("role", ["customer_support", "agent", "manager"])
def test_non_management_cannot_use_admin_target_routes(flow, role):
    client, _, _, login, officer, *_ = flow
    login(role)
    assert client.get("/admin/customer-support").status_code == 403
    assert client.get("/api/admin/customer-support/officers").status_code == 403
    assert client.get(f"/api/admin/customer-support/progress?officer_id={officer}").status_code == 403
    assert client.post("/api/admin/customer-support/targets", json={}).status_code == 403
