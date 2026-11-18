"""Explicit school-editor template selection, independent of canonical history."""
import hashlib
import json
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4
from typing import Any

from backend.database import Database
from backend.services.schedule_experiments import _item, create_experiment, get_experiment

_selection_lock = Lock()


def _encoded(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_encoded(payload).encode("utf-8")).hexdigest()


def _ids(*values: Any) -> list[str]:
    result: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            result.update(str(item) for item in value if item not in (None, ""))
        elif value not in (None, ""):
            result.add(str(value))
    return sorted(result)


def _lesson_semantics(lesson: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    audience = lesson.get("audience") if isinstance(lesson.get("audience"), dict) else {}
    snapshot = lesson.get("audience_snapshot") if isinstance(lesson.get("audience_snapshot"), dict) else {}
    canonical = lesson.get("canonical") if isinstance(lesson.get("canonical"), dict) else {}
    assignment = canonical.get("assignment") if isinstance(canonical.get("assignment"), dict) else {}
    metadata = assignment.get("metadata") if isinstance(assignment.get("metadata"), dict) else {}
    canonical_rule = metadata.get("audience_rule") if isinstance(metadata.get("audience_rule"), dict) else {}
    groups = _ids(lesson.get("classId"), lesson.get("classIds"), lesson.get("groupId"), lesson.get("groupIds"),
                  audience.get("class_ids"), audience.get("classIds"), audience.get("group_ids"), audience.get("groupIds"),
                  assignment.get("canonical_group_ids"))
    confirmed_members = _ids(lesson.get("confirmedParticipantIds"))
    members = _ids(lesson.get("studentIds"), lesson.get("student_ids"), snapshot.get("student_ids"),
                   confirmed_members, audience.get("student_ids"), audience.get("studentIds"),
                   audience.get("include_student_ids"), canonical_rule.get("include_student_ids"))
    excluded_members = _ids(audience.get("exclude_student_ids"), canonical_rule.get("exclude_student_ids"))
    excluded_groups = _ids(lesson.get("excludedGroupIds"), audience.get("partition_group_ids"),
                           audience.get("excludedGroupIds"), canonical_rule.get("partition_group_ids"))
    teachers = _ids(lesson.get("teacherId"), lesson.get("teacherIds"), lesson.get("responsible_teacher_ids"),
                    lesson.get("responsibleTeacherIds"), assignment.get("teacher_ids"), metadata.get("responsible_teacher_ids"))
    responsible_teachers = _ids(lesson.get("responsible_teacher_ids"), lesson.get("responsibleTeacherIds"),
                                metadata.get("responsible_teacher_ids"))
    slots = payload.get("slots") if isinstance(payload.get("slots"), list) else []
    period = lesson.get("period")
    try:
        period_index = int(period)
    except (TypeError, ValueError):
        period_index = -1
    slot = slots[period_index] if 0 <= period_index < len(slots) and isinstance(slots[period_index], dict) else {}
    explicit_start, explicit_end = lesson.get("start"), lesson.get("end")
    is_event = lesson.get("kind") == "event" or lesson.get("canonicalRole") == "special_event"
    if is_event or (explicit_start and explicit_end and (explicit_start != slot.get("start") or explicit_end != slot.get("end"))):
        start, end = explicit_start or slot.get("start") or "", explicit_end or slot.get("end") or ""
    else:
        start, end = slot.get("start") or explicit_start or "", slot.get("end") or explicit_end or ""
    activity_type = lesson.get("activityType") or metadata.get("activity_type") or "lesson"
    special_mark = lesson.get("specialMark") or metadata.get("special_mark") or ""
    audience_mode = lesson.get("audienceMode") or ""
    audience_kind = audience.get("kind") or canonical_rule.get("kind") or assignment.get("audience_kind") or ""
    canonical_role = lesson.get("canonicalRole") or assignment.get("role") or assignment.get("assignment_kind") or ""
    # This allowlist mirrors fields consumed by constructor publication and
    # canonical projections. Import coordinates, resolution history, snapshot
    # fingerprints and timestamps are deliberately excluded as service data.
    values: dict[str, Any] = {
        "subject": str(lesson.get("subject") or lesson.get("activity") or ""),
        "activity_type": str(activity_type),
        "activity_catalog_id": str(lesson.get("activityCatalogId") or ""),
        "activity_name": str(lesson.get("activityNameSnapshot") or ""),
        "elective_direction": str(lesson.get("electiveDirection") or ""),
        "eligible_grades": _ids(lesson.get("eligibleGrades")),
        "grade_scope": str(lesson.get("grade_scope") or audience.get("grade") or canonical_rule.get("grade") or ""),
        "day": lesson.get("day", lesson.get("weekday")),
        "period": period,
        "start": str(start), "end": str(end),
        "class_id": _ids(lesson.get("classId")), "class_ids": _ids(lesson.get("classIds")),
        "group_id": _ids(lesson.get("groupId")), "group_ids": _ids(lesson.get("groupIds")),
        "groups": groups,
        "audience_kind": str(audience_kind), "audience_mode": str(audience_mode),
        "audience_type": str(audience.get("type") or ""),
        "audience_classes": _ids(audience.get("class_ids"), audience.get("classIds")),
        "audience_groups": _ids(audience.get("group_ids"), audience.get("groupIds"), canonical_rule.get("class_group_id")),
        "members": members, "confirmed_members": confirmed_members,
        "excluded_members": excluded_members,
        "excluded_groups": excluded_groups,
        "partition_dimension": str(audience.get("partition_dimension") or canonical_rule.get("partition_dimension") or ""),
        "audience_snapshot_members": _ids(snapshot.get("student_ids")),
        "teachers": teachers, "responsible_teachers": responsible_teachers,
        "room": str(lesson.get("room") or metadata.get("room") or ""),
        "special_mark": str(special_mark), "kind": str(lesson.get("kind") or ""),
        "canonical_role": str(canonical_role),
        "activity_format": str(metadata.get("activity_format") or lesson.get("activity_format") or ""),
        "informational": bool(lesson.get("informational")),
        "sdep_independent": bool(lesson.get("sdepIndependent")),
        "format": str(lesson.get("format") or ""), "description": str(lesson.get("description") or ""),
        "place": str(lesson.get("place") or ""), "note": str(lesson.get("note") or ""),
        "protected": bool(lesson.get("protected")), "deferred": bool(lesson.get("deferred")),
        "needs_clarification": bool(lesson.get("needsClarification")),
        "clarification_reason": str(lesson.get("clarificationReason") or ""),
        "import_error": str(lesson.get("importError") or ""),
    }
    return values


_FIELD_LABELS = {
    "subject": ("Предмет или занятие", "Меняется название занятия в расписании."),
    "activity_type": ("Тип занятия", "Может изменить правила участия, отображения и проверки конфликтов."),
    "activity_catalog_id": ("Карточка занятия", "Меняется связь с каталогом специальных занятий."),
    "activity_name": ("Название занятия из каталога", "Меняется отображаемое название специального занятия."),
    "elective_direction": ("Направление курса по выбору", "Меняется направление доступности курса."),
    "eligible_grades": ("Параллели курса", "Меняется круг учеников, которым доступен курс."),
    "grade_scope": ("Параллель", "Меняется параллель, для которой назначено занятие."),
    "day": ("День недели", "Занятие переносится на другой день."),
    "period": ("Номер урока", "Меняется место занятия в сетке."),
    "start": ("Начало", "Меняется время занятия."), "end": ("Окончание", "Меняется время занятия."),
    "class_id": ("Основной класс", "Может измениться охват класса."),
    "class_ids": ("Классы", "Меняется круг классов, для которых строится расписание."),
    "group_id": ("Основная группа", "Может измениться охват учебной группы."),
    "group_ids": ("Учебные группы", "Меняется круг групп, которым назначено занятие."),
    "groups": ("Классы и группы", "Меняется круг учеников, которым назначено занятие."),
    "audience_kind": ("Правило охвата", "Меняется способ назначения участников."),
    "audience_mode": ("Режим охвата", "Меняются правила распределения учеников между занятиями."),
    "audience_type": ("Тип назначения участников", "Меняется способ, которым canonical связывает занятие с аудиторией."),
    "audience_classes": ("Классы в правиле охвата", "Меняется круг классов-участников."),
    "audience_groups": ("Группы в правиле охвата", "Меняется круг групп-участников."),
    "partition_dimension": ("Измерение распределения", "Меняются правила распределения участников между группами."),
    "members": ("Участники", "Меняется персональный список учеников на занятии."),
    "confirmed_members": ("Подтверждённые участники", "Меняется явно подтверждённый список учеников."),
    "excluded_members": ("Исключённые ученики", "Эти ученики больше не входят в занятие."),
    "excluded_groups": ("Исключённые группы", "Меняется остаточный охват занятия."),
    "audience_snapshot_members": ("Состав по подтверждённому снимку", "Меняется сохранённый состав учеников."),
    "teachers": ("Преподаватели", "Меняется персональное расписание преподавателей."),
    "responsible_teachers": ("Ответственные преподаватели", "Меняется, кому назначается ответственность за занятие."),
    "room": ("Кабинет", "Меняется место проведения занятия."),
    "special_mark": ("Специальный статус", "Меняются правила занятия, например обед или отсутствие урока."),
    "kind": ("Формат записи", "Меняется обработка события или урока."),
    "canonical_role": ("Роль занятия", "Меняется роль занятия при canonical-публикации."),
    "activity_format": ("Формат активности", "Меняется вид активности, назначаемый в canonical-расписании."),
    "informational": ("Информационный статус", "Меняется, считается ли запись занятием для участников."),
    "sdep_independent": ("Независимое занятие СДЭП", "Меняется участие в расписании СДЭП."),
    "format": ("Формат проведения", "Меняются сведения о формате занятия."),
    "description": ("Описание", "Меняется описание, которое увидят участники."),
    "place": ("Место проведения", "Меняется место, указанное участникам."),
    "note": ("Примечание", "Меняются дополнительные сведения о занятии."),
    "protected": ("Защищённое занятие", "Меняется необходимость отдельного согласования."),
    "deferred": ("Отложенное занятие", "Меняется, попадёт ли занятие в текущую публикацию."),
    "needs_clarification": ("Требуется уточнение", "Меняется готовность занятия к публикации."),
    "clarification_reason": ("Причина уточнения", "Меняется предупреждение администратору."),
    "import_error": ("Ошибка сопоставления", "Меняется проблема переноса исходных данных."),
}


def _display_names(database: Database, table: str, ids: list[str], known: dict[str, str] | None = None) -> list[str]:
    if not ids:
        return []
    if known is not None:
        return [known.get(value, value) for value in ids]
    try:
        with database.connection() as connection:
            placeholders = ",".join("?" for _ in ids)
            columns = "id,display_name" if table == "identities" else "id,display_name,name"
            rows = database.execute(connection, f"SELECT {columns} FROM {table} WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        names = {str(row["id"]): str(row["display_name"] or dict(row).get("name") or row["id"]) for row in rows}
        return [names.get(value, value) for value in ids]
    except Exception:
        return ids


def _name_maps(database: Database, *payloads: dict[str, Any]) -> dict[str, dict[str, str]]:
    group_ids: set[str] = set()
    identity_ids: set[str] = set()
    for payload in payloads:
        for index, lesson in enumerate(payload.get("lessons") or []):
            if not isinstance(lesson, dict):
                continue
            semantic = _lesson_semantics(lesson, payload)
            for key in ("groups", "audience_classes", "audience_groups", "excluded_groups"):
                group_ids.update(semantic[key])
            for key in ("members", "confirmed_members", "excluded_members", "audience_snapshot_members", "teachers", "responsible_teachers"):
                identity_ids.update(semantic[key])
    result: dict[str, dict[str, str]] = {"groups": {}, "identities": {}}
    for table, ids in (("groups", group_ids), ("identities", identity_ids)):
        if not ids:
            continue
        try:
            values = sorted(ids)
            with database.connection() as connection:
                placeholders = ",".join("?" for _ in values)
                columns = "id,display_name" if table == "identities" else "id,display_name,name"
                rows = database.execute(connection, f"SELECT {columns} FROM {table} WHERE id IN ({placeholders})", tuple(values)).fetchall()
            result[table] = {str(row["id"]): str(row["display_name"] or dict(row).get("name") or row["id"]) for row in rows}
        except Exception:
            pass
    return result


def _lesson_summary(database: Database, lesson: dict[str, Any], payload: dict[str, Any],
                    names: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    semantic = _lesson_semantics(lesson, payload)
    day = semantic["day"]
    day_label = _WEEKDAYS[day] if isinstance(day, int) and 0 <= day < 7 else day
    return {"subject": semantic["subject"], "day": day_label, "period": semantic["period"],
        "start": semantic["start"], "end": semantic["end"], "groups": _display_names(database, "groups", semantic["groups"], (names or {}).get("groups")),
        "members": semantic["members"], "teachers": _display_names(database, "identities", semantic["teachers"], (names or {}).get("identities")), "room": semantic["room"],
        "activity_type": semantic["activity_type"], "special_mark": semantic["special_mark"],
        "audience_kind": semantic["audience_kind"], "audience_mode": semantic["audience_mode"],
        "excluded_groups": semantic["excluded_groups"],
        "problems": sorted(str(v) for v in (lesson.get("clarificationReason"),) if v)
            + (["importError"] if lesson.get("importError") else [])
            + (["needsClarification"] if lesson.get("needsClarification") else [])}


def _lesson_changes(before: dict[str, Any], after: dict[str, Any], before_payload: dict[str, Any],
                    after_payload: dict[str, Any], database: Database,
                    names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    left, right = _lesson_semantics(before, before_payload), _lesson_semantics(after, after_payload)
    name_fields = {"class_id", "class_ids", "group_id", "group_ids", "groups", "audience_classes", "audience_groups", "excluded_groups"}
    person_fields = {"members", "confirmed_members", "excluded_members", "audience_snapshot_members", "teachers", "responsible_teachers"}
    changes = []
    for key in _FIELD_LABELS:
        if left[key] == right[key]:
            continue
        name_table = "groups" if key in name_fields else "identities" if key in person_fields else ""
        before_text = ", ".join(_display_names(database, name_table, left[key], names.get(name_table))) if name_table else display_value(left[key])
        after_text = ", ".join(_display_names(database, name_table, right[key], names.get(name_table))) if name_table else display_value(right[key])
        changes.append({"field": key, "label": _FIELD_LABELS[key][0], "impact": _FIELD_LABELS[key][1],
             "before": left[key], "after": right[key], "before_text": before_text or "нет", "after_text": after_text or "нет"})
    return changes


def display_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "нет"
    if value in (None, ""):
        return "нет"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_MODE_LABELS = {"normal": "Обычный день", "online": "Онлайн", "sdep": "СДЭП"}


def _payload_semantics(payload: dict[str, Any]) -> dict[str, Any]:
    def modes(value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        result = {}
        for key, days in value.items():
            if isinstance(days, dict):
                normalized = {str(day): str(mode) for day, mode in days.items() if mode not in (None, "", "normal")}
                if normalized:
                    result[str(key)] = dict(sorted(normalized.items(), key=lambda pair: int(pair[0])))
        return dict(sorted(result.items()))
    slots = payload.get("slots") if isinstance(payload.get("slots"), list) else []
    return {
        "days": sorted(set(int(day) for day in payload.get("days", []) if str(day).isdigit())),
        "slots": [{"start": str(slot.get("start") or ""), "end": str(slot.get("end") or "")}
                  for slot in slots if isinstance(slot, dict)],
        "classes": _ids(payload.get("classes")),
        "day_modes": modes(payload.get("day_modes")),
        "grade_modes": modes(payload.get("grade_modes")),
        "lessons": {str(lesson.get("id") or index): _lesson_semantics(lesson, payload)
                    for index, lesson in enumerate(payload.get("lessons") or []) if isinstance(lesson, dict)},
    }


def _human_modes(database: Database, values: dict[str, dict[str, str]], *, grades: bool) -> str:
    scope_labels: dict[str, str] = {}
    if not grades:
        ids = sorted(values)
        if ids:
            try:
                with database.connection() as connection:
                    placeholders = ",".join("?" for _ in ids)
                    rows = database.execute(connection,
                        f"SELECT id,name,display_name FROM groups WHERE id IN ({placeholders})", tuple(ids)).fetchall()
                scope_labels = {str(row["id"]): str(row["display_name"] or row["name"] or row["id"]) for row in rows}
            except Exception:
                scope_labels = {}
    rows = []
    for scope, days in values.items():
        for day, mode in days.items():
            day_name = _WEEKDAYS[int(day)] if day.isdigit() and 0 <= int(day) < 7 else day
            target = f"{scope} класс" if grades else scope_labels.get(scope, f"класс/группа {scope}")
            rows.append(f"{target}, {day_name}: {_MODE_LABELS.get(mode, mode)}")
    return "; ".join(rows) or "Обычный режим"


def _payload_changes(database: Database, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    left, right = _payload_semantics(before), _payload_semantics(after)
    changes = []
    for key, label, impact in (
        ("days", "Учебные дни", "Меняются дни недели, для которых формируется расписание."),
        ("slots", "Звонки и интервалы", "Меняются фактическое время и порядок занятий."),
        ("classes", "Классы шаблона", "Меняется набор классов, входящих в шаблон."),
        ("day_modes", "Режимы дней по классам", "Меняется обычный, онлайн или СДЭП режим для затронутых классов и учеников."),
        ("grade_modes", "Режимы дней по параллелям", "Меняется обычный, онлайн или СДЭП режим для затронутых учеников."),
    ):
        if left[key] == right[key]:
            continue
        if key in {"day_modes", "grade_modes"}:
            old_text = _human_modes(database, left[key], grades=key == "grade_modes")
            new_text = _human_modes(database, right[key], grades=key == "grade_modes")
        elif key == "slots":
            old_text = "; ".join(f"{index + 1}: {slot['start']}–{slot['end']}" for index, slot in enumerate(left[key])) or "Нет интервалов"
            new_text = "; ".join(f"{index + 1}: {slot['start']}–{slot['end']}" for index, slot in enumerate(right[key])) or "Нет интервалов"
        elif key == "days":
            old_text = ", ".join(_WEEKDAYS[day] for day in left[key]) or "Нет учебных дней"
            new_text = ", ".join(_WEEKDAYS[day] for day in right[key]) or "Нет учебных дней"
        else:
            old_text = ", ".join(_display_names(database, "groups", left[key])) or "Нет классов"
            new_text = ", ".join(_display_names(database, "groups", right[key])) or "Нет классов"
        changes.append({"field": key, "label": label, "impact": impact,
                        "before": old_text, "after": new_text})
    return changes


def _selected_snapshot(database: Database, selection: Any, active: dict[str, Any]) -> dict[str, Any] | None:
    active_payload = active["payload"]
    active_hash = _digest(active_payload)
    if active_hash == str(selection["source_payload_sha256"]):
        return {"id": str(selection["draft_id"]), "revision": int(selection["source_revision"]),
                "name": active["name"], "payload": active_payload, "payload_hash": active_hash}
    with database.connection() as connection:
        row = database.execute(connection, """SELECT next_payload,next_revision,next_payload_sha256
            FROM school_template_selection_history
            WHERE next_draft_id=? AND next_payload_sha256=?
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(selection["draft_id"]), str(selection["source_payload_sha256"]))).fetchone()
    if row:
        payload = row["next_payload"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        return {"id": str(selection["draft_id"]), "revision": int(selection["source_revision"]),
                "name": active["name"], "payload": payload,
                "payload_hash": str(selection["source_payload_sha256"])}
    # Template publication also advances the selection pointer, without
    # changing the editor payload or the canonical publication protocol. Its
    # immutable source archive is a second valid place to recover that exact
    # selected payload when a later edit creates semantic drift.
    try:
        from backend.services.constructor_publication import get_published_source
        with database.connection() as connection:
            rows = database.execute(connection, """SELECT id FROM schedule_constructor_publications
                WHERE draft_id=? ORDER BY created_at DESC,id DESC""", (str(selection["draft_id"]),)).fetchall()
        for publication in rows:
            source = get_published_source(database, str(publication["id"])).get("published_source")
            if source and source.get("payload_fingerprint") == str(selection["source_payload_sha256"]):
                return {"id": str(selection["draft_id"]), "revision": int(selection["source_revision"]),
                        "name": active["name"], "payload": source["payload"],
                        "payload_hash": str(selection["source_payload_sha256"])}
    except Exception:
        pass
    return None


def _conflicts(lessons: list[dict[str, Any]], payload: dict[str, Any],
               names: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    def minutes(value: str) -> int | None:
        try:
            hour, minute = map(int, value.split(":")[:2])
            return hour * 60 + minute
        except (TypeError, ValueError):
            return None
    issues = []
    rows = [(item, _lesson_semantics(item, payload)) for item in lessons]
    for index, (first, a) in enumerate(rows):
        if a["day"] is None or first.get("informational") or first.get("specialMark") in {"lunch", "no_lesson"}:
            continue
        start_a, end_a = minutes(a["start"]), minutes(a["end"])
        if start_a is None or end_a is None or end_a <= start_a:
            continue
        for second, b in rows[index + 1:]:
            if (b["day"] is None or a["day"] != b["day"] or second.get("informational")
                    or second.get("specialMark") in {"lunch", "no_lesson"}):
                continue
            start_b, end_b = minutes(b["start"]), minutes(b["end"])
            if start_b is None or end_b is None or end_b <= start_b or max(start_a, start_b) >= min(end_a, end_b):
                continue
            shared_groups = sorted(set(a["groups"]) & set(b["groups"]))
            shared_teachers = sorted(set(a["teachers"]) & set(b["teachers"]))
            if shared_groups or shared_teachers:
                issues.append({"lesson_id": str(first.get("id") or ""), "details": [
                    *( ["Конфликт группы: " + ", ".join(names.get("groups", {}).get(value, value) for value in shared_groups)] if shared_groups else []),
                    *( ["Конфликт преподавателя: " + ", ".join(names.get("identities", {}).get(value, value) for value in shared_teachers)] if shared_teachers else []),
                ]})
    return issues


def preview_school_template_selection(database: Database, draft_id: str) -> dict[str, Any]:
    candidate = get_experiment(database, draft_id)
    payload = candidate["payload"]
    if payload.get("draft_kind") != "school" or payload.get("week_start") or payload.get("source_template"):
        raise ValueError("Выбрать основным можно только сохранённый шаблон школы")
    try:
        active = open_active_school_template(database)
    except ValueError as error:
        if "не назначен" not in str(error):
            raise
        active = None
    selection = None
    if active:
        with database.connection() as connection:
            selection = database.execute(connection, "SELECT * FROM school_template_selection WHERE scope='school'").fetchone()
    baseline = _selected_snapshot(database, selection, active) if selection and active else None
    baseline_available = baseline is not None if active else True
    baseline_recovery_required = bool(active and selection and not baseline_available)
    baseline_payload = baseline["payload"] if baseline else (active or {}).get("payload", {})
    before = {str(item.get("id")): item for item in baseline_payload.get("lessons", []) if isinstance(item, dict)}
    after = {str(item.get("id")): item for item in payload.get("lessons", []) if isinstance(item, dict)}
    names = _name_maps(database, baseline_payload, payload)
    added = [after[key] for key in sorted(after.keys() - before.keys())]
    removed = [before[key] for key in sorted(before.keys() - after.keys())]
    changed = [{"before": before[key], "after": after[key]} for key in sorted(before.keys() & after.keys())
               if _lesson_semantics(before[key], baseline_payload) != _lesson_semantics(after[key], payload)]
    lesson_changes = [{"id": str(item["after"].get("id") or ""),
        "before": _lesson_summary(database, item["before"], baseline_payload, names),
        "after": _lesson_summary(database, item["after"], payload, names),
        "changes": _lesson_changes(item["before"], item["after"], baseline_payload, payload, database, names)}
        for item in changed]
    problems = [{"lesson_id": str(item.get("id") or ""), "details": _lesson_summary(database, item, payload, names)["problems"]}
                for item in after.values() if _lesson_summary(database, item, payload, names)["problems"]]
    problems.extend(_conflicts(list(after.values()), payload, names))
    if active and not baseline_available:
        problems.append({"lesson_id": "selected-template", "details": [
            "Историческая ревизия недоступна. Сначала администратор должен отдельно утвердить текущее состояние как новую базовую версию."]})
    settings_changes = _payload_changes(database, baseline_payload, payload) if baseline_available else []
    if baseline_recovery_required:
        # There is no truthful diff against the missing historical revision.
        # Keep the candidate and its problems visible, and show the present
        # editor contents in a separate recovery inventory instead.
        added, removed, changed, lesson_changes, settings_changes = [], [], [], [], []
    recovery_inventory = []
    recovery_problems = []
    if baseline_recovery_required and active:
        active_payload = active.get("payload") or {}
        active_lessons = [item for item in active_payload.get("lessons", []) if isinstance(item, dict)]
        recovery_problems = [{"lesson_id": str(item.get("id") or ""), "details": summary["problems"]} for item in active_lessons if (summary := _lesson_summary(database, item, active_payload, names))["problems"]]
        recovery_problems.extend(_conflicts(active_lessons, active_payload, names))
        recovery_inventory = [{"id": str(item.get("id") or ""), **_lesson_summary(database, item, active_payload, names)}
                              for item in active_payload.get("lessons", []) if isinstance(item, dict)]
    return {"current": {"id": active["id"], "name": active["name"],
                         "revision": int(selection["source_revision"]) if selection else active["revision"],
                         "draft_revision": active["revision"],
                         "has_unreviewed_changes": bool(active.get("selection_stale")),
                         "snapshot_available": baseline_available} if active else None,
            "candidate": {"id": candidate["id"], "name": candidate["name"], "revision": candidate["revision"]},
            "current_hash": baseline["payload_hash"] if baseline else None,
            "candidate_hash": _digest(payload),
            "added": [{"id": str(item.get("id") or ""), **_lesson_summary(database, item, payload, names)} for item in added],
            "removed": [{"id": str(item.get("id") or ""), **_lesson_summary(database, item, baseline_payload, names)} for item in removed],
            "changed": lesson_changes, "settings_changes": settings_changes,
            "requires_explicit_problem_acknowledgement": bool(problems),
            "problems": problems,
            "has_semantic_changes": bool(added or removed or changed or settings_changes),
            "baseline_recovery_required": baseline_recovery_required,
            "baseline_recovery": ({"draft_id": active["id"], "revision": int(active["revision"]),
                "payload_hash": _digest(active["payload"]),
                "missing_revision": int(selection["source_revision"]),
                "missing_payload_hash": str(selection["source_payload_sha256"]),
                "contents": recovery_inventory, "problems": recovery_problems} if baseline_recovery_required and active and selection else None)}


def select_school_template(database: Database, draft_id: str, *, expected_revision: int,
                           expected_current_id: str | None, expected_current_revision: int | None,
                           expected_candidate_hash: str, actor_user_id: Any = None) -> dict[str, Any]:
    with _selection_lock:
        if database.database_url:
            with database.connection() as connection:
                database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                                 ("my-island:school-template-selection",))
                return _select_school_template_locked(database, draft_id,
                    expected_revision=expected_revision, expected_current_id=expected_current_id,
                    expected_current_revision=expected_current_revision,
                    expected_candidate_hash=expected_candidate_hash, actor_user_id=actor_user_id)
        return _select_school_template_locked(database, draft_id, expected_revision=expected_revision,
            expected_current_id=expected_current_id, expected_current_revision=expected_current_revision,
            expected_candidate_hash=expected_candidate_hash, actor_user_id=actor_user_id)


def _select_school_template_locked(database: Database, draft_id: str, *, expected_revision: int,
                           expected_current_id: str | None, expected_current_revision: int | None,
                           expected_candidate_hash: str, actor_user_id: Any = None) -> dict[str, Any]:
    preview = preview_school_template_selection(database, draft_id)
    candidate = get_experiment(database, draft_id)
    if int(candidate["revision"]) != expected_revision or preview["candidate_hash"] != expected_candidate_hash:
        raise ValueError("Шаблон изменился после сравнения. Обновите различия перед переключением.")
    if preview["current"] and (preview["current"]["id"] != expected_current_id or int(preview["current"]["revision"]) != int(expected_current_revision or 0)):
        raise ValueError("Основной шаблон уже изменился. Обновите сравнение и повторите подтверждение.")
    now = datetime.now(timezone.utc).isoformat()
    history_id = str(uuid4())
    candidate_payload = candidate["payload"]
    previous = None
    json_parameter = "?::jsonb" if database.database_url else "?"
    with database.connection() as connection:
        locked = database.execute(connection,
            "SELECT * FROM school_template_selection WHERE scope='school'" + (" FOR UPDATE" if database.database_url else "")).fetchone()
        locked_id = str(locked["draft_id"]) if locked else None
        if locked_id != expected_current_id:
            raise ValueError("Основной шаблон уже изменился. Обновите сравнение и повторите подтверждение.")
        if locked and (int(locked["source_revision"]) != int(expected_current_revision or 0)
                       or (preview["current_hash"] is not None
                           and str(locked["source_payload_sha256"]) != str(preview["current_hash"]))):
            raise ValueError("Снимок основного шаблона изменился. Обновите сравнение и повторите подтверждение.")
        suffix = " FOR UPDATE" if database.database_url else ""
        locked_candidate_row = database.execute(connection,
            "SELECT * FROM schedule_experiment_drafts WHERE id=?" + suffix, (draft_id,)).fetchone()
        if not locked_candidate_row:
            raise ValueError("Сохранённый шаблон больше не существует")
        locked_candidate = _item(locked_candidate_row)
        if (int(locked_candidate["revision"]) != expected_revision
                or _digest(locked_candidate["payload"]) != expected_candidate_hash):
            raise ValueError("Шаблон изменился после сравнения. Обновите различия перед переключением.")
        if expected_current_id:
            previous_row = database.execute(connection,
                "SELECT * FROM schedule_experiment_drafts WHERE id=?" + suffix, (expected_current_id,)).fetchone()
            current_draft = _item(previous_row) if previous_row else None
            previous = _selected_snapshot(database, locked, current_draft) if current_draft and locked else None
            if not previous:
                raise ValueError("Снимок выбранной версии не найден; безопасное переключение остановлено.")
        history_sql = ("INSERT INTO school_template_selection_history "
            "(id,previous_draft_id,previous_revision,previous_payload,previous_payload_sha256,next_draft_id,next_revision,next_payload,next_payload_sha256,actor_user_id,created_at) "
            f"VALUES (?,?,?,{json_parameter},?,?,{json_parameter},?,?,?,?)")
        database.execute(connection, history_sql, (history_id, previous["id"] if previous else None,
            previous["revision"] if previous else None, _encoded(previous["payload"]) if previous else None,
            _digest(previous["payload"]) if previous else None, candidate["id"], candidate["revision"],
            _encoded(candidate_payload), preview["candidate_hash"], str(actor_user_id) if actor_user_id is not None else None, now))
        database.execute(connection, f"""INSERT INTO school_template_selection
            (scope,draft_id,source_draft_id,source_revision,source_payload_sha256,activated_at)
            VALUES ('school',?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
            draft_id=excluded.draft_id,source_draft_id=excluded.source_draft_id,
            source_revision=excluded.source_revision,source_payload_sha256=excluded.source_payload_sha256,
            activated_at=excluded.activated_at""", (candidate["id"], candidate["id"], candidate["revision"], preview["candidate_hash"], now))
    return {"selected": {"id": candidate["id"], "revision": candidate["revision"]}, "history_id": history_id}


def approve_legacy_template_baseline(database: Database, draft_id: str, *, expected_revision: int,
                                    expected_payload_hash: str, expected_missing_revision: int,
                                    expected_missing_payload_hash: str, actor_user_id: Any = None, acknowledge_problems: bool = False) -> dict[str, Any]:
    """Explicitly establish the current editor state as baseline when legacy history is gone."""
    with _selection_lock:
        preview = preview_school_template_selection(database, draft_id)
        recovery = preview.get("baseline_recovery") or {}
        if not preview.get("baseline_recovery_required") or recovery.get("draft_id") != draft_id:
            raise ValueError("Исторический снимок уже доступен или выбранный шаблон изменился. Обновите проверку.")
        if int(recovery.get("revision") or 0) != expected_revision or recovery.get("payload_hash") != expected_payload_hash:
            raise ValueError("Текущий шаблон изменился после проверки. Обновите данные.")
        if int(recovery.get("missing_revision") or 0) != expected_missing_revision or recovery.get("missing_payload_hash") != expected_missing_payload_hash:
            raise ValueError("Указатель исторической ревизии изменился. Обновите данные.")
        if recovery.get("problems") and not acknowledge_problems:
            raise ValueError("Подтвердите проблемы и конфликты текущего шаблона.")
        now = datetime.now(timezone.utc).isoformat()
        history_id = str(uuid4())
        json_parameter = "?::jsonb" if database.database_url else "?"
        with database.connection() as connection:
            if database.database_url:
                database.execute(connection, "SELECT pg_advisory_xact_lock(hashtextextended(?,0))",
                    ("my-island:school-template-selection",))
            selection = database.execute(connection,
                "SELECT * FROM school_template_selection WHERE scope='school'" + (" FOR UPDATE" if database.database_url else "")).fetchone()
            if not selection or str(selection["draft_id"]) != draft_id:
                raise ValueError("Основной шаблон уже изменился. Обновите сравнение и повторите подтверждение.")
            if (int(selection["source_revision"]) != expected_missing_revision
                    or str(selection["source_payload_sha256"]) != expected_missing_payload_hash):
                raise ValueError("Указатель исторической ревизии изменился. Обновите данные.")
            draft_row = database.execute(connection,
                "SELECT * FROM schedule_experiment_drafts WHERE id=?" + (" FOR UPDATE" if database.database_url else ""),
                (draft_id,)).fetchone()
            if not draft_row:
                raise ValueError("Выбранный шаблон больше не существует.")
            draft = _item(draft_row)
            if int(draft["revision"]) != expected_revision or _digest(draft["payload"]) != expected_payload_hash:
                raise ValueError("Текущий шаблон изменился после проверки. Обновите данные.")
            existing = database.execute(connection,
                "SELECT approved_payload_sha256 FROM school_template_baseline_approvals WHERE scope='school'" + (" FOR UPDATE" if database.database_url else "")).fetchone()
            if existing:
                raise ValueError("Новая базовая версия уже утверждена. Обновите сравнение.")
            database.execute(connection, f"""INSERT INTO school_template_baseline_approvals
                (scope,draft_id,approved_revision,approved_payload_sha256,missing_revision,missing_payload_sha256,provenance,actor_user_id,created_at)
                VALUES ('school',?,?,?,?,?,'legacy_editor_state_explicitly_approved_without_historical_snapshot',?,?)""",
                (draft_id, expected_revision, expected_payload_hash, expected_missing_revision,
                 expected_missing_payload_hash, str(actor_user_id) if actor_user_id is not None else None, now))
            history_sql = ("INSERT INTO school_template_selection_history "
                "(id,previous_draft_id,previous_revision,previous_payload,previous_payload_sha256,next_draft_id,next_revision,next_payload,next_payload_sha256,actor_user_id,created_at) "
                f"VALUES (?,?,?,NULL,?,?,?,{json_parameter},?,?,?)")
            database.execute(connection, history_sql, (history_id, draft_id, expected_missing_revision,
                expected_missing_payload_hash, draft_id, expected_revision, _encoded(draft["payload"]),
                expected_payload_hash, str(actor_user_id) if actor_user_id is not None else None, now))
            database.execute(connection, """UPDATE school_template_selection
                SET source_draft_id=?,source_revision=?,source_payload_sha256=?,activated_at=? WHERE scope='school'""",
                (draft_id, expected_revision, expected_payload_hash, now))
        return {"approved": True, "selected": {"id": draft_id, "revision": expected_revision},
            "missing_revision": expected_missing_revision, "history_id": history_id,
            "provenance": "legacy_editor_state_explicitly_approved_without_historical_snapshot"}


def rollback_school_template(database: Database, *, history_id: str, actor_user_id: Any = None) -> dict[str, Any]:
    with database.connection() as connection:
        row = database.execute(connection, "SELECT * FROM school_template_selection_history WHERE id=?", (history_id,)).fetchone()
    if not row or not row["previous_payload"]:
        raise ValueError("Для этой записи нет предыдущей версии шаблона")
    payload = row["previous_payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    if _digest(payload) != row["previous_payload_sha256"]:
        raise ValueError("Снимок предыдущего шаблона повреждён; возврат заблокирован")
    restored = create_experiment(database, name=f"Восстановленный шаблон · {row['previous_draft_id']}",
        source="import", payload=payload, actor_user_id=actor_user_id)
    try:
        current = open_active_school_template(database)
    except ValueError:
        current = None
    result = select_school_template(database, restored["id"], expected_revision=restored["revision"],
        expected_current_id=current["id"] if current else None,
        expected_current_revision=current["selected_revision"] if current else None,
        expected_candidate_hash=_digest(restored["payload"]),
        actor_user_id=actor_user_id)
    return {"draft": restored, **result}


def open_active_school_template(database: Database) -> dict[str, Any]:
    # One statement observes the pointer and its draft in the same DB snapshot.
    # Opening never imports, normalizes, creates, or rewrites a template.
    with database.connection() as connection:
        row = database.execute(connection, """SELECT d.*,s.source_revision AS selected_revision,
                s.source_payload_sha256 AS selected_payload_sha256
            FROM school_template_selection s
            JOIN schedule_experiment_drafts d ON d.id=s.draft_id
            WHERE s.scope='school'""").fetchone()
    if not row:
        raise ValueError("Основной школьный шаблон не назначен. Нужен проверенный редакторский черновик; canonical-копия автоматически не создаётся.")
    draft = _item(row)
    if (draft["payload"].get("draft_kind") != "school"
            or draft["payload"].get("week_start") or draft["payload"].get("source_template")):
        raise ValueError("Активный школьный шаблон указывает на неподходящий черновик")
    selection = {"draft_id": draft["id"], "source_revision": draft["selected_revision"],
                 "source_payload_sha256": draft["selected_payload_sha256"]}
    snapshot = _selected_snapshot(database, selection, draft)
    draft["selection_snapshot_available"] = snapshot is not None
    draft["selection_stale"] = snapshot is None or _payload_semantics(snapshot["payload"]) != _payload_semantics(draft["payload"])
    draft["selected_revision"] = int(draft["selected_revision"])
    draft["already_exists"] = True
    return draft
