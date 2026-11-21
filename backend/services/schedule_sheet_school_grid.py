"""Compact school day overviews with linked lesson details on the same worksheet."""
from collections import defaultdict
from math import ceil, lcm
from datetime import date, timedelta
import re

from backend.services.canonical_schedule import canonical_group_grade
from backend.services.schedule_experiments import resolve_payload_day_mode
from backend.services.schedule_sheet_export import DAY_NAMES, FORMAT_VERSION, _class_key, _hash, _label, _teacher_fill
from backend.services.schedule_sheet_layout_v3 import activity_semantics

GRID_LAYOUT = "school-grid"
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")



def week_caption(week, days):
    first = date.fromisoformat(week)
    last = first + timedelta(days=max(4, max(days, default=4)))
    if first.month == last.month:
        return f"{first.day}–{last.day} {_MONTHS[last.month-1]}"
    return f"{first.day} {_MONTHS[first.month-1]} – {last.day} {_MONTHS[last.month-1]}"


def subgroup_rank(group):
    name = str(group.get("name") or "")
    split = re.fullmatch(r"(?:grade[0-9]+:split:|english:)([1-9][0-9]*)", name)
    if split:
        return int(split[1]) - 1
    math = re.fullmatch(r"grade9-math-([ABC])", name)
    if math:
        return "ABC".index(math[1])
    value = str(group.get("subject_subgroup") or "").strip().upper()
    if re.fullmatch(r"[1-9][0-9]*", value):
        return int(value) - 1
    if re.fullmatch(r"[A-Z]", value):
        return ord(value) - ord("A")
    return None


def group_order(lesson, groups):
    audience = lesson.get("audience") or {}
    assignment = (lesson.get("canonical") or {}).get("assignment") or {}
    metadata = assignment.get("metadata") or {}
    linked = set((lesson.get("groupIds") or []) + (audience.get("group_ids") or []) +
                 (assignment.get("canonical_group_ids") or []) +
                 ((metadata.get("audience") or {}).get("canonical_group_ids") or []) +
                 ((metadata.get("audience_rule") or {}).get("group_ids") or []))
    if lesson.get("groupId"):
        linked.add(lesson["groupId"])
    ids = sorted(key for key in linked if groups.get(key, {}).get("group_type") != "class")
    rank = subgroup_rank(groups.get(ids[0], {})) if len(ids) == 1 else None
    return (rank if rank is not None else 100, tuple(ids or sorted(lesson["classIds"])))


TIME_WIDTH = 86
GRADE_WIDTHS = {"5": 106, "6": 106, "7": 192, "8": 144, "9": 220, "10": 165, "11": 177, None: 104}
GRID_WIDTH = 1258
_ACTIVITY_TYPES = {"club": "Кружок", "optional_activity": "Кружок", "elective": "Курс по выбору",
                   "practice": "Практикум", "consultation": "Консультация", "laboratory": "Лаборатория"}
_SHORT_TITLES = {
    "Русский язык": "Русский", "Английский язык": "Английский",
    "Проектная мастерская": "Проектная", "Исследование окружающего мира": "Окружающий мир",
    "Разговорная практика": "Разговорная", "История искусства": "История искусств",
}
_MODE_LABELS = {"online": "Онлайн", "sdep": "СДЭП", None: "Режим не согласован"}


def saved_notes(lesson, existing_notes):
    return list(dict.fromkeys(
        clean for sid in lesson["_source_ids"] for note in existing_notes.get(sid, [])
        if (clean := "\n".join(line for line in note.splitlines() if not line.lstrip().startswith("MY_ISLAND:")).strip())
    ))


