from __future__ import annotations

import json
from typing import Any

from backend.config import Settings
from backend.database import Database
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.google_sheets import import_school_schedule_tabs
from backend.services.journal_import import sync_journal_values
from backend.services.teacher_directory import SHEET_NAME, sync_teacher_directory
from backend.services.teacher_directory_reconciliation import SHEET_NAME as STRUCTURED_TEACHER_SHEET, TeacherReconciliationPlan, build_teacher_reconciliation_plan, render_teacher_reconciliation_report
from backend.services.schedule_pipeline import classify_tab, looks_like_schedule_matrix, refresh_schedule_pipeline


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
    store = GoogleTokenStore()
    token = store.load()
    if not token or not token.get("refresh_token"):
        raise GoogleLiveError("Stored Google refresh token is required")
    client = GoogleLiveClient(config, token, store)
    account = client.userinfo()
    email = str(account.get("email", "")).strip()
    if not email:
        raise GoogleLiveError("Google userinfo did not return an email")
    return client, email


class TeacherDirectoryApplyBlocked(RuntimeError):
    """The authoritative teacher source is not safe to apply yet."""


def _structured_teacher_source(settings: Settings) -> tuple[str, str, str, list[list[str]]]:
    client, _ = _client(settings)
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


def refresh_schedule_v1(database: Database, settings: Settings) -> dict[str, Any]:
    """Refresh Schedule Integration v1 through the School Data lifecycle."""
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    if not spreadsheet_id:
        reason = "GOOGLE_SHEETS_SPREADSHEET_ID is required"
        return {"status": "blocked", "auth_state": "missing_spreadsheet_id", "error": reason, "message": reason}
    if not GoogleTokenStore().has_refresh_token():
        reason = "Authorize Google OAuth and store a refresh token before refreshing schedule"
        return {"status": "blocked", "auth_state": "missing_google_token", "error": reason, "message": reason}
    client, _ = _client(settings)
    metadata = client.spreadsheet(spreadsheet_id)
    spreadsheet_title = str((metadata.get("properties") or {}).get("title", ""))
    visible = [sheet for sheet in metadata.get("sheets") or [] if not (sheet.get("properties") or {}).get("hidden")]
    visible_titles = [str((sheet.get("properties") or {}).get("title", "")) for sheet in visible]
    plain_ranges = [f"'{title.replace(chr(39), chr(39) * 2)}'!A1:V200" for title in visible_titles]
    plain_values = client.sheet_values_many(spreadsheet_id, plain_ranges)
    tabs = []
    for sheet, values in zip(visible, plain_values):
        props = sheet.get("properties") or {}
        tab_title = str(props.get("title", ""))
        schedule_like = looks_like_schedule_matrix(values)
        if classify_tab(tab_title, schedule_like=schedule_like) == "irrelevant":
            continue
        grid = client.sheet_grid_range(spreadsheet_id, tab_title, "A1:V200")
        grid_sheet = (grid.get("sheets") or [{}])[0]
        data = (grid_sheet.get("data") or [{}])[0]
        rows = []
        for row in data.get("rowData") or []:
            rows.append([value for value in (row.get("values") or [])])
        tabs.append({"sheet_id": props.get("sheetId"), "title": tab_title, "merges": sheet.get("merges") or grid_sheet.get("merges") or [], "values": rows})
    return refresh_schedule_pipeline(database, spreadsheet_id, spreadsheet_title, tabs)


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


def refresh_teacher_directory(database: Database, settings: Settings) -> dict[str, Any]:
    """Read the authoritative teacher-assignment tab and reconcile only teachers."""
    client, _ = _client(settings)
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
    spreadsheet_id, title, sheet_title, values = _structured_teacher_source(settings)
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
            if changed:
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
    spreadsheet_id, title, sheet_title, values = _structured_teacher_source(settings)
    with database.connection() as connection:
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
