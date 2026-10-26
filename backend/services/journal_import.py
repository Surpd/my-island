from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from backend.database import Database


SUMMARY_TYPES = {
    "итого": "total_points", "сумма баллов за триместр": "total_points", "сумма баллов за триместр всего": "total_points",
    "допуск": "admission", "допуск к аттестации": "admission", "допуск к аттестации за триместр": "admission",
    "средняя отметка": "average_grade", "средняя оценка": "average_grade", "средний балл": "average_grade", "средний балл за триместр": "average_grade",
    "итоговая": "term_grade", "итоговая оценка": "term_grade", "оценка за триместр": "term_grade",
    "пропущенные весы": "missing_weights", "пропущенные работы": "missing_weights", "не выставлены оценки": "missing_weights",
}
SUMMARY_HEADERS = set(SUMMARY_TYPES) | {"допуск к аттестации", "итоговая оценка"}
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
    exact = next((kind for kind, aliases in HEADER_ALIASES.items() if normalized in aliases), None)
    if exact:
        return exact
    # Some creative journals place a teacher/subject label above the header
    # in the same merged cell. Match an entire line, never a work title substring.
    lines = [_normalized(line).rstrip(":") for line in str(value or "").splitlines()]
    return next((kind for kind, aliases in HEADER_ALIASES.items() if any(line in aliases for line in lines)), None)


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


