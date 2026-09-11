CREATE TABLE IF NOT EXISTS public.student_preview_targets (
  preview_id uuid PRIMARY KEY REFERENCES public.student_preview_sessions(id) ON DELETE CASCADE,
  identity_id uuid NOT NULL REFERENCES public.identities(id) ON DELETE CASCADE
);

ALTER TABLE public.student_preview_targets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.student_preview_targets FROM anon, authenticated;
