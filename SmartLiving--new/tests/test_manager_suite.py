from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import mongomock
import pytest
from bson import ObjectId
from flask import Flask
from werkzeug.exceptions import Forbidden

from services.manager_suite_scope import manager_scope, scoped_db


@pytest.fixture
def branch_data(monkeypatch):
    import login
    raw = mongomock.MongoClient().suite_tests
    manager, other_manager, agent, other_agent, support, customer, other_customer = [ObjectId() for _ in range(7)]
    raw.users.insert_many([
        {'_id': manager, 'role': 'manager', 'branch': 'Accra', 'name': 'Accra Manager'},
        {'_id': other_manager, 'role': 'manager', 'branch': 'Kumasi', 'name': 'Kumasi Manager'},
        {'_id': agent, 'role': 'agent', 'manager_id': str(manager), 'name': 'Accra Agent'},
        {'_id': other_agent, 'role': 'agent', 'branch': 'Kumasi', 'manager_id': other_manager, 'name': 'Kumasi Agent'},
        {'_id': support, 'role': 'customer_support', 'branch': 'Accra'},
    ])
    raw.customers.insert_many([
        {'_id': customer, 'agent_id': str(agent), 'name': 'Own Customer'},
        # A misleading historical branch label must never override ownership.
        {'_id': other_customer, 'agent_id': other_agent, 'branch': 'Accra', 'name': 'Foreign Customer'},
    ])
    identity = {'is_authenticated': True, 'role': 'manager', 'user_id': str(manager)}
    monkeypatch.setattr(login, 'get_current_identity', lambda: identity)
    app = Flask(__name__)
    return SimpleNamespace(raw=raw, db=scoped_db(raw), identity=identity, app=app, manager=manager,
                           other_manager=other_manager, agent=agent, other_agent=other_agent,
                           support=support, customer=customer, other_customer=other_customer)


def test_employee_and_customer_scope_cannot_be_replaced_by_query(branch_data):
    f = branch_data
    with f.app.test_request_context('/api/customer-support/customers'):
        assert {row['_id'] for row in f.db.users.find({})} == {f.manager, f.agent, f.support}
        assert [row['_id'] for row in f.db.customers.find({'$or': [{}, {'_id': f.other_customer}]})] == [f.customer]
        assert f.db.customers.find_one(f.other_customer) is None
        assert f.db.customers.count_documents({}) == 1
        assert f.db.users.distinct('branch') == ['Accra']


