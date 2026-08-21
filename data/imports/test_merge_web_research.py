from __future__ import annotations

import unittest

import merge_web_research as merge


class MergeWebResearchTests(unittest.TestCase):
    def test_discovers_every_lettered_research_batch(self) -> None:
        self.assertEqual([path.name for path in merge.RESEARCH_PATHS][-1], "sf-gym-web-research-e.json")

    def test_merge_source_is_pinned_raw_osm_not_generated_fixture(self) -> None:
        self.assertEqual(merge.RAW_OSM_PATH.name, "sf-gyms-osm-raw.json")

    def test_fixed_import_date_produces_reproducible_timestamp(self) -> None:
        self.assertEqual(merge.resolve_imported_at("2026-08-19"), "2026-08-19T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            merge.resolve_imported_at("08/19/2026")

    def test_canonical_address_ignores_city_zip_and_street_spelling(self) -> None:
        self.assertEqual(
            merge.canonical_address("350 Third Street, San Francisco, CA 94107"),
            merge.canonical_address("350 3rd St, San Francisco"),
        )

    def test_address_match_prefers_same_brand_at_shared_address(self) -> None:
        gyms = [
            {"name": "Example Pilates", "address": "100 Market Street, San Francisco"},
            {"name": "Equinox Market", "address": "100 Market St, San Francisco, CA 94105"},
        ]
        record = {"name": "Equinox Downtown", "address": "100 Market Street, San Francisco, CA 94105"}
        match = merge.address_match(
            record,
            merge.indexed_gyms(gyms, merge.normalized),
            merge.indexed_gyms(gyms, merge.canonical_address),
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Equinox Market")

    def test_new_gym_preserves_separate_mandatory_fees(self) -> None:
        record = {
            "name": "Example Gym",
            "address": "1 Market Street, San Francisco, CA 94105",
            "monthlyPrice": 20,
            "dayPassPrice": 10,
            "annualFee": 49,
            "enrollmentFee": 25,
            "processingFee": 5,
        }
        gym = merge.new_gym(record, (37.79, -122.4), "2026-08-17T00:00:00Z", "2026-08-17")
        self.assertEqual(gym["monthlyPrice"], 20)
        self.assertEqual(gym["annualFee"], 49)
        self.assertEqual(gym["enrollmentFee"], 25)
        self.assertEqual(gym["processingFee"], 5)

    def test_collapses_only_known_brand_duplicates_with_real_street_addresses(self) -> None:
        gyms = [
            {"id": "osm-1", "name": "Barry's", "address": "2280 Market Street, San Francisco", "amenities": []},
            {
                "id": "web-1",
                "name": "Barry's San Francisco - Castro",
                "address": "2280 Market St, San Francisco, CA 94114",
                "amenities": ["showers"],
            },
            {"id": "osm-2", "name": "F45 Training", "address": "San Francisco", "amenities": []},
            {"id": "osm-3", "name": "F45 Training", "address": "San Francisco", "amenities": []},
        ]
        unique, collapsed = merge.collapse_known_brand_duplicates(gyms)
        self.assertEqual(collapsed, 1)
        self.assertEqual(len(unique), 3)
        self.assertEqual(unique[0]["id"], "osm-1")
        self.assertEqual(unique[0]["name"], "Barry's San Francisco - Castro")
        self.assertEqual(unique[0]["amenities"], ["showers"])

    def test_same_operator_address_with_distinct_location_ids_is_not_merged(self) -> None:
        gyms = [
            {
                "id": "pool",
                "name": "Hamilton Pool",
                "address": "1900 Geary Boulevard, San Francisco",
                "websiteUrl": "https://sfrecpark.org/pool",
                "operatorLocationId": "hamilton-pool-215",
            },
            {
                "id": "rec",
                "name": "Hamilton Recreation Center",
                "address": "1900 Geary Boulevard, San Francisco",
                "websiteUrl": "https://sfrecpark.org/rec",
                "operatorLocationId": "hamilton-recreation-center-93",
            },
        ]
        unique, collapsed = merge.collapse_known_brand_duplicates(gyms)
        self.assertEqual(collapsed, 0)
        self.assertEqual([gym["id"] for gym in unique], ["pool", "rec"])

    def test_location_overrides_can_update_or_suppress_by_stable_id(self) -> None:
        gyms = [
            {"id": "keep", "name": "Keep"},
            {"id": "update", "name": "Old"},
            {"id": "remove", "name": "Stale"},
            {"id": "hold", "name": "Ambiguous"},
        ]
        document = {
            "overrides": [
                {"id": "update", "action": "update", "name": "Current", "recordStatus": "coming_soon"},
                {"id": "remove", "action": "suppress", "reason": "stale"},
                {"id": "hold", "action": "review-hold", "recordStatus": "identity-review"},
            ]
        }
        output, suppressed, updated = merge.apply_location_overrides(gyms, document)
        self.assertEqual([gym["id"] for gym in output], ["keep", "update", "hold"])
        self.assertEqual(output[1]["name"], "Current")
        self.assertEqual(suppressed, 1)
        self.assertEqual(updated, 2)
        self.assertEqual(output[2]["recordStatus"], "identity-review")

    def test_stale_raw_aliases_are_suppressed_before_research_address_matching(self) -> None:
        document = {"overrides": [
            {"id": "stale-osm", "action": "suppress", "reason": "reviewed stale alias"},
            {"id": "linked-alias", "action": "suppress", "canonicalId": "current"},
            {"id": "review", "action": "review-hold"},
        ]}
        self.assertEqual(merge.premerge_suppressed_ids(document), {"stale-osm"})

    def test_suppressed_alias_can_link_to_canonical_without_overwriting_current_fields(self) -> None:
        gyms = [
            {"id": "stale", "name": "Longer Stale Studio Name", "address": "100 Market Street", "sourceUrl": "https://www.openstreetmap.org/node/1", "monthlyPrice": None},
            {"id": "current", "name": "Current Fit", "address": "100 Market Street", "sourceUrl": "https://current.example/", "websiteUrl": "https://current.example/", "monthlyPrice": 100},
        ]
        document = {
            "overrides": [
                {"id": "stale", "action": "suppress", "canonicalId": "current", "reason": "reviewed stale alias", "sourceUrl": "https://official.example/evidence"}
            ]
        }
        output, suppressed, updated = merge.apply_location_overrides(gyms, document)
        self.assertEqual(suppressed, 1)
        self.assertEqual(updated, 0)
        self.assertEqual([gym["id"] for gym in output], ["current"])
        self.assertEqual(output[0]["name"], "Current Fit")
        self.assertEqual(output[0]["monthlyPrice"], 100)
        self.assertEqual(output[0]["sourceAliases"][0]["id"], "stale")
        self.assertEqual(output[0]["sourceAliases"][0]["reason"], "reviewed stale alias")

    def test_known_operator_brand_matches_even_when_only_one_record_has_a_domain(self) -> None:
        raw = {"name": "24 Hour Fitness", "address": "1850 Ocean Avenue, San Francisco"}
        official = {"name": "24 Hour Fitness Ocean", "address": "1850 Ocean Avenue, San Francisco", "websiteUrl": "https://www.24hourfitness.com/gyms/ocean"}
        self.assertTrue(merge.same_operator(raw, official))
        self.assertEqual(merge.operator_identity(raw), merge.operator_identity(official))

    def test_livefit_compact_brand_name_uses_canonical_operator_identity(self) -> None:
        self.assertEqual(merge.brand_key("LiveFitGym Wellness Club"), "live-fit")
        self.assertEqual(
            merge.operator_identity({"name": "LiveFitGym Wellness Club", "address": "780 Valencia St"}),
            "live-fit",
        )

    def test_different_operators_at_same_address_never_auto_match(self) -> None:
        gyms = [{"name": "Former Fitness", "address": "100 Market Street, San Francisco"}]
        record = {"name": "New Training", "address": "100 Market Street, San Francisco"}
        self.assertIsNone(
            merge.address_match(record, merge.indexed_gyms(gyms, merge.normalized), merge.indexed_gyms(gyms, merge.canonical_address))
        )

    def test_unverified_osm_identity_at_current_operator_address_becomes_review_hold_candidate(self) -> None:
        gyms = [
            {"id": "osm-old", "name": "Old Studio", "address": "100 Market Street, San Francisco", "sourceUrl": "https://www.openstreetmap.org/node/1"},
            {"id": "web-current", "name": "Current Pilates", "address": "100 Market St, San Francisco, CA 94105", "websiteUrl": "https://current.example/", "sourceUrl": "https://current.example/"},
        ]
        candidates = merge.replacement_identity_candidates(gyms, {"overrides": [], "distinctPairs": []})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["holdIds"], ["osm-old"])
        self.assertEqual(candidates[0]["rightId"], "web-current")

    def test_two_current_official_colocated_operators_are_not_replacement_candidates(self) -> None:
        gyms = [
            {"id": "one", "name": "Studio One", "address": "100 Market Street", "websiteUrl": "https://one.example/"},
            {"id": "two", "name": "Studio Two", "address": "100 Market Street", "websiteUrl": "https://two.example/"},
        ]
        self.assertEqual(merge.replacement_identity_candidates(gyms, {"overrides": [], "distinctPairs": []}), [])


if __name__ == "__main__":
    unittest.main()
