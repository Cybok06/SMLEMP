package com.cytech.smartlivingcallsync.domain

import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class DebouncedSyncTriggerTest {
    @Test
    fun `rapid notifications produce one synchronization`() = runTest {
        var executions = 0
        val trigger = DebouncedSyncTrigger(this, 3_000) { executions++ }

        trigger.signal()
        advanceTimeBy(1_000)
        trigger.signal()
        advanceTimeBy(1_000)
        trigger.signal()
        advanceTimeBy(3_001)

        assertEquals(1, executions)
    }

    @Test
    fun `cancel prevents synchronization`() = runTest {
        var executions = 0
        val trigger = DebouncedSyncTrigger(this, 3_000) { executions++ }
        trigger.signal()
        trigger.cancel()
        advanceTimeBy(3_001)
        assertEquals(0, executions)
    }
}
