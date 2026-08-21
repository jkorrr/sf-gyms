"""Merge researched gym records into the reproducible SF directory fixture.

The research files are intentionally separate from the OSM import so that a
price observation can be audited without overwriting the source map data. This
script geocodes only researched addresses that do not already match an OSM
record, rate-limits the public Nominatim endpoint, and never invents a point or
price when a source does not provide one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
RAW_OSM_PATH = ROOT / "data" / "imports" / "sf-gyms-osm-raw.json"
OSM_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
WEB_PATH = ROOT / "apps" / "web" / "lib" / "sf-gyms-osm.json"
CACHE_PATH = ROOT / "data" / "imports" / "sf-gym-geocode-cache.json"
LOCATION_OVERRIDES_PATH = ROOT / "data" / "imports" / "official-location-overrides.json"
IDENTITY_REVIEW_PATH = ROOT / "data" / "imports" / "identity-review.json"
RESEARCH_PATHS = tuple(sorted((ROOT / "data" / "imports").glob("sf-gym-web-research-*.json")))
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SF_BOUNDS = (37.68, 37.86, -122.56, -122.30)
USER_AGENT = "sf-gyms-data-import/0.2 (+https://github.com/jkorrr/sf-gyms)"


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text(value).casefold()).strip()


def canonical_address(value: str) -> str:
    """Return a unit/ZIP-insensitive street key for official-location matching."""

    components = [normalized(part) for part in text(value).split(",") if normalized(part)]
    if not components:
        return ""
    street = components[0]
    replacements = {
        r"\bfirst\b": "1st",
        r"\bsecond\b": "2nd",
        r"\bthird\b": "3rd",
        r"\bfourth\b": "4th",
        r"\bfifth\b": "5th",
        r"\bsixth\b": "6th",
        r"\bseventh\b": "7th",
        r"\beighth\b": "8th",
        r"\bninth\b": "9th",
        r"\btenth\b": "10th",
        r"\bstreet\b": "st",
        r"\bavenue\b": "ave",
        r"\bboulevard\b": "blvd",
        r"\broad\b": "rd",
        r"\bdrive\b": "dr",
        r"\blane\b": "ln",
        r"\bplace\b": "pl",
        r"\bhighway\b": "hwy",
    }
    for pattern, replacement in replacements.items():
        street = re.sub(pattern, replacement, street)
    street = re.sub(r"\b(?:suite|ste|unit|level|floor|fl)\b.*$", "", street).strip()
    return re.sub(r"\s+", " ", street)


def brand_key(value: str) -> str:
    name = normalized(value)
    aliases = (
        ("24 hour fitness", "24-hour-fitness"),
        ("planet fitness", "planet-fitness"),
        ("crunch fitness", "crunch"),
        ("fitness sf", "fitness-sf"),
        ("live fit", "live-fit"),
        ("bay club", "bay-club"),
        ("equinox", "equinox"),
        ("orangetheory", "orangetheory"),
        ("f45", "f45"),
        ("barry s", "barrys"),
        ("solidcore", "solidcore"),
        ("soulcycle", "soulcycle"),
        ("ymca", "ymca"),
        ("mx3", "mx3-fitness"),
        ("flagship", "flagship-training"),
        ("corepower", "corepower-yoga"),
        ("evolve pilates", "evolve-pilates"),
        ("lotusland", "lotusland-yoga"),
    )
    return next((key for alias, key in aliases if alias in name), name)


KNOWN_BRANDS = {
    "24-hour-fitness",
    "planet-fitness",
    "crunch",
    "fitness-sf",
    "live-fit",
    "bay-club",
    "equinox",
    "orangetheory",
    "f45",
    "barrys",
    "solidcore",
    "soulcycle",
    "ymca",
    "mx3-fitness",
    "flagship-training",
    "corepower-yoga",
    "evolve-pilates",
    "lotusland-yoga",
}


def official_domain(value: str) -> str:
    try:
        domain = urlparse(text(value)).netloc.casefold().removeprefix("www.")
    except ValueError:
        return ""
    return "" if domain == "openstreetmap.org" else domain


def operator_identity(gym: dict[str, Any]) -> str:
    brand = brand_key(text(gym.get("name")))
    if brand in KNOWN_BRANDS:
        return brand
    domain = official_domain(text(gym.get("websiteUrl"))) or official_domain(text(gym.get("priceSourceUrl")))
    return domain or brand


def same_operator(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_brand, right_brand = brand_key(text(left.get("name"))), brand_key(text(right.get("name")))
    if left_brand in KNOWN_BRANDS or right_brand in KNOWN_BRANDS:
        return left_brand == right_brand
    left_domain = official_domain(text(left.get("websiteUrl"))) or official_domain(text(left.get("priceSourceUrl")))
    right_domain = official_domain(text(right.get("websiteUrl"))) or official_domain(text(right.get("priceSourceUrl")))
    if left_domain and right_domain:
        return left_domain == right_domain
    left_tokens, right_tokens = name_tokens(text(left.get("name"))), name_tokens(text(right.get("name")))
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap >= 0.8


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
    query_address = f"{canonical_address(address)}, San Francisco, CA"
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("latitude") is not None:
        point = (float(cached["latitude"]), float(cached["longitude"]))
        return point if in_sf(*point) else None
    if isinstance(cached, dict) and cached.get("status") == "not_found" and cached.get("query") == query_address:
        return None

    wait_for = 1.1 - (time.monotonic() - last_request[0])
    if wait_for > 0:
        time.sleep(wait_for)
    query = urlencode(
        {
            "q": query_address,
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
        cache[key] = {"status": "not_found", "query": query_address}
        save_json(CACHE_PATH, cache)
        return None
    try:
        point = (float(results[0]["lat"]), float(results[0]["lon"]))
    except (KeyError, TypeError, ValueError):
        cache[key] = {"status": "not_found", "query": query_address}
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
    digest = hashlib.sha1(f"{normalized(name)}|{normalized(address)}".encode("utf-8")).hexdigest()[:14]
    return f"web-{digest}"


def is_open_247(hours: str) -> bool:
    compact = normalized(hours).replace(" ", "")
    return "24 7" in normalized(hours) or "24/7" in hours.replace(" ", "") or "247" in compact


def has_price(record: dict[str, Any]) -> bool:
    return record.get("monthlyPrice") is not None or record.get("dayPassPrice") is not None


def research_date(record: dict[str, Any], fallback: str) -> str:
    return text(record.get("priceObservedAt")) or fallback


def enrich(existing: dict[str, Any], record: dict[str, Any], observed: str) -> None:
    if brand_key(existing.get("name", "")) == brand_key(record.get("name", "")):
        if len(normalized(record.get("name", ""))) > len(normalized(existing.get("name", ""))):
            existing["name"] = record["name"]
        if len(normalized(record.get("address", ""))) > len(normalized(existing.get("address", ""))):
            existing["address"] = record["address"]
    if record.get("gymType") and existing.get("gymType") in {"", "Fitness centre", "Gym"}:
        existing["gymType"] = record["gymType"]
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
            existing["dayPassPrice"] = record.get("dayPassPrice")
            existing["freshness"] = "verified"
            existing["priceSource"] = record.get("sourceName", "Official web research")
            existing["priceSourceUrl"] = record.get("priceSourceUrl") or record.get("websiteUrl", "")
            existing["priceNote"] = record.get("priceNote", "")
            existing["priceObservedAt"] = observed
            for field in (
                "annualFee",
                "enrollmentFee",
                "initiationFee",
                "processingFee",
                "planName",
                "planScope",
                "billingInterval",
                "billingIntervalPrice",
            ):
                if field in record:
                    existing[field] = record.get(field)


def new_gym(record: dict[str, Any], point: tuple[float, float], imported_at: str, observed: str) -> dict[str, Any]:
    name = text(record.get("name"))
    address = text(record.get("address")) or "San Francisco"
    website = text(record.get("websiteUrl"))
    price_url = text(record.get("priceSourceUrl"))
    return {
        "id": stable_id(name, address),
        "name": name,
        "neighborhood": text(record.get("neighborhood")) or "San Francisco",
        "address": address,
        "gymType": text(record.get("gymType")) or "Fitness centre",
        "latitude": round(point[0], 7),
        "longitude": round(point[1], 7),
        "monthlyPrice": record.get("monthlyPrice"),
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
        "annualFee": record.get("annualFee"),
        "enrollmentFee": record.get("enrollmentFee"),
        "initiationFee": record.get("initiationFee"),
        "processingFee": record.get("processingFee"),
        "planName": text(record.get("planName")),
        "planScope": text(record.get("planScope")),
        "billingInterval": text(record.get("billingInterval")),
        "billingIntervalPrice": record.get("billingIntervalPrice"),
    }


def indexed_gyms(gyms: list[dict[str, Any]], key_fn: Any) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for gym in gyms:
        key = key_fn(gym.get("address", ""))
        if key:
            index.setdefault(key, []).append(gym)
    return index


def append_source_alias(target: dict[str, Any], alias: dict[str, Any], decision: dict[str, Any] | None = None) -> None:
    """Retain a suppressed source identity without copying its stale display fields."""

    target.setdefault("sourceAliases", [])
    alias_summary = {
        "id": text(alias.get("id")),
        "name": text(alias.get("name")),
        "address": text(alias.get("address")),
        "sourceUrl": text(alias.get("sourceUrl")),
    }
    if decision:
        alias_summary["reason"] = text(decision.get("reason"))
        alias_summary["decisionSourceUrl"] = text(decision.get("sourceUrl"))
    if alias_summary["id"] and alias_summary["id"] != target.get("id") and alias_summary not in target["sourceAliases"]:
        target["sourceAliases"].append(alias_summary)


def merge_location_data(target: dict[str, Any], alias: dict[str, Any]) -> None:
    """Merge a reviewed alias into a stable canonical location without losing evidence."""

    append_source_alias(target, alias)

    if len(text(alias.get("name"))) > len(text(target.get("name"))):
        target["name"] = alias["name"]
    if re.search(r"\d", text(alias.get("address"))) and len(text(alias.get("address"))) > len(text(target.get("address"))):
        target["address"] = alias["address"]
    target["amenities"] = list(dict.fromkeys(list_text(target.get("amenities")) + list_text(alias.get("amenities"))))[:12]
    if text(target.get("hours")) in {"", "Hours not listed"} and text(alias.get("hours")) not in {"", "Hours not listed"}:
        target["hours"] = alias["hours"]
    if not official_domain(text(target.get("websiteUrl"))) and official_domain(text(alias.get("websiteUrl"))):
        target["websiteUrl"] = alias["websiteUrl"]

    target_date = text(target.get("priceObservedAt"))
    alias_date = text(alias.get("priceObservedAt"))
    if alias.get("monthlyPrice") is not None and (target.get("monthlyPrice") is None or alias_date >= target_date):
        price_fields = (
            "monthlyPrice", "dayPassPrice", "annualFee", "enrollmentFee", "initiationFee", "processingFee",
            "billingInterval", "billingIntervalPrice", "planName", "planScope", "commitmentType",
            "minimumCommitmentMonths", "priceSource", "priceSourceUrl", "priceNote", "priceObservedAt",
        )
        for field in price_fields:
            if field in alias:
                target[field] = alias[field]


def collapse_known_brand_duplicates(gyms: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse same-operator records at one numeric canonical street address."""

    unique: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    collapsed = 0
    for gym in gyms:
        brand = operator_identity(gym)
        street = canonical_address(gym.get("address", ""))
        key = (brand, street)
        if not brand or not re.search(r"\d", street):
            unique.append(gym)
            continue
        existing = seen.get(key)
        if existing is None:
            seen[key] = gym
            unique.append(gym)
            continue
        if not same_operator(existing, gym):
            unique.append(gym)
            continue
        existing_location_id = text(existing.get("operatorLocationId"))
        gym_location_id = text(gym.get("operatorLocationId"))
        if existing_location_id and gym_location_id and existing_location_id != gym_location_id:
            # One operator may run distinct, separately addressable facilities at
            # the same street address (for example a pool and recreation center).
            # Distinct public location IDs are stronger than address similarity.
            unique.append(gym)
            continue
        collapsed += 1
        if text(existing.get("id")).startswith("web-") and text(gym.get("id")).startswith("osm-"):
            existing_index = unique.index(existing)
            unique[existing_index] = gym
            seen[key] = gym
            merge_location_data(gym, existing)
        else:
            merge_location_data(existing, gym)
    return unique, collapsed


