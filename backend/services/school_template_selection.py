"""Explicit school-editor template selection, independent of canonical history."""
import hashlib
import json
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4
from typing import Any

from backend.database import Database
from backend.services.schedule_experiments import _item, create_experiment, get_experiment

_selection_lock = Lock()


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()


def _lesson_summary(lesson: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": lesson.get("subject") or lesson.get("activity") or "",
        "day": lesson.get("day", lesson.get("weekday")),
        "start": lesson.get("start") or lesson.get("start_time") or "",
        "end": lesson.get("end") or lesson.get("end_time") or "",
        "groups": sorted(set(map(str, lesson.get("groupIds") or lesson.get("classIds") or ([lesson.get("groupId")] if lesson.get("groupId") else [])))),
        "members": sorted(set(map(str, lesson.get("studentIds") or (lesson.get("audience_snapshot") or {}).get("student_ids") or []))),
        "teachers": sorted(set(map(str, lesson.get("teacherIds") or ([lesson.get("teacherId")] if lesson.get("teacherId") else [])))),
        "room": str(lesson.get("room") or ""),
        "problems": sorted(str(v) for v in (lesson.get("clarificationReason"),) if v) + (["importError"] if lesson.get("importError") else []) + (["needsClarification"] if lesson.get("needsClarification") else []),
    }


def _conflicts(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def minutes(value: str) -> int | None:
        try:
            hour, minute = map(int, value.split(":")[:2])
            return hour * 60 + minute
        except (TypeError, ValueError):
            return None
    issues = []
    rows = [(item, _lesson_summary(item)) for item in lessons]
    for index, (first, a) in enumerate(rows):
        if a["day"] is None or first.get("informational") or first.get("specialMark") in {"lunch", "no_lesson"}:
            continue
        start_a, end_a = minutes(a["start"]), minutes(a["end"])
        if start_a is None or end_a is None or end_a <= start_a:
            continue
        for second, b in rows[index + 1:]:
            if (b["day"] is None or a["day"] != b["day"] or second.get("informational")
                    or second.get("specialMark") in {"lunch", "no_lesson"}):
                continue
            start_b, end_b = minutes(b["start"]), minutes(b["end"])
            if start_b is None or end_b is None or end_b <= start_b or max(start_a, start_b) >= min(end_a, end_b):
                continue
            shared_groups = sorted(set(a["groups"]) & set(b["groups"]))
            shared_teachers = sorted(set(a["teachers"]) & set(b["teachers"]))
            if shared_groups or shared_teachers:
                issues.append({"lesson_id": str(first.get("id") or ""), "details": [
                    *( ["Конфликт группы: " + ", ".join(shared_groups)] if shared_groups else []),
                    *( ["Конфликт преподавателя: " + ", ".join(shared_teachers)] if shared_teachers else []),
                ]})
    return issues


def preview_school_template_selection(database: Database, draft_id: str) -> dict[str, Any]:
    candidate = get_experiment(database, draft_id)
    payload = candidate["payload"]
    if payload.get("draft_kind") != "school" or payload.get("week_start") or payload.get("source_template"):
        raise ValueError("Выбрать основным можно только сохранённый шаблон школы")
    try:
        current = open_active_school_template(database)
    except ValueError as error:
        if "не назначен" not in str(error):
            raise
        current = None
    before = {str(item.get("id")): item for item in (current or {}).get("payload", {}).get("lessons", []) if isinstance(item, dict)}
    after = {str(item.get("id")): item for item in payload.get("lessons", []) if isinstance(item, dict)}
    added = [after[key] for key in sorted(after.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - after.keys())]
    changed = [{"before": before[key], "after": after[key]} for key in sorted(before.keys() & after.keys())
               if _lesson_summary(before[key]) != _lesson_summary(after[key])]
    problems = [{"lesson_id": str(item.get("id") or ""), "details": _lesson_summary(item)["problems"]}
                for item in after.values() if _lesson_summary(item)["problems"]]
    problems.extend(_conflicts(list(after.values())))
    return {"current": {"id": current["id"], "name": current["name"], "revision": current["revision"]} if current else None,
            "candidate": {"id": candidate["id"], "name": candidate["name"], "revision": candidate["revision"]},
            "current_hash": _digest(current["payload"]) if current else None,
            "candidate_hash": _digest(payload),
            "added": [{"id": str(item.get("id") or ""), **_lesson_summary(item)} for item in added],
            "removed": [{"id": str(item.get("id") or ""), **_lesson_summary(item)} for item in removed],
            "changed": [{"id": str(item["after"].get("id") or ""), "before": _lesson_summary(item["before"]), "after": _lesson_summary(item["after"])} for item in changed],
            "problems": problems,
            "requires_explicit_problem_acknowledgement": bool(problems)}


def select_school_template(database: Database, draft_id: str, *, expected_revision: int,
                           expected_current_id: str | None, expected_current_revision: int | None,
                           expected_candidate_hash: str, actor_user_id: Any = None) -> dict[str, Any]:
    with _selection_lock:
        if database.database_url:
            with database.connection() as connection:
                database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                                 ("my-island:school-template-selection",))
                return _select_school_template_locked(database, draft_id,
                    expected_revision=expected_revision, expected_current_id=expected_current_id,
                    expected_current_revision=expected_current_revision,
                    expected_candidate_hash=expected_candidate_hash, actor_user_id=actor_user_id)
        return _select_school_template_locked(database, draft_id, expected_revision=expected_revision,
            expected_current_id=expected_current_id, expected_current_revision=expected_current_revision,
            expected_candidate_hash=expected_candidate_hash, actor_user_id=actor_user_id)


