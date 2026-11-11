package com.cytech.smartlivingcallsync.receiver

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import com.cytech.smartlivingcallsync.data.local.AppPreferences
import com.cytech.smartlivingcallsync.worker.SyncWorkScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class PhoneStateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return
        val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
        val stateStore = context.getSharedPreferences(STATE_PREFERENCES, Context.MODE_PRIVATE)
        val previous = stateStore.getString(PREVIOUS_STATE, null)
        stateStore.edit().putString(PREVIOUS_STATE, state).apply()
        if (!PhoneStateTransitionTracker.shouldSchedule(previous, state)) return

        val pendingResult = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                val settings = AppPreferences(context.applicationContext).settings.first()
                if (AutomaticSyncGate.canSchedule(settings.automaticSyncEnabled, settings.deviceId) &&
                    hasCallLogPermission(context)
                ) {
                    SyncWorkScheduler.enqueueCallLogSync(context.applicationContext)
                    SyncWorkScheduler.schedulePeriodicFallback(context.applicationContext)
                }
            } finally {
                pendingResult.finish()
            }
        }
    }

    private companion object {
        const val STATE_PREFERENCES = "phone_state_receiver"
        const val PREVIOUS_STATE = "previous_state"
    }
}

object PhoneStateTransitionTracker {
    const val RINGING = "RINGING"
    const val OFFHOOK = "OFFHOOK"
    const val IDLE = "IDLE"

    fun shouldSchedule(previous: String?, current: String): Boolean =
        current == IDLE && previous in setOf(RINGING, OFFHOOK)
}

object AutomaticSyncGate {
    fun canSchedule(enabled: Boolean, deviceId: String?): Boolean =
        enabled && !deviceId.isNullOrBlank()
}

fun hasCallLogPermission(context: Context): Boolean =
    ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALL_LOG) ==
        PackageManager.PERMISSION_GRANTED
