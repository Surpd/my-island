# Schedule Canonical V2 — Grade 7 Row-Level Correction Report

Date: 2026-09-13  
Status: `READY_FOR_GRADE7_MEMBERSHIP_MIGRATION`  
Canonical v1: unchanged  
Memberships: unchanged  
Production UI: unchanged

## Result

The accepted Grade 7 model is preserved:

```text
base_class: 7-А | 7-Б
shared_split: 1 | 2
english_group: 3 | 4 | 5
elective/special memberships
```

The row-level correction is now applied in the candidate. A complete F/G row is
classified first; only then are its parallel assignments routed.

## Row-level corrections

| Rows | Previous interpretation | Corrected interpretation | Evidence |
|---|---|---|---|
| F5 / G5 | Мат 1 / Русский | split 1 → Математика; split 2 → Русский язык | F5 explicitly marks split 1; the repeated paired F/G layout supplies the complementary split 2. |
| F6 / G6 | Физ 1 / Пластика | split 1 → Физика; split 2 → Пластика | F6 explicitly marks split 1; the parallel activity is the other side of the same shared row. |
| F9 / G9 | Русский / Мат 2 | split 1 → Русский язык; split 2 → Математика | G9 explicitly marks split 2; the parallel F9 activity is complementary split 1. |
| F10 / G10 | Пластика / Физ 2 | split 1 → Пластика; split 2 → Физика | G10 explicitly marks split 2; the parallel F10 activity is complementary split 1. |
| F22 / G22 | Пластика / Мат 2 | split 1 → Пластика; split 2 → Математика | G22 explicitly marks split 2; the parallel F22 activity is complementary split 1. |
| F23 / G23 | Мат 1 / Пластика | split 1 → Математика; split 2 → Пластика | F23 explicitly marks split 1; the parallel activity is complementary split 2. |

Changed routing dimension: **6 assignments**.  
Removed from semantic assignments: **6 🥨 markers** and **4 ⚪ extracurricular cells**.  
Current candidate: **39 logical blocks / 61 semantic assignments**.

## Shared split rows

The following source rows prove that the same 1/2 partition is reused across
subjects. The number on the second side is inferred only when the paired row
evidence makes the complement unambiguous.

| Day | Time | Split 1 source | Split 1 activity | Split 2 source | Split 2 activity |
|---|---|---|---|---|---|
| Пн | 9:55-10:40 | F5 | Математика | G5 | Русский язык |
| Пн | 10:55 -11:40 | F6 | Физика | G6 | Пластика |
| Пн | 13:40 - 14:25 | F9 | Русский язык | G9 | Математика |
| Пн | 14:35 -15:20 | F10 | Пластика | G10 | Физика |
| Вт | 14:35 -15:20 | F22 | Пластика | G22 | Математика |
| Вт | 15:25 - 16:10 | F23 | Математика | G23 | Пластика |
| Ср | 10:55 -11:40 | F30 | Биология | G30 | Математика |
| Ср | 13:40 - 14:25 | F33 | Математика | G33 | Биология |
| Ср | 14:35 -15:20 | F34 | Физика | G34 | Русский язык |
| Ср | 15:30 - 16:15 | F35 | Русский язык | G35 | Физика |
| Пт | 12:45-13:30 | F56 | Математика | G56 | Русский язык |
| Пт | 13:40 - 14:25 | F57 | Русский язык | G57 | Математика |

Subjects using the shared split: **Математика, Физика, Биология, Русский язык**.

No subject-specific Grade 7 Physics/Biology/Russian roster was found. The current
group roster contains one disjoint 7-1/7-2 partition (11/13), so the candidate
references these existing IDs provisionally as neutral shared split 1/2. No new
subject-specific memberships are proposed.

## Base-class rows

Base-class routing is used only for explicitly class-based rows such as
`Литература 7-А | История 7-Б`, `Биология 7-А | Литература 7-Б`,
`География 7-А | История искусства 7-Б`, and equivalent rows.

