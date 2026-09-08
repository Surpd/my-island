-- Security/index backfill for 022_admin_browser_sessions.
-- 022 was already applied to production before the advisor check; this
-- additive patch brings the live schema to the migration's final contract.

begin;

create index if not exists admin_login_challenges_actor_idx on public.admin_login_challenges(actor_user_id, created_at desc);
create index if not exists admin_browser_sessions_user_idx on public.admin_browser_sessions(user_id, revoked_at, expires_at);
create index if not exists admin_reconciliation_runs_actor_idx on public.admin_reconciliation_runs(actor_user_id, created_at desc);

alter table public.admin_login_challenges enable row level security;
alter table public.admin_browser_sessions enable row level security;
alter table public.admin_reconciliation_runs enable row level security;
revoke all on table public.admin_login_challenges, public.admin_browser_sessions,
  public.admin_reconciliation_runs from anon, authenticated;

commit;
