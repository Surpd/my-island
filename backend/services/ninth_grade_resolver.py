"""Shadow-only semantic resolver for Grade 9 Schedule Parser V2."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any, Iterable, Mapping
import re

from backend.database import Database
from backend.services.schedule_parser_v2 import (
    NO_LESSON, NinthGradeContext, ScheduleAssignment, ScheduleRowInterpretation,
    ScheduleRowMode, ScheduleSourceCell, ScheduleSourceRow, classify_simple_activity,
    normalize_no_lesson,
)

_MATH_ANCHOR = re.compile(r"\b(?:мат(?:ематика|ем)?|math)\s*(?:группа\s*)?([a-zа-я])\b", re.IGNORECASE)
_ENGLISH_ANCHOR = re.compile(r"\b(?:англ(?:ийский)?|english)\s*(?:группа\s*)?(\d{1,2})\b", re.IGNORECASE)
_OGE = re.compile(r"\b(?:огэ|oge)\b", re.IGNORECASE)
_CLASS = re.compile(r"^\s*\d{1,2}(?:[-а-яёa-z])?(?:-\d+)?\s*$", re.IGNORECASE)


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def _grade(value: Any) -> str:
    match = re.match(r"\s*(\d{1,2})", str(value or ""))
    return match.group(1) if match else ""


def load_ninth_grade_context(database: Database, *, day: str | None = None) -> NinthGradeContext:
    """Load canonical Grade 9 groups/memberships without mutating the DB."""
    with database.connection() as connection:
        groups = [dict(row) for row in database.execute(connection, """SELECT id,name,display_name,group_type,subject,
            base_class_name,subject_subgroup,exam_track FROM groups
            WHERE canonical IS TRUE""").fetchall()]
        students = [dict(row) for row in database.execute(connection, """SELECT id,class_name FROM identities
            WHERE kind='student' AND status='active'
              AND SUBSTR(COALESCE(class_name, ''), 1, 1) = '9'""").fetchall()]
        membership_rows = [dict(row) for row in database.execute(connection, """SELECT m.identity_id,m.group_id,m.valid_from,m.valid_until
            FROM memberships m JOIN identities i ON i.id=m.identity_id
            WHERE m.active IS TRUE AND i.kind='student' AND i.status='active'
              AND SUBSTR(COALESCE(i.class_name, ''), 1, 1) = '9'""").fetchall()]
    if day:
        membership_rows = [row for row in membership_rows
                           if (not row.get("valid_from") or str(row["valid_from"]) <= day)
                           and (not row.get("valid_until") or str(row["valid_until"]) >= day)]
    memberships: dict[str, set[str]] = defaultdict(set)
    for row in membership_rows:
        memberships[str(row["group_id"])].add(str(row["identity_id"]))
    active_students = {str(row["id"]) for row in students}
    def is_grade9(group: Mapping[str, Any]) -> bool:
        group_id = str(group.get("id"))
        declared_grade = _grade(group.get("base_class_name") or group.get("name") or group.get("display_name"))
        return declared_grade == "9" or bool(memberships.get(group_id))
    grade9 = [group for group in groups if is_grade9(group)]
    base = [group for group in grade9 if _norm(group.get("group_type")) == "class"]
    base_ids = {str(group["id"]) for group in base}
    all_students = {student_id for group_id, student_ids in memberships.items() if group_id in base_ids for student_id in student_ids}
    # Keep a conservative fallback for installations where base memberships
    # have not been imported yet; this does not invent membership relations.
    all_students.update(str(row["id"]) for row in students if str(row["id"]) in active_students and _grade(row.get("class_name")) == "9")
    math = [group for group in grade9 if _norm(group.get("subject")) in {"математика", "матем"} and not _norm(group.get("exam_track"))]
    # Canonical instructional semantics take precedence over a lexical exam
    # marker: a subject subgroup is part of the English partition even when
    # its source label also contains an exam marker.
    english = [group for group in grade9 if _norm(group.get("subject")) in {"английский", "английский язык", "англ"}
               and _norm(group.get("subject_subgroup"))]
    exam = [group for group in grade9 if _norm(group.get("exam_track")) in {"огэ", "oge"}]
    return NinthGradeContext(frozenset(all_students), tuple(base), tuple(math), tuple(english), tuple(exam),
                             {key: frozenset(value) for key, value in memberships.items()})


def learn_partition_lanes(context: NinthGradeContext, rows: Iterable[ScheduleSourceRow]) -> NinthGradeContext:
    """Learn source-column lanes only from explicit, canonical subgroup anchors."""
    candidates: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    families = {"math": context.math_groups, "english": context.english_groups}
    for row in rows:
        if row.grade_scope != "9":
            continue
        for cell in row.cells:
            anchor = _anchor(cell)
            if not anchor or cell.source_column is None:
                continue
            family, subgroup = anchor
            matches = _match_group(families[family], subgroup=subgroup)
            if len(matches) == 1:
                candidates[family][cell.source_column].add(str(matches[0]["id"]))
    lane_columns: dict[str, dict[int, str]] = defaultdict(dict)
    conflicts: dict[str, dict[int, tuple[str, ...]]] = defaultdict(dict)
    for family, columns in candidates.items():
        for column, group_ids in columns.items():
            if len(group_ids) == 1:
                lane_columns[family][column] = next(iter(group_ids))
            elif group_ids:
                conflicts[family][column] = tuple(sorted(group_ids))
    return replace(context, lane_columns={key: dict(value) for key, value in lane_columns.items()},
                   lane_conflicts={key: dict(value) for key, value in conflicts.items()})


def _text(cell: ScheduleSourceCell) -> str:
    return " ".join((cell.raw_text, str(cell.parsed.get("subject") or ""), str(cell.parsed.get("modifiers") or "")))


def _match_group(groups: Iterable[Mapping[str, Any]], *, subgroup: str | None = None,
                 subject: str | None = None, exam: bool = False) -> list[Mapping[str, Any]]:
    result = []
    for group in groups:
        if subgroup is not None and _norm(group.get("subject_subgroup")) != _norm(subgroup):
            continue
        if subject and _norm(group.get("subject")) != _norm(subject):
            continue
        if exam and not _norm(group.get("exam_track")):
            continue
        result.append(group)
    return result


def _activity(cell: ScheduleSourceCell) -> str:
    no_lesson = normalize_no_lesson(cell.raw_text)
    if no_lesson:
        return no_lesson
    simple = classify_simple_activity(cell.raw_text)
    if simple:
        return simple
    subject = str(cell.parsed.get("subject") or "").strip()
    if subject:
        return re.sub(r"\s+(?:ОГЭ|OGE)\s*$", "", subject, flags=re.IGNORECASE).strip()
    first = next((line.strip() for line in cell.raw_text.splitlines() if line.strip()), "")
    return re.sub(r"\s+(?:ОГЭ|OGE)\s*$", "", first, flags=re.IGNORECASE).strip() or NO_LESSON


def _anchor(cell: ScheduleSourceCell) -> tuple[str, str] | None:
    text = _text(cell)
    math = _MATH_ANCHOR.search(text)
    if math:
        return "math", math.group(1).upper().replace("А", "A").replace("В", "B").replace("С", "C")
    english = _ENGLISH_ANCHOR.search(text)
    return ("english", english.group(1)) if english else None


def _belongs_to_partition(cell: ScheduleSourceCell, family: str) -> bool:
    """Check the cell's own semantics before applying a learned lane."""
    anchor = _anchor(cell)
    if anchor:
        return anchor[0] == family
    subject = _norm(cell.parsed.get("subject"))
    if family == "math":
        return subject in {"математика", "матем"}
    return subject in {"английский", "английский язык", "англ", "english"}


