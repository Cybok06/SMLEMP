package com.cytech.smartlivingcallsync

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.cytech.smartlivingcallsync.ui.ConnectionUiState
import com.cytech.smartlivingcallsync.ui.MainScreen
import com.cytech.smartlivingcallsync.ui.MainViewModel
import com.cytech.smartlivingcallsync.ui.PermissionUiState
import com.cytech.smartlivingcallsync.ui.theme.SmartLivingCallSyncTheme

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels { MainViewModel.Factory(application) }
    private val permissionPrefs by lazy { getSharedPreferences("permission_state", MODE_PRIVATE) }
    private val resumeCounter = mutableStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            SmartLivingCallSyncTheme {
                val state by viewModel.uiState.collectAsState()
                var permissionState by remember { mutableStateOf(evaluatePermissionState()) }
                var connectionState by remember { mutableStateOf(ConnectionUiState.NOT_CHECKED) }
                val resumed by resumeCounter
                val permissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestMultiplePermissions(),
                ) {
                    permissionPrefs.edit().putBoolean("call_log_requested", true).apply()
                    permissionState = evaluatePermissionState()
                }
                LaunchedEffect(resumed) {
                    permissionState = evaluatePermissionState()
                    connectionState = currentConnectionState()
                    viewModel.refreshAutomaticSync()
                }
                LaunchedEffect(permissionState, state.deviceId) { viewModel.refreshAutomaticSync() }
                MainScreen(
                    state = state,
                    permissionState = permissionState,
                    connectionState = connectionState,
                    onRequestPermission = {
                        permissionLauncher.launch(
                            arrayOf(
                                Manifest.permission.READ_CALL_LOG,
                                Manifest.permission.READ_PHONE_STATE,
                                Manifest.permission.READ_PHONE_NUMBERS,
                            )
                        )
                    },
                    onOpenSettings = {
                        startActivity(
                            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                                data = Uri.fromParts("package", packageName, null)
                            }
                        )
                    },
                    onSaveDeviceSettings = viewModel::saveDeviceSettings,
                    onAutomaticSyncChange = viewModel::setAutomaticSyncEnabled,
                    onSync = {
                        connectionState = currentConnectionState()
                        viewModel.synchronize(permissionState == PermissionUiState.ALLOWED)
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        resumeCounter.value += 1
    }

    private fun evaluatePermissionState(): PermissionUiState {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED) {
            return PermissionUiState.ALLOWED
        }
        val requested = permissionPrefs.getBoolean("call_log_requested", false)
        return if (requested && !shouldShowRequestPermissionRationale(Manifest.permission.READ_CALL_LOG)) {
            PermissionUiState.PERMANENTLY_DENIED
        } else PermissionUiState.DENIED
    }

    private fun currentConnectionState(): ConnectionUiState {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return ConnectionUiState.NOT_CHECKED
        val network = manager.activeNetwork ?: return ConnectionUiState.OFFLINE
        val capabilities = manager.getNetworkCapabilities(network) ?: return ConnectionUiState.OFFLINE
        return if (capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
            ConnectionUiState.CONNECTED
        } else ConnectionUiState.OFFLINE
    }
}
