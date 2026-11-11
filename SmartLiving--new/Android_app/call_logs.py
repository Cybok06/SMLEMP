from datetime import datetime, timezone


TYPE_MAP = {1: "inbound", 2: "outbound", 3: "missed"}


def is_android():
    try:
        from kivy.utils import platform
        return platform == "android"
    except ImportError:
        return False


def _android_context():
    from jnius import autoclass
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    if activity is not None: return activity
    service = autoclass("org.kivy.android.PythonService").mService
    if service is not None: return service
    raise RuntimeError("Android application context is unavailable.")


def permission_state():
    if not is_android(): return "unavailable"
    from android.permissions import Permission, check_permission
    return "allowed" if check_permission(Permission.READ_CALL_LOG) else "not_allowed"


def request_call_log_permissions(callback):
    if not is_android():
        callback("unavailable"); return
    from android.permissions import Permission, request_permissions
    permissions = [Permission.READ_CALL_LOG, Permission.READ_PHONE_STATE, "android.permission.READ_PHONE_NUMBERS"]
    def completed(_permissions, grants):
        if all(grants): callback("allowed"); return
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            denied_forever = not activity.shouldShowRequestPermissionRationale(Permission.READ_CALL_LOG)
            callback("permanently_denied" if denied_forever else "denied")
        except Exception:
            callback("denied")
    request_permissions(permissions, completed)


def app_install_time_ms():
    if not is_android():
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    from jnius import autoclass
    context = _android_context()
    info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0)
    return int(info.firstInstallTime)


def _sim_number(activity, account_id):
    if not account_id: return ""
    try:
        from jnius import autoclass
        context = autoclass("android.content.Context")
        subscriptions = activity.getSystemService(context.TELEPHONY_SUBSCRIPTION_SERVICE).getActiveSubscriptionInfoList()
        telephony = activity.getSystemService(context.TELEPHONY_SERVICE)
        if subscriptions:
            for subscription in subscriptions:
                identifiers = {str(subscription.getSubscriptionId()), str(subscription.getIccId() or "")}
                if str(account_id) not in identifiers: continue
                number = subscription.getNumber() or telephony.createForSubscriptionId(subscription.getSubscriptionId()).getLine1Number()
                return str(number or "")
    except Exception:
        return ""
    return ""


def read_call_logs(since_ms=None):
    if permission_state() != "allowed":
        raise PermissionError("Call-log permission has not been granted.")
    from jnius import autoclass
    activity = _android_context()
    call_log = autoclass("android.provider.CallLog$Calls")
    projection = ["_id", "number", "type", "date", "duration", "phone_account_id"]
    selection = "date >= ?" if since_ms is not None else None
    args = [str(int(since_ms))] if since_ms is not None else None
    cursor = activity.getContentResolver().query(call_log.CONTENT_URI, projection, selection, args, "date ASC")
    calls = []
    if cursor is None: return calls
    try:
        indexes = {name: cursor.getColumnIndex(name) for name in projection}
        while cursor.moveToNext():
            call_type = TYPE_MAP.get(cursor.getInt(indexes["type"]))
            if not call_type: continue
            android_id = str(cursor.getLong(indexes["_id"]))
            started_ms = cursor.getLong(indexes["date"])
            account_id = cursor.getString(indexes["phone_account_id"]) or ""
            calls.append({
                "external_call_id": android_id,
                "phone_number": cursor.getString(indexes["number"]) or "",
                "call_type": call_type,
                "started_at": datetime.fromtimestamp(started_ms / 1000, timezone.utc).isoformat(),
                "duration_seconds": cursor.getInt(indexes["duration"]),
                "sim_account": account_id,
                "from_number": _sim_number(activity, account_id),
            })
    finally:
        cursor.close()
    return calls
