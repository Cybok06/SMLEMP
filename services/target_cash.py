"""Product and SUSU collection targets, independent of product unit targets."""
from services.request_memo import request_memo

CHANNELS = ('product_cash', 'susu_cash')


@request_memo
def collections_by_agent(collection, agent_ids, start, end):
    from bson import ObjectId
    references = list(agent_ids) + [ObjectId(str(aid)) for aid in agent_ids if ObjectId.is_valid(str(aid))]
    rows = collection.aggregate([
        {'$match': {'agent_id': {'$in': references}, 'date': {'$gte': start, '$lte': end},
                    'payment_type': {'$in': ['PRODUCT', 'SUSU']}}},
        {'$group': {'_id': {'agent': {'$toString': '$agent_id'}, 'type': '$payment_type'},
                    'total': {'$sum': {'$toDouble': {'$ifNull': ['$amount', 0]}}}}},
    ])
    result = {}
    for row in rows:
        aid = str(row['_id']['agent'])
        channel = 'product_cash' if row['_id']['type'] == 'PRODUCT' else 'susu_cash'
        result.setdefault(aid, {})[channel] = round(float(row.get('total') or 0), 2)
    return result


def extend_summary(summary, target, collected, *, quota=False):
    caps = summary['quota' if quota else 'targets']
    suffix = 'quota' if quota else 'target'
    for channel in CHANNELS:
        cap = float(target.get(channel + '_' + suffix) or 0)
        achieved = round(float(collected.get(channel) or 0), 2)
        caps[channel + '_' + suffix] = cap
        summary['achieved'][channel + '_achieved'] = achieved
        summary['percents'][channel + '_pct'] = round(achieved / cap * 100, 2) if cap else 0
    metrics = ('product', 'cash', 'customer') + CHANNELS
    percentages = [summary['percents'].get(c + '_pct', 0) for c in metrics if caps.get(c + '_' + suffix)]
    summary['percents']['overall_pct'] = round(sum(percentages) / len(percentages), 2) if percentages else 0
    return summary


def extend_daily_progress(progress, target_map, collection, start, end):
    collected = collections_by_agent(collection, list(progress), start, end)
    for aid, row in progress.items():
        caps = target_map.get(aid) or {}
        for channel in CHANNELS:
            cap = float(caps.get(channel + '_target_daily') or 0)
            achieved = float(collected.get(aid, {}).get(channel) or 0)
            row[channel + '_target_daily'] = cap
            row[channel + '_achieved_today'] = achieved
            row[channel + '_progress_pct'] = round(achieved / cap * 100, 2) if cap else 0
        pcts = [row.get(c + '_progress_pct', 0) for c in ('cash', 'product', 'customer') + CHANNELS if row.get(c + '_target_daily')]
        row['overall_progress_pct'] = round(sum(pcts) / len(pcts), 2) if pcts else 0
        if any(row[c + '_target_daily'] for c in CHANNELS):
            row['target_hit'] = bool(pcts) and all(pct >= 100 for pct in pcts)


def extend_daily_totals(totals, progress):
    for channel in CHANNELS:
        for kind, suffix in (('target', 'target_daily'), ('achieved', 'achieved_today')):
            totals[f'total_{channel}_{kind}_today'] = round(sum(row.get(channel + '_' + suffix, 0) for row in progress.values()), 2)
    totals['agents_with_targets_count'] = sum(any(row.get(c + '_target_daily') for c in ('cash', 'product', 'customer') + CHANNELS) for row in progress.values())
    totals['agents_hit_target_count'] = sum(bool(row.get('target_hit')) for row in progress.values())
