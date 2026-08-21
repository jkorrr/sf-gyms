"""Build a free-source identity/status review queue without scraping search results.

The adapter queries San Francisco's public Socrata business-registration API for
records that still lack an operator URL. Matches are evidence candidates only;
they never close, suppress, rename, or price a listing automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "data" / "imports" / "cost-coverage-review.json"
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "public-source-discovery-observations.json"
CACHE_PATH = ROOT / "data" / "imports" / "public-source-discovery-cache.json"
MANUAL_SEARCH_PATH = ROOT / "data" / "imports" / "manual-source-search.json"
DATA_SF_ENDPOINT = "https://data.sfgov.org/resource/g8m3-pdis.json"
DATA_SF_DATASET_URL = "https://data.sfgov.org/Economy-and-Community/Registered-Business-Locations-San-Francisco/g8m3-pdis/about_data"
USER_AGENT = "sf-gyms-public-research/1.0 (+https://github.com/jkorrr/sf-gyms)"
GENERIC_NAMES = {"gym", "fitness center", "fitness centre", "hot yoga", "body curl", "leg stretch", "hand walk", "hop kick", "vault bar"}


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text(value).casefold()).strip()


def address_number(value: Any) -> str:
    match = re.search(r"\b(\d{1,6})\b", text(value))
    return match.group(1) if match else ""


def identity_score(listing: dict[str, Any], business: dict[str, Any]) -> float:
    left = normalized(listing.get("name"))
    right = normalized(business.get("dba_name"))
    if not left or not right:
        return 0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_tokens, right_tokens = set(left.split()), set(right.split())
    token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens | right_tokens else 0
    name_score = max(sequence, token_score)
    listing_number = address_number(listing.get("address"))
    business_number = address_number(business.get("full_business_address"))
    if listing_number:
        address_score = 1.0 if listing_number == business_number else 0.0
        return round(name_score * 0.72 + address_score * 0.28, 4)
    return round(name_score * 0.85, 4)


def parse_date(value: Any) -> date | None:
    raw = text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def status_signal(business: dict[str, Any], today: date) -> str:
    admin_closed = normalized(business.get("administratively_closed")) in {"true", "yes", "y", "1"}
    end_dates = [parse_date(business.get(field)) for field in ("location_end_date", "dba_end_date")]
    ended = any(value is not None and value <= today for value in end_dates)
    if admin_closed or ended:
        return "closed-signal"
    if any(value is not None and value > today for value in end_dates):
        return "active-signal"
    return "registered-signal"


def query_url(name: str, limit: int = 25) -> str:
    params = {
        "$select": "uniqueid,dba_name,full_business_address,business_zip,dba_start_date,dba_end_date,location_start_date,location_end_date,administratively_closed",
        "$q": name,
        "$limit": str(limit),
    }
    return f"{DATA_SF_ENDPOINT}?{urlencode(params)}"


def fetch_json(url: str, timeout: float) -> tuple[str, list[dict[str, Any]]]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed official public API endpoint.
            return "fetched", json.loads(response.read(2_000_000).decode("utf-8"))
    except HTTPError as error:
        return f"http-{error.code}", []
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return "network-error", []


def research_listing(listing: dict[str, Any], rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    ranked = sorted(((identity_score(listing, row), row) for row in rows), key=lambda item: item[0], reverse=True)
    matches = []
    for score, row in ranked[:5]:
        if score < 0.45:
            continue
        matches.append(
            {
                "score": score,
                "dbaName": text(row.get("dba_name")),
                "address": text(row.get("full_business_address")),
                "postalCode": text(row.get("business_zip")),
                "businessStartDate": text(row.get("dba_start_date")),
                "businessEndDate": text(row.get("dba_end_date")),
                "locationStartDate": text(row.get("location_start_date")),
                "locationEndDate": text(row.get("location_end_date")),
                "administrativelyClosed": text(row.get("administratively_closed")),
                "statusSignal": status_signal(row, today),
                "sourceRecordId": text(row.get("uniqueid")),
            }
        )
    if not matches:
        disposition = "no-match"
    elif matches[0]["score"] >= 0.78 and (len(matches) == 1 or matches[0]["score"] - matches[1]["score"] >= 0.08):
        disposition = "strong-match-review"
    else:
        disposition = "ambiguous-match-review"
    return {
        "gymId": listing["id"],
        "name": listing["name"],
        "address": listing.get("address", ""),
        "query": listing["name"],
        "disposition": disposition,
        "matches": matches,
        "sourceUrl": DATA_SF_DATASET_URL,
        "autoApply": False,
    }


def manual_search_record(listing: dict[str, Any], gym: dict[str, Any], operator_urls: dict[str, list[str]]) -> dict[str, Any]:
    name = text(listing.get("name"))
    address = text(listing.get("address"))
    operator = text(gym.get("operatorKey"))
    street = re.sub(r",?\s*San Francisco.*$", "", address, flags=re.IGNORECASE).strip()
    queries = [
        f'"{name}" "{street}" official',
        f'"{name}" San Francisco membership pricing',
        f'"{name}" San Francisco classes rates',
    ]
    return {
        "gymId": listing["id"],
        "name": name,
        "address": address,
        "likelyOperator": operator,
        "queries": queries,
        "sameOperatorOfficialUrls": operator_urls.get(operator, [])[:8],
        "reviewInstructions": "Locate the operator-owned location or pricing page. Aggregators may be leads only; approve no fact without an official page or linked operator-owned public storefront.",
    }


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--date", help="Override research date")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    today = datetime.fromisoformat(args.date).date() if args.date else datetime.now(UTC).date()
    review = json.loads(REVIEW_PATH.read_text(encoding="utf-8")) if REVIEW_PATH.exists() else {"records": []}
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8")) if SOURCE_PATH.exists() else {"gyms": []}
    gyms_by_id = {text(gym.get("id")): gym for gym in source.get("gyms", [])}
    operator_urls: dict[str, list[str]] = {}
    for gym in source.get("gyms", []):
        operator = text(gym.get("operatorKey"))
        url = text(gym.get("websiteUrl"))
        if operator and url and not url.startswith("https://www.openstreetmap.org"):
            operator_urls.setdefault(operator, [])
            if url not in operator_urls[operator]:
                operator_urls[operator].append(url)
    listings = [item for item in review.get("records", []) if item.get("discoveryStatus") == "needs-official-site"]
    if args.limit:
        listings = listings[: args.limit]
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    observations = []
    request_count = 0
    for listing in listings:
        name = text(listing.get("name"))
        key = normalized(name)
        if key in GENERIC_NAMES or len(key) < 4:
            observations.append({**research_listing(listing, [], today), "fetchStatus": "skipped-generic-name"})
            continue
        cached = cache.get(key, {})
        fetched_at = parse_date(cached.get("fetchedAt"))
        if not args.refresh and fetched_at and today - fetched_at <= timedelta(days=30):
            status, rows = "cached", cached.get("rows", [])
        else:
            status, rows = fetch_json(query_url(name), args.timeout)
            request_count += 1
            if status == "fetched":
                cache[key] = {"fetchedAt": today.isoformat(), "rows": rows}
            time.sleep(0.2)
        observations.append({**research_listing(listing, rows, today), "fetchStatus": status})
    output = {
        "generatedAt": today.isoformat(),
        "source": DATA_SF_DATASET_URL,
        "policy": "Evidence candidates only; no automatic status, suppression, identity, or price changes.",
        "attemptedListings": len(listings),
        "requests": request_count,
        "dispositionCounts": {
            key: sum(item["disposition"] == key for item in observations)
            for key in ("strong-match-review", "ambiguous-match-review", "no-match")
        },
        "observations": observations,
    }
    save_json(CACHE_PATH, cache)
    save_json(OBSERVATIONS_PATH, output)
    manual_records = [manual_search_record(listing, gyms_by_id.get(text(listing.get("id")), {}), operator_urls) for listing in listings]
    save_json(
        MANUAL_SEARCH_PATH,
        {
            "generatedAt": today.isoformat(),
            "policy": "Human search packet only. Search-result pages are never scraped and leads never become verified facts without official evidence.",
            "recordCount": len(manual_records),
            "records": manual_records,
        },
    )
    print(json.dumps({key: output[key] for key in ("attemptedListings", "requests", "dispositionCounts")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
