# Test report

Date: 2026-08-13

## Automated coverage

- Android call-type mapping and unsupported-type skip
- installation cutoff boundary
- UTC ISO timestamp conversion and seconds preservation
- deterministic local sync key
- exact JSON field set with no customer identity
- MockWebServer exact path/header/body contract, including absence of `Authorization`
- created and duplicate success handling
- invalid and client-error permanent failure, offline retention
- maximum batch-size enforcement
- SIM1/SIM2, unavailable-number, and permission-restricted interface behavior
- Room duplicate insertion and interrupted-process recovery (instrumentation)
- granted, denied, and permanently denied/settings UI states (instrumentation)
- phone-state transitions: ringing-to-idle, answered ringing/off-hook-to-idle, and outbound off-hook-to-idle
- repeated-idle suppression plus automatic-sync disabled and missing-Device-ID gates
- WorkManager connected-network constraint, four-second CallLog finalization delay, and 15-minute fallback configuration (instrumentation)

## Latest local result

- `./gradlew test`: passed
- `./gradlew assembleDebug assembleAndroidTest`: passed; APK produced at `app/build/outputs/apk/debug/app-debug.apk`
- Instrumentation sources and APK compiled successfully. `adb devices` reported no connected devices, so on-device execution was not attempted.

## Required physical validation

Not yet claimed as complete. A configured device ID and a controlled company phone are required. Validate outbound, inbound, missed/zero-duration, SIM1, SIM2, unavailable line number, matched/unknown Ghana numbers, repeated sync, offline/reconnect, process restart, APK update with a pending queue, and confirmation that no pre-install calls appear. Confirm the existing web Calls Management UI shows Android / Needs Update and expected device/SIM/customer/type/duration fields.

Also validate automatic delivery with the Activity closed normally and after swiping it from Recents. Android's explicit Settings > Apps > Force Stop places an app in the stopped state and can suppress receivers/WorkManager until the user launches or interacts with it again; this app does not and cannot bypass that platform behavior.
