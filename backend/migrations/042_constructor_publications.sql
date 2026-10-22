-- Prepared only. Apply to an approved school database as a separate release.
-- Manual runtime pointers and immutable constructor audit records are separate
-- from lesson/canonical payloads. Draft deletion must not erase publication
-- evidence, so draft_id deliberately has no cascading foreign key.
begin;

create table if not exists public.schedule_publication_heads (
    scope_key text primary key,
    published_id text not null,
    version_id text not null,
    revision bigint not null check (revision > 0),
    updated_at text not null
);

create table if not exists public.schedule_constructor_publications (
    id text primary key,
    draft_id text not null,
    draft_revision bigint not null check (draft_revision > 0),
    scope_kind text not null check (scope_kind in ('template','week')),
    scope_key text not null,
    base_version_id text,
    previous_published_id text,
    published_id text not null,
    version_id text not null,
    payload_fingerprint text not null,
    actor_user_id text,
    created_at text not null,
    metadata text not null
);

create index if not exists constructor_publications_scope_idx
    on public.schedule_constructor_publications(scope_key,created_at);

alter table public.schedule_publication_heads enable row level security;
alter table public.schedule_constructor_publications enable row level security;
revoke all on public.schedule_publication_heads from anon, authenticated;
revoke all on public.schedule_constructor_publications from anon, authenticated;
grant all on public.schedule_publication_heads to service_role;
grant all on public.schedule_constructor_publications to service_role;

commit;
