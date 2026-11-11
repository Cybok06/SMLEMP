import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path

import mongomock
import pytest
from bson import ObjectId
from flask import Flask, redirect, session

from services.meeting_report_metrics import customer_breakdowns


@pytest.fixture
def report_app(monkeypatch):
    database = mongomock.MongoClient().meeting_report
    fake_db = types.ModuleType('db'); fake_db.db = database
    monkeypatch.setitem(sys.modules, 'db', fake_db)
    path = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('_meeting_report_test', path / 'routes/meeting_report.py')
    report = importlib.util.module_from_spec(spec); spec.loader.exec_module(report)
    # Sales-of-goods valuation uses Mongo conversion operators not provided by
    # mongomock. Keep that independent existing calculation outside these tests.
    monkeypatch.setattr(report, '_build_products_sold', lambda *args, **kwargs: {})
    original_aggregate = database.customers.aggregate
    def aggregate(pipeline, *args, **kwargs):
        if any(stage.get('$unwind') == '$purchases' for stage in pipeline):
            return []
        return original_aggregate(pipeline, *args, **kwargs)
    monkeypatch.setattr(database.customers, 'aggregate', aggregate)
    app = Flask(__name__, template_folder=str(path / 'templates'), static_folder=str(path / 'static'))
    app.secret_key = 'meeting-report-tests'; app.testing = True
    app.register_blueprint(report.meeting_report_bp)
    app.url_build_error_handlers.append(lambda error, endpoint, values: '/_unused/' + endpoint)
    manager, other_manager, agent, other_agent, empty_manager = [ObjectId() for _ in range(5)]
    database.users.insert_many([
        {'_id': manager, 'role': 'manager', 'name': 'Accra Manager', 'branch': 'Accra'},
        {'_id': other_manager, 'role': 'manager', 'name': 'Tema Manager', 'branch': 'Tema'},
        {'_id': empty_manager, 'role': 'manager', 'name': 'Empty Manager', 'branch': 'Cape Coast'},
        {'_id': agent, 'role': 'agent', 'name': 'Agent One', 'manager_id': manager, 'branch': 'Accra'},
        {'_id': other_agent, 'role': 'agent', 'name': 'Agent Two', 'manager_id': other_manager, 'branch': 'Tema'},
    ])
    @app.get('/test/signin/<role>')
    def browser_signin(role):
        session.clear(); session[role + '_id'] = str(manager if role == 'manager' else ObjectId())
        session['role'] = role
        return redirect('/meeting-report/')
    client = app.test_client()
    def signin(role='manager', user_id=manager):
        with client.session_transaction() as state:
            state.clear(); state[role + '_id'] = str(user_id); state['role'] = role
    signin()
    return app, client, database, signin, manager, agent, other_agent, empty_manager


def seed_portfolio(database, agent):
    owing, paid, susu, closed, no_cards = [ObjectId() for _ in range(5)]
    database.customers.insert_many([
        {'_id': owing, 'agent_id': str(agent), 'name': 'Still paying', 'purchases': [{'product': {'name': 'Cooker', 'total': 100}}]},
        {'_id': paid, 'agent_id': str(agent), 'name': 'Fully paid', 'purchases': [{'product': {'name': 'TV', 'total': 100}}]},
        {'_id': susu, 'agent_id': str(agent), 'name': 'SUSU saver', 'purchases': []},
        {'_id': closed, 'agent_id': str(agent), 'name': 'Closed card', 'purchases': [{'product': {'total': 100, 'status': 'closed'}}]},
        {'_id': no_cards, 'agent_id': str(agent), 'name': 'No active card', 'purchases': []},
    ])
    database.payments.insert_many([
        {'agent_id': str(agent), 'customer_id': owing, 'product_index': 0, 'payment_type': 'PRODUCT', 'amount': 30.0, 'date': '2026-10-02'},
        {'agent_id': str(agent), 'customer_id': paid, 'product_index': '0', 'payment_type': 'PRODUCT', 'amount': 100.0, 'date': '2026-09-01'},
        {'agent_id': str(agent), 'customer_id': str(susu), 'payment_type': 'SUSU', 'amount': 20.0, 'date': '2026-10-03'},
        {'agent_id': str(agent), 'customer_id': str(susu), 'payment_type': 'WITHDRAWAL', 'amount': 20.0, 'date': '2026-10-04'},
    ])
    return owing, paid, susu, closed, no_cards


