# Schedule and publication contract

This is the durable contract for schedule ingestion, approval, publication,
role projections, operational diagnostics, and optional Google Sheets delivery.
It describes behavior in the repository; it is not a record of any live
school's timetable or a release checklist for a particular deployment.

## Semantic authority and data flow

The approved canonical template is the semantic source of truth. Google Sheets
is an input source: ingestion keeps source values and structural context
(formatting, merges, notes, provenance, and fingerprints) so changes can be
reviewed. A normalized import or a preview does not itself approve or publish
schedule data.

```text
read-only source snapshot
  -> reviewed canonical template
  -> immutable effective week and explicit publication head
  -> role-filtered student/teacher projection
  -> optional admin observability and optional Sheets delivery
```

Canonical routing is opt-in. `SCHEDULE_BACKEND` defaults to `disabled`; do not
assume that importing a template activates canonical schedule responses. The
legacy Schedule Integration v1 and parser compatibility paths are not a
fallback for canonical routing. Bootstrap/import, approval, effective-week
materialization, and publication are separate operator actions.
Release 140 runs the canonical schedule and consolidated constructor in
production. The disabled default keeps unconfigured environments closed.

`NO_LESSON` is an explicit effective per-student final state. It is not a
default generated from a day mode. Unresolved audiences or structural
uncertainty must remain unresolved and visible to administrators rather than
being widened to a class or school.

## Publication selection

`resolve_published_week` resolves by the Monday containing the requested school
date. The immutable effective week and its publication metadata are selected
together. The current template and mutable drafts do not silently replace an
already approved week.

| Origin/state | Meaning |
| --- | --- |
| `manual_week` | Explicit weekly publication head points to a validated immutable edition. The saved authoring archive is checked; a broken head fails closed. |
| `approved_materialization` | Latest approved effective materialization for that week. A newer template or unrelated draft does not hide it. |
| `template_only` | An approved template exists but there is no materialized week to serve. |
| `missing` / `canonical_not_materialized` | No approved weekly edition can be served. |
| `broken_head` / `broken_publication` | An explicit head or its archive is invalid; role projections return no schedule rows. |

Only approved statuses (`approved_baseline`, `approved_with_exceptions`, and
`authoritative`) are eligible for role-facing data. Coverage metadata can be
`complete`, `partial`, `with_exceptions`, or `unknown`; consumers must retain
the distinction. A partial/exceptional publication is not evidence that
unresolved rows are safe to assign.

The publication receipt contract makes a confirmed publish/restore retryable
after a lost response. `operation_id` identifies the exact command: repeating
it with the same parameters returns the original receipt; reusing it with
different parameters conflicts. A stale preview still requires a fresh
preview. The receipt is available only to the authoring administrator.
The receipt implementation is in
`backend/services/constructor_publication.py`.

Publish and restore both require preview and confirmation of a specific draft,
revision, scope and week. An `info` finding (such as a missing room) can pass.
A `conflict` defers the whole linked block on both sides of a clash, including
the linked parts of a move. A `critical` issue such as invalid source identity,
an invalid time or stale base prevents confirmation. Preview reports the
excluded records while preserving the author's draft. Previous assignments do
not fill gaps automatically, and exclusion never widens the remaining audience.
The server revalidates revision, fingerprints and publication head under a
transaction lock. Runtime projection mismatch rolls back persistence. Restore
creates a new immutable edition. A manual publication protects that head against
automatic weekly-source application (`manual_publication_protected`).

## Role projections and ranges

Student and teacher projections read the selected published effective week,
then apply the role's canonical membership or assignment rules before API
serialization. Preview parameters select a person and date; they do not grant
access. Drafts and superseded weeks are not exposed as published schedule data.

Teacher visibility is assignment-specific: a teacher must be in `teacher_ids`,
or in `responsible_teacher_ids` for an `independent_practice` assignment.
Lunch, no-lesson, SDEP, and cancelled blocks are hidden from teacher lesson
lists. Explicitly linked joint activities may be merged while retaining their
canonical audience union. Student projections expose only student-facing
fields and do not expose optional activities as ordinary lessons.

## School rules

