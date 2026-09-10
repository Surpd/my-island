"""Deterministic, auditable Schedule Integration v1.

The adapter reads School Directory data but never mutates identities, groups,
memberships, teacher assignments, journals, Classroom, or notifications.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from backend.database import Database
from backend.services.google_sheets import _parse_lesson_semantics


_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_WEEKDAYS = {"пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2,
             "чт": 3, "четверг": 3, "пт": 4, "пятница": 4, "сб": 5, "суббота": 5}
_CLASS = re.compile(r"^\d{1,2}(?:-[А-ЯЁA-Z])?(?:-\d+)?$", re.IGNORECASE)
_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*$")
_DATE = re.compile(r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*$")
_ROOM_LINE = re.compile(r"^(?:каб(?:инет)?\.?\s*.*|зал\b.*|ауд\.?\s*.*)$", re.IGNORECASE)
_NON_LESSON = ("🥨", "обед", "перемена", "окно", "нет урока", "выходной")
_SPECIAL = ("экскурс", "поездк", "мероприят", "праздник", "линейк", "спектак", "встреча")


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).split())


def _school_year(spreadsheet_title: str, tab_title: str) -> int:
    match = re.search(r"(?<!\d)(20\d{2})\s*/\s*\d{2}(?!\d)", f"{spreadsheet_title} {tab_title}")
    return int(match.group(1)) if match else date.today().year


def parse_date_range(title: str, year: int | None = None) -> tuple[str, str] | None:
    """Parse `7-11 Сентября`, `14 - 18.09`, and close variants."""
    text = title.casefold().replace("ё", "е")
    selected_year = year or next((int(x) for x in re.findall(r"20\d{2}", text)), date.today().year)
    word_month = next((number for stem, number in _MONTHS.items() if stem in text), None)
    word_range = re.search(r"(?<!\d)(\d{1,2})\s*[-–—]\s*(\d{1,2})(?!\s*[./]\s*\d)", text)
    if word_range and word_month:
        try:
            return (date(selected_year, word_month, int(word_range.group(1))).isoformat(),
                    date(selected_year, word_month, int(word_range.group(2))).isoformat())
        except ValueError:
            return None
    numeric = re.search(r"(?<!\d)(\d{1,2})\s*[-–—]\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if numeric:
        parsed_year = int(numeric.group(4) or selected_year)
        if parsed_year < 100:
            parsed_year += 2000
        try:
            return (date(parsed_year, int(numeric.group(3)), int(numeric.group(1))).isoformat(),
                    date(parsed_year, int(numeric.group(3)), int(numeric.group(2))).isoformat())
        except ValueError:
            return None
    explicit = re.findall(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if len(explicit) >= 2:
        def build(parts: tuple[str, str, str]) -> date:
            parsed_year = int(parts[2] or selected_year)
            if parsed_year < 100:
                parsed_year += 2000
            return date(parsed_year, int(parts[1]), int(parts[0]))
        try:
            return build(explicit[0]).isoformat(), build(explicit[1]).isoformat()
        except ValueError:
            return None
    return None


def classify_tab(title: str, *, schedule_like: bool = True, year: int | None = None) -> str:
    normalized = title.casefold()
    if schedule_like and any(token in normalized for token in ("шаблон", "template", "образец")):
        return "template"
    if schedule_like and parse_date_range(title, year):
        return "weekly"
    return "unknown" if schedule_like else "irrelevant"


def lesson_kind(raw_text: str) -> str:
    text = _norm(raw_text)
    if not text or any(token in text for token in _NON_LESSON):
        return "nonlesson"
    if "отмен" in text:
        return "cancelled"
    if any(token in text for token in _SPECIAL):
        return "special_event"
    if "курс" in text and "выбор" in text or "электив" in text:
        return "course_choice"
    if "клуб" in text or "круж" in text or "внеуроч" in text or raw_text.strip().startswith("⚪"):
        return "extracurricular"
    if "классн" in text and "час" in text or re.search(r"\bкл\s*час\b", text):
        return "class_hour"
    if "практик" in text:
        return "practicum"
    if "онлайн" in text or "online" in text or "онлвйн" in text:
        return "online"
    if "объедин" in text or "параллел" in text or "совмест" in text:
        return "combined"
    return "lesson"


def _cell_value(cell: Any) -> str:
    if isinstance(cell, Mapping):
        return str(cell.get("formattedValue") or cell.get("value") or "")
    return str(cell or "")


def _color(cell: Any) -> str:
    if not isinstance(cell, Mapping):
        return ""
    style = (cell.get("effectiveFormat") or {}).get("backgroundColorStyle") or {}
    rgb = style.get("rgbColor") or (cell.get("effectiveFormat") or {}).get("backgroundColor") or {}
    if not rgb:
        return ""
    channels = [round(float(rgb.get(name, 0 if name != "alpha" else 1)), 4) for name in ("red", "green", "blue", "alpha")]
    return ",".join(str(value) for value in channels)


def _a1(row: int, column: int) -> str:
    number = column + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def _merge_for(tab: Mapping[str, Any], row: int, column: int) -> dict[str, Any] | None:
    for merge in tab.get("merges") or []:
        if int(merge.get("startRowIndex", 0)) <= row < int(merge.get("endRowIndex", 0)) and int(merge.get("startColumnIndex", 0)) <= column < int(merge.get("endColumnIndex", 0)):
            return dict(merge)
    return None


def snapshot_cells(tab: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Capture content plus relevant blank colored/noted/merged cells in the bounded range."""
    result: list[dict[str, Any]] = []
    sheet_id = str(tab.get("sheet_id") or tab.get("sheetId") or "")
    for row_index, row in enumerate(tab.get("values") or []):
        for column, cell in enumerate(row or []):
            raw_text = _cell_value(cell)
            raw_cell = dict(cell) if isinstance(cell, Mapping) else {"formattedValue": raw_text}
            merge_data = _merge_for(tab, row_index, column)
            if not raw_text.strip() and not _color(cell) and not raw_cell.get("note") and not merge_data:
                continue
            result.append({"record_key": f"{sheet_id}:{_a1(row_index, column)}", "source_cell": _a1(row_index, column),
                           "raw_text": raw_text, "source_color": _color(cell), "merge_data": merge_data,
                           "effective_format": raw_cell.get("effectiveFormat") or {}, "raw_cell": raw_cell})
    return result


