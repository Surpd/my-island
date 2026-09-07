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

`groups` is the canonical cohort object. Student memberships, teacher assignments, homeroom assignments, schedule lesson audiences, and external source mappings are independent relationships. Imported memberships retain provenance and validity periods; explicit include/exclude overrides are evaluated above them. Teacher access is granted only by a current teacher/homeroom assignment, never by a teacher-shaped membership.

Journal source markers are mapped independently across `base_class_name`, `subject_subgroup`, `classroom_course_id`, and `exam_track`. An optional `group_id` is only an explicit internal app link; it must not be used to encode all of those dimensions in one group name. The confirmed Grade 9 Mathematics structure is three canonical instructional groups: `A`, `B`, and `C`. Old `9-1`/`9-2`/`9-3` markers are bounded source aliases only, never runtime group identifiers. Base classes, Classroom courses, and exam tracks remain separate dimensions and are linked only by explicit evidence.

The schedule importer deterministically establishes a class block before parsing lesson cells. A non-class header terminates the block; uncertain ownership stays unresolved, and adjacent-class cells never enter parser context or diagnostics. Validation finishes before transactional replacement, so a failed parse leaves the last valid snapshot intact. Semantic LLM enrichment, when added, belongs only in background sync and must receive the already-bounded block.

## School Directory sync boundary

Migrations `013`–`016` add the durable path `source → snapshot → structural record/diff → optional semantic interpretation → validated candidate → canonical apply`. Source records and candidates retain evidence; conflicts and unknown identities become review issues. A manually confirmed mapping is immutable and versioned through end/supersede instead of silent replacement.

The first live School Directory bootstrap confirmed the 2026/27 source authority: base classes come from `Списки по классам 26/27`, instructional and English rosters from `списки групп 26-27`, and OGE/EGE/profile memberships from `ОГЭ/ЕГЭ`. The school has separate base classes 9-А and 9-Д; Grade 9 Math is the distinct canonical A/B/C subject structure. Teacher labels in rosters are not treated as canonical teachers unless a full identity is proven.

Initial bootstrap may use Groq for ambiguous semantic units, but Groq is behind a provider/model-neutral interface and never writes canonical rows. Subsequent runs compare deterministic record fingerprints and send only new or changed ambiguous units to the provider. The last known valid snapshot remains explicit.

Schedule entries can carry an extensible `audience_rule` and resolved-audience diagnostics. Rules support canonical cohorts, union/intersection, exclusion/complement, and explicit exceptions, so a residual lesson does not require a fake permanent group.

## Integrations

- Google Sheets: server-side read boundary and deterministic parser are ready; the first local grant is still required before live reads.
- Google Classroom: course/courseWork/studentSubmission reads use the same OAuth token and no service-account impersonation.
- Supabase/Postgres: migrations `001`–`016` are the schema contract. Migrations `010`–`012` add explicit school-group dimensions/provenance, archive legacy schedule runtime, and reconcile Grade 9 Math to A/B/C. Migrations `013`–`016` add School Directory staging/review, temporal constraints, relationship validation, versioned/immutable manual source mappings, FK indexes, and composable schedule audiences. RLS plus revoked `anon`/`authenticated` grants form a deny-by-default Data API boundary, while application authorization and cross-student isolation are enforced in the backend repository layer.
- Render: `render.yaml` is the API Blueprint. Schema migrations remain an explicit operator step before deploying code that uses them.
