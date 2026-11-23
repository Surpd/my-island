# Optional Codex diagnostics access

This is a separate principal and credential, not a human administrator account,
not a second site, and not a Telegram-role switch. The existing owner login
remains the same. For an interactive browser, visit `/admin/diagnostics` on the
existing frontend. Successful authentication opens the same admin navigation and
components with a prominent read-only indicator, backed by dedicated projections.
The session expires after 15 minutes with no auto-renewal in the browser.

## Current scope and limits

| Existing section | Diagnostic view |
| --- | --- |
| Today | Aggregate students/teachers/group counts, schedule publication status, available draft metadata. |
| Schedule | Existing constructor read surface, safe class captions, dates, times, known subjects, generic lesson names and warning flags. No external meeting URLs, pupil rosters, teacher assignments, archived drafts, publication history, export links, live audience prediction, or original conflict text. |
| Pupils and groups | Capped class/group inventory, no pupils, names, memberships, comments, or membership search. |
| Teachers | Capped pseudonymized inventory. Assignments, names, contacts, and personal schedules are not visible. |
| Journals | Bounded grade/term/source status and safely known subjects. Workbook identifiers are pseudonymous; no grade ledger, assessments, names, source links, mappings, or detailed import errors. |
| Preview | Existing control layout only. Cannot select or impersonate an actual teacher or pupil or start a preview session. |
| Settings | Backend/database indicators; no account details, live Google verification, reconnect, or OAuth. |

A blank table can be an intentional privacy boundary, not absence of underlying
school data. Diagnose real schedule layout and navigation here. Use the isolated
synthetic workspace for cases requiring full student/teacher or journal detail.

## Credential and operator workflow

1. Apply additive Postgres migration 052 through the approved release procedure.
   Leave the feature disabled until the environment and credential are approved.
2. Keep the configured dedicated key unchanged in Render and the approved
   host-scoped network Vault. Use the exact same value in both. The backend
   hashes it internally without trimming or complexity rules. Empty keys and
   keys with spaces, Unicode or control characters are not valid HTTP-header
   credentials. Use a randomly generated dedicated key when provisioning a new
   value, and never reuse any account password.
   The existing `MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256` option still works
   by itself. If both nonempty configurations are supplied, they must match
   or access is disabled. An explicitly blank or malformed raw secret also
   disables access. Do not store either form in a public frontend variable.
   Never paste a key, verifier or session into chat, a repository, a URL,
   screenshots, command arguments, or logs. Disable request/response header
   capture on these routes.
3. Set the server-only `MY_ISLAND_CODEX_DIAGNOSTICS=1` flag. Missing or invalid
   configuration disables access. This is not a Vite environment variable.
4. To revoke one session, use its opaque ID from the restricted audit records
   and the operator-side session revoke operation (`revoke(database, id)`).
   Browser logout also revokes server-side. Removing the flag or the verifier
   disables access on all refreshed server workers; rotating the key or verifier
   invalidates all existing tokens. Changing only the storage format while
   using the same key preserves existing sessions. Keep the feature off or use a new key
   for re-enablement after suspected compromise.
5. Treat audit entries and session data as restricted operator records. The
   audit logs only event type, opaque session ID and view category. No access
   key, session bearer, user identities, search text, or records are logged.

Diagnostic login admits five exchanges in a shared 15-minute window. Both
successful and failed attempts count; if the budget is exhausted, new
diagnostic sessions temporarily receive HTTP 429. Existing diagnostic sessions
and normal human-admin access are unaffected. The rate limiter stores no
client addresses or keys and ignores client-supplied proxy headers.

### Automated access (not browser credential injection)

A supported OpenAI-hosted environment with restricted network access and a
host-scoped Vault credential can send a GET/POST HTTP flow without exposing the
long-lived key to the sandbox. Attach the Vault to the authorized environment
and allow the exact approved backend hostname for both egress and secret
substitution. The exchange is `POST /api/diagnostics/exchange` with the Vault
placeholder unchanged as the value of `X-My-Island-Diagnostic-Key`. No query or
body is needed. Hold the returned short-lived `token` in memory, send
`Authorization: Bearer …` to `GET /api/diagnostics/view/…`, and end with
`POST /api/diagnostics/logout`. Do not print the exchange response or bearer.
Require HTTPS and reject redirects when supplying the long-lived key.

The [OpenAI Vault documentation](https://developers.openai.com/api/docs/guides/agents-api/tools/vaults)
describes outbound header substitution for supported hosted sessions. This
cannot reveal the stored value for pasting into a browser input. For an
authorized Playwright process sharing the network-enabled environment, the
process can exchange using the network credential without exposing that key
to Chromium. It may pass only the returned 15-minute bearer directly to a
same-origin, single-tab bootstrap in memory, then load the existing `/admin`
UI. Restrict the bootstrap to the exact approved Sites origin and main frame;
do not place either credential in code, URLs, traces, snapshots, screenshots,
tool output or inherited browser contexts. Use a fresh browser context and
revoke the session when finished. Diagnostic requests send no cookies; Sites
and Render are different origins, so third-party cookie behavior is irrelevant.

An unrelated hosted browser or the hidden browser tool cannot be assumed to
share the network Vault or that Playwright context. This implementation does
not assert that such a browser can sign in unattended. Its supported fallback
is owner-driven entry in the dedicated password form using the trusted
credential manager, not posting a key to Codex. Independently confirm both the
allowed Render HTTPS hostname and Sites CORS origin before activating. No
live Vault/browser provisioning was part of this implementation.

### Verification

Use the usual E2E runner with an owned synthetic workspace:
`MY_ISLAND_E2E_VERIFY_DIAGNOSTICS=1 node scripts/run-e2e.mjs e2e/codex-diagnostic-login.spec.ts`.
The runner generates a disposable key in memory, deliberately strips any
production diagnostic configuration, and gives its isolated API only the
synthetic verifier. The real-transport scenario checks browser CORS, legacy
authority separation, write/read denials, logout revocation and human auth
parity. Nothing is written to a school deployment.

## Boundary for future changes

The server currently issues only `diagnostic.read`. Read views dispatch through
an explicit server allowlist to hand-selected outputs, not the legacy routes;
GET is not automatically safe. There is no wildcard permission, no conversion
to a human role, and no business write endpoint. A future write requires its own
scope, endpoint, allowlisted parameters, and additional approval/audit tests.
Do not forward diagnostic authorization on other domains or browser APIs.
