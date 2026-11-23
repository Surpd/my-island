"""Seed only the marked, isolated browser-test database through the admin API."""
from unittest.mock import Mock, patch

from backend.scripts import local_test_env as local

if local.PROFILE != "e2e" or local.MARKER.read_text() != local.MARKER_CONTENT:
    raise RuntimeError("Expected the initialized local browser-test database")
local.configure_environment()

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.database import Database
from backend.handlers.api import create_router

database = Database(str(local.DB_PATH), None)
app = FastAPI()
app.include_router(create_router(database))
api = TestClient(app)
header = {"X-Dev-Auth": "admin:1"}
title = "II · Математика"
rows = [["Дата", "Итог", "25.11"], ["Задание", "", "Проверочная"], ["Вес", "", 2],
        ["Категория (У/О/П)", "", "О"], ["Тестовый ученик", 4, 4], ["Другой участник", 5, 5]]
for day in range(1, 13):
    name = "Проверочная работа по теме «Как построить доказательство и проверить каждый шаг решения»" if day == 12 else f"Задача {day}"
    for row, value in zip(rows, [f"{day:02}.12", name, 1 + day % 3, ("О", "У", "П")[day % 3], 3 + day % 3, 5]):
        row.append(value)
google = Mock()
tabs = {
    title: rows,
    "II · История": [["Дата", "", "01.12"], ["Задание", "", "Доклад"], ["Вес", "", 2],
                     ["Категория (У/О/П)", "", "У"], ["Тестовый ученик", "", 5]],
    "II · Физика": [["Дата", "", "02.12"], ["Задание", "", "Наблюдение"], ["Вес", "", 1],
                    ["Категория (У/О/П)", "", "У"], ["Тестовый ученик", "", ""]],
}
google.spreadsheet.return_value = {"sheets": [{"properties": {"sheetId": n, "title": t}} for n, t in enumerate(tabs, 12)]}
google.sheet_values.side_effect = lambda _, address, **kwargs: tabs[address.split("'")[1]]
payload = {"spreadsheet_url": "synthetic-browser-journal", "grade": 7, "term_number": 2, "auto_sync_enabled": False}
with patch("backend.services.google_sync_service._client", return_value=(google, None)):
    for dry in (True, False, False):
        response = api.post("/api/admin/journals/connect", headers=header, json={**payload, "dry_run": dry})
        assert response.status_code == 200 and response.json()["status"] == "success"
with database.connection() as connection:
    identity = connection.execute("SELECT id FROM identities WHERE origin='local_e2e' AND source_ref='local-e2e:student'").fetchone()
for source in database.journal_status():
    if source["spreadsheet_id"] == payload["spreadsheet_url"]:
        student = next(row for row in database.journal_detail(source["id"])["students"] if row["display_name"] == "Тестовый ученик")
        assert database.match_journal_student(source["id"], student["id"], identity["id"])
