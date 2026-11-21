"""Self-contained school timetable cells and a separate hidden source mapping."""
from collections import defaultdict
from math import ceil, lcm
from datetime import date, timedelta
import re

from backend.services.canonical_schedule import canonical_group_grade
from backend.services.schedule_experiments import resolve_payload_day_mode
from backend.services.schedule_sheet_export import (DAY_NAMES, FORMAT_VERSION, _COMPACT_SUBJECTS,
                                                    _COMPACT_TEACHERS, _class_key, _hash, _label)
from backend.services.teacher_ui_colors import APPROVED_SOURCE_COLORS
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


def instructional_ids(lesson, groups):
    audience = lesson.get("audience") or {}
    assignment = (lesson.get("canonical") or {}).get("assignment") or {}
    metadata = assignment.get("metadata") or {}
    linked = [*(audience.get("group_ids") or []), *(lesson.get("groupIds") or []),
              *([lesson["groupId"]] if lesson.get("groupId") else []),
              *(assignment.get("canonical_group_ids") or []),
              *((metadata.get("audience") or {}).get("canonical_group_ids") or []),
              *((metadata.get("audience_rule") or {}).get("group_ids") or [])]
    return list(dict.fromkeys(key for key in linked if groups.get(key, {}).get("group_type") != "class"))


def group_order(lesson, groups):
    ids = sorted(instructional_ids(lesson, groups))
    rank = subgroup_rank(groups.get(ids[0], {})) if len(ids) == 1 else None
    classes = sorted(_label(groups[key]) for key in lesson["classIds"])
    return (rank if rank is not None else 100, bool(ids), tuple(ids or classes))


_FIXED_LANES = {
    "7": {"grade7:split:1": 0, "grade7:split:2": 1},
    "9": {"grade9-math-A": 0, "grade9-math-B": 1, "grade9-math-C": 2},
}


def fixed_lane(lesson, groups, grade):
    if (lesson.get("audience") or {}).get("kind") == "available_slot" or lesson.get("audienceMode") == "remainder":
        return None
    ids = instructional_ids(lesson, groups)
    name = groups.get(ids[0], {}).get("name") if len(ids) == 1 else ""
    return _FIXED_LANES.get(grade, {}).get(name)


def source_lane(lesson, grade):
    source = lesson.get("source") or {}
    if str(source.get("sheet_id")) not in {"638874869", "714307019"}:
        return None
    columns = {"7": "FG", "9": "LMN"}.get(grade, "")
    matches = [re.fullmatch(r"([A-Z]+)[1-9]\d*", ref) for ref in source.get("cells", []) if isinstance(ref, str)]
    if matches and all(m and len(m[1]) == 1 and m[1] in columns for m in matches):
        indices = {columns.index(m[1]) for m in matches}
        if len(indices) == 1:
            return indices.pop()


def parallel_positions(entries, grade, groups):
    """Return row, lane and division count. Unproven peers get a separate row."""
    fixed = [fixed_lane(lesson, groups, grade) for lesson, _ in entries]
    if not any(lane is not None for lane in fixed):
        return [(0, n, len(entries), "") for n in range(len(entries))]
    count = 3 if grade == "9" else 2
    lanes = [lane if lane is not None else source_lane(entry[0], grade) for lane, entry in zip(fixed, entries)]
    free = set(range(count)) - set(lanes)
    if len(free) == lanes.count(None) == 1:
        n = lanes.index(None)
        lesson = entries[n][0]
        if (lesson.get("audience") or {}).get("kind") == "available_slot" or lesson.get("audienceMode") == "remainder":
            lanes[n] = free.pop()
    used = defaultdict(int)
    extra = []
    result = [None] * len(entries)
    for n in sorted(range(len(entries)), key=lambda i: fixed[i] is None):
        lane = lanes[n]
        if lane is None:
            extra.append(n)
        else:
            result[n] = (used[lane], lane, count, "")
            used[lane] += 1
    for i, n in enumerate(extra):
        result[n] = (max(used.values()) + i, 0, 1, "Позиция среди подгрупп не подтверждена")
    return result


