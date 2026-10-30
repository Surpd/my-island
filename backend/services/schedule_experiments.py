from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from typing import Any, Mapping
import re
from uuid import UUID, uuid4
from threading import Lock

from backend.database import Database
from backend.services.schedule_rooms import display_room
from backend.services.canonical_schedule import canonical_version, read_canonical_template
from backend.services.schedule_activities import list_activities, validate_activity_lessons


DEFAULT_SLOTS = [
    {"start": "09:00", "end": "09:45"}, {"start": "09:55", "end": "10:40"},
    {"start": "10:55", "end": "11:40"}, {"start": "11:50", "end": "12:35"},
    {"start": "12:45", "end": "13:30"}, {"start": "13:40", "end": "14:25"},
    {"start": "14:35", "end": "15:20"}, {"start": "15:30", "end": "16:15"},
]

_school_template_open_lock = Lock()


def _canonical_time(value: Any) -> str:
    """Postgres TIME and SQLite text must address the same bell slot."""
    text = str(value or "")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::00)?", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text


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
    for key in ("school_source", "import_review", "transfer_result"):
        if isinstance(raw.get(key), (dict, list)):
            result[key] = raw[key]
    if isinstance(raw.get("source_template"), Mapping):
        result["source_template"] = dict(raw["source_template"])
    return result


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


