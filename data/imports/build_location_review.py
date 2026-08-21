"""Turn structured official location evidence into fail-closed review proposals."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import merge_web_research as identity

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "official-location-observations.json"
OUTPUT_PATH = ROOT / "data" / "imports" / "official-location-review.json"
PLACEHOLDER_HOURS = {"", "hours not listed", "hours vary"}


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def street_number(value: Any) -> str:
    match = re.search(r"\b(\d{1,6})\b", text(value))
    return match.group(1) if match else ""


def name_score(left: str, right: str) -> float:
    left_normalized, right_normalized = identity.normalized(left), identity.normalized(right)
    if not left_normalized or not right_normalized:
        return 0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_tokens, right_tokens = set(left_normalized.split()), set(right_normalized.split())
    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) if left_tokens and right_tokens else 0
    return round(max(sequence, overlap), 4)


def day_label(value: Any) -> str:
    raw = text(value).rsplit("/", 1)[-1]
    return raw[:2].title() if raw else ""


def format_hours(value: Any) -> str:
    if isinstance(value, str):
        return "" if "00:00-00:00" in value else value.strip()
    if not isinstance(value, list):
        return ""
    if value and all(isinstance(item, str) for item in value):
        return "; ".join(text(item) for item in value if text(item) and "00:00-00:00" not in text(item))
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        opens, closes = text(item.get("opens")), text(item.get("closes"))
        if not opens or not closes or (opens == "00:00" and closes == "00:00"):
            continue
        days_value = item.get("dayOfWeek")
        days = [day_label(day) for day in days_value] if isinstance(days_value, list) else [day_label(days_value)]
        days = [day for day in days if day]
        if days:
            label = days[0] if len(days) == 1 else f"{days[0]}-{days[-1]}"
            parts.append(f"{label} {opens}-{closes}")
    return "; ".join(parts)


def proposal_for(gym: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    observed_address = text(observation.get("address"))
    observed_name = text(observation.get("name"))
    current_number, observed_number = street_number(gym.get("address")), street_number(observed_address)
    score = name_score(text(gym.get("name")), observed_name)
    san_francisco_address = not observed_address or "san francisco" in identity.normalized(observed_address)
    same_address = bool(
        observed_address
        and current_number
        and observed_number
        and current_number == observed_number
        and identity.canonical_address(text(gym.get("address"))) == identity.canonical_address(observed_address)
    )
    generic_current_address = not current_number
    identity_sound = san_francisco_address and (
        same_address
        or (generic_current_address and bool(observed_number) and score >= 0.8)
        or (not observed_address and score >= 0.8)
    )
    proposed: dict[str, Any] = {}
    if identity_sound:
        if observed_address and (generic_current_address or len(observed_address) > len(text(gym.get("address")))):
            proposed["address"] = observed_address
        if observed_name and score >= 0.8 and len(observed_name) > len(text(gym.get("name"))):
            proposed["name"] = observed_name
        hours = format_hours(observation.get("hours"))
        if hours and identity.normalized(gym.get("hours")) in PLACEHOLDER_HOURS:
            proposed["hours"] = hours
        amenities = [text(item) for item in observation.get("amenities", []) if text(item)]
        if amenities:
            proposed["amenities"] = amenities
    return {
        "gymId": gym["id"],
        "gymName": gym["name"],
        "currentAddress": gym.get("address", ""),
        "sourceUrl": observation.get("sourceUrl", ""),
        "capturedAt": observation.get("capturedAt", ""),
        "contentHash": observation.get("contentHash", ""),
        "identityScore": score,
        "identityDisposition": "strong-exact-review" if identity_sound and proposed else "reject-or-manual-review",
        "observed": {
            "name": observed_name,
            "address": observed_address,
            "hours": format_hours(observation.get("hours")),
            "amenities": observation.get("amenities", []),
        },
        "proposedChanges": proposed,
        "reviewStatus": "pending",
        "autoApply": False,
    }


def main() -> int:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    observations = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8")) if OBSERVATIONS_PATH.exists() else {"observations": []}
    by_id = {text(gym.get("id")): gym for gym in source.get("gyms", [])}
    proposals = [proposal_for(by_id[text(item.get("gymId"))], item) for item in observations.get("observations", []) if text(item.get("gymId")) in by_id]
    proposals.sort(key=lambda item: (item["identityDisposition"] != "strong-exact-review", item["gymName"], item["sourceUrl"], item["contentHash"]))
    output = {
        "generatedAt": observations.get("generatedAt", ""),
        "policy": "Generated proposals only. Every change must be copied to official-location-approved.json with reviewStatus=approved before publication.",
        "proposalCount": len(proposals),
        "strongExactReviewCount": sum(item["identityDisposition"] == "strong-exact-review" for item in proposals),
        "proposals": proposals,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("proposalCount", "strongExactReviewCount")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
