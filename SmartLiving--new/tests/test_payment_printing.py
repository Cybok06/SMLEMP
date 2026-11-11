"""Receipt integration tests run exclusively against an isolated in-memory database."""
import importlib.util
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import sys
import types
from unittest.mock import patch

import mongomock
import pytest
from bson import ObjectId
from flask import Flask, abort, session, render_template
from flask_login import LoginManager, UserMixin, current_user
from jinja2 import ChoiceLoader, DictLoader


@pytest.fixture
def printer_app():
    database = mongomock.MongoClient().db
    fake_db = types.ModuleType('db'); fake_db.db = database
    login = types.ModuleType('login')
    login.get_current_identity = lambda: {'user_id': current_user.id, 'role': current_user.role}
    def role_required(role):
        def decorate(fn):
            @wraps(fn)
            def guarded(*args, **kwargs):
                if not current_user.is_authenticated or current_user.role != role:
                    abort(403)
                return fn(*args, **kwargs)
            return guarded
        return decorate
    login.role_required = role_required
    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    with patch.dict(sys.modules, {'db': fake_db, 'login': login}):
        access = load('services.printer_access', 'services/printer_access.py')
        printing = load('services.payment_printing', 'services/payment_printing.py')
        analysis = load('services.printer_analysis', 'services/printer_analysis.py')
        payments = load('_printer_payment_test', 'payment.py')
        access_routes = load('_printer_access_test', 'routes/printer_access.py')
        analysis_routes = load('_printer_analysis_test', 'routes/printer_analysis.py')
        ids = {key: ObjectId() for key in ['agent', 'other', 'executive', 'manager']}
        for key, oid in ids.items():
            database.users.insert_one({'_id': oid, 'name': key.title(),
                'role': 'agent' if key == 'other' else key,
                'branch': 'Accra', 'manager_id': ids['manager']})
        customer = ObjectId()
        database.customers.insert_one({'_id': customer, 'name': 'Ama <unsafe>',
            'agent_id': str(ids['agent']), 'phone_number': '0240000000'})
        payments.get_accessible_agent_ids = lambda uid: [str(uid)]
        root = Path(__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
        app.secret_key = 'isolated-printer-test'; app.testing = True
        manager = LoginManager(app)
        class User(UserMixin):
            def __init__(self, doc): self.id = str(doc['_id']); self.role = doc['role']
        @manager.user_loader
        def user_loader(uid):
            doc = database.users.find_one({'_id': ObjectId(uid)})
            return User(doc) if doc else None
        app.jinja_loader = ChoiceLoader([DictLoader({'side_bar.html': '', 'executive_sidebar.html': ''}), app.jinja_loader])
        app.url_build_error_handlers.append(lambda error, endpoint, values: '/_unused/' + endpoint)
        app.register_blueprint(payments.payment_bp, url_prefix="/payment")
        app.register_blueprint(access_routes.printer_access_bp)
        app.register_blueprint(analysis_routes.printer_analysis_bp)
        @app.get('/_signin/<key>')
        def signin(key):
            session.clear(); session['_user_id'] = str(ids[key]); session['_fresh'] = True
            session['payment_print_csrf'] = 'csrf'
            return 'ok'
        @app.get('/_printer')
        def printer_page():
            return ('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">'
                    '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>'
                    + render_template('partials/payment_printer.html', printer_enabled=True, printer_csrf='csrf'))
        client = app.test_client(); client.get('/_signin/agent')
        yield app, client, database, ids, customer, access, printing


def save_payment(env):
    app, client, db, ids, customer, access, _ = env
    access.set_access('branch', 'Accra', True, str(ids['executive']))
    result = client.post('/payment/add_payment', data={'customer_id': str(customer), 'amount': '25.50', 'is_susu': 'yes'},
                         headers={'X-Requested-With': 'XMLHttpRequest'})
    assert result.status_code == 200, result.data
    assert result.json['ok']
    return result.json['receipt']


def action(client, receipt, kind, **data):
    return client.post('/payment/payment-receipts/' + receipt['document_id'] + '/' + kind,
                       json=data, headers={'X-Printer-CSRF': 'csrf'})


def test_printer_analysis_counts_only_required_payments(printer_app):
    _, _, database, ids, _, _, _ = printer_app
    from services.printer_analysis import build_analysis
    base = datetime(2026, 10, 28, 12, 0)
    def payment(minutes_ago, **fields):
        document = {'printer_feature_enabled': True, 'recorded_by_agent_id': str(ids['agent']),
                    'payment_recorded_at': base - timedelta(minutes=minutes_ago),
                    'date': '2026-10-28', 'amount': 25, 'printed': False,
                    'print_status': 'pending', 'receipt_prints': {}}
        document.update(fields)
        database.payments.insert_one(document)
    payment(20, printed=True, printed_at=base-timedelta(minutes=18), print_delay_seconds=120,
            receipt_prints={'short': {'printed': True, 'status': 'printed'},
                            'full': {'printed': True, 'status': 'printed'}})
    payment(30, printed=True, printed_at=base-timedelta(minutes=10), print_delay_seconds=1200,
            printer_branch='Kumasi',
            receipt_prints={'short': {'printed': True, 'status': 'printed'}})
    payment(25)
    payment(5)
    payment(25, print_status='uncertain')
    payment(30, printer_feature_enabled=False)
    payment(24 * 60 + 1)
    result = build_analysis(base.date(), base.date(), now=base.replace(tzinfo=timezone.utc))
    summary = result['summary']
    assert summary == {'required': 5, 'printed_payments': 2, 'coverage_percent': 40.0,
                       'receipts_sent': 3, 'short_sent': 2, 'full_sent': 1,
                       'missed': 1, 'waiting': 1, 'uncertain': 1,
                       'median_interval_seconds': 660.0, 'late_prints': 1}
    assert result['daily'][0]['required'] == 5
    assert result['branches'][0]['branch'] == 'Accra'
    assert build_analysis(base.date(), base.date(), branch='Kumasi', now=base)['summary']['required'] == 1
    assert result['interval_buckets']['1-5 min'] == 1
    assert result['interval_buckets']['15-60 min'] == 1
    assert build_analysis(base.date(), base.date(), agent_id=str(ids['other']), now=base)['summary']['required'] == 0
    assert build_analysis(base.date(), base.date(), status='missed', now=base)['summary']['required'] == 1


def test_printer_analysis_routes_are_executive_only(printer_app):
    _, client, _, _, _, _, _ = printer_app
    assert client.get('/executive/printer-analysis').status_code == 403
    assert client.get('/executive/printer-analysis/data').status_code == 403
    client.get('/_signin/executive')
    assert client.get('/executive/printer-analysis').status_code == 200
    assert client.get('/executive/printer-analysis/data').json['summary']['required'] == 0
    assert client.get('/executive/printer-analysis/data?start=bad').status_code == 400
    assert client.get('/executive/printer-analysis/data?agent=bad').status_code == 400


def test_access_inheritance_overrides_and_revocation(printer_app):
    _, _, db, ids, _, access, _ = printer_app
    assert not access.has_printer_access(ids['agent'])
    access.set_access('branch', 'Accra', True, 'executive')
    assert access.has_printer_access(ids['agent']) and access.has_printer_access(ids['other'])
    access.set_access('agent', str(ids['agent']), False, 'executive')
    assert not access.has_printer_access(ids['agent'])
    access.set_access('agent', str(ids['agent']), None, 'executive')
    assert access.has_printer_access(ids['agent'])
    access.set_access('branch', 'Accra', False, 'executive')
    access.set_access('agent', str(ids['agent']), True, 'executive')
    assert access.has_printer_access(ids['agent']) and not access.has_printer_access(ids['other'])
    db.users.update_one({'_id': ids['other']}, {'$unset': {'branch': ''}})
    assert access.agent_branch(db.users.find_one({'_id': ids['other']})) == 'Accra'
    assert not access.has_printer_access(ids['executive'])


def test_payment_claim_complete_once_and_server_timing(printer_app):
    _, client, db, ids, _, _, _ = printer_app
    receipt = save_payment(printer_app)
    assert receipt['printer_enabled'] is True
    payment = db.payments.find_one({})
    assert payment['printed'] is False and payment['printed_at'] is None
    assert payment['payment_recorded_at'] == payment['created_at']
    assert action(client, receipt, 'skip').status_code == 400
    assert db.payments.find_one({})['print_status'] == 'pending'
    claim = action(client, receipt, 'claim')
    assert claim.status_code == 200
    assert action(client, receipt, 'claim').status_code == 409
    assert action(client, receipt, 'skip').status_code == 400
    token = claim.json['token']
    assert action(client, receipt, 'finish', token='wrong', outcome='printed').status_code == 409
    db.payments.update_one({}, {'$set': {'payment_recorded_at': datetime.utcnow() - timedelta(seconds=10)}})
    done = action(client, receipt, 'finish', token=token, outcome='printed')
    assert done.json['receipt']['printed'] is True
    assert done.json['receipt']['print_delay_seconds'] >= 10
    assert action(client, receipt, 'claim').status_code == 409
    assert action(client, receipt, 'finish', token=token, outcome='printed').json['receipt']['printed_at'] == done.json['receipt']['printed_at']
    assert db.payments.count_documents({}) == 1


def test_unauthorized_csrf_cross_agent_and_uncertain_lock(printer_app):
    _, client, db, ids, _, access, _ = printer_app
    receipt = save_payment(printer_app)
    assert client.post('/payment/payment-receipts/'+receipt['document_id']+'/claim', json={}).status_code == 403
    client.get('/_signin/other')
    assert action(client, receipt, 'claim').status_code == 409
    client.get('/_signin/agent')
    access.set_access('agent', str(ids['agent']), False, 'executive')
    assert action(client, receipt, 'claim').status_code == 403
    access.set_access('agent', str(ids['agent']), True, 'executive')
    claim = action(client, receipt, 'claim').json
    access.set_access('agent', str(ids['agent']), False, 'executive')
    assert action(client, receipt, 'finish', token=claim['token'], outcome='uncertain').status_code == 200
    assert db.payments.find_one({})['printed'] is False
    access.set_access('agent', str(ids['agent']), True, 'executive')
    assert action(client, receipt, 'claim').status_code == 409


def test_executive_page_and_default_legacy_flow(printer_app):
    _, client, db, ids, _, access, _ = printer_app
    assert client.get('/executive/printer-access').status_code == 403
    assert b'paymentPrinterPanel' not in client.get('/payment/add_payment').data
    client.get('/_signin/executive')
    assert client.get('/executive/printer-access').status_code == 200
    with client.session_transaction() as state: csrf = state['printer_access_csrf']
    assert client.post('/executive/printer-access', data={'scope':'branch','target':'Accra','action':'grant','csrf':csrf}).status_code == 302
    client.get('/_signin/agent')
    assert b'paymentPrinterPanel' in client.get('/payment/add_payment').data
    assert access.has_printer_access(ids['agent'])


def test_printer_access_loads_agents_in_small_batches(printer_app):
    _, client, db, ids, _, _, _ = printer_app
    db.users.insert_many([
        {'_id': ObjectId(), 'name': f'Agent {i:03}', 'username': f'agent{i:03}',
         'role': 'agent', 'branch': 'Accra' if i % 2 else '', 'manager_id': ids['manager']}
        for i in range(70)
    ])
    client.get('/_signin/executive')
    with patch.object(db.users, 'find', wraps=db.users.find) as find_users, \
         patch.object(db.users, 'distinct', wraps=db.users.distinct) as distinct_users, \
         patch.object(db.printer_access, 'find', wraps=db.printer_access.find) as find_grants:
        shell = client.get('/executive/printer-access')
        assert shell.status_code == 200
        assert distinct_users.call_count == find_grants.call_count == 0
        assert not any(call.args and call.args[0] == {'role': 'agent'} for call in find_users.call_args_list)
        assert b'Agent 000' not in shell.data
        shell_reads = find_users.call_count

        first = client.get('/executive/printer-access/agents').json
        assert len(first['agents']) == 25 and first['next_cursor']
        assert find_users.call_count - shell_reads <= 3 and find_grants.call_count == 1
        assert distinct_users.call_count == 0
        seen = {a['id'] for a in first['agents']}
        cursor = first['next_cursor']
        while cursor:
            page = client.get('/executive/printer-access/agents?after=' + cursor).json
            assert len(page['agents']) <= 25
            assert seen.isdisjoint({a['id'] for a in page['agents']})
            seen.update(a['id'] for a in page['agents'])
            cursor = page['next_cursor']
        assert len(seen) == 72
        assert distinct_users.call_count == 0
        branches = client.get('/executive/printer-access/branches').json
        assert branches['branches'] == [{'name': 'Accra', 'enabled': False}]
        assert distinct_users.call_count == 1

    searched = client.get('/executive/printer-access/agents?q=Agent%2006').json
    assert {a['name'] for a in searched['agents']} == {f'Agent {i:03}' for i in range(60, 70)}
    filtered = client.get('/executive/printer-access/agents?branch=Accra').json
    assert filtered['agents'] and all(a['branch'] == 'Accra' for a in filtered['agents'])
    assert client.get('/executive/printer-access/agents?after=bad').status_code == 400


def test_branch_filter_respects_agents_own_branch(printer_app):
    _, client, db, ids, _, _, _ = printer_app
    other = db.users.insert_one({'name': 'Other Branch', 'role': 'agent', 'branch': 'Kumasi',
                                 'manager_id': ids['manager']}).inserted_id
    inherited = db.users.insert_one({'name': 'Inherited Branch', 'role': 'agent',
                                     'manager_id': ids['manager']}).inserted_id
    client.get('/_signin/executive')
    rows = client.get('/executive/printer-access/agents?branch=Accra').json['agents']
    included = {row['id'] for row in rows}
    assert str(inherited) in included
    assert str(other) not in included


def test_atomic_concurrent_claims(printer_app):
    from concurrent.futures import ThreadPoolExecutor
    _, _, _, ids, _, _, printing = printer_app
    receipt = save_payment(printer_app)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: printing.claim_print(ObjectId(receipt['document_id']), ids['agent']), range(12)))
    assert sum(result is not None for result in results) == 1