- Grade 9 Math uses canonical instructional groups A/B/C. Preserve explicit membership and residual audiences. Base classes, English groups, exam tracks and Classroom courses are independent dimensions. See [Architecture](ARCHITECTURE.md).
- A shared elective block keeps individual teacher and room assignments for diagnostics and Teacher Campus. Student Campus shows one neutral card per eligible block and interval, with no teacher, room or inferred direction. Parallel tracks in the same shared block avoid false student clashes. Teacher and room clashes still use each assignment. Clubs stay out of Student Campus. Legacy elective labels alone do not establish the shared block.
- `independent_practice` can omit an attending teacher and name a responsible teacher. An in-person practice reserves its room. Consultation reserves the teacher while remaining optional for the student. Mandatory lessons remain visible. Class online days supply the default format; an individual slot can also be marked online.
- Personal exclusions require a reason and are versioned. Template exclusions are inherited by future draft weeks. A dated override stays in its week. A weekly lab uses a preview of replacements for selected participants, with original lesson IDs restored on disabling it. If the lab overlaps only part of a lesson's time, the operator reviews its boundaries.
- Multiple draft weeks or templates can be named, copied, archived and restored. Final deletion requires an archived draft with no dependent records. Published editions can be inspected separately. Shared teacher colors are stored with a revision independently of lessons. Approved evidence and manual overrides are applied explicitly, with a stable fallback for unconfirmed colors.

Range APIs resolve each ISO week touched by the inclusive date range and filter
rows back to the exact requested dates. An unmaterialized middle week is sparse
input and does not erase published weeks before or after it. A draft or
superseded week makes the aggregate `not_published` and suppresses its data.
For mixed approved statuses, inspect per-week publication metadata; aggregate
status is representative, while returned `items` are the visibility signal.
See `backend/services/canonical_schedule.py` and
`backend/tests/test_canonical_schedule.py`.

## Editing, revisions, and diagnostics

Constructor edits are drafts guarded by revision/fingerprint checks. Preview
reports affected rows, unresolved records, and blockers; confirmation applies
only the reviewed state. Directory and draft revision contracts prevent stale
administrator edits from silently overwriting concurrent work; see
`DIRECTORY_REVISIONS.md` and `ADMIN_LIVE_EDITING.md` for those durable protocols.

The read-only admin observability surface reports canonical state, effective
weeks, publication heads/history, and safe schedule diagnostics. It is an
administrative view, not a separate source of truth. Optional Codex diagnostics
is a separate, read-only principal with a narrower allowlist; it is not a human
admin role and does not expose rosters or personal assignments. Its credential
and privacy boundary are documented in `CODEX_DIAGNOSTICS.md`.

The admin “Today” service is a date-scoped view over the selected published
edition. Missing publication yields an unknown lesson count (`null`); an empty
published day yields zero. Room output is the list of rooms occupied in the
schedule, not a complete room inventory. Cancelled events remain visible;
lunch and `NO_LESSON` do not count as lessons. Next-week draft inspection is
independent of the published day. It reports preview readiness/blockers and
does not publish or certify that a lesson occurred. See
`backend/services/operational_day.py` and
`backend/tests/test_operational_day.py`.

## Optional Google Sheets delivery

The persistent published-week export is optional and feature-flagged by
`MY_ISLAND_PUBLISHED_WEEK_SHEETS`; it requires migration 056 and the configured
approved export book. A publish/restore can enqueue a delivery for the exact
week head. The worker reads the checked author archive, excludes deferred
records, and renders the established school grid. Delivery state is separate
from publication state: a queued, failed, or unconfirmed export does not undo
the published edition. Retry/status belongs to admin history.

Managed tabs are bound to stable sheet IDs and ownership markers. The worker
checks the target marker and managed range before replacing content, performs
atomic batch updates, and verifies the marker and written contents by reading
them back. User edits inside the managed range, a marker collision, or an
occupied expansion range block delivery. Use an unmanaged area for human
notes. Lost responses are reconciled by reading the marker before retrying;
stale queued writes are rejected after a newer head. Google access remains
read-only outside this narrowly scoped managed export flow. See
`backend/services/published_week_sheets.py`,
`backend/services/schedule_sheet_publishing.py`, and
`backend/tests/test_published_week_sheets.py`.

## Stable references

- API and persistence behavior: `backend/services/canonical_schedule.py`,
  `backend/services/constructor_publication.py`,
  `backend/services/operational_day.py`.
- Google source and OAuth boundaries: `INTEGRATIONS.md`.
- Focused regressions: `backend/tests/test_canonical_schedule.py`,
  `backend/tests/test_constructor_publication.py`,
  `backend/tests/test_operational_day.py`,
  `backend/tests/test_published_week_sheets.py`.
- Design baselines and visual acceptance references: [Design system](DESIGN_SYSTEM.md).
