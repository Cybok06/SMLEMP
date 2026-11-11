package com.cytech.smartlivingcallsync.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.cytech.smartlivingcallsync.data.local.AppPreferences
import com.cytech.smartlivingcallsync.worker.SyncWorkScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val pendingResult = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                val settings = AppPreferences(context.applicationContext).settings.first()
                if (AutomaticSyncGate.canSchedule(settings.automaticSyncEnabled, settings.deviceId) &&
                    hasCallLogPermission(context)
                ) {
                    SyncWorkScheduler.schedulePeriodicFallback(context.applicationContext)
                } else {
                    SyncWorkScheduler.disableAutomaticSync(context.applicationContext)
                }
            } finally {
                pendingResult.finish()
            }
        }
    }
}
