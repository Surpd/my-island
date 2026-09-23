# Schedule canonical v1 — unresolved root-cause triage

## Verdict

**READY_FOR_MANUAL_RECONCILIATION**

Canonical interpretation errors were not found. The canonical artifact remains version
`4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93`; no routing assignment was changed.

Before triage: 114 `UNRESOLVED` student-slots, 104 blocked canonical blocks, 151 fully resolved blocks, zero duplicate effective allocations.

After triage: 114 `UNRESOLVED` student-slots, 104 blocked canonical blocks, 151 fully resolved blocks, zero duplicate effective allocations. Nothing was converted to `NO_LESSON`, because none of the remaining cases can be resolved from authoritative evidence without a human decision.

## Root causes

| Root cause | Primary category | Students affected | Blocks affected | Student-slots affected | Recommended action |
|---|---|---:|---:|---:|---|
| Active identity has no canonical base class: Кулиш Владимир | `DIRECTORY_DEFECT` | 1 | 35 | 35 | Reconcile identity lifecycle and class 5 membership; do not blindly restore the old membership |
| Active identity has no canonical base class: Коуттс Александра | `DIRECTORY_DEFECT` | 1 | 40 | 40 | Confirm whether the student is still active; then either restore a proven 7-1/7-2 membership or mark/correct the identity |
| No authoritative Grade 9 Math or English partition for Холодова Татьяна | `SOURCE_AMBIGUITY` | 1 | 22 | 22 | Human must specify one Math A/B/C group and one English 6/7/8 group, or explicitly confirm an alternative programme |
| English partition omits Кулиш Александр | `SOURCE_AMBIGUITY` | 1 | 3 | 3 | Human must select English 1 or 2 |
| English partition omits Воронин Лаврентий | `SOURCE_AMBIGUITY` | 1 | 6 | 6 | Human must select English 3, 4 or 5 |
| English partition omits Кокурин Лев | `SOURCE_AMBIGUITY` | 1 | 6 | 6 | Human must select English 3, 4 or 5 |
| Monday 16:20 contains simultaneous OGE groups with overlapping authoritative memberships | `REAL_SOURCE_CONFLICT` | 2 | 1 | 2 | Decide one activity for each affected student in that slot, or add an explicit alternation rule to the source |

The root-cause rows account for all 114 unresolved student-slots exactly. Block counts overlap: three Grade 5 English blocks contain both the Кулиш Владимир base defect and the Кулиш Александр English ambiguity; six Grade 7 English blocks contain both the Коуттс base defect and the Воронин English ambiguity.

For a mutually exclusive primary classification of all 104 blocked blocks:

- 75 blocks: `DIRECTORY_DEFECT` caused by an active identity outside canonical base classes;
- 28 blocks: `SOURCE_AMBIGUITY` only;
- 1 block: `REAL_SOURCE_CONFLICT`;
- 0 blocks: `CANONICAL_INTERPRETATION_ERROR`.

The bootstrap JSON field `directory_blocker_blocks=103` predates this evidence-based classification. It means only that 103 blocks encountered missing canonical membership coverage during routing; it does not mean that all 103 are true `DIRECTORY_DEFECT`s. The decomposition is:

```text
75 base-class DIRECTORY_DEFECT blocks
+ 37 blocks carrying SOURCE_AMBIGUITY
-  9 blocks carrying both causes
= 103 unique non-conflict blocked blocks
```

The nine overlaps are three Grade 5 English blocks (Кулиш Владимир base defect + Кулиш Александр English ambiguity) and six Grade 7 English blocks (Коуттс Александра base defect + Воронин Лаврентий English ambiguity). Assigning one primary category per block places those overlaps under `DIRECTORY_DEFECT`, leaving `37 - 9 = 28` `SOURCE_AMBIGUITY`-only blocks.

