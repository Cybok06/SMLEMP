"""Branch-bound database access for managers using the HR/Support suite.

The adapter is inactive for other roles and outside suite routes. Existing query
filters are intersected with ownership; callers cannot replace the branch filter.
"""
from copy import deepcopy
import re

from bson import ObjectId
from flask import abort, g, has_request_context, request


SUITE_PATHS = ('/hr', '/api/hr', '/customer-support/app', '/api/customer-support')
CONFIG = {'hr_roles', 'hr_leave_settings', 'hr_critical_days', 'payroll_settings'}
CUSTOMER_RECORDS = {'payments', 'packages', 'card_closures', 'inventory_products_outflow',
                    'complaints', 'customer_support_followups', 'customer_support_documents',
                    'customer_support_messages', 'customer_support_manual_activities',
                    'customer_support_customer_detail_edits', 'customer_support_calls', 'customer_archive_logs'}
EMPLOYEE_RECORDS = {'hr_cases', 'cases', 'hr_exits', 'assets', 'hr_debts', 'staff_loans',
                    'attendance_workdays', 'hr_meeting_attendance', 'hr_manual_attendance',
                    'payrolls', 'payroll_records', 'payroll_deductions', 'customer_support_targets',
                    'customer_support_field_visits', 'customer_support_notification_state'}
BRANCH_RECORDS = {'hr_recruits', 'hr_reminders', 'hr_audit_logs', 'payroll_audit_logs',
                  'staff_loan_manual_staff', 'images', 'hr_customer_handovers'}
USER_REFERENCES = ('employee_id', 'employee.user_id', 'user_id', 'agent_id', 'manager_id',
                   'officer_id', 'assigned_to_id', 'assigned_to_id_str', 'assignee_id',
                   'follow_up_agent_id', 'repeat_purchase.agent_id', 'to_user_id', 'from_user_id')
CUSTOMER_REFERENCES = ('customer_id',)
BRANCH_FIELDS = ('branch', 'branch_name', 'agent_branch', 'manager_branch',
                 'recruiting_branch', 'assigned_branch', 'employee.branch')


def _values(ids):
    result = set()
    for value in ids:
        result.add(value)
        result.add(str(value))
        if ObjectId.is_valid(str(value)):
            result.add(ObjectId(str(value)))
    return list(result)


def _and(left, right):
    return {'$and': [left, right]} if left and right else left or right or {}


