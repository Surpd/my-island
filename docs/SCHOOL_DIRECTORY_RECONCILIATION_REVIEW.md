# School Directory reconciliation review

Дата: 2026-09-07. **Очередь закрыта: 0 открытых review items.**

## Применённые решения пользователя

- `Иващенко Фёдор` — старая запись; identity оставлена для historical provenance, но переведена в `inactive`, все active memberships закрыты, merge не выполнялся.
- `Нестерова Алиса` — старая запись; identity оставлена для historical provenance, но переведена в `inactive`, все active memberships закрыты.
- `Холодова Татьяна` — добавлена user-confirmed base-class membership `9-А` с `admin_override` и active membership guard; последующий неполный source не должен молча заменить это решение.

## Проверка active Directory

- Active canonical students: **115**.
- Иващенко Фёдор: отсутствует в active Directory, active memberships: **0**.
- Нестерова Алиса: отсутствует в active Directory, active memberships: **0**.
- Холодова Татьяна: active, base class **9-А**, active memberships: **5**.
- Open resolution issues в production: **0**.
- Горлова Вика/Виктория и Мищенко Петя/Пётр остаются одной canonical identity; duplicate active identities не создавались.

## Admin projection/UI verification

Найденная системная проблема была в projection: `/api/admin/people` отдавал связи единым плоским `groups`, а `/api/admin/groups` — только counts. Из-за этого UI не мог надёжно отличить базовый класс, instructional membership и ОГЭ/ЕГЭ/profile.

Исправлено:

- explicit `base_class`, `base_classes`, `instructional_memberships`, `exam_profile_memberships`, `teacher_assignments`, `relationship_issue` в People Library;
- active group rosters и `is_manual`/source provenance в Groups API;
- фильтр People: сначала базовый класс, затем учебная/экзаменационная группа;
- School UI: базовые классы и учебные/exam groups разделены, карточка состава открывается отдельно, переходы используют ту же canonical person;
- поиск получил отдельные class/group constraints и доступное имя поля.

## Regression dataset

- Grade 9 Math: A = **5 source-derived**, B = **11 source-derived**, C = **12 source-derived + 1 manual override** (13 active total).
- Холодова Татьяна открывается как `9-А` и сохраняет предметные/exam memberships.
- Иващенко Фёдор и Нестерова Алиса не попадают в active People/API projections.
- Alias pairs Горлова и Мищенко не дублируются.
- Production groups API сохраняет active student rosters и показывает manual/source distinction.

## Проверка пяти случайных учеников

Проверены `Бруннер Агата` (11), `Куренков Иван` (9-Д), `Николаев Максим` (6), `Парамонов Женя` (6) и `Турчин Алексей` (7). Для каждого найден ровно один base-class membership; instructional и exam/profile memberships отделены по `group_type` и имеют source refs. У Куренкова обнаружился один лишний active `admin_override` row на тот же base class 9-Д — он деактивирован как дубль, исходная official membership сохранена. Manual Math C membership сохранена отдельно. Дополнительно подтверждены связи Куренкова: English `9 класс · 7 Ангелина` (`списки групп 26-27!N30`), Social base (`B56`), Physics/Chemistry OGE (`H76`/`N74`) и Informatics OGE (`L88`).

При этой проверке обнаружена системная ошибка первого применения: валидированные English membership records для групп 4, 5, 7, 8 и 10 существовали, но соответствующие group rows отсутствовали. Это не было проблемой источника или отдельных учеников; добавлены только пять групп, явно подтверждённых заголовками `H26/J26/N26/P26/T26`, и их однозначные memberships. Короткие имена `Маша/Оля/Катя` сопоставлены только там, где уже есть canonical identity mapping; остальные identities не создавались.

Тем же общим правилом восстановлены ещё четыре отсутствовавшие roster groups: Math `7-2`, Math `11 Угл Анна`, Social `9 ОГЭ Антон` и Informatics `9 ОГЭ Тарас`. Это не отдельные исключения для Куренкова: repair выбирает любые validated source records без canonical group и материализует только подтверждённые заголовки и детерминированные identity mappings.

Короткие teacher labels (`Иван`, `Мария`, `Ангелина`, `Игорь`, `Тарас`, `Антон`, `Юлия`, `ЕВ`, `ИА`) по-прежнему являются source metadata, а не canonical teachers или assignments.
