-- Google sync normalization for Classroom metadata/materials/submissions.
-- Keep Data API access deny-by-default; the trusted backend uses the database URL.

create unique index if not exists groups_name_type_unique_idx
  on public.groups(name, group_type);

alter table public.classroom_courses
  add column if not exists section text,
  add column if not exists description text,
  add column if not exists room text,
  add column if not exists update_time timestamptz,
  add column if not exists raw_source jsonb;

alter table public.classroom_coursework
  add column if not exists state text,
  add column if not exists work_type text,
  add column if not exists max_points numeric,
  add column if not exists update_time timestamptz,
  add column if not exists raw_source jsonb;

alter table public.classroom_student_submissions
  add column if not exists external_student_id text,
  add column if not exists draft_grade numeric,
  add column if not exists late boolean,
  add column if not exists raw_source jsonb;

create table if not exists public.classroom_coursework_materials (
  id uuid primary key default extensions.gen_random_uuid(),
  course_id uuid references public.classroom_courses(id),
  coursework_id uuid references public.classroom_coursework(id),
  external_material_key text not null unique,
  source_type text not null check (source_type in ('embedded', 'dedicated')),
  title text not null default '',
  material_type text not null default '',
  url text,
  drive_file_id text,
  raw_source jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((course_id is not null) <> (coursework_id is not null))
);

create index if not exists classroom_coursework_course_idx
  on public.classroom_coursework(course_id);
create index if not exists classroom_materials_coursework_idx
  on public.classroom_coursework_materials(coursework_id);
create index if not exists classroom_materials_course_idx
  on public.classroom_coursework_materials(course_id);
create index if not exists classroom_submissions_coursework_idx
  on public.classroom_student_submissions(coursework_id);
create index if not exists classroom_submissions_identity_idx
  on public.classroom_student_submissions(identity_id);

alter table public.classroom_coursework_materials enable row level security;
revoke all on table public.classroom_coursework_materials from anon, authenticated;
