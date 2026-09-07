begin;

create table public.student_selection_facts (
  id uuid primary key default extensions.gen_random_uuid(),
  identity_id uuid not null references public.identities(id),
  academic_year text not null,
  grade_level text not null,
  subject text not null,
  selection_kind text not null check (selection_kind in ('oge', 'ege_profile')),
  source text not null,
  source_ref text not null,
  evidence jsonb not null default '{}'::jsonb,
  active boolean not null default true,
  valid_from date,
  valid_until date,
  created_at timestamptz not null default now(),
  check (valid_until is null or valid_from is null or valid_until >= valid_from),
  unique (identity_id, academic_year, subject, selection_kind, source, source_ref)
);

create unique index student_selection_facts_active_logical_idx
  on public.student_selection_facts(identity_id, academic_year, subject, selection_kind)
  where active and valid_until is null;
create index student_selection_facts_identity_idx
  on public.student_selection_facts(identity_id, active);

alter table public.student_selection_facts enable row level security;
revoke all on table public.student_selection_facts from anon, authenticated;

alter table public.school_candidate_changes
  drop constraint school_candidate_changes_entity_type_check;
alter table public.school_candidate_changes
  add constraint school_candidate_changes_entity_type_check check (
    entity_type in (
      'person', 'group', 'membership', 'teacher_assignment',
      'homeroom_assignment', 'source_mapping', 'schedule_audience',
      'selection_fact'
    )
  );

commit;
