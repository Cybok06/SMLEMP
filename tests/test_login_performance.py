"""Real login code and template; isolated collections and no external calls."""
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import bcrypt
import mongomock
import pytest
from bson import ObjectId
from flask import Flask
from flask_login import LoginManager

ROOT = Path(__file__).resolve().parents[1]


def test_login_uses_smartliving_brand_and_share_image(login_app):
    app, _, _ = login_app
    response = app.test_client().get('/login')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<title>Sign in | SMARTLIVING V2 Emporium Plus</title>' in html
    assert 'content="#2e0848"' in html
    assert 'property="og:image"' in html
    assert 'https://localhost/static/login/smartliving-logo.png' in html
    assert '/static/login/smartliving-logo.png' in html
    assert 'window.__SMARTLIVING_LOGIN__' in html
    assert 'AXELERA' not in html
    asset_names = re.findall(r'/static/login/assets/(index-[^"?]+\.(?:js|css))', html)
    assert len(asset_names) == 2
    assert all((ROOT / 'static/login/assets' / name).is_file() for name in asset_names)


@pytest.fixture
def approval_app(login_app, monkeypatch):
    app, database, login = login_app
    for name in ('register', 'agent', 'routes.pending_agents'):
        spec = importlib.util.spec_from_file_location(name, ROOT / (name.replace('.', '/') + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    app.register_blueprint(sys.modules['register'].register_bp)
    app.register_blueprint(sys.modules['agent'].agent_bp)
    app.register_blueprint(sys.modules['routes.pending_agents'].pending_agents_bp)
    app.add_url_rule('/agent-dashboard', 'dashboard_agent.agent_dashboard', lambda: 'agent dashboard')
    app.url_build_error_handlers.append(lambda error, endpoint, values: '/unused/' + endpoint)
    manager_id = database.users.insert_one({'username': 'manager', 'role': 'manager', 'name': 'Manager One', 'branch': 'Accra'}).inserted_id
    executive_id = database.users.insert_one({'username': 'executive', 'role': 'executive', 'name': 'Executive One'}).inserted_id
    return app, database, manager_id, executive_id


@pytest.mark.parametrize('reviewer_role', ['executive', 'hr', 'admin'])
def test_manager_registration_approval_and_login(approval_app, reviewer_role):
    app, db, manager_id, executive_id = approval_app
    manager = app.test_client()
    with manager.session_transaction() as state:
        state.update(user_id=str(manager_id), manager_id=str(manager_id), role='manager')
    response = manager.get('/register')
    assert response.status_code == 200 and b'Under Review' in response.data
    with manager.session_transaction() as state:
        csrf = state['agent_registration_csrf']
    response = manager.post('/register', data={
        'csrf_token': csrf, 'username': 'new_agent', 'password': 'secret123',
        'name': 'New Agent', 'status': 'Active', 'branch': 'Forged branch',
    })
    assert response.status_code == 302
    agent = db.users.find_one({'username': 'new_agent'})
    assert agent['status'] == 'Under Review' and agent['is_active'] is False
    assert agent['branch'] == 'Accra' and agent['manager_id'] == manager_id
    assert agent['password'] != 'secret123'
    assert b'Under Review' in manager.get('/agents?status=under_review').data
    for route in ('login', 'agent'):
        with app.test_request_context():
            from flask import url_for
            toggle_url = url_for(route + '.toggle_agent_status', agent_id=agent['_id'])
        assert manager.post(toggle_url).status_code == 302
        assert db.users.find_one({'_id': agent['_id']})['status'] == 'Under Review'
    applicant = app.test_client()
    applicant.post('/login', data={'username': 'new_agent', 'password': 'secret123'})
    with applicant.session_transaction() as state:
        assert 'agent_id' not in state
        assert any('under review' in message for _, message in state['_flashes'])
    assert db.login_logs.count_documents({}) == 0
    if reviewer_role != 'executive':
        executive_id = db.users.insert_one({'username': reviewer_role, 'role': reviewer_role, 'name': 'Reviewer'}).inserted_id
    executive = app.test_client()
    with executive.session_transaction() as state:
        state.update(user_id=str(executive_id), role=reviewer_role)
        state[reviewer_role + '_id'] = str(executive_id)
    pending = executive.get('/executive/pending-agents')
    assert pending.status_code == 200 and b'New Agent' in pending.data and b'Manager One' in pending.data
    sidebar_marker = {'executive': b'id="sidebar"', 'hr': b'hr-sidebar', 'admin': b'adminSidebar'}[reviewer_role]
    assert sidebar_marker in pending.data
    with executive.session_transaction() as state:
        token = state['agent_approval_csrf']
    endpoint = f'/executive/pending-agents/{agent["_id"]}/approve'
    assert executive.post(endpoint, data={'csrf_token': token}).status_code == 302
    approved = db.users.find_one({'_id': agent['_id']})
    assert approved['status'] == 'Active' and approved['is_active'] is True
    assert approved['agent_approved_by'] == str(executive_id)
    assert b'No agents are waiting' in executive.get('/executive/pending-agents').data
    assert applicant.post('/login', data={'username': 'new_agent', 'password': 'secret123'}).location.endswith('/agent-dashboard')
    with applicant.session_transaction() as state:
        assert state['agent_id'] == str(agent['_id'])
    assert db.login_logs.count_documents({}) == 1


@pytest.mark.parametrize('reviewer_role', ['executive', 'hr', 'admin'])
def test_pending_approval_permissions_and_csrf(approval_app, reviewer_role):
    app, db, manager_id, executive_id = approval_app
    agent_id = db.users.insert_one({'role': 'agent', 'status': 'Under Review'}).inserted_id
    endpoint = f'/executive/pending-agents/{agent_id}/approve'
    client = app.test_client()
    assert client.get('/executive/pending-agents').status_code == 302
    with client.session_transaction() as state:
        state.update(user_id=str(manager_id), manager_id=str(manager_id), role='manager')
    assert client.get('/executive/pending-agents').status_code == 403
    assert client.post(endpoint).status_code == 403
    if reviewer_role != 'executive':
        executive_id = db.users.insert_one({'username': reviewer_role, 'role': reviewer_role}).inserted_id
    with client.session_transaction() as state:
        state.clear()
        state.update(user_id=str(executive_id), role=reviewer_role)
        state[reviewer_role + '_id'] = str(executive_id)
    assert client.post(endpoint).status_code == 400
    assert db.users.find_one({'_id': agent_id})['status'] == 'Under Review'


@pytest.fixture
def login_app(monkeypatch):
    database = mongomock.MongoClient().test
    fake = ModuleType('db'); fake.db = database; fake.users_collection = database.users
    monkeypatch.setitem(sys.modules, 'db', fake)
    for name in ('user_model', 'services.login_audit', 'login'):
        spec = importlib.util.spec_from_file_location(name, ROOT / (name.replace('.', '/') + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module); spec.loader.exec_module(module)
    module = sys.modules['login']
    app = Flask(__name__, template_folder=str(ROOT / 'templates')); app.secret_key = 'fixture'
    manager = LoginManager(app)
    manager.user_loader(lambda value: None)
    app.register_blueprint(module.login_bp)
    app.add_url_rule('/forgot', 'auth_password_reset.forgot_password', lambda: 'forgot')
    app.add_url_rule('/manager', 'manager_dashboard.manager_dashboard_view', lambda: 'manager')
    from services.account_access import enforce_account_access
    app.before_request(lambda: enforce_account_access(module._find_identity_doc, module.ROLE_SESSION_KEYS))
    monkeypatch.setattr(module, 'get_location', lambda ip: pytest.fail('login must not wait for optional geolocation'))
    return app, database, module


def test_anonymous_get_and_post_keep_audit_without_external_lookup(login_app):
    app, db, module = login_app
    client = app.test_client()
    response = client.get('/login')
    assert response.status_code == 200 and b'<form' in response.data
    assert db.login_logs.count_documents({}) == 0
    password = bcrypt.hashpw(b'fixture-secret', bcrypt.gensalt(rounds=12)).decode()
    user = db.users.insert_one({'username': 'officer', 'password': password, 'role': 'manager', 'is_active': True}).inserted_id
    assert client.post('/login', data={'username': 'officer', 'password': 'wrong'}).status_code == 302
    assert db.login_logs.count_documents({}) == 0
    response = client.post('/login?next=https://untrusted.example/', data={'username': 'officer', 'password': 'fixture-secret'})
    assert response.status_code == 302 and response.location.endswith('/manager')
    record = db.login_logs.find_one()
    assert record['user_id'] == str(user) and record['ip_enrichment']['state'] == 'pending'
    assert record['ip_location'] == {}
    assert db.users.find_one() ['password'] == password
    assert client.get('/login').location.endswith('/manager')
    db.users.update_one({'_id': user}, {'$set': {'login_blocked': True}})
    response = client.get('/login', headers={'Accept': 'application/json'})
    assert response.status_code == 401


def test_archived_eligibility_and_safe_next(login_app):
    app, db, module = login_app
    password = bcrypt.hashpw(b'fixture-secret', bcrypt.gensalt(rounds=12)).decode()
    oid = db.Exited.insert_one({'username': 'archived', 'password': password, 'role': 'manager', 'employment_status': 'Exited', 'is_active': False}).inserted_id
    client = app.test_client()
    client.post('/login', data={'username': 'archived', 'password': 'fixture-secret'})
    assert db.login_logs.count_documents({}) == 0
    db.Exited.update_one({'_id': oid}, {'$set': {'is_active': True}})
    response = client.post('/login', data={'username': 'archived', 'password': 'fixture-secret', 'next': '/manager?tab=approved'})
    assert response.location.endswith('/manager?tab=approved')
    assert db.login_logs.count_documents({}) == 1


def test_logout_clears_session_and_returns_to_react_login(login_app):
    app, _db, _module = login_app
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 'fixture-user'
        session['manager_id'] = 'fixture-user'
        session['role'] = 'manager'

    response = client.get('/logout', follow_redirects=False)
    assert response.status_code == 302
    assert response.location.endswith('/login')
    with client.session_transaction() as session:
        assert 'user_id' not in session
        assert 'manager_id' not in session

    response = client.get('/login')
    assert b'/static/login/assets/' in response.data


def test_identity_document_is_reused_only_inside_request(login_app, monkeypatch):
    app, db, module = login_app
    oid = db.users.insert_one({'role': 'agent'}).inserted_id
    original, calls = module.users_col.find_one, []
    def find(*args, **kwargs):
        calls.append(1); return original(*args, **kwargs)
    monkeypatch.setattr(module.users_col, 'find_one', find)
    for _ in range(2):
        with app.test_request_context('/'):
            assert module._find_identity_doc(str(oid)) == module._find_identity_doc(str(oid))
    assert len(calls) == 2
