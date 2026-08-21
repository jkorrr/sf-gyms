from __future__ import annotations

import unittest

import build_location_review as review


class LocationReviewTests(unittest.TestCase):
    def test_exact_location_can_propose_hours_but_never_auto_applies(self) -> None:
        gym = {"id": "gym-1", "name": "Example Gym", "address": "1 Market Street, San Francisco, CA", "hours": "Hours not listed"}
        observation = {"name": "Example Gym SF", "address": "1 Market St, San Francisco, CA, 94105", "hours": [{"dayOfWeek": ["Monday", "Tuesday"], "opens": "06:00", "closes": "22:00"}], "sourceUrl": "https://example.com"}
        value = review.proposal_for(gym, observation)
        self.assertEqual(value["identityDisposition"], "strong-exact-review")
        self.assertEqual(value["proposedChanges"]["hours"], "Mo-Tu 06:00-22:00")
        self.assertFalse(value["autoApply"])

    def test_other_city_branch_is_rejected(self) -> None:
        gym = {"id": "gym-1", "name": "Flagship Training", "address": "201 King Street, San Francisco", "hours": "Hours not listed"}
        observation = {"name": "Flagship", "address": "1918 8th Avenue, Seattle, WA", "hours": "Mo-Fr 06:00-20:00"}
        self.assertEqual(review.proposal_for(gym, observation)["identityDisposition"], "reject-or-manual-review")


if __name__ == "__main__":
    unittest.main()
