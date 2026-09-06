# Мой Остров — build brief v2 for Luna

## Source of truth

This document is the current and only product/build source of truth for the project. The previous `OSTROV_ONLINE_LUNA_BUILD_BRIEF.md` is retained as historical material and must not override this brief.

Read this document fully before making architectural or product decisions. Preserve the stable product/domain constraints below, but do not spend time resolving visual details that are intentionally left replaceable.

## Product and project

Create **«Мой Остров»** from scratch as a real Telegram Mini App + Telegram bot foundation for a school.

- Technical slug / repository: `my-island`
- Local workspace: `D:\Projects\my-island`
- The project is new; assume no legacy implementation.
- The application is a personal school cabinet where a spatial island is the student home/navigation experience.
- This is not a game and not a visual-only spike. Schedule, homework, grades and school information must remain fast, normal functional UI.

Work autonomously where reasonable. Choose pragmatic implementation details yourself, while preserving the product model, security boundaries and priorities below.

## Prepared external accounts and current state

Technical Google account already exists:

`antischool.island@gmail.com`

It already has:

- read access to the school Google Sheet containing the schedule;
- teacher access to one real Grade 9 mathematics Google Classroom for integration testing.

Also prepared:

- Render account/organization;
- Supabase account/organization;
- Supabase connected to the existing GitHub account;
- private GitHub repository `my-island` already created;
- Telegram bot already created, but its token is not in the repository.

Not prepared yet:

- Google Cloud project and OAuth credentials/configuration.

Use the technical Google account as the intended identity for future Google integrations. If OAuth/Google Cloud configuration becomes necessary, stop at the real blocker and explain the exact user action or secret required. Do not fake a live integration.

Do not create or mutate external Supabase, Render, Google Cloud, GitHub, or production Telegram resources without explicit user approval. Local preparation of configuration, migrations, manifests, contracts and setup instructions is allowed.

Do not commit, push or deploy.

## Product roles

Support:

- student;
- teacher;
- admin.

A Telegram account may have more than one role.

First implementation priority:

1. student flow;
2. identity approval;
3. groups and memberships;
4. schedule;
5. basic admin;
6. teacher foundation;
7. Google Classroom boundary and first live test when OAuth is available.

## Telegram identity and approval

Telegram Mini App identity must be verified server-side using Telegram `initData`. Never trust a Telegram user id supplied only by the frontend.

First-login flow:

1. Recognize and verify the Telegram account.
2. Let the user select a role.
3. Let a student/teacher select their canonical identity from an allowed search/list.
4. Create an identity claim.
5. Before approval, expose no personal school data.
6. Let an admin approve or reject the claim.
7. After approval, link the Telegram account to the canonical student/teacher identity.

At minimum support these states:

- pending;
- approved;
- rejected;
- identity conflict.

Do not silently bind one canonical person to multiple Telegram accounts. Use stable internal IDs; names are display/search fields, not identity keys.

Provide a safe local development mode that works outside Telegram. It must be explicit and must never weaken production authentication or authorization.

## Groups and memberships

Groups are a central domain object. One internal group model must be able to connect:

- students;
- schedule audiences;
- teachers;
- Google Classroom courses;
- future official grade sources.

Expected group types include:

- class;
- mathematics subgroup;
- English subgroup;
- OGE/EGE group;
- electives and other school groups.

Memberships must preserve provenance/source, for example:

- official spreadsheet/import;
- admin override;
- manual/self-selectable elective.

An official re-import must not accidentally destroy an explicit admin override. Design memberships with active/validity state where useful.

## Schedule

The current schedule source of truth is the school Google Sheet.

Architecture:

`Google Sheet → importer/parser → validation → normalized database → application`

The frontend must not query the spreadsheet directly on every page view.

Normalize at least:

- date;
- start/end time;
- subject;
- teacher;
- room;
- audience/group(s);
- source metadata;
- raw/source representation when useful for debugging.

The sheet may contain merged cells, days/times/classes, subgroups, electives or multiple options in one visual region, and semi-structured subject + teacher initials + room cells.

Parsing is deterministic-first. An LLM is not mandatory for normal synchronization.

A sync should roughly follow:

`fetch → detect/change/hash if useful → parse → validate → atomic update`

If parsing or validation fails, keep the last valid schedule. Never wipe valid current data before a successful replacement. Make imports idempotent where practical.

Admin must be able to see last successful sync, latest error, validation problems and a manual refresh action.

The first real import should be attempted when the existing read access makes it possible; otherwise use explicit mock/sample state and document that it is not live.

## Google Classroom

The school does not use a managed Google Workspace domain. The intended integration model is the normal technical Google account `antischool.island@gmail.com`, added as a teacher to required Classroom courses.

Do not design around a service account pretending to be a Classroom teacher.

Prepare clean boundaries for:

- courses;
- courseWork;
- studentSubmissions.

Map Classroom courses explicitly to internal groups.

