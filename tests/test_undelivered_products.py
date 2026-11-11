import importlib.util
import sys
import types
import io
from datetime import datetime
from pathlib import Path

import mongomock
import pytest
from bson import ObjectId
from flask import Flask, session, url_for
from werkzeug.routing import BuildError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def missing_flow(monkeypatch):
    database = mongomock.MongoClient().missing_products
    fake_db = types.ModuleType('db'); fake_db.db = database
    fake_db.users_collection = database.users
    monkeypatch.setitem(sys.modules, 'db', fake_db)
    import login
    def identity():
        return {'is_authenticated': bool(session.get('user_id')), 'user_id': session.get('user_id'), 'role': session.get('role'), 'available_roles': []}
    monkeypatch.setattr(login, 'get_current_identity', identity)
    spec = importlib.util.spec_from_file_location('_missing_products', ROOT / 'routes/undelivered_items.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.secret_key = 'missing-tests'; app.testing = True
    app.register_blueprint(module.undelivered_items_bp)
    app.add_url_rule('/login', 'login.login', lambda: 'login')
    def template_url(endpoint, **values):
        try:return url_for(endpoint, **values)
        except BuildError:return '/fixture/' + endpoint
    app.jinja_env.globals['url_for'] = template_url
    app.context_processor(lambda: {'identity': identity()})
    manager, other_manager, agent, other_agent, customer, inventory, other_inventory = [ObjectId() for _ in range(7)]
    database.users.insert_many([
        {'_id': manager, 'name': 'Accra Manager', 'role': 'manager', 'branch': 'Accra'},
        {'_id': other_manager, 'name': 'Kumasi Manager', 'role': 'manager', 'branch': 'Kumasi'},
        {'_id': agent, 'name': 'Accra Agent', 'role': 'agent', 'branch': 'Accra', 'manager_id': manager},
        {'_id': other_agent, 'name': 'Kumasi Agent', 'role': 'agent', 'branch': 'Kumasi', 'manager_id': other_manager},
    ])
    database.customers.insert_one({'_id': customer, 'name': 'Ama Customer', 'agent_id': agent,
        'purchases': [{'product': {'name': 'Kitchen package', 'status': 'active'}}]})
    database.inventory.insert_many([
        {'_id': inventory, 'name': 'Rice cooker', 'manager_id': manager},
        {'_id': other_inventory, 'name': 'Foreign product', 'manager_id': other_manager},
        {'name': 'Shared item'},
    ])
    client = app.test_client()
    def signin(role='agent', user_id=agent):
        with client.session_transaction() as state:
            state.clear();state.update(user_id=str(user_id), role=role, **{role + '_id': str(user_id)})
    signin()
    return app, client, database, signin, manager, other_manager, agent, other_agent, customer, inventory, other_inventory


def create_missing(flow, **changes):
    _, client, _, _, _, _, _, _, customer, inventory, _ = flow
    return client.post(f'/customer/{customer}/undelivered/0', json={
        'components': [{'inventory_id': str(inventory), 'name': 'FORGED NAME', 'undelivered_qty': 2}],
        'manual_items': [{'name': 'Serving tray', 'undelivered_qty': 1}], **changes})


def test_agent_saves_inventory_and_manual_items_against_original_card(missing_flow):
    _, client, database, _, manager, _, agent, _, customer, inventory, _ = missing_flow
    result = create_missing(missing_flow)
    assert result.status_code == 201, result.json
    row = database.undelivered_items.find_one({})
    assert row['customer_id'] == customer and row['agent_id'] == str(agent) and row['manager_id'] == manager
    assert row['packaged_product']['name'] == 'Kitchen package'
    assert row['undelivered_items'][0]['name'] == 'Rice cooker'
    assert row['undelivered_items'][1]['source'] == 'manual'
    assert row['status'] == 'pending'
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 1
    assert create_missing(missing_flow).status_code == 409
    assert database.undelivered_items.count_documents({}) == 1
    assert client.get('/undelivered-products').status_code == 200


def test_manager_changes_status_shared_with_agent_and_badge_until_delivered(missing_flow):
    _, client, _, signin, manager, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    signin('manager', manager)
    for version, status in enumerate(['preparing', 'products_in', 'delivered']):
        result = client.post(f'/undelivered_items/{key}/status', json={'status': status, 'version': version})
        assert result.status_code == 200, result.json
        assert client.get('/undelivered_items/count_due').json['pending_total'] == (0 if status == 'delivered' else 1)
        signin()
        row = client.get('/undelivered_items/data?status=all').json['rows'][0]
        assert row['status'] == status and row['version'] == version + 1
        signin('manager', manager)
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'preparing'}).status_code == 409


