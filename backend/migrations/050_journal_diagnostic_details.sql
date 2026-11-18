ALTER TABLE public.journal_sources
  ADD COLUMN IF NOT EXISTS last_error_code text,
  ADD COLUMN IF NOT EXISTS last_error_details text,
  ADD COLUMN IF NOT EXISTS classification text,
  ADD COLUMN IF NOT EXISTS classification_reason text;
