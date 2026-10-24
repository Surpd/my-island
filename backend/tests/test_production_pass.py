from __future__ import annotations

import os
import tempfile
import threading
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
        absence = next(item for item in parsed["results"] if item["student_name"] == "Иванов Иван" and item["source_coordinate"].startswith("C"))
        self.assertEqual((absence["numeric_score"], absence["status"]), (None, "н"))

    def test_roster_keeps_student_with_no_scores_yet(self):
        values = [*self.VALUES, ["C", "", "", ""], ["Новая Ученица", "", "", ""]]
        parsed = parse_journal_values(values, grade=9, subject="Математика", sheet_title="Математика")
        self.assertIn("Новая Ученица", {student["student_name"] for student in parsed["students"]})
        self.assertNotIn("Новая Ученица", {result["student_name"] for result in parsed["results"]})

    def test_real_summary_headers_are_typed_and_not_imported_as_assignments(self):
        values = [
            ["Дата", "01.09", "02.09", "", "", "", "", ""],
            ["Задание", "Пробник ОГЭ — оценка", "ИТОГОВОЕ СОБЕСЕДОВАНИЕ", "Сумма баллов за триместр", "Допуск к аттестации ", "Средняя отметка ", "Оценка за триместр", "Пропущенные весы"],
            ["Вес", 1, 1, "", "", "", "", ""],
            ["Оценка", 5, 5, "", "", "", "", ""],
            ["Иванов Иван", 4, 5, 17, True, "4,366666667", "", 2],
        ]
        parsed = parse_journal_values(values, grade=9, subject="Русский язык", sheet_title="Русский язык")
        self.assertEqual([item["title"] for item in parsed["assessments"]], ["Пробник ОГЭ — оценка", "ИТОГОВОЕ СОБЕСЕДОВАНИЕ"])
        self.assertEqual({item["summary_type"] for item in parsed["summaries"]}, {"total_points", "admission", "average_grade", "missing_weights"})
        average = next(item for item in parsed["summaries"] if item["summary_type"] == "average_grade")
        admission = next(item for item in parsed["summaries"] if item["summary_type"] == "admission")
        self.assertEqual(average["numeric_value"], "4.366666667")
        self.assertIsNone(next((item for item in parsed["summaries"] if item["summary_type"] == "term_grade"), None))
        self.assertEqual(admission["text_value"], "True")

    def test_sheet_rename_keeps_period_identity_and_new_empty_term_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "period.db"))
            database.initialize()
            sync_journal_values(database, self.VALUES, spreadsheet_id="book", spreadsheet_title="Журнал", grade=9, subject="Математика", sheet_title="Математика", sheet_id=123)
            original = database.journal_status()[0]
            changed_title = "Математика — I триместр"
            sync_journal_values(database, self.VALUES, spreadsheet_id="book", spreadsheet_title="Журнал", grade=9, subject="Математика", sheet_title=changed_title, sheet_id=123)
            renamed = database.journal_status()[0]
            self.assertEqual((renamed["id"], renamed["term_number"], renamed["subject"]), (original["id"], 1, "Математика"))
            self.assertEqual(len(renamed["markers"]), len(original["markers"]))
            blank = [["Дата", "", "", ""], ["Задание", "", "Средняя отметка", "Оценка за триместр"], ["Вес", "", "", ""], ["Оценка", "", "", ""], ["Петрова Анна", "", "", ""]]
            sync_journal_values(database, blank, spreadsheet_id="book", spreadsheet_title="Журнал", grade=9, subject="Математика", sheet_title="Математика", sheet_id=456)
            new = next(source for source in database.journal_status() if source["sheet_id"] == "456")
            detail = database.journal_detail(new["id"])
            self.assertEqual((new["term_number"], new["period_status"], new["assessment_count"], new["result_count"]), (None, "unassigned", 0, 0))
            self.assertEqual([student["display_name"] for student in detail["students"]], ["Петрова Анна"])
            self.assertTrue(database.set_journal_current_term("book", 9, "2026/27", 2))
            self.assertEqual(next(source for source in database.journal_status() if source["id"] == original["id"])["period_status"], "archived")
            self.assertTrue(database.set_journal_source_term(new["id"], 2))
            sync_journal_values(database, blank, spreadsheet_id="book", spreadsheet_title="Журнал", grade=9, subject="Математика", sheet_title="Математика", sheet_id=456)
            new = next(source for source in database.journal_status() if source["sheet_id"] == "456")
            self.assertEqual((new["term_number"], new["period_status"]), (2, "current"))
            sync_journal_values(database, blank, spreadsheet_id="book", spreadsheet_title="Журнал", grade=9, subject="Математика", sheet_title="Математика", sheet_id=456)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_sources WHERE spreadsheet_id='book'").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_students WHERE source_id=?", (new["id"],)).fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_results WHERE assessment_id IN (SELECT id FROM journal_assessments WHERE source_id=?)", (new["id"],)).fetchone()[0], 0)

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
            detail = database.journal_detail(source_id, "A")
            self.assertEqual(len(detail["assessments"]), 2)
            ivan = next(student for student in detail["students"] if student["display_name"] == "Иванов Иван")
            self.assertEqual(ivan["results"][str(detail["assessments"][0]["id"])]["numeric_score"], 8.5)
            self.assertTrue(any(summary["label"] == "Итого" and summary["numeric_value"] == 8.5 for summary in ivan["summaries"]))
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

    def test_repeated_gradebook_blocks_and_stable_tab_id_are_discovered(self):
        values = [
            ["9A"], ["Дата", "01.09"], ["Задание", "Работа A"], ["Вес", 1], ["Оценка", 5],
            ["Иванов Иван", 4], ["9B"], ["Дата", "01.09"], ["Задание", "Работа B"], ["Вес", 1], ["Оценка", 5],
            ["Петров Пётр", 5],
        ]
        parsed = parse_journal_values(values, grade=9, subject="Математика", sheet_title="Математика", sheet_id=123)
        self.assertEqual({student["group_marker"] for student in parsed["students"]}, {"A", "B"})
        self.assertEqual(len(parsed["assessments"]), 2)
        self.assertTrue(all(item["external_key"].startswith("123:") for item in parsed["assessments"]))

    def test_reimport_preserves_assignment_and_student_ids_across_order_and_column_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика", sheet_id=4)
            with database.connection() as connection:
                initial = {row["title"]: row["id"] for row in connection.execute("SELECT id,title FROM journal_assessments WHERE active=1")}
                students = {row["student_name"]: row["journal_student_id"] for row in connection.execute("SELECT student_name,journal_student_id FROM journal_results WHERE is_current=1")}
            changed = [list(row) for row in self.VALUES]
            changed[1][1] = "Домашняя работа"  # The title changes but the roster/value vector stays unique.
            changed[5], changed[6] = changed[6], changed[5]  # Students move.
            for row in changed:
                row.insert(2, "")  # A new assessment is inserted before the existing second work.
            changed[0][2] = "03.09"
            changed[1][2] = "Новая работа"
            changed[2][2] = 1
            changed[3][2] = 10
            changed[5][2] = 5
            changed[6][2] = 3
            changed[8][2] = 2
            changed[5][3] = 4  # Existing score correction on the second work.
            sync_journal_values(database, changed, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика", sheet_id=4)
            with database.connection() as connection:
                current = connection.execute("SELECT id,title FROM journal_assessments WHERE active=1").fetchall()
                student_rows = connection.execute("SELECT display_name AS student_name,id AS journal_student_id FROM journal_students WHERE active=1").fetchall()
                result_count = connection.execute("SELECT COUNT(*) FROM journal_results WHERE is_current=1").fetchone()[0]
            titles = {row["title"] for row in current}
            self.assertIn("Новая работа", titles)
            self.assertIn("Домашняя работа", titles)
            current_student_ids = {row["student_name"]: row["journal_student_id"] for row in student_rows}
            ids_after = {row["title"]: row["id"] for row in current}
            self.assertEqual(ids_after["Домашняя работа"], initial["Входное тестирование"])
            self.assertEqual(ids_after["ДЗ"], initial["ДЗ"])
            for name, identity in students.items():
                self.assertEqual(current_student_ids[name], identity)
            self.assertEqual(result_count, 9)
            self.assertEqual(len({row["id"] for row in current}), len(current))

    def test_empty_source_cell_is_tombstoned_and_unknown_student_does_not_take_another_students_score(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            changed = [list(row) for row in self.VALUES]
            changed[5][1] = ""
            changed.append(["Новый Ученик", "", "", ""])
            sync_journal_values(database, changed, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            with database.connection() as connection:
                absent = connection.execute("SELECT is_current FROM journal_results WHERE student_name='Иванов Иван' AND numeric_score=8.5").fetchone()
                unknown = connection.execute("SELECT identity_id,match_status FROM journal_students WHERE display_name='Новый Ученик'").fetchone()
                history = connection.execute("SELECT COUNT(*) FROM journal_result_history").fetchone()[0]
            self.assertEqual(absent["is_current"], 0)
            self.assertEqual((unknown["identity_id"], unknown["match_status"]), (None, "unlinked"))
            self.assertGreaterEqual(history, 1)

    def test_manual_student_match_is_persisted_as_a_source_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            database.create_identity("student", "Ученик Петрова", "9AN")
            identity = database.create_identity("student", "Петрова Анна", "9AN")
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            source_id = database.journal_status()[0]["id"]
            with database.connection() as connection:
                student_id = connection.execute("SELECT id FROM journal_students WHERE display_name='Петрова Анна'").fetchone()[0]
            self.assertTrue(database.match_journal_student(source_id, student_id, identity["id"]))
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            with database.connection() as connection:
                student = connection.execute("SELECT identity_id,match_method FROM journal_students WHERE id=?", (student_id,)).fetchone()
                mapping = connection.execute("SELECT identity_id,manually_confirmed FROM school_source_mappings WHERE external_key='alias:петрова анна'").fetchone()
            self.assertEqual((student["identity_id"], student["match_method"]), (identity["id"], "admin_selected"))
            self.assertEqual((mapping["identity_id"], mapping["manually_confirmed"]), (identity["id"], 1))

    def test_ambiguous_or_partial_block_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            with self.assertRaises(ValueError):
                sync_journal_values(database, [["Дата", "01.09"], ["Задание", "Сломанная работа"], ["Иванов Иван", 3]], spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика")
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_results WHERE is_current=1").fetchone()[0], 6)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_assessments WHERE active=1").fetchone()[0], 2)

    def test_repeated_and_overlapping_syncs_do_not_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            outcomes: list[object] = []
            barrier = threading.Barrier(2)

            def sync():
                try:
                    barrier.wait()
                    outcomes.append(sync_journal_values(database, self.VALUES, spreadsheet_id="current-grade-9", spreadsheet_title="Журнал 9 класс", grade=9, subject="Математика", sheet_title="Математика"))
                except Exception as error:  # Captured for a useful test assertion.
                    outcomes.append(error)

            threads = [threading.Thread(target=sync) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(len(outcomes), 2)
            self.assertFalse([item for item in outcomes if isinstance(item, Exception)], outcomes)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_sources").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_assessments WHERE active=1").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_results WHERE is_current=1").fetchone()[0], 6)

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
