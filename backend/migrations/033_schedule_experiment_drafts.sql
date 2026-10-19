begin;

create table if not exists public.schedule_experiment_drafts (
  id text primary key,
  name text not null,
  base_version_id text references public.canonical_schedule_versions(version_id) on delete set null,
  revision bigint not null default 1 check (revision > 0),
  payload jsonb not null default '{}'::jsonb,
  created_by uuid references public.users(id) on delete set null,
  updated_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists schedule_experiment_drafts_updated_idx
  on public.schedule_experiment_drafts(updated_at desc);

alter table public.schedule_experiment_drafts enable row level security;
revoke all on table public.schedule_experiment_drafts from anon, authenticated;

commit;
