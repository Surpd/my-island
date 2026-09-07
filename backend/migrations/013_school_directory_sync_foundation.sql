-- School Directory bootstrap/sync foundation.
-- External data is staged with evidence and reviewed before canonical writes.
-- Student membership, teacher assignment and schedule audience remain separate.

begin;

alter table public.memberships
  add constraint memberships_valid_period_ck
  check (valid_until is null or valid_from is null or valid_until >= valid_from),
  add constraint memberships_student_only_ck
  check (member_role = 'student');

alter table public.teacher_assignments
  alter column source_ref set default '',
  add constraint teacher_assignments_valid_period_ck
  check (valid_until is null or valid_from is null or valid_until >= valid_from);

alter table public.homeroom_assignments
  alter column source_ref set default '',
  add constraint homeroom_assignments_valid_period_ck
  check (valid_until is null or valid_from is null or valid_until >= valid_from);

update public.teacher_assignments set source_ref = '' where source_ref is null;
update public.homeroom_assignments set source_ref = '' where source_ref is null;
alter table public.teacher_assignments alter column source_ref set not null;
alter table public.homeroom_assignments alter column source_ref set not null;

do $$
declare constraint_name text;
begin
  select conname into constraint_name from pg_constraint
   where conrelid = 'public.teacher_assignments'::regclass and contype = 'u'
     and pg_get_constraintdef(oid) like '%teacher_identity_id, group_id, subject, capability, source%';
  if constraint_name is not null then execute format('alter table public.teacher_assignments drop constraint %I', constraint_name); end if;
  select conname into constraint_name from pg_constraint
   where conrelid = 'public.homeroom_assignments'::regclass and contype = 'u'
     and pg_get_constraintdef(oid) like '%teacher_identity_id, class_group_id, source%';
  if constraint_name is not null then execute format('alter table public.homeroom_assignments drop constraint %I', constraint_name); end if;
end $$;

alter table public.teacher_assignments add constraint teacher_assignments_source_ref_key
  unique (teacher_identity_id, group_id, subject, capability, source, source_ref);
alter table public.homeroom_assignments add constraint homeroom_assignments_source_ref_key
  unique (teacher_identity_id, class_group_id, source, source_ref);

alter table public.membership_overrides
  add constraint membership_overrides_valid_period_ck
  check (valid_until is null or valid_from is null or valid_until >= valid_from),
  add constraint membership_overrides_student_only_ck
  check (member_role = 'student');

create or replace function public.validate_school_relationship()
returns trigger language plpgsql set search_path = '' as $$
declare
  identity_kind text;
  identity_status text;
  canonical_group boolean;
  canonical_group_type text;
begin
  if tg_table_name = 'memberships' then
    select kind, status into identity_kind, identity_status from public.identities where id = new.identity_id;
    select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.group_id;
    if new.member_role <> 'student' or identity_kind <> 'student' then
      raise exception 'membership requires a student identity';
    end if;
  elsif tg_table_name = 'teacher_assignments' then
    select kind, status into identity_kind, identity_status from public.identities where id = new.teacher_identity_id;
    select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.group_id;
    if identity_kind <> 'teacher' then
      raise exception 'teacher assignment requires a teacher identity';
    end if;
  else
    select kind, status into identity_kind, identity_status from public.identities where id = new.teacher_identity_id;
    select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.class_group_id;
    if identity_kind <> 'teacher' then
      raise exception 'homeroom assignment requires a teacher identity';
    end if;
  end if;
  if identity_status <> 'active' or canonical_group is not true then
    raise exception 'school relationship requires active identity and canonical group';
  end if;
  if tg_table_name = 'homeroom_assignments' and canonical_group_type <> 'class' then
    raise exception 'homeroom assignment requires a canonical class group';
  end if;
  return new;
end $$;

create trigger memberships_validate_school_relationship
  before insert or update on public.memberships
  for each row execute function public.validate_school_relationship();
create trigger teacher_assignments_validate_school_relationship
  before insert or update on public.teacher_assignments
  for each row execute function public.validate_school_relationship();
