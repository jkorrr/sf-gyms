"""Rendered, review-only fallback for JavaScript pricing pages.

This stage is deliberately separate from the static crawler. It opens only
committed operator URLs, may activate public pricing/package tabs, and records
short candidate labels plus hashes. It never fills or submits a form, creates
an account, authenticates, or auto-publishes a price.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

import crawl_official_sources as static_crawler

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
STATIC_ATTEMPTS_PATH = ROOT / "data" / "imports" / "official-crawl-attempts.json"
STATIC_OBSERVATIONS_PATH = ROOT / "data" / "imports" / "official-crawl-observations.json"
ATTEMPTS_PATH = ROOT / "data" / "imports" / "rendered-crawl-attempts.json"
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "rendered-crawl-observations.json"
MAX_JSON_BYTES = 4_000_000
PUBLIC_TAB_LABELS = {
    "membership", "memberships", "package", "packages", "pricing", "rates", "passes", "personal training",
    "monthly", "month-to-month", "flexible", "6-month", "12-month",
}
NEUTRAL_TERM_TAB_LABELS = ("monthly", "month-to-month", "flexible")
PRICE_CARD_SELECTOR = "article, [role='listitem'], [class*='price' i], [class*='plan' i], [class*='membership' i], [class*='package' i]"
PUBLIC_TAB_SELECTOR = "button, [role='tab'], a, label[for]"
ACCESS_BLOCK_COOLDOWN_DAYS = 28
RENDER_RESEARCH_PATH_RE = re.compile(
    r"/(?:pricing|prices|pricespolicies|rates?|memberships?|plans?|packages?|passes|drop-?in|buy|join)(?:/|$|[?#])",
    re.IGNORECASE,
)


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def host(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold()
    except ValueError:
        return ""


def allowed_network_response(operator_url: str, response_url: str) -> bool:
    operator_host = host(operator_url)
    response_host = host(response_url)
    if not operator_host or not response_host:
        return False
    if response_host == operator_host or response_host.endswith(f".{operator_host}"):
        return True
    return any(
        response_host == domain or response_host.endswith(f".{domain}")
        for domain in static_crawler.BOOKING_DOMAINS
        if "classpass.com" not in domain
    )


def is_safe_public_tab_label(label: str) -> bool:
    return " ".join(text(label).casefold().split()) in PUBLIC_TAB_LABELS


def is_safe_mindbody_category_label(label: str) -> bool:
    """Bound selector traversal to public product categories, never account actions."""

    return static_crawler.is_safe_mindbody_category_label(label)


def safe_mindbody_contract_href(current_url: str, href: str) -> bool:
    """Allow only Mindbody's public Contracts tab, never account/cart routes."""

    try:
        current = urlparse(current_url)
        target = urlparse(urljoin(current_url, href))
    except ValueError:
        return False
    query = {key.casefold(): values for key, values in parse_qs(target.query).items()}
    return bool(
        current.netloc.casefold() == target.netloc.casefold()
        and target.path.casefold().endswith("/asp/main_shop.asp")
        and query.get("pmode") == ["0"]
    )


def safe_public_tab_href(current_url: str, href: str) -> bool:
    """Allow in-page public tabs, never let a navigation label change pages."""

    if not text(href):
        return True
    try:
        current = urldefrag(current_url)[0]
        target = urldefrag(urljoin(current_url, href))[0]
    except ValueError:
        return False
    return bool(current and target and current == target)


def score_location_label(gym: dict[str, Any], label: str) -> int:
    """Score a public location selector without inventing geospatial matches."""

    candidate = " ".join(re.findall(r"[a-z0-9]+", text(label).casefold()))
    if not candidate:
        return 0
    address = " ".join(re.findall(r"[a-z0-9]+", text(gym.get("address")).casefold()))
    name = " ".join(re.findall(r"[a-z0-9]+", text(gym.get("name")).casefold()))
    address_tokens = [token for token in address.split() if len(token) >= 3 or token.isdigit()]
    name_tokens = [token for token in name.split() if len(token) >= 4 and token not in {"fitness", "studio", "climbing", "training"}]
    score = sum(2 for token in address_tokens if token in candidate)
    score += sum(1 for token in name_tokens if token in candidate)
    street_number = next((token for token in address_tokens if token.isdigit()), "")
    if street_number and street_number in candidate:
        score += 8
    if "san francisco" in candidate:
        score += 4
    return score


