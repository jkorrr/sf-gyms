-- Stable discovery taxonomy. Keep gym_type as the source/operator description.
alter table public.gyms
  add column venue_type text not null default 'traditional_gym'
  check (venue_type in (
    'traditional_gym',
    'boutique_fitness',
    'yoga_studio',
    'pilates_barre',
    'martial_arts_boxing',
    'climbing_gym',
    'gymnastics',
    'personal_training',
    'recreation_sports',
    'outdoor_fitness',
    'dance_movement'
  ));

create index gyms_published_venue_type_idx
  on public.gyms (venue_type, name)
  where status = 'published';
