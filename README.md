# SF Gyms

A Supabase-backed, concurrency-safe gym discovery platform for people moving to San Francisco.

The first release is deliberately split into three layers:

- `apps/web`: a static-exportable Next.js frontend that can run on GitHub Pages.
- `apps/api`: a Python FastAPI service for trusted commands, imports, admin workflows, and future mobile clients.
- `supabase`: PostgreSQL/PostGIS migrations, RLS policies, auth triggers, and seed data.

## Local development

### Web demo

```powershell
cd apps/web
npm install
npm run dev
```

The web app runs in a safe read-only demo mode when Supabase variables are absent or invalid. It includes a real MapLibre GL JS map, OpenFreeMap vector tiles with an OpenStreetMap raster fallback, OpenStreetMap attribution, 20px gym markers, neighborhood and price filters, gym detail cards, local save/compare state, and the Google OAuth callback path. The page explains which capability is unavailable instead of failing during static rendering.

### Refresh the San Francisco directory

The committed directory is a normalized snapshot of named fitness facilities found in OpenStreetMap through the Overpass API. It intentionally does not scrape Google or private business sites. Refresh it from the repository root with:

```powershell
python data/imports/import_osm_sf_gyms.py
```

The importer writes both the source fixture at `data/imports/sf-gyms-osm.json` and the static web fixture at `apps/web/lib/sf-gyms-osm.json`. Neighborhood labels are tagged from OSM when available and otherwise assigned from approximate San Francisco coordinate boxes. OSM coverage is useful but not exhaustive.

Official price observations are kept separately in `data/imports/official-price-overrides.json`. Each observation includes the official source URL, the date checked, and a note explaining whether the value is a starting price, day pass, or plan-specific rate. These values are intentionally displayed with provenance and a reminder to confirm current rates; they are not a promise that every plan at a gym costs that amount. Refresh them manually when a gym changes its public pricing.

Location search is an explicit, user-triggered single lookup against public Nominatim. It is not autocomplete or bulk geocoding. The current-location option computes distance locally in the browser.

### API

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --app-dir apps/api --reload
```

The API exposes `/healthz`, `/api/v1/gyms`, and the versioned OpenAPI contract. Set `DEMO_MODE=false` and `DATABASE_URL` after connecting a Supabase or local Postgres database.

### Supabase

1. Create a Supabase project.
2. Run `supabase/migrations/0001_initial.sql` in the SQL editor or with the Supabase CLI.
3. Configure Google under Supabase Auth providers.
4. Add exact redirect URLs for local development and GitHub Pages.
5. Copy `.env.example` to `.env` and fill in the browser-safe public URL and publishable key.

The web client accepts `NEXT_PUBLIC_SUPABASE_URL` plus either a current `sb_publishable_...` key or an older browser-safe anon JWT. When both are valid, Google sign-in uses Supabase Auth with PKCE and the session is persisted by the Supabase browser client. When they are missing, incomplete, placeholder values, or malformed, the directory stays usable in demo mode and does not expose a stack trace or secret-shaped value. The current static prototype stores saved gyms locally; cloud-save syncing should be enabled only after imported listings have stable Supabase `gym_location_id` values and their RLS policies are verified.

Never put `SUPABASE_SERVICE_ROLE_KEY`, database credentials, or Google client secrets in the frontend or GitHub Pages artifact.

For GitHub Pages, public build variables must be provided to the Pages build job (for example as GitHub Actions **Variables**, not committed files):

```text
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
```

These values are intentionally public in the static bundle. Do not substitute `SUPABASE_SERVICE_ROLE_KEY`, `sb_secret_...`, a database password, or a Google OAuth client secret. The repository's CI currently builds the public demo fixture; enabling cloud authentication in the deployed Pages artifact requires wiring these two public variables into the Pages build environment after the Supabase redirect allowlist is configured.

## GitHub Pages

The project site target is:

```text
https://jkorrr.github.io/sf-gyms/
```

GitHub Pages only hosts the static frontend. The FastAPI service is independently deployable as a Docker container. The Pages workflow uses `NEXT_PUBLIC_BASE_PATH=/sf-gyms` and deploys only from `main`.

Before the first deployment, open the repository's **Settings → Pages** and select **GitHub Actions** as the source. The repository must be public or the GitHub account must have a plan that supports Pages for private repositories. If the workflow's build job succeeds but deployment returns `404: Ensure GitHub Pages has been enabled`, enable that setting (or use the public-repository/static-host fallback described in the system design); no source code change is required.

## Design and architecture notes

- Public reads are safe to cache; authenticated responses are not.
- Price history is append-only through `price_assertions`.
- Unique constraints, idempotency keys, version columns, and short transactions provide concurrency safety.
- RLS is enabled on all exposed Supabase tables.
- OAuth uses PKCE and a root callback compatible with static GitHub Pages hosting.
- API contracts are versioned so a future Expo/React Native app can use the same backend.
- MapLibre uses `https://tiles.openfreemap.org/styles/liberty`; if the public vector style does not finish loading, the UI automatically switches to OpenStreetMap raster streets and also exposes a manual provider toggle. The map displays provider/OSM attribution and can later switch to a self-hosted or commercial vector-tile source without changing gym data. The public OpenStreetMap tile service is suitable for this prototype, not an ad-scale production workload; review tile-provider terms and move to an appropriate hosted or self-hosted source as usage grows.