Homework may show:

- title;
- description/instructions;
- deadline;
- subject/group;
- submitted/not submitted when available;
- link to the original Classroom item;
- Classroom grade when available.

Classroom grades are not official school journal grades. Keep them in a separate domain and label them clearly in the UI. Never infer automatically that a Classroom assignment corresponds to an official journal grade.

Until Google OAuth credentials exist, implement the integration architecture, schema/contracts, configuration/status UI and test/mock boundary. Clearly distinguish mock/test state from live integration. Do not claim a live Classroom connection without OAuth.

## Official grades

Official grades are a separate domain from Classroom. Prepare for subject, date, value, type/weight when available, optional comment and averages/statistics.

The official journal source can be connected later. Do not couple this domain to Classroom.

## Announcements and events

Treat announcements and events as different concepts.

### Announcements

Operational or organizational school information, such as a form, registration, document request, important message, deadline or CTA.

Support targeted audience, active-until/expiry and optional completion state.

### Events

Actual school-life activities, such as Halloween, a fair, self-government day, concert, theater, excursion, trip, thematic week or activity day.

An active event may affect the visual state of the island event plaza, but ordinary events must not force a game-like interaction model.

## Student UX and navigation

The student home is an illustrated Grade 9 prototype island. It is spatial navigation, not gameplay.

Do not use a permanent bottom tab bar. Do not add permanent floating navigation buttons over the island. Buildings/locations may themselves be hotspots, with small contextual labels or a brief highlight only when useful.

Stable semantic locations/hotspots are:

1. Schedule
2. Homework
3. Grades
4. Information / announcements

The exact geography, landmark shapes, visual composition and placement of these hotspots are intentionally not final. Keep them in a replaceable config rather than business logic. A reasonable prototype may associate the four functions with distinct school-like landmarks, but the code must not depend on a particular map or art direction.

Profile/settings are opened from a top-right avatar/control and are not an island location.

There may be non-clickable worldbuilding locations. A central event plaza is normally part of the world, not a permanent app section; when a real event is active, it may transform visually and become interactive.

### “Сегодня” bottom sheet

At the bottom of the island, implement a collapsible/draggable HTML sheet named **Сегодня**. It is the primary daily utility surface.

Collapsed state should surface only the most useful immediate information, such as:

- current lesson;
- next lesson;
- nearest homework deadline;
- important announcement/event.

Expanded state becomes a practical daily dashboard. The island continues visually behind the sheet; do not reserve an empty rectangle below the artwork.

Routine information must remain fast to access through “Сегодня”, even when the user never explores the island.

Functional sections opened from hotspots are normal fast HTML UI. Close-up artwork is context, not the entire interface.

## Island visual and technical model

Use raster artwork for the world rather than attempting to build the entire island as SVG layers.

Build one sufficiently good temporary Grade 9 prototype art pack now. Do not spend time on final production island art.

The visual layer must be replaceable and config-driven. Keep these outside business logic:

- master island asset;
- close-up assets;
- hotspot coordinates and semantic IDs;
- camera/transition values;
- day/night variants;
- event variants.

Support a future per-grade pack model for grades 5 / 6 / 7 / 8 / 9 / 10 / 11, but do not build all seven now.

Do not create, render or animate characters/real students in this iteration. No walking animations, ambient population, avatar generator or character-specific product work. Leave a clean extension point only if it naturally falls out of the architecture.

### Location transition

On hotspot tap:

- quick zoom/translate toward the selected location;
- approximately 200–300 ms;
- optionally crossfade from the master raster to a separate close-up raster;
- then show the functional section UI.

Do not make long game-like cinematic transitions. Back should return quickly to the same island state.

### Visual direction

Keep visual direction independent from product logic. Stable guidance is:

- premium stylized 3D illustration;
- colorful and alive, but not childish;
- suitable for Grade 5 and Grade 11;
- coastal / vertical / terraced island-town feeling;
- meaningful architectural landmarks;
- clean modern interface typography over the illustrated world;
- no generic neon/glassmorphism;
- no mobile-game/toy feeling.

Do not fix a final island geography or final art direction beyond these durable principles. Day/night later should use aligned artwork variants with real lighting changes, not only a blue overlay.

## Reference usage

At most three reference types may be supplied. References are directional and are not ready-made design specifications.

1. **Island art-direction reference, if attached.** Use only for broad mood, scale, architectural richness and coastal/terraced world feeling. Do not copy its exact island, composition or assets.
2. **UX/navigation board.** Use only to understand the interaction principle `island → zoom → close-up → functional HTML UI` and the “Сегодня” surface. Do not copy characters, phone frame, labels, exact composition or visual design.
3. **Real Telegram Mini App screenshot.** Use only as a viewport/chrome reference: understand the usable area inside Telegram and safe-area constraints. Do not copy the old app’s design.

