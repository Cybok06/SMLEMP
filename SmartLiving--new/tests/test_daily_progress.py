import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock
import pytest
from flask import Flask
from bson import ObjectId

@pytest.fixture
def setup(monkeypatch):
    fake = ModuleType("db")
    fake.db = MagicMock()
    monkeypatch.setitem(sys.modules, "db", fake)
    root = Path(__file__).resolve().parents[1]
    for name, path in [("routes.executive_target", "routes/executive_target.py"), ("routes.daily_progress", "routes/daily_progress.py")]:
        spec = importlib.util.spec_from_file_location(name, root / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    module.db = MagicMock()
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.secret_key = "test"
    app.register_blueprint(module.daily_progress_bp)
    app.add_url_rule("/agent", "dashboard_agent.agent_dashboard", lambda: "agent")
    app.add_url_rule("/manager", "manager_target.manager_targets", lambda: "manager")
    app.add_url_rule("/targets", "executive_targets_analytics.executive_targets_analytics_page", lambda: "targets")
    return module, app

@pytest.mark.parametrize("role", ["executive", "manager", "agent"])
def test_role_cash_scope(setup, role):
    module, app = setup
    mid, aid = ObjectId(), ObjectId()
    user = {"_id": aid if role == "agent" else mid, "manager_id": mid}
    targets = [{"manager_id": mid, "cash_target": 200, "agent_allocations": [{"agent_id": str(aid), "cash_quota": 50}]}]
    if role == "executive": targets.append({"manager_id": ObjectId(), "cash_target": 400})
    module.db.__getitem__.return_value.find.side_effect = [targets] + ([] if role == "agent" else [[{"_id": aid}]])
    module._aggregate_payments_by_agent = MagicMock(return_value={str(aid): 120})
    with app.test_request_context(): data = module.cash_snapshot(role, user)
    assert data["target"] == {"executive":600, "manager":200, "agent":50}[role]
    assert data["percent"] == {"executive":20, "manager":60, "agent":240}[role]
    assert data["collected"] == 120
    if role == "agent": assert data["exceeded"] == 70
    queries = module.db.__getitem__.return_value.find.call_args_list
    assert queries[0].args[0]["is_active"] is True
    if role != "executive": assert queries[0].args[0]["manager_id"] == mid


def test_pending_and_zero_target(setup):
    module, app = setup
    module.db.__getitem__.return_value.find.return_value = []
    module._aggregate_payments_by_agent = MagicMock(return_value={})
    with app.test_request_context():
        data = module.cash_snapshot("agent", {"_id": ObjectId()})
    assert data["percent"] == 0
    assert "not been assigned" in data["empty_message"]


def test_dismiss_requires_token_and_authentication(setup):
    module, app = setup
    client = app.test_client()
    assert client.get("/dashboard/daily-progress").status_code == 403
    with client.session_transaction() as session:
        session.update(role="agent", agent_id=str(ObjectId()), daily_progress_token="secret")
    module.db.__getitem__.return_value.find_one.return_value = {"role":"agent"}
    assert client.post("/dashboard/daily-progress/dismiss", json={"token":"wrong"}).status_code == 403
    assert client.post("/dashboard/daily-progress/dismiss", json={"token":"secret"}).status_code == 200
    with client.session_transaction() as session: assert session["daily_progress_seen"] is True


def test_context_only_on_dashboard_once_per_login(setup):
    from flask import session
    module, app = setup
    with app.test_request_context('/agent'):
        session.update(role="agent", agent_id=str(ObjectId()))
        assert "daily_progress_token" in module.dashboard_progress_context()
        session["daily_progress_seen"] = True
        assert module.dashboard_progress_context() == {}
        session.pop("daily_progress_seen")
        assert "daily_progress_token" in module.dashboard_progress_context()
    with app.test_request_context('/targets'):
        session.update(role="agent", agent_id=str(ObjectId()))
        assert module.dashboard_progress_context() == {}


def test_modal_renders(setup):
    module, app = setup
    with app.test_request_context():
        html = app.jinja_env.get_template("partials/daily_progress_modal.html").render(daily_progress_token="test")
    assert "OKAY" in html and "View Targets" in html and "showModal" in html
