"""Create synthetic problem navigation scenarios in the loopback fixture DB."""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("ux_local_harness", Path(__file__).with_name("local_harness.py"))
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

from backend.database import Database
from backend.services.schedule_experiments import create_experiment, get_experiment
from backend.services.schedule_term_comparison import term_comparison_lesson_fingerprint

SOURCE = "synthetic-issue-navigation"
NAME = "II триместр 2026/27 · рабочая копия"
SLOTS = [
    ("09:00", "09:45"), ("09:55", "10:40"), ("10:55", "11:40"),
    ("11:50", "12:35"), ("12:45", "13:30"), ("13:40", "14:25"),
    ("14:35", "15:20"), ("15:30", "16:15"), ("16:20", "17:05"),
]
SUBJECTS = ["Математика", "Русский язык", "История", "Биология", "Физика", "Литература", "География", "Информатика", "Классный час"]


def seed_issue_navigation(database: Database, fixture: dict) -> dict:
    class_ids, people = {}, {}
    teacher_ids = fixture["teachers"]
    teacher_names = {tid: f"Synthetic Teacher {i:02d}" for i, tid in enumerate(teacher_ids, 1)}
    for label in ("5-Т", "6-Т", "7-А", "7-Б", "8-Т", "9-Ан", "9-Д", "10-Т", "11-Т", "11"):
        class_ids[label] = str(database.create_group(
            f"Fixture UX class {label}", "class",
            display_name="11 · базовый класс" if label == "11" else label, base_class_name=label,
        )["id"])
        people[label] = []
        for index in range(5):
            student = database.create_identity("student", f"Тестовый ученик {label} {index + 1}", label)
            person = str(student["id"])
            people[label].append(person)
            database.create_membership(class_ids[label], person, "student", SOURCE, f"{label}:{index}")

    groups = {}
    roster = defaultdict(list)

    def group(label, subject, students, teacher, excluded=()):
        group_id = str(database.create_group(
            f"Fixture UX group {label}", "instructional", display_name=label, subject=subject,
        )["id"])
        groups[label] = group_id
        roster[group_id] = [x for x in students if x not in excluded]
        for index, student_id in enumerate(students):
            database.create_membership(group_id, student_id, "student", SOURCE, f"{label}:{index}")
        with database.connection() as conn:
            conn.execute("""INSERT INTO teacher_assignments
                (teacher_identity_id,group_id,subject,active,source,source_ref) VALUES (?,?,?,1,?,?)""",
                (teacher, group_id, subject, SOURCE, group_id))
            for learner in excluded:
                conn.execute("""INSERT INTO membership_overrides
                    (identity_id,group_id,action,member_role,active) VALUES (?,?,'exclude','student',1)""",
                    (learner, group_id))
        return group_id

    eng = group("Английский · группа 10", "Английский ЕГЭ", [*people["10-Т"][:2], *people["11-Т"][:2]], teacher_ids[0])
    phys_11 = group("Физика ЕГЭ · 11", "Физика ЕГЭ", people["11-Т"][:3], teacher_ids[1])
    phys_shared = group("Физика ЕГЭ · 10–11", "Физика ЕГЭ", [people["10-Т"][2], people["11-Т"][3]], teacher_ids[2])
    history = group("История ЕГЭ · 10–11", "История ЕГЭ", [people["10-Т"][3], people["11-Т"][3]], teacher_ids[3])
    literature = group("Литература ОГЭ · 9", "Литература ОГЭ", [*people["9-Ан"][:4], people["9-Д"][0]], teacher_ids[4],
                       excluded=(people["9-Ан"][3], people["9-Д"][0]))

    def lesson(key, day, period, subject, teacher, room, labels, *, group_id=None):
        ids = [class_ids[label] for label in labels]
        rule = {"kind": "groups", "group_ids": [group_id]} if group_id else {"kind": "classes", "class_ids": ids}
        group_ids = [group_id] if group_id else ids
        confirmed = roster[group_id] if group_id else [learner for label in labels for learner in people[label]]
        return {
            "id": f"ux-{key.lower()}", "day": day, "period": period, "start": SLOTS[period][0], "end": SLOTS[period][1],
            "classId": ids[0], "classIds": ids, "groupId": group_ids[0], "groupIds": group_ids,
            "subject": subject, "teacherId": teacher, "teacherIds": [teacher], "room": room, "audience": rule,
            "audience_snapshot": {"student_ids": confirmed},
        }

    english_lunch = group("Английский · смешанная группа", "Английский",
                          [*people["7-А"], *people["7-Б"], *people["8-Т"]], teacher_ids[0])
    social = group("Обществознание · 11 класс · База", "Обществознание", people["11"][:3], teacher_ids[5])
    database.set_teacher_assignment(teacher_ids[6], class_ids["11"], "Русский язык", True, None)
    cases = {
        "G8": lesson("G8", 0, 4, "Английский", teacher_ids[0], "8", ["7-А", "7-Б", "8-Т"], group_id=english_lunch),
        "Q53": lesson("Q53", 4, 1, "Английский ЕГЭ", teacher_ids[0], "18", ["10-Т", "11-Т"], group_id=eng),
        "T53": lesson("T53", 4, 1, "Физика ЕГЭ", teacher_ids[1], "16", ["11-Т"], group_id=phys_11),
        "S11": lesson("S11", 0, 7, "Физика ЕГЭ", teacher_ids[2], "17", ["10-Т", "11-Т"], group_id=phys_shared),
        "U11": lesson("U11", 0, 7, "История ЕГЭ", teacher_ids[3], "15", ["10-Т", "11-Т"], group_id=history),
        "M43": lesson("M43", 3, 3, "Литература ОГЭ", teacher_ids[4], "12", ["9-Ан"], group_id=literature),
        "ROOM-A": lesson("ROOM-A", 1, 8, "География · кабинет", teacher_ids[5], "309", ["5-Т"]),
        "ROOM-B": lesson("ROOM-B", 1, 8, "Информатика · кабинет", teacher_ids[6], "309", ["11-Т"]),
        "GROUP": lesson("GROUP", 2, 5, "Практикум без группы", teacher_ids[7], "14", ["8-Т"]),
        "MOVE": lesson("MOVE", 1, 6, "Творческая мастерская", teacher_ids[8], "19", ["9-Д"]),
    }
    cases["GROUP"].update(groupId="", groupIds=[], audience={"kind": "", "class_ids": [], "group_ids": []},
                          needsClarification=True, clarificationReason="Нужно подтвердить учебную группу практикума.")
    cases["GROUP"].pop("audience_snapshot")
    occupied_slots = {(l["day"], l["period"]) for l in cases.values()}
    fillers = [
        lesson(f"filler-{day}-{period}-{column}", day, period, SUBJECTS[period], teacher_ids[column + 10],
               str(101 + column), [name])
        for day in range(5) for period in range(len(SLOTS)) if (day, period) not in occupied_slots
        for column, name in enumerate(class_ids) if name != "11" and (day, period, name) not in {(3, 8, "9-Д"), (2, 8, "9-Д")}
    ]
    cases["S7"] = lesson("S7", 0, 3, "Обществознание", teacher_ids[5], "16", ["11"], group_id=social)
    lessons = [*fillers, *cases.values()]
    labels_by_class = {value: label for label, value in class_ids.items()}
    labels_by_class[class_ids["11"]] = "11 · базовый класс"

    def side(value):
        return {
            "day": value["day"], "start": value["start"], "end": value["end"],
            "classes": [labels_by_class[g] for g in value["classIds"]], "subject": value["subject"],
            "room": value["room"], "teachers": [teacher_names[t] for t in value["teacherIds"]],
            "groups": [label for label, gid in groups.items() if gid in value["groupIds"]],
            "kind": value.get("specialMark", "lesson"), "warning": value.get("clarificationReason", ""),
        }

    def item(cell, before, after, reason, kind, linked=None, related=(), roster_evidence=None, mode=None):
        assessment={"category": "admin", "reason": reason, "type": kind, "priority": 1,
                    "options": ["Уточнить факт у ответственного.", "Сохранить текущую запись до сверки."]}
        if related:
            assessment["related_keys"] = [f"cells:{r}" for r in related]
        if roster_evidence:
            assessment["roster_evidence"] = roster_evidence
        sheet_text = f"{cell} · {after['subject']}" if after else f"{cell} · пусто"
        result = {"key": f"cells:{cell}", "cells": [cell], "kind": mode or ("changed" if linked else "added"),
                  "note": reason, "before": side(before) if before else None, "after": side(after) if after else None,
                  "sheet_text": sheet_text, "initial_status": "pending", "assessment": assessment}
        if linked:
            result["lesson_id"] = linked["id"]
        return result

    roster_evidence = {
        "sources": [{"gid": "728827779", "range": "N48:N55", "label": "Исторический список литературы ОГЭ"}],
        "historical": "Пять участников в историческом списке.", "current": "Трое действующих участников из 9-Ан.",
        "overrides": "Два ручных исключения.", "reliability": "Подтверждены два исключения и текущий состав.",
        "outcome": "clarified", "reason": "Колонка 9-Д сама по себе не подтверждает участие.",
    }
    lunch_proposal = {**cases["G8"], "subject": "Обед", "specialMark": "lunch", "teacherIds": [], "groupIds": [],
                      "room": "", "classIds": [class_ids["7-Б"]],
                      "clarificationReason": "Уточните, относится ли обед ко всему классу."}
    items = [
        item("S7", cases["S7"], {**cases["S7"], "subject": "Русский язык", "room": "15",
                                "teacherIds": [teacher_ids[6]], "groupIds": []},
             "Русский язык для всего класса подтверждён в справочнике.", "Группа и классы", cases["S7"]),
        item("G8", cases["G8"], lunch_proposal, "Обед для 7-Б вместо части общего занятия.",
             "Связь исходных ячеек", cases["G8"]),
        item("J8", cases["G8"], {**lunch_proposal, "classIds": [class_ids["8-Т"]]},
             "Соседнее предложение из другой ячейки.", "Связь исходных ячеек", cases["G8"]),
        item("Q53", cases["Q53"], cases["Q53"], "Двое учеников попадают на английский и физику одновременно.",
             "Пересечение составов", cases["Q53"], ("T53",)),
        item("T53", cases["T53"], cases["T53"], "Физика пересекается с тем же практикумом английского.",
             "Пересечение составов", cases["T53"], ("Q53",)),
        item("S11", cases["S11"], cases["S11"], "Один участник физики одновременно назначен на историю.",
             "Пересечение составов", cases["S11"], ("U11",)),
        item("U11", cases["U11"], cases["U11"], "История и физика назначены в один интервал.",
             "Пересечение составов", cases["U11"], ("S11",)),
        item("M43", cases["M43"], {**cases["M43"], "classIds": [class_ids["9-Д"]]},
             "В классе 9-Д отсутствуют участники действующей группы литературы ОГЭ.",
             "Состав учебной группы", cases["M43"], roster_evidence=roster_evidence),
        item("H45", cases["ROOM-B"], cases["ROOM-B"], "Кабинет 309 занят двумя занятиями одновременно.",
             "Кабинет", cases["ROOM-B"]),
        item("I39", cases["GROUP"], cases["GROUP"], "В копии требуется выбрать учебную группу практикума.",
             "Не подтверждена группа", cases["GROUP"]),
        item("M23", cases["MOVE"], {**cases["MOVE"], "day": 3, "period": 8, "start": SLOTS[8][0], "end": SLOTS[8][1]},
             "Творческую мастерскую предлагают перенести на четверг 16:20–17:05.",
             "Перенос занятия", cases["MOVE"]),
        item("V48", None, {**cases["MOVE"], "day": 2, "period": 8, "start": SLOTS[8][0], "end": SLOTS[8][1],
                            "subject": "Лаборатория без карточки"},
             "В таблице есть лаборатория, для которой ещё нет карточки.", "Новое занятие"),
        item("W48", {**cases["MOVE"], "day": 4, "period": 8, "start": SLOTS[8][0], "end": SLOTS[8][1],
                      "subject": "Архивное занятие"}, None,
             "Требуется уточнить судьбу старого занятия.", "Удаление", mode="removed"),
    ]
    payload = {
        "draft_kind": "school", "days": list(range(5)), "slots": [{"start": a, "end": b} for a, b in SLOTS],
        "classes": list(class_ids.values()), "lessons": lessons,
        "school_source": {"term2_preparation": {"first_week": "2026-11-23", "comparison": {
            "items": items, "decisions": [],
            "baseline_lessons": {l["id"]: term_comparison_lesson_fingerprint(l) for l in lessons},
        }}},
    }
    created = create_experiment(database, name=NAME, source="import", payload=payload,
                                _trusted_resolution_history=True, actor_user_id=fixture["admin_user_id"])
    with database.connection() as conn:
        conn.execute("UPDATE schedule_experiment_drafts SET revision=3 WHERE id=?", (created["id"],))
        active = conn.execute("SELECT draft_id FROM school_template_selection WHERE scope='school'").fetchone()
    saved = get_experiment(database, created["id"])
    assert saved["revision"] == 3 and len(saved["payload"]["lessons"]) == len(lessons)
    assert len(saved["payload"]["school_source"]["term2_preparation"]["comparison"]["items"]) == len(items)
    assert len(set(roster[eng]) & set(roster[phys_11])) == 2
    assert len(set(roster[phys_shared]) & set(roster[history])) == 1
    assert len(roster[literature]) == 3 and not (set(people["9-Д"]) & set(roster[literature]))
    return {"id": created["id"], "name": NAME, "revision": 3, "primary_id": active["draft_id"],
            "classes": class_ids, "cases": {key: value["id"] for key, value in cases.items()}}


if __name__ == "__main__":
    fixture = json.loads(harness.FIXTURE_PATH.read_text())
    auth = json.loads(harness.AUTH_PATH.read_text())
    assert fixture["synthetic"] and auth["synthetic"] and Path(auth["database"]) == harness.DB_PATH
    if "issue_navigation" not in fixture:
        fixture["issue_navigation"] = seed_issue_navigation(Database(path=harness.DB_PATH), fixture)
        harness.FIXTURE_PATH.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
    print("Synthetic issue navigation fixture ready, including comparison positions and two pupil overlap sets.")
