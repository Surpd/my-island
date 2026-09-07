-- The first directory apply also left four validated roster groups without
-- canonical group rows. Restore only groups proven by their live sheet headers.
with wanted(name, display_name, subject, base_class_name, subject_subgroup, exam_track, provenance_ref) as (
  values
    ('subject:Информатика:9 класс:ОГЭ Тарас', 'Информатика · 9 класс · ОГЭ Тарас', 'Информатика', '9', null, 'ОГЭ', 'списки групп 26-27!L86'),
    ('subject:Математика:11 класс:Угл Анна', 'Математика · 11 класс · Угл Анна', 'Математика', '11', 'Угл Анна', null, 'списки групп 26-27!V5'),
    ('subject:Математика:7 класс:7-2 Иван', 'Математика · 7 класс · 7-2 Иван', 'Математика', '7', '7-2 Иван', null, 'списки групп 26-27!H5'),
    ('subject:Обществознание:9 класс:ОГЭ Антон', 'Обществознание · 9 класс · ОГЭ Антон', 'Обществознание', '9', null, 'ОГЭ', 'списки групп 26-27!D50')
)
insert into groups (name, group_type, display_name, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical)
select w.name, case when w.exam_track is null then 'subject_group' else 'exam_track' end,
       w.display_name,w.subject,w.base_class_name,w.subject_subgroup,w.exam_track,
       'google_sheet',w.provenance_ref,true
from wanted w where not exists (select 1 from groups g where g.name=w.name);

with aliases(source_given, canonical_given) as (
  values ('вася','василий'), ('николай','коля'), ('коля','николай'),
         ('саша','александра'), ('катя','екатерина')
), source_rows as (
  select distinct on (sr.record_key)
    sr.record_key,sr.source_ref,sr.structural_payload->>'name' person_name,
    sr.structural_payload->>'group' group_name
  from school_source_records sr join school_source_snapshots ss on ss.id=sr.snapshot_id
  where sr.parse_status='validated' and sr.structural_payload->>'group' in (
    'subject:Информатика:9 класс:ОГЭ Тарас','subject:Математика:11 класс:Угл Анна',
    'subject:Математика:7 класс:7-2 Иван','subject:Обществознание:9 класс:ОГЭ Антон')
  order by sr.record_key,ss.is_last_known_valid desc,ss.observed_at desc
), matched as (
  select distinct on (s.record_key) s.source_ref,s.group_name,s.person_name,i.id identity_id
  from source_rows s join identities i on i.kind='student' and i.status='active'
   and (
     lower(regexp_replace(i.display_name,'[^[:alnum:]А-Яа-яЁё]+','','g')) = lower(regexp_replace(s.person_name,'[^[:alnum:]А-Яа-яЁё]+','','g'))
     or (
       split_part(lower(i.display_name),' ',1)=split_part(lower(s.person_name),' ',1)
       and split_part(lower(i.display_name),' ',2)=coalesce(
         (select a.canonical_given from aliases a where a.source_given=split_part(lower(s.person_name),' ',2)),
         split_part(lower(s.person_name),' ',2))
     )
   )
  order by s.record_key,case when lower(regexp_replace(i.display_name,'[^[:alnum:]А-Яа-яЁё]+','','g')) = lower(regexp_replace(s.person_name,'[^[:alnum:]А-Яа-яЁё]+','','g')) then 0 else 1 end,i.id
)
insert into memberships (group_id,identity_id,member_role,source,active,valid_from,source_ref)
select g.id,m.identity_id,'student','official_import',true,current_date,m.source_ref
from matched m join groups g on g.name=m.group_name
where not exists (select 1 from memberships existing where existing.group_id=g.id and existing.identity_id=m.identity_id and existing.active=true);
