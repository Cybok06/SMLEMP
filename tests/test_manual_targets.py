import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from flask import Flask
from werkzeug.datastructures import MultiDict


@pytest.fixture
def flow(monkeypatch):
    fake_db = ModuleType("db")
    fake_db.db = MagicMock()
    monkeypatch.setitem(sys.modules, "db", fake_db)
    root = Path(__file__).resolve().parents[1]
    modules = []
    for name, filename in [("routes.executive_target", "routes/executive_target.py"), ("manager_target", "manager_target.py")]:
        spec = importlib.util.spec_from_file_location(name, root / filename)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    executive, manager = modules
    collection = MagicMock()
    executive.executive_targets_col = manager.targets_collection = collection
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.secret_key = "test"
    app.register_blueprint(executive.executive_target_bp)
    app.register_blueprint(manager.manager_target_bp)
    client = app.test_client()
    mid, tid, aid, bid = (ObjectId() for _ in range(4))
    target = {"_id": tid, "manager_id": mid, "period": "daily", "is_active": True,
              "product_target": 3, "cash_target": 10.01, "customer_target": 1, "agent_allocations": []}
    collection.find_one.return_value = target
    manager.users_collection = MagicMock()
    manager.users_collection.find_one.return_value = {"_id": mid, "role": "manager"}
    manager.users_collection.find.return_value.sort.return_value = [{"_id": aid, "name": "A"}, {"_id": bid, "name": "B"}]
    executive.users_col = manager.users_collection
    with client.session_transaction() as session:
        session["manager_id"] = str(mid)
        session["executive_id"] = "executive"
    return client, executive, manager, collection, target, aid, bid


def payload(aid, bid):
    return MultiDict([("agent_id[]", str(aid)), ("agent_id[]", str(bid)),
                      ("product_quota[]", "2"), ("product_quota[]", "1"),
                      ("cash_quota[]", "7.01"), ("cash_quota[]", "3"),
                      ("customer_quota[]", "1"), ("customer_quota[]", "0")])


def test_creation_waits_for_manager_even_without_agents(flow):
    client, executive, manager, collection, target, aid, bid = flow
    response = client.post("/executive/targets/create", data={"manager_id": str(target["manager_id"]), "period": "daily", "product_target": "3", "cash_target": "10.01", "customer_target": "1"})
    assert response.status_code == 302
    saved = collection.insert_one.call_args.args[0]
    assert saved["agent_allocations"] == []
    assert saved["distribution_mode"] == "manual"
    executive.users_col.find.assert_not_called()
    assert client.post(f"/executive/targets/rebuild/{target['_id']}").status_code == 409


def test_creates_separate_product_and_susu_cash_targets(flow):
    client, executive, manager, collection, target, aid, bid = flow
    response = client.post('/executive/targets/create', data={
        'manager_id': str(target['manager_id']), 'period': 'monthly', 'product_target': '0',
        'cash_target': '0', 'customer_target': '0', 'product_cash_target': '200.50', 'susu_cash_target': '90.25'})
    assert response.status_code == 302
    saved = collection.insert_one.call_args.args[0]
    assert saved['product_cash_target'] == 200.50 and saved['susu_cash_target'] == 90.25
    assert saved['cash_target'] == 0 and saved['agent_allocations'] == []


@pytest.mark.parametrize('channel', ['product_cash', 'susu_cash'])
@pytest.mark.parametrize('value', ['-1', 'NaN', 'Infinity', '1.005'])
def test_invalid_channel_target_cannot_be_created(flow, channel, value):
    client, executive, manager, collection, target, aid, bid = flow
    response = client.post('/executive/targets/create', data={
        'manager_id': str(target['manager_id']), 'product_target': '1', 'cash_target': '0',
        'customer_target': '0', channel + '_target': value})
    assert response.status_code == 400
    collection.insert_one.assert_not_called()
    collection.update_many.assert_not_called()


