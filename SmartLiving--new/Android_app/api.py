import requests


class SmartLivingAPI:
    def __init__(self, base_url, token, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def sync_calls(self, device_id, calls):
        if not self.token:
            raise RuntimeError("Device sync token is not configured.")
        response = requests.post(
            f"{self.base_url}/api/customer-support/mobile/calls/sync",
            json={"device_id": device_id, "calls": calls},
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            timeout=self.timeout,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Server returned HTTP {response.status_code} without JSON.") from exc
        if response.status_code >= 500:
            raise RuntimeError(body.get("error") or "SmartLiving is temporarily unavailable.")
        if response.status_code in (401, 403):
            raise PermissionError(body.get("error") or "Device authorization failed.")
        return body
