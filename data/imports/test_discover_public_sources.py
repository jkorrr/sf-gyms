from __future__ import annotations

import unittest
from datetime import date

import discover_public_sources as discovery


class PublicSourceDiscoveryTests(unittest.TestCase):
    def test_identity_score_rewards_exact_name_and_address(self) -> None:
        listing = {"name": "Sunset Gym", "address": "1247 9th Avenue, San Francisco"}
        exact = {"dba_name": "Sunset Gym", "full_business_address": "1247 9th Ave"}
        wrong = {"dba_name": "Sunset Gymnastics", "full_business_address": "900 Irving St"}
        self.assertGreater(discovery.identity_score(listing, exact), 0.95)
        self.assertLess(discovery.identity_score(listing, wrong), 0.7)

    def test_closed_signal_uses_end_date_or_admin_flag(self) -> None:
        self.assertEqual(discovery.status_signal({"location_end_date": "2025-01-01"}, date(2026, 8, 18)), "closed-signal")
        self.assertEqual(discovery.status_signal({"administratively_closed": "Y"}, date(2026, 8, 18)), "closed-signal")
        self.assertEqual(discovery.status_signal({"administratively_closed": "***Administratively Closed"}, date(2026, 8, 18)), "closed-signal")
        self.assertEqual(discovery.status_signal({}, date(2026, 8, 18)), "registered-signal")

    def test_discovery_never_auto_applies(self) -> None:
        listing = {"id": "gym-1", "name": "Sunset Gym", "address": "1247 9th Avenue"}
        result = discovery.research_listing(listing, [{"dba_name": "Sunset Gym", "full_business_address": "1247 9th Ave"}], date(2026, 8, 18))
        self.assertEqual(result["disposition"], "strong-match-review")
        self.assertFalse(result["autoApply"])

    def test_manual_packet_contains_exact_identity_queries_and_operator_hints(self) -> None:
        listing = {"id": "gym-1", "name": "Sunset Gym", "address": "1247 9th Avenue, San Francisco, CA"}
        value = discovery.manual_search_record(listing, {"operatorKey": "sunset-gym"}, {"sunset-gym": ["https://sunsetgym.com/locations"]})
        self.assertIn('"Sunset Gym" "1247 9th Avenue" official', value["queries"])
        self.assertEqual(value["sameOperatorOfficialUrls"], ["https://sunsetgym.com/locations"])

    def test_address_discovery_emits_replacement_candidate_without_auto_applying(self) -> None:
        listing = {"id": "old", "name": "Classical Pilates Studio", "address": "2636 Ocean Avenue, San Francisco, CA 94132"}
        rows = [
            {
                "uniqueid": "current-1",
                "certificate_number": "1115105",
                "ownership_name": "Fitness2function",
                "dba_name": "Fitness2function",
                "full_business_address": "2636 Ocean Ave",
                "self_reported_naics_code": "713940",
                "location_start_date": "2020-06-18T00:00:00",
            },
            {
                "uniqueid": "closed-1",
                "dba_name": "Former Pilates",
                "full_business_address": "2636 Ocean Ave",
                "self_reported_naics_code": "713940",
                "location_end_date": "2020-01-01T00:00:00",
            },
            {
                "uniqueid": "wrong-address",
                "dba_name": "Another Fitness",
                "full_business_address": "2638 Ocean Ave",
                "self_reported_naics_code": "713940",
            },
        ]
        result = discovery.research_listing(listing, [], date(2026, 8, 21), rows)
        self.assertEqual(result["disposition"], "possible-replacement-review")
        self.assertEqual([item["dbaName"] for item in result["replacementCandidates"]], ["Fitness2function"])
        self.assertFalse(result["replacementCandidates"][0]["autoApply"])

    def test_address_query_uses_canonical_numbered_street(self) -> None:
        url = discovery.query_address_url("2636 Ocean Avenue, San Francisco, CA 94132")
        self.assertIn("2636+ocean+ave", url)


if __name__ == "__main__":
    unittest.main()
