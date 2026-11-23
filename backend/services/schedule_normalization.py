from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date
from typing import Any

from backend.services.schedule_edit_validation import CLUB_LESSON_ONLY_NOTES, is_legacy_group_link_note, normalized_note, notes, without_legacy_group_notes


def payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verified_teacher_directory_evidence(database: Any, draft: Mapping[str, Any]) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Read active teacher identities and active group assignments in one query."""
    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    raw_reference = str(payload.get("week_start") or payload.get("reference_date") or date.today().isoformat())[:10]
    try:
        reference = date.fromisoformat(raw_reference)
    except ValueError:
        reference = date.today()
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT i.id AS teacher_id,ta.group_id,ta.subject
            FROM identities i
            LEFT JOIN teacher_assignments ta ON ta.teacher_identity_id=i.id AND ta.active IS TRUE
              AND (ta.valid_from IS NULL OR ta.valid_from <= ?)
              AND (ta.valid_until IS NULL OR ta.valid_until >= ?)
            LEFT JOIN groups g ON g.id=ta.group_id AND g.archived_at IS NULL
            WHERE i.kind='teacher' AND i.status='active' AND (ta.id IS NULL OR g.id IS NOT NULL)""", (reference, reference)).fetchall()
    active_teachers = {str(row["teacher_id"]) for row in rows}
    assignments = {(str(row["teacher_id"]), str(row["group_id"]), " ".join(str(row["subject"] or "").casefold().split()))
                   for row in rows if row["group_id"] is not None}
    return active_teachers, assignments


