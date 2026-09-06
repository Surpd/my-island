-- Keep normalized journal roster independent from Telegram/app identities.
-- Group mapping stores base class, subject subgroup, Classroom and exam track
-- as separate dimensions; group_id is only an optional internal app link.

create table if not exists public.journal_students (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.journal_sources(id) on delete cascade,
  student_key text not null,
  display_name text not null,
  group_marker text not null,
  identity_id uuid references public.identities(id),
  match_status text not null default 'unlinked' check (match_status in ('unlinked', 'linked', 'ambiguous')),
  match_method text,
  source_row integer,
  raw_source jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id, group_marker, student_key)
);

alter table public.journal_results
  add column if not exists journal_student_id uuid references public.journal_students(id) on delete set null;

alter table public.journal_group_mappings
  alter column group_id drop not null,
  add column if not exists base_class_name text,
  add column if not exists subject_subgroup text,
  add column if not exists classroom_course_id uuid references public.classroom_courses(id),
  add column if not exists exam_track text;

create index if not exists journal_students_source_marker_idx
  on public.journal_students(source_id, group_marker);
create index if not exists journal_students_identity_idx
  on public.journal_students(identity_id);
create index if not exists journal_results_student_idx
  on public.journal_results(journal_student_id);
create index if not exists journal_group_mappings_dimensions_idx
  on public.journal_group_mappings(source_id, subject_subgroup, base_class_name, exam_track);

alter table public.journal_students enable row level security;
revoke all on table public.journal_students from anon, authenticated;

-- Backfill the roster from the already normalized snapshot. This does not invent
-- memberships or Telegram accounts: identity_id remains nullable.
insert into public.journal_students (
  source_id, student_key, display_name, group_marker, identity_id,
  match_status, match_method, raw_source
)
select
  a.source_id,
  lower(regexp_replace(trim(r.student_name), '\s+', ' ', 'g')) || '|' || r.group_marker,
  min(r.student_name),
  r.group_marker,
  case when count(distinct r.identity_id) = 1 then (array_agg(r.identity_id) filter (where r.identity_id is not null))[1] else null end,
  case when count(distinct r.identity_id) = 1 then 'linked' else 'unlinked' end,
  case when count(distinct r.identity_id) = 1 then 'canonical_name' else null end,
  jsonb_build_object('backfilled_from', 'journal_results')
from public.journal_results r
join public.journal_assessments a on a.id = r.assessment_id
group by a.source_id, lower(regexp_replace(trim(r.student_name), '\s+', ' ', 'g')) || '|' || r.group_marker, r.group_marker
on conflict (source_id, group_marker, student_key) do update set
  display_name = excluded.display_name,
  identity_id = coalesce(public.journal_students.identity_id, excluded.identity_id),
  match_status = case when coalesce(public.journal_students.identity_id, excluded.identity_id) is null then 'unlinked' else 'linked' end,
  match_method = case when coalesce(public.journal_students.identity_id, excluded.identity_id) is null then null else coalesce(public.journal_students.match_method, excluded.match_method) end,
  updated_at = now();

update public.journal_results r
set journal_student_id = js.id
from public.journal_assessments a, public.journal_students js
where r.assessment_id = a.id
  and js.source_id = a.source_id
  and js.group_marker = r.group_marker
  and js.student_key = lower(regexp_replace(trim(r.student_name), '\s+', ' ', 'g')) || '|' || r.group_marker;