def describe_lesson(lesson, groups, teachers, existing_notes):
    audience = lesson.get("audience") or {}
    group_ids = list(dict.fromkeys([*(audience.get("group_ids") or []), *(lesson.get("groupIds") or []),
                                   *([lesson["groupId"]] if lesson.get("groupId") else [])]))
    instructional = [i for i in group_ids if groups.get(i, {}).get("group_type") != "class"]
    labels = [_label(groups[i]) for i in lesson["classIds"]]
    group_labels = [_label(groups[i]) if i in groups else "Группа требует уточнения" for i in instructional]
    teacher_ids = list(dict.fromkeys(filter(None, [lesson.get("teacherId"), *(lesson.get("teacherIds") or [])])))
    staff = [str(teachers[i].get("display_name") or teachers[i].get("name") or "Преподаватель")
             if i in teachers else "Преподаватель требует уточнения" for i in teacher_ids]
    activity = activity_semantics(lesson)
    name = activity["display_name"] or lesson["subject"]
    kind = lesson.get("activityType")
    club = activity["section"] == "clubs"
    formal_type = _ACTIVITY_TYPES.get(kind or activity["activity_type"])
    if kind in {"practice", "consultation", "laboratory"}:
        prefix, _, title = name.partition(" · ")
        if title and prefix in {formal_type, "Консультация по желанию"}:
            name = title
    full_name = f"{formal_type} · {name}" if formal_type else name
    room = lesson.get("room") or lesson.get("place") or ""
    overview_name = full_name if kind in {"practice", "consultation", "laboratory"} else name
    if audience.get("kind") == "available_slot" or lesson.get("audienceMode") == "remainder":
        group_labels.append("Для свободных учеников")
    elif audience.get("kind") == "students":
        group_labels.append("Индивидуальный состав")
    reasons = []
    if any(i not in groups for i in instructional):
        reasons.append("Группа отсутствует в справочнике")
    if any(i not in teachers for i in teacher_ids):
        reasons.append("Преподаватель отсутствует в справочнике")
    if activity["section"] == "activity_review":
        reasons.append("Уточните тип дополнительного занятия")
    if not reasons and (lesson.get("importError") or lesson.get("needsClarification") and lesson.get("specialMark") != "lunch"):
        source_reason = str(lesson.get("clarificationReason") or "").casefold()
        if re.search(r"групп|состав|аудитор|участник|класс", source_reason):
            reasons.append("Уточните класс или состав группы")
        elif re.search(r"врем|день|слот", source_reason):
            reasons.append("Уточните день или время занятия")
        elif re.search(r"учител|педагог|преподавател", source_reason):
            reasons.append("Уточните преподавателя")
        elif re.search(r"кабинет|помещен", source_reason):
            reasons.append("Уточните кабинет")
        elif lesson.get("importError"):
            reasons.append("Ошибка сопоставления при импорте")
        else:
            reasons.append("В черновике сохранена пометка о проверке. Причина не указана.")
    bg = _teacher_fill(teacher_ids, teachers, staff)
    rgb = [int(bg[i:i+2], 16) for i in (1, 3, 5)]
    bg = "#" + "".join(f"{round(value*.21+255*.79):02X}" for value in rgb)
    if lesson.get("specialMark") == "lunch":
        bg = "#F6E5CD"
    if (activity["participation"] == "opt_in" or kind == "consultation") and bg == "#F7F8FA":
        bg = "#EDE8FA"
    interval = f"{lesson['start']}–{lesson['end']}"
    return dict(name=full_name, overview_name=overview_name, classes=", ".join(labels) or "Общее занятие",
                groups=", ".join(group_labels), staff=", ".join(staff), room=room, bg=bg, club=club, elective=activity["activity_type"] == "elective",
                group_ids=group_ids, teacher_ids=teacher_ids, interval=interval, notice=" · ".join(reasons),
                notes=saved_notes(lesson, existing_notes))


def wrapped_lines(text, width, size):
    """Estimate native Arial wrapping to budget row height without clipping the values."""
    limit = max(1, width - 5)
    lines, used = 1, 0
    for word in text.split():
        pixels = sum(.9 if char in "МШЩЖЮФЫWMW@%" else .63 if char.isupper()
                     else .34 if char in " .,:;|il!·" else .52 for char in word) * size * 4 / 3
        if used and used + pixels + size * .4 > limit:
            lines += 1
            used = 0
        if pixels > limit:
            lines += ceil(pixels / limit) - 1
            used = pixels % limit
        else:
            used += pixels + size * .4
    return lines


