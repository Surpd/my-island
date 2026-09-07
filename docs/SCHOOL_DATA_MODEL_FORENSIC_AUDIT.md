# School Data Model — forensic audit

> Срез production и актуальных Google Sheets на 2026-09-08. Аудит выполнен read-only: код, Supabase и Google Sheets не изменялись; reconciliation/apply не запускался.

## One-page summary

### What is a student currently?

```text
identities (человек, kind=student)
  ├─ memberships → group(type=class)             один базовый класс
  ├─ memberships → group(type=subject_group)     дополнительные учебные группы
  ├─ memberships → group(type=exam_track)        сейчас смешаны выбор и учебная группа ОГЭ/ЕГЭ
  └─ users.identity_id / account_identity_links  необязательный аккаунт/Telegram
```

`school_people` в текущей схеме нет. Канонический человек — `identities`; `users` — аккаунт, а не школьная личность. `identities.class_name` — совместимое денормализованное поле, но канонический базовый класс фактически хранится как membership в `groups(group_type='class')`.

На production сейчас: 115 активных учеников, 1 активный учитель, 78 активных/канонических групп (8 классов, 22 предметные группы, 48 exam-track), 591 активная строка membership (113 class, 211 subject group, 267 exam track), 3 teacher assignments, 0 homeroom. Открытых review issues нет. У двух учеников (`Мартынов Иван`, `Стрижов Георгий`) `class_name='8'`, но нет активного class membership — это реальная непоследовательность.

### What SHOULD NOT be duplicated?

- Обычные уроки всего класса (литература/русский/история для 9-Д) не требуют отдельной membership по каждому предмету: достаточно class membership и schedule audience класса.
- Один и тот же выбор ОГЭ/ЕГЭ и фактическая учебная группа не должны быть двумя неразличимыми `exam_track` memberships.
- Технический marker `Группа Математики` и свободная заметка не должны становиться постоянной предметной membership без доказанной семантики.
- Имя учителя в заголовке колонки — source label/hint, а не teacher identity или teacher assignment.
- Официальная и legacy/manual строки одной связи не должны выглядеть как две связи. API сейчас скрывает такую физическую дупликацию у Math C Куренкова, но она остаётся в БД.

Главный вывод: признаков массового повреждения identity-графа нет. Основная проблема — semantic/legacy debt: выбор, профиль, roster-группа и технические маркеры сведены в один `exam_track`; свежие exam source records не были повторно доведены до canonical provenance; snapshots не содержат полного сырого листа.

## 1. Фактический pipeline

```text
Google Sheets (live)
  → однократный deterministic parser / bootstrap script
  → school_source_snapshots (метаданные чтения, не полный raw grid)
  → school_source_records (извлечённые факты)
  → school_candidate_changes + identity matching/mappings
  → identities / groups / memberships / teacher_assignments
  → Database.list_people_library(), list_groups_admin()
  → /api/admin/people, /api/admin/groups
  → Admin School / People Library
```

Важное ограничение: в репозитории есть deterministic parsing и validation/provider boundary, но нет законченного регулярно вызываемого runtime-orchestrator, который воспроизводит весь production bootstrap/reconciliation. Production был собран серией one-off bootstrap/SQL операций. `school_semantic_interpretations` пуст: Groq не участвовал в текущем canonical state.

Последние snapshots валидны, однако `raw_payload` содержит только `{tab, range, read_only}`, а `structural_payload` — сводные counts. Полные значения ячеек в snapshot не сохранены. Извлечённые факты трассируются через `school_source_records`, но нераспознанная заметка может исчезнуть из внутренней истории.

## 2. Inventory сущностей

