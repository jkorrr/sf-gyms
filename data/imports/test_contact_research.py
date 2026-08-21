from __future__ import annotations

import unittest

import contact_research as contact


class ContactResearchTests(unittest.TestCase):
    def safe_form(self) -> dict[str, object]:
        return {
            "formIndex": 0,
            "url": "https://gym.example/pricing",
            "action": "https://gym.example/pricing",
            "method": "POST",
            "formText": "Ask about current membership pricing",
            "submitLabel": "Request pricing",
            "captcha": False,
            "fields": [
                {"fieldIndex": 0, "tag": "input", "type": "text", "name": "name", "label": "Name", "required": True},
                {"fieldIndex": 1, "tag": "input", "type": "email", "name": "email", "label": "Email", "required": True},
                {"fieldIndex": 2, "tag": "input", "type": "tel", "name": "phone", "label": "Phone", "required": False},
                {"fieldIndex": 3, "tag": "textarea", "type": "textarea", "name": "message", "label": "Question", "required": True},
                {"fieldIndex": 4, "tag": "button", "type": "submit", "name": "", "label": "Request pricing", "required": False},
            ],
        }

    def test_safe_pricing_form_requires_exact_domain_approval(self) -> None:
        form = contact.evaluate_form_policy(self.safe_form())
        self.assertEqual(form["submissionStatus"], "eligible-after-domain-approval")
        approval = {
            "domain": form["domain"],
            "termsHash": form["termsHash"],
            "actionDomains": [form["actionDomain"]],
            "approvedAt": "2026-08-19T00:00:00+00:00",
        }
        self.assertTrue(contact.approval_valid(approval, form))
        self.assertFalse(contact.approval_valid({**approval, "termsHash": "changed"}, form))

    def test_required_split_name_and_phone_consent_fail_closed(self) -> None:
        raw = self.safe_form()
        raw["formText"] = "By submitting you agree to receive automated phone calls and text messages."
        raw["fields"] = [
            {"fieldIndex": 0, "tag": "input", "type": "text", "name": "first_name", "label": "First name", "required": True},
            {"fieldIndex": 1, "tag": "input", "type": "text", "name": "last_name", "label": "Last name", "required": True},
            {"fieldIndex": 2, "tag": "input", "type": "email", "name": "email", "label": "Email", "required": True},
            {"fieldIndex": 3, "tag": "input", "type": "tel", "name": "phone", "label": "Phone", "required": True},
            {"fieldIndex": 4, "tag": "textarea", "type": "textarea", "name": "message", "label": "Message", "required": True},
            {"fieldIndex": 5, "tag": "input", "type": "checkbox", "name": "sms", "label": "I agree to automated texts", "required": True},
        ]
        form = contact.evaluate_form_policy(raw)
        self.assertIn("required-split-name-fields", form["blockers"])
        self.assertIn("required-phone-marketing-consent", form["blockers"])
        self.assertIn("phone-field-with-call-or-text-consent", form["blockers"])

    def test_account_payment_and_captcha_forms_are_never_eligible(self) -> None:
        raw = self.safe_form()
        raw["captcha"] = True
        raw["submitLabel"] = "Create account"
        raw["fields"].append(
            {"fieldIndex": 5, "tag": "input", "type": "password", "name": "password", "label": "Password", "required": True}
        )
        form = contact.evaluate_form_policy(raw)
        self.assertEqual(form["submissionStatus"], "blocked")
        self.assertIn("captcha-or-human-challenge", form["blockers"])
        self.assertIn("account-or-authentication-form", form["blockers"])

    def test_simple_math_challenge_is_treated_as_captcha_not_unknown_data(self) -> None:
        raw = self.safe_form()
        raw["fields"].append({
            "fieldIndex": 5, "tag": "input", "type": "text", "name": "anti_spam",
            "label": "What is 2 + 7?", "required": True,
        })
        form = contact.evaluate_form_policy(raw)
        challenge = next(field for field in form["fields"] if field["name"] == "anti_spam")
        self.assertEqual(challenge["category"], "human-challenge")
        self.assertIn("captcha-or-human-challenge", form["blockers"])
        self.assertNotIn("unknown-required-field", form["blockers"])

    def test_contact_values_use_plus_addressing_without_hardcoded_identity(self) -> None:
        self.assertEqual(contact.plus_address("research@example.com"), "research+sfgyms@example.com")
        self.assertEqual(contact.plus_address("research+gym@example.com"), "research+gym@example.com")
        self.assertEqual(contact.plus_address("invalid"), "")

    def test_redaction_removes_email_phone_and_explicit_secrets(self) -> None:
        output = contact.redact_sensitive(
            "Thanks Alex. Reply to alex@example.com or call 415-555-1212.", ["Alex"]
        )
        self.assertNotIn("Alex", output)
        self.assertNotIn("alex@example.com", output)
        self.assertNotIn("415-555-1212", output)

    def test_contact_discovery_includes_estimates_that_can_be_replaced_by_operator_reply(self) -> None:
        base = {
            "publicationStatus": "publish",
            "recordStatus": "open",
            "entityKind": "martial-arts",
            "websiteUrl": "https://gym.example/contact",
            "operatorKey": "gym.example",
        }
        gyms = contact.candidate_gyms({"gyms": [
            {**base, "id": "estimated", "name": "Estimated Dojo", "pricingStatus": "estimated", "pricingAccess": "form-required"},
            {**base, "id": "verified", "name": "Verified Dojo", "pricingStatus": "verified", "pricingAccess": "public"},
            {**base, "id": "estimated-public", "name": "Public Estimate", "pricingStatus": "estimated", "pricingAccess": "public"},
        ]})
        self.assertEqual([gym["id"] for gym in gyms], ["estimated"])

    def test_report_separates_current_pending_and_stale_form_records(self) -> None:
        records = [
            {"gymId": "current", "status": "scanned", "forms": []},
            {"gymId": "stale", "status": "scanned", "forms": []},
        ]
        candidates = [
            {"id": "current", "pricingStatus": "gated"},
            {"id": "pending", "pricingStatus": "estimated"},
        ]
        report = contact.build_discovery_report(records, "2026-08-21", candidates)
        self.assertEqual(report["currentCandidateGymCount"], 2)
        self.assertEqual(report["candidateGymsScanned"], 1)
        self.assertEqual(report["candidateGymsPendingScan"], 1)
        self.assertEqual(report["pendingScanGymIds"], ["pending"])
        self.assertEqual(report["staleManifestRecordCount"], 1)
        self.assertEqual(report["candidatePricingStatusCounts"], {"estimated": 1, "gated": 1})


if __name__ == "__main__":
    unittest.main()