def test_channel_quotas_are_distributed_and_tracked_separately(flow, monkeypatch):
    client, executive, manager, collection, target, aid, bid = flow
    target.update(product_cash_target=6.01, susu_cash_target=4.00)
    data = payload(aid, bid)
    data.setlist('product_cash_quota[]', ['4.01', '2'])
    data.setlist('susu_cash_quota[]', ['3', '1'])
    assert client.post(f"/manager/targets/distribute/{target['_id']}", data=data).status_code == 302
    target.update(collection.update_one.call_args.args[1]['$set'])
    executive._aggregate_payments_by_agent = lambda *args: {str(aid): 7.01, str(bid): 3}
    executive._aggregate_products_by_agent = lambda *args: {}
    executive._aggregate_customers_by_agent = lambda *args: {}
    executive._get_manager_agents = lambda *args: [{'id': str(aid)}, {'id': str(bid)}]
    executive.payments_col.aggregate.return_value = [
        {'_id': {'agent': str(aid), 'type': 'PRODUCT'}, 'total': 4.01},
        {'_id': {'agent': str(aid), 'type': 'SUSU'}, 'total': 3},
    ]
    summary = executive.compute_agent_target_progress(str(aid), target['manager_id'], 'daily')
    assert summary['quota']['product_cash_quota'] == 4.01
    assert summary['quota']['susu_cash_quota'] == 3
    assert summary['achieved']['product_cash_achieved'] == 4.01
    assert summary['achieved']['susu_cash_achieved'] == 3
    assert summary['percents']['product_cash_pct'] == summary['percents']['susu_cash_pct'] == 100
    team = executive.compute_manager_target_progress(target['manager_id'], 'daily')
    assert team['targets']['susu_cash_target'] == 4
    assert team['agents'][0]['product_cash_achieved'] == 4.01
    assert team['allocation_pending'] is False
    data.setlist('susu_cash_quota[]', ['3', '0'])
    monkeypatch.setattr(manager, 'render_template', lambda *args, **kwargs: kwargs.get('error') or 'page')
    collection.update_one.reset_mock()
    assert client.post(f"/manager/targets/distribute/{target['_id']}", data=data).status_code == 400
    collection.update_one.assert_not_called()


def test_executive_can_update_existing_target_without_losing_allocations(flow):
    client, executive, manager, collection, target, aid, bid = flow
    target['agent_allocations'] = [{'agent_id': str(aid), 'cash_quota': 5}]
    with client.session_transaction() as state:
        state['executive_targets_csrf'] = 'token'
    response = client.post(f"/executive/targets/update/{target['_id']}", data={
        'csrf_token': 'token', 'product_cash_target': '50.25', 'susu_cash_target': '75.50'})
    assert response.status_code == 302
    update = collection.update_one.call_args.args[1]
    assert update['$set']['cash_target'] == 10.01
    assert update['$set']['product_cash_target'] == 50.25
    assert update['$set']['susu_cash_target'] == 75.50
    assert 'agent_allocations' not in update['$set']
    assert 'change_history' in update['$push']


def test_executive_edit_checks_permissions_csrf_and_allocation_limits(flow):
    client, executive, manager, collection, target, aid, bid = flow
    endpoint = f"/executive/targets/update/{target['_id']}"
    assert client.post(endpoint).status_code == 400
    with client.session_transaction() as state:
        state['executive_targets_csrf'] = 'token'
    target['agent_allocations'] = [{'agent_id': str(aid), 'cash_quota': 5}]
    assert client.post(endpoint, data={'csrf_token': 'token', 'cash_target': '4'}).status_code == 400
    collection.update_one.assert_not_called()
    assert client.post(endpoint, data={'csrf_token': 'token', 'cash_target': '4', 'reset_allocations': 'yes'}).status_code == 302
    assert collection.update_one.call_args.args[1]['$set']['agent_allocations'] == []
    with client.session_transaction() as state:
        state.pop('executive_id')
    assert client.post(endpoint, data={'csrf_token': 'token'}).status_code == 401


