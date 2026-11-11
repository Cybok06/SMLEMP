"""Executive, HR and admin review of agents registered by managers."""
import re
import secrets
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from db import db
from login import get_current_identity, role_required

pending_agents_bp = Blueprint('pending_agents', __name__)


def pending_query():
    return {'role': 'agent', '$or': [
        {'agent_approval_status': 'pending'},
        {'status': {'$regex': '^under review$', '$options': 'i'}},
    ]}


@pending_agents_bp.route('/executive/pending-agents')
@role_required('executive', 'hr', 'admin')
def pending_agents():
    query = pending_query()
    search = request.args.get('search', '').strip()
    if search:
        pattern = {'$regex': re.escape(search), '$options': 'i'}
        query = {'$and': [query, {'$or': [{key: pattern} for key in ('name', 'username', 'phone', 'branch')]}]}
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    total = db.users.count_documents(query)
    total_pages = max(1, (total + 19) // 20)
    page = min(page, total_pages)
    agents = list(db.users.find(query, {'password': 0}).sort('date_registered', -1).skip((page - 1) * 20).limit(20))
    manager_ids = {agent.get('manager_id') for agent in agents if agent.get('manager_id')}
    manager_oids = [ObjectId(str(value)) for value in manager_ids if ObjectId.is_valid(str(value))]
    managers = {str(row['_id']): row.get('name') or row.get('username') for row in
                db.users.find({'_id': {'$in': manager_oids}}, {'name': 1, 'username': 1})}
    session.setdefault('agent_approval_csrf', secrets.token_urlsafe(32))
    reviewer_role = get_current_identity()['role']
    template = 'hr_pages/hr_shell.html' if reviewer_role == 'hr' else 'pending_agents.html'
    return render_template(template, agents=agents, managers=managers,
                           total=total, page=page, total_pages=total_pages, search=search,
                           reviewer_role=reviewer_role, active_page='pending_agents',
                           hr_content_template='partials/pending_agents_content.html',
                           page_title='Pending Agents')


@pending_agents_bp.route('/executive/pending-agents/<agent_id>/approve', methods=['POST'])
@role_required('executive', 'hr', 'admin')
def approve_agent(agent_id):
    expected = session.get('agent_approval_csrf')
    if not expected or not secrets.compare_digest(request.form.get('csrf_token', ''), expected):
        return 'Invalid form token', 400
    if not ObjectId.is_valid(agent_id):
        return 'Invalid agent ID', 400
    query = pending_query()
    query['_id'] = ObjectId(agent_id)
    result = db.users.update_one(query, {'$set': {
        'status': 'Active', 'is_active': True, 'agent_approval_status': 'approved',
        'agent_approved_by': get_current_identity()['user_id'],
        'agent_approved_at': datetime.utcnow(),
    }})
    if result.modified_count:
        flash('Agent approved. The account can now log in.', 'success')
    else:
        flash('This agent is no longer pending approval or could not be found.', 'warning')
    return redirect(url_for('pending_agents.pending_agents'))
