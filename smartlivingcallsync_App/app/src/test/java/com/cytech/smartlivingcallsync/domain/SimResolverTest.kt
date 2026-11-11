package com.cytech.smartlivingcallsync.domain

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class SimResolverTest {
    @Test fun singleSimResolvesAccountAndLine() = assertSim("SIM1", "0240000001")
    @Test fun simSlotOneResolves() = assertSim("slot-0", "0240000001")
    @Test fun simSlotTwoResolves() = assertSim("slot-1", "0550000002")
    @Test fun unavailableLineUsesBlankWithoutGuessing() = assertSim("SIM1", "")
    @Test fun permissionRestrictedUsesBlankWithoutCrashing() = assertSim("SIM2", "")

    private fun assertSim(account: String, line: String) = runTest {
        val resolver = object : SimResolver {
            override suspend fun resolve(phoneAccountId: String?) = SimInfo(phoneAccountId.orEmpty(), line)
        }
        assertEquals(SimInfo(account, line), resolver.resolve(account))
    }
}
