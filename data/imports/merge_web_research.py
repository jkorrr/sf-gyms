"""Merge researched gym records into the reproducible SF directory fixture.

The research files are intentionally separate from the OSM import so that a
price observation can be audited without overwriting the source map data. This
script geocodes only researched addresses that do not already match an OSM
record, rate-limits the public Nominatim endpoint, and never invents a point or
price when a source does not provide one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from classify_venue import classify_all, classify_venue

ROOT = Path(__file__).resolve().parents[2]
OSM_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
WEB_PATH = ROOT / "apps" / "web" / "lib" / "sf-gyms-osm.json"
CACHE_PATH = ROOT / "data" / "imports" / "sf-gym-geocode-cache.json"
RESEARCH_PATHS = (
    ROOT / "data" / "imports" / "sf-gym-web-research-a.json",
    ROOT / "data" / "imports" / "sf-gym-web-research-b.json",
    ROOT / "data" / "imports" / "sf-gym-web-research-c.json",
    ROOT / "data" / "imports" / "sf-gym-web-research-d.json",
    ROOT / "data" / "imports" / "sf-gym-web-research-e.json",
    ROOT / "data" / "imports" / "sf-gym-web-research-f.json",
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SF_BOUNDS = (37.68, 37.86, -122.56, -122.30)
USER_AGENT = "sf-gyms-data-import/0.2 (+https://github.com/jkorrr/sf-gyms)"


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text(value).casefold()).strip()


def name_tokens(value: str) -> set[str]:
    ignored = {"san", "francisco", "sf", "the", "gym", "fitness"}
    return {token for token in normalized(value).split() if token not in ignored}


def distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def in_sf(latitude: float, longitude: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = SF_BOUNDS
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def list_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def geocode_address(address: str, cache: dict[str, Any], last_request: list[float]) -> tuple[float, float] | None:
    key = normalized(address)
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("latitude") is not None:
        point = (float(cached["latitude"]), float(cached["longitude"]))
        return point if in_sf(*point) else None
    if cached == {"status": "not_found"}:
        return None

    wait_for = 1.1 - (time.monotonic() - last_request[0])
    if wait_for > 0:
        time.sleep(wait_for)
    query = urlencode(
        {
            "q": f"{address}, San Francisco, CA",
            "format": "jsonv2",
            "limit": "1",
            "countrycodes": "us",
            "addressdetails": "1",
        }
    )
    request = Request(f"{NOMINATIM_URL}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_request[0] = time.monotonic()
    try:
        with urlopen(request, timeout=25) as response:  # noqa: S310 - fixed HTTPS endpoint above
            results = json.load(response)
    except Exception as error:  # pragma: no cover - depends on public endpoint availability
        print(f"Geocoding failed for {address}: {error}", file=sys.stderr)
        return None
    if not results:
        cache[key] = {"status": "not_found", "query": address}
        save_json(CACHE_PATH, cache)
        return None
    try:
        point = (float(results[0]["lat"]), float(results[0]["lon"]))
    except (KeyError, TypeError, ValueError):
        cache[key] = {"status": "not_found", "query": address}
        save_json(CACHE_PATH, cache)
        return None
    if not in_sf(*point):
        cache[key] = {"status": "outside_sf", "query": address, "latitude": point[0], "longitude": point[1]}
        save_json(CACHE_PATH, cache)
        return None
    cache[key] = {
        "status": "ok",
        "query": address,
        "displayName": results[0].get("display_name", ""),
        "latitude": point[0],
        "longitude": point[1],
    }
    save_json(CACHE_PATH, cache)
    return point


def stable_id(name: str, address: str) -> str:
    digest = hashlib.sha1(f"{normalized(name)}|{normalized(address)}".encode()).hexdigest()[:14]
    return f"web-{digest}"


def is_open_247(hours: str) -> bool:
    compact = normalized(hours).replace(" ", "")
    return "24 7" in normalized(hours) or "24/7" in hours.replace(" ", "") or "247" in compact


def has_price(record: dict[str, Any]) -> bool:
    return any(record.get(field) is not None for field in (
        "monthlyPrice", "annualFee", "dayPassPrice", "enrollmentFee", "initiationFee",
        "annualPrepayPrice", "personalTrainingSessionPrice",
    ))


def research_date(record: dict[str, Any], fallback: str) -> str:
    return text(record.get("priceObservedAt")) or fallback


def enrich(existing: dict[str, Any], record: dict[str, Any], observed: str) -> None:
    existing.setdefault("annualFee", None)
    existing.setdefault("annualFeeNote", "")
    for field in ("monthlyUnlimitedPrice", "annualPrepayPrice", "enrollmentFee", "enrollmentFeeNote", "initiationFee", "initiationFeeNote", "personalTrainingSessionPrice"):
        if record.get(field) is not None:
            existing[field] = record[field]
    if record.get("websiteUrl") and existing.get("websiteUrl", "").startswith("https://www.openstreetmap.org"):
        existing["websiteUrl"] = record["websiteUrl"]
    if existing.get("neighborhood") in {"", "San Francisco"} and record.get("neighborhood"):
        existing["neighborhood"] = record["neighborhood"]
    if existing.get("hours") in {"", "Hours not listed"} and record.get("hours"):
        existing["hours"] = record["hours"]
    existing["amenities"] = list(dict.fromkeys(list_text(existing.get("amenities")) + list_text(record.get("amenities"))))[:8]
    if has_price(record):
        current_date = text(existing.get("priceObservedAt"))
        if not existing.get("priceSource") or observed >= current_date:
            existing["monthlyPrice"] = record.get("monthlyPrice")
            existing["annualFee"] = record.get("annualFee")
            existing["dayPassPrice"] = record.get("dayPassPrice")
            existing["freshness"] = "verified"
            existing["priceSource"] = record.get("sourceName", "Official web research")
            existing["priceSourceUrl"] = record.get("priceSourceUrl") or record.get("websiteUrl", "")
            existing["priceNote"] = record.get("priceNote", "")
            existing["annualFeeNote"] = record.get("annualFeeNote", "")
            existing["priceObservedAt"] = observed


def new_gym(record: dict[str, Any], point: tuple[float, float], imported_at: str, observed: str) -> dict[str, Any]:
    name = text(record.get("name"))
    address = text(record.get("address")) or "San Francisco"
    website = text(record.get("websiteUrl"))
    price_url = text(record.get("priceSourceUrl"))
    gym = {
        "id": stable_id(name, address),
        "name": name,
        "neighborhood": text(record.get("neighborhood")) or "San Francisco",
        "address": address,
        "gymType": text(record.get("gymType")) or "Fitness centre",
        "latitude": round(point[0], 7),
        "longitude": round(point[1], 7),
        "monthlyPrice": record.get("monthlyPrice"),
        "monthlyUnlimitedPrice": record.get("monthlyUnlimitedPrice"),
        "annualFee": record.get("annualFee"),
        "annualFeeNote": text(record.get("annualFeeNote")),
        "annualPrepayPrice": record.get("annualPrepayPrice"),
        "enrollmentFee": record.get("enrollmentFee"),
        "enrollmentFeeNote": text(record.get("enrollmentFeeNote")),
        "initiationFee": record.get("initiationFee"),
        "initiationFeeNote": text(record.get("initiationFeeNote")),
        "personalTrainingSessionPrice": record.get("personalTrainingSessionPrice"),
        "dayPassPrice": record.get("dayPassPrice"),
        "freshness": "verified" if has_price(record) else "unknown",
        "isOpen247": is_open_247(text(record.get("hours"))),
        "amenities": list(dict.fromkeys(list_text(record.get("amenities"))))[:8],
        "description": "Official web research listing. Verify current pricing, terms, and hours before visiting.",
        "hours": text(record.get("hours")) or "Hours not listed",
        "websiteUrl": website or price_url or "https://www.openstreetmap.org/",
        "sourceName": text(record.get("sourceName")) or "Official web research",
        "sourceId": f"web-research/{stable_id(name, address)}",
        "sourceUrl": website or price_url or "https://www.openstreetmap.org/",
        "importedAt": imported_at,
        "priceSource": text(record.get("sourceName")) if has_price(record) else "",
        "priceSourceUrl": price_url,
        "priceNote": text(record.get("priceNote")),
        "priceObservedAt": observed if has_price(record) else "",
    }
    gym["venueType"] = classify_venue(gym)
    return gym


def main() -> int:
    base = load_json(OSM_PATH)
    cache = load_json(CACHE_PATH) if CACHE_PATH.exists() else {}
    research_paths = [path for path in RESEARCH_PATHS if path.exists()]
    research_documents = [load_json(path) for path in research_paths]
    imported_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    gyms = list(base.get("gyms", []))
    address_index = {normalized(gym.get("address", "")): gym for gym in gyms if gym.get("address")}
    last_request = [0.0]
    added = 0
    enriched = 0
    skipped = 0
    geocoded = 0
    for document in research_documents:
        fallback_date = text(document.get("_meta", {}).get("researchDate")) or imported_at[:10]
        for record in document.get("gyms", []):
            name = text(record.get("name"))
            address = text(record.get("address"))
            if not name or not address:
                skipped += 1
                continue
            point: tuple[float, float] | None = None
            if record.get("latitude") is not None and record.get("longitude") is not None:
                candidate = (float(record["latitude"]), float(record["longitude"]))
                point = candidate if in_sf(*candidate) else None
            if point is None:
                point = geocode_address(address, cache, last_request)
                if point is not None:
                    geocoded += 1
            if point is None:
                skipped += 1
                continue

            observed = research_date(record, fallback_date)
            existing = address_index.get(normalized(address))
            if existing is None:
                candidates = [gym for gym in gyms if name_tokens(gym.get("name", "")) == name_tokens(name)]
                existing = next(
                    (
                        gym
                        for gym in candidates
                        if distance_km((float(gym["latitude"]), float(gym["longitude"])), point) <= 0.35
                    ),
                    None,
                )
            if existing is not None:
                enrich(existing, record, observed)
                enriched += 1
                continue

            gym = new_gym(record, point, imported_at, observed)
            gyms.append(gym)
            address_index[normalized(address)] = gym
            added += 1

    for gym in gyms:
        gym.setdefault("annualFee", None)
        gym.setdefault("annualFeeNote", "")

    classify_all(gyms)

    gyms.sort(key=lambda gym: (normalized(gym.get("name", "")), normalized(gym.get("address", ""))))
    metadata = dict(base.get("_meta", {}))
    metadata.update(
        {
            "source": "OpenStreetMap plus official web research supplements",
            "importedAt": imported_at,
            "supplementalSources": [str(path.relative_to(ROOT)).replace("\\", "/") for path in research_paths],
            "venueTaxonomyVersion": 1,
            "supplementalNotes": "Official web research is provenance-backed and may describe starting rates, day passes, promotions, eligibility-limited plans, or free trials. Null means the public source did not publish a safe comparable price. Confirm all rates before joining.",
        }
    )
    output = {"_meta": metadata, "gyms": gyms}
    save_json(OSM_PATH, output)
    save_json(WEB_PATH, output)
    save_json(CACHE_PATH, cache)
    print(json.dumps({"total": len(gyms), "added": added, "enriched": enriched, "geocoded": geocoded, "skipped": skipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
