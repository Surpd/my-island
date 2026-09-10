"""Derived slot-level student allocation for Schedule v1.

The allocator is deliberately read-only with respect to Directory.  It consumes
canonical groups and dated memberships and rebuilds only Schedule projections.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from backend.database import Database


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def _subject(value: Any) -> str:
    normalized = _norm(value)
    normalized = normalized.removesuffix(" язык")
    if normalized in {"история искусства", "история искусств"}:
        return "история искусств"
    return normalized


def _grade(value: Any) -> str:
    match = re.match(r"\s*(\d{1,2})", str(value or ""))
    return match.group(1) if match else ""


def _json(database: Database, value: Any) -> Any:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if database.database_url:
        from psycopg.types.json import Jsonb
        return Jsonb(value, dumps=lambda payload: json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return serialized


def _active_on(row: Mapping[str, Any], day: str) -> bool:
    start = str(row.get("valid_from") or "")
    end = str(row.get("valid_until") or "")
    return (not start or start <= day) and (not end or end >= day)


def _allocation_key(lesson: Mapping[str, Any], grade: str) -> str:
    modifiers = lesson.get("modifiers") or {}
    parts = [grade, lesson.get("subject"), lesson.get("teacher_hint"), modifiers.get("subject_subgroup"), modifiers.get("exam_track")]
    return "allocation:" + "|".join(_norm(value) for value in parts)


_SOURCE_CONTEXT_SUBJECTS = {"обед", "обед перерыв", "перерыв", "обед break"}


def _is_source_context(lesson: Mapping[str, Any]) -> bool:
    if lesson.get("activity_type") in {"nonlesson", "cancelled"}:
        return True
    subject = _subject(lesson.get("subject"))
    raw_text = str((lesson.get("raw_payload") or {}).get("raw_text") or "").strip()
    return subject in _SOURCE_CONTEXT_SUBJECTS or raw_text in {"🥨", "🍽️"}


def _case_key(lesson: Mapping[str, Any], grade: str) -> tuple[str, str, str]:
    return (str(lesson.get("id") or ""), str(lesson.get("week_start") or ""), str(grade))


def _candidate_groups(
    lesson: Mapping[str, Any], grade: str, groups: Sequence[Mapping[str, Any]],
    group_members: Mapping[str, set[str]], universe: set[str], assignments: Sequence[Mapping[str, Any]],
    manual_rules: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], str]:
    manual = manual_rules.get(_norm(_allocation_key(lesson, grade)))
    if manual:
        decision_type = str(manual.get("decision_type") or "canonical_group")
        if decision_type in {"canonical_group", "groups", "group"}:
            group_ids = [str(value) for value in (manual.get("group_ids") or []) if value]
            if not group_ids and manual.get("group_id"):
                group_ids = [str(manual["group_id"])]
            if group_ids:
                return group_ids, "manual/admin decision"
    modifiers = lesson.get("modifiers") or {}
    subgroup = _norm(modifiers.get("subject_subgroup"))
    exam = _norm(modifiers.get("exam_track"))
    subject = _subject(lesson.get("subject"))
    if subject == "английский" and subgroup:
        numbered = []
        for group in groups:
            if _norm(group.get("group_type")) == "class" or _subject(group.get("subject")) != subject:
                continue
            match = re.search(r"(?:^|\s)(\d+)\s*$", _norm(group.get("name") or group.get("display_name")))
            if match and match.group(1) == subgroup:
                numbered.append(str(group["id"]))
        if len(numbered) == 1:
            # Numeric English labels are authoritative identifiers. Resolve
            # them before dated-member filtering so an archived week cannot
            # silently fall back to a different teacher-owned group.
            return numbered, "explicit membership"
    candidates = []
    for group in groups:
        group_id = str(group["id"])
        if _norm(group.get("group_type")) == "class" or not (group_members.get(group_id, set()) & universe):
            continue
        base = _grade(group.get("base_class_name"))
        if base and base != grade:
            continue
        if subject and _subject(group.get("subject")) != subject:
            continue
        candidates.append(group)
    if lesson.get("activity_type") in {"course_choice", "digital_track"}:
        exact = [str(group["id"]) for group in candidates if _subject(group.get("subject")) == subject]
        return exact, "explicit membership" if len(exact) == 1 else ""
    if subgroup:
        exact = [str(group["id"]) for group in candidates if _norm(group.get("subject_subgroup")) == subgroup]
        if len(exact) == 1:
            return exact, "explicit membership"
    if exam:
        exact = [str(group["id"]) for group in candidates if _norm(group.get("exam_track")) == exam]
        if len(exact) == 1:
            return exact, "explicit membership"
        # In Directory, some exam-oriented groups are named "Угл" rather than
        # carrying exam_track.  This is deterministic only when exactly one such
        # subject group exists for the grade.
        advanced = [str(group["id"]) for group in candidates if _norm(group.get("subject_subgroup")) in {"угл", "advanced", "углублённая", "углубленная"}]
        if len(advanced) == 1:
            return advanced, "explicit membership"
    teacher_ids = {str(value) for value in (lesson.get("resolved_identity_ids") or [])}
    if len(teacher_ids) == 1:
        assigned_group_ids = {
            str(item["group_id"]) for item in assignments
            if str(item.get("teacher_identity_id")) in teacher_ids and _subject(item.get("subject")) == subject
        }
        teacher_groups = [str(group["id"]) for group in candidates if str(group["id"]) in assigned_group_ids]
        if len(teacher_groups) == 1:
            return teacher_groups, "explicit membership"
        if not exam and not subgroup:
            base = [str(group["id"]) for group in candidates if _norm(group.get("subject_subgroup")) in {"база", "base", "базовая"}]
            if len(base) == 1 and str(base[0]) in assigned_group_ids:
                return base, "explicit membership"
    return [], ""


def rebuild_schedule_allocations(database: Database, connection: Any, snapshot_id: Any) -> dict[str, Any]:
    """Replace derived audiences/allocations for one immutable raw snapshot."""
    database.execute(connection, "DELETE FROM schedule_student_allocations WHERE source_snapshot_id=?", (snapshot_id,))
    database.execute(connection, "DELETE FROM schedule_lesson_audiences WHERE source_snapshot_id=?", (snapshot_id,))
    rows = [dict(row) for row in database.execute(connection, """SELECT * FROM schedule_lessons
        WHERE source_snapshot_id=? AND version_kind='weekly' AND lesson_date IS NOT NULL
          AND resolution_status <> 'EXCLUDED'""", (snapshot_id,)).fetchall()]
    for row in rows:
        for key in ("modifiers", "resolved_identity_ids", "resolved_group_ids", "raw_payload", "merge_data"):
            row[key] = database._decode_json_value(row.get(key)) or ({} if key in {"modifiers", "raw_payload", "merge_data"} else [])
    groups = [dict(row) for row in database.execute(connection, """SELECT id,name,display_name,group_type,subject,
        base_class_name,subject_subgroup,exam_track FROM groups WHERE canonical IS TRUE""").fetchall()]
    students = [dict(row) for row in database.execute(connection, "SELECT id,display_name FROM identities WHERE kind='student' AND status='active'").fetchall()]
    memberships = [dict(row) for row in database.execute(connection, """SELECT m.identity_id,m.group_id,m.valid_from,m.valid_until
        FROM memberships m JOIN identities i ON i.id=m.identity_id WHERE m.active IS TRUE AND i.kind='student' AND i.status='active'""").fetchall()]
    overrides = [dict(row) for row in database.execute(connection, """SELECT identity_id,group_id,valid_from,valid_until
        FROM membership_overrides WHERE active IS TRUE AND action='exclude'""").fetchall()]
    assignments = [dict(row) for row in database.execute(connection, """SELECT teacher_identity_id,group_id,subject
        FROM teacher_assignments WHERE active IS TRUE""").fetchall()]
    source = database.execute(connection, "SELECT source_id FROM school_source_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    mapping_rows = database.execute(connection, """SELECT external_key,canonical_value FROM school_source_mappings
        WHERE source_id=? AND mapping_type='audience_rule' AND status='confirmed' AND valid_until IS NULL""", (source["source_id"],)).fetchall() if source else []
    manual_rules: dict[str, dict[str, Any]] = {}
    manual_case_rules: dict[tuple[str, str, str], dict[str, Any]] = {}
    for mapping in mapping_rows:
        value = database._decode_json_value(mapping["canonical_value"])
        if not isinstance(value, dict):
            continue
        if value.get("group_id") and not value.get("group_ids"):
            value = {**value, "decision_type": value.get("decision_type") or "canonical_group", "group_ids": [value["group_id"]]}
        external_key = str(mapping["external_key"] or "")
        if external_key.startswith("case:"):
            parts = external_key.split(":", 3)
            lesson_id = str(value.get("lesson_id") or (parts[1] if len(parts) > 1 else ""))
            week = str(value.get("week_start") or (parts[2] if len(parts) > 2 else ""))
            grade = str(value.get("grade_scope") or (parts[3] if len(parts) > 3 else ""))
            if lesson_id and grade:
                manual_case_rules[(lesson_id, week, grade)] = value
        else:
            manual_rules[_norm(external_key)] = value

    student_names = {str(item["id"]): str(item["display_name"]) for item in students}
    group_by_id = {str(group["id"]): group for group in groups}
    class_groups = [group for group in groups if _norm(group.get("group_type")) == "class"]
    structural_columns: dict[int, str] = {}
    column_evidence: dict[int, set[str]] = defaultdict(set)
    class_names = {_norm(group.get("base_class_name") or group.get("name")): str(group.get("base_class_name") or group.get("name")) for group in class_groups}
    for row in rows:
        payload = row.get("raw_payload") or {}
        column = payload.get("source_column") if isinstance(payload, Mapping) else None
        audience_name = _norm(row.get("audience"))
        if isinstance(column, int) and audience_name in class_names:
            column_evidence[column].add(class_names[audience_name])
    for column, names in column_evidence.items():
        if len(names) == 1:
            structural_columns[column] = next(iter(names))
    slot_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    valid_grades = {"5", "6", "7", "8", "9", "10", "11"}
    for row in rows:
        # A merged cell can represent a joint lesson for more than one
        # parallel (for example 5+6). Materialize the same source lesson in
        # each affected grade slot; the raw lesson itself remains immutable.
        raw_payload = row.get("raw_payload") or {}
        merged = raw_payload.get("merged_audiences") if isinstance(raw_payload, Mapping) else []
        grades = {_grade(row.get("audience"))}
        grades.update(_grade(value) for value in (merged or []))
        modifiers = row.get("modifiers") or {}
        # English groups 1/2 are one cross-grade 5–6 line in the source
        # (written as "1/2" and "2/1" in the two physical columns).  Both
        # canonical groups must therefore participate in both grade slots;
        # membership still decides the individual assignment.
        if (_subject(row.get("subject")) == "английский"
                and _norm(modifiers.get("subject_subgroup")) in {"1", "2"}
                and grades & {"5", "6"}):
            grades.update({"5", "6"})
        if not (row.get("modifiers") or {}).get("synthetic_cancelled") and not _is_source_context(row):
            for grade in sorted(grades & valid_grades):
                slot_rows[(str(row["lesson_date"]), str(row["start_time"]), grade)].append(row)

    allocations: list[dict[str, Any]] = []
    audience_rows: dict[str, dict[str, Any]] = {}
    pending_gaps: list[dict[str, Any]] = []
    assigned_later: dict[tuple[str, str], list[str]] = defaultdict(list)
    dedup_slot_keys: set[tuple[str, str, str]] = set()
    structural_slot_keys: set[tuple[str, str, str]] = set()
    complement_slot_keys: set[tuple[str, str, str]] = set()

    for (lesson_day, start_time, grade), lessons in sorted(slot_rows.items()):
        excluded = {(str(item["identity_id"]), str(item["group_id"])) for item in overrides if _active_on(item, lesson_day)}
        group_members: dict[str, set[str]] = defaultdict(set)
        for membership in memberships:
            identity_id, group_id = str(membership["identity_id"]), str(membership["group_id"])
            if _active_on(membership, lesson_day) and (identity_id, group_id) not in excluded:
                group_members[group_id].add(identity_id)
        base_groups = [group for group in class_groups if _grade(group.get("base_class_name") or group.get("name")) == grade]
        universe = set().union(*(group_members.get(str(group["id"]), set()) for group in base_groups)) if base_groups else set()
        if not universe:
            continue
        activity_rules: dict[str, dict[str, Any]] = {}
        explicit_count = 0
        for lesson in lessons:
            lesson_id = str(lesson["id"])
            decision = manual_case_rules.get(_case_key(lesson, grade)) or manual_rules.get(_norm(_allocation_key(lesson, grade)))
            decision_type = str((decision or {}).get("decision_type") or "")
            if decision_type in {"window", "no_lesson", "source_context", "ignore_source"}:
                activity_rules[lesson_id] = {"kind": "ignored", "groups": [], "students": set(), "reason": decision_type}
                continue
            if decision_type == "parallel":
                activity_rules[lesson_id] = {"kind": "class", "groups": [str(group["id"]) for group in base_groups], "students": set(universe), "reason": "manual/admin decision"}
                explicit_count += 1
                continue
            if decision_type == "base_class":
                selected = [str(value) for value in (decision or {}).get("group_ids", []) if value]
                if not selected:
                    audience = _norm(lesson.get("audience"))
                    selected = [str(group["id"]) for group in base_groups if _norm(group.get("base_class_name") or group.get("name")) == audience]
                if selected:
                    activity_rules[lesson_id] = {"kind": "class", "groups": selected, "students": set().union(*(group_members.get(group_id, set()) for group_id in selected)), "reason": "manual/admin decision"}
                    explicit_count += 1
                    continue
            if decision_type == "complement":
                activity_rules[lesson_id] = {"kind": "complement", "groups": [], "students": set(), "reason": "manual complement"}
                explicit_count += 1
                continue
            if lesson.get("activity_type") == "digital_track":
                activity_rules[lesson_id] = {"kind": "class", "groups": [str(group["id"]) for group in base_groups], "students": set(universe), "reason": "structural digital track"}
                explicit_count += 1
                continue
            group_ids, reason = _candidate_groups(lesson, grade, groups, group_members, universe, assignments, manual_rules)
            if group_ids:
                activity_rules[lesson_id] = {"kind": "group" if len(group_ids) == 1 else "group_set", "groups": group_ids, "students": set().union(*(group_members.get(group_id, set()) for group_id in group_ids)) & universe, "reason": reason}
                explicit_count += 1
            else:
                activity_rules[lesson_id] = {"kind": "pending", "groups": group_ids, "students": set(), "reason": ""}

        seen_activity_keys: dict[tuple[Any, ...], str] = {}
        for lesson in lessons:
            lesson_id = str(lesson["id"]); rule = activity_rules[lesson_id]
            payload = lesson.get("raw_payload") or {}
            if lesson.get("activity_type") == "digital_track":
                semantic_key = ("digital_track", grade)
            elif rule["kind"] in {"group", "group_set", "class"}:
                semantic_key = (tuple(sorted(rule.get("groups", []))), _subject(lesson.get("subject")), _norm(lesson.get("teacher_hint")), lesson.get("activity_type"))
            elif rule["kind"] == "pending":
                semantic_key = ("pending", _subject(lesson.get("subject")), _norm(lesson.get("teacher_hint")), _norm(lesson.get("room")), _norm(lesson.get("audience")))
            else:
                continue
            if semantic_key in seen_activity_keys:
                rule.update({"kind": "duplicate", "students": set(), "reason": "duplicate canonical activity", "duplicate_of": seen_activity_keys[semantic_key]})
                dedup_slot_keys.add((lesson_day, start_time, grade))
            else:
                seen_activity_keys[semantic_key] = lesson_id

        pending_lessons = [lesson for lesson in lessons if activity_rules[str(lesson["id"])]["kind"] == "pending" and lesson.get("activity_type") != "nonlesson"]
        pending_lessons = [lesson for lesson in pending_lessons if not _is_source_context(lesson)]
        if explicit_count and len(pending_lessons) == 1:
            rule = activity_rules[str(pending_lessons[0]["id"])]
            rule.update({"kind": "complement", "reason": "complement"})
            complement_slot_keys.add((lesson_day, start_time, grade))

        # A structural column is evidence for a base-class audience only when
        # that class has one remaining activity in the slot.  Cross-class
        # instructional groups are allocated first; if several companion
        # activities remain, interpreting any of them as the complement would
        # be a guess and must stay in admin review.
        structural_pending_counts: dict[str, int] = defaultdict(int)
        if not explicit_count:
            for pending in pending_lessons:
                payload = pending.get("raw_payload") or {}
                column = payload.get("source_column") if isinstance(payload, Mapping) else None
                structural_audience = structural_columns.get(column) if isinstance(column, int) else None
                if structural_audience and _grade(structural_audience) == grade:
                    structural_pending_counts[_norm(structural_audience)] += 1

        for lesson in lessons:
            lesson_id = str(lesson["id"])
            rule = activity_rules[lesson_id]
            if rule["kind"] != "pending":
                continue
            audience = str(lesson.get("audience") or "")
            payload = lesson.get("raw_payload") or {}
            column = payload.get("source_column") if isinstance(payload, Mapping) else None
            structural_audience = structural_columns.get(column) if isinstance(column, int) else None
            if structural_audience and _grade(structural_audience) == grade:
                audience = structural_audience
            siblings = [item for item in lessons if str(item.get("audience") or "") == audience and item.get("activity_type") != "nonlesson"]
            merged_names = [str(value) for value in ((lesson.get("raw_payload") or {}).get("merged_audiences") or [])]
            merged_names = [value for value in merged_names if _grade(value) == grade]
            merged_classes = [group for group in base_groups if any(_norm(group.get("base_class_name") or group.get("name")) == _norm(value) for value in [audience, *merged_names])]
            direct_class = next((group for group in merged_classes if _norm(group.get("base_class_name") or group.get("name")) == _norm(audience)), None)
            if direct_class and structural_audience and not explicit_count and structural_pending_counts[_norm(structural_audience)] == 1:
                rule.update({"kind": "class", "groups": [str(direct_class["id"])], "students": group_members.get(str(direct_class["id"]), set()), "reason": "structural column"})
                structural_slot_keys.add((lesson_day, start_time, grade))
            elif merged_classes and (len(siblings) == 1 or len(merged_classes) > 1 or lesson.get("activity_type") == "combined"):
                group_ids = [str(group["id"]) for group in merged_classes]
                rule.update({"kind": "class", "groups": group_ids, "students": set().union(*(group_members.get(group_id, set()) for group_id in group_ids)), "reason": "explicit membership"})
            else:
                rule.update({"kind": "ambiguous", "reason": "ambiguous lesson audience"})

        complement_ids = [str(lesson["id"]) for lesson in lessons if activity_rules[str(lesson["id"])]["kind"] == "complement"]
        for lesson in lessons:
            lesson_id = str(lesson["id"]); rule = activity_rules[lesson_id]
            audience_status = "ambiguous" if rule["kind"] == "ambiguous" else "resolved"
            existing = audience_rows.get(lesson_id)
            if existing:
                grades = set(str(existing["grade"]).split(",")) | {grade}
                existing["grade"] = ",".join(sorted(grades, key=lambda value: int(value) if value.isdigit() else value))
                existing["groups"] = sorted(set(existing.get("groups", [])) | set(rule.get("groups", [])))
            else:
                audience_rows[lesson_id] = {"lesson": lesson, "grade": grade, "kind": rule["kind"], "groups": rule.get("groups", []), "status": audience_status,
                                            "reason": rule["reason"], "key": _allocation_key(lesson, grade)}

        for student_id in sorted(universe):
            candidates = []
            for lesson in lessons:
                lesson_id = str(lesson["id"]); rule = activity_rules[lesson_id]
                if rule["kind"] in {"group", "group_set", "class"} and student_id in rule["students"]:
                    candidates.append((lesson, rule))
            if len(candidates) > 1:
                allocations.append({"day": lesson_day, "time": start_time, "grade": grade, "student": student_id, "lesson": None,
                                    "kind": "conflict", "status": "conflict", "reason": "conflict",
                                    "provenance": {"reason": "conflict", "candidate_lesson_ids": [str(item[0]["id"]) for item in candidates]}})
            elif len(candidates) == 1:
                lesson, rule = candidates[0]
                allocations.append({"day": lesson_day, "time": start_time, "grade": grade, "student": student_id, "lesson": lesson,
                                    "kind": "lunch" if lesson.get("activity_type") == "nonlesson" else "lesson", "status": "assigned", "reason": rule["reason"],
                                    "provenance": {"reason": rule["reason"], "group_ids": rule.get("groups", []), "audience_key": _allocation_key(lesson, grade)}})
                assigned_later[(lesson_day, student_id)].append(start_time)
            elif len(complement_ids) == 1:
                lesson = next(item for item in lessons if str(item["id"]) == complement_ids[0]); reason = activity_rules[complement_ids[0]]["reason"]
                allocations.append({"day": lesson_day, "time": start_time, "grade": grade, "student": student_id, "lesson": lesson,
                                    "kind": "lunch" if lesson.get("activity_type") == "nonlesson" else "lesson", "status": "assigned", "reason": reason,
                                    "provenance": {"reason": reason, "excluded_lesson_ids": [str(item[0]["id"]) for item in candidates], "audience_key": _allocation_key(lesson, grade)}})
                assigned_later[(lesson_day, student_id)].append(start_time)
            elif any(rule["kind"] == "ambiguous" for rule in activity_rules.values()):
                allocations.append({"day": lesson_day, "time": start_time, "grade": grade, "student": student_id, "lesson": None,
                                    "kind": "unassigned", "status": "unassigned", "reason": "ambiguous lesson audience",
                                    "provenance": {"reason": "ambiguous lesson audience", "activity_ids": [str(item["id"]) for item in lessons]}})
            else:
                pending_gaps.append({"day": lesson_day, "time": start_time, "grade": grade, "student": student_id, "lesson": None})

    for gap in pending_gaps:
        later = any(value > gap["time"] for value in assigned_later.get((gap["day"], gap["student"]), []))
        if later:
            allocations.append({**gap, "kind": "window", "status": "assigned", "reason": "window", "provenance": {"reason": "window"}})

    audience_params = []
    for item in audience_rows.values():
        lesson = item["lesson"]
        audience_params.append((snapshot_id, lesson["id"], lesson.get("week_start"), lesson["lesson_date"], lesson["start_time"], item["grade"], item["kind"],
            _json(database, item["groups"]), item["status"], item["reason"], _json(database, {"reason": item["reason"], "audience_key": item["key"]})))
    end_times = {str(row["id"]): row.get("end_time") for row in rows}
    week_by_day = {str(row.get("lesson_date")): row.get("week_start") for row in rows}
    allocation_params = []
    for item in allocations:
        lesson_id = item["lesson"]["id"] if item.get("lesson") else None
        allocation_params.append((snapshot_id, item["lesson"].get("week_start") if item.get("lesson") else week_by_day.get(item["day"]),
            item["day"], item["time"], end_times.get(str(lesson_id)), item["grade"], item["student"], lesson_id, item["kind"], item["status"], item["reason"], _json(database, item["provenance"])))
    audience_sql = """INSERT INTO schedule_lesson_audiences(source_snapshot_id,lesson_id,week_start,lesson_date,start_time,grade_scope,audience_kind,resolved_group_ids,status,rule_reason,provenance)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    allocation_sql = """INSERT INTO schedule_student_allocations(source_snapshot_id,week_start,lesson_date,start_time,end_time,grade_scope,student_identity_id,lesson_id,allocation_kind,status,reason,provenance)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    if audience_params or allocation_params:
        cursor = connection.cursor()
        if audience_params:
            cursor.executemany(audience_sql.replace("?", "%s") if database.database_url else audience_sql, audience_params)
        if allocation_params:
            cursor.executemany(allocation_sql.replace("?", "%s") if database.database_url else allocation_sql, allocation_params)
        cursor.close()
    slot_status: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"unassigned": 0, "conflict": 0})
    for item in allocations:
        if item["status"] in {"unassigned", "conflict"}:
            slot_status[(item["day"], item["time"], item["grade"])][item["status"]] += 1
    return {"slots": len(slot_rows), "complete_slots": len(slot_rows) - len(slot_status),
            "unassigned_slots": sum(1 for value in slot_status.values() if value["unassigned"]),
            "conflict_slots": sum(1 for value in slot_status.values() if value["conflict"]),
            "allocations": len(allocations), "students": len(student_names), "dedup_slots": len(dedup_slot_keys),
            "structural_column_slots": len(structural_slot_keys), "complement_slots": len(complement_slot_keys)}
