"""Read-only live validation for a persisted Grade 7 canonical candidate."""
from __future__ import annotations

import argparse
import collections
import json

from backend.config import get_settings
from backend.database import Database
from backend.services.canonical_schedule import (
    canonical_version,
    effective_blocks,
    effective_status,
    project_student,
    project_teacher,
    read_canonical_template,
)


def run(version_id: str, week_start: str) -> dict[str, object]:
    settings = get_settings()
    database = Database(settings.database_path, settings.database_url, autocommit=True)
    version = canonical_version(database, version_id)
    if not version:
        raise ValueError(f"Canonical version was not found: {version_id}")
    template = read_canonical_template(database, version_id)
    effective = effective_blocks(database, week_start)
    selected_status = effective_status(database, week_start)

    with database.connection() as connection:
        students = list(database.execute(connection, """
            SELECT id,display_name,class_name
              FROM identities
             WHERE kind='student' AND status='active'
               AND class_name IN ('7-А','7-Б')
             ORDER BY display_name
        """).fetchall())
        groups = list(database.execute(connection, """
            SELECT id,name FROM groups WHERE canonical IS TRUE
        """).fetchall())
        assignment_rows = list(database.execute(connection, """
            SELECT a.teacher_ids,a.canonical_group_ids
              FROM canonical_schedule_assignments a
              JOIN canonical_schedule_blocks b ON b.id=a.block_id
             WHERE b.version_id=?
        """, (version_id,)).fetchall())
        v1 = database.execute(connection, """
            SELECT version_id,status,parent_version_id,source_snapshot_id,source_fingerprint
              FROM canonical_schedule_versions
             WHERE version_id='4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93'
        """).fetchone()

    canonical_group_ids = {str(row["id"]) for row in groups}
    unknown_group_refs: list[str] = []
    teacher_ids: set[str] = set()
    for row in assignment_rows:
        group_ids = database._decode_json_value(row["canonical_group_ids"]) or []
        unknown_group_refs.extend(str(value) for value in group_ids if str(value) not in canonical_group_ids)
        teacher_ids.update(str(value) for value in (database._decode_json_value(row["teacher_ids"]) or []))

    state_counts: collections.Counter[str] = collections.Counter()
    duplicate_slots: list[dict[str, object]] = []
    multiple_activities: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    for student in students:
        projection = project_student(database, student["id"], week_start)
        for item in projection["items"]:
            state_counts[str(item["state"])] += 1
        slots: dict[tuple[str, str, str], list[dict[str, object]]] = collections.defaultdict(list)
        for item in projection["items"]:
            slots[(str(item.get("date")), str(item.get("start_time")), str(item.get("end_time")))].append(item)
        for slot, items in slots.items():
            activities = sorted({
                str(item.get("activity") or "")
                for item in items
                if item.get("state") == "ACTIVITY" and str(item.get("activity") or "").strip()
            })
            if len(items) > 1:
                duplicate_slots.append({"student": student["display_name"], "slot": slot})
            if len(activities) > 1:
                multiple_activities.append({"student": student["display_name"], "slot": slot, "activities": activities})
        for issue in projection["issues"]:
            unresolved.append({"student": student["display_name"], **issue})
        if len(samples) < 4:
            samples.append({
                "student": student["display_name"], "class_name": student["class_name"],
                "items": len(projection["items"]), "issues": projection["issues"],
                "first_items": projection["items"][:3],
            })

    teacher_samples = []
    for teacher_id in sorted(teacher_ids)[:5]:
        projection = project_teacher(database, teacher_id, week_start)
        teacher_samples.append({"teacher_id": teacher_id, "items": len(projection["items"]), "issues": projection["issues"]})

    return {
        "version": {key: version.get(key) for key in (
            "version_id", "status", "parent_version_id", "source_snapshot_id", "source_fingerprint",
        )},
        "template": {
            "blocks": len((template or {}).get("blocks") or {}),
            "assignments": sum(len(block.get("assignments") or []) for block in ((template or {}).get("blocks") or {}).values()),
        },
        "effective": {
            "week_start": week_start, "status": effective.get("status"),
            "items": len(effective.get("items") or []),
            "effective_week_id": (effective.get("effective_week") or {}).get("effective_week_id"),
            "patch_count": (effective.get("effective_week") or {}).get("patch_count", 0),
        },
        "students": len(students), "state_counts": dict(state_counts),
        "duplicate_student_slot_allocations": len(duplicate_slots),
        "multiple_distinct_activities": len(multiple_activities),
        "unknown_group_refs": sorted(set(unknown_group_refs)),
        "unexplained_unresolved": len(unresolved), "unresolved_examples": unresolved[:10],
        "samples": samples, "teachers_in_assignments": len(teacher_ids),
        "teacher_projection_samples": teacher_samples,
        "canonical_status_api": {
            "selected_version": (selected_status.get("canonical") or {}).get("version_id"),
            "effective_week_present": bool(selected_status.get("effective_week")),
        },
        "canonical_v1": dict(v1) if v1 else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="candidate-v2-grade7-fd6d3f3beccca0fc")
    parser.add_argument("--week-start", default="2026-09-14")
    args = parser.parse_args()
    print(json.dumps(run(args.version, args.week_start), ensure_ascii=False, default=str, indent=2))
