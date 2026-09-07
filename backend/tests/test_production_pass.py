from __future__ import annotations

import os
import tempfile
import unittest

from backend.database import Database
from backend.services.journal_import import parse_journal_values, sync_journal_values
from backend.services.google_sync_service import _resolve_sheet_title
from backend.services.google_sheets import parse_school_schedule_values
from backend.services.admin import assign_homeroom, assign_teacher, override_membership
from backend.services.teacher import journal_view


class StructuralScheduleTests(unittest.TestCase):
    def test_unknown_header_terminates_class_block_instead_of_leaking_context(self):
        values = [
            ["", "9-А", "", "служебное", "", "10-А"],
            ["07.09", "Пн", "Пн", "Пн", "Пн", "Пн"],
            ["9:00 - 9:45", "Русский", "Математика", "Естествознание", "Физика", "История"],
        ]
        rows = parse_school_schedule_values(values, sheet_title="7-11 Сентября", spreadsheet_title="Расписание 2026/27")
        self.assertEqual([row["audience"] for row in rows], ["9-А", "9-А", "", "", "10-А"])
        self.assertEqual(rows[2]["parse_status"], "ambiguous")
        self.assertIn("no class header owner", rows[2]["parse_diagnostics"])


class JournalTests(unittest.TestCase):
    VALUES = [
        ["Дата", "01.09", "02.09", ""],
        ["Задание", "Входное тестирование", "ДЗ", "Итого"],
        ["Вес", "2", "1", ""],
        ["Оценка", "10", "5", ""],
        ["A", "", "", ""],
        ["Иванов Иван", "8,5", "н", "8,5"],
        ["Петрова Анна", "10", "5", "15"],
        ["B", "", "", ""],
        ["Сидоров Пётр", "7", "4", "11"],
    ]

    def test_current_journal_tab_matching_ignores_incidental_whitespace(self):
        self.assertEqual(_resolve_sheet_title(["Русский язык ", "Математика "], "Математика"), "Математика ")

    def test_current_gradebook_layout_is_parsed_without_summary_columns(self):
        parsed = parse_journal_values(self.VALUES, grade=9, subject="Математика", sheet_title="Математика")
        self.assertEqual([(item["title"], item["weight"], item["max_score"]) for item in parsed["assessments"]], [("Входное тестирование", 2.0, 10.0), ("ДЗ", 1.0, 5.0)])
        self.assertEqual(len(parsed["results"]), 6)
        absence = next(item for item in parsed["results"] if item["student_name"] == "Иванов Иван" and item["assessment_key"].endswith(":C"))
        self.assertEqual((absence["numeric_score"], absence["status"]), (None, "н"))

    def test_roster_keeps_student_with_no_scores_yet(self):
        values = [*self.VALUES, ["C", "", "", ""], ["Новая Ученица", "", "", ""]]
        parsed = parse_journal_values(values, grade=9, subject="Математика", sheet_title="Математика")
        self.assertIn("Новая Ученица", {student["student_name"] for student in parsed["students"]})
        self.assertNotIn("Новая Ученица", {result["student_name"] for result in parsed["results"]})

    def test_normalized_journal_supports_teacher_analytics(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            with database.connection() as connection:
                teacher_identity = connection.execute("INSERT INTO identities(kind, display_name) VALUES ('teacher', 'Teacher') RETURNING id").fetchone()[0]
                teacher_user = connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (10, 'teacher', ?) RETURNING id", (teacher_identity,)).fetchone()[0]
                connection.execute("INSERT INTO user_roles(user_id, role) VALUES (?, 'teacher')", (teacher_user,))
                student_identity = connection.execute("INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Иванов Иван', '9-А') RETURNING id").fetchone()[0]
            group = database.create_group("Math A", "subject_group")
            database.create_membership(group["id"], student_identity, "student", "official_import")
            database.set_teacher_assignment(teacher_identity, group["id"], "Математика", True, teacher_user)
            counts = sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            source_id = database.journal_status()[0]["id"]
            database.map_journal_group(source_id, "A", group["id"], teacher_user)
            database.map_journal_group(source_id, "B", None, teacher_user, subject_subgroup="B", source="school_mapping")
            database.set_teacher_assignment(teacher_identity, group["id"], "Математика", True, teacher_user, subject_subgroup="B")
            status = database.journal_status()[0]
            view = journal_view(database.list_group_journal(group["id"]))
            self.assertEqual(counts["assessments"], 2)
            self.assertEqual(counts["roster_students"], 3)
            self.assertEqual(counts["linked_students"], 1)
            self.assertEqual(counts["mapped_results"], 2)
            self.assertEqual(status["markers"], ["A", "B"])
            self.assertEqual(status["roster_student_count"], 3)
            self.assertEqual(status["account_unlinked_count"], 4)
            self.assertEqual(status["mappings"][0]["group_name"], "Math A")
            subgroup_mapping = next(item for item in status["mappings"] if item["group_marker"] == "B")
            self.assertEqual((subgroup_mapping["group_id"], subgroup_mapping["subject_subgroup"]), (None, "B"))
            self.assertTrue(database.teacher_can_access_journal_marker(teacher_user, source_id, "B"))
            self.assertFalse(database.teacher_can_access_journal_marker(teacher_user, source_id, "C"))
            self.assertEqual(view["students"][0]["name"], "Иванов Иван")
            self.assertIn("Петрова Анна", {student["name"] for student in view["students"]})
            self.assertEqual(view["analytics"]["average"], 7.83)

    def test_legacy_grade_nine_math_markers_normalize_to_confirmed_groups(self):
        parsed = parse_journal_values(
            [["Дата", "01.09"], ["Задание", "ДЗ"], ["Вес", "1"], ["Оценка", "5"], ["9-1"], ["Иванов Иван", "5"]],
            grade=9,
            subject="Математика",
            sheet_title="Математика",
        )
        self.assertEqual(parsed["students"][0]["group_marker"], "A")

    def test_journal_failure_marks_health_without_deleting_last_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            database.record_journal_failure("current-grade-9", "Математика", "temporary API failure")
            status = database.journal_status()[0]
            self.assertEqual(status["status"], "error")
            self.assertEqual(status["result_count"], 6)


class AdminOperationsTests(unittest.TestCase):
    def test_teacher_and_admin_roles_are_independent_and_membership_exclusion_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            identity = database.create_identity("student", "Student", "9-А")
            with database.connection() as connection:
                user = connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (20, 'student', ?) RETURNING id", (identity["id"],)).fetchone()[0]
                connection.execute("INSERT INTO user_roles(user_id, role) VALUES (?, 'student')", (user,))
            group = database.create_group("9-А", "class")
            database.create_membership(group["id"], identity["id"], "student", "official_import")
            database.set_membership_override(identity["id"], group["id"], "student", "exclude", user, "temporary correction")
            self.assertEqual(database.get_student_profile(user)["groups"], [])
            with database.connection() as connection:
                official = connection.execute("SELECT active FROM memberships WHERE identity_id = ? AND source = 'official_import'", (identity["id"],)).fetchone()
            self.assertEqual(official["active"], 1)
            database.set_user_roles(user, ["teacher"], "teacher")
            self.assertTrue(database.user_has_role(user, "teacher"))
            self.assertFalse(database.user_has_role(user, "admin"))

    def test_assignment_overrides_validate_identity_kind_group_type_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            student = database.create_identity("student", "Student", "9-А")
            teacher = database.create_identity("teacher", "Teacher")
            class_group = database.create_group("9-А", "class")
            subject_group = database.create_group("9-А Math", "subject_group")
            with self.assertRaisesRegex(ValueError, "teacher identity"):
                assign_teacher(database, student["id"], class_group["id"], "", True, None)
            with self.assertRaisesRegex(ValueError, "class group"):
                assign_homeroom(database, teacher["id"], subject_group["id"], True, None)
            with self.assertRaisesRegex(ValueError, "reason is required"):
                override_membership(database, student["id"], class_group["id"], "student", "include", None)


if __name__ == "__main__":
    unittest.main()
