from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date
from typing import Any


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


def normalization_preview(draft: Mapping[str, Any], *, verified_active_teachers: set[str] | None = None,
                          verified_teacher_assignments: set[tuple[str, str, str]] | None = None) -> dict[str, Any]:
    """Plan only evidence-backed legacy type updates; all other rows are preserved."""
    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    lessons = payload.get("lessons") if isinstance(payload.get("lessons"), list) else []
    blocks: Counter[str] = Counter(str(row.get("blockId") or "") for row in lessons if isinstance(row, Mapping))
    block_types: dict[str, set[str]] = {}
    for row in lessons:
        if isinstance(row, Mapping) and row.get("blockId"):
            block_types.setdefault(str(row["blockId"]), set()).add(str(row.get("activityType") or ""))
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
            if row.get("electiveDirection") and not block_id:
                manual.append({"lesson_id": str(row.get("id") or ""), "reason": "Элективное направление не связано с общим блоком; требуется групповое сопоставление"})
        elif not kind and strong_legacy_block:
            changes.append({"lesson_id": str(row.get("id") or ""), "before": "(не задан)", "after": "elective",
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
        retained_warning_counts[reason] += 1
        if len(retained_warning_examples[reason]) < 5:
            retained_warning_examples[reason].append({"lesson_id": lesson_id, "subject": str(row.get("subject") or "Занятие")})
    source_hash = payload_hash(payload)
    plan = {"changes": changes, "source_hash": source_hash, "source_revision": int(draft.get("revision") or 0),
            "source_id": str(draft.get("id") or "")}
    return {
        "source_id": plan["source_id"], "source_name": str(draft.get("name") or ""),
        "source_revision": plan["source_revision"], "source_hash": source_hash,
        "plan_hash": payload_hash(plan), "lesson_count": len(lessons),
        "automatic_count": len(changes), "already_current_count": already_current,
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
    changes = {str(row["lesson_id"]) for row in preview.get("changes") or []}
    for lesson in payload.get("lessons") or []:
        if isinstance(lesson, dict):
            lesson_changes = [row for row in preview.get("changes") or [] if str(row.get("lesson_id") or "") == str(lesson.get("id") or "")]
            for change in lesson_changes:
                if change.get("field") in {"teacher_assignment_warning", "teacher_identity_warning"}:
                    lesson.pop("needsClarification", None)
                    lesson.pop("clarificationReason", None)
                    lesson["normalization_resolution"] = {
                        "code": "teacher_assignment_verified" if change.get("field") == "teacher_assignment_warning" else "teacher_identity_verified",
                        "source": "teacher_assignments" if change.get("field") == "teacher_assignment_warning" else "identity_directory"}
                else:
                    lesson["activityType"] = "elective"
    source = dict(payload.get("school_source") or {})
    source["normalization_copy"] = {"source_id": preview["source_id"],
        "source_revision": preview["source_revision"], "source_hash": preview["source_hash"],
        "plan_hash": preview["plan_hash"], "changed_count": len(changes)}
    payload["school_source"] = source
    return payload