def detect_access_blocker(title: str, visible_text: str, html: str = "") -> str:
    """Classify strong public-page access-control signals without bypassing them."""

    sample = " ".join(f"{title} {visible_text[:8_000]} {html[:8_000]}".casefold().split())
    cloudflare_signal = "cloudflare" in sample or "cf-chl-" in sample or "challenge-platform" in sample
    if cloudflare_signal and any(
        phrase in sample
        for phrase in ("security check", "verify you are human", "performing security verification", "just a moment")
    ):
        return "platform-security-check"
    if "captcha" in sample and any(phrase in sample for phrase in ("verify", "security", "human")):
        return "captcha-required"
    if re.search(r"\b(?:sign in|log in)\b", title, re.IGNORECASE) and not re.search(
        r"\b(?:price|pricing|membership|package|plan)\b", visible_text, re.IGNORECASE
    ):
        return "authentication-required"
    return ""


def remove_unattached_crunch_promotions(candidates: list[dict[str, Any]], source_url: str) -> list[dict[str, Any]]:
    """Prefer complete Crunch card candidates over detached summary dues."""

    if not host(source_url).endswith("crunch.com"):
        return candidates
    attached_promotion_amounts = {
        float(candidate.get("amount") or 0)
        for candidate in candidates
        if text(candidate.get("sourceProductId")).endswith("-current-offer")
        and (candidate.get("promotion") or {}).get("isPromotion")
    }
    if not attached_promotion_amounts:
        return candidates
    return [
        candidate
        for candidate in candidates
        if candidate.get("sourceProductId")
        or candidate.get("productType") != "monthly"
        or float(candidate.get("amount") or 0) not in attached_promotion_amounts
    ]


def access_block_is_current(attempted_at: str, as_of: str, cooldown_days: int = ACCESS_BLOCK_COOLDOWN_DAYS) -> bool:
    try:
        attempted = datetime.fromisoformat(text(attempted_at)[:10]).date()
        current = datetime.fromisoformat(text(as_of)[:10]).date()
    except ValueError:
        return False
    return 0 <= (current - attempted).days < cooldown_days


def render_target_urls(gym: dict[str, Any], attempts: list[dict[str, Any]]) -> list[str]:
    """Return reviewed operator and storefront targets for rendered recovery."""

    seeds = [
        text(gym.get("websiteUrl")),
        text(gym.get("officialUrl")),
        text(gym.get("priceSourceUrl")),
    ]
    operator_hosts = {host(value) for value in seeds if static_crawler.is_public_http_url(value)}
    official_path = urlparse(text(gym.get("officialUrl"))).path.casefold()
    location_slugs = [
        segment
        for segment in official_path.split("/")
        if len(segment) >= 4
        and segment not in {"location", "locations", "pricing", "prices", "membership", "memberships", "schedule", "san-francisco"}
    ]
    stable_location_slug = max(location_slugs, key=len, default="")
    values = list(seeds)
    for attempt in attempts:
        attempt_url = text(attempt.get("url"))
        if text(attempt.get("status")) in {"robots-disallowed", "host-backoff-after-429"}:
            continue
        is_platform = bool(static_crawler.platform_adapters.platform_for_url(attempt_url))
        parsed = urlparse(attempt_url) if static_crawler.is_public_http_url(attempt_url) else None
        is_operator_research = bool(
            parsed
            and host(attempt_url) in operator_hosts
            and RENDER_RESEARCH_PATH_RE.search(parsed.path + ("?" + parsed.query if parsed.query else ""))
            and (not stable_location_slug or stable_location_slug in parsed.path.casefold())
        )
        if is_platform or is_operator_research:
            values.append(attempt_url)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not static_crawler.is_public_http_url(value):
            continue
        normalized_url = urldefrag(value)[0]
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        result.append(normalized_url)
    return result[:12]


