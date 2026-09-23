# Student Membership Production Apply — 2026-09-08

## 1. Pre-apply safety gate

- PASS: production baseline matched reviewed artifact: 488 active memberships.
- Reviewed deactivation IDs/provenance revalidated before mutation.
- Ambiguous identities/groups: 0/0; source contradictions: 0; duplicates: 0.
- Materialized plan: 19 CREATE and 17 DEACTIVATE; the five additional CREATE memberships belong only to Кулиш Александр and Воронин Лаврентий.

## 2–4. Production mutations

- Identities created: **2** — Кулиш Александр, Воронин Лаврентий.
- Groups created: **1** — `instructional:история:9:огэ` / `История · 9 класс · ОГЭ`.
- Memberships created: **19**.
- Memberships deactivated: **17**; rows were retained and ended with validity semantics.
- Physical deletes: **0**; teacher assignments and schedule audiences untouched.

### Membership CREATE list

- Болотова Ульяна → `english:4`
- Бруннер Агата → `instructional:литература:11:база`
- Маркова Иванка → `7-2`
- Никифоров Илья → `math:11:advanced`
- Осипов Артем → `instructional:информатика:9:огэ`
- Осипчук Всеволод → `7-1`, `english:5`
- Провоторова Мария → `generic:course-choice`, `generic:digital-track`
- Сахаров Лев → `7-1`
- Соколов Федор → `english:4`
- Хлопов Саша → `instructional:химия:9:огэ`
- Чтак Северина → `7-2`
- Чудайкина Ярослава → `7-2`
- Воронин Лаврентий → `7-2`, `generic:course-choice`, `generic:digital-track`
- Кулиш Александр → `5`, `generic:course-choice`

### Membership DEACTIVATE list

- Болотова Ульяна → `english:5`
- Бруннер Агата → `instructional:литература:11:егэ`
- Коуттс Александра → `7-2`, `generic:course-choice`, `generic:digital-track`
- Кулиш Владимир → `5`, `generic:course-choice`
- Маркова Иванка → `7-1`
- Михалюк Максим → `generic:course-choice`, `generic:digital-track`
- Осипов Артем → `instructional:информатика:9:база`
- Осипчук Всеволод → `7-2`, `english:4`
- Сахаров Лев → `7-2`
- Соколов Федор → `english:5`
- Чтак Северина → `7-1`
- Чудайкина Ярослава → `7-1`

## 5–8. Readback

- Active memberships: **488 → 490**.
- Canonical students: **123 → 125**; canonical groups: **50 → 51**.
- Кулиш Александр and Кулиш Владимир are distinct canonical identities.
- Воронин Лаврентий exists exactly once and has stable memberships including 7-2.
- Холодова Татьяна protected membership preserved; known Grade 9 partition exception remains intentional.
- Grade 11 Math/Society/Literature XOR invariants: PASS in post-apply dry-run.

## 9. Post-apply dry-run

- Current → expected: **490 → 489**.
- KEEP: **489**; CREATE: **0**; DEACTIVATE: **0**; PROTECTED: **1**; UNRESOLVED: **0**.
- WRONG_GROUP: **0**; STALE: **0**; duplicates: **0**; ambiguities: **0/0**; source contradictions: **0**; invariant violations: **0**.
- Grade 9 Math/English: PASS_WITH_KNOWN_EXCEPTION for Холодова Татьяна only.

## 10–12. Residuals, tests, artifacts

- Residual production diff: none requiring mutation; only protected Холодова remains outside expected target by design.
- Tests: **76/76 PASS** before and after apply; `git diff --check` PASS.
- Apply JSON: `docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json`.
- Final reconciliation JSON/Markdown: `docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json` / `.md`.
