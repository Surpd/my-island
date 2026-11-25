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

Проверки 25.11.2026: 103 существовавшие проверки канонического расписания и публикации и две новые SQLite-регрессии прошли. Chromium E2E с настоящими локальными FastAPI и SQLite подтвердил совпадение редакции в двух ролевых сессиях preview и admin published-view, видимое занятие и HTTP 403 для обеих школьных ролей на административной ручке. Команда: `npm run test:e2e -- e2e/miniapp-preview.spec.ts -g "preview shows isolated journal scores"` (Windows: PYTHONUTF8=1). GitHub Actions запускает целевые backend suites для изменений контракта.

### Сервер рабочего пространства «Сегодня»

`GET /api/admin/operations/day?day=YYYY-MM-DD` возвращает выбранную дату, семь дат недели, метаданные опубликованной редакции, запланированное число занятий, события и алерты. Для отсутствующей публикации число занятий равно null, для пустого дня опубликованной недели — 0. Значение по умолчанию — календарная дата в SCHOOL_TIMEZONE (Europe/Moscow, как в административном preview). Поле rooms возвращает только занятые кабинеты по расписанию, полнота фонда неизвестна. Отмены остаются в списке событий. Обед и NO_LESSON исключены. Ссылки href у событий и алертов проверяют роль администратора и идентификатор редакции. При смене head ответ HTTP 409 содержит ссылку на текущий день.

`GET /api/admin/operations/next-week-drafts?day=YYYY-MM-DD` использует школьную административную сессию. Показывает неархивные школьные варианты следующего понедельника, их ревизии и ссылки в конструктор. Для единственного варианта или явно выбранного draft_id возвращает блокеры и число отложенных занятий из действующего preview. Запрос будущей недели независим от опубликованного дня.

Миграций нет. Служба не фиксирует проведение занятий или доступность всего фонда. Визуальное подключение ожидает утверждения дизайна. Проверки: `python -m unittest backend.tests.test_operational_day -v` — три интеграционных сценария SQLite прошли. Покрыты пересечения кабинетов и преподавателей, смежные интервалы, отмена и замена, online/SDEP, Новый год, отсутствие публикации, авторизация, смена редакции и сбои источников по отдельности.

Дополнительно прошли SQLite-сценарий реальной частичной публикации и Chromium E2E на изолированной локальной БД. Браузерная проверка читает запись по href, сверяет её редакцию с Campus обеих ролей и получает HTTP 403 при чтении административной записи с каждой школьной ролью.
### Постоянная вкладка для опубликованной недели

Включение `MY_ISLAND_PUBLISHED_WEEK_SHEETS=1` выполняется после применения миграции 056. Каждый ручной выпуск недели или восстановление записывает в той же транзакции задание для настроенной книги экспорта и версию head. Используются существующие настройки разрешённой книги и защищённого OAuth. Старые вкладки выгрузки черновика сохраняются. Для существовавших недель автоматический перенос по названию отсутствует.

Воркер FastAPI читает проверенный архив автора, исключает отложенные записи и строит существующий школьный макет. Справочные подписи заморожены при публикации. Административная история показывает отдельный статус Google Sheets; после ошибки либо ручного исправления вкладки можно повторить попытку. Потеря ответа после batchUpdate сначала разрешается чтением маркера. Повторы используют backoff до часа. Доставленная редакция подтверждается повторным чтением содержимого и маркера в Google.

Привязка книги и понедельника фиксирует постоянный gid. Вкладка и именованный диапазон создаются атомарно. При обновлении прежний идентификатор диапазона поглощается в одном batchUpdate с очисткой старого прямоугольника, записью форматированной сетки и созданием нового маркера. Устаревший batch не проходит проверку Google после смены маркера. При пользовательском изменении значений или заметок внутри управляемой области, коллизии идентификаторов либо занятой области расширения задание блокируется. Внешнее редактирование во время самого запроса остаётся ограничением Google Sheets; для ручной работы предназначено пространство вне управляемого диапазона. [Атомарность batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

`python -m unittest backend.tests.test_published_week_sheets -v`: три теста SQLite и синтетического транспорта проверяют неизменность gid и соседних данных, исключение отложенного занятия, 429/503, задержанный старый запрос, потерянный ответ после записи и обнаружение изменённых ячеек. Реальные книги не использовались.

На 25.11.2026 дополнительно прошли проверки закрытого доступа к повторной выгрузке, повреждённой ссылки head и удаления старого хвоста. Планировщик использует интервалы, назначения преподавателей и кабинеты из той же effective-недели, которую читает Campus. Несопоставимое расхождение с авторским архивом останавливает доставку. Выполнены шесть новых SQLite/fake Google тестов, 25 существующих тестов экспорта, TypeScript и Playwright-сценарий истории с отдельными статусами и повторной попыткой. [GitHub Actions: миграция 056 и два конкурирующих работника PostgreSQL 17](https://github.com/Surpd/my-island/actions/runs/45634732980). Для реальной проверки согласованности изменяемых вручную ячеек нужна отдельная разрешённая тестовая книга.

Квитанция выпуска или восстановления содержит sheet_delivery_queued. Окно публикации отдельно подтверждает постановку выгрузки в очередь. Актуальный исход доставки читается из истории. Chromium проверил квитанцию, отображение ошибки доставки и повтор из истории (1 passed, 31.2 s).

Ревью контракта добавило имена канонических групп и преподавателей, ссылки на карточки групп, ответственных за практику и все исходные занятия совместной записи. Перенос определяется по исходным дню и интервалу прикреплённого шаблона. Сравнение сохраняет одновременно зафиксированные изменения. Для нескольких черновиков следующей недели состояние requires_preview предлагает точно выбранную проверку, а выполнившийся preview возвращает ready или blocked. У расписания без полного каталога помещений доступен только перечень занятых кабинетов.
