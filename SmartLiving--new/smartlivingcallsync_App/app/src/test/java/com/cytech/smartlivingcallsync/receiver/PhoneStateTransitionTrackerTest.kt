package com.cytech.smartlivingcallsync.receiver

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneStateTransitionTrackerTest {
    @Test fun `ringing then idle schedules synchronization`() {
        assertTrue(PhoneStateTransitionTracker.shouldSchedule(ringing, idle))
    }

    @Test fun `ringing offhook idle schedules only at final transition`() {
        assertFalse(PhoneStateTransitionTracker.shouldSchedule(ringing, offhook))
        assertTrue(PhoneStateTransitionTracker.shouldSchedule(offhook, idle))
    }

    @Test fun `offhook then idle schedules synchronization`() {
        assertTrue(PhoneStateTransitionTracker.shouldSchedule(offhook, idle))
    }

    @Test fun `repeated idle does not schedule duplicate work`() {
        assertFalse(PhoneStateTransitionTracker.shouldSchedule(idle, idle))
    }

    @Test fun `disabled automatic sync cannot schedule`() {
        assertFalse(AutomaticSyncGate.canSchedule(false, "support-phone-01"))
    }

    @Test fun `missing device id cannot schedule`() {
        assertFalse(AutomaticSyncGate.canSchedule(true, "  "))
    }

    @Test fun `enabled automatic sync with device id can schedule`() {
        assertTrue(AutomaticSyncGate.canSchedule(true, "support-phone-01"))
    }

    private companion object {
        const val ringing = PhoneStateTransitionTracker.RINGING
        const val offhook = PhoneStateTransitionTracker.OFFHOOK
        const val idle = PhoneStateTransitionTracker.IDLE
    }
}
