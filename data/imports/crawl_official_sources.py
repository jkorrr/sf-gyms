"""Conservative crawler for official gym pages and linked public storefronts.

The crawler discovers candidate observations; it never auto-publishes a price.
It does not submit forms, authenticate, or transmit personal information.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import time
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

import cost_coverage as coverage
import platform_adapters

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
ATTEMPTS_PATH = ROOT / "data" / "imports" / "official-crawl-attempts.json"
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "official-crawl-observations.json"
CACHE_PATH = ROOT / "data" / "imports" / "official-crawl-cache.json"
LOCATION_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "official-location-observations.json"
DEAL_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "deal-observations.json"
DEAL_REPORT_PATH = ROOT / "data" / "imports" / "deal-report.json"
USER_AGENT = "sf-gyms-public-research/1.0 (+https://github.com/jkorrr/sf-gyms)"
MAX_RESPONSE_BYTES = 4_000_000
DOMAIN_DELAY_SECONDS = 1.5
MAX_DOMAIN_429S = 2
STALE_AFTER_DAYS = 35

BOOKING_DOMAINS = {
    "clients.mindbodyonline.com",
    "cart.mindbodyonline.com",
    "www.clubready.com",
    "clubready.com",
    "marianatek.com",
    "marianaiframes.com",
    "momence.com",
    "www.wellnessliving.com",
    "wellnessliving.com",
    "pushpress.com",
    "www.pushpress.com",
    "wodify.com",
    "app.wodify.com",
    "onbookee.com",
    "members.clubpilates.com",
    "members.stretchlab.com",
    "zenplanner.com",
    "as.me",
    "gymdesk.com",
    "eventbrite.com",
    "acuityscheduling.com",
    "squarespacescheduling.com",
    "classpass.com",
}
MONEY_RE = re.compile(r"\$(\d{1,4}(?:\.\d{1,2})?)")
MONTHLY_RE = re.compile(
    r"(?P<label>.{0,110}?\$(?P<amount>\d{1,4}(?:\.\d{1,2})?)[^$]{0,70}?(?:/\s*mo(?:nth)?|per\s+month|monthly))",
    re.IGNORECASE | re.DOTALL,
)
DROP_IN_AFTER_RE = re.compile(
    r"(?P<label>.{0,90}?(?:drop[ -]?in|single (?:class|visit)|day pass).{0,70}?\$(?P<amount>\d{1,4}(?:\.\d{1,2})?))",
    re.IGNORECASE | re.DOTALL,
)
DROP_IN_BEFORE_RE = re.compile(
    r"(?P<label>.{0,90}?\$(?P<amount>\d{1,4}(?:\.\d{1,2})?)[^$]{0,45}?(?:drop[ -]?in|single (?:class|visit)|day pass))",
    re.IGNORECASE | re.DOTALL,
)
PROMOTION_RE = re.compile(
    r"\b(?:intro|introductory|trial|first month|founding|presale|limited time|new client|new member|"
    r"save|discount|percent off|free month|waived|special offer|flash sale)\b|\b\d{1,2}%\s*off\b",
    re.IGNORECASE,
)
RESTRICTED_RE = re.compile(r"\b(?:student|resident|employee|employer|corporate|senior|youth|military)\b", re.IGNORECASE)
CLASS_ALLOWANCE_RE = re.compile(r"\b(\d{1,3})\s*(?:classes?|visits?|sessions?)\s*(?:per|/)?\s*(week|month|30 days|4 weeks)?\b", re.IGNORECASE)
VISIBLE_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'’& -]{2,55}\s(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|Lane|Ln\.?|Drive|Dr\.?|Way|Court|Ct\.?)"
    r"(?:\s*(?:,|\|)\s*(?:Suite|Ste|Unit|Floor|Fl)\s*[A-Za-z0-9-]+)?(?:\s*,?\s*San Francisco(?:\s*,?\s*(?:CA|California))?(?:\s+941\d{2})?)?\b",
    re.IGNORECASE,
)
RESEARCH_PATH_RE = re.compile(
    r"/(?:pricing|prices|rates?|memberships?|plans?|packages?|passes|drop-?in|buy|join|locations?)(?:/|$|[?#])",
    re.IGNORECASE,
)
RESEARCH_EXCLUDE_RE = re.compile(r"/(?:login|signin|sign-in|account|checkout|cart)(?:/|$|[?#])", re.IGNORECASE)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.json_ld: list[str] = []
        self.hydration_json: list[str] = []
        self.visible: list[str] = []
        self._in_script = False
        self._script_type = ""
        self._script_id = ""
        self._script_parts: list[str] = []
        self._hidden_depth = 0
        self._tag_stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag.casefold() == "iframe" and values.get("src"):
            self.links.append(values["src"])
        if tag.casefold() == "script":
            self._in_script = True
            self._script_type = values.get("type", "").casefold()
            self._script_id = values.get("id", "").casefold()
            self._script_parts = []
        introduced_hidden = bool(values.get("hidden") or values.get("aria-hidden", "").casefold() == "true" or "display:none" in values.get("style", "").replace(" ", "").casefold())
        self._tag_stack.append((tag.casefold(), introduced_hidden))
        if introduced_hidden:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script":
            if self._script_type == "application/ld+json" and self._script_parts:
                self.json_ld.append("".join(self._script_parts))
            elif self._script_parts and (self._script_type in {"application/json", "application/manifest+json"} or self._script_id == "__next_data__"):
                self.hydration_json.append("".join(self._script_parts))
            self._in_script = False
            self._script_type = ""
            self._script_id = ""
            self._script_parts = []
        closing = tag.casefold()
        while self._tag_stack:
            opened, introduced_hidden = self._tag_stack.pop()
            if introduced_hidden:
                self._hidden_depth = max(0, self._hidden_depth - 1)
            if opened == closing:
                break

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
        elif not self._hidden_depth and data.strip():
            self.visible.append(data.strip())


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:220]


def hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except ValueError:
        return ""


def is_public_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = MONEY_RE.search(text(value))
    if match:
        return float(match.group(1))
    try:
        return float(text(value))
    except ValueError:
        return None


def platform_name(source_url: str) -> str:
    detected = platform_adapters.platform_for_url(source_url)
    if detected:
        return detected
    host = hostname(source_url)
    mappings = (
        ("mindbodyonline.com", "mindbody"),
        ("clubready.com", "clubready"),
        ("marianatek.com", "mariana-tek"),
        ("marianaiframes.com", "mariana-tek"),
        ("momence.com", "momence"),
        ("wellnessliving.com", "wellnessliving"),
        ("pushpress.com", "pushpress"),
        ("wodify.com", "wodify"),
        ("onbookee.com", "bookee"),
        ("members.clubpilates.com", "xponential-member-app"),
        ("members.stretchlab.com", "xponential-member-app"),
        ("zenplanner.com", "zen-planner"),
        ("as.me", "acuity"),
        ("gymdesk.com", "gymdesk"),
    )
    return next((label for domain, label in mappings if host == domain or host.endswith(f".{domain}")), "operator-site")


def candidate_metadata(label: str, cadence: str) -> dict[str, Any]:
    clean = normalized_label(label)
    lower = clean.casefold()
    cadence_lower = cadence.casefold()
    allowance = CLASS_ALLOWANCE_RE.search(clean)
    if allowance:
        class_allowance: dict[str, Any] | None = {
            "count": float(allowance.group(1)),
            "period": (allowance.group(2) or "month").casefold(),
        }
    elif "unlimited" in lower:
        class_allowance = {"count": None, "period": "month", "unlimited": True}
    else:
        class_allowance = None
    if any(word in lower for word in ("drop-in", "drop in", "single class", "single visit", "day pass")):
        product_type = "drop-in"
    elif any(word in cadence_lower for word in ("month", "week", "year", "p1m", "mon")) or "membership" in lower:
        product_type = "monthly"
    else:
        product_type = "offer"
    commitment_match = re.search(r"\b(\d{1,2})[ -]?(?:month|mo)\b", lower)
    return {
        "productType": product_type,
        "classAllowance": class_allowance,
        "promotion": {"isPromotion": bool(PROMOTION_RE.search(clean)), "label": clean if PROMOTION_RE.search(clean) else ""},
        "eligibility": {
            "type": "restricted" if RESTRICTED_RE.search(clean) else "standard-adult",
            "restrictions": [RESTRICTED_RE.search(clean).group(0)] if RESTRICTED_RE.search(clean) else [],
        },
        "commitment": {
            "type": "fixed-term" if commitment_match else ("month-to-month" if "month-to-month" in lower or "no commitment" in lower else "unknown"),
            "minimumMonths": int(commitment_match.group(1)) if commitment_match else None,
        },
    }


def structured_candidates(json_blocks: list[str], source_url: str, method: str = "json-ld") -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in json_blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in walk_json(parsed):
            node_type = node.get("@type") or node.get("type")
            types = {text(item).casefold() for item in node_type} if isinstance(node_type, list) else {text(node_type).casefold()}
            price_keys = ("price", "lowPrice", "amount", "unitAmount", "priceAmount")
            if method == "json-ld" and not types.intersection({"offer", "aggregateoffer", "unitpricespecification", "product"}):
                continue
            if method != "json-ld" and not any(key in node for key in price_keys):
                continue
            amount = numeric(next((node.get(key) for key in price_keys if node.get(key) is not None), None))
            if amount is None or amount <= 0 or amount > 2000:
                continue
            label = text(node.get("name") or node.get("title") or node.get("label") or node.get("productName")) or text(node.get("description")) or "/".join(sorted(types))
            cadence = text(node.get("unitCode") or node.get("billingDuration") or node.get("billingIncrement") or node.get("billingPeriod") or node.get("interval") or node.get("frequency"))
            if method != "json-ld" and not normalized_label(label):
                continue
            metadata = candidate_metadata(label, cadence)
            candidates.append(
                {
                    "sourceProductId": text(node.get("@id") or node.get("id") or node.get("productId") or node.get("priceId")),
                    "amount": amount,
                    "currency": text(node.get("priceCurrency")) or "USD",
                    "rawLabel": normalized_label(label),
                    "cadence": normalized_label(cadence),
                    **metadata,
                    "method": method,
                    "adapter": platform_name(source_url),
                    "evidenceTier": "official-public",
                    "exactLocationMatch": "candidate",
                    "sourceUrl": source_url,
                    "autoPublishEligible": False,
                }
            )
    return candidates


def crunch_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct regular and promotional Crunch plan cards for review.

    Crunch renders the current discounted dues beside an explicit ``reg:``
    amount.  The generic monthly regex sees only the first number and can make
    a temporary discount look like an ordinary price.  This adapter activates
    only on Crunch-owned pages and only when both amounts and the public
    month-to-month language are present.  Payment-table fees are associated
    with their matching plan/variant rather than copied across cards.
    """

    host = hostname(source_url)
    if not (host == "crunch.com" or host.endswith(".crunch.com")):
        return []
    # The live cards render cents as a separate typographic node (for example
    # ``$`` / ``127`` / ``20`` / ``/mo``). Rejoin only that explicit dues
    # shape before collapsing whitespace; ordinary whole-dollar cards remain
    # untouched.
    line_value = re.sub(
        r"\$\s*(\d{1,4})\s+(\d{2})\s*(?=/\s*mo(?:nth)?\b)",
        r"$\1.\2",
        visible_text,
        flags=re.IGNORECASE,
    )
    line_value = line_value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\s+", " ", line_value).strip()
    if not re.search(r"\b(?:month-to-month|no commitment)\b", value, re.IGNORECASE):
        return []

    card_re = re.compile(
        r"^(?P<name>All Crunch|City Crunch|One Crunch)\s*$\n.{0,180}?"
        r"\$\s*(?P<current>\d{1,4}(?:\.\d{1,2})?)\s*(?:/\s*mo(?:nth)?|monthly)"
        r".{0,180}?\breg(?:ular)?\s*:?\s*\$\s*(?P<regular>\d{1,4}(?:\.\d{1,2})?)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    cards = list(card_re.finditer(line_value))
    if not cards:
        return []

    plan_order = ("all crunch", "city crunch", "one crunch")

    def fee_pairs(label: str, stop_labels: tuple[str, ...]) -> dict[str, tuple[float, float]]:
        stop = "|".join(re.escape(item) for item in stop_labels)
        segment = re.search(
            rf"\b{re.escape(label)}\b(?P<body>.{{0,500}}?)(?=\b(?:{stop})\b|$)",
            value,
            re.IGNORECASE,
        )
        if not segment:
            return {}
        amounts = [float(item) for item in MONEY_RE.findall(segment.group("body"))]
        if len(amounts) < 6:
            return {}
        return {name: (amounts[index * 2], amounts[index * 2 + 1]) for index, name in enumerate(plan_order)}

    enrollment = fee_pairs("Enrollment Fee", ("First Month Dues", "Processing Fee", "Subtotal"))
    processing = fee_pairs("Processing Fee", ("Subtotal", "Last Month Dues", "Annual Fee"))
    scopes = {
        "all crunch": ("Worldwide Crunch club access", "multi-location"),
        "city crunch": ("California Signature Crunch club access", "multi-location"),
        "one crunch": ("Ordinary access at the named Crunch club", "single-location"),
    }
    candidates: list[dict[str, Any]] = []
    for card in cards:
        display_name = " ".join(word.capitalize() for word in card.group("name").split())
        key = card.group("name").casefold()
        current = float(card.group("current"))
        regular = float(card.group("regular"))
        if current <= 0 or regular <= 0 or current >= regular or regular > 2000:
            continue
        access_scope, scope_type = scopes[key]
        best_value = key == "all crunch" and bool(
            re.search(r"(?:Best Value.{0,100}All Crunch|All Crunch.{0,100}Best Value)", value, re.IGNORECASE)
        )

        def fees(variant: int) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            if key in enrollment:
                result.append({
                    "type": "enrollment", "amount": enrollment[key][variant], "currency": "USD",
                    "cadence": "one-time", "mandatory": True,
                })
            if key in processing:
                result.append({
                    "type": "processing", "amount": processing[key][variant], "currency": "USD",
                    "cadence": "one-time", "mandatory": True,
                })
            return result

        common = {
            "currency": "USD",
            "cadence": "month",
            "productType": "monthly",
            "accessScope": access_scope,
            "scopeType": scope_type,
            "classAllowance": None,
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {"type": "month-to-month", "minimumMonths": None},
            "method": "visible-crunch-plan-card",
            "adapter": "crunch-plan-cards",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-location",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        }
        slug = key.replace(" ", "-")
        candidates.append({
            **common,
            "sourceProductId": f"{slug}-regular",
            "amount": regular,
            "rawLabel": f"{display_name} regular rate ${regular:g}/month",
            "promotion": {"isPromotion": False, "label": ""},
            "fees": fees(0),
            "bestValueLabel": best_value,
        })
        candidates.append({
            **common,
            "sourceProductId": f"{slug}-current-offer",
            "amount": current,
            "rawLabel": f"{display_name} current discounted rate ${current:g}/month; regular ${regular:g}/month",
            "promotion": {"isPromotion": True, "label": "Current discounted offer"},
            "fees": fees(1),
            "bestValueLabel": False,
        })
    return candidates


def twenty_four_hour_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct the public 24 Hour Fitness membership matrix for review.

    The location cards split plan names, dues, billing variants, and ``Due
    Today`` values across typographic nodes.  Generic currency extraction does
    not see the dues at all because the dollar sign is omitted from the
    accessible amount node.  This adapter keeps no-commitment, commitment, and
    yearly variants distinct and never treats upfront dues as a fee.
    """

    host = hostname(source_url)
    if not (host == "24hourfitness.com" or host.endswith(".24hourfitness.com")):
        return []
    value = re.sub(r"\s+", " ", visible_text).strip()
    if not all(label in value for label in ("Monthly", "Monthly Commitment", "Yearly", "Silver")):
        return []

    section_re = re.compile(
        r"\bMonthly\b(?P<monthly>.*?)(?:\bMonthly Commitment\b)"
        r"(?P<commitment>.*?)(?:\bYearly\b)(?P<yearly>.*?)(?:\bChoose the gym membership|\bWhat you get:|$)",
        re.IGNORECASE,
    )
    sections = section_re.search(value)
    if not sections:
        return []

    annual_match = re.search(r"\$(\d{1,4}(?:\.\d{1,2})?)\s+Annual Fee required", value, re.IGNORECASE)
    annual_fee = float(annual_match.group(1)) if annual_match else None

    def fees() -> list[dict[str, Any]]:
        return ([{
            "type": "annual", "amount": annual_fee, "currency": "USD",
            "cadence": "yearly", "mandatory": True,
        }] if annual_fee is not None else [])

    common = {
        "currency": "USD",
        "productType": "monthly",
        "classAllowance": None,
        "eligibility": {"type": "standard-adult", "restrictions": []},
        "method": "visible-24-hour-membership-matrix",
        "adapter": "24-hour-membership-matrix",
        "evidenceTier": "official-public",
        "exactLocationMatch": "exact-location",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }
    scopes = {
        "platinum": ("National club access with premium amenities and a buddy pass", "multi-location"),
        "gold": ("Northern California regional club access with classes and amenities", "multi-location"),
        "silver": ("One-club cardio and weights access at the named location", "single-location"),
        "national": ("National club access with classes and amenities", "multi-location"),
    }
    candidates: list[dict[str, Any]] = []
    monthly_section = sections.group("monthly")
    monthly_re = re.compile(
        r"(?P<name>Platinum|Gold|Silver)\s+as low as\s+\$?(?P<amount>\d{1,4}(?:\.\d{1,2})?)"
        r"\s+per month\s+\$?(?P<due>\d{1,4}(?:\.\d{1,2})?)\s+Due Today",
        re.IGNORECASE,
    )
    for match in monthly_re.finditer(monthly_section):
        name = match.group("name").capitalize()
        key = name.casefold()
        amount = float(match.group("amount"))
        card_prefix = monthly_section[max(0, match.start() - 40):match.start()]
        promoted = bool(re.search(r"\$10\s+OFF", card_prefix, re.IGNORECASE))
        access_scope, scope_type = scopes[key]
        candidates.append({
            **common,
            "sourceProductId": f"{key}-monthly-no-commitment",
            "name": f"{name} Monthly",
            "amount": amount,
            "cadence": "month",
            "billingInterval": "month",
            "accessScope": access_scope,
            "scopeType": scope_type,
            "commitment": {"type": "month-to-month", "minimumMonths": None},
            "promotion": {"isPromotion": promoted, "label": "$10 off ongoing monthly dues" if promoted else ""},
            "fees": fees(),
            "rawLabel": f"{name} ${amount:g}/month; ${float(match.group('due')):g} due today",
        })

    commitment_match = re.search(
        r"Platinum\s+\$?(?P<amount>\d{1,4}(?:\.\d{1,2})?)\s+per month\s+"
        r"\$?(?P<due>\d{1,4}(?:\.\d{1,2})?)\s+Due Today",
        sections.group("commitment"), re.IGNORECASE,
    )
    if commitment_match:
        amount = float(commitment_match.group("amount"))
        candidates.append({
            **common,
            "sourceProductId": "platinum-monthly-commitment",
            "name": "Platinum Monthly Commitment",
            "amount": amount,
            "cadence": "month",
            "billingInterval": "month",
            "accessScope": scopes["platinum"][0],
            "scopeType": scopes["platinum"][1],
            "commitment": {"type": "fixed-term", "minimumMonths": None, "rawLabel": "Monthly Commitment"},
            "promotion": {"isPromotion": False, "label": ""},
            "fees": fees(),
            "rawLabel": f"Platinum commitment ${amount:g}/month; ${float(commitment_match.group('due')):g} due today",
        })

    yearly_re = re.compile(
        r"(?P<name>Platinum|National)\s+\$?(?P<equivalent>\d{1,4}(?:\.\d{1,2})?)\s+per month\s+"
        r"\$?(?P<due>\d{1,5}(?:\.\d{1,2})?)\s+Due Today",
        re.IGNORECASE,
    )
    for match in yearly_re.finditer(sections.group("yearly")):
        name = match.group("name").capitalize()
        key = name.casefold()
        annual_dues = float(match.group("due"))
        access_scope, scope_type = scopes[key]
        candidates.append({
            **common,
            "sourceProductId": f"{key}-yearly-auto-renewal",
            "name": f"{name} Yearly Auto-Renewal",
            "amount": annual_dues,
            "cadence": "year",
            "billingInterval": "year",
            "accessScope": access_scope,
            "scopeType": scope_type,
            "commitment": {"type": "prepaid", "minimumMonths": 12},
            "promotion": {"isPromotion": False, "label": ""},
            "fees": fees(),
            "rawLabel": f"{name} ${annual_dues:g}/year; displayed equivalent ${float(match.group('equivalent')):g}/month",
        })
    return candidates


def equinox_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Extract the complete public Equinox membership cards for a club."""

    host = hostname(source_url)
    if not (host == "equinox.com" or host.endswith(".equinox.com")):
        return []
    value = re.sub(r"\s+", " ", visible_text).strip()
    card_re = re.compile(
        r"(?P<name>Destination West|All-Access|Destination|Select)\s+"
        r"(?P<popular>Most Popular\s+)?\$(?P<amount>\d{1,4}(?:\.\d{1,2})?)\s*/\s*mo\b",
        re.IGNORECASE,
    )
    cards = list(card_re.finditer(value))
    if len(cards) < 2:
        return []
    product_ids = {"select": "15", "all-access": "2931", "destination": "2735", "destination west": "2737"}
    scopes = {
        "select": ("One-club access at the named Equinox location", "single-location"),
        "all-access": ("Access to 90+ Equinox clubs across North America", "multi-location"),
        "destination": ("Access to 110+ Equinox clubs globally including Sports Club locations", "multi-location"),
        "destination west": ("Destination access plus Equinox Century City and Santa Monica East", "multi-location"),
    }
    candidates: list[dict[str, Any]] = []
    for card in cards:
        display_name = " ".join(word.capitalize() for word in card.group("name").split())
        key = card.group("name").casefold()
        amount = float(card.group("amount"))
        access_scope, scope_type = scopes[key]
        candidates.append({
            "sourceProductId": product_ids[key],
            "name": display_name,
            "amount": amount,
            "currency": "USD",
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "accessScope": access_scope,
            "scopeType": scope_type,
            "classAllowance": None,
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {"type": "unknown", "minimumMonths": None},
            "promotion": {"isPromotion": False, "label": ""},
            "fees": [],
            "bestValueLabel": bool(card.group("popular")),
            "rawLabel": f"{display_name} ${amount:g}/month" + ("; Most Popular" if card.group("popular") else ""),
            "method": "visible-equinox-plan-card",
            "adapter": "equinox-plan-cards",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-location",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates


def planet_fitness_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Associate Planet Fitness dues, fees, commitments, and access cards."""

    host = hostname(source_url)
    if not (host == "planetfitness.com" or host.endswith(".planetfitness.com")):
        return []
    value = re.sub(r"\s+", " ", visible_text).strip()
    black_start = re.search(r"PF BLACK CARD", value, re.IGNORECASE)
    classic_start = re.search(r"\bClassic\b", value, re.IGNORECASE)
    if not black_start or not classic_start or classic_start.start() <= black_start.start():
        return []
    end_match = re.search(r"\bCLUB INFO\b", value[classic_start.end():], re.IGNORECASE)
    classic_end = classic_start.end() + end_match.start() if end_match else len(value)
    segments = [
        ("PF Black Card", "pf-black-card", value[black_start.start():classic_start.start()]),
        ("Classic", "classic", value[classic_start.start():classic_end]),
    ]
    candidates: list[dict[str, Any]] = []
    for name, slug, segment in segments:
        amount_match = re.search(r"\$(\d{1,4}(?:\.\d{1,2})?)\s*/\s*mo\b", segment, re.IGNORECASE)
        startup_match = re.search(r"\$(\d{1,4}(?:\.\d{1,2})?)\s+Startup Fee", segment, re.IGNORECASE)
        annual_match = re.search(r"\$(\d{1,4}(?:\.\d{1,2})?)\s+Annual Fee", segment, re.IGNORECASE)
        if not amount_match or not startup_match or not annual_match:
            continue
        amount = float(amount_match.group(1))
        no_commitment = bool(re.search(r"No Commitment", segment, re.IGNORECASE))
        term_match = re.search(r"(\d{1,2})\s+Month Commitment", segment, re.IGNORECASE)
        presale = bool(re.search(r"Pre-Grand Opening|Pre-Sale|SPECIAL DEAL", segment, re.IGNORECASE))
        worldwide = slug == "pf-black-card"
        startup_amount = float(startup_match.group(1))
        plan_fees = []
        if startup_amount > 0:
            plan_fees.append({"type": "enrollment", "amount": startup_amount, "currency": "USD", "cadence": "one-time", "mandatory": True})
        plan_fees.append({"type": "annual", "amount": float(annual_match.group(1)), "currency": "USD", "cadence": "yearly", "mandatory": True})
        candidates.append({
            "sourceProductId": slug,
            "name": name,
            "amount": amount,
            "currency": "USD",
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "accessScope": "All Planet Fitness clubs worldwide" if worldwide else "Unlimited access to the named home club",
            "scopeType": "multi-location" if worldwide else "single-location",
            "classAllowance": None,
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {
                "type": "month-to-month" if no_commitment else "fixed-term" if term_match else "unknown",
                "minimumMonths": int(term_match.group(1)) if term_match else None,
            },
            "promotion": {"isPromotion": presale, "label": "Pre-grand-opening sale" if presale else ""},
            "fees": plan_fees,
            "bestValueLabel": worldwide and bool(re.search(r"Best Value", segment, re.IGNORECASE)),
            "rawLabel": f"{name} ${amount:g}/month; ${startup_amount:g} startup; ${float(annual_match.group(1)):g} annual",
            "method": "visible-planet-fitness-plan-card",
            "adapter": "planet-fitness-plan-cards",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-location",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates


def visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    specialized = (
        crunch_visible_candidates(visible_text, source_url)
        or twenty_four_hour_visible_candidates(visible_text, source_url)
        or equinox_visible_candidates(visible_text, source_url)
        or planet_fitness_visible_candidates(visible_text, source_url)
    )
    candidates: list[dict[str, Any]] = list(specialized)
    patterns = (("drop-in", DROP_IN_AFTER_RE), ("drop-in", DROP_IN_BEFORE_RE)) if specialized else (
        ("monthly", MONTHLY_RE), ("drop-in", DROP_IN_AFTER_RE), ("drop-in", DROP_IN_BEFORE_RE)
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(visible_text):
            amount = float(match.group("amount"))
            if amount <= 0 or amount > 2000:
                continue
            candidates.append(
                {
                    "amount": amount,
                    "currency": "USD",
                    "rawLabel": normalized_label(match.group("label")),
                    "cadence": "month" if kind == "monthly" else "visit",
                    "productType": kind,
                    **candidate_metadata(match.group("label"), "month" if kind == "monthly" else "visit"),
                    "method": "visible-text-candidate",
                    "adapter": platform_name(source_url),
                    "evidenceTier": "official-public",
                    "exactLocationMatch": "candidate",
                    "sourceUrl": source_url,
                    "autoPublishEligible": False,
                }
            )
    return candidates


def bookee_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct review candidates from a public Bookee pricing iframe.

    Bookee renders plan cards as predictable title/price/credit/subscription
    text after JavaScript loads. The adapter records complete candidate
    metadata for review but deliberately never auto-publishes a price.
    """
    if platform_name(source_url) != "bookee":
        return []
    lines = [normalized_label(line) for line in visible_text.splitlines() if normalized_label(line)]
    candidates: list[dict[str, Any]] = []
    title_re = re.compile(r"^(?:unlimited )?monthly (?:training )?membership\b", re.IGNORECASE)
    drop_in_re = re.compile(r"^drop[ -]?in class x1$", re.IGNORECASE)
    for index, title in enumerate(lines):
        is_monthly = bool(title_re.search(title))
        is_drop_in = bool(drop_in_re.search(title))
        if not (is_monthly or is_drop_in):
            continue
        window = lines[index + 1 : index + 18]
        amount = None
        for line in window:
            match = MONEY_RE.search(line)
            if match:
                amount = float(match.group(1))
                break
        if amount is None or amount <= 0 or amount > 2000:
            continue
        allowance: dict[str, Any] | None = None
        allowance_match = re.search(r"\b(\d{1,3})x?\s*(?:classes?|credits?)\b", title, re.IGNORECASE)
        if not allowance_match:
            allowance_match = next((re.search(r"\b(\d{1,3})\s*credits?\b", line, re.IGNORECASE) for line in window if re.search(r"\b\d{1,3}\s*credits?\b", line, re.IGNORECASE)), None)
        if allowance_match:
            allowance = {"count": float(allowance_match.group(1)), "period": "month"}
        elif "unlimited" in title.casefold() or any(line.casefold() == "unlimited" for line in window):
            allowance = {"count": None, "period": "month", "unlimited": True}
        commitment_text = " ".join([title, *window])
        commitment_match = re.search(r"\b(3|6|12)[ -]?(?:month|months|m\b)", commitment_text, re.IGNORECASE)
        practice_only = "open studio" in title.casefold()
        source_product_id = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        candidates.append({
            "sourceProductId": source_product_id,
            "amount": amount,
            "currency": "USD",
            "rawLabel": title,
            "cadence": "visit" if is_drop_in else "month",
            "productType": "drop-in" if is_drop_in else "monthly",
            "classAllowance": allowance,
            "promotion": {"isPromotion": False, "label": ""},
            "eligibility": {
                "type": "practice-only" if practice_only else "standard-adult",
                "restrictions": ["Does not provide ordinary instructed-class access"] if practice_only else [],
            },
            "commitment": {
                "type": "fixed-term" if commitment_match else "unknown",
                "minimumMonths": int(commitment_match.group(1)) if commitment_match else None,
            },
            "method": "rendered-bookee-card",
            "adapter": "bookee",
            "evidenceTier": "official-public",
            "exactLocationMatch": "candidate",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates


def mariana_buy_page_candidates(payload: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
    """Reconstruct public products from Mariana Tek's read-only buy-page API."""

    if platform_name(source_url) != "mariana-tek":
        return []
    cadence_map = {"MO": "month", "WK": "week", "YR": "year", "DY": "day"}
    candidates: list[dict[str, Any]] = []
    for section in payload.get("buy_page_sections", []):
        for product in section.get("product_listings", []) if isinstance(section, dict) else []:
            if not isinstance(product, dict) or product.get("is_in_stock") is False:
                continue
            amount = numeric(product.get("price"))
            if amount is None or amount <= 0 or amount > 10_000:
                continue
            attributes = {
                text(item.get("name")): item.get("value")
                for item in product.get("product_display_attributes", [])
                if isinstance(item, dict) and text(item.get("name"))
            }
            product_kind = text(product.get("product_type")).casefold()
            interval_code = text(attributes.get("Payment Interval")).upper()
            interval_length = int(numeric(attributes.get("Payment Interval Length")) or 1)
            is_intro = text(attributes.get("Intro Offer")).casefold() == "true"
            user_segment = text(attributes.get("User Segment")).casefold()
            usage_limit = numeric(attributes.get("Usage Interval Limit"))
            credit_quantity = numeric(attributes.get("Credit Quantity"))
            if product_kind == "memberships":
                product_type = "monthly" if interval_code in {"MO", "WK"} else "offer"
                allowance = (
                    {"count": usage_limit, "period": cadence_map.get(interval_code, "month")}
                    if usage_limit is not None
                    else {"count": None, "period": "month", "unlimited": True}
                )
                cadence = cadence_map.get(interval_code, interval_code.casefold())
                commitment = {
                    "type": "month-to-month" if interval_code == "MO" and not attributes.get("Renewal Limit") else "fixed-term",
                    "minimumMonths": interval_length if interval_code == "MO" else (12 * interval_length if interval_code == "YR" else None),
                }
            else:
                product_type = "drop-in" if credit_quantity == 1 and not is_intro else "offer"
                allowance = {"count": credit_quantity, "period": "purchase"} if credit_quantity is not None else None
                cadence = "visit" if product_type == "drop-in" else "one-time"
                commitment = {"type": "none", "minimumMonths": None}
            name = normalized_label(text(product.get("name")))
            candidates.append({
                "sourceProductId": text(product.get("id")),
                "amount": amount,
                "currency": text(product.get("currency_code")) or "USD",
                "rawLabel": name,
                "cadence": cadence,
                "intervalCount": interval_length,
                "productType": product_type,
                "classAllowance": allowance,
                "promotion": {"isPromotion": is_intro, "label": name if is_intro else ""},
                "eligibility": {
                    "type": "standard-adult" if user_segment in {"", "everyone"} and not is_intro else "new-client",
                    "restrictions": [] if user_segment in {"", "everyone"} and not is_intro else [user_segment or "intro offer"],
                },
                "commitment": commitment,
                "locations": [text(value) for value in product.get("locations", [])],
                "method": "public-mariana-buy-page-api",
                "adapter": "mariana-tek",
                "evidenceTier": "official-public",
                "exactLocationMatch": "candidate",
                "sourceUrl": source_url,
                "autoPublishEligible": False,
            })
    return candidates


def mariana_storefronts(html: str) -> list[str]:
    tenant_match = re.search(r"TENANT_NAME\s*=\s*['\"]([a-z0-9-]+)['\"]", html, re.IGNORECASE)
    routes = re.findall(r"data-mariana-integrations\s*=\s*['\"]/buy/(\d+)", html, re.IGNORECASE)
    if not tenant_match:
        return []
    tenant = tenant_match.group(1)
    return [f"https://{tenant}.marianatek.com/api/customer/v1/locations/{location_id}/buy-page" for location_id in dict.fromkeys(routes)]


def xponential_package_candidates(payload: dict[str, Any], source_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Reconstruct Xponential packages and plan-linked fees from public APIs."""

    if platform_name(source_url) != "xponential-member-app":
        return [], []
    packages = payload.get("packages") if isinstance(payload.get("packages"), list) else []
    if isinstance(payload.get("package"), dict):
        packages = [payload["package"]]
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    candidates: list[dict[str, Any]] = []
    nested: list[str] = []
    for package in packages:
        if not isinstance(package, dict) or package.get("is_free") is True:
            continue
        amount = numeric((package.get("price") or {}).get("numeric") if isinstance(package.get("price"), dict) else package.get("price"))
        if amount is None or amount <= 0 or amount > 10_000:
            continue
        product_id = text(package.get("id"))
        recurring = package.get("is_recurring") is True
        membership = package.get("is_membership") is True
        credit_count = numeric(package.get("credit_count"))
        unlimited = package.get("is_unlimited") is True
        interval = text(package.get("interval")).casefold() or ("month" if recurring else "one-time")
        interval_count = int(numeric(package.get("interval_count")) or 1)
        payment_count = int(numeric(package.get("payment_count")) or 1)
        description = normalized_label(text(package.get("description")))
        minimum_match = re.search(r"\b(\d{1,2})[ -]month minimum\b", description, re.IGNORECASE)
        minimum_months = int(minimum_match.group(1)) if minimum_match else (payment_count * interval_count if membership and recurring and interval == "month" and payment_count > 1 else None)
        if membership and recurring:
            product_type = "monthly" if interval in {"month", "week"} else "offer"
            cadence = interval
            allowance = {"count": None, "period": interval, "unlimited": True} if unlimited else {"count": credit_count, "period": interval}
        elif credit_count == 1:
            product_type = "drop-in"
            cadence = "visit"
            allowance = {"count": 1.0, "period": "purchase"}
        else:
            product_type = "offer"
            cadence = "one-time"
            allowance = {"count": credit_count, "period": "purchase"} if credit_count is not None else None
        fees = []
        for fee in plan.get("fees", []) if isinstance(plan.get("fees"), list) else []:
            if not isinstance(fee, dict):
                continue
            subtotal = fee.get("subtotal") if isinstance(fee.get("subtotal"), dict) else {}
            fee_amount = numeric(subtotal.get("numeric"))
            if fee_amount is None or fee_amount < 0:
                continue
            fee_name = normalized_label(text(fee.get("name"))) or "Mandatory fee"
            fee_type = "enrollment" if "enroll" in fee_name.casefold() else "other"
            fees.append({
                "type": fee_type,
                "name": fee_name,
                "amount": fee_amount,
                "currency": text(subtotal.get("currency_code")) or "USD",
                "cadence": "one-time",
                "mandatory": True,
            })
        name = normalized_label(text(package.get("name")))
        promotion_text = f"{name} {description}"
        is_promotion = bool(PROMOTION_RE.search(promotion_text) or re.search(r"\bfirst[ -]?time clients?\b", promotion_text, re.IGNORECASE))
        detail_url = ""
        if "/packages" in source_url and "/package_details/" not in source_url and product_id:
            detail_url = source_url.split("/packages", 1)[0] + f"/package_details/{product_id}"
            nested.append(detail_url)
        candidates.append({
            "sourceProductId": product_id,
            "sourceSystemProductIds": {
                "clubReadyPackageId": text(package.get("clubready_id")),
                "clubReadyPlanId": text(package.get("clubready_plan_id")),
            },
            "amount": amount,
            "currency": text((package.get("price") or {}).get("currency_code")) or "USD",
            "rawLabel": name,
            "description": description,
            "cadence": cadence,
            "intervalCount": interval_count,
            "productType": product_type,
            "classAllowance": allowance,
            "promotion": {"isPromotion": is_promotion, "label": name if is_promotion else ""},
            "eligibility": {"type": "new-client" if is_promotion else "standard-adult", "restrictions": ["Introductory or first-time-client product"] if is_promotion else []},
            "commitment": {"type": "fixed-term" if minimum_months else ("month-to-month" if membership and recurring else "none"), "minimumMonths": minimum_months},
            "fees": fees,
            "detailUrl": detail_url,
            "method": "public-xponential-package-detail-api" if plan else "public-xponential-packages-api",
            "adapter": "xponential-member-app",
            "evidenceTier": "official-public",
            "exactLocationMatch": "candidate",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates, list(dict.fromkeys(nested))


def xponential_storefronts(html: str) -> list[str]:
    domain_match = re.search(r"data-endpoint-domain\s*=\s*['\"](https://members\.(?:clubpilates|stretchlab)\.com)['\"]", html, re.IGNORECASE)
    route_match = re.search(r"data-endpoint\s*=\s*['\"]/api/v2/locations/([^/'\"]+)/schedule_entries", html, re.IGNORECASE)
    if not domain_match:
        return []
    location_match = route_match or re.search(r"data-location\s*=\s*['\"]([^/'\"]+)['\"]", html, re.IGNORECASE)
    if not location_match:
        return []
    return [f"{domain_match.group(1)}/api/locations/{location_match.group(1)}/packages"]


def linked_storefronts(base_url: str, links: list[str]) -> list[str]:
    results: list[str] = []
    base_host = hostname(base_url)
    for value in links:
        candidate = urljoin(base_url, value)
        host = hostname(candidate)
        if not is_public_http_url(candidate):
            continue
        approved_booking = any(host == domain or host.endswith(f".{domain}") for domain in BOOKING_DOMAINS)
        approved_operator_page = (
            host == base_host
            and candidate != base_url
            and RESEARCH_PATH_RE.search(urlparse(candidate).path + ("?" + urlparse(candidate).query if urlparse(candidate).query else ""))
            and not RESEARCH_EXCLUDE_RE.search(urlparse(candidate).path)
        )
        if approved_booking or approved_operator_page:
            if "classpass.com" in host:
                continue  # Marketplace links may inform discovery but never exact evidence.
            if host in {"pushpress.com", "www.pushpress.com"}:
                continue  # Vendor marketing pages are not operator storefronts; operator checkout uses a dedicated subdomain.
            if candidate not in results:
                results.append(candidate)
    return results[:12]


def robots_allowed(url: str, timeout: float) -> tuple[bool, str]:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs originate in committed public listing data.
            body = response.read(500_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        parser.parse(body.splitlines())
        return parser.can_fetch(USER_AGENT, url), "checked"
    except HTTPError as error:
        if error.code in {401, 403}:
            return False, f"robots-http-{error.code}"
        return True, f"robots-http-{error.code}"
    except (URLError, TimeoutError, OSError):
        return True, "robots-unavailable"


def fetch_page(url: str, timeout: float, conditional: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed, robots_status = robots_allowed(url, timeout)
    if not allowed:
        return {"status": "robots-disallowed", "url": url, "robotsStatus": robots_status}
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5"}
    if conditional:
        if conditional.get("etag"):
            headers["If-None-Match"] = conditional["etag"]
        if conditional.get("lastModified"):
            headers["If-Modified-Since"] = conditional["lastModified"]
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs originate in committed public listing data.
            content_type = response.headers.get_content_type()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return {"status": "response-too-large", "url": response.geturl(), "robotsStatus": robots_status}
            charset = response.headers.get_content_charset() or "utf-8"
            html = body.decode(charset, errors="replace")
            return {
                "status": "fetched",
                "url": response.geturl(),
                "contentType": content_type,
                "html": html,
                "etag": response.headers.get("ETag", ""),
                "lastModified": response.headers.get("Last-Modified", ""),
                "robotsStatus": robots_status,
            }
    except HTTPError as error:
        if error.code == 304:
            return {"status": "not-modified", "url": url, "robotsStatus": robots_status}
        return {
            "status": f"http-{error.code}", "url": url, "robotsStatus": robots_status,
            "retryAfter": text(error.headers.get("Retry-After")) if error.headers else "",
        }
    except (URLError, TimeoutError, OSError) as error:
        return {"status": "network-error", "url": url, "robotsStatus": robots_status, "error": text(error)[:200]}


def parse_page(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str]:
    html = text(result.get("html"))
    if not html:
        return [], [], ""
    source_url = text(result.get("url"))
    is_json = "json" in text(result.get("contentType")).casefold()
    if is_json and platform_name(source_url) == "mariana-tek":
        try:
            payload = json.loads(html)
        except json.JSONDecodeError:
            payload = {}
        return mariana_buy_page_candidates(payload, source_url), [], hashlib.sha256(html.encode("utf-8")).hexdigest()
    if is_json and platform_name(source_url) == "xponential-member-app":
        try:
            payload = json.loads(html)
        except json.JSONDecodeError:
            payload = {}
        candidates, nested = xponential_package_candidates(payload, source_url)
        return candidates, nested, hashlib.sha256(html.encode("utf-8")).hexdigest()
    if is_json and platform_adapters.platform_for_url(source_url):
        try:
            payload = json.loads(html)
        except json.JSONDecodeError:
            payload = {}
        candidates = platform_adapters.extract_candidates(payload, source_url)
        candidates.extend(structured_candidates([html], source_url, "public-platform-json"))
        return deduplicate_candidates(candidates), [], hashlib.sha256(html.encode("utf-8")).hexdigest()
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:  # HTMLParser is tolerant, but malformed pages should not abort the crawl.
        pass
    visible = normalized_label(" ".join(parser.visible)) if len(" ".join(parser.visible)) <= 220 else " ".join(parser.visible)
    candidates = structured_candidates(parser.json_ld, text(result.get("url")))
    candidates.extend(structured_candidates(parser.hydration_json, text(result.get("url")), "embedded-hydration-json"))
    candidates.extend(visible_candidates(visible, text(result.get("url"))))
    deduplicated = deduplicate_candidates(candidates)
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    stores = linked_storefronts(source_url, parser.links)
    for candidate in mariana_storefronts(html):
        if candidate not in stores:
            stores.append(candidate)
    for candidate in xponential_storefronts(html):
        if candidate not in stores:
            stores.append(candidate)
    return deduplicated, stores[:12], digest


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in candidates:
        key = (candidate.get("amount"), candidate.get("productType"), candidate.get("rawLabel"), candidate.get("sourceProductId"))
        if key not in seen:
            seen.add(key)
            deduplicated.append(candidate)
    return deduplicated


def structured_location_candidates(json_ld_blocks: list[str], source_url: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in json_ld_blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in walk_json(parsed):
            node_type = node.get("@type")
            types = {text(item).casefold() for item in node_type} if isinstance(node_type, list) else {text(node_type).casefold()}
            if not types.intersection({"exercisegym", "healthclub", "localbusiness", "sportsactivitylocation"}):
                continue
            address_value = node.get("address")
            if isinstance(address_value, dict):
                address = ", ".join(
                    text(address_value.get(key))
                    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                    if text(address_value.get(key))
                )
            else:
                address = text(address_value)
            geo = node.get("geo") if isinstance(node.get("geo"), dict) else {}
            hours = node.get("openingHours") or node.get("openingHoursSpecification") or []
            amenities = []
            for feature in node.get("amenityFeature", []) if isinstance(node.get("amenityFeature"), list) else []:
                if isinstance(feature, dict) and feature.get("value") not in {False, "false", 0} and text(feature.get("name")):
                    amenities.append(text(feature.get("name")))
            raw_label = normalized_label(f"{text(node.get('name'))} | {address} | {text(hours)}")
            candidates.append(
                {
                    "name": text(node.get("name")),
                    "address": address,
                    "latitude": numeric(geo.get("latitude")),
                    "longitude": numeric(geo.get("longitude")),
                    "hours": hours,
                    "amenities": amenities,
                    "sourceUrl": source_url,
                    "method": "json-ld-location",
                    "rawLabel": raw_label,
                    "contentHash": hashlib.sha256(raw_label.encode("utf-8")).hexdigest(),
                    "autoPublishEligible": False,
                }
            )
    return candidates


def visible_location_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    candidates = []
    for match in VISIBLE_ADDRESS_RE.finditer(visible_text):
        address = normalized_label(match.group(0))
        if not address or not re.search(r"\b\d{1,6}\b", address):
            continue
        raw_label = f"Visible address: {address}"
        candidates.append(
            {
                "name": "",
                "address": address,
                "latitude": None,
                "longitude": None,
                "hours": [],
                "amenities": [],
                "sourceUrl": source_url,
                "method": "visible-address-candidate",
                "rawLabel": raw_label,
                "contentHash": hashlib.sha256(raw_label.encode("utf-8")).hexdigest(),
                "autoPublishEligible": False,
            }
        )
    return candidates[:10]


def parse_location_page(result: dict[str, Any]) -> list[dict[str, Any]]:
    html = text(result.get("html"))
    if not html:
        return []
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    candidates = structured_location_candidates(parser.json_ld, text(result.get("url")))
    candidates.extend(visible_location_candidates(" ".join(parser.visible), text(result.get("url"))))
    deduplicated = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = (text(candidate.get("name")), text(candidate.get("address")), text(candidate.get("method")))
        if key not in seen:
            seen.add(key)
            deduplicated.append(candidate)
    return deduplicated


def deal_eligible_gym(gym: dict[str, Any]) -> bool:
    return (
        gym.get("publicationStatus") == "publish"
        and gym.get("recordStatus") != "coming_soon"
        and gym.get("entityKind") in {"gym", "studio", "martial-arts"}
        and gym.get("accessModel") not in {"restricted", "not-applicable"}
        and gym.get("accessAvailability") not in {"waitlist", "enrollment-paused", "members-only", "presale"}
    )


def should_crawl(gym: dict[str, Any], cache: dict[str, Any], mode: str, today: datetime) -> bool:
    url = text(gym.get("websiteUrl"))
    if not is_public_http_url(url) or coverage.is_osm_url(url):
        return False
    if mode == "full":
        return True
    if deal_eligible_gym(gym):
        cached = cache.get(url, {})
        last_attempt = text(cached.get("lastAttemptAt"))
        try:
            attempted = datetime.fromisoformat(last_attempt)
        except ValueError:
            return True
        if today - attempted >= timedelta(hours=20):
            return True
    if mode == "deals":
        return False
    cached = cache.get(url, {})
    if cached.get("status") not in {"fetched", "not-modified"}:
        return True
    last_attempt = text(cached.get("lastAttemptAt"))
    try:
        attempted = datetime.fromisoformat(last_attempt)
    except ValueError:
        return True
    observed = text(gym.get("priceObservedAt"))
    try:
        verified = datetime.fromisoformat(observed)
    except ValueError:
        verified = datetime.min
    return today - attempted >= timedelta(days=7) and (gym.get("monthlyPrice") is None or today - verified >= timedelta(days=STALE_AFTER_DAYS))


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def explicit_visible_promotion_candidates(label: str) -> list[dict[str, Any]]:
    """Associate a visible promotion phrase with its nearest dollar amount.

    Generic price regexes intentionally retain broad context. This second pass
    prevents a nearby ordinary price or annual fee from becoming the deal amount.
    """

    money = list(MONEY_RE.finditer(label))
    promotions = list(PROMOTION_RE.finditer(label))
    if not money or not promotions:
        return []
    scored: list[tuple[int, re.Match[str]]] = []
    for amount_match in money:
        start, end = amount_match.span()
        local = label[max(0, start - 45) : min(len(label), end + 55)]
        amount_text = re.escape(amount_match.group(0))
        if re.search(rf"(?:annual|maintenance)\s+fee[^$]{{0,25}}{amount_text}|{amount_text}[^.]{{0,25}}(?:annual|maintenance)\s+fee", local, re.I):
            continue
        distance = min(abs(start - promo.start()) for promo in promotions)
        if distance <= 110:
            scored.append((distance, amount_match))
    if not scored:
        return []
    best_distance = min(item[0] for item in scored)
    candidates: list[dict[str, Any]] = []
    for distance, amount_match in scored:
        if distance > best_distance + 12:
            continue
        amount = float(amount_match.group(1))
        if amount <= 0 or amount > 2000:
            continue
        start, end = amount_match.span()
        snippet = normalized_label(label[max(0, start - 90) : min(len(label), end + 90)])
        lowered = snippet.casefold()
        if re.search(r"\b(?:first|intro(?:ductory)?)\s+month\b", lowered):
            product_type, cadence = "monthly", "first-month"
        elif re.search(r"\b(?:class|classes|pack|credits?)\b", lowered):
            product_type, cadence = "class-pack", "one-time"
        elif re.search(r"\b(?:initiation|join|enrollment|setup)\s+fee\b", lowered):
            product_type, cadence = "promotional-fee", "one-time"
        elif re.search(r"\b(?:drop[ -]?in|single visit|day pass)\b", lowered):
            product_type, cadence = "drop-in", "visit"
        else:
            product_type, cadence = "promotion", "one-time"
        candidates.append({"amount": amount, "productType": product_type, "cadence": cadence, "label": snippet})
    return candidates


def deal_candidates(
    observations: list[dict[str, Any]], eligible_gym_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Return sanitized review-only promotions without replacing ordinary prices."""

    deals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, float]] = set()
    for observation in observations:
        if eligible_gym_ids is not None and text(observation.get("gymId")) not in eligible_gym_ids:
            continue
        promotion = observation.get("promotion") or {}
        if not promotion.get("isPromotion"):
            continue
        raw_label = text(observation.get("rawLabel"))
        if text(observation.get("method")) == "visible-text-candidate":
            derived = explicit_visible_promotion_candidates(raw_label)
        else:
            derived = [{
                "amount": float(observation.get("amount") or 0),
                "productType": text(observation.get("productType")),
                "cadence": text(observation.get("cadence")),
                "label": text(promotion.get("label")) or raw_label,
            }]
        for candidate in derived:
            amount = float(candidate.get("amount") or 0)
            product_type = text(candidate.get("productType"))
            source_product = text(observation.get("sourceProductId")) or f"{product_type}:{amount:g}"
            key = (text(observation.get("gymId")), text(observation.get("sourceUrl")), source_product, amount)
            if amount <= 0 or amount > 2000 or key in seen:
                continue
            seen.add(key)
            evidence_label = normalized_label(candidate.get("label"))
            deals.append({
                "id": hashlib.sha256("|".join(map(str, key)).encode("utf-8")).hexdigest()[:20],
                "gymId": text(observation.get("gymId")),
                "gymName": text(observation.get("gymName")),
                "amount": amount,
                "currency": text(observation.get("currency")) or "USD",
                "productType": product_type,
                "cadence": text(candidate.get("cadence")),
                "label": evidence_label,
                "expiresAt": text(promotion.get("expiresAt")) or None,
                "sourceUrl": text(observation.get("sourceUrl")),
                "capturedAt": text(observation.get("capturedAt")),
                "contentHash": hashlib.sha256(evidence_label.encode("utf-8")).hexdigest(),
                "reviewStatus": "pending",
                "replacesOrdinaryPrice": False,
            })
    return sorted(deals, key=lambda item: (item["gymId"], item["sourceUrl"], item["amount"], item["label"]))


def crawl_gym(
    gym: dict[str, Any],
    cache: dict[str, Any],
    today: datetime,
    timeout: float,
    domain_locks: dict[str, threading.Lock],
    last_domain_request: dict[str, float],
    domain_429_counts: dict[str, int],
    domain_next_request: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    def rate_limited_fetch(url: str) -> dict[str, Any]:
        host = hostname(url)
        with domain_locks[host]:
            if domain_429_counts.get(host, 0) >= MAX_DOMAIN_429S:
                return {"status": "host-backoff-after-429", "url": url, "robotsStatus": "not-requested"}
            now = time.monotonic()
            wait_for = max(
                DOMAIN_DELAY_SECONDS - (now - last_domain_request.get(host, 0)),
                domain_next_request.get(host, 0) - now,
            )
            if wait_for > 0:
                time.sleep(wait_for)
            result = fetch_page(url, timeout, cache.get(url))
            last_domain_request[host] = time.monotonic()
            if result.get("status") == "http-429":
                domain_429_counts[host] = domain_429_counts.get(host, 0) + 1
                retry_after = text(result.get("retryAfter"))
                try:
                    delay = min(max(float(retry_after), 0), 300)
                except ValueError:
                    delay = 15.0 * (2 ** (domain_429_counts[host] - 1))
                domain_next_request[host] = time.monotonic() + delay
            return result

    url = text(gym.get("websiteUrl"))
    result = rate_limited_fetch(url)
    offers, storefronts, digest = parse_page(result)
    location_candidates = parse_location_page(result)
    if result.get("status") == "not-modified":
        offers = list(cache.get(url, {}).get("candidates", []))
        storefronts = linked_storefronts(url, list(cache.get(url, {}).get("linkedStorefronts", [])))
        location_candidates = list(cache.get(url, {}).get("locationCandidates", []))
        digest = text(cache.get(url, {}).get("contentHash"))
    attempted_at = today.date().isoformat()
    previous_hash = text(cache.get(url, {}).get("contentHash"))
    published = gym.get("monthlyPrice")
    candidate_monthly = [offer["amount"] for offer in offers if offer.get("productType") == "monthly" or offer.get("cadence") == "month"]
    price_change = bool(
        published is not None
        and any(abs(float(candidate) - float(published)) / float(published) > 0.2 for candidate in candidate_monthly)
    )
    attempts = [
        {
            "gymId": gym["id"],
            "name": gym["name"],
            "url": url,
            "attemptedAt": attempted_at,
            "status": result["status"],
            "robotsStatus": result.get("robotsStatus", ""),
            "contentHash": digest,
            "contentChanged": bool(previous_hash and digest and previous_hash != digest),
            "candidateCount": len(offers),
            "linkedStorefronts": storefronts,
            "requiresReview": bool(offers),
            "priceChangeOver20Percent": price_change,
        }
    ]
    observations = [{"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **offer} for offer in offers]
    location_observations = [{"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **candidate} for candidate in location_candidates]
    updates = {
        url: {
            "status": result["status"],
            "lastAttemptAt": attempted_at,
            "etag": result.get("etag", ""),
            "lastModified": result.get("lastModified", ""),
            "contentHash": digest,
            "candidates": offers,
            "linkedStorefronts": storefronts,
            "locationCandidates": location_candidates,
        }
    }
    for storefront in storefronts:
        store_result = rate_limited_fetch(storefront)
        store_offers, nested, store_digest = parse_page(store_result)
        if store_result.get("status") == "not-modified":
            store_offers = list(cache.get(storefront, {}).get("candidates", []))
            store_digest = text(cache.get(storefront, {}).get("contentHash"))
        attempts.append(
            {
                "gymId": gym["id"],
                "name": gym["name"],
                "url": storefront,
                "attemptedAt": attempted_at,
                "status": store_result["status"],
                "robotsStatus": store_result.get("robotsStatus", ""),
                "contentHash": store_digest,
                "contentChanged": bool(text(cache.get(storefront, {}).get("contentHash")) and store_digest and text(cache.get(storefront, {}).get("contentHash")) != store_digest),
                "candidateCount": len(store_offers),
                "linkedFrom": url,
                "requiresReview": bool(store_offers),
                "priceChangeOver20Percent": False,
            }
        )
        observations.extend({"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **offer} for offer in store_offers)
        updates[storefront] = {
            "status": store_result["status"],
            "lastAttemptAt": attempted_at,
            "etag": store_result.get("etag", ""),
            "lastModified": store_result.get("lastModified", ""),
            "contentHash": store_digest,
            "candidates": store_offers,
            "linkedStorefronts": nested,
            "locationCandidates": [],
        }
        for detail_url in nested[:12]:
            detail_result = rate_limited_fetch(detail_url)
            detail_offers, _deeper, detail_digest = parse_page(detail_result)
            if detail_result.get("status") == "not-modified":
                detail_offers = list(cache.get(detail_url, {}).get("candidates", []))
                detail_digest = text(cache.get(detail_url, {}).get("contentHash"))
            attempts.append({
                "gymId": gym["id"],
                "name": gym["name"],
                "url": detail_url,
                "attemptedAt": attempted_at,
                "status": detail_result["status"],
                "robotsStatus": detail_result.get("robotsStatus", ""),
                "contentHash": detail_digest,
                "contentChanged": bool(text(cache.get(detail_url, {}).get("contentHash")) and detail_digest and text(cache.get(detail_url, {}).get("contentHash")) != detail_digest),
                "candidateCount": len(detail_offers),
                "linkedFrom": storefront,
                "requiresReview": bool(detail_offers),
                "priceChangeOver20Percent": False,
            })
            observations.extend({"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **offer} for offer in detail_offers)
            updates[detail_url] = {
                "status": detail_result["status"],
                "lastAttemptAt": attempted_at,
                "etag": detail_result.get("etag", ""),
                "lastModified": detail_result.get("lastModified", ""),
                "contentHash": detail_digest,
                "candidates": detail_offers,
                "linkedStorefronts": [],
                "locationCandidates": [],
            }
    return attempts, observations, location_observations, updates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("deals", "weekly", "full"), default="weekly")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gym-id", action="append", default=[], help="Crawl only the specified stable gym ID; may be repeated")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--date", help="Override attempt date")
    args = parser.parse_args()
    today = datetime.fromisoformat(args.date) if args.date else datetime.now(UTC).replace(tzinfo=None)
    document = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    candidates = [gym for gym in document.get("gyms", []) if should_crawl(gym, cache, args.mode, today)]
    if args.gym_id:
        requested_ids = set(args.gym_id)
        candidates = [gym for gym in candidates if text(gym.get("id")) in requested_ids]
    if args.limit:
        candidates = candidates[: args.limit]
    existing_attempts_document = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8")) if ATTEMPTS_PATH.exists() else {"attempts": []}
    existing_observations_document = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8")) if OBSERVATIONS_PATH.exists() else {"observations": []}
    existing_locations_document = json.loads(LOCATION_OBSERVATIONS_PATH.read_text(encoding="utf-8")) if LOCATION_OBSERVATIONS_PATH.exists() else {"observations": []}
    attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    location_observations: list[dict[str, Any]] = []
    domain_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
    last_domain_request: dict[str, float] = {}
    domain_429_counts: dict[str, int] = {}
    domain_next_request: dict[str, float] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(
            lambda gym: crawl_gym(
                gym, cache, today, args.timeout, domain_locks, last_domain_request,
                domain_429_counts, domain_next_request,
            ),
            candidates,
        )
        for gym_attempts, gym_observations, gym_locations, cache_updates in results:
            attempts.extend(gym_attempts)
            observations.extend(gym_observations)
            location_observations.extend(gym_locations)
            cache.update(cache_updates)
    crawled_gym_ids = {text(gym.get("id")) for gym in candidates}
    attempts_by_key = {
        (text(item.get("gymId")), text(item.get("url"))): item
        for item in existing_attempts_document.get("attempts", [])
        if text(item.get("gymId")) not in crawled_gym_ids
    }
    attempts_by_key.update({(text(item.get("gymId")), text(item.get("url"))): item for item in attempts})
    attempts = sorted(attempts_by_key.values(), key=lambda item: (text(item.get("gymId")), text(item.get("url"))))
    observations = [item for item in existing_observations_document.get("observations", []) if text(item.get("gymId")) not in crawled_gym_ids] + observations
    observations.sort(key=lambda item: (text(item.get("gymId")), text(item.get("sourceUrl")), float(item.get("amount", 0)), text(item.get("rawLabel"))))
    location_observations = [item for item in existing_locations_document.get("observations", []) if text(item.get("gymId")) not in crawled_gym_ids] + location_observations
    location_observations.sort(key=lambda item: (text(item.get("gymId")), text(item.get("sourceUrl")), text(item.get("rawLabel"))))
    save_json(CACHE_PATH, cache)
    save_json(ATTEMPTS_PATH, {"generatedAt": today.date().isoformat(), "mode": args.mode, "attempts": attempts})
    save_json(OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "observations": observations})
    save_json(LOCATION_OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "observations": location_observations})
    eligible_deal_ids = {
        text(gym.get("id")) for gym in document.get("gyms", []) if deal_eligible_gym(gym)
    }
    deals = deal_candidates(observations, eligible_deal_ids)
    save_json(DEAL_OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "mode": args.mode, "deals": deals})
    save_json(DEAL_REPORT_PATH, {
        "generatedAt": today.date().isoformat(),
        "mode": args.mode,
        "dealCandidateCount": len(deals),
        "locationCount": len({item["gymId"] for item in deals}),
        "reviewRequiredCount": sum(item["reviewStatus"] == "pending" for item in deals),
        "ordinaryPricesRemainAuthoritative": True,
    })
    print(json.dumps({"candidateGyms": len(candidates), "requests": len(attempts), "observations": len(observations), "dealCandidates": len(deals), "reviewRequired": sum(item["requiresReview"] for item in attempts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
