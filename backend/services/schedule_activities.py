"""Catalog for optional schedule activities (clubs, electives and events)."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping
from uuid import uuid4

from backend.database import Database


SEED_ACTIVITIES = [
    ("animation", "Анимация", "club", ["анимация", "мультипликация"]),
    ("aikido", "Айкидо", "club", ["айкидо"]),
    ("spanish", "Испанский язык", "club", ["испанский", "испанский язык"]),
    ("french", "Французский язык", "club", ["французский", "французский язык"]),
    ("game_design", "Геймдизайн", "club", ["game design", "геймдизайн", "game-design"]),
    ("chess", "Шахматы", "club", ["шахматы", "шахматный клуб", "японские шахматы", "сёги", "shogi"]),
    ("academic_drawing", "Академический рисунок", "club", ["академический рисунок", "рисунок"]),
    ("atelier", "Ателье", "club", ["ателье"]),
    ("vocal", "Вокал", "club", ["вокал", "вокальный клуб"]),
    ("programming", "Программирование", "club", ["программирование", "программирование на python"]),
]


class ActivityNotFound(Exception):
    pass


class ActivityConflict(Exception):
    def __init__(self, current: Mapping[str, Any] | None = None):
        super().__init__("Schedule activity was changed in another session")
        self.current = dict(current or {})


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for field in ("aliases", "teacher_ids", "eligible_grades"):
        try:
            result[field] = json.loads(result.get(field) or "[]")
        except (TypeError, json.JSONDecodeError):
            result[field] = []
    result["archived"] = bool(result.get("archived"))
    result["status"] = "archived" if result["archived"] else "active"
    return result


def _clean(payload: Mapping[str, Any]) -> dict[str, Any]:
    name = re.sub(r"^[^\wА-Яа-яЁё]+", "", str(payload.get("name") or "")).strip()
    kind = str(payload.get("type") or "club").strip()
    if not name:
        raise ValueError("name is required")
    if kind not in {"club", "elective", "event"}:
        raise ValueError("type must be club, elective or event")
    aliases = payload.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
        raise ValueError("aliases must be a list of strings")
    teacher_ids = payload.get("teacher_ids", payload.get("teacherIds", []))
    grades = payload.get("eligible_grades", payload.get("eligibleGrades", []))
    if not isinstance(teacher_ids, list) or not isinstance(grades, list):
        raise ValueError("teacher_ids and eligible_grades must be lists")
    if any(not isinstance(g, int) or isinstance(g, bool) or not 1 <= g <= 11 for g in grades):
        raise ValueError("eligible_grades must contain integers from 1 to 11")
    return {"name": name, "type": kind, "aliases": json.dumps(list(dict.fromkeys(a.strip() for a in aliases if a.strip())), ensure_ascii=False),
            "description": str(payload.get("description") or "").strip(),
            "teacher_ids": json.dumps(list(dict.fromkeys(str(v) for v in teacher_ids if str(v))), ensure_ascii=False),
            "room": str(payload.get("room") or "").strip(),
            "eligible_grades": json.dumps(sorted(set(int(v) for v in grades)), ensure_ascii=False)}


def _actor_id(database: Database, actor_user_id: Any) -> Any:
    if database.database_url or actor_user_id is None:
        return actor_user_id
    if isinstance(actor_user_id, int) and not isinstance(actor_user_id, bool):
        return actor_user_id
    return int(actor_user_id) if isinstance(actor_user_id, str) and actor_user_id.isdecimal() else None


def list_activities(database: Database, *, include_archived: bool = False) -> list[dict[str, Any]]:
    with database.connection() as connection:
        rows = database.execute(connection,
            "SELECT id,name,type,aliases,description,teacher_ids,room,eligible_grades,revision,archived,created_at,updated_at FROM schedule_activity_catalog "
            + ("" if include_archived else "WHERE archived=FALSE ") + "ORDER BY archived,name,id").fetchall()
    return [_decode(row) for row in rows]


def create_activity(database: Database, payload: Mapping[str, Any], *, actor_user_id: Any = None) -> dict[str, Any]:
    cleaned = _clean(payload)
    _assert_aliases_unique(database, cleaned)
    activity_id = str(uuid4())
    with database.connection() as connection:
        row = database.execute(connection, """INSERT INTO schedule_activity_catalog
            (id,name,type,aliases,description,teacher_ids,room,eligible_grades,revision,archived,created_by,updated_by)
            VALUES (?,?,?,?,?,?,?,?,1,FALSE,?,?) RETURNING id,name,type,aliases,description,teacher_ids,room,eligible_grades,revision,archived,created_at,updated_at""",
            (activity_id, cleaned["name"], cleaned["type"], cleaned["aliases"], cleaned["description"], cleaned["teacher_ids"], cleaned["room"], cleaned["eligible_grades"], _actor_id(database, actor_user_id), _actor_id(database, actor_user_id))).fetchone()
    return _decode(row)


def update_activity(database: Database, activity_id: str, *, expected_revision: int,
                    payload: Mapping[str, Any], actor_user_id: Any = None, archive: bool | None = None) -> dict[str, Any]:
    cleaned = _clean(payload)
    _assert_aliases_unique(database, cleaned, exclude_id=activity_id)
    if archive is not None:
        archived = archive == "archived" or archive is True
    elif "status" in payload:
        if payload["status"] not in {"active", "archived"}:
            raise ValueError("status must be active or archived")
        archived = payload["status"] == "archived"
    else:
        archived = bool(payload["archived"]) if "archived" in payload else None
    with database.connection() as connection:
        row = database.execute(connection, """UPDATE schedule_activity_catalog SET name=?,type=?,aliases=?,description=?,teacher_ids=?,room=?,eligible_grades=?,
            archived=COALESCE(?,archived),revision=revision+1,updated_by=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND revision=? RETURNING id,name,type,aliases,description,teacher_ids,room,eligible_grades,revision,archived,created_at,updated_at""",
            (cleaned["name"], cleaned["type"], cleaned["aliases"], cleaned["description"], cleaned["teacher_ids"], cleaned["room"], cleaned["eligible_grades"], archived, _actor_id(database, actor_user_id), activity_id, expected_revision)).fetchone()
        if row:
            return _decode(row)
        current = database.execute(connection, "SELECT id,name,type,aliases,description,teacher_ids,room,eligible_grades,revision,archived,created_at,updated_at FROM schedule_activity_catalog WHERE id=?", (activity_id,)).fetchone()
        if not current:
            raise ActivityNotFound(activity_id)
        raise ActivityConflict(_decode(current))


def save_activity(database: Database, payload: Mapping[str, Any], *, activity_id: str | None = None,
                  expected_revision: int | None = None, actor_user_id: Any = None) -> dict[str, Any]:
    """Create or revision-checked edit/archive, matching the admin route contract."""
    if activity_id is None:
        if expected_revision is not None:
            raise ValueError("expected_revision is only valid when updating an activity")
        return create_activity(database, payload, actor_user_id=actor_user_id)
    if expected_revision is None:
        raise ValueError("expected_revision is required when updating an activity")
    return update_activity(database, activity_id, expected_revision=expected_revision,
                           payload=payload, actor_user_id=actor_user_id,
                           archive=payload.get("archive") if "archive" in payload else None)


def _assert_aliases_unique(database: Database, cleaned: Mapping[str, Any], *, exclude_id: str | None = None) -> None:
    names = {normalize_activity_name(cleaned["name"])}
    names.update(normalize_activity_name(value) for value in json.loads(cleaned["aliases"]))
    for item in list_activities(database, include_archived=True):
        if str(item["id"]) == exclude_id:
            continue
        existing = {normalize_activity_name(item["name"])}
        existing.update(normalize_activity_name(value) for value in item.get("aliases", []))
        if names & existing:
            raise ValueError(f"Activity name or alias already belongs to {item['name']}")


def seed_activities(database: Database) -> None:
    """Idempotently seed the initial catalog; existing administrator edits win."""
    with database.connection() as connection:
        for activity_id, name, kind, aliases in SEED_ACTIVITIES:
            database.execute(connection, """INSERT INTO schedule_activity_catalog(id,name,type,aliases)
                VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (activity_id, name, kind, json.dumps(aliases, ensure_ascii=False)))