def apply_location_overrides(
    gyms: list[dict[str, Any]], document: dict[str, Any]
) -> tuple[list[dict[str, Any]], int, int]:
    overrides = {item["id"]: item for item in document.get("overrides", []) if item.get("id")}
    by_id = {text(gym.get("id")): gym for gym in gyms}
    merged_ids: set[str] = set()
    for alias_id, override in overrides.items():
        if override.get("action") != "merge":
            continue
        alias = by_id.get(alias_id)
        target = by_id.get(text(override.get("mergeInto")))
        if alias is None or target is None or alias is target:
            continue
        merge_location_data(target, alias)
        merged_ids.add(alias_id)
    for alias_id, override in overrides.items():
        if override.get("action") != "suppress" or not text(override.get("canonicalId")):
            continue
        alias = by_id.get(alias_id)
        target = by_id.get(text(override.get("canonicalId")))
        if alias is not None and target is not None and alias is not target:
            append_source_alias(target, alias, override)
    output: list[dict[str, Any]] = []
    suppressed = 0
    updated = 0
    for gym in gyms:
        if text(gym.get("id")) in merged_ids:
            suppressed += 1
            continue
        override = overrides.get(gym.get("id"))
        if override is None:
            output.append(gym)
            continue
        if override.get("action") == "suppress":
            suppressed += 1
            continue
        if override.get("action") in {"update", "review-hold"}:
            for key, value in override.items():
                if key not in {"id", "action", "reason", "sourceUrl"}:
                    gym[key] = value
            updated += 1
        output.append(gym)
    return output, suppressed, updated


