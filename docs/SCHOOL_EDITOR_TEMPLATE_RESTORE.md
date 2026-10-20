# Main school editor template

The production school/import button opens the draft explicitly referenced by
`school_template_selection(scope='school')`. Opening is a single read, without
canonical import, name matching, date ordering or payload rewriting. Missing
selection is an actionable error; it never produces an empty/canonical fallback.
Canonical versions remain historical structured sources. Dated weeks remain
detached editor copies. Duplicating a draft cannot change the active pointer.

Migration 040 adds only the backend-owned selection table, with RLS enabled and
no PUBLIC/anon/authenticated access. Its restrictive FK protects the selected
draft from deletion. No existing canonical/draft/directory rows are changed by
the schema migration. SQLite initialization has the equivalent table.

## Restoring the reviewed source

The original local SQLite draft `4dc886ce-db12-4faf-bbc7-e614a2fce673`, revision
45, is the source of truth. It contains 386 distinct source IDs: 337 non-lunch
records and 49 lunch marks, with source-led review metadata, audience rules,
snapshots, room/teacher references and unresolved clarifications. Existing
transfer_result totals are historical import accounting and are not recomputed
or substituted for current payload counts. No legacy canonical NO_LESSON rows
are added. No Google parsing is part of restoration.

The original stored JSON byte SHA-256 is
`94a0cf88d5f29934c19a40ff636a28d3d268c5b864ae49454e768d555aa9b24e`.
Its stable semantic JSON SHA-256 is
`12f22d13cf5b67b3c80a134ffce1e09be4d18537b231c05589ac79bdcbdd9fbe`.
Production JSONB formatting may differ; parsed payload equality is required.

`python -m backend.scripts.restore_school_editor_template SOURCE
--expected-sha256 HASH --report REPORT` performs a transaction-read-only
reference check through the approved project connection. `--apply` is an
explicit operator action: it repeats integrity checks, creates a new draft
retaining revision 45 and the entire payload, inserts the selection, verifies
stored equality, then commits both writes atomically. It never calls payload
normalizers or audience recalculation. A checksum mismatch, unknown reference,
invalid source or different existing selection prevents insertion. Repeating a
successful restore reuses its recorded ID. Private payloads/reports stay ignored
under artifacts, not in Git or frontend bundles.

Verified references at restoration: 47 groups, 24 teachers and 112 snapshot
student IDs, all present in the production directory. No groups/memberships are
created or reassigned. The old canonical copy remains a separate accessible
draft with its original payload/revision.

Targeted SQL-backed tests cover selection without canonical fallback, renames,
edits/reload, duplicate/week isolation, active FK protection, restore dry run,
checksum/reference failures and idempotent exact-payload restoration. Existing
export logic is reused, including logical duplicate grouping and all source IDs.
