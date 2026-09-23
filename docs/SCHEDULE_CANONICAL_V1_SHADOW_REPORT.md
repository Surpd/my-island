# Schedule canonical bootstrap v1 — shadow report

## Result

The first canonical routing baseline was built from the real `Расписание 2026/27` spreadsheet, tab `2026/27 шаблон`, and the current canonical directory. It remains shadow-only and is not connected to the authoritative production schedule.

- Canonical version: `4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93`.
- Source snapshot: `6cff68a6-0fca-4089-b750-ea67fa63242e`, observed `2026-09-10T09:14:19.623226+00:00`.
- Source fingerprint: `c145928d9058cb6bbe6d30283d3eedc97300c5f7cbae5724eca19e2862cf5699`.
- 396 meaningful source cells became 255 logical canonical blocks.
- 151 blocks are fully resolved; 104 contain an unresolved student route.
- The artifact's pre-triage counter calls 103 blocks `directory_blocker_blocks`: mechanically, each lacks enough canonical membership coverage to route every student. This counter is not the evidence-based `DIRECTORY_DEFECT` category. Root-cause triage splits those 103 blocks into 75 `DIRECTORY_DEFECT` blocks and 28 `SOURCE_AMBIGUITY`-only blocks; the remaining block is one `REAL_SOURCE_CONFLICT`.
- 114 active students were projected; all have routes and no student has two effective states for the same slot.
- 106 students are fully routable; eight have at least one `UNRESOLVED` route.
- Route states: 3,217 `ACTIVITY`, 1,022 final `NO_LESSON`, 114 `UNRESOLVED`.

The source evidence retained per block includes coordinates, raw text, source columns, audience labels, merged audiences, merge metadata, colors, source tab and a structural fingerprint. The current DB snapshot contains no separate notes field.

## Reconciliation of the 103-block counter

The original bootstrap used "directory blocker" as an implementation-level description: routing looked up a required membership and could not find one. It did not answer why that membership was unavailable. The later evidence review introduced mutually exclusive root-cause categories:

- `DIRECTORY_DEFECT`: the active identity and canonical base directory are inconsistent and need reconciliation;
- `SOURCE_AMBIGUITY`: the canonical directory agrees with the authoritative rosters, but those rosters do not state which instructional partition receives the student;
- `REAL_SOURCE_CONFLICT`: authoritative memberships are valid individually but produce simultaneous activities in the real template;
- `CANONICAL_INTERPRETATION_ERROR`: the canonical interpretation itself is wrong. None was found.

The 103 non-conflict blocked blocks reconcile exactly as follows:

| Root cause | Category | Blocks carrying this cause | Overlap with a directory-defect block | Mutually exclusive blocks |
|---|---|---:|---:|---:|
| Кулиш Владимир has no canonical base class | `DIRECTORY_DEFECT` | 35 | — | 35 |
| Коуттс Александра has no canonical base class | `DIRECTORY_DEFECT` | 40 | — | 40 |
| Холодова Татьяна is absent from Grade 9 Math A/B/C and English 6/7/8 source partitions | `SOURCE_AMBIGUITY` | 22 | 0 | 22 |
| Кулиш Александр is absent from the English 1/2 source partition | `SOURCE_AMBIGUITY` | 3 | 3 Grade 5 English blocks also blocked by Кулиш Владимир | 0 |
| Воронин Лаврентий is absent from the English 3/4/5 source partition | `SOURCE_AMBIGUITY` | 6 | 6 Grade 7 English blocks also blocked by Коуттс Александра | 0 |
| Кокурин Лев is absent from the English 3/4/5 source partition | `SOURCE_AMBIGUITY` | 6 | 0 | 6 |
| **Total** |  | **112 cause occurrences across 103 unique blocks** | **9** | **103 = 75 + 28** |

Thus 37 blocks carry an instructional `SOURCE_AMBIGUITY` cause, but nine of them also carry a base-class `DIRECTORY_DEFECT`. For the required one-primary-category-per-block accounting, those nine are counted under `DIRECTORY_DEFECT`; this leaves 28 `SOURCE_AMBIGUITY`-only blocks. At student-slot level the instructional ambiguities still account for all 37 affected routes: 22 + 3 + 6 + 6.

