# Teacher Directory reconciliation apply

Applied to production on 2026-09-08 from `Расписание 2026/27` → `Учителя и группы — данные`.

## Foundation

- Created canonical audience: `instructional:литература:11:база`.
- Roster rule: canonical Grade 11 class minus canonical Literature EGE.
- Added 7 student memberships; the 6 Literature EGE memberships and the original Grade 11 memberships were not changed.
- No identities, source snapshots, or audit history were deleted.

## Assignment apply plan

### CREATE — 12

| Teacher | Subject | Audience |
| --- | --- | --- |
| Антон | География | 9-А |
| Антон | География | 9-Д |
| Антон | Обществознание | 9 · База |
| Антон | Обществознание | 9 · ОГЭ |
| Ирина Анатольевна | Литература | 11 · База |
| Леонид | История | 10 |
| Леонид | История | 11 |
| Леонид | Обществознание | 10 · База |
| Леонид | Обществознание | 10 · Угл |
| Леонид | Обществознание | 11 · База |
| Юлия | Литература | 9-А |
| Юлия | Литература | 9-Д |

The table contains the 12 real created records.

### UPDATE — 6 dimension metadata rows

Existing relationships only; no teacher/audience relationship changed:

- Анастасия · Биология · Biology EGE 10: subject subgroup set to ЕГЭ.
- Дмитрий Филиппов · Математика · Grade 9 Math A/B/C: base class set to 9.
- Елена Викторовна · Литература · Literature EGE 11: subject subgroup set to ЕГЭ.
- Родион · Химия · Chemistry EGE 10: subject subgroup set to ЕГЭ.

### DEACTIVATE — 12

All were `official_import` assignments absent from the authoritative structured source and were ended with `valid_until`; history was retained.

| Teacher | Subject | Current audience | Superseded by |
| --- | --- | --- | --- |
| Антон | География | 11 | Structured source; Grade 11 Geography is computed schedule audience |
| Елена Викторовна | Литература | 8 | Structured source; Elena teaches Russian in Grade 8 |
| Елена Викторовна | Литература | 9-А | Structured source; Yulia teaches Literature 9-А |
| Елена Викторовна | Литература | 9-Д | Structured source; Yulia teaches Literature 9-Д |
| Ирина Анатольевна | Русский язык | 11 | Structured source; Tatyana teaches Russian 11 |
| Ирина Анатольевна | Литература | 7-1 | Structured source; no current assignment |
| Ирина Анатольевна | Литература | 7-2 | Structured source; no current assignment |
| Ирина Анатольевна | Литература | 11 | Structured source; replaced by Literature 11 Base |
| Татьяна | Русский язык | 8 | Structured source; Elena teaches Russian 8 |
| Татьяна | Литература | 11 | Structured source; Irina teaches Literature Base 11 |
| Юлия | Русский язык | 7-1 | Structured source; no current assignment |
| Юлия | Русский язык | 7-2 | Structured source; no current assignment |

### DELETE PHYSICALLY — 0

No physical deletion was necessary or performed.

### PROTECTED / CANNOT DECIDE — 0

## Final read-back

- Active student memberships: **488** (before: 481; delta: +7).
- Active teacher assignments: **125**.
- Active teachers: **24**.
- Duplicate active assignments: **0**.
- Unresolved teacher-directory issues: **0**.
- Conflicts: **0**.
- Computed schedule audiences: **1**; no permanent assignment was created for it.
- Final verification artifacts: `TEACHER_DIRECTORY_RECONCILIATION_FINAL_2026-09-08.md` and `.json`.
