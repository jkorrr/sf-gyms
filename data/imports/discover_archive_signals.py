"""Collect archive/index status signals for failed official operator URLs.

Archive captures can support manual closure, relocation, and historical-domain
research, but they are never current price evidence.  The output is review-only
and stores capture metadata rather than archived page content.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ATTEMPTS_PATH = ROOT / "official-crawl-attempts.json"
OUTPUT_PATH = ROOT / "archive-status-signals.json"
COLLECTIONS_URL = "https://index.commoncrawl.org/collinfo.json"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
USER_AGENT = "sf-gyms-public-research/1.0 (+https://github.com/jkorrr/sf-gyms)"
FAILED = {"network-error", "http-404", "http-410", "official-domain-parked", "official-domain-disconnected"}


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def fetch_json(url: str, timeout: float) -> Any:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public archive/index endpoints.
        return json.loads(response.read(2_000_000).decode("utf-8"))


def wayback_query(url: str) -> str:
    return f"{CDX_URL}?{urlencode({'url': url, 'output': 'json', 'filter': 'statuscode:200', 'fl': 'timestamp,original,statuscode,digest', 'limit': '1', 'from': '2024'})}"


def common_crawl_query(index_id: str, url: str) -> str:
    return f"https://index.commoncrawl.org/{index_id}-index?{urlencode({'url': url, 'output': 'json', 'filter': 'status:200', 'limit': '1'})}"


def parse_wayback(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return None
    row = payload[1]
    return {
        "captureTimestamp": text(row[0]) if len(row) > 0 else "",
        "capturedUrl": text(row[1]) if len(row) > 1 else "",
        "status": text(row[2]) if len(row) > 2 else "",
        "contentDigest": text(row[3]) if len(row) > 3 else "",
    }


def parse_common_crawl(payload: str) -> dict[str, Any] | None:
    line = next((value for value in payload.splitlines() if value.strip()), "")
    if not line:
        return None
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        return None
    return {
        "captureTimestamp": text(item.get("timestamp")),
        "capturedUrl": text(item.get("url")),
        "status": text(item.get("status")),
        "contentDigest": text(item.get("digest")),
        "indexFilename": text(item.get("filename")),
    }


def fetch_text(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/x-ndjson"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Common Crawl endpoint.
        return response.read(2_000_000).decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    generated_at = args.date or datetime.now(UTC).date().isoformat()
    attempts = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8")) if ATTEMPTS_PATH.exists() else {"attempts": []}
    latest: dict[str, dict[str, Any]] = {}
    for attempt in attempts.get("attempts", []):
        if text(attempt.get("status")) in FAILED and text(attempt.get("url")):
            latest[text(attempt.get("gymId"))] = attempt
    targets = sorted(latest.values(), key=lambda item: text(item.get("gymId")))[: args.limit]
    index_id = ""
    errors: list[str] = []
    try:
        collections = fetch_json(COLLECTIONS_URL, args.timeout)
        index_id = text(collections[0].get("id")) if isinstance(collections, list) and collections else ""
    except Exception as error:
        errors.append(f"common-crawl-index:{type(error).__name__}")
    signals: list[dict[str, Any]] = []
    for target in targets:
        item = {
            "gymId": text(target.get("gymId")),
            "url": text(target.get("url")),
            "currentFetchStatus": text(target.get("status")),
            "wayback": None,
            "commonCrawl": None,
            "reviewStatus": "pending",
            "currentPriceEvidenceEligible": False,
        }
        try:
            item["wayback"] = parse_wayback(fetch_json(wayback_query(item["url"]), args.timeout))
        except Exception as error:
            errors.append(f"wayback:{item['gymId']}:{type(error).__name__}")
        time.sleep(1.5)
        if index_id:
            try:
                item["commonCrawl"] = parse_common_crawl(fetch_text(common_crawl_query(index_id, item["url"]), args.timeout))
            except Exception as error:
                errors.append(f"common-crawl:{item['gymId']}:{type(error).__name__}")
            time.sleep(1.5)
        signals.append(item)
    output = {
        "_meta": {
            "generatedAt": generated_at,
            "policy": "Historical status/identity leads only; archive captures can never verify current pricing.",
            "targetCount": len(targets),
            "signalCount": sum(bool(item.get("wayback") or item.get("commonCrawl")) for item in signals),
            "errorCount": len(errors),
        },
        "signals": signals,
        "errors": errors,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output["_meta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
