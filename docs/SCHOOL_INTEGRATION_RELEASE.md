# Выпуск школьной интеграции — 26 октября 2026

## Подтверждённые назначения

| Область | Проверенный target |
| --- | --- |
| GitHub | `Surpd/my-island`, repository ID `1358337215`; отдельная ветка `codex/miniapp-integration` |
| Supabase | org `jnvelhucxsrjexigmchx`, project `kmrcbewmxeqqxfauiwyy`, host `db.kmrcbewmxeqqxfauiwyy.supabase.co`, `eu-west-1` |
| Render | аккаунт `antischool.island@gmail.com`, workspace `tea-dae3dcgu01pc73culo30`, service `srv-daeie9m7bikc739usge0`, `https://my-island-api.onrender.com` |
| GPT Sites | project `appgprj_6a9d26108af481918318b34094e1d73b`, owner `706c9176-5c88-4cba-9feb-2673bc87e1c7`, `https://my-island.mitya971108.chatgpt.site` |

Все поля Render совпали с connector response. У сервиса `autoDeploy=no`, `autoDeployTrigger=off`; отправка отдельной GitHub-ветки не является деплоем. Sites identity/owner/public URL подтверждены connector response; текущий доступ `public` сохраняется.

Через разрешённый вход в Render Dashboard дополнительно подтверждены фактические `SCHEDULE_BACKEND=canonical`, `APP_ENV=production`, `DEV_AUTH_ENABLED=false`, `MY_ISLAND_CONSOLIDATED_SCHEDULE=1`; оба локальных school-workspace флага равны `0`. Глобальное включение не требуется. Production `DATABASE_URL` адресует разрешённый project через session pooler `aws-1-eu-west-1.pooler.supabase.com:5432`; пароль не выводился и не изменялся. CORS preflight с точным Sites origin, credentials и `X-Preview-Id` прошёл; Sites production environment уже содержит правильный явный API URL.

Предыдущий «блокер владельца» не был ошибкой подключения: Supabase metadata API не имеет отдельного поля owner email. Точные project/org/host/region совпали с разрешённым target; подключение из существующего `.env` работает. Значения секретов в документацию не включаются.

## Проверено на школьном источнике

Школьные сведения читались транзакциями `READ ONLY`. Проверены пять различных учеников (девятые математических групп A/B/C, другой класс, отдельный ученик без Telegram) и два преподавателя с несколькими группами. Проверялись профили, группы, сопоставления журналов, результаты, canonical-проекции и запрет доступа преподавателя к посторонней группе. Детальные результаты отдельных детей не сохраняются в Git и публичный PR.

Использована существующая materialized-неделя с 21 сентября 2026 и связанная с ней подтверждённая версия `approved_with_exceptions`. Замечания источника сохраняются; они не исправлялись изменением memberships, назначений или оценок. Новые publication heads не создавались. Наличие подтверждённого исторического источника не означает наличие публикации на текущую неделю.

Исправлены transaction-pooler prepared statements, расхождение обзора с runtime-выбором исторической недели, безопасный локальный preview store и повторный iframe handshake. Публичные range-проекции не возвращают занятия из draft/superseded-версий. Миграций для этих изменений нет.

Каталог журналов преподавателя использует тот же SQL-предикат доступа в одном соединении вместо отдельного подключения для каждого маркера. Ближайшее занятие в Teacher Campus считается от выбранного дня, включая исторические недели.

## Проверки и подготовленный артефакт

