import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


class SyncStorage:
    def __init__(self, path):
        self.path = path
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS call_queue (
                sync_key TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL, synced_at TEXT
            )""")
            conn.execute("CREATE TABLE IF NOT EXISTS sync_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def enqueue(self, sync_key, payload):
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO call_queue(sync_key,payload,created_at) VALUES(?,?,?)",
                         (sync_key, json.dumps(payload), datetime.now(timezone.utc).isoformat()))

    def pending(self, limit=100):
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT sync_key,payload FROM call_queue WHERE status='pending' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        return [(key, json.loads(payload)) for key, payload in rows]

    def mark_synced(self, sync_keys):
        if not sync_keys: return
        with self._connect() as conn:
            conn.executemany("UPDATE call_queue SET status='synced',synced_at=?,last_error=NULL WHERE sync_key=?",
                             [(datetime.now(timezone.utc).isoformat(), key) for key in sync_keys])

    def mark_failed(self, sync_keys, error):
        with self._connect() as conn:
            conn.executemany("UPDATE call_queue SET attempts=attempts+1,last_error=? WHERE sync_key=?", [(str(error)[:500], key) for key in sync_keys])

    def pending_count(self):
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM call_queue WHERE status='pending'").fetchone()[0]

    def last_sync(self):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT MAX(synced_at) FROM call_queue WHERE status='synced'").fetchone()
        return row[0] if row and row[0] else "Never"

    def get_metadata(self, key, default=None):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM sync_metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_metadata(self, key, value):
        with self._connect() as conn:
            conn.execute("INSERT INTO sync_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
