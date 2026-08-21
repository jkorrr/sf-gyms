from __future__ import annotations

import unittest
import json
from datetime import datetime
from pathlib import Path

import crawl_official_sources as crawler


class OfficialCrawlerTests(unittest.TestCase):
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
        self.assertEqual(crawler.platform_name("https://example.com/pricing"), "operator-site")

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

    def test_follows_owned_storefront_but_not_marketplace(self) -> None:
        links = [
            "https://clients.mindbodyonline.com/classic/ws?studioid=1",
            "https://classpass.com/studios/example",
            "/contact",
        ]
        stores = crawler.linked_storefronts("https://example.com/pricing", links)
        self.assertEqual(stores, ["https://clients.mindbodyonline.com/classic/ws?studioid=1"])

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
