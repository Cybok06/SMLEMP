"""Server-owned HR attribution and durable, payload-free change history."""
from copy import deepcopy
from datetime import datetime, timezone

from bson import ObjectId
from flask import g, has_request_context, request, current_app

ATTRIBUTION_FIELDS = {'hr_recorded_by', 'hr_last_updated_by'}
# Other login roles currently grant organisation-wide data access. Allowing a
# manager to create those credentials would bypass the suite's branch boundary.
BRANCH_STAFF_ROLES = {'agent'}
AUDIT_COLLECTION = 'hr_record_activity'
INTERNAL_COLLECTIONS = {AUDIT_COLLECTION, 'hr_audit_logs', 'payroll_audit_logs', 'activity_logs'}


def attribution(document):
    return {key: document[key] for key in ATTRIBUTION_FIELDS if isinstance(document.get(key), dict)}


def is_hr_request():
    if not has_request_context() or request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return False
    return (any(request.path == path or request.path.startswith(path + '/')
                for path in ('/hr', '/api/hr', '/api/recruitment'))
            or (request.path.startswith('/api/staff-loans')
                and request.headers.get('X-Manager-Suite') == 'hr'))


def actor(database):
    if not is_hr_request():
        return None
    from login import get_current_identity
    identity = get_current_identity()
    if not identity.get('is_authenticated') or identity.get('role') not in {'manager', 'hr', 'admin', 'executive'}:
        return None
    if not hasattr(g, '_hr_audit_actor'):
        key = str(identity.get('user_id') or '')
        user = database.users.find_one({'_id': ObjectId(key) if ObjectId.is_valid(key) else key}) or {}
        g._hr_audit_actor = {
            'user_id': key, 'name': user.get('name') or identity.get('name') or user.get('username') or 'Staff',
            'role': identity['role'], 'branch': str(user.get('branch') or ''),
        }
    return {**g._hr_audit_actor, 'at': datetime.now(timezone.utc).isoformat()}


def prepare(database, name, value, insert=False):
    who = actor(database)
    if not who or name in INTERNAL_COLLECTIONS:
        return value
    value = deepcopy(value)
    replacement = insert or not any(key.startswith('$') for key in value)
    if replacement:
        # Restore/exit copies retain their original provenance from the stored
        # employee, rather than trusting metadata sent by the client.
        original = None
        if name in {'users', 'Exited'} and value.get('_id'):
            original = (database.users.find_one({'_id': value['_id']}, {'hr_recorded_by': 1})
                        or database.Exited.find_one({'_id': value['_id']}, {'hr_recorded_by': 1}))
        for field in ATTRIBUTION_FIELDS:
            value.pop(field, None)
        value['hr_recorded_by'] = (original or {}).get('hr_recorded_by') or who
        value['hr_last_updated_by'] = who
    else:
        # Metadata cannot be forged, removed, or redirected through operators.
        for operator, fields in value.items():
            if isinstance(fields, dict):
                for key in list(fields):
                    if key.split('.')[0] in ATTRIBUTION_FIELDS:
                        fields.pop(key)
        value.setdefault('$set', {})['hr_last_updated_by'] = who
        value.setdefault('$setOnInsert', {})['hr_recorded_by'] = who
        # Embedded HR records (leave, cases, wages, files, ratings, attendance)
        # must retain attribution independently of their parent employee.
        for operator in ('$push', '$addToSet'):
            for key, item in value.get(operator, {}).items():
                items = item.get('$each', []) if isinstance(item, dict) and '$each' in item else [item]
                for document in items:
                    if isinstance(document, dict):
                        document['hr_recorded_by'] = who
                        document['hr_last_updated_by'] = who
        for key, item in list(value.get('$set', {}).items()):
            if isinstance(item, dict) and key not in ATTRIBUTION_FIELDS and '.' in key:
                item['hr_last_updated_by'] = who
            parts = key.split('.')
            if len(parts) > 2 and (parts[1] == '$' or parts[1].isdigit() or parts[1].startswith('$[')):
                value['$set']['.'.join(parts[:2]) + '.hr_last_updated_by'] = who
    return value


PROJECTION = {'_id': 1, 'name': 1, 'full_name': 1, 'title': 1, 'employee_id': 1,
              'employee_name': 1, 'user_id': 1, 'branch': 1, 'recruiting_branch': 1,
              'suite_branch': 1, 'hr_recorded_by': 1}


def record(database, name, operation, documents, update=None, session=None):
    who = actor(database)
    if not who or not documents or name in INTERNAL_COLLECTIONS:
        return
    # Store field names and record labels, never passwords, pay values, personal
    # documents, uploaded content, interview notes, or request bodies.
    fields = sorted({key for op, values in (update or {}).items()
                     for key in (values if op.startswith('$') and isinstance(values, dict) else [op])
                     if key.split('.')[0] not in ATTRIBUTION_FIELDS | {'suite_branch'}})
    endpoint = (request.endpoint or '').split('.')[-1].replace('_', ' ')
    timestamp = datetime.now(timezone.utc)
    entries = [{
        'actor': who, 'manager_id': who['user_id'] if who['role'] == 'manager' else None,
        'branch': doc.get('branch') or doc.get('recruiting_branch') or doc.get('suite_branch') or who['branch'],
        'collection': name, 'record_id': str(doc.get('_id') or ''),
        'record_label': str(doc.get('full_name') or doc.get('name') or doc.get('title')
                            or doc.get('employee_name') or doc.get('employee_id') or name.replace('_', ' '))[:200],
        'operation': operation, 'action': endpoint, 'fields': fields,
        'path': request.path, 'timestamp': timestamp,
    } for doc in documents]
    try:
        database[AUDIT_COLLECTION].insert_many(entries, **({'session': session} if session is not None else {}))
    except Exception:
        current_app.logger.exception('HR record activity could not be persisted')
