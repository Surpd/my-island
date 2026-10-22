"""Generate a new Sheets grid from a frozen Schedule Studio payload.

No Google ingestion/parser, database writes, or live directory lookups occur here.
The same plan can be rendered to XLSX or sent through Sheets batchUpdate.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
import re
from typing import Any, Mapping


FORMAT_VERSION = 1
DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
# School teacher palette. Identity lookup uses directory labels, never cell text.
TEACHER_COLORS = {
    "юлия": "#274E13", "иа": "#CCCCCC", "иван": "#1155CC",
    "антон": "#674EA7", "ев": "#4285F4", "ангелина": "#A64D79",
    "игорь": "#FF0000", "дф": "#E06666", "сергей": "#F7F75B",
    "татьяна": "#00FF00", "лида": "#A4C2F4", "леонид": "#FF00FF",
    "марина": "#EA9999", "анастасия": "#FF6D01", "родион": "#F4CCCC",
    "анна": "#00FFFF", "тарас": "#B4A7D6", "дк": "#6AA84F",
    "дмитрий филиппов": "#E06666", "ирина анатольевна": "#CCCCCC",
    "елена викторовна": "#4285F4", "дмитрий к": "#6AA84F",
    "анна владимировна": "#00FFFF", "анна елисеева": "#00FFFF",
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def column_name(index: int) -> str:
    result = ""
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


def _label(group: Mapping[str, Any]) -> str:
    label = str(group.get("display_name") or group.get("name") or group["id"])
    return label.split("·", 1)[0].strip().removesuffix(" класс") if group.get("group_type") == "class" else label


def _class_key(group: Mapping[str, Any]) -> tuple[int, str]:
    label = _label(group)
    number = re.match(r"\d+", label)
    return (int(number[0]) if number else 999, label)


def freeze_week(source: Mapping[str, Any], week_start: str) -> dict[str, Any]:
    """Detached snapshot, including directory names. No references to a live template."""
    monday = date.fromisoformat(week_start)
    if monday.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    def safe(value: Any) -> Any:
        if isinstance(value, dict):
            result = {k: safe(v) for k, v in value.items() if k not in {
                "student_ids", "studentIds", "confirmedParticipantIds", "confirmed_participant_ids",
                "audience_snapshot", "raw_text", "raw_text_by_cell"}}
            participants = value.get("confirmedParticipantIds", value.get("confirmed_participant_ids"))
            if participants:
                result["confirmed_participants_fingerprint"] = _hash(sorted(set(map(str, participants))))
            return result
        return [safe(v) for v in value] if isinstance(value, list) else deepcopy(value)
    snapshot = safe(dict(source))
    snapshot["payload"]["week_start"] = week_start
    snapshot["format_version"] = FORMAT_VERSION
    snapshot["snapshot_id"] = f"mi-week-{week_start}-{_hash(snapshot)[:20]}"
    return snapshot


def build_sheet_plan(snapshot: Mapping[str, Any], *, title: str,
                     sheet_id: int, mapping_sheet_id: int, layout: str = "classic",
                     audience_evidence: Mapping[str, Any] | None = None,
                     existing_notes: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    if layout not in {"classic", "compact-v2", "readable-v3", "final-compact"}:
        raise ValueError("Unknown schedule sheet layout")
    payload = snapshot["payload"]
    groups = {str(g["id"]): g for g in snapshot["groups"]}
    teachers = {str(t["id"]): t for t in snapshot["teachers"]}
    classes = sorted((groups[str(cid)] for cid in payload["classes"]), key=_class_key)
    class_ids = [str(c["id"]) for c in classes]
    if len(class_ids) != len(set(class_ids)) or not class_ids:
        raise ValueError("Expected unique, resolved school classes")
    slots = payload["slots"]
    lessons = deepcopy(payload["lessons"])
    if len({str(l["id"]) for l in lessons}) != len(lessons):
        raise ValueError("Lesson IDs must be nonempty and unique")
    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lesson in lessons:
        if not lesson.get("id") or not str(lesson.get("subject") or "").strip():
            raise ValueError("Each lesson requires an ID and subject")
        day = int(lesson["day"])
        if day not in payload["days"]:
            raise ValueError(f"Lesson {lesson['id']} is outside exported days")
        period = int(lesson.get("period", -1))
        slot = slots[period] if 0 <= period < len(slots) else {}
        lesson["start"] = str(lesson.get("start") or slot.get("start") or "")[:5]
        lesson["end"] = str(lesson.get("end") or slot.get("end") or "")[:5]
        if not (re.fullmatch(r"\d{2}:\d{2}", lesson["start"]) and
                re.fullmatch(r"\d{2}:\d{2}", lesson["end"]) and lesson["start"] < lesson["end"]):
            raise ValueError(f"Invalid lesson interval: {lesson['id']}")
        targets = list(dict.fromkeys(cid for cid in (lesson.get("classIds") or [lesson.get("classId")]) if cid))
        independent_activity = lesson.get("activityType") in {"club", "elective", "event"}
        if (not targets and not independent_activity) or any(cid not in class_ids for cid in targets):
            raise ValueError(f"Unresolved class placement: {lesson['id']}")
        lesson["classIds"] = targets
        by_day[day].append(lesson)

    if layout in {"readable-v3", "final-compact"}:
        from backend.services.schedule_sheet_layout_v3 import build_readable_plan
        audit = None
        if layout == "final-compact":
            from backend.services.schedule_sheet_occurrences import logical_occurrences
            by_day, audit = logical_occurrences(by_day, groups, str(snapshot["payload"]["week_start"]))
        return build_readable_plan(snapshot, by_day, groups, teachers, classes, title=title,
                                   sheet_id=sheet_id, mapping_sheet_id=mapping_sheet_id,
                                   audience_evidence=audience_evidence or {}, existing_notes=existing_notes or {},
                                   final_compact=layout == "final-compact", logical_audit=audit)
    if layout == "compact-v2":
        return _compact_sheet_plan(snapshot, by_day, groups, teachers, classes,
                                   title=title, sheet_id=sheet_id, mapping_sheet_id=mapping_sheet_id)

    # Interval colouring keeps parallel lessons side by side for their full duration.
    # Allocate longer lessons first at equal starts, then stable IDs. No source coordinates.
    lanes: dict[tuple[int, str, str], int] = {}
    widths = dict.fromkeys(class_ids, 1)
    for day, items in by_day.items():
        for cid in class_ids:
            lane_ends: list[str] = []
            ordered = sorted((l for l in items if cid in l["classIds"]),
                             key=lambda l: (l["start"], -int(l["end"].replace(":", "")), str(l["id"])))
            for lesson in ordered:
                lane = next((i for i, end in enumerate(lane_ends) if end <= lesson["start"]), len(lane_ends))
                if lane == len(lane_ends):
                    lane_ends.append(lesson["end"])
                else:
                    lane_ends[lane] = lesson["end"]
                lanes[(day, cid, str(lesson["id"]))] = lane
            widths[cid] = max(widths[cid], len(lane_ends))
    offsets = {}
    column = 1
    for cid in class_ids:
        offsets[cid] = column
        column += widths[cid]
    cells: list[dict[str, Any]] = []
    mapping = []
    row_heights = {}
    day_rows = []
    snapshot_id = snapshot["snapshot_id"]

    def cell(r: int, c: int, text: str, *, h: int = 1, w: int = 1,
             bg: str = "#FFFFFF", fg: str = "#202124", bold: bool = False,
             note: str = "", size: int = 11, align: str = "CENTER") -> dict[str, Any]:
        item = dict(row=r, col=c, height=h, width=w, text=text, bg=bg, fg=fg,
                    bold=bold, note=note, size=size, align=align)
        cells.append(item)
        return item

    monday = date.fromisoformat(payload["week_start"])
    cell(0, 0, "Время", bold=True, bg="#E8EAED")
    for group in classes:
        cid = str(group["id"])
        cell(0, offsets[cid], _label(group), w=widths[cid], bold=True, size=18, bg="#E8EAED")
    source = snapshot.get("source") or {}
    clarification_count = sum(bool(l.get("needsClarification")) for l in lessons)
    cell(1, 0, f"{monday:%d.%m}–{monday + timedelta(days=4):%d.%m}", bold=True)
    cell(1, 1, f"My Island · {monday:%d.%m}–{monday + timedelta(days=4):%d.%m.%Y} · неделя из шаблона · требуют уточнения: {clarification_count} (примечания)",
         w=column-1, bold=True, align="LEFT", note=f"Снимок {snapshot_id}\nИсточник: {source.get('name', '')}, редакция {source.get('revision', '')}. "
         "Недельные замены из Google-образца не импортировались.")
    row_heights.update({0: 38, 1: 30})
    row = 2
    for day in sorted(payload["days"]):
        items = by_day[day]
        day_rows.append(row)
        cell(row, 0, f"{monday + timedelta(days=day):%d.%m}", bg="#D9D9D9", bold=True)
        cell(row, 1, DAY_NAMES[day], w=column-1, bg="#D9D9D9", bold=True, size=13, align="LEFT")
        row_heights[row] = 30
        row += 1
        boundaries = sorted({s[k] for s in slots for k in ("start", "end")} |
                            {l[k] for l in items for k in ("start", "end")})
        intervals = [(start, end) for start, end in zip(boundaries, boundaries[1:])
                     if any(s["start"] <= start and end <= s["end"] for s in slots)
                     or any(l["start"] <= start and end <= l["end"] for l in items)]
        row_for_start = {start: row + i for i, (start, _) in enumerate(intervals)}
        row_for_end = {end: row + i + 1 for i, (_, end) in enumerate(intervals)}
        for i, (start, end) in enumerate(intervals):
            cell(row + i, 0, f"{start}–{end}", bold=True, bg="#F3F4F6", size=10)
            row_heights[row + i] = 100
        occupied = {}
        for lesson in sorted(items, key=lambda l: (l["start"], str(l["id"]))):
            lid = str(lesson["id"])
            r1, r2 = row_for_start[lesson["start"]], row_for_end[lesson["end"]]
            tids = [str(t) for t in dict.fromkeys(lesson.get("teacherIds") or ([lesson["teacherId"]] if lesson.get("teacherId") else []))]
            gids = list(dict.fromkeys(lesson.get("groupIds") or ([lesson["groupId"]] if lesson.get("groupId") else [])))
            teacher_names = [str(teachers[t].get("display_name") or teachers[t].get("name") or t) if t in teachers else "Преподаватель не сопоставлен" for t in tids]
            group_names = [_label(groups[g]) if g in groups else "Группа не сопоставлена" for g in gids if g not in class_ids]
            special = lesson.get("specialMark") or lesson.get("kind")
            bg = "#FABF8F" if special == "lunch" else "#F3F4F6" if special == "no_lesson" else "#D9EAD3" if special == "event" else _teacher_fill(tids, teachers, teacher_names)
            rgb = [int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            fg = "#FFFFFF" if sum(v * k for v, k in zip(rgb, (.2126, .7152, .0722))) < .48 else "#202124"
            lines = [str(lesson["subject"])]
            if group_names:
                lines.append(", ".join(group_names))
            if teacher_names:
                lines.append(", ".join(teacher_names))
            room = str(lesson.get("room") or "").strip()
            if room:
                lines.append(room if re.match(r"каб|зал|онлайн", room, re.I) else f"каб. {room}")
            if lesson.get("audienceMode") == "remainder" or (lesson.get("audience") or {}).get("kind") == "available_slot":
                lines.append("Для свободных учеников")
            # Expand a lone lesson across that class's free lanes. Parallel records stay distinct.
            spans = []
            for cid in sorted(lesson["classIds"], key=class_ids.index):
                parallel = any(other["id"] != lid and cid in other["classIds"] and
                               other["start"] < lesson["end"] and lesson["start"] < other["end"] for other in items)
                c1 = offsets[cid] + (lanes[(day, cid, lid)] if parallel else 0)
                c2 = c1 + (1 if parallel else widths[cid])
                if spans and spans[-1][1] == c1:
                    spans[-1] = (spans[-1][0], c2, spans[-1][2] + [cid])
                else:
                    spans.append((c1, c2, [cid]))
            for c1, c2, cids in spans:
                for rr in range(r1, r2):
                    for cc in range(c1, c2):
                        if (rr, cc) in occupied:
                            raise ValueError(f"Cell collision: {lid} and {occupied[(rr, cc)]}")
                        occupied[(rr, cc)] = lid
                a1 = f"{column_name(c1)}{r1 + 1}:{column_name(c2 - 1)}{r2}"
                identity = {"format": FORMAT_VERSION, "snapshot_id": snapshot_id, "lesson_id": lid, "class_ids": cids}
                note = "MY_ISLAND:" + json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
                if lesson.get("needsClarification"):
                    note += "\nТребует уточнения: " + str(lesson.get("clarificationReason") or "Аудитория занятия")
                cell(r1, c1, "\n".join(lines), h=r2 - r1, w=c2 - c1, bg=bg, fg=fg, note=note)
                mapping.append({**identity, "sheet_id": sheet_id, "range": a1,
                                "content_hash": _hash(lesson), "needs_clarification": bool(lesson.get("needsClarification"))})
                needed_height = max(100, 21 * sum(max(1, (len(line) + 19 * (c2-c1)-1) // (19 * (c2-c1))) for line in lines))
                if r2-r1 == 1:
                    row_heights[r1] = max(row_heights[r1], needed_height)
        row += len(intervals)
    exported = {m["lesson_id"] for m in mapping}
    if exported != {str(l["id"]) for l in lessons}:
        raise ValueError("Not every source lesson was placed")
    return {"format_version": FORMAT_VERSION, "snapshot_id": snapshot_id, "title": title,
            "sheet_id": sheet_id, "mapping_sheet_id": mapping_sheet_id, "rows": row, "columns": column,
            "cells": cells, "mapping": mapping, "snapshot": deepcopy(dict(snapshot)),
            "row_heights": row_heights, "day_rows": day_rows, "class_columns": [
                {"id": cid, "label": _label(groups[cid]), "start": offsets[cid], "width": widths[cid]} for cid in class_ids],
            "summary": {"lessons": len(lessons), "placements": len(mapping), "classes": len(classes),
                        "clarifications": sum(bool(l.get("needsClarification")) for l in lessons),
                        "missing_teachers": sorted({t for l in lessons for t in (l.get("teacherIds") or []) if t not in teachers}),
                        "missing_groups": sorted({g for l in lessons for g in (l.get("groupIds") or []) if g not in groups})}}


_COMPACT_SUBJECTS = {
    "Английский язык": "Англ.", "Математика": "Матем.", "Русский язык": "Русский",
    "Обществознание": "Общ.", "История искусства": "Ист. иск.", "Литература": "Литер.",
    "Классный час": "Кл. час", "Лаборатория текста": "Лаб. текста",
    "Физическая культура": "Физкульт.", "Курс по выбору": "Курс",
    "Информатика": "Информ.", "Практикум": "практ.",
}
_COMPACT_TEACHERS = {
    "Дмитрий Филиппов": "ДФ", "Елена Викторовна": "ЕВ", "Ирина Анатольевна": "ИА",
    "Дмитрий К": "ДК", "Анна Владимировна": "Анна В.", "Анна Елисеева": "Анна Е.",
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _teacher_fill(teacher_ids: list[str], teachers: Mapping[str, Any], names: list[str]) -> str:
    """Use the ID-keyed shared UI color when present, with the legacy name fallback."""
    if teacher_ids:
        teacher = teachers.get(str(teacher_ids[0]))
        color = teacher.get("ui_color") if teacher else None
        if isinstance(color, str) and _HEX_COLOR.fullmatch(color):
            return color
    return TEACHER_COLORS.get(names[0].casefold(), "#E2E8F0") if names else "#E2E8F0"


def _compact_text(lesson: Mapping[str, Any], groups: Mapping[str, Any],
                  teachers: Mapping[str, Any], specific_classes: list[str]) -> tuple[str, str, str]:
    """Abbreviate display labels only; IDs, source snapshot and full notes stay intact."""
    subject = str(lesson["subject"])
    short = subject
    for original, replacement in _COMPACT_SUBJECTS.items():
        short = re.sub(re.escape(original), replacement, short, flags=re.I)
    short = re.sub(r"\s*·\s*", " · ", short).strip()
    gids = list(dict.fromkeys(lesson.get("groupIds") or ([lesson["groupId"]] if lesson.get("groupId") else [])))
    tags = []
    full_groups = []
    for gid in gids:
        group = groups.get(gid)
        if group is None:
            tags.append("Группа ?")
            continue
        if group.get("group_type") == "class":
            continue
        full_groups.append(_label(group))
        name = str(group.get("name") or "")
        if name.startswith("english:"):
            tag = name.split(":", 1)[1]
        elif name.startswith("grade9-math-"):
            tag = name.rsplit("-", 1)[1]
        elif name.startswith("grade7:split:"):
            tag = "7." + name.rsplit(":", 1)[1]
        else:
            parts = [part.strip() for part in _label(group).split("·")]
            # Keep the track or course direction, omit repeated subject/grade text.
            tag = " · ".join(p for p in parts if p.casefold() != subject.casefold()
                             and not re.fullmatch(r"\d+\s+класс", p))
            tag = tag.replace("Углублённая", "угл.").replace("Базовая", "база").replace("База", "база")
        if tag and tag.casefold() not in short.casefold() and tag not in tags:
            tags.append(tag)
    if tags:
        short += " " + "/".join(tags)
    if specific_classes:
        short += " · " + "/".join(specific_classes)
    tids = [str(t) for t in dict.fromkeys(lesson.get("teacherIds") or ([lesson["teacherId"]] if lesson.get("teacherId") else []))]
    full_teachers = [str(teachers[t].get("display_name") or teachers[t].get("name") or t)
                     if t in teachers else "Преподаватель ?" for t in tids]
    teacher_line = ", ".join(_COMPACT_TEACHERS.get(t, t) for t in full_teachers)
    room = str(lesson.get("room") or "").strip()
    room_short = re.sub(r"^каб(?:инет)?\.?\s*", "", room, flags=re.I)
    detail = " · ".join(filter(None, (teacher_line, room_short)))
    if lesson.get("audienceMode") == "remainder" or (lesson.get("audience") or {}).get("kind") == "available_slot":
        short += " · свободные"
    text = "\n".join(filter(None, (short, detail)))
    full = "\n".join(filter(None, (subject, "; ".join(full_groups), ", ".join(full_teachers), room)))
    special = lesson.get("specialMark") or lesson.get("kind")
    bg = ("#FABF8F" if special == "lunch" else "#F3F4F6" if special == "no_lesson" else
          "#D9EAD3" if special == "event" else _teacher_fill(tids, teachers, full_teachers))
    return text, full, bg


def _compact_sheet_plan(snapshot: Mapping[str, Any], by_day: Mapping[int, list[dict[str, Any]]],
                        groups: Mapping[str, Any], teachers: Mapping[str, Any], classes: list[Mapping[str, Any]],
                        *, title: str, sheet_id: int, mapping_sheet_id: int) -> dict[str, Any]:
    """One column per grade. Concurrent IDs stack inside each shared time band.

    Deduplication is by ID within a grade/band, never by subject, group overlap,
    or teacher. A multi-grade lesson retains every grade placement, combining
    adjacent equal rectangles when possible. Long lessons split only where a
    stacked parallel lesson prevents a continuous rectangular merge.
    """
    payload = snapshot["payload"]
    class_grades = {str(g["id"]): _class_key(g)[0] for g in classes}
    grade_classes: dict[int, set[str]] = defaultdict(set)
    for cid, grade in class_grades.items():
        if grade == 999:
            raise ValueError(f"Class has no grade number: {cid}")
        grade_classes[grade].add(cid)
    grades = sorted(grade_classes)
    offsets = {grade: i + 1 for i, grade in enumerate(grades)}
    columns = 1 + len(grades)
    snapshot_id = snapshot["snapshot_id"]
    cells: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    row_heights: dict[int, int] = {0: 28, 1: 22}
    day_rows = []
    slot_rows = []
    blocks = []
    monday = date.fromisoformat(payload["week_start"])

    def cell(row: int, col: int, text: str, *, height: int = 1, width: int = 1,
             bg: str = "#FFFFFF", fg: str = "#202124", bold: bool = False,
             note: str = "", size: int = 10, align: str = "LEFT") -> dict[str, Any]:
        result = dict(row=row, col=col, text=text, height=height, width=width,
                      bg=bg, fg=fg, bold=bold, note=note, size=size, align=align)
        cells.append(result)
        return result

    cell(0, 0, "Время", bg="#E8EAED", bold=True, align="CENTER")
    for grade in grades:
        cell(0, offsets[grade], str(grade), bg="#E8EAED", bold=True, size=14, align="CENTER")
    cell(1, 0, f"{monday:%d.%m}–{monday + timedelta(days=4):%d.%m}", size=9, align="CENTER")
    clarification_count = sum(bool(l.get("needsClarification")) for items in by_day.values() for l in items)
    cell(1, 1, f"My Island · неделя из шаблона · {clarification_count} уточнений в примечаниях",
         width=columns-1, size=9, note=f"Снимок {snapshot_id}. Полные названия и исходные данные сохранены в примечаниях и скрытой вкладке.")
    row = 2
    for day in sorted(payload["days"]):
        items = by_day.get(day, [])
        day_rows.append(row)
        cell(row, 0, f"{monday + timedelta(days=day):%d.%m}", bg="#4B5563", fg="#FFFFFF", bold=True, align="CENTER")
        cell(row, 1, DAY_NAMES[day], width=columns-1, bg="#4B5563", fg="#FFFFFF", bold=True, size=11)
        row_heights[row] = 25
        row += 1
        boundaries = sorted({s[k] for s in payload["slots"] for k in ("start", "end")} |
                            {l[k] for l in items for k in ("start", "end")})
        intervals = [(a, b) for a, b in zip(boundaries, boundaries[1:])
                     if any(s["start"] <= a and b <= s["end"] for s in payload["slots"])
                     or any(l["start"] <= a and b <= l["end"] for l in items)]
        for start, end in intervals:
            active = [l for l in items if l["start"] < end and start < l["end"]]
            per_grade = {grade: sorted((l for l in active if any(class_grades[c] == grade for c in l["classIds"])),
                                      key=lambda l: (str(l["id"]))) for grade in grades}
            count = max((len(ls) for ls in per_grade.values()), default=0) or 1
            slot_rows.append(row)
            cell(row, 0, f"{start}\n{end}", height=count, bg="#F3F4F6", bold=True, size=9, align="CENTER")
            for rr in range(row, row + count):
                row_heights[rr] = 34
            for grade in grades:
                grade_items = per_grade[grade]
                for i, lesson in enumerate(grade_items):
                    top = row + i * count // len(grade_items)
                    bottom = row + (i + 1) * count // len(grade_items)
                    cids = sorted(c for c in lesson["classIds"] if class_grades[c] == grade)
                    specific = [_label(groups[c]) for c in cids] if set(cids) != grade_classes[grade] else []
                    text, full, bg = _compact_text(lesson, groups, teachers, specific)
                    if lesson["start"] < start or end < lesson["end"]:
                        full += f"\nПолное время: {lesson['start']}–{lesson['end']}"
                    blocks.append(dict(row=top, end_row=bottom, col=offsets[grade], end_col=offsets[grade]+1,
                                       lesson=lesson, class_ids=cids, grade_numbers=[grade], start=start, end=end,
                                       text=text, full=full, bg=bg))
                    # Estimate wrapping using the actual font/column size; no font shrinking.
                    lines = sum(max(1, math.ceil(len(line) / 25)) for line in text.splitlines())
                    needed = math.ceil((lines * 15 + 6) / (bottom-top))
                    for rr in range(top, bottom):
                        row_heights[rr] = max(row_heights[rr], needed)
            row += count

    # Rejoin long activities across adjacent time bands when the column is free.
    vertical = []
    last_by_column = {}
    for block in sorted(blocks, key=lambda b: (b["col"], b["row"])):
        previous = last_by_column.get(block["col"])
        if (previous and previous["lesson"]["id"] == block["lesson"]["id"] and
                previous["end_row"] == block["row"] and previous["end"] == block["start"]):
            previous["end_row"] = block["end_row"]
            previous["end"] = block["end"]
        else:
            vertical.append(block)
            last_by_column[block["col"]] = block
    # A shared cross-grade activity may use one continuous coloured rectangle.
    merged = []
    for block in sorted(vertical, key=lambda b: (b["row"], b["end_row"], str(b["lesson"]["id"]), b["col"])):
        previous = merged[-1] if merged else None
        if (previous and previous["lesson"]["id"] == block["lesson"]["id"] and
                previous["row"] == block["row"] and previous["end_row"] == block["end_row"] and
                previous["end_col"] == block["col"]):
            previous["end_col"] = block["end_col"]
            previous["class_ids"] += block["class_ids"]
            previous["grade_numbers"] += block["grade_numbers"]
        else:
            merged.append(block)
    for block in merged:
        lesson = block["lesson"]
        # A merge may combine a specific class in one grade with a whole other
        # grade. Rebuild the caption for that combined audience, not just its left edge.
        partial = any(set(block["class_ids"]) & grade_classes[g] != grade_classes[g]
                      for g in block["grade_numbers"])
        specific = [_label(groups[c]) for c in block["class_ids"]] if partial else []
        block["text"], block["full"], _ = _compact_text(lesson, groups, teachers, specific)
        if block["start"] != lesson["start"] or block["end"] != lesson["end"]:
            block["full"] += f"\nПолное время: {lesson['start']}–{lesson['end']}"
        identity = {"format": FORMAT_VERSION, "snapshot_id": snapshot_id, "lesson_id": str(lesson["id"]),
                    "class_ids": block["class_ids"]}
        note = "MY_ISLAND:" + json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        note += "\n" + block["full"]
        if lesson.get("needsClarification"):
            note += "\nТребует уточнения: " + str(lesson.get("clarificationReason") or "Аудитория занятия")
        rgb = [int(block["bg"][i:i+2], 16)/255 for i in (1, 3, 5)]
        fg = "#FFFFFF" if sum(v*k for v, k in zip(rgb, (.2126, .7152, .0722))) < .48 else "#202124"
        cell(block["row"], block["col"], block["text"], height=block["end_row"]-block["row"],
             width=block["end_col"]-block["col"], bg=block["bg"], fg=fg, note=note)
        a1 = f"{column_name(block['col'])}{block['row']+1}:{column_name(block['end_col']-1)}{block['end_row']}"
        mapping.append({**identity, "sheet_id": sheet_id, "range": a1, "content_hash": _hash(lesson),
                        "needs_clarification": bool(lesson.get("needsClarification")),
                        "grade_numbers": block["grade_numbers"], "segment_start": block["start"], "segment_end": block["end"]})
    # Check source grade/time coverage independently of rectangle merging.
    for day, items in by_day.items():
        for lesson in items:
            lesson_grades = {class_grades[c] for c in lesson["classIds"]}
            placed = [m for m in mapping if m["lesson_id"] == str(lesson["id"])]
            for grade in lesson_grades:
                segments = sorted((m["segment_start"], m["segment_end"]) for m in placed if grade in m["grade_numbers"])
                cursor = lesson["start"]
                for start, end in segments:
                    if start != cursor:
                        raise ValueError(f"Duplicate or missing grade/time segment: {lesson['id']}")
                    cursor = end
                if cursor != lesson["end"]:
                    raise ValueError(f"Incomplete lesson coverage: {lesson['id']}")
    occupied = set()
    for c in cells:
        for rr in range(c["row"], c["row"]+c["height"]):
            for cc in range(c["col"], c["col"]+c["width"]):
                if (rr, cc) in occupied:
                    raise ValueError("Compact cell collision")
                occupied.add((rr, cc))
    all_lessons = [l for items in by_day.values() for l in items]
    return {"format_version": FORMAT_VERSION, "layout": "compact-v2", "snapshot_id": snapshot_id, "title": title,
            "sheet_id": sheet_id, "mapping_sheet_id": mapping_sheet_id, "rows": row, "columns": columns,
            "cells": cells, "mapping": mapping, "snapshot": deepcopy(dict(snapshot)), "row_heights": row_heights,
            "column_widths": [80] + [148]*len(grades), "day_rows": day_rows, "slot_rows": slot_rows,
            "class_columns": [{"id": str(g), "label": str(g), "start": offsets[g], "width": 1} for g in grades],
            "summary": {"lessons": len(all_lessons), "placements": len(mapping), "classes": len(classes),
                        "grades": len(grades), "width_px": 80+148*len(grades), "clarifications": clarification_count,
                        "missing_teachers": sorted({t for l in all_lessons for t in (l.get("teacherIds") or []) if t not in teachers}),
                        "missing_groups": sorted({g for l in all_lessons for g in (l.get("groupIds") or []) if g not in groups})}}


def sheets_requests(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Add-only atomic batch: existing worksheets are never copied or edited."""
    sid, mid = plan["sheet_id"], plan["mapping_sheet_id"]
    def grid(r1: int, c1: int, r2: int, c2: int) -> dict[str, int]:
        return dict(sheetId=sid, startRowIndex=r1, endRowIndex=r2, startColumnIndex=c1, endColumnIndex=c2)
    def color(value: str) -> dict[str, float]:
        return dict(zip(("red", "green", "blue"), (int(value[i:i+2], 16) / 255 for i in (1, 3, 5))))
    internal_rows = [["snapshot_id", plan["snapshot_id"]], ["format_version", FORMAT_VERSION],
                     ["source", json.dumps(plan["snapshot"].get("source"), ensure_ascii=False)],
                     ["week_start", plan["snapshot"]["payload"]["week_start"]],
                     ["directory", json.dumps({k: plan["snapshot"][k] for k in ("groups", "teachers")}, ensure_ascii=False)],
                     ["lesson_id", "lesson_snapshot", "placements"]]
    for lesson in plan["snapshot"]["payload"]["lessons"]:
        source_id = str(lesson["id"])
        placements = []
        for m in plan["mapping"]:
            if source_id not in m.get("source_lesson_ids", [m["lesson_id"]]):
                continue
            entry = dict(m)
            if "source_hashes" in entry:
                entry["lesson_id"] = source_id
                entry["content_hash"] = entry["source_hashes"][source_id]
            placements.append(entry)
        internal_rows.append([source_id, json.dumps(lesson, ensure_ascii=False), json.dumps(placements, ensure_ascii=False)])
    if plan.get("layout") in {"readable-v3", "final-compact"}:
        context = json.dumps(plan["presentation_context"], ensure_ascii=False)
        for offset in range(0, len(context), 25000):
            internal_rows.append([f"presentation_context:{offset//25000}", context[offset:offset+25000],
                                  plan["presentation_fingerprint"]])
    requests = [{"addSheet": {"properties": {"sheetId": sid, "title": plan["title"], "gridProperties": {
        "rowCount": plan["rows"], "columnCount": plan["columns"], "frozenRowCount": 2,
        "frozenColumnCount": 1, "hideGridlines": True}}}},
        {"addSheet": {"properties": {"sheetId": mid, "title": f"_MI {sid}", "hidden": True,
                                     "gridProperties": {"rowCount": len(internal_rows), "columnCount": 3}}}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                       "properties": {"pixelSize": 122}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 1, "endIndex": plan["columns"]},
                                       "properties": {"pixelSize": 166}, "fields": "pixelSize"}}]
    matrix = [[{} for _ in range(plan["columns"])] for _ in range(plan["rows"])]
    for c in plan["cells"]:
        matrix[c["row"]][c["col"]] = {"userEnteredValue": {"stringValue": c["text"]}, "note": c["note"],
            "userEnteredFormat": {"backgroundColor": color(c["bg"]), "textFormat": {"fontFamily": "Arial", "fontSize": c["size"], "bold": c["bold"], "foregroundColor": color(c["fg"])},
                                  "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE", "horizontalAlignment": c["align"]}}
        if c["height"] > 1 or c["width"] > 1:
            requests.append({"mergeCells": {"range": grid(c["row"], c["col"], c["row"]+c["height"], c["col"]+c["width"]), "mergeType": "MERGE_ALL"}})
    requests.insert(4, {"updateCells": {"start": {"sheetId": sid}, "rows": [{"values": r} for r in matrix], "fields": "userEnteredValue,userEnteredFormat,note"}})
    requests.append({"updateCells": {"start": {"sheetId": mid}, "rows": [{"values": [{"userEnteredValue": {"stringValue": str(v)}} for v in r]} for r in internal_rows], "fields": "userEnteredValue"}})
    height_runs: list[list[int]] = []
    for row, height in sorted((int(row), height) for row, height in plan["row_heights"].items()):
        if height_runs and height_runs[-1][1] == row and height_runs[-1][2] == height:
            height_runs[-1][1] = row+1
        else:
            height_runs.append([row, row+1, height])
    for start, end, height in height_runs:
        requests.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": start, "endIndex": end}, "properties": {"pixelSize": height}, "fields": "pixelSize"}})
    for c in plan["class_columns"]:
        requests.append({"updateBorders": {"range": grid(0, c["start"], plan["rows"], c["start"]+c["width"]), "left": {"style": "SOLID", "color": color("#9AA0A6")}, "right": {"style": "SOLID", "color": color("#9AA0A6")}}})
    if plan.get("layout") == "compact-v2":
        requests[2]["updateDimensionProperties"]["properties"]["pixelSize"] = plan["column_widths"][0]
        requests[3]["updateDimensionProperties"]["properties"]["pixelSize"] = plan["column_widths"][1]
        for c in plan["cells"]:
            if c["note"].startswith("MY_ISLAND:"):
                matrix[c["row"]][c["col"]]["userEnteredFormat"]["borders"] = {
                    edge: {"style": "SOLID", "color": color("#B6BEC9")} for edge in ("top", "bottom")}
        for request in requests:
            if "updateBorders" in request:
                for edge in ("left", "right"):
                    request["updateBorders"][edge] = {"style": "SOLID_MEDIUM", "color": color("#6B7280")}
        for row in plan["slot_rows"]:
            requests.append({"updateBorders": {"range": grid(row, 0, row+1, plan["columns"]),
                                               "top": {"style": "SOLID", "color": color("#9CA3AF")}}})
        for row in plan["day_rows"]:
            requests.append({"updateBorders": {"range": grid(row, 0, row+1, plan["columns"]),
                "top": {"style": "SOLID_MEDIUM", "color": color("#374151")},
                "bottom": {"style": "SOLID_MEDIUM", "color": color("#374151")}}})
    if plan.get("layout") in {"readable-v3", "final-compact"}:
        # Narrow native subcolumns share a fixed width budget, rather than
        # multiplying the width when simultaneous activities increase.
        requests[2]["updateDimensionProperties"]["properties"]["pixelSize"] = plan["column_widths"][0]
        requests[3]["updateDimensionProperties"]["properties"]["pixelSize"] = 1
        run_start = 1
        while run_start < plan["columns"]:
            width = plan["column_widths"][run_start]
            run_end = run_start + 1
            while run_end < plan["columns"] and plan["column_widths"][run_end] == width:
                run_end += 1
            requests.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS",
                "startIndex": run_start, "endIndex": run_end}, "properties": {"pixelSize": width}, "fields": "pixelSize"}})
            run_start = run_end
        for c in plan["cells"]:
            if not c["note"].startswith("MY_ISLAND:"):
                continue
            requests.append({"updateBorders": {"range": grid(c["row"], c["col"], c["row"]+c["height"], c["col"]+c["width"]),
                **{edge: {"style": "SOLID", "color": color("#AEB8C5")} for edge in ("top", "bottom", "left", "right")}}})
        for slot in plan["slot_ranges"]:
            requests.append({"updateBorders": {"range": grid(slot["start"], 0, slot["end"], plan["columns"]),
                "top": {"style": "SOLID_MEDIUM", "color": color("#8996A8")},
                "bottom": {"style": "SOLID", "color": color("#B8C0CA")}}})
        for row in plan["day_rows"]:
            requests.append({"updateBorders": {"range": grid(row, 0, row+1, plan["columns"]),
                "top": {"style": "SOLID_MEDIUM", "color": color("#344054")},
                "bottom": {"style": "SOLID_MEDIUM", "color": color("#344054")}}})
    return requests