def club_positions(placements, widths):
    """Keep grade anchors, borrowing empty neighbors before widening an overcrowded band."""
    minimum = 75
    occupied = {i for _, _, _, col, span in placements for i in range(col, col + span)}
    result = []
    by_grade = defaultdict(list)
    for item in placements:
        by_grade[item[0]].append(item)
    for items in by_grade.values():
        left = items[0][3]
        right = items[-1][3] + items[-1][4]
        count = len(items)
        while sum(widths[left:right]) < minimum * count:
            if right < len(widths) and right not in occupied:
                occupied.add(right)
                right += 1
            elif left > 1 and left - 1 not in occupied:
                left -= 1
                occupied.add(left)
            else:
                break
        for i, (grade, entry, row, _, _) in enumerate(items):
            stop = left + 1
            target = sum(widths[left:right]) / (count-i)
            while stop < right-(count-i-1) and sum(widths[left:stop]) < target:
                stop += 1
            if i == count-1:
                stop = right
            # Exception for a genuinely overcrowded day: widen instead of truncating.
            widths[left] += max(0, minimum - sum(widths[left:stop]))
            result.append((grade, entry, row, left, stop-left))
            left = stop
    return result


_UNKNOWN_GROUP = "Группа требует уточнения"


def group_caption(group):
    name = str(group.get("name") or "")
    if split := re.fullmatch(r"grade(\d+):split:(\d+)", name):
        return f"{split[1]}-{split[2]}"
    if math := re.fullmatch(r"grade9-math-([ABC])", name):
        return f"9{math[1]}"
    if english := re.fullmatch(r"english:(\d+)", name):
        return f"Англ. {english[1]}"
    if name in {"math:11:base", "math:11:advanced"}:
        return "База" if name.endswith("base") else "Угл."
    if subgroup := str(group.get("subject_subgroup") or "").strip():
        if re.fullmatch(r"[\w.-]{1,12}", subgroup):
            return subgroup
    label = str(group.get("display_name") or name).strip()
    if name.startswith("instructional:") and "·" in label:
        label = label.rsplit("·", 1)[-1].strip()
    if re.search(r"индивидуальн.*состав|без индивидуального назначения|инд\. состав", label, re.I):
        return ""
    if not label or label == str(group.get("id")) or ":" in label:
        return _UNKNOWN_GROUP
    return label


def cell_group(lesson, details, groups, grade, classes_by_grade):
    audience = lesson.get("audience") or {}
    kind = audience.get("kind")
    if kind == "available_slot" or lesson.get("audienceMode") == "remainder":
        return "Свободные" if lesson.get("specialMark") != "lunch" else "Остальные"
    if lesson.get("specialMark") == "lunch" and kind in {"classes", "students"}:
        return ""
    if details["group_ids"]:
        return ", ".join(dict.fromkeys(filter(None, (group_caption(groups.get(key, {})) for key in details["group_ids"]))))
    if kind == "groups":
        return _UNKNOWN_GROUP
    if kind == "students":
        return ""
    confirmed = set(audience.get("class_ids") or lesson["classIds"])
    candidates = classes_by_grade[grade]
    if selected := candidates & confirmed:
        if selected != candidates:
            return ", ".join(sorted(_label(groups[c]) for c in selected))
    # Classless choices have a confirmed direction on the catalog item.
    if details["elective"] and lesson.get("electiveDirection") == "digital":
        return "Цифр. трек"
    return ""


TIME_WIDTH = 86
GRADE_WIDTHS = {"5": 128, "6": 120, "7": 192, "8": 144, "9": 220, "10": 154, "11": 167, None: 82}
GRID_WIDTH = 1258
_ACTIVITY_TYPES = {"club": "Кружок", "optional_activity": "Кружок", "elective": "Курс по выбору",
                   "practice": "Практикум", "consultation": "Консультация", "laboratory": "Лаборатория"}
