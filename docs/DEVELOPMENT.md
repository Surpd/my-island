# Разработка и проверки

Установите зависимости по [README](../README.md). Команды выполняются из корня проекта. Реальные данные, ключи и отчёты с именами или оценками храните в защищённом локальном каталоге. `.env.example` описывает имена переменных. Backend секреты никогда не передаются в frontend либо в Git.

## Локальные профили

- `MY_ISLAND_TEST_PROFILE=manual` и `npm run dev:local` запускают API на 8000 и UI на 3000, используют только помеченную SQLite в `.local-test/`. Профиль `e2e` использует собственную SQLite. Синтетические роли по query string работают в тестовом режиме.
- Без тестового профиля `npm run dev:local` использует одобренный Supabase из локальной конфигурации в режиме чтения школы и требует Telegram-код администратора. Перед подключением сверить [манифест](../.project-identity.json). При ошибке подключения или чужом проекте сервер останавливается.
- `npm run reset:local` пересоздаёт только помеченную тестовую SQLite. `npm run test:local:auth` проверяет локальную авторизацию, `npm run test:local` запускает backend unittest-набор.

## Проверка перед PR

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHON_DOTENV_DISABLED = '1'
python -m unittest discover -s backend/tests -v
$tests = @(rg --files components e2e tests -g '*.test.mjs')
node --test @tests
npx --no-install tsc --noEmit
npm run lint
$env:VITE_API_BASE_URL = 'http://127.0.0.1:8011'
npm run build
npx playwright install chromium
npm run test:e2e
```

`npm run test:e2e:schedule` запускает основной браузерный сценарий расписания. Runner копирует исходники и открытый `.openai/hosting.json` в отдельный `.local-test/e2e-workspace`, использует собственный кеш Vite и порты 3011/8011. Global setup проверяет loopback и маркер SQLite до сброса. Данные и авторизация синтетические, Google подменён fixture. Trace и скриншоты отказов лежат в `.local-test/e2e-workspace/.local-test/playwright-results`. `MY_ISLAND_E2E_VERIFY_DIAGNOSTICS=1` включает дополнительные сценарии диагностических сессий.

В [интеграционном CI](../.github/workflows/architecture-integration.yml) PostgreSQL 17 воспроизводит прежнюю схему и применяет 054–058 на синтетических строках. Миграция 044 требует внешние Supabase cron/net, из 045 используется схема до разового школьного backfill. Профиль проверяет историю, транзакции и ролевые границы. Используйте исключительно отдельный тестовый DSN. `python -m backend.scripts.release_postgres_check` ограничен изолированной БД `my_island_release_check` на loopback:55432. Условные PostgreSQL-тесты в общем unittest-запуске пропускаются без собственного тестового DSN.

## Профиль конструктора

Запустите `python scripts/schedule-regression/local_harness.py` на 8041 и `node scripts/schedule-regression/frontend.mjs` на 3041. Harness использует синтетические данные, отключает секреты и исходящий доступ. Сбрасывайте fixture перед повтором сценария с публикацией или правкой. `integration_flow.py`, `react-integration.mjs`, `restore-react.mjs`, `colors-react.mjs`, `release-smoke.mjs` проверяют реальные HTTP/UI-циклы. Для проверки отключённых старых маршрутов задайте `MY_ISLAND_CONSOLIDATION_E2E=1` и запустите Playwright с `scripts/schedule-regression/playwright.config.mjs`.

`historical-regression.mjs` дополнительно требует закрытый файл `artifacts/template-restore/recovered-draft-payload.json` с историческим черновиком (386 записей, 427 визуальных проекций). Принадлежность к группам проверяйте на синтетических fixtures. Результаты профиля идут в `.local-test/consolidation/` или в CI-артефакты.
