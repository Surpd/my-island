"""Display labels from journal headers, separate from persistent roster identity."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

TERM_PREFIX = re.compile(r"I{1,3}\s+триместр\s*·\s*(.+)", re.IGNORECASE)
MATH_LABEL = re.compile(r"9\s*[-–]?\s*([ABCАВС])", re.IGNORECASE)
GROUP_LABEL = re.compile(r"группа\s+(?:№\s*)?([1-9][0-9]?|[A-ZА-ЯЁ])", re.IGNORECASE)
ENGLISH_SUBGROUP = re.compile(
    r"английский(?:\s+язык)?\s+([1-9][0-9]?)(?:\s*·\s*[А-ЯЁ][А-ЯЁ-]*(?:\s+[А-ЯЁ][А-ЯЁ-]*)?)?", re.IGNORECASE)
STORED_LABEL = re.compile(r"9[ABC]|(?:Группа (?:[1-9][0-9]?|[A-ZА-ЯЁ]))|Английский [1-9][0-9]?")
CYRILLIC_LOOKALIKES = str.maketrans("АВС", "ABC")


class JournalGroupLabelConflict(ValueError):
    """The source argument cannot identify the same saved journal groups."""


def explicit_header_label(value: object, *, grade: int, subject: str) -> str | None:
    """Read only explicit short labels from the gradebook's header cell."""
    text = str(value or "").strip()
    term = TERM_PREFIX.fullmatch(text)
    label = term[1].strip() if term else text
    named = GROUP_LABEL.fullmatch(label)
    if named:
        return "Группа " + named[1].upper()
    normalized_subject = " ".join(subject.casefold().split())
    if normalized_subject in {"английский", "английский язык", "english"}:
        english = ENGLISH_SUBGROUP.fullmatch(label)
        if english:
            # Ignore an optional teacher suffix; it is not the subgroup name.
            return "Английский " + english[1]
    if not term or grade != 9 or normalized_subject not in {"математика", "math"}:
        return None
    maths = MATH_LABEL.fullmatch(label)
    return "9" + maths[1].upper().translate(CYRILLIC_LOOKALIKES) if maths else None


def journal_group_labels(assessment_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    evidence: dict[str, set[str | None]] = defaultdict(set)
    for row in assessment_rows:
        raw = row["raw_source"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        label = raw.get("source_group_label") if isinstance(raw, dict) else None
        evidence[str(row["group_marker"])].add(label if isinstance(label, str) and STORED_LABEL.fullmatch(label) else None)
    labels = {key: next(iter(values)) for key, values in evidence.items() if len(values) == 1 and None not in values}
    return {key: label for key, label in labels.items() if list(labels.values()).count(label) == 1}


def verify_group_labels(
    previous_labels: Mapping[str, str],
    incoming_labels: Mapping[str, str],
    previous_roster: Iterable[Mapping[str, Any]],
    incoming_roster: Iterable[Mapping[str, Any]],
    existing_markers: Iterable[str] = (),
) -> None:
    """Reject unsafe transition of legacy positional keys without changing any key."""
    def by_marker(rows: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            result[str(row["group_marker"])].add(str(row["student_key"]).rsplit("|", 1)[0])
        return result

    error = ("Не удалось подтвердить, что названия групп соответствуют сохранённым группам журнала. "
             "Сверьте заголовки и составы групп. Прежние оценки и связи сохранены.")
    if any(incoming_labels.get(marker) != label for marker, label in previous_labels.items()):
        raise JournalGroupLabelConflict(error)
    old_rosters = by_marker(previous_roster)
    new_rosters = by_marker(incoming_roster)
    known_markers = set(existing_markers) | old_rosters.keys()
    for marker in incoming_labels.keys() - previous_labels.keys():
        if marker in known_markers:
            old_keys = old_rosters.get(marker)
            # On first adoption, coordinates alone cannot prove which old
            # group the header names. Require a complete same-key roster.
            if not old_keys or old_keys != new_rosters.get(marker) or any(
                    candidate != marker and keys == old_keys for candidate, keys in old_rosters.items()):
                raise JournalGroupLabelConflict(error)
