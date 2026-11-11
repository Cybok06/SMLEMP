package com.cytech.smartlivingcallsync.data.repository

import com.cytech.smartlivingcallsync.data.local.CallDao
import com.cytech.smartlivingcallsync.data.local.SyncStatus
import com.cytech.smartlivingcallsync.data.local.SyncedCallEntity
import com.cytech.smartlivingcallsync.data.remote.SmartLivingApi
import com.cytech.smartlivingcallsync.data.remote.SyncCallsRequest
import com.cytech.smartlivingcallsync.data.remote.SyncCallsResponse
import com.cytech.smartlivingcallsync.data.remote.SyncItemResult
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response
import java.io.IOException

class CallSyncRepositoryTest {
    @Test fun createdAndDuplicateBothMarkRowsSynced() = runTest {
        val dao = FakeDao(twoRows())
        val api = FakeApi { Response.success(SyncCallsResponse(true, 1, 1, 0, listOf(
            SyncItemResult(0, "1", "created"), SyncItemResult(1, "2", "duplicate")
        ))) }
        val result = CallSyncRepository(dao, api).syncQueued(DEVICE)
        assertEquals(1, result.created); assertEquals(1, result.duplicates)
        assertTrue(dao.rows.all { it.syncStatus == SyncStatus.SYNCED })
    }

    @Test fun invalidStaysVisibleAndDoesNotRetryForever() = runTest {
        val dao = FakeDao(listOf(row("1")))
        val api = FakeApi { Response.success(SyncCallsResponse(false, failed = 1, results = listOf(
            SyncItemResult(0, "1", "invalid", error = "duration_seconds is invalid")
        ))) }
        val result = CallSyncRepository(dao, api).syncQueued(DEVICE)
        assertEquals(1, result.failed)
        assertEquals(SyncStatus.FAILED_PERMANENT, dao.rows.single().syncStatus)
    }

    @Test fun offlineFailureRemainsQueuedForRetry() = runTest {
        val dao = FakeDao(listOf(row("1")))
        val api = FakeApi { throw IOException("offline") }
        CallSyncRepository(dao, api).syncQueued(DEVICE)
        assertEquals(SyncStatus.FAILED_RETRYABLE, dao.rows.single().syncStatus)
    }

    @Test fun clientErrorIsPermanentWithoutAuthenticationHandling() = runTest {
        val dao = FakeDao(listOf(row("1")))
        val api = FakeApi { Response.error(401, okhttp3.ResponseBody.create(null, "{}")) }
        val result = CallSyncRepository(dao, api).syncQueued(DEVICE)
        assertEquals("SmartLiving returned HTTP 401.", result.error)
        assertEquals(SyncStatus.FAILED_PERMANENT, dao.rows.single().syncStatus)
    }

    @Test fun blankDeviceIdBlocksSyncBeforeNetworkRequest() = runTest {
        var requested = false
        val repository = CallSyncRepository(FakeDao(emptyList()), FakeApi {
            requested = true
            error("must not be called")
        })
        val error = runCatching { repository.syncQueued("   ") }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message.orEmpty().contains("Device ID is required"))
        assertEquals(false, requested)
    }

    @Test(expected = IllegalArgumentException::class)
    fun batchSizeNeverExceeds500() = runTest {
        CallSyncRepository(FakeDao(emptyList()), FakeApi { error("not called") }).syncQueued(DEVICE, 501)
    }

    private class FakeApi(private val action: suspend (SyncCallsRequest) -> Response<SyncCallsResponse>) : SmartLivingApi {
        override suspend fun syncCalls(request: SyncCallsRequest) = action(request)
    }

    private class FakeDao(initial: List<SyncedCallEntity>) : CallDao {
        val rows = initial.toMutableList()
        private val count = MutableStateFlow(rows.count { it.syncStatus != SyncStatus.SYNCED })
        override suspend fun insertAll(calls: List<SyncedCallEntity>): List<Long> = calls.map { call ->
            if (rows.any { it.syncKey == call.syncKey }) -1 else { rows += call; 1 }
        }
        override suspend fun nextBatch(deviceId: String, limit: Int) = rows.filter {
            it.deviceId == deviceId && it.syncStatus in setOf(SyncStatus.PENDING, SyncStatus.FAILED_RETRYABLE)
        }.take(limit)
        override fun observePendingCount(): Flow<Int> = count
        override fun observeDiagnostics(limit: Int): Flow<List<SyncedCallEntity>> = MutableStateFlow(rows.take(limit))
        override suspend fun markSyncing(keys: List<String>) = update(keys) { it.copy(syncStatus = SyncStatus.SYNCING, attemptCount = it.attemptCount + 1) }
        override suspend fun markSynced(key: String, at: Long) = update(listOf(key)) { it.copy(syncStatus = SyncStatus.SYNCED, syncedAt = at) }
        override suspend fun markFailed(key: String, status: SyncStatus, error: String) = update(listOf(key)) { it.copy(syncStatus = status, lastError = error) }
        override suspend fun recoverInterrupted(reason: String) = update(rows.filter { it.syncStatus == SyncStatus.SYNCING }.map { it.syncKey }) { it.copy(syncStatus = SyncStatus.FAILED_RETRYABLE) }
        override suspend fun claimNextBatch(deviceId: String, limit: Int): List<SyncedCallEntity> {
            val batch = nextBatch(deviceId, limit); markSyncing(batch.map { it.syncKey }); return batch
        }
        private fun update(keys: List<String>, change: (SyncedCallEntity) -> SyncedCallEntity) {
            rows.indices.forEach { i -> if (rows[i].syncKey in keys) rows[i] = change(rows[i]) }
            count.value = rows.count { it.syncStatus != SyncStatus.SYNCED }
        }
    }

    companion object {
        const val DEVICE = "support-phone-01"
        fun row(id: String) = SyncedCallEntity("$DEVICE:$id", DEVICE, id, "0530000000", "", "outbound", "2026-08-12T18:49:00Z", 10, "SIM1")
        fun twoRows() = listOf(row("1"), row("2"))
    }
}
