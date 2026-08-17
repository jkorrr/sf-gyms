-- SF Gyms initial schema.
-- All mutable commands should use short transactions and the functions below.

create extension if not exists pgcrypto;
create extension if not exists postgis;

create type public.publish_status as enum ('draft', 'published', 'archived');
create type public.price_freshness as enum ('verified', 'gym_reported', 'user_reported', 'stale', 'unknown');
create type public.claim_status as enum ('pending', 'approved', 'rejected', 'revoked');

create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
as $$
begin
  new.updated_at = timezone('utc', now());
  new.version = old.version + 1;
  return new;
end;
$$;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url text,
  role text not null default 'user' check (role in ('user', 'owner', 'moderator', 'admin')),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0)
);

create table public.gyms (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 200),
  slug text not null unique check (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
  gym_type text not null default 'Gym',
  description text,
  website_url text,
  phone text,
  status public.publish_status not null default 'draft',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0)
);

create table public.gym_locations (
  id uuid primary key default gen_random_uuid(),
  gym_id uuid not null references public.gyms(id) on delete cascade,
  address text not null,
  neighborhood text,
  coordinates geography(Point, 4326) not null,
  is_open_24_7 boolean not null default false,
  hours jsonb not null default '{}'::jsonb,
  source_key text unique,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0),
  unique (gym_id, address)
);

create table public.amenities (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  label text not null unique
);

create table public.gym_location_amenities (
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  amenity_id uuid not null references public.amenities(id) on delete cascade,
  primary key (gym_location_id, amenity_id)
);

create table public.price_plans (
  id uuid primary key default gen_random_uuid(),
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  plan_type text not null check (plan_type in ('monthly', 'annual', 'day_pass', 'trial', 'class_pack')),
  billing_interval text,
  amount numeric(10, 2) check (amount is null or amount >= 0),
  initiation_fee numeric(10, 2) not null default 0 check (initiation_fee >= 0),
  contract_term text,
  cancellation_policy text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0),
  unique (gym_location_id, plan_type, billing_interval)
);

create table public.price_assertions (
  id uuid primary key default gen_random_uuid(),
  price_plan_id uuid not null references public.price_plans(id) on delete cascade,
  amount numeric(10, 2) check (amount is null or amount >= 0),
  initiation_fee numeric(10, 2) not null default 0 check (initiation_fee >= 0),
  source_url text,
  source_type text not null check (source_type in ('curated', 'gym_claimed', 'user_reported', 'imported')),
  freshness public.price_freshness not null default 'unknown',
  status public.publish_status not null default 'draft',
  verified_at timestamptz,
  effective_from timestamptz,
  version integer not null check (version > 0),
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  unique (price_plan_id, version)
);

create table public.saved_gyms (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  created_at timestamptz not null default timezone('utc', now()),
  unique (user_id, gym_location_id)
);

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  intent text not null check (intent in ('tour', 'trial', 'call', 'website')),
  email text,
  note text check (note is null or length(note) <= 1000),
  idempotency_scope text not null,
  created_at timestamptz not null default timezone('utc', now()),
  unique (idempotency_scope)
);

create table public.claims (
  id uuid primary key default gen_random_uuid(),
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  claimant_id uuid not null references auth.users(id) on delete cascade,
  status public.claim_status not null default 'pending',
  evidence_url text,
  reviewed_by uuid references public.profiles(id) on delete set null,
  reviewed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0)
);

create unique index claims_one_approved_location
  on public.claims (gym_location_id)
  where status = 'approved';

create table public.reviews (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  overall_rating integer not null check (overall_rating between 1 and 5),
  cleanliness integer check (cleanliness between 1 and 5),
  crowding integer check (crowding between 1 and 5),
  equipment integer check (equipment between 1 and 5),
  value integer check (value between 1 and 5),
  body text check (body is null or length(body) <= 5000),
  visit_date date,
  status public.publish_status not null default 'draft',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0),
  unique (user_id, gym_location_id)
);

create table public.moderation_actions (
  id uuid primary key default gen_random_uuid(),
  moderator_id uuid not null references public.profiles(id) on delete restrict,
  target_type text not null,
  target_id uuid not null,
  action text not null,
  reason text,
  created_at timestamptz not null default timezone('utc', now())
);

create table public.audit_events (
  id bigint generated always as identity primary key,
  actor_id uuid references auth.users(id) on delete set null,
  action text not null,
  entity_type text not null,
  entity_id uuid,
  request_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create table public.idempotency_keys (
  id uuid primary key default gen_random_uuid(),
  scope_key text not null unique,
  operation text not null,
  response_status integer,
  response_body jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  expires_at timestamptz not null default timezone('utc', now()) + interval '24 hours'
);

create index gym_locations_coordinates_gist on public.gym_locations using gist (coordinates);
create index gym_locations_neighborhood_idx on public.gym_locations (neighborhood);
create index price_assertions_freshness_idx on public.price_assertions (freshness, verified_at desc);
create index reviews_published_idx on public.reviews (gym_location_id, created_at desc) where status = 'published';
create index audit_events_entity_idx on public.audit_events (entity_type, entity_id, created_at desc);

create trigger profiles_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();
create trigger gyms_updated_at before update on public.gyms
  for each row execute function public.set_updated_at();
create trigger gym_locations_updated_at before update on public.gym_locations
  for each row execute function public.set_updated_at();
create trigger price_plans_updated_at before update on public.price_plans
  for each row execute function public.set_updated_at();
create trigger claims_updated_at before update on public.claims
  for each row execute function public.set_updated_at();
create trigger reviews_updated_at before update on public.reviews
  for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name, avatar_url)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', new.email), new.raw_user_meta_data ->> 'avatar_url')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

