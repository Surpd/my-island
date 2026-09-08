"""Read-only reconciliation planner for the structured teacher source.

The ``Учителя и группы — данные`` tab is an assignment source.  This module
normalizes its rows, resolves only existing canonical teachers/subjects/groups,
and returns a machine-readable plan.  It deliberately has no persistence
calls: the caller may inspect the plan, but it cannot mutate memberships or
teacher assignments by accident.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .teacher_directory import TEACHER_ALIASES, SUBJECT_ALIASES, _clean, _key, canonical_teacher_name


SHEET_NAME = "Учителя и группы — данные"
WHOLE_CLASS = "whole_class"
COMPUTED_AUDIENCE = "computed_schedule_audience"


@dataclass(frozen=True)
class StructuredTeacherRow:
    row_number: int
    raw_teacher: str
    raw_subject: str
    raw_grade: str
    raw_audience: str
    raw_shared_lesson: str
    raw_note: str
    teacher_name: str
    subject: str
    grades: tuple[str, ...]
    audience_kind: str
    audience_value: str
    tags: tuple[str, ...]

    @property
    def source_ref(self) -> str:
        return f"{SHEET_NAME}!A{self.row_number}:F{self.row_number}"


@dataclass(frozen=True)
class ReconciliationAssignment:
    teacher_id: str
    teacher_name: str
    subject: str
    group_id: str
    group_name: str
    base_class_name: str | None
    subject_subgroup: str | None
    exam_track: str | None
    source_ref: str
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def natural_key(self) -> tuple[str, str, str]:
        return self.teacher_id, self.group_id, self.subject


@dataclass(frozen=True)
class ReconciliationIssue:
    row_number: int | None
    source_ref: str
    raw_values: Mapping[str, Any]
    normalized_values: Mapping[str, Any]
    candidates: tuple[str, ...]
    reason: str
    needs_confirmation: str
    kind: str = "unresolved"


@dataclass
class TeacherReconciliationPlan:
    rows: list[StructuredTeacherRow] = field(default_factory=list)
    assignments: list[ReconciliationAssignment] = field(default_factory=list)
    issues: list[ReconciliationIssue] = field(default_factory=list)
    computed_schedule_audiences: list[dict[str, Any]] = field(default_factory=list)
    skipped_blank_rows: list[int] = field(default_factory=list)
    current_assignments: list[Mapping[str, Any]] = field(default_factory=list)
    stale_assignments: list[Mapping[str, Any]] = field(default_factory=list)
    manual_protected_assignments: list[Mapping[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "rows": [asdict(row) for row in self.rows],
            "assignments": [asdict(item) for item in self.assignments],
            "issues": [asdict(item) for item in self.issues],
            "computed_schedule_audiences": self.computed_schedule_audiences,
            "skipped_blank_rows": self.skipped_blank_rows,
            "current_assignments": [dict(item) for item in self.current_assignments],
            "stale_assignments": [dict(item) for item in self.stale_assignments],
            "manual_protected_assignments": [dict(item) for item in self.manual_protected_assignments],
        }


def _value(raw: Sequence[Any], index: int) -> str:
    return _clean(raw[index] if index < len(raw) else "")


def normalize_grade(value: Any) -> str:
    token = _clean(value).replace("–", "-").replace("—", "-")
    token = re.sub(r"\s*-\s*", "-", token)
    if re.fullmatch(r"9\s*[-/]\s*[аa]", token, re.IGNORECASE):
        return "9-А"
    if re.fullmatch(r"9\s*[-/]\s*[дd]", token, re.IGNORECASE):
        return "9-Д"
    return token


def _grade_tokens(value: str) -> tuple[str, ...]:
    text = normalize_grade(value)
    if not text:
        return ()
    found: list[str] = []
    for start, end in re.findall(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})(?!\d)", text):
        found.extend(str(number) for number in range(int(start), int(end) + 1))
    if not found:
        found = [text]
    return tuple(dict.fromkeys(found))


def normalize_audience(value: Any) -> tuple[str, str, tuple[str, ...]]:
    text = _clean(value)
    lower = _key(text)
    tags: list[str] = []
    if "база" in lower and ("доп" in lower or "+" in lower):
        tags.append("base_plus_additional")
    if "отдельн" in lower:
        tags.append("separate")
    if "по расписанию" in lower:
        tags.append("schedule_context")
    if "огэ" in lower:
        tags.append("oge_context")
    if "егэ" in lower:
        tags.append("ege_context")
    if "весь класс" in lower:
        return WHOLE_CLASS, "", tuple(dict.fromkeys(tags))
    if "дополнительная группа" in lower or lower in {"доп", "дополнительная"}:
        return COMPUTED_AUDIENCE, "grade_without_society_ege", tuple(dict.fromkeys(tags))
    match = re.search(r"групп[аы]?\s*([0-9]+|[a-cа-в])", lower, re.IGNORECASE)
    if match:
        number = match.group(1)
        letter = {"а": "A", "б": "B", "в": "C"}.get(number, number.upper())
        return "group", letter, tuple(dict.fromkeys(tags))
    if re.search(r"\bбаза\b", lower):
        return "base", "", tuple(dict.fromkeys(tags))
    if re.search(r"\bогэ\b", lower):
        return "oge", "", tuple(dict.fromkeys(tags))
    if re.search(r"\bегэ\b", lower):
        return "ege", "", tuple(dict.fromkeys(tags))
    return "unknown", text, tuple(dict.fromkeys(tags))


def parse_structured_teacher_rows(values: Sequence[Sequence[Any]]) -> tuple[list[StructuredTeacherRow], list[ReconciliationIssue], list[int]]:
    if not values:
        raise ValueError(f"{SHEET_NAME}: пустой лист")
    headers = [_key(value) for value in values[0]]
    required = ("учитель", "предмет", "класс", "группа / аудитория")
    if any(item not in headers for item in required):
        raise ValueError(f"{SHEET_NAME}: ожидаются колонки Учитель, Предмет, Класс, Группа / аудитория")
    indexes = {name: headers.index(name) for name in required}
    optional = {
        "совместный урок": headers.index("совместный урок") if "совместный урок" in headers else 4,
        "примечание": headers.index("примечание") if "примечание" in headers else 5,
    }
    rows: list[StructuredTeacherRow] = []
    issues: list[ReconciliationIssue] = []
    blanks: list[int] = []
    for row_number, raw in enumerate(values[1:], start=2):
        teacher = _value(raw, indexes["учитель"])
        subject = _value(raw, indexes["предмет"])
        grade = _value(raw, indexes["класс"])
        audience = _value(raw, indexes["группа / аудитория"])
        shared = _value(raw, optional["совместный урок"])
        note = _value(raw, optional["примечание"])
        if not any((teacher, subject, grade, audience, shared, note)):
            blanks.append(row_number)
            continue
        normalized_subjects = tuple(SUBJECT_ALIASES.get(_key(token), "") for token in re.split(r"[,;/]+", subject) if _clean(token))
        normalized_subjects = tuple(item for item in normalized_subjects if item)
        audience_kind, audience_value, tags = normalize_audience(audience)
        note_lower = _key(note)
        merged_tags = list(tags)
        shared_lower = _key(shared)
        if ("база" in note_lower and ("доп" in note_lower or "+" in note_lower)) or ("база" in shared_lower and ("доп" in shared_lower or "+" in shared_lower)):
            merged_tags.append("base_plus_additional")
        if "по расписанию" in note_lower:
            merged_tags.append("schedule_context")
        if "занимаются вместе" in _key(shared) or "занимаются вместе" in note_lower:
            merged_tags.append("shared_lesson")
        tags = tuple(dict.fromkeys(merged_tags))
        normalized = {
            "teacher": canonical_teacher_name(teacher),
            "subject": normalized_subjects,
            "grades": _grade_tokens(grade),
            "audience_kind": audience_kind,
            "audience_value": audience_value,
        }
        grade_required = not (_key(normalized_subjects[0] if normalized_subjects else subject) == _key("Английский язык") and audience_kind == "group")
        if not teacher or not subject or (grade_required and not grade) or not audience:
            issues.append(ReconciliationIssue(row_number, f"{SHEET_NAME}!A{row_number}:F{row_number}", {"teacher": teacher, "subject": subject, "grade": grade, "audience": audience, "shared_lesson": shared, "note": note}, normalized, (), "неполная строка источника", "уточнить все обязательные поля", "conflict"))
            continue
        if not normalized_subjects:
            issues.append(ReconciliationIssue(row_number, f"{SHEET_NAME}!A{row_number}:F{row_number}", {"teacher": teacher, "subject": subject, "grade": grade, "audience": audience, "shared_lesson": shared, "note": note}, normalized, (), "предмет не распознан через canonical subject aliases", "подтвердить canonical subject", "unmatched_subject"))
            continue
        rows.append(StructuredTeacherRow(row_number, teacher, subject, grade, audience, shared, note, normalized["teacher"], normalized_subjects[0], normalized["grades"], audience_kind, audience_value, tags))
    return rows, issues, blanks


def _group_candidates(groups: Mapping[str, Mapping[str, Any]], subject: str, grade: str, marker: str) -> list[Mapping[str, Any]]:
    result = []
    for group in groups.values():
        if _key(group.get("subject")) != _key(subject) or _clean(group.get("base_class_name")) != grade:
            continue
        if marker == "oge" and _key(group.get("exam_track")) == "огэ":
            result.append(group)
        elif marker == "ege" and _key(group.get("exam_track")) == "егэ":
            result.append(group)
        elif marker == "base" and _key(group.get("subject_subgroup")) == "база":
            result.append(group)
    # Current canonical society profile groups are explicitly the EGE branch
    # even though their persisted subgroup label is ``Угл``.
    if marker == "ege" and not result and subject == "Обществознание":
        result = [group for group in groups.values() if _key(group.get("subject")) == _key(subject) and _clean(group.get("base_class_name")) == grade and _key(group.get("subject_subgroup")) == "угл"]
    return result


def _class_names(grade: str) -> tuple[str, ...]:
    if grade == "9":
        return "9-А", "9-Д"
    if grade == "7":
        return "7-1", "7-2"
    return (grade,)


def _whole_class_groups(groups: Mapping[str, Mapping[str, Any]], grade: str) -> list[Mapping[str, Any]]:
    result = []
    for name in _class_names(grade):
        group = groups.get(name)
        if group and group.get("group_type") == "class" and group.get("canonical"):
            result.append(group)
    return result


def _assignment_from_group(row: StructuredTeacherRow, teacher_id: str, group: Mapping[str, Any], *, context: Mapping[str, Any] | None = None) -> ReconciliationAssignment:
    return ReconciliationAssignment(teacher_id, row.teacher_name, row.subject, str(group["id"]), str(group["name"]), group.get("base_class_name"), group.get("subject_subgroup"), group.get("exam_track"), row.source_ref, context or {})


def _issue(row: StructuredTeacherRow, reason: str, candidates: Iterable[str] = (), *, kind: str = "unresolved", confirmation: str = "подтвердить целевую canonical audience") -> ReconciliationIssue:
    return ReconciliationIssue(row.row_number, row.source_ref, {"teacher": row.raw_teacher, "subject": row.raw_subject, "grade": row.raw_grade, "audience": row.raw_audience, "shared_lesson": row.raw_shared_lesson, "note": row.raw_note}, {"teacher": row.teacher_name, "subject": row.subject, "grades": row.grades, "audience_kind": row.audience_kind, "audience_value": row.audience_value, "tags": row.tags}, tuple(candidates), reason, confirmation, kind)


def _row_assignments(row: StructuredTeacherRow, teacher_id: str, groups: Mapping[str, Mapping[str, Any]]) -> tuple[list[ReconciliationAssignment], list[dict[str, Any]], list[ReconciliationIssue]]:
    assignments: list[ReconciliationAssignment] = []
    computed: list[dict[str, Any]] = []
    issues: list[ReconciliationIssue] = []
    if row.audience_kind == COMPUTED_AUDIENCE:
        computed.append({"row_number": row.row_number, "source_ref": row.source_ref, "teacher": row.teacher_name, "subject": row.subject, "rule": "grade 11 students without Society EGE join Geography 10", "raw_note": row.raw_note})
        return assignments, computed, issues
    if row.audience_kind == "group":
        if row.subject == "Английский язык":
            group = groups.get(f"english:{row.audience_value}")
            if group:
                assignments.append(_assignment_from_group(row, teacher_id, group, context={"english_group": row.audience_value, "exam_marker": "oge" if "оге_context" in row.tags else "ege" if "ege_context" in row.tags else None}))
            else:
                issues.append(_issue(row, f"английская группа {row.audience_value} отсутствует среди canonical groups", (f"english:{row.audience_value}",)))
            return assignments, computed, issues
        if row.subject == "Математика" and "9" in row.grades:
            group = groups.get(f"grade9-math-{row.audience_value}")
            if group:
                assignments.append(_assignment_from_group(row, teacher_id, group))
            else:
                issues.append(_issue(row, "Math 9 subgroup отсутствует среди canonical groups", (f"grade9-math-{row.audience_value}",)))
            return assignments, computed, issues
        issues.append(_issue(row, "группа указана, но для этого предмета нет явного structural mapping", kind="conflict"))
        return assignments, computed, issues
    for grade in row.grades:
        if row.audience_kind == WHOLE_CLASS:
            found = _whole_class_groups(groups, grade)
            if not found:
                issues.append(_issue(row, f"canonical class для {grade} не найден", _class_names(grade)))
            assignments.extend(_assignment_from_group(row, teacher_id, group) for group in found)
            continue
        if row.audience_kind in {"oge", "ege", "base"}:
            if row.subject == "Математика" and grade == "11":
                group = groups.get("math:11:advanced" if row.audience_kind == "ege" else "math:11:base")
                if group:
                    assignments.append(_assignment_from_group(row, teacher_id, group, context={"exam_marker": row.audience_kind}))
                else:
                    issues.append(_issue(row, f"Math 11 {row.audience_kind} canonical group отсутствует"))
            else:
                candidates = _group_candidates(groups, row.subject, grade, row.audience_kind)
                if len(candidates) == 1:
                    assignments.append(_assignment_from_group(row, teacher_id, candidates[0], context={"exam_marker": row.audience_kind}))
                elif not candidates:
                    issues.append(_issue(row, f"canonical {row.audience_kind.upper()} / base group для {grade} не найден", (f"{row.subject}:{grade}:{row.audience_kind}",)))
                else:
                    issues.append(_issue(row, "несколько canonical audience подходят одинаково", tuple(str(item["name"]) for item in candidates), kind="conflict"))
            if row.audience_kind in {"oge", "ege"} and "base_plus_additional" in row.tags:
                base_candidates = _group_candidates(groups, row.subject, grade, "base")
                if base_candidates:
                    assignments.extend(_assignment_from_group(row, teacher_id, group, context={"base_plus_additional": True}) for group in base_candidates)
                elif row.audience_kind == "oge":
                    class_groups = _whole_class_groups(groups, grade)
                    assignments.extend(_assignment_from_group(row, teacher_id, group, context={"base_plus_additional": True, "base_fallback": "class"}) for group in class_groups)
            continue
        issues.append(_issue(row, "аудитория не распознана явным structural rule", kind="conflict", confirmation="указать canonical audience или явное правило маршрутизации"))
    return assignments, computed, issues


def _merge_assignment_context(previous: ReconciliationAssignment, current: ReconciliationAssignment) -> tuple[ReconciliationAssignment, bool]:
    """Merge compatible duplicate source rows without inventing a conflict."""
    merged = dict(previous.context)
    conflict = False
    for key, value in current.context.items():
        if key in merged and merged[key] not in (None, value) and value not in (None, merged[key]):
            conflict = True
        elif value not in (None, False, ""):
            merged[key] = value
    refs = list(merged.get("source_refs", ()))
    for ref in (previous.source_ref, current.source_ref):
        if ref not in refs:
            refs.append(ref)
    if len(refs) > 1:
        merged["source_refs"] = refs
    return ReconciliationAssignment(previous.teacher_id, previous.teacher_name, previous.subject, previous.group_id, previous.group_name, previous.base_class_name, previous.subject_subgroup, previous.exam_track, previous.source_ref, merged), conflict


def build_teacher_reconciliation_plan(
    values: Sequence[Sequence[Any]],
    *,
    canonical_teachers: Sequence[Mapping[str, Any]],
    canonical_groups: Mapping[str, Mapping[str, Any]],
    canonical_subjects: Iterable[str],
    current_assignments: Sequence[Mapping[str, Any]],
) -> TeacherReconciliationPlan:
    rows, parse_issues, blanks = parse_structured_teacher_rows(values)
    teacher_index: dict[str, list[Mapping[str, Any]]] = {}
    for identity in canonical_teachers:
        teacher_index.setdefault(_key(identity.get("display_name")), []).append(identity)
    subjects = {_key(item) for item in canonical_subjects}
    plan = TeacherReconciliationPlan(rows=rows, skipped_blank_rows=blanks, current_assignments=list(current_assignments), issues=list(parse_issues))
    desired: dict[tuple[str, str, str], ReconciliationAssignment] = {}
    for row in rows:
        teacher_candidates = teacher_index.get(_key(row.teacher_name), [])
        if not teacher_candidates:
            plan.issues.append(_issue(row, "teacher не найден среди существующих canonical identities/explicit aliases", tuple(str(item.get("display_name")) for item in teacher_index.get(_key(row.raw_teacher), [])), kind="unmatched_teacher", confirmation="подтвердить identity mapping; fuzzy merge запрещён"))
            continue
        if len(teacher_candidates) > 1:
            plan.issues.append(_issue(row, "несколько canonical teacher identities совпали точно", tuple(str(item.get("display_name")) for item in teacher_candidates), kind="conflict", confirmation="выбрать одну canonical identity"))
            continue
        subject = row.subject
        if _key(subject) not in subjects:
            plan.issues.append(_issue(row, "subject отсутствует среди canonical subjects", (), kind="unmatched_subject", confirmation="добавить явное subject mapping; новый subject автоматически не создаём"))
            continue
        row_assignments, computed, row_issues = _row_assignments(row, str(teacher_candidates[0]["id"]), canonical_groups)
        plan.computed_schedule_audiences.extend(computed)
        plan.issues.extend(row_issues)
        for assignment in row_assignments:
            if assignment.natural_key in desired:
                previous = desired[assignment.natural_key]
                merged, conflicting_context = _merge_assignment_context(previous, assignment)
                desired[assignment.natural_key] = merged
                if conflicting_context:
                    plan.issues.append(_issue(row, "одна logical assignment пришла из нескольких строк с разным контекстом", (previous.source_ref, assignment.source_ref), kind="conflict", confirmation="проверить, является ли это один assignment с объединённым контекстом"))
                continue
            desired[assignment.natural_key] = assignment
    plan.assignments = sorted(desired.values(), key=lambda item: (item.teacher_name, item.group_name, item.subject))
    current_by_key: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for item in current_assignments:
        current_by_key.setdefault((str(item.get("teacher_id")), str(item.get("group_id")), str(item.get("subject"))), []).append(item)
    desired_keys = set(desired)
    current_keys = set(current_by_key)
    stale: list[Mapping[str, Any]] = []
    manual_missing: list[Mapping[str, Any]] = []
    unresolved_scope = {
        (_key(issue.normalized_values.get("teacher")), _key(issue.normalized_values.get("subject")), str(grade))
        for issue in plan.issues
        if issue.kind in {"unresolved", "conflict"}
        for grade in issue.normalized_values.get("grades", ())
    }
    for key, items in current_by_key.items():
        if key in desired_keys:
            continue
        for item in items:
            source = str(item.get("source", ""))
            source_ref = str(item.get("source_ref", ""))
            if source == "official_import" or source_ref.startswith("Учителя и группы!"):
                scope = (_key(item.get("teacher_name")), _key(item.get("subject")), _clean(item.get("base_class_name")))
                if scope in unresolved_scope:
                    manual_missing.append({**dict(item), "deactivation_guard": "source has unresolved audience for the same teacher/subject/grade"})
                else:
                    stale.append(item)
            elif source == "manual_confirmation" or source == "admin_override":
                manual_missing.append(item)
    plan.stale_assignments = stale
    plan.manual_protected_assignments = manual_missing
    audience_issue_count = sum(1 for issue in plan.issues if issue.kind in {"unresolved", "conflict"})
    counts = {
        "rows_read": max(0, len(values) - 1),
        "normalized_rows": len(rows),
        "matched_teacher": sum(1 for row in rows if any(_key(identity.get("display_name")) == _key(row.teacher_name) for identity in canonical_teachers)),
        "unmatched_teacher": sum(1 for issue in plan.issues if issue.kind == "unmatched_teacher"),
        "matched_subject": sum(1 for row in rows if _key(row.subject) in subjects),
        "unmatched_subject": sum(1 for issue in plan.issues if issue.kind == "unmatched_subject"),
        "matched_audience": max(0, len(rows) - audience_issue_count),
        "unresolved_audience": sum(1 for issue in plan.issues if issue.kind in {"unresolved", "conflict"}),
        "proposed_create_assignments": len(desired_keys - current_keys),
        "proposed_update_assignments": 0,
        "proposed_deactivate_assignments": len(stale),
        "manual_assignments_needing_confirmation": len(manual_missing),
        "unchanged_assignments": len(desired_keys & current_keys),
        "computed_schedule_audiences": len(plan.computed_schedule_audiences),
        "conflicts": sum(1 for issue in plan.issues if issue.kind == "conflict"),
        "skipped_blank_rows": len(blanks),
        "student_memberships_touched": 0,
        "production_apply_performed": 0,
    }
    plan.counts = counts
    return plan


def render_teacher_reconciliation_report(plan: TeacherReconciliationPlan, *, spreadsheet_id: str, spreadsheet_title: str) -> str:
    """Render a stable human report from the same structured plan as JSON."""
    lines = [
        "# Teacher Directory dry-run reconciliation",
        "",
        f"Source: `{spreadsheet_title}` → `{SHEET_NAME}` (`{spreadsheet_id}`)",
        "Mode: read-only; no teacher assignments or student memberships were changed.",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
    ]
    for key, value in plan.counts.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Proposed assignment changes", "", "| Teacher | Subject | Canonical audience | Source row | Context |", "| --- | --- | --- | --- | --- |"])
    for item in plan.assignments:
        lines.append(f"| {item.teacher_name} | {item.subject} | {item.group_name} | {item.source_ref} | {dict(item.context) or '—'} |")
    lines.extend(["", "## Existing assignments absent from the structured source", "", "| Teacher | Subject | Current audience | Source | Source row |", "| --- | --- | --- | --- | --- |"])
    for item in plan.stale_assignments:
        lines.append(f"| {item.get('teacher_name', '—')} | {item.get('subject', '—')} | {item.get('group_name', '—')} | {item.get('source', '—')} | {item.get('source_ref', '—')} |")
    if not plan.stale_assignments:
        lines.append("None.")
    if plan.manual_protected_assignments:
        lines.extend(["", "Assignments protected from automatic deactivation because the same source relationship is unresolved:", "", "| Teacher | Subject | Current audience | Source | Source row | Guard |", "| --- | --- | --- | --- | --- | --- |"])
        for item in plan.manual_protected_assignments:
            lines.append(f"| {item.get('teacher_name', '—')} | {item.get('subject', '—')} | {item.get('group_name', '—')} | {item.get('source', '—')} | {item.get('source_ref', '—')} | {item.get('deactivation_guard', '—')} |")
    lines.extend(["", "## Computed schedule audiences", ""])
    if plan.computed_schedule_audiences:
        lines.extend(["| Source row | Teacher | Subject | Rule |", "| --- | --- | --- | --- |"])
        for item in plan.computed_schedule_audiences:
            lines.append(f"| {item['source_ref']} | {item['teacher']} | {item['subject']} | {item['rule']} |")
    else:
        lines.append("None.")
    lines.extend(["", "## Unresolved / conflicts", "", "| Row | Raw source | Normalized | Candidates | Why not applied | Needed confirmation |", "| ---: | --- | --- | --- | --- | --- |"])
    for issue in plan.issues:
        raw = "; ".join(f"{key}={value!r}" for key, value in issue.raw_values.items() if value not in ("", None))
        normalized = "; ".join(f"{key}={value!r}" for key, value in issue.normalized_values.items() if value not in ("", (), None))
        lines.append(f"| {issue.row_number or '—'} | {raw} | {normalized} | {', '.join(issue.candidates) or '—'} | {issue.reason} | {issue.needs_confirmation} |")
    lines.extend(["", "## Safety", "", "- Student memberships touched: **0**.", "- Production teacher assignments applied: **0**.", "- No canonical teacher, subject, or group is created by this dry-run."])
    return "\n".join(lines) + "\n"
