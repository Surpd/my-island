from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from typing import Any
import re
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from threading import Lock

from backend.database import Database
from backend.services.schedule_rooms import display_room
from backend.services.canonical_schedule import _assignment_metadata, canonical_group_grade, canonical_version, read_canonical_template
from backend.services.schedule_activities import list_activities, validate_activity_lessons
from backend.services.schedule_edit_validation import (
    CLUB_LESSON_ONLY_NOTES, GROUP_LINK_NOTES, TEACHER_ASSIGNMENT_NOTE, TEACHER_IDENTITY_NOTE,
    audit_resolved_notes, normalized_note, notes, read_active_scope_directory,
    scope_verified, selected_scope, subject_key,
)


DEFAULT_SLOTS = [
    {"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"},
    {"start": "10:55", "end": "11:40"}, {"start": "11:50", "end": "12:35"},
    {"start": "12:45", "end": "13:30"}, {"start": "13:40", "end": "14:25"},
    {"start": "14:35", "end": "15:20"}, {"start": "15:30", "end": "16:15"},
]

_school_template_open_lock = Lock()
_week_creation_lock = Lock()


def _canonical_time(value: Any) -> str:
    """Postgres TIME and SQLite text must address the same bell slot."""
    text = str(value or "")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::00)?", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text


def _clean_grade_modes(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        if value in (None, {}):
            return {}
        raise ValueError("grade_modes must map canonical grade numbers to weekdays")
    result: dict[str, dict[str, str]] = {}
    for raw_grade, modes in value.items():
        grade_match = re.fullmatch(r"\s*(\d{1,2})\s*", str(raw_grade))
        if not grade_match:
            raise ValueError("grade_modes keys must be canonical grade numbers 5..11")
        grade = str(int(grade_match.group(1)))
        if int(grade) not in range(5, 12) or not isinstance(modes, Mapping):
            raise ValueError("grade_modes support grades 5..11 and weekday maps")
        clean: dict[str, str] = {}
        for raw_day, raw_mode in modes.items():
            day = int(raw_day)
            mode = str(raw_mode or "normal").strip().lower()
            if day < 0 or day > 6 or mode not in {"normal", "online", "sdep"}:
                raise ValueError("grade_modes weekdays must be 0..6 and modes normal, online or sdep")
            if mode == "sdep" and int(grade) not in {9, 10, 11}:
                raise ValueError("SDEP is available only for grades 9–11")
            if mode != "normal":
                clean[str(day)] = mode
        if clean:
            result[grade] = clean
    return result


def _class_grade_map(database: Database, payload: Mapping[str, Any]) -> dict[str, str]:
    class_ids = sorted({str(value) for value in payload.get("classes") or []})
    if not class_ids:
        return {}
    placeholders = ",".join("?" for _ in class_ids)
    with database.connection() as connection:
        rows = database.execute(connection,
            f"SELECT id,name,display_name,base_class_name,group_type,role FROM groups WHERE id IN ({placeholders})",
            tuple(class_ids)).fetchall()
    return {str(row["id"]): canonical_group_grade(dict(row)) for row in rows}


def resolve_payload_day_mode(payload: Mapping[str, Any], grade: str, weekday: int,
                             class_grades: Mapping[str, str]) -> tuple[str | None, list[str]]:
    """Resolve grade mode; return a null mode and conflicting IDs instead of guessing."""
    grade = str(grade or "")
    day = str(weekday)
    explicit = (payload.get("grade_modes") or {}).get(grade) or {}
    if day in explicit:
        return str(explicit[day]), []
    legacy = payload.get("day_modes") or {}
    class_ids = sorted(class_id for class_id, class_grade in class_grades.items() if class_grade == grade)
    values = {str((legacy.get(class_id) or {}).get(day) or "normal") for class_id in class_ids}
    if len(values) > 1:
        return None, class_ids
    return (next(iter(values)) if values else "normal"), []


def payload_day_mode_conflicts(database: Database, payload: Mapping[str, Any], *,
                               groups: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    class_grades = (_class_grade_map(database, payload) if groups is None else
                    {class_id: canonical_group_grade(groups[class_id])
                     for class_id in map(str, payload.get("classes") or []) if class_id in groups})
    grades = sorted({grade for grade in class_grades.values() if grade}, key=int)
    conflicts = []
    for grade in grades:
        for weekday in range(7):
            _, class_ids = resolve_payload_day_mode(payload, grade, weekday, class_grades)
            if class_ids:
                conflicts.append({"grade": grade, "weekday": weekday, "class_ids": class_ids})
    return conflicts


def _canonical_provenance(value: Any) -> Any:
    """Normalize native SQL values only at the canonical-to-draft boundary.

    Display times are already normalized above; the complete nested source
    block also contains Postgres TIME, timestamps and UUID identifiers.
    Unknown types remain errors instead of being silently stringified.
    """
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_provenance(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_provenance(item) for item in value]
    return value


class ExperimentNotFound(Exception):
    pass


class ExperimentConflict(Exception):
    def __init__(self, current: Mapping[str, Any] | None) -> None:
        super().__init__("Experimental schedule was changed in another session")
        self.current = dict(current or {})


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _item(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["payload"] = _payload(value.get("payload"))
    return value


def _clean_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    days = sorted({int(day) for day in raw.get("days", [0, 1, 2, 3, 4])})
    if any(day < 0 or day > 6 for day in days):
        raise ValueError("days must contain weekday numbers from 0 to 6")
    slots = []
    for slot in raw.get("slots", DEFAULT_SLOTS):
        if not isinstance(slot, Mapping) or not slot.get("start") or not slot.get("end"):
            raise ValueError("Each slot requires start and end")
        slots.append({"start": str(slot["start"]), "end": str(slot["end"])})
    classes = sorted({str(item) for item in raw.get("classes", []) if str(item)})
    lessons = raw.get("lessons", [])
    if not isinstance(lessons, list) or any(not isinstance(lesson, Mapping) for lesson in lessons):
        raise ValueError("lessons must be a list of objects")
    clean_lessons = []
    for item in lessons:
        lesson = dict(item)
        # Activity details live in the existing experimental schedule payload.
        for field in ("activityType", "activityCatalogId", "activityNameSnapshot", "electiveDirection",
                      "eligibleGrades", "confirmedParticipantIds"):
            if field in item:
                lesson[field] = item[field]
        snapshot = lesson.get("audience_snapshot")
        if isinstance(snapshot, Mapping):
            lesson["audience_snapshot"] = {"student_ids": _ids(snapshot.get("student_ids")),
                                           "fingerprint": str(snapshot.get("fingerprint") or ""),
                                           "resolved_at": str(snapshot.get("resolved_at") or "")}
        clean_lessons.append(lesson)
    result = {"days": days, "slots": slots, "classes": classes, "lessons": clean_lessons}
    raw_day_modes = raw.get("day_modes") or {}
    if not isinstance(raw_day_modes, Mapping):
        raise ValueError("day_modes must map administrative class ids to weekdays")
    day_modes: dict[str, dict[str, str]] = {}
    for class_id, modes in raw_day_modes.items():
        if not isinstance(modes, Mapping):
            raise ValueError("Each day_modes class entry must map weekdays to modes")
        clean_modes: dict[str, str] = {}
        for raw_day, raw_mode in modes.items():
            day = int(raw_day)
            mode = str(raw_mode or "normal").strip().lower()
            if day < 0 or day > 6 or mode not in {"normal", "online", "sdep"}:
                raise ValueError("day_modes weekdays must be 0..6 and modes normal, online or sdep")
            if mode != "normal":
                clean_modes[str(day)] = mode
        if clean_modes:
            day_modes[str(class_id)] = clean_modes
    if day_modes:
        result["day_modes"] = day_modes
    grade_modes = _clean_grade_modes(raw.get("grade_modes"))
    if grade_modes:
        result["grade_modes"] = grade_modes
    if raw.get("reference_date"):
        result["reference_date"] = date.fromisoformat(str(raw["reference_date"])).isoformat()
    if raw.get("week_start"):
        result["week_start"] = date.fromisoformat(str(raw["week_start"])).isoformat()
    widths = raw.get("column_widths") or {}
    if isinstance(widths, Mapping):
        result["column_widths"] = {str(key): max(90, min(600, int(width))) for key, width in widths.items() if str(key)}
    if raw.get("column_width_mode") in {"auto", "manual"}:
        result["column_width_mode"] = raw["column_width_mode"]
    if raw.get("draft_kind") in {"demo", "test", "user", "school"}:
        result["draft_kind"] = raw["draft_kind"]
    if raw.get("fixture_version"):
        result["fixture_version"] = str(raw["fixture_version"])
    # The school import is reviewable only while its source and unresolved
    # records survive normal edits, renames, and reloads of the draft.
    for key in ("school_source", "import_review", "transfer_result", "laboratory_blueprints", "laboratory_meetings", "laboratory_history"):
        if isinstance(raw.get(key), (dict, list)):
            result[key] = raw[key]
    blueprints = raw.get("laboratory_blueprints", [])
    if not isinstance(blueprints, list) or any(not isinstance(b, Mapping) or not isinstance(b.get("id"), str)
                                               or not b["id"] or not isinstance(b.get("subject"), str)
                                               or not b["subject"].strip() for b in blueprints):
        raise ValueError("Для заготовки лаборатории нужны название и идентификатор")
    if len({b["id"] for b in blueprints}) != len(blueprints):
        raise ValueError("Идентификаторы заготовок должны быть уникальны")
    meetings = raw.get("laboratory_meetings", {})
    if not isinstance(meetings, Mapping) or meetings and not result.get("week_start"):
        raise ValueError("Лабораторию можно активировать только в недельном черновике")
    active_ids = {m.get("meeting_id") for m in meetings.values() if isinstance(m, Mapping)}
    if any(l.get("activityType") == "laboratory" and l.get("id") not in active_ids for l in lessons):
        raise ValueError("Добавьте лабораторию через предпросмотр замещения")
    if isinstance(raw.get("source_template"), Mapping):
        result["source_template"] = dict(raw["source_template"])
    return result


def _validate_day_modes(database: Database, payload: Mapping[str, Any]) -> None:
    _clean_grade_modes(payload.get("grade_modes"))
    day_modes = payload.get("day_modes") or {}
    if not day_modes:
        return
    class_ids = set(str(value) for value in payload.get("classes") or [])
    if set(day_modes) - class_ids:
        raise ValueError("Day modes may only target administrative classes in this template")
    sdep_classes = {class_id for class_id, modes in day_modes.items()
                    if any(mode == "sdep" for mode in modes.values())}
    if not sdep_classes:
        return
    placeholders = ",".join("?" for _ in sdep_classes)
    with database.connection() as connection:
        rows = database.execute(connection,
            f"SELECT id,name,display_name,base_class_name,group_type,role FROM groups WHERE id IN ({placeholders})",
            tuple(sorted(sdep_classes))).fetchall()
    found = {str(row["id"]): row for row in rows}
    for class_id in sdep_classes:
        group = found.get(class_id)
        grade = canonical_group_grade(dict(group)) if group else ""
        if not group or not grade or int(grade) not in {9, 10, 11}:
            raise ValueError("SDEP is available only for administrative classes 9–11")


def _ids(value: Any) -> list[str]:
    return sorted({str(item) for item in value if str(item)}) if isinstance(value, list) else []


def _audience_fingerprint(lesson: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    slots = payload.get("slots") or DEFAULT_SLOTS
    rule = _lesson_audience(lesson)
    interval = _lesson_interval(lesson, slots) if isinstance(slots, list) else None
    source = {"rule": rule, "day": lesson.get("day", lesson.get("weekday")),
              "interval": interval, "reference_date": payload.get("reference_date") or payload.get("week_start") or ""}
    canonical = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _lesson_special_mark(lesson: Mapping[str, Any]) -> str:
    canonical = lesson.get("canonical") if isinstance(lesson.get("canonical"), Mapping) else {}
    assignment = canonical.get("assignment") if isinstance(canonical.get("assignment"), Mapping) else {}
    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
    return str(lesson.get("specialMark") or metadata.get("special_mark") or "")


def _is_lunch_lesson(lesson: Mapping[str, Any]) -> bool:
    mark = _lesson_special_mark(lesson)
    if mark == "lunch" or str(lesson.get("activityType") or "").casefold() == "lunch":
        return True
    canonical = lesson.get("canonical") if isinstance(lesson.get("canonical"), Mapping) else {}
    assignment = canonical.get("assignment") if isinstance(canonical.get("assignment"), Mapping) else {}
    role = str(lesson.get("canonicalRole") or assignment.get("assignment_kind") or assignment.get("role") or "").casefold()
    label = str(lesson.get("activity") or lesson.get("subject") or "").strip().casefold()
    # Historical canonical exports sometimes kept the lunch label and role
    # without copying special_mark into constructor fields.
    return role in {"lunch", "special_lunch"} or role == "residual" and label in {"обед", "lunch"}


def _lesson_activity_type(lesson: Mapping[str, Any]) -> str:
    if lesson.get("activityType"):
        return str(lesson["activityType"])
    canonical = lesson.get("canonical")
    assignment = canonical.get("assignment") if isinstance(canonical, Mapping) else None
    origin_metadata = _assignment_metadata(assignment.get("metadata")) if isinstance(assignment, Mapping) else {}
    origin_type = origin_metadata.get("activity_type")
    if origin_type and origin_type != "lesson":
        return str(origin_type)
    details = origin_metadata.get("constructor_details")
    details = details if isinstance(details, Mapping) else {}
    block_id = lesson.get("blockId", details.get("blockId"))
    block_name = lesson.get("blockName", details.get("blockName"))
    if block_id and str(block_name or "").strip().casefold() in {"курс по выбору", "курсы по выбору", "цифровой трек"}:
        return "elective"
    return "event" if lesson.get("kind") == "event" else "lesson"


def _lesson_audience(lesson: Mapping[str, Any]) -> dict[str, Any]:
    """Read both the current ID rule and persisted pre-rule constructor lessons."""
    # The direction itself never has an inferred student roster. An elective
    # can still occupy the slot through its shared class card.
    if _lesson_activity_type(lesson) in {"club", "elective"}:
        return {"kind": "students", "group_ids": [], "class_ids": [], "grade": "", "student_ids": []}
    mark = _lesson_special_mark(lesson)
    if mark == "no_lesson" or lesson.get("kind") == "event" and lesson.get("informational") is True and not isinstance(lesson.get("audience"), Mapping):
        return {"kind": "", "group_ids": [], "class_ids": [], "grade": "", "student_ids": []}
    if (_is_lunch_lesson(lesson) and lesson.get("needsClarification") is True
            and str(lesson.get("clarificationReason") or "").strip() == "Уточните охват обеда: класс или отдельная группа"):
        # Historical lunch rows with an advisory-only class column never
        # asserted which students were actually assigned. Keep them as
        # informational markers until an explicit audience rule is authored.
        return {"kind": "", "group_ids": [], "class_ids": [], "grade": "", "student_ids": []}
    raw = lesson.get("audience")
    if isinstance(raw, Mapping):
        kind = str(raw.get("kind") or "")
        if kind == "remaining":
            kind = "available_slot"
        display_class_ids = _ids(lesson.get("classIds")) or _ids([lesson.get("classId")] if lesson.get("classId") else [])
        result = {
            "kind": kind,
            "group_ids": _ids(raw.get("group_ids") or raw.get("groupIds")),
            "class_ids": _ids(raw.get("class_ids") or raw.get("classIds")) or ([] if raw.get("grade") else display_class_ids),
            # The constructor's grid column is authoritative when an audience
            # rule was saved without an explicit grade. Preserve that scope
            # instead of requiring the user to pick one administrative class.
            "grade": str(raw.get("grade") or lesson.get("grade_scope") or ""),
            "student_ids": _ids(raw.get("student_ids") or raw.get("studentIds") or raw.get("include_student_ids")),
        }
        for field in ("include_student_ids", "exclude_student_ids", "partition_group_ids"):
            if raw.get(field):
                result[field] = _ids(raw[field])
        if raw.get("partition_dimension"):
            result["partition_dimension"] = str(raw["partition_dimension"])
        return result
    group_ids = _ids(lesson.get("groupIds")) or _ids([lesson.get("groupId")] if lesson.get("groupId") else [])
    class_ids = _ids(lesson.get("classIds")) or _ids([lesson.get("classId")] if lesson.get("classId") else [])
    canonical = lesson.get("canonical") if isinstance(lesson.get("canonical"), Mapping) else {}
    assignment = canonical.get("assignment") if isinstance(canonical.get("assignment"), Mapping) else {}
    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
    source_rule = metadata.get("audience_rule") if isinstance(metadata.get("audience_rule"), Mapping) else {}
    source_kind = str(source_rule.get("kind") or "")
    source_class_id = str(source_rule.get("class_group_id") or "")
    source_grade = str(source_rule.get("grade") or lesson.get("grade_scope") or "")
    source_class_ids = [source_class_id] if source_class_id else ([] if source_grade else class_ids)
    if lesson.get("audienceMode") == "remainder" or source_rule.get("kind") == "remaining":
        return {"kind": "legacy_remaining", "group_ids": [],
                "class_ids": source_class_ids,
                "grade": source_grade,
                "student_ids": [],
                "partition_group_ids": _ids(source_rule.get("partition_group_ids") or lesson.get("excludedGroupIds")),
                "partition_dimension": str(source_rule.get("partition_dimension") or "")}
    if source_kind in {"whole_grade", "available_slot"}:
        return {"kind": "classes" if source_kind == "whole_grade" else "available_slot",
                "group_ids": [], "class_ids": source_class_ids, "grade": source_grade, "student_ids": [],
                "include_student_ids": _ids(source_rule.get("include_student_ids")),
                "exclude_student_ids": _ids(source_rule.get("exclude_student_ids"))}
    if source_kind == "students":
        return {"kind": "students", "group_ids": [], "class_ids": [], "grade": "",
                "student_ids": _ids(source_rule.get("include_student_ids"))}
    if group_ids:
        # Canonical copies can contain both a class and an instructional group.
        # The old JSON had no separate audience rule, so retain that union.
        return {"kind": "groups", "group_ids": group_ids, "class_ids": [], "grade": "", "student_ids": [], "legacy": True,
                "include_student_ids": _ids(source_rule.get("include_student_ids")),
                "exclude_student_ids": _ids(source_rule.get("exclude_student_ids"))}
    if class_ids:
        return {"kind": "classes", "group_ids": [], "class_ids": class_ids, "grade": "", "student_ids": []}
    # Preserve the constructor's visible class scope for unresolved lessons.
    # It lets the preview decide whether an unknown assignment can overlap a
    # remainder without pretending that the assignment has zero students.
    return {"kind": "", "group_ids": [], "class_ids": class_ids,
            "grade": source_grade, "student_ids": []}


def _time_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = map(int, match.groups())
    return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None


def _lesson_day_modes(payload: Mapping[str, Any], lesson: Mapping[str, Any], class_grades: Mapping[str, str]) -> set[str]:
    rule = _lesson_audience(lesson)
    class_ids = {str(value) for value in (lesson.get("classIds") or []) if value}
    class_ids.update(str(value) for value in (rule.get("class_ids") or rule.get("group_ids") or []) if value)
    class_ids.update(str(value) for value in (lesson.get("groupIds") or []) if value)
    class_ids.update(str(value) for value in (lesson.get("classId"), lesson.get("groupId")) if value)
    try:
        weekday = int(lesson.get("day", lesson.get("weekday", -1)))
    except (TypeError, ValueError):
        return set()
    grades = {class_grades.get(str(class_id), "") for class_id in class_ids}
    if rule.get("grade"):
        grades.add(str(rule["grade"]))
    grades.discard("")
    modes = {resolve_payload_day_mode(payload, grade, weekday, class_grades)[0] for grade in grades}
    return {str(mode) for mode in modes if mode is not None}


def lesson_is_online(lesson: Mapping[str, Any], modes: set[str]) -> bool:
    if lesson.get("format") == "online":
        return True
    return str(lesson.get("room") or "").strip().casefold() in {"онлайн", "online"} or modes == {"online"}


def physical_room_key(lesson, payload, grades):
    from backend.services.schedule_rooms import room_key
    modes = _lesson_day_modes(payload, lesson, grades)
    if lesson_is_online(lesson, modes) or modes == {"sdep"}:
        return ""
    key = room_key(lesson.get("room"))
    return "" if key in {"онлайн", "online"} else key


def _lesson_interval(lesson: Mapping[str, Any], slots: list[Any]) -> tuple[int, int] | None:
    try:
        slot = slots[int(lesson.get("period", -1))]
        if int(lesson.get("period", -1)) < 0:
            slot = {}
    except (IndexError, TypeError, ValueError):
        slot = {}
    if not isinstance(slot, Mapping):
        slot = {}
    explicit_range = bool(lesson.get("start") and lesson.get("end") and
                          (lesson.get("start") != slot.get("start") or lesson.get("end") != slot.get("end")))
    use_range = lesson.get("kind") == "event" or lesson.get("canonicalRole") == "special_event" or explicit_range
    start = _time_minutes((lesson.get("start") if use_range else None) or slot.get("start") or lesson.get("start"))
    end = _time_minutes((lesson.get("end") if use_range else None) or slot.get("end") or lesson.get("end"))
    return (start, end) if start is not None and end is not None and start < end else None


def _grade_number(value: Any) -> str | None:
    """Extract a grade from an explicit grade label or human class label.

    Callers pass canonical group metadata/display names, never internal group
    identifiers. Keeping this small parser centralized prevents technical IDs
    from accidentally expanding a lesson into a parallel.
    """
    label = str(value or "").strip()
    if ":" in label:
        return None
    match = re.match(r"^\s*(\d{1,2})(?!\d)", label, re.IGNORECASE)
    return match.group(1) if match else None


def _lesson_weekday(lesson: Mapping[str, Any]) -> int | None:
    raw_day = lesson.get("day", lesson.get("weekday"))
    if str(raw_day) in {"0", "1", "2", "3", "4", "5", "6"}:
        return int(raw_day)
    return None


def _load_experiment_audience_snapshot(database: Database, start: str, end: str) -> dict[str, Any]:
    with database.connection() as connection:
        return {
            "students": database.execute(connection,
                "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active'").fetchall(),
            "groups": database.execute(connection, """SELECT id,name AS raw_name,display_name,
                COALESCE(display_name,name) AS name,group_type,base_class_name,semantic_dimension
                FROM groups WHERE canonical IS TRUE AND archived_at IS NULL""").fetchall(),
            "memberships": database.execute(connection, """SELECT m.identity_id,m.group_id,m.valid_from,m.valid_until FROM memberships m
                JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
                WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student'
                  AND i.status='active' AND g.canonical IS TRUE AND g.archived_at IS NULL
                  AND (m.valid_from IS NULL OR m.valid_from <= ?)
                  AND (m.valid_until IS NULL OR m.valid_until >= ?)""", (end, start)).fetchall(),
            "membership_overrides": database.execute(connection, """SELECT identity_id,group_id,action,valid_from,valid_until
                FROM membership_overrides WHERE active IS TRUE AND member_role='student' AND action IN ('include','exclude')
                  AND (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_until IS NULL OR valid_until >= ?)""", (end, start)).fetchall(),
        }


def experiment_audience_preview(database: Database, payload: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve all audiences from one experimental snapshot, without writing data."""
    import logging
    from time import perf_counter
    total_started = perf_counter()
    raw_date = payload.get("reference_date") or payload.get("week_start")
    reference_date = date.fromisoformat(str(raw_date)[:10]) if raw_date else date.today()
    lessons = payload.get("lessons") or []
    slots = payload.get("slots") or DEFAULT_SLOTS
    if not isinstance(lessons, list) or not isinstance(slots, list):
        raise ValueError("lessons and slots must be lists")
    query_ms = 0.0
    if snapshot is None:
        query_started = perf_counter()
        day = reference_date.isoformat()
        snapshot = _load_experiment_audience_snapshot(database, day, day)
        query_ms = (perf_counter() - query_started) * 1000
    students = {str(row["id"]): dict(row) for row in snapshot["students"]}
    groups = {str(row["id"]): dict(row) for row in snapshot["groups"]}
    memberships = snapshot["memberships"]
    membership_overrides = snapshot["membership_overrides"]
    calculation_started = perf_counter()
    directory_state = {
        "students": sorted(students.values(), key=lambda row: str(row.get("id"))),
        "groups": sorted(groups.values(), key=lambda row: str(row.get("id"))),
        "memberships": sorted((dict(row) for row in memberships), key=lambda row: (str(row.get("identity_id")), str(row.get("group_id")), str(row.get("valid_from")), str(row.get("valid_until")))),
        "membership_overrides": sorted((dict(row) for row in membership_overrides), key=lambda row: (str(row.get("identity_id")), str(row.get("group_id")), str(row.get("action")), str(row.get("valid_from")), str(row.get("valid_until")))),
    }
    directory_fingerprint = hashlib.sha256(json.dumps(directory_state, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()
    excluded_pairs = {(str(row["identity_id"]), str(row["group_id"])) for row in membership_overrides if row["action"] == "exclude"}
    included_pairs = {(str(row["identity_id"]), str(row["group_id"])) for row in membership_overrides if row["action"] == "include"}
    by_group: dict[str, set[str]] = {}
    raw_members_by_group: dict[str, set[str]] = {}
    for row in memberships:
        student_id = str(row["identity_id"])
        group_id = str(row["group_id"])
        if student_id in students:
            raw_members_by_group.setdefault(group_id, set()).add(student_id)
            if (student_id, group_id) not in excluded_pairs:
                by_group.setdefault(group_id, set()).add(student_id)
    for student_id, group_id in included_pairs:
        if student_id in students and group_id in groups:
            raw_members_by_group.setdefault(group_id, set()).add(student_id)
            if (student_id, group_id) not in excluded_pairs:
                by_group.setdefault(group_id, set()).add(student_id)
    by_class: dict[str, set[str]] = {group_id: by_group.get(group_id, set()) for group_id, group in groups.items() if group["group_type"] == "class"}
    student_classes: dict[str, set[str]] = {}
    for class_id, members in by_class.items():
        for student_id in members:
            student_classes.setdefault(student_id, set()).add(class_id)

    def resolve_explicit(rule: Mapping[str, Any], *, allow_empty: bool = False) -> tuple[set[str], str | None]:
        kind = rule["kind"]
        if kind == "students":
            requested = (set(rule["student_ids"]) | set(rule.get("include_student_ids") or [])) - set(rule.get("exclude_student_ids") or [])
            missing = requested - students.keys()
            return requested & students.keys(), (f"Ученики не найдены: {', '.join(sorted(missing))}" if missing else None)
        if kind not in {"groups", "classes", "available_slot", "legacy_remaining"}:
            return set(), "У занятия не указана аудитория"
        ids = rule["group_ids"] if kind == "groups" else rule["class_ids"]
        if kind == "groups" and rule.get("legacy"):
            instructional = [item for item in ids if item in groups and groups[item]["group_type"] != "class"]
            if instructional:
                ids = instructional
        ids_from_grade = kind in {"classes", "available_slot", "legacy_remaining"} and not ids and bool(rule["grade"])
        if ids_from_grade:
            grade = _grade_number(rule["grade"])
            if grade:
                ids = [group_id for group_id, group in groups.items()
                       if group["group_type"] == "class"
                       and _grade_number(group.get("base_class_name") or group.get("display_name") or group.get("raw_name") or group.get("name")) == grade
                       and not re.fullmatch(r"\s*" + re.escape(grade) + r"\s*класс\s*",
                                            str(group.get("display_name") or group.get("raw_name") or group.get("name") or ""), re.IGNORECASE)]
        if not ids:
            return set(), "Не выбран исходный класс или параллель" if kind in {"available_slot", "legacy_remaining"} else "Не выбраны группы или классы"
        wrong = [item for item in ids if item not in groups or (kind != "groups" and groups[item]["group_type"] != "class")
                 or (kind == "groups" and not rule.get("legacy") and groups[item]["group_type"] == "class")]
        if wrong:
            return set(), "Не найдены выбранные группы или классы в школьном справочнике."
        can_skip_unpopulated_grade_class = ids_from_grade and any(by_group.get(item) for item in ids)
        empty = [item for item in ids if not by_group.get(item)
                 and (kind == "groups" or bool(raw_members_by_group.get(item))
                      or not can_skip_unpopulated_grade_class)]
        if empty and not allow_empty:
            return set(), f"Неизвестен состав аудитории «{', '.join(str(groups[item]['name'] or item) for item in empty)}»."
        resolved = set().union(*(by_group.get(item, set()) for item in ids))
        if kind == "legacy_remaining":
            partition_ids = set(rule.get("partition_group_ids") or [])
            dimension = rule.get("partition_dimension")
            if dimension:
                partition_ids.update(group_id for group_id, group in groups.items()
                                     if group.get("semantic_dimension") == dimension)
            if not partition_ids:
                return set(), "В исходном остатке не указано разбиение"
            unknown_partitions = [item for item in partition_ids if item not in groups or not by_group.get(item)]
            if unknown_partitions:
                return set(), f"Неизвестен состав групп разбиения: {', '.join(sorted(unknown_partitions))}"
            resolved.difference_update(set().union(*(by_group[item] for item in partition_ids)))
        if rule.get("include_student_ids"):
            unknown_students = set(rule["include_student_ids"]) - students.keys()
            if unknown_students:
                return set(), f"Ученики не найдены: {', '.join(sorted(unknown_students))}"
            resolved.update(rule["include_student_ids"])
        resolved.difference_update(rule.get("exclude_student_ids") or [])
        return resolved, None

    states: dict[int, dict[str, Any]] = {}
    states_by_day: dict[int | None, list[int]] = {}
    elective_classes: dict[tuple[int | None, tuple[int, int], str], set[str]] = {}
    elective_indexes: dict[tuple[int | None, tuple[int, int], str], list[int]] = {}
    for index, lesson in enumerate(lessons):
        if not isinstance(lesson, Mapping):
            raise ValueError("Each lesson must be an object")
        lesson_id = str(lesson.get("id") or index)
        rule = _lesson_audience(lesson)
        interval = _lesson_interval(lesson, slots)
        base, reason = resolve_explicit(rule, allow_empty=_lesson_activity_type(lesson) == "consultation")
        if not rule["kind"] and (_lesson_special_mark(lesson) == "no_lesson"
                                  or lesson.get("kind") == "event" and lesson.get("informational") is True
                                  or _is_lunch_lesson(lesson) and lesson.get("needsClarification") is True):
            reason = None  # An informational event has no assigned students.
        if rule["kind"] == "available_slot" and not interval:
            reason = "Не задан корректный временной интервал"
        activity_type = _lesson_activity_type(lesson)
        optional_roster = activity_type in {"club", "elective", "consultation"}
        snapshot_value = None if optional_roster else lesson.get("audience_snapshot")
        saved_snapshot = dict(snapshot_value) if isinstance(snapshot_value, Mapping) else None
        snapshot_ids = set(_ids(saved_snapshot.get("student_ids"))) if saved_snapshot else set()
        day = _lesson_weekday(lesson)
        states[index] = {"id": lesson_id, "lesson": lesson, "rule": rule, "interval": interval,
                         "base": base, "occupancy_base": snapshot_ids if saved_snapshot is not None else base,
                         "snapshot": saved_snapshot, "snapshot_ids": snapshot_ids,
                         "fingerprint": _audience_fingerprint(lesson, payload), "reason": reason}
        if activity_type != "consultation":
            states_by_day.setdefault(day, []).append(index)
        if activity_type == "elective" and lesson.get("blockId") and interval:
            key = (day, interval, str(lesson["blockId"]))
            class_ids = _ids(lesson.get("classIds") or lesson.get("displayClassIds"))
            if lesson.get("classId"):
                class_ids.append(str(lesson["classId"]))
            elective_classes.setdefault(key, set()).update(class_ids)
            elective_indexes.setdefault(key, []).append(index)
    for key, classes in elective_classes.items():
        cohort = set().union(*(by_class.get(class_id, set()) for class_id in classes))
        for index in elective_indexes[key]:
            # Only the common class card occupies the slot. A direction
            # never acquires a personal roster through this calculation.
            states[index]["occupancy_base"] = cohort

    def possible_cohort(rule: Mapping[str, Any]) -> set[str] | None:
        """Narrow an unknown assignment only when its class context is certain."""
        class_ids = rule.get("class_ids") or []
        if class_ids and all(item not in groups for item in class_ids):
            # Some imported constructor lessons retain display-only IDs such
            # as ``grade7:base:7-А``. A shared explicit grade prefix is enough
            # to scope the uncertainty to that canonical parallel.
            matches = [re.match(r"^grade(\d+):base:", item, re.IGNORECASE) for item in class_ids]
            prefixes = {match.group(1) for match in matches if match}
            if len(matches) == len(class_ids) and len(prefixes) == 1:
                grade = next(iter(prefixes))
                scoped = [item for item, group in groups.items()
                          if group["group_type"] == "class"
                          and re.match(r"^\s*" + grade + r"(?!\d)", str(group["name"]))]
                if scoped:
                    return set().union(*(by_class[item] for item in scoped))
        if not class_ids and rule.get("kind") == "groups":
            group_ids = rule.get("group_ids") or []
            if not group_ids or any(item not in groups for item in group_ids):
                return None
            names = {str(groups[item].get("base_class_name") or "").strip().casefold() for item in group_ids}
            if not names or "" in names:
                return None
            class_ids = [item for item, group in groups.items()
                         if group["group_type"] == "class" and str(group["name"]).strip().casefold() in names]
            if not class_ids:
                return None
        if class_ids and all(item in by_class for item in class_ids):
            return set().union(*(by_class[item] for item in class_ids))
        return None

    def scope_grade(rule: Mapping[str, Any]) -> str | None:
        grade = _grade_number(rule.get("grade"))
        if grade:
            return grade
        class_ids = rule.get("class_ids") or []
        display_grades = [re.match(r"^grade(\d+):base:", item, re.IGNORECASE) for item in class_ids]
        if class_ids and all(display_grades):
            values = {match.group(1) for match in display_grades if match}
            return next(iter(values)) if len(values) == 1 else None
        class_names = [str(groups[item].get("base_class_name") or groups[item].get("display_name") or groups[item].get("raw_name") or groups[item].get("name") or "") for item in class_ids
                       if item in groups and groups[item].get("group_type") == "class"]
        values = {value for name in class_names if (value := _grade_number(name))}
        return next(iter(values)) if class_names and len(values) == 1 else None
    results: dict[str, dict[str, Any]] = {}
    for state_key, state in states.items():
        lesson_id = state["id"]
        rule, interval, base = state["rule"], state["interval"], state["base"]
        reason = state["reason"]
        status = "ok"
        if reason:
            invalid_id = reason.startswith(("Не найдены выбранные группы", "Ученики не найдены"))
            status = "error" if state["lesson"].get("importError") or invalid_id else "incomplete"
        excluded: set[str] = set()
        blockers: list[dict[str, Any]] = []
        is_lunch = _is_lunch_lesson(state["lesson"])
        if (rule["kind"] == "available_slot" or is_lunch) and not reason:
            for other_key in states_by_day.get(_lesson_weekday(state["lesson"]), []):
                if other_key == state_key:
                    continue
                other = states[other_key]
                if _is_lunch_lesson(other["lesson"]) or _lesson_special_mark(other["lesson"]) == "no_lesson":
                    continue
                other_interval = other["interval"]
                if not other_interval or not interval or not (interval[0] < other_interval[1] and other_interval[0] < interval[1]):
                    continue
                overlap = base & other["occupancy_base"]
                if other["rule"]["kind"] == "available_slot":
                    if is_lunch:
                        # A concurrent remainder assignment is itself an
                        # educational fallback, so those candidate students
                        # cannot also be claimed by lunch. Keep the lesson
                        # resolved with an empty overlap instead of treating
                        # lunch as a conflicting academic activity.
                        excluded.update(overlap)
                    elif overlap:
                        blockers.append({"lesson_id": other["id"],
                                         "subject": str(other["lesson"].get("subject") or other["lesson"].get("activity") or ""),
                                         "reason": "Пересекаются исходные аудитории двух занятий «Все остальные»"})
                elif other["reason"]:
                    potential = possible_cohort(other["rule"])
                    known_other_grade = scope_grade(other["rule"])
                    known_target_grade = scope_grade(rule)
                    proven_different_grades = (known_other_grade is not None and known_target_grade is not None
                                               and known_other_grade != known_target_grade)
                    if not proven_different_grades and (potential is None or base & potential):
                        blockers.append({"lesson_id": other["id"],
                                         "subject": str(other["lesson"].get("subject") or other["lesson"].get("activity") or ""),
                                         "reason": "Состав пересекающегося занятия неизвестен: " + other["reason"]})
                else:
                    excluded.update(overlap)
        if blockers:
            status = "ambiguous"
            reason = blockers[0]["reason"]
        candidate = base - excluded
        has_uncertainty = bool(blockers) or reason is not None
        snapshot = state["snapshot"]
        frozen_ids = state["snapshot_ids"]
        assigned = (frozen_ids if snapshot is not None else candidate) if status == "ok" or snapshot is not None else set()
        can_propose = status == "ok"
        proposed = sorted(candidate) if can_propose else None
        fingerprint_changed = snapshot is not None and state["fingerprint"] != str(snapshot.get("fingerprint") or "")
        # A changed time/date fingerprint alone does not mean the frozen roster
        # changed. Keep review mandatory when the current rule is ambiguous or
        # invalid, and when its resolved participants differ from the snapshot.
        needs_recalculation = snapshot is not None and (status != "ok" or proposed != sorted(frozen_ids))
        source_known = state["reason"] is None
        results[lesson_id] = {"status": status, "student_ids": sorted(assigned),
                              "audience_snapshot": snapshot,
                              "needs_recalculation": needs_recalculation,
                              "fingerprint_changed": fingerprint_changed,
                              "proposed_student_ids": proposed,
                              "proposed_count": len(proposed) if proposed is not None else None,
                              "source_count": len(base) if source_known else None,
                              "count": len(assigned) if snapshot is not None else None if has_uncertainty else len(assigned),
                              "potential_count": (len(candidate) if source_known and (blockers or rule["kind"] == "available_slot")
                                                  else None if reason else len(assigned)),
                              "class_ids": sorted(set().union(*(student_classes.get(item, set()) for item in assigned))),
                              "excluded_count": len(excluded) if source_known else None, "blockers": blockers,
                              **({"reason": reason} if reason else {})}
    if os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
        logging.getLogger(__name__).info(
            "school audience preview stages query_ms=%.1f calculation_ms=%.1f total_ms=%.1f lessons=%d students=%d groups=%d memberships=%d overrides=%d",
            query_ms, (perf_counter() - calculation_started) * 1000,
            (perf_counter() - total_started) * 1000, len(lessons), len(students),
            len(groups), len(memberships), len(membership_overrides),
        )
    return {"lessons": results, "directory_fingerprint": directory_fingerprint}


def experiment_week_audience_preview(
    database: Database, payload: Mapping[str, Any], reference: date, *,
    snapshot_for_date: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve each authored weekday on its own date using only overlapping rows."""
    monday = reference - timedelta(days=reference.weekday())
    lessons_by_day: dict[int | None, list[Mapping[str, Any]]] = {}
    for index, lesson in enumerate(payload.get("lessons") or []):
        weekday = _lesson_weekday(lesson)
        lessons_by_day.setdefault(weekday, []).append(lesson if lesson.get("id") else {**lesson, "id": str(index)})
    if not lessons_by_day:
        return {"lessons": {}, "directory_fingerprint": ""}
    # Imported rows can be reviewed and saved before they have a weekday.
    # Resolve their directory evidence on the reference date, isolated from
    # the placed lessons that will be eligible for publication.
    dates = {day: (monday + timedelta(days=day)).isoformat() if day is not None else reference.isoformat()
             for day in lessons_by_day}
    if snapshot_for_date is None:
        directory_snapshot = _load_experiment_audience_snapshot(database, min(dates.values()), max(dates.values()))

        def snapshot_for_date(day: str) -> dict[str, Any]:
            return {**directory_snapshot, **{key: [row for row in directory_snapshot[key]
                                          if (not row["valid_from"] or str(row["valid_from"]) <= day)
                                          and (not row["valid_until"] or day <= str(row["valid_until"]))]
                                   for key in ("memberships", "membership_overrides")}}
    results, fingerprints = {}, {}
    for day, lessons in sorted(lessons_by_day.items(), key=lambda item: (item[0] is None, item[0] or 0)):
        day_text = dates[day]
        daily_payload = {**payload, "reference_date": day_text, "lessons": []}
        for lesson in lessons:
            snapshot = lesson.get("audience_snapshot")
            if isinstance(snapshot, Mapping) and snapshot.get("fingerprint") == _audience_fingerprint(lesson, payload):
                # A change of evaluation date alone does not change a confirmed
                # roster. The actual participants are still checked below.
                snapshot = {**snapshot, "fingerprint": _audience_fingerprint(lesson, daily_payload)}
                lesson = {**lesson, "audience_snapshot": snapshot}
            daily_payload["lessons"].append(lesson)
        resolved = experiment_audience_preview(database, daily_payload, snapshot=snapshot_for_date(day_text))
        results.update(resolved["lessons"])
        fingerprints[str(day)] = resolved["directory_fingerprint"]
    return {"lessons": results, "directory_fingerprint": hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _canonical_payload(database: Database, version_id: str, *, source_blocks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    template = {"blocks": source_blocks} if source_blocks is not None else read_canonical_template(database, version_id)
    if not template:
        raise ValueError("Canonical schedule version was not found")
    with database.connection() as connection:
        class_rows = database.execute(connection, "SELECT id,COALESCE(display_name,name) AS name FROM groups WHERE canonical IS TRUE AND group_type='class' ORDER BY name").fetchall()
    classes = {str(row["id"]): str(row["name"]) for row in class_rows}
    with database.connection() as connection:
        known_group_ids = {str(row["id"]) for row in database.execute(connection, "SELECT id FROM groups WHERE canonical IS TRUE").fetchall()}
    lessons = []
    days: set[int] = set()
    slot_set: set[tuple[str, str]] = set()
    pending: list[tuple[str, int, Mapping[str, Any], Mapping[str, Any], str, str, list[str]]] = []
    for block in (template.get("blocks") or {}).values():
        weekday = block.get("weekday")
        if weekday is not None:
            weekday = int(weekday)
            days.add(weekday)
        start = _canonical_time(block.get("start_time") or (block.get("slot") or {}).get("start"))
        end = _canonical_time(block.get("end_time") or (block.get("slot") or {}).get("end"))
        if start and end:
            slot_set.add((start, end))
        for assignment_index, assignment in enumerate(block.get("assignments") or []):
            group_ids = [str(item) for item in assignment.get("canonical_group_ids") or []]
            pending.append((str(block.get("block_key") or ""), assignment_index, block, assignment, start, end, group_ids))
    # Keep the canonical bell schedule intact even when no block happens to use
    # a period. Building slots from occupied times alone renumbers lessons and
    # can turn (for example) period 5 at 12:45 into the third grid row.
    slot_keys = {(slot["start"], slot["end"]) for slot in DEFAULT_SLOTS}
    slot_keys.update(slot_set)
    slots = [{"start": start, "end": end} for start, end in sorted(slot_keys)]
    slot_indexes = {(slot["start"], slot["end"]): index for index, slot in enumerate(slots)}
    class_memberships: dict[str, set[str]] = {}
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT DISTINCT audience.group_id AS audience_id, base.group_id AS class_id
            FROM memberships audience
            JOIN memberships base ON base.identity_id=audience.identity_id AND base.active IS TRUE AND base.member_role='student'
            JOIN groups base_group ON base_group.id=base.group_id AND base_group.canonical IS TRUE AND base_group.group_type='class'
            WHERE audience.active IS TRUE AND audience.member_role='student'"""
        ).fetchall()
    for row in rows:
        class_memberships.setdefault(str(row["audience_id"]), set()).add(str(row["class_id"]))
    class_ids = set(classes)
    for block_key, assignment_index, block, assignment, start, end, group_ids in pending:
        weekday = block.get("weekday")
        weekday = int(weekday) if weekday is not None else None
        grade_scope = str(block.get("grade_scope") or "").strip()
        target_class_ids = {item for item in group_ids if item in class_ids}
        source_metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
        source_rule = source_metadata.get("audience_rule") if isinstance(source_metadata.get("audience_rule"), Mapping) else {}
        rule_class_id = str(source_rule.get("class_group_id") or "")
        if rule_class_id in class_ids:
            target_class_ids.add(rule_class_id)
        for group_id in group_ids:
            target_class_ids.update(class_memberships.get(group_id, set()))
        if not target_class_ids and grade_scope:
            grade_tokens = {part.strip() for part in grade_scope.replace("класс", "").split(",") if part.strip()}
            grade_numbers = set(re.findall(r"\d+", grade_scope))
            exact_matches = {
                group_id for group_id, name in classes.items()
                if any(name.strip().casefold() in {token.casefold(), f"{token} класс".casefold()} for token in grade_tokens)
            }
            target_class_ids.update(exact_matches or {
                group_id for group_id, name in classes.items()
                if any(name.strip().casefold() in {token.casefold(), f"{token} класс".casefold()}
                       or name.strip().casefold().startswith(token.casefold()) for token in grade_tokens)
                or any(name.strip().startswith(number) for number in grade_numbers)
            })
        ordered_class_ids = sorted(target_class_ids, key=lambda item: (classes.get(item, "").casefold(), item))
        class_id = ordered_class_ids[0] if len(ordered_class_ids) == 1 else ""
        teacher_ids = [str(item) for item in assignment.get("teacher_ids") or []]
        metadata = assignment.get("metadata") or {}
        details = metadata.get("constructor_details") if isinstance(metadata.get("constructor_details"), Mapping) else {}
        descriptive = {key: details[key] for key in (
            "description", "place", "format", "connectionInfo", "responsible_teacher_ids", "kind", "informational",
            "activityCatalogId", "activityNameSnapshot", "eligibleGrades",
            "confirmedParticipantIds", "electiveDirection", "blockId", "blockName") if key in details}
        activity_type = metadata.get("activity_type")
        if activity_type in {"club", "elective", "event", "consultation", "practice", "laboratory"}:
            descriptive["activityType"] = activity_type
        if metadata.get("special_mark") in {"lunch", "no_lesson"}:
            descriptive["specialMark"] = metadata["special_mark"]
        role = str(assignment.get("role") or assignment.get("assignment_kind") or "")
        audience_kind = str(assignment.get("audience_kind") or (metadata.get("audience") or {}).get("type") or "")
        audience_rule = metadata.get("audience_rule") if isinstance(metadata.get("audience_rule"), Mapping) else {}
        activity = str(assignment.get("activity") or "")
        raw_room = display_room(metadata.get("room"))
        room = "" if raw_room in {"—", "–", "-"} else raw_room
        no_lesson = role == "explicit_no_lesson" or activity.strip().casefold() in {"нет урока", "нет занятия"}
        if no_lesson:
            descriptive["specialMark"] = "no_lesson"
        residual = not no_lesson and (role in {"default", "residual", "final_state", "final_unassigned"} or audience_kind in {"complement", "remaining"} or audience_rule.get("kind") == "remaining")
        time_is_valid = bool(start and end and (start, end) in slot_indexes)
        unknown_group_ids = [group_id for group_id in group_ids if group_id not in known_group_ids]
        explicit_participants = audience_rule.get("kind") == "students"
        teacher_only = activity_type == "club" or descriptive.get("informational") is True
        missing_scope = not ordered_class_ids and not group_ids and not residual and not explicit_participants and not teacher_only
        requires_clarification = missing_scope or weekday is None or not time_is_valid or bool(unknown_group_ids)
        source_assignment = _canonical_provenance(assignment)
        source_block = _canonical_provenance(block)
        lessons.append({
            **descriptive,
            "id": f"{block_key}:{assignment_index}",
            "day": int(weekday) if weekday is not None else -1,
            "period": slot_indexes.get((start, end), -1),
            "classId": class_id,
            "classIds": ordered_class_ids,
            "subject": activity,
            "teacherId": teacher_ids[0] if teacher_ids else "",
            "groupId": next((group_id for group_id in group_ids if group_id not in classes), group_ids[0] if group_ids else ""),
            "room": room,
            "note": str(metadata.get("note") or ""),
            "groupIds": group_ids,
            "teacherIds": teacher_ids,
            "start": start,
            "end": end,
            "grade_scope": str(block.get("grade_scope") or ""),
            "audienceMode": "remainder" if residual else "canonical",
            "canonicalRole": role,
            "excludedGroupIds": [str(value) for value in audience_rule.get("partition_group_ids") or []],
            "needsClarification": requires_clarification,
            "importError": bool(unknown_group_ids),
            "clarificationReason": (f"Canonical-группы отсутствуют в справочнике: {', '.join(unknown_group_ids)}." if unknown_group_ids else
                                    "Не удалось сопоставить canonical-аудиторию с классами или группами." if missing_scope else
                                    "В исходном блоке не указаны день или время." if weekday is None or not time_is_valid else ""),
            "canonical": {"block_key": block_key, "assignment_index": assignment_index, "assignment": source_assignment, "block": source_block},
        })
    transferred = sum(not lesson["needsClarification"] and not lesson["importError"] for lesson in lessons)
    return {"days": sorted(days) or [0, 1, 2, 3, 4], "slots": slots, "classes": list(classes), "lessons": lessons,
            "transfer_result": {"transferred": transferred, "needs_clarification": sum(bool(lesson["needsClarification"]) and not lesson["importError"] for lesson in lessons),
                                "import_errors": sum(bool(lesson["importError"]) for lesson in lessons)}}


def list_experiments(database: Database) -> list[dict[str, Any]]:
    with database.connection() as connection:
        rows = database.execute(connection, "SELECT id,name,base_version_id,revision,created_at,updated_at,payload FROM schedule_experiment_drafts ORDER BY updated_at DESC,id").fetchall()
        selection_table = database.execute(connection,
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='school_template_selection'"
            if database.database_url else
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_template_selection'").fetchone()
        selected_template_id = None
        if selection_table:
            selected = database.execute(connection,
                "SELECT draft_id FROM school_template_selection WHERE scope='school'").fetchone()
            selected_template_id = str(selected["draft_id"]) if selected else None
        publication_table = database.execute(connection,
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='schedule_constructor_publications'"
            if database.database_url else
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_constructor_publications'").fetchone()
        published_draft_ids = {str(row["draft_id"]) for row in database.execute(connection,
            "SELECT DISTINCT draft_id FROM schedule_constructor_publications").fetchall()} if publication_table else set()
        archived_ids = [str(row["id"]) for row in rows if _payload(row["payload"]).get("draft_archive")]
        deletable_ids = {draft_id for draft_id in archived_ids
                         if not _draft_links(database, connection, draft_id)
                         and not any(str(row["id"]) != draft_id and draft_id in str(row["payload"]) for row in rows)}
    result = []
    for row in rows:
        item = dict(row)
        payload = _payload(item.pop("payload"))
        lessons = payload.get("lessons") or []
        tagged = payload.get("draft_kind")
        if tagged in {"demo", "test", "user", "school"}:
            kind = tagged
        elif item["name"].startswith(("E2E filled week ", "Проверочная копия canonical ")) or any(
                str(lesson.get("id") or "").startswith("e2e-") for lesson in lessons if isinstance(lesson, Mapping)):
            kind = "test"
        else:
            kind = "unclassified"
        group_ids = {str(group_id) for lesson in lessons if isinstance(lesson, Mapping)
                     for group_id in (lesson.get("groupIds") or [lesson.get("groupId")]) if group_id}
        item.update({"draft_kind": kind, "lesson_count": len(lessons),
                     "event_count": sum(lesson.get("kind") == "event" for lesson in lessons if isinstance(lesson, Mapping)),
                     "group_count": len(group_ids), "day_count": len(payload.get("days") or []),
                     "week_start": payload.get("week_start"),
                     # The explicit selection pointer is authoritative. Older role
                     # tags can remain on superseded copies and must not promote them.
                     "is_primary_template": bool(selected_template_id and str(item["id"]) == selected_template_id),
                     "has_publications": str(item["id"]) in published_draft_ids,
                     "archived_at": (payload.get("draft_archive") or {}).get("at"),
                     "has_active_laboratories": bool(payload.get("laboratory_meetings")),
                     "can_delete": str(item["id"]) in deletable_ids and not payload.get("laboratory_meetings"),
                     "source_template": payload.get("source_template")})
        result.append(item)
    return result


def get_experiment(database: Database, experiment_id: str) -> dict[str, Any]:
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?", (experiment_id,)).fetchone()
    if not row:
        raise ExperimentNotFound(experiment_id)
    return _item(row)


def _draft_actor_id(database: Database, actor_user_id: Any) -> Any:
    if database.database_url or actor_user_id is None:
        return actor_user_id
    # Local school drafts use SQLite's users(id INTEGER) foreign key; the
    # authenticated school administrator has a PostgreSQL UUID instead.
    if isinstance(actor_user_id, int) and not isinstance(actor_user_id, bool):
        return actor_user_id
    if isinstance(actor_user_id, str) and actor_user_id.isdecimal():
        return int(actor_user_id)
    return None


def create_experiment(database: Database, *, name: str, source: str, payload: Mapping[str, Any] | None = None,
                      actor_user_id: Any = None, _base_version_id: str | None = None,
                      _trusted_resolution_history: bool = False, _copy_key: str | None = None,
                      _source_guard: tuple[str, int] | None = None) -> dict[str, Any]:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("name is required")
    if source not in {"empty", "canonical", "import"}:
        raise ValueError("source must be empty, canonical, or import")
    if source == "import" and not payload:
        raise ValueError("Imported experiments require a structured payload")
    if source == "empty":
        with database.connection() as connection:
            rows = database.execute(connection, "SELECT id FROM groups WHERE canonical IS TRUE AND group_type='class' ORDER BY name").fetchall()
        seed = {"days": [0, 1, 2, 3, 4], "slots": DEFAULT_SLOTS, "classes": [str(row["id"]) for row in rows], "lessons": []}
        if payload:
            seed.update(dict(payload))
        payload = seed
    # Internal trusted-copy provenance only. Public import payloads cannot pick
    # a canonical base; HTTP adapters deliberately do not expose this argument.
    base_version_id = str(_base_version_id) if _base_version_id else None
    if source == "canonical":
        version = canonical_version(database)
        if not version:
            raise ValueError("No canonical schedule is available")
        base_version_id = str(version["version_id"])
        seed = _canonical_payload(database, base_version_id)
        if payload:
            seed.update(dict(payload))
        payload = seed
    experiment_id = str(uuid5(NAMESPACE_URL, f"my-island-school/schedule/{_copy_key}") if _copy_key else uuid4())
    now = datetime.now(timezone.utc).isoformat()
    transfer_result = dict((payload or {}).get("transfer_result") or {}) if source in {"canonical", "import"} else None
    clean_payload = _clean_payload(payload)
    if _trusted_resolution_history and payload.get("laboratory_blueprint_history"):
        clean_payload["laboratory_blueprint_history"] = payload["laboratory_blueprint_history"]
    _validate_day_modes(database, clean_payload)
    from backend.services.schedule_term_comparison import retain_term_comparison
    if not _trusted_resolution_history:
        if clean_payload.get("laboratory_meetings") or clean_payload.get("laboratory_history"):
            raise ValueError("Активируйте лабораторию через предпросмотр замещения")
        for lesson in clean_payload["lessons"]:
            lesson.pop("resolution_history", None)
            lesson.pop("attendance_history", None)
            lesson.pop("attendance_week_baseline", None)
    if not _trusted_resolution_history or clean_payload.get("week_start"):
        retain_term_comparison(clean_payload, None)
    provenance = clean_payload.get("school_source")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    if provenance.get("role") == "primary_school_template" and not provenance.get("primary_draft_id"):
        clean_payload["school_source"] = {**provenance, "primary_draft_id": experiment_id}
    clean_payload["lessons"] = validate_activity_lessons(clean_payload["lessons"], list_activities(database, include_archived=True))
    encoded = json.dumps(clean_payload, ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    actor_id = _draft_actor_id(database, actor_user_id)
    params = (experiment_id, normalized_name, base_version_id, encoded, actor_id, actor_id, now, now)
    insert_values = f"VALUES (?,?,?,1,{encoded_sql},?,?,?,?)"
    if _source_guard:
        source_lock = " FOR SHARE" if database.database_url else ""
        insert_values = f"""SELECT ?,?,?,1,{encoded_sql},?,?,?,? WHERE EXISTS (
            SELECT 1 FROM schedule_experiment_drafts WHERE id=? AND revision=?{source_lock})"""
        params += _source_guard
    with database.connection() as connection:
        row = database.execute(connection, f"""INSERT INTO schedule_experiment_drafts
            (id,name,base_version_id,revision,payload,created_by,updated_by,created_at,updated_at)
            {insert_values} ON CONFLICT (id) DO NOTHING RETURNING *""", params).fetchone()
        if row is None:
            if _source_guard:
                original = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?",
                                            (_source_guard[0],)).fetchone()
                if not original or int(original["revision"]) != _source_guard[1]:
                    raise ExperimentConflict(_item(original) if original else None)
            row = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?", (experiment_id,)).fetchone()
    result = _item(row)
    if transfer_result is not None:
        result["transfer_result"] = transfer_result
    return result


def open_school_canonical_template(database: Database, *, actor_user_id: Any = None) -> dict[str, Any]:
    """Open the persisted primary copy without replacing edits or opening a week.

    The production source is the approved canonical DB, not a local Google
    capture. Serialize first creation across workers; later opens are reads.
    """
    with _school_template_open_lock, database.connection() as lock_connection:
        if database.database_url:
            database.execute(lock_connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                             ("my-island:primary-school-template",))
        version = canonical_version(database)
        if not version:
            raise ValueError("No approved canonical template was found")
        candidates = []
        for summary in list_experiments(database):
            if summary["draft_kind"] != "school":
                continue
            existing = get_experiment(database, summary["id"])
            payload = existing["payload"]
            if payload.get("week_start") or payload.get("source_template"):
                continue
            provenance = payload.get("school_source")
            provenance = provenance if isinstance(provenance, Mapping) else {}
            if (provenance.get("role") == "primary_school_template"
                    and provenance.get("primary_draft_id") == existing["id"]):
                existing["already_exists"] = True
                return existing
            if (existing.get("base_version_id") == str(version["version_id"])
                    and existing["name"] == "Школа · canonical template"):
                candidates.append(existing)
        if candidates:
            existing = min(candidates, key=lambda item: (str(item["created_at"]), item["id"]))
            existing["already_exists"] = True
            return existing
        return create_experiment(database, name="Школа · canonical template", source="canonical",
            payload={"draft_kind": "school", "school_source": {
                "kind": "canonical_template", "role": "primary_school_template",
                "canonical_version_id": str(version["version_id"]),
                "canonical_fingerprint": str(version.get("source_fingerprint") or ""),
            }, "import_review": {"status": "canonical_copy", "message":
                "Копия подтверждённого canonical-расписания из школьной базы. Исходный Google-шаблон при открытии не перечитывается и может отличаться."}},
            actor_user_id=actor_user_id)


def create_effective_week_experiment(database: Database, effective_week_id: str, *, actor_user_id: Any = None) -> dict[str, Any]:
    """Make an editable draft of an exact immutable week; leave runtime untouched."""
    from backend.services.canonical_schedule import _block_rows, _assignments
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM canonical_effective_weeks WHERE effective_week_id=?", (effective_week_id,)).fetchone()
    if not row:
        raise ValueError("Effective week version not found")
    for summary in list_experiments(database):
        if summary.get("week_start") == str(row["week_start"]):
            existing = get_experiment(database, summary["id"])
            if (existing["payload"].get("school_source") or {}).get("effective_week_id") == effective_week_id:
                return existing
    blocks = {}
    for block in _block_rows(database, effective_week_id):
        if block.get("change_kind") == "cancelled":
            continue
        patch = block.get("patch") or {}
        assignments = _assignments(database, block.get("canonical_block_id"), patch)
        assignments = [{**item, "canonical_group_ids": item.get("canonical_group_ids") or
                        (item.get("audience") or {}).get("canonical_group_ids") or []} for item in assignments]
        slot = patch.get("slot") or {}
        blocks[str(block["block_key"])] = {**block, **patch,
            "start_time": slot.get("start") or patch.get("start_time") or block.get("start_time"),
            "end_time": slot.get("end") or patch.get("end_time") or block.get("end_time"), "assignments": assignments}
    version_id = str(row["version_id"])
    from backend.services.canonical_schedule import _published_source_for_effective
    source, _ = _published_source_for_effective(database, dict(row))
    archived = (source or {}).get("payload") or {}
    has_special_history = archived.get("laboratory_blueprints") or archived.get("laboratory_meetings") or archived.get("laboratory_history")
    payload = dict(archived) if has_special_history else _canonical_payload(database, version_id, source_blocks=blocks)
    payload.update({"draft_kind": "school", "week_start": str(row["week_start"]),
                    "reference_date": str(row["week_start"]), "school_source": {
                        "kind": "effective_week", "effective_week_id": effective_week_id, "canonical_version_id": version_id}})
    return create_experiment(database, name=f"Неделя {row['week_start']} · черновик исторической версии", source="import",
                             payload=payload, actor_user_id=actor_user_id, _base_version_id=version_id,
                             _trusted_resolution_history=bool(has_special_history))


def duplicate_experiment(database: Database, experiment_id: str, *, expected_revision: int | None = None, copy_request_id: str | None = None, name: str | None = None, actor_user_id: Any = None) -> dict[str, Any]:
    source = get_experiment(database, experiment_id)
    copy_id = str(uuid4())
    if copy_request_id:
        copy_id = str(uuid5(NAMESPACE_URL, f"my-island/draft-copy/{source['id']}/{expected_revision}/{actor_user_id}/{copy_request_id}"))
        try:
            return get_experiment(database, copy_id)
        except ExperimentNotFound:
            pass
    if expected_revision is not None and int(source["revision"]) != expected_revision:
        raise ExperimentConflict(source)
    normalized_name = str(name or f"{source['name']} — копия").strip()
    if not normalized_name:
        raise ValueError("name is required")
    now = datetime.now(timezone.utc).isoformat()
    duplicate_payload = dict(source["payload"])
    if duplicate_payload.get("draft_kind") == "demo":
        duplicate_payload["draft_kind"] = "user"
    clean_copy = _clean_payload(duplicate_payload)
    if "laboratory_blueprint_history" in duplicate_payload:
        clean_copy["laboratory_blueprint_history"] = duplicate_payload["laboratory_blueprint_history"]
    if clean_copy.get("week_start"):
        clean_copy["week_variant"] = True
    encoded = json.dumps(clean_copy, ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    source_lock = " FOR SHARE" if database.database_url else ""
    with database.connection() as connection:
        row = database.execute(connection, f"""INSERT INTO schedule_experiment_drafts
            (id,name,base_version_id,revision,payload,created_by,updated_by,created_at,updated_at)
            SELECT ?,?,?,1,{encoded_sql},?,?,?,? WHERE EXISTS (
                SELECT 1 FROM schedule_experiment_drafts WHERE id=? AND revision=?{source_lock})
            ON CONFLICT (id) DO NOTHING RETURNING *""",
            (copy_id, normalized_name, source.get("base_version_id"), encoded, _draft_actor_id(database, actor_user_id),
             _draft_actor_id(database, actor_user_id), now, now, source["id"], source["revision"])).fetchone()
        if row is None:
            row = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?", (copy_id,)).fetchone()
        if row is None:
            raise ExperimentConflict(source)
    return _item(row)


def create_experiment_week(database: Database, experiment_id: str, *, week_start: str,
                           expected_revision: int, actor_user_id: Any = None) -> dict[str, Any]:
    with _week_creation_lock:
        if database.database_url:
            key = f"my-island:week:{week_start}"
            with database.connection() as connection:
                database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))", ("my-island:school-template-selection",))
                database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))", (key,))
                return _create_experiment_week_locked(database, experiment_id, week_start=week_start,
                    expected_revision=expected_revision, actor_user_id=actor_user_id)
        return _create_experiment_week_locked(database, experiment_id, week_start=week_start,
            expected_revision=expected_revision, actor_user_id=actor_user_id)


def _create_experiment_week_locked(database: Database, experiment_id: str, *, week_start: str,
                                   expected_revision: int, actor_user_id: Any = None) -> dict[str, Any]:
    """Create a detached week draft copied from a template at a known revision."""
    start = date.fromisoformat(str(week_start))
    if start.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    source = get_experiment(database, experiment_id)
    if int(source["revision"]) != expected_revision:
        raise ExperimentConflict(source)
    if source["payload"].get("draft_archive"):
        raise ValueError("Выберите действующий шаблон для создания недели.")
    # Treat the source template and calendar week as an idempotency key. A
    # repeated request (for example after a lost response) must reopen the
    # independent draft already created for that exact template revision.
    calendar_drafts = False
    for summary in list_experiments(database):
        if summary.get("week_start") != start.isoformat() or summary.get("archived_at"):
            continue
        calendar_drafts = True
        existing = get_experiment(database, summary["id"])
        provenance = existing["payload"].get("source_template") or {}
        if (not existing["payload"].get("week_variant") and str(provenance.get("id") or "") == str(source["id"])
                and int(provenance.get("revision") or 0) == int(source["revision"])):
            existing["already_exists"] = True
            return existing

    if calendar_drafts:
        raise ValueError(f"Неделя с {start.isoformat()} уже сохранена. Выберите черновик из списка.")
    payload = json.loads(json.dumps(source["payload"], ensure_ascii=False))
    if payload.get("laboratory_meetings"):
        from backend.services.schedule_laboratories import restore_lessons
        enabled = payload.pop("laboratory_meetings")
        # Replay the inverse operations in reverse activation order (also after JSONB roundtrips).
        for entry in reversed(payload.get("laboratory_history", [])):
            receipt = enabled.pop(entry["blueprint_id"], None)
            if receipt:
                payload["lessons"] = restore_lessons(payload["lessons"], receipt)
    payload.pop("laboratory_history", None)
    for lesson in payload["lessons"]:
        if "attendance_week_baseline" in lesson:
            baseline = lesson.pop("attendance_week_baseline")
            rule = _lesson_audience(lesson)
            rule["exclude_student_ids"] = baseline
            lesson["audience"] = rule
            lesson.pop("audience_snapshot", None)
    original_by_id = {lesson["id"]: lesson for lesson in payload["lessons"] if lesson.get("id")}
    original_context = dict(payload)
    payload["week_start"] = start.isoformat()
    payload["reference_date"] = start.isoformat()
    payload["source_template"] = {"id": source["id"], "revision": int(source["revision"])}
    for copied in payload.get("lessons") or []:
        original = original_by_id.get(copied.get("id"), copied)
        snapshot = copied.get("audience_snapshot")
        if isinstance(snapshot, dict) and snapshot.get("fingerprint") == _audience_fingerprint(original, original_context):
            # A trusted date-only copy preserves confirmed participants. Rebind
            # the rule fingerprint, never recompute/fill the roster here. A
            # changed target-week membership still requires explicit review.
            snapshot["fingerprint"] = _audience_fingerprint(copied, payload)
    return create_experiment(database, name=f"Неделя {start.isoformat()} · {source['name']}",
                             source="import", payload=payload, actor_user_id=actor_user_id,
                             _base_version_id=source.get("base_version_id"), _trusted_resolution_history=True)


_DRAFT_LINKS = {
    "school_template_selection": ("draft_id", "source_draft_id"),
    "school_template_selection_history": ("previous_draft_id", "next_draft_id"),
    "school_template_baseline_approvals": ("draft_id",),
    "schedule_constructor_publications": ("draft_id",),
    "schedule_sheet_exports": ("draft_id",),
}


def _draft_links(database, connection, experiment_id):
    table_query = ("SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema()"
                   if database.database_url else "SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in database.execute(connection, table_query).fetchall()}
    return {table for table, columns in _DRAFT_LINKS.items() if table in tables
            and database.execute(connection, f"SELECT 1 FROM {table} WHERE "
                + " OR ".join(f"{column}=?" for column in columns) + " LIMIT 1",
                (experiment_id,) * len(columns)).fetchone()}


def manage_experiment(database, experiment_id, *, expected_revision, action, confirmed=False, name=None, actor_user_id=None):
    if action not in {"rename", "archive", "restore", "delete"}:
        raise ValueError("Неизвестное действие с черновиком.")
    with _week_creation_lock, database.connection() as connection:
        if database.database_url:
            database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                             ("my-island:school-template-selection",))
        else:
            database.execute(connection, "BEGIN IMMEDIATE")
        suffix = " FOR UPDATE" if database.database_url else ""
        row = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?" + suffix, (experiment_id,)).fetchone()
        if not row:
            raise ExperimentNotFound(experiment_id)
        draft = _item(row)
        if draft["revision"] != expected_revision:
            raise ExperimentConflict(draft)
        payload = draft["payload"]
        if action == "rename":
            if payload.get("draft_archive"):
                raise ValueError("Сначала восстановите черновик из архива.")
            title = str(name or "").strip()
            if not title:
                raise ValueError("Введите название черновика.")
            if title == draft["name"]:
                return draft
            updated = database.execute(connection, """UPDATE schedule_experiment_drafts
                SET name=?,revision=revision+1,updated_at=?,updated_by=? WHERE id=? RETURNING *""",
                (title, datetime.now(timezone.utc).isoformat(), _draft_actor_id(database, actor_user_id), experiment_id)).fetchone()
            return _item(updated)
        links = _draft_links(database, connection, experiment_id)
        if action != "restore" and "school_template_selection" in links:
            raise ValueError("Сначала назначьте другой основной шаблон.")
        if action != "restore" and payload.get("laboratory_meetings"):
            raise ValueError("Сначала отключите лаборатории и восстановите заменённые уроки.")
        if action == "delete":
            if not confirmed or not payload.get("draft_archive"):
                raise ValueError("Сначала архивируйте запись, затем подтвердите окончательное удаление.")
            references = database.execute(connection, "SELECT payload FROM schedule_experiment_drafts WHERE id<>?", (experiment_id,)).fetchall()
            if links or any(experiment_id in json.dumps(_payload(ref["payload"])) for ref in references):
                raise ValueError("Есть связанные черновики или история. Запись можно оставить в архиве или восстановить.")
            database.execute(connection, "DELETE FROM schedule_experiment_drafts WHERE id=?", (experiment_id,))
            return {"deleted": experiment_id}
        if bool(payload.get("draft_archive")) == (action == "archive"):
            return draft
        now = datetime.now(timezone.utc).isoformat()
        if action == "archive":
            payload["draft_archive"] = {"at": now, "by": str(actor_user_id or "")}
        else:
            payload.pop("draft_archive", None)
        payload.setdefault("draft_lifecycle_history", []).append({"action": action, "at": now, "by": str(actor_user_id or "")})
        encoded = json.dumps(payload, ensure_ascii=False)
        parameter = "?::jsonb" if database.database_url else "?"
        updated = database.execute(connection, f"""UPDATE schedule_experiment_drafts
            SET payload={parameter},revision=revision+1,updated_at=?,updated_by=? WHERE id=? RETURNING *""",
            (encoded, now, _draft_actor_id(database, actor_user_id), experiment_id)).fetchone()
        return _item(updated)


def delete_experiment(database: Database, experiment_id: str) -> None:
    """Delete disposable local experiments using the same dependency checks."""
    current = get_experiment(database, experiment_id)
    if not current["payload"].get("draft_archive"):
        current = manage_experiment(database, experiment_id, expected_revision=current["revision"], action="archive")
    manage_experiment(database, experiment_id, expected_revision=current["revision"], action="delete", confirmed=True)


def save_experiment(database: Database, experiment_id: str, *, expected_revision: int, payload: Mapping[str, Any], name: str | None = None, actor_user_id: Any = None, audience_database: Database | None = None, term_comparison_key: str | None = None, _trusted_resolution_history: bool = False, _preserve_unconfirmed_rosters: bool = False, _audience_resolution: Mapping[str, Any] | None = None,
                    editor_audience_changes: list[str] | None = None) -> dict[str, Any]:
    clean = _clean_payload(payload)
    _validate_day_modes(database, clean)
    submitted_lessons = clean["lessons"]
    clean["lessons"] = validate_activity_lessons(submitted_lessons, list_activities(database, include_archived=True))
    normalized_name = str(name or "").strip() if name is not None else None
    if name is not None and not normalized_name:
        raise ValueError("name is required")
    current = get_experiment(database, experiment_id)
    if int(current["revision"]) != expected_revision:
        raise ExperimentConflict(current)
    if current["payload"].get("draft_archive"):
        raise ValueError("Восстановите черновик из архива перед редактированием.")
    previous_blueprints = {b["id"]: b for b in current["payload"].get("laboratory_blueprints", [])}
    next_blueprints = {b["id"]: b for b in clean.get("laboratory_blueprints", [])}
    active_blueprints = current["payload"].get("laboratory_meetings", {})
    if any(previous_blueprints.get(key) != next_blueprints.get(key) for key in active_blueprints):
        raise ValueError("Сначала отключите лабораторию и восстановите уроки.")
    blueprint_history = list(current["payload"].get("laboratory_blueprint_history", []))
    for key in dict.fromkeys([*previous_blueprints, *next_blueprints]):
        before, after = previous_blueprints.get(key), next_blueprints.get(key)
        if before != after:
            blueprint_history.append({"revision": expected_revision + 1, "actor": str(actor_user_id or ""),
                            "before": before, "after": after})
    for field in ("laboratory_meetings", "laboratory_history"):
        if clean.get(field) != current["payload"].get(field) and field in clean:
            raise ValueError("Измените лабораторию через предпросмотр замещения")
        if field in current["payload"]:
            clean[field] = current["payload"][field]
    if current["payload"].get("laboratory_meetings"):
        meetings = [lesson for lesson in current["payload"]["lessons"] if lesson.get("laboratoryOrigin")]
        def affected_rows(value):
            matches = {}
            for lesson in value["lessons"]:
                interval = _lesson_interval(lesson, value["slots"])
                for meeting in meetings:
                    lab = _lesson_interval(meeting, current["payload"]["slots"])
                    if (lesson.get("id") == meeting["id"] or lesson.get("day") == meeting["day"]
                            and interval and lab and interval[0] < lab[1] and lab[0] < interval[1]):
                        matches[lesson["id"]] = lesson
            return matches
        if affected_rows(clean) != affected_rows(current["payload"]) or any(
                clean.get(k) != current["payload"].get(k) for k in ("days", "slots", "grade_modes", "day_modes")):
            raise ValueError("Для изменения замещения или режима дня отключите лабораторию и проверьте новый предпросмотр")
    if "source_template" in current["payload"]:
        clean["source_template"] = current["payload"]["source_template"]
    current_week = current["payload"].get("week_start")
    if clean.get("week_start") and clean["week_start"] != current_week:
        raise ValueError("Чтобы начать работу над другой неделей, создайте отдельный черновик.")
    if current_week:
        clean["week_start"] = current_week
    from backend.services.schedule_term_comparison import retain_term_comparison
    retain_term_comparison(clean, current["payload"], record_edits=not _trusted_resolution_history,
                           review_key=term_comparison_key, actor_user_id=actor_user_id,
                           audience_database=audience_database or database)

    def lesson_key(lesson: Mapping[str, Any], index: int) -> str:
        return str(lesson.get("id") or f"index:{index}")

    previous_lessons = current["payload"].get("lessons") or []
    previous_by_key = {lesson_key(item, index): item for index, item in enumerate(previous_lessons) if isinstance(item, Mapping)}
    unchanged: set[str] = set()
    dependent: set[str] = set()
    if editor_audience_changes is not None and actor_user_id is not None:
        explicit_keys = set(editor_audience_changes)
        old_payload = current["payload"]
        submitted_by_key = {lesson_key(item, index): item for index, item in enumerate(submitted_lessons)}
        next_by_key = {lesson_key(item, index): item for index, item in enumerate(clean["lessons"])}
        old_slots, new_slots = old_payload.get("slots") or [], clean["slots"]

        def window(row, slots):
            return (_lesson_weekday(row), _lesson_interval(row, slots)) if row else (None, None)

        def authored(row):
            return {k: v for k, v in row.items() if k not in {
                "audience_snapshot", "attendance_history", "attendance_week_baseline", "edit_validation_history", "resolution_history",
            }}

        def explicit_reset(row):
            return "audience_snapshot" in row and row["audience_snapshot"] is None

        same_date = (old_payload.get("reference_date") or old_payload.get("week_start")) == (clean.get("reference_date") or clean.get("week_start"))
        unchanged = {key for key, row in submitted_by_key.items()
                     if key not in explicit_keys and key in previous_by_key and authored(row) == authored(previous_by_key[key])
                     and (not explicit_reset(row) or explicit_reset(previous_by_key[key]))
                     and same_date and window(row, new_slots) == window(previous_by_key[key], old_slots)}

        # Include old and new windows: moving/deleting a concurrent lesson can
        # also free seats in a remainder or lunch. Fixed rosters never inherit a
        # neighbor's edit; dependent rosters still use the full resolver below.
        dirty_windows: list[tuple[int | None, tuple[int, int]]] = []
        for key in (previous_by_key.keys() | next_by_key.keys()) - unchanged:
            before, after = previous_by_key.get(key), next_by_key.get(key)
            if (before and after and window(before, old_slots) == window(after, new_slots)
                    and _lesson_audience(before) == _lesson_audience(after)
                    and _lesson_activity_type(before) == _lesson_activity_type(after)
                    and _lesson_special_mark(before) == _lesson_special_mark(after)
                    and before.get("blockId") == after.get("blockId")
                    and before.get("classIds") == after.get("classIds")
                    and not (explicit_reset(after) and not explicit_reset(before))):
                continue  # A room/teacher-only edit did not change its occupancy.
            for row, slots in ((before, old_slots), (after, new_slots)):
                if row and not _is_lunch_lesson(row) and _lesson_activity_type(row) != "consultation" and _lesson_special_mark(row) != "no_lesson":
                    day, interval = window(row, slots)
                    if day is not None and interval:
                        dirty_windows.append((day, interval))
        for key in unchanged:
            row = previous_by_key[key]
            if _lesson_audience(row)["kind"] != "available_slot" and not _is_lunch_lesson(row):
                continue
            day, interval = window(row, old_slots)
            if interval and any(day == other_day and interval[0] < other[1] and other[0] < interval[1]
                                for other_day, other in dirty_windows):
                dependent.add(key)

    reset_keys: set[str] = set()
    for index, lesson in enumerate(clean["lessons"]):
        key = lesson_key(lesson, index)
        previous = previous_by_key.get(key) or {}
        if key in unchanged:
            clean["lessons"][index] = dict(previous)
            continue
        if previous.get("edit_validation_history"):
            lesson["edit_validation_history"] = previous["edit_validation_history"]
        else:
            lesson.pop("edit_validation_history", None)
        lesson.pop("attendance_week_baseline", None)
        if "attendance_week_baseline" in previous:
            lesson["attendance_week_baseline"] = previous["attendance_week_baseline"]
        history = list(previous.get("attendance_history") or [])
        lesson.pop("attendance_history", None)
        reason = str(lesson.pop("attendance_reason", "") or "").strip()
        if current_week and "attendance_week_baseline" in previous and _lesson_weekday(lesson) != _lesson_weekday(previous):
            rule = _lesson_audience(lesson)
            rule["exclude_student_ids"] = lesson.pop("attendance_week_baseline")
            lesson.update(audience=rule, audience_snapshot=None)
            reason = "Персональное исключение завершено при переносе на другую дату"
        old_exclusions = set(_lesson_audience(previous).get("exclude_student_ids") or [])
        new_exclusions = set(_lesson_audience(lesson).get("exclude_student_ids") or [])
        if previous and old_exclusions != new_exclusions:
            if not reason:
                reason = "Изменение правила состава в конструкторе"
            if current_week and "attendance_week_baseline" not in lesson:
                lesson["attendance_week_baseline"] = sorted(old_exclusions)
            history.append({"scope": current_week or "template", "at": datetime.now(timezone.utc).isoformat(), "actor": str(actor_user_id or ""),
                            "reason": reason, "excluded": sorted(new_exclusions - old_exclusions),
                            "restored": sorted(old_exclusions - new_exclusions)})
        if history:
            lesson["attendance_history"] = history
        if lesson.get("activityType") == "consultation" and not (lesson.get("teacherId") or lesson.get("teacherIds")):
            raise ValueError("Выберите преподавателя консультации")
        if not _trusted_resolution_history:
            lesson.pop('resolution_history',None)
            if previous.get('resolution_history'):
                from copy import deepcopy
                lesson['resolution_history']=deepcopy(previous['resolution_history'])
        if _lesson_activity_type(lesson) == "consultation":
            lesson.pop("audience_snapshot", None)
            continue
        if _lesson_activity_type(lesson) in {"club", "elective"}:
            # Keep historical evidence while using no personal roster.
            existing = previous.get("audience_snapshot")
            if "audience_snapshot" not in lesson and isinstance(existing, Mapping):
                lesson["audience_snapshot"] = dict(existing)
            continue
        if "audience_snapshot" in lesson and lesson["audience_snapshot"] is None:
            reset_keys.add(key)
            continue
        existing = previous.get("audience_snapshot")
        if isinstance(existing, Mapping):
            # Older frontends omit unknown fields. Keep the persisted factual
            # roster unless the client explicitly confirms a recalculation.
            lesson["audience_snapshot"] = dict(existing)
        else:
            lesson.pop("audience_snapshot", None)

    reference_date = clean.get("week_start") or clean.get("reference_date")
    reference = date.fromisoformat(str(reference_date)[:10]) if reference_date else date.today()
    resolved = (_audience_resolution.get("lessons", {}) if isinstance(_audience_resolution, Mapping)
                else experiment_week_audience_preview(audience_database or database, clean, reference)["lessons"])
    editor_feedback: list[dict[str, Any]] = []
    if editor_audience_changes is not None and actor_user_id is not None:
        resets_before_validation = set(reset_keys)
        editor_feedback = _validate_ordinary_editor_changes(
            audience_database or database, clean, previous_by_key, resolved, reference,
            set(editor_audience_changes), lesson_key, reset_keys, actor_user_id,
        )
        # The first preview deliberately includes frozen occupancy. Re-evaluate
        # dependent rosters only after a verified correction releases that old roster.
        if dependent and any(isinstance(previous_by_key[key].get("audience_snapshot"), Mapping)
                             for key in reset_keys - resets_before_validation if key in previous_by_key):
            resolved = experiment_week_audience_preview(audience_database or database, clean, reference)["lessons"]
    for index, lesson in enumerate(clean["lessons"]):
        key = lesson_key(lesson, index)
        previous = previous_by_key.get(key) or {}
        if key in unchanged and key not in dependent:
            continue
        if _lesson_activity_type(lesson) in {"club", "elective", "consultation"}:
            continue
        had_snapshot = isinstance(previous.get("audience_snapshot"), Mapping)
        explicit_reset = key in reset_keys
        if had_snapshot and not explicit_reset:
            preview = resolved.get(str(lesson.get("id") or index), {})
            old_snapshot = lesson.get("audience_snapshot")
            proposed = preview.get("proposed_student_ids")
            if (isinstance(old_snapshot, Mapping) and preview.get("status") == "ok"
                    and proposed is not None and _ids(proposed) == _ids(old_snapshot.get("student_ids"))):
                # Rebind only the context fingerprint after the exact roster was
                # checked and found unchanged. Never replace confirmed student IDs.
                lesson["audience_snapshot"] = {**dict(old_snapshot),
                                                "fingerprint": _audience_fingerprint(lesson, clean)}
            continue
        preview = resolved.get(str(lesson.get("id") or index), {})
        if explicit_reset and (preview.get("status") != "ok" or preview.get("proposed_student_ids") is None):
            raise ValueError(f"Audience for lesson {lesson.get('id') or index} cannot be recalculated until its preview is unambiguous")
        if _preserve_unconfirmed_rosters and not explicit_reset:
            lesson.pop("audience_snapshot", None)
            continue
        if preview.get("status") != "ok" or not _lesson_audience(lesson)["kind"]:
            lesson.pop("audience_snapshot", None)
            continue
        student_ids = preview.get("proposed_student_ids")
        if student_ids is None:
            student_ids = preview.get("student_ids") or []
        lesson["audience_snapshot"] = {"student_ids": _ids(student_ids),
                                       "fingerprint": _audience_fingerprint(lesson, clean),
                                       "resolved_at": datetime.now(timezone.utc).isoformat()}
    clean = _clean_payload(clean)
    # Avoid even metadata normalization on neighbors the editor did not touch.
    # Only a confirmed derived snapshot may change on a dependent lesson.
    for index, lesson in enumerate(clean["lessons"]):
        key = lesson_key(lesson, index)
        if key not in unchanged:
            continue
        preserved = dict(previous_by_key[key])
        if key in dependent and "audience_snapshot" in lesson:
            preserved["audience_snapshot"] = lesson["audience_snapshot"]
        clean["lessons"][index] = preserved
    if blueprint_history:
        clean["laboratory_blueprint_history"] = blueprint_history
    for field in ("week_variant", "draft_lifecycle_history"):
        if field in current["payload"]:
            clean[field] = current["payload"][field]
    saved = _write_experiment_revision(database, experiment_id, expected_revision, clean,
                                       normalized_name=normalized_name, actor_user_id=actor_user_id)
    if editor_feedback:
        saved["editor_validation"] = editor_feedback
    return saved


def _validate_ordinary_editor_changes(database, payload, previous_by_key, resolved, reference,
                                      explicit_ids, lesson_key, reset_keys, actor_user_id):
    from backend.services.schedule_normalization import verified_teacher_directory_evidence

    edited = []
    scope_ids = set()
    for index, lesson in enumerate(payload["lessons"]):
        key = lesson_key(lesson, index)
        previous = previous_by_key.get(key)
        if not previous:
            lesson.pop("edit_validation_history", None)
            continue
        if not lesson.get("needsClarification") or lesson.get("importError") or not previous.get("needsClarification"):
            continue
        if lesson.get("clarificationReason") != previous.get("clarificationReason"):
            continue  # This path only retires inherited notes; it never replaces a teacher-authored reason.
        type_before, type_after = _lesson_activity_type(previous), _lesson_activity_type(lesson)
        old_scope = selected_scope(_lesson_audience(previous))
        new_scope = selected_scope(_lesson_audience(lesson))
        ordinary_fields = ("subject", "room", "teacherId", "teacherIds", "day", "start", "end", "period", "format", "connectionInfo")
        if (key not in explicit_ids and old_scope == new_scope and type_before == type_after
                and all(previous.get(field) == lesson.get(field) for field in ordinary_fields)):
            continue
        clauses = {normalized_note(clause) for clause in notes(lesson.get("clarificationReason"))}
        if clauses & GROUP_LINK_NOTES and key in explicit_ids:
            scope_ids.update(new_scope[1])
        edited.append((key, lesson, previous, type_after, old_scope, new_scope, clauses))
    active_directory = read_active_scope_directory(database, scope_ids)
    feedback = []
    teachers_by_date = {}
    for key, lesson, previous, kind, old_scope, new_scope, clauses in edited:
        retired, evidence = set(), {}
        if kind == "club":
            retired.update(clauses & CLUB_LESSON_ONLY_NOTES)
            if retired:
                evidence["activity_type"] = "club"
        subject = subject_key(lesson.get("subject"))
        if clauses & GROUP_LINK_NOTES and key in explicit_ids and kind not in {"club", "elective", "event", "consultation"}:
            # Imports can supply a canonical ID and even a saved snapshot.
            # Only this authorized editor gesture verifies the inherited link.
            raw_audience = lesson.get("audience") or {}
            raw_keys = "group_ids" if new_scope[0] == "groups" else "class_ids"
            explicit_scope = (isinstance(raw_audience, Mapping) and raw_audience.get("kind") == new_scope[0]
                              and _ids(raw_audience.get(raw_keys)) == new_scope[1])
            preview = resolved.get(str(lesson.get("id")), {})
            proposed = preview.get("proposed_student_ids")
            if not (explicit_scope and scope_verified(*new_scope, active_directory)
                    and preview.get("status") == "ok" and isinstance(proposed, list)):
                raise ValueError("Новый состав не подтверждён. Выберите действующую группу или конкретные классы с известным составом и повторите сохранение.")
            retired.update(clauses & GROUP_LINK_NOTES)
            evidence["audience"] = {"before": {"kind": old_scope[0], "scope_ids": old_scope[1]},
                                    "after": {"kind": new_scope[0], "scope_ids": new_scope[1]},
                                    "participant_count": len(proposed)}
            if old_scope != new_scope:
                # An explicit new selection authorizes its resolved membership.
                reset_keys.add(key)
                lesson["audience_snapshot"] = None
            # Reaffirming the same group does NOT silently replace a previously
            # saved roster when membership has drifted; roster review stays separate.
        if clauses & {TEACHER_IDENTITY_NOTE, TEACHER_ASSIGNMENT_NOTE}:
            teacher_ids = {str(value) for value in [lesson.get("teacherId"), *(lesson.get("teacherIds") or [])] if value}
            day = _lesson_weekday(lesson)
            actual_day = reference - timedelta(days=reference.weekday()) + timedelta(days=day) if day is not None else reference
            if teacher_ids and actual_day not in teachers_by_date:
                teachers_by_date[actual_day] = verified_teacher_directory_evidence(database, {
                    "payload": {"reference_date": actual_day.isoformat()}})
            active, assignments = teachers_by_date.get(actual_day, (set(), set()))
            if teacher_ids and teacher_ids <= active:
                if TEACHER_IDENTITY_NOTE in clauses:
                    retired.add(TEACHER_IDENTITY_NOTE)
                    evidence["teacher_directory"] = "active_identity"
                if TEACHER_ASSIGNMENT_NOTE in clauses and new_scope[1] and subject:
                    matches = {(teacher, scope) for teacher, scope, name in assignments
                               if teacher in teacher_ids and scope in new_scope[1] and subject_key(name) == subject}
                    if {teacher for teacher, _ in matches} == teacher_ids and {scope for _, scope in matches} == set(new_scope[1]):
                        retired.add(TEACHER_ASSIGNMENT_NOTE)
                        evidence["teacher_directory"] = "active_subject_assignment"
        if retired and audit_resolved_notes(lesson, previous, retired, evidence, actor_user_id=actor_user_id):
            if "audience" in evidence:
                feedback.append({"lesson_id": key, "status": "audience_verified",
                                 "participant_count": evidence["audience"]["participant_count"]})
            else:
                feedback.append({"lesson_id": key, "status": "legacy_notes_resolved"})
    return feedback


def _write_experiment_revision(database: Database, experiment_id: str, expected_revision: int,
                               payload: Mapping[str, Any], *, normalized_name: str | None = None,
                               actor_user_id: Any = None) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    now = datetime.now(timezone.utc).isoformat()
    archived = "payload->'draft_archive' IS NULL" if database.database_url else "json_extract(payload, '$.draft_archive') IS NULL"
    with database.connection() as connection:
        row = database.execute(connection, f"""UPDATE schedule_experiment_drafts
            SET payload={encoded_sql},name=COALESCE(?,name),revision=revision+1,updated_by=?,updated_at=?
            WHERE id=? AND revision=? AND {archived} RETURNING *""",
            (encoded, normalized_name, _draft_actor_id(database, actor_user_id), now, experiment_id, expected_revision)).fetchone()
        if row:
            return _item(row)
        current = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?", (experiment_id,)).fetchone()
    if current:
        item = _item(current)
        if item["revision"] == expected_revision and item["payload"].get("draft_archive"):
            raise ValueError("Восстановите черновик из архива перед редактированием.")
        raise ExperimentConflict(item)
    raise ExperimentNotFound(experiment_id)