def normalize_activity_name(value: str) -> str:
    text = " ".join(re.sub(r"^[^\wА-Яа-яЁё]+", "", str(value or "")).casefold().replace("ё", "е").split())
    if text in {"геймдизайн", "game-design"}:
        return "game design"
    if text in {"японские шахматы", "сёги", "shogi"}:
        return "chess"
    return text


def validate_activity_lessons(lessons: list[Any], catalog: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve optional activity metadata and freeze historical display names."""
    by_id = {str(item["id"]): item for item in catalog}
    aliases = {normalize_activity_name(a): item for item in catalog for a in [item["name"], *item.get("aliases", [])]}
    result = []
    for source in lessons:
        if not isinstance(source, Mapping):
            raise ValueError("Each lesson must be an object")
        lesson = dict(source)
        activity_type = lesson.get("activityType")
        # Resolve only the explicit legacy optional marker plus a confirmed
        # catalog alias, when importing/saving a detached draft. A subject word
        # on its own (e.g. programming) never establishes club participation.
        legacy_subject = str(lesson.get("subject") or "")
        legacy_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", legacy_subject).strip()
        legacy_item = aliases.get(normalize_activity_name(legacy_name))
        if activity_type is None and legacy_subject.lstrip().startswith("⚪") and legacy_item and legacy_item.get("type") == "club":
            display = str(legacy_item["name"])
            if legacy_name.casefold() in {"японские шахматы", "сёги", "shogi", "game design"}:
                display = {"японские шахматы": "Японские шахматы", "сёги": "Сёги", "shogi": "Shogi", "game design": "Game Design"}[legacy_name.casefold()]
            lesson.update(activityType="club", activityCatalogId=str(legacy_item["id"]), activityNameSnapshot=display,
                          legacySubject=legacy_subject, subject=display,
                          displayClassIds=lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else []))
            activity_type = "club"
        if activity_type is not None and activity_type not in {"lesson", "club", "elective", "event"}:
            raise ValueError("activityType must be lesson, club, elective or event")
        if activity_type in {"club", "elective"}:
            cid = str(lesson.get("activityCatalogId") or "")
            explicitly_detached = "activityCatalogId" in lesson and not cid
            item = by_id.get(cid) if cid else None if explicitly_detached else aliases.get(normalize_activity_name(lesson.get("activityNameSnapshot") or lesson.get("subject") or ""))
            if not cid and item and item.get("type") != activity_type:
                item = None
            if cid and not item:
                raise ValueError(f"Unknown activity catalog id: {cid}")
            if item:
                if item.get("type") != activity_type:
                    raise ValueError("activityType must match the catalog activity type")
                lesson["activityCatalogId"] = str(item["id"])
                if not str(lesson.get("activityNameSnapshot") or "").strip():
                    lesson["activityNameSnapshot"] = str(item["name"])
            if not str(lesson.get("activityNameSnapshot") or "").strip():
                # Older constructor drafts may have the real title in subject
                # but predate the frozen optional-activity display snapshot.
                # Preserve that authored name without inventing a catalog entry.
                subject_name = str(lesson.get("subject") or "").strip()
                if subject_name:
                    lesson["activityNameSnapshot"] = subject_name
            if not str(lesson.get("activityNameSnapshot") or "").strip():
                raise ValueError("Optional activities require activityNameSnapshot")
            if "eligibleGrades" in lesson:
                grades = lesson["eligibleGrades"]
                if not isinstance(grades, list) or any(not isinstance(g, int) or isinstance(g, bool) or g < 1 or g > 11 for g in grades):
                    raise ValueError("eligibleGrades must contain grade numbers from 1 to 11")
                lesson["eligibleGrades"] = sorted(set(grades))
            if "electiveDirection" in lesson and lesson["electiveDirection"] != "digital":
                raise ValueError("electiveDirection must be digital when provided")
            if "confirmedParticipantIds" in lesson and not isinstance(lesson["confirmedParticipantIds"], list):
                raise ValueError("confirmedParticipantIds must be a list")
            if "confirmedParticipantIds" in lesson:
                lesson["confirmedParticipantIds"] = sorted({str(value) for value in lesson["confirmedParticipantIds"] if str(value)})
        result.append(lesson)
    return result
