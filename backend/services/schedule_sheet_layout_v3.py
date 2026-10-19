"""Bounded-width school layout; presentation policy never mutates source lessons."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
import json
import math
import re
import textwrap
from typing import Any, Mapping

from backend.services.schedule_sheet_export import (
    DAY_NAMES, FORMAT_VERSION, _class_key, _compact_text, _hash, _label, column_name,
)


# Exact names explicitly classified by the teacher, not a time-of-day heuristic.
OPTIONAL_NAMES = {
    "анимация": "Анимация", "айкидо": "Айкидо", "испанский": "Испанский",
    "испанский язык": "Испанский", "геймдизайн": "Геймдизайн", "гейм-дизайн": "Геймдизайн",
    "шахматы": "Шахматы", "японские шахматы": "Японские шахматы",
    "академический рисунок": "Академический рисунок", "рисование": "Рисование",
    "ателье": "Ателье", "вокал": "Вокал",
}
# Exact identity confirmed by the teacher and the 2026-10-17 directory audit.
# The older immutable snapshot calls this same class 9-А. Do not rename the DB.
CLASS_DISPLAY_NAMES = {"b021cc32-b132-4842-9cf3-54d7960112ac": "9-Ан"}
GRADE_WIDTHS = {5: 116, 6: 116, 7: 180, 8: 124, 9: 252, 10: 180, 11: 180}


def activity_semantics(lesson: Mapping[str, Any]) -> dict[str, str]:
    subject = str(lesson.get("subject") or "")
    name = re.sub(r"^[^\wА-Яа-яЁё]+", "", subject).strip()
    snapshot_name = str(lesson.get("activityNameSnapshot") or lesson.get("activity_name_snapshot") or "").strip()
    display_name = snapshot_name or name
    key = " ".join(display_name.casefold().replace("ё", "е").split())
    kind = str(lesson.get("activityType") or lesson.get("activity_type") or lesson.get("kind") or "").casefold()
    catalog_id = lesson.get("activityCatalogId") or lesson.get("activity_catalog_id")
    eligible = lesson.get("eligibleGrades", lesson.get("eligible_grades", [])) or []
    direction = lesson.get("electiveDirection") or lesson.get("elective_direction")

    # Persisted type is authoritative: a programming club must never be inferred
    # from its name, and a normal lesson named like a catalog item stays a lesson.
    if kind in {"lesson", "club", "elective", "event"}:
        canonical = kind
        if kind == "club":
            section, participation = "clubs", "opt_in"
        elif kind == "elective":
            section, participation = "schedule", "opt_in"
        elif kind == "event":
            section, participation = "schedule", "unspecified"
        else:
            section, participation = "schedule", "unspecified"
        semantics = {"activity_type": canonical, "participation": participation, "section": section,
                     "classification_basis": "explicit_activity_type", "display_name": display_name}
    elif subject.lstrip().startswith("⚪") and key in OPTIONAL_NAMES:
        semantics = {"activity_type": "optional_activity", "participation": "opt_in", "section": "clubs",
                     "classification_basis": "teacher_confirmed_legacy_catalog", "display_name": OPTIONAL_NAMES[key]}
    elif subject.lstrip().startswith("⚪"):
        return {"activity_type": "unspecified", "participation": "unspecified", "section": "activity_review",
                "classification_basis": "legacy_marker_is_not_a_confirmed_type", "display_name": name}
    else:
        semantics = {"activity_type": "unspecified", "participation": "unspecified", "section": "schedule",
                     "classification_basis": "existing_schedule_record", "display_name": name}
    if catalog_id is not None:
        semantics["activity_catalog_id"] = str(catalog_id)
    if snapshot_name:
        semantics["activity_name_snapshot"] = snapshot_name
    if eligible:
        semantics["eligible_grades"] = ",".join(map(str, sorted(set(eligible))))
    if direction:
        semantics["elective_direction"] = str(direction)
    return semantics


_FULL_SUBJECT_TITLES = {
    "программирование": "Программирование", "программ.": "Программирование",
    "компьютерная графика": "Компьютерная графика", "комп. граф.": "Компьютерная графика",
    "графика": "Компьютерная графика", "граф.": "Компьютерная графика",
    "графический дизайн": "Графический дизайн",
    "шум · медиамастерская": "ШУМ · Медиамастерская", "шум · медиа": "ШУМ · Медиамастерская",
    "шум медиамастерская": "ШУМ · Медиамастерская",
    "лаборатория · прошлое · настоящее": "Лаборатория · Прошлое · Настоящее",
    "лаб. · прошл./наст.": "Лаборатория · Прошлое · Настоящее",
    "прошлое · настоящее": "Лаборатория · Прошлое · Настоящее",
    "история искусства": "История искусства", "ист. иск.": "История искусства", "иск.": "История искусства",
    "ист-иск": "История искусства", "графический дизайн": "Графический дизайн",
    "лаб текста": "Лаборатория текста", "лаборатория": "Лаборатория",
    "english through cinema": "English Through Cinema", "кухня кино. озвучка": "Кухня кино. Озвучка",
    "лингвистический детектив": "Лингвистический детектив", "логикон": "Логикон",
    "шальная наука": "Шальная наука", "юннаты xxi": "Юннаты XXI",
    "cambridge flyers": "Cambridge Flyers",
}


def _full_subject_title(subject: Any) -> str | None:
    value = " ".join(str(subject or "").strip().split())
    value = re.sub(r"^[^\wА-Яа-яЁё]+", "", value)
    key = " ".join(value.casefold().replace("ё", "е").split())
    if key in _FULL_SUBJECT_TITLES:
        return _FULL_SUBJECT_TITLES[key]
    # Group labels can trail an approved canonical title in the source subject.
    for alias, title in _FULL_SUBJECT_TITLES.items():
        if key.startswith(alias + " · "):
            return title + value[len(alias):]
    return None


def _grade_regions(active, resolved, grades):
    """Shared activities join adjacent grade areas into one horizontal panel."""
    intervals = []
    for lesson in active:
        targets = resolved[str(lesson["id"])]["grade_numbers"]
        intervals.append((min(grades.index(g) for g in targets), max(grades.index(g) for g in targets)))
    intervals.extend((i, i) for i in range(len(grades)))
    runs = []
    for left, right in sorted(intervals):
        if runs and left <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], right)
        else:
            runs.append([left, right])
    return [grades[left:right+1] for left, right in runs]


def _final_caption(text, pixel_width):
    # Full fields remain in notes; these are conventional school abbreviations.
    replacements = {"Математика": "Мат.", "Естествознание": "Естество.", "Программирование": "Программ.",
                    "Компьютерная графика": "Комп. граф.", "Медиамастерская": "медиа", "Классный час": "Кл. час",
                    "Прошлое · Настоящее": "Прошл./Наст.", "История": "Ист.", "Биология": "Биол.",
                    "Физика": "Физ.", "Литература": "Литер.", "Репетиция": "Репет."}
    for old, short in replacements.items():
        text = re.sub(re.escape(old), short, text, flags=re.I)
    lines = text.splitlines()
    if lines:
        for old, short in {"Cambridge Flyers": "Flyers", "Лингвистический детектив": "Лингв.",
                           "English Through Cinema": "Кино-англ.", "Кухня кино. Озвучка": "Озвуч.",
                           "Шальная наука": "Наука", "Матпроф Мат. · угл.": "Матпроф"}.items():
            lines[0] = lines[0].replace(old, short)
        for old, short in {"Русский · свободные": "Рус. своб.", "Естество.": "Ест.",
                           "Ист. иск.": "Иск.", "Творчество": "Творч.", "География": "Геогр.",
                           "Пластика": "Пласт.", "Комп. граф.": "Граф.",
                           "Лаб. · Прошл./Наст.": "Прош./наст."}.items():
            lines[0] = lines[0].replace(old, short)
        lines[0] = re.sub(r"ШУМ · медиа · группа (\d+)", r"ШУМ \1", lines[0])
        lines[0] = re.sub(r"Репет\. (\d+-[А-Яа-я]+) · \1", r"Репет. \1", lines[0])
        if pixel_width < 110:
            lines[0] = lines[0].replace("Логикон", "Лог.").replace("Юннаты XXI", "Юннаты")
        lines[0] = re.sub(r"\[([\d/–]+)\]", r"\1", lines[0])
        if pixel_width < 70:
            lines[0] = lines[0].replace("Лингв. ", "Лингв.").replace("Юннаты ", "Юн. ")
    if len(lines) > 2:
        lines = [lines[0], " · ".join(lines[1:])]
    if pixel_width < 110 and len(lines) > 1:
        for old, short in {"Ангелина": "Анг.", "Леонид": "Леон.", "Анастасия": "Анаст.",
                           "Татьяна": "Тат.", "Андрей": "Андр.", "Родион": "Род.", "Алексей": "Алекс."}.items():
            lines[1] = lines[1].replace(old, short)
        if pixel_width < 65:
            lines[1] = lines[1].replace("Алекс.", "Ал.")
    return "\n".join(lines)


def build_readable_plan(snapshot: Mapping[str, Any], by_day: Mapping[int, list[dict[str, Any]]],
                        groups: Mapping[str, Any], teachers: Mapping[str, Any], classes: list[Mapping[str, Any]],
                        *, title: str, sheet_id: int, mapping_sheet_id: int,
                        audience_evidence: Mapping[str, Any], existing_notes: Mapping[str, list[str]],
                        final_compact: bool = False, logical_audit: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = snapshot["payload"]
    source_version = (snapshot.get("source", {}).get("school_source") or {}).get("canonical_version_id")
    evidence_version = (audience_evidence.get("evidence_source") or {}).get("canonical_version_id")
    if source_version and audience_evidence and evidence_version != source_version:
        raise ValueError("Audience evidence belongs to another canonical version")
    display_groups = deepcopy(dict(groups))
    for cid, label in CLASS_DISPLAY_NAMES.items():
        if cid in display_groups:
            display_groups[cid]["display_name"] = label
    class_grades = {str(g["id"]): _class_key(g)[0] for g in classes}
    if 999 in class_grades.values():
        raise ValueError("Every class must have a grade number")
    grade_classes: dict[int, set[str]] = defaultdict(set)
    for cid, grade in class_grades.items():
        grade_classes[grade].add(cid)
    grades = sorted(grade_classes)
    semantics = {str(l["id"]): activity_semantics(l) for ls in by_day.values() for l in ls}
    resolved = {}
    issues = []
    for items in by_day.values():
        for lesson in items:
            lid = str(lesson["id"])
            audience = lesson.get("audience") or {}
            kind = audience.get("kind")
            source_cids = set(lesson["classIds"])
            cids = set(source_cids)
            gids = list(audience.get("group_ids") or lesson.get("groupIds") or [])
            if kind == "classes":
                explicit = set(audience.get("class_ids") or [])
                if explicit:
                    if not explicit <= class_grades.keys():
                        raise ValueError(f"Unknown explicit audience class: {lid}")
                    cids = explicit
            elif kind == "groups":
                # Cached scopes may add a missing display grade but may never
                # exclude a source grade or establish current student membership.
                cached = {c for gid in gids for c in audience_evidence.get("group_class_ids", {}).get(gid, [])
                          if c in class_grades}
                if cached and cached != source_cids:
                    issues.append({"lesson_id": lid, "kind": "cached_group_scope_differs",
                                   "source_class_ids": sorted(source_cids), "cached_class_ids": sorted(cached),
                                   "resolution": "group label retained; visual grades use union; roster not changed"})
                cids |= cached
            resolved[lid] = {"class_ids": sorted(cids), "grade_numbers": sorted({class_grades[c] for c in cids}),
                             "audience_kind": str(kind or "unspecified"), "group_ids": gids,
                             "source_class_ids": sorted(source_cids)}
            # Eligibility is a grade-level display scope, never a class or
            # participant assignment. This also permits a new club with no
            # source class IDs to appear in the sheet.
            eligible = lesson.get("eligibleGrades", lesson.get("eligible_grades"))
            if eligible and semantics[lid]["activity_type"] in {"club", "elective"}:
                eligible_grades = sorted(set(eligible))
                unknown = set(eligible_grades) - set(grades)
                if unknown:
                    raise ValueError(f"Unknown eligible activity grades for {lid}: {sorted(unknown)}")
                resolved[lid]["grade_numbers"] = eligible_grades
            elif not resolved[lid]["grade_numbers"] and semantics[lid]["activity_type"] in {"elective", "event"}:
                # A classless timed item gets a general visual placement over
                # the grade columns. This does not create class/audience IDs.
                resolved[lid]["grade_numbers"] = list(grades)
            if semantics[lid]["section"] == "activity_review":
                issues.append({"lesson_id": lid, "kind": "activity_type_unconfirmed", "subject": lesson["subject"]})
    # Derive horizontal divisions from local single-grade concurrency. Shared
    # groups have their own narrow stacked tracks, never extra wide columns.
    bands = {}
    divisions = dict.fromkeys(grades, 1)
    for day in sorted(payload["days"]):
        main = [l for l in by_day.get(day, []) if semantics[str(l["id"])]["section"] == "schedule"]
        boundaries = sorted({s[k] for s in payload["slots"] for k in ("start", "end")} |
                            {l[k] for l in main for k in ("start", "end")})
        intervals = [(a, b) for a, b in zip(boundaries, boundaries[1:])
                     if any(s["start"] <= a and b <= s["end"] for s in payload["slots"])
                     or any(l["start"] <= a and b <= l["end"] for l in main)]
        if final_compact:
            intervals = sorted({(s["start"], s["end"]) for s in payload["slots"]} |
                               {(l["start"], l["end"]) for l in main if not any(
                                   l["start"] < s["end"] and s["start"] < l["end"] for s in payload["slots"])})
            intervals = [(a, b) for a, b in intervals if any(l["start"] < b and a < l["end"] for l in main)]
        bands[day] = []
        for start, end in intervals:
            active = sorted((l for l in main if l["start"] < end and start < l["end"]), key=lambda l: str(l["id"]))
            single = {g: [l for l in active if resolved[str(l["id"])]["grade_numbers"] == [g]] for g in grades}
            shared = [l for l in active if len(resolved[str(l["id"])]["grade_numbers"]) > 1]
            for grade in grades:
                divisions[grade] = math.lcm(divisions[grade], max(1, len(single[grade])))
            bands[day].append((start, end, single, shared))
    widths = [76]
    grade_columns = {}
    gutters = []
    for i, grade in enumerate(grades):
        if i:
            gutters.append(len(widths))
            widths.append(8)
        units = 12 if final_compact else divisions[grade]
        start = len(widths)
        grade_width = GRADE_WIDTHS.get(grade, 160)
        # The total stays exact even when a grade width is not divisible by LCM.
        widths.extend(grade_width//units + (1 if j < grade_width % units else 0) for j in range(units))
        grade_columns[grade] = dict(id=str(grade), label=str(grade), start=start, width=units)
    columns = len(widths)
    cells = []
    mapping = []
    row_heights = {}
    day_rows = []
    slot_ranges = []
    day_sections = []
    day_metrics = []
    monday = date.fromisoformat(payload["week_start"])

    def cell(r, c, text, *, h=1, w=1, bg="#FFFFFF", fg="#202124", size=10, bold=False,
             note="", align="LEFT"):
        value = dict(row=r, col=c, text=text, height=h, width=w, bg=bg, fg=fg, size=size,
                     bold=bold, note=note, align=align)
        cells.append(value)
        return value

    def add_lesson(lesson, row, col, height, width, start, end, section, target_grades, region_grades=None):
        lid = str(lesson["id"])
        audience = resolved[lid]
        cids = sorted(c for c in audience["class_ids"] if class_grades[c] in target_grades)
        explicit = audience["audience_kind"] == "classes"
        partial = explicit and any(set(cids) & grade_classes[g] != grade_classes[g] for g in target_grades)
        captions = [_label(display_groups[c]) for c in cids] if partial else []
        text, full, bg = _compact_text(lesson, display_groups, teachers, captions)
        text = text.replace("Матем.", "Математика").replace("Лаборатория", "Лаб.").replace("Английский", "Англ.")
        pixel_width = sum(widths[col:col+width])
        full_title_caption = False
        size = 9 if pixel_width < 110 else 10
        if section != "schedule":
            original_lines = text.splitlines()
            text = f"{start}–{end} · {semantics[lid]['display_name']}"
            if len(original_lines) > 1:
                text += "\n" + " · ".join(original_lines[1:])
            if section == "activity_review":
                text += " · тип уточнить"
        elif pixel_width < 100 and not final_compact:
            # Room on its own short line avoids squeezing the teacher's name.
            lines = text.splitlines()
            if len(lines) > 1:
                lines[-1] = lines[-1].replace(" · ", "\n")
            text = "\n".join(lines)
        if final_compact:
            if section == "schedule" and region_grades and len(region_grades) > 1:
                # The merged grade area has one card per occurrence. Explicit
                # scope labels make it readable to every participating grade.
                ordered = sorted(target_grades)
                scope = (f"{ordered[0]}–{ordered[-1]}" if len(ordered) > 1 and ordered == list(range(ordered[0], ordered[-1]+1))
                         else "/".join(map(str, ordered)))
                lines = text.splitlines()
                lines[0] += " [" + scope + "]"
                text = "\n".join(lines)
            if section == "schedule":
                text = _final_caption(text, pixel_width)
                canonical_title = (_full_subject_title(lesson.get("subject")) or
                                   _full_subject_title(lesson.get("activityNameSnapshot") or lesson.get("activity_name_snapshot")))
                if semantics[lid]["activity_type"] == "elective":
                    canonical_title = canonical_title or semantics[lid]["display_name"]
                if canonical_title:
                    full_title_caption = True
                    lines = text.splitlines()
                    if lines:
                        scope = ""
                        if region_grades and len(region_grades) > 1:
                            ordered_scope = sorted(target_grades)
                            label_scope = (f"{ordered_scope[0]}–{ordered_scope[-1]}" if len(ordered_scope) > 1 and ordered_scope == list(range(ordered_scope[0], ordered_scope[-1]+1))
                                           else "/".join(map(str, ordered_scope)))
                            scope = " [" + label_scope + "]"
                        badge = ""
                        if semantics[lid]["activity_type"] == "elective":
                            badge = ("Цифровой трек" if semantics[lid].get("elective_direction", "").casefold() == "digital"
                                     else "Курсы по выбору")
                            if canonical_title.casefold() == badge.casefold():
                                badge = ""
                        lines[0] = (badge + " · " if badge else "") + canonical_title + scope
                        text = "\n".join(lines)
            if lesson.get("_dedup_conflicts"):
                lines = text.splitlines()
                lines[0] += " ?"
                text = "\n".join(lines)
        source_ids = lesson.get("_source_ids", [lid])
        identity = {"format": FORMAT_VERSION, "snapshot_id": snapshot["snapshot_id"], "lesson_id": lid,
                    "class_ids": cids}
        if final_compact:
            identity.update(logical_lesson_id=lesson["_logical_id"], source_lesson_ids=source_ids)
        note = "MY_ISLAND:" + json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        note += "\n" + full + f"\nПолное время: {lesson['start']}–{lesson['end']}"
        for field, label in (("description", "Описание"), ("place", "Место"), ("format", "Формат"), ("note", "Примечание")):
            if lesson.get(field):
                note += f"\n{label}: {lesson[field]}"
        note += "\nАудитория: " + audience["audience_kind"] + "; группы: " + ", ".join(audience["group_ids"])
        if audience["audience_kind"] == "groups":
            note += "\nЗанятие учебной группы. Исходная колонка/текущий состав группы не ограничивает урок административным классом."
        if any(_label(groups[c]) != _label(display_groups[c]) for c in cids):
            note += "\nНазвание 9-Ан подтверждено пользователем и аудитом 17.10; в старом справочнике тот же ID подписан 9-А."
        if semantics[lid]["activity_type"] not in {"lesson", "unspecified"}:
            note += "\nТип: " + semantics[lid]["activity_type"] + "; участие: " + semantics[lid]["participation"]
        if section != "schedule":
            note += "\nИсходные classIds сохранены как provenance; они не назначают необязательную активность ученикам."
        if lesson.get("needsClarification"):
            note += "\nТребует уточнения: " + str(lesson.get("clarificationReason") or "Аудитория занятия")
        if final_compact:
            if len(source_ids) > 1:
                note += "\nОдно логическое занятие; связанные исходные ID: " + ", ".join(source_ids)
                note += "\nПодтверждённые преподаватель/кабинет дополнены из совместимых исходных записей."
            if lesson.get("_dedup_conflicts"):
                note += "\nНе объединено: " + ", ".join(lesson["_dedup_conflicts"])
            for sid, reason in lesson.get("_source_clarifications", {}).items():
                note += f"\nИсходное уточнение {sid}: {reason}"
        for sid in source_ids:
            for previous_note in existing_notes.get(sid, []):
                if previous_note and previous_note not in note:
                    note += "\nСохранённое примечание " + sid + ":\n" + previous_note
        rgb = [int(bg[i:i+2], 16)/255 for i in (1, 3, 5)]
        fg = "#FFFFFF" if sum(v*k for v, k in zip(rgb, (.2126, .7152, .0722))) < .48 else "#202124"
        cell(row, col, text, h=height, w=width, bg=bg, fg=fg, size=size, note=note)
        # Wrapped text estimate uses actual card width and font, with breathing
        # room in every row. Narrow three-card tracks retain a 9pt floor.
        chars = max(7, int((pixel_width-8)/(size*.56)))
        lines = sum(max(1, math.ceil(len(line)/chars)) for line in text.splitlines())
        needed = math.ceil((lines*(size*1.45)+(8 if final_compact else 14))/height)
        if full_title_caption:
            # Word wrapping can need more lines than character-count division,
            # especially a long Cyrillic word in a half-grade card. Allow this
            # individual band extra space while keeping ordinary slots at 46px.
            title_chars = max(5, int((pixel_width-10)/(size*.7)))
            wrapped_lines = sum(max(1, len(textwrap.wrap(line, width=title_chars, break_on_hyphens=False)))
                                for line in text.splitlines())
            needed = max(needed, math.ceil((wrapped_lines*size*1.6+12)/height))
        for rr in range(row, row+height):
            row_heights[rr] = max(row_heights.get(rr, 46 if final_compact else 50), needed)
        mapping.append({**identity, "sheet_id": sheet_id,
                        "range": f"{column_name(col)}{row+1}:{column_name(col+width-1)}{row+height}",
                        "content_hash": _hash(lesson), "needs_clarification": bool(lesson.get("needsClarification")),
                        "grade_numbers": target_grades, "segment_start": start, "segment_end": end,
                        "source_class_ids": lesson["classIds"], **semantics[lid],
                        "audience_kind": audience["audience_kind"], "group_ids": audience["group_ids"]})
        if final_compact:
            mapping[-1].update(source_hashes=lesson["_source_hashes"], lesson_start=lesson["start"], lesson_end=lesson["end"])

    cell(0, 0, "Время", bg="#E8EAED", bold=True, align="CENTER")
    for grade, geo in grade_columns.items():
        cell(0, geo["start"], str(grade), w=geo["width"], bg="#E8EAED", bold=True, size=14, align="CENTER")
    cell(1, 0, f"{monday:%d.%m}–{monday+timedelta(days=4):%d.%m}", size=9, align="CENTER")
    cell(1, 1, "My Island · неделя из шаблона · уточнения и полные названия в примечаниях", w=columns-1, size=9)
    row_heights.update({0: 30, 1: 25})
    row = 2
    for day in sorted(payload["days"]):
        day_start_row = row
        if day_rows:
            if not final_compact:
                row_heights[row] = 10
                row += 1
        day_rows.append(row)
        day_short = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[day]
        cell(row, 0, f"{day_short} {monday+timedelta(days=day):%d.%m}", bg="#344054", fg="#FFFFFF", bold=True, align="CENTER")
        cell(row, 1, DAY_NAMES[day], w=columns-1, bg="#344054", fg="#FFFFFF", bold=True, size=12)
        row_heights[row] = 26 if final_compact else 30
        row += 1
        for start, end, single, shared in bands[day]:
            if final_compact:
                active = [l for ls in single.values() for l in ls] + shared
                cell(row, 0, f"{start}\n{end}", bg="#F3F4F6", size=9, bold=True, align="CENTER")
                row_heights[row] = 46
                for region in _grade_regions(active, resolved, grades):
                    activities = sorted((l for l in active if set(resolved[str(l["id"])]["grade_numbers"]) & set(region)),
                                        key=lambda l: str(l["id"]))
                    if not activities:
                        continue
                    left = grade_columns[region[0]]["start"]
                    right = grade_columns[region[-1]]["start"] + grade_columns[region[-1]]["width"]
                    for i, lesson in enumerate(activities):
                        c1, c2 = left+i*(right-left)//len(activities), left+(i+1)*(right-left)//len(activities)
                        add_lesson(lesson, row, c1, 1, c2-c1, max(start, lesson["start"]), min(end, lesson["end"]),
                                   "schedule", resolved[str(lesson["id"])]["grade_numbers"], region)
                slot_ranges.append({"start": row, "end": row+1, "day": day, "start_time": start, "end_time": end})
                row += 1
                continue
            tracks = []
            placements = []
            # Shared groups may stack; local single-grade lessons are ALWAYS
            # one horizontal bucket. No subject-based merging/deduplication.
            def reserve(targets):
                track = next((i for i, used in enumerate(tracks) if not used.intersection(targets)), len(tracks))
                if track == len(tracks):
                    tracks.append(set())
                tracks[track].update(targets)
                return track
            for lesson in shared:
                targets = resolved[str(lesson["id"])]["grade_numbers"]
                placements.append((reserve(targets), targets, [lesson], True))
            for grade, lessons in single.items():
                if lessons:
                    placements.append((reserve([grade]), [grade], lessons, False))
            count = len(tracks) or 1
            cell(row, 0, f"{start}\n{end}", h=count, bg="#F3F4F6", size=9, bold=True, align="CENTER")
            for rr in range(row, row+count):
                row_heights[rr] = 50
            for track, targets, lessons, is_shared in placements:
                # Extend into unoccupied tracks for the same grades, leaving
                # shared activities in the other grades readable side-by-side.
                height = 1
                while track+height < count and not tracks[track+height].intersection(targets):
                    height += 1
                if is_shared:
                    runs = []
                    for grade in targets:
                        geo = grade_columns[grade]
                        if runs and grades.index(grade) == grades.index(runs[-1][2][-1])+1:
                            runs[-1][1] = geo["start"]+geo["width"]
                            runs[-1][2].append(grade)
                        else:
                            runs.append([geo["start"], geo["start"]+geo["width"], [grade]])
                    for left, right, run_grades in runs:
                        add_lesson(lessons[0], row+track, left, height, right-left, start, end, "schedule", run_grades)
                else:
                    geo = grade_columns[targets[0]]
                    unit = geo["width"]//len(lessons)
                    for i, lesson in enumerate(lessons):
                        add_lesson(lesson, row+track, geo["start"]+i*unit, height, unit,
                                   start, end, "schedule", targets)
            slot_ranges.append({"start": row, "end": row+count})
            row += count
            row_heights[row] = 6
            row += 1
        for section, heading in (("clubs", "Кружки"),
                                 ("activity_review", "Дополнительные занятия · тип уточнить")):
            activities = sorted((l for l in by_day.get(day, []) if semantics[str(l["id"])]["section"] == section),
                                key=lambda l: (l["start"], str(l["id"])))
            if not activities:
                continue
            day_sections.append({"day": day, "row": row, "section": section})
            cell(row, 1, heading, w=columns-1, bg="#EDF1F5", bold=True)
            row_heights[row] = 23 if final_compact else 27
            row += 1
            # Three roomy cards per row, independent of historical grade columns.
            card_width = (columns-1)//3
            for i in range(0, len(activities), 3):
                for j, lesson in enumerate(activities[i:i+3]):
                    left = 1+j*card_width
                    width = card_width if j < 2 else columns-left
                    add_lesson(lesson, row, left, 1, width, lesson["start"], lesson["end"], section,
                               resolved[str(lesson["id"])]["grade_numbers"])
                row += 1
        day_metrics.append({"day": day, "start_row": day_start_row, "end_row": row,
                            "rows": row-day_start_row, "height_px": sum(row_heights[r] for r in range(day_start_row, row))})
    # Validate exact coverage of every logical ID, grade and time segment.
    lessons = [l for ls in by_day.values() for l in ls]
    if {m["lesson_id"] for m in mapping} != {str(l["id"]) for l in lessons}:
        raise ValueError("V3 lost a source lesson")
    for lesson in lessons:
        lid = str(lesson["id"])
        for grade in resolved[lid]["grade_numbers"]:
            if final_compact:
                actual = sorted((m["segment_start"], m["segment_end"]) for m in mapping
                                if m["lesson_id"] == lid and grade in m["grade_numbers"])
                if semantics[lid]["section"] == "schedule":
                    expected = sorted((max(a, lesson["start"]), min(b, lesson["end"]))
                                      for a, b, _, _ in bands[int(lesson["day"])]
                                      if lesson["start"] < b and a < lesson["end"])
                else:
                    expected = [(lesson["start"], lesson["end"])]
                if actual != expected:
                    raise ValueError(f"Final layout lost slot coverage: {lid}")
                continue
            cursor = lesson["start"]
            for start, end in sorted((m["segment_start"], m["segment_end"]) for m in mapping
                                     if m["lesson_id"] == lid and grade in m["grade_numbers"]):
                if start != cursor:
                    raise ValueError(f"V3 duplicated or omitted a grade/time segment: {lid}")
                cursor = end
            if cursor != lesson["end"]:
                raise ValueError(f"V3 incomplete lesson: {lid}")
    occupied = set()
    for c in cells:
        for rr in range(c["row"], c["row"]+c["height"]):
            for cc in range(c["col"], c["col"]+c["width"]):
                if (rr, cc) in occupied:
                    raise ValueError("V3 cell collision")
                occupied.add((rr, cc))
    context = {"layout": "readable-v3", "policy_version": 1, "class_display_names": CLASS_DISPLAY_NAMES,
               "audience_evidence": dict(audience_evidence), "optional_catalog": OPTIONAL_NAMES,
               "activities": semantics, "resolved_audiences": resolved, "issues": issues,
               "preserved_notes": dict(existing_notes)}
    if final_compact:
        context.update(layout="final-compact", logical_occurrence_audit=dict(logical_audit or {}))
        source_ids = {sid for m in mapping for sid in m["source_lesson_ids"]}
        if source_ids != {str(l["id"]) for l in payload["lessons"]}:
            raise ValueError("Final layout lost a source alias")
    result = {"format_version": FORMAT_VERSION, "layout": "final-compact" if final_compact else "readable-v3", "snapshot_id": snapshot["snapshot_id"],
            "title": title, "sheet_id": sheet_id, "mapping_sheet_id": mapping_sheet_id, "rows": row,
            "columns": columns, "column_widths": widths, "cells": cells, "mapping": mapping,
            "snapshot": deepcopy(dict(snapshot)), "row_heights": row_heights, "day_rows": day_rows,
            "slot_ranges": slot_ranges, "day_sections": day_sections, "gutters": gutters,
            "class_columns": list(grade_columns.values()), "presentation_context": context,
            "presentation_fingerprint": _hash(context),
            "summary": {"lessons": len(lessons), "placements": len(mapping), "grades": len(grades),
                        "width_px": sum(widths), "optional_activities": sum(s["section"] == "clubs" for s in semantics.values()),
                        "activity_types_to_review": sum(s["section"] == "activity_review" for s in semantics.values()),
                        "audience_scope_differences": sum(i["kind"] == "cached_group_scope_differs" for i in issues),
                        "clarifications": sum(bool(l.get("needsClarification")) for l in lessons)}}
    result["day_metrics"] = day_metrics
    if final_compact:
        result["summary"].update(source_ids=len(source_ids), logical_occurrences=len(lessons),
                                  merged_duplicate_clusters=len(logical_audit["merged_clusters"]),
                                  ambiguous_duplicate_clusters=len(logical_audit["ambiguous_clusters"]))
    return result
