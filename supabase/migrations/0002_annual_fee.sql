-- Keep mandatory annual dues separate from the recurring monthly amount.
-- NULL means the public source did not publish an annual fee; zero means the
-- source explicitly states that no annual fee is charged.
alter table public.price_plans
  add column if not exists annual_fee numeric(10, 2)
  check (annual_fee is null or annual_fee >= 0);

alter table public.price_assertions
  add column if not exists annual_fee numeric(10, 2)
  check (annual_fee is null or annual_fee >= 0);

comment on column public.price_plans.annual_fee is
  'Mandatory annual fee associated with this plan; NULL means not publicly listed.';

comment on column public.price_assertions.annual_fee is
  'Source-backed annual fee observation; NULL means not publicly listed.';

create index if not exists price_assertions_annual_fee_idx
  on public.price_assertions (annual_fee, verified_at desc);
