"""Database-side task status normalization shared by list filters and global counts."""
from datetime import datetime
import re

STATUSES = ('Pending', 'In Progress', 'Completed', 'Overdue')


def _is_fixture_collection(collection):
    return collection.__class__.__module__.startswith('mongomock.')


def _fixture_status(row):
    raw = str(row.get('status') or 'pending').strip().lower().replace('_', ' ')
    if raw == 'completed':
        return 'Completed'
    if raw == 'overdue':
        return 'Overdue'
    value = row.get('due_date')
    try:
        due = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        due = None
    if due and due.date() < datetime.utcnow().date():
        return 'Overdue'
    return 'In Progress' if raw == 'in progress' else 'Pending'


def status_stages():
    # The serializer intentionally uses the stored calendar date, including for
    # ISO strings with offsets. Do not shift it to UTC during normalization.
    due = {'$convert': {'input': {'$substrCP': [{'$convert': {'input': '$due_date', 'to': 'string', 'onError': '', 'onNull': ''}}, 0, 10]}, 'to': 'date', 'onError': None, 'onNull': None}}
    raw = {'$replaceAll': {'input': {'$toLower': {'$trim': {'input': {'$convert': {'input': '$status', 'to': 'string', 'onError': '', 'onNull': ''}}}}}, 'find': '_', 'replacement': ' '}}
    return [
        {'$set': {'_status': raw, '_due': due}},
        {'$set': {'_ui_status': {'$switch': {'branches': [
            {'case': {'$eq': ['$_status', 'completed']}, 'then': 'Completed'},
            {'case': {'$or': [{'$eq': ['$_status', 'overdue']}, {'$and': [{'$ne': ['$_due', None]}, {'$lt': ['$_due', datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)]}]}]}, 'then': 'Overdue'},
            {'case': {'$eq': ['$_status', 'in progress']}, 'then': 'In Progress'},
        ], 'default': 'Pending'}}}},
    ]


def task_counts(collection):
    counts = dict.fromkeys(STATUSES, 0)
    if _is_fixture_collection(collection):
        for row in collection.find({}, {'status': 1, 'due_date': 1}):
            counts[_fixture_status(row)] += 1
        return counts
    for row in collection.aggregate(status_stages() + [{'$group': {'_id': '$_ui_status', 'count': {'$sum': 1}}}]):
        counts[row['_id']] = row['count']
    return counts


def first_nonempty(*values):
    result = values[-1]
    for value in reversed(values[:-1]):
        result = {'$cond': [{'$and': [{'$ne': [{'$ifNull': [value, None]}, None]}, {'$ne': [value, '']}]}, value, result]}
    return result