def _explicit_lane_refs(cell: ScheduleSourceCell, family: str) -> tuple[str, ...]:
    anchor = _anchor(cell)
    if anchor and anchor[0] == family:
        return (anchor[1],)
    match = re.search(r"\bгрупп\w*\s+([a-zа-яё0-9]+(?:\s*[,/]\s*[a-zа-яё0-9]+)*)", _text(cell), re.IGNORECASE)
    if not match:
        return ()
    values = []
    for value in re.split(r"\s*[,/]\s*", match.group(1)):
        normalized = value.upper().replace("А", "A").replace("В", "B").replace("С", "C") if family == "math" else value
        if (family == "math" and re.fullmatch(r"[A-Z]", normalized)) or (family == "english" and normalized.isdigit()):
            values.append(normalized)
    return tuple(dict.fromkeys(values))


def _assignments_conflicts(assignments: Iterable[ScheduleAssignment]) -> list[dict[str, Any]]:
    owners: dict[str, list[str]] = defaultdict(list)
    for assignment in assignments:
        for student_id in assignment.student_ids:
            owners[student_id].append(assignment.activity)
    return [{"student_id": student_id, "activities": activities} for student_id, activities in sorted(owners.items()) if len(activities) > 1]


def _merge_identical_assignments(assignments: Iterable[ScheduleAssignment]) -> list[ScheduleAssignment]:
    merged: dict[tuple[str, frozenset[str], str], ScheduleAssignment] = {}
    for assignment in assignments:
        key = (_norm(assignment.activity), assignment.student_ids, assignment.kind)
        current = merged.get(key)
        if current:
            merged[key] = replace(current,
                                  source_cells=tuple(dict.fromkeys((*current.source_cells, *assignment.source_cells))),
                                  group_ids=tuple(dict.fromkeys((*current.group_ids, *assignment.group_ids))))
        else:
            merged[key] = assignment
    return list(merged.values())


