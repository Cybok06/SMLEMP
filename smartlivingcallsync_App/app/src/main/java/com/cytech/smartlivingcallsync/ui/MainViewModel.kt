package com.cytech.smartlivingcallsync.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.cytech.smartlivingcallsync.SmartLivingApplication
import com.cytech.smartlivingcallsync.data.local.SyncedCallEntity
import com.cytech.smartlivingcallsync.domain.SyncOutcome
import com.cytech.smartlivingcallsync.domain.AutomaticSyncState
import com.cytech.smartlivingcallsync.worker.SyncWorkScheduler
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class MainUiState(
    val deviceId: String? = null,
    val deviceName: String? = null,
    val lastSuccessfulSyncAt: Long? = null,
    val pendingCount: Int = 0,
    val diagnostics: List<SyncedCallEntity> = emptyList(),
    val syncing: Boolean = false,
    val lastResult: String = "Not synchronized yet",
    val automaticSyncState: AutomaticSyncState = AutomaticSyncState.PERMISSION_REQUIRED,
    val automaticSyncEnabled: Boolean = true,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as SmartLivingApplication
    private val container = app.container
    private val operation = MutableStateFlow(OperationState())
    private val automatic = combine(
        container.callLogObserver.state,
        container.callLogObserver.lastResult,
    ) { state, result -> state to result }

    private data class OperationState(
        val syncing: Boolean = false,
        val result: String = "Not synchronized yet",
    )

    val uiState: StateFlow<MainUiState> = combine(
        container.preferences.settings,
        container.repository.pendingCount,
        container.repository.diagnostics,
        operation,
        automatic,
    ) { settings, pending, diagnostics, op, auto ->
        MainUiState(
            deviceId = settings.deviceId, deviceName = settings.deviceName,
            lastSuccessfulSyncAt = settings.lastSuccessfulSyncAt,
            pendingCount = pending, diagnostics = diagnostics,
            syncing = op.syncing, lastResult = auto.second ?: op.result,
            automaticSyncState = auto.first,
            automaticSyncEnabled = settings.automaticSyncEnabled,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), MainUiState())

    fun saveDeviceSettings(deviceId: String, deviceName: String, onComplete: (String?) -> Unit) {
        val cleanId = deviceId.trim(); val cleanName = deviceName.trim()
        if (cleanId.isBlank()) {
            onComplete("Device ID is required."); return
        }
        viewModelScope.launch {
            runCatching {
                container.preferences.saveDeviceSettings(cleanId, cleanName)
            }.onSuccess {
                container.callLogObserver.refresh()
                operation.value = OperationState(result = "Device settings saved. Ready to synchronize.")
                onComplete(null)
            }.onFailure { onComplete("Device settings could not be saved.") }
        }
    }

    fun synchronize(permissionAllowed: Boolean) {
        if (!permissionAllowed) {
            operation.value = operation.value.copy(result = "Grant call-log permission before synchronizing.")
            return
        }
        if (operation.value.syncing) return
        viewModelScope.launch {
            operation.value = OperationState(syncing = true, result = "Reading and synchronizing calls…")
            when (val result = container.coordinator.synchronize()) {
                is SyncOutcome.Completed -> {
                    val s = result.summary
                    val message = s.error ?: "Created ${s.created}, duplicate ${s.duplicates}, failed ${s.failed}"
                    operation.value = OperationState(result = message)
                    if (s.error != null && uiState.value.automaticSyncEnabled) {
                        SyncWorkScheduler.enqueueCallLogSync(app)
                    }
                }
                is SyncOutcome.ConfigurationError -> operation.value = OperationState(result = result.message)
                is SyncOutcome.Failed -> operation.value = OperationState(result = result.message)
            }
        }
    }

    fun refreshAutomaticSync() = container.callLogObserver.refresh()

    fun setAutomaticSyncEnabled(enabled: Boolean) {
        viewModelScope.launch { container.preferences.setAutomaticSyncEnabled(enabled) }
    }

    class Factory(private val application: Application) : ViewModelProvider.AndroidViewModelFactory(application)
}
