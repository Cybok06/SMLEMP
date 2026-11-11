package com.cytech.smartlivingcallsync.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore("smartliving_settings")

data class AppSettings(
    val installationCutoffMs: Long? = null,
    val lastSuccessfulSyncAt: Long? = null,
    val deviceId: String? = null,
    val deviceName: String? = null,
    val automaticSyncEnabled: Boolean = true,
)

class AppPreferences(private val context: Context) {
    private object Keys {
        val cutoff = longPreferencesKey("installation_cutoff_ms")
        val lastSync = longPreferencesKey("last_successful_sync_at")
        val deviceId = stringPreferencesKey("device_id")
        val deviceName = stringPreferencesKey("device_name")
        val automaticSyncEnabled = booleanPreferencesKey("automatic_sync_enabled")
    }
    val settings: Flow<AppSettings> = context.dataStore.data.map {
        AppSettings(
            it[Keys.cutoff], it[Keys.lastSync], it[Keys.deviceId], it[Keys.deviceName],
            it[Keys.automaticSyncEnabled] ?: true,
        )
    }
    suspend fun installationCutoff(packageFirstInstallTime: Long): Long {
        val existing = settings.first().installationCutoffMs
        if (existing != null) return existing
        context.dataStore.edit { it[Keys.cutoff] = packageFirstInstallTime }
        return packageFirstInstallTime
    }
    suspend fun saveDeviceSettings(deviceId: String, deviceName: String) = context.dataStore.edit {
        it[Keys.deviceId] = deviceId; it[Keys.deviceName] = deviceName
    }
    suspend fun setAutomaticSyncEnabled(enabled: Boolean) = context.dataStore.edit {
        it[Keys.automaticSyncEnabled] = enabled
    }
    suspend fun recordSuccessfulSync(at: Long) = context.dataStore.edit { it[Keys.lastSync] = at }
}
