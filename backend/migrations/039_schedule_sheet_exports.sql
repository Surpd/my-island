-- Explicit additive operator migration; no existing schedule records are modified.
CREATE TABLE IF NOT EXISTS schedule_sheet_exports (
  export_key TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  target_id TEXT NOT NULL,
  sheet_id INTEGER NOT NULL,
  mapping_sheet_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'prepared',
  snapshot TEXT NOT NULL,
  plan TEXT NOT NULL,
  url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE schedule_sheet_exports ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE schedule_sheet_exports FROM anon, authenticated;