_SHORT_TITLES = {
    "Русский язык": "Русский", "Английский язык": "Английский",
    "Проектная мастерская": "Проектная", "Исследование окружающего мира": "Окружающий мир",
    "Разговорная практика": "Разг. практ.", "История искусства": "Ист. иск.",
    "Лаборатория · Прошлое · Настоящее": "Лаб. прошл./наст.",
}
_MODE_LABELS = {"online": "Онлайн", "sdep": "СДЭП", None: "Режим не согласован"}


def saved_notes(lesson, existing_notes):
    return list(dict.fromkeys(
        clean for sid in lesson["_source_ids"] for note in existing_notes.get(sid, [])
        if (clean := "\n".join(line for line in note.splitlines() if not line.lstrip().startswith("MY_ISLAND:")).strip())
    ))


def describe_lesson(lesson, groups, teachers, existing_notes):
    group_ids = instructional_ids(lesson, groups)
    teacher_ids = list(dict.fromkeys(filter(None, [lesson.get("teacherId"), *(lesson.get("teacherIds") or [])])))
    staff = [name for i in teacher_ids if i in teachers
             if (name := teachers[i].get("display_name") or teachers[i].get("name"))]
    activity = activity_semantics(lesson)
    name = activity["display_name"] or lesson["subject"]
    kind = lesson.get("activityType")
    club = activity["section"] == "clubs"
    formal_type = _ACTIVITY_TYPES.get(kind or activity["activity_type"])
    if kind in {"practice", "consultation", "laboratory"}:
        prefix, _, title = name.partition(" · ")
        if title and prefix in {formal_type, "Консультация по желанию"}:
            name = title
    short_type = {"Курс по выбору": "Курс", "Консультация": "Конс.", "Лаборатория": "Лаб."}.get(formal_type, formal_type)
    overview_name = f"{short_type} · {name}" if short_type and not club else name
    room = lesson.get("room") or lesson.get("place") or ""
    reasons = []
    if any(i not in groups for i in group_ids):
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
    primary = teacher_ids[0] if teacher_ids else ""
    color = teachers.get(primary, {}).get("ui_color") or APPROVED_SOURCE_COLORS.get(primary, {}).get("color")
    bg = color if color and re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#F1F3F8"
    if lesson.get("specialMark") == "lunch":
        bg = "#FABF8F"
    elif lesson.get("specialMark") == "no_lesson":
        bg = "#F3F4F6"
    elif lesson.get("kind") == "event" or kind == "event":
        bg = "#D9EAD3"
    return dict(overview_name=overview_name, subject=name, activity_prefix=short_type if not club else None,
                staff=", ".join(_COMPACT_TEACHERS.get(name, name) for name in staff), room=room, bg=bg,
                club=club, elective=activity["activity_type"] == "elective",
                group_ids=group_ids, teacher_ids=teacher_ids, notice=" · ".join(reasons),
                marker=lesson.get("specialMark"), start=lesson["start"],
                notes=saved_notes(lesson, existing_notes))


