from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Sequence

from backend.database import Database


SUMMARY_HEADERS = {"итого", "допуск", "оценка", "итоговая", "пропущенные весы"}
GROUP_MARKER_RE = re.compile(r"^\d{1,2}-\d{1,2}$")


def column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _normalized(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value: object, school_year_start: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d.%m.%Y", "%d.%m", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
            if pattern == "%d.%m":
                year = school_year_start if parsed.month >= 8 else school_year_start + 1
                parsed = parsed.replace(year=year)
            return parsed.date().isoformat()
        except ValueError:
            continue
    return None


def parse_journal_values(values: Sequence[Sequence[object]], *, grade: int, subject: str, sheet_title: str, school_year_start: int = 2026) -> dict[str, Any]:
    """Parse the current deterministic Date/Assignment/Weight/Grade journal layout.

    The parser intentionally does not infer memberships. It preserves the explicit source
    group marker and links students later only by a unique canonical-name match.
    """
    if not values:
        raise ValueError(f"{sheet_title}: journal sheet is empty")
    labels: dict[str, int] = {}
    for row_index, row in enumerate(values):
        if not row:
            continue
        label = _normalized(row[0])
        if label in {"дата", "задание", "вес", "оценка"} and label not in labels:
            labels[label] = row_index
    missing = {"дата", "задание", "вес", "оценка"} - labels.keys()
    if missing:
        raise ValueError(f"{sheet_title}: missing journal header rows: {', '.join(sorted(missing))}")

    width = max(len(row) for row in values)
    task_row = values[labels["задание"]]
    assessment_columns: list[int] = []
    for column in range(1, width):
        title = str(task_row[column] if column < len(task_row) else "").strip()
        if _normalized(title) in SUMMARY_HEADERS:
            break
        if title:
            assessment_columns.append(column)
    if not assessment_columns:
        raise ValueError(f"{sheet_title}: no assessment columns found")

    assessments: list[dict[str, Any]] = []
    for column in assessment_columns:
        def cell(row_name: str) -> object:
            row = values[labels[row_name]]
            return row[column] if column < len(row) else ""

        title = str(cell("задание")).strip()
        source_column = column_name(column)
        assessments.append({
            "external_key": f"{sheet_title}:{source_column}",
            "title": title,
            "assessed_on": _date(cell("дата"), school_year_start),
            "weight": _number(cell("вес")),
            "max_score": _number(cell("оценка")),
            "source_column": source_column,
            "raw_source": {"date": cell("дата"), "title": title, "weight": cell("вес"), "max_score": cell("оценка")},
        })

    data_start = max(labels.values()) + 1
    group_marker = ""
    results: list[dict[str, Any]] = []
    for row_index in range(data_start, len(values)):
        row = values[row_index]
        first = str(row[0] if row else "").strip()
        if not first:
            continue
        if GROUP_MARKER_RE.fullmatch(first):
            group_marker = first
            continue
        if not group_marker or _normalized(first) in SUMMARY_HEADERS:
            continue
        for assessment, column in zip(assessments, assessment_columns):
            raw = row[column] if column < len(row) else ""
            text = str(raw or "").strip()
            if not text:
                continue
            numeric = _number(raw)
            results.append({
                "assessment_key": assessment["external_key"],
                "student_name": first,
                "group_marker": group_marker,
                "numeric_score": numeric,
                "status": None if numeric is not None else text.casefold(),
                "source_coordinate": f"{column_name(column)}{row_index + 1}",
                "raw_source": {"value": raw, "student_row": row_index + 1, "group_marker": group_marker},
            })
    if not results:
        raise ValueError(f"{sheet_title}: no student results found")
    payload = {"grade": grade, "subject": subject, "assessments": assessments, "results": results}
    payload["source_hash"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return payload


def sync_journal_values(database: Database, values: Sequence[Sequence[object]], *, spreadsheet_id: str, spreadsheet_title: str, grade: int, subject: str, sheet_title: str) -> dict[str, int]:
    parsed = parse_journal_values(values, grade=grade, subject=subject, sheet_title=sheet_title)
    source = {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": spreadsheet_title,
        "grade": grade,
        "subject": subject,
        "sheet_title": sheet_title,
        "source_hash": parsed["source_hash"],
    }
    return database.save_journal_snapshot(source, parsed["assessments"], parsed["results"])
