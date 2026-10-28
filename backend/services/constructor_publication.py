"""Explicit, transactional publication of saved constructor drafts.

No Google writes or authored-payload mutations: the adapter creates immutable
canonical/effective artifacts and advances a school-local publication pointer.
Explicit template publication advances the author's base/revision atomically.
Schema installation is an explicit deployment concern, never a preview side effect.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from backend.services import canonical_schedule as canonical
from backend.services.schedule_experiments import (
    get_experiment, experiment_audience_preview, _lesson_audience, _lesson_interval, _audience_fingerprint, _canonical_time,
)


PUBLICATION_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS schedule_publication_heads (
        scope_key TEXT PRIMARY KEY, published_id TEXT NOT NULL,
        version_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS schedule_constructor_publications (
        id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, draft_revision INTEGER NOT NULL,
        scope_kind TEXT NOT NULL CHECK(scope_kind IN ('template','week')),
        scope_key TEXT NOT NULL, base_version_id TEXT, previous_published_id TEXT,
        published_id TEXT NOT NULL, version_id TEXT NOT NULL,
        payload_fingerprint TEXT NOT NULL, actor_user_id TEXT,
        created_at TEXT NOT NULL, metadata TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS constructor_publications_scope_idx ON schedule_constructor_publications(scope_key,created_at)",
)


class PublicationConflict(ValueError):
    """The saved draft, directory, base, or publication head changed."""


class PublicationBlocked(ValueError):
    def __init__(self, preview: dict[str, Any]):
        self.preview = preview
        super().__init__("Publication requires resolution of preview blockers")


class _TransactionDatabase:
    """Keep existing canonical services inside the caller's one transaction."""
    autocommit = False

    def __init__(self, database: Any, connection: Any):
        self._database, self._connection = database, connection
        self.database_url = database.database_url

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)

    @contextmanager
    def connection(self):
        yield self._connection


def initialize_publication_schema(database: Any) -> None:
    """Explicit local fixture setup; production migrations must be approved."""
    with database.connection() as connection:
        for statement in PUBLICATION_SCHEMA:
            database.execute(connection, statement)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def _scope(scope_kind: str, week_start: str | None) -> str:
    if scope_kind == "template":
        return "template"
    if scope_kind != "week" or not week_start:
        raise ValueError("scope_kind must be template or week with week_start")
    selected = date.fromisoformat(week_start)
    if selected.weekday() != 0:
        raise ValueError("week_start must be Monday")
    return f"week:{selected.isoformat()}"


def _semantic(block: Mapping[str, Any]) -> dict[str, Any]:
    """Compare meaning without generated persistence ids and provenance."""
    slot = block.get("slot") or {}
    return {"weekday": block.get("weekday"),
            "start": _canonical_time(block.get("start_time") or slot.get("start")) or None,
            "end": _canonical_time(block.get("end_time") or slot.get("end")) or None,
            "grade": str(block.get("grade_scope") or ""),
            "assignments": [{"activity": a.get("activity"),
                "role": a.get("assignment_kind") or a.get("role") or "primary",
                "groups": sorted(str(i) for i in a.get("canonical_group_ids") or (a.get("audience") or {}).get("canonical_group_ids") or []),
                "teachers": sorted(str(i) for i in a.get("teacher_ids") or []),
                "metadata": {"room": str((a.get("metadata") or {}).get("room") or ""),
                             "note": str((a.get("metadata") or {}).get("note") or ""),
                             "activity_type": (a.get("metadata") or {}).get("activity_type") or "lesson",
                             "audience_coverage": (a.get("metadata") or {}).get("audience_coverage"),
                             "joint_activity_id": (a.get("metadata") or {}).get("joint_activity_id"),
                             "special_mark": (a.get("metadata") or {}).get("special_mark"),
                             "constructor_details": {k: v for k, v in ((a.get("metadata") or {}).get("constructor_details") or {}).items()
                                                     if k != "audience_snapshot"},
                             "audience_rule": (a.get("metadata") or {}).get("audience_rule") or
                                 {"kind": "groups", "group_ids": sorted(str(i) for i in a.get("canonical_group_ids") or (a.get("audience") or {}).get("canonical_group_ids") or [])}}}
                for a in block.get("assignments") or []]}


def _directory_fingerprint(database: Any) -> str:
    # A membership edit between preview and confirm invalidates the token even
    # when the affected assignment happened to have no visible count change.
    with database.connection() as connection:
        data = {table: [dict(row) for row in database.execute(connection, query).fetchall()]
                for table, query in {
                    "identities": "SELECT id,kind,status,class_name,display_name FROM identities ORDER BY id",
                    "groups": "SELECT * FROM groups ORDER BY id",
                    "memberships": "SELECT * FROM memberships ORDER BY id",
                    "overrides": "SELECT * FROM membership_overrides ORDER BY id",
                }.items()}
    return _hash(data)


def _change_items(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"block_key": key,
             "operation": "add" if key not in before else "remove" if key not in after else "change",
             "before": _semantic(before[key]) if key in before else None,
             "after": _semantic(after[key]) if key in after else None}
            for key in sorted(before.keys() | after.keys())
            if key not in before or key not in after or _semantic(before[key]) != _semantic(after[key])]


_ISSUE_MESSAGES = {
    "teacher_assignment_unverified": "Нагрузка преподавателя не подтверждена отдельно; сохранено явно указанное назначение",
    "lunch_scope_advisory": "Обед показан как информационная отметка указанного класса; состав отдельной группы не подтверждён",
    "joint_activity": "Совместное занятие: совпадают предмет, преподаватели, точное время и кабинет; исходные аудитории сохранены",
    "joint_activity_unconfirmed": "Возможно совместное занятие, но общий кабинет не подтверждён; нельзя автоматически объединить назначения преподавателя",
    "review_required": "Занятие требует проверки",
    "audience_unresolved": "Состав участников не подтверждён или изменился",
    "unknown_teacher": "Указанный преподаватель отсутствует или неактивен",
    "student_overlap": "У одних учеников одновременно несколько занятий",
    "teacher_overlap": "Преподаватель одновременно назначен на несколько занятий",
    "unsupported_partial_cohort": "Нельзя безопасно определить оставшуюся часть параллели",
    "missing_runtime_grade": "Не определена параллель участников",
    "empty_activity": "Не указано название занятия",
    "unknown_class": "Класс не подтверждён в школьном справочнике",
    "missing_special_scope": "Не определена аудитория школьного события",
    "duplicate_lesson_id": "Повторяется идентификатор записи: структура неоднозначна",
    "missing_lesson_id": "У записи отсутствует стабильный идентификатор",
    "missing_origin_block": "Исходный canonical-блок отсутствует в выбранной базе",
    "invalid_slot": "Неверный день или интервал времени",
    "protected": "Изменение защищённого занятия требует отдельного разрешения",
    "protected_base": "Нельзя изменить или исключить защищённый canonical-блок",
    "stale_base": "Базовая версия изменилась: обновите черновик перед публикацией",
    "missing_base": "Перед публикацией недели нужен опубликованный базовый шаблон",
    "projection_mismatch": "Canonical-проекция не совпадает с подтверждённым составом занятия",
}


