# Integration contract

## Google OAuth

The technical account is `antischool.island@gmail.com`. A local read-only smoke runner performs one consent, callback/code exchange, account verification, token persistence under ignored `data/`, and Sheets/Classroom reads. Normal runs reuse the stored refresh token; `--reauthorize` is the explicit escape hatch. It does not write Google or database data.

Create a Google Cloud OAuth client of type **Web application** when ready. Configure this exact local redirect URI:

`http://localhost:8000/api/integrations/google/callback`

The first read-only consent request should use only:

- `https://www.googleapis.com/auth/spreadsheets.readonly` for schedule and current 2026/27 journal spreadsheets;
- `https://www.googleapis.com/auth/classroom.courses.readonly`;
- `https://www.googleapis.com/auth/classroom.coursework.students.readonly`;
- `https://www.googleapis.com/auth/classroom.student-submissions.students.readonly`.

The Classroom scopes are for the teacher account’s courses, coursework and student submissions. `openid` and `email` are included only to verify the authorized technical account. Do not replace this with a service account. Run `python -m backend.scripts.google_oauth_smoke` for a fresh consent flow, or add `--reuse` to use the ignored local refresh token. Google calls remain outside Student/Teacher request handlers; schedule, Classroom, and journal importers normalize a complete snapshot before transactional database replacement. No Google writes are implemented.

Current journal sync uses the separate 2026/27 grade spreadsheets recorded in the project context. Grade 9 Mathematics is normalized into `journal_sources`, `journal_assessments`, `journal_students`, and `journal_results`; the roster is usable even when `identity_id` is null. Its confirmed canonical instructional groups are A/B/C. Legacy `9-1`/`9-2`/`9-3` source markers normalize to A/B/C in the bounded compatibility path; base class, Classroom course, and exam track remain separate optional mapping fields.

School Directory sources use the shared `school_sources` registry and immutable snapshots/records. Bootstrap stages candidates with source coordinates/evidence before canonical apply. Incremental sync is fingerprint/diff-first; unchanged records bypass semantic work, and conflicting or structurally uncertain records remain unresolved. Groq is the intended semantic provider, with the model selected by configuration after a representative benchmark. Neither Groq nor a Google adapter may create canonical people/groups directly.

## Runtime env checklist

Fill now for local Telegram verification: `DEV_AUTH_ENABLED=true` is safe only with `APP_ENV=development`; `TELEGRAM_BOT_TOKEN` is needed for signed Telegram `initData` tests and stays server-only.

Fill for real Postgres after the migration is approved/applied: `DATABASE_URL`, copied from Supabase’s Connect dialog. The backend uses it server-side via psycopg and does not run migrations on startup.

Hosted deployment also requires correct `CORS_ORIGINS`, `BACKEND_PUBLIC_URL`, and `FRONTEND_PUBLIC_URL`. Apply database migrations before rolling out backend code that queries the new schema.
