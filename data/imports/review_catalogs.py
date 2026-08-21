"""Approve a grouped official catalog proposal after explicit human checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REVIEW_PATH = ROOT / "official-catalog-review.json"
APPROVED_PATH = ROOT / "official-crawl-approved.json"


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def load(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def approval_from_proposal(proposal: dict[str, Any], reviewed_at: str) -> dict[str, Any]:
    if proposal.get("conflicts"):
        raise ValueError("Proposal has unresolved source-product conflicts; it cannot be approved.")
    offers = list(proposal.get("planOffers") or [])
    drop_ins = list(proposal.get("dropInOffers") or [])
    source_urls = list(proposal.get("sourceUrls") or [])
    if not offers and not drop_ins:
        raise ValueError("Proposal contains no attached plan or drop-in offers.")
    for offer in offers + drop_ins:
        offer.setdefault("evidence", {})["exactLocationMatch"] = "exact-location-reviewed"
    return {
        "gymId": text(proposal.get("gymId")),
        "priceSource": "Reviewed official public catalog",
        "priceSourceUrl": source_urls[0] if source_urls else "",
        "priceObservedAt": max(
            (text((offer.get("evidence") or {}).get("observedAt")) for offer in offers + drop_ins),
            default=reviewed_at,
        ),
        "priceNote": "Complete public catalog approved after exact-location, eligibility, plan-card, promotion, and fee-linkage review.",
        "planOffers": offers,
        "dropInOffers": drop_ins,
    }


def approve(gym_id: str, reviewed_at: str, replace: bool, review_path: Path, approved_path: Path) -> dict[str, Any]:
    review = load(review_path, {"proposals": []})
    proposal = next((item for item in review.get("proposals", []) if text(item.get("gymId")) == gym_id), None)
    if proposal is None:
        raise ValueError(f"No pending catalog proposal exists for {gym_id}.")
    approved = load(approved_path, {"_meta": {}, "approvals": []})
    existing = next((item for item in approved.get("approvals", []) if text(item.get("gymId")) == gym_id), None)
    if existing is not None and not replace:
        raise ValueError(f"An approved observation already exists for {gym_id}; pass --replace to supersede it.")
    replacement = approval_from_proposal(proposal, reviewed_at)
    approvals = [item for item in approved.get("approvals", []) if text(item.get("gymId")) != gym_id]
    approvals.append(replacement)
    approvals.sort(key=lambda item: text(item.get("gymId")))
    approved["approvals"] = approvals
    approved.setdefault("_meta", {})["reviewedAt"] = reviewed_at
    approved["_meta"]["catalogApprovalMethod"] = "Explicit reviewer confirmation of exact location, standard-adult eligibility, plan-card association, promotions, and plan-linked fees."
    approved_path.write_text(json.dumps(approved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return replacement


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("approve",))
    parser.add_argument("--gym-id", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--confirm-exact-location", action="store_true")
    parser.add_argument("--confirm-standard-adult", action="store_true")
    parser.add_argument("--confirm-plan-card-association", action="store_true")
    parser.add_argument("--confirm-fees-linked", action="store_true")
    parser.add_argument("--confirm-promotions-marked", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    confirmations = (
        args.confirm_exact_location,
        args.confirm_standard_adult,
        args.confirm_plan_card_association,
        args.confirm_fees_linked,
        args.confirm_promotions_marked,
    )
    if not all(confirmations):
        parser.error("all five --confirm-* checks are required")
    replacement = approve(args.gym_id, args.date, args.replace, REVIEW_PATH, APPROVED_PATH)
    print(json.dumps(replacement, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
