from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from collections import Counter

from backend.services.directory_revisions import directory_import_connection
from backend.config import Settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore, load_google_client
from backend.services.google_oauth import google_config
from backend.services.journal_group_labels import JournalGroupLabelConflict
from backend.services.journal_import import (classify_journal_values, deferred_grade7_second_period_subject,
                                             parse_journal_values, subject_period_from_title, sync_journal_values)
from backend.services.journal_source_settings import load_source_settings, select_sync_tabs
from backend.services.teacher_directory import SHEET_NAME, sync_teacher_directory
from backend.services.teacher_directory_reconciliation import MANUAL_ASSIGNMENT_SOURCES, SHEET_NAME as STRUCTURED_TEACHER_SHEET, TeacherReconciliationPlan, build_teacher_reconciliation_plan, render_teacher_reconciliation_report


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


def _client(settings: Settings, database: Database | None = None) -> tuple[GoogleLiveClient, str]:
    config = google_config(settings)
    missing_config = [
        name
        for name, value in (
            ("GOOGLE_OAUTH_CLIENT_ID", config.client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", config.client_secret),
            ("GOOGLE_OAUTH_REDIRECT_URI", config.redirect_uri),
        )
        if not value
    ]
    if missing_config:
        raise GoogleLiveError(f"Google OAuth configuration is missing: {', '.join(missing_config)}")
    store = GoogleTokenStore(database=database, encryption_key=settings.google_oauth_token_encryption_key)
    return load_google_client(config, store, settings.google_oauth_allowed_email)


class TeacherDirectoryApplyBlocked(RuntimeError):
    """The authoritative teacher source is not safe to apply yet."""


class JournalImportBusy(GoogleLiveError):
    """A validated selection cannot claim the existing workbook run."""


class JournalSourceExcluded(ValueError):
    """The requested tab was explicitly excluded from journal imports."""


def _structured_teacher_source(settings: Settings, database: Database | None = None) -> tuple[str, str, str, list[list[str]]]:
    client, _ = _client(settings, database)
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    metadata = client.spreadsheet(spreadsheet_id)
    title = str((metadata.get("properties") or {}).get("title", ""))
    tabs = [str((sheet.get("properties") or {}).get("title", "")) for sheet in metadata.get("sheets") or []]
    sheet_title = _resolve_sheet_title(tabs, STRUCTURED_TEACHER_SHEET)
    if not sheet_title:
        raise GoogleLiveError(f"Teacher directory tab {STRUCTURED_TEACHER_SHEET} was not found in {title}")
    escaped_title = sheet_title.replace("'", "''")
    values = client.sheet_values(spreadsheet_id, f"'{escaped_title}'!A:F")
    return spreadsheet_id, title, sheet_title, values


def _audit_in_transaction(database: Database, connection: Any, action: str, entity_type: str, details: Any) -> None:
    database.execute(
        connection,
        "INSERT INTO audit_log(actor_user_id, action, entity_type, details) VALUES (?, ?, ?, ?)",
        (None, action, entity_type, json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)),
    )


def _ensure_literature_11_base(database: Database, connection: Any) -> dict[str, Any]:
    """Create the confirmed Literature 11 Base audience without guessing a roster."""
    groups = database.execute(
        connection,
        """SELECT id, name, display_name, group_type, subject, base_class_name, subject_subgroup,
                  exam_track, provenance_source, provenance_ref, canonical
             FROM groups WHERE canonical IS TRUE""",
    ).fetchall()
    equivalent = [
        row for row in groups
        if row["group_type"] == "subject_group"
        and row["subject"] == "Литература"
        and row["base_class_name"] == "11"
        and str(row["subject_subgroup"] or "").casefold() in {"база", "base"}
        and not row["exam_track"]
    ]
    if len(equivalent) > 1:
        raise TeacherDirectoryApplyBlocked("Several canonical Literature 11 Base groups exist; automatic apply is blocked")
    if equivalent:
        return {"group": dict(equivalent[0]), "created": False, "memberships_added": 0, "roster_rule": None}

    class_groups = [row for row in groups if row["group_type"] == "class" and row["name"] == "11"]
    ege_groups = [
        row for row in groups
        if row["group_type"] == "subject_group"
        and row["subject"] == "Литература"
        and row["base_class_name"] == "11"
        and row["exam_track"] == "ЕГЭ"
    ]
    if len(class_groups) != 1 or len(ege_groups) != 1:
        raise TeacherDirectoryApplyBlocked("Literature 11 Base roster cannot be derived: canonical class/EGE audience is not unique")
    class_group, ege_group = class_groups[0], ege_groups[0]
    class_members = database.execute(
        connection,
        """SELECT identity_id, source_ref FROM memberships
           WHERE group_id = ? AND member_role = 'student' AND active IS TRUE""",
        (class_group["id"],),
    ).fetchall()
    ege_members = database.execute(
        connection,
        """SELECT identity_id FROM memberships
           WHERE group_id = ? AND member_role = 'student' AND active IS TRUE""",
        (ege_group["id"],),
    ).fetchall()
    class_ids = {str(row["identity_id"]) for row in class_members}
    ege_ids = {str(row["identity_id"]) for row in ege_members}
    if not class_ids or not ege_ids or not ege_ids.issubset(class_ids):
        raise TeacherDirectoryApplyBlocked("Literature 11 Base roster cannot be derived: EGE students are not a subset of class 11")
    base_members = [row for row in class_members if str(row["identity_id"]) not in ege_ids]
    if not base_members:
        raise TeacherDirectoryApplyBlocked("Literature 11 Base roster would be empty")

    group = database.execute(
        connection,
        """INSERT INTO groups(name, group_type, display_name, subject, base_class_name, subject_subgroup,
                              provenance_source, provenance_ref, canonical)
           VALUES (?, 'subject_group', ?, ?, ?, ?, ?, ?, TRUE)
           ON CONFLICT(name, group_type) DO UPDATE SET name = excluded.name
           RETURNING *""",
        (
            "instructional:литература:11:база",
            "Литература · 11 класс · База",
            "Литература",
            "11",
            "База",
            "google_sheet",
            "списки групп 26-27!R50; derived=11-minus-literature-ege",
        ),
    ).fetchone()
    membership_source_ref = "списки групп 26-27!R50; derived=11-minus-literature-ege"
    for member in base_members:
        database.execute(
            connection,
            """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active)
               VALUES (?, ?, 'student', 'official_import', ?, TRUE)
               ON CONFLICT(group_id, identity_id, member_role, source, source_ref)
               DO UPDATE SET active = TRUE""",
            (group["id"], member["identity_id"], membership_source_ref),
        )
    _audit_in_transaction(
        database,
        connection,
        "teacher_directory.group_created",
        "group",
        {"group_id": group["id"], "group_name": group["name"], "roster_rule": "Grade 11 class minus Literature EGE", "memberships_added": len(base_members), "source_ref": membership_source_ref},
    )
    return {"group": dict(group), "created": True, "memberships_added": len(base_members), "roster_rule": "Grade 11 class minus Literature EGE"}