def _get(document, path):
    if path in document:
        return document[path]
    value = document
    for part in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class BranchScope:
    def __init__(self, database, identity):
        self.database = database
        self.user_id = str(identity.get('user_id') or '')
        key = ObjectId(self.user_id) if ObjectId.is_valid(self.user_id) else self.user_id
        manager = database.users.find_one({'_id': key, 'role': 'manager'}) or {}
        self.branch = str(manager.get('branch') or '').strip()
        if not self.branch:
            abort(403, description='Your manager account must have a branch before opening the suite.')
        self.exact_branch = {'$regex': r'^\s*' + re.escape(self.branch) + r'\s*$', '$options': 'i'}
        self._ids = None

    def ids(self):
        if self._ids is None:
            managers = list(self.database.users.find({'role': 'manager'}, {'_id': 1, 'branch': 1}))
            managers += list(self.database.Exited.find({'role': 'manager'}, {'_id': 1, 'branch': 1}))
            manager_ids = _values(row['_id'] for row in managers if str(row.get('branch') or '').strip().casefold() == self.branch.casefold())
            all_manager_ids = _values(row['_id'] for row in managers)
            # Agents inherit their current manager's branch. Their own stored
            # label may still describe the branch they transferred out of.
            employee_query = {'$or': [
                {'role': {'$ne': 'agent'}, 'branch': self.exact_branch},
                {'role': 'agent', 'manager_id': {'$in': manager_ids}},
                {'role': 'agent', 'manager_id': {'$nin': all_manager_ids}, 'branch': self.exact_branch},
            ]}
            employees = list(self.database.users.find(employee_query, {'_id': 1, 'role': 1}))
            former = list(self.database.Exited.find(employee_query, {'_id': 1, 'role': 1}))
            live_former_ids = set(self.database.users.distinct('_id', {'_id': {'$in': [row['_id'] for row in former]}}))
            former = [row for row in former if row['_id'] not in live_former_ids]
            manual = list(self.database.staff_loan_manual_staff.find(
                {'$or': [{'branch': self.exact_branch}, {'suite_branch': self.branch}]}, {'_id': 1}))
            users = _values(row['_id'] for row in employees + former + manual)
            agents = _values(row['_id'] for row in employees + former if row.get('role') == 'agent')
            customers = list(self.database.customers.distinct('_id', {'agent_id': {'$in': agents}}))
            archived = list(self.database.Archived_customers.distinct('_id', {'agent_id': {'$in': agents}}))
            live_archived_ids = set(self.database.customers.distinct('_id', {'_id': {'$in': archived}}))
            customers.extend(value for value in archived if value not in live_archived_ids)
            self._ids = {'users': users, 'agents': agents, 'customers': _values(customers)}
        return self._ids

    def branch_query(self):
        return {'$or': [{'suite_branch': self.branch}] + [{field: self.exact_branch} for field in BRANCH_FIELDS]}

    def related_query(self, fields, values, fallback=None):
        # A historical branch label must not override a transferred customer's
        # current ownership. Fallback applies only to records with no reference.
        choices = []
        for index, field in enumerate(fields):
            previous_empty = {'$and': [{earlier: {'$in': [None, '']}} for earlier in fields[:index]]} if index else {}
            choices.append(_and(previous_empty, {field: {'$in': values}}))
        if fallback:
            empty = {'$and': [{field: {'$in': [None, '']}} for field in fields]}
            choices.append(_and(empty, fallback))
        return {'$or': choices}

    def query(self, name, writing=False):
        ids = self.ids()
        if name in {'users', 'Exited'}:
            return {'_id': {'$in': ids['users']}}
        if name in {'customers', 'Archived_customers'}:
            return {'agent_id': {'$in': ids['agents']}}
        if name in CONFIG:
            if writing:
                return self.branch_query()
            return {'$or': [self.branch_query(), {'branch': {'$in': [None, '']}}]}
        if name == 'work_time_settings' and not writing:
            return {'_id': 'organization'}
        if name in CUSTOMER_RECORDS:
            fallback = self.related_query(('agent_id', 'officer_id', 'created_by', 'assigned_to_id'), ids['users'], self.branch_query())
            return self.related_query(CUSTOMER_REFERENCES, ids['customers'], fallback)
        if name == 'tasks':
            fallback = self.related_query(('assignee_id', 'agent_id', 'created_by'), ids['users'], self.branch_query())
            return self.related_query(CUSTOMER_REFERENCES, ids['customers'], fallback)
        if name in EMPLOYEE_RECORDS:
            return self.related_query(USER_REFERENCES, ids['users'], self.branch_query())
        if name in BRANCH_RECORDS:
            if name == 'hr_customer_handovers':
                return self.related_query(CUSTOMER_REFERENCES, ids['customers'], self.branch_query())
            return self.branch_query()
        if name == 'products':
            return {'$or': [{'manager_id': {'$in': ids['users']}}, {'manager_id': {'$in': [None, '']}}]}
        # New collections require an explicit ownership policy before exposure.
        return {'_id': {'$exists': False}}

    def validate(self, name, document):
        if name not in CONFIG | CUSTOMER_RECORDS | EMPLOYEE_RECORDS | BRANCH_RECORDS | {'users', 'Exited', 'customers', 'Archived_customers', 'tasks'}:
            abort(403, description='This collection is not available in the manager suite.')
        if name == 'hr_roles':
            abort(403, description='Organisation-wide roles must be maintained by central HR.')
        for field in BRANCH_FIELDS + ('suite_branch',):
            value = _get(document, field)
            if value and str(value).strip().casefold() != self.branch.casefold():
                abort(403, description='You can only change records within your own branch.')
        ids = self.ids()
        for field in USER_REFERENCES:
            value = _get(document, field)
            if value not in (None, '') and value not in ids['users'] and str(value) not in ids['users']:
                abort(403, description='The selected employee belongs to another branch.')
        for field in CUSTOMER_REFERENCES:
            value = _get(document, field)
            if value not in (None, '') and value not in ids['customers'] and str(value) not in ids['customers']:
                abort(403, description='The selected customer belongs to another branch.')
        if name == 'users':
            for field in ('main_admin', 'account_group_id', 'primary_user_id', 'is_role_profile'):
                if field in document:
                    abort(403, description='Account privileges must be maintained by an administrator.')

    def pipeline(self, pipeline):
        rewritten = deepcopy(pipeline)
        for stage in rewritten:
            if any(key in stage for key in ('$out', '$merge', '$unionWith', '$graphLookup')):
                abort(403, description='This aggregation is not available in the manager suite.')
            if '$lookup' in stage:
                lookup = stage['$lookup']
                lookup['pipeline'] = [{'$match': self.query(lookup['from'])}] + self.pipeline(lookup.get('pipeline', []))
            if '$facet' in stage:
                stage['$facet'] = {key: self.pipeline(value) for key, value in stage['$facet'].items()}
        return rewritten


