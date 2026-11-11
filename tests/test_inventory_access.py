import ast
from pathlib import Path

import pytest
from flask import Flask, jsonify, redirect, request, url_for

ROOT = Path(__file__).resolve().parents[1]


def load_functions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('role', ['inventory', 'executive'])
def test_inventory_roles_receive_full_module_access(role):
    identity = {'is_authenticated': True, 'role': role, 'is_main_admin': False}
    ns = load_functions('login.py', {'role_required'}, {
        'get_current_identity': lambda: identity, 'request': request, 'jsonify': jsonify,
        'redirect': redirect, 'url_for': url_for,
    })
    app = Flask(__name__)
    with app.test_request_context('/api/inventory/session'):
        result = ns['role_required']('inventory')(lambda: identity['is_main_admin'])()
        assert result is True


def test_inventory_role_does_not_gain_access_to_other_modules():
    identity = {'is_authenticated': True, 'role': 'inventory', 'is_main_admin': False}
    ns = load_functions('login.py', {'role_required'}, {
        'get_current_identity': lambda: identity, 'request': request, 'jsonify': jsonify,
        'redirect': redirect, 'url_for': url_for,
    })
    app = Flask(__name__)
    with app.test_request_context('/api/admin/users'):
        response, status = ns['role_required']('admin')(lambda: 'private')()
        assert status == 403
        assert identity['is_main_admin'] is False


@pytest.mark.parametrize('role,authenticated,expected', [
    ('inventory', True, None), ('executive', True, None),
    ('manager', True, 403), ('inventory', False, 401),
])
def test_inventory_settings_access(role, authenticated, expected):
    identity = {'role': role, 'is_authenticated': authenticated, 'is_main_admin': False}
    ns = load_functions('Backend/inventory/settings_api.py', {'_main_admin_guard', '_forbidden_json'}, {
        'get_current_identity': lambda: identity, 'jsonify': jsonify,
    })
    with Flask(__name__).app_context():
        result = ns['_main_admin_guard']()
        assert (result[1] if result else None) == expected
