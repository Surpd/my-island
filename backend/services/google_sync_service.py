from __future__ import annotations

from typing import Any

from backend.config import Settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.google_sheets import import_school_schedule_tabs
from backend.services.journal_import import sync_journal_values


CURRENT_JOURNAL_FOLDER_ID = "1u7LS5hjyiTRe4fgUoEqRB4T9c02Nq18L"
CURRENT_JOURNAL_SPREADSHEETS = {
    5: "18XFQhqvY2AFoKsX462802oNCkMc9Vvqaxosl3fLRiLc",
    6: "1I4agtpIiIokYiIrwsrpyiH4tVFMwyr4-DpdM0upcvHg",
    7: "15Kp7nXeEVpFxvDgapWCJ6MKgM75jbzePGE_S20QoTdY",
    8: "1IbjJQSh-ReldpKuP0o55o8PhixydWS09obSfmHlA--8",
    9: "1RaT1O-qNnbc3WnQs2Fhikcu03mPLl6JpatqhqBU5a8I",
    10: "1fkljnYY4WHNRVkDeQHIqFeKGLAE4kqh-TT33Wb8TRgk",
    11: "1q4zVwRwQObfgvpdg2WntKfdFAnDSW8hn7-2Ar_uutco",
}


def _resolve_sheet_title(titles: list[str], requested: str) -> str | None:
    normalized = requested.strip().casefold()
    return next((title for title in titles if title.strip().casefold() == normalized), None)


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


def refresh_journal(database: Database, settings: Settings, grade: int = 9, subject: str = "Математика") -> dict[str, Any]:
    if grade not in CURRENT_JOURNAL_SPREADSHEETS:
        raise ValueError("Journal grade must be between 5 and 11")
    spreadsheet_id = CURRENT_JOURNAL_SPREADSHEETS[grade]
    try:
        client, _ = _client(settings)
        metadata = client.spreadsheet(spreadsheet_id)
        title = str((metadata.get("properties") or {}).get("title", f"Журнал {grade} класс"))
        tabs = [str((sheet.get("properties") or {}).get("title", "")) for sheet in metadata.get("sheets") or []]
        sheet_title = _resolve_sheet_title(tabs, subject)
        if not sheet_title:
            raise GoogleLiveError(f"Journal tab {subject} was not found in {title}")
        escaped_title = sheet_title.replace("'", "''")
        values = client.sheet_values(spreadsheet_id, f"'{escaped_title}'!A:ZZ")
        counts = sync_journal_values(
            database,
            values,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_title=title,
            grade=grade,
            subject=subject,
            sheet_title=sheet_title,
        )
    except (GoogleLiveError, ValueError, RuntimeError) as error:
        database.record_journal_failure(spreadsheet_id, subject, str(error))
        database.record_audit_event("journal_sync.failed", "journal_sync", {"grade": grade, "subject": subject, "error": str(error)})
        raise
    database.record_audit_event("journal_sync.success", "journal_sync", {"grade": grade, "subject": subject, **counts})
    return {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": title, "grade": grade, "subject": subject, "counts": counts}
