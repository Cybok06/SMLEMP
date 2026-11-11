import unittest
from pathlib import Path
from jinja2 import Environment


class AgentDeliveryUITests(unittest.TestCase):
    def render(self, status, packaging_status="", amount_left=0, role="agent", transferred=False):
        source = (Path(__file__).resolve().parents[1] / "templates/partials/customer_tabs/products.html").read_text(encoding="utf-8")
        return Environment().from_string(source).render(
            purchases=[{"product": {"name": "Max A", "status": status, "packaging_status": packaging_status,
                                    "transfer_status": "transferred_out" if transferred else "", "price": 200, "quantity": 1, "total": 200},
                        "amount_left": amount_left, "amount_paid": 200 - amount_left, "progress": 100,
                        "components_catalog": [], "purchase_date": "2026-01-01", "end_date": "2026-01-02"}],
            session={f"{role}_id": "actor"}, customer_id="customer",
            url_for=lambda endpoint, **kwargs: "/" + endpoint, today="2026-01-02",
        )

    def test_packaging_and_legacy_packaged_allow_delivery(self):
        for status in ("packaging", "packaged", "delivering"):
            with self.subTest(status=status):
                self.assertIn('data-delivery-form="1"', self.render(status))

    def test_completed_product_with_packaging_marker_allows_delivery(self):
        self.assertIn('data-delivery-form="1"', self.render("completed", "packaging"))
        self.assertIn('data-status-badge="1">Packaging', self.render("completed", "packaging"))

    def test_submitted_or_completed_before_packaging_has_no_delivery_action(self):
        for status in ("active", "completed", "submitted_for_packaging"):
            with self.subTest(status=status):
                self.assertNotIn('data-delivery-form="1"', self.render(status))

    def test_unpaid_transferred_or_non_agent_has_no_delivery_action(self):
        self.assertNotIn('data-delivery-form="1"', self.render("packaged", amount_left=10))
        self.assertNotIn('data-delivery-form="1"', self.render("packaged", transferred=True))
        self.assertNotIn('data-delivery-form="1"', self.render("packaged", role="manager"))

    def test_terminal_status_cannot_be_overridden_by_old_packaging_marker(self):
        for status in ("delivered", "closed"):
            self.assertNotIn('data-delivery-form="1"', self.render(status, "packaging"))


if __name__ == "__main__": unittest.main()
