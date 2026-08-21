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
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, urldefrag, urlencode, urljoin, urlparse
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
RENDERED_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "rendered-crawl-observations.json"
OPERATOR_DOCUMENT_CANDIDATES_PATH = ROOT / "data" / "imports" / "operator-document-candidates.json"
USER_AGENT = "sf-gyms-public-research/1.0 (+https://github.com/jkorrr/sf-gyms)"
MAX_RESPONSE_BYTES = 4_000_000
DOMAIN_DELAY_SECONDS = 1.5
MAX_DOMAIN_429S = 2
STALE_AFTER_DAYS = 35
MAX_LINKED_REQUESTS_PER_GYM = 36
MAX_LINK_DEPTH = 3
MAX_OPERATOR_LINK_DEPTH = 2
MAX_OPERATOR_REQUESTS_PER_GYM = 12
MAX_REVIEWED_SEED_URLS = 8

BOOKING_DOMAINS = {
    "clients.mindbodyonline.com",
    "cart.mindbodyonline.com",
    "janeapp.com",
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
    "onlinejoin.abcfitness.com",
    "portal.movementgyms.com",
    "portal.approach.app",
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
COST_CONTEXT_SEMANTIC_RE = re.compile(
    r"\b(?:memberships?|training|tuition|programs?|packages?|rates?|lessons?|sessions?|classes?|visits?|drop[ -]?ins?|day passes?|hours?)\b",
    re.IGNORECASE,
)
COST_RANGE_VISIBLE_RE = re.compile(
    r"(?P<label>[^.;\n$]{0,120}?\b(?:memberships?|training|tuition|programs?|packages?|rates?|lessons?|sessions?|classes?|visits?|drop[ -]?ins?|day passes?|hours?)\b[^.;\n$]{0,80}?)"
    r"\$\s*(?P<low>\d{1,5}(?:\.\d{1,2})?)\s*(?:[–—-]|\bto\b)\s*\$?\s*(?P<high>\d{1,5}(?:\.\d{1,2})?)"
    r"\s*(?:(?:/|per\s+)(?P<cadence>session|class|visit|hour|month|week|4\s+weeks?|28\s+days?|one[ -]?time))?",
    re.IGNORECASE,
)
COST_START_VISIBLE_RE = re.compile(
    r"(?P<label>[^.;\n$]{0,120}?\b(?:memberships?|training|tuition|programs?|packages?|rates?|lessons?|sessions?|classes?|visits?|drop[ -]?ins?|day passes?|hours?)\b[^.;\n$]{0,80}?"
    r"\b(?:starts?|starting)\s+(?:from|at)|[^.;\n$]{0,120}?\b(?:memberships?|training|tuition|programs?|packages?|rates?|lessons?|sessions?|classes?|visits?|drop[ -]?ins?|day passes?|hours?)\b[^.;\n$]{0,80}?\bfrom)"
    r"\s*\$\s*(?P<amount>\d{1,5}(?:\.\d{1,2})?)"
    r"\s*(?:(?:/|per\s+)(?P<cadence>session|class|visit|hour|month|week|4\s+weeks?|28\s+days?|one[ -]?time))?",
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
    r"/(?:pricing|prices|pricespolicies|rates?|memberships?|plans?|packages?|passes|drop-?in|buy|join|locations?|faqs?|how-it-works)(?:/|$|[?#])",
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
        ("janeapp.com", "jane"),
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


NON_SEMANTIC_QUERY_KEYS = {"_gl", "fbclid", "gclid", "lang", "language", "locale"}
LOCATION_PATH_MARKERS = {"club", "clubs", "location", "locations", "studio", "studios", "yoga-studios"}
LOCATION_ROUTE_TAILS = {
    "book", "booking", "buy", "classes", "drop-in", "dropins", "join", "membership", "memberships",
    "packages", "passes", "plans", "prices", "pricing", "rates", "schedule", "shop",
}


def request_identity(url: str) -> str:
    """Return a stable request identity without presentation/tracking variants."""

    normalized = urldefrag(text(url))[0]
    parsed = urlparse(normalized)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in NON_SEMANTIC_QUERY_KEYS and not key.casefold().startswith("utm_")
    ]
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(
        scheme=parsed.scheme.casefold(),
        netloc=parsed.netloc.casefold(),
        path=path,
        query=urlencode(sorted(query, key=lambda item: (item[0].casefold(), item[1]))),
        fragment="",
    ).geturl()


def location_route_slug(url: str) -> str | None:
    """Extract the branch slug from an operator location-directory URL."""

    segments = [segment.casefold() for segment in urlparse(url).path.split("/") if segment]
    marker_index = next((index for index, segment in enumerate(segments) if segment in LOCATION_PATH_MARKERS), None)
    if marker_index is None:
        return None
    tail = segments[marker_index + 1:]
    while tail and tail[-1] in LOCATION_ROUTE_TAILS:
        tail.pop()
    return tail[-1] if tail else ""


def operator_location_slugs(gym: dict[str, Any]) -> set[str]:
    """Collect reviewed location slugs that may identify this exact listing."""

    slugs: set[str] = set()
    operator_location_id = re.sub(r"[^a-z0-9-]+", "-", text(gym.get("operatorLocationId")).casefold()).strip("-")
    if operator_location_id:
        slugs.add(operator_location_id)
    for key in ("websiteUrl", "officialUrl"):
        slug = location_route_slug(text(gym.get(key)))
        if slug:
            slugs.add(slug)
    return slugs


def operator_page_matches_gym(url: str, gym: dict[str, Any] | None) -> bool:
    """Reject unrelated chain branches while retaining global pricing pages."""

    if gym is None:
        return True
    slug = location_route_slug(url)
    if slug is None:
        return True
    if not slug:
        return False
    reviewed_slugs = operator_location_slugs(gym)
    if slug in reviewed_slugs:
        return True
    return not reviewed_slugs and slug in {"san-francisco", "sf"}


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
            label = text(node.get("name") or node.get("title") or node.get("label") or node.get("productName")) or text(node.get("description")) or "/".join(sorted(types))
            cadence = text(node.get("unitCode") or node.get("billingDuration") or node.get("billingIncrement") or node.get("billingPeriod") or node.get("interval") or node.get("frequency"))
            low_price = numeric(node.get("lowPrice"))
            high_price = numeric(node.get("highPrice"))
            if "aggregateoffer" in types and low_price is not None and 0 < low_price <= 10_000:
                high_price = high_price if high_price is not None else low_price
                if low_price <= high_price <= 10_000:
                    raw_label = normalized_label(
                        f"{label} ${low_price:g}"
                        + (f"–${high_price:g}" if high_price != low_price else " and up")
                        + (f" per {cadence}" if cadence else "")
                    )
                    candidates.append(
                        {
                            "kind": "range" if high_price != low_price else "starting-price",
                            "low": low_price,
                            "high": high_price,
                            "currency": text(node.get("priceCurrency")) or "USD",
                            "rawLabel": raw_label,
                            "cadence": normalized_label(cadence) or "unknown",
                            "productType": "cost-context",
                            "promotion": {"isPromotion": bool(PROMOTION_RE.search(raw_label)), "label": raw_label if PROMOTION_RE.search(raw_label) else ""},
                            "method": method,
                            "adapter": platform_name(source_url),
                            "evidenceTier": "official-public",
                            "exactLocationMatch": "candidate",
                            "sourceUrl": source_url,
                            "autoPublishEligible": False,
                            "selectable": False,
                        }
                    )
                continue
            amount = numeric(next((node.get(key) for key in price_keys if node.get(key) is not None), None))
            if amount is None or amount <= 0 or amount > 2000:
                continue
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

        def fees(variant: int, plan_key: str = key) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            if plan_key in enrollment:
                result.append({
                    "type": "enrollment", "amount": enrollment[plan_key][variant], "currency": "USD",
                    "cadence": "one-time", "mandatory": True,
                })
            if plan_key in processing:
                result.append({
                    "type": "processing", "amount": processing[plan_key][variant], "currency": "USD",
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


def operator_visible_candidate(
    source_url: str,
    adapter: str,
    product_id: str,
    name: str,
    amount: float,
    *,
    product_type: str = "offer",
    cadence: str = "one-time",
    access_scope: str = "Named location",
    scope_type: str = "single-location",
    allowance: dict[str, Any] | None = None,
    commitment_type: str = "none",
    minimum_months: int | None = None,
    eligibility_type: str = "standard-adult",
    restrictions: list[str] | None = None,
    promotion: bool = False,
    promotion_label: str = "",
    fees: list[dict[str, Any]] | None = None,
    best_value: bool = False,
    raw_label: str = "",
) -> dict[str, Any]:
    return {
        "sourceProductId": product_id,
        "name": name,
        "amount": amount,
        "currency": "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "productType": product_type,
        "accessScope": access_scope,
        "scopeType": scope_type,
        "classAllowance": allowance,
        "eligibility": {"type": eligibility_type, "restrictions": restrictions or []},
        "commitment": {"type": commitment_type, "minimumMonths": minimum_months},
        "promotion": {"isPromotion": promotion, "label": promotion_label},
        "fees": fees or [],
        "bestValueLabel": best_value,
        "rawLabel": raw_label or f"{name} ${amount:g}/{cadence}",
        "method": f"visible-{adapter}-catalog",
        "adapter": adapter,
        "evidenceTier": "official-public",
        "exactLocationMatch": "exact-location",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }


def orangetheory_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct exact-location Orangetheory membership cards.

    Studio pages also render a generic footer amount for a casual visit and
    may retain hidden promotion templates.  This adapter requires the three
    named recurring tiers and associates only an amount carrying a monthly
    cadence inside its visible tier segment.
    """

    host = hostname(source_url)
    if not (host == "orangetheory.com" or host.endswith(".orangetheory.com")):
        return []
    value = re.sub(r"\s+", " ", visible_text).strip()
    if not all(re.search(rf"\b{name}\b", value, re.IGNORECASE) for name in ("Premier", "Elite", "Basic")):
        return []
    commitment = "month-to-month" if re.search(r"month[ -]to[ -]month|30[ -]day cancellation", value, re.IGNORECASE) else "unknown"
    allowances = {
        "premier": {"count": None, "period": "month", "unlimited": True},
        "elite": {"count": 8, "period": "month", "unlimited": False},
        "basic": {"count": 4, "period": "month", "unlimited": False},
    }
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    card_re = re.compile(
        r"\b(?P<name>Premier|Elite|Basic)\b(?P<body>.{0,700}?)(?=\b(?:Premier|Elite|Basic)\b|$)",
        re.IGNORECASE,
    )
    for card in card_re.finditer(value):
        key = card.group("name").casefold()
        if key in seen:
            continue
        body = card.group("body")
        amounts = list(re.finditer(r"\$\s*(\d{1,4}(?:\.\d{1,2})?)\s*(?:/\s*mo(?:nth)?|per\s+month|monthly)", body, re.IGNORECASE))
        ordinary = []
        for amount_match in amounts:
            context = body[max(0, amount_match.start() - 100):amount_match.end()]
            if not re.search(r"first month|new member|intro|limited time|promo", context, re.IGNORECASE):
                ordinary.append(amount_match)
        selected = (ordinary or amounts)[0] if (ordinary or amounts) else None
        if selected is None:
            continue
        amount = float(selected.group(1))
        if not 0 < amount <= 2_000:
            continue
        seen.add(key)
        display = card.group("name").capitalize()
        candidates.append(operator_visible_candidate(
            source_url,
            "orangetheory-tier-cards",
            key,
            display,
            amount,
            product_type="monthly",
            cadence="month",
            access_scope="Recurring coached Orangetheory classes with multi-studio access",
            scope_type="multi-location",
            allowance=allowances[key],
            commitment_type=commitment,
            raw_label=f"{display} ${amount:g}/month",
        ))
    return candidates if {item["sourceProductId"] for item in candidates} == {"premier", "elite", "basic"} else []


def approach_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Extract public products rendered by an Approach location storefront."""

    if platform_name(source_url) != "approach":
        return []
    value = re.sub(r"\s+", " ", visible_text).strip()
    membership = re.search(
        r"\bUnlimited Membership\b.{0,260}?\$\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?).{0,70}?\b(?:month|monthly|recurring)\b",
        value,
        re.IGNORECASE,
    )
    if not membership:
        return []
    amount = float(membership.group("amount"))
    return [operator_visible_candidate(
        source_url,
        "approach",
        "unlimited-membership",
        "Unlimited Membership",
        amount,
        product_type="monthly",
        cadence="month",
        access_scope="Unlimited access to all operator locations",
        scope_type="multi-location",
        allowance={"count": None, "period": "month", "unlimited": True},
        commitment_type="unknown",
    )]


def independent_operator_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct complete catalogs from stable, operator-labeled rate cards.

    These adapters activate only on the named official host and only while the
    expected labels and amounts remain present.  A changed or incomplete card
    therefore fails closed into review instead of silently reusing old values.
    """

    host = hostname(source_url)
    value = re.sub(r"\s+", " ", visible_text).strip()
    lowered = value.casefold()

    def has(*labels: str) -> bool:
        return all(label.casefold() in lowered for label in labels)

    def recurring(
        adapter: str, product_id: str, name: str, amount: float, **kwargs: Any,
    ) -> dict[str, Any]:
        commitment_type = text(kwargs.pop("commitment_type", "month-to-month")) or "month-to-month"
        return operator_visible_candidate(
            source_url, adapter, product_id, name, amount,
            product_type="monthly", cadence="month", commitment_type=commitment_type, **kwargs,
        )

    if host.endswith("benchmarkclimbing.com") and has("Day Pass", "$30", "5 Day Passes", "$140", "10 Day Passes", "$270"):
        adapter = "benchmark-climbing"
        return [
            operator_visible_candidate(source_url, adapter, "adult-day-pass", "Adult Day Pass", 30, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "five-day-passes", "5 Day Passes", 140, allowance={"count": 5, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-day-passes", "10 Day Passes", 270, allowance={"count": 10, "period": "purchase", "unlimited": False}),
        ]

    if host.endswith("federalfitnesscenters.com") and has("General Public", "$47", "$40", "Initiation"):
        adapter = "federal-fitness-center"
        initiation = [{"type": "initiation", "amount": 40, "currency": "USD", "cadence": "one-time", "mandatory": True}]
        offers = [
            recurring(adapter, "general-public-all-clubs", "General Public / Federal Contractor / State Government", 47, access_scope="All Federal Fitness Center clubs where general-public access is permitted", scope_type="multi-location", commitment_type="unknown", fees=initiation),
            recurring(adapter, "federal-employee-local", "Federal Employee — 450 Golden Gate", 40, access_scope="450 Golden Gate Avenue only", eligibility_type="federal-employee", restrictions=["Federal employee eligibility required"], commitment_type="unknown", fees=initiation),
            recurring(adapter, "federal-employee-all-clubs", "Federal Employee — All FFC Clubs", 43, access_scope="All Federal Fitness Center clubs", scope_type="multi-location", eligibility_type="federal-employee", restrictions=["Federal employee eligibility required"], commitment_type="unknown", fees=initiation),
        ]
        if has("Day Pass", "$20"):
            offers.append(operator_visible_candidate(source_url, adapter, "day-pass", "Day Pass", 20, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}))
        return offers

    if host.endswith("clubs.healthclubsystems.com") and has("Annual Membership", "Monthly Dues", "$77", "12"):
        adapter = "fit-bernal-fit"
        return [operator_visible_candidate(
            source_url,
            adapter,
            "annual-membership-individual",
            "Annual Membership — Individual",
            77,
            product_type="monthly",
            cadence="month",
            access_scope="Standard individual gym membership with key-fob access",
            commitment_type="fixed-term",
            minimum_months=12,
            fees=[{"type": "enrollment", "amount": 99, "currency": "USD", "cadence": "one-time", "mandatory": True}],
            raw_label="Annual Membership; $77 monthly dues; 12-month term; $99 individual join fee",
        )]

    if host.endswith("pilatesschoolsf.com") and has("Single Membership", "$250", "10 classes"):
        adapter = "pilates-school-geary"
        return [
            recurring(adapter, "single-membership", "Single Membership", 250, allowance={"count": 10, "period": "month", "unlimited": False}, access_scope="10 Geary Studio classes monthly plus selected Geary School reformer/tower classes"),
            recurring(adapter, "shared-two", "Shared Membership — Two People", 225, allowance={"count": 10, "period": "month", "unlimited": False}, eligibility_type="shared-membership", restrictions=["Requires a two-person shared membership"]),
            recurring(adapter, "shared-three", "Shared Membership — Three People", 199, allowance={"count": 10, "period": "month", "unlimited": False}, eligibility_type="shared-membership", restrictions=["Requires a three-person shared membership"]),
            recurring(adapter, "shared-four", "Shared Membership — Four People", 189, allowance={"count": 10, "period": "month", "unlimited": False}, eligibility_type="shared-membership", restrictions=["Requires a four-person shared membership"]),
            operator_visible_candidate(source_url, adapter, "intro-three", "New-Student 3-Class Intro Pack", 50, allowance={"count": 3, "period": "purchase", "unlimited": False}, promotion=True, promotion_label="New-student introductory pack", eligibility_type="new-student", restrictions=["New students only"]),
            operator_visible_candidate(source_url, adapter, "eight-class-pack", "8-Class Pack", 280, allowance={"count": 8, "period": "purchase", "unlimited": False}),
        ]

    if host.endswith("strongfriendsgym.com") and has("Drop-In", "$60"):
        return [operator_visible_candidate(source_url, "strong-friends", "drop-in", "Drop-In — Any Class", 60, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False})]

    if host.endswith("worldteamusa.net") and has("Day Pass", "$49", "Introduction Lesson", "$79", "30 Day Trial", "$285"):
        adapter = "world-team-usa"
        return [
            operator_visible_candidate(source_url, adapter, "day-pass", "Day Pass", 49, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}, restrictions=["Prior martial-arts experience required"]),
            operator_visible_candidate(source_url, adapter, "introduction-lesson", "Introduction Lesson", 79, allowance={"count": 1, "period": "purchase", "unlimited": False}, eligibility_type="onboarding", restrictions=["For new or inexperienced students"]),
            operator_visible_candidate(source_url, adapter, "thirty-day-trial", "30-Day Unlimited Trial", 285, allowance={"count": None, "period": "30 days", "unlimited": True}, promotion=True, promotion_label="30-day trial", eligibility_type="new-student", restrictions=["Trial offer"]),
            operator_visible_candidate(source_url, adapter, "online-seven-day-trial", "7-Day Online / Muay Thai & Fitness Trial", 105, allowance={"count": None, "period": "7 days", "unlimited": True}, promotion=True, promotion_label="7-day trial", eligibility_type="online-or-trial", restrictions=["Trial or online access product"]),
            operator_visible_candidate(source_url, adapter, "six-week-bootcamp", "6-Week Bootcamp", 399, allowance={"count": None, "period": "6 weeks", "unlimited": False}),
        ]

    if host.endswith("livefitgym.com") and has("Basic", "$117", "Premier", "$137", "Massage + Gym", "$207", "$227", "Wellness", "$357", "$377"):
        adapter = "live-fit-signup"
        term = {"commitment_type": "fixed-term", "minimum_months": 6}
        return [
            operator_visible_candidate(source_url, adapter, "basic", "Basic", 117, product_type="monthly", cadence="month", access_scope="Cole Valley, Nob Hill, Polk Street, and Hayes Valley locations", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
            operator_visible_candidate(source_url, adapter, "basic-massage-gym", "Massage + Gym — Basic Locations", 207, product_type="monthly", cadence="month", access_scope="Basic-location gym access plus monthly massage", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
            operator_visible_candidate(source_url, adapter, "basic-wellness", "Wellness — Basic Locations", 357, product_type="monthly", cadence="month", access_scope="Basic-location gym access plus wellness services", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
            operator_visible_candidate(source_url, adapter, "premier", "Premier", 137, product_type="monthly", cadence="month", access_scope="All eight Live Fit Gym locations", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
            operator_visible_candidate(source_url, adapter, "premier-massage-gym", "Massage + Gym — Premier Locations", 227, product_type="monthly", cadence="month", access_scope="All-location gym access plus monthly massage", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
            operator_visible_candidate(source_url, adapter, "premier-wellness", "Wellness — Premier Locations", 377, product_type="monthly", cadence="month", access_scope="All-location gym access plus wellness services", scope_type="multi-location", allowance={"count": None, "period": "month", "unlimited": True}, **term),
        ]

    if host.endswith("thecitycrossfit.com") and has("Month To Month", "$275", "Unlimited", "$250", "12x Per Month", "$35", "Drop In", "10 Class Pack"):
        adapter = "city-crossfit"
        return [
            recurring(adapter, "twelve-per-month", "12 Classes per Month", 250, allowance={"count": 12, "period": "month", "unlimited": False}),
            recurring(adapter, "unlimited-monthly", "Unlimited Monthly", 275, allowance={"count": None, "period": "month", "unlimited": True}),
            operator_visible_candidate(source_url, adapter, "drop-in", "Drop-In Class", 35, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-class-pack", "10 Class Pack", 275, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "intro-series", "Required 3-Session Intro Series", 275, allowance={"count": 3, "period": "purchase", "unlimited": False}, eligibility_type="onboarding", restrictions=["Required for new CrossFitters; not ordinary recurring access"]),
        ]

    if host.endswith("forgekravmaga.com") and has("Monthly Membership", "$200/month", "Annual Membership", "$2,160/year", "10 Class Pack", "$375", "Drop-In Day Pass", "$40"):
        adapter = "forge-krav-maga"
        return [
            recurring(adapter, "monthly-membership", "Monthly Membership", 200, allowance={"count": None, "period": "month", "unlimited": True}, access_scope="Unlimited Forge programs at the named location"),
            operator_visible_candidate(source_url, adapter, "annual-membership", "Annual Membership", 2160, cadence="year", allowance={"count": None, "period": "year", "unlimited": True}, commitment_type="prepaid", minimum_months=12),
            operator_visible_candidate(source_url, adapter, "drop-in-day-pass", "Drop-In Day Pass", 40, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-class-pack", "10 Class Pack", 375, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "two-trial-classes", "Two Trial Classes", 30, allowance={"count": 2, "period": "purchase", "unlimited": False}, promotion=True, promotion_label="New-student trial"),
        ]

    if host.endswith("funkydoor.com") and has("Auto Monthly - $129", "one time $49 sign up fee", "10 Class Pack - $260", "24 Class Pack - $495", "Drop-In Classes: $36"):
        adapter = "funky-door"
        signup_fee = [{"type": "enrollment", "amount": 49, "currency": "USD", "cadence": "one-time", "mandatory": True}]
        return [
            recurring(adapter, "studio-auto-monthly", "Studio Membership — Auto Monthly", 129, allowance={"count": None, "period": "month", "unlimited": True}, fees=signup_fee, best_value=True),
            recurring(adapter, "all-access-monthly", "All Access In-Person and At Home", 175, allowance={"count": None, "period": "month", "unlimited": True}, fees=signup_fee),
            recurring(adapter, "at-home-only", "At Home Livestream Only", 79, allowance={"count": None, "period": "month", "unlimited": True}, eligibility_type="online-only", restrictions=["Does not provide in-person use of the named location"]),
            operator_visible_candidate(source_url, adapter, "drop-in", "Drop-In Class", 36, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-class-pack", "10 Class Pack", 260, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "twenty-four-class-pack", "24 Class Pack", 495, allowance={"count": 24, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "forty-eight-class-pack", "48 Class Pack", 890, allowance={"count": 48, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "one-month-unlimited", "One-Month Unlimited Pass", 219, allowance={"count": None, "period": "30 days", "unlimited": True}),
            operator_visible_candidate(source_url, adapter, "annual-prepaid", "Annual Unlimited Membership", 1199, cadence="year", allowance={"count": None, "period": "year", "unlimited": True}, commitment_type="prepaid", minimum_months=12),
            operator_visible_candidate(source_url, adapter, "new-student-two-month", "New Student Two-Month Unlimited", 129, allowance={"count": None, "period": "2 months", "unlimited": True}, promotion=True, promotion_label="First-time local-student offer", eligibility_type="local-new-student", restrictions=["First-time student", "Bay Area resident with local ID"]),
            operator_visible_candidate(source_url, adapter, "visitor-seven-day", "Visitor Seven-Day Unlimited", 95, allowance={"count": None, "period": "7 days", "unlimited": True}, eligibility_type="visitor", restrictions=["Out-of-town visitors only"]),
        ]

    if host.endswith("studiobylivebetter.com") and has("Yoga Membership", "$150 per month", "Pilates Membership", "All Access Membership", "$210 per month", "4 Class Pack $116", "Yoga Class", "$30"):
        adapter = "studio-by-live-better"
        return [
            recurring(adapter, "yoga-membership", "Yoga Membership", 150, allowance={"count": None, "period": "month", "unlimited": True}, access_scope="Unlimited yoga classes at the named location"),
            recurring(adapter, "pilates-membership", "Pilates Membership", 150, allowance={"count": None, "period": "month", "unlimited": True}, access_scope="Unlimited mat Pilates classes at the named location"),
            recurring(adapter, "all-access-membership", "All Access Membership", 210, allowance={"count": None, "period": "month", "unlimited": True}, access_scope="Unlimited yoga, meditation, movement, sound-bath, and mat Pilates classes"),
            operator_visible_candidate(source_url, adapter, "yoga-four-pack", "Yoga 4 Class Pack", 116, allowance={"count": 4, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "yoga-five-pack", "Yoga 5 Class Pack", 140, allowance={"count": 5, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "yoga-ten-pack", "Yoga 10 Class Pack", 270, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "all-access-four-pack", "All Access 4 Class Pack", 132, allowance={"count": 4, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "all-access-five-pack", "All Access 5 Class Pack", 160, allowance={"count": 5, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "all-access-ten-pack", "All Access 10 Class Pack", 310, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "yoga-drop-in", "Yoga Class Drop-In", 30, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "pilates-drop-in", "Mat Pilates Drop-In", 35, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}, best_value=False),
        ]

    if host.endswith("sunsetgym.com") and has("6 Month Commitment", "Standard Rate", "$74.99", "Discount Rate", "$69.99", "No Enrollment Fees"):
        adapter = "sunset-gym"
        return [
            operator_visible_candidate(source_url, adapter, "standard-six-month", "Standard Monthly Membership", 74.99, product_type="monthly", cadence="month", commitment_type="fixed-term", minimum_months=6),
            operator_visible_candidate(source_url, adapter, "discount-six-month", "Discount Monthly Membership", 69.99, product_type="monthly", cadence="month", commitment_type="fixed-term", minimum_months=6, eligibility_type="eligibility-discount", restrictions=["Student, teacher, or age 55+ proof required"]),
            operator_visible_candidate(source_url, adapter, "couples-six-month", "Couples Monthly Membership", 140, product_type="monthly", cadence="month", commitment_type="fixed-term", minimum_months=6, eligibility_type="household", restrictions=["Couples membership"]),
        ]

    if host.endswith("yogamayu.com") and has("$195", "Monthly Auto Renewal", "2 months minimum commitment", "$28", "Drop-in", "$125", "5 in studio classes"):
        adapter = "yoga-mayu"
        return [
            operator_visible_candidate(source_url, adapter, "monthly-auto-renewal", "Unlimited Monthly Auto Renewal", 195, product_type="monthly", cadence="month", allowance={"count": None, "period": "month", "unlimited": True}, commitment_type="fixed-term", minimum_months=2),
            operator_visible_candidate(source_url, adapter, "drop-in", "Drop-In Class", 28, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "five-class-pass", "5 In-Studio Classes", 125, allowance={"count": 5, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-class-pass", "10 In-Studio Classes", 235, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "twenty-class-pass", "20 In-Studio Classes", 420, allowance={"count": 20, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "thirty-day-unlimited", "30-Day Unlimited Pass", 205, allowance={"count": None, "period": "30 days", "unlimited": True}),
            operator_visible_candidate(source_url, adapter, "new-student-three", "New Student 3-Class Special", 60, allowance={"count": 3, "period": "purchase", "unlimited": False}, promotion=True, promotion_label="First-time-student offer"),
        ]

    if host.endswith("solunayogaandwellness.com") and has("Basic Membership", "$100", "5 Classes per Month", "Standard Membership", "$160", "Unlimited Membership", "$189", "Single Drop in class", "$35"):
        adapter = "soluna-yoga"
        commitment = {"commitment_type": "fixed-term", "minimum_months": 3}
        return [
            operator_visible_candidate(source_url, adapter, "basic-five", "Basic Membership", 100, product_type="monthly", cadence="month", allowance={"count": 5, "period": "month", "unlimited": False}, **commitment),
            operator_visible_candidate(source_url, adapter, "standard-nine", "Standard Membership", 160, product_type="monthly", cadence="month", allowance={"count": 9, "period": "month", "unlimited": False}, **commitment),
            operator_visible_candidate(source_url, adapter, "unlimited", "Unlimited Membership", 189, product_type="monthly", cadence="month", allowance={"count": None, "period": "month", "unlimited": True}, **commitment),
            operator_visible_candidate(source_url, adapter, "drop-in", "Single Drop-In Class", 35, product_type="drop-in", cadence="visit", allowance={"count": 1, "period": "visit", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "four-class-pack", "4 Class Pack", 100, allowance={"count": 4, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "eight-class-pack", "8 Class Pack", 160, allowance={"count": 8, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "intro-three", "Intro 3-Class Special", 30, allowance={"count": 3, "period": "purchase", "unlimited": False}, promotion=True, promotion_label="Introductory offer"),
        ]

    if host.endswith("lotuslandyogasf.com") and has("Unlimited Monthly Sliding-Scale Membership $135 - $175", "$175", "One Month", "$450", "3 Month", "$122", "5 Class", "$220", "10 Class"):
        adapter = "lotusland-yoga"
        return [
            operator_visible_candidate(source_url, adapter, "one-month-unlimited", "One-Month Unlimited Pass", 175, allowance={"count": None, "period": "month", "unlimited": True}),
            operator_visible_candidate(source_url, adapter, "three-month-unlimited", "Three-Month Unlimited Pass", 450, allowance={"count": None, "period": "3 months", "unlimited": True}, commitment_type="prepaid", minimum_months=3),
            operator_visible_candidate(source_url, adapter, "five-class-pack", "5 Class Package", 122, allowance={"count": 5, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "ten-class-pack", "10 Class Package", 220, allowance={"count": 10, "period": "purchase", "unlimited": False}),
            operator_visible_candidate(source_url, adapter, "new-student-three", "New Student 3-Class Special", 30, allowance={"count": 3, "period": "purchase", "unlimited": False}, promotion=True, promotion_label="New-student offer"),
        ]
    return []


def perform_for_golf_plan_descriptors(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Retain amount-withheld P4G memberships as reviewable catalog products.

    Perform for Golf publicly enumerates recurring plan names and session
    allowances while disclosing the coached-plan amounts only after a
    consultation.  Dropping those cards makes a complete public catalog look
    like one isolated $300 Mindbody contract.  These descriptors preserve the
    named products with ``amount=None`` and can never become a selected exact
    price because their purchase method is contact-required.
    """

    host = hostname(source_url)
    if not (host == "performforgolf.com" or host.endswith(".performforgolf.com")):
        return []
    compact = " ".join(visible_text.split())
    named_plans = (
        ("par-4-sessions", "PAR Membership", 4, r"PAR\s+MEMBERSHIP\s*\(\s*4\s+SESSIONS?\s*/\s*MONTH\s*\)"),
        ("birdie-6-sessions", "Birdie Membership", 6, r"BIRDIE\s+MEMBERSHI\s*P\s*\(\s*6\s+SESSIONS?\s*/\s*MONTH\s*\)"),
        ("eagle-8-sessions", "Eagle Membership", 8, r"EAGLE\s+MEMBERSHIP\s*\(\s*8\s+SESSIONS?\s*/\s*MONTH\s*\)"),
        ("albatross-10-sessions", "Albatross Membership", 10, r"ALBATROSS(?:\s+MEMBERSHIP)?\s*\(\s*10\s+SESSIONS?\s*/\s*MONTH\s*\)"),
        ("ace-12-sessions", "Ace Membership", 12, r"ACE\s+MEMBERSHIP\s*\(\s*12\s+SESSIONS?\s*/\s*MONTH\s*\)"),
    )

    def descriptor(product_id: str, name: str, allowance: int, raw_label: str) -> dict[str, Any]:
        return {
            "kind": "plan-descriptor",
            "sourceProductId": product_id,
            "name": name,
            "amount": None,
            "currency": "USD",
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "classAllowance": {"count": allowance, "period": "month", "unlimited": False},
            "accessScope": (
                f"{allowance} interchangeable one-to-one coaching sessions per month plus "
                "designated open-gym and simulator access at all locations"
            ),
            "scopeType": "multi-location",
            "commitment": {"type": "unknown", "minimumMonths": None, "rawLabel": "Auto-monthly basis"},
            "promotion": {"isPromotion": False, "label": ""},
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "availability": "available",
            "purchaseMethod": "contact-required",
            "fees": [],
            "rawLabel": raw_label,
            "method": "visible-perform-for-golf-plan-descriptor",
            "adapter": "perform-for-golf-plan-descriptors",
            "evidenceTier": "official-public",
            "exactLocationMatch": "operator-market-multi-location",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        }

    matches = [
        descriptor(product_id, name, allowance, match.group(0))
        for product_id, name, allowance, pattern in named_plans
        if (match := re.search(pattern, compact, re.IGNORECASE))
    ]
    # Fail closed if the visible membership table is incomplete or has changed.
    if matches and len(matches) != len(named_plans):
        return []
    if matches:
        return matches

    minimum = re.search(
        r"memberships?\s+based\s+on\s+one[- ]on[- ]one\s+sessions?.{0,100}?"
        r"(?:from\s+)?2x\s*/\s*month.{0,80}?(?:up\s+to\s+)?12x\s*/\s*month",
        compact,
        re.IGNORECASE,
    )
    if minimum:
        return [descriptor(
            "unnamed-2-sessions",
            "2x/Month Membership (name not published)",
            2,
            minimum.group(0),
        )]
    return []


def visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    specialized = (
        crunch_visible_candidates(visible_text, source_url)
        or twenty_four_hour_visible_candidates(visible_text, source_url)
        or equinox_visible_candidates(visible_text, source_url)
        or planet_fitness_visible_candidates(visible_text, source_url)
        or orangetheory_visible_candidates(visible_text, source_url)
        or approach_visible_candidates(visible_text, source_url)
        or independent_operator_visible_candidates(visible_text, source_url)
        or perform_for_golf_plan_descriptors(visible_text, source_url)
    )
    candidates: list[dict[str, Any]] = list(specialized)
    candidates.extend(visible_cost_context_candidates(visible_text, source_url))
    requires_complete_card_adapter = hostname(source_url).endswith("orangetheory.com")
    patterns = (("drop-in", DROP_IN_AFTER_RE), ("drop-in", DROP_IN_BEFORE_RE)) if specialized or requires_complete_card_adapter else (
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


def visible_cost_context_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Capture explicit official ranges/starting prices without inventing a scalar.

    A range is accepted only when the same visible phrase names a purchasable
    product or service. Promotions are retained by the crawler only as scalar
    deal candidates elsewhere; they are not eligible official cost context.
    """

    candidates: list[dict[str, Any]] = []
    matches: list[tuple[str, re.Match[str]]] = [
        *(('range', match) for match in COST_RANGE_VISIBLE_RE.finditer(visible_text)),
        *(('starting-price', match) for match in COST_START_VISIBLE_RE.finditer(visible_text)),
    ]
    for kind, match in matches:
        raw_label = normalized_label(match.group(0))
        if not COST_CONTEXT_SEMANTIC_RE.search(raw_label) or PROMOTION_RE.search(raw_label):
            continue
        if kind == "range":
            low = float(match.group("low"))
            high = float(match.group("high"))
        else:
            low = high = float(match.group("amount"))
        if not (0 < low <= high <= 10_000):
            continue
        cadence = normalized_label(match.group("cadence") or "unknown").casefold()
        if cadence == "unknown":
            lowered = raw_label.casefold()
            if re.search(r"\b(?:monthly|per month)\b", lowered):
                cadence = "month"
            elif re.search(r"\bdrop[ -]?in\b", lowered):
                cadence = "class"
        context_product_type = (
            "drop-in" if re.search(r"\bdrop[ -]?in\b", raw_label, re.IGNORECASE)
            else "membership" if re.search(r"\b(?:membership|monthly)\b", raw_label, re.IGNORECASE)
            else "service"
        )
        candidates.append(
            {
                "kind": kind,
                "low": low,
                "high": high,
                "currency": "USD",
                "rawLabel": raw_label,
                "cadence": cadence,
                "productType": "cost-context",
                "contextProductType": context_product_type,
                "promotion": {"isPromotion": False, "label": ""},
                "method": "visible-cost-context",
                "adapter": platform_name(source_url),
                "evidenceTier": "official-public",
                "exactLocationMatch": "candidate",
                "sourceUrl": source_url,
                "autoPublishEligible": False,
                "selectable": False,
            }
        )
    return deduplicate_candidates(candidates)


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


def abc_fitness_storefronts(source_url: str) -> list[str]:
    """Expand an operator-linked ABC Online Join URL into its public catalog."""

    if platform_name(source_url) != "abc-fitness":
        return []
    parsed = urlparse(source_url)
    values = parse_qs(parsed.query)
    club = text((values.get("club") or values.get("clubNumber") or [""])[0])
    if not club or not club.isdigit():
        return []
    return [f"https://{parsed.netloc}/api/online-join/signup/planList?clubNumber={club}"]


def abc_fitness_catalog_candidates(payload: Any, source_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Reconstruct ABC Fitness plans and plan-linked charges from public APIs."""

    if platform_name(source_url) != "abc-fitness":
        return [], []
    parsed = urlparse(source_url)
    club = text((parse_qs(parsed.query).get("clubNumber") or [""])[0])
    if isinstance(payload, list):
        nested = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            plan_id = text(item.get("planId"))
            if club and plan_id and re.fullmatch(r"[A-Za-z0-9-]{8,80}", plan_id):
                nested.append(
                    f"https://{parsed.netloc}/api/online-join/signup/calculatePlan?planId={plan_id}&clubNumber={club}"
                )
        return [], list(dict.fromkeys(nested))
    if not isinstance(payload, dict):
        return [], []
    plan_id = text(payload.get("planId"))
    name = text(payload.get("planName"))
    amount = numeric(payload.get("renewalAmount") or payload.get("scheduleTotalAmount"))
    if not plan_id or not name or amount is None or amount <= 0:
        return [], []
    senior = bool(re.search(r"\bsenior\b", name, re.IGNORECASE))
    dual = bool(re.search(r"\bdual\s+site\b", name, re.IGNORECASE))
    fees = []
    for charge in payload.get("downPayments", []):
        if not isinstance(charge, dict):
            continue
        label = text(charge.get("name"))
        charge_amount = numeric(charge.get("total") or charge.get("subTotal"))
        if charge_amount is None or charge_amount <= 0 or not re.search(r"\bfee\b", label, re.IGNORECASE):
            continue
        fees.append({
            "type": "enrollment" if "enrollment" in label.casefold() else "other",
            "name": label,
            "amount": charge_amount,
            "currency": "USD",
            "cadence": "one-time",
            "mandatory": True,
        })
    cadence = "month" if "month" in text(payload.get("renewalFrequency")).casefold() else "unknown"
    candidate = {
        "sourceProductId": plan_id,
        "name": name.removesuffix("_MP"),
        "amount": amount,
        "currency": "USD",
        "cadence": cadence,
        "billingInterval": cadence,
        "productType": "monthly" if cadence == "month" else "offer",
        "accessScope": "Both operator locations" if dual else "Named home location",
        "scopeType": "multi-location" if dual else "single-location",
        "classAllowance": None,
        "eligibility": {
            "type": "age-restricted" if senior else "standard-adult",
            "restrictions": ["Age 65+"] if senior else [],
        },
        "commitment": {
            "type": "month-to-month" if text(payload.get("agreementTerm")).casefold() == "open" else "unknown",
            "minimumMonths": None,
            "rawLabel": text(payload.get("agreementTerm")),
        },
        "promotion": {"isPromotion": bool(payload.get("activePresale")), "label": "Presale" if payload.get("activePresale") else ""},
        "fees": fees,
        "bestValueLabel": False,
        "rawLabel": f"{name} ${amount:g}/{cadence}; " + ", ".join(f"{fee['name']} ${fee['amount']:g}" for fee in fees),
        "method": "public-abc-fitness-plan-api",
        "adapter": "abc-fitness",
        "evidenceTier": "official-public",
        "exactLocationMatch": "exact-location",
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }
    return [candidate], []


def public_platform_json_candidates(payload: Any, source_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Route captured public JSON through the most semantic platform adapter.

    The generic graph walker remains a fallback, but must not replace an
    adapter that understands billing intervals, location scope, or plan-linked
    fees.  Both the static and rendered crawlers use this single dispatcher so
    JavaScript hydration cannot silently lose catalog semantics.
    """

    platform = platform_name(source_url)
    if platform == "mariana-tek":
        return mariana_buy_page_candidates(payload if isinstance(payload, dict) else {}, source_url), []
    if platform == "xponential-member-app":
        return xponential_package_candidates(payload if isinstance(payload, dict) else {}, source_url)
    if platform == "abc-fitness":
        return abc_fitness_catalog_candidates(payload, source_url)
    if platform_adapters.platform_for_url(source_url):
        candidates = platform_adapters.extract_candidates(payload, source_url)
        candidates.extend(structured_candidates([json.dumps(payload)], source_url, "public-platform-json"))
        return deduplicate_candidates(candidates), []
    return [], []


def linked_storefronts(
    base_url: str,
    links: list[str],
    gym: dict[str, Any] | None = None,
) -> list[str]:
    results: list[str] = []
    identities: set[str] = set()
    base_host = hostname(base_url)
    for value in links:
        candidate = urljoin(base_url, value)
        host = hostname(candidate)
        if not is_public_http_url(candidate):
            continue
        approved_booking = approved_booking_url(candidate)
        approved_operator_page = (
            host == base_host
            and candidate != base_url
            and RESEARCH_PATH_RE.search(urlparse(candidate).path + ("?" + urlparse(candidate).query if urlparse(candidate).query else ""))
            and not RESEARCH_EXCLUDE_RE.search(urlparse(candidate).path)
            and operator_page_matches_gym(candidate, gym)
        )
        if approved_booking or approved_operator_page:
            identity = request_identity(candidate)
            if identity not in identities:
                identities.add(identity)
                results.append(candidate)
    return results[:12]


MINDBODY_EMBED_SITE_RE = re.compile(
    r"\bdata-mb-site-id\s*=\s*['\"](?P<site_id>\d{1,12})['\"]",
    re.IGNORECASE,
)


def mindbody_embedded_storefronts(html: str) -> list[str]:
    """Recover public Mindbody stores from account-only HealCode embeds.

    Some operators expose only a ``Login | Register`` widget on their own
    site.  The widget still carries the operator's public Mindbody Site ID,
    which is sufficient to open the standard unauthenticated services store.
    ``data-site-id`` is deliberately ignored because that is a HealCode widget
    identity rather than the Mindbody business Site ID.
    """

    results: list[str] = []
    for match in MINDBODY_EMBED_SITE_RE.finditer(html):
        site_id = match.group("site_id")
        candidate = f"https://clients.mindbodyonline.com/classic/ws?studioid={site_id}&stype=41"
        if candidate not in results:
            results.append(candidate)
    return results[:4]


def approved_booking_url(url: str) -> bool:
    """Return whether a reviewed URL is an allowed public booking surface."""

    host = hostname(url)
    if not is_public_http_url(url) or RESEARCH_EXCLUDE_RE.search(urlparse(url).path):
        return False
    if not any(host == domain or host.endswith(f".{domain}") for domain in BOOKING_DOMAINS):
        return False
    if "classpass.com" in host:
        return False  # Marketplaces may create discovery leads but never exact evidence.
    if host in {"pushpress.com", "www.pushpress.com"}:
        return False  # Operator checkout uses a dedicated subdomain, not vendor marketing pages.
    return True


def is_operator_wide_pricing_document(url: str) -> bool:
    """Reject location-scoped pricing pages unless identity matched upstream."""

    segments = [segment.casefold() for segment in urlparse(url).path.split("/") if segment]
    categories = {
        "pricing", "prices", "pricespolicies", "rate", "rates", "membership", "memberships",
        "plan", "plans", "package", "packages", "pass", "passes", "drop-in", "dropins", "buy", "join",
    }
    allowed_prefixes = {"content", "membership", "memberships", "memberships-join-us", "shop", "online"}
    allowed_tails = {
        "options", "membership-options", "membership-options-2", "benefits", "faqs", "faq", "policies", "policy",
        "types", "types-of-membership", "pricing", "prices", "rates", "plans", "packages", "passes", "buy", "join",
    }
    for index, segment in enumerate(segments):
        if segment not in categories:
            continue
        prefixes = segments[:index]
        tails = segments[index + 1:]
        prefixes_are_generic = all(value in allowed_prefixes or re.fullmatch(r"[a-z]{2}", value) for value in prefixes)
        tails_are_generic = all(value in allowed_tails for value in tails)
        if prefixes_are_generic and tails_are_generic:
            return True
    return False


@lru_cache(maxsize=1)
def load_operator_document_candidates(path: Path = OPERATOR_DOCUMENT_CANDIDATES_PATH) -> tuple[dict[str, Any], ...]:
    """Load review-only official sitemap leads once per crawler process."""

    if not path.exists():
        return ()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(item for item in document.get("candidates", []) if isinstance(item, dict))


def reviewed_seed_routes(
    gym: dict[str, Any],
    document_candidates: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build bounded crawl seeds from committed, reviewed evidence routes.

    Operator URLs are trusted only from the canonical website/official fields.
    Other evidence must share one of those exact hosts (allowing a ``www``
    variant) or use an approved public booking domain. Source/directory URLs are
    intentionally excluded.
    """

    def host_key(value: str) -> str:
        host = hostname(value)
        return host[4:] if host.startswith("www.") else host

    def candidate(field: str, value: Any) -> tuple[str, str] | None:
        url = urldefrag(text(value))[0]
        if not is_public_http_url(url) or coverage.is_osm_url(url):
            return None
        if RESEARCH_EXCLUDE_RE.search(urlparse(url).path):
            return None
        return field, url

    canonical = [
        item for item in (
            candidate("websiteUrl", gym.get("websiteUrl")),
            candidate("officialUrl", gym.get("officialUrl")),
        ) if item
    ]
    operator_hosts = {
        host_key(url)
        for _field, url in canonical
        if platform_name(url) == "operator-site"
    }

    values: list[tuple[str, Any]] = [
        ("websiteUrl", gym.get("websiteUrl")),
        ("officialUrl", gym.get("officialUrl")),
        ("priceSourceUrl", gym.get("priceSourceUrl")),
    ]
    gym_id = text(gym.get("id"))
    sitemap_leads = document_candidates if document_candidates is not None else load_operator_document_candidates()
    for item in sitemap_leads:
        if text(item.get("reviewStatus")) not in {"pending", "approved"}:
            continue
        url = urldefrag(text(item.get("url")))[0]
        matching_ids = {text(value) for value in item.get("matchingGymIds", [])}
        exact_match = gym_id in matching_ids and float(item.get("identityScore") or 0) >= 2
        operator_wide = (
            text(item.get("candidateType")) == "operator-document"
            and not matching_ids
            and is_operator_wide_pricing_document(url)
        )
        if (
            (exact_match or operator_wide)
            and host_key(url) in operator_hosts
            and RESEARCH_PATH_RE.search(urlparse(url).path)
            and not RESEARCH_EXCLUDE_RE.search(urlparse(url).path)
        ):
            values.append(("operatorDocumentCandidate", url))
    for collection_name in ("plans", "dropIns"):
        for item in gym.get(collection_name, []) or []:
            if not isinstance(item, dict):
                continue
            for evidence_key in ("evidence", "sourceEvidence"):
                evidence = item.get(evidence_key)
                if isinstance(evidence, dict):
                    values.append((f"{collection_name}.{evidence_key}.url", evidence.get("url") or evidence.get("sourceUrl")))
    for item in gym.get("costContext", []) or []:
        if isinstance(item, dict):
            values.append(("costContext.sourceUrl", item.get("sourceUrl") or item.get("url")))

    routes: list[dict[str, str]] = []
    seen: set[str] = set()
    for field, value in values:
        normalized = candidate(field, value)
        if not normalized:
            continue
        source_field, url = normalized
        allowed = source_field in {"websiteUrl", "officialUrl"}
        allowed = allowed or host_key(url) in operator_hosts or approved_booking_url(url)
        identity = request_identity(url)
        if not allowed or identity in seen:
            continue
        seen.add(identity)
        routes.append({"url": url, "sourceField": source_field})
        if len(routes) >= MAX_REVIEWED_SEED_URLS:
            break
    return routes


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


def fetch_once_for_run(
    url: str,
    run_requests: dict[str, concurrent.futures.Future[dict[str, Any]]],
    run_requests_lock: threading.Lock,
    fetcher: Any,
) -> dict[str, Any]:
    """Share one physical public request across every logical location crawl.

    Operators frequently expose one market-wide pricing page or booking catalog
    from several location pages.  Crawling per canonical location used to fetch
    that same URL once per location, making large chains both slow and noisy.
    A Future is installed before the request starts so concurrent workers wait
    for and reuse the first response instead of racing duplicate requests.
    Fragments, tracking parameters, and presentation-only locale parameters
    are intentionally ignored. Product, location, and catalog query values
    remain distinct identities.
    """

    request_key = request_identity(url)
    with run_requests_lock:
        future = run_requests.get(request_key)
        owner = future is None
        if owner:
            future = concurrent.futures.Future()
            run_requests[request_key] = future
    assert future is not None
    if not owner:
        shared = dict(future.result())
        shared["sharedResponse"] = True
        return shared
    try:
        result = fetcher()
    except BaseException as error:
        future.set_exception(error)
        raise
    future.set_result(result)
    return result


def parse_page(
    result: dict[str, Any],
    gym: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], str]:
    html = text(result.get("html"))
    if not html:
        return [], [], ""
    source_url = text(result.get("url"))
    is_json = "json" in text(result.get("contentType")).casefold()
    if is_json and platform_adapters.platform_for_url(source_url):
        try:
            payload = json.loads(html)
        except json.JSONDecodeError:
            payload = {}
        candidates, nested = public_platform_json_candidates(payload, source_url)
        return candidates, nested, hashlib.sha256(html.encode("utf-8")).hexdigest()
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
    stores = linked_storefronts(source_url, parser.links, gym)
    for candidate in mindbody_embedded_storefronts(html):
        if candidate not in stores:
            stores.append(candidate)
    for candidate in mariana_storefronts(html):
        if candidate not in stores:
            stores.append(candidate)
    for candidate in xponential_storefronts(html):
        if candidate not in stores:
            stores.append(candidate)
    for candidate in abc_fitness_storefronts(source_url):
        if candidate not in stores:
            stores.append(candidate)
    return deduplicated, stores[:12], digest


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in candidates:
        key = (
            candidate.get("kind"), candidate.get("low"), candidate.get("high"), candidate.get("amount"),
            candidate.get("productType"), candidate.get("rawLabel"), candidate.get("sourceProductId"),
        )
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
                if isinstance(feature, dict) and feature.get("value") not in {False, "false"} and text(feature.get("name")):
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
    routes = reviewed_seed_routes(gym)
    if not routes:
        return False
    url = routes[0]["url"]
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


def load_rendered_deal_observations(path: Path = RENDERED_OBSERVATIONS_PATH) -> list[dict[str, Any]]:
    """Keep rendered promotion evidence when a static crawl refreshes deals."""

    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in document.get("observations", []) if isinstance(item, dict)]


def crawl_gym(
    gym: dict[str, Any],
    cache: dict[str, Any],
    today: datetime,
    timeout: float,
    domain_locks: dict[str, threading.Lock],
    last_domain_request: dict[str, float],
    domain_429_counts: dict[str, int],
    domain_next_request: dict[str, float],
    run_requests: dict[str, concurrent.futures.Future[dict[str, Any]]],
    run_requests_lock: threading.Lock,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    def rate_limited_fetch(url: str) -> dict[str, Any]:
        def perform_fetch() -> dict[str, Any]:
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

        return fetch_once_for_run(url, run_requests, run_requests_lock, perform_fetch)

    seed_routes = reviewed_seed_routes(gym)
    if not seed_routes:
        return [], [], [], {}
    url = seed_routes[0]["url"]
    result = rate_limited_fetch(url)
    offers, storefronts, digest = parse_page(result, gym)
    location_candidates = parse_location_page(result)
    if result.get("status") == "not-modified":
        offers = list(cache.get(url, {}).get("candidates", []))
        storefronts = linked_storefronts(url, list(cache.get(url, {}).get("linkedStorefronts", [])), gym)
        location_candidates = list(cache.get(url, {}).get("locationCandidates", []))
        digest = text(cache.get(url, {}).get("contentHash"))
    attempted_at = today.date().isoformat()
    previous_hash = text(cache.get(url, {}).get("contentHash"))
    published = gym.get("monthlyPrice")
    candidate_monthly = [
        amount
        for offer in offers
        if (offer.get("productType") == "monthly" or offer.get("cadence") == "month")
        and (amount := numeric(offer.get("amount"))) is not None
    ]
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
            "sharedResponse": bool(result.get("sharedResponse")),
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
    pending: list[tuple[str, str, int]] = [
        (route["url"], f"reviewed-record:{route['sourceField']}", 1)
        for route in seed_routes[1:]
    ]
    pending.extend((storefront, url, 1) for storefront in storefronts)
    visited: set[str] = {request_identity(url)}
    operator_request_count = int(platform_name(url) == "operator-site")
    booking_request_count = int(platform_name(url) != "operator-site")
    frontier_skip_reasons: dict[str, int] = defaultdict(int)
    while pending and len(visited) < MAX_LINKED_REQUESTS_PER_GYM:
        storefront, linked_from, depth = pending.pop(0)
        storefront_identity = request_identity(storefront)
        if storefront_identity in visited:
            continue
        is_operator_request = platform_name(storefront) == "operator-site"
        if is_operator_request and operator_request_count >= MAX_OPERATOR_REQUESTS_PER_GYM:
            frontier_skip_reasons["operator-request-budget"] += 1
            continue
        visited.add(storefront_identity)
        operator_request_count += int(is_operator_request)
        booking_request_count += int(not is_operator_request)
        store_result = rate_limited_fetch(storefront)
        store_offers, nested, store_digest = parse_page(store_result, gym)
        store_location_candidates = parse_location_page(store_result)
        if store_result.get("status") == "not-modified":
            store_offers = list(cache.get(storefront, {}).get("candidates", []))
            nested = linked_storefronts(
                storefront,
                list(cache.get(storefront, {}).get("linkedStorefronts", [])),
                gym,
            )
            store_location_candidates = list(cache.get(storefront, {}).get("locationCandidates", []))
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
                "sharedResponse": bool(store_result.get("sharedResponse")),
                "linkedFrom": linked_from,
                "linkDepth": depth,
                "requiresReview": bool(store_offers),
                "priceChangeOver20Percent": False,
            }
        )
        observations.extend({"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **offer} for offer in store_offers)
        location_observations.extend(
            {"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **candidate}
            for candidate in store_location_candidates
        )
        updates[storefront] = {
            "status": store_result["status"],
            "lastAttemptAt": attempted_at,
            "etag": store_result.get("etag", ""),
            "lastModified": store_result.get("lastModified", ""),
            "contentHash": store_digest,
            "candidates": store_offers,
            "linkedStorefronts": nested,
            "locationCandidates": store_location_candidates,
        }
        if depth < MAX_LINK_DEPTH:
            queued = {request_identity(item[0]) for item in pending}
            for detail_url in nested[:12]:
                child_is_operator = platform_name(detail_url) == "operator-site"
                if child_is_operator and not operator_page_matches_gym(detail_url, gym):
                    frontier_skip_reasons["different-location"] += 1
                    continue
                if child_is_operator and depth >= MAX_OPERATOR_LINK_DEPTH:
                    frontier_skip_reasons["operator-depth"] += 1
                    continue
                detail_identity = request_identity(detail_url)
                if detail_identity not in visited and detail_identity not in queued:
                    pending.append((detail_url, storefront, depth + 1))
                    queued.add(detail_identity)
    attempts_by_identity = {request_identity(text(item.get("url"))): item for item in attempts}
    reviewed_attempts = [
        attempts_by_identity[request_identity(route["url"])]
        for route in seed_routes
        if request_identity(route["url"]) in attempts_by_identity
    ]
    terminal_gone_statuses = {"http-404", "http-410"}
    all_reviewed_seeds_gone = bool(reviewed_attempts) and len(reviewed_attempts) == len(seed_routes) and all(
        text(item.get("status")) in terminal_gone_statuses for item in reviewed_attempts
    )
    attempts[0]["reviewedSeedCount"] = len(seed_routes)
    attempts[0]["reviewedSeedAttemptCount"] = len(reviewed_attempts)
    attempts[0]["operatorRequestCount"] = operator_request_count
    attempts[0]["bookingRequestCount"] = booking_request_count
    attempts[0]["frontierSkipReasons"] = dict(sorted(frontier_skip_reasons.items()))
    attempts[0]["allReviewedSeedsGone"] = all_reviewed_seeds_gone
    if all_reviewed_seeds_gone:
        attempts[0]["requiresReview"] = True
        attempts[0]["sourceStatusReviewReason"] = "all-reviewed-operator-and-evidence-routes-return-404-or-410"
    return attempts, observations, location_observations, updates


def fail_closed_crawl(
    gym: dict[str, Any],
    today: datetime,
    runner: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Isolate one source/parser failure instead of aborting a city-wide run."""

    try:
        return runner()
    except Exception as error:
        return ([{
            "gymId": text(gym.get("id")),
            "name": text(gym.get("name")),
            "url": text(gym.get("officialUrl")) or text(gym.get("websiteUrl")),
            "attemptedAt": today.date().isoformat(),
            "status": "worker-error",
            "robotsStatus": "unknown",
            "contentHash": "",
            "contentChanged": False,
            "candidateCount": 0,
            "sharedResponse": False,
            "linkedStorefronts": [],
            "requiresReview": True,
            "priceChangeOver20Percent": False,
            "error": f"{type(error).__name__}: {text(error)}"[:240],
        }], [], [], {})


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
    run_requests: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
    run_requests_lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(
            lambda gym: fail_closed_crawl(
                gym,
                today,
                lambda: crawl_gym(
                    gym, cache, today, args.timeout, domain_locks, last_domain_request,
                    domain_429_counts, domain_next_request, run_requests, run_requests_lock,
                ),
            ),
            candidates,
        )
        for gym_attempts, gym_observations, gym_locations, cache_updates in results:
            attempts.extend(gym_attempts)
            observations.extend(gym_observations)
            location_observations.extend(gym_locations)
            cache.update(cache_updates)
    run_attempts = list(attempts)
    crawled_gym_ids = {text(gym.get("id")) for gym in candidates}
    attempts_by_key = {
        (text(item.get("gymId")), text(item.get("url"))): item
        for item in existing_attempts_document.get("attempts", [])
        if text(item.get("gymId")) not in crawled_gym_ids
    }
    attempts_by_key.update({(text(item.get("gymId")), text(item.get("url"))): item for item in attempts})
    attempts = sorted(attempts_by_key.values(), key=lambda item: (text(item.get("gymId")), text(item.get("url"))))
    observations = [item for item in existing_observations_document.get("observations", []) if text(item.get("gymId")) not in crawled_gym_ids] + observations
    observations.sort(
        key=lambda item: (
            text(item.get("gymId")), text(item.get("sourceUrl")), text(item.get("kind")),
            float(item.get("low", 0) or 0), float(item.get("amount", 0) or 0), text(item.get("rawLabel")),
        )
    )
    location_observations = [item for item in existing_locations_document.get("observations", []) if text(item.get("gymId")) not in crawled_gym_ids] + location_observations
    location_observations.sort(key=lambda item: (text(item.get("gymId")), text(item.get("sourceUrl")), text(item.get("rawLabel"))))
    save_json(CACHE_PATH, cache)
    save_json(ATTEMPTS_PATH, {"generatedAt": today.date().isoformat(), "mode": args.mode, "attempts": attempts})
    save_json(OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "observations": observations})
    save_json(LOCATION_OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "observations": location_observations})
    eligible_deal_ids = {
        text(gym.get("id")) for gym in document.get("gyms", []) if deal_eligible_gym(gym)
    }
    deals = deal_candidates(
        observations + load_rendered_deal_observations(),
        eligible_deal_ids,
    )
    save_json(DEAL_OBSERVATIONS_PATH, {"generatedAt": today.date().isoformat(), "mode": args.mode, "deals": deals})
    save_json(DEAL_REPORT_PATH, {
        "generatedAt": today.date().isoformat(),
        "mode": args.mode,
        "dealCandidateCount": len(deals),
        "locationCount": len({item["gymId"] for item in deals}),
        "reviewRequiredCount": sum(item["reviewStatus"] == "pending" for item in deals),
        "ordinaryPricesRemainAuthoritative": True,
        "includesRenderedEvidence": True,
    })
    print(json.dumps({
        "candidateGyms": len(candidates),
        "logicalRequests": len(run_attempts),
        "physicalRequests": len(run_requests),
        "sharedResponseReuses": sum(bool(item.get("sharedResponse")) for item in run_attempts),
        "observations": len(observations),
        "dealCandidates": len(deals),
        "reviewRequired": sum(item["requiresReview"] for item in run_attempts),
        "sourceStatusReviews": sum(bool(item.get("allReviewedSeedsGone")) for item in run_attempts),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
