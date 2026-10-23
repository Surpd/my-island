# ScheduleConstructor — release and operations

Updated 2026-10-22 after production cutover; branch `codex/schedule-consolidation`. This is the current operational document; old one-off reports are privately archived.

## Exact targets and current rollback versions

Verify `.project-identity.json` before external actions.

| Resource | Verified target |
|---|---|
| Supabase | `kmrcbewmxeqqxfauiwyy`, organization `jnvelhucxsrjexigmchx`, eu-west-1 |
| Render | workspace `tea-dae3dcgu01pc73culo30`, service `srv-daeie9m7bikc739usge0`, `https://my-island-api.onrender.com` |
| GitHub | `https://github.com/Surpd/my-island`; Render branch main, auto-deploy OFF |
| GPT Sites | `appgprj_6a9d26108af481918318b34094e1d73b`, owner `706c9176-5c88-4cba-9feb-2673bc87e1c7` |
| Frontend | `https://my-island.mitya971108.chatgpt.site`; existing public audience |

Rollback backend: deploy `dep-dbbgdr61egvs73fv89l0`, commit `7480787f1255d15059d4ab140e3b43680ae6a99e`.
Rollback frontend: Sites **87**, source `45bcc223c510180e539939958707a54c1149fed0`, successful deployment `appgdep_6ad70852dc7c8191a1c08fb947579795`, env revision 1. The Sites mirror in the identity manifest is the publishing source; GitHub push alone is not frontend deployment.

## Existing school store

Read-only Supabase preflight: 149 identities, 520 active memberships, 8 constructor drafts, 3 legacy editor drafts, 17 canonical versions, 6 effective weeks. Tables 041/042 are absent before cutover.

The selected school template is draft `4f900570-3ba7-41c5-bc4d-c68331239980`, revision **45**, **386 authored lessons**; source draft `4dc886ce-db12-4faf-bbc7-e614a2fce673`. Its stored and recomputed SHA256 is `12f22d13cf5b67b3c80a134ffce1e09be4d18537b231c05589ac79bdcbdd9fbe`.
No new permanent database or data import is needed. The local school SQLite contains that source revision and another local draft; both are backed up, not automatically merged.

Production injects the same configured Database into directory/session, drafts, publication and preferences. It does not choose another source from a repository .env. Opening the selected draft does not import, normalize or rewrite it. Local read-only school mode remains separate.

## Production release record

Production release completed 2026-10-22:

- Refreshed and checksum-verified the private PostgreSQL backup before DDL.
- Applied migrations **041_teacher_ui_colors** and **042_constructor_publications** to the approved Supabase. Post-migration read-only verification confirmed the selected template remained rev45, 386 lessons, SHA256 `12f22d13cf5b67b3c80a134ffce1e09be4d18537b231c05589ac79bdcbdd9fbe`; 8 constructor drafts, 3 editor drafts, 17 canonical versions, and 6 effective weeks remain. New UI-settings and publication tables are empty (no constructor publication head exists yet). RLS is enabled and `service_role` access is present.
- Pushed reviewed source to the canonical GitHub `main`. Render remains manual-deploy only; backend deployment `dep-dbd487i9lscs73e61kgg` is live at commit `dc6a0326f91280bcb3c30c90e80d49ef704d0218`.
- Merged required production Render flags while preserving existing secrets and database connection. `/health` returned `status=ok`, `environment=production`, and the expected release SHA.
- Sites runtime environment revision 2 preserves the approved API base and selects school mode. Frontend version **88**, source `1ce648d5bf60599db58e3b06e63bb1df3ae2eddc`, is deployed to the existing public Site.
- Render error-log query and Sites Worker error-log query returned no errors after release. Browser checks confirmed the admin sign-in page and school-source label; admin data verification requires its one-time Telegram code and was not performed without an authenticated session.
- Weekly refresh holds a manual constructor publication with `manual_publication_protected` and exposes pending review; this behavior passed isolated PostgreSQL integration checks. Production has no constructor publication head yet, so the hold will become active when the first manual publication is made. No school template/week was published during cutover.
- Shared color writes are ready but the production color table starts empty. Teachers still receive the stable ID-based fallback palette. Applying the 14 evidence-backed source colors and migrating any browser-local overrides remains an authenticated admin action; neither was run during cutover.

Render MCP confirms the exact service and supports merged env updates, but exposes no environment getter in this session. Existing `DATABASE_URL` and Google/Telegram secrets were preserved by merge; they were not read back through Render. Do not request secret values in chat.

## Private backup and rollback

Git-ignored `.local-test/release-backup/production-before-release.dump`: PostgreSQL CUSTOM, 5,649,550 bytes, SHA256 `2db036fb0a2a77b32ea0ac01c79d2237b7b1b2ea17b76a4175a6e091924df383`.
Schema-only `public-schema.sql`: SHA256 `442d99894342f22dca0495d4de33f6493dafbf5be6489b67afbace3f4235d929`.
The local school SQLite was backed up with SQLite's backup API into the same directory. These backups include private records and session material: never commit or attach them.

`pg_restore --list` validated the archive. Its public schema and data were restored into a separate temporary PostgreSQL 17.11 database; all counts, revision 45, 386 lessons and the template SHA256 matched. That private verification DB was dropped and PostgreSQL stopped. Only a local extensions.gen_random_uuid shim was needed; production already provides it.

Normal rollback disables the consolidated flag, restores the recorded Render commit and Sites version 87 with previous env settings. Leave additive tables in place, preserving settings and publication evidence. No data restore is needed if no publication occurred. Reversing a later publication uses explicit preview/restore, producing a new immutable artifact. Never overwrite newer school edits with an old full backup; emergency DB restore requires a new backup, outage window and separate approved recovery plan.

