# Hourly journal refresh setup

The API and admin UI support scheduled refreshes, but the job stays disabled until
the approved Supabase project has this Cron job and Render has the matching secret.
Run this only for the `kmrcbewmxeqqxfauiwyy` Supabase project after Google OAuth is
reconnected for `antischool.island@gmail.com`.

1. Generate a random secret locally. Set it as `JOURNAL_CRON_SECRET` on the approved
   Render service `srv-daeie9m7bikc739usge0` and redeploy that backend.
2. In the Supabase SQL Editor for the approved project, store the same value in Vault
   (replace the placeholder; do not commit it):

   ```sql
   select vault.create_secret('REPLACE_WITH_THE_SAME_RANDOM_SECRET', 'my_island_journal_cron_secret');
   ```

3. Schedule the protected endpoint once per UTC hour from 05:00 through 19:00,
   corresponding to 08:00 through 22:00 Moscow time. Re-running this setup replaces
   the named job rather than creating duplicates:

   ```sql
   select cron.unschedule(jobid)
   from cron.job
   where jobname = 'my-island-journal-hourly';

   select cron.schedule(
     'my-island-journal-hourly',
     '0 5-19 * * *',
     $$
     select net.http_post(
       url := 'https://my-island-api.onrender.com/api/admin/journals/cron',
       headers := jsonb_build_object(
         'Content-Type', 'application/json',
         'X-Journal-Cron-Secret', (
           select decrypted_secret
           from vault.decrypted_secrets
           where name = 'my_island_journal_cron_secret'
           limit 1
         )
       ),
       body := '{}'::jsonb
     );
     $$
   );
   ```

The admin API enables per-spreadsheet automation only after it sees this active job.
After setup, reconnect or refresh the journal through the admin panel and turn on
the hourly toggle. Inspect `cron.job_run_details` and the journal sync history when
checking the first scheduled run.

## Release rollback

If the release must be rolled back, redeploy the prior Render deployment and the
prior Sites version. Leave migration 043 applied: it is an additive compatibility
migration and the previous application can ignore its extra columns and tables.
Do not drop the new tables or columns after journal imports have begun. Before the
release, the approved database had 111 journal result rows and zero duplicate
student/assessment pairs. Migration 043 refuses to run if such duplicates appear
and does not delete or rewrite result data. A database rollback is unnecessary for
an application rollback and could discard real import history.
