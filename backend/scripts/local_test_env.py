"""Isolated local API profile for My Island development and E2E testing.

This module deliberately sets every integration variable before importing the
application. It never reads or copies values from the user's .env file.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
PROFILE = os.getenv("MY_ISLAND_TEST_PROFILE", "manual")
if PROFILE not in {"manual", "e2e", "schedule"}:
    raise ValueError("Unknown local test profile")
TEST_DIR = ROOT / ".local-test" / (PROFILE if PROFILE in {"e2e", "schedule"} else "")
DB_PATH = TEST_DIR / "my-island-test.sqlite3"
MARKER = TEST_DIR / ".my-island-local-test-db"
MARKER_CONTENT = "My Island local test database v1\n"


def configure_environment() -> None:
    api_port = int(os.getenv("MY_ISLAND_TEST_API_PORT", "8011"))
    frontend_port = int(os.getenv("MY_ISLAND_TEST_FRONTEND_PORT", "3011"))
    if not (1024 <= api_port <= 65535 and 1024 <= frontend_port <= 65535):
        raise RuntimeError("Local test ports must be unprivileged TCP ports")
    os.environ.update({
        "APP_ENV": "development",
        "DEV_AUTH_ENABLED": "true",
        "MY_ISLAND_LOCAL_TEST_MODE": "true",
        "DATABASE_URL": "",
        "DATABASE_PATH": str(DB_PATH),
        "CORS_ORIGINS": f"http://localhost:{frontend_port},http://127.0.0.1:{frontend_port}",
        "BACKEND_PUBLIC_URL": "",
        "FRONTEND_PUBLIC_URL": "",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_WEBHOOK_SECRET": "",
        "TELEGRAM_BOOTSTRAP_USER_IDS": "",
        "GOOGLE_SHEETS_SPREADSHEET_ID": "",
        "GOOGLE_CLASSROOM_COURSE_ID": "",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "GOOGLE_OAUTH_CLIENT_SECRET": "",
        "GOOGLE_OAUTH_REDIRECT_URI": "",
        "GOOGLE_OAUTH_REFRESH_TOKEN": "",
        "GOOGLE_OAUTH_GRANTED_SCOPES": "",
        "GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY": "",
        "GOOGLE_OAUTH_ALLOWED_EMAIL": "",
        "SCHEDULE_BACKEND": "canonical",
        "VITE_API_BASE_URL": f"http://localhost:{api_port}",
    })


def _check_directory() -> None:
    if TEST_DIR.resolve() != (ROOT / ".local-test" / (PROFILE if PROFILE in {"e2e", "schedule"} else "")).resolve():
        raise RuntimeError("Refusing to use an unexpected local test directory")
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    if MARKER.exists() and MARKER.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise RuntimeError("Local test DB marker is unexpected; refusing to modify this directory")
    if DB_PATH.exists() and not MARKER.exists():
        raise RuntimeError("A database already exists without the local-test marker; refusing to use or erase it")
    if not MARKER.exists():
        MARKER.write_text(MARKER_CONTENT, encoding="utf-8")


def reset_database() -> None:
    _check_directory()
    for path in (DB_PATH, Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm")):
        if path.exists():
            if path.parent.resolve() != TEST_DIR.resolve():
                raise RuntimeError("Refusing to remove a database file outside .local-test")
            path.unlink()
    seed_database()


def seed_database() -> None:
    configure_environment()
    _check_directory()
    from backend.database import Database

    database = Database(str(DB_PATH), None)
    database.initialize()
    synthetic_teachers: dict[str, tuple[str, str]] = {}
    with database.connection() as connection:
        seed_users = (
            (900002, "admin", "Локальный администратор", None),
            (900003, "teacher", "Тестовый преподаватель", None),
            (900001, "student", "Тестовый ученик", "7"),
        )
        identities: dict[str, int] = {}
        for telegram_id, role, display_name, class_name in seed_users:
            if role == "admin":
                connection.execute(
                    "INSERT INTO users(telegram_user_id, role) VALUES (?, ?) "
                    "ON CONFLICT(telegram_user_id) DO UPDATE SET role=excluded.role",
                    (telegram_id, role),
                )
                continue
            identity_ref = f"local-e2e:{role}"
            identity = connection.execute(
                "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (identity_ref,),
            ).fetchone()
            if not identity:
                connection.execute(
                    "INSERT INTO identities(kind,display_name,class_name,origin,source_ref) VALUES (?,?,?,?,?)",
                    (role, display_name, class_name, "local_e2e", identity_ref),
                )
                identity = connection.execute(
                    "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (identity_ref,),
                ).fetchone()
            identity_id = int(identity["id"])
            identities[role] = identity_id
            connection.execute(
                "INSERT INTO users(telegram_user_id,role,identity_id) VALUES (?,?,?) "
                "ON CONFLICT(telegram_user_id) DO UPDATE SET role=excluded.role, identity_id=excluded.identity_id",
                (telegram_id, role, identity_id),
            )
        for telegram_id, role, _, _ in seed_users:
            user = connection.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_id,)).fetchone()
            user_id = int(user["id"])
            connection.execute(
                "INSERT INTO user_roles(user_id,role,source) VALUES (?,?, 'local_e2e') "
                "ON CONFLICT(user_id,role) DO NOTHING", (user_id, role),
            )
            if role in identities:
                connection.execute(
                    "INSERT INTO account_identity_links(user_id,identity_id,status,source,source_ref,confirmed_by) "
                    "VALUES (?,?,'confirmed','local_e2e',?,?) ON CONFLICT(user_id) DO UPDATE SET "
                    "identity_id=excluded.identity_id,status='confirmed',source='local_e2e',source_ref=excluded.source_ref",
                    (user_id, identities[role], f"local-e2e:{role}", user_id),
                )

        connection.execute(
            "INSERT INTO groups(name,group_type,display_name,base_class_name,canonical) "
            "VALUES ('7','class','7 класс','7',1) ON CONFLICT(name,group_type) DO NOTHING"
        )
        group = connection.execute("SELECT id FROM groups WHERE name='7' AND group_type='class'").fetchone()
        student = connection.execute("SELECT id FROM users WHERE telegram_user_id=900001").fetchone()
        teacher = connection.execute("SELECT id FROM users WHERE telegram_user_id=900003").fetchone()
        group_id = int(group["id"])
        student_user_id = int(student["id"])
        teacher_user_id = int(teacher["id"])
        connection.execute(
            "INSERT INTO memberships(group_id,identity_id,member_role,source,source_ref,active,created_by) "
            "VALUES (?,?,'student','local_e2e','test-student',1,?) ON CONFLICT DO NOTHING",
            (group_id, identities["student"], student_user_id),
        )
        connection.execute(
            "INSERT INTO teacher_assignments(teacher_identity_id,group_id,subject,base_class_name,active,source,source_ref,created_by) "
            "VALUES (?,?,'Математика','7',1,'local_e2e','test-math',?) ON CONFLICT DO NOTHING",
            (identities["teacher"], group_id, teacher_user_id),
        )
        today = date.today().isoformat()
        connection.execute(
            "INSERT INTO schedule_entries(entry_key,lesson_date,start_time,end_time,subject,teacher,audience,lesson_type,parse_status,source_tab,week_start,source_hash,raw_source,audience_rule,resolved_audience,teacher_identity_ids) "
            "VALUES (?,?, '09:00','09:45','Математика','Тестовый преподаватель','7 класс','lesson','parsed','Local E2E',?,'local-e2e-fixture','Математика · Тестовый преподаватель · 7 класс','','',?) "
            "ON CONFLICT(entry_key) DO UPDATE SET lesson_date=excluded.lesson_date,week_start=excluded.week_start,teacher_identity_ids=excluded.teacher_identity_ids",
            (f"local-e2e:math-7:{today}", today, today, json.dumps([identities["teacher"]])),
        )

        # Canonical schedule used only by the isolated E2E SQLite profile.
        # These groups deliberately cover parallel partitions, a cross-grade
        # English cohort, and a group/remainder split.
        group_specs = [
            (str(grade), "class", f"{grade} класс", str(grade), "base_class", "")
            for grade in range(5, 12)
        ] + [
            (f"Math {grade}-A", "instructional", f"Математика A · {grade}", str(grade), "instructional_group", "math")
            for grade in range(5, 12)
        ] + [
            (f"Math {grade}-B", "instructional", f"Математика B · {grade}", str(grade), "instructional_group", "math")
            for grade in range(5, 12)
        ] + [
            ("Math 11 Interage", "instructional", "Математика 10–11 · общая группа", "10", "instructional_group", "math11_interage"),
            ("Math 9-C", "instructional", "Математика C · 9", "9", "instructional_group", "math"),
            ("Math 7-1", "instructional", "Математика 7-1", "7", "instructional_group", "math"),
            ("grade7:base:7-А", "class", "7-А · базовый класс", "7-А", "base_class", "grade7_base_class"),
            ("grade7:base:7-Б", "class", "7-Б · базовый класс", "7-Б", "base_class", "grade7_base_class"),
            ("grade7:split:1", "instructional", "Математика · 7.1", None, "instructional_partition", "grade7_shared_split"),
            ("grade7:split:2", "instructional", "Математика · 7.2", None, "instructional_partition", "grade7_shared_split"),
            ("9-Ан", "class", "9-Ан · базовый класс", "9-Ан", "base_class", "grade9_base_class"),
            ("9-Д", "class", "9-Д · базовый класс", "9-Д", "base_class", "grade9_base_class"),
            ("english:6", "instructional", "Английский · группа 6", None, "instructional_group", "english"),
            ("English 10-11", "instructional", "Английский · 10–11", "10", "instructional_group", "english"),
        ] + [
            (f"E2E English 5-6 {lane}", "instructional", f"E2E Английский 5–6 · {lane}", "5", "instructional_group", "english")
            for lane in ("A", "B")
        ] + [
            ("E2E Digital 7-8", "instructional", "E2E Цифровая грамотность 7–8", "7", "instructional_group", "digital"),
            *[(f"E2E Elective {index}", "instructional", f"E2E Факультатив {index}", "5", "instructional_group", "elective") for index in range(1, 5)],
            ("E2E Empty Group", "instructional", "E2E Пустая группа", "5", "instructional_group", "elective"),
        ]
        group_ids: dict[str, str] = {"7": str(group_id)}
        for name, group_type, display_name, base_class, role, dimension in group_specs:
            connection.execute(
                "INSERT INTO groups(name,group_type,display_name,base_class_name,role,semantic_dimension,canonical) "
                "VALUES (?,?,?,?,?,?,1) ON CONFLICT(name,group_type) DO UPDATE SET "
                "display_name=excluded.display_name,base_class_name=excluded.base_class_name,role=excluded.role,semantic_dimension=excluded.semantic_dimension",
                (name, group_type, display_name, base_class, role, dimension),
            )
            row = connection.execute("SELECT id FROM groups WHERE name=? AND group_type=?", (name, group_type)).fetchone()
            group_ids[name] = str(row["id"])
        connection.execute("UPDATE groups SET subject = 'Математика', subject_subgroup = substr(name, -1) WHERE name IN ('grade7:split:1', 'grade7:split:2')")
        connection.execute("UPDATE groups SET subject = 'Математика', subject_subgroup = substr(name, -1) WHERE name LIKE 'Math %'")
        connection.execute("UPDATE groups SET subject = 'Английский язык', subject_subgroup = '6' WHERE name = 'english:6'")

        for grade in range(5, 12):
            for lane in range(3):
                source_ref = f"local-e2e:teacher-{grade}-{lane}"
                display_name = f"Педагог {grade}-{lane + 1}"
                row = connection.execute(
                    "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (source_ref,),
                ).fetchone()
                if not row:
                    connection.execute(
                        "INSERT INTO identities(kind,display_name,origin,source_ref) VALUES ('teacher',?,'local_e2e',?)",
                        (display_name, source_ref),
                    )
                    row = connection.execute(
                        "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (source_ref,),
                    ).fetchone()
                synthetic_teachers[f"{grade}:{lane}"] = (str(row["id"]), display_name)

        def ensure_student(source_ref: str, name: str, grade: str) -> str:
            identity = connection.execute(
                "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (source_ref,),
            ).fetchone()
            if identity:
                return str(identity["id"])
            connection.execute(
                "INSERT INTO identities(kind,display_name,class_name,origin,source_ref) VALUES ('student',?,?, 'local_e2e',?)",
                (name, grade, source_ref),
            )
            identity = connection.execute(
                "SELECT id FROM identities WHERE origin='local_e2e' AND source_ref=?", (source_ref,),
            ).fetchone()
            return str(identity["id"])

        students_by_group = {
            "7": [str(identities["student"]), ensure_student("local-e2e:student-remainder", "Тестовый ученик остатка", "7")],
            "Math 7-1": [str(identities["student"])],
            "9": [ensure_student("local-e2e:student-9a", "Тестовый ученик 9A", "9"),
                 ensure_student("local-e2e:student-9b", "Тестовый ученик 9B", "9"),
                 ensure_student("local-e2e:student-9c", "Тестовый ученик 9C", "9")],
            "Math 9-A": [], "Math 9-B": [], "Math 9-C": [],
            "English 10-11": [ensure_student("local-e2e:student-10", "Тестовый ученик 10", "10"),
                              ensure_student("local-e2e:student-11", "Тестовый ученик 11", "11")],
            "Math 11 Interage": [ensure_student("local-e2e:student-10", "Тестовый ученик 10", "10"),
                                 ensure_student("local-e2e:student-11", "Тестовый ученик 11", "11")],
            "10": [], "11": [],
        }
        seventh = [
            ensure_student(f"local-e2e:grade7-{index}", f"E2E ученик 7-{index}", "7-А" if index <= 2 else "7-Б")
            for index in range(1, 5)
        ]
        students_by_group["grade7:base:7-А"] = seventh[:2]
        students_by_group["grade7:base:7-Б"] = seventh[2:]
        students_by_group["grade7:split:1"] = [seventh[0], seventh[2]]
        students_by_group["grade7:split:2"] = [seventh[1], seventh[3]]
        students_by_group["grade7:split:2"].append(str(identities["student"]))
        students_by_group["9-Ан"] = students_by_group["9"][:2]
        students_by_group["9-Д"] = students_by_group["9"][2:]
        students_by_group["english:6"] = [students_by_group["9"][0], students_by_group["English 10-11"][0]]
        # Give every parallel a small, deterministic synthetic class roster.
        # The nine-grade A/B/C partition remains explicit for conflict tests.
        for grade in (5, 6, 8):
            class_name = str(grade)
            students_by_group[class_name] = [ensure_student(f"local-e2e:student-{grade}", f"Тестовый ученик {grade}", class_name)]
        # Scenario rosters stay outside the canonical math partitions, so the
        # extra lessons below produce only the conflicts they explicitly model.
        for lane in ("A", "B"):
            for grade in (5, 6):
                student = ensure_student(f"local-e2e:e2e-english-{lane}-{grade}", f"E2E Английский {lane} · {grade}", str(grade))
                students_by_group.setdefault(str(grade), []).append(student)
                students_by_group.setdefault(f"E2E English 5-6 {lane}", []).append(student)
        for grade in (7, 8):
            student = ensure_student(f"local-e2e:e2e-digital-{grade}", f"E2E Цифровая грамотность · {grade}", str(grade))
            students_by_group.setdefault(str(grade), []).append(student)
            students_by_group.setdefault("E2E Digital 7-8", []).append(student)
        for index in range(1, 5):
            students_by_group[f"E2E Elective {index}"] = []
            for grade in (5, 6):
                student = ensure_student(f"local-e2e:e2e-elective-{index}-{grade}", f"E2E Факультатив · {index} · {grade}", str(grade))
                students_by_group.setdefault(str(grade), []).append(student)
                students_by_group[f"E2E Elective {index}"].append(student)
        students_by_group["E2E Empty Group"] = []
        students_by_group["9"] = [ensure_student(f"local-e2e:student-9{letter.lower()}", f"Тестовый ученик 9{letter}", "9") for letter in ("A", "B", "C")]
        students_by_group["10"] = [students_by_group["English 10-11"][0]]
        students_by_group["11"] = [students_by_group["English 10-11"][1]]
        for math_name, student in zip(("Math 9-A", "Math 9-B", "Math 9-C"), students_by_group["9"]):
            students_by_group[math_name] = [student]
        for grade in (5, 6, 8, 10, 11):
            student = (students_by_group.get(str(grade)) or [None])[0]
            if student:
                students_by_group[f"Math {grade}-A"] = [student]
                students_by_group[f"Math {grade}-B"] = []
        students_by_group["Math 7-A"] = [str(identities["student"])]
        students_by_group["Math 7-B"] = []
        for group_name, members in students_by_group.items():
            for identity_id in members:
                # The seeded test student already has this exact base-class
                # membership above; inserting it again with another source_ref
                # makes the profile return the same group twice.
                if group_name == "7" and identity_id == str(identities["student"]):
                    continue
                connection.execute(
                    "INSERT INTO memberships(group_id,identity_id,member_role,source,source_ref,active,created_by) "
                    "VALUES (?,?,'student','local_e2e',?,1,?) ON CONFLICT DO NOTHING",
                    (group_ids[group_name], identity_id, f"e2e:{group_name}:{identity_id}", student_user_id),
                )

    from backend.services.canonical_schedule import import_canonical_artifact

    def assigned(activity: str, audience: dict, *, room: str = "", teacher_key: str = "7:0") -> dict:
        teacher_id, teacher_name = synthetic_teachers[teacher_key]
        return {
            "activity": activity,
            "audience": audience,
            "teacher_ids": [teacher_id],
            "source_cells": ["E2E fixture"],
            "metadata": {"teachers": [teacher_name], "room": room},
        }

    week_start = "2026-09-21"
    slots = [("09:00", "09:45"), ("09:55", "10:40"), ("10:55", "11:40"),
             ("11:50", "12:35"), ("12:45", "13:30"), ("13:40", "14:25")]
    blocks: dict[str, dict] = {}

    def put_block(day: int, period: int, grade_scope: str, assignments: list[dict], *, mode: str = "SINGLE") -> None:
        start, end = slots[period]
        key = f"{week_start}|{day}|{start}|{grade_scope}"
        blocks[key] = {"weekday": day, "slot": {"start": start, "end": end},
                       "grade_scope": grade_scope, "status": "resolved", "mode": mode,
                       "derived_from": {"source_cells": ["Synthetic E2E schedule"]},
                       "assignments": assignments}

    # Fill the whole school week. Grade 9 first-period parallel A/B/C remains
    # stable because existing browser checks use this exact frame.
    for weekday in range(5):
        for grade in range(5, 12):
            class_id = group_ids[str(grade)]
            math_groups = [f"Math {grade}-A", f"Math {grade}-B"]
            if grade == 9:
                math_groups.append("Math 9-C")
            for period, subject in enumerate(("Математика", "Русский язык", "История", "Естествознание")):
                if grade == 9 and weekday == 0 and period == 0:
                    assignments = [assigned("Математика", {"type": "canonical_groups", "canonical_group_ids": [group_ids[name]]}, room=str(19 + lane * 20), teacher_key=f"9:{lane}")
                                   for lane, name in enumerate(("Math 9-A", "Math 9-B", "Math 9-C"))]
                    put_block(weekday, period, "9", assignments, mode="PARALLEL")
                    continue
                if subject == "Математика":
                    assignments = [assigned(subject, {"type": "canonical_groups", "canonical_group_ids": [group_ids[name]]}, room=str(10 + grade + lane * 20), teacher_key=f"{grade}:{lane}")
                                   for lane, name in enumerate(math_groups)]
                    put_block(weekday, period, str(grade), assignments, mode="PARALLEL")
                else:
                    put_block(weekday, period, str(grade), [assigned(subject, {"type": "canonical_groups", "canonical_group_ids": [class_id]}, room=str(10 + grade), teacher_key=f"{grade}:0")])
    # Cross-grade English and explicit source-only no-lesson marker.
    put_block(2, 4, "10,11", [assigned("Английский", {"type": "canonical_groups", "canonical_group_ids": [group_ids["English 10-11"]]}, room="60", teacher_key="10:2")])
    put_block(1, 1, "7", [assigned("Математика", {"type": "canonical_groups", "canonical_group_ids": [group_ids["Math 7-1"]]}, room="17", teacher_key="7:0"),
                           assigned("Нет урока", {"type": "source_only_audience", "label": "7"})], mode="SPLIT")
    artifact = {
        "schema_version": "local-e2e-v1", "version_id": "local-e2e-template",
        "source_snapshot": {"id": "local-e2e-template-source", "fingerprint": "local-e2e-template-fingerprint"},
        "blocks": blocks,
    }
    import_canonical_artifact(database, artifact, status="approved_baseline")


def run_api() -> None:
    configure_environment()
    seed_database()
    import uvicorn

    port = int(os.getenv("MY_ISLAND_TEST_API_PORT", "8011"))
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False, log_level="info")


def run_tests(pattern: str) -> int:
    configure_environment()
    seed_database()
    import unittest

    suite = unittest.defaultTestLoader.discover(str(ROOT / "backend" / "tests"), pattern=pattern)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reset", "seed", "api", "test", "preflight"))
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    if args.command == "reset":
        configure_environment()
        reset_database()
        print(f"Local test database reset and seeded: {DB_PATH}")
        return 0
    if args.command == "seed":
        seed_database()
        print(f"Local test database ready: {DB_PATH}")
        return 0
    if args.command == "preflight":
        configure_environment()
        from backend.config import Settings
        settings = Settings.from_env()
        settings.validate()
        if not settings.local_test_mode or settings.database_url or Path(settings.database_path).resolve() != DB_PATH.resolve():
            raise RuntimeError("E2E requires only the marked .local-test SQLite database")
        if TEST_DIR.resolve() != (ROOT / ".local-test" / (PROFILE if PROFILE in {"e2e", "schedule"} else "")).resolve():
            raise RuntimeError("E2E test directory is not the dedicated .local-test directory")
        if MARKER.exists() and MARKER.read_text(encoding="utf-8") != MARKER_CONTENT:
            raise RuntimeError("Local test DB marker is unexpected; refusing to modify this directory")
        if DB_PATH.exists() and not MARKER.exists():
            raise RuntimeError("A database already exists without the local-test marker; refusing to use or erase it")
        if any((settings.telegram_bot_token, settings.google_oauth_client_id,
                settings.google_oauth_client_secret, settings.google_oauth_refresh_token,
                settings.google_sheets_spreadsheet_id, settings.google_classroom_course_id)):
            raise RuntimeError("E2E preflight found a non-local integration setting")
        print("E2E preflight passed: loopback-only app, local-test mode, isolated SQLite, external integrations disabled")
        return 0
    if args.command == "api":
        run_api()
        return 0
    return run_tests(args.pattern)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"Local test environment refused to start: {error}", file=sys.stderr)
        raise SystemExit(2)
