from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import unittest
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import crawl_official_sources as crawler


class OfficialCrawlerTests(unittest.TestCase):
    def test_parser_upgrade_invalidates_only_candidate_cache_metadata(self) -> None:
        stale = {"etag": "old", "lastModified": "yesterday", "candidates": [{"amount": 149}]}
        current = {**stale, "parserVersion": crawler.PARSER_VERSION}

        self.assertIsNone(crawler.conditional_cache_metadata(stale))
        self.assertIs(crawler.conditional_cache_metadata(current), current)

    def test_static_deal_refresh_can_retain_rendered_observations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rendered.json"
            path.write_text(json.dumps({"observations": [{"gymId": "gym-1", "amount": 99}]}), encoding="utf-8")

            observations = crawler.load_rendered_deal_observations(path)

        self.assertEqual(observations, [{"gymId": "gym-1", "amount": 99}])

    def test_visible_official_ranges_and_starting_prices_are_non_scalar_candidates(self) -> None:
        candidates = crawler.visible_cost_context_candidates(
            "1-on-1 Training $150–$250 per session. Classes start at $200 per month.",
            "https://operator.example/rates",
        )

        self.assertEqual([(item["kind"], item["low"], item["high"]) for item in candidates], [
            ("range", 150, 250), ("starting-price", 200, 200),
        ])
        self.assertTrue(all("amount" not in item and item["selectable"] is False for item in candidates))

    def test_visible_cost_context_rejects_promotions_and_bare_numeric_ranges(self) -> None:
        candidates = crawler.visible_cost_context_candidates(
            "First month membership special $99-$129. Dimensions are $100-$200.",
            "https://operator.example/rates",
        )

        self.assertEqual(candidates, [])

    def test_json_ld_aggregate_offer_is_range_not_false_exact_price(self) -> None:
        block = json.dumps({
            "@type": "AggregateOffer",
            "name": "Adult Training Memberships",
            "lowPrice": 150,
            "highPrice": 250,
            "priceCurrency": "USD",
        })

        candidates = crawler.structured_candidates([block], "https://operator.example/pricing")

        self.assertEqual(len(candidates), 1)
        self.assertEqual((candidates[0]["kind"], candidates[0]["low"], candidates[0]["high"]), ("range", 150, 250))
        self.assertNotIn("amount", candidates[0])

    def test_reviewed_seed_routes_follow_operator_evidence_and_public_booking_only(self) -> None:
        gym = {
            "websiteUrl": "https://www.operator.example/",
            "officialUrl": "https://operator.example/location/sf#details",
            "priceSourceUrl": "https://operator.example/pricing#plans",
            "sourceUrl": "https://directory.example/operator",
            "plans": [
                {"evidence": {"url": "https://momence.com/operator/memberships#current"}},
                {"evidence": {"url": "https://unrelated.example/pricing"}},
            ],
            "dropIns": [{"sourceEvidence": {"url": "https://operator.example/checkout"}}],
            "costContext": [
                {"sourceUrl": "https://operator.example/pricing#range"},
                {"sourceUrl": "https://classpass.com/studios/operator"},
            ],
        }

        routes = crawler.reviewed_seed_routes(gym)

        self.assertEqual(
            routes,
            [
                {"url": "https://www.operator.example/", "sourceField": "websiteUrl"},
                {"url": "https://operator.example/location/sf", "sourceField": "officialUrl"},
                {"url": "https://operator.example/pricing", "sourceField": "priceSourceUrl"},
                {"url": "https://momence.com/operator/memberships", "sourceField": "plans.evidence.url"},
            ],
        )

    def test_reviewed_seed_routes_allow_reviewed_operator_subdomains_only(self) -> None:
        gym = {
            "websiteUrl": "https://www.operator.example/location/sf",
            "officialUrl": "https://www.operator.example/location/sf",
            "priceSourceUrl": "https://app.operator.example/pricing?location=sf",
            "plans": [
                {"evidence": {"url": "https://help.operator.example/memberships"}},
                {"evidence": {"url": "https://operator.example.evil.test/pricing"}},
            ],
        }

        routes = crawler.reviewed_seed_routes(gym)

        self.assertIn(
            {"url": "https://app.operator.example/pricing?location=sf", "sourceField": "priceSourceUrl"},
            routes,
        )
        self.assertIn(
            {"url": "https://help.operator.example/memberships", "sourceField": "plans.evidence.url"},
            routes,
        )
        self.assertFalse(any("evil.test" in item["url"] for item in routes))

    def test_ymca_sf_adapter_separates_monthly_dues_and_join_fees(self) -> None:
        visible = """
        Become A Member Membership Types
        Teen Individuals ages 13-18 Monthly Fee $55 join fee $149 VISIT a branch
        Young Adult Individuals ages 19-25 Monthly Fee $75 join fee $149 JOIN
        Adult Individuals ages 26-66 Monthly Fee $91 join fee $149 JOIN
        Active Older Adult Individuals ages 67+ Monthly Fee $85 join fee $149 JOIN
        Single Adult Household with Children One adult plus dependents Monthly Fee $112 join fee $199 JOIN
        Dual Adult Household with no Children Two adults Monthly Fee $152 join fee $199 JOIN
        Dual Adult Household with Children Two adults plus dependents Monthly Fee $182 join fee $199 JOIN
        Household members save up to $340 per month.
        """

        candidates = crawler.visible_candidates(visible, "https://www.ymcasf.org/membership/")

        self.assertEqual(len(candidates), 7)
        adult = next(item for item in candidates if item["sourceProductId"] == "adult")
        self.assertEqual((adult["amount"], adult["cadence"], adult["eligibility"]["type"]), (91, "month", "standard-adult"))
        self.assertEqual(adult["fees"], [{
            "type": "enrollment", "amount": 149, "currency": "USD", "cadence": "one-time", "mandatory": True,
        }])
        self.assertEqual(adult["exactLocationMatch"], "operator-market-multi-location")
        self.assertFalse(any(item["amount"] == 340 for item in candidates))

    def test_json_ld_product_preserves_billing_increment_and_product_identity(self) -> None:
        payload = {
            "@context": "https://schema.org",
            "@graph": [{
                "@type": "Product",
                "name": "F45 Unlimited | Month to Month | Biweekly Autopay",
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "USD",
                    "price": 155,
                    "priceSpecification": {
                        "@type": "UnitPriceSpecification",
                        "price": 155,
                        "billingIncrement": 2,
                        "unitCode": "WEEK",
                        "eligibleQuantity": {
                            "@type": "QuantitativeValue", "unitCode": "WEEK", "minValue": 6,
                        },
                    },
                },
            }],
        }
        candidates = crawler.structured_candidates(
            [json.dumps(payload)], "https://f45training.com/studio/citycentersf/",
        )
        recurring = next(item for item in candidates if item["rawLabel"].startswith("F45 Unlimited"))

        self.assertEqual((recurring["amount"], recurring["cadence"], recurring["intervalCount"]), (155, "WEEK", 2))
        self.assertTrue(recurring["classAllowance"]["unlimited"])
        self.assertEqual(recurring["commitment"]["minimumDays"], 42)
        self.assertEqual(crawler.candidate_normalized_monthly(recurring), 335.83)

        gym = {
            "id": "f45", "monthlyPrice": 335.83, "selectedPlanId": "f45:plan:unlimited-biweekly",
            "plans": [{
                "id": "f45:plan:unlimited-biweekly", "sourceProductId": "unlimited-biweekly",
                "name": "Unlimited Membership",
                "classAllowance": {"count": None, "period": "month", "unlimited": True},
                "billing": {"amount": 155, "normalizedMonthly": 335.83},
                "evidence": {
                    "url": "https://f45training.com/studio/citycentersf/", "rawLabel": "Unlimited Membership",
                },
            }],
        }
        audit = crawler.audit_selected_plan_price(gym, [{**recurring, "gymId": "f45"}])

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["candidateNormalizedMonthly"], 335.83)

    def test_visible_monthly_toggle_state_preserves_plan_label(self) -> None:
        visible = """
        MEMBERSHIPS Monthly
        Monthly 4 Membership $145/mo* Auto-charged each month. Classes do not roll over month over month.
        Monthly 8 Membership $255/mo* Auto-charged each month.
        Monthly Unlimited Membership $385/mo* Max 1 class per day.
        """
        candidates = crawler.visible_candidates(
            visible, "https://solidcore.co/membership-perks?siteId=5723396&locationId=11",
        )
        four = next(item for item in candidates if item["amount"] == 145)

        self.assertIn("Monthly 4 Membership", four["rawLabel"])
        self.assertEqual(four["cadence"], "month")
        gym = {
            "id": "solidcore", "monthlyPrice": 145, "selectedPlanId": "solidcore:plan:four",
            "plans": [{
                "id": "solidcore:plan:four", "sourceProductId": "four-month-to-month",
                "name": "4 Classes — Month-to-Month",
                "classAllowance": {"count": 4, "period": "month", "unlimited": False},
                "billing": {"amount": 145, "normalizedMonthly": 145},
                "evidence": {
                    "url": "https://solidcore.co/membership-perks?siteId=5723396&locationId=11",
                    "rawLabel": "4 Classes — Month-to-Month",
                },
            }],
        }
        audit = crawler.audit_selected_plan_price(gym, [{**four, "gymId": "solidcore"}])

        self.assertEqual(audit["status"], "matched-within-threshold")

    def test_solidcore_committed_tab_is_not_labeled_month_to_month(self) -> None:
        visible = """
        MEMBERSHIPS 12-Month
        Committed+ 4 Membership $108/mo Save 25% with a 12-month commitment.
        Committed+ 8 Membership $191/mo Save 25% with a 12-month commitment.
        Committed+ Unlimited Membership $288/mo Save 25% with a 12-month commitment.
        Committed+ Travel Monthly Unlimited $315/mo Save 25% with a 12-month commitment.
        """

        candidates = crawler.solidcore_visible_candidates(
            visible, "https://solidcore.co/membership-perks?siteId=5723396&locationId=11",
        )

        self.assertEqual(candidates, [])

    def test_official_url_without_website_is_still_a_crawl_seed(self) -> None:
        gym = {"officialUrl": "https://operator.example/location/sf", "monthlyPrice": None}
        self.assertTrue(crawler.should_crawl(gym, {}, "full", datetime(2026, 8, 21)))

    def test_sitemap_pricing_leads_become_review_only_crawl_seeds(self) -> None:
        gym = {
            "id": "sf-location",
            "websiteUrl": "https://operator.example/locations/sf",
            "officialUrl": "https://operator.example/locations/sf",
        }
        candidates = [
            {
                "url": "https://operator.example/locations/sf/pricing",
                "candidateType": "exact-location-document",
                "matchingGymIds": ["sf-location"],
                "identityScore": 2,
                "reviewStatus": "pending",
            },
            {
                "url": "https://operator.example/memberships",
                "candidateType": "operator-document",
                "matchingGymIds": [],
                "identityScore": 1,
                "reviewStatus": "pending",
            },
            {
                "url": "https://operator.example/locations/other-city",
                "candidateType": "operator-document",
                "matchingGymIds": [],
                "identityScore": 1,
                "reviewStatus": "pending",
            },
            {
                "url": "https://evil.example/locations/sf/pricing",
                "candidateType": "exact-location-document",
                "matchingGymIds": ["sf-location"],
                "identityScore": 2,
                "reviewStatus": "pending",
            },
            {
                "url": "https://operator.example/pricing-old",
                "candidateType": "operator-document",
                "matchingGymIds": [],
                "identityScore": 1,
                "reviewStatus": "rejected",
            },
        ]

        routes = crawler.reviewed_seed_routes(gym, candidates)

        self.assertIn({"url": "https://operator.example/locations/sf/pricing", "sourceField": "operatorDocumentCandidate"}, routes)
        self.assertIn({"url": "https://operator.example/memberships", "sourceField": "operatorDocumentCandidate"}, routes)
        self.assertFalse(any("evil.example" in item["url"] or item["url"].endswith("pricing-old") for item in routes))
        self.assertFalse(any(item["url"].endswith("other-city") for item in routes))

    def test_request_identity_removes_only_presentation_and_tracking_variants(self) -> None:
        variants = [
            "https://operator.example/pricing/?locale=en&utm_source=map&location=1421#plans",
            "https://OPERATOR.example/pricing?location=1421&locale=es",
        ]

        identities = {crawler.request_identity(value) for value in variants}

        self.assertEqual(identities, {"https://operator.example/pricing?location=1421"})
        self.assertNotEqual(
            crawler.request_identity("https://operator.example/pricing?location=1421"),
            crawler.request_identity("https://operator.example/pricing?location=1422"),
        )

    def test_operator_location_links_are_bound_to_the_current_listing(self) -> None:
        gym = {
            "officialUrl": "https://www.ymcasf.org/location/bayview-hunters-point-ymca/",
            "websiteUrl": "https://www.ymcasf.org/location/bayview-hunters-point-ymca/",
        }

        self.assertTrue(crawler.operator_page_matches_gym(
            "https://www.ymcasf.org/location/bayview-hunters-point-ymca/#hours", gym,
        ))
        self.assertTrue(crawler.operator_page_matches_gym("https://www.ymcasf.org/membership/", gym))
        self.assertFalse(crawler.operator_page_matches_gym("https://www.ymcasf.org/locations/", gym))
        self.assertFalse(crawler.operator_page_matches_gym(
            "https://www.ymcasf.org/location/bayview-hunters-point-ymca/hope-sf/", gym,
        ))
        self.assertFalse(crawler.operator_page_matches_gym(
            "https://www.ymcasf.org/location/buchanan-ymca/", gym,
        ))

    def test_location_filter_supports_nested_operator_directory_shapes(self) -> None:
        gym = {
            "officialUrl": "https://www.corepoweryoga.com/yoga-studios/ca/san-francisco/duboce",
            "operatorLocationId": "duboce",
        }

        self.assertTrue(crawler.operator_page_matches_gym(
            "https://www.corepoweryoga.com/yoga-studios/ca/san-francisco/duboce/pricing", gym,
        ))
        self.assertFalse(crawler.operator_page_matches_gym(
            "https://www.corepoweryoga.com/yoga-studios/ca/san-francisco/nopa", gym,
        ))

    def test_linked_storefronts_deduplicate_locale_variants_and_keep_catalog_identity(self) -> None:
        links = [
            "/pricing?locale=en",
            "/pricing/?locale=es&utm_source=footer",
            "/pricing?location=1421&locale=en",
        ]

        routes = crawler.linked_storefronts("https://operator.example/", links)

        self.assertEqual(
            [crawler.request_identity(value) for value in routes],
            ["https://operator.example/pricing", "https://operator.example/pricing?location=1421"],
        )

    def test_operator_wide_pricing_document_rejects_other_location_slugs(self) -> None:
        self.assertTrue(crawler.is_operator_wide_pricing_document("https://operator.example/pricing"))
        self.assertTrue(crawler.is_operator_wide_pricing_document("https://operator.example/es/pricing"))
        self.assertTrue(crawler.is_operator_wide_pricing_document("https://operator.example/content/buy"))
        self.assertTrue(crawler.is_operator_wide_pricing_document("https://operator.example/membership/membership-options"))
        self.assertFalse(crawler.is_operator_wide_pricing_document("https://operator.example/es/pricing/arlington"))
        self.assertFalse(crawler.is_operator_wide_pricing_document("https://operator.example/locations/sf/pricing"))

    def test_reviewed_price_route_is_crawled_even_when_homepage_is_404(self) -> None:
        gym = {
            "id": "example-gym",
            "name": "Example Gym",
            "websiteUrl": "https://operator.example/",
            "officialUrl": "https://operator.example/",
            "priceSourceUrl": "https://operator.example/pricing",
            "monthlyPrice": None,
        }
        calls: list[str] = []

        def fake_fetch(url: str, _timeout: float, _cached: object) -> dict[str, object]:
            calls.append(url)
            if url.endswith("/pricing"):
                return {
                    "status": "fetched",
                    "url": url,
                    "html": "<p>Basic membership $99 per month.</p>",
                    "robotsStatus": "checked",
                }
            return {"status": "http-404", "url": url, "html": "", "robotsStatus": "checked"}

        with patch.object(crawler, "fetch_page", side_effect=fake_fetch), patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0):
            attempts, observations, _locations, _updates = crawler.crawl_gym(
                gym,
                {},
                datetime(2026, 8, 21),
                1,
                defaultdict(threading.Lock),
                {},
                {},
                {},
                {},
                threading.Lock(),
            )

        self.assertEqual(calls, ["https://operator.example/", "https://operator.example/pricing"])
        self.assertEqual(attempts[1]["linkedFrom"], "reviewed-record:priceSourceUrl")
        self.assertFalse(attempts[0]["allReviewedSeedsGone"])
        self.assertEqual([item["amount"] for item in observations], [99])

    def test_all_reviewed_routes_gone_creates_status_review_flag(self) -> None:
        gym = {
            "id": "closed-gym",
            "name": "Closed Gym",
            "websiteUrl": "https://closed.example/",
            "priceSourceUrl": "https://closed.example/pricing",
            "monthlyPrice": None,
        }

        def fake_fetch(url: str, _timeout: float, _cached: object) -> dict[str, object]:
            return {"status": "http-404", "url": url, "html": "", "robotsStatus": "checked"}

        with patch.object(crawler, "fetch_page", side_effect=fake_fetch), patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0):
            attempts, _observations, _locations, _updates = crawler.crawl_gym(
                gym,
                {},
                datetime(2026, 8, 21),
                1,
                defaultdict(threading.Lock),
                {},
                {},
                {},
                {},
                threading.Lock(),
            )

        self.assertTrue(attempts[0]["allReviewedSeedsGone"])
        self.assertTrue(attempts[0]["requiresReview"])
        self.assertEqual(
            attempts[0]["sourceStatusReviewReason"],
            "all-reviewed-operator-and-evidence-routes-return-404-or-410",
        )

    def test_official_range_does_not_crash_exact_price_change_detection(self) -> None:
        gym = {
            "id": "range-gym",
            "name": "Range Gym",
            "websiteUrl": "https://range.example/",
            "monthlyPrice": 100,
        }

        def fake_fetch(url: str, _timeout: float, _cached: object) -> dict[str, object]:
            return {
                "status": "fetched", "url": url, "robotsStatus": "checked",
                "html": "<p>Personal training sessions range $150-$250 per session.</p>",
            }

        with patch.object(crawler, "fetch_page", side_effect=fake_fetch), patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0):
            attempts, observations, _locations, _updates = crawler.crawl_gym(
                gym, {}, datetime(2026, 8, 21), 1, defaultdict(threading.Lock), {}, {}, {}, {}, threading.Lock(),
            )

        self.assertEqual(observations[0]["kind"], "range")
        self.assertFalse(attempts[0]["priceChangeOver20Percent"])

    def test_selected_plan_price_audit_ignores_more_expensive_alternatives(self) -> None:
        gym = {
            "id": "multi-plan",
            "monthlyPrice": 30,
            "selectedPlanId": "multi-plan:plan:silver",
            "priceSourceUrl": "https://operator.example/pricing",
            "plans": [{
                "id": "multi-plan:plan:silver", "sourceProductId": "silver", "name": "Silver Monthly",
                "billing": {"amount": 30, "normalizedMonthly": 30},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Silver Monthly"},
            }],
        }
        observations = [
            {"gymId": "multi-plan", "sourceUrl": "https://operator.example/pricing", "sourceProductId": "silver",
             "name": "Silver Monthly", "amount": 30, "cadence": "month", "promotion": {"isPromotion": False}},
            {"gymId": "multi-plan", "sourceUrl": "https://operator.example/pricing", "sourceProductId": "platinum",
             "name": "Platinum Monthly", "amount": 90, "cadence": "month", "promotion": {"isPromotion": False}},
        ]

        audit = crawler.audit_selected_plan_price(gym, observations)

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["matchMethod"], "source-product-id")
        self.assertEqual(audit["candidateNormalizedMonthly"], 30)

    def test_selected_plan_price_audit_normalizes_four_week_billing(self) -> None:
        gym = {
            "id": "four-week-plan",
            "monthlyPrice": 130,
            "selectedPlanId": "four-week-plan:plan:basic",
            "plans": [{
                "id": "four-week-plan:plan:basic", "sourceProductId": "basic", "name": "Basic",
                "billing": {"amount": 120, "normalizedMonthly": 130},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Basic"},
            }],
        }
        observations = [{
            "gymId": "four-week-plan", "sourceUrl": "https://operator.example/pricing",
            "sourceProductId": "basic", "name": "Basic", "amount": 120, "cadence": "4 weeks",
            "promotion": {"isPromotion": False},
        }]

        audit = crawler.audit_selected_plan_price(gym, observations)

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["candidateNormalizedMonthly"], 130)

    def test_selected_plan_price_audit_flags_only_a_matched_current_change(self) -> None:
        gym = {
            "id": "changed-plan",
            "monthlyPrice": 100,
            "selectedPlanId": "changed-plan:plan:basic",
            "plans": [{
                "id": "changed-plan:plan:basic", "sourceProductId": "basic", "name": "Basic",
                "billing": {"amount": 100, "normalizedMonthly": 100},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Basic"},
            }],
        }
        observations = [{
            "gymId": "changed-plan", "sourceUrl": "https://operator.example/pricing",
            "sourceProductId": "basic", "name": "Basic", "amount": 125, "cadence": "month",
            "promotion": {"isPromotion": False},
        }]
        attempts = [
            {"gymId": "changed-plan", "url": "https://operator.example/", "reviewedSeedCount": 2,
             "requiresReview": False, "priceChangeOver20Percent": True},
            {"gymId": "changed-plan", "url": "https://operator.example/pricing", "linkedFrom": "reviewed-record",
             "requiresReview": False, "priceChangeOver20Percent": False},
        ]

        crawler.reconcile_selected_plan_price_audits([gym], attempts, observations)

        self.assertEqual(attempts[0]["selectedPlanPriceAuditStatus"], "changed-over-20-percent")
        self.assertFalse(attempts[0]["priceChangeOver20Percent"])
        self.assertTrue(attempts[1]["priceChangeOver20Percent"])
        self.assertEqual(attempts[1]["priceChangeEvidence"]["candidateNormalizedMonthly"], 125)

    def test_selected_plan_price_audit_fails_closed_on_current_variant_conflict(self) -> None:
        gym = {
            "id": "variant-plan",
            "monthlyPrice": 100,
            "selectedPlanId": "variant-plan:plan:basic",
            "plans": [{
                "id": "variant-plan:plan:basic", "sourceProductId": "basic", "name": "Basic",
                "billing": {"amount": 100, "normalizedMonthly": 100},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Basic"},
            }],
        }
        observations = [
            {"gymId": "variant-plan", "sourceUrl": "https://operator.example/pricing", "sourceProductId": "basic",
             "name": "Basic", "amount": 125, "cadence": "month", "promotion": {"isPromotion": False}},
            {"gymId": "variant-plan", "sourceUrl": "https://operator.example/pricing", "sourceProductId": "basic",
             "name": "Basic", "amount": 140, "cadence": "month", "promotion": {"isPromotion": False}},
        ]
        attempts = [{
            "gymId": "variant-plan", "url": "https://operator.example/pricing", "reviewedSeedCount": 1,
            "requiresReview": False, "priceChangeOver20Percent": True,
        }]

        crawler.reconcile_selected_plan_price_audits([gym], attempts, observations)

        self.assertEqual(attempts[0]["selectedPlanPriceAuditStatus"], "ambiguous-current-variants")
        self.assertFalse(attempts[0]["priceChangeOver20Percent"])
        self.assertTrue(attempts[0]["requiresReview"])
        self.assertFalse(attempts[0]["requiresReviewBeforePriceAudit"])

        crawler.reconcile_selected_plan_price_audits([gym], attempts, observations[:1])

        self.assertEqual(attempts[0]["selectedPlanPriceAuditStatus"], "changed-over-20-percent")
        self.assertTrue(attempts[0]["requiresReview"])
        self.assertFalse(attempts[0]["requiresReviewBeforePriceAudit"])

        observations[0]["amount"] = 100
        crawler.reconcile_selected_plan_price_audits([gym], attempts, observations[:1])

        self.assertEqual(attempts[0]["selectedPlanPriceAuditStatus"], "matched-within-threshold")
        self.assertFalse(attempts[0]["requiresReview"])
        self.assertNotIn("requiresReviewBeforePriceAudit", attempts[0])

    def test_reconcile_reuses_same_operator_reviewed_market_source(self) -> None:
        source_url = "https://operator.example/pricing?market=northern-california"
        gyms = [
            {
                "id": gym_id, "operatorId": "shared-operator", "priceSourceUrl": source_url,
                "monthlyPrice": 260, "selectedPlanId": f"{gym_id}:plan:eight",
                "plans": [{
                    "id": f"{gym_id}:plan:eight", "sourceProductId": "eight", "name": "8 Classes/Month",
                    "classAllowance": {"count": 8, "period": "month", "unlimited": False},
                    "billing": {"amount": 260, "normalizedMonthly": 260},
                    "evidence": {"url": source_url, "rawLabel": "8 Classes/Month"},
                }],
            }
            for gym_id in ("castro", "fidi")
        ]
        attempts = [
            {"gymId": gym["id"], "url": source_url, "reviewedSeedCount": 1, "requiresReview": False}
            for gym in gyms
        ]
        observations = [{
            "gymId": "castro", "sourceUrl": source_url, "sourceProductId": "eight",
            "rawLabel": "Northern California 8 Classes/Month Recurring Membership",
            "amount": 260, "cadence": "month", "classAllowance": {"count": 8, "period": "month"},
            "promotion": {"isPromotion": False},
        }]

        crawler.reconcile_selected_plan_price_audits(gyms, attempts, observations)

        self.assertEqual(
            [attempt["selectedPlanPriceAuditStatus"] for attempt in attempts],
            ["matched-within-threshold", "matched-within-threshold"],
        )

    def test_reconcile_does_not_reuse_reviewed_source_across_operators(self) -> None:
        source_url = "https://booking.example/shared-pricing"
        gyms = [
            {
                "id": gym_id, "operatorId": operator_id, "priceSourceUrl": source_url,
                "monthlyPrice": 100, "selectedPlanId": f"{gym_id}:plan:basic",
                "plans": [{
                    "id": f"{gym_id}:plan:basic", "sourceProductId": "basic", "name": "Basic",
                    "billing": {"amount": 100, "normalizedMonthly": 100},
                    "evidence": {"url": source_url, "rawLabel": "Basic"},
                }],
            }
            for gym_id, operator_id in (("one", "operator-one"), ("two", "operator-two"))
        ]
        attempts = [
            {"gymId": gym["id"], "url": source_url, "reviewedSeedCount": 1, "requiresReview": False}
            for gym in gyms
        ]
        observations = [{
            "gymId": "one", "sourceUrl": source_url, "sourceProductId": "basic",
            "rawLabel": "Basic", "amount": 100, "cadence": "month", "promotion": {"isPromotion": False},
        }]

        crawler.reconcile_selected_plan_price_audits(gyms, attempts, observations)

        self.assertEqual(attempts[0]["selectedPlanPriceAuditStatus"], "matched-within-threshold")
        self.assertEqual(attempts[1]["selectedPlanPriceAuditStatus"], "selected-plan-not-observed")

    def test_selected_plan_audit_accepts_matching_product_from_public_platform_api(self) -> None:
        gym = {
            "id": "flagship", "monthlyPrice": 128.92, "selectedPlanId": "flagship:plan:weekly",
            "priceSourceUrl": "https://momence.com/Flagship/membership/1x-per-Week/237774",
            "plans": [{
                "id": "flagship:plan:weekly", "sourceProductId": "237774", "name": "1x per Week",
                "billing": {"amount": 119, "normalizedMonthly": 128.92},
                "evidence": {
                    "url": "https://momence.com/Flagship/membership/1x-per-Week/237774",
                    "rawLabel": "1x per Week",
                },
            }],
        }
        observation = {
            "gymId": "flagship", "sourceProductId": "237774", "amount": 119,
            "rawLabel": "1x per Week", "cadence": "week", "intervalCount": 1,
            "promotion": {"isPromotion": False},
            "sourceUrl": "https://momence.com/_api/primary/plugin/memberships/237774",
        }

        audit = crawler.audit_selected_plan_price(gym, [observation])

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["sourceProductId"], "237774")

        observation["sourceProductId"] = "different-product"
        self.assertEqual(
            crawler.audit_selected_plan_price(gym, [observation])["status"],
            "selected-plan-not-observed",
        )

    def test_reconcile_shares_public_platform_api_product_for_same_declared_source(self) -> None:
        price_source = "https://momence.com/Flagship/membership/1x-per-Week/237774"
        gyms = [
            {
                "id": gym_id, "operatorId": "flagship", "priceSourceUrl": price_source,
                "monthlyPrice": 128.92, "selectedPlanId": f"{gym_id}:plan:weekly",
                "plans": [{
                    "id": f"{gym_id}:plan:weekly", "sourceProductId": "237774", "name": "1x per Week",
                    "billing": {"amount": 119, "normalizedMonthly": 128.92},
                    "evidence": {"url": price_source, "rawLabel": "1x per Week"},
                }],
            }
            for gym_id in ("castro", "marina")
        ]
        attempts = [
            {"gymId": gym["id"], "url": price_source, "reviewedSeedCount": 1, "requiresReview": False}
            for gym in gyms
        ]
        observations = [{
            "gymId": "castro", "sourceProductId": "237774", "amount": 119,
            "rawLabel": "1x per Week", "cadence": "week", "intervalCount": 1,
            "promotion": {"isPromotion": False},
            "sourceUrl": "https://momence.com/_api/primary/plugin/memberships/237774",
        }]

        crawler.reconcile_selected_plan_price_audits(gyms, attempts, observations)

        self.assertEqual(
            [attempt["selectedPlanPriceAuditStatus"] for attempt in attempts],
            ["matched-within-threshold", "matched-within-threshold"],
        )

    def test_selected_plan_label_match_rejects_multi_price_visible_snippet(self) -> None:
        selected = {"name": "Unlimited Month-to-Month", "sourceProductId": ""}
        candidate = {
            "rawLabel": "Unlimited $180, Unlimited $200, Part-Time Membership $140 per month",
            "sourceProductId": "",
        }

        self.assertIsNone(crawler.selected_plan_candidate_match(selected, candidate))

    def test_selected_plan_price_audit_rejects_add_on_with_same_plan_name(self) -> None:
        gym = {
            "id": "core-access",
            "monthlyPrice": 165,
            "selectedPlanId": "core-access:plan:core",
            "plans": [{
                "id": "core-access:plan:core", "sourceProductId": "core", "name": "Core Access",
                "billing": {"amount": 165, "normalizedMonthly": 165},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Core Access"},
            }],
        }
        observations = [{
            "gymId": "core-access", "sourceUrl": "https://operator.example/pricing",
            "rawLabel": "Additional fee: Core Access for just $50/month", "amount": 50,
            "cadence": "month", "promotion": {"isPromotion": False},
        }]

        audit = crawler.audit_selected_plan_price(gym, observations)

        self.assertEqual(audit["status"], "selected-plan-not-observed")

    def test_selected_plan_price_audit_matches_adult_month_to_month_facets(self) -> None:
        gym = {
            "id": "adult-membership",
            "monthlyPrice": 250,
            "selectedPlanId": "adult-membership:plan:monthly",
            "plans": [{
                "id": "adult-membership:plan:monthly", "sourceProductId": "monthly",
                "name": "Adult Month-to-Month", "billing": {"amount": 250, "normalizedMonthly": 250},
                "evidence": {"url": "https://operator.example/join", "rawLabel": "Adult Month-to-Month"},
            }],
        }
        observations = [
            {"gymId": "adult-membership", "sourceUrl": "https://operator.example/join",
             "rawLabel": "Additional children $125/mo", "amount": 125, "cadence": "month",
             "promotion": {"isPromotion": False}},
            {"gymId": "adult-membership", "sourceUrl": "https://operator.example/join",
             "rawLabel": "Month-to-month contract. Adult Membership $220/mo", "amount": 220,
             "cadence": "month", "promotion": {"isPromotion": False}},
        ]

        audit = crawler.audit_selected_plan_price(gym, observations)

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["matchMethod"], "plan-facets")
        self.assertEqual(audit["candidateNormalizedMonthly"], 220)

    def test_selected_plan_price_audit_rejects_unlimited_alternative(self) -> None:
        selected = {
            "name": "3x Weekly Month-to-Month", "sourceProductId": "",
            "classAllowance": {"count": 3, "period": "week", "unlimited": False},
        }
        candidate = {
            "rawLabel": "Unlimited memberships — Month-to-Month $320/month", "sourceProductId": "",
            "classAllowance": {"count": None, "period": "month", "unlimited": True},
        }

        self.assertIsNone(crawler.selected_plan_candidate_match(selected, candidate))

    def test_selected_plan_match_ignores_attached_setup_fee_amount(self) -> None:
        selected = {
            "name": "1x/Week Reservation Plan", "sourceProductId": "",
            "classAllowance": {"count": 1, "period": "week", "unlimited": False},
        }
        candidate = {
            "rawLabel": "+$75 setup 1x Per Week $129.00 / month", "amount": 129,
            "sourceProductId": "",
        }

        match = crawler.selected_plan_candidate_match(selected, candidate)

        self.assertEqual(match[1], "distinctive-label-tokens")

    def test_selected_plan_price_audit_can_reconfirm_by_amount_and_allowance(self) -> None:
        gym = {
            "id": "partner-plan",
            "monthlyPrice": 490,
            "selectedPlanId": "partner-plan:plan:eight",
            "plans": [{
                "id": "partner-plan:plan:eight", "sourceProductId": "eight", "name": "Partner Membership",
                "classAllowance": {"count": 8, "period": "month", "unlimited": False},
                "billing": {"amount": 490, "normalizedMonthly": 490},
                "evidence": {"url": "https://operator.example/pricing", "rawLabel": "Partner Membership"},
            }],
        }
        observations = [{
            "gymId": "partner-plan", "sourceUrl": "https://operator.example/pricing",
            "rawLabel": "Pricing: 8 sessions/month $490/month", "amount": 490, "cadence": "month",
            "classAllowance": {"count": 8, "period": "month"}, "promotion": {"isPromotion": False},
        }]

        audit = crawler.audit_selected_plan_price(gym, observations)

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["matchMethod"], "amount-and-class-allowance")

    def test_worker_error_isolated_as_review_attempt(self) -> None:
        gym = {"id": "broken", "name": "Broken Gym", "officialUrl": "https://broken.example/"}

        def broken_runner():
            raise KeyError("unexpected source shape")

        attempts, observations, locations, updates = crawler.fail_closed_crawl(
            gym, datetime(2026, 8, 21), broken_runner,
        )

        self.assertEqual(attempts[0]["status"], "worker-error")
        self.assertTrue(attempts[0]["requiresReview"])
        self.assertIn("KeyError", attempts[0]["error"])
        self.assertEqual((observations, locations, updates), ([], [], {}))

    def test_operator_frontier_has_a_separate_bounded_request_budget(self) -> None:
        gym = {
            "id": "bounded",
            "name": "Bounded Gym",
            "websiteUrl": "https://bounded.example/",
            "officialUrl": "https://bounded.example/",
            "monthlyPrice": None,
        }
        links = "".join(f'<a href="/pricing/{index}">Plan {index}</a>' for index in range(20))

        def fake_fetch(url: str, _timeout: float, _cached: object) -> dict[str, object]:
            return {
                "status": "fetched",
                "url": url,
                "robotsStatus": "checked",
                "contentType": "text/html",
                "html": links if url == "https://bounded.example/" else "<p>No public amount.</p>",
            }

        with patch.object(crawler, "fetch_page", side_effect=fake_fetch), patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0):
            attempts, _observations, _locations, _updates = crawler.crawl_gym(
                gym, {}, datetime(2026, 8, 21), 1, defaultdict(threading.Lock), {}, {}, {}, {}, threading.Lock(),
            )

        self.assertEqual(len(attempts), crawler.MAX_OPERATOR_REQUESTS_PER_GYM)
        self.assertEqual(attempts[0]["operatorRequestCount"], crawler.MAX_OPERATOR_REQUESTS_PER_GYM)
        self.assertEqual(attempts[0]["bookingRequestCount"], 0)
        self.assertEqual(attempts[0]["frontierSkipReasons"], {"operator-request-budget": 1})

    def test_concurrent_locations_share_one_physical_request(self) -> None:
        requests: dict[str, concurrent.futures.Future[dict[str, object]]] = {}
        lock = threading.Lock()
        calls = 0
        release = threading.Event()

        def fetcher() -> dict[str, object]:
            nonlocal calls
            calls += 1
            release.wait(timeout=1)
            return {"status": "fetched", "url": "https://operator.example/pricing"}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                crawler.fetch_once_for_run,
                "https://operator.example/pricing#plans",
                requests,
                lock,
                fetcher,
            )
            second = executor.submit(
                crawler.fetch_once_for_run,
                "https://operator.example/pricing#memberships",
                requests,
                lock,
                fetcher,
            )
            release.set()
            results = [first.result(), second.result()]

        self.assertEqual(calls, 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(sum(bool(result.get("sharedResponse")) for result in results), 1)

    def test_same_operator_pricing_documents_are_followed_but_accounts_are_not(self) -> None:
        links = crawler.linked_storefronts(
            "https://operator.example/location/sf",
            ["/pricing", "/memberships/options", "/account/login", "/about", "https://momence.com/operator/memberships"],
        )
        self.assertIn("https://operator.example/pricing", links)
        self.assertIn("https://operator.example/memberships/options", links)
        self.assertIn("https://momence.com/operator/memberships", links)
        self.assertNotIn("https://operator.example/account/login", links)
        self.assertNotIn("https://operator.example/about", links)

    def test_extracts_json_ld_offer_as_review_candidate(self) -> None:
        block = '{"@type":"Product","name":"Basic","offers":{"@type":"Offer","price":"129","priceCurrency":"USD"}}'
        offers = crawler.structured_candidates([block], "https://example.com/pricing")
        self.assertTrue(any(offer["amount"] == 129 for offer in offers))
        self.assertTrue(all(not offer["autoPublishEligible"] for offer in offers))

    def test_extracts_next_hydration_offer_with_plan_metadata(self) -> None:
        html = '<script id="__NEXT_DATA__" type="application/json">{"props":{"plan":{"id":"basic-4","name":"Basic 4 classes per month","price":119,"interval":"month"}}}</script>'
        offers, _stores, _digest = crawler.parse_page({"html": html, "url": "https://example.com/pricing"})
        self.assertEqual(offers[0]["sourceProductId"], "basic-4")
        self.assertEqual(offers[0]["classAllowance"]["count"], 4)
        self.assertEqual(offers[0]["method"], "embedded-hydration-json")

    def test_script_javascript_is_never_treated_as_visible_price_text(self) -> None:
        html = '<script>window.hiddenPromo = "Trial $1 per month";</script><p>Membership details</p>'
        offers, _stores, _digest = crawler.parse_page({"html": html, "url": "https://example.com/pricing"})
        self.assertEqual(offers, [])

    def test_booking_domains_receive_named_adapters(self) -> None:
        self.assertEqual(crawler.platform_name("https://clients.mindbodyonline.com/classic/ws"), "mindbody")
        self.assertEqual(crawler.platform_name("https://vrv3studioscom.onbookee.com/pricing"), "bookee")
        self.assertEqual(crawler.platform_name("https://empirejiujitsuca.sites.zenplanner.com/sign-up-now.cfm"), "zen-planner")
        self.assertEqual(crawler.platform_name("https://joltathleticsappts.as.me/"), "acuity")
        self.assertEqual(crawler.platform_name("https://back-to-sports-fitness-and-therapy.gymdesk.com/pricing"), "gymdesk")
        self.assertEqual(crawler.platform_name("https://onlinejoin.abcfitness.com/signup/plan?club=1"), "abc-fitness")
        self.assertEqual(crawler.platform_name("https://portal.movementgyms.com/san-francisco/memberships/monthly-membership"), "redpoint")
        self.assertEqual(crawler.platform_name("https://benchmark.portal.approach.app/membership-type/3"), "approach")
        self.assertEqual(crawler.platform_name("https://example.com/pricing"), "operator-site")

    def test_abc_fitness_catalog_expands_plan_list_and_plan_linked_fee(self) -> None:
        join_url = "https://onlinejoin.abcfitness.com/signup/plan?club=31627&planId=general"
        self.assertEqual(
            crawler.abc_fitness_storefronts(join_url),
            ["https://onlinejoin.abcfitness.com/api/online-join/signup/planList?clubNumber=31627"],
        )
        plan_list = [
            {"planId": "general-plan-123", "planName": "General Public Membership"},
            {"planId": "senior-plan-123", "planName": "Senior Membership"},
        ]
        offers, nested = crawler.abc_fitness_catalog_candidates(
            plan_list,
            "https://onlinejoin.abcfitness.com/api/online-join/signup/planList?clubNumber=31627",
        )
        self.assertEqual(offers, [])
        self.assertEqual(len(nested), 2)
        summary = {
            "planId": "general-plan-123",
            "planName": "General Public Membership",
            "agreementTerm": "Open",
            "renewalAmount": "$144.00",
            "renewalFrequency": "Monthly",
            "downPayments": [
                {"name": "Enrollment Fee", "total": "$100.00"},
                {"name": "Prorated First Month Dues", "total": "$42.00"},
            ],
        }
        candidates, deeper = crawler.abc_fitness_catalog_candidates(summary, nested[0])
        self.assertEqual(deeper, [])
        self.assertEqual(candidates[0]["amount"], 144)
        self.assertEqual(candidates[0]["commitment"]["type"], "month-to-month")
        self.assertEqual([(fee["type"], fee["amount"]) for fee in candidates[0]["fees"]], [("enrollment", 100)])

    def test_abc_text_plain_json_reconstructs_term_fee_and_reviewed_catalog_provenance(self) -> None:
        catalog_url = "https://livefitgym.com/signup/"
        plan_list_url = "https://onlinejoin.abcfitness.com/api/online-join/signup/planList?clubNumber=32319"
        plan_id = "7301d4e290184005b71868d99fbf9707"
        plan_list = [{"planId": plan_id, "planName": "Premier 6 Month Term"}]

        offers, nested, digest = crawler.parse_page({
            "html": json.dumps(plan_list), "url": plan_list_url, "contentType": "text/plain;charset=UTF-8",
        })

        self.assertEqual(offers, [])
        self.assertEqual(nested, [
            f"https://onlinejoin.abcfitness.com/api/online-join/signup/calculatePlan?planId={plan_id}&clubNumber=32319",
        ])
        self.assertTrue(digest)

        detail = {
            "planId": plan_id,
            "planName": "Premier 6 Month Term",
            "agreementTerm": "Installment",
            "termInMonths": 6,
            "renewalAmount": "$137.00",
            "renewalFrequency": "Monthly",
            "downPayments": [
                {"name": "Enrollment Fee", "total": "$0.00"},
                {"name": "First Month Dues", "total": "$137.00"},
                {"name": "Last Month Dues", "total": "$137.00"},
            ],
            "clubFees": [{
                "feeName": "Annual Fee", "feeAmount": "$49.00", "feeApply": True, "feeRecurring": True,
            }],
        }
        candidates, deeper = crawler.abc_fitness_catalog_candidates(detail, nested[0])
        candidate = candidates[0]

        self.assertEqual(deeper, [])
        self.assertEqual(candidate["sourceProductAliases"], ["premier"])
        self.assertEqual(candidate["commitment"], {
            "type": "fixed-term", "minimumMonths": 6, "rawLabel": "Installment",
        })
        self.assertEqual(
            [(fee["type"], fee["amount"], fee["cadence"]) for fee in candidate["fees"]],
            [("annual", 49, "year")],
        )

        gym = {
            "id": "live-fit", "monthlyPrice": 137, "selectedPlanId": "live-fit:plan:premier",
            "priceSourceUrl": catalog_url,
            "plans": [{
                "id": "live-fit:plan:premier", "sourceProductId": "premier", "name": "Premier",
                "billing": {"amount": 137, "normalizedMonthly": 137},
                "evidence": {"url": catalog_url, "rawLabel": "Premier"},
            }],
        }
        observation = {**candidate, "gymId": "live-fit", "catalogSourceUrl": catalog_url}
        audit = crawler.audit_selected_plan_price(gym, [observation])

        self.assertEqual(audit["status"], "matched-within-threshold")
        self.assertEqual(audit["matchMethod"], "source-product-alias")

        observation["catalogSourceUrl"] = "https://different.example/pricing"
        self.assertEqual(
            crawler.audit_selected_plan_price(gym, [observation])["status"],
            "selected-plan-not-observed",
        )

        self.assertTrue(crawler.may_follow_nested_catalog(plan_list_url, crawler.MAX_LINK_DEPTH))
        self.assertFalse(crawler.may_follow_nested_catalog(plan_list_url, crawler.MAX_LINK_DEPTH + 1))
        self.assertFalse(crawler.may_follow_nested_catalog(catalog_url, crawler.MAX_LINK_DEPTH))
        self.assertTrue(crawler.preferred_accept_header(plan_list_url).startswith("application/json"))
        self.assertTrue(crawler.preferred_accept_header(catalog_url).startswith("text/html"))

    def test_discovers_operator_owned_bookee_iframe(self) -> None:
        html = '<iframe src="https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211"></iframe>'
        _offers, stores, _digest = crawler.parse_page({"html": html, "url": "https://www.vrv3studios.com/schedule"})
        self.assertEqual(stores, ["https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211"])

    def test_reconstructs_bookee_membership_and_drop_in_cards(self) -> None:
        visible = """Monthly Membership (4x Classes, 3M)
$149/month
4 credit
Subscription
Renews in 1 month
This membership requires a 3-month commitment.
Drop-In Class x1
$44
1 credit
Credit pack
1 month"""
        offers = crawler.bookee_visible_candidates(visible, "https://vrv3studioscom.onbookee.com/pricing")
        self.assertEqual([offer["amount"] for offer in offers], [149, 44])
        self.assertEqual(offers[0]["classAllowance"]["count"], 4)
        self.assertEqual(offers[0]["commitment"]["minimumMonths"], 3)
        self.assertEqual(offers[1]["productType"], "drop-in")
        self.assertTrue(all(not offer["autoPublishEligible"] for offer in offers))

    def test_discovers_and_reconstructs_mariana_buy_page(self) -> None:
        html = """<div data-mariana-integrations=\"/buy/48717\"></div><script>var TENANT_NAME = 'thecoremvmt';</script>"""
        _offers, stores, _digest = crawler.parse_page({"html": html, "url": "https://thecoremvmt.com/pricing/"})
        self.assertEqual(stores, ["https://thecoremvmt.marianatek.com/api/customer/v1/locations/48717/buy-page"])
        fixture = json.loads((Path(__file__).parent / "fixtures" / "mariana-buy-page.json").read_text(encoding="utf-8"))
        offers = crawler.mariana_buy_page_candidates(fixture, stores[0])
        self.assertEqual([offer["amount"] for offer in offers], [118, 3333, 20, 38])
        self.assertEqual(offers[0]["classAllowance"]["count"], 4)
        self.assertEqual(offers[0]["commitment"]["type"], "month-to-month")
        self.assertTrue(offers[2]["promotion"]["isPromotion"])
        self.assertEqual(offers[3]["productType"], "drop-in")
        self.assertTrue(all(not offer["autoPublishEligible"] for offer in offers))

    def test_discovers_xponential_catalog_and_plan_linked_fee(self) -> None:
        html = '<div data-endpoint-domain="https://members.clubpilates.com" data-endpoint="/api/v2/locations/clubpilates-soma-ca/schedule_entries"></div>'
        _offers, stores, _digest = crawler.parse_page({"html": html, "url": "https://www.clubpilates.com/location/soma"})
        self.assertEqual(stores, ["https://members.clubpilates.com/api/locations/clubpilates-soma-ca/packages"])
        fixture_root = Path(__file__).parent / "fixtures"
        packages = json.loads((fixture_root / "xponential-packages.json").read_text(encoding="utf-8"))
        offers, nested = crawler.xponential_package_candidates(packages, stores[0])
        self.assertEqual([offer["amount"] for offer in offers], [45, 129, 229, 289])
        self.assertEqual(offers[0]["productType"], "drop-in")
        self.assertEqual(offers[1]["classAllowance"]["count"], 4)
        self.assertEqual(offers[1]["commitment"]["minimumMonths"], 3)
        self.assertEqual(len(nested), 4)
        detail = json.loads((fixture_root / "xponential-package-detail.json").read_text(encoding="utf-8"))
        detail_offers, detail_nested = crawler.xponential_package_candidates(detail, nested[1])
        self.assertEqual(detail_nested, [])
        self.assertEqual(detail_offers[0]["fees"][0]["type"], "enrollment")
        self.assertEqual(detail_offers[0]["fees"][0]["amount"], 149)
        self.assertTrue(all(not offer["autoPublishEligible"] for offer in offers + detail_offers))

    def test_xponential_location_attribute_and_first_timer_flag_are_not_false_promotion(self) -> None:
        html = '<div data-endpoint-domain="https://members.stretchlab.com" data-location="stretchlab-soma"></div>'
        _offers, stores, _digest = crawler.parse_page({"html": html, "url": "https://www.stretchlab.com/location/soma"})
        self.assertEqual(stores, ["https://members.stretchlab.com/api/locations/stretchlab-soma/packages"])
        payload = {"packages": [{
            "id": "single", "name": "Single 25 Minute Session: Non-Member Rate", "credit_count": 1,
            "is_membership": False, "is_recurring": False, "is_first_timer_booking": True,
            "interval": "day", "interval_count": 30, "description": "One 25 Minute Stretch. 30 Day Expiration.",
            "price": {"numeric": 75, "currency_code": "USD"},
        }]}
        offers, _nested = crawler.xponential_package_candidates(payload, stores[0])
        self.assertFalse(offers[0]["promotion"]["isPromotion"])
        self.assertEqual(offers[0]["eligibility"]["type"], "standard-adult")

    def test_extracts_location_identity_hours_and_amenities_for_review(self) -> None:
        block = '{"@type":"ExerciseGym","name":"Example Gym","address":{"@type":"PostalAddress","streetAddress":"1 Market St","addressLocality":"San Francisco","addressRegion":"CA","postalCode":"94105"},"geo":{"latitude":37.79,"longitude":-122.4},"openingHours":["Mo-Fr 06:00-22:00"],"amenityFeature":[{"name":"Showers","value":true}]}'
        values = crawler.structured_location_candidates([block], "https://example.com/location")
        self.assertEqual(values[0]["address"], "1 Market St, San Francisco, CA, 94105")
        self.assertEqual(values[0]["amenities"], ["Showers"])
        self.assertFalse(values[0]["autoPublishEligible"])

    def test_visible_address_is_a_review_candidate_not_an_auto_update(self) -> None:
        values = crawler.visible_location_candidates("Visit us at 123 Market Street, Suite 4, San Francisco, CA 94105 today.", "https://example.com/location")
        self.assertEqual(values[0]["address"], "123 Market Street, Suite 4, San Francisco, CA 94105")
        self.assertFalse(values[0]["autoPublishEligible"])

    def test_extracts_visible_monthly_and_drop_in_candidates(self) -> None:
        value = "Basic membership $99 per month. Standard Drop In is $25."
        offers = crawler.visible_candidates(value, "https://example.com/pricing")
        self.assertEqual({offer["productType"] for offer in offers}, {"monthly", "drop-in"})

    def test_monthly_extractor_uses_amount_nearest_cadence_not_processing_fee(self) -> None:
        offers = crawler.visible_candidates("Fees are 2.89% + $0.30. Core Max is $229/month.", "https://example.com")
        self.assertEqual([offer["amount"] for offer in offers if offer["productType"] == "monthly"], [229])

    def test_crunch_cards_separate_regular_rates_promotions_and_plan_fees(self) -> None:
        visible = """Best Value
All Crunch
Multi-Club Access
$ 127 20 /mo reg: $ 159
Month-to-Month No Commitment
City Crunch
Multi-Club Access
$88/mo reg: $ 110
Month-to-Month No Commitment
One Crunch
Single Club Access
$ 80 75 /mo reg: $ 95
Month-to-Month No Commitment
Enrollment Fee $150 $20 $150 $20 $150 $15 First Month Dues
Processing Fee $49.95 $39.95 $49.95 $39.95 $49.95 $39.95 Subtotal"""
        offers = crawler.visible_candidates(visible, "https://www.crunch.com/locations/chestnut")
        regular = [offer for offer in offers if not offer["promotion"]["isPromotion"]]
        promotional = [offer for offer in offers if offer["promotion"]["isPromotion"]]
        self.assertEqual([offer["amount"] for offer in regular], [159, 110, 95])
        self.assertEqual([offer["amount"] for offer in promotional], [127.2, 88, 80.75])
        self.assertEqual(
            [(fee["type"], fee["amount"]) for fee in regular[0]["fees"]],
            [("enrollment", 150), ("processing", 49.95)],
        )
        self.assertEqual(promotional[2]["fees"][0]["amount"], 15)
        self.assertTrue(regular[0]["bestValueLabel"])
        self.assertTrue(all(offer["method"] == "visible-crunch-plan-card" for offer in offers))
        self.assertTrue(all(not offer["autoPublishEligible"] for offer in offers))

    def test_rendered_platform_dispatch_preserves_mariana_semantics(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures" / "mariana-buy-page.json").read_text(encoding="utf-8"))
        offers, nested = crawler.public_platform_json_candidates(
            fixture, "https://thecoremvmt.marianatek.com/api/customer/v1/locations/48717/buy-page"
        )
        self.assertEqual(nested, [])
        self.assertEqual([offer["amount"] for offer in offers], [118, 3333, 20, 38])
        self.assertEqual(offers[0]["method"], "public-mariana-buy-page-api")

    def test_24_hour_matrix_preserves_variants_and_selectable_silver(self) -> None:
        visible = """Offer on select monthly memberships. $69.99 Annual Fee required.
Monthly
$10 OFF* Platinum as low as 49.99 per month 50.99 Due Today
$10 OFF* Gold as low as 34.99 per month 34.99 Due Today
Silver as low as 29.99 per month 29.99 Due Today
Monthly Commitment
Platinum 56.99 per month $1.00 Due Today
Yearly
Platinum 28.99 per month 347.88 Due Today
National 24.99 per month 299.88 Due Today
Choose the gym membership option for you"""
        offers = crawler.visible_candidates(
            visible, "https://www.24hourfitness.com/gyms/san-francisco-ca/potrero-sport"
        )
        self.assertEqual(len(offers), 6)
        silver = next(item for item in offers if item["sourceProductId"] == "silver-monthly-no-commitment")
        yearly = next(item for item in offers if item["sourceProductId"] == "platinum-yearly-auto-renewal")
        self.assertEqual(silver["amount"], 29.99)
        self.assertFalse(silver["promotion"]["isPromotion"])
        self.assertEqual(silver["fees"][0]["amount"], 69.99)
        self.assertEqual(yearly["amount"], 347.88)
        self.assertEqual(yearly["billingInterval"], "year")
        self.assertTrue(next(item for item in offers if item["sourceProductId"] == "gold-monthly-no-commitment")["promotion"]["isPromotion"])
        self.assertTrue(all(item["method"] == "visible-24-hour-membership-matrix" for item in offers))

    def test_equinox_cards_retain_most_popular_operator_label(self) -> None:
        visible = """Select $297 / mo Access to one Club with immaculate spaces.
All-Access Most Popular $350 / mo Access to 90+ Clubs across North America.
Destination $370 / mo Access to 110+ Clubs globally.
Destination West $410 / mo Everything a Destination Membership offers."""
        offers = crawler.visible_candidates(visible, "https://www.equinox.com/clubs/northern-california/pinest")
        self.assertEqual([item["amount"] for item in offers], [297, 350, 370, 410])
        self.assertTrue(next(item for item in offers if item["sourceProductId"] == "2931")["bestValueLabel"])
        self.assertFalse(next(item for item in offers if item["sourceProductId"] == "15")["bestValueLabel"])
        self.assertTrue(all(item["method"] == "visible-equinox-plan-card" for item in offers))

    def test_planet_fitness_cards_link_each_startup_fee_to_its_plan(self) -> None:
        visible = """PF BLACK CARD® Best Value $24.99 /mo plus taxes & fees.
$1 Startup Fee $49 Annual Fee No Commitment Offer Expires August 30th.
Classic $15 /mo plus taxes & fees. Unlimited access to your home club.
$49 Startup Fee $49 Annual Fee No Commitment Offer Expires August 30th.
CLUB INFO"""
        offers = crawler.visible_candidates(
            visible, "https://www.planetfitness.com/gyms/san-francisco-lakeshore-ca"
        )
        black = next(item for item in offers if item["sourceProductId"] == "pf-black-card")
        classic = next(item for item in offers if item["sourceProductId"] == "classic")
        self.assertEqual([(fee["type"], fee["amount"]) for fee in black["fees"]], [("enrollment", 1), ("annual", 49)])
        self.assertEqual([(fee["type"], fee["amount"]) for fee in classic["fees"]], [("enrollment", 49), ("annual", 49)])
        self.assertTrue(black["bestValueLabel"])
        self.assertEqual(classic["commitment"]["type"], "month-to-month")

    def test_planet_fitness_presale_is_not_an_eligible_ordinary_plan(self) -> None:
        visible = """PF BLACK CARD® $24.99 /mo Pre-Grand Opening Sale Extended!
$0 Startup Fee $49 Annual Fee No Commitment.
SPECIAL DEAL Classic $15 /mo Pre-Grand Opening Sale Extended!
$1 Startup Fee $49 Annual Fee 12 Month Commitment.
CLUB INFO"""
        offers = crawler.visible_candidates(
            visible, "https://www.planetfitness.com/gyms/san-francisco-ca-relocation"
        )
        self.assertTrue(all(item["promotion"]["isPromotion"] for item in offers))
        classic = next(item for item in offers if item["sourceProductId"] == "classic")
        self.assertEqual(classic["commitment"], {"type": "fixed-term", "minimumMonths": 12})
        self.assertEqual(classic["fees"][0]["amount"], 1)

    def test_orangetheory_cards_ignore_first_month_promotion_and_generic_visit_context(self) -> None:
        visible = """Premier $269 / month Unlimited classes. New members first month $209 / month.
Elite $199 / month 8 classes per month. Basic $119 / month 4 classes per month.
Month-to-month with 30-day cancellation. Recommended casual visit is $35 and varies by studio."""
        offers = crawler.visible_candidates(
            visible, "https://www.orangetheory.com/en-us/locations/san-francisco-california-1150"
        )
        self.assertEqual([(item["sourceProductId"], item["amount"]) for item in offers], [
            ("premier", 269), ("elite", 199), ("basic", 119),
        ])
        self.assertTrue(all(item["commitment"]["type"] == "month-to-month" for item in offers))
        self.assertFalse(any(item["amount"] == 35 for item in offers))
        partial_card = crawler.visible_candidates(
            "Elite $199 /mo. Price per class $24.88 8 Classes Monthly",
            "https://www.orangetheory.com/en-us/locations/san-francisco-california-1150",
        )
        self.assertEqual(partial_card, [])

    def test_approach_storefront_recovers_unlimited_membership(self) -> None:
        offers = crawler.visible_candidates(
            "Unlimited Membership $99 recurring monthly. Unlimited access to all Benchmark locations.",
            "https://benchmark.portal.approach.app/membership-type/3",
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual((offers[0]["amount"], offers[0]["productType"]), (99, "monthly"))
        self.assertTrue(offers[0]["classAllowance"]["unlimited"])

    def test_perform_for_golf_retains_named_amount_withheld_memberships(self) -> None:
        visible = """
        Membership Types
        PAR MEMBERSHIP (4 SESSIONS/MONTH)
        BIRDIE MEMBERSHIP (6 SESSIONS/MONTH)
        EAGLE MEMBERSHIP (8 SESSIONS/MONTH)
        ALBATROSS (10 SESSIONS/MONTH)
        ACE MEMBERSHIP (12 SESSIONS/MONTH)
        Membership runs on an auto-monthly basis.
        """

        offers = crawler.visible_candidates(visible, "https://www.performforgolf.com/how-it-works")

        self.assertEqual(len(offers), 5)
        self.assertEqual([offer["classAllowance"]["count"] for offer in offers], [4, 6, 8, 10, 12])
        self.assertTrue(all(offer["amount"] is None for offer in offers))
        self.assertTrue(all(offer["purchaseMethod"] == "contact-required" for offer in offers))
        self.assertTrue(all(offer["kind"] == "plan-descriptor" for offer in offers))

    def test_perform_for_golf_retains_unnamed_public_minimum_tier(self) -> None:
        visible = (
            "We have memberships based on one-on-one sessions. "
            "Clients can from 2x/month and up to 12x/month"
        )

        offers = crawler.visible_candidates(visible, "https://www.performforgolf.com/faq")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["sourceProductId"], "unnamed-2-sessions")
        self.assertEqual(offers[0]["classAllowance"]["count"], 2)
        self.assertIsNone(offers[0]["amount"])

    def test_partial_perform_for_golf_table_fails_closed(self) -> None:
        visible = "PAR MEMBERSHIP (4 SESSIONS/MONTH) BIRDIE MEMBERSHIP (6 SESSIONS/MONTH)"

        self.assertEqual(
            crawler.perform_for_golf_plan_descriptors(
                visible, "https://www.performforgolf.com/how-it-works",
            ),
            [],
        )

    def test_remaining_operator_cards_preserve_terms_fees_and_restrictions(self) -> None:
        federal = crawler.independent_operator_visible_candidates(
            "General Public $47 monthly. Federal Employee $40. All Clubs $43. $40 Initiation Fee. Day Pass $20.",
            "https://www.federalfitnesscenters.com/federal-fitness-center",
        )
        self.assertEqual(next(item for item in federal if item["sourceProductId"] == "general-public-all-clubs")["fees"][0]["amount"], 40)
        self.assertEqual(next(item for item in federal if item["sourceProductId"] == "day-pass")["amount"], 20)

        bernal = crawler.independent_operator_visible_candidates(
            "Annual Membership Monthly Dues $77 Term 12 months $99 individual join fee",
            "https://clubs.healthclubsystems.com/php/ocFN.php?mp=a",
        )
        self.assertEqual((bernal[0]["commitment"]["minimumMonths"], bernal[0]["fees"][0]["amount"]), (12, 99))

        pilates = crawler.independent_operator_visible_candidates(
            "Single Membership $250 10 classes. Shared 2 $225 Shared 3 $199 Shared 4 $189. 3 class intro $50. 8 class pack $280.",
            "https://www.pilatesschoolsf.com/drop-in-pricing/",
        )
        self.assertEqual(next(item for item in pilates if item["sourceProductId"] == "single-membership")["amount"], 250)
        self.assertEqual(next(item for item in pilates if item["sourceProductId"] == "shared-four")["eligibility"]["type"], "shared-membership")

    def test_live_fit_and_pay_per_visit_catalogs_are_reconstructed(self) -> None:
        live_fit = crawler.independent_operator_visible_candidates(
            "Basic $117 Massage + Gym $207 Wellness $357 Premier $137 Massage + Gym $227 Wellness $377",
            "https://livefitgym.com/signup/",
        )
        self.assertEqual(len(live_fit), 6)
        self.assertTrue(all(item["commitment"]["minimumMonths"] == 6 for item in live_fit))

        strong = crawler.independent_operator_visible_candidates(
            "Drop-In any class $60. Semi-private classes start at $43 with membership.",
            "https://www.strongfriendsgym.com/programs/drop-in",
        )
        self.assertEqual([(item["productType"], item["amount"]) for item in strong], [("drop-in", 60)])

        world = crawler.independent_operator_visible_candidates(
            "Day Pass $49 Introduction Lesson $79 30 Day Trial $285 7 Day Trial Online $105 6 Week Bootcamp $399",
            "https://www.worldteamusa.net/services",
        )
        self.assertEqual(next(item for item in world if item["sourceProductId"] == "day-pass")["amount"], 49)
        self.assertTrue(next(item for item in world if item["sourceProductId"] == "thirty-day-trial")["promotion"]["isPromotion"])

    def test_independent_operator_cards_reconstruct_city_and_forge_catalogs(self) -> None:
        city = crawler.independent_operator_visible_candidates(
            "Month To Month $275 Unlimited $250 12x Per Month $35 Drop In 10 Class Pack",
            "https://www.thecitycrossfit.com/memberships",
        )
        self.assertEqual({item["sourceProductId"] for item in city}, {
            "twelve-per-month", "unlimited-monthly", "drop-in", "ten-class-pack", "intro-series",
        })
        self.assertEqual(next(item for item in city if item["sourceProductId"] == "twelve-per-month")["classAllowance"]["count"], 12)
        self.assertEqual(next(item for item in city if item["sourceProductId"] == "drop-in")["amount"], 35)

        forge = crawler.independent_operator_visible_candidates(
            "Monthly Membership $200/month Annual Membership $2,160/year 10 Class Pack $375 Drop-In Day Pass $40",
            "https://www.forgekravmaga.com/pricing",
        )
        annual = next(item for item in forge if item["sourceProductId"] == "annual-membership")
        trial = next(item for item in forge if item["sourceProductId"] == "two-trial-classes")
        self.assertEqual((annual["amount"], annual["cadence"], annual["commitment"]["minimumMonths"]), (2160, "year", 12))
        self.assertTrue(trial["promotion"]["isPromotion"])

    def test_independent_operator_cards_preserve_fees_and_sliding_scale(self) -> None:
        funky = crawler.independent_operator_visible_candidates(
            "Auto Monthly - $129 one time $49 sign up fee 10 Class Pack - $260 24 Class Pack - $495 Drop-In Classes: $36",
            "https://funkydoor.com/prices",
        )
        selected = next(item for item in funky if item["sourceProductId"] == "studio-auto-monthly")
        self.assertEqual(selected["fees"][0], {
            "type": "enrollment", "amount": 49, "currency": "USD", "cadence": "one-time", "mandatory": True,
        })
        self.assertTrue(selected["bestValueLabel"])

        lotus = crawler.independent_operator_visible_candidates(
            "Unlimited Monthly Sliding-Scale Membership $135 - $175 $175 One Month $450 3 Month $122 5 Class $220 10 Class",
            "https://www.lotuslandyogasf.com/pricespolicies/",
        )
        self.assertNotIn("sliding-scale-monthly", {item["sourceProductId"] for item in lotus})
        context = crawler.visible_cost_context_candidates(
            "Unlimited Monthly Sliding-Scale Membership $135 - $175",
            "https://www.lotuslandyogasf.com/pricespolicies/",
        )[0]
        self.assertEqual((context["low"], context["high"]), (135, 175))
        self.assertTrue(crawler.RESEARCH_PATH_RE.search("/pricespolicies/"))

    def test_follows_owned_storefront_but_not_marketplace(self) -> None:
        links = [
            "https://clients.mindbodyonline.com/classic/ws?studioid=1",
            "https://classpass.com/studios/example",
            "/contact",
        ]
        stores = crawler.linked_storefronts("https://example.com/pricing", links)
        self.assertEqual(stores, ["https://clients.mindbodyonline.com/classic/ws?studioid=1"])

    def test_recovers_mindbody_store_from_account_only_healcode_embed(self) -> None:
        html = (
            '<healcode-widget data-type="account-link" data-site-id="116080" '
            'data-mb-site-id="5734215">Login | Register</healcode-widget>'
        )
        self.assertEqual(crawler.mindbody_embedded_storefronts(html), [
            "https://clients.mindbodyonline.com/classic/ws?studioid=5734215&stype=41"
        ])

    def test_mindbody_embed_ignores_healcode_site_id_and_invalid_values(self) -> None:
        html = '<healcode-widget data-site-id="116080" data-mb-site-id="not-a-business-id"></healcode-widget>'
        self.assertEqual(crawler.mindbody_embedded_storefronts(html), [])

    def test_mindbody_deep_link_recovers_public_services_route(self) -> None:
        source = (
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?"
            "studioid=597858&stype=42&pMode=2&tg=99&utm_source=operator"
        )
        self.assertEqual(
            crawler.mindbody_public_services_route(source),
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&pMode=1",
        )
        self.assertEqual(crawler.mindbody_public_services_route("https://example.com/?studioid=597858"), "")
        self.assertEqual(crawler.mindbody_public_services_route("https://clients.mindbodyonline.com/store"), "")

    def test_mindbody_service_categories_are_safe_prioritized_and_bounded(self) -> None:
        source = "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&pMode=1"
        html = """
            <select id="optTG" name="optTG">
              <option value="0">Select item</option>
              <option value="8">Teacher Workshops</option>
              <option value="67">In Studio Memberships</option>
              <option value="41">NEW Memberships</option>
              <option value="17">Class Packages</option>
              <option value="99">Gift Cards</option>
              <option value="abc">Private Training</option>
            </select>
        """
        routes = crawler.mindbody_service_category_routes(source, html)
        self.assertEqual(routes, [
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&tg=67&pMode=1",
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&tg=41&pMode=1",
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&tg=17&pMode=1",
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&tg=8&pMode=1",
        ])

    def test_mindbody_route_discovery_precedes_unrelated_storefront_links(self) -> None:
        result = {
            "url": "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&pMode=2",
            "contentType": "text/html",
            "html": '<select name="optTG"><option value="67">In Studio Memberships</option></select>',
        }
        _offers, stores, _digest = crawler.parse_page(result)
        self.assertEqual(stores[:2], [
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&pMode=1",
            "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&stype=41&tg=67&pMode=1",
        ])

    def test_mindbody_blank_session_reset_shell_is_access_blocked(self) -> None:
        url = "https://clients.mindbodyonline.com/ASP/main_shop.asp?studioid=597858&pMode=1"
        self.assertEqual(
            crawler.static_access_blocker(
                url,
                '<script src="mb.sessionhelpers.js"></script><script>mb.sessionHelpers.resetSession();</script>',
            ),
            "identity-session-reset-required",
        )
        self.assertEqual(crawler.static_access_blocker("https://operator.example/pricing", "resetSession();"), "")

    def test_vendor_marketing_homepage_is_not_treated_as_storefront(self) -> None:
        self.assertEqual(crawler.linked_storefronts("https://example.com", ["https://www.pushpress.com/"]), [])

    def test_rejects_non_http_urls(self) -> None:
        self.assertFalse(crawler.is_public_http_url("javascript:alert(1)"))
        self.assertFalse(crawler.is_public_http_url("file:///tmp/page"))

    def test_deals_exclude_suppressed_closed_and_restricted_locations(self) -> None:
        base = {
            "publicationStatus": "publish", "recordStatus": "open", "entityKind": "gym",
            "accessModel": "membership", "accessAvailability": None,
        }
        self.assertTrue(crawler.deal_eligible_gym(base))
        self.assertFalse(crawler.deal_eligible_gym({**base, "publicationStatus": "suppress-alias"}))
        self.assertFalse(crawler.deal_eligible_gym({**base, "recordStatus": "coming_soon"}))
        self.assertFalse(crawler.deal_eligible_gym({**base, "accessModel": "restricted"}))
        self.assertFalse(crawler.deal_eligible_gym({**base, "accessAvailability": "waitlist"}))

        observations = [
            {"gymId": "open", "gymName": "Open", "amount": 49, "currency": "USD", "productType": "monthly", "cadence": "month", "rawLabel": "First month special $49/month", "sourceUrl": "https://open.example", "capturedAt": "2026-08-19", "promotion": {"isPromotion": True, "label": "First month special"}},
            {"gymId": "closed", "gymName": "Closed", "amount": 49, "currency": "USD", "productType": "monthly", "cadence": "month", "rawLabel": "First month special $49/month", "sourceUrl": "https://closed.example", "capturedAt": "2026-08-19", "promotion": {"isPromotion": True, "label": "First month special"}},
        ]
        deals = crawler.deal_candidates(observations, {"open"})
        self.assertEqual([deal["gymId"] for deal in deals], ["open"])

    def test_visible_deal_amount_uses_nearest_promotion_not_ordinary_plan_or_fee(self) -> None:
        bar = crawler.explicit_visible_promotion_candidates(
            "First month only $99. Some restrictions apply. $185/Month thereafter."
        )
        self.assertEqual([item["amount"] for item in bar], [99])
        park = crawler.explicit_visible_promotion_candidates(
            "SPECIAL $199 Intro Month. First month for just $199. Purchase Drop In Credit $35."
        )
        self.assertEqual({item["amount"] for item in park}, {199})
        fee = crawler.explicit_visible_promotion_candidates(
            "First month dues waived. Initiation fee waived. $69.99 Annual Fee is in addition to monthly dues."
        )
        self.assertEqual(fee, [])

    def test_weekly_mode_still_polls_current_deal_pages_daily(self) -> None:
        gym = {
            "websiteUrl": "https://gym.example/pricing", "publicationStatus": "publish",
            "recordStatus": "open", "entityKind": "gym", "accessModel": "membership",
            "accessAvailability": None, "monthlyPrice": 100, "priceObservedAt": "2026-08-19",
        }
        cache = {"https://gym.example/pricing": {"status": "fetched", "lastAttemptAt": "2026-08-18"}}
        self.assertTrue(crawler.should_crawl(gym, cache, "weekly", datetime.fromisoformat("2026-08-19T23:00:00")))


if __name__ == "__main__":
    unittest.main()
