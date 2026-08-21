from __future__ import annotations

import unittest
from datetime import datetime

import audit_reported_evidence as audit


class ReportedEvidenceAuditTests(unittest.TestCase):
    def test_audit_detects_visible_amount_without_retaining_page_text(self) -> None:
        report = {"id": "r1", "gymId": "g1", "amount": 25, "publishedAt": "2026-04-15", "sourceUrl": "https://example.com/report"}
        result = {"status": "fetched", "robotsStatus": "checked", "html": "<html><body>Drop in was $25.</body></html>"}
        inspected = audit.inspect_report(report, result, {}, datetime(2026, 8, 18))
        self.assertTrue(inspected["amountStillVisible"])
        self.assertFalse(inspected["requiresReview"])
        self.assertNotIn("html", inspected)


if __name__ == "__main__":
    unittest.main()
