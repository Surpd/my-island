"""Shared, deterministic ingestion primitives for canonical weekly schedules."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any, Mapping, Sequence

from backend.services.schedule_parser_v2 import classify_simple_activity, normalize_no_lesson
from backend.services.teacher_directory import resolve_teacher_id


CLASS_RE = re.compile(r"^\s*\d{1,2}(?:-[А-ЯЁA-Z])?(?:-\d+)?\s*$", re.IGNORECASE)
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")
DAYS = {"пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2, "чт": 3, "четверг": 3, "пт": 4, "пятница": 4}
MONTHS = {
    "января": 1, "январь": 1, "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3, "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5, "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7, "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9, "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11, "декабря": 12, "декабрь": 12,
}
ROOM_LINE_RE = re.compile(r"^\s*(?:каб(?:инет)?\.?\s*[^\n]*|зал(?:\s*[^\n]*)?|\d{1,3})\s*$", re.IGNORECASE)
ROOM_INLINE_RE = re.compile(r"\s+(?:каб(?:инет)?\.?\s*[^\n]*|зал(?:\s*[^\n]*)?)(?=\s*$)", re.IGNORECASE)
WEEK_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:([а-яё]+)\s*)?[-–—]\s*(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?",
    re.IGNORECASE,
)


class WeeklyIngestionError(ValueError):
    """Controlled source-selection/layout failure suitable for admin output."""


@dataclass(frozen=True)
class WeeklyTabCandidate:
    title: str
    sheet_id: str
    week_start: str
    week_end: str

    def as_dict(self) -> dict[str, str]:
        return {"title": self.title, "sheet_id": self.sheet_id, "week_start": self.week_start, "week_end": self.week_end}


def norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def semantic_norm(value: Any) -> str:
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


def parse_week_title(title: str, *, reference_date: date) -> tuple[date, date] | None:
    if "шаблон" in title.casefold():
        return None
    match = WEEK_RE.search(title)
    if not match:
        return None
    start_day, start_month_name, end_day, end_month_name, explicit_year = match.groups()
    end_month = MONTHS.get(end_month_name.casefold())
    start_month = MONTHS.get((start_month_name or end_month_name).casefold())
    if not start_month or not end_month:
        return None
    year = int(explicit_year or reference_date.year)
    start_year = year - 1 if start_month > end_month else year
    try:
        start = date(start_year, start_month, int(start_day))
        end = date(year, end_month, int(end_day))
    except ValueError:
        return None
    if end < start or (end - start).days > 14:
        return None
    return start, end


def discover_weekly_tab(
    sheets: Sequence[Mapping[str, Any]], *, target_date: date | None = None, explicit_week_start: str | None = None,
) -> tuple[WeeklyTabCandidate, list[dict[str, str]]]:
    target = date.fromisoformat(explicit_week_start) if explicit_week_start else (target_date or date.today())
    candidates: list[WeeklyTabCandidate] = []
    malformed: list[str] = []
    for sheet in sheets:
        props = sheet.get("properties") if isinstance(sheet.get("properties"), Mapping) else sheet
        title = str(props.get("title") or "")
        if "шаблон" in title.casefold():
            continue
        parsed = parse_week_title(title, reference_date=target)
        if parsed is None:
            if re.search(r"\d{1,2}\s*[-–—]\s*\d{1,2}", title):
                malformed.append(title)
            continue
        start, end = parsed
        candidates.append(WeeklyTabCandidate(title, str(props.get("sheetId") or props.get("sheet_id") or ""), start.isoformat(), end.isoformat()))
    diagnostics = [item.as_dict() for item in candidates]
    matches = [item for item in candidates if item.week_start == explicit_week_start] if explicit_week_start else [item for item in candidates if date.fromisoformat(item.week_start) <= target <= date.fromisoformat(item.week_end)]
    if len(matches) != 1:
        detail = f"candidates={diagnostics}"
        if malformed:
            detail += f"; malformed={malformed}"
        if not matches:
            raise WeeklyIngestionError(f"Weekly tab was not found for {explicit_week_start or target.isoformat()}; {detail}")
        raise WeeklyIngestionError(f"Weekly tab selection is ambiguous for {explicit_week_start or target.isoformat()}; matches={[item.as_dict() for item in matches]}; {detail}")
    return matches[0], diagnostics


def _header_and_owners(values: Sequence[Sequence[Any]]) -> tuple[int, list[str]]:
    header_index = next((r for r, row in enumerate(values[:12]) if sum(bool(CLASS_RE.fullmatch(cell_text(v).strip())) for v in row[1:]) >= 2), None)
    if header_index is None:
        raise WeeklyIngestionError("Schedule layout drift: class-header row was not found in the first 12 rows")
    width = max((len(row) for row in values), default=0)
    header = list(values[header_index]) + [""] * max(0, width - len(values[header_index]))
    owners, owner = [""] * width, ""
    for column in range(1, width):
        value = cell_text(header[column]).strip()
        if CLASS_RE.fullmatch(value):
            owner = value
        elif value:
            owner = ""
        owners[column] = owner
    if len({item for item in owners if item}) < 2:
        raise WeeklyIngestionError("Schedule layout drift: fewer than two class audience columns were found")
    return header_index, owners


def layout_profile(values: Sequence[Sequence[Any]], merges: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    header_index, owners = _header_and_owners(values)
    used_columns = [index for index, owner in enumerate(owners) if owner]
    if not used_columns or max(used_columns) >= 64 or len(values) > 500:
        raise WeeklyIngestionError(f"Schedule layout drift: unsupported bounds rows={len(values)}, columns={max(used_columns, default=0) + 1}")
    return {
        "class_header_row": header_index,
        "used_columns": used_columns,
        "audiences": [owners[index] for index in used_columns],
        "row_count": len(values),
        "column_count": max((len(row) for row in values), default=0),
        "merge_count": len(merges),
        "signature": {"header": [cell_text(item).strip() for item in values[header_index]], "used_columns": used_columns},
    }


def _merge_index(merges: Sequence[Mapping[str, Any]]) -> dict[tuple[int, int], Mapping[str, Any]]:
    result: dict[tuple[int, int], Mapping[str, Any]] = {}
    for merge in merges:
        start_row, end_row = int(merge.get("startRowIndex", 0)), int(merge.get("endRowIndex", 0))
        start_col, end_col = int(merge.get("startColumnIndex", 0)), int(merge.get("endColumnIndex", 0))
        for row in range(start_row, end_row):
            for column in range(start_col, end_col):
                result[(row, column)] = merge
    return result


def parse_structure(
    values: list[list[Any]], *, sheet_id: str, title: str, week_start: str,
    merges: Sequence[Mapping[str, Any]] = (),
) -> dict[tuple[int, str, str, str], list[dict[str, Any]]]:
    profile = layout_profile(values, merges)
    header_index, owners = _header_and_owners(values)
    width = len(owners)
    merge_by_cell = _merge_index(merges)
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
                current_owner = owners[column]
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
        for column in range(1, width):
            merge = merge_by_cell.get((row_index, column))
            if merge and (row_index != int(merge.get("startRowIndex", 0)) or column != int(merge.get("startColumnIndex", 0))):
                continue
            value = row[column] if column < len(row) else ""
            raw_text = cell_text(value).strip()
            if not raw_text:
                continue
            audience = owners[column]
            if not grade(audience):
                continue
            merged_audiences: list[str] = []
            merge_data = None
            if merge:
                merge_data = dict(merge)
                merged_audiences = list(dict.fromkeys(
                    owners[index] for index in range(int(merge.get("startColumnIndex", column)), min(int(merge.get("endColumnIndex", column + 1)), width))
                    if owners[index]
                ))
            grade_scope = ",".join(sorted({grade(item) for item in (merged_audiences or [audience]) if grade(item)}, key=int))
            key = (current_weekday, start_time, end_time, grade_scope)
            result.setdefault(key, []).append({
                "source_cell": a1(row_index, column), "source_column": column, "source_row": row_index,
                "raw_text": raw_text, "audience": audience, "merged_audiences": merged_audiences,
                "merge_data": merge_data, "source_day_label": day_labels[column], "sheet_id": sheet_id,
                "tab_title": title, "lesson_date": day_date(week_start, current_weekday), "weekday": current_weekday,
                "start_time": start_time, "end_time": end_time, "layout_signature": profile["signature"],
            })
    if not result:
        raise WeeklyIngestionError("Schedule layout drift: no logical lesson rows were parsed")
    return result


def meta_map(items: Sequence[Mapping[str, Any]], *, weekly: bool = False) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        coordinate = str(item.get("coordinate") or "")
        fmt = item.get("effectiveFormat") or {} if weekly else {}
        result[coordinate] = {
            "background_color": item.get("background_color") if not weekly else (fmt.get("backgroundColorStyle", {}).get("rgbColor") or fmt.get("backgroundColor")),
            "text_format": item.get("text_format") if not weekly else fmt.get("textFormat"), "note": item.get("note"),
        }
    return result


def raw_by_coord(parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {str(item["source_cell"]): item for cells in parsed.values() for item in cells}


def by_source_column(items: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    return {int(item["source_column"]): item for item in items if item.get("source_column") is not None}


def _subject_hint(raw: str) -> str:
    first = str(raw).splitlines()[0].strip()
    if classify_simple_activity(raw):
        return first
    match = re.split(r"\s+(?=\d|[A-ZА-ЯЁ])", first, maxsplit=1)
    return match[0].strip() if match else first


def parse_weekly_lessons(parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]], teachers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Adapt merge-aware structural rows to the existing V2 semantic input."""
    lessons = []
    for cells in parsed.values():
        for cell in cells:
            raw = str(cell["raw_text"])
            teacher_id, _teacher_issue = resolve_teacher_id(raw, "", teachers)
            activity_type = "extracurricular" if raw.lstrip().startswith("⚪") else "nonlesson" if normalize_no_lesson(raw) else "simple_activity" if classify_simple_activity(raw) else "lesson"
            room_match = re.search(r"(?:каб(?:\.|инет)?\s*[^\n]+|зал[^\n]*|онлайн[^\n]*)", raw, re.IGNORECASE)
            parsed_cell = {
                "subject": _subject_hint(raw), "teacher_hint": raw,
                "room": room_match.group(0).strip() if room_match else "", "activity_type": activity_type,
                "modifiers": {"exam_track": "ОГЭ" if re.search(r"\bогэ\b", raw, re.IGNORECASE) else "", "subject_subgroup": ""},
                "parse_status": "validated" if teacher_id else "ambiguous", "resolved_identity_ids": [teacher_id] if teacher_id else [],
            }
            lessons.append({**cell, **parsed_cell, "record_key": f"{cell['sheet_id']}:{cell['source_cell']}", "raw_payload": {
                "raw_text": raw, "source_column": cell["source_column"], "source_row": cell["source_row"],
                "merged_audiences": list(cell.get("merged_audiences") or []), "merge_data": cell.get("merge_data"),
            }})
    return lessons


