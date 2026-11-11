"""Migration eligibility checks, independent of app startup and live databases."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "active_user_migration",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_active_users.py",
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class EligibilityTests(unittest.TestCase):
    def reason(self, **changes):
        user = {"_id": "active", "status": "Active", "role": "agent", **changes}
        return migration.exclusion_reason(user, {"archived"}, {"deleted"}, {"exit"})

    def test_legacy_active_and_probation_are_eligible(self):
        self.assertIsNone(self.reason())
        self.assertIsNone(self.reason(employment_status="Probation"))

    def test_exit_stays_excluded_even_when_login_is_enabled(self):
        self.assertIsNotNone(self.reason(employment_status="Exited", is_active=True, account_locked=False))

    def test_external_archive_deletion_and_exit_records_override_active(self):
        for uid in ("archived", "deleted", "exit"):
            with self.subTest(uid=uid):
                self.assertIsNotNone(self.reason(_id=uid))

    def test_inactive_and_soft_deleted_users_are_excluded(self):
        for changes in ({"is_deleted": True}, {"deleted_at": "date"}, {"status": "Not Active"}, {"is_active": False}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.reason(**changes))

    def test_locked_blocked_and_pending_agents_are_excluded(self):
        for changes in ({"account_locked": True}, {"login_blocked": True}, {"agent_approval_status": "pending"}, {"status": "Under Review"}):
            with self.subTest(changes=changes):
                self.assertIsNotNone(self.reason(**changes))


if __name__ == "__main__":
    unittest.main()