def test_channel_aggregation_scopes_dates_agents_and_payment_types():
    from services.target_cash import collections_by_agent
    collection = MagicMock()
    aid = ObjectId()
    collection.aggregate.return_value = [
        {'_id': {'agent': str(aid), 'type': 'PRODUCT'}, 'total': 20.5},
        {'_id': {'agent': str(aid), 'type': 'SUSU'}, 'total': 7.25}]
    result = collections_by_agent(collection, [str(aid)], '2026-10-01', '2026-10-31')
    match = collection.aggregate.call_args.args[0][0]['$match']
    assert match['payment_type'] == {'$in': ['PRODUCT', 'SUSU']}
    assert match['date'] == {'$gte': '2026-10-01', '$lte': '2026-10-31'}
    assert set(match['agent_id']['$in']) == {aid, str(aid)}
    assert result[str(aid)] == {'product_cash': 20.5, 'susu_cash': 7.25}


def test_daily_dashboard_does_not_award_hit_for_under_target_susu():
    from services.target_cash import extend_daily_progress, extend_daily_totals
    collection = MagicMock()
    aid = str(ObjectId())
    collection.aggregate.return_value = [
        {'_id': {'agent': aid, 'type': 'PRODUCT'}, 'total': 200},
        {'_id': {'agent': aid, 'type': 'SUSU'}, 'total': 20}]
    progress = {aid: {'target_hit': True}}
    caps = {aid: {'product_cash_target_daily': 100, 'susu_cash_target_daily': 50}}
    extend_daily_progress(progress, caps, collection, '2026-10-22', '2026-10-22')
    assert progress[aid]['product_cash_progress_pct'] == 200
    assert progress[aid]['susu_cash_progress_pct'] == 40
    assert progress[aid]['target_hit'] is False
    totals = {}
    extend_daily_totals(totals, progress)
    assert totals['agents_with_targets_count'] == 1
    assert totals['agents_hit_target_count'] == 0
    assert totals['total_product_cash_achieved_today'] == 200


def test_executive_cash_dashboard_period_and_totals(flow, monkeypatch):
    client, executive, manager, collection, target, aid, bid = flow
    collection.find.return_value = [target]
    monkeypatch.setattr(executive, 'compute_manager_target_progress', lambda *args: {
        'targets': {'cash_target': 100, 'product_cash_target': 70, 'susu_cash_target': 30},
        'achieved': {'cash_achieved': 60, 'product_cash_achieved': 40, 'susu_cash_achieved': 20}})
    response = client.get('/executive/targets/cash-progress?period=weekly')
    assert response.status_code == 200
    assert response.json['totals']['susu_cash_achieved'] == 20
    assert response.json['totals']['product_cash_target'] == 70
    assert client.get('/executive/targets/cash-progress?period=invalid').status_code == 400


def test_target_edit_controls_and_cash_progress_render(flow):
    from jinja2 import ChoiceLoader, DictLoader
    from flask import render_template
    client, executive, manager, collection, target, aid, bid = flow
    env = client.application.jinja_env
    env.loader = ChoiceLoader([DictLoader({'executive_sidebar.html': ''}), env.loader])
    target.update(product_cash_target=12.25, susu_cash_target=8.5)
    collection.find.return_value.sort.return_value.limit.return_value = [target]
    response = client.get('/executive/targets')
    assert response.status_code == 200
    assert b'Product Cash' in response.data and b'SUSU Cash' in response.data
    assert b'name="product_cash_target"' in response.data and b'value="12.25"' in response.data
    assert b'Clear agent allocations' in response.data
    with client.session_transaction() as state:
        assert state['executive_targets_csrf']
    for quota in (False, True):
        summary = {'quota' if quota else 'targets': {
            ('product_cash_quota' if quota else 'product_cash_target'): 100,
            ('susu_cash_quota' if quota else 'susu_cash_target'): 50},
            'achieved': {'product_cash_achieved': 25, 'susu_cash_achieved': 30},
            'percents': {'product_cash_pct': 25, 'susu_cash_pct': 60}}
        with client.application.test_request_context():
            html = render_template('partials/target_cash_channels.html', t=summary)
        assert '25.00 / 100.00' in html and '30.00 / 50.00' in html


