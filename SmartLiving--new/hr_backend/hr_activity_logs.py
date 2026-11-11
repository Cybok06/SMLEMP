"""Central HR's read-only view of work recorded across manager branches."""
from datetime import datetime, timedelta, timezone
import math
import re

from flask import abort, jsonify, render_template, request
from login import get_current_identity
from hr_backend.hr_dashboard import hr_bp, db


def central_hr():
    identity = get_current_identity()
    if not identity.get('is_authenticated'):
        abort(401)
    if identity.get('role') not in {'hr', 'admin', 'executive'}:
        abort(403)
    return identity


def filters():
    query = {}
    manager = request.args.get('manager', '').strip()
    if manager:
        query.update({'actor.user_id': manager, 'actor.role': 'manager'})
    source = request.args.get('source', '')
    if source == 'manager':
        query['actor.role'] = 'manager'
    elif source == 'hr' and not manager:
        query['actor.role'] = {'$in': ['hr', 'admin', 'executive']}
    branch = request.args.get('branch', '').strip()
    if branch:
        query['branch'] = branch
    operation = request.args.get('operation', '')
    if operation in {'created', 'updated', 'deleted'}:
        query['operation'] = operation
    dates = {}
    for key, operator in (('from', '$gte'), ('to', '$lt')):
        value = request.args.get(key, '')
        if value:
            try:
                stamp = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                abort(400, description='Enter valid activity dates.')
            dates[operator] = stamp + timedelta(days=1) if key == 'to' else stamp
    if dates:
        if '$gte' in dates and '$lt' in dates and dates['$gte'] >= dates['$lt']:
            abort(400, description='The end date must be on or after the start date.')
        query['timestamp'] = dates
    search = request.args.get('search', '').strip()[:100]
    if search:
        query['$or'] = [{field: {'$regex': re.escape(search), '$options': 'i'}}
                        for field in ('record_label', 'action', 'collection', 'actor.name')]
    return query


def serialize(document):
    document = {**document, '_id': str(document['_id'])}
    if isinstance(document.get('timestamp'), datetime):
        stamp = document['timestamp']
        document['timestamp'] = (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).isoformat()
    return document


@hr_bp.get('/activity-logs')
def activity_logs():
    central_hr()
    query = filters()
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        abort(400, description='Invalid activity page.')
    total = db.hr_record_activity.count_documents(query)
    pages = max(1, math.ceil(total / 50))
    page = min(page, pages)
    rows = list(db.hr_record_activity.find(query).sort([('timestamp', -1), ('_id', -1)]).skip((page - 1) * 50).limit(50))
    managers = list(db.hr_record_activity.aggregate([
        {'$match': {'actor.role': 'manager'}}, {'$sort': {'timestamp': -1}},
        {'$group': {'_id': '$actor.user_id', 'name': {'$first': '$actor.name'}, 'branch': {'$first': '$actor.branch'}}},
        {'$sort': {'name': 1}},
    ]))
    # Include managers before their first action, as well as historical actors.
    known = {person['_id'] for person in managers}
    for user in db.users.find({'role': 'manager'}, {'name': 1, 'username': 1, 'branch': 1}):
        if str(user['_id']) not in known:
            managers.append({'_id': str(user['_id']), 'name': user.get('name') or user.get('username') or 'Manager', 'branch': user.get('branch', '')})
    managers.sort(key=lambda person: person['name'].casefold())
    branches = sorted(value for value in db.hr_record_activity.distinct('branch') if isinstance(value, str) and value)
    def page_url(number):
        from flask import url_for
        return url_for('hr.activity_logs', **{**request.args.to_dict(), 'page': number})
    context = dict(rows=rows, managers=managers, branches=branches, total=total, page=page, pages=pages,
                   filters=request.args, previous=page_url(max(1, page - 1)), next_page=page_url(min(pages, page + 1)),
                   active_page='activity_logs', hr_content_template='hr_pages/partials/hr_activity_logs_inner.html')
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render_template(context['hr_content_template'], **context)
    return render_template('hr_pages/hr_shell.html', **context)


@hr_bp.get('/activity-logs/context')
def activity_context():
    central_hr()
    path = request.args.get('path', '/hr/dashboard').split('?')[0]
    if path.startswith('/hr/employee/'):
        query = {'collection': {'$in': ['users', 'Exited']}, 'record_id': path.split('/')[3]}
    else:
        area = path.rstrip('/').split('/')[-1]
        collections = {
            'employees': ['users', 'Exited'], 'recruits': ['hr_recruits', 'users'],
            'attendance': ['users', 'attendance_workdays', 'hr_meeting_attendance', 'hr_manual_attendance'],
            'leave': ['users'], 'payroll': ['payrolls', 'payroll_records', 'payroll_deductions', 'users'],
            'staff-loans': ['staff_loans', 'staff_loan_manual_staff'], 'exits': ['hr_exits', 'Exited', 'hr_customer_handovers'],
            'reminders': ['hr_reminders'], 'cases': ['users', 'hr_cases', 'cases'], 'performance': ['users'],
        }.get(area)
        query = {'collection': {'$in': collections}} if collections else {}
        employee_fields = {
            'leave': r'^(leaves|leave_policy)(\.|$)',
            'performance': r'^(performance_records|star_rating|sales_level)(\.|$)',
            'cases': r'^cases(\.|$)',
            'payroll': r'^wages_tips(\.|$)',
            'attendance': r'^(attendance|leaves)(\.|$)',
        }.get(area)
        if employee_fields:
            query['$or'] = [{'collection': {'$ne': 'users'}}, {'fields': {'$regex': employee_fields}}]
    query['actor.role'] = 'manager'
    rows = db.hr_record_activity.find(query).sort('timestamp', -1).limit(10)
    return jsonify(ok=True, records=[serialize(doc) for doc in rows])


@hr_bp.record_once
def register_hr_audit_ui(state):
    @state.app.context_processor
    def hr_audit_context():
        identity = get_current_identity()
        return {'hr_audit_central': bool(identity.get('is_authenticated') and identity.get('role') in {'hr', 'admin', 'executive'})}

    try:
        db.hr_record_activity.create_index([('actor.user_id', 1), ('timestamp', -1)])
        db.hr_record_activity.create_index([('actor.role', 1), ('timestamp', -1)])
        db.hr_record_activity.create_index([('collection', 1), ('record_id', 1), ('timestamp', -1)])
        db.hr_record_activity.create_index([('timestamp', -1)])
    except Exception:
        state.app.logger.exception('Unable to create HR activity indexes')
