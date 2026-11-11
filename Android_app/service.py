import time
import logging
from pathlib import Path

from sync_worker import SyncWorker


def service_data_dir():
    from jnius import autoclass
    service = autoclass("org.kivy.android.PythonService").mService
    return Path(str(service.getFilesDir().getAbsolutePath())) / "app"


worker = SyncWorker(service_data_dir())
while True:
    try:
        worker.run_once()
    except Exception:
        # Pending rows remain in SQLite and will be retried when connectivity returns.
        logging.exception("SmartLiving background call sync failed; retrying in 30 seconds")
    time.sleep(30)
