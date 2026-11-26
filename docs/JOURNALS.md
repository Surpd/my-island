# School journals contract

This document describes durable journal import, privacy, period, matching,
analytics, and recovery behavior. It contains no pupil-level records. Current
source IDs, credentials, and private backup contents belong in restricted
operator configuration or ignored local backups, never in this document.

## Sources and imports

Google journal workbooks are read-only input. Import normalizes a complete
snapshot and replaces the database snapshot transactionally; failed or partial
imports retain the last successful data rather than presenting partial results
as current. Workbooks are processed sequentially. Per-workbook locks and sync
run leases prevent overlapping imports; the school-wide import lock avoids
concurrent heavy runs. A failure in one tab does not discard its prior snapshot
or prevent unrelated tabs from completing.

Source settings are distinct: `auto` participates in scheduled sync,
`manual` skips scheduled sync but can be refreshed manually, and `excluded`
skips ordinary parsing while preserving saved results and history. Scheduled
refresh also requires auto-sync to be enabled. Newly connected sources default
to manual until explicitly enabled. A sync status must be read with its last
successful snapshot timestamp; an error does not make stale-but-successful
data equivalent to a never-imported source.

Period assignment is explicit. Academic year and term number are source
metadata; `current` selects which valid period appears on the current student
view. Historical sources remain available for administrative analytics.
Term-level import asks for the requested source period; unsupported sheets
remain visible as import issues and must not be coerced into ordinary grades.
Period validation lives in `backend/services/journal_periods.py`.

Operational imports and backups are described in
`SCHOOL_JOURNALS_OPERATIONS.md`. Backups and source captures contain private
school data and must stay outside Git. Restore into isolated staging first,
then review and restore only affected rows; never truncate or replace whole
production tables as an automatic import fallback.

## Roster and role access

`journal_students` is the normalized source roster and may exist without an
application identity. An optional `identity_id` link is used only when a
unique canonical match or an explicit reviewed association exists. Missing
Telegram/app accounts therefore do not remove a student from teacher journal,
history, or analytics views.

Student endpoints return only the student's linked individual rows. Teacher
endpoints require a current teacher assignment and restrict rows to canonical
group membership; teams remain separate from personal grades. Administrative
matching is previewed before confirmation. Exact-name matching is bounded to
the source grade, reviewed aliases are preserved across imports, and automatic
group links require a complete canonical roster match. Ambiguous or mixed
rosters require an administrator decision. Matching changes associations, not
scores. See `backend/services/journal_matching.py`,
`backend/services/journal_admin_matching.py`, and
`backend/tests/test_journal_admin_matching.py`.

Team entries use an explicit `entry_kind` and participant junction. They are
not inferred from punctuation or conjunctions in a name, and team results do
not become individual grades. Participant changes have a separate audit
event. See `journal-matching-recovery.md` for safe reversal rules.

## Analytics and cell activity

The admin overview and per-source analytics are read-only views over the same
active work, source roster, and current values as the journal screens. Access
is administrator-only. Group filters include shared work when it applies to
the selected group. Date filters are inclusive and use assessment dates;
undated work is excluded from a date-filtered result and counted separately.
Period summary cells cover the entire selected period, independent of the
assessment date filter.

Each eligible assessment/roster-row pair contributes one cell. Numeric
coverage distinguishes numeric results (including zero), status marks, and
empty cells; it is `null` when there is no eligible denominator or no
successful snapshot. Unknown assessment categories remain unknown. Analytics
does not reclassify work or change grading rules. The aggregate endpoint
supports grade, subject, year, term, group, and date filters; source detail
rechecks authorization. See `backend/services/journal_analytics.py`.

Cell activity is an import-observed history, not a grade-entry audit. Migration
057 records observed creation/change/removal of a current cell and reports
counts by UTC day and change type. It does not identify who entered a grade or
when it was changed in Google. A first import can appear as cell creation;
history before migration is not reconstructed. Reimporting an identical
snapshot creates no new change. See `backend/tests/test_journal_cell_activity.py`.

Student Campus keeps source journals distinct and displays the latest relevant
result without averaging unlike scales. An imported average is shown as a source
summary. Results preserve zero, absent grades, points and special statuses as
distinct values. Dated work is shown most recent first, followed by undated work.
The return action preserves the selected period, scroll position and focus.

## Recovery and known limits

Do not reverse matching or classification by restoring an entire workbook.
Stop conflicting imports, inspect later audit events, and restore only the
reviewed association/classification/current flags. Preserve later scores,
result history, team participants, and legitimate edits. If an association or
alias changed after the target confirmation, refuse a blind reversal. The
operator workflow and exact safeguards are in `journal-matching-recovery.md`
and `SCHOOL_JOURNALS_OPERATIONS.md`.

Known limits: an import records the time data was observed, not the original
Google edit time; an unlinked roster row cannot be shown as a student's
personal result until reviewed; ambiguous roster/group mappings remain
unresolved; an unavailable source has no trustworthy current metrics; and
unsupported exam-statistics formats remain issues rather than grades.

## Stable references and visual results

- Import and source controls: `backend/services/google_sync_service.py`,
  `backend/services/journal_periods.py`, `backend/handlers/api.py`.
- Role views and matching: `backend/services/teacher.py`,
  `backend/services/journal_matching.py`,
  `backend/services/journal_admin_matching.py`.
- Tests: `backend/tests/test_journal_import_parser.py`,
  `backend/tests/test_journal_scoped_import.py`,
  `backend/tests/test_journal_role_views.py`,
  `backend/tests/test_journal_admin_matching.py`,
  `backend/tests/test_journal_current_period_postgres.py`,
  `backend/tests/test_journal_cell_activity.py`.
- UI acceptance references: [Design system](DESIGN_SYSTEM.md).
