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
2. Generate a 256-bit random base64url value in a trusted secret manager. Store
   the original only in that manager or approved network Vault, and configure
   `MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256` on the approved backend with its
   lowercase SHA-256 fingerprint. Treat the verifier as sensitive configuration
   too. Never paste either, or the returned session, into a chat, repository,
   URL, screenshot, issue, trace, command arguments, or log. Do not enable
   request/response header capture on these routes.
3. Set the server-only `MY_ISLAND_CODEX_DIAGNOSTICS=1` flag. Missing or invalid
   configuration disables access. This is not a Vite environment variable.
4. To revoke one session, use its opaque ID from the restricted audit records
   and the operator-side session revoke operation (`revoke(database, id)`).
   Browser logout also revokes server-side. Removing the flag or the verifier
   disables access on all refreshed server workers; rotating the verifier
   invalidates all existing tokens. Keep the feature off or use a new key
   for re-enablement after suspected compromise.
5. Treat audit entries and session data as restricted operator records. The
   audit logs only event type, opaque session ID and view category. No access
   key, session bearer, user identities, search text, or records are logged.

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
cannot reveal the stored value for pasting into a browser input. Browser login
requires a trusted, separately authorized credential holder; if the current
Codex environment lacks supported Vault delivery, do not work around it by
sending secrets to the agent. No live Vault/browser provisioning was part of
this implementation.

## Boundary for future changes

The server currently issues only `diagnostic.read`. Read views dispatch through
an explicit server allowlist to hand-selected outputs, not the legacy routes;
GET is not automatically safe. There is no wildcard permission, no conversion
to a human role, and no business write endpoint. A future write requires its own
scope, endpoint, allowlisted parameters, and additional approval/audit tests.
Do not forward diagnostic authorization on other domains or browser APIs.
