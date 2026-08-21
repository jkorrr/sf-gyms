"""Classify every SF listing and add conservative, auditable cost coverage.

Verified prices remain in monthlyPrice/dayPassPrice. Estimates are written only
to estimatedMonthly and therefore cannot be mistaken for operator-published
prices by existing consumers of the fixture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import platform_adapters

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
WEB_PATH = ROOT / "apps" / "web" / "lib" / "sf-gyms-osm.json"
REPORT_PATH = ROOT / "data" / "imports" / "cost-coverage-report.json"
REVIEW_PATH = ROOT / "data" / "imports" / "cost-coverage-review.json"
CRAWL_ATTEMPTS_PATH = ROOT / "data" / "imports" / "official-crawl-attempts.json"
RENDERED_CRAWL_ATTEMPTS_PATH = ROOT / "data" / "imports" / "rendered-crawl-attempts.json"
LOCATION_METADATA_APPROVED_PATH = ROOT / "data" / "imports" / "official-location-approved.json"
APPROVED_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "official-crawl-approved.json"
OPERATOR_CATALOG_APPROVED_PATH = ROOT / "data" / "imports" / "official-operator-catalog-approved.json"
LOCATION_OVERRIDES_PATH = ROOT / "data" / "imports" / "official-location-overrides.json"
SOURCE_DISCOVERIES_PATH = ROOT / "data" / "imports" / "public-source-discovery.json"
METADATA_RECOVERY_GLOB = "official-metadata-recovery-*.json"
REPORTED_EVIDENCE_PATH = ROOT / "data" / "imports" / "reported-price-evidence.json"
OPERATOR_CONFIRMED_PATH = ROOT / "data" / "imports" / "operator-confirmed-approved.json"
DEAL_APPROVED_PATH = ROOT / "data" / "imports" / "deal-approved.json"
PUBLIC_DISCOVERY_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "public-source-discovery-observations.json"
MANUAL_SOURCE_SEARCH_PATH = ROOT / "data" / "imports" / "manual-source-search.json"
REPORTED_EVIDENCE_AUDIT_PATH = ROOT / "data" / "imports" / "reported-evidence-audit.json"
OFFICIAL_COMPARABLES_PATH = ROOT / "data" / "imports" / "official-comparable-prices.json"
ESTIMATOR_VERSION = "sf-bay-area-v5-withheld-price-calibrated"
SELECTION_RULE_VERSION = "neutral-basic-v2"
REPORTED_PRICE_VERSION = "reported-cost-v1"
REPORTED_MAX_AGE_DAYS = 548

ENTITY_KINDS = {
    "gym",
    "studio",
    "martial-arts",
    "public-recreation",
    "outdoor-equipment",
    "non-consumer",
}
ACCESS_MODELS = {
    "membership",
    "class-membership",
    "class-pack",
    "drop-in",
    "free-public",
    "restricted",
    "not-applicable",
}
PRICING_STATUSES = {
    "verified",
    "official-range",
    "operator-confirmed",
    "reported",
    "estimated",
    "free",
    "pay-per-visit",
    "not-applicable",
    "gated",
    "unresolved",
}
OPERATOR_CONFIRMED_STALE_DAYS = 90
DEAL_STALE_DAYS = 7

OUTDOOR_EQUIPMENT_RE = re.compile(
    r"\b(?:achill+es stretch|balance beam|bench leg raise|body curl|chin[ -]?up|circle body|"
    r"hand walk|hop kick|knee lift|leg stretch|log jumps?|push[ -]?up|sit ?& ?reach|"
    r"sit reach|sit[ -]?up|step[ -]?up|touch toes|vault bar|exercise area)\b",
    re.IGNORECASE,
)
PUBLIC_RECREATION_RE = re.compile(
    r"\b(?:park|playground|tennis courts?|basketball courts?|soccer fields?|little league|"
    r"recreation cent(?:er|re)|clubhouse|aquatic|pool|pavilion|bocce ball)\b",
    re.IGNORECASE,
)
POOL_OR_BOOKABLE_RE = re.compile(r"\b(?:pool|swimming|tennis center)\b", re.IGNORECASE)
NON_CONSUMER_RE = re.compile(
    r"\b(?:job corps|pistol range|police|corporate wellness|employee fitness|private facility)\b",
    re.IGNORECASE,
)
MARTIAL_ARTS_RE = re.compile(
    r"\b(?:jiu[ -]?jitsu|karate|tae kwon do|taekwondo|muay thai|mma|wushu|kung fu|"
    r"krav maga|boxing|martial arts?|kenpo|hapkido|choy lay fut)\b",
    re.IGNORECASE,
)
STUDIO_RE = re.compile(
    r"\b(?:yoga|pilates|lagree|barre|solidcore|bodyrok|core40|soulcycle|orangetheory|"
    r"f45|barry(?: s)?|crossfit|row house|stretch ?lab|dance|gymnastics|personal training|cycling|spin|reformer|vrv3)\b",
    re.IGNORECASE,
)
COMMERCIAL_GYM_RE = re.compile(
    r"\b(?:crossfit|fitness cent(?:er|re)|fitness club|health club|climbing gym|boulders?|"
    r"fitness|weightlifting|barbell|powerlifting|gymnasium|gym)\b",
    re.IGNORECASE,
)


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text(value).casefold()).strip()


def canonical_address(value: Any) -> str:
    """Return the same unit/ZIP-insensitive street key used by the identity merge."""

    components = [normalized(part) for part in text(value).split(",") if normalized(part)]
    if not components:
        return ""
    street = components[0]
    replacements = {
        r"\bfirst\b": "1st", r"\bsecond\b": "2nd", r"\bthird\b": "3rd",
        r"\bfourth\b": "4th", r"\bfifth\b": "5th", r"\bsixth\b": "6th",
        r"\bseventh\b": "7th", r"\beighth\b": "8th", r"\bninth\b": "9th",
        r"\btenth\b": "10th", r"\bstreet\b": "st", r"\bavenue\b": "ave",
        r"\bboulevard\b": "blvd", r"\broad\b": "rd", r"\bdrive\b": "dr",
        r"\blane\b": "ln", r"\bplace\b": "pl", r"\bhighway\b": "hwy",
    }
    for pattern, replacement in replacements.items():
        street = re.sub(pattern, replacement, street)
    street = re.sub(r"\b(?:suite|ste|unit|level|floor|fl)\b.*$", "", street).strip()
    return re.sub(r"\s+", " ", street)


def official_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold().removeprefix("www.")
    except ValueError:
        return ""


def is_osm_url(url: str) -> bool:
    return official_domain(url) == "openstreetmap.org"


def classify_entity(gym: dict[str, Any]) -> str:
    haystack = normalized(" ".join((text(gym.get("name")), text(gym.get("gymType")), text(gym.get("description")))))
    name = normalized(gym.get("name"))
    if OUTDOOR_EQUIPMENT_RE.search(haystack):
        return "outdoor-equipment"
    if NON_CONSUMER_RE.search(haystack):
        return "non-consumer"
    if MARTIAL_ARTS_RE.search(name):
        return "martial-arts"
    if STUDIO_RE.search(name):
        return "studio"
    if COMMERCIAL_GYM_RE.search(name):
        return "gym"
    if PUBLIC_RECREATION_RE.search(haystack):
        return "public-recreation"
    if MARTIAL_ARTS_RE.search(haystack):
        return "martial-arts"
    if STUDIO_RE.search(haystack):
        return "studio"
    return "gym"


def modality(gym: dict[str, Any], entity_kind: str) -> str:
    value = normalized(f"{gym.get('name', '')} {gym.get('gymType', '')}")
    groups = (
        ("yoga", ("yoga", "corepower", "haum", "folk")),
        ("pilates-lagree-barre", ("pilates", "lagree", "solidcore", "bodyrok", "core40", "barre")),
        ("martial-arts-boxing", ("jiu", "jitsu", "karate", "tae kwon", "muay", "mma", "boxing", "martial", "kenpo", "wushu", "kung fu", "krav")),
        ("interval-studio", ("orangetheory", "barry")),
        ("crossfit-strength", ("crossfit", "barbell", "strength", "powerlifting", "athletic performance", "strong friends")),
        ("functional-hiit-studio", ("f45",)),
        ("cycling-rowing-studio", ("soulcycle", "row house", "rumble")),
        ("assisted-stretch-studio", ("stretch lab",)),
        ("personal-training", ("personal training", "private training", "trainer access")),
        ("gymnastics-dance", ("gymnastics", "dance")),
        ("premium-club", ("equinox", "bay club")),
        ("budget-full-service", ("24 hour", "planet fitness", "crunch", "fitness sf", "live fit", "ymca", "fitness 19", "city sports")),
    )
    for label, needles in groups:
        if any(needle in value for needle in needles):
            return label
    if entity_kind == "public-recreation":
        return "public-recreation"
    if entity_kind == "outdoor-equipment":
        return "outdoor-equipment"
    if entity_kind == "non-consumer":
        return "non-consumer"
    if entity_kind == "martial-arts":
        return "martial-arts-boxing"
    if entity_kind == "studio":
        return "independent-studio"
    return "independent-gym"


def operator_key(gym: dict[str, Any]) -> str:
    value = normalized(gym.get("name"))
    aliases = (
        ("24 hour fitness", "24-hour-fitness"),
        ("planet fitness", "planet-fitness"),
        ("crunch", "crunch"),
        ("fitness sf", "fitness-sf"),
        ("live fit", "live-fit"),
        ("bay club", "bay-club"),
        ("equinox", "equinox"),
        ("orangetheory", "orangetheory"),
        ("orange theory", "orangetheory"),
        ("corepower", "corepower-yoga"),
        ("f45", "f45"),
        ("barry", "barrys"),
        ("solidcore", "solidcore"),
        ("soulcycle", "soulcycle"),
        ("pure barre", "pure-barre"),
        ("bodyrok", "bodyrok"),
        ("core40", "core40"),
        ("mx3", "mx3-fitness"),
        ("love story yoga", "love-story-yoga"),
        ("muscle beach", "muscle-beach"),
        ("iron mettle", "iron-and-mettle"),
    )
    for needle, key in aliases:
        if needle in value:
            return key
    domain = official_domain(text(gym.get("websiteUrl")))
    if domain and not is_osm_url(text(gym.get("websiteUrl"))):
        return domain
    tokens = [token for token in value.split() if token not in {"san", "francisco", "sf", "gym", "fitness", "studio"}]
    return "-".join(tokens[:3]) or normalized(gym.get("id"))


def access_model(gym: dict[str, Any], entity_kind: str) -> str:
    name = text(gym.get("name"))
    access_text = normalized(
        " ".join(
            (
                text(gym.get("name")),
                text(gym.get("description")),
                text(gym.get("priceNote")),
                text(gym.get("accessAvailability")),
            )
        )
    )
    if any(
        phrase in access_text
        for phrase in (
            "reserved exclusively",
            "not open to the public",
            "current clients working with a trainer",
            "employee only",
            "employees only",
        )
    ):
        return "restricted"
    if entity_kind == "outdoor-equipment":
        return "free-public"
    if entity_kind == "non-consumer":
        return "restricted"
    if entity_kind == "public-recreation":
        if gym.get("dayPassPrice") is not None or POOL_OR_BOOKABLE_RE.search(name):
            return "drop-in"
        return "free-public"
    if entity_kind in {"studio", "martial-arts"}:
        return "class-membership"
    return "membership"


def fee_list(gym: dict[str, Any]) -> list[dict[str, Any]]:
    fields = (
        ("annual", "annualFee", "yearly"),
        ("enrollment", "enrollmentFee", "one-time"),
        ("initiation", "initiationFee", "one-time"),
        ("processing", "processingFee", "one-time"),
        ("activation", "activationFee", "one-time"),
    )
    return [
        {"type": label, "amount": gym[field], "currency": "USD", "cadence": cadence, "mandatory": True}
        for label, field, cadence in fields
        if gym.get(field) is not None
    ]


def normalized_monthly(amount: float, interval: str, interval_count: int = 1) -> float | None:
    cadence = normalized(interval)
    count = max(1, int(interval_count or 1))
    if cadence in {"month", "monthly"}:
        return amount / count
    if cadence in {"week", "weekly"}:
        return amount * 52 / (12 * count)
    if cadence in {"biweekly", "two weeks", "2 weeks"}:
        return amount * 26 / (12 * count)
    if cadence in {"four weeks", "4 weeks"}:
        return amount * 13 / (12 * count)
    if cadence in {"30 day", "30 days"}:
        return amount * 365.2425 / (30 * 12 * count)
    if cadence in {"year", "annual", "yearly"}:
        return amount / (12 * count)
    return None


def class_allowance(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "count": value.get("count"),
            "period": text(value.get("period")) or "month",
            "unlimited": bool(value.get("unlimited")),
            "disclosed": bool(value.get("disclosed", True)),
        }
    if isinstance(value, (int, float)):
        return {"count": float(value), "period": "month", "unlimited": False, "disclosed": True}
    raw = normalized(value)
    if not raw:
        return None
    if "unlimited" in raw:
        return {"count": None, "period": "month", "unlimited": True, "disclosed": True}
    number_words = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "twelve": "12", "sixteen": "16", "twenty": "20",
    }
    for word, digit in number_words.items():
        raw = re.sub(rf"\b{word}\b", digit, raw)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:classes?|sessions?|visits?|x)?\s*(?:per|a|/)?\s*(weekly|week|monthly|month|4 weeks|four weeks)", raw)
    if not match:
        return None
    count, period = float(match.group(1)), match.group(2)
    if period in {"week", "weekly"}:
        count = count * 52 / 12
        period = "month"
    elif period in {"4 weeks", "four weeks"}:
        count = count * 13 / 12
        period = "month"
    elif period == "monthly":
        period = "month"
    return {"count": round(count, 2), "period": period, "unlimited": False, "disclosed": True}


def evidence_for(gym: dict[str, Any], raw_label: str, method: str = "reviewed-official-observation") -> dict[str, Any]:
    url = text(gym.get("priceSourceUrl"))
    observed = text(gym.get("priceObservedAt"))
    digest = hashlib.sha256(f"{url}|{observed}|{raw_label}".encode()).hexdigest()
    return {
        "url": url,
        "observedAt": observed,
        "source": text(gym.get("priceSource")) or text(gym.get("sourceName")),
        "method": method,
        "rawLabel": raw_label[:220],
        "contentHash": digest,
        "evidenceTier": "official-public",
        "exactLocationMatch": (
            "operator-market-multi-location"
            if text(gym.get("operatorCatalogApprovalId"))
            else "exact-location"
            if re.search(r"\d", text(gym.get("address")))
            else "location-unconfirmed"
        ),
        "sourceProductId": "",
        "conflictFlags": [],
    }


def evidence_for_offer(gym: dict[str, Any], offer: dict[str, Any], raw_label: str) -> dict[str, Any]:
    """Build evidence while allowing a reviewed offer to name its own source page."""

    evidence = evidence_for(gym, raw_label)
    url = text(offer.get("sourceUrl")) or evidence["url"]
    observed_at = text(offer.get("observedAt")) or evidence["observedAt"]
    source = text(offer.get("source")) or evidence["source"]
    method = text(offer.get("captureMethod")) or evidence["method"]
    evidence_tier = text(offer.get("evidenceTier")) or evidence["evidenceTier"]
    exact_location_match = text(offer.get("exactLocationMatch")) or evidence["exactLocationMatch"]
    conflict_flags = list(offer.get("conflictFlags") or evidence["conflictFlags"])
    evidence.update(
        {
            "url": url,
            "observedAt": observed_at,
            "source": source,
            "method": method,
            "evidenceTier": evidence_tier,
            "exactLocationMatch": exact_location_match,
            "conflictFlags": conflict_flags,
            "sourceProductId": text(offer.get("sourceProductId")),
            "contentHash": hashlib.sha256(f"{url}|{observed_at}|{raw_label}".encode()).hexdigest(),
        }
    )
    return evidence


def normalize_plan_offer(gym: dict[str, Any], offer: dict[str, Any], index: int, access: str) -> dict[str, Any]:
    billing_input = offer.get("billing", {}) if isinstance(offer.get("billing"), dict) else {}
    raw_amount = billing_input.get("amount", offer.get("amount"))
    amount = float(raw_amount) if raw_amount is not None and text(raw_amount) else None
    raw_amount_low = billing_input.get("amountLow", offer.get("amountLow"))
    raw_amount_high = billing_input.get("amountHigh", offer.get("amountHigh"))
    amount_low = float(raw_amount_low) if raw_amount_low is not None and text(raw_amount_low) else None
    amount_high = float(raw_amount_high) if raw_amount_high is not None and text(raw_amount_high) else None
    interval = text(billing_input.get("interval") or offer.get("billingInterval")) or "month"
    interval_count = int(billing_input.get("intervalCount") or offer.get("intervalCount") or 1)
    monthly = billing_input.get("normalizedMonthly")
    if monthly is None and amount is not None and amount > 0:
        monthly = normalized_monthly(amount, interval, interval_count)
    source_product_id = text(offer.get("sourceProductId"))
    stable_part = source_product_id or hashlib.sha1(
        f"{text(offer.get('name'))}|{amount if amount is not None else 'undisclosed'}|{interval}|{interval_count}".encode()
    ).hexdigest()[:12]
    commitment_input = offer.get("commitment", {}) if isinstance(offer.get("commitment"), dict) else {}
    promotion_input = offer.get("promotion", {}) if isinstance(offer.get("promotion"), dict) else {}
    eligibility_input = offer.get("eligibility", {}) if isinstance(offer.get("eligibility"), dict) else {}
    availability = text(offer.get("availability")) or ("presale" if gym.get("recordStatus") == "coming_soon" else "available")
    scope = text(offer.get("accessScope")) or text(gym.get("planScope")) or ("Named studio" if access == "class-membership" else "Named location")
    raw_label = text(offer.get("rawLabel")) or text(offer.get("name")) or text(gym.get("priceNote"))
    fees = offer.get("fees") if isinstance(offer.get("fees"), list) else fee_list(gym)
    evidence = dict(offer.get("evidence")) if isinstance(offer.get("evidence"), dict) else evidence_for_offer(gym, offer, raw_label)
    evidence.setdefault("evidenceTier", text(offer.get("evidenceTier")) or "official-public")
    evidence.setdefault("exactLocationMatch", text(offer.get("exactLocationMatch")) or "exact-location")
    evidence.setdefault("sourceProductId", source_product_id)
    evidence.setdefault("conflictFlags", offer.get("conflictFlags", []))
    normalized_allowance = class_allowance(offer.get("classAllowance") or f"{text(offer.get('name'))} {scope}") or (
        {"count": None, "period": "month", "unlimited": False, "disclosed": False}
        if (text(offer.get("productType")) or ("class-membership" if access == "class-membership" else "membership")) == "class-membership"
        else None
    )
    if normalized_allowance and normalized(interval) in {"one time", "one-time"}:
        normalized_allowance["period"] = "purchase"
    return {
        "id": f"{gym['id']}:plan:{stable_part}",
        "sourceProductId": source_product_id,
        "name": text(offer.get("name")) or ("Standard recurring class membership" if access == "class-membership" else "Standard recurring membership"),
        "productType": text(offer.get("productType")) or ("class-membership" if access == "class-membership" else "membership"),
        "accessScope": scope,
        "scopeType": text(offer.get("scopeType")) or (
            "multi-location"
            if any(phrase in scope.casefold() for phrase in ("all locations", "across", "multi-location", "operator locations"))
            else "single-location"
        ),
        "classAllowance": normalized_allowance,
        "billing": {
            "amount": amount,
            "amountLow": amount_low,
            "amountHigh": amount_high,
            "currency": text(billing_input.get("currency") or offer.get("currency")) or "USD",
            "interval": interval,
            "intervalCount": interval_count,
            "normalizedMonthly": round(float(monthly), 2) if monthly is not None else None,
            "normalizationFormula": text(billing_input.get("normalizationFormula")) or (
                "undisclosed" if amount is None
                else "not applicable: one-time purchase" if normalized(interval) in {"one time", "one-time"}
                else "amount" if normalized(interval) in {"month", "monthly"}
                else f"normalized from {amount:g} per {interval}"
            ),
        },
        "commitment": {
            "type": text(commitment_input.get("type") or offer.get("commitmentType")) or "unknown",
            "minimumMonths": commitment_input.get("minimumMonths", offer.get("minimumCommitmentMonths")),
            "minimumDays": commitment_input.get("minimumDays", offer.get("minimumCommitmentDays")),
            "rawLabel": text(commitment_input.get("rawLabel")),
        },
        "availability": availability,
        "purchaseMethod": text(offer.get("purchaseMethod")) or "direct-public",
        "eligibility": {
            "type": text(eligibility_input.get("type")) or ("restricted" if access in {"restricted", "not-applicable"} else "standard-adult"),
            "restrictions": eligibility_input.get("restrictions", []),
        },
        "promotion": {
            "isPromotion": bool(promotion_input.get("isPromotion", promotion_input.get("isPromo", False))),
            "label": text(promotion_input.get("label")),
            "expiresAt": promotion_input.get("expiresAt"),
        },
        "fees": fees,
        "bestValueLabel": bool(offer.get("bestValueLabel")),
        "upfrontDues": offer.get("upfrontDues") if isinstance(offer.get("upfrontDues"), list) else [],
        "evidence": evidence,
        "selected": False,
        "selectionReason": "",
    }


def infer_legacy_plan_metadata(gym: dict[str, Any], access: str) -> dict[str, Any]:
    """Recover explicit plan attributes from reviewed labels without inventing offers.

    This intentionally recognizes only phrases present in the approved observation.
    Ambiguous attributes remain unknown and alternatives are added only as curated
    ``planOffers`` in the reviewed upstream file.
    """
    note = text(gym.get("priceNote"))
    value = normalized(f"{gym.get('name', '')} {note}")
    name = text(gym.get("planName"))
    mappings = (
        ("gold is displayed", "Gold"),
        ("single site membership", "Single-Site Membership"),
        ("adult ymca membership", "Adult Membership"),
        ("organization wide adult membership", "Adult Membership"),
        ("standard membership", "Standard Membership"),
        ("usual price", "Monthly Membership"),
        ("month to month membership", "Month-to-Month Membership"),
        ("official pricing page lists", "Monthly Membership"),
        ("city crossfit", "12 Classes Monthly"),
        ("one crunch", "One Crunch"),
        ("individual membership", "Individual Membership"),
        ("destination access", "Destination Access"),
        ("select single club access", "Select Access"),
        ("select access", "Select Access"),
        ("general public membership", "General Public Membership"),
        ("all gym access", "All-Gym Access"),
        ("basic is", "Basic"),
        ("premier is", "Premier"),
        ("monthly unlimited", "Monthly Unlimited"),
        ("silver glow", "Silver Glow"),
        ("adult full access membership", "Adult Full-Access Membership"),
        ("memberships start from", "Unlimited Membership — Starting Price"),
        ("core access", "Core Access"),
        ("open gym only", "Open Gym Only"),
        ("classic is", "Classic"),
        ("4 class membership", "4 Classes Monthly"),
        ("soul renew", "Soul Renew 4"),
        ("yoga or pilates membership", "Yoga or Pilates Membership"),
        ("club bar 5", "Club Bar 5"),
        ("drop in memberships start", "Drop-In Membership"),
        ("4 classes per month", "4 Classes Monthly"),
        ("8 classes", "8 Classes Monthly"),
    )
    if not name:
        for phrase, label in mappings:
            if phrase in value:
                name = label
                break
    if not name:
        name = "Recurring class membership" if access == "class-membership" else "Recurring membership"

    allowance: dict[str, Any] | None = None
    if access == "class-membership":
        allowance_match = re.search(r"(?:for\s+)?(\d+)\s*(?:live\s+)?classes(?:\s+per\s+month)?", value)
        if allowance_match:
            allowance = {"count": float(allowance_match.group(1)), "period": "month", "unlimited": False}
        elif "club bar 5" in value:
            allowance = {"count": 5.0, "period": "month", "unlimited": False}
        elif "unlimited" in normalized(name):
            allowance = {"count": None, "period": "month", "unlimited": True}

    scope = text(gym.get("planScope"))
    if not scope:
        if any(phrase in value for phrase in ("all location", "all 8 gym", "all gym access", "across all location", "organization wide")):
            scope = "All operator locations"
        elif any(phrase in value for phrase in ("single site", "single club", "named location")):
            scope = "Named location"
        else:
            scope = "Named studio" if access == "class-membership" else "Named location"

    commitment_type = text(gym.get("commitmentType"))
    minimum_months = gym.get("minimumCommitmentMonths")
    if not commitment_type:
        if any(phrase in value for phrase in ("month to month", "no commitment", "auto monthly", "recurring every 30 days")):
            commitment_type = "month-to-month"
        else:
            commitment_type = "unknown"
    return {
        "name": name,
        "accessScope": scope,
        "classAllowance": allowance,
        "commitmentType": commitment_type,
        "minimumCommitmentMonths": minimum_months,
    }


def legacy_plan_offer(gym: dict[str, Any], access: str) -> dict[str, Any] | None:
    monthly = gym.get("monthlyPrice")
    if monthly is None:
        return None
    inferred = infer_legacy_plan_metadata(gym, access)
    amount = gym.get("billingIntervalPrice") if gym.get("billingIntervalPrice") is not None else monthly
    return {
        "sourceProductId": "legacy-reviewed-selected",
        "name": inferred["name"],
        "productType": "class-membership" if access == "class-membership" else "membership",
        "accessScope": inferred["accessScope"],
        "classAllowance": inferred["classAllowance"],
        "amount": amount,
        "currency": "USD",
        "billingInterval": text(gym.get("billingInterval")) or "month",
        "intervalCount": 1,
        "commitmentType": inferred["commitmentType"],
        "minimumCommitmentMonths": inferred["minimumCommitmentMonths"],
        "fees": fee_list(gym),
        "rawLabel": text(gym.get("priceNote")),
    }


def monthly_class_count(plan: dict[str, Any]) -> float:
    allowance = plan.get("classAllowance") or {}
    if allowance.get("unlimited"):
        return math.inf
    count = allowance.get("count")
    if count is None:
        return math.inf
    period = normalized(allowance.get("period"))
    if period in {"week", "weekly"}:
        return float(count) * 52 / 12
    if period in {"four weeks", "4 weeks"}:
        return float(count) * 13 / 12
    return float(count)


def commitment_months(plan: dict[str, Any]) -> float:
    commitment = plan.get("commitment") or {}
    if commitment.get("minimumDays") is not None:
        return float(commitment["minimumDays"]) / 30.4375
    if commitment.get("minimumMonths") is not None:
        return float(commitment["minimumMonths"])
    return math.inf


def plan_is_eligible(plan: dict[str, Any]) -> bool:
    billing = plan.get("billing") or {}
    return (
        plan.get("productType") in {"membership", "class-membership"}
        and
        plan.get("availability") == "available"
        and plan.get("purchaseMethod") == "direct-public"
        and (plan.get("eligibility") or {}).get("type") == "standard-adult"
        and not (plan.get("promotion") or {}).get("isPromotion")
        and billing.get("normalizedMonthly") is not None
        and float(billing["normalizedMonthly"]) > 0
    )


def select_plan(plans: list[dict[str, Any]], access: str) -> tuple[dict[str, Any] | None, str]:
    eligible = [plan for plan in plans if plan_is_eligible(plan)]
    if not eligible:
        return None, "No standard-adult, direct-purchase recurring plan passed eligibility validation."
    month_to_month = [
        plan for plan in eligible if normalized((plan.get("commitment") or {}).get("type")) in {"month to month", "no commitment", "none"}
    ]
    pool = month_to_month or eligible
    unknown_commitment = False
    if not month_to_month:
        known_terms = [commitment_months(plan) for plan in pool if math.isfinite(commitment_months(plan))]
        if known_terms:
            shortest = min(known_terms)
            pool = [plan for plan in pool if math.isclose(commitment_months(plan), shortest)]
        else:
            unknown_commitment = True
    if access == "class-membership":
        qualifying = [plan for plan in pool if monthly_class_count(plan) >= 4]
        pool = qualifying or [plan for plan in pool if monthly_class_count(plan) > 0] or pool
        pool.sort(
            key=lambda plan: (
                monthly_class_count(plan),
                float(plan["billing"]["normalizedMonthly"]),
                0 if plan.get("scopeType") == "single-location" else 1,
            )
        )
    else:
        pool.sort(
            key=lambda plan: (
                float(plan["billing"]["normalizedMonthly"]),
                0 if plan.get("scopeType") == "single-location" else 1,
                len((plan.get("eligibility") or {}).get("restrictions", [])),
            )
        )
    selected = pool[0]
    reason = (
        "Selected the smallest eligible recurring class allowance of at least four classes per month."
        if access == "class-membership"
        else "Selected the cheapest eligible recurring plan permitting ordinary location use."
    )
    if unknown_commitment:
        reason += " The source did not publish a commitment term."
    elif not month_to_month:
        reason += " No month-to-month plan was available, so the shortest public commitment was used."
    return selected, reason


def select_plan_views(
    plans: list[dict[str, Any]], access: str, has_source_catalog: bool
) -> tuple[str | None, str | None, dict[str, dict[str, str]]]:
    """Select deterministic typical and highest-access views from a full catalog."""

    eligible = [plan for plan in plans if plan_is_eligible(plan)]
    incomplete_reason = "Alternative public plans have not been reconstructed from the source catalog."
    if not has_source_catalog:
        return None, None, {
            "typical": {"status": "unavailable-incomplete-catalog", "reason": incomplete_reason},
            "highestAccess": {"status": "unavailable-incomplete-catalog", "reason": incomplete_reason},
        }
    if not eligible:
        reason = "No eligible standard-adult recurring plan is available in the reviewed catalog."
        return None, None, {
            "typical": {"status": "not-applicable", "reason": reason},
            "highestAccess": {"status": "not-applicable", "reason": reason},
        }

    by_price = sorted(eligible, key=lambda plan: (float(plan["billing"]["normalizedMonthly"]), text(plan.get("id"))))
    typical = by_price[(len(by_price) - 1) // 2]

    def scope_rank(plan: dict[str, Any]) -> tuple[int, int]:
        """Rank only access breadth that the operator actually discloses.

        A binary single/multi-location rank made a 90-club plan look identical
        to a global or operator-specific expanded tier.  Preserve the explicit
        hierarchy without inventing amenity value: named expanded tiers first,
        then global, disclosed club count, national, regional, generic
        multi-location, and finally single-location access.
        """

        scope = normalized(f"{plan.get('name', '')} {plan.get('scopeType', '')} {plan.get('accessScope', '')}")
        counts = [int(value) for value in re.findall(r"\b(\d{2,4})\s*\+?\s*clubs?\b", scope)]
        disclosed_count = max(counts, default=0)
        if "destination west" in scope:
            return 7, disclosed_count
        if any(label in scope for label in ("global", "worldwide")):
            return 6, disclosed_count
        if disclosed_count:
            return 5, disclosed_count
        if any(label in scope for label in ("national", "north america")):
            return 4, 0
        if "regional" in scope:
            return 3, 0
        if any(label in scope for label in ("all location", "multi location", "all club", "all gym")):
            return 2, 0
        if any(label in scope for label in ("single location", "named location", "home location", "named studio", "one club")):
            return 1, 0
        return 0, 0

    def allowance_rank(plan: dict[str, Any]) -> float:
        allowance = plan.get("classAllowance") or {}
        if allowance.get("unlimited"):
            return math.inf
        if allowance.get("disclosed") and allowance.get("count") is not None:
            return monthly_class_count(plan)
        return -1.0

    if access == "class-membership":
        largest_allowance = max(allowance_rank(plan) for plan in eligible)
        allowance_pool = [plan for plan in eligible if allowance_rank(plan) == largest_allowance]
        largest_scope = max(scope_rank(plan) for plan in allowance_pool)
        highest = min(
            (plan for plan in allowance_pool if scope_rank(plan) == largest_scope),
            key=lambda plan: (float(plan["billing"]["normalizedMonthly"]), text(plan.get("id"))),
        )
    else:
        largest_scope = max(scope_rank(plan) for plan in eligible)
        highest = min(
            (plan for plan in eligible if scope_rank(plan) == largest_scope),
            key=lambda plan: (float(plan["billing"]["normalizedMonthly"]), text(plan.get("id"))),
        )
    return typical["id"], highest["id"], {
        "typical": {
            "status": "selected",
            "reason": "Median normalized monthly price among eligible recurring plans; even-sized catalogs resolve toward the lower price.",
        },
        "highestAccess": {
            "status": "selected",
            "reason": "Cheapest eligible plan with the largest disclosed allowance and scope.",
        },
    }


def select_best_value_plan(plans: list[dict[str, Any]], has_source_catalog: bool) -> tuple[str | None, dict[str, str]]:
    """Select only an operator-labeled best-value plan; never invent value scoring."""

    if not has_source_catalog:
        return None, {
            "status": "unavailable-incomplete-catalog",
            "reason": "Alternative public plans have not been reconstructed from the source catalog.",
        }
    labeled = [plan for plan in plans if plan_is_eligible(plan) and plan.get("bestValueLabel")]
    if not labeled:
        return None, {
            "status": "not-labeled-by-operator",
            "reason": "The operator did not publicly label an eligible plan as best value or most popular.",
        }
    selected = min(labeled, key=lambda plan: (float(plan["billing"]["normalizedMonthly"]), text(plan.get("id"))))
    return selected["id"], {
        "status": "selected",
        "reason": "The operator explicitly labeled this eligible plan as best value, most popular, or recommended.",
    }


COST_RANGE_RE = re.compile(
    r"(?P<label>[^.;\n]{0,100}?)\$(?P<low>\d{1,4}(?:\.\d{1,2})?)"
    r"\s*(?:[–—-]|\bto\b|\bthrough\b)\s*\$?(?P<high>\d{1,4}(?:\.\d{1,2})?)"
    r"\s*(?:/|per\s+)?(?P<cadence>session|class|visit|hour|month)?",
    re.IGNORECASE,
)
COST_START_RE = re.compile(
    r"(?P<label>[^.;\n]{0,100}?\b(?:starts?|starting)\s+(?:from|at)\s+)\$(?P<amount>\d{1,4}(?:\.\d{1,2})?)"
    r"\s*(?:/|per\s+)?(?P<cadence>session|class|visit|hour|month)?",
    re.IGNORECASE,
)


def normalized_cost_context_monthly(value: float, cadence: str) -> float | None:
    normalized_cadence = normalized(cadence)
    if normalized_cadence in {"month", "monthly", "per month"}:
        return round(value, 2)
    if normalized_cadence in {"4 weeks", "four weeks", "28 days", "every 4 weeks", "every four weeks"}:
        return round(value * 13 / 12, 2)
    if normalized_cadence in {"week", "weekly", "per week"}:
        return round(value * 52 / 12, 2)
    return None


def build_cost_context(gym: dict[str, Any], plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain official ranges/starting prices without promoting them to exact plans."""

    contexts: list[dict[str, Any]] = []
    explicit = gym.get("costContextOffers") if isinstance(gym.get("costContextOffers"), list) else []
    for item in explicit:
        if not isinstance(item, dict):
            continue
        low = item.get("low", item.get("amount"))
        high = item.get("high", low)
        if low is None or high is None:
            continue
        cadence = text(item.get("cadence")) or "unknown"
        context = {
            "kind": text(item.get("kind")) or ("range" if float(high) != float(low) else "starting-price"),
            "label": text(item.get("label")) or text(item.get("name")) or "Official cost context",
            "low": float(low), "high": float(high), "currency": text(item.get("currency")) or "USD",
            "cadence": cadence, "productType": text(item.get("productType")) or "service",
            "sourceUrl": text(item.get("sourceUrl")) or text(gym.get("priceSourceUrl")) or text(gym.get("officialUrl")),
            "observedAt": text(item.get("observedAt")) or text(gym.get("priceObservedAt")),
            "evidenceTier": text(item.get("evidenceTier")) or "official-public",
            "exactLocationMatch": text(item.get("exactLocationMatch")) or "exact-location",
            "conflictFlags": list(item.get("conflictFlags") or []),
            "note": text(item.get("note")),
            "selectable": False,
        }
        normalized_low = item.get("normalizedMonthlyLow")
        normalized_high = item.get("normalizedMonthlyHigh")
        context["normalizedMonthlyLow"] = (
            float(normalized_low) if normalized_low is not None else normalized_cost_context_monthly(float(low), cadence)
        )
        context["normalizedMonthlyHigh"] = (
            float(normalized_high) if normalized_high is not None else normalized_cost_context_monthly(float(high), cadence)
        )
        contexts.append(context)
    for plan in plans:
        amount = (plan.get("billing") or {}).get("amount")
        label = text(plan.get("name"))
        if amount is None or not re.search(r"\b(?:start(?:s|ing)?|from)\b", label, re.IGNORECASE):
            continue
        contexts.append({
            "kind": "starting-price", "label": label, "low": float(amount), "high": float(amount),
            "currency": text((plan.get("billing") or {}).get("currency")) or "USD",
            "cadence": text((plan.get("billing") or {}).get("interval")) or "unknown",
            "productType": text(plan.get("productType")) or "service",
            "sourceUrl": text((plan.get("evidence") or {}).get("url")),
            "observedAt": text((plan.get("evidence") or {}).get("observedAt")),
            "evidenceTier": "official-public", "selectable": False,
        })
    note = text(gym.get("priceNote"))
    source_url = text(gym.get("priceSourceUrl")) or text(gym.get("officialUrl"))
    observed_at = text(gym.get("priceObservedAt"))
    # Free-text parsing is a legacy fallback. Reviewed explicit contexts and
    # structured starting-price plans are authoritative and should not be
    # duplicated under a second, sentence-fragment label.
    if source_url and not contexts:
        for match in COST_RANGE_RE.finditer(note):
            low, high = float(match.group("low")), float(match.group("high"))
            if 0 < low <= high <= 10_000:
                contexts.append({
                    "kind": "range", "label": text(match.group("label")).strip(" :-") or "Official service range",
                    "low": low, "high": high, "currency": "USD", "cadence": text(match.group("cadence")) or "unknown",
                    "productType": "service", "sourceUrl": source_url, "observedAt": observed_at,
                    "evidenceTier": "official-public", "selectable": False,
                })
        for match in COST_START_RE.finditer(note):
            amount = float(match.group("amount"))
            if 0 < amount <= 10_000:
                contexts.append({
                    "kind": "starting-price", "label": text(match.group("label")).strip(" :-") or "Official starting price",
                    "low": amount, "high": amount, "currency": "USD", "cadence": text(match.group("cadence")) or "unknown",
                    "productType": "service", "sourceUrl": source_url, "observedAt": observed_at,
                    "evidenceTier": "official-public", "selectable": False,
                })
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float, str]] = set()
    for item in contexts:
        key = (normalized(item["label"]), item["low"], item["high"], item["cadence"])
        if key in seen:
            continue
        seen.add(key)
        item["id"] = f"{gym['id']}:cost-context:{hashlib.sha1('|'.join(map(str, key)).encode()).hexdigest()[:12]}"
        deduplicated.append(item)
    return deduplicated


