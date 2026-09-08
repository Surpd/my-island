-- Browser admin authentication and reviewed reconciliation run metadata.
-- Apply through the existing production migration process; application startup
-- intentionally does not mutate Postgres schema.

begin;

create table if not exists public.admin_login_challenges (
  id bigserial primary key,
  token_hash text not null unique,
  actor_user_id uuid not null references public.users(id) on delete cascade,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.admin_browser_sessions (
  id bigserial primary key,
  token_hash text not null unique,
  user_id uuid not null references public.users(id) on delete cascade,
  expires_at timestamptz not null,
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.admin_reconciliation_runs (
  id uuid primary key default extensions.gen_random_uuid(),
  actor_user_id uuid references public.users(id),
  status text not null check (status in ('ready_for_review', 'approved', 'applied', 'failed')),
  payload jsonb not null,
  result jsonb,
  created_at timestamptz not null default now(),
  reviewed_at timestamptz,
  applied_at timestamptz
);

create index if not exists admin_browser_sessions_active_idx on public.admin_browser_sessions(token_hash, revoked_at, expires_at);
create index if not exists admin_login_challenges_actor_idx on public.admin_login_challenges(actor_user_id, created_at desc);
create index if not exists admin_browser_sessions_user_idx on public.admin_browser_sessions(user_id, revoked_at, expires_at);
create index if not exists admin_reconciliation_runs_actor_idx on public.admin_reconciliation_runs(actor_user_id, created_at desc);
create index if not exists admin_reconciliation_runs_status_idx on public.admin_reconciliation_runs(status, created_at desc);

-- The application uses a privileged server-side Postgres connection. Keep the
-- new public-schema tables deny-by-default for Supabase Data API roles.
alter table public.admin_login_challenges enable row level security;
alter table public.admin_browser_sessions enable row level security;
alter table public.admin_reconciliation_runs enable row level security;
revoke all on table public.admin_login_challenges, public.admin_browser_sessions,
  public.admin_reconciliation_runs from anon, authenticated;

commit;