The 28 are grouped as 22 Grade 9 Math/English blocks affected by Холодова Татьяна and six Grade 8 English blocks affected by Кокурин Лев. Кулиш Александр's three and Воронин Лаврентий's six ambiguity blocks are real additional root causes, but their blocks are already counted in the 75-block primary `DIRECTORY_DEFECT` bucket because of the overlaps above.

This is ambiguity rather than another directory defect because current canonical memberships agree with the authoritative instructional rosters: those rosters themselves omit each student from every candidate group. They establish neither an expected group nor evidence that the DB missed a stated membership. The schedule establishes only that an instructional partition is active; it cannot determine the student's group without inventing membership.

## Base-class gaps

### Кулиш Владимир

Current state:

- active identity, `class_name=5`;
- active canonical membership only in `english:1`;
- no active canonical class 5 membership;
- live `списки групп 26-27` contains him in Math 5 (`B8`) and English 1 (`B28`);
- live `Списки по классам 26/27` does not contain him; current `B4` contains Кулиш Александр.

History matters here. The 7 September source snapshot contained Кулиш Владимир at `Списки по классам 26/27!B4`, but that observation was manually mapped to the distinct identity Кулиш Александр. The reviewed production apply then deliberately deactivated Кулиш Владимир's class 5 and course-choice memberships as stale. Therefore the schedule must not silently recreate class membership.

Classification: `DIRECTORY_DEFECT`, specifically an active-identity/base-membership and provenance inconsistency. Confidence that he belongs to the Grade 5 instructional population is high from two current group rosters, but the exact lifecycle/base-roster correction still requires confirmation.

Recommended action: confirm that Кулиш Владимир is an active Grade 5 student distinct from Кулиш Александр. If confirmed, add canonical class `5`, restore applicable stable Grade 5 memberships, and replace the stale `B4` identity provenance with current evidence. Otherwise mark/correct the identity so it is not part of the active schedule cohort.

### Коуттс Александра

Current state:

- active identity, `class_name=7-2`;
- zero active memberships;
- no match in the current live class roster or current `списки групп 26-27`;
- the identity predates the 8 September reconciliation; it was not added after canonical v1;
- historical source records contained her at class `F9` and group split `H11`;
- the reviewed production apply deliberately deactivated `7-2`, course-choice and digital-track memberships as stale.

Current cell `F9` no longer contains Коуттс Александра, so the stored identity source reference is not usable as current proof.

Classification: `DIRECTORY_DEFECT`, specifically an active identity whose roster-backed memberships were ended while the identity remained active.

Recommended action: confirm whether she is still enrolled. If yes, obtain a current authoritative class/split row and restore the proven base group plus applicable Grade 7 stable groups. If no, end/correct active student status. Do not infer English until the identity/base question is settled.

## Instructional partition gaps

### Холодова Татьяна

- Base class `9-А` is manually protected and retained.
- Authoritative memberships: Biology OGE (`B73`) and Chemistry OGE (`N76`).
- She is absent from Math A/B/C and English 6/7/8 rosters.
- `ОГЭ/ЕГЭ` explicitly shows `-` for her Math group and no English selection.
- The authoritative reconciliation already records this as the sole known Grade 9 Math/English exception.

Classification: `SOURCE_AMBIGUITY`. The canonical directory accurately reflects the available sources; the source does not state which partition receives her. This causes 15 Math and seven English unresolved slots.

Manual action: provide Math A/B/C and English 6/7/8, or explicitly document that she follows a different programme and define its routing semantics.

### Кулиш Александр

- Current base class 5 is confirmed at `Списки по классам 26/27!B4`.
- He does not appear in either English 1 or English 2 in the current live instructional roster.

Classification: `SOURCE_AMBIGUITY`, affecting three English slots. Manual action: choose English 1 or English 2.

### Воронин Лаврентий

- Current Grade 7 base/split evidence is present; canonical group is `7-2`.
- He does not appear in English 3/4/5.

