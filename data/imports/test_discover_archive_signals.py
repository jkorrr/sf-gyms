import unittest

from discover_archive_signals import common_crawl_query, parse_common_crawl, parse_wayback, wayback_query


class ArchiveSignalTests(unittest.TestCase):
    def test_queries_are_scoped_to_exact_url(self):
        self.assertIn("url=https%3A%2F%2Fgym.example%2F", wayback_query("https://gym.example/"))
        self.assertIn("url=https%3A%2F%2Fgym.example%2F", common_crawl_query("CC-MAIN-TEST", "https://gym.example/"))

    def test_archive_payloads_are_reduced_to_metadata(self):
        wayback = parse_wayback([["timestamp", "original", "statuscode", "digest"], ["20260101", "https://gym.example", "200", "ABC"]])
        self.assertEqual(wayback["contentDigest"], "ABC")
        common = parse_common_crawl('{"timestamp":"20260101","url":"https://gym.example","status":"200","digest":"DEF"}')
        self.assertEqual(common["contentDigest"], "DEF")


if __name__ == "__main__":
    unittest.main()