def has_publishable_official_cost_context(gym: dict[str, Any]) -> bool:
    """Return true only for reviewed, nonconflicting official ranges."""

    if gym.get("officialPriceConflict") or normalized(gym.get("pricingAccess")) in {
        "official status conflict", "official price conflict",
    }:
        return False
    contexts = gym.get("costContext") if isinstance(gym.get("costContext"), list) else []
    if any(
        text(item.get("kind")) == "conflicting-price" or bool(item.get("conflictFlags"))
        for item in contexts if isinstance(item, dict)
    ):
        return False
    for item in contexts:
        if not isinstance(item, dict) or text(item.get("kind")) not in {"range", "starting-price"}:
            continue
        try:
            low, high = float(item.get("low")), float(item.get("high"))
        except (TypeError, ValueError):
            continue
        if (
            0 < low <= high <= 10_000
            and text(item.get("evidenceTier")) == "official-public"
            and text(item.get("sourceUrl")).startswith("https://")
            and bool(text(item.get("observedAt")))
            and item.get("selectable") is False
        ):
            return True
    return False


def has_publishable_official_specialized_service(gym: dict[str, Any]) -> bool:
    """Recognize exact public trainer-led services without inventing a day pass.

    Restricted appointment facilities can publish useful exact service prices,
    but those prices must remain outside the ordinary unrestricted day-pass
    compatibility field.
    """

    for plan in gym.get("plans", []):
        if not isinstance(plan, dict) or (plan.get("promotion") or {}).get("isPromotion"):
            continue
        billing = plan.get("billing") or {}
        evidence = plan.get("evidence") or {}
        eligibility = normalized((plan.get("eligibility") or {}).get("type"))
        try:
            amount = float(billing.get("amount"))
        except (TypeError, ValueError):
            continue
        if (
            amount > 0
            and normalized(billing.get("interval")) in {"one time", "one-time", "visit", "session"}
            and eligibility in {"trainer required", "restricted", "appointment required"}
            and text(evidence.get("evidenceTier")) == "official-public"
            and text(evidence.get("url")).startswith("https://")
            and bool(text(evidence.get("observedAt")))
        ):
            return True
    return False


