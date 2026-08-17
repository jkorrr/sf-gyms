insert into public.amenities (slug, label) values
  ('free-weights', 'Free weights'),
  ('squat-racks', 'Squat racks'),
  ('showers', 'Showers'),
  ('classes', 'Classes'),
  ('sauna', 'Sauna'),
  ('basketball', 'Basketball'),
  ('cardio', 'Cardio')
on conflict (slug) do nothing;

insert into public.gyms (id, name, slug, gym_type, description, status)
values
  ('11111111-1111-4111-8111-111111111111', 'Mission Strength Co.', 'mission-strength-co', 'Strength gym', 'A welcoming strength-focused gym with serious equipment and a neighborhood feel.', 'published'),
  ('22222222-2222-4222-8222-222222222222', 'Hayes Valley Movement', 'hayes-valley-movement', 'Boutique fitness', 'Small-group classes and open-gym hours in a bright, calm studio.', 'published'),
  ('33333333-3333-4333-8333-333333333333', 'North Beach Community Gym', 'north-beach-community-gym', 'Community gym', 'An affordable local option with broad equipment and court access.', 'published')
on conflict (id) do nothing;

insert into public.gym_locations (id, gym_id, address, neighborhood, coordinates, is_open_24_7, hours, source_key)
values
  ('11111111-1111-4111-8111-111111111111', '11111111-1111-4111-8111-111111111111', '2200 Mission Street, San Francisco, CA', 'Mission', ST_SetSRID(ST_Point(-122.4181, 37.7614), 4326)::geography, true, '{"Monday-Friday":"Open 24 hours","Saturday-Sunday":"Open 24 hours"}', 'demo:mission-strength'),
  ('22222222-2222-4222-8222-222222222222', '22222222-2222-4222-8222-222222222222', '480 Hayes Street, San Francisco, CA', 'Hayes Valley', ST_SetSRID(ST_Point(-122.4248, 37.7765), 4326)::geography, false, '{"Monday-Friday":"6:00 AM–9:00 PM","Saturday-Sunday":"8:00 AM–6:00 PM"}', 'demo:hayes-movement'),
  ('33333333-3333-4333-8333-333333333333', '33333333-3333-4333-8333-333333333333', '1450 Stockton Street, San Francisco, CA', 'North Beach', ST_SetSRID(ST_Point(-122.4089, 37.7999), 4326)::geography, false, '{"Monday-Friday":"5:00 AM–10:00 PM","Saturday-Sunday":"7:00 AM–8:00 PM"}', 'demo:north-beach')
on conflict (id) do nothing;

insert into public.price_plans (gym_location_id, plan_type, billing_interval, amount, initiation_fee)
values
  ('11111111-1111-4111-8111-111111111111', 'monthly', 'month', 89, 0),
  ('11111111-1111-4111-8111-111111111111', 'day_pass', null, 20, 0),
  ('22222222-2222-4222-8222-222222222222', 'monthly', 'month', 139, 25),
  ('22222222-2222-4222-8222-222222222222', 'day_pass', null, 30, 0),
  ('33333333-3333-4333-8333-333333333333', 'monthly', 'month', 49, 0),
  ('33333333-3333-4333-8333-333333333333', 'day_pass', null, 12, 0)
on conflict (gym_location_id, plan_type, billing_interval) do nothing;

insert into public.price_assertions (price_plan_id, amount, source_type, freshness, status, verified_at, version)
select pp.id, pp.amount, 'curated',
       case when pp.gym_location_id = '33333333-3333-4333-8333-333333333333' then 'stale'::public.price_freshness else 'verified'::public.price_freshness end,
       'published', timezone('utc', now()), 1
from public.price_plans pp
where not exists (select 1 from public.price_assertions pa where pa.price_plan_id = pp.id);
