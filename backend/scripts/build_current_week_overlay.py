"""Offline adapter for the shared canonical weekly-ingestion engine."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from backend.scripts.validate_full_current_v2 import project, teacher_projection
from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical
from backend.services.weekly_schedule_ingestion import (
    assignment_signature, build_weekly_diff, by_source_column, comparison_key,
    layout_profile, meta_map, norm, parse_structure, parse_weekly_lessons,
    raw_by_coord, semantic_norm,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--template-input", type=Path, required=True)
    parser.add_argument("--template-meta", type=Path, required=True)
    parser.add_argument("--weekly-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    template_input = json.loads(args.template_input.read_text(encoding="utf-8"))
    template_meta_input = json.loads(args.template_meta.read_text(encoding="utf-8"))
    weekly_input = json.loads(args.weekly_input.read_text(encoding="utf-8"))
    db, teachers = template_input["db"], template_input["db"].get("teachers") or []
    week_start = str(weekly_input["week_start"])
    template_merges = template_input.get("merges") or []
    weekly_merges = weekly_input.get("merges") or []
    template_parsed = parse_structure(template_input["values"], sheet_id=str(template_input["sheet_id"]), title=str(template_input["title"]), week_start=week_start, merges=template_merges)
    weekly_parsed = parse_structure(weekly_input["values"], sheet_id=str(weekly_input["sheet_id"]), title=str(weekly_input["sheet_title"]), week_start=week_start, merges=weekly_merges)
    weekly_lessons = parse_weekly_lessons(weekly_parsed, teachers)
    source_payload = {key: weekly_input.get(key) for key in ("spreadsheet_id", "spreadsheet_title", "sheet_id", "sheet_title", "range", "week_start", "week_end", "values", "structured_cells", "merges", "merges_note")}
    source_fingerprint = hashlib.sha256(json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    weekly_candidate = build_bootstrap_canonical({"lessons": weekly_lessons, "students": db.get("students") or [], "teachers": teachers, "groups": db.get("groups") or [], "memberships": db.get("memberships") or [], "snapshot": {"id": f"weekly-raw-{weekly_input['sheet_id']}-{week_start}", "fingerprint": source_fingerprint}})
    diff = build_weekly_diff(artifact, template_parsed, weekly_parsed, weekly_candidate, meta_map(template_meta_input), meta_map(weekly_input.get("structured_cells") or [], weekly=True))
    effective_artifact = copy.deepcopy(artifact)
    effective_blocks = effective_artifact["blocks"]
    for item in diff["items"]:
        if item["classification"] == "UNCHANGED":
            continue
        base = effective_blocks.get(item["canonical_block_key"]) if item["canonical_block_key"] else None
        if base is None:
            if item.get("weekly_block"):
                effective_blocks[item["patch"]["block_key"]] = copy.deepcopy(item["weekly_block"])
            continue
        patch = item["patch"]
        if item["change_kind"] == "cancelled":
            base["assignments"] = []
        elif "assignments" in patch:
            base["assignments"] = copy.deepcopy(patch["assignments"])
        if item["change_kind"] == "moved":
            base["weekday"], base["slot"], base["grade_scope"] = patch["weekday"], patch["slot"], patch["grade_scope"]
    student, teacher = project(effective_artifact, db), teacher_projection(effective_artifact, db)
    template_raw, weekly_raw = raw_by_coord(template_parsed), raw_by_coord(weekly_parsed)
    changed = [item for item in diff["items"] if item["classification"] != "UNCHANGED"]
    spot_checks = [{"classification": item["classification"], "key": item["key"], "template": [{"cell": coord, "raw": template_raw.get(coord, {}).get("raw_text")} for coord in item["template_cells"]], "weekly": [{"cell": coord, "raw": weekly_raw.get(coord, {}).get("raw_text")} for coord in item["weekly_cells"]], "overlay": item["patch"]} for item in changed[:10]]
    report = {
        "source_snapshot": {"id": f"weekly-raw-{weekly_input['sheet_id']}-{week_start}", "fingerprint": source_fingerprint, "sheet_id": weekly_input["sheet_id"], "week_start": week_start, "week_end": weekly_input["week_end"]},
        "source_payload": source_payload, "layout": layout_profile(weekly_input["values"], weekly_merges),
        "diff_counts": diff["counts"], "overlay": diff["patches"], "overlay_patch_count": len(diff["patches"]),
        "effective_block_count": len(effective_blocks), "baseline_artifact_version": artifact.get("version_id"),
        "student_projection": {"states": student["states"], "duplicate_student_slots": student["duplicate_student_slots"], "multiple_activities": student["multiple_activities"], "unknown_group_refs": student["unknown_group_refs"], "unresolved_count": len(student["unresolved"]), "unresolved_by_reason": dict(Counter(str(item.get("reason") or "") for item in student["unresolved"])), "active_students": len(db.get("students") or [])},
        "teacher_projection": {"resolved_assignments": teacher["resolved_assignments"], "unresolved_assignments": teacher["unresolved_assignments"], "orphan_expected_teacher": teacher["orphan_expected_teacher"], "simultaneous_conflicts": teacher["simultaneous_conflicts"]},
        "spot_checks": spot_checks, "semantic_resolution": "shared weekly engine; V2 resolver only for changed blocks", "weekly_candidate_unresolved_blocks": len(weekly_candidate.get("unresolved") or []),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"source_snapshot": report["source_snapshot"], "diff_counts": diff["counts"], "overlay_patch_count": len(diff["patches"]), "effective_block_count": len(effective_blocks)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