- Полный backend: 528 тестов, `OK`, 8 пропущены. Write-enabled PostgreSQL tests намеренно не включались против школьной БД.
- JavaScript: 43/43. Playwright: единый финальный прогон 7/7 за 32,6 секунды, включая повторный handshake и мобильную админку 360/390/430.
- Lint, TypeScript и production build прошли. Identity guard подтвердил разрешённый target с явным API URL и отказал без него.
- Read-only smoke пяти разных учеников и двух преподавателей прошёл; браузерная проверка использовала настоящую административную сессию. Физический Telegram не проверен.
- Frontend упакован штатным Sites `package-site.mjs` в `.local-test/school-release/my-island-frontend.tar.gz`: 121 файл, SHA-256 `6267ce877328eb5332a595e65b4e7492cff6d0b5cda5576313ef5f7b2d113d32`. Внутри только `dist/`, правильный Sites project ID и явный API URL. Проверка на существующие локальные секреты прошла; исходники `.env`, школьные данные и тестовые БД в архив не входят.

Архив подготовлен локально, не загружен и не опубликован. Перед сохранением Sites-версии необходимо связать его с точным pushed source SHA настроенного Sites mirror; одного SHA канонического GitHub-репозитория для этого недостаточно.

## Порядок выпуска после разрешения владельца

1. Перед действием повторно сверить targets с `.project-identity.json`. Сохранить live Render SHA/deploy ID и live Sites version ID. Проверить Git HEAD выпуска и чистое рабочее дерево. Не брать более новый непроверенный HEAD.
2. Backend: Render обновляется первым, с конкретным проверенным commit SHA. Сохранить `DATABASE_URL`, Telegram/Google secrets, `APP_ENV=production`, `DEV_AUTH_ENABLED=false`, текущие согласованные feature flags и точный `CORS_ORIGINS=https://my-island.mitya971108.chatgpt.site`. Не переносить локальные `MY_ISLAND_SCHOOL_WORKSPACE`/`MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE` в production. Не выполнять миграции или импорты.
3. Дождаться Render live; `/health.release` должен совпасть с SHA. Через штатную административную сессию проверить каталог, create/read/end preview, отдельные student/teacher endpoints и published-view за 21 сентября. Проверить реальное значение `SCHEDULE_BACKEND`: если оно не `canonical`, не менять глобально автоматически. Это отдельное согласование после проверки проекций и замечаний источника.
4. Frontend: после успешного backend smoke обновить только настроенный Sites source mirror. Получить credential с отключённым publish-on-push, сверить project/repository/branch. Собрать с явным `VITE_API_BASE_URL=https://my-island-api.onrender.com` и `VITE_SCHOOL_DIRECTORY_MODE=school`; сохранить версию с её точным source SHA и соответствующим архивом, затем выполнить Sites deployment. GitHub push сам по себе frontend не публикует.
5. Проверить production admin/preview на нескольких реальных школьных личностях, смену даты, журнал, профиль, выход из preview и браузерную авторизацию. Проверить Telegram с собственными ролями на физических Android/iOS: BackButton, safe areas, возврат в админку. Публикация не подтверждает прохождение этой физической проверки.

Новый frontend требует новые `/api/admin/previews*` и `/api/admin/schedule/published-view`; со старым backend он несовместим по этим новым функциям. Новый backend сохраняет старые маршруты, поэтому порядок backend → frontend обязателен.

## Откат

Если проблема после frontend-публикации — сначала вернуть предыдущую подтверждённую Sites-версию, не менять её аудиторию. Если затем нужен откат backend — выполнить Render deploy ранее сохранённого SHA после возврата старого frontend. При ошибке backend smoke до frontend-публикации frontend не обновлять. Изменений схемы и школьных сведений в этом выпуске нет, поэтому откат не требует обратной миграции.

Исходный live Render: SHA `c9c3bf1a50ccc3ffd383b953076b3b718d82f31d`, deploy `dep-dbf0lt0n74is73b4v230`. Исходный Sites: version 100, `appgprj_6a9d26108af481918318b34094e1d73b~appgver_e23609ac5c0c81918ce554e9438b73de`, deployment `appgdep_6ade14e89658819186a4bd8b4eaaca72`, status `succeeded`. Перед выпуском повторно подтвердить актуальность обеих исходных версий: параллельный выпуск мог изменить production.
