from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.auth import AuthError


class JournalRoleViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "journals.db")
        self.database.initialize()
        self.app = FastAPI()
        self.app.include_router(create_router(self.database))
        self.client = TestClient(self.app)
        self.users: dict[str, int] = {}
        self._create_fixture()
        self.auth_patch = patch("backend.handlers.api.resolve_auth", side_effect=self._resolve_auth)
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()
        self.temp.cleanup()

    def _resolve_auth(self, _init_data, dev_header):
        user_id = self.users.get(dev_header or "")
        if user_id is None:
            raise AuthError("Test identity is not available")
        user = self.database.get_user_by_id(user_id)
        return {"id": user["telegram_user_id"], "dev": True}

    def _create_user(self, key: str, role: str, identity_id: int | None) -> int:
        with self.database.connection() as connection:
            user = self.database.execute(connection,
                "INSERT INTO users(telegram_user_id, role, identity_id) VALUES (?, ?, ?) RETURNING id",
                (710000 + len(self.users), role, identity_id)).fetchone()
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, ?)", (user["id"], role))
        self.users[key] = user["id"]
        return user["id"]

    def _create_fixture(self):
        with self.database.connection() as connection:
            student = self.database.execute(connection,
                "INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Student One', '9-А') RETURNING id").fetchone()["id"]
            other_student = self.database.execute(connection,
                "INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Student Two', '9-А') RETURNING id").fetchone()["id"]
            teacher = self.database.execute(connection,
                "INSERT INTO identities(kind, display_name) VALUES ('teacher', 'Teacher One') RETURNING id").fetchone()["id"]
        self.student_identity = student
        self.other_student_identity = other_student
        self.teacher_identity = teacher
        self.student_user = self._create_user("student-auth", "student", student)
        self.unlinked_user = self._create_user("unlinked-auth", "student", None)
        self.teacher_user = self._create_user("teacher-auth", "teacher", teacher)

        self.source_ids: list[int] = []
        self.group_ids: list[int] = []
        with self.database.connection() as connection:
            for title, period_status, term_number in (("Math I", "archived", 1), ("Math II", "current", 2)):
                source_id = self.database.execute(connection, """INSERT INTO journal_sources(
                    spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, academic_year,
                    term_number, period_status, status)
                    VALUES ('school-book', 'Grade 9', 9, 'Math', ?, '2026/27', ?, ?, 'ready') RETURNING id""",
                    (title, term_number, period_status)).fetchone()["id"]
                self.source_ids.append(source_id)
                assessment_id = self.database.execute(connection, """INSERT INTO journal_assessments(
                    source_id, external_key, title, assessed_on, source_column, group_marker)
                    VALUES (?, ?, ?, '2026-09-01', 'B', 'A') RETURNING id""",
                    (source_id, f"work-{term_number}", f"Work {term_number}")).fetchone()["id"]
                student_row = self.database.execute(connection, """INSERT INTO journal_students(
                    source_id, student_key, display_name, group_marker, identity_id, match_status)
                    VALUES (?, ?, 'Student One', 'A', ?, 'linked') RETURNING id""",
                    (source_id, f"student-one-{term_number}", student)).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(
                    assessment_id, journal_student_id, identity_id, student_name, group_marker,
                    numeric_score, source_coordinate)
                    VALUES (?, ?, ?, 'Student One', 'A', ?, 'B6')""",
                    (assessment_id, student_row, student, 4 + term_number))
                self.database.execute(connection, """INSERT INTO journal_student_summaries(
                    journal_student_id, label, summary_type, numeric_value, source_coordinate)
                    VALUES (?, 'Средняя отметка', 'average_grade', ?, 'D6')""",
                    (student_row, 4 + term_number))
                self.database.execute(connection, """INSERT INTO journal_student_summaries(
                    journal_student_id, label, summary_type, numeric_value, source_coordinate, is_current)
                    VALUES (?, 'Старая средняя', 'average_grade', 2, 'E6', 0)""", (student_row,))

                team_row = self.database.execute(connection, """INSERT INTO journal_students(
                    source_id, student_key, entry_kind, display_name, group_marker, identity_id, match_status)
                    VALUES (?, ?, 'team', 'Team One', 'A', ?, 'linked') RETURNING id""",
                    (source_id, f"team-one-{term_number}", student)).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(
                    assessment_id, journal_student_id, identity_id, student_name, group_marker,
                    numeric_score, source_coordinate)
                    VALUES (?, ?, ?, 'Team One', 'A', 5, 'B7')""", (assessment_id, team_row, student))

                unlinked_row = self.database.execute(connection, """INSERT INTO journal_students(
                    source_id, student_key, display_name, group_marker, match_status)
                    VALUES (?, ?, 'Unlinked Student', 'A', 'unlinked') RETURNING id""",
                    (source_id, f"unlinked-{term_number}")).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(
                    assessment_id, journal_student_id, student_name, group_marker, numeric_score, source_coordinate)
                    VALUES (?, ?, 'Unlinked Student', 'A', 3, 'B8')""", (assessment_id, unlinked_row))

                group_a = self.database.execute(connection,
                    "INSERT INTO groups(name, group_type, subject) VALUES (?, 'subject_group', 'Math') RETURNING id",
                    (f"Math A {term_number}",)).fetchone()["id"]
                group_b = self.database.execute(connection,
                    "INSERT INTO groups(name, group_type, subject) VALUES (?, 'subject_group', 'Math') RETURNING id",
                    (f"Math B {term_number}",)).fetchone()["id"]
                self.group_ids.append(group_a)
                self.database.execute(connection, """INSERT INTO journal_group_mappings(source_id, group_marker, group_id)
                    VALUES (?, 'A', ?), (?, 'B', ?)""", (source_id, group_a, source_id, group_b))
                self.database.execute(connection, """INSERT INTO teacher_assignments(
                    teacher_identity_id, group_id, subject, active, created_by)
                    VALUES (?, ?, 'Math', 1, ?)""", (teacher, group_a, self.teacher_user))
                self.database.execute(connection, """INSERT INTO journal_assessments(
                    source_id, external_key, title, assessed_on, source_column, group_marker)
                    VALUES (?, ?, 'Work B', '2026-09-02', 'C', 'B')""",
                    (source_id, f"work-b-{term_number}"))
                self.database.execute(connection, """INSERT INTO journal_students(
                    source_id, student_key, display_name, group_marker, identity_id, match_status)
                    VALUES (?, ?, 'Other Student', 'B', ?, 'linked')""",
                    (source_id, f"other-student-{term_number}", other_student))
                outsider_row = self.database.execute(connection, """INSERT INTO journal_students(
                    source_id, student_key, display_name, group_marker, identity_id, match_status)
                    VALUES (?, ?, 'Other Student', 'A', ?, 'linked') RETURNING id""",
                    (source_id, f"outsider-a-{term_number}", other_student)).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(
                    assessment_id, journal_student_id, identity_id, student_name, group_marker,
                    numeric_score, source_coordinate)
                    VALUES (?, ?, ?, 'Other Student', 'A', 2, 'B9')""",
                    (assessment_id, outsider_row, other_student))
                self.database.execute(connection, """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref)
                    VALUES (?, ?, 'student', 'test_fixture', ?)""", (group_a, student, f"term-{term_number}"))

    def test_student_journals_include_only_own_linked_individual_results_and_archived_periods(self):
        response = self.client.get("/api/student/journals", headers={"X-Dev-Auth": "student-auth"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual({item["source"]["period_status"] for item in body["items"]}, {"archived", "current"})
        for item in body["items"]:
            self.assertEqual(item["student"]["display_name"], "Student One")
            self.assertEqual([result["numeric_score"] for result in item["results"]], [4 + item["source"]["term_number"]])
            self.assertEqual([summary["summary_type"] for summary in item["summaries"]], ["average_grade"])
            self.assertEqual([summary["label"] for summary in item["summaries"]], ["Средняя отметка"])
            self.assertTrue(all(assessment["group_marker"] == "A" for assessment in item["assessments"]))

    def test_teacher_listing_has_only_accessible_pairs_and_detail_stays_scoped(self):
        response = self.client.get("/api/teacher/journals", headers={"X-Dev-Auth": "teacher-auth"})
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertEqual(len(items), 2)  # Marker A in each archived/current source.
        self.assertTrue(all(item["group_marker"] == "A" for item in items))
        self.assertTrue(all("students" not in item and "results" not in item for item in items))

        source_id = items[0]["source_id"]
        allowed = self.client.get(f"/api/teacher/journals/{source_id}/A", headers={"X-Dev-Auth": "teacher-auth"})
        denied = self.client.get(f"/api/teacher/journals/{source_id}/B", headers={"X-Dev-Auth": "teacher-auth"})
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(denied.status_code, 403)
        detail = allowed.json()
        self.assertEqual(detail["source"]["period_status"], items[0]["period_status"])
        self.assertEqual([student["display_name"] for student in detail["students"]], ["Student One"])
        self.assertEqual(detail["students"][0]["summaries"][0]["summary_type"], "average_grade")
        self.assertEqual([result["student_name"] for result in detail["assessments"][0]["results"]], ["Student One"])
        group_journal = self.database.list_group_journal(self.group_ids[-1])
        self.assertEqual({row["student_name"] for row in group_journal}, {"Student One"})
        self.assertTrue(all(row["identity_id"] == self.student_identity for row in group_journal))

    def test_teacher_journal_source_catalogue_reuses_one_connection_and_keeps_scope(self):
        with patch.object(self.database, "connection", wraps=self.database.connection) as connection_spy:
            pairs = self.database.teacher_journal_sources(self.teacher_user)

        self.assertEqual(connection_spy.call_count, 1)
        self.assertEqual(len(pairs), 2)
        self.assertEqual({item["group_marker"] for item in pairs}, {"A"})
        self.assertEqual(set(item["source_id"] for item in pairs), set(self.source_ids))

    def test_unmapped_all_marker_falls_back_only_to_assigned_canonical_class(self):
        with self.database.connection() as connection:
            student_a = self.database.execute(connection,
                "INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Class A Student', '7-А') RETURNING id").fetchone()["id"]
            student_b = self.database.execute(connection,
                "INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Class B Student', '7-Б') RETURNING id").fetchone()["id"]
            class_a = self.database.execute(connection,
                "INSERT INTO groups(name, group_type, subject, base_class_name, canonical) VALUES ('7-А', 'class', 'Math', '7-А', 1) RETURNING id").fetchone()["id"]
            class_b = self.database.execute(connection,
                "INSERT INTO groups(name, group_type, subject, base_class_name, canonical) VALUES ('7-Б', 'class', 'Math', '7-Б', 1) RETURNING id").fetchone()["id"]
            self.database.execute(connection, """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref)
                VALUES (?, ?, 'student', 'test_fixture', '7-a'), (?, ?, 'student', 'test_fixture', '7-b')""",
                (class_a, student_a, class_b, student_b))
            self.database.execute(connection, """INSERT INTO teacher_assignments(
                teacher_identity_id, group_id, subject, active, created_by)
                VALUES (?, ?, 'Math', 1, ?)""", (self.teacher_identity, class_a, self.teacher_user))

            source_ids = {}
            for subject in ("Math", "English"):
                source_id = self.database.execute(connection, """INSERT INTO journal_sources(
                    spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, academic_year,
                    term_number, period_status, status)
                    VALUES (?, 'Grade 7', 7, ?, ?, '2026/27', 1, 'current', 'ready') RETURNING id""",
                    (f"shared-{subject.lower()}", subject, f"{subject} all")).fetchone()["id"]
                source_ids[subject] = source_id
                assessment_id = self.database.execute(connection, """INSERT INTO journal_assessments(
                    source_id, external_key, title, assessed_on, source_column, group_marker)
                    VALUES (?, ?, ?, '2026-09-03', 'B', 'all') RETURNING id""",
                    (source_id, f"work-{subject}", f"{subject} shared work")).fetchone()["id"]
                for key, identity_id, name in (("a", student_a, "Class A Student"), ("b", student_b, "Class B Student")):
                    journal_student_id = self.database.execute(connection, """INSERT INTO journal_students(
                        source_id, student_key, entry_kind, display_name, group_marker, identity_id, match_status)
                        VALUES (?, ?, 'individual', ?, 'all', ?, 'linked') RETURNING id""",
                        (source_id, f"{subject}-{key}", name, identity_id)).fetchone()["id"]
                    self.database.execute(connection, """INSERT INTO journal_results(
                        assessment_id, journal_student_id, identity_id, student_name, group_marker,
                        numeric_score, source_coordinate)
                        VALUES (?, ?, ?, ?, 'all', 5, ?)""",
                        (assessment_id, journal_student_id, identity_id, name, f"{key}-{subject}"))
            mapped_source = self.database.execute(connection, """INSERT INTO journal_sources(
                spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, academic_year,
                term_number, period_status, status)
                VALUES ('shared-math-mapped', 'Grade 7', 7, 'Math', 'Math mapped all', '2026/27', 1, 'current', 'ready') RETURNING id""").fetchone()["id"]
            source_ids["Mapped"] = mapped_source
            self.database.execute(connection, """INSERT INTO journal_group_mappings(source_id, group_marker, group_id)
                VALUES (?, 'all', ?)""", (mapped_source, class_b))
            no_intersection_source = self.database.execute(connection, """INSERT INTO journal_sources(
                spreadsheet_id, spreadsheet_title, grade, subject, sheet_title, academic_year,
                term_number, period_status, status)
                VALUES ('shared-math-class-b-only', 'Grade 7', 7, 'Math', 'Math class B only', '2026/27', 1, 'current', 'ready') RETURNING id""").fetchone()["id"]
            source_ids["NoOwnClassStudent"] = no_intersection_source
            self.database.execute(connection, """INSERT INTO journal_students(
                source_id, student_key, entry_kind, display_name, group_marker, identity_id, match_status)
                VALUES (?, 'math-b-only', 'individual', 'Class B Student', 'all', ?, 'linked')""",
                (no_intersection_source, student_b))

        pairs = self.database.teacher_journal_sources(self.teacher_user)
        math_pairs = [item for item in pairs if item["source_id"] == source_ids["Math"] and item["group_marker"] == "all"]
        self.assertEqual(len(math_pairs), 1)
        self.assertFalse(any(item["source_id"] == source_ids["English"] for item in pairs))
        self.assertFalse(any(item["source_id"] == source_ids["Mapped"] for item in pairs))
        self.assertFalse(any(item["source_id"] == source_ids["NoOwnClassStudent"] for item in pairs))

        detail = self.database.teacher_journal_detail(self.teacher_user, source_ids["Math"], "all")
        self.assertIsNotNone(detail)
        self.assertEqual([student["display_name"] for student in detail["students"]], ["Class A Student"])
        self.assertEqual([result["student_name"] for result in detail["assessments"][0]["results"]], ["Class A Student"])
        self.assertFalse(self.database.teacher_can_access_journal_marker(self.teacher_user, source_ids["English"], "all"))
        self.assertIsNone(self.database.teacher_journal_detail(self.teacher_user, source_ids["English"], "all"))
        self.assertFalse(self.database.teacher_can_access_journal_marker(self.teacher_user, source_ids["Mapped"], "all"))
        self.assertFalse(self.database.teacher_can_access_journal_marker(self.teacher_user, source_ids["NoOwnClassStudent"], "all"))
        self.assertIsNone(self.database.teacher_journal_detail(self.teacher_user, source_ids["NoOwnClassStudent"], "all"))

    def test_role_and_unlinked_identity_checks_fail_closed(self):
        self.assertEqual(self.client.get("/api/teacher/journals", headers={"X-Dev-Auth": "student-auth"}).status_code, 403)
        self.assertEqual(self.client.get("/api/student/journals", headers={"X-Dev-Auth": "teacher-auth"}).status_code, 403)
        self.assertEqual(self.client.get("/api/student/journals", headers={"X-Dev-Auth": "unlinked-auth"}).status_code, 403)
        self.assertEqual(self.client.get("/api/student/journals").status_code, 401)


if __name__ == "__main__":
    unittest.main()
