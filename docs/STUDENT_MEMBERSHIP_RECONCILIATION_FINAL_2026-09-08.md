# Student Membership Dry-Run — 2026-09-08

Режим: **READ-ONLY**. Production apply не выполнялся.

## Summary

- Canonical students inspected: **125**
- Current active memberships: **490**
- Expected active memberships: **489**
- KEEP: **489**; CREATE: **0**; DEACTIVATE: **0**; PROTECTED: **1**; UNRESOLVED: **0**
- Duplicate memberships: **0**; wrong-group: **0**; stale: **0**
- Ambiguous identities: **0**; manual identity pending: **0**; ambiguous groups: **0**; source contradictions: **0**
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

- Current: **8**, expected: **8**.
- Current-only: —; expected-only: —.

## Known edge cases

- **Иващенко Фёдор** — excluded_current_structure; current: —; expected: —.
- **Нестерова Алиса** — excluded_current_structure; current: —; expected: —.
- **Холодова Татьяна** — UNRESOLVED; current: 9-А, instructional:биология:9:огэ, instructional:химия:9:огэ; expected: instructional:биология:9:огэ, instructional:химия:9:огэ.
- **Куренков Иван** — deterministically reconciled; current: 9-Д, english:7, grade9-math-C, instructional:информатика:9:огэ, instructional:обществознание:9:база, instructional:физика:9:огэ, instructional:химия:9:огэ; expected: 9-Д, english:7, grade9-math-C, instructional:информатика:9:огэ, instructional:обществознание:9:база, instructional:физика:9:огэ, instructional:химия:9:огэ.

## Future structural preparation


## Proposed deactivations

Каждая строка ниже также полностью сохранена в JSON с membership id, provenance, source ref, stale reason, authoritative source, structural rule, confidence и why-safe.

| Student | Group | Membership | Source ref | Classification |
|---|---|---|---|---|

## Assignment diff

Подробный machine-readable diff находится в JSON рядом с этим отчётом.

## Safety

- Не создавались и не менялись identities/groups.
- Не менялись teacher assignments.
- Не изменялись memberships и validity periods.
- Следующий apply-pass должен быть отдельным явно разрешённым действием и не должен принимать JSON как безусловную команду.
