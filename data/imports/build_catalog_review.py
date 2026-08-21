"""Build human-review packets from static and rendered catalog observations.

The crawler is intentionally discovery-only.  This module groups high-quality
plan-card and platform-JSON observations into catalog proposals while keeping
loose price-shaped text in a rejected-evidence appendix.  Nothing produced by
this file is consumed by the published fixture until a reviewer explicitly
approves it with ``review_catalogs.py``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = ROOT / "sf-gyms-osm.json"
STATIC_PATH = ROOT / "official-crawl-observations.json"
RENDERED_PATH = ROOT / "rendered-crawl-observations.json"
OUTPUT_PATH = ROOT / "official-catalog-review.json"
APPROVED_PATH = ROOT / "official-crawl-approved.json"

STRUCTURED_METHOD_PREFIXES = (
    "public-", "json-ld", "rendered-visible-plan-card",
    "visible-cost-context", "rendered-visible-cost-context",
)
CARD_SEMANTIC_RE = re.compile(
    r"\b(?:memberships?|plans?|packages?|classes?|drop[ -]?ins?|unlimited|monthly|month|week|visits?|sessions?|passes?|"
    r"training|tuition|programs?|rates?|lessons?|hours?|day passes?)\b",
    re.IGNORECASE,
)
CARD_MONEY_RE = re.compile(r"\$\s*(\d{1,6}(?:\.\d{1,2})?)")


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def source_product_id(candidate: dict[str, Any]) -> str:
    explicit = text(candidate.get("sourceProductId"))
    if explicit:
        return explicit
    card_hash = text(candidate.get("cardAssociationHash")) or text(candidate.get("cardHash"))
    return f"card-{card_hash[:16]}" if card_hash else ""


def candidate_is_plan_descriptor(candidate: dict[str, Any]) -> bool:
    """Validate a named recurring plan whose public amount is withheld."""

    if text(candidate.get("kind")) != "plan-descriptor" or text(candidate.get("amount")):
        return False
    allowance = candidate.get("classAllowance")
    if not isinstance(allowance, dict):
        return False
    try:
        count = float(allowance.get("count") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        source_product_id(candidate)
        and count > 0
        and text(candidate.get("cadence")) == "month"
        and text(candidate.get("productType")) == "monthly"
        and text(candidate.get("purchaseMethod")) in {"account-required", "contact-required", "form-required"}
        and text(candidate.get("sourceUrl")).startswith("https://")
        and CARD_SEMANTIC_RE.search(text(candidate.get("rawLabel")))
    )


def candidate_is_attached(candidate: dict[str, Any]) -> bool:
    """Accept only a product/card observation, never an unattached dollar regex."""

    if candidate_is_plan_descriptor(candidate):
        return True
    if candidate_is_cost_context(candidate):
        return True
    method = text(candidate.get("method")).casefold()
    if method == "visible-text-candidate":
        return False
    if method.startswith("public-"):
        label = text(candidate.get("rawLabel"))
        has_product_semantics = bool(
            text(candidate.get("sourceProductId"))
            or CARD_SEMANTIC_RE.search(label)
            or candidate.get("classAllowance")
            or text(candidate.get("cadence")) not in {"", "one-time"}
            or text(candidate.get("productType")) in {"monthly", "drop-in"}
        )
        if not has_product_semantics:
            return False
    if method == "rendered-visible-plan-card":
        label = text(candidate.get("rawLabel"))
        amounts = {float(value) for value in CARD_MONEY_RE.findall(label)}
        if not CARD_SEMANTIC_RE.search(label) or len(amounts) > 1:
            return False
    return bool(
        text(candidate.get("sourceProductId"))
        or text(candidate.get("cardAssociationHash"))
        or text(candidate.get("cardHash"))
        or method.startswith(STRUCTURED_METHOD_PREFIXES)
    )


def candidate_is_cost_context(candidate: dict[str, Any]) -> bool:
    """Validate a non-selectable official range or starting price."""

    if text(candidate.get("kind")) not in {"range", "starting-price"}:
        return False
    if (candidate.get("promotion") or {}).get("isPromotion"):
        return False
    try:
        low = float(candidate.get("low"))
        high = float(candidate.get("high"))
    except (TypeError, ValueError):
        return False
    if not (0 < low <= high <= 10_000):
        return False
    method = text(candidate.get("method")).casefold()
    label = text(candidate.get("rawLabel"))
    if method in {"visible-cost-context", "rendered-visible-cost-context"} and not CARD_SEMANTIC_RE.search(label):
        return False
    return bool(text(candidate.get("sourceUrl"))) and (
        method.startswith("public-")
        or method.startswith("json-ld")
        or method in {"visible-cost-context", "rendered-visible-cost-context"}
    )


def evidence(candidate: dict[str, Any], observed_at: str) -> dict[str, Any]:
    source_url = text(candidate.get("sourceUrl"))
    raw_label = " ".join(text(candidate.get("rawLabel")).split())[:220]
    method = text(candidate.get("method")) or "catalog-review-candidate"
    digest = text(candidate.get("contentHash")) or hashlib.sha256(
        f"{source_url}|{observed_at}|{method}|{raw_label}".encode()
    ).hexdigest()
    return {
        "url": source_url,
        "observedAt": observed_at,
        "source": text(candidate.get("adapter")) or "official public source",
        "method": method,
        "rawLabel": raw_label,
        "contentHash": digest,
        "evidenceTier": text(candidate.get("evidenceTier")) or "official-public",
        "exactLocationMatch": text(candidate.get("exactLocationMatch")) or "candidate",
        "sourceProductId": source_product_id(candidate),
        "conflictFlags": list(candidate.get("conflictFlags") or []),
    }


def plan_offer(candidate: dict[str, Any], observed_at: str) -> dict[str, Any]:
    label = " ".join(text(candidate.get("rawLabel")).split())[:160] or "Public plan"
    cadence = text(candidate.get("cadence")) or "month"
    commitment = candidate.get("commitment") if isinstance(candidate.get("commitment"), dict) else {}
    result = {
        "sourceProductId": source_product_id(candidate),
        "name": label,
        "productType": "class-membership" if candidate.get("classAllowance") else "membership",
        "accessScope": text(candidate.get("accessScope")) or "Scope must be confirmed from the linked official product card.",
        "scopeType": text(candidate.get("scopeType")) or None,
        "amount": float(candidate["amount"]) if text(candidate.get("amount")) else None,
        "currency": text(candidate.get("currency")) or "USD",
        "billingInterval": cadence,
        "intervalCount": int(candidate.get("intervalCount") or 1),
        "classAllowance": candidate.get("classAllowance"),
        "commitmentType": text(commitment.get("type")) or "unknown",
        "minimumCommitmentMonths": commitment.get("minimumMonths"),
        "promotion": candidate.get("promotion") or {"isPromotion": False, "label": ""},
        "eligibility": candidate.get("eligibility") or {"type": "standard-adult", "restrictions": []},
        "availability": text(candidate.get("availability")) or "available",
        "purchaseMethod": text(candidate.get("purchaseMethod")) or "direct-public",
        "fees": list(candidate.get("fees") or []),
        "bestValueLabel": bool(candidate.get("bestValueLabel")),
        "evidence": evidence(candidate, observed_at),
    }
    return {key: value for key, value in result.items() if value is not None or key == "amount"}


def drop_in_offer(candidate: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        "sourceProductId": source_product_id(candidate),
        "name": " ".join(text(candidate.get("rawLabel")).split())[:160] or "Single visit or class",
        "accessScope": "One ordinary visit or class; scope must be confirmed during review.",
        "amount": float(candidate["amount"]),
        "currency": text(candidate.get("currency")) or "USD",
        "eligibility": candidate.get("eligibility") or {"type": "standard-adult", "restrictions": []},
        "promotion": candidate.get("promotion") or {"isPromotion": False, "label": ""},
        "purchaseMethod": text(candidate.get("purchaseMethod")) or "direct-public",
        "fees": list(candidate.get("fees") or []),
        "evidence": evidence(candidate, observed_at),
    }


def cost_context_offer(candidate: dict[str, Any], observed_at: str) -> dict[str, Any]:
    raw_label = " ".join(text(candidate.get("rawLabel")).split())[:220]
    return {
        "sourceProductId": source_product_id(candidate),
        "kind": text(candidate.get("kind")),
        "label": raw_label or "Official cost context",
        "low": float(candidate["low"]),
        "high": float(candidate["high"]),
        "currency": text(candidate.get("currency")) or "USD",
        "cadence": text(candidate.get("cadence")) or "unknown",
        "productType": text(candidate.get("contextProductType")) or "service",
        "sourceUrl": text(candidate.get("sourceUrl")),
        "observedAt": observed_at,
        "evidenceTier": text(candidate.get("evidenceTier")) or "official-public",
        "exactLocationMatch": text(candidate.get("exactLocationMatch")) or "candidate",
        "captureMethod": text(candidate.get("method")) or "catalog-review-candidate",
        "contentHash": text(candidate.get("contentHash")) or hashlib.sha256(
            f"{text(candidate.get('sourceUrl'))}|{observed_at}|{raw_label}".encode()
        ).hexdigest(),
        "conflictFlags": list(candidate.get("conflictFlags") or []),
        "note": text(candidate.get("note")),
        "selectable": False,
    }


def offer_signature(offer: dict[str, Any], group: str) -> tuple[str, str, float, str]:
    return (
        group,
        text(offer.get("sourceProductId")) or text(offer.get("name")).casefold(),
        round(float(offer.get("amount") or 0), 2),
        text(offer.get("billingInterval")) if group == "plan" else "visit",
    )


def catalog_signatures(value: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    signatures = [offer_signature(offer, "plan") for offer in value.get("planOffers", []) if isinstance(offer, dict)]
    signatures.extend(offer_signature(offer, "drop-in") for offer in value.get("dropInOffers", []) if isinstance(offer, dict))
    signatures.extend(
        (
            "cost-context", text(offer.get("kind")),
            text(offer.get("sourceProductId")) or text(offer.get("label")).casefold(),
            round(float(offer.get("low") or 0), 2), round(float(offer.get("high") or 0), 2),
            text(offer.get("cadence")),
        )
        for offer in value.get("costContextOffers", []) if isinstance(offer, dict)
    )
    return tuple(sorted(signatures))


def merge_with_approved_offers(
    observed: list[dict[str, Any]],
    approved: list[dict[str, Any]],
    group: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retain a reviewed catalog baseline while adding new crawl products.

    Catalog review is incremental: a crawler may discover one new route without
    re-observing an already approved booking widget.  A replacement proposal
    must therefore include the reviewed baseline or approval would silently
    erase products.  Reviewed values win when the same product is re-observed;
    a changed non-null amount becomes an explicit fail-closed conflict.
    """

    merged = [copy.deepcopy(item) for item in approved if isinstance(item, dict)]
    index_by_key: dict[str, int] = {}
    for index, offer in enumerate(merged):
        key = text(offer.get("sourceProductId")) or text(offer.get("name")).casefold()
        if key:
            index_by_key[key] = index
    conflicts: list[dict[str, Any]] = []
    for offer in observed:
        key = text(offer.get("sourceProductId")) or text(offer.get("name")).casefold()
        if not key or key not in index_by_key:
            index_by_key[key] = len(merged)
            merged.append(offer)
            continue
        incumbent = merged[index_by_key[key]]
        incumbent_amount = incumbent.get("amount")
        observed_amount = offer.get("amount")
        if incumbent_amount is not None and observed_amount is not None:
            if round(float(incumbent_amount), 2) != round(float(observed_amount), 2):
                conflicts.append({
                    "type": "approved-source-product-price-conflict",
                    "productType": group,
                    "sourceProductKey": key,
                    "approvedAmount": float(incumbent_amount),
                    "observedAmount": float(observed_amount),
                    "publicationEffect": "fail-closed",
                })
            continue
        if incumbent_amount is None and observed_amount is not None:
            merged[index_by_key[key]] = offer
    return merged, conflicts


