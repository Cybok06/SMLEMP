from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from login import get_current_identity
from services.agent_deliveries import delivery_page, undelivered_count

agent_deliveries_bp = Blueprint('agent_deliveries', __name__)


@agent_deliveries_bp.get('/agent/deliveries')
def deliveries():
    identity = get_current_identity()
    if not identity.get('is_authenticated'):
        return redirect(url_for('login.login', next=request.path))
    if identity.get('role') != 'agent':
        return 'Forbidden', 403
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    context = delivery_page(identity['user_id'], request.args.get('status', 'undelivered'), page)
    if page > context['total_pages']:
        return redirect(url_for('.deliveries', status=context['status'], page=context['total_pages']))
    return render_template('agent_deliveries.html', **context, agent_deliveries_count=context['undelivered_count'])


@agent_deliveries_bp.get('/api/agent/deliveries/count')
def delivery_count():
    identity = get_current_identity()
    if not identity.get('is_authenticated'):
        return jsonify(ok=False, message='Please sign in.'), 401
    if identity.get('role') != 'agent':
        return jsonify(ok=False, message='Only agents can access deliveries.'), 403
    return jsonify(ok=True, count=undelivered_count(identity['user_id']))