@pytest.mark.parametrize('role', ['agent', 'manager'])
def test_foreign_roles_cannot_list_update_or_mark_delivered(missing_flow, role):
    _, client, database, signin, _, other_manager, _, other_agent, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    signin(role, other_agent if role == 'agent' else other_manager)
    assert client.get('/undelivered_items/data').json['rows'] == []
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 0
    for route in ('status', 'mark_delivered'):
        assert client.post(f'/undelivered_items/{key}/{route}', json={'status': 'delivered'}).status_code == 404
    assert database.undelivered_items.find_one({})['status'] == 'pending'
    assert create_missing(missing_flow).status_code in (403, 404)


def test_inventory_options_and_save_are_branch_scoped(missing_flow):
    _, client, _, _, _, _, _, _, customer, _, other_inventory = missing_flow
    options = client.get(f'/customer/{customer}/undelivered/0/inventory').json['products']
    assert {row['name'] for row in options} == {'Rice cooker', 'Shared item'}
    assert create_missing(missing_flow, components=[{'inventory_id': str(other_inventory), 'undelivered_qty': 1}]).status_code == 400


@pytest.mark.parametrize('changes', [
    {'components': [], 'manual_items': []}, {'components': 'invalid'},
    {'components': [], 'manual_items': [{'name': '', 'undelivered_qty': 1}]},
    {'components': [], 'manual_items': [{'name': 'Tray', 'undelivered_qty': -1}]},
    {'components': [], 'manual_items': [{'name': 'Tray', 'undelivered_qty': 1.5}]},
    {'expected_delivery_date': 'not-a-date'},
])
def test_invalid_items_cannot_create_empty_or_bad_records(missing_flow, changes):
    assert create_missing(missing_flow, **changes).status_code == 400
    assert missing_flow[2].undelivered_items.count_documents({}) == 0


def test_stale_updates_do_not_overwrite_status_and_history(missing_flow):
    _, client, database, signin, manager, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    signin('manager', manager)
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'preparing', 'version': 0}).status_code == 200
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'delivered', 'version': 0}).status_code == 409
    row = database.undelivered_items.find_one({})
    assert row['status'] == 'preparing' and len(row['history']) == 2


def test_agent_delivery_requires_photo_and_upload_failure_keeps_record_open(missing_flow, monkeypatch):
    _, client, database, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    result = client.post(f'/undelivered_items/{key}/status', json={'status': 'delivered'})
    assert result.status_code == 400
    assert database.undelivered_items.find_one({})['status'] == 'pending'
    from services import delivery_evidence
    from Backend.inventory.stock_deductions_store import StockDeductionError
    def failure(*args):raise StockDeductionError('Upload failed', status=502)
    monkeypatch.setattr(delivery_evidence, 'upload_delivery_photo', failure)
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'delivered'}).status_code == 502
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 1


def test_agent_successful_photo_delivery_preserves_evidence_and_closes_badge(missing_flow, monkeypatch):
    _, client, database, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    from services import delivery_evidence
    def upload(photo, identity):
        assert photo and photo.read() == b'camera-photo'
        return {'source': 'camera', 'url': 'https://example.com/proof.jpg', 'uploaded_by': identity['user_id']}
    monkeypatch.setattr(delivery_evidence, 'upload_delivery_photo', upload)
    result = client.post(f'/undelivered_items/{key}/status', data={
        'status': 'delivered', 'version': '0', 'delivery_photo': (io.BytesIO(b'camera-photo'), 'delivery-camera.jpg')})
    assert result.status_code == 200
    row = database.undelivered_items.find_one({})
    assert row['delivery_evidence']['source'] == 'camera'
    assert row['status'] == 'delivered' and 'open_card_key' not in row
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 0


def test_legacy_records_pagination_and_scope_override(missing_flow):
    _, client, database, signin, manager, other_manager, agent, _, customer, *_ = missing_flow
    database.undelivered_items.insert_many([{'customer_id': customer, 'product_index': i,
        'status': 'preparing', 'agent_id': str(agent), 'manager_id': str(manager),
        'created_at': datetime(2026, 10, 21), 'undelivered_items': [{'name': 'Legacy part', 'undelivered_qty': 1}]}
        for i in range(32)])
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 32
    data = client.get('/undelivered_items/data').json
    assert len(data['rows']) == 30 and data['pages'] == 2
    assert len(client.get('/undelivered_items/data?page=2').json['rows']) == 2
    signin('manager', manager)
    assert client.get('/undelivered_items/data?manager_id=' + str(other_manager)).json['total'] == 32
    key = str(database.undelivered_items.find_one({})['_id'])
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'products_in', 'version': 0}).status_code == 200