def identity_review_candidates(gyms: list[dict[str, Any]], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Emit close same-operator pairs that require a reviewed merge decision."""

    distinct_pairs = {
        frozenset((text(item.get("leftId")), text(item.get("rightId"))))
        for item in document.get("distinctPairs", [])
    }
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(gyms):
        for right in gyms[index + 1 :]:
            if frozenset((text(left.get("id")), text(right.get("id")))) in distinct_pairs:
                continue
            if not same_operator(left, right):
                continue
            left_street, right_street = canonical_address(left.get("address", "")), canonical_address(right.get("address", ""))
            exact_street = bool(re.search(r"\d", left_street) and left_street == right_street)
            near = False
            try:
                near = distance_km(
                    (float(left["latitude"]), float(left["longitude"])),
                    (float(right["latitude"]), float(right["longitude"])),
                ) <= 0.12
            except (KeyError, TypeError, ValueError):
                pass
            if not exact_street and not near:
                continue
            candidates.append(
                {
                    "leftId": left["id"],
                    "leftName": left["name"],
                    "leftAddress": left.get("address", ""),
                    "rightId": right["id"],
                    "rightName": right["name"],
                    "rightAddress": right.get("address", ""),
                    "operatorIdentity": operator_identity(left),
                    "reason": "same-operator-exact-address" if exact_street else "same-operator-nearby",
                    "publicationStatus": "review-hold",
                }
            )
    return candidates


def replacement_identity_candidates(gyms: list[dict[str, Any]], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Fail closed on an unverified source identity sharing an address with a current operator.

    This never merges different operators. It only generates a review hold for
    the weaker source identity; the current official operator remains publishable.
    Reviewed `suppress`/`merge` overrides and explicit distinct pairs remove the
    candidate on the next immutable rebuild.
    """

    distinct_pairs = {
        frozenset((text(item.get("leftId")), text(item.get("rightId"))))
        for item in document.get("distinctPairs", [])
    }
    decided_ids = {
        text(item.get("id"))
        for item in document.get("overrides", [])
        if item.get("action") in {"suppress", "merge"}
    }
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(gyms):
        for right in gyms[index + 1 :]:
            pair = frozenset((text(left.get("id")), text(right.get("id"))))
            if pair in distinct_pairs or pair & decided_ids:
                continue
            street = canonical_address(left.get("address", ""))
            if not re.search(r"\d", street) or street != canonical_address(right.get("address", "")):
                continue
            if same_operator(left, right):
                continue
            left_official = bool(official_domain(text(left.get("websiteUrl"))) or official_domain(text(left.get("priceSourceUrl"))))
            right_official = bool(official_domain(text(right.get("websiteUrl"))) or official_domain(text(right.get("priceSourceUrl"))))
            if left_official == right_official:
                continue
            weak, current = (right, left) if left_official else (left, right)
            if not text(weak.get("id")).startswith("osm-") and "openstreetmap.org" not in text(weak.get("sourceUrl")):
                continue
            candidates.append(
                {
                    "leftId": weak["id"],
                    "leftName": weak.get("name", ""),
                    "leftAddress": weak.get("address", ""),
                    "rightId": current["id"],
                    "rightName": current.get("name", ""),
                    "rightAddress": current.get("address", ""),
                    "operatorIdentity": "possible-replacement",
                    "reason": "different-operator-exact-address-with-unverified-source-identity",
                    "publicationStatus": "review-hold",
                    "holdIds": [weak["id"]],
                }
            )
    return candidates


def finalize_identity_fields(gyms: list[dict[str, Any]], held_ids: set[str]) -> None:
    for gym in gyms:
        if not official_domain(text(gym.get("websiteUrl"))):
            gym["websiteUrl"] = ""
        gym["officialUrl"] = text(gym.get("officialUrl")) or text(gym.get("websiteUrl"))
        gym["operatorLocationId"] = text(gym.get("operatorLocationId"))
        gym["canonicalLocationId"] = gym["id"]
        gym["operatorId"] = operator_identity(gym)
        gym["canonicalAddress"] = canonical_address(gym.get("address", ""))
        gym.setdefault("sourceAliases", [])
        gym["publicationStatus"] = "review-hold" if gym["id"] in held_ids else "publish"


def address_match(
    record: dict[str, Any],
    exact_index: dict[str, list[dict[str, Any]]],
    street_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    address = text(record.get("address"))
    candidates = exact_index.get(normalized(address), [])
    if not candidates:
        candidates = street_index.get(canonical_address(address), [])
    if not candidates:
        return None
    same_brand = [gym for gym in candidates if same_operator(gym, record)]
    return same_brand[0] if len(same_brand) == 1 else None


def resolve_imported_at(fixed_date: str | None = None) -> str:
    """Return a reproducible import timestamp when a crawl date is pinned."""
    if fixed_date:
        try:
            parsed = datetime.strptime(fixed_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("--date must use YYYY-MM-DD") from exc
        return f"{parsed.date().isoformat()}T00:00:00Z"
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Pin importedAt to YYYY-MM-DDT00:00:00Z for deterministic rebuilds")
    args = parser.parse_args()
    if not RAW_OSM_PATH.exists():
        raise FileNotFoundError(f"Immutable raw input missing: {RAW_OSM_PATH}")
    base = load_json(RAW_OSM_PATH)
    cache = load_json(CACHE_PATH) if CACHE_PATH.exists() else {}
    location_overrides = load_json(LOCATION_OVERRIDES_PATH) if LOCATION_OVERRIDES_PATH.exists() else {"overrides": []}
    research_documents = [load_json(path) for path in RESEARCH_PATHS]
    imported_at = resolve_imported_at(args.date)

    gyms, collapsed = collapse_known_brand_duplicates(list(base.get("gyms", [])))
    address_index = indexed_gyms(gyms, normalized)
    street_index = indexed_gyms(gyms, canonical_address)
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
            observed = research_date(record, fallback_date)
            existing = address_match(record, address_index, street_index)
            if existing is not None:
                enrich(existing, record, observed)
                enriched += 1
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
            address_index.setdefault(normalized(address), []).append(gym)
            street_index.setdefault(canonical_address(address), []).append(gym)
            added += 1

    gyms, suppressed, status_updated = apply_location_overrides(gyms, location_overrides)
    gyms, post_merge_collapsed = collapse_known_brand_duplicates(gyms)
    collapsed += post_merge_collapsed
    identity_review = identity_review_candidates(gyms, location_overrides)
    identity_review.extend(replacement_identity_candidates(gyms, location_overrides))
    identity_review.extend(
        {
            "leftId": text(item.get("id")),
            "leftName": text(next((gym.get("name") for gym in gyms if gym.get("id") == item.get("id")), "")),
            "leftAddress": text(next((gym.get("address") for gym in gyms if gym.get("id") == item.get("id")), "")),
            "rightId": "",
            "rightName": "",
            "rightAddress": "",
            "operatorIdentity": "unresolved",
            "reason": text(item.get("reason")) or "explicit-identity-review",
            "publicationStatus": "review-hold",
            "sourceUrl": text(item.get("sourceUrl")),
        }
        for item in location_overrides.get("overrides", [])
        if item.get("action") == "review-hold"
    )
    held_ids: set[str] = set()
    for item in identity_review:
        explicit = [text(value) for value in item.get("holdIds", []) if text(value)]
        held_ids.update(explicit or [text(item.get("leftId")), text(item.get("rightId"))])
    held_ids.discard("")
    held_ids.update(
        text(item.get("id"))
        for item in location_overrides.get("overrides", [])
        if item.get("action") == "review-hold"
    )
    finalize_identity_fields(gyms, held_ids)
    gyms.sort(key=lambda gym: (normalized(gym.get("name", "")), normalized(gym.get("address", ""))))
    metadata = dict(base.get("_meta", {}))
    metadata.update(
        {
            "source": "OpenStreetMap plus official web research supplements",
            "immutableBase": str(RAW_OSM_PATH.relative_to(ROOT)).replace("\\", "/"),
            "importedAt": imported_at,
            "supplementalSources": [str(path.relative_to(ROOT)).replace("\\", "/") for path in RESEARCH_PATHS],
            "locationOverrides": str(LOCATION_OVERRIDES_PATH.relative_to(ROOT)).replace("\\", "/"),
            "planSelectionPolicy": "Lowest publicly listed ongoing recurring plan that permits ordinary use of the named location; excludes introductory, eligibility-limited, prepaid, and fixed-term offers when a clean month-to-month option exists. Class studios use the smallest ongoing monthly class plan. Non-monthly billing is normalized with its original cadence retained. Mandatory fees remain separate.",
            "supplementalNotes": "Official web research is provenance-backed. Null means the public source did not publish a safely comparable value. Promotions and free trials are documented but not substituted for neutral ongoing or standard drop-in prices. Confirm all rates before joining.",
        }
    )
    output = {"_meta": metadata, "gyms": gyms}
    save_json(OSM_PATH, output)
    save_json(WEB_PATH, output)
    save_json(CACHE_PATH, cache)
    save_json(
        IDENTITY_REVIEW_PATH,
        {
            "generatedAt": imported_at[:10],
            "policy": "Same-operator nearby identities fail closed until a reviewed merge decision is committed.",
            "records": identity_review,
        },
    )
    print(
        json.dumps(
            {
                "total": len(gyms),
                "added": added,
                "enriched": enriched,
                "geocoded": geocoded,
                "skipped": skipped,
                "duplicatesCollapsed": collapsed,
                "locationsSuppressed": suppressed,
                "locationsUpdated": status_updated,
                "identityReviewHolds": len(identity_review),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
