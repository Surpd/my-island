# Локальные браузерные E2E-тесты

Требуются Node.js из `package.json` и Python-окружение проекта. Установить Chromium при первом запуске: `npx playwright install chromium`.

- `npm run test:e2e` — полный браузерный набор (админ, учитель, ученик и Schedule).
- `npm run test:e2e:schedule` — только основной сценарий Schedule.

Команды E2E сначала синхронизируют исходники в `.local-test/e2e-workspace` и запускают Playwright оттуда. Это отдельный vinext project с собственным lock, чтобы не блокировать обычный dev server. Отдельные пакеты подключены junction в физическую папку `node_modules` тестового workspace. Кэш Vite остаётся отдельным: общий junction всей папки смешивал оптимизированные модули React двух серверов. `.local-test`, `.env`, Cloudflare vars, credentials, school data и build caches проекта не копируются. Серверы используют только порты 3011/8011 и изолированную SQLite внутри workspace; порты 3000/8000 и школьная SQLite не затрагиваются.

Global setup проверяет loopback и маркер SQLite в workspace до сброса тестовой БД. Google preview заменён локальным fixture; сохранение черновика, публикация и повторное чтение проверяются через локальный API/SQLite. Реальные Google Sheets, production и внешние БД не используются. При остановке локального runner отправляется сигнал дочерним серверам, после чего он дожидается их `close`; зависшие процессы получают ограниченный grace period и завершаются перед выходом runner.

Из `.openai` копируется только несекретный `hosting.json`, который импортирует конфигурация Vite. Остальное состояние этой папки исключено.

Итоговый отчёт печатается в терминал; trace и screenshot упавших тестов сохраняются в `.local-test/e2e-workspace/.local-test/playwright-results`.
