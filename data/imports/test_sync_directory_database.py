import json
import tempfile
import unittest
from pathlib import Path

from sync_directory_database import (
    FixtureValidationError,
    load_fixture,
    prepare_fixture,
    render_fixture,
)


def fixture() -> dict:
    return {
        "_meta": {"importedAt": "2026-08-20T00:00:00Z"},
        "gyms": [
            {
                "id": "gym-one",
                "canonicalLocationId": "operator:one",
                "name": "Gym One",
                "operatorId": "operator",
                "publicationStatus": "publish",
                "pricingStatus": "verified",
                "monthlyPrice": 99,
            }
        ],
    }


class SyncDirectoryDatabaseTests(unittest.TestCase):
    def test_prepares_lossless_record_and_stable_hash(self):
        prepared = prepare_fixture(fixture())
        self.assertEqual(len(prepared.records), 1)
        self.assertEqual(prepared.records[0].canonical_location_id, "operator:one")
        self.assertEqual(json.loads(prepared.records[0].payload_json)["monthlyPrice"], 99)
        self.assertEqual(len(prepared.source_hash), 64)
        self.assertEqual(prepared.source_hash, prepare_fixture(fixture()).source_hash)

    def test_rejects_secret_shaped_fields(self):
        raw = fixture()
        raw["gyms"][0]["client_secret"] = "must-not-ship"
        with self.assertRaises(FixtureValidationError):
            prepare_fixture(raw)

    def test_rejects_duplicate_identity(self):
        raw = fixture()
        raw["gyms"].append(dict(raw["gyms"][0], id="gym-two"))
        with self.assertRaises(FixtureValidationError):
            prepare_fixture(raw)

    def test_render_and_load_round_trip(self):
        raw = fixture()
        rendered = render_fixture(raw["_meta"], raw["gyms"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fixture.json"
            path.write_text(rendered, encoding="utf-8")
            loaded = load_fixture(path)
        self.assertEqual(loaded.source_hash, prepare_fixture(raw).source_hash)


if __name__ == "__main__":
    unittest.main()
