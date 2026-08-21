from __future__ import annotations

import unittest

import render_official_sources as rendered


class RenderedCrawlerTests(unittest.TestCase):
    def test_network_capture_is_limited_to_operator_or_approved_booking_domains(self) -> None:
        operator = "https://example.com/pricing"
        self.assertTrue(rendered.allowed_network_response(operator, "https://api.example.com/plans.json"))
        self.assertTrue(rendered.allowed_network_response(operator, "https://clients.mindbodyonline.com/api/plans"))
        self.assertTrue(rendered.allowed_network_response(operator, "https://vrv3studioscom.onbookee.com/api/plans"))
        self.assertFalse(rendered.allowed_network_response(operator, "https://tracker.invalid/collect"))
        self.assertFalse(rendered.allowed_network_response(operator, "https://classpass.com/api/prices"))

    def test_only_neutral_public_pricing_tabs_are_clickable(self) -> None:
        self.assertTrue(rendered.is_safe_public_tab_label("Memberships"))
        self.assertTrue(rendered.is_safe_public_tab_label(" Packages "))
        self.assertFalse(rendered.is_safe_public_tab_label("Join now"))
        self.assertFalse(rendered.is_safe_public_tab_label("Create account"))

    def test_incremental_results_replace_only_processed_gym_evidence(self) -> None:
        attempts, observations = rendered.merge_incremental_results(
            [{"gymId": "keep", "url": "https://keep.example"}, {"gymId": "replace", "url": "https://old.example"}],
            [{"gymId": "keep", "amount": 10}, {"gymId": "replace", "amount": 20}],
            [{"gymId": "replace", "url": "https://new.example"}],
            [{"gymId": "replace", "amount": 30}],
            {"replace"},
        )
        self.assertEqual({item["gymId"] for item in attempts}, {"keep", "replace"})
        self.assertEqual({item["url"] for item in attempts}, {"https://keep.example", "https://new.example"})
        self.assertEqual({item["amount"] for item in observations}, {10, 30})

    def test_cross_domain_navigation_still_requires_approved_booking_host(self) -> None:
        self.assertTrue(rendered.allowed_network_response("https://operator.example", "https://tenant.pushpress.com/landing/plans"))
        self.assertFalse(rendered.allowed_network_response("https://operator.example", "https://unapproved.example/pricing"))

    def test_deal_mode_renders_all_published_commercial_operator_pages(self) -> None:
        document = {
            "gyms": [
                {"id": "open", "name": "Open Gym", "websiteUrl": "https://open.example", "publicationStatus": "publish", "recordStatus": "open", "entityKind": "gym", "accessModel": "membership", "monthlyPrice": 100},
                {"id": "park", "name": "Park", "websiteUrl": "https://park.example", "publicationStatus": "publish", "recordStatus": "open", "entityKind": "public-recreation", "accessModel": "free-public"},
                {"id": "future", "name": "Future", "websiteUrl": "https://future.example", "publicationStatus": "publish", "recordStatus": "coming_soon", "entityKind": "studio", "accessModel": "class-membership"},
            ]
        }
        candidates = rendered.candidate_gyms(document, {"attempts": []}, "deals")
        self.assertEqual([item["id"] for item in candidates], ["open"])
        weekly_candidates = rendered.candidate_gyms(document, {"attempts": []}, "weekly")
        self.assertIn("open", [item["id"] for item in weekly_candidates])

    def test_linked_booking_storefronts_are_rendered_as_first_class_targets(self) -> None:
        document = {"gyms": [{
            "id": "studio", "name": "Studio", "websiteUrl": "https://studio.example/pricing",
            "publicationStatus": "publish", "recordStatus": "open", "entityKind": "studio",
            "accessModel": "class-membership", "monthlyPrice": None,
        }]}
        attempts = {"attempts": [
            {"gymId": "studio", "url": "https://studio.example/pricing", "status": "fetched", "candidateCount": 0},
            {"gymId": "studio", "url": "https://momence.com/studio/memberships", "status": "fetched", "candidateCount": 0},
        ]}
        candidates = rendered.candidate_gyms(document, attempts, "weekly")
        self.assertEqual({item["websiteUrl"] for item in candidates}, {
            "https://studio.example/pricing", "https://momence.com/studio/memberships",
        })


if __name__ == "__main__":
    unittest.main()
