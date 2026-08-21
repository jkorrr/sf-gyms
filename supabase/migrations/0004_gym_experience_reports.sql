-- Moderated, structured gym experiences. These tables intentionally do not
-- extend the original generic reviews scaffold: an experience is tied to a
-- visit context, revisions are immutable, and only an approved revision can
-- become public.

alter table public.idempotency_keys
  add column if not exists principal_id uuid references auth.users(id) on delete cascade,
  add column if not exists request_hash text;

create index if not exists idempotency_keys_principal_operation_idx
  on public.idempotency_keys (principal_id, operation, created_at desc);

create table public.gym_experience_reports (
  id uuid primary key default gen_random_uuid(),
  gym_location_id uuid not null references public.gym_locations(id) on delete cascade,
  author_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'pending'
    check (status in ('draft', 'pending', 'published', 'rejected', 'withdrawn', 'hidden', 'removed')),
  latest_revision_id uuid,
  current_published_revision_id uuid,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  version integer not null default 1 check (version > 0),
  check (status <> 'published' or current_published_revision_id is not null)
);

create table public.gym_experience_revisions (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.gym_experience_reports(id) on delete cascade,
  revision_number integer not null check (revision_number > 0),
  visit_date date not null,
  time_bucket text check (time_bucket is null or time_bucket in (
    'early_morning', 'morning', 'midday', 'evening', 'late_night'
  )),
  relationship text not null check (relationship in (
    'member', 'former_member', 'trial', 'day_pass', 'guest', 'other'
  )),
  equipment_availability text check (equipment_availability is null or equipment_availability in (
    'available', 'short_wait', 'long_wait', 'not_available'
  )),
  crowding text check (crowding is null or crowding in (
    'quiet', 'moderate', 'busy', 'packed'
  )),
  cleanliness text check (cleanliness is null or cleanliness in (
    'needs_attention', 'acceptable', 'clean', 'exceptionally_clean'
  )),
  value_assessment text check (value_assessment is null or value_assessment in (
    'poor', 'fair', 'good', 'excellent'
  )),
  billing_transparency text check (billing_transparency is null or billing_transparency in (
    'unclear', 'partly_clear', 'clear'
  )),
  listing_accuracy text check (listing_accuracy is null or listing_accuracy in (
    'inaccurate', 'partly_accurate', 'accurate'
  )),
  body text check (body is null or length(body) between 1 and 2000),
  status text not null default 'pending'
    check (status in ('draft', 'pending', 'published', 'rejected', 'withdrawn', 'hidden', 'removed')),
  submitted_at timestamptz,
  published_at timestamptz,
  moderated_by uuid references public.profiles(id) on delete set null,
  moderated_at timestamptz,
  moderation_reason text check (moderation_reason is null or length(moderation_reason) <= 1000),
  created_at timestamptz not null default timezone('utc', now()),
  unique (report_id, revision_number),
  unique (report_id, id),
  check (
    body is not null
    or equipment_availability is not null
    or crowding is not null
    or cleanliness is not null
    or value_assessment is not null
    or billing_transparency is not null
    or listing_accuracy is not null
  ),
  check (status <> 'published' or published_at is not null)
);

alter table public.gym_experience_reports
  add constraint gym_experience_reports_latest_revision_fk
  foreign key (id, latest_revision_id)
  references public.gym_experience_revisions (report_id, id)
  deferrable initially deferred;

alter table public.gym_experience_reports
  add constraint gym_experience_reports_published_revision_fk
  foreign key (id, current_published_revision_id)
  references public.gym_experience_revisions (report_id, id)
  deferrable initially deferred;

create table public.gym_experience_flags (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.gym_experience_reports(id) on delete cascade,
  reporter_id uuid not null references auth.users(id) on delete cascade,
  reason text not null check (reason in (
    'spam', 'conflict_of_interest', 'privacy', 'harassment', 'not_firsthand', 'other'
  )),
  note text check (note is null or length(note) between 1 and 1000),
  status text not null default 'open' check (status in ('open', 'reviewed', 'dismissed', 'actioned')),
  created_at timestamptz not null default timezone('utc', now()),
  reviewed_at timestamptz,
  reviewed_by uuid references public.profiles(id) on delete set null
);

create index gym_experience_reports_published_location_idx
  on public.gym_experience_reports (gym_location_id, updated_at desc, id desc)
  where status = 'published';

create index gym_experience_reports_author_idx
  on public.gym_experience_reports (author_id, created_at desc, id desc);

create index gym_experience_revisions_report_idx
  on public.gym_experience_revisions (report_id, revision_number desc);

create index gym_experience_revisions_moderation_queue_idx
  on public.gym_experience_revisions (submitted_at, id)
  where status = 'pending';

create unique index gym_experience_flags_one_open_per_reporter_idx
  on public.gym_experience_flags (report_id, reporter_id)
  where status = 'open';

create trigger gym_experience_reports_updated_at
  before update on public.gym_experience_reports
  for each row execute function public.set_updated_at();

alter table public.gym_experience_reports enable row level security;
alter table public.gym_experience_revisions enable row level security;
alter table public.gym_experience_flags enable row level security;

create policy gym_experience_reports_public_read
  on public.gym_experience_reports for select
  to anon, authenticated
  using (status = 'published' and current_published_revision_id is not null);

create policy gym_experience_reports_self_read
  on public.gym_experience_reports for select
  to authenticated
  using ((select auth.uid()) = author_id);

create policy gym_experience_revisions_public_read
  on public.gym_experience_revisions for select
  to anon, authenticated
  using (exists (
    select 1
    from public.gym_experience_reports report
    where report.id = gym_experience_revisions.report_id
      and report.status = 'published'
      and report.current_published_revision_id = gym_experience_revisions.id
      and gym_experience_revisions.status = 'published'
      and gym_experience_revisions.published_at is not null
  ));

create policy gym_experience_revisions_self_read
  on public.gym_experience_revisions for select
  to authenticated
  using (exists (
    select 1
    from public.gym_experience_reports report
    where report.id = gym_experience_revisions.report_id
      and report.author_id = (select auth.uid())
  ));

create policy gym_experience_flags_self_read
  on public.gym_experience_flags for select
  to authenticated
  using ((select auth.uid()) = reporter_id);

-- Browser roles can read approved content and their own workflow rows, but all
-- writes go through the authenticated FastAPI command boundary. Explicit grants
-- are required for Supabase projects that disable automatic Data API exposure.
revoke all on public.gym_experience_reports from anon, authenticated;
revoke all on public.gym_experience_revisions from anon, authenticated;
revoke all on public.gym_experience_flags from anon, authenticated;

grant select on public.gym_experience_reports to anon, authenticated;
grant select on public.gym_experience_revisions to anon, authenticated;
grant select on public.gym_experience_flags to authenticated;

grant select, insert, update on public.gym_experience_reports to service_role;
grant select, insert on public.gym_experience_revisions to service_role;
grant update (status, published_at, moderated_by, moderated_at, moderation_reason)
  on public.gym_experience_revisions to service_role;
grant select, insert, update on public.gym_experience_flags to service_role;
grant select, insert, update on public.idempotency_keys to service_role;

comment on table public.gym_experience_reports is
  'Moderated gym-experience threads. Public content is selected through current_published_revision_id.';

comment on table public.gym_experience_revisions is
  'Immutable structured snapshots; edits create a new pending revision instead of mutating published content.';

comment on column public.gym_experience_revisions.relationship is
  'Self-reported relationship to the gym. It is not proof of attendance or membership.';
