"""Compact school day overviews with linked lesson details on the same worksheet."""
from collections import defaultdict
from datetime import date, timedelta
import re

from backend.services.canonical_schedule import canonical_group_grade
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


DAY_BODY_HEIGHT = 34
GRADE_BUDGET = 158
TIME_WIDTH = 87

_SUBJECT_SHORT = {
    "математика": "Мат", "алгебра": "Алг", "геометрия": "Геом", "английский язык": "Англ",
    "английский": "Англ", "русский язык": "Рус", "литература": "Лит", "история": "Ист",
    "биология": "Био", "география": "Гео", "обществознание": "Общ", "информатика": "Инф",
    "физика": "Физ", "химия": "Хим", "физкультура": "ФК", "обед": "Обед",
    "классный час": "Кл.ч", "история искусства": "Иск", "проектная мастерская": "ПрМ",
    "исследование окружающего мира": "ИОМ", "текст и смысл": "ТиС", "разговорная практика": "РП",
    "медиамастерская": "Медиа",
}
_ACTIVITY_PREFIXES = {"practice": "Пр", "consultation": "Конс", "laboratory": "Лаб"}


def compact_subject(name):
    words = re.findall(r"[^\W_]+", name, re.UNICODE)
    if name.casefold() in _SUBJECT_SHORT:
        return _SUBJECT_SHORT[name.casefold()]
    if len(words) > 1:
        return "".join(word[0].upper() for word in words[:4])
    # A clickable abbreviation for one-word subjects with no school shorthand.
    return (words[0][:3] + ".") if words else "Зан."


def saved_notes(lesson, existing_notes):
    return list(dict.fromkeys(
        clean for sid in lesson["_source_ids"] for note in existing_notes.get(sid, [])
        if (clean := "\n".join(line for line in note.splitlines() if not line.lstrip().startswith("MY_ISLAND:")).strip())
    ))


def describe_lesson(lesson, groups, teachers, existing_notes, bell_times):
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
    extra = activity["participation"] == "opt_in" or kind == "consultation"
    formal_type = {"club": "Кружок", "optional_activity": "Кружок", "elective": "Курс по выбору",
                   "practice": "Практикум", "consultation": "Консультация", "laboratory": "Лаборатория"}.get(kind or activity["activity_type"])
    if formal_type and " · " in name and kind in _ACTIVITY_PREFIXES:
        name = name.split(" · ", 1)[1]
    full_name = f"{formal_type} · {name}" if formal_type else name
    room = lesson.get("room") or lesson.get("place") or ""
    room_number = re.fullmatch(r"(?:каб\.?\s*)?([\d]+[А-Яа-яA-Za-z]?)", room, re.IGNORECASE)
    room_short = room_number[1] if room_number else "Онл" if room == "Онлайн" else room if len(room) <= 5 else ""
    group = groups.get(instructional[0], {}) if len(instructional) == 1 else {}
    rank = subgroup_rank(group)
    group_short = group.get("subject_subgroup")
    if rank is not None and not group_short:
        group_short = chr(65 + rank) if group.get("name", "").startswith("grade9-math-") else str(rank + 1)
    if not group_short and len(labels) == 1:
        group_short = labels[0].partition("-")[2]
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
    if extra and bg == "#F7F8FA":
        bg = "#EDE8FA"
    interval = f"{lesson['start']}–{lesson['end']}"
    short = _ACTIVITY_PREFIXES.get(kind) or compact_subject(name)
    return dict(name=full_name, short=short, group_short=str(group_short or ""), room_short=room_short,
                classes=", ".join(labels) or "Общее занятие", groups=", ".join(group_labels),
                staff=", ".join(staff), room=room, bg=bg, extra=extra, group_ids=group_ids,
                teacher_short="".join(word[0] for word in staff[0].split()[:3]) if staff and teacher_ids[0] in teachers else "",
                teacher_ids=teacher_ids, interval=interval, notice=" · ".join(reasons),
                atypical=bell_times.get(lesson["start"]) != lesson["end"], notes=saved_notes(lesson, existing_notes))


