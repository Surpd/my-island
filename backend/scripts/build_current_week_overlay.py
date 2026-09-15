"""Build a V2 weekly overlay from the live weekly sheet snapshot.

The sheet is read as structure only here.  Unchanged canonical blocks are
reused; changed source rows are resolved with the existing V2 bootstrap
resolver and never with the legacy semantic parser.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from backend.scripts.build_full_current_v2 import teacher_id_for
from backend.services.schedule_canonical_bootstrap import build_bootstrap_canonical
from backend.services.schedule_parser_v2 import classify_simple_activity, normalize_no_lesson
from backend.scripts.validate_full_current_v2 import project, teacher_projection, normalize_key


CLASS_RE = re.compile(r"^\s*\d{1,2}(?:-[А-ЯЁA-Z])?(?:-\d+)?\s*$", re.IGNORECASE)
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")
DAYS = {"пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2, "чт": 3, "четверг": 3, "пт": 4, "пятница": 4}
ROOM_LINE_RE = re.compile(r"^\s*(?:каб(?:инет)?\.?\s*[^\n]*|зал(?:\s*[^\n]*)?|\d{1,3})\s*$", re.IGNORECASE)
ROOM_INLINE_RE = re.compile(r"\s+(?:каб(?:инет)?\.?\s*[^\n]*|зал(?:\s*[^\n]*)?)(?=\s*$)", re.IGNORECASE)


def norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def semantic_norm(value: Any) -> str:
    """Normalize lesson meaning while ignoring cabinet/room-only edits.

    Weekly sheets commonly change only the room line.  Such a change must be
    retained as source metadata, but it must not re-run semantic resolution and
    accidentally create a new audience/unresolved result.  Teacher, subject,
    exam track and delivery markers (including ``онлайн``) remain semantic.
    """
    lines = []
    for line in str(value or "").splitlines():
        if ROOM_LINE_RE.fullmatch(line):
            continue
        lines.append(ROOM_INLINE_RE.sub("", line))
    return norm("\n".join(lines))


def grade(value: Any) -> str:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return match.group(1) if match else ""


def a1(row: int, column: int) -> str:
    number, result = column + 1, ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return f"{result}{row + 1}"


def cell_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("formattedValue") or value.get("value") or "")
    return str(value or "")


def day_date(week_start: str, weekday: int) -> str:
    return (date.fromisoformat(week_start) + timedelta(days=weekday)).isoformat()


def parse_structure(values: list[list[Any]], *, sheet_id: str, title: str, week_start: str) -> dict[tuple[int, str, str, str], list[dict[str, Any]]]:
    header_index = next((r for r, row in enumerate(values[:12]) if sum(bool(CLASS_RE.fullmatch(cell_text(v).strip())) for v in row[1:]) >= 2), None)
    if header_index is None:
        raise ValueError(f"{title}: class header not found")
    width = max((len(row) for row in values), default=0)
    header = list(values[header_index]) + [""] * max(0, width - len(values[header_index]))
    owners = [""] * width
    owner = ""
    for column in range(1, width):
        value = cell_text(header[column]).strip()
        if CLASS_RE.fullmatch(value):
            owner = value
        elif value:
            owner = ""
        owners[column] = owner

    result: dict[tuple[int, str, str, str], list[dict[str, Any]]] = {}
    current_weekday: int | None = None
    day_labels = [""] * width
    for row_index, row in enumerate(values[header_index + 1:], start=header_index + 1):
        first = cell_text(row[0]).strip() if row else ""
        hits = []
        for value in row[1:]:
            token = norm(cell_text(value)).split(" ", 1)[0] if norm(cell_text(value)) else ""
            if token in DAYS:
                hits.append(DAYS[token])
        if len(hits) >= 2:
            current_weekday = Counter(hits).most_common(1)[0][0]
            day_labels = [""] * width
            inherited, inherited_owner = "", ""
            for column in range(1, width):
                current_owner = owners[column] if column < len(owners) else ""
                raw_label = cell_text(row[column]).strip() if column < len(row) else ""
                if current_owner != inherited_owner:
                    inherited, inherited_owner = "", current_owner
                if raw_label:
                    inherited = raw_label
                day_labels[column] = inherited
            continue
        match = TIME_RE.fullmatch(first)
        if not match or current_weekday is None:
            continue
        start_time = f"{int(match.group(1)):02d}:{match.group(2)}"
        end_time = f"{int(match.group(3)):02d}:{match.group(4)}"
        for column, value in enumerate(row[1:], start=1):
            raw_text = cell_text(value).strip()
            if not raw_text or column >= len(owners):
                continue
            audience = owners[column]
            target_grade = grade(audience)
            if not target_grade:
                continue
            key = (current_weekday, start_time, end_time, target_grade)
            result.setdefault(key, []).append({
                "source_cell": a1(row_index, column),
                "source_column": column,
                "source_row": row_index,
                "raw_text": raw_text,
                "audience": audience,
                "source_day_label": day_labels[column] if column < len(day_labels) else "",
                "sheet_id": sheet_id,
                "tab_title": title,
                "lesson_date": day_date(week_start, current_weekday),
                "weekday": current_weekday,
                "start_time": start_time,
                "end_time": end_time,
            })
    return result


def meta_map(items: list[Mapping[str, Any]], *, weekly: bool = False) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        coordinate = str(item.get("coordinate") or "")
        fmt = item.get("effectiveFormat") or {} if weekly else {}
        result[coordinate] = {
            "background_color": item.get("background_color") if not weekly else (fmt.get("backgroundColorStyle", {}).get("rgbColor") or fmt.get("backgroundColor")),
            "text_format": item.get("text_format") if not weekly else fmt.get("textFormat"),
            "note": item.get("note"),
        }
    return result


def source_map(block: Mapping[str, Any], parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]], key: tuple[int, str, str, str]) -> list[str]:
    coords = []
    for item in (block.get("derived_from") or {}).get("source_cells") or []:
        coordinate = item.get("coordinate") if isinstance(item, Mapping) else item
        if coordinate:
            coords.append(str(coordinate))
    return coords or [str(item["source_cell"]) for item in parsed.get(key, [])]


def raw_by_coord(parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {str(item["source_cell"]): item for cells in parsed.values() for item in cells}


def by_source_column(items: list[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    """Index cells within one logical block by source column, not row number."""
    return {int(item["source_column"]): item for item in items if item.get("source_column") is not None}


def subject_hint(raw: str) -> str:
    first = str(raw).splitlines()[0].strip()
    if classify_simple_activity(raw):
        return first
    match = re.split(r"\s+(?=\d|[A-ZА-ЯЁ])", first, maxsplit=1)
    return match[0].strip() if match else first


def parse_weekly_lessons(parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]], teachers: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    lessons = []
    for cells in parsed.values():
        for cell in cells:
            raw = str(cell["raw_text"])
            teacher_id, teacher_issue = teacher_id_for(raw, "", teachers)
            activity_type = "extracurricular" if raw.lstrip().startswith("⚪") else "nonlesson" if normalize_no_lesson(raw) else "simple_activity" if classify_simple_activity(raw) else "lesson"
            room_match = re.search(r"(?:каб(?:\.|инет)?\s*[^\n]+|зал[^\n]*|онлайн[^\n]*)", raw, re.IGNORECASE)
            parsed_cell = {
                "subject": subject_hint(raw),
                "teacher_hint": raw,
                "room": room_match.group(0).strip() if room_match else "",
                "activity_type": activity_type,
                "modifiers": {"exam_track": "ОГЭ" if re.search(r"\bогэ\b", raw, re.IGNORECASE) else "", "subject_subgroup": ""},
                "parse_status": "validated" if teacher_id else "ambiguous",
                "resolved_identity_ids": [teacher_id] if teacher_id else [],
            }
            lessons.append({**cell, **parsed_cell, "record_key": f"{cell['sheet_id']}:{cell['source_cell']}", "raw_payload": {"raw_text": raw, "source_column": cell["source_column"], "source_row": cell["source_row"], "merged_audiences": []}})
    return lessons


def assignment_signature(block: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    result = set()
    for item in block.get("assignments") or []:
        audience = item.get("audience") if isinstance(item.get("audience"), Mapping) else {}
        groups = tuple(sorted(str(x) for x in audience.get("canonical_group_ids") or item.get("canonical_group_ids") or []))
        result.add((str(item.get("activity") or ""), groups, tuple(sorted(str(x) for x in item.get("teacher_ids") or []))))
    return result


def comparison_key(block: Mapping[str, Any]) -> tuple[int, str, str, str]:
    """Normalize legacy short-time formatting before template/week comparison."""
    weekday, start, end, scope = normalize_key(block)
    def padded(value: str) -> str:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
        return f"{int(match.group(1)):02d}:{match.group(2)}" if match else value
    return int(weekday), padded(start), padded(end), scope


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
    template_meta = json.loads(args.template_meta.read_text(encoding="utf-8"))
    weekly_input = json.loads(args.weekly_input.read_text(encoding="utf-8"))
    db = template_input["db"]
    teachers = db.get("teachers") or []
    template_values = template_input["values"]
    weekly_values = weekly_input["values"]
    template_parsed = parse_structure(template_values, sheet_id=str(template_input["sheet_id"]), title=str(template_input["title"]), week_start="2026-09-14")
    weekly_parsed = parse_structure(weekly_values, sheet_id=str(weekly_input["sheet_id"]), title=str(weekly_input["sheet_title"]), week_start="2026-09-14")
    template_raw = raw_by_coord(template_parsed)
    weekly_raw = raw_by_coord(weekly_parsed)
    template_meta_by_coord = meta_map(template_meta)
    weekly_meta_by_coord = meta_map(weekly_input.get("structured_cells") or [], weekly=True)
    weekly_lessons = parse_weekly_lessons(weekly_parsed, teachers)
    weekly_corpus = {"lessons": weekly_lessons, "students": db.get("students") or [], "teachers": teachers, "groups": db.get("groups") or [], "memberships": db.get("memberships") or [], "snapshot": {"id": "weekly-raw-646998161-2026-09-14", "fingerprint": ""}}
    weekly_candidate = build_bootstrap_canonical(weekly_corpus, explicit_sdep=((4, "10"), (4, "11")))
    canonical_by_key = {comparison_key(block): block for block in (artifact.get("blocks") or {}).values()}
    weekly_by_key = {comparison_key(block): block for block in (weekly_candidate.get("blocks") or {}).values() if block.get("slot")}
    template_blocks_by_key = template_parsed
    diffs: list[dict[str, Any]] = []

    for key, canonical in sorted(canonical_by_key.items()):
        structural_key = (int(key[0]), key[1], key[2], key[3])
        if key[1] == "" and key[2] == "":
            continue
        base_coords = source_map(canonical, template_blocks_by_key, structural_key)
        weekly_cells = weekly_parsed.get(structural_key, [])
        base_items = {coord: template_raw.get(coord, {"source_cell": coord, "raw_text": ""}) for coord in base_coords}
        week_items = {str(item["source_cell"]): item for item in weekly_cells}
        base_nonempty = by_source_column([item for item in base_items.values() if str(item.get("raw_text") or "").strip()])
        week_nonempty = by_source_column([item for item in week_items.values() if str(item.get("raw_text") or "").strip()])
        base_text = {column: semantic_norm(item.get("raw_text")) for column, item in base_nonempty.items()}
        week_text = {column: semantic_norm(item.get("raw_text")) for column, item in week_nonempty.items()}
        base_raw = {column: norm(item.get("raw_text")) for column, item in base_nonempty.items()}
        week_raw = {column: norm(item.get("raw_text")) for column, item in week_nonempty.items()}
        base_meta = {column: template_meta_by_coord.get(str(item.get("source_cell")), {}) for column, item in base_nonempty.items()}
        week_meta = {column: weekly_meta_by_coord.get(str(item.get("source_cell")), {}) for column, item in week_nonempty.items()}
        if not week_nonempty:
            classification, change_kind = "CANCELLED", "cancelled"
        elif base_text != week_text:
            classification, change_kind = "REPLACED", "replaced"
        elif base_raw != week_raw or base_meta != week_meta:
            classification, change_kind = "METADATA_ONLY", "metadata"
        else:
            classification, change_kind = "UNCHANGED", "unchanged"
        weekly_block = weekly_by_key.get(key)
        if weekly_block and classification == "REPLACED" and assignment_signature(canonical) and assignment_signature(canonical) != assignment_signature(weekly_block):
            if {x[1] for x in assignment_signature(canonical)} != {x[1] for x in assignment_signature(weekly_block)}:
                classification = "SEMANTIC_AUDIENCE_CHANGE"
        patch: dict[str, Any] = {"change_classification": classification, "weekly_source_cells": sorted(week_items), "weekly_raw_text": {coord: item.get("raw_text") for coord, item in week_items.items()}}
        if classification in {"REPLACED", "SEMANTIC_AUDIENCE_CHANGE"} and weekly_block:
            patch["assignments"] = weekly_block.get("assignments") or []
            patch["source_provenance"] = weekly_block.get("derived_from") or {}
        elif classification == "METADATA_ONLY":
            patch["source_provenance"] = {"sheet": weekly_input["sheet_title"], "source_cells": [{"coordinate": coord, "raw_text": item.get("raw_text")} for coord, item in week_items.items()], "metadata_only": True}
        diffs.append({"key": key, "canonical_block_key": canonical.get("block_key"), "classification": classification, "change_kind": change_kind, "template_cells": base_coords, "weekly_cells": sorted(week_items), "patch": patch, "weekly_block": weekly_block})

    for key, weekly_block in sorted(weekly_by_key.items()):
        if key in canonical_by_key:
            continue
        cells = weekly_parsed.get((int(key[0]), key[1], key[2], key[3]), [])
        patch = {"change_classification": "ADDED", "assignments": weekly_block.get("assignments") or [], "weekday": key[0], "slot": {"start": key[1], "end": key[2]}, "grade_scope": key[3], "source_provenance": weekly_block.get("derived_from") or {}, "weekly_source_cells": [item["source_cell"] for item in cells]}
        diffs.append({"key": key, "canonical_block_key": None, "classification": "ADDED", "change_kind": "added", "template_cells": [], "weekly_cells": [item["source_cell"] for item in cells], "patch": patch, "weekly_block": weekly_block})

    # Detect a provable move only when the raw source text is unique on both sides.
    cancelled = [item for item in diffs if item["classification"] == "CANCELLED"]
    added = [item for item in diffs if item["classification"] == "ADDED"]
    for old in cancelled:
        old_text = {semantic_norm(template_raw.get(coord, {}).get("raw_text")) for coord in old["template_cells"]} - {""}
        matches = [new for new in added if old_text and old_text == ({semantic_norm(weekly_raw.get(coord, {}).get("raw_text")) for coord in new["weekly_cells"]} - {""})]
        if len(matches) == 1:
            new = matches[0]
            old["classification"], old["change_kind"] = "MOVED", "moved"
            old["patch"].update({"weekday": new["key"][0], "slot": {"start": new["key"][1], "end": new["key"][2]}, "grade_scope": new["key"][3], "assignments": new["patch"].get("assignments") or [], "source_provenance": new["patch"].get("source_provenance") or {}, "moved_from": old["key"], "moved_to": new["key"]})
            diffs.remove(new)

    source_payload = {"spreadsheet_id": weekly_input["spreadsheet_id"], "spreadsheet_title": weekly_input["spreadsheet_title"], "sheet_id": weekly_input["sheet_id"], "sheet_title": weekly_input["sheet_title"], "range": weekly_input["range"], "week_start": weekly_input["week_start"], "week_end": weekly_input["week_end"], "values": weekly_input["values"], "structured_cells": weekly_input.get("structured_cells") or [], "merges": weekly_input.get("merges") or [], "merges_note": weekly_input.get("merges_note")}
    encoded = json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    source_fingerprint = hashlib.sha256(encoded).hexdigest()
    source_snapshot = {"id": weekly_corpus["snapshot"]["id"], "fingerprint": source_fingerprint, "source_fingerprint": source_fingerprint, "sheet_id": weekly_input["sheet_id"], "week_start": weekly_input["week_start"], "week_end": weekly_input["week_end"]}
    overlay = [{key: item[key] for key in ("canonical_block_key", "key", "classification", "change_kind", "template_cells", "weekly_cells", "patch")} for item in diffs if item["classification"] != "UNCHANGED"]
    effective_artifact = copy.deepcopy(artifact)
    effective_blocks = effective_artifact["blocks"]
    for item in overlay:
        base = effective_blocks.get(item["canonical_block_key"]) if item["canonical_block_key"] else None
        if base is None:
            wb = next((x["weekly_block"] for x in diffs if x.get("key") == tuple(item["key"])), None)
            if wb:
                effective_blocks[f"weekly-only|{item['key']}"] = copy.deepcopy(wb)
            continue
        patch = item["patch"]
        if item["change_kind"] == "cancelled":
            base["assignments"] = []
        elif "assignments" in patch:
            base["assignments"] = copy.deepcopy(patch["assignments"])
        if item["change_kind"] == "moved":
            base["weekday"], base["slot"], base["grade_scope"] = patch["weekday"], patch["slot"], patch["grade_scope"]
    student = project(effective_artifact, db)
    teacher = teacher_projection(effective_artifact, db)
    counts = Counter(item["classification"] for item in diffs)
    changed_items = [x for x in diffs if x["classification"] != "UNCHANGED"]
    selected: list[dict[str, Any]] = []
    for wanted in ("METADATA_ONLY", "CANCELLED", "REPLACED", "MOVED", "ADDED", "SEMANTIC_AUDIENCE_CHANGE"):
        selected.extend([x for x in changed_items if x["classification"] == wanted][:2])
    for wanted_grade in ("7", "9", "10", "11"):
        selected.extend([x for x in changed_items if x["key"][3] == wanted_grade][:2])
    selected_keys = set()
    selected = [x for x in selected if not (str(x["key"]) in selected_keys or selected_keys.add(str(x["key"])))][:10]
    if len(selected) < 10:
        selected.extend([x for x in changed_items if str(x["key"]) not in selected_keys][:10-len(selected)])
    spot_checks: list[dict[str, Any]] = []
    for item in selected:
        spot_checks.append({"classification": item["classification"], "key": item["key"], "template": [{"cell": coord, "raw": template_raw.get(coord, {}).get("raw_text")} for coord in item["template_cells"]], "weekly": [{"cell": coord, "raw": weekly_raw.get(coord, {}).get("raw_text")} for coord in item["weekly_cells"]], "overlay": item["patch"], "effective_assignment_count": len((item.get("weekly_block") or {}).get("assignments") or []) if item["classification"] != "CANCELLED" else 0})
    report = {"source_snapshot": source_snapshot, "source_payload": source_payload, "diff_counts": dict(counts), "overlay": overlay, "overlay_patch_count": len(overlay), "effective_block_count": len(effective_blocks), "baseline_artifact_version": artifact.get("version_id"), "student_projection": {"states": student["states"], "duplicate_student_slots": student["duplicate_student_slots"], "multiple_activities": student["multiple_activities"], "unknown_group_refs": student["unknown_group_refs"], "unresolved_count": len(student["unresolved"]), "unresolved_by_reason": dict(Counter(str(item.get("reason") or "") for item in student["unresolved"])), "active_students": len(db.get("students") or [])}, "teacher_projection": {"resolved_assignments": teacher["resolved_assignments"], "unresolved_assignments": teacher["unresolved_assignments"], "orphan_expected_teacher": teacher["orphan_expected_teacher"], "simultaneous_conflicts": teacher["simultaneous_conflicts"]}, "spot_checks": spot_checks, "semantic_resolution": "V2 resolver only for changed blocks; unchanged blocks reuse canonical v2", "weekly_candidate_unresolved_blocks": len(weekly_candidate.get("unresolved") or [])}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"source_snapshot": source_snapshot, "diff_counts": dict(counts), "overlay_patch_count": len(overlay), "effective_block_count": len(effective_blocks), "student": report["student_projection"], "teacher": {"resolved": teacher["resolved_assignments"], "unresolved": len(teacher["unresolved_assignments"]), "orphan": len(teacher["orphan_expected_teacher"]), "conflicts": len(teacher["simultaneous_conflicts"])}, "weekly_candidate_unresolved_blocks": report["weekly_candidate_unresolved_blocks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
