"""Receipt rollout metrics for executive reporting."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo
from bson import ObjectId
from db import db
from services.payment_receipt_tracking import receipt_payment_id

GHANA = ZoneInfo('Africa/Accra')
GRACE_SECONDS = 15 * 60
PROJECTION = {'_id': 1, 'recorded_by_agent_id': 1, 'agent_id': 1, 'manager_id': 1,
              'printer_branch': 1, 'amount': 1, 'date': 1, 'payment_id': 1,
              'payment_recorded_at': 1, 'printed': 1, 'printed_at': 1,
              'print_delay_seconds': 1, 'print_status': 1, 'print_started_at': 1,
              'receipt_prints': 1}


def _utc_naive(value):
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _person_id(value):
    return str(value or '')


def _user_map(agent_ids):
    ids = [ObjectId(value) for value in agent_ids if ObjectId.is_valid(value)]
    agents = {str(row['_id']): row for row in db.users.find(
        {'_id': {'$in': ids}}, {'name': 1, 'username': 1, 'branch': 1, 'manager_id': 1})} if ids else {}
    manager_ids = {str(row['manager_id']) for row in agents.values() if not row.get('branch') and row.get('manager_id')}
    mids = [ObjectId(value) for value in manager_ids if ObjectId.is_valid(value)]
    managers = {str(row['_id']): row.get('branch') or '' for row in db.users.find(
        {'_id': {'$in': mids}}, {'branch': 1})} if mids else {}
    return agents, managers


def _status(payment, now):
    formats = payment.get('receipt_prints') or {}
    printed_formats = [name for name in ('short', 'full') if (formats.get(name) or {}).get('printed') is True]
    if payment.get('printed') or printed_formats:
        copies = len(printed_formats) if printed_formats else 1  # Old records have no format detail.
        return 'printed', copies, printed_formats
    states = [(formats.get(name) or {}).get('status') for name in ('short', 'full')]
    if payment.get('print_status') == 'uncertain' or 'uncertain' in states:
        return 'uncertain', 0, []
    recorded = _utc_naive(payment.get('payment_recorded_at'))
    age = (now - recorded).total_seconds() if recorded else 0
    if age >= GRACE_SECONDS:
        return 'missed', 0, []
    return 'waiting', 0, []


def build_analysis(start_date, end_date, branch='', agent_id='', status='', now=None):
    """One bounded payment scan, then batched staff lookup. No print tokens leave this service."""
    now = _utc_naive(now or datetime.now(timezone.utc))
    start = datetime.combine(start_date, time.min, GHANA).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(end_date + timedelta(days=1), time.min, GHANA).astimezone(timezone.utc).replace(tzinfo=None)
    query = {'printer_feature_enabled': True,
             'payment_recorded_at': {'$gte': start, '$lt': end}}
    if agent_id:
        query['recorded_by_agent_id'] = agent_id
    payments = list(db.payments.find(query, PROJECTION).sort('payment_recorded_at', -1))
    agent_ids = {_person_id(p.get('recorded_by_agent_id') or p.get('agent_id')) for p in payments}
    agents, managers = _user_map(agent_ids)
    daily = {str(start_date + timedelta(days=i)): {'date': str(start_date + timedelta(days=i)), 'required': 0,
            'printed': 0, 'missed': 0} for i in range((end_date-start_date).days + 1)}
    branches = defaultdict(lambda: {'required': 0, 'printed': 0, 'missed': 0})
    states = {key: 0 for key in ('printed', 'waiting', 'missed', 'uncertain')}
    intervals = []
    buckets = {'Under 1 min': 0, '1-5 min': 0, '5-15 min': 0, '15-60 min': 0, 'Over 60 min': 0}
    rows = []
    receipts_sent = short_sent = full_sent = late_prints = required = 0
    for payment in payments:
        uid = _person_id(payment.get('recorded_by_agent_id') or payment.get('agent_id'))
        agent = agents.get(uid) or {}
        agent_branch = payment.get('printer_branch') or agent.get('branch') or managers.get(_person_id(agent.get('manager_id'))) or 'Unassigned'
        if branch and agent_branch != branch:
            continue
        outcome, copies, printed_formats = _status(payment, now)
        if status and outcome != status:
            continue
        required += 1
        receipts_sent += copies
        short_sent += 'short' in printed_formats
        full_sent += 'full' in printed_formats
        states[outcome] += 1
        day = _utc_naive(payment.get('payment_recorded_at')).replace(tzinfo=timezone.utc).astimezone(GHANA).date().isoformat()
        if day in daily:
            daily[day]['required'] += 1
            daily[day]['printed'] += outcome == 'printed'
            daily[day]['missed'] += outcome == 'missed'
        branch_row = branches[agent_branch]
        branch_row['required'] += 1
        branch_row['printed'] += outcome == 'printed'
        branch_row['missed'] += outcome == 'missed'
        formats = payment.get('receipt_prints') or {}
        format_times = [_utc_naive(item['printed_at']) for item in formats.values() if item.get('printed_at')]
        printed_at = _utc_naive(payment.get('printed_at')) or (min(format_times) if format_times else None)
        delay = payment.get('print_delay_seconds')
        if outcome == 'printed' and delay is None and printed_at:
            delay = max(0, (printed_at - _utc_naive(payment['payment_recorded_at'])).total_seconds())
        if outcome == 'printed' and delay is not None:
            delay = float(delay)
            intervals.append(delay)
            late_prints += delay >= GRACE_SECONDS
            bucket = ('Under 1 min' if delay < 60 else '1-5 min' if delay < 300 else
                      '5-15 min' if delay < 900 else '15-60 min' if delay < 3600 else 'Over 60 min')
            buckets[bucket] += 1
        if len(rows) < 100:
            rows.append({'document_id': str(payment['_id']), 'payment_id': receipt_payment_id(payment),
                         'agent': agent.get('name') or agent.get('username') or uid or 'Unknown',
                         'branch': agent_branch, 'amount': round(float(payment.get('amount') or 0), 2),
                         'recorded_at': _utc_naive(payment['payment_recorded_at']).isoformat() + 'Z',
                         'printed_at': printed_at.isoformat() + 'Z' if printed_at else None,
                         'interval_seconds': round(float(delay), 1) if delay is not None else None,
                         'status': outcome, 'short_status': (formats.get('short') or {}).get('status') or 'Unknown',
                         'full_status': (formats.get('full') or {}).get('status') or 'Unknown'})
    branch_chart = [{'branch': name, **values,
                     'coverage_percent': round(100 * values['printed'] / values['required'], 1)}
                    for name, values in sorted(branches.items(), key=lambda item: (-item[1]['required'], item[0]))]
    summary = {'required': required, 'printed_payments': states['printed'],
               'coverage_percent': round(100 * states['printed'] / required, 1) if required else 0,
               'receipts_sent': receipts_sent, 'short_sent': short_sent, 'full_sent': full_sent,
               'missed': states['missed'],
               'waiting': states['waiting'], 'uncertain': states['uncertain'],
               'median_interval_seconds': round(median(intervals), 1) if intervals else None,
               'late_prints': late_prints}
    return {'summary': summary, 'daily': list(daily.values()), 'branches': branch_chart,
            'status_breakdown': states, 'interval_buckets': buckets, 'payments': rows,
            'details_limit': 100, 'grace_minutes': 15}
