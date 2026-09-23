# Student Membership Dry-Run — 2026-09-08

Режим: **READ-ONLY**. Production apply не выполнялся.

## Summary

- Canonical students inspected: **123**
- Current active memberships: **488**
- Expected active memberships: **484**
- KEEP: **470**; CREATE: **14**; DEACTIVATE: **17**; PROTECTED: **1**; UNRESOLVED: **3**
- Duplicate memberships: **0**; wrong-group: **3**; stale: **14**
- Ambiguous identities: **0**; manual identity pending: **3**; ambiguous groups: **0**; source contradictions: **0**
- Invariant violations: **0**
- Computed audiences intentionally skipped: **3**
- Production source snapshot rows inspected: **10**
- Production writes performed: **0**

## Invariants

- **global_base_class_at_most_one** — PASS. "current active memberships"
- **global_no_duplicate_logical_membership** — PASS. []
- **grade9_math_exactly_one** — PASS_WITH_KNOWN_EXCEPTION. []
- **grade9_english_exactly_one** — PASS_WITH_KNOWN_EXCEPTION. []
- **grade11_math_xor** — PASS. []
- **grade11_society_xor** — PASS. []
- **grade11_literature_xor** — PASS. []
- **forbidden_active_canonical_memberships_absent** — PASS. []

## Human confirmation required

Нет вопросов, требующих ручного выбора.

## Computed audiences not materialized

- Grade 11 students without Society EGE join Geography 10 lesson — computed schedule audience, not permanent membership.
- Остальные = base slot minus routed parallel audiences — computed audience, not canonical group.
- shared Physics/History 10-11 lessons — shared lesson does not merge canonical groups.

## Literature 11 Base recheck

- Current: **7**, expected: **8**.
- Current-only: —; expected-only: Бруннер Агата.

## Known edge cases

- **Иващенко Фёдор** — excluded_current_structure; current: —; expected: —.
- **Нестерова Алиса** — excluded_current_structure; current: —; expected: —.
- **Холодова Татьяна** — UNRESOLVED; current: 9-А, instructional:биология:9:огэ, instructional:химия:9:огэ; expected: instructional:биология:9:огэ, instructional:химия:9:огэ.
- **Куренков Иван** — deterministically reconciled; current: 9-Д, english:7, grade9-math-C, instructional:информатика:9:огэ, instructional:обществознание:9:база, instructional:физика:9:огэ, instructional:химия:9:огэ; expected: 9-Д, english:7, grade9-math-C, instructional:информатика:9:огэ, instructional:обществознание:9:база, instructional:физика:9:огэ, instructional:химия:9:огэ.

## Future structural preparation

- `instructional:история:9:огэ` — group creation is proposed for a future apply pass only; production was not changed.

## Proposed deactivations

Каждая строка ниже также полностью сохранена в JSON с membership id, provenance, source ref, stale reason, authoritative source, structural rule, confidence и why-safe.

| Student | Group | Membership | Source ref | Classification |
|---|---|---|---|---|
| Болотова Ульяна | english:5 | `ebd4c3c7-f2c5-458e-82df-053b3070f9c2` | списки групп 26-27!J27 | WRONG_GROUP |
| Бруннер Агата | instructional:литература:11:егэ | `8949bbe5-8d80-4d6b-8051-13ece6b48059` | списки групп 26-27!R51 | STALE |
| Коуттс Александра | 7-2 | `4d7229d8-1aa2-40c5-8ca9-3627e218f8d8` | списки групп 26-27!H11 | STALE |
| Коуттс Александра | generic:course-choice | `b4f77365-ec70-4776-abaf-d33b40a3c656` | target-rule:grades-5-8 | STALE |
| Коуттс Александра | generic:digital-track | `c0c4fb3b-d588-46bb-b789-8ea5d49b54ee` | target-rule:grades-7-8 | STALE |
| Кулиш Владимир | 5 | `a2125327-652c-4352-8aa9-f6e83a4d250d` | Списки по классам 26/27!B4 | STALE |
| Кулиш Владимир | generic:course-choice | `601e90b1-fbee-4e6e-8178-bdbcc51d8a35` | target-rule:grades-5-8 | STALE |
| Маркова Иванка | 7-1 | `b9ec93a3-6534-4f96-92e8-4c949103d1dd` | списки групп 26-27!F11 | STALE |
| Михалюк Максим | generic:course-choice | `63114405-6e3e-40e5-b0bc-c368cde54cd2` | target-rule:grades-5-8 | STALE |
| Михалюк Максим | generic:digital-track | `159ce257-48e9-45f3-9861-6646b3230573` | target-rule:grades-7-8 | STALE |
| Осипов Артем | instructional:информатика:9:база | `fca37754-eacc-48a1-b50c-36777540fa68` | списки групп 26-27!J94 | STALE |
| Осипчук Всеволод | 7-2 | `f5ec3ecc-5622-40a8-aa18-e8da8f746408` | списки групп 26-27!H14 | STALE |
| Осипчук Всеволод | english:4 | `48c8d17b-4e79-4a2e-941a-0326b740f4cb` | списки групп 26-27!H33 | WRONG_GROUP |
| Сахаров Лев | 7-2 | `041ea3a2-7d62-470a-ba90-f67b65a0c5bb` | списки групп 26-27!H15 | STALE |
| Соколов Федор | english:5 | `c4b2d5bd-2730-471c-849b-fe955c87d90f` | списки групп 26-27!J38 | WRONG_GROUP |
| Чтак Северина | 7-1 | `f51bda4b-b159-4ddd-9bb1-67c82f811786` | списки групп 26-27!F15 | STALE |
| Чудайкина Ярослава | 7-1 | `e367c940-fd0d-40a5-b05f-a8897d1d680a` | списки групп 26-27!F16 | STALE |

## Assignment diff

Подробный machine-readable diff находится в JSON рядом с этим отчётом.

## Safety

- Не создавались и не менялись identities/groups.
- Не менялись teacher assignments.
- Не изменялись memberships и validity periods.
- Следующий apply-pass должен быть отдельным явно разрешённым действием и не должен принимать JSON как безусловную команду.