def normalize_drop_in(gym: dict[str, Any], offer: dict[str, Any], index: int, access: str) -> dict[str, Any]:
    raw_amount = offer.get("amount")
    amount = float(raw_amount) if raw_amount is not None and text(raw_amount) else None
    raw_low, raw_high = offer.get("amountLow"), offer.get("amountHigh")
    amount_low = float(raw_low) if raw_low is not None and text(raw_low) else None
    amount_high = float(raw_high) if raw_high is not None and text(raw_high) else None
    raw_label = text(offer.get("rawLabel")) or text(offer.get("name")) or "Standard unrestricted single visit or class"
    return {
        "id": f"{gym['id']}:drop-in:{text(offer.get('sourceProductId')) or index}",
        "sourceProductId": text(offer.get("sourceProductId")),
        "name": text(offer.get("name")) or "Standard drop-in",
        "productType": "drop-in",
        "accessScope": text(offer.get("accessScope")) or "Single unrestricted visit or class",
        "amount": amount,
        "amountLow": amount_low,
        "amountHigh": amount_high,
        "currency": text(offer.get("currency")) or "USD",
        "eligibility": offer.get("eligibility") or {"type": "standard-adult", "restrictions": []},
        "promotion": offer.get("promotion") or {"isPromotion": False, "label": "", "expiresAt": None},
        "availability": text(offer.get("availability")) or ("presale" if gym.get("recordStatus") == "coming_soon" else "available"),
        "purchaseMethod": text(offer.get("purchaseMethod")) or "direct-public",
        "ordinaryUse": offer.get("ordinaryUse", True) is not False,
        "evidence": offer.get("evidence") or evidence_for_offer(gym, offer, raw_label),
        "selected": False,
        "selectionReason": "",
    }


def build_plan_catalog(gym: dict[str, Any], access: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, str | None, list[str]]:
    raw_plans = gym.get("planOffers") if isinstance(gym.get("planOffers"), list) else []
    may_synthesize_legacy = access not in {"restricted", "not-applicable"} and gym.get("entityKind") != "non-consumer"
    if not raw_plans and may_synthesize_legacy:
        legacy = legacy_plan_offer(gym, access)
        raw_plans = [legacy] if legacy else []
    plans = [normalize_plan_offer(gym, offer, index, access) for index, offer in enumerate(raw_plans) if offer]
    raw_drop_ins = gym.get("dropInOffers") if isinstance(gym.get("dropInOffers"), list) else []
    legacy_day_pass = gym.get("dayPassPrice")
    if (
        not raw_drop_ins
        and may_synthesize_legacy
        and isinstance(legacy_day_pass, (int, float))
        and legacy_day_pass > 0
    ):
        raw_drop_ins = [{"amount": legacy_day_pass, "name": "Standard drop-in"}]
    drop_ins = [normalize_drop_in(gym, offer, index, access) for index, offer in enumerate(raw_drop_ins)]
    selected, reason = select_plan(plans, access)
    if selected:
        selected["selected"] = True
        selected["selectionReason"] = reason
    eligible_drop_ins = [
        offer for offer in drop_ins
        if offer["availability"] == "available"
        and offer["purchaseMethod"] == "direct-public"
        and (offer.get("eligibility") or {}).get("type") == "standard-adult"
        and not (offer.get("promotion") or {}).get("isPromotion")
        and offer.get("ordinaryUse", True)
        and isinstance(offer.get("amount"), (int, float))
        and offer["amount"] > 0
    ]
    selected_drop = min(eligible_drop_ins, key=lambda offer: offer["amount"], default=None)
    if selected_drop:
        selected_drop["selected"] = True
        selected_drop["selectionReason"] = "Selected the ordinary unrestricted single visit or class."
    errors: list[str] = []
    if gym.get("officialPriceConflict"):
        errors.append("Current official sources contain an unresolved price conflict.")
    if gym.get("monthlyPrice") is not None and selected is None and access not in {"restricted", "not-applicable"} and gym.get("recordStatus") != "coming_soon":
        errors.append("A source price exists but no eligible recurring plan can be selected.")
    if selected and (not text((selected.get("evidence") or {}).get("url")) or not text((selected.get("evidence") or {}).get("observedAt"))):
        errors.append("Selected plan lacks an official source URL or observation date.")
    return plans, drop_ins, selected["id"] if selected else None, selected_drop["id"] if selected_drop else None, errors


def apply_approved_observations(gyms: list[dict[str, Any]], document: dict[str, Any]) -> int:
    by_id = {text(gym.get("id")): gym for gym in gyms}
    applied = 0
    allowed = (
        "monthlyPrice",
        "dayPassPrice",
        "annualFee",
        "enrollmentFee",
        "initiationFee",
        "processingFee",
        "activationFee",
        "billingInterval",
        "billingIntervalPrice",
        "planName",
        "planScope",
        "priceSource",
        "priceSourceUrl",
        "priceObservedAt",
        "priceNote",
        "commitmentType",
        "minimumCommitmentMonths",
        "planOffers",
        "dropInOffers",
        "catalogCompleteness",
        "costContextOffers",
        "officialPriceConflict",
        "officialTermsConflict",
    )
    for approval in document.get("approvals", []):
        gym = by_id.get(text(approval.get("gymId")))
        if gym is None:
            continue
        for field in allowed:
            if field in approval:
                gym[field] = approval[field]
        gym["freshness"] = "verified"
        applied += 1
    return applied


def apply_operator_catalog_approvals(gyms: list[dict[str, Any]], document: dict[str, Any]) -> int:
    """Apply one reviewed multi-location catalog to explicit, operator-matched targets.

    Operator identity alone is never enough to fan out a catalog. Each approval
    must enumerate its target location IDs, name the canonical operator ID, and
    carry current official evidence. Invalid scope fails the rebuild rather than
    publishing a catalog on the wrong branch.
    """

    by_id = {text(gym.get("id")): gym for gym in gyms}
    allowed = {
        "monthlyPrice",
        "dayPassPrice",
        "annualFee",
        "enrollmentFee",
        "initiationFee",
        "processingFee",
        "activationFee",
        "billingInterval",
        "billingIntervalPrice",
        "planName",
        "planScope",
        "priceSource",
        "priceSourceUrl",
        "priceObservedAt",
        "priceNote",
        "commitmentType",
        "minimumCommitmentMonths",
        "planOffers",
        "dropInOffers",
        "catalogCompleteness",
        "costContextOffers",
        "officialPriceConflict",
    }
    applied = 0
    for approval in document.get("approvals", []):
        if approval.get("reviewStatus") != "approved":
            continue
        operator_id = text(approval.get("operatorId"))
        gym_ids = [text(value) for value in approval.get("gymIds", []) if text(value)]
        fields = approval.get("sharedFields")
        if not operator_id or not gym_ids or len(gym_ids) != len(set(gym_ids)) or not isinstance(fields, dict):
            raise ValueError("Reviewed operator catalog requires operatorId, unique gymIds, and sharedFields.")
        if not any(gym_id in by_id for gym_id in gym_ids):
            # Unit tests and scoped audit runs may intentionally operate on an
            # unrelated subset. Once any approved target is in scope, require
            # the complete reviewed target set and fail closed below.
            continue
        unknown_fields = set(fields) - allowed
        if unknown_fields:
            raise ValueError(f"Unsupported operator catalog fields: {sorted(unknown_fields)}")
        source_url = text(fields.get("priceSourceUrl"))
        observed_at = text(fields.get("priceObservedAt"))
        if not source_url.startswith("https://") or not observed_at:
            raise ValueError("Reviewed operator catalog requires an HTTPS source and observation date.")
        targets: list[dict[str, Any]] = []
        for gym_id in gym_ids:
            gym = by_id.get(gym_id)
            if gym is None:
                raise ValueError(f"Unknown operator catalog target: {gym_id}")
            if text(gym.get("operatorId")) != operator_id:
                raise ValueError(
                    f"Operator catalog target {gym_id} belongs to {text(gym.get('operatorId'))!r}, not {operator_id!r}."
                )
            targets.append(gym)
        for gym in targets:
            for field, value in fields.items():
                gym[field] = copy.deepcopy(value)
            gym["operatorCatalogApprovalId"] = text(approval.get("id"))
            gym["freshness"] = "verified"
            applied += 1
    return applied


