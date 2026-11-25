begin;
create table if not exists public.journal_cell_events (
    id uuid primary key default extensions.gen_random_uuid(),
    source_id uuid not null references public.journal_sources(id) on delete cascade,
    assessment_id uuid not null,
    journal_student_id uuid not null,
    group_marker text not null,
    change_kind text not null check(change_kind in ('added','changed','removed')),
    observed_at timestamptz not null default now()
);
create index if not exists journal_cell_events_source_time on public.journal_cell_events(source_id,observed_at);
alter table public.journal_cell_events enable row level security;
revoke all on public.journal_cell_events from anon, authenticated;
grant all on public.journal_cell_events to service_role;
commit;
