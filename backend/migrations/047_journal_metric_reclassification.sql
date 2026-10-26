-- Safely move exact calculated journal columns out of ordinary assignments.
begin;
-- Reclassify only exact known calculation columns. Keep the original result/history rows intact,
-- mirror values into summaries (and their history), and stop treating those columns as assignments.
with classified as (
  select a.id assessment_id,a.source_id,a.title,s.id student_id,s.display_name,s.group_marker,
         case when lower(trim(a.title)) in ('средняя отметка','средняя оценка','средний балл','средний балл за триместр') then 'average_grade'
              when lower(trim(a.title)) in ('допуск','допуск к аттестации','допуск к аттестации за триместр') then 'admission'
              when lower(trim(a.title)) in ('оценка за триместр','итоговая','итоговая оценка') then 'term_grade'
              when lower(trim(a.title)) in ('итого','сумма баллов за триместр','сумма баллов за триместр всего') then 'total_points'
              when lower(trim(a.title)) in ('пропущенные весы','пропущенные работы','не выставлены оценки') then 'missing_weights' end summary_type,
         r.id result_id,r.numeric_score,r.status,r.source_coordinate,r.raw_source,r.is_current
  from public.journal_assessments a
  join public.journal_sources src on src.id=a.source_id
  join public.journal_results r on r.assessment_id=a.id
  join public.journal_students s on s.id=r.journal_student_id
  where lower(trim(a.title)) in ('средняя отметка','средняя оценка','средний балл','средний балл за триместр',
    'допуск','допуск к аттестации','допуск к аттестации за триместр','оценка за триместр','итоговая','итоговая оценка',
    'итого','сумма баллов за триместр','сумма баллов за триместр всего','пропущенные весы','пропущенные работы','не выставлены оценки')
), upserted as (
  insert into public.journal_student_summaries(journal_student_id,label,summary_type,numeric_value,text_value,source_coordinate,raw_source,is_current)
  select student_id,title,summary_type,numeric_score,case when numeric_score is null then status end,source_coordinate,raw_source,is_current
  from classified where is_current is true
  on conflict(journal_student_id,label) do update set
    summary_type=excluded.summary_type,numeric_value=excluded.numeric_value,text_value=excluded.text_value,
    source_coordinate=excluded.source_coordinate,raw_source=excluded.raw_source,is_current=excluded.is_current,updated_at=now()
  returning id,journal_student_id,label
)
insert into public.journal_summary_history(summary_id,summary_type,numeric_value,text_value,source_coordinate,raw_source,is_current,recorded_at)
select summary.id,c.summary_type,h.numeric_score,case when h.numeric_score is null then h.status end,h.source_coordinate,h.raw_source,h.is_current,h.recorded_at
from classified c
join public.journal_student_summaries summary on summary.journal_student_id=c.student_id and summary.label=c.title
join public.journal_result_history h on h.result_id=c.result_id
where not exists (select 1 from public.journal_summary_history existing where existing.summary_id=summary.id
  and existing.source_coordinate=h.source_coordinate and existing.numeric_value is not distinct from h.numeric_score
  and existing.text_value is not distinct from case when h.numeric_score is null then h.status end
  and existing.recorded_at=h.recorded_at);

update public.journal_assessments a set active=false
where lower(trim(a.title)) in ('средняя отметка','средняя оценка','средний балл','средний балл за триместр',
  'допуск','допуск к аттестации','допуск к аттестации за триместр','оценка за триместр','итоговая','итоговая оценка',
  'итого','сумма баллов за триместр','сумма баллов за триместр всего','пропущенные весы','пропущенные работы','не выставлены оценки');
update public.journal_results r set is_current=false,updated_at=now()
from public.journal_assessments a where a.id=r.assessment_id and a.active=false
  and lower(trim(a.title)) in ('средняя отметка','средняя оценка','средний балл','средний балл за триместр',
  'допуск','допуск к аттестации','допуск к аттестации за триместр','оценка за триместр','итоговая','итоговая оценка',
  'итого','сумма баллов за триместр','сумма баллов за триместр всего','пропущенные весы','пропущенные работы','не выставлены оценки');
commit;
