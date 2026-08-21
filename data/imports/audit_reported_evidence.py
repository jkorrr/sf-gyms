"""Recheck committed third-party price evidence without storing review prose."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import crawl_official_sources as crawler

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "data" / "imports" / "reported-price-evidence.json"
AUDIT_PATH = ROOT / "data" / "imports" / "reported-evidence-audit.json"
CACHE_PATH = ROOT / "data" / "imports" / "reported-evidence-cache.json"
MAX_AGE_DAYS = 548


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def compact_text(html: str) -> str:
    parser = crawler.PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(parser.visible)).casefold()


def inspect_report(report: dict[str, Any], result: dict[str, Any], previous: dict[str, Any], today: datetime) -> dict[str, Any]:
    html = text(result.get("html"))
    visible = compact_text(html) if html else ""
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest() if html else ""
    amount = report.get("amount")
    amount_patterns = {f"${float(amount):g}", f"{float(amount):g} usd"} if amount is not None else set()
    try:
        age_days = (today.date() - datetime.fromisoformat(text(report.get("publishedAt"))).date()).days
    except ValueError:
        age_days = MAX_AGE_DAYS + 1
    return {
        "reportId": text(report.get("id")),
        "gymId": text(report.get("gymId")),
        "url": text(report.get("sourceUrl")),
        "attemptedAt": today.date().isoformat(),
        "status": text(result.get("status")),
        "robotsStatus": text(result.get("robotsStatus")),
        "contentHash": digest,
        "contentChanged": bool(previous.get("contentHash") and digest and previous.get("contentHash") != digest),
        "amountStillVisible": any(pattern.casefold() in visible for pattern in amount_patterns),
        "withinRecencyWindow": 0 <= age_days <= MAX_AGE_DAYS,
        "ageDays": age_days,
        "requiresReview": text(result.get("status")) not in {"fetched", "not-modified"} or (bool(html) and not any(pattern.casefold() in visible for pattern in amount_patterns)),
    }


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--date", help="Override audit date")
    args = parser.parse_args()
    today = datetime.fromisoformat(args.date) if args.date else datetime.now(UTC).replace(tzinfo=None)
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8")) if EVIDENCE_PATH.exists() else {"reports": []}
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    audits = []
    for report in evidence.get("reports", []):
        url = text(report.get("sourceUrl"))
        result = crawler.fetch_page(url, args.timeout, cache.get(url))
        audit = inspect_report(report, result, cache.get(url, {}), today)
        audits.append(audit)
        cache[url] = {
            "status": audit["status"],
            "lastAttemptAt": audit["attemptedAt"],
            "contentHash": audit["contentHash"] or cache.get(url, {}).get("contentHash", ""),
            "etag": result.get("etag", ""),
            "lastModified": result.get("lastModified", ""),
        }
    output = {
        "generatedAt": today.date().isoformat(),
        "policy": "Availability/hash/claim checks only; no review prose is retained.",
        "reportsChecked": len(audits),
        "requiresReview": sum(item["requiresReview"] for item in audits),
        "audits": audits,
    }
    save_json(CACHE_PATH, cache)
    save_json(AUDIT_PATH, output)
    print(json.dumps({"reportsChecked": output["reportsChecked"], "requiresReview": output["requiresReview"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
