"""Explicitly promote or reject sanitized deal candidates from a review PR."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "deal-observations.json"
APPROVED_PATH = ROOT / "data" / "imports" / "deal-approved.json"


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def load(path: Path, fallback: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_candidate(candidate_id: str, observations: dict[str, Any]) -> dict[str, Any]:
    candidate = next((item for item in observations.get("deals", []) if text(item.get("id")) == candidate_id), None)
    if candidate is None:
        raise SystemExit("Deal candidate not found in the current observations file.")
    return candidate


def decision_from(candidate: dict[str, Any], status: str, args: argparse.Namespace) -> dict[str, Any]:
    decision = {
        key: candidate.get(key)
        for key in (
            "id", "gymId", "gymName", "amount", "currency", "productType", "cadence", "label",
            "expiresAt", "sourceUrl", "capturedAt", "contentHash", "replacesOrdinaryPrice",
        )
    }
    decision.update({
        "reviewStatus": status,
        "reviewedAt": datetime.now(UTC).date().isoformat(),
        "reviewNote": text(args.review_note),
    })
    if status == "approved":
        decision["standardAdult"] = True
        decision["eligibilityLabel"] = text(args.eligibility_label) or "Standard adult public offer"
        decision["replacesOrdinaryPrice"] = False
    else:
        decision["standardAdult"] = False
        decision["rejectionReason"] = text(args.reason)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    approve = subparsers.add_parser("approve")
    approve.add_argument("--id", required=True)
    approve.add_argument("--confirm-standard-adult", action="store_true", required=True)
    approve.add_argument("--confirm-ordinary-price-separate", action="store_true", required=True)
    approve.add_argument("--eligibility-label", default="Standard adult public offer")
    approve.add_argument("--review-note", required=True)
    reject = subparsers.add_parser("reject")
    reject.add_argument("--id", required=True)
    reject.add_argument("--reason", required=True)
    reject.add_argument("--review-note", default="")
    args = parser.parse_args()

    observations = load(OBSERVATIONS_PATH, {"deals": []})
    decisions = load(APPROVED_PATH, {"_meta": {}, "approvals": []})
    if args.command == "list":
        existing = {text(item.get("id")): text(item.get("reviewStatus")) for item in decisions.get("approvals", [])}
        print(json.dumps([
            {
                "id": item.get("id"), "gymId": item.get("gymId"), "gymName": item.get("gymName"),
                "label": item.get("label"), "amount": item.get("amount"), "expiresAt": item.get("expiresAt"),
                "sourceUrl": item.get("sourceUrl"), "decision": existing.get(text(item.get("id")), "pending"),
            }
            for item in observations.get("deals", [])
        ], indent=2))
        return 0

    candidate = find_candidate(args.id, observations)
    if args.command == "approve":
        if not args.confirm_standard_adult or not args.confirm_ordinary_price_separate:
            raise SystemExit("Approval requires both explicit confirmation flags.")
        decision = decision_from(candidate, "approved", args)
    else:
        decision = decision_from(candidate, "rejected", args)
    decisions["approvals"] = [item for item in decisions.get("approvals", []) if text(item.get("id")) != args.id]
    decisions["approvals"].append(decision)
    decisions["approvals"].sort(key=lambda item: (text(item.get("gymId")), text(item.get("id"))))
    save(APPROVED_PATH, decisions)
    print(json.dumps({"id": args.id, "gymId": candidate.get("gymId"), "reviewStatus": decision["reviewStatus"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
