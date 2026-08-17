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

The web app runs in demo mode when Supabase variables are absent. It includes a map-style explorer, filters, gym detail cards, save/compare state, and the Google OAuth callback path.

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
5. Copy `.env.example` to `.env` and fill in the publishable key and server-only values.

Never put `SUPABASE_SERVICE_ROLE_KEY`, database credentials, or Google client secrets in the frontend or GitHub Pages artifact.

## GitHub Pages

The project site target is:

```text
https://jkorrr.github.io/sf-gyms/
```

GitHub Pages only hosts the static frontend. The FastAPI service is independently deployable as a Docker container. The Pages workflow uses `NEXT_PUBLIC_BASE_PATH=/sf-gyms` and deploys only from `main`.

## Design and architecture notes

- Public reads are safe to cache; authenticated responses are not.
- Price history is append-only through `price_assertions`.
- Unique constraints, idempotency keys, version columns, and short transactions provide concurrency safety.
- RLS is enabled on all exposed Supabase tables.
- OAuth uses PKCE and a root callback compatible with static GitHub Pages hosting.
- API contracts are versioned so a future Expo/React Native app can use the same backend.
