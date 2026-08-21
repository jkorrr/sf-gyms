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
MONEY_RANGE_RE = re.compile(
    r"\$(?P<low>\d{1,4}(?:\.\d{1,2})?)\s*(?:-|–|—|to)\s*\$?(?P<high>\d{1,4}(?:\.\d{1,2})?)",
    re.I,
)
FEE_TYPE_RE = re.compile(
    r"\b(?P<type>annual|enrollment|initiation|processing|setup|activation)\s+(?:membership\s+)?fee\b",
    re.I,
)
QUOTED_REPLY_RE = re.compile(
    r"^(?:on .+ wrote:|[-_]{2,}\s*original message\s*[-_]{2,}|from:\s|sent:\s|subject:\s)",
    re.I,
)
NO_STANDARD_PLAN_RE = re.compile(
    r"\b(?:do not|don't|does not|doesn't|no longer)\s+offer\s+(?:an?\s+)?(?:standard\s+|public\s+|ordinary\s+)?"
    r"(?:monthly\s+|recurring\s+)?(?:plan|membership|drop[ -]?in|single visit|single class)\b|"
    r"\bno\s+(?:standard\s+|public\s+|ordinary\s+)?(?:monthly\s+|recurring\s+)?(?:plan|membership|drop[ -]?in)\s+is\s+(?:available|offered)\b",
    re.I,
)
CUSTOM_QUOTE_RE = re.compile(r"\b(?:custom|personalized|individualized)\s+(?:pricing|quote|rate)|\bpricing\s+(?:depends on|varies by)\b", re.I)
EFFECTIVE_DATE_RE = re.compile(
    r"\b(?:effective|rates?\s+(?:took|take)\s+effect(?:ive)?(?:\s+on)?|as of)\s+"
    r"(?P<date>(?:[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})|(?:\d{4}-\d{2}-\d{2}))\b",
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


def unquoted_reply(value: str) -> str:
    """Keep the newly authored reply and discard common quoted-thread blocks."""

    kept: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if QUOTED_REPLY_RE.search(line):
            break
        if line.startswith(">"):
            continue
        kept.append(raw_line)
    return "\n".join(kept).strip()


def semantic_label(segment: str, product_type: str) -> str:
    """Return a narrow product label without retaining arbitrary reply prose."""

    patterns = (
        r"(?:our|the|current)?\s*([A-Za-z0-9][A-Za-z0-9 &+/'-]{0,48}?\s+(?:membership|plan|package))\s*(?:is|costs?|:|-)\s*\$",
        r"\b(drop[ -]?in|single class|single visit|day pass)\b[^$]{0,35}\$",
    )
    for pattern in patterns:
        match = re.search(pattern, segment, re.I)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" -:")
            value = re.sub(r"^(?:our|the|current)\s+", "", value, flags=re.I)
            return value[:64]
    return "Operator-confirmed drop-in" if product_type == "drop-in" else "Operator-confirmed recurring plan"


def cadence_metadata(segment: str) -> tuple[str, int]:
    lower = segment.casefold()
    if re.search(r"\b(?:every|per)\s+4\s+weeks?\b|/\s*4\s*weeks?", lower):
        return "4 weeks", 4
    if re.search(r"\b(?:biweekly|every\s+2\s+weeks?)\b", lower):
        return "2 weeks", 2
    if re.search(r"\b(?:weekly|per\s+week|/\s*w(?:k|eek))\b", lower):
        return "week", 1
    if re.search(r"\b(?:monthly|per\s+month|/\s*mo(?:nth)?)\b", lower):
        return "month", 1
    if re.search(r"\b(?:drop[ -]?in|single class|single visit|day pass|per\s+(?:class|visit|session))\b", lower):
        return "visit", 1
    return "one-time", 1


def allowance_metadata(segment: str) -> dict[str, Any] | None:
    if re.search(r"\bunlimited\b", segment, re.I):
        return {"count": None, "period": "month", "unlimited": True, "disclosed": True}
    match = re.search(
        r"\b(?P<count>\d{1,3})\s*(?:x\s*/?\s*|classes?|visits?|sessions?)\s*(?:per|/)?\s*"
        r"(?P<period>week|month|30 days|4 weeks)?\b",
        segment,
        re.I,
    )
    if not match:
        return None
    return {
        "count": float(match.group("count")),
        "period": (match.group("period") or "month").casefold(),
        "unlimited": False,
        "disclosed": True,
    }


def commitment_metadata(segment: str) -> dict[str, Any]:
    if re.search(r"\b(?:month[ -]to[ -]month|no\s+(?:minimum\s+)?commitment|cancel anytime)\b", segment, re.I):
        return {"type": "month-to-month", "minimumMonths": None}
    match = re.search(r"\b(?P<months>\d{1,2})[ -]?(?:month|mo)\s+(?:minimum|commitment|contract|term)\b", segment, re.I)
    if match:
        return {"type": "fixed-term", "minimumMonths": int(match.group("months"))}
    return {"type": "unknown", "minimumMonths": None}


def normalized_monthly(amount: float, cadence: str) -> tuple[float | None, str]:
    if cadence == "month":
        return round(amount, 2), "amount per month"
    if cadence == "week":
        return round(amount * 52 / 12, 2), "weekly amount × 52 / 12"
    if cadence == "2 weeks":
        return round(amount * 26 / 12, 2), "biweekly amount × 26 / 12"
    if cadence == "4 weeks":
        return round(amount * 13 / 12, 2), "four-week amount × 13 / 12"
    return None, "not applicable"


