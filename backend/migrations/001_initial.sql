-- Supabase/Postgres schema for the Telegram Mini App + trusted backend auth model.
create type app_role as enum ('student', 'teacher', 'admin');
create type claim_status as enum ('pending', 'approved', 'rejected', 'identity_conflict');
create type membership_source as enum ('official_import', 'admin_override', 'manual_elective');

create table public.users (
  id uuid primary key default extensions.gen_random_uuid(),
  telegram_user_id bigint not null unique,
  role app_role not null,
  identity_id uuid,
  created_at timestamptz not null default now()
);
create table public.identities (
  id uuid primary key default extensions.gen_random_uuid(),
  kind text not null check (kind in ('student', 'teacher')),
  display_name text not null,
  class_name text,
  status text not null default 'active'
);
alter table public.users add constraint users_identity_fk foreign key (identity_id) references public.identities(id);
create table public.identity_claims (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.users(id),
  identity_id uuid not null references public.identities(id),
  requested_role text not null check (requested_role in ('student', 'teacher')),
  status claim_status not null default 'pending',
  reviewed_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  reviewed_at timestamptz
);
create table public.groups (
  id uuid primary key default extensions.gen_random_uuid(),
  name text not null,
  group_type text not null
);
create table public.memberships (
  id uuid primary key default extensions.gen_random_uuid(),
  group_id uuid not null references public.groups(id),
  identity_id uuid not null references public.identities(id),
  member_role app_role not null,
  source membership_source not null,
  active boolean not null default true,
  valid_from date,
  valid_until date,
  unique (group_id, identity_id, source)
);
create table public.schedule_entries (
  id uuid primary key default extensions.gen_random_uuid(),
  entry_key text not null unique,
  lesson_date date not null,
  start_time time not null,
  end_time time,
  subject text not null,
  teacher text,
  room text,
  audience text,
  source_hash text not null,
  raw_source jsonb
);
create table public.announcements (
  id uuid primary key default extensions.gen_random_uuid(), title text not null, body text not null,
  audience text, expires_at timestamptz, active boolean not null default true
);
create table public.events (
  id uuid primary key default extensions.gen_random_uuid(), title text not null, starts_at timestamptz not null,
  ends_at timestamptz, location text, audience text, active boolean not null default true
);
create table public.schedule_syncs (
  id uuid primary key default extensions.gen_random_uuid(), status text not null,
  source text not null, source_hash text, error text, validation_problems jsonb,
  created_at timestamptz not null default now()
);
create table public.audit_log (
  id uuid primary key default extensions.gen_random_uuid(), actor_user_id uuid references public.users(id),
  action text not null, entity_type text not null, entity_id uuid, details jsonb,
  created_at timestamptz not null default now()
);
create table public.classroom_courses (
  id uuid primary key default extensions.gen_random_uuid(), group_id uuid not null references public.groups(id),
  external_course_id text not null unique, title text not null, teacher_account text,
  created_at timestamptz not null default now()
);
create table public.classroom_coursework (
  id uuid primary key default extensions.gen_random_uuid(), course_id uuid not null references public.classroom_courses(id),
  external_coursework_id text not null unique, title text not null, description text not null default '',
  due_at timestamptz, alternate_link text, created_at timestamptz not null default now()
);
create table public.classroom_student_submissions (
  id uuid primary key default extensions.gen_random_uuid(), coursework_id uuid not null references public.classroom_coursework(id),
  identity_id uuid not null references public.identities(id), external_submission_id text not null unique,
  state text not null, assigned_grade numeric, updated_at timestamptz not null default now()
);
create table public.official_grades (
  id uuid primary key default extensions.gen_random_uuid(), identity_id uuid not null references public.identities(id),
  subject text not null, graded_on date not null, value numeric not null,
  grade_type text, weight numeric, comment text, source text not null,
  created_at timestamptz not null default now()
);

create index users_telegram_user_id_idx on public.users(telegram_user_id);
create index identity_claims_status_idx on public.identity_claims(status, created_at);
create index memberships_identity_active_idx on public.memberships(identity_id, active);
create index schedule_entries_date_time_idx on public.schedule_entries(lesson_date, start_time);
create index official_grades_identity_date_idx on public.official_grades(identity_id, graded_on desc);
create unique index users_identity_unique_idx on public.users(identity_id) where identity_id is not null;

-- This app does not use Supabase Auth. The verified Telegram backend is the only runtime data path.
-- All exposed tables remain deny-by-default for Data API roles.
alter table public.users enable row level security;
alter table public.identities enable row level security;
alter table public.identity_claims enable row level security;
alter table public.groups enable row level security;
alter table public.memberships enable row level security;
alter table public.schedule_entries enable row level security;
alter table public.announcements enable row level security;
alter table public.events enable row level security;
alter table public.schedule_syncs enable row level security;
alter table public.audit_log enable row level security;
alter table public.classroom_courses enable row level security;
alter table public.classroom_coursework enable row level security;
alter table public.classroom_student_submissions enable row level security;
alter table public.official_grades enable row level security;

-- No anonymous or authenticated Data API access. Explicit revokes keep exposure opt-in.
revoke all on table public.users, public.identities, public.identity_claims, public.groups, public.memberships,
  public.schedule_entries, public.schedule_syncs, public.announcements, public.events, public.audit_log,
  public.classroom_courses, public.classroom_coursework, public.classroom_student_submissions, public.official_grades from anon, authenticated;
