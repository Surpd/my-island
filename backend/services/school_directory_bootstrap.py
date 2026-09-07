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


def parse_group_rosters(rows: Sequence[Sequence[Any]]) -> tuple[list[DirectoryGroup], list[DirectoryMembership], list[DirectoryIssue]]:
    """Parse obvious roster blocks; teacher labels are evidence, not identities."""
    groups: list[DirectoryGroup] = []
    memberships: list[DirectoryMembership] = []
    issues: list[DirectoryIssue] = []

    # Each block has subject row, class row, label row, then names in even columns.
    blocks = [(2, 3, 4, 17, "Математика"), (23, 24, 25, 41, "Английский язык"), (47, 48, 49, 66, "Обществознание"),
              (47, 48, 49, 66, "Литература"), (47, 48, 49, 66, "География"), (69, 70, 71, 79, "Биология"),
              (69, 70, 71, 79, "Физика"), (69, 70, 71, 79, "Химия"), (84, 85, 86, 98, "История"), (84, 85, 86, 98, "Информатика")]
    for subject_row, class_row, label_row, end_row, subject in blocks:
        # subject headings are sparse; use the columns whose heading matches this block.
        for col in range(1, max((len(r) for r in rows), default=0), 2):
            heading = _cell(rows, subject_row, col)
            if heading and heading.casefold() != subject.casefold():
                continue
            if not heading:
                # A block may have one heading only; stop at a blank run when the requested subject is not present.
                continue
            label = _cell(rows, label_row, col)
            class_label = _cell(rows, class_row, col)
            if not class_label:
                for previous in range(col - 2, -1, -2):
                    class_label = _cell(rows, class_row, previous)
                    if class_label:
                        break
            if not class_label and subject == "Математика" and re.match(r"^9-", label, re.I):
                class_label = "9 класс"
            if not class_label or not label:
                continue
            group_key = f"subject:{_subject_slug(subject)}:{class_label}:{label}"
            group_type = "subject_group" if "ОГЭ" not in label and "ЕГЭ" not in label else "exam_track"
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
            group_key = f"subject:{_subject_slug(subject)}:{class_label}:{label}"
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
