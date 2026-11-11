package com.cytech.smartlivingcallsync.domain

import android.provider.CallLog
import java.time.Instant
import java.time.format.DateTimeFormatter

enum class CallType(val wireValue: String) { INBOUND("inbound"), OUTBOUND("outbound"), MISSED("missed") }

fun mapAndroidCallType(type: Int): CallType? = when (type) {
    CallLog.Calls.INCOMING_TYPE -> CallType.INBOUND
    CallLog.Calls.OUTGOING_TYPE -> CallType.OUTBOUND
    CallLog.Calls.MISSED_TYPE -> CallType.MISSED
    else -> null
}

fun epochMillisToIsoUtc(value: Long): String = DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(value))
fun localSyncKey(deviceId: String, externalCallId: String): String = "$deviceId:$externalCallId"
fun isAtOrAfterInstallationCutoff(callDateMillis: Long, cutoffMillis: Long): Boolean = callDateMillis >= cutoffMillis

data class SimInfo(val accountId: String, val lineNumber: String = "")

data class CallLogRecord(
    val externalCallId: String,
    val phoneNumber: String,
    val callType: CallType,
    val startedAt: String,
    val durationSeconds: Long,
    val simAccount: String,
    val fromNumber: String,
)

interface CallLogReader { suspend fun readSince(cutoffMillis: Long): List<CallLogRecord> }
interface SimResolver { suspend fun resolve(phoneAccountId: String?): SimInfo }
