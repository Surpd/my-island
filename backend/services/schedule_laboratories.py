"""Week-local laboratory substitutions, committed against the previewed draft revision."""

from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from hashlib import sha256
import json

from backend.services.schedule_experiments import (
    ExperimentConflict,
    _audience_fingerprint,
    _class_grade_map,
    _lesson_interval,
    physical_room_key,
    _lesson_day_modes,
    _write_experiment_revision,
    experiment_week_audience_preview,
    get_experiment,
)


def _teachers(lesson):
    return set(lesson.get("teacherIds") or []) | (
        {lesson["teacherId"]} if lesson.get("teacherId") else set()
    )


def restore_lessons(lessons, receipt):
    affected = {row["lesson"]["id"]: row for row in receipt["originals"]}
    restored = [
        lesson
        for lesson in lessons
        if lesson["id"] not in affected and lesson["id"] != receipt["meeting_id"]
    ]
    for original in sorted(affected.values(), key=lambda row: row["index"]):
        restored.insert(original["index"], original["lesson"])
    return restored


def laboratory_action(
    store,
    directory,
    draft_id,
    revision,
    blueprint_id,
    overrides,
    *,
    token=None,
    deactivate=False,
    actor=None,
):
    draft = get_experiment(store, draft_id)
    if draft["revision"] != revision:
        raise ExperimentConflict(draft)
    payload = deepcopy(draft["payload"])
    if payload.get("draft_archive"):
        raise ValueError("Восстановите недельный черновик из архива.")
    if not payload.get("week_start"):
        raise ValueError("Выберите недельный черновик")
    receipts = payload.setdefault("laboratory_meetings", {})
    receipt = receipts.get(blueprint_id)
    lessons = payload["lessons"]
    if deactivate:
        if not receipt:
            raise ValueError("Лаборатория уже отключена")
        payload["lessons"] = restore_lessons(lessons, receipt)
        del receipts[blueprint_id]
        changes = receipt["changes"]
    else:
        if receipt:
            raise ValueError("Для изменения встречи сначала отключите её")
        blueprint = next(
            (
                b
                for b in payload.get("laboratory_blueprints", [])
                if b["id"] == blueprint_id
            ),
            None,
        )
        deleted = any((edit.get("before") or {}).get("id") == blueprint_id and not edit.get("after")
                      for edit in payload.get("laboratory_blueprint_history", []))
        if not blueprint and not deleted and blueprint_id in {"school-lab-0", "school-lab-1", "school-lab-2"}:
            directions = ["Текст и смысл", "Прошлое и настоящее", "Мир вокруг нас"]
            blueprint = {"subject": directions[int(blueprint_id[-1])]}
        if not blueprint:
            raise ValueError("Заготовка не найдена")
        allowed = {
            "day",
            "period",
            "start",
            "end",
            "subject",
            "classId",
            "classIds",
            "audience",
            "teacherId",
            "teacherIds",
            "room",
            "format",
            "description",
        }
        meeting = {k: v for k, v in {**blueprint, **overrides}.items() if k in allowed}
        meeting.update(
            id=f"lab-{blueprint_id}",
            activityType="laboratory",
            laboratoryOrigin=blueprint_id,
        )
        interval = _lesson_interval(meeting, payload["slots"])
        if (
            not meeting.get("subject", "").strip()
            or not meeting.get("teacherId")
            or not interval
            or meeting.get("day") not in payload["days"]
            or any(l["id"] == meeting["id"] for l in lessons)
        ):
            raise ValueError("Проверьте тему, ведущего, день и время лаборатории")
        class_ids = meeting.get("classIds", [])
        if (
            not isinstance(class_ids, list)
            or not class_ids
            or set(class_ids) - set(payload["classes"])
        ):
            raise ValueError("Укажите классы размещения лаборатории")
        if (meeting.get("audience") or {}).get("kind") not in {
            "groups",
            "classes",
            "students",
        }:
            raise ValueError("Укажите конкретный состав лаборатории")
        with directory.connection() as connection:
            staff = directory.execute(
                connection,
                "SELECT id FROM identities WHERE kind='teacher' AND status='active'",
            ).fetchall()
        if _teachers(meeting) - {str(row["id"]) for row in staff}:
            raise ValueError("Выберите действующих преподавателей")
        week = date.fromisoformat(payload["week_start"])

        def roster(row):
            if row.get("status") != "ok" or row.get("needs_recalculation"):
                raise ValueError(
                    "Подтвердите состав пересекающихся занятий перед замещением"
                )
            return set(row["student_ids"])

        before = experiment_week_audience_preview(directory, payload, week)["lessons"]
        lab_students = roster(
            experiment_week_audience_preview(
                directory, {**payload, "lessons": [meeting]}, week
            )["lessons"][meeting["id"]]
        )
        if not lab_students:
            raise ValueError("Выберите участников лаборатории")
        grades = _class_grade_map(directory, payload)
        modes = _lesson_day_modes(payload, meeting, grades)
        if (
            "sdep" in modes
            or len(modes) > 1
            and meeting.get("format") not in {"online", "offline"}
        ):
            raise ValueError("Проверьте режимы дня у выбранных классов лаборатории")
        changes, originals, conflicts, effective = [], [], [], []
        for index, lesson in enumerate(lessons):
            time = _lesson_interval(lesson, payload["slots"])
            if (
                lesson.get("day") != meeting["day"]
                or not time
                or time[0] >= interval[1]
                or interval[0] >= time[1]
            ):
                effective.append(lesson)
                continue
            optional = lesson.get("activityType") in {
                "consultation",
                "club",
                "elective",
                "event",
            } or lesson.get("specialMark")
            students = set() if optional else roster(before[lesson["id"]])
            attending = students & lab_students
            continuing = students - attending
            if attending:
                if lesson.get("protected"):
                    raise ValueError("Защищённое занятие требует отдельного решения")
                if any(
                    lesson["id"] in {r["lesson"]["id"] for r in active["originals"]}
                    for active in receipts.values()
                ):
                    raise ValueError("Этот урок уже замещён другой лабораторией")
                if (
                    lesson.get("activityType") == "laboratory"
                    or interval[0] > time[0]
                    or interval[1] < time[1]
                ):
                    raise ValueError(
                        "Лаборатория пересекает другую встречу или только часть урока. Уточните время."
                    )
                originals.append({"index": index, "lesson": lesson})
                changes.append(
                    {
                        "lesson_id": lesson["id"],
                        "subject": lesson["subject"],
                        "interval": time,
                        "attending": sorted(attending),
                        "continuing": sorted(continuing),
                        "teachers": sorted(_teachers(lesson)),
                        "room": lesson.get("room"),
                    }
                )
                if not continuing:
                    continue
                lesson = {
                    **lesson,
                    "audience": {"kind": "students", "student_ids": sorted(continuing)},
                    "audience_snapshot": None,
                }
            if _teachers(lesson) & _teachers(meeting):
                conflicts.append(
                    {
                        "lesson_id": lesson["id"],
                        "message": f"{lesson['subject']} — преподаватель нужен на обоих занятиях",
                    }
                )
            if physical_room_key(lesson, payload, grades) and physical_room_key(
                lesson, payload, grades
            ) == physical_room_key(meeting, payload, grades):
                conflicts.append(
                    {
                        "lesson_id": lesson["id"],
                        "message": f"{lesson['subject']} — занят кабинет {lesson['room']}",
                    }
                )
            effective.append(lesson)
        meeting["audience_snapshot"] = {
            "student_ids": sorted(lab_students),
            "fingerprint": _audience_fingerprint(meeting, payload),
            "resolved_at": week.isoformat(),
        }
        payload["lessons"] = [*effective, meeting]
        receipts[blueprint_id] = {
            "meeting_id": meeting["id"],
            "date": (week + timedelta(days=meeting["day"])).isoformat(),
            "originals": originals,
            "changes": changes,
        }
        if conflicts:
            if token is not None:
                raise ValueError(
                    "Замещение изменилось. Проверьте оставшиеся конфликты."
                )
            return {"changes": changes, "conflicts": conflicts}
    digest = sha256(
        json.dumps(
            [draft_id, revision, payload, deactivate],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if token is None:
        return {"changes": changes, "conflicts": [], "token": digest}
    if token != digest:
        raise ValueError("Предпросмотр устарел. Проверьте замещение ещё раз.")
    payload.setdefault("laboratory_history", []).append(
        {
            "blueprint_id": blueprint_id,
            "action": "disable" if deactivate else "enable",
            "actor": str(actor or ""),
            "at": datetime.now(timezone.utc).isoformat(),
            "changes": changes,
        }
    )
    return _write_experiment_revision(
        store, draft_id, revision, payload, actor_user_id=actor
    )
