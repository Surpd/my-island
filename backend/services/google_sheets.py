from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
import re
from typing import Any, Protocol

from backend.database import Database
from backend.services.schedule_import import record_failed_sync, sync_schedule, sync_schedule_with_stats


HEADER_ALIASES = {
    "date": "date", "дата": "date", "day": "date",
    "start": "start_time", "start_time": "start_time", "время": "start_time", "time": "start_time",
    "end": "end_time", "end_time": "end_time", "до": "end_time",
    "subject": "subject", "предмет": "subject", "урок": "subject",
    "teacher": "teacher", "учитель": "teacher", "преподаватель": "teacher",
    "room": "room", "кабинет": "room", "аудитория": "room",
    "audience": "audience", "группа": "audience", "класс": "audience",
}

_DATE_RE = re.compile(r"^\s*(?P<day>\d{1,2})[./](?P<month>\d{1,2})\s*$")
_RUSSIAN_DATE_RE = re.compile(r"^\s*(?P<day>\d{1,2})\s+(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b", re.IGNORECASE)
_TIME_RANGE_RE = re.compile(r"^\s*(?P<start>\d{1,2}:\d{2})\s*[-–—]\s*(?P<end>\d{1,2}:\d{2})\s*$")
_ROOM_RE = re.compile(r"^\s*(?P<room>каб(?:инет)?\.?\s*.+|зал\b.*|онлайн\b.*)\s*$", re.IGNORECASE)
_TEACHER_RE = re.compile(r"\b(?P<teacher>Елена\s+Викторовна|ДФ|ЕВ|ИА|АнК|Дмитрий|Юлия|Антон|Игорь|Леонид|Ангелина|Тарас|Иван|Родион|Анастасия|Мария|Анна|Татьяна|Елена|Сергей)\b", re.IGNORECASE)
_CLASS_TOKEN_RE = re.compile(r"\b\d{1,2}(?:-[А-ЯЁA-Z])\b", re.IGNORECASE)
_CLASS_HEADER_RE = re.compile(r"^\d{1,2}(?:-[А-ЯЁA-Z])?(?:-\d+)?$", re.IGNORECASE)
_SCHOOL_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


class LessonSemanticFallback(Protocol):
    """Optional semantic fallback; it may fill lesson fields but never owns audience."""

    def parse(self, raw_text: str, audience: str) -> dict[str, Any]: ...


class StructuralBoundaryError(ValueError):
    pass


def sheet_values_to_rows(values: Sequence[Sequence[str]]) -> list[dict[str, str]]:
    if not values:
        raise ValueError("Google Sheet returned no values")
    headers = [HEADER_ALIASES.get(str(value).strip().lower(), str(value).strip().lower()) for value in values[0]]
    required = {"date", "start_time", "subject"}
    if not required.issubset(headers):
        missing = ", ".join(sorted(required - set(headers)))
        raise ValueError(f"Google Sheet is missing required columns: {missing}")
    rows: list[dict[str, str]] = []
    for values_row in values[1:]:
        row = {header: str(values_row[index]).strip() if index < len(values_row) else "" for index, header in enumerate(headers) if header}
        if any(row.values()):
            rows.append(row)
    return rows


def _school_year_start(spreadsheet_title: str = "", sheet_title: str = "") -> int:
    match = re.search(r"(?<!\d)(20\d{2})\s*/\s*\d{2}(?!\d)", f"{spreadsheet_title} {sheet_title}")
    return int(match.group(1)) if match else date.today().year


def _parse_date_marker(value: object, school_year_start: int) -> str | None:
    text = str(value or "").strip()
    match = _DATE_RE.match(text)
    if match:
        month = int(match.group("month"))
        day = int(match.group("day"))
    else:
        russian_match = _RUSSIAN_DATE_RE.match(text)
        if not russian_match:
            return None
        month = _SCHOOL_MONTHS[russian_match.group("month").lower()]
        day = int(russian_match.group("day"))
    try:
        return date(school_year_start, month, day).isoformat()
    except ValueError as error:
        raise ValueError(f"invalid schedule date marker: {text}") from error


