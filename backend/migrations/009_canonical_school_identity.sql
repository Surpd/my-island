-- Canonical school people stay independent from Telegram accounts. The legacy
-- users.identity_id column remains the runtime compatibility link.

alter table public.identities
  add column if not exists origin text not null default 'legacy',
  add column if not exists source_ref text,
  add column if not exists manually_confirmed boolean not null default false,
  add column if not exists created_at timestamptz not null default now();

create table if not exists public.account_identity_links (
  id uuid primary key default extensions.gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  identity_id uuid not null references public.identities(id),
  status text not null default 'confirmed' check (status in ('pending', 'confirmed', 'conflict', 'revoked')),
  source text not null,
  source_ref text,
  confirmed_by uuid references public.users(id),
  confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id),
  unique (identity_id)
);

insert into public.account_identity_links (user_id, identity_id, status, source, source_ref, confirmed_at)
select u.id, u.identity_id, 'confirmed', 'legacy_backfill', 'users.identity_id', u.created_at
  from public.users u
 where u.identity_id is not null
on conflict (user_id) do nothing;

-- Canonical people, role assignments and identity links are created through
-- explicit admin confirmation or a controlled data migration, never by names
-- or by assuming which Telegram account is the owner.

create index if not exists account_identity_links_status_idx on public.account_identity_links(status, created_at);
alter table public.account_identity_links enable row level security;
revoke all on table public.account_identity_links from anon, authenticated;
