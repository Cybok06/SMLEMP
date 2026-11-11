"""Isolated real-Mongo contract/performance check. Accepts loopback only; creates its own DB."""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient, monitoring
from pytest import MonkeyPatch


class Counter(monitoring.CommandListener):
    count = 0
    def started(self, event): self.count += 1
    def succeeded(self, event): pass
    def failed(self, event): pass


def main():
    uri = Path('.test-tools/mongo-uri.txt').read_text().strip()
    if urlparse(uri).hostname != '127.0.0.1':
        raise SystemExit('Only the isolated loopback server is allowed')
    counter = Counter()
    mongo = MongoClient(uri, event_listeners=[counter], serverSelectionTimeoutMS=2000)
    database = mongo['performance_fixture_' + uuid4().hex]
    sys.path.insert(0, str(Path('tests').resolve()))
    from test_customer_support_targets import flow
    from services.support_task_queries import task_page, task_counts
    patch = MonkeyPatch()
    try:
        client, _, _, _, *_ = flow.__wrapped__(patch)
        module = sys.modules['customer_support_backend.tickets_calls_api']
        module.db = database
        for key, value in list(vars(module).items()):
            if key.endswith('_col') and hasattr(value, 'name'):
                setattr(module, key, database[value.name])
        person, customer = ObjectId(), ObjectId()
        database.users.insert_one({'_id': person, 'name': 'Related Officer'})
        database.customers.insert_one({'_id': customer, 'name': 'Related Customer'})
        now = datetime.utcnow()
        rows = [{'_id': ObjectId(), 'title': f'Task {i}', 'description': 'fixture ' * 40, 'timestamp': now - timedelta(seconds=i),
                 'status': ['pending', ' IN_PROGRESS ', 'completed', 'overdue', 'other'][i % 5],
                 'due_date': [now + timedelta(days=1), (now - timedelta(days=2)).isoformat(), None, 'invalid', '2026-01-01T23:00:00-05:00'][i % 5],
                 'customer_id': customer if i % 2 else str(customer), 'assignee_id': person if i % 2 else str(person),
                 'priority': 'High' if i % 2 else 'Medium', 'category': 'Support'} for i in range(1503)]
        database.tasks.insert_many(rows)
        expected = module._hydrate_tasks(rows)
        counts = {status: sum(item['status'] == status for item in expected) for status in ('Pending', 'In Progress', 'Completed', 'Overdue')}
        assert task_counts(database.tasks) == counts
        all_ids = []
        for page in range(1, 62):
            selected, global_counts, metadata = task_page(database.tasks, {'page': page})
            assert global_counts == counts and metadata['total'] == 1503
            all_ids.extend(str(row['_id']) for row in selected)
        assert len(set(all_ids)) == len(rows) == len(all_ids)
        for args in [{'q': 'Related Customer'}, {'q': 'Related Officer'}, {'q': expected[0]['id']}, {'status': 'Overdue', 'priority': 'High'}, {'q': 'Task 15'}]:
            selected, _, metadata = task_page(database.tasks, args)
            matching = [item for item in expected if (not args.get('status') or item['status'] == args['status']) and (not args.get('priority') or item['priority'] == args['priority']) and (not args.get('q') or any(args['q'].lower() in item[key].lower() for key in ('id', 'title', 'customer', 'assignee')))]
            assert metadata['total'] == len(matching), (args, metadata, len(matching))
        results = []
        for label, path in [('legacy_list', '/api/customer-support/tasks'), ('counts', '/api/customer-support/tasks/counts'), ('page_25', '/api/customer-support/tasks?paginated=1')]:
            samples = []
            for _ in range(5):
                counter.count = 0
                start = time.perf_counter()
                response = client.get(path)
                elapsed = (time.perf_counter() - start) * 1000
                assert response.status_code == 200, response.data
                samples.append({'ms': round(elapsed, 3), 'bytes': len(response.data), 'db_commands': counter.count})
            results.append({'endpoint': label, 'samples': samples})
        Path('docs/performance-local-measurements.json').write_text(json.dumps({'scope': '1503 synthetic tasks; isolated local MongoDB; legacy list still caps at 1000; no production traffic', 'results': results}, indent=2))
        print('Real MongoDB: normalized counts, mixed IDs, complete pagination and related-name search passed.')
        print(json.dumps(results))
    finally:
        mongo.drop_database(database.name)
        mongo.close()
        patch.undo()


if __name__ == '__main__':
    main()
