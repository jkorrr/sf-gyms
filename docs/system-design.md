# SF Gyms system design

SF Gyms is deliberately split into a static, useful read-only experience and a separately deployable Python API. The public map can run from GitHub Pages, while authenticated workflows and privileged operations can move to FastAPI without changing the database model or mobile contract.

## Runtime boundaries

```text
Browser / future Expo app
        |
        |  Supabase Auth (PKCE) + public reads protected by RLS
        v
Supabase PostgreSQL + PostGIS + Storage
        ^
        |  server-side service role only
        |
FastAPI API / admin tools / imports  --->  Render or another container host
```

- `apps/web` is a static-exportable Next.js application. In GitHub Pages mode it renders demo or public data without requiring the API to be online.
- `apps/api` is stateless. It owns privileged workflows, imports, moderation, audit events, and future business logic.
- Supabase is the system of record. SQL migrations, RLS policies, seed fixtures, and data validation rules remain in this repository so a free-tier project can be recreated or restored.
- `packages/api-contract/openapi.yaml` is the compatibility boundary shared by web, API, and future mobile clients.

## Data and consistency model

Gym facts are normalized around `gyms`, `gym_locations`, `price_plans`, and append-only `price_assertions`. The UI displays the latest trusted assertion; it never overwrites history. A source, actor, verification date, and effective date make stale or conflicting prices explainable.

The database is the concurrency coordinator:

- unique constraints prevent duplicate saves, source records, and active claims;
- transactions cover multi-step writes;
- `version` and `updated_at` support optimistic edits and HTTP `409 Conflict` responses;
- short `FOR UPDATE` sections protect claim approval and ownership transfer;
- transaction-scoped advisory locks serialize imports by city and protect price append operations;
- idempotency keys make retried leads, reports, claims, reviews, and future payments safe;
- background imports must retry with exponential backoff and record terminal failures in a dead-letter table before they are enabled in production.

Never hold a database lock while calling an external service. Keep API instances stateless and use Supabase's supported pooler, connection timeouts, and bounded pools. Public, non-personalized gym data may be cached with `updated_at` or ETags; authenticated responses must retain the user/session boundary.

## Map behavior

The current map is provider-neutral so the product can validate demand before taking on map-key cost. A real map adapter can later use MapLibre, Google Maps, or another provider without changing gym queries.

Production map requests should:

1. debounce viewport/filter changes;
2. cancel the previous request with `AbortController`;
3. attach a monotonically increasing request sequence to every response;
4. request bounded pages or vector tiles instead of a whole city;
5. show a freshness/update state if a selected listing changed while it was open.

The API should expose a stable bounding-box/radius query and cursor pagination. A materialized geographic read model, search index, read replica, or Redis cache can be added later behind the same contract.

## Google sign-in lifecycle

Supabase Auth is the identity provider; application roles live in `profiles` and are never inferred from a Google claim. Web and mobile use PKCE with separate Google OAuth clients. Register exact origins and redirect URLs for local, staging, GitHub Pages, Android, and iOS; do not use wildcards.

For GitHub Pages, the project-root URL (`/sf-gyms/`) is the callback target. The browser exchanges the single-use code, immediately removes it from the URL, and rejects an invalid destination unless it is on an allowlist. The PKCE verifier is scoped to the browser tab. Expired, reused, or mismatched codes restart login without logging codes or tokens.

Profile creation is an idempotent upsert triggered by first login. Multiple tabs may observe the same auth event safely. Do not merge accounts solely by email; add an explicit verified identity-linking flow later. Refresh-token rotation failures clear the session and require reauthentication. Future SSR must create a Supabase client per request, use secure HTTP-only cookies, and disable CDN caching on auth-cookie routes.

Expo/React Native should use native Google clients, universal/deep links, and platform-secure storage. It should reuse this API contract and Supabase project, never a Google client secret.

## Security and privacy controls

- Validate every FastAPI request with Pydantic and authorize both the function and the object being changed.
- Keep Supabase service-role keys, database credentials, OAuth secrets, map secrets, and admin credentials server-side only.
- Enable RLS on every exposed table. Public reads are limited to published gym data; user, owner, moderator, and admin records are scoped by role and ownership.
- Add rate limits and bot protection to search-heavy or abuse-prone endpoints, especially reviews, reports, claims, leads, and login.
- Use strict CORS, HTTPS/HSTS, CSP, secure headers, restrictive referrer policy, and CSRF protection whenever cookie-authenticated mutations are introduced.
- Render reviews as text, validate upload type/size/dimensions, scan files, and never fetch arbitrary user URLs from the server (SSRF protection).
- Prevent open redirects, redact PII/tokens from structured logs, and audit pricing, claims, moderation, roles, and admin actions.
- Run Ruff, mypy, Bandit, pip-audit, dependency updates, secret scanning, and container vulnerability scans in CI.
- Provide Terms, Privacy, account deletion, data correction, and review-appeal workflows before broad user-generated content launch.

The security test plan includes broken object/function authorization, unrestricted resource consumption, SSRF, unsafe API consumption, misconfiguration, RLS bypass attempts, stale writes, and duplicate/replayed requests.

## Deployment and evolution

Pull requests run tests, lint, type checks, security checks, accessibility checks, and a static build without production secrets. `main` publishes the static artifact to `https://jkorrr.github.io/sf-gyms/` when the repository/account plan permits private-repository Pages; otherwise publish a sanitized public frontend repository or use a free static host connected to this private source repository. The existing `jkorrr.github.io` user site is not modified.

Use expand/migrate/contract database changes: add nullable or additive fields first, ship clients that understand both shapes, backfill, then remove old fields in a later migration. This keeps older web and mobile clients compatible. Maintain exports/backups and perform restore drills because Supabase Free projects have limited capacity and may pause when inactive.

Observability should eventually include request IDs, latency/error metrics, database pool saturation, import freshness, moderation queues, auth failures, and alerting on unusual write or rate-limit patterns. Keep analytics consent-aware and avoid collecting precise location or sensitive workout information unless it is necessary.
