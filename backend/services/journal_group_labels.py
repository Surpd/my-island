"""Display labels from journal headers, separate from persistent roster identity."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

MATH_HEADER = re.compile(r"II\s+триместр\s*·\s*9\s*[-–]?\s*([ABCАВС])", re.IGNORECASE)
CYRILLIC_LOOKALIKES = str.maketrans("АВС", "ABC")


def math_header_label(value: object, *, grade: int, subject: str) -> str | None:
    if grade != 9 or subject.strip().casefold() not in {"математика", "math"}:
        return None
    match = MATH_HEADER.fullmatch(str(value or "").strip())
    return "9" + match[1].upper().translate(CYRILLIC_LOOKALIKES) if match else None


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
        evidence[str(row["group_marker"])].add(label if label in ("9A", "9B", "9C") else None)
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

    error = ("Не удалось подтвердить, что группы 9A, 9B и 9C соответствуют сохранённым группам журнала. "
             "Сверьте заголовки и составы групп. Прежние оценки и связи сохранены.")
    if any(incoming_labels.get(marker) != label for marker, label in previous_labels.items()):
        raise ValueError(error)
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
                raise ValueError(error)