These are not a second copy of the base-membership defects. For Кулиш Владимир and Коуттс Александра, the active identity lifecycle conflicts with canonical base-class coverage. For the four instructional cases, the canonical DB faithfully matches the available authoritative instructional rosters, but the source itself omits the student from every candidate partition. The schedule proves that a partition is needed, but cannot prove which group is correct; assigning one would invent membership from schedule layout. That is why their primary cause is `SOURCE_AMBIGUITY`.

## Canonical audiences and memberships

The directory contains 51 relevant canonical groups:

- 9 base classes;
- 24 instructional-partition groups;
- 16 elective overlays;
- 2 special activity groups (`Курс по выбору`, `Цифровой трек`).

Role inference uses membership intersections and cohort coverage. A lexical `ОГЭ/ЕГЭ` marker does not override a complete, disjoint instructional partition. For example, English 8 and Literature 11 EGE are classified as instructional partitions in their respective layouts.

Important coverage:

| Partition | Group counts | Canonical cohort | Covered | Gap / overlap |
|---|---:|---:|---:|---|
| Grade 9 Math A/B/C | 5 / 11 / 12 | 29 | 28 | Холодова Татьяна missing; no overlap |
| Grade 9 English 6/7/8 | 7 / 6 / 15 | 29 | 28 | Холодова Татьяна missing; no overlap |
| Grade 5 English 1/2 | 3 / 3 | 7 | 6 | Кулиш Александр missing |
| Grade 6 English 1/2 | 7 / 9 | 16 | 16 | exact |
| Grade 7 English 3/4/5 | 9 / 8 / 6 | 24 | 23 | Воронин Лаврентий missing |
| Grade 8 English 3/4/5 | 1 / 5 / 6 | 13 | 12 | Кокурин Лев missing |
| Grade 10 English 9/10 | 4 / 6 | 10 | 10 | exact |
| Grade 11 English 9/10 | 7 / 6 | 13 | 13 | exact |
| Grade 11 Math base/advanced | 6 / 7 | 13 | 13 | exact |
| Grade 11 Literature base/EGE | 8 / 5 | 13 | 13 | exact |
| Grade 10 Social base/advanced | 7 / 3 | 10 | 10 | exact |
| Grade 11 Social base/advanced | 5 / 8 | 13 | 13 | exact |

Two active students are absent from canonical base-class memberships and therefore cannot be silently assigned from a class column: Кулиш Владимир (class 5) and Коуттс Александра (class 7-2). Their relevant routes are `UNRESOLVED`, not `NO_LESSON`.

English 6/7/8 are present in the current DB as canonical groups. Their memberships come from the prior reconciliation against the authoritative Google tab `списки групп 26-27`; the bootstrap consumes those memberships read-only and does not infer rosters from the schedule.

## Grade 9 canonical interpretation

All 43 logical Grade 9 rows are represented. Mode distribution: 11 `CLASS`, 11 `MATH`, 7 `ENGLISH`, 4 `MATH_WITH_ELECTIVES`, 10 `ELECTIVES`.

The L/M/N lane mapping is learned from repeated labelled anchors in this source corpus and stored as evidence for this layout; it is not a permanent production constant. For this snapshot it proves L/11 → Math A, M/12 → Math B, N/13 → Math C, with the analogous English 6/7/8 lanes.

Representative blocks:

- Tuesday 10:55, `L18 + N18`: Math A+B (16 students) → History of Art; Math C (12) → Mathematics; Холодова Татьяна → `UNRESOLVED` because she is outside A/B/C.
- Tuesday 11:50, `L19 + M19 + N19`: Math A (5) → Mathematics; Math B (11) → final `NO_LESSON` from the explicit lunch lane; Math C (12) → History of Art; Холодова Татьяна → `UNRESOLVED`.
- Monday 13:40, `L9:M9:N9`: English 6 (7) → Russian; English 7 (6) and English 8 (15) → English. `English 8 OGE` is resolved by canonical instructional-group semantics, not by an OGE exception. Холодова Татьяна → `UNRESOLVED`.
- Wednesday 13:40, `L33:M33:N33`: Math B (11) → Mathematics; six eligible remaining OGE members → Chemistry; 12 other proven remaining students → final `NO_LESSON`; Холодова Татьяна → `UNRESOLVED`.
- Monday 16:20, `L12:M12:N12`: Chemistry, Informatics and Literature OGE are elective overlays. Two students have contradictory overlapping memberships and remain `UNRESOLVED`; the other 27 students have one exact activity or final `NO_LESSON`.
- Friday 15:30, `L59:M59:N59`: Geography OGE (4) → Geography; class 9-D (14) → Training; 11 remaining class 9-A students → final `NO_LESSON`.

