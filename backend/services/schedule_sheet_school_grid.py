"""Editable school timetable: a physical column per grade and stacked slot cards."""
from collections import defaultdict
from datetime import date, timedelta
from textwrap import wrap

from backend.services.canonical_schedule import canonical_group_grade
from backend.services.schedule_sheet_export import DAY_NAMES, FORMAT_VERSION, _class_key, _hash, _label, _teacher_fill, column_name
from backend.services.schedule_sheet_layout_v3 import activity_semantics

GRID_LAYOUT = "school-grid"
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
GRADE_WIDTH = 146
TIME_WIDTH = 98
CARD_LINES = 4


def week_caption(week, days):
    first = date.fromisoformat(week)
    last = first + timedelta(days=max(4, max(days, default=4)))
    if first.month == last.month:
        return f"{first.day}–{last.day} {_MONTHS[last.month-1]}"
    return f"{first.day} {_MONTHS[first.month-1]} – {last.day} {_MONTHS[last.month-1]}"


def build_school_grid(snapshot, by_day, groups, teachers, classes, *, title, sheet_id,
                      mapping_sheet_id, existing_notes, logical_audit):
    payload = snapshot["payload"]
    source = snapshot.get("source") or {}
    week = payload.get("week_start")
    monday = date.fromisoformat(week) if week else None
    grade_by_class = {str(c["id"]): canonical_group_grade(c) or _label(c) for c in sorted(classes, key=_class_key)}
    grades = list(dict.fromkeys(grade_by_class.values()))
    captions = {grade: f"{grade} класс" if grade.isdecimal() else grade for grade in grades}
    extra = any(not lesson["classIds"] for lessons in by_day.values() for lesson in lessons)
    if extra:
        grades.append(None)
        captions[None] = "Общие занятия"
    columns = len(grades) + 1
    positions = {grade: i for i, grade in enumerate(grades, 1)}
    widths = [TIME_WIDTH, *[GRADE_WIDTH for _ in grades]]
    cells, mapping, day_rows, slot_ranges = [], [], [], []
    heights = {}
    clarification_count = 0

    def cell(row, col, text, *, width=1, bg="#FFFFFF", fg="#202B3B", bold=False, note="", size=10):
        cells.append(dict(row=row, col=col, width=width, height=1, text=text, bg=bg,
                          fg=fg, bold=bold, note=note, size=size, align="LEFT"))

    cell(0, 0, "My Island", bg="#243A51", fg="#FFFFFF", bold=True)
    heading = f"Неделя {week_caption(week, payload['days'])} {monday.year}" if week else "Школьный шаблон"
    cell(0, 1, heading, width=columns-1, bg="#243A51", fg="#FFFFFF", bold=True, size=13)
    heights[0] = 28
    cell(1, 0, "Снимок", bg="#F1F5F8", bold=True)
    updated = str(source.get("updated_at") or "")[:10]
    stamp = f" · {updated}" if updated else ""
    cell(1, 1, f"{source.get('name') or 'Расписание My Island'} · редакция {source.get('revision', '—')}{stamp}\n"
         "Актуальность и замечания проверяйте в конструкторе. Изменения в таблице остаются в Google Sheets.",
         width=columns-1, bg="#F1F5F8")
    heights[1] = 88 if columns == 2 else 64 if columns == 3 else 49
    cell(2, 0, "Время", bg="#DFE9F1", bold=True)
    for grade, col in positions.items():
        cell(2, col, captions[grade], bg="#DFE9F1", bold=True)
    heights[2] = 25
    row = 3

    for day in sorted(payload["days"]):
        day_rows.append(row)
        caption = DAY_NAMES[day]
        if monday:
            caption += f" · {monday + timedelta(days=day):%d.%m}"
        cell(row, 0, DAY_NAMES[day][:2], bg="#334D64", fg="#FFFFFF", bold=True)
        cell(row, 1, caption, width=columns-1, bg="#334D64", fg="#FFFFFF", bold=True)
        heights[row] = 26
        row += 1
        slots = defaultdict(list)
        for lesson in by_day.get(day, []):
            slots[lesson["start"]].append(lesson)
        bell_times = {str(slot["start"])[:5]: str(slot["end"])[:5] for slot in payload["slots"]}
        for start in sorted(set(slots) | bell_times.keys()):
            if not slots[start]:
                cell(row, 0, f"{start}–{bell_times[start]}", bg="#EEF3F8", size=9)
                heights[row] = 26
                slot_ranges.append({"start": row, "end": row + 1})
                row += 1
                continue
            start_row = row
            stacks = defaultdict(list)
            for lesson in sorted(slots[start], key=lambda item: (item["end"], item["subject"], str(item["id"]))):
                lesson_id = str(lesson["id"])
                targets = sorted({grade_by_class[c] for c in lesson["classIds"]}, key=positions.__getitem__) or [None]
                anchor = targets[0]
                audience = lesson.get("audience") or {}
                group_ids = list(dict.fromkeys([*(audience.get("group_ids") or []),
                    *(lesson.get("groupIds") or ([lesson["groupId"]] if lesson.get("groupId") else []))]))
                group_labels = [_label(groups[key]) if key in groups else "Уточнить группу"
                                for key in group_ids if groups.get(key, {}).get("group_type") != "class"]
                teacher_ids = list(dict.fromkeys([*([lesson["teacherId"]] if lesson.get("teacherId") else []), *(lesson.get("teacherIds") or [])]))
                staff = [str(teachers[key].get("display_name") or teachers[key].get("name") or "Преподаватель")
                         if key in teachers else "Уточнить преподавателя" for key in teacher_ids]
                activity = activity_semantics(lesson)
                unknown = (lesson.get("needsClarification") or lesson.get("importError")
                           or "Уточнить группу" in group_labels or "Уточнить преподавателя" in staff
                           or activity["section"] == "activity_review")
                clarification_count += bool(unknown)
                name = activity["display_name"] or lesson["subject"]
                if activity["section"] == "clubs":
                    name = f"Кружок · {name}"
                elif activity["activity_type"] == "elective":
                    name = f"По выбору · {name}"
                if unknown:
                    name = f"⚠ {name}"
                labels = [_label(groups[c]) for c in lesson["classIds"]]
                scope = ", ".join(labels) if len(labels) > 1 or (labels and labels[0] != anchor) else ""
                if audience.get("kind") == "available_slot" or lesson.get("audienceMode") == "remainder":
                    group_labels.append("Для свободных учеников")
                elif audience.get("kind") == "students":
                    group_labels.append("Индивидуальный состав")
                ends = {item["end"] for item in slots[start]}
                interval = f"{start}–{lesson['end']}" if len(ends) > 1 else ""
                detail = " · ".join(filter(None, (", ".join(staff), lesson.get("room"))))
                lines = [line for part in (name, scope, *dict.fromkeys(group_labels), detail, interval) if part
                         for line in wrap(part, 19)]
                bg = _teacher_fill(teacher_ids, teachers, staff)
                rgb = [int(bg[i:i+2], 16) for i in (1, 3, 5)]
                bg = "#" + "".join(f"{round(value*.21+255*.79):02X}" for value in rgb)
                if lesson.get("specialMark") == "lunch":
                    bg = "#F6E5CD"
                notes = ["\n".join(line for line in note.splitlines() if not line.lstrip().startswith("MY_ISLAND:")).strip()
                         for sid in lesson["_source_ids"] for note in existing_notes.get(sid, [])]
                notes = list(dict.fromkeys(filter(None, notes)))
                col = positions[anchor]
                offsets = stacks[col]
                first_row = start_row + len(offsets)
                for i in range(0, len(lines), CARD_LINES):
                    offsets.append((lines[i:i+CARD_LINES], bg, i == 0, "\n".join(dict.fromkeys(notes)) if not i else ""))
                references = []
                for other in targets[1:]:
                    target_col = positions[other]
                    target_row = start_row + len(stacks[target_col])
                    reference = f"Совместное занятие · {column_name(col)}{first_row+1}"
                    stacks[target_col].append((wrap(reference, 19), "#EFF4F9", False, ""))
                    references.append({"row": target_row, "col": target_col})
                mapping.append(dict(lesson_id=lesson_id, source_lesson_ids=lesson["_source_ids"],
                                    source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                                    row=first_row, col=col, segment_start=start, segment_end=lesson["end"],
                                    day=day, class_ids=lesson["classIds"], grade_numbers=targets if anchor is not None else [],
                                    group_ids=group_ids, teacher_ids=teacher_ids, references=references,
                                    rows=(len(lines)+CARD_LINES-1)//CARD_LINES))
            for offset in range(max(map(len, stacks.values()))):
                active = [(col, stack[offset]) for col, stack in stacks.items() if offset < len(stack)]
                heights[row] = max(31, 10 + 16 * max(len(card[0]) for _, card in active))
                if not offset:
                    ends = {lesson["end"] for lesson in slots[start]}
                    cell(row, 0, f"{start}–{next(iter(ends))}" if len(ends) == 1 else start,
                         bg="#EEF3F8", bold=True, size=9)
                for col, (lines, bg, bold, note) in active:
                    cell(row, col, "\n".join(lines), bg=bg, bold=bold, note=note, size=9)
                row += 1
            slot_ranges.append({"start": start_row, "end": row})

    if clarification_count:
        cells[1]["text"] += f" · ⚠ {clarification_count}"
    source_ids = [sid for item in mapping for sid in item["source_lesson_ids"]]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != {str(l["id"]) for l in payload["lessons"]}:
        raise ValueError("Экспорт потерял связь с исходными занятиями.")
    context = {"layout": GRID_LAYOUT, "logical_occurrence_audit": logical_audit}
    return {"format_version": FORMAT_VERSION, "layout": GRID_LAYOUT, "title": title, "sheet_id": sheet_id,
            "mapping_sheet_id": mapping_sheet_id, "snapshot_id": snapshot["snapshot_id"], "snapshot": snapshot,
            "rows": row, "columns": columns, "column_widths": widths, "row_heights": heights,
            "day_rows": day_rows, "slot_ranges": slot_ranges, "cells": cells, "mapping": mapping,
            "class_columns": [{"start": col, "width": 1} for col in positions.values()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping), "clarifications": clarification_count}}
