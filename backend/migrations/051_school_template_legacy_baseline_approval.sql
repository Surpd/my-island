CREATE TABLE IF NOT EXISTS public.school_template_baseline_approvals (
  scope text PRIMARY KEY CHECK (scope = 'school'),
  draft_id text NOT NULL,
  approved_revision integer NOT NULL CHECK (approved_revision > 0),
  approved_payload_sha256 text NOT NULL,
  missing_revision integer NOT NULL CHECK (missing_revision > 0),
  missing_payload_sha256 text NOT NULL,
  provenance text NOT NULL,
  actor_user_id text,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.school_template_baseline_approvals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.school_template_baseline_approvals FROM PUBLIC, anon, authenticated;
