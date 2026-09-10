-- Derived, rebuildable lesson audiences and per-student slot allocations.
begin;

create table if not exists public.schedule_lesson_audiences (
  id uuid primary key default extensions.gen_random_uuid(),
  source_snapshot_id uuid not null references public.school_source_snapshots(id) on delete cascade,
  lesson_id uuid not null references public.schedule_lessons(id) on delete cascade,
  week_start date,
  lesson_date date not null,
  start_time time not null,
  grade_scope text not null,
  audience_kind text not null,
  resolved_group_ids jsonb not null default '[]'::jsonb,
  status text not null check (status in ('resolved','ambiguous')),
  rule_reason text not null,
  provenance jsonb not null default '{}'::jsonb,
  unique(source_snapshot_id, lesson_id)
);

create table if not exists public.schedule_student_allocations (
  id uuid primary key default extensions.gen_random_uuid(),
  source_snapshot_id uuid not null references public.school_source_snapshots(id) on delete cascade,
  week_start date,
  lesson_date date not null,
  start_time time not null,
  end_time time,
  grade_scope text not null,
  student_identity_id uuid not null references public.identities(id),
  lesson_id uuid references public.schedule_lessons(id) on delete cascade,
  allocation_kind text not null check (allocation_kind in ('lesson','lunch','window','end_of_day','unassigned','conflict')),
  status text not null check (status in ('assigned','unassigned','conflict')),
  reason text not null,
  provenance jsonb not null default '{}'::jsonb,
  unique(source_snapshot_id, lesson_date, start_time, grade_scope, student_identity_id)
);

create index if not exists schedule_lesson_audiences_slot_idx on public.schedule_lesson_audiences(source_snapshot_id, week_start, lesson_date, start_time, grade_scope);
create index if not exists schedule_lesson_audiences_lesson_idx on public.schedule_lesson_audiences(lesson_id);
create index if not exists schedule_student_allocations_slot_idx on public.schedule_student_allocations(source_snapshot_id, week_start, lesson_date, start_time, grade_scope, status);
create index if not exists schedule_student_allocations_student_idx on public.schedule_student_allocations(student_identity_id, lesson_date, start_time);
create index if not exists schedule_student_allocations_lesson_idx on public.schedule_student_allocations(lesson_id);

alter table public.schedule_lesson_audiences enable row level security;
alter table public.schedule_student_allocations enable row level security;
revoke all on table public.schedule_lesson_audiences from anon, authenticated;
revoke all on table public.schedule_student_allocations from anon, authenticated;

commit;
