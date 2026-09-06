from __future__ import annotations

from typing import Any

from backend.config import Settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.google_sheets import import_school_schedule_tabs


def _client(settings: Settings) -> tuple[GoogleLiveClient, str]:
    store = GoogleTokenStore()
    token = store.load()
    if not token or not token.get("refresh_token"):
        raise GoogleLiveError("Stored Google refresh token is required")
    client = GoogleLiveClient(google_config(settings), token, store)
    account = client.userinfo()
    email = str(account.get("email", "")).strip()
    if not email:
        raise GoogleLiveError("Google userinfo did not return an email")
    return client, email


def refresh_schedule(database: Database, settings: Settings, week_start: str | None = None) -> dict[str, Any]:
    client, _ = _client(settings)
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    spreadsheet = client.spreadsheet(spreadsheet_id)
    spreadsheet_title = str((spreadsheet.get("properties") or {}).get("title", ""))
    visible_tabs = [
        str((sheet.get("properties") or {}).get("title"))
        for sheet in spreadsheet.get("sheets") or []
        if not (sheet.get("properties") or {}).get("hidden") and (sheet.get("properties") or {}).get("title")
    ]
    values_by_tab = client.sheet_values_many(spreadsheet_id, [f"{name}!A:Z" for name in visible_tabs])
    result = import_school_schedule_tabs(
        database,
        list(zip(visible_tabs, values_by_tab)),
        spreadsheet_title=spreadsheet_title,
        source=f"google_sheets:{spreadsheet_id}",
        week_start=week_start,
        return_stats=True,
    )
    return {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": spreadsheet_title, "stats": result}


def refresh_classroom(database: Database, settings: Settings) -> dict[str, Any]:
    client, teacher_account = _client(settings)
    courses = client.classroom_courses_for_teacher()
    course_id = settings.google_classroom_course_id or "800979670564"
    course = next((item for item in courses if item.get("id") == course_id), None)
    if course is None:
        raise GoogleLiveError(f"Configured Classroom course {course_id} was not found for the teacher account")
    return {"course_id": course_id, "course_name": course.get("name"), "counts": sync_classroom_course(database, client, course, teacher_account=teacher_account)}