def apply_source_discoveries(gyms: list[dict[str, Any]], document: dict[str, Any]) -> int:
    by_id = {text(gym.get("id")): gym for gym in gyms}
    allowed = (
        "name",
        "address",
        "websiteUrl",
        "officialUrl",
        "operatorLocationId",
        "latitude",
        "longitude",
        "hours",
        "recordStatus",
        "accessAvailability",
        "pricingAccess",
        "entityKindOverride",
        "accessModelOverride",
        "modalityOverride",
    )
    applied = 0
    for discovery in document.get("discoveries", []):
        gym = by_id.get(text(discovery.get("gymId")))
        if gym is None or discovery.get("reviewStatus") != "approved":
            continue
        for field in allowed:
            if field in discovery:
                gym[field] = discovery[field]
        if text(discovery.get("address")):
            gym["canonicalAddress"] = canonical_address(discovery["address"])
        additions = [text(item) for item in discovery.get("amenities", []) if text(item)]
        if additions:
            gym["amenities"] = list(dict.fromkeys([*gym.get("amenities", []), *additions]))
        gym["discoveryEvidence"] = {
            "sourceUrl": text(discovery.get("sourceUrl")),
            "sourceType": text(discovery.get("sourceType")),
            "observedAt": text(discovery.get("observedAt")),
            "note": text(discovery.get("note")),
        }
        applied += 1
    return applied


def apply_location_metadata_approvals(gyms: list[dict[str, Any]], document: dict[str, Any]) -> int:
    by_id = {text(gym.get("id")): gym for gym in gyms}
    applied = 0
    for approval in document.get("approvals", []):
        gym = by_id.get(text(approval.get("gymId")))
        changes = approval.get("proposedChanges") if isinstance(approval.get("proposedChanges"), dict) else {}
        if (
            gym is None
            or approval.get("reviewStatus") != "approved"
            or not text(approval.get("sourceUrl"))
            or not text(approval.get("capturedAt"))
            or not text(approval.get("contentHash"))
        ):
            continue
        for field in ("name", "address", "hours"):
            if text(changes.get(field)):
                gym[field] = changes[field]
        if text(changes.get("address")):
            gym["canonicalAddress"] = canonical_address(changes["address"])
        additions = [text(item) for item in changes.get("amenities", []) if text(item)]
        if additions:
            gym["amenities"] = list(dict.fromkeys([*gym.get("amenities", []), *additions]))
        evidence = {
            "sourceUrl": approval["sourceUrl"],
            "capturedAt": approval["capturedAt"],
            "contentHash": approval["contentHash"],
            "method": "reviewed-json-ld-location",
        }
        unique_evidence: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in [*gym.get("locationEvidence", []), evidence]:
            key = (
                text(item.get("sourceUrl")), text(item.get("capturedAt")),
                text(item.get("contentHash")), text(item.get("method")),
            )
            unique_evidence[key] = item
        gym["locationEvidence"] = [unique_evidence[key] for key in sorted(unique_evidence)]
        applied += 1
    return applied


def normalized_reported_monthly(report: dict[str, Any]) -> float | None:
    amount = report.get("amount")
    if amount is None or float(amount) <= 0:
        return None
    cadence = normalized(report.get("cadence"))
    interval_count = int(report.get("intervalCount") or 1)
    if cadence in {"month", "monthly"}:
        return float(amount) / interval_count
    if cadence in {"week", "weekly"}:
        return float(amount) * 52 / (12 * interval_count)
    if cadence in {"biweekly", "two weeks", "2 weeks"}:
        return float(amount) * 26 / (12 * interval_count)
    if cadence in {"four weeks", "4 weeks"}:
        return float(amount) * 13 / (12 * interval_count)
    if cadence in {"year", "annual", "yearly"}:
        return float(amount) / (12 * interval_count)
    return None


def attach_reported_evidence(gyms: list[dict[str, Any]], document: dict[str, Any], generated_at: str) -> int:
    by_id = {text(gym.get("id")): gym for gym in gyms}
    generated_date = datetime.fromisoformat(generated_at).date()
    attached = 0
    for gym in gyms:
        gym["priceReports"] = []
        gym["reportedMonthly"] = None
    for report in document.get("reports", []):
        gym = by_id.get(text(report.get("gymId")))
        if gym is None:
            continue
        published = text(report.get("publishedAt"))
        try:
            age_days = (generated_date - datetime.fromisoformat(published).date()).days
        except ValueError:
            age_days = REPORTED_MAX_AGE_DAYS + 1
        normalized_monthly = normalized_reported_monthly(report) if report.get("productType") == "monthly" else None
        eligible = (
            0 <= age_days <= REPORTED_MAX_AGE_DAYS
            and report.get("identityMatch") == "exact-location"
            and report.get("eligibility") == "standard-adult"
            and report.get("reviewStatus") == "approved"
            and bool(text(report.get("sourceUrl")))
        )
        gym["priceReports"].append(
            {
                "id": text(report.get("id")),
                "productType": text(report.get("productType")),
                "amount": report.get("amount"),
                "currency": text(report.get("currency")) or "USD",
                "cadence": text(report.get("cadence")),
                "normalizedMonthly": round(normalized_monthly, 2) if normalized_monthly is not None else None,
                "publishedAt": published,
                "capturedAt": text(report.get("capturedAt")),
                "sourceUrl": text(report.get("sourceUrl")),
                "sourcePublisher": text(report.get("sourcePublisher")),
                "sourceType": text(report.get("sourceType")),
                "evidenceLabel": text(report.get("evidenceLabel"))[:220],
                "eligibleForSummary": eligible,
                "ageDays": age_days,
            }
        )
        attached += 1
    for gym in gyms:
        usable = [
            report
            for report in gym["priceReports"]
            if report["eligibleForSummary"] and report["productType"] == "monthly" and report["normalizedMonthly"] is not None
        ]
        independent = {report["sourceUrl"]: report for report in usable}
        if len(independent) < 2:
            continue
        values = sorted(float(report["normalizedMonthly"]) for report in independent.values())
        point = rounded_price(statistics.median(values))
        low, high = rounded_price(min(values)), rounded_price(max(values))
        spread = (max(values) - min(values)) / statistics.median(values) if statistics.median(values) else 1
        gym["reportedMonthly"] = {
            "point": point,
            "low": low,
            "high": high,
            "currency": "USD",
            "confidence": "high" if len(values) >= 3 and spread <= 0.2 else ("medium" if spread <= 0.2 else "low"),
            "conflict": spread > 0.2,
            "sourceCount": len(values),
            "newestPublishedAt": max(report["publishedAt"] for report in independent.values()),
            "basis": "Independent recent reports for this exact location",
            "version": REPORTED_PRICE_VERSION,
        }
    return attached


def attach_operator_confirmed(gyms: list[dict[str, Any]], document: dict[str, Any], generated_at: str) -> int:
    """Attach reviewed private operator confirmations without promoting them to public prices."""

    by_id = {text(gym.get("id")): gym for gym in gyms}
    generated_date = datetime.fromisoformat(generated_at).date()
    for gym in gyms:
        gym["operatorConfirmedMonthly"] = None
    attached = 0
    for approval in document.get("approvals", []):
        gym = by_id.get(text(approval.get("gymId")))
        if gym is None or approval.get("reviewStatus") != "approved":
            continue
        if approval.get("standardAdult") is not True or approval.get("confidential") is True:
            continue
        amount = approval.get("amount")
        normalized_value = normalized_monthly(
            float(amount), text(approval.get("cadence")) or "month", int(approval.get("intervalCount") or 1)
        ) if amount is not None and float(amount) > 0 else None
        confirmed_at = text(approval.get("confirmedAt"))
        evidence_id = text(approval.get("evidenceId"))
        if normalized_value is None or not confirmed_at or not evidence_id:
            continue
        try:
            age_days = (generated_date - datetime.fromisoformat(confirmed_at).date()).days
        except ValueError:
            continue
        gym["operatorConfirmedMonthly"] = {
            "amount": float(amount),
            "currency": text(approval.get("currency")) or "USD",
            "cadence": text(approval.get("cadence")) or "month",
            "intervalCount": int(approval.get("intervalCount") or 1),
            "normalizedMonthly": round(normalized_value, 2),
            "planName": text(approval.get("planName")),
            "accessScope": text(approval.get("accessScope")),
            "classAllowance": approval.get("classAllowance"),
            "commitment": approval.get("commitment") or {"type": "unknown"},
            "fees": approval.get("fees") if isinstance(approval.get("fees"), list) else [],
            "confirmedAt": confirmed_at,
            "contactMethod": text(approval.get("contactMethod")) or "email",
            "evidenceId": evidence_id,
            "freshness": "current" if 0 <= age_days <= OPERATOR_CONFIRMED_STALE_DAYS else "stale",
            "publiclyReproducible": False,
        }
        attached += 1
    return attached


def attach_deals(gyms: list[dict[str, Any]], document: dict[str, Any], generated_at: str) -> int:
    """Attach reviewed, current official promotions without changing ordinary prices."""

    by_id = {text(gym.get("id")): gym for gym in gyms}
    generated_date = datetime.fromisoformat(generated_at).date()
    for gym in gyms:
        gym["deals"] = []
    attached = 0
    for approval in document.get("approvals", []):
        gym = by_id.get(text(approval.get("gymId")))
        if gym is None or approval.get("reviewStatus") != "approved":
            continue
        if approval.get("standardAdult") is not True or approval.get("replacesOrdinaryPrice") is not False:
            continue
        amount = approval.get("amount")
        captured_at = text(approval.get("capturedAt"))
        source_url = text(approval.get("sourceUrl"))
        content_hash = text(approval.get("contentHash"))
        if amount is None or float(amount) <= 0 or not captured_at or not source_url or not content_hash:
            continue
        try:
            age_days = (generated_date - datetime.fromisoformat(captured_at).date()).days
        except ValueError:
            continue
        expires_at = text(approval.get("expiresAt"))
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at).date() < generated_date:
                    continue
            except ValueError:
                continue
        if not 0 <= age_days <= DEAL_STALE_DAYS:
            continue
        gym["deals"].append({
            "id": text(approval.get("id")) or hashlib.sha256(
                f"{gym['id']}|{source_url}|{content_hash}".encode()
            ).hexdigest()[:20],
            "label": text(approval.get("label"))[:220],
            "amount": float(amount),
            "currency": text(approval.get("currency")) or "USD",
            "productType": text(approval.get("productType")),
            "cadence": text(approval.get("cadence")),
            "eligibilityLabel": text(approval.get("eligibilityLabel")) or "Standard adult public offer",
            "sourceUrl": source_url,
            "capturedAt": captured_at,
            "expiresAt": expires_at or None,
            "contentHash": content_hash,
            "freshness": "current",
            "replacesOrdinaryPrice": False,
        })
        attached += 1
    for gym in gyms:
        gym["deals"].sort(key=lambda deal: (deal["expiresAt"] or "9999-12-31", deal["amount"], deal["label"]))
    return attached


def percentile(values: list[float], fraction: float) -> float:
    """Return a conservative nearest-rank empirical percentile.

    Linear interpolation narrows small-sample residual intervals between observed
    order statistics. Nearest-rank bounds retain actual leave-one-out residuals,
    which is the safer interpretation for the displayed 10th-90th percentile.
    """

    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


def rounded_price(value: float) -> float:
    return round(value / 5) * 5.0


def rounded_interval(value: float, *, upper: bool) -> float:
    """Round a displayed uncertainty bound outward so presentation cannot narrow it."""
    scaled = value / 5
    return (math.ceil(scaled) if upper else math.floor(scaled)) * 5.0


def estimate_from(values: Iterable[float], confidence: str, basis: str, generated_at: str) -> dict[str, Any]:
    sample = sorted(float(value) for value in values)
    point = rounded_price(statistics.median(sample))
    # Calibrate against the same $5-rounded point estimate shown publicly. Using
    # unrounded predictions here can overstate coverage after display rounding.
    predictions = [rounded_price(statistics.median(sample[:index] + sample[index + 1 :])) for index in range(len(sample))]
    errors = [abs(predicted - actual) / actual for predicted, actual in zip(predictions, sample, strict=True)]
    ratios = [actual / predicted for predicted, actual in zip(predictions, sample, strict=True) if predicted > 0]
    low_factor, high_factor = percentile(ratios, 0.10), percentile(ratios, 0.90)
    low, high = point * low_factor, point * high_factor
    if math.isclose(low, high):
        low, high = low * 0.9, high * 1.1
    coverage = statistics.fmean(
        1.0 if predicted * low_factor <= actual <= predicted * high_factor else 0.0
        for predicted, actual in zip(predictions, sample, strict=True)
    )
    return {
        "point": point,
        "low": rounded_interval(low, upper=False),
        "high": rounded_interval(high, upper=True),
        "currency": "USD",
        "confidence": confidence,
        "basis": basis,
        "sampleSize": len(sample),
        "generatedAt": generated_at,
        "estimatorVersion": ESTIMATOR_VERSION,
        "rangeMethod": "cross-validated-80-percent-residual-interval",
        "validationMedianAbsolutePercentageError": round(statistics.median(errors), 4),
        "validationRangeCoverage": round(coverage, 4),
    }


KNOWN_CHAIN_OPERATORS = {
    "24-hour-fitness", "planet-fitness", "crunch", "fitness-sf", "live-fit", "bay-club", "equinox",
    "orangetheory", "corepower-yoga", "f45", "barrys", "solidcore", "soulcycle", "pure-barre",
    "bodyrok", "core40", "mx3-fitness", "ymcasf.org",
}


def allowance_bucket(gym: dict[str, Any]) -> str:
    selected_id = text(gym.get("selectedPlanId"))
    selected = next((plan for plan in gym.get("plans", []) if plan.get("id") == selected_id), None)
    if not selected:
        return "unknown"
    count = monthly_class_count(selected)
    if math.isinf(count):
        return "unlimited-or-unmetered"
    if count <= 4:
        return "up-to-4"
    if count <= 8:
        return "5-to-8"
    return "9-plus"


def market_model(gym: dict[str, Any]) -> str:
    return "chain" if gym.get("operatorKey") in KNOWN_CHAIN_OPERATORS else "independent"


def modality_keys(gym: dict[str, Any]) -> tuple[str, str]:
    base = f"{gym['modality']}|{gym['accessModel']}|{market_model(gym)}"
    return f"{base}|{allowance_bucket(gym)}", base


