from __future__ import annotations

import argparse
import unittest

import review_deals


class DealReviewTests(unittest.TestCase):
    def test_approved_decision_preserves_evidence_and_forces_price_separation(self) -> None:
        candidate = {
            "id": "deal-1", "gymId": "gym-1", "gymName": "Gym", "amount": 49,
            "currency": "USD", "productType": "monthly", "cadence": "first month",
            "label": "First month", "expiresAt": "2026-08-31", "sourceUrl": "https://gym.example/deal",
            "capturedAt": "2026-08-19", "contentHash": "a" * 64, "replacesOrdinaryPrice": False,
        }
        args = argparse.Namespace(review_note="Checked official terms", eligibility_label="All adults")
        decision = review_deals.decision_from(candidate, "approved", args)
        self.assertEqual(decision["reviewStatus"], "approved")
        self.assertTrue(decision["standardAdult"])
        self.assertFalse(decision["replacesOrdinaryPrice"])
        self.assertEqual(decision["contentHash"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
