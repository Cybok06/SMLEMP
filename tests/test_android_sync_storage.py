import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "Android_app" / "storage.py"
SPEC = importlib.util.spec_from_file_location("android_sync_storage", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SyncStorage = MODULE.SyncStorage


def test_queue_is_idempotent_and_persistent(tmp_path):
    path = tmp_path / "queue.sqlite3"
    storage = SyncStorage(path)
    payload = {"external_call_id": "42", "phone_number": "0530393625"}
    storage.enqueue("phone-01:42", payload)
    storage.enqueue("phone-01:42", payload)
    assert storage.pending_count() == 1
    assert SyncStorage(path).pending(10) == [("phone-01:42", payload)]


def test_failed_call_remains_pending_until_synced(tmp_path):
    storage = SyncStorage(tmp_path / "queue.sqlite3")
    storage.enqueue("phone-01:43", {"external_call_id": "43"})
    storage.mark_failed(["phone-01:43"], "offline")
    assert storage.pending_count() == 1
    storage.mark_synced(["phone-01:43"])
    assert storage.pending_count() == 0
    assert storage.last_sync() != "Never"


def test_install_cutoff_metadata_persists(tmp_path):
    path = tmp_path / "queue.sqlite3"
    storage = SyncStorage(path)
    assert storage.get_metadata("install_cutoff_ms") is None
    storage.set_metadata("install_cutoff_ms", 1770000000000)
    assert SyncStorage(path).get_metadata("install_cutoff_ms") == "1770000000000"
