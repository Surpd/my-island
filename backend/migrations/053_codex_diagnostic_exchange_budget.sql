-- Singleton login budget: no client identifiers or credentials are persisted.
CREATE TABLE IF NOT EXISTS public.codex_diagnostic_exchange_budget (
  id integer PRIMARY KEY CHECK (id = 1),
  reset_at bigint NOT NULL,
  attempts integer NOT NULL
);
ALTER TABLE public.codex_diagnostic_exchange_budget ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.codex_diagnostic_exchange_budget FROM PUBLIC, anon, authenticated;
