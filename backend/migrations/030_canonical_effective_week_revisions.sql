-- A weekly sheet may be corrected after an effective week was materialized.
-- Preserve each source-derived overlay revision; never overwrite history or
-- mutate the canonical baseline.
begin;

alter table public.canonical_effective_weeks
  add column if not exists overlay_fingerprint text not null default '';

alter table public.canonical_effective_weeks
  add column if not exists revision_at timestamptz not null default now();

alter table public.canonical_effective_weeks
  drop constraint if exists canonical_effective_weeks_version_id_week_start_key;

create unique index if not exists canonical_effective_weeks_revision_idx
  on public.canonical_effective_weeks(version_id, week_start, overlay_fingerprint);

create index if not exists canonical_effective_weeks_latest_revision_idx
  on public.canonical_effective_weeks(version_id, week_start, revision_at desc);

commit;
