"""First-party read projections; never delegate to an administrator handler."""
from __future__ import annotations

from datetime import date
import hmac
from pathlib import Path
import json
import re
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from backend.database import Database
from backend.services.canonical_schedule import effective_status
from backend.services import codex_diagnostics as auth
from backend.services.schedule_experiments import ExperimentNotFound, get_experiment, list_experiments

PREFIX = "/api/diagnostics"
PRIVATE = {"Cache-Control": "no-store, private", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
_READ_VIEWS = {
    "overview": "overview", "system": "system", "google/status": "integration",
    "groups": "groups", "people": "directory", "school/directory": "directory",
    "school/auth/session": "session",
    "schedule/overview": "schedule", "schedule/observability": "schedule-index",
    "schedule/experiments/capabilities": "schedule",
    "schedule/experiments/school": "schedule-drafts",
    "schedule/experiments/school/directory": "schedule-directory",
    "schedule/activities": "schedule-catalog",
    "journals": "journals",
    "previews/people": "preview",
}
_SUBJECTS = set(json.loads((Path(__file__).parent.parent / "services/schedule_subject_aliases.json").read_text()).values())
_PERIODS = {"current", "archived", "unassigned"}
_ACTIVITY_TYPES = {"lesson", "practice", "consultation", "club", "elective", "event", "lunch", "laboratory", "no_lesson"}


def _response(value: dict):
    return JSONResponse(jsonable_encoder(value), headers=PRIVATE)


def _status(status: int, detail: str):
    return HTTPException(status, detail, headers=PRIVATE)


def _pure_subject(value):
    return value if isinstance(value, str) and value in _SUBJECTS else "Предмет скрыт"


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _clock(value):
    return value if isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) else None


def _directory(database: Database, session_id: str, *, with_teachers: bool = True) -> dict:
    # Never query pupil rows, membership, account identifiers or comments.
    with database.connection() as connection:
        groups = database.execute(connection, """SELECT id, group_type, base_class_name, subject FROM groups
            WHERE canonical IS TRUE AND archived_at IS NULL ORDER BY group_type,id LIMIT 120""").fetchall()
        teachers = database.execute(connection, """SELECT id FROM identities
            WHERE kind='teacher' AND status='active' ORDER BY id LIMIT 100""").fetchall() if with_teachers else []
    group_rows = []
    for index, group in enumerate(groups, 1):
        match = re.fullmatch(r"(5|6|7|8|9|10|11)(?:\s*[-–—]?\s*([А-Яа-яA-Za-z]{1,2}))?(?:\s+класс)?",
                             str(group["base_class_name"] or ""))
        grade = match[1] if match else None
        kind = "class" if group["group_type"] == "class" else "instructional"
        label = (f"{grade}-{match[2].upper()}" if match and match[2] else f"{grade} класс") if grade and kind == "class" else f"Группа {index}"
        group_rows.append({"id": str(group["id"]), "group_type": kind, "name": label, "display_name": label,
                           "base_class_name": grade, "subject": None if kind == "class" else _pure_subject(group["subject"])})
    teacher_rows = [{"id": hmac.digest(session_id.encode(), str(row["id"]).encode(), "sha256").hex()[:24],
                     "kind": "teacher", "status": "active", "display_name": f"Преподаватель {index}"}
                    for index, row in enumerate(teachers, 1)]
    return {"groups": group_rows, "teachers": teacher_rows, "students": [], "rooms": [], "read_only": True, "write_ready": False}


def _schedule_drafts(database: Database) -> dict:
    rows = [row for row in list_experiments(database) if row["draft_kind"] in {"school", "user"} and not row["archived_at"]]
    return {"items": [{
        "id": str(row["id"]), "name": "Основной шаблон" if row["is_primary_template"] else f"Черновик {index}",
        "revision": row["revision"], "week_start": _date(row["week_start"]), "draft_kind": "school",
        "is_primary_template": row["is_primary_template"], "lesson_count": row["lesson_count"],
        "event_count": row["event_count"], "group_count": row["group_count"], "day_count": row["day_count"],
    } for index, row in enumerate(rows[:60], 1)]}


