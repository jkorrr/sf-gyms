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

    def test_acuity_business_catalog_keeps_subscriptions_and_class_restrictions(self) -> None:
        payload = {
            "ownerKey": "public-owner",
            "name": "Public Fitness Studio",
            "currencyAbbreviation": "USD",
            "products": {"Memberships": [{
                "id": 10,
                "title": "Unlimited Fitness Membership",
                "description": "Unlimited fitness classes and open gym.",
                "price": 250,
                "isSubscription": True,
                "subscriptionTermsText": "$250.00 per month",
            }]},
            "appointmentTypes": {"General Fitness": [{
                "id": 20,
                "name": "Adult Strength Class",
                "active": True,
                "price": "50.00",
                "type": "class",
                "private": False,
                "classSize": 10,
            }, {
                "id": 21,
                "name": "Youth Strength Class",
                "active": True,
                "price": "45.00",
                "type": "class",
                "private": False,
                "classSize": 10,
            }]},
        }

        candidates = adapters.acuity_business_candidates(payload, "https://studio.as.me/schedule/public-owner")

        membership = next(item for item in candidates if item["sourceProductId"] == "10")
        adult = next(item for item in candidates if item["sourceProductId"] == "20")
        youth = next(item for item in candidates if item["sourceProductId"] == "21")
        self.assertEqual((membership["amount"], membership["cadence"], membership["productType"]), (250, "month", "monthly"))
        self.assertTrue(membership["classAllowance"]["unlimited"])
        self.assertEqual((adult["productType"], adult["ordinaryUse"]), ("drop-in", True))
        self.assertEqual(youth["eligibility"], {"type": "youth", "restrictions": ["Youth product"]})
        self.assertFalse(youth["ordinaryUse"])
        self.assertTrue(all(item["method"] == "public-acuity-embedded-business" for item in candidates))
        self.assertEqual(adapters.acuity_business_candidates(payload, "https://example.com/schedule"), [])

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

    def test_momence_membership_uses_base_price_not_card_checkout_total(self) -> None:
        fixture = self.rendered_fixtures["momenceMembership"]

        candidates = adapters.momence_membership_card_candidates(
            fixture["visibleText"], fixture["url"], fixture["pageTitle"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["name"]), ("776365", "The Work"))
        self.assertEqual((candidate["amount"], candidate["cadence"]), (375, "month"))
        self.assertEqual(candidate["classAllowance"], {"count": 12.0, "period": "month", "unlimited": False})
        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertEqual(candidate["fees"], [])
        self.assertNotIn("390.37", candidate["rawLabel"])
        self.assertNotIn("15.37", candidate["rawLabel"])
        self.assertFalse(candidate["autoPublishEligible"])

    def test_momence_public_api_preserves_allowance_term_and_no_inferred_fee(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "momence-membership.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        source = "https://momence.com/_api/primary/plugin/memberships/776365"

        candidates = adapters.momence_membership_api_candidates(payload, source)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["name"]), ("776365", "The Work"))
        self.assertEqual((candidate["amount"], candidate["cadence"]), (375, "month"))
        self.assertEqual(candidate["classAllowance"], {"count": 12.0, "period": "month", "unlimited": False})
        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertEqual(candidate["fees"], [])
        self.assertEqual(candidate["method"], "public-momence-membership-api")
        self.assertFalse(candidate["autoPublishEligible"])


if __name__ == "__main__":
    unittest.main()
