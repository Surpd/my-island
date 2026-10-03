begin;

create table if not exists public.google_oauth_pending_states (
  state_hash text primary key,
  actor_user_id text not null,
  expires_at_epoch bigint not null,
  created_at timestamptz not null default now()
);

create index if not exists google_oauth_pending_states_expiry_idx
  on public.google_oauth_pending_states(expires_at_epoch);

create table if not exists public.google_oauth_credentials (
  credential_key text primary key check (credential_key = 'primary'),
  token_ciphertext text not null,
  account_email text not null,
  granted_scopes text not null default '',
  updated_by text,
  updated_at timestamptz not null default now()
);

alter table public.google_oauth_pending_states enable row level security;
alter table public.google_oauth_credentials enable row level security;
revoke all on table public.google_oauth_pending_states from anon, authenticated;
revoke all on table public.google_oauth_credentials from anon, authenticated;

commit;