def _parse_lesson_cell(value: object) -> tuple[str, str, str]:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return "", "", ""
    room = ""
    remaining: list[str] = []
    for line in lines:
        match = _ROOM_RE.match(line)
        if match:
            room = match.group("room").strip()
        else:
            remaining.append(line)
    if not remaining:
        return "", "", room
    subject = remaining[0]
    teacher = remaining[1] if len(remaining) >= 2 else ""
    return subject, teacher, room


def _parse_lesson_semantics(value: object, audience: str, fallback: LessonSemanticFallback | None = None) -> dict[str, Any]:
    """Parse the school's compact cell grammar without using the class letter as a subgroup."""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    raw = "\n".join(lines)
    if not lines:
        return {"subject": "", "teacher": "", "room": "", "parse_status": "skipped", "parse_diagnostics": "empty cell"}
    room = ""
    content: list[str] = []
    for line in lines:
        match = _ROOM_RE.match(line)
        if match:
            room = match.group("room").strip()
        else:
            content.append(line)
    first = content[0] if content else ""
    teacher = content[1] if len(content) >= 2 else ""
    match = _TEACHER_RE.search(first)
    if match:
        teacher = match.group("teacher").strip()
        first = f"{first[:match.start()]} {first[match.end():]}".strip()
    elif len(content) < 2:
        teacher = ""

    exam_track = "ОГЭ" if re.search(r"\bОГЭ\b", raw, re.IGNORECASE) else "ЕГЭ" if re.search(r"\bЕГЭ\b", raw, re.IGNORECASE) else ""
    delivery_mode = "online" if re.search(r"онлайн|онлвйн", raw, re.IGNORECASE) else ""
    lesson_type = "class_hour" if re.search(r"\bкл\s*час", raw, re.IGNORECASE) else "extracurricular" if re.search(r"клуб|внеуроч|круж", raw, re.IGNORECASE) else "practice" if re.search(r"практикум|практика", raw, re.IGNORECASE) else "lesson"
    subject_subgroup = ""
    subgroup_match = re.search(r"\bмат(?:ем|ематика)?\s+([ABCАВС])\b", first, re.IGNORECASE)
    if subgroup_match:
        subject_subgroup = subgroup_match.group(1).upper().replace("А", "A").replace("В", "B").replace("С", "C")
    else:
        english_match = re.search(r"\bангл(?:ийский)?\s+(?:(?:группа|гр)\.?\s*)?(\d{1,2})\b", first, re.IGNORECASE)
        if english_match and not audience.startswith("10") and not audience.startswith("11"):
            subject_subgroup = english_match.group(1)

    subject_patterns = (
        (r"^кл\s*час", "Классный час"),
        (r"^⚪?английский\s+клуб", "Английский"),
        (r"^англ(?:ийский)?\b", "Английский"),
        (r"^мат(?:ем|ематика)?\b|^матпроф\b", "Математика"),
        (r"^информ(?:атика)?\b|^инфор\b", "Информатика"),
        (r"^обществознание\b|^общество\b|^обществ\b", "Обществознание"),
        (r"^литература\b|^литер\b", "Литература"),
        (r"^русский\b|^рус\b", "Русский язык"),
        (r"^история\s+искусств(?:а)?\b", "История искусств"),
        (r"^история\b", "История"),
        (r"^физика\b|^физ\b", "Физика"),
        (r"^химия\b|^хим\b", "Химия"),
        (r"^биология\b|^био\b", "Биология"),
        (r"^география\b|^геогр\b", "География"),
        (r"^естествознание\b", "Естествознание"),
    )
    subject = first
    for pattern, normalized in subject_patterns:
        if re.search(pattern, first, re.IGNORECASE):
            subject = normalized
            break
    diagnostics: list[str] = []
    unknown_tokens: list[str] = []
    if subject == first and not any(re.search(pattern, first, re.IGNORECASE) for pattern, _ in subject_patterns):
        unknown_tokens.append(first)
    if not teacher:
        diagnostics.append("teacher not confidently identified")
    if not room:
        diagnostics.append("room not provided")
    if not subject:
        diagnostics.append("subject not identified")
    if audience and _CLASS_TOKEN_RE.search(first) and not re.search(re.escape(audience), first, re.IGNORECASE):
        diagnostics.append("class marker in lesson text differs from column audience")
    if unknown_tokens:
        diagnostics.append(f"unknown tokens: {', '.join(unknown_tokens)}")
    if fallback and (unknown_tokens or not teacher):
        candidate = fallback.parse(raw, audience)
        if isinstance(candidate, dict):
            if subject == first and candidate.get("subject"):
                subject = str(candidate["subject"]).strip()
            if not teacher and candidate.get("teacher"):
                teacher = str(candidate["teacher"]).strip()
            if not room and candidate.get("room"):
                room = str(candidate["room"]).strip()
            if not subject_subgroup and candidate.get("subject_subgroup"):
                subject_subgroup = str(candidate["subject_subgroup"]).strip()
            if not exam_track and candidate.get("exam_track"):
                exam_track = str(candidate["exam_track"]).strip()
            if candidate.get("lesson_type"):
                lesson_type = str(candidate["lesson_type"]).strip()
            if candidate.get("delivery_mode"):
                delivery_mode = str(candidate["delivery_mode"]).strip()
            diagnostics.append("semantic fallback applied")
            unknown_tokens = []
    special = lesson_type != "lesson" or bool(delivery_mode or exam_track)
    if not subject:
        parse_status = "failed"
    elif unknown_tokens or any(item.startswith("class marker") for item in diagnostics):
        parse_status = "ambiguous"
    elif special:
        parse_status = "special"
    elif not teacher or not room:
        parse_status = "partial"
    else:
        parse_status = "parsed"
    return {
        "subject": subject,
        "teacher": teacher,
        "room": room,
        "subject_subgroup": subject_subgroup,
        "exam_track": exam_track,
        "lesson_type": lesson_type,
        "delivery_mode": delivery_mode,
        "parse_status": parse_status,
        "parse_diagnostics": "; ".join(diagnostics),
        "unknown_tokens": unknown_tokens,
        "raw_text": raw,
    }


