"""Run from the project root: python -m scripts.enrich_login_locations --limit 100."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    from db import db
    from services.login_enrichment import enrich_pending
    result = enrich_pending(db.login_logs, args.limit)
    print(json.dumps(result))
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
