"""Offline runtime-equivalent validation for the current-template v2 artifact."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping


OPTIONAL_TEACHER_ACTIVITIES = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}


def grade(value: Any) -> str:
    text = str(value or "")
    return text.split("-", 1)[0] if text and text[0].isdigit() else ""


def normalize_key(block: Mapping[str, Any]) -> tuple[str, str, str, str]:
    slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
    def short_time(value: Any) -> str:
        text = str(value or "")
        return text[:5] if len(text) >= 5 and text[2:3] == ":" else text
    return str(block.get("weekday")), short_time(slot.get("start")), short_time(slot.get("end")), str(block.get("grade_scope") or "")


def membership_index(db: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for item in db.get("memberships") or []:
        result[str(item["group_id"])].add(str(item["identity_id"]))
    return result


def project(artifact: Mapping[str, Any], db: Mapping[str, Any]) -> dict[str, Any]:
    groups = {str(item["id"]): item for item in db.get("groups") or []}
    memberships = membership_index(db)
    students = {str(item["id"]): item for item in db.get("students") or []}
    blocks = list((artifact.get("blocks") or {}).values())
    routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    duplicate_slots: list[dict[str, Any]] = []
    multiple_activities: list[dict[str, Any]] = []
    unknown_refs: list[dict[str, Any]] = []
    for block in blocks:
        gscope = {x for x in str(block.get("grade_scope") or "").split(",") if x}
        slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
        for student_id, student in students.items():
            if grade(student.get("class_name")) not in gscope:
                continue
            remaining = {student_id}
            claims: list[dict[str, Any]] = []
            reason = ""
            for assignment in block.get("assignments") or []:
                activity = str(assignment.get("activity") or "")
                role = str(assignment.get("role") or assignment.get("kind") or "primary")
                audience = assignment.get("audience") if isinstance(assignment.get("audience"), Mapping) else {}
                group_ids = [str(x) for x in audience.get("canonical_group_ids") or assignment.get("canonical_group_ids") or []]
                missing = [x for x in group_ids if x not in groups]
                if missing:
                    unknown_refs.append({"block_key": block.get("block_key"), "student_id": student_id, "group_ids": missing})
                    reason = "canonical group not found: " + ", ".join(missing)
                    continue
                audience_ids = set().union(*(memberships.get(x, set()) for x in group_ids)) if group_ids else set(remaining)
                if role in {"default", "residual", "final_state", "final_unassigned", "explicit_no_lesson"} or assignment.get("audience_kind") in {"complement", "remaining"}:
                    audience_ids = set(remaining)
                else:
                    audience_ids &= set(remaining)
                if student_id in audience_ids:
                    if activity == "NO_LESSON" or role in {"final_state", "final_unassigned", "explicit_no_lesson"}:
                        claims.append({"state": "NO_LESSON", "activity": "NO_LESSON", "source": assignment.get("source_cells") or []})
                    else:
                        claims.append({"state": "ACTIVITY", "activity": activity, "source": assignment.get("source_cells") or []})
                    remaining.discard(student_id)
            for issue in block.get("unresolved") or []:
                if student_id in {str(x) for x in issue.get("student_ids") or []}:
                    reason = str(issue.get("reason") or "canonical block unresolved")
            activities = {x["activity"] for x in claims if x["state"] == "ACTIVITY"}
            if reason or len(activities) > 1:
                state = "UNRESOLVED"
                unresolved.append({"block_key": block.get("block_key"), "student_id": student_id, "reason": reason or "multiple effective activities"})
            elif claims:
                state = claims[0]["state"]
            else:
                state = "NO_LESSON"
            routes[student_id].append({"block_key": block.get("block_key"), "weekday": block.get("weekday"), "start_time": slot.get("start"), "state": state, "activities": sorted(activities), "claims": claims})
            if len(activities) > 1:
                multiple_activities.append({"student_id": student_id, "block_key": block.get("block_key"), "activities": sorted(activities)})
    for student_id, items in routes.items():
        counts = Counter((item["weekday"], item["start_time"]) for item in items if item["start_time"])
        for key, count in counts.items():
            if count > 1:
                duplicate_slots.append({"student_id": student_id, "weekday": key[0], "start_time": key[1], "count": count})
    states = Counter(item["state"] for items in routes.values() for item in items)
    return {"states": dict(states), "routes": routes, "unresolved": unresolved, "duplicate_student_slots": duplicate_slots, "multiple_activities": multiple_activities, "unknown_group_refs": unknown_refs}


def teacher_projection(artifact: Mapping[str, Any], db: Mapping[str, Any]) -> dict[str, Any]:
    teacher_ids = {str(item["id"]) for item in db.get("teachers") or []}
    resolved = 0
    unresolved = []
    orphan = []
    occupied: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str, tuple[str, ...]]] = set()
    for block in (artifact.get("blocks") or {}).values():
        slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
        start = str(slot.get("start") or "")
        for assignment in block.get("assignments") or []:
            if str(assignment.get("activity")) == "NO_LESSON":
                continue
            ids = [str(x) for x in assignment.get("teacher_ids") or []]
            if ids:
                resolved += 1
                for teacher_id in ids:
                    if teacher_id not in teacher_ids:
                        unresolved.append({"block_key": block.get("block_key"), "teacher_id": teacher_id, "reason": "teacher identity not found"})
                    if start:
                        group_ids = tuple(sorted(str(x) for x in assignment.get("canonical_group_ids") or []))
                        signature = (str(block.get("weekday")), start, teacher_id, str(assignment.get("activity") or ""), group_ids)
                        if signature in seen:
                            continue
                        seen.add(signature)
                        key = (str(block.get("weekday")), start, teacher_id)
                        occupied[key].append({"block_key": block.get("block_key"), "activity": assignment.get("activity")})
            else:
                if str(assignment.get("activity") or "") in OPTIONAL_TEACHER_ACTIVITIES:
                    continue
                metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), Mapping) else {}
                unresolved.append({"block_key": block.get("block_key"), "source_cells": assignment.get("source_cells"), "reason": metadata.get("teacher_issue") or "teacher_id=null"})
                if assignment.get("role") not in {"special", "residual", "default"}:
                    orphan.append({"block_key": block.get("block_key"), "activity": assignment.get("activity"), "source_cells": assignment.get("source_cells")})
    conflicts = [{"key": key, "items": items} for key, items in occupied.items() if len(items) > 1]
    return {"resolved_assignments": resolved, "unresolved_assignments": unresolved, "orphan_expected_teacher": orphan, "simultaneous_conflicts": conflicts}


def semantic_diff(v1: Mapping[str, Any], v2: Mapping[str, Any]) -> dict[str, Any]:
    left = {normalize_key(b): b for b in (v1.get("blocks") or {}).values()}
    right = {normalize_key(b): b for b in (v2.get("blocks") or {}).values()}
    result = Counter()
    details: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if a is None:
            result["added"] += 1; details.append({"key": key, "change": "added"}); continue
        if b is None:
            result["removed"] += 1; details.append({"key": key, "change": "removed"}); continue
        def signature(block: Mapping[str, Any]) -> set[tuple[Any, ...]]:
            output = set()
            for item in block.get("assignments") or []:
                audience = item.get("audience") if isinstance(item.get("audience"), Mapping) else {}
                groups = tuple(sorted(str(x) for x in audience.get("canonical_group_ids") or item.get("canonical_group_ids") or []))
                output.add((str(item.get("activity") or ""), groups, tuple(sorted(str(x) for x in item.get("teacher_ids") or [])), tuple(sorted(str(x) for x in item.get("source_cells") or []))))
            return output
        changes = []
        if signature(a) != signature(b):
            old, new = signature(a), signature(b)
            if {x[1] for x in old} != {x[1] for x in new}: changes.append("audience/routing")
            if {x[0] for x in old} != {x[0] for x in new}: changes.append("activity")
            if {x[2] for x in old} != {x[2] for x in new}: changes.append("teacher")
        if str(a.get("mode") or "") != str(b.get("mode") or ""):
            changes.append("routing dimension")
        if changes:
            unique = tuple(sorted(set(changes))); result["changed"] += 1
            for change in unique: result[change] += 1
            details.append({"key": key, "change": list(unique)})
        else:
            result["unchanged"] += 1
    return {"counts": dict(result), "details": details}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--artifact", type=Path, required=True); parser.add_argument("--input", type=Path, required=True); parser.add_argument("--v1", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8")); payload = json.loads(args.input.read_text(encoding="utf-8")); v1 = json.loads(args.v1.read_text(encoding="utf-8"))
    projection = project(artifact, payload["db"]); teachers = teacher_projection(artifact, payload["db"]); diff = semantic_diff(v1, artifact)
    known = Counter(str(item.get("reason") or "") for item in projection["unresolved"])
    report = {"artifact": {"version_id": artifact.get("version_id"), "parent_version_id": artifact.get("parent_version_id"), "blocks": len(artifact.get("blocks") or {}), "assignments": sum(len(x.get("assignments") or []) for x in (artifact.get("blocks") or {}).values()), "status": artifact.get("status"), "authoritative": artifact.get("authoritative")}, "student_projection": {"states": projection["states"], "duplicate_student_slots": projection["duplicate_student_slots"], "multiple_activities": projection["multiple_activities"], "unknown_group_refs": projection["unknown_group_refs"], "unresolved_count": len(projection["unresolved"]), "unresolved_by_reason": dict(known), "active_students": len(payload["db"].get("students") or [])}, "teacher_projection": {"resolved_assignments": teachers["resolved_assignments"], "unresolved_assignments": teachers["unresolved_assignments"], "orphan_expected_teacher": teachers["orphan_expected_teacher"], "simultaneous_conflicts": teachers["simultaneous_conflicts"]}, "semantic_diff": diff, "grade7_reuse": artifact.get("grade7_reuse"), "source_metadata": artifact.get("source_metadata"), "current_week": {"tab": "14-18 сентября", "available": True, "used_as_ground_truth": False}}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"artifact": report["artifact"], "student": report["student_projection"], "teacher": {k: len(v) if isinstance(v, list) else v for k, v in report["teacher_projection"].items() if k != "simultaneous_conflicts"}, "diff": report["semantic_diff"]["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
