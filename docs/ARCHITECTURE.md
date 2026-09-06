# Architecture

## Boundaries

`handlers → services → database` is the server rule. Handlers parse HTTP and auth headers, services enforce product invariants, and database owns persistence/transactions.

Telegram Mini App authentication is server-side. `initData` is checked with the bot token; a Telegram id sent only in JSON is never accepted. Local development uses an explicit `DEV_AUTH_ENABLED=true` plus `X-Dev-Auth: student:1`; the resolver rejects this path when `APP_ENV=production`.

Runtime configuration is centralized in `backend/config.py`; browser code reads only the public `VITE_API_BASE_URL` value from the build environment. `DATABASE_URL` selects the Postgres/Supabase connection for the same repository methods; when it is present, startup does not mutate schema and migrations remain an explicit operator action. Supabase Auth is not used: Data API roles `anon` and `authenticated` have no table grants or policies. The backend verifies Telegram `initData`, uses a privileged server-side database connection, and scopes personal queries through its internal user-to-identity mapping.

Google boundaries are split into OAuth URL preparation, deterministic Sheets matrix normalization, and the existing schedule validation/atomic replacement service. The intended account is `antischool.island@gmail.com`; a normal OAuth web application client is required, not a service account. Classroom reads use courses, student coursework, and student-submissions read scopes; official grades remain a separate table/domain.

## Identity approval

The intended sequence is `Telegram user → role → canonical identity → identity_claim(pending) → admin review → app_user.identity_id`. Before approval, personal endpoints must return no school-personal data. A second account claiming an already-linked canonical identity becomes `identity_conflict` instead of being silently linked.

## Groups and schedule

`groups` is the common audience object for students, teachers, schedule, Classroom courses, and future official sources. Memberships include source and active/validity fields so an official import cannot erase an admin override.

The schedule importer validates all rows and only then performs a replacement transaction. A failed parse records an error and leaves the last valid entries intact. The Google Sheet adapter is intentionally not enabled until read access/OAuth configuration is confirmed.

## Integrations

- Google Sheets: server-side read boundary and deterministic parser are ready; the first local grant is still required before live reads.
- Google Classroom: course/courseWork/studentSubmission reads use the same OAuth token and no service-account impersonation.
- Supabase: `001_initial.sql` is the approved schema contract; RLS is enabled as a deny-by-default boundary for Data API roles, while application authorization and cross-student isolation are enforced in the backend repository layer.
- Render: `render.yaml` is a non-applied API Blueprint; deployment and service env vars are intentionally out of scope for this pass.
