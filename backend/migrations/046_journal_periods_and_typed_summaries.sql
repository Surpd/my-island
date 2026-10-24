-- Preserve journal sources and result history while recording period identity and typed metrics.
begin;

alter table public.journal_sources
  add column if not exists academic_year text not null default '2026/27',
  add column if not exists term_number smallint,
  add column if not exists period_status text not null default 'current';
alter table public.journal_sources drop constraint if exists journal_sources_term_number_check;
alter table public.journal_sources add constraint journal_sources_term_number_check
  check (term_number is null or term_number between 1 and 3);
alter table public.journal_sources drop constraint if exists journal_sources_period_status_check;
alter table public.journal_sources add constraint journal_sources_period_status_check
  check (period_status in ('current','archived','unassigned'));

create table if not exists public.journal_term_settings (
  spreadsheet_id text not null,
  grade integer not null check (grade between 5 and 11),
  academic_year text not null,
  current_term smallint not null default 1 check (current_term between 1 and 3),
  updated_at timestamptz not null default now(),
  primary key (spreadsheet_id, grade, academic_year)
);
alter table public.journal_term_settings enable row level security;
revoke all on public.journal_term_settings from anon, authenticated;
grant all on public.journal_term_settings to service_role;

update public.journal_sources set term_number = 1 where term_number is null;
insert into public.journal_term_settings(spreadsheet_id,grade,academic_year,current_term)
select distinct spreadsheet_id,grade,academic_year,1 from public.journal_sources
on conflict (spreadsheet_id,grade,academic_year) do nothing;

alter table public.journal_student_summaries add column if not exists summary_type text not null default 'technical';
alter table public.journal_summary_history add column if not exists summary_type text not null default 'technical';
update public.journal_student_summaries set summary_type = case
  when lower(trim(label)) in ('средняя отметка','средняя оценка','средний балл','средний балл за триместр') then 'average_grade'
  when lower(trim(label)) in ('допуск','допуск к аттестации','допуск к аттестации за триместр') then 'admission'
  when lower(trim(label)) in ('оценка за триместр','итоговая','итоговая оценка') then 'term_grade'
  when lower(trim(label)) in ('итого','сумма баллов за триместр','сумма баллов за триместр всего') then 'total_points'
  when lower(trim(label)) in ('пропущенные весы','пропущенные работы','не выставлены оценки') then 'missing_weights'
  else 'technical' end;
update public.journal_summary_history h set summary_type=s.summary_type
from public.journal_student_summaries s where s.id=h.summary_id;

create unique index if not exists journal_sources_one_current_period_idx
  on public.journal_sources(spreadsheet_id,grade,academic_year,subject)
  where period_status='current';
commit;
