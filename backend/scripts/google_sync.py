from __future__ import annotations

import argparse
import json
import sys

from backend.config import get_settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.google_sheets import import_school_schedule_tabs
from backend.services.google_sync_service import refresh_teacher_directory


def run(write: bool, teacher_directory: bool = False) -> int:
    settings = get_settings()
    store = GoogleTokenStore()
    token = store.load()
    if not token or not token.get("refresh_token"):
        raise GoogleLiveError("Stored Google refresh token is required")
    client = GoogleLiveClient(google_config(settings), token, store)
    account = client.userinfo()
    teacher_account = str(account.get("email", "")).strip()
    if not teacher_account:
        raise GoogleLiveError("Google userinfo did not return an email")

    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    spreadsheet = client.spreadsheet(spreadsheet_id)
    spreadsheet_title = str((spreadsheet.get("properties") or {}).get("title", ""))
    visible_tabs = [
        str((sheet.get("properties") or {}).get("title"))
        for sheet in spreadsheet.get("sheets") or []
        if not (sheet.get("properties") or {}).get("hidden") and (sheet.get("properties") or {}).get("title")
    ]
    values_by_tab = client.sheet_values_many(spreadsheet_id, [f"{name}!A:Z" for name in visible_tabs])
    schedule_tabs = list(zip(visible_tabs, values_by_tab))

    courses = client.classroom_courses_for_teacher()
    course_id = settings.google_classroom_course_id or "800979670564"
    course = next((item for item in courses if item.get("id") == course_id), None)
    if course is None:
        raise GoogleLiveError(f"Configured Classroom course {course_id} was not found for the teacher account")

    if teacher_directory:
        if not write:
            print(json.dumps({"mode": "dry_run", "teacher_directory_tab": "Учителя и группы", "spreadsheet_id": spreadsheet_id}, ensure_ascii=False))
            return 0
        database = Database(settings.database_path, settings.database_url)
        print(json.dumps(refresh_teacher_directory(database, settings), ensure_ascii=False))
        return 0
    if not write:
        print(json.dumps({"mode": "dry_run", "schedule_tabs": visible_tabs, "course_id": course_id, "course_name": course.get("name")}, ensure_ascii=False))
        return 0

    database = Database(settings.database_path, settings.database_url)
    schedule_count = import_school_schedule_tabs(
        database,
        schedule_tabs,
        spreadsheet_title=spreadsheet_title,
        source=f"google_sheets:{spreadsheet_id}",
    )
    classroom_counts = sync_classroom_course(database, client, course, teacher_account=teacher_account)
    print(json.dumps({
        "mode": "write",
        "schedule_tabs": [name for name, _ in schedule_tabs],
        "schedule_entries": schedule_count,
        "classroom_course_id": course_id,
        "classroom_course_name": course.get("name"),
        "classroom": classroom_counts,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync read-only Google sources into the backend database")
    parser.add_argument("--write", action="store_true", help="write normalized data to configured DATABASE_URL")
    parser.add_argument("--teacher-directory", action="store_true", help="sync the authoritative Учителя и группы tab")
    try:
        args = parser.parse_args()
        raise SystemExit(run(args.write, args.teacher_directory))
    except (GoogleLiveError, ValueError, RuntimeError) as error:
        print(f"Google sync blocked: {error}", file=sys.stderr)
        raise SystemExit(1) from error
