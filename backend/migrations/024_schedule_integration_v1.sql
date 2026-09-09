-- Schedule Integration v1: immutable source lifecycle and canonical-independent lessons.
begin;

create table if not exists public.schedule_lessons (
  id uuid primary key default extensions.gen_random_uuid(),
  source_snapshot_id uuid not null references public.school_source_snapshots(id) on delete cascade,
  source_record_id uuid references public.school_source_records(id) on delete set null,
  record_key text not null,
  lesson_date date,
  start_time time,
  end_time time,
  subject text not null default '',
  teacher text not null default '',
  audience text not null default '',
  lesson_kind text not null default 'lesson',
  resolution_status text not null default 'UNRESOLVED',
  resolved_identity_ids jsonb not null default '[]'::jsonb,
  resolved_group_ids jsonb not null default '[]'::jsonb,
  diagnostics jsonb not null default '{}'::jsonb,
  raw_payload jsonb not null default '{}'::jsonb,
  unique(source_snapshot_id, record_key)
);
alter table public.schedule_lessons add column if not exists sheet_id text;
alter table public.schedule_lessons add column if not exists tab_title text;
alter table public.schedule_lessons add column if not exists source_record_id uuid references public.school_source_records(id) on delete set null;
alter table public.schedule_lessons add column if not exists version_kind text not null default 'weekly';
alter table public.schedule_lessons add column if not exists week_start date;
alter table public.schedule_lessons add column if not exists week_end date;
alter table public.schedule_lessons add column if not exists weekday integer;
alter table public.schedule_lessons add column if not exists teacher_hint text not null default '';
alter table public.schedule_lessons add column if not exists room text not null default '';
alter table public.schedule_lessons add column if not exists activity_type text not null default 'lesson';
alter table public.schedule_lessons add column if not exists modifiers jsonb not null default '{}'::jsonb;
alter table public.schedule_lessons add column if not exists confidence numeric;
alter table public.schedule_lessons add column if not exists evidence jsonb not null default '{}'::jsonb;
alter table public.schedule_lessons add column if not exists source_cell text;
alter table public.schedule_lessons add column if not exists source_color text;
alter table public.schedule_lessons add column if not exists merge_data jsonb;
alter table public.schedule_lessons add column if not exists baseline_record_key text;
alter table public.schedule_lessons add column if not exists baseline_data jsonb;
alter table public.schedule_lessons add column if not exists diff_status text;
alter table public.schedule_lessons add column if not exists issue_reason text;
create table if not exists public.schedule_source_tabs (
  id uuid primary key default extensions.gen_random_uuid(), source_id uuid not null references public.school_sources(id) on delete cascade,
  sheet_id text not null, title text not null, classification text not null, week_start date, week_end date, updated_at timestamptz not null default now(),
  unique(source_id, sheet_id)
);
alter table public.schedule_source_tabs enable row level security;
revoke all on table public.schedule_source_tabs from anon, authenticated;
create index if not exists schedule_lessons_snapshot_idx on public.schedule_lessons(source_snapshot_id, lesson_date, start_time);
create index if not exists schedule_lessons_status_idx on public.schedule_lessons(resolution_status);
create index if not exists schedule_lessons_week_status_idx on public.schedule_lessons(source_snapshot_id, version_kind, week_start, resolution_status);
create index if not exists schedule_lessons_diff_idx on public.schedule_lessons(source_snapshot_id, diff_status);
create index if not exists schedule_source_tabs_source_week_idx on public.schedule_source_tabs(source_id, classification, week_start);
alter table public.schedule_lessons enable row level security;
revoke all on table public.schedule_lessons from anon, authenticated;

commit;
