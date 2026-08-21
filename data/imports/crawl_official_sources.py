"""Conservative crawler for official gym pages and linked public storefronts.

The crawler discovers candidate observations; it never auto-publishes a price.
It does not submit forms, authenticate, or transmit personal information.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import http.cookiejar
import json
import re
import threading
import time
import zlib
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, unquote, urldefrag, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser

import brotli
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
PARSER_VERSION = "selected-plan-catalog-v28"
MAX_RESPONSE_BYTES = 4_000_000
DOMAIN_DELAY_SECONDS = 1.5
MAX_DOMAIN_429S = 2
STALE_AFTER_DAYS = 35
MAX_LINKED_REQUESTS_PER_GYM = 36
MAX_LINK_DEPTH = 3
MAX_OPERATOR_LINK_DEPTH = 2
MAX_OPERATOR_REQUESTS_PER_GYM = 12
MAX_REVIEWED_SEED_URLS = 8
SENSITIVE_PERSISTED_QUERY_PARAMS = frozenset({
    "access_token",
    "authorization",
    "client_secret",
    "code",
    "cookie",
    "csrf",
    "csrf_token",
    "password",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "token",
})

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
    "members.purebarre.com",
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
    "oms-sales-api.bayclubs.io",
    "classpass.com",
}
MONEY_AMOUNT_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d{1,5})(?:\.\d{1,2})?"
MONEY_RE = re.compile(rf"\$({MONEY_AMOUNT_PATTERN})(?!\d|,\d)")
MONTHLY_RE = re.compile(
    rf"(?P<label>.{{0,110}}?\$(?P<amount>{MONEY_AMOUNT_PATTERN})(?!\d|,\d)[^$]{{0,70}}?(?:/\s*mo(?:nth)?|per\s+month|monthly))",
    re.IGNORECASE | re.DOTALL,
)
DROP_IN_AFTER_RE = re.compile(
    rf"(?P<label>.{{0,90}}?(?:drop[ -]?in|single (?:class|visit)|day pass).{{0,70}}?\$(?P<amount>{MONEY_AMOUNT_PATTERN})(?!\d|,\d))",
    re.IGNORECASE | re.DOTALL,
)
DROP_IN_BEFORE_RE = re.compile(
    rf"(?P<label>.{{0,90}}?\$(?P<amount>{MONEY_AMOUNT_PATTERN})(?!\d|,\d)[^$]{{0,45}}?(?:drop[ -]?in|single (?:class|visit)|day pass))",
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
BOOKING_ACTION_EXCLUDE_RE = re.compile(
    r"/(?:login|signin|sign-in|account|checkout|registration|prospect|free-?trial|retail(?:-cart)?)"
    r"(?:\.cfm)?(?:/|$|[?#])",
    re.IGNORECASE,
)
MONTH_TO_MONTH_RE = re.compile(r"\bmonth\s*[-–—]?\s*to\s*[-–—]?\s*month\b", re.IGNORECASE)
BAY_CLUB_API_HOST = "oms-sales-api.bayclubs.io"
BAY_CLUB_API_BASE = f"https://{BAY_CLUB_API_HOST}/api/1.0"
BAY_CLUB_CLUBS_URL = f"{BAY_CLUB_API_BASE}/clubs"
BAY_CLUB_BUILDER_HOST = "join.bayclubs.com"
BAY_CLUB_BUILDER_PATH = "/shared/membership-builder"
REDPOINT_HOST = "portal.movementgyms.com"
REDPOINT_CLIENT_VERSION = "1.3.723"
REDPOINT_PREVIEW_MARKER = "sfGymPreview"
REDPOINT_GRAPHQL_PATH = "/graphql-public"
REDPOINT_CSRF_PATH = "/csrf-bootstrap"
REDPOINT_ID_PREFIXES = {
    "plan": "UGxhbjo",
    "session": "U2Vzc2lvbj",
    "facility": "RmFjaWxpdHk6",
    "enrollment": "RW5yb2xsbWVudFR5cGU6",
}
SOULCYCLE_HOSTS = {"soul-cycle.com", "www.soul-cycle.com"}
SOULCYCLE_SERIES_PATH = "/series/"
SOULCYCLE_SERIES_API_RE = re.compile(
    r"^/series/json/(?P<region_id>\d{1,4})/?$",
    re.IGNORECASE,
)
ACUITY_BUSINESS_ASSIGN_RE = re.compile(r"\bvar\s+BUSINESS\s*=\s*", re.IGNORECASE)
MOMENCE_MEMBERSHIP_PAGE_RE = re.compile(
    r"^/(?:m|[^/]+/membership/[^/]+)/(\d{1,12})/?$",
    re.IGNORECASE,
)
MOMENCE_MEMBERSHIP_API_RE = re.compile(
    r"^/_api/primary/plugin/memberships/(\d{1,12})/?$",
    re.IGNORECASE,
)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.json_ld: list[str] = []
        self.hydration_json: list[str] = []
        self.visible: list[str] = []
        self.squarespace_text_blocks: list[str] = []
        self._in_script = False
        self._script_type = ""
        self._script_id = ""
        self._script_parts: list[str] = []
        self._hidden_depth = 0
        self._tag_stack: list[tuple[str, bool]] = []
        self._squarespace_block_depth: int | None = None
        self._squarespace_block_parts: list[str] = []

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
        introduced_hidden = bool(
            tag.casefold() in {"style", "template", "noscript"}
            or values.get("hidden")
            or values.get("aria-hidden", "").casefold() == "true"
            or "display:none" in values.get("style", "").replace(" ", "").casefold()
        )
        self._tag_stack.append((tag.casefold(), introduced_hidden))
        if introduced_hidden:
            self._hidden_depth += 1
        if (
            self._squarespace_block_depth is None
            and values.get("data-sqsp-block", "").casefold() == "text"
        ):
            self._squarespace_block_depth = len(self._tag_stack)
            self._squarespace_block_parts = []

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
        closes_squarespace_block = bool(
            self._squarespace_block_depth is not None
            and len(self._tag_stack) == self._squarespace_block_depth
            and self._tag_stack
            and self._tag_stack[-1][0] == closing
        )
        while self._tag_stack:
            opened, introduced_hidden = self._tag_stack.pop()
            if introduced_hidden:
                self._hidden_depth = max(0, self._hidden_depth - 1)
            if opened == closing:
                break
        if closes_squarespace_block:
            block = re.sub(r"\s+", " ", " ".join(self._squarespace_block_parts)).strip()
            if block:
                self.squarespace_text_blocks.append(block[:2000])
            self._squarespace_block_depth = None
            self._squarespace_block_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
        elif not self._hidden_depth and data.strip():
            self.visible.append(data.strip())
            if self._squarespace_block_depth is not None:
                self._squarespace_block_parts.append(data.strip())


class SoulCyclePackParser(HTMLParser):
    """Capture complete public price cards while preserving input metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._div_depth = 0
        self._card_depth: int | None = None
        self._text_parts: list[str] = []
        self._attributes: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "div":
            self._div_depth += 1
            classes = {value.casefold() for value in values.get("class", "").split()}
            if self._card_depth is None and "pack-card" in classes:
                self._card_depth = self._div_depth
                self._text_parts = []
                self._attributes = {
                    "cardQaId": values.get("data-qa-id", ""),
                    "cardClass": values.get("class", ""),
                }
        if self._card_depth is None:
            return
        if tag.casefold() == "input":
            name = values.get("name", "")
            if name and values.get("value", ""):
                self._attributes[f"input:{name}"] = values["value"]
            for key, value in values.items():
                if key.startswith("data-") and value:
                    self._attributes[key] = value

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "div":
            return
        if self._card_depth is not None and self._div_depth == self._card_depth:
            self.cards.append({
                "text": " ".join(" ".join(self._text_parts).split()),
                "attributes": dict(self._attributes),
            })
            self._card_depth = None
            self._text_parts = []
            self._attributes = {}
        self._div_depth = max(0, self._div_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._card_depth is not None and data.strip():
            self._text_parts.append(data.strip())


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def url_has_sensitive_query(value: Any) -> bool:
    """Return whether an HTTP URL contains transient credential-like state."""

    url = text(value)
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    return any(
        key.casefold() in SENSITIVE_PERSISTED_QUERY_PARAMS
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    )


def redact_persisted_url(value: Any) -> str:
    """Remove session/token query parameters from committed audit URLs."""

    url = text(value)
    if not url:
        return ""
    if not url_has_sensitive_query(url):
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.query:
        return url
    retained = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in SENSITIVE_PERSISTED_QUERY_PARAMS
    ]
    return parsed._replace(query=urlencode(retained, doseq=True)).geturl()


def sanitize_persisted_value(value: Any) -> Any:
    """Recursively redact transient query state without retaining raw secrets."""

    if isinstance(value, dict):
        return {key: sanitize_persisted_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_persisted_value(item) for item in value]
    if isinstance(value, str) and value.casefold().startswith(("http://", "https://")):
        return redact_persisted_url(value)
    return value


def cache_for_persistence(cache: dict[str, Any]) -> dict[str, Any]:
    """Drop transient request entries and links before committing crawl cache."""

    persisted: dict[str, Any] = {}
    for url, entry in cache.items():
        if url_has_sensitive_query(url):
            continue
        cleaned = sanitize_persisted_value(entry)
        if isinstance(entry, dict) and isinstance(entry.get("linkedStorefronts"), list):
            cleaned["linkedStorefronts"] = [
                redact_persisted_url(item)
                for item in entry["linkedStorefronts"]
                if not url_has_sensitive_query(item)
            ]
        persisted[redact_persisted_url(url)] = cleaned
    return persisted


def normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:220]


def hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except ValueError:
        return ""


def same_operator_web_host(left_url: str, right_url: str) -> bool:
    """Match an operator root and its first-party subdomains, never peers."""

    left = hostname(left_url)
    right = hostname(right_url)
    left = left[4:] if left.startswith("www.") else left
    right = right[4:] if right.startswith("www.") else right
    return bool(
        left
        and right
        and (left == right or left.endswith(f".{right}") or right.endswith(f".{left}"))
    )


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
        return float(match.group(1).replace(",", ""))
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
        ("members.purebarre.com", "xponential-member-app"),
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


