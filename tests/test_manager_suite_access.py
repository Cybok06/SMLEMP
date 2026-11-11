import ast
import importlib.util
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from bson import ObjectId
from flask import jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from jinja2 import ChoiceLoader, DictLoader
from werkzeug.routing import BuildError

from test_login_performance import login_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def manager_suite_app(login_app, monkeypatch):
    app, database, login = login_app
    app.static_folder = str(ROOT / 'static')
    manager, other_manager, agent, other_agent, officer, own_customer, other_customer = [ObjectId() for _ in range(7)]
    database.users.insert_many([
        {'_id': manager, 'name': 'Accra Manager', 'username': 'manager-a', 'role': 'manager', 'branch': 'Accra', 'status': 'Active'},
        {'_id': other_manager, 'name': 'Kumasi Manager', 'username': 'manager-b', 'role': 'manager', 'branch': 'Kumasi', 'status': 'Active'},
        {'_id': agent, 'name': 'Accra Agent', 'username': 'agent-a', 'role': 'agent', 'branch': 'Accra', 'manager_id': manager},
        {'_id': other_agent, 'name': 'Kumasi Agent', 'username': 'agent-b', 'role': 'agent', 'branch': 'Kumasi', 'manager_id': other_manager},
        {'_id': officer, 'name': 'Support Officer', 'username': 'support', 'role': 'customer_support', 'branch': 'Accra'},
    ])
    database.customers.insert_many([
        {'_id': own_customer, 'agent_id': str(agent), 'agent_branch': 'Accra', 'name': 'Own Customer', 'purchases': []},
        {'_id': other_customer, 'agent_id': str(other_agent), 'agent_branch': 'Kumasi', 'name': 'Foreign Customer', 'purchases': []},
    ])
    database.complaints.insert_many([
        {'customer_id': own_customer, 'assigned_to_id': str(agent), 'status': 'Open', 'created_at': datetime.utcnow()},
        {'customer_id': other_customer, 'assigned_to_id': str(other_agent), 'status': 'Open', 'created_at': datetime.utcnow()},
    ])

    def load(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / (name.replace('.', '/') + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(__import__('sys').modules, name, module)
        spec.loader.exec_module(module)
        return module

    hr = load('hr_backend.hr_dashboard')
    load('hr_backend.hr_activity_logs')
    attendance = load('hr_backend.hr_attendance')
    work_time = load('routes.attendance')
    loans = load('hr_backend.staff_loans')
    load('routes.executive_archive_customers')
    customers = load('customer_support_backend.customers_api')
    operations = load('customer_support_backend.tickets_calls_api')
    activity = load('customer_support_backend.activity_logs_api')
    profile = load('customer_support_backend.profile_api')
    app.register_blueprint(hr.hr_bp)
    app.register_blueprint(work_time.attendance_bp)
    app.register_blueprint(loans.staff_loans_bp)
    app.register_blueprint(customers.customer_support_customers_bp)
    app.register_blueprint(operations.customer_support_operations_bp)
    app.register_blueprint(activity.support_activity_bp)
    app.register_blueprint(profile.customer_support_profile_bp)
    # Keep this test focused on the suite's navigation, authorization and branch
    # data; the existing HR shell has its own independent UI workflows.
    app.jinja_loader = ChoiceLoader([DictLoader({
        'hr_pages/hr_dashboard.html': '<title>HR</title><h1>HR Control Center</h1><p>Branch: {{ current_branch }}</p><p>{{ hr_branches|join(", ") }}</p>',
    }), app.jinja_loader])
    app.context_processor(lambda: {'identity': login.get_current_identity()})
    real_url = url_for

    def template_url(endpoint, **values):
        try:
            return real_url(endpoint, **values)
        except BuildError:
            return '/fixture/' + endpoint
    app.jinja_env.globals['url_for'] = template_url
    app.view_functions['manager_dashboard.manager_dashboard_view'] = lambda: render_template_string(
        '<title>Manager</title>{% include "manager_sidebar.html" %}<h1>Manager Dashboard</h1>')

    # Exercise the actual frontend gate from app.py without booting unrelated
    # production integrations or allowing a real database connection.
    node = next(node for node in ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig')).body
                if isinstance(node, ast.FunctionDef) and node.name == 'operations_management_react_app')
    node.decorator_list = []
    frontend = ROOT / 'Frontend/Customer_Support/dist'
    assert (frontend / 'index.html').is_file(), (
        'Customer Support build missing. Run npm ci and npm run build in Frontend/Customer_Support.'
    )
    # Do not inject db: the production route must import its own dependency.
    namespace = {'get_current_identity': login.get_current_identity, 'request': request,
                 'redirect': redirect, 'url_for': url_for, 'send_from_directory': send_from_directory,
                 'os': os,
                 'OPERATIONS_MANAGEMENT_REACT_DIR': str(frontend)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), namespace)
    app.add_url_rule('/customer-support/app', 'operations_management_react_app', namespace['operations_management_react_app'])
    app.add_url_rule('/customer-support/app/<path:path>', 'operations_management_react_app', namespace['operations_management_react_app'])
    app.add_url_rule('/api/executive/support-probe', 'executive_probe', login.role_required('customer_support')(lambda: 'private'))
    app.add_url_rule('/api/agent/support-probe', 'agent_probe', login.role_required('agent')(lambda: 'private'))

    client = app.test_client()
    def signin(role='manager', user_id=manager):
        with client.session_transaction() as state:
            state.clear()
            state.update(role=role, user_id=str(user_id), **{role + '_id': str(user_id)})
    signin()
    return app, client, database, signin, manager, other_manager, agent, other_agent, officer, own_customer, other_customer


def test_manager_work_time_analytics_are_branch_limited(manager_suite_app):
    _, client, database, _, manager, _, agent, other_agent, *_ = manager_suite_app
    for user_id in (agent, other_agent):
        database.attendance_workdays.insert_one({'user_id': str(user_id), 'attendance_date': '2026-01-02'})
    response = client.get('/attendance/api/analytics?from=2026-01-01&to=2026-01-03')
    assert response.status_code == 200
    assert {row['user_id'] for row in response.json['employees']} >= {str(manager), str(agent)}
    assert str(other_agent) not in {row['user_id'] for row in response.json['employees']}
    assert [row['user_id'] for row in response.json['records']] == [str(agent)]
    assert client.get('/admin/work-time').status_code == 403


def test_suite_tabs_reuse_manager_session_and_default_hr_to_own_branch(manager_suite_app):
    _, client, _, _, manager, *_ = manager_suite_app
    html = client.get('/manager').get_data(as_text=True)
    assert 'managerSuiteMenu' in html
    assert 'href="/hr/dashboard" target="_blank"' in html
    assert 'href="/customer-support/app" target="_blank"' in html
    response = client.get('/hr/dashboard?branch=Kumasi')
    assert response.status_code == 200
    assert 'Branch: Accra' in response.get_data(as_text=True)
    assert 'Kumasi' not in response.get_data(as_text=True)
    assert client.get('/customer-support/app').status_code == 200
    assert client.get('/api/customer-support/me').json['profile']['user_id'] == str(manager)
    with client.session_transaction() as state:
        assert state['role'] == 'manager' and state['manager_id'] == str(manager)
        assert not state.get('hr_id') and not state.get('customer_support_id')
    assert client.get('/manager').status_code == 200


def test_manager_support_serves_real_frontend_and_compiled_assets(manager_suite_app):
    _, client, *_ = manager_suite_app
    response = client.get('/customer-support/app')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<div id="root"></div>' in html
    assets = re.findall(r'(?:src|href)="(/customer-support/app/assets/[^"\s]+)"', html)
    assert any(path.endswith('.js') for path in assets)
    assert any(path.endswith('.css') for path in assets)
    for path in assets:
        assert client.get(path).status_code == 200, path
    assert client.get('/customer-support/app/customers').status_code == 200


def test_hr_employee_picker_hides_other_branches(manager_suite_app):
    _, client, _, _, manager, _, agent, *_ = manager_suite_app
    result = client.get('/hr/attendance/employees_list')
    assert result.status_code == 200
    people = result.json['employees']
    assert {row['id'] for row in people} >= {str(manager), str(agent)}
    assert all(row['branch'] == 'Accra' for row in people)


def test_support_global_cache_never_leaks_into_manager_branch(manager_suite_app):
    _, client, _, signin, manager, other_manager, _, _, officer, *_ = manager_suite_app
    signin('customer_support', officer)
    assert client.get('/api/customer-support/dashboard').json['metrics']['openTickets'] == 2
    assert len(client.get('/api/customer-support/customers').json['customers']) == 2
    signin('manager', manager)
    assert client.get('/api/customer-support/dashboard').json['metrics']['openTickets'] == 1
    data = client.get('/api/customer-support/customers').json
    assert [row['name'] for row in data['customers']] == ['Own Customer']
    assert data['filters']['branches'] == ['Accra']
    signin('manager', other_manager)
    data = client.get('/api/customer-support/customers').json
    assert [row['name'] for row in data['customers']] == ['Foreign Customer']
    assert data['filters']['branches'] == ['Kumasi']


def test_foreign_customer_urls_and_mutations_are_blocked(manager_suite_app):
    _, client, database, _, _, _, _, other_agent, _, _, other_customer = manager_suite_app
    assert client.get(f'/api/customer-support/customers/{other_customer}').status_code == 404
    result = client.post('/api/customer-support/tickets', json={
        'customer_id': str(other_customer), 'agent_id': str(other_agent), 'subject': 'Foreign', 'description': 'Blocked',
    })
    assert result.status_code == 400
    assert database.complaints.count_documents({}) == 2
    result = client.post('/api/customer-support/customers/archive', json={'customer_ids': [str(other_customer)]})
    assert result.status_code == 200 and result.json['counts']['archived'] == 0
    assert database.customers.find_one({'_id': other_customer})


def test_suite_entitlement_does_not_grant_other_roles(manager_suite_app):
    _, client, _, *_ = manager_suite_app
    assert client.get('/api/executive/support-probe').status_code == 403
    assert client.get('/api/agent/support-probe').status_code == 403


def test_manager_without_branch_cannot_open_suite(manager_suite_app):
    _, client, database, _, manager, *_ = manager_suite_app
    database.users.update_one({'_id': manager}, {'$unset': {'branch': ''}})
    assert client.get('/hr/dashboard').status_code == 403
    assert client.get('/customer-support/app').status_code == 403
    assert client.get('/api/customer-support/me').status_code == 403


def test_hr_staff_loan_directory_requires_suite_context_and_stays_in_branch(manager_suite_app):
    _, client, _, _, manager, _, agent, *_ = manager_suite_app
    assert client.get('/api/staff-loans/employees').status_code == 403
    result = client.get('/api/staff-loans/employees', headers={'X-Manager-Suite': 'hr'})
    assert result.status_code == 200
    people = result.json['employees']
    assert {row['id'] for row in people} >= {str(manager), str(agent)}
    assert all('Kumasi' not in row['name'] for row in people)
    assert client.get('/hr/staff-loans').status_code == 200


def test_leave_approval_cannot_access_another_branch_employee(manager_suite_app):
    _, client, database, _, _, _, _, other_agent, *_ = manager_suite_app
    leave_id = ObjectId()
    database.users.update_one({'_id': other_agent}, {'$set': {'leaves': [{'_id': leave_id, 'status': 'Pending'}]}})
    response = client.post(f'/hr/leave/request/{other_agent}/{leave_id}/approve')
    assert response.status_code == 404
    assert database.users.find_one({'_id': other_agent})['leaves'][0]['status'] == 'Pending'


def test_manager_can_create_own_branch_ticket_with_a_branch_unique_reference(manager_suite_app):
    _, client, database, signin, manager, other_manager, agent, other_agent, _, customer, other_customer = manager_suite_app
    def create(customer_id, agent_id):
        return client.post('/api/customer-support/tickets', json={
            'customer_id': str(customer_id), 'agent_id': str(agent_id),
            'subject': 'Branch issue', 'description': 'Customer needs assistance.',
        })
    first = create(customer, agent)
    assert first.status_code == 201
    first_reference = first.json['ticket']['id']
    own = database.complaints.find_one({'ticket_no': first_reference})
    assert own['suite_branch'] == 'Accra' and own['created_by'] == str(manager)
    signin('manager', other_manager)
    second = create(other_customer, other_agent)
    assert second.status_code == 201
    assert second.json['ticket']['id'] != first_reference
