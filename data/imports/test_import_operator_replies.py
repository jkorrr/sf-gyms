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


if __name__ == "__main__":
    unittest.main()
