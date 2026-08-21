import json
import tempfile
import unittest
from pathlib import Path

from build_catalog_review import build_review, candidate_is_attached
from review_catalogs import approval_from_proposal, approve


class CatalogReviewTests(unittest.TestCase):
    def test_loose_visible_text_is_rejected(self):
        self.assertFalse(candidate_is_attached({"method": "visible-text-candidate", "amount": 99}))

    def test_structured_location_metadata_is_rejected_as_a_product(self):
        self.assertFalse(candidate_is_attached({
            "method": "public-momence-json", "amount": 99,
            "rawLabel": "Lotusland Yoga SF", "productType": "offer", "cadence": "one-time",
        }))

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
        self.assertEqual(result["proposals"][0]["catalogCompleteness"]["plans"], "partial")

    def test_official_range_becomes_context_only_proposal(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One", "address": "1 Main St"}]}
        document = {"generatedAt": "2026-08-20", "observations": [{
            "gymId": "gym-1", "kind": "range", "low": 150, "high": 250,
            "rawLabel": "One-on-one training $150–$250 per session", "cadence": "session",
            "productType": "cost-context", "method": "visible-cost-context",
            "sourceUrl": "https://gym.example/rates",
        }]}

        result = build_review(fixture, [document], "2026-08-20")

        proposal = result["proposals"][0]
        self.assertEqual(proposal["planOffers"], [])
        self.assertEqual(proposal["dropInOffers"], [])
        self.assertEqual((proposal["costContextOffers"][0]["low"], proposal["costContextOffers"][0]["high"]), (150, 250))

    def test_context_only_proposal_can_be_approved_without_scalar_leakage(self):
        proposal = {
            "gymId": "gym-1",
            "sourceUrls": ["https://gym.example/rates"],
            "planOffers": [], "dropInOffers": [],
            "costContextOffers": [{
                "kind": "starting-price", "label": "Classes start at $200/month",
                "low": 200, "high": 200, "cadence": "month",
                "sourceUrl": "https://gym.example/rates", "observedAt": "2026-08-20",
                "selectable": False,
            }],
            "catalogCompleteness": {"plans": "none-observed", "dropIns": "none-observed"},
            "conflicts": [],
        }

        approval = approval_from_proposal(proposal, "2026-08-20")

        self.assertEqual(approval["costContextOffers"][0]["exactLocationMatch"], "exact-location-reviewed")
        self.assertNotIn("monthlyPrice", approval)
        self.assertIn("no exact compatibility price", approval["priceNote"])

    def test_conflicting_ranges_fail_context_approval(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One"}]}
        observations = [{
            "gymId": "gym-1", "kind": "range", "low": low, "high": high,
            "rawLabel": "Training range", "sourceProductId": "training-range",
            "productType": "cost-context", "method": "public-momence-json",
            "sourceUrl": "https://momence.com/gym/1",
        } for low, high in ((150, 250), (175, 275))]
        result = build_review(fixture, [{"generatedAt": "2026-08-20", "observations": observations}], "2026-08-20")

        self.assertEqual(result["proposals"][0]["conflicts"][0]["type"], "source-product-range-conflict")
        with self.assertRaises(ValueError):
            approval_from_proposal(result["proposals"][0], "2026-08-20")

    def test_same_membership_range_on_schedule_and_pricing_pages_is_one_context(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One"}]}
        observations = [
            {
                "gymId": "gym-1", "kind": "range", "low": 135, "high": 175,
                "rawLabel": label, "cadence": "month", "contextProductType": "membership",
                "productType": "cost-context", "method": "visible-cost-context", "sourceUrl": source,
            }
            for label, source in (
                ("Unlimited monthly membership $135–$175", "https://gym.example/pricing/"),
                ("Monthly unlimited membership $135–$175", "https://gym.example/schedule/"),
            )
        ]

        result = build_review(fixture, [{"generatedAt": "2026-08-20", "observations": observations}], "2026-08-20")

        contexts = result["proposals"][0]["costContextOffers"]
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["sourceUrl"], "https://gym.example/pricing/")

    def test_default_approval_does_not_claim_a_complete_catalog(self):
        proposal = {
            "gymId": "gym-1",
            "sourceUrls": ["https://momence.com/gym/1"],
            "planOffers": [{"name": "Basic", "amount": 99, "evidence": {"observedAt": "2026-08-20"}}],
            "dropInOffers": [],
            "catalogCompleteness": {"plans": "partial", "dropIns": "none-observed"},
            "conflicts": [],
        }
        approval = approval_from_proposal(proposal, "2026-08-20")
        self.assertEqual(approval["catalogCompleteness"]["plans"], "partial")
        self.assertIn("may contain additional products", approval["priceNote"])
        self.assertNotIn("Complete public catalog", approval["priceNote"])

    def test_reviewer_can_explicitly_mark_a_catalog_complete(self):
        proposal = {
            "gymId": "gym-1",
            "sourceUrls": ["https://momence.com/gym/1"],
            "planOffers": [{"name": "Only Plan", "amount": 99, "evidence": {"observedAt": "2026-08-20"}}],
            "dropInOffers": [],
            "conflicts": [],
        }
        approval = approval_from_proposal(proposal, "2026-08-20", "complete", "none-observed")
        self.assertEqual(approval["catalogCompleteness"]["plans"], "complete")
        self.assertIn("complete public recurring/package catalog", approval["priceNote"])

    def test_same_rendered_card_candidates_are_deduplicated(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One"}]}
        observations = [
            {
                "gymId": "gym-1", "amount": 25, "rawLabel": label,
                "productType": "drop-in", "method": "rendered-visible-plan-card",
                "cardAssociationHash": "same-card", "sourceUrl": "https://gym.example/pricing",
            }
            for label in ("Single Class $25", "Drop-in Single Class $25")
        ]
        result = build_review(fixture, [{"generatedAt": "2026-08-20", "observations": observations}], "2026-08-20")
        self.assertEqual(len(result["proposals"][0]["dropInOffers"]), 1)

    def test_unchanged_approved_catalog_does_not_reenter_pending_queue(self):
        fixture = {"gyms": [{"id": "gym-1", "name": "Gym One"}]}
        observation = {
            "gymId": "gym-1", "amount": 99, "rawLabel": "Basic Membership",
            "sourceProductId": "basic", "productType": "monthly", "cadence": "month",
            "method": "public-momence-json", "sourceUrl": "https://momence.com/gym/1",
        }
        approved = {"approvals": [{
            "gymId": "gym-1",
            "planOffers": [{"sourceProductId": "basic", "name": "Basic Membership", "amount": 99, "billingInterval": "month"}],
            "dropInOffers": [],
            "catalogCompleteness": {"plans": "partial", "dropIns": "none-observed"},
        }]}
        result = build_review(fixture, [{"generatedAt": "2026-08-20", "observations": [observation]}], "2026-08-20", approved)
        self.assertEqual(result["_meta"]["proposalCount"], 0)
        self.assertEqual(result["_meta"]["unchangedApprovedCount"], 1)
        self.assertEqual(result["unchangedApproved"][0]["gymId"], "gym-1")

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
