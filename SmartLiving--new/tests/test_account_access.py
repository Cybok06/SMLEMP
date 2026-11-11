import ast
from datetime import datetime
from pathlib import Path
import secrets
from unittest.mock import MagicMock

import pytest
from flask import Flask, session, jsonify, redirect, url_for, flash, request
from flask_login import LoginManager
from flask_login.utils import encode_cookie

from services.account_access import enforce_account_access
from services.account_access import login_blocked


@pytest.fixture
def access_app():
    app = Flask(__name__)
    app.secret_key = 'test-only'
    manager = LoginManager(app)
    manager.user_loader(lambda uid: None)
    doc = {'_id': 'target', 'auth_version': '0'}
    app.before_request(lambda: enforce_account_access(lambda uid: doc, ['manager_id', 'executive_id']))
    app.add_url_rule('/login', 'login.login', lambda: 'login')
    app.add_url_rule('/page', 'page', lambda: 'private', methods=['GET', 'POST'])
    return app, doc


@pytest.mark.parametrize('key', ['user_id', 'manager_id', 'executive_id', '_user_id'])
def test_revokes_legacy_sessions_on_any_page(access_app, key):
    app, doc = access_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[key] = 'target'
    assert client.get('/page').status_code == 200
    doc['auth_version'] = 'revoked'
    assert client.get('/page').status_code == 302
    with client.session_transaction() as sess:
        assert key not in sess


def test_multiple_devices_and_fresh_login(access_app):
    app, doc = access_app
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        with client.session_transaction() as sess:
            sess.update(user_id='target', auth_version='0')
    doc['auth_version'] = 'new'
    for client in clients:
        assert client.get('/page').status_code == 302
    with clients[0].session_transaction() as sess:
        sess.update(user_id='target', auth_version='new')
    assert clients[0].get('/page').status_code == 200


@pytest.mark.parametrize('token', ['target', 'target:old'])
def test_remember_cookie_cannot_restore_revoked_session(access_app, token):
    app, doc = access_app
    doc['auth_version'] = 'new'
    client = app.test_client()
    with app.app_context():
        client.set_cookie('remember_token', encode_cookie(token))
    response = client.get('/page')
    assert response.status_code == 302
    assert any('remember_token=;' in value for value in response.headers.getlist('Set-Cookie'))


def test_block_enforced_for_api_and_unblock_does_not_restore_old_session(access_app):
    app, doc = access_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(user_id='target', auth_version='0')
    doc.update(login_blocked=True, auth_version='new')
    response = client.post('/page', json={})
    assert response.status_code == 401
    assert 'blocked' in response.json['error']
    doc['login_blocked'] = False
    with client.session_transaction() as sess:
        sess.update(user_id='target', auth_version='0')
    assert client.get('/page').status_code == 302


def action_app():
    # Load the route in isolation so tests never connect to the production DB.
    tree = ast.parse(Path('executive_users.py').read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'executive_account_access')
    node.decorator_list = []
    users = MagicMock()
    users.find_one.return_value = {'_id': 'target'}
    env = dict(session=session, request=request, jsonify=jsonify, redirect=redirect,
               url_for=url_for, flash=flash, secrets=secrets, datetime=datetime,
               users_col=users, _exec_users_write_guard=lambda: session.get('role') == 'executive',
               _safe_origin=lambda: True, _coerce_object_id=lambda uid: uid,
               _linked_role_docs=lambda doc: [doc, {'_id': 'linked'}],
               get_current_identity=lambda: {'user_id': 'actor'}, log_activity=MagicMock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<route>', 'exec'), env)
    app = Flask(__name__)
    app.secret_key = 'test-only'
    app.add_url_rule('/action/<user_id>', view_func=env['executive_account_access'], methods=['POST'])
    app.add_url_rule('/profile/<user_id>', 'executive_users.executive_user_profile', lambda user_id: 'profile')
    return app, users, env


@pytest.mark.parametrize('action', ['block', 'unblock', 'logout_all'])
def test_actions_apply_to_all_linked_roles_and_audit(action):
    app, users, env = action_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(role='executive', account_access_csrf='csrf')
    assert client.post('/action/target', data={'action': action, 'csrf_token': 'csrf'}).status_code == 302
    query, update = users.update_many.call_args.args
    assert set(query['_id']['$in']) == {'target', 'linked'}
    fields = update['$set']
    assert ('auth_version' in fields) == (action != 'unblock')
    if action != 'logout_all':
        assert fields['login_blocked'] == (action == 'block')
    env['log_activity'].assert_called_once()


@pytest.mark.parametrize('role,csrf', [('admin', 'csrf'), ('agent', 'csrf'), ('executive', 'wrong')])
def test_rejects_unauthorized_or_forged_actions(role, csrf):
    app, users, _ = action_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(role=role, account_access_csrf='csrf')
    assert client.post('/action/target', data={'action': 'block', 'csrf_token': csrf}).status_code == 403
    users.update_many.assert_not_called()


def test_blocked_account_cannot_start_session_or_switch_role():
    tree = ast.parse(Path('login.py').read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_set_role_session')
    env = {'login_blocked': login_blocked}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<login>', 'exec'), env)
    with pytest.raises(ValueError, match='blocked'):
        env['_set_role_session']('agent', {'_id': 'target', 'login_blocked': True})


def test_valid_remember_cookie_remains_usable(access_app):
    app, doc = access_app
    doc['auth_version'] = 'new'
    client = app.test_client()
    with app.app_context():
        client.set_cookie('remember_token', encode_cookie('target:new'))
    assert client.get('/page').status_code == 200


def test_executive_cannot_block_own_linked_account():
    app, users, env = action_app()
    env['get_current_identity'] = lambda: {'user_id': 'linked'}
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(role='executive', account_access_csrf='csrf')
    assert client.post('/action/target', data={'action': 'block', 'csrf_token': 'csrf'}).status_code == 302
    users.update_many.assert_not_called()
