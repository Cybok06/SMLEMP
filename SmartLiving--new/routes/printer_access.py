"""Executive printer access: a fast shell with paged agent and lazy branch data."""
import hmac
import re
import secrets
from bson import ObjectId
from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for
from login import get_current_identity, role_required
from db import db
from services.printer_access import set_access

printer_access_bp = Blueprint('printer_access', __name__)
PAGE_SIZE = 25


def _branches():
    return sorted({str(value).strip() for value in db.users.distinct(
        'branch', {'role': {'$in': ['agent', 'manager']}}) if isinstance(value, str) and value.strip()})


def _agents_page():
    query = {'role': 'agent'}
    term = (request.args.get('q') or '').strip()[:80]
    branch = (request.args.get('branch') or '').strip()[:100]
    after = (request.args.get('after') or '').strip()
    if term:
        prefix = re.compile('^' + re.escape(term), re.IGNORECASE)
        clauses = [{'name': prefix}, {'username': prefix}, {'branch': prefix}]
        if ObjectId.is_valid(term):
            clauses.append({'_id': ObjectId(term)})
        query['$or'] = clauses
    if branch:
        managers = list(db.users.find({'role': 'manager', 'branch': branch}, {'_id': 1}))
        manager_ids = [item for row in managers for item in (row['_id'], str(row['_id']))]
        clause = {'$or': [
            {'branch': branch},
            {'branch': {'$in': ['', None]}, 'manager_id': {'$in': manager_ids}},
            {'branch': {'$exists': False}, 'manager_id': {'$in': manager_ids}},
        ]}
        if '$or' in query:
            query['$and'] = [{'$or': query.pop('$or')}, clause]
        else:
            query.update(clause)
    if after:
        if not ObjectId.is_valid(after):
            abort(400)
        query['_id'] = {'$gt': ObjectId(after)}
    docs = list(db.users.find(query, {'name': 1, 'username': 1, 'branch': 1, 'manager_id': 1})
                .sort('_id', 1).limit(PAGE_SIZE + 1))
    has_more = len(docs) > PAGE_SIZE
    docs = docs[:PAGE_SIZE]
    missing_manager_ids = {str(a['manager_id']) for a in docs if not a.get('branch') and a.get('manager_id')}
    manager_oids = [ObjectId(value) for value in missing_manager_ids if ObjectId.is_valid(value)]
    manager_map = {str(m['_id']): str(m.get('branch') or '').strip()
                   for m in db.users.find({'_id': {'$in': manager_oids}}, {'branch': 1})} if manager_oids else {}
    branches = {str(a.get('branch') or '').strip() or manager_map.get(str(a.get('manager_id') or ''), '') for a in docs}
    grant_ids = ['agent:' + str(a['_id']) for a in docs] + ['branch:' + b for b in branches if b]
    grants = {g['_id']: g.get('enabled') for g in db.printer_access.find({'_id': {'$in': grant_ids}}, {'enabled': 1})} if grant_ids else {}
    rows = []
    for agent in docs:
        user_id = str(agent['_id'])
        branch_name = str(agent.get('branch') or '').strip() or manager_map.get(str(agent.get('manager_id') or ''), '')
        override = grants.get('agent:' + user_id)
        enabled = override if isinstance(override, bool) else grants.get('branch:' + branch_name) is True
        rows.append({'id': user_id, 'name': agent.get('name') or agent.get('username') or user_id,
                     'branch': branch_name, 'enabled': enabled,
                     'override': override if isinstance(override, bool) else None})
    return jsonify(ok=True, agents=rows, next_cursor=str(docs[-1]['_id']) if has_more else None)


@printer_access_bp.get('/executive/printer-access/agents')
@role_required('executive')
def agents():
    return _agents_page()


@printer_access_bp.get('/executive/printer-access/branches')
@role_required('executive')
def branches():
    names = _branches()
    grants = {row['_id']: row.get('enabled') is True for row in db.printer_access.find(
        {'_id': {'$in': ['branch:' + name for name in names]}}, {'enabled': 1})} if names else {}
    return jsonify(ok=True, branches=[{'name': name, 'enabled': grants.get('branch:' + name, False)} for name in names])


@printer_access_bp.route('/executive/printer-access', methods=['GET', 'POST'])
@role_required('executive')
def page():
    token = session.setdefault('printer_access_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not hmac.compare_digest(token, request.form.get('csrf', '')):
            abort(403)
        scope, target = request.form.get('scope'), request.form.get('target', '').strip()
        action = request.form.get('action')
        if scope not in ('agent', 'branch') or action not in ('grant', 'revoke', 'inherit'):
            abort(400)
        if scope == 'agent':
            if not ObjectId.is_valid(target) or not db.users.find_one({'_id': ObjectId(target), 'role': 'agent'}, {'_id': 1}):
                abort(400)
        elif not target or not db.users.find_one({'branch': target, 'role': {'$in': ['agent', 'manager']}}, {'_id': 1}):
            abort(400)
        if action == 'inherit' and scope != 'agent':
            abort(400)
        enabled = None if action == 'inherit' else action == 'grant'
        set_access(scope, target, enabled, str(get_current_identity()['user_id']))
        if request.headers.get('X-Requested-With', '').lower() == 'xmlhttprequest':
            return jsonify(ok=True)
        return redirect(url_for('printer_access.page'))
    return render_template('printer_access.html', csrf=token)