def test_status_uses_all_time_card_balance_and_susu_not_period_attendance(report_app):
    _, _, database, _, _, agent, *_ = report_app
    seed_portfolio(database, agent)
    assert customer_breakdowns(database.customers, database.payments, {'agent_id': str(agent)})[str(agent)] == {'active': 2, 'completed': 3}


def test_mixed_cards_reversals_transfers_and_susu_never_offset_product_balance(report_app):
    _, _, database, _, _, agent, *_ = report_app
    customer = ObjectId()
    database.customers.insert_one({'_id': customer, 'agent_id': str(agent), 'purchases': [
        {'product': {'total': 100, 'status': 'closed'}},
        {'product': {'price': '50', 'quantity': 2}},
    ]})
    database.payments.insert_many([
        {'customer_id': str(customer), 'product_index': 1, 'payment_type': 'PRODUCT', 'amount': 100},
        {'customer_id': customer, 'product_index': '1', 'payment_type': 'WITHDRAWAL', 'amount': 10},
    ])
    assert customer_breakdowns(database.customers, database.payments, {})[str(agent)]['active'] == 1
    database.customers.update_one({'_id': customer}, {'$set': {'purchases.1.product.transfer_status': 'transferred_out'}})
    assert customer_breakdowns(database.customers, database.payments, {})[str(agent)]['completed'] == 1
    database.payments.insert_one({'customer_id': customer, 'payment_type': 'SUSU', 'product_index': None, 'amount': 5})
    assert customer_breakdowns(database.customers, database.payments, {})[str(agent)]['active'] == 1


@pytest.mark.parametrize('role', ['manager', 'admin', 'executive'])
def test_agent_report_sales_split_and_status_partition_for_every_allowed_role(report_app, role):
    _, client, database, signin, manager, agent, *_ = report_app
    seed_portfolio(database, agent)
    signin(role, manager)
    result = client.get('/meeting-report/agent-metrics', query_string={'agent_id': str(agent), 'start': '2026-10-01', 'end': '2026-10-31'})
    assert result.status_code == 200, result.json
    summary = result.json['summary']
    assert summary['total_sales'] == 50.0
    assert summary['sales_breakdown'] == {'susu': 20.0, 'products': 30.0}
    assert summary['payments_count'] == 2
    assert summary['total_customers'] == 5 and summary['active_customers'] == 2 and summary['attendance_rate'] == 40.0
    assert summary['customer_breakdown'] == {'active': 2, 'completed': 3}


def test_team_and_manager_scope_match_agent_breakdowns_and_no_agent_is_zero(report_app):
    _, client, database, signin, manager, agent, _, empty_manager = report_app
    seed_portfolio(database, agent)
    params = {'manager_id': str(manager), 'start': '2026-10-01', 'end': '2026-10-31'}
    team = client.get('/meeting-report/team-metrics', query_string=params)
    assert team.status_code == 200
    row = team.json['agents'][0]
    summary = client.get('/meeting-report/agent-metrics', query_string={**params, 'scope': 'manager'}).json['summary']
    assert row['sales_breakdown'] == summary['sales_breakdown'] == {'susu': 20.0, 'products': 30.0}
    assert row['customer_breakdown'] == summary['customer_breakdown'] == {'active': 2, 'completed': 3}
    signin('executive')
    summary = client.get('/meeting-report/agent-metrics', query_string={**params, 'manager_id': str(empty_manager), 'scope': 'manager'}).json['summary']
    assert summary['sales_breakdown'] == {'susu': 0.0, 'products': 0.0}
    assert summary['customer_breakdown'] == {'active': 0, 'completed': 0}