def card_caption(info, width, *, club, stacked, online_day=False):
    size = 8 if stacked or width < 160 else 9
    room = info["room"]
    if online_day and room == "Онлайн":
        room = ""  # The grade's day band already labels the format.
    room_number = re.fullmatch(r"каб\.?\s*(\d+\w?)", room, re.IGNORECASE)
    if room_number:
        room = room_number[1]
    staff = info["staff"]
    if wrapped_lines(" · ".join(filter(None, (staff, room))), width, size) > 1 and " " in staff:
        primary = staff.split(",")[0].split()
        last = primary[-1]
        staff = f"Пед. {last}" if last.isdigit() else primary[0]
    metadata = " · ".join(filter(None, (staff, room)))
    title = info["overview_name"]
    time = f"{info['interval'].split('–')[0]} " if club else ""
    if wrapped_lines(time + title, width, size) > 1:
        title = _SHORT_TITLES.get(title, title)
    if width < 50:
        title = {"Окружающий мир": "Окр. мир", "Разговорная": "Разг. практика"}.get(title, title)
    if club:
        title = time + title
    if stacked and wrapped_lines(f"{title} · {metadata}", width, size) == 1:
        lines = [" · ".join(filter(None, (title, metadata)))]
    else:
        lines = [line for line in (title, metadata) if line]
    height = 4 + ceil(size * 1.43) * sum(wrapped_lines(line, width, size) for line in lines)
    return "\n".join(lines), size, height


