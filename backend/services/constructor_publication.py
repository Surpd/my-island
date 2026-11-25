"""Explicit, transactional publication of saved constructor drafts.

No Google writes or authored-payload mutations: the adapter creates immutable
canonical/effective artifacts and advances a school-local publication pointer.
Explicit template publication advances the author's base/revision atomically.
Schema installation is an explicit deployment concern, never a preview side effect.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from backend.services.schedule_subjects import subject_label
from backend.services import canonical_schedule as canonical
from backend.services.schedule_experiments import (
    get_experiment, experiment_week_audience_preview, _is_lunch_lesson, _lesson_activity_type, _lesson_audience,
    _lesson_interval, _lesson_weekday, _canonical_time,
    _lesson_day_modes, lesson_is_online, lesson_is_sdep_display, payload_day_mode_conflicts,
)
from backend.services.constructor_resolution import validated_exceptions
from backend.services.schedule_rooms import room_key
from backend.services.schedule_edit_validation import (
    TEACHER_ASSIGNMENT_NOTE, TEACHER_IDENTITY_NOTE, day_mode_review_reason,
    is_legacy_group_link_note, normalized_note, notes, without_legacy_group_notes,
)
from backend.services.schedule_normalization import LUNCH_SCOPE_CLAUSE, scope_warning_evidence, without_scope_clauses


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
    """CREATE TABLE IF NOT EXISTS schedule_publication_receipts (
        operation_id TEXT PRIMARY KEY, actor_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL)""",
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


def _requires_physical_teacher(lesson: Mapping[str, Any], independent_practice: bool = False) -> bool:
    if independent_practice or lesson.get("activityType") in {"practice", "club", "elective", "event"}:
        return False
    if lesson.get("kind") == "event" or lesson.get("informational") is True:
        return False
    if lesson.get("specialMark") in {"lunch", "no_lesson"}:
        return False
    return True


def initialize_publication_schema(database: Any) -> None:
    """Explicit local fixture setup; production migrations must be approved."""
    with database.connection() as connection:
        from backend.services.published_week_sheets import SCHEMA as sheet_schema
        for statement in (*PUBLICATION_SCHEMA, *sheet_schema):
            database.execute(connection, statement)
        if not database.database_url:
            columns = database.execute(connection, "PRAGMA table_info(published_week_sheet_bindings)").fetchall()
            if not any(row["name"] == "delivered_fingerprint" for row in columns):
                database.execute(connection, "ALTER TABLE published_week_sheet_bindings ADD COLUMN delivered_fingerprint TEXT")



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
            "assignments": [{"activity": subject_label(a.get("activity")),
                "role": a.get("assignment_kind") or a.get("role") or "primary",
                "groups": sorted(str(i) for i in a.get("canonical_group_ids") or (a.get("audience") or {}).get("canonical_group_ids") or []),
                "teachers": sorted(str(i) for i in a.get("teacher_ids") or []),
                "metadata": {"room": room_key((a.get("metadata") or {}).get("room")),
                             "note": str((a.get("metadata") or {}).get("note") or ""),
                             "activity_type": (a.get("metadata") or {}).get("activity_type") or "lesson",
                             "day_plan_mode": (a.get("metadata") or {}).get("day_plan_mode") or "normal",
                             "audience_coverage": (a.get("metadata") or {}).get("audience_coverage"),
                             "joint_activity_id": (a.get("metadata") or {}).get("joint_activity_id"),
                             "activity_format": (a.get("metadata") or {}).get("activity_format"),
                             "responsible_teacher_ids": sorted(str(i) for i in (a.get("metadata") or {}).get("responsible_teacher_ids") or []),
                             "special_mark": (a.get("metadata") or {}).get("special_mark"),
                             "constructor_details": {k: v for k, v in ((a.get("metadata") or {}).get("constructor_details") or {}).items()
                                                     if k != "audience_snapshot"},
                             "audience_rule": (a.get("metadata") or {}).get("audience_rule") or
                                 {"kind": "groups", "group_ids": sorted(str(i) for i in a.get("canonical_group_ids") or (a.get("audience") or {}).get("canonical_group_ids") or [])}}}
                for a in block.get("assignments") or []]}


@dataclass
class _PublicationDirectory:
    """Request-local directory used by roster resolution and the real projector."""

    students: dict[str, Any]
    teachers: dict[str, str]
    groups: dict[str, Any]
    all_groups: dict[str, Any]
    memberships: list[dict[str, Any]]
    overrides: list[dict[str, Any]]
    fingerprint: str

    @classmethod
    def load(cls, database: Any) -> _PublicationDirectory:
        queries = {
            "identities": "SELECT id,kind,status,class_name,display_name FROM identities ORDER BY id",
            "groups": "SELECT * FROM groups ORDER BY id",
            "memberships": "SELECT * FROM memberships ORDER BY id",
            "overrides": "SELECT * FROM membership_overrides ORDER BY id",
        }
        with database.connection() as connection:
            data = {table: [dict(row) for row in database.execute(connection, query).fetchall()]
                    for table, query in queries.items()}
        students = {str(row["id"]): row for row in data["identities"] if row["kind"] == "student" and row["status"] == "active"}
        teachers = {str(row["id"]): str(row["display_name"]) for row in data["identities"] if row["kind"] == "teacher" and row["status"] == "active"}
        all_groups = {str(row["id"]): row for row in data["groups"]}
        groups = {key: group for key, group in all_groups.items() if group["canonical"] and not group["archived_at"]}
        memberships = [{**row, "identity_status": "active", "identity_kind": "student", "group_canonical": True}
                       for row in data["memberships"] if row["active"] and row["member_role"] == "student"
                       and str(row["identity_id"]) in students and str(row["group_id"]) in groups]
        overrides = [row for row in data["overrides"] if row["active"] and row["member_role"] == "student"
                     and row["action"] in {"include", "exclude"}]
        # Even edits with no visible effect on the assigned counts invalidate
        # an earlier confirmation token.
        return cls(students, teachers, groups, all_groups, memberships, overrides, _hash(data))

    def audience_snapshot(self, day: str) -> dict[str, Any]:
        return {
            "students": [{key: row[key] for key in ("id", "display_name", "class_name")} for row in self.students.values()],
            "groups": [{**{key: row[key] for key in ("id", "display_name", "group_type", "base_class_name", "semantic_dimension")},
                        "raw_name": row["name"], "name": row["display_name"] if row["display_name"] is not None else row["name"]}
                       for row in self.groups.values()],
            "memberships": [{key: row[key] for key in ("identity_id", "group_id", "valid_from", "valid_until")}
                            for row in self.memberships if canonical._date_in_period(day, row["valid_from"], row["valid_until"])],
            "membership_overrides": [{key: row[key] for key in ("identity_id", "group_id", "action", "valid_from", "valid_until")}
                                     for row in self.overrides if canonical._date_in_period(day, row["valid_from"], row["valid_until"])],
        }


def _change_items(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old = _semantic(before[key]) if key in before else None
        new = _semantic(after[key]) if key in after else None
        if old != new:
            changes.append({"block_key": key, "operation": "add" if old is None else "remove" if new is None else "change",
                            "before": old, "after": new})
    return changes


_ISSUE_MESSAGES = {
    "joint_activity": "Совместное занятие подтверждено. Составы групп сохранены.",
    "review_required": "Занятие требует проверки",
    "explicit_deferred": "Запись отложена администратором и сохранена в исходном расписании",
    "mixed_day_modes_unsupported": "У классов разные режимы дня. Проверьте составы групп и режимы.",
    "day_mode_conflict": "Для классов одной параллели заданы разные режимы дня",
    "audience_unresolved": "Состав участников не подтверждён или изменился",
    "empty_activity": "Не указано название занятия",
    "elective_block_unlinked": "Курс по выбору не связан с общим блоком. Сопоставьте направление с блоком в конструкторе.",
    "elective_block_name_conflict": "У направлений одного блока разные названия. Укажите общее название.",
    "teacher_overlap": "У преподавателя два занятия одновременно. Уточните назначение или время.",
    "student_overlap": "У ученика два занятия одновременно. Уточните составы групп или время.",
    "room_overlap": "В кабинете два очных занятия одновременно. Измените кабинет или время.",
    "joint_activity_unconfirmed": "Подтвердите совместное занятие или измените время.",
    "stale_group_subject_mismatch": "Связь с сохранённой группой не подтверждена. Проверьте выбранную группу.",
    "missing_teacher": "Для обычного занятия не указан преподаватель",
    "missing_room": "Для очного занятия не указан кабинет",
    "unknown_teacher": "Указанный преподаватель отсутствует или неактивен",
    "missing_runtime_grade": "Не удалось определить параллель для участников",
    "unsupported_partial_cohort": "Для части параллели укажите подтверждённый состав участников.",
    "unknown_class": "Класс не найден в школьном справочнике",
    "missing_special_scope": "Не удалось определить класс для специальной отметки",
    "duplicate_lesson_id": "Повторяется идентификатор исходной записи",
    "missing_lesson_id": "У исходной записи отсутствует стабильный идентификатор",
    "missing_origin_block": "Не найдена исходная опубликованная запись",
    "invalid_slot": "Указано неверное время или день занятия",
    "protected": "Изменение защищённой записи требует отдельного решения",
    "protected_base": "Изменена защищённая запись опубликованного расписания",
    "stale_base": "Исходная версия расписания изменилась. Обновите черновик и повторите проверку.",
    "missing_base": "Перед публикацией недели нужен опубликованный школьный шаблон",
    "projection_mismatch": "Не удалось безопасно подтвердить персональное отображение записи",
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
                           reference: date,
                           conflict_student_ids_by_block: Mapping[str, set[str]] | None = None,
                           *, use_projection_cache: bool = True,
                           directory: _PublicationDirectory | None = None) -> set[str]:
    """Use the real projector in memory, before any immutable artifact is written.

    In particular a removed partition must never widen a remaining audience.
    This does not materialize a week or copy directory rows to a second store.
    """
    if not blocks:
        return set()
    directory = directory or _PublicationDirectory.load(database)
    students = directory.students
    candidates = {key: {**b, "block_key": key, "start_time": b["slot"]["start"], "end_time": b["slot"]["end"],
                        "patch": {"assignments": b["assignments"]}} for key, b in blocks.items()}
    context = canonical.StudentProjectionContext({}, students, directory.groups, directory.memberships,
                                                 directory.overrides, list(candidates.values()))
    mismatches = set()

    def assignment_is_elective(assignment: Mapping[str, Any]) -> bool:
        metadata = canonical._assignment_metadata(assignment.get("metadata") or {})
        details = metadata.get("constructor_details") if isinstance(metadata.get("constructor_details"), Mapping) else {}
        return (metadata.get("activity_type") or details.get("activityType")) == "elective"

    # A school's pupils usually repeat the same canonical membership pattern
    # (for example, base class + one instructional subgroup). Projection state
    # depends on that pattern and grade, not the student's name. Reuse it while
    # retaining per-student projections whenever audience rules or a confirmed
    # personal conflict make identity itself significant.
    projection_cache: dict[tuple[Any, ...], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    identity_sensitive_by_day: dict[int, set[str]] = {}
    students_by_grade: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    student_grade_by_id = {}
    for student_id, student in students.items():
        grade = canonical._projection_grade(context, student)
        students_by_grade[grade].append((student_id, student))
        student_grade_by_id[student_id] = grade
    for source_block in blocks.values():
        weekday = source_block.get("weekday")
        if weekday is None:
            continue
        for assignment in source_block.get("assignments") or []:
            rule = canonical._assignment_audience_rule(assignment)
            identity_sensitive_by_day.setdefault(int(weekday), set()).update(
                str(value) for field in ("include_student_ids", "exclude_student_ids")
                for value in rule.get(field) or [])

    def projected_for(student_id: str, student: Mapping[str, Any], membership_groups: set[str],
                      candidate: Mapping[str, Any], day: str, individual: set[str], *, required_only: bool = False):
        if not use_projection_cache:
            return canonical._project_student_block(database, context, student, candidate, day)
        cache_key = (candidate["block_key"], required_only, student_grade_by_id[student_id],
                     frozenset(membership_groups), student_id if student_id in individual else None)
        projected = projection_cache.get(cache_key)
        if projected is None:
            projected = canonical._project_student_block(database, context, student, candidate, day)
            projection_cache[cache_key] = projected
        return projected

    for key, block in blocks.items():
        weekday = block["weekday"]
        day = (reference + timedelta(days=weekday)).isoformat()
        candidate = candidates[key]
        assignments = block["assignments"]
        grades = str(block.get("grade_scope") or "").split(",")
        expected_by_student: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        elective_assignments = []
        required_assignments = []
        lunch_assignments = []
        for index, assignment in enumerate(assignments):
            lesson_id = assignment["metadata"]["constructor_lesson_id"]
            for student_id in (audience.get(lesson_id) or {}).get("student_ids") or []:
                expected_by_student[str(student_id)].append(assignment)
            if assignment_is_elective(assignment):
                details = canonical._assignment_metadata(assignment["metadata"]).get("constructor_details") or {}
                if details.get("blockId"):
                    elective_assignments.append((str(details["blockId"]), set(assignment.get("canonical_group_ids") or [])))
            else:
                required_assignments.append(assignment)
            if assignment["metadata"].get("special_mark") == "lunch":
                lunch_assignments.append((index, assignment))
        if any(student_grade_by_id.get(sid) not in grades for sid in expected_by_student):
            mismatches.add(key)  # A grade filter must never silently lose a confirmed pupil.
        individual = identity_sensitive_by_day.get(weekday, set()) | (conflict_student_ids_by_block or {}).get(key, set())
        individual.update(str(sid) for unresolved in block.get("unresolved") or [] for sid in unresolved.get("student_ids") or [])
        for sid, student in (entry for grade in dict.fromkeys(grades) for entry in students_by_grade.get(grade, [])):
            membership_groups = canonical._student_memberships(context, sid, day)
            expected = expected_by_student.get(sid, []) + [
                a for index, a in lunch_assignments
                if canonical._other_assignment_occupies_slot(context, student, membership_groups, a, str(block["grade_scope"]))
                and not canonical._student_busy_in_slot(database, context, student, membership_groups, candidate, index, exact_intervals=True, include_available_slot=True)
            ]
            projected, _ = projected_for(sid, student, membership_groups, candidate, day, individual)
            expected_activities = [a for a in expected if a["activity"] != "NO_LESSON"]
            if projected["state"] == "ACTIVITY" and projected.get("activity_type") == "sdep_plan":
                planned = {item.get("activity") for item in projected.get("activities") or []}
                expected_planned = {item.get("activity") for item in expected_activities
                                    if (item.get("metadata") or {}).get("day_plan_mode") == "sdep"}
                if not expected_planned or not expected_planned.issubset(planned):
                    mismatches.add(key)
            elif projected["state"] == "ACTIVITY":
                if projected.get("activity_type") == "elective":
                    block_ids = {block_id for block_id, groups in elective_assignments if groups.intersection(membership_groups)}
                    if len(block_ids) == 1:
                        # A successful shared elective card proves only that
                        # the elective lanes can be folded together. Validate
                        # every required assignment in this same canonical
                        # block independently so the elective branch cannot
                        # mask an ordinary lesson or a projection failure.
                        expected_required = [assignment for assignment in expected_activities
                            if not assignment_is_elective(assignment)]
                        if required_assignments or expected_required:
                            required_candidate = {**candidate, "patch": {**candidate.get("patch", {}),
                                "assignments": required_assignments}}
                            required_projection, _ = projected_for(sid, student, membership_groups,
                                required_candidate, day, individual, required_only=True)
                            required_names = {str(assignment.get("activity") or "") for assignment in expected_required}
                            if required_names:
                                matches_required = required_projection.get("state") == "ACTIVITY" and any(
                                    assignment.get("activity") == required_projection.get("activity")
                                    and ((assignment.get("metadata") or {}).get("day_plan_mode") == "sdep"
                                         or set(assignment.get("teacher_ids") or []) == set(str(value) for value in
                                             required_projection.get("teacher_ids") or []))
                                    for assignment in expected_required)
                                if not matches_required:
                                    mismatches.add(key)
                            elif required_projection.get("state") in {"ACTIVITY", "UNRESOLVED"}:
                                mismatches.add(key)
                        continue
                matches = [a for a in expected_activities if a["activity"] == projected.get("activity")
                           and ((a.get("metadata") or {}).get("day_plan_mode") == "sdep"
                                or set(a["teacher_ids"]) == set(str(t) for t in projected.get("teacher_ids") or []))]
                if not matches:
                    mismatches.add(key)
            elif (projected["state"] == "UNRESOLVED"
                  and set(projected.get("issue_codes") or []) == {"MULTIPLE_ACTIVITY_CLAIMS"}
                  and sid in (conflict_student_ids_by_block or {}).get(key, set())):
                # The exact student has a confirmed timetable collision. Keep
                # this block's assignments for unaffected classmates and attach
                # a personal conflict marker in the immutable source archive.
                # Other projection failures, even for the same student, remain
                # blocking and are handled by the normal mismatch path.
                continue
            elif projected["state"] == "UNRESOLVED" or expected_activities:
                mismatches.add(key)
    return mismatches


def _compile(database: Any, payload: Mapping[str, Any], base: Mapping[str, Any] | None,
             scope_kind: str, reference_week: str | None = None,
             audience_output: dict[str, Any] | None = None, *,
             directory: _PublicationDirectory | None = None) -> tuple[dict[str, Any], list, list]:
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
        if "specialMark" not in lesson and origin_metadata.get("special_mark"):
            lesson["specialMark"] = origin_metadata["special_mark"]
        activity_type = _lesson_activity_type(lesson)
        if "activityType" not in lesson and activity_type != "lesson":
            lesson["activityType"] = activity_type
    reference = date.fromisoformat(str(reference_week or payload.get("reference_date") or payload.get("week_start") or date.today())[:10])
    reference -= timedelta(days=reference.weekday())
    directory = directory or _PublicationDirectory.load(database)
    audience = experiment_week_audience_preview(
        database, payload, reference, snapshot_for_date=directory.audience_snapshot)["lessons"]
    if audience_output is not None:
        audience_output.update(audience)
    teachers, groups = directory.teachers, directory.groups
    students = {student_id: str(row["class_name"] or "") for student_id, row in directory.students.items()}
    class_grades = {group_id: canonical.canonical_group_grade(group) for group_id, group in groups.items()
                    if not group.get("archived_at")}
    verified_lunch_scopes = scope_warning_evidence(payload, {
        group_id: class_grades[group_id] for group_id, group in groups.items()
        if group.get("group_type") == "class"
    })
    elective_cohorts: dict[tuple[str, int, str], set[str]] = {}
    elective_block_names: dict[str, set[str]] = {}
    for lesson in lessons:
        if str(lesson.get("activityType") or "") != "elective" or not lesson.get("blockId"):
            continue
        interval = _lesson_interval(lesson, payload.get("slots") or [])
        try:
            day = int(lesson.get("day", lesson.get("weekday", -1)))
        except (TypeError, ValueError):
            continue
        if interval is None:
            continue
        cohort = {str(value) for value in (lesson.get("classIds") or lesson.get("displayClassIds") or []) if value}
        if lesson.get("classId"):
            cohort.add(str(lesson["classId"]))
        cohort = {value for value in cohort if value in groups and groups[value].get("group_type") == "class"}
        block_id = str(lesson["blockId"])
        block_name = str(lesson.get("blockName") or "").strip()
        if block_name:
            elective_block_names.setdefault(block_id, set()).add(block_name)
        key = (block_id, day, f"{interval[0]}:{interval[1]}")
        elective_cohorts.setdefault(key, set()).update(cohort)
    elective_students: dict[tuple[str, int, str], set[str]] = {}
    if elective_cohorts:
        context = canonical.StudentProjectionContext({}, directory.students, directory.groups,
                                                     directory.memberships, directory.overrides, [])
        for key, cohort in elective_cohorts.items():
            day = (reference + timedelta(days=key[1])).isoformat()
            elective_students[key] = {student_id for student_id in students
                                      if cohort.intersection(canonical._student_memberships(context, student_id, day))}
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
        exceptions = validated_exceptions(payload.get("lessons") or [], lesson, scope=reference_week or payload.get("week_start") or "template", slots=payload.get("slots") or [])
        practice = any(e["type"]=="independent_practice" for e in exceptions)
        if lesson.get("deferred") or any(e["type"]=="defer" for e in exceptions):
            block("explicit_deferred", "Запись явно отложена администратором")
        result = audience.get(lesson_id) or {}
        special = "lunch" if _is_lunch_lesson(lesson) else lesson.get("specialMark")
        activity_type = str(lesson.get("activityType") or "lesson")
        is_lunch = special == "lunch" or activity_type == "lunch"
        is_optional = activity_type in {"club", "elective"}
        if activity_type == "elective" and len(elective_block_names.get(str(lesson.get("blockId") or ""), set())) > 1:
            block("elective_block_name_conflict", "У направлений с одним общим идентификатором различаются названия блока. Укажите одинаковое название для всех направлений.")
        is_event = activity_type == "event" or lesson.get("kind") == "event"
        informational_unassigned_event = is_event and lesson.get("informational") is True and not isinstance(lesson.get("audience"), Mapping)
        display_classes = lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else [])
        rule = _lesson_audience(lesson)
        lunch_has_configured_audience = is_lunch and (
            isinstance(lesson.get("audience"), Mapping)
            or rule.get("kind") in {"groups", "students", "available_slot", "legacy_remaining"}
            or bool(rule.get("grade"))
        )
        known_lunch_classes = bool(display_classes) and all(
            str(group_id) in groups and groups[str(group_id)]["group_type"] == "class"
            for group_id in display_classes)
        lunch_scope_confirmed = is_lunch and (
            (result.get("status") == "ok" and not result.get("needs_recalculation") and bool(rule.get("kind")))
            if lunch_has_configured_audience else known_lunch_classes
        )
        legacy_lunch_scope_verified = verified_lunch_scopes.get(lesson_id, (None,))[0] == "lunch_scope_warning"
        if lunch_has_configured_audience and not lunch_scope_confirmed and result.get("status") == "ok" and not result.get("needs_recalculation"):
            block("audience_unresolved", "Уточните, каким классам, группам или ученикам назначен обед.")
        if not lunch_scope_confirmed and not is_optional and (result.get("status") != "ok" or result.get("needs_recalculation")):
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
        if not subject and special not in {"lunch", "no_lesson"}:
            block("empty_activity", "Activity name is required")
        if activity_type == "elective" and not lesson.get("blockId"):
            block("elective_block_unlinked", "Курс по выбору пока не связан с общим блоком. Сопоставьте направление с существующим блоком в конструкторе; автоматическое объединение по названию и времени небезопасно.")
        teacher_ids = sorted({str(x) for x in (lesson.get("teacherIds") or []) + ([lesson["teacherId"]] if lesson.get("teacherId") else [])})
        is_display_marker = special in {"lunch", "no_lesson"}
        if is_display_marker:
            # Old staffing fields on a grid marker carry no teaching assignment.
            teacher_ids = []
        responsible_source = [] if is_display_marker else (
            lesson.get("responsible_teacher_ids") or lesson.get("responsibleTeacherIds") or ([] if activity_type == "practice" else teacher_ids))
        if not isinstance(responsible_source, (list, tuple, set)):
            responsible_source = teacher_ids
        responsible_teacher_ids = sorted({str(value) for value in responsible_source if value})
        teacher_evidence = set(teacher_ids) | (set(responsible_teacher_ids) if practice or activity_type == "practice" else set())
        review_reason = str(lesson.get("clarificationReason") or "Занятие требует проверки")
        class_modes = _lesson_day_modes(payload, lesson, class_grades, audience_grades={
            canonical._grade(students.get(str(student_id), "")) for student_id in result.get("student_ids") or []
        })
        online_only = lesson_is_online(lesson, class_modes)
        sdep_independent = lesson_is_sdep_display(lesson, class_modes)
        if not lesson.get("importError") and (sdep_independent or online_only):
            review_reason = day_mode_review_reason(review_reason, sdep_display=sdep_independent, online=online_only)
        if sdep_independent:
            teacher_ids, responsible_teacher_ids, teacher_evidence = [], [], set()
        if len(class_modes) > 1 and (lesson.get("format") not in {"offline", "online"} or "sdep" in class_modes):
            block("mixed_day_modes_unsupported", "Занятие охватывает классы с разными режимами дня; разделите аудитории или режимы")
        if lesson.get("importError") and (sdep_independent or online_only):
            block("review_required", review_reason)
        elif needs_review:
            if is_legacy_group_link_note(lesson):
                if not result.get("verified_saved_group"):
                    class_labels = [str(groups[str(group_id)].get("display_name") or groups[str(group_id)].get("name") or group_id)
                                    for group_id in display_classes if str(group_id) in groups]
                    raw = _lesson_audience(lesson)
                    labels = [str(groups[str(group_id)].get("display_name") or groups[str(group_id)].get("name"))
                              for group_id in raw.get("group_ids") or [] if str(group_id) in groups]
                    blockers.append({"code": "stale_group_subject_mismatch", "lesson_id": lesson_id, "severity": "info",
                        "message": f"Связь с группой из старой записи не подтверждена. Предмет: {subject or 'не указан'}. Класс: {', '.join(class_labels) or 'не подтверждён'}. "
                                   + (f"Сохранённая группа: {', '.join(labels)}. " if labels else "")
                                   + "Проверьте состав. Если он подходит, оставьте эту группу."})
                # Suppress only the obsolete label, never independent warnings.
                review_reason = without_legacy_group_notes(review_reason)
            # An explicit, active teacher is sufficient for publication. A
            # separate workload row is not required evidence for the lesson.
            # Historical import notes can be compound. Clear only the teacher
            # clause when every explicitly named teacher is currently active.
            active_teacher_note = "не подтверждён преподаватель"
            reason_without_active_teacher_note = review_reason
            if sdep_independent:
                pass  # Only the exact mode-dependent clauses above are waived.
            elif online_only or is_lunch:
                # The legacy substring fallback below may erase an unrelated
                # compound reason. Mode-aware records retain every unwaived clause.
                waived = {TEACHER_IDENTITY_NOTE} if not teacher_ids or not teacher_evidence - teachers.keys() else set()
                if teacher_evidence and not teacher_evidence - teachers.keys():
                    waived.add(TEACHER_ASSIGNMENT_NOTE)
                reason_without_active_teacher_note = "; ".join(
                    clause for clause in notes(review_reason) if normalized_note(clause) not in waived)
            else:
                if active_teacher_note in review_reason.casefold() and teacher_ids and not teacher_evidence - teachers.keys():
                    reason_without_active_teacher_note = re.sub(active_teacher_note, "", review_reason, flags=re.IGNORECASE)
                    reason_without_active_teacher_note = re.sub(r"\s*(?:[,;·]|\bи\b)\s*", " ", reason_without_active_teacher_note, flags=re.IGNORECASE).strip(" .,:;-")
                if active_teacher_note in review_reason.casefold() and not teacher_ids:
                    reason_without_active_teacher_note = ""  # missing_teacher below reports the current condition.
                elif ("нет подтверждённого назначения преподавателя группе" in review_reason.casefold()
                      and teacher_evidence and not teacher_evidence - teachers.keys()):
                    reason_without_active_teacher_note = re.sub(r"нет подтверждённого назначения преподавателя группе", "", review_reason, flags=re.IGNORECASE).strip(" .,:;-и")
            if is_lunch and (lunch_scope_confirmed or legacy_lunch_scope_verified):
                reason_without_active_teacher_note = without_scope_clauses(
                    reason_without_active_teacher_note, frozenset({LUNCH_SCOPE_CLAUSE}))
                if lesson.get("importError"):
                    reason_without_active_teacher_note = reason_without_active_teacher_note or "Проверьте ошибку импорта обеда."
            if not reason_without_active_teacher_note:
                pass  # Only the validated old clause is removed from review.
            elif is_optional:
                pass
            else:
                block("review_required", reason_without_active_teacher_note)
        if teacher_evidence - teachers.keys():
            if is_optional:
                blockers.append({"code": "unknown_teacher", "severity": "info", "lesson_id": lesson_id,
                                 "message": "У направления указан отсутствующий или неактивный преподаватель. Общее занятие остаётся в расписании учеников; неподтверждённое назначение преподавателя не публикуется."})
                teacher_ids = [teacher_id for teacher_id in teacher_ids if teacher_id in teachers]
            else:
                block("unknown_teacher", "An assigned teacher is missing or inactive")
        if activity_type == "consultation" and not teacher_ids:
            block("missing_consultation_teacher", "Назначьте преподавателя консультации")
        elif not teacher_ids and not sdep_independent and not is_optional and not is_event and _requires_physical_teacher(lesson, practice):
            blockers.append({"code": "missing_teacher", "lesson_id": lesson_id,
                             "message": "Преподаватель занятия не подтверждён", "severity": "info"})
        elif not teacher_ids and activity_type == "elective" and not teacher_evidence:
            blockers.append({"code": "missing_teacher", "lesson_id": lesson_id, "severity": "info",
                             "message": "У направления не указан преподаватель. Общий блок остаётся виден ученикам до подтверждения назначения."})
        if practice:
            teacher_ids = []
        elective_block_id = str(lesson.get("blockId") or "") if activity_type == "elective" else ""
        elective_student_ids: set[str] = set()
        if elective_block_id:
            elective_student_ids = elective_students.get((elective_block_id, day, f"{interval[0]}:{interval[1]}"), set())
        if not is_lunch and special != "no_lesson" and not informational_unassigned_event and not sdep_independent:
            occupancy.append({"id": lesson_id, "day": day, "interval": interval,
                              "teachers": set() if sdep_independent or practice else set(teacher_ids),
                              "students": (set(result.get("student_ids") or []) | elective_student_ids
                                           if activity_type == "elective" else set() if is_optional or sdep_independent or activity_type == "consultation"
                                           else set(result.get("student_ids") or [])),
                              "subject": subject, "room": "" if sdep_independent or online_only or room_key(lesson.get("room")) in {"онлайн", "online"} else room_key(lesson.get("room")),
                              "activity_type": "event" if is_event else activity_type,
                              "elective_block_id": elective_block_id,
                              "sdep_independent": sdep_independent})
        grade = str(rule.get("grade") or lesson.get("grade_scope") or "")
        if not grade and is_optional:
            grade = ",".join(sorted({str(value) for value in lesson.get("eligibleGrades") or []}, key=lambda value: int(value) if value.isdigit() else 99))
        class_ids = rule.get("class_ids") or []
        display_class_ids = lesson.get("classIds") or ([lesson["classId"]] if lesson.get("classId") else [])
        if is_optional and activity_type == "elective" and lesson.get("blockId"):
            cohort_key = (str(lesson["blockId"]), day, f"{interval[0]}:{interval[1]}")
            display_class_ids = sorted(elective_cohorts.get(cohort_key) or {str(value) for value in display_class_ids})
        if not grade:
            grades = {_class_grade(groups[g]) for g in class_ids or display_class_ids if g in groups}
            grades.discard(None)
            grade = ",".join(sorted(grades, key=int))
        if not grade:
            grades = {m.group(1) for sid in result.get("student_ids") or []
                      for m in [re.match(r"\s*(\d+)", students.get(str(sid), ""))] if m}
            grade = ",".join(sorted(grades, key=int))
        if not grade and result.get("student_ids") and not is_optional:
            block("missing_runtime_grade", "Student audience has no canonical grade scope")
        group_ids = list(rule.get("group_ids") or [])
        audience_rule: dict[str, Any]
        kind = rule.get("kind")
        if is_lunch and any(str(value) not in groups or groups[str(value)].get("group_type") != "class"
                            for value in display_classes):
            block("unknown_class", "Для обеда указан отсутствующий или недействующий класс размещения.")
        if is_lunch and not lunch_has_configured_audience:
            # A trusted class-grid lunch marker may have no separate audience.
            # Otherwise preserve the configured rule below (including exclusions).
            raw_lunch_classes = [str(value) for value in (display_classes or rule.get("class_ids") or [])]
            lunch_classes = [value for value in raw_lunch_classes
                             if str(value) in groups and groups[str(value)]["group_type"] == "class"]
            group_ids = lunch_classes
            audience_rule = {"kind": "groups", "group_ids": lunch_classes}
            if not raw_lunch_classes and grade:
                audience_rule = {"kind": "whole_grade", "grade": grade}
            elif not lunch_classes and not grade:
                block("missing_special_scope", "Lunch requires a canonical class or parallel")
        elif is_optional and activity_type == "elective":
            # Electives are shown to each explicitly covered class as a shared
            # block; they do not infer or persist a direction-level student list.
            group_ids = [str(value) for value in display_class_ids if str(value) in groups]
            audience_rule = {"kind": "groups", "group_ids": group_ids}
        elif kind in {"groups", "classes"}:
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
                selected_by_grade: dict[str, set[str]] = defaultdict(set)
                unknown_classes = []
                for class_id in {str(value) for value in class_ids}:
                    group = groups.get(class_id)
                    class_grade = _class_grade(group) if group and group.get("group_type") == "class" else None
                    if not class_grade:
                        unknown_classes.append(class_id)
                    else:
                        selected_by_grade[class_grade].add(class_id)
                declared_grades = {value for value in grade.split(",") if value}
                full_parallel_scope = bool(selected_by_grade) and all(
                    selected == {group_id for group_id, group in groups.items()
                                 if group["group_type"] == "class" and _class_grade(group) == class_grade}
                    for class_grade, selected in selected_by_grade.items()
                )
                if (unknown_classes or declared_grades != set(selected_by_grade)
                        or not full_parallel_scope):
                    block("unsupported_partial_cohort", "Remaining audience spans only part of a parallel; confirm an explicit participant list")
                elif kind == "available_slot" and len(selected_by_grade) > 1:
                    # Canonical available-slot rules have one grade/class
                    # context. Freeze the already reviewed multi-grade result
                    # instead of widening one grade into another at runtime.
                    saved_snapshot = lesson.get("audience_snapshot")
                    saved_ids = (saved_snapshot.get("student_ids")
                                 if isinstance(saved_snapshot, Mapping) else None)
                    resolved_ids = result.get("proposed_student_ids")
                    if (result.get("status") == "ok" and not result.get("needs_recalculation")
                            and isinstance(saved_ids, list) and isinstance(resolved_ids, list)
                            and {str(value) for value in saved_ids} == {str(value) for value in resolved_ids}):
                        audience_rule = {"kind": "students", "include_student_ids": sorted({
                            str(value) for value in saved_ids
                        })}
                    else:
                        block("unsupported_partial_cohort", "Multi-grade remaining audience requires a reviewed participant list")
                elif len(selected_by_grade) > 1:
                    block("unsupported_partial_cohort", "Legacy multi-grade remaining audience requires an explicit reviewed participant list")
        elif kind == "students":
            audience_rule = {"kind": "students", "include_student_ids": rule.get("student_ids") or []}
        elif informational_unassigned_event:
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
        elective_block_id = str(lesson.get("blockId") or "") if activity_type == "elective" else ""
        if activity_type == "consultation":
            elective_block_id = f"consultation:{lesson_id}"
            same_slot = False
        key = origin_key if same_slot else "constructor-" + _hash([day, start, end, grade, class_scope, elective_block_id])[:24]
        lesson_keys[lesson_id] = key
        contexts[lesson_id] = {"day": day, "start": start, "end": end, "subject": subject or "Обед", "grade_scope": grade,
            "class_ids": display_class_ids, "assignment_kind": kind or special or "unspecified",
            "teachers": [teachers[t] for t in teacher_ids if t in teachers],
            "groups": [str(groups[g]["name"]) for g in (group_ids or class_ids or display_class_ids) if g in groups]}
        if special not in {"lunch", "no_lesson"} and not is_optional and not is_event and not online_only and not sdep_independent and not str(lesson.get("room") or "").strip():
            blockers.append({"code": "missing_room", "lesson_id": lesson_id,
                             "message": "Кабинет не указан", "severity": "info"})
        role = "explicit_no_lesson" if special == "no_lesson" else "residual" if special == "lunch" or kind in {"available_slot", "legacy_remaining"} else "primary"
        preserved_metadata = deepcopy(canonical._assignment_metadata((origin.get("assignment") or {}).get("metadata") or {}))
        for duplicate in ("id", "block_id", "ordinal", "assignment_kind", "audience_kind",
                          "canonical_group_ids", "teacher_ids", "audience", "source_cells", "joint_activity_id"):
            preserved_metadata.pop(duplicate, None)
        if not practice and activity_type != "practice" and preserved_metadata.get("activity_format") == "independent_practice":
            for field in ("activity_format", "responsible_teacher_ids", "responsible_teachers"):
                preserved_metadata.pop(field, None)
        # Retain authored descriptive/activity fields separately from audience
        # rules; no UI preference or teacher-color field enters lesson payloads.
        constructor_details = {**deepcopy(preserved_metadata.get("constructor_details") or {}), **{k: deepcopy(lesson[k]) for k in (
            "description", "place", "format", "connectionInfo", "responsible_teacher_ids", "activityCatalogId", "activityNameSnapshot",
            "eligibleGrades", "confirmedParticipantIds", "electiveDirection", "audience_snapshot", "blockId", "blockName",
            "kind", "informational", "sdepIndependent", "allDay", "activityType") if k in lesson}}
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
                                   "activity_type": "event" if is_event else activity_type,
                                   "day_plan_mode": "sdep" if sdep_independent else "online" if online_only else "normal",
                                   **({"activity_format":"independent_practice", "responsible_teacher_ids":responsible_teacher_ids,
                                       "responsible_teachers":[teachers[t] for t in responsible_teacher_ids if t in teachers]} if practice or activity_type == "practice" else {}),
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
    joint_by_lesson: dict[str, str] = {}
    for lesson in lessons:
        lesson_id = str(lesson.get("id") or "")
        receipts = validated_exceptions(lessons, lesson, scope=reference_week or payload.get("week_start") or "template", slots=payload.get("slots") or [])
        joint_receipt = next((item for item in receipts if item.get("type") == "joint"), None)
        link = str(lesson.get("jointActivityId") or (joint_receipt or {}).get("id") or "")
        if link:
            joint_rows.setdefault(link, []).append(lesson_id)
    occupancy_by_id = {row["id"]: row for row in occupancy}
    for link, linked_ids in joint_rows.items():
        joint_occupancy = [occupancy_by_id[lid] for lid in linked_ids if lid in occupancy_by_id]
        signatures = {(row["day"], row["interval"], row["subject"], tuple(sorted(row["teachers"])), row["room"])
                      for row in joint_occupancy}
        explicitly_linked = len(linked_ids) == len(joint_occupancy) and len(linked_ids) > 1
        if explicitly_linked and len(signatures) == 1 and joint_occupancy[0]["teachers"] and joint_occupancy[0]["room"]:
            # A shared authored link or revision-bound administrator receipt is
            # required. Matching names/time/teacher/room alone never merges work.
            for lid in linked_ids:
                joint_by_lesson[lid] = link
            blockers.append({"code": "joint_activity", "severity": "info", "lesson_id": linked_ids[0], "related_lesson_ids": linked_ids})
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
            if (not {"consultation", "practice", "laboratory"}.intersection({left["activity_type"], right["activity_type"]}) and left["subject"] == right["subject"] and lesson_keys.get(left["id"]) == lesson_keys.get(right["id"])
                    and left["students"] == right["students"]
                    and (not left["teachers"] or not right["teachers"] or left["teachers"] == right["teachers"])
                    and (not left["room"] or not right["room"] or left["room"] == right["room"])):
                continue
            if left["id"] in joint_by_lesson and joint_by_lesson.get(left["id"]) == joint_by_lesson.get(right["id"]):
                continue
            event_overlay = left.get("activity_type") == "event" or right.get("activity_type") == "event"
            same_elective_block = bool(left.get("elective_block_id") and left.get("elective_block_id") == right.get("elective_block_id"))
            if left["students"] & right["students"] and not event_overlay and not same_elective_block:
                blockers.append({"code": "student_overlap", "lesson_id": right["id"], "related_lesson_ids": [left["id"], right["id"]],
                                 "affected_student_ids": sorted(left["students"] & right["students"]),
                                 "message": f"Conflicting student audience overlaps lesson {left['id']}"})
            if left["teachers"] & right["teachers"]:
                code = "joint_activity_unconfirmed" if left["subject"] == right["subject"] and left["interval"] == right["interval"] and left["teachers"] == right["teachers"] and (not left["room"] or not right["room"]) else "teacher_overlap"
                blockers.append({"code": code, "lesson_id": right["id"], "related_lesson_ids": [left["id"], right["id"]],
                                 "message": f"Teacher is assigned to simultaneous different activities ({left['id']})"})
            if left["room"] and left["room"] == right["room"]:
                blockers.append({"code": "room_overlap", "lesson_id": right["id"], "related_lesson_ids": [left["id"], right["id"]],
                                 "message": f"Room is assigned to simultaneous different activities ({left['id']})"})
    # Defer complete destination blocks and all pieces of a moved origin block.
    # Both sides of a collision are excluded; ordering never picks a winner.
    critical_codes = {"duplicate_lesson_id", "missing_lesson_id", "missing_origin_block", "invalid_slot", "protected", "protected_base"}
    excluded: set[str] = set()
    for issue in blockers:
        issue.setdefault("severity", "critical" if issue["code"] in critical_codes else "conflict")
        lid = issue.get("lesson_id")
        issue["context"] = contexts.get(lid, {})
        issue["block_key"] = lesson_keys.get(lid)
        if ((issue["severity"] == "conflict" and issue["code"] != "student_overlap")
                or issue["code"] == "stale_group_subject_mismatch"):
            excluded.update(lesson_keys[i] for i in issue.get("related_lesson_ids", [lid]) if i in lesson_keys)
    direct_excluded = set(excluded)
    changed = True
    while changed:
        changed = False
        connected = connected_units(excluded)
        if connected != excluded:
            excluded = connected
            changed = True
        # An unscoped item is excluded on its own. Sharing its time slot does
        # not make otherwise valid classes part of the same publication unit.
    for lid, key in sorted(lesson_keys.items()):
        if key in excluded:
            connected_keys = connected_units({key})
            causes = [issue for issue in blockers if ((issue["severity"]=="conflict" and issue["code"]!="student_overlap") or issue["code"]=="stale_group_subject_mismatch") and
                      any(lesson_keys.get(i) in connected_keys for i in issue.get("related_lesson_ids",[issue.get("lesson_id")]))]
            deferred.append({"lesson_id": lid, "block_key": key, "action": "excluded", "context": contexts[lid],
                "exclusion_scope": "direct" if key in direct_excluded else "cascade",
                "cause_codes": sorted({i["code"] for i in causes}),
                "cause_lesson_ids": sorted({i for issue in causes for i in issue.get("related_lesson_ids",[issue.get("lesson_id")]) if i}),
                "reason": "Непосредственно затронуто диагностикой; исходная запись остаётся в черновике" if key in direct_excluded
                    else "Исключено из-за атомарной связи с непосредственно затронутой записью; исходная запись остаётся в черновике"})
    for issue in blockers:
        issue["action"] = "excluded" if issue.get("block_key") in excluded else "blocked" if issue["severity"] == "critical" else "published"
    blocks = {key: value for key, value in blocks.items() if key not in excluded}
    invalid_projections: set[str] = set()
    conflicted_students_by_block: dict[str, set[str]] = {}
    for issue in blockers:
        if issue.get("code") != "student_overlap":
            continue
        related_keys = {lesson_keys[lesson_id] for lesson_id in issue.get("related_lesson_ids") or []
                        if lesson_id in lesson_keys}
        # Keep the exception scoped to the exact student and each concrete
        # block named by this verified overlap. The projector still rejects
        # every unrelated UNRESOLVED issue for that student.
        for block_key in related_keys:
            conflicted_students_by_block.setdefault(block_key, set()).update(
                str(sid) for sid in issue.get("affected_student_ids") or []
            )
    while blocks:
        invalid = _projection_mismatches(database, blocks, audience, reference, conflicted_students_by_block,
                                         directory=directory)
        if not invalid:
            break
        # Projector mismatches invalidate only the affected immutable block.
        # The mismatch identifies the full block; adjacent lessons stay intact.
        invalid = invalid & blocks.keys()
        invalid_projections.update(invalid)
        blocks = {key: value for key, value in blocks.items() if key not in invalid}
    for lid, key in sorted(lesson_keys.items()):
        if key in invalid_projections:
            blockers.append({"code": "projection_mismatch", "severity": "conflict", "lesson_id": lid,
                             "block_key": key, "context": contexts[lid], "action": "excluded"})
            deferred.append({"lesson_id": lid, "block_key": key, "context": contexts[lid], "action": "excluded",
                             "exclusion_scope": "direct",
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


def _published_source(payload: Mapping[str, Any], diagnostics: list, deferred: list,
                      reference_week: str | None, audiences: Mapping[str, Any],
                      teachers: set[str]) -> dict[str, Any]:
    """Archive authored truth separately from safe runtime assignments."""
    unresolved = {item["lesson_id"] for item in deferred}
    issues_by_lesson = defaultdict(list)
    for issue in diagnostics:
        for lesson_id in dict.fromkeys(issue.get("related_lesson_ids") or [issue.get("lesson_id")]):
            issues_by_lesson[lesson_id].append(issue)
    records = []
    for index,lesson in enumerate(payload.get("lessons") or []):
        lid = str(lesson.get("id") or index)
        issues = issues_by_lesson[lid]
        exceptions=validated_exceptions(payload.get("lessons") or [],lesson,scope=reference_week or payload.get("week_start") or "template",slots=payload.get("slots") or [])
        acknowledgements=[e for e in exceptions if e["type"] in {"joint","independent_practice"}]
        interval=_lesson_interval(lesson,payload.get("slots") or [])
        result=audiences.get(lid,{})
        reliable=result.get("status")=="ok" and not result.get("needs_recalculation")
        assigned_teachers=sorted(({str(t) for t in lesson.get("teacherIds") or []}|({str(lesson["teacherId"])} if lesson.get("teacherId") else set()))&teachers)
        if _is_lunch_lesson(lesson) or lesson.get("specialMark") == "no_lesson":
            assigned_teachers = []
        practice=any(e["type"]=="independent_practice" for e in acknowledgements)
        records.append({"lesson_id": lid, "status": "unresolved" if lid in unresolved else "acknowledged_exception" if acknowledgements else "resolved_with_remarks" if issues else "resolved",
                        "day": _lesson_weekday(lesson),
                        "start":f"{interval[0]//60:02d}:{interval[0]%60:02d}" if interval else None,
                        "end":f"{interval[1]//60:02d}:{interval[1]%60:02d}" if interval else None,
                        "known_student_ids":result.get("student_ids",[]) if reliable else [],
                        "known_teacher_ids":[] if practice else assigned_teachers,
                        "responsible_teacher_ids":assigned_teachers if practice else [],
                        "acknowledged_exceptions":deepcopy(acknowledgements),
                        "diagnostic_codes": sorted({issue["code"] for issue in issues}),
                        "conflict_student_ids": sorted({str(sid) for issue in issues if issue.get("code") == "student_overlap"
                                                         for sid in issue.get("affected_student_ids") or []}),
                        "related_lesson_ids": sorted({str(i) for issue in issues for i in issue.get("related_lesson_ids", []) if str(i)!=lid})})
    return {"schema_version": "published-source-v1", "payload_fingerprint": _hash(payload),
            "payload": deepcopy(dict(payload)), "records": records}


def _elective_delivery_records(database: Any, blocks: Mapping[str, Any], directory: _PublicationDirectory,
                               reference: date) -> list[dict[str, Any]]:
    """Project shared elective cards without inventing direction enrolments."""
    if not blocks:
        return []
    candidates = {key: {**block, "block_key": key, "start_time": block["slot"]["start"],
                        "end_time": block["slot"]["end"], "patch": {"assignments": block["assignments"]}}
                  for key, block in blocks.items()}
    context = canonical.StudentProjectionContext({}, directory.students, directory.groups,
                                                 directory.memberships, directory.overrides,
                                                 list(candidates.values()))
    records = []
    for key, candidate in candidates.items():
        assignments = candidate.get("patch", {}).get("assignments") or []
        elective = []
        for assignment in assignments:
            metadata = canonical._assignment_metadata(assignment.get("metadata") or {})
            details = metadata.get("constructor_details") if isinstance(metadata.get("constructor_details"), Mapping) else {}
            if (metadata.get("activity_type") or details.get("activityType")) == "elective":
                elective.append((assignment, details))
        if not elective:
            continue
        grades = {value for value in str(candidate.get("grade_scope") or "").split(",") if value}
        day = (reference + timedelta(days=int(candidate["weekday"]))).isoformat()
        student_ids = []
        unresolved_student_ids = []
        label = ""
        for student_id, student in directory.students.items():
            if student.get("status") != "active" or canonical._projection_grade(context, student) not in grades:
                continue
            projected, _ = canonical._project_student_block(database, context, student, candidate, day)
            if projected.get("state") == "ACTIVITY" and projected.get("activity_type") == "elective":
                student_ids.append(str(student_id))
                label = label or str(projected.get("activity") or "Курс по выбору")
            elif projected.get("state") == "UNRESOLVED":
                unresolved_student_ids.append(str(student_id))
        source_ids = [str((assignment.get("metadata") or {}).get("constructor_lesson_id") or "")
                      for assignment, _ in elective]
        source_ids = [value for value in source_ids if value]
        if not source_ids:
            continue
        records.append({"delivery_id": f"elective:{key}", "lesson_id": source_ids[0],
                        "source_lesson_ids": source_ids, "status": "resolved",
                        "known_student_ids": sorted(student_ids),
                        "unresolved_student_ids": sorted(unresolved_student_ids),
                        "display_label": label or next((str(details.get("blockName") or "") for _, details in elective
                                                        if details.get("blockName")), "Курс по выбору")})
    return records


def preview_publication(database: Any, draft_id: str, *, scope_kind: str,
                        expected_revision: int, week_start: str | None = None,
                        draft_database: Any = None) -> dict[str, Any]:
    split_drafts = draft_database if draft_database is not database else None
    if isinstance(database, _TransactionDatabase):
        result, _, _ = _build_publication_preview(database, draft_id, scope_kind, expected_revision, week_start, split_drafts)
        return result
    connect_started = perf_counter()
    with database.connection() as connection:
        connect_seconds = perf_counter() - connect_started
        if not database.database_url:
            database.execute(connection, "BEGIN")
        else:
            if not getattr(database, "read_only", False):
                if database.autocommit:
                    connection.autocommit = False
                database.execute(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            database.execute(connection, "SET LOCAL statement_timeout = '20s'")
        result, _, _ = _build_publication_preview(
            _TransactionDatabase(database, connection), draft_id, scope_kind, expected_revision, week_start, split_drafts)
    if connect_seconds >= 2:
        logging.getLogger(__name__).warning("schedule preview database connection took %.0f ms", connect_seconds * 1000)
    return result


def _build_publication_preview(database: Any, draft_id: str, scope_kind: str, expected_revision: int,
                               week_start: str | None, draft_database: Any = None
                               ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    started = perf_counter()
    scope_key = _scope(scope_kind, week_start)
    draft = get_experiment(draft_database or database, draft_id)
    if int(draft["revision"]) != int(expected_revision):
        raise PublicationConflict("Draft revision changed; reload before publication")
    if draft["payload"].get("draft_archive"):
        raise ValueError("Восстановите черновик из архива перед публикацией.")
    authored_week = draft["payload"].get("week_start")
    if authored_week and (scope_kind != "week" or week_start != authored_week):
        raise ValueError("Черновик недели можно опубликовать только на её исходные даты.")
    version = canonical.canonical_version(database)
    base_id = str(draft.get("base_version_id") or (version or {}).get("version_id") or "")
    base = canonical.read_canonical_template(database, base_id) if base_id else None
    head = canonical.publication_head(database, scope_key)
    effective = canonical._effective_week(database, None, week_start) if scope_kind == "week" else None
    directory = _PublicationDirectory.load(database)
    loaded_at = perf_counter()
    audience_snapshot: dict[str, Any] = {}
    blocks, diagnostics, deferred = _compile(database, draft["payload"], base, scope_kind,
                                         week_start if scope_kind == "week" else None,
                                         audience_output=audience_snapshot, directory=directory)
    compiled_at = perf_counter()
    for conflict in payload_day_mode_conflicts(database, draft["payload"], groups=directory.all_groups):
        diagnostics.append({"code": "day_mode_conflict", "severity": "critical",
                            "grade": conflict["grade"], "weekday": conflict["weekday"],
                            "class_ids": conflict["class_ids"],
                            "message": f"Режимы дня для {conflict['grade']} параллели различаются; выберите общий режим"})
    if version and base_id != version["version_id"]:
        diagnostics.append({"code": "stale_base", "severity": "critical"})
    if scope_kind == "week" and not base:
        diagnostics.append({"code": "missing_base", "severity": "critical"})
    messages_by_lesson = {}
    for issue in diagnostics:
        if issue.get("action") == "excluded":
            issue["action"] = "retained_unresolved"
        code = issue["code"]
        message = issue.get("message") if code in {"review_required", "stale_group_subject_mismatch", "elective_block_unlinked",
            "elective_block_name_conflict", "unknown_teacher", "missing_teacher", "missing_consultation_teacher", "day_mode_conflict"} else None
        issue["message"] = message or _ISSUE_MESSAGES.get(code, "Проверьте исходную запись в конструкторе")
        for lesson_id in issue.get("related_lesson_ids") or [issue.get("lesson_id")]:
            messages_by_lesson.setdefault((lesson_id, code), issue["message"])
    for entry in deferred:
        entry["action"] = "retained_unresolved"
        related = [entry["lesson_id"], *(entry.get("cause_lesson_ids") or [])]
        messages = [next((messages_by_lesson[(lesson_id, code)] for lesson_id in related
                          if (lesson_id, code) in messages_by_lesson), _ISSUE_MESSAGES.get(code, ""))
                    for code in entry.get("cause_codes") or []]
        cause = " · ".join(dict.fromkeys(message for message in messages if message))
        entry["reason"] = cause or "Нужно уточнить состав участников."
        if entry.get("exclusion_scope") == "cascade":
            entry["reason"] = f"Связанное занятие требует проверки. {entry['reason']}"
    blockers = [issue for issue in diagnostics if issue["severity"] == "critical"]
    before = (base or {}).get("blocks") or {}
    if scope_kind == "week" and effective:
        before = {row["block_key"]: {**row, "assignments": canonical._assignments(database, row.get("canonical_block_id"), row.get("patch") or {})}
                  for row in canonical._block_rows(database, effective["effective_week_id"]) if row.get("change_kind") != "cancelled"}
    changes = _change_items(before, blocks)
    change_counts = {kind: sum(item["operation"] == operation for item in changes)
                     for kind, operation in (("added", "add"), ("removed", "remove"), ("changed", "change"))}
    diff = {**change_counts,
            "before_blocks": len(before), "after_blocks": len(blocks)}
    unchanged = (blocks.keys() & before.keys()) - {change["block_key"] for change in changes}
    summary = {"ready_lessons": sum(len(b.get("assignments") or []) for b in blocks.values()),
               "deferred_lessons": len(deferred), "deferred_blocks": len({item["block_key"] for item in deferred}),
               "unchanged_lessons": sum(len(blocks[k].get("assignments") or []) for k in unchanged),
               "replaced_lessons": sum(len(before[k].get("assignments") or []) for k in blocks.keys() & before.keys() - unchanged),
               **{f"{severity}_count": sum(d["severity"] == severity for d in diagnostics) for severity in ("info", "conflict", "critical")}}
    published_source = _published_source(draft["payload"], diagnostics, deferred,
                                         week_start if scope_kind=="week" else None,
                                         audience_snapshot, set(directory.teachers))
    reference = date.fromisoformat(str(week_start or draft["payload"].get("reference_date")
                                         or draft["payload"].get("week_start") or date.today())[:10])
    reference -= timedelta(days=reference.weekday())
    published_source["elective_delivery_records"] = _elective_delivery_records(
        database, blocks, directory, reference)
    elective_lane_count = 0
    elective_card_keys: set[tuple[str, str]] = set()
    ordinary_campus_cards = 0
    for block_key, compiled_block in blocks.items():
        for assignment in compiled_block.get("assignments") or []:
            metadata = assignment.get("metadata") or {}
            activity_type = str(metadata.get("activity_type") or "lesson")
            details = metadata.get("constructor_details") if isinstance(metadata.get("constructor_details"), Mapping) else {}
            if activity_type == "club" or assignment.get("activity") == "NO_LESSON":
                continue
            if activity_type == "elective":
                elective_lane_count += 1
                if details.get("blockId"):
                    elective_card_keys.add((block_key, str(details["blockId"])))
                continue
            ordinary_campus_cards += 1
    main_campus_cards = ordinary_campus_cards + len(elective_card_keys)
    source_records = published_source.get("records") or []
    source_activity_types = {str(lesson.get("id") or index): _lesson_activity_type(lesson)
                             for index, lesson in enumerate(draft["payload"].get("lessons") or [])}
    deferred_personal = sum(source_activity_types.get(str(row.get("lesson_id")), "lesson") not in {"club", "elective"}
                            and row.get("context", {}).get("assignment_kind") not in {"lunch", "no_lesson"}
                            for row in deferred)
    summary.update(source_lessons=len(published_source["records"]), resolved_lessons=summary["ready_lessons"],
                   unresolved_lessons=len(deferred), campus_lessons=main_campus_cards, admin_only_lessons=len(deferred),
                   main_campus_cards=main_campus_cards, elective_blocks=len(elective_card_keys),
                   elective_lanes=elective_lane_count,
                   clubs_hidden=sum(value == "club" for value in source_activity_types.values()),
                   unresolved_personal_assignments=deferred_personal,
                   non_blocking_warnings=sum(item.get("severity") == "info" for item in diagnostics),
                   with_remarks_lessons=sum(r["status"]=="resolved_with_remarks" for r in published_source["records"]),
                   confirmed_lessons=sum(r["status"]=="resolved" for r in published_source["records"]),
                   acknowledged_exception_lessons=sum(r["status"]=="acknowledged_exception" for r in published_source["records"]),
                   acknowledged_exceptions=len({e["id"] for r in published_source["records"] for e in r["acknowledged_exceptions"]}))
    conflict_diagnostics = [item for item in diagnostics if item.get("severity") == "conflict"]
    summary.update(
        diagnostic_events=len(diagnostics),
        conflict_lessons=len({str(value) for item in conflict_diagnostics
                              for value in [item.get("lesson_id"), *(item.get("related_lesson_ids") or [])] if value}),
        affected_students=len({str(value) for item in conflict_diagnostics for value in item.get("affected_student_ids") or []}),
        day_mode_issues=sum(item.get("code") in {"day_mode_conflict", "mixed_day_modes_unsupported"} for item in diagnostics),
        direct_exclusions=len({item["lesson_id"] for item in deferred if item.get("exclusion_scope") == "direct"}),
        cascade_exclusions=len({item["lesson_id"] for item in deferred if item.get("exclusion_scope") == "cascade"}),
    )
    result = {"draft_id": draft_id, "draft_revision": draft["revision"], "scope_kind": scope_kind,
              "scope_key": scope_key, "week_start": week_start if scope_kind == "week" else None,
              "base_version_id": base_id or None, "effective_week_id": (effective or {}).get("effective_week_id"),
              "head_revision": (head or {}).get("revision", 0), "previous_published_id": (head or {}).get("published_id"),
              "payload_fingerprint": published_source["payload_fingerprint"], "directory_fingerprint": directory.fingerprint,
              "can_publish": not blockers, "blockers": blockers, "deferred": deferred, "diff": diff,
              "diagnostics": diagnostics, "summary": summary, "published_source": published_source,
              "source_kind": "template" if scope_kind == "template" else "authored_week" if draft["payload"].get("week_start") == week_start else "template_week",
              "change_items": changes,
              "source_policy": "manual_head_protected_source_refresh_requires_explicit_review"}
    result["read_only"] = bool(getattr(database, "read_only", False)) or (draft_database is not None and draft_database is not database)
    result["publication_available"] = not result["read_only"]
    result["preview_token"] = _hash({**result, "blocks": blocks})
    elapsed = perf_counter() - started
    if elapsed >= 5:
        logging.getLogger(__name__).warning(
            "slow schedule preview scope=%s lessons=%d load_ms=%.0f compile_ms=%.0f total_ms=%.0f",
            scope_kind, summary["source_lessons"], (loaded_at - started) * 1000,
            (compiled_at - loaded_at) * 1000, elapsed * 1000)
    return result, blocks, draft


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
        "block_count": len(actual), "assignment_count": sum(len(b.get("assignments") or []) for b in actual.values()), "verified": True,
        "source_verified": bool(preview.get("published_source")), "source_record_count": len((preview.get("published_source") or {}).get("records") or [])}
    metadata = {"preview": preview, "blocks": blocks, "runtime_readback": readback_result,
                "published_source": preview.get("published_source"),
                "restored_from": restored_from}
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO schedule_constructor_publications
            (id,draft_id,draft_revision,scope_kind,scope_key,base_version_id,previous_published_id,published_id,version_id,payload_fingerprint,actor_user_id,created_at,metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (publication_id, preview["draft_id"], preview["draft_revision"],
            preview["scope_kind"], preview["scope_key"], preview["base_version_id"], preview["previous_published_id"],
            published_id, version_id, preview["payload_fingerprint"], str(actor) if actor is not None else None,
            now, json.dumps(metadata, ensure_ascii=False, default=str)))
        saved = database.execute(connection, "SELECT metadata FROM schedule_constructor_publications WHERE id=?", (publication_id,)).fetchone()
        archived = json.loads(saved["metadata"]).get("published_source")
        if archived != preview.get("published_source"):
            raise RuntimeError("Published source readback failed; transaction rolled back")
        if archived and (_hash(archived["payload"]) != archived["payload_fingerprint"] or
                         len(archived["records"]) != len(archived["payload"].get("lessons") or [])):
            raise RuntimeError("Published source integrity failed; transaction rolled back")
        from backend.services.published_week_sheets import enabled, enqueue
        sheet_delivery_queued = enabled() and preview["scope_kind"] == "week"
        if sheet_delivery_queued:
            enqueue(database, connection, preview["week_start"], publication_id)
    return {"status": "published", "sheet_delivery_queued": sheet_delivery_queued, "publication_id": publication_id, "scope_kind": preview["scope_kind"],
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


def _receipt(database, connection, operation_id, actor_key, request_hash):
    row = database.execute(connection, """SELECT actor_key, request_hash, result_json
        FROM schedule_publication_receipts WHERE operation_id=?""", (operation_id,)).fetchone()
    if row is None:
        return None
    if actor_key != row["actor_key"] or request_hash != row["request_hash"]:
        raise PublicationConflict("Operation ID already used for another request")
    return json.loads(row["result_json"])


def publication_receipt(database, operation_id, actor):
    with database.connection() as connection:
        row = database.execute(connection, """SELECT result_json FROM schedule_publication_receipts
            WHERE operation_id=? AND actor_key=?""",
            (operation_id, str(actor) if actor is not None else "")).fetchone()
    return json.loads(row["result_json"]) if row else None


def _run_command(database, operation_id, actor, request, execute):
    actor_key = str(actor) if actor is not None else ""
    fingerprint = _hash(request)
    try:
        with _transaction(database) as bound:
            if operation_id:
                existing = _receipt(bound, bound._connection, operation_id, actor_key, fingerprint)
                if existing is not None:
                    return existing
            result = execute(bound)
            if operation_id:
                result["operation_id"] = operation_id
                bound.execute(bound._connection, """INSERT INTO schedule_publication_receipts
                    (operation_id, actor_key, request_hash, result_json) VALUES (?,?,?,?)""",
                    (operation_id, actor_key, fingerprint, json.dumps(result, default=str)))
            return result
    except PublicationConflict:
        # A SERIALIZABLE snapshot may predate the winner's commit. Read the
        # receipt on a fresh connection only after this transaction rolled back.
        if operation_id:
            with database.connection() as connection:
                existing = _receipt(database, connection, operation_id, actor_key, fingerprint)
                if existing is not None:
                    return existing
        raise


def publish_publication(database: Any, draft_id: str, *, scope_kind: str, expected_revision: int,
                        preview_token: str, confirmed: bool = False, week_start: str | None = None,
                        actor_user_id: Any = None, operation_id: str | None = None) -> dict[str, Any]:
    if confirmed is not True or not preview_token:
        raise ValueError("Explicit confirmation and preview_token are required")
    request = ("publish", draft_id, scope_kind, expected_revision, week_start, preview_token)

    def execute(bound):
        # Lock the actual saved revision against simultaneous constructor saves.
        if database.database_url:
            database.execute(bound._connection, "SELECT id FROM schedule_experiment_drafts WHERE id=? FOR UPDATE", (draft_id,))
        preview, blocks, draft = _build_publication_preview(bound, draft_id, scope_kind, expected_revision, week_start)
        if preview["preview_token"] != preview_token:
            raise PublicationConflict("Preview is stale; review the new diff before confirming")
        if not preview["can_publish"]:
            raise PublicationBlocked(preview)
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

    return _run_command(database, operation_id, actor_user_id, request, execute)


def list_publications(database: Any, draft_id: str | None = None) -> dict[str, Any]:
    with database.connection() as connection:
        query = "SELECT * FROM schedule_constructor_publications"
        rows = database.execute(connection, query + (" WHERE draft_id=?" if draft_id else "") + " ORDER BY created_at DESC,id DESC LIMIT 100",
                                (draft_id,) if draft_id else ()).fetchall()
        heads = {row["scope_key"]: dict(row) for row in database.execute(connection,
            "SELECT * FROM schedule_publication_heads ORDER BY scope_key").fetchall()}
        from backend.services.published_week_sheets import enabled
        deliveries = {}
        if rows and enabled():
            ids = [r["id"] for r in rows]
            params = ",".join("?" for _ in ids)
            for job in database.execute(connection, f"""SELECT j.publication_id,j.state,j.attempts,j.error_code,
                    j.head_revision,b.delivered_revision FROM published_week_sheet_jobs j
                    JOIN published_week_sheet_bindings b USING(target_id,week_start)
                    WHERE j.publication_id IN ({params})""", tuple(ids)).fetchall():
                deliveries[job["publication_id"]] = dict(job)
    result = []
    for row in rows:
        item = dict(row)
        metadata = json.loads(item.pop("metadata"))
        if item["id"] in deliveries:
            item["sheet_delivery"] = deliveries[item["id"]]
        item["runtime_readback"] = metadata["runtime_readback"]
        item["restored_from"] = metadata.get("restored_from")
        item["diagnostics"] = metadata.get("preview", {}).get("diagnostics", [])
        item["deferred"] = [{**entry, "action": entry.get("action") or (
            "preserved_base" if entry.get("reason") == "Unresolved change retained in draft; base block preserved"
            else "unknown")} for entry in metadata.get("preview", {}).get("deferred", [])]
        item["summary"] = metadata.get("preview", {}).get("summary")
        item["published_source_available"] = bool(metadata.get("published_source"))
        result.append(item)
    return {"items": result, "heads": heads}


def get_published_source(database: Any, publication_id: str) -> dict[str, Any]:
    """Admin service: legacy history is explicitly unavailable, never reconstructed."""
    with database.connection() as connection:
        row = database.execute(connection, "SELECT metadata,payload_fingerprint FROM schedule_constructor_publications WHERE id=?", (publication_id,)).fetchone()
    if not row:
        raise ValueError("Publication not found")
    source = _checked_published_source(json.loads(row["metadata"]), row["payload_fingerprint"])
    return {"publication_id": publication_id, "available": bool(source), "published_source": source}


def _checked_published_source(metadata: Mapping[str, Any], payload_fingerprint: str) -> dict | None:
    """Fail closed on damaged new archives; preserve archive-free legacy history."""
    source = metadata.get("published_source")
    preview_source = (metadata.get("preview") or {}).get("published_source")
    if source is None and preview_source is None:
        return None
    valid = isinstance(source, dict) and source == preview_source
    if valid:
        payload = source.get("payload")
        records = source.get("records")
        valid = isinstance(payload, dict) and isinstance(records, list)
    if valid:
        lesson_ids = [str(lesson.get("id") or index) for index, lesson in enumerate(payload.get("lessons") or [])]
        record_ids = [str(record.get("lesson_id")) for record in records]
        valid = (_hash(payload) == source.get("payload_fingerprint") == payload_fingerprint
                 and len(lesson_ids) == len(set(lesson_ids))
                 and sorted(lesson_ids) == sorted(record_ids))
    if not valid:
        raise ValueError("Нарушена целостность опубликованного источника. Восстановление заблокировано.")
    return source


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
    try:
        archived_source = _checked_published_source(metadata, item["payload_fingerprint"])
    except ValueError as error:
        archived_source = None
        blockers.append({"code": "published_source_integrity", "severity": "critical", "message": str(error)})
    directory = _PublicationDirectory.load(database)
    teachers, groups, students = set(directory.teachers), set(directory.groups), set(directory.students)
    start = date.fromisoformat(str(metadata["preview"].get("week_start") or date.today()))
    start -= timedelta(days=start.weekday())
    members_by_day: dict[str, dict[str, set[str]]] = {}

    def memberships_on(day: str) -> dict[str, set[str]]:
        cached = members_by_day.get(day)
        if cached is not None:
            return cached
        result: dict[str, set[str]] = defaultdict(set)
        snapshot = directory.audience_snapshot(day)
        for row in snapshot["memberships"]:
            result[str(row["group_id"])].add(str(row["identity_id"]))
        excluded: dict[str, set[str]] = defaultdict(set)
        for row in snapshot["membership_overrides"]:
            group_id, student_id = str(row["group_id"]), str(row["identity_id"])
            if row["action"] == "include" and student_id in students:
                result[group_id].add(student_id)
            elif row["action"] == "exclude":
                excluded[group_id].add(student_id)
        for group_id, student_ids in excluded.items():
            result[group_id].difference_update(student_ids)
        members_by_day[day] = result
        return result

    for key, b in target.items():
        actual_day = (start + timedelta(days=int(b.get("weekday") or 0))).isoformat()
        members = memberships_on(actual_day)
        for a in b.get("assignments") or []:
            rule = (a.get("metadata") or {}).get("audience_rule") or {}
            responsible = (a.get("metadata") or {}).get("responsible_teacher_ids") or []
            if (set(str(i) for i in a.get("teacher_ids") or []) | set(str(i) for i in responsible)) - teachers:
                blockers.append({"code": "unknown_teacher", "message": f"Historical teacher missing/inactive in {key}"})
            if set(str(i) for i in a.get("canonical_group_ids") or []) - groups:
                blockers.append({"code": "unknown_group", "message": f"Historical group missing/noncanonical in {key}"})
            if set(str(i) for i in rule.get("include_student_ids") or []) - students:
                blockers.append({"code": "unknown_student", "message": f"Historical participant missing/inactive in {key}"})
            group_ids = set(str(i) for i in a.get("canonical_group_ids") or [])
            if rule.get("class_group_id"):
                group_ids.add(str(rule["class_group_id"]))
            for group_id in group_ids & groups:
                if not members.get(group_id) and (a.get("metadata") or {}).get("special_mark") not in {"lunch", "no_lesson"}:
                    blockers.append({"code": "unknown_membership", "message": f"Historical group {group_id} has no confirmed membership for {actual_day}"})
    for key, b in current.items():
        protected = (b.get("status") == "protected" or (b.get("evidence") or {}).get("protected")
                     or any((a.get("metadata") or {}).get("protected") for a in b.get("assignments") or []))
        if protected and (key not in target or _semantic(b) != _semantic(target[key])):
            blockers.append({"code": "protected_base", "message": f"Restore would replace protected block {key}"})
    result = {"publication_id": publication_id, "scope_kind": item["scope_kind"],
              "scope_key": item["scope_key"], "current_published_id": head["published_id"],
              "target_published_id": item["published_id"], "expected_head_revision": head["revision"],
              "directory_fingerprint": directory.fingerprint,
              "target_source_fingerprint": _hash(archived_source) if archived_source else None,
              "can_restore": not blockers, "blockers": blockers,
              "change_items": _change_items(current, target),
              "diff": {"added": len(target.keys() - current.keys()), "removed": len(current.keys() - target.keys()),
                       "changed": sum(_semantic(current[k]) != _semantic(target[k]) for k in target.keys() & current.keys()),
                       "before_blocks": len(current), "after_blocks": len(target)}}
    result["preview_token"] = _hash(result)
    return result


def restore_publication(database: Any, publication_id: str, *, expected_head_revision: int,
                        preview_token: str, confirmed: bool = False, actor_user_id: Any = None,
                        operation_id: str | None = None) -> dict[str, Any]:
    """Restore as a NEW immutable artifact with a current-head CAS guard."""
    if confirmed is not True or not preview_token:
        raise ValueError("Explicit restore confirmation is required")
    request = ("restore", publication_id, expected_head_revision, preview_token)

    def execute(bound):
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

    return _run_command(database, operation_id, actor_user_id, request, execute)
