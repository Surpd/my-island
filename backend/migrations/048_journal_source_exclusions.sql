begin;

alter table public.journal_sources
  add column if not exists excluded_from_journals boolean not null default false;

commit;