def verified_teacher_assignment_pairs(database: Any, draft: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    return verified_teacher_directory_evidence(database, draft)[1]


LUNCH_SCOPE_CLAUSE = "Уточните охват обеда: класс или отдельная группа"
ELECTIVE_ROSTER_CLAUSES = frozenset({
    "Состав направления курса по выбору не подтверждён",
    "Не подтверждена учебная группа или аудитория курса",
    "Направление курса не имеет отдельного подтверждённого roster",
})


def verified_administrative_classes(database: Any) -> dict[str, str]:
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT id,name,display_name,base_class_name FROM groups
            WHERE canonical IS TRUE AND group_type='class' AND archived_at IS NULL""").fetchall()
    from backend.services.schedule_experiments import _grade_number
    return {str(row["id"]): next((n for key in ("base_class_name", "display_name", "name")
                                  if (n := _grade_number(row[key]))), "") for row in rows}


def scope_warning_evidence(payload: Mapping[str, Any], classes: Mapping[str, str], *,
                           unresolved_electives: dict[str, str] | None = None) -> dict[str, tuple[str, frozenset[str]]]:
    """Exact legacy clauses that active directory/constructor evidence supersedes.

    This is a warning projection, not a roster inference or a block assignment.
    """
    from backend.services.schedule_experiments import _is_lunch_lesson, _lesson_activity_type, _lesson_interval, _lesson_weekday

    lessons = [r for r in (payload.get("lessons") or []) if isinstance(r, Mapping)]
    blocks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in lessons:
        block_id = row.get("blockId")
        if isinstance(block_id, str) and block_id.strip():
            blocks[block_id].append(row)
    slots = payload.get("slots") or []

    def ids(row: Mapping[str, Any], list_key: str, singular: str) -> set[str]:
        raw = row.get(list_key)
        return ({str(value) for value in raw if value} if isinstance(raw, list) else set()) | (
            {str(row[singular])} if row.get(singular) else set())

    def scope(row: Mapping[str, Any]) -> set[str]:
        listed = ids(row, "classIds", "")
        singular = str(row.get("classId") or "")
        if singular and listed and singular not in listed:
            return set()
        return listed or ({singular} if singular else set())

    def block_problem(row: Mapping[str, Any]) -> str:
        block_id = row.get("blockId")
        if not isinstance(block_id, str) or not block_id.strip() or block_id != block_id.strip():
            return "Не указан подтверждённый общий блок"
        day = _lesson_weekday(row)
        interval = _lesson_interval(row, slots)
        if day is None or not interval:
            return "Не подтверждён день или интервал общего блока"
        for peer in blocks[block_id]:
            if (_lesson_activity_type(peer) != "elective" or _is_lunch_lesson(peer) or peer.get("importError")):
                return "В общем блоке есть запись другого типа или ошибка импорта"
            if (_lesson_weekday(peer) != day or _lesson_interval(peer, slots) != interval):
                return "Направления общего блока стоят в разных временных интервалах"
            class_ids = scope(peer)
            if not class_ids or not class_ids <= classes.keys():
                return "Есть направление без подтверждённой привязки к действующему классу"
            eligibility = peer.get("eligibleGrades")
            if eligibility and (not isinstance(eligibility, list) or not all(
                    classes[c] and classes[c] in {str(g) for g in eligibility} for c in class_ids)):
                return "Класс не соответствует допустимым параллелям направления"
        return ""

    result = {}
    for row in lessons:
        if not row.get("id") or row.get("needsClarification") is not True or row.get("importError"):
            continue
        clauses = frozenset(str(row.get("clarificationReason") or "").strip().split("; "))
        class_ids = scope(row)
        if _is_lunch_lesson(row) and LUNCH_SCOPE_CLAUSE in clauses:
            if not class_ids or not class_ids <= classes.keys():
                continue
            raw = row.get("audience") or {}
            if not isinstance(raw, Mapping) or raw.get("kind") not in {None, "", "classes"}:
                continue
            raw_classes = raw.get("class_ids") or raw.get("classIds") or []
            if raw_classes and (not isinstance(raw_classes, list) or {str(c) for c in raw_classes} != class_ids):
                continue
            if (ids(row, "groupIds", "groupId") - class_ids or raw.get("grade") or row.get("grade_scope")
                    or row.get("audienceMode") == "remainder" or row.get("excludedGroupIds")
                    or any(raw.get(key) for key in ("group_ids", "groupIds", "student_ids", "studentIds",
                                                    "include_student_ids", "exclude_student_ids", "partition_group_ids", "partition_dimension"))):
                continue
            result[str(row["id"])] = ("lunch_scope_warning", frozenset({LUNCH_SCOPE_CLAUSE}))
        elif (_lesson_activity_type(row) == "elective" and not _is_lunch_lesson(row)
              and (present := clauses & ELECTIVE_ROSTER_CLAUSES)):
            problem = block_problem(row)
            if not problem:
                result[str(row["id"])] = ("elective_scope_warning", present)
            elif unresolved_electives is not None:
                unresolved_electives[str(row["id"])] = problem
    return result


def without_scope_clauses(reason: Any, clauses: frozenset[str]) -> str:
    return "; ".join(part for part in str(reason or "").strip().split("; ") if part not in clauses)


def apply_scope_warning_retirement(lesson: dict[str, Any], field: str, clauses: frozenset[str]) -> None:
    if field != "elective_scope_warning":
        return
    remaining = without_scope_clauses(lesson.get("clarificationReason"), clauses)
    if remaining:
        lesson["clarificationReason"] = remaining
    else:
        lesson.pop("needsClarification", None)
        lesson.pop("clarificationReason", None)
    lesson["scope_warning_normalization"] = {
        "code": "elective_class_block_verified",
        "removed_clauses": sorted(clauses),
    }


def verified_saved_group_warning_ids(database: Any, draft: Mapping[str, Any]) -> set[str]:
    from backend.services.schedule_experiments import experiment_week_audience_preview

    payload = draft["payload"]
    ids = {str(row["id"]) for row in (payload.get("lessons") or [])
           if row.get("id") and row.get("needsClarification") is True and is_legacy_group_link_note(row)}
    if not ids:
        return set()
    reference = date.fromisoformat(str(payload.get("week_start") or payload.get("reference_date") or date.today().isoformat())[:10])
    resolved = experiment_week_audience_preview(database, payload, reference)["lessons"]
    return {lesson_id for lesson_id in ids if resolved.get(lesson_id, {}).get("verified_saved_group")}


def normalization_preview(draft: Mapping[str, Any], *, verified_active_teachers: set[str] | None = None,
                          verified_teacher_assignments: set[tuple[str, str, str]] | None = None,
                          verified_classes: Mapping[str, str] | None = None,
                          verified_group_links: set[str] | None = None) -> dict[str, Any]:
    """Plan only evidence-backed legacy type updates; all other rows are preserved."""
    from backend.services.schedule_experiments import _lesson_activity_type

    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    lessons = payload.get("lessons") if isinstance(payload.get("lessons"), list) else []
    blocks: Counter[str] = Counter(str(row.get("blockId") or "") for row in lessons if isinstance(row, Mapping))
    block_types: dict[str, set[str]] = {}
    for row in lessons:
        if isinstance(row, Mapping) and row.get("blockId"):
            block_types.setdefault(str(row["blockId"]), set()).add(str(row.get("activityType") or ""))
    unresolved_electives: dict[str, str] = {}
    scope_evidence = scope_warning_evidence(payload, verified_classes or {}, unresolved_electives=unresolved_electives)
    changes: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    unchanged = 0
    already_current = 0
    unclassified = 0
    retained_warning_counts: Counter[str] = Counter()
    retained_warning_examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    verified_teacher_assignments = verified_teacher_assignments or set()
    verified_active_teachers = verified_active_teachers or set()
    for row in lessons:
        if not isinstance(row, Mapping):
            continue
        block_id = str(row.get("blockId") or "")
        kind = str(row.get("activityType") or "")
        # Stable imported elective ids encode the source slot role. Require
        # repeated membership in that same explicit block before migrating.
        strong_legacy_block = (block_id.startswith("source:") and block_id.endswith(":electives")
                               and blocks[block_id] > 1 and block_types.get(block_id, set()) <= {"", "elective"})
        if kind == "elective":
            already_current += 1
            unchanged += 1
            if not block_id and (row.get("electiveDirection") or set(str(row.get("clarificationReason") or "").split("; ")) & ELECTIVE_ROSTER_CLAUSES):
                manual.append({"lesson_id": str(row.get("id") or ""), "reason": "Элективное направление не связано с общим блоком; требуется групповое сопоставление"})
        elif not kind and strong_legacy_block:
            changes.append({"lesson_id": str(row.get("id") or ""), "field": "activity_type",
                            "before": "(не задан)", "after": "elective",
                            "companion_fields": ["activityNameSnapshot"] if not row.get("activityNameSnapshot") else [],
                            "block_id": block_id, "reason": "Стабильный исходный ID общего блока electives; в блоке несколько направлений"})
        elif not kind and row.get("electiveDirection"):
            manual.append({"lesson_id": str(row.get("id") or ""), "reason": "Направление электива не связано с подтверждённым общим блоком"})
            unclassified += 1
            unchanged += 1
        elif not kind:
            unclassified += 1
            unchanged += 1
        else:
            already_current += 1
            unchanged += 1
        warning_reason = str(row.get("clarificationReason") or "").strip()
        row_id = str(row.get("id") or "")
        if row_id in unresolved_electives and not any(item["lesson_id"] == row_id for item in manual):
            manual.append({"lesson_id": row_id, "reason": unresolved_electives[row_id]})
        if row_id in scope_evidence and scope_evidence[row_id][0] == "elective_scope_warning":
            field, clauses = scope_evidence[row_id]
            changes.append({"lesson_id": row_id, "field": field, "before": warning_reason,
                            "after": without_scope_clauses(warning_reason, clauses) or "(снято)",
                            "clauses": sorted(clauses),
                            "reason": "Общий блок проверен по типу, времени, классам и доступности; персональный состав не требуется"})
        if row.get("needsClarification") is True and warning_reason == "Не подтверждён преподаватель":
            teacher_ids = {str(value) for value in (row.get("teacherIds") or []) if str(value)}
            if row.get("teacherId"):
                teacher_ids.add(str(row["teacherId"]))
            if teacher_ids and teacher_ids <= verified_active_teachers:
                changes.append({"lesson_id": str(row.get("id") or ""), "field": "teacher_identity_warning",
                    "before": warning_reason, "after": "(снято)", "reason": "Явно указанный преподаватель найден в активном справочнике"})
        elif row.get("needsClarification") is True and warning_reason == "Нет подтверждённого назначения преподавателя группе":
            teacher_ids = {str(value) for value in (row.get("teacherIds") or []) if str(value)}
            if row.get("teacherId"):
                teacher_ids.add(str(row["teacherId"]))
            group_ids = {str(value) for value in (row.get("groupIds") or []) if str(value)}
            class_ids = {str(value) for value in (row.get("classIds") or []) if str(value)}
            if row.get("groupId"):
                group_ids.add(str(row["groupId"]))
            if row.get("classId"):
                class_ids.add(str(row["classId"]))
            group_ids |= class_ids
            subject = " ".join(str(row.get("subject") or "").casefold().split())
            if teacher_ids and group_ids and all(any((teacher, group, subject) in verified_teacher_assignments
                                                     for group in group_ids) for teacher in teacher_ids):
                changes.append({"lesson_id": str(row.get("id") or ""), "field": "teacher_assignment_warning",
                    "before": warning_reason, "after": "(снято)", "reason": "Активный преподаватель и действующее назначение совпадают по группе и предмету"})
    cleared_warning_ids = {str(item["lesson_id"]) for item in changes
                           if item.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"}}
    for row in lessons:
        if not isinstance(row, Mapping) or row.get("needsClarification") is not True:
            continue
        lesson_id = str(row.get("id") or "")
        if lesson_id in cleared_warning_ids:
            continue
        reason = str(row.get("clarificationReason") or "Причина не сохранена")
        if _lesson_activity_type(row) == "club" and not row.get("importError"):
            remaining = [clause for clause in notes(reason) if normalized_note(clause) not in CLUB_LESSON_ONLY_NOTES]
            if not remaining:
                continue
            reason = "; ".join(remaining)
        if lesson_id in scope_evidence:
            reason = without_scope_clauses(reason, scope_evidence[lesson_id][1])
            if not reason:
                continue
        if lesson_id in (verified_group_links or ()) and is_legacy_group_link_note(row):
            reason = without_legacy_group_notes(reason)
            if not reason:
                continue
        retained_warning_counts[reason] += 1
        if len(retained_warning_examples[reason]) < 5:
            retained_warning_examples[reason].append({"lesson_id": lesson_id, "subject": str(row.get("subject") or "Занятие")})
    source_hash = payload_hash(payload)
    plan = {"changes": changes, "source_hash": source_hash, "source_revision": int(draft.get("revision") or 0),
            "source_id": str(draft.get("id") or "")}
    type_change_count = sum(item.get("field") == "activity_type" for item in changes)
    teacher_warning_clear_count = sum(item.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"} for item in changes)
    return {
        "source_id": plan["source_id"], "source_name": str(draft.get("name") or ""),
        "source_revision": plan["source_revision"], "source_hash": source_hash,
        "plan_hash": payload_hash(plan), "lesson_count": len(lessons),
        "automatic_count": len(changes), "activity_type_change_count": type_change_count,
        "teacher_warning_clear_count": teacher_warning_clear_count,
        "lunch_scope_warning_suppressed_count": sum(field == "lunch_scope_warning" for field, _ in scope_evidence.values()),
        "elective_scope_warning_clear_count": sum(r["field"] == "elective_scope_warning" for r in changes),
        "already_current_count": already_current,
        "unchanged_count": unchanged, "unclassified_count": unclassified,
        "manual_count": len(manual), "real_conflict_count": None,
        "changes": changes, "manual": manual,
        "retained_warning_count": sum(retained_warning_counts.values()),
        "retained_warnings": [{"reason": reason, "count": count, "examples": retained_warning_examples[reason]}
                              for reason, count in sorted(retained_warning_counts.items())],
        "notes": ["Неоднозначные записи и сохранённые предупреждения оставлены без изменений.",
                  "Цифровые направления без общего blockId нужно сопоставить отдельно в групповом конструкторе."],
    }


def normalized_copy_payload(draft: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(draft.get("payload") or {}))
    changes = list(preview.get("changes") or [])
    for lesson in payload.get("lessons") or []:
        if isinstance(lesson, dict):
            lesson_changes = [row for row in changes if str(row.get("lesson_id") or "") == str(lesson.get("id") or "")]
            for change in lesson_changes:
                if change.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"}:
                    lesson.pop("needsClarification", None)
                    lesson.pop("clarificationReason", None)
                    lesson["normalization_resolution"] = {
                        "code": "teacher_assignment_verified" if change.get("field") == "teacher_assignment_warning" else "teacher_identity_verified",
                        "source": "teacher_assignments" if change.get("field") == "teacher_assignment_warning" else "identity_directory"}
                elif change.get("field") == "elective_scope_warning":
                    apply_scope_warning_retirement(lesson, change["field"], frozenset(change["clauses"]))
                elif change.get("field") == "activity_type":
                    lesson["activityType"] = "elective"
                    if "activityNameSnapshot" in change.get("companion_fields", []) and str(lesson.get("subject") or "").strip():
                        lesson["activityNameSnapshot"] = str(lesson["subject"]).strip()
    source = dict(payload.get("school_source") or {})
    source["normalization_copy"] = {"source_id": preview["source_id"],
        "source_revision": preview["source_revision"], "source_hash": preview["source_hash"],
        "plan_hash": preview["plan_hash"], "changed_count": len(changes),
        "activity_type_change_count": int(preview.get("activity_type_change_count") or 0),
        "teacher_warning_clear_count": int(preview.get("teacher_warning_clear_count") or 0),
        "change_plan": [{"lesson_id": str(row.get("lesson_id") or ""),
                         "fields": ([row["field"], *row.get("companion_fields", [])]
                                    if row.get("field") == "activity_type" else
                                    ["needsClarification", "clarificationReason",
                                     "scope_warning_normalization" if row["field"] == "elective_scope_warning"
                                     else "normalization_resolution"])}
                        for row in changes]}
    payload["school_source"] = source
    return payload
