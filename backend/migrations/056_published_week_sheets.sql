begin;
CREATE TABLE IF NOT EXISTS published_week_sheet_bindings(
        target_id TEXT NOT NULL, week_start TEXT NOT NULL, sheet_id INTEGER NOT NULL,
        delivered_revision INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(target_id,week_start), UNIQUE(target_id,sheet_id));
ALTER TABLE public.published_week_sheet_bindings ADD COLUMN IF NOT EXISTS delivered_fingerprint TEXT;
CREATE TABLE IF NOT EXISTS published_week_sheet_jobs(
        target_id TEXT NOT NULL, publication_id TEXT NOT NULL, week_start TEXT NOT NULL,
        head_revision INTEGER NOT NULL, directory_json TEXT NOT NULL, state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0,
        lease_token TEXT, lease_until INTEGER NOT NULL DEFAULT 0, error_code TEXT,
        PRIMARY KEY(target_id,publication_id), UNIQUE(target_id,week_start,head_revision));
alter table public.published_week_sheet_jobs enable row level security;
revoke all on public.published_week_sheet_jobs from anon, authenticated;
grant all on public.published_week_sheet_jobs to service_role;
alter table public.published_week_sheet_bindings enable row level security;
revoke all on public.published_week_sheet_bindings from anon, authenticated;
grant all on public.published_week_sheet_bindings to service_role;
commit;