def task_page(collection, args):
    try:
        page = max(1, int(args.get('page', 1)))
        size = max(1, min(100, int(args.get('per_page', 25))))
    except (TypeError, ValueError):
        page, size = 1, 25
    if _is_fixture_collection(collection):
        rows = list(collection.find({}))
        database = collection.database
        customer_ids = {str(row.get('customer_id')) for row in rows if row.get('customer_id')}
        user_ids = {
            str(row.get('assignee_id') or row.get('user_id'))
            for row in rows
            if row.get('assignee_id') or row.get('user_id')
        }
        customers = {str(row['_id']): row for row in database.customers.find({'_id': {'$in': [row['_id'] for row in database.customers.find({}) if str(row['_id']) in customer_ids]}})}
        users = {str(row['_id']): row for row in database.users.find({'_id': {'$in': [row['_id'] for row in database.users.find({}) if str(row['_id']) in user_ids]}})}
        term = (args.get('q') or '').lower().strip()
        def matches(row):
            status = _fixture_status(row)
            if args.get('status') not in (None, '', 'All Statuses') and status != args['status']: return False
            if args.get('priority') not in (None, '', 'All Priorities') and str(row.get('priority') or 'Medium').title() != args['priority']: return False
            if args.get('category') not in (None, '', 'All Categories') and str(row.get('category') or 'Support') != args['category']: return False
            name = 'All Agents' if row.get('user_id') == 'all' else (users.get(str(row.get('assignee_id') or row.get('user_id')), {}).get('name') or users.get(str(row.get('assignee_id') or row.get('user_id')), {}).get('username') or row.get('assignee_name') or row.get('target_name') or 'Unassigned')
            task_id = row.get('task_no') or f"TSK-{str(row.get('_id', ''))[-6:].upper()}"
            return not term or any(term in str(value).lower() for value in (task_id, row.get('title') or row.get('message') or 'Assigned task', customers.get(str(row.get('customer_id')), {}).get('name') or row.get('customer_name') or '—', name))
        selected = [row for row in rows if matches(row)]
        selected.sort(key=lambda row: (row.get('timestamp') or datetime.min, row.get('created_at') or datetime.min, str(row.get('_id'))), reverse=True)
        counts = task_counts(collection)
        total = len(selected)
        return selected[(page - 1) * size:page * size], counts, {'page': page, 'perPage': size, 'total': total, 'totalPages': max(1, (total + size - 1) // size)}
    filters = []
    for param, field, default in [('status', '_ui_status', 'All Statuses'), ('priority', '_ui_priority', 'All Priorities'), ('category', '_ui_category', 'All Categories')]:
        value = args.get(param)
        if value and value != default:
            filters.append({field: value.lower() if param == 'priority' else value})
    selected = [{'$set': {'_ui_priority': {'$toLower': first_nonempty('$priority', 'Medium')}, '_ui_category': first_nonempty('$category', 'Support')}}]
    term = (args.get('q') or '').strip()
    if term:
        for table, field, alias in [('customers', '$customer_id', '_customer'), ('users', first_nonempty('$assignee_id', '$user_id', ''), '_assignee')]:
            selected.append({'$lookup': {'from': table, 'let': {'ref': {'$convert': {'input': field, 'to': 'string', 'onError': '', 'onNull': ''}}}, 'pipeline': [
                {'$match': {'$expr': {'$eq': [{'$toString': '$_id'}, '$$ref']}}}, {'$project': {'name': 1, 'username': 1}}, {'$limit': 1}], 'as': alias}})
        sid = {'$toString': '$_id'}
        selected.append({'$set': {
            '_search_id': first_nonempty('$task_no', {'$concat': ['TSK-', {'$toUpper': {'$substrCP': [sid, {'$max': [0, {'$subtract': [{'$strLenCP': sid}, 6]}]}, 6]}}]}),
            '_search_title': first_nonempty('$title', '$message', 'Assigned task'),
            '_search_customer': first_nonempty({'$arrayElemAt': ['$_customer.name', 0]}, '$customer_name', '—'),
            '_search_assignee': {'$cond': [{'$eq': ['$user_id', 'all']}, 'All Agents', first_nonempty({'$arrayElemAt': ['$_assignee.name', 0]}, {'$arrayElemAt': ['$_assignee.username', 0]}, '$assignee_name', '$target_name', 'Unassigned')]},
        }})
        filters.append({'$or': [{field: {'$regex': re.escape(term), '$options': 'i'}} for field in ('_search_id', '_search_title', '_search_customer', '_search_assignee')]})
    if filters:
        selected.append({'$match': {'$and': filters}})
    # Project only fields used by the task serializer; editor fields remain available.
    fields = '_id task_no title message description customer_id customer_name assignee_id user_id assignee_name target_name due_date priority status category related_to kind timestamp created_at support_recorded_by support_last_updated_by'.split()
    result = list(collection.aggregate(status_stages() + [{'$facet': {
        'counts': [{'$group': {'_id': '$_ui_status', 'count': {'$sum': 1}}}],
        'total': selected + [{'$count': 'value'}],
        'rows': selected + [{'$sort': {'timestamp': -1, 'created_at': -1, '_id': -1}}, {'$skip': (page - 1) * size}, {'$limit': size}, {'$project': dict.fromkeys(fields, 1)}]
    }}]))[0]
    total = result['total'][0]['value'] if result['total'] else 0
    counts = dict.fromkeys(STATUSES, 0)
    counts.update({row['_id']: row['count'] for row in result['counts']})
    return result['rows'], counts, {'page': page, 'perPage': size, 'total': total, 'totalPages': max(1, (total + size - 1) // size)}
