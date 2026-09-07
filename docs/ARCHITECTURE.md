# Architecture

## Boundaries

`handlers → services → database` is the server rule. Handlers parse HTTP and auth headers, services enforce product invariants, and database owns persistence/transactions.

Telegram Mini App authentication is server-side. `initData` is checked with the bot token; a Telegram id sent only in JSON is never accepted. Local development uses an explicit `DEV_AUTH_ENABLED=true` plus `X-Dev-Auth: student:1`; the resolver rejects this path when `APP_ENV=production`.

Runtime configuration is centralized in `backend/config.py`; browser code reads only the public `VITE_API_BASE_URL` value from the build environment. `DATABASE_URL` selects the Postgres/Supabase connection for the same repository methods; when it is present, startup does not mutate schema and migrations remain an explicit operator action. Supabase Auth is not used: Data API roles `anon` and `authenticated` have no table grants or policies. The backend verifies Telegram `initData`, uses a privileged server-side database connection, and scopes personal queries through its internal user-to-identity mapping.

Google boundaries are split into OAuth URL preparation, deterministic Sheets matrix normalization, and background snapshot replacement. The intended account is `antischool.island@gmail.com`; a normal OAuth web application client is required, not a service account. Classroom reads use courses, student coursework, and student-submissions read scopes. Current 2026/27 journals use their own normalized sources/assessments/results domain and never write back to Google.

## Identity approval

The intended sequence is `Telegram user → role → canonical identity → identity_claim(pending) → admin review → app_user.identity_id`. Before approval, personal endpoints must return no school-personal data. A second account claiming an already-linked canonical identity becomes `identity_conflict` instead of being silently linked.

Journal roster is deliberately outside that approval sequence. `journal_students` is the normalized source roster and can represent a student with no Telegram/app account; `identity_id` is an optional link used only when a unique canonical match or later admin action is available. Journal results point to `journal_student_id`, so missing app identity never removes a student from teacher journal/history/analytics views.

## Groups and schedule

`groups` is the common audience object for students, teachers, schedule, Classroom courses, announcements, and journals. Imported memberships remain immutable provenance; explicit include/exclude overrides are evaluated above them. Teacher subject assignments and homeroom assignments are independent records, so `teacher`, `admin`, and homeroom capability do not collapse into one role.

Journal source markers are mapped independently across `base_class_name`, `subject_subgroup`, `classroom_course_id`, and `exam_track`. An optional `group_id` is only an explicit internal app link; it must not be used to encode all of those dimensions in one group name. Current Grade 9 Mathematics mapping is `9-1 → A`, `9-2 → B`, `9-3 → C`; base class, Classroom course, and exam track remain unset until their own authoritative assignments are known.

The schedule importer deterministically establishes a class block before parsing lesson cells. A non-class header terminates the block; uncertain ownership stays unresolved, and adjacent-class cells never enter parser context or diagnostics. Validation finishes before transactional replacement, so a failed parse leaves the last valid snapshot intact. Semantic LLM enrichment, when added, belongs only in background sync and must receive the already-bounded block.

## Integrations

- Google Sheets: server-side read boundary and deterministic parser are ready; the first local grant is still required before live reads.
- Google Classroom: course/courseWork/studentSubmission reads use the same OAuth token and no service-account impersonation.
- Supabase/Postgres: migrations `001`–`011` are the schema contract. Migrations `010`–`011` add explicit school-group dimensions/provenance, membership source references, and a recoverable archive boundary for disposable legacy schedule snapshots. RLS plus revoked `anon`/`authenticated` grants form a deny-by-default Data API boundary, while application authorization and cross-student isolation are enforced in the backend repository layer.
- Render: `render.yaml` is the API Blueprint. Schema migrations remain an explicit operator step before deploying code that uses them.
