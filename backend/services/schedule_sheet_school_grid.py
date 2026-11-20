"""Editable school timetable: a physical column per grade and stacked slot cards."""
from collections import defaultdict
from datetime import date, timedelta
from textwrap import shorten
import re

from backend.services.canonical_schedule import canonical_group_grade
from backend.services.schedule_sheet_export import DAY_NAMES, FORMAT_VERSION, _class_key, _hash, _label, _teacher_fill, column_name
from backend.services.schedule_sheet_layout_v3 import activity_semantics

GRID_LAYOUT = "school-grid"
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
GRADE_WIDTH = 184
TIME_WIDTH = 98



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
    # Continue a long lesson into each occupied bell interval without creating
    # another occurrence or duplicating its source IDs in the hidden map.
    bell_starts = sorted({str(slot["start"])[:5] for slot in payload["slots"]})
    segments = defaultdict(list)
    for day, lessons in by_day.items():
        for lesson in lessons:
            segments[day].append(lesson)
            for bell in bell_starts:
                if lesson["start"] < bell < lesson["end"]:
                    segments[day].append({**lesson, "start": bell, "continuation": True})
    by_day = segments
    anchors = {}
    slots_by_grade = defaultdict(lambda: defaultdict(list))
    for day, lessons in by_day.items():
        for lesson in lessons:
            targets = {grade_by_class[c] for c in lesson["classIds"]} or {None}
            for grade in targets:
                slots_by_grade[grade][day, lesson["start"]].append(lesson)

    placements = {}
    spans = {}
    positions = {}
    columns = 1
    for grade in grades:
        slots = slots_by_grade[grade]
        width = max((len(lessons) for lessons in slots.values()), default=1)
        for (_, start), lessons in slots.items():
            ordered = sorted(lessons, key=lambda lesson: group_order(lesson, groups))
            for index, lesson in enumerate(ordered):
                left = index * width // len(ordered)
                right = (index + 1) * width // len(ordered)
                placements[grade, start, lesson["id"]] = left, right - left
        spans[grade] = width
        positions[grade] = columns
        columns += width
    widths = [TIME_WIDTH] + [GRADE_WIDTH] * (columns - 1)
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
        cell(2, col, captions[grade], width=spans[grade], bg="#DFE9F1", bold=True)
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
            ends = {item["end"] for item in slots[start]}
            cell(row, 0, f"{start}–{next(iter(ends))}" if len(ends) == 1 else start, bg="#EEF3F8", bold=True)
            heights[row] = 66
            for lesson in sorted(slots[start], key=lambda item: (item["end"], item["subject"], str(item["id"]))):
                lesson_id = str(lesson["id"])
                targets = sorted({grade_by_class[c] for c in lesson["classIds"]}, key=positions.__getitem__) or [None]
                anchor = targets[0]
                if lesson.get("continuation"):
                    original = anchors[lesson_id]
                    for grade in targets:
                        col = positions[grade] + placements[grade, start, lesson_id][0]
                        cell(row, col, f"Продолжение · {column_name(original['col'])}{original['row']+1}", size=9, width=placements[grade, start, lesson_id][1])
                        original["references"].append({"row": row, "col": col, "continuation": True})
                    continue
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
                full = "\n".join(filter(None, (name, scope, *group_labels, detail, interval)))
                # Keep the confirmed group visible when adjacent subjects swap.
                caption_group = ", ".join(group_labels) or scope
                compact = "\n".join(filter(None, (name, caption_group, detail, interval)))
                bg = _teacher_fill(teacher_ids, teachers, staff)
                rgb = [int(bg[i:i+2], 16) for i in (1, 3, 5)]
                bg = "#" + "".join(f"{round(value*.21+255*.79):02X}" for value in rgb)
                if lesson.get("specialMark") == "lunch":
                    bg = "#F6E5CD"
                notes = ["\n".join(line for line in note.splitlines() if not line.lstrip().startswith("MY_ISLAND:")).strip()
                         for sid in lesson["_source_ids"] for note in existing_notes.get(sid, [])]
                notes = list(dict.fromkeys(filter(None, notes)))
                lane, width = placements[anchor, start, lesson_id]
                col = positions[anchor] + lane
                first_row = row
                caption = compact
                if len(caption) > 76:
                    caption = "\n".join(shorten(part, width=24, placeholder="…") for part in caption.splitlines())
                if full != caption:
                    notes.append(full)
                cell(row, col, caption, width=width, bg=bg, bold=True, size=9, note="\n".join(notes))
                references = []
                for other in targets[1:]:
                    target_col = positions[other] + placements[other, start, lesson_id][0]
                    cell(row, target_col, f"Совместное занятие · {column_name(col)}{row+1}", size=9, width=placements[other, start, lesson_id][1])
                    references.append({"row": row, "col": target_col})
                mapping.append(dict(lesson_id=lesson_id, source_lesson_ids=lesson["_source_ids"],
                                    source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                                    row=first_row, col=col, segment_start=start, segment_end=lesson["end"],
                                    day=day, class_ids=lesson["classIds"], grade_numbers=targets if anchor is not None else [],
                                    group_ids=group_ids, teacher_ids=teacher_ids, references=references,
                                    rows=1))
                anchors[lesson_id] = mapping[-1]
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
            "class_columns": [{"start": col, "width": spans[grade]} for grade, col in positions.items()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping), "clarifications": clarification_count}}