The source class roster contains 12 students in 7-А and 12 in 7-Б. These are
independent from shared split 1/2; no 7-А = 7-1 or 7-Б = 7-2 assumption is used.

## English rows

English remains a separate partition. Current template rows route only to the
explicit English groups 3, 4, and 5; a neighboring blank or 🥨 cell does not become
a base-class lesson. The non-covered English students receive the row's residual
state, not a fabricated base-class assignment.

Current source roster totals are English 3/4/5 = **10/13/12** across Grades 7–8.
The live DB Grade 7 subset is **9/8/6**.

## Excluded and residual rows

- `🥨` is never stored as an activity or assignment. It produces only a final
  residual `NO_LESSON` state after real audiences are resolved.
- Four `⚪` rows (Айкидо, Испанский, Вокал and the other circle marker) are
  excluded from the canonical school schedule.
- Programming, Machine Graphics, and ШУМ/Медиамастерская remain
  `ELECTIVE_OR_SPECIAL` digital-track activities.
- Course-choice remains `ELECTIVE_OR_SPECIAL`; it is not converted into a
  shared split.

## Provisional student projection

Projection used the current active DB identities and existing memberships as
provisional evidence only. No DB rows were changed.

| Metric | Result |
|---|---:|
| Active DB Grade 7 identities | 25 |
| Current class-roster identities mapped | 24 |
| Shared split 1 / 2 memberships | 11 / 13 |
| Shared split overlap | 0 |
| English 3 / 4 / 5 memberships in Grade 7 | 9 / 8 / 6 |
| Routed Grade 7 rows | 32 |
| ACTIVITY | 633 |
| NO_LESSON | 131 |
| UNRESOLVED | 36 |
| Duplicate student-slot allocations | 0 |
| Multiple distinct activities after filtering | 0 |
| Unknown group references | 0 |
| Unexplained unresolved | 0 |

The 36 unresolved states are known directory gaps, not routing conflicts:
12 are the extra active DB identity without current base-class mapping, 12 are
the same identity without shared-split membership, and 12 cover two identities
without Grade 7 English membership across six English rows.

Remaining manual items are the four source-name/identity aliases in the 7-1/7-2
math roster comparison. They must be reviewed before applying memberships.

## Human-readable row matrix

