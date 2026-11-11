package com.cytech.smartlivingcallsync.worker

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.cytech.smartlivingcallsync.SmartLivingApplication
import com.cytech.smartlivingcallsync.domain.SyncOutcome

class CallSyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val coordinator = (applicationContext as SmartLivingApplication).container.coordinator
        return when (val outcome = coordinator.synchronize()) {
            is SyncOutcome.Completed -> when {
                outcome.summary.error != null -> Result.retry()
                else -> Result.success()
            }
            is SyncOutcome.ConfigurationError -> Result.failure()
            is SyncOutcome.Failed -> Result.retry()
        }
    }
}
