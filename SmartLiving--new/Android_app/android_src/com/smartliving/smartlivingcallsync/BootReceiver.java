package com.smartliving.smartlivingcallsync;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        Intent service = new Intent(context, ServiceCallsync.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(service);
        else context.startService(service);
    }
}
