# Official web research imports

Each `sf-gym-web-research-*.json` file is an auditable batch of public observations from official operator pages or operator-owned booking widgets. `merge_web_research.py` discovers every matching batch automatically, so a new lettered batch does not require a code change. The merge always begins with `sf-gyms-osm-raw.json`, pinned from the original OSM-only revision; generated `sf-gyms-osm.json` files are outputs and are never reused as merge inputs.

`official-location-overrides.json` contains reviewed identity decisions. Canonical records receive stable location/operator IDs, canonical addresses, source aliases, and `publicationStatus`. Only the same reviewed operator at the same canonical street address or with a shared operator location ID can merge automatically. Distinct non-empty operator location IDs prevent an address-only merge, which preserves separately operated co-located facilities such as a pool and recreation center. Different operators at one address, replacements, relocations, near-coordinate matches, and spelling-only matches fail closed for review.

## Neutral plan selection

For one comparable `monthlyPrice` per location:

1. Use the lowest publicly listed, ongoing recurring plan that permits ordinary use of the named location.
2. Exclude introductory, first-month, student, employer, resident, age-restricted, and invitation-only prices.
3. Exclude prepaid annual and fixed-term commitments when a clean month-to-month option exists.
4. For class studios, use the smallest ongoing monthly class plan, not a trial bundle.
5. Normalize non-monthly recurring billing to a calendar month and retain the original amount and interval in `billingIntervalPrice` and `billingInterval`.
6. Keep `annualFee`, `enrollmentFee`, `initiationFee`, `processingFee`, and `activationFee` separate from `monthlyPrice`.
7. Use `null` when a public source does not disclose a safely comparable value. Never infer a price from another city, club, or plan tier.

`dayPassPrice` is the standard public single visit or single class. Free trials, member-hosted discounts, and intro offers are described in `priceNote` but are not substituted for the standard amount.

Every absent compatibility price has its own explanation: `monthlyPriceBlocker` describes why a verified recurring amount is unavailable and `dayPassPriceBlocker` does the same for an ordinary unrestricted visit. `metadataStatus` records an explicit listed, not-published, not-found, or not-applicable state for official URL, amenities, and operator location ID. Hours additionally distinguish `exact-hours` from truthful `access-schedule` semantics used by appointment-, reservation-, class-, and program-based facilities. Named public plans whose amount is contact-gated remain in `plans[]` with `billing.amount: null`; they are never coerced to `$0` and can never become the selected verified plan.

`catalogStatus.plans` and `catalogStatus.dropIns` distinguish a reviewed source catalog from a legacy selected-price wrapper and a genuinely absent catalog. A verified compatibility price therefore never implies that every alternative product has already been reconstructed. Reviewed hours, amenities, schedule semantics, operator IDs, and URL corrections recovered in broad audits live in `official-metadata-recovery-*.json`; they use the same approved-discovery field allowlist and remain separate from price observations.

`official-operator-catalog-approved.json` is the reviewed multi-location catalog layer. Every approval must enumerate the exact canonical location IDs it applies to, match their canonical `operatorId`, provide an HTTPS official source and observation date, and include only allowlisted catalog fields. Operator identity by itself never triggers propagation. A scoped rebuild containing none of an approval's locations ignores that approval; once any target is present, a missing target or operator mismatch fails closed.

## Public JavaScript-backed sources

Inspect sources in this order:

1. Server-rendered JSON-LD `Product`/`Offer` data.
2. Public operator APIs, XHR responses, or embedded hydration data used by the visible page.
3. The rendered plan cards in an operator-owned booking or pricing widget.
4. Official help/FAQ text when no structured price is published.

Record the final official page or widget URL in `priceSourceUrl`, the observation date in `priceObservedAt`, and enough cadence, access, promotion, and fee context in `priceNote` to audit the selection. Do not create accounts or enter contact/payment data merely to fill a missing price.

## Merge and verification

