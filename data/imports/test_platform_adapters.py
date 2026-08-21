from __future__ import annotations

import json
import unittest
from pathlib import Path

import platform_adapters as adapters


FIXTURE = Path(__file__).parent / "fixtures" / "platform-catalogs.json"


class PlatformAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
