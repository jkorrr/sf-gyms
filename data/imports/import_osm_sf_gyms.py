"""Import named San Francisco fitness facilities from OpenStreetMap via Overpass.

This is deliberately a small, reproducible import rather than a business-site
scraper. OpenStreetMap data is community-maintained, so the app labels these
listings as source data that may need verification by a gym or user.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from classify_venue import classify_venue

ROOT = Path(__file__).resolve().parents[2]
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SF_BBOX = "37.70,-122.53,37.84,-122.35"
PRICE_OVERRIDES_PATH = ROOT / "data" / "imports" / "official-price-overrides.json"
NEIGHBORHOOD_BOXES = (
    ("Bayview–Hunters Point", 37.705, 37.745, -122.405, -122.355),
    ("Visitacion Valley", 37.705, 37.735, -122.445, -122.405),
    ("Excelsior", 37.715, 37.755, -122.475, -122.425),
    ("Outer Mission", 37.735, 37.765, -122.455, -122.405),
    ("Bernal Heights", 37.735, 37.765, -122.445, -122.405),
    ("Mission", 37.745, 37.78, -122.435, -122.405),
    ("Potrero Hill", 37.745, 37.775, -122.415, -122.38),
    ("Sunset", 37.735, 37.79, -122.53, -122.455),
    ("Parkside", 37.735, 37.775, -122.49, -122.44),
    ("Haight–Ashbury", 37.755, 37.785, -122.465, -122.43),
    ("Castro", 37.75, 37.775, -122.45, -122.425),
    ("SOMA", 37.765, 37.795, -122.42, -122.38),
    ("Downtown", 37.775, 37.81, -122.425, -122.385),
    ("Nob Hill", 37.785, 37.81, -122.43, -122.405),
    ("North Beach", 37.79, 37.82, -122.42, -122.39),
    ("Russian Hill", 37.795, 37.82, -122.45, -122.415),
    ("Pacific Heights", 37.785, 37.81, -122.46, -122.425),
    ("Marina", 37.795, 37.825, -122.48, -122.44),
    ("Presidio", 37.78, 37.81, -122.52, -122.45),
    ("Inner Richmond", 37.775, 37.805, -122.51, -122.46),
    ("Outer Richmond", 37.775, 37.81, -122.53, -122.49),
)
QUERY = f"""
[out:json][timeout:120];
(
  nwr["leisure"="fitness_centre"]({SF_BBOX});
  nwr["leisure"="sports_centre"]({SF_BBOX});
  nwr["amenity"="gym"]({SF_BBOX});
  nwr["sport"~"fitness|gymnastics|weightlifting|bodybuilding|crossfit",i]({SF_BBOX});
);
out center tags;
"""


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def safe_url(value: str) -> str:
    return value if re.match(r"^https?://", value, re.IGNORECASE) else ""


def address_from_tags(tags: dict[str, Any]) -> str:
    number = text(tags.get("addr:housenumber"))
    street = text(tags.get("addr:street"))
    city = text(tags.get("addr:city")) or "San Francisco"
    postcode = text(tags.get("addr:postcode"))
    first_line = " ".join(part for part in (number, street) if part)
    return ", ".join(part for part in (first_line, city, postcode) if part) or city


def neighborhood_from_tags(tags: dict[str, Any], latitude: float, longitude: float) -> str:
    tagged = (
        text(tags.get("addr:neighbourhood"))
        or text(tags.get("addr:suburb"))
        or text(tags.get("addr:district"))
    )
    if tagged:
        return tagged
    for name, min_lat, max_lat, min_lon, max_lon in NEIGHBORHOOD_BOXES:
        if min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon:
            return name
    return "San Francisco"


def gym_type(tags: dict[str, Any]) -> str:
    leisure = text(tags.get("leisure")).lower()
    sport = text(tags.get("sport")).lower()
    if "crossfit" in sport:
        return "CrossFit / functional fitness"
    if leisure == "sports_centre":
        return "Sports centre"
    if "gymnastics" in sport:
        return "Gymnastics"
    if "weightlifting" in sport or "bodybuilding" in sport:
        return "Strength gym"
    return "Fitness centre"


def amenities_from_tags(tags: dict[str, Any]) -> list[str]:
    amenities: list[str] = []
    sport = text(tags.get("sport"))
    access = text(tags.get("access")).lower()
    wheelchair = text(tags.get("wheelchair")).lower()
    if sport:
        amenities.append(sport.replace(";", ", "))
    if text(tags.get("opening_hours")):
        amenities.append("Hours listed")
    if text(tags.get("website")) or text(tags.get("contact:website")):
        amenities.append("Website listed")
    if access in {"customers", "members"}:
        amenities.append("Member access")
    if wheelchair in {"yes", "limited"}:
        amenities.append("Wheelchair access")
    if text(tags.get("operator")):
        amenities.append(text(tags["operator"]))
    return list(dict.fromkeys(amenities))[:6]


def element_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    if element.get("type") == "node" and element.get("lat") is not None:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None


def normalize(
    element: dict[str, Any], imported_at: str, price_overrides: list[dict[str, Any]]
) -> dict[str, Any] | None:
    tags = element.get("tags") or {}
    name = text(tags.get("name")) or text(tags.get("official_name"))
    coordinates = element_coordinates(element)
    if not name or coordinates is None:
        return None
    latitude, longitude = coordinates
    city = text(tags.get("addr:city")).casefold()
    if city and city not in {"san francisco", "sf"}:
        return None
    osm_type = text(element.get("type"))
    osm_id = int(element["id"])
    osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    hours = text(tags.get("opening_hours")) or "Hours not listed"
    is_open_247 = "24/7" in hours.replace(" ", "").lower()
    website = safe_url(text(tags.get("contact:website")) or text(tags.get("website")))
    display_address = address_from_tags(tags)
    price = next(
        (override for override in price_overrides if re.search(text(override.get("match")), name, re.IGNORECASE)),
        {},
    )
    record = {
        "id": f"osm-{osm_type}-{osm_id}",
        "name": name,
        "neighborhood": neighborhood_from_tags(tags, latitude, longitude),
        "address": display_address,
        "gymType": gym_type(tags),
        "latitude": round(latitude, 7),
        "longitude": round(longitude, 7),
        "monthlyPrice": price.get("monthlyPrice"),
        "dayPassPrice": price.get("dayPassPrice"),
        "freshness": "verified" if price else "unknown",
        "isOpen247": is_open_247,
        "amenities": amenities_from_tags(tags),
        "description": "OpenStreetMap listing. Verify current pricing and hours with the gym.",
        "hours": hours,
        "websiteUrl": website or osm_url,
        "sourceName": "OpenStreetMap",
        "sourceId": f"{osm_type}/{osm_id}",
        "sourceUrl": osm_url,
        "importedAt": imported_at,
        "priceSource": price.get("priceSource", ""),
        "priceSourceUrl": price.get("priceSourceUrl", ""),
        "priceNote": price.get("priceNote", ""),
        "priceObservedAt": price.get("priceObservedAt", ""),
    }
    record["venueType"] = classify_venue(record)
    return record


def fetch_elements() -> tuple[list[dict[str, Any]], str]:
    payload = urlencode({"data": QUERY}).encode("utf-8")
    request = Request(
        OVERPASS_URL,
        data=payload,
        headers={"User-Agent": "sf-gyms-data-import/0.1 (+https://github.com/jkorrr/sf-gyms)"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed HTTPS endpoint above
        body = json.load(response)
    imported_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")  # noqa: UP017 - supports Python versions without datetime.UTC
    return body.get("elements", []), imported_at


def write_outputs(gyms: list[dict[str, Any]], imported_at: str) -> None:
    document = {
        "_meta": {
            "source": "OpenStreetMap",
            "sourceUrl": "https://www.openstreetmap.org/",
            "queryUrl": OVERPASS_URL,
            "boundingBox": SF_BBOX,
            "importedAt": imported_at,
            "license": "ODbL 1.0",
            "venueTaxonomyVersion": 1,
            "notes": "Named OSM-tagged fitness facilities in the San Francisco bounding box; coverage is not exhaustive.",
        },
        "gyms": gyms,
    }
    output_paths = [
        ROOT / "data" / "imports" / "sf-gyms-osm.json",
        ROOT / "apps" / "web" / "lib" / "sf-gyms-osm.json",
    ]
    for path in output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    try:
        elements, imported_at = fetch_elements()
    except Exception as error:  # pragma: no cover - network failure path
        print(f"Overpass import failed: {error}", file=sys.stderr)
        return 1

    price_overrides = json.loads(PRICE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    gyms: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for element in elements:
        gym = normalize(element, imported_at, price_overrides)
        if gym is None:
            continue
        dedupe_key = (gym["name"].casefold(), round(gym["latitude"], 5), round(gym["longitude"], 5))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        gyms.append(gym)
    gyms.sort(key=lambda gym: (gym["name"].casefold(), gym["address"]))
    write_outputs(gyms, imported_at)
    print(f"Imported {len(gyms)} named San Francisco fitness facilities from OpenStreetMap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
