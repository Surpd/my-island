# Canonical student projection — Stage 2 validation report

Date: 2026-09-20  
Requested pilot week: 2026-09-21 through 2026-09-25  
Mode: read-only; canonical schedule remains non-authoritative

## Implemented reliability boundary

- Matching now builds the complete set of membership-applicable assignment claims before selection.
- Selection is deterministic and independent of assignment ordinal.
- Mutually exclusive membership overlap is detected by `groups.semantic_dimension`; when it is absent, the documented fallback uses group role/type plus grade and subject.
- Base-class plus a more specific instructional claim is compatible; the instructional claim wins.
- Different semantic dimensions are not reported as membership overlap. Assignment role/specificity resolves a proved primary versus secondary choice; equally applicable different activities fail closed.
- A primary/specific assignment without canonical groups fails closed as `GROUPLESS_PRIMARY_ASSIGNMENT`.
- Only residual/default/final assignments may consume the remaining audience.
- Mutable students, memberships, validity periods, group state, and exclusion overrides are request-scoped. Immutable canonical assignments remain cached only by immutable canonical block id.
- Every discrepancy includes student, date/slot, block, assignment, source-cell, group, membership, semantic-dimension, and final-state evidence when applicable.

## Validator issue codes

`INCOMPATIBLE_MEMBERSHIP_OVERLAP`, `UNCOVERED_PARTITION_MEMBER`, `GROUPLESS_PRIMARY_ASSIGNMENT`, `MULTIPLE_ACTIVITY_CLAIMS`, `UNEXPECTED_NO_LESSON`, `UNKNOWN_CANONICAL_GROUP`, `STALE_OR_INVALID_MEMBERSHIP`, `WRONG_EFFECTIVE_SLOT`, `WEEKLY_REPLACEMENT_LOST_AUDIENCE`, `WEEKLY_REPLACEMENT_LOST_TEACHER`, `AUDIENCE_PROJECTION_DIVERGENCE`, and `CANONICAL_BLOCK_UNRESOLVED`.

The report separates projection output from validation findings and reports totals school-wide and per grade. The comparison mode is source/effective consistency. Legacy comparison is intentionally not used because no equivalent trustworthy legacy baseline was established.

## Automated verification

- Targeted projection and validator suite: 26 passed.
- Full backend suite: 187 passed.
- Covered: normal membership, base plus specific group, residual routing, group-less primary, Math overlap, English overlap, different dimensions, exclusion override, validity periods, inactive membership/identity, mutation without restart, uncovered partitions, NO_LESSON regression prevention, ordinal independence, lost weekly audience, and moved-slot mismatch.

## Live DB-backed pilot

The Supabase project is healthy and was inspected read-only.

No `canonical_effective_weeks` row exists for `2026-09-21`. No `school_source_snapshots` row exists for the 21–25 September weekly source either. The latest persisted weekly snapshot is `14-18 сентября`, fingerprint `62f919ba7f7333872d111789059cd19404873506d10e45f6a20201fc5f7a945f`, effective through 2026-09-18. The latest persisted effective revisions are also for week start `2026-09-14`.

Therefore the requested live metrics for 21–25 September are deliberately not fabricated:

- students: unavailable
- student-slots: unavailable
- ACTIVITY / NO_LESSON / UNRESOLVED: unavailable
- multiple-activity claims: unavailable
- membership-overlap issues: unavailable
- unexpected NO_LESSON: unavailable
- Grade 9 metrics: unavailable

## Remaining limitation

Most existing Grade 9 groups do not yet carry explicit `role` or `semantic_dimension`. The validator safely falls back to group type, grade, and subject. This proves Math A/B/C and English partition overlap in current naming/metadata, but directory metadata should eventually become explicit so validation does not depend on naming conventions.

## Verdict

Stage 2 code is ready for pilot validation. Runtime verdict is **blocked**, solely because Stage 1 has not persisted the requested 21–25 September source snapshot and effective-week revision. No schedule data, memberships, canonical semantics, UI, or feature flags were changed.