| Day | Time | Route by | Group 1/A/English-X → activity | Group 2/B/English-Y → activity |
|---|---|---|---|---|
| Пн | 9:00 - 9:45 | BASE_CLASS_A_B | 7-А → Кл. час Иван | — |
| Пн | 9:55-10:40 | SHARED_SPLIT_1_2 | split 1 → Математика | split 2 → Русский язык |
| Пн | 10:55 -11:40 | SHARED_SPLIT_1_2 | split 1 → Физика | split 2 → Пластика |
| Пн | 11:50 - 12:35 | ENGLISH_3_4_5 | English 3 → Английский язык | NO_LESSON |
| Пн | 12:45-13:30 | ENGLISH_3_4_5 | English 4 → Английский язык | English 5 → Английский язык |
| Пн | 13:40 - 14:25 | SHARED_SPLIT_1_2 | split 1 → Русский язык | split 2 → Математика |
| Пн | 14:35 -15:20 | SHARED_SPLIT_1_2 | split 1 → Пластика | split 2 → Физика |
| Пн | 15:30 - 16:15 | ELECTIVE_OR_SPECIAL | excluded | — |
| Вт | 9:55-10:40 | BASE_CLASS_A_B | 7-А → Литература | 7-Б → История |
| Вт | 10:55 -11:40 | BASE_CLASS_A_B | 7-А → Биология | 7-Б → Литература |
| Вт | 11:50 - 12:35 | BASE_CLASS_A_B | 7-А → История | 7-Б → Биология |
| Вт | 12:45-13:30 | ENGLISH_3_4_5 | English 3 → Английский язык | NO_LESSON |
| Вт | 13:40 - 14:25 | ENGLISH_3_4_5 | English 4 → Английский язык | English 5 → Английский язык |
| Вт | 14:35 -15:20 | SHARED_SPLIT_1_2 | split 1 → Пластика | split 2 → Математика |
| Вт | 15:25 - 16:10 | SHARED_SPLIT_1_2 | split 1 → Математика | split 2 → Пластика |
| Вт | 16:20 -17:05 | ELECTIVE_OR_SPECIAL | excluded | — |
| Ср | 9:00 - 9:45 | BASE_CLASS_A_B | 7-А → Литература | 7-Б → История |
| Ср | 9:55-10:40 | BASE_CLASS_A_B | 7-А → История | 7-Б → Литература |
| Ср | 10:55 -11:40 | SHARED_SPLIT_1_2 | split 1 → Биология | split 2 → Математика |
| Ср | 11:50 - 12:35 | NO_LESSON | NO_LESSON | NO_LESSON |
| Ср | 12:45-13:30 | ELECTIVE_OR_SPECIAL | 7-А → Курс по выбору | — |
| Ср | 13:40 - 14:25 | SHARED_SPLIT_1_2 | split 1 → Математика | split 2 → Биология |
| Ср | 14:35 -15:20 | SHARED_SPLIT_1_2 | split 1 → Физика | split 2 → Русский язык |
| Ср | 15:30 - 16:15 | SHARED_SPLIT_1_2 | split 1 → Русский язык | split 2 → Физика |
| Ср | 16:20 -17:05 | ELECTIVE_OR_SPECIAL | — | excluded |
| Чт | 9:00 - 9:45 | BASE_CLASS_A_B | 7-А → География | 7-Б → История |
| Чт | 9:55-10:40 | BASE_CLASS_A_B | 7-А → История | 7-Б → География |
| Чт | 10:55 -11:40 | BASE_CLASS_A_B | 7-А → Литература | 7-Б → Русский язык |
| Чт | 11:50 - 12:35 | BASE_CLASS_A_B | 7-А → Тренинг | 7-Б → Литература |
| Чт | 12:45-13:30 | NO_LESSON | NO_LESSON | NO_LESSON |
| Чт | 13:40 - 14:25 | BASE_CLASS_A_B | 7-А → Творчество | 7-Б → Творчество |
| Чт | 14:35 -15:20 | BASE_CLASS_A_B | 7-А → Русский язык | 7-Б → Тренинг |
| Чт | 15:30 - 16:15 | ELECTIVE_OR_SPECIAL | excluded | — |
| Пт | 9:00 - 9:45 | ENGLISH_3_4_5 | English 3 → Английский язык | — |
| Пт | 9:55-10:40 | ENGLISH_3_4_5 | English 4 → Английский язык | English 5 → Английский язык |
| Пт | 10:55 -11:40 | ELECTIVE_OR_SPECIAL | 7-А → Цифровой трек | 7-Б → Цифровой трек |
| Пт | 11:50 - 12:35 | ELECTIVE_OR_SPECIAL | 7-А → Цифровой трек | 7-Б → Цифровой трек |
| Пт | 12:45-13:30 | SHARED_SPLIT_1_2 | split 1 → Математика | split 2 → Русский язык |
| Пт | 13:40 - 14:25 | SHARED_SPLIT_1_2 | split 1 → Русский язык | split 2 → Математика |

## Candidate

Candidate file:
`docs/SCHEDULE_CANONICAL_V2_GRADE7_CANDIDATE.json`

- version: `candidate-v2-grade7-fd6d3f3beccca0fc`;
- parent v1: `4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93`;
- current template fingerprint: `c4532841c9daf9a7`;
- status: `candidate`;
- persistence: not performed.

## Verdict

`READY_FOR_GRADE7_MEMBERSHIP_MIGRATION`

The canonical correction is complete: shared rows are routed as whole parallel
blocks, base-class rows remain base-class rows, English remains independent, and
parallel assignments no longer create false conflicts. The next operation is a
controlled/manual membership migration after reviewing the four identity aliases;
production UI and canonical v1 remain untouched.