def candidate_gyms(
    document: dict[str, Any],
    attempts_document: dict[str, Any],
    mode: str = "weekly",
    rendered_attempts_document: dict[str, Any] | None = None,
    as_of: str = "",
) -> list[dict[str, Any]]:
    attempts_by_gym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts_document.get("attempts", []):
        attempts_by_gym[text(attempt.get("gymId"))].append(attempt)
    blocked_render_keys = {
        (text(attempt.get("gymId")), text(attempt.get("url")))
        for attempt in (rendered_attempts_document or {}).get("attempts", [])
        if mode != "full"
        and text(attempt.get("status")) == "access-blocked"
        and access_block_is_current(text(attempt.get("attemptedAt")), as_of)
    }
    candidates = []
    for gym in document.get("gyms", []):
        url = text(gym.get("websiteUrl"))
        if not static_crawler.is_public_http_url(url) or static_crawler.coverage.is_osm_url(url):
            continue
        restricted_public_catalog = any(
            static_crawler.platform_adapters.platform_for_url(text(value))
            for value in (gym.get("websiteUrl"), gym.get("officialUrl"), gym.get("priceSourceUrl"))
            if text(value)
        )
        if (
            gym.get("publicationStatus") != "publish"
            or gym.get("accessModel") in {"free-public", "not-applicable"}
            or (gym.get("accessModel") == "restricted" and not restricted_public_catalog)
        ):
            continue
        if static_crawler.deal_eligible_gym(gym):
            candidates.append(gym)
            continue
        if mode == "deals":
            continue
        needs_price_recovery = gym.get("monthlyPrice") is None or gym.get("officialPriceConflict") or gym.get("freshness") == "stale"
        if not needs_price_recovery:
            continue
        prior = attempts_by_gym.get(text(gym.get("id")), [])
        static_empty = not prior or all(int(item.get("candidateCount", 0) or 0) == 0 for item in prior)
        static_failed = any(text(item.get("status")) not in {"fetched", "not-modified"} for item in prior)
        if static_empty or static_failed or gym.get("officialPriceConflict") or gym.get("monthlyPrice") is None:
            candidates.append(gym)
    expanded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for gym in candidates:
        render_urls = render_target_urls(gym, attempts_by_gym.get(text(gym.get("id")), []))
        for render_url in render_urls:
            key = (text(gym.get("id")), render_url)
            if not render_url or key in seen or key in blocked_render_keys:
                continue
            seen.add(key)
            expanded.append({**gym, "websiteUrl": render_url, "renderSourceUrl": text(gym.get("websiteUrl"))})
    return sorted(
        expanded,
        key=lambda gym: (
            gym.get("recordStatus") == "coming_soon",
            gym.get("monthlyPrice") is not None,
            text(gym.get("operatorKey")),
            text(gym.get("name")),
            text(gym.get("websiteUrl")),
        ),
    )


def rendered_observation(
    gym: dict[str, Any],
    candidate: dict[str, Any],
    attempted_at: str,
    catalog_source_url: str,
) -> dict[str, Any]:
    """Attach the reviewed render target to redirected public evidence."""

    observation = {
        "gymId": gym["id"],
        "gymName": gym["name"],
        "capturedAt": attempted_at,
        **candidate,
    }
    observation["catalogSourceUrl"] = text(candidate.get("catalogSourceUrl")) or catalog_source_url
    return observation


