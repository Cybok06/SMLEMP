package com.cytech.smartlivingcallsync.domain

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CallLog
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class AndroidCallLogReader(
    private val context: Context,
    private val simResolver: SimResolver,
) : CallLogReader {
    override suspend fun readSince(cutoffMillis: Long): List<CallLogRecord> = withContext(Dispatchers.IO) {
        check(ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED) {
            "Call log permission is not granted"
        }
        val projection = arrayOf(
            CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.TYPE,
            CallLog.Calls.DATE, CallLog.Calls.DURATION, CallLog.Calls.PHONE_ACCOUNT_ID,
        )
        val output = mutableListOf<CallLogRecord>()
        context.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            projection,
            "${CallLog.Calls.DATE} >= ?",
            arrayOf(cutoffMillis.toString()),
            "${CallLog.Calls.DATE} ASC",
        )?.use { cursor ->
            val idColumn = cursor.getColumnIndexOrThrow(CallLog.Calls._ID)
            val numberColumn = cursor.getColumnIndexOrThrow(CallLog.Calls.NUMBER)
            val typeColumn = cursor.getColumnIndexOrThrow(CallLog.Calls.TYPE)
            val dateColumn = cursor.getColumnIndexOrThrow(CallLog.Calls.DATE)
            val durationColumn = cursor.getColumnIndexOrThrow(CallLog.Calls.DURATION)
            val accountColumn = cursor.getColumnIndexOrThrow(CallLog.Calls.PHONE_ACCOUNT_ID)
            while (cursor.moveToNext()) {
                val callType = mapAndroidCallType(cursor.getInt(typeColumn)) ?: continue
                val accountId = cursor.getString(accountColumn)
                val sim = simResolver.resolve(accountId)
                output += CallLogRecord(
                    externalCallId = cursor.getLong(idColumn).toString(),
                    phoneNumber = cursor.getString(numberColumn).orEmpty(),
                    callType = callType,
                    startedAt = epochMillisToIsoUtc(cursor.getLong(dateColumn)),
                    durationSeconds = cursor.getLong(durationColumn).coerceAtLeast(0),
                    simAccount = sim.accountId.take(160),
                    fromNumber = sim.lineNumber,
                )
            }
        }
        output
    }
}