def test_verified_momo_receipt_defaults_and_idempotence(printer_app):
    _, _, db, ids, customer, access, _ = printer_app
    module = sys.modules['_printer_payment_test']
    access.set_access('agent', str(ids['agent']), True, 'executive')
    attempt = {'reference': 'TEST-MOMO', 'customer_id': customer,
               'agent_id': str(ids['agent']), 'recorded_by_agent_id': str(ids['agent']),
               'manager_id': ids['manager'], 'amount': 15, 'amount_pesewas': 1500,
               'date': '2026-10-28', 'payment_type': 'SUSU', 'status': 'pending'}
    db.paystack_payment_attempts.insert_one(attempt)
    verified = {'status': 'success', 'reference': 'TEST-MOMO', 'amount': 1500,
                'currency': 'GHS', 'channel': 'mobile_money', 'id': 123}
    payment, created = module._finalize_momo_attempt('TEST-MOMO', verified)
    assert created and payment['printed'] is False
    assert payment['print_status'] == 'pending' and payment['payment_recorded_at']
    again, created = module._finalize_momo_attempt('TEST-MOMO', verified)
    assert not created and again['_id'] == payment['_id']
    assert db.payments.count_documents({}) == 1


def test_receipt_design_and_encoder_are_copied_from_prototype():
    root = Path(__file__).resolve().parents[1]
    prototype = (root/'printer_test-main/index.html').read_text(encoding='utf-8')
    javascript = (root/'static/js/payment_printer.js').read_text(encoding='utf-8')
    template = (root/'templates/partials/payment_printer.html').read_text(encoding='utf-8')
    css = (root/'static/payment_printer.css').read_text(encoding='utf-8')
    article = prototype[prototype.index('<article id="thermalReceipt"'):prototype.index('</article>')+len('</article>')]
    assert article in template
    styles = prototype[prototype.index('    .receipt-stage {'):prototype.index('    .print-anim-panel {')]
    assert styles in css
    encoder = prototype[prototype.index('      function sanitizePrinterText('):prototype.index('      async function writePrinterBytes(')]
    start = encoder.index('      function renderPaymentProgress(')
    end = encoder.index('      function createProgressCanvas(', start)
    encoder = encoder[:start] + encoder[end:]
    assert encoder in javascript
    assert 'receiptFormat:"short"' in javascript
    assert 'receiptPrintSkip' not in template + javascript


