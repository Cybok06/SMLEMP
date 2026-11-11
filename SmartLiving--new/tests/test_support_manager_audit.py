from datetime import datetime, timezone

from bson import ObjectId
import pytest

from test_login_performance import login_app
from test_manager_suite_access import manager_suite_app
from test_manager_suite import branch_data


def log_call(client, customer_id, **changes):
    return client.post('/api/customer-support/calls', json={
        'customer_id': str(customer_id), 'type': 'Outbound', 'department': 'Customer Support',
        'purpose': 'Delivery follow-up', 'duration': '2m 10s', 'outcome': 'Resolved',
        'notes': 'PRIVATE CALL NOTES', **changes})


def add_task(client, customer_id, **changes):
    return client.post('/api/customer-support/tasks', json={
        'title': 'Arrange delivery', 'customer_id': str(customer_id), 'due_date': '2026-12-01', **changes})


def test_manager_call_has_badge_metadata_immediately_and_safe_activity(manager_suite_app):
    _, client, database, _, manager, _, _, _, _, customer, _ = manager_suite_app
    response = log_call(client, customer, support_recorded_by={'role': 'customer_support', 'name': 'Forged'})
    assert response.status_code == 201
    call = response.json['call']
    assert call['support_recorded_by']['user_id'] == str(manager)
    assert call['support_recorded_by']['name'] == 'Accra Manager'
    assert call['support_recorded_by']['role'] == 'manager'
    stored = database.customer_support_calls.find_one({'_id': ObjectId(call['_id'])})
    assert stored['support_recorded_by'] == call['support_recorded_by']
    record = database.support_record_activity.find_one({'record_id': call['_id']})
    assert record['operation'] == 'created' and record['record_type'] == 'calls'
    assert record['reference'] == call['id'] and record['branch'] == 'Accra'
    assert 'PRIVATE CALL NOTES' not in str(record)
    assert 'hr_recorded_by' not in stored
    assert database.hr_record_activity.count_documents({}) == 0


def test_first_human_logging_a_mobile_stub_gets_recorder_badge(manager_suite_app):
    _, client, database, _, manager, _, _, _, _, customer, _ = manager_suite_app
    key = database.customer_support_calls.insert_one({'source': 'android', 'customer_id': customer,
        'customer_name': 'Own Customer', 'type': 'Outbound', 'enrichment_status': 'needs_update'}).inserted_id
    response = client.patch('/api/customer-support/calls/' + str(key), json={
        'purpose': 'Delivery follow-up', 'outcome': 'Resolved', 'department': 'Customer Support'})
    assert response.status_code == 200
    assert response.json['call']['support_recorded_by']['user_id'] == str(manager)
    assert response.json['call']['support_recorded_by']['role'] == 'manager'


def test_manager_task_create_update_delete_preserves_origin_and_complete_history(manager_suite_app):
    _, client, database, _, manager, *_rest = manager_suite_app
    customer = manager_suite_app[-2]
    response = add_task(client, customer)
    assert response.status_code == 201
    task = response.json['task']
    origin = task['support_recorded_by']
    assert origin['role'] == 'manager'
    assert database.tasks.find_one(ObjectId(task['_id']))['created_by_role'] == 'manager'
    response = client.patch('/api/customer-support/tasks/' + task['_id'], json={'status': 'Completed'})
    assert response.status_code == 200
    assert response.json['task']['support_recorded_by'] == origin
    assert response.json['task']['support_last_updated_by']['user_id'] == str(manager)
    assert client.delete('/api/customer-support/tasks/' + task['_id']).status_code == 200
    assert database.tasks.find_one(ObjectId(task['_id'])) is None
    assert [row['operation'] for row in database.support_record_activity.find({'record_id': task['_id']}).sort('timestamp', 1)] == ['created', 'updated', 'deleted']


def test_central_support_edits_keep_manager_origin_and_filter_by_source(manager_suite_app):
    _, client, database, signin, manager, _, _, _, officer, customer, _ = manager_suite_app
    call = log_call(client, customer).json['call']
    signin('customer_support', officer)
    response = client.patch('/api/customer-support/calls/' + call['_id'], json={
        'purpose': 'Review delivery', 'department': 'Customer Support', 'outcome': 'Resolved'})
    assert response.status_code == 200
    assert response.json['call']['support_recorded_by'] == call['support_recorded_by']
    assert response.json['call']['support_last_updated_by']['role'] == 'customer_support'
    main_task = add_task(client, customer).json['task']
    assert main_task['support_recorded_by']['role'] == 'customer_support'
    records = client.get('/api/customer-support/activity-logs').json
    assert records['pagination']['total'] == 3
    manager_records = client.get('/api/customer-support/activity-logs', query_string={'manager': str(manager)}).json['records']
    assert len(manager_records) == 1 and manager_records[0]['record_type'] == 'calls'
    assert client.get('/api/customer-support/activity-logs?source=support').json['pagination']['total'] == 2
    assert client.get('/api/customer-support/me').json['profile']['can_view_activity_logs'] is True


