package com.cytech.smartlivingcallsync.ui

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import com.cytech.smartlivingcallsync.ui.theme.SmartLivingCallSyncTheme
import org.junit.Rule
import org.junit.Test

class PermissionUiInstrumentedTest {
    @get:Rule val compose = createComposeRule()

    @Test fun permissionGrantedState() = show(PermissionUiState.ALLOWED, "Allowed")
    @Test fun permissionDeniedStateOffersRetry() {
        show(PermissionUiState.DENIED, "Denied")
        compose.onNodeWithText("Grant Permission").assertExists()
    }
    @Test fun permissionPermanentlyDeniedOffersSettings() {
        show(PermissionUiState.PERMANENTLY_DENIED, "Permanently denied")
        compose.onNodeWithText("Open App Settings").assertExists()
    }
    @Test fun emptyQueueIsDisplayed() {
        setContent(PermissionUiState.ALLOWED)
        compose.onNodeWithText("0").assertExists()
    }
    @Test fun requiredTokenFreeMainUiIsDisplayed() {
        setContent(PermissionUiState.ALLOWED)
        listOf(
            "SmartLiving Call Sync", "Connection status", "Device Name", "Device ID",
            "Automatic Sync", "Background Trigger", "Call Log Permission", "Last Sync",
            "Pending Calls", "Last Result", "Sync Now",
        ).forEach { compose.onNodeWithText(it).assertExists() }
        compose.onNodeWithText("Device Token", substring = true, ignoreCase = true).assertDoesNotExist()
        compose.onNodeWithText("Enroll", substring = true, ignoreCase = true).assertDoesNotExist()
    }

    private fun show(permission: PermissionUiState, expected: String) {
        setContent(permission)
        compose.onNodeWithText(expected).assertExists()
    }
    private fun setContent(permission: PermissionUiState) {
        compose.setContent {
            SmartLivingCallSyncTheme {
                MainScreen(
                    MainUiState(deviceId = "device", deviceName = "Support Phone"),
                    permission, ConnectionUiState.NOT_CHECKED, {}, {}, { _, _, _ -> }, {}, {},
                )
            }
        }
    }
}