def _reconciliation_inputs(database: Database, connection: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]], set[str]]:
    teachers = [dict(row) for row in database.execute(connection, "SELECT id, display_name, status FROM identities WHERE kind = 'teacher' AND status = 'active'").fetchall()]
    group_rows = database.execute(connection, "SELECT id, name, group_type, subject, base_class_name, subject_subgroup, exam_track, canonical FROM groups WHERE canonical IS TRUE").fetchall()
    groups = {str(row["name"]): dict(row) for row in group_rows}
    assignment_rows = database.execute(connection, """SELECT ta.id, ta.teacher_identity_id AS teacher_id, i.display_name AS teacher_name,
            ta.group_id, g.name AS group_name, ta.subject, ta.base_class_name, ta.subject_subgroup,
            ta.exam_track, ta.source, ta.source_ref, ta.active, ta.valid_until
        FROM teacher_assignments ta
        JOIN identities i ON i.id = ta.teacher_identity_id
        JOIN groups g ON g.id = ta.group_id
        WHERE ta.active IS TRUE""").fetchall()
    current_assignments = [dict(row) for row in assignment_rows]
    canonical_subjects = {str(row["subject"]) for row in group_rows if row["subject"]}
    canonical_subjects.update(str(row["subject"]) for row in assignment_rows if row["subject"])
    return teachers, groups, current_assignments, canonical_subjects


def refresh_classroom(database: Database, settings: Settings) -> dict[str, Any]:
    client, teacher_account = _client(settings, database)
    courses = client.classroom_courses_for_teacher()
    course_id = settings.google_classroom_course_id or "800979670564"
    course = next((item for item in courses if item.get("id") == course_id), None)
    if course is None:
        raise GoogleLiveError(f"Configured Classroom course {course_id} was not found for the teacher account")
    return {"course_id": course_id, "course_name": course.get("name"), "counts": sync_classroom_course(database, client, course, teacher_account=teacher_account)}


def refresh_journal(database: Database, settings: Settings, grade: int = 9, subject: str = "Математика", sheet_id: int | str | None = None, *, spreadsheet_id: str | None = None) -> dict[str, Any]:
    target_id = spreadsheet_id or CURRENT_JOURNAL_SPREADSHEETS.get(grade)
    if not target_id:
        raise ValueError("Journal grade must be between 5 and 11")
    with database.journal_workbook_lock("__school_import__") as available:
        if not available:
            raise GoogleLiveError("Сейчас обновляется другой журнал школы. Повторите позже.")
        with database.journal_workbook_lock(target_id) as acquired:
            if not acquired:
                raise GoogleLiveError("Для этой таблицы уже выполняется синхронизация")
            return _refresh_single_journal(database, settings, grade, subject, sheet_id, spreadsheet_id=target_id)


