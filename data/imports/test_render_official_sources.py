from __future__ import annotations

import unittest

import render_official_sources as rendered


class RenderedCrawlerTests(unittest.TestCase):
    def test_cloudflare_security_check_is_classified_without_bypass(self) -> None:
        blocker = rendered.detect_access_blocker(
            "Security Check",
            "Performing security verification. Verify you are human.",
            '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>',
        )
        self.assertEqual(blocker, "platform-security-check")

    def test_recent_access_block_is_skipped_until_monthly_full_retry(self) -> None:
        document = {"gyms": [{
            "id": "studio", "name": "Studio", "websiteUrl": "https://clients.mindbodyonline.com/store",
            "publicationStatus": "publish", "recordStatus": "open", "entityKind": "studio",
            "accessModel": "class-membership", "monthlyPrice": None,
        }]}
        rendered_attempts = {"attempts": [{
            "gymId": "studio", "url": "https://clients.mindbodyonline.com/store",
            "status": "access-blocked", "accessBlocker": "platform-security-check", "attemptedAt": "2026-08-20",
        }]}
        self.assertEqual(rendered.candidate_gyms(document, {"attempts": []}, "weekly", rendered_attempts, "2026-08-21"), [])
        self.assertEqual(len(rendered.candidate_gyms(document, {"attempts": []}, "full", rendered_attempts, "2026-08-21")), 1)

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
        self.assertTrue(rendered.is_safe_public_tab_label("Personal Training"))
        self.assertTrue(rendered.is_safe_public_tab_label("Monthly"))
        self.assertTrue(rendered.is_safe_public_tab_label("12-Month"))
        self.assertTrue(rendered.is_safe_public_tab_label("Flexible"))
        self.assertFalse(rendered.is_safe_public_tab_label("Join now"))
        self.assertFalse(rendered.is_safe_public_tab_label("Buy"))
        self.assertFalse(rendered.is_safe_public_tab_label("Create account"))
        self.assertIn("label[for]", rendered.PUBLIC_TAB_SELECTOR)

    def test_mindbody_category_selector_avoids_account_and_gift_actions(self) -> None:
        self.assertTrue(rendered.is_safe_mindbody_category_label("In Studio Memberships"))
        self.assertTrue(rendered.is_safe_mindbody_category_label("Open Pole"))
        self.assertFalse(rendered.is_safe_mindbody_category_label("Select item"))
        self.assertFalse(rendered.is_safe_mindbody_category_label("Gift Cards"))

    def test_mindbody_contract_navigation_is_same_host_and_contract_only(self) -> None:
        current = "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=5734215&pMode=1"
        self.assertTrue(rendered.safe_mindbody_contract_href(current, "/asp/main_shop.asp?pMode=0&tabID=3"))
        self.assertFalse(rendered.safe_mindbody_contract_href(current, "/asp/main_shop.asp?pMode=4&tabID=3"))
        self.assertFalse(rendered.safe_mindbody_contract_href(current, "https://example.com/asp/main_shop.asp?pMode=0"))

    def test_public_tab_links_cannot_navigate_off_the_exact_location_page(self) -> None:
        current = "https://operator.example/locations/sf#pricing"
        self.assertTrue(rendered.safe_public_tab_href(current, "#memberships"))
        self.assertTrue(rendered.safe_public_tab_href(current, ""))
        self.assertFalse(rendered.safe_public_tab_href(current, "/memberships"))
        self.assertFalse(rendered.safe_public_tab_href(current, "https://operator.example/pricing"))

    def test_crunch_render_prefers_attached_promotion_over_detached_summary_amount(self) -> None:
        candidates = [
            {"amount": 80.75, "productType": "monthly", "promotion": {"isPromotion": False}},
            {
                "sourceProductId": "one-crunch-current-offer", "amount": 80.75,
                "productType": "monthly", "promotion": {"isPromotion": True},
            },
            {
                "sourceProductId": "one-crunch-regular", "amount": 95,
                "productType": "monthly", "promotion": {"isPromotion": False},
            },
        ]
        filtered = rendered.remove_unattached_crunch_promotions(
            candidates, "https://www.crunch.com/locations/chestnut"
        )
        self.assertEqual([candidate.get("sourceProductId") for candidate in filtered], [
            "one-crunch-current-offer", "one-crunch-regular",
        ])

    def test_incremental_results_replace_only_processed_gym_evidence(self) -> None:
        attempts, observations = rendered.merge_incremental_results(
            [{"gymId": "keep", "url": "https://keep.example"}, {"gymId": "replace", "url": "https://old.example"}],
            [{"gymId": "keep", "amount": 10}, {"gymId": "replace", "amount": 20}],
            [{"gymId": "replace", "url": "https://new.example"}],
            [{"gymId": "replace", "amount": 30}, {"gymId": "replace", "amount": 30}],
            {"replace"},
        )
        self.assertEqual({item["gymId"] for item in attempts}, {"keep", "replace"})
        self.assertEqual({item["url"] for item in attempts}, {"https://keep.example", "https://new.example"})
        self.assertEqual({item["amount"] for item in observations}, {10, 30})
        self.assertEqual(len(observations), 2)

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

    def test_restricted_facility_with_public_platform_catalog_is_rendered(self) -> None:
        document = {"gyms": [{
            "id": "trainer", "name": "Trainer Studio", "websiteUrl": "https://trainer.example/",
            "officialUrl": "https://trainer.example/", "priceSourceUrl": "https://trainer.janeapp.com/",
            "publicationStatus": "publish", "recordStatus": "open", "entityKind": "studio",
            "accessModel": "restricted", "monthlyPrice": None,
        }]}
        candidates = rendered.candidate_gyms(document, {"attempts": []}, "weekly")
        self.assertEqual({item["websiteUrl"] for item in candidates}, {
            "https://trainer.example/", "https://trainer.janeapp.com/",
        })

    def test_price_source_and_same_operator_research_pages_are_render_targets(self) -> None:
        gym = {
            "id": "studio", "name": "Studio", "address": "1414 Van Ness Avenue, San Francisco, CA",
            "websiteUrl": "https://studio.example/", "officialUrl": "https://studio.example/location/sf",
            "priceSourceUrl": "https://studio.example/pricing#plans",
        }
        attempts = [
            {"url": "https://studio.example/memberships", "status": "fetched"},
            {"url": "https://studio.example/about", "status": "fetched"},
            {"url": "https://benchmark.portal.approach.app/membership-type/3", "status": "fetched"},
        ]
        self.assertEqual(rendered.render_target_urls(gym, attempts), [
            "https://studio.example/",
            "https://studio.example/location/sf",
            "https://studio.example/pricing",
            "https://studio.example/memberships",
            "https://benchmark.portal.approach.app/membership-type/3",
        ])

    def test_same_operator_routes_must_match_a_stable_location_slug(self) -> None:
        gym = {
            "id": "bar", "name": "The Bar Method FiDi", "address": "234 Bush Street",
            "websiteUrl": "https://barmethod.com/locations/san-francisco-fidi/",
            "officialUrl": "https://barmethod.com/locations/san-francisco-fidi/",
            "priceSourceUrl": "https://barmethod.marianatek.com/api/customer/v1/locations/49114/buy-page",
        }
        attempts = [
            {"url": "https://barmethod.com/locations/san-francisco-fidi/buy/", "status": "fetched"},
            {"url": "https://barmethod.com/locations/san-francisco-downtown/buy/", "status": "fetched"},
        ]
        targets = rendered.render_target_urls(gym, attempts)
        self.assertIn("https://barmethod.com/locations/san-francisco-fidi/buy/", targets)
        self.assertNotIn("https://barmethod.com/locations/san-francisco-downtown/buy/", targets)

    def test_approach_location_selector_prefers_exact_street(self) -> None:
        gym = {"name": "Benchmark Climbing", "address": "1414 Van Ness Avenue, San Francisco, CA 94109"}
        sf = rendered.score_location_label(gym, "San Francisco — 1414 Van Ness Ave")
        berkeley = rendered.score_location_label(gym, "Berkeley — 1607 Shattuck Ave")
        self.assertGreaterEqual(sf, 8)
        self.assertGreater(sf, berkeley)


if __name__ == "__main__":
    unittest.main()