def render_gym(browser: Any, gym: dict[str, Any], attempted_at: str, timeout_ms: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = text(gym.get("websiteUrl"))
    allowed, robots_status = static_crawler.robots_allowed(url, timeout_ms / 1000)
    if not allowed:
        return {
            "gymId": gym["id"], "name": gym["name"], "url": url, "attemptedAt": attempted_at,
            "status": "robots-disallowed", "robotsStatus": robots_status, "candidateCount": 0, "requiresReview": False,
        }, []

    context = browser.new_context(java_script_enabled=True, service_workers="block")
    page = context.new_page()
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in {"image", "media", "font"}
        else route.continue_(),
    )
    network_candidates: list[dict[str, Any]] = []
    network_hashes: list[str] = []

    def capture_response(response: Any) -> None:
        response_url = text(response.url)
        if not allowed_network_response(url, response_url):
            return
        content_type = text(response.headers.get("content-type")).casefold()
        if "json" not in content_type:
            return
        try:
            body = response.body()
        except Exception:
            return
        if not body or len(body) > MAX_JSON_BYTES:
            return
        try:
            payload = body.decode("utf-8", errors="strict")
            parsed = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        network_hashes.append(hashlib.sha256(body).hexdigest())
        platform_candidates, _nested = static_crawler.public_platform_json_candidates(parsed, response_url, gym)
        if platform_candidates:
            network_candidates.extend(platform_candidates)
        else:
            network_candidates.extend(static_crawler.structured_candidates([payload], response_url, "rendered-public-json"))

    page.on("response", capture_response)
    status = "rendered"
    error = ""
    html = ""
    visible = ""
    visible_sources: list[tuple[str, str]] = []
    visible_card_sources: list[tuple[str, str]] = []
    platform_card_candidates: list[dict[str, Any]] = []
    clicked_tabs: list[str] = []
    access_blocker = ""
    page_title = ""

    def capture_visible_state() -> str:
        try:
            current_visible = page.locator("body").inner_text(timeout=timeout_ms)
        except Exception:
            return ""
        visible_sources.append((page.url, current_visible))
        for card in page.locator(PRICE_CARD_SELECTOR).all()[:250]:
            try:
                if not card.is_visible():
                    continue
                card_text = "\n".join(line.strip() for line in card.inner_text(timeout=300).splitlines() if line.strip())
                if "$" in card_text and 8 <= len(card_text) <= 1_500:
                    visible_card_sources.append((page.url, card_text))
            except Exception:
                continue
        return current_visible

    try:
        page.goto(url, wait_until="commit", timeout=timeout_ms)
        # Crunch hydrates regular rates and plan-linked fee tables after its
        # summary prices. Waiting for that operator-owned DOM prevents the
        # early summary amounts from being mistaken for complete plan cards.
        platform = static_crawler.platform_name(url)
        dynamic_platform = platform in {"approach", "mariana-tek", "xponential-member-app", "mindbody", "jane"}
        page.wait_for_timeout(
            4000 if dynamic_platform or host(url).endswith(("crunch.com", "orangetheory.com", "solidcore.co")) else 1500
        )
        if static_crawler.platform_name(url) == "approach":
            choices: list[tuple[int, Any]] = []
            for button in page.locator("button").all()[:100]:
                try:
                    if button.is_visible():
                        choices.append((score_location_label(gym, button.inner_text(timeout=300)), button))
                except Exception:
                    continue
            best_score, best_button = max(choices, key=lambda item: item[0], default=(0, None))
            if best_button is not None and best_score >= 8:
                best_button.click(timeout=1000)
                page.wait_for_timeout(300)
                for save in page.locator("button").all()[:100]:
                    try:
                        if re.fullmatch(r"\s*Save\s*", save.inner_text(timeout=300), re.IGNORECASE) and save.is_visible() and save.is_enabled():
                            save.click(timeout=1000)
                            page.wait_for_timeout(1000)
                            break
                    except Exception:
                        continue
        for locator in page.locator(PUBLIC_TAB_SELECTOR).all()[:150]:
            try:
                label = " ".join(locator.inner_text(timeout=300).split())
                if is_safe_public_tab_label(label) and locator.is_visible():
                    href = text(locator.get_attribute("href"))
                    if not safe_public_tab_href(page.url, href):
                        continue
                    locator.click(timeout=1000)
                    page.wait_for_timeout(500)
                    clicked_tabs.append(label)
                    capture_visible_state()
            except Exception:
                continue
        # React tab controls can invalidate handles collected before the first
        # click. Reacquire the controls and finish on a neutral no-term option
        # so the selected-plan audit never treats a commitment discount as the
        # ordinary month-to-month price.
        neutral_tab_activated = False
        for neutral_label in NEUTRAL_TERM_TAB_LABELS:
            for locator in page.locator(PUBLIC_TAB_SELECTOR).all()[:150]:
                try:
                    label = " ".join(locator.inner_text(timeout=300).split())
                    if label.casefold() != neutral_label or not locator.is_visible():
                        continue
                    href = text(locator.get_attribute("href"))
                    if not safe_public_tab_href(page.url, href):
                        continue
                    locator.click(timeout=1000)
                    page.wait_for_timeout(750)
                    clicked_tabs.append(label)
                    capture_visible_state()
                    neutral_tab_activated = True
                    break
                except Exception:
                    continue
            if neutral_tab_activated:
                break
        if platform == "mindbody":
            category_options: list[tuple[str, str]] = []
            try:
                first_select = page.locator("select").first
                for option in first_select.locator("option").all()[:80]:
                    label = " ".join(option.inner_text(timeout=300).split())
                    value = text(option.get_attribute("value"))
                    if value and is_safe_mindbody_category_label(label):
                        category_options.append((label, value))
            except Exception:
                category_options = []
            for category_label, value in category_options[:20]:
                try:
                    page.locator("select").first.select_option(value=value, timeout=1500)
                    page.wait_for_timeout(800)
                    clicked_tabs.append(category_label)
                    for row in page.locator("li:has(.purchaseItem)").all()[:100]:
                        if not row.is_visible():
                            continue
                        card_text = "\n".join(line.strip() for line in row.inner_text(timeout=500).splitlines() if line.strip())
                        product = row.locator(".purchaseItem").first
                        product_id = text(product.get_attribute("value"))
                        platform_card_candidates.extend(
                            static_crawler.platform_adapters.mindbody_purchase_item_candidates(
                                category_label, card_text, page.url, product_id
                            )
                        )
                except Exception:
                    continue
            contract_href = ""
            for link in page.locator("a").all()[:100]:
                try:
                    if " ".join(link.inner_text(timeout=300).split()).casefold() != "contracts":
                        continue
                    href = text(link.get_attribute("href"))
                    if safe_mindbody_contract_href(page.url, href):
                        contract_href = urljoin(page.url, href)
                        break
                except Exception:
                    continue
            if contract_href:
                try:
                    page.goto(contract_href, wait_until="commit", timeout=timeout_ms)
                    page.wait_for_timeout(900)
                    clicked_tabs.append("Contracts")
                    contract_select = page.locator("select").first
                    contract_options: list[tuple[str, str]] = []
                    for option in contract_select.locator("option").all()[:80]:
                        label = " ".join(option.inner_text(timeout=300).split())
                        value = text(option.get_attribute("value"))
                        if value and value != "0" and is_safe_mindbody_category_label(label):
                            contract_options.append((label, value))
                    for contract_label, value in contract_options[:20]:
                        contract_select.select_option(value=value, timeout=1500)
                        page.wait_for_timeout(800)
                        contract_text = page.locator("body").inner_text(timeout=1500)
                        platform_card_candidates.extend(
                            static_crawler.platform_adapters.mindbody_contract_candidates(
                                contract_label, contract_text, page.url, value
                            )
                        )
                except Exception:
                    pass
        if platform == "jane":
            for service in page.locator("a[href*='/treatment/']").all()[:200]:
                try:
                    if not service.is_visible():
                        continue
                    platform_card_candidates.extend(
                        static_crawler.platform_adapters.jane_service_card_candidates(
                            service.inner_text(timeout=500), page.url, text(service.get_attribute("href"))
                        )
                    )
                except Exception:
                    continue
        visible = capture_visible_state()
        page_title = page.title()
        for frame in page.frames:
            if frame == page.main_frame or not allowed_network_response(url, text(frame.url)):
                continue
            try:
                frame_text = frame.locator("body").inner_text(timeout=1500)
            except Exception:
                continue
            if frame_text.strip():
                visible_sources.append((text(frame.url), frame_text))
        html = page.content()
        access_blocker = detect_access_blocker(page_title, visible, html)
        if access_blocker:
            status = "access-blocked"
            network_candidates.clear()
            network_hashes.clear()
            visible_sources.clear()
            visible_card_sources.clear()
    except Exception as exc:
        status = "render-error"
        error = text(exc)[:240]
    finally:
        context.close()

    dom_candidates: list[dict[str, Any]] = []
    for visible_url, visible_text in visible_sources:
        dom_candidates.extend(static_crawler.visible_candidates(visible_text, visible_url))
        dom_candidates.extend(static_crawler.bookee_visible_candidates(visible_text, visible_url))
    for visible_url, card_text in visible_card_sources:
        for candidate in static_crawler.visible_candidates(card_text, visible_url):
            candidate["method"] = (
                "rendered-visible-cost-context"
                if candidate.get("kind") in {"range", "starting-price"}
                else "rendered-visible-plan-card"
            )
            candidate["cardAssociationHash"] = hashlib.sha256(card_text.encode("utf-8")).hexdigest()
            dom_candidates.append(candidate)
    dom_candidates = remove_unattached_crunch_promotions(dom_candidates, url)
    observations = network_candidates + platform_card_candidates + dom_candidates
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in observations:
        key = (
            candidate.get("kind"), candidate.get("low"), candidate.get("high"), candidate.get("amount"),
            candidate.get("productType"), candidate.get("rawLabel"), candidate.get("sourceUrl"),
        )
        if key not in seen:
            seen.add(key)
            observation = rendered_observation(gym, candidate, attempted_at, url)
            # Preserve the exact committed/render target that authorized a
            # redirected frame or approved booking/API response. The selected-
            # plan audit still requires a semantic product/label match and an
            # approved public platform, so this cannot turn detached amounts
            # into plan confirmations.
            deduplicated.append(observation)
    attempt = {
        "gymId": gym["id"],
        "name": gym["name"],
        "url": url,
        "attemptedAt": attempted_at,
        "status": status,
        "robotsStatus": robots_status,
        "contentHash": hashlib.sha256(html.encode("utf-8")).hexdigest() if html else "",
        "networkEvidenceHashes": sorted(set(network_hashes)),
        "clickedPublicTabs": sorted(set(clicked_tabs)),
        "candidateCount": len(deduplicated),
        "requiresReview": bool(deduplicated),
        "accessBlocker": access_blocker,
        "error": error,
        "policy": "Review candidates only; no forms, authentication, contact data, or automatic publication.",
    }
    return attempt, deduplicated


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_incremental_results(
    existing_attempts: list[dict[str, Any]],
    existing_observations: list[dict[str, Any]],
    new_attempts: list[dict[str, Any]],
    new_observations: list[dict[str, Any]],
    processed_gym_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace evidence only for processed gyms so partial weekly runs retain audit history."""

    attempts_by_key = {
        (text(item.get("gymId")), text(item.get("url"))): item
        for item in existing_attempts
        if text(item.get("gymId")) not in processed_gym_ids
    }
    attempts_by_key.update({(text(item.get("gymId")), text(item.get("url"))): item for item in new_attempts})
    attempts = sorted(attempts_by_key.values(), key=lambda item: (text(item.get("gymId")), text(item.get("url"))))
    combined_observations = [
        item for item in existing_observations
        if text(item.get("gymId")) not in processed_gym_ids
    ] + new_observations
    observations: list[dict[str, Any]] = []
    observation_keys: set[tuple[Any, ...]] = set()
    for item in combined_observations:
        key = (
            text(item.get("gymId")), text(item.get("sourceUrl")), text(item.get("sourceProductId")),
            text(item.get("kind")), float(item.get("low", 0) or 0), float(item.get("high", 0) or 0),
            float(item.get("amount", 0) or 0), text(item.get("productType")), text(item.get("rawLabel")),
        )
        if key in observation_keys:
            continue
        observation_keys.add(key)
        observations.append(item)
    observations.sort(
        key=lambda item: (
            text(item.get("gymId")), text(item.get("sourceUrl")),
            text(item.get("kind")), float(item.get("low", 0) or 0),
            float(item.get("amount", 0) or 0), text(item.get("rawLabel")),
        )
    )
    return attempts, observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gym-id", action="append", default=[], help="Render only the specified stable gym ID; may be repeated")
    parser.add_argument("--mode", choices=("deals", "weekly", "full"), default="weekly")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--date", help="Override attempt date")
    args = parser.parse_args()
    attempted_at = args.date or datetime.now(UTC).date().isoformat()
    document = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    static_attempts = json.loads(STATIC_ATTEMPTS_PATH.read_text(encoding="utf-8")) if STATIC_ATTEMPTS_PATH.exists() else {"attempts": []}
    existing_attempts_document = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8")) if ATTEMPTS_PATH.exists() else {"attempts": []}
    existing_observations_document = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8")) if OBSERVATIONS_PATH.exists() else {"observations": []}
    gyms = candidate_gyms(
        document,
        static_attempts,
        args.mode,
        existing_attempts_document,
        attempted_at,
    )
    if args.gym_id:
        requested_ids = set(args.gym_id)
        gyms = [gym for gym in gyms if text(gym.get("id")) in requested_ids]
    if args.limit:
        gyms = gyms[: args.limit]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - CI installs the optional rendered dependency.
        raise SystemExit("Rendered crawl requires Playwright: pip install playwright && python -m playwright install chromium") from exc

    attempts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    last_host_request: dict[str, float] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for gym in gyms:
                gym_host = host(text(gym.get("websiteUrl")))
                wait_for = static_crawler.DOMAIN_DELAY_SECONDS - (time.monotonic() - last_host_request.get(gym_host, 0))
                if wait_for > 0:
                    time.sleep(wait_for)
                attempt, candidates = render_gym(browser, gym, attempted_at, args.timeout_ms)
                last_host_request[gym_host] = time.monotonic()
                attempts.append(attempt)
                observations.extend(candidates)
                if len(attempts) % 10 == 0:
                    checkpoint_ids = {text(item.get("gymId")) for item in attempts}
                    checkpoint_attempts, checkpoint_observations = merge_incremental_results(
                        existing_attempts_document.get("attempts", []),
                        existing_observations_document.get("observations", []),
                        attempts,
                        observations,
                        checkpoint_ids,
                    )
                    save_json(ATTEMPTS_PATH, {"generatedAt": attempted_at, "mode": args.mode, "attempts": checkpoint_attempts})
                    save_json(OBSERVATIONS_PATH, {"generatedAt": attempted_at, "mode": args.mode, "observations": checkpoint_observations})
        finally:
            browser.close()
    run_attempts = attempts
    run_observations = observations
    processed_gym_ids = {text(gym.get("id")) for gym in gyms}
    attempts, observations = merge_incremental_results(
        existing_attempts_document.get("attempts", []),
        existing_observations_document.get("observations", []),
        run_attempts,
        run_observations,
        processed_gym_ids,
    )
    save_json(ATTEMPTS_PATH, {"generatedAt": attempted_at, "mode": args.mode, "attempts": attempts})
    save_json(OBSERVATIONS_PATH, {"generatedAt": attempted_at, "mode": args.mode, "observations": observations})
    static_observations_document = (
        json.loads(STATIC_OBSERVATIONS_PATH.read_text(encoding="utf-8"))
        if STATIC_OBSERVATIONS_PATH.exists()
        else {"observations": []}
    )
    eligible_deal_ids = {
        text(gym.get("id")) for gym in document.get("gyms", []) if static_crawler.deal_eligible_gym(gym)
    }
    deals = static_crawler.deal_candidates(
        static_observations_document.get("observations", []) + observations,
        eligible_deal_ids,
    )
    save_json(static_crawler.DEAL_OBSERVATIONS_PATH, {
        "generatedAt": attempted_at,
        "mode": args.mode,
        "deals": deals,
    })
    save_json(static_crawler.DEAL_REPORT_PATH, {
        "generatedAt": attempted_at,
        "mode": args.mode,
        "dealCandidateCount": len(deals),
        "locationCount": len({item["gymId"] for item in deals}),
        "reviewRequiredCount": sum(item["reviewStatus"] == "pending" for item in deals),
        "ordinaryPricesRemainAuthoritative": True,
        "includesRenderedEvidence": True,
    })
    print(json.dumps({"candidateGyms": len(gyms), "attempts": len(run_attempts), "observations": len(run_observations), "dealCandidates": len(deals), "reviewRequired": sum(bool(item.get("requiresReview")) for item in run_attempts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
