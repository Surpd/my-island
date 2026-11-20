"""Saved Schedule Studio revision -> idempotent, add-only Google export.

Credentials stay in the existing server token store. One operator-approved
spreadsheet is selected per deployment. School source workbooks are read-only.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
from hmac import compare_digest
import json
import os
import re
from typing import Any

from backend.database import Database
from backend.services.google_contract import SCHOOL_SCHEDULE_SPREADSHEET_ID, SHEETS_WRITE_SCOPE
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore, load_google_client
from backend.services.google_oauth import google_config
from backend.services.schedule_experiments import ExperimentConflict, get_experiment
from backend.services.schedule_sheet_export import build_sheet_plan, freeze_template, freeze_week, sheets_requests

TEST_EXPORT_TARGET = "11VZK05M0QNV3sGny2PLWL3_yiuyuQSVnj-6QtIa5cDQ"
EXPORT_POLICY = "compact-activities-v3"
LEGACY_WEEK_POLICY = "compact-activities-v2"


class ExportAuthorizationRequired(Exception):
    pass


class ExportPreviewConflict(ValueError):
    pass


def export_client(database: Database, settings: Any) -> GoogleLiveClient:
    if settings.local_test_mode:
        raise ExportAuthorizationRequired("Для экспорта нужен доступ backend к Google Sheets. Локальный тестовый профиль не использует Google-секреты.")
    if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
        raise ExportAuthorizationRequired("Для локальной школьной среды нужно отдельное серверное подключение Google. Подключение production в режиме только чтения не используется.")
    if not google_config(settings).oauth_ready or not settings.google_oauth_token_encryption_key:
        raise ExportAuthorizationRequired("Настройте защищённое серверное подключение Google для экспорта.")
    if not database.get_google_oauth_credential():
        raise ExportAuthorizationRequired("Подключите Google для экспорта. Токены локальных CLI не используются приложением.")
    store = GoogleTokenStore(database=database, encryption_key=settings.google_oauth_token_encryption_key)
    try:
        client, account = load_google_client(google_config(settings), store, settings.google_oauth_allowed_email)
    except GoogleLiveError as error:
        raise ExportAuthorizationRequired("Переподключите Google в настройках сервера.") from error
    token = client.token
    if not token or SHEETS_WRITE_SCOPE not in str(token.get("scope") or "").split():
        raise ExportAuthorizationRequired("Подключение Google имеет только чтение или отсутствует. Администратору нужно разрешить экспорт Google Sheets при переподключении Google.")
    if account != settings.google_oauth_allowed_email.casefold():
        raise ExportAuthorizationRequired("Аккаунт серверного подключения Google не подтверждён для My Island.")
    return client


def _target(school_source: Any = None) -> str:
    target = os.getenv("GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID", TEST_EXPORT_TARGET).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
        raise ValueError("Укажите ID книги Google Sheets для выгрузок без ссылки.")
    protected = {SCHOOL_SCHEDULE_SPREADSHEET_ID, os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()}
    if isinstance(school_source, Mapping):
        protected.add(str(school_source.get("spreadsheet_id") or "").strip())
    if target in protected:
        raise ValueError("Исходная книга школьного расписания доступна только для чтения.")
    approved = {entry.strip() for entry in os.getenv("GOOGLE_SCHEDULE_EXPORT_APPROVED_SPREADSHEET_IDS", "").split(",")}
    if target != TEST_EXPORT_TARGET and target not in approved:
        raise ValueError("Книга для выгрузок ещё не разрешена в настройках сервера.")
    return target


def _check_selected_target(export: Mapping[str, Any]) -> str:
    source = export["snapshot"]["source"].get("school_source")
    target = _target(source)
    if export["target_id"] != target:
        raise ExportPreviewConflict("Книга для выгрузок изменилась. Откройте предпросмотр заново.")
    return target


def _spreadsheet_url(target: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{target}/edit"


def _worksheet_url(export: Mapping[str, Any]) -> str:
    return f"{_spreadsheet_url(_check_selected_target(export))}#gid={export['sheet_id']}"


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("snapshot", "plan"):
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    return result


def _presentation_teacher_colors(database: Database) -> dict[str, str] | None:
    """Read shared teacher UI colors only for the explicitly consolidated export."""
    if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
        return None
    from backend.services.teacher_ui_colors import list_colors

    return {str(item["teacher_id"]): str(item["color"]) for item in list_colors(database, "school")}


def prepare_export(database: Database, experiment_id: str, *, expected_revision: int,
                   directory_database: Database | None = None) -> dict[str, Any]:
    """Render the saved draft without opening a Google connection or saving records."""
    draft = get_experiment(database, experiment_id)
    if int(draft["revision"]) != expected_revision:
        raise ExperimentConflict(draft)
    payload = draft["payload"]
    week = str(payload.get("week_start") or "")
    if week and date.fromisoformat(week).weekday() != 0:
        raise ValueError("Укажите дату понедельника в черновике недели.")
    target = _target(payload.get("school_source"))
    directory_database = directory_database or database
    presentation_colors = _presentation_teacher_colors(directory_database)
    color_fingerprint = ""
    if presentation_colors is not None:
        color_fingerprint = hashlib.sha256(json.dumps(sorted(presentation_colors.items()), ensure_ascii=False,
                                                       separators=(",", ":")).encode()).hexdigest()

    def export_key(policy: str) -> str:
        digest_input = f"{experiment_id}:{expected_revision}:{target}:{policy}"
        if presentation_colors is not None:
            digest_input += f":teacher-ui:{color_fingerprint}"
        return hashlib.sha256(digest_input.encode()).hexdigest()

    digest = export_key(EXPORT_POLICY)
    legacy_prepared = None
    with database.connection() as connection:
        existing = database.execute(connection, "SELECT * FROM schedule_sheet_exports WHERE export_key=?", (digest,)).fetchone()
        if existing is None and week:
            legacy = database.execute(connection, "SELECT * FROM schedule_sheet_exports WHERE export_key=?",
                                      (export_key(LEGACY_WEEK_POLICY),)).fetchone()
            if legacy and legacy["status"] == "exported":
                existing = legacy
            elif legacy and legacy["status"] == "prepared":
                legacy_prepared = _decode(legacy)
    if existing:
        receipt = _decode(existing)
        _check_selected_target(receipt)
        return receipt
    with directory_database.connection() as connection:
        groups = [dict(r) for r in directory_database.execute(connection, "SELECT id,name,display_name,group_type,base_class_name,subject FROM groups ORDER BY id").fetchall()]
        teachers = [dict(r) for r in directory_database.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' ORDER BY id").fetchall()]
    for group in groups:
        group["id"] = str(group["id"])
    for teacher in teachers:
        teacher["id"] = str(teacher["id"])
    source = {"source": {"draft_id": experiment_id, "name": draft["name"], "revision": expected_revision,
                          "school_source": payload.get("school_source")},
              "payload": payload, "groups": groups, "teachers": teachers}
    snapshot = freeze_week(source, week) if week else freeze_template(source)
    if presentation_colors is not None:
        for teacher in snapshot["teachers"]:
            color = presentation_colors.get(str(teacher["id"]))
            if color:
                teacher["ui_color"] = color
    sheet_id = int(digest[:8], 16) % 2_000_000_000 + 1
    title = f"MI · {week or 'Шаблон'} · r{expected_revision} · {digest[:6]}"
    notes = {str(l["id"]): [str(l["note"])] for l in payload["lessons"] if l.get("note")}
    plan = build_sheet_plan(snapshot, title=title, sheet_id=sheet_id, mapping_sheet_id=sheet_id+1,
                            layout="final-compact", existing_notes=notes)
    return {"export_key": digest, "draft_id": experiment_id, "revision": expected_revision, "target_id": target,
            "sheet_id": sheet_id, "mapping_sheet_id": sheet_id + 1, "title": title, "status": "prepared",
            "snapshot": snapshot, "plan": plan, "legacy_prepared": legacy_prepared}


def _preview_token(export: dict[str, Any]) -> str:
    snapshot_id = export["snapshot"]["snapshot_id"]
    return hashlib.sha256(f"{export['export_key']}:{snapshot_id}".encode()).hexdigest()


def _export_summary(export: dict[str, Any]) -> dict[str, Any]:
    week = export["snapshot"]["payload"].get("week_start")
    return {"draft_id": export["draft_id"], "revision": export["revision"],
            "name": export["snapshot"]["source"]["name"], "kind": "week" if week else "template",
            "week_start": week, "title": export["title"], "export_key": export["export_key"]}


def preview_saved_export(database: Database, experiment_id: str, *, expected_revision: int,
                         directory_database: Database | None = None) -> dict[str, Any]:
    export = prepare_export(database, experiment_id, expected_revision=expected_revision,
                            directory_database=directory_database)
    target = _check_selected_target(export)
    plan = export["plan"]
    summary = {key: plan["summary"][key] for key in ("lessons", "placements", "clarifications")}
    summary.update(rows=plan["rows"], columns=plan["columns"])
    visible_fields = ("row", "col", "height", "width", "text", "bg", "fg", "bold")
    visible_cells = [{key: cell[key] for key in visible_fields} for cell in plan["cells"]]
    rows, columns = min(plan["rows"], 8), min(plan["columns"], 5)
    sample = [{**cell,
               "height": min(cell["height"], rows - cell["row"]),
               "width": min(cell["width"], columns - cell["col"])}
              for cell in visible_cells if cell["row"] < rows and cell["col"] < columns]
    row_heights = {int(row): height for row, height in plan["row_heights"].items()}
    day_labels = {cell["row"]: cell["text"] for cell in visible_cells if cell["col"] == 1}
    grid = {"rows": plan["rows"], "columns": plan["columns"], "cells": visible_cells,
            "column_widths": plan.get("column_widths") or [122] + [166] * (plan["columns"] - 1),
            "row_heights": [row_heights.get(row, 21) for row in range(plan["rows"])],
            "days": [{"row": row, "label": day_labels[row]} for row in plan["day_rows"] if row in day_labels]}
    return {**_export_summary(export), "preview_token": _preview_token(export), "summary": summary,
            "sample": {"rows": rows, "columns": columns, "cells": sample},
            "grid": grid,
            "target_url": _spreadsheet_url(target),
            "status": "existing" if export["status"] == "exported" else "prepared",
            **({"url": _worksheet_url(export)} if export["status"] == "exported" else {})}


def _record_export(database: Database, export: dict[str, Any]) -> dict[str, Any]:
    """Freeze the confirmed revision before the add-only request is sent."""
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO schedule_sheet_exports
            (export_key,draft_id,revision,target_id,sheet_id,mapping_sheet_id,title,status,snapshot,plan)
            SELECT ?,?,?,?,?,?,?,'prepared',?,? FROM schedule_experiment_drafts WHERE id=? AND revision=?
            ON CONFLICT(export_key) DO NOTHING""",
            (export["export_key"], export["draft_id"], export["revision"], export["target_id"],
             export["sheet_id"], export["mapping_sheet_id"], export["title"],
             json.dumps(export["snapshot"], ensure_ascii=False, default=str),
             json.dumps(export["plan"], ensure_ascii=False, default=str), export["draft_id"], export["revision"]))
        row = database.execute(connection, """SELECT e.* FROM schedule_sheet_exports e
            JOIN schedule_experiment_drafts d ON d.id=? AND d.revision=e.revision
            WHERE e.draft_id=? AND e.export_key=?""", (export["draft_id"], export["draft_id"], export["export_key"])).fetchone()
    if not row:
        raise ExperimentConflict(get_experiment(database, export["draft_id"]))
    return _decode(row)


