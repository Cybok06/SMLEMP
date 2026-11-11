"""Executive-only reporting for payments that required a printed receipt."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bson import ObjectId
from flask import Blueprint, abort, jsonify, render_template, request

from login import role_required
from services.printer_analysis import build_analysis

printer_analysis_bp = Blueprint('printer_analysis', __name__)


@printer_analysis_bp.get('/executive/printer-analysis')
@role_required('executive')
def page():
    return render_template('printer_analysis.html')


@printer_analysis_bp.get('/executive/printer-analysis/data')
@role_required('executive')
def data():
    today = datetime.now(ZoneInfo('Africa/Accra')).date()
    try:
        start = date.fromisoformat(request.args.get('start') or str(today - timedelta(days=29)))
        end = date.fromisoformat(request.args.get('end') or str(today))
    except ValueError:
        abort(400, 'Dates must be YYYY-MM-DD.')
    if start > end or (end - start).days > 366 or end > today:
        abort(400, 'Select a valid date range of at most 367 days through today.')
    agent = request.args.get('agent', '').strip()
    if agent and not ObjectId.is_valid(agent):
        abort(400, 'Invalid agent ID.')
    status = request.args.get('status', '').strip()
    if status and status not in ('printed', 'waiting', 'missed', 'uncertain'):
        abort(400, 'Invalid status.')
    branch = request.args.get('branch', '').strip()[:100]
    result = build_analysis(start, end, branch=branch, agent_id=agent, status=status)
    return jsonify(result)