## Preserved functions and source policy

One Campus Pop constructor preserves Day/Week and all targets, parallel cells/multiple lessons, ParticipantsPicker, inspector, quick entry, events/clubs/courses, lunches/no-lesson, copy/move, keyboard/DnD, undo/redo, diagnostics/ambiguities, recovery, save, merge/409 and export. Existing canonical v2 resolution and membership semantics are reused.

Publication is separate from save. Preview has concrete before/after changes, blockers and deferred records. Confirmation revalidates revision/base/head/directory fingerprint. PostgreSQL uses a serializable transaction, advisory lock and draft row lock; runtime readback occurs before commit. Failed verification rolls back canonical/head/audit rows. Restore requires its own preview/confirm and creates another immutable artifact.

Old UI and tests remain in `archive/schedule-admin/`. Five authenticated writes return 410: PUT/DELETE /api/admin/schedule/draft; POST /api/admin/schedule/draft/publish, /api/admin/schedule/template/confirm, /api/admin/schedule/import-to-draft. The unsafe old inspector is unreachable; restoring it first requires fixing open/close PUT. No fake version history remains. Production /experiments/school/import only opens the selected existing draft.

Google readers, parsers, source snapshots and weekly preview/refresh remain. A manual template/week head blocks automatic source application (`manual_publication_protected`). Sources shows the hold and offers the existing read-only weekly preview. It does not pretend changes were detected before reading Google. Preview compares with the source canonical basis; this is not approval to replace a manual head. For this conservative release transfer needed changes to a draft, then review its publication diff. No automatic reconciliation engine was added.

## Shared teacher colors

`teacher_ui_colors(school_id,teacher_identity_id)` stores base HEX/evidence, nullable manual HEX, revision and actor/time separately from lessons, canonical and teacher mappings. Trusted server selects the school. Fourteen source-backed ID→HEX settings are prepared; ten unconfirmed/absent matches use stable fallback. Applying approved source colors is an explicit settings action, not a migration side effect. Existing manual overrides are preserved.

LocalStorage migration uploads only manual overrides after frozen preview and revision/CAS checks and retains the browser original. Denied storage does not prevent server settings. Cross-device refresh works; realtime broadcasting is not implemented. Private evidence/mapping is retained under `.local-test/release-archive/`.

## Reproducible checks and actual results

Current reusable scripts are in `scripts/schedule-regression/`; generated output is ignored under `.local-test/consolidation/results`.

- Start `python scripts/schedule-regression/local_harness.py` (loopback 8041, secrets stripped, egress blocked). Run `node scripts/schedule-regression/frontend.mjs` in another terminal: it mirrors only actual frontend source files to an isolated local directory and uses localhost:3041, API 127.0.0.1:8041 and school mode. Wait for both servers to be ready. Reset synthetic fixture before each destructive HTTP/React publication cycle.
- `integration_flow.py`, `react-integration.mjs`, `restore-react.mjs`, `colors-react.mjs`, `release-smoke.mjs`, `historical-regression.mjs` cover real HTTP/UI flows. Set MY_ISLAND_CONSOLIDATION_E2E=1 and run Playwright with `scripts/schedule-regression/playwright.config.mjs` for retired-route checks.
- Historical UI requires private `artifacts/template-restore/recovered-draft-payload.json`: **386 records / 427 visual projections**, not membership evidence. Synthetic backend/PG fixtures separately verify memberships.
- `python -m backend.scripts.release_postgres_check` accepts only MY_ISLAND_RELEASE_TEST_DATABASE_URL on localhost:55432/my_island_release_check. Load a schema-only dump into an empty temporary PG, provide local roles/UUID shim, never real school rows. Temporary binaries/data remain ignored.

Passed: genuine PG17.11 migrations 041/042 and idempotence; native UUID/TIME/JSONB; memberships; edit/save/readback; actual HTTP409/current revision; week copy; template/week publication/readback; stale restore CAS and immutable restore; source hold; teacher color FK; render-only export without Google; fault-injected rollback without partial rows. Private backup restore passed.

158 targeted backend tests and 19 JS unit tests passed. Historical React contracts T01, T03–T06, T10–T11, T15 passed (14 scenarios). Real React save/reload/409 merge/publication/restore and independent browser colors passed; 3 Playwright legacy-retirement tests passed. Source hold/preview and shell passed at 1920×1080, 1366×768 and 390×844. Typecheck, lint and explicit production-API build passed.

Real PG testing found a release bug: HTTP409 conflict.current contained native datetime/UUID, producing 500. JSON-safe encoding fixed it without changing merge semantics. Production directory injection and browser-session forwarding were also fixed.

## Cleanup and remaining limits

Obsolete design prototypes/galleries/screenshots and duplicate frontend packages were removed. A stale frontend test mirror had copied runtime/data folders: those were removed and the mirror refreshed from actual release source. Useful regression harnesses were versioned; old reports/one-off repair utilities/color evidence privately archived. Canonical snapshots, parsers, useful tests, historical data, legacy archive and Git history are preserved.

Production smoke is pending cutover. The active Render env getter is unavailable through MCP. A production consolidated startup now rejects any database outside the approved Supabase project and conflicting local/read-only flags; agent-entered updates merge only requested keys. Validate authenticated readback before releasing the frontend. Live Google export has not been tested by writing school data; its existing dedicated test-target restriction remains. Ten teacher colors remain unconfirmed. Viewport/context simulations are not physical phone/keyboard or two-physical-device testing.
