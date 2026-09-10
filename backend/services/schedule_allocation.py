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
    return normalized.removesuffix(" язык")


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


def _candidate_groups(
    lesson: Mapping[str, Any], grade: str, groups: Sequence[Mapping[str, Any]],
    group_members: Mapping[str, set[str]], universe: set[str], assignments: Sequence[Mapping[str, Any]],
    manual_rules: Mapping[str, str],
) -> tuple[list[str], str]:
    manual = manual_rules.get(_norm(_allocation_key(lesson, grade)))
    if manual:
        return [manual], "manual/admin mapping"
    modifiers = lesson.get("modifiers") or {}
    subgroup = _norm(modifiers.get("subject_subgroup"))
    exam = _norm(modifiers.get("exam_track"))
    subject = _subject(lesson.get("subject"))
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
    manual_rules: dict[str, str] = {}
    for mapping in mapping_rows:
        value = database._decode_json_value(mapping["canonical_value"])
        if isinstance(value, dict) and value.get("group_id"):
            manual_rules[_norm(mapping["external_key"])] = str(value["group_id"])

    student_names = {str(item["id"]): str(item["display_name"]) for item in students}
    group_by_id = {str(group["id"]): group for group in groups}
    class_groups = [group for group in groups if _norm(group.get("group_type")) == "class"]
    slot_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grade = _grade(row.get("audience"))
        if grade in {"5", "6", "7", "8", "9", "10", "11"} and not (row.get("modifiers") or {}).get("synthetic_cancelled") and row.get("activity_type") != "cancelled":
            slot_rows[(str(row["lesson_date"]), str(row["start_time"]), grade)].append(row)

    allocations: list[dict[str, Any]] = []
    audience_rows: list[dict[str, Any]] = []
    pending_gaps: list[dict[str, Any]] = []
    assigned_later: dict[tuple[str, str], list[str]] = defaultdict(list)

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
            group_ids, reason = _candidate_groups(lesson, grade, groups, group_members, universe, assignments, manual_rules)
            if len(group_ids) == 1:
                activity_rules[lesson_id] = {"kind": "group", "groups": group_ids, "students": group_members.get(group_ids[0], set()) & universe, "reason": reason}
                explicit_count += 1
            else:
                activity_rules[lesson_id] = {"kind": "pending", "groups": group_ids, "students": set(), "reason": ""}

        pending_lessons = [lesson for lesson in lessons if activity_rules[str(lesson["id"])]["kind"] == "pending" and lesson.get("activity_type") != "nonlesson"]
        lunches = [lesson for lesson in lessons if lesson.get("activity_type") == "nonlesson"]
        if explicit_count and len(pending_lessons) == 1 and not lunches:
            rule = activity_rules[str(pending_lessons[0]["id"])]
            rule.update({"kind": "complement", "reason": "complement"})
        if lunches and any(lesson.get("activity_type") != "nonlesson" for lesson in lessons):
            if len(lunches) == 1 and not any(rule["kind"] == "complement" for rule in activity_rules.values()):
                activity_rules[str(lunches[0]["id"])].update({"kind": "complement", "reason": "contextual lunch"})

        for lesson in lessons:
            lesson_id = str(lesson["id"])
            rule = activity_rules[lesson_id]
            if rule["kind"] != "pending":
                continue
            audience = str(lesson.get("audience") or "")
            siblings = [item for item in lessons if str(item.get("audience") or "") == audience and item.get("activity_type") != "nonlesson"]
            direct_class = next((group for group in base_groups if _norm(group.get("base_class_name") or group.get("name")) == _norm(audience)), None)
            if direct_class and len(siblings) == 1:
                rule.update({"kind": "class", "groups": [str(direct_class["id"])], "students": group_members.get(str(direct_class["id"]), set()), "reason": "explicit membership"})
            elif lesson.get("activity_type") == "combined" and direct_class:
                rule.update({"kind": "class", "groups": [str(direct_class["id"])], "students": group_members.get(str(direct_class["id"]), set()), "reason": "explicit membership"})
            else:
                rule.update({"kind": "ambiguous", "reason": "ambiguous lesson audience"})

        complement_ids = [str(lesson["id"]) for lesson in lessons if activity_rules[str(lesson["id"])]["kind"] == "complement"]
        for lesson in lessons:
            lesson_id = str(lesson["id"]); rule = activity_rules[lesson_id]
            audience_status = "ambiguous" if rule["kind"] == "ambiguous" else "resolved"
            audience_rows.append({"lesson": lesson, "grade": grade, "kind": rule["kind"], "groups": rule.get("groups", []), "status": audience_status,
                                  "reason": rule["reason"], "key": _allocation_key(lesson, grade)})

        for student_id in sorted(universe):
            candidates = []
            for lesson in lessons:
                lesson_id = str(lesson["id"]); rule = activity_rules[lesson_id]
                if rule["kind"] in {"group", "class"} and student_id in rule["students"]:
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
        reason = "window" if later else "end of day"
        allocations.append({**gap, "kind": "window" if later else "end_of_day", "status": "assigned", "reason": reason, "provenance": {"reason": reason}})

    for item in audience_rows:
        lesson = item["lesson"]
        database.execute(connection, """INSERT INTO schedule_lesson_audiences(source_snapshot_id,lesson_id,week_start,lesson_date,start_time,grade_scope,audience_kind,resolved_group_ids,status,rule_reason,provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, lesson["id"], lesson.get("week_start"), lesson["lesson_date"], lesson["start_time"], item["grade"], item["kind"],
            _json(database, item["groups"]), item["status"], item["reason"], _json(database, {"reason": item["reason"], "audience_key": item["key"]})))
    end_times = {str(row["id"]): row.get("end_time") for row in rows}
    week_by_day = {str(row.get("lesson_date")): row.get("week_start") for row in rows}
    for item in allocations:
        lesson_id = item["lesson"]["id"] if item.get("lesson") else None
        database.execute(connection, """INSERT INTO schedule_student_allocations(source_snapshot_id,week_start,lesson_date,start_time,end_time,grade_scope,student_identity_id,lesson_id,allocation_kind,status,reason,provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, item["lesson"].get("week_start") if item.get("lesson") else week_by_day.get(item["day"]),
            item["day"], item["time"], end_times.get(str(lesson_id)), item["grade"], item["student"], lesson_id, item["kind"], item["status"], item["reason"], _json(database, item["provenance"])))
    slot_status: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"unassigned": 0, "conflict": 0})
    for item in allocations:
        if item["status"] in {"unassigned", "conflict"}:
            slot_status[(item["day"], item["time"], item["grade"])][item["status"]] += 1
    return {"slots": len(slot_rows), "complete_slots": len(slot_rows) - len(slot_status),
            "unassigned_slots": sum(1 for value in slot_status.values() if value["unassigned"]),
            "conflict_slots": sum(1 for value in slot_status.values() if value["conflict"]),
            "allocations": len(allocations), "students": len(student_names)}
