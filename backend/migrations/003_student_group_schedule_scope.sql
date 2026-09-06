-- Explicit group-to-schedule audience mapping for backend-scoped student reads.
-- Keep custom Telegram authorization server-side and Data API deny-by-default.

create table if not exists public.group_schedule_audiences (
  id uuid primary key default extensions.gen_random_uuid(),
  group_id uuid not null references public.groups(id),
  audience text not null,
  subject text not null default '',
  subject_subgroup text not null default '',
  exam_track text not null default '',
  created_at timestamptz not null default now(),
  unique (group_id, audience, subject, subject_subgroup, exam_track)
);

alter table public.schedule_entries
  add column if not exists subject_subgroup text,
  add column if not exists exam_track text,
  add column if not exists lesson_type text not null default 'lesson',
  add column if not exists delivery_mode text,
  add column if not exists parse_status text not null default 'parsed',
  add column if not exists parse_diagnostics jsonb,
  add column if not exists source_tab text,
  add column if not exists source_coordinate text,
  add column if not exists header_source_column text,
  add column if not exists week_start date;

create index if not exists group_schedule_audiences_group_idx
  on public.group_schedule_audiences(group_id);
create index if not exists memberships_group_active_idx
  on public.memberships(group_id, active);
create index if not exists schedule_entries_audience_date_time_idx
  on public.schedule_entries(audience, lesson_date, start_time);
create index if not exists schedule_entries_audience_scope_idx
  on public.schedule_entries(audience, subject_subgroup, exam_track, lesson_date, start_time);
create index if not exists group_schedule_audiences_scope_idx
  on public.group_schedule_audiences(audience, subject, subject_subgroup, exam_track);
create index if not exists schedule_entries_week_idx
  on public.schedule_entries(week_start, lesson_date, start_time);

alter table public.group_schedule_audiences enable row level security;
revoke all on table public.group_schedule_audiences from anon, authenticated;
