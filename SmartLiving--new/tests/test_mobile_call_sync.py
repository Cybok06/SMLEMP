import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from bson import ObjectId
from flask import Flask


class MemoryCollection:
    def __init__(self, rows=None):
        self.rows = [deepcopy(row) for row in (rows or [])]

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        rows = self.rows
        if query.get("phone_number", {}).get("$exists"):
            rows = [row for row in rows if row.get("phone_number")]
        return [deepcopy(row) for row in rows]

    def find_one(self, query, projection=None, **kwargs):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return deepcopy(row)
        return None

    def insert_one(self, row):
        saved = deepcopy(row)
        saved.setdefault("_id", ObjectId())
        self.rows.append(saved)
        return types.SimpleNamespace(inserted_id=saved["_id"])


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getattr__(self, name):
        return self.collections.setdefault(name, MemoryCollection())


def load_module(database):
    fake_db = types.ModuleType("db")
    fake_db.db = database
    fake_login = types.ModuleType("login")
    fake_login.get_current_identity = lambda: {"role": "customer_support", "user_id": "test"}
    fake_login.role_required = lambda *roles: (lambda fn: fn)
    path = Path(__file__).parents[1] / "customer_support_backend" / "tickets_calls_api.py"
    spec = importlib.util.spec_from_file_location("mobile_sync_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"db": fake_db, "login": fake_login}):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def api():
    database = FakeDatabase()
    database.customers.rows.append({"_id": ObjectId(), "name": "Matched Customer", "phone_number": "0530393625", "branch": "HQ"})
    module = load_module(database)
    app = Flask(__name__)
    app.register_blueprint(module.customer_support_operations_bp)
    return app.test_client(), database


def call(external_id, call_type="outbound", phone="0530393625"):
    return {"external_call_id": external_id, "phone_number": phone, "from_number": "0240000001",
            "call_type": call_type, "started_at": "2026-08-13T00:20:00Z", "duration_seconds": 120,
            "sim_account": "SIM1"}


def post(client, calls, device_id="HQ-Phone"):
    return client.post("/api/customer-support/mobile/calls/sync", json={"device_id": device_id, "calls": calls})


def test_post_without_authorization_creates_outbound_and_matches_customer(api):
    client, database = api
    response = post(client, [call("OUT-1")])
    assert response.status_code == 200
    assert response.get_json()["created"] == 1
    saved = database.customer_support_calls.rows[0]
    assert saved["type"] == "Outbound"
    assert saved["customer_match"] == "matched"
    assert saved["customer_name"] == "Matched Customer"
    assert saved["source"] == "android"
    assert saved["enrichment_status"] == "needs_update"


@pytest.mark.parametrize("call_type,expected", [("inbound", "Inbound"), ("missed", "Missed")])
def test_inbound_and_missed_calls_are_created(api, call_type, expected):
    client, database = api
    response = post(client, [call(f"TYPE-{call_type}", call_type)])
    assert response.status_code == 200
    saved = database.customer_support_calls.rows[0]
    assert saved["type"] == expected
    assert saved["follow_up"] is (call_type == "missed")


def test_unknown_number_is_stored_as_not_customer(api):
    client, database = api
    response = post(client, [call("UNKNOWN-1", phone="0551234567")])
    assert response.status_code == 200
    saved = database.customer_support_calls.rows[0]
    assert saved["customer_id"] is None
    assert saved["customer_name"] is None
    assert saved["customer_match"] == "not_customer"


def test_duplicate_request_does_not_insert_again(api):
    client, database = api
    assert post(client, [call("DUP-1")]).get_json()["created"] == 1
    response = post(client, [call("DUP-1")])
    assert response.status_code == 200
    assert response.get_json()["duplicates"] == 1
    assert len(database.customer_support_calls.rows) == 1


@pytest.mark.parametrize("payload,error", [
    ({"device_id": "", "calls": [call("NO-DEVICE")]}, "device_id"),
    ({"device_id": "HQ-Phone", "calls": []}, "between 1 and 500"),
])
def test_invalid_top_level_input_is_rejected(api, payload, error):
    client, _database = api
    response = client.post("/api/customer-support/mobile/calls/sync", json=payload)
    assert response.status_code == 400
    assert error in response.get_json()["error"]


def test_invalid_call_type_is_reported(api):
    client, database = api
    response = post(client, [call("BAD-TYPE", "voicemail")])
    body = response.get_json()
    assert response.status_code == 400
    assert body["failed"] == 1
    assert body["results"][0]["status"] == "invalid"
    assert "call_type" in body["results"][0]["error"]
    assert not database.customer_support_calls.rows