| Entity | Фактический смысл и source of truth | Создание/обновление | Runtime | Оценка |
|---|---|---|---|---|
| `identities` | Канонические ученики/учителя; Sheets для школьных людей, manual/legacy для аккаунтов | bootstrap/reconciliation и admin linking | API/UI | Нужна |
| `users` + `account_identity_links` | My Island account, Telegram и связь с identity | auth/admin confirmation | API/UI | Нужны отдельно от person |
| `groups(type=class)` | Базовые классы | class sheet + manual legacy | API/UI, schedule join | Нужны |
| `groups(type=subject_group)` | Реальные постоянные предметные группы | group roster | API/UI, schedule join | Нужны |
| `groups(type=exam_track)` | Сейчас одновременно выбор/профиль и roster-группа | обе групповые вкладки | API/UI, schedule join | Нужна декомпозиция semantics |
| `memberships` | Все постоянные связи ученика с class/subject/exam group; provenance на строке | reconciliation/manual | API/UI/schedule | Нужна, но тип связи слишком груб |
| `teacher_assignments` | Подтверждённое преподавание группы | manual/known-good | API/UI | Нужна; сейчас только 3 Math A/B/C у Дмитрия |
| `homerooms` | Классное руководство | только explicit | API/UI | Нужна; сейчас 0 |
| `school_sources` | Реестр authority-источников | migration/admin config | ingestion | Нужна |
| `school_source_snapshots` | Версия чтения/fingerprint/status | ingestion | diagnostics | Нужна, но raw capture неполон |
| `school_source_records` | Нормализованные source facts | parser | reconciliation/diagnostics | Нужны |
| `school_candidate_changes` | Предложенные изменения до canonical apply | reconciliation | audit/review | Нужны как staging |
| `school_source_mappings` | Stable external key → canonical target | reconciliation/manual review | resolution | Нужны; сейчас 23 current identity mappings |
| `school_resolution_issues` | Исключения/review | validator/reconciliation | Admin diagnostics | Нужна; 2 historical resolved, 0 open |
| `membership_overrides` | Явное include/exclude поверх источника | admin | reconciliation/runtime | Нужна для редких исключений; сейчас 1 active include |
| `school_semantic_interpretations` | Кэш/аудит semantic provider | semantic boundary | не используется | Допустима; сейчас 0 |
| `group_schedule_audiences` | Мост canonical group → schedule audience | schedule setup | schedule resolver | Нужен, но все 3 current rows archived |

Staging-таблицы защищены RLS; grants для `anon`/`authenticated` отсутствуют. Ослабления security не обнаружено.

## 3. Taxonomy всех отношений

Категории: **A** — постоянная canonical identity relationship; **B** — source fact/metadata; **C** — schedule audience, вычисляется на уроке; **D** — текущая сомнительная/смешанная модель.

| Relationship example | Категория | Current storage | Current source | Needed long-term? | Why |
|---|---:|---|---|---|---|
| Базовый класс `9-Д` | A | class group + membership | `Списки по классам 26/27!J:J` | Да | Стабильная принадлежность ученика |
| `Math C` | A | subject_group + membership | `списки групп 26-27!P:P` | Да | Реальная отдельная учебная группа |
| `English group 7 Ангелина` | A + B | subject_group membership; имя учителя встроено в label | group roster | Группа — да; label учителя — metadata | Audience отличается от base class; teacher требует отдельного assignment |
| `Physics OGE` из выбора | D | exam_track membership | `ОГЭ/ЕГЭ` | Да, но как `exam_choice`, не roster | Сейчас неотличимо от учебной OGE-группы |
| `Physics OGE` из roster | A | exam_track membership | group roster | Да, как instructional exam group | Это фактическая учебная аудитория |
| `Chemistry OGE` | D/A | аналогично Physics | оба источника | Да после разделения | Два семантических слоя совпадают не всегда |
| `Informatics OGE` | D/A | choice + roster + legacy manual group | оба источника/manual | Да после reconciliation | Сейчас до трёх групп одной видимой идеи |
| `Social Studies Base Антон` | A + B | subject_group membership | group roster | Группа — да; `Антон` — metadata | Постоянная учебная группа, но teacher label не assignment |
| `9 класс · Группа Математики` | D | exam_track membership | `ОГЭ/ЕГЭ!M:M` | Нет как отдельная membership без семантики | Технический marker ошибочно трактуется как предмет |
| `9 класс · Математика профиль` | D | exam_track membership | `ОГЭ/ЕГЭ!C:C` | Да как profile/choice | Не должна смешиваться с Math A/B/C roster |
| `Информатика + Биология` | B | не извлекается | безымянная боковая заметка | Только provenance/context | Нет заголовка/однозначного правила |
| Литература · 9-Д | C | schedule entry + class audience | будущий schedule source | Не как student membership | Наследуется через class audience |
| Русский · 9-Д | C | то же | будущий schedule source | Не как membership | То же |
| История · 9-Д | C | то же | будущий schedule source | Не как membership | То же |

