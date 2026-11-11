import ast
import logging
import sys
import importlib.util
from types import ModuleType
from datetime import datetime, timedelta
from pathlib import Path

import mongomock
import pytest
from flask import Flask

from test_customer_support_targets import flow
from services.login_enrichment import enrich_pending
from services.performance import install

ROOT = Path(__file__).resolve().parents[1]


def test_login_enrichment_is_durable_idempotent_and_retries():
    logs = mongomock.MongoClient().test.login_logs
    now = datetime.utcnow()
    ident = logs.insert_one({'ip': '8.8.8.8', 'ip_enrichment': {'state': 'pending', 'attempts': 0, 'next_attempt': now}}).inserted_id
    def failure(ip):
        raise TimeoutError('sensitive information must not be saved')
    assert enrich_pending(logs, lookup=failure)['retry'] == 1
    assert logs.find_one()['ip_enrichment']['error_type'] == 'TimeoutError'
    assert enrich_pending(logs, lookup=failure)['processed'] == 0
    logs.update_one({'_id': ident}, {'$set': {'ip_enrichment.next_attempt': now}})
    assert enrich_pending(logs, lookup=lambda ip: {'country': 'Ghana'})['completed'] == 1
    assert logs.find_one()['ip_location'] == {'country': 'Ghana'}
    assert enrich_pending(logs, lookup=failure)['processed'] == 0


def test_login_enrichment_recovers_expired_lease_and_reports_terminal_failure():
    logs = mongomock.MongoClient().test.login_logs
    logs.insert_one({'ip': '', 'ip_enrichment': {'state': 'running', 'attempts': 4, 'lease': 'old', 'next_attempt': datetime.utcnow() - timedelta(minutes=6)}})
    assert enrich_pending(logs, lookup=lambda ip: 1 / 0)['failed'] == 1
    assert logs.find_one()['ip_enrichment']['state'] == 'failed'


def test_request_timings_do_not_log_request_secrets(monkeypatch, caplog):
    monkeypatch.setenv('PERFORMANCE_SAMPLE_RATE', '1')
    app = Flask(__name__)
    install(app)
    app.add_url_rule('/items/<secret>', 'item', lambda secret: 'ok')
    with caplog.at_level(logging.INFO, logger='smartliving.performance'):
        assert app.test_client().get('/items/private-secret?password=secret').status_code == 200
    assert 'endpoint=item' in caplog.text
    assert 'private-secret' not in caplog.text and 'password' not in caplog.text


def test_notifications_construct_once_and_keep_global_unread_counts(flow, monkeypatch):
    client = flow[0]
    module = sys.modules['customer_support_backend.tickets_calls_api']
    calls = []
    def events():
        calls.append(1)
        return [{'id': 'a', 'type': 'task', 'createdAt': datetime.utcnow()}, {'id': 'b', 'type': 'ticket', 'createdAt': datetime.utcnow()}]
    monkeypatch.setattr(module, '_notification_events', events)
    assert client.patch('/api/customer-support/notifications', json={'id': 'a'}).status_code == 200
    response = client.get('/api/customer-support/notifications?type=ticket&per_page=1')
    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json['unreadCount'] == 1
    assert response.json['counts']['task'] == 1
    assert response.json['notifications'][0]['id'] == 'b'


def test_counts_endpoint_does_not_hydrate_or_reconcile(flow, monkeypatch):
    from services import support_task_queries
    monkeypatch.setattr(support_task_queries, 'task_counts', lambda collection: {'Pending': 1201})
    module = sys.modules['customer_support_backend.tickets_calls_api']
    monkeypatch.setattr(module, '_hydrate_tasks', lambda rows: pytest.fail('counts must not hydrate tasks'))
    client, _, _, login, *_ = flow
    response = client.get('/api/customer-support/tasks/counts')
    assert response.json == {'ok': True, 'counts': {'Pending': 1201}}
    assert len(response.data) < 100
    login('manager')
    assert client.get('/api/customer-support/tasks/counts').status_code == 403


def test_budget_shared_scan_retains_legacy_arithmetic():
    tree = ast.parse((ROOT / 'accounting_routes/budget.py').read_text(encoding='utf-8'))
    names = {'_sales_snapshot', '_sales_actual_period', '_monthly_sales_series'}
    namespace = {'datetime': datetime, 'Dict': dict}
    rows = [{'amount': '0.1', 'date': datetime(2026, 1, 1)}, {'amount': '0.2', 'date': datetime(2026, 1, 2)}, {'amount': 'bad', 'date': datetime(2026, 2, 1)}, {'amount': '9', 'date': None}]
    class Payments:
        calls = 0
        def find(self, query, projection):
            assert query == {'payment_type': {'$ne': 'WITHDRAWAL'}}
            self.calls += 1
            return iter(rows)
    payments = Payments()
    namespace.update(payments_col=payments, _get_doc_date=lambda doc, keys: doc['date'], _safe_float=lambda value: float(value) if value != 'bad' else 0)
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[]), '<budget>', 'exec'), namespace)
    with Flask(__name__).test_request_context('/'):
        start, end = datetime(2026, 1, 1), datetime(2026, 2, 28)
        assert namespace['_sales_actual_period'](start, end) == 0.3
        assert namespace['_monthly_sales_series'](start, end) == {'2026-01': 0.3, '2026-02': 0}
        assert payments.calls == 1