def test_other_branch_cannot_read_update_or_delete_manager_records(manager_suite_app):
    _, client, database, signin, _, other_manager, _, _, _, customer, other_customer = manager_suite_app
    call = log_call(client, customer).json['call']
    task = add_task(client, customer).json['task']
    signin('manager', other_manager)
    assert client.get('/api/customer-support/calls').json['calls'] == []
    assert client.get('/api/customer-support/tasks').json['tasks'] == []
    assert client.patch('/api/customer-support/calls/' + call['_id'], json={}).status_code == 404
    assert client.delete('/api/customer-support/tasks/' + task['_id']).status_code == 404
    assert log_call(client, customer).status_code == 400
    assert database.support_record_activity.count_documents({}) == 2
    assert log_call(client, other_customer).status_code == 201


@pytest.mark.parametrize('role', ['manager', 'hr', 'agent', 'inventory'])
def test_global_support_activity_api_does_not_grant_other_roles_access(manager_suite_app, role):
    _, client, _, signin, manager, *_ = manager_suite_app
    signin(role, manager)
    assert client.get('/api/customer-support/activity-logs').status_code == 403
    if role == 'manager':
        assert client.get('/api/customer-support/me').json['profile']['can_view_activity_logs'] is False


def test_activity_filters_dates_pagination_and_read_only_access(manager_suite_app):
    _, client, database, signin, manager, other_manager, _, _, officer, *_ = manager_suite_app
    for number in range(55):
        other = number == 54
        database.support_record_activity.insert_one({'actor': {'user_id': str(other_manager if other else manager), 'role': 'manager', 'name': 'Kumasi Manager' if other else 'Accra Manager', 'branch': 'Kumasi' if other else 'Accra'},
            'branch': 'Kumasi' if other else 'Accra', 'timestamp': datetime(2026, 10, 21, tzinfo=timezone.utc),
            'operation': 'created', 'record_type': 'calls' if other else 'tasks', 'record_label': f'Record {number}',
            'reference': f'REF-{number}', 'collection': 'tasks', 'record_id': str(ObjectId()), 'action': 'support tasks', 'fields': []})
    signin('customer_support', officer)
    response = client.get('/api/customer-support/activity-logs').json
    assert len(response['records']) == 50 and response['pagination']['pages'] == 2
    assert len(client.get('/api/customer-support/activity-logs?page=2').json['records']) == 5
    filtered = client.get('/api/customer-support/activity-logs?type=calls&branch=Kumasi&from=2026-10-21&to=2026-10-21').json
    assert filtered['pagination']['total'] == 1
    assert filtered['records'][0]['actor']['user_id'] == str(other_manager)
    assert client.get('/api/customer-support/activity-logs?search=%5B').json['records'] == []
    for query in ('from=bad', 'from=2026-12-01&to=2026-01-01', 'page=bad', 'type=invalid'):
        assert client.get('/api/customer-support/activity-logs?' + query).status_code == 400
    assert client.post('/api/customer-support/activity-logs').status_code == 405


def test_metadata_cannot_be_forged_or_removed_through_update_operators(branch_data):
    f = branch_data
    with f.app.test_request_context('/api/customer-support/tickets', method='POST'):
        key = f.db.complaints.insert_one({'customer_id': f.customer, 'subject': 'Delivery issue'}).inserted_id
    original = f.raw.complaints.find_one(key)['support_recorded_by']
    with f.app.test_request_context('/api/customer-support/tickets/update', method='PATCH'):
        f.db.complaints.update_one({'_id': key}, {'$set': {'support_recorded_by.role': 'executive'}, '$unset': {'support_recorded_by': ''}})
    assert f.raw.complaints.find_one(key)['support_recorded_by'] == original


def test_customer_timeline_and_ticket_notes_show_manager_origin(manager_suite_app):
    _, client, _, _, _, _, agent, _, _, customer, _ = manager_suite_app
    call = log_call(client, customer).json['call']
    response = client.post('/api/customer-support/tickets', json={'customer_id': str(customer), 'agent_id': str(agent), 'subject': 'Delivery check', 'description': 'Check item'})
    assert response.status_code == 201
    ticket = response.json['ticket']
    assert ticket['support_recorded_by']['role'] == 'manager'
    response = client.post('/api/customer-support/tickets/' + ticket['id'] + '/updates', json={'kind': 'note', 'text': 'Contacted customer'})
    assert response.status_code == 201, response.json
    detail = client.get('/api/customer-support/tickets/' + ticket['id']).json['ticket']
    assert detail['updates'][-1]['support_recorded_by']['role'] == 'manager'
    activities = client.get('/api/customer-support/customers/' + str(customer) + '/activity').json['activities']
    assert next(row for row in activities if row['id'] == 'call:' + call['_id'])['support_recorded_by']['role'] == 'manager'