def merge_with_approved_contexts(
    observed: list[dict[str, Any]],
    approved: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged = [copy.deepcopy(item) for item in approved if isinstance(item, dict)]
    signatures = {
        catalog_signatures({"costContextOffers": [item]})[0]
        for item in merged
    }
    values_by_key: dict[tuple[str, str, str], set[tuple[float, float]]] = defaultdict(set)
    for item in merged + observed:
        key = (
            text(item.get("kind")),
            text(item.get("sourceProductId")) or text(item.get("label")).casefold(),
            text(item.get("cadence")),
        )
        values_by_key[key].add((float(item.get("low") or 0), float(item.get("high") or 0)))
    for item in observed:
        signature = catalog_signatures({"costContextOffers": [item]})[0]
        if signature not in signatures:
            signatures.add(signature)
            merged.append(item)
    conflicts = [
        {
            "type": "approved-source-product-range-conflict",
            "kind": kind,
            "sourceProductKey": product_key,
            "ranges": [{"low": low, "high": high} for low, high in sorted(values)],
            "publicationEffect": "fail-closed",
        }
        for (kind, product_key, _cadence), values in sorted(values_by_key.items())
        if len(values) > 1
    ]
    return merged, conflicts


def approved_source_urls(approval: dict[str, Any]) -> set[str]:
    urls = {text(approval.get("priceSourceUrl"))}
    for offer in list(approval.get("planOffers") or []) + list(approval.get("dropInOffers") or []):
        if not isinstance(offer, dict):
            continue
        urls.add(text(offer.get("sourceUrl")))
        evidence_value = offer.get("evidence") if isinstance(offer.get("evidence"), dict) else {}
        urls.add(text(evidence_value.get("url")))
    for context in approval.get("costContextOffers") or []:
        if isinstance(context, dict):
            urls.add(text(context.get("sourceUrl")))
    return {url for url in urls if url}


def deduplicate_cost_contexts(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    values_by_product: dict[tuple[str, str, str], set[tuple[float, float]]] = defaultdict(set)
    for candidate in candidates:
        source_url = text(candidate.get("sourceUrl"))
        product_key = (
            source_product_id(candidate)
            or text(candidate.get("cardAssociationHash"))
            or " ".join(text(candidate.get("rawLabel")).casefold().split())
        )
        kind = text(candidate.get("kind"))
        low, high = float(candidate["low"]), float(candidate["high"])
        values_by_product[(source_url, kind, product_key)].add((low, high))
        context_product_type = text(candidate.get("contextProductType")) or "service"
        cadence = text(candidate.get("cadence")) or "unknown"
        dedup_key = (
            kind, context_product_type, low, high, cadence,
            product_key if context_product_type == "service" else "",
        )
        incumbent = selected.get(dedup_key)
        candidate_score = (
            bool(re.search(r"/(?:pricing|prices|rates|memberships?|packages?)(?:/|$)", source_url, re.IGNORECASE)),
            bool(source_product_id(candidate)),
            -len(text(candidate.get("rawLabel"))),
        )
        incumbent_score = (
            bool(re.search(r"/(?:pricing|prices|rates|memberships?|packages?)(?:/|$)", text((incumbent or {}).get("sourceUrl")), re.IGNORECASE)),
            bool(source_product_id(incumbent or {})),
            -len(text((incumbent or {}).get("rawLabel"))),
        )
        if incumbent is None or candidate_score > incumbent_score:
            selected[dedup_key] = candidate
    conflicts = [
        {
            "type": "source-product-range-conflict",
            "kind": kind,
            "sourceProductKey": product_key,
            "ranges": [{"low": low, "high": high} for low, high in sorted(values)],
            "publicationEffect": "fail-closed",
        }
        for (_source_url, kind, product_key), values in sorted(values_by_product.items())
        if len(values) > 1
    ]
    return list(selected.values()), conflicts


def deduplicate(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Rendered selectors often capture both a nested amount element and its
    # containing product card.  Prefer the label that preserves product and
    # promotion semantics, while retaining genuinely distinct equal-price
    # products for review.
    redundant: set[int] = set()
    for left_index, left in enumerate(candidates):
        if text(left.get("method")) != "rendered-visible-plan-card":
            continue
        left_label = " ".join(text(left.get("rawLabel")).casefold().split())
        for right_index, right in enumerate(candidates):
            if left_index == right_index or text(right.get("method")) != "rendered-visible-plan-card":
                continue
            if (
                float(left.get("amount") or 0) != float(right.get("amount") or 0)
                or text(left.get("productType")) != text(right.get("productType"))
                or text(left.get("sourceUrl")) != text(right.get("sourceUrl"))
            ):
                continue
            right_label = " ".join(text(right.get("rawLabel")).casefold().split())
            if not left_label or left_label == right_label or left_label not in right_label:
                continue
            left_promo = bool((left.get("promotion") or {}).get("isPromotion"))
            right_promo = bool((right.get("promotion") or {}).get("isPromotion"))
            left_words = re.findall(r"[a-z]{3,}", left_label)
            if (right_promo and not left_promo) or len(left_words) < 2:
                redundant.add(left_index)
            elif left_promo == right_promo:
                redundant.add(right_index)
    candidates = [candidate for index, candidate in enumerate(candidates) if index not in redundant]
    selected: dict[tuple[str, float, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    amounts_by_product: dict[tuple[str, str], set[float]] = defaultdict(set)
    priced_products = {
        text(candidate.get("sourceProductId"))
        for candidate in candidates
        if text(candidate.get("sourceProductId"))
        and not candidate_is_plan_descriptor(candidate)
        and float(candidate.get("amount") or 0) > 0
    }
    for candidate in candidates:
        product_type = text(candidate.get("productType"))
        product_key = (
            text(candidate.get("sourceProductId"))
            or text(candidate.get("cardAssociationHash"))
            or text(candidate.get("cardHash"))
            or text(candidate.get("rawLabel")).casefold()
        )
        descriptor = candidate_is_plan_descriptor(candidate)
        if descriptor and text(candidate.get("sourceProductId")) in priced_products:
            continue
        amount = float(candidate.get("amount") or 0)
        if not product_key or (amount <= 0 and not descriptor):
            continue
        if not descriptor:
            amounts_by_product[(product_type, product_key)].add(amount)
        key = (product_key, amount, product_type)
        incumbent = selected.get(key)
        method = text(candidate.get("method"))
        incumbent_method = text((incumbent or {}).get("method"))
        label_richness = min(len(re.findall(r"[a-z0-9]+", text(candidate.get("rawLabel")).casefold())), 30)
        incumbent_richness = min(len(re.findall(r"[a-z0-9]+", text((incumbent or {}).get("rawLabel")).casefold())), 30)
        score = (
            3 if method.startswith("public-") else 2 if method in {"rendered-visible-plan-card", "visible-perform-for-golf-plan-descriptor"} else 1,
            bool(text(candidate.get("sourceProductId"))),
            label_richness,
        )
        incumbent_score = (
            3 if incumbent_method.startswith("public-") else 2 if incumbent_method in {"rendered-visible-plan-card", "visible-perform-for-golf-plan-descriptor"} else 1,
            bool(text((incumbent or {}).get("sourceProductId"))),
            incumbent_richness,
        )
        if incumbent is None or score > incumbent_score:
            selected[key] = candidate
    for (product_type, product_key), amounts in sorted(amounts_by_product.items()):
        if len(amounts) > 1:
            conflicts.append({
                "type": "source-product-price-conflict",
                "productType": product_type,
                "sourceProductKey": product_key,
                "amounts": sorted(amounts),
                "publicationEffect": "fail-closed",
            })
    return list(selected.values()), conflicts


def build_review(
    fixture: dict[str, Any],
    documents: list[dict[str, Any]],
    generated_at: str,
    approved_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gyms = {text(gym.get("id")): gym for gym in fixture.get("gyms", [])}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        observed_at = text(document.get("generatedAt")) or generated_at
        for raw in document.get("observations", []):
            if not isinstance(raw, dict) or text(raw.get("gymId")) not in gyms:
                continue
            candidate = dict(raw)
            candidate["_observedAt"] = text(candidate.get("capturedAt")) or observed_at
            if candidate_is_attached(candidate):
                grouped[text(candidate.get("gymId"))].append(candidate)
            else:
                rejected[text(candidate.get("gymId"))].append({
                    "amount": candidate.get("amount"),
                    "low": candidate.get("low"),
                    "high": candidate.get("high"),
                    "kind": candidate.get("kind"),
                    "rawLabel": text(candidate.get("rawLabel"))[:220],
                    "sourceUrl": text(candidate.get("sourceUrl")),
                    "method": text(candidate.get("method")),
                    "reason": "Unattached price/range/descriptor text is not a plan catalog record or official cost context.",
                })

    approved_by_id = {
        text(item.get("gymId")): item
        for item in (approved_document or {}).get("approvals", [])
        if isinstance(item, dict) and text(item.get("gymId"))
    }
    proposals: list[dict[str, Any]] = []
    unchanged_approved: list[dict[str, Any]] = []
    for gym_id, candidates in sorted(grouped.items()):
        context_candidates = [candidate for candidate in candidates if candidate_is_cost_context(candidate)]
        price_candidates = [candidate for candidate in candidates if not candidate_is_cost_context(candidate)]
        unique, conflicts = deduplicate(price_candidates)
        unique_contexts, context_conflicts = deduplicate_cost_contexts(context_candidates)
        conflicts.extend(context_conflicts)
        plans: list[dict[str, Any]] = []
        drop_ins: list[dict[str, Any]] = []
        for candidate in unique:
            observed_at = text(candidate.pop("_observedAt", generated_at))
            if text(candidate.get("productType")) == "drop-in":
                drop_ins.append(drop_in_offer(candidate, observed_at))
            else:
                plans.append(plan_offer(candidate, observed_at))
        contexts = [
            cost_context_offer(candidate, text(candidate.pop("_observedAt", generated_at)))
            for candidate in unique_contexts
        ]
        prior_approval = approved_by_id.get(gym_id)
        if prior_approval:
            plans, baseline_conflicts = merge_with_approved_offers(
                plans, list(prior_approval.get("planOffers") or []), "plan",
            )
            conflicts.extend(baseline_conflicts)
            drop_ins, baseline_conflicts = merge_with_approved_offers(
                drop_ins, list(prior_approval.get("dropInOffers") or []), "drop-in",
            )
            conflicts.extend(baseline_conflicts)
            contexts, baseline_conflicts = merge_with_approved_contexts(
                contexts, list(prior_approval.get("costContextOffers") or []),
            )
            conflicts.extend(baseline_conflicts)
        if not plans and not drop_ins and not contexts:
            continue
        gym = gyms[gym_id]
        source_urls = sorted({
            text((offer.get("evidence") or {}).get("url"))
            for offer in plans + drop_ins
            if text((offer.get("evidence") or {}).get("url"))
        })
        source_urls = sorted(set(source_urls).union(
            text(item.get("sourceUrl")) for item in contexts if text(item.get("sourceUrl"))
        ))
        if prior_approval:
            source_urls = sorted(set(source_urls).union(approved_source_urls(prior_approval)))
        proposal = {
            "gymId": gym_id,
            "gymName": text(gym.get("name")),
            "canonicalAddress": text(gym.get("canonicalAddress")) or text(gym.get("address")),
            "operatorId": text(gym.get("operatorId")),
            "reviewStatus": "pending",
            "publicationEligible": False,
            "sourceUrls": source_urls,
            "planOffers": plans,
            "dropInOffers": drop_ins,
            "costContextOffers": contexts,
            "catalogCompleteness": {
                "plans": "partial" if plans else "none-observed",
                "dropIns": "partial" if drop_ins else "none-observed",
            },
            "catalogCompletenessReason": (
                "The proposal retains the previously reviewed baseline and adds newly observed attached offers. "
                "A reviewer must explicitly confirm that the source exposed every current product before marking either catalog complete."
            ),
            "conflicts": conflicts,
            "reviewChecklist": {
                "exactLocation": False,
                "standardAdultEligibility": False,
                "planCardAssociation": False,
                "feesLinkedToEachPlan": False,
                "promotionsMarked": False,
            },
            "currentPublished": {
                "monthlyPrice": gym.get("monthlyPrice"),
                "dayPassPrice": gym.get("dayPassPrice"),
                "selectedPlanId": gym.get("selectedPlanId"),
                "catalogStatus": gym.get("catalogStatus"),
            },
            "approvedCatalogBaseline": {
                "included": bool(prior_approval),
                "planOfferCount": len((prior_approval or {}).get("planOffers") or []),
                "dropInOfferCount": len((prior_approval or {}).get("dropInOffers") or []),
                "costContextOfferCount": len((prior_approval or {}).get("costContextOffers") or []),
                "catalogCompleteness": (prior_approval or {}).get("catalogCompleteness"),
            },
        }
        if prior_approval and not conflicts and catalog_signatures(prior_approval) == catalog_signatures(proposal):
            unchanged_approved.append({
                "gymId": gym_id,
                "gymName": text(gym.get("name")),
                "sourceUrls": source_urls,
                "planOfferCount": len(plans),
                "dropInOfferCount": len(drop_ins),
                "costContextOfferCount": len(contexts),
                "catalogCompleteness": prior_approval.get("catalogCompleteness") or proposal["catalogCompleteness"],
                "status": "unchanged-approved",
            })
            continue
        proposals.append(proposal)
    rejected_count = sum(len(items) for items in rejected.values())
    return {
        "_meta": {
            "generatedAt": generated_at,
            "methodology": "Grouped review-only public product/card observations. Loose dollar text is rejected. No proposal is a verified price until independently approved.",
            "proposalCount": len(proposals),
            "unchangedApprovedCount": len(unchanged_approved),
            "rejectedUnattachedObservationCount": rejected_count,
        },
        "proposals": proposals,
        "unchangedApproved": unchanged_approved,
        "rejectedEvidence": [
            {"gymId": gym_id, "observations": items}
            for gym_id, items in sorted(rejected.items())
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    fixture = load_json(FIXTURE_PATH, {"gyms": []})
    documents = [load_json(STATIC_PATH, {"observations": []}), load_json(RENDERED_PATH, {"observations": []})]
    generated_at = args.date or max((text(item.get("generatedAt")) for item in documents), default="")
    approved = load_json(APPROVED_PATH, {"approvals": []})
    output = build_review(fixture, documents, generated_at, approved)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output["_meta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