def fee_candidates(value: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for line in value.splitlines():
        if not FEE_TYPE_RE.search(line):
            continue
        fee_mentions = list(FEE_TYPE_RE.finditer(line))
        for match in crawler.MONEY_RE.finditer(line):
            around = line[max(0, match.start() - 80) : match.end() + 80]
            amount_center = (match.start() + match.end()) / 2
            fee_match = min(
                fee_mentions,
                key=lambda item: abs(((item.start() + item.end()) / 2) - amount_center),
                default=None,
            )
            if not fee_match:
                continue
            amount = float(match.group(1))
            lower = around.casefold()
            waived = bool(re.search(r"\b(?:waived|optional|not required|no)\b", lower)) or amount == 0
            fee_type = fee_match.group("type").casefold()
            cadence = "year" if fee_type == "annual" else "one-time"
            candidates.append({
                "type": fee_type,
                "amount": amount,
                "currency": "USD",
                "cadence": cadence,
                "mandatory": not waived,
                "rawLabel": f"{fee_type.title()} fee: ${amount:g} {cadence}",
            })
    by_key = {(item["type"], item["amount"], item["cadence"], item["mandatory"]): item for item in candidates}
    return list(by_key.values())


def effective_date(value: str) -> str:
    match = EFFECTIVE_DATE_RE.search(value)
    if not match:
        return ""
    raw = match.group("date").replace(",", "")
    for pattern in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    return ""


def structured_reply_candidates(value: str) -> dict[str, Any]:
    """Extract review-safe plan facts while preserving plan/fee ambiguity."""

    reply = unquoted_reply(value)
    fees = fee_candidates(reply)
    ranges: list[dict[str, Any]] = []
    prices: list[dict[str, Any]] = []
    segments = [part.strip() for line in reply.splitlines() for part in re.split(r"(?<=[.!?])\s+|\s*;\s*", line) if part.strip()]
    for segment in segments:
        range_spans: list[tuple[int, int]] = []
        for match in MONEY_RANGE_RE.finditer(segment):
            low, high = float(match.group("low")), float(match.group("high"))
            if low <= 0 or high < low or high > 10_000:
                continue
            cadence, interval_count = cadence_metadata(segment)
            product_type = "drop-in" if cadence == "visit" else "monthly" if cadence in {"month", "week", "2 weeks", "4 weeks"} else "offer"
            ranges.append({
                "low": low,
                "high": high,
                "currency": "USD",
                "productType": product_type,
                "cadence": cadence,
                "intervalCount": interval_count,
                "rawLabel": f"{semantic_label(segment, product_type)}: ${low:g}–${high:g} per {cadence}",
                "classAllowance": allowance_metadata(segment),
                "commitment": commitment_metadata(segment),
            })
            range_spans.append(match.span())
        for match in crawler.MONEY_RE.finditer(segment):
            if any(start <= match.start() < end for start, end in range_spans):
                continue
            around = segment[max(0, match.start() - 80) : match.end() + 80]
            if FEE_TYPE_RE.search(around):
                continue
            amount = float(match.group(1))
            if amount <= 0 or amount > 10_000:
                continue
            cadence, interval_count = cadence_metadata(segment)
            product_type = "drop-in" if cadence == "visit" else "monthly" if cadence in {"month", "week", "2 weeks", "4 weeks"} else "offer"
            if product_type == "offer" and not re.search(r"\b(?:plan|membership|package|class|visit|session|rate|price|cost)\b", segment, re.I):
                continue
            normalized, formula = normalized_monthly(amount, cadence)
            label = semantic_label(segment, product_type)
            prices.append({
                "amount": amount,
                "currency": "USD",
                "productType": product_type,
                "cadence": cadence,
                "intervalCount": interval_count,
                "normalizedMonthly": normalized,
                "normalizationFormula": formula,
                "rawLabel": f"{label}: ${amount:g} per {cadence}",
                "classAllowance": allowance_metadata(segment),
                "commitment": commitment_metadata(segment),
                "promotion": crawler.candidate_metadata(segment, cadence)["promotion"],
                "eligibility": crawler.candidate_metadata(segment, cadence)["eligibility"],
                "fees": [],
            })
    recurring = [item for item in prices if item["productType"] == "monthly"]
    mandatory_fees = [item for item in fees if item["mandatory"]]
    if len(recurring) == 1 and mandatory_fees:
        recurring[0]["fees"] = [{**item, "linkage": "single-recurring-plan-in-reply"} for item in mandatory_fees]
    price_by_key = {(item["amount"], item["productType"], item["cadence"], item["rawLabel"]): item for item in prices}
    range_by_key = {(item["low"], item["high"], item["productType"], item["cadence"]): item for item in ranges}
    statements: list[str] = []
    if NO_STANDARD_PLAN_RE.search(reply):
        statements.append("no-standard-plan-or-drop-in-offered")
    if CUSTOM_QUOTE_RE.search(reply):
        statements.append("custom-or-personalized-pricing")
    return {
        "priceCandidates": list(price_by_key.values()),
        "rangeCandidates": list(range_by_key.values()),
        "feeCandidates": fees,
        "effectiveDate": effective_date(reply),
        "operatorStatements": statements,
    }


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
    sanitized = contact_research.redact_sensitive(unquoted_reply(body))
    confidential = bool(CONFIDENTIAL_RE.search(sanitized))
    structured = structured_reply_candidates(sanitized)
    candidates = structured["priceCandidates"]
    if not candidates and not structured["rangeCandidates"]:
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
                "fees": [],
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
        "rangeCandidates": structured["rangeCandidates"],
        "feeCandidates": structured["feeCandidates"],
        "effectiveDate": structured["effectiveDate"],
        "operatorStatements": structured["operatorStatements"],
        "reviewStatus": "pending",
        "status": "price-candidates-found" if candidates or structured["rangeCandidates"] else "operator-statement-found" if structured["operatorStatements"] else "no-price-candidate-found",
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