From the repository root:

```powershell
python -m unittest discover -s data/imports -p 'test_*.py'
python data/imports/merge_web_research.py --date 2026-08-19
```

The merge checks exact and canonical street addresses before geocoding, collapses same-brand duplicate imports at real street addresses, preserves stable OSM IDs where available, and writes both the source and web fixtures. Pass `--date YYYY-MM-DD` in verification and automation so `importedAt` is pinned and repeated same-date builds are byte-identical.

`official-location-overrides.json` is the audited identity/status layer for stale source aliases, exact official titles, and announced locations. Suppression entries must include an official locator URL and a reason; they remove a duplicate or stale alias, never an unreviewed listing.

## Cost coverage

`cost_coverage.py` classifies every published listing, reconstructs complete reviewed `plans[]` and `dropIns[]`, deterministically assigns `selectedPlanId`/`selectedDropInId`, derives compatibility prices only from those selections, and writes the publication report and manual-review queue. Mandatory fees remain attached to the observed plan. A selection with missing evidence, promotional/restricted eligibility, a source conflict, or an invalid product fails closed.

The same stage replaces source-boilerplate descriptions with factual record-specific summaries derived only from classification, neighborhood, selected price state, and listed amenities. The coverage report separately counts specific descriptions, official URLs, listed hours, listed amenities, operator location IDs, and explicit gap states; publication fails if a null price or metadata gap becomes silent.

Estimates never populate `monthlyPrice` or `dayPassPrice`. High confidence requires at least four tightly clustered same-operator observations and no more than 10% leave-one-out median error. Medium confidence requires at least eight comparable modality/access observations, no more than 20% error, and at least 75% validation range coverage. Low-confidence estimates are not published. Displayed ranges use conservative nearest-rank 10th–90th percentiles of leave-one-out residual ratios (an approximately 80% calibrated interval); bounds round outward to $5 and never narrow between observed residuals.

`crawl_official_sources.py` checks committed official URLs and linked operator-owned storefronts. Each listing is seeded from its reviewed website, official page, price source, plan/drop-in evidence, and cost-context URLs, capped at eight routes; evidence routes must share the reviewed operator host or use an approved public booking domain, and account, login, cart, checkout, marketplace, and directory URLs are rejected. This lets a known pricing route be rechecked even when the operator homepage is missing or stale. It extracts JSON-LD, embedded JSON hydration state, visible price cards, same-operator pricing/package links, and public Mindbody, Momence, WellnessLiving, ClubReady/Xponential, PushPress, Wodify, Zen Planner, GymDesk, Acuity/Squarespace Scheduling, Bookee, Mariana Tek, and Eventbrite data. Explicit visible ranges, starting prices, and JSON-LD `AggregateOffer` bounds become non-selectable cost-context candidates instead of false exact low-end prices; promotions and bare numeric ranges are rejected. `platform_adapters.py` converts public vendor JSON into a shared review shape with source product IDs, cadence, allowance, commitment, promotions, operator labels, and plan-linked fees. Xponential catalogs are followed into read-only package-detail endpoints so mandatory fees remain tied to the correct plan. The crawler uses eight workers globally, serializes each host, waits at least 1.5 seconds between same-host requests, honors `Retry-After`, stops a host after its second 429, respects robots directives, and uses conditional requests. It never submits a form or authenticates.

Operator-card adapters also reconstruct the rendered 24 Hour Fitness membership matrix, Equinox club access tiers, Planet Fitness plan-linked startup/annual fees, and Crunch regular-versus-promotional cards. These adapters remain review-only. If the visible catalog and its linked public enrollment product disagree—as currently observed on Live Fit's legacy signup page—the catalog stays incomplete and the conflict remains in the recovery queue rather than being promoted.

