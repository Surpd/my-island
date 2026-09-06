# Integration contract

## Google OAuth

The technical account is `antischool.island@gmail.com`. A local read-only smoke runner performs one consent, callback/code exchange, account verification, token persistence under ignored `data/`, and Sheets/Classroom reads. Normal runs reuse the stored refresh token; `--reauthorize` is the explicit escape hatch. It does not write Google or database data.

Create a Google Cloud OAuth client of type **Web application** when ready. Configure this exact local redirect URI:

`http://localhost:8000/api/integrations/google/callback`

The first read-only consent request should use only:

- `https://www.googleapis.com/auth/spreadsheets.readonly` for the schedule spreadsheet;
- `https://www.googleapis.com/auth/classroom.courses.readonly`;
- `https://www.googleapis.com/auth/classroom.coursework.students.readonly`;
- `https://www.googleapis.com/auth/classroom.student-submissions.students.readonly`.

The Classroom scopes are for the teacher account’s courses, coursework and student submissions. `openid` and `email` are included only to verify the authorized technical account. Do not replace this with a service account. Run `python -m backend.scripts.google_oauth_smoke` for a fresh consent flow, or add `--reuse` to use the ignored local refresh token. Google calls remain outside student request handlers; a future importer will normalize and validate the result before writing the application database.

## Runtime env checklist

Fill now for local Telegram verification: `DEV_AUTH_ENABLED=true` is safe only with `APP_ENV=development`; `TELEGRAM_BOT_TOKEN` is needed for signed Telegram `initData` tests and stays server-only.

Fill for real Postgres after the migration is approved/applied: `DATABASE_URL`, copied from Supabase’s Connect dialog. The backend uses it server-side via psycopg and does not run migrations on startup.

Fill later for hosted deployment: `CORS_ORIGINS`, `BACKEND_PUBLIC_URL`, `FRONTEND_PUBLIC_URL`. No Render environment variables have been set by this pass.
