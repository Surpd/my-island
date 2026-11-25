# Teacher workload analytics contract

`GET /api/admin/analytics/teachers?start_day=YYYY-MM-DD&end_day=YYYY-MM-DD`
is an admin-only, read-only summary over the canonical teacher projection. It
uses the range resolver from PR #52 (`codex/teacher-range`, commit
`636fa7863584258b2cb70f207c7e2395dfb24342`), cherry-picked into this branch;
the analytics PR therefore depends on that range contract and must be merged
after it (or rebased onto it).

Each teacher includes scheduled minutes, lead minutes, responsible-teacher
minutes, joint-activity minutes, activity-type minutes, exact date/interval
details, and unknown-duration count. A responsible teacher is counted only for
`independent_practice` rows where they are in `responsible_teacher_ids` and
not in the physical `teacher_ids` set. Joint activities are represented once
per teacher projection and carry their provenance ID. Cancelled, lunch,
no-lesson, draft, and superseded rows are not workload.

The response never includes student membership or roster data. If no approved
canonical week is available, the response reports `canonical_not_materialized`;
aggregate numeric fields are `null` rather than manufactured zeroes. For a
partially covered range, aggregate fields are also `null` and observed values
are exposed under `observed_*` with `coverage_status: partial`. Zero is emitted
only when every requested week is known and published
(`coverage_status: complete`), including a fully published week with no teacher
intervals. A broken publication head is unavailable and never falls back to
legacy schedule data.

Acceptance: minutes are reproducible from published interval records; 30/45/90
minute intervals, historical and sparse weeks, joint/online lessons, cancelled
and moved rows, clubs, and responsible-vs-lead roles remain distinguishable.
Class analytics is a follow-up PR using the same read-service and grouping
details by canonical audience.

Audit comparison: covered are published provenance, exact intervals, 30/45/90
minute durations, historical/sparse ranges, role separation, joint/online and
activity-format fields, admin permission, HTTP coverage, and date/day-window
summaries. The reader resolves each distinct published week once, then fans
out canonical blocks in memory to the selected teachers. A synthetic roster
regression keeps query count effectively constant when 40 unassigned teachers
are added. The remaining operational follow-up is a PostgreSQL latency and
isolation benchmark on a production-sized roster; class trajectory/window
aggregation belongs to PR #56.
