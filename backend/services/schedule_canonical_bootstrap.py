"""Build the initial, agent-reviewed canonical routing template from a live corpus.

This is a bootstrap utility, not an authoritative runtime importer.  It uses
the immutable schedule snapshot plus canonical memberships and preserves every
unresolved routing fact instead of converting ambiguity to NO_LESSON.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence

from backend.services.ninth_grade_resolver import NinthGradeResolver, learn_partition_lanes
from backend.services.schedule_parser_v2 import (
    NO_LESSON,
    NinthGradeContext,
    ScheduleAssignment,
    ScheduleSourceRow,
    classify_simple_activity,
    group_schedule_rows,
    normalize_no_lesson,
)
from backend.services.school_data import stable_fingerprint


_GRADE = re.compile(r"^\s*(\d{1,2})")
_ENGLISH_NUMBER = re.compile(r"(?:англ(?:ийский)?|english)\s*(\d{1,2})(?:\s*/\s*(\d{1,2}))?", re.IGNORECASE)
_EXAM = re.compile(r"\b(?:огэ|егэ|oge)\b", re.IGNORECASE)


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def _grade(value: Any) -> str:
    match = _GRADE.match(str(value or ""))
    return match.group(1) if match else ""


def _subject(value: Any) -> str:
    text = _norm(value)
    aliases = (
        ("англ", "английский"), ("english", "английский"),
        ("мат", "математика"), ("инфор", "информатика"),
        ("общ", "обществознание"), ("литер", "литература"),
        ("био", "биология"), ("физ", "физика"),
    )
    for prefix, canonical in aliases:
        if text == prefix or text.startswith(prefix):
            return canonical
    return text


def _lesson_source(lesson: Mapping[str, Any]) -> dict[str, Any]:
    raw = lesson.get("raw_payload") if isinstance(lesson.get("raw_payload"), Mapping) else {}
    result = {**dict(raw), **dict(lesson)}
    result["raw_payload"] = dict(raw)
    # Parsed live-matrix lessons already carry source facts at the top level;
    # historical DB rows carry them inside raw_payload.  Preserve either form
    # instead of silently dropping coordinates/raw text in the adapter.
    result["raw_text"] = str(raw.get("raw_text") or lesson.get("raw_text") or lesson.get("subject") or "")
    result["source_column"] = raw.get("source_column") if raw.get("source_column") is not None else lesson.get("source_column")
    result["source_row"] = raw.get("source_row") if raw.get("source_row") is not None else lesson.get("source_row")
    result["merged_audiences"] = list(raw.get("merged_audiences") or lesson.get("merged_audiences") or [])
    return result


def _membership_index(corpus: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for item in corpus.get("memberships") or []:
        result[str(item["group_id"])].add(str(item["identity_id"]))
    return {key: frozenset(value) for key, value in result.items()}


def _group_role(group: Mapping[str, Any], memberships: Mapping[str, frozenset[str]], students: Mapping[str, Mapping[str, Any]]) -> str:
    if _norm(group.get("group_type")) == "class":
        return "base_class"
    if group.get("exam_track"):
        return "elective_overlay"
    if str(group.get("name") or "").startswith("generic:"):
        return "special_activity_group"
    member_grades = {_grade(students[item].get("class_name")) for item in memberships.get(str(group.get("id")), ()) if item in students}
    return "instructional_partition" if member_grades else "unknown"


def _semantic_group_roles(
    groups: Sequence[Mapping[str, Any]],
    memberships: Mapping[str, frozenset[str]],
    students: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Infer directory roles from membership structure, not lexical markers alone."""
    roles = {str(group["id"]): _group_role(group, memberships, students) for group in groups}
    cohorts: dict[str, set[str]] = defaultdict(set)
    for student_id, student in students.items():
        cohorts[_grade(student.get("class_name"))].add(student_id)
    subjects = {_subject(group.get("subject")) for group in groups if _subject(group.get("subject"))}
    for grade, cohort in cohorts.items():
        if not grade or not cohort:
            continue
        for subject in subjects:
            candidates = [
                group for group in groups
                if _norm(group.get("group_type")) != "class"
                and not str(group.get("name") or "").startswith("generic:")
                and _subject(group.get("subject")) == subject
                and bool(set(memberships.get(str(group["id"]), ())) & cohort)
            ]
            member_sets = [set(memberships.get(str(group["id"]), ())) & cohort for group in candidates]
            counts = Counter(student_id for member_set in member_sets for student_id in member_set)
            covered = set(counts)
            # A one-student directory gap is preserved separately, but does not
            # turn an otherwise clear instructional partition into an overlay.
            if len(candidates) >= 2 and len(cohort - covered) <= 1 and not any(count > 1 for count in counts.values()):
                for group in candidates:
                    roles[str(group["id"])] = "instructional_partition"
    return roles


