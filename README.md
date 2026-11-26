# Мой Остров

My Island — школьное Telegram Mini App для учеников и преподавателей и веб-админка для сотрудников. Приложение показывает опубликованное расписание и журналы, а админка ведёт справочник, черновики, публикации и синхронизацию источников.

Стабильная точка выпуска — [GPT Sites 140 и PR №61](CHANGELOG.md). Для работы с внешней инфраструктурой используйте [манифест проекта](.project-identity.json) и [правила AGENTS.md](AGENTS.md).

## Запуск с синтетическими данными

Требуются Node.js ≥22.13 и Python 3.12. В PowerShell из корня:

```powershell
npm ci
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt httpx==0.28.1
$env:MY_ISLAND_TEST_PROFILE = 'manual'
npm run dev:local
```

Откройте [localhost:3000](http://localhost:3000/). В тестовом профиле роли задаются параметром `?role=student|teacher|admin`. Подробности профилей, E2E и PostgreSQL есть в [руководстве по разработке](docs/DEVELOPMENT.md).

## Код и документация

- `app/`, `components/`, `public/` — frontend Vinext/React, админка и Student/Teacher Campus.
- `backend/handlers/`, `backend/services/`, `backend/database.py` — API FastAPI, предметные правила и SQLite/PostgreSQL.
- `backend/migrations/`, `backend/tests/`, `e2e/` — история схемы и регрессии.
- [Архитектура](docs/ARCHITECTURE.md) · [Расписание](docs/SCHEDULE.md) · [Журналы](docs/JOURNALS.md) · [Интеграции](docs/INTEGRATIONS.md)
- [Разработка](docs/DEVELOPMENT.md) · [Выпуск и восстановление](docs/DEPLOYMENT.md) · [Дизайн-система](docs/DESIGN_SYSTEM.md) · [Дальнейшие задачи](docs/ROADMAP.md)
