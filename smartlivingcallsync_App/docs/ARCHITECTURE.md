# Architecture

## Layers

- `domain`: pure call mapping/time/key models plus framework-facing `CallLogReader` and `SimResolver` interfaces. `SyncCoordinator` owns the serialized end-to-end flow.
- `data/local`: Room queue and transactions, DataStore metadata/cutoff/last-sync state.
- `data/remote`: exact kotlinx-serialization DTOs and the Retrofit endpoint. No authentication interceptor is installed.
- `data/repository`: durable enqueue, batching, per-index result handling, and failure classification.
- `worker`: a constrained unique WorkManager adapter reusing `SyncCoordinator`.
- `receiver`: a non-exported phone-state trigger and boot recovery scheduler. Receivers never read CallLog or perform network/database work.
- `ui`: single-activity Material 3 Compose device settings, permission/status, progress, and diagnostics screens.

Framework access is behind interfaces so call-log and SIM behavior can use fakes. Dependencies are assembled in a small application container; Hilt was intentionally omitted because the graph is small.

## Persistence and idempotency

Room primary key: `device_id + ":" + external_call_id`. Inserts use `IGNORE`. Rows move through `PENDING`, `SYNCING`, `SYNCED`, `FAILED_RETRYABLE`, and `FAILED_PERMANENT`. A transaction claims batches; startup/sync recovery moves abandoned `SYNCING` rows back to retryable. The backend unique index is the final idempotency boundary.

The first-install cutoff is read from package metadata and persisted once. App updates preserve both sources. Clearing data removes the local copy, but package first-install time is used again.

## Upgrade behavior

- Room remains at schema version 1 because call entities never stored authentication data. Existing pending and synced rows therefore survive an APK update without a destructive migration.
- Startup removes the obsolete encrypted credential preferences and legacy Android Keystore alias from older app versions.
- Backups and device transfer remain disabled. Diagnostics show external call ID/type/status/error, not full phone numbers.

## Failure policy

| Outcome | Local handling |
|---|---|
| `created`, `duplicate` | `SYNCED` |
| `invalid`, `rejected`, HTTP 400 | permanent/visible |
| other HTTP 4xx | permanent/visible |
| timeout/network, HTTP 429/5xx | retryable and retained |
| interrupted `SYNCING` | recovered to retryable |

`last_successful_sync_at` advances only after an HTTP exchange whose batch has no failures. An empty local queue does not pretend that a server exchange occurred.

## Automatic background synchronization

`PhoneStateReceiver` is the primary Activity-independent trigger. It records only `RINGING`, `OFFHOOK`, and `IDLE` state and enqueues `smartliving-call-state-sync` when a call returns to `IDLE`; it never uses a number from the broadcast. `CallLogObserver` is a secondary process-alive trigger. Both call `SyncWorkScheduler.enqueueCallLogSync`, which replaces duplicate pending work, waits four seconds for CallLog finalization, requires a connected network, and runs the existing `CallSyncWorker`/`SyncCoordinator` pipeline.

A unique network-constrained periodic request runs every 15 minutes as recovery. `BootReceiver` restores that periodic request after reboot when automatic sync is enabled, a Device ID exists, and call-log permission is granted. Automatic sync defaults to enabled and is persisted in DataStore; disabling it cancels automatic one-time and periodic work without affecting manual Sync Now.

Android Force Stop is an OS-level exception: after the user explicitly force-stops the app in Settings, Android may suppress receivers and scheduled work until the app is launched or otherwise interacted with again. Normally closing the Activity or swiping it from Recents does not place the app in that stopped state.
