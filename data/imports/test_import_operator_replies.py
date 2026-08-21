from __future__ import annotations

import unittest
from email.message import EmailMessage

import import_operator_replies as replies


class OperatorReplyImportTests(unittest.TestCase):
    def message_bytes(self, body: str, subject: str = "Re: [sf-gyms:gym-1] pricing") -> bytes:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = "operator@example.com"
        message["To"] = "research@example.com"
        message["Date"] = "Wed, 19 Aug 2026 12:00:00 -0700"
        message.set_content(body)
        return message.as_bytes()

    def test_import_keeps_only_sanitized_price_candidates_and_hashes(self) -> None:
        raw = self.message_bytes(
            "Hi Alex, our Standard plan is $120 per month. Email alex@example.com or call 415-555-1212."
        )
        result = replies.parse_message(raw, {"gym-1"})
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["gymId"], "gym-1")
        self.assertEqual(result["priceCandidates"][0]["amount"], 120)
        serialized = str(result)
        self.assertNotIn("alex@example.com", serialized)
        self.assertNotIn("415-555-1212", serialized)
        self.assertNotIn("Hi Alex", serialized)
        self.assertTrue(result["evidenceId"].startswith("sha256:"))

    def test_confidential_reply_is_flagged_and_never_approved_automatically(self) -> None:
        result = replies.parse_message(
            self.message_bytes("This is a private quote and not for publication: $180/month."), {"gym-1"}
        )
        assert result is not None
        self.assertTrue(result["confidential"])
        self.assertEqual(result["reviewStatus"], "pending")

    def test_unknown_or_missing_gym_tag_is_skipped(self) -> None:
        raw = self.message_bytes("Membership is $100/month.", subject="Pricing reply")
        self.assertIsNone(replies.parse_message(raw, {"gym-1"}))
        self.assertIsNone(replies.parse_message(raw, {"gym-1"}, "other"))

    def test_structured_reply_keeps_allowance_commitment_fees_dropin_and_effective_date(self) -> None:
        result = replies.parse_message(self.message_bytes(
            "Standard Membership is $120 per month for 4 classes per month, with a 3-month minimum.\n"
            "The enrollment fee is $25 and the annual fee is $49.\n"
            "A single class is $35 per visit. Rates took effect August 1, 2026."
        ), {"gym-1"})
        assert result is not None
        recurring = next(item for item in result["priceCandidates"] if item["productType"] == "monthly")
        drop_in = next(item for item in result["priceCandidates"] if item["productType"] == "drop-in")
        self.assertEqual(recurring["amount"], 120)
        self.assertEqual(recurring["classAllowance"]["count"], 4)
        self.assertEqual(recurring["commitment"], {"type": "fixed-term", "minimumMonths": 3})
        self.assertEqual([(fee["type"], fee["amount"]) for fee in recurring["fees"]], [("enrollment", 25), ("annual", 49)])
        self.assertEqual(drop_in["amount"], 35)
        self.assertEqual(result["effectiveDate"], "2026-08-01")

    def test_ranges_are_not_misrepresented_as_exact_prices(self) -> None:
        result = replies.parse_message(
            self.message_bytes("One-on-one training costs $150–$250 per session depending on coach."), {"gym-1"}
        )
        assert result is not None
        self.assertEqual(result["priceCandidates"], [])
        self.assertEqual(result["rangeCandidates"][0]["low"], 150)
        self.assertEqual(result["rangeCandidates"][0]["high"], 250)
        self.assertEqual(result["rangeCandidates"][0]["cadence"], "visit")

    def test_multiple_recurring_plans_do_not_inherit_unlinked_fees(self) -> None:
        result = replies.parse_message(self.message_bytes(
            "Basic plan is $100 per month.\nUnlimited plan is $180 per month.\nEnrollment fee is $40."
        ), {"gym-1"})
        assert result is not None
        recurring = [item for item in result["priceCandidates"] if item["productType"] == "monthly"]
        self.assertEqual(len(recurring), 2)
        self.assertTrue(all(item["fees"] == [] for item in recurring))
        self.assertEqual(result["feeCandidates"][0]["amount"], 40)

    def test_quoted_thread_prices_are_ignored(self) -> None:
        result = replies.parse_message(self.message_bytes(
            "Our current plan is $125 per month.\n\nOn Tue, Aug 18, 2026 at 9:00 AM Research wrote:\n"
            "> An old directory listed $75 per month."
        ), {"gym-1"})
        assert result is not None
        self.assertEqual([item["amount"] for item in result["priceCandidates"]], [125])

    def test_no_plan_and_custom_quote_statements_are_preserved(self) -> None:
        result = replies.parse_message(self.message_bytes(
            "We do not offer a standard monthly membership. Pricing depends on the trainer and program."
        ), {"gym-1"})
        assert result is not None
        self.assertEqual(result["status"], "operator-statement-found")
        self.assertEqual(result["operatorStatements"], [
            "no-standard-plan-or-drop-in-offered", "custom-or-personalized-pricing",
        ])


if __name__ == "__main__":
    unittest.main()
