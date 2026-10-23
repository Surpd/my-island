-- Preserve journal history and stable links across normal spreadsheet edits.
begin;

alter table public.journal_sources
  add column if not exists sheet_id text,
  add column if not exists auto_sync_enabled boolean not null default false;

alter table public.journal_assessments
  add column if not exists value_signature text,
  add column if not exists group_marker text not null default 'all',
  add column if not exists active boolean not null default true,
  add column if not exists last_seen_at timestamptz;

alter table public.journal_students
  add column if not exists active boolean not null default true;

alter table public.journal_results
  add column if not exists is_current boolean not null default true,
  add column if not exists updated_at timestamptz not null default now();

create table if not exists public.journal_result_history (
  id uuid primary key default extensions.gen_random_uuid(),
  result_id uuid not null,
  numeric_score numeric,
  status text,
  source_coordinate text not null,
  raw_source jsonb,
  is_current boolean not null,
  recorded_at timestamptz not null default now()
);

create table if not exists public.journal_sync_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  spreadsheet_id text not null,
  grade integer not null check (grade between 5 and 11),
  subject text,
  trigger_kind text not null default 'manual' check (trigger_kind in ('manual', 'scheduled')),
  status text not null check (status in ('running', 'success', 'partial', 'error')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  error text,
  counts jsonb
);

create table if not exists public.journal_student_summaries (
  id uuid primary key default extensions.gen_random_uuid(),
  journal_student_id uuid not null references public.journal_students(id) on delete cascade,
  label text not null,
  numeric_value numeric,
  text_value text,
  source_coordinate text not null,
  raw_source jsonb,
  is_current boolean not null default true,
  updated_at timestamptz not null default now(),
  unique (journal_student_id, label)
);
create table if not exists public.journal_summary_history (
  id uuid primary key default extensions.gen_random_uuid(),
  summary_id uuid not null,
  numeric_value numeric,
  text_value text,
  source_coordinate text not null,
  raw_source jsonb,
  is_current boolean not null,
  recorded_at timestamptz not null default now()
);

-- Keep this migration non-destructive. If duplicate pairs appeared after preflight,
-- abort and resolve them with a separately reviewed, fully reversible data change.
do $$
begin
  if exists (
    select 1
    from public.journal_results
    where journal_student_id is not null
    group by assessment_id, journal_student_id
    having count(*) > 1
  ) then
    raise exception 'journal_results contains duplicate student/assessment pairs; refusing to apply migration 043';
  end if;
end;
$$;

create unique index if not exists journal_results_student_assessment_uidx
  on public.journal_results(assessment_id, journal_student_id)
  where journal_student_id is not null;
create index if not exists journal_results_current_idx
  on public.journal_results(assessment_id, is_current);
create index if not exists journal_assessments_current_idx
  on public.journal_assessments(source_id, active);
create index if not exists journal_sync_runs_started_idx
  on public.journal_sync_runs(spreadsheet_id, started_at desc);
create unique index if not exists journal_sync_runs_one_active_source_idx
  on public.journal_sync_runs(spreadsheet_id) where status = 'running';

alter table public.journal_result_history enable row level security;
alter table public.journal_student_summaries enable row level security;
alter table public.journal_summary_history enable row level security;
alter table public.journal_sync_runs enable row level security;
revoke all on table public.journal_result_history, public.journal_student_summaries, public.journal_summary_history, public.journal_sync_runs from anon, authenticated;

commit;