## 4. Source fact → canonical → runtime

| Source fact | Parser output | Canonical output | Runtime use |
|---|---|---|---|
| Имя в class column | person + class membership records | identity + class group membership | People Library; class filter; schedule class audience |
| Имя в Math/English/etc roster column | membership record with group label | subject_group membership | People Library; exact group audience |
| Имя в OGE/EGE roster column | membership record | exam_track membership | Сейчас показывается как exam/profile и может участвовать в exact exam audience |
| Непустая subject cell в `ОГЭ/ЕГЭ` (`FALSE`/`просто` исключены) | `exam:*` source fact | exam_track membership при initial bootstrap | People Library; семантически choice/profile, не доказанная roster group |
| `Группа Математики` | обычный exam subject fact; дополнительный A/B/C parser path фактически ненадёжен | отдельная exam_track membership | Пользовательской пользы нет; Math A/B/C уже берётся из roster |
| Teacher token в заголовке | group label/hint | часть display/subgroup text | Не создаёт teacher identity/assignment |
| Учебный план | не импортируется | ничего | Только ручная структурная сверка |

`Database.list_people_library()` классифицирует отношения только по `group_type`: class → `base_class`, exam_track → `exam_profile`, subject/instructional → `instructional`. Поэтому различие choice и roster после canonical write потеряно для API. Физические дубли одного `group_id` API сворачивает, предпочитая official source, но сообщает совокупные sources/manual flag.

## 5. Base-class inheritance и schedule

Архитектурно **да, отдельные Literature/Russian/History memberships не нужны**. `Database.list_schedule_entries_for_user()` соединяет пользователя → active student membership → group → active `group_schedule_audiences` → schedule entry. Для простого class mapping совпадают `audience`, а `subject_subgroup` и `exam_track` должны быть пустыми. Subject-group и exam-group ветки требуют точного subgroup/track и при наличии — subject.

Но фактический ответ для production **сейчас нет**: все 481 schedule entries и все 3 audience mappings архивированы, активного расписания нет. Более новый JSON `audience_rule` умеет валидировать `cohort/union/intersection/exclude/complement/explicit`, но runtime resolver его не выполняет. Существующий рабочий путь — legacy mapping через `group_schedule_audiences`, не `audience_rule`.

Следовательно, после подключения расписания class membership `9-Д` будет достаточной только если есть активный mapping `group 9-Д ↔ audience 9-Д` и активные schedule entries без subgroup/exam dimensions.

## 6. ОГЭ/ЕГЭ: фактическая семантика

Live sheet содержит Grade 9/11 строки учеников, subject columns с boolean/text choices, profile values, техническую `Группа Математики` и свободные заметки. Current parser создаёт факт/membership из любого непустого subject cell, кроме значений `false` и `просто`. Поэтому `TRUE`, название предмета и `по желанию` одинаково становятся membership.

Отдельный roster sheet одновременно создаёт реальные OGE/EGE учебные группы. Оба результата имеют `group_type='exam_track'`. В production поэтому есть пары с одинаковыми учениками: Biology OGE 6 строк/3 человека, Geography 8/4, Physics 14/7, Chemistry 11/6, Literature 10/5; Informatics дополнительно содержит legacy manual вариант (14 строк/7 человек). Это не обязательно ложные source facts, но неверное объединение типов.