Sanity checks on reconstructed routes:

- Копылов Олег: Tue 11:50 Mathematics; Wed 13:40 `NO_LESSON`; Fri 15:30 `NO_LESSON`.
- Александрова Екатерина: Tue 11:50 `NO_LESSON`; Wed 13:40 Mathematics; Mon 13:40 English.
- Холодова Татьяна: Mon 13:40, Tue 11:50 and Wed 13:40 are `UNRESOLVED`; Mon 16:20 is Chemistry from her explicit OGE membership; Fri 15:30 is final `NO_LESSON`.

The only semantic conflict block is Monday 16:20: Куренков Иван belongs to Chemistry + Informatics OGE, and Жалнина Василиса belongs to Chemistry + Literature OGE. No precedence is invented.

## SDEP and simple activities

SDEP is outside the Grade 9 resolver. It is emitted only as a day-level activity for grades 10–11 when the source day label explicitly contains `SDEP`. The Friday marker observed in this template produces two day blocks; Friday alone is never sufficient.

Creativity, Training, Course Choice and Digital Track are valid activities without a required teacher. The Grade 7–8 programming/ШУМ/machine-graphics block is represented as Digital Track. Cells beginning with `⚪` are excluded as extracurricular circles. Lunch, pretzel and windows only become final `NO_LESSON`; they are not activities and do not create a mode-wide default.

## Incremental template and weekly flow

The retained incremental components now operate against this baseline:

1. Compare previous and current raw block fingerprints.
2. Reuse unchanged canonical blocks.
3. Apply deterministic metadata patches when only teacher/room/note metadata changes.
4. Send only changed ambiguous semantic blocks to the constrained interpreter.
5. Validate the returned audience against canonical memberships and create a new immutable canonical version.
6. Apply weekly patches to a copy of the canonical version; never mutate the template.

The weekly overlay now targets the canonical block by `(weekday, start, end, grade scope)` instead of the weekly sheet's own row key. This prevents duplicate canonical + weekly slots. A genuinely added or moved slot is stored as a weekly-only block.

Real DB-backed weekly evidence from `7-11 Сентября` (`week_start=2026-09-07`): 416 source cells — 270 same as baseline, 51 modified, 48 cancelled, 26 replaced, 20 added and one special event.

- Example deterministic patch: Grade 9 `N7` keeps Math C and changes only room `каб.17 → каб.10`; routing is reused unchanged.
- Example semantic weekly patch: Grade 9 `L7` changes `🥨 → Литература ОГЭ`; the overlay must allocate only canonical Literature OGE members and recompute the remaining final states.
- Example structural move: the Tuesday Grade 9 OGE set moves from 15:25 to 15:30 as cancellation + additions; it becomes a weekly-only slot while the old canonical slot receives its cancellation overlay.

There is no second authoritative template snapshot in the current DB, so a real historical template-to-template semantic patch cannot be demonstrated yet. The patch planner and immutable weekly overlay are covered by tests; the next template snapshot will supply that comparison without reparsing unchanged blocks.

## Readiness

- Current bootstrap verdict: **READY_FOR_MANUAL_RECONCILIATION**. The artifact is versioned, traceable to the live snapshot, contains exact student sets, and exposes every unresolved route.
- Authoritative production switch: **NO-GO**.

The complete root-cause classification of all 114 unresolved student-slots and the single final human reconciliation queue are recorded in `SCHEDULE_CANONICAL_V1_TRIAGE.md`. That queue is authoritative; this report intentionally does not maintain a second blocker list. No canonical assignment was changed during triage because no proven `CANONICAL_INTERPRETATION_ERROR` was found.

The existing constrained schema, validation/allocation, versioning, patch planning and weekly overlay are kept. Full-template Groq bootstrap orchestration remains shadow-only and is no longer on the critical path; future AI is limited to ambiguous changed blocks.