def audit_groups(corpus: Mapping[str, Any]) -> dict[str, Any]:
    students = {str(item["id"]): item for item in corpus.get("students") or []}
    memberships = _membership_index(corpus)
    groups = list(corpus.get("groups") or [])
    by_id = {str(group["id"]): group for group in groups}
    semantic_roles = _semantic_group_roles(groups, memberships, students)
    classification = [
        {
            "group_id": group_id,
            "name": group.get("name"),
            "display_name": group.get("display_name"),
            "role": semantic_roles[group_id],
            "active_member_count": len(memberships.get(group_id, ())),
            "member_classes": dict(Counter(str(students[item].get("class_name")) for item in memberships.get(group_id, ()) if item in students)),
        }
        for group_id, group in by_id.items()
    ]
    base_by_grade: dict[str, set[str]] = defaultdict(set)
    for group_id, group in by_id.items():
        if _norm(group.get("group_type")) == "class":
            base_by_grade[_grade(group.get("base_class_name") or group.get("name"))].update(memberships.get(group_id, ()))

    students_by_grade: dict[str, set[str]] = defaultdict(set)
    for student_id, student in students.items():
        students_by_grade[_grade(student.get("class_name"))].add(student_id)
    base_coverage = [
        {
            "grade": grade,
            "active_student_count": len(student_ids),
            "covered_count": len(student_ids & base_by_grade.get(grade, set())),
            "missing_student_ids": sorted(student_ids - base_by_grade.get(grade, set())),
            "foreign_student_ids": sorted(base_by_grade.get(grade, set()) - student_ids),
        }
        for grade, student_ids in sorted(students_by_grade.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 99)
        if grade
    ]

    def partition(name: str, group_ids: Sequence[str], grade: str) -> dict[str, Any]:
        sets = [set(memberships.get(group_id, ())) & base_by_grade[grade] for group_id in group_ids]
        counts: Counter[str] = Counter(item for members in sets for item in members)
        return {
            "name": name,
            "grade": grade,
            "group_ids": list(group_ids),
            "group_counts": [len(item) for item in sets],
            "cohort_count": len(base_by_grade[grade]),
            "covered_count": len(set().union(*sets) if sets else set()),
            "missing_student_ids": sorted(base_by_grade[grade] - set(counts)),
            "overlapping_student_ids": sorted(item for item, count in counts.items() if count > 1),
        }

    def named(prefixes: Sequence[str]) -> list[str]:
        return [group_id for group_id, group in by_id.items() if str(group.get("name") or "") in prefixes]

    partitions = [
        partition("grade9_math", named(("grade9-math-A", "grade9-math-B", "grade9-math-C")), "9"),
        partition("grade9_english", named(("english:6", "english:7", "english:8")), "9"),
        partition("grade5_6_english", named(("english:1", "english:2")), "5"),
        partition("grade5_6_english", named(("english:1", "english:2")), "6"),
        partition("grade7_8_english", named(("english:3", "english:4", "english:5")), "7"),
        partition("grade7_8_english", named(("english:3", "english:4", "english:5")), "8"),
        partition("grade10_11_english", named(("english:9", "english:10")), "10"),
        partition("grade10_11_english", named(("english:9", "english:10")), "11"),
        partition("grade11_math", named(("math:11:base", "math:11:advanced")), "11"),
        partition("grade11_literature", named(("instructional:литература:11:база", "instructional:литература:11:егэ")), "11"),
        partition("grade10_social", named(("instructional:обществознание:10:база", "instructional:обществознание:10:угл")), "10"),
        partition("grade11_social", named(("instructional:обществознание:11:база", "instructional:обществознание:11:угл")), "11"),
    ]
    return {"classification": classification, "base_coverage": base_coverage, "partitions": partitions}


