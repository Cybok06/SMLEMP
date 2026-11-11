"""Run bounded repair regularly: python -m scripts.repair_delivery_followups --limit 200."""
import argparse
import json

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    from db import db
    from services.delivery_followups import reconcile_delivery_followups
    result = reconcile_delivery_followups(db, args.limit)
    print(json.dumps(result))
    raise SystemExit(1 if result['retry'] else 0)