def _class_grade(group: Mapping[str, Any]) -> str | None:
    """Resolve a class's scope by its canonical ID metadata, not a technical key."""
    for name in ("base_class_name", "display_name", "name"):
        grade = canonical._grade(str(group.get(name) or ""))
        if grade:
            return grade
    technical = re.match(r"^grade(\d{1,2}):base:", str(group.get("name") or ""))
    return technical.group(1) if technical else None


def _projection_mismatches(database: Any, blocks: Mapping[str, Any], audience: Mapping[str, Any],
                           reference: date) -> set[str]:
    """Use the real projector in memory, before any immutable artifact is written.

    In particular a removed partition must never widen a remaining audience.
    This does not materialize a week or copy directory rows to a second store.
    """
    if not blocks:
        return set()
    with database.connection() as connection:
        def rows(query):
            return [dict(r) for r in database.execute(connection, query).fetchall()]
        students = {str(r["id"]): r for r in rows("SELECT id,display_name,class_name,status FROM identities WHERE kind='student' AND status='active'")}
        groups = {str(r["id"]): r for r in rows("SELECT * FROM groups WHERE canonical IS TRUE")}
        memberships = rows("""SELECT m.*,i.status AS identity_status,i.kind AS identity_kind,g.canonical AS group_canonical
            FROM memberships m JOIN identities i ON i.id=m.identity_id JOIN groups g ON g.id=m.group_id""")
        exclusions = rows("SELECT * FROM membership_overrides WHERE active IS TRUE AND action='exclude'")
    candidates = {key: {**b, "block_key": key, "start_time": b["slot"]["start"], "end_time": b["slot"]["end"],
                        "patch": {"assignments": b["assignments"]}} for key, b in blocks.items()}
    context = canonical.StudentProjectionContext({}, students, groups, memberships, exclusions, list(candidates.values()))
    mismatches = set()
    for key, block in blocks.items():
        day = (reference + timedelta(days=block["weekday"])).isoformat()
        candidate = candidates[key]
        expected_ids = {str(sid) for a in block["assignments"] for sid in
                        (audience.get(a["metadata"]["constructor_lesson_id"]) or {}).get("student_ids") or []}
        if any(sid not in students or canonical._grade(students[sid].get("class_name")) not in str(block.get("grade_scope") or "").split(",") for sid in expected_ids):
            mismatches.add(key)  # A grade filter must never silently lose a confirmed pupil.
        for sid, student in students.items():
            if canonical._grade(student.get("class_name")) not in str(block.get("grade_scope") or "").split(","):
                continue
            expected = [a for a in block["assignments"] if sid in set(str(x) for x in
                (audience.get(a["metadata"]["constructor_lesson_id"]) or {}).get("student_ids") or [])]
            # Lunch is intentionally absent from constructor occupancy. Its
            # explicitly confirmed grid cohort still receives the lunch marker.
            membership_groups = canonical._student_memberships(context, sid, day)
            expected += [a for index, a in enumerate(block["assignments"]) if a["metadata"].get("special_mark") == "lunch"
                         and canonical._other_assignment_occupies_slot(context, student, membership_groups, a, str(block["grade_scope"]))
                         and not canonical._student_busy_in_slot(database, context, student, membership_groups, candidate, index, exact_intervals=True, include_available_slot=True)]
            projected, _ = canonical._project_student_block(database, context, student, candidate, day)
            expected_activities = [a for a in expected if a["activity"] != "NO_LESSON"]
            if projected["state"] == "ACTIVITY":
                matches = [a for a in expected_activities if a["activity"] == projected.get("activity")
                           and set(a["teacher_ids"]) == set(str(t) for t in projected.get("teacher_ids") or [])]
                if not matches:
                    mismatches.add(key)
            elif projected["state"] == "UNRESOLVED" or expected_activities:
                mismatches.add(key)
    return mismatches