def _select_school_template_locked(database: Database, draft_id: str, *, expected_revision: int,
                           expected_current_id: str | None, expected_current_revision: int | None,
                           expected_candidate_hash: str, actor_user_id: Any = None) -> dict[str, Any]:
    preview = preview_school_template_selection(database, draft_id)
    candidate = get_experiment(database, draft_id)
    if int(candidate["revision"]) != expected_revision or preview["candidate_hash"] != expected_candidate_hash:
        raise ValueError("Шаблон изменился после сравнения. Обновите различия перед переключением.")
    if preview["current"] and (preview["current"]["id"] != expected_current_id or int(preview["current"]["revision"]) != int(expected_current_revision or 0)):
        raise ValueError("Основной шаблон уже изменился. Обновите сравнение и повторите подтверждение.")
    now = datetime.now(timezone.utc).isoformat()
    history_id = str(uuid4())
    candidate_payload = candidate["payload"]
    previous = get_experiment(database, expected_current_id) if expected_current_id else None
    json_parameter = "?::jsonb" if database.database_url else "?"
    with database.connection() as connection:
        locked = database.execute(connection,
            "SELECT draft_id FROM school_template_selection WHERE scope='school'" + (" FOR UPDATE" if database.database_url else "")).fetchone()
        locked_id = str(locked["draft_id"]) if locked else None
        if locked_id != expected_current_id:
            raise ValueError("Основной шаблон уже изменился. Обновите сравнение и повторите подтверждение.")
        suffix = " FOR UPDATE" if database.database_url else ""
        locked_candidate_row = database.execute(connection,
            "SELECT * FROM schedule_experiment_drafts WHERE id=?" + suffix, (draft_id,)).fetchone()
        if not locked_candidate_row:
            raise ValueError("Сохранённый шаблон больше не существует")
        locked_candidate = _item(locked_candidate_row)
        if (int(locked_candidate["revision"]) != expected_revision
                or _digest(locked_candidate["payload"]) != expected_candidate_hash):
            raise ValueError("Шаблон изменился после сравнения. Обновите различия перед переключением.")
        if expected_current_id:
            previous_row = database.execute(connection,
                "SELECT * FROM schedule_experiment_drafts WHERE id=?" + suffix, (expected_current_id,)).fetchone()
            previous = _item(previous_row) if previous_row else None
            if (not previous or int(previous["revision"]) != int(expected_current_revision or 0)
                    or _digest(previous["payload"]) != preview["current_hash"]):
                raise ValueError("Текущий шаблон изменился после сравнения. Обновите различия перед переключением.")
        history_sql = ("INSERT INTO school_template_selection_history "
            "(id,previous_draft_id,previous_revision,previous_payload,previous_payload_sha256,next_draft_id,next_revision,next_payload,next_payload_sha256,actor_user_id,created_at) "
            f"VALUES (?,?,?,{json_parameter},?,?,{json_parameter},?,?,?,?)")
        database.execute(connection, history_sql, (history_id, previous["id"] if previous else None,
            previous["revision"] if previous else None, _encoded(previous["payload"]) if previous else None,
            _digest(previous["payload"]) if previous else None, candidate["id"], candidate["revision"],
            _encoded(candidate_payload), preview["candidate_hash"], str(actor_user_id) if actor_user_id is not None else None, now))
        database.execute(connection, f"""INSERT INTO school_template_selection
            (scope,draft_id,source_draft_id,source_revision,source_payload_sha256,activated_at)
            VALUES ('school',?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
            draft_id=excluded.draft_id,source_draft_id=excluded.source_draft_id,
            source_revision=excluded.source_revision,source_payload_sha256=excluded.source_payload_sha256,
            activated_at=excluded.activated_at""", (candidate["id"], candidate["id"], candidate["revision"], preview["candidate_hash"], now))
    return {"selected": {"id": candidate["id"], "revision": candidate["revision"]}, "history_id": history_id}


def rollback_school_template(database: Database, *, history_id: str, actor_user_id: Any = None) -> dict[str, Any]:
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM school_template_selection_history WHERE id=?", (history_id,)).fetchone()
    if not row or not row["previous_payload"]:
        raise ValueError("Для этой записи нет предыдущей версии шаблона")
    payload = row["previous_payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if _digest(payload) != row["previous_payload_sha256"]:
        raise ValueError("Снимок предыдущего шаблона повреждён; возврат заблокирован")
    restored = create_experiment(database, name=f"Восстановленный шаблон · {row['previous_draft_id']}",
        source="import", payload=payload, actor_user_id=actor_user_id)
    try:
        current = open_active_school_template(database)
    except ValueError:
        current = None
    result = select_school_template(database, restored["id"], expected_revision=restored["revision"],
        expected_current_id=current["id"] if current else None,
        expected_current_revision=current["revision"] if current else None,
        expected_candidate_hash=_digest(restored["payload"]),
        actor_user_id=actor_user_id)
    return {"draft": restored, **result}


def open_active_school_template(database: Database) -> dict[str, Any]:
    # One statement observes the pointer and its draft in the same DB snapshot.
    # Opening never imports, normalizes, creates, or rewrites a template.
    with database.connection() as connection:
        row = database.execute(connection, """SELECT d.*
            FROM school_template_selection s
            JOIN schedule_experiment_drafts d ON d.id=s.draft_id
            WHERE s.scope='school'""").fetchone()
    if not row:
        raise ValueError("Основной школьный шаблон не назначен. Нужен проверенный редакторский черновик; canonical-копия автоматически не создаётся.")
    draft = _item(row)
    if (draft["payload"].get("draft_kind") != "school"
            or draft["payload"].get("week_start") or draft["payload"].get("source_template")):
        raise ValueError("Активный школьный шаблон указывает на неподходящий черновик")
    draft["already_exists"] = True
    return draft
