-- Apply only after successful manual and repeated production imports.
-- The request secret must already be stored in Vault and configured on Render.
begin;
create extension if not exists pg_cron;
create extension if not exists pg_net;
do $$
begin
  if not exists (select 1 from vault.decrypted_secrets where name = 'my_island_journal_cron_secret') then
    raise exception 'Journal Cron secret is not provisioned';
  end if;
  if not exists (select 1 from public.journal_sources
    where spreadsheet_id = '1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I'
      and auto_sync_enabled is true) then
    raise exception 'Validated journal has not been enabled for automatic sync';
  end if;
end $$;
-- Supabase Cron uses UTC: 05:00..19:00 UTC is 08:00..22:00 Moscow.
select cron.schedule('my-island-journal-hourly', '0 5-19 * * *', $job$
  select net.http_post(
    url := 'https://my-island-api.onrender.com/api/admin/journals/cron',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Journal-Cron-Secret', (select decrypted_secret from vault.decrypted_secrets where name = 'my_island_journal_cron_secret')
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  );
$job$);
commit;
