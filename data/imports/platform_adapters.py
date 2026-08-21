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
from urllib.parse import parse_qs, urlparse

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


def acuity_business_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct Acuity's public catalog and class products.

    Public Acuity/Squarespace Scheduling pages embed one bounded ``BUSINESS``
    object before the application boots.  It contains the same products and
    appointment types rendered by the unauthenticated storefront, including
    stable IDs and subscription terms.  This adapter deliberately consumes
    only that object; session state, account data, checkout state, CAPTCHA
    keys, and unrelated inline variables never enter the research fixture.
    """

    if platform_for_url(source_url) != "acuity" or not isinstance(payload, dict):
        return []
    if not text(payload.get("ownerKey")) or not text(payload.get("name")):
        return []

    currency = text(payload.get("currencyAbbreviation")) or "USD"
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()

    def append_candidate(
        node: dict[str, Any],
        title: str,
        amount: float,
        product_type: str,
        cadence: str,
        allowance: dict[str, Any] | None,
        recurring: bool,
        description: str,
        eligibility_type: str,
        restrictions: list[str],
        promotion: bool,
        ordinary_use: bool,
    ) -> None:
        product_id = text(node.get("id"))
        if not product_id or not title or not 0 < amount <= 10_000:
            return
        key = (product_id, amount, product_type)
        if key in seen:
            return
        seen.add(key)
        terms = text(node.get("subscriptionTermsText"))
        semantic = " ".join(value for value in (title, terms) if value)
        alias = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        raw_parts = [title, f"{currency} {amount:g}", terms, description]
        candidates.append({
            "sourceProductId": product_id,
            "sourceProductAliases": [alias] if alias else [],
            "sourceProductIdAuthority": "operator-widget",
            "name": title,
            "amount": amount,
            "currency": currency,
            "cadence": cadence,
            "billingInterval": cadence,
            "intervalCount": 1,
            "productType": product_type,
            "accessScope": description or title,
            "scopeType": "operator-storefront",
            "classAllowance": allowance,
            "promotion": {"isPromotion": promotion, "label": title if promotion else ""},
            "eligibility": {"type": eligibility_type, "restrictions": restrictions},
            "commitment": commitment(node, semantic, recurring),
            "fees": [],
            "ordinaryUse": ordinary_use,
            "bestValueLabel": bool(BEST_VALUE_RE.search(title)),
            "purchaseMethod": "direct-public",
            "rawLabel": " — ".join(value for value in raw_parts if value)[:500],
            "method": "public-acuity-embedded-business",
            "adapter": "acuity",
            "evidenceTier": "official-public",
            "exactLocationMatch": "operator-storefront",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })

    catalog = payload.get("catalog")
    product_collections: list[Any] = []
    if isinstance(catalog, dict):
        product_collections.append(catalog.get("products", []))
    product_collections.append(payload.get("products", []))
    products: list[Any] = []
    for collection in product_collections:
        if isinstance(collection, list):
            products.extend(collection)
        elif isinstance(collection, dict):
            for values in collection.values():
                if isinstance(values, list):
                    products.extend(values)
    for product in products:
        if not isinstance(product, dict):
            continue
        title = text(product.get("title"))
        description = text(product.get("description"))
        amount = amount_from(product)
        if not title or amount is None:
            continue
        terms = text(product.get("subscriptionTermsText"))
        recurring = product.get("isSubscription") is True or bool(
            re.search(r"\bper\s+(?:week|month|year)\b", terms, re.IGNORECASE)
        )
        cadence, _interval_count = cadence_from(product, f"{title} {terms}")
        if recurring and cadence == "one-time":
            cadence = "month"
        semantic = f"{title} {description}"
        allowance = class_allowance(product, semantic, cadence)
        promotion = bool(PROMOTION_RE.search(title))
        restriction = RESTRICTED_RE.search(title)
        eligibility_type = "restricted" if restriction else ("new-client" if promotion else "standard-adult")
        restrictions = (
            [restriction.group(0)]
            if restriction
            else (["Promotional or introductory product"] if promotion else [])
        )
        if recurring:
            product_type = "monthly"
        elif (allowance or {}).get("count") == 1:
            product_type, cadence = "drop-in", "visit"
        elif allowance or re.search(r"\b(?:pack|package)\b", title, re.IGNORECASE):
            product_type = "class-pack"
        else:
            product_type = "offer"
        append_candidate(
            product, title, amount, product_type, cadence, allowance, recurring,
            description, eligibility_type, restrictions, promotion,
            recurring and not promotion and not restriction,
        )

    appointment_types = payload.get("appointmentTypes")
    if isinstance(appointment_types, dict):
        for category, values in appointment_types.items():
            if not isinstance(values, list):
                continue
            for appointment in values:
                if not isinstance(appointment, dict) or appointment.get("active") is False:
                    continue
                title = text(appointment.get("name"))
                description = text(appointment.get("description"))
                amount = amount_from(appointment)
                if not title or amount is None:
                    continue
                combined = f"{category} {title} {description}"
                promotion = bool(PROMOTION_RE.search(title))
                youth = re.search(r"\b(?:youth|kids?|child(?:ren)?)\b", combined, re.IGNORECASE)
                restricted = re.search(r"\b(?:invite only|members? only|assessment required)\b", combined, re.IGNORECASE)
                is_public_class = (
                    text(appointment.get("type")).casefold() == "class"
                    and appointment.get("private") is not True
                    and (number(appointment.get("classSize")) or 0) > 1
                )
                standard_adult = bool(
                    re.search(r"\badult\b|\bgeneral fitness\b", combined, re.IGNORECASE)
                    and not youth
                    and not restricted
                )
                if youth:
                    eligibility_type, restrictions = "youth", ["Youth product"]
                elif restricted:
                    eligibility_type, restrictions = "restricted", [restricted.group(0)]
                elif promotion:
                    eligibility_type, restrictions = "new-client", ["Promotional or introductory product"]
                else:
                    eligibility_type, restrictions = "standard-adult", []
                append_candidate(
                    appointment,
                    title,
                    amount,
                    "drop-in" if is_public_class else "offer",
                    "visit" if is_public_class else "one-time",
                    {"count": 1.0, "period": "visit", "unlimited": False} if is_public_class else None,
                    False,
                    description or text(category),
                    eligibility_type,
                    restrictions,
                    promotion,
                    is_public_class and standard_adult and not promotion,
                )

    return candidates


def momence_membership_card_candidates(
    visible_text: str,
    source_url: str,
    page_title: str = "",
) -> list[dict[str, Any]]:
    """Extract one bounded recurring product from a public Momence page.

    Momence's checkout shell renders the base recurring price above optional
    card-processing arithmetic and contact fields.  The adapter reads only the
    product header, cadence, contract count, and description; it never treats
    the checkout total or optional card fee as membership dues.
    """

    if platform_for_url(source_url) != "momence":
        return []
    try:
        parsed = urlparse(source_url)
    except ValueError:
        return []
    product_match = re.search(r"/(?:membership/[^/]+|m)/(\d{1,12})(?:/|$)", parsed.path, re.IGNORECASE)
    if not product_match:
        return []
    body = text(visible_text).replace("\r", "")
    if not body or not re.search(r"\bRenews every\b", body, re.IGNORECASE):
        return []
    amount_match = re.search(
        r"(?:^|\n)Price\s*(?:\n|\s)+\$\s*(\d{1,6}(?:\.\d{1,2})?)\b",
        body,
        re.IGNORECASE,
    )
    cadence_match = re.search(
        r"\bRenews every\s+(month|week|2 weeks|4 weeks|28 days|year)\b",
        body,
        re.IGNORECASE,
    )
    if not amount_match or not cadence_match:
        return []
    amount = float(amount_match.group(1))
    if not 0 < amount <= 10_000:
        return []
    cadence = cadence_match.group(1).casefold()
    title = text(page_title).split(" - ", 1)[0].strip()
    if not title or title.casefold() in {"momence", "membership"}:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        renew_index = next((index for index, line in enumerate(lines) if line.casefold().startswith("renews every")), -1)
        title = lines[renew_index - 1] if renew_index > 0 else "Momence Membership"
    description_match = re.search(
        r"(?:^|\n)Description\s*(?:\n|\s)+(?P<description>.*?)(?=\n\s*Purchase Now\b|\n\s*Contact Details\b|$)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    description = " ".join(text(description_match.group("description") if description_match else "").split())
    semantic = f"{title} {description}"
    in_person_allowance = re.search(
        r"\b(?:up to\s+)?(\d{1,3})\s+in-person classes\b",
        semantic,
        re.IGNORECASE,
    )
    allowance = (
        {"count": float(in_person_allowance.group(1)), "period": cadence, "unlimited": False}
        if in_person_allowance
        else class_allowance({}, semantic, cadence)
    )
    renewals_match = re.search(r"\b(\d{1,3})\s+renewals? required\b", body, re.IGNORECASE)
    minimum_months = int(renewals_match.group(1)) if renewals_match and cadence == "month" else None
    promotion = bool(PROMOTION_RE.search(title))
    restriction = RESTRICTED_RE.search(title)
    eligibility_type = "restricted" if restriction else ("new-client" if promotion else "standard-adult")
    restrictions = (
        [restriction.group(0)]
        if restriction
        else (["Promotional or introductory product"] if promotion else [])
    )
    product_id = product_match.group(1)
    alias = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    raw_label = " — ".join(filter(None, (
        title,
        f"USD {amount:g} every {cadence}",
        f"{minimum_months} renewals required" if minimum_months else "",
        description,
    )))
    return [{
        "sourceProductId": product_id,
        "sourceProductAliases": list(dict.fromkeys(filter(None, (alias, f"momence-{product_id}")))),
        "sourceProductIdAuthority": "operator-widget",
        "name": title,
        "amount": amount,
        "currency": "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "intervalCount": 1,
        "productType": "monthly",
        "accessScope": description or title,
        "scopeType": "operator-storefront",
        "classAllowance": allowance,
        "promotion": {"isPromotion": promotion, "label": title if promotion else ""},
        "eligibility": {"type": eligibility_type, "restrictions": restrictions},
        "commitment": {
            "type": "fixed-term" if minimum_months else "unknown",
            "minimumMonths": minimum_months,
        },
        "fees": [],
        "ordinaryUse": not promotion and not restriction,
        "bestValueLabel": bool(BEST_VALUE_RE.search(title)),
        "purchaseMethod": "direct-public",
        "rawLabel": raw_label[:500],
        "method": "rendered-momence-membership",
        "adapter": "momence",
        "evidenceTier": "official-public",
        "exactLocationMatch": "operator-storefront",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]


def momence_membership_api_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct one exact recurring product from Momence's public API."""

    if platform_for_url(source_url) != "momence" or not isinstance(payload, dict):
        return []
    route = re.search(r"/_api/primary/plugin/memberships/(\d{1,12})(?:/|$)", urlparse(source_url).path, re.IGNORECASE)
    product_id = text(payload.get("id"))
    if not route or route.group(1) != product_id or text(payload.get("type")).casefold() != "subscription":
        return []
    title = text(payload.get("name"))
    description = " ".join(text(payload.get("description")).split())
    amount = amount_from(payload)
    if not title or amount is None or not 0 < amount <= 10_000:
        return []
    duration = max(1, int(number(payload.get("duration")) or 1))
    unit = text(payload.get("durationUnit")).casefold()
    if unit.startswith("month"):
        cadence, interval_count = "month", duration
    elif unit.startswith("week") and duration in {2, 4}:
        cadence, interval_count = f"{duration} weeks", 1
    elif unit.startswith("week"):
        cadence, interval_count = "week", duration
    elif unit.startswith("day") and duration == 28:
        cadence, interval_count = "4 weeks", 1
    elif unit.startswith("year"):
        cadence, interval_count = "year", duration
    else:
        return []
    in_person_allowance = re.search(
        r"\b(?:up to\s+)?(\d{1,3})\s+in-person classes\b",
        description,
        re.IGNORECASE,
    )
    event_count = number(payload.get("numberOfEvents"))
    allowance_count = float(in_person_allowance.group(1)) if in_person_allowance else event_count
    allowance = (
        {"count": allowance_count, "period": cadence, "unlimited": False}
        if allowance_count is not None
        else class_allowance({}, f"{title} {description}", cadence)
    )
    minimum_renewals = int(number(payload.get("minimumAutoRenews")) or 0)
    minimum_months = minimum_renewals * duration if minimum_renewals and unit.startswith("month") else None
    promotion = bool(PROMOTION_RE.search(title) or payload.get("freeTrial") is True or number(payload.get("paidTrialAmount")) not in {None, 0})
    restrictions: list[str] = []
    eligibility_type = "new-client" if promotion else "standard-adult"
    if payload.get("hasAccessRestrictions") is True:
        eligibility_type = "restricted"
        restrictions.append("Operator access restrictions apply")
    if payload.get("isRestrictedByAge") is True:
        eligibility_type = "restricted"
        minimum_age = number(payload.get("minEligibleAge"))
        maximum_age = number(payload.get("maxEligibleAge"))
        restrictions.append(
            "Age restriction"
            + (f" {int(minimum_age)}+" if minimum_age is not None and maximum_age is None else "")
        )
    if promotion:
        restrictions.append("Promotional or introductory product")
    joining_fee = number(payload.get("joiningFeeInCurrency"))
    fees = []
    if joining_fee is not None and joining_fee > 0:
        fees.append({
            "type": "enrollment",
            "name": "Joining fee",
            "amount": joining_fee,
            "currency": text(payload.get("currency")).upper() or "USD",
            "cadence": "one-time",
            "mandatory": True,
        })
    alias = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    raw_label = " — ".join(filter(None, (
        title,
        f"{text(payload.get('currency')).upper() or 'USD'} {amount:g} every {duration} {unit}",
        f"{minimum_renewals} minimum renewals" if minimum_renewals else "",
        description,
    )))
    return [{
        "sourceProductId": product_id,
        "sourceProductAliases": list(dict.fromkeys(filter(None, (alias, f"momence-{product_id}")))),
        "sourceProductIdAuthority": "operator-widget",
        "name": title,
        "amount": amount,
        "currency": text(payload.get("currency")).upper() or "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "intervalCount": interval_count,
        "productType": "monthly",
        "accessScope": description or title,
        "scopeType": "operator-storefront",
        "classAllowance": allowance,
        "promotion": {"isPromotion": promotion, "label": title if promotion else ""},
        "eligibility": {"type": eligibility_type, "restrictions": restrictions},
        "commitment": {
            "type": "fixed-term" if minimum_renewals else "unknown",
            "minimumMonths": minimum_months,
        },
        "fees": fees,
        "ordinaryUse": not promotion and eligibility_type == "standard-adult",
        "bestValueLabel": bool(BEST_VALUE_RE.search(title)),
        "purchaseMethod": "direct-public",
        "rawLabel": raw_label[:500],
        "method": "public-momence-membership-api",
        "adapter": "momence",
        "evidenceTier": "official-public",
        "exactLocationMatch": "operator-storefront",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]


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


def pushpress_plan_detail_candidates(
    card_text: str,
    detail_text: str,
    source_url: str,
    detail_href: str,
) -> list[dict[str, Any]]:
    """Reconstruct one public PushPress plan after opening its detail modal.

    The list card attaches the product name to one amount and allowance. The
    public modal adds the original billing cadence plus a stable plan ID in
    its non-checkout ``Select`` link. Optional card-processing arithmetic is
    intentionally excluded; the displayed base price is the ACH amount.
    """

    if platform_for_url(source_url) != "pushpress":
        return []
    try:
        source = urlparse(source_url)
        detail = urlparse(detail_href)
    except ValueError:
        return []
    if detail.netloc and detail.netloc.casefold() != source.netloc.casefold():
        return []
    product_match = re.search(
        r"/landing/plans/(?P<id>plan_[A-Za-z0-9_-]{6,80})/participant(?:/|$)",
        detail.path,
        re.IGNORECASE,
    )
    if not product_match:
        return []
    card_lines = [line.strip() for line in text(card_text).splitlines() if line.strip() and line.strip() != "•"]
    if len(card_lines) < 2:
        return []
    name = card_lines[0]
    card_amount = next(
        (
            float(match.group(1).replace(",", ""))
            for line in card_lines[1:]
            if (match := re.fullmatch(r"\$\s*([\d,]+(?:\.\d{1,2})?)", line))
        ),
        None,
    )
    detail_body = text(detail_text).replace("\r", "")
    price_match = re.search(
        r"(?:^|\n)Price\s*(?:\n|\s)+\$\s*([\d,]+(?:\.\d{1,2})?)\b",
        detail_body,
        re.IGNORECASE,
    )
    billing_match = re.search(
        r"(?:^|\n)Billing frequency\s*(?:\n|\s)+([^\n]+)",
        detail_body,
        re.IGNORECASE,
    )
    sessions_match = re.search(
        r"(?:^|\n)Sessions\s*(?:\n|\s)+(Unlimited|\d{1,4})\b",
        detail_body,
        re.IGNORECASE,
    )
    if not price_match or not billing_match:
        return []
    amount = float(price_match.group(1).replace(",", ""))
    if card_amount is None or abs(card_amount - amount) > 0.01 or not 0 < amount <= 10_000:
        return []
    billing_label = " ".join(billing_match.group(1).split())
    billing_lower = billing_label.casefold()
    if re.search(r"\b(?:every\s+)?4\s+weeks?\b|\b28\s+days?\b", billing_lower):
        cadence = "4 weeks"
    elif re.search(r"\b(?:every\s+)?2\s+weeks?\b|\bbiweekly\b", billing_lower):
        cadence = "2 weeks"
    elif re.search(r"\b(?:every\s+)?month(?:ly)?\b", billing_lower):
        cadence = "month"
    elif re.search(r"\b(?:every\s+)?week(?:ly)?\b", billing_lower):
        cadence = "week"
    elif re.search(r"\b(?:one[ -]?time|once|non-recurring)\b", billing_lower):
        cadence = "one-time"
    else:
        return []
    session_label = sessions_match.group(1) if sessions_match else ""
    unlimited = session_label.casefold() == "unlimited"
    session_count = None if unlimited or not session_label else float(session_label)
    allowance = (
        {"count": session_count, "period": "visit" if cadence == "one-time" else cadence, "unlimited": unlimited}
        if sessions_match
        else None
    )
    promotion = bool(PROMOTION_RE.search(name))
    restriction = RESTRICTED_RE.search(name)
    name_lower = name.casefold()
    if cadence != "one-time":
        product_type = "monthly"
    elif DROP_IN_RE.search(name):
        product_type = "drop-in"
        cadence = "visit"
    elif re.search(r"\b(?:punch card|class pass|pack)\b", name, re.IGNORECASE):
        product_type = "class-pack"
    else:
        product_type = "offer"
    cycle_match = re.search(r"minimum\s+(\d{1,3})\s+cycles?", name, re.IGNORECASE)
    prepaid_months = re.search(r"\b(\d{1,2})\s+months?\s+prepaid\b", name, re.IGNORECASE)
    if cycle_match:
        cycles = int(cycle_match.group(1))
        commitment_value: dict[str, Any] = {
            "type": "minimum-term",
            "minimumMonths": cycles if cadence == "month" else None,
            "minimumDays": cycles * 28 if cadence == "4 weeks" else None,
            "rawLabel": cycle_match.group(0),
        }
    elif prepaid_months:
        commitment_value = {
            "type": "fixed-term",
            "minimumMonths": int(prepaid_months.group(1)),
            "minimumDays": None,
            "rawLabel": prepaid_months.group(0),
        }
    else:
        commitment_value = {
            "type": "none" if cadence in {"one-time", "visit"} else "unknown",
            "minimumMonths": None,
            "minimumDays": None,
            "rawLabel": "",
        }
    if restriction:
        eligibility_type, restrictions = "restricted", [restriction.group(0)]
    elif promotion:
        eligibility_type, restrictions = "new-client", ["Promotional or introductory product"]
    else:
        eligibility_type, restrictions = "standard-adult", []
    product_id = product_match.group("id")
    alias = re.sub(r"[^a-z0-9]+", "-", re.sub(r"\([^)]*\)", "", name_lower)).strip("-")
    raw_label = " — ".join(filter(None, (
        name,
        f"USD {amount:g}",
        billing_label,
        f"{session_label} sessions" if session_label else "",
    )))
    return [{
        "sourceProductId": product_id,
        "sourceProductAliases": [alias] if alias else [],
        "sourceProductIdAuthority": "operator-widget",
        "name": name,
        "amount": amount,
        "currency": "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "intervalCount": 1,
        "productType": product_type,
        "accessScope": f"{session_label} sessions" if session_label else name,
        "scopeType": "operator-storefront",
        "classAllowance": allowance,
        "promotion": {"isPromotion": promotion, "label": name if promotion else ""},
        "eligibility": {"type": eligibility_type, "restrictions": restrictions},
        "commitment": commitment_value,
        "fees": [],
        "ordinaryUse": not promotion and not restriction and product_type in {"monthly", "drop-in"},
        "bestValueLabel": bool(BEST_VALUE_RE.search(name)),
        "purchaseMethod": "direct-public",
        "rawLabel": raw_label[:500],
        "method": "rendered-pushpress-plan-detail",
        "adapter": "pushpress",
        "evidenceTier": "official-public",
        "exactLocationMatch": "operator-storefront",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]


def wix_purchase_card_candidates(
    card_text: str,
    source_url: str,
    purchase_href: str,
) -> list[dict[str, Any]]:
    """Reconstruct one bounded Wix purchase card with a stable checkout link.

    Wix pages often render a plan as sibling text elements rather than a
    semantic article.  The rendered crawler supplies only the nearest DOM
    ancestor containing one purchase action, so prices from adjacent cards or
    savings copy cannot attach to this offer.  A same-origin or recognized
    public booking URL is required before a candidate is emitted.
    """

    try:
        source = urlparse(source_url)
        target = urlparse(purchase_href)
    except ValueError:
        return []
    if source.scheme not in {"http", "https"} or not source.netloc or target.scheme not in {"http", "https"}:
        return []
    same_origin = target.netloc.casefold() == source.netloc.casefold()
    if not same_origin and not platform_for_url(purchase_href):
        return []

    lines = [" ".join(line.split()) for line in text(card_text).splitlines() if " ".join(line.split())]
    if not 3 <= len(lines) <= 80:
        return []
    price_indexes = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"\$\s*[\d,]+(?:\.\d{1,2})?", line)
    ]
    if len(price_indexes) != 1:
        return []
    price_index = price_indexes[0]
    amount_match = MONEY_RE.fullmatch(lines[price_index])
    if not amount_match:
        return []
    amount = float(amount_match.group(1).replace(",", ""))
    if not 0 < amount <= 25_000:
        return []
    name = next(
        (
            line
            for line in lines[price_index + 1:price_index + 5]
            if 2 <= len(line) <= 100 and PRODUCT_SEMANTIC_RE.search(line)
        ),
        "",
    )
    if not name:
        return []

    combined = " ".join(lines)
    name_lower = name.casefold()
    if DROP_IN_RE.search(name):
        cadence, product_type = "visit", "drop-in"
    elif re.search(r"\bweek(?:ly)?\b", name, re.IGNORECASE):
        cadence, product_type = "one-time", "class-pack"
    elif re.search(r"\b(?:annual|year(?:ly)?)\b", name, re.IGNORECASE):
        cadence, product_type = "year", "monthly"
    elif re.search(r"\bmonth(?:ly)?\b", name, re.IGNORECASE):
        cadence, product_type = "month", "monthly"
    else:
        return []

    query = {key.casefold(): values for key, values in parse_qs(target.query).items()}
    product_id = next(
        (
            text(values[0])
            for key in ("prodid", "productid", "planid", "serviceid", "itemid")
            if (values := query.get(key)) and text(values[0])
        ),
        "",
    )
    if not product_id or len(product_id) > 100:
        return []
    slug = re.sub(r"[^a-z0-9]+", "-", name_lower).strip("-")
    aliases = [slug] if slug else []
    reordered = re.fullmatch(r"monthly\s+(.+?)\s+membership", name_lower)
    if reordered:
        aliases.append(f"{re.sub(r'[^a-z0-9]+', '-', reordered.group(1)).strip('-')}-monthly")

    household = bool(re.search(r"\b(?:couples?|two|2)\s+(?:people|person|membership)|\bmembership\s+for\s+2\b", combined, re.IGNORECASE))
    promotion = bool(PROMOTION_RE.search(name))
    if product_type == "drop-in":
        allowance = {"count": 1, "period": "visit", "unlimited": False}
    elif re.search(r"\bunlimited\b", combined, re.IGNORECASE):
        allowance = {"count": None, "period": cadence, "unlimited": True}
    else:
        allowance = None

    if cadence == "month" and re.search(r"\b(?:cancel\s+any\s*time|no\s+contracts?)\b", combined, re.IGNORECASE):
        commitment = {"type": "month-to-month", "minimumMonths": None, "minimumDays": None, "rawLabel": "Cancel anytime"}
    elif cadence == "year":
        commitment = {"type": "fixed-term", "minimumMonths": 12, "minimumDays": None, "rawLabel": "Annual membership"}
    else:
        commitment = {"type": "none" if cadence in {"visit", "one-time"} else "unknown", "minimumMonths": None, "minimumDays": None, "rawLabel": ""}

    scope_type = "multi-location" if re.search(r"\baccess\s+to\s+\d+\s+gym\s+locations?\b", combined, re.IGNORECASE) else "single-location"
    restrictions = ["Two-person household membership"] if household else []
    raw_label = " — ".join(filter(None, (name, f"USD {amount:g}", cadence, "Unlimited" if (allowance or {}).get("unlimited") else "")))
    return [{
        "sourceProductId": product_id,
        "sourceProductAliases": list(dict.fromkeys(alias for alias in aliases if alias)),
        "sourceProductIdAuthority": "operator-widget",
        "name": name,
        "amount": amount,
        "currency": "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "intervalCount": 1,
        "productType": product_type,
        "accessScope": name,
        "scopeType": scope_type,
        "classAllowance": allowance,
        "promotion": {"isPromotion": promotion, "label": name if promotion else ""},
        "eligibility": {"type": "household" if household else "standard-adult", "restrictions": restrictions},
        "commitment": commitment,
        "fees": [],
        "ordinaryUse": not promotion and not household and product_type in {"monthly", "drop-in"},
        "bestValueLabel": bool(BEST_VALUE_RE.search(name)),
        "purchaseMethod": "direct-public",
        "rawLabel": raw_label[:500],
        "method": "rendered-wix-purchase-card",
        "adapter": "wix-purchase-card",
        "evidenceTier": "official-public",
        "exactLocationMatch": "operator-market-catalog" if scope_type == "multi-location" else "exact-location",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }]
