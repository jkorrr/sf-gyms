"""Discover official pricing/location documents from operator sitemaps.

This monthly, review-only pass requests at most a few sitemap documents per
operator host, honors robots directives through the shared crawler, and emits
candidate URLs.  It does not scrape search-result pages or turn a URL into a
verified fact.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import crawl_official_sources as crawler


ROOT = Path(__file__).resolve().parent
SOURCE_PATH = ROOT / "sf-gyms-osm.json"
OUTPUT_PATH = ROOT / "operator-document-candidates.json"
CACHE_PATH = ROOT / "operator-document-cache.json"
PATH_RE = re.compile(
    r"/(?:pricing|rates?|memberships?|plans?|packages?|passes?|drop-?ins?|buy|join|locations?|studios?|clubs?)(?:/|$|[-_?])",
    re.IGNORECASE,
)
SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(https?://\S+)", re.IGNORECASE | re.MULTILINE)
LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def load(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def sitemap_urls(robots_text: str, operator_origin: str) -> list[str]:
    values = list(dict.fromkeys(SITEMAP_RE.findall(robots_text)))
    return values[:3] or [urljoin(f"{operator_origin}/", "sitemap.xml")]


def extract_locations(xml: str, operator_origin: str) -> tuple[list[str], list[str]]:
    locations = [text(value).replace("&amp;", "&") for value in LOC_RE.findall(xml)]
    same_host = [value for value in locations if origin(value) == operator_origin]
    nested = [value for value in same_host if value.casefold().endswith((".xml", ".xml.gz")) or "sitemap" in urlparse(value).path.casefold()]
    documents = [value for value in same_host if PATH_RE.search(urlparse(value).path)]
    return list(dict.fromkeys(nested))[:3], list(dict.fromkeys(documents))[:500]


def fetch_robots(operator_origin: str, timeout: float) -> str:
    request = Request(
        urljoin(f"{operator_origin}/", "robots.txt"),
        headers={"User-Agent": crawler.USER_AGENT, "Accept": "text/plain"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator origin is from reviewed fixture.
            return response.read(500_000).decode("utf-8", errors="replace")
    except Exception:
        return ""


def document_score(url: str, gyms: list[dict[str, Any]]) -> tuple[int, list[str]]:
    lowered = re.sub(r"[^a-z0-9]+", " ", url.casefold())
    matches: list[str] = []
    for gym in gyms:
        tokens = [token for token in re.sub(r"[^a-z0-9]+", " ", text(gym.get("name")).casefold()).split() if len(token) >= 4]
        location_id = re.sub(r"[^a-z0-9]+", " ", text(gym.get("operatorLocationId")).casefold()).strip()
        if location_id and location_id in lowered or tokens and sum(token in lowered for token in tokens) >= min(2, len(tokens)):
            matches.append(text(gym.get("id")))
    return (2 if matches else 1), matches


def discover(document: dict[str, Any], generated_at: str, timeout: float, limit: int = 0) -> dict[str, Any]:
    by_origin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gym in document.get("gyms", []):
        if gym.get("publicationStatus") != "publish" or gym.get("entityKind") not in {"gym", "studio", "martial-arts"}:
            continue
        operator_origin = origin(text(gym.get("officialUrl")) or text(gym.get("websiteUrl")))
        if operator_origin:
            by_origin[operator_origin].append(gym)
    items = sorted(by_origin.items())[: limit or None]
    candidates: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for operator_origin, gyms in items:
        robots = fetch_robots(operator_origin, timeout)
        queue = sitemap_urls(robots, operator_origin)
        seen: set[str] = set()
        discovered_urls: list[str] = []
        while queue and len(seen) < 3:
            sitemap = queue.pop(0)
            if sitemap in seen:
                continue
            seen.add(sitemap)
            allowed, robots_status = crawler.robots_allowed(sitemap, timeout)
            if not allowed:
                attempts.append({"operatorOrigin": operator_origin, "url": sitemap, "status": "robots-disallowed", "robotsStatus": robots_status})
                continue
            result = crawler.fetch_page(sitemap, timeout, None)
            attempts.append({
                "operatorOrigin": operator_origin,
                "url": sitemap,
                "status": text(result.get("status")),
                "robotsStatus": text(result.get("robotsStatus")),
            })
            if result.get("status") == "fetched":
                nested, documents = extract_locations(text(result.get("html")), operator_origin)
                queue.extend(value for value in nested if value not in seen)
                discovered_urls.extend(documents)
            time.sleep(crawler.DOMAIN_DELAY_SECONDS)
        for url in list(dict.fromkeys(discovered_urls)):
            score, gym_ids = document_score(url, gyms)
            candidates.append({
                "operatorOrigin": operator_origin,
                "url": url,
                "candidateType": "exact-location-document" if gym_ids else "operator-document",
                "matchingGymIds": gym_ids,
                "identityScore": score,
                "reviewStatus": "pending",
                "autoApply": False,
            })
    return {
        "_meta": {
            "generatedAt": generated_at,
            "policy": "Official sitemap URL leads only. Every identity and price still requires source review.",
            "operatorHostCount": len(items),
            "sitemapRequestCount": len(attempts),
            "candidateUrlCount": len(candidates),
        },
        "attempts": attempts,
        "candidates": sorted(candidates, key=lambda item: (item["operatorOrigin"], item["url"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    generated_at = args.date or datetime.now(UTC).date().isoformat()
    result = discover(load(SOURCE_PATH, {"gyms": []}), generated_at, args.timeout, args.limit)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["_meta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
