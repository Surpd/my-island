-- Store grade-wide teaching responsibility as one explicit target row.
-- Existing group-level assignments remain attached to canonical groups.
begin;

alter table public.teacher_assignments
  alter column group_id drop not null,
  add column if not exists grade_level text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.teacher_assignments'::regclass
      and conname = 'teacher_assignments_target_ck'
  ) then
    alter table public.teacher_assignments
      add constraint teacher_assignments_target_ck
      check (
        (group_id is not null and grade_level is null) or
        (group_id is null and grade_level is not null and grade_level ~ '^[0-9]{1,2}$')
      );
  end if;
end $$;

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
    if new.group_id is not null then
      select canonical, group_type into canonical_group, canonical_group_type from public.groups where id = new.group_id;
    end if;
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
  if new.active and (identity_status <> 'active' or
     (tg_table_name = 'teacher_assignments' and new.group_id is not null and canonical_group is not true) or
     (tg_table_name <> 'teacher_assignments' and canonical_group is not true)) then
    raise exception 'school relationship requires active identity and canonical group';
  end if;
  if new.active and tg_table_name = 'homeroom_assignments' and canonical_group_type <> 'class' then
    raise exception 'homeroom assignment requires a canonical class group';
  end if;
  return new;
end $$;



create unique index if not exists teacher_assignments_grade_target_uq
  on public.teacher_assignments(
    teacher_identity_id, grade_level, subject,
    coalesce(subject_subgroup, ''), coalesce(exam_track, ''),
    coalesce(classroom_course_id::text, ''), capability, source, source_ref
  )
  where group_id is null;

commit;

