-- Dedicated agent sessions never bind to or inherit the role of a human user.
CREATE TABLE IF NOT EXISTS public.codex_diagnostic_sessions (
  id text PRIMARY KEY,
  token_hash text NOT NULL UNIQUE,
  credential_epoch text NOT NULL,
  scopes text NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.codex_diagnostic_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.codex_diagnostic_sessions FROM PUBLIC, anon, authenticated;
