from __future__ import annotations

import os
import tempfile
import unittest

from backend.database import Database
from backend.services.teacher_directory import (
    build_assignment_candidates,
    canonical_teacher_name,
    normalize_subjects,
    parse_teacher_rows,
    sync_teacher_directory,
)
from backend.scripts.build_full_current_v2 import teacher_id_for


class TeacherDirectoryTests(unittest.TestCase):
    def _groups(self, database: Database) -> dict[str, dict]:
        with database.connection() as connection:
            rows = connection.execute("SELECT * FROM groups WHERE canonical IS TRUE").fetchall()
        return {row["name"]: dict(row) for row in rows}

    def test_live_sheet_rows_map_only_existing_canonical_groups(self):
        values = [
            ["Учитель", "Предмет", "Классы и группы"],
            ["Дмитрий Ф", "Математика", "9: группы А, В, С; 11: база"],
            ["Ангелина", "Английский язык", "группы 1, 3, 4, 6, 7, 9"],
            ["Тарас", "Информатика", "9: база и ОГЭ; ЕГЭ 10 и егэ 11"],
        ]
        rows, issues = parse_teacher_rows(values)
        self.assertFalse(issues)
        candidates, mapping_issues = build_assignment_candidates(rows, {
            "grade9-math-A": {"name": "grade9-math-A", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "A", "canonical": True},
            "grade9-math-B": {"name": "grade9-math-B", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "B", "canonical": True},
            "grade9-math-C": {"name": "grade9-math-C", "group_type": "subject_group", "subject": "Математика", "base_class_name": "9", "subject_subgroup": "C", "canonical": True},
            "math:11:base": {"name": "math:11:base", "group_type": "subject_group", "subject": "Математика", "base_class_name": "11", "subject_subgroup": "Base", "canonical": True},
            **{f"english:{n}": {"name": f"english:{n}", "group_type": "subject_group", "subject": "Английский язык", "subject_subgroup": str(n), "canonical": True} for n in (1, 3, 4, 6, 7, 9)},
            "instructional:информатика:9:база": {"name": "instructional:информатика:9:база", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "9", "subject_subgroup": "База", "canonical": True},
            "instructional:информатика:9:огэ": {"name": "instructional:информатика:9:огэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "9", "subject_subgroup": "ОГЭ", "exam_track": "ОГЭ", "canonical": True},
            "instructional:информатика:10:егэ": {"name": "instructional:информатика:10:егэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "10", "subject_subgroup": "ЕГЭ", "exam_track": "ЕГЭ", "canonical": True},
            "instructional:информатика:11:егэ": {"name": "instructional:информатика:11:егэ", "group_type": "subject_group", "subject": "Информатика", "base_class_name": "11", "subject_subgroup": "ЕГЭ", "exam_track": "ЕГЭ", "canonical": True},
        })
        self.assertEqual(canonical_teacher_name("ДФ"), "Дмитрий Филиппов")
        self.assertEqual(canonical_teacher_name("Дмитрий Ф"), "Дмитрий Филиппов")
        self.assertEqual(normalize_subjects("Русский язык, литература, лаборатория"), ("Русский язык", "Литература"))
        self.assertEqual(len(candidates), 4 + 6 + 4)
        self.assertFalse(mapping_issues)

    def test_schedule_bootstrap_uses_shared_teacher_alias_directory(self):
        teachers = [
            {"id": "andrey", "display_name": "Андрей"},
            {"id": "dmitry", "display_name": "Дмитрий Филиппов"},
            {"id": "dmitry-k", "display_name": "Дмитрий К"},
            {"id": "irina", "display_name": "Ирина Анатольевна"},
            {"id": "elena", "display_name": "Елена Викторовна"},
        ]
        for alias, expected in (("ANK", "andrey"), ("DF", "dmitry"), ("ДК", "dmitry-k"), ("ИА", "irina"), ("ЕВ", "elena")):
            teacher_id, issue = teacher_id_for(alias, "", teachers)
            self.assertEqual(teacher_id, expected)
            self.assertIsNone(issue)

    def test_sync_is_idempotent_and_does_not_touch_student_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            student = database.create_identity("student", "Ученик Тестов", "5")
            class_group = database.create_group("5", "class")
            database.create_membership(class_group["id"], student["id"], "student", "official_import", "base!A1")
            for name in ("5", "6"):
                group = database.get_group(class_group["id"] if name == "5" else database.create_group(name, "class")["id"])
                with database.connection() as connection:
                    connection.execute("UPDATE groups SET canonical=1, base_class_name=? WHERE id=?", (name, group["id"]))
            rows = [["Учитель", "Предмет", "Классы и группы"], ["Мария", "Математика", "5, 6"]]
            first = sync_teacher_directory(database, rows, spreadsheet_id="sheet", spreadsheet_title="Расписание 2026/27")
            second = sync_teacher_directory(database, rows, spreadsheet_id="sheet", spreadsheet_title="Расписание 2026/27")
            self.assertEqual(first["student_memberships_touched"], 0)
            self.assertEqual(second["student_memberships_touched"], 0)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) AS c FROM memberships WHERE identity_id=? AND active IS TRUE", (student["id"],)).fetchone()["c"], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) AS c FROM teacher_assignments WHERE active IS TRUE", ()).fetchone()["c"], 2)

    def test_teacher_my_groups_and_reverse_lookup_use_existing_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Учитель Тестов")
            student = database.create_identity("student", "Ученик Тестов", "5")
            group = database.create_group("5", "class")
            database.create_membership(group["id"], student["id"], "student", "official_import", "base!A1")
            user = database.get_or_create_user(991, "teacher")
            with database.connection() as connection:
                connection.execute("UPDATE users SET identity_id = ? WHERE id = ?", (teacher["id"], user["id"]))
            database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, user["id"], base_class_name="5")
            groups = database.list_teacher_groups(user["id"])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["name"], "5")
            self.assertEqual(groups[0]["subject"], "Математика")
            self.assertEqual(groups[0]["student_count"], 1)
            admin_group = database.list_groups_admin()[0]
            self.assertEqual(admin_group["teacher_assignments"][0]["teacher_name"], "Учитель Тестов")

    def test_grade_assignment_is_a_single_first_class_target(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Учитель Тестов")
            user = database.get_or_create_user(992, "teacher")
            groups = [database.create_group(name, "class") for name in ("7А", "7Б")]
            other_grade = database.create_group("70А", "class")
            with database.connection() as connection:
                connection.execute("UPDATE users SET identity_id = ? WHERE id = ?", (teacher["id"], user["id"]))
            first = database.set_teacher_grade_assignments(teacher["id"], "7", "Математика", True, user["id"], exam_track="ЕГЭ")
            second = database.set_teacher_grade_assignments(teacher["id"], "7", "Математика", True, user["id"], exam_track="ЕГЭ")
            ordinary = database.set_teacher_grade_assignments(teacher["id"], "7", "Математика", True, user["id"], exam_track=None)
            ordinary_repeat = database.set_teacher_grade_assignments(teacher["id"], "7", "Математика", True, user["id"], exam_track=None)
            self.assertEqual(len(first), 1)
            self.assertIsNone(first[0]["group_id"])
            self.assertEqual(first[0]["grade_level"], "7")
            self.assertEqual(first[0]["exam_track"], "ЕГЭ")
            self.assertEqual(first[0]["id"], second[0]["id"])
            self.assertNotEqual(first[0]["id"], ordinary[0]["id"])
            self.assertEqual(ordinary[0]["id"], ordinary_repeat[0]["id"])
            self.assertTrue(database.teacher_can_access_group(user["id"], groups[0]["id"]))
            self.assertTrue(database.teacher_can_access_group(user["id"], groups[1]["id"]))
            self.assertFalse(database.teacher_can_access_group(user["id"], other_grade["id"]))
            self.assertEqual({item["name"] for item in database.list_teacher_groups(user["id"])}, {"7А", "7Б"})
            with database.connection() as connection:
                rows = connection.execute("SELECT group_id, grade_level FROM teacher_assignments WHERE teacher_identity_id=?", (teacher["id"],)).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["grade_level"] for row in rows}, {"7"})
            replacement = database.replace_teacher_grade_assignment(first[0]["id"], teacher["id"], "8", "Физика", user["id"], exam_track="ОГЭ")
            self.assertEqual(replacement["grade_level"], "8")
            self.assertEqual(replacement["exam_track"], "ОГЭ")
            with database.connection() as connection:
                old = connection.execute("SELECT active FROM teacher_assignments WHERE id=?", (first[0]["id"],)).fetchone()
            self.assertFalse(old["active"])

    def test_group_assignment_edit_preserves_and_updates_exam_context(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Учитель Тестов")
            user = database.get_or_create_user(994, "teacher")
            first_group = database.create_group("9А", "class")
            second_group = database.create_group("9Б", "class")
            assignment = database.set_teacher_assignment(teacher["id"], first_group["id"], "Информатика", True,
                user["id"], base_class_name="9", subject_subgroup="ЕГЭ", exam_track="ЕГЭ")
            assignment_repeat = database.set_teacher_assignment(teacher["id"], first_group["id"], "Информатика", True,
                user["id"], base_class_name="9", subject_subgroup="ЕГЭ", exam_track="ЕГЭ")
            ordinary = database.set_teacher_assignment(teacher["id"], first_group["id"], "Информатика", True,
                user["id"], base_class_name="9", subject_subgroup="", exam_track=None)
            ordinary_repeat = database.set_teacher_assignment(teacher["id"], first_group["id"], "Информатика", True,
                user["id"], base_class_name="9", subject_subgroup="", exam_track=None)
            self.assertEqual(assignment["id"], assignment_repeat["id"])
            self.assertNotEqual(assignment["id"], ordinary["id"])
            self.assertEqual(ordinary["id"], ordinary_repeat["id"])
            updated = database.replace_teacher_assignment(assignment["id"], teacher["id"], first_group["id"],
                "Информатика", user["id"], exam_track="ОГЭ")
            self.assertEqual(updated["exam_track"], "ОГЭ")
            moved = database.replace_teacher_assignment(updated["id"], teacher["id"], second_group["id"], "Информатика", user["id"])
            self.assertEqual(moved["exam_track"], "ОГЭ")
            self.assertEqual(moved["subject_subgroup"], "ЕГЭ")

    def test_classroom_course_context_coexists_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Учитель Тестов")
            user = database.get_or_create_user(995, "teacher")
            group = database.create_group("10А", "class")
            with database.connection() as connection:
                course = connection.execute("INSERT INTO classroom_courses(group_id,external_course_id,title) VALUES (?,?,?) RETURNING id", (group["id"], "course-10a", "Математика 10А")).fetchone()
            ordinary = database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, user["id"])
            course_scoped = database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, user["id"], classroom_course_id=course["id"])
            course_repeat = database.set_teacher_assignment(teacher["id"], group["id"], "Математика", True, user["id"], classroom_course_id=course["id"])
            self.assertNotEqual(ordinary["id"], course_scoped["id"])
            self.assertEqual(course_scoped["id"], course_repeat["id"])
            self.assertEqual(course_scoped["classroom_course_id"], course["id"])

    def test_existing_sqlite_teacher_assignments_upgrade_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "school.db"))
            database.initialize()
            teacher = database.create_identity("teacher", "Учитель Тестов")
            group = database.create_group("8А", "class")
            user = database.get_or_create_user(993, "teacher")
            existing = database.set_teacher_assignment(teacher["id"], group["id"], "Физика", True, user["id"])
            with database.connection() as connection:
                connection.execute("""CREATE TABLE teacher_assignments_legacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_identity_id INTEGER NOT NULL REFERENCES identities(id),
                    group_id INTEGER NOT NULL REFERENCES groups(id),
                    subject TEXT NOT NULL DEFAULT '', base_class_name TEXT, subject_subgroup TEXT,
                    classroom_course_id INTEGER REFERENCES classroom_courses(id), exam_track TEXT,
                    capability TEXT NOT NULL DEFAULT 'teach', source TEXT NOT NULL DEFAULT 'admin_override',
                    source_ref TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
                    valid_from TEXT, valid_until TEXT, created_by INTEGER REFERENCES users(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(teacher_identity_id, group_id, subject, capability, source, source_ref))""")
                connection.execute("INSERT INTO teacher_assignments_legacy SELECT id,teacher_identity_id,group_id,subject,base_class_name,subject_subgroup,classroom_course_id,exam_track,capability,source,source_ref,active,valid_from,valid_until,created_by,created_at FROM teacher_assignments")
                connection.execute("DROP TABLE teacher_assignments")
                connection.execute("ALTER TABLE teacher_assignments_legacy RENAME TO teacher_assignments")
            database.initialize()
            with database.connection() as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(teacher_assignments)").fetchall()}
                row = connection.execute("SELECT group_id,grade_level FROM teacher_assignments WHERE id=?", (existing["id"],)).fetchone()
            self.assertIn("grade_level", columns)
            self.assertEqual(row["group_id"], group["id"])
            self.assertIsNone(row["grade_level"])


if __name__ == "__main__":
    unittest.main()
