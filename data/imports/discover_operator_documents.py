"""Discover official pricing/location documents from operator sitemaps.

This monthly, review-only pass requests at most a few sitemap documents per
operator host, honors robots directives through the shared crawler, and emits
candidate URLs.  It does not scrape search-result pages or turn a URL into a
verified fact.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
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
    parsed = urlparse(url)
    # The operator name commonly appears in the hostname. Including it made
    # every location page for a chain look like an exact match for every gym.
    # Only the path/query may supply location identity evidence.
    lowered = re.sub(r"[^a-z0-9]+", " ", f"{parsed.path} {parsed.query}".casefold())
    matches: list[str] = []
    for gym in gyms:
        tokens = [token for token in re.sub(r"[^a-z0-9]+", " ", text(gym.get("name")).casefold()).split() if len(token) >= 4]
        location_id = re.sub(r"[^a-z0-9]+", " ", text(gym.get("operatorLocationId")).casefold()).strip()
        if location_id and location_id in lowered or tokens and sum(token in lowered for token in tokens) >= min(2, len(tokens)):
            matches.append(text(gym.get("id")))
    return (2 if matches else 1), matches


def discover_origin(
    operator_origin: str,
    gyms: list[dict[str, Any]],
    generated_at: str,
    timeout: float,
    cache: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Discover one origin sequentially so a host never sees concurrent requests."""

    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    robots = fetch_robots(operator_origin, timeout)
    # robots.txt and the first sitemap are separate requests to the same host.
    # Keep the per-host cadence conservative even though unrelated hosts run
    # concurrently.
    time.sleep(crawler.DOMAIN_DELAY_SECONDS)
    queue = sitemap_urls(robots, operator_origin)
    seen: set[str] = set()
    discovered_urls: list[str] = []
    while queue and len(seen) < 3:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        cached = cache.get(sitemap, {})
        result = crawler.fetch_page(sitemap, timeout, cached or None)
        status = text(result.get("status"))
        cache_status = "miss"
        nested: list[str] = []
        documents: list[str] = []
        if status == "fetched":
            body = text(result.get("html"))
            nested, documents = extract_locations(body, operator_origin)
            updates[sitemap] = {
                "etag": text(result.get("etag")),
                "lastModified": text(result.get("lastModified")),
                "contentHash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "nested": nested,
                "documents": documents,
                "fetchedAt": generated_at,
            }
            cache_status = "refreshed" if cached else "miss"
        elif status == "not-modified" and cached:
            nested = list(cached.get("nested") or [])
            documents = list(cached.get("documents") or [])
            updates[sitemap] = dict(cached)
            cache_status = "not-modified"
        elif cached:
            # Sitemap URLs are discovery leads only. Reusing a prior extracted
            # lead after a transient failure cannot publish a price, while it
            # prevents one flaky host from erasing the review queue.
            nested = list(cached.get("nested") or [])
            documents = list(cached.get("documents") or [])
            updates[sitemap] = dict(cached)
            cache_status = "stale-fallback"
        attempts.append({
            "operatorOrigin": operator_origin,
            "url": sitemap,
            "status": status,
            "robotsStatus": text(result.get("robotsStatus")),
            "cacheStatus": cache_status,
        })
        queue.extend(value for value in nested if value not in seen)
        discovered_urls.extend(documents)
        time.sleep(crawler.DOMAIN_DELAY_SECONDS)
    for url in list(dict.fromkeys(discovered_urls)):
        score, gym_ids = document_score(url, gyms)
        # A chain sitemap may contain hundreds of unrelated branch pages. Keep
        # location documents only when their path identifies a fixture gym;
        # generic operator-wide pricing/package documents remain useful leads.
        if not gym_ids and not crawler.is_operator_wide_pricing_document(url):
            continue
        candidates.append({
            "operatorOrigin": operator_origin,
            "url": url,
            "candidateType": "exact-location-document" if gym_ids else "operator-document",
            "matchingGymIds": gym_ids,
            "identityScore": score,
            "reviewStatus": "pending",
            "autoApply": False,
        })
    return attempts, candidates, updates


def discover(
    document: dict[str, Any],
    generated_at: str,
    timeout: float,
    limit: int = 0,
    workers: int = 8,
    cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    cache_entries = cache if cache is not None else {}
    worker_count = min(max(1, workers), len(items)) if items else 0
    if worker_count:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(discover_origin, operator_origin, gyms, generated_at, timeout, cache_entries): operator_origin
                for operator_origin, gyms in items
            }
            for future in concurrent.futures.as_completed(futures):
                operator_origin = futures[future]
                try:
                    origin_attempts, origin_candidates, updates = future.result()
                except Exception as error:  # Fail one host closed without losing the city-wide run.
                    attempts.append({
                        "operatorOrigin": operator_origin,
                        "url": operator_origin,
                        "status": "worker-error",
                        "robotsStatus": "unknown",
                        "cacheStatus": "not-used",
                        "error": text(error)[:200],
                    })
                    continue
                attempts.extend(origin_attempts)
                candidates.extend(origin_candidates)
                cache_entries.update(updates)
    attempts.sort(key=lambda item: (item["operatorOrigin"], item["url"]))
    return {
        "_meta": {
            "generatedAt": generated_at,
            "policy": "Official sitemap URL leads only. Every identity and price still requires source review.",
            "operatorHostCount": len(items),
            "workerCount": worker_count,
            "sitemapRequestCount": len(attempts),
            "candidateUrlCount": len(candidates),
            "cacheNotModifiedCount": sum(item.get("cacheStatus") == "not-modified" for item in attempts),
            "cacheFallbackCount": sum(item.get("cacheStatus") == "stale-fallback" for item in attempts),
        },
        "attempts": attempts,
        "candidates": sorted(candidates, key=lambda item: (item["operatorOrigin"], item["url"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    generated_at = args.date or datetime.now(UTC).date().isoformat()
    cache_document = load(CACHE_PATH, {"_meta": {}, "entries": {}})
    cache = cache_document.get("entries") if isinstance(cache_document.get("entries"), dict) else {}
    result = discover(load(SOURCE_PATH, {"gyms": []}), generated_at, args.timeout, args.limit, args.workers, cache)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cache_output = {
        "_meta": {
            "updatedAt": generated_at,
            "policy": "Conditional-request metadata and extracted URL leads only; no raw sitemap or page content is retained.",
            "entryCount": len(cache),
        },
        "entries": {key: cache[key] for key in sorted(cache)},
    }
    CACHE_PATH.write_text(json.dumps(cache_output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["_meta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
