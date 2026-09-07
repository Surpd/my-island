-- Keep the previous schedule parser output recoverable but outside the
-- canonical/runtime read path. Schedule Integration v1 will write a fresh
-- snapshot and may later remove these archived rows with an explicit operation.

alter table public.schedule_entries
  add column if not exists archived boolean not null default false;
alter table public.group_schedule_audiences
  add column if not exists archived boolean not null default false;
alter table public.schedule_syncs
  add column if not exists archived boolean not null default false;

create index if not exists schedule_entries_active_date_idx
  on public.schedule_entries(archived, lesson_date, start_time);
create index if not exists schedule_syncs_active_created_idx
  on public.schedule_syncs(archived, created_at desc);
create index if not exists group_schedule_audiences_active_idx
  on public.group_schedule_audiences(archived, group_id);
