"""Least-privilege Gmail transport for reviewed gym-pricing outreach.

Credentials and recipient approvals come only from encrypted environment
secrets.  The committed output contains gym IDs, dates, hashes, and sanitized
price candidates; it never stores recipient addresses, raw mail, tokens, or
message/thread IDs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import import_operator_replies as replies

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
OUTPUT_PATH = ROOT / "data" / "imports" / "operator-confirmed-observations.json"
ATTEMPTS_PATH = ROOT / "data" / "imports" / "contact-submission-attempts.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
LABEL_QUERY = "label:sf-gym-pricing newer_than:60d"
INQUIRY_V1 = (
    "Hello, I maintain an independent San Francisco gym cost guide. Could you confirm the current standard-adult "
    "pricing for this exact location: the least expensive ordinary recurring membership, its class or visit "
    "allowance, billing cadence, minimum commitment, mandatory enrollment/annual/initiation/processing fees, "
    "and the ordinary single visit or class price? Please also include the date the rates took effect. "
    "We do not need a trial, promotional, student, employer, resident, or prepaid-annual price. Thank you."
)
INQUIRY_V2 = (
    "Hello, I maintain an independent San Francisco gym cost guide. I am researching the exact location below.\n\n"
    "Gym: {gym_name}\n"
    "Address: {address}\n\n"
    "Could you confirm the current standard-adult pricing for this location? Please reply with:\n"
    "- the least expensive ordinary recurring membership and its class or visit allowance;\n"
    "- its billing cadence and minimum commitment;\n"
    "- each mandatory enrollment, annual, initiation, processing, setup, or activation fee;\n"
    "- the ordinary unrestricted single visit or class price; and\n"
    "- the date these rates took effect.\n\n"
    "We do not need a trial, promotional, student, employer, resident, personalized-training quote, or prepaid-annual "
    "price. If no standard public plan or ordinary drop-in is offered, that confirmation is useful too. Thank you."
)
TEMPLATES = {"v1": INQUIRY_V1, "v2": INQUIRY_V2}
DEFAULT_TEMPLATE_VERSION = "v2"
FOLLOW_UP = "Hello, I am following up once on the standard-adult pricing request below. If no public standard plan is available, that confirmation is also useful. Thank you."


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def configured() -> bool:
    return all(text(os.environ.get(name)) for name in (
        "GYM_RESEARCH_GMAIL_CLIENT_ID", "GYM_RESEARCH_GMAIL_CLIENT_SECRET", "GYM_RESEARCH_GMAIL_REFRESH_TOKEN",
    ))


def request_json(url: str, token: str = "", method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Google API endpoints.
        return json.loads(response.read(4_000_000).decode("utf-8"))


def access_token() -> str:
    fields = urlencode({
        "client_id": text(os.environ.get("GYM_RESEARCH_GMAIL_CLIENT_ID")),
        "client_secret": text(os.environ.get("GYM_RESEARCH_GMAIL_CLIENT_SECRET")),
        "refresh_token": text(os.environ.get("GYM_RESEARCH_GMAIL_REFRESH_TOKEN")),
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = Request(TOKEN_URL, data=fields, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Google OAuth endpoint.
        result = json.loads(response.read(1_000_000).decode("utf-8"))
    token = text(result.get("access_token"))
    if not token:
        raise RuntimeError("Google OAuth refresh returned no access token.")
    return token


def list_messages(token: str, query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params = {"q": query, "maxResults": "100"}
        if page_token:
            params["pageToken"] = page_token
        payload = request_json(f"{API_ROOT}/messages?{urlencode(params)}", token)
        results.extend(item for item in payload.get("messages", []) if isinstance(item, dict))
        page_token = text(payload.get("nextPageToken"))
        if not page_token or len(results) >= 500:
            break
    return results[:500]


def raw_message(token: str, message_id: str) -> bytes:
    payload = request_json(f"{API_ROOT}/messages/{quote(message_id)}?format=raw", token)
    encoded = text(payload.get("raw"))
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def merge_observations(observations: list[dict[str, Any]], generated_at: str) -> None:
    existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) if OUTPUT_PATH.exists() else {"observations": []}
    by_evidence = {text(item.get("evidenceId")): item for item in existing.get("observations", [])}
    by_evidence.update({text(item.get("evidenceId")): item for item in observations if text(item.get("evidenceId"))})
    OUTPUT_PATH.write_text(json.dumps({
        "generatedAt": generated_at,
        "policy": "Sanitized review queue only; raw correspondence and Gmail identifiers are never committed.",
        "observations": sorted(by_evidence.values(), key=lambda item: (item["gymId"], item["receivedAt"], item["evidenceId"])),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def poll(token: str, today: date) -> dict[str, Any]:
    fixture = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    known_ids = {text(gym.get("id")) for gym in fixture.get("gyms", [])}
    observations: list[dict[str, Any]] = []
    skipped = 0
    for item in list_messages(token, LABEL_QUERY):
        raw = raw_message(token, text(item.get("id")))
        observation = replies.parse_message(raw, known_ids)
        if observation is None:
            skipped += 1
            continue
        observations.append(observation)
    merge_observations(observations, today.isoformat())
    return {"configured": True, "messagesSeen": len(observations) + skipped, "observations": len(observations), "skipped": skipped}


def approval_records() -> list[dict[str, Any]]:
    encoded = text(os.environ.get("GYM_RESEARCH_EMAIL_APPROVALS_B64"))
    if not encoded:
        return []
    try:
        document = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("GYM_RESEARCH_EMAIL_APPROVALS_B64 is not valid base64-encoded JSON.") from None
    return [item for item in document.get("approvals", []) if item.get("reviewStatus") == "approved"]


def template_hash(version: str = DEFAULT_TEMPLATE_VERSION) -> str:
    template = TEMPLATES.get(version)
    if template is None:
        raise ValueError(f"Unsupported inquiry template version: {version}")
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def approval_template_version(approval: dict[str, Any]) -> str:
    """Keep existing reviewed v1 approvals valid while defaulting new approvals to v2."""

    version = text(approval.get("templateVersion"))
    return version if version in TEMPLATES else "v1"


def inquiry_body(gym: dict[str, Any], version: str = DEFAULT_TEMPLATE_VERSION) -> str:
    template = TEMPLATES.get(version)
    if template is None:
        raise ValueError(f"Unsupported inquiry template version: {version}")
    if version == "v1":
        return template
    gym_name = text(gym.get("name"))
    address = text(gym.get("canonicalAddress")) or text(gym.get("address"))
    if not gym_name or not address:
        raise ValueError("The exact gym name and address are required for v2 outreach.")
    return template.format(gym_name=gym_name, address=address)


def approval_is_valid(approval: dict[str, Any]) -> bool:
    """Require exact reviewed email/source domains and the immutable inquiry."""

    recipient = text(approval.get("recipient"))
    source_url = text(approval.get("sourceUrl"))
    if "@" not in recipient or not source_url:
        return False
    recipient_domain = recipient.rsplit("@", 1)[1].casefold()
    from urllib.parse import urlparse

    try:
        source_domain = urlparse(source_url).netloc.casefold()
    except ValueError:
        return False
    version = approval_template_version(approval)
    return bool(
        recipient_domain
        and source_domain
        and recipient_domain == text(approval.get("recipientDomain")).casefold()
        and source_domain == text(approval.get("sourceDomain")).casefold()
        and text(approval.get("templateHash")) == template_hash(version)
        and approval.get("exactLocationConfirmed") is True
        and approval.get("publicOperatorEmailConfirmed") is True
    )


def outreach_action(sent_dates: list[date], today: date) -> str:
    sent = sorted(value for value in sent_dates if value <= today)
    if not sent:
        return "initial"
    if today - sent[-1] >= timedelta(days=180):
        return "initial"
    if len(sent) == 1 and today - sent[-1] >= timedelta(days=14):
        return "follow-up"
    return "none"


def sent_dates_for(token: str, gym_id: str) -> list[date]:
    dates: list[date] = []
    for item in list_messages(token, f'in:sent subject:"[SFGYM:{gym_id}]" newer_than:365d'):
        payload = request_json(f"{API_ROOT}/messages/{quote(text(item.get('id')))}?format=metadata", token)
        try:
            dates.append(datetime.fromtimestamp(int(payload.get("internalDate")) / 1000, UTC).date())
        except (TypeError, ValueError, OSError):
            continue
    return dates


def send_email(token: str, recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    request_json(f"{API_ROOT}/messages/send", token, "POST", {"raw": encoded})


def send_approved(token: str, today: date) -> dict[str, Any]:
    fixture = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    gyms = {text(gym.get("id")): gym for gym in fixture.get("gyms", [])}
    sent: list[dict[str, Any]] = []
    for approval in approval_records():
        gym_id = text(approval.get("gymId"))
        recipient = text(approval.get("recipient"))
        source_url = text(approval.get("sourceUrl"))
        gym = gyms.get(gym_id)
        if not gym or not approval_is_valid(approval):
            continue
        action = outreach_action(sent_dates_for(token, gym_id), today)
        if action == "none":
            continue
        version = approval_template_version(approval)
        subject = f"[SFGYM:{gym_id}] San Francisco standard-adult pricing request"
        send_email(token, recipient, subject, inquiry_body(gym, version) if action == "initial" else FOLLOW_UP)
        sent.append({
            "gymId": gym_id, "gymName": text(gym.get("name")), "channel": "email", "action": action,
            "templateVersion": version,
            "contactHash": hashlib.sha256(recipient.casefold().encode("utf-8")).hexdigest(),
            "sourceUrl": source_url, "submittedAt": today.isoformat(), "containsContactData": False,
        })
    if sent:
        attempts = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8")) if ATTEMPTS_PATH.exists() else {"attempts": []}
        attempts.setdefault("attempts", []).extend(sent)
        ATTEMPTS_PATH.write_text(json.dumps(attempts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"configured": True, "approvedRecipients": len(approval_records()), "messagesSent": len(sent)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("poll", "send-approved", "template-hash"))
    parser.add_argument("--date")
    parser.add_argument("--template-version", choices=tuple(TEMPLATES), default=DEFAULT_TEMPLATE_VERSION)
    args = parser.parse_args()
    today = date.fromisoformat(args.date) if args.date else datetime.now(UTC).date()
    if args.command == "template-hash":
        print(template_hash(args.template_version))
        return 0
    if not configured():
        print(json.dumps({"configured": False, "command": args.command, "reason": "Gmail OAuth secrets are not configured."}))
        return 0
    try:
        token = access_token()
        result = poll(token, today) if args.command == "poll" else send_approved(token, today)
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as error:
        print(json.dumps({"configured": True, "command": args.command, "error": text(error)[:240]}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
