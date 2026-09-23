"""Refresh derived projection counters after an auditable directory update."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.scripts.validate_full_current_v2 import project


def main() -> None:
    artifact_path = Path("docs/SCHEDULE_CANONICAL_V2_FULL_CANDIDATE.json")
    input_path = Path("backend/.current_full_v2_input.json")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result = project(artifact, payload["db"])
    unresolved = result["unresolved"]
    states = result["states"]
    routes = result["routes"]
    students = {str(item["id"]): item for item in payload["db"].get("students") or []}
    active_ids = set(students)
    students_with_routes = set(routes) & active_ids
    unresolved_student_ids = sorted({str(item["student_id"]) for item in unresolved})
    artifact.setdefault("summary", {}).update({
        "student_route_states": dict(states),
        "students_with_unresolved_routes": len(unresolved_student_ids),
        "fully_routable_students": len(active_ids - set(unresolved_student_ids)),
        "active_student_count": len(active_ids),
    })
    artifact["routing_validation"] = {
        "active_student_count": len(active_ids),
        "students_with_routes": len(students_with_routes),
        "students_without_routes": sorted(active_ids - students_with_routes),
        "duplicate_student_slots": result["duplicate_student_slots"],
        "unresolved_student_ids": unresolved_student_ids,
        "exactly_one_effective_state_per_routed_slot": not result["duplicate_student_slots"],
    }
    artifact.setdefault("derived_validation", {})["directory_cleanup"] = {
        "decision_date": "2026-09-14",
        "active_students": len(active_ids),
        "student_route_states": dict(states),
        "unresolved_by_reason": dict(Counter(str(item.get("reason") or "") for item in unresolved)),
        "canonical_semantics_changed": False,
        "source_rosters_changed": False,
        "membership_decisions": ["Кулиш Владимир inactive", "Кулиш Александр -> english:1"],
    }
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(artifact_path), "version_id": artifact.get("version_id"), "student_route_states": dict(states), "unresolved_count": len(unresolved), "active_students": len(active_ids)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