def assignment_signature(block: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    result = set()
    for item in block.get("assignments") or []:
        audience = item.get("audience") if isinstance(item.get("audience"), Mapping) else {}
        groups = tuple(sorted(str(x) for x in audience.get("canonical_group_ids") or item.get("canonical_group_ids") or []))
        result.add((str(item.get("activity") or ""), groups, tuple(sorted(str(x) for x in item.get("teacher_ids") or []))))
    return result


def comparison_key(block: Mapping[str, Any]) -> tuple[int, str, str, str]:
    slot = block.get("slot") if isinstance(block.get("slot"), Mapping) else {}
    def padded(value: Any) -> str:
        text = str(value or "")[:5]
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text
    return int(block.get("weekday") or 0), padded(slot.get("start") or block.get("start_time")), padded(slot.get("end") or block.get("end_time")), str(block.get("grade_scope") or "")


def _source_map(block: Mapping[str, Any], parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]], key: tuple[int, str, str, str]) -> list[str]:
    provenance = block.get("derived_from") or block.get("source_provenance") or {}
    coords = []
    for item in provenance.get("source_cells") or []:
        coordinate = item.get("coordinate") if isinstance(item, Mapping) else item
        if coordinate:
            coords.append(str(coordinate))
    return coords or [str(item["source_cell"]) for item in parsed.get(key, [])]


