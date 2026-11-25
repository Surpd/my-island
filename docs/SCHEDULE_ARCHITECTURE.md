# Schedule architecture

## Source of truth

The approved canonical template is the only semantic source of truth. The
current approved baseline is `SCHEDULE_CANONICAL_V1_SHADOW.json`; its
validation and manual-reconciliation boundary are recorded in
`SCHEDULE_CANONICAL_V1_SHADOW_REPORT.md` and
`SCHEDULE_CANONICAL_V1_TRIAGE.md`.

```text
Google Sheet raw data (values, colours, merges, notes)
  -> structural snapshot + provenance + fingerprint
  -> approved immutable canonical template
  -> structural template diff and semantic patches
  -> immutable weekly overlay
  -> effective schedule
  -> student and teacher projections / allocation validation
```

`NO_LESSON` is an effective per-student final state. It is never a
mode-specific default assignment.

## Component status

| Component | Status | Responsibility |
|---|---|---|
| Google Sheets ingestion and `schedule_pipeline` structural snapshot/diff helpers | KEEP | Raw source, formatting, merge and note preservation; fingerprinting and weekly structural diff. |
| `schedule_canonical_bootstrap` | KEEP, bootstrap-only | Produces an auditable initial canonical artifact; never runs in an API request or production routing path. |
| `schedule_allocation` | KEEP | Exact student-slot allocation and duplicate/conflict validation. |
| `schedule_parser_v2` row structures and `NinthGradeResolver` | MIGRATE | Bootstrap-only compatibility code used to reproduce the accepted v1 artifact. They are not runtime engines and must be folded into canonical-template maintenance before the next authoritative-pilot implementation. |
| `schedule_pipeline` V1 reconciliation, `schedule_v1_*` database accessors and admin routes | MIGRATE | Existing compatibility API for historical V1 records. It must be replaced by a canonical/effective-schedule adapter before authoritative scheduling is enabled; it must not be used as a fallback for canonical routing. |
| V2 shadow runner, AI semantic shadow/provider, Groq shadow scripts and their dedicated tests | REMOVE | Removed. They were non-production comparison experiments and did not create the approved canonical template. |
| `schedule_import` and old `schedule_entries` readers | MIGRATE | Historical import/read compatibility; no new semantic behaviour may be added here. |

## Runtime guard and API rollout

No canonical request may fall back to V1, Parser V2, or `NinthGradeResolver`.
The canonical read-side is selected explicitly with `SCHEDULE_BACKEND=canonical`;
the default is `disabled`, so no schedule semantic engine is active until a
canonical pilot explicitly enables it. There is no legacy schedule fallback.
by this bootstrap. In the default mode, student and teacher compatibility reads
remain read-only and do not invoke any parser/importer. Canonical persistence and
effective-week materialization are separate admin/operational steps; no request
path makes a canonical version authoritative automatically.

## Removed experimental paths

- `backend/services/schedule_semantic_shadow.py`
- `backend/services/schedule_ai_shadow.py`
- `backend/services/schedule_semantic_ai.py`
- `backend/scripts/schedule_v2_shadow.py`
- `backend/scripts/schedule_ai_shadow.py`
- dedicated V2/AI shadow tests

## Regression ownership

Canonical/effective-schedule tests must retain coverage for Grade 9 Math and
English partitions, OGE overlays, explicit residual activities, final
`NO_LESSON`, moved/cancelled/replaced weekly slots, fail-closed unresolved
routes, and duplicate student-slot detection. Tests that only compare one
deprecated semantic engine with another are intentionally removed.

### Контракт опубликованной недели (25 ноября 2026)

`canonical_schedule.resolve_published_week(database, day)` принимает школьную календарную дату и возвращает редакцию для её понедельника. Поле `metadata` безопасно для ролевой выдачи. Внутренние `effective` и `source` предназначены для проекций и содержат служебные данные. В рамках одного вызова занятия читаются по выбранному неизменяемому `effective_week_id`.

| origin | Что получает потребитель |
|---|---|
| manual_week | Назначенная недельная публикация. Проверяются неделя, базовая версия, архив и целостность сохранённого авторского источника при его наличии. revision — номер смены head. |
| approved_materialization | Последняя по дате наблюдения утверждённая материализация для этого понедельника. revision — отпечаток overlay. Новая базовая версия или чужой черновик не скрывают утверждённую неделю. |
| template_only | Доступен утверждённый шаблон. Недельной выдачи и строк для аналитики нет. |
| missing | Утверждённая недельная редакция отсутствует. |
| broken_head | Явное назначение повреждено. Персональная выдача и published preview пусты, status — broken_publication. |

Поле coverage различает complete и partial по записям проверенного авторского источника. Для старых данных возможны with_exceptions и unknown. Поле publication_id относится к явной недельной публикации. Административный published-view возвращает объект publication, ролевые schedule API — массив publications. Авторизация и фильтрация персональных строк действуют до выдачи API. Условия публикации, записи БД и формат существующих полей сохраняются. Миграций нет.

Регрессионные сценарии в `backend/tests/test_constructor_publication.py` проверяют выбор исторической версии, частичную публикацию, обе роли, расхождение с черновиком, обрыв каждой из двух ссылок head, смену шаблона и переход с декабря на январь. Две новые регрессии выполнены на SQLite.

### Постоянная вкладка для опубликованной недели

Включение `MY_ISLAND_PUBLISHED_WEEK_SHEETS=1` выполняется после применения миграции 056. Каждый ручной выпуск недели или восстановление записывает в той же транзакции задание для настроенной книги экспорта и версию head. Используются существующие настройки разрешённой книги и защищённого OAuth. Старые вкладки выгрузки черновика сохраняются. Для существовавших недель автоматический перенос по названию отсутствует.

Воркер FastAPI читает проверенный архив автора, исключает отложенные записи и строит существующий школьный макет. Справочные подписи заморожены при публикации. Административная история показывает отдельный статус Google Sheets; после ошибки либо ручного исправления вкладки можно повторить попытку. Потеря ответа после batchUpdate сначала разрешается чтением маркера. Повторы используют backoff до часа. Доставленная редакция подтверждается повторным чтением содержимого и маркера в Google.

Привязка книги и понедельника фиксирует постоянный gid. Вкладка и именованный диапазон создаются атомарно. При обновлении прежний идентификатор диапазона поглощается в одном batchUpdate с очисткой старого прямоугольника, записью форматированной сетки и созданием нового маркера. Устаревший batch не проходит проверку Google после смены маркера. При пользовательском изменении значений или заметок внутри управляемой области, коллизии идентификаторов либо занятой области расширения задание блокируется. Внешнее редактирование во время самого запроса остаётся ограничением Google Sheets; для ручной работы предназначено пространство вне управляемого диапазона. [Атомарность batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

`python -m unittest backend.tests.test_published_week_sheets -v`: три теста SQLite и синтетического транспорта проверяют неизменность gid и соседних данных, исключение отложенного занятия, 429/503, задержанный старый запрос, потерянный ответ после записи и обнаружение изменённых ячеек. Реальные книги не использовались.
