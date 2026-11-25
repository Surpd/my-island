# Class workload analytics contract

`GET /api/admin/analytics/classes?start_day=YYYY-MM-DD&end_day=YYYY-MM-DD`
groups published canonical teacher intervals by canonical audience group. It
depends on the teacher-load PR (#53) and therefore transitively on the
multi-week range contract in PR #52; this branch is based on
`codex/teacher-load`.

Each group reports scheduled minutes, interval count, activity-type minutes,
unknown-duration count, dates, and source interval details. Co-teaching does
not multiply class duration: identical group/date/slot/activity intervals are
deduplicated across teachers. Explicit joint activities include their
provenance ID, so parallel streams do not create a false longer school day.
Cancelled, lunch, no-lesson, draft, superseded, and unmaterialized weeks do
not become zero-valued lessons. No student roster or membership data is
returned.

Acceptance: a class with two teachers has one 45-minute interval; separate
groups in a joint activity each retain their own audience bucket; sparse and
historical ranges remain date-bounded; unknown time values are reported rather
than silently treated as zero. The implementation now exposes trajectory
dimensions, group-type/trajectory filters, daily first/last interval windows,
occupied minutes, and gap minutes computed from a union of overlapping
intervals. This avoids the audit's false-window failure mode. A remaining
scale risk is one canonical projection per active teacher; a future measured
batch resolver should replace that fan-out before very large schools are
enabled.
