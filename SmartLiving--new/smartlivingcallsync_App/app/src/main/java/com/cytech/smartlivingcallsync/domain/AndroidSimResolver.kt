package com.cytech.smartlivingcallsync.domain

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class AndroidSimResolver(private val context: Context) : SimResolver {
    override suspend fun resolve(phoneAccountId: String?): SimInfo = withContext(Dispatchers.IO) {
        val stableAccount = phoneAccountId.orEmpty().take(160)
        if (phoneAccountId.isNullOrBlank() || !hasPhonePermission()) return@withContext SimInfo(stableAccount)
        try {
            val subscriptions = context.getSystemService(SubscriptionManager::class.java)
                ?.activeSubscriptionInfoList.orEmpty()
            val match = subscriptions.firstOrNull { it.matchesAccount(phoneAccountId) }
                ?: subscriptions.singleOrNull()
                ?: return@withContext SimInfo(stableAccount)
            SimInfo(stableAccount, lineNumber(match).orEmpty())
        } catch (_: SecurityException) {
            SimInfo(stableAccount)
        } catch (_: UnsupportedOperationException) {
            SimInfo(stableAccount)
        }
    }

    private fun hasPhonePermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED

    @Suppress("DEPRECATION")
    private fun lineNumber(info: SubscriptionInfo): String? {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_NUMBERS) == PackageManager.PERMISSION_GRANTED
        ) {
            context.getSystemService(SubscriptionManager::class.java)?.getPhoneNumber(info.subscriptionId)
                ?.takeIf { it.isNotBlank() }?.let { return it }
        }
        return context.getSystemService(TelephonyManager::class.java)
            ?.createForSubscriptionId(info.subscriptionId)?.line1Number?.takeIf { it.isNotBlank() }
    }

    @Suppress("DEPRECATION")
    private fun SubscriptionInfo.matchesAccount(account: String): Boolean =
        subscriptionId.toString() == account || simSlotIndex.toString() == account || iccId == account
}
