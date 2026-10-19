-- Archive canonical school groups without changing their identity or history.
-- Archived groups remain canonical so historical schedule and journal links resolve;
-- selectors should omit rows with archived_at set when offering new choices.
ALTER TABLE public.groups
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;

CREATE INDEX IF NOT EXISTS groups_archived_at_idx
  ON public.groups (archived_at)
  WHERE archived_at IS NOT NULL;
