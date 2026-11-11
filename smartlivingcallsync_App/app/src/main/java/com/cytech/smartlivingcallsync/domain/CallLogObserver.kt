package com.cytech.smartlivingcallsync.domain

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.CallLog
import androidx.core.content.ContextCompat
import com.cytech.smartlivingcallsync.data.local.AppPreferences
import com.cytech.smartlivingcallsync.worker.SyncWorkScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

enum class AutomaticSyncState { ACTIVE, PERMISSION_REQUIRED, DEVICE_SETUP_REQUIRED, DISABLED, ERROR }

/** Process-scoped secondary trigger. All actual synchronization runs through WorkManager. */
class CallLogObserver(
    context: Context,
    private val preferences: AppPreferences,
) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow(AutomaticSyncState.PERMISSION_REQUIRED)
    val state: StateFlow<AutomaticSyncState> = _state.asStateFlow()
    private val _lastResult = MutableStateFlow<String?>(null)
    val lastResult: StateFlow<String?> = _lastResult.asStateFlow()
    private var registered = false
    private var configuredDeviceId = ""
    private var automaticSyncEnabled = true
    private val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) {
            if (!registered) return
            _lastResult.value = "Background synchronization scheduled"
            SyncWorkScheduler.enqueueCallLogSync(appContext)
        }
    }

    init {
        scope.launch {
            preferences.settings.collectLatest {
                configuredDeviceId = it.deviceId?.trim().orEmpty()
                automaticSyncEnabled = it.automaticSyncEnabled
                refresh()
            }
        }
    }

    /** Safe to call repeatedly, including after permission results and Activity resumes. */
    @Synchronized
    fun refresh() {
        val allowed = ContextCompat.checkSelfPermission(appContext, Manifest.permission.READ_CALL_LOG) ==
            PackageManager.PERMISSION_GRANTED
        val desired = automaticSyncEnabled && allowed && configuredDeviceId.isNotBlank()
        if (desired && !registered) {
            try {
                appContext.contentResolver.registerContentObserver(CallLog.Calls.CONTENT_URI, true, observer)
                registered = true
                _state.value = AutomaticSyncState.ACTIVE
                SyncWorkScheduler.schedulePeriodicFallback(appContext)
            } catch (_: SecurityException) {
                registered = false
                _state.value = AutomaticSyncState.PERMISSION_REQUIRED
            } catch (_: RuntimeException) {
                registered = false
                _state.value = AutomaticSyncState.ERROR
            }
        } else if (!desired) {
            unregister()
            _state.value = when {
                !automaticSyncEnabled -> AutomaticSyncState.DISABLED
                !allowed -> AutomaticSyncState.PERMISSION_REQUIRED
                else -> AutomaticSyncState.DEVICE_SETUP_REQUIRED
            }
            SyncWorkScheduler.disableAutomaticSync(appContext)
        }
    }

    @Synchronized
    private fun unregister() {
        if (registered) runCatching { appContext.contentResolver.unregisterContentObserver(observer) }
        registered = false
    }
}
