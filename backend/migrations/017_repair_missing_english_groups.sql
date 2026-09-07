-- Repair groups whose validated source records were staged/applied before the
-- corresponding group rows existed.  The source is the current English roster
-- block; only exact or deterministic short-name matches are materialized.
with wanted(name, display_name, subject, base_class_name, subject_subgroup, exam_track, provenance_ref) as (
  values
    ('subject:Английский язык:7-8 класс:4 Ангелина', 'Английский язык · 7-8 класс · 4 Ангелина', 'Английский язык', '7-8', '4 Ангелина', null, 'списки групп 26-27!H26'),
    ('subject:Английский язык:7-8 класс:5 Игорь', 'Английский язык · 7-8 класс · 5 Игорь', 'Английский язык', '7-8', '5 Игорь', null, 'списки групп 26-27!J26'),
    ('subject:Английский язык:9 класс:7 Ангелина', 'Английский язык · 9 класс · 7 Ангелина', 'Английский язык', '9', '7 Ангелина', null, 'списки групп 26-27!N26'),
    ('subject:Английский язык:9 класс:8 ОГЭ Игорь', 'Английский язык · 9 класс · 8 ОГЭ Игорь', 'Английский язык', '9', null, 'ОГЭ', 'списки групп 26-27!P26'),
    ('subject:Английский язык:10-11 класс:10 ЕГЭ Игорь', 'Английский язык · 10-11 класс · 10 ЕГЭ Игорь', 'Английский язык', '10-11', null, 'ЕГЭ', 'списки групп 26-27!T26')
)
insert into groups (name, group_type, display_name, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical)
select w.name,
       case when w.exam_track is null then 'subject_group' else 'exam_track' end,
       w.display_name, w.subject, w.base_class_name, w.subject_subgroup, w.exam_track,
       'google_sheet', w.provenance_ref, true
from wanted w
where not exists (select 1 from groups g where g.name = w.name);

with source_rows as (
  select distinct on (sr.record_key)
    sr.record_key,
    sr.source_ref,
    sr.structural_payload->>'name' as person_name,
    sr.structural_payload->>'group' as group_name
  from school_source_records sr
  join school_source_snapshots ss on ss.id = sr.snapshot_id
  where sr.parse_status = 'validated'
    and sr.structural_payload->>'group' in (
      'subject:Английский язык:7-8 класс:4 Ангелина',
      'subject:Английский язык:7-8 класс:5 Игорь',
      'subject:Английский язык:9 класс:7 Ангелина',
      'subject:Английский язык:9 класс:8 ОГЭ Игорь',
      'subject:Английский язык:10-11 класс:10 ЕГЭ Игорь'
    )
  order by sr.record_key, ss.is_last_known_valid desc, ss.observed_at desc
), matched as (
  select distinct on (s.record_key)
    s.record_key, s.source_ref, s.group_name, i.id as identity_id
  from source_rows s
  join identities i on i.kind = 'student' and i.status = 'active'
   and (
     lower(regexp_replace(i.display_name, '[^[:alnum:]А-Яа-яЁё]+', '', 'g')) =
       lower(regexp_replace(s.person_name, '[^[:alnum:]А-Яа-яЁё]+', '', 'g'))
     or (
       split_part(lower(i.display_name), ' ', 1) = split_part(lower(s.person_name), ' ', 1)
       and split_part(lower(i.display_name), ' ', 2) = case split_part(lower(s.person_name), ' ', 2)
         when 'оля' then 'ольга'
         when 'катя' then 'екатерина'
         else split_part(lower(s.person_name), ' ', 2)
       end
     )
   )
  order by s.record_key,
    case when lower(regexp_replace(i.display_name, '[^[:alnum:]А-Яа-яЁё]+', '', 'g')) =
      lower(regexp_replace(s.person_name, '[^[:alnum:]А-Яа-яЁё]+', '', 'g')) then 0 else 1 end,
    i.id
)
insert into memberships (group_id, identity_id, member_role, source, active, valid_from, source_ref)
select g.id, m.identity_id, 'student', 'official_import', true, current_date, m.source_ref
from matched m
join groups g on g.name = m.group_name
where not exists (
  select 1 from memberships existing
  where existing.group_id = g.id
    and existing.identity_id = m.identity_id
    and existing.active = true
);
