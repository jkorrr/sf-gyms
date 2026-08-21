import unittest

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


if __name__ == "__main__":
    unittest.main()
