package com.cytech.smartlivingcallsync.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class SyncCallDto(
    @SerialName("external_call_id") val externalCallId: String,
    @SerialName("phone_number") val phoneNumber: String,
    @SerialName("from_number") val fromNumber: String,
    @SerialName("call_type") val callType: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("duration_seconds") val durationSeconds: Long,
    @SerialName("sim_account") val simAccount: String,
)

@Serializable
data class SyncCallsRequest(
    @SerialName("device_id") val deviceId: String,
    val calls: List<SyncCallDto>,
)

@Serializable
data class SyncItemResult(
    val index: Int,
    @SerialName("external_call_id") val externalCallId: String? = null,
    val status: String,
    @SerialName("call_id") val callId: String? = null,
    val error: String? = null,
)

@Serializable
data class SyncCallsResponse(
    val success: Boolean,
    val created: Int = 0,
    val duplicates: Int = 0,
    val failed: Int = 0,
    val results: List<SyncItemResult> = emptyList(),
    val error: String? = null,
)
