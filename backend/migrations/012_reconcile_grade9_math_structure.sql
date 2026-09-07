-- Reconcile the confirmed Grade 9 Mathematics instructional groups.
-- A/B/C are canonical subject groups; old 9-1/9-2/9-3 and the Classroom-only
-- group are bounded legacy/source rows, not runtime school structure.

begin;

-- Rename only the three rows created by the previous journal mapping pass.
-- If a canonical row already exists, fail rather than merging silently.
do $$
declare
  legacy_name text;
  canonical_name text;
  subgroup text;
begin
  for legacy_name, canonical_name, subgroup in
    select * from (values
      ('9-1 Дмитрий', 'grade9-math-A', 'A'),
      ('9-2 Дмитрий', 'grade9-math-B', 'B'),
      ('9-3 Дмитрий', 'grade9-math-C', 'C')
    ) as mapping(legacy_name, canonical_name, subgroup)
  loop
    if exists (select 1 from public.groups where name = canonical_name and group_type = 'subject_group')
       and exists (select 1 from public.groups where name = legacy_name and group_type = 'subject_subgroup') then
      raise exception 'Cannot reconcile duplicate canonical/legacy Grade 9 Math group: %', subgroup;
    end if;
    update public.groups
       set name = canonical_name,
           group_type = 'subject_group',
           display_name = 'Математика · группа ' || subgroup || ' · 9 класс',
           subject = 'Математика',
           base_class_name = null,
           subject_subgroup = subgroup,
           exam_track = null,
           provenance_source = 'school_structure',
           provenance_ref = 'grade9:math:' || subgroup,
           canonical = true
     where name = legacy_name
       and group_type = 'subject_subgroup'
       and subject = 'Математика'
       and subject_subgroup = subgroup
       and canonical = true;
  end loop;
end $$;

insert into public.groups (name, group_type, display_name, subject, base_class_name,
                           subject_subgroup, exam_track, provenance_source, provenance_ref, canonical)
values
  ('grade9-math-A', 'subject_group', 'Математика · группа A · 9 класс', 'Математика', null, 'A', null, 'school_structure', 'grade9:math:A', true),
  ('grade9-math-B', 'subject_group', 'Математика · группа B · 9 класс', 'Математика', null, 'B', null, 'school_structure', 'grade9:math:B', true),
  ('grade9-math-C', 'subject_group', 'Математика · группа C · 9 класс', 'Математика', null, 'C', null, 'school_structure', 'grade9:math:C', true)
on conflict (name, group_type) do update
  set display_name = excluded.display_name,
      subject = excluded.subject,
      base_class_name = excluded.base_class_name,
      subject_subgroup = excluded.subject_subgroup,
      exam_track = excluded.exam_track,
      provenance_source = excluded.provenance_source,
      provenance_ref = excluded.provenance_ref,
      canonical = true;

-- Move every known source/reference to the canonical dimension before removing
-- the Classroom-only duplicate. This preserves mappings and memberships.
update public.teacher_assignments ta
   set group_id = g.id,
       base_class_name = null,
       subject_subgroup = g.subject_subgroup,
       exam_track = null
  from public.groups old_group
  join public.groups g on g.group_type = 'subject_group'
                       and g.subject = 'Математика'
                       and g.subject_subgroup = old_group.subject_subgroup
 where ta.group_id = old_group.id
   and old_group.group_type = 'subject_subgroup';

update public.journal_group_mappings gm
   set group_id = g.id,
       group_marker = case gm.group_marker when '9-1' then 'A' when '9-2' then 'B' when '9-3' then 'C' else gm.group_marker end,
       subject_subgroup = g.subject_subgroup,
       base_class_name = null,
       exam_track = null
  from public.journal_sources js
       cross join public.groups g
 where gm.source_id = js.id
   and js.grade = 9
   and js.subject = 'Математика'
   and gm.group_marker in ('9-1', '9-2', '9-3')
   and g.group_type = 'subject_group'
   and g.subject = 'Математика'
   and g.subject_subgroup = case gm.group_marker when '9-1' then 'A' when '9-2' then 'B' when '9-3' then 'C' end;

update public.journal_students student
   set group_marker = case student.group_marker when '9-1' then 'A' when '9-2' then 'B' when '9-3' then 'C' else student.group_marker end,
       raw_source = jsonb_set(coalesce(student.raw_source, '{}'::jsonb), '{legacy_group_marker}', to_jsonb(student.group_marker), true),
       updated_at = now()
  from public.journal_sources js
 where student.source_id = js.id
   and js.grade = 9
   and js.subject = 'Математика'
   and student.group_marker in ('9-1', '9-2', '9-3');

