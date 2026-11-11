package com.cytech.smartlivingcallsync.data.local

import androidx.room.Entity
import androidx.room.PrimaryKey

enum class SyncStatus { PENDING, SYNCING, SYNCED, FAILED_RETRYABLE, FAILED_PERMANENT }

@Entity(tableName = "synced_calls")
data class SyncedCallEntity(
    @PrimaryKey val syncKey: String,
    val deviceId: String,
    val externalCallId: String,
    val phoneNumber: String,
    val fromNumber: String,
    val callType: String,
    val startedAt: String,
    val durationSeconds: Long,
    val simAccount: String,
    val syncStatus: SyncStatus = SyncStatus.PENDING,
    val attemptCount: Int = 0,
    val lastError: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
    val syncedAt: Long? = null,
)
