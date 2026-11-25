# Teacher schedule range contract

`project_teacher_range(database, identity_id, start_day, end_day)` resolves each
ISO-calendar week intersecting the requested inclusive date range. It filters
items, issues, and day modes again by the exact requested dates, so a Monday
week cannot leak dates outside the request and a range may cross month or year
boundaries.

Only approved canonical publications (`approved_baseline`,
`approved_with_exceptions`, `authoritative`) contribute schedule data. A week
without an effective materialization is sparse input: it contributes no items
and does not hide approved weeks before or after it. Draft or superseded weeks
make the aggregate `not_published` and suppress its data, preserving the
existing publication visibility rule.

Teacher audience rules remain per assignment: the teacher must be listed in
`teacher_ids`, or in `responsible_teacher_ids` only for an
`independent_practice` assignment. Lunch, no-lesson, SDEP, and cancelled
blocks remain hidden. Explicitly linked joint activities are merged by date,
slot, and joint activity ID while retaining the union of canonical audiences.

Acceptance checks:

- published weeks before and after an unmaterialized middle week are returned;
- a request spanning December/January returns only dates in the inclusive
  range;
- existing cancellation, role restriction, and joint-activity tests remain
  green;
- publication metadata exposed by single-week projections is retained when
  present in any week of the aggregate.

Known risk: the aggregate status is representative when approved weeks use
different approved statuses; callers should treat `items` as the authoritative
visibility signal and inspect per-week publication metadata when available.
