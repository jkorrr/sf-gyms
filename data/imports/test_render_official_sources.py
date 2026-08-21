from __future__ import annotations

import unittest

import render_official_sources as rendered


class RenderedCrawlerTests(unittest.TestCase):
    def test_rendered_observation_preserves_reviewed_catalog_target(self) -> None:
        candidate = {
            "sourceUrl": "https://tenant.momence.com/api/products",
            "sourceProductId": "plan-4",
            "amount": 119,
        }
        observation = rendered.rendered_observation(
            {"id": "studio", "name": "Studio"},
            candidate,
            "2026-08-21",
            "https://momence.com/studio/memberships",
        )
        self.assertEqual(observation["catalogSourceUrl"], "https://momence.com/studio/memberships")
        self.assertEqual(observation["sourceUrl"], "https://tenant.momence.com/api/products")

    def test_cloudflare_security_check_is_classified_without_bypass(self) -> None:
        blocker = rendered.detect_access_blocker(
            "Security Check",
            "Performing security verification. Verify you are human.",
            '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>',
        )
        self.assertEqual(blocker, "platform-security-check")

    def test_empty_aws_waf_shell_is_access_blocker(self) -> None:
        blocker = rendered.detect_access_blocker(
            "Bookee",
            "",
            '<script src="https://example.sdk.awswaf.com/challenge.js"></script>'
            '<script>localStorage.getItem("aws-waf-token")</script>',
        )
        self.assertEqual(blocker, "platform-security-check")

    def test_hidden_wix_captcha_code_does_not_block_visible_pricing(self) -> None:
        blocker = rendered.detect_access_blocker(
            "Plans & Pricing",
            "$125 Monthly Individual Membership. Buy now.",
            '<script>const captchaSecurity = "verify human";</script>',
        )
        self.assertEqual(blocker, "")

    def test_strong_visible_waitlist_copy_creates_review_signal(self) -> None:
        self.assertEqual(
            rendered.detect_availability_signal(
                "All semi-private classes are currently full. We are not accepting new members at this time."
            ),
            "enrollment-paused",
        )
        self.assertEqual(
            rendered.detect_availability_signal("Join a class today or ask about future programs."),
            "",
        )

    def test_rendered_observation_redacts_contact_data_from_audit_labels(self) -> None:
        synthetic_phone = "415" + "-555-0100"
        synthetic_email = "price" + "@example.com"
        observation = rendered.rendered_observation(
            {"id": "gym-1", "name": "Example Gym"},
            {
                "rawLabel": f"Text {synthetic_phone} or {synthetic_email} for the $30 day pass",
                "sourceUrl": "https://example.com/pricing",
            },
            "2026-08-21",
            "https://example.com/pricing",
        )

        self.assertEqual(
            observation["rawLabel"],
            "Text [phone redacted] or [email redacted] for the $30 day pass",
        )
        self.assertEqual(observation["sourceUrl"], "https://example.com/pricing")

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
        self.assertTrue(rendered.is_safe_mindbody_contract_link_label("Contracts"))
        self.assertTrue(rendered.is_safe_mindbody_contract_link_label("Monthly Memberships"))
        self.assertFalse(rendered.is_safe_mindbody_contract_link_label("Cart"))
        self.assertFalse(rendered.is_safe_mindbody_contract_link_label("Account"))

    def test_public_tab_links_cannot_navigate_off_the_exact_location_page(self) -> None:
        current = "https://operator.example/locations/sf#pricing"
        self.assertTrue(rendered.safe_public_tab_href(current, "#memberships"))
        self.assertTrue(rendered.safe_public_tab_href(current, ""))
        self.assertFalse(rendered.safe_public_tab_href(current, "https://operator.example/locations/sf"))
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

    def test_incremental_merge_redacts_carried_forward_contact_data(self) -> None:
        synthetic_phone = "415" + ".555.0100"
        synthetic_email = "legacy" + "@example.com"

        _attempts, observations = rendered.merge_incremental_results(
            [],
            [{
                "gymId": "keep",
                "amount": 30,
                "rawLabel": f"Call {synthetic_phone} or {synthetic_email} for a day pass",
                "sourceUrl": "https://keep.example/pricing",
            }],
            [],
            [],
            set(),
        )

        self.assertEqual(
            observations[0]["rawLabel"],
            "Call [phone redacted] or [email redacted] for a day pass",
        )
        self.assertEqual(observations[0]["sourceUrl"], "https://keep.example/pricing")

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

    def test_bookee_public_pricing_fragment_is_preserved_but_checkout_fragment_is_not(self) -> None:
        gym = {
            "id": "vrv3", "name": "VRV3 Studios", "address": "520 Haight Street",
            "websiteUrl": "https://www.vrv3studios.com/",
            "officialUrl": "https://www.vrv3studios.com/",
            "priceSourceUrl": "https://www.vrv3studios.com/schedule#/pricing/r/1154/loc/1211",
        }
        targets = rendered.render_target_urls(gym, [
            {"url": "https://www.vrv3studios.com/schedule#/pricing/buy/r/1154/loc/1211?id=28057", "status": "fetched"},
            {"url": "https://vrv3studioscom.onbookee.com/public/forms/waiver-id", "status": "fetched"},
        ])
        self.assertIn("https://www.vrv3studios.com/schedule#/pricing/r/1154/loc/1211", targets)
        self.assertIn("https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211", targets)
        self.assertNotIn("https://vrv3studioscom.onbookee.com/public/forms/waiver-id", targets)
        self.assertNotIn("https://www.vrv3studios.com/schedule#/pricing/buy/r/1154/loc/1211?id=28057", targets)

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

    def test_same_operator_global_pricing_route_is_rendered_for_location_page(self) -> None:
        gym = {
            "id": "core", "name": "CORE MVMT - Castro", "address": "2349 Market Street",
            "websiteUrl": "https://thecoremvmt.com/location/castro/",
            "officialUrl": "https://thecoremvmt.com/location/castro/",
            "priceSourceUrl": "https://thecoremvmt.marianatek.com/api/customer/v1/locations/48717/buy-page",
        }
        attempts = [
            {"url": "https://thecoremvmt.com/pricing", "status": "fetched"},
            {"url": "https://thecoremvmt.com/location/downtown/buy/", "status": "fetched"},
        ]
        targets = rendered.render_target_urls(gym, attempts)
        self.assertIn("https://thecoremvmt.com/pricing", targets)
        self.assertNotIn("https://thecoremvmt.com/location/downtown/buy/", targets)

    def test_approach_location_selector_prefers_exact_street(self) -> None:
        gym = {"name": "Benchmark Climbing", "address": "1414 Van Ness Avenue, San Francisco, CA 94109"}
        sf = rendered.score_location_label(gym, "San Francisco — 1414 Van Ness Ave")
        berkeley = rendered.score_location_label(gym, "Berkeley — 1607 Shattuck Ave")
        self.assertGreaterEqual(sf, 8)
        self.assertGreater(sf, berkeley)

    def test_operator_product_key_collapses_api_and_dom_variants(self) -> None:
        api = {
            "sourceUrl": "https://tenant.marianatek.com/api/customer/v1/buy-page",
            "sourceProductId": "memberships-14787", "amount": 118,
        }
        dom = {
            "sourceUrl": "https://tenant.marianaiframes.com/iframe/buy/48717",
            "sourceProductId": "memberships-14787", "amount": 118,
        }
        other = {**dom, "sourceProductId": "memberships-14789"}
        self.assertEqual(rendered.operator_product_key(api), rendered.operator_product_key(dom))
        self.assertNotEqual(rendered.operator_product_key(api), rendered.operator_product_key(other))

    def test_public_browser_capture_is_validated_and_keeps_original_observation_date(self) -> None:
        gym = {
            "id": "vrv3", "name": "VRV3 Studios", "publicationStatus": "publish",
            "websiteUrl": "https://www.vrv3studios.com/",
            "officialUrl": "https://www.vrv3studios.com/",
            "priceSourceUrl": "https://www.vrv3studios.com/schedule#/pricing/r/1154/loc/1211",
        }
        captures = {"captures": [{
            "gymId": "vrv3",
            "sourceUrl": "https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211",
            "catalogSourceUrl": gym["priceSourceUrl"],
            "capturedAt": "2026-08-21",
            "cards": [{
                "serviceGroupId": "7800",
                "productName": "Monthly Membership (4x Classes, 3M)",
                "displayedPrice": "$149/month",
                "locationLabel": "San Francisco",
                "sectionLabel": "Monthly Memberships (3 Month Commitment)",
                "cardText": "Monthly Membership (4x Classes, 3M)\n$149/month\n4 credit\nSubscription\nRenews in 1 month\nSan Francisco\nRequires a 3-month commitment.",
            }],
        }]}

        observations = rendered.public_browser_capture_observations(
            {"gyms": [gym]}, captures, {"vrv3"},
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["capturedAt"], "2026-08-21")
        self.assertEqual(observations[0]["amount"], 149)
        self.assertIn("four-monthly-3m", observations[0]["sourceProductAliases"])
        self.assertEqual(observations[0]["method"], "captured-public-bookee-product-card")
        self.assertRegex(observations[0]["contentHash"], r"^[0-9a-f]{64}$")

    def test_public_browser_capture_rejects_unlinked_or_checkout_sources(self) -> None:
        gym = {
            "id": "vrv3", "name": "VRV3 Studios", "publicationStatus": "publish",
            "websiteUrl": "https://www.vrv3studios.com/",
            "officialUrl": "https://www.vrv3studios.com/",
            "priceSourceUrl": "https://www.vrv3studios.com/schedule#/pricing/r/1154/loc/1211",
        }
        capture = {
            "gymId": "vrv3", "capturedAt": "2026-08-21", "cards": [{}],
            "sourceUrl": "https://vrv3studioscom.onbookee.com/pricing/buy/r/1154/loc/1211",
            "catalogSourceUrl": "https://unlinked.example/pricing",
        }
        self.assertEqual(
            rendered.public_browser_capture_observations(
                {"gyms": [gym]}, {"captures": [capture]}, {"vrv3"},
            ),
            [],
        )

    def test_mindbody_public_browser_capture_reconstructs_contract_and_drop_in(self) -> None:
        gym = {
            "id": "yoga-flow-noe", "name": "Yoga Flow Noe", "publicationStatus": "publish",
            "websiteUrl": "https://yogaflowsf.com/noe/",
            "officialUrl": "https://yogaflowsf.com/noe/",
            "priceSourceUrl": "https://yogaflowsf.com/membership/",
        }
        source_url = "https://clients.mindbodyonline.com/classic/ws?studioid=5732277&stype=41"
        captures = {"captures": [{
            "gymId": gym["id"], "sourceUrl": source_url,
            "catalogSourceUrl": gym["priceSourceUrl"], "capturedAt": "2026-08-21",
            "locationLabel": "Yoga Flow SF - Noe | 4049 24th Street, San Francisco, CA 94114",
            "contractCards": [{
                "productId": "104", "productName": "4 Class Monthly Membership",
                "contractText": "4 Class Monthly Membership\n4 in-studio classes each month at the selected studio\n$100.00 every month\nMonth-to-month commitment; cancel anytime.",
            }],
            "purchaseRows": [{
                "categoryLabel": "Classes", "productId": "100002",
                "cardText": "In-Studio Drop-In - Noe\n$35.00",
            }],
        }]}

        observations = rendered.public_browser_capture_observations(
            {"gyms": [gym]}, captures, {gym["id"]},
        )

        self.assertEqual(len(observations), 2)
        by_method = {item["method"]: item for item in observations}
        contract = by_method["captured-public-mindbody-contract"]
        drop_in = by_method["captured-public-mindbody-purchase-item"]
        self.assertEqual((contract["amount"], contract["classAllowance"]["count"]), (100, 4))
        self.assertIn("membership-4", contract["sourceProductAliases"])
        self.assertEqual((drop_in["amount"], drop_in["cadence"]), (35, "visit"))
        self.assertIn("noe-drop-in", drop_in["sourceProductAliases"])
        self.assertEqual(contract["capturedAt"], "2026-08-21")

    def test_mindbody_public_browser_capture_rejects_checkout_query(self) -> None:
        gym = {
            "id": "yoga-flow-noe", "name": "Yoga Flow Noe", "publicationStatus": "publish",
            "websiteUrl": "https://yogaflowsf.com/noe/",
            "officialUrl": "https://yogaflowsf.com/noe/",
            "priceSourceUrl": "https://yogaflowsf.com/membership/",
        }
        capture = {
            "gymId": gym["id"], "capturedAt": "2026-08-21", "locationLabel": "Noe",
            "sourceUrl": "https://clients.mindbodyonline.com/classic/ws?studioid=5732277&stype=41&prodid=104",
            "catalogSourceUrl": gym["priceSourceUrl"], "contractCards": [{}], "purchaseRows": [],
        }
        self.assertEqual(
            rendered.public_browser_capture_observations(
                {"gyms": [gym]}, {"captures": [capture]}, {gym["id"]},
            ),
            [],
        )

    def test_operator_site_browser_capture_routes_bounded_cards_and_rate_tables(self) -> None:
        gym = {
            "id": "independent-gym", "name": "Independent Gym", "publicationStatus": "publish",
            "websiteUrl": "https://gym.example/", "officialUrl": "https://gym.example/",
            "priceSourceUrl": "https://gym.example/membership-agreement/",
        }
        captures = {"captures": [{
            "gymId": gym["id"], "sourceUrl": gym["priceSourceUrl"],
            "catalogSourceUrl": gym["priceSourceUrl"], "capturedAt": "2026-08-21",
            "cards": [{
                "productName": "Core Access", "displayedPrice": "$165/month",
                "sectionLabel": "Memberships",
                "cardText": "CORE ACCESS $165/month Full solo facility access at all locations.",
            }],
            "rateTableText": (
                "Membership Types: Recurring Term Membership (4-Week Plan): "
                "Sign-up Fee: $49.99 Cancellation Fee: None Commitment: No long-term commitment "
                "Cost: $107.95 per 4-week period Description: Full facility access on a recurring basis. "
                "1 Day Pass: Sign-up Fee: None Cost: $34.95 per day Description: One facility visit."
            ),
        }]}

        observations = rendered.public_browser_capture_observations(
            {"gyms": [gym]}, captures, {gym["id"]},
        )

        by_id = {item["sourceProductId"]: item for item in observations}
        self.assertEqual(by_id["core-access"]["amount"], 165)
        self.assertEqual(by_id["core-access"]["method"], "captured-public-operator-plan-card")
        self.assertEqual(by_id["recurring-four-week"]["amount"], 107.95)
        self.assertEqual(by_id["recurring-four-week"]["fees"][0]["amount"], 49.99)
        self.assertEqual(by_id["day-pass"]["amount"], 34.95)
        self.assertTrue(all(item["capturedAt"] == "2026-08-21" for item in observations))


if __name__ == "__main__":
    unittest.main()
