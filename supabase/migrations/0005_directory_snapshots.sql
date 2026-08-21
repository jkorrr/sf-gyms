-- Lossless publication snapshot for the reviewed SF gym directory.
--
-- The normalized v1 tables remain available to the API, while this table stores
-- the complete reviewed fixture (plans, fees, estimates, blockers, provenance,
-- and selection decisions) without flattening or dropping fields.

create table if not exists public.gym_directory_sync_runs (
  id uuid primary key default gen_random_uuid(),
  source_hash text not null check (source_hash ~ '^[0-9a-f]{64}$'),
  source_record_count integer not null check (source_record_count >= 0),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  status text not null default 'running' check (status in ('running', 'complete', 'failed')),
  started_at timestamptz not null default timezone('utc', now()),
  completed_at timestamptz,
  check ((status = 'complete' and completed_at is not null) or status <> 'complete')
);

create table if not exists public.gym_directory_records (
  canonical_location_id text primary key check (length(trim(canonical_location_id)) between 1 and 300),
  source_id text not null unique check (length(trim(source_id)) between 1 and 300),
  ordinal integer not null check (ordinal >= 0),
  name text not null check (length(trim(name)) between 1 and 300),
  operator_id text,
  publication_status text not null
    check (publication_status in ('publish', 'suppress-alias', 'review-hold')),
  pricing_status text not null
    check (pricing_status in (
      'verified', 'estimated', 'free', 'pay-per-visit',
      'not-applicable', 'gated', 'unresolved'
    )),
  record_hash text not null check (record_hash ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  sync_run_id uuid not null references public.gym_directory_sync_runs(id) on delete restrict,
  synced_at timestamptz not null default timezone('utc', now())
);

create index if not exists gym_directory_records_publication_idx
  on public.gym_directory_records (publication_status, ordinal);
create index if not exists gym_directory_records_pricing_idx
  on public.gym_directory_records (pricing_status);
create index if not exists gym_directory_sync_runs_completed_idx
  on public.gym_directory_sync_runs (completed_at desc)
  where status = 'complete';

alter table public.gym_directory_sync_runs enable row level security;
alter table public.gym_directory_records enable row level security;

revoke all on table public.gym_directory_sync_runs from anon, authenticated;
revoke insert, update, delete, truncate, references, trigger
  on table public.gym_directory_records from anon, authenticated;
grant select on table public.gym_directory_records to anon, authenticated;

-- The public directory can read only canonical records explicitly approved for
-- publication. Sync history remains private because it has no public policy.
drop policy if exists gym_directory_records_public_read on public.gym_directory_records;
create policy gym_directory_records_public_read
  on public.gym_directory_records
  for select
  using (publication_status = 'publish');

comment on table public.gym_directory_records is
  'Current lossless reviewed directory snapshot. Written only by trusted database sync tooling.';
comment on table public.gym_directory_sync_runs is
  'Private audit metadata for atomic fixture-to-database synchronization.';