create trigger homeroom_assignments_validate_school_relationship
  before insert or update on public.homeroom_assignments
  for each row execute function public.validate_school_relationship();

create table public.school_sources (
  id uuid primary key default extensions.gen_random_uuid(),
  source_type text not null check (source_type in (
    'base_class_list', 'instructional_group_list', 'exam_profile_list',
    'journal', 'schedule', 'classroom', 'manual', 'other'
  )),
  external_key text not null,
  display_name text not null,
  location_ref text,
  authority_status text not null default 'unknown'
    check (authority_status in ('unknown', 'reference', 'authoritative', 'manual')),
  configuration jsonb not null default '{}'::jsonb,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_type, external_key)
);

create table public.school_sync_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.school_sources(id),
  mode text not null check (mode in ('bootstrap', 'incremental', 'full_reparse')),
  status text not null default 'started'
    check (status in ('started', 'staged', 'applied', 'failed', 'unresolved')),
  idempotency_key text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  diagnostics jsonb not null default '{}'::jsonb,
  unique (source_id, idempotency_key)
);

create table public.school_source_snapshots (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.school_sources(id),
  sync_run_id uuid not null references public.school_sync_runs(id),
  previous_snapshot_id uuid references public.school_source_snapshots(id),
  fingerprint text not null,
  observed_at timestamptz not null,
  effective_from date,
  effective_until date,
  raw_payload jsonb,
  structural_payload jsonb not null default '{}'::jsonb,
  status text not null default 'staged'
    check (status in ('staged', 'valid', 'rejected', 'superseded')),
  is_last_known_valid boolean not null default false,
  created_at timestamptz not null default now(),
  check (effective_until is null or effective_from is null or effective_until >= effective_from),
  unique (source_id, fingerprint)
);

create unique index school_source_snapshots_last_valid_idx
  on public.school_source_snapshots(source_id) where is_last_known_valid;

create table public.school_source_records (
  id uuid primary key default extensions.gen_random_uuid(),
  snapshot_id uuid not null references public.school_source_snapshots(id) on delete cascade,
  record_key text not null,
  fingerprint text not null,
  source_ref text not null,
  change_kind text not null check (change_kind in ('new', 'changed', 'unchanged', 'deleted')),
  parse_status text not null default 'structural'
    check (parse_status in ('structural', 'semantic_required', 'validated', 'unresolved', 'rejected')),
  raw_payload jsonb,
  structural_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (snapshot_id, record_key)
);

create table public.school_semantic_interpretations (
  id uuid primary key default extensions.gen_random_uuid(),
  source_record_id uuid not null references public.school_source_records(id) on delete cascade,
  provider text not null,
  model text not null,
  request_fingerprint text not null,
  request_payload jsonb not null,
  response_payload jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'validated', 'rejected', 'unresolved', 'failed')),
  validation_diagnostics jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (source_record_id, provider, model, request_fingerprint)
);

create table public.school_candidate_changes (
  id uuid primary key default extensions.gen_random_uuid(),
  sync_run_id uuid not null references public.school_sync_runs(id),
  source_record_id uuid references public.school_source_records(id),
  change_type text not null check (change_type in ('create', 'update', 'end', 'map')),
  entity_type text not null check (entity_type in (
    'person', 'group', 'membership', 'teacher_assignment', 'homeroom_assignment',
    'source_mapping', 'schedule_audience'
  )),
  natural_key text not null,
  proposed_payload jsonb not null,
  evidence jsonb not null check (evidence <> '{}'::jsonb),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected', 'applied', 'unresolved', 'conflict')),
  manual_decision boolean not null default false,
  decision_by uuid references public.users(id),
  decision_at timestamptz,
  applied_at timestamptz,
  created_at timestamptz not null default now(),
  unique (sync_run_id, entity_type, natural_key)
);

