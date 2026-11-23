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
title = "II · Синтетический предмет"
rows = [["Дата", "Итог", "25.11"], ["Задание", "", "Проверочная"], ["Вес", "", 2],
        ["Категория (У/О/П)", "", "О"], ["Тестовый ученик", 4, 4], ["Другой участник", 5, 5]]
google = Mock()
google.spreadsheet.return_value = {"sheets": [{"properties": {"sheetId": 12, "title": title}}]}
google.sheet_values.return_value = rows
payload = {"spreadsheet_url": "synthetic-browser-journal", "grade": 7, "term_number": 2, "auto_sync_enabled": False}
with patch("backend.services.google_sync_service._client", return_value=(google, None)):
    for dry in (True, False, False):
        response = api.post("/api/admin/journals/connect", headers=header, json={**payload, "dry_run": dry})
        assert response.status_code == 200 and response.json()["status"] == "success"
source = next(row for row in database.journal_status() if row["spreadsheet_id"] == payload["spreadsheet_url"])
student = next(row for row in database.journal_detail(source["id"])["students"] if row["display_name"] == "Тестовый ученик")
with database.connection() as connection:
    identity = connection.execute("SELECT id FROM identities WHERE origin='local_e2e' AND source_ref='local-e2e:student'").fetchone()
assert database.match_journal_student(source["id"], student["id"], identity["id"])
