# My Island — Admin Console Implementation — 2026-09-08

## Scope

Admin Console вынесена в отдельную browser-first surface внутри того же repo/build. Student и Teacher Mini App entry point не переписывались и продолжают жить в `app/page.tsx`.

## Architecture before / after

Before: Admin был состоянием `section === 'admin'` внутри общего Telegram-oriented page и грузил все admin endpoint’ы одним initial `Promise.allSettled`.

After: добавлен отдельный route [app/admin/page.tsx](../app/admin/page.tsx) с URL navigation и route-level loading:

- `/admin`
- `/admin/people`
- `/admin/people/:identityId`
- `/admin/groups`
- `/admin/groups/:groupId`
- `/admin/sources`
- `/admin/sources/:sourceId`
- `/admin/reconciliation`
- `/admin/schedule`
- `/admin/journals`
- `/admin/audit`
- `/admin/system`

Admin shell имеет постоянный desktop sidebar, top bar, breadcrumbs, system status, admin identity, logout, readable tables, sticky table headers, explicit loading/error/empty states и responsive fallback для узкого окна. Bottom Telegram tabs в Admin route не используются.

## Browser auth

Добавлен двухтранспортный auth flow:

1. Telegram-authenticated admin вызывает `POST /api/admin/auth/challenge`.
2. Сервер создаёт одноразовый token с TTL.
3. Browser отправляет token на `POST /api/admin/auth/exchange`.
4. Сервер проверяет single-use/TTL/admin role и устанавливает HttpOnly cookie `my_island_admin_session`.
5. Каждый admin request повторно проверяет server-side admin role.
6. `POST /api/admin/auth/logout` отзывает session и пишет audit event.

Cookie: HttpOnly, `SameSite=Lax`, `Secure` в production, TTL семь дней. Dev `X-Dev-Auth: admin:1` остаётся доступен только при `APP_ENV != production` и `DEV_AUTH_ENABLED=true`; query `?role=admin` не является production auth.

Для production Postgres добавлена migration [backend/migrations/022_admin_browser_sessions.sql](../backend/migrations/022_admin_browser_sessions.sql). Она должна быть применена существующим production migration process; приложение намеренно не мутирует Postgres schema на startup.

## API surface

Добавлены:

- `GET /api/admin/auth/session`
- `POST /api/admin/auth/challenge`
- `POST /api/admin/auth/exchange`
- `POST /api/admin/auth/logout`
- `GET /api/admin/system`
- `GET /api/admin/sources`
- `GET /api/admin/sources/:sourceId`
- `GET /api/admin/groups/:groupId`
- `POST /api/admin/reconciliation/student-memberships/dry-run`
- `GET /api/admin/reconciliation/student-memberships/latest`
- `GET /api/admin/reconciliation/student-memberships/runs/:runId`
- `POST /api/admin/reconciliation/student-memberships/runs/:runId/review`
- `POST /api/admin/reconciliation/student-memberships/apply?run_id=:runId`

Apply не принимает arbitrary plan JSON: client передаёт только server-generated run ID. Перед apply сервер требует reviewed status, повторно читает authoritative source, сравнивает summary с reviewed run, запускает существующие safety checks, использует transactional apply service и возвращает readback. Local SQLite apply блокируется существующим production guard; production apply не запускался в рамках UI pass.

## School Data pages

Sources health использует `school_sources`, latest sync run, last valid snapshot, fingerprint, record count, unresolved issues и pending/conflict candidate changes. Source detail показывает snapshot history, sync runs, records, issues и mappings в ограниченном human-readable drill-down, без raw SQL dump.

People использует существующий canonical people projection и narrow person endpoint. В detail видны identity, account/role state, base class, instructional memberships, exam/profile facts, memberships/provenance, teacher assignments и issue summary.

Groups получили table view и canonical detail. Stable roster отделён от schedule audience; detail показывает provenance, active students и teacher assignments.

## Reconciliation workflow

UI отображает server-generated diff с `KEEP`, `CREATE`, `DEACTIVATE`, `PROTECTED`, `UNRESOLVED`, `WRONG_GROUP`, `STALE`, issues и invariants. Последовательность:

`dry-run → review → server revalidation → explicit apply → readback/audit`

Known protected cases не превращаются в обычную ошибку; они видны как отдельный `PROTECTED` state. Student membership service остаётся единственным write authority для этого workflow; schedule, teacher assignments и selection facts не материализуются через него.

## Schedule / Journals / Classroom

Schedule route показывает sync history и parse diagnostics, подчёркивая различие между stable membership, lesson audience и computed audience.

Journals route показывает journal source state и Classroom sync history. Official journal snapshot и Classroom metadata/grades не объединяются в один источник истины.

## Migration parity

`Database.initialize()` получил additive local SQLite compatibility pass для старых dev DB: отсутствующие новые поля `groups`, `memberships`, `schedule_entries`, `schedule_syncs`, identities и admin session tables добавляются без destructive reset. `GET /api/admin/system` теперь возвращает schema checks и data counts. Production Postgres остаётся migration-driven.

## Safety decisions

- No physical deletes.
- No fuzzy identity auto-merge.
- No membership creation from schedule audience.
- No selection fact → instructional membership conversion.
- No teacher assignment mutation from reconciliation UI.
- No production apply was executed during implementation verification.
- Secrets are not shown by System page.
- Existing Student/Teacher page and Telegram auth path were left intact.

## Verification

- `npx tsc --noEmit` — pass.
- `npm run lint` — pass.
- `npm run build` — pass; routes `/` and `/admin` classified by Vinext.
- `python -m unittest discover -s backend/tests -q` — 79 tests, pass.
- Local FastAPI smoke with `X-Dev-Auth: admin:1`: core Admin GET endpoints returned 200 after additive schema parity.
- Browser session smoke: challenge, single-use exchange, cookie-authenticated Admin GET, logout, reused code rejected with 401.

## Remaining limitations

- The legacy Telegram Admin surface now exposes `Код для браузера`, which calls the challenge endpoint so an admin can copy the one-time code into the browser. A polished Telegram bot/deep-link delivery UX remains a follow-up because this repository has no bot command handler.
- Local checked-in DB has no registered School Data sources, so Sources and reconciliation show honest empty/not-run states until source sync data is present.
- Visual browser screenshot verification was blocked by the local Vinext server binding/desktop browser connectivity in this environment; build and HTTP smoke passed, and the route is implemented with explicit responsive styles.
- Existing legacy Admin implementation remains in `app/page.tsx` for the old role-switch path. The new `/admin` route is the browser-first surface; removing legacy Admin should be a separate migration after users move to the new URL.