def test_manual_allocation_reaches_agent_progress(flow):
    client, executive, manager, collection, target, aid, bid = flow
    response = client.post(f"/manager/targets/distribute/{target['_id']}", data=payload(aid, bid))
    assert response.status_code == 302
    update = collection.update_one.call_args
    assert update.args[0]["manager_id"] == target["manager_id"]
    assert update.args[0]["is_active"] is True
    target.update(update.args[1]["$set"])
    executive._aggregate_payments_by_agent = lambda *args: {str(aid): 5}
    executive._aggregate_products_by_agent = lambda *args: {str(aid): 1}
    executive._aggregate_customers_by_agent = lambda *args: {str(aid): 1}
    summary = executive.compute_agent_target_progress(str(aid), target["manager_id"], "daily")
    assert summary["quota"] == {"product_quota": 2, "cash_quota": 7.01, "customer_quota": 1,
                                "product_cash_quota": 0.0, "susu_cash_quota": 0.0}
    assert summary["achieved"]["cash_achieved"] == 5
    assert executive.compute_agent_target_progress(str(ObjectId()), target["manager_id"], "daily") is None


@pytest.mark.parametrize("field,value", [("cash_quota[]", "NaN"), ("cash_quota[]", "-1"), ("cash_quota[]", "1.001"), ("product_quota[]", "1.5"), ("product_quota[]", "100"), ("agent_id[]", "invalid")])
def test_invalid_allocations_never_save(flow, field, value, monkeypatch):
    client, executive, manager, collection, target, aid, bid = flow
    monkeypatch.setattr(manager, "render_template", lambda *args, **kwargs: kwargs.get("error") or "page")
    data = payload(aid, bid)
    values = data.getlist(field)
    values[0] = value
    data.setlist(field, values)
    response = client.post(f"/manager/targets/distribute/{target['_id']}", data=data)
    assert response.status_code == 400
    collection.update_one.assert_not_called()


def test_foreign_inactive_and_invalid_targets_rejected(flow):
    client, executive, manager, collection, target, aid, bid = flow
    collection.find_one.return_value = None
    assert client.post(f"/manager/targets/distribute/{target['_id']}", data=payload(aid, bid)).status_code == 404
    assert client.get("/manager/targets/distribute/invalid").status_code == 404
    collection.update_one.assert_not_called()


def test_no_allocation_means_no_agent_target(flow):
    client, executive, manager, collection, target, aid, bid = flow
    executive._aggregate_payments_by_agent = lambda *args: {}
    executive._aggregate_products_by_agent = lambda *args: {}
    executive._aggregate_customers_by_agent = lambda *args: {}
    assert executive.compute_agent_target_progress(str(aid), target["manager_id"], "daily") is None


def test_templates_parse(flow):
    client, *_ = flow
    client.application.jinja_env.filters['format_money'] = lambda value: f'{float(value):,.2f}'
    for name in ("manager_target.html", "manager_target_distribute.html", "executive/targets.html", "manager_dashboard.html", "manager_sidebar.html", 'dashboard_agent.html', 'executive_dashboard.html', 'partials/target_cash_channels.html', 'partials/executive_cash_targets.html'):
        client.application.jinja_env.get_template(name)


@pytest.mark.parametrize("field,value", [("period", "quarterly"), ("cash_target", "NaN"), ("product_target", "-1"), ("customer_target", "0.5")])
def test_invalid_executive_targets_rejected(flow, field, value):
    client, executive, manager, collection, target, aid, bid = flow
    data = {"manager_id": str(target["manager_id"]), "period": "daily", "product_target": "3", "cash_target": "10.01", "customer_target": "1"}
    data[field] = value
    assert client.post("/executive/targets/create", data=data).status_code == 400
    collection.insert_one.assert_not_called()
    collection.update_many.assert_not_called()


