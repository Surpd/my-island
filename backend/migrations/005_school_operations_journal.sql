-- Production operations model: independent teacher assignments, homeroom capability,
-- audited membership overrides, audience-aware information, and read-only journals.

alter table public.announcements
  add column if not exists audience_kind text not null default 'all',
  add column if not exists audience_ref text,
  add column if not exists publish_at timestamptz,
  add column if not exists starts_at timestamptz,
  add column if not exists pinned boolean not null default false,
  add column if not exists author_user_id uuid references public.users(id),
  add column if not exists status text not null default 'published';

create table if not exists public.teacher_assignments (
  id uuid primary key default extensions.gen_random_uuid(),
  teacher_identity_id uuid not null references public.identities(id),
  group_id uuid not null references public.groups(id),
  subject text not null default '',
  capability text not null default 'teach',
  source text not null default 'admin_override',
  source_ref text,
  active boolean not null default true,
  valid_from date,
  valid_until date,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (teacher_identity_id, group_id, subject, capability, source)
);

create table if not exists public.homeroom_assignments (
  id uuid primary key default extensions.gen_random_uuid(),
  teacher_identity_id uuid not null references public.identities(id),
  class_group_id uuid not null references public.groups(id),
  source text not null default 'admin_override',
  source_ref text,
  active boolean not null default true,
  valid_from date,
  valid_until date,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (teacher_identity_id, class_group_id, source)
);

create table if not exists public.membership_overrides (
  id uuid primary key default extensions.gen_random_uuid(),
  identity_id uuid not null references public.identities(id),
  group_id uuid not null references public.groups(id),
  member_role app_role not null,
  action text not null check (action in ('include', 'exclude')),
  reason text,
  active boolean not null default true,
  valid_from date,
  valid_until date,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now()
);

create table if not exists public.journal_sources (
  id uuid primary key default extensions.gen_random_uuid(),
  spreadsheet_id text not null,
  spreadsheet_title text not null,
  grade integer not null check (grade between 5 and 11),
  subject text not null,
  sheet_title text not null,
  source_hash text,
  status text not null default 'ready',
  last_synced_at timestamptz,
  last_error text,
  unique (spreadsheet_id, sheet_title)
);

create table if not exists public.journal_assessments (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.journal_sources(id) on delete cascade,
  external_key text not null,
  title text not null,
  assessed_on date,
  weight numeric,
  max_score numeric,
  source_column text not null,
  raw_source jsonb,
  unique (source_id, external_key)
);

create table if not exists public.journal_results (
  id uuid primary key default extensions.gen_random_uuid(),
  assessment_id uuid not null references public.journal_assessments(id) on delete cascade,
  identity_id uuid references public.identities(id),
  student_name text not null,
  group_marker text not null,
  numeric_score numeric,
  status text,
  source_coordinate text not null,
  raw_source jsonb,
  unique (assessment_id, source_coordinate)
);

create table if not exists public.journal_group_mappings (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.journal_sources(id) on delete cascade,
  group_marker text not null,
  group_id uuid not null references public.groups(id),
  source text not null default 'admin_override',
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  unique (source_id, group_marker)
);

create index if not exists teacher_assignments_teacher_idx on public.teacher_assignments(teacher_identity_id, active);
create index if not exists teacher_assignments_group_idx on public.teacher_assignments(group_id, active);
create index if not exists teacher_assignments_created_by_idx on public.teacher_assignments(created_by);
create index if not exists homeroom_assignments_teacher_idx on public.homeroom_assignments(teacher_identity_id, active);
create index if not exists homeroom_assignments_group_idx on public.homeroom_assignments(class_group_id, active);
create index if not exists homeroom_assignments_created_by_idx on public.homeroom_assignments(created_by);
create index if not exists membership_overrides_subject_idx on public.membership_overrides(identity_id, group_id, active);
create index if not exists membership_overrides_group_idx on public.membership_overrides(group_id, active);
create index if not exists membership_overrides_created_by_idx on public.membership_overrides(created_by);
create index if not exists journal_results_identity_idx on public.journal_results(identity_id);
create index if not exists journal_results_group_idx on public.journal_results(group_marker);
create index if not exists journal_group_mappings_group_idx on public.journal_group_mappings(group_id);
create index if not exists journal_group_mappings_created_by_idx on public.journal_group_mappings(created_by);
create index if not exists announcements_author_idx on public.announcements(author_user_id);

alter table public.teacher_assignments enable row level security;
alter table public.homeroom_assignments enable row level security;
alter table public.membership_overrides enable row level security;
alter table public.journal_sources enable row level security;
alter table public.journal_assessments enable row level security;
alter table public.journal_results enable row level security;
alter table public.journal_group_mappings enable row level security;

revoke all on table public.teacher_assignments, public.homeroom_assignments,
  public.membership_overrides, public.journal_sources, public.journal_assessments,
  public.journal_results, public.journal_group_mappings from anon, authenticated;
