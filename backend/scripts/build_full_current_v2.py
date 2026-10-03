"""Build a non-authoritative canonical v2 from the live current template.

This script is intentionally a bootstrap tool.  It reads a connector-produced
current-template input, uses the existing row-level V2 resolver, and keeps the
accepted Grade 7 interpretation as-is.  It never writes the directory or
changes memberships.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical
from backend.services.schedule_pipeline import parse_schedule_matrix
from backend.services.school_data import stable_fingerprint
from backend.services.teacher_directory import resolve_teacher_id


BELL_SCHEDULE = (
    ("09:00", "09:45"), ("09:55", "10:40"), ("10:55", "11:40"),
    ("11:50", "12:35"), ("12:45", "13:30"), ("13:40", "14:25"),
    ("14:35", "15:20"), ("15:30", "16:15"), ("16:25", "17:10"),
)
WEEKDAY_TOKENS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4}


def normalize_bell_times(lessons: list[dict[str, Any]], values: list[list[Any]]) -> int:
    """Map source timetable rows to the school's fixed bell schedule.

    Source tabs occasionally contain stale or mistyped labels (e.g. 15:25).
    The row's position within its weekday is the canonical period number.
    """
    row_periods: dict[tuple[int, int], int] = {}
    weekday: int | None = None
    next_period = 0
    time_pattern = re.compile(r"^\s*\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\s*$")
    for row_index, row in enumerate(values):
        first = str((row or [""])[0] or "").strip()
        weekday_hits = []
        for cell in row[1:]:
            token = norm(cell).split(" ", 1)[0] if cell else ""
            if token in WEEKDAY_TOKENS:
                weekday_hits.append(WEEKDAY_TOKENS[token])
        if len(weekday_hits) >= 2:
            weekday = Counter(weekday_hits).most_common(1)[0][0]
            next_period = 0
            continue
        if weekday is None or not time_pattern.fullmatch(first):
            continue
        if next_period < len(BELL_SCHEDULE):
            row_periods[(weekday, row_index)] = next_period
        next_period += 1

    normalized = 0
    for lesson in lessons:
        period = row_periods.get((int(lesson.get("weekday", -1)), int(lesson.get("source_row", -1))))
        if period is None:
            continue
        start, end = BELL_SCHEDULE[period]
        lesson["start_time"], lesson["end_time"] = start, end
        lesson["slot_key"] = f"{lesson.get('weekday')}|{start}|{lesson.get('source_column')}"
        lesson["bell_period"] = period + 1
        normalized += 1
    return normalized


def add_explicit_grade7_group_fallback(
    artifact: dict[str, Any], lessons: list[Mapping[str, Any]],
    groups: list[Mapping[str, Any]], memberships: list[Mapping[str, Any]],
) -> int:
    """Keep explicit current Grade 7 labels editable when inference is ambiguous.

    The live sheet's explicit 7-A/7-B and split 1/2 markers map to existing
    canonical roster groups. This is an intentionally reviewable bootstrap,
    not a claim that the old Grade 7 interpretation still applies.
    """
    group_ids = {str(item.get("name")): str(item["id"]) for item in groups}
    member_ids: dict[str, list[str]] = {}
    for item in memberships:
        member_ids.setdefault(str(item["group_id"]), []).append(str(item["identity_id"]))
    lesson_by_cell = {str(item.get("source_cell")): item for item in lessons}
    routed = 0
    for block in (artifact.get("blocks") or {}).values():
        if str(block.get("grade_scope")) != "7":
            continue
        unresolved = list(block.get("unresolved") or [])
        keep = []
        for issue in unresolved:
            if issue.get("reason") != "explicit instructional audience has no unique canonical group":
                keep.append(issue)
                continue
            cell = str(issue.get("source_cell") or "")
            lesson = lesson_by_cell.get(cell)
            raw = str((lesson or {}).get("raw_text") or issue.get("raw_text") or "")
            base_match = re.search(r"\b7\s*[-–]?\s*([АAБB])\b", raw, re.IGNORECASE)
            split_match = re.search(r"\b(?:мат\w*|физ\w*|биол\w*)\s*(?:группа\s*)?([12])\b", raw, re.IGNORECASE)
            group_name = None
            if base_match:
                letter = "А" if base_match.group(1).casefold() in {"а", "a"} else "Б"
                group_name = f"grade7:base:7-{letter}"
            elif split_match:
                group_name = f"grade7:split:{split_match.group(1)}"
            group_id = group_ids.get(group_name or "")
            if not group_id or not lesson:
                keep.append(issue)
                continue
            teacher_ids = [str(value) for value in lesson.get("resolved_identity_ids") or []]
            activity = str(lesson.get("subject") or lesson.get("raw_text") or "").strip()
            block.setdefault("assignments", []).append({
                "activity": activity, "role": "primary",
                "audience": {"type": "canonical_groups", "canonical_group_ids": [group_id]},
                "student_ids": sorted(member_ids.get(group_id, [])),
                "student_count": len(member_ids.get(group_id, [])),
                "teacher_ids": teacher_ids,
                "source_cells": [cell],
                "metadata": {
                    "approximate_grade7_audience": True,
                    "mapping_basis": "explicit class or split marker from current template",
                    "review_before_accepting": True,
                },
            })
            routed += 1
        block["unresolved"] = keep
        if not keep:
            block["status"] = "resolved"

    previous = {str(item.get("block_key")): item for item in artifact.get("unresolved") or []}
    artifact["unresolved"] = [
        {"block_key": key, "weekday": block.get("weekday"), "slot": block.get("slot"),
         "issues": list(block.get("unresolved") or []),
         "conflict_student_ids": list(previous.get(str(key), {}).get("conflict_student_ids") or [])}
        for key, block in (artifact.get("blocks") or {}).items() if block.get("unresolved")
    ]
    artifact.setdefault("summary", {})["approximate_grade7_assignments"] = routed
    artifact["summary"]["unresolved_blocks"] = len(artifact["unresolved"])
    artifact["summary"]["resolved_blocks"] = max(0, artifact["summary"].get("logical_blocks", 0) - len(artifact["unresolved"]))
    return routed


def add_editable_source_class_fallback(
    artifact: dict[str, Any], lessons: list[Mapping[str, Any]], groups: list[Mapping[str, Any]],
) -> int:
    """Retain a lesson card when semantics are unresolved but its class is explicit."""
    class_groups: dict[str, list[str]] = {}
    for group in groups:
        if norm(group.get("group_type")) != "class":
            continue
        label = norm(group.get("base_class_name") or group.get("name"))
        if label:
            class_groups.setdefault(label, []).append(str(group["id"]))
    lesson_by_cell = {str(item.get("source_cell")): item for item in lessons}
    added = 0
    for block in (artifact.get("blocks") or {}).values():
        assigned_cells = {
            str(cell)
            for assignment in block.get("assignments") or []
            for cell in assignment.get("source_cells") or []
        }
        for issue in block.get("unresolved") or []:
            if issue.get("reason") not in {"missing canonical OGE instructional group", "explicit instructional audience has no unique canonical group"}:
                continue
            coordinates = list(issue.get("source_cells") or [])
            if issue.get("source_cell"):
                coordinates.append(issue["source_cell"])
            for cell in dict.fromkeys(str(value) for value in coordinates):
                if cell in assigned_cells:
                    continue
                lesson = lesson_by_cell.get(cell)
                if not lesson:
                    continue
                source_class = norm(lesson.get("audience"))
                candidates = class_groups.get(source_class, [])
                if len(candidates) != 1:
                    continue
                group_id = candidates[0]
                raw_text = str(lesson.get("raw_text") or "")
                block.setdefault("assignments", []).append({
                    "activity": str(lesson.get("subject") or raw_text).strip(),
                    "role": "primary",
                    "audience": {"type": "canonical_groups", "canonical_group_ids": [group_id]},
                    "teacher_ids": [str(value) for value in lesson.get("resolved_identity_ids") or []],
                    "source_cells": [cell],
                    "metadata": {
                        "approximate_source_class_audience": True,
                        "source_audience_label": str(lesson.get("audience") or ""),
                        "source_raw_text": raw_text,
                        "review_before_accepting": True,
                    },
                })
                assigned_cells.add(cell)
                added += 1
    artifact.setdefault("summary", {})["editable_source_class_fallbacks"] = added
    return added


def norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def cell_by_coord(values: list[list[Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for r, row in enumerate(values, start=1):
        for c, value in enumerate(row, start=1):
            n = c
            letters = ""
            while n:
                n, rem = divmod(n - 1, 26)
                letters = chr(65 + rem) + letters
            result[f"{letters}{r}"] = str(value or "")
    return result


def teacher_id_for(raw: str, hint: str, teachers: list[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    return resolve_teacher_id(raw, hint, teachers)


def add_teacher_resolution(artifact: dict[str, Any], lessons: list[Mapping[str, Any]], teachers: list[Mapping[str, Any]]) -> None:
    teacher_optional_activities = {"Творчество", "Тренинг", "Курс по выбору", "Цифровой трек", "SDEP"}
    by_coord = {str(item.get("source_cell")): item for item in lessons}
    teacher_counts = Counter()
    teacher_issues: list[dict[str, Any]] = []
    for block in artifact.get("blocks", {}).values():
        block_issues = list(block.get("unresolved") or [])
        for assignment in block.get("assignments") or []:
            sources = [by_coord.get(str(coord), {}) for coord in assignment.get("source_cells") or []]
            if assignment.get("activity") == "NO_LESSON":
                continue
            ids: list[str] = []
            issues: list[str] = []
            for source in sources:
                raw = str(source.get("raw_text") or "")
                hint = str(source.get("teacher_hint") or "")
                teacher_id, issue = teacher_id_for(raw, hint, teachers)
                if teacher_id and teacher_id not in ids:
                    ids.append(teacher_id)
                if issue and raw and not raw.lstrip().startswith("⚪"):
                    issues.append(f"{source.get('source_cell')}: {issue}")
            # Existing resolver-provided IDs remain authoritative if present.
            for teacher_id in assignment.get("teacher_ids") or []:
                if str(teacher_id) not in ids:
                    ids.append(str(teacher_id))
            assignment["teacher_ids"] = sorted(ids)
            assignment["teachers"] = sorted({str(item.get("display_name")) for item in teachers if str(item.get("id")) in ids})
            metadata = dict(assignment.get("metadata") or {})
            if issues and assignment.get("activity") not in teacher_optional_activities:
                metadata["teacher_issue"] = "; ".join(sorted(set(issues)))
                teacher_issues.append({"block_key": block.get("block_key"), "source_cells": assignment.get("source_cells"), "reason": metadata["teacher_issue"]})
            elif not ids and assignment.get("activity") not in teacher_optional_activities:
                metadata["teacher_issue"] = "teacher_id=null: source does not provide a unique teacher"
                teacher_issues.append({"block_key": block.get("block_key"), "source_cells": assignment.get("source_cells"), "reason": metadata["teacher_issue"]})
            assignment["metadata"] = metadata
            if ids:
                teacher_counts["resolved"] += 1
            elif assignment.get("activity") in teacher_optional_activities:
                teacher_counts["optional"] += 1
            else:
                teacher_counts["unresolved"] += 1
        if block_issues:
            block["unresolved"] = block_issues
    artifact["teacher_resolution"] = {
        "resolved_assignment_count": teacher_counts["resolved"],
        "unresolved_assignment_count": teacher_counts["unresolved"],
        "optional_assignment_count": teacher_counts["optional"],
        "issues": teacher_issues,
        "rule": "explicit source teacher text/alias matched uniquely to active teacher identity; otherwise null with reason",
    }


def normalize_grade7_candidate_block(candidate: Mapping[str, Any], groups: list[Mapping[str, Any]], memberships: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_name = {str(item.get("name")): str(item["id"]) for item in groups}
    member_map: dict[str, set[str]] = {}
    for item in memberships:
        member_map.setdefault(str(item["group_id"]), set()).add(str(item["identity_id"]))
    source_cells = list(candidate.get("source_cells") or [])
    if not source_cells:
        source_cells = [{"coordinate": item.get("source_coordinate"), "source_audience": item.get("source_audience"), "raw_text": item.get("raw_text")} for item in candidate.get("assignments") or []]
    universe = set()
    for group_name in ("grade7:base:7-А", "grade7:base:7-Б"):
        universe.update(member_map.get(by_name.get(group_name, ""), set()))
    assignments: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for ordinal, item in enumerate(candidate.get("assignments") or []):
        if str(item.get("activity")) == "NO_LESSON":
            continue
        group_ids = [str(value) for value in item.get("canonical_group_ids") or []]
        # A row-level split can contain an ordinary class lesson next to an
        # explicitly split lesson.  The accepted Grade 7 candidate records
        # that ordinary cell as an "inferred complement", but that inference
        # must not make the cell inherit split 1/2.  Its own source audience
        # is the stronger evidence: ordinary cells route to 7-А/7-Б, while
        # only an explicit split marker routes to grade7:split:1/2.
        if (
            item.get("routing_dimension") == "SHARED_SPLIT_1_2"
            and bool(item.get("inferred_complement"))
            and not re.search(r"\b(?:мат|математика|физ|физика)\s*(?:группа\s*)?[12]\b", str(item.get("raw_text") or ""), re.IGNORECASE)
        ):
            label = str(item.get("source_audience") or "").strip()
            base_id = by_name.get(f"grade7:base:{label}")
            if base_id:
                group_ids = [base_id]
        if not group_ids:
            label = str(item.get("source_audience") or ((item.get("audience") or {}).get("label") if isinstance(item.get("audience"), Mapping) else ""))
            group_id = by_name.get(f"grade7:base:{label}")
            if group_id:
                group_ids = [group_id]
        student_ids = set().union(*(member_map.get(group_id, set()) for group_id in group_ids)) & universe if group_ids else set()
        if not student_ids:
            continue
        claimed.update(student_ids)
        raw = str(item.get("raw_text") or "")
        assignments.append({
            "activity": item.get("activity") or raw.splitlines()[0],
            "role": "primary" if item.get("routing_dimension") in {"BASE_CLASS_A_B", "SHARED_SPLIT_1_2", "ENGLISH_3_4_5"} else "special",
            "audience": {"type": "canonical_groups", "canonical_group_ids": group_ids},
            "student_ids": sorted(student_ids), "student_count": len(student_ids),
            "teacher_ids": [], "teachers": [], "source_cells": [item.get("source_coordinate")],
            "metadata": {
                "accepted_grade7_routing_dimension": item.get("routing_dimension"),
                "source_audience": item.get("source_audience"),
                "source_cell_precedence": "explicit split marker > source audience for inferred complement"
                if item.get("routing_dimension") == "SHARED_SPLIT_1_2" and item.get("inferred_complement")
                else None,
            },
        })
    remaining = universe - claimed
    if remaining:
        assignments.append({
            "activity": "NO_LESSON", "role": "final_unassigned",
            "audience": {"type": "canonical_groups", "canonical_group_ids": []},
            "student_ids": sorted(remaining), "student_count": len(remaining),
            "teacher_ids": [], "teachers": [], "source_cells": [], "metadata": {"rule": "final state after Grade 7 semantic resolution"},
        })
    slot_start = str((candidate.get("slot") or {}).get("start") or "")
    match = re.match(r"\s*(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", slot_start)
    if not match:
        match = re.match(r"\s*(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", str((candidate.get("slot") or {}).get("end") or ""))
    day_map = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4}
    weekday = day_map.get(norm(candidate.get("weekday")))
    return {
        "block_key": candidate.get("block_key"), "weekday": weekday,
        "slot": {"start": match.group(1) if match else slot_start, "end": match.group(2) if match else None},
        "grade_scope": "7", "status": "resolved", "interpretation_source": "accepted_grade7_candidate_v2",
        "mode": candidate.get("row_routing_dimension") or "DETERMINISTIC_SIMPLE", "assignments": assignments,
        "unresolved": [], "source_warnings": [],
        "derived_from": {"sheet": "2026/27 шаблон", "source_cells": source_cells,
                         "structural_fingerprint": stable_fingerprint(source_cells)},
        "evidence": {"accepted_candidate_block_key": candidate.get("block_key"), "residual_behavior": candidate.get("residual_behavior")},
    }


def merge_grade7(artifact: dict[str, Any], candidate: Mapping[str, Any], groups: list[Mapping[str, Any]], memberships: list[Mapping[str, Any]]) -> dict[str, Any]:
    candidate_blocks = candidate.get("blocks") or {}
    if isinstance(candidate_blocks, list):
        candidate_blocks = {str(item.get("block_key")): item for item in candidate_blocks}
    replaced = 0
    by_source: dict[str, Mapping[str, Any]] = {}
    for item in candidate_blocks.values():
        for source in item.get("source_cells") or []:
            if source.get("coordinate"):
                by_source[str(source["coordinate"])] = item
    for key, block in list(artifact.get("blocks", {}).items()):
        if str(block.get("grade_scope")) != "7":
            continue
        coordinates = [str(item.get("coordinate")) for item in block.get("derived_from", {}).get("source_cells", []) if item.get("coordinate")]
        replacement = next((by_source[item] for item in coordinates if item in by_source), None) or candidate_blocks.get(key)
        if replacement is None:
            # The accepted candidate is keyed by the same live source layout;
            # keep the mismatch explicit rather than inventing a mapping.
            block.setdefault("unresolved", []).append({"reason": "accepted Grade 7 candidate has no matching current source block"})
            continue
        artifact["blocks"][key] = normalize_grade7_candidate_block(replacement, groups, memberships)
        artifact["blocks"][key]["block_key"] = key
        replaced += 1
    artifact.setdefault("grade7_reuse", {})["candidate_version_id"] = candidate.get("version_id")
    artifact["grade7_reuse"]["replaced_block_count"] = replaced
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--values", type=Path, help="Fresh source values JSON; replaces values in --input")
    parser.add_argument("--grade7", type=Path, help="Optional reviewed Grade 7 override; omitted to use the live source")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-version-id", default="4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if args.values:
        payload["values"] = json.loads(args.values.read_text(encoding="utf-8"))
    source_values = payload["values"]
    db = payload["db"]
    tab = {"sheet_id": payload["sheet_id"], "title": payload["title"], "values": source_values}
    lessons = parse_schedule_matrix(tab, "Расписание 2026/27")
    normalized_bell_lessons = normalize_bell_times(lessons, source_values)
    teachers = list(db.get("teachers") or [])
    teacher_names = {norm(item.get("display_name")): str(item["id"]) for item in teachers}
    for lesson in lessons:
        teacher_id, issue = teacher_id_for(str(lesson.get("raw_text") or ""), str(lesson.get("teacher_hint") or ""), teachers)
        lesson["resolved_identity_ids"] = [teacher_id] if teacher_id else []
        lesson["teacher_resolution_issue"] = issue
    source_fingerprint = stable_fingerprint({"spreadsheet_id": payload["spreadsheet_id"], "sheet_id": payload["sheet_id"], "values": source_values})
    corpus = {
        "snapshot": {
            "id": f"live-template-{payload['sheet_id']}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "source_fingerprint": source_fingerprint,
            "fingerprint": source_fingerprint,
            "spreadsheet_id": payload["spreadsheet_id"],
            "sheet_id": payload["sheet_id"],
            "tab_title": payload["title"],
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
        "lessons": lessons,
        "groups": db.get("groups") or [],
        "students": db.get("students") or [],
        "memberships": db.get("memberships") or [],
        "teachers": teachers,
    }
    artifact = build_bootstrap_canonical(corpus, explicit_sdep=())
    if args.grade7:
        candidate = json.loads(args.grade7.read_text(encoding="utf-8"))
        artifact = merge_grade7(artifact, candidate, corpus["groups"], corpus["memberships"])
    approximate_grade7_assignments = add_explicit_grade7_group_fallback(artifact, lessons, corpus["groups"], corpus["memberships"])
    editable_source_class_fallbacks = add_editable_source_class_fallback(artifact, lessons, corpus["groups"])
    add_teacher_resolution(artifact, lessons, teachers)
    artifact["schema_version"] = "canonical-schedule-v2"
    artifact["interpretation_source"] = "live_template_agent_bootstrap_v2"
    artifact["parent_version_id"] = args.parent_version_id
    artifact["status"] = "approved_with_exceptions" if artifact.get("unresolved") or artifact.get("teacher_resolution", {}).get("issues") else "candidate"
    artifact["authoritative"] = False
    artifact["source_snapshot"] = corpus["snapshot"]
    artifact["directory_fingerprint"] = stable_fingerprint({"groups": corpus["groups"], "memberships": corpus["memberships"]})
    artifact["source_metadata"] = {
        "structured_values": True,
        "source_cells": len(lessons),
        "bell_times_normalized": normalized_bell_lessons,
        "approximate_grade7_assignments": approximate_grade7_assignments,
        "editable_source_class_fallbacks": editable_source_class_fallbacks,
        "bell_schedule": [{"period": index + 1, "start": start, "end": end} for index, (start, end) in enumerate(BELL_SCHEDULE)],
        "sheet": payload["title"],
        "sheet_id": payload["sheet_id"],
        "source_of_truth": "live Google Sheet current template",
        "historical_week_excluded": "7-11 Сентября",
    }
    artifact["version_id"] = stable_fingerprint({"parent": artifact["parent_version_id"], "source": artifact["source_snapshot"], "blocks": artifact["blocks"]})
    artifact.setdefault("summary", {})["teacher_resolved_assignments"] = artifact["teacher_resolution"]["resolved_assignment_count"]
    artifact["summary"]["teacher_unresolved_assignments"] = artifact["teacher_resolution"]["unresolved_assignment_count"]
    artifact["summary"]["grade7_reused_blocks"] = artifact.get("grade7_reuse", {}).get("replaced_block_count", 0)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"version_id": artifact["version_id"], "summary": artifact["summary"], "grade7_reuse": artifact.get("grade7_reuse"), "teacher_resolution": artifact["teacher_resolution"], "bell_times_normalized": normalized_bell_lessons}, ensure_ascii=False))


if __name__ == "__main__":
    main()