def _ninth_context(corpus: Mapping[str, Any], memberships: Mapping[str, frozenset[str]]) -> NinthGradeContext:
    groups = list(corpus.get("groups") or [])
    students = {str(item["id"]) for item in corpus.get("students") or [] if _grade(item.get("class_name")) == "9"}
    base = tuple(group for group in groups if _norm(group.get("group_type")) == "class" and _grade(group.get("base_class_name") or group.get("name")) == "9")
    math = tuple(group for group in groups if _subject(group.get("subject")) == "математика" and _norm(group.get("subject_subgroup")) in {"a", "b", "c", "а", "в", "с"})
    english = tuple(group for group in groups if str(group.get("name") or "") in {"english:6", "english:7", "english:8"})
    exam = tuple(group for group in groups if _norm(group.get("exam_track")) in {"огэ", "oge"} and bool(set(memberships.get(str(group.get("id")), ())) & students))
    return NinthGradeContext(frozenset(students), base, math, english, exam, memberships)


def _base_groups(row: ScheduleSourceRow, groups: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    row_grades = set(row.grade_scope.split(","))
    return [group for group in groups if _norm(group.get("group_type")) == "class" and _grade(group.get("base_class_name") or group.get("name")) in row_grades]


def _exact_class_groups(cell: Any, groups: Sequence[Mapping[str, Any]]) -> list[str]:
    names = {_norm(cell.audience), *(_norm(item) for item in cell.merged_audiences)}
    return [str(group["id"]) for group in groups if _norm(group.get("group_type")) == "class" and _norm(group.get("base_class_name") or group.get("name")) in names]


def _english_group_for_cell(cell: Any, target_grade: str, groups: Sequence[Mapping[str, Any]]) -> str | None:
    match = _ENGLISH_NUMBER.search(cell.raw_text)
    if not match:
        return None
    numbers = [match.group(1), match.group(2)]
    number = numbers[0]
    if numbers[1] and target_grade == "6":
        number = numbers[1]
    for group in groups:
        if str(group.get("name") or "") == f"english:{number}":
            return str(group["id"])
    return None


def _explicit_instructional_marker(cell: Any) -> bool:
    """Return whether source text explicitly claims a routed partition."""
    raw = str(cell.raw_text or "")
    if _ENGLISH_NUMBER.search(raw) or _EXAM.search(raw):
        return True
    return bool(re.search(
        r"\b(?:мат(?:ематика|ем)?|матпроф|физ(?:ика)?|био(?:логия)?|"
        r"информ(?:атика)?|литер(?:атура)?|обществ(?:ознание)?|хим(?:ия)?)"
        r"\s+(?:группа\s*)?(?:[а-яa-z]|\d+)\b",
        raw,
        re.IGNORECASE,
    ))


def _instructional_group(cell: Any, grade: str, groups: Sequence[Mapping[str, Any]], universe: set[str], memberships: Mapping[str, frozenset[str]]) -> str | None:
    english = _english_group_for_cell(cell, grade, groups)
    if english:
        return english
    raw = _norm(cell.raw_text)
    subject = _subject(cell.parsed.get("subject"))
    candidates = []
    for group in groups:
        group_id = str(group["id"])
        if _norm(group.get("group_type")) == "class" or not (set(memberships.get(group_id, ())) & universe):
            continue
        if subject and _subject(group.get("subject")) != subject:
            continue
        group_grade = _grade(group.get("base_class_name"))
        if group_grade and group_grade != grade:
            continue
        candidates.append(group)
    if _EXAM.search(cell.raw_text):
        exam = [item for item in candidates if _norm(item.get("exam_track")) in {"огэ", "егэ", "oge"}]
        if len(exam) == 1:
            return str(exam[0]["id"])
        # Some current rosters name the canonical exam-preparation
        # partition "Угл" while the schedule says ЕГЭ.  Treat that as an
        # equivalent only when the directory proves there is exactly one
        # advanced instructional candidate for this subject/grade; never
        # choose among several candidates or fall back to the base class.
        advanced = [item for item in candidates if _norm(item.get("subject_subgroup")) in {"угл", "advanced", "углублённая", "углубленная"}]
        if len(advanced) == 1:
            return str(advanced[0]["id"])
        return None
    if any(marker in raw for marker in ("база", "базовая")):
        base = [item for item in candidates if _norm(item.get("subject_subgroup")) in {"база", "base", "базовая"}]
        if len(base) == 1:
            return str(base[0]["id"])
    if any(marker in raw for marker in ("матпроф", "проф", "угл")):
        advanced = [item for item in candidates if _norm(item.get("subject_subgroup")) in {"угл", "advanced", "углублённая", "углубленная"}]
        if len(advanced) == 1:
            return str(advanced[0]["id"])
    return None


def _assignment_payload(activity: str, role: str, group_ids: Sequence[str], student_ids: set[str], cells: Sequence[Any], teachers: Mapping[str, str], teacher_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "activity": activity,
        "role": role,
        "audience": {"type": "canonical_groups", "canonical_group_ids": list(group_ids)},
        "student_ids": sorted(student_ids),
        "student_count": len(student_ids),
        "teacher_ids": list(teacher_ids),
        "teachers": [teachers[item] for item in teacher_ids if item in teachers],
        "source_cells": [cell.source_cell for cell in cells],
    }


def _simple_block(row: ScheduleSourceRow, slot_rows: Sequence[ScheduleSourceRow], corpus: Mapping[str, Any], memberships: Mapping[str, frozenset[str]], teachers: Mapping[str, str]) -> dict[str, Any]:
    groups = list(corpus.get("groups") or [])
    group_by_id = {str(group["id"]): group for group in groups}
    students = {str(item["id"]): item for item in corpus.get("students") or []}
    semantic_roles = _semantic_group_roles(groups, memberships, students)
    base = _base_groups(row, groups)
    row_grades = set(row.grade_scope.split(","))
    universe = {
        str(student["id"])
        for student in corpus.get("students") or []
        if _grade(student.get("class_name")) in row_grades
    }
    base_members = set().union(*(set(memberships.get(str(group["id"]), ())) for group in base)) if base else set()
    base_gaps = universe - base_members
    cells = list(row.cells)
    # English partitions cross adjacent class columns. Bring the complete
    # parallel line into each grade block, then intersect by its base cohort.
    english_partition = any(_ENGLISH_NUMBER.search(cell.raw_text) for cell in cells)
    if english_partition:
        families = {"5": {"5", "6"}, "6": {"5", "6"}, "7": {"7", "8"}, "8": {"7", "8"}, "10": {"10", "11"}, "11": {"10", "11"}}
        target = next(iter(row_grades), "")
        related = families.get(target, {target})
        # The instructional group may span adjacent grades, but each source
        # column still has an owner.  Do not copy Grade 11's rendered cell
        # into Grade 10's block (or vice versa); that creates duplicate teacher
        # routes while losing source ownership evidence.
        cells = [
            cell for item in slot_rows if set(item.grade_scope.split(",")) & related
            for cell in item.cells
            if _ENGLISH_NUMBER.search(cell.raw_text) and _grade(cell.audience) in row_grades
        ]
    assignments: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    grouped_cells: set[str] = set()
    for cell in cells:
        if cell.parsed.get("activity_type") == "extracurricular" or cell.raw_text.lstrip().startswith("⚪"):
            continue
        target_grade = next(iter(row_grades), "")
        group_id = _instructional_group(cell, target_grade, groups, universe, memberships)
        if group_id:
            group = group_by_id[group_id]
            members = (set(memberships.get(group_id, ())) & universe) - base_gaps
            role = "primary" if semantic_roles[group_id] == "instructional_partition" else "elective_overlay"
            teacher_ids = [str(item) for item in cell.parsed.get("resolved_identity_ids", ())]
            activity = classify_simple_activity(cell.raw_text) or str(group.get("subject") or cell.parsed.get("subject") or cell.raw_text)
            assignments.append(_assignment_payload(activity, role, [group_id], members, [cell], teachers, teacher_ids))
            grouped_cells.add(cell.source_cell)
        elif _explicit_instructional_marker(cell):
            # A source marker such as English 6 or Physics EGE is an
            # audience claim, not an ordinary whole-class lesson.  Do not
            # fall through to the base class when the canonical partition is
            # absent or ambiguous.
            unresolved.append({
                "source_cell": cell.source_cell,
                "reason": "explicit instructional audience has no unique canonical group",
                "raw_text": cell.raw_text,
            })
            grouped_cells.add(cell.source_cell)
    active_students = set().union(*(set(item["student_ids"]) for item in assignments)) if assignments else set()
    for cell in row.cells:
        if cell.source_cell in grouped_cells or cell.parsed.get("activity_type") == "extracurricular" or cell.raw_text.lstrip().startswith("⚪"):
            continue
        exact_groups = _exact_class_groups(cell, groups)
        audience = set().union(*(set(memberships.get(group_id, ())) for group_id in exact_groups)) & universe if exact_groups else set(universe)
        audience -= base_gaps
        no_lesson = normalize_no_lesson(cell.raw_text)
        if no_lesson:
            audience -= active_students
            if audience:
                assignments.append(_assignment_payload(NO_LESSON, "explicit_no_lesson", exact_groups, audience, [cell], teachers, ()))
            continue
        activity = classify_simple_activity(cell.raw_text) or str(cell.parsed.get("subject") or cell.raw_text)
        role = "residual" if assignments else "primary"
        if role == "residual":
            audience -= active_students
        teacher_ids = [str(item) for item in cell.parsed.get("resolved_identity_ids", ())]
        if audience:
            assignments.append(_assignment_payload(activity, role, exact_groups, audience, [cell], teachers, teacher_ids))
            active_students.update(audience)
        elif not exact_groups and universe:
            unresolved.append({"source_cell": cell.source_cell, "reason": "canonical audience cannot be proven"})
    per_student: dict[str, list[str]] = defaultdict(list)
    for assignment in assignments:
        if assignment["activity"] != NO_LESSON:
            for student_id in assignment["student_ids"]:
                per_student[student_id].append(assignment["activity"])
    conflicts = {student_id: values for student_id, values in per_student.items() if len(set(values)) > 1}
    partition_gaps: set[str] = set()
    if english_partition:
        english_members = {
            student_id for group in groups if str(group.get("name") or "").startswith("english:")
            for student_id in memberships.get(str(group["id"]), ())
        }
        partition_gaps = universe - english_members
        if partition_gaps:
            unresolved.append({"reason": "student is missing from the active English instructional partition", "student_ids": sorted(partition_gaps)})
    if base_gaps:
        unresolved.append({"reason": "student is missing from the canonical base-class membership", "student_ids": sorted(base_gaps)})
    if conflicts:
        unresolved.append({"reason": "incompatible double assignment", "student_ids": sorted(conflicts)})
    conflict_students = set(conflicts) | partition_gaps | base_gaps
    if conflict_students:
        for assignment in assignments:
            assignment["student_ids"] = sorted(set(assignment["student_ids"]) - conflict_students)
            assignment["student_count"] = len(assignment["student_ids"])
    assigned = {item for assignment in assignments for item in assignment["student_ids"]}
    remaining = universe - assigned - conflict_students
    if remaining:
        assignments.append(_assignment_payload(NO_LESSON, "final_unassigned", (), remaining, (), teachers, ()))
    return {"assignments": assignments, "unresolved": unresolved, "universe": universe, "conflict_students": conflict_students}


def _ninth_block(row: ScheduleSourceRow, resolver: NinthGradeResolver, teachers: Mapping[str, str]) -> dict[str, Any]:
    result = resolver.interpret(row)
    assignments: list[dict[str, Any]] = []
    semantic_assignments: list[ScheduleAssignment] = [*result.primary_assignments, *result.secondary_assignments]
    if result.default_assignment is not None:
        semantic_assignments.append(result.default_assignment)
    semantic_assignments.extend(result.final_assignments)
    for item in semantic_assignments:
        source = [cell for cell in row.cells if cell.source_cell in item.source_cells]
        teacher_ids = sorted({str(value) for cell in source for value in cell.parsed.get("resolved_identity_ids", ())})
        assignments.append(_assignment_payload(item.activity, item.kind, item.group_ids, set(item.student_ids), source, teachers, teacher_ids))
    per_student: dict[str, list[str]] = defaultdict(list)
    for assignment in assignments:
        if assignment["activity"] != NO_LESSON:
            for student_id in assignment["student_ids"]:
                per_student[student_id].append(assignment["activity"])
    conflicts = {student_id for student_id, values in per_student.items() if len(set(values)) > 1}
    unresolved = list(result.unresolved_blocks)
    for issue in unresolved:
        conflicts.update(str(item) for item in issue.get("student_ids", ()) if item)
    family = "math" if result.evidence.get("math_anchors") else "english" if result.evidence.get("english_anchors") else ""
    if family:
        groups = resolver.context.math_groups if family == "math" else resolver.context.english_groups
        partition_members = set().union(*(set(resolver.context.members(group)) for group in groups)) if groups else set()
        missing = set(resolver.context.all_students) - partition_members
        if missing:
            unresolved.append({"reason": f"student is missing from the active {family} instructional partition", "student_ids": sorted(missing)})
            conflicts.update(missing)
    if conflicts:
        for assignment in assignments:
            if assignment["activity"] == NO_LESSON:
                assignment["student_ids"] = sorted(set(assignment["student_ids"]) - conflicts)
                assignment["student_count"] = len(assignment["student_ids"])
    return {"assignments": assignments, "unresolved": unresolved, "universe": set(resolver.context.all_students), "conflict_students": conflicts, "mode": result.mode.value, "evidence": result.evidence}


def _is_sdep_row(row: ScheduleSourceRow, explicit_sdep: set[tuple[int | None, str]]) -> bool:
    if row.grade_scope not in {"10", "11"}:
        return False
    return (row.weekday, row.grade_scope) in explicit_sdep or any(
        re.search(r"\bsdep\b", str(cell.provenance.get("source_day_label") or ""), re.IGNORECASE)
        for cell in row.cells
    )


def build_bootstrap_canonical(corpus: Mapping[str, Any], *, explicit_sdep: Sequence[tuple[int | None, str]] = ()) -> dict[str, Any]:
    lessons = [_lesson_source(item) for item in corpus.get("lessons") or []]
    # Expose normalized resolver fields retained inside raw_payload.
    for lesson in lessons:
        lesson["resolved_identity_ids"] = list(lesson.get("resolved_identity_ids") or [])
        lesson["resolved_group_ids"] = list(lesson.get("resolved_group_ids") or [])
    rows = group_schedule_rows(lessons)
    memberships = _membership_index(corpus)
    teachers = {str(item["id"]): str(item["display_name"]) for item in corpus.get("teachers") or []}
    students = {str(item["id"]): item for item in corpus.get("students") or []}
    ninth_rows = [row for row in rows if row.grade_scope == "9"]
    ninth_context = learn_partition_lanes(_ninth_context(corpus, memberships), ninth_rows)
    ninth_resolver = NinthGradeResolver(ninth_context)
    sdep_rows: dict[tuple[int | None, str], list[ScheduleSourceRow]] = defaultdict(list)
    regular_rows: list[ScheduleSourceRow] = []
    for row in rows:
        if _is_sdep_row(row, set(explicit_sdep)):
            sdep_rows[(row.weekday, row.grade_scope)].append(row)
        else:
            regular_rows.append(row)
    by_slot: dict[tuple[Any, ...], list[ScheduleSourceRow]] = defaultdict(list)
    for row in regular_rows:
        by_slot[(row.weekday, row.start_time, row.end_time)].append(row)
    blocks: dict[str, Any] = {}
    student_routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_blocks: list[dict[str, Any]] = []
    warning_count = 0
    for row in regular_rows:
        data = _ninth_block(row, ninth_resolver, teachers) if row.grade_scope == "9" else _simple_block(row, by_slot[(row.weekday, row.start_time, row.end_time)], corpus, memberships, teachers)
        universe = set(data.pop("universe"))
        conflict_students = set(data.pop("conflict_students"))
        status = "unresolved" if data["unresolved"] or conflict_students else "resolved"
        source_cells = [
            {
                "coordinate": cell.source_cell,
                "column": cell.source_column,
                "raw_text": cell.raw_text,
                "audience": cell.audience,
                "color": cell.source_color,
                "merge": cell.merge_data,
                "merged_audiences": list(cell.merged_audiences),
            }
            for cell in row.cells
        ]
        block = {
            "block_key": row.row_key,
            "weekday": row.weekday,
            "slot": {"start": row.start_time, "end": row.end_time},
            "grade_scope": row.grade_scope,
            "status": status,
            "interpretation_source": "agent_bootstrap_v1",
            "mode": data.get("mode", "DETERMINISTIC_SIMPLE"),
            "assignments": data["assignments"],
            "unresolved": data["unresolved"],
            "source_warnings": [
                {"source_cell": cell.source_cell, "resolution_status": cell.parsed.get("resolution_status")}
                for cell in row.cells if cell.parsed.get("resolution_status") in {"WARNING", "CONFLICT"}
            ],
            "derived_from": {
                "sheet": next((cell.provenance.get("tab_title") for cell in row.cells if cell.provenance.get("tab_title")), None),
                "source_cells": source_cells,
                "structural_fingerprint": stable_fingerprint(source_cells),
            },
            "evidence": data.get("evidence", {}),
        }
        warning_count += len(block["source_warnings"])
        blocks[row.row_key] = block
        if status == "unresolved":
            unresolved_blocks.append({"block_key": row.row_key, "weekday": row.weekday, "slot": row.start_time, "issues": data["unresolved"], "conflict_student_ids": sorted(conflict_students)})
        student_activities: dict[str, list[str]] = defaultdict(list)
        for assignment in data["assignments"]:
            for student_id in assignment["student_ids"]:
                student_activities[student_id].append(assignment["activity"])
        for student_id in sorted(universe):
            activities = [item for item in student_activities.get(student_id, []) if item != NO_LESSON]
            if student_id in conflict_students:
                state, activity = "UNRESOLVED", None
            elif len(set(activities)) == 1:
                state, activity = "ACTIVITY", activities[0]
            elif len(set(activities)) > 1:
                state, activity = "UNRESOLVED", None
            else:
                state, activity = NO_LESSON, NO_LESSON
            student_routes[student_id].append({"block_key": row.row_key, "weekday": row.weekday, "start_time": row.start_time, "state": state, "activity": activity})
    groups = list(corpus.get("groups") or [])
    for (weekday, grade_scope), day_rows in sorted(sdep_rows.items(), key=lambda item: (item[0][0] if item[0][0] is not None else 99, item[0][1])):
        base = _base_groups(day_rows[0], groups)
        universe = set().union(*(set(memberships.get(str(group["id"]), ())) for group in base)) if base else set()
        block_key = f"{day_rows[0].cells[0].provenance.get('sheet_id')}|{day_rows[0].cells[0].provenance.get('tab_title')}|weekday-{weekday}|SDEP|{grade_scope}"
        source_cells = [
            {"coordinate": cell.source_cell, "raw_text": cell.raw_text, "source_day_label": cell.provenance.get("source_day_label")}
            for row in day_rows for cell in row.cells
        ]
        group_ids = [str(group["id"]) for group in base]
        blocks[block_key] = {
            "block_key": block_key, "weekday": weekday, "slot": None, "grade_scope": grade_scope,
            "status": "resolved", "interpretation_source": "agent_bootstrap_v1", "mode": "SDEP_DAY",
            "assignments": [_assignment_payload("SDEP", "day_special", group_ids, universe, (), teachers, ())],
            "unresolved": [], "source_warnings": [],
            "derived_from": {"sheet": day_rows[0].cells[0].provenance.get("tab_title"), "source_cells": source_cells, "structural_fingerprint": stable_fingerprint(source_cells)},
            "evidence": {"rule": "explicit source day marker; grades 10-11 only"},
        }
        for student_id in sorted(universe):
            student_routes[student_id].append({"block_key": block_key, "weekday": weekday, "start_time": None, "state": "ACTIVITY", "activity": "SDEP"})
    route_summary = Counter(item["state"] for values in student_routes.values() for item in values)
    duplicate_student_slots: list[dict[str, Any]] = []
    for student_id, routes in student_routes.items():
        slot_counts = Counter((route["weekday"], route["start_time"]) for route in routes)
        for (weekday, start_time), count in slot_counts.items():
            if count > 1:
                duplicate_student_slots.append({
                    "student_id": student_id,
                    "weekday": weekday,
                    "start_time": start_time,
                    "route_count": count,
                })
    students_without_routes = sorted(set(students) - set(student_routes))
    unresolved_student_ids = sorted({
        student_id
        for student_id, routes in student_routes.items()
        if any(route["state"] == "UNRESOLVED" for route in routes)
    } | set(students_without_routes))
    routing_validation = {
        "active_student_count": len(students),
        "students_with_routes": len(set(student_routes) & set(students)),
        "students_without_routes": students_without_routes,
        "duplicate_student_slots": duplicate_student_slots,
        "unresolved_student_ids": unresolved_student_ids,
        "exactly_one_effective_state_per_routed_slot": not duplicate_student_slots,
    }
    directory_blocker_blocks = sum(
        bool(item["issues"]) and all(str(issue.get("reason") or "").startswith("student is missing") for issue in item["issues"])
        for item in unresolved_blocks
    )
    semantic_conflict_blocks = len(unresolved_blocks) - directory_blocker_blocks
    snapshot = dict(corpus.get("snapshot") or {})
    identity = {"snapshot_id": snapshot.get("id"), "source_fingerprint": snapshot.get("fingerprint"), "blocks": blocks}
    return {
        "schema_version": "canonical-schedule-bootstrap-v1",
        "version_id": stable_fingerprint(identity),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "unresolved" if unresolved_blocks or students_without_routes or duplicate_student_slots else "validated",
        "authoritative": False,
        "source_snapshot": snapshot,
        "directory_fingerprint": stable_fingerprint({"groups": corpus.get("groups") or [], "memberships": corpus.get("memberships") or []}),
        "group_audit": audit_groups(corpus),
        "summary": {
            "source_cells": len(lessons),
            "logical_blocks": len(blocks),
            "resolved_blocks": sum(item["status"] == "resolved" for item in blocks.values()),
            "unresolved_blocks": len(unresolved_blocks),
            "directory_blocker_blocks": directory_blocker_blocks,
            "semantic_conflict_blocks": semantic_conflict_blocks,
            "grade9_blocks": len(ninth_rows),
            "sdep_day_blocks": len(sdep_rows),
            "source_warnings": warning_count,
            "student_route_states": dict(route_summary),
            "students_with_unresolved_routes": len(unresolved_student_ids),
            "fully_routable_students": len(students) - len(unresolved_student_ids),
        },
        "routing_validation": routing_validation,
        "unresolved": unresolved_blocks,
        "blocks": blocks,
        "students": {student_id: {"display_name": item.get("display_name"), "class_name": item.get("class_name"), "routes": student_routes.get(student_id, [])} for student_id, item in students.items()},
        "incremental_policy": {
            "unchanged_structural_fingerprint": "reuse canonical block",
            "teacher_room_note_only": "deterministic metadata patch",
            "audience_activity_merge_or_lane_change": "constrained AI-assisted patch against previous canonical block",
            "weekly_changes": "effective-week overlay; canonical template is immutable",
        },
    }
