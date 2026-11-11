package com.cytech.smartlivingcallsync.domain

import android.provider.CallLog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelsTest {
    @Test fun incomingMapsToInbound() = assertEquals(CallType.INBOUND, mapAndroidCallType(CallLog.Calls.INCOMING_TYPE))
    @Test fun outgoingMapsToOutbound() = assertEquals(CallType.OUTBOUND, mapAndroidCallType(CallLog.Calls.OUTGOING_TYPE))
    @Test fun missedMapsToMissed() = assertEquals(CallType.MISSED, mapAndroidCallType(CallLog.Calls.MISSED_TYPE))
    @Test fun unsupportedTypeIsSkipped() = assertNull(mapAndroidCallType(CallLog.Calls.REJECTED_TYPE))
    @Test fun installationCutoffExcludesOlderCalls() {
        assertFalse(isAtOrAfterInstallationCutoff(999, 1_000))
        assertTrue(isAtOrAfterInstallationCutoff(1_000, 1_000))
    }
    @Test fun isoTimestampIsUtcAndExact() = assertEquals("2026-08-12T18:49:00Z", epochMillisToIsoUtc(1_786_560_540_000L))
    @Test fun durationRemainsSeconds() {
        val record = CallLogRecord("1", "0530", CallType.OUTBOUND, "2026-08-12T18:49:00Z", 332, "SIM1", "")
        assertEquals(332, record.durationSeconds)
    }
    @Test fun localKeyIsDeterministic() = assertEquals("support-phone-01:7821", localSyncKey("support-phone-01", "7821"))
    @Test fun blankDeviceIdHasActionableValidationError() {
        assertTrue(validateDeviceId("   ")?.contains("Device ID is required") == true)
        assertNull(validateDeviceId("HQ-Phone"))
    }
}
