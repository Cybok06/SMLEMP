package com.cytech.smartlivingcallsync.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

@Dao
interface CallDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertAll(calls: List<SyncedCallEntity>): List<Long>

    @Query("SELECT * FROM synced_calls WHERE deviceId = :deviceId AND syncStatus IN ('PENDING','FAILED_RETRYABLE') ORDER BY startedAt ASC LIMIT :limit")
    suspend fun nextBatch(deviceId: String, limit: Int): List<SyncedCallEntity>

    @Query("SELECT COUNT(*) FROM synced_calls WHERE syncStatus != 'SYNCED'")
    fun observePendingCount(): Flow<Int>

    @Query("SELECT * FROM synced_calls WHERE syncStatus != 'SYNCED' ORDER BY createdAt DESC LIMIT :limit")
    fun observeDiagnostics(limit: Int = 50): Flow<List<SyncedCallEntity>>

    @Query("UPDATE synced_calls SET syncStatus = 'SYNCING', attemptCount = attemptCount + 1, lastError = NULL WHERE syncKey IN (:keys)")
    suspend fun markSyncing(keys: List<String>)

    @Query("UPDATE synced_calls SET syncStatus = 'SYNCED', syncedAt = :at, lastError = NULL WHERE syncKey = :key")
    suspend fun markSynced(key: String, at: Long)

    @Query("UPDATE synced_calls SET syncStatus = :status, lastError = :error WHERE syncKey = :key")
    suspend fun markFailed(key: String, status: SyncStatus, error: String)

    @Query("UPDATE synced_calls SET syncStatus = 'FAILED_RETRYABLE', lastError = :reason WHERE syncStatus = 'SYNCING'")
    suspend fun recoverInterrupted(reason: String = "Previous synchronization was interrupted")

    @Transaction
    suspend fun claimNextBatch(deviceId: String, limit: Int): List<SyncedCallEntity> {
        val batch = nextBatch(deviceId, limit)
        if (batch.isNotEmpty()) markSyncing(batch.map { it.syncKey })
        return batch
    }
}