def _lesson_audience(lesson: Mapping[str, Any]) -> dict[str, Any]:
    """Read both the current ID rule and persisted pre-rule constructor lessons."""
    # Clubs currently target teachers and time only; historical class, roster,
    # and grade eligibility fields must never turn them into student occupancy.
    if lesson.get("activityType") == "club":
        return {"kind": "students", "group_ids": [], "class_ids": [], "grade": "", "student_ids": []}
    # Electives can retain explicit participants, while grade eligibility is
    # display metadata and never expands into a whole class.
    if lesson.get("activityType") == "elective" and "confirmedParticipantIds" in lesson:
        return {"kind": "students", "group_ids": [], "class_ids": [], "grade": "",
                "student_ids": _ids(lesson.get("confirmedParticipantIds"))}
    mark = _lesson_special_mark(lesson)
    if mark == "no_lesson" or lesson.get("kind") == "event" and lesson.get("informational") is True:
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
        return {
            "kind": kind,
            "group_ids": _ids(raw.get("group_ids") or raw.get("groupIds")),
            "class_ids": _ids(raw.get("class_ids") or raw.get("classIds")) or ([] if raw.get("grade") else display_class_ids),
            # The constructor's grid column is authoritative when an audience
            # rule was saved without an explicit grade. Preserve that scope
            # instead of requiring the user to pick one administrative class.
            "grade": str(raw.get("grade") or lesson.get("grade_scope") or ""),
            "student_ids": _ids(raw.get("student_ids") or raw.get("studentIds") or raw.get("include_student_ids")),
        }
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
    if snapshot is not None:
        students = {str(row["id"]): dict(row) for row in snapshot["students"]}
        groups = {str(row["id"]): dict(row) for row in snapshot["groups"]}
        memberships = snapshot["memberships"]
        membership_overrides = snapshot["membership_overrides"]
    else:
        query_started = perf_counter()
        with database.connection() as connection:
            students = {str(row["id"]): dict(row) for row in database.execute(connection,
                "SELECT id,display_name,class_name FROM identities WHERE kind='student' AND status='active'").fetchall()}
            groups = {str(row["id"]): dict(row) for row in database.execute(connection,
                "SELECT id,name AS raw_name,display_name,COALESCE(display_name,name) AS name,group_type,base_class_name,semantic_dimension FROM groups WHERE canonical IS TRUE").fetchall()}
            memberships = database.execute(connection, """SELECT m.identity_id,m.group_id,m.valid_from,m.valid_until FROM memberships m
                JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id
                WHERE m.active IS TRUE AND m.member_role='student' AND i.kind='student'
                  AND i.status='active' AND g.canonical IS TRUE
                  AND (m.valid_from IS NULL OR m.valid_from <= ?)
                  AND (m.valid_until IS NULL OR m.valid_until >= ?)""", (reference_date.isoformat(), reference_date.isoformat())).fetchall()
            membership_overrides = database.execute(connection, """SELECT identity_id,group_id,action,valid_from,valid_until FROM membership_overrides
                WHERE active IS TRUE AND member_role='student' AND action IN ('include','exclude')
                  AND (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_until IS NULL OR valid_until >= ?)""", (reference_date.isoformat(), reference_date.isoformat())).fetchall()
        query_ms = (perf_counter() - query_started) * 1000
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

    def resolve_explicit(rule: Mapping[str, Any]) -> tuple[set[str], str | None]:
        kind = rule["kind"]
        if kind == "students":
            requested = set(rule["student_ids"])
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
            return set(), f"Не найдены подходящие canonical группы: {', '.join(wrong)}"
        can_skip_unpopulated_grade_class = ids_from_grade and any(by_group.get(item) for item in ids)
        empty = [item for item in ids if not by_group.get(item)
                 and (kind == "groups" or bool(raw_members_by_group.get(item))
                      or not can_skip_unpopulated_grade_class)]
        if empty:
            return set(), f"Неизвестен состав групп или классов: {', '.join(empty)}"
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
    for index, lesson in enumerate(lessons):
        if not isinstance(lesson, Mapping):
            raise ValueError("Each lesson must be an object")
        lesson_id = str(lesson.get("id") or index)
        rule = _lesson_audience(lesson)
        interval = _lesson_interval(lesson, slots)
        base, reason = resolve_explicit(rule)
        if not rule["kind"] and (lesson.get("kind") == "event" or _lesson_special_mark(lesson) == "no_lesson"
                                  or _is_lunch_lesson(lesson) and lesson.get("needsClarification") is True):
            reason = None  # An informational event has no assigned students.
        if rule["kind"] == "available_slot" and not interval:
            reason = "Не задан корректный временной интервал"
        optional_roster = lesson.get("activityType") == "club"
        snapshot_value = None if optional_roster else lesson.get("audience_snapshot")
        snapshot = dict(snapshot_value) if isinstance(snapshot_value, Mapping) else None
        snapshot_ids = set(_ids(snapshot.get("student_ids"))) if snapshot else set()
        states[index] = {"id": lesson_id, "lesson": lesson, "rule": rule, "interval": interval,
                         "base": base, "occupancy_base": snapshot_ids if snapshot is not None else base,
                         "snapshot": snapshot, "snapshot_ids": snapshot_ids,
                         "fingerprint": _audience_fingerprint(lesson, payload), "reason": reason}

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
            invalid_id = reason.startswith(("Не найдены подходящие canonical группы", "Ученики не найдены"))
            status = "error" if state["lesson"].get("importError") or invalid_id else "incomplete"
        excluded: set[str] = set()
        blockers: list[dict[str, Any]] = []
        is_lunch = _is_lunch_lesson(state["lesson"])
        if (rule["kind"] == "available_slot" or is_lunch) and not reason:
            for other_key, other in states.items():
                if other_key == state_key or other["lesson"].get("day") != state["lesson"].get("day"):
                    continue
                if _lesson_special_mark(other["lesson"]) in {"lunch", "no_lesson"}:
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
            "description", "place", "format", "kind", "informational",
            "activityCatalogId", "activityNameSnapshot", "eligibleGrades",
            "confirmedParticipantIds", "electiveDirection") if key in details}
        activity_type = metadata.get("activity_type")
        if activity_type in {"club", "elective", "event"}:
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
                      actor_user_id: Any = None, _base_version_id: str | None = None, _trusted_resolution_history: bool = False) -> dict[str, Any]:
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
    experiment_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    transfer_result = dict((payload or {}).get("transfer_result") or {}) if source in {"canonical", "import"} else None
    clean_payload = _clean_payload(payload)
    if not _trusted_resolution_history:
        for lesson in clean_payload['lessons']:lesson.pop('resolution_history',None)
    provenance = clean_payload.get("school_source")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    if provenance.get("role") == "primary_school_template" and not provenance.get("primary_draft_id"):
        clean_payload["school_source"] = {**provenance, "primary_draft_id": experiment_id}
    clean_payload["lessons"] = validate_activity_lessons(clean_payload["lessons"], list_activities(database, include_archived=True))
    encoded = json.dumps(_clean_payload(clean_payload), ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    with database.connection() as connection:
        row = database.execute(connection, f"""INSERT INTO schedule_experiment_drafts
            (id,name,base_version_id,revision,payload,created_by,updated_by,created_at,updated_at)
            VALUES (?,?,?,1,{encoded_sql},?,?,?,?) RETURNING *""",
            (experiment_id, normalized_name, base_version_id, encoded, _draft_actor_id(database, actor_user_id), _draft_actor_id(database, actor_user_id), now, now)).fetchone()
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
    payload = _canonical_payload(database, version_id, source_blocks=blocks)
    payload.update({"draft_kind": "school", "week_start": str(row["week_start"]),
                    "reference_date": str(row["week_start"]), "school_source": {
                        "kind": "effective_week", "effective_week_id": effective_week_id, "canonical_version_id": version_id}})
    return create_experiment(database, name=f"Неделя {row['week_start']} · черновик исторической версии", source="import",
                             payload=payload, actor_user_id=actor_user_id, _base_version_id=version_id)


def duplicate_experiment(database: Database, experiment_id: str, *, name: str | None = None, actor_user_id: Any = None) -> dict[str, Any]:
    source = get_experiment(database, experiment_id)
    normalized_name = str(name or f"{source['name']} — копия").strip()
    if not normalized_name:
        raise ValueError("name is required")
    experiment_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    duplicate_payload = dict(source["payload"])
    if duplicate_payload.get("draft_kind") == "demo":
        duplicate_payload["draft_kind"] = "user"
    encoded = json.dumps(_clean_payload(duplicate_payload), ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    with database.connection() as connection:
        row = database.execute(connection, f"""INSERT INTO schedule_experiment_drafts
            (id,name,base_version_id,revision,payload,created_by,updated_by,created_at,updated_at)
            VALUES (?,?,?,1,{encoded_sql},?,?,?,?) RETURNING *""",
            (experiment_id, normalized_name, source.get("base_version_id"), encoded, _draft_actor_id(database, actor_user_id), _draft_actor_id(database, actor_user_id), now, now)).fetchone()
    return _item(row)


def create_experiment_week(database: Database, experiment_id: str, *, week_start: str,
                           expected_revision: int, actor_user_id: Any = None) -> dict[str, Any]:
    """Create a detached week draft copied from a template at a known revision."""
    start = date.fromisoformat(str(week_start))
    if start.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    source = get_experiment(database, experiment_id)
    if int(source["revision"]) != expected_revision:
        raise ExperimentConflict(source)
    payload = json.loads(json.dumps(source["payload"], ensure_ascii=False))
    payload["week_start"] = start.isoformat()
    payload["reference_date"] = start.isoformat()
    payload["source_template"] = {"id": source["id"], "revision": int(source["revision"])}
    for original, copied in zip(source["payload"].get("lessons") or [], payload.get("lessons") or []):
        snapshot = copied.get("audience_snapshot")
        if isinstance(snapshot, dict) and snapshot.get("fingerprint") == _audience_fingerprint(original, source["payload"]):
            # A trusted date-only copy preserves confirmed participants. Rebind
            # the rule fingerprint, never recompute/fill the roster here. A
            # changed target-week membership still requires explicit review.
            snapshot["fingerprint"] = _audience_fingerprint(copied, payload)
    return create_experiment(database, name=f"Неделя {start.isoformat()} · {source['name']}",
                             source="import", payload=payload, actor_user_id=actor_user_id,
                             _base_version_id=source.get("base_version_id"), _trusted_resolution_history=True)


def delete_experiment(database: Database, experiment_id: str) -> None:
    with database.connection() as connection:
        row = database.execute(connection, "DELETE FROM schedule_experiment_drafts WHERE id=? RETURNING id", (experiment_id,)).fetchone()
    if not row:
        raise ExperimentNotFound(experiment_id)


def save_experiment(database: Database, experiment_id: str, *, expected_revision: int, payload: Mapping[str, Any], name: str | None = None, actor_user_id: Any = None, audience_database: Database | None = None, _trusted_resolution_history: bool = False, _preserve_unconfirmed_rosters: bool = False, _audience_resolution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    clean = _clean_payload(payload)
    clean["lessons"] = validate_activity_lessons(clean["lessons"], list_activities(database, include_archived=True))
    normalized_name = str(name or "").strip() if name is not None else None
    if name is not None and not normalized_name:
        raise ValueError("name is required")
    current = get_experiment(database, experiment_id)
    if int(current["revision"]) != expected_revision:
        raise ExperimentConflict(current)

    def lesson_key(lesson: Mapping[str, Any], index: int) -> str:
        return str(lesson.get("id") or f"index:{index}")

    previous_lessons = current["payload"].get("lessons") or []
    previous_by_key = {lesson_key(item, index): item for index, item in enumerate(previous_lessons) if isinstance(item, Mapping)}
    reset_keys: set[str] = set()
    for index, lesson in enumerate(clean["lessons"]):
        key = lesson_key(lesson, index)
        previous = previous_by_key.get(key) or {}
        if not _trusted_resolution_history:
            lesson.pop('resolution_history',None)
            if previous.get('resolution_history'):
                from copy import deepcopy
                lesson['resolution_history']=deepcopy(previous['resolution_history'])
        if lesson.get("activityType") == "club":
            # Preserve historical snapshots, but they are excluded from club
            # occupancy and must not be recalculated as student audiences.
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

    resolved = (_audience_resolution.get("lessons", {}) if isinstance(_audience_resolution, Mapping)
                else experiment_audience_preview(audience_database or database, clean)["lessons"])
    for index, lesson in enumerate(clean["lessons"]):
        key = lesson_key(lesson, index)
        previous = previous_by_key.get(key) or {}
        if lesson.get("activityType") == "club":
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
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    encoded_sql = "?::jsonb" if database.database_url else "?"
    now = datetime.now(timezone.utc).isoformat()
    with database.connection() as connection:
        row = database.execute(connection, f"""UPDATE schedule_experiment_drafts
            SET payload={encoded_sql},name=COALESCE(?,name),revision=revision+1,updated_by=?,updated_at=?
            WHERE id=? AND revision=? RETURNING *""",
            (encoded, normalized_name, _draft_actor_id(database, actor_user_id), now, experiment_id, expected_revision)).fetchone()
        if row:
            return _item(row)
        current = database.execute(connection, "SELECT * FROM schedule_experiment_drafts WHERE id=?", (experiment_id,)).fetchone()
    if current:
        raise ExperimentConflict(_item(current))
    raise ExperimentNotFound(experiment_id)