Classification: `SOURCE_AMBIGUITY`, affecting six English slots. Manual action: choose English 3, 4 or 5.

### Кокурин Лев

- Current class 8 is confirmed at `Списки по классам 26/27!J14`.
- He does not appear in English 3/4/5.

Classification: `SOURCE_AMBIGUITY`, affecting six English slots. Manual action: choose English 3, 4 or 5.

These four instructional gaps are not `DIRECTORY_DEFECT`: the canonical DB is consistent with the authoritative instructional roster. The missing information is in the school source itself, so no exact group can be recommended safely.

## Monday 16:20 real source conflict

Template evidence:

- `2026/27 шаблон!L12`: Chemistry OGE practicum;
- `2026/27 шаблон!M12`: Informatics OGE;
- `2026/27 шаблон!N12`: Literature OGE;
- all three cells are separate, unmerged, on Monday 16:20–17:05;
- visual inspection shows three parallel colored lanes;
- no note, alternation marker or residual/default wording exists.

The lanes cannot be restricted to the class headers. Chemistry contains students from both 9-А and 9-Д, while Literature also contains students from both classes. The columns are therefore parallel activity lanes for the Grade 9 cohort, not independent class ownership.

Membership evidence:

- Куренков Иван: Chemistry OGE (`N74`) and Informatics OGE (`L88`) are both authoritative active roster memberships.
- Жалнина Василиса: Chemistry OGE (`N77`) and Literature OGE (`N52`) are both authoritative active roster memberships.

Classification: `REAL_SOURCE_CONFLICT`. Multiple OGE memberships are legitimate; the conflict exists only because these memberships meet in the same real slot.

Human messages:

> Monday 16:20 — Иван Куренков состоит в Chemistry OGE и Informatics OGE. Обе группы стоят одновременно. Нужно выбрать его занятие в этом слоте или явно указать правило чередования.

> Monday 16:20 — Василиса Жалнина состоит в Chemistry OGE и Literature OGE. Обе группы стоят одновременно. Нужно выбрать её занятие в этом слоте или явно указать правило чередования.

No membership should be removed globally to solve this one-slot conflict.

## Other source warnings and collisions

The nine parser-level source warnings do not add student-routing unresolved states:

- `C52`, `C53`, `C54`, `M59`, `N59`: teacher absent. This is allowed for the corresponding simple activities and training.
- `F43`, `G46`, `I30`: training teacher text/color resolution differs, but teacher is optional and audience routing is proven.
- `N24`: Physics OGE practicum has a teacher-only ambiguity (`АнК` in text versus a different scoped color mapping). Audience and student routing are resolved; leave teacher unset until confirmed.

No additional student collision was found. Duplicate effective allocations remain zero.

## Final reconciliation queue

This is the single authoritative queue for canonical bootstrap v1.

1. Кулиш Владимир — confirm active Grade 5 status and identity distinction; then restore class `5` and stable Grade 5 memberships, or correct/end the active identity.
2. Коуттс Александра — confirm current enrollment; if active, obtain a current authoritative class/split row and restore exactly that route; otherwise end/correct the active identity. Do not reuse the stale 7-2 evidence as current proof.
3. Холодова Татьяна — specify Math A/B/C and English 6/7/8, or provide explicit alternative routing.
4. Кулиш Александр — specify English 1/2.
5. Воронин Лаврентий — specify English 3/4/5.
6. Кокурин Лев — specify English 3/4/5.
7. Monday 16:20 — select Chemistry or Informatics for Куренков Иван.
8. Monday 16:20 — select Chemistry or Literature for Жалнина Василиса.
9. Optional teacher cleanup: confirm the Physics OGE practicum teacher in `N24`.
10. After items 1–8, rebuild the shadow projection and require zero unexplained `UNRESOLVED` routes and zero duplicate allocations before a separate manual acceptance decision.

Production schedule remains unchanged. Production memberships were not modified during this triage.
