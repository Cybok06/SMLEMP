# SmartLiving Call Sync V1

## Architecture

This is a small Kivy Android utility, not a replacement for the SmartLiving web application. `call_logs.py` reads Android's native call log through Pyjnius, `storage.py` keeps a persistent SQLite retry queue, `api.py` uploads batches, and `main.py` provides the manual Sync Now screen.

The Flask endpoint stores imported records in the existing `customer_support_calls` collection. Imported calls appear in the normal Calls Management page and require a support officer to complete business information.

## Dependencies and permissions

Dependencies are Kivy, Pyjnius, Requests, Buildozer, and python-for-android. The APK requests `READ_CALL_LOG` and `READ_PHONE_STATE` at runtime. `INTERNET` and `ACCESS_NETWORK_STATE` are normal manifest permissions. Denied and permanently denied call-log permission states are shown in the UI.

## Call reading and local state

The Android projection reads `_id`, `number`, `type`, `date`, `duration`, and `phone_account_id`. Types 1, 2, and 3 map to inbound, outbound, and missed. Unsupported Android call types are skipped.

The deterministic local key is `<device_id>:<android_call_log_id>`. SQLite uses that key as its primary key, keeps failed uploads pending, and marks created or server-reported duplicate records as synced. Restarting the application does not clear this state.

The first-run database stores Android's package `firstInstallTime`. Every call-log query includes `date >= firstInstallTime`, so calls made before installation are never queued, including when the first app launch happens later.

Where Android and the carrier expose it, `READ_PHONE_NUMBERS` and the call's phone-account identifier resolve the local SIM line into `from_number`. Some carriers deliberately return an empty line number; those records remain valid and the web UI displays “Not available” rather than inventing a number.

## API

Production base URL: `https://smartliving-u2rf.onrender.com`

Endpoint: `POST /api/customer-support/mobile/calls/sync`

```json
{
  "device_id": "support-phone-01",
  "calls": [{
    "external_call_id": "7821",
    "phone_number": "0530393625",
    "call_type": "outbound",
    "started_at": "2026-08-12T18:49:00+00:00",
    "duration_seconds": 332,
    "sim_account": "SIM1",
    "from_number": "0530000001"
  }]
}
```

```json
{"success": true, "created": 1, "duplicates": 0, "failed": 0, "results": [{"index": 0, "status": "created", "call_id": "CALL-ANDROID-..."}]}
```

Requests require `Authorization: Bearer <device token>`. The raw token is not stored by Flask; MongoDB stores its SHA-256 hash in `customer_support_mobile_devices`:

```javascript
db.customer_support_mobile_devices.insertOne({
  device_id: "support-phone-01",
  name: "Support Phone 01",
  token_hash: "<sha256-of-random-token>",
  active: true,
  officer_id: "<optional-user-id>",
  officer_name: "<optional-display-name>"
})
```

Generate a token and hash on an administrator-controlled machine:

```bash
python -c "import secrets,hashlib; t=secrets.token_urlsafe(32); print('TOKEN='+t); print('SHA256='+hashlib.sha256(t.encode()).hexdigest())"
```

Store the raw token only in the Android app's private `sync_config.json`:

```json
{"device_id":"support-phone-01","sync_token":"<raw-token>","api_base_url":"https://smartliving-u2rf.onrender.com"}
```

For development, the same values can be supplied as `SMARTLIVING_DEVICE_ID`, `SMARTLIVING_SYNC_TOKEN`, and `SMARTLIVING_API_BASE_URL`. Never commit the token or `sync_config.json`.

## Customer matching and duplicates

The server normalizes Ghana numbers such as `0530393625`, `233530393625`, and `+233530393625` to `233530393625`. It matches against the existing Customers collection and takes names and IDs only from MongoDB. Unknown numbers are retained with `customer_match: not_customer`.

MongoDB has a partial unique index on `(device_id, external_call_id)` for `source: android`. This protects against manual retries, network retries, and app restarts. Manual calls are unaffected.

## Development

Kivy UI development can run on desktop, but Android call-log access reports unavailable:

```bash
cd Android_app
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python main.py
```

Buildozer requires Linux or WSL2; native Windows APK builds are not supported by Buildozer. In Ubuntu/WSL2, install Java, Android build dependencies, Python headers, and Buildozer, then run:

```bash
cd Android_app
buildozer android debug
```

The debug APK is created under `Android_app/bin/`. Enable developer options and USB debugging on a physical company Android phone, connect it, then install with:

```bash
adb install -r bin/*.apk
```

Provision the app-private `sync_config.json` using the organization's controlled deployment process before syncing. For a debug APK, create a local `sync_config.json`, then pipe it into the debuggable app's private files directory (the exact directory can differ by python-for-android version):

```bash
adb shell run-as com.smartliving.smartlivingcallsync mkdir -p files/app
adb shell run-as com.smartliving.smartlivingcallsync sh -c 'cat > files/app/sync_config.json' < sync_config.json
```

Confirm the directory shown by `adb shell run-as com.smartliving.smartlivingcallsync find files -maxdepth 3 -type f`. For production, use managed enrollment and Android Keystore rather than ADB. Register the matching device and token hash in MongoDB first. Open the app, grant Call Logs permission, and press Sync Now.

## Background synchronization

After the required first launch, token provisioning, and runtime permission approval, the app starts a sticky foreground python-for-android service. The service polls every 30 seconds, immediately uploads queued records when the API becomes reachable, and is restarted by a `BOOT_COMPLETED` receiver after the phone boots. A persistent Android notification is expected for the foreground service.

Android does not permit an app to grant itself call-log permission or initialize protected configuration before its first launch. Some manufacturers also terminate foreground services under aggressive battery management. On each company device, set SmartLiving Call Sync battery usage to **Unrestricted**, allow background activity/data, and do not place it in sleeping-app lists.

## Current limitations and next steps

SIM line numbers can be unavailable when the carrier does not provision them on the SIM. Network checks use a 30-second retry loop rather than an Android network callback. A next phase should use WorkManager/network constraints, Android Keystore token storage, managed device enrollment, and device revocation administration.
