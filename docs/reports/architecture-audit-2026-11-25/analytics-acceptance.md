# Analytics acceptance registry

This registry records the audit criteria for PRs #52, #53, and #56. It is
deliberately explicit about remaining scale work; passing unit tests is not
treated as proof of production performance.

| Criterion | #52 teacher range | #53 teacher analytics | #56 class analytics |
|---|---|---|---|
| Historical/sparse weeks | covered | inherited | inherited |
| Provenance and exact intervals | covered | covered in details | covered in details |
| Lead vs responsible roles | inherited | covered | retained per source interval |
| 30/45/90-minute values | regression coverage | aggregation covered | aggregation covered |
| Categories/formats/online | source fields retained | filters + detail fields | activity distribution + trajectory |
| Joint/co-teaching dedup | source projection | joint provenance | class interval union/dedup |
| Daily windows/gaps | n/a | teacher day summaries | covered; overlapping intervals unioned |
| Admin permissions/HTTP | existing route contract | targeted HTTP test | route follows same admin guard |
| Batch/query-count proof | n/a | not yet measured; per-teacher projection fan-out | same remaining risk |

Direct artifacts: [teacher range contract](../../../SCHEDULE_TEACHER_RANGE_CONTRACT.md),
[teacher analytics contract](../../../TEACHER_LOAD_ANALYTICS.md), and
[class analytics contract](../../../CLASS_LOAD_ANALYTICS.md). Draft PRs:
[#52](https://github.com/Surpd/my-island/pull/52),
[#53](https://github.com/Surpd/my-island/pull/53),
[#56](https://github.com/Surpd/my-island/pull/56).

The missing batch/query-count measurement is a release blocker for large
schools, not hidden behind the MVP acceptance claim. Real HTTP coverage exists
for the teacher route; class HTTP coverage and PostgreSQL latency/isolation
checks remain follow-up work.
