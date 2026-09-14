"""Phase 1 building blocks for the row-oriented schedule parser.

This module is intentionally read-only and is not wired into the production
schedule pipeline yet.  It groups the already parsed source cells and defines
the small intermediate vocabulary that a future semantic resolver can use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping, Sequence


class ScheduleRowMode(str, Enum):
    CLASS = "CLASS"
    MATH = "MATH"
    ENGLISH = "ENGLISH"
    MATH_WITH_ELECTIVES = "MATH_WITH_ELECTIVES"
    ENGLISH_WITH_ELECTIVES = "ENGLISH_WITH_ELECTIVES"
    ELECTIVES = "ELECTIVES"
    GLOBAL = "GLOBAL"
    SPECIAL = "SPECIAL"
    SDEP = "SDEP"
    UNKNOWN = "UNKNOWN"


NO_LESSON = "NO_LESSON"


@dataclass(frozen=True)
class ScheduleSourceCell:
    source_cell: str
    source_column: int | None
    raw_text: str
    audience: str
    source_row: int | None = None
    source_color: str = ""
    merge_data: Mapping[str, Any] | None = None
    merged_audiences: tuple[str, ...] = ()
    parsed: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleSourceRow:
    row_key: str
    date: str | None
    weekday: int | None
    start_time: str
    end_time: str
    grade_scope: str
    cells: tuple[ScheduleSourceCell, ...]


@dataclass
class ScheduleRowInterpretation:
    row_key: str
    mode: ScheduleRowMode
    primary_assignments: list[Any] = field(default_factory=list)
    secondary_assignments: list[Any] = field(default_factory=list)
    default_assignment: Any = None
    unresolved_blocks: list[Any] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    final_assignments: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class ScheduleAssignment:
    """A semantic assignment over a concrete set of students."""

    activity: str
    student_ids: frozenset[str] = frozenset()
    source_cells: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    kind: str = "primary"


@dataclass(frozen=True)
class NinthGradeContext:
    """Read-only canonical context consumed by the Grade 9 shadow resolver."""

    all_students: frozenset[str]
    base_classes: tuple[Mapping[str, Any], ...] = ()
    math_groups: tuple[Mapping[str, Any], ...] = ()
    english_groups: tuple[Mapping[str, Any], ...] = ()
    exam_groups: tuple[Mapping[str, Any], ...] = ()
    memberships: Mapping[str, frozenset[str]] = field(default_factory=dict)
    lane_columns: Mapping[str, Mapping[int, str]] = field(default_factory=dict)
    lane_conflicts: Mapping[str, Mapping[int, tuple[str, ...]]] = field(default_factory=dict)

    def members(self, group: Mapping[str, Any]) -> frozenset[str]:
        return frozenset(self.memberships.get(str(group.get("id")), frozenset())) & self.all_students


_GRADE_RE = re.compile(r"^\s*(\d{1,2})")


def _grade(value: Any) -> str:
    match = _GRADE_RE.match(str(value or ""))
    return match.group(1) if match else ""


def _cell_grades(lesson: Mapping[str, Any]) -> tuple[str, ...]:
    values = [lesson.get("audience")]
    values.extend((lesson.get("merged_audiences") or []))
    return tuple(sorted({grade for grade in (_grade(value) for value in values) if grade}, key=int))


def _row_key(lesson: Mapping[str, Any], grade_scope: str) -> str:
    sheet = str(lesson.get("sheet_id") or "")
    tab = str(lesson.get("tab_title") or "")
    day = str(lesson.get("lesson_date") or f"weekday-{lesson.get('weekday')}")
    return "|".join((sheet, tab, day, str(lesson.get("start_time") or ""), str(lesson.get("end_time") or ""), grade_scope))


def _source_cell(lesson: Mapping[str, Any]) -> ScheduleSourceCell:
    raw_payload = lesson.get("raw_payload") if isinstance(lesson.get("raw_payload"), Mapping) else {}
    parsed = {
        key: lesson.get(key)
        for key in (
            "subject", "teacher_hint", "room", "activity_type", "modifiers", "parse_status",
            "resolution_status", "resolved_identity_ids", "resolved_group_ids",
        )
        if key in lesson
    }
    provenance = {
        key: lesson.get(key)
        for key in ("sheet_id", "tab_title", "source_cell", "source_row", "source_column", "source_day_label", "record_key")
        if key in lesson
    }
    if raw_payload:
        provenance["raw_payload"] = dict(raw_payload)
    return ScheduleSourceCell(
        source_cell=str(lesson.get("source_cell") or ""),
        source_column=int(lesson["source_column"]) if lesson.get("source_column") is not None else None,
        raw_text=str(lesson.get("raw_text") or ""),
        audience=str(lesson.get("audience") or ""),
        source_row=int(lesson["source_row"]) if lesson.get("source_row") is not None else None,
        source_color=str(lesson.get("source_color") or ""),
        merge_data=lesson.get("merge_data"),
        merged_audiences=tuple(str(value) for value in (lesson.get("merged_audiences") or [])),
        parsed=parsed,
        provenance=provenance,
    )


def group_schedule_rows(parsed_cells: Sequence[Mapping[str, Any]]) -> list[ScheduleSourceRow]:
    """Group parser output by one logical date/weekday, time slot and grade.

    The function does not interpret semantics and does not mutate its input.
    A source cell is emitted once for its owning grade row; merged audiences
    are retained as metadata instead of duplicating the cell.
    """
    grouped: dict[tuple[Any, ...], list[ScheduleSourceCell]] = {}
    metadata: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for lesson in parsed_cells:
        grades = _cell_grades(lesson)
        grade_scope = ",".join(grades) if grades else ""
        key = (
            str(lesson.get("sheet_id") or ""),
            str(lesson.get("tab_title") or ""),
            lesson.get("lesson_date"),
            lesson.get("weekday"),
            str(lesson.get("start_time") or ""),
            str(lesson.get("end_time") or ""),
            grade_scope,
        )
        grouped.setdefault(key, []).append(_source_cell(lesson))
        metadata[key] = lesson
    result: list[ScheduleSourceRow] = []
    for key, cells in grouped.items():
        first = metadata[key]
        ordered = tuple(sorted(cells, key=lambda cell: (cell.source_column is None, cell.source_column or 0, cell.source_cell)))
        result.append(ScheduleSourceRow(
            row_key=_row_key(first, str(key[-1])),
            date=str(first.get("lesson_date")) if first.get("lesson_date") else None,
            weekday=int(first["weekday"]) if first.get("weekday") is not None else None,
            start_time=str(first.get("start_time") or ""),
            end_time=str(first.get("end_time") or ""),
            grade_scope=str(key[-1]),
            cells=ordered,
        ))
    return sorted(result, key=lambda row: (row.date or "", row.weekday if row.weekday is not None else 99, row.start_time, row.grade_scope, row.row_key))


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def _lexical_text(cell: ScheduleSourceCell) -> str:
    return _norm(" ".join((cell.raw_text, str(cell.parsed.get("subject") or ""), str(cell.parsed.get("modifiers") or ""))))


def normalize_no_lesson(value: Any) -> str | None:
    if str(value or "").strip() in {"🥨", "🍽️"}:
        return NO_LESSON
    text = _norm(value)
    if not text:
        return None
    if (text.startswith("обед") or text in {"перерыв", "перемена", "свободны", "свободен", "нет урока", "нет уроков", "окно"}):
        return NO_LESSON
    return None


def classify_simple_activity(value: Any) -> str | None:
    text = _norm(value)
    for pattern, canonical in SIMPLE_ACTIVITY_MARKERS:
        if pattern.search(text):
            return canonical
    return None


def detect_sdep_day(rows: Sequence[ScheduleSourceRow]) -> bool:
    """Detect a whole-day SDEP only from explicit 10–11 day markers."""
    if not rows or any(row.grade_scope not in {"10", "11"} for row in rows):
        return False
    marker = re.compile(r"\bsdep\b", re.IGNORECASE)
    grades = {row.grade_scope for row in rows}
    return all(any(marker.search(str(cell.provenance.get("source_day_label") or ""))
                   for row in rows if row.grade_scope == grade for cell in row.cells) for grade in grades)


SIMPLE_ACTIVITY_MARKERS = (
    (re.compile(r"творчеств"), "Творчество"),
    (re.compile(r"курс\s+по\s+выбор|электив"), "Курс по выбору"),
    (re.compile(r"цифров\w*\s+трек|программирован|машинн\w*\s+график|(?:^|\s)шум(?:\s|\.|$)|медиамастерск"), "Цифровой трек"),
    (re.compile(r"тренинг"), "Тренинг"),
)