def build_cohorts(
    gyms: list[dict[str, Any]], external: list[dict[str, Any]] | None = None
) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, list[float]]]:
    by_operator: dict[str, list[float]] = defaultdict(list)
    by_modality: dict[str, list[float]] = defaultdict(list)
    by_entity: dict[str, list[float]] = defaultdict(list)
    for gym in gyms:
        monthly = gym.get("monthlyPrice")
        if monthly is None or float(monthly) <= 0:
            continue
        if gym.get("publicationStatus") == "review-hold" or gym.get("planValidationErrors"):
            continue
        if gym.get("pricingStatus") in {"gated", "not-applicable"} or gym.get("accessModel") in {"restricted", "not-applicable"}:
            continue
        by_operator[gym["operatorKey"]].append(float(monthly))
        detailed_key, broad_key = modality_keys(gym)
        by_modality[detailed_key].append(float(monthly))
        by_modality[broad_key].append(float(monthly))
        by_entity[gym["entityKind"]].append(float(monthly))
    for item in external or []:
        if item.get("reviewStatus") != "approved" or item.get("evidenceTier") != "official-public":
            continue
        if item.get("exactLocationMatch") != "exact-location" or item.get("promotion") is True or item.get("restricted") is True:
            continue
        monthly = item.get("normalizedMonthly")
        if monthly is None or float(monthly) <= 0:
            continue
        operator = text(item.get("operatorKey"))
        detailed = "|".join((
            text(item.get("modality")), text(item.get("accessModel")), text(item.get("marketModel")), text(item.get("allowanceBucket")),
        ))
        broad = "|".join((text(item.get("modality")), text(item.get("accessModel")), text(item.get("marketModel"))))
        if operator:
            by_operator[operator].append(float(monthly))
        if all(text(item.get(key)) for key in ("modality", "accessModel", "marketModel", "allowanceBucket")):
            by_modality[detailed].append(float(monthly))
            by_modality[broad].append(float(monthly))
        if text(item.get("entityKind")):
            by_entity[text(item.get("entityKind"))].append(float(monthly))
    return by_operator, by_modality, by_entity


def estimate_for(
    gym: dict[str, Any],
    cohorts: tuple[dict[str, list[float]], dict[str, list[float]], dict[str, list[float]]],
    generated_at: str,
) -> dict[str, Any] | None:
    by_operator, by_modality, by_entity = cohorts
    operator_values = by_operator.get(gym["operatorKey"], [])
    if len(operator_values) >= 4:
        mean = statistics.fmean(operator_values)
        variation = statistics.pstdev(operator_values) / mean if mean else 1
        if variation <= 0.10:
            estimate = estimate_from(operator_values, "high", f"Comparable verified {gym['operatorKey']} locations in San Francisco and the Bay Area", generated_at)
            if estimate["validationMedianAbsolutePercentageError"] <= 0.10 and estimate["validationRangeCoverage"] >= 0.75:
                return estimate
    detailed_key, broad_key = modality_keys(gym)
    modality_values = by_modality.get(detailed_key, [])
    basis_key = detailed_key
    if len(modality_values) < 8:
        modality_values = by_modality.get(broad_key, [])
        basis_key = broad_key
    if len(modality_values) >= 8:
        estimate = estimate_from(modality_values, "medium", f"Comparable verified San Francisco and Bay Area cohort: {basis_key}", generated_at)
        if estimate["validationMedianAbsolutePercentageError"] <= 0.20 and estimate["validationRangeCoverage"] >= 0.75:
            return estimate
    return None


def leave_one_out_validation(gyms: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    results: dict[str, list[dict[str, float]]] = defaultdict(list)
    priced = [gym for gym in gyms if gym.get("monthlyPrice") is not None and float(gym["monthlyPrice"]) > 0]
    for target in priced:
        comparison = [gym for gym in gyms if gym is not target]
        estimate = estimate_for(target, build_cohorts(comparison), generated_at)
        if estimate is None:
            continue
        actual = float(target["monthlyPrice"])
        error = abs(float(estimate["point"]) - actual) / actual
        covered = float(estimate["low"]) <= actual <= float(estimate["high"])
        results[target["modality"]].append({"absolutePercentageError": error, "covered": 1.0 if covered else 0.0})
    summary: dict[str, Any] = {}
    all_rows: list[dict[str, float]] = []
    for label, rows in sorted(results.items()):
        all_rows.extend(rows)
        summary[label] = {
            "sampleSize": len(rows),
            "medianAbsolutePercentageError": round(statistics.median(row["absolutePercentageError"] for row in rows), 4),
            "rangeCoverage": round(statistics.fmean(row["covered"] for row in rows), 4),
        }
    summary["overall"] = {
        "sampleSize": len(all_rows),
        "medianAbsolutePercentageError": round(statistics.median(row["absolutePercentageError"] for row in all_rows), 4) if all_rows else None,
        "rangeCoverage": round(statistics.fmean(row["covered"] for row in all_rows), 4) if all_rows else None,
    }
    return summary


def estimate_passes_modality_validation(
    gym: dict[str, Any], estimate: dict[str, Any], validation: dict[str, Any]
) -> bool:
    """Fail closed when a modality has enough outer-fold evidence and misses its gate."""
    stats = validation.get(text(gym.get("modality")), {})
    minimum = 4 if estimate.get("confidence") == "high" else 8
    if int(stats.get("sampleSize") or 0) < minimum:
        # Small cohorts still have the estimate's internal leave-one-out gate;
        # do not invent an additional judgment from too few outer folds.
        return True
    error = stats.get("medianAbsolutePercentageError")
    range_coverage = stats.get("rangeCoverage")
    maximum_error = 0.10 if estimate.get("confidence") == "high" else 0.20
    return error is not None and error <= maximum_error and range_coverage is not None and range_coverage >= 0.75


def estimate_is_publishable(gym: dict[str, Any], estimate: dict[str, Any] | None) -> bool:
    """Allow estimates for withheld prices, never for unavailable or disputed access.

    A contact/form/account gate describes how an otherwise ordinary consumer
    facility withholds its exact amount.  It is not itself evidence that the
    facility is restricted.  The estimate remains separate from verified
    compatibility fields and still has to pass the cohort gates before this
    policy check runs.
    """

    if estimate is None:
        return False
    if gym.get("publicationStatus") == "review-hold" or gym.get("planValidationErrors"):
        return False
    if normalized(gym.get("pricingAccess")) in {"official status conflict", "official price conflict"}:
        return False
    if normalized(gym.get("recordStatus")) in {"closed", "coming soon", "conflict", "presale", "status review"}:
        return False
    if normalized(gym.get("accessAvailability")) in {"waitlist", "enrollment paused", "members only", "presale"}:
        return False
    if gym.get("accessModel") in {"free-public", "restricted", "not-applicable"}:
        return False
    if gym.get("entityKind") not in {"gym", "studio", "martial-arts"}:
        return False
    return True


def blocker_for(gym: dict[str, Any]) -> str:
    if gym.get("recordStatus") == "coming_soon":
        return "Location is not open yet and does not publish a neutral ongoing price."
    pricing_access = normalized(gym.get("pricingAccess"))
    discovery_blockers = {
        "official domain parked": "The business's former official domain is parked or no longer contains an operator website.",
        "official domain disconnected": "The business's official domain is disconnected and no current operator storefront was found.",
        "official site dns failure": "The linked operator domain no longer resolves and no replacement official site was verified.",
        "official site blocked": "The official site blocks public automated access and exposes no accessible public pricing storefront.",
        "official site placeholder": "The current official site is only a placeholder and exposes no services, schedule, or rate card.",
        "no official site": "No current official operator website or public booking storefront was verified for this location.",
        "official status conflict": "Current official operator and government sources disagree about whether this location is operating.",
        "official price conflict": "Current official pages contain conflicting price or contract terms that require review.",
    }
    if pricing_access in discovery_blockers:
        return discovery_blockers[pricing_access]
    website = text(gym.get("websiteUrl"))
    note = text(gym.get("priceNote"))
    if "did not publish" in note.casefold() or "not publicly" in note.casefold():
        return "Official public pages do not disclose a comparable recurring price."
    if not website or is_osm_url(website):
        return "An official pricing page has not yet been discovered from the source listing."
    return "The public operator page or linked storefront does not expose a safely comparable recurring price."


def monthly_price_blocker(gym: dict[str, Any]) -> str:
    """Explain every absent verified monthly compatibility price."""

    if gym.get("monthlyPrice") is not None:
        return ""
    status = text(gym.get("pricingStatus"))
    if status == "estimated":
        return "No verified recurring price was published; the displayed monthly amount is a separately labeled validated estimate."
    if status == "reported":
        return "No verified recurring price was published; the displayed amount is based only on separately labeled recent reports."
    if status == "operator-confirmed":
        return "No reproducible public recurring price was published; the displayed amount was confirmed privately by the operator."
    return text(gym.get("pricingBlocker")) or blocker_for(gym)


def day_pass_price_blocker(gym: dict[str, Any]) -> str:
    """Explain every absent ordinary unrestricted visit/class price."""

    if gym.get("dayPassPrice") is not None:
        return ""
    status = text(gym.get("pricingStatus"))
    if status == "free":
        return "This facility is free to use, so a paid day pass does not apply."
    if status == "not-applicable":
        return "This is not a generally purchasable consumer facility, so an ordinary day pass does not apply."
    if gym.get("recordStatus") == "coming_soon":
        return "This location is not open yet, so no ordinary visit price is available."
    if gym.get("accessAvailability") in {"waitlist", "enrollment-paused", "members-only", "presale"}:
        return "Ordinary public visits are not currently available."
    if status == "pay-per-visit":
        return "The official source lists packages, reservations, or specialized sessions but no ordinary unrestricted single visit."
    if status == "gated":
        return "The operator does not publish an ordinary unrestricted single-visit price without contact, a form, or account access."
    if status == "unresolved":
        return "No current ordinary unrestricted single-visit price could be verified from the available public evidence."
    return "The official public source did not publish an ordinary unrestricted single visit or class price."


def hours_metadata_status(gym: dict[str, Any]) -> dict[str, str]:
    """Distinguish exact hours, schedule-only access, and genuine metadata gaps."""

    hours = text(gym.get("hours"))
    lowered = hours.casefold()
    non_consumer = gym.get("entityKind") == "non-consumer"
    if hours in {"", "Hours not listed"}:
        if non_consumer:
            return {
                "status": "not-applicable",
                "reason": "Public consumer hours do not apply to this restricted or non-consumer facility.",
            }
        return {
            "status": "not-published",
            "reason": "No current visit hours were published in the reviewed sources.",
        }
    if re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b", lowered) or re.search(
        r"\b(?:24\s*hours|24\s*/\s*7)\b", lowered
    ):
        return {"status": "exact-hours", "reason": ""}
    access_terms = (
        "appointment",
        "booking",
        "calendar",
        "class",
        "event",
        "key-fob",
        "program",
        "reservation",
        "reserve",
        "schedule",
        "scheduled",
        "self-access",
        "session",
        "waitlist",
    )
    if any(term in lowered for term in access_terms):
        return {
            "status": "access-schedule",
            "reason": "The operator publishes appointment, reservation, class, or program access rather than fixed walk-in hours.",
        }
    if any(
        phrase in lowered
        for phrase in ("not published", "not stated", "not available", "inaccessible", "could not be recovered")
    ):
        return {
            "status": "not-published",
            "reason": "The reviewed official source did not publish a current visit schedule.",
        }
    return {"status": "listed", "reason": ""}


def metadata_status(gym: dict[str, Any]) -> dict[str, Any]:
    """Make non-price gaps explicit instead of silently relying on empty strings."""

    amenities_listed = bool(gym.get("amenities"))
    official_url_listed = bool(text(gym.get("officialUrl")))
    equipment = gym.get("entityKind") == "outdoor-equipment"
    non_consumer = gym.get("entityKind") == "non-consumer"
    return {
        "officialUrl": {
            "status": "listed" if official_url_listed else ("not-applicable" if equipment else "not-found"),
            "reason": "" if official_url_listed else (
                "An individual outdoor exercise-equipment node does not normally have an operator page."
                if equipment else "No authoritative current operator or facility page was verified."
            ),
        },
        "hours": hours_metadata_status(gym),
        "amenities": {
            "status": "listed" if amenities_listed else ("not-applicable" if non_consumer else "not-published"),
            "reason": "" if amenities_listed else (
                "Consumer amenity details do not apply to this restricted or non-consumer facility."
                if non_consumer else "No reliable amenity list was published in the reviewed sources."
            ),
        },
        "operatorLocationId": {
            "status": "listed" if text(gym.get("operatorLocationId")) else "not-published",
            "reason": "" if text(gym.get("operatorLocationId")) else "The operator did not expose a stable public location identifier.",
        },
    }


def listing_description(gym: dict[str, Any]) -> str:
    """Build a factual, record-specific summary exclusively from reviewed fields."""

    kind_labels = {
        "gym": "gym",
        "studio": "fitness studio",
        "martial-arts": "martial-arts school",
        "public-recreation": "public recreation facility",
        "outdoor-equipment": "outdoor exercise facility",
        "non-consumer": "restricted or non-consumer fitness facility",
    }
    modality_labels = {
        "budget-full-service": "full-service fitness",
        "independent-gym": "independent fitness",
        "pilates-lagree-barre": "Pilates, Lagree, or barre",
        "yoga": "yoga",
        "crossfit-strength": "coached strength and conditioning",
        "martial-arts-boxing": "martial arts or boxing",
        "interval-studio": "interval training",
        "functional-hiit-studio": "functional and high-intensity training",
        "cycling-rowing-studio": "cycling or rowing",
        "personal-training": "personal training",
        "public-recreation": "public recreation",
        "outdoor-fitness": "outdoor fitness",
        "institutional-recreation": "institutional recreation",
    }
    entity_kind = text(gym.get("entityKind"))
    kind = kind_labels.get(entity_kind, "fitness listing")
    modality = modality_labels.get(text(gym.get("modality")), text(gym.get("modality")).replace("-", " "))
    if entity_kind in {"public-recreation", "outdoor-equipment", "non-consumer"}:
        modality = ""
    neighborhood = text(gym.get("neighborhood"))
    first = f"{gym['name']} is a {modality + ' ' if modality else ''}{kind}"
    first += f" in {neighborhood}." if neighborhood else "."

    status = text(gym.get("pricingStatus"))
    if gym.get("monthlyPrice") is not None:
        price_sentence = f"The selected official recurring plan is ${float(gym['monthlyPrice']):.2f} per month."
    elif gym.get("dayPassPrice") is not None:
        price_sentence = f"The selected official ordinary visit or class price is ${float(gym['dayPassPrice']):.2f}."
    elif status == "estimated" and gym.get("estimatedMonthly"):
        estimate = gym["estimatedMonthly"]
        price_sentence = f"No exact recurring price is public; the validated estimate is about ${float(estimate['point']):.0f} per month."
    elif status == "free":
        price_sentence = "It is classified as free public access rather than a paid membership."
    elif status == "pay-per-visit":
        price_sentence = "It uses packages, reservations, or pay-per-visit access rather than a verified recurring membership."
    elif status == "not-applicable":
        price_sentence = "It is not classified as a generally purchasable consumer membership."
    elif status == "operator-confirmed" and gym.get("operatorConfirmedMonthly"):
        price_sentence = f"The operator privately confirmed a standard recurring price of ${float(gym['operatorConfirmedMonthly']['normalizedMonthly']):.2f} per month."
    elif status == "gated":
        price_sentence = "Current consumer pricing is not publicly disclosed without additional access or direct contact."
    else:
        price_sentence = "Current consumer pricing still needs confirmation from public evidence."

    amenities = [text(item) for item in gym.get("amenities", []) if text(item).casefold() != "website listed"][:3]
    amenity_sentence = f"Listed features include {', '.join(amenities)}." if amenities else "No reliable amenity list has been published yet."
    return " ".join((first, price_sentence, amenity_sentence))