update public.journal_results result
   set group_marker = case result.group_marker when '9-1' then 'A' when '9-2' then 'B' when '9-3' then 'C' else result.group_marker end,
       raw_source = jsonb_set(coalesce(result.raw_source, '{}'::jsonb), '{legacy_group_marker}', to_jsonb(result.group_marker), true)
  from public.journal_assessments assessment
  join public.journal_sources js on js.id = assessment.source_id
 where result.assessment_id = assessment.id
   and js.grade = 9
   and js.subject = 'Математика'
   and result.group_marker in ('9-1', '9-2', '9-3');

-- Repoint the Classroom course and its student membership from the source-only
-- class row to the confirmed C instructional group. No new membership is made.
update public.classroom_courses course
   set group_id = canonical.id
  from public.groups old_group
  join public.groups canonical on canonical.group_type = 'subject_group'
                               and canonical.subject = 'Математика'
                               and canonical.subject_subgroup = 'C'
 where course.group_id = old_group.id
   and old_group.group_type = 'class'
   and old_group.provenance_source = 'classroom_mapping'
   and old_group.subject = 'Математика'
   and old_group.subject_subgroup = 'C';

-- Merge a duplicate membership only when the same identity/role/source/ref is
-- already present on canonical C; otherwise preserve the existing row by move.
delete from public.memberships old_membership
 where old_membership.group_id in (
   select old_group.id from public.groups old_group
    where old_group.group_type = 'class'
      and old_group.provenance_source = 'classroom_mapping'
      and old_group.subject = 'Математика'
      and old_group.subject_subgroup = 'C'
 )
 and exists (
   select 1 from public.memberships canonical_membership
    where canonical_membership.group_id = (select id from public.groups where name = 'grade9-math-C' and group_type = 'subject_group')
      and canonical_membership.identity_id = old_membership.identity_id
      and canonical_membership.member_role = old_membership.member_role
      and canonical_membership.source = old_membership.source
      and canonical_membership.source_ref = old_membership.source_ref
 );

update public.memberships membership
   set group_id = canonical.id
  from public.groups old_group
  join public.groups canonical on canonical.group_type = 'subject_group'
                               and canonical.subject = 'Математика'
                               and canonical.subject_subgroup = 'C'
 where membership.group_id = old_group.id
   and old_group.group_type = 'class'
   and old_group.provenance_source = 'classroom_mapping'
   and old_group.subject = 'Математика'
   and old_group.subject_subgroup = 'C';

-- Archived schedule audience rows are retained for diagnostics/recovery but no
-- longer point at the disposable Classroom group.
delete from public.group_schedule_audiences old_audience
 where old_audience.group_id in (
   select old_group.id from public.groups old_group
    where old_group.group_type = 'class'
      and old_group.provenance_source = 'classroom_mapping'
      and old_group.subject = 'Математика'
      and old_group.subject_subgroup = 'C'
 )
 and exists (
   select 1 from public.group_schedule_audiences canonical_audience
    where canonical_audience.group_id = (select id from public.groups where name = 'grade9-math-C' and group_type = 'subject_group')
      and canonical_audience.audience = old_audience.audience
      and canonical_audience.subject = old_audience.subject
      and canonical_audience.subject_subgroup = old_audience.subject_subgroup
      and canonical_audience.exam_track = old_audience.exam_track
 );

update public.group_schedule_audiences audience
   set group_id = canonical.id
  from public.groups old_group
  join public.groups canonical on canonical.group_type = 'subject_group'
                               and canonical.subject = 'Математика'
                               and canonical.subject_subgroup = 'C'
 where audience.group_id = old_group.id
   and old_group.group_type = 'class'
   and old_group.provenance_source = 'classroom_mapping'
   and old_group.subject = 'Математика'
   and old_group.subject_subgroup = 'C';

delete from public.groups old_group
 where old_group.group_type = 'class'
   and old_group.provenance_source = 'classroom_mapping'
   and old_group.subject = 'Математика'
   and old_group.subject_subgroup = 'C'
   and old_group.name = '9-C Математика Дмитрий'
   and not exists (select 1 from public.classroom_courses where group_id = old_group.id)
   and not exists (select 1 from public.memberships where group_id = old_group.id)
   and not exists (select 1 from public.teacher_assignments where group_id = old_group.id)
   and not exists (select 1 from public.group_schedule_audiences where group_id = old_group.id);

create index if not exists groups_school_structure_ref_idx
  on public.groups(provenance_source, provenance_ref);

commit;
