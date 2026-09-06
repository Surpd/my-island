# Мой Остров

Telegram Mini App + school backend. Первый вертикальный срез — student flow для 9 класса с заменяемым raster-island pack и реальными scoped reads из Postgres.

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

Ежедневный entrypoint для cron/Render Cron — `python -m backend.scripts.schedule_daily`.

## Структура

- `app/` — Mini App shell, daily sheet и config-driven locations.
- `public/island/` — временный Grade 9 master asset; заменяется без изменения business logic.
- `backend/handlers/` — HTTP boundary.
- `backend/services/` — Telegram verification, identity approval и deterministic schedule importer.
- `backend/database.py` — единый repository слой для SQLite isolated tests и Supabase/Postgres runtime.
- `backend/migrations/001_initial.sql` — Supabase/Postgres reference schema + deny-by-default RLS.
- `render.yaml` — Render Blueprint для API; secrets отмечены `sync: false`.
- `docs/ARCHITECTURE.md` — durable boundaries и интеграционные статусы.

## Проверка

```powershell
npm run build
python -m unittest discover -s backend/tests -v
```

Google Sheets/Classroom подключаются через единый server-side OAuth smoke/integration path: токен хранится только в ignored `data/google-oauth-token.json` или в server-only environment fallback, обычный student request path Google API не вызывает, а importer сначала читает/валидирует источник и только затем может писать нормализованные данные. Локальный admin shell доступен через `http://localhost:3000/?role=admin` при включённом dev auth.