def test_unauthenticated_endpoints_do_not_expose_records(missing_flow):
    _, client, _, _, *_ = missing_flow
    create_missing(missing_flow)
    with client.session_transaction() as state:state.clear()
    assert client.get('/undelivered_items/data').status_code == 403
    assert client.get('/undelivered_items/count_due').status_code == 403


def inventory_actor(flow):
    key = ObjectId()
    flow[2].users.insert_one({'_id': key, 'role': 'inventory', 'name': 'Inventory Officer'})
    flow[3]('inventory', key)
    return key


def test_manager_forwards_once_inventory_requires_reason_shared_with_agent(missing_flow):
    _, client, database, signin, manager, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    inventory_actor(missing_flow)
    assert client.get('/undelivered_items/data').json['rows'] == []
    assert client.get('/undelivered_items/count_due').json['pending_total'] == 0
    assert client.post(f'/undelivered_items/{key}/inventory-update', json={'reason': 'No stock', 'version': 0}).status_code == 404
    signin('manager', manager)
    url = f'/undelivered_items/{key}/send-to-inventory'
    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 200
    row = database.undelivered_items.find_one({})
    assert row['status'] == 'pending' and row['version'] == 1
    assert len([entry for entry in row['history'] if entry.get('event') == 'sent_to_inventory']) == 1
    officer = inventory_actor(missing_flow)
    assert client.get('/undelivered_items/data').json['total'] == 1
    url = f'/undelivered_items/{key}/inventory-update'
    for reason in ['', '   ', 'x' * 2001, None, {'bad': True}]:
        assert client.post(url, json={'reason': reason, 'status': 'preparing', 'version': 1}).status_code == 400
    assert database.undelivered_items.find_one({})['status'] == 'pending'
    assert client.post(url, json={'reason': 'Awaiting supplier stock', 'status': 'preparing', 'version': 1}).status_code == 200
    row = database.undelivered_items.find_one({})
    assert row['inventory_updated_by'] == str(officer)
    assert row['history'][-1]['reason'] == 'Awaiting supplier stock'
    assert client.post(url, json={'reason': 'Stale overwrite', 'version': 1}).status_code == 409
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'products_in'}).status_code == 403
    assert client.post(url, json={'reason': 'Stock ready', 'status': 'delivered', 'version': 2}).status_code == 400
    for role, actor in [('manager', manager), ('agent', missing_flow[6])]:
        signin(role, actor)
        result = client.get('/undelivered_items/data').json['rows'][0]
        assert result['inventory_reason'] == 'Awaiting supplier stock'
        assert result['status'] == 'preparing'
        assert client.post(url, json={'reason': 'Forged', 'version': 2}).status_code == 403
    signin('manager', manager)
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'delivered', 'version': 2}).status_code == 200
    inventory_actor(missing_flow)
    assert client.post(url, json={'reason': 'Reopen', 'status': 'pending', 'version': 3}).status_code == 409


def test_only_own_manager_can_forward_open_record(missing_flow):
    _, client, _, signin, manager, other_manager, *_ = missing_flow
    key = create_missing(missing_flow).json['id']
    url = f'/undelivered_items/{key}/send-to-inventory'
    assert client.post(url).status_code == 403
    signin('manager', other_manager)
    assert client.post(url).status_code == 404
    signin('manager', manager)
    assert client.post(f'/undelivered_items/{key}/status', json={'status': 'delivered'}).status_code == 200
    assert client.post(url).status_code == 409


def test_customer_product_card_displays_escaped_inventory_reason(missing_flow):
    from flask import render_template
    app = missing_flow[0]
    with app.test_request_context('/'):
        html = render_template('partials/customer_tabs/products.html', customer_id=str(missing_flow[8]), purchases=[{
            'product': {'name': 'Kitchen package', 'status': 'active'}, 'amount_left': 50,
            'progress': 0, 'components_catalog': [], 'inventory_reason': 'Awaiting stock <script>bad()</script>'}])
    assert 'Inventory reason:' in html and 'Awaiting stock &lt;script&gt;' in html
    assert '<script>bad()</script>' not in html


def test_existing_inventory_roles_receive_new_page_without_database_migration(missing_flow):
    spec = importlib.util.spec_from_file_location('_missing_inventory_settings', ROOT / 'Backend/inventory/settings_store.py')
    settings = importlib.util.module_from_spec(spec); spec.loader.exec_module(settings)
    permissions = settings.normalize_permissions({'inventory': {'view': True, 'visible': True}})
    assert permissions['undelivered-items']['view'] and permissions['undelivered-items']['edit']
    assert settings.normalize_permissions({'undelivered-items': {'view': False}})['undelivered-items']['view'] is False
    assert all(role['permissions']['undelivered-items']['view'] for role in settings.default_role_docs())