create table public.school_resolution_issues (
  id uuid primary key default extensions.gen_random_uuid(),
  sync_run_id uuid not null references public.school_sync_runs(id),
  source_record_id uuid references public.school_source_records(id),
  candidate_change_id uuid references public.school_candidate_changes(id),
  issue_type text not null,
  status text not null default 'open' check (status in ('open', 'resolved', 'ignored')),
  details jsonb not null,
  evidence jsonb not null default '{}'::jsonb,
  resolution jsonb,
  resolved_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

create table public.school_source_mappings (
  id uuid primary key default extensions.gen_random_uuid(),
  source_id uuid not null references public.school_sources(id),
  external_key text not null,
  mapping_type text not null check (mapping_type in ('identity', 'group', 'classroom_course', 'subject', 'audience_rule')),
  identity_id uuid references public.identities(id),
  group_id uuid references public.groups(id),
  classroom_course_id uuid references public.classroom_courses(id),
  canonical_value text,
  status text not null default 'proposed' check (status in ('proposed', 'confirmed', 'conflict', 'revoked')),
  manually_confirmed boolean not null default false,
  evidence jsonb not null default '{}'::jsonb,
  valid_from date,
  valid_until date,
  created_by uuid references public.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  supersedes_mapping_id uuid references public.school_source_mappings(id),
  check (num_nonnulls(identity_id, group_id, classroom_course_id, canonical_value) = 1),
  check (
    (mapping_type = 'identity' and identity_id is not null) or
    (mapping_type = 'group' and group_id is not null) or
    (mapping_type = 'classroom_course' and classroom_course_id is not null) or
    (mapping_type in ('subject', 'audience_rule') and canonical_value is not null)
  ),
  check (valid_until is null or valid_from is null or valid_until >= valid_from)
);

create unique index school_source_mappings_current_idx
  on public.school_source_mappings(source_id, external_key, mapping_type)
  where valid_until is null and status <> 'revoked';

create or replace function public.protect_manual_school_mapping()
returns trigger language plpgsql set search_path = '' as $$
begin
  if old.manually_confirmed and (
    new.source_id is distinct from old.source_id or
    new.external_key is distinct from old.external_key or
    new.mapping_type is distinct from old.mapping_type or
    new.identity_id is distinct from old.identity_id or
    new.group_id is distinct from old.group_id or
    new.classroom_course_id is distinct from old.classroom_course_id or
    new.canonical_value is distinct from old.canonical_value or
    new.status is distinct from old.status or
    new.manually_confirmed is not true
  ) then
    raise exception 'manually confirmed source mapping is immutable; end and supersede it';
  end if;
  return new;
end $$;

create trigger school_source_mappings_protect_manual
  before update on public.school_source_mappings
  for each row execute function public.protect_manual_school_mapping();

alter table public.schedule_entries
  add column if not exists audience_rule jsonb,
  add column if not exists resolved_audience jsonb,
  add column if not exists teacher_identity_ids uuid[] not null default '{}',
  add column if not exists source_snapshot_id uuid references public.school_source_snapshots(id);

create index school_sync_runs_review_idx on public.school_sync_runs(status, started_at desc);
create index school_source_records_diff_idx on public.school_source_records(snapshot_id, change_kind, parse_status);
create index school_candidate_changes_review_idx on public.school_candidate_changes(status, entity_type, created_at);
create index school_resolution_issues_open_idx on public.school_resolution_issues(status, issue_type, created_at);
create index school_source_mappings_target_idx on public.school_source_mappings(mapping_type, status, identity_id, group_id);
create index schedule_entries_source_snapshot_idx on public.schedule_entries(source_snapshot_id);

alter table public.school_sources enable row level security;
alter table public.school_sync_runs enable row level security;
alter table public.school_source_snapshots enable row level security;
alter table public.school_source_records enable row level security;
alter table public.school_semantic_interpretations enable row level security;
alter table public.school_candidate_changes enable row level security;
alter table public.school_resolution_issues enable row level security;
alter table public.school_source_mappings enable row level security;

revoke all on table public.school_sources, public.school_sync_runs,
  public.school_source_snapshots, public.school_source_records,
  public.school_semantic_interpretations, public.school_candidate_changes,
  public.school_resolution_issues, public.school_source_mappings from anon, authenticated;

commit;