class NinthGradeResolver:
    """Resolve one row into student sets; never writes or allocates production data."""

    def __init__(self, context: NinthGradeContext | None = None):
        self.context = context or NinthGradeContext(frozenset())

    def _row_universe(self, row: ScheduleSourceRow) -> frozenset[str]:
        """A Grade 9 visual row always ranges over the whole Grade 9 cohort."""
        return self.context.all_students

    def _unresolved(self, row: ScheduleSourceRow, mode: ScheduleRowMode, reason: str, **extra: Any) -> ScheduleRowInterpretation:
        evidence = {"source_cells": [cell.source_cell for cell in row.cells], "source_columns": [cell.source_column for cell in row.cells], **extra}
        return ScheduleRowInterpretation(row.row_key, mode, unresolved_blocks=[{"row_key": row.row_key, "reason": reason, **evidence}], evidence=evidence)

    def _lane_groups(self, row: ScheduleSourceRow, family: str, anchors: list[tuple[ScheduleSourceCell, str]]) -> dict[int, Mapping[str, Any]]:
        groups = self.context.math_groups if family == "math" else self.context.english_groups
        by_id = {str(group.get("id")): group for group in groups}
        by_lane = {_norm(group.get("subject_subgroup")): group for group in groups if group.get("subject_subgroup")}
        mapping: dict[int, Mapping[str, Any]] = {
            int(column): by_id[group_id]
            for column, group_id in self.context.lane_columns.get(family, {}).items()
            if group_id in by_id
        }
        for cell, lane in anchors:
            group = by_lane.get(_norm(lane))
            if group and cell.source_column is not None:
                mapping[cell.source_column] = group
        return {column: group for column, group in mapping.items() if group}

    @staticmethod
    def _natural_key(value: Any) -> tuple[Any, ...]:
        return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", _norm(value)))

    def interpret(self, row: ScheduleSourceRow) -> ScheduleRowInterpretation:
        excluded = [cell for cell in row.cells
                    if _norm(cell.parsed.get("activity_type")) in {"extracurricular", "cancelled"}]
        if excluded:
            semantic_row = replace(row, cells=tuple(cell for cell in row.cells if cell not in excluded))
            if not semantic_row.cells:
                final = [ScheduleAssignment(NO_LESSON, self.context.all_students, (), (), "final_state")]
                return ScheduleRowInterpretation(
                    row.row_key,
                    ScheduleRowMode.SPECIAL,
                    evidence={"excluded_source_cells": [cell.source_cell for cell in excluded],
                              "remaining_student_ids": sorted(self.context.all_students)},
                    final_assignments=final,
                )
            result = self.interpret(semantic_row)
            result.evidence["excluded_source_cells"] = [cell.source_cell for cell in excluded]
            return result
        text = " ".join(_text(cell) for cell in row.cells)
        anchors = [(cell, value) for cell in row.cells if (value := _anchor(cell))]
        math_anchors = [(cell, value[1]) for cell, value in anchors if value[0] == "math"]
        english_anchors = [(cell, value[1]) for cell, value in anchors if value[0] == "english"]
        oge_cells = [cell for cell in row.cells if _OGE.search(_text(cell))]
        simple_cells = [cell for cell in row.cells if classify_simple_activity(cell.raw_text)]
        evidence: dict[str, Any] = {"source_cells": [cell.source_cell for cell in row.cells], "source_columns": {cell.source_cell: cell.source_column for cell in row.cells},
                                    "math_anchors": [{"cell": cell.source_cell, "lane": lane} for cell, lane in math_anchors],
                                    "english_anchors": [{"cell": cell.source_cell, "lane": lane} for cell, lane in english_anchors],
                                    "oge_cells": [cell.source_cell for cell in oge_cells],
                                    "no_lesson_cells": [cell.source_cell for cell in row.cells if normalize_no_lesson(cell.raw_text)],
                                    "math_anchor": math_anchors[0][1] if len(math_anchors) == 1 else None,
                                    "english_anchor": english_anchors[0][1] if len(english_anchors) == 1 else None,
                                    "simple_activities": {cell.source_cell: classify_simple_activity(cell.raw_text) for cell in simple_cells}}
        if math_anchors and english_anchors:
            return self._unresolved(row, ScheduleRowMode.UNKNOWN, "contradictory partition anchors", **evidence)
        if math_anchors:
            anchor_cells = {item[0].source_cell for item in math_anchors}
            overlays = [cell for cell in oge_cells if cell.source_cell not in anchor_cells]
            return self._resolve_partition(row, ScheduleRowMode.MATH_WITH_ELECTIVES if overlays else ScheduleRowMode.MATH, "math", math_anchors, overlays, evidence)
        if english_anchors:
            # Any canonically resolved instructional anchor is primary; a
            # lexical exam marker on that same cell cannot turn it into an
            # elective overlay.
            anchor_cells = {item[0].source_cell for item in english_anchors}
            overlays = [cell for cell in oge_cells if cell.source_cell not in anchor_cells]
            return self._resolve_partition(row, ScheduleRowMode.ENGLISH_WITH_ELECTIVES if overlays else ScheduleRowMode.ENGLISH, "english", english_anchors, overlays, evidence, english=True)
        if oge_cells:
            return self._resolve_electives(row, oge_cells, evidence)
        if simple_cells:
            return self._resolve_simple(row, simple_cells, evidence)
        return self._resolve_class(row, evidence)

    def _resolve_partition(self, row: ScheduleSourceRow, mode: ScheduleRowMode, family: str, anchors: list[tuple[ScheduleSourceCell, str]], oge_cells: list[ScheduleSourceCell], evidence: dict[str, Any], english: bool = False) -> ScheduleRowInterpretation:
        lane_map = self._lane_groups(row, family, anchors)
        evidence["lane_columns"] = {str(column): str(group.get("id")) for column, group in lane_map.items()}
        relevant_conflicts = {str(column): list(group_ids) for column, group_ids in self.context.lane_conflicts.get(family, {}).items()
                              if any(cell.source_column == column for cell in row.cells)}
        if relevant_conflicts:
            return self._unresolved(row, ScheduleRowMode.UNKNOWN, f"conflicting learned {family} lane mapping",
                                    lane_conflicts=relevant_conflicts, **evidence)
        if any(cell.source_column is not None and cell.source_column not in lane_map for cell, _ in anchors):
            return self._unresolved(row, ScheduleRowMode.UNKNOWN, f"explicit {family} anchor has no canonical group", **evidence)
        primary: list[ScheduleAssignment] = []
        row_universe = self._row_universe(row)
        for cell in row.cells:
            if cell in oge_cells or normalize_no_lesson(cell.raw_text):
                continue
            groups = []
            refs = _explicit_lane_refs(cell, family) if _belongs_to_partition(cell, family) else ()
            if refs:
                family_groups = self.context.math_groups if family == "math" else self.context.english_groups
                for ref in refs:
                    matches = _match_group(family_groups, subgroup=ref)
                    if len(matches) != 1:
                        return self._unresolved(row, ScheduleRowMode.UNKNOWN, f"explicit {family} group set is not canonical",
                                                **evidence, cell=cell.source_cell, subgroup=ref)
                    groups.append(matches[0])
            elif (_belongs_to_partition(cell, family)
                  and cell.source_column is not None
                  and cell.source_column in lane_map):
                groups = [lane_map[cell.source_column]]
            if not groups:
                continue
            students = frozenset().union(*(self.context.members(group) for group in groups)) & row_universe
            primary.append(ScheduleAssignment(_activity(cell), students, (cell.source_cell,),
                                              tuple(str(group["id"]) for group in groups), "primary"))
        conflicts = _assignments_conflicts(primary)
        blocked = {str(item["student_id"]) for item in conflicts}
        if conflicts:
            evidence["conflicting_memberships"] = conflicts
            primary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in primary]
        claimed = set().union(*(item.student_ids for item in primary)) if primary else set()
        available = set(row_universe) - claimed - blocked
        secondary_candidates: list[ScheduleAssignment] = []
        for cell in oge_cells:
            subject = _activity(cell)
            matches = _match_group(self.context.exam_groups, subject=subject, exam=True)
            if len(matches) != 1:
                return self._unresolved(row, mode, "OGE activity has no unique canonical instructional group", **evidence, missing_group=subject, cell=cell.source_cell)
            group = matches[0]
            students = self.context.members(group) & frozenset(available)
            secondary_candidates.append(ScheduleAssignment(subject, students, (cell.source_cell,), (str(group["id"]),), "secondary"))
        elective_conflicts = _assignments_conflicts(secondary_candidates)
        elective_blocked = {str(item["student_id"]) for item in elective_conflicts}
        if elective_conflicts:
            evidence["conflicting_elective_memberships"] = elective_conflicts
            blocked.update(elective_blocked)
        secondary = [replace(item, student_ids=frozenset(set(item.student_ids) - elective_blocked)) for item in secondary_candidates]
        available -= set().union(*(item.student_ids for item in secondary)) if secondary else set()
        available -= elective_blocked
        residual_cells = [cell for cell in row.cells if cell not in oge_cells and not normalize_no_lesson(cell.raw_text)
                          and not (_belongs_to_partition(cell, family) and _explicit_lane_refs(cell, family))
                          and not (_belongs_to_partition(cell, family)
                                   and cell.source_column is not None
                                   and cell.source_column in lane_map)]
        residual_activities = {_norm(_activity(cell)) for cell in residual_cells}
        default = None
        residual_assignments: list[ScheduleAssignment] = []
        if residual_cells and len(residual_activities) == 1:
            residual_groups = {
                str(group["id"]): group
                for cell in residual_cells
                for group in self._base_groups_for_cell(cell)
            }
            if not residual_groups:
                return self._unresolved(row, mode, "residual activity has no canonical base audience", **evidence,
                                        residual_cells=[cell.source_cell for cell in residual_cells])
            residual_scope = frozenset().union(
                *(self.context.members(group) for group in residual_groups.values())
            )
            assigned = frozenset(available) & residual_scope
            default = ScheduleAssignment(_activity(residual_cells[0]), assigned,
                                         tuple(cell.source_cell for cell in residual_cells),
                                         tuple(residual_groups), "default")
            evidence["explicit_complement_cells"] = [cell.source_cell for cell in residual_cells]
            evidence["explicit_complement_group_ids"] = list(residual_groups)
            available -= set(assigned)
        elif len(residual_activities) > 1:
            # Different ordinary activities can legitimately occupy the
            # residual class columns of the same parallel row (for example
            # Plastic for 9-А and Geography for 9-Д next to Math B).  Resolve
            # each cell against its own explicit base audience instead of
            # forcing all residual cells into one complement activity.
            for cell in residual_cells:
                groups = self._base_groups_for_cell(cell)
                if not groups:
                    return self._unresolved(row, mode, "residual activity has no canonical base audience", **evidence,
                                            residual_cells=[item.source_cell for item in residual_cells],
                                            unresolved_cell=cell.source_cell)
                scope = frozenset().union(*(self.context.members(group) for group in groups))
                assigned = frozenset(available) & scope
                residual_assignments.append(ScheduleAssignment(
                    _activity(cell), assigned, (cell.source_cell,),
                    tuple(str(group["id"]) for group in groups), "residual"))
                available -= set(assigned)
            evidence["explicit_residual_cells"] = [cell.source_cell for cell in residual_cells]
            evidence["explicit_residual_group_ids"] = {
                cell.source_cell: [str(group["id"]) for group in self._base_groups_for_cell(cell)]
                for cell in residual_cells
            }
            all_assignments = [*primary, *secondary, *residual_assignments]
            conflicts = _assignments_conflicts(all_assignments)
            if conflicts:
                blocked = {str(item["student_id"]) for item in conflicts}
                evidence["conflicting_memberships"] = conflicts
                primary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in primary]
                secondary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in secondary]
                residual_assignments = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in residual_assignments]
                available -= blocked
            # Keep the same return vocabulary: the residual assignments are
            # represented as default plus explicit residuals below.
            default = residual_assignments[0] if residual_assignments else None
            residual_assignments = residual_assignments[1:]
        evidence["remaining_student_ids"] = sorted(available)
        unresolved = []
        if blocked:
            unresolved.append({"row_key": row.row_key, "reason": "contradictory memberships", "student_ids": sorted(blocked)})
        final = [ScheduleAssignment(NO_LESSON, frozenset(available), (), (), "final_state")] if available else []
        secondary = [*secondary, *residual_assignments]
        return ScheduleRowInterpretation(row.row_key, mode, primary, secondary, default,
                                         unresolved_blocks=unresolved, evidence=evidence, final_assignments=final)

    def _resolve_electives(self, row: ScheduleSourceRow, cells: list[ScheduleSourceCell], evidence: dict[str, Any]) -> ScheduleRowInterpretation:
        secondary: list[ScheduleAssignment] = []
        for cell in cells:
            subject = _activity(cell)
            matches = _match_group(self.context.exam_groups, subject=subject, exam=True)
            if len(matches) != 1:
                return self._unresolved(row, ScheduleRowMode.ELECTIVES, "missing canonical OGE instructional group", **evidence, missing_group=subject)
            group = matches[0]
            secondary.append(ScheduleAssignment(subject, self.context.members(group), (cell.source_cell,), (str(group["id"]),), "secondary"))
        conflicts = _assignments_conflicts(secondary)
        blocked = {str(item["student_id"]) for item in conflicts}
        if conflicts:
            evidence["conflicting_elective_memberships"] = conflicts
            secondary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in secondary]
        claimed = set().union(*(assignment.student_ids for assignment in secondary)) if secondary else set()
        available = set(self._row_universe(row)) - claimed - blocked
        residual_cells = [cell for cell in row.cells if cell not in cells and not normalize_no_lesson(cell.raw_text)]
        residual_activities = {_norm(_activity(cell)) for cell in residual_cells}
        if len(residual_activities) > 1:
            return self._unresolved(row, ScheduleRowMode.ELECTIVES, "multiple residual activities cannot be assigned as one complement",
                                    **evidence, residual_cells=[cell.source_cell for cell in residual_cells])
        default = None
        if residual_cells:
            residual_groups = {
                str(group["id"]): group
                for cell in residual_cells
                for group in self._base_groups_for_cell(cell)
            }
            if not residual_groups:
                return self._unresolved(
                    row,
                    ScheduleRowMode.ELECTIVES,
                    "residual activity has no canonical base audience",
                    **evidence,
                    residual_cells=[cell.source_cell for cell in residual_cells],
                )
            residual_scope = frozenset().union(
                *(self.context.members(group) for group in residual_groups.values())
            )
            assigned = frozenset(available) & residual_scope
            default = ScheduleAssignment(_activity(residual_cells[0]), assigned,
                                         tuple(cell.source_cell for cell in residual_cells),
                                         tuple(residual_groups), "default")
            evidence["explicit_complement_cells"] = [cell.source_cell for cell in residual_cells]
            evidence["explicit_complement_group_ids"] = list(residual_groups)
            available -= set(assigned)
        evidence["remaining_student_ids"] = sorted(available)
        unresolved = [{"row_key": row.row_key, "reason": "contradictory elective memberships", "student_ids": sorted(blocked)}] if blocked else []
        final = [ScheduleAssignment(NO_LESSON, frozenset(available), (), (), "final_state")] if available else []
        return ScheduleRowInterpretation(row.row_key, ScheduleRowMode.ELECTIVES, secondary_assignments=secondary,
                                         default_assignment=default, unresolved_blocks=unresolved,
                                         evidence=evidence, final_assignments=final)

    def _base_groups_for_cell(self, cell: ScheduleSourceCell) -> list[Mapping[str, Any]]:
        audiences = [cell.audience, *cell.merged_audiences]
        return [group for group in self.context.base_classes
                if any(_norm(group.get("name")) == _norm(audience)
                       or _norm(group.get("display_name")) == _norm(audience)
                       or _norm(group.get("base_class_name")) == _norm(audience) for audience in audiences)]

    def _resolve_simple(self, row: ScheduleSourceRow, cells: list[ScheduleSourceCell], evidence: dict[str, Any]) -> ScheduleRowInterpretation:
        primary: list[ScheduleAssignment] = []
        for cell in cells:
            groups = self._base_groups_for_cell(cell)
            if not groups:
                return self._unresolved(row, ScheduleRowMode.SPECIAL, "simple activity has no canonical base audience", **evidence,
                                        audience=cell.audience, cell=cell.source_cell)
            students = frozenset().union(*(self.context.members(group) for group in groups))
            primary.append(ScheduleAssignment(_activity(cell), students, (cell.source_cell,),
                                              tuple(str(group["id"]) for group in groups), "primary"))
        return self._finalize(row, ScheduleRowMode.SPECIAL, _merge_identical_assignments(primary), [], None, evidence)

    def _resolve_class(self, row: ScheduleSourceRow, evidence: dict[str, Any]) -> ScheduleRowInterpretation:
        primary: list[ScheduleAssignment] = []
        for cell in row.cells:
            if normalize_no_lesson(cell.raw_text):
                continue
            candidates = self._base_groups_for_cell(cell)
            if not candidates:
                return self._unresolved(row, ScheduleRowMode.CLASS, "base class is not uniquely canonical", **evidence, audience=cell.audience)
            students = frozenset().union(*(self.context.members(group) for group in candidates))
            primary.append(ScheduleAssignment(_activity(cell), students, (cell.source_cell,),
                                              tuple(str(group["id"]) for group in candidates), "primary"))
        return self._finalize(row, ScheduleRowMode.CLASS, primary, [], None, evidence)

    def _finalize(self, row: ScheduleSourceRow, mode: ScheduleRowMode, primary: list[ScheduleAssignment],
                  secondary: list[ScheduleAssignment], default: ScheduleAssignment | None,
                  evidence: dict[str, Any]) -> ScheduleRowInterpretation:
        conflicts = _assignments_conflicts([*primary, *secondary])
        blocked = {str(item["student_id"]) for item in conflicts}
        if conflicts:
            evidence["conflicting_memberships"] = conflicts
            primary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in primary]
            secondary = [replace(item, student_ids=frozenset(set(item.student_ids) - blocked)) for item in secondary]
        claimed = set().union(*(item.student_ids for item in [*primary, *secondary])) if primary or secondary else set()
        if default:
            claimed.update(default.student_ids)
        remaining = set(self._row_universe(row)) - claimed - blocked
        evidence["remaining_student_ids"] = sorted(remaining)
        unresolved = [{"row_key": row.row_key, "reason": "contradictory memberships", "student_ids": sorted(blocked)}] if blocked else []
        final = [ScheduleAssignment(NO_LESSON, frozenset(remaining), (), (), "final_state")] if remaining else []
        return ScheduleRowInterpretation(row.row_key, mode, primary, secondary, default,
                                         unresolved_blocks=unresolved, evidence=evidence, final_assignments=final)


