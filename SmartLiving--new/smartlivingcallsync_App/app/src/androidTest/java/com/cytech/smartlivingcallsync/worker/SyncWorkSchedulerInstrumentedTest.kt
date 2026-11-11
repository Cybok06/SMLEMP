package com.cytech.smartlivingcallsync.worker

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.NetworkType
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class SyncWorkSchedulerInstrumentedTest {
    @Test fun immediateCallWorkRequiresNetworkAndFinalizationDelay() {
        val workSpec = SyncWorkScheduler.buildCallLogSyncRequest().workSpec
        assertEquals(NetworkType.CONNECTED, workSpec.constraints.requiredNetworkType)
        assertEquals(
            TimeUnit.SECONDS.toMillis(SyncWorkScheduler.CALL_LOG_FINALIZE_DELAY_SECONDS),
            workSpec.initialDelay,
        )
    }

    @Test fun recoveryIntervalUsesAndroidMinimum() {
        assertEquals(15L, SyncWorkScheduler.PERIODIC_FALLBACK_MINUTES)
    }
}
