-- Complete the repaired English rosters for source short names that already
-- have a deterministic canonical identity mapping in the directory.
with aliases(source_given, canonical_given) as (
  values ('маша', 'мария'), ('оля', 'ольга'), ('катя', 'екатерина')
), source_rows as (
  select distinct on (sr.record_key)
    sr.record_key, sr.source_ref,
    sr.structural_payload->>'name' as person_name,
    sr.structural_payload->>'group' as group_name
  from school_source_records sr
  join school_source_snapshots ss on ss.id=sr.snapshot_id
  where sr.parse_status='validated'
    and sr.structural_payload->>'group' in (
      'subject:Английский язык:7-8 класс:4 Ангелина',
      'subject:Английский язык:7-8 класс:5 Игорь',
      'subject:Английский язык:9 класс:7 Ангелина',
      'subject:Английский язык:9 класс:8 ОГЭ Игорь',
      'subject:Английский язык:10-11 класс:10 ЕГЭ Игорь')
  order by sr.record_key, ss.is_last_known_valid desc, ss.observed_at desc
), matched as (
  select distinct on (s.record_key)
    s.source_ref, s.group_name, i.id as identity_id
  from source_rows s
  join aliases a on split_part(lower(s.person_name), ' ', 2)=a.source_given
  join identities i on i.kind='student' and i.status='active'
    and split_part(lower(i.display_name), ' ', 1)=split_part(lower(s.person_name), ' ', 1)
    and split_part(lower(i.display_name), ' ', 2)=a.canonical_given
  order by s.record_key, i.id
)
insert into memberships (group_id, identity_id, member_role, source, active, valid_from, source_ref)
select g.id, m.identity_id, 'student', 'official_import', true, current_date, m.source_ref
from matched m join groups g on g.name=m.group_name
where not exists (
  select 1 from memberships existing
  where existing.group_id=g.id and existing.identity_id=m.identity_id and existing.active=true
);
