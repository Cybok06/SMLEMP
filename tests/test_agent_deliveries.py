from datetime import datetime, timedelta
from pathlib import Path

import mongomock
import pytest
from bson import ObjectId
from flask import Flask, session, url_for
from werkzeug.routing import BuildError

from routes import agent_deliveries as routes
from services import agent_deliveries as service

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deliveries_flow(monkeypatch):
    database = mongomock.MongoClient().agent_deliveries_tests
    monkeypatch.setattr(service, 'db', database)
    agent, other_agent, customer, other_customer = [ObjectId() for _ in range(4)]
    database.customers.insert_many([
        {'_id': customer, 'name': 'Ama Customer', 'phone_number': '0241234567', 'agent_id': agent},
        {'_id': other_customer, 'name': 'Other Customer', 'agent_id': str(other_agent)},
    ])

    def identity():
        return {'is_authenticated': bool(session.get('user_id')), 'role': session.get('role'),
                'user_id': session.get('user_id'), 'available_roles': []}

    monkeypatch.setattr(routes, 'get_current_identity', identity)
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.secret_key = 'delivery-tests-only'
    app.register_blueprint(routes.agent_deliveries_bp)
    app.add_url_rule('/login', 'login.login', lambda: 'login')
    app.add_url_rule('/view/customer/<customer_id>', 'view.view_customer_profile', lambda customer_id: 'profile')
    app.add_url_rule('/completed-cards/<customer_id>/<int:product_index>/mark-delivered',
                     'completed_cards.completed_card_mark_delivered', lambda **kwargs: 'delivery', methods=['POST'])
    # The sidebar links to other application blueprints, outside this fixture.
    def template_url(endpoint, **values):
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return '/fixture/' + endpoint
    app.jinja_env.globals['url_for'] = template_url
    app.context_processor(lambda: {'identity': identity()})
    client = app.test_client()

    def sign_in(role='agent', user_id=None):
        with client.session_transaction() as state:
            state.clear()
            state.update(role=role, user_id=str(user_id or agent))

    def submit(index=0, status='packaging', owner=None, days_ago=0, **changes):
        package = {'_id': ObjectId(), 'customer_id': owner or customer, 'product_index': index,
                   'agent_id': str(agent), 'status': status, 'product': {'name': f'Product {index}'},
                   'created_at': datetime.utcnow() - timedelta(days=days_ago), **changes}
        database.packages.insert_one(package)
        return package

    sign_in()
    return client, database, sign_in, submit, agent, other_agent, customer, other_customer


def test_queue_and_badge_include_all_dates_and_exclude_delivered_cancelled_and_other_agents(deliveries_flow):
    client, database, _, submit, agent, _, _, other_customer = deliveries_flow
    submit(0, 'pending', days_ago=100)
    submit(1, 'packaging')
    submit(2, 'delivering')
    submit(3, 'delivered')
    submit(4, 'cancelled')
    submit(5, 'closed')
    submit(0, 'packaging', owner=other_customer)
    data = service.delivery_page(agent)
    assert data['undelivered_count'] == 3 and data['total'] == 3
    assert {row['product_index'] for row in data['rows']} == {0, 1, 2}
    assert client.get('/api/agent/deliveries/count').json == {'ok': True, 'count': 3}
    html = client.get('/agent/deliveries').get_data(as_text=True)
    assert 'Ama Customer' in html and 'Other Customer' not in html
    assert '3 undelivered products' in html
    assert html.count('data-confirm-delivery=') == 2
    assert '/completed-cards/' in html and 'js/delivery_camera.js' in html


def test_status_filters_do_not_change_total_undelivered_badge(deliveries_flow):
    _, _, _, submit, agent, *_ = deliveries_flow
    submit(0, 'packaging')
    submit(1, 'delivered')
    data = service.delivery_page(agent, status='delivered')
    assert data['total'] == 1 and data['rows'][0]['status'] == 'delivered'
    assert not data['rows'][0]['can_deliver'] and data['undelivered_count'] == 1
    assert service.delivery_page(agent, status='all')['total'] == 2


def test_badge_drops_after_delivery_status_is_saved(deliveries_flow):
    client, database, _, submit, *_ = deliveries_flow
    package = submit()
    assert client.get('/api/agent/deliveries/count').json['count'] == 1
    database.packages.update_one({'_id': package['_id']}, {'$set': {'status': 'delivered'}})
    assert client.get('/api/agent/deliveries/count').json['count'] == 0
    assert 'You have no undelivered submitted products.' in client.get('/agent/deliveries').get_data(as_text=True)


def test_transfer_uses_current_customer_owner_not_old_package_agent(deliveries_flow):
    client, database, sign_in, submit, agent, other_agent, customer, _ = deliveries_flow
    submit()
    database.customers.update_one({'_id': customer}, {'$set': {'agent_id': str(other_agent)}})
    assert service.undelivered_count(agent) == 0
    sign_in(user_id=other_agent)
    assert client.get('/api/agent/deliveries/count').json['count'] == 1
    assert 'Ama Customer' in client.get('/agent/deliveries').get_data(as_text=True)


def test_duplicate_submission_is_one_undelivered_product(deliveries_flow):
    _, _, _, submit, agent, *_ = deliveries_flow
    submit(days_ago=10)
    submit(status='delivered')
    assert service.undelivered_count(agent) == 0
    assert service.delivery_page(agent, status='all')['total'] == 1


def test_legacy_packaged_status_and_string_agent_id(deliveries_flow):
    _, database, _, submit, agent, _, customer, _ = deliveries_flow
    database.customers.update_one({'_id': customer}, {'$set': {'agent_id': str(agent)}})
    submit(status='packaged')
    row = service.delivery_page(agent)['rows'][0]
    assert row['status'] == 'packaging' and row['can_deliver']


def test_pagination_and_invalid_page_and_status(deliveries_flow):
    client, _, _, submit, agent, *_ = deliveries_flow
    for index in range(32):
        submit(index)
    data = service.delivery_page(agent, page=2)
    assert len(data['rows']) == 2 and data['total_pages'] == 2
    assert client.get('/agent/deliveries?page=999').status_code == 302
    assert client.get('/agent/deliveries?page=bad&status=invalid').status_code == 200
    assert client.get('/api/agent/deliveries/count?agent_id=someone-else').json['count'] == 32


@pytest.mark.parametrize('role', ['manager', 'executive', 'inventory'])
def test_non_agents_cannot_access_page_or_count(deliveries_flow, role):
    client, _, sign_in, *_ = deliveries_flow
    sign_in(role)
    assert client.get('/agent/deliveries').status_code == 403
    assert client.get('/api/agent/deliveries/count').status_code == 403


def test_sign_in_required(deliveries_flow):
    client, *_ = deliveries_flow
    with client.session_transaction() as state:
        state.clear()
    assert client.get('/agent/deliveries').status_code == 302
    assert client.get('/api/agent/deliveries/count').status_code == 401