def manager_scope(database):
    if not has_request_context():
        return None
    loan_suite = request.headers.get('X-Manager-Suite') == 'hr' and (request.path == '/api/staff-loans' or request.path.startswith('/api/staff-loans/'))
    if not loan_suite and request.path != '/attendance/api/analytics' and not any(request.path == prefix or request.path.startswith(prefix + '/') for prefix in SUITE_PATHS):
        return None
    from login import get_current_identity
    identity = get_current_identity()
    if identity.get('role') != 'manager' or not identity.get('is_authenticated'):
        return None
    raw = database.raw if isinstance(database, SuiteDatabase) else database
    cache = getattr(g, '_manager_suite_scopes', {})
    key = id(raw)
    if key not in cache:
        cache[key] = BranchScope(raw, identity)
        g._manager_suite_scopes = cache
    return cache[key]


class SuiteCollection:
    def __init__(self, database, collection):
        self.database = database
        self.raw = collection
        self.name = collection.name

    def _query(self, query=None, writing=False):
        scope = manager_scope(self.database)
        query = query if isinstance(query, dict) else {'_id': query} if query is not None else {}
        return _and(query, scope.query(self.name, writing)) if scope else query

    def find(self, filter=None, *args, **kwargs):
        return self.raw.find(self._query(filter), *args, **kwargs)

    def find_one(self, filter=None, *args, **kwargs):
        return self.raw.find_one(self._query(filter), *args, **kwargs)

    def count_documents(self, filter, *args, **kwargs):
        return self.raw.count_documents(self._query(filter), *args, **kwargs)

    def estimated_document_count(self, **kwargs):
        return self.count_documents({}, **kwargs)

    def distinct(self, key, filter=None, *args, **kwargs):
        return self.raw.distinct(key, self._query(filter), *args, **kwargs)

    def aggregate(self, pipeline, *args, **kwargs):
        scope = manager_scope(self.database)
        if scope:
            pipeline = [{'$match': scope.query(self.name)}] + scope.pipeline(pipeline)
        return self.raw.aggregate(pipeline, *args, **kwargs)

    def _prepare(self, value, insert=False, query=None):
        from services.suite_record_audit import prepare
        value = prepare(self.database.raw, self.name, value, insert=insert, query=query) if isinstance(value, dict) else value
        scope = manager_scope(self.database)
        if not scope:
            return value
        if insert and self.name in CONFIG:
            abort(403, description='Organisation-wide settings must be maintained by central HR.')
        if not isinstance(value, dict):
            abort(403, description='Pipeline updates are not available in the manager suite.')
        value = deepcopy(value)
        updates = [value] if insert or not any(key.startswith('$') for key in value) else [fields for op, fields in value.items() if op in {'$set', '$setOnInsert', '$inc'}]
        if '$rename' in value:
            abort(403)
        protected = set(USER_REFERENCES + CUSTOMER_REFERENCES + BRANCH_FIELDS + ('suite_branch',))
        if self.name == 'users':
            protected.update({'role', 'main_admin', 'account_group_id', 'primary_user_id', 'is_role_profile',
                              'password', 'username', 'email', 'phone', 'phone_number'})
        # Ownership and privileges may only be assigned explicitly, so operators
        # cannot remove a reference or modify a protected field indirectly.
        for operator, fields in value.items():
            if operator.startswith('$') and operator not in {'$set', '$setOnInsert'}:
                if any(field == key or field.startswith(key + '.') or key.startswith(field + '.')
                       for field in fields for key in protected):
                    abort(403, description='Ownership and account fields require an explicit valid value.')
        for fields in updates:
            scope.validate(self.name, fields)
            if self.name == 'users':
                current = list(self.raw.find(self._query(query), {'role': 1})) if not insert else []
                if 'role' in fields and (not current or any(row.get('role') != fields['role'] for row in current)):
                    abort(403, description='Changing account roles requires an administrator.')
                credentials = {'password', 'username', 'email', 'phone', 'phone_number'} & fields.keys()
                if credentials and any(row.get('role') != 'agent' and str(row['_id']) != scope.user_id for row in current):
                    abort(403, description='Privileged account credentials require an administrator.')
        if insert:
            value['suite_branch'] = scope.branch
        elif '$set' in value or any(key.startswith('$') for key in value):
            value.setdefault('$set', {})['suite_branch'] = scope.branch
        else:
            value['suite_branch'] = scope.branch
        return value

    def _invalidate(self):
        scope = manager_scope(self.database)
        if scope and self.name in {'users', 'Exited', 'customers', 'Archived_customers', 'staff_loan_manual_staff'}:
            scope._ids = None

    def insert_one(self, document, *args, **kwargs):
        from services.suite_record_audit import record, ATTRIBUTION_FIELDS
        prepared = self._prepare(document, insert=True)
        result = self.raw.insert_one(prepared, *args, **kwargs)
        document.update({field: prepared[field] for field in ATTRIBUTION_FIELDS if field in prepared})
        record(self.database.raw, self.name, 'created', [{**prepared, '_id': result.inserted_id}], session=kwargs.get('session'))
        self._invalidate()
        return result

    def insert_many(self, documents, *args, **kwargs):
        from services.suite_record_audit import record
        prepared = [self._prepare(doc, insert=True) for doc in documents]
        result = self.raw.insert_many(prepared, *args, **kwargs)
        record(self.database.raw, self.name, 'created', [{**doc, '_id': key} for doc, key in zip(prepared, result.inserted_ids)], session=kwargs.get('session'))
        self._invalidate()
        return result

    def _write(self, method, filter, update=None, *args, **kwargs):
        from services.suite_record_audit import actor, record, PROJECTION
        scope = manager_scope(self.database)
        if scope and self.name == 'hr_roles':
            abort(403)
        prepared = [] if update is None else [self._prepare(update, query=filter)]
        if scope and self.name in CONFIG and kwargs.get('upsert'):
            abort(403, description='New organisation settings require central HR.')
        query = self._query(filter, writing=True)
        targets = []
        session = kwargs.get('session')
        session_options = {'session': session} if session is not None else {}
        if actor(self.database.raw):
            cursor = self.raw.find(query, PROJECTION, **session_options)
            targets = list(cursor if method.endswith('_many') else cursor.limit(1))
        result = getattr(self.raw, method)(query, *prepared, *args, **kwargs)
        if isinstance(result, dict):
            changed = True
        else:
            changed = bool(getattr(result, 'modified_count', 0) or getattr(result, 'deleted_count', 0) or getattr(result, 'upserted_id', None))
        if changed:
            if not targets and getattr(result, 'upserted_id', None):
                targets = list(self.raw.find({'_id': result.upserted_id}, PROJECTION, **session_options))
            operation = 'deleted' if update is None else 'created' if getattr(result, 'upserted_id', None) else 'updated'
            record(self.database.raw, self.name, operation, targets,
                   update=update, session=session)
        self._invalidate()
        return result

    def update_one(self, filter, update, *args, **kwargs):
        return self._write('update_one', filter, update, *args, **kwargs)

    def update_many(self, filter, update, *args, **kwargs):
        return self._write('update_many', filter, update, *args, **kwargs)

    def replace_one(self, filter, replacement, *args, **kwargs):
        return self._write('replace_one', filter, replacement, *args, **kwargs)

    def find_one_and_update(self, filter, update, *args, **kwargs):
        return self._write('find_one_and_update', filter, update, *args, **kwargs)

    def find_one_and_replace(self, filter, replacement, *args, **kwargs):
        return self._write('find_one_and_replace', filter, replacement, *args, **kwargs)

    def delete_one(self, filter, *args, **kwargs):
        return self._write('delete_one', filter, None, *args, **kwargs)

    def delete_many(self, filter, *args, **kwargs):
        return self._write('delete_many', filter, None, *args, **kwargs)

    def find_one_and_delete(self, filter, *args, **kwargs):
        return self._write('find_one_and_delete', filter, None, *args, **kwargs)

    def with_options(self, *args, **kwargs):
        return SuiteCollection(self.database, self.raw.with_options(*args, **kwargs))

    def __getattr__(self, name):
        if name in {'create_index', 'create_indexes', 'index_information', 'list_indexes', 'full_name'}:
            return getattr(self.raw, name)
        if manager_scope(self.database):
            abort(403, description='This database operation is not available in the manager suite.')
        return getattr(self.raw, name)


class SuiteDatabase:
    def __init__(self, database):
        self.raw = database

    def __getitem__(self, name):
        return SuiteCollection(self, self.raw[name])

    def __getattr__(self, name):
        if name in {'client', 'name', 'list_collection_names'}:
            return getattr(self.raw, name)
        return self[name]

    def get_collection(self, name, *args, **kwargs):
        return SuiteCollection(self, self.raw.get_collection(name, *args, **kwargs))


def scoped_db(database):
    return database if isinstance(database, SuiteDatabase) else SuiteDatabase(database)
