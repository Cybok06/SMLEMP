package com.cytech.smartlivingcallsync.domain

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Coalesces the several notifications Android emits while finalizing one call. */
class DebouncedSyncTrigger(
    private val scope: CoroutineScope,
    private val delayMillis: Long = 3_000,
    private val action: suspend () -> Unit,
) {
    private var job: Job? = null

    @Synchronized
    fun signal() {
        job?.cancel()
        job = scope.launch {
            delay(delayMillis)
            action()
        }
    }

    @Synchronized
    fun cancel() {
        job?.cancel()
        job = null
    }
}