def contrast_text(background):
    channels = [int(background[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    luminance = sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return "#000000" if (luminance + .05) ** 2 >= .0525 else "#FFFFFF"


def wrapped_lines(text, width, size):
    """Estimate native Arial wrapping to budget row height without clipping the values."""
    limit = max(1, width - 8)  # Include padding and the heavier class separator.
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


def card_caption(info, group, width, *, club, online_day=False):
    size = 8 if width < 160 else 9
    room = "Онлайн" if online_day and not info["marker"] else info["room"]
    if room_number := re.fullmatch(r"каб\.?\s*(\d+\w?)", room, re.IGNORECASE):
        room = f"каб.{room_number[1]}"
    staff = info["staff"]
    if info["marker"] == "lunch":
        staff, room = "", ""
    metadata = " · ".join(filter(None, (staff, room)))
    title = info["overview_name"]
    time = f"{info['start']} " if club else ""
    if wrapped_lines(" · ".join(filter(None, (time + title, group))), width, size) > 1:
        subject = _SHORT_TITLES.get(info["subject"], _COMPACT_SUBJECTS.get(info["subject"], info["subject"]))
        prefix = info["activity_prefix"]
        title = f"{prefix} · {subject}" if prefix else subject
    # The English heading already identifies the subject as well as the numbered group.
    if title in {"Английский", "Английский язык", "Англ."} and re.fullmatch(r"Англ\. \d+", group):
        title, group = group, ""
    title = " · ".join(filter(None, (time + title, group)))
    lines = [line for line in (title, metadata) if line]
    if (club or info["elective"]) and wrapped_lines(" · ".join(lines), width, size) == 1:
        lines = [" · ".join(lines)]
    height = 4 + ceil(size * 1.43) * sum(wrapped_lines(line, width, size) for line in lines)
    return "\n".join(lines), size, height


def build_school_grid(snapshot, by_day, groups, teachers, classes, *, title, sheet_id,
                      mapping_sheet_id, existing_notes, logical_audit):
    payload = snapshot["payload"]
    week = payload.get("week_start")
    monday = date.fromisoformat(week) if week else None
    grade_by_class = {str(c["id"]): canonical_group_grade(c) or _label(c) for c in sorted(classes, key=_class_key)}
    grades = list(dict.fromkeys(grade_by_class.values()))
    classes_by_grade = defaultdict(set)
    for class_id, grade in grade_by_class.items():
        classes_by_grade[grade].add(class_id)
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
        if any(fixed_lane(l, groups, grade) is not None for section in rows_by_slot.values() for l, _ in section.get(grade, [])):
            counts.add(3 if grade == "9" else 2)
        counts.update(len(section[grade]) for section in clubs_by_day.values() if section.get(grade))
        common = lcm(*counts)
        seats[grade] = common if common <= 12 else ceil(max(counts) / 6) * 6
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
    for day in days:
        day_rows.append(row)
        caption = DAY_NAMES[day] + (f" · {monday + timedelta(days=day):%d.%m}" if monday else "")
        day_labels[row] = caption
        modes = {grade: _MODE_LABELS.get(resolve_payload_day_mode(payload, grade, day, grade_by_class)[0], "")
                 for grade in grades if grade is not None}
        if any(modes.values()):
            cell(row, 0, caption, bg="#344D64", fg="#FFFFFF", bold=True, size=8)
            for grade, col in columns_by_grade.items():
                label = modes.get(grade, "")
                cell(row, col, label, width=seats[grade], bg="#345D5C" if label else "#344D64", fg="#FFFFFF",
                     bold=True, align="CENTER")
            heights[row] = max(22, 4 + 12 * wrapped_lines(caption, TIME_WIDTH, 8))
            for grade, label in modes.items():
                if label:
                    col = columns_by_grade[grade]
                    width = sum(widths[col:col+seats[grade]])
                    heights[row] = max(heights[row], 4 + 14 * wrapped_lines(label, width, 9))
        else:
            cell(row, 0, DAY_NAMES[day][:2], bg="#344D64", fg="#FFFFFF", bold=True)
            cell(row, 1, caption, width=columns-1, bg="#344D64", fg="#FFFFFF", bold=True)
            heights[row] = 22
        row += 1
        for club in (False, True):
            if club:
                if not clubs_by_day[day]:
                    continue
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
                placement_notes = {}
                height = 1
                for grade, entries in sorted(sections.items(), key=lambda section: columns_by_grade[section[0]]):
                    ordered = sorted(entries, key=lambda entry: (entry[0]["start"], group_order(entry[0], groups)))
                    parallel, stacked = [], []
                    for entry in ordered:
                        details = info[str(entry[0]["id"])]
                        (stacked if not club and details["elective"] else parallel).append(entry)
                    count = seats[grade]
                    first_col = columns_by_grade[grade]
                    positions = ([(0, i, len(parallel), "") for i in range(len(parallel))] if club else
                                 parallel_positions(parallel, grade, groups))
                    for entry, (level, lane, divisions, notice) in zip(parallel, positions):
                        left, right = lane * count // divisions, (lane + 1) * count // divisions
                        placements.append((grade, entry, level, first_col + left, right - left))
                        if notice:
                            placement_notes[entry[0]["id"]] = notice
                    offset = max((pos[0] + 1 for pos in positions), default=0)
                    for index, entry in enumerate(stacked):
                        placements.append((grade, entry, offset + index, first_col, count))
                    height = max(height, offset + len(stacked))
                if club:
                    placements = club_positions(placements, widths)
                cell(row, 0, "Кружки" if club else time, bg="#F3EFF8" if club else "#EEF3F8",
                     bold=bool(sections), size=8, height=height)
                for offset in range(height):
                    heights[row + offset] = 21
                slot_ranges.append({"start": row, "end": row + height})
                for grade, (lesson, continuation), offset, col, width in placements:
                    lesson_id = str(lesson["id"])
                    details = info[lesson_id]
                    card_row = row + offset
                    anchor = lesson_id not in mapping
                    group = "" if club else cell_group(lesson, details, groups, grade, classes_by_grade)
                    text, size, text_height = card_caption(details, group, sum(widths[col:col+width]), club=club,
                                                         online_day=modes.get(grade) == "Онлайн")
                    heights[card_row] = max(heights[card_row], text_height)
                    cell(card_row, col, text, width=width, bg=details["bg"], fg=contrast_text(details["bg"]),
                         size=size, note="\n".join(details["notes"]) if anchor else "")
                    if anchor:
                        mapping[lesson_id] = dict(lesson_id=lesson_id, source_lesson_ids=lesson["_source_ids"],
                            source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                            row=card_row, col=col, segment_start=lesson["start"], segment_end=lesson["end"],
                            day=day, class_ids=lesson["classIds"],
                            grade_numbers=sorted({grade_by_class[c] for c in lesson["classIds"]}),
                            group_ids=details["group_ids"], teacher_ids=details["teacher_ids"], references=[], rows=1,
                            group_label=group, review=placement_notes.get(lesson_id) or details["notice"] or (_UNKNOWN_GROUP if _UNKNOWN_GROUP in group else ""))
                    else:
                        mapping[lesson_id]["references"].append({"row": card_row, "col": col, "group_label": group, **({"continuation": True} if continuation else {})})
                row += height

    mapping = list(mapping.values())
    source_ids = [sid for item in mapping for sid in item["source_lesson_ids"]]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != {str(l["id"]) for l in payload["lessons"]}:
        raise ValueError("Экспорт потерял связь с исходными занятиями.")
    context = {"layout": GRID_LAYOUT, "logical_occurrence_audit": logical_audit}
    return {"format_version": FORMAT_VERSION, "layout": GRID_LAYOUT, "title": title, "sheet_id": sheet_id,
            "mapping_sheet_id": mapping_sheet_id, "snapshot_id": snapshot["snapshot_id"], "snapshot": snapshot,
            "rows": row, "overview_rows": row, "columns": columns, "column_widths": widths, "row_heights": heights,
            "day_rows": day_rows, "day_labels": day_labels, "frozen_rows": 1, "slot_ranges": slot_ranges, "cells": cells, "mapping": mapping,
            "class_columns": [{"start": col, "width": seats[grade]} for grade, col in columns_by_grade.items()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping),
                        "clarifications": sum(bool(item["review"]) for item in mapping)}}
