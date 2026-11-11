from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label

from call_logs import permission_state, request_call_log_permissions
from sync_worker import SyncWorker


class CallSyncView(BoxLayout):
    def __init__(self, app, **kwargs):
        super().__init__(orientation="vertical", padding=dp(24), spacing=dp(14), **kwargs)
        self.app = app
        self.title = Label(text="SmartLiving Call Sync", font_size="24sp", bold=True)
        self.connection = Label(text="Connection: Not checked")
        self.permission = Label(text="Call Log Permission: Checking")
        self.last_sync = Label(text="Last Sync: Never")
        self.pending = Label(text="Pending Calls: 0")
        self.message = Label(text="", text_size=(dp(310), None))
        self.sync_button = Button(text="Sync Now", size_hint_y=None, height=dp(52))
        self.sync_button.bind(on_release=lambda *_: self.app.sync_now())
        for widget in (self.title, self.connection, self.permission, self.last_sync, self.pending, self.message, self.sync_button): self.add_widget(widget)

    def refresh(self):
        state = permission_state().replace("_", " ").title()
        self.permission.text = f"Call Log Permission: {state}"
        self.last_sync.text = f"Last Sync: {self.app.storage.last_sync()}"
        self.pending.text = f"Pending Calls: {self.app.storage.pending_count()}"


class SmartLivingCallSyncApp(App):
    def build(self):
        data_dir = Path(self.user_data_dir); data_dir.mkdir(parents=True, exist_ok=True)
        self.worker = SyncWorker(data_dir)
        self.config_data = self.worker.config
        self.storage = self.worker.storage
        self.view = CallSyncView(self)
        Clock.schedule_once(lambda *_: self._ensure_permission(), 0.2)
        return self.view

    def _ensure_permission(self):
        if permission_state() == "allowed":
            self.view.refresh(); self._start_background_service()
        else: request_call_log_permissions(lambda state: Clock.schedule_once(lambda *_: self._permission_result(state)))

    def _permission_result(self, state):
        self.view.refresh()
        if state == "permanently_denied": self.view.message.text = "Permission permanently denied. Enable Call Logs in Android app settings."
        elif state != "allowed": self.view.message.text = "Call-log permission is required to synchronize calls."
        else: self._start_background_service()

    def _start_background_service(self):
        try:
            from kivy.utils import platform
            if platform != "android": return
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            autoclass("com.smartliving.smartlivingcallsync.ServiceCallsync").start(activity, "")
        except Exception as exc:
            self.view.message.text = f"Background sync could not start: {exc}"

    def sync_now(self):
        self.view.sync_button.disabled = True; self.view.message.text = "Reading call logs..."
        Clock.schedule_once(lambda *_: self._sync(), 0)

    def _sync(self):
        try:
            self.view.message.text = "Uploading pending calls..."
            self.worker.run_once()
            self.view.connection.text = "Connection: Connected"; self.view.message.text = "Synchronization complete."
        except PermissionError as exc:
            self.view.message.text = str(exc)
        except Exception as exc:
            self.view.connection.text = "Connection: Offline"; self.view.message.text = f"Sync pending: {exc}"
        finally:
            self.view.sync_button.disabled = False; self.view.refresh()


if __name__ == "__main__":
    SmartLivingCallSyncApp().run()
