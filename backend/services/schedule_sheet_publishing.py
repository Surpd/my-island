"""Saved Schedule Studio revision -> idempotent, add-only Google export.

Credentials stay in the existing server token store. The first rollout is
restricted to the explicitly authorized test spreadsheet, never the school source.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from typing import Any

from backend.database import Database
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore, load_google_client
from backend.services.google_oauth import google_config
from backend.services.schedule_experiments import ExperimentConflict, get_experiment
from backend.services.schedule_sheet_export import build_sheet_plan, freeze_week, sheets_requests

TEST_EXPORT_TARGET = "11VZK05M0QNV3sGny2PLWL3_yiuyuQSVnj-6QtIa5cDQ"
SHEETS_WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
EXPORT_POLICY = "compact-activities-v2"


class ExportAuthorizationRequired(Exception):
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


def _target() -> str:
    target = os.getenv("GOOGLE_SCHEDULE_EXPORT_SPREADSHEET_ID", TEST_EXPORT_TARGET)
    if target != TEST_EXPORT_TARGET:
        raise ValueError("This rollout only permits the approved test export spreadsheet")
    return target


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
    draft = get_experiment(database, experiment_id)
    if int(draft["revision"]) != expected_revision:
        raise ExperimentConflict(draft)
    payload = draft["payload"]
    week = str(payload.get("week_start") or "")
    if not week or date.fromisoformat(week).weekday() != 0:
        raise ValueError("Сначала создайте отдельную неделю с датой понедельника")
    target = _target()
    directory_database = directory_database or database
    presentation_colors = _presentation_teacher_colors(directory_database)
    color_fingerprint = ""
    if presentation_colors is not None:
        color_fingerprint = hashlib.sha256(json.dumps(sorted(presentation_colors.items()), ensure_ascii=False,
                                                       separators=(",", ":")).encode()).hexdigest()
    digest_input = f"{experiment_id}:{expected_revision}:{target}:{EXPORT_POLICY}"
    if presentation_colors is not None:
        digest_input += f":teacher-ui:{color_fingerprint}"
    digest = hashlib.sha256(digest_input.encode()).hexdigest()
    with database.connection() as connection:
        completed = database.execute(connection, """SELECT * FROM schedule_sheet_exports
            WHERE export_key=? AND status='exported'
            ORDER BY created_at DESC LIMIT 1""", (digest,)).fetchone()
    if completed:
        return _decode(completed)
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
    snapshot = freeze_week(source, week)
    # Add presentation preferences after freeze_week so UI colors never alter
    # the frozen lesson content snapshot or its source snapshot ID.
    if presentation_colors is not None:
        for teacher in snapshot["teachers"]:
            color = presentation_colors.get(str(teacher["id"]))
            if color:
                teacher["ui_color"] = color
    with database.connection() as connection:
        old = database.execute(connection, "SELECT * FROM schedule_sheet_exports WHERE export_key=?", (digest,)).fetchone()
    if old:
        return _decode(old)
    sheet_id = int(digest[:8], 16) % 2_000_000_000 + 1
    title = f"MI · {week} · r{expected_revision} · {digest[:6]}"
    notes = {str(l["id"]): [str(l["note"])] for l in payload["lessons"] if l.get("note")}
    plan = build_sheet_plan(snapshot, title=title, sheet_id=sheet_id, mapping_sheet_id=sheet_id+1,
                            layout="final-compact", existing_notes=notes)
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO schedule_sheet_exports
            (export_key,draft_id,revision,target_id,sheet_id,mapping_sheet_id,title,status,snapshot,plan)
            VALUES (?,?,?,?,?,?,?,'prepared',?,?) ON CONFLICT(export_key) DO NOTHING""",
            (digest, experiment_id, expected_revision, target, sheet_id, sheet_id+1, title,
             json.dumps(snapshot, ensure_ascii=False, default=str), json.dumps(plan, ensure_ascii=False, default=str)))
        row = database.execute(connection, "SELECT * FROM schedule_sheet_exports WHERE export_key=?", (digest,)).fetchone()
    return _decode(row)


def complete_export(database: Database, export: dict[str, Any]) -> dict[str, Any]:
    """Called only after an authenticated writer verifies the real Google result."""
    url = f"https://docs.google.com/spreadsheets/d/{export['target_id']}/edit#gid={export['sheet_id']}"
    with database.connection() as connection:
        database.execute(connection, "UPDATE schedule_sheet_exports SET status='exported',url=?,updated_at=CURRENT_TIMESTAMP WHERE export_key=?",
                         (url, export["export_key"]))
    return {"status": "exported", "url": url, "export_key": export["export_key"], "revision": export["revision"]}


def _verify_remote(export: dict[str, Any], client: Any) -> bool:
    metadata = client.spreadsheet(export["target_id"])
    by_id = {s["properties"]["sheetId"]: s["properties"] for s in metadata.get("sheets", [])}
    main, hidden = by_id.get(export["sheet_id"]), by_id.get(export["mapping_sheet_id"])
    if not main and not hidden:
        return False
    if not main or not hidden or main["title"] != export["title"] or hidden["title"] != f"_MI {export['sheet_id']}":
        raise ValueError("Export sheet ID collision; existing tabs were not modified")
    check = client.sheet_values(export["target_id"], f"'{hidden['title']}'!A1:B1")
    if check != [["snapshot_id", export["snapshot"]["snapshot_id"]]]:
        raise ValueError("Export snapshot does not match; existing tabs were not modified")
    return True


def publish_export(database: Database, export: dict[str, Any], client: Any) -> dict[str, Any]:
    if export["target_id"] != TEST_EXPORT_TARGET:
        raise ValueError("Unapproved export target")
    # Deterministic IDs plus readback make retries safe, including a lost HTTP
    # response after Google's atomic batch was already committed.
    if not _verify_remote(export, client):
        try:
            client.batch_update_spreadsheet(export["target_id"], sheets_requests(export["plan"]))
        except GoogleLiveError:
            if not _verify_remote(export, client):
                raise
        if not _verify_remote(export, client):
            raise GoogleLiveError("Google export was not present after write")
    return complete_export(database, export)


def export_saved_week(database: Database, experiment_id: str, *, expected_revision: int,
                      settings: Any, directory_database: Database | None = None, client: Any = None) -> dict[str, Any]:
    export = prepare_export(database, experiment_id, expected_revision=expected_revision, directory_database=directory_database)
    if export["status"] == "exported":
        return {"status": "existing", "url": export["url"], "revision": export["revision"], "export_key": export["export_key"]}
    try:
        client = client or export_client(database, settings)
    except ExportAuthorizationRequired as error:
        return {"status": "authorization_required", "message": str(error), "revision": export["revision"],
                "export_key": export["export_key"], "reconnect_available": bool(
                    not settings.local_test_mode and os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") != "1"
                    and getattr(settings, "google_oauth_client_id", None) and getattr(settings, "google_oauth_client_secret", None)
                    and getattr(settings, "google_oauth_redirect_uri", None) and getattr(settings, "google_oauth_token_encryption_key", None))}
    return publish_export(database, export, client)