Для `Группа Математики` parser также пытается получить ASCII `A/B/C`, однако live marker находится в M и содержит кириллические `а/в/с`; соответствующий путь не является надёжным источником canonical Math A/B/C. Настоящие Grade 9 Math A/B/C подтверждаются roster sheet columns L/N/P. Безымянная заметка вроде `Информатика + Биология` parser игнорирует — это правильно для canonical state, но snapshot не сохраняет её raw content.

## 7. Иван Куренков — end-to-end trace

Identity: одна active legacy identity, связанная с подтверждённым student account. Дубликата person нет. API собирает связи ниже по canonical identity.

| Live source fact | Source record / canonical relation | Provenance/state | Почему существует |
|---|---|---|---|
| class sheet `J8`: Куренков Иван | person + class membership → `9-Д` | official, active | Базовый класс |
| group roster `P11` | membership → `Grade 9 Math C` | official, active; рядом существует active legacy `admin_override` той же связи | Реальная Math C group |
| group roster `N30` | membership → English 9 group 7 Ангелина | official, active | Реальная English group |
| group roster `B56` | membership → Social Base Антон | official, active | Реальная учебная группа |
| exam sheet live `C15` | `exam:*` → Math profile | canonical official active, но canonical source_ref всё ещё `C16` | Выбор/profile |
| exam live `G15` + roster `H76` | два facts → choice Physics + Physics OGE roster | official active; canonical exam ref stale `G16` | Два разных смысла сейчас выглядят одинаково |
| exam live `I15` + roster `N74` | choice Chemistry + Chemistry OGE roster | official active; stale `I16` | То же |
| exam live `K15` + roster `L88` + legacy manual group | choice + roster + manual Informatics | active; stale `K16` у choice | Legacy semantic duplication |
| exam live `M15` | `Группа Математики` exam fact/membership | active; stale `M16` | Технический marker ошибочно канонизирован |
| боковая заметка `Информатика + Биология` | source record отсутствует | не canonical | Нет однозначного header/rule |

### Почему Math C раньше был manual

Legacy `admin_override` был создан до нового directory bootstrap. Migration 012 перепривязала старые Grade 9/Classroom структуры к canonical `grade9-math-C`, сохранив source/source_ref. Позже deterministic-v2 действительно нашёл Куренкова в live roster `P11` и добавил official membership. Unique key включает source/ref, поэтому manual и official строки смогли сосуществовать. People API сворачивает их по одному group id и выбирает official как primary, но `is_manual=true` остаётся как признак одной из provenance-строк. Точный автор/момент исходного manual add невосстановим: у membership нет `created_at`, отдельного audit/override record для него нет.

Есть отдельный provenance drift: последний exam reparse записал свежие source records с row 15, но создал 0 candidate changes, поэтому canonical memberships сохранили initial refs row 16. Нельзя доказать, был ли это сдвиг строк в Google или старая ошибка извлечения; доказано только, что current staging и canonical refs расходятся.

## 8. Representative student shapes

| Ученик | Source facts | Current canonical relationships |
|---|---|---|
| Мовина Миласлава, Grade 5 | class `B8`; English roster `B29` | class 5; English 5–6 group 1 Ангелина |
| Белимов Мирон, Grade 6 | class `D3`; English `B33` | class 6; English 5–6 group 1 Ангелина; отдельной Math membership нет |
| Винарский Михаил, Grade 7 | class `F4`; Math `F7`; English `H28` | class 7; Math 7-1 Иван; English 7–8 group 4 Ангелина |
| Куренков Иван, Grade 9 | см. trace выше | 9-Д; Math C; English 7; Social Base; choice/profile и roster OGE relations |
| Косыгин Тимофей, Grade 10 | class `N6`; English `R29`; Literature `P51`; Math `R10`; exam choices C/D/F row 43 | class 10; три instructional groups; Math profile, Society, Literature choices |
| Качалова Ульяна, Grade 11 | class `Q7`; English `R35`; Math `V8`; choices C/G/I row 57; roster Physics `L74`, Informatics `P88` | class 11; English+Math groups; profile choices; Physics/Informatics EGE roster duplicates |

Все 115 active students имеют хотя бы одну active subject/instructional membership, но это не доказывает полноту учебного покрытия: group roster перечисляет только раздельные группы, а обычные предметы могут идти базовым классом.