def _compile(database: Any, payload: Mapping[str, Any], base: Mapping[str, Any] | None,
             scope_kind: str, reference_week: str | None = None) -> tuple[dict[str, Any], list, list]:
    blockers: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    lessons = payload.get("lessons") or []
    if not isinstance(lessons, list):
        raise ValueError("lessons must be a list")
    if any(not isinstance(lesson, Mapping) for lesson in lessons):
        raise ValueError("Each lesson must be an object")
    # Older canonical-to-constructor copies omit newer descriptive/activity
    # fields. Recover their persisted meaning for this adapter without editing
    # the saved draft or weakening explicit fields authored by the user.
    payload = deepcopy(dict(payload))
    lessons = payload["lessons"]
    for lesson in lessons:
        origin_metadata = canonical._assignment_metadata(((lesson.get("canonical") or {}).get("assignment") or {}).get("metadata") or {})
        for name, value in (origin_metadata.get("constructor_details") or {}).items():
            if name != "audience_snapshot" and name not in lesson:
                lesson[name] = deepcopy(value)
        if "activityType" not in lesson and origin_metadata.get("activity_type") not in {None, "lesson"}:
            lesson["activityType"] = origin_metadata["activity_type"]
        if "specialMark" not in lesson and origin_metadata.get("special_mark"):
            lesson["specialMark"] = origin_metadata["special_mark"]
    reference = date.fromisoformat(str(reference_week or payload.get("reference_date") or payload.get("week_start") or date.today())[:10])
    reference -= timedelta(days=reference.weekday())
    # Membership validity is evaluated on each lesson's actual calendar day,
    # including changes that take effect in the middle of the selected week.
    by_day = {int(l.get("day", l.get("weekday", -1))) for l in lessons
              if isinstance(l, Mapping) and str(l.get("day", l.get("weekday", -1))).isdigit()}
    audience = {}
    for day in by_day:
        day_payload = deepcopy(dict(payload))
        day_payload["reference_date"] = (reference + timedelta(days=day)).isoformat()
        for original, evaluated in zip(lessons, day_payload["lessons"]):
            snapshot = evaluated.get("audience_snapshot")
            if snapshot and snapshot.get("fingerprint") == _audience_fingerprint(original, payload):
                # Advancing the evaluation date alone does not invalidate the
                # user's confirmed rule; changed actual memberships still do.
                snapshot["fingerprint"] = _audience_fingerprint(evaluated, day_payload)
        day_result = experiment_audience_preview(database, day_payload)["lessons"]
        audience.update({str(l.get("id") or i): day_result[str(l.get("id") or i)] for i, l in enumerate(lessons)
                         if l.get("day", l.get("weekday")) == day})
    with database.connection() as connection:
        teachers = {str(r["id"]): str(r["display_name"]) for r in database.execute(connection,
            "SELECT id,display_name FROM identities WHERE kind='teacher' AND status='active'").fetchall()}
        groups = {str(r["id"]): dict(r) for r in database.execute(connection,
            "SELECT id,name,display_name,group_type,base_class_name FROM groups WHERE canonical IS TRUE").fetchall()}
        students = {str(r["id"]): str(r["class_name"] or "") for r in database.execute(connection,
            "SELECT id,class_name FROM identities WHERE kind='student' AND status='active'").fetchall()}
    blocks: dict[str, Any] = {}
    ids: set[str] = set()
    occupancy: list[dict[str, Any]] = []
    lesson_keys: dict[str, str] = {}
    contexts: dict[str, dict[str, Any]] = {}
    origin_keys: dict[str, str] = {}
    for index, raw in enumerate(lessons):
        lesson = dict(raw)
        lesson_id = str(lesson.get("id") or index)
        def block(code: str, message: str) -> None:
            blockers.append({"code": code, "lesson_id": lesson_id, "message": message})
        if not lesson.get("id"):
            block("missing_lesson_id", "Stable lesson id required")
        if lesson_id in ids:
            block("duplicate_lesson_id", "Duplicate lesson id")
        ids.add(lesson_id)
        origin = lesson.get("canonical") or {}
        origin_key = str(origin.get("block_key") or "")
        origin_keys[lesson_id] = origin_key
        origin_block = (base or {}).get("blocks", {}).get(origin_key)
        if origin_key and not origin_block:
            block("missing_origin_block", "Canonical origin block missing")
        needs_review = any(lesson.get(k) for k in ("review_required", "needsClarification", "importError", "unresolved"))
        if lesson.get("deferred"):
            block("explicit_deferred", "Запись явно отложена администратором")
        result = audience.get(lesson_id) or {}
        if result.get("status") != "ok" or result.get("needs_recalculation"):
            block("audience_unresolved", str(result.get("reason") or "Audience requires confirmation or recalculation"))
        interval = _lesson_interval(lesson, payload.get("slots") or [])
        try:
            day = int(lesson.get("day", lesson.get("weekday", -1)))
        except (ValueError, TypeError):
            day = -1
        if day not in range(7) or interval is None:
            block("invalid_slot", "Valid weekday and time interval required")
            continue
        subject = str(lesson.get("subject") or lesson.get("activity") or "").strip()
        special = lesson.get("specialMark")
        if not subject and special not in {"lunch", "no_lesson"}:
            block("empty_activity", "Activity name is required")
        teacher_ids = sorted({str(x) for x in (lesson.get("teacherIds") or []) + ([lesson["teacherId"]] if lesson.get("teacherId") else [])})
        review_reason = str(lesson.get("clarificationReason") or "Занятие требует проверки")
        display_classes = lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else [])
        if needs_review:
            # Imported staffing checks do not make an explicit student audience
            # ambiguous. Keep the diagnostic without inventing any teacher.
            if review_reason == "Не подтверждён преподаватель" and not teacher_ids:
                pass  # The missing_teacher diagnostic below records this once.
            elif (review_reason == "Нет подтверждённого назначения преподавателя группе"
                  and teacher_ids and not set(teacher_ids) - teachers.keys()):
                blockers.append({"code": "teacher_assignment_unverified", "lesson_id": lesson_id, "severity": "info"})
            elif (special == "lunch" and review_reason == "Уточните охват обеда: класс или отдельная группа"
                  and display_classes and all(str(g) in groups and groups[str(g)]["group_type"] == "class" for g in display_classes)):
                blockers.append({"code": "lunch_scope_advisory", "lesson_id": lesson_id, "severity": "info"})
            else:
                block("review_required", review_reason)
        if set(teacher_ids) - teachers.keys():
            block("unknown_teacher", "An assigned teacher is missing or inactive")
        if not teacher_ids and not special and not (lesson.get("kind") == "event" and lesson.get("informational") is True):
            blockers.append({"code": "missing_teacher", "lesson_id": lesson_id,
                             "message": "Преподаватель занятия не подтверждён", "severity": "info"})
        if special not in {"lunch", "no_lesson"} and not (lesson.get("kind") == "event" and lesson.get("informational") is True):
            occupancy.append({"id": lesson_id, "day": day, "interval": interval,
                              "teachers": set(teacher_ids), "students": set(result.get("student_ids") or []),
                              "subject": subject, "room": str(lesson.get("room") or "").strip()})
        rule = _lesson_audience(lesson)
        grade = str(rule.get("grade") or lesson.get("grade_scope") or "")
        class_ids = rule.get("class_ids") or []
        display_class_ids = lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else [])
        if not grade:
            grades = {_class_grade(groups[g]) for g in class_ids or display_class_ids if g in groups}
            grades.discard(None)
            grade = ",".join(sorted(grades, key=int))
        if not grade:
            grades = {m.group(1) for sid in result.get("student_ids") or []
                      for m in [re.match(r"\s*(\d+)", students.get(str(sid), ""))] if m}
            grade = ",".join(sorted(grades, key=int))
        if not grade and result.get("student_ids"):
            block("missing_runtime_grade", "Student audience has no canonical grade scope")
        group_ids = list(rule.get("group_ids") or [])
        audience_rule: dict[str, Any]
        kind = rule.get("kind")
        if kind in {"groups", "classes"}:
            group_ids = group_ids if kind == "groups" else class_ids
            audience_rule = {"kind": "groups", "group_ids": group_ids}
            if not group_ids and grade:
                audience_rule = {"kind": "whole_grade", "grade": grade}
        elif kind in {"available_slot", "legacy_remaining"}:
            audience_rule = {"kind": "available_slot" if kind == "available_slot" else "remaining", "grade": grade,
                             "partition_group_ids": rule.get("partition_group_ids") or [],
                             "partition_dimension": rule.get("partition_dimension") or ""}
            if len(class_ids) == 1:
                audience_rule["class_group_id"] = class_ids[0]
            elif len(class_ids) > 1:
                matching = {g for g, r in groups.items() if r["group_type"] == "class" and _class_grade(r) == grade}
                if set(class_ids) != matching:
                    block("unsupported_partial_cohort", "Remaining audience spans only part of a parallel; confirm an explicit participant list")
        elif kind == "students":
            audience_rule = {"kind": "students", "include_student_ids": rule.get("student_ids") or []}
        elif lesson.get("kind") == "event" and lesson.get("informational") is True:
            audience_rule = {"kind": "students", "include_student_ids": []}
        else:
            # Informational events, lunch and explicit no-lesson have a grid
            # cohort but are never silently assigned to an arbitrary class.
            display_classes = lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else [])
            if any(str(g) not in groups for g in display_classes):
                block("unknown_class", "Special marker has an unknown canonical class")
            group_ids = [str(g) for g in display_classes if str(g) in groups]
            audience_rule = {"kind": "groups", "group_ids": group_ids}
            if not group_ids and grade:
                audience_rule = {"kind": "whole_grade", "grade": grade}
            elif not group_ids and not grade and special:
                block("missing_special_scope", "Lunch/no-lesson requires a canonical class or parallel")
        for key in ("include_student_ids", "exclude_student_ids"):
            if rule.get(key):
                audience_rule[key] = rule[key]
        start, end = (f"{v // 60:02d}:{v % 60:02d}" for v in interval)
        original = origin_block or {}
        original_slot = original.get("slot") or {}
        same_slot = (original.get("weekday"), _canonical_time(original.get("start_time") or original_slot.get("start")), _canonical_time(original.get("end_time") or original_slot.get("end")), str(original.get("grade_scope") or "")) == (day, start, end, grade)
        # Newly authored rows have no canonical origin. Distinct administrative
        # class scopes in the same parallel are separate units, not one enormous
        # block. Shared origins remain atomic; real audience/teacher collisions
        # and residual projection dependencies are still checked across units.
        class_scope = sorted({str(g) for g in class_ids or display_class_ids
                              if str(g) in groups and groups[str(g)]["group_type"] == "class"})
        key = origin_key if same_slot else "constructor-" + _hash([day, start, end, grade, class_scope])[:24]
        lesson_keys[lesson_id] = key
        contexts[lesson_id] = {"day": day, "start": start, "end": end, "subject": subject or "Обед", "grade_scope": grade,
            "class_ids": display_class_ids, "assignment_kind": kind or special or "unspecified",
            "teachers": [teachers[t] for t in teacher_ids if t in teachers],
            "groups": [str(groups[g]["name"]) for g in (group_ids or class_ids or display_class_ids) if g in groups]}
        if not str(lesson.get("room") or "").strip():
            blockers.append({"code": "missing_room", "lesson_id": lesson_id,
                             "message": "Кабинет не указан", "severity": "info"})
        role = "explicit_no_lesson" if special == "no_lesson" else "residual" if special == "lunch" or kind in {"available_slot", "legacy_remaining"} else "primary"
        preserved_metadata = deepcopy(canonical._assignment_metadata((origin.get("assignment") or {}).get("metadata") or {}))
        for duplicate in ("id", "block_id", "ordinal", "assignment_kind", "audience_kind",
                          "canonical_group_ids", "teacher_ids", "audience", "source_cells", "joint_activity_id"):
            preserved_metadata.pop(duplicate, None)
        # Retain authored descriptive/activity fields separately from audience
        # rules; no UI preference or teacher-color field enters lesson payloads.
        constructor_details = {**deepcopy(preserved_metadata.get("constructor_details") or {}), **{k: deepcopy(lesson[k]) for k in (
            "description", "place", "format", "activityCatalogId", "activityNameSnapshot",
            "eligibleGrades", "confirmedParticipantIds", "electiveDirection", "audience_snapshot",
            "kind", "informational") if k in lesson}}
        assignment = {"activity": "NO_LESSON" if special == "no_lesson" else subject or "Обед",
                      "role": role, "audience": {"type": "canonical_groups", "canonical_group_ids": group_ids},
                      "canonical_group_ids": group_ids, "teacher_ids": teacher_ids,
                      "source_cells": deepcopy((origin.get("assignment") or {}).get("source_cells") or []),
                      "metadata": {**preserved_metadata, "constructor_details": constructor_details,
                                   "room": str(lesson.get("room") or ""), "note": str(lesson.get("note") or ""),
                                   "audience_rule": audience_rule, "manual_override": True,
                                   "audience_coverage": "explicit",
                                   "teachers": [teachers[t] for t in teacher_ids if t in teachers],
                                   "constructor_lesson_id": lesson_id,
                                   "activity_type": lesson.get("activityType") or "lesson",
                                   "special_mark": special}}
        if lesson.get("protected"):
            original_assignment = origin.get("assignment") or {}
            before_assignment = {**original, "assignments": [original_assignment]}
            after_assignment = {"weekday": day, "slot": {"start": start, "end": end},
                                "grade_scope": grade, "assignments": [assignment]}
            if not origin_block or _semantic(before_assignment) != _semantic(after_assignment):
                block("protected", "Protected lesson requires separate approval before publishing a replacement")
        target = blocks.setdefault(key, {"weekday": day, "slot": {"start": start, "end": end},
            "grade_scope": grade, "status": "resolved", "mode": "SINGLE", "assignments": []})
        target["assignments"].append(assignment)
        if len(target["assignments"]) > 1:
            target["mode"] = "PARALLEL"
    joint_rows: dict[str, list[str]] = {}
    for row in occupancy:
        if row["teachers"] and row["room"]:
            joint_id = _hash([row["day"],row["interval"],row["subject"],sorted(row["teachers"]),row["room"]])
            joint_rows.setdefault(joint_id, []).append(row["id"])
    joint_by_lesson = {lid: joint for joint,lids in joint_rows.items() if len(lids)>1 for lid in lids}
    for joint,lids in joint_rows.items():
        if len(lids)>1:
            blockers.append({"code":"joint_activity","severity":"info","lesson_id":lids[0],"related_lesson_ids":lids})
    for value in blocks.values():
        for assignment in value["assignments"]:
            lid = assignment["metadata"]["constructor_lesson_id"]
            if lid in joint_by_lesson:
                assignment["metadata"]["joint_activity_id"] = joint_by_lesson[lid]
    def connected_units(keys: set[str]) -> set[str]:
        while True:
            origins = {origin_keys[lid] for lid,key in lesson_keys.items() if key in keys and origin_keys[lid]}
            joints = {joint_by_lesson[lid] for lid,key in lesson_keys.items() if key in keys and lid in joint_by_lesson}
            expanded = keys | {key for lid,key in lesson_keys.items() if origin_keys[lid] in origins or joint_by_lesson.get(lid) in joints}
            if expanded == keys:
                return keys
            keys = expanded
    for index, left in enumerate(occupancy):
        for right in occupancy[index + 1:]:
            if (left["day"] != right["day"] or left["interval"][0] >= right["interval"][1]
                    or right["interval"][0] >= left["interval"][1]):
                continue
            # Duplicate source cells may omit the teacher/room on one copy.
            # Only compatible copies of the exact same roster are equivalent;
            # equal subject text alone must not hide two teachers or rooms.
            if (left["subject"] == right["subject"] and lesson_keys.get(left["id"]) == lesson_keys.get(right["id"])
                    and left["students"] == right["students"]
                    and (not left["teachers"] or not right["teachers"] or left["teachers"] == right["teachers"])
                    and (not left["room"] or not right["room"] or left["room"] == right["room"])):
                continue
            if left["id"] in joint_by_lesson and joint_by_lesson.get(left["id"]) == joint_by_lesson.get(right["id"]):
                continue
            if left["students"] & right["students"]:
                blockers.append({"code": "student_overlap", "lesson_id": right["id"], "related_lesson_ids": [left["id"], right["id"]],
                                 "message": f"Conflicting student audience overlaps lesson {left['id']}"})
            if left["teachers"] & right["teachers"]:
                code = "joint_activity_unconfirmed" if left["subject"] == right["subject"] and left["interval"] == right["interval"] and left["teachers"] == right["teachers"] and (not left["room"] or not right["room"]) else "teacher_overlap"
                blockers.append({"code": code, "lesson_id": right["id"], "related_lesson_ids": [left["id"], right["id"]],
                                 "message": f"Teacher is assigned to simultaneous different activities ({left['id']})"})
    # Defer complete destination blocks and all pieces of a moved origin block.
    # Both sides of a collision are excluded; ordering never picks a winner.
    critical_codes = {"duplicate_lesson_id", "missing_lesson_id", "missing_origin_block", "invalid_slot", "protected", "protected_base"}
    excluded: set[str] = set()
    for issue in blockers:
        issue.setdefault("severity", "critical" if issue["code"] in critical_codes else "conflict")
        lid = issue.get("lesson_id")
        issue["context"] = contexts.get(lid, {})
        issue["block_key"] = lesson_keys.get(lid)
        if issue["severity"] == "conflict":
            excluded.update(lesson_keys[i] for i in issue.get("related_lesson_ids", [lid]) if i in lesson_keys)
    changed = True
    while changed:
        changed = False
        connected = connected_units(excluded)
        if connected != excluded:
            excluded = connected
            changed = True
        # A missing cohort cannot safely identify a smaller parallel unit.
        unknown_slots = {(blocks[k]["weekday"], blocks[k]["slot"]["start"], blocks[k]["slot"]["end"])
                         for k in excluded if not blocks[k].get("grade_scope")}
        for key, value in blocks.items():
            if (value["weekday"], value["slot"]["start"], value["slot"]["end"]) in unknown_slots and key not in excluded:
                excluded.add(key)
                changed = True
    for lid, key in sorted(lesson_keys.items()):
        if key in excluded:
            connected_keys = connected_units({key})
            causes = [issue for issue in blockers if issue["severity"]=="conflict" and
                      any(lesson_keys.get(i) in connected_keys for i in issue.get("related_lesson_ids",[issue.get("lesson_id")]))]
            if not causes:
                slot = (blocks[key]["weekday"],blocks[key]["slot"]["start"],blocks[key]["slot"]["end"])
                unknown_keys = {k for k in excluded if not blocks[k].get("grade_scope") and
                                (blocks[k]["weekday"],blocks[k]["slot"]["start"],blocks[k]["slot"]["end"])==slot}
                causes = [issue for issue in blockers if issue["severity"]=="conflict" and issue.get("block_key") in unknown_keys]
            deferred.append({"lesson_id": lid, "block_key": key, "action": "excluded", "context": contexts[lid],
                "cause_codes": sorted({i["code"] for i in causes}),
                "cause_lesson_ids": sorted({i for issue in causes for i in issue.get("related_lesson_ids",[issue.get("lesson_id")]) if i}),
                "reason": "Весь связанный блок исключён из публикации; исходная запись остаётся в черновике"})
    for issue in blockers:
        issue["action"] = "excluded" if issue.get("block_key") in excluded else "blocked" if issue["severity"] == "critical" else "published"
    blocks = {key: value for key, value in blocks.items() if key not in excluded}
    invalid_projections: set[str] = set()
    while blocks:
        invalid = _projection_mismatches(database, blocks, audience, reference)
        if not invalid:
            break
        invalid = connected_units(invalid) & blocks.keys()
        invalid_projections.update(invalid)
        blocks = {key: value for key, value in blocks.items() if key not in invalid}
    for lid, key in sorted(lesson_keys.items()):
        if key in invalid_projections:
            blockers.append({"code": "projection_mismatch", "severity": "conflict", "lesson_id": lid,
                             "block_key": key, "context": contexts[lid], "action": "excluded"})
            deferred.append({"lesson_id": lid, "block_key": key, "context": contexts[lid], "action": "excluded",
                             "cause_codes":["projection_mismatch"], "cause_lesson_ids":[lid],
                             "reason": "Весь блок исключён: проверка canonical-проекции не пройдена"})
    blocks = {key: value for key, value in blocks.items() if key not in invalid_projections}
    # Never infer permission to remove a protected canonical block from its
    # absence in the saved constructor payload.
    for key, old in (base or {}).get("blocks", {}).items():
        if (old.get("status") == "protected" or (old.get("evidence") or {}).get("protected")
                or any((a.get("metadata") or {}).get("protected") for a in old.get("assignments") or [])):
            if key not in blocks or _semantic(old) != _semantic(blocks[key]):
                blockers.append({"code": "protected_base", "severity": "critical", "block_key": key,
                    "context": {"day": old.get("weekday"), "start": _canonical_time(old.get("start_time") or (old.get("slot") or {}).get("start")),
                        "end": _canonical_time(old.get("end_time") or (old.get("slot") or {}).get("end")),
                        "subject": ", ".join(str(a.get("activity") or "") for a in old.get("assignments") or [])},
                    "action": "blocked", "message": f"Protected block {key} cannot be changed"})
    return blocks, blockers, deferred


