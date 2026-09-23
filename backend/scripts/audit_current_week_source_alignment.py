"""Read-only audit of current-week source cells against effective v2 audiences."""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.scripts.build_current_week_overlay import parse_structure
from backend.scripts.validate_full_current_v2 import project


TRACK_SUBJECTS = {
    "физ": "физика",
    "хим": "химия",
    "био": "биология",
    "геог": "география",
    "истор": "история",
    "литер": "литература",
    "информ": "информатика",
    "обществ": "обществознание",
}


def grade(value: Any) -> str:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return match.group(1) if match else ""


def token(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<!\d){re.escape(value)}(?!\d)", text))


def expected_group(raw: Any, audience: Any) -> str | None:
    text = str(raw or "").casefold()
    class_name = str(audience or "")
    current_grade = grade(class_name)
    if current_grade not in {"7", "9", "10", "11"}:
        return None
    if not text or text.startswith(("🥨", "⚪")) or "обед" in text:
        return None
    if current_grade == "7":
        for number in ("3", "4", "5"):
            if "англ" in text and token(text, number):
                return f"english:{number}"
        for number in ("1", "2"):
            if token(text, number) and any(word in text for word in ("мат", "физ", "биолог", "рус")):
                return f"grade7:split:{number}"
        if any(word in text for word in ("русский", "литер", "истори", "обществ", "географ", "естеств", "классн")):
            return f"grade7:base:{class_name}"
        return None
    if current_grade == "9":
        if "англ" in text:
            for number in ("6", "7", "8"):
                if token(text, number):
                    return f"english:{number}"
        if "мат" in text or "матем" in text:
            match = re.search(r"(?<![а-яa-z])([abcабв])(?![а-яa-z])", text)
            if match:
                # The sheet uses Cyrillic look-alikes А/В/С for the three
                # canonical Math lanes; Cyrillic Б is not the second lane.
                return f"grade9-math-{'ABC'['авс'.index(match.group(1))] if match.group(1) in 'авс' else match.group(1).upper()}"
        if "огэ" in text:
            subject = next((value for marker, value in TRACK_SUBJECTS.items() if marker in text), None)
            if subject:
                return f"instructional:{subject}:9:огэ"
        if any(word in text for word in ("русский", "литер", "истори", "обществ", "географ", "естеств", "классн")):
            return class_name
        return None
    if "англ" in text:
        for number in ("9", "10"):
            if token(text, number):
                return f"english:{number}"
    if "егэ" in text or "огэ" in text:
        subject = next((value for marker, value in TRACK_SUBJECTS.items() if marker in text), None)
        if subject:
            exam = "огэ" if "огэ" in text else "егэ"
            return f"instructional:{subject}:{current_grade}:{exam}"
    if "матпроф" in text or ("мат" in text and "проф" in text):
        return "math:11:advanced"
    if "мат" in text and "база" in text and current_grade == "11":
        return "math:11:base"
    return None


