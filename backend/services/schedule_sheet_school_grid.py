"""Self-contained school timetable cells and a separate hidden source mapping."""
from collections import defaultdict
from math import ceil, lcm
from datetime import date, timedelta
import json
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
    current = [key for key in [*(lesson.get("groupIds") or []), lesson.get("groupId")]
               if key and groups.get(key, {}).get("group_type") != "class"]
    if audience.get("kind"):
        linked = audience.get("group_ids", []) if audience["kind"] == "groups" else []
    else:
        linked = current or [*(assignment.get("canonical_group_ids") or []),
                            *((metadata.get("audience") or {}).get("canonical_group_ids") or []),
                            *((metadata.get("audience_rule") or {}).get("group_ids") or [])]
    return list(dict.fromkeys(key for key in linked if groups.get(key, {}).get("group_type") != "class"))


_FIXED_LANES = {
    "7": {"grade7:split:1": 0, "grade7:split:2": 1},
    "9": {"grade9-math-A": 0, "grade9-math-B": 1, "grade9-math-C": 2},
}


def column_group(ids, groups, grade):
    local = []
    for key in ids:
        name = groups.get(key, {}).get("name") or ""
        owner = re.match(r"grade(7|9)(?=:split:|-math-)", name)
        if not owner or owner[1] == grade:
            local.append(key)
    return groups.get(local[0], {}) if len(local) == 1 else {}


def source_lane(lesson, grade):
    source = lesson.get("source") or {}
    if str(source.get("sheet_id")) not in {"638874869", "714307019"}:
        return None
    columns = {"7": "FG", "9": "LMN"}.get(grade, "")
    cells = source.get("cells")
    if not isinstance(cells, list) or not cells or not all(isinstance(c, str) for c in cells):
        return None
    matches = [re.fullmatch(r"([A-Z])([1-9]\d*)", ref) for ref in cells]
    if all(m and m[1] in columns for m in matches):
        indices = {columns.index(m[1]) for m in matches}
        if len(indices) == 1:
            return indices.pop()



def placement_kind(lesson):
    explicit = (lesson.get("audience") or {}).get("kind")
    if explicit:
        return explicit
    metadata = ((lesson.get("canonical") or {}).get("assignment") or {}).get("metadata") or {}
    rule = (metadata.get("audience_rule") or {}).get("kind")
    if rule == "whole_grade":
        return "classes"
    if rule in {"available_slot", "remaining"} or lesson.get("audienceMode") == "remainder":
        return "available_slot"
    return rule


def lane_entry(lesson, grade, groups):
    ids = instructional_ids(lesson, groups)
    audience = lesson.get("audience") or {}
    names = sorted(groups.get(key, {}).get("name") or key for key in ids)
    staff = sorted(set(filter(None, [lesson.get("teacherId"), *(lesson.get("teacherIds") or [])])))
    cells = (lesson.get("source") or {}).get("cells")
    roster = ("0" + str(lesson["_lane_roster_order"]).zfill(12) if "_lane_roster_order" in lesson else
              "0" + json.dumps(sorted(audience["student_ids"]), ensure_ascii=False, separators=(",", ":"))
              if audience.get("student_ids") else "1")
    key = [names, lesson["subject"], lesson.get("activityType") or "", lesson.get("room") or "",
           sorted(map(str,cells)) if isinstance(cells, list) else [], lesson.get("start") or "", lesson.get("end") or "",
           lesson.get("blockName") or "", lesson.get("format") or "", lesson.get("place") or "",
           lesson.get("specialMark") or "", lesson.get("kind") or "", audience.get("kind") or "", staff,
           sorted(lesson.get("classIds") or [lesson.get("classId") or ""]), roster]
    group = column_group(ids, groups, grade)
    rank = subgroup_rank(group)
    kind = placement_kind(lesson)
    remainder = kind == "available_slot"
    return dict(id=lesson["id"], order=(100 if rank is None else rank,
                json.dumps(key, ensure_ascii=False, separators=(",", ":")), lesson["id"]),
                fixed=None if remainder else _FIXED_LANES.get(grade, {}).get(group.get("name")), source=source_lane(lesson, grade),
                whole=kind == "classes" and not audience.get("exclude_student_ids") and not lesson.get("_lane_partial_class"),
                remainder=remainder)


