"""Server-owned attribution for Customer Support work and its change history."""
from copy import deepcopy
from datetime import datetime, timezone

from bson import ObjectId
from flask import current_app, g, has_request_context, request

ATTRIBUTION_FIELDS = {'support_recorded_by', 'support_last_updated_by'}
AUDIT_COLLECTION = 'support_record_activity'
INTERNAL_COLLECTIONS = {AUDIT_COLLECTION, 'activity_logs', 'customer_support_notification_state'}
PROJECTION = {key: 1 for key in ('_id', 'title', 'subject', 'name', 'customer_name', 'customer_id',
    'branch', 'branch_name', 'suite_branch', 'task_no', 'call_no', 'ticket_no', 'kind')}


def actor(database):
    if (not has_request_context() or request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}
            or not request.path.startswith('/api/customer-support/')):
        return None
    from login import get_current_identity
    identity = get_current_identity()
    if not identity.get('is_authenticated') or identity.get('role') not in {'manager', 'customer_support', 'admin', 'executive'}:
        return None
    if not hasattr(g, '_support_audit_actor'):
        key = str(identity.get('user_id') or '')
        user = database.users.find_one({'_id': ObjectId(key) if ObjectId.is_valid(key) else key}) or {}
        g._support_audit_actor = {'user_id': key,
            'name': user.get('name') or identity.get('name') or user.get('username') or 'Staff',
            'role': identity['role'], 'branch': str(user.get('branch') or '')}
    return {**g._support_audit_actor, 'at': datetime.now(timezone.utc).isoformat()}


def attribution(document):
    return {key: document[key] for key in ATTRIBUTION_FIELDS if isinstance(document.get(key), dict)}


def prepare(database, name, value, insert=False, query=None):
    who = actor(database)
    if not who or name in INTERNAL_COLLECTIONS:
        return value
    value = deepcopy(value)
    if insert or not any(key.startswith('$') for key in value):
        original = None
        if value.get('_id'):
            original = database[name].find_one({'_id': value['_id']}, {'support_recorded_by': 1})
            if not original and name in {'customers', 'Archived_customers'}:
                other = 'customers' if name == 'Archived_customers' else 'Archived_customers'
                original = database[other].find_one({'_id': value['_id']}, {'support_recorded_by': 1})
        value['support_recorded_by'] = (original or {}).get('support_recorded_by') or who
        value['support_last_updated_by'] = who
    else:
        for operator, fields in value.items():
            if not isinstance(fields, dict):
                continue
            for key in list(fields):
                if key.split('.')[0] in ATTRIBUTION_FIELDS or (operator == '$rename' and str(fields[key]).split('.')[0] in ATTRIBUTION_FIELDS):
                    fields.pop(key)
        value.setdefault('$set', {})['support_last_updated_by'] = who
        value.setdefault('$setOnInsert', {})['support_recorded_by'] = who
        # A device creates a call stub before any staff member logs it. The
        # first authenticated officer claiming that stub is its human recorder.
        if name == 'customer_support_calls' and value['$set'].get('officer_id') == who['user_id'] and query:
            existing = database[name].find_one(query, {'officer_id': 1, 'support_recorded_by': 1})
            if existing and not existing.get('officer_id') and not existing.get('support_recorded_by'):
                value['$set']['support_recorded_by'] = who
                value['$setOnInsert'].pop('support_recorded_by', None)
        for operator in ('$push', '$addToSet'):
            for item in value.get(operator, {}).values():
                documents = item.get('$each', []) if isinstance(item, dict) and '$each' in item else [item]
                for document in documents:
                    if isinstance(document, dict):
                        document['support_recorded_by'] = who
                        document['support_last_updated_by'] = who
        for key in ('latest_review', 'repeat_purchase'):
            document = value.get('$set', {}).get(key)
            if isinstance(document, dict):
                document['support_recorded_by'] = who
                document['support_last_updated_by'] = who
    return value


def record(database, name, operation, documents, update=None, session=None):
    who = actor(database)
    if not who or not documents or name in INTERNAL_COLLECTIONS:
        return
    fields = sorted({key for op, values in (update or {}).items()
        for key in (values if op.startswith('$') and isinstance(values, dict) else [op])
        if key.split('.')[0] not in ATTRIBUTION_FIELDS | {'suite_branch'}})
    action = (request.endpoint or '').split('.')[-1].replace('_', ' ')
    entries = []
    for document in documents:
        collection_type = {'customer_support_calls': 'calls', 'tasks': 'tasks', 'complaints': 'tickets',
            'customers': 'customers', 'Archived_customers': 'customers'}.get(name, 'other')
        reference = document.get('call_no') or document.get('task_no') or document.get('ticket_no') or ''
        # Names and titles identify the work; request bodies, call notes,
        # recordings, customer contact details and document URLs are excluded.
        entries.append({'actor': who, 'manager_id': who['user_id'] if who['role'] == 'manager' else None,
            'branch': document.get('branch') or document.get('branch_name') or document.get('suite_branch') or who['branch'],
            'collection': name, 'record_type': collection_type, 'record_id': str(document.get('_id') or ''),
            'record_label': str(document.get('title') or document.get('subject') or document.get('customer_name') or document.get('name') or name.replace('_', ' '))[:200],
            'reference': str(reference), 'customer_id': str(document.get('customer_id') or ''),
            'operation': operation, 'action': action, 'fields': fields, 'path': request.path,
            'timestamp': datetime.now(timezone.utc)})
    try:
        database[AUDIT_COLLECTION].insert_many(entries, **({'session': session} if session is not None else {}))
    except Exception:
        current_app.logger.exception('Customer Support record activity could not be persisted')
