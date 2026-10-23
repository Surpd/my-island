from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Sequence

from backend.database import Database


SUMMARY_HEADERS = {"итого", "допуск", "допуск к аттестации", "итоговая", "итоговая оценка", "средний балл", "средняя оценка", "пропущенные весы"}
HEADER_ALIASES = {
    "date": {"дата", "дата работы", "дата выполнения"},
    "title": {"задание", "работа", "название работы", "тема задания"},
    "weight": {"вес", "вес работы"},
    "max_score": {"оценка", "максимальный балл", "макс. балл", "максимум"},
}
MATH_GROUP_RE = re.compile(r"^(?:9\s*[-–]?\s*[ABC]|9\s*[-–]?\s*[123]|[ABC])$", re.IGNORECASE)


def column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _normalized(value: object) -> str:
    return " ".join(str(value if value is not None else "").strip().casefold().replace("ё", "е").split())


def _header_kind(value: object) -> str | None:
    normalized = _normalized(value).rstrip(":")
    return next((kind for kind, aliases in HEADER_ALIASES.items() if normalized in aliases), None)


def _canonical_group_marker(marker: str, *, grade: int, subject: str) -> str:
    if grade == 9 and _normalized(subject) in {"математика", "math"}:
        compact = re.sub(r"\s+", "", marker).replace("–", "-")
        return {"A": "A", "B": "B", "C": "C", "9-1": "A", "9-2": "B", "9-3": "C", "9A": "A", "9B": "B", "9C": "C"}.get(compact.upper(), "all")
    return "all"


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value: object, school_year_start: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Google Sheets date serials use 1899-12-30 as day zero.
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    text = str(value).strip()
    for pattern in ("%d.%m.%Y", "%d.%m", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
            if pattern == "%d.%m":
                parsed = parsed.replace(year=school_year_start if parsed.month >= 8 else school_year_start + 1)
            return parsed.date().isoformat()
        except ValueError:
            continue
    return None


def _cell(row: Sequence[object], column: int) -> object:
    return row[column] if column < len(row) else None


def _signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _header_blocks(values: Sequence[Sequence[object]], sheet_title: str) -> list[dict[str, int]]:
    blocks: list[dict[str, int]] = []
    for index, row in enumerate(values):
        if _header_kind(_cell(row, 0)) != "date":
            continue
        found: dict[str, int] = {"date": index}
        for next_index in range(index + 1, min(index + 8, len(values))):
            kind = _header_kind(_cell(values[next_index], 0))
            if kind and kind not in found:
                found[kind] = next_index
        missing = set(HEADER_ALIASES) - found.keys()
        if missing:
            raise ValueError(f"{sheet_title}: неполный блок журнала около строки {index + 1}; не найдены: {', '.join(sorted(missing))}")
        blocks.append(found)
    if not blocks:
        expected = ", ".join(sorted(next(iter(HEADER_ALIASES.values()))))
        raise ValueError(f"{sheet_title}: не найден блок с датой, работой, весом и максимальным баллом")
    return blocks


def parse_journal_values(
    values: Sequence[Sequence[object]], *, grade: int, subject: str, sheet_title: str,
    school_year_start: int = 2026, sheet_id: int | str | None = None,
) -> dict[str, Any]:
    """Parse structurally-discovered gradebook blocks without relying on row/column coordinates.

    Repeated header blocks (including separate class and teacher blocks) are handled in the
    same tab. Assignment signatures use metadata plus the roster/value vector; coordinates
    remain descriptive metadata only.
    """
    if not values:
        raise ValueError(f"{sheet_title}: журнал пуст")
    blocks = _header_blocks(values, sheet_title)
    width = max((len(row) for row in values), default=0)
    assessments: list[dict[str, Any]] = []
    students_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for block_index, block in enumerate(blocks):
        block_start = min(block.values())
        previous_end = max(blocks[block_index - 1].values()) + 1 if block_index else 0
        marker = "all"
        if grade == 9 and _normalized(subject) in {"математика", "math"}:
            for prior in values[previous_end:block_start]:
                text = str(_cell(prior, 0) or "").strip()
                if MATH_GROUP_RE.fullmatch(text):
                    marker = _canonical_group_marker(text, grade=grade, subject=subject)
        elif len(blocks) > 1:
            # Repeated blocks can represent separate local rosters (for example,
            # parallel English groups). Keep their sheet labels verbatim; do not
            # infer canonical class membership from a label we do not understand.
            label_start = previous_end
            labels = []
            for label_row in range(block["date"] - 1, label_start - 1, -1):
                row = values[label_row]
                label = str(_cell(row, 0) or "").strip()
                other_values = any(str(_cell(row, column) or "").strip() for column in range(1, len(row)))
                if label and not other_values and not _header_kind(label):
                    labels.append(label)
                    break
            marker = labels[0] if labels else f"block {block_index + 1}"

        title_row = values[block["title"]]
        summary_columns = [(column, str(_cell(title_row, column) or "").strip()) for column in range(1, width) if _normalized(_cell(title_row, column)) in SUMMARY_HEADERS]
        metadata_rows = {key: values[row_index] for key, row_index in block.items()}
        end = blocks[block_index + 1]["date"] if block_index + 1 < len(blocks) else len(values)
        student_rows: list[tuple[int, str, str]] = []
        current_marker = marker
        for row_index in range(max(block.values()) + 1, end):
            row = values[row_index]
            name = str(_cell(row, 0) if _cell(row, 0) is not None else "").strip()
            normalized_name = _normalized(name)
            if not name or normalized_name in SUMMARY_HEADERS or _header_kind(name):
                continue
            if grade == 9 and _normalized(subject) in {"математика", "math"} and MATH_GROUP_RE.fullmatch(name):
                current_marker = _canonical_group_marker(name, grade=grade, subject=subject)
                continue
            # Keep students with an empty grade row while ignoring ordinary section labels.
            has_assignment_value = any(_cell(row, col) is not None and str(_cell(row, col)).strip() for col in range(1, width))
            if not has_assignment_value and len(normalized_name.split()) < 2:
                continue
            student_rows.append((row_index, name, current_marker))

        for row_index, name, student_marker in student_rows:
            student_key = f"{_normalized(name)}|{student_marker}"
            for column, label in summary_columns:
                raw_summary = _cell(values[row_index], column)
                if raw_summary is None or str(raw_summary).strip() == "":
                    continue
                numeric_summary = _number(raw_summary)
                summaries.append({
                    "student_key": student_key, "student_name": name, "group_marker": student_marker,
                    "label": label, "numeric_value": numeric_summary,
                    "text_value": None if numeric_summary is not None else str(raw_summary).strip(),
                    "source_coordinate": f"{column_name(column)}{row_index + 1}",
                    "raw_source": {"value": raw_summary, "student_row": row_index + 1},
                })

        block_assessments: list[tuple[int, dict[str, Any]]] = []
        for column in range(1, width):
            title = str(_cell(title_row, column) if _cell(title_row, column) is not None else "").strip()
            if not title or _normalized(title) in SUMMARY_HEADERS:
                continue
            meta = {
                "title": title,
                "assessed_on": _date(_cell(metadata_rows["date"], column), school_year_start),
                "weight": _number(_cell(metadata_rows["weight"], column)),
                "max_score": _number(_cell(metadata_rows["max_score"], column)),
                "group_marker": marker,
            }
            block_assessments.append((column, meta))
        if not block_assessments:
            raise ValueError(f"{sheet_title}: в блоке {block_index + 1} нет заданий")

        for column, metadata in block_assessments:
            score_vector = []
            pending: list[dict[str, Any]] = []
            for row_index, name, student_marker in student_rows:
                student_key = f"{_normalized(name)}|{student_marker}"
                student = students_by_key.setdefault((student_marker, student_key), {
                    "student_name": name, "student_key": student_key, "group_marker": student_marker,
                    "source_row": row_index + 1, "raw_source": {"student_row": row_index + 1},
                })
                raw_value = _cell(values[row_index], column)
                text_value = str(raw_value if raw_value is not None else "").strip()
                score = _number(raw_value)
                score_vector.append((student_key, score if score is not None else text_value.casefold()))
                if not text_value:
                    continue
                pending.append({
                    "student_name": name, "student_key": student_key, "group_marker": student_marker,
                    "numeric_score": score, "status": None if score is not None else text_value.casefold(),
                    "source_coordinate": f"{column_name(column)}{row_index + 1}",
                    "raw_source": {"value": raw_value, "student_row": row_index + 1, "group_marker": student_marker},
                })
            identity_value = {**metadata, "score_vector": sorted(score_vector)}
            external_key = f"{sheet_id or 'sheet'}:{_signature(identity_value)}"
            assessment = {
                "external_key": external_key, **metadata, "source_column": column_name(column),
                "value_signature": _signature(sorted(score_vector)),
                "raw_source": {"date": _cell(metadata_rows["date"], column), "title": metadata["title"],
                               "weight": _cell(metadata_rows["weight"], column), "max_score": _cell(metadata_rows["max_score"], column),
                               "header_rows": {key: index + 1 for key, index in block.items()}, "group_marker": marker},
            }
            assessments.append(assessment)
            for result in pending:
                result["assessment_key"] = external_key
                results.append(result)

    # Empty placeholder columns often repeat metadata but carry no grades. They
    # have no stable identity to preserve, so keep one. If the same signature
    # contains actual results, leave it as a diagnostic instead of guessing.
    result_keys = {item["assessment_key"] for item in results}
    unique_assessments: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for assessment in assessments:
        signature = assessment["external_key"]
        if signature in seen_signatures:
            if signature in result_keys:
                raise ValueError(f"{sheet_title}: несколько заданий имеют неразличимые признаки; импорт остановлен")
            continue
        seen_signatures.add(signature)
        unique_assessments.append(assessment)
    assessments = unique_assessments
    payload = {"grade": grade, "subject": subject, "assessments": assessments,
               "students": list(students_by_key.values()), "results": results, "summaries": summaries}
    payload["source_hash"] = _signature(payload)
    return payload


def sync_journal_values(
    database: Database, values: Sequence[Sequence[object]], *, spreadsheet_id: str,
    spreadsheet_title: str, grade: int, subject: str, sheet_title: str,
    sheet_id: int | str | None = None,
) -> dict[str, int]:
    parsed = parse_journal_values(values, grade=grade, subject=subject, sheet_title=sheet_title, sheet_id=sheet_id)
    source = {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": spreadsheet_title, "grade": grade,
              "subject": subject, "sheet_title": sheet_title, "sheet_id": sheet_id,
              "source_hash": parsed["source_hash"]}
    return database.save_journal_snapshot(source, parsed["assessments"], parsed["results"], parsed["students"], parsed["summaries"])
