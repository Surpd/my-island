# Schedule architecture

## Source of truth

The approved canonical template is the only semantic source of truth. The
current approved baseline is `SCHEDULE_CANONICAL_V1_SHADOW.json`; its
validation and manual-reconciliation boundary are recorded in
`SCHEDULE_CANONICAL_V1_SHADOW_REPORT.md` and
`SCHEDULE_CANONICAL_V1_TRIAGE.md`.

```text
Google Sheet raw data (values, colours, merges, notes)
  -> structural snapshot + provenance + fingerprint
  -> approved immutable canonical template
  -> structural template diff and semantic patches
  -> immutable weekly overlay
  -> effective schedule
  -> student and teacher projections / allocation validation
```

`NO_LESSON` is an effective per-student final state. It is never a
mode-specific default assignment.

## Component status

| Component | Status | Responsibility |
|---|---|---|
| Google Sheets ingestion and `schedule_pipeline` structural snapshot/diff helpers | KEEP | Raw source, formatting, merge and note preservation; fingerprinting and weekly structural diff. |
| `schedule_canonical_bootstrap` | KEEP, bootstrap-only | Produces an auditable initial canonical artifact; never runs in an API request or production routing path. |
| `schedule_allocation` | KEEP | Exact student-slot allocation and duplicate/conflict validation. |
| `schedule_parser_v2` row structures and `NinthGradeResolver` | MIGRATE | Bootstrap-only compatibility code used to reproduce the accepted v1 artifact. They are not runtime engines and must be folded into canonical-template maintenance before the next authoritative-pilot implementation. |
| `schedule_pipeline` V1 reconciliation, `schedule_v1_*` database accessors and admin routes | MIGRATE | Existing compatibility API for historical V1 records. It must be replaced by a canonical/effective-schedule adapter before authoritative scheduling is enabled; it must not be used as a fallback for canonical routing. |
| V2 shadow runner, AI semantic shadow/provider, Groq shadow scripts and their dedicated tests | REMOVE | Removed. They were non-production comparison experiments and did not create the approved canonical template. |
| `schedule_import` and old `schedule_entries` readers | MIGRATE | Historical import/read compatibility; no new semantic behaviour may be added here. |

## Runtime guard and API rollout

No canonical request may fall back to V1, Parser V2, or `NinthGradeResolver`.
The canonical read-side is selected explicitly with `SCHEDULE_BACKEND=canonical`;
the default is `disabled`, so no schedule semantic engine is active until a
canonical pilot explicitly enables it. There is no legacy schedule fallback.
by this bootstrap. In the default mode, student and teacher compatibility reads
remain read-only and do not invoke any parser/importer. Canonical persistence and
effective-week materialization are separate admin/operational steps; no request
path makes a canonical version authoritative automatically.

## Removed experimental paths

- `backend/services/schedule_semantic_shadow.py`
- `backend/services/schedule_ai_shadow.py`
- `backend/services/schedule_semantic_ai.py`
- `backend/scripts/schedule_v2_shadow.py`
- `backend/scripts/schedule_ai_shadow.py`
- dedicated V2/AI shadow tests

## Regression ownership

Canonical/effective-schedule tests must retain coverage for Grade 9 Math and
English partitions, OGE overlays, explicit residual activities, final
`NO_LESSON`, moved/cancelled/replaced weekly slots, fail-closed unresolved
routes, and duplicate student-slot detection. Tests that only compare one
deprecated semantic engine with another are intentionally removed.
