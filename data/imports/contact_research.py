"""Approval-gated local pricing-inquiry form worker.

Discovery is read-only and writes only field/consent metadata. Submission is
local-only, requires an exact domain + terms-hash approval, reads contact data
from environment variables, and stores only redacted audit facts. It never
creates accounts, authenticates, enters payment data, or accepts SMS/call
marketing consent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import crawl_official_sources as crawler

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "data" / "imports" / "sf-gyms-osm.json"
MANIFEST_PATH = ROOT / "data" / "imports" / "contact-form-manifests.json"
REPORT_PATH = ROOT / "data" / "imports" / "contact-research-report.json"
ATTEMPTS_PATH = ROOT / "data" / "imports" / "contact-submission-attempts.json"
OBSERVATIONS_PATH = ROOT / "data" / "imports" / "operator-confirmed-observations.json"
PRIVATE_DIR = ROOT / "data" / "private"
APPROVAL_PATH = PRIVATE_DIR / "contact-domain-approvals.json"
STATE_PATH = PRIVATE_DIR / "contact-submission-state.json"
ZIP_CODE = "94107"
RESUBMIT_AFTER_DAYS = 180
FORM_LINK_RE = re.compile(r"\b(?:pricing|rates?|membership|tuition|cost|quote|contact|join)\b", re.I)
PHONE_CONSENT_RE = re.compile(r"\b(?:sms|texts?|texting|text messages?|phone calls?|telephone|autodial|automated calls?)\b", re.I)
EMAIL_MARKETING_RE = re.compile(r"\b(?:email|newsletter|mailing list|marketing messages?)\b", re.I)
TERMS_RE = re.compile(r"\b(?:terms|privacy|policy|consent)\b", re.I)
ACCOUNT_RE = re.compile(r"\b(?:create (?:an )?account|register account|password|log ?in|sign ?in)\b", re.I)
PRICING_RE = re.compile(r"\b(?:price|pricing|rate|cost|membership|tuition|dues|quote|plan)\b", re.I)
PROHIBITED_FIELD_RE = re.compile(
    r"\b(?:password|passcode|otp|credit|debit|card|cvv|cvc|billing|bank|routing|ssn|social security|"
    r"date of birth|dob|birthdate|street address|home address|emergency contact)\b",
    re.I,
)
PUBLIC_FORM_DOMAINS = {
    "forms.hsforms.com",
    "share.hsforms.com",
    "form.jotform.com",
    "jotform.com",
    "form.typeform.com",
    "typeform.com",
}
INQUIRY_MESSAGE = (
    "Please share the current standard-adult direct-purchase pricing for this location: "
    "the lowest ordinary recurring plan, a typical recurring plan, the highest-access recurring plan, "
    "an ordinary single visit or class, commitment terms, and any mandatory fees. Please exclude trials, "
    "introductory or founding offers, employer/student/resident rates, and prepaid annual discounts."
)


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def domain(url: str) -> str:
    try:
        return urlparse(url).netloc.casefold().split(":", 1)[0]
    except ValueError:
        return ""


def is_operator_or_public_form(operator_url: str, target_url: str) -> bool:
    operator_domain = domain(operator_url)
    target_domain = domain(target_url)
    if not operator_domain or not target_domain:
        return False
    return (
        target_domain == operator_domain
        or target_domain.endswith(f".{operator_domain}")
        or target_domain in PUBLIC_FORM_DOMAINS
        or any(target_domain == item or target_domain.endswith(f".{item}") for item in crawler.BOOKING_DOMAINS)
    )


def plus_address(address: str, tag: str = "sfgyms") -> str:
    local, separator, host = text(address).partition("@")
    if not separator or not local or not host:
        return ""
    if "+" in local:
        return f"{local}@{host}"
    return f"{local}+{tag}@{host}"


def classify_field(field: dict[str, Any]) -> str:
    field_type = text(field.get("type")).casefold()
    if field_type in {"hidden", "submit", "button", "reset", "image"}:
        return "ignored"
    blob = " ".join(
        text(field.get(key)) for key in ("type", "name", "id", "autocomplete", "label")
    )
    lowered = blob.casefold()
    if PROHIBITED_FIELD_RE.search(blob) or field_type == "password":
        return "prohibited"
    if field_type in {"checkbox", "radio"}:
        return "consent"
    if field_type == "email" or re.search(r"\b(?:e-?mail)\b", lowered):
        return "email"
    if field_type == "tel" or re.search(r"\b(?:phone|mobile|telephone)\b", lowered):
        return "phone"
    if re.search(r"\b(?:zip|postal)\b", lowered):
        return "postal-code"
    if re.search(r"\b(?:first\s*name|last\s*name|surname|family name)\b", lowered):
        return "split-name"
    if re.search(r"\b(?:full name|your name|name)\b", lowered):
        return "name"
    if field.get("tag") == "textarea" or re.search(r"\b(?:message|question|comment|inquiry|details|notes?)\b", lowered):
        return "message"
    return "unknown"


def classify_consent(field: dict[str, Any]) -> str:
    label = text(field.get("label"))
    if PHONE_CONSENT_RE.search(label):
        return "phone-marketing"
    if EMAIL_MARKETING_RE.search(label):
        return "email-marketing"
    if TERMS_RE.search(label):
        return "terms"
    return "unknown"


def terms_hash(form: dict[str, Any]) -> str:
    stable = {
        "url": text(form.get("url")),
        "action": text(form.get("action")),
        "method": text(form.get("method")).upper(),
        "submitLabel": text(form.get("submitLabel")),
        "policyTextHash": text(form.get("policyTextHash")),
        "fields": [
            {
                "category": text(field.get("category")),
                "type": text(field.get("type")),
                "required": bool(field.get("required")),
                "label": text(field.get("label")),
                "consentType": text(field.get("consentType")),
            }
            for field in form.get("fields", [])
            if field.get("category") != "ignored"
        ],
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()


def evaluate_form_policy(form: dict[str, Any]) -> dict[str, Any]:
    normalized_fields: list[dict[str, Any]] = []
    blockers: list[str] = []
    for source_field in form.get("fields", []):
        field = {key: source_field.get(key) for key in (
            "fieldIndex", "tag", "type", "name", "id", "autocomplete", "label", "required", "disabled"
        )}
        field["category"] = classify_field(field)
        field["consentType"] = classify_consent(field) if field["category"] == "consent" else ""
        normalized_fields.append(field)
        if field["category"] == "prohibited":
            blockers.append("prohibited-sensitive-field")
        if field["category"] == "split-name" and field.get("required"):
            blockers.append("required-split-name-fields")
        if field["category"] == "unknown" and field.get("required"):
            blockers.append("unknown-required-field")
        if field["category"] == "consent" and field.get("required") and field["consentType"] in {"phone-marketing", "unknown"}:
            blockers.append(f"required-{field['consentType']}-consent")
    visible = " ".join((text(form.get("formText")), text(form.get("submitLabel"))))
    categories = {field["category"] for field in normalized_fields}
    if form.get("captcha"):
        blockers.append("captcha-or-human-challenge")
    if ACCOUNT_RE.search(visible) or "prohibited" in categories:
        blockers.append("account-or-authentication-form")
    if "message" not in categories and not PRICING_RE.search(visible):
        blockers.append("not-a-pricing-inquiry-form")
    if "phone" in categories and PHONE_CONSENT_RE.search(visible):
        blockers.append("phone-field-with-call-or-text-consent")
    if "email" not in categories:
        blockers.append("no-email-field")
    action_domain = domain(text(form.get("action")) or text(form.get("url")))
    form_domain = domain(text(form.get("url")))
    if action_domain and form_domain and action_domain != form_domain and not action_domain.endswith(f".{form_domain}"):
        blockers.append("external-action-requires-separate-approval")
    normalized = {
        **{key: form.get(key) for key in ("formIndex", "url", "action", "method", "submitLabel")},
        "domain": form_domain,
        "actionDomain": action_domain,
        "fields": normalized_fields,
        "blockers": sorted(set(blockers)),
        "policyTextHash": hashlib.sha256(" ".join(visible.split()).encode("utf-8")).hexdigest(),
    }
    normalized["termsHash"] = terms_hash(normalized)
    normalized["submissionStatus"] = "eligible-after-domain-approval" if not blockers else "blocked"
    return normalized


def redact_sensitive(value: str, secrets: list[str] | None = None) -> str:
    result = text(value)
    for secret in secrets or []:
        if secret:
            result = result.replace(secret, "[redacted]")
    result = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted-email]", result, flags=re.I)
    result = re.sub(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)", "[redacted-phone]", result)
    return result


def approval_valid(approval: dict[str, Any], form: dict[str, Any]) -> bool:
    return (
        text(approval.get("domain")) == text(form.get("domain"))
        and text(approval.get("termsHash")) == text(form.get("termsHash"))
        and text(form.get("actionDomain")) in set(approval.get("actionDomains", [])) | {text(form.get("domain"))}
        and bool(text(approval.get("approvedAt")))
    )


def candidate_gyms(document: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            gym for gym in document.get("gyms", [])
            if gym.get("publicationStatus") == "publish"
            and gym.get("recordStatus") != "coming_soon"
            and gym.get("entityKind") in {"gym", "studio", "martial-arts"}
            and gym.get("pricingStatus") in {"gated", "unresolved"}
            and crawler.is_public_http_url(text(gym.get("websiteUrl")))
            and not crawler.coverage.is_osm_url(text(gym.get("websiteUrl")))
        ],
        key=lambda gym: (text(gym.get("operatorKey")), text(gym.get("name"))),
    )


def build_discovery_report(records: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    forms = [form for record in records for form in record.get("forms", [])]
    eligible = [
        (record, form)
        for record in records for form in record.get("forms", [])
        if form.get("submissionStatus") == "eligible-after-domain-approval"
    ]
    return {
        "generatedAt": generated_at,
        "candidateGymsScanned": len(records),
        "recordStatusCounts": dict(sorted(Counter(text(item.get("status")) for item in records).items())),
        "formsFound": len(forms),
        "eligibleFormCount": len(eligible),
        "eligibleGymCount": len({text(record.get("gymId")) for record, _form in eligible}),
        "blockedFormCount": sum(form.get("submissionStatus") == "blocked" for form in forms),
        "blockerCounts": dict(sorted(Counter(
            blocker for form in forms for blocker in form.get("blockers", [])
        ).items())),
        "eligibleForms": [
            {
                "gymId": record.get("gymId"), "gymName": record.get("gymName"),
                "domain": form.get("domain"), "actionDomain": form.get("actionDomain"),
                "formId": form.get("formId"), "termsHash": form.get("termsHash"),
            }
            for record, form in eligible
        ],
        "submissionPolicy": "No submission without an exact local domain/action-domain and terms-hash approval.",
    }


def write_discovery_report(manifest: dict[str, Any]) -> int:
    report = build_discovery_report(manifest.get("records", []), text(manifest.get("generatedAt")))
    save_json(REPORT_PATH, report)
    print(json.dumps({key: report[key] for key in ("candidateGymsScanned", "formsFound", "eligibleGymCount", "blockedFormCount")}))
    return 0


def raw_forms(page: Any, page_url: str) -> list[dict[str, Any]]:
    return page.evaluate(
        """(pageUrl) => Array.from(document.forms).map((form, formIndex) => {
          const elements = Array.from(form.querySelectorAll('input, textarea, select, button'));
          const labelFor = (el) => {
            if (el.id) {
              const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
              if (label) return (label.innerText || label.textContent || '').trim();
            }
            const parent = el.closest('label');
            return parent ? (parent.innerText || parent.textContent || '').trim() : '';
          };
          const submit = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
          return {
            formIndex,
            url: pageUrl,
            action: form.action || pageUrl,
            method: (form.method || 'GET').toUpperCase(),
            formText: (form.innerText || '').slice(0, 1200),
            submitLabel: submit ? ((submit.innerText || submit.value || '').trim()) : '',
            captcha: Boolean(form.querySelector('[class*="captcha" i], [id*="captcha" i], iframe[src*="captcha" i], iframe[src*="recaptcha" i]')),
            fields: elements.map((el, fieldIndex) => ({
              fieldIndex,
              tag: el.tagName.toLowerCase(),
              type: (el.type || '').toLowerCase(),
              name: el.name || '',
              id: el.id || '',
              autocomplete: el.autocomplete || '',
              label: labelFor(el),
              required: Boolean(el.required),
              disabled: Boolean(el.disabled)
            }))
          };
        })""",
        page_url,
    )


def discover_gym(browser: Any, gym: dict[str, Any], timeout_ms: int, observed_at: str) -> dict[str, Any]:
    operator_url = text(gym.get("websiteUrl"))
    allowed, robots_status = crawler.robots_allowed(operator_url, timeout_ms / 1000)
    result: dict[str, Any] = {
        "gymId": text(gym.get("id")), "gymName": text(gym.get("name")), "operatorUrl": operator_url,
        "discoveredAt": observed_at, "robotsStatus": robots_status, "forms": [], "status": "robots-disallowed" if not allowed else "scanned",
    }
    if not allowed:
        return result
    context = browser.new_context(java_script_enabled=True, service_workers="block")
    page = context.new_page()
    page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_())
    urls = [operator_url]
    try:
        page.goto(operator_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(750)
        for link in page.locator("a[href]").all()[:200]:
            try:
                label = " ".join(link.inner_text(timeout=150).split())
                href = text(link.get_attribute("href"))
            except Exception:
                continue
            target = urljoin(page.url, href)
            if FORM_LINK_RE.search(label) and is_operator_or_public_form(operator_url, target):
                urls.append(target)
        for candidate_url in list(dict.fromkeys(urls))[:8]:
            page_allowed, _ = crawler.robots_allowed(candidate_url, timeout_ms / 1000)
            if not page_allowed:
                continue
            try:
                page.goto(candidate_url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(600)
            except Exception:
                continue
            for item in raw_forms(page, page.url):
                evaluated = evaluate_form_policy(item)
                evaluated["formId"] = hashlib.sha256(
                    f"{gym['id']}|{evaluated['url']}|{evaluated['formIndex']}|{evaluated['termsHash']}".encode("utf-8")
                ).hexdigest()[:20]
                result["forms"].append(evaluated)
        result["forms"] = list({item["formId"]: item for item in result["forms"]}.values())
        if not result["forms"]:
            result["status"] = "no-form-found"
    except Exception as exc:
        result["status"] = "render-error"
        result["error"] = redact_sensitive(text(exc))[:240] or type(exc).__name__
    finally:
        context.close()
    return result


def discover(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Form discovery requires Playwright: pip install playwright && python -m playwright install chromium") from exc
    document = load_json(SOURCE_PATH, {"gyms": []})
    gyms = candidate_gyms(document)
    if args.gym_id:
        ids = set(args.gym_id)
        gyms = [gym for gym in gyms if text(gym.get("id")) in ids]
    if args.limit:
        gyms = gyms[: args.limit]
    observed_at = args.date or datetime.now(UTC).date().isoformat()
    existing = load_json(MANIFEST_PATH, {"records": []})
    by_id = {text(item.get("gymId")): item for item in existing.get("records", [])}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for gym in gyms:
                by_id[text(gym.get("id"))] = discover_gym(browser, gym, args.timeout_ms, observed_at)
        finally:
            browser.close()
    records = sorted(by_id.values(), key=lambda item: (text(item.get("gymName")), text(item.get("gymId"))))
    save_json(MANIFEST_PATH, {
        "generatedAt": observed_at,
        "policy": "Read-only discovery; submission requires an exact local domain and terms-hash approval.",
        "records": records,
    })
    save_json(REPORT_PATH, build_discovery_report(records, observed_at))
    print(json.dumps({"candidateGyms": len(gyms), "forms": sum(len(item.get("forms", [])) for item in records)}))
    return 0


def approve_domain(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH, {"records": []})
    matching = [
        form for record in manifest.get("records", []) for form in record.get("forms", [])
        if text(form.get("domain")) == args.domain and text(form.get("termsHash")) == args.terms_hash
    ]
    if not matching:
        raise SystemExit("No discovered form matches that exact domain and terms hash.")
    approvals = load_json(APPROVAL_PATH, {"approvals": []})
    approvals["approvals"] = [
        item for item in approvals.get("approvals", [])
        if not (text(item.get("domain")) == args.domain and text(item.get("termsHash")) == args.terms_hash)
    ]
    action_domains = sorted(set(args.action_domain or []) | {args.domain})
    approvals["approvals"].append({
        "domain": args.domain,
        "termsHash": args.terms_hash,
        "actionDomains": action_domains,
        "approvedAt": datetime.now(UTC).isoformat(),
        "scope": "one-pricing-inquiry-form; no accounts; no SMS/call consent",
    })
    save_json(APPROVAL_PATH, approvals)
    print(json.dumps({"approvedDomain": args.domain, "termsHash": args.terms_hash, "actionDomains": action_domains}))
    return 0


def contact_values() -> dict[str, str]:
    base_email = text(os.environ.get("GYM_RESEARCH_EMAIL"))
    values = {
        "name": text(os.environ.get("GYM_RESEARCH_NAME")),
        "email": plus_address(base_email, text(os.environ.get("GYM_RESEARCH_EMAIL_TAG")) or "sfgyms"),
        "phone": text(os.environ.get("GYM_RESEARCH_PHONE")),
        "postal-code": ZIP_CODE,
        "message": INQUIRY_MESSAGE,
    }
    return values


def find_manifest_form(gym_id: str, form_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(MANIFEST_PATH, {"records": []})
    for record in manifest.get("records", []):
        if text(record.get("gymId")) != gym_id:
            continue
        for form in record.get("forms", []):
            if text(form.get("formId")) == form_id:
                return record, form
    raise SystemExit("Form not found in the current discovery manifest.")


def submission_allowed(form: dict[str, Any], approvals: list[dict[str, Any]], values: dict[str, str]) -> list[str]:
    blockers = [item for item in form.get("blockers", []) if item != "external-action-requires-separate-approval"]
    approval = next((item for item in approvals if approval_valid(item, form)), None)
    if approval is None:
        blockers.append("domain-or-terms-not-approved")
    for field in form.get("fields", []):
        category = text(field.get("category"))
        if field.get("required") and category in values and not values[category]:
            blockers.append(f"missing-local-{category}")
    return sorted(set(blockers))


def submit(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Form submission requires Playwright: pip install playwright && python -m playwright install chromium") from exc
    record, discovered_form = find_manifest_form(args.gym_id, args.form_id)
    approvals = load_json(APPROVAL_PATH, {"approvals": []}).get("approvals", [])
    values = contact_values()
    blockers = submission_allowed(discovered_form, approvals, values)
    state = load_json(STATE_PATH, {"submissions": []})
    prior = next((item for item in state.get("submissions", []) if item.get("gymId") == args.gym_id and item.get("formId") == args.form_id), None)
    if prior:
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(text(prior.get("submittedAt")))
            if age.days < RESUBMIT_AFTER_DAYS:
                blockers.append("recent-submission-already-recorded")
        except ValueError:
            blockers.append("invalid-prior-submission-state")
    if blockers:
        raise SystemExit("Submission blocked: " + ", ".join(sorted(set(blockers))))

    submitted_at = datetime.now(UTC).isoformat()
    outcome = "unknown"
    response_candidates: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(java_script_enabled=True, service_workers="block")
        page = context.new_page()
        try:
            page.goto(text(discovered_form.get("url")), wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(750)
            live_forms = [evaluate_form_policy(item) for item in raw_forms(page, page.url)]
            live = next((item for item in live_forms if item["termsHash"] == discovered_form["termsHash"]), None)
            if live is None:
                raise SystemExit("Submission blocked: live form changed since approval; rediscover and reapprove.")
            form_locator = page.locator("form").nth(int(discovered_form["formIndex"]))
            elements = form_locator.locator("input, textarea, select, button")
            for field in discovered_form.get("fields", []):
                if field.get("disabled") or field.get("category") == "ignored":
                    continue
                locator = elements.nth(int(field["fieldIndex"]))
                category = text(field.get("category"))
                consent_type = text(field.get("consentType"))
                if category == "consent":
                    if field.get("required") and consent_type in {"terms", "email-marketing"}:
                        locator.check()
                    continue
                value = values.get(category, "")
                if value and (field.get("required") or category in {"email", "message", "name"}):
                    if text(field.get("tag")) == "select":
                        raise SystemExit("Submission blocked: required select fields need form-specific review.")
                    locator.fill(value)
            submit_button = form_locator.locator('button[type="submit"], input[type="submit"], button:not([type])').first
            if submit_button.count() == 0:
                raise SystemExit("Submission blocked: no explicit submit control.")
            submit_button.click(timeout=args.timeout_ms)
            page.wait_for_timeout(1500)
            visible = page.locator("body").inner_text(timeout=args.timeout_ms)
            sanitized = redact_sensitive(visible, list(values.values()))
            outcome = "submitted-confirmation-visible" if re.search(r"\b(?:thank|received|submitted|we(?:'|’)ll (?:contact|reply))\b", sanitized, re.I) else "submitted-no-confirmation-detected"
            for candidate in crawler.visible_candidates(sanitized, page.url):
                candidate_amount = candidate.get("amount")
                candidate_type = text(candidate.get("productType"))
                candidate_cadence = text(candidate.get("cadence"))
                response_candidates.append({
                    "gymId": args.gym_id,
                    "gymName": text(record.get("gymName")),
                    "capturedAt": submitted_at[:10],
                    "amount": candidate_amount,
                    "currency": text(candidate.get("currency")) or "USD",
                    "productType": candidate_type,
                    "cadence": candidate_cadence,
                    "rawLabel": f"{candidate_type} ${candidate_amount:g} per {candidate_cadence}",
                    "promotion": candidate.get("promotion") or {"isPromotion": False, "label": ""},
                    "eligibility": candidate.get("eligibility") or {"type": "unknown", "restrictions": []},
                    "sourceTier": "private-operator-response",
                    "reviewStatus": "pending",
                    "publiclyReproducible": False,
                })
        finally:
            context.close()
            browser.close()

    state.setdefault("submissions", []).append({
        "gymId": args.gym_id, "formId": args.form_id, "domain": discovered_form["domain"],
        "termsHash": discovered_form["termsHash"], "submittedAt": submitted_at,
    })
    save_json(STATE_PATH, state)
    attempts = load_json(ATTEMPTS_PATH, {"attempts": []})
    attempts.setdefault("attempts", []).append({
        "gymId": args.gym_id, "gymName": text(record.get("gymName")), "formId": args.form_id,
        "domain": discovered_form["domain"], "termsHash": discovered_form["termsHash"],
        "submittedAt": submitted_at, "outcome": outcome, "candidateCount": len(response_candidates),
        "containsContactData": False,
    })
    save_json(ATTEMPTS_PATH, attempts)
    observations = load_json(OBSERVATIONS_PATH, {"observations": []})
    observations.setdefault("observations", []).extend(response_candidates)
    save_json(OBSERVATIONS_PATH, observations)
    print(json.dumps({"gymId": args.gym_id, "outcome": outcome, "candidateCount": len(response_candidates)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--gym-id", action="append", default=[])
    discover_parser.add_argument("--limit", type=int, default=0)
    discover_parser.add_argument("--timeout-ms", type=int, default=20000)
    discover_parser.add_argument("--date")
    discover_parser.set_defaults(handler=discover)
    report_parser = subparsers.add_parser("report")
    report_parser.set_defaults(handler=lambda _args: write_discovery_report(load_json(MANIFEST_PATH, {"records": []})))
    approval_parser = subparsers.add_parser("approve-domain")
    approval_parser.add_argument("--domain", required=True)
    approval_parser.add_argument("--terms-hash", required=True)
    approval_parser.add_argument("--action-domain", action="append", default=[])
    approval_parser.set_defaults(handler=approve_domain)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--gym-id", required=True)
    submit_parser.add_argument("--form-id", required=True)
    submit_parser.add_argument("--timeout-ms", type=int, default=30000)
    submit_parser.set_defaults(handler=submit)
    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
