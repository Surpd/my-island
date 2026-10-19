# Локальная среда My Island

Команды запускаются из корня проекта.

```powershell
npm run dev:local       # обычная локальная версия: API :8000 + интерфейс :3000
npm run test:local:auth # авторизационные проверки на отдельной SQLite
npm run test:local      # полный backend unittest-набор на отдельной SQLite
npm run reset:local     # пересоздать только помеченную SQLite с тестовыми данными
```

Обычный `npm run dev:local` подключается к одобренному проекту Supabase `kmrcbewmxeqqxfauiwyy` из `.env`, требует настоящий Telegram-код администратора `/admincode` и работает с данными школы только на чтение. Если соединение с Supabase недоступно или не соответствует проекту, backend не запускается с тестовой SQLite. Интерфейс показывает источник данных.

Тестовый сервер запускается только с явным профилем:

```powershell
$env:MY_ISLAND_TEST_PROFILE = 'manual' # либо 'e2e'
npm run dev:local
```

Он использует только `.local-test/my-island-test.sqlite3` (для `e2e` — `.local-test/e2e/my-island-test.sqlite3`) и показывает метку «Тестовая база · SQLite». Ссылки `?role=admin`, `?role=teacher` и `?role=student` предназначены только для этого изолированного тестового профиля. В школьном режиме `?role=admin` не выдаёт доступ.