def test_manager_dashboard_does_not_invent_quotas(flow, monkeypatch):
    client, executive, manager, collection, target, aid, bid = flow
    spec = importlib.util.spec_from_file_location("target_test_dashboard", Path(__file__).resolve().parents[1] / "manager_dashboard.py")
    dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard)
    dashboard.executive_targets_col = collection
    dashboard.users_collection = MagicMock()
    dashboard.users_collection.find.return_value = [{"_id": aid, "name": "A"}]
    dashboard.payments_collection = MagicMock()
    dashboard.customers_collection = MagicMock()
    progress, totals = dashboard._daily_target_context(str(target["manager_id"]))
    assert progress[str(aid)]["cash_target_daily"] == 0
    assert progress[str(aid)]["product_target_daily"] == 0
    assert totals["agents_with_targets_count"] == 0


def test_manager_pages_render_with_pending_and_assigned_targets(flow, monkeypatch):
    from jinja2 import ChoiceLoader, DictLoader
    client, executive, manager, collection, target, aid, bid = flow
    env = client.application.jinja_env
    env.loader = ChoiceLoader([DictLoader({"manager_sidebar.html": "", "app_footer.html": ""}), env.loader])
    executive._aggregate_payments_by_agent = lambda *args: {}
    executive._aggregate_products_by_agent = lambda *args: {}
    executive._aggregate_customers_by_agent = lambda *args: {}
    executive._get_manager_agents = lambda *args: [{"id": str(aid), "name": "A"}]
    response = client.get("/manager/targets/")
    assert response.status_code == 200
    assert b"Awaiting your distribution" in response.data
    response = client.get(f"/manager/targets/distribute/{target['_id']}")
    assert response.status_code == 200
    assert b"product_quota[]" in response.data
    client.post(f"/manager/targets/distribute/{target['_id']}", data=payload(aid, bid))
    target.update(collection.update_one.call_args.args[1]["$set"])
    response = client.get(f"/manager/targets/distribute/{target['_id']}")
    assert response.status_code == 200
    assert b'value="7.01"' in response.data


def test_duplicate_and_foreign_agents_rejected(flow, monkeypatch):
    client, executive, manager, collection, target, aid, bid = flow
    monkeypatch.setattr(manager, "render_template", lambda *args, **kwargs: kwargs.get("error") or "page")
    for second in (aid, ObjectId()):
        assert client.post(f"/manager/targets/distribute/{target['_id']}", data=payload(aid, second)).status_code == 400
    collection.update_one.assert_not_called()


@pytest.mark.parametrize("day,start,end", [("2026-09-07", "2026-09-07", "2026-09-13"), ("2026-09-13", "2026-09-07", "2026-09-13"), ("2027-01-01", "2026-12-28", "2027-01-03")])
def test_weekly_calendar_window(flow, monkeypatch, day, start, end):
    from datetime import date
    client, executive, *_ = flow
    monkeypatch.setattr(executive, "_utc_today_date", lambda: date.fromisoformat(day))
    window = executive._period_window("weekly")
    assert (window["start_str"], window["end_str"]) == (start, end)


def test_weekly_creation_and_manual_agent_progress(flow):
    client, executive, manager, collection, target, aid, bid = flow
    response = client.post("/executive/targets/create", data={"manager_id":str(target["manager_id"]), "period":"weekly", "product_target":"3", "cash_target":"10.01", "customer_target":"1"})
    assert response.status_code == 302
    saved = collection.insert_one.call_args.args[0]
    assert saved["period"] == "weekly" and saved["agent_allocations"] == []
    target.update(saved)
    assert client.post(f"/manager/targets/distribute/{target['_id']}", data=payload(aid,bid)).status_code == 302
    target.update(collection.update_one.call_args.args[1]["$set"])
    executive._aggregate_payments_by_agent = lambda *args: {str(aid): 7.01}
    executive._aggregate_products_by_agent = lambda *args: {}
    executive._aggregate_customers_by_agent = lambda *args: {}
    summary = executive.compute_agent_target_progress(str(aid), target["manager_id"], "weekly")
    assert summary["period"] == "weekly"
    assert summary["percents"]["cash_pct"] == 100