def test_repair_is_bounded_and_invalid_rows_do_not_starve_valid_events():
    from services.delivery_followups import reconcile_delivery_followups
    db = mongomock.MongoClient().test
    db.packages.insert_one({'status': 'delivered'})
    db.packages.insert_one({'status': 'delivered', 'customer_id': 'customer', 'product_index': 0})
    assert reconcile_delivery_followups(db, 1) == {'processed': 1, 'repaired': 0, 'retry': 1}
    assert reconcile_delivery_followups(db, 1) == {'processed': 1, 'repaired': 1, 'retry': 0}
    assert reconcile_delivery_followups(db, 1)['processed'] == 0
    assert db.tasks.count_documents({}) == 1


def test_inventory_reads_preserve_defaults_without_writes(monkeypatch):
    database = mongomock.MongoClient().test
    fake = ModuleType('db'); fake.db = database
    monkeypatch.setitem(sys.modules, 'db', fake)
    spec = importlib.util.spec_from_file_location('inventory_settings_fixture', ROOT / 'Backend/inventory/settings_store.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    rows = module.get_inventory_roles()
    assert {row['id'] for row in rows} == set(module.MANAGED_ROLE_IDS)
    assert database.inventory_roles.count_documents({}) == 0
    module.ensure_default_inventory_roles()
    assert module.get_inventory_roles() == rows


def test_chat_pages_ties_history_and_acknowledges_only_returned_messages(monkeypatch):
    from bson import ObjectId
    db = mongomock.MongoClient().test
    fake = ModuleType('db'); fake.db = db
    monkeypatch.setitem(sys.modules, 'db', fake)
    spec = importlib.util.spec_from_file_location('chat_fixture', ROOT / 'chat.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = Flask(__name__); app.secret_key = 'fixture'; app.register_blueprint(module.chat_bp)
    me, other = str(ObjectId()), str(ObjectId())
    db.users.insert_one({'_id': ObjectId(me), 'role': 'agent'})
    ids = db.chat_messages.insert_many([{'sender_id': other, 'receiver_id': me, 'content': str(i), 'timestamp': datetime(2026, 1, 1), 'is_read': False} for i in range(65)]).inserted_ids
    client = app.test_client()
    with client.session_transaction() as state: state['agent_id'] = me
    first = client.get(f'/get_messages/{other}?paged=1').json
    assert len(first['messages']) == 50 and first['hasMore']
    assert db.chat_messages.count_documents({'is_read': False}) == 15
    older = client.get(f'/get_messages/{other}?paged=1&before={first["messages"][0]["id"]}').json
    assert len(older['messages']) == 15 and not older['hasMore']
    assert [item['id'] for item in older['messages'] + first['messages']] == [str(oid) for oid in ids]
    assert client.get(f'/get_messages/unrelated?paged=1&after={ids[0]}').status_code == 404
    assert client.get(f'/get_messages/{other}?paged=1&after={ids[-1]}').json['messages'] == []


def test_public_assets_never_match_business_files():
    from services.public_assets import is_public_asset
    app = Flask(__name__)
    app.add_url_rule('/customer-support/app/<path:path>', 'operations_management_react_app', lambda path: str(is_public_asset()))
    client = app.test_client()
    assert client.get('/customer-support/app/assets/index-Ab123456.js').data == b'True'
    for name in ['index.html', 'assets/../../secret-Ab123456.js', 'uploads/customer-Ab123456.jpg', 'assets/config.json']:
        assert client.get('/customer-support/app/' + name).data == b'False'


def test_index_declaration_cannot_write_to_database():
    from pymongo.synchronous.mongo_client import MongoClient
    from services.index_management import ManagedDatabase, declarations
    # Constructing with connect=False is safe; any actual DB command would fail.
    client = MongoClient('mongodb://127.0.0.1:1', connect=False, serverSelectionTimeoutMS=1)
    db = ManagedDatabase(client, 'fixture')
    assert db.tasks.create_index([('status', 1)], unique=True) == 'status_1'
    assert declarations[('tasks', 'status_1')]['options']['unique'] is True
    assert db.get_collection('tasks').create_index('created_at') == 'created_at_1'
    client.close()