def _find_header(values: Sequence[Sequence[Any]]) -> int | None:
    for row_index, row in enumerate(values[:12]):
        if sum(bool(_CLASS.fullmatch(_cell_value(value).strip())) for value in row[1:]) >= 2:
            return row_index
    return None


def looks_like_schedule_matrix(values: Sequence[Sequence[Any]]) -> bool:
    header = _find_header(values)
    if header is None:
        return False
    time_rows = sum(bool(_TIME.fullmatch(_cell_value(row[0]))) for row in values[header + 1:] if row)
    weekday_rows = sum(sum(_norm(_cell_value(cell)) in _WEEKDAYS for cell in row[1:]) >= 2 for row in values[header + 1:])
    return time_rows >= 2 and weekday_rows >= 1


def _parse_marker(text: str, school_year: int) -> str | None:
    match = _DATE.fullmatch(text)
    if not match:
        return None
    parsed_year = int(match.group(3) or school_year)
    if parsed_year < 100:
        parsed_year += 2000
    try:
        return date(parsed_year, int(match.group(2)), int(match.group(1))).isoformat()
    except ValueError:
        return None


def _parse_cell(raw_text: str, audience: str) -> dict[str, Any]:
    parsed = _parse_lesson_semantics(raw_text, audience)
    kind = lesson_kind(raw_text)
    modifiers = {
        "exam_track": parsed.get("exam_track") or "",
        "subject_subgroup": parsed.get("subject_subgroup") or "",
        "delivery_mode": parsed.get("delivery_mode") or ("online" if kind == "online" else ""),
        "parser_diagnostics": parsed.get("parse_diagnostics") or "",
    }
    if kind == "nonlesson":
        subject = "Обед / перерыв" if raw_text.strip() == "🥨" else (parsed.get("subject") or raw_text.strip())
    else:
        subject = parsed.get("subject") or raw_text.splitlines()[0].strip()
    return {"subject": subject, "teacher_hint": parsed.get("teacher") or "", "room": parsed.get("room") or "",
            "activity_type": kind, "modifiers": modifiers, "parse_status": parsed.get("parse_status") or "ambiguous"}