def _safe_draft(database: Database, session_id: str, draft_id: str) -> dict:
    try:
        item = get_experiment(database, str(UUID(draft_id)))
    except (ExperimentNotFound, ValueError):
        raise _status(404, "Черновик не найден.") from None
    if item["payload"].get("draft_archive") or item["payload"].get("draft_kind") not in {"school", "user"}:
        raise _status(404, "Черновик не найден.")
    classes = {row["id"] for row in _directory(database, session_id, with_teachers=False)["groups"] if row["group_type"] == "class"}
    source = item["payload"]
    # Opt-in field projection: notes, links, source strings, participants,
    # embedded canonical structures, and audit history never cross this boundary.
    slots = [{"start": _clock(row.get("start")) or "00:00", "end": _clock(row.get("end")) or "00:01"}
             for row in source.get("slots", [])[:30] if isinstance(row, dict)]
    class_ids = [str(value) for value in source.get("classes", []) if str(value) in classes]
    lessons = []
    for index, row in enumerate(source.get("lessons", [])[:600], 1):
        if not isinstance(row, dict) or type(row.get("day")) is not int or type(row.get("period")) is not int:
            continue
        if not (0 <= row["day"] <= 6 and 0 <= row["period"] < len(slots)):
            continue
        display_classes = list(dict.fromkeys(str(value) for value in
            (row.get("displayClassIds") or row.get("classIds") or [row.get("classId")]) if str(value) in classes))
        kind = row.get("activityType")
        lessons.append({"id": f"diagnostic-{index}", "day": row["day"], "period": row["period"],
                        "kind": "event" if row.get("kind") == "event" else "lesson",
                        "activityType": kind if isinstance(kind, str) and kind in _ACTIVITY_TYPES else "lesson",
                        "subject": _pure_subject(row.get("subject")), "classIds": display_classes,
                        "start": _clock(row.get("start")), "end": _clock(row.get("end")),
                        "needsClarification": row.get("needsClarification") is True})
    return {"id": str(item["id"]), "name": "Диагностический просмотр расписания", "revision": item["revision"],
            "payload": {"draft_kind": "school", "week_start": _date(source.get("week_start")),
                        "classes": class_ids, "days": [day for day in source.get("days", []) if type(day) is int and 0 <= day <= 6],
                        "slots": slots, "lessons": lessons}}


