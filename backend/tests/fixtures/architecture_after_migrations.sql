DO $$
BEGIN
  IF (SELECT count(*) FROM identities WHERE display_name LIKE 'Fixture %' AND revision=1) <> 2
    OR (SELECT count(*) FROM groups WHERE name='Fixture 9A' AND revision=1) <> 1
    OR (SELECT count(*) FROM memberships WHERE id='10000000-0000-0000-0000-000000000005') <> 1
    OR (SELECT count(*) FROM journal_sources WHERE spreadsheet_id='synthetic-book') <> 1
    OR (SELECT count(*) FROM users WHERE telegram_user_id=60000001 AND role='admin') <> 1 THEN
    RAISE EXCEPTION 'Preexisting school fixture lost or mutated';
  END IF;
  IF EXISTS (SELECT 1 FROM directory_edit_history)
    OR EXISTS (SELECT 1 FROM schedule_publication_receipts)
    OR EXISTS (SELECT 1 FROM journal_cell_events)
    OR EXISTS (SELECT 1 FROM admin_widget_layouts) THEN
    RAISE EXCEPTION 'Additive migrations created phantom activity';
  END IF;
  IF EXISTS (
      SELECT 1 FROM pg_class JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace
      WHERE nspname='public' AND relname IN (
          'directory_edit_history','schedule_publication_receipts','published_week_sheet_bindings',
          'published_week_sheet_jobs','journal_cell_events','admin_widget_layouts'
      ) AND (NOT relrowsecurity OR has_table_privilege('anon',pg_class.oid,'SELECT')
          OR has_table_privilege('authenticated',pg_class.oid,'SELECT'))
  ) THEN
    RAISE EXCEPTION 'Direct access to new tables was granted to school client roles';
  END IF;
END $$;