def grade_layout(slots, grade):
    """Mirror timetableLanes. Only display coordinates are allocated here."""
    width = len(_FIXED_LANES.get(grade, {}))
    def base(entries):
        if len(entries) == 1 and entries[0]["whole"]:
            return 0
        return width if any(e["fixed"] is not None or e["source"] is not None or e["remainder"] for e in entries) else 0

    anchored = max([width, *[len({e["fixed"] for e in entries if e["fixed"] is not None}) +
                   sum(e["fixed"] is None for e in entries) for entries in slots.values() if base(entries)]])
    result = {}
    for slot, entries in slots.items():
        nominal = base(entries)
        seats = anchored if nominal else max(1, len(entries))
        buckets = [[] for _ in range(seats)]
        uncertain = set()
        pending = []
        for e in sorted(entries, key=lambda e: e["order"]):
            if e["fixed"] is not None:
                buckets[e["fixed"]].append(e)
            else:
                pending.append(e)
        for remainder in (False, True):
            peers = [e for e in pending if e["remainder"] == remainder]
            waiting = []
            for e in peers:
                col = e["source"]
                if (not e["whole"] and col is not None and col < nominal and not buckets[col]
                        and sum(not p["whole"] and p["source"] == col for p in peers) == 1):
                    buckets[col].append(e)
                else:
                    waiting.append(e)
            for e in waiting:
                col = next(i for i, b in enumerate(buckets) if not b)
                buckets[col].append(e)
                if nominal and (not remainder or len(peers) > 1 or not all(buckets[:nominal]) or col >= nominal):
                    uncertain.add(e["id"])
        subdivisions = max(1, *(len(bucket) for bucket in buckets))
        positions = {}
        for seat, bucket in enumerate(buckets):
            for i, e in enumerate(bucket):
                left = seat * subdivisions + i * subdivisions // len(bucket)
                right = seat * subdivisions + (i + 1) * subdivisions // len(bucket)
                notice = ("Несколько занятий одной группы одновременно" if len(bucket) > 1 else
                          "Позиция среди подгрупп не подтверждена" if e["id"] in uncertain else "")
                positions[e["id"]] = (left, right, notice)
        result[slot] = seats * subdivisions, positions
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
    kind = placement_kind(lesson)
    if kind == "available_slot":
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
                marker=lesson.get("specialMark"), start=lesson["start"], end=lesson["end"],
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
    time = f"{info['start']}–{info['end']} " if club else ""
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
    bell_times = {str(slot["start"])[:5]: str(slot["end"])[:5] for slot in payload["slots"]}
    info = {str(lesson["id"]): describe_lesson(lesson, groups, teachers, existing_notes)
            for lessons in by_day.values() for lesson in lessons}
    # Lanes divide the budget of their grade. Only links and source IDs connect
    # repeated display cards for cross-class and multi-period activities.
    rows_by_slot = defaultdict(lambda: defaultdict(list))
    originals = {lesson["id"]: lesson for lesson in payload["lessons"]}
    clubs_by_day = defaultdict(list)
    for day, lessons in by_day.items():
        for lesson in lessons:
            if info[str(lesson["id"])]["club"]:
                clubs_by_day[day].append(lesson)
                continue
            targets = {grade_by_class[c] for c in lesson["classIds"]}
            if not targets and None not in grades:
                grades.append(None)
            starts = sorted(bell for bell, end in bell_times.items() if lesson["start"] < end and bell < lesson["end"])
            for index, start in enumerate(starts or [lesson["start"]]):
                for grade in targets or {None}:
                    rows_by_slot[day, start][grade].append((lesson, index > 0))
    seats, layouts = {}, {}
    for grade in grades:
        layouts[grade] = grade_layout({
            slot: [lane_entry(originals[l["id"]], grade, groups) for l, _ in section[grade]]
            for slot, section in rows_by_slot.items() if grade in section}, grade)
        counts = {width for width, _ in layouts[grade].values()} or {1}
        common = lcm(*counts)
        seats[grade] = common if common <= 24 else ceil(max(counts) / 6) * 6
    columns_by_grade = {}
    widths = [TIME_WIDTH]
    requested_width = sum(GRADE_WIDTHS.get(grade, 120) for grade in grades)
    for grade in grades:
        budget = GRADE_WIDTHS.get(grade, 120)
        if requested_width > GRID_WIDTH - TIME_WIDTH:
            budget = budget * (GRID_WIDTH - TIME_WIDTH) // requested_width
        # A crowded school band can scroll horizontally. Keep each actual
        # merged card at least as legible as the former club band (75 px).
        count = seats[grade]
        smallest = min((right * count // divisions - left * count // divisions
                        for divisions, positions in layouts[grade].values()
                        for left, right, _ in positions.values()), default=count)
        budget = max(budget, ceil(75 * count / smallest))
        columns_by_grade[grade] = len(widths)
        widths.extend([(part + 1) * budget // count - part * budget // count for part in range(count)])
    columns = len(widths)
    cells, mapping, day_rows, day_labels, slot_ranges, heights = [], {}, [], {}, [], {}
    class_band_ranges = []

    def cell(row, col, text, *, width=1, bg="#FFFFFF", fg="#202B3B", bold=False, note="", size=9, align="LEFT", height=1, **links):
        entry = dict(row=row, col=col, width=width, height=height, text=text, bg=bg,
                     fg=fg, bold=bold, note=note, size=size, align=align, **links)
        cells.append(entry)
        return entry

    days = sorted(payload["days"])
    cell(0, 0, "Время", bg="#243A51", fg="#FFFFFF", bold=True)
    for grade, col in columns_by_grade.items():
        label = "Без параллели" if grade is None else f"{grade} класс" if grade.isdecimal() else grade
        cell(0, col, label, width=seats[grade], bg="#243A51", fg="#FFFFFF", bold=True, align="CENTER")
    heights[0] = 25
    row = 1
    for day in days:
        class_start = row
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
        starts = set(bell_times) | {start for weekday, start in rows_by_slot if weekday == day}
        for start in sorted(starts):
            sections = rows_by_slot.get((day, start), {})
            ends = {lesson["end"] for entries in sections.values() for lesson, _ in entries}
            stop = bell_times.get(start) or (next(iter(ends)) if len(ends) == 1 else "")
            time = f"{start}–{stop}" if stop else f"с {start}"
            placements = []
            placement_notes = {}
            for grade, entries in sorted(sections.items(), key=lambda item: columns_by_grade[item[0]]):
                divisions, positions = layouts[grade][day, start]
                count = seats[grade]
                for entry in entries:
                    left, right, notice = positions[entry[0]["id"]]
                    col = columns_by_grade[grade] + left * count // divisions
                    span = right * count // divisions - left * count // divisions
                    placements.append((grade, entry, 0, col, span))
                    if notice:
                        placement_notes[entry[0]["id"]] = notice
            cell(row, 0, time, bg="#EEF3F8", bold=bool(sections), size=8)
            heights[row] = 21
            slot_ranges.append({"start": row, "end": row + 1})
            for grade, (lesson, continuation), offset, col, width in placements:
                lesson_id = str(lesson["id"])
                details = info[lesson_id]
                card_row = row + offset
                anchor = lesson_id not in mapping
                group = cell_group(lesson, details, groups, grade, classes_by_grade)
                text, size, text_height = card_caption(details, group, sum(widths[col:col+width]), club=details["club"],
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
            row += 1

        class_band_ranges.append({"start": class_start, "end": row})
        clubs = sorted(clubs_by_day[day], key=lambda l: (l["start"], l["end"], str(l["id"])))
        if clubs:
            cell(row, 0, "Кружки", width=columns, bg="#EDF1F5", bold=True)
            heights[row] = 23
            row += 1
            # Use only real sheet columns; merge entire cards across grade boundaries.
            per_row = min(3, columns - 1, max(1, sum(widths[1:]) // 145))
            for first in range(0, len(clubs), per_row):
                batch = clubs[first:first + per_row]
                heights[row] = 21
                for index, lesson in enumerate(batch):
                    lid = str(lesson["id"])
                    details = info[lid]
                    col = 1 + index * (columns - 1) // len(batch)
                    right = 1 + (index + 1) * (columns - 1) // len(batch)
                    known_classes = list(dict.fromkeys(c for c in [
                        *(lesson.get("displayClassIds") or []),
                        *((lesson.get("audience") or {}).get("class_ids") or []),
                        *lesson["classIds"],
                    ] if c in grade_by_class))
                    labels = [_label(groups[c]) for c in known_classes]
                    labels.extend(group_caption(groups.get(key, {})) for key in details["group_ids"])
                    audience = ", ".join(dict.fromkeys(filter(None, labels)))
                    text, size, text_height = card_caption(
                        details, audience, sum(widths[col:right]), club=True,
                    )
                    heights[row] = max(heights[row], text_height)
                    cell(row, col, text, width=right-col, bg=details["bg"], fg=contrast_text(details["bg"]),
                         size=size, note="\n".join(details["notes"]))
                    mapping[lid] = dict(lesson_id=lid, source_lesson_ids=lesson["_source_ids"],
                        source_hashes=lesson["_source_hashes"], logical_id=lesson["_logical_id"],
                        row=row, col=col, segment_start=lesson["start"], segment_end=lesson["end"],
                        day=day, class_ids=lesson["classIds"],
                        grade_numbers=sorted({grade_by_class[c] for c in lesson["classIds"]}),
                        group_ids=details["group_ids"], teacher_ids=details["teacher_ids"], references=[], rows=1,
                        group_label=audience, review=details["notice"], section="clubs")
                row += 1

    mapping = list(mapping.values())
    source_ids = [sid for item in mapping for sid in item["source_lesson_ids"]]
    if len(source_ids) != len(set(source_ids)) or set(source_ids) != {str(l["id"]) for l in payload["lessons"]}:
        raise ValueError("Экспорт потерял связь с исходными занятиями.")
    context = {"layout": GRID_LAYOUT, "logical_occurrence_audit": logical_audit}
    return {"format_version": FORMAT_VERSION, "layout": GRID_LAYOUT, "title": title, "sheet_id": sheet_id,
            "mapping_sheet_id": mapping_sheet_id, "snapshot_id": snapshot["snapshot_id"], "snapshot": snapshot,
            "rows": row, "overview_rows": row, "columns": columns, "column_widths": widths, "row_heights": heights,
            "day_rows": day_rows, "day_labels": day_labels, "frozen_rows": 1, "slot_ranges": slot_ranges, "cells": cells, "mapping": mapping,
            "class_band_ranges": class_band_ranges if any(clubs_by_day.values()) else None,
            "class_columns": [{"start": col, "width": seats[grade]} for grade, col in columns_by_grade.items()],
            "presentation_context": context, "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(source_ids), "placements": len(mapping),
                        "clarifications": sum(bool(item["review"]) for item in mapping)}}