def test_receipt_urls_follow_deployment_mount(printer_app):
    import json
    import re
    _, client, _, ids, _, access, _ = printer_app
    access.set_access('agent', str(ids['agent']), True, 'executive')
    response = client.get('/payment/add_payment', environ_overrides={'SCRIPT_NAME': '/crm'})
    config = json.loads(re.search(r'<script id="paymentPrinterConfig"[^>]*>(.*?)</script>', response.text).group(1))
    assert config['pendingUrl'] == '/crm/payment/payment-receipts/pending'
    assert config['actionUrl'] == '/crm/payment/payment-receipts/__PAYMENT_ID__/__ACTION__'
    assert 'Automatic receipts' not in response.text
    assert 'MP583 connected. Receipts will print automatically.' not in response.text
    assert 'id="paymentPrinterConnect"' in response.text
    assert 'id="pendingPrinterCount"' in response.text


def test_each_format_prints_once_with_independent_times(printer_app):
    _, client, db, _, _, _, _ = printer_app
    receipt = save_payment(printer_app)
    short = action(client, receipt, 'claim', format='short').json
    first = action(client, receipt, 'finish', format='short', token=short['token'], outcome='printed').json['receipt']
    assert action(client, receipt, 'claim', format='short').status_code == 409
    long = action(client, receipt, 'claim', format='full').json
    assert action(client, receipt, 'finish', format='short', token=long['token'], outcome='printed').status_code == 409
    second = action(client, receipt, 'finish', format='full', token=long['token'], outcome='printed').json['receipt']
    assert second['receipt_prints']['short']['printed'] and second['receipt_prints']['full']['printed']
    assert second['printed_at'] == first['printed_at']
    assert second['receipt_prints']['full']['printed_at'] >= second['receipt_prints']['short']['printed_at']
    assert action(client, receipt, 'claim', format='full').status_code == 409
    assert action(client, receipt, 'claim', format='invalid').status_code == 400
    assert 'token' not in str(second['receipt_prints'])
    assert db.payments.find_one({})['payment_id'] == receipt['payment_id']


