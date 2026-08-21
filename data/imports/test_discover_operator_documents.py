import threading
import time
import unittest
from unittest.mock import patch

import crawl_official_sources as crawler
import discover_operator_documents as discovery
from discover_operator_documents import document_score, extract_locations, sitemap_urls


class OperatorDocumentDiscoveryTests(unittest.TestCase):
    def test_robots_sitemap_or_default(self):
        self.assertEqual(sitemap_urls("Sitemap: https://gym.example/map.xml", "https://gym.example"), ["https://gym.example/map.xml"])
        self.assertEqual(sitemap_urls("", "https://gym.example"), ["https://gym.example/sitemap.xml"])

    def test_extracts_only_same_origin_research_documents(self):
        xml = "<urlset><url><loc>https://gym.example/pricing</loc></url><url><loc>https://evil.example/pricing</loc></url><url><loc>https://gym.example/blog</loc></url></urlset>"
        nested, documents = extract_locations(xml, "https://gym.example")
        self.assertEqual(nested, [])
        self.assertEqual(documents, ["https://gym.example/pricing"])

    def test_scores_location_slug(self):
        score, ids = document_score("https://gym.example/locations/north-beach/pricing", [{"id": "g1", "name": "Gym", "operatorLocationId": "north-beach"}])
        self.assertEqual(score, 2)
        self.assertEqual(ids, ["g1"])

    def test_operator_name_in_hostname_does_not_match_every_location(self):
        gym = {"id": "sf", "name": "Pilates Addiction SF King Street", "operatorLocationId": ""}
        wrong_score, wrong_ids = document_score("https://pilatesaddiction.com/location/alamo-heights", [gym])
        right_score, right_ids = document_score("https://pilatesaddiction.com/location/sf-king-street", [gym])

        self.assertEqual((wrong_score, wrong_ids), (1, []))
        self.assertEqual((right_score, right_ids), (2, ["sf"]))

    def test_unrelated_chain_location_pages_are_not_review_candidates(self):
        document = {"gyms": [{
            "id": "sf", "name": "Pilates Addiction SF King Street", "operatorLocationId": "",
            "publicationStatus": "publish", "entityKind": "studio", "officialUrl": "https://pilatesaddiction.example/",
        }]}
        sitemap = (
            "<urlset>"
            "<url><loc>https://pilatesaddiction.example/location/alamo-heights</loc></url>"
            "<url><loc>https://pilatesaddiction.example/location/sf-king-street</loc></url>"
            "<url><loc>https://pilatesaddiction.example/pricing</loc></url>"
            "</urlset>"
        )
        with (
            patch.object(discovery, "fetch_robots", return_value=""),
            patch.object(crawler, "fetch_page", return_value={
                "status": "fetched", "url": "https://pilatesaddiction.example/sitemap.xml",
                "robotsStatus": "checked", "html": sitemap,
            }),
            patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0),
        ):
            result = discovery.discover(document, "2026-08-21", 1, workers=1, cache={})

        self.assertEqual(
            [item["url"] for item in result["candidates"]],
            [
                "https://pilatesaddiction.example/location/sf-king-street",
                "https://pilatesaddiction.example/pricing",
            ],
        )

    def test_runs_unrelated_operator_hosts_concurrently(self):
        document = {"gyms": [
            {"id": "a", "name": "Alpha Gym", "publicationStatus": "publish", "entityKind": "gym", "officialUrl": "https://alpha.example/"},
            {"id": "b", "name": "Beta Gym", "publicationStatus": "publish", "entityKind": "studio", "officialUrl": "https://beta.example/"},
        ]}
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_fetch(url, _timeout, _conditional):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            host = "alpha" if "alpha.example" in url else "beta"
            return {
                "status": "fetched", "url": url, "robotsStatus": "checked",
                "html": f"<urlset><url><loc>https://{host}.example/pricing</loc></url></urlset>",
            }

        with (
            patch.object(discovery, "fetch_robots", return_value=""),
            patch.object(crawler, "fetch_page", side_effect=fake_fetch),
            patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0),
        ):
            result = discovery.discover(document, "2026-08-21", 1, workers=2, cache={})

        self.assertEqual(result["_meta"]["workerCount"], 2)
        self.assertEqual(result["_meta"]["candidateUrlCount"], 2)
        self.assertGreaterEqual(peak, 2)

    def test_conditional_cache_preserves_extracted_leads_on_not_modified(self):
        document = {"gyms": [{
            "id": "a", "name": "Alpha Gym", "publicationStatus": "publish", "entityKind": "gym",
            "officialUrl": "https://alpha.example/",
        }]}
        cache = {}
        fetched = {
            "status": "fetched", "url": "https://alpha.example/sitemap.xml", "robotsStatus": "checked",
            "etag": '"v1"', "lastModified": "Wed, 19 Aug 2026 00:00:00 GMT",
            "html": "<urlset><url><loc>https://alpha.example/pricing</loc></url></urlset>",
        }
        with (
            patch.object(discovery, "fetch_robots", return_value=""),
            patch.object(crawler, "fetch_page", return_value=fetched),
            patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0),
        ):
            first = discovery.discover(document, "2026-08-21", 1, workers=1, cache=cache)
        self.assertEqual(first["_meta"]["candidateUrlCount"], 1)
        self.assertEqual(cache["https://alpha.example/sitemap.xml"]["etag"], '"v1"')

        with (
            patch.object(discovery, "fetch_robots", return_value=""),
            patch.object(crawler, "fetch_page", return_value={
                "status": "not-modified", "url": "https://alpha.example/sitemap.xml", "robotsStatus": "checked",
            }),
            patch.object(crawler, "DOMAIN_DELAY_SECONDS", 0),
        ):
            second = discovery.discover(document, "2026-08-22", 1, workers=1, cache=cache)
        self.assertEqual(second["_meta"]["candidateUrlCount"], 1)
        self.assertEqual(second["_meta"]["cacheNotModifiedCount"], 1)


if __name__ == "__main__":
    unittest.main()
