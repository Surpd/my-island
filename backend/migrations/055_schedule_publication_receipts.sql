begin;
create table if not exists public.schedule_publication_receipts (
    operation_id text primary key,
    actor_key text not null,
    request_hash text not null,
    result_json text not null
);
alter table public.schedule_publication_receipts enable row level security;
revoke all on public.schedule_publication_receipts from anon, authenticated;
grant all on public.schedule_publication_receipts to service_role;
commit;