def _header_owners(headers: Sequence[str]) -> list[tuple[str, int]]:
    """Resolve ownership before semantic parsing and fail closed at unknown headers.

    A blank column inherits only from the nearest confirmed class header. Any non-empty,
    non-class header terminates that block so a neighboring area cannot leak into it.
    """
    owners: list[tuple[str, int]] = []
    current = ""
    current_column = 0
    for column_index, value in enumerate(headers):
        normalized = str(value or "").strip()
        if column_index and _CLASS_HEADER_RE.fullmatch(normalized):
            current = normalized
            current_column = column_index + 1
        elif column_index and normalized:
            current = ""
            current_column = 0
        owners.append((current, current_column))
    return owners


def parse_school_schedule_values(
    values: Sequence[Sequence[str]],
    *,
    sheet_title: str,
    spreadsheet_title: str = "",
    semantic_fallback: LessonSemanticFallback | None = None,
) -> list[dict[str, str]]:
    """Parse the school's weekly matrix: class headers, date rows, time rows, lesson cells."""
    if not values:
        raise ValueError(f"{sheet_title}: Google Sheet returned no values")
    school_year_start = _school_year_start(spreadsheet_title, sheet_title)
    width = max(len(row) for row in values)
    headers = [str(value or "").strip() for value in values[0]]
    headers.extend([""] * (width - len(headers)))
    owners = _header_owners(headers)
    if not any(headers[1:]):
        raise ValueError(f"{sheet_title}: class header row is missing")
    entries: list[dict[str, str]] = []
    current_date = ""
    saw_date = False
    for row_index, row in enumerate(values, start=1):
        first_cell = row[0] if row else ""
        date_marker = _parse_date_marker(first_cell, school_year_start)
        time_match = _TIME_RANGE_RE.match(str(first_cell or ""))
        if date_marker and not time_match:
            current_date = date_marker
            saw_date = True
            continue
        if not time_match or not current_date:
            continue
        start_hour, start_minute = time_match.group("start").split(":")
        end_hour, end_minute = time_match.group("end").split(":")
        start_time = f"{int(start_hour):02d}:{start_minute}"
        end_time = f"{int(end_hour):02d}:{end_minute}"
        for column_index, value in enumerate(row[1:], start=1):
            if not str(value or "").strip():
                continue
            audience, header_column = owners[column_index] if column_index < len(owners) else ("", 0)
            parsed = _parse_lesson_semantics(value, audience, semantic_fallback)
            if not parsed["subject"]:
                parsed["subject"] = str(value).splitlines()[0].strip()
            if not audience:
                parsed["parse_status"] = "ambiguous"
                parsed["parse_diagnostics"] = "; ".join(filter(None, [parsed.get("parse_diagnostics", ""), "no class header owner for column"]))
            entries.append({
                "date": current_date,
                "start_time": start_time,
                "end_time": end_time,
                "subject": parsed["subject"],
                "teacher": parsed["teacher"],
                "room": parsed["room"],
                "audience": audience,
                "subject_subgroup": parsed.get("subject_subgroup", ""),
                "exam_track": parsed.get("exam_track", ""),
                "lesson_type": parsed.get("lesson_type", "lesson"),
                "delivery_mode": parsed.get("delivery_mode", ""),
                "parse_status": parsed.get("parse_status", "ambiguous"),
                "parse_diagnostics": parsed.get("parse_diagnostics", ""),
                "unknown_tokens": parsed.get("unknown_tokens", []),
                "raw_source": {
                    "sheet": sheet_title,
                    "row": row_index,
                    "column": column_index + 1,
                    "coordinate": f"{chr(64 + column_index + 1)}{row_index}" if column_index < 26 else f"R{column_index + 1}C{row_index}",
                    "header_source_column": header_column,
                    "cell": str(value),
                },
            })
    if not saw_date:
        raise ValueError(f"{sheet_title}: no date rows found")
    if not entries:
        raise ValueError(f"{sheet_title}: no schedule entries found")
    return entries