If a reference is absent, use a temporary reasonable implementation and keep the visual layer replaceable. Do not block the foundation on finding or perfecting references.

## Telegram Mini App viewport

Design for a real Telegram Mini App, not a fake elongated phone mockup. Account for Telegram chrome/header, safe areas, changing viewport height, expanded mode and potential future fullscreen.

Do not rely only on `100vh`. Use Telegram viewport/safe-area APIs through a small adapter if appropriate.

Check at least:

- 393×852;
- 375×812;
- 360×800.

## Teacher UX

Teachers use the same backend and design system, but do not need to be forced into the student island metaphor. The initial teacher foundation may be a practical dashboard.

Prepare for teacher schedule, groups, Classroom assignments, submission counts, schedule changes and announcements/events. Teacher identity also uses approval.

## Admin

Build a practical admin area; it may be desktop-first/responsive and does not need the island metaphor.

Minimum useful capabilities:

- pending identity claims;
- approve/reject;
- identity conflicts;
- students;
- teachers;
- groups;
- memberships;
- schedule import status/errors;
- manual schedule sync;
- announcements;
- events;
- integration status/config placeholders;
- audit trail for important admin actions.

## Telegram bot

The bot complements the Mini App and does not duplicate it.

Architecture should support personalized notifications for new homework, deadline reminders, schedule changes, new official grades, important announcements and events.

Use the same identity/group model. Prepare notification preferences and quiet-hours architecture, but do not inflate the first iteration by implementing every notification type.

## Security and data boundaries

Required:

- backend authorization for role-specific and personal endpoints;
- no personal student data before approved identity;
- personal/school tables deny-by-default with appropriate RLS policies;
- service-role credentials server-side only, never in the client;
- production Telegram auth cannot be bypassed by dev mode;
- secrets outside the repository;
- no unnecessary logging of Telegram `initData`, OAuth tokens or personal data;
- clear status/config boundaries for external integrations;
- migrations and validation;
- basic auditability for admin mutations;
- verification of cross-student data isolation.

Frontend guards are not authorization. Verify that one student cannot read another student’s personal data through direct API calls or altered client state.

## Stack and repository

Choose a pragmatic modern stack suitable for:

- Telegram Mini App frontend;
- backend API;
- Postgres/Supabase;
- Telegram bot;
- Google Sheet and Classroom integrations;
- later deployment to Render and suitable frontend hosting.

Keep architecture lean and respect `handlers → services → database`; do not mix layers or overengineer generic infrastructure for hypothetical products.

The global `AGENTS.md` already contains stable workflow and safety rules. Do not duplicate generic rules in the repository.

Create a project-level `AGENTS.md` only if genuinely useful; if created, keep it short and project-specific, covering only the product, critical domain invariants, core docs and important validation commands.

Keep documentation lean:

- `README.md` for local setup and commands;
- a small `docs/ARCHITECTURE.md` for durable boundaries and decisions;
- short integration notes only when genuinely necessary.

Avoid speculative documentation churn.

## First iteration priorities

Build one coherent working vertical foundation, not superficial breadth:

1. clean project/repository foundation;
2. domain/database model;
3. Telegram auth boundary + safe dev mode;
4. identity claim → admin approval → authorized student flow end-to-end;
5. groups and memberships;
6. schedule importer architecture + first real import if source access is available;
7. student island shell with replaceable Grade 9 prototype art/config;
8. “Сегодня” bottom sheet;
9. functional schedule view;
10. announcements/events;
11. basic admin;
12. teacher foundation;
13. Classroom integration boundary and first live test after OAuth credentials are available;
14. critical security/domain tests.

Use mock/sample data only where a real external source is unavailable, and make that state explicit in the UI/config/docs.

## Verification

Use targeted verification while developing. At minimum verify:

- production Telegram auth cannot be bypassed by dev mode;
- unapproved identities cannot receive personal data;
- approval flow works end-to-end;
- membership precedence/source behavior;
- failed schedule import preserves the last valid schedule;
- schedule parsing has fixture-based tests;
- RLS/cross-student isolation works;
- the main student UI works at target mobile sizes without horizontal overflow;
- build, typecheck and critical tests succeed.

Do not run expensive broad suites repeatedly when targeted verification is sufficient.

## Blockers and external actions

Work autonomously until a real blocker. If a secret, OAuth configuration, access grant or mutation of an external resource is required, stop and ask for only the specific action needed.

Do not create or mutate external Supabase, Render, Google Cloud, GitHub, production Telegram or other production resources without explicit approval.

Do not commit, push or deploy.

## Final report

At the end, report concisely:

- chosen stack and reasoning;
- repository/project structure;
- what works end-to-end;
- database/schema/migrations;
- Telegram auth and approval status;
- schedule integration status;
- Classroom integration status;
- student UI status;
- teacher/admin status;
- what is real versus mock/stub;
- tests/build results;
- blockers requiring user action;
- the smallest useful next increment.