def _refresh_single_journal(database: Database, settings: Settings, grade: int, subject: str, sheet_id: int | str | None, *, spreadsheet_id: str | None = None) -> dict[str, Any]:
    if grade not in CURRENT_JOURNAL_SPREADSHEETS and not spreadsheet_id:
        raise ValueError("Journal grade must be between 5 and 11")
    spreadsheet_id = spreadsheet_id or CURRENT_JOURNAL_SPREADSHEETS[grade]
    try:
        client, _ = _client(settings, database)
        metadata = client.spreadsheet(spreadsheet_id)
        title = str((metadata.get("properties") or {}).get("title", f"Журнал {grade} класс"))
        sheets = [sheet.get("properties") or {} for sheet in metadata.get("sheets") or []]
        selected = next((sheet for sheet in sheets if sheet_id is not None and str(sheet.get("sheetId")) == str(sheet_id)), None)
        if sheet_id is None:
            selected = next((sheet for sheet in sheets if str(sheet.get("title", "")).strip().casefold() == subject.strip().casefold()), None)
        sheet_title = str(selected.get("title", "")) if selected else None
        if not selected or not sheet_title:
            raise GoogleLiveError(f"Journal tab {subject} was not found in {title}")
        preference = load_source_settings(database, spreadsheet_id).get(str(selected.get("sheetId")))
        if preference and preference.get("excluded_from_journals"):
            raise JournalSourceExcluded("Эта вкладка исключена из журналов и не может быть обновлена.")
        bound_period = preference.get("term_number") if preference else None
        bound_subject = preference.get("subject") if preference else sheet_title
        if grade == 7 and deferred_grade7_second_period_subject(bound_subject or sheet_title, bound_period):
            return {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": title, "grade": grade,
                    "subject": sheet_title, "sheet_id": selected.get("sheetId"), "status": "skipped",
                    "classification": "deferred_grade7_period2",
                    "reason": "Импорт математики и физики 7 класса за II период временно отложен."}
        escaped_title = sheet_title.replace("'", "''")
        values = client.sheet_values(spreadsheet_id, f"'{escaped_title}'!A:ZZ", value_render_option="UNFORMATTED_VALUE")
        auxiliary = classify_journal_values(values, sheet_title=sheet_title)
        if auxiliary:
            database.record_journal_classification(spreadsheet_id, title, grade, sheet_title, selected.get("sheetId"), auxiliary["kind"], auxiliary["reason"])
            return {"spreadsheet_id": spreadsheet_id, "subject": sheet_title, "status": "skipped", "classification": auxiliary["kind"], "reason": auxiliary["reason"]}
        counts = sync_journal_values(
            database,
            values,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_title=title,
            grade=grade,
            subject=sheet_title.strip(),
            sheet_title=sheet_title,
            sheet_id=selected.get("sheetId"),
        )
    except JournalSourceExcluded:
        raise
    except (GoogleLiveError, ValueError, RuntimeError) as error:
        diagnostic = _journal_error_diagnostic(error)
        if diagnostic["code"] != "period_conflict":
            database.record_journal_failure(spreadsheet_id, subject, diagnostic["message"], code=diagnostic["code"], details=diagnostic["details"], sheet_id=sheet_id)
        database.record_audit_event("journal_sync.failed", "journal_sync", {"grade": grade, "subject": subject, "error_code": diagnostic["code"]})
        raise
    database.record_audit_event("journal_sync.success", "journal_sync", {"grade": grade, "spreadsheet_id": spreadsheet_id, "subject": sheet_title, **counts})
    return {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": title, "grade": grade, "subject": sheet_title, "sheet_id": selected.get("sheetId"), "counts": counts}


def refresh_grade_journal(database: Database, settings: Settings, grade: int = 9, *, spreadsheet_id: str | None = None, trigger_kind: str = "manual", run_id: Any | None = None, term_number: int | None = None, dry_run: bool = False,
                          selected_sheet_ids: list[str] | None = None, preflight_token: str | None = None) -> dict[str, Any]:
    if grade not in CURRENT_JOURNAL_SPREADSHEETS and not spreadsheet_id:
        raise ValueError("Для этого класса не задан источник журнала")
    target_id = spreadsheet_id or CURRENT_JOURNAL_SPREADSHEETS[grade]
    with database.journal_workbook_lock("__school_import__") as available:
        if not available:
            raise GoogleLiveError("Сейчас обновляется другой журнал школы. Повторите позже.")
        with database.journal_workbook_lock(target_id) as acquired:
            if not acquired:
                raise GoogleLiveError("Для этой таблицы уже выполняется синхронизация")
            return _refresh_grade_journal_locked(database, settings, grade, target_id, trigger_kind=trigger_kind, run_id=run_id, term_number=term_number, dry_run=dry_run,
                                                 selected_sheet_ids=selected_sheet_ids, preflight_token=preflight_token)


def _refresh_grade_journal_locked(database: Database, settings: Settings, grade: int, target_id: str, *, trigger_kind: str, run_id: Any | None, term_number: int | None, dry_run: bool,
                                  selected_sheet_ids: list[str] | None, preflight_token: str | None) -> dict[str, Any]:
    """Import every gradebook tab in one connected workbook using stable Google tab IDs."""
    from backend.services.journal_periods import journal_period_label, valid_journal_period
    if not valid_journal_period(grade, term_number):
        raise ValueError(f"Для {grade} класса доступны только 1–2 {journal_period_label(grade)}.")
    client, _ = _client(settings, database)
    metadata = client.spreadsheet(target_id)
    title = str((metadata.get("properties") or {}).get("title", f"Журнал {grade} класс"))
    tabs = [sheet.get("properties") or {} for sheet in metadata.get("sheets") or []]
    if not tabs:
        raise GoogleLiveError(f"В источнике {title} нет вкладок")
    outcomes = []
    valid_tabs = [tab for tab in tabs if str(tab.get("title", "")).strip() and tab.get("sheetId") is not None]
    if term_number is not None:
        return _refresh_scoped_journal(database, client, grade, target_id, title, valid_tabs,
                                       term_number=term_number, dry_run=dry_run, run_id=run_id,
                                       selected_sheet_ids=selected_sheet_ids, preflight_token=preflight_token,
                                       trigger_kind=trigger_kind)
    journal_scope = database.journal_import_scope(target_id, grade)
    source_by_sheet_id = {str(row["sheet_id"]): row for row in journal_scope["sources"]
                          if row["sheet_id"] is not None}
    scheduled_ids = None
    if trigger_kind == "scheduled":
        current_term = journal_scope["current_term"]
        by_id = source_by_sheet_id
        # An hourly job cannot approve a recognized prefix by itself. Both the
        # book's current-term setting and this existing tab's opt-in must agree.
        scheduled_ids = {str(tab["sheetId"]) for tab in valid_tabs if (
            (row := by_id.get(str(tab["sheetId"]))) and row["grade"] == grade and
            row["academic_year"] == "2026/27" and row["term_number"] == current_term and
            row.get("period_status") != "unassigned" and
            subject_period_from_title(tab["title"])[1] in (None, current_term)
        )}
    if trigger_kind != "scheduled" and term_number is None and not dry_run:
        previously_connected = {str(row["sheet_id"]) for row in journal_scope["sources"]
                                 if row["last_synced_at"] is not None and row["period_status"] != "unassigned"}
        valid_tabs = [tab for tab in valid_tabs if subject_period_from_title(tab["title"])[1] in (None, 1)
                      or str(tab["sheetId"]) in previously_connected]
    source_settings = load_source_settings(database, target_id)
    sync_tabs, skipped_count, excluded_count = select_sync_tabs(valid_tabs, source_settings, trigger_kind=trigger_kind)
    if scheduled_ids is not None:
        approved = [tab for tab in sync_tabs if str(tab["sheetId"]) in scheduled_ids]
        skipped_count += len(sync_tabs) - len(approved)
        sync_tabs = approved
    total_tabs = len(valid_tabs)
    sync_tab_ids = {str(tab.get("sheetId")) for tab in sync_tabs}
    for tab in valid_tabs:
        sheet_title = str(tab.get("title", ""))
        sheet_id = tab.get("sheetId")
        if str(sheet_id) not in sync_tab_ids:
            preference = source_settings.get(str(sheet_id))
            status = "excluded" if preference and preference.get("excluded_from_journals") else "skipped"
            outcomes.append({"sheet_id": sheet_id, "subject": sheet_title, "status": status})
            if run_id is not None:
                database.update_journal_sync_progress(run_id, {
                    "completed_tabs": len(outcomes), "total_tabs": total_tabs,
                    "succeeded": sum(item["status"] == "success" for item in outcomes),
                    "failed": sum(item["status"] == "error" for item in outcomes),
                    "skipped": sum(item["status"] == "skipped" for item in outcomes),
                    "excluded": sum(item["status"] == "excluded" for item in outcomes),
                })
            continue
        escaped_title = sheet_title.replace("'", "''")
        try:
            bound_period = (source_by_sheet_id.get(str(sheet_id)) or {}).get("term_number")
            if grade == 7 and deferred_grade7_second_period_subject(sheet_title, bound_period):
                outcomes.append({"sheet_id": sheet_id, "subject": sheet_title, "status": "skipped",
                    "classification": "deferred_grade7_period2",
                    "reason": "Импорт математики и физики 7 класса за II период временно отложен."})
                skipped_count += 1
                if run_id is not None:
                    database.update_journal_sync_progress(run_id, {
                        "completed_tabs": len(outcomes), "total_tabs": total_tabs,
                        "succeeded": sum(item["status"] == "success" for item in outcomes),
                        "failed": sum(item["status"] == "error" for item in outcomes),
                        "skipped": skipped_count, "excluded": excluded_count,
                    })
                continue
            values = [] if classify_journal_values([], sheet_title=sheet_title) else client.sheet_values(target_id, f"'{escaped_title}'!A:ZZ", value_render_option="UNFORMATTED_VALUE")
            auxiliary = classify_journal_values(values, sheet_title=sheet_title)
            if auxiliary:
                if not dry_run:
                    database.record_journal_classification(target_id, title, grade, sheet_title, sheet_id, auxiliary["kind"], auxiliary["reason"])
                outcomes.append({"sheet_id": sheet_id, "subject": sheet_title, "status": "skipped",
                    "classification": auxiliary["kind"], "reason": auxiliary["reason"]})
                skipped_count += 1
                if run_id is not None:
                    database.update_journal_sync_progress(run_id, {
                        "completed_tabs": len(outcomes), "total_tabs": total_tabs,
                        "succeeded": sum(item["status"] == "success" for item in outcomes),
                        "failed": sum(item["status"] == "error" for item in outcomes),
                        "skipped": skipped_count, "excluded": excluded_count,
                    })
                continue
            if dry_run:
                subject, _ = subject_period_from_title(sheet_title)
                preview = parse_journal_values(values, grade=grade, subject=subject, sheet_title=sheet_title,
                                               sheet_id=sheet_id, period_number=bound_period)
                counts = {key: len(preview[key]) for key in ("assessments", "students", "results", "summaries")}
            else:
                counts = sync_journal_values(
                    database, values, spreadsheet_id=target_id, spreadsheet_title=title,
                    grade=grade, subject=sheet_title.strip(), sheet_title=sheet_title, sheet_id=sheet_id,
                    expected_term=bound_period,
                )
            outcomes.append({"sheet_id": sheet_id, "subject": sheet_title, "status": "success", "counts": counts})
        except Exception as error:
            diagnostic = _journal_error_diagnostic(error)
            logging.getLogger(__name__).error("Journal tab import failed grade=%s error_type=%s category=%s", grade, type(error).__name__, diagnostic["code"])
            try:
                if not dry_run and diagnostic["code"] != "period_conflict":
                    database.record_journal_failure(target_id, sheet_title.strip(), diagnostic["message"],
                        code=diagnostic["code"], details=diagnostic["details"],
                        grade=grade, spreadsheet_title=title, sheet_title=sheet_title, sheet_id=sheet_id)
            except Exception as record_error:
                logging.getLogger(__name__).error("Failed to persist journal tab failure grade=%s error_type=%s", grade, type(record_error).__name__)
            outcomes.append({"sheet_id": sheet_id, "subject": sheet_title, "status": "error",
                "error_code": diagnostic["code"], "error": diagnostic["message"], "error_details": diagnostic["details"]})
        if run_id is not None:
            succeeded = sum(item["status"] == "success" for item in outcomes)
            failed = sum(item["status"] == "error" for item in outcomes)
            database.update_journal_sync_progress(run_id, {
                "completed_tabs": len(outcomes), "total_tabs": total_tabs,
                "succeeded": succeeded, "failed": failed,
                "skipped": skipped_count, "excluded": excluded_count,
            })
    success_count = sum(item["status"] == "success" for item in outcomes)
    failure_count = sum(item["status"] == "error" for item in outcomes)
    if not dry_run and not success_count and not (skipped_count or excluded_count):
        raise GoogleLiveError("Не удалось импортировать ни одной вкладки; предыдущие данные сохранены")
    if trigger_kind == "manual" and not dry_run:
        database.record_audit_event("journal_sync.success", "journal_sync", {"grade": grade, "spreadsheet_id": target_id, "successful_tabs": success_count, "failed_tabs": len(outcomes) - success_count})
    status = "partial" if failure_count or (success_count and success_count + skipped_count + excluded_count != len(outcomes)) else "success"
    return {"spreadsheet_id": target_id, "spreadsheet_title": title, "grade": grade, "status": status,
            "skipped_tabs": skipped_count, "excluded_tabs": excluded_count, "tabs": outcomes}


def _refresh_scoped_journal(database, client, grade, target_id, title, valid_tabs, *, term_number, dry_run, run_id,
                             selected_sheet_ids, preflight_token, trigger_kind="manual"):
    """Plan selected tabs with one Google read each, then write only if all pass."""
    year = "2026/27"
    scope = database.journal_import_scope(target_id, grade, year)
    sources = scope["sources"]
    is_selection = selected_sheet_ids is not None or preflight_token is not None
    connected_ids = {str(row["sheet_id"]) for row in sources if row["sheet_id"] is not None
                     and row["grade"] == grade and row["academic_year"] == year and row["term_number"] == term_number
                     and row["period_status"] != "unassigned" and row["last_synced_at"] is not None
                     and not row["classification"]
                     and (trigger_kind != "scheduled" or row["auto_sync_enabled"])}
    excluded_ids = {str(row["sheet_id"]) for row in sources if row["excluded_from_journals"]}
    selected = []
    for tab in valid_tabs:
        tab_id = str(tab["sheetId"])
        explicit_period = subject_period_from_title(tab["title"])[1]
        matches_period = explicit_period == term_number or (explicit_period is None and tab_id in connected_ids)
        if matches_period and (dry_run or is_selection or (tab_id in connected_ids and tab_id not in excluded_ids)):
            selected.append(tab)
    registered_grade = next((number for number, book in CURRENT_JOURNAL_SPREADSHEETS.items() if book == target_id), grade)
    if registered_grade != grade or any(row["grade"] != grade for row in sources):
        raise ValueError("Выбранный класс не соответствует этому источнику журнала.")

    def subject_key(value):
        return " ".join(str(value or "").casefold().replace("ё", "е").split())

    occupied = {}
    for row in sources:
        if row["academic_year"] == year and row["term_number"] == term_number and not row["classification"]:
            occupied.setdefault(subject_key(row["subject"]), []).append(row)
    incoming = {}
    metadata_ids = Counter(str(tab["sheetId"]) for tab in valid_tabs)
    for tab in selected:
        if not classify_journal_values([], sheet_title=tab["title"]):
            label, _ = subject_period_from_title(tab["title"])
            key = subject_key(label)
            incoming[key] = incoming.get(key, 0) + 1

    rows, prepared = [], []
    for tab in selected:
        sheet_id, sheet_title = str(tab["sheetId"]), str(tab["title"])
        # Instructions are never exposed as pickable subject rows, even if a
        # valid subject ID has been unexpectedly repurposed as an instruction.
        if classify_journal_values([], sheet_title=sheet_title):
            continue
        row = {"sheet_id": sheet_id, "subject": sheet_title, "connected": sheet_id in connected_ids}
        rows.append(row)
        if grade == 7 and deferred_grade7_second_period_subject(sheet_title, term_number):
            row.update(status="skipped", classification="deferred_grade7_period2",
                       reason="Импорт математики и физики 7 класса за II период временно отложен.")
            continue
        if metadata_ids[sheet_id] > 1:
            row.update(status="error", error_code="sheet_identity_conflict", error="ID вкладки встречается в книге больше одного раза.")
            continue
        stable = [prior for prior in sources if str(prior["sheet_id"]) == sheet_id or prior["sheet_title"] == sheet_title]
        if any(prior["term_number"] not in (None, term_number) or prior["academic_year"] not in (None, year)
               or (prior["term_number"] is None and prior["last_synced_at"] is not None) for prior in stable):
            row.update(status="error", error_code="period_conflict", error="ID или название вкладки уже привязано к другому периоду.")
            continue
        if any(prior["sheet_id"] is not None and str(prior["sheet_id"]) != sheet_id for prior in stable):
            row.update(status="error", error_code="sheet_identity_conflict", error="Название уже связано с другим ID вкладки.")
            continue
        auxiliary = classify_journal_values([], sheet_title=sheet_title)
        if auxiliary:
            row.update(status="skipped", classification=auxiliary["kind"], reason=auxiliary["reason"])
            continue
        label, _ = subject_period_from_title(sheet_title)
        key = subject_key(label)
        matches = occupied.get(key, [])
        if incoming[key] > 1 or any(
            str(prior["sheet_id"]) != sheet_id and (prior["sheet_id"] is not None or prior["sheet_title"] != sheet_title)
            for prior in matches):
            row.update(status="error", error_code="duplicate_subject_period", error="Другой ID вкладки уже связан с этим предметом и периодом.")
            continue
        if any(prior["excluded_from_journals"] for prior in stable):
            row.update(status="error", error_code="excluded_subject", error="Вкладка исключена из журналов. Проверьте настройки источника.")
            continue
        try:
            escaped_title = sheet_title.replace("'", "''")
            values = client.sheet_values(target_id, f"'{escaped_title}'!A:ZZ", value_render_option="UNFORMATTED_VALUE")
            auxiliary = classify_journal_values(values, sheet_title=sheet_title)
            if auxiliary:
                rows.remove(row)
                continue
            parsed = parse_journal_values(values, grade=grade, subject=label, sheet_title=sheet_title,
                                          sheet_id=sheet_id, period_number=term_number)
            # Match the snapshot writer: prefer exact GID, then an unupgraded
            # record with the same title. This read does not reconnect a tab.
            prior = next((item for item in stable if str(item["sheet_id"]) == sheet_id), None)
            if prior is None:
                prior = next((item for item in stable if item["sheet_id"] is None and item["sheet_title"] == sheet_title), None)
            if prior is not None:
                database.preview_journal_group_label_import(prior["id"], parsed["assessments"], parsed["students"])
            row.update(status="success", counts={name: len(parsed[name]) for name in ("assessments", "students", "results", "summaries")})
            prepared.append((row, label, parsed))
        except Exception as error:
            diagnostic = _journal_error_diagnostic(error)
            row.update(status="error", error_code=diagnostic["code"], error=diagnostic["message"])

    def metadata_identity(tabs):
        return sorted((str(tab["sheetId"]), str(tab["title"])) for tab in tabs
                      if str(tab.get("title", "")).strip() and tab.get("sheetId") is not None)

    token = hashlib.sha256(json.dumps({
        "book": target_id, "grade": grade, "term": term_number, "year": year,
        "metadata": metadata_identity(valid_tabs),
        "sources": sorted(sources, key=lambda row: str(row["id"])),
        "rows": rows, "data": [(row["sheet_id"], parsed["source_hash"]) for row, _, parsed in prepared],
    }, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest() if dry_run or is_selection else None
    message = None
    if is_selection and not dry_run:
        requested = selected_sheet_ids or []
        eligible = {row["sheet_id"] for row, _, _ in prepared}
        if not preflight_token or token != preflight_token:
            message = "Список вкладок или данные изменились после проверки. Проверьте таблицу ещё раз."
        elif not requested or len(set(requested)) != len(requested) or not set(requested) <= eligible:
            message = "Выберите хотя бы одну проверенную предметную вкладку из этого источника."
        else:
            # Values are read using exact escaped tab titles. Recheck that the
            # title-to-GID mapping did not change during those Google reads.
            latest = client.spreadsheet(target_id)
            latest_tabs = [sheet.get("properties") or {} for sheet in latest.get("sheets") or []]
            if metadata_identity(latest_tabs) != metadata_identity(valid_tabs):
                message = "Вкладки изменились во время проверки. Проверьте таблицу ещё раз."
            else:
                chosen = set(requested)
                rows = [row for row in rows if row["sheet_id"] in chosen]
                prepared = [entry for entry in prepared if entry[0]["sheet_id"] in chosen]
    elif not dry_run and not prepared and not rows:
        message = "Нет подключённых предметных вкладок. Сначала выберите их при подключении."
    blocked = bool(message) or not prepared or (not dry_run and any(row["status"] == "error" for row in rows))
    own_run = None
    if not dry_run and not blocked and is_selection:
        own_run = database.start_journal_sync(target_id, grade, None, "manual", book_lock_held=True)
        if own_run is None:
            raise JournalImportBusy("Для этой таблицы уже выполняется синхронизация.")
        run_id = own_run
    if not dry_run and not blocked:
        for index, (row, label, parsed) in enumerate(prepared, start=1):
            try:
                saved_source = {"spreadsheet_id": target_id, "spreadsheet_title": title, "grade": grade,
                                "subject": label, "sheet_title": row["subject"], "sheet_id": row["sheet_id"],
                                "academic_year": year, "term_number": term_number, "expected_term": term_number,
                                "source_hash": parsed["source_hash"]}
                row["counts"] = database.save_journal_snapshot(
                    saved_source, parsed["assessments"], parsed["results"], parsed["students"], parsed["summaries"])
            except Exception as error:
                diagnostic = _journal_error_diagnostic(error)
                row.pop("counts", None)
                row.update(status="error", error_code=diagnostic["code"], error=diagnostic["message"])
            if run_id is not None:
                try:
                    database.update_journal_sync_progress(run_id, {
                        "completed_tabs": len(rows) - len(prepared) + index, "total_tabs": len(rows),
                    })
                except Exception:
                    logging.getLogger(__name__).warning("Journal import progress update unavailable")
    # A real refresh of an already connected tab must leave an actionable
    # diagnostic on that stable source. Preflight and selected imports remain
    # read-only/atomic, and record_journal_failure preserves the old snapshot.
    if not dry_run and not is_selection:
        for row in rows:
            if row.get("connected") and row.get("status") == "error":
                label, _ = subject_period_from_title(row["subject"])
                database.record_journal_failure(
                    target_id, label, row["error"], code=row.get("error_code"),
                    grade=grade, spreadsheet_title=title, sheet_title=row["subject"],
                    sheet_id=row["sheet_id"],
                )
    succeeded = sum(row["status"] == "success" for row in rows) if dry_run or not blocked else 0
    failures = sum(row["status"] == "error" for row in rows)
    skipped = sum(row["status"] == "skipped" for row in rows)
    if not dry_run and blocked:
        for row in rows:
            if row["status"] == "success":
                row.update(status="not_imported", reason="Импорт остановлен: не все вкладки прошли проверку.")
    status = "blocked" if blocked else "failed" if not succeeded else "partial" if failures else "success"
    if own_run is not None:
        database.finish_journal_sync(own_run, status="error" if status == "failed" else status,
                                     counts={"tabs": len(rows), "succeeded": succeeded, "failed": failures})
    return {"spreadsheet_id": target_id, "spreadsheet_title": title, "grade": grade,
            "status": status,
            "successful_tabs": succeeded, "failed_tabs": failures, "skipped_tabs": skipped,
            "excluded_tabs": 0, "tabs": rows, "message": message,
            **({"preflight_token": token} if dry_run and prepared else {})}


def _journal_error_diagnostic(error: Exception) -> dict[str, str]:
    """Return admin-safe diagnostics; never copy request URLs, tokens, or roster rows to logs."""
    from backend.services.google_live import GoogleApiError
    if isinstance(error, JournalGroupLabelConflict):
        return {"code": "journal_group_label_conflict", "message": str(error), "details": "Прежние оценки и связи сохранены."}
    if isinstance(error, ValueError):
        detail = str(error)[:700]
        if any(token in detail.casefold() for token in ("конфликт сопоставления", "невозможно однозначно сопоставить", "неоднозначное сопоставление")):
            return {"code": "assessment_mapping_conflict", "message": "Несколько работ совпадают. Проверьте даты и названия работ перед повтором.", "details": "Предыдущие оценки сохранены."}
        if "класс или учебный год вкладки" in detail.casefold():
            return {"code": "source_scope_conflict", "message": "Вкладка уже относится к другому классу или учебному году.", "details": "Предыдущие оценки сохранены."}
        if "другой id вкладки" in detail.casefold():
            return {"code": "duplicate_subject_period", "message": "Другой ID вкладки уже связан с этим предметом и периодом.", "details": "Предыдущие оценки сохранены."}
        if "период вкладки отличается" in detail.casefold():
            return {"code": "period_conflict", "message": "ID вкладки уже относится к другому триместру. Проверьте источник.", "details": "Предыдущие оценки сохранены."}
        if "не найден блок" in detail.casefold() or "неполный блок журнала" in detail.casefold():
            return {"code": "unsupported_structure", "message": "Структура вкладки не распознана как журнал. Предыдущие данные сохранены.", "details": detail}
        return {"code": "invalid_journal_data", "message": "Данные вкладки не прошли проверку. Предыдущие данные сохранены.", "details": detail}
    if isinstance(error, GoogleApiError):
        status = int(getattr(error, "status", 0) or 0)
        reason = str(getattr(error, "reason", "") or "")
        code = str(getattr(error, "error_code", "") or "")
        if code == "invalid_grant":
            return {"code": "google_authorization", "message": "Истекла или отозвана авторизация Google. Переподключите аккаунт.", "details": "Google OAuth: invalid_grant"}
        if status == 403 or reason == "insufficientPermissions":
            return {"code": "google_access", "message": "У подключённого аккаунта нет доступа к вкладке.", "details": f"Google Sheets API: HTTP {status or 'unknown'} ({reason or code or 'access denied'})"}
        if status == 429 or status >= 500:
            return {"code": "temporary_google_error", "message": "Google Sheets временно не ответил. Повторите синхронизацию.", "details": f"Google Sheets API: HTTP {status}"}
        return {"code": "google_request_error", "message": "Google Sheets отклонил запрос. Проверьте подключение и права.", "details": f"Google Sheets API: HTTP {status or 'unknown'}"}
    if isinstance(error, GoogleLiveError):
        message = str(error).casefold()
        if any(token in message for token in ("oauth", "authorization", "token", "аккаунт")):
            return {"code": "google_authorization", "message": "Проблема авторизации Google. Переподключите аккаунт.", "details": type(error).__name__}
        return {"code": "temporary_google_error", "message": "Google Sheets временно недоступен. Повторите синхронизацию.", "details": type(error).__name__}
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return {"code": "temporary_server_error", "message": "Временная ошибка сервера. Повторите синхронизацию.", "details": type(error).__name__}
    diagnostic = getattr(error, "diag", None)
    if diagnostic is not None:
        return {"code": "temporary_server_error", "message": "Временная ошибка сервера. Предыдущие данные сохранены.", "details": type(error).__name__}
    return {"code": "unexpected_sync_error", "message": "Не удалось обновить вкладку. Предыдущие данные сохранены.", "details": type(error).__name__}


def _safe_journal_error_message(error: Exception) -> str:
    return _journal_error_diagnostic(error)["code"]


def refresh_teacher_directory(database: Database, settings: Settings) -> dict[str, Any]:
    """Read the authoritative teacher-assignment tab and reconcile only teachers."""
    client, _ = _client(settings, database)
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    metadata = client.spreadsheet(spreadsheet_id)
    title = str((metadata.get("properties") or {}).get("title", ""))
    tabs = [str((sheet.get("properties") or {}).get("title", "")) for sheet in metadata.get("sheets") or []]
    sheet_title = _resolve_sheet_title(tabs, SHEET_NAME)
    if not sheet_title:
        raise GoogleLiveError(f"Teacher directory tab {SHEET_NAME} was not found in {title}")
    escaped_title = sheet_title.replace("'", "''")
    values = client.sheet_values(spreadsheet_id, f"'{escaped_title}'!A:D")
    result = sync_teacher_directory(database, values, spreadsheet_id=spreadsheet_id, spreadsheet_title=title)
    database.record_audit_event("teacher_directory_sync.success", "teacher_directory", {"spreadsheet_id": spreadsheet_id, **result})
    return {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": title, "sheet_title": sheet_title, **result}


def refresh_teacher_directory_dry_run(database: Database, settings: Settings) -> dict[str, Any]:
    """Read the structured teacher source and compare it with current state.

    This path intentionally performs no snapshot, issue, identity, assignment,
    audit, or membership writes.  It is safe to run against production DB
    credentials for an operator-facing preview.
    """
    spreadsheet_id, title, sheet_title, values = _structured_teacher_source(settings, database)
    with database.connection() as connection:
        teachers, groups, current_assignments, canonical_subjects = _reconciliation_inputs(database, connection)
    plan = build_teacher_reconciliation_plan(values, canonical_teachers=teachers, canonical_groups=groups, canonical_subjects=canonical_subjects, current_assignments=current_assignments)
    return {
        "mode": "dry_run",
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": title,
        "sheet_title": sheet_title,
        "report": render_teacher_reconciliation_report(plan, spreadsheet_id=spreadsheet_id, spreadsheet_title=title),
        **plan.to_dict(),
    }


def _apply_plan(database: Database, connection: Any, plan: TeacherReconciliationPlan) -> dict[str, Any]:
    if plan.counts.get("unresolved_audience", 0) or plan.counts.get("conflicts", 0) or plan.counts.get("unmatched_teacher", 0) or plan.counts.get("unmatched_subject", 0):
        raise TeacherDirectoryApplyBlocked(
            f"Teacher reconciliation is not safe to apply: unresolved={plan.counts.get('unresolved_audience', 0)}, conflicts={plan.counts.get('conflicts', 0)}, unmatched_teacher={plan.counts.get('unmatched_teacher', 0)}, unmatched_subject={plan.counts.get('unmatched_subject', 0)}"
        )

    current_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in plan.current_assignments:
        current_by_key.setdefault((str(item["teacher_id"]), str(item["group_id"]), str(item["subject"])), []).append(dict(item))
    duplicate_ids = {str(item["id"]) for item in plan.duplicate_assignments}
    deactivate = []
    for item in [*plan.stale_assignments, *plan.duplicate_assignments]:
        reason = "exact duplicate of an active canonical assignment" if item in plan.duplicate_assignments else "absent from the authoritative structured teacher source"
        supersedes = "same teacher + subject + canonical audience natural key" if item in plan.duplicate_assignments else "current Учителя и группы — данные source of truth"
        deactivate.append({**dict(item), "reason": reason, "superseded_by": supersedes})
        database.execute(connection, "UPDATE teacher_assignments SET active = FALSE, valid_until = CURRENT_DATE WHERE id = ?", (item["id"],))
        _audit_in_transaction(database, connection, "teacher_assignment.deactivated", "teacher_assignment", {"assignment_id": item["id"], "teacher": item.get("teacher_name"), "subject": item.get("subject"), "audience": item.get("group_name"), "reason": reason, "superseded_by": supersedes})

    creates = []
    keeps = []
    updates = []
    for desired in plan.assignments:
        key = desired.natural_key
        active = [item for item in current_by_key.get(key, []) if str(item.get("id")) not in duplicate_ids]
        if active:
            survivor = active[0]
            changed = any(survivor.get(field) != value for field, value in {
                "base_class_name": desired.base_class_name,
                "subject_subgroup": desired.subject_subgroup,
                "exam_track": desired.exam_track,
            }.items()) or survivor.get("valid_until") is not None
            if changed and survivor.get("source") not in MANUAL_ASSIGNMENT_SOURCES:
                database.execute(
                    connection,
                    """UPDATE teacher_assignments
                       SET base_class_name = ?, subject_subgroup = ?, exam_track = ?, valid_until = NULL
                       WHERE id = ?""",
                    (desired.base_class_name, desired.subject_subgroup, desired.exam_track, survivor["id"]),
                )
                updates.append({"teacher": desired.teacher_name, "subject": desired.subject, "audience": desired.group_name, "assignment_id": survivor.get("id")})
            else:
                keeps.append({"teacher": desired.teacher_name, "subject": desired.subject, "audience": desired.group_name, "assignment_id": survivor.get("id"), "source": survivor.get("source"), "source_ref": survivor.get("source_ref")})
            continue
        database.execute(
            connection,
            """INSERT INTO teacher_assignments(teacher_identity_id, group_id, subject, base_class_name, subject_subgroup,
                   exam_track, capability, source, source_ref, active)
               VALUES (?, ?, ?, ?, ?, ?, 'teach', 'official_import', ?, TRUE)
               ON CONFLICT(teacher_identity_id, group_id, subject, capability, source, source_ref)
               DO UPDATE SET active = TRUE, base_class_name = excluded.base_class_name,
                   subject_subgroup = excluded.subject_subgroup, exam_track = excluded.exam_track,
                   valid_until = NULL""",
            (desired.teacher_id, desired.group_id, desired.subject, desired.base_class_name, desired.subject_subgroup, desired.exam_track, desired.source_ref),
        )
        created = {"teacher": desired.teacher_name, "subject": desired.subject, "audience": desired.group_name, "source_ref": desired.source_ref}
        creates.append(created)
        _audit_in_transaction(database, connection, "teacher_assignment.created", "teacher_assignment", created)

    result = {
        "CREATE": creates,
        "UPDATE": updates,
        "DEACTIVATE": deactivate,
        "DELETE PHYSICALLY": [],
        "KEEP": keeps,
        "PROTECTED / CANNOT DECIDE": [dict(item) for item in plan.manual_protected_assignments],
    }
    _audit_in_transaction(database, connection, "teacher_directory_reconciliation.applied", "teacher_directory", {"created": len(creates), "updated": len(updates), "deactivated": len(deactivate), "physically_deleted": 0, "protected": len(plan.manual_protected_assignments), "student_memberships_touched": 0})
    return result


def refresh_teacher_directory_reconciliation_apply(database: Database, settings: Settings) -> dict[str, Any]:
    """Apply the guarded structured teacher-directory reconciliation to production."""
    spreadsheet_id, title, sheet_title, values = _structured_teacher_source(settings, database)
    with directory_import_connection(database) as connection:
        foundation = _ensure_literature_11_base(database, connection)
        teachers, groups, current_assignments, canonical_subjects = _reconciliation_inputs(database, connection)
        plan = build_teacher_reconciliation_plan(values, canonical_teachers=teachers, canonical_groups=groups, canonical_subjects=canonical_subjects, current_assignments=current_assignments)
        actions = _apply_plan(database, connection, plan)
        counts = database.execute(connection, """SELECT
            (SELECT COUNT(*) FROM memberships WHERE active IS TRUE) AS active_student_memberships,
            (SELECT COUNT(*) FROM teacher_assignments WHERE active IS TRUE) AS active_teacher_assignments,
            (SELECT COUNT(DISTINCT teacher_identity_id) FROM teacher_assignments WHERE active IS TRUE) AS active_teachers,
            (SELECT COUNT(*) FROM teacher_assignments ta1 JOIN teacher_assignments ta2
             ON ta1.teacher_identity_id = ta2.teacher_identity_id AND ta1.group_id = ta2.group_id
             AND ta1.subject = ta2.subject AND ta1.active IS TRUE AND ta2.active IS TRUE AND ta1.id < ta2.id) AS duplicate_active_assignments""").fetchone()
    result = {
        "mode": "apply",
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": title,
        "sheet_title": sheet_title,
        "foundation": foundation,
        "counts": dict(plan.counts),
        "apply_plan": actions,
        "final_counts": dict(counts),
        "student_memberships_touched": foundation["memberships_added"],
        "report": render_teacher_reconciliation_report(plan, spreadsheet_id=spreadsheet_id, spreadsheet_title=title),
        **plan.to_dict(),
    }
    database.record_audit_event("teacher_directory_reconciliation.success", "teacher_directory", {"spreadsheet_id": spreadsheet_id, "final_counts": result["final_counts"], "student_memberships_touched": result["student_memberships_touched"]})
    return result