def conditional_cache_metadata(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Use validators only when cached candidates came from this parser build.

    A 304 response has no body to reparse.  Sending old validators after an
    extractor upgrade would therefore preserve stale or malformed candidates
    indefinitely.  Parser-version mismatch forces one full response, after
    which ordinary ETag/Last-Modified requests resume.
    """

    if not isinstance(entry, dict) or text(entry.get("parserVersion")) != PARSER_VERSION:
        return None
    return entry


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
            if method == "json-ld" and "product" in types:
                product_label = text(
                    node.get("name") or node.get("title") or node.get("label") or node.get("productName")
                ) or text(node.get("description"))
                offer_value = node.get("offers")
                offers = offer_value if isinstance(offer_value, list) else [offer_value]
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    specification_value = offer.get("priceSpecification")
                    specifications = specification_value if isinstance(specification_value, list) else [specification_value]
                    billing_nodes = [item for item in specifications if isinstance(item, dict)] or [offer]
                    for billing_node in billing_nodes:
                        amount = numeric(
                            next(
                                (
                                    billing_node.get(key)
                                    for key in ("price", "amount", "unitAmount", "priceAmount")
                                    if billing_node.get(key) is not None
                                ),
                                offer.get("price"),
                            )
                        )
                        if amount is None or amount <= 0 or amount > 2000:
                            continue
                        cadence = text(
                            billing_node.get("unitCode")
                            or billing_node.get("billingDuration")
                            or billing_node.get("billingPeriod")
                            or billing_node.get("interval")
                            or billing_node.get("frequency")
                        )
                        interval_count = numeric(
                            billing_node.get("billingIncrement")
                            or billing_node.get("billingIntervalCount")
                            or billing_node.get("intervalCount")
                        )
                        metadata = candidate_metadata(product_label, cadence)
                        eligible_quantity = billing_node.get("eligibleQuantity")
                        if isinstance(eligible_quantity, dict):
                            minimum_value = numeric(eligible_quantity.get("minValue"))
                            minimum_unit = text(eligible_quantity.get("unitCode")).casefold()
                            if minimum_value and minimum_unit in {"day", "week", "month"}:
                                minimum_days = minimum_value * {"day": 1, "week": 7, "month": 30}[minimum_unit]
                                metadata["commitment"] = {
                                    "type": "minimum-term",
                                    "minimumMonths": minimum_value if minimum_unit == "month" else None,
                                    "minimumDays": minimum_days,
                                }
                        candidate = {
                            "sourceProductId": text(
                                billing_node.get("@id")
                                or billing_node.get("id")
                                or offer.get("@id")
                                or offer.get("id")
                                or node.get("@id")
                                or node.get("id")
                                or node.get("sku")
                            ),
                            "amount": amount,
                            "currency": text(
                                billing_node.get("priceCurrency") or offer.get("priceCurrency")
                            ) or "USD",
                            "rawLabel": normalized_label(product_label),
                            "cadence": normalized_label(cadence),
                            **metadata,
                            "method": method,
                            "adapter": platform_name(source_url),
                            "evidenceTier": "official-public",
                            "exactLocationMatch": "candidate",
                            "sourceUrl": source_url,
                            "autoPublishEligible": False,
                        }
                        if interval_count is not None and interval_count > 0:
                            candidate["intervalCount"] = interval_count
                        candidates.append(candidate)
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
        amounts = [float(item.replace(",", "")) for item in MONEY_RE.findall(segment.group("body"))]
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
    exact_location_match: str = "exact-location",
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
        "exactLocationMatch": exact_location_match,
        "sourceUrl": source_url,
        "autoPublishEligible": False,
    }


def ymca_sf_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct YMCA SF's market-wide membership table and linked join fees.

    The public page renders ``Monthly Fee`` and ``join fee`` in the same card.
    A generic nearest-dollar regex therefore mistakes the enrollment charge
    for recurring dues.  This adapter requires each age/household descriptor,
    keeps the fee attached to its own plan, and excludes savings copy elsewhere
    on the page from the product catalog.
    """

    host = hostname(source_url)
    if not (host == "ymcasf.org" or host.endswith(".ymcasf.org")):
        return []
    value = " ".join(visible_text.split())
    if "Membership Types" not in value or "Monthly Fee" not in value or "join fee" not in value:
        return []
    specs = (
        ("teen", "Teen Membership", r"\bTeen\b.{0,90}?Individuals ages 13-18", "youth", ["Ages 13–18"]),
        ("young-adult", "Young Adult Membership", r"\bYoung Adult\b.{0,90}?Individuals ages 19-25", "age-restricted", ["Ages 19–25"]),
        ("adult", "Adult Membership", r"\bAdult\b.{0,90}?Individuals ages 26-66", "standard-adult", ["Published adult band is ages 26–66"]),
        ("active-older-adult", "Active Older Adult Membership", r"\bActive Older Adult\b.{0,90}?Individuals ages 67\+", "senior", ["Ages 67+"]),
        ("single-adult-household", "Single Adult Household with Children", r"\bSingle Adult Household\b.{0,30}?with Children", "household", ["One adult plus dependent children"]),
        ("dual-adult-household", "Dual Adult Household with No Children", r"\bDual Adult Household\b.{0,30}?with no Children", "household", ["Two adults in one household"]),
        ("dual-adult-household-children", "Dual Adult Household with Children", r"\bDual Adult Household\b.{0,30}?with Children", "household", ["Two adults plus dependent children"]),
    )
    candidates: list[dict[str, Any]] = []
    for product_id, name, identity_pattern, eligibility_type, restrictions in specs:
        match = re.search(
            identity_pattern
            + r".{0,260}?Monthly Fee\s*\$?\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?)"
            + r"\s*join fee\s*\$?\s*(?P<join>\d{1,4}(?:\.\d{1,2})?)",
            value,
            re.IGNORECASE,
        )
        if not match:
            continue
        amount = float(match.group("amount"))
        join_fee = float(match.group("join"))
        candidates.append(operator_visible_candidate(
            source_url,
            "ymca-sf-membership-table",
            product_id,
            name,
            amount,
            product_type="monthly",
            cadence="month",
            access_scope="YMCA of Greater San Francisco facilities",
            scope_type="multi-location",
            commitment_type="unknown",
            eligibility_type=eligibility_type,
            restrictions=restrictions,
            fees=[{
                "type": "enrollment",
                "amount": join_fee,
                "currency": "USD",
                "cadence": "one-time",
                "mandatory": True,
            }],
            raw_label=f"{name} ${amount:g}/month; ${join_fee:g} join fee",
            exact_location_match="operator-market-multi-location",
        ))
    return candidates


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
    commitment = "month-to-month" if (
        MONTH_TO_MONTH_RE.search(value)
        or re.search(r"30[ -]day cancellation", value, re.IGNORECASE)
    ) else "unknown"
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

    if host.endswith("jccsf.org") and has("Adult Full Access", "Regular Rate: $198/month", "$200 Enrollment Fee"):
        enrollment = [{
            "type": "enrollment", "amount": 200, "currency": "USD",
            "cadence": "one-time", "mandatory": True,
        }]
        return [recurring(
            "jccsf-adult-membership",
            "adult-full-access",
            "Adult Full-Access Membership",
            198,
            access_scope=(
                "Full fitness-center access including weekly group fitness, lap pool, "
                "locker rooms, sauna, and steam room"
            ),
            commitment_type="unknown",
            fees=enrollment,
            raw_label=(
                "Adult Full Access; regular rate $198/month; standard enrollment $200; "
                "current enrollment promotion shown separately"
            ),
        )]

    if host.endswith("mightypilates.com") and has("Mighty Monthly Pass", "$360/month", "$36/class"):
        return [recurring(
            "mighty-pilates",
            "mighty-monthly-ten",
            "Mighty Monthly Pass",
            360,
            allowance={"count": 10, "period": "month", "unlimited": False},
            access_scope="Ten reformer Pilates classes monthly at the Presidio Heights studio",
            best_value=True,
            raw_label="Mighty Monthly Pass $360/month by autopay; 10 classes at $36/class",
        )]

    if host.endswith("musclebeachsf.com") and has(
        "Day Pass", "$30", "Week Pass", "$70", "Monthly Individual Membership", "$125",
        "Monthly Couples Membership", "$200",
    ):
        adapter = "muscle-beach"
        return [
            operator_visible_candidate(
                source_url, adapter, "day-pass", "Day Pass", 30,
                product_type="drop-in", cadence="visit",
                allowance={"count": 1, "period": "visit", "unlimited": False},
                access_scope="One 24-hour visit",
            ),
            operator_visible_candidate(
                source_url, adapter, "week-pass", "Week Pass", 70,
                allowance={"count": None, "period": "7 days", "unlimited": True},
                access_scope="Seven-day visitor pass",
            ),
            recurring(
                adapter, "individual-monthly", "Monthly Individual Membership", 125,
                allowance={"count": None, "period": "month", "unlimited": True},
                access_scope="Unlimited gym visits at both Muscle Beach locations",
                scope_type="multi-location", minimum_months=1,
                raw_label="Monthly Individual Membership $125; unlimited visits; one-month minimum",
            ),
            recurring(
                adapter, "couples-monthly", "Monthly Couples Membership", 200,
                allowance={"count": None, "period": "month", "unlimited": True},
                access_scope="Unlimited gym visits for two people at both locations",
                scope_type="multi-location", minimum_months=1,
                eligibility_type="household", restrictions=["Two-person couples membership"],
            ),
        ]

    if host.endswith("raisethebarfitness.net") and has(
        "gym-only memberships", "without the requirement of personal training", "$150 per month",
    ):
        candidate = recurring(
            "raise-the-bar",
            "community-gym-membership",
            "Community Gym Membership",
            150,
            access_scope="Independent use of the private gym without required personal-training sessions",
            raw_label="Community gym-only membership $150/month; availability intentionally limited",
        )
        candidate["purchaseMethod"] = "contact-required"
        candidate["availability"] = "limited"
        return [candidate]
    return []


def embedded_operator_candidates(html: str, source_url: str) -> list[dict[str, Any]]:
    """Recover tightly scoped operator rate cards embedded in page state.

    Script contents remain excluded from generic extraction.  Mighty Pilates is
    an explicit exception because its official page puts the complete labeled
    membership card in an inline state payload rather than rendered text.
    """

    if not hostname(source_url).endswith("mightypilates.com"):
        return []
    embedded_text = normalized_label(unescape(re.sub(r"<[^>]+>", " ", html)))
    return independent_operator_visible_candidates(embedded_text, source_url)


CARD_PLAN_SEMANTIC_RE = re.compile(
    r"\b(?:membership|pass|class pack|drop[ -]?in|unlimited|classes? monthly|"
    r"core access|facility access|gym access|full access|all access)\b",
    re.IGNORECASE,
)
CARD_MONTHLY_PRICE_RE = re.compile(
    r"\$\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?)\s*(?:USD\s*)?"
    r"(?:/\s*(?:mo|month)|per\s+month)\b",
    re.IGNORECASE,
)
CARD_DURATION_RE = re.compile(
    r"\b(?P<count>one|three|six|twelve|\d{1,2})\s*[- ]?months?\b",
    re.IGNORECASE,
)
DUDA_PLAN_NAME_RE = re.compile(
    r"data-ai-tag=[\"']Plan\s+\d+:\s*plan\s+name[\"']",
    re.IGNORECASE,
)


def card_plan_name(prefix: str) -> str:
    """Return a stable concise product label from the text before its price."""

    value = re.sub(r"\([^)]*(?:per\s+month|months?|commitment)[^)]*\)", " ", prefix, flags=re.IGNORECASE)
    value = re.sub(r"\b\d{1,3}x(?:\s+classes?)?(?:\s+(?:per|a)\s+month)?\b.*$", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -–—:;|\ufeff")
    return value[:100]


def card_class_allowance(label: str) -> dict[str, Any] | None:
    if re.search(r"\bunlimited\b", label, re.IGNORECASE):
        return {"count": None, "period": "month", "unlimited": True}
    match = re.search(
        r"\b(?P<count>\d{1,3})x(?:\s+classes?)?\s*(?:\([^)]*\))?"
        r"(?:\s+classes?)?\s*(?:(?:per|a)\s+month)?\b",
        label,
        re.IGNORECASE,
    )
    if not match:
        match = CLASS_ALLOWANCE_RE.search(label)
    if not match:
        return None
    count = int(match.group("count") if "count" in match.groupdict() else match.group(1))
    return {"count": count, "period": "month", "unlimited": False}


def card_commitment_months(label: str) -> int | None:
    explicit = re.search(
        r"\b(?P<count>\d{1,2})\s*[- ]?(?:mo(?:nth)?s?)\s+minimum\s+commitment\b"
        r"|\b(?P<count_short>\d{1,2})\s*[- ]?(?:mo(?:nth)?s?)\s+commitment\b",
        label,
        re.IGNORECASE,
    )
    if explicit:
        return int(explicit.group("count") or explicit.group("count_short"))
    parenthetical = re.search(r"\((?P<count>\d{1,2})\s*months?\)", label, re.IGNORECASE)
    return int(parenthetical.group("count")) if parenthetical else None


def card_commitment(label: str) -> tuple[str, int | None]:
    """Return explicit recurring commitment semantics from a bounded card."""

    minimum_months = card_commitment_months(label)
    if minimum_months:
        return "minimum-term", minimum_months
    if re.search(
        r"\b(?:no\s+commitments?|month[ -]to[ -]month|cancel\s+any\s*time)\b",
        label,
        re.IGNORECASE,
    ):
        return "month-to-month", None
    return "unknown", None


def labeled_plan_card_candidates(
    cards: Iterable[str],
    source_url: str,
    adapter: str,
) -> list[dict[str, Any]]:
    """Convert semantically bounded public rate cards into complete offers."""

    candidates: list[dict[str, Any]] = []
    duration_words = {"one": 1, "three": 3, "six": 6, "twelve": 12}
    for raw_card in cards:
        card = re.sub(r"\s+", " ", unescape(raw_card)).strip()
        if not card or not CARD_PLAN_SEMANTIC_RE.search(card):
            continue
        duration = CARD_DURATION_RE.search(card)
        paid_in_full = bool(re.search(r"paid\s+in\s+full|prepaid", card, re.IGNORECASE))
        if duration and paid_in_full:
            raw_count = duration.group("count").casefold()
            months = duration_words.get(raw_count, int(raw_count) if raw_count.isdigit() else 0)
            price = re.search(r"(?:membership|pass)[^$]{0,45}\$(?P<amount>\d{1,5}(?:\.\d{1,2})?)", card, re.IGNORECASE)
            if not months or not price:
                continue
            name = card_plan_name(card[:price.start("amount") - 1])
            if not name:
                continue
            product_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
            candidate = operator_visible_candidate(
                source_url,
                adapter,
                product_id,
                name,
                float(price.group("amount")),
                product_type="monthly",
                cadence="month",
                access_scope="Access described by the bounded official prepaid plan card",
                allowance=card_class_allowance(card),
                commitment_type="prepaid",
                minimum_months=months,
                promotion=bool(PROMOTION_RE.search(card)),
                promotion_label=card if PROMOTION_RE.search(card) else "",
                raw_label=card[:500],
                exact_location_match="operator-market-catalog",
            )
            candidate["intervalCount"] = months
            candidates.append(candidate)
            continue
        recurring = CARD_MONTHLY_PRICE_RE.search(card)
        promotion = bool(PROMOTION_RE.search(card))
        if recurring:
            component_prefix = card[max(0, recurring.start() - 120):recurring.start()]
            if (
                re.search(r"\b(?:add[ -]?on|additional fee)\b", component_prefix, re.IGNORECASE)
                and not re.search(r"\bno additional fee", component_prefix, re.IGNORECASE)
            ):
                continue
            amount = float(recurring.group("amount"))
            name = card_plan_name(card[:recurring.start()])
            if not name or len(name.split()) > 12:
                continue
            commitment_type, minimum_months = card_commitment(card)
            product_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
            if minimum_months:
                product_id = f"{product_id}-{minimum_months}-month"
            candidate = operator_visible_candidate(
                source_url,
                adapter,
                product_id,
                name,
                amount,
                product_type="monthly",
                cadence="month",
                access_scope="Access described by the bounded official operator plan card",
                allowance=card_class_allowance(card),
                commitment_type=commitment_type,
                minimum_months=minimum_months,
                eligibility_type="online-only" if re.search(r"livestream\s+only", card, re.IGNORECASE) else "standard-adult",
                restrictions=["Livestream-only; no in-person access"] if re.search(r"livestream\s+only", card, re.IGNORECASE) else [],
                promotion=promotion,
                promotion_label=card if promotion else "",
                best_value=bool(re.search(r"\bbest (?:deal|value)\b|\bmost popular\b", card, re.IGNORECASE)),
                raw_label=card[:500],
                exact_location_match="operator-market-catalog",
            )
            candidates.append(candidate)
            continue

        visit = re.search(r"\b(drop[ -]?in|single class)\b[^$]{0,45}\$(?P<amount>\d{1,4}(?:\.\d{1,2})?)", card, re.IGNORECASE)
        pack = re.search(r"\b(?P<count>\d{1,3})\s*class\s+pack\b[^$]{0,45}\$(?P<amount>\d{1,5}(?:\.\d{1,2})?)", card, re.IGNORECASE)
        if visit:
            name = card_plan_name(card[:visit.start("amount") - 1]) or "Drop-In Class"
            online_only = bool(re.search(r"livestream", name, re.IGNORECASE))
            candidates.append(operator_visible_candidate(
                source_url, adapter, re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-"), name,
                float(visit.group("amount")), product_type="drop-in", cadence="visit",
                allowance={"count": 1, "period": "visit", "unlimited": False},
                eligibility_type="online-only" if online_only else "standard-adult",
                restrictions=["Livestream-only; no in-person access"] if online_only else [],
                promotion=promotion, promotion_label=card if promotion else "", raw_label=card[:500],
                exact_location_match="operator-market-catalog",
            ))
        elif pack:
            count = int(pack.group("count"))
            name = card_plan_name(card[:pack.start("amount") - 1]) or f"{count}-Class Pack"
            online_only = bool(re.search(r"livestream", name, re.IGNORECASE))
            candidates.append(operator_visible_candidate(
                source_url, adapter, re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-"), name,
                float(pack.group("amount")), allowance={"count": count, "period": "purchase", "unlimited": False},
                eligibility_type="online-only" if online_only else "standard-adult",
                restrictions=["Livestream-only; no in-person access"] if online_only else [],
                promotion=promotion, promotion_label=card if promotion else "", raw_label=card[:500],
                exact_location_match="operator-market-catalog",
            ))
    for candidate in candidates:
        candidate["sourceProductIdAuthority"] = "synthetic-label"
    return candidates


def duda_plan_cards(html: str) -> list[str]:
    """Extract Duda's explicitly labeled plan-name/price feature groups."""

    # Duda may ship the same semantic markup inside an escaped page-state
    # string.  Decode only the harmless quote/newline escapes needed to recover
    # HTML structure; never evaluate the surrounding script.
    source = unescape(html).replace(r'\"', '"').replace(r"\n", " ")
    markers = list(DUDA_PLAN_NAME_RE.finditer(source))
    cards: list[str] = []
    for index, marker in enumerate(markers):
        start = source.rfind("<", 0, marker.start())
        end = source.rfind("<", 0, markers[index + 1].start()) if index + 1 < len(markers) else len(source)
        if start < 0 or end <= start:
            continue
        parser = PageParser()
        try:
            parser.feed(source[start:end])
        except Exception:
            continue
        card = re.sub(r"\s+", " ", " ".join(parser.visible)).strip()
        if card:
            cards.append(card[:2000])
    return cards


WORDPRESS_CLASS_BOX_RE = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*\bclass-box\b[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)

WEBFLOW_SHOP_BLOCK_RE = re.compile(
    r"<(?:div|article|section)\b[^>]*class=[\"'][^\"']*\bblock-shop\b[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)


def html_fragment_text(fragment: str) -> str:
    parser = PageParser()
    try:
        parser.feed(fragment)
    except Exception:
        return ""
    return re.sub(r"\s+", " ", " ".join(parser.visible)).strip()


def webflow_shop_cards(html: str) -> list[str]:
    """Return visible text from Webflow commerce/rate cards as bounded units.

    Webflow operator sites commonly render a plan title, price, cadence, and
    description inside repeated ``block-shop`` containers.  Preserving those
    boundaries prevents a nearby add-on price from being attached to the
    selected plan and works without relying on host-specific product names.
    """

    markers = list(WEBFLOW_SHOP_BLOCK_RE.finditer(html))
    cards: list[str] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else min(len(html), marker.start() + 8000)
        card = html_fragment_text(html[marker.start():end])
        if card:
            cards.append(card[:2000])
    return cards


def wordpress_class_box_candidates(html: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct WordPress rate cards with explicit title/price sub-elements."""

    markers = list(WORDPRESS_CLASS_BOX_RE.finditer(html))
    candidates: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else min(len(html), marker.start() + 6000)
        fragment = html[marker.start():end]
        title_match = re.search(
            r"<div\b[^>]*class=[\"'][^\"']*\bclass-title\b[^\"']*[\"'][^>]*>(?P<body>.*?)</div>",
            fragment,
            re.IGNORECASE | re.DOTALL,
        )
        price_match = re.search(
            r"<div\b[^>]*class=[\"'][^\"']*\bclass-price\b[^\"']*[\"'][^>]*>(?P<body>.*?)</div>",
            fragment,
            re.IGNORECASE | re.DOTALL,
        )
        if not title_match or not price_match:
            continue
        title = html_fragment_text(title_match.group("body"))
        price_label = html_fragment_text(price_match.group("body"))
        amount_match = MONEY_RE.search(price_label)
        if not title or not amount_match or not re.search(r"\bmonthly\b", title, re.IGNORECASE):
            continue
        amount = float(amount_match.group(1).replace(",", ""))
        minimum_months = card_commitment_months(title)
        name = re.sub(
            r"\b\d{1,2}\s*[- ]?(?:mo(?:nth)?s?)\s+(?:minimum\s+)?commitment\b",
            " ",
            title,
            flags=re.IGNORECASE,
        )
        name = re.sub(r"\s+", " ", name).strip(" -–—:;")
        service = re.search(r"\bdata-service-id=[\"'](?P<id>[A-Za-z0-9_-]{1,64})[\"']", fragment, re.IGNORECASE)
        product_id = service.group("id") if service else re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        raw_label = re.sub(r"\s+", " ", f"{title} {price_label}").strip()
        is_promotion = bool(PROMOTION_RE.search(title) or re.search(r"\bspecial\b", title, re.IGNORECASE))
        candidate = operator_visible_candidate(
            source_url,
            "wordpress-class-box",
            product_id,
            name,
            amount,
            product_type="monthly",
            cadence="month",
            access_scope="Access described by the bounded official operator plan card",
            allowance=card_class_allowance(title),
            commitment_type="minimum-term" if minimum_months else "unknown",
            minimum_months=minimum_months,
            promotion=is_promotion,
            promotion_label=title if is_promotion else "",
            raw_label=raw_label,
            exact_location_match="operator-market-catalog",
        )
        candidate["sourceProductIdAuthority"] = "operator-widget"
        candidates.append(candidate)
    return candidates


ZEN_PLANNER_TOKEN_RE = re.compile(
    r"(?P<heading><strong\b[^>]*>.*?</strong>)|"
    r"(?P<card><table\b[^>]*\bid=[\"']category[\"'][^>]*>.*?</table>)",
    re.IGNORECASE | re.DOTALL,
)
ZEN_PLANNER_PRODUCT_ID_RE = re.compile(
    r"\bMembershipTemplateId=(?P<id>[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12})\b",
    re.IGNORECASE,
)
ZEN_PLANNER_TITLE_RE = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*\bbold\b[^\"']*[\"'][^>]*>(?P<body>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
ZEN_PLANNER_SUBTEXT_RE = re.compile(
    r"<div\b[^>]*class=[\"'][^\"']*\bsubtext\b[^\"']*[\"'][^>]*>(?P<body>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
ZEN_PLANNER_PRICE_RE = re.compile(
    r"\(\s*\$\s*(?P<amount>\d{1,6}(?:,\d{3})*(?:\.\d{1,2})?)\s*\)\s*$",
    re.IGNORECASE,
)


def zen_planner_html_candidates(html: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct public Zen Planner membership cards without entering checkout."""

    if platform_name(source_url) != "zen-planner":
        return []
    current_category = ""
    candidates: list[dict[str, Any]] = []
    for token in ZEN_PLANNER_TOKEN_RE.finditer(html):
        heading = token.group("heading")
        if heading:
            current_category = html_fragment_text(heading)
            continue
        card = token.group("card") or ""
        product = ZEN_PLANNER_PRODUCT_ID_RE.search(card)
        title_match = ZEN_PLANNER_TITLE_RE.search(card)
        if not product or not title_match:
            continue
        title = html_fragment_text(title_match.group("body"))
        price = ZEN_PLANNER_PRICE_RE.search(title)
        if not price:
            continue
        amount = float(price.group("amount").replace(",", ""))
        if amount <= 0 or amount > 20_000:
            continue
        name = ZEN_PLANNER_PRICE_RE.sub("", title).strip(" -–—:;")
        subtext_match = ZEN_PLANNER_SUBTEXT_RE.search(card)
        subtext = html_fragment_text(subtext_match.group("body")) if subtext_match else ""
        semantic_text = " ".join(filter(None, (current_category, name, subtext)))
        lowered = semantic_text.casefold()
        is_drop_in = bool(re.search(r"\b(?:drop[ -]?in|day pass|single class)\b", lowered))
        class_pack = re.search(r"\b(?P<count>\d{1,3})\s*class(?:es)?\s+pass\b", lowered)
        duration = re.search(r"\b(?P<count>\d{1,2})\s*months?\s+prepaid\b", lowered)
        pif_duration = re.search(r"\bpif\s+(?P<count>\d{1,2})\s*mo(?:nths?)?\b", lowered)
        year_duration = re.search(r"\b(?P<count>\d{1,2})\s+years?(?:\s+prepaid)?\b", lowered)
        one_year_prepaid = bool(re.search(r"\bone\s+year\s+prepaid\b", lowered))
        one_month_pass = bool(re.search(r"\bone month pass\b", lowered))
        one_week_pass = bool(re.search(r"\bone week pass\b|\b1 week pass\b", lowered))
        allowance_match = re.search(
            r"\b(?P<count>\d{1,3})\s*classes?\s+(?:a|per)\s+(?P<period>week|month)\b",
            semantic_text,
            re.IGNORECASE,
        ) or CLASS_ALLOWANCE_RE.search(semantic_text)
        if is_drop_in:
            allowance = {"count": 1.0, "period": "visit", "unlimited": False}
        elif class_pack:
            allowance = {
                "count": float(class_pack.group("count")),
                "period": "purchase",
                "unlimited": False,
            }
        elif "unlimited" in lowered:
            allowance: dict[str, Any] | None = {
                "count": None,
                "period": "month",
                "unlimited": True,
            }
        elif allowance_match:
            allowance = {
                "count": float(allowance_match.group("count") if "count" in allowance_match.groupdict() else allowance_match.group(1)),
                "period": (
                    allowance_match.group("period")
                    if "period" in allowance_match.groupdict()
                    else allowance_match.group(2)
                    or "month"
                ).casefold(),
                "unlimited": False,
            }
        else:
            allowance = None

        prepaid_months = (
            int(duration.group("count")) if duration
            else int(pif_duration.group("count")) if pif_duration
            else int(year_duration.group("count")) * 12 if year_duration
            else 12 if one_year_prepaid
            else 1 if one_month_pass
            else 0
        )
        recurring = bool(
            not prepaid_months
            and not one_week_pass
            and not is_drop_in
            and not class_pack
            and re.search(r"\b(?:membership|monthly|classes? (?:a|per) week)\b", lowered)
        )
        if is_drop_in:
            product_type = "drop-in"
            cadence = "visit"
            interval_count = 1
            commitment = {"type": "none", "minimumMonths": None}
        elif class_pack or one_week_pass:
            product_type = "class-pack"
            cadence = "one-time"
            interval_count = 1
            commitment = {"type": "none", "minimumMonths": None}
        elif prepaid_months:
            product_type = "monthly"
            cadence = "month"
            interval_count = prepaid_months
            commitment = {"type": "prepaid", "minimumMonths": prepaid_months}
        elif recurring:
            product_type = "monthly"
            cadence = "month"
            interval_count = 1
            commitment = {"type": "unknown", "minimumMonths": None}
        else:
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        aliases = [slug] if slug else []
        if re.search(r"\(\s*monthly\s*\)", name, re.IGNORECASE):
            aliases.append("monthly")
        raw_label = " — ".join(filter(None, (current_category, name, subtext, f"${amount:g}")))
        candidates.append({
            "sourceProductId": product.group("id").upper(),
            "sourceProductAliases": list(dict.fromkeys(aliases)),
            "sourceProductIdAuthority": "operator-widget",
            "name": name,
            "amount": amount,
            "currency": "USD",
            "rawLabel": raw_label[:500],
            "cadence": cadence,
            "billingInterval": cadence,
            "intervalCount": interval_count,
            "productType": product_type,
            "accessScope": subtext or current_category,
            "scopeType": "single-location",
            "classAllowance": allowance,
            "promotion": {
                "isPromotion": bool(PROMOTION_RE.search(semantic_text)),
                "label": semantic_text if PROMOTION_RE.search(semantic_text) else "",
            },
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": commitment,
            "fees": [],
            "bestValueLabel": bool(re.search(r"\bbest value\b|\bmost popular\b", semantic_text, re.IGNORECASE)),
            "method": "public-zen-planner-plan-card",
            "adapter": "zen-planner",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-operator-catalog",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return deduplicate_candidates(candidates)


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


def solidcore_visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Parse solidcore's public month-to-month tab without mixing term discounts.

    The page defaults to a 12-month discount and changes the same cards when
    the public ``Monthly`` tab is activated.  Candidate identity therefore
    comes from the visible plan title, allowance and attached ``/mo`` amount,
    never from a page-wide dollar regex.
    """

    if not hostname(source_url).endswith("solidcore.co"):
        return []
    compact = " ".join(visible_text.split())
    # The 12-month and month-to-month tabs reuse nearly identical card names.
    # Require the neutral tab's complete plan vocabulary before interpreting
    # any amount as month-to-month; otherwise ``Travel Monthly Unlimited`` on
    # the default committed tab can look like an ordinary monthly plan.
    neutral_plan_markers = (
        "monthly 4 membership",
        "monthly 8 membership",
        "monthly unlimited membership",
    )
    if not all(marker in compact.casefold() for marker in neutral_plan_markers):
        return []
    plan_re = re.compile(
        r"\b(?P<name>Monthly\s+(?:(?P<count>4|8)\s+Membership|Unlimited\s+Membership)"
        r"|Travel\s+Monthly\s+Unlimited)\s+\$\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?)\s*/\s*mo\b",
        re.IGNORECASE,
    )
    candidates: list[dict[str, Any]] = []
    for match in plan_re.finditer(compact):
        name = normalized_label(match.group("name"))
        count = numeric(match.group("count"))
        unlimited = "unlimited" in name.casefold()
        candidates.append(
            {
                "sourceProductId": "",
                "amount": float(match.group("amount")),
                "currency": "USD",
                "rawLabel": name,
                "cadence": "month",
                "productType": "monthly",
                "classAllowance": {
                    "count": count,
                    "period": "month",
                    "unlimited": unlimited,
                },
                "promotion": {"isPromotion": False, "label": ""},
                "eligibility": {"type": "standard-adult", "restrictions": []},
                "commitment": {"type": "month-to-month", "minimumMonths": None},
                "method": "visible-plan-cards",
                "adapter": "solidcore",
                "evidenceTier": "official-public",
                "exactLocationMatch": "candidate",
                "sourceUrl": source_url,
                "autoPublishEligible": False,
            }
        )
    return candidates


ALLOWANCE_MONTHLY_CARD_RE = re.compile(
    r"\b(?P<count>\d{1,3})\s*(?:x|class(?:es)?|times?)\s*(?:/|per|a)\s*month\s*:?\s*"
    r"\$\s*(?P<amount>\d{1,4}(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)


def allowance_monthly_card_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Pair compact recurring allowance labels with their adjacent amounts."""

    compact = " ".join(visible_text.split())
    candidates: list[dict[str, Any]] = []
    for match in ALLOWANCE_MONTHLY_CARD_RE.finditer(compact):
        count = int(match.group("count"))
        amount = float(match.group("amount"))
        if count <= 0 or count > 100 or amount <= 0 or amount > 2000:
            continue
        # Promotion words must be attached to this compact card, not a later
        # intro product in the page-wide flattened text.
        local = compact[max(0, match.start() - 45):min(len(compact), match.end() + 20)]
        term_context = compact[max(0, match.start() - 260):min(len(compact), match.end() + 80)]
        minimum_term = re.search(
            r"\b(?P<months>\d{1,2})[ -]months?\s+(?:minimum|commitment)\b"
            r"|\b(?:minimum|commitment)(?:\s+of)?\s+(?P<minimum_months>\d{1,2})[ -]months?\b",
            term_context,
            re.IGNORECASE,
        )
        minimum_months = int(minimum_term.group("months") or minimum_term.group("minimum_months")) if minimum_term else None
        name = f"{count}x Monthly"
        raw_label = f"{name} ${amount:g}/month"
        is_promotion = bool(PROMOTION_RE.search(local))
        candidates.append({
            "sourceProductId": "",
            "name": name,
            "amount": amount,
            "currency": "USD",
            "rawLabel": raw_label,
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "classAllowance": {"count": count, "period": "month", "unlimited": False},
            "promotion": {"isPromotion": is_promotion, "label": local if is_promotion else ""},
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {
                "type": "minimum-term" if minimum_months else "month-to-month" if MONTH_TO_MONTH_RE.search(compact) else "unknown",
                "minimumMonths": minimum_months,
            },
            "fees": [],
            "bestValueLabel": bool(re.search(r"\bbest value\b", compact[max(0, match.start() - 80):match.start()], re.IGNORECASE)),
            "method": "visible-allowance-plan-card",
            "adapter": "allowance-plan-cards",
            "evidenceTier": "official-public",
            "exactLocationMatch": "candidate",
            "sourceUrl": source_url,
            "contentHash": hashlib.sha256(raw_label.encode("utf-8")).hexdigest(),
            "autoPublishEligible": False,
        })
    return candidates


MEMBERSHIP_AGREEMENT_PLAN_RE = re.compile(
    r"(?P<name>"
    r"(?:Recurring|Ongoing|Standard|Monthly)[A-Za-z0-9 ()/&'’\-]{0,70}(?:Membership|Plan)"
    r"(?:\s*\([^)]{0,50}\))?"
    r"|(?:1|One|Single)\s+(?:Day|Week|Month)\s+Pass"
    r")\s*:?\s*Sign-up Fee\s*:",
    re.IGNORECASE,
)


def membership_agreement_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct labeled plans from a public membership-agreement rate table.

    Some independent gyms publish their only complete catalog inside a legal
    agreement rather than a pricing page.  This adapter activates only when
    the table exposes all four structural labels and then bounds every amount
    to a named plan.  Operational clauses such as freeze or late fees are
    deliberately outside those plan boundaries and cannot become dues.
    """

    value = re.sub(r"\s+", " ", visible_text).strip()
    required = ("membership types", "sign-up fee", "cost", "description")
    if not all(label in value.casefold() for label in required):
        return []
    markers = list(MEMBERSHIP_AGREEMENT_PLAN_RE.finditer(value))
    candidates: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        segment = value[marker.start():end]
        cost = re.search(
            r"\bCost\s*:\s*\$\s*(?P<amount>\d{1,5}(?:\.\d{1,2})?)"
            r"(?P<tail>.{0,80})",
            segment,
            re.IGNORECASE,
        )
        if not cost:
            continue
        amount = float(cost.group("amount"))
        if amount <= 0 or amount > 20_000:
            continue
        raw_name = re.sub(r"\s+", " ", marker.group("name")).strip(" -–—:;")
        lowered_name = raw_name.casefold()
        fee_match = re.search(
            r"Sign-up Fee\s*:\s*(?P<fee>None|\$\s*\d{1,5}(?:\.\d{1,2})?)",
            segment,
            re.IGNORECASE,
        )
        fees: list[dict[str, Any]] = []
        if fee_match and fee_match.group("fee").casefold() != "none":
            fee_amount = float(fee_match.group("fee").replace("$", "").strip())
            fees.append({
                "type": "enrollment",
                "amount": fee_amount,
                "currency": "USD",
                "cadence": "one-time",
                "mandatory": True,
            })
        description_match = re.search(
            r"\bDescription\s*:\s*(?P<description>.{1,500})",
            segment,
            re.IGNORECASE,
        )
        description = normalized_label(description_match.group("description")) if description_match else ""
        is_day_pass = bool(re.search(r"\b(?:1|one)\s+day\s+pass\b", lowered_name))
        is_pass = "pass" in lowered_name
        four_week = bool(re.search(r"\b4[ -]?week\b|\bfour[ -]?week\b", segment, re.IGNORECASE))
        recurring = not is_pass and bool(
            re.search(r"\b(?:recurring|ongoing|monthly|membership)\b", lowered_name)
            and re.search(r"\b(?:recurring|no long[ -]?term commitment|per (?:month|4[ -]?week period))\b", segment, re.IGNORECASE)
        )
        if is_day_pass:
            product_id = "day-pass"
            name = "One-Day Pass"
            product_type = "drop-in"
            cadence = "visit"
            commitment_type = "none"
            allowance = {"count": 1, "period": "visit", "unlimited": False}
        elif is_pass:
            product_id = re.sub(r"[^a-z0-9]+", "-", lowered_name).strip("-")
            name = raw_name
            product_type = "class-pack"
            cadence = "one-time"
            commitment_type = "none"
            allowance = None
        elif recurring:
            product_id = "recurring-four-week" if four_week else re.sub(r"[^a-z0-9]+", "-", lowered_name).strip("-")
            name = "Recurring Four-Week Membership" if four_week else raw_name
            product_type = "monthly"
            cadence = "4 weeks" if four_week else "month"
            commitment_type = (
                "month-to-month"
                if re.search(r"\bno long[ -]?term commitment\b|\bcancel(?:lation)? fee\s*:\s*none\b", segment, re.IGNORECASE)
                else "unknown"
            )
            allowance = None
        else:
            continue
        candidate = operator_visible_candidate(
            source_url,
            "membership-agreement",
            product_id,
            name,
            amount,
            product_type=product_type,
            cadence=cadence,
            access_scope=description or "Access described by the named membership-agreement plan",
            allowance=allowance,
            commitment_type=commitment_type,
            fees=fees,
            raw_label=segment[:500],
        )
        candidate["sourceProductIdAuthority"] = "synthetic-label"
        candidates.append(candidate)
    return candidates


def visible_candidates(visible_text: str, source_url: str) -> list[dict[str, Any]]:
    specialized = (
        crunch_visible_candidates(visible_text, source_url)
        or twenty_four_hour_visible_candidates(visible_text, source_url)
        or equinox_visible_candidates(visible_text, source_url)
        or planet_fitness_visible_candidates(visible_text, source_url)
        or ymca_sf_visible_candidates(visible_text, source_url)
        or orangetheory_visible_candidates(visible_text, source_url)
        or approach_visible_candidates(visible_text, source_url)
        or membership_agreement_candidates(visible_text, source_url)
        or independent_operator_visible_candidates(visible_text, source_url)
        or perform_for_golf_plan_descriptors(visible_text, source_url)
        or solidcore_visible_candidates(visible_text, source_url)
        or allowance_monthly_card_candidates(visible_text, source_url)
    )
    candidates: list[dict[str, Any]] = list(specialized)
    candidates.extend(visible_cost_context_candidates(visible_text, source_url))
    requires_complete_card_adapter = hostname(source_url).endswith("orangetheory.com")
    complete_visible_catalog = any(
        text(candidate.get("adapter")) == "membership-agreement"
        for candidate in specialized
    )
    patterns = () if complete_visible_catalog else (
        (("drop-in", DROP_IN_AFTER_RE), ("drop-in", DROP_IN_BEFORE_RE))
        if specialized or requires_complete_card_adapter
        else (("monthly", MONTHLY_RE), ("drop-in", DROP_IN_AFTER_RE), ("drop-in", DROP_IN_BEFORE_RE))
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(visible_text):
            amount = float(match.group("amount").replace(",", ""))
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
                amount = float(match.group(1).replace(",", ""))
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
            name = normalized_label(text(product.get("name")))
            if platform_adapters.GIFT_CARD_RE.search(name):
                continue
            trainer_required = platform_adapters.TRAINER_REQUIRED_RE.search(name)
            special_class = platform_adapters.SPECIAL_CLASS_RE.search(name)
            if product_kind == "memberships":
                product_type = "monthly" if interval_code in {"MO", "WK", "YR"} else "offer"
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
                product_type = (
                    "drop-in" if credit_quantity == 1
                    else "class-pack" if credit_quantity is not None and credit_quantity > 1
                    else "offer"
                )
                allowance = (
                    {
                        "count": credit_quantity,
                        "period": "visit" if product_type == "drop-in" else "purchase",
                    }
                    if credit_quantity is not None else None
                )
                cadence = "visit" if product_type == "drop-in" else "one-time"
                commitment = {"type": "none", "minimumMonths": None}
            if is_intro:
                eligibility_type = "new-client"
                restrictions = [user_segment or "intro offer"]
            elif trainer_required:
                eligibility_type = "trainer-required"
                restrictions = ["Trainer-required service"]
            elif special_class:
                eligibility_type = "special-class"
                restrictions = ["Special-purpose class"]
            elif user_segment not in {"", "everyone"}:
                eligibility_type = "restricted"
                restrictions = [user_segment]
            else:
                eligibility_type = "standard-adult"
                restrictions = []
            candidates.append({
                "sourceProductId": text(product.get("id")),
                "sourceProductIdAuthority": "operator-widget",
                "amount": amount,
                "currency": text(product.get("currency_code")) or "USD",
                "rawLabel": name,
                "cadence": cadence,
                "intervalCount": interval_length,
                "productType": product_type,
                "classAllowance": allowance,
                "promotion": {"isPromotion": is_intro, "label": name if is_intro else ""},
                "eligibility": {
                    "type": eligibility_type,
                    "restrictions": restrictions,
                },
                "commitment": commitment,
                "locations": [text(value) for value in product.get("locations", [])],
                "ordinaryUse": not is_intro and not trainer_required and not special_class,
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
            "name": name,
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
    domain_match = re.search(
        r"data-endpoint-domain\s*=\s*['\"]"
        r"(https://members\.(?:clubpilates|purebarre|stretchlab)\.com)['\"]",
        html,
        re.IGNORECASE,
    )
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
    for charge in payload.get("clubFees", []):
        if not isinstance(charge, dict) or charge.get("feeApply") is False:
            continue
        label = text(charge.get("feeName"))
        charge_amount = numeric(charge.get("feeAmount"))
        if charge_amount is None or charge_amount <= 0 or not re.search(r"\bfee\b", label, re.IGNORECASE):
            continue
        label_lower = label.casefold()
        fee_type = next(
            (value for token, value in (
                ("annual", "annual"), ("enrollment", "enrollment"),
                ("initiation", "initiation"), ("processing", "processing"),
            ) if token in label_lower),
            "other",
        )
        fees.append({
            "type": fee_type,
            "name": label,
            "amount": charge_amount,
            "currency": "USD",
            "cadence": "year" if charge.get("feeRecurring") is True and fee_type == "annual" else "one-time",
            "mandatory": True,
        })
    cadence = "month" if "month" in text(payload.get("renewalFrequency")).casefold() else "unknown"
    agreement_term = text(payload.get("agreementTerm"))
    open_agreement = agreement_term.casefold() in {"open", "month-to-month", "month to month"}
    term_months = None if open_agreement else numeric(payload.get("termInMonths"))
    alias_label = re.sub(r"\b\d+\s+months?\s+term\b", "", name, flags=re.IGNORECASE)
    source_product_alias = re.sub(r"[^a-z0-9]+", "-", alias_label.casefold()).strip("-")
    candidate = {
        "sourceProductId": plan_id,
        "sourceProductAliases": [source_product_alias] if source_product_alias else [],
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
            "type": "month-to-month" if open_agreement else ("fixed-term" if term_months and term_months > 0 else "unknown"),
            "minimumMonths": term_months if term_months and term_months > 0 else None,
            "rawLabel": agreement_term,
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


EQUINOX_MEMBERSHIP_API_RE = re.compile(
    r"^/api/cms/facilities/(?P<facility_id>\d{1,8})/membership/plans/?$",
    re.IGNORECASE,
)


def is_equinox_membership_api_url(source_url: str) -> bool:
    """Allow only Equinox's documented, same-origin public plan endpoint."""

    parsed = urlparse(text(source_url))
    host = parsed.netloc.casefold()
    return bool(
        parsed.scheme.casefold() == "https"
        and (host == "equinox.com" or host.endswith(".equinox.com"))
        and EQUINOX_MEMBERSHIP_API_RE.fullmatch(parsed.path)
    )


def equinox_membership_catalog_routes(hydration_blocks: Iterable[str], source_url: str) -> list[str]:
    """Recover one exact-club plan API route from Equinox Next.js state.

    Club pages also embed a global appointment locator containing hundreds of
    other facility IDs.  Only the page's authoritative club/facility fields or
    its dynamic route parameter are accepted so a location crawl cannot fan
    out into unrelated branches.
    """

    parsed_source = urlparse(text(source_url))
    host = parsed_source.netloc.casefold()
    if not (
        parsed_source.scheme.casefold() == "https"
        and (host == "equinox.com" or host.endswith(".equinox.com"))
        and not is_equinox_membership_api_url(source_url)
    ):
        return []

    for block in hydration_blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        page_props = payload.get("props", {}).get("pageProps", {})
        if not isinstance(page_props, dict):
            page_props = {}
        club = page_props.get("club", {})
        facility = page_props.get("facility", {})
        club_details = page_props.get("clubDetails", {})
        query = payload.get("query", {})
        candidates = (
            club.get("fields", {}).get("clubData", {}).get("fields", {}).get("facilityId")
            if isinstance(club, dict) else None,
            facility.get("facilityId") if isinstance(facility, dict) else None,
            club_details.get("facilityId") if isinstance(club_details, dict) else None,
            query.get("facilityId") if isinstance(query, dict) else None,
        )
        facility_id = next(
            (text(value) for value in candidates if re.fullmatch(r"\d{1,8}", text(value))),
            "",
        )
        if facility_id:
            return [
                f"{parsed_source.scheme}://{parsed_source.netloc}"
                f"/api/cms/facilities/{facility_id}/membership/plans"
            ]
    return []


def equinox_membership_catalog_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct exact recurring plans from Equinox's public club API."""

    if not isinstance(payload, dict) or not is_equinox_membership_api_url(source_url):
        return []
    facility_status = text(payload.get("facilityStatus")).casefold()
    is_presale = payload.get("isPresale") is True
    if facility_status and facility_status not in {"open", "presale", "coming soon"}:
        return []
    country = text(payload.get("country")).upper()
    currency = {"US": "USD", "CA": "CAD", "GB": "GBP"}.get(country, "USD")
    club_name = text(payload.get("clubName"))
    candidates: list[dict[str, Any]] = []
    for plan in payload.get("result", []):
        if not isinstance(plan, dict):
            continue
        properties = plan.get("planProperties", {})
        if not isinstance(properties, dict):
            continue
        amount = numeric(properties.get("monthlyFee"))
        product_id = text(plan.get("id") or plan.get("planId"))
        name = text(plan.get("planType") or plan.get("friendlyName") or plan.get("name"))
        if not product_id or not name or amount is None or amount <= 0:
            continue
        access_scope = text(
            plan.get("planDescription")
            or plan.get("description")
            or plan.get("membershipModuleDescription")
        )
        promotion = plan.get("promotion", {})
        if not isinstance(promotion, dict):
            promotion = {}
        promotion_label = text(promotion.get("description") or promotion.get("name"))
        fees: list[dict[str, Any]] = []
        initiation = properties.get("initiation", {})
        initiation_amount = numeric(initiation.get("totalDues")) if isinstance(initiation, dict) else numeric(initiation)
        if initiation_amount is not None and initiation_amount > 0:
            fees.append({
                "type": "initiation",
                "name": "Initiation Fee",
                "amount": initiation_amount,
                "currency": currency,
                "cadence": "one-time",
                "mandatory": True,
            })
        raw_parts = [f"{name} ${amount:g}/month"]
        if club_name:
            raw_parts.append(club_name)
        if fees:
            raw_parts.append(f"standard initiation ${fees[0]['amount']:g}")
        if promotion_label:
            raw_parts.append(f"current offer: {promotion_label}")
        candidates.append({
            "sourceProductId": product_id,
            "name": name,
            "amount": amount,
            "currency": currency,
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "accessScope": access_scope or ("Named home location" if name.casefold() == "select" else name),
            "scopeType": "single-location" if name.casefold() == "select" else "multi-location",
            "classAllowance": None,
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {"type": "unknown", "minimumMonths": None, "rawLabel": "Not disclosed by plan API"},
            # A temporary initiation discount or spa credit does not make the
            # ordinary recurring monthly dues promotional.
            "promotion": {
                "isPromotion": is_presale,
                "label": promotion_label if is_presale else "",
                "context": promotion_label,
            },
            "fees": fees,
            "bestValueLabel": False,
            "rawLabel": "; ".join(raw_parts)[:500],
            "method": "public-equinox-membership-api",
            "adapter": "equinox",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-location",
            "sourceUrl": source_url,
            "autoPublishEligible": False,
        })
    return candidates


BAY_CLUB_SHARED_PRODUCTS_RE = re.compile(
    r"^/api/1\.0/products/shared/(?P<club_code>[A-Z0-9]{2,12})/?$",
    re.IGNORECASE,
)
BAY_CLUB_CALCULATE_PATH = "/api/1.0/pricing/shared/calculate"


def is_bay_club_api_url(source_url: str) -> bool:
    """Allow only the public catalog routes used by Bay Club's join builder."""

    parsed = urlparse(text(source_url))
    if parsed.scheme.casefold() != "https" or parsed.netloc.casefold() != BAY_CLUB_API_HOST:
        return False
    path = parsed.path.rstrip("/") or "/"
    if path.casefold() == "/api/1.0/clubs":
        return not parsed.query
    if BAY_CLUB_SHARED_PRODUCTS_RE.fullmatch(path):
        return not parsed.query
    if path.casefold() != BAY_CLUB_CALCULATE_PATH:
        return False
    query = parse_qs(parsed.query)
    return bool(
        set(query) == {"clubCode"}
        and len(query["clubCode"]) == 1
        and re.fullmatch(r"[A-Z0-9]{2,12}", query["clubCode"][0], re.IGNORECASE)
    )


def is_bay_club_pricing_calculation_url(source_url: str) -> bool:
    parsed = urlparse(text(source_url))
    return bool(
        is_bay_club_api_url(source_url)
        and parsed.path.rstrip("/").casefold() == BAY_CLUB_CALCULATE_PATH
    )


def bay_club_catalog_routes(source_url: str, gym: dict[str, Any] | None) -> list[str]:
    """Enter the public catalog only from Bay Club's reviewed join builder."""

    if not isinstance(gym, dict) or text(gym.get("operatorId") or gym.get("operatorKey")) != "bay-club":
        return []
    parsed = urlparse(text(source_url))
    if (
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() == BAY_CLUB_BUILDER_HOST
        and parsed.path.rstrip("/").casefold() == BAY_CLUB_BUILDER_PATH
    ):
        return [BAY_CLUB_CLUBS_URL]
    return []


def bay_club_club_code_from_url(source_url: str) -> str:
    parsed = urlparse(text(source_url))
    shared_match = BAY_CLUB_SHARED_PRODUCTS_RE.fullmatch(parsed.path.rstrip("/"))
    if shared_match:
        return shared_match.group("club_code").upper()
    if parsed.path.rstrip("/").casefold() == BAY_CLUB_CALCULATE_PATH:
        values = parse_qs(parsed.query).get("clubCode", [])
        if len(values) == 1 and re.fullmatch(r"[A-Z0-9]{2,12}", values[0], re.IGNORECASE):
            return values[0].upper()
    return ""


def bay_club_legacy_product_alias(club_code: str, product_name: str) -> str:
    """Retain compatibility with reviewed pre-API product identifiers."""

    short_names = {
        "single site": "single-site",
        "executive club north bay": "executive-north",
        "executive club south bay": "executive-south",
        "club west gold": "club-west-gold",
    }
    normalized_name = " ".join(text(product_name).casefold().split())
    slug = short_names.get(normalized_name)
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", normalized_name).strip("-")
    return f"{club_code.casefold()}-{slug}-monthly" if club_code and slug else ""


def bay_club_public_api_candidates(
    payload: Any,
    source_url: str,
    gym: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reconstruct Bay Club's exact public, standard-adult monthly catalog.

    The official builder first loads a public club list, then a shared product
    catalog, and finally calls a price-only calculator. The calculator request
    contains no contact or account data and this adapter never creates a cart.
    """

    if not isinstance(payload, dict) or not is_bay_club_api_url(source_url):
        return [], []
    parsed = urlparse(source_url)
    path = parsed.path.rstrip("/") or "/"
    club_code = bay_club_club_code_from_url(source_url)
    if path.casefold() == "/api/1.0/clubs":
        gym_name = text((gym or {}).get("name")).casefold()
        operator_location_id = text((gym or {}).get("operatorLocationId")).casefold()
        matching = [
            club for club in payload.get("clubs", [])
            if isinstance(club, dict)
            and (
                (gym_name and text(club.get("name")).casefold() == gym_name)
                or (operator_location_id and text(club.get("code")).casefold() == operator_location_id)
            )
        ]
        if len(matching) != 1:
            return [], []
        club_code = text(matching[0].get("code")).upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", club_code):
            return [], []
        return [], [
            f"{BAY_CLUB_API_BASE}/products/shared/{club_code}",
            f"{BAY_CLUB_API_BASE}/pricing/shared/calculate?clubCode={club_code}",
        ]
    if BAY_CLUB_SHARED_PRODUCTS_RE.fullmatch(path):
        return [], [f"{BAY_CLUB_API_BASE}/pricing/shared/calculate?clubCode={club_code}"]
    if path.casefold() != BAY_CLUB_CALCULATE_PATH or not club_code:
        return [], []

    candidates: list[dict[str, Any]] = []
    for calculation in payload.get("productsCalculations", []):
        if not isinstance(calculation, dict):
            continue
        product = calculation.get("product", {})
        if not isinstance(product, dict):
            continue
        product_id = text(product.get("productId"))
        name = text(product.get("name"))
        amount = numeric(product.get("monthlyDues"))
        if not product_id or not name or amount is None or amount <= 0 or product.get("monthlyAllowed") is False:
            continue
        accessible_clubs = [
            item for item in product.get("accesibleClubs", []) if isinstance(item, dict)
        ]
        accessible_codes = list(dict.fromkeys(
            text(item.get("code")).upper() for item in accessible_clubs if text(item.get("code"))
        ))
        accessible_names = list(dict.fromkeys(
            text(item.get("name")) for item in accessible_clubs if text(item.get("name"))
        ))
        # The named home location must actually be available under the plan.
        if club_code not in accessible_codes:
            continue
        regular_initiation = numeric(product.get("initiationFee"))
        promotion_initiation = numeric(product.get("promotionInitiationFee"))
        current_initiation = (
            promotion_initiation if promotion_initiation is not None else regular_initiation
        )
        promotion_context = text((calculation.get("productCost") or {}).get("promoDescription"))
        fees: list[dict[str, Any]] = []
        if current_initiation is not None and current_initiation >= 0:
            fee: dict[str, Any] = {
                "type": "initiation",
                "name": "Initiation Fee",
                "amount": current_initiation,
                "currency": "USD",
                "cadence": "one-time",
                "mandatory": current_initiation > 0,
            }
            if (
                regular_initiation is not None
                and promotion_initiation is not None
                and promotion_initiation < regular_initiation
            ):
                fee.update({
                    "standardAmount": regular_initiation,
                    "promotionApplied": True,
                    "promotionLabel": promotion_context,
                })
            fees.append(fee)
        source_product_aliases = list(dict.fromkeys(filter(None, (
            text(product.get("code")),
            bay_club_legacy_product_alias(club_code, name),
        ))))
        raw_parts = [f"{name} ${amount:g}/month", f"home club {club_code}"]
        if current_initiation is not None:
            raw_parts.append(f"current initiation ${current_initiation:g}")
        if regular_initiation is not None and promotion_initiation is not None and promotion_initiation < regular_initiation:
            raw_parts.append(f"standard initiation ${regular_initiation:g}")
        raw_label = "; ".join(raw_parts)
        candidates.append({
            "sourceProductId": product_id,
            "sourceProductAliases": source_product_aliases,
            "operatorLocationId": club_code,
            "name": name,
            "amount": amount,
            "currency": "USD",
            "cadence": "month",
            "billingInterval": "month",
            "productType": "monthly",
            "accessScope": text(product.get("longDescription")) or ", ".join(accessible_names),
            "scopeType": "single-location" if len(set(accessible_codes)) == 1 else "multi-location",
            "accessibleLocationIds": accessible_codes,
            "classAllowance": None,
            "eligibility": {"type": "standard-adult", "restrictions": []},
            "commitment": {
                "type": "month-to-month" if calculation.get("isMonthToMonthEnabled") else "unknown",
                "minimumMonths": None,
                "rawLabel": "Monthly billing available" if calculation.get("isMonthToMonthEnabled") else "Not disclosed",
            },
            # Monthly dues remain the ordinary price even when a current
            # initiation-fee promotion is shown separately.
            "promotion": {
                "isPromotion": False,
                "label": "",
                "context": promotion_context,
            },
            "fees": fees,
            "bestValueLabel": bool(product.get("isMostPopular")),
            "rawLabel": raw_label,
            "method": "public-bay-club-membership-calculator",
            "adapter": "bay-club-public-api",
            "evidenceTier": "official-public",
            "exactLocationMatch": "exact-location",
            "sourceUrl": source_url,
            "contentHash": hashlib.sha256(raw_label.encode("utf-8")).hexdigest(),
            "autoPublishEligible": False,
        })
    return candidates, []


def may_follow_nested_catalog(source_url: str, depth: int) -> bool:
    """Permit only documented public catalog-to-detail hops past the base frontier."""

    if depth < MAX_LINK_DEPTH:
        return True
    parsed = urlparse(source_url)
    return bool(
        depth == MAX_LINK_DEPTH
        and platform_name(source_url) == "abc-fitness"
        and parsed.path.casefold().endswith("/api/online-join/signup/planlist")
    )


def is_soulcycle_series_api_url(source_url: str) -> bool:
    """Allow only SoulCycle's public, read-only regional series endpoint."""

    parsed = urlparse(text(source_url))
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() in SOULCYCLE_HOSTS
        and SOULCYCLE_SERIES_API_RE.fullmatch(parsed.path)
        and parse_qs(parsed.query).get("active-menu") == ["cycle"]
    )


def soulcycle_market_label(gym: dict[str, Any] | None) -> str:
    """Derive the reviewed operator market from canonical location metadata."""

    if not isinstance(gym, dict):
        return ""
    explicit = text(gym.get("market") or gym.get("city"))
    if explicit:
        return explicit
    identity_text = " ".join(text(gym.get(key)) for key in ("name", "address", "canonicalAddress"))
    if re.search(r"\bSan Francisco\b", identity_text, re.IGNORECASE):
        return "San Francisco"
    return ""


def html_attribute(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(['\"])(?P<value>.*?)\1",
        tag,
        re.IGNORECASE | re.DOTALL,
    )
    return unescape(match.group("value")) if match else ""


def soulcycle_series_catalog_routes(
    html: str,
    source_url: str,
    gym: dict[str, Any] | None,
) -> list[str]:
    """Resolve one exact-market SoulCycle catalog route from public HTML."""

    parsed = urlparse(text(source_url))
    if (
        parsed.netloc.casefold() not in SOULCYCLE_HOSTS
        or not parsed.path.casefold().startswith(SOULCYCLE_SERIES_PATH)
        or not text(html)
    ):
        return []
    market = soulcycle_market_label(gym)
    if not market:
        return []
    region_id = ""
    for tag_match in re.finditer(r"<a\b[^>]*>", html, re.IGNORECASE | re.DOTALL):
        tag = tag_match.group(0)
        title = html_attribute(tag, "title")
        if title.casefold() != f"change region to {market}".casefold():
            continue
        candidate = html_attribute(tag, "data-id")
        if re.fullmatch(r"\d{1,4}", candidate):
            region_id = candidate
            break
    if not region_id:
        return []
    menu = "cycle"
    page_match = re.search(
        r"<[^>]*\bclass\s*=\s*(['\"])[^'\"]*\bjs-series-page\b[^'\"]*\1[^>]*>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if page_match:
        menu = html_attribute(page_match.group(0), "data-menu") or menu
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", menu, re.IGNORECASE):
        return []
    return [
        f"https://www.soul-cycle.com/series/json/{region_id}/?{urlencode({'active-menu': menu})}"
    ]


def soulcycle_money(value: Any) -> float | None:
    match = re.search(r"\$?\s*(\d{1,6}(?:,\d{3})*(?:\.\d{1,2})?)", text(value))
    return float(match.group(1).replace(",", "")) if match else None


def soulcycle_series_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Reconstruct SoulCycle's complete market catalog from bounded cards."""

    if not is_soulcycle_series_api_url(source_url) or not isinstance(payload, dict):
        return []
    route_match = SOULCYCLE_SERIES_API_RE.fullmatch(urlparse(source_url).path)
    route_region = int(route_match.group("region_id")) if route_match else 0
    payload_region = numeric(payload.get("region_id"))
    if payload_region is None or int(payload_region) != route_region:
        return []
    fragments = [
        text(payload.get("purchasable_renewable_series")),
        text(payload.get("purchasable_cycle_series")),
    ]
    candidates: list[dict[str, Any]] = []
    for fragment in fragments:
        if not fragment:
            continue
        parser = SoulCyclePackParser()
        try:
            parser.feed(fragment)
        except Exception:
            continue
        for card in parser.cards:
            attributes = card.get("attributes") or {}
            card_text = normalized_label(text(card.get("text")))
            price_label = text(attributes.get("data-series-price"))
            amount = soulcycle_money(price_label)
            product_id = text(attributes.get("input:product"))
            size_label = text(attributes.get("input:size"))
            series_id = text(attributes.get("data-series-id"))
            series_name = text(attributes.get("data-series-name"))
            semantic = " ".join((series_id, series_name, card_text))
            if amount is None or not 0 < amount <= 10_000 or not product_id:
                continue
            count_match = re.fullmatch(r"\d{1,3}", size_label)
            count = float(size_label) if count_match else None
            unlimited = bool(re.search(r"\bunlimited\b|∞", semantic, re.IGNORECASE))
            recurring = bool(re.search(r"SOUL\s*RENEW|recurring payment", semantic, re.IGNORECASE))
            restricted_student = bool(re.search(r"\b(?:student|university)\b", semantic, re.IGNORECASE))
            promotion = bool(re.search(r"\b(?:new rider|starter)\b", semantic, re.IGNORECASE))
            ordinary_single = bool(not recurring and not promotion and not restricted_student and count == 1)
            if recurring:
                name = f"Soul Renew {int(count)}" if count is not None else "Soul Renew"
                product_type = "monthly"
                cadence = "30 days"
                allowance_period = "30 days"
            elif ordinary_single:
                name = "Single Class"
                product_type = "drop-in"
                cadence = "visit"
                allowance_period = "visit"
            else:
                base_name = "SoulCycle Student" if restricted_student else "SoulCycle Starter" if promotion else "SoulCycle"
                suffix = "Unlimited" if unlimited else f"{int(count)} Classes" if count is not None else "Class Pack"
                name = f"{base_name} {suffix}"
                product_type = "class-pack"
                cadence = "one-time"
                allowance_period = "purchase"
            aliases: list[str] = []
            if recurring and count is not None:
                aliases.append(f"soul-renew-{int(count)}")
            if ordinary_single:
                aliases.append("single-class")
            semantic_slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
            if semantic_slug:
                aliases.append(semantic_slug)
            expiration_match = re.search(
                r"\bExpires?\s+in\s+(\d{1,3})\s+(days?|months?)\b",
                card_text,
                re.IGNORECASE,
            )
            candidates.append({
                "sourceProductId": product_id,
                "sourceProductAliases": list(dict.fromkeys(aliases)),
                "sourceProductIdAuthority": "operator-widget",
                "name": name,
                "amount": amount,
                "currency": "USD",
                "cadence": cadence,
                "billingInterval": cadence,
                "intervalCount": 1,
                "productType": product_type,
                "accessScope": (
                    "San Francisco SoulCycle market"
                    if recurring or product_type == "class-pack"
                    else "One ordinary SoulCycle class in the San Francisco market"
                ),
                "scopeType": "operator-market",
                "classAllowance": {
                    "count": count,
                    "period": allowance_period,
                    "unlimited": unlimited,
                },
                "eligibility": {
                    "type": "student" if restricted_student else "standard-adult",
                    "restrictions": ["Student eligibility"] if restricted_student else [],
                },
                "commitment": {
                    "type": "unknown" if recurring else "none",
                    "minimumMonths": None,
                },
                "promotion": {
                    "isPromotion": promotion,
                    "label": "New-rider starter offer" if promotion else "",
                },
                "expiration": {
                    "count": int(expiration_match.group(1)) if expiration_match else None,
                    "unit": expiration_match.group(2).casefold() if expiration_match else "",
                },
                "fees": [],
                "ordinaryUse": ordinary_single,
                "bestValueLabel": False,
                "rawLabel": f"{name} — {card_text}"[:500],
                "method": "public-soulcycle-series-json",
                "adapter": "soulcycle-official",
                "evidenceTier": "official-public",
                "exactLocationMatch": "operator-market-multi-location",
                "sourceUrl": "https://www.soul-cycle.com/series/",
                "publicApiUrl": source_url,
                "autoPublishEligible": False,
            })
    return deduplicate_candidates(candidates)


def acuity_embedded_business_candidates(html: str, source_url: str) -> list[dict[str, Any]]:
    """Parse only Acuity's public ``BUSINESS`` bootstrap object.

    The surrounding inline script also contains session and CAPTCHA variables.
    Using ``JSONDecoder.raw_decode`` at the reviewed assignment boundary keeps
    those values out of observations and committed cache artifacts.
    """

    if platform_adapters.platform_for_url(source_url) != "acuity" or not text(html):
        return []
    decoder = json.JSONDecoder()
    for match in ACUITY_BUSINESS_ASSIGN_RE.finditer(html):
        remainder = html[match.end():].lstrip()
        if not remainder.startswith("{"):
            continue
        try:
            payload, _end = decoder.raw_decode(remainder)
        except json.JSONDecodeError:
            continue
        candidates = platform_adapters.acuity_business_candidates(payload, source_url)
        if candidates:
            return candidates
    return []


def momence_membership_api_route(source_url: str) -> str:
    """Derive Momence's anonymous read-only membership endpoint."""

    try:
        parsed = urlparse(text(source_url))
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or parsed.netloc.casefold() not in {"momence.com", "www.momence.com"}:
        return ""
    match = MOMENCE_MEMBERSHIP_PAGE_RE.fullmatch(unquote(parsed.path))
    if not match:
        return ""
    return f"https://momence.com/_api/primary/plugin/memberships/{match.group(1)}"


def is_momence_membership_api_url(source_url: str) -> bool:
    try:
        parsed = urlparse(text(source_url))
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() in {"momence.com", "www.momence.com"}
        and MOMENCE_MEMBERSHIP_API_RE.fullmatch(parsed.path)
    )


def public_platform_json_candidates(
    payload: Any,
    source_url: str,
    gym: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Route captured public JSON through the most semantic platform adapter.

    The generic graph walker remains a fallback, but must not replace an
    adapter that understands billing intervals, location scope, or plan-linked
    fees.  Both the static and rendered crawlers use this single dispatcher so
    JavaScript hydration cannot silently lose catalog semantics.
    """

    if is_equinox_membership_api_url(source_url):
        return equinox_membership_catalog_candidates(payload, source_url), []
    if is_bay_club_api_url(source_url):
        return bay_club_public_api_candidates(payload, source_url, gym)
    if is_soulcycle_series_api_url(source_url):
        return soulcycle_series_candidates(payload, source_url), []
    if is_momence_membership_api_url(source_url):
        return platform_adapters.momence_membership_api_candidates(payload, source_url), []
    platform = platform_name(source_url)
    if platform == "mariana-tek":
        return mariana_buy_page_candidates(payload if isinstance(payload, dict) else {}, source_url), []
    if platform == "xponential-member-app":
        return xponential_package_candidates(payload if isinstance(payload, dict) else {}, source_url)
    if platform == "abc-fitness":
        return abc_fitness_catalog_candidates(payload, source_url)
    if platform == "redpoint" and is_redpoint_preview_url(source_url):
        return redpoint_preview_candidates(payload, source_url), []
    if platform_adapters.platform_for_url(source_url):
        candidates = platform_adapters.extract_candidates(payload, source_url)
        candidates.extend(structured_candidates([json.dumps(payload)], source_url, "public-platform-json"))
        return deduplicate_candidates(candidates), []
    return [], []


def redpoint_location_slug(url: str) -> str:
    """Return the reviewed Movement location segment carried by a URL."""

    parsed = urlparse(text(url))
    host = parsed.netloc.casefold()
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if not segments:
        return ""
    if host == REDPOINT_HOST:
        return segments[0] if segments[0] not in {"n", "graphql-public", "csrf-bootstrap"} else ""
    if host in {"movementgyms.com", "www.movementgyms.com"}:
        return segments[0]
    return ""


def redpoint_catalog_link_allowed(base_url: str, candidate_url: str) -> bool:
    """Bound Movement's portal frontier to one location's public catalogs.

    The portal footer exposes every Movement market plus profile, tour,
    instruction, youth, and calendar routes. Following all of them turned one
    reviewed San Francisco record into dozens of unrelated requests. Only
    membership/pass catalogs under the exact location slug are useful for a
    cost audit. The crawler-created read-only preview route is also allowed.
    """

    parsed = urlparse(text(candidate_url))
    if parsed.netloc.casefold() != REDPOINT_HOST:
        return True
    query = parse_qs(parsed.query)
    if parsed.path.casefold() == REDPOINT_GRAPHQL_PATH and query.get(REDPOINT_PREVIEW_MARKER) == ["1"]:
        return True
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if len(segments) < 3:
        return False
    base_slug = redpoint_location_slug(base_url)
    if not base_slug or segments[0] != base_slug:
        return False
    remainder = segments[1:]
    if remainder[0] == "n":
        remainder = remainder[1:]
    return bool(
        len(remainder) >= 2
        and remainder[0] in {"membership", "memberships", "pass", "passes"}
        and not BOOKING_ACTION_EXCLUDE_RE.search(parsed.path)
    )


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
        redpoint_allowed = host != REDPOINT_HOST or redpoint_catalog_link_allowed(base_url, candidate)
        approved_booking = approved_booking_url(candidate) and not BOOKING_ACTION_EXCLUDE_RE.search(
            urlparse(candidate).path
        ) and redpoint_allowed
        approved_operator_page = (
            host == base_host
            and candidate != base_url
            and RESEARCH_PATH_RE.search(urlparse(candidate).path + ("?" + urlparse(candidate).query if urlparse(candidate).query else ""))
            and not RESEARCH_EXCLUDE_RE.search(urlparse(candidate).path)
            and operator_page_matches_gym(candidate, gym)
            and redpoint_allowed
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
MINDBODY_SERVICE_SELECT_RE = re.compile(
    r"<select\b(?=[^>]*\bname\s*=\s*['\"]?optTG\b)[^>]*>(?P<body>.*?)</select>",
    re.IGNORECASE | re.DOTALL,
)
MINDBODY_SERVICE_OPTION_RE = re.compile(
    r"<option\b(?P<attrs>[^>]*)>(?P<label>.*?)</option>",
    re.IGNORECASE | re.DOTALL,
)
MINDBODY_OPTION_VALUE_RE = re.compile(
    r"\bvalue\s*=\s*(?:['\"](?P<quoted>\d{1,12})['\"]|(?P<plain>\d{1,12})(?:\s|$))",
    re.IGNORECASE,
)
MINDBODY_CATEGORY_INCLUDE_RE = re.compile(
    r"\b(?:memberships?|packages?|classes?|passes|series|workshops?|open pole|training|access|privates?)\b",
    re.IGNORECASE,
)
MINDBODY_CATEGORY_EXCLUDE_RE = re.compile(
    r"\b(?:gift|account|login|log in|sign in|register|checkout|cart)\b",
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


def is_safe_mindbody_category_label(label: str) -> bool:
    """Allow only public product categories, never account or checkout actions."""

    value = " ".join(text(label).casefold().split())
    return bool(
        value
        and value != "select item"
        and not MINDBODY_CATEGORY_EXCLUDE_RE.search(value)
        and MINDBODY_CATEGORY_INCLUDE_RE.search(value)
    )


def mindbody_public_services_route(url: str) -> str:
    """Derive Mindbody's public Services tab from any reviewed store deep link.

    Operator sites often link to a gift card, login, or one isolated product
    while the same unauthenticated storefront exposes its complete public
    Services catalog. The numeric business ``studioid`` is the only value
    carried forward; product, cart, date, locale, and tracking parameters are
    deliberately discarded.
    """

    try:
        parsed = urlparse(text(url))
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or parsed.netloc.casefold() != "clients.mindbodyonline.com":
        return ""
    query = {
        key.casefold(): value
        for key, values in parse_qs(parsed.query).items()
        if (value := next((item for item in values if text(item)), ""))
    }
    site_id = text(query.get("studioid"))
    if not re.fullmatch(r"\d{1,12}", site_id):
        return ""
    return (
        "https://clients.mindbodyonline.com/ASP/main_shop.asp?"
        + urlencode((("studioid", site_id), ("stype", "41"), ("pMode", "1")))
    )


def mindbody_service_category_routes(source_url: str, html: str) -> list[str]:
    """Enumerate bounded public Services categories from Mindbody HTML.

    ``optTG`` values are public catalog category IDs. Only numeric IDs from a
    reviewed business storefront are accepted, and the result remains a
    review-only crawl route. Membership-shaped categories are prioritized so
    a large studio catalog cannot exhaust the per-listing request budget before
    its most comparison-relevant offers are visited.
    """

    base = mindbody_public_services_route(source_url)
    if not base or not text(html):
        return []
    categories: list[tuple[int, int, str]] = []
    seen_values: set[str] = set()
    ordinal = 0
    for select_match in MINDBODY_SERVICE_SELECT_RE.finditer(html):
        for option_match in MINDBODY_SERVICE_OPTION_RE.finditer(select_match.group("body")):
            value_match = MINDBODY_OPTION_VALUE_RE.search(option_match.group("attrs"))
            value = text((value_match.group("quoted") or value_match.group("plain")) if value_match else "")
            label = " ".join(unescape(re.sub(r"<[^>]+>", " ", option_match.group("label"))).split())
            if not value or value == "0" or value in seen_values or not is_safe_mindbody_category_label(label):
                continue
            seen_values.add(value)
            lowered = label.casefold()
            priority = 0 if "membership" in lowered or "access" in lowered else 1 if re.search(r"\b(?:package|pass|class|series)\b", lowered) else 2
            categories.append((priority, ordinal, value))
            ordinal += 1
    site_id = parse_qs(urlparse(base).query)["studioid"][0]
    return [
        "https://clients.mindbodyonline.com/ASP/main_shop.asp?"
        + urlencode((("studioid", site_id), ("stype", "41"), ("tg", value), ("pMode", "1")))
        for _priority, _ordinal, value in sorted(categories)[:12]
    ]


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
    # A reviewed canonical merge can retain a former domain on the stable OSM
    # record while recording the replacement operator domain as a source alias.
    # Trust only those committed alias URLs; a bare cross-domain price source
    # remains excluded unless it is an approved booking platform.
    operator_hosts.update({
        host_key(url)
        for alias in gym.get("sourceAliases", []) or []
        if isinstance(alias, dict)
        and (url := text(alias.get("sourceUrl")))
        and is_public_http_url(url)
        and not coverage.is_osm_url(url)
        and platform_name(url) == "operator-site"
    })

    def matches_operator_host(url: str) -> bool:
        candidate_host = host_key(url)
        return any(
            candidate_host == reviewed_host
            or candidate_host.endswith(f".{reviewed_host}")
            or reviewed_host.endswith(f".{candidate_host}")
            for reviewed_host in operator_hosts
        )

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
            and matches_operator_host(url)
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
        allowed = allowed or matches_operator_host(url) or approved_booking_url(url)
        if not allowed:
            continue
        derived_services = mindbody_public_services_route(url)
        prioritized = (
            [(f"{source_field}.mindbodyServices", derived_services), (source_field, url)]
            if derived_services and request_identity(derived_services) != request_identity(url)
            else [(source_field, url)]
        )
        for route_field, route_url in prioritized:
            identity = request_identity(route_url)
            if identity in seen:
                continue
            seen.add(identity)
            routes.append({"url": route_url, "sourceField": route_field})
            if len(routes) >= MAX_REVIEWED_SEED_URLS:
                return routes
    return routes


@lru_cache(maxsize=256)
def load_robots_parser(robots_url: str, timeout: float) -> tuple[RobotFileParser | None, str]:
    """Fetch one robots policy per origin for the life of a crawl process."""

    request = Request(robots_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/plain",
        "Accept-Encoding": "identity",
    })
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs originate in committed public listing data.
            body = response.read(500_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        parser.parse(body.splitlines())
        return parser, "checked"
    except HTTPError as error:
        if error.code in {401, 403}:
            return None, f"robots-http-{error.code}"
        return None, f"robots-http-{error.code}"
    except (URLError, TimeoutError, OSError):
        return None, "robots-unavailable"


def robots_allowed(url: str, timeout: float) -> tuple[bool, str]:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser, status = load_robots_parser(robots_url, timeout)
    if status in {"robots-http-401", "robots-http-403"}:
        return False, status
    return (parser.can_fetch(USER_AGENT, url) if parser else True), status


def static_access_blocker(url: str, html: str) -> str:
    """Classify public shells that require a disallowed session-reset POST.

    Mindbody sometimes returns HTTP 200 with an otherwise blank page whose
    only action is ``resetSession()``. That helper submits an identity-logout
    form before the catalog can load. The research bot never submits forms, so
    the response must be recorded as access-blocked instead of a successful
    empty catalog.
    """

    if platform_name(url) == "mindbody" and re.search(
        r"\bmb\.sessionHelpers\.resetSession\s*\(\s*\)\s*;?",
        html,
        re.IGNORECASE,
    ):
        return "identity-session-reset-required"
    return ""


def preferred_accept_header(url: str) -> str:
    """Prefer structured responses only for explicit public API routes."""

    path = urlparse(url).path.casefold()
    if re.search(r"(?:^|/)_?api(?:/|$)", path) or path.endswith(".json"):
        return "application/json,text/plain;q=0.9,*/*;q=0.5"
    return "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5"


def bay_club_pricing_request_body(url: str) -> bytes:
    """Build the anonymous, non-persistent public calculator request body."""

    if not is_bay_club_pricing_calculation_url(url):
        return b""
    club_code = bay_club_club_code_from_url(url)
    return json.dumps(
        {
            "clubCode": club_code,
            "membersConfigurations": [],
            "availableInSharedBuilder": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_response_body(body: bytes, content_encoding: str) -> bytes:
    """Decode standard HTTP content codings in reverse application order."""

    encodings = [value.strip().casefold() for value in content_encoding.split(",") if value.strip()]
    decoded = body
    for encoding in reversed(encodings):
        if encoding == "identity":
            continue
        if encoding == "br":
            decoded = brotli.decompress(decoded)
        elif encoding in {"gzip", "x-gzip"}:
            decoded = gzip.decompress(decoded)
        elif encoding == "deflate":
            decoded = zlib.decompress(decoded)
        else:
            raise ValueError(f"unsupported content encoding: {encoding}")
        if len(decoded) > MAX_RESPONSE_BYTES:
            raise OverflowError("decoded response exceeds size limit")
    return decoded


def decode_nuxt_devalue_payload(value: str) -> Any:
    """Decode Nuxt's bounded public ``devalue`` state without executing it."""

    try:
        table = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(table, list) or not table or len(table) > 75_000:
        return None
    cache: dict[int, Any] = {}
    resolving: set[int] = set()
    node_count = 0

    def resolve_index(index: int, depth: int = 0) -> Any:
        nonlocal node_count
        if index < 0:
            return None
        if index >= len(table) or depth > 80 or node_count > 250_000:
            raise ValueError("invalid or excessive Nuxt devalue graph")
        if index in cache:
            return cache[index]
        if index in resolving:
            return None
        resolving.add(index)
        raw = table[index]
        node_count += 1
        if isinstance(raw, dict):
            output: dict[str, Any] = {}
            cache[index] = output
            for key, child in raw.items():
                output[text(key)] = resolve_child(child, depth + 1)
        elif isinstance(raw, list):
            output = []
            cache[index] = output
            output.extend(resolve_child(child, depth + 1) for child in raw)
        else:
            output = raw
            cache[index] = output
        resolving.discard(index)
        return output

    def resolve_child(child: Any, depth: int) -> Any:
        if isinstance(child, bool):
            return child
        if isinstance(child, int):
            return resolve_index(child, depth)
        if isinstance(child, list):
            return [resolve_child(item, depth + 1) for item in child]
        if isinstance(child, dict):
            return {text(key): resolve_child(item, depth + 1) for key, item in child.items()}
        return child

    try:
        return resolve_index(0)
    except (RecursionError, ValueError):
        return None


NUXT_DATA_SCRIPT_RE = re.compile(
    r"<script\b(?=[^>]*(?:\bid\s*=\s*['\"]__NUXT_DATA__['\"]|\bdata-nuxt-data\b))[^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def redpoint_membership_metadata(html: str, source_url: str) -> dict[str, str]:
    """Recover exact public IDs needed by Movement's read-only price preview."""

    if platform_name(source_url) != "redpoint" or not text(html):
        return {}
    decoded = None
    for match in NUXT_DATA_SCRIPT_RE.finditer(html):
        # Script contents are JSON, not HTML text. Decoding entities here can
        # turn a literal ``&quot;`` inside an embedded rich-text string into an
        # unescaped quote and corrupt the entire payload.
        decoded = decode_nuxt_devalue_payload(match.group("body"))
        if decoded is not None:
            break
    if decoded is None:
        return {}
    nodes = list(walk_json(decoded))

    def first_node(prefix: str, predicate: Any | None = None) -> dict[str, Any]:
        for node in nodes:
            identifier = text(node.get("id"))
            if identifier.startswith(prefix) and (predicate is None or predicate(node)):
                return node
        return {}

    plan = first_node(
        REDPOINT_ID_PREFIXES["plan"],
        lambda node: text(node.get("planType")).casefold() in {"", "membership"},
    )
    session = first_node(REDPOINT_ID_PREFIXES["session"])
    expected_location = redpoint_location_slug(source_url).replace("-", " ")
    facility = first_node(
        REDPOINT_ID_PREFIXES["facility"],
        lambda node: normalized_label(text(node.get("label") or node.get("slug"))).casefold().replace("-", " ") == expected_location,
    )
    enrollment = first_node(
        REDPOINT_ID_PREFIXES["enrollment"],
        lambda node: bool(re.search(
            r"\bprimary member\b",
            " ".join(text(node.get(key)) for key in ("name", "title", "label", "singular", "plural")),
            re.IGNORECASE,
        )),
    )
    if not enrollment:
        enrollment = first_node(
            REDPOINT_ID_PREFIXES["enrollment"],
            lambda node: not re.search(
                r"\b(?:student|child|youth|household|dependent|senior)\b",
                " ".join(text(node.get(key)) for key in ("name", "title", "label", "singular", "plural")),
                re.IGNORECASE,
            ),
        )
    if not all((plan, session, facility, enrollment)):
        return {}
    if plan.get("active") is False:
        return {}

    date_values: set[str] = set()
    for node in nodes:
        for key, value in node.items():
            if "date" not in key.casefold():
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T]|$)", text(item))
                if match:
                    date_values.add(match.group(1))
    if not date_values:
        return {}

    billing_interval = ""
    billing_type = ""
    for node in nodes:
        candidate_interval = text(node.get("billingInterval"))
        candidate_type = text(node.get("billingType"))
        if candidate_interval or candidate_type:
            billing_interval = billing_interval or candidate_interval
            billing_type = billing_type or candidate_type
    client_match = re.search(r"/cpx/static/v(?P<version>\d+-\d+-\d+)(?:-|/)", html, re.IGNORECASE)
    parsed = urlparse(source_url)
    presentation = plan.get("presentation") if isinstance(plan.get("presentation"), dict) else {}
    return {
        "planId": text(plan.get("id")),
        "planName": text(plan.get("presentationTitle") or presentation.get("title") or plan.get("title") or plan.get("name") or "Membership"),
        "sessionId": text(session.get("id")),
        "facilityId": text(facility.get("id")),
        "enrollmentTypeId": text(enrollment.get("id")),
        "enrollmentTypeName": text(enrollment.get("name") or enrollment.get("title") or enrollment.get("label") or enrollment.get("singular") or "Primary Member"),
        "startDate": min(date_values),
        "billingInterval": billing_interval,
        "billingType": billing_type,
        "sourcePath": parsed.path,
        "locationSlug": redpoint_location_slug(source_url),
        "clientVersion": client_match.group("version").replace("-", ".") if client_match else REDPOINT_CLIENT_VERSION,
    }


def redpoint_preview_route(html: str, source_url: str) -> str:
    """Create a deterministic internal route for one anonymous price preview."""

    metadata = redpoint_membership_metadata(html, source_url)
    if not metadata:
        return ""
    if metadata.get("billingInterval").casefold() not in {"", "month", "monthly"}:
        return ""
    if metadata.get("billingType").casefold() not in {"", "recurring"}:
        return ""
    query = urlencode(((REDPOINT_PREVIEW_MARKER, "1"), *sorted(metadata.items())))
    return f"https://{REDPOINT_HOST}{REDPOINT_GRAPHQL_PATH}?{query}"


def is_redpoint_preview_url(url: str) -> bool:
    parsed = urlparse(text(url))
    return bool(
        parsed.scheme.casefold() == "https"
        and parsed.netloc.casefold() == REDPOINT_HOST
        and parsed.path.casefold() == REDPOINT_GRAPHQL_PATH
        and parse_qs(parsed.query).get(REDPOINT_PREVIEW_MARKER) == ["1"]
    )


REDPOINT_PREVIEW_QUERY = """query PreviewSessionContractQuery($sessionId: ID!, $input: PreviewContractInput!, $language: Language!) {
  session(id: $sessionId) {
    previewContract(input: $input) {
      cart {
        items {
          __typename
          ... on CartProductItem {
            unitPrice originalUnitPrice quantity description extendedTotal note
            productVariant { id }
          }
        }
        subtotal inclusiveTaxTotal exclusiveTaxTotal total surchargeNote(language: $language)
      }
      nextBillDate paymentDueMode depositAmount
    }
  }
}"""


def redpoint_preview_request_payload(url: str) -> dict[str, Any]:
    values = {key: item[0] for key, item in parse_qs(urlparse(url).query).items() if item}
    required = ("sessionId", "facilityId", "enrollmentTypeId", "startDate", "sourcePath")
    if not is_redpoint_preview_url(url) or not all(text(values.get(key)) for key in required):
        return {}
    source_path = text(values["sourcePath"])
    if not source_path.startswith("/") or ".." in source_path:
        return {}
    return {
        "operationName": "PreviewSessionContractQuery",
        "query": REDPOINT_PREVIEW_QUERY,
        "variables": {
            "sessionId": text(values["sessionId"]),
            "input": {
                "startDate": f"{text(values['startDate'])}T00:00:00",
                "enrollmentTypeCounts": [{
                    "enrollmentTypeId": text(values["enrollmentTypeId"]),
                    "count": 1,
                }],
                "promotionCodes": [],
            },
            "language": "ENGLISH",
        },
    }


def redpoint_preview_candidates(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Associate Movement's public recurring dues and fee line items."""

    if not is_redpoint_preview_url(source_url) or not isinstance(payload, dict):
        return []
    values = {key: item[0] for key, item in parse_qs(urlparse(source_url).query).items() if item}
    preview = (((payload.get("data") or {}).get("session") or {}).get("previewContract") or {})
    cart = preview.get("cart") or {}
    items = cart.get("items") or []
    dues_item: dict[str, Any] | None = None
    fees: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = text(item.get("description") or item.get("note"))
        amount = numeric(item.get("unitPrice") or item.get("extendedTotal"))
        if amount is None or amount <= 0:
            continue
        lowered = label.casefold()
        if re.search(r"\b(?:annual|enrollment|enrolment|initiation|activation|processing|setup|join)\s+fee\b", lowered):
            fee_type = next(
                (mapped for token, mapped in (
                    ("annual", "annual"), ("enrollment", "enrollment"), ("enrolment", "enrollment"),
                    ("initiation", "initiation"), ("activation", "activation"),
                    ("processing", "processing"), ("setup", "processing"), ("join", "enrollment"),
                ) if token in lowered),
                "other",
            )
            fees.append({
                "type": fee_type,
                "name": label,
                "amount": amount,
                "currency": "USD",
                "cadence": "year" if fee_type == "annual" else "one-time",
                "mandatory": True,
            })
        elif re.search(r"\b(?:monthly dues|membership dues|monthly membership|membership)\b", lowered):
            dues_item = item
    if not dues_item:
        return []
    amount = numeric(dues_item.get("unitPrice") or dues_item.get("extendedTotal"))
    if amount is None or amount <= 0:
        return []
    source_path = text(values.get("sourcePath"))
    source_page = f"https://{REDPOINT_HOST}{source_path}"
    plan_id = text(values.get("planId"))
    plan_name = text(values.get("planName")) or "Monthly Membership — Primary Member"
    slug_alias = re.sub(r"[^a-z0-9]+", "-", plan_name.casefold()).strip("-")
    path_alias = urlparse(source_page).path.rstrip("/").rsplit("/", 1)[-1]
    aliases = list(dict.fromkeys(filter(None, ("monthly-primary", slug_alias, path_alias))))
    fee_label = ", ".join(f"{fee['name']} ${fee['amount']:g}" for fee in fees)
    raw_label = f"{plan_name} — {text(dues_item.get('description'))} ${amount:g}/month"
    if fee_label:
        raw_label += f"; {fee_label}"
    return [{
        "sourceProductId": plan_id,
        "sourceProductAliases": aliases,
        "sourceProductIdAuthority": "operator-widget",
        "name": plan_name,
        "amount": amount,
        "currency": "USD",
        "cadence": "month",
        "billingInterval": "month",
        "intervalCount": 1,
        "productType": "monthly",
        "accessScope": "Unlimited climbing, yoga, and fitness access across Movement locations",
        "scopeType": "multi-location",
        "classAllowance": {"count": None, "period": "month", "unlimited": True},
        "eligibility": {"type": "standard-adult", "restrictions": []},
        "commitment": {"type": "unknown", "minimumMonths": None},
        "promotion": {"isPromotion": False, "label": ""},
        "fees": fees,
        "bestValueLabel": False,
        "rawLabel": raw_label[:500],
        "method": "public-redpoint-preview-query",
        "adapter": "redpoint",
        "evidenceTier": "official-public",
        "exactLocationMatch": "exact-location",
        "sourceUrl": source_page,
        "autoPublishEligible": False,
    }]


def fetch_redpoint_preview(url: str, timeout: float, robots_status: str) -> dict[str, Any]:
    """Execute only Movement's anonymous, read-only contract preview query."""

    payload = redpoint_preview_request_payload(url)
    if not payload:
        return {"status": "invalid-public-preview", "url": url, "robotsStatus": robots_status}
    values = {key: item[0] for key, item in parse_qs(urlparse(url).query).items() if item}
    source_page = f"https://{REDPOINT_HOST}{values['sourcePath']}"
    csrf_url = f"https://{REDPOINT_HOST}{REDPOINT_CSRF_PATH}"
    for candidate in (source_page, csrf_url):
        allowed, status = robots_allowed(candidate, timeout)
        if not allowed:
            return {"status": "robots-disallowed", "url": url, "robotsStatus": status}
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))

    def public_request(request: Request, limit: int = MAX_RESPONSE_BYTES) -> tuple[bytes, Any]:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 - exact reviewed public operator host.
            body = response.read(limit + 1)
            if len(body) > limit:
                raise OverflowError("Redpoint response exceeds size limit")
            return decode_response_body(body, text(response.headers.get("Content-Encoding"))), response.headers

    common_headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
        "X-Redpoint-Hq-Client": text(values.get("clientVersion")) or REDPOINT_CLIENT_VERSION,
    }
    try:
        public_request(Request(source_page, headers={**common_headers, "Accept": "text/html"}))
        time.sleep(DOMAIN_DELAY_SECONDS)
        bootstrap_body, bootstrap_headers = public_request(Request(
            csrf_url,
            headers={**common_headers, "Accept": "application/json,text/plain;q=0.9,*/*;q=0.5", "Referer": source_page},
        ), 500_000)
        csrf_token = text(bootstrap_headers.get("X-CSRF-Token"))
        for cookie in cookie_jar:
            if "csrf" in cookie.name.casefold() or "xsrf" in cookie.name.casefold():
                csrf_token = unquote(cookie.value)
                break
        if not csrf_token:
            try:
                bootstrap_payload = json.loads(bootstrap_body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                bootstrap_payload = {}
            csrf_token = text(bootstrap_payload.get("token") or bootstrap_payload.get("csrfToken")) if isinstance(bootstrap_payload, dict) else ""
        if not csrf_token:
            return {"status": "public-preview-csrf-unavailable", "url": url, "robotsStatus": robots_status}
        time.sleep(DOMAIN_DELAY_SECONDS)
        request_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        response_body, response_headers = public_request(Request(
            f"https://{REDPOINT_HOST}{REDPOINT_GRAPHQL_PATH}",
            data=request_body,
            method="POST",
            headers={
                **common_headers,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": f"https://{REDPOINT_HOST}",
                "Referer": source_page,
                "X-CSRF-TOKEN": csrf_token,
                "RPHQ-Facility": text(values.get("facilityId")),
            },
        ))
        body_text = response_body.decode(response_headers.get_content_charset() or "utf-8", errors="replace")
        try:
            response_payload = json.loads(body_text)
        except json.JSONDecodeError:
            return {"status": "public-preview-invalid-json", "url": url, "robotsStatus": robots_status}
        if not redpoint_preview_candidates(response_payload, url):
            return {
                "status": "public-preview-no-price",
                "url": url,
                "robotsStatus": robots_status,
                "html": body_text,
                "contentType": "application/json",
            }
        return {
            "status": "fetched",
            "url": url,
            "robotsStatus": robots_status,
            "html": body_text,
            "contentType": "application/json",
            "contentEncoding": text(response_headers.get("Content-Encoding")),
            "etag": "",
            "lastModified": "",
            "accessBlocker": "",
        }
    except HTTPError as error:
        return {
            "status": f"http-{error.code}",
            "url": url,
            "robotsStatus": robots_status,
            "retryAfter": text(error.headers.get("Retry-After")) if error.headers else "",
        }
    except (URLError, TimeoutError, OSError, OverflowError, ValueError, zlib.error, brotli.error) as error:
        return {"status": "network-error", "url": url, "robotsStatus": robots_status, "error": text(error)[:200]}


def fetch_page(url: str, timeout: float, conditional: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed, robots_status = robots_allowed(url, timeout)
    if not allowed:
        return {"status": "robots-disallowed", "url": url, "robotsStatus": robots_status}
    if is_redpoint_preview_url(url):
        return fetch_redpoint_preview(url, timeout, robots_status)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": preferred_accept_header(url),
        # urllib does not transparently decode Brotli.  Several official gym
        # sites send ``Content-Encoding: br`` even when no encoding preference
        # is supplied, so explicitly request the uncompressed representation.
        "Accept-Encoding": "identity",
    }
    request_body = bay_club_pricing_request_body(url)
    if request_body:
        parsed = urlparse(url)
        request_url = parsed._replace(query="", fragment="").geturl()
        headers["Content-Type"] = "application/json"
    else:
        request_url = url
    if conditional and not request_body:
        if conditional.get("etag"):
            headers["If-None-Match"] = conditional["etag"]
        if conditional.get("lastModified"):
            headers["If-Modified-Since"] = conditional["lastModified"]
    request = Request(
        request_url,
        data=request_body or None,
        headers=headers,
        method="POST" if request_body else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs originate in committed public listing data.
            content_type = response.headers.get_content_type()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            response_url = url if request_body else response.geturl()
            if len(body) > MAX_RESPONSE_BYTES:
                return {"status": "response-too-large", "url": response_url, "robotsStatus": robots_status}
            content_encoding = text(response.headers.get("Content-Encoding"))
            try:
                body = decode_response_body(body, content_encoding)
            except (brotli.error, OSError, OverflowError, ValueError, zlib.error) as error:
                return {
                    "status": "unsupported-content-encoding",
                    "url": response_url,
                    "robotsStatus": robots_status,
                    "contentEncoding": content_encoding,
                    "error": text(error)[:200],
                }
            charset = response.headers.get_content_charset() or "utf-8"
            html = body.decode(charset, errors="replace")
            access_blocker = static_access_blocker(response_url, html)
            return {
                "status": "access-blocked" if access_blocker else "fetched",
                "url": response_url,
                "contentType": content_type,
                "contentEncoding": content_encoding,
                "html": html,
                "etag": response.headers.get("ETag", ""),
                "lastModified": response.headers.get("Last-Modified", ""),
                "robotsStatus": robots_status,
                "accessBlocker": access_blocker,
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
    public_platform = platform_adapters.platform_for_url(source_url)
    approved_public_catalog = (
        bool(public_platform)
        or is_equinox_membership_api_url(source_url)
        or is_soulcycle_series_api_url(source_url)
    )
    json_shaped = html.lstrip().startswith(("{", "["))
    if approved_public_catalog and (is_json or json_shaped):
        try:
            payload = json.loads(html)
        except json.JSONDecodeError:
            payload = {}
        candidates, nested = public_platform_json_candidates(payload, source_url, gym)
        return candidates, nested, hashlib.sha256(html.encode("utf-8")).hexdigest()
    parser = PageParser()
    try:
        parser.feed(html)
    except Exception:  # HTMLParser is tolerant, but malformed pages should not abort the crawl.
        pass
    visible = normalized_label(" ".join(parser.visible)) if len(" ".join(parser.visible)) <= 220 else " ".join(parser.visible)
    candidates = structured_candidates(parser.json_ld, text(result.get("url")))
    candidates.extend(structured_candidates(parser.hydration_json, text(result.get("url")), "embedded-hydration-json"))
    bounded_candidates = labeled_plan_card_candidates(
        parser.squarespace_text_blocks,
        source_url,
        "squarespace-plan-card",
    )
    bounded_candidates.extend(labeled_plan_card_candidates(
        duda_plan_cards(html),
        source_url,
        "duda-plan-card",
    ))
    bounded_candidates.extend(labeled_plan_card_candidates(
        webflow_shop_cards(html),
        source_url,
        "webflow-shop-card",
    ))
    bounded_candidates.extend(wordpress_class_box_candidates(html, source_url))
    bounded_candidates.extend(zen_planner_html_candidates(html, source_url))
    visible_page_candidates = visible_candidates(visible, text(result.get("url")))
    if bounded_candidates:
        # A page-wide text regex loses card boundaries and can reinterpret
        # per-month display arithmetic, savings, or a neighboring offer as a
        # standalone plan.  Prefer the bounded card result while retaining
        # domain-specific adapters and non-selectable official cost context.
        visible_page_candidates = [
            candidate
            for candidate in visible_page_candidates
            if text(candidate.get("method"))
            not in {"visible-text-candidate", "visible-allowance-plan-card"}
        ]
    candidates.extend(visible_page_candidates)
    candidates.extend(bounded_candidates)
    candidates.extend(embedded_operator_candidates(html, source_url))
    candidates.extend(acuity_embedded_business_candidates(html, source_url))
    deduplicated = deduplicate_candidates(candidates)
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    stores: list[str] = []
    stores.extend(equinox_membership_catalog_routes(parser.hydration_json, source_url))
    stores.extend(bay_club_catalog_routes(source_url, gym))
    stores.extend(soulcycle_series_catalog_routes(html, source_url, gym))
    momence_api = momence_membership_api_route(source_url)
    if momence_api:
        stores.append(momence_api)
    redpoint_preview = redpoint_preview_route(html, source_url)
    if redpoint_preview:
        stores.append(redpoint_preview)
    mindbody_services = mindbody_public_services_route(source_url)
    if mindbody_services and request_identity(mindbody_services) != request_identity(source_url):
        stores.append(mindbody_services)
    for candidate in mindbody_service_category_routes(source_url, html):
        if request_identity(candidate) != request_identity(source_url) and candidate not in stores:
            stores.append(candidate)
    for candidate in linked_storefronts(source_url, parser.links, gym):
        if candidate not in stores:
            stores.append(candidate)
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


TRANSIENT_CRAWL_STATUSES = {
    "network-error",
    "http-429",
    "host-backoff-after-429",
    "response-too-large",
    "unsupported-content-encoding",
}


def merge_transient_cache_entry(
    existing: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    """Retain the last parseable response when a refresh fails transiently."""

    previous = existing if isinstance(existing, dict) else {}
    if text(update.get("status")) not in TRANSIENT_CRAWL_STATUSES or not previous:
        return update
    merged = {**previous, **update}
    for key in (
        "candidates", "linkedStorefronts", "locationCandidates", "contentHash",
        "etag", "lastModified", "parserVersion",
    ):
        if key in previous:
            merged[key] = previous[key]
        else:
            merged.pop(key, None)
    previous_success = text(previous.get("status")) in {"fetched", "not-modified"}
    last_success = text(previous.get("lastSuccessfulAt"))
    if not last_success and previous_success:
        last_success = text(previous.get("lastAttemptAt"))
    if last_success:
        merged["lastSuccessfulAt"] = last_success
    else:
        merged.pop("lastSuccessfulAt", None)
    return merged


def reusable_transient_cache(
    entry: dict[str, Any] | None,
    result: dict[str, Any],
    source_url: str,
    gym: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]], str, str] | None:
    """Return last-good parsed evidence when a public refresh fails transiently."""

    cached = entry if isinstance(entry, dict) else {}
    if text(result.get("status")) not in TRANSIENT_CRAWL_STATUSES or not cached:
        return None
    offers = list(cached.get("candidates", []))
    stores = linked_storefronts(source_url, list(cached.get("linkedStorefronts", [])), gym)
    locations = list(cached.get("locationCandidates", []))
    if not offers and not stores and not locations:
        return None
    captured_at = text(cached.get("lastSuccessfulAt"))
    if not captured_at and text(cached.get("status")) in {"fetched", "not-modified"}:
        captured_at = text(cached.get("lastAttemptAt"))
    if not captured_at:
        return None
    return offers, stores, locations, text(cached.get("contentHash")), captured_at


def merge_crawl_observations(
    existing: list[dict[str, Any]],
    current: list[dict[str, Any]],
    crawled_gym_ids: set[str],
    run_attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep prior evidence only when every current route failed transiently."""

    attempts_by_gym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in run_attempts:
        attempts_by_gym[text(attempt.get("gymId"))].append(attempt)
    current_ids = {text(item.get("gymId")) for item in current}
    retain_ids = {
        gym_id for gym_id in crawled_gym_ids
        if gym_id not in current_ids
        and attempts_by_gym.get(gym_id)
        and all(
            text(attempt.get("status")) in TRANSIENT_CRAWL_STATUSES
            for attempt in attempts_by_gym[gym_id]
        )
    }
    combined = [
        item for item in existing
        if text(item.get("gymId")) not in crawled_gym_ids
        or text(item.get("gymId")) in retain_ids
    ] + current
    # A reviewed operator page and its explicit priceSourceUrl can both lead
    # to the same redirected booking page. Preserve that provenance in the
    # attempt log, but keep only one semantic product observation so catalog
    # counts and deal candidates do not double. Prefer the discovery path on
    # the same booking host as the evidence itself.
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in combined:
        identity = json.dumps(
            {key: value for key, value in item.items() if key != "catalogSourceUrl"},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        incumbent = deduplicated.get(identity)
        if incumbent is None:
            deduplicated[identity] = item
            continue
        item_same_host = hostname(text(item.get("catalogSourceUrl"))) == hostname(text(item.get("sourceUrl")))
        incumbent_same_host = hostname(text(incumbent.get("catalogSourceUrl"))) == hostname(text(incumbent.get("sourceUrl")))
        if item_same_host and not incumbent_same_host:
            deduplicated[identity] = item
    combined = list(deduplicated.values())
    combined.sort(
        key=lambda item: (
            text(item.get("gymId")), text(item.get("sourceUrl")), text(item.get("kind")),
            float(item.get("low", 0) or 0), float(item.get("amount", 0) or 0), text(item.get("rawLabel")),
        )
    )
    return combined


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
        amount = float(amount_match.group(1).replace(",", ""))
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
                "_stableProduct": bool(text(observation.get("sourceProductId"))),
            })
    stable_semantics = {
        (
            item["gymId"], item["sourceUrl"], item["amount"],
            item["productType"], item["cadence"],
        )
        for item in deals
        if item["_stableProduct"]
    }
    filtered: list[dict[str, Any]] = []
    for item in deals:
        semantic = (
            item["gymId"], item["sourceUrl"], item["amount"],
            item["productType"], item["cadence"],
        )
        if not item["_stableProduct"] and semantic in stable_semantics:
            continue
        item.pop("_stableProduct", None)
        filtered.append(item)
    return sorted(filtered, key=lambda item: (item["gymId"], item["sourceUrl"], item["amount"], item["label"]))


def load_rendered_deal_observations(path: Path = RENDERED_OBSERVATIONS_PATH) -> list[dict[str, Any]]:
    """Keep rendered promotion evidence when a static crawl refreshes deals."""

    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in document.get("observations", []) if isinstance(item, dict)]


PLAN_LABEL_STOPWORDS = {
    "a", "access", "adult", "and", "auto", "autopay", "billing", "class", "classes",
    "contract", "for", "membership", "monthly", "month", "plan", "rate", "regular", "renewal",
    "the", "to",
}
NON_STANDARD_COMPONENT_RE = re.compile(
    r"\b(?:add[ -]?on|additional (?:child|children|fee|person)|child(?:ren)?|employee|family add|"
    r"kid(?:s)?|optional (?:access|fee|upgrade)|per additional|senior|student|supplement|upgrade|youth)\b",
    re.IGNORECASE,
)
PLAN_LINKED_FEE_FRAGMENT_RE = re.compile(
    r"(?:\+\s*)?\$\s*\d{1,4}(?:\.\d{1,2})?\s*(?:annual|activation|cancell?ation|termination|enroll?ment|"
    r"join|processing|setup)(?:\s+fee)?|(?:annual|activation|cancell?ation|enroll?ment|join|processing|"
    r"setup|termination)\s+fee\s*[:+-]?\s*\$\s*\d{1,4}(?:\.\d{1,2})?",
    re.IGNORECASE,
)
PLAN_INTRO_COMPONENT_FRAGMENT_RE = re.compile(
    r"(?:first|intro(?:ductory)?|trial)\s+(?:class|visit|session)[^$.\n]{0,35}\$\s*\d{1,4}(?:\.\d{1,2})?"
    r"|\$\s*\d{1,4}(?:\.\d{1,2})?[^.\n]{0,35}(?:first|intro(?:ductory)?|trial)\s+(?:class|visit|session)",
    re.IGNORECASE,
)


def plan_label_tokens(value: Any) -> set[str]:
    """Return distinctive tokens for matching one reviewed plan to one live card."""

    return {
        token
        for token in re.findall(r"[a-z0-9]+", text(value).casefold())
        if token not in PLAN_LABEL_STOPWORDS and len(token) > 1
    }


def plan_identity_label(value: Any, candidate_amount: Any = None) -> str:
    """Remove attached fee arithmetic while preserving the product card label."""

    label = PLAN_INTRO_COMPONENT_FRAGMENT_RE.sub(" ", text(value))
    label = PLAN_LINKED_FEE_FRAGMENT_RE.sub(" ", label)
    amount = numeric(candidate_amount)
    if amount is None:
        return label
    remaining_amounts = [float(item.replace(",", "")) for item in MONEY_RE.findall(label)]
    if remaining_amounts and not any(abs(item - amount) <= 0.01 for item in remaining_amounts):
        return ""
    return label


def class_allowance_is_disclosed(allowance: dict[str, Any]) -> bool:
    """Distinguish an explicit zero/limited allowance from a legacy unknown."""

    if not allowance:
        return False
    if allowance.get("disclosed") is True:
        return True
    return bool(allowance.get("unlimited")) or numeric(allowance.get("count")) is not None


def class_allowances_match_exactly(selected: dict[str, Any], candidate: dict[str, Any]) -> bool:
    selected_allowance = selected.get("classAllowance") or {}
    candidate_allowance = candidate.get("classAllowance") or {}
    if not selected_allowance or not candidate_allowance:
        return False
    if bool(selected_allowance.get("unlimited")) != bool(candidate_allowance.get("unlimited")):
        return False
    selected_count = numeric(selected_allowance.get("count"))
    candidate_count = numeric(candidate_allowance.get("count"))
    if selected_count is None or candidate_count is None:
        return bool(selected_allowance.get("unlimited")) and bool(candidate_allowance.get("unlimited"))
    if selected_count != candidate_count:
        return False
    selected_period = text(selected_allowance.get("period")).casefold()
    candidate_period = text(candidate_allowance.get("period")).casefold()
    return not selected_period or not candidate_period or selected_period == candidate_period


def candidate_normalized_monthly(candidate: dict[str, Any]) -> float | None:
    """Normalize an explicitly recurring candidate while retaining its raw cadence."""

    amount = numeric(candidate.get("amount"))
    if amount is None or amount <= 0:
        return None
    cadence = text(candidate.get("cadence") or candidate.get("billingInterval")).casefold().replace("_", " ")
    interval_count = numeric(candidate.get("intervalCount") or candidate.get("billingIntervalCount")) or 1
    if interval_count <= 0:
        return None
    if cadence in {"month", "monthly", "p1m"}:
        return round(amount / interval_count, 2)
    if cadence in {"4 weeks", "four weeks", "28 days", "28 day"}:
        return round(amount * 13 / 12 / interval_count, 2)
    if cadence in {"2 weeks", "two weeks", "biweekly", "bi-weekly"}:
        return round(amount * 26 / 12 / interval_count, 2)
    if cadence in {"week", "weekly", "p1w"}:
        return round(amount * 52 / 12 / interval_count, 2)
    if cadence in {"year", "yearly", "annual", "p1y"}:
        return round(amount / 12 / interval_count, 2)
    return None


def selected_plan_candidate_match(
    selected: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[int, str] | None:
    """Return a conservative selected-plan match score and its evidence method.

    Stable public product IDs are authoritative. Label matching is a fallback
    only for cards containing one dollar amount; a broad visible-text snippet
    spanning several plans cannot identify which amount belongs to the
    selected plan.
    """

    selected_allowance = selected.get("classAllowance") or {}
    candidate_allowance = candidate.get("classAllowance") or {}
    if class_allowance_is_disclosed(selected_allowance) and class_allowance_is_disclosed(candidate_allowance):
        if bool(selected_allowance.get("unlimited")) != bool(candidate_allowance.get("unlimited")):
            return None
        selected_count = numeric(selected_allowance.get("count"))
        candidate_count = numeric(candidate_allowance.get("count"))
        if selected_count is not None and candidate_count is not None and selected_count != candidate_count:
            return None

    selected_product_id = text(selected.get("sourceProductId"))
    candidate_product_id = text(candidate.get("sourceProductId"))
    if selected_product_id and candidate_product_id:
        if selected_product_id == candidate_product_id:
            return 100, "source-product-id"
        candidate_aliases = {
            text(value) for value in candidate.get("sourceProductAliases", []) if text(value)
        }
        if selected_product_id in candidate_aliases:
            return 95, "source-product-alias"
        if text(candidate.get("sourceProductIdAuthority")) != "synthetic-label":
            return None

    raw_label = plan_identity_label(
        candidate.get("name") or candidate.get("rawLabel"),
        candidate.get("amount"),
    )
    if not raw_label or len(MONEY_RE.findall(raw_label)) > 1:
        return None
    selected_labels = [
        text(selected.get("name")),
        text((selected.get("evidence") or {}).get("rawLabel")),
    ]
    candidate_normalized = normalized_label(raw_label).casefold()
    for label in selected_labels:
        normalized = normalized_label(label).casefold()
        if normalized and (normalized == candidate_normalized or normalized in candidate_normalized):
            return 90, "exact-plan-label"
    selected_combined = " ".join(selected_labels).casefold()
    if (
        MONTH_TO_MONTH_RE.search(selected_combined)
        and MONTH_TO_MONTH_RE.search(raw_label)
        and ("adult" not in selected_combined or re.search(r"\badult\b", raw_label, re.IGNORECASE))
    ):
        return 85, "plan-facets"
    selected_tokens = set().union(*(plan_label_tokens(label) for label in selected_labels))
    candidate_tokens = plan_label_tokens(raw_label)
    if not selected_tokens or not candidate_tokens:
        return None
    overlap = selected_tokens & candidate_tokens
    coverage = len(overlap) / len(selected_tokens)
    if overlap and coverage >= 0.6:
        return 70 + int(coverage * 10), "distinctive-label-tokens"
    return None


def selected_plan_commitment_changed(selected: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Detect a current minimum term that conflicts with the reviewed plan."""

    selected_commitment = selected.get("commitment") or {}
    candidate_commitment = candidate.get("commitment") or {}
    selected_type = text(selected_commitment.get("type")).casefold()
    candidate_type = text(candidate_commitment.get("type")).casefold()
    selected_months = numeric(selected_commitment.get("minimumMonths"))
    candidate_months = numeric(candidate_commitment.get("minimumMonths"))
    if selected_type in {"", "unknown"} or candidate_type in {"", "unknown"}:
        return False
    if selected_months is not None and candidate_months is not None:
        return selected_months != candidate_months
    selected_month_to_month = selected_type in {"month-to-month", "none"} and selected_months is None
    candidate_month_to_month = candidate_type in {"month-to-month", "none"} and candidate_months is None
    if selected_month_to_month and candidate_months is not None:
        return True
    if candidate_month_to_month and selected_months is not None:
        return True
    return False


def audit_selected_plan_price(
    gym: dict[str, Any],
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Audit the published selected plan against current matched candidates.

    Alternative plans never trigger the alert. A change is emitted only when
    the current candidate can be tied to the selected public product or a
    distinctive single-card label. Conflicting current variants fail closed as
    an ambiguity rather than asserting a price change.
    """

    published = numeric(gym.get("monthlyPrice"))
    selected_plan_id = text(gym.get("selectedPlanId"))
    if published is None or published <= 0 or not selected_plan_id:
        return None
    selected = next(
        (item for item in gym.get("plans", []) or [] if text(item.get("id")) == selected_plan_id),
        None,
    )
    if not isinstance(selected, dict):
        return {
            "status": "invalid-selected-plan",
            "selectedPlanId": selected_plan_id,
            "publishedMonthly": published,
        }
    selected_billing = selected.get("billing") or {}
    selected_raw_amount = numeric(selected_billing.get("amount"))
    selected_source_product_id = text(selected.get("sourceProductId"))
    selected_source_url_values = tuple(
        url
        for url in (
            text((selected.get("evidence") or {}).get("url")),
            text(gym.get("priceSourceUrl")),
        )
        if url
    )
    selected_urls = {
        request_identity(url) for url in selected_source_url_values
    }
    selected_public_platforms = {
        platform_name(url) for url in selected_source_url_values if platform_name(url) != "operator-site"
    }
    matches: list[dict[str, Any]] = []
    for candidate in observations:
        if text(candidate.get("gymId")) != text(gym.get("id")):
            continue
        source_url = text(candidate.get("sourceUrl"))
        match = selected_plan_candidate_match(selected, candidate)
        if selected_urls and source_url and request_identity(source_url) not in selected_urls:
            linked_public_catalog = (
                request_identity(text(candidate.get("catalogSourceUrl"))) in selected_urls
                and (
                    platform_name(source_url) != "operator-site"
                    or is_equinox_membership_api_url(source_url)
                )
            )
            stable_public_platform_product = (
                bool(selected_source_product_id)
                and text(candidate.get("sourceProductId")) == selected_source_product_id
                and platform_name(source_url) in selected_public_platforms
            )
            same_operator_strong_plan = bool(
                match
                and match[0] >= 85
                and not is_equinox_membership_api_url(source_url)
                and not is_bay_club_api_url(source_url)
                and any(same_operator_web_host(source_url, selected_url) for selected_url in selected_source_url_values)
                and operator_page_matches_gym(source_url, gym)
            )
            if not (linked_public_catalog or stable_public_platform_product or same_operator_strong_plan):
                continue
        promotion = candidate.get("promotion") or {}
        eligibility = candidate.get("eligibility") or {}
        if promotion.get("isPromotion") or text(eligibility.get("type")) in {
            "employee", "household", "military", "new-client", "online-only", "restricted",
            "senior", "student", "youth",
        }:
            continue
        amount = numeric(candidate.get("amount"))
        if amount is None or amount <= 0:
            continue
        if NON_STANDARD_COMPONENT_RE.search(text(candidate.get("rawLabel") or candidate.get("name"))):
            continue
        if (
            not match
            and selected_raw_amount is not None
            and abs(amount - selected_raw_amount) <= 0.01
            and class_allowances_match_exactly(selected, candidate)
        ):
            match = (60, "amount-and-class-allowance")
        if not match:
            continue
        score, method = match
        normalized_monthly = candidate_normalized_monthly(candidate)
        if selected_raw_amount is not None and abs(amount - selected_raw_amount) <= 0.01:
            normalized_monthly = published
        if normalized_monthly is None:
            continue
        matches.append({
            "score": score,
            "matchMethod": method,
            "sourceUrl": source_url,
            "sourceProductId": text(candidate.get("sourceProductId")),
            "candidateAmount": amount,
            "candidateCadence": text(candidate.get("cadence") or candidate.get("billingInterval")),
            "candidateNormalizedMonthly": normalized_monthly,
            "commitmentChanged": selected_plan_commitment_changed(selected, candidate),
            "candidateCommitment": candidate.get("commitment") or {},
        })
    if not matches:
        return {
            "status": "selected-plan-not-observed",
            "selectedPlanId": selected_plan_id,
            "publishedMonthly": published,
        }
    best_score = max(item["score"] for item in matches)
    best = [item for item in matches if item["score"] == best_score]
    unchanged_terms = [item for item in best if not item["commitmentChanged"]]
    if unchanged_terms:
        best = unchanged_terms
    distinct_amounts = sorted({round(item["candidateNormalizedMonthly"], 2) for item in best})
    if len(distinct_amounts) != 1:
        return {
            "status": "ambiguous-current-variants",
            "selectedPlanId": selected_plan_id,
            "publishedMonthly": published,
            "candidateNormalizedMonthlyValues": distinct_amounts,
            "matchMethod": best[0]["matchMethod"],
            "sourceUrl": best[0]["sourceUrl"],
        }
    current = distinct_amounts[0]
    relative_change = abs(current - published) / published
    terms_changed = all(item["commitmentChanged"] for item in best)
    evidence = {
        "status": (
            "selected-plan-terms-changed"
            if terms_changed
            else "changed-over-20-percent" if relative_change > 0.2
            else "matched-within-threshold"
        ),
        "selectedPlanId": selected_plan_id,
        "publishedMonthly": published,
        "candidateAmount": best[0]["candidateAmount"],
        "candidateCadence": best[0]["candidateCadence"],
        "candidateNormalizedMonthly": current,
        "relativeChange": round(relative_change, 4),
        "matchMethod": best[0]["matchMethod"],
        "sourceUrl": best[0]["sourceUrl"],
        "sourceProductId": best[0]["sourceProductId"],
    }
    if terms_changed:
        evidence["publishedCommitment"] = selected.get("commitment") or {}
        evidence["candidateCommitment"] = best[0]["candidateCommitment"]
    return evidence


def reconcile_selected_plan_price_audits(
    gyms: Iterable[dict[str, Any]],
    attempts: list[dict[str, Any]],
    observations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace page-wide price flags with selected-plan-aware audit evidence."""

    gym_list = list(gyms)
    gyms_by_id = {text(gym.get("id")): gym for gym in gym_list}
    observations_by_gym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observations_by_reviewed_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    observations_by_declared_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        gym_id = text(observation.get("gymId"))
        observations_by_gym[gym_id].append(observation)
        source_gym = gyms_by_id.get(gym_id, {})
        operator_id = text(source_gym.get("operatorId") or source_gym.get("operatorKey"))
        source_identity = request_identity(text(observation.get("sourceUrl")))
        if operator_id and source_identity:
            observations_by_reviewed_source[(operator_id, source_identity)].append(observation)
        declared_source_identity = request_identity(text(source_gym.get("priceSourceUrl")))
        if operator_id and declared_source_identity:
            observations_by_declared_source[(operator_id, declared_source_identity)].append(observation)
    attempts_by_gym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        if "requiresReviewBeforePriceAudit" in attempt:
            attempt["requiresReview"] = bool(attempt.pop("requiresReviewBeforePriceAudit"))
        attempt.pop("priceChangeEvidence", None)
        attempt.pop("selectedPlanPriceAuditStatus", None)
        attempt["priceChangeOver20Percent"] = False
        attempts_by_gym[text(attempt.get("gymId"))].append(attempt)
    for gym in gym_list:
        gym_attempts = attempts_by_gym.get(text(gym.get("id")), [])
        if not gym_attempts:
            continue
        gym_id = text(gym.get("id"))
        gym_observations = list(observations_by_gym.get(gym_id, []))
        operator_id = text(gym.get("operatorId") or gym.get("operatorKey"))
        reviewed_source = request_identity(text(gym.get("priceSourceUrl")))
        if operator_id and reviewed_source:
            shared_observations = (
                observations_by_reviewed_source.get((operator_id, reviewed_source), [])
                + observations_by_declared_source.get((operator_id, reviewed_source), [])
            )
            seen_shared_observations: set[int] = set()
            for observation in shared_observations:
                if id(observation) in seen_shared_observations:
                    continue
                seen_shared_observations.add(id(observation))
                if text(observation.get("gymId")) == gym_id:
                    continue
                gym_observations.append({**observation, "gymId": gym_id})
        audit = audit_selected_plan_price(gym, gym_observations)
        if not audit:
            continue
        root = next((item for item in gym_attempts if "reviewedSeedCount" in item), gym_attempts[0])
        root["selectedPlanPriceAuditStatus"] = audit["status"]
        if audit["status"] in {
            "invalid-selected-plan", "ambiguous-current-variants", "selected-plan-terms-changed",
        }:
            root["requiresReviewBeforePriceAudit"] = bool(root.get("requiresReview"))
            root["requiresReview"] = True
            root["priceChangeEvidence"] = audit
            continue
        if audit["status"] != "changed-over-20-percent":
            continue
        target_identity = request_identity(text(audit.get("sourceUrl")))
        target = next(
            (item for item in gym_attempts if request_identity(text(item.get("url"))) == target_identity),
            root,
        )
        target["requiresReviewBeforePriceAudit"] = bool(target.get("requiresReview"))
        target["priceChangeOver20Percent"] = True
        target["requiresReview"] = True
        target["priceChangeEvidence"] = audit
    return attempts


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
                result = fetch_page(url, timeout, conditional_cache_metadata(cache.get(url)))
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
    stale_cache = reusable_transient_cache(cache.get(url), result, url, gym)
    if result.get("status") == "not-modified":
        offers = list(cache.get(url, {}).get("candidates", []))
        storefronts = linked_storefronts(url, list(cache.get(url, {}).get("linkedStorefronts", [])), gym)
        location_candidates = list(cache.get(url, {}).get("locationCandidates", []))
        digest = text(cache.get(url, {}).get("contentHash"))
    elif stale_cache:
        offers, storefronts, location_candidates, digest, _captured_at = stale_cache
    attempted_at = today.date().isoformat()
    evidence_captured_at = stale_cache[4] if stale_cache else attempted_at
    previous_hash = text(cache.get(url, {}).get("contentHash"))
    attempts = [
        {
            "gymId": gym["id"],
            "name": gym["name"],
            "url": url,
            "attemptedAt": attempted_at,
            "status": result["status"],
            "accessBlocker": result.get("accessBlocker", ""),
            "robotsStatus": result.get("robotsStatus", ""),
            "contentHash": digest,
            "contentChanged": bool(previous_hash and digest and previous_hash != digest),
            "candidateCount": len(offers),
            "staleCacheReused": bool(stale_cache),
            "sharedResponse": bool(result.get("sharedResponse")),
            "linkedStorefronts": storefronts,
            "requiresReview": bool(offers),
            "priceChangeOver20Percent": False,
        }
    ]
    observations = [
        {"gymId": gym["id"], "gymName": gym["name"], "capturedAt": evidence_captured_at, **offer, "catalogSourceUrl": url}
        for offer in offers
    ]
    location_observations = [{"gymId": gym["id"], "gymName": gym["name"], "capturedAt": attempted_at, **candidate} for candidate in location_candidates]
    updates = {
        url: {
            "status": result["status"],
            "accessBlocker": result.get("accessBlocker", ""),
            "lastAttemptAt": attempted_at,
            "etag": result.get("etag", ""),
            "lastModified": result.get("lastModified", ""),
            "parserVersion": PARSER_VERSION,
            "contentHash": digest,
            "candidates": offers,
            "linkedStorefronts": storefronts,
            "locationCandidates": location_candidates,
        }
    }
    pending: list[tuple[str, str, int, str]] = [
        (route["url"], f"reviewed-record:{route['sourceField']}", 1, route["url"])
        for route in seed_routes[1:]
    ]
    pending.extend((storefront, url, 1, url) for storefront in storefronts)
    visited: set[str] = {request_identity(url)}
    operator_request_count = int(platform_name(url) == "operator-site")
    booking_request_count = int(platform_name(url) != "operator-site")
    frontier_skip_reasons: dict[str, int] = defaultdict(int)
    while pending and len(visited) < MAX_LINKED_REQUESTS_PER_GYM:
        storefront, linked_from, depth, catalog_source_url = pending.pop(0)
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
        store_stale_cache = reusable_transient_cache(
            cache.get(storefront), store_result, storefront, gym,
        )
        if store_result.get("status") == "not-modified":
            store_offers = list(cache.get(storefront, {}).get("candidates", []))
            nested = linked_storefronts(
                storefront,
                list(cache.get(storefront, {}).get("linkedStorefronts", [])),
                gym,
            )
            store_location_candidates = list(cache.get(storefront, {}).get("locationCandidates", []))
            store_digest = text(cache.get(storefront, {}).get("contentHash"))
        elif store_stale_cache:
            store_offers, nested, store_location_candidates, store_digest, _store_captured_at = store_stale_cache
        store_evidence_captured_at = store_stale_cache[4] if store_stale_cache else attempted_at
        attempts.append(
            {
                "gymId": gym["id"],
                "name": gym["name"],
                "url": storefront,
                "attemptedAt": attempted_at,
                "status": store_result["status"],
                "accessBlocker": store_result.get("accessBlocker", ""),
                "robotsStatus": store_result.get("robotsStatus", ""),
                "contentHash": store_digest,
                "contentChanged": bool(text(cache.get(storefront, {}).get("contentHash")) and store_digest and text(cache.get(storefront, {}).get("contentHash")) != store_digest),
                "candidateCount": len(store_offers),
                "staleCacheReused": bool(store_stale_cache),
                "sharedResponse": bool(store_result.get("sharedResponse")),
                "linkedFrom": linked_from,
                "linkDepth": depth,
                "requiresReview": bool(store_offers),
                "priceChangeOver20Percent": False,
            }
        )
        observations.extend(
            {
                "gymId": gym["id"], "gymName": gym["name"], "capturedAt": store_evidence_captured_at,
                **offer, "catalogSourceUrl": catalog_source_url,
            }
            for offer in store_offers
        )
        location_observations.extend(
            {"gymId": gym["id"], "gymName": gym["name"], "capturedAt": store_evidence_captured_at, **candidate}
            for candidate in store_location_candidates
        )
        updates[storefront] = {
            "status": store_result["status"],
            "accessBlocker": store_result.get("accessBlocker", ""),
            "lastAttemptAt": attempted_at,
            "etag": store_result.get("etag", ""),
            "lastModified": store_result.get("lastModified", ""),
            "parserVersion": PARSER_VERSION,
            "contentHash": store_digest,
            "candidates": store_offers,
            "linkedStorefronts": nested,
            "locationCandidates": store_location_candidates,
        }
        if may_follow_nested_catalog(storefront, depth):
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
                    pending.append((detail_url, storefront, depth + 1, catalog_source_url))
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
    parser.add_argument(
        "--reconcile-only",
        action="store_true",
        help="Recompute selected-plan price alerts from retained evidence without network requests",
    )
    args = parser.parse_args()
    today = datetime.fromisoformat(args.date) if args.date else datetime.now(UTC).replace(tzinfo=None)
    document = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    candidates = [gym for gym in document.get("gyms", []) if should_crawl(gym, cache, args.mode, today)]
    if args.reconcile_only:
        candidates = []
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
            for url, update in cache_updates.items():
                cache[url] = merge_transient_cache_entry(cache.get(url), update)
    run_attempts = list(attempts)
    crawled_gym_ids = {text(gym.get("id")) for gym in candidates}
    attempts_by_key = {
        (text(item.get("gymId")), text(item.get("url"))): item
        for item in existing_attempts_document.get("attempts", [])
        if text(item.get("gymId")) not in crawled_gym_ids
    }
    attempts_by_key.update({(text(item.get("gymId")), text(item.get("url"))): item for item in attempts})
    attempts = sorted(attempts_by_key.values(), key=lambda item: (text(item.get("gymId")), text(item.get("url"))))
    observations = merge_crawl_observations(
        existing_observations_document.get("observations", []),
        observations,
        crawled_gym_ids,
        run_attempts,
    )
    price_audit_observations = observations + load_rendered_deal_observations()
    attempts = reconcile_selected_plan_price_audits(
        document.get("gyms", []),
        attempts,
        price_audit_observations,
    )
    if args.reconcile_only:
        existing_deals_document = (
            json.loads(DEAL_OBSERVATIONS_PATH.read_text(encoding="utf-8"))
            if DEAL_OBSERVATIONS_PATH.exists()
            else {"deals": []}
        )
        save_json(ATTEMPTS_PATH, sanitize_persisted_value({
            "generatedAt": text(existing_attempts_document.get("generatedAt")) or today.date().isoformat(),
            "mode": text(existing_attempts_document.get("mode")) or args.mode,
            "attempts": attempts,
        }))
        print(json.dumps({
            "candidateGyms": 0,
            "logicalRequests": 0,
            "physicalRequests": 0,
            "sharedResponseReuses": 0,
            "observations": len(observations),
            "dealCandidates": len(existing_deals_document.get("deals", [])),
            "reviewRequired": 0,
            "sourceStatusReviews": 0,
        }))
        return 0
    location_observations = merge_crawl_observations(
        existing_locations_document.get("observations", []),
        location_observations,
        crawled_gym_ids,
        run_attempts,
    )
    save_json(CACHE_PATH, cache_for_persistence(cache))
    save_json(ATTEMPTS_PATH, sanitize_persisted_value({
        "generatedAt": today.date().isoformat(), "mode": args.mode, "attempts": attempts,
    }))
    save_json(OBSERVATIONS_PATH, sanitize_persisted_value({
        "generatedAt": today.date().isoformat(), "observations": observations,
    }))
    save_json(LOCATION_OBSERVATIONS_PATH, sanitize_persisted_value({
        "generatedAt": today.date().isoformat(), "observations": location_observations,
    }))
    eligible_deal_ids = {
        text(gym.get("id")) for gym in document.get("gyms", []) if deal_eligible_gym(gym)
    }
    deals = deal_candidates(
        observations + load_rendered_deal_observations(),
        eligible_deal_ids,
    )
    save_json(DEAL_OBSERVATIONS_PATH, sanitize_persisted_value({
        "generatedAt": today.date().isoformat(), "mode": args.mode, "deals": deals,
    }))
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