def build_school_grid(snapshot, by_day, groups, teachers, classes, *, title, sheet_id,
                      mapping_sheet_id, existing_notes, logical_audit):
    payload = snapshot["payload"]
    source = snapshot.get("source") or {}
    week = payload.get("week_start")
    monday = date.fromisoformat(week) if week else None
    grade_by_class = {str(c["id"]): canonical_group_grade(c) or _label(c) for c in sorted(classes, key=_class_key)}
    grades = list(dict.fromkeys(grade_by_class.values()))
    extras_column = any(not lesson["classIds"] for lessons in by_day.values() for lesson in lessons)
    if extras_column:
        grades.append(None)
    bell_times = {str(slot["start"])[:5]: str(slot["end"])[:5] for slot in payload["slots"]}
    info = {str(lesson["id"]): describe_lesson(lesson, groups, teachers, existing_notes)
            for lessons in by_day.values() for lesson in lessons}
    # Lanes divide the budget of their grade. Only links and source IDs connect
    # repeated display cards for cross-class and multi-period activities.
    rows_by_slot = defaultdict(lambda: defaultdict(list))
    clubs_by_day = defaultdict(lambda: defaultdict(list))
    for day, lessons in by_day.items():
        for lesson in lessons:
            club = info[str(lesson["id"])]["club"]
            targets = {grade_by_class[c] for c in lesson["classIds"]} or {None}
            if club:
                for grade in targets:
                    clubs_by_day[day][grade].append((lesson, False))
                continue
            starts = [lesson["start"], *(bell for bell in bell_times if lesson["start"] < bell < lesson["end"])]
            for index, start in enumerate(starts):
                for grade in targets:
                    rows_by_slot[day, start][grade].append((lesson, index > 0))
    seats = {}
    for grade in grades:
        counts = {sum(not info[str(lesson["id"])]["elective"] for lesson, _ in section[grade])
                  for section in rows_by_slot.values() if grade in section} - {0} or {1}
        # Equal-width results for 2 and 3 visible groups in the same grade.
        common = lcm(*counts)
        seats[grade] = common if common <= 12 else max(counts)
    columns_by_grade = {}
    widths = [TIME_WIDTH]
    requested_width = sum(GRADE_WIDTHS.get(grade, 120) for grade in grades)
    for grade in grades:
        budget = GRADE_WIDTHS.get(grade, 120)
        if requested_width > GRID_WIDTH - TIME_WIDTH:
            budget = budget * (GRID_WIDTH - TIME_WIDTH) // requested_width
        columns_by_grade[grade] = len(widths)
        count = seats[grade]
        widths.extend([(part + 1) * budget // count - part * budget // count for part in range(count)])
    columns = len(widths)
    cells, mapping, day_rows, day_labels, slot_ranges, heights = [], {}, [], {}, [], {}
    draft_id = source.get("draft_id")

    def cell(row, col, text, *, width=1, bg="#FFFFFF", fg="#202B3B", bold=False, note="", size=9, align="LEFT", height=1, **links):
        entry = dict(row=row, col=col, width=width, height=height, text=text, bg=bg,
                     fg=fg, bold=bold, note=note, size=size, align=align, **links)
        cells.append(entry)
        return entry

    days = sorted(payload["days"])
    cell(0, 0, "Время", bg="#243A51", fg="#FFFFFF", bold=True)
    for grade, col in columns_by_grade.items():
        label = "Общие" if grade is None else f"{grade} класс" if grade.isdecimal() else grade
        cell(0, col, label, width=seats[grade], bg="#243A51", fg="#FFFFFF", bold=True, align="CENTER")
    heights[0] = 25
    row = 1
    linked_cards = defaultdict(list)
    for day in days:
        day_rows.append(row)
        caption = DAY_NAMES[day] + (f" · {monday + timedelta(days=day):%d.%m}" if monday else "")
        day_labels[row] = caption
        modes = {grade: _MODE_LABELS.get(resolve_payload_day_mode(payload, grade, day, grade_by_class)[0], "")
                 for grade in grades if grade is not None}
        if any(modes.values()):
            cell(row, 0, caption, bg="#DCE7F1", bold=True, size=8)
            for grade, col in columns_by_grade.items():
                label = modes.get(grade, "")
                cell(row, col, label, width=seats[grade], bg="#D7EAE9" if label else "#DCE7F1",
                     bold=True, align="CENTER")
            heights[row] = max(22, 4 + 12 * wrapped_lines(caption, TIME_WIDTH, 8))
            for grade, label in modes.items():
                if label:
                    col = columns_by_grade[grade]
                    width = sum(widths[col:col+seats[grade]])
                    heights[row] = max(heights[row], 4 + 14 * wrapped_lines(label, width, 9))
        else:
            cell(row, 0, DAY_NAMES[day][:2], bg="#DCE7F1", bold=True)
            cell(row, 1, caption, width=columns-1, bg="#DCE7F1", bold=True)
            heights[row] = 22
        row += 1
        for club in (False, True):
            if club:
                if not clubs_by_day[day]:
                    continue
                cell(row, 0, "", bg="#E7DFF4", bold=True, size=8)
                cell(row, 1, "Кружки", width=columns-1, bg="#E7DFF4", bold=True)
                heights[row] = 20
                row += 1
                windows = [("", clubs_by_day[day])]
            else:
                starts = set(bell_times) | {start for weekday, start in rows_by_slot if weekday == day}
                windows = [(start, rows_by_slot.get((day, start), {})) for start in sorted(starts)]
            for start, sections in windows:
                ends = {lesson["end"] for entries in sections.values() for lesson, _ in entries}
                stop = bell_times.get(start) or (next(iter(ends)) if len(ends) == 1 else "")
                time = f"{start}–{stop}" if stop else f"с {start}"
                # A grade uses one horizontal row for parallel lessons, then one
                # full-width row per choice within the same time band.
                placements = []
                height = 1
                for grade, entries in sorted(sections.items(), key=lambda section: columns_by_grade[section[0]]):
                    ordered = sorted(entries, key=lambda entry: (entry[0]["start"], group_order(entry[0], groups)))
                    parallel, stacked = [], []
                    for entry in ordered:
                        details = info[str(entry[0]["id"])]
                        (stacked if club or details["elective"] else parallel).append(entry)
                    count = seats[grade]
                    first_col = columns_by_grade[grade]
                    for index, entry in enumerate(parallel):
                        left = index * count // len(parallel)
                        right = (index + 1) * count // len(parallel)
                        placements.append((grade, entry, 0, first_col + left, right - left, False))
                    offset = int(bool(parallel))
                    for index, entry in enumerate(stacked):
                        placements.append((grade, entry, offset + index, first_col, count, True))
                    height = max(height, offset + len(stacked))
                cell(row, 0, "" if club else time, bg="#F3EFF8" if club else "#EEF3F8",
                     bold=bool(sections), size=8, height=height)
                for offset in range(height):
                    heights[row + offset] = 21
                slot_ranges.append({"start": row, "end": row + height})
                for grade, (lesson, continuation), offset, col, width, stacked in placements:
                    lesson_id = str(lesson["id"])
                    details = info[lesson_id]
                    card_row = row + offset
                    anchor = lesson_id not in mapping
                    text, size, text_height = card_caption(details, sum(widths[col:col+width]), club=club, stacked=stacked,
                                                         online_day=modes.get(grade) == "Онлайн")
                    heights[card_row] = max(heights[card_row], text_height)
                    linked_cards[lesson_id].append(cell(card_row, col, text, width=width, bg=details["bg"],
                        size=size, note="\n".join(details["notes"]) if anchor else ""))
                    if anchor:
                        mapping[lesson_id] = dict(lesson_id=lesson_id, source_lesson_ids=lesson["_source_ids"],
                            source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                            row=card_row, col=col, segment_start=lesson["start"], segment_end=lesson["end"],
                            day=day, class_ids=lesson["classIds"],
                            grade_numbers=sorted({grade_by_class[c] for c in lesson["classIds"]}),
                            group_ids=details["group_ids"], teacher_ids=details["teacher_ids"], references=[], rows=1)
                    else:
                        mapping[lesson_id]["references"].append({"row": card_row, "col": col, **({"continuation": True} if continuation else {})})
                row += height

    overview_rows = row
    cell(row, 0, "Карточки", bg="#243A51", fg="#FFFFFF", bold=True)
    cell(row, 1, "Подробности занятий · слева ссылка обратно в расписание", width=columns-1, bg="#243A51", fg="#FFFFFF", bold=True)
    heights[row] = 25
    row += 1
    characters_per_line = max(1, sum(widths[1:]) // 7)
    for lesson_id, entry in mapping.items():
        details = info[lesson_id]
        for linked in linked_cards[lesson_id]:
            linked["link_row"] = row
        entry["details_row"] = row
        cell(row, 0, f"↑ {DAY_NAMES[entry['day']][:2]} {entry['segment_start']}", link_row=entry["row"], bg="#EEF3F8")
        scope = " · ".join(filter(None, (details["classes"], details["groups"], details["staff"], details["room"])))
        reason = details["notice"]
        text = f"{details['interval']} · {details['name']}\n{scope}"
        if reason:
            text += f"\nУточнить · {reason}"
        links = {"lesson_link": {"draft_id": str(draft_id), "lesson_id": entry["source_lesson_ids"][0]}} if draft_id else {}
        cell(row, 1, text, width=columns-1, bg=details["bg"], **links)
        heights[row] = max(44, 16 + 15 * sum(max(1, (len(line) + characters_per_line - 1) // characters_per_line) for line in text.splitlines()))
        row += 1
    mapping = list(mapping.values())
    source_ids = [sid for item in mapping for sid in item["source_lesson_ids"]]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != {str(l["id"]) for l in payload["lessons"]}:
        raise ValueError("Экспорт потерял связь с исходными занятиями.")
    context = {"layout": GRID_LAYOUT, "logical_occurrence_audit": logical_audit}
    return {"format_version": FORMAT_VERSION, "layout": GRID_LAYOUT, "title": title, "sheet_id": sheet_id,
            "mapping_sheet_id": mapping_sheet_id, "snapshot_id": snapshot["snapshot_id"], "snapshot": snapshot,
            "rows": row, "overview_rows": overview_rows, "columns": columns, "column_widths": widths, "row_heights": heights,
            "day_rows": day_rows, "day_labels": day_labels, "frozen_rows": 1, "slot_ranges": slot_ranges, "cells": cells, "mapping": mapping,
            "class_columns": [{"start": col, "width": seats[grade]} for grade, col in columns_by_grade.items()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping),
                        "clarifications": sum(bool(details["notice"]) for details in info.values())}}
