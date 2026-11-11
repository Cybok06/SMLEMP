package com.cytech.smartlivingcallsync.worker

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object SyncWorkScheduler {
    const val CALL_STATE_SYNC_WORK = "smartliving-call-state-sync"
    const val PERIODIC_SYNC_WORK = "smartliving-call-sync-periodic"
    const val CALL_LOG_FINALIZE_DELAY_SECONDS = 4L
    const val PERIODIC_FALLBACK_MINUTES = 15L

    fun buildCallLogSyncRequest() = OneTimeWorkRequestBuilder<CallSyncWorker>()
        .setConstraints(networkConstraints())
        .setInitialDelay(CALL_LOG_FINALIZE_DELAY_SECONDS, TimeUnit.SECONDS)
        .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
        .build()

    fun enqueueCallLogSync(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            CALL_STATE_SYNC_WORK, ExistingWorkPolicy.REPLACE, buildCallLogSyncRequest(),
        )
    }

    fun schedulePeriodicFallback(context: Context) {
        val request = PeriodicWorkRequestBuilder<CallSyncWorker>(PERIODIC_FALLBACK_MINUTES, TimeUnit.MINUTES)
            .setConstraints(networkConstraints())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_SYNC_WORK,
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }

    fun disableAutomaticSync(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(CALL_STATE_SYNC_WORK)
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_SYNC_WORK)
    }

    private fun networkConstraints() = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()
}
