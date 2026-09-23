begin;

create table if not exists public.schedule_editor_drafts (
  draft_key text primary key,
  scope_kind text not null check (scope_kind in ('template','week')),
  scope_key text not null,
  base_version_id text references public.canonical_schedule_versions(version_id) on delete restrict,
  base_effective_week_id text references public.canonical_effective_weeks(effective_week_id) on delete restrict,
  revision bigint not null default 1 check (revision > 0),
  payload jsonb not null default '{"changes":[]}'::jsonb,
  source_context jsonb not null default '{}'::jsonb,
  created_by uuid references public.users(id) on delete set null,
  updated_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (scope_kind, scope_key)
);

create index if not exists schedule_editor_drafts_updated_idx
  on public.schedule_editor_drafts(updated_at desc);

alter table public.schedule_editor_drafts enable row level security;
revoke all on table public.schedule_editor_drafts from anon, authenticated;

commit;