def _summary_number(value: object) -> str | None:
    """Keep exact source precision for calculated summary values."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        number = Decimal(text)
        return str(number) if number.is_finite() else None
    except InvalidOperation:
        return None


def _maximum(value: object) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric
    match = re.search(r"оценка.*из\s*(\d+(?:[.,]\d+)?)", _normalized(value))
    return _number(match.group(1)) if match else None


def _summary_type(value: object) -> str | None:
    return SUMMARY_TYPES.get(_normalized(value).rstrip(":"))


_TERM_RE = re.compile(r"\s*(?:[-–—:]\s*)?(?:\(\s*)?(?:триместр\s*)?(?:([1-3])|([ivx]+))(?:\s*триместр)?\s*\)?\s*$", re.IGNORECASE)


def subject_period_from_title(title: str) -> tuple[str, int | None]:
    """Return a canonical subject and an explicit trimester, if present."""
    match = _TERM_RE.search(title.strip())
    if not match:
        return title.strip(), None
    token = (match.group(1) or match.group(2) or "").casefold()
    term = int(token) if token.isdigit() else {"i": 1, "ii": 2, "iii": 3}.get(token)
    subject = title[:match.start()].strip(" -–—:()")
    return (subject or title.strip()), term


def _date(value: object, school_year_start: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Google Sheets date serials use 1899-12-30 as day zero.
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    text = re.sub(r"\.{2,}", ".", str(value).strip())
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
        implicit_date = (not str(_cell(row, 0) or "").strip() and index + 1 < len(values)
                         and _header_kind(_cell(values[index + 1], 0)) == "title"
                         and any(_date(cell, 2026) for cell in row[1:]))
        if _header_kind(_cell(row, 0)) != "date" and not implicit_date:
            continue
        found: dict[str, int] = {"date": index}
        for next_index in range(index + 1, min(index + 8, len(values))):
            kind = _header_kind(_cell(values[next_index], 0))
            if kind and kind not in found:
                found[kind] = next_index
            if _normalized(_cell(values[next_index], 0)) == "участники" and any(re.search(r"оценка.*из\s*\d+", _normalized(v)) for v in values[next_index][1:]):
                found.setdefault("max_score", next_index)
        # A date-like row is only a gradebook block when it is followed by an
        # assignment-title header. Auxiliary tables often have their own dates
        # but use labels such as "Ученик" and statistics, not assignment columns.
        if "title" not in found:
            continue
        missing = set(HEADER_ALIASES) - found.keys()
        # Project checklists deliberately have no numerical weights/maxima.
        if _normalized(sheet_title).startswith("проекты") and found.get("title") == index + 1:
            missing -= {"weight", "max_score"}
        if missing:
            raise ValueError(f"{sheet_title}: неполный блок журнала около строки {index + 1}; не найдены: {', '.join(sorted(missing))}")
        blocks.append(found)
    if not blocks:
        expected = ", ".join(sorted(next(iter(HEADER_ALIASES.values()))))
        raise ValueError(f"{sheet_title}: не найден блок с датой, работой, весом и максимальным баллом")
    return blocks


def _display_name(value: str) -> str:
    """Remove appended private comments from a display name conservatively."""
    # Notes are commonly appended on a new line or in parentheses. Do not
    # split on ordinary spaces/punctuation, which can be part of a real name.
    name = re.split(r"\s*\r?\n\s*", value, maxsplit=1)[0].strip()
    name = re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()
    return name


def _section_label(value: object) -> bool:
    text = _normalized(value)
    return bool(re.match(r"^(?:group|английский|математика|обществознание|группа|графика|программирование|медиамастерская|машинная графика|ш\.у\.м\.|базовая группа|профильная группа)\b", text)
                or re.fullmatch(r"(?:[5-9]|1[01])\s*[-–]?\s*[а-яa-z]+|[abc]|[А-ЯЁ]{3,}", str(value or "").strip()))


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
    # Auxiliary exercise/statistics tables may start with their own date row,
    # followed by a compact roster header rather than gradebook metadata.
    auxiliary_starts = [index for index, row in enumerate(values)
                        if (_header_kind(_cell(row, 0)) == "date" or _date(_cell(row, 0), school_year_start) is not None)
                        and index + 1 < len(values)
                        and _normalized(_cell(values[index + 1], 0)) == "ученик"]
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
                if label and not other_values and not _header_kind(label) and _section_label(label):
                    labels.append(label)
                    break
            marker = labels[0] if labels else f"block {block_index + 1}"

        title_row = values[block["title"]]
        summary_columns = [(column, str(_cell(title_row, column) or "").strip(), _summary_type(_cell(title_row, column))) for column in range(1, width) if _summary_type(_cell(title_row, column))]
        metadata_rows = {key: values[row_index] for key, row_index in block.items()}
        end = blocks[block_index + 1]["date"] if block_index + 1 < len(blocks) else len(values)
        later_auxiliary = [start for start in auxiliary_starts if start > max(block.values())]
        if later_auxiliary:
            end = min(end, min(later_auxiliary))
        student_rows: list[tuple[int, str, str]] = []
        current_marker = marker
        for row_index in range(max(block.values()) + 1, end):
            row = values[row_index]
            raw_name = str(_cell(row, 0) if _cell(row, 0) is not None else "").strip()
            name = _display_name(raw_name)
            normalized_name = _normalized(name)
            if not name or normalized_name in SUMMARY_HEADERS or _header_kind(name):
                continue
            if grade == 9 and _normalized(subject) in {"математика", "math"} and MATH_GROUP_RE.fullmatch(name):
                current_marker = _canonical_group_marker(name, grade=grade, subject=subject)
                continue
            if _section_label(name):
                continue
            # Keep students with an empty grade row while ignoring ordinary section labels.
            has_assignment_value = any(_cell(row, col) is not None and str(_cell(row, col)).strip() for col in range(1, width))
            if not has_assignment_value and len(normalized_name.split()) < 2:
                continue
            student_rows.append((row_index, name, current_marker))

        for row_index, name, student_marker in student_rows:
            student_key = f"{_normalized(name)}|{student_marker}"
            students_by_key.setdefault((student_marker, student_key), {
                "student_name": name, "student_key": student_key, "group_marker": student_marker,
                "entry_kind": "team" if (re.match(r"^(?:команда|team)\b", _normalized(name)) or
                    (grade in {10, 11} and _normalized(subject) in {"история искусства", "art history"} and
                     re.search(r"\s+и\s+", _normalized(name)))) else "individual",
                "source_row": row_index + 1, "raw_source": {"student_row": row_index + 1, "source_name": str(_cell(values[row_index], 0) or "").strip()},
            })

        for row_index, name, student_marker in student_rows:
            student_key = f"{_normalized(name)}|{student_marker}"
            for column, label, summary_type in summary_columns:
                raw_summary = _cell(values[row_index], column)
                if raw_summary is None or str(raw_summary).strip() == "":
                    continue
                numeric_summary = _summary_number(raw_summary)
                summaries.append({
                    "student_key": student_key, "student_name": name, "group_marker": student_marker,
                    "label": label, "summary_type": summary_type, "numeric_value": numeric_summary,
                    "text_value": None if numeric_summary is not None else str(raw_summary).strip(),
                    "source_coordinate": f"{column_name(column)}{row_index + 1}",
                    "raw_source": {"value": raw_summary, "student_row": row_index + 1},
                })

        block_assessments: list[tuple[int, dict[str, Any]]] = []
        for column in range(1, width):
            title = str(_cell(title_row, column) if _cell(title_row, column) is not None else "").strip()
            if not title or _summary_type(title):
                continue
            meta = {
                "title": title,
                "assessed_on": _date(_cell(metadata_rows["date"], column), school_year_start),
                "weight": _number(_cell(metadata_rows.get("weight", []), column)),
                "max_score": _maximum(_cell(metadata_rows.get("max_score", []), column)),
                "group_marker": marker,
            }
            block_assessments.append((column, meta))
        for column, metadata in block_assessments:
            score_vector = []
            pending: list[dict[str, Any]] = []
            for row_index, name, student_marker in student_rows:
                student_key = f"{_normalized(name)}|{student_marker}"
                student = students_by_key.setdefault((student_marker, student_key), {
                    "student_name": name, "student_key": student_key, "group_marker": student_marker,
                    "source_row": row_index + 1, "raw_source": {"student_row": row_index + 1, "source_name": str(_cell(values[row_index], 0) or "").strip()},
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
                               "weight": _cell(metadata_rows.get("weight", []), column), "max_score": _cell(metadata_rows.get("max_score", []), column),
                               "header_rows": {key: index + 1 for key, index in block.items()}, "group_marker": marker},
            }
            assessments.append(assessment)
            for result in pending:
                result["assessment_key"] = external_key
                results.append(result)

    # Preserve distinct, initially identical populated source columns. Their
    # occurrence only disambiguates an unchanged snapshot. Later non-identical
    # edits still hit the database's ambiguous-match guard and retain history.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for assessment in assessments:
        grouped.setdefault(assessment["external_key"], []).append(assessment)
    result_keys = {item["assessment_key"] for item in results}
    for key, duplicates in grouped.items():
        if len(duplicates) > 1 and key in result_keys:
            for ordinal, item in enumerate(duplicates, 1):
                item["external_key"] = f"{key}:occurrence:{ordinal}"
                item["raw_source"]["identical_source_columns"] = len(duplicates)
                for result in results:
                    if result["assessment_key"] == key and re.match(r"[A-Z]+", result["source_coordinate"])[0] == item["source_column"]:
                        result["assessment_key"] = item["external_key"]
    unique_assessments: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for assessment in assessments:
        signature = assessment["external_key"]
        if signature in seen_signatures:
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
    canonical_subject, explicit_term = subject_period_from_title(sheet_title)
    parsed = parse_journal_values(values, grade=grade, subject=canonical_subject, sheet_title=sheet_title, sheet_id=sheet_id)
    school_year_start = 2026
    school_year = f"{school_year_start}/{str(school_year_start + 1)[-2:]}"
    source = {"spreadsheet_id": spreadsheet_id, "spreadsheet_title": spreadsheet_title, "grade": grade,
              "subject": canonical_subject, "sheet_title": sheet_title, "sheet_id": sheet_id,
              "academic_year": school_year, "term_number": explicit_term,
              "source_hash": parsed["source_hash"]}
    return database.save_journal_snapshot(source, parsed["assessments"], parsed["results"], parsed["students"], parsed["summaries"])