def identity_duplicate_audit(gyms: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Separate true same-operator/address collisions from reviewed co-location."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for gym in gyms:
        operator = text(gym.get("operatorId")) or text(gym.get("operatorKey"))
        address = canonical_address(gym.get("canonicalAddress") or gym.get("address"))
        if operator and address and re.search(r"\d", address):
            grouped[(operator, address)].append(gym)
    duplicates: list[dict[str, Any]] = []
    distinct_colocations: list[dict[str, Any]] = []
    for (operator, address), records in sorted(grouped.items()):
        if len(records) < 2:
            continue
        location_ids = [text(record.get("operatorLocationId")) for record in records]
        item = {
            "operatorId": operator,
            "canonicalAddress": address,
            "gymIds": [text(record.get("id")) for record in records],
            "operatorLocationIds": location_ids,
        }
        if all(location_ids) and len(set(location_ids)) == len(location_ids):
            distinct_colocations.append(item)
        else:
            duplicates.append(item)
    return {"duplicates": duplicates, "distinctCoLocations": distinct_colocations}


def verified_price_is_stale(gym: dict[str, Any], generated_at: str) -> bool:
    observed = text(gym.get("priceObservedAt"))
    if not observed:
        return True
    try:
        observed_date = datetime.fromisoformat(observed).date()
        generated_date = datetime.fromisoformat(generated_at).date()
    except ValueError:
        return True
    return (generated_date - observed_date).days > 35


def enrich_document(document: dict[str, Any], generated_at: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    gyms = [dict(gym) for gym in document.get("gyms", [])]
    discovery_documents = [
        json.loads(SOURCE_DISCOVERIES_PATH.read_text(encoding="utf-8"))
        if SOURCE_DISCOVERIES_PATH.exists()
        else {"discoveries": []}
    ]
    discovery_documents.extend(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE_DISCOVERIES_PATH.parent.glob(METADATA_RECOVERY_GLOB))
    )
    discoveries_document = {
        "discoveries": [
            item
            for source_document in discovery_documents
            for item in source_document.get("discoveries", [])
        ]
    }
    discoveries_applied = apply_source_discoveries(gyms, discoveries_document)
    location_approvals_document = json.loads(LOCATION_METADATA_APPROVED_PATH.read_text(encoding="utf-8")) if LOCATION_METADATA_APPROVED_PATH.exists() else {"approvals": []}
    location_metadata_applied = apply_location_metadata_approvals(gyms, location_approvals_document)
    approvals_document = json.loads(APPROVED_OBSERVATIONS_PATH.read_text(encoding="utf-8")) if APPROVED_OBSERVATIONS_PATH.exists() else {"approvals": []}
    approvals_applied = apply_approved_observations(gyms, approvals_document)
    operator_catalog_document = json.loads(OPERATOR_CATALOG_APPROVED_PATH.read_text(encoding="utf-8")) if OPERATOR_CATALOG_APPROVED_PATH.exists() else {"approvals": []}
    operator_catalog_locations_applied = apply_operator_catalog_approvals(gyms, operator_catalog_document)
    reports_document = json.loads(REPORTED_EVIDENCE_PATH.read_text(encoding="utf-8")) if REPORTED_EVIDENCE_PATH.exists() else {"reports": []}
    reported_evidence_attached = attach_reported_evidence(gyms, reports_document, generated_at)
    operator_confirmed_document = json.loads(OPERATOR_CONFIRMED_PATH.read_text(encoding="utf-8")) if OPERATOR_CONFIRMED_PATH.exists() else {"approvals": []}
    operator_confirmed_attached = attach_operator_confirmed(gyms, operator_confirmed_document, generated_at)
    deal_approved_document = json.loads(DEAL_APPROVED_PATH.read_text(encoding="utf-8")) if DEAL_APPROVED_PATH.exists() else {"approvals": []}
    approved_deals_attached = attach_deals(gyms, deal_approved_document, generated_at)
    public_discovery_document = json.loads(PUBLIC_DISCOVERY_OBSERVATIONS_PATH.read_text(encoding="utf-8")) if PUBLIC_DISCOVERY_OBSERVATIONS_PATH.exists() else {}
    manual_source_search_document = json.loads(MANUAL_SOURCE_SEARCH_PATH.read_text(encoding="utf-8")) if MANUAL_SOURCE_SEARCH_PATH.exists() else {"records": []}
    reported_audit_document = json.loads(REPORTED_EVIDENCE_AUDIT_PATH.read_text(encoding="utf-8")) if REPORTED_EVIDENCE_AUDIT_PATH.exists() else {}
    comparable_document = json.loads(OFFICIAL_COMPARABLES_PATH.read_text(encoding="utf-8")) if OFFICIAL_COMPARABLES_PATH.exists() else {"observations": []}
    external_comparables = comparable_document.get("observations", []) if isinstance(comparable_document.get("observations"), list) else []
    crawl_document = json.loads(CRAWL_ATTEMPTS_PATH.read_text(encoding="utf-8")) if CRAWL_ATTEMPTS_PATH.exists() else {"attempts": []}
    rendered_crawl_document = json.loads(RENDERED_CRAWL_ATTEMPTS_PATH.read_text(encoding="utf-8")) if RENDERED_CRAWL_ATTEMPTS_PATH.exists() else {"attempts": []}
    crawl_attempts = crawl_document.get("attempts", []) + rendered_crawl_document.get("attempts", [])
    overrides_document = json.loads(LOCATION_OVERRIDES_PATH.read_text(encoding="utf-8")) if LOCATION_OVERRIDES_PATH.exists() else {"overrides": []}
    suppressed_aliases = [
        {
            "id": text(item.get("id")),
            "reason": text(item.get("reason")),
            "sourceUrl": text(item.get("sourceUrl")),
        }
        for item in overrides_document.get("overrides", [])
        if item.get("action") in {"suppress", "merge"}
    ]
    attempts_by_gym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in crawl_attempts:
        attempts_by_gym[text(attempt.get("gymId"))].append(attempt)
    discovery_by_gym = {
        text(item.get("gymId")): item
        for item in public_discovery_document.get("observations", [])
        if text(item.get("gymId"))
    }
    manual_search_by_gym = {
        text(item.get("gymId")): item
        for item in manual_source_search_document.get("records", [])
        if text(item.get("gymId"))
    }
    for gym in gyms:
        website = text(gym.get("websiteUrl"))
        reviewed_price_url = text(gym.get("priceSourceUrl"))
        gym["officialUrl"] = (
            text(gym.get("officialUrl"))
            or (website if website and not is_osm_url(website) else "")
            or (reviewed_price_url if reviewed_price_url.startswith(("http://", "https://")) and not is_osm_url(reviewed_price_url) else "")
        )
        if not website and text(gym.get("officialUrl")):
            gym["websiteUrl"] = text(gym.get("officialUrl"))
            website = gym["websiteUrl"]
        gym["amenities"] = list(
            dict.fromkeys(
                text(item)
                for item in gym.get("amenities", [])
                if text(item) and normalized(item) not in {"website listed", "hours listed"}
            )
        )
        hours = text(gym.get("hours"))
        if len(hours) >= 2 and hours[0] == hours[-1] == '"':
            gym["hours"] = hours[1:-1]
        gym["operatorLocationId"] = text(gym.get("operatorLocationId"))
        existing_kind = text(gym.get("entityKind"))
        kind = text(gym.get("entityKindOverride")) or (existing_kind if existing_kind in ENTITY_KINDS else "") or classify_entity(gym)
        gym["entityKind"] = kind
        gym["modality"] = text(gym.get("modalityOverride")) or text(gym.get("modality")) or modality(gym, kind)
        gym["operatorKey"] = operator_key(gym)
        existing_access = text(gym.get("accessModel"))
        gym["accessModel"] = text(gym.get("accessModelOverride")) or (existing_access if existing_access in ACCESS_MODELS else "") or access_model(gym, kind)
        gym["selectionRuleVersion"] = SELECTION_RULE_VERSION
        has_source_plan_offers = isinstance(gym.get("planOffers"), list) and bool(gym.get("planOffers"))
        has_source_drop_in_offers = isinstance(gym.get("dropInOffers"), list) and bool(gym.get("dropInOffers"))
        completeness = gym.get("catalogCompleteness") if isinstance(gym.get("catalogCompleteness"), dict) else {}
        plan_completeness = text(completeness.get("plans")) or ("complete" if has_source_plan_offers else "none-observed")
        drop_in_completeness = text(completeness.get("dropIns")) or ("complete" if has_source_drop_in_offers else "none-observed")
        has_source_plan_catalog = has_source_plan_offers and plan_completeness == "complete"
        has_source_drop_in_catalog = has_source_drop_in_offers and drop_in_completeness == "complete"
        plans, drop_ins, selected_plan_id, selected_drop_in_id, plan_errors = build_plan_catalog(gym, gym["accessModel"])
        gym["plans"] = plans
        gym["dropIns"] = drop_ins
        gym["selectedPlanId"] = selected_plan_id
        gym["selectedDropInId"] = selected_drop_in_id
        typical_plan_id, highest_access_plan_id, plan_view_status = select_plan_views(
            plans, gym["accessModel"], has_source_plan_catalog
        )
        gym["typicalPlanId"] = typical_plan_id
        gym["highestAccessPlanId"] = highest_access_plan_id
        best_value_plan_id, best_value_status = select_best_value_plan(plans, has_source_plan_catalog)
        gym["bestValuePlanId"] = best_value_plan_id
        plan_view_status["bestValue"] = best_value_status
        gym["planViewStatus"] = plan_view_status
        gym["costContext"] = build_cost_context(gym, plans)
        selected_plan = next((plan for plan in plans if plan["id"] == selected_plan_id), None)
        selected_drop_in = next((offer for offer in drop_ins if offer["id"] == selected_drop_in_id), None)
        gym["selectionReason"] = selected_plan.get("selectionReason", "") if selected_plan else ""
        gym["planValidationErrors"] = plan_errors
        gym["catalogStatus"] = {
            "plans": {
                "status": (
                    "source-catalog"
                    if has_source_plan_catalog
                    else "source-fragment"
                    if has_source_plan_offers
                    else "selected-only"
                    if plans
                    else "none"
                ),
                "reason": (
                    "Reviewed source offers are retained before deterministic selection."
                    if has_source_plan_catalog
                    else "Reviewed source offers are retained, but completeness of the public catalog has not been established."
                    if has_source_plan_offers
                    else (
                        "Only the reviewed selected price was available in the legacy input; alternative plans still require source reconstruction."
                        if plans
                        else "No public recurring or package catalog was verified."
                    )
                ),
            },
            "dropIns": {
                "status": (
                    "source-catalog"
                    if has_source_drop_in_catalog
                    else "source-fragment"
                    if has_source_drop_in_offers
                    else "selected-only"
                    if drop_ins
                    else "none"
                ),
                "reason": (
                    "Reviewed public visit or class offers are retained before deterministic selection."
                    if has_source_drop_in_catalog
                    else "Reviewed visit or class offers are retained, but completeness of the public catalog has not been established."
                    if has_source_drop_in_offers
                    else (
                        "Only the reviewed selected visit price was available in the legacy input; alternative visit products still require source reconstruction."
                        if drop_ins
                        else "No ordinary public single-visit or single-class catalog was verified."
                    )
                ),
            },
        }
        gym["monthlyPrice"] = selected_plan["billing"]["normalizedMonthly"] if selected_plan else None
        gym["dayPassPrice"] = selected_drop_in["amount"] if selected_drop_in else None
        for field in ("annualFee", "enrollmentFee", "initiationFee", "processingFee", "activationFee"):
            gym[field] = None
        fee_field = {
            "annual": "annualFee",
            "enrollment": "enrollmentFee",
            "initiation": "initiationFee",
            "processing": "processingFee",
            "activation": "activationFee",
        }
        if selected_plan:
            for fee in selected_plan.get("fees", []):
                field = fee_field.get(text(fee.get("type")))
                if field and fee.get("mandatory") and fee.get("amount") is not None:
                    gym[field] = fee["amount"]
        if gym.get("publicationStatus") == "review-hold":
            gym["planValidationErrors"].append("Location identity is held for reviewed canonical reconciliation.")
            gym["monthlyPrice"] = None
            gym["dayPassPrice"] = None
        if gym["accessModel"] in {"restricted", "not-applicable"} or gym["entityKind"] == "non-consumer":
            gym["monthlyPrice"] = None
            gym["dayPassPrice"] = None
        gym["estimatedMonthly"] = None
        gym["pricingBlocker"] = ""

    cohorts = build_cohorts(gyms, external_comparables)
    estimator_validation = leave_one_out_validation(gyms, generated_at)
    approved_source_discovery_ids = {
        text(item.get("gymId"))
        for item in discoveries_document.get("discoveries", [])
        if item.get("reviewStatus") == "approved" and text(item.get("gymId"))
    }
    review: list[dict[str, Any]] = []
    for gym in gyms:
        estimate_rejected = False
        forced_unresolved = normalized(gym.get("pricingAccess")) in {"official status conflict", "official price conflict"}
        held_or_invalid = gym.get("publicationStatus") == "review-hold" or bool(gym.get("planValidationErrors"))
        if gym.get("publicationStatus") == "review-hold" or gym.get("planValidationErrors"):
            gym["pricingStatus"] = "unresolved"
            gym["pricingBlocker"] = "; ".join(gym.get("planValidationErrors", [])) or "Identity or official pricing requires review."
            gym["monthlyPrice"] = None
            gym["dayPassPrice"] = None
            estimate = None
        else:
            estimate = None if forced_unresolved else estimate_for(gym, cohorts, generated_at)
            if estimate is not None and not estimate_passes_modality_validation(gym, estimate, estimator_validation):
                estimate = None
                estimate_rejected = True
        if gym["accessModel"] == "free-public":
            gym["pricingStatus"] = "free"
            gym["pricingBlocker"] = "Monthly membership is not applicable to this free public facility."
            continue
        if gym["entityKind"] == "non-consumer" or gym["accessModel"] == "not-applicable":
            gym["pricingStatus"] = "not-applicable"
            gym["pricingBlocker"] = "This is not a generally purchasable public gym membership."
            continue
        if gym["accessModel"] == "restricted":
            if (gym.get("operatorConfirmedMonthly") or {}).get("freshness") == "current":
                gym["pricingStatus"] = "operator-confirmed"
                gym["pricingBlocker"] = "The operator confirmed a current standard-adult price privately; no reproducible public checkout price is available."
            elif not held_or_invalid and has_publishable_official_cost_context(gym):
                gym["pricingStatus"] = "official-range"
                gym["estimatedMonthly"] = None
                gym["pricingBlocker"] = "The operator publishes a current official range or starting price, but trainer, service, or eligibility choices prevent selection of one exact standard-adult scalar."
            elif not held_or_invalid and has_publishable_official_specialized_service(gym):
                gym["pricingStatus"] = "pay-per-visit"
                gym["estimatedMonthly"] = None
                gym["pricingBlocker"] = "The operator publishes an exact trainer-led appointment price, but it is not an ordinary unrestricted gym or class drop-in."
            elif gym["entityKind"] in {"gym", "studio", "martial-arts"} and gym.get("pricingAccess") in {"account-required", "contact-required", "form-required"}:
                gym["pricingStatus"] = "gated"
                gym["pricingBlocker"] = "Access requires a trainer, appointment, invitation, or eligibility check, and the exact consumer rate is not publicly purchasable."
            elif gym["entityKind"] in {"gym", "studio", "martial-arts"} and gym.get("pricingAccess") in {
                "official-content-unavailable",
                "official-domain-disconnected",
                "official-domain-parked",
                "official-site-placeholder",
            }:
                gym["pricingStatus"] = "unresolved"
                gym["pricingBlocker"] = blocker_for(gym)
            else:
                gym["pricingStatus"] = "not-applicable"
                gym["pricingBlocker"] = "This restricted facility does not offer an ordinary public membership or unrestricted visit."
            continue
        if gym["entityKind"] == "public-recreation" and gym["accessModel"] == "drop-in":
            gym["pricingStatus"] = "pay-per-visit"
            gym["pricingBlocker"] = "This facility uses visit or reservation pricing rather than a comparable monthly membership."
            continue
        if forced_unresolved:
            gym["pricingStatus"] = "unresolved"
            gym["estimatedMonthly"] = None
            gym["monthlyPrice"] = None
            gym["dayPassPrice"] = None
            gym["pricingBlocker"] = blocker_for(gym)
            continue
        if gym.get("recordStatus") == "coming_soon" or gym.get("accessAvailability") in {
            "waitlist",
            "enrollment-paused",
            "members-only",
            "presale",
        }:
            gym["pricingStatus"] = "gated"
            gym["estimatedMonthly"] = None
            gym["pricingBlocker"] = (
                "This location is not open for ordinary enrollment yet."
                if gym.get("recordStatus") == "coming_soon"
                else "The operator currently limits or pauses ordinary enrollment."
            )
            continue
        eligible_class_packs = [
            plan for plan in gym.get("plans", [])
            if plan.get("productType") == "class-pack"
            and (plan.get("eligibility") or {}).get("type") == "standard-adult"
            and not (plan.get("promotion") or {}).get("isPromotion")
            and (plan.get("billing") or {}).get("amount") is not None
        ]
        if gym["accessModel"] == "class-pack" and eligible_class_packs:
            gym["pricingStatus"] = "pay-per-visit"
            gym["pricingBlocker"] = "Official public class, session, or term packages are available, but no eligible recurring membership was verified."
            continue
        if not held_or_invalid and gym.get("monthlyPrice") is not None and gym.get("selectedPlanId"):
            gym["pricingStatus"] = "verified"
            if verified_price_is_stale(gym, generated_at):
                gym["freshness"] = "stale"
            continue
        if not held_or_invalid and gym.get("dayPassPrice") is not None and gym.get("selectedDropInId"):
            gym["pricingStatus"] = "pay-per-visit"
            gym["pricingBlocker"] = "An official unrestricted visit or class price is available, but no eligible recurring membership was verified."
            continue
        if not held_or_invalid and (gym.get("operatorConfirmedMonthly") or {}).get("freshness") == "current":
            gym["pricingStatus"] = "operator-confirmed"
            gym["estimatedMonthly"] = None
            gym["pricingBlocker"] = "The operator confirmed a current standard-adult price privately; no reproducible public checkout price is available."
            continue
        if not held_or_invalid and has_publishable_official_cost_context(gym):
            gym["pricingStatus"] = "official-range"
            gym["estimatedMonthly"] = None
            gym["pricingBlocker"] = "The operator publishes a current official range or starting price, but no single standard-adult scalar can be selected without inventing precision."
            continue
        if not held_or_invalid and gym.get("pricingAccess") in {"account-required", "contact-required", "form-required"}:
            exact_price_blocker = {
                "account-required": "The operator discloses location pricing only after authentication or account creation.",
                "contact-required": "The operator requires direct contact to disclose location pricing.",
                "form-required": "The operator requires a personal-information form before disclosing location pricing.",
            }[gym["pricingAccess"]]
            if estimate_is_publishable(gym, estimate):
                gym["pricingStatus"] = "estimated"
                gym["estimatedMonthly"] = estimate
                gym["pricingBlocker"] = exact_price_blocker + " A separately labeled, cross-validated cohort estimate is available."
            else:
                gym["pricingStatus"] = "gated"
                gym["estimatedMonthly"] = None
                gym["pricingBlocker"] = exact_price_blocker
                if estimate_rejected:
                    gym["pricingBlocker"] += " The comparable modality failed cross-validated error or uncertainty-range requirements."
            continue
        if held_or_invalid:
            gym["pricingStatus"] = "unresolved"
        else:
            gym["pricingBlocker"] = blocker_for(gym)
            if estimate_rejected:
                gym["pricingBlocker"] += " The comparable modality failed cross-validated error or uncertainty-range requirements."
        if not held_or_invalid and gym.get("reportedMonthly") is not None:
            gym["pricingStatus"] = "reported"
        elif not held_or_invalid and estimate is not None:
            gym["pricingStatus"] = "estimated"
            gym["estimatedMonthly"] = estimate
        else:
            gym["pricingStatus"] = "unresolved"
        gym_attempts = attempts_by_gym.get(text(gym.get("id")), [])
        gym_id = text(gym.get("id"))
        has_official_site = bool(text(gym.get("websiteUrl"))) and not is_osm_url(text(gym.get("websiteUrl")))
        has_reviewed_official_evidence = bool(
            gym.get("priceSourceUrl")
            and gym.get("priceObservedAt")
            and (gym.get("selectedPlanId") or gym.get("selectedDropInId"))
        )
        if gym_attempts:
            source_attempt_status = "crawl-attempted"
            source_attempt_blocker = ""
        elif gym_id in approved_source_discovery_ids:
            source_attempt_status = "reviewed-source-attempt"
            source_attempt_blocker = ""
        elif not has_official_site and (gym_id in discovery_by_gym or gym_id in manual_search_by_gym):
            source_attempt_status = "discovery-attempted"
            discovery = discovery_by_gym.get(gym_id, {})
            disposition = text(discovery.get("disposition")) or "manual-search-required"
            source_attempt_blocker = f"No official site was verified; discovery disposition: {disposition}."
        elif has_reviewed_official_evidence:
            source_attempt_status = "reviewed-official-evidence"
            source_attempt_blocker = ""
        else:
            source_attempt_status = "unattempted"
            source_attempt_blocker = (
                "Official page is queued for extraction."
                if has_official_site
                else "Official-site discovery has not been logged."
            )
        review.append(
            {
                "id": gym["id"],
                "name": gym["name"],
                "address": gym["address"],
                "websiteUrl": gym.get("websiteUrl", ""),
                "entityKind": gym["entityKind"],
                "modality": gym["modality"],
                "pricingStatus": gym["pricingStatus"],
                "pricingBlocker": gym["pricingBlocker"],
                "estimatedMonthly": gym["estimatedMonthly"],
                "reportedMonthly": gym.get("reportedMonthly"),
                "operatorConfirmedMonthly": gym.get("operatorConfirmedMonthly"),
                "priceReports": gym.get("priceReports", []),
                "publicationStatus": gym.get("publicationStatus", "publish"),
                "planValidationErrors": gym.get("planValidationErrors", []),
                "selectedPlanId": gym.get("selectedPlanId"),
                "catalogStatus": gym.get("catalogStatus"),
                "discoveryStatus": "needs-official-site" if not has_official_site else "official-page-present",
                "crawlStatus": "not-applicable" if not has_official_site else ("attempted" if gym_attempts else "queued"),
                "sourceAttemptStatus": source_attempt_status,
                "sourceAttemptBlocker": source_attempt_blocker,
                "crawlAttempts": [{"url": item.get("url", ""), "status": item.get("status", ""), "candidateCount": item.get("candidateCount", 0)} for item in gym_attempts],
            }
        )

    # Rebuild the audit rows after every pricing-state branch has finished. Several
    # fail-closed states intentionally exit the classification loop early, so an
    # in-loop audit append would silently omit exactly the records most in need of
    # an explicit source-attempt disposition.
    review = []
    for gym in gyms:
        gym["monthlyPriceBlocker"] = monthly_price_blocker(gym)
        gym["dayPassPriceBlocker"] = day_pass_price_blocker(gym)
        gym["metadataStatus"] = metadata_status(gym)
        gym["description"] = listing_description(gym)
        for optional_field in ("priceSource", "priceSourceUrl", "priceObservedAt", "planName", "planScope", "billingInterval"):
            if optional_field in gym and not text(gym.get(optional_field)):
                gym.pop(optional_field, None)
        gym_attempts = attempts_by_gym.get(text(gym.get("id")), [])
        gym_id = text(gym.get("id"))
        has_official_site = bool(text(gym.get("websiteUrl"))) and not is_osm_url(text(gym.get("websiteUrl")))
        has_reviewed_official_evidence = bool(
            gym.get("priceSourceUrl")
            and gym.get("priceObservedAt")
            and (gym.get("selectedPlanId") or gym.get("selectedDropInId"))
        )
        if gym_attempts:
            source_attempt_status = "crawl-attempted"
            source_attempt_blocker = ""
        elif gym_id in approved_source_discovery_ids:
            source_attempt_status = "reviewed-source-attempt"
            source_attempt_blocker = ""
        elif not has_official_site and (gym_id in discovery_by_gym or gym_id in manual_search_by_gym):
            source_attempt_status = "discovery-attempted"
            discovery = discovery_by_gym.get(gym_id, {})
            disposition = text(discovery.get("disposition")) or "manual-search-required"
            source_attempt_blocker = f"No official site was verified; discovery disposition: {disposition}."
        elif has_reviewed_official_evidence:
            source_attempt_status = "reviewed-official-evidence"
            source_attempt_blocker = ""
        else:
            source_attempt_status = "unattempted"
            source_attempt_blocker = (
                "Official page is queued for extraction."
                if has_official_site
                else "Official-site discovery has not been logged."
            )
        review.append(
            {
                "id": gym["id"],
                "name": gym["name"],
                "address": gym["address"],
                "websiteUrl": gym.get("websiteUrl", ""),
                "entityKind": gym["entityKind"],
                "modality": gym["modality"],
                "pricingStatus": gym["pricingStatus"],
                "pricingBlocker": gym["pricingBlocker"],
                "estimatedMonthly": gym["estimatedMonthly"],
                "reportedMonthly": gym.get("reportedMonthly"),
                "priceReports": gym.get("priceReports", []),
                "publicationStatus": gym.get("publicationStatus", "publish"),
                "planValidationErrors": gym.get("planValidationErrors", []),
                "selectedPlanId": gym.get("selectedPlanId"),
                "catalogStatus": gym.get("catalogStatus"),
                "discoveryStatus": "needs-official-site" if not has_official_site else "official-page-present",
                "crawlStatus": "not-applicable" if not has_official_site else ("attempted" if gym_attempts else "queued"),
                "sourceAttemptStatus": source_attempt_status,
                "sourceAttemptBlocker": source_attempt_blocker,
                "crawlAttempts": [
                    {"url": item.get("url", ""), "status": item.get("status", ""), "candidateCount": item.get("candidateCount", 0)}
                    for item in gym_attempts
                ],
            }
        )

    statuses = Counter(gym["pricingStatus"] for gym in gyms)
    kinds = Counter(gym["entityKind"] for gym in gyms)
    identity_audit = identity_duplicate_audit(gyms)
    commercial = [
        gym for gym in gyms
        if gym["entityKind"] in {"gym", "studio", "martial-arts"}
        and gym.get("recordStatus") != "coming_soon"
        and gym.get("accessModel") not in {"restricted", "not-applicable"}
        and gym.get("accessAvailability") not in {"waitlist", "enrollment-paused", "members-only", "presale"}
    ]
    review_by_id = {text(item.get("id")): item for item in review}
    commercial_with_source_attempt = [
        gym for gym in commercial
        if review_by_id.get(text(gym.get("id")), {}).get("sourceAttemptStatus") != "unattempted"
    ]
    actionable = [gym for gym in commercial if gym["pricingStatus"] in {"verified", "official-range", "operator-confirmed", "reported", "estimated", "pay-per-visit"}]
    meaningful_cost = [
        gym for gym in commercial
        if gym["pricingStatus"] in {"verified", "official-range", "operator-confirmed", "reported", "estimated", "pay-per-visit", "free", "not-applicable"}
        or bool(gym.get("costContext"))
    ]
    publicly_priced_commercial = [
        gym for gym in commercial
        if gym.get("monthlyPrice") is not None or gym.get("dayPassPrice") is not None
    ]

    def has_reconstructed_relevant_catalogs(gym: dict[str, Any]) -> bool:
        catalog = gym.get("catalogStatus", {})
        plans_complete = gym.get("monthlyPrice") is None or (catalog.get("plans") or {}).get("status") == "source-catalog"
        drops_complete = gym.get("dayPassPrice") is None or (catalog.get("dropIns") or {}).get("status") == "source-catalog"
        return plans_complete and drops_complete

    reconstructed_catalog_commercial = [
        gym for gym in publicly_priced_commercial if has_reconstructed_relevant_catalogs(gym)
    ]
    catalog_priority_records: list[dict[str, Any]] = []
    catalog_platform_counts: Counter[str] = Counter()
    for gym in publicly_priced_commercial:
        if has_reconstructed_relevant_catalogs(gym):
            continue
        urls = [
            text(gym.get("priceSourceUrl")),
            text(gym.get("websiteUrl")),
            text(gym.get("officialUrl")),
            *[text(item.get("url")) for item in attempts_by_gym.get(text(gym.get("id")), [])],
        ]
        platforms = list(dict.fromkeys(platform_adapters.platform_for_url(url) for url in urls if platform_adapters.platform_for_url(url)))
        group = platforms[0] if platforms else "generic-official"
        catalog_platform_counts[group] += 1
        catalog_priority_records.append(
            {
                "id": gym.get("id"),
                "name": gym.get("name"),
                "operatorId": gym.get("operatorId"),
                "platforms": platforms,
                "monthlyPrice": gym.get("monthlyPrice"),
                "dayPassPrice": gym.get("dayPassPrice"),
                "planCatalogStatus": (gym.get("catalogStatus", {}).get("plans") or {}).get("status"),
                "dropInCatalogStatus": (gym.get("catalogStatus", {}).get("dropIns") or {}).get("status"),
                "priceSourceUrl": gym.get("priceSourceUrl", ""),
            }
        )
    commercial_exact_address = [
        gym for gym in commercial
        if re.search(r"\d", text(gym.get("address"))) or re.search(r"\d", text(gym.get("canonicalAddress")))
    ]
    report = {
        "generatedAt": generated_at,
        "estimatorVersion": ESTIMATOR_VERSION,
        "selectionRuleVersion": SELECTION_RULE_VERSION,
        "totalListings": len(gyms),
        "pricingStatusCounts": {status: statuses.get(status, 0) for status in sorted(PRICING_STATUSES)},
        "suppressedAliasCount": len(suppressed_aliases),
        "suppressedAliases": suppressed_aliases,
        "identityIntegrity": identity_audit,
        "auditedListingInputs": len(gyms) + len(suppressed_aliases),
        "entityKindCounts": dict(sorted(kinds.items())),
        "verifiedMonthlyCount": sum(gym.get("monthlyPrice") is not None for gym in gyms),
        "verifiedDayPassCount": sum(gym.get("dayPassPrice") is not None for gym in gyms),
        "staleVerifiedCount": sum(gym.get("pricingStatus") == "verified" and gym.get("freshness") == "stale" for gym in gyms),
        "approvedCrawlObservationsApplied": approvals_applied,
        "approvedOperatorCatalogLocationsApplied": operator_catalog_locations_applied,
        "operatorConfirmedObservationsApplied": operator_confirmed_attached,
        "operatorConfirmedMonthlyCount": sum(gym.get("operatorConfirmedMonthly") is not None for gym in gyms),
        "approvedDealsApplied": approved_deals_attached,
        "activeDealCount": sum(len(gym.get("deals", [])) for gym in gyms),
        "approvedSourceDiscoveriesApplied": discoveries_applied,
        "approvedLocationMetadataApplied": location_metadata_applied,
        "reportedEvidenceCount": reported_evidence_attached,
        "reportedMonthlyCount": sum(gym.get("reportedMonthly") is not None for gym in gyms),
        "estimatedMonthlyCount": sum(gym.get("estimatedMonthly") is not None for gym in gyms),
        "approvedExternalComparableCount": sum(
            item.get("reviewStatus") == "approved" and item.get("evidenceTier") == "official-public"
            for item in external_comparables
        ),
        "officialCostContextListingCount": sum(bool(gym.get("costContext")) for gym in gyms),
        "officialRangeCount": sum(gym.get("pricingStatus") == "official-range" for gym in gyms),
        "fieldCoverage": {
            "specificDescriptionCount": sum(
                bool(text(gym.get("description")))
                and "OpenStreetMap listing" not in text(gym.get("description"))
                and "Official web research listing" not in text(gym.get("description"))
                for gym in gyms
            ),
            "officialUrlCount": sum(bool(text(gym.get("officialUrl"))) for gym in gyms),
            "hoursListedCount": sum(
                (gym.get("metadataStatus", {}).get("hours") or {}).get("status") in {"exact-hours", "access-schedule", "listed"}
                for gym in gyms
            ),
            "exactHoursCount": sum(
                (gym.get("metadataStatus", {}).get("hours") or {}).get("status") == "exact-hours" for gym in gyms
            ),
            "accessScheduleSemanticsCount": sum(
                (gym.get("metadataStatus", {}).get("hours") or {}).get("status") == "access-schedule" for gym in gyms
            ),
            "hoursUnpublishedCount": sum(
                (gym.get("metadataStatus", {}).get("hours") or {}).get("status") == "not-published" for gym in gyms
            ),
            "hoursNotApplicableCount": sum(
                (gym.get("metadataStatus", {}).get("hours") or {}).get("status") == "not-applicable" for gym in gyms
            ),
            "amenitiesListedCount": sum(bool(gym.get("amenities")) for gym in gyms),
            "operatorLocationIdCount": sum(bool(text(gym.get("operatorLocationId"))) for gym in gyms),
            "sourcePlanCatalogCount": sum((gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "source-catalog" for gym in gyms),
            "sourcePlanFragmentCount": sum((gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "source-fragment" for gym in gyms),
            "selectedOnlyPlanCatalogCount": sum((gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "selected-only" for gym in gyms),
            "noPlanCatalogCount": sum((gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "none" for gym in gyms),
            "sourceDropInCatalogCount": sum((gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "source-catalog" for gym in gyms),
            "sourceDropInFragmentCount": sum((gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "source-fragment" for gym in gyms),
            "selectedOnlyDropInCatalogCount": sum((gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "selected-only" for gym in gyms),
            "noDropInCatalogCount": sum((gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "none" for gym in gyms),
            "selectedUnknownCommitmentCount": sum(
                any(
                    plan.get("id") == gym.get("selectedPlanId")
                    and normalized((plan.get("commitment") or {}).get("type")) == "unknown"
                    for plan in gym.get("plans", [])
                )
                for gym in gyms
            ),
            "selectedUndisclosedClassAllowanceCount": sum(
                any(
                    plan.get("id") == gym.get("selectedPlanId")
                    and gym.get("accessModel") == "class-membership"
                    and not bool((plan.get("classAllowance") or {}).get("disclosed"))
                    for plan in gym.get("plans", [])
                )
                for gym in gyms
            ),
            "typicalPlanSelectedCount": sum(bool(text(gym.get("typicalPlanId"))) for gym in gyms),
            "highestAccessPlanSelectedCount": sum(bool(text(gym.get("highestAccessPlanId"))) for gym in gyms),
            "bestValuePlanSelectedCount": sum(bool(text(gym.get("bestValuePlanId"))) for gym in gyms),
            "explicitMonthlyPriceStateCount": sum(
                gym.get("monthlyPrice") is not None or bool(text(gym.get("monthlyPriceBlocker"))) for gym in gyms
            ),
            "explicitDayPassPriceStateCount": sum(
                gym.get("dayPassPrice") is not None or bool(text(gym.get("dayPassPriceBlocker"))) for gym in gyms
            ),
            "metadataGapStatusCount": sum(
                all(
                    isinstance(gym.get("metadataStatus", {}).get(field), dict)
                    and bool(text(gym["metadataStatus"][field].get("status")))
                    for field in ("officialUrl", "hours", "amenities", "operatorLocationId")
                )
                for gym in gyms
            ),
        },
        "commercialListings": len(commercial),
        "commercialAttemptedSourceListings": len(commercial_with_source_attempt),
        "commercialAttemptedSourceCoverage": round(len(commercial_with_source_attempt) / len(commercial), 4) if commercial else 1,
        "commercialUnattemptedSourceListings": [
            {
                "id": gym.get("id"),
                "name": gym.get("name"),
                "websiteUrl": gym.get("websiteUrl", ""),
                "blocker": review_by_id.get(text(gym.get("id")), {}).get("sourceAttemptBlocker", ""),
            }
            for gym in commercial
            if review_by_id.get(text(gym.get("id")), {}).get("sourceAttemptStatus") == "unattempted"
        ],
        "commercialExactAddressListings": len(commercial_exact_address),
        "commercialExactAddressCoverage": round(len(commercial_exact_address) / len(commercial), 4) if commercial else 1,
        "actionableCommercialListings": len(actionable),
        "actionableCommercialCoverage": round(len(actionable) / len(commercial), 4) if commercial else 1,
        "meaningfulCostCommercialListings": len(meaningful_cost),
        "meaningfulCostCommercialCoverage": round(len(meaningful_cost) / len(commercial), 4) if commercial else 1,
        "officialSiteUnresolvedCount": sum(item["discoveryStatus"] == "needs-official-site" for item in review),
        "officialPagePresentCount": sum(item["discoveryStatus"] == "official-page-present" for item in review),
        "officialSiteDiscoveryQueue": sum(
            item["discoveryStatus"] == "needs-official-site" and item["sourceAttemptStatus"] == "unattempted"
            for item in review
        ),
        "officialPageExtractionQueue": sum(
            item["discoveryStatus"] == "official-page-present" and item["sourceAttemptStatus"] == "unattempted"
            for item in review
        ),
        "catalogReconstructionQueue": {
            "publiclyPricedCommercialListings": len(publicly_priced_commercial),
            "reconstructedRelevantCatalogListings": len(reconstructed_catalog_commercial),
            "reconstructedRelevantCatalogCoverage": round(
                len(reconstructed_catalog_commercial) / len(publicly_priced_commercial), 4
            ) if publicly_priced_commercial else 1,
            "targetCoverage": 0.9,
            "targetMet": (
                len(reconstructed_catalog_commercial) / len(publicly_priced_commercial) >= 0.9
            ) if publicly_priced_commercial else True,
            "priorityPlatformCounts": dict(sorted(catalog_platform_counts.items(), key=lambda item: (-item[1], item[0]))),
            "priorityRecords": catalog_priority_records,
            "sourceFragmentPlanCatalogs": sum(
                (gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "source-fragment" for gym in gyms
            ),
            "selectedOnlyPlanCatalogs": sum(
                (gym.get("catalogStatus", {}).get("plans") or {}).get("status") == "selected-only" for gym in gyms
            ),
            "sourceFragmentDropInCatalogs": sum(
                (gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "source-fragment" for gym in gyms
            ),
            "selectedOnlyDropInCatalogs": sum(
                (gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") == "selected-only" for gym in gyms
            ),
        },
        "crawlSummary": {
            "attemptedGyms": len(attempts_by_gym),
            "requestCount": len(crawl_attempts),
            "statusCounts": dict(sorted(Counter(text(item.get("status")) for item in crawl_attempts).items())),
            "reviewCandidatePages": sum(bool(item.get("requiresReview")) for item in crawl_attempts),
            "linkedStorefrontRequests": sum("linkedFrom" in item for item in crawl_attempts),
            "renderedRequests": len(rendered_crawl_document.get("attempts", [])),
            "renderedAccessBlockedRequests": sum(
                text(item.get("status")) == "access-blocked"
                for item in rendered_crawl_document.get("attempts", [])
            ),
            "renderedAccessBlockerCounts": dict(sorted(Counter(
                text(item.get("accessBlocker"))
                for item in rendered_crawl_document.get("attempts", [])
                if text(item.get("accessBlocker"))
            ).items())),
            "priceChangeFlags": sum(bool(item.get("priceChangeOver20Percent")) for item in crawl_attempts),
            "allReviewedSeedsGoneFlags": sum(bool(item.get("allReviewedSeedsGone")) for item in crawl_attempts),
        },
        "publicSourceDiscoverySummary": {
            "attemptedListings": public_discovery_document.get("attemptedListings", 0),
            "requests": public_discovery_document.get("requests", 0),
            "dispositionCounts": public_discovery_document.get("dispositionCounts", {}),
        },
        "reportedEvidenceAudit": {
            "reportsChecked": reported_audit_document.get("reportsChecked", 0),
            "requiresReview": reported_audit_document.get("requiresReview", 0),
        },
        "estimatorValidation": estimator_validation,
        "withheldEstimateModalities": {
            label: stats
            for label, stats in estimator_validation.items()
            if label != "overall"
            and int(stats.get("sampleSize") or 0) >= 8
            and (
                stats.get("medianAbsolutePercentageError") is None
                or stats["medianAbsolutePercentageError"] > 0.20
                or stats.get("rangeCoverage") is None
                or stats["rangeCoverage"] < 0.75
            )
        },
        "publicationChecks": {
            "allClassified": all(gym.get("entityKind") in ENTITY_KINDS and gym.get("accessModel") in ACCESS_MODELS for gym in gyms),
            "allHavePricingStatus": all(gym.get("pricingStatus") in PRICING_STATUSES for gym in gyms),
            "noEstimateInVerifiedField": all(gym.get("monthlyPrice") is None or gym.get("estimatedMonthly") is None for gym in gyms),
            "noEstimateOnGatedOrUnavailable": all(
                gym.get("estimatedMonthly") is None
                for gym in gyms
                if gym.get("pricingStatus") in {"gated", "not-applicable"}
                or gym.get("recordStatus") == "coming_soon"
                or gym.get("accessAvailability") in {"waitlist", "enrollment-paused", "members-only"}
            ),
            "noEstimateOnRestrictedConflictedOrUnavailable": all(
                gym.get("estimatedMonthly") is None
                for gym in gyms
                if gym.get("accessModel") in {"free-public", "restricted", "not-applicable"}
                or gym.get("entityKind") not in {"gym", "studio", "martial-arts"}
                or normalized(gym.get("pricingAccess")) in {"official status conflict", "official price conflict"}
                or normalized(gym.get("recordStatus")) in {"closed", "coming soon", "conflict", "presale", "status review"}
                or normalized(gym.get("accessAvailability")) in {"waitlist", "enrollment paused", "members only", "presale"}
            ),
            "reportedPricesRemainOutOfVerifiedFields": all(gym.get("monthlyPrice") is None for gym in gyms if gym.get("pricingStatus") == "reported"),
            "operatorConfirmedPricesRemainOutOfVerifiedFields": all(
                gym.get("monthlyPrice") is None and (gym.get("operatorConfirmedMonthly") or {}).get("publiclyReproducible") is False
                for gym in gyms if gym.get("pricingStatus") == "operator-confirmed"
            ),
            "officialRangesRemainOutOfVerifiedFields": all(
                gym.get("monthlyPrice") is None
                and gym.get("dayPassPrice") is None
                and gym.get("estimatedMonthly") is None
                and has_publishable_official_cost_context(gym)
                for gym in gyms if gym.get("pricingStatus") == "official-range"
            ),
            "dealsNeverReplaceOrdinaryPrices": all(
                deal.get("replacesOrdinaryPrice") is False
                for gym in gyms for deal in gym.get("deals", [])
            ),
            "suppressedAliasesHaveReasons": all(item["id"] and item["reason"] and item["sourceUrl"] for item in suppressed_aliases),
            "verifiedPlansResolve": all(
                gym.get("selectedPlanId")
                and sum(plan.get("id") == gym.get("selectedPlanId") for plan in gym.get("plans", [])) == 1
                and not gym.get("planValidationErrors")
                for gym in gyms if gym.get("pricingStatus") == "verified"
            ),
            "compatibilityPricesMatchSelections": all(
                (
                    gym.get("monthlyPrice") is None
                    or any(
                        plan.get("id") == gym.get("selectedPlanId")
                        and math.isclose(float(plan["billing"]["normalizedMonthly"]), float(gym["monthlyPrice"]), abs_tol=0.011)
                        for plan in gym.get("plans", [])
                    )
                )
                and (
                    gym.get("dayPassPrice") is None
                    or any(
                        offer.get("id") == gym.get("selectedDropInId")
                        and math.isclose(float(offer["amount"]), float(gym["dayPassPrice"]), abs_tol=0.011)
                        for offer in gym.get("dropIns", [])
                    )
                )
                for gym in gyms
            ),
            "noPublicLowConfidenceEstimates": all(
                (gym.get("estimatedMonthly") or {}).get("confidence") != "low" for gym in gyms
            ),
            "publishedEstimatesPassModalityValidation": all(
                estimate_passes_modality_validation(gym, gym["estimatedMonthly"], estimator_validation)
                for gym in gyms if gym.get("estimatedMonthly") is not None
            ),
            "zeroIdentityReviewHolds": all(gym.get("publicationStatus") != "review-hold" for gym in gyms),
            "zeroUnreviewedSameOperatorExactAddressDuplicates": not identity_audit["duplicates"],
            "noOfficialPriceConflictsPublished": all(
                gym.get("pricingStatus") != "verified" for gym in gyms if gym.get("officialPriceConflict")
            ),
            "noGenericDescriptions": all(
                bool(text(gym.get("description")))
                and "OpenStreetMap listing" not in text(gym.get("description"))
                and "Official web research listing" not in text(gym.get("description"))
                for gym in gyms
            ),
            "allNullMonthlyPricesHaveReasons": all(
                gym.get("monthlyPrice") is not None or bool(text(gym.get("monthlyPriceBlocker"))) for gym in gyms
            ),
            "allNullDayPassPricesHaveReasons": all(
                gym.get("dayPassPrice") is not None or bool(text(gym.get("dayPassPriceBlocker"))) for gym in gyms
            ),
            "allMetadataGapsHaveStates": all(
                all(
                    isinstance(gym.get("metadataStatus", {}).get(field), dict)
                    and bool(text(gym["metadataStatus"][field].get("status")))
                    and (
                        gym["metadataStatus"][field]["status"] in {"listed", "exact-hours"}
                        or bool(text(gym["metadataStatus"][field].get("reason")))
                    )
                    for field in ("officialUrl", "hours", "amenities", "operatorLocationId")
                )
                for gym in gyms
            ),
            "allCatalogCompletenessStatesExplicit": all(
                (gym.get("catalogStatus", {}).get("plans") or {}).get("status") in {"source-catalog", "source-fragment", "selected-only", "none"}
                and (gym.get("catalogStatus", {}).get("dropIns") or {}).get("status") in {"source-catalog", "source-fragment", "selected-only", "none"}
                for gym in gyms
            ),
            "costContextNeverLeaksIntoVerifiedFields": all(
                not gym.get("costContext")
                or all(item.get("selectable") is False for item in gym.get("costContext", []))
                for gym in gyms
            ),
        },
        "releaseGates": {
            "everyCommercialListingHasAttemptedSourceLog": len(commercial_with_source_attempt) == len(commercial),
            "commercialActionableCoverageAtLeast90Percent": (len(actionable) / len(commercial) >= 0.9) if commercial else True,
            "commercialMeaningfulCostCoverageAtLeast97Percent": (len(meaningful_cost) / len(commercial) >= 0.97) if commercial else True,
            "commercialExactAddressCoverageAtLeast95Percent": (len(commercial_exact_address) / len(commercial) >= 0.95) if commercial else True,
            "commercialCatalogCoverageAtLeast90Percent": (
                len(reconstructed_catalog_commercial) / len(publicly_priced_commercial) >= 0.9
            ) if publicly_priced_commercial else True,
            "zeroUnreviewedSameOperatorExactAddressDuplicates": not identity_audit["duplicates"],
            "overallEstimatorMedianErrorAtMost15Percent": (
                estimator_validation.get("overall", {}).get("medianAbsolutePercentageError") is not None
                and estimator_validation["overall"]["medianAbsolutePercentageError"] <= 0.15
            ),
            "overallEstimatorRangeCoverageAtLeast75Percent": (
                estimator_validation.get("overall", {}).get("rangeCoverage") is not None
                and estimator_validation["overall"]["rangeCoverage"] >= 0.75
            ),
        },
    }
    metadata = dict(document.get("_meta", {}))
    metadata["costCoverage"] = {
        "generatedAt": generated_at,
        "report": "data/imports/cost-coverage-report.json",
        "estimatorVersion": ESTIMATOR_VERSION,
        "selectionRuleVersion": SELECTION_RULE_VERSION,
        "verifiedPricesRemainSeparateFromEstimates": True,
        "reportedPriceVersion": REPORTED_PRICE_VERSION,
        "reportedPricesRequireIndependentSources": 2,
        "reportedPriceMaximumAgeDays": REPORTED_MAX_AGE_DAYS,
        "fieldCompletenessVersion": "explicit-gap-state-v1",
    }
    return {"_meta": metadata, "gyms": gyms}, report, {"generatedAt": generated_at, "records": review}


def validate_publication(document: dict[str, Any], report: dict[str, Any]) -> None:
    failures = [name for name, passed in report["publicationChecks"].items() if not passed]
    if failures:
        raise ValueError(f"Cost coverage publication checks failed: {', '.join(failures)}")
    for gym in document.get("gyms", []):
        if gym.get("entityKind") in {"public-recreation", "outdoor-equipment", "non-consumer"} and gym.get("pricingStatus") in {"free", "not-applicable"}:
            if gym.get("estimatedMonthly") is not None:
                raise ValueError(f"Non-commercial listing received an estimate: {gym.get('id')}")


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate without writing output files")
    parser.add_argument("--date", help="Override generation date (YYYY-MM-DD) for reproducible tests")
    args = parser.parse_args()
    generated_at = args.date or datetime.now(UTC).date().isoformat()
    document = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    enriched, report, review = enrich_document(document, generated_at)
    validate_publication(enriched, report)
    if not args.check:
        save_json(SOURCE_PATH, enriched)
        save_json(WEB_PATH, enriched)
        save_json(REPORT_PATH, report)
        save_json(REVIEW_PATH, review)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