def test_parallel_format_claims_and_uncertain_second_print(printer_app):
    from concurrent.futures import ThreadPoolExecutor
    _, client, db, ids, _, _, printing = printer_app
    receipt = save_payment(printer_app)
    def claim(name):
        row = printing.claim_print(ObjectId(receipt['document_id']), ids['agent'], name)
        return (name, row)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(claim, ['short', 'full'] * 8))
    successes = {name: row for name, row in rows if row}
    assert sum(row is not None for _, row in rows) == 2
    short_token = successes['short']['receipt_prints']['short']['token']
    long_token = successes['full']['receipt_prints']['full']['token']
    short = action(client, receipt, 'finish', token=short_token, format='short', outcome='printed').json['receipt']
    response = action(client, receipt, 'finish', token=long_token, format='full', outcome='uncertain')
    assert response.json['receipt']['printed'] is True
    assert response.json['receipt']['printed_at'] == short['printed_at']
    assert response.json['receipt']['receipt_prints']['full']['printed'] is False
    assert action(client, receipt, 'claim', format='full').status_code == 409


def test_todays_unprinted_receipts_use_recorded_time_boundaries(printer_app):
    from datetime import timezone
    _, client, db, _, _, _, _ = printer_app
    save_payment(printer_app)
    sample = db.payments.find_one({})
    db.payments.delete_many({})
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    expected = []
    for offset, printed in [(timedelta(milliseconds=-1), False), (timedelta(), False),
                            (timedelta(days=1, milliseconds=-1), False), (timedelta(days=1), False),
                            (timedelta(hours=1), True)]:
        row = {**sample, '_id': ObjectId(), 'payment_recorded_at': start + offset, 'printed': printed}
        db.payments.insert_one(row)
        if timedelta() <= offset < timedelta(days=1) and not printed:
            expected.append(str(row['_id']))
    response = client.get('/payment/payment-receipts/pending')
    assert response.status_code == 200
    assert {r['document_id'] for r in response.json['receipts']} == set(expected)
    assert response.json['count'] == len(expected)
    for index in range(31):
        db.payments.insert_one({**sample, '_id': ObjectId(),
                                'payment_recorded_at': start + timedelta(minutes=index), 'printed': False})
    response = client.get('/payment/payment-receipts/pending')
    assert response.json['count'] == len(expected) + 31
    assert len(response.json['receipts']) == 30


