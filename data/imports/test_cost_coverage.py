from __future__ import annotations

import unittest

import cost_coverage as coverage


def gym(identifier: str, name: str, monthly: float | None, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": identifier,
        "name": name,
        "gymType": "Fitness centre",
        "description": "",
        "address": "1 Market Street, San Francisco, CA",
        "websiteUrl": "https://example.com/location",
        "sourceName": "Official site",
        "sourceUrl": "https://example.com/location",
        "monthlyPrice": monthly,
        "dayPassPrice": None,
        "priceSource": "Official site" if monthly is not None else "",
        "priceSourceUrl": "https://example.com/pricing" if monthly is not None else "",
        "priceObservedAt": "2026-08-17" if monthly is not None else "",
        "priceNote": "",
        "amenities": [],
    }
    value.update(extra)
    return value


class CostCoverageTests(unittest.TestCase):
    def test_restricted_legacy_scalars_do_not_manufacture_consumer_catalogs(self) -> None:
        value = gym(
            "restricted-legacy",
            "Restricted Legacy Facility",
            99,
            dayPassPrice=25,
            entityKind="gym",
        )

        plans, drops, selected_plan, selected_drop, errors = coverage.build_plan_catalog(value, "restricted")

        self.assertEqual(plans, [])
        self.assertEqual(drops, [])
        self.assertIsNone(selected_plan)
        self.assertIsNone(selected_drop)
        self.assertEqual(errors, [])

    def test_zero_legacy_day_pass_does_not_create_fake_drop_in(self) -> None:
        value = gym("zero-drop", "Zero Drop Gym", 99, dayPassPrice=0, entityKind="gym")

        _plans, drops, _selected_plan, selected_drop, _errors = coverage.build_plan_catalog(value, "membership")

        self.assertEqual(drops, [])
        self.assertIsNone(selected_drop)

    def test_operator_catalog_approval_applies_only_to_explicit_matching_targets(self) -> None:
        first = gym("first", "First Branch", 80, operatorId="example-chain")
        second = gym("second", "Second Branch", 80, operatorId="example-chain")
        unrelated = gym("unrelated", "Unrelated Gym", 70, operatorId="other-chain")
        document = {
            "approvals": [{
                "id": "example-market-catalog",
                "reviewStatus": "approved",
                "operatorId": "example-chain",
                "gymIds": ["first", "second"],
                "sharedFields": {
                    "monthlyPrice": 90,
                    "priceSource": "Example official market catalog",
                    "priceSourceUrl": "https://example.com/pricing",
                    "priceObservedAt": "2026-08-21",
                    "planOffers": [{
                        "sourceProductId": "all-access",
                        "name": "All Access",
                        "amount": 90,
                        "billingInterval": "month",
                        "scopeType": "multi-location",
                        "commitmentType": "month-to-month",
                        "fees": [],
                    }],
                    "catalogCompleteness": {"plans": "complete", "dropIns": "none-observed"},
                },
            }]
        }

        values = [first, second, unrelated]
        self.assertEqual(coverage.apply_operator_catalog_approvals(values, document), 2)
        self.assertEqual(first["monthlyPrice"], 90)
        self.assertEqual(second["monthlyPrice"], 90)
        self.assertEqual(unrelated["monthlyPrice"], 70)
        self.assertEqual(first["operatorCatalogApprovalId"], "example-market-catalog")
        self.assertIsNot(first["planOffers"], second["planOffers"])

    def test_operator_catalog_approval_rejects_operator_mismatch(self) -> None:
        value = gym("branch", "Wrong Branch", 80, operatorId="other-chain")
        document = {
            "approvals": [{
                "id": "bad-scope",
                "reviewStatus": "approved",
                "operatorId": "example-chain",
                "gymIds": ["branch"],
                "sharedFields": {
                    "priceSourceUrl": "https://example.com/pricing",
                    "priceObservedAt": "2026-08-21",
                    "planOffers": [],
                },
            }]
        }
        with self.assertRaisesRegex(ValueError, "belongs to"):
            coverage.apply_operator_catalog_approvals([value], document)

    def test_operator_catalog_approval_ignores_fully_unrelated_scoped_input(self) -> None:
        document = {
            "approvals": [{
                "id": "out-of-scope",
                "reviewStatus": "approved",
                "operatorId": "example-chain",
                "gymIds": ["missing-first", "missing-second"],
                "sharedFields": {
                    "priceSourceUrl": "https://example.com/pricing",
                    "priceObservedAt": "2026-08-21",
                    "planOffers": [],
                },
            }]
        }

        value = gym("unrelated", "Unrelated Gym", 70, operatorId="other-chain")
        self.assertEqual(coverage.apply_operator_catalog_approvals([value], document), 0)
        self.assertNotIn("operatorCatalogApprovalId", value)

    def test_operator_catalog_approval_rejects_partially_missing_target_set(self) -> None:
        document = {
            "approvals": [{
                "id": "partial-scope",
                "reviewStatus": "approved",
                "operatorId": "example-chain",
                "gymIds": ["present", "missing"],
                "sharedFields": {
                    "priceSourceUrl": "https://example.com/pricing",
                    "priceObservedAt": "2026-08-21",
                    "planOffers": [],
                },
            }]
        }

        value = gym("present", "Present Branch", 80, operatorId="example-chain")
        with self.assertRaisesRegex(ValueError, "Unknown operator catalog target"):
            coverage.apply_operator_catalog_approvals([value], document)

    def test_enrichment_is_idempotent_for_fixed_date(self) -> None:
        value = gym("stable", "Stable Gym", 80, dayPassPrice=25)
        value["planOffers"] = [{
            "sourceProductId": "basic",
            "name": "Basic Membership",
            "amount": 80,
            "billingInterval": "month",
            "commitmentType": "month-to-month",
            "fees": [],
        }]
        value["dropInOffers"] = [{
            "sourceProductId": "single-visit",
            "name": "Single Visit",
            "amount": 25,
            "billingInterval": "one-time",
            "commitmentType": "none",
            "fees": [],
        }]

        first_document, first_report, first_review = coverage.enrich_document(
            {"_meta": {}, "gyms": [value]}, "2026-08-21"
        )
        second_document, second_report, second_review = coverage.enrich_document(
            first_document, "2026-08-21"
        )

        self.assertEqual(second_document, first_document)
        self.assertEqual(second_report, first_report)
        self.assertEqual(second_review, first_review)

    def test_classifies_public_and_outdoor_assets_separately(self) -> None:
        public = gym("public", "Richmond Recreation Center", None)
        equipment = gym("equipment", "CHIN-UP", None)
        self.assertEqual(coverage.classify_entity(public), "public-recreation")
        self.assertEqual(coverage.classify_entity(equipment), "outdoor-equipment")
        self.assertEqual(coverage.access_model(public, "public-recreation"), "free-public")

    def test_classifies_hyphenated_equipment_and_named_pool(self) -> None:
        self.assertEqual(coverage.classify_entity(gym("equipment", "BENCH LEG-RAISE", None)), "outdoor-equipment")
        self.assertEqual(coverage.classify_entity(gym("pool", "Charlie Sava Pool", None)), "public-recreation")

    def test_plan_catalog_keeps_fees_separate_and_selects_plan(self) -> None:
        value = gym("priced", "Example Gym", 25, annualFee=49, initiationFee=10)
        plans, _drop_ins, selected_id, _selected_drop_id, errors = coverage.build_plan_catalog(value, "membership")
        self.assertEqual(errors, [])
        self.assertEqual(plans[0]["id"], selected_id)
        self.assertEqual(plans[0]["billing"]["normalizedMonthly"], 25)
        self.assertEqual({fee["type"] for fee in plans[0]["fees"]}, {"annual", "initiation"})

    def test_offer_specific_source_page_is_preserved_in_evidence(self) -> None:
        value = gym("sources", "Example Gym", 80)
        value["planOffers"] = [{
            "sourceProductId": "basic",
            "name": "Basic",
            "amount": 80,
            "billingInterval": "month",
            "sourceUrl": "https://example.com/membership",
            "observedAt": "2026-08-21",
            "fees": [],
        }]
        value["dropInOffers"] = [{
            "sourceProductId": "single",
            "name": "Single Visit",
            "amount": 25,
            "sourceUrl": "https://example.com/day-passes",
            "observedAt": "2026-08-21",
        }]
        plans, drops, *_rest = coverage.build_plan_catalog(value, "membership")
        self.assertEqual(plans[0]["evidence"]["url"], "https://example.com/membership")
        self.assertEqual(plans[0]["evidence"]["sourceProductId"], "basic")
        self.assertEqual(drops[0]["evidence"]["url"], "https://example.com/day-passes")
        self.assertEqual(drops[0]["evidence"]["sourceProductId"], "single")

    def test_activation_fee_remains_linked_to_selected_plan(self) -> None:
        value = gym("activation", "Example Combat Gym", 229)
        value["planOffers"] = [{
            "sourceProductId": "unlimited",
            "name": "Unlimited",
            "amount": 229,
            "billingInterval": "month",
            "commitmentType": "month-to-month",
            "fees": [{"type": "activation", "amount": 29, "currency": "USD", "cadence": "one-time", "mandatory": True}],
        }]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        published = document["gyms"][0]
        self.assertEqual(published["activationFee"], 29)
        selected = next(plan for plan in published["plans"] if plan["id"] == published["selectedPlanId"])
        self.assertEqual(selected["fees"][0]["type"], "activation")

    def test_upfront_last_month_dues_are_not_mandatory_fees(self) -> None:
        value = gym("dues", "Example Gym", 29.99)
        value["planOffers"] = [{
            "sourceProductId": "basic",
            "name": "Basic",
            "amount": 29.99,
            "billingInterval": "month",
            "commitmentType": "month-to-month",
            "fees": [{"type": "annual", "amount": 69, "mandatory": True}],
            "upfrontDues": [{"type": "last-month-dues", "amount": 29.99, "currency": "USD"}],
        }]
        plans, _drop_ins, selected_id, _selected_drop_id, errors = coverage.build_plan_catalog(value, "membership")
        selected = next(plan for plan in plans if plan["id"] == selected_id)
        self.assertEqual(errors, [])
        self.assertEqual(selected["upfrontDues"][0]["type"], "last-month-dues")
        self.assertEqual([fee["type"] for fee in selected["fees"]], ["annual"])

    def test_modality_estimate_does_not_fill_verified_field(self) -> None:
        gyms = [gym(f"yoga-{index}", f"Example Yoga {index}", price) for index, price in enumerate((100, 105, 110, 115, 120, 125, 130, 135))]
        gyms.append(gym("missing", "Missing Yoga", None))
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": gyms}, "2026-08-17")
        missing = next(item for item in document["gyms"] if item["id"] == "missing")
        self.assertIsNone(missing["monthlyPrice"])
        self.assertEqual(missing["pricingStatus"], "estimated")
        self.assertEqual(missing["estimatedMonthly"]["point"], 120)
        self.assertEqual(missing["estimatedMonthly"]["rangeMethod"], "cross-validated-80-percent-residual-interval")
        self.assertTrue(report["publicationChecks"]["noEstimateInVerifiedField"])
        self.assertIn("gated", report["pricingStatusCounts"])
        self.assertIn("unresolved", report["pricingStatusCounts"])

    def test_free_public_listing_never_receives_estimate(self) -> None:
        gyms = [gym(f"base-{index}", f"Example Gym {index}", price) for index, price in enumerate((50, 60, 70, 80, 90))]
        gyms.append(gym("park", "Mission Playground", None))
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": gyms}, "2026-08-17")
        park = next(item for item in document["gyms"] if item["id"] == "park")
        self.assertEqual(park["pricingStatus"], "free")
        self.assertIsNone(park["estimatedMonthly"])

    def test_restricted_facility_is_not_in_adult_commercial_denominator(self) -> None:
        restricted = gym("youth", "Youth Gymnastics", None, entityKindOverride="studio", accessModelOverride="restricted")
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [restricted]}, "2026-08-17")
        self.assertEqual(document["gyms"][0]["pricingStatus"], "not-applicable")
        self.assertEqual(report["commercialListings"], 0)

    def test_trainer_restricted_contact_price_is_gated_not_not_applicable(self) -> None:
        restricted = gym(
            "trainer",
            "Trainer-Only Studio",
            None,
            entityKindOverride="studio",
            accessModelOverride="restricted",
            pricingAccess="contact-required",
        )
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [restricted]}, "2026-08-17")
        self.assertEqual(document["gyms"][0]["pricingStatus"], "gated")
        self.assertIn("trainer", document["gyms"][0]["pricingBlocker"])
        self.assertEqual(report["commercialListings"], 0)

    def test_enrollment_paused_facility_is_not_in_actionable_commercial_denominator(self) -> None:
        available = gym("available", "Available Gym", 80)
        paused = gym("paused", "Paused Gym", None, accessAvailability="waitlist")
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [available, paused]}, "2026-08-17")
        self.assertEqual(next(item for item in document["gyms"] if item["id"] == "paused")["pricingStatus"], "gated")
        self.assertEqual(report["commercialListings"], 1)
        self.assertEqual(report["actionableCommercialCoverage"], 1)

    def test_official_class_pack_is_actionable_without_monthly_price(self) -> None:
        value = gym("pack", "Example Training Studio", None, accessModelOverride="class-pack")
        value["planOffers"] = [{
            "sourceProductId": "four-pack",
            "name": "Four Sessions",
            "productType": "class-pack",
            "amount": 200,
            "billingInterval": "one-time",
            "commitmentType": "none",
        }]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-17")
        published = document["gyms"][0]
        self.assertEqual(published["pricingStatus"], "pay-per-visit")
        self.assertIsNone(published["monthlyPrice"])
        self.assertIsNone(published["selectedPlanId"])

    def test_contact_gated_price_can_use_validated_estimate_without_leaking_into_verified_field(self) -> None:
        priced = [gym(f"pilates-{index}", f"Example Pilates {index}", price) for index, price in enumerate((100, 105, 110, 115, 120, 125, 130, 135))]
        target = gym("gated", "Contact Only Pilates", None, pricingAccess="contact-required")
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [*priced, target]}, "2026-08-17")
        published = next(item for item in document["gyms"] if item["id"] == "gated")
        self.assertEqual(published["pricingStatus"], "estimated")
        self.assertEqual(published["estimatedMonthly"]["point"], 120)
        self.assertIsNone(published["monthlyPrice"])
        self.assertIn("requires direct contact", published["pricingBlocker"])

    def test_contact_gated_price_stays_gated_without_validated_cohort(self) -> None:
        target = gym("gated", "Contact Only Pilates", None, pricingAccess="contact-required")
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [target]}, "2026-08-17")
        published = document["gyms"][0]
        self.assertEqual(published["pricingStatus"], "gated")
        self.assertIsNone(published["estimatedMonthly"])
        self.assertIsNone(published["monthlyPrice"])

    def test_coming_soon_location_never_retains_an_estimate(self) -> None:
        priced = [gym(f"pilates-{index}", f"Example Pilates {index}", price) for index, price in enumerate((100, 105, 110, 115, 120, 125, 130, 135))]
        target = gym("coming", "Coming Soon Pilates", None, recordStatus="coming_soon")
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [*priced, target]}, "2026-08-17")
        published = next(item for item in document["gyms"] if item["id"] == "coming")
        self.assertEqual(published["pricingStatus"], "gated")
        self.assertIsNone(published["estimatedMonthly"])
        self.assertTrue(report["publicationChecks"]["noEstimateOnGatedOrUnavailable"])

    def test_contact_gate_does_not_override_unavailable_estimate_exclusions(self) -> None:
        priced = [gym(f"pilates-{index}", f"Example Pilates {index}", price) for index, price in enumerate((100, 105, 110, 115, 120, 125, 130, 135))]
        target = gym(
            "coming-contact",
            "Coming Soon Contact Pilates",
            None,
            recordStatus="coming_soon",
            pricingAccess="contact-required",
        )
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [*priced, target]}, "2026-08-17")
        published = next(item for item in document["gyms"] if item["id"] == "coming-contact")
        self.assertEqual(published["pricingStatus"], "gated")
        self.assertIsNone(published["estimatedMonthly"])
        self.assertTrue(report["publicationChecks"]["noEstimateOnRestrictedConflictedOrUnavailable"])

    def test_estimate_requires_minimum_comparable_sample(self) -> None:
        target = gym("target", "Unpriced Martial Arts", None)
        target["entityKind"] = "martial-arts"
        target["modality"] = "martial-arts-boxing"
        target["operatorKey"] = "target"
        target["accessModel"] = "class-membership"
        cohorts = ({}, {"martial-arts-boxing|class-membership|independent|unknown": [100] * 7, "martial-arts-boxing|class-membership|independent": [100] * 7}, {})
        self.assertIsNone(coverage.estimate_for(target, cohorts, "2026-08-17"))

    def test_only_reviewed_official_external_comparables_enter_cohorts(self) -> None:
        external = [
            {"reviewStatus": "approved", "evidenceTier": "official-public", "exactLocationMatch": "exact-location", "normalizedMonthly": 120, "operatorKey": "chain", "modality": "yoga", "accessModel": "class-membership", "marketModel": "chain", "allowanceBucket": "up-to-4", "entityKind": "studio"},
            {"reviewStatus": "pending", "evidenceTier": "official-public", "exactLocationMatch": "exact-location", "normalizedMonthly": 1, "operatorKey": "chain", "modality": "yoga", "accessModel": "class-membership", "marketModel": "chain", "allowanceBucket": "up-to-4", "entityKind": "studio"},
            {"reviewStatus": "approved", "evidenceTier": "reported", "exactLocationMatch": "exact-location", "normalizedMonthly": 2, "operatorKey": "chain", "modality": "yoga", "accessModel": "class-membership", "marketModel": "chain", "allowanceBucket": "up-to-4", "entityKind": "studio"},
        ]
        by_operator, by_modality, _by_entity = coverage.build_cohorts([], external)
        self.assertEqual(by_operator["chain"], [120.0])
        self.assertEqual(by_modality["yoga|class-membership|chain|up-to-4"], [120.0])

    def test_estimate_display_rounding_never_narrows_interval(self) -> None:
        estimate = coverage.estimate_from([101, 104, 109, 113, 118, 124, 131, 138], "medium", "test", "2026-08-17")
        self.assertEqual(estimate["low"] % 5, 0)
        self.assertEqual(estimate["high"] % 5, 0)
        self.assertLessEqual(estimate["low"], estimate["point"])
        self.assertGreaterEqual(estimate["high"], estimate["point"])
        self.assertGreaterEqual(estimate["validationRangeCoverage"], 0.75)

    def test_residual_percentiles_use_conservative_nearest_rank(self) -> None:
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        self.assertEqual(coverage.percentile(values, 0.10), 1)
        self.assertEqual(coverage.percentile(values, 0.90), 8)

    def test_failed_modality_validation_withholds_public_estimate(self) -> None:
        target = {"modality": "independent-gym"}
        estimate = {"confidence": "medium"}
        validation = {
            "independent-gym": {
                "sampleSize": 10,
                "medianAbsolutePercentageError": 0.31,
                "rangeCoverage": 0.6,
            }
        }
        self.assertFalse(coverage.estimate_passes_modality_validation(target, estimate, validation))

    def test_selection_normalizes_weekly_allowance_and_prefers_smallest_four_plus(self) -> None:
        value = gym("crossfit", "Example CrossFit", 219)
        value["planOffers"] = [
            {"sourceProductId": "two-weekly", "name": "2x Weekly", "amount": 219, "billingInterval": "month", "classAllowance": {"count": 2, "period": "week"}, "commitmentType": "month-to-month"},
            {"sourceProductId": "unlimited", "name": "Unlimited", "amount": 299, "billingInterval": "month", "classAllowance": "unlimited", "commitmentType": "month-to-month"},
        ]
        plans, _drop_ins, selected_id, _drop_id, errors = coverage.build_plan_catalog(value, "class-membership")
        self.assertEqual(errors, [])
        self.assertEqual(next(plan["name"] for plan in plans if plan["id"] == selected_id), "2x Weekly")

    def test_class_allowance_recovers_word_number_and_monthly_label(self) -> None:
        allowance = coverage.class_allowance("Five classes per month")
        self.assertEqual(allowance, {"count": 5.0, "period": "month", "unlimited": False, "disclosed": True})
        monthly = coverage.class_allowance("4 Classes Monthly")
        self.assertEqual(monthly, {"count": 4.0, "period": "month", "unlimited": False, "disclosed": True})

    def test_unspecified_scope_defaults_to_single_location(self) -> None:
        value = gym("scope", "Example Studio", 100)
        value["planOffers"] = [{"sourceProductId": "basic", "name": "Basic", "amount": 100, "accessScope": "Ordinary recurring studio access"}]
        plans, _drop_ins, _selected_id, _drop_id, _errors = coverage.build_plan_catalog(value, "class-membership")
        self.assertEqual(plans[0]["scopeType"], "single-location")

    def test_selection_prefers_month_to_month_over_cheaper_term(self) -> None:
        value = gym("terms", "Example Gym", 109)
        value["planOffers"] = [
            {"sourceProductId": "flex", "name": "Flexible", "amount": 109, "billingInterval": "month", "commitmentType": "month-to-month"},
            {"sourceProductId": "annual", "name": "Annual", "amount": 99, "billingInterval": "month", "commitmentType": "fixed-term", "minimumCommitmentMonths": 12},
        ]
        plans, _drop_ins, selected_id, _drop_id, errors = coverage.build_plan_catalog(value, "membership")
        self.assertEqual(errors, [])
        self.assertEqual(next(plan["name"] for plan in plans if plan["id"] == selected_id), "Flexible")

    def test_four_week_billing_is_normalized_but_original_cadence_is_retained(self) -> None:
        value = gym("four-week", "Example Gym", 116.95)
        value["planOffers"] = [{"sourceProductId": "four-week", "name": "Four Week", "amount": 107.95, "billingInterval": "4 weeks", "commitmentType": "month-to-month"}]
        plans, _drop_ins, selected_id, _drop_id, errors = coverage.build_plan_catalog(value, "membership")
        selected = next(plan for plan in plans if plan["id"] == selected_id)
        self.assertEqual(errors, [])
        self.assertEqual(selected["billing"]["interval"], "4 weeks")
        self.assertEqual(selected["billing"]["normalizedMonthly"], 116.95)

    def test_thirty_day_billing_is_normalized_but_original_cadence_is_retained(self) -> None:
        value = gym("thirty-day", "Example Gym", 302.34)
        value["planOffers"] = [{"sourceProductId": "thirty-day", "name": "Thirty Day", "amount": 298, "billingInterval": "30 days", "commitmentType": "minimum-term", "minimumCommitmentMonths": 3}]
        plans, _drop_ins, selected_id, _drop_id, errors = coverage.build_plan_catalog(value, "membership")
        selected = next(plan for plan in plans if plan["id"] == selected_id)
        self.assertEqual(errors, [])
        self.assertEqual(selected["billing"]["interval"], "30 days")
        self.assertEqual(selected["billing"]["normalizedMonthly"], 302.34)

    def test_different_studio_products_do_not_share_one_modality(self) -> None:
        orangetheory = gym("otf", "Orangetheory Fitness", None)
        f45 = gym("f45", "F45 Training", None)
        row_house = gym("row", "Row House", None)
        self.assertEqual(coverage.modality(orangetheory, "studio"), "interval-studio")
        self.assertEqual(coverage.modality(f45, "studio"), "functional-hiit-studio")
        self.assertEqual(coverage.modality(row_house, "studio"), "cycling-rowing-studio")

    def test_only_reviewed_fields_are_promoted_to_verified_price(self) -> None:
        gyms = [gym("target", "Example Gym", None)]
        applied = coverage.apply_approved_observations(
            gyms,
            {"approvals": [{"gymId": "target", "monthlyPrice": 99, "priceSourceUrl": "https://example.com/pricing", "unexpected": "ignored"}]},
        )
        self.assertEqual(applied, 1)
        self.assertEqual(gyms[0]["monthlyPrice"], 99)
        self.assertNotIn("unexpected", gyms[0])
        self.assertEqual(gyms[0]["freshness"], "verified")

    def test_two_recent_independent_reports_create_separate_summary(self) -> None:
        gyms = [gym("target", "Example Independent Gym", None)]
        reports = {
            "reports": [
                {"id": "a", "gymId": "target", "productType": "monthly", "amount": 100, "cadence": "month", "publishedAt": "2026-03-01", "capturedAt": "2026-08-18", "sourceUrl": "https://example.net/a", "sourcePublisher": "A", "sourceType": "community", "identityMatch": "exact-location", "eligibility": "standard-adult", "reviewStatus": "approved", "evidenceLabel": "Reported $100 monthly."},
                {"id": "b", "gymId": "target", "productType": "monthly", "amount": 110, "cadence": "month", "publishedAt": "2026-05-01", "capturedAt": "2026-08-18", "sourceUrl": "https://example.org/b", "sourcePublisher": "B", "sourceType": "community", "identityMatch": "exact-location", "eligibility": "standard-adult", "reviewStatus": "approved", "evidenceLabel": "Reported $110 monthly."},
            ]
        }
        attached = coverage.attach_reported_evidence(gyms, reports, "2026-08-18")
        self.assertEqual(attached, 2)
        self.assertEqual(gyms[0]["reportedMonthly"]["point"], 105)
        self.assertEqual(gyms[0]["reportedMonthly"]["confidence"], "medium")
        self.assertIsNone(gyms[0]["monthlyPrice"])

    def test_single_or_old_report_does_not_create_summary(self) -> None:
        gyms = [gym("target", "Example Independent Gym", None)]
        reports = {"reports": [{"id": "old", "gymId": "target", "productType": "monthly", "amount": 90, "cadence": "month", "publishedAt": "2023-01-01", "sourceUrl": "https://example.net/old", "identityMatch": "exact-location", "eligibility": "standard-adult", "reviewStatus": "approved"}]}
        coverage.attach_reported_evidence(gyms, reports, "2026-08-18")
        self.assertIsNone(gyms[0]["reportedMonthly"])

    def test_only_approved_source_discoveries_apply(self) -> None:
        gyms = [gym("target", "Example Gym", None)]
        count = coverage.apply_source_discoveries(gyms, {"discoveries": [
            {"gymId": "target", "reviewStatus": "approved", "websiteUrl": "https://official.example/", "officialUrl": "https://official.example/", "operatorLocationId": "location-a", "latitude": 37.77, "longitude": -122.41, "sourceUrl": "https://official.example/", "sourceType": "official-site", "observedAt": "2026-08-18"},
            {"gymId": "target", "reviewStatus": "pending", "name": "Wrong Name"},
        ]})
        self.assertEqual(count, 1)
        self.assertEqual(gyms[0]["websiteUrl"], "https://official.example/")
        self.assertEqual(gyms[0]["officialUrl"], "https://official.example/")
        self.assertEqual(gyms[0]["operatorLocationId"], "location-a")
        self.assertEqual(gyms[0]["latitude"], 37.77)
        self.assertEqual(gyms[0]["longitude"], -122.41)
        self.assertEqual(gyms[0]["name"], "Example Gym")

    def test_disconnected_official_domain_has_an_explicit_blocker(self) -> None:
        value = gym("target", "Example Gym", None, websiteUrl="", pricingAccess="official-domain-disconnected")
        self.assertIn("disconnected", coverage.blocker_for(value))

    def test_official_status_conflict_stays_visible_unresolved_without_estimate(self) -> None:
        conflict = gym(
            "conflict",
            "Conflicted Studio",
            None,
            websiteUrl="https://example.com",
            pricingAccess="official-status-conflict",
            entityKindOverride="studio",
            accessModelOverride="class-membership",
        )
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [conflict]}, "2026-08-19")
        result = document["gyms"][0]
        self.assertEqual(result.get("publicationStatus", "publish"), "publish")
        self.assertEqual(result["pricingStatus"], "unresolved")
        self.assertIsNone(result["estimatedMonthly"])
        self.assertIn("disagree", result["pricingBlocker"])

    def test_drop_in_selection_can_exclude_non_neutral_maintenance_variant(self) -> None:
        value = gym("stretch", "Stretch Studio", 239, entityKindOverride="studio", accessModelOverride="class-membership")
        value["planOffers"] = [{"sourceProductId": "four", "name": "Four Sessions", "classAllowance": 4, "amount": 239, "billingInterval": "month"}]
        value["dropInOffers"] = [
            {"sourceProductId": "maintenance", "name": "Maintenance Session", "amount": 75, "ordinaryUse": False},
            {"sourceProductId": "standard", "name": "Standard Session", "amount": 135, "ordinaryUse": True},
        ]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        self.assertEqual(document["gyms"][0]["dayPassPrice"], 135)

    def test_official_url_falls_back_to_reviewed_operator_price_source(self) -> None:
        value = gym("priced", "Officially Priced Gym", 100, websiteUrl="", officialUrl="")
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        self.assertEqual(document["gyms"][0]["officialUrl"], "https://example.com/pricing")

    def test_location_metadata_requires_review_and_hashed_official_evidence(self) -> None:
        gyms = [gym("target", "Example Gym", None, hours="Hours not listed", canonicalAddress="san francisco")]
        count = coverage.apply_location_metadata_approvals(gyms, {"approvals": [
            {"gymId": "target", "reviewStatus": "approved", "sourceUrl": "https://example.com/location", "capturedAt": "2026-08-18", "contentHash": "abc", "proposedChanges": {"address": "2081 Hayes Street, San Francisco, CA 94117", "hours": "Mo-Fr 06:00-22:00", "amenities": ["Showers"]}},
            {"gymId": "target", "reviewStatus": "pending", "sourceUrl": "https://example.com/wrong", "capturedAt": "2026-08-18", "contentHash": "def", "proposedChanges": {"name": "Wrong"}},
        ]})
        self.assertEqual(count, 1)
        self.assertEqual(gyms[0]["hours"], "Mo-Fr 06:00-22:00")
        self.assertEqual(gyms[0]["amenities"], ["Showers"])
        self.assertEqual(gyms[0]["name"], "Example Gym")
        self.assertEqual(gyms[0]["canonicalAddress"], "2081 hayes st")

    def test_venue_prefixed_exact_address_counts_as_exact(self) -> None:
        value = gym("venue", "Venue Gym", 100, address="Rutter Center, 1675 Owens Street, San Francisco, CA 94158", canonicalAddress="rutter center")
        _document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        self.assertEqual(report["commercialExactAddressListings"], 1)

    def test_named_offer_with_undisclosed_amount_is_not_coerced_to_free(self) -> None:
        value = gym("undisclosed", "Contact Catalog Gym", None, pricingAccess="contact-required")
        value["planOffers"] = [{
            "sourceProductId": "par",
            "name": "PAR Membership",
            "accessScope": "Four coached sessions per month",
            "classAllowance": 4,
            "amount": None,
            "billingInterval": "month",
            "purchaseMethod": "contact-required",
        }]
        plans, _drop_ins, selected_id, _drop_id, errors = coverage.build_plan_catalog(value, "class-membership")
        self.assertIsNone(plans[0]["billing"]["amount"])
        self.assertIsNone(plans[0]["billing"]["normalizedMonthly"])
        self.assertIsNone(selected_id)
        self.assertEqual(errors, [])

    def test_every_absent_compatibility_price_has_its_own_reason(self) -> None:
        value = gym("missing", "Example Missing Gym", None, pricingAccess="contact-required", hours="Hours not listed")
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        result = document["gyms"][0]
        self.assertTrue(result["monthlyPriceBlocker"])
        self.assertTrue(result["dayPassPriceBlocker"])
        self.assertEqual(result["metadataStatus"]["hours"]["status"], "not-published")
        self.assertTrue(report["publicationChecks"]["allNullMonthlyPricesHaveReasons"])
        self.assertTrue(report["publicationChecks"]["allNullDayPassPricesHaveReasons"])
        self.assertTrue(report["publicationChecks"]["allMetadataGapsHaveStates"])

    def test_hours_metadata_separates_exact_schedule_semantics_and_unpublished(self) -> None:
        exact = gym("exact", "Exact Gym", None, hours="Mon-Fri 6:00am-9:00pm")
        scheduled = gym("scheduled", "Scheduled Studio", None, hours="Class schedule varies; reserve before visiting")
        missing = gym("missing-hours", "Missing Gym", None, hours="Hours not published; the operator page is unavailable")
        document, report, _review = coverage.enrich_document(
            {"_meta": {}, "gyms": [exact, scheduled, missing]}, "2026-08-19"
        )
        statuses = {item["id"]: item["metadataStatus"]["hours"]["status"] for item in document["gyms"]}
        self.assertEqual(statuses["exact"], "exact-hours")
        self.assertEqual(statuses["scheduled"], "access-schedule")
        self.assertEqual(statuses["missing-hours"], "not-published")
        self.assertEqual(report["fieldCoverage"]["exactHoursCount"], 1)
        self.assertEqual(report["fieldCoverage"]["accessScheduleSemanticsCount"], 1)
        self.assertEqual(report["fieldCoverage"]["hoursUnpublishedCount"], 1)

    def test_restricted_commercial_with_dead_official_site_is_unresolved_not_not_applicable(self) -> None:
        value = gym(
            "dead-site",
            "Restricted Personal Training",
            None,
            entityKindOverride="studio",
            accessModelOverride="restricted",
            pricingAccess="official-domain-parked",
        )
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        self.assertEqual(document["gyms"][0]["pricingStatus"], "unresolved")

    def test_audit_rows_include_early_exit_pricing_states(self) -> None:
        free = gym("audit-free", "Mission Playground", None)
        gated = gym("audit-gated", "Contact Gym", None, pricingAccess="contact-required")
        _document, report, review = coverage.enrich_document({"_meta": {}, "gyms": [free, gated]}, "2026-08-19")
        self.assertEqual({row["id"] for row in review["records"]}, {"audit-free", "audit-gated"})
        self.assertEqual(next(row for row in review["records"] if row["id"] == "audit-free")["pricingStatus"], "free")
        self.assertEqual(next(row for row in review["records"] if row["id"] == "audit-gated")["pricingStatus"], "gated")
        self.assertTrue(report["publicationChecks"]["allCatalogCompletenessStatesExplicit"])

    def test_cost_enrichment_preserves_canonical_classification_on_repeat(self) -> None:
        value = gym("repeat", "Parent Fitness Course", None, entityKindOverride="public-recreation", accessModelOverride="free-public")
        first, first_report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        second, second_report, _review = coverage.enrich_document(first, "2026-08-19")
        self.assertEqual(first["gyms"][0]["entityKind"], second["gyms"][0]["entityKind"])
        self.assertEqual(first["gyms"][0]["accessModel"], second["gyms"][0]["accessModel"])
        self.assertEqual(first_report["entityKindCounts"], second_report["entityKindCounts"])

    def test_description_is_specific_and_derived_from_structured_fields(self) -> None:
        value = gym(
            "described",
            "Example Yoga",
            120,
            neighborhood="Mission",
            entityKindOverride="studio",
            modalityOverride="yoga",
            amenities=["Yoga mats", "Showers"],
        )
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        description = document["gyms"][0]["description"]
        self.assertIn("Example Yoga", description)
        self.assertIn("Mission", description)
        self.assertIn("$120.00", description)
        self.assertNotIn("OpenStreetMap listing", description)
        self.assertTrue(report["publicationChecks"]["noGenericDescriptions"])

    def test_identity_audit_accepts_distinct_public_location_ids_at_one_address(self) -> None:
        shared = [
            {
                **gym("pool", "Hamilton Pool", None, address="1900 Geary Boulevard, San Francisco"),
                "operatorId": "sfrecpark.org",
                "operatorLocationId": "hamilton-pool-215",
            },
            {
                **gym("rec", "Hamilton Recreation Center", None, address="1900 Geary Boulevard, San Francisco"),
                "operatorId": "sfrecpark.org",
                "operatorLocationId": "hamilton-recreation-center-93",
            },
        ]
        audit = coverage.identity_duplicate_audit(shared)
        self.assertEqual(audit["duplicates"], [])
        self.assertEqual(len(audit["distinctCoLocations"]), 1)

    def test_complete_catalog_selects_basic_typical_and_highest_access_views(self) -> None:
        value = gym("views", "Plan Views Studio", 89, entityKindOverride="studio", accessModelOverride="class-membership")
        value["planOffers"] = [
            {"sourceProductId": "four", "name": "4 Classes", "amount": 89, "billingInterval": "month", "classAllowance": 4, "commitmentType": "month-to-month"},
            {"sourceProductId": "eight", "name": "8 Classes", "amount": 149, "billingInterval": "month", "classAllowance": 8, "commitmentType": "month-to-month"},
            {"sourceProductId": "unlimited", "name": "Unlimited", "amount": 219, "billingInterval": "month", "classAllowance": "unlimited", "commitmentType": "month-to-month"},
        ]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-19")
        result = document["gyms"][0]
        self.assertTrue(result["selectedPlanId"].endswith(":four"))
        self.assertTrue(result["typicalPlanId"].endswith(":eight"))
        self.assertTrue(result["highestAccessPlanId"].endswith(":unlimited"))

    def test_full_service_highest_access_respects_disclosed_market_breadth(self) -> None:
        value = gym("scope-views", "Scope Views Gym", 242)
        value["planOffers"] = [
            {"sourceProductId": "select", "name": "Select", "amount": 242, "accessScope": "One-club access at the named location", "scopeType": "single-location"},
            {"sourceProductId": "all-access", "name": "All-Access", "amount": 350, "accessScope": "Access to 90+ clubs across North America", "scopeType": "multi-location"},
            {"sourceProductId": "destination", "name": "Destination", "amount": 370, "accessScope": "Access to 110+ clubs globally", "scopeType": "multi-location"},
            {"sourceProductId": "destination-west", "name": "Destination West", "amount": 410, "accessScope": "Destination access plus two expanded West Coast clubs", "scopeType": "multi-location"},
        ]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-21")
        result = document["gyms"][0]
        self.assertTrue(result["selectedPlanId"].endswith(":select"))
        self.assertTrue(result["highestAccessPlanId"].endswith(":destination-west"))

    def test_best_value_requires_an_explicit_operator_label(self) -> None:
        value = gym("value", "Value Studio", 119, entityKindOverride="studio", accessModelOverride="class-membership")
        value["planOffers"] = [
            {"sourceProductId": "four", "name": "4 Classes", "amount": 119, "classAllowance": 4, "bestValueLabel": False},
            {"sourceProductId": "eight", "name": "8 Classes Most Popular", "amount": 189, "classAllowance": 8, "bestValueLabel": True},
        ]
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-20")
        result = document["gyms"][0]
        self.assertEqual(result["bestValuePlanId"], "value:plan:eight")
        self.assertEqual(result["planViewStatus"]["bestValue"]["status"], "selected")

    def test_official_range_is_context_not_verified_price(self) -> None:
        value = gym(
            "range", "Range Training", None,
            pricingAccess="contact-required",
            priceSourceUrl="https://example.com/rates",
            priceObservedAt="2026-08-20",
            priceNote="Official one-on-one training costs $150-$250 per session depending on coach.",
        )
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-20")
        result = document["gyms"][0]
        self.assertIsNone(result["monthlyPrice"])
        self.assertEqual(result["pricingStatus"], "gated")
        self.assertEqual(result["costContext"][0]["low"], 150)
        self.assertEqual(result["costContext"][0]["high"], 250)
        self.assertFalse(result["costContext"][0]["selectable"])
        self.assertTrue(report["publicationChecks"]["costContextNeverLeaksIntoVerifiedFields"])

    def test_explicit_conflicting_context_preserves_warning_and_normalizes_four_week_cadence(self) -> None:
        value = gym(
            "conflicting-context",
            "Conflicting Context Studio",
            None,
            pricingAccess="official-price-conflict",
            costContextOffers=[{
                "kind": "conflicting-price",
                "label": "One current terms block",
                "amount": 220,
                "cadence": "4 weeks",
                "productType": "class-membership",
                "sourceUrl": "https://example.com/terms",
                "observedAt": "2026-08-21",
                "conflictFlags": ["duplicate-contradictory-terms"],
                "note": "Do not select while the live terms conflict remains.",
            }],
        )
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-21")
        result = document["gyms"][0]
        context = result["costContext"][0]
        self.assertEqual(result["pricingStatus"], "unresolved")
        self.assertIsNone(result["monthlyPrice"])
        self.assertEqual(context["kind"], "conflicting-price")
        self.assertEqual(context["normalizedMonthlyLow"], 238.33)
        self.assertEqual(context["conflictFlags"], ["duplicate-contradictory-terms"])
        self.assertTrue(report["publicationChecks"]["costContextNeverLeaksIntoVerifiedFields"])

    def test_catalog_coverage_reports_selected_only_price_as_reconstruction_priority(self) -> None:
        selected_only = gym("selected-only", "Selected Only Gym", 99)
        full = gym("full", "Full Catalog Gym", 119)
        full["planOffers"] = [{
            "sourceProductId": "basic",
            "name": "Basic Membership",
            "amount": 119,
            "billingInterval": "month",
            "billingIntervalCount": 1,
            "productType": "membership",
            "accessScope": "Normal gym access",
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "promotion": {"isPromotion": False, "label": "", "expiresAt": None},
            "fees": [],
            "evidence": {"url": "https://example.com/full", "observedAt": "2026-08-21", "source": "Official", "method": "fixture", "rawLabel": "Basic Membership"},
        }]
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [selected_only, full]}, "2026-08-21")
        self.assertEqual(len(document["gyms"]), 2)
        queue = report["catalogReconstructionQueue"]
        self.assertEqual(queue["publiclyPricedCommercialListings"], 2)
        self.assertEqual(queue["reconstructedRelevantCatalogListings"], 1)
        self.assertEqual(queue["reconstructedRelevantCatalogCoverage"], 0.5)
        self.assertEqual([item["id"] for item in queue["priorityRecords"]], ["selected-only"])
        self.assertFalse(report["releaseGates"]["commercialCatalogCoverageAtLeast90Percent"])

    def test_selected_only_catalog_does_not_invent_typical_or_highest_access(self) -> None:
        document, _report, _review = coverage.enrich_document({"_meta": {}, "gyms": [gym("legacy", "Legacy Gym", 99)]}, "2026-08-19")
        result = document["gyms"][0]
        self.assertIsNone(result["typicalPlanId"])
        self.assertEqual(result["planViewStatus"]["typical"]["status"], "unavailable-incomplete-catalog")

    def test_source_fragment_does_not_invent_typical_or_highest_access(self) -> None:
        value = gym("fragment", "Fragment Studio", 89, entityKindOverride="studio", accessModelOverride="class-membership")
        value["planOffers"] = [
            {"sourceProductId": "four", "name": "4 Classes", "amount": 89, "billingInterval": "month", "classAllowance": 4},
            {"sourceProductId": "eight", "name": "8 Classes", "amount": 149, "billingInterval": "month", "classAllowance": 8},
        ]
        value["catalogCompleteness"] = {"plans": "partial", "dropIns": "none-observed"}
        document, report, _review = coverage.enrich_document({"_meta": {}, "gyms": [value]}, "2026-08-20")
        result = document["gyms"][0]
        self.assertEqual(result["catalogStatus"]["plans"]["status"], "source-fragment")
        self.assertIsNone(result["typicalPlanId"])
        self.assertIsNone(result["highestAccessPlanId"])
        self.assertEqual(report["fieldCoverage"]["sourcePlanFragmentCount"], 1)

    def test_operator_confirmed_price_is_separate_from_verified_monthly(self) -> None:
        value = gym("confirmed", "Confirmed Gym", None, pricingAccess="contact-required")
        applied = coverage.attach_operator_confirmed([value], {"approvals": [{
            "gymId": "confirmed", "reviewStatus": "approved", "standardAdult": True,
            "confidential": False, "amount": 120, "cadence": "4 weeks", "intervalCount": 1,
            "planName": "Standard", "confirmedAt": "2026-08-18", "contactMethod": "email",
            "evidenceId": "sha256:test",
        }]}, "2026-08-19")
        self.assertEqual(applied, 1)
        self.assertEqual(value["operatorConfirmedMonthly"]["normalizedMonthly"], 130)
        self.assertIsNone(value.get("monthlyPrice"))

    def test_reviewed_deal_is_current_and_cannot_replace_ordinary_price(self) -> None:
        value = gym("deal", "Deal Gym", 100)
        applied = coverage.attach_deals([value], {"approvals": [{
            "id": "summer",
            "gymId": "deal",
            "reviewStatus": "approved",
            "standardAdult": True,
            "replacesOrdinaryPrice": False,
            "label": "First month $49",
            "amount": 49,
            "currency": "USD",
            "productType": "monthly",
            "cadence": "first month",
            "sourceUrl": "https://example.com/deal",
            "capturedAt": "2026-08-18",
            "expiresAt": "2026-08-31",
            "contentHash": "a" * 64,
        }]}, "2026-08-19")
        self.assertEqual(applied, 1)
        self.assertEqual(value["deals"][0]["amount"], 49)
        self.assertFalse(value["deals"][0]["replacesOrdinaryPrice"])
        self.assertEqual(value["monthlyPrice"], 100)

    def test_stale_or_unreviewed_deals_do_not_publish(self) -> None:
        value = gym("deal", "Deal Gym", 100)
        base = {
            "gymId": "deal", "standardAdult": True, "replacesOrdinaryPrice": False,
            "amount": 49, "sourceUrl": "https://example.com/deal", "contentHash": "b" * 64,
        }
        applied = coverage.attach_deals([value], {"approvals": [
            {**base, "reviewStatus": "approved", "capturedAt": "2026-08-01"},
            {**base, "reviewStatus": "pending", "capturedAt": "2026-08-18"},
        ]}, "2026-08-19")
        self.assertEqual(applied, 0)
        self.assertEqual(value["deals"], [])

    def test_location_metadata_evidence_is_deduplicated_on_repeat(self) -> None:
        value = gym("location", "Location Gym", 100)
        approval = {
            "gymId": "location", "reviewStatus": "approved",
            "sourceUrl": "https://example.com/location", "capturedAt": "2026-08-19",
            "contentHash": "c" * 64, "changes": {"operatorLocationId": "location-1"},
        }
        document = {"approvals": [approval]}
        coverage.apply_location_metadata_approvals([value], document)
        coverage.apply_location_metadata_approvals([value], document)
        self.assertEqual(len(value["locationEvidence"]), 1)


if __name__ == "__main__":
    unittest.main()
