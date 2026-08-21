"""Import local operator replies into sanitized, review-only price observations.

Raw .eml files live under ignored data/private/. Only hashes, dates, gym IDs,
short redacted price labels, and parsed candidate amounts are written to the
committed review queue. Nothing is promoted without a separate reviewed entry
in operator-confirmed-approved.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import contact_research
import crawl_official_sources as crawler

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
DEFAULT_INPUT = ROOT / "data" / "private" / "operator-replies"
OUTPUT_PATH = ROOT / "data" / "imports" / "operator-confirmed-observations.json"
GYM_ID_RE = re.compile(r"\[(?:sf-gyms|sfgym):([^\]]+)\]", re.I)
CONFIDENTIAL_RE = re.compile(
    r"\b(?:confidential|do not (?:publish|share|post)|not for publication|internal use only|private quote)\b",
    re.I,
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_body(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            value = part.get_content()
        except (LookupError, UnicodeDecodeError):
            continue
        if content_type == "text/plain":
            plain.append(text(value))
        else:
            parser = TextExtractor()
            parser.feed(text(value))
            html.append("\n".join(parser.parts))
    return "\n".join(item for item in (plain or html) if item)


def received_date(message: Message) -> str:
    try:
        parsed = parsedate_to_datetime(text(message.get("Date")))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return datetime.now(UTC).date().isoformat()


def parse_message(raw: bytes, known_ids: set[str], explicit_gym_id: str = "") -> dict[str, Any] | None:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = text(message.get("Subject"))
    match = GYM_ID_RE.search(subject)
    gym_id = explicit_gym_id or (text(match.group(1)) if match else "")
    if gym_id not in known_ids:
        return None
    body = extract_body(message)
    sanitized = contact_research.redact_sensitive(body)
    confidential = bool(CONFIDENTIAL_RE.search(sanitized))
    candidates = []
    for candidate in crawler.visible_candidates(sanitized, "operator-response://email"):
        product_type = text(candidate.get("productType"))
        cadence = text(candidate.get("cadence"))
        amount = candidate.get("amount")
        candidates.append({
            "amount": amount,
            "currency": candidate.get("currency") or "USD",
            "productType": product_type,
            "cadence": cadence,
            "rawLabel": f"{product_type} ${amount:g} per {cadence}",
            "promotion": candidate.get("promotion") or {"isPromotion": False, "label": ""},
            "eligibility": candidate.get("eligibility") or {"type": "unknown", "restrictions": []},
        })
    evidence_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    return {
        "gymId": gym_id,
        "evidenceId": evidence_id,
        "receivedAt": received_date(message),
        "contactMethod": "email",
        "confidential": confidential,
        "bodyHash": hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
        "priceCandidates": candidates,
        "reviewStatus": "pending",
        "status": "price-candidates-found" if candidates else "no-price-candidate-found",
        "requiredReview": [
            "confirm exact location identity",
            "confirm standard-adult eligibility",
            "reconstruct complete cadence, access scope, commitment, and mandatory fees",
            "exclude promotions and personalized quotes",
            "confirm no confidentiality restriction",
        ],
        "containsRawMessage": False,
        "containsContactData": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--gym-id", help="Use only when importing exactly one message whose subject lacks the gym tag")
    args = parser.parse_args()
    paths = [args.input] if args.input.is_file() else sorted(args.input.glob("*.eml")) if args.input.exists() else []
    if args.gym_id and len(paths) != 1:
        raise SystemExit("--gym-id may be used only when --input resolves to exactly one .eml file.")
    fixture = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    known_ids = {text(gym.get("id")) for gym in fixture.get("gyms", [])}
    existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) if OUTPUT_PATH.exists() else {"observations": []}
    by_evidence = {text(item.get("evidenceId")): item for item in existing.get("observations", [])}
    skipped = 0
    for path in paths:
        observation = parse_message(path.read_bytes(), known_ids, text(args.gym_id))
        if observation is None:
            skipped += 1
            continue
        by_evidence[observation["evidenceId"]] = observation
    observations = sorted(by_evidence.values(), key=lambda item: (item["gymId"], item["receivedAt"], item["evidenceId"]))
    save_json(OUTPUT_PATH, {
        "generatedAt": datetime.now(UTC).date().isoformat(),
        "policy": "Sanitized review queue only; raw correspondence remains in ignored data/private and is never committed.",
        "observations": observations,
    })
    print(json.dumps({"messagesSeen": len(paths), "observations": len(observations), "skippedWithoutValidGymId": skipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