def test_updates_deletes_and_upserts_cannot_target_another_branch(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/employees/profile'):
        assert f.db.users.update_one({'_id': f.other_agent}, {'$set': {'name': 'Changed'}}).modified_count == 0
        assert f.db.users.delete_one({'_id': f.other_agent}).deleted_count == 0
        f.db.users.update_many({'role': 'agent'}, {'$set': {'status': 'Probation'}})
        assert f.raw.users.find_one({'_id': f.agent})['status'] == 'Probation'
        assert 'status' not in f.raw.users.find_one({'_id': f.other_agent})
        with pytest.raises(Forbidden):
            f.db.users.update_one({'_id': f.agent}, {'$set': {'manager_id': str(f.other_manager)}})
        with pytest.raises(Forbidden):
            f.db.users.update_one({'_id': f.agent}, {'$set': {'branch': 'Kumasi'}})
        with pytest.raises(Forbidden):
            f.db.customers.update_one({'_id': f.customer}, {'$set': {'agent_id': f.other_agent}}, upsert=True)


def test_new_hr_and_support_records_are_bound_to_the_branch(branch_data):
    f = branch_data
    with f.app.test_request_context('/api/hr/cases'):
        case = f.db.hr_cases.insert_one({'employee_id': str(f.agent), 'status': 'Open'}).inserted_id
        assert f.db.hr_cases.find_one(case)['suite_branch'] == 'Accra'
        with pytest.raises(Forbidden):
            f.db.hr_cases.insert_one({'employee_id': str(f.other_agent)})
        with pytest.raises(Forbidden):
            f.db.hr_exits.insert_one({'employee': {'user_id': str(f.other_agent), 'branch': 'Kumasi'}})
    with f.app.test_request_context('/api/customer-support/tickets'):
        ticket = f.db.complaints.insert_one({'customer_id': f.customer, 'assigned_to_id': str(f.agent)}).inserted_id
        assert f.db.complaints.find_one(ticket)
        with pytest.raises(Forbidden):
            f.db.complaints.insert_one({'customer_id': f.other_customer, 'branch': 'Accra'})


def test_customer_and_employee_ownership_take_priority_over_labels_and_creators(branch_data):
    f = branch_data
    f.raw.complaints.insert_many([
        {'customer_id': f.customer, 'status': 'Open'},
        {'customer_id': f.other_customer, 'created_by': str(f.manager), 'branch': 'Accra'},
    ])
    f.raw.hr_cases.insert_many([
        {'employee_id': str(f.agent)},
        {'employee_id': str(f.other_agent), 'manager_id': str(f.manager), 'branch': 'Accra'},
    ])
    with f.app.test_request_context('/api/customer-support/tickets'):
        assert f.db.complaints.count_documents({}) == 1
    with f.app.test_request_context('/hr/cases'):
        assert f.db.hr_cases.count_documents({}) == 1


def test_transferred_customers_follow_the_current_branch(branch_data):
    f = branch_data
    f.raw.packages.insert_one({'customer_id': f.customer, 'branch': 'Accra'})
    with f.app.test_request_context('/api/customer-support/deliveries'):
        assert f.db.packages.count_documents({}) == 1
    f.raw.customers.update_one({'_id': f.customer}, {'$set': {'agent_id': f.other_agent}})
    with f.app.test_request_context('/api/customer-support/deliveries'):
        assert f.db.packages.count_documents({}) == 0


def test_agent_transfer_overrides_a_stale_branch_label(branch_data):
    f = branch_data
    f.raw.users.update_one({'_id': f.agent}, {'$set': {'branch': 'Accra', 'manager_id': f.other_manager}})
    with f.app.test_request_context('/api/customer-support/customers'):
        assert f.db.users.find_one(f.agent) is None
        assert f.db.customers.count_documents({}) == 0


def test_archived_copies_cannot_override_current_ownership(branch_data):
    f = branch_data
    f.raw.Archived_customers.insert_one({'_id': f.other_customer, 'agent_id': f.agent})
    f.raw.Exited.insert_one({'_id': f.other_agent, 'role': 'agent', 'manager_id': f.manager})
    f.raw.packages.insert_one({'customer_id': f.other_customer})
    with f.app.test_request_context('/api/customer-support/deliveries'):
        assert f.db.packages.count_documents({}) == 0
        assert f.db.users.find_one(f.other_agent) is None


def test_manager_cannot_grant_global_privileges_or_reset_privileged_accounts(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/employees/profile'):
        for fields in ({'role': 'executive'}, {'main_admin': True}, {'account_group_id': 'admin-group'}):
            with pytest.raises(Forbidden):
                f.db.users.update_one({'_id': f.agent}, {'$set': fields})
        with pytest.raises(Forbidden):
            f.db.users.update_one({'_id': f.support}, {'$set': {'password': 'replacement'}})
        assert f.db.users.update_one({'_id': f.agent}, {'$set': {'name': 'Updated Agent'}}).modified_count == 1


def test_global_configuration_is_readable_but_cannot_be_changed(branch_data):
    f = branch_data
    f.raw.hr_leave_settings.insert_one({'_id': 'default', 'annual_days': 20})
    f.raw.hr_leave_settings.insert_one({'branch': 'Kumasi', 'annual_days': 30})
    with f.app.test_request_context('/hr/leave/settings'):
        assert len(list(f.db.hr_leave_settings.find({}))) == 1
        with pytest.raises(Forbidden):
            f.db.hr_leave_settings.update_one({'_id': 'default'}, {'$set': {'annual_days': 99}}, upsert=True)
        with pytest.raises(Forbidden):
            f.db.hr_roles.insert_one({'name': 'Company administrator'})
    assert f.raw.hr_leave_settings.find_one('default')['annual_days'] == 20


def test_update_operators_cannot_remove_ownership_or_change_privileges(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/employees/profile'):
        for update in ({'$unset': {'manager_id': ''}}, {'$inc': {'main_admin': 1}},
                       {'$rename': {'name': 'role'}}, {'$push': {'account_group_id': 'admin'}}):
            with pytest.raises(Forbidden):
                f.db.users.update_one({'_id': f.agent}, update)


def test_aggregations_and_lookup_subqueries_receive_branch_constraints(branch_data):
    f = branch_data
    original = [{'$lookup': {'from': 'users', 'localField': 'agent_id', 'foreignField': '_id', 'as': 'agent'}}]
    snapshot = deepcopy(original)
    with f.app.test_request_context('/api/customer-support/customers'):
        with patch.object(f.raw.customers, 'aggregate', return_value=[]) as aggregate:
            list(f.db.customers.aggregate(original))
        pipeline = aggregate.call_args.args[0]
        assert pipeline[0]['$match']['agent_id']['$in']
        assert pipeline[1]['$lookup']['pipeline'][0]['$match']['_id']['$in']
        with pytest.raises(Forbidden):
            list(f.db.customers.aggregate([{'$unionWith': 'customers'}]))
    assert original == snapshot


@pytest.mark.parametrize('role', ['hr', 'executive', 'customer_support', 'admin'])
def test_other_roles_retain_existing_access(branch_data, role):
    f = branch_data
    f.identity['role'] = role
    with f.app.test_request_context('/hr/employees'):
        assert f.db.users.count_documents({}) == 5
        assert f.db.customers.count_documents({}) == 2


def test_manager_session_outside_suite_is_unchanged(branch_data):
    f = branch_data
    with f.app.test_request_context('/manager/dashboard'):
        assert manager_scope(f.db) is None
        assert f.db.users.count_documents({}) == 5


def test_missing_manager_branch_fails_closed(branch_data):
    f = branch_data
    f.raw.users.update_one({'_id': f.manager}, {'$unset': {'branch': ''}})
    with f.app.test_request_context('/hr/dashboard'):
        with pytest.raises(Forbidden):
            f.db.users.find_one(f.manager)
