from api import SmartLivingAPI
from call_logs import app_install_time_ms, read_call_logs
from config import BATCH_SIZE, REQUEST_TIMEOUT_SECONDS, load_device_config
from storage import SyncStorage


class SyncWorker:
    def __init__(self, data_dir):
        self.config = load_device_config(data_dir)
        self.storage = SyncStorage(str(data_dir / "call_sync.sqlite3"))
        self.api = SmartLivingAPI(self.config["api_base_url"], self.config["sync_token"], REQUEST_TIMEOUT_SECONDS)
        if self.storage.get_metadata("install_cutoff_ms") is None:
            self.storage.set_metadata("install_cutoff_ms", app_install_time_ms())

    def run_once(self):
        device_id = self.config["device_id"]
        if not device_id: raise RuntimeError("Device ID is not configured.")
        cutoff = int(self.storage.get_metadata("install_cutoff_ms"))
        for call in read_call_logs(since_ms=cutoff):
            self.storage.enqueue(f"{device_id}:{call['external_call_id']}", call)
        while True:
            pending = self.storage.pending(BATCH_SIZE)
            if not pending: break
            keys, calls = zip(*pending)
            result = self.api.sync_calls(device_id, list(calls))
            successful = [keys[item["index"]] for item in result.get("results", []) if item.get("status") in ("created", "duplicate")]
            self.storage.mark_synced(successful)
            failed = [key for key in keys if key not in successful]
            if failed:
                self.storage.mark_failed(failed, "Server rejected one or more calls.")
                break
        return self.storage.pending_count()