def _journals(database: Database, session_id: str) -> list[dict]:
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT id,grade,subject,academic_year,term_number,period_status,status,last_synced_at
            FROM journal_sources WHERE grade BETWEEN 5 AND 11 ORDER BY grade,id LIMIT 140""").fetchall()
    result = []
    for row in rows:
        grade, year = row["grade"], row["academic_year"] or ""
        if not re.fullmatch(r"[0-9]{4}/[0-9]{2}", str(year)):
            year = None
        identity = hmac.digest(session_id.encode(), str(row["id"]).encode(), "sha256").hex()[:24]
        subject = _pure_subject(row["subject"])
        result.append({"id": identity, "grade": grade, "academic_year": year, "term_number": row["term_number"],
                       "period_status": row["period_status"] if row["period_status"] in _PERIODS else "unassigned",
                       "status": row["status"] if row["status"] in {"active", "success", "error", "connected", "failed", "unknown", "partial", "ready"} else "unknown",
                       "last_synced_at": _date(row["last_synced_at"]), "last_error": "Подробнее только в личной сессии." if row["status"] in {"error", "failed"} else None, "subject": subject, "sheet_title": subject,
                       "spreadsheet_id": f"diagnostic-{grade}", "spreadsheet_title": f"Журналы {grade} класса"})
    return result


def create_codex_diagnostic_router(database: Database) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def require(header: str | None, view: str | None = None):
        if not auth.configured_epoch():
            raise _status(404, "Диагностический доступ выключен.")
        session_id = auth.authorize(database, auth.bearer_token(header), view=view)
        if not session_id:
            raise _status(401, "Диагностическая сессия недействительна или истекла.")
        return session_id

    @router.post("/exchange")
    def exchange(x_my_island_diagnostic_key: str | None = Header(default=None)):
        if not auth.configured_epoch():
            raise _status(404, "Диагностический доступ выключен.")
        try:
            result = auth.exchange(database, x_my_island_diagnostic_key)
        except auth.ExchangeThrottled:
            raise _status(429, "Слишком много попыток входа. Попробуйте позже.") from None
        if not result:
            raise _status(401, "Диагностический ключ не принят.")
        return _response(result)

    @router.get("/session")
    def session(authorization: str | None = Header(default=None)):
        require(authorization)
        return _response({"scope": auth.READ_SCOPE})

    @router.post("/logout")
    def logout(authorization: str | None = Header(default=None)):
        auth.revoke(database, require(authorization))
        return _response({"ok": True})

    @router.get("/view/{path:path}")
    def read(path: str, request: Request, authorization: str | None = Header(default=None)):
        view = _READ_VIEWS.get(path)
        draft = re.fullmatch(r"schedule/experiments/school/([a-fA-F0-9-]{36})", path)
        journal = re.fullmatch(r"journals/([a-f0-9]{24})/(detail|sync-history)", path)
        if not (view or draft or journal):
            require(authorization)
            raise _status(403, "Этот запрос недоступен без личной сессии администратора.")
        session_id = require(authorization, view or ("schedule-detail" if draft else "journal-detail"))
        if draft:
            return _response(_safe_draft(database, session_id, draft[1]))
        if journal:
            if journal[2] == "sync-history":
                return _response({"items": []})
            source = next((item for item in _journals(database, session_id) if item["id"] == journal[1]), None)
            if not source:
                raise _status(404, "Журнал не найден.")
            return _response({"source": source, "groups": [], "assessments": [], "students": []})
        if view == "overview":
            data = database.admin_overview()
            return _response({key: data[key] for key in ("canonical_students", "canonical_teachers", "groups")})
        if view == "system":
            data = database.system_health()
            return _response({"health": {"schema_status": data["schema_status"]}, "settings": {"database_configured": bool(database.database_url)}})
        if view == "integration":
            return _response({"status": "diagnostic_only", "message": "Живые подключения и учётные записи недоступны в диагностическом режиме.",
                              "reconnect_available": False})
        if view == "schedule":
            if path.endswith("capabilities"):
                return _response({"demo_available": False, "school_available": True, "consolidated_schedule": False})
            status = effective_status(database, _date(request.query_params.get("week_start")))
            return _response({"canonical": {"version_id": "available"} if status["canonical"] else None,
                              "effective_week": {"week_start": _date(status["effective_week"]["week_start"])} if status["effective_week"] else None})
        if view == "schedule-index":
            return _response({"blocks": [], "weeks": [], "load_error": "История публикаций и внешние источники скрыты."})
        if view == "schedule-drafts":
            return _response(_schedule_drafts(database))
        if view in {"directory", "groups", "schedule-directory"}:
            directory = _directory(database, session_id)
            if path == "groups":
                return _response({"items": directory["groups"]})
            if path == "people":
                if request.query_params.get("kind") != "teacher":
                    raise _status(403, "Имена, состав групп и данные учеников скрыты.")
                return _response({"items": directory["teachers"]})
            return _response(directory)
        if view == "journals":
            return _response({"items": _journals(database, session_id), "known_sources": [], "scheduler_configured": False})
        if view == "session":
            return _response({"scope": auth.READ_SCOPE})
        if view == "preview":
            return _response({"items": [], "classes": [], "groups": []})
        return _response({"items": []})  # No raw catalog entries.
    return router
