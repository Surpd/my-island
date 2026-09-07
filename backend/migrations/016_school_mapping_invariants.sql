-- Tighten mapping target semantics and make confirmed manual decisions immutable.
alter table public.school_candidate_changes
  add constraint school_candidate_changes_evidence_ck check (evidence <> '{}'::jsonb);

alter table public.school_source_mappings
  add constraint school_source_mappings_target_type_ck check (
    (mapping_type = 'identity' and identity_id is not null) or
    (mapping_type = 'group' and group_id is not null) or
    (mapping_type = 'classroom_course' and classroom_course_id is not null) or
    (mapping_type in ('subject', 'audience_rule') and canonical_value is not null)
  );

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
