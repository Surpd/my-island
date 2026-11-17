CREATE TABLE IF NOT EXISTS public.school_template_selection_history (
  id text PRIMARY KEY,
  previous_draft_id text,
  previous_revision integer,
  previous_payload jsonb,
  previous_payload_sha256 text,
  next_draft_id text NOT NULL,
  next_revision integer NOT NULL CHECK (next_revision > 0),
  next_payload jsonb NOT NULL,
  next_payload_sha256 text NOT NULL,
  actor_user_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS school_template_selection_history_created_idx
  ON public.school_template_selection_history(created_at DESC);
ALTER TABLE public.school_template_selection_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.school_template_selection_history FROM PUBLIC, anon, authenticated;
