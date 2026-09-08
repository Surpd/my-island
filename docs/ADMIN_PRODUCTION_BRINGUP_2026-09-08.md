# My Island — Admin Production Bring-up — 2026-09-08

## Executive result

Production bring-up подготовлен и локально проверен, но production migration/deploy/browser QA не могут быть честно отмечены как выполненные: доступный публичный target не соответствует этому репозиторию, а Render deploy/migration credentials или workflow в текущем окружении отсутствуют.

Production reconciliation apply не выполнялся.

## 1. Migration

Status: **blocked — not applied to an external production database**.

Migration inspected: [backend/migrations/022_admin_browser_sessions.sql](../backend/migrations/022_admin_browser_sessions.sql).

It is additive: creates browser challenge/session tables, reconciliation run metadata table and indexes. No `DROP`, destructive rewrite, membership mutation or canonical School Data rewrite is present.

Local SQLite parity was applied additively by `Database.initialize()`. Local schema health reports `current`.

The repository intentionally does not auto-apply Postgres migrations at startup. A production migration runner or database operator is required.

## 2. Deployment

Status: **not deployed from this environment**.

Observed deployment configuration:

- `render.yaml` describes `my-island-api` but has `autoDeployTrigger: off`.
- No `.github/workflows` deployment workflow exists.
- Render CLI is unavailable.
- GitHub CLI is unavailable.
- No Render deploy hook or deploy credential is present in the repository environment.

The worktree contains uncommitted implementation and prior reconciliation files. No commit, push or deploy was performed to avoid publishing a mixed/unreviewed worktree.

## 3. Public target verification

The known public target checked read-only was not this My Island build:

- `https://islandquiz.online/admin` rendered an IslandQuiz application with “Доступ запрещён”.
- `https://api.islandquiz.online/health` returned 404.
- `https://api.islandquiz.online/api/admin/auth/session` returned 404.

Therefore no production Admin URL for this repository could be confirmed.

## 4. Auth verification

Local end-to-end smoke passed:

`admin challenge → one-time exchange → HttpOnly cookie session → Admin API → logout → revoked session`.

Observed results:

- challenge: 200;
- exchange: 200;
- authenticated session: 200;
- authenticated overview: 200;
- logout: 200;
- reused challenge: 401.

Static security checks:

- challenge token is random and stored hashed;
- challenge has five-minute TTL and single-use consumption;
- browser session is stored hashed;
- session TTL is seven days;
- production cookie is HttpOnly, Secure and SameSite=Lax;
- admin role is checked server-side on every request;
- logout revokes the session;
- dev auth remains guarded by non-production environment checks.

No production cookie flag verification was possible without the correct production target.

## 5. Local browser/UI QA

The new route is implemented in [app/admin/page.tsx](../app/admin/page.tsx) and the build contains `/admin`.

The local HTTP/API smoke passed, but the desktop browser could not connect to the local Vinext server because the environment exposed the dev listener on an inaccessible loopback binding. Screenshot-level visual QA therefore remains blocked locally.

The public browser target was inspected, but it is a different application and was not used as My Island QA evidence.

## 6. Local data/API QA

With local SQLite and dev admin auth:

- `/api/admin/system` — 200;
- `/api/admin/overview` — 200;
- `/api/admin/people` — 200;
- `/api/admin/people/:id` — 200;
- `/api/admin/groups` — 200;
- `/api/admin/groups/:id` — 200;
- `/api/admin/sources` — 200;
- `/api/admin/reconciliation/student-memberships/latest` — 200;
- `/api/admin/schedule/syncs` — 200;
- `/api/admin/journals` — 200;
- `/api/admin/audit` — 200.

The checked-in local DB has no registered School Data source rows, so Sources and reconciliation correctly show empty/not-run states rather than fabricated production values.

Student/Teacher regression smoke:

- `/api/student/today` — 200;
- `/api/teacher/home` — 200.

## 7. Reconciliation QA

The UI and API support read-only dry-run, server-generated run storage, explicit review and guarded apply. No dry-run was executed against production because the correct production API target was not identified and the local environment lacks the authoritative Google token.

The apply endpoint remains guarded by reviewed run state, source revalidation, production database requirements and the existing transactional apply service. No production apply was attempted.

## 8. Legacy Admin decision

Legacy Admin remains in [app/page.tsx](../app/page.tsx) because Student/Teacher share that entry point and production deployment of the new browser surface has not yet been proven. A browser-code button was added to the legacy Admin to bridge the current auth flow.

Removal should happen only after the correct production target is deployed and browser smoke passes.

## 9. Tests

- `npx tsc --noEmit` — pass.
- `npm run lint` — pass.
- `npm run build` — pass; `/` and `/admin` are included.
- `python -m unittest discover -s backend/tests -q` — 79 tests, pass.
- Local Admin API smoke — pass.
- Local Student/Teacher API smoke — pass.
- `git diff --check` — pass; only normal Windows line-ending warnings were reported.

## 10. Files changed in this bring-up

- [app/admin/page.tsx](../app/admin/page.tsx)
- [app/globals.css](../app/globals.css)
- [app/page.tsx](../app/page.tsx)
- [backend/database.py](../backend/database.py)
- [backend/handlers/api.py](../backend/handlers/api.py)
- [backend/main.py](../backend/main.py)
- [backend/migrations/022_admin_browser_sessions.sql](../backend/migrations/022_admin_browser_sessions.sql)
- [backend/tests/test_admin_console.py](../backend/tests/test_admin_console.py)

Existing prior reconciliation files remain uncommitted and were not deleted or reset.

## Single external blocker

Provide the actual My Island production deployment target and authorized migration/deploy path (Render service/deploy hook or equivalent, plus the correct production URL). Without that, applying SQL or pushing the current mixed worktree would risk changing the wrong external system.