def test_date_changes_only_affect_collections_not_current_portfolio(report_app):
    _, client, database, _, _, agent, *_ = report_app
    seed_portfolio(database, agent)
    summary = client.get('/meeting-report/agent-metrics', query_string={'agent_id': str(agent), 'start': '2026-09-01', 'end': '2026-09-30'}).json['summary']
    assert summary['sales_breakdown'] == {'susu': 0, 'products': 100}
    assert summary['active_customers'] == 1 and summary['customer_breakdown'] == {'active': 2, 'completed': 3}


def test_manager_cannot_select_other_agents_or_override_team_scope(report_app):
    _, client, _, _, manager, _, other_agent, *_ = report_app
    assert client.get('/meeting-report/agent-metrics?agent_id=' + str(other_agent)).status_code == 403
    result = client.get('/meeting-report/team-metrics?manager_id=' + str(ObjectId()))
    assert {row['id'] for row in result.json['agents']} == {str(report_app[5])}


def test_legacy_payment_types_and_comparison_reconcile_to_total(report_app):
    _, client, database, _, _, agent, *_ = report_app
    owing, *_ = seed_portfolio(database, agent)
    database.payments.insert_many([
        {'agent_id': str(agent), 'customer_id': owing, 'amount': 5.0, 'date': '2026-10-05'},
        {'agent_id': str(agent), 'customer_id': owing, 'payment_type': 'SUS', 'amount': 7.0, 'date': '2026-10-05'},
        {'agent_id': str(agent), 'customer_id': owing, 'is_susu': True, 'amount': 3.0, 'date': '2026-10-05'},
    ])
    summary = client.get('/meeting-report/agent-metrics', query_string={'agent_id': str(agent), 'start': '2026-10-01', 'end': '2026-10-31',
        'compare_start': '2026-09-01', 'compare_end': '2026-09-30'}).json
    assert summary['summary']['sales_breakdown'] == {'susu': 30.0, 'products': 35.0}
    assert sum(summary['summary']['sales_breakdown'].values()) == summary['summary']['total_sales']
    assert summary['compare']['sales_breakdown'] == {'susu': 0.0, 'products': 100.0}


def test_multiple_cards_default_susu_and_unknown_legacy_prices(report_app):
    _, _, database, _, _, agent, *_ = report_app
    customers = [ObjectId() for _ in range(4)]
    database.customers.insert_many([
        {'_id': customers[0], 'agent_id': str(agent), 'purchases': [{'product': {'total': 100}}, {'product': {'total': 200}}]},
        {'_id': customers[1], 'agent_id': str(agent), 'purchases': [], 'susu_default_rate': 5},
        {'_id': customers[2], 'agent_id': str(agent), 'purchases': [{'product': {'name': 'Legacy open card'}}]},
        {'_id': customers[3], 'agent_id': str(agent), 'purchases': [{'product': {'total': 0}}]},
    ])
    database.payments.insert_many([
        {'customer_id': customers[0], 'product_index': 0, 'payment_type': 'PRODUCT', 'amount': 100},
        {'customer_id': customers[0], 'product_index': 1, 'payment_type': 'PRODUCT', 'amount': 50},
    ])
    assert customer_breakdowns(database.customers, database.payments, {})[str(agent)] == {'active': 3, 'completed': 1}