`render_official_sources.py` is the headless-Chromium fallback for pages whose static response is empty, failed, or conflicting. It may activate only neutral public Memberships/Packages/Pricing tabs and captures JSON only from the operator host or approved booking hosts. It stores short evidence labels and hashes, never full review text or personal data, and never fills a form. Its output is review-only. In `deals` mode it revisits every open published commercial operator page, combines rendered and static promotion candidates, and writes `deal-observations.json`; only human-reviewed entries copied to `deal-approved.json` can appear on the site. A deal is never the ordinary selected plan and expires from display after seven days without reconfirmation (or at its stated expiration date).

Mindbody links receive an additional bounded recovery pass. Any reviewed `clients.mindbodyonline.com` deep link with a numeric `studioid` is normalized to that business's unauthenticated Services route, and public numeric `optTG` category IDs are followed with memberships/access categories first and a twelve-category cap. Account, gift-card, cart, checkout, and nonnumeric actions are excluded. A blank HTTP-200 shell whose only action is `mb.sessionHelpers.resetSession()` is recorded as `identity-session-reset-required`, because completing that flow would submit an identity form; the bot never does so. Rendered Mindbody cards become monthly only when the card explicitly says monthly, per month, every month, recurring, or auto-renewing. Fixed-duration labels such as “3 Month Special” remain one-time fixed-term offers and cannot inflate the neutral monthly comparison.

`build_catalog_review.py` is the fail-closed bridge between crawling and publication. It groups source-product JSON, visible plan cards, and context-only official ranges into `official-catalog-review.json`, rejects loose price-shaped text, and flags one-product/multiple-price or one-product/multiple-range conflicts. It still does not publish. A reviewer must check the exact location, standard-adult eligibility, plan-card association, promotions, and fee linkage before promoting a clean proposal:

```powershell
python data/imports/build_catalog_review.py --date YYYY-MM-DD
python data/imports/review_catalogs.py approve --gym-id GYM_ID --date YYYY-MM-DD --confirm-exact-location --confirm-standard-adult --confirm-plan-card-association --confirm-fees-linked --confirm-promotions-marked
python data/imports/cost_coverage.py --date YYYY-MM-DD
```

After approval, `cost_coverage.py` independently selects the neutral basic plan, the median-priced typical plan, the cheapest highest-access plan, and an operator-labeled best-value plan when one is explicitly marked. Promotions remain in the deal tier and official ranges remain unselectable `costContext[]`; neither can leak into verified compatibility prices. A reviewed nonconflicting range receives the explicit `official-range` status, remains excluded from exact-price filters, and supersedes weaker estimates without pretending that either endpoint is the buyer's exact price.

On monthly full runs, `discover_operator_documents.py` groups listings by reviewed operator origin, reads declared sitemaps (or the conventional root sitemap), and emits same-origin pricing, membership, package, pass, join, and location URLs to `operator-document-candidates.json`. It uses up to eight workers across unrelated hosts while keeping each host strictly sequential, requests at most three sitemap documents per host, obeys robots directives, and treats every result as a lead. Conditional-request metadata and extracted URL leads are retained in `operator-document-cache.json`; raw sitemap content is not committed. Exact-location and same-operator pricing leads may be crawled into review candidates, but they cannot publish a price without approval. This catches operator directories and structured locators without scraping search-result pages.

The same monthly run uses `discover_archive_signals.py` for official URLs whose current crawl fails. It queries the Internet Archive CDX API and the latest Common Crawl index, retaining only capture timestamps, URLs, status codes, digests, and index filenames in `archive-status-signals.json`. Archived content is a status/identity lead only and can never verify a current plan or price.

Reviewers can list and explicitly promote one candidate without hand-editing JSON. Both confirmation flags are required:

```powershell
python data/imports/review_deals.py list
python data/imports/review_deals.py approve --id DEAL_ID --confirm-standard-adult --confirm-ordinary-price-separate --review-note "Checked official offer terms"
python data/imports/cost_coverage.py --date YYYY-MM-DD
```

## Polling schedule and runtime bounds

