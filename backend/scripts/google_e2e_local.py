from __future__ import annotations

import os
import tempfile

from backend.config import get_settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.google_sheets import looks_like_school_schedule, parse_school_schedule_values
from backend.services.schedule_import import record_failed_sync, sync_schedule


def run() -> None:
    settings = get_settings()
    store = GoogleTokenStore()
    client = GoogleLiveClient(google_config(settings), store.load(), store)
    spreadsheet = client.spreadsheet(settings.google_sheets_spreadsheet_id or "")
    properties = spreadsheet.get("properties") or {}
    tabs = [sheet.get("properties") or {} for sheet in spreadsheet.get("sheets") or []]
    names = [str(tab["title"]) for tab in tabs if not tab.get("hidden") and tab.get("title")]
    values_by_sheet = client.sheet_values_many(settings.google_sheets_spreadsheet_id or "", [f"{name}!A:Z" for name in names])
    rows: list[dict] = []
    selected_tabs: list[str] = []
    for name, values in zip(names, values_by_sheet):
        if looks_like_school_schedule(values):
            selected_tabs.append(name)
            rows.extend(parse_school_schedule_values(values, sheet_title=name, spreadsheet_title=str(properties.get("title", ""))))

    courses = client.classroom_courses_for_teacher()
    course_id = settings.google_classroom_course_id or "800979670564"
    course = next(course for course in courses if course.get("id") == course_id)
    with tempfile.TemporaryDirectory() as directory:
        database = Database(os.path.join(directory, "google-e2e.db"))
        database.initialize()
        schedule_first = sync_schedule(database, rows, source="google_sheets:live")
        schedule_second = sync_schedule(database, rows, source="google_sheets:live")
        try:
            sync_schedule(database, [{"date": "not-a-date", "start_time": "10:00", "subject": "invalid"}], source="google_sheets:live")
        except ValueError as error:
            record_failed_sync(database, "google_sheets:live", error)
        with database.connection() as connection:
            schedule_after_failure = connection.execute("SELECT COUNT(*) FROM schedule_entries").fetchone()[0]
            failed_syncs = connection.execute("SELECT COUNT(*) FROM schedule_syncs WHERE status = 'failed'").fetchone()[0]
        classroom_first = sync_classroom_course(database, client, course, teacher_account="antischool.island@gmail.com")
        classroom_second = sync_classroom_course(database, client, course, teacher_account="antischool.island@gmail.com")
        with database.connection() as connection:
            classroom_rows = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("classroom_courses", "classroom_coursework", "classroom_coursework_materials", "classroom_student_submissions")
            }
    print({
        "schedule_tabs": selected_tabs,
        "schedule_rows": len(rows),
        "schedule_sync_counts": [schedule_first, schedule_second],
        "schedule_after_failed": schedule_after_failure,
        "failed_sync_records": failed_syncs,
        "classroom_first": classroom_first,
        "classroom_second": classroom_second,
        "classroom_rows": classroom_rows,
    })


if __name__ == "__main__":
    run()
