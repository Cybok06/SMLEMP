"""Printer access is opt-in, with individual overrides over branch grants."""
from datetime import datetime
from bson import ObjectId
from db import db


def user_document(user_id):
    if not ObjectId.is_valid(str(user_id)):
        return None
    return db.users.find_one({'_id': ObjectId(str(user_id))})


def agent_branch(agent):
    branch = str(agent.get('branch') or '').strip()
    if not branch and agent.get('manager_id'):
        manager = user_document(agent['manager_id']) or {}
        branch = str(manager.get('branch') or '').strip()
    return branch


def has_printer_access(user_id):
    agent = user_document(user_id)
    if not agent or agent.get('role') != 'agent':
        return False
    override = db.printer_access.find_one({'_id': 'agent:' + str(user_id)})
    if override and isinstance(override.get('enabled'), bool):
        return override['enabled']
    branch = agent_branch(agent)
    grant = db.printer_access.find_one({'_id': 'branch:' + branch}) if branch else None
    return bool(grant and grant.get('enabled') is True)


def payment_print_defaults(recorded_at, agent_id):
    enabled = has_printer_access(agent_id)
    branch = agent_branch(user_document(agent_id) or {}) if enabled else ''
    return {
        'printed': False, 'printed_at': None,
        'payment_recorded_at': recorded_at, 'print_delay_seconds': None,
        'printer_feature_enabled': enabled,
        'printer_branch': branch,
        'print_status': 'pending' if enabled else 'not_requested',
        'receipt_prints': {name: {'status': 'pending', 'printed': False,
                                'printed_at': None, 'print_delay_seconds': None}
                           for name in ('short', 'full')},
    }


def set_access(scope, target, enabled, actor):
    now = datetime.utcnow()
    db.printer_access.update_one(
        {'_id': scope + ':' + target},
        {'$set': {'scope': scope, 'target': target, 'enabled': enabled,
                  'updated_at': now, 'updated_by': actor},
         '$push': {'history': {'enabled': enabled, 'at': now, 'by': actor}}},
        upsert=True,
    )
