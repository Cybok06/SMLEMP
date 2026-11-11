import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask


class _DummyCollection:
    def create_index(self, *args, **kwargs):
        return None

    def update_many(self, *args, **kwargs):
        return None


class _DummyDatabase:
    def __getitem__(self, name):
        return _DummyCollection()


def _load_module():
    fake_db = types.ModuleType("db")
    fake_db.db = _DummyDatabase()
    fake_login = types.ModuleType("login")
    fake_login.get_current_identity = lambda: {"role": "executive", "user_id": "test"}
    fake_login.role_required = lambda *roles: (lambda fn: fn)

    module_path = Path(__file__).resolve().parents[1] / "routes" / "external_access.py"
    spec = importlib.util.spec_from_file_location("external_access_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"db": fake_db, "login": fake_login}):
        spec.loader.exec_module(module)
    return module


class ExternalAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()
        cls.app = Flask(__name__)

    def test_all_seven_scopes_are_published(self):
        self.assertEqual(
            set(self.module.API_KEY_SCOPES),
            {"closed_customers", "payments", "closed_cards", "completed_cards", "customers", "users", "products"},
        )

    def test_date_range_is_inclusive_of_end_date(self):
        with self.app.test_request_context("/?start_date=2026-08-01&end_date=2026-08-08"):
            start, end, raw = self.module._parse_date_range()
        self.assertEqual(start, datetime(2026, 8, 1))
        self.assertEqual(end, datetime(2026, 8, 9))
        self.assertEqual(raw, {"start_date": "2026-08-01", "end_date": "2026-08-08"})

    def test_invalid_or_reversed_dates_are_rejected(self):
        for query in ("/?start_date=08-01-2026", "/?start_date=2026-08-09&end_date=2026-08-08"):
            with self.subTest(query=query), self.app.test_request_context(query):
                with self.assertRaises(self.module.ExternalApiValidationError):
                    self.module._parse_date_range()

    def test_user_endpoint_uses_allow_list_without_password(self):
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return {}, 200, 0

        with patch.object(self.module, "_collection_response", side_effect=capture):
            self.module.external_users.__wrapped__(key_doc={})

        projection = captured["projection"]
        self.assertNotIn("password", projection)
        self.assertTrue(all(value == 1 for value in projection.values()))
        self.assertEqual(captured["date_field"], "date_registered")
        self.assertEqual(captured["fallback_date_field"], "created_at")

    def test_external_page_uses_modal_tabs_and_green_responses(self):
        template_path = Path(__file__).resolve().parents[1] / "templates" / "executive_external_access.html"
        template = template_path.read_text(encoding="utf-8")
        self.assertIn('id="generateKeyModal"', template)
        self.assertIn('id="keys-pane"', template)
        self.assertIn('id="logs-pane"', template)
        self.assertIn('class="response-block"', template)
        self.assertIn("background:#071f16", template)
        self.assertNotIn("Executive API Control", template)
        self.assertNotIn("Hashed keys, request logging", template)


if __name__ == "__main__":
    unittest.main()
