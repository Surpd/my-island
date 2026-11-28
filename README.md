# Мой Остров

Telegram Mini App + school backend с единым responsive Student / Teacher / Admin frontend. Student и Teacher работают как scene-based Mini App, а Admin — как desktop-friendly control center.

## Идентичность проекта и production safety

Этот репозиторий относится только к **My Island**. Он не связан с IslandQuiz,
Island Quiz или Island Kingdom. Точные запрещённые внешние targets и обязательные
проверки хранятся в `.project-identity.json`, а правила для автоматизированных
агентов — в `AGENTS.md`.

Production-сборка требует явный `VITE_API_BASE_URL`; hard-coded fallback запрещён.
Перед любым Render/Supabase/Sites действием необходимо сверить точные account,
workspace ID, service ID, repository и domain. Команда `npm run verify:identity`
останавливает сборку при отсутствии API target или совпадении с известным чужим
проектом.

## Запуск

```powershell
npm install
npm run dev
```

Frontend открывается на `http://localhost:3000` и читает backend API через `VITE_API_BASE_URL`. В локальной разработке можно использовать только dev auth из `.env`; в production он запрещён.

Backend boundary:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
$env:DEV_AUTH_ENABLED = 'true'
uvicorn backend.main:app --reload --port 8000
```

Для production `APP_ENV=production`; тогда `X-Dev-Auth` отвергается и каждый запрос должен содержать проверяемый Telegram `initData`. Секрет `TELEGRAM_BOT_TOKEN` хранится только в окружении. Если задан `DATABASE_URL`, тот же repository слой использует Postgres через psycopg; приложение не применяет migrations автоматически.

Скопируйте `.env.example` в `.env` и заполните только нужные server-side значения. OAuth refresh token можно хранить локально в ignored `data/google-oauth-token.json`; для ephemeral production filesystem предусмотрен `GOOGLE_OAUTH_REFRESH_TOKEN`.

Реальные read-only синхронизации запускаются отдельно от student request path:

```powershell
python -m backend.scripts.google_sync
python -m backend.scripts.google_sync --write
```

Расписание не имеет cron entrypoint до завершения canonical reconciliation: legacy daily importer удалён и не является fallback. Для явного shadow-пилота canonical read-side задайте server-side `SCHEDULE_BACKEND=canonical`; по умолчанию `SCHEDULE_BACKEND=disabled`, поэтому ни один schedule semantic engine не активируется и текущий Mini App не переключается глобально.

Текущие журналы 2026/27 читаются только из отдельных таблиц 5–11 классов. Admin запускает синхронизацию нужного класса/предмета, после чего явное сопоставление marker группы с внутренней группой открывает read-only журнал и аналитику преподавателю. Записи обратно в Google Sheets не выполняются.

## Подключение школы через Telegram

1. Ученик или преподаватель отправляет `/start` школьному боту в личном чате,
   заполняет сведения и отправляет заявку. Черновик и статус доступны при повторном входе.
2. Администратор открывает **Настройки → Пользователи и доступ → Заявки**,
   сверяет школьную запись и подтверждает её либо отклоняет заявку с причиной.
3. Управляющий администратор назначает завучу дополнительную роль во вкладке
   **Администраторы** после подтверждения его как преподавателя. Завуч может
   переключаться между Teacher Campus и админкой. На компьютере нужно
   запросить `/admincode` у того же бота и вставить одноразовый код на странице
   `/admin`. Код действует пять минут.
4. Смена Telegram-аккаунта доступна через подтверждение новой заявки с явной
   заменой старого аккаунта. Школьная запись и её данные сохраняются. При отзыве
   сессии и коды аннулируются. После восстановления откройте Mini App заново из бота.

Перед выпуском применяется миграция **059**. Первый запуск фиксирует исходного
управляющего по существующему аккаунту и `TELEGRAM_BOOTSTRAP_USER_IDS`; проверяйте
точный Telegram ID первичного администратора перед выпуском. Дальнейший вход и
изменения этого параметра не меняют закреплённое полномочие. При неоднозначных
исторических ролях проверьте, что backend определил исходного администратора,
прежде чем открывать доступ школе.

Для бота используются текущие `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` и
HTTPS `FRONTEND_PUBLIC_URL`. Backend определяет публичное имя бота через Telegram
`getMe` при запуске, а `TELEGRAM_BOT_USERNAME` позволяет задать его явно.
Результат рассмотрения сохраняется до отправки уведомления. Если доставка не
удалась, актуальный статус будет доступен в боте.

Действующие права определяют `user_roles`, подтверждённая школьная привязка
`account_identity_links` и состояние аккаунта. `users.identity_id` поддерживается
в той же транзакции для совместимости школьных запросов. Право назначения
администраторов хранится отдельно. Сведения из Telegram и совпадения по
школьному справочнику служат подсказками при проверке заявки.

## Структура

- `app/` — единый Student / Teacher / Admin frontend, scene hotspots и Today bottom sheet.
- `public/island/` — заменяемые student и teacher raster scenes без business logic внутри арта.
- `backend/handlers/` — HTTP boundary.
- `backend/services/` — Telegram verification, identity approval и deterministic schedule importer.
- `backend/database.py` — единый repository слой для SQLite isolated tests и Supabase/Postgres runtime.
- `backend/migrations/` — последовательная Supabase/Postgres schema, включая additive roles, preview, assignments, information и normalized journals; Data API deny-by-default.
- `render.yaml` — Render Blueprint для API; secrets отмечены `sync: false`.
- `docs/ARCHITECTURE.md` — durable boundaries и интеграционные статусы.

## Проверка

```powershell
npm run build
python -m unittest discover -s backend/tests -v
```

Google Sheets/Classroom подключаются через единый server-side OAuth smoke/integration path: токен хранится только в ignored `data/google-oauth-token.json` или в server-only environment fallback, обычный student request path Google API не вызывает, а importer сначала читает/валидирует источник и только затем может писать нормализованные данные. Локальный admin shell доступен через `http://localhost:3000/?role=admin` при включённом dev auth.
