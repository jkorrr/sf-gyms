"""Public booking-platform catalog adapters.

The adapters operate only on JSON already returned by an operator page or an
approved operator-owned booking host.  They produce review candidates, never
verified prices.  The deliberately small shared shape lets the crawler retain
complete plan semantics without teaching its generic dollar regex about every
vendor payload.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

MONEY_RE = re.compile(r"\$?\s*(\d{1,6}(?:\.\d{1,2})?)")
PROMOTION_RE = re.compile(
    r"\b(?:intro|trial|first month|first class|first visit|first session|first week|founding|presale|"
    r"new client|new member|new student|welcome|limited time|special|summer|seasonal|save|off)\b",
    re.IGNORECASE,
)
RESTRICTED_RE = re.compile(r"\b(?:student|resident|employee|senior|youth|military|corporate)\b", re.IGNORECASE)
DROP_IN_RE = re.compile(r"\b(?:drop[ -]?in|single (?:class|visit|session)|day pass)\b", re.IGNORECASE)
FEE_RE = re.compile(r"\b(?:annual|enrollment|enrolment|initiation|activation|processing|setup|join)\s+fee\b", re.IGNORECASE)
BEST_VALUE_RE = re.compile(r"\b(?:best value|most popular|recommended)\b", re.IGNORECASE)
PRODUCT_SEMANTIC_RE = re.compile(
    r"\b(?:membership|plan|package|class|session|visit|pass|drop[ -]?in|unlimited|monthly|"
    r"weekly|week|private|semi-private|training|open gym|autopay|recurring)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlatformProfile:
    name: str
    domains: tuple[str, ...]


PROFILES = (
    PlatformProfile("mindbody", ("mindbodyonline.com",)),
    PlatformProfile("momence", ("momence.com",)),
    PlatformProfile("wellnessliving", ("wellnessliving.com",)),
    PlatformProfile("clubready", ("clubready.com",)),
    PlatformProfile(
        "xponential-member-app",
        ("members.clubpilates.com", "members.purebarre.com", "members.stretchlab.com"),
    ),
    PlatformProfile("pushpress", ("pushpress.com",)),
    PlatformProfile("wodify", ("wodify.com",)),
    PlatformProfile("zen-planner", ("zenplanner.com",)),
    PlatformProfile("gymdesk", ("gymdesk.com",)),
    PlatformProfile("acuity", ("as.me", "acuityscheduling.com", "squarespacescheduling.com")),
    PlatformProfile("jane", ("janeapp.com",)),
    PlatformProfile("bookee", ("onbookee.com",)),
    PlatformProfile("mariana-tek", ("marianatek.com", "marianaiframes.com")),
    PlatformProfile("eventbrite", ("eventbrite.com",)),
    PlatformProfile("abc-fitness", ("onlinejoin.abcfitness.com",)),
    PlatformProfile("redpoint", ("portal.movementgyms.com",)),
    PlatformProfile("approach", ("portal.approach.app",)),
    PlatformProfile("bay-club-public-api", ("oms-sales-api.bayclubs.io",)),
)

FITNESS_SERVICE_RE = re.compile(
    r"\b(?:personal training|strength training|fitness training|athletic performance|sports performance|"
    r"conditioning|mobility training)\b",
    re.IGNORECASE,
)
CLINICAL_SERVICE_RE = re.compile(
    r"\b(?:physical therapy|physiotherapy|occupational therapy|chiropractic|acupuncture|massage therapy|"
    r"manual therapy|telehealth)\b",
    re.IGNORECASE,
)

NAME_KEYS = ("name", "title", "label", "productName", "packageName", "membershipName", "serviceName")
ID_KEYS = ("id", "uuid", "productId", "packageId", "membershipId", "planId", "priceId", "optionId")
AMOUNT_KEYS = ("price", "amount", "unitAmount", "unit_price", "priceAmount", "regularPrice", "monthlyPrice")
CENTS_KEYS = ("amountCents", "amount_cents", "priceCents", "price_cents", "unitAmountCents", "unit_amount", "unit_amount_cents")
CADENCE_KEYS = ("cadence", "interval", "billingInterval", "billingPeriod", "billingCycle", "frequency", "renewalPeriod")
ALLOWANCE_KEYS = ("creditCount", "credit_count", "credits", "sessionsPerMonth", "classesPerMonth", "visitsPerMonth", "usageLimit")
COMMITMENT_KEYS = ("minimumMonths", "minimum_months", "contractMonths", "termMonths", "commitmentMonths", "paymentCount")
FEE_COLLECTION_KEYS = ("fees", "additionalFees", "setupFees", "mandatoryFees", "charges")


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except ValueError:
        return ""


def platform_for_url(url: str) -> str:
    host = hostname(url)
    for profile in PROFILES:
        if any(host == domain or host.endswith(f".{domain}") for domain in profile.domains):
            return profile.name
    return ""


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first(node: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = node.get(key)
        if value is not None and value != "":
            return value
    return None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return number(first(value, ("numeric", "amount", "value", "price")))
    match = MONEY_RE.search(text(value).replace(",", ""))
    return float(match.group(1)) if match else None


def amount_from(node: dict[str, Any]) -> float | None:
    cents_value = first(node, CENTS_KEYS)
    if cents_value is not None:
        cents = number(cents_value)
        if cents is not None:
            return round(cents / 100, 2)
    return number(first(node, AMOUNT_KEYS))


def cadence_from(node: dict[str, Any], label: str) -> tuple[str, int]:
    raw = text(first(node, CADENCE_KEYS)).casefold().replace("_", " ")
    count = int(number(first(node, ("intervalCount", "interval_count", "billingIntervalCount"))) or 1)
    combined = f"{raw} {label.casefold()}"
    if any(value in combined for value in ("4 week", "28 day", "p4w")):
        return "4 weeks", count
    if any(value in combined for value in ("biweekly", "bi-weekly", "2 week")):
        return "2 weeks", count
    if any(value in combined for value in ("weekly", "week", "p1w")):
        return "week", count
    if any(value in combined for value in ("year", "annual", "p1y")):
        return "year", count
    if any(value in combined for value in ("month", "monthly", "p1m", "autopay", "recurring")):
        return "month", count
    return "one-time", 1


def class_allowance(node: dict[str, Any], label: str, cadence: str) -> dict[str, Any] | None:
    if bool(first(node, ("unlimited", "isUnlimited", "is_unlimited"))) or "unlimited" in label.casefold():
        return {"count": None, "period": cadence if cadence != "one-time" else "month", "unlimited": True}
    value = number(first(node, ALLOWANCE_KEYS))
    if value is None:
        match = re.search(r"\b(\d{1,3})\s*(?:classes?|visits?|sessions?|credits?)\b", label, re.IGNORECASE)
        value = float(match.group(1)) if match else None
    if value is None:
        return None
    return {"count": value, "period": cadence if cadence != "one-time" else "purchase", "unlimited": False}


def commitment(node: dict[str, Any], label: str, recurring: bool) -> dict[str, Any]:
    months = number(first(node, COMMITMENT_KEYS))
    if months is None:
        match = re.search(r"\b(\d{1,2})[ -]month(?: minimum| commitment| contract)?\b", label, re.IGNORECASE)
        months = float(match.group(1)) if match else None
    if months:
        return {"type": "fixed-term", "minimumMonths": int(months)}
    if recurring and re.search(r"\b(?:month[ -]to[ -]month|no commitment|cancel anytime)\b", label, re.IGNORECASE):
        return {"type": "month-to-month", "minimumMonths": None}
    return {"type": "unknown" if recurring else "none", "minimumMonths": None}


def fee_type(label: str) -> str:
    lowered = label.casefold()
    for value in ("annual", "enrollment", "initiation", "activation", "processing", "setup"):
        if value in lowered or (value == "enrollment" and "enrolment" in lowered):
            return value
    return "other"


def fees_from(node: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in FEE_COLLECTION_KEYS:
        values = node.get(key)
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            continue
        for fee in values:
            if not isinstance(fee, dict):
                continue
            amount = amount_from(fee)
            label = text(first(fee, NAME_KEYS)) or "Mandatory fee"
            if amount is None or amount < 0 or amount > 2_000:
                continue
            mandatory = fee.get("mandatory", fee.get("isMandatory", True)) is not False
            if not mandatory:
                continue
            result.append({
                "type": fee_type(label), "name": label, "amount": amount,
                "currency": text(first(fee, ("currency", "currencyCode", "priceCurrency"))) or "USD",
                "cadence": text(first(fee, CADENCE_KEYS)) or "one-time", "mandatory": True,
            })
    return result


def has_product_semantics(node: dict[str, Any], label: str) -> bool:
    """Reject location, cart, and account objects that merely contain money.

    Public booking payloads frequently mix products with studio metadata and
    checkout totals.  A candidate therefore needs either a product-shaped
    label or an explicit product/billing attribute; a generic ``id`` and an
    amount are deliberately insufficient.
    """

    if PRODUCT_SEMANTIC_RE.search(label):
        return True
    explicit_product_keys = (
        "productId", "packageId", "membershipId", "planId", "priceId", "optionId",
        "productName", "packageName", "membershipName", "serviceName",
    )
    if any(node.get(key) not in (None, "") for key in explicit_product_keys):
        return True
    semantic_keys = CADENCE_KEYS + ALLOWANCE_KEYS + COMMITMENT_KEYS + (
        "recurring", "isRecurring", "is_recurring", "autoRenew", "autorenew",
        "unlimited", "isUnlimited", "is_unlimited",
    )
    return any(node.get(key) not in (None, "") for key in semantic_keys)


def extract_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Extract conservative review candidates from a supported platform JSON payload."""

    platform = platform_for_url(source_url)
    if not platform:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()
    for node in walk(payload):
        label = text(first(node, NAME_KEYS))
        amount = amount_from(node)
        if not label or amount is None or amount <= 0 or amount > 10_000:
            continue
        if not has_product_semantics(node, label):
            continue
        if FEE_RE.search(label) and not re.search(r"\b(?:membership|plan|package|class|session)\b", label, re.IGNORECASE):
            continue
        product_id = text(first(node, ID_KEYS))
        cadence, interval_count = cadence_from(node, label)
        recurring_flag = first(node, ("recurring", "isRecurring", "is_recurring", "autoRenew", "autorenew"))
        recurring = recurring_flag is True or cadence not in {"one-time", "visit"}
        allowance = class_allowance(node, label, cadence)
        is_promotion = bool(PROMOTION_RE.search(label))
        if DROP_IN_RE.search(label) or (allowance or {}).get("count") == 1 and not recurring:
            product_type = "drop-in"
            cadence = "visit"
        elif recurring:
            product_type = "monthly"
        else:
            product_type = "offer"
        key = (product_id or label.casefold(), amount, product_type)
        if key in seen:
            continue
        seen.add(key)
        restricted = None if re.search(r"\bnew (?:client|member|student)\b", label, re.IGNORECASE) else RESTRICTED_RE.search(label)
        location_ids = node.get("locationIds") or node.get("locations") or node.get("studioIds") or []
        if not isinstance(location_ids, list):
            location_ids = [location_ids]
        candidates.append({
            "sourceProductId": product_id,
            "amount": amount,
            "currency": text(first(node, ("currency", "currencyCode", "currency_code", "priceCurrency"))) or "USD",
            "rawLabel": " ".join(label.split())[:220],
            "cadence": cadence,
            "intervalCount": interval_count,
            "productType": product_type,
            "classAllowance": allowance,
            "promotion": {"isPromotion": is_promotion, "label": label if is_promotion else ""},
            "eligibility": {
                "type": "restricted" if restricted else ("new-client" if is_promotion else "standard-adult"),
                "restrictions": [restricted.group(0)] if restricted else (["Promotional or introductory product"] if is_promotion else []),
            },
            "commitment": commitment(node, label, recurring),
            "fees": fees_from(node),
            "locations": [text(value) for value in location_ids if text(value)],
            "bestValueLabel": bool(BEST_VALUE_RE.search(label) or first(node, ("isPopular", "mostPopular", "recommended")) is True),
            "purchaseMethod": "direct-public",
            "method": f"public-{platform}-json",
            "adapter": platform,
            "evidenceTier": "official-public",
            "exactLocationMatch": "candidate",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates


def jane_service_card_candidates(card_text: str, source_url: str, href: str = "") -> list[dict[str, Any]]:
    """Extract exact fitness-service prices from a public Jane appointment card.

    Jane storefronts often mix fitness services with clinical care. Only
    explicitly fitness-shaped services are retained, and they remain
    trainer-required one-time review candidates rather than ordinary drop-ins.
    """

    if platform_for_url(source_url) != "jane":
        return []
    label_lines = [" ".join(line.split()) for line in text(card_text).splitlines() if text(line)]
    label = next((line for line in label_lines if "$" not in line and not re.fullmatch(r"\d+\s*minutes?", line, re.IGNORECASE) and not line.casefold().startswith("offered by")), "")
    amount_match = re.search(r"\$\s*(\d{1,4}(?:\.\d{1,2})?)", card_text)
    duration_match = re.search(r"\b(\d{1,3})\s*minutes?\b", card_text, re.IGNORECASE)
    if not label or not amount_match or not FITNESS_SERVICE_RE.search(label) or CLINICAL_SERVICE_RE.search(label):
        return []
    amount = float(amount_match.group(1))
    if amount <= 0 or amount > 2_000:
        return []
    identity = re.search(r"discipline/(\d+)/treatment/(\d+)", href)
    product_id = f"discipline-{identity.group(1)}-treatment-{identity.group(2)}" if identity else ""
    duration = int(duration_match.group(1)) if duration_match else None
    scope = f"One trainer-led {duration}-minute session" if duration else "One trainer-led session"
    return [{
        "sourceProductId": product_id,
        "amount": amount,
        "currency": "USD",
        "rawLabel": label[:220],
        "cadence": "one-time",
        "intervalCount": 1,
        "productType": "offer",
        "classAllowance": {"count": 1, "period": "purchase", "unlimited": False},
        "accessScope": scope,
        "durationMinutes": duration,
        "promotion": {"isPromotion": False, "label": ""},
        "eligibility": {
            "type": "trainer-required",
            "restrictions": ["One-to-one appointment; not an ordinary unrestricted gym or class drop-in"],
        },
        "commitment": {"type": "none", "minimumMonths": None},
        "fees": [],
        "locations": [],
        "bestValueLabel": False,
        "purchaseMethod": "direct-public",
        "method": "rendered-jane-service-card",
        "adapter": "jane",
        "evidenceTier": "official-public",
        "exactLocationMatch": "candidate",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]


def mindbody_purchase_item_candidates(
    category_label: str,
    card_text: str,
    source_url: str,
    source_product_id: str = "",
) -> list[dict[str, Any]]:
    """Extract a product associated with its price from a rendered Mindbody row."""

    if platform_for_url(source_url) != "mindbody":
        return []
    lines = [" ".join(line.split()) for line in text(card_text).splitlines() if text(line)]
    label = next((line for line in lines if "$" not in line), "")
    amount_match = re.search(r"\$\s*(\d{1,5}(?:\.\d{1,2})?)", card_text)
    if not label or not amount_match:
        return []
    amount = float(amount_match.group(1))
    if amount <= 0 or amount > 10_000:
        return []
    combined = f"{category_label} {label}"
    cadence, interval_count = cadence_from({}, combined)
    recurring = bool(re.search(
        r"\b(?:monthly|auto[ -]?renew(?:s|ing)?|recurring|every\s+(?:month|four weeks|4 weeks|week|year)|"
        r"per\s+month)\b|/\s*(?:mo|month)\b",
        combined,
        re.IGNORECASE,
    ))
    duration_match = re.search(r"\b(\d{1,2})[ -]months?\b", combined, re.IGNORECASE)
    if not recurring:
        cadence, interval_count = "one-time", 1
    allowance = class_allowance({}, combined, cadence)
    promotion = bool(PROMOTION_RE.search(combined))
    online_only = bool(re.search(r"\bvirtual\b", combined, re.IGNORECASE))
    drop_in = bool(DROP_IN_RE.search(combined))
    commitment_value = commitment({}, combined, recurring)
    if duration_match and not recurring:
        commitment_value = {"type": "fixed-term", "minimumMonths": int(duration_match.group(1))}
    return [{
        "sourceProductId": text(source_product_id),
        "amount": amount,
        "currency": "USD",
        "rawLabel": label[:220],
        "categoryLabel": " ".join(text(category_label).split())[:160],
        "cadence": "visit" if drop_in else cadence,
        "intervalCount": interval_count,
        "productType": "drop-in" if drop_in else ("monthly" if recurring else "offer"),
        "classAllowance": allowance,
        "promotion": {"isPromotion": promotion, "label": combined if promotion else ""},
        "eligibility": {
            "type": "online-only" if online_only else ("new-client" if promotion else "standard-adult"),
            "restrictions": ["Virtual product; not in-person location access"] if online_only else (["Promotional or seasonal product"] if promotion else []),
        },
        "commitment": commitment_value,
        "fees": [],
        "locations": [],
        "bestValueLabel": bool(BEST_VALUE_RE.search(combined)),
        "purchaseMethod": "direct-public",
        "method": "rendered-mindbody-purchase-item",
        "adapter": "mindbody",
        "evidenceTier": "official-public",
        "exactLocationMatch": "candidate",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]


def mindbody_contract_candidates(
    contract_label: str,
    contract_text: str,
    source_url: str,
    source_product_id: str = "",
) -> list[dict[str, Any]]:
    """Extract a recurring plan from one publicly visible Mindbody contract.

    Contract pages repeat totals and component rows.  The recurring charge is
    accepted only when Mindbody explicitly renders ``$X every <cadence>``;
    totals, zero-dollar companion passes, and checkout arithmetic are ignored.
    """

    if platform_for_url(source_url) != "mindbody":
        return []
    label = " ".join(text(contract_label).split())
    recurring_match = re.search(
        r"\$\s*(?P<amount>\d{1,5}(?:\.\d{1,2})?)\s+every\s+"
        r"(?P<cadence>month|four weeks|4 weeks|week|year)\b",
        contract_text,
        re.IGNORECASE,
    )
    if not label or not recurring_match:
        return []
    amount = float(recurring_match.group("amount"))
    if amount <= 0 or amount > 10_000:
        return []
    cadence = recurring_match.group("cadence").casefold()
    cadence = "4 weeks" if cadence in {"four weeks", "4 weeks"} else cadence
    combined = " ".join(f"{label} {contract_text}".split())
    promotion = bool(PROMOTION_RE.search(combined))
    fees: list[dict[str, Any]] = []
    fee_re = re.compile(
        r"(?P<label>(?:annual|enroll?ment|initiation|activation|processing|setup)\s+fee)"
        r"\s*[:\-]?\s*\$\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    for match in fee_re.finditer(contract_text):
        fee_amount = float(match.group("amount"))
        if fee_amount < 0 or fee_amount > 2_000:
            continue
        fees.append({
            "type": fee_type(match.group("label")),
            "name": " ".join(match.group("label").split()),
            "amount": fee_amount,
            "currency": "USD",
            "cadence": "year" if "annual" in match.group("label").casefold() else "one-time",
            "mandatory": True,
        })
    eligibility_type = "new-client" if promotion else "standard-adult"
    return [{
        "sourceProductId": text(source_product_id),
        "amount": amount,
        "currency": "USD",
        "rawLabel": label[:220],
        "categoryLabel": "Contracts",
        "cadence": cadence,
        "intervalCount": 1,
        "productType": "monthly" if cadence in {"month", "4 weeks", "week"} else "offer",
        "classAllowance": class_allowance({}, label, cadence),
        "accessScope": label,
        "promotion": {"isPromotion": promotion, "label": label if promotion else ""},
        "eligibility": {
            "type": eligibility_type,
            "restrictions": ["Promotional contract"] if promotion else [],
        },
        "commitment": commitment({}, combined, True),
        "fees": fees,
        "locations": [],
        "bestValueLabel": bool(BEST_VALUE_RE.search(combined)),
        "purchaseMethod": "direct-public",
        "method": "rendered-mindbody-contract",
        "adapter": "mindbody",
        "evidenceTier": "official-public",
        "exactLocationMatch": "candidate",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]
