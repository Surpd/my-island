# School Directory reconciliation review

Дата: 2026-09-07. Это единственная оставшаяся очередь human review после live-read и reconciliation. Нормальные записи, включая 312/312 instructional roster records и четыре подтверждённых alias merge, сюда не попадают.

## 1. Missing base-class links (одна системная причина)

| Сущность | Source value / provenance | Контекст | Почему автоматике нельзя выбрать | Варианты |
|---|---|---|---|---|
| Иващенко Фёдор | `списки групп 26-27!B54`, `списки групп 26-27!B88` | 9-class instructional/exam records | authoritative `Списки по классам 26/27` не содержит человека; source не различает 9-А/9-Д | подтвердить 9-А или 9-Д |
| Нестерова Алиса | `списки групп 26-27!J93` | Informatics · 9 класс · База Тарас | есть только instructional roster, нет base-class source record | подтвердить 9-А или 9-Д |
| Холодова Татьяна | `списки групп 26-27!B73`, `!N76` | Biology/Chemistry · 9 класс · ОГЭ | есть только exam-prep rosters, нет base-class source record | подтвердить 9-А или 9-Д |

Для всех трёх identity сама по себе однозначна; unresolved только структурная связь с базовым классом. Memberships уже применены и не потеряны.

## 2. Не является review item

- `Горлова Вика` = `Горлова Виктория`, `Мищенко Петя` = `Мищенко Пётр`, `Коченкова Тая` = `Коченкова Таисия`, `Кудимов Петя` = `Кудимов Пётр`, and the equally unambiguous `Провоторова Маша` = `Провоторова Мария`: deterministic name-variant/context resolver, canonical identities merged, source observations retained.
- `Федя` отсутствует в live authoritative base list (`Списки по классам 26/27!J16` — старый snapshot only). Relationship ended by generic source-disappearance lifecycle; identity is inactive and was not merged with Иващенко Фёдор.
- Short teacher labels remain source metadata. They do not create teachers or teaching assignments.
- Grade 9 Math source is complete: A=5, B=11, C=12, unresolved=0. Obsolete 9-1/9-2/9-3 are absent.

Groq audit: no calls were necessary in this pass. The backend-only replaceable provider is implemented, but deterministic validation remains mandatory before any canonical mutation.