def parse_schedule_matrix(tab: Mapping[str, Any], spreadsheet_title: str = "") -> list[dict[str, Any]]:
    """Parse the real school matrix, preserving formatting and merged context."""
    values = tab.get("values") or []
    header_index = _find_header(values)
    if header_index is None or not looks_like_schedule_matrix(values):
        return []
    width = max((len(row) for row in values), default=0)
    header = list(values[header_index]) + [""] * max(0, width - len(values[header_index]))
    owners: list[str] = [""] * width
    current_owner = ""
    for column in range(1, width):
        value = _cell_value(header[column]).strip()
        if _CLASS.fullmatch(value):
            current_owner = value
        elif value:
            current_owner = ""
        owners[column] = current_owner
    school_year = _school_year(spreadsheet_title, str(tab.get("title", "")))
    title_range = parse_date_range(str(tab.get("title", "")), school_year)
    current_date: str | None = None
    current_weekday: int | None = None
    lessons: list[dict[str, Any]] = []
    for row_index, row in enumerate(values[header_index + 1:], start=header_index + 1):
        if not row:
            continue
        first = _cell_value(row[0]).strip()
        marker = _parse_marker(first, school_year)
        weekday_hits = [_WEEKDAYS[_norm(_cell_value(cell))] for cell in row[1:] if _norm(_cell_value(cell)) in _WEEKDAYS]
        if marker:
            current_date = marker
            current_weekday = date.fromisoformat(marker).weekday()
            continue
        if len(weekday_hits) >= 2:
            current_weekday = Counter(weekday_hits).most_common(1)[0][0]
            current_date = None
            if title_range:
                start = date.fromisoformat(title_range[0])
                current_date = (start + timedelta(days=current_weekday - start.weekday())).isoformat()
            continue
        time_match = _TIME.fullmatch(first)
        if not time_match or current_weekday is None:
            continue
        start_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        end_time = f"{int(time_match.group(3)):02d}:{time_match.group(4)}"
        for column, cell in enumerate(row[1:], start=1):
            raw_text = _cell_value(cell).strip()
            if not raw_text:
                continue
            merge = _merge_for(tab, row_index, column)
            audience = owners[column] if column < len(owners) else ""
            merged_audiences: list[str] = []
            if merge:
                for merged_column in range(int(merge.get("startColumnIndex", column)), int(merge.get("endColumnIndex", column + 1))):
                    if merged_column < len(owners) and owners[merged_column] and owners[merged_column] not in merged_audiences:
                        merged_audiences.append(owners[merged_column])
            parsed = _parse_cell(raw_text, audience)
            coordinate = _a1(row_index, column)
            sheet_id = str(tab.get("sheet_id") or tab.get("sheetId") or "")
            slot_key = f"{current_weekday}|{start_time}|{column}"
            lessons.append({
                "record_key": f"{sheet_id}:{coordinate}", "slot_key": slot_key, "sheet_id": sheet_id,
                "tab_title": str(tab.get("title", "")), "source_cell": coordinate, "source_row": row_index,
                "source_column": column, "week_start": title_range[0] if title_range else None,
                "week_end": title_range[1] if title_range else None, "weekday": current_weekday,
                "lesson_date": current_date, "start_time": start_time, "end_time": end_time,
                "audience": audience, "merged_audiences": merged_audiences, "raw_text": raw_text,
                "source_color": _color(cell), "merge_data": merge, "raw_cell": dict(cell) if isinstance(cell, Mapping) else {"formattedValue": raw_text},
                **parsed,
            })
    return lessons