The GitHub Actions workflow starts at `01:17`, `07:17`, `13:17`, and `19:17 UTC`. The three non-13:17 runs poll the dedicated research-mail label and send only secret-stored, pre-approved inquiries. The 13:17 run refreshes deals Tuesday through Sunday, runs the broader recovery queue on Mondays, and upgrades the first Monday of each month to the complete 355-input audit. GitHub may queue scheduled jobs. The whole job has a hard two-hour timeout. Rendered pages have a 15-second navigation timeout and static requests have a 1.5-second same-host minimum; a deals pass should normally take roughly 15–45 minutes, while a monthly full pass may approach the cap when many operator sites are slow. The report records attempts and failures so a timeout cannot silently become a price.

Verified observations are considered stale after 35 days unless reconfirmed. Approved deal cards require evidence no more than seven days old. Private operator confirmations remain current for 90 days. The local inquiry worker will not resubmit the same approved form for 180 days.

## Approval-gated pricing inquiries

`contact_research.py` is intentionally local-only and is not called by GitHub Actions. Discovery renders gated/unresolved operator pages without filling a field and writes only form metadata, consent labels, and hashes:

```powershell
python data/imports/contact_research.py discover --limit 10
```

The sanitized summary is written to `contact-research-report.json`; regenerate it without network access with `python data/imports/contact_research.py report`.

Review a form in `contact-form-manifests.json`, then explicitly approve its exact domain and current terms hash. If the form posts to a different host, that exact action domain must also be named:

```powershell
python data/imports/contact_research.py approve-domain --domain operator.example --terms-hash HASH --action-domain operator.example
```

Load `GYM_RESEARCH_NAME`, `GYM_RESEARCH_EMAIL`, and, only when a required phone field is acceptable, `GYM_RESEARCH_PHONE` into the local shell. The worker plus-addresses the email with `GYM_RESEARCH_EMAIL_TAG` and uses ZIP `94107`. Submit one approved pricing inquiry by stable gym and form ID:

```powershell
python data/imports/contact_research.py submit --gym-id GYM_ID --form-id FORM_ID
```

Submission fails closed if the form changes, its domain/action is not approved, it requests split first/last names, authentication, payment, DOB/address, a CAPTCHA, unknown required fields, or required SMS/call consent. Optional marketing consent stays unchecked; a required email-only marketing checkbox is the sole marketing exception. It does not create accounts. Contact values and raw responses are never committed; local approvals/submission state live under ignored `data/private/`.

Save operator replies as `.eml` files under `data/private/operator-replies/` with `[sf-gyms:GYM_ID]` in the subject, then run:

```powershell
python data/imports/import_operator_replies.py
```

The importer stores only a message hash, date, gym ID, confidentiality flag, and redacted structured review candidates in `operator-confirmed-observations.json`. It separates exact prices from ranges, strips quoted thread history, parses cadence and normalized monthly cost, retains disclosed class allowance and commitment, keeps standalone fee candidates separate, and links mandatory fees only when the reply contains exactly one recurring plan. It also records a strict effective date and explicit `no standard plan` or `custom pricing` statements when present. Human review must still confirm the exact plan and promote a non-confidential standard-adult observation into `operator-confirmed-approved.json`. Operator-confirmed prices remain a distinct non-public tier and never populate verified-only `monthlyPrice` or `dayPassPrice`.

