package com.cytech.smartlivingcallsync.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.cytech.smartlivingcallsync.data.local.SyncedCallEntity
import com.cytech.smartlivingcallsync.domain.AutomaticSyncState
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

enum class PermissionUiState { ALLOWED, DENIED, PERMANENTLY_DENIED }
enum class ConnectionUiState { CONNECTED, OFFLINE, NOT_CHECKED }

@Composable
@OptIn(ExperimentalMaterial3Api::class)
fun MainScreen(
    state: MainUiState,
    permissionState: PermissionUiState,
    connectionState: ConnectionUiState,
    onRequestPermission: () -> Unit,
    onOpenSettings: () -> Unit,
    onSaveDeviceSettings: (String, String, (String?) -> Unit) -> Unit,
    onAutomaticSyncChange: (Boolean) -> Unit,
    onSync: () -> Unit,
) {
    var diagnostics by remember { mutableStateOf(false) }
    Scaffold(topBar = { TopAppBar(title = { Text("SmartLiving Call Sync") }) }) { padding ->
        if (diagnostics) {
            DiagnosticsScreen(Modifier.padding(padding), state.diagnostics) { diagnostics = false }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                item {
                    StatusCard("Connection status", when (connectionState) {
                        ConnectionUiState.CONNECTED -> "Connected"
                        ConnectionUiState.OFFLINE -> "Offline"
                        ConnectionUiState.NOT_CHECKED -> "Not checked"
                    })
                }
                item { DeviceSettingsCard(state.deviceName.orEmpty(), state.deviceId.orEmpty(), onSaveDeviceSettings) }
                item { AutomaticSyncCard(state, connectionState, onAutomaticSyncChange) }
                item { StatusCard("Background Trigger", backgroundTriggerLabel(state.automaticSyncState)) }
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Call Log Permission", style = MaterialTheme.typography.labelLarge)
                            Text(permissionLabel(permissionState), style = MaterialTheme.typography.titleMedium)
                            if (permissionState != PermissionUiState.ALLOWED) {
                                Spacer(Modifier.height(8.dp))
                                Text("SmartLiving reads calls made after installation so they can be queued for support review.")
                                Spacer(Modifier.height(8.dp))
                                if (permissionState == PermissionUiState.PERMANENTLY_DENIED) {
                                    OutlinedButton(onClick = onOpenSettings) { Text("Open App Settings") }
                                } else {
                                    OutlinedButton(onClick = onRequestPermission) { Text("Grant Permission") }
                                }
                            }
                        }
                    }
                }
                item { StatusCard("Last Sync", formatLastSync(state.lastSuccessfulSyncAt)) }
                item { StatusCard("Pending Calls", state.pendingCount.toString()) }
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text("Last Result", style = MaterialTheme.typography.labelLarge)
                            Text(state.lastResult)
                        }
                    }
                }
                item {
                    Button(
                        onClick = onSync,
                        enabled = !state.syncing && permissionState == PermissionUiState.ALLOWED,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (state.syncing) {
                            CircularProgressIndicator(Modifier.height(20.dp), strokeWidth = 2.dp)
                            Text("  Synchronizing…")
                        } else Text("Sync Now")
                    }
                }
                item {
                    OutlinedButton(onClick = { diagnostics = true }, modifier = Modifier.fillMaxWidth()) {
                        Text("Pending & Errors")
                    }
                    Spacer(Modifier.height(24.dp))
                }
            }
        }
    }
}

@Composable
private fun StatusCard(label: String, value: String) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(label, style = MaterialTheme.typography.labelLarge)
            Text(value, style = MaterialTheme.typography.titleMedium)
        }
    }
}

@Composable
private fun AutomaticSyncCard(
    state: MainUiState,
    connectionState: ConnectionUiState,
    onAutomaticSyncChange: (Boolean) -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text("Automatic Sync", style = MaterialTheme.typography.labelLarge)
                Text(automaticSyncLabel(state.automaticSyncState, connectionState), style = MaterialTheme.typography.titleMedium)
            }
            Switch(checked = state.automaticSyncEnabled, onCheckedChange = onAutomaticSyncChange)
        }
    }
}

@Composable
fun DeviceSettingsCard(
    initialDeviceName: String,
    initialDeviceId: String,
    onSave: (String, String, (String?) -> Unit) -> Unit,
) {
    var deviceId by remember(initialDeviceId) { mutableStateOf(initialDeviceId) }
    var deviceName by remember(initialDeviceName) { mutableStateOf(initialDeviceName) }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf(false) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(deviceName, {
                deviceName = it
                saved = false
            }, label = { Text("Device Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(deviceId, {
                deviceId = it
                saved = false
            }, label = { Text("Device ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            OutlinedButton(
                onClick = {
                    saving = true
                    error = null
                    onSave(deviceId, deviceName) { saveError ->
                        saving = false
                        error = saveError
                        saved = saveError == null
                    }
                },
                enabled = !saving,
            ) {
                if (saving) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text("  Saving…")
                } else {
                    Text(if (saved) "Saved" else "Save Device Settings")
                }
            }
        }
    }
}

@Composable
fun DiagnosticsScreen(modifier: Modifier, records: List<SyncedCallEntity>, onBack: () -> Unit) {
    Column(modifier.fillMaxSize().padding(20.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("Pending & Errors", style = MaterialTheme.typography.headlineSmall)
            OutlinedButton(onClick = onBack) { Text("Back") }
        }
        Text("Full phone numbers are not displayed here.")
        Spacer(Modifier.height(12.dp))
        if (records.isEmpty()) Text("No pending or failed calls.")
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(records, key = { it.syncKey }) { record ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text("Call ${record.externalCallId} · ${record.callType}")
                        Text(record.syncStatus.name)
                        record.lastError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    }
                }
            }
        }
    }
}

private fun permissionLabel(state: PermissionUiState) = when (state) {
    PermissionUiState.ALLOWED -> "Allowed"
    PermissionUiState.DENIED -> "Denied"
    PermissionUiState.PERMANENTLY_DENIED -> "Permanently denied"
}

private fun automaticSyncLabel(state: AutomaticSyncState, connection: ConnectionUiState) = when {
    state == AutomaticSyncState.ACTIVE && connection == ConnectionUiState.OFFLINE -> "Temporarily Offline · calls will be queued"
    state == AutomaticSyncState.ACTIVE -> "● Active · watching for new calls"
    state == AutomaticSyncState.PERMISSION_REQUIRED -> "Permission Required"
    state == AutomaticSyncState.DEVICE_SETUP_REQUIRED -> "Device Setup Required"
    state == AutomaticSyncState.DISABLED -> "Disabled"
    else -> "Error"
}

private fun backgroundTriggerLabel(state: AutomaticSyncState) = when (state) {
    AutomaticSyncState.ACTIVE -> "Ready"
    AutomaticSyncState.PERMISSION_REQUIRED -> "Permission Required"
    AutomaticSyncState.DEVICE_SETUP_REQUIRED -> "Device ID Required"
    AutomaticSyncState.DISABLED -> "Disabled"
    AutomaticSyncState.ERROR -> "Error"
}

private fun formatLastSync(value: Long?): String = value?.let {
    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss z").withZone(ZoneId.systemDefault()).format(Instant.ofEpochMilli(it))
} ?: "Never"
