from __future__ import annotations

import json
import unittest
from pathlib import Path

import platform_adapters as adapters

FIXTURE = Path(__file__).parent / "fixtures" / "platform-catalogs.json"
RENDERED_FIXTURE = Path(__file__).parent / "fixtures" / "rendered-platform-cards.json"


class PlatformAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.rendered_fixtures = json.loads(RENDERED_FIXTURE.read_text(encoding="utf-8"))

    def test_all_supported_platform_fixtures_reconstruct_a_product(self) -> None:
        for name, fixture in self.fixtures.items():
            with self.subTest(name=name):
                candidates = adapters.extract_candidates(fixture["payload"], fixture["url"])
                self.assertGreaterEqual(len(candidates), 1)
                self.assertGreater(candidates[0]["amount"], 0)
                self.assertEqual(candidates[0]["evidenceTier"], "official-public")
                self.assertFalse(candidates[0]["autoPublishEligible"])

    def test_generic_platform_json_excludes_gifts_and_marks_nonstandard_services(self) -> None:
        payload = {"products": [
            {"id": "gift-1", "name": "10-Class Pack Email Gift Card", "price": 320},
            {"id": "private-1", "name": "Private Session", "price": 150, "creditCount": 1},
            {"id": "charity-1", "name": "Charity Class", "price": 30, "creditCount": 1},
            {"id": "pack-1", "name": "10-Class Pack", "price": 300, "creditCount": 10},
        ]}
        candidates = adapters.extract_candidates(payload, "https://tenant.marianatek.com/api/catalog")
        self.assertEqual({item["sourceProductId"] for item in candidates}, {"private-1", "charity-1", "pack-1"})
        by_id = {item["sourceProductId"]: item for item in candidates}
        self.assertEqual(by_id["private-1"]["eligibility"]["type"], "trainer-required")
        self.assertFalse(by_id["private-1"]["ordinaryUse"])
        self.assertEqual(by_id["charity-1"]["eligibility"]["type"], "special-class")
        self.assertFalse(by_id["charity-1"]["ordinaryUse"])
        self.assertEqual(by_id["pack-1"]["productType"], "class-pack")

    def test_bookee_cards_reconstruct_catalog_semantics_and_stable_aliases(self) -> None:
        monthly_fixture = self.rendered_fixtures["bookeeMonthlyMembership"]
        drop_in_fixture = self.rendered_fixtures["bookeeDropIn"]
        monthly = adapters.bookee_product_card_candidates(
            monthly_fixture["cardText"], monthly_fixture["productName"],
            monthly_fixture["displayedPrice"], monthly_fixture["url"],
            monthly_fixture["serviceGroupId"], monthly_fixture["locationLabel"],
            monthly_fixture["sectionLabel"],
        )[0]
        drop_in = adapters.bookee_product_card_candidates(
            drop_in_fixture["cardText"], drop_in_fixture["productName"],
            drop_in_fixture["displayedPrice"], drop_in_fixture["url"],
            drop_in_fixture["serviceGroupId"], drop_in_fixture["locationLabel"],
            drop_in_fixture["sectionLabel"],
        )[0]

        self.assertEqual((monthly["amount"], monthly["productType"], monthly["cadence"]), (149, "monthly", "month"))
        self.assertEqual(monthly["classAllowance"], {"count": 4, "period": "month", "unlimited": False})
        self.assertEqual(monthly["commitment"]["minimumMonths"], 3)
        self.assertIn("four-monthly-3m", monthly["sourceProductAliases"])
        self.assertTrue(monthly["ordinaryUse"])
        self.assertEqual(monthly["exactLocationMatch"], "exact-location")
        self.assertEqual((drop_in["amount"], drop_in["productType"], drop_in["cadence"]), (44, "drop-in", "visit"))
        self.assertIn("class-drop-in", drop_in["sourceProductAliases"])
        self.assertTrue(drop_in["ordinaryUse"])

    def test_bookee_cards_keep_promotions_and_practice_only_memberships_ineligible(self) -> None:
        intro_fixture = self.rendered_fixtures["bookeeIntroOffer"]
        practice_fixture = self.rendered_fixtures["bookeePracticeMembership"]
        intro = adapters.bookee_product_card_candidates(
            intro_fixture["cardText"], intro_fixture["productName"], intro_fixture["displayedPrice"],
            intro_fixture["url"], intro_fixture["serviceGroupId"], intro_fixture["locationLabel"],
            intro_fixture["sectionLabel"],
        )[0]
        practice = adapters.bookee_product_card_candidates(
            practice_fixture["cardText"], practice_fixture["productName"], practice_fixture["displayedPrice"],
            practice_fixture["url"], practice_fixture["serviceGroupId"], practice_fixture["locationLabel"],
            practice_fixture["sectionLabel"],
        )[0]

        self.assertTrue(intro["promotion"]["isPromotion"])
        self.assertIn("intro-four", intro["sourceProductAliases"])
        self.assertFalse(intro["ordinaryUse"])
        self.assertEqual(practice["eligibility"]["type"], "practice-only")
        self.assertEqual(practice["commitment"]["minimumMonths"], 6)
        self.assertIn("open-studio-eight-monthly", practice["sourceProductAliases"])
        self.assertFalse(practice["ordinaryUse"])

    def test_bookee_open_studio_single_is_a_restricted_drop_in(self) -> None:
        candidate = adapters.bookee_product_card_candidates(
            "OPEN STUDIO X1\n$\n18\n1 credit\nCredit pack\nSan Francisco",
            "OPEN STUDIO X1", "$18",
            "https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211",
            "5086", "San Francisco", "Open Studio",
        )[0]

        self.assertEqual((candidate["productType"], candidate["cadence"]), ("drop-in", "visit"))
        self.assertEqual(candidate["eligibility"]["type"], "practice-only")
        self.assertIn("open-studio-drop-in", candidate["sourceProductAliases"])
        self.assertFalse(candidate["ordinaryUse"])

    def test_bookee_class_pack_has_review_stable_semantic_alias(self) -> None:
        candidate = adapters.bookee_product_card_candidates(
            "Drop-In Class x5\n$\n205\n5 credit\nCredit pack\n3 months\nSan Francisco",
            "Drop-In Class x5", "$205",
            "https://vrv3studioscom.onbookee.com/pricing/r/1154/loc/1211",
            "5103", "San Francisco", "Drop-In Class Packs",
        )[0]

        self.assertEqual(candidate["productType"], "class-pack")
        self.assertIn("five-class-pack", candidate["sourceProductAliases"])

    def test_bookee_cards_fail_closed_without_bounded_identity_or_single_price(self) -> None:
        fixture = self.rendered_fixtures["bookeeMonthlyMembership"]
        self.assertEqual(
            adapters.bookee_product_card_candidates(
                fixture["cardText"], fixture["productName"], fixture["displayedPrice"],
                fixture["url"], "", fixture["locationLabel"], fixture["sectionLabel"],
            ),
            [],
        )
        self.assertEqual(
            adapters.bookee_product_card_candidates(
                fixture["cardText"], fixture["productName"], "$149/month $30 add-on",
                fixture["url"], fixture["serviceGroupId"], fixture["locationLabel"], fixture["sectionLabel"],
            ),
            [],
        )

    def test_cents_prices_and_four_week_cadence_are_preserved(self) -> None:
        fixture = self.fixtures["momence"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertEqual(candidate["amount"], 119)
        self.assertEqual(candidate["cadence"], "4 weeks")
        self.assertEqual(candidate["classAllowance"]["count"], 4)

    def test_plan_linked_fee_does_not_become_a_second_plan(self) -> None:
        fixture = self.fixtures["mindbody"]
        candidates = adapters.extract_candidates(fixture["payload"], fixture["url"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["fees"][0]["type"], "enrollment")
        self.assertEqual(candidates[0]["fees"][0]["amount"], 25)

    def test_promotions_are_retained_but_marked_ineligible(self) -> None:
        fixture = self.fixtures["promotion"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertTrue(candidate["promotion"]["isPromotion"])
        self.assertEqual(candidate["eligibility"]["type"], "new-client")

    def test_operator_labeled_best_value_is_preserved(self) -> None:
        fixture = self.fixtures["mariana-tek"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertTrue(candidate["bestValueLabel"])

    def test_unknown_hosts_are_not_scraped_as_platform_catalogs(self) -> None:
        self.assertEqual(adapters.extract_candidates({"name": "Plan", "price": 99}, "https://tracker.invalid/api"), [])

    def test_location_metadata_and_checkout_totals_are_not_products(self) -> None:
        payload = {
            "studio": {"id": "loc-1", "name": "Lotusland Yoga SF", "total": 99},
            "checkout": {"name": "Order total", "amount": 99},
        }
        self.assertEqual(adapters.extract_candidates(payload, "https://momence.com/api/store"), [])

    def test_new_student_language_is_marked_promotional(self) -> None:
        candidates = adapters.extract_candidates(
            {"products": [{"productId": "welcome", "name": "New Student Special", "price": 49, "billingPeriod": "month"}]},
            "https://momence.com/api/memberships",
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["promotion"]["isPromotion"])
        self.assertEqual(candidates[0]["eligibility"]["type"], "new-client")

    def test_bookee_unit_amount_is_interpreted_as_cents(self) -> None:
        fixture = self.fixtures["bookee"]
        candidate = adapters.extract_candidates(fixture["payload"], fixture["url"])[0]
        self.assertEqual(candidate["amount"], 129)

    def test_acuity_business_catalog_keeps_subscriptions_and_class_restrictions(self) -> None:
        payload = {
            "ownerKey": "public-owner",
            "name": "Public Fitness Studio",
            "currencyAbbreviation": "USD",
            "products": {"Memberships": [{
                "id": 10,
                "title": "Unlimited Fitness Membership",
                "description": "Unlimited fitness classes and open gym.",
                "price": 250,
                "isSubscription": True,
                "subscriptionTermsText": "$250.00 per month",
            }]},
            "appointmentTypes": {"General Fitness": [{
                "id": 20,
                "name": "Adult Strength Class",
                "active": True,
                "price": "50.00",
                "type": "class",
                "private": False,
                "classSize": 10,
            }, {
                "id": 21,
                "name": "Youth Strength Class",
                "active": True,
                "price": "45.00",
                "type": "class",
                "private": False,
                "classSize": 10,
            }]},
        }

        candidates = adapters.acuity_business_candidates(payload, "https://studio.as.me/schedule/public-owner")

        membership = next(item for item in candidates if item["sourceProductId"] == "10")
        adult = next(item for item in candidates if item["sourceProductId"] == "20")
        youth = next(item for item in candidates if item["sourceProductId"] == "21")
        self.assertEqual((membership["amount"], membership["cadence"], membership["productType"]), (250, "month", "monthly"))
        self.assertTrue(membership["classAllowance"]["unlimited"])
        self.assertEqual((adult["productType"], adult["ordinaryUse"]), ("drop-in", True))
        self.assertEqual(youth["eligibility"], {"type": "youth", "restrictions": ["Youth product"]})
        self.assertFalse(youth["ordinaryUse"])
        self.assertTrue(all(item["method"] == "public-acuity-embedded-business" for item in candidates))
        self.assertEqual(adapters.acuity_business_candidates(payload, "https://example.com/schedule"), [])

    def test_jane_personal_training_card_is_exact_but_trainer_required(self) -> None:
        fixture = self.rendered_fixtures["janePersonalTraining"]
        candidates = adapters.jane_service_card_candidates(
            fixture["cardText"], fixture["url"], fixture["href"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "discipline-2-treatment-3")
        self.assertEqual(candidates[0]["amount"], 250)
        self.assertEqual(candidates[0]["eligibility"]["type"], "trainer-required")
        self.assertFalse(candidates[0]["autoPublishEligible"])

    def test_jane_clinical_service_is_excluded_from_fitness_catalog(self) -> None:
        fixture = self.rendered_fixtures["janeClinicalExclusion"]
        candidates = adapters.jane_service_card_candidates(
            fixture["cardText"], fixture["url"], fixture["href"],
        )
        self.assertEqual(candidates, [])

    def test_mindbody_product_row_keeps_price_attached_and_marks_special(self) -> None:
        fixture = self.rendered_fixtures["mindbodyPromotionalMembership"]
        candidates = adapters.mindbody_purchase_item_candidates(
            fixture["categoryLabel"], fixture["cardText"], fixture["url"], fixture["sourceProductId"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "11259")
        self.assertEqual(candidates[0]["amount"], 699)
        self.assertEqual(candidates[0]["cadence"], "one-time")
        self.assertEqual(candidates[0]["productType"], "offer")
        self.assertEqual(candidates[0]["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertTrue(candidates[0]["promotion"]["isPromotion"])
        self.assertEqual(candidates[0]["method"], "rendered-mindbody-purchase-item")

    def test_mindbody_contract_uses_recurring_charge_not_total_or_zero_pass(self) -> None:
        fixture = self.rendered_fixtures["mindbodyRecurringContract"]
        candidates = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"], fixture["sourceProductId"],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["sourceProductId"], "179")
        self.assertEqual(candidates[0]["amount"], 300)
        self.assertEqual(candidates[0]["cadence"], "month")
        self.assertEqual(candidates[0]["commitment"]["type"], "month-to-month")
        self.assertEqual(candidates[0]["fees"], [])
        self.assertFalse(candidates[0]["autoPublishEligible"])

    def test_mindbody_yoga_flow_contract_recovers_allowance_scope_and_stable_aliases(self) -> None:
        fixture = self.rendered_fixtures["mindbodyYogaFlowContract"]
        candidate = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )[0]

        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("104", 100))
        self.assertEqual(candidate["classAllowance"], {
            "count": 4, "period": "month", "unlimited": False,
        })
        self.assertEqual(candidate["scopeType"], "single-location")
        self.assertIn("membership-4", candidate["sourceProductAliases"])
        self.assertIn("four-monthly", candidate["sourceProductAliases"])
        self.assertFalse(candidate["promotion"]["isPromotion"])
        self.assertTrue(candidate["ordinaryUse"])
        self.assertEqual(candidate["exactLocationMatch"], "exact-location")

    def test_mindbody_first_month_offer_does_not_contaminate_ongoing_plan(self) -> None:
        fixture = self.rendered_fixtures["mindbodyYogaFlowUnlimitedIntro"]
        candidates = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )

        self.assertEqual(len(candidates), 2)
        ongoing, intro = candidates
        self.assertEqual((ongoing["amount"], ongoing["scopeType"]), (210, "multi-location"))
        self.assertFalse(ongoing["promotion"]["isPromotion"])
        self.assertTrue(ongoing["ordinaryUse"])
        self.assertEqual((intro["amount"], intro["sourceProductId"]), (99, "103-first-month"))
        self.assertTrue(intro["promotion"]["isPromotion"])
        self.assertEqual(intro["eligibility"]["type"], "new-client")
        self.assertFalse(intro["ordinaryUse"])

    def test_mindbody_class_rows_distinguish_drop_in_from_pack(self) -> None:
        drop_fixture = self.rendered_fixtures["mindbodyYogaFlowDropInRow"]
        pack_fixture = self.rendered_fixtures["mindbodyYogaFlowPackRow"]
        drop_in = adapters.mindbody_purchase_item_candidates(
            drop_fixture["categoryLabel"], drop_fixture["cardText"], drop_fixture["url"],
            drop_fixture["sourceProductId"], drop_fixture["locationLabel"],
        )[0]
        pack = adapters.mindbody_purchase_item_candidates(
            pack_fixture["categoryLabel"], pack_fixture["cardText"], pack_fixture["url"],
            pack_fixture["sourceProductId"], pack_fixture["locationLabel"],
        )[0]

        self.assertEqual((drop_in["productType"], drop_in["cadence"]), ("drop-in", "visit"))
        self.assertEqual(drop_in["classAllowance"], {
            "count": 1.0, "period": "visit", "unlimited": False,
        })
        self.assertIn("noe-drop-in", drop_in["sourceProductAliases"])
        self.assertTrue(drop_in["ordinaryUse"])
        self.assertEqual(pack["productType"], "drop-in")
        self.assertIn("noe-five-pack", pack["sourceProductAliases"])
        self.assertFalse(pack["ordinaryUse"])

    def test_mindbody_annual_renewal_term_parses_plural_months(self) -> None:
        fixture = self.rendered_fixtures["mindbodyBijaAnnualRenewal"]
        candidate = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )[0]

        self.assertEqual((candidate["amount"], candidate["classAllowance"]["count"]), (129, 4))
        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 12})
        self.assertEqual(candidate["eligibility"]["type"], "standard-adult")

    def test_mindbody_explicit_minimum_beats_platform_billing_horizon(self) -> None:
        fixture = self.rendered_fixtures["mindbodyHotYogaMinimumTerm"]
        candidate = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )[0]

        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertIn("monthly-autopay", candidate["sourceProductAliases"])

    def test_mindbody_explicit_month_to_month_beats_renewal_cycle(self) -> None:
        fixture = self.rendered_fixtures["mindbodyBayCasualMonthToMonth"]
        candidate = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )[0]

        self.assertEqual(candidate["commitment"], {"type": "month-to-month", "minimumMonths": None})
        self.assertEqual(candidate["classAllowance"]["count"], 5)
        self.assertEqual(candidate["scopeType"], "multi-location")
        self.assertTrue(candidate["ordinaryUse"])

    def test_mindbody_couples_plan_is_not_standard_adult(self) -> None:
        fixture = self.rendered_fixtures["mindbodyBijaCouplesPlan"]
        candidate = adapters.mindbody_contract_candidates(
            fixture["contractLabel"], fixture["contractText"], fixture["url"],
            fixture["sourceProductId"], fixture["locationLabel"],
        )[0]

        self.assertEqual(candidate["eligibility"]["type"], "couples-only")
        self.assertFalse(candidate["ordinaryUse"])

    def test_momence_membership_uses_base_price_not_card_checkout_total(self) -> None:
        fixture = self.rendered_fixtures["momenceMembership"]

        candidates = adapters.momence_membership_card_candidates(
            fixture["visibleText"], fixture["url"], fixture["pageTitle"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["name"]), ("776365", "The Work"))
        self.assertEqual((candidate["amount"], candidate["cadence"]), (375, "month"))
        self.assertEqual(candidate["classAllowance"], {"count": 12.0, "period": "month", "unlimited": False})
        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertEqual(candidate["fees"], [])
        self.assertNotIn("390.37", candidate["rawLabel"])
        self.assertNotIn("15.37", candidate["rawLabel"])
        self.assertFalse(candidate["autoPublishEligible"])

    def test_momence_public_api_preserves_allowance_term_and_no_inferred_fee(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "momence-membership.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        source = "https://momence.com/_api/primary/plugin/memberships/776365"

        candidates = adapters.momence_membership_api_candidates(payload, source)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["name"]), ("776365", "The Work"))
        self.assertEqual((candidate["amount"], candidate["cadence"]), (375, "month"))
        self.assertEqual(candidate["classAllowance"], {"count": 12.0, "period": "month", "unlimited": False})
        self.assertEqual(candidate["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertEqual(candidate["fees"], [])
        self.assertEqual(candidate["method"], "public-momence-membership-api")
        self.assertFalse(candidate["autoPublishEligible"])

    def test_pushpress_modal_recovers_product_cadence_allowance_and_minimum_cycles(self) -> None:
        fixture = self.rendered_fixtures["pushpressRecurring"]

        candidates = adapters.pushpress_plan_detail_candidates(
            fixture["cardText"], fixture["detailText"], fixture["url"], fixture["detailHref"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["sourceProductId"], "plan_50247613f3f44f")
        self.assertEqual((candidate["amount"], candidate["cadence"]), (289, "4 weeks"))
        self.assertEqual(candidate["classAllowance"], {
            "count": None, "period": "4 weeks", "unlimited": True,
        })
        self.assertEqual(candidate["commitment"], {
            "type": "minimum-term",
            "minimumMonths": None,
            "minimumDays": 56,
            "rawLabel": "minimum 2 cycle",
        })
        self.assertEqual(candidate["fees"], [])
        self.assertNotIn("2.9", candidate["rawLabel"])
        self.assertFalse(candidate["autoPublishEligible"])

    def test_pushpress_drop_in_remains_one_visit_not_monthly(self) -> None:
        fixture = self.rendered_fixtures["pushpressDropIn"]

        candidates = adapters.pushpress_plan_detail_candidates(
            fixture["cardText"], fixture["detailText"], fixture["url"], fixture["detailHref"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["amount"], candidate["cadence"], candidate["productType"]), (32.5, "visit", "drop-in"))
        self.assertEqual(candidate["classAllowance"], {
            "count": 1.0, "period": "visit", "unlimited": False,
        })
        self.assertEqual(candidate["commitment"]["type"], "none")

    def test_mariana_tek_card_uses_dedicated_price_not_per_class_math(self) -> None:
        fixture = self.rendered_fixtures["marianaTekMonthly"]
        candidates = adapters.mariana_tek_product_card_candidates(
            fixture["cardText"], fixture["productName"], fixture["displayedPrice"],
            fixture["url"], fixture["sourceProductId"], fixture["locationLabel"],
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["sourceProductId"], "memberships-14787")
        self.assertEqual(candidate["amount"], 118)
        self.assertEqual(candidate["cadence"], "month")
        self.assertEqual(candidate["classAllowance"]["count"], 4)
        self.assertFalse(candidate["promotion"]["isPromotion"])

    def test_mariana_tek_intro_and_gift_cards_do_not_become_ordinary_plans(self) -> None:
        fixture = self.rendered_fixtures["marianaTekIntro"]
        intro = adapters.mariana_tek_product_card_candidates(
            fixture["cardText"], fixture["productName"], fixture["displayedPrice"],
            fixture["url"], fixture["sourceProductId"], fixture["locationLabel"],
        )[0]
        self.assertEqual(intro["productType"], "drop-in")
        self.assertTrue(intro["promotion"]["isPromotion"])
        self.assertEqual(
            adapters.mariana_tek_product_card_candidates(
                "Drop-In Single Class Email Gift Card\n$38\n.00",
                "Drop-In Single Class Email Gift Card",
                "$38\n.00",
                "https://tenant.marianaiframes.com/iframe/buy/48717",
                "credits-14779",
                "Castro",
            ),
            [],
        )
        pack_fixture = self.rendered_fixtures["marianaTekClassPack"]
        pack = adapters.mariana_tek_product_card_candidates(
            pack_fixture["cardText"], pack_fixture["productName"], pack_fixture["displayedPrice"],
            pack_fixture["url"], pack_fixture["sourceProductId"], pack_fixture["locationLabel"],
        )[0]
        self.assertFalse(pack["promotion"]["isPromotion"])

    def test_wix_purchase_card_recovers_stable_product_and_monthly_terms(self) -> None:
        fixture = self.rendered_fixtures["wixMonthlyMembership"]

        candidates = adapters.wix_purchase_card_candidates(
            fixture["cardText"], fixture["url"], fixture["purchaseHref"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("113", 125))
        self.assertIn("individual-monthly", candidate["sourceProductAliases"])
        self.assertEqual((candidate["cadence"], candidate["productType"]), ("month", "monthly"))
        self.assertEqual(candidate["commitment"]["type"], "month-to-month")
        self.assertEqual(candidate["classAllowance"], {
            "count": None, "period": "month", "unlimited": True,
        })
        self.assertEqual(candidate["scopeType"], "multi-location")
        self.assertFalse(candidate["promotion"]["isPromotion"])

    def test_wix_purchase_card_keeps_day_pass_and_annual_prepaid_distinct(self) -> None:
        day = self.rendered_fixtures["wixDayPass"]
        annual = self.rendered_fixtures["wixAnnualMembership"]

        day_candidate = adapters.wix_purchase_card_candidates(
            day["cardText"], day["url"], day["purchaseHref"],
        )[0]
        annual_candidate = adapters.wix_purchase_card_candidates(
            annual["cardText"], annual["url"], annual["purchaseHref"],
        )[0]

        self.assertEqual((day_candidate["amount"], day_candidate["cadence"], day_candidate["productType"]), (30, "visit", "drop-in"))
        self.assertEqual(day_candidate["commitment"]["type"], "none")
        self.assertEqual((annual_candidate["amount"], annual_candidate["cadence"]), (1250, "year"))
        self.assertEqual(annual_candidate["commitment"], {
            "type": "fixed-term", "minimumMonths": 12, "minimumDays": None,
            "rawLabel": "Annual membership",
        })
        self.assertFalse(annual_candidate["promotion"]["isPromotion"])

    def test_squarespace_fluid_card_links_selected_plan_and_fee(self) -> None:
        fixture = self.rendered_fixtures["squarespaceMonthToMonth"]

        candidates = adapters.squarespace_fluid_card_candidates(
            fixture["cardText"], fixture["url"], fixture["purchaseHref"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("100", 220))
        self.assertIn("month-to-month-four-week", candidate["sourceProductAliases"])
        self.assertEqual((candidate["cadence"], candidate["productType"]), ("4 weeks", "monthly"))
        self.assertEqual(candidate["commitment"]["type"], "month-to-month")
        self.assertEqual(candidate["classAllowance"], {
            "count": None, "period": "4 weeks", "unlimited": True,
        })
        self.assertEqual(candidate["fees"], [{
            "type": "enrollment", "amount": 75, "currency": "USD",
            "cadence": "one-time", "mandatory": True,
        }])
        self.assertFalse(candidate["promotion"]["isPromotion"])

    def test_squarespace_fluid_card_preserves_commitment_and_prepaid_amount(self) -> None:
        contract = self.rendered_fixtures["squarespaceSixPayments"]
        annual = self.rendered_fixtures["squarespaceAnnualPrepaid"]

        contract_candidate = adapters.squarespace_fluid_card_candidates(
            contract["cardText"], contract["url"], contract["purchaseHref"],
        )[0]
        annual_candidate = adapters.squarespace_fluid_card_candidates(
            annual["cardText"], annual["url"], annual["purchaseHref"],
        )[0]

        self.assertEqual((contract_candidate["amount"], contract_candidate["cadence"]), (200, "4 weeks"))
        self.assertEqual(contract_candidate["commitment"]["minimumDays"], 168)
        self.assertNotIn("month-to-month-four-week", contract_candidate["sourceProductAliases"])
        self.assertFalse(contract_candidate["promotion"]["isPromotion"])
        self.assertEqual((annual_candidate["amount"], annual_candidate["cadence"]), (2340, "year"))
        self.assertEqual(annual_candidate["commitment"]["minimumMonths"], 12)
        self.assertEqual(annual_candidate["fees"], [])
        self.assertTrue(annual_candidate["bestValueLabel"])

    def test_squarespace_fluid_card_ignores_per_class_arithmetic_and_reads_fragment_id(self) -> None:
        fixture = self.rendered_fixtures["squarespaceInlineMembership"]

        candidates = adapters.squarespace_fluid_card_candidates(
            fixture["cardText"], fixture["url"], fixture["purchaseHref"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("7443", 460))
        self.assertEqual((candidate["cadence"], candidate["productType"]), ("4 weeks", "monthly"))
        self.assertEqual(candidate["classAllowance"], {
            "count": 8, "period": "4 weeks", "unlimited": False,
        })
        self.assertIn("semi-private-eight-four-week", candidate["sourceProductAliases"])

    def test_linked_purchase_card_uses_stable_product_and_ignores_attached_savings(self) -> None:
        fixture = self.rendered_fixtures["linkedMonthlyMembership"]

        candidates = adapters.linked_purchase_card_candidates(
            fixture["cardText"], fixture["url"], fixture["purchaseHref"],
            fixture["linkLabel"], fixture["sectionLabel"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("195", 109))
        self.assertIn("four-monthly", candidate["sourceProductAliases"])
        self.assertEqual((candidate["cadence"], candidate["productType"]), ("month", "monthly"))
        self.assertEqual(candidate["classAllowance"], {
            "count": 4, "period": "month", "unlimited": False,
        })
        self.assertFalse(candidate["promotion"]["isPromotion"])
        self.assertNotIn("USD 30", candidate["rawLabel"])

    def test_linked_purchase_card_parses_comma_price_without_false_two_dollar_offer(self) -> None:
        fixture = self.rendered_fixtures["linkedAnnualMembership"]

        candidate = adapters.linked_purchase_card_candidates(
            fixture["cardText"], fixture["url"], fixture["purchaseHref"],
            fixture["linkLabel"], fixture["sectionLabel"],
        )[0]

        self.assertEqual((candidate["sourceProductId"], candidate["amount"]), ("100042", 2399))
        self.assertEqual((candidate["cadence"], candidate["productType"]), ("year", "monthly"))
        self.assertEqual(candidate["commitment"]["minimumMonths"], 12)
        self.assertIn("annual-prepay", candidate["sourceProductAliases"])

    def test_linked_purchase_card_fails_closed_without_stable_product_id(self) -> None:
        fixture = self.rendered_fixtures["linkedMonthlyMembership"]
        self.assertEqual(
            adapters.linked_purchase_card_candidates(
                fixture["cardText"], fixture["url"], "https://clients.mindbodyonline.com/classic/ws",
                fixture["linkLabel"], fixture["sectionLabel"],
            ),
            [],
        )

    def test_linked_personal_training_alias_cannot_match_class_membership(self) -> None:
        candidate = adapters.linked_purchase_card_candidates(
            "4 Sessions\n$279\n1 workout per week.",
            "https://www.yubalance.com/noe-valley/",
            "https://clients.mindbodyonline.com/classic/ws?studioid=286632&stype=40&prodId=205",
            "4 Sessions",
            "Monthly Personal Training Memberships",
        )[0]

        self.assertIn("four-sessions-monthly", candidate["sourceProductAliases"])
        self.assertNotIn("four-monthly", candidate["sourceProductAliases"])
        self.assertEqual(candidate["eligibility"]["type"], "trainer-required")
        self.assertFalse(candidate["ordinaryUse"])

    def test_sectioned_cards_reconstruct_recurring_plan_term_and_alias(self) -> None:
        fixture = self.rendered_fixtures["sectionedMonthlyMembership"]

        candidates = adapters.sectioned_price_card_candidates(
            fixture["cardText"], fixture["url"], fixture["sectionLabel"], fixture["sectionNote"],
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate["amount"], candidate["cadence"], candidate["productType"]), (129, "month", "monthly"))
        self.assertEqual(candidate["classAllowance"], {"count": 4, "period": "month", "unlimited": False})
        self.assertEqual(candidate["commitment"]["type"], "minimum-term")
        self.assertEqual(candidate["commitment"]["minimumMonths"], 3)
        self.assertIn("four-monthly", candidate["sourceProductAliases"])
        self.assertEqual(candidate["sourceProductIdAuthority"], "synthetic-label")
        self.assertTrue(candidate["ordinaryUse"])

    def test_sectioned_cards_keep_drop_in_and_intro_offer_distinct(self) -> None:
        package = self.rendered_fixtures["sectionedClassPackage"]
        intro = self.rendered_fixtures["sectionedIntroOffer"]

        drop_in = adapters.sectioned_price_card_candidates(
            package["cardText"], package["url"], package["sectionLabel"], package["sectionNote"],
        )[0]
        promotion = adapters.sectioned_price_card_candidates(
            intro["cardText"], intro["url"], intro["sectionLabel"], intro["sectionNote"],
        )[0]

        self.assertEqual((drop_in["amount"], drop_in["productType"], drop_in["cadence"]), (35, "drop-in", "visit"))
        self.assertTrue(drop_in["ordinaryUse"])
        self.assertEqual(drop_in["sourceProductAliases"][-1], "class-drop-in")
        self.assertEqual((promotion["amount"], promotion["productType"]), (20, "class-pack"))
        self.assertTrue(promotion["promotion"]["isPromotion"])
        self.assertFalse(promotion["ordinaryUse"])

    def test_sectioned_cards_fail_closed_without_one_attached_price_or_known_section(self) -> None:
        self.assertEqual(
            adapters.sectioned_price_card_candidates(
                "4X A MONTH\n$129\nSetup Fee $49", "https://operator.example/location", "Memberships",
            ),
            [],
        )
        self.assertEqual(
            adapters.sectioned_price_card_candidates(
                "Basic\n$99", "https://operator.example/location", "Amenities",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