def seed_operations(database, manager, agent, other_agent):
    stamp = datetime(2026, 10, 21, 12)
    database.customer_support_calls.insert_many([
        {'officer_id': str(manager), 'type': 'Outbound', 'started_at': stamp, 'enrichment_status': 'complete'},
        {'officer_id': manager, 'type': 'Inbound', 'created_at': stamp},
        {'officer_id': str(manager), 'type': 'Missed', 'started_at': stamp},
        {'officer_id': str(manager), 'type': 'Outbound', 'started_at': stamp, 'enrichment_status': 'needs_update'},
        {'officer_id': str(other_agent), 'type': 'Outbound', 'started_at': stamp},
        {'officer_id': str(manager), 'type': 'Outbound', 'started_at': datetime(2026, 9, 1)},
    ])
    database.customer_support_field_visits.insert_many([
        {'officer_id': manager, 'started_at': stamp}, {'officer_id': str(other_agent), 'started_at': stamp},
        {'officer_id': str(manager), 'started_at': datetime(2026, 9, 1)},
    ])
    database.customer_support_manual_activities.insert_many([
        {'created_by': str(manager), 'category': 'Interaction', 'occurred_at': stamp},
        {'created_by': manager, 'category': 'Interaction', 'occurred_at': datetime(2026, 10, 31, 23, 59)},
        {'created_by': str(manager), 'category': 'Note', 'occurred_at': stamp},
        {'created_by': str(other_agent), 'category': 'Interaction', 'occurred_at': stamp},
    ])
    customer = database.customers.insert_one({'agent_id': str(agent), 'purchases': []}).inserted_id
    foreign_customer = database.customers.insert_one({'agent_id': str(other_agent), 'purchases': []}).inserted_id
    database.packages.insert_many([
        {'customer_id': customer, 'product_index': 0, 'status': 'delivered', 'created_at': datetime(2026, 9, 1), 'delivered_at': stamp},
        {'customer_id': str(customer), 'product_index': '0', 'status': 'delivered', 'created_at': datetime(2026, 9, 2), 'delivered_at': stamp},
        {'customer_id': customer, 'product_index': 1, 'status': 'delivered', 'delivered_at': datetime(2026, 9, 1)},
        {'customer_id': customer, 'product_index': 2, 'status': 'delivering', 'delivered_at': stamp},
        {'customer_id': customer, 'product_index': 3, 'status': 'delivered', 'delivered_at': '2026-10-31T23:59:59Z'},
        {'customer_id': customer, 'product_index': 4, 'status': 'delivered'},
        {'customer_id': foreign_customer, 'product_index': 0, 'status': 'delivered', 'delivered_at': stamp},
    ])


@pytest.mark.parametrize('role', ['manager', 'admin', 'executive'])
def test_manager_personal_support_work_and_team_deliveries_with_date_filter(report_app, role):
    _, client, database, signin, manager, agent, other_agent, _ = report_app
    seed_operations(database, manager, agent, other_agent)
    signin(role, manager)
    params = {'scope': 'manager', 'manager_id': str(manager), 'start': '2026-10-01', 'end': '2026-10-31'}
    summary = client.get('/meeting-report/agent-metrics', query_string=params).json['summary']
    assert summary['support_activity'] == {'calls': 2, 'field_visits': 1, 'interactions': 2}
    assert summary['deliveries_done'] == 2
    summary = client.get('/meeting-report/agent-metrics', query_string={**params, 'start': '2026-09-01', 'end': '2026-09-30'}).json['summary']
    assert summary['support_activity'] == {'calls': 1, 'field_visits': 1, 'interactions': 0}
    assert summary['deliveries_done'] == 1


def test_agent_and_team_delivery_counts_match_and_manager_metrics_stay_personal(report_app):
    _, client, database, _, manager, agent, other_agent, _ = report_app
    seed_operations(database, manager, agent, other_agent)
    params = {'agent_id': str(agent), 'manager_id': str(manager), 'start': '2026-10-01', 'end': '2026-10-31'}
    summary = client.get('/meeting-report/agent-metrics', query_string=params).json['summary']
    assert summary['deliveries_done'] == 2 and summary['support_activity'] is None
    team = client.get('/meeting-report/team-metrics', query_string=params).json['agents']
    assert team[0]['deliveries_done'] == 2


def test_manager_without_agents_still_has_personal_support_activity(report_app):
    _, client, database, signin, _, _, _, manager = report_app
    database.customer_support_calls.insert_one({'officer_id': str(manager), 'started_at': datetime(2026, 10, 15), 'type': 'Outbound'})
    signin('manager', manager)
    response = client.get('/meeting-report/agent-metrics?scope=manager&start=2026-10-01&end=2026-10-31').json
    assert response['agent']['scope'] == 'manager'
    assert response['summary']['support_activity']['calls'] == 1
    assert response['summary']['deliveries_done'] == 0
