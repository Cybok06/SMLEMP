package com.cytech.smartlivingcallsync.domain

import android.content.Context
import com.cytech.smartlivingcallsync.data.local.AppPreferences
import com.cytech.smartlivingcallsync.data.repository.CallSyncRepository
import com.cytech.smartlivingcallsync.data.repository.SyncSummary
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex

sealed interface SyncOutcome {
    data class Completed(val discovered: Int, val summary: SyncSummary) : SyncOutcome
    data class ConfigurationError(val message: String) : SyncOutcome
    data class Failed(val message: String) : SyncOutcome
}

class SyncCoordinator(
    private val context: Context,
    private val preferences: AppPreferences,
    private val callLogReader: CallLogReader,
    private val repository: CallSyncRepository,
) {
    private val mutex = Mutex()

    suspend fun synchronize(): SyncOutcome {
        if (!mutex.tryLock()) return SyncOutcome.Failed("A synchronization is already running.")
        return try {
            val settings = preferences.settings.first()
            val deviceId = settings.deviceId?.trim().orEmpty()
            validateDeviceId(deviceId)?.let { return SyncOutcome.ConfigurationError(it) }
            val firstInstallTime = context.packageManager.getPackageInfo(context.packageName, 0).firstInstallTime
            val cutoff = preferences.installationCutoff(firstInstallTime)
            repository.recoverInterrupted()
            val calls = callLogReader.readSince(cutoff)
            repository.enqueue(deviceId, calls)
            val summary = repository.syncQueued(deviceId)
            if (summary.failed == 0 && summary.error == null && (summary.created + summary.duplicates) > 0) {
                preferences.recordSuccessfulSync(System.currentTimeMillis())
            }
            SyncOutcome.Completed(calls.size, summary)
        } catch (error: SecurityException) {
            SyncOutcome.Failed("Call-log or phone permission is required.")
        } catch (error: Exception) {
            SyncOutcome.Failed(error.message ?: "Synchronization failed.")
        } finally {
            mutex.unlock()
        }
    }
}

fun validateDeviceId(deviceId: String): String? =
    if (deviceId.isBlank()) "Device ID is required. Enter and save a Device ID before synchronizing." else null
