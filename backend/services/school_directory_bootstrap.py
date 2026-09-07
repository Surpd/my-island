"""Deterministic School Directory extraction and safety checks.

The Google connector supplies plain cell matrices; this module turns only
structurally obvious cells into candidates.  It deliberately does not resolve
teacher first names or infer homerooms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .school_data import stable_fingerprint
from .identity_reconciliation import IdentityCandidate, Resolution, SourceObservation, resolve_identity


def clean_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()
    return text


def name_without_note(value: Any) -> str:
    return clean_name(re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")))


def name_key(value: Any) -> str:
    return re.sub(r"[^\wа-яё]+", "", clean_name(value).casefold(), flags=re.IGNORECASE)


@dataclass(frozen=True)
class DirectoryPerson:
    display_name: str
    kind: str = "student"
    class_name: str | None = None
    source_ref: str = ""


@dataclass(frozen=True)
class DirectoryGroup:
    name: str
    group_type: str
    display_name: str
    subject: str | None = None
    base_class_name: str | None = None
    subject_subgroup: str | None = None
    exam_track: str | None = None
    source_ref: str = ""


@dataclass(frozen=True)
class DirectoryMembership:
    person: str
    group: str
    source_ref: str
    note: str = ""


@dataclass(frozen=True)
class DirectoryIssue:
    issue_type: str
    natural_key: str
    details: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class DirectorySelection:
    person: str
    grade: str
    subject: str
    source_ref: str
    selection_kind: str


@dataclass(frozen=True)
class ManifestItem:
    action: str
    natural_key: str
    payload: Mapping[str, Any]
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class BootstrapDiff:
    deleted_memberships: int
    changed_memberships: int
    deleted_people: int
    deleted_groups: int
    reasons: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)


def reconcile_source_person(
    source_name: str,
    canonical_people: Iterable[IdentityCandidate],
    *,
    class_name: str | None = None,
    source_ref: str = "",
    observations: Iterable[SourceObservation] = (),
) -> Resolution:
    """Shared apply-layer entry point; parsers never create canonical people."""
    return resolve_identity(source_name, canonical_people, class_name=class_name, source_ref=source_ref, observations=observations)


def _cell(rows: Sequence[Sequence[Any]], row: int, col: int) -> str:
    if row >= len(rows) or col >= len(rows[row]):
        return ""
    return clean_name(rows[row][col])


def parse_class_lists(rows: Sequence[Sequence[Any]]) -> tuple[list[DirectoryPerson], list[DirectoryGroup], list[DirectoryMembership]]:
    """Parse the paired number/name columns of the current class list tab."""
    pairs = ((0, "5"), (2, "6"), (4, "7"), (6, "8"), (8, "9-Д"), (10, "9-А"), (12, "10"), (15, "11"))
    people: dict[str, DirectoryPerson] = {}
    groups: list[DirectoryGroup] = []
    memberships: list[DirectoryMembership] = []
    for col, grade in pairs:
        group_key = f"class:{grade}"
        groups.append(DirectoryGroup(group_key, "class", f"{grade} · базовый класс", base_class_name=grade, source_ref=f"Списки по классам 26/27!{chr(65 + col + 1)}2:{chr(65 + col + 1)}100"))
        for row in range(1, len(rows)):
            value = name_without_note(_cell(rows, row, col + 1))
            if not value or value.lower() in {"false", "true"} or value.isdigit():
                continue
            key = name_key(value)
            person = people.get(key)
            if person is None:
                people[key] = DirectoryPerson(value, class_name=grade, source_ref=f"Списки по классам 26/27!{chr(65 + col + 1)}{row + 1}")
            memberships.append(DirectoryMembership(value, group_key, f"Списки по классам 26/27!{chr(65 + col + 1)}{row + 1}"))
    return list(people.values()), groups, memberships


def _subject_slug(subject: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "-", subject.casefold(), flags=re.IGNORECASE).strip("-")


def carry_forward_cells(row: Sequence[Any], columns: Sequence[int]) -> dict[int, str]:
    """Expand display values across adjacent columns covered by merged headers."""
    current = ""
    result: dict[int, str] = {}
    for col in columns:
        value = clean_name(row[col] if col < len(row) else "")
        if value:
            current = value
        result[col] = current
    return result


def english_group_key(label: str) -> str:
    match = re.match(r"^(\d+)\b", clean_name(label))
    if not match:
        raise ValueError("English group label must start with its global number")
    return f"english:{match.group(1)}"


def literal_true(value: Any) -> bool:
    return clean_name(value).casefold() == "true"


def parse_exam_selections(rows: Sequence[Sequence[Any]]) -> tuple[list[DirectorySelection], list[DirectoryIssue]]:
    """Extract only literal TRUE checkbox facts; text remains reviewable metadata."""
    selections: list[DirectorySelection] = []
    issues: list[DirectoryIssue] = []
    blocks = (("9", 1, 34), ("10", 35, 47), ("11", 47, len(rows)))
    for grade, title_row, end_row in blocks:
        headers = rows[title_row + 1] if title_row + 1 < len(rows) else ()
        for row in range(title_row + 2, min(end_row, len(rows))):
            person = _cell(rows, row, 1)
            if not person:
                continue
            for col in range(2, len(headers)):
                subject = _cell(rows, title_row + 1, col)
                if not subject:
                    continue
                value = _cell(rows, row, col)
                ref = f"ОГЭ/ЕГЭ!{chr(65 + col)}{row + 1}"
                if literal_true(value):
                    selections.append(DirectorySelection(person, grade, subject, ref, "oge" if grade == "9" else "ege_profile"))
                elif value and value.casefold() != "false":
                    issues.append(DirectoryIssue(
                        "non_boolean_selection_value",
                        f"selection:{grade}:{name_key(person)}:{_subject_slug(subject)}",
                        f"'{value}' не является literal TRUE и не импортируется как выбор",
                        (ref,),
                    ))
    return selections, issues


def parse_group_rosters(rows: Sequence[Sequence[Any]]) -> tuple[list[DirectoryGroup], list[DirectoryMembership], list[DirectoryIssue]]:
    """Parse obvious roster blocks; teacher labels are evidence, not identities."""
    groups: list[DirectoryGroup] = []
    memberships: list[DirectoryMembership] = []
    issues: list[DirectoryIssue] = []

    # Sparse/merged headings apply to adjacent subgroup columns until the next value.
    width = max((len(r) for r in rows), default=0)
    columns = tuple(range(1, width, 2))
    blocks = ((47, 48, 49, 66), (69, 70, 71, 79), (84, 85, 86, 98))
    for subject_row, class_row, label_row, end_row in blocks:
        subjects = carry_forward_cells(rows[subject_row] if subject_row < len(rows) else (), columns)
        classes = carry_forward_cells(rows[class_row] if class_row < len(rows) else (), columns)
        for col in columns:
            subject = subjects[col]
            label = _cell(rows, label_row, col)
            class_label = classes[col]
            if not subject or not class_label or not label:
                continue
            group_key = f"subject:{_subject_slug(subject)}:{class_label}:{label}"
            # This sheet is an instructional roster. OGE/EGE in the label
            # describes the class taught, not a student's selection fact.
            group_type = "subject_group"
            groups.append(DirectoryGroup(group_key, group_type, f"{subject} · {class_label} · {label}", subject=subject, base_class_name=class_label.replace(" класс", ""), subject_subgroup=label, exam_track=("ОГЭ" if "ОГЭ" in label else "ЕГЭ" if "ЕГЭ" in label else None), source_ref=f"списки групп 26-27!{chr(65 + col)}{label_row + 1}"))
            for row in range(label_row + 1, min(end_row, len(rows))):
                person = _cell(rows, row, col)
                if not person:
                    continue
                memberships.append(DirectoryMembership(person, group_key, f"списки групп 26-27!{chr(65 + col)}{row + 1}"))
    # Math and English have multiple adjacent columns under a shared heading.
    for subject, header_row, label_row, cols in (("Математика", 3, 4, (1,3,5,7,9,11,13,15,17,19,21)), ("Английский язык",24,25,(1,3,5,7,9,11,13,15,17,19))):
        for col in cols:
            class_label, label = _cell(rows, header_row, col), _cell(rows, label_row, col)
            if not class_label:
                for previous in reversed([candidate for candidate in cols if candidate < col]):
                    class_label = _cell(rows, header_row, previous)
                    if class_label:
                        break
            if not class_label and subject == "Математика" and re.match(r"^9-", label, re.I):
                class_label = "9 класс"
            if not class_label or not label:
                continue
            group_key = english_group_key(label) if subject == "Английский язык" else f"subject:{_subject_slug(subject)}:{class_label}:{label}"
            subgroup_match = re.match(r"^9-([ABC])", label, re.IGNORECASE)
            subgroup = subgroup_match.group(1).upper() if subgroup_match else (label.split(" ", 1)[0] if label.split(" ", 1)[0].isdigit() or label.split(" ", 1)[0].upper() in {"A", "B", "C"} else label)
            groups.append(DirectoryGroup(group_key, "subject_group", f"{subject} · {class_label} · {label}", subject=subject, base_class_name=class_label.replace(" класс", ""), subject_subgroup=subgroup, source_ref=f"списки групп 26-27!{chr(65 + col)}{label_row + 1}"))
            for row in range(label_row + 1, min(41 if subject == "Английский язык" else 17, len(rows))):
                person = _cell(rows, row, col)
                if person:
                    memberships.append(DirectoryMembership(person, group_key, f"списки групп 26-27!{chr(65 + col)}{row + 1}"))
            # The header contains only a first name (or initials), so do not map it to a teacher.
            teacher_hint = re.sub(r"^[0-9]+\s*", "", label).strip()
            if teacher_hint:
                issues.append(DirectoryIssue("ambiguous_teacher_header", group_key, f"Источник даёт только teacher hint '{teacher_hint}', недостаточно для canonical identity/assignment", (f"списки групп 26-27!{chr(65 + col)}{label_row + 1}",)))
    return groups, memberships, issues


def assess_bulk_change(previous: Mapping[str, int], current: Mapping[str, int]) -> BootstrapDiff:
    deleted_memberships = max(0, previous.get("memberships", 0) - current.get("memberships", 0))
    changed_memberships = abs(current.get("memberships", 0) - previous.get("memberships", 0))
    deleted_people = max(0, previous.get("students", 0) - current.get("students", 0))
    deleted_groups = max(0, previous.get("groups", 0) - current.get("groups", 0))
    reasons: list[str] = []
    if previous.get("memberships", 0) >= 20 and deleted_memberships >= max(10, previous["memberships"] // 3):
        reasons.append(f"Рабочие группы: обнаружено аномальное массовое изменение, затронуто {deleted_memberships} memberships.")
    if previous.get("students", 0) >= 20 and deleted_people >= max(10, previous["students"] // 4):
        reasons.append(f"Списки учеников: исчезло {deleted_people} записей, автоматическое применение остановлено.")
    if previous.get("groups", 0) >= 8 and deleted_groups >= max(4, previous["groups"] // 3):
        reasons.append(f"Структура школы: исчезло {deleted_groups} групп, автоматическое применение остановлено.")
    return BootstrapDiff(deleted_memberships, changed_memberships, deleted_people, deleted_groups, tuple(reasons))


def candidate_fingerprint(entity_type: str, natural_key: str, payload: Mapping[str, Any]) -> str:
    return stable_fingerprint({"entity_type": entity_type, "natural_key": natural_key, "payload": dict(payload)})


def deduplicate_manifest(items: Iterable[ManifestItem]) -> list[ManifestItem]:
    """Keep one logical action per natural key while retaining all provenance."""
    result: dict[tuple[str, str], ManifestItem] = {}
    for item in items:
        key = (item.action, item.natural_key)
        previous = result.get(key)
        if previous is None:
            result[key] = item
            continue
        refs = sorted(set(previous.evidence.get("source_refs", ())) | set(item.evidence.get("source_refs", ())))
        result[key] = ManifestItem(item.action, item.natural_key, item.payload, {**previous.evidence, **item.evidence, "source_refs": refs})
    return sorted(result.values(), key=lambda item: (item.action, item.natural_key))


def render_manifest_examples(items: Iterable[ManifestItem]) -> str:
    """Render from manifest objects so examples cannot diverge from computed actions."""
    rows = ["| Action | Natural key | Evidence |", "|---|---|---|"]
    for item in deduplicate_manifest(items):
        refs = ", ".join(str(ref) for ref in item.evidence.get("source_refs", ())) or "—"
        rows.append(f"| {item.action} | {item.natural_key} | {refs} |")
    return "\n".join(rows)
