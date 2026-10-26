# School journal operations

Journals use the existing seven `CURRENT_JOURNAL_SPREADSHEETS` sources and
Supabase database. Google sources remain read only. No new canonical people or
groups are created by an import.

## Import and matching

The existing hourly Cron sends one request to the backend. Background tasks run
workbooks sequentially. A PostgreSQL session advisory lock for `__school_import__`
prevents simultaneous heavy school imports; workbook locks and sync-run leases
retain the existing recovery mechanism. A failed tab leaves its last snapshot
intact and does not stop other tabs.

Exact names are restricted to the source grade. Reviewed workbook aliases survive
subsequent imports. Automatic group links require the complete canonical roster
to match exactly; incomplete or mixed rosters need an administrator decision.
The matching panel previews identity corrections before confirmation and records
previous associations in the audit log. Scores are not changed by matching.

Student endpoints return only that student's linked individual rows. Teacher
endpoints require an active assignment and filter results to canonical group
membership. Teams remain separate from personal grades.

## Private backup and controlled rollout

Run `python -m backend.scripts.journal_school_rollout backup` before importing.
The command verifies the approved production project and saves a repeatable-read
gzip JSON snapshot of journal tables and source mappings in ignored `data/`.
The output includes table counts and a SHA-256 digest. Never commit the snapshot
or source capture: both contain private school data.

`import` loads an already captured, bounded source snapshot sequentially. `repeat`
reimports three representative tabs and compares stable IDs, values, mappings,
and history digests. `report` prints aggregate counts without pupil names.
The capture helper requests unformatted effective values to preserve precision
and checkbox semantics.

For recovery, load the snapshot into an isolated staging schema first, preserving
UUIDs and JSON/numeric types using the production table definitions and
`jsonb_populate_recordset`. Compare affected workbook/source IDs and restore only
reviewed rows by primary key, in parent-before-child order. Restore journal
sources, assessments and students before results/summaries/history and mappings.
Do not truncate production tables or overwrite canonical identities, groups,
memberships, OAuth credentials, or unrelated school-source rows. New rows created
after the backup and later legitimate edits must be reviewed separately. A
restoration is a separate operator action, never an automatic import fallback.

The transposed tab `Прогресс ЕГЭ русский` is exam statistics rather than a normal
gradebook. Its unsupported format remains visible as an import issue; task
percentages must not become pupil grades.
# Per-tab controls and classification repair

Each source keeps its settings by spreadsheet ID and Google sheet ID. `auto`
participates in Cron, `manual` skips Cron but permits a manual refresh, and
`excluded` skips ordinary parsing in either mode. Exclusion preserves all saved
results and histories. Reconnecting a workbook preserves existing preferences.

Journal matching offers a complete class roster, school-wide search, explicit
batch alias previews and duplicate-checked student creation. Creation adds only
the existing canonical base-class membership. A possible duplicate, including an
inactive identity, blocks creation. Review that existing identity in People.

For the October 25 classification repair, a private backup was saved at
`data/journal-quality-before-20261025.json.gz` (SHA-256
`d1666c786a62ac9322d27e2c826b9e8f02909f1a23aaa1754c7123b0c0600639`).
`backend.scripts.journal_quality_cleanup` defaults to a read-only preview and
requires this backup plus fresh bounded CellData captures. `--apply` changes
classification/current flags and display names, never score values or source
Sheets. It rejects concurrent/queued imports. Audit action
`journal_quality.reclassified` records the previous associations and flags.

For a targeted recovery, stop conflicting imports, compare stable IDs with the
private backup and audit, and restore only the affected classification/link/
current flags. Keep later scores and history intact; do not replace whole tables.
