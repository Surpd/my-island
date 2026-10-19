"""One-time, source-led projection of the approved school template.

The sheet supplies occurrences; the canonical directory supplies identities.
This module accepts already parsed source cells and never persists rosters.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Mapping, Sequence

from backend.services.school_experiment_workspace import school_class_label


def _group_for_source(raw: str, subject: str, grade: str, groups_by_name: Mapping[str, str]) -> str:
    first = raw.splitlines()[0] if raw.splitlines() else raw
    english = re.search(r"(?:англ(?:ийский)?|english)\s*(?:группа\s*)?(\d{1,2})\b", first, re.IGNORECASE)
    if english:
        return groups_by_name.get(f"english:{int(english.group(1))}", "")
    math = re.search(r"(?:мат(?:ематика|ем)?|math)\s*(?:группа\s*)?([ABCАВС])\b", first, re.IGNORECASE)
    if math and grade == "9":
        letter = math.group(1).upper().translate(str.maketrans("АВС", "ABC"))
        return groups_by_name.get(f"grade9-math-{letter}", "")
    seventh = re.search(r"(?:мат(?:ематика|ем)?|math)\s*7[.\-]([12])\b", first, re.IGNORECASE)
    if seventh and grade == "7":
        return groups_by_name.get(f"grade7:split:{seventh.group(1)}", "")
    track = "огэ" if re.search(r"\bогэ\b", raw, re.IGNORECASE) else "егэ" if re.search(r"\bегэ\b", raw, re.IGNORECASE) else ""
    if track and subject:
        return groups_by_name.get(f"instructional:{subject.casefold()}:{grade}:{track}", "")
    return ""


def project_source_lessons(
    cells: Sequence[Mapping[str, Any]], semantics: Sequence[Mapping[str, Any]],
    *, groups: Sequence[Mapping[str, Any]], teachers: Sequence[Mapping[str, Any]],
    teacher_pairs: set[tuple[str, str]], group_classes: Mapping[str, set[str]],
    old_source_text: Mapping[str, str], old_lessons: Sequence[Mapping[str, Any]],
    slots: Sequence[Mapping[str, str]], sheet_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    group_by_name = {str(item["name"]): str(item["id"]) for item in groups}
    group_by_id = {str(item["id"]): item for item in groups}
    class_by_label: dict[str, str] = {}
    class_ids = set()
    for group in groups:
        if group.get("group_type") != "class":
            continue
        group_id = str(group["id"])
        class_ids.add(group_id)
        for label in (group.get("name"), group.get("display_name")):
            if label:
                class_by_label[school_class_label(str(label).split("·", 1)[0].strip())] = group_id
    teacher_ids = {str(item["id"]) for item in teachers}
    semantic_by_cell = {str(item["source_cell"]): item for item in semantics}
    old_by_cell: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for lesson in old_lessons:
        for coordinate in ((lesson.get("canonical") or {}).get("assignment") or {}).get("source_cells") or []:
            old_by_cell[str(coordinate)].append(lesson)
        for duplicate in lesson.get("source_duplicates") or []:
            for coordinate in duplicate.get("source_cells") or []:
                old_by_cell[str(coordinate)].append(lesson)
    slot_index = {(str(slot["start"]), str(slot["end"])): index for index, slot in enumerate(slots)}
    projected: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for cell in cells:
        coordinate = str(cell["source_cell"])
        raw = str(cell.get("raw_text") or "").strip()
        if not raw:
            continue
        semantic = semantic_by_cell.get(coordinate) or {}
        day = int(cell["weekday"])
        start, end = str(cell["start_time"]), str(cell["end_time"])
        owner = str(cell.get("audience") or "").strip()
        merged = [str(value) for value in cell.get("merged_audiences") or []]
        visual_class_ids = sorted({class_by_label[school_class_label(label)] for label in (merged or [owner])
                                   if school_class_label(label) in class_by_label})
        grade_match = re.match(r"\s*(\d+)", owner)
        grade = grade_match.group(1) if grade_match else ""
        previous = old_by_cell.get(coordinate) or []
        same_text = old_source_text.get(coordinate, "").strip() == raw
        is_lunch = "🥨" in raw or bool(re.match(r"\s*обед\b", raw, re.IGNORECASE))
        is_absence = bool(re.match(r"\s*нет\s+(?:урока|занятия)\b", raw, re.IGNORECASE))
        special = "lunch" if is_lunch else "no_lesson" if is_absence else ""
        subject = "Обед" if is_lunch else "Нет урока" if is_absence else str(semantic.get("subject") or "").strip()
        if not subject:
            subject = raw.splitlines()[0].strip()
        proposed_group = "" if special else _group_for_source(raw, subject, grade, group_by_name)
        if not proposed_group and not special and same_text:
            old_group_sets = {tuple(sorted(str(group_id) for group_id in item.get("groupIds") or [])) for item in previous}
            if len(old_group_sets) == 1:
                only = next(iter(old_group_sets))
                if len(only) == 1 and only[0] in group_by_id:
                    proposed_group = only[0]
        course_without_roster = bool(re.search(r"курс\s+по\s+выбору|цифров(?:ой|ые|ая)?\s+трек", raw, re.IGNORECASE))
        possible_digital_track = bool(re.search(r"программирован", raw, re.IGNORECASE)) and bool(group_by_name.get("generic:digital-track"))
        english_without_group = bool(re.search(r"англ|english", raw, re.IGNORECASE)) and not proposed_group
        if course_without_roster or possible_digital_track:
            proposed_group = ""
        if not proposed_group and not special and not course_without_roster and not possible_digital_track and not english_without_group and len(visual_class_ids) == 1:
            proposed_group = visual_class_ids[0]
        instructional = bool(proposed_group and proposed_group not in class_ids)
        class_for_group = sorted(group_classes.get(proposed_group, set())) if instructional else []
        if class_for_group and visual_class_ids and not set(class_for_group).intersection(visual_class_ids):
            proposed_group = ""
            instructional = False
            class_for_group = []
        class_ids_for_lesson = class_for_group or visual_class_ids
        parsed_teacher_ids = [] if special else [str(value) for value in semantic.get("resolved_identity_ids") or [] if str(value) in teacher_ids]
        if not parsed_teacher_ids and same_text:
            old_teacher_sets = {tuple(sorted(str(value) for value in item.get("teacherIds") or [])) for item in previous}
            if len(old_teacher_sets) == 1:
                parsed_teacher_ids = list(next(iter(old_teacher_sets)))
        teacher_id = parsed_teacher_ids[0] if len(set(parsed_teacher_ids)) == 1 else ""
        room = "" if special else str(semantic.get("room") or "").strip()
        if not room and same_text and len(previous) == 1:
            room = str(previous[0].get("room") or "")
        reasons = []
        if not class_ids_for_lesson:
            reasons.append("Не сопоставлен административный класс")
        if not special:
            if not proposed_group:
                reasons.append("Не подтверждена учебная группа или аудитория курса")
            elif instructional and not class_for_group:
                reasons.append("У группы нет подтверждённого состава по классам")
            if not teacher_id:
                reasons.append("Не подтверждён преподаватель")
            elif proposed_group and (teacher_id, proposed_group) not in teacher_pairs:
                reasons.append("Нет подтверждённого назначения преподавателя группе")
        if (start, end) not in slot_index:
            reasons.append("Нет соответствующего звонка")
        if special == "lunch":
            reasons.append("Уточните охват обеда: класс или отдельная группа")
        if course_without_roster:
            reasons.append("Направление курса не имеет отдельного подтверждённого roster")
        if possible_digital_track:
            reasons.append("Уточните, относится ли программирование к цифровому треку и каков состав группы")
        item = {
            "id": f"sheet:{sheet_id}:{coordinate}", "day": day,
            "period": slot_index.get((start, end), -1), "start": start, "end": end,
            "classId": class_ids_for_lesson[0] if len(class_ids_for_lesson) == 1 else "",
            "classIds": class_ids_for_lesson, "groupId": proposed_group,
            "groupIds": [proposed_group] if proposed_group else [],
            "audience": ({"kind": "groups", "group_ids": [proposed_group]} if instructional
                         else {"kind": "classes", "class_ids": [proposed_group]} if proposed_group in class_ids
                         else {"kind": "", "group_ids": [], "class_ids": []}),
            "subject": subject, "teacherId": teacher_id,
            "teacherIds": [teacher_id] if teacher_id else [], "room": room,
            "needsClarification": bool(reasons), "clarificationReason": "; ".join(dict.fromkeys(reasons)),
            "source": {"sheet_id": sheet_id, "sheet_title": "2026/27 шаблон",
                       "cells": [coordinate], "raw_text": raw,
                       "raw_text_by_cell": {coordinate: raw}, "source_class_label": owner},
        }
        if special:
            item["specialMark"] = special
        projected.append(item)
        if reasons:
            issues.append({"source_cells": [coordinate], "day": day, "start": start, "end": end,
                           "grade": grade, "reason": item["clarificationReason"], "raw_text": raw,
                           "proposed_lesson_id": item["id"], "status": "needs_confirmation"})
    # A confirmed cross-class instructional group has one occurrence at a time.
    # Preserve all source cells on that occurrence; never merge unrelated courses.
    result: list[dict[str, Any]] = []
    by_group_time: dict[tuple[Any, ...], dict[str, Any]] = {}
    for lesson in projected:
        group_id = lesson["groupId"]
        key = (lesson["day"], lesson["start"], lesson["end"], group_id,
               lesson["subject"], lesson["teacherId"])
        if group_id and group_id not in class_ids and not lesson.get("specialMark") and key in by_group_time:
            existing = by_group_time[key]
            existing["source"]["cells"].extend(lesson["source"]["cells"])
            existing["source"]["raw_text_by_cell"].update(lesson["source"]["raw_text_by_cell"])
            existing["classIds"] = sorted(set(existing["classIds"]) | set(lesson["classIds"]))
            existing["classId"] = existing["classIds"][0] if len(existing["classIds"]) == 1 else ""
            existing["needsClarification"] |= lesson["needsClarification"]
            if lesson["clarificationReason"] and lesson["clarificationReason"] not in existing["clarificationReason"]:
                existing["clarificationReason"] = "; ".join(filter(None, (existing["clarificationReason"], lesson["clarificationReason"])))
            for issue in issues:
                if issue["proposed_lesson_id"] == lesson["id"]:
                    issue["proposed_lesson_id"] = existing["id"]
            continue
        result.append(lesson)
        if group_id and group_id not in class_ids and not lesson.get("specialMark"):
            by_group_time[key] = lesson
    return result, issues