def _artifact_blocks(blocks: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in blocks.items():
        block = deepcopy(value)
        block["slot"] = {"start": _canonical_time(block.get("start_time") or (block.get("slot") or {}).get("start")),
                         "end": _canonical_time(block.get("end_time") or (block.get("slot") or {}).get("end"))}
        block["assignments"] = [{"activity": a.get("activity"),
            "role": a.get("assignment_kind") or a.get("role") or "primary",
            "canonical_group_ids": a.get("canonical_group_ids") or (a.get("audience") or {}).get("canonical_group_ids") or [],
            "teacher_ids": a.get("teacher_ids") or [], "source_cells": a.get("source_cells") or [],
            "metadata": canonical._assignment_metadata(a.get("metadata") or {})}
            for a in block.get("assignments") or []]
        result[key] = block
    return result


def preview_publication(database: Any, draft_id: str, *, scope_kind: str,
                        expected_revision: int, week_start: str | None = None,
                        draft_database: Any = None) -> dict[str, Any]:
    scope_key = _scope(scope_kind, week_start)
    draft = get_experiment(draft_database or database, draft_id)
    if int(draft["revision"]) != int(expected_revision):
        raise PublicationConflict("Draft revision changed; reload before publication")
    version = canonical.canonical_version(database)
    base_id = str(draft.get("base_version_id") or (version or {}).get("version_id") or "")
    base = canonical.read_canonical_template(database, base_id) if base_id else None
    head = canonical.publication_head(database, scope_key)
    effective = canonical._effective_week(database, None, week_start) if scope_kind == "week" else None
    blocks, diagnostics, deferred = _compile(database, draft["payload"], base, scope_kind,
                                         week_start if scope_kind == "week" else None)
    if version and base_id != version["version_id"]:
        diagnostics.append({"code": "stale_base", "severity": "critical"})
    if scope_kind == "week" and not base:
        diagnostics.append({"code": "missing_base", "severity": "critical"})
    for issue in diagnostics:
        issue["message"] = issue.get("message") if issue["code"] == "review_required" else _ISSUE_MESSAGES.get(issue["code"], issue.get("message", "Требуется проверка"))
    blockers = [issue for issue in diagnostics if issue["severity"] == "critical"]
    before = (base or {}).get("blocks") or {}
    if scope_kind == "week" and effective:
        before = {row["block_key"]: {**row, "assignments": canonical._assignments(database, row.get("canonical_block_id"), row.get("patch") or {})}
                  for row in canonical._block_rows(database, effective["effective_week_id"]) if row.get("change_kind") != "cancelled"}
    diff = {"added": len(blocks.keys() - before.keys()), "removed": len(before.keys() - blocks.keys()),
            "changed": sum(_semantic(before[k]) != _semantic(blocks[k]) for k in blocks.keys() & before.keys()),
            "before_blocks": len(before), "after_blocks": len(blocks)}
    unchanged = {k for k in blocks.keys() & before.keys() if _semantic(before[k]) == _semantic(blocks[k])}
    summary = {"ready_lessons": sum(len(b.get("assignments") or []) for b in blocks.values()),
               "deferred_lessons": len(deferred), "deferred_blocks": len({item["block_key"] for item in deferred}),
               "unchanged_lessons": sum(len(blocks[k].get("assignments") or []) for k in unchanged),
               "replaced_lessons": sum(len(before[k].get("assignments") or []) for k in blocks.keys() & before.keys() - unchanged),
               **{f"{severity}_count": sum(d["severity"] == severity for d in diagnostics) for severity in ("info", "conflict", "critical")}}
    result = {"draft_id": draft_id, "draft_revision": draft["revision"], "scope_kind": scope_kind,
              "scope_key": scope_key, "week_start": week_start if scope_kind == "week" else None,
              "base_version_id": base_id or None, "effective_week_id": (effective or {}).get("effective_week_id"),
              "head_revision": (head or {}).get("revision", 0), "previous_published_id": (head or {}).get("published_id"),
              "payload_fingerprint": _hash(draft["payload"]), "directory_fingerprint": _directory_fingerprint(database),
              "can_publish": not blockers, "blockers": blockers, "deferred": deferred, "diff": diff,
              "diagnostics": diagnostics, "summary": summary,
              "source_kind": "template" if scope_kind == "template" else "authored_week" if draft["payload"].get("week_start") == week_start else "template_week",
              "change_items": _change_items(before, blocks),
              "source_policy": "manual_head_protected_source_refresh_requires_explicit_review"}
    result["read_only"] = bool(getattr(database, "read_only", False)) or (draft_database is not None and draft_database is not database)
    result["publication_available"] = not result["read_only"]
    result["preview_token"] = _hash({**result, "blocks": blocks})
    return result


def _write_head(database: Any, scope: str, published_id: str, version_id: str, now: str) -> None:
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO schedule_publication_heads(scope_key,published_id,version_id,revision,updated_at)
            VALUES (?,?,?,1,?) ON CONFLICT(scope_key) DO UPDATE SET published_id=excluded.published_id,
            version_id=excluded.version_id,revision=schedule_publication_heads.revision+1,updated_at=excluded.updated_at""",
            (scope, published_id, version_id, now))


def _persist(database: Any, preview: dict[str, Any], blocks: Mapping[str, Any], actor: Any,
             *, restored_from: str | None = None) -> dict[str, Any]:
    blocks = _artifact_blocks(blocks)
    publication_id = "publication-" + uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    if preview["scope_kind"] == "template":
        version_id = "constructor-template-" + uuid4().hex
        canonical.import_canonical_artifact(database, {"version_id": version_id,
            "parent_version_id": preview["base_version_id"], "schema_version": "canonical-v2",
            "source_snapshot": {"id": publication_id, "fingerprint": preview["payload_fingerprint"]},
            "summary": {"constructor_publication_id": publication_id,
                        "publication_summary": preview.get("summary"),
                        "deferred_lesson_ids": [item["lesson_id"] for item in preview.get("deferred", [])]}, "blocks": blocks},
            status="approved_with_exceptions" if preview.get("deferred") else "authoritative", approved_at=now)
        published_id = version_id
    else:
        version_id = preview["base_version_id"]
        base = canonical.read_canonical_template(database, version_id)["blocks"]
        patches = [{"block_key": key, "change_kind": "cancelled", "assignments": []} for key in base.keys() - blocks.keys()]
        patches += [{"block_key": key, "change_kind": "replaced" if key in base else "added",
                     **{k: v for k, v in value.items() if k in {"weekday", "slot", "grade_scope", "assignments"}},
                     "manual_override": True} for key, value in blocks.items()]
        effective = canonical.materialize_effective_week(database, version_id, preview["week_start"], patches,
            overlay_fingerprint=_hash([publication_id, preview["payload_fingerprint"]]), overlay_observed_at=now)
        published_id = effective["effective_week_id"]
    _write_head(database, preview["scope_key"], published_id, version_id, now)
    if preview["scope_kind"] == "template":
        readback = canonical.read_canonical_template(database)
        actual = readback["blocks"] if readback else {}
        correct_id = readback and readback["version_id"] == published_id
    else:
        runtime = canonical.effective_blocks(database, preview["week_start"])
        actual = {row["block_key"]: {**row, "assignments": canonical._assignments(database, row.get("canonical_block_id"), row.get("patch") or {})}
                  for row in runtime.get("items", []) if row.get("change_kind") != "cancelled"}
        correct_id = (canonical._effective_week(database, None, preview["week_start"]) or {}).get("effective_week_id") == published_id
    if not correct_id or {k: _semantic(v) for k, v in actual.items()} != {k: _semantic(v) for k, v in blocks.items()}:
        raise RuntimeError("Canonical runtime readback failed; transaction rolled back")
    readback_result = {"version_id": version_id,
        "effective_week_id": published_id if preview["scope_kind"] == "week" else None,
        "block_count": len(actual), "assignment_count": sum(len(b.get("assignments") or []) for b in actual.values()), "verified": True}
    metadata = {"preview": preview, "blocks": blocks, "runtime_readback": readback_result,
                "restored_from": restored_from}
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO schedule_constructor_publications
            (id,draft_id,draft_revision,scope_kind,scope_key,base_version_id,previous_published_id,published_id,version_id,payload_fingerprint,actor_user_id,created_at,metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (publication_id, preview["draft_id"], preview["draft_revision"],
            preview["scope_kind"], preview["scope_key"], preview["base_version_id"], preview["previous_published_id"],
            published_id, version_id, preview["payload_fingerprint"], str(actor) if actor is not None else None,
            now, json.dumps(metadata, ensure_ascii=False, default=str)))
    return {"status": "published", "publication_id": publication_id, "scope_kind": preview["scope_kind"],
            "published_id": published_id, "previous_published_id": preview["previous_published_id"],
            "runtime_readback": readback_result, "deferred": preview["deferred"],
            "summary": preview.get("summary"), "version_id": version_id, "created_at": now,
            "week_start": preview.get("week_start"), "scope_key": preview["scope_key"]}


@contextmanager
def _transaction(database: Any):
    if getattr(database, "read_only", False):
        raise ValueError("Publication is unavailable for a read-only school preview")
    if database.autocommit:
        raise ValueError("Publication requires a transactional database connection")
    try:
        with database.connection() as connection:
            if database.database_url:
                database.execute(connection, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                database.execute(connection, "SELECT pg_advisory_xact_lock(719817523)")
            else:
                database.execute(connection, "BEGIN IMMEDIATE")
            yield _TransactionDatabase(database, connection)
    except Exception as error:
        if getattr(error, "sqlstate", None) in {"40001", "40P01"}:
            raise PublicationConflict("Concurrent school data changed; review a fresh preview") from error
        raise


def publish_publication(database: Any, draft_id: str, *, scope_kind: str, expected_revision: int,
                        preview_token: str, confirmed: bool = False, week_start: str | None = None,
                        actor_user_id: Any = None) -> dict[str, Any]:
    if confirmed is not True or not preview_token:
        raise ValueError("Explicit confirmation and preview_token are required")
    with _transaction(database) as bound:
        # Lock the actual saved revision against simultaneous constructor saves.
        if database.database_url:
            database.execute(bound._connection, "SELECT id FROM schedule_experiment_drafts WHERE id=? FOR UPDATE", (draft_id,))
        preview = preview_publication(bound, draft_id, scope_kind=scope_kind,
            expected_revision=expected_revision, week_start=week_start)
        if preview["preview_token"] != preview_token:
            raise PublicationConflict("Preview is stale; review the new diff before confirming")
        if not preview["can_publish"]:
            raise PublicationBlocked(preview)
        draft = get_experiment(bound, draft_id)
        base = canonical.read_canonical_template(bound, preview["base_version_id"]) if preview["base_version_id"] else None
        blocks, _, _ = _compile(bound, draft["payload"], base, scope_kind,
                                 week_start if scope_kind == "week" else None)
        result = _persist(bound, preview, blocks, actor_user_id)
        if scope_kind == "template":
            # Successful explicit publication advances only this author's base
            # metadata. The authored payload is untouched; revision CAS makes
            # another browser reload before its next save/publication.
            row = bound.execute(bound._connection, """UPDATE schedule_experiment_drafts
                SET base_version_id=?,revision=revision+1,updated_at=?
                WHERE id=? AND revision=? RETURNING revision""",
                (result["published_id"], datetime.now(timezone.utc).isoformat(), draft_id, expected_revision)).fetchone()
            if not row:
                raise PublicationConflict("Draft changed during publication")
            result["draft_revision"] = int(row["revision"])
            result["base_version_id"] = result["published_id"]
            if (draft["payload"].get("draft_kind") == "school"
                    and not draft["payload"].get("week_start")
                    and not draft["payload"].get("source_template")):
                # The explicit published school author becomes the main editor
                # template too; selecting it must not reopen an older copy.
                bound.execute(bound._connection, """INSERT INTO school_template_selection
                    (scope,draft_id,source_draft_id,source_revision,source_payload_sha256,activated_at)
                    VALUES ('school',?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
                    draft_id=excluded.draft_id,source_draft_id=excluded.source_draft_id,
                    source_revision=excluded.source_revision,source_payload_sha256=excluded.source_payload_sha256,
                    activated_at=excluded.activated_at""",
                    (draft_id, draft_id, result["draft_revision"], _hash(draft["payload"]),
                     datetime.now(timezone.utc).isoformat()))
        else:
            result["draft_revision"] = expected_revision
            result["base_version_id"] = preview["base_version_id"]
        return result


def list_publications(database: Any, draft_id: str | None = None) -> dict[str, Any]:
    with database.connection() as connection:
        query = "SELECT * FROM schedule_constructor_publications"
        rows = database.execute(connection, query + (" WHERE draft_id=?" if draft_id else "") + " ORDER BY created_at DESC,id DESC LIMIT 100",
                                (draft_id,) if draft_id else ()).fetchall()
        heads = {row["scope_key"]: dict(row) for row in database.execute(connection,
            "SELECT * FROM schedule_publication_heads ORDER BY scope_key").fetchall()}
    result = []
    for row in rows:
        item = dict(row)
        metadata = json.loads(item.pop("metadata"))
        item["runtime_readback"] = metadata["runtime_readback"]
        item["restored_from"] = metadata.get("restored_from")
        item["diagnostics"] = metadata.get("preview", {}).get("diagnostics", [])
        item["deferred"] = [{**entry, "action": entry.get("action") or (
            "preserved_base" if entry.get("reason") == "Unresolved change retained in draft; base block preserved"
            else "unknown")} for entry in metadata.get("preview", {}).get("deferred", [])]
        item["summary"] = metadata.get("preview", {}).get("summary")
        result.append(item)
    return {"items": result, "heads": heads}


def preview_restore(database: Any, publication_id: str, *, expected_head_revision: int) -> dict[str, Any]:
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM schedule_constructor_publications WHERE id=?", (publication_id,)).fetchone()
    if not row:
        raise ValueError("Publication not found")
    item = dict(row)
    head = canonical.publication_head(database, item["scope_key"])
    if not head or int(head["revision"]) != int(expected_head_revision):
        raise PublicationConflict("Publication head changed; review before restoring")
    metadata = json.loads(item["metadata"])
    target = metadata["blocks"]
    if item["scope_kind"] == "template":
        current = canonical.read_canonical_template(database, head["version_id"])["blocks"]
    else:
        current = {b["block_key"]: {**b, "assignments": canonical._assignments(database, b.get("canonical_block_id"), b.get("patch") or {})}
                   for b in canonical._block_rows(database, head["published_id"]) if b.get("change_kind") != "cancelled"}
    blockers = []
    with database.connection() as connection:
        teachers = {str(r["id"]) for r in database.execute(connection,
            "SELECT id FROM identities WHERE kind='teacher' AND status='active'").fetchall()}
        groups = {str(r["id"]) for r in database.execute(connection,
            "SELECT id FROM groups WHERE canonical IS TRUE").fetchall()}
        students = {str(r["id"]) for r in database.execute(connection,
            "SELECT id FROM identities WHERE kind='student' AND status='active'").fetchall()}
    for key, b in target.items():
        for a in b.get("assignments") or []:
            rule = (a.get("metadata") or {}).get("audience_rule") or {}
            if set(str(i) for i in a.get("teacher_ids") or []) - teachers:
                blockers.append({"code": "unknown_teacher", "message": f"Historical teacher missing/inactive in {key}"})
            if set(str(i) for i in a.get("canonical_group_ids") or []) - groups:
                blockers.append({"code": "unknown_group", "message": f"Historical group missing/noncanonical in {key}"})
            if set(str(i) for i in rule.get("include_student_ids") or []) - students:
                blockers.append({"code": "unknown_student", "message": f"Historical participant missing/inactive in {key}"})
            start = date.fromisoformat(str(metadata["preview"].get("week_start") or date.today()))
            start -= timedelta(days=start.weekday())
            actual_day = (start + timedelta(days=int(b.get("weekday") or 0))).isoformat()
            group_ids = set(str(i) for i in a.get("canonical_group_ids") or [])
            if rule.get("class_group_id"):
                group_ids.add(str(rule["class_group_id"]))
            for group_id in group_ids & groups:
                with database.connection() as connection:
                    members = database.execute(connection, """SELECT m.identity_id FROM memberships m
                        JOIN identities i ON i.id=m.identity_id WHERE m.group_id=? AND m.active IS TRUE
                        AND m.member_role='student' AND i.kind='student' AND i.status='active'
                        AND (m.valid_from IS NULL OR m.valid_from<=?) AND (m.valid_until IS NULL OR m.valid_until>=?)""",
                        (group_id, actual_day, actual_day)).fetchall()
                    overrides = database.execute(connection, """SELECT identity_id,action FROM membership_overrides
                        WHERE group_id=? AND active IS TRUE AND member_role='student'
                        AND (valid_from IS NULL OR valid_from<=?) AND (valid_until IS NULL OR valid_until>=?)""",
                        (group_id, actual_day, actual_day)).fetchall()
                resolved = {str(r["identity_id"]) for r in members}
                resolved.update(str(r["identity_id"]) for r in overrides if r["action"] == "include" and str(r["identity_id"]) in students)
                resolved.difference_update(str(r["identity_id"]) for r in overrides if r["action"] == "exclude")
                if not resolved and (a.get("metadata") or {}).get("special_mark") not in {"lunch", "no_lesson"}:
                    blockers.append({"code": "unknown_membership", "message": f"Historical group {group_id} has no confirmed membership for {actual_day}"})
    for key, b in current.items():
        protected = (b.get("status") == "protected" or (b.get("evidence") or {}).get("protected")
                     or any((a.get("metadata") or {}).get("protected") for a in b.get("assignments") or []))
        if protected and (key not in target or _semantic(b) != _semantic(target[key])):
            blockers.append({"code": "protected_base", "message": f"Restore would replace protected block {key}"})
    result = {"publication_id": publication_id, "scope_kind": item["scope_kind"],
              "scope_key": item["scope_key"], "current_published_id": head["published_id"],
              "target_published_id": item["published_id"], "expected_head_revision": head["revision"],
              "directory_fingerprint": _directory_fingerprint(database),
              "can_restore": not blockers, "blockers": blockers,
              "change_items": _change_items(current, target),
              "diff": {"added": len(target.keys() - current.keys()), "removed": len(current.keys() - target.keys()),
                       "changed": sum(_semantic(current[k]) != _semantic(target[k]) for k in target.keys() & current.keys()),
                       "before_blocks": len(current), "after_blocks": len(target)}}
    result["preview_token"] = _hash(result)
    return result


def restore_publication(database: Any, publication_id: str, *, expected_head_revision: int,
                        preview_token: str, confirmed: bool = False, actor_user_id: Any = None) -> dict[str, Any]:
    """Restore as a NEW immutable artifact with a current-head CAS guard."""
    if confirmed is not True or not preview_token:
        raise ValueError("Explicit restore confirmation is required")
    with _transaction(database) as bound:
        row = database.execute(bound._connection, "SELECT * FROM schedule_constructor_publications WHERE id=?", (publication_id,)).fetchone()
        if not row:
            raise ValueError("Publication not found")
        item = dict(row)
        restore_preview = preview_restore(bound, publication_id, expected_head_revision=expected_head_revision)
        if restore_preview["preview_token"] != preview_token:
            raise PublicationConflict("Restore preview changed; review it again")
        if not restore_preview["can_restore"]:
            raise PublicationBlocked(restore_preview)
        head = canonical.publication_head(bound, item["scope_key"])
        if not head or int(head["revision"]) != int(expected_head_revision):
            raise PublicationConflict("Publication head changed; review before restoring")
        metadata = json.loads(item["metadata"])
        preview = metadata["preview"]
        preview.update(previous_published_id=head["published_id"], head_revision=head["revision"])
        if item["scope_kind"] == "template":
            preview["base_version_id"] = head["version_id"]
        return _persist(bound, preview, metadata["blocks"], actor_user_id, restored_from=publication_id)