def complete_export(database: Database, export: dict[str, Any]) -> dict[str, Any]:
    """Called only after an authenticated writer verifies the real Google result."""
    url = _worksheet_url(export)
    with database.connection() as connection:
        database.execute(connection, "UPDATE schedule_sheet_exports SET status='exported',url=?,updated_at=CURRENT_TIMESTAMP WHERE export_key=?",
                         (url, export["export_key"]))
    return {"status": "exported", "url": url, "export_key": export["export_key"], "revision": export["revision"]}


def _verify_remote(export: dict[str, Any], client: Any) -> bool:
    metadata = client.spreadsheet(_check_selected_target(export))
    by_id = {s["properties"]["sheetId"]: s["properties"] for s in metadata.get("sheets", [])}
    main, hidden = by_id.get(export["sheet_id"]), by_id.get(export["mapping_sheet_id"])
    if not main and not hidden:
        return False
    if not main or not hidden or main["title"] != export["title"] or hidden["title"] != f"_MI {export['sheet_id']}":
        raise ValueError("Export sheet ID collision; existing tabs were not modified")
    check = client.sheet_values(_check_selected_target(export), f"'{hidden['title']}'!A1:B1")
    if check != [["snapshot_id", export["snapshot"]["snapshot_id"]]]:
        raise ValueError("Export snapshot does not match; existing tabs were not modified")
    return True


