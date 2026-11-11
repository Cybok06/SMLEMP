# SmartLiving Call Sync

Native Kotlin/Compose utility for company Android phones. It reads supported calls made on or after the app's real first-install time, queues them durably, and sends them to the existing SmartLiving customer-support backend. It does **not** implement customer records, tickets, call enrichment, or the SmartLiving web UI.

## Requirements

- Android Studio with JDK 11 or newer (the bundled JBR is supported)
- Android SDK 37 for this checkout; `minSdk 26`, `targetSdk 37`
- A controlled Android phone for call-log and SIM verification
- A non-empty device ID and a local display name

## Build and install

```powershell
./gradlew test
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

For connected tests, start an API-compatible emulator or attach a device, then run:

```powershell
./gradlew connectedAndroidTest
```

## Device settings

Launch the app, enter a non-empty Device ID and an optional local Device Name, then save. The Device ID is included in every sync request as an ordinary identifier. The call-sync endpoint does not use device authentication.

Installing an update preserves the Room queue and settings. On first startup, legacy encrypted call-sync credential material from older releases is removed without modifying the Room database.

## Permissions

The app requests `READ_CALL_LOG`, `READ_PHONE_STATE`, and `READ_PHONE_NUMBERS` at runtime. Call-log access is mandatory. Phone-state/number access improves SIM-line resolution; if Android or the carrier does not expose a line number, `from_number` is sent as an empty string without guessing. A permanent call-log denial shows an **Open App Settings** action.

Android and Google Play restrict Call Log permissions. This app is intended for controlled company devices and may require enterprise/private distribution. Public Play distribution requires a policy review and may not be eligible.

## Manual synchronization

**Sync Now** performs one repository-level serialized run:

1. Load the immutable cutoff persisted from `PackageInfo.firstInstallTime`.
2. Query `CallLog.Calls` with `DATE >= cutoff` and skip unsupported call types.
3. Resolve the phone account/SIM where permitted and insert unseen calls into Room using `device_id:external_call_id`.
4. Claim pending/retryable rows transactionally and POST batches of 100 (never more than 500).
5. Mark `created` and `duplicate` as synced. Keep `invalid`/`rejected` visible as permanent failures. Queue network, timeout, 429, and 5xx outcomes for exponential-backoff work.

The server's unique `(device_id, external_call_id)` index remains authoritative after uncertain network outcomes.

## Background readiness

Manual sync is the V1 milestone. The same `SyncCoordinator` is reused by `CallSyncWorker`. Failed transient manual exchanges enqueue constrained unique one-time work with `NetworkType.CONNECTED` and exponential backoff. There is no polling loop. Periodic or `ContentObserver`-triggered scheduling should be enabled only after physical-device V1 validation; Android and manufacturer policies can defer background execution.

## Troubleshooting

- **Device ID missing:** enter and save a non-empty Device ID before synchronizing.
- **Calls remain pending:** inspect **Pending & Errors**. Offline/5xx/429 rows remain queued; invalid/rejected rows require a data/code correction.
- **From number is blank:** this is valid when the carrier or Android does not expose the SIM line number. `sim_account` is preserved.
- **Older calls missing:** intentional. Pre-installation calls are excluded by the immutable installation cutoff.
- **No immediate background sync:** expected. WorkManager is opportunistic and subject to OS/battery policy.

See [Architecture](docs/ARCHITECTURE.md), [API contract](docs/API_CONTRACT.md), and [test report](docs/TEST_REPORT.md).