def test_legacy_printed_or_uncertain_receipts_are_not_unlocked(printer_app):
    _, client, db, _, _, _, _ = printer_app
    receipt = save_payment(printer_app)
    db.payments.update_one({}, {'$unset': {'receipt_prints': ''}, '$set': {'printed': True, 'print_status': 'printed'}})
    for name in ('short', 'full'):
        assert action(client, receipt, 'claim', format=name).status_code == 409
    db.payments.update_one({}, {'$set': {'printed': False, 'print_status': 'uncertain'}})
    for name in ('short', 'full'):
        assert action(client, receipt, 'claim', format=name).status_code == 409


def test_profile_payment_tabs_show_receipt_fields_for_all_roles(printer_app):
    from flask import Flask
    _, client, db, _, customer, _, _ = printer_app
    receipt = save_payment(printer_app)
    claim = action(client, receipt, 'claim').json
    action(client, receipt, 'finish', token=claim['token'], outcome='printed')
    row = db.payments.find_one({})
    db.payments.insert_one({**row, '_id': ObjectId(), 'payment_type': 'PRODUCT'})
    fake_db = types.ModuleType('db'); fake_db.db = db
    spec = importlib.util.spec_from_file_location('_receipt_profile_tabs', 'view.py')
    view = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake_db}):
        spec.loader.exec_module(view)
    app = Flask(__name__, template_folder=str(Path('templates').resolve()))
    app.secret_key = 'receipt-tabs'
    app.register_blueprint(view.view_bp)
    tabs = app.test_client()
    for role in ('agent', 'manager', 'executive'):
        with tabs.session_transaction() as state:
            state.clear(); state[role + '_id'] = str(ObjectId())
        for name in ('payments', 'susu'):
            response = tabs.get(f'/customer/{customer}/tab/{name}')
            assert response.status_code == 200, response.text
            assert 'Printed: Yes' in response.text
            assert 'Printed at:' in response.text and 'Interval:' in response.text
            assert receipt['payment_id'] in response.text
            assert 'Short / long receipts' in response.text