def compare_v1_v2(v1: Mapping[str, Any] | None, v2: ScheduleRowInterpretation | None) -> str:
    if not v1 or not v2:
        return "not_comparable"
    if v2.unresolved_blocks:
        return "v2_new_unresolved" if v1.get("resolution_status") in {"RESOLVED", "WARNING"} else "equivalent"
    if v2.evidence.get("conflicting_memberships") and not v1.get("conflicts"):
        return "regression"
    v1_mode = str(v1.get("mode") or v1.get("activity_type") or "")
    v2_students = set().union(*(assignment.student_ids for assignment in v2.primary_assignments + v2.secondary_assignments))
    v1_students = {str(value) for value in (v1.get("student_ids") or [])}
    if v1_mode == v2.mode.value and v1_students == v2_students:
        return "equivalent"
    if v1.get("resolution_status") in {"WARNING", "UNRESOLVED"} and v2.mode != ScheduleRowMode.UNKNOWN:
        return "v2_fixed_likely_error"
    if v2.mode.value in {"MATH", "ENGLISH", "MATH_WITH_ELECTIVES", "ENGLISH_WITH_ELECTIVES", "ELECTIVES"}:
        return "v2_more_specific"
    return "regression"


def format_row_diagnostic(row: ScheduleSourceRow, interpretation: ScheduleRowInterpretation) -> str:
    day = row.date or (f"weekday {row.weekday}" if row.weekday is not None else "unknown day")
    lines = [f"{day} / {row.start_time}-{row.end_time} / Grade {row.grade_scope or 'unknown'}", "cells:"]
    for cell in row.cells:
        column = cell.source_column if cell.source_column is not None else "?"
        lines.append(f'  col {column}: {cell.source_cell} "{cell.raw_text}" ({cell.audience or "no owner"})')
    math_anchor = interpretation.evidence.get("math_anchor") or interpretation.evidence.get("math_anchors") or "none"
    english_anchor = interpretation.evidence.get("english_anchor") or interpretation.evidence.get("english_anchors") or "none"
    lines.extend(["", "anchors:", f"  math: {math_anchor}", f"  english: {english_anchor}", "", f"possible mode: {interpretation.mode.value}", f"unresolved: {', '.join(str(item.get('reason', item)) if isinstance(item, Mapping) else str(item) for item in interpretation.unresolved_blocks) or 'none'}"])
    return "\n".join(lines)
