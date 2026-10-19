# Production school-template opening hotfix

## Confirmed failure

On 19 October 2026 Render logged HTTP 500 for
`POST /api/admin/schedule/experiments/school/import`.
`create_experiment` failed at JSON serialization with
`TypeError: Object of type time is not JSON serializable`.

The grid's top-level start/end strings were normalized, but each card also
retained the complete nested canonical SQL block and assignment. Psycopg
returns native `datetime.time`, timestamps and UUID values in those rows.
Saved JSON drafts did not have this problem, explaining why smaller saved
templates and weeks opened successfully.

Normalize those known SQL types recursively at the canonical-to-draft
provenance boundary. Keep unsupported values as serialization errors; do not
use a blanket `default=str`, discard source fields or return an empty template.

## Source contract

Production has no persisted 386-record main school copy. Its pre-hotfix records
are separate test/user drafts without canonical template provenance; they must
not be overwritten or adopted as the main school template.

The production button opens a saved primary school **canonical copy**, creating
it only if none exists. It uses canonical version
`manual-2c2c1ba62bc14be9683e69b4` (approved_with_exceptions): 246 blocks and 378
assignment records, of which 65 retain the legacy subject `NO_LESSON`.
The separate Google-derived snapshot, revision 45, has 386 records: 337 lessons
and 49 lunch records. They are different projections, not a simple eight-lesson
loss. This hotfix does not import the local Google snapshot, alter canonical
records, reconcile these sources or rewrite old drafts.

The UI names the canonical source before opening and retains a source notice
afterward. PostgreSQL advisory transaction locking serializes first creation.
An explicit primary marker is tied to the primary draft's own ID. Subsequent
opens preserve its ID, payload, name and revision, including after edits.
Weeks and copied drafts cannot become the primary simply by sharing a base
canonical version. Existing legacy canonical template copies are reused.

## Verification

- Reproduced the exception using the actual production canonical projection
  through the existing transaction-read-only PostgreSQL adapter; no production
  draft was written during diagnosis.
- The same actual 378-card projection serializes successfully after the fix.
- Added a real SQLite integration regression with 265 blocks and 386
  assignments. Native SQL TIME values are materialized at the SQLite row-factory
  boundary to match PostgreSQL, without mocking the import/persistence services.
  Before the fix it reproduces the exact TypeError. The fixed test covers JSON
  provenance round-trip, all IDs, save/reload, repeated opening, edited primary
  preservation, copied-draft exclusion, detached weeks and unsupported-value
  rejection.
- Targeted schedule/import/production-route tests pass (27 tests); TypeScript
  and constructor lint pass. Production browser verification and deployment
  identities are recorded in the private completion report.

No schema migration is required. The activity catalog, exporter, canonical
groups, memberships and original Google spreadsheet are unchanged.
