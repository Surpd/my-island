from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from uuid import NAMESPACE_URL, uuid5
from typing import Any

from backend.services.schedule_rooms import room_key
from backend.services.schedule_subjects import subject_label
from backend.services.schedule_experiments import (
    ExperimentConflict,
    ExperimentNotFound,
    _audience_fingerprint,
    _lesson_activity_type,
    _lesson_audience,
    _lesson_interval,
    _write_experiment_revision,
    _validate_day_modes,
    experiment_week_audience_preview,
    get_experiment,
)


def term_comparison_lesson_fingerprint(lesson: Mapping[str, Any], *, semantic=False) -> str:
    fields = (
        "day", "weekday", "period", "start", "end", "subject", "teacherId", "teacherIds",
        "room", "place", "classId", "classIds", "displayClassIds", "groupId", "groupIds",
        "audience", "audienceMode", "excludedGroupIds", "kind", "activityType",
        "specialMark", "blockId", "blockName", "format", "informational", "allDay",
    )
    author_fields = {key: lesson[key] for key in fields if key in lesson}
    if semantic:
        for key in ("subject", "room", "place"):
            author_fields[key] = _field_value(lesson, key)
    encoded = json.dumps(author_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _term_preparation(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    source = payload.get("school_source")
    if isinstance(source, Mapping):
        term = source.get("term2_preparation")
        if isinstance(term, Mapping):
            return term
    return {}


def _lesson_digests(payload: Mapping[str, Any], *, semantic=False) -> dict[str, str]:
    return {str(row["id"]): term_comparison_lesson_fingerprint(row, semantic=semantic)
            for row in payload.get("lessons") or [] if row.get("id")}


def retain_term_comparison(payload: dict[str, Any], previous: Mapping[str, Any] | None,
                           *, record_edits: bool = False, review_key: str | None = None,
                           actor_user_id: Any = None, audience_database: Any = None) -> None:
    """Discard client-written metadata and record edits from saved timetable changes."""
    source = payload.get("school_source")
    source = dict(source) if isinstance(source, Mapping) else {}
    term = dict(_term_preparation(payload))
    if "comparison" in term:
        del term["comparison"]
    if not payload.get("week_start") and previous:
        term.update(deepcopy(_term_preparation(previous)))
        comparison = term.get("comparison")
        if record_edits and isinstance(comparison, dict):
            before = _lesson_digests(previous, semantic=True)
            after = _lesson_digests(payload, semantic=True)
            raw_digests = _lesson_digests(payload)
            changes = {key: raw_digests.get(key) for key in before.keys() | after.keys()
                       if before.get(key) != after.get(key)}
            # Receipts from older revisions use the raw author-field digest. Keep a
            # verified previous edit actionable across a subsequent spelling edit.
            receipts = comparison.get("manual_edits") or {}
            if receipts:
                previous_raw = _lesson_digests(previous)
                for key in receipts.keys() & raw_digests.keys() - changes.keys():
                    if receipts[key] == previous_raw.get(key):
                        receipts[key] = raw_digests[key]
            if changes:
                comparison.setdefault("manual_edits", {}).update(changes)
                _close_reviewed_edits(comparison, previous, payload, changes, review_key, actor_user_id, audience_database)
    if term:
        source["term2_preparation"] = term
    elif "term2_preparation" in source:
        del source["term2_preparation"]
    if source:
        payload["school_source"] = source
    else:
        payload.pop("school_source", None)


def _slot_matches(lesson: Mapping[str, Any], target: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    start, end = target.get("start"), target.get("end")
    if not (isinstance(start, str) and isinstance(end, str) and target.get("day") is not None):
        return False
    interval = _lesson_interval({"start": start, "end": end}, [])
    return bool(interval and lesson.get("day", lesson.get("weekday")) == target["day"]
                and _lesson_interval(lesson, payload.get("slots") or []) == interval)


def record_term_comparison_decision(database: Any, draft_id: str, *, expected_revision: int, key: str,
                                    action: str, reason: str, lesson_id: str | None,
                                    actor_user_id: Any) -> dict[str, Any]:
    draft = get_experiment(database, draft_id)
    if int(draft["revision"]) != expected_revision:
        raise ExperimentConflict(draft)
    payload = deepcopy(draft["payload"])
    preparation = _term_preparation(payload)
    comparison = preparation.get("comparison")
    if payload.get("week_start") or not isinstance(comparison, dict):
        raise ValueError("Сверка доступна только в проверочной копии шаблона.")
    item = next((row for row in comparison.get("items", []) if row.get("key") == key), None)
    if item is None:
        raise ValueError("Позиция сверки отсутствует.")
    if item.get("initial_status") == "accepted":
        raise ValueError("Это изменение уже внесено при подготовке копии.")
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("Укажите основание решения до 1000 символов.")
    statuses = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}
    if action not in statuses:
        raise ValueError("Недопустимое решение по сверке.")
    receipts = comparison.setdefault("decisions", [])
    prior = next((row for row in reversed(receipts) if row.get("key") == key), None)
    if prior and prior.get("status") in {"accepted", "rejected"}:
        raise ValueError("Решение по этой позиции уже сохранено.")

    linked_id = None
    if action == "accept":
        baseline = comparison.get("baseline_lessons")
        if not isinstance(baseline, Mapping) or not baseline:
            raise ValueError("В копии нет сохранённой версии для проверки правки.")
        saved = {str(row.get("id") or ""): row for row in payload.get("lessons") or []}
        manual_edits = comparison.get("manual_edits") or {}
        source_id = str(item.get("lesson_id") or "")
        linked_id = str(lesson_id or source_id)
        if source_id and linked_id != source_id:
            raise ValueError("Сохраните правку в занятии, связанном с позицией сверки.")
        if item.get("kind") == "removed":
            if (not source_id or source_id not in baseline or source_id in saved
                    or source_id not in manual_edits or manual_edits[source_id] is not None):
                raise ValueError("Удалите связанное занятие в редакторе и сохраните черновик.")
        else:
            lesson = saved.get(linked_id)
            digest = term_comparison_lesson_fingerprint(lesson) if lesson else None
            if not digest or baseline.get(linked_id) == digest or manual_edits.get(linked_id) != digest:
                raise ValueError("Сначала внесите правку в редакторе и сохраните черновик.")
            target = item.get("after")
            if not isinstance(target, Mapping) or not _slot_matches(lesson, target, payload):
                raise ValueError("Выберите сохранённое занятие в слоте, указанном в таблице.")

    receipts.append({
        "key": key,
        "status": statuses[action],
        "reason": reason,
        "lesson_id": linked_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor_user_id),
    })
    return _write_experiment_revision(database, draft_id, expected_revision, payload,
                                      actor_user_id=actor_user_id)


def _field_value(row, key):
    value = row.get(key)
    if key == "subject":
        return subject_label(value).casefold()
    if key in {"room", "place"}:
        return room_key(value)
    return value


def _close_reviewed_edits(comparison, previous, payload, changes, review_key, actor_user_id, audience_database):
    receipts = comparison.setdefault("decisions", [])
    closed = {row["key"] for row in receipts if row.get("status") in {"accepted", "rejected"}}
    pending = [row for row in comparison.get("items", [])
               if row.get("initial_status") != "accepted" and row["key"] not in closed]
    old = {str(row.get("id")): row for row in previous.get("lessons", [])}
    new = {str(row.get("id")): row for row in payload.get("lessons", [])}
    for item in pending:
        lesson_id = str(item.get("lesson_id") or "")
        linked = [row for row in pending if lesson_id and str(row.get("lesson_id") or "") == lesson_id]
        if lesson_id in changes:
            before, after = old.get(lesson_id), new.get(lesson_id)
            if len(linked) > 1:
                if review_key == item["key"] and len(_matching_saved_rows(item, payload, new, changes, audience_database)) == 1:
                    receipts.append(_receipt(item["key"], "manual", lesson_id, actor_user_id))
                continue
            if after is None:
                if item.get("kind") != "removed" and review_key != item["key"]:
                    continue
            elif review_key != item["key"]:
                a, b = item.get("before") or {}, item.get("after") or {}
                fields = {
                    "subject": ("subject", "kind", "specialMark"), "kind": ("kind", "activityType", "specialMark"),
                    "room": ("room", "place"), "teachers": ("teacherId", "teacherIds"),
                    "groups": ("groupId", "groupIds", "audience"), "classes": ("classIds", "classId", "audience"),
                    "day": ("day",), "start": ("start", "period"), "end": ("end", "period"),
                }
                relevant = {field for key, names in fields.items() if _field_value(a, key) != _field_value(b, key) for field in names}
                if not any(_field_value(before or {}, field) != _field_value(after, field) for field in relevant):
                    continue
        elif not lesson_id and review_key == item["key"] and isinstance(item.get("after"), Mapping):
            resolved = _matching_saved_rows(item, payload, new, changes, audience_database)
            if len(resolved) != 1:
                continue
            lesson_id = resolved[0]
        else:
            continue
        receipts.append(_receipt(item["key"], "manual", lesson_id, actor_user_id))


def _matching_saved_rows(item, payload, lessons, changes, database):
    target = item.get("after")
    if not database or not isinstance(target, Mapping) or not target.get("classes"):
        return []
    groups, _ = _directory(database)
    try:
        classes = _match_names(target["classes"], [g for g in groups if g["group_type"] == "class"], "класс")
    except ValueError:
        return []
    return [key for key in changes if key in lessons
            and _slot_matches(lessons[key], target, payload)
            and _field_value(lessons[key], "subject") == _field_value(target, "subject")
            and set(_class_ids(lessons[key])) == set(classes)]


choice_reasons = {
    "current": "Оставлена рабочая копия.",
    "original": "Восстановлен вариант исходного шаблона.",
    "sheet": "Применён вариант из таблицы.",
    "manual": "Сохранена ручная правка в конструкторе.",
}
choice_codes = ("current", "original", "sheet")


def _receipt(key, choice, lesson_id, actor_user_id):
    return {
        "key": key, "status": "accepted", "choice": choice, "reason": choice_reasons[choice],
        "lesson_id": lesson_id, "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor_user_id),
    }


def _choice_context(database, draft_id, expected_revision, key):
    draft = get_experiment(database, draft_id)
    if int(draft["revision"]) != expected_revision:
        raise ExperimentConflict(draft)
    payload = draft["payload"]
    term = _term_preparation(payload)
    comparison = term.get("comparison")
    if payload.get("week_start") or not isinstance(comparison, dict):
        raise ValueError("Сверка доступна только в отдельной рабочей копии шаблона.")
    if str(term.get("derived_from_id")) == str(draft_id):
        raise ValueError("Исходный шаблон защищён от изменений через сверку.")
    item = next((row for row in comparison.get("items", []) if row.get("key") == key), None)
    if item is None:
        raise ValueError("Позиция сверки отсутствует.")
    if item.get("initial_status") == "accepted" or any(
        row.get("key") == key and row.get("status") in {"accepted", "rejected"}
        for row in comparison.get("decisions", [])
    ):
        raise ValueError("Решение по этой позиции уже сохранено.")
    return payload, term, item


def _label(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"\s*·\s*базовый класс$", "", text)
    text = re.sub(r"^(\d{1,2})\s+класс$", r"\1", text)
    return " ".join(text.translate(str.maketrans("–—‑ё", "---е")).split())


def _directory(database):
    with database.connection() as connection:
        groups = database.execute(connection, """SELECT id,name,display_name,group_type FROM groups
            WHERE canonical IS TRUE AND archived_at IS NULL""").fetchall()
        teachers = database.execute(connection, """SELECT id,display_name FROM identities
            WHERE kind='teacher' AND status='active'""").fetchall()
    return [dict(row) for row in groups], [dict(row) for row in teachers]


def _match_names(values, records, kind):
    result = []
    for label in values or []:
        ids = {str(row["id"]) for row in records
               if _label(label) in {_label(row.get("name")), _label(row.get("display_name"))}}
        if len(ids) != 1:
            raise ValueError(f"Нужно уточнить {kind} «{label}» в конструкторе.")
        result.extend(ids)
    return list(dict.fromkeys(result))


def _class_ids(lesson):
    return [str(value) for value in (lesson.get("classIds") or lesson.get("displayClassIds")
                                     or ([lesson["classId"]] if lesson.get("classId") else []))]


def _with_classes(lesson, classes):
    for key in ("displayClassIds", "grade_scope"):
        lesson.pop(key, None)
    lesson.update(classId=classes[0], classIds=classes)
    return lesson


def _projection(payload, lesson, group_labels, teacher_labels):
    interval = _lesson_interval(lesson, payload.get("slots") or [])
    times = [f"{value // 60:02d}:{value % 60:02d}" for value in interval] if interval else ["", ""]
    audience = _lesson_audience(lesson)
    snapshot = lesson.get("audience_snapshot")
    whole_class = audience["kind"] == "classes" and not any(
        audience.get(key) for key in ("include_student_ids", "exclude_student_ids")
    )
    return {"subject": lesson.get("subject") or "Занятие", "day": lesson.get("day", lesson.get("weekday")),
            "start": times[0], "end": times[1], "room": lesson.get("room") or "",
            "classes": [group_labels.get(key, "Класс требует уточнения") for key in _class_ids(lesson)],
            "teachers": [teacher_labels.get(str(key), "Преподаватель требует уточнения")
                         for key in lesson.get("teacherIds") or ([lesson["teacherId"]] if lesson.get("teacherId") else [])],
            "groups": [group_labels.get(key, "Группа требует уточнения") for key in audience["group_ids"]],
            "whole_class": whole_class,
            **({"student_count": len(snapshot.get("student_ids") or [])} if isinstance(snapshot, Mapping) else {})}


def _verify_class_teaching(database, teacher_ids, classes, subject, lesson_date):
    # Compare complete subject names/known abbreviations. Exam and subgroup
    # assignments do not prove that the same teacher teaches the whole class.
    subject_key = subject_label(subject).casefold()
    class_placeholders = ", ".join("?" for _ in classes)
    with database.connection() as connection:
        rows = database.execute(connection, f"""SELECT teacher_identity_id, group_id, subject FROM teacher_assignments
            WHERE active IS TRUE AND capability = 'teach' AND group_id IN ({class_placeholders})
              AND classroom_course_id IS NULL AND COALESCE(TRIM(exam_track), '') = ''
              AND COALESCE(TRIM(subject_subgroup), '') = ''
              AND (valid_from IS NULL OR valid_from <= ?) AND (valid_until IS NULL OR valid_until >= ?)""",
            (*classes, lesson_date, lesson_date)).fetchall()
    matching = {(str(row["teacher_identity_id"]), str(row["group_id"])) for row in rows
                if subject_label(row["subject"]).casefold() == subject_key}
    if not {(teacher_id, class_id) for teacher_id in teacher_ids for class_id in classes} <= matching:
        raise ValueError("В таблице не указана учебная группа. В справочнике нет назначения этих преподавателей на предмет для всего указанного класса.")


def _resolve_class_roster(database, payload, replacement, reference):
    resolved = experiment_week_audience_preview(database, {**payload, "lessons": [replacement]}, reference)
    roster = resolved["lessons"][replacement["id"]]
    students = roster.get("proposed_student_ids")
    if roster["status"] != "ok" or students is None:
        raise ValueError("Назначение на весь класс найдено. Проверьте действующий состав класса в справочнике.")
    replacement["audience_snapshot"] = {"student_ids": students,
                                        "fingerprint": _audience_fingerprint(replacement, payload),
                                        "resolved_at": ""}


def _sheet_lessons(database, draft_id, payload, term, item, current, groups, teachers):
    target = item.get("after")
    if not isinstance(target, Mapping):
        if item.get("kind") == "removed" and item.get("lesson_id") and current:
            before = item.get("before")
            if isinstance(before, Mapping):
                class_ids = _match_names(before.get("classes"), [g for g in groups if g["group_type"] == "class"], "класс")
                if class_ids and set(class_ids) == set(_class_ids(current)):
                    return [str(current["id"])], []
        raise ValueError("Нельзя однозначно определить, какую запись следует убрать.")
    # A missing link is not evidence that an existing lesson should be replaced.
    if current is None and item.get("kind") != "added":
        raise ValueError("Выберите связанное занятие в конструкторе.")
    kind = target.get("kind") or "lesson"
    subject = str(target.get("subject") or "").strip()
    if not subject or kind not in {"lesson", "lunch"}:
        raise ValueError("Для этой записи нужно уточнить тип и назначения в конструкторе.")
    base_classes = [g for g in groups if g["group_type"] == "class" and str(g["id"]) in payload["classes"]]
    classes = _match_names(target.get("classes"), base_classes, "класс")
    if not classes:
        raise ValueError("Укажите, к какому классу относится занятие.")
    slots = payload.get("slots") or []
    periods = [i for i, slot in enumerate(slots) if _slot_matches({"day": target["day"], "period": i}, target, payload)]
    if len(periods) != 1 or target.get("day") not in payload["days"]:
        raise ValueError("Проверьте время в конструкторе. Оно отличается от звонков в рабочей копии.")
    teacher_ids = _match_names(target.get("teachers"), teachers, "преподавателя")
    group_ids = _match_names(target.get("groups"), [g for g in groups if g["group_type"] != "class"], "группу")
    if current and (_lesson_activity_type(current) != "lesson" or current.get("blockId")):
        raise ValueError("Занятие входит в отдельный блок или каталог. Уточните тип в конструкторе.")
    whole_class = kind == "lesson" and not group_ids
    if kind == "lesson":
        if target.get("warning"):
            raise ValueError(str(target["warning"]))
        if not teacher_ids:
            raise ValueError("В таблице не указан преподаватель. Выберите преподавателя в конструкторе.")
    if whole_class:
        reference = date.fromisoformat(str(term.get("first_week") or payload.get("reference_date") or date.today()))
        lesson_date = reference + timedelta(days=target["day"] - reference.weekday())
        _verify_class_teaching(database, teacher_ids, classes, subject, lesson_date)
    replacement = {"id": str(current["id"]) if current else str(uuid5(NAMESPACE_URL, f"{draft_id}/{item['key']}")),
                   "subject": subject, "day": target["day"], "period": periods[0],
                   "start": target["start"], "end": target["end"], "room": target.get("room") or "",
                   "teacherId": teacher_ids[0] if teacher_ids else "", "teacherIds": teacher_ids,
                   "groupId": group_ids[0] if group_ids else classes[0], "groupIds": group_ids or classes,
                   "kind": "lesson", "activityType": "lesson",
                   "audience": {"kind": "groups", "group_ids": group_ids} if group_ids
                   else {"kind": "classes", "class_ids": classes}}
    _with_classes(replacement, classes)
    if kind == "lunch":
        replacement["specialMark"] = "lunch"
    if current is None:
        if any(_slot_matches(other, target, payload) and _field_value(other, "subject") == subject_label(subject).casefold()
               and set(_class_ids(other)) & set(classes) for other in payload["lessons"]):
            raise ValueError("В этом слоте уже есть похожее занятие. Укажите нужную запись в конструкторе.")
        if whole_class:
            _resolve_class_roster(database, payload, replacement, reference)
        return [], [replacement]

    current_classes = _class_ids(current)
    remaining = [key for key in current_classes if key not in classes]
    rule = _lesson_audience(current)
    if remaining:
        if kind != "lunch" or not _slot_matches(current, target, payload) or not set(classes) <= set(current_classes):
            raise ValueError("Занятие охватывает несколько классов. Уточните разбиение в конструкторе.")
        replacement["id"] = str(uuid5(NAMESPACE_URL, f"{draft_id}/{item['key']}"))
        if any(other.get("id") != current["id"] and _slot_matches(other, target, payload)
               and other.get("specialMark") == "lunch" and set(_class_ids(other)) & set(classes)
               for other in payload["lessons"]):
            raise ValueError("В этом слоте уже есть обед для этого класса.")
        rest = _with_classes(deepcopy(current), remaining)
        _split_lunch_audience(database, payload, term, current, rest, replacement)
        return [str(current["id"])], [rest, replacement]
    if (kind == "lesson" and set(classes) == set(current_classes)
            and rule["kind"] == replacement["audience"]["kind"] and set(group_ids) == set(rule["group_ids"])):
        # Preserve the precise rule and confirmed roster when only timetable fields change.
        replacement["audience"] = deepcopy(current.get("audience") or replacement["audience"])
        if isinstance(current.get("audience_snapshot"), Mapping):
            replacement["audience_snapshot"] = deepcopy(current["audience_snapshot"])
    elif rule.get("kind") in {"students", "available_slot", "legacy_remaining"} or any(
        rule.get(key) for key in ("include_student_ids", "exclude_student_ids")
    ):
        raise ValueError("Сохранён индивидуальный состав. Уточните изменения состава в конструкторе.")
    if whole_class and "audience_snapshot" not in replacement:
        _resolve_class_roster(database, payload, replacement, reference)
    preserved = ("resolution_history", "source", "format") if kind == "lesson" else ("resolution_history", "source")
    for field in preserved:
        if field in current:
            replacement[field] = deepcopy(current[field])
    return [str(current["id"])], [replacement]


def _split_lunch_audience(database, payload, term, current, remaining, lunch):
    rule = _lesson_audience(current)
    class_sets = (_class_ids(current), _class_ids(lunch), _class_ids(remaining))
    probes = [{"id": f"class-{index}", "day": lunch["day"],
               "audience": {"kind": "classes", "class_ids": classes}}
              for index, classes in enumerate(class_sets)]
    effective_date = date.fromisoformat(str(payload.get("reference_date") or term.get("first_week") or date.today()))
    preview = experiment_week_audience_preview(database, {**payload, "lessons": [current, *probes]}, effective_date)["lessons"]
    assigned, all_classes, lunch_classes, remaining_classes = (
        preview[str(key)] for key in (current["id"], *(probe["id"] for probe in probes)))
    if any(row["status"] != "ok" for row in (assigned, all_classes, lunch_classes, remaining_classes)):
        raise ValueError("Не удалось подтвердить состав для разделения урока и обеда.")
    participants = set(assigned["student_ids"])
    lunch_students = set(lunch_classes["student_ids"])
    remaining_students = set(remaining_classes["student_ids"])
    if (not participants <= set(all_classes["student_ids"]) or lunch_students & remaining_students
            or not participants <= lunch_students | remaining_students
            or not participants & remaining_students):
        raise ValueError("Состав общего урока нельзя однозначно разделить по классам.")
    if rule["kind"] == "classes":
        remaining["audience"] = {"kind": "classes", "class_ids": _class_ids(remaining)}
        remaining["groupIds"] = _class_ids(remaining)
        remaining["groupId"] = remaining["groupIds"][0]
    elif rule["kind"] in {"groups", "students"}:
        remaining["audience"] = {**rule, "class_ids": _class_ids(remaining), "grade": "",
                                "exclude_student_ids": sorted(set(rule.get("exclude_student_ids") or []) | lunch_students)}
    else:
        raise ValueError("Состав общего урока требует ручного уточнения.")
    for lesson, ids in ((remaining, participants - lunch_students), (lunch, lunch_students)):
        lesson["audience_snapshot"] = {"student_ids": sorted(ids), "fingerprint": _audience_fingerprint(lesson, payload),
                                      "resolved_at": ""}


def _choice_plan(database, draft_id, payload, term, item, choice, groups, teachers):
    linked = str(item.get("lesson_id") or "")
    current = next((row for row in payload["lessons"] if str(row.get("id")) == linked), None)
    if choice == "current":
        return [], []
    if choice == "sheet":
        return _sheet_lessons(database, draft_id, payload, term, item, current, groups, teachers)
    if choice != "original":
        raise ValueError("Выберите один из вариантов сверки.")
    if item.get("kind") == "added" and not item.get("before") and not linked:
        return [], []
    if not linked or not term.get("derived_from_id"):
        raise ValueError("Для этой строки не установлено исходное занятие.")
    try:
        source = get_experiment(database, term["derived_from_id"])
    except ExperimentNotFound:
        raise ValueError("Исходная редакция шаблона недоступна.") from None
    if source["revision"] != term.get("derived_from_revision"):
        raise ValueError("Исходный шаблон уже изменился. Для восстановления нужна редакция из сверки.")
    original = next((row for row in source["payload"]["lessons"] if str(row.get("id")) == linked), None)
    if original is None:
        raise ValueError("В исходной редакции нет сопоставленного занятия.")
    if not set(_class_ids(original)) <= set(payload["classes"]):
        raise ValueError("Проверьте исходные классы в конструкторе.")
    if current and set(_class_ids(current)) != set(_class_ids(original)) and any(
        decision.get("choice") == "sheet" and decision.get("lesson_id") == linked
        for decision in _term_preparation(payload)["comparison"].get("decisions", [])
    ):
        raise ValueError("Занятие уже разделено по другой позиции. Уточните исходный вариант в конструкторе.")
    restored = deepcopy(original)
    source_slots = source["payload"]["slots"]
    current_slots = payload["slots"]
    if source_slots != current_slots:
        interval = _lesson_interval(original, source_slots)
        periods = [index for index in range(len(current_slots))
                   if _lesson_interval({"period": index}, current_slots) == interval]
        if len(periods) != 1:
            raise ValueError("Время исходного занятия отличается от звонков рабочей копии.")
        restored["period"] = periods[0]
    if current and "resolution_history" in current:
        restored["resolution_history"] = deepcopy(current["resolution_history"])
    return ([linked] if current else []), [restored]


def _plan_token(draft_id, revision, key, choice, removed, added):
    data = json.dumps([draft_id, revision, key, choice, removed, added], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def preview_term_comparison_choices(database, draft_id, *, expected_revision, key, audience_database=None):
    payload, term, item = _choice_context(database, draft_id, expected_revision, key)
    directory = audience_database or database
    groups, teachers = _directory(directory)
    group_labels = {str(row["id"]): row["display_name"] or row["name"] for row in groups}
    teacher_labels = {str(row["id"]): row["display_name"] for row in teachers}
    result = {}
    for choice in choice_codes:
        try:
            removed, added = _choice_plan(database if choice == "original" else directory, draft_id,
                                          payload, term, item, choice, groups, teachers)
            previous = [row for row in payload["lessons"] if str(row.get("id")) in removed]
            changed = previous != added
            result[choice] = {
                "token": _plan_token(draft_id, expected_revision, key, choice, removed, added),
                "before": [_projection(payload, row, group_labels, teacher_labels) for row in previous] if changed else [],
                "after": [_projection(payload, row, group_labels, teacher_labels) for row in added] if changed else [],
            }
        except ValueError as error:
            result[choice] = {"unavailable": str(error)}
    return result


def apply_term_comparison_choice(database, draft_id, *, expected_revision, key, choice, preview_token,
                                 actor_user_id, audience_database=None):
    current, term, item = _choice_context(database, draft_id, expected_revision, key)
    directory = audience_database or database
    groups, teachers = _directory(directory) if choice == "sheet" else ([], [])
    # Read the original from the same store as the comparison, including local mirrors.
    removed, added = _choice_plan(database if choice == "original" else directory, draft_id,
                                   current, term, item, choice, groups, teachers)
    if _plan_token(draft_id, expected_revision, key, choice, removed, added) != preview_token:
        raise ValueError("Вариант изменился. Откройте предпросмотр заново.")
    payload = deepcopy(current)
    replacements = {str(row["id"]): row for row in added}
    lessons = []
    for lesson in payload["lessons"]:
        identity = str(lesson.get("id"))
        if identity not in removed:
            lessons.append(lesson)
        elif identity in replacements:
            lessons.append(replacements.pop(identity))
    lessons.extend(replacements.values())
    payload["lessons"] = lessons
    if removed or added:
        _validate_day_modes(database, payload)
    comparison = _term_preparation(payload)["comparison"]
    linked = item.get("lesson_id") or (added[0]["id"] if len(added) == 1 else None)
    comparison.setdefault("decisions", []).append(_receipt(key, choice, linked, actor_user_id))
    return _write_experiment_revision(database, draft_id, expected_revision, payload,
                                      actor_user_id=actor_user_id)