def diff_template_week(baseline: Iterable[Mapping[str, Any]], weekly: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    left = {str(item.get("slot_key") or item.get("record_key")): item for item in baseline}
    right = {str(item.get("slot_key") or item.get("record_key")): item for item in weekly}
    result: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        base, actual = left.get(key), right.get(key)
        actual_kind = (actual or {}).get("activity_type") or lesson_kind(str((actual or {}).get("value", "")))
        if base is None:
            change = "SPECIAL_EVENT" if actual_kind == "special_event" else "ADDED"
        elif actual is None:
            change = "CANCELLED"
        elif actual_kind == "special_event":
            change = "SPECIAL_EVENT"
        elif _norm(base.get("subject")) != _norm(actual.get("subject")):
            change = "REPLACED"
        elif any(_norm(base.get(field)) != _norm(actual.get(field)) for field in ("teacher_hint", "room")) or base.get("modifiers") != actual.get("modifiers"):
            change = "MODIFIED"
        else:
            change = "SAME_AS_BASELINE"
        result.append({"slot_key": key, "change": change, "baseline": base, "weekly": actual})
    return result


def _identity_aliases(identity: Mapping[str, Any]) -> set[str]:
    words = _norm(identity.get("display_name")).split()
    aliases = {_norm(identity.get("display_name"))}
    aliases.update(word for word in words if len(word) >= 3)
    if len(words) >= 2:
        aliases.add("".join(word[0] for word in words))
        aliases.add(words[0][0] + words[1][0])
    return {alias for alias in aliases if len(alias) >= 2}


def _explicit_teacher_candidates(raw_text: str, teacher_hint: str, identities: Sequence[Mapping[str, Any]], identity_mappings: Mapping[str, list[str]]) -> list[str]:
    candidates = set(identity_mappings.get(_norm(teacher_hint), []))
    target = _norm(teacher_hint) or _norm(raw_text)
    aliases_by_identity = {str(identity["id"]): _identity_aliases(identity) for identity in identities}
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for identity_id, aliases in aliases_by_identity.items():
        for alias in aliases:
            alias_owners[alias].add(identity_id)
    for alias, owners in alias_owners.items():
        if len(owners) == 1 and (target == alias or re.search(rf"(?:^| )({re.escape(alias)})(?: |$)", target)):
            candidates.update(owners)
    return sorted(candidates)


def _group_candidates(lesson: Mapping[str, Any], groups: Sequence[Mapping[str, Any]], group_mappings: Mapping[str, list[str]]) -> list[str]:
    audience = _norm(lesson.get("audience"))
    mapped = set(group_mappings.get(audience, []))
    subject = _norm(lesson.get("subject"))
    modifiers = lesson.get("modifiers") or {}
    subgroup = _norm(modifiers.get("subject_subgroup"))
    exam_track = _norm(modifiers.get("exam_track"))
    direct: list[Mapping[str, Any]] = []
    for group in groups:
        names = {_norm(group.get("name")), _norm(group.get("display_name")), _norm(group.get("base_class_name"))}
        if audience and audience in names:
            direct.append(group)
    narrowed = [group for group in direct if (not subject or not group.get("subject") or _norm(group.get("subject")) == subject)
                and (not subgroup or _norm(group.get("subject_subgroup")) == subgroup)
                and (not exam_track or _norm(group.get("exam_track")) == exam_track)]
    if len(narrowed) == 1:
        mapped.add(str(narrowed[0]["id"]))
    elif not subgroup and not exam_track:
        class_groups = [group for group in direct if _norm(group.get("group_type")) in {"class", "base class", "base_class"}]
        if len(class_groups) == 1:
            mapped.add(str(class_groups[0]["id"]))
        elif len(direct) == 1:
            mapped.add(str(direct[0]["id"]))
    return sorted(mapped)


def _mapping_indexes(mappings: Sequence[Mapping[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
    identity: dict[str, list[str]] = defaultdict(list)
    group: dict[str, list[str]] = defaultdict(list)
    subject: dict[str, str] = {}
    for mapping in mappings:
        key = _norm(mapping.get("external_key"))
        if mapping.get("mapping_type") == "identity" and mapping.get("identity_id"):
            identity[key].append(str(mapping["identity_id"]))
        elif mapping.get("mapping_type") == "group" and mapping.get("group_id"):
            group[key].append(str(mapping["group_id"]))
        elif mapping.get("mapping_type") == "subject" and mapping.get("canonical_value"):
            subject[key] = str(mapping["canonical_value"])
    return dict(identity), dict(group), subject


def _color_evidence(lessons: Sequence[dict[str, Any]], identities: Sequence[Mapping[str, Any]], identity_mappings: Mapping[str, list[str]]) -> dict[str, dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for lesson in lessons:
        if lesson.get("activity_type") not in {"lesson", "online", "practicum", "class_hour"} or not lesson.get("source_color"):
            continue
        candidates = _explicit_teacher_candidates(str(lesson.get("raw_text", "")), str(lesson.get("teacher_hint", "")), identities, identity_mappings)
        if len(candidates) == 1:
            counts[str(lesson["source_color"])][candidates[0]] += 1
    result: dict[str, dict[str, Any]] = {}
    for color, counter in counts.items():
        teacher_id, count = counter.most_common(1)[0]
        total = sum(counter.values())
        if count >= 2 and count / total >= 0.8:
            result[color] = {"teacher_id": teacher_id, "count": count, "total": total, "ratio": round(count / total, 3)}
    return result


def _resolve_lesson(lesson: dict[str, Any], identities: Sequence[Mapping[str, Any]], groups: Sequence[Mapping[str, Any]],
                    mappings: Sequence[Mapping[str, Any]], assignments: Sequence[Mapping[str, Any]],
                    color_map: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    identity_mappings, group_mappings, subject_mappings = _mapping_indexes(mappings)
    if _norm(lesson.get("subject")) in subject_mappings:
        lesson["subject"] = subject_mappings[_norm(lesson["subject"])]
    teacher_ids = _explicit_teacher_candidates(lesson.get("raw_text", ""), lesson.get("teacher_hint", ""), identities, identity_mappings)
    group_ids = _group_candidates(lesson, groups, group_mappings)
    evidence: dict[str, Any] = {"text_teacher_candidates": teacher_ids, "group_candidates": group_ids}
    assignment_ids: list[str] = []
    if len(group_ids) == 1:
        subject = _norm(lesson.get("subject"))
        assignment_ids = sorted({str(item["teacher_identity_id"]) for item in assignments
                                 if str(item.get("group_id")) == group_ids[0]
                                 and (not item.get("subject") or _norm(item.get("subject")) == subject)})
        if assignment_ids:
            evidence["teacher_assignment_candidates"] = assignment_ids
        if not teacher_ids and len(assignment_ids) == 1:
            teacher_ids = assignment_ids
            evidence["teacher_resolution"] = "canonical_teacher_assignment"
        elif len(teacher_ids) > 1 and len(set(teacher_ids) & set(assignment_ids)) == 1:
            teacher_ids = sorted(set(teacher_ids) & set(assignment_ids))
            evidence["teacher_resolution"] = "text_plus_teacher_assignment"
    color = str(lesson.get("source_color") or "")
    color_signal = color_map.get(color)
    conflict_reason = ""
    if color_signal:
        evidence["color_teacher"] = dict(color_signal)
        color_teacher = str(color_signal["teacher_id"])
        if not teacher_ids:
            teacher_ids = [color_teacher]
            evidence["teacher_resolution"] = "scoped_color_secondary"
        elif len(teacher_ids) == 1 and teacher_ids[0] != color_teacher:
            conflict_reason = "Text teacher conflicts with scoped background-color evidence"
    kind = lesson.get("activity_type")
    if kind == "nonlesson" or kind == "cancelled":
        status, confidence = "NON_LESSON", 1.0
    elif kind == "special_event":
        status, confidence = "SPECIAL_EVENT", 1.0
    elif conflict_reason or len(teacher_ids) > 1 or len(group_ids) > 1:
        status, confidence = "CONFLICT", 0.0
    elif teacher_ids and group_ids:
        status, confidence = "RESOLVED", 1.0 if lesson.get("teacher_hint") else 0.78
    elif teacher_ids or group_ids:
        status, confidence = "WARNING", 0.55
    else:
        status, confidence = "UNRESOLVED", 0.0
    reasons: list[str] = []
    if conflict_reason:
        reasons.append(conflict_reason)
    if not lesson.get("audience"):
        reasons.append("No owning class/group header")
    if kind not in {"nonlesson", "special_event", "cancelled"} and not teacher_ids:
        reasons.append("Teacher is unresolved")
    if kind not in {"nonlesson", "special_event", "cancelled"} and not group_ids:
        reasons.append("Audience is not mapped to a canonical group")
    return {**lesson, "resolved_identity_ids": teacher_ids, "resolved_group_ids": group_ids,
            "resolution_status": status, "confidence": confidence, "evidence": evidence,
            "issue_reason": "; ".join(reasons)}


def _json(database: Database, value: Any) -> Any:
    if database.database_url:
        from psycopg.types.json import Jsonb
        return Jsonb(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def refresh_schedule_pipeline(database: Database, spreadsheet_id: str, spreadsheet_title: str,
                              tabs: Sequence[Mapping[str, Any]], *, actor: Any | None = None) -> dict[str, Any]:
    """Persist an immutable snapshot and materialize current template/weekly lessons."""
    year = _school_year(spreadsheet_title, "")
    prepared: list[dict[str, Any]] = []
    for tab in tabs:
        matrix_like = looks_like_schedule_matrix(tab.get("values") or [])
        classification = classify_tab(str(tab.get("title", "")), schedule_like=matrix_like, year=year)
        lessons = parse_schedule_matrix(tab, spreadsheet_title) if classification in {"template", "weekly"} else []
        prepared.append({"sheet_id": str(tab.get("sheet_id") or tab.get("sheetId") or ""), "title": str(tab.get("title", "")),
                         "hidden": bool(tab.get("hidden")), "classification": classification,
                         "date_range": parse_date_range(str(tab.get("title", "")), year), "schedule_like": matrix_like,
                         "merges": list(tab.get("merges") or []), "lessons": lessons, "grid_cells": snapshot_cells(tab)})
    relevant = [tab for tab in prepared if tab["classification"] in {"template", "weekly"} or tab["schedule_like"]]
    snapshot_payload = [{"sheet_id": tab["sheet_id"], "title": tab["title"], "classification": tab["classification"],
                         "date_range": tab["date_range"], "merges": tab["merges"],
                         "cells": tab["grid_cells"]} for tab in relevant]
    source_fingerprint = fingerprint(snapshot_payload)
    with database.connection() as connection:
        source = database.execute(connection, """INSERT INTO school_sources(source_type, external_key, display_name, location_ref, authority_status, configuration)
            VALUES ('schedule', ?, ?, ?, 'authoritative', ?)
            ON CONFLICT(source_type, external_key) DO UPDATE SET display_name=excluded.display_name, location_ref=excluded.location_ref,
              configuration=excluded.configuration, updated_at=CURRENT_TIMESTAMP RETURNING id""",
            (f"google_sheets:{spreadsheet_id}:schedule", spreadsheet_title, spreadsheet_id, _json(database, {"spreadsheet_id": spreadsheet_id}))).fetchone()
        source_id = source["id"]
        previous = database.execute(connection, "SELECT id,fingerprint FROM school_source_snapshots WHERE source_id=? AND is_last_known_valid IS TRUE ORDER BY id DESC LIMIT 1", (source_id,)).fetchone()
        run = database.execute(connection, """INSERT INTO school_sync_runs(source_id,mode,status,idempotency_key,diagnostics)
            VALUES (?,'incremental','started',?,?) RETURNING id""",
            (source_id, f"{source_fingerprint}:{datetime.now(timezone.utc).isoformat(timespec='microseconds')}", _json(database, {"tabs": len(relevant)}))).fetchone()
        run_id = run["id"]
        for tab in prepared:
            if not tab["sheet_id"]:
                continue
            start, end = tab["date_range"] or (None, None)
            database.execute(connection, """INSERT INTO schedule_source_tabs(source_id,sheet_id,title,classification,week_start,week_end,updated_at)
                VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(source_id,sheet_id) DO UPDATE SET title=excluded.title,
                classification=excluded.classification,week_start=excluded.week_start,week_end=excluded.week_end,updated_at=CURRENT_TIMESTAMP""",
                (source_id, tab["sheet_id"], tab["title"], tab["classification"], start, end))
        if previous and str(previous["fingerprint"]) == source_fingerprint:
            database.execute(connection, "UPDATE school_sync_runs SET status='applied',finished_at=CURRENT_TIMESTAMP,diagnostics=? WHERE id=?",
                             (_json(database, {"status": "unchanged", "snapshot_id": previous["id"]}), run_id))
            return _overview_result(database, connection, source_id, previous["id"], run_id, "unchanged")
        reusable = database.execute(connection, "SELECT id FROM school_source_snapshots WHERE source_id=? AND fingerprint=? ORDER BY id DESC LIMIT 1", (source_id, source_fingerprint)).fetchone()
        if reusable:
            database.execute(connection, "UPDATE school_source_snapshots SET is_last_known_valid=FALSE,status='superseded' WHERE source_id=? AND is_last_known_valid IS TRUE", (source_id,))
            database.execute(connection, "UPDATE school_source_snapshots SET is_last_known_valid=TRUE,status='valid' WHERE id=?", (reusable["id"],))
            database.execute(connection, "UPDATE school_sync_runs SET status='applied',finished_at=CURRENT_TIMESTAMP,diagnostics=? WHERE id=?",
                             (_json(database, {"status": "reused_snapshot", "snapshot_id": reusable["id"]}), run_id))
            return _overview_result(database, connection, source_id, reusable["id"], run_id, "unchanged")
        previous_records: dict[str, tuple[str, str]] = {}
        if previous:
            rows = database.execute(connection, "SELECT record_key,fingerprint,source_ref FROM school_source_records WHERE snapshot_id=?", (previous["id"],)).fetchall()
            previous_records = {str(row["record_key"]): (str(row["fingerprint"]), str(row["source_ref"])) for row in rows}
            database.execute(connection, "UPDATE school_source_snapshots SET is_last_known_valid=FALSE,status='superseded' WHERE id=?", (previous["id"],))
        snapshot = database.execute(connection, """INSERT INTO school_source_snapshots(source_id,sync_run_id,previous_snapshot_id,fingerprint,observed_at,raw_payload,structural_payload,status,is_last_known_valid)
            VALUES (?,?,?,?,CURRENT_TIMESTAMP,?,?,'valid',TRUE) RETURNING id""",
            (source_id, run_id, previous["id"] if previous else None, source_fingerprint, _json(database, snapshot_payload), _json(database, snapshot_payload))).fetchone()
        snapshot_id = snapshot["id"]
        identities = [dict(row) for row in database.execute(connection, "SELECT id,display_name FROM identities WHERE kind='teacher' AND status='active'").fetchall()]
        groups = [dict(row) for row in database.execute(connection, "SELECT id,name,display_name,group_type,subject,base_class_name,subject_subgroup,exam_track FROM groups WHERE canonical IS TRUE").fetchall()]
        mappings = [dict(row) for row in database.execute(connection, """SELECT external_key,mapping_type,identity_id,group_id,canonical_value,evidence
            FROM school_source_mappings WHERE source_id=? AND status='confirmed' AND valid_until IS NULL""", (source_id,)).fetchall()]
        assignments = [dict(row) for row in database.execute(connection, "SELECT teacher_identity_id,group_id,subject FROM teacher_assignments WHERE active IS TRUE").fetchall()]
        identity_mappings, _, _ = _mapping_indexes(mappings)
        all_lessons = [lesson for tab in relevant for lesson in tab["lessons"]]
        color_map = _color_evidence(all_lessons, identities, identity_mappings)
        templates = [tab for tab in relevant if tab["classification"] == "template"]
        template = max(templates, key=lambda item: len(item["lessons"]), default=None)
        template_by_slot = {lesson["slot_key"]: lesson for lesson in (template["lessons"] if template else [])}
        materialized: list[dict[str, Any]] = []
        current_record_keys: set[str] = set()
        record_ids: dict[str, Any] = {}
        issue_count = 0
        for tab in relevant:
            if tab["classification"] == "unknown" and tab["schedule_like"]:
                database.execute(connection, "INSERT INTO school_resolution_issues(sync_run_id,issue_type,details,evidence) VALUES (?,'schedule_tab_period_unresolved',?,?)",
                                 (run_id, _json(database, {"title": tab["title"], "sheet_id": tab["sheet_id"]}), _json(database, {"schedule_like": True})))
                issue_count += 1
            lesson_coordinates = {lesson["record_key"] for lesson in tab["lessons"]}
            for cell in tab["grid_cells"]:
                record_payload = {key: value for key, value in cell.items() if key != "raw_cell"}
                # The immutable record identity includes raw values, notes and both
                # user-entered/effective formatting, not only the parsed projection.
                record_fingerprint = fingerprint(cell)
                record_key = cell["record_key"]
                current_record_keys.add(record_key)
                old = previous_records.get(record_key)
                change_kind = "new" if old is None else "unchanged" if old[0] == record_fingerprint else "changed"
                record = database.execute(connection, """INSERT INTO school_source_records(snapshot_id,record_key,fingerprint,source_ref,change_kind,parse_status,raw_payload,structural_payload)
                    VALUES (?,?,?,?,?,?,?,?) RETURNING id""", (snapshot_id, record_key, record_fingerprint,
                    f"{tab['title']}!{cell['source_cell']}", change_kind, "validated" if record_key in lesson_coordinates else "structural",
                    _json(database, cell.get("raw_cell") or {}), _json(database, record_payload))).fetchone()
                record_ids[record_key] = record["id"]
            for lesson in tab["lessons"]:
                lesson["source_record_id"] = record_ids.get(lesson["record_key"])
            if tab["classification"] == "weekly":
                weekly_diffs = diff_template_week(template_by_slot.values(), tab["lessons"])
                for diff in weekly_diffs:
                    if diff["weekly"] is not None:
                        item = dict(diff["weekly"])
                    else:
                        base = diff["baseline"]
                        item = {**base, "record_key": f"{tab['sheet_id']}:cancelled:{base['slot_key']}", "sheet_id": tab["sheet_id"],
                                "tab_title": tab["title"], "week_start": tab["date_range"][0] if tab["date_range"] else None,
                                "week_end": tab["date_range"][1] if tab["date_range"] else None,
                                "lesson_date": (date.fromisoformat(tab["date_range"][0]) + timedelta(days=int(base["weekday"]))).isoformat() if tab["date_range"] else None,
                                "raw_text": "", "activity_type": "cancelled", "source_cell": None,
                                "modifiers": {**(base.get("modifiers") or {}), "synthetic_cancelled": True}}
                    item["diff_status"] = diff["change"]
                    item["baseline_record_key"] = diff["baseline"].get("record_key") if diff["baseline"] else None
                    item["baseline_data"] = {key: value for key, value in diff["baseline"].items() if key != "raw_cell"} if diff["baseline"] else None
                    materialized.append(item)
            elif tab["classification"] == "template":
                for lesson in tab["lessons"]:
                    materialized.append({**lesson, "diff_status": "BASELINE", "baseline_record_key": None, "baseline_data": None})
        for old_key, (old_fingerprint, old_ref) in previous_records.items():
            if old_key not in current_record_keys:
                database.execute(connection, """INSERT INTO school_source_records(snapshot_id,record_key,fingerprint,source_ref,change_kind,parse_status,raw_payload,structural_payload)
                    VALUES (?,?,?,?, 'deleted','validated','{}','{}')""", (snapshot_id, old_key, old_fingerprint, old_ref))
        counts: Counter[str] = Counter()
        diffs: Counter[str] = Counter()
        for raw_lesson in materialized:
            lesson = _resolve_lesson(raw_lesson, identities, groups, mappings, assignments, color_map)
            counts[lesson["resolution_status"]] += 1
            diffs[lesson.get("diff_status") or ""] += 1
            database.execute(connection, """INSERT INTO schedule_lessons(source_snapshot_id,source_record_id,record_key,sheet_id,tab_title,version_kind,week_start,week_end,weekday,lesson_date,start_time,end_time,subject,teacher_hint,room,audience,activity_type,lesson_kind,modifiers,resolution_status,resolved_identity_ids,resolved_group_ids,confidence,evidence,source_cell,source_color,merge_data,baseline_record_key,baseline_data,diff_status,issue_reason,raw_payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, lesson.get("source_record_id"), lesson["record_key"], lesson["sheet_id"], lesson["tab_title"],
                "template" if lesson.get("diff_status") == "BASELINE" else "weekly", lesson.get("week_start"), lesson.get("week_end"), lesson.get("weekday"), lesson.get("lesson_date"), lesson.get("start_time"), lesson.get("end_time"),
                lesson.get("subject", ""), lesson.get("teacher_hint", ""), lesson.get("room", ""), lesson.get("audience", ""), lesson.get("activity_type", "lesson"), lesson.get("activity_type", "lesson"),
                _json(database, lesson.get("modifiers") or {}), lesson["resolution_status"], _json(database, lesson["resolved_identity_ids"]), _json(database, lesson["resolved_group_ids"]), lesson["confidence"],
                _json(database, lesson["evidence"]), lesson.get("source_cell"), lesson.get("source_color"), _json(database, lesson.get("merge_data")), lesson.get("baseline_record_key"),
                _json(database, lesson.get("baseline_data")) if lesson.get("baseline_data") else None, lesson.get("diff_status"), lesson.get("issue_reason"), _json(database, {key: value for key, value in lesson.items() if key != "raw_cell"})))
            if lesson["resolution_status"] in {"WARNING", "UNRESOLVED", "CONFLICT"}:
                database.execute(connection, "INSERT INTO school_resolution_issues(sync_run_id,source_record_id,issue_type,details,evidence) VALUES (?,?,?,?,?)",
                                 (run_id, lesson.get("source_record_id"), f"schedule_{lesson['resolution_status'].casefold()}",
                                  _json(database, {"reason": lesson["issue_reason"], "week_start": lesson.get("week_start"), "cell": lesson.get("source_cell"), "subject": lesson.get("subject")}),
                                  _json(database, lesson["evidence"])))
                issue_count += 1
        diagnostics = {"snapshot_id": snapshot_id, "parsed_weekly_lessons": sum(len(tab["lessons"]) for tab in relevant if tab["classification"] == "weekly"),
                       "template_lessons": len(template_by_slot), "materialized": len(materialized), "issues": issue_count,
                       "resolution": dict(counts), "diffs": dict(diffs), "color_mappings": color_map}
        database.execute(connection, "UPDATE school_sync_runs SET status='applied',finished_at=CURRENT_TIMESTAMP,diagnostics=? WHERE id=?", (_json(database, diagnostics), run_id))
        return _overview_result(database, connection, source_id, snapshot_id, run_id, "applied", diagnostics)


def _overview_result(database: Database, connection: Any, source_id: Any, snapshot_id: Any, run_id: Any,
                     status: str, diagnostics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    tabs = [dict(row) for row in database.execute(connection, "SELECT sheet_id,title,classification,week_start,week_end FROM schedule_source_tabs WHERE source_id=? ORDER BY COALESCE(week_start,'9999-12-31'),title", (source_id,)).fetchall()]
    return {"status": status, "auth_state": "connected", "source_id": source_id, "snapshot_id": snapshot_id,
            "sync_run_id": run_id, "tabs": tabs, "summary": dict(diagnostics or {})}
