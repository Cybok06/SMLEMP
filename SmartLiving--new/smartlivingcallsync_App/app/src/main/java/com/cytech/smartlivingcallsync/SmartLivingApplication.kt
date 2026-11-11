package com.cytech.smartlivingcallsync

import android.app.Application
import android.content.Context
import com.cytech.smartlivingcallsync.data.local.AppDatabase
import com.cytech.smartlivingcallsync.data.local.AppPreferences
import com.cytech.smartlivingcallsync.data.remote.ApiFactory
import com.cytech.smartlivingcallsync.data.repository.CallSyncRepository
import com.cytech.smartlivingcallsync.domain.AndroidCallLogReader
import com.cytech.smartlivingcallsync.domain.AndroidSimResolver
import com.cytech.smartlivingcallsync.domain.SyncCoordinator
import com.cytech.smartlivingcallsync.domain.CallLogObserver
import java.security.KeyStore

class SmartLivingApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }

    override fun onCreate() {
        super.onCreate()
        removeLegacyCallSyncCredentials()
        container.callLogObserver
    }

    private fun removeLegacyCallSyncCredentials() {
        getSharedPreferences("secure_device_credentials", Context.MODE_PRIVATE).edit().clear().apply()
        runCatching {
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            val legacyAlias = "smartliving_device_token_v1"
            if (keyStore.containsAlias(legacyAlias)) keyStore.deleteEntry(legacyAlias)
        }
    }
}

class AppContainer(application: Application) {
    val preferences = AppPreferences(application)
    private val database = AppDatabase.get(application)
    val repository = CallSyncRepository(database.callDao(), ApiFactory.create())
    val coordinator = SyncCoordinator(
        application, preferences,
        AndroidCallLogReader(application, AndroidSimResolver(application)), repository,
    )
    val callLogObserver = CallLogObserver(application, preferences)
}