create or replace function public.prevent_profile_role_change()
returns trigger
language plpgsql
security invoker
as $$
begin
  if coalesce(auth.role(), '') <> 'service_role'
     and current_user not in ('supabase_admin', 'postgres')
     and new.role <> old.role then
    raise exception 'profile roles can only be changed by trusted administration';
  end if;
  return new;
end;
$$;

create trigger profiles_role_guard before update on public.profiles
  for each row execute function public.prevent_profile_role_change();

create or replace function public.append_price_assertion(
  p_price_plan_id uuid,
  p_amount numeric,
  p_initiation_fee numeric,
  p_source_url text,
  p_source_type text,
  p_freshness public.price_freshness,
  p_status public.publish_status,
  p_verified_at timestamptz,
  p_effective_from timestamptz,
  p_created_by uuid
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  next_version integer;
  new_id uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_price_plan_id::text, 0));
  select coalesce(max(version), 0) + 1 into next_version
  from public.price_assertions
  where price_plan_id = p_price_plan_id;

  insert into public.price_assertions (
    price_plan_id, amount, initiation_fee, source_url, source_type,
    freshness, status, verified_at, effective_from, version, created_by
  ) values (
    p_price_plan_id, p_amount, coalesce(p_initiation_fee, 0), p_source_url, p_source_type,
    p_freshness, p_status, p_verified_at, p_effective_from, next_version, p_created_by
  ) returning id into new_id;
  return new_id;
end;
$$;

create or replace function public.approve_claim(p_claim_id uuid, p_reviewer uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  location_id uuid;
begin
  select gym_location_id into location_id from public.claims where id = p_claim_id for update;
  if location_id is null then raise exception 'claim not found'; end if;
  perform pg_advisory_xact_lock(hashtextextended(location_id::text, 0));
  update public.claims
    set status = 'revoked', reviewed_by = p_reviewer, reviewed_at = timezone('utc', now())
    where gym_location_id = location_id and status = 'approved' and id <> p_claim_id;
  update public.claims
    set status = 'approved', reviewed_by = p_reviewer, reviewed_at = timezone('utc', now())
    where id = p_claim_id;
end;
$$;

revoke execute on function public.handle_new_user() from anon, authenticated;
revoke execute on function public.append_price_assertion(
  uuid, numeric, numeric, text, text, public.price_freshness, public.publish_status,
  timestamptz, timestamptz, uuid
) from anon, authenticated;
revoke execute on function public.approve_claim(uuid, uuid) from anon, authenticated;

alter table public.profiles enable row level security;
alter table public.gyms enable row level security;
alter table public.gym_locations enable row level security;
alter table public.amenities enable row level security;
alter table public.gym_location_amenities enable row level security;
alter table public.price_plans enable row level security;
alter table public.price_assertions enable row level security;
alter table public.saved_gyms enable row level security;
alter table public.leads enable row level security;
alter table public.claims enable row level security;
alter table public.reviews enable row level security;
alter table public.moderation_actions enable row level security;
alter table public.audit_events enable row level security;
alter table public.idempotency_keys enable row level security;

create policy gyms_public_read on public.gyms for select using (status = 'published');
create policy gym_locations_public_read on public.gym_locations for select using (
  exists (select 1 from public.gyms g where g.id = gym_id and g.status = 'published')
);
create policy amenities_public_read on public.amenities for select using (true);
create policy location_amenities_public_read on public.gym_location_amenities for select using (true);
create policy price_plans_public_read on public.price_plans for select using (
  exists (select 1 from public.gym_locations gl join public.gyms g on g.id = gl.gym_id
          where gl.id = gym_location_id and g.status = 'published')
);
create policy price_assertions_public_read on public.price_assertions for select using (
  status = 'published' and exists (
    select 1
    from public.price_plans pp
    join public.gym_locations gl on gl.id = pp.gym_location_id
    join public.gyms g on g.id = gl.gym_id
    where pp.id = price_plan_id and g.status = 'published'
  )
);

create policy profiles_self_read on public.profiles for select using (auth.uid() = id);
create policy profiles_self_update on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);
create policy saved_gyms_self_read on public.saved_gyms for select using (auth.uid() = user_id);
create policy saved_gyms_self_insert on public.saved_gyms for insert with check (auth.uid() = user_id);
create policy saved_gyms_self_delete on public.saved_gyms for delete using (auth.uid() = user_id);
create policy leads_self_insert on public.leads for insert with check (auth.uid() = user_id);
create policy leads_self_read on public.leads for select using (auth.uid() = user_id);
create policy claims_self_read on public.claims for select using (auth.uid() = claimant_id);
create policy claims_self_insert on public.claims for insert
  with check (auth.uid() = claimant_id and status = 'pending');
create policy reviews_public_read on public.reviews for select using (status = 'published');
create policy reviews_self_insert on public.reviews for insert
  with check (auth.uid() = user_id and status = 'draft');
create policy reviews_self_update on public.reviews for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

revoke all on public.audit_events from anon, authenticated;
revoke all on public.moderation_actions from anon, authenticated;
revoke all on public.idempotency_keys from anon, authenticated;
