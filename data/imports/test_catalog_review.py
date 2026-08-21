import json
import tempfile
import unittest
from pathlib import Path

from build_catalog_review import build_review, candidate_is_attached
from review_catalogs import approve


class CatalogReviewTests(unittest.TestCase):
    def test_loose_visible_text_is_rejected(self):
        self.assertFalse(candidate_is_attached({"method": "visible-text-candidate", "amount": 99}))

    def test_platform_product_becomes_pending_proposal(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One", "address": "1 Main St"}]}
        document = {"generatedAt": "2026-08-20", "observations": [{
            "gymId": "gym-1", "amount": 99, "rawLabel": "Basic Membership",
            "sourceProductId": "basic", "productType": "monthly", "cadence": "month",
            "method": "public-momence-json", "sourceUrl": "https://momence.com/gym/1",
        }]}
        result = build_review(fixture, [document], "2026-08-20")
        self.assertEqual(result["_meta"]["proposalCount"], 1)
        self.assertFalse(result["proposals"][0]["publicationEligible"])
        self.assertEqual(result["proposals"][0]["planOffers"][0]["sourceProductId"], "basic")

    def test_ambiguous_multi_price_card_is_rejected(self):
        self.assertFalse(candidate_is_attached({
            "method": "rendered-visible-plan-card", "cardHash": "abc",
            "rawLabel": "Membership $99 intro then $229 monthly", "amount": 229,
        }))

    def test_bare_nested_amount_card_is_rejected(self):
        self.assertFalse(candidate_is_attached({
            "method": "rendered-visible-plan-card", "cardHash": "abc",
            "rawLabel": "$229 / mo", "amount": 229,
        }))

    def test_conflicting_product_amounts_fail_approval(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One"}]}
        observations = [{
            "gymId": "gym-1", "amount": amount, "rawLabel": "Basic", "sourceProductId": "basic",
            "productType": "monthly", "cadence": "month", "method": "public-momence-json",
            "sourceUrl": "https://momence.com/gym/1",
        } for amount in (99, 119)]
        result = build_review(fixture, [{"generatedAt": "2026-08-20", "observations": observations}], "2026-08-20")
        with tempfile.TemporaryDirectory() as folder:
            review = Path(folder) / "review.json"
            approved = Path(folder) / "approved.json"
            review.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaises(ValueError):
                approve("gym-1", "2026-08-20", False, review, approved)


if __name__ == "__main__":
    unittest.main()
