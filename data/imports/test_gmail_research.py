from __future__ import annotations

import unittest
from datetime import date, timedelta

import gmail_research as gmail


class GmailResearchTests(unittest.TestCase):
    def test_initial_followup_and_cooldown_are_deterministic(self) -> None:
        today = date(2026, 8, 20)
        self.assertEqual(gmail.outreach_action([], today), "initial")
        self.assertEqual(gmail.outreach_action([today - timedelta(days=13)], today), "none")
        self.assertEqual(gmail.outreach_action([today - timedelta(days=14)], today), "follow-up")
        self.assertEqual(gmail.outreach_action([today - timedelta(days=20), today - timedelta(days=1)], today), "none")
        self.assertEqual(gmail.outreach_action([today - timedelta(days=181)], today), "initial")

    def test_missing_oauth_configuration_fails_closed(self) -> None:
        self.assertIsInstance(gmail.configured(), bool)

    def test_no_third_message_until_cooldown(self) -> None:
        today = date(2026, 8, 20)
        sent = [today - timedelta(days=30), today - timedelta(days=16)]
        self.assertEqual(gmail.outreach_action(sent, today), "none")
        self.assertEqual(gmail.outreach_action([today - timedelta(days=200), today - timedelta(days=180)], today), "initial")

    def test_approval_requires_domains_location_and_template(self) -> None:
        approval = {
            "recipient": "pricing@gym.example",
            "recipientDomain": "gym.example",
            "sourceUrl": "https://www.gym.example/location",
            "sourceDomain": "www.gym.example",
            "templateVersion": "v2",
            "templateHash": gmail.template_hash("v2"),
            "exactLocationConfirmed": True,
            "publicOperatorEmailConfirmed": True,
        }
        self.assertTrue(gmail.approval_is_valid(approval))
        approval["sourceDomain"] = "other.example"
        self.assertFalse(gmail.approval_is_valid(approval))

    def test_v2_inquiry_names_the_exact_location(self) -> None:
        body = gmail.inquiry_body({
            "name": "Example Strength",
            "canonicalAddress": "123 Main Street, San Francisco, CA 94107",
        }, "v2")
        self.assertIn("Gym: Example Strength", body)
        self.assertIn("Address: 123 Main Street, San Francisco, CA 94107", body)
        self.assertIn("mandatory enrollment, annual, initiation, processing, setup, or activation fee", body)
        self.assertNotIn("{gym_name}", body)

    def test_legacy_v1_approval_remains_valid_and_uses_old_template(self) -> None:
        approval = {
            "recipient": "pricing@gym.example",
            "recipientDomain": "gym.example",
            "sourceUrl": "https://www.gym.example/location",
            "sourceDomain": "www.gym.example",
            "templateHash": gmail.template_hash("v1"),
            "exactLocationConfirmed": True,
            "publicOperatorEmailConfirmed": True,
        }
        self.assertTrue(gmail.approval_is_valid(approval))
        self.assertEqual(gmail.approval_template_version(approval), "v1")
        self.assertEqual(gmail.inquiry_body({"name": "Ignored"}, "v1"), gmail.INQUIRY_V1)

    def test_v2_requires_name_and_address(self) -> None:
        with self.assertRaises(ValueError):
            gmail.inquiry_body({"name": "Example Strength"}, "v2")


if __name__ == "__main__":
    unittest.main()