def build_effective(artifact: dict[str, Any], overlay_report: dict[str, Any]) -> dict[str, Any]:
    effective = copy.deepcopy(artifact)
    for item in overlay_report.get("overlay") or []:
        block = effective.get("blocks", {}).get(item.get("canonical_block_key"))
        if not block:
            continue
        if item.get("change_kind") == "cancelled":
            block["assignments"] = []
        elif "assignments" in item.get("patch", {}):
            block["assignments"] = copy.deepcopy(item["patch"]["assignments"])
        if item.get("change_kind") == "moved":
            patch = item.get("patch") or {}
            block["weekday"] = patch.get("weekday", block.get("weekday"))
            block["slot"] = patch.get("slot", block.get("slot"))
            block["grade_scope"] = patch.get("grade_scope", block.get("grade_scope"))
    return effective


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--db-input", type=Path, required=True)
    parser.add_argument("--weekly-input", type=Path, required=True)
    parser.add_argument("--overlay-report", type=Path, required=True)
    args = parser.parse_args()

    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    db = json.loads(args.db_input.read_text(encoding="utf-8"))["db"]
    weekly = json.loads(args.weekly_input.read_text(encoding="utf-8"))
    overlay_report = json.loads(args.overlay_report.read_text(encoding="utf-8"))
    effective = build_effective(artifact, overlay_report)
    projection = project(effective, db)

    groups = {str(item["id"]): item for item in db.get("groups") or []}
    students = {str(item["id"]): item for item in db.get("students") or []}
    memberships: dict[str, set[str]] = defaultdict(set)
    for item in db.get("memberships") or []:
        if str(item.get("status", "active")).lower() == "active":
            memberships[str(item["group_id"])].add(str(item["identity_id"]))
    name_to_id = {str(item.get("name") or ""): key for key, item in groups.items()}

    source_items: dict[str, dict[str, Any]] = {}
    parsed = parse_structure(
        weekly["values"],
        sheet_id=str(weekly["sheet_id"]),
        title=str(weekly["sheet_title"]),
        week_start="2026-09-14",
    )
    for items in parsed.values():
        for item in items:
            source_items[str(item["source_cell"])] = item

    blocks_by_cell: dict[str, set[str]] = defaultdict(set)
    assignments_by_cell_block: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for block_key, block in (effective.get("blocks") or {}).items():
        for source in (block.get("derived_from") or {}).get("source_cells") or []:
            coordinate = source.get("coordinate") if isinstance(source, dict) else source
            if coordinate:
                blocks_by_cell[str(coordinate)].add(str(block_key))
        for assignment in block.get("assignments") or []:
            for coordinate in assignment.get("source_cells") or []:
                assignments_by_cell_block[(str(coordinate), str(block_key))].append(assignment)

    rows: list[dict[str, Any]] = []
    for coordinate, source in sorted(source_items.items()):
        expected = expected_group(source.get("raw_text"), source.get("audience"))
        # In this corpus the authoritative roster calls the EGE partition
        # "Угл" and does not set exam_track.  Compare against that canonical
        # group when no explicit EGE-named group exists.
        if expected and expected.endswith(":егэ") and expected not in name_to_id:
            advanced_name = expected.rsplit(":", 1)[0] + ":угл"
            if advanced_name in name_to_id:
                expected = advanced_name
        if not expected:
            continue
        for block_key in sorted(blocks_by_cell.get(coordinate, set())):
            block = effective.get("blocks", {}).get(block_key) or {}
            if not block.get("slot") or str((block.get("slot") or {}).get("start") or "") == "SDEP":
                continue
            actual_names: set[str] = set()
            actual_student_ids: set[str] = set()
            assignments: list[dict[str, Any]] = []
            for assignment in assignments_by_cell_block.get((coordinate, block_key), []):
                if str(assignment.get("activity") or "") == "NO_LESSON":
                    continue
                audience = assignment.get("audience") if isinstance(assignment.get("audience"), dict) else {}
                group_ids = assignment.get("canonical_group_ids") or audience.get("canonical_group_ids") or []
                names = [groups.get(str(group_id), {}).get("name", str(group_id)) for group_id in group_ids]
                actual_names.update(names)
                actual_student_ids.update(str(student_id) for student_id in assignment.get("student_ids") or [])
                assignments.append({"activity": assignment.get("activity"), "groups": names, "student_count": len(assignment.get("student_ids") or [])})
            if expected in actual_names:
                continue
            expected_id = name_to_id.get(expected)
            expected_students = memberships.get(expected_id, set()) if expected_id else set()
            missing = expected_students - actual_student_ids
            wrong = actual_student_ids - expected_students
            rows.append({
                "cell": coordinate,
                "class": source.get("audience"),
                "weekday": block.get("weekday"),
                "slot": block.get("slot"),
                "raw": str(source.get("raw_text") or "").replace("\n", " / "),
                "expected_group": expected,
                "actual_groups": sorted(actual_names),
                "assignments": assignments,
                "missing_students": len(missing),
                "wrong_students": len(wrong),
                "missing_sample": sorted(students[item].get("display_name", "") for item in missing if item in students)[:8],
                "wrong_sample": sorted(students[item].get("display_name", "") for item in wrong if item in students)[:8],
            })

    unresolved_ids = {str(item["student_id"]) for item in projection.get("unresolved") or []}
    target_students = [item for item in db.get("students") or [] if grade(item.get("class_name")) in {"7", "9", "10", "11"}]
    target_items = [item for student in target_students for item in projection.get("routes", {}).get(str(student["id"]), [])]
    target_clean_items = [item for student in target_students if str(student["id"]) not in unresolved_ids for item in projection.get("routes", {}).get(str(student["id"]), [])]
    print(json.dumps({
        "projection": {
            "target_students": len(target_students),
            "known_unresolved_students_excluded": len(unresolved_ids),
            "clean_students": len(target_students) - len(unresolved_ids),
            "clean_student_slots": len(target_clean_items),
            "states_all_target": dict(Counter(item["state"] for item in target_items)),
            "states_clean": dict(Counter(item["state"] for item in target_clean_items)),
            "duplicates_all_target": len(projection.get("duplicate_student_slots") or []),
            "multiple_activities_all_target": len(projection.get("multiple_activities") or []),
            "unknown_group_refs_all_target": len(projection.get("unknown_group_refs") or []),
        },
        "source_alignment": {
            "high_confidence_mismatch_rows": len(rows),
            "by_grade": dict(Counter(grade(item["class"]) for item in rows)),
            "by_mapping": {f"{key[0]} -> {key[1]}": value for key, value in Counter((item["expected_group"], ",".join(item["actual_groups"])) for item in rows).items()},
            "missing_student_refs": sum(item["missing_students"] for item in rows),
            "wrong_student_refs": sum(item["wrong_students"] for item in rows),
            "rows": rows,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
