"""Read-only oversight for the central Customer Support team."""
from datetime import datetime, timedelta, timezone
import re

from flask import Blueprint, abort, jsonify, request
from db import db
from login import get_current_identity

support_activity_bp = Blueprint('support_activity', __name__, url_prefix='/api/customer-support')


def central_support():
    identity = get_current_identity()
    if not identity.get('is_authenticated'):
        abort(401)
    if identity.get('role') not in {'customer_support', 'admin', 'executive'}:
        abort(403)


@support_activity_bp.get('/activity-logs')
def activity_logs():
    central_support()
    query = {}
    manager = request.args.get('manager', '').strip()
    if manager:
        query.update({'actor.user_id': manager, 'actor.role': 'manager'})
    source = request.args.get('source', '')
    if source == 'manager':
        query['actor.role'] = 'manager'
    elif source == 'support':
        if manager:
            query['_id'] = {'$exists': False}
        else:
            query['actor.role'] = {'$in': ['customer_support', 'admin', 'executive']}
    for argument, field, choices in (
        ('operation', 'operation', {'created', 'updated', 'deleted'}),
        ('type', 'record_type', {'calls', 'tasks', 'tickets', 'customers', 'other'}),
    ):
        value = request.args.get(argument, '')
        if value:
            if value not in choices:
                return jsonify(ok=False, message='Invalid activity filter.'), 400
            query[field] = value
    if request.args.get('branch'):
        query['branch'] = request.args['branch']
    dates = {}
    for argument, operator in (('from', '$gte'), ('to', '$lt')):
        value = request.args.get(argument)
        if value:
            try:
                stamp = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                return jsonify(ok=False, message='Enter valid activity dates.'), 400
            dates[operator] = stamp + timedelta(days=1) if argument == 'to' else stamp
    if dates:
        if '$gte' in dates and '$lt' in dates and dates['$gte'] >= dates['$lt']:
            return jsonify(ok=False, message='The end date must be on or after the start date.'), 400
        query['timestamp'] = dates
    search = request.args.get('search', '').strip()[:100]
    if search:
        query['$or'] = [{field: {'$regex': re.escape(search), '$options': 'i'}}
            for field in ('record_label', 'reference', 'action', 'actor.name')]
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        return jsonify(ok=False, message='Invalid activity page.'), 400
    total = db.support_record_activity.count_documents(query)
    pages = max(1, (total + 49) // 50)
    page = min(page, pages)
    rows = []
    for document in db.support_record_activity.find(query).sort([('timestamp', -1), ('_id', -1)]).skip((page - 1) * 50).limit(50):
        stamp = document['timestamp']
        rows.append({**document, '_id': str(document['_id']),
            'timestamp': (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).isoformat()})
    managers = list(db.support_record_activity.aggregate([
        {'$match': {'actor.role': 'manager'}}, {'$sort': {'timestamp': -1}},
        {'$group': {'_id': '$actor.user_id', 'name': {'$first': '$actor.name'}, 'branch': {'$first': '$actor.branch'}}},
    ]))
    known = {person['_id'] for person in managers}
    for user in db.users.find({'role': 'manager'}, {'name': 1, 'username': 1, 'branch': 1}):
        if str(user['_id']) not in known:
            managers.append({'_id': str(user['_id']), 'name': user.get('name') or user.get('username') or 'Manager', 'branch': user.get('branch', '')})
    managers.sort(key=lambda person: person['name'].casefold())
    branches = sorted(value for value in db.support_record_activity.distinct('branch') if isinstance(value, str) and value)
    return jsonify(ok=True, records=rows, managers=managers, branches=branches,
                   pagination={'page': page, 'pages': pages, 'total': total})


@support_activity_bp.record_once
def indexes(state):
    try:
        for keys in ([('timestamp', -1)], [('actor.user_id', 1), ('timestamp', -1)],
                     [('actor.role', 1), ('timestamp', -1)], [('branch', 1), ('timestamp', -1)],
                     [('record_type', 1), ('timestamp', -1)]):
            db.support_record_activity.create_index(keys)
    except Exception:
        state.app.logger.exception('Unable to create Customer Support activity indexes')