def publish_export(database: Database, export: dict[str, Any], client: Any) -> dict[str, Any]:
    # Deterministic IDs plus readback make retries safe, including a lost HTTP
    # response after Google's atomic batch was already committed.
    if not _verify_remote(export, client):
        requests = sheets_requests(export["plan"])
        try:
            client.batch_update_spreadsheet(_check_selected_target(export), requests)
        except GoogleLiveError:
            if not _verify_remote(export, client):
                raise
        if not _verify_remote(export, client):
            raise GoogleLiveError("Google export was not present after write")
    return complete_export(database, export)


def export_saved_schedule(database: Database, experiment_id: str, *, expected_revision: int, preview_token: str,
                          settings: Any, directory_database: Database | None = None, client: Any = None) -> dict[str, Any]:
    export = prepare_export(database, experiment_id, expected_revision=expected_revision, directory_database=directory_database)
    _check_selected_target(export)
    if not compare_digest(_preview_token(export), preview_token):
        raise ExportPreviewConflict("Предпросмотр экспорта устарел. Откройте его заново.")
    summary = _export_summary(export)
    if export["status"] == "exported":
        return {**summary, "status": "existing", "url": _worksheet_url(export)}
    try:
        client = client or export_client(database, settings)
    except ExportAuthorizationRequired as error:
        return {**summary, "status": "authorization_required", "message": str(error), "reconnect_available": bool(
                    not settings.local_test_mode and os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") != "1"
                    and getattr(settings, "google_oauth_client_id", None) and getattr(settings, "google_oauth_client_secret", None)
                    and getattr(settings, "google_oauth_redirect_uri", None) and getattr(settings, "google_oauth_token_encryption_key", None))}
    legacy = export.get("legacy_prepared")
    if legacy and _verify_remote(legacy, client):
        # A previous release might have committed the Sheets batch but lost
        # the database receipt. Reconcile it without resending its old plan.
        return {**_export_summary(legacy), **complete_export(database, legacy), "status": "existing"}
    recorded = _record_export(database, export)
    _check_selected_target(recorded)
    if not compare_digest(_preview_token(recorded), preview_token):
        raise ExportPreviewConflict("Предпросмотр экспорта устарел. Откройте его заново.")
    if recorded["status"] == "exported":
        return {**summary, "status": "existing", "url": _worksheet_url(recorded)}
    return {**summary, **publish_export(database, recorded, client)}
