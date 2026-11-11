[app]
title = SmartLiving Call Sync
package.name = smartlivingcallsync
package.domain = com.smartliving
source.dir = .
source.include_exts = py,png,jpg,kv,json
version = 0.1.0
requirements = python3,kivy,pyjnius,requests
services = callsync:service.py:foreground:sticky
orientation = portrait
fullscreen = 0
android.permissions = READ_CALL_LOG,READ_PHONE_STATE,READ_PHONE_NUMBERS,INTERNET,ACCESS_NETWORK_STATE,RECEIVE_BOOT_COMPLETED,FOREGROUND_SERVICE,FOREGROUND_SERVICE_DATA_SYNC
android.add_src = android_src
android.extra_manifest_application_arguments = extra_manifest_application_arguments.xml
android.api = 35
android.minapi = 26
android.archs = arm64-v8a,armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