## 9. Authority и completeness источников

| Source | Доказанная authority | Что означает absence |
|---|---|---|
| `Списки по классам 26/27` | Базовый класс и основная student roster | Возможная source/data проблема; current code не выводит класс из других листов |
| `списки групп 26-27` | Реальные постоянные instructional rosters, включая Math/English и OGE/EGE rosters | Только «источник не указал отдельную группу»; не означает, что ученик не посещает предмет |
| `ОГЭ/ЕГЭ` | Выборы/profile flags и связанные markers | Не означает отсутствие предмета или учебной группы; roster authority находится отдельно |
| `учебные планы` | Структура curriculum/hours/group counts | Не person roster; current code вообще не импортирует |

Пример Grade 6 без отдельной Math membership нельзя интерпретировать как «не ходит на математику»: current canonical state лишь не содержит отдельной Math audience. Обычная математика может наследоваться через class schedule; parser сам этого факта не создаёт и не диагностирует как ошибку.

## 10. Semantic debt и риски

| Debt | Current behavior | Risk | Future model |
|---|---|---|---|
| Class vs `class_name` | class дублируется полем и membership | Drift: уже 2 students без class membership | Membership — authority; поле projection/cache с invariant |
| Exam choice vs roster | оба `exam_track` | Дубли и неверный schedule audience | `exam_choice/profile_choice` отдельно от instructional exam group |
| Marker vs membership | `Группа Математики` канонизируется | Ложная «группа» в UI/resolver | Typed source metadata; explicit mapping only |
| Schedule audience vs membership | обычные предметы не создаются, что правильно | При отсутствии mapping уроки исчезают | Resolver rule/mapping с coverage diagnostics |
| Grade/teacher в display name | участвуют в group label | Rename создаёт ложную identity группы; teacher assignment не доказан | Stable group key + structured grade/subject; teacher separately |
| Official + manual same relation | физически две rows, API dedupe | Скрытый provenance ambiguity | Effective relation layer + explicit supersession |
| Snapshots | только metadata/counts | Нельзя воспроизвести проигнорированные cells | Сохранять bounded raw grid/content hash |
| Reparse without canonical candidate | exam records обновлены, memberships нет | Stale source refs, неполная трассировка | Один атомарный staged run с declared no-op/update outcomes |

## 11. Corruption assessment

Identity-level systemic corruption не обнаружена: 115 active students, одна canonical Dmitry teacher identity, у Дмитрия только Teacher+Admin, нет Student/homeroom, assignments только Grade 9 Math A/B/C; obsolete `9-1/9-2/9-3` не возвращены. Основные roster patterns последовательны.

Однако состояние нельзя назвать полностью чистым: две class-membership дырки, legacy/manual duplicates, exam semantic duplication и stale exam provenance refs. Это локальные invariants и системная семантическая задолженность, а не массовое смешение людей.

## 12. Решения, необходимые до Schedule

1. Развести типы `exam/profile choice` и `instructional exam group`; определить, какой из них является schedule audience.
2. Решить судьбу `Группа Математики`: metadata/mapping hint или typed choice; не отдельная exam membership. Grade 9 Math A/B/C брать из authoritative roster.
3. Выбрать один исполняемый audience mechanism: довести JSON `audience_rule` resolver либо формально оставить `group_schedule_audiences`; добавить coverage diagnostics для class audiences.
4. Установить invariant «ровно один active base-class membership для active student» и определить безопасную обработку source absence, не делая destructive mass removal.
5. Сделать ingestion воспроизводимым: сохранять достаточный raw snapshot и завершать каждый reparse явными candidate/no-op outcomes, чтобы canonical provenance не отставал от source records.

## Evidence boundary

Аудит опирается на live read-only Google ranges, production Supabase, migrations 001–019 и текущие service/API paths. Existing reports использовались только как навигация. Расписание и журнал не проектировались и не изменялись. Никакие выводы из teacher labels, заметок или отсутствия строки не превращались в новые данные.