def build_weekly_diff(
    canonical: Mapping[str, Any], template_parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]],
    weekly_parsed: Mapping[tuple[int, str, str, str], list[dict[str, Any]]], weekly_artifact: Mapping[str, Any],
    template_meta: Mapping[str, Any] | None = None, weekly_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_by_key = {comparison_key(block): block for block in (canonical.get("blocks") or {}).values() if block.get("slot") or block.get("start_time")}
    weekly_by_key = {comparison_key(block): block for block in (weekly_artifact.get("blocks") or {}).values() if block.get("slot") or block.get("start_time")}
    template_raw, weekly_raw = raw_by_coord(template_parsed), raw_by_coord(weekly_parsed)
    template_meta, weekly_meta = template_meta or {}, weekly_meta or {}
    diffs: list[dict[str, Any]] = []
    for key, base in canonical_by_key.items():
        source_cells = _source_map(base, template_parsed, key)
        base_items = [template_raw.get(coord, {"source_cell": coord, "raw_text": ""}) for coord in source_cells]
        week_items = list(weekly_parsed.get(key, []))
        base_cells, week_cells = by_source_column(base_items), by_source_column(week_items)
        def cell_semantics(item: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
            audiences = item.get("merged_audiences") or [item.get("audience")]
            return semantic_norm(item.get("raw_text")), tuple(sorted(norm(value) for value in audiences if value))
        base_sem = {column: cell_semantics(item) for column, item in base_cells.items()}
        week_sem = {column: cell_semantics(item) for column, item in week_cells.items()}
        base_raw = {column: norm(item.get("raw_text")) for column, item in base_cells.items()}
        week_raw_values = {column: norm(item.get("raw_text")) for column, item in week_cells.items()}
        base_format = {column: template_meta.get(str(item.get("source_cell")), {}) for column, item in base_cells.items()}
        week_format = {column: weekly_meta.get(str(item.get("source_cell")), {}) for column, item in week_cells.items()}
        if not week_cells:
            classification, kind = "CANCELLED", "cancelled"
        elif base_sem != week_sem:
            classification, kind = "REPLACED", "replaced"
        elif base_raw != week_raw_values or base_format != week_format:
            classification, kind = "METADATA_ONLY", "metadata"
        else:
            classification, kind = "UNCHANGED", "unchanged"
        weekly_block = weekly_by_key.get(key)
        if weekly_block and classification == "REPLACED" and assignment_signature(base) != assignment_signature(weekly_block):
            if {x[1] for x in assignment_signature(base)} != {x[1] for x in assignment_signature(weekly_block)}:
                classification = "SEMANTIC_AUDIENCE_CHANGE"
        weekly_coords = [str(item["source_cell"]) for item in week_items]
        patch: dict[str, Any] = {"block_key": base.get("block_key"), "change_kind": kind, "change_classification": classification, "weekly_source_cells": sorted(weekly_coords), "weekly_raw_text": {coord: weekly_raw[coord].get("raw_text") for coord in weekly_coords}}
        if classification in {"REPLACED", "SEMANTIC_AUDIENCE_CHANGE"} and weekly_block:
            patch["assignments"] = weekly_block.get("assignments") or []
            patch["source_provenance"] = weekly_block.get("derived_from") or {}
        elif classification == "REPLACED":
            patch["source_issue"] = "changed source has no deterministic V2 assignment replacement"
        elif classification == "METADATA_ONLY":
            patch["source_provenance"] = {"source_cells": [{"coordinate": coord, "raw_text": weekly_raw[coord].get("raw_text")} for coord in weekly_coords], "metadata_only": True}
        diffs.append({"key": key, "canonical_block_key": base.get("block_key"), "classification": classification, "change_kind": kind, "template_cells": source_cells, "weekly_cells": weekly_coords, "patch": patch, "weekly_block": weekly_block})
    for key, block in weekly_by_key.items():
        if key in canonical_by_key:
            continue
        cells = weekly_parsed.get(key, [])
        coords = [str(item["source_cell"]) for item in cells]
        patch = {"block_key": f"weekly-only|{key}", "change_kind": "weekly_only", "change_classification": "ADDED", "assignments": block.get("assignments") or [], "weekday": key[0], "slot": {"start": key[1], "end": key[2]}, "grade_scope": key[3], "source_provenance": block.get("derived_from") or {}, "weekly_source_cells": coords}
        diffs.append({"key": key, "canonical_block_key": None, "classification": "ADDED", "change_kind": "weekly_only", "template_cells": [], "weekly_cells": coords, "patch": patch, "weekly_block": block})
    cancelled = [item for item in diffs if item["classification"] == "CANCELLED"]
    added = [item for item in diffs if item["classification"] == "ADDED"]
    for old in cancelled:
        old_text = {semantic_norm(template_raw.get(coord, {}).get("raw_text")) for coord in old["template_cells"]} - {""}
        matches = [new for new in added if old_text and old_text == ({semantic_norm(weekly_raw.get(coord, {}).get("raw_text")) for coord in new["weekly_cells"]} - {""})]
        if len(matches) == 1:
            new = matches[0]
            old["classification"], old["change_kind"] = "MOVED", "moved"
            old["patch"].update({"change_classification": "MOVED", "change_kind": "moved", "weekday": new["key"][0], "slot": {"start": new["key"][1], "end": new["key"][2]}, "grade_scope": new["key"][3], "assignments": new["patch"].get("assignments") or [], "source_provenance": new["patch"].get("source_provenance") or {}, "moved_from": old["key"], "moved_to": new["key"]})
            diffs.remove(new)
            added.remove(new)
    counts = Counter(item["classification"] for item in diffs)
    return {"items": diffs, "counts": dict(counts), "patches": [item["patch"] for item in diffs if item["classification"] != "UNCHANGED"]}