def card_caption(info, width, *, shared, continuation):
    mark = ("↳" if continuation else "↔" if shared else "") + ("?" if info["notice"] else "")
    narrow = width < 47
    if width >= 92 and len(info["name"]) <= (width - 12) // 6:
        title = " ".join(filter(None, (mark, info["name"])))
    else:
        title = info["short"] if narrow else f"{mark[:1]}{info['short']}"
    if width < 30:
        return title, 7
    if info["atypical"] and width >= 89:
        secondary = info["interval"]
    else:
        secondary = " ".join(filter(None, (info["group_short"], info["room_short"],
                                             info["teacher_short"] if width >= 70 else "")))
        if len(secondary) > (width - 4) // 5:
            secondary = info["group_short"] or info["room_short"]
        if info["atypical"] and width >= 34:
            secondary = info["interval"].split("–")[1]
    if narrow and mark:
        secondary = f"{mark[-1]}{secondary}" if len(secondary) <= 4 else mark[-1]
    return "\n".join(filter(None, (title, secondary))), 8


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
    info = {str(lesson["id"]): describe_lesson(lesson, groups, teachers, existing_notes, bell_times)
            for lessons in by_day.values() for lesson in lessons}
    # Lanes divide the budget of their grade. Only links and source IDs connect
    # repeated display cards for cross-class and multi-period activities.
    rows_by_slot = defaultdict(lambda: defaultdict(list))
    for day, lessons in by_day.items():
        for lesson in lessons:
            extra = info[str(lesson["id"])]["extra"]
            starts = [lesson["start"], *(bell for bell in bell_times if lesson["start"] < bell < lesson["end"])]
            for index, start in enumerate(starts):
                for grade in {grade_by_class[c] for c in lesson["classIds"]} or {None}:
                    rows_by_slot[day, extra, start][grade].append((lesson, index > 0))
    seats = {grade: max((len(section.get(grade, [])) for section in rows_by_slot.values()), default=1) or 1
             for grade in grades}
    columns_by_grade = {}
    widths = [TIME_WIDTH]
    budget = min(GRADE_BUDGET, 1136 // max(len(grades), 1))
    for grade in grades:
        columns_by_grade[grade] = len(widths)
        count = seats[grade]
        widths.extend([(part + 1) * budget // count - part * budget // count for part in range(count)])
    columns = len(widths)
    cells, mapping, day_rows, slot_ranges, heights = [], {}, [], [], {}
    draft_id = source.get("draft_id")

    def cell(row, col, text, *, width=1, bg="#FFFFFF", fg="#202B3B", bold=False, note="", size=9, align="LEFT", **links):
        entry = dict(row=row, col=col, width=width, height=1, text=text, bg=bg,
                     fg=fg, bold=bold, note=note, size=size, align=align, **links)
        cells.append(entry)
        return entry

    days = sorted(payload["days"])
    cell(0, 0, "My Island", bg="#243A51", fg="#FFFFFF", bold=True)
    day_links = []
    # Day buttons span complete grade blocks so they stay wide at normal zoom.
    for index, day in enumerate(days):
        left, right = index * (columns - 1) // len(days) + 1, (index + 1) * (columns - 1) // len(days) + 1
        if left != right:
            day_links.append((day, cell(0, left, DAY_NAMES[day], width=right-left, bg="#243A51", fg="#FFFFFF", bold=True)))
    heights[0] = 24
    cell(1, 0, f"Ред. {source.get('revision', '—')}", bg="#EFF3F8", bold=True)
    cell(1, 1, "↔ совместное · ↳ продолжение · ? уточнить данные. Откройте карточку для деталей и перехода в My Island. Пусто · нет записи.",
         width=columns-1, bg="#EFF3F8", size=8)
    heights[1] = 34 if len(grades) < 4 else 22
    cell(2, 0, "Время", bg="#DFE9F1", bold=True)
    for grade, col in columns_by_grade.items():
        label = "Общие" if grade is None else f"{grade} класс" if grade.isdecimal() else grade
        cell(2, col, label, width=seats[grade], bg="#DFE9F1", bold=True)
    heights[2] = 22
    row = 3
    linked_cards = defaultdict(list)
    for day in days:
        day_rows.append(row)
        caption = DAY_NAMES[day] + (f" · {monday + timedelta(days=day):%d.%m}" if monday else "")
        cell(row, 0, DAY_NAMES[day][:2], bg="#334D64", fg="#FFFFFF", bold=True)
        cell(row, 1, caption, width=columns-1, bg="#334D64", fg="#FFFFFF", bold=True)
        heights[row] = 23
        row += 1
        for extra in (False, True):
            starts = {start for d, opt, start in rows_by_slot if d == day and opt == extra}
            if extra and not starts:
                continue
            if extra:
                cell(row, 0, "По желанию", bg="#E7DFF4", bold=True, size=8)
                cell(row, 1, "Кружки и дополнительные занятия", width=columns-1, bg="#E7DFF4", bold=True)
                heights[row] = 20
                row += 1
            else:
                starts.update(bell_times)
            for start in sorted(starts):
                sections = rows_by_slot.get((day, extra, start), {})
                ends = sorted({lesson["end"] for entries in sections.values() for lesson, _ in entries})
                stop = bell_times.get(start) if not extra else None
                stop = stop or (ends[0] if len(ends) == 1 else "")
                time = f"{start}–{stop}" if stop else f"с {start}"
                cell(row, 0, time, bg="#F3EFF8" if extra else "#EEF3F8", bold=bool(sections), size=8)
                heights[row] = DAY_BODY_HEIGHT if sections else 22
                slot_ranges.append({"start": row, "end": row + 1})
                for grade, entries in sorted(sections.items(), key=lambda section: columns_by_grade[section[0]]):
                    ordered = sorted(entries, key=lambda entry: group_order(entry[0], groups))
                    count = seats[grade]
                    first_col = columns_by_grade[grade]
                    for index, (lesson, continuation) in enumerate(ordered):
                        lesson_id = str(lesson["id"])
                        details = info[lesson_id]
                        col = first_col + index * count // len(ordered)
                        width = first_col + (index + 1) * count // len(ordered) - col
                        anchor = lesson_id not in mapping
                        shared = len({grade_by_class[c] for c in lesson["classIds"]}) > 1
                        text, size = card_caption(details, sum(widths[col:col+width]), shared=shared, continuation=continuation)
                        linked_cards[lesson_id].append(cell(row, col, text, width=width, bg=details["bg"],
                            bold=True, size=size, align="CENTER", note="\n".join(details["notes"]) if anchor else ""))
                        if anchor:
                            mapping[lesson_id] = dict(lesson_id=lesson_id, source_lesson_ids=lesson["_source_ids"],
                                source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                                row=row, col=col, segment_start=lesson["start"], segment_end=lesson["end"],
                                day=day, class_ids=lesson["classIds"],
                                grade_numbers=sorted({grade_by_class[c] for c in lesson["classIds"]}),
                                group_ids=details["group_ids"], teacher_ids=details["teacher_ids"], references=[], rows=1)
                        else:
                            mapping[lesson_id]["references"].append({"row": row, "col": col, **({"continuation": True} if continuation else {})})
                row += 1

    for day, link in day_links:
        link["link_row"] = day_rows[days.index(day)]
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
            "day_rows": day_rows, "slot_ranges": slot_ranges, "cells": cells, "mapping": mapping,
            "class_columns": [{"start": col, "width": seats[grade]} for grade, col in columns_by_grade.items()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping),
                        "clarifications": sum(bool(details["notice"]) for details in info.values())}}
