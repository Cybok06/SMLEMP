import json
import os
from pathlib import Path


PRODUCTION_API_BASE_URL = "https://smartliving-u2rf.onrender.com"
API_BASE_URL = os.environ.get("SMARTLIVING_API_BASE_URL", PRODUCTION_API_BASE_URL).rstrip("/")
REQUEST_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100


def load_device_config(data_dir):
    """Secrets live in app-private storage or environment variables, never source."""
    path = Path(data_dir) / "sync_config.json"
    saved = {}
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
    return {
        "api_base_url": os.environ.get("SMARTLIVING_API_BASE_URL") or saved.get("api_base_url") or API_BASE_URL,
        "device_id": os.environ.get("SMARTLIVING_DEVICE_ID") or saved.get("device_id") or "",
        "sync_token": os.environ.get("SMARTLIVING_SYNC_TOKEN") or saved.get("sync_token") or "",
    }
