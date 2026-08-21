from __future__ import annotations

import json
import unittest
from pathlib import Path

import platform_adapters as adapters

FIXTURE = Path(__file__).parent / "fixtures" / "platform-catalogs.json"
RENDERED_FIXTURE = Path(__file__).parent / "fixtures" / "rendered-platform-cards.json"


class PlatformAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.rendered_fixtures = json.loads(RENDERED_FIXTURE.read_text(encoding="utf-8"))

    def test_all_supported_platform_fixtures_reconstruct_a_product(self) -> None:
        for name, fixture in self.fixtures.items():
            with self.subTest(name=name):
                candidates = adapters.extract_candidates(fixture["payload"], fixture["url"])
                self.assertGreaterEqual(len(candidates), 1)
                self.assertGreater(candidates[0]["amount"], 0)
                self.assertEqual(candidates[0]["evidenceTier"], "official-public")
                self.assertFalse(candidates[0]["autoPublishEligible"])

    def test_cents_prices_and_four_week_cadence_are_preserved(self) -> None:
        fixture = self.fixtures["momence"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertEqual(candidate["amount"], 119)
        self.assertEqual(candidate["cadence"], "4 weeks")
        self.assertEqual(candidate["classAllowance"]["count"], 4)

    def test_plan_linked_fee_does_not_become_a_second_plan(self) -> None:
        fixture = self.fixtures["mindbody"]
        candidates = adapters.extract_candidates(fixture["payload"], fixture["url"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["fees"][0]["type"], "enrollment")
        self.assertEqual(candidates[0]["fees"][0]["amount"], 25)

    def test_promotions_are_retained_but_marked_ineligible(self) -> None:
        fixture = self.fixtures["promotion"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertTrue(candidate["promotion"]["isPromotion"])
        self.assertEqual(candidate["eligibility"]["type"], "new-client")

    def test_operator_labeled_best_value_is_preserved(self) -> None:
        fixture = self.fixtures["mariana-tek"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertTrue(candidate["bestValueLabel"])

    def test_unknown_hosts_are_not_scraped_as_platform_catalogs(self) -> None:
        self.assertEqual(adapters.extract_candidates({"name": "Plan", "price": 99}, "https://tracker.invalid/api"), [])

    def test_location_metadata_and_checkout_totals_are_not_products(self) -> None:
        payload = {
            "studio": {"id": "loc-1", "name": "Lotusland Yoga SF", "total": 99},
            "checkout": {"name": "Order total", "amount": 99},
        }
        self.assertEqual(adapters.extract_candidates(payload, "https://momence.com/api/store"), [])

    def test_new_student_language_is_marked_promotional(self) -> None:
        candidates = adapters.extract_candidates(
            {"products": [{"productId": "welcome", "name": "New Student Special", "price": 49, "billingPeriod": "month"}]},
            "https://momence.com/api/memberships",
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["promotion"]["isPromotion"])
        self.assertEqual(candidates[0]["eligibility"]["type"], "new-client")

    def test_bookee_unit_amount_is_interpreted_as_cents(self) -> None:
        fixture = self.fixtures["bookee"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertEqual(candidate["amount"], 129)

    def test_jane_personal_training_card_is_exact_but_trainer_required(self) -> None:
        fixture = self.rendered_fixtures["janePersonalTraining"]
        candidates = adapters.jane_service_card_candidates(
            fixture["cardText"], fixture["url"], fixture["href"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "discipline-2-treatment-3")
        self.assertEqual(candidates[0]["amount"], 250)
        self.assertEqual(candidates[0]["eligibility"]["type"], "trainer-required")
        self.assertFalse(candidates[0]["autoPublishEligible"])

    def test_jane_clinical_service_is_excluded_from_fitness_catalog(self) -> None:
        fixture = self.rendered_fixtures["janeClinicalExclusion"]
        candidates = adapters.jane_service_card_candidates(
            fixture["cardText"], fixture["url"], fixture["href"],
        )
        self.assertEqual(candidates, [])

    def test_mindbody_product_row_keeps_price_attached_and_marks_special(self) -> None:
        fixture = self.rendered_fixtures["mindbodyPromotionalMembership"]
        candidates = adapters.mindbody_purchase_item_candidates(
            fixture["categoryLabel"], fixture["cardText"], fixture["url"], fixture["sourceProductId"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "11259")
        self.assertEqual(candidates[0]["amount"], 699)
        self.assertEqual(candidates[0]["cadence"], "one-time")
        self.assertEqual(candidates[0]["productType"], "offer")
        self.assertEqual(candidates[0]["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertTrue(candidates[0]["promotion"]["isPromotion"])
        self.assertEqual(candidates[0]["method"], "rendered-mindbody-purchase-item")

    def test_mindbody_contract_uses_recurring_charge_not_total_or_zero_pass(self) -> None:
        fixture = self.rendered_fixtures["mindbodyRecurringContract"]
        candidates = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"], fixture["sourceProductId"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "179")
        self.assertEqual(candidates[0]["amount"], 300)
        self.assertEqual(candidates[0]["cadence"], "month")
        self.assertEqual(candidates[0]["commitment"]["type"], "month-to-month")
        self.assertEqual(candidates[0]["fees"], [])
        self.assertFalse(candidates[0]["autoPublishEligible"])


if __name__ == "__main__":
    unittest.main()
