-- Explicit pointer only: never infer the main editor template from canonical,
-- a name, creation order, or a copied payload marker. No existing row changes.
CREATE TABLE IF NOT EXISTS public.school_template_selection (
  scope TEXT PRIMARY KEY CHECK (scope = 'school'),
  draft_id TEXT NOT NULL REFERENCES public.schedule_experiment_drafts(id) ON DELETE RESTRICT,
  source_draft_id TEXT NOT NULL,
  source_revision INTEGER NOT NULL CHECK (source_revision > 0),
  source_payload_sha256 TEXT NOT NULL,
  activated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.school_template_selection ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.school_template_selection FROM PUBLIC, anon, authenticated;
