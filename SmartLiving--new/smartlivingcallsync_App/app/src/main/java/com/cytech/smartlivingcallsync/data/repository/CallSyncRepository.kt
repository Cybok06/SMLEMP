package com.cytech.smartlivingcallsync.data.repository

import com.cytech.smartlivingcallsync.data.local.CallDao
import com.cytech.smartlivingcallsync.data.local.SyncStatus
import com.cytech.smartlivingcallsync.data.local.SyncedCallEntity
import com.cytech.smartlivingcallsync.data.remote.SmartLivingApi
import com.cytech.smartlivingcallsync.data.remote.SyncCallDto
import com.cytech.smartlivingcallsync.data.remote.SyncCallsRequest
import com.cytech.smartlivingcallsync.domain.CallLogRecord
import com.cytech.smartlivingcallsync.domain.localSyncKey
import kotlinx.coroutines.flow.Flow
import java.io.IOException

data class SyncSummary(
    val created: Int = 0,
    val duplicates: Int = 0,
    val failed: Int = 0,
    val error: String? = null,
) {
    operator fun plus(other: SyncSummary) = SyncSummary(
        created + other.created, duplicates + other.duplicates, failed + other.failed,
        other.error ?: error,
    )
}

class CallSyncRepository(
    private val dao: CallDao,
    private val api: SmartLivingApi,
) {
    val pendingCount: Flow<Int> = dao.observePendingCount()
    val diagnostics: Flow<List<SyncedCallEntity>> = dao.observeDiagnostics()

    suspend fun recoverInterrupted() = dao.recoverInterrupted()
    suspend fun enqueue(deviceId: String, calls: List<CallLogRecord>) {
        require(deviceId.isNotBlank()) { "Device ID is required before calls can be queued." }
        dao.insertAll(calls.map { call ->
            SyncedCallEntity(
                syncKey = localSyncKey(deviceId, call.externalCallId), deviceId = deviceId,
                externalCallId = call.externalCallId, phoneNumber = call.phoneNumber,
                fromNumber = call.fromNumber, callType = call.callType.wireValue,
                startedAt = call.startedAt, durationSeconds = call.durationSeconds,
                simAccount = call.simAccount,
            )
        })
    }

    suspend fun syncQueued(deviceId: String, batchSize: Int = 100): SyncSummary {
        require(deviceId.isNotBlank()) { "Device ID is required before calls can be synchronized." }
        require(batchSize in 1..500)
        var total = SyncSummary()
        while (true) {
            val batch = dao.claimNextBatch(deviceId, batchSize)
            if (batch.isEmpty()) return total
            val summary = sendBatch(deviceId, batch)
            total += summary
            if (summary.error != null) return total
        }
    }

    private suspend fun sendBatch(deviceId: String, batch: List<SyncedCallEntity>): SyncSummary {
        val response = try {
            api.syncCalls(SyncCallsRequest(deviceId, batch.map { it.toDto() }))
        } catch (error: IOException) {
            batch.forEach { dao.markFailed(it.syncKey, SyncStatus.FAILED_RETRYABLE, "Network unavailable; queued for retry") }
            return SyncSummary(failed = batch.size, error = "Network unavailable. Calls remain queued.")
        } catch (error: Exception) {
            batch.forEach { dao.markFailed(it.syncKey, SyncStatus.FAILED_RETRYABLE, "Unexpected transport error") }
            return SyncSummary(failed = batch.size, error = "Synchronization could not be completed.")
        }
        if (!response.isSuccessful) {
            val code = response.code()
            val retryable = code == 429 || code >= 500
            val status = if (retryable) SyncStatus.FAILED_RETRYABLE else SyncStatus.FAILED_PERMANENT
            val safeError = when {
                code == 400 -> "Server rejected the call data (HTTP 400)."
                retryable -> "SmartLiving is temporarily unavailable (HTTP $code)."
                else -> "SmartLiving returned HTTP $code."
            }
            batch.forEach { dao.markFailed(it.syncKey, status, safeError) }
            return SyncSummary(failed = batch.size, error = safeError)
        }
        val body = response.body()
        if (body == null) {
            batch.forEach { dao.markFailed(it.syncKey, SyncStatus.FAILED_RETRYABLE, "Empty server response") }
            return SyncSummary(failed = batch.size, error = "SmartLiving returned an empty response.")
        }
        var created = 0; var duplicates = 0; var failed = 0
        val byIndex = body.results.associateBy { it.index }
        batch.forEachIndexed { index, item ->
            val result = byIndex[index]
            when (result?.status) {
                "created" -> { dao.markSynced(item.syncKey, System.currentTimeMillis()); created++ }
                "duplicate" -> { dao.markSynced(item.syncKey, System.currentTimeMillis()); duplicates++ }
                "invalid", "rejected" -> {
                    dao.markFailed(item.syncKey, SyncStatus.FAILED_PERMANENT, result.error ?: result.status)
                    failed++
                }
                else -> {
                    dao.markFailed(item.syncKey, SyncStatus.FAILED_RETRYABLE, "Unrecognized or missing item result")
                    failed++
                }
            }
        }
        return SyncSummary(created, duplicates, failed, if (failed > 0) "Some calls require attention." else null)
    }

    private fun SyncedCallEntity.toDto() = SyncCallDto(
        externalCallId, phoneNumber, fromNumber, callType, startedAt,
        durationSeconds.coerceAtLeast(0), simAccount.take(160),
    )
}