For scheduled direct email, configure a dedicated research mailbox with Gmail OAuth secrets `GYM_RESEARCH_GMAIL_CLIENT_ID`, `GYM_RESEARCH_GMAIL_CLIENT_SECRET`, and `GYM_RESEARCH_GMAIL_REFRESH_TOKEN`. Give it only `gmail.send` and `gmail.readonly`, and apply the `sf-gym-pricing` label to research threads. Approved recipient/domain/location records are supplied as base64 JSON in the encrypted `GYM_RESEARCH_EMAIL_APPROVALS_B64` Actions secret; each approval must include the exact recipient/source domains, `exactLocationConfirmed`, `publicOperatorEmailConfirmed`, `templateVersion`, and the current immutable template hash from `python data/imports/gmail_research.py template-hash --template-version v2`. Changed or incomplete approvals fail closed. Version 2 names the reviewed gym and canonical address and asks for a structured basic plan, allowance, cadence, commitment, mandatory fees, ordinary drop-in, and effective date; legacy version-1 approvals remain valid only for the unchanged version-1 body. No address book, token, recipient address, message body, Gmail ID, or personal value is committed. `gmail_research.py` uses stable `[SFGYM:location-id]` subjects, sends one inquiry plus at most one follow-up after 14 days, enforces a 180-day cooldown, polls replies every six hours for 60 days, and writes only sanitized review candidates. Replies publish as `operator-confirmed` only after human approval.

`discover_public_sources.py` works the remaining no-site queue against free public identity sources. The current adapter queries DataSF registered-business records for business name, address, and status dates only. It also emits `manual-source-search.json` with exact identity queries and same-operator official URL hints. It does not collect owner or contact data, scrape search-result pages, or mutate a listing. Strong and ambiguous matches are written to `public-source-discovery-observations.json` for human review; only entries explicitly marked `approved` in `public-source-discovery.json` may change the fixture.

`reported-price-evidence.json` is the structured intake for recent non-operator price reports. It stores only the claimed amount/cadence, exact-location match, observation date, and URL--never copied review prose or ratings. A report must be no more than 18 months old, standard-adult, and approved. One report remains internal evidence; at least two independent matching URLs are required before the site can show a `Recently reported` monthly price. Reported values never populate verified compatibility fields or supply mandatory fees.

`audit_reported_evidence.py` rechecks availability, freshness, a content hash, and whether the structured amount remains visible. Changed or unconfirmable reports are flagged for review. Conflicting eligible reports produce a visible range and warning rather than false precision.

```powershell
python data/imports/materialize_raw_osm.py
python data/imports/merge_web_research.py --date 2026-08-19
python data/imports/cost_coverage.py --date 2026-08-19
python data/imports/discover_public_sources.py
python data/imports/crawl_official_sources.py --mode weekly
python data/imports/render_official_sources.py --mode weekly
python data/imports/build_catalog_review.py --date 2026-08-19
python data/imports/audit_reported_evidence.py
python data/imports/merge_web_research.py --date 2026-08-19
python data/imports/cost_coverage.py --date 2026-08-19
python -m unittest discover -s data/imports -p 'test_*.py'
```

The scheduled workflow polls deals daily, runs broader recovery weekly, upgrades the first Monday of each month to a full crawl, validates the site, and opens or updates a data-review pull request. It never deploys candidate data automatically.

## Database synchronization

The committed JSON fixture remains the reviewed publication input. After review,
`sync_directory_database.py` stores every canonical record losslessly in
`public.gym_directory_records`; it does not flatten complete catalogs into the
older compatibility tables. `public.gym_directory_sync_runs` records the source
hash and count so an incomplete upload cannot be presented as current. Public
RLS access is limited to records whose `publicationStatus` is `publish`, while
sync-run audit metadata has no public policy.

Keep the Postgres connection string only in `DATABASE_URL`. The command does not
accept a URL argument, never prints it, and rejects secret-shaped fields in the
fixture. Apply the migration and atomically push the reviewed snapshot, then
regenerate both committed fixtures from the database:

```powershell
python data/imports/sync_directory_database.py verify
python data/imports/sync_directory_database.py roundtrip --apply-migration
```

The manual GitHub workflow uses the protected `production-data` environment and
the encrypted `SF_GYMS_DATABASE_URL` Actions secret. Only the secret name appears
in the repository. Do not use a publishable/anon key for this trusted write and
never commit a direct Postgres URL, database password, Supabase service-role key,
or OAuth credential.
