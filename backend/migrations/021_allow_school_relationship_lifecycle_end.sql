-- Historical relationships must be endable after an identity or group becomes inactive.
-- Creation/reactivation of an active relationship remains strictly validated.

create or replace function public.validate_school_relationship()
returns trigger
language plpgsql
set search_path = ''
as $$
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

  if new.active and (identity_status <> 'active' or canonical_group is not true) then
    raise exception 'school relationship requires active identity and canonical group';
  end if;
  if new.active and tg_table_name = 'homeroom_assignments' and canonical_group_type <> 'class' then
    raise exception 'homeroom assignment requires a canonical class group';
  end if;
  return new;
end;
$$;