def looks_like_school_schedule(values: Sequence[Sequence[str]]) -> bool:
    if not values or not values[0]:
        return False
    class_headers = sum(1 for value in values[0][1:] if _CLASS_HEADER_RE.fullmatch(str(value or "").strip()))
    has_date_marker = any(_parse_date_marker(row[0], date.today().year) for row in values[1:] if row)
    return class_headers >= 2 and has_date_marker


def import_school_schedule_tabs(
    database: Database,
    tabs: Sequence[tuple[str, Sequence[Sequence[str]]]],
    *,
    spreadsheet_title: str,
    source: str,
    week_start: str | None = None,
    return_stats: bool = False,
) -> int | dict[str, int]:
    selected = 0
    try:
        parsed_tabs: list[tuple[str, list[dict[str, str]]]] = []
        for sheet_title, values in tabs:
            if not looks_like_school_schedule(values):
                continue
            parsed_tabs.append((sheet_title, parse_school_schedule_values(values, sheet_title=sheet_title, spreadsheet_title=spreadsheet_title)))
        if not parsed_tabs:
            raise ValueError("No visible weekly schedule matrix was found")
        target = date.fromisoformat(week_start) if week_start else date.today()
        target_week = target - timedelta(days=target.weekday())
        matching = [item for item in parsed_tabs if any(row["date"] == target.isoformat() or row["date"] == target_week.isoformat() for row in item[1])]
        if not matching:
            future = [item for item in parsed_tabs if min(row["date"] for row in item[1]) >= target.isoformat()]
            matching = [min(future, key=lambda item: min(row["date"] for row in item[1]))] if future else [max(parsed_tabs, key=lambda item: max(row["date"] for row in item[1]))]
        rows = [row for _, tab_rows in matching for row in tab_rows]
        selected = len(matching)
        stats = sync_schedule_with_stats(database, rows, source=source)
        return stats if return_stats else stats["total"]
    except Exception as error:
        record_failed_sync(database, source, error)
        raise


class GoogleSheetsScheduleImporter:
    def __init__(self, database: Database, fetch_values: Callable[[str], Sequence[Sequence[str]]]) -> None:
        self.database = database
        self.fetch_values = fetch_values

    def import_range(self, spreadsheet_id: str, source_range: str = "A:Z") -> int:
        rows = sheet_values_to_rows(self.fetch_values(f"{spreadsheet_id}!{source_range}"))
        return sync_schedule(self.database, rows, source=f"google_sheets:{spreadsheet_id}:{source_range}")
