from datetime import datetime, timezone

from bson import ObjectId
import pytest
from werkzeug.exceptions import Forbidden

from test_manager_suite import branch_data
from test_manager_suite_access import manager_suite_app
from test_login_performance import login_app


def test_hr_changes_have_server_owned_attribution_and_logs(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/reminders/create', method='POST'):
        key = f.db.hr_reminders.insert_one({'title': 'Branch training', 'hr_recorded_by': {'role': 'hr', 'name': 'Forged'}}).inserted_id
        stored = f.raw.hr_reminders.find_one(key)
        assert stored['hr_recorded_by']['role'] == 'manager'
        assert stored['hr_recorded_by']['user_id'] == str(f.manager)
        assert stored['hr_recorded_by']['name'] == 'Accra Manager'
    with f.app.test_request_context('/hr/reminders/update', method='POST'):
        f.db.hr_reminders.update_one({'_id': key}, {'$set': {'title': 'Updated', 'hr_recorded_by': {'role': 'executive'}}})
    with f.app.test_request_context('/hr/reminders/delete', method='DELETE'):
        f.db.hr_reminders.delete_one({'_id': key})
    logs = list(f.raw.hr_record_activity.find({'record_id': str(key)}).sort('timestamp', 1))
    assert [entry['operation'] for entry in logs] == ['created', 'updated', 'deleted']
    assert all(entry['actor']['user_id'] == str(f.manager) and entry['branch'] == 'Accra' for entry in logs)
    assert logs[-1]['record_label'] == 'Updated'


def test_central_hr_edits_preserve_manager_origin_and_identify_new_actor(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/reminders/create', method='POST'):
        key = f.db.hr_reminders.insert_one({'title': 'Manager entry'}).inserted_id
    original = f.raw.hr_reminders.find_one(key)['hr_recorded_by']
    f.identity.update(role='hr', user_id=str(f.support))
    with f.app.test_request_context('/hr/reminders/update', method='POST'):
        f.db.hr_reminders.update_one({'_id': key}, {'$set': {'title': 'Reviewed by main HR'}})
    stored = f.raw.hr_reminders.find_one(key)
    assert stored['hr_recorded_by'] == original
    assert stored['hr_last_updated_by']['role'] == 'hr'
    assert f.raw.hr_record_activity.find_one({'operation': 'updated'})['actor']['role'] == 'hr'


def test_embedded_case_attribution_and_secret_values_are_not_logged(branch_data):
    f = branch_data
    case_id = ObjectId()
    with f.app.test_request_context('/hr/employee/case/add', method='POST'):
        f.db.users.update_one({'_id': f.agent}, {'$push': {'cases': {'_id': case_id, 'title': 'Attendance', 'details': 'PRIVATE-NOTES'}}, '$set': {'password': 'SECRET-PASSWORD'}})
    assert f.raw.users.find_one(f.agent)['cases'][0]['hr_recorded_by']['role'] == 'manager'
    log = f.raw.hr_record_activity.find_one({})
    assert log['fields'] == ['cases', 'password']
    assert 'PRIVATE-NOTES' not in str(log) and 'SECRET-PASSWORD' not in str(log)
    with f.app.test_request_context('/hr/employee/case/update', method='POST'):
        f.db.users.update_one({'_id': f.agent, 'cases._id': case_id}, {'$set': {'cases.$.status': 'Closed'}})
    assert f.raw.users.find_one(f.agent)['cases'][0]['hr_last_updated_by']['user_id'] == str(f.manager)


def test_foreign_unmatched_and_read_requests_do_not_create_completed_activity(branch_data):
    f = branch_data
    with f.app.test_request_context('/hr/employees/update', method='POST'):
        assert f.db.users.update_one({'_id': f.other_agent}, {'$set': {'name': 'Changed'}}).matched_count == 0
        with pytest.raises(Forbidden):
            f.db.users.update_one({'_id': f.agent}, {'$set': {'branch': 'Kumasi'}})
    with f.app.test_request_context('/hr/dashboard'):
        f.db.users.find_one(f.agent)
    assert f.raw.hr_record_activity.count_documents({}) == 0


def test_other_modules_do_not_receive_hr_metadata_or_logs(branch_data):
    f = branch_data
    with f.app.test_request_context('/api/customer-support/tickets', method='POST'):
        key = f.db.complaints.insert_one({'customer_id': f.customer, 'subject': 'Support'}).inserted_id
    assert 'hr_recorded_by' not in f.raw.complaints.find_one(key)
    assert f.raw.hr_record_activity.count_documents({}) == 0


def test_activity_page_filters_managers_dates_and_sources_and_is_read_only(manager_suite_app):
    _, client, database, signin, manager, other_manager, *_ = manager_suite_app
    hr_id = ObjectId()
    database.users.insert_one({'_id': hr_id, 'name': 'Main HR', 'role': 'hr', 'username': 'main-hr'})
    stamp = datetime.now(timezone.utc)
    for user_id, branch, label, role in [(manager, 'Accra', 'Accra entry', 'manager'), (other_manager, 'Kumasi', 'Kumasi entry', 'manager'), (hr_id, 'HQ', 'Main HR entry', 'hr')]:
        database.hr_record_activity.insert_one({'actor': {'user_id': str(user_id), 'role': role, 'name': label, 'branch': branch}, 'branch': branch,
            'timestamp': stamp, 'operation': 'created', 'record_id': str(ObjectId()), 'record_label': label, 'collection': 'hr_reminders',
            'action': 'reminder created', 'fields': []})
    assert client.get('/hr/activity-logs').status_code == 403
    assert client.get('/hr/activity-logs/context').status_code == 403
    signin('hr', hr_id)
    response = client.get('/hr/activity-logs')
    assert response.status_code == 200
    assert all(label in response.get_data(as_text=True) for label in ['Accra entry', 'Kumasi entry', 'Main HR entry'])
    response = client.get('/hr/activity-logs', query_string={'manager': str(manager)})
    html = response.get_data(as_text=True)
    # Names also appear in manager options, so assert record table contents.
    table = html.split('<tbody>')[1].split('</tbody>')[0]
    assert 'Accra entry' in table and 'Kumasi entry' not in table and 'Main HR entry' not in table
    assert 'No activities match' in client.get('/hr/activity-logs?to=2000-01-01').get_data(as_text=True)
    assert client.get('/hr/activity-logs?from=invalid').status_code == 400
    assert client.get('/hr/activity-logs?from=2026-10-21&to=2026-01-01').status_code == 400
    context = client.get('/hr/activity-logs/context?path=/hr/reminders').json
    assert {row['branch'] for row in context['records']} == {'Accra', 'Kumasi'}
    assert client.post('/hr/activity-logs').status_code == 405
    signin('agent', manager)
    assert client.get('/hr/activity-logs').status_code == 403
