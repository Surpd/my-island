from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.auth import AuthError
from backend.services.journal_import import parse_journal_values, sync_journal_values


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

    def test_journal_overview_reconciles_archive_and_current_source_with_detail(self):
        self._create_user("overview-admin", "admin", None)
        headers = {"X-Dev-Auth": "overview-admin"}
        path = "/api/admin/analytics/journals"
        self.assertEqual(self.client.get(path, headers={"X-Dev-Auth": "student-auth"}).status_code, 403)
        params = {"group_marker": "A", "grade": 9, "subject": "Math", "academic_year": "2026/27"}
        response = self.client.get(path, params=params, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source_count"], 2)
        for item in response.json()["items"]:
            detail = self.client.get(f"/api/admin/journals/{item['id']}/analytics",
                                     params={"group_marker": "A"}, headers=headers).json()
            for field, value in detail["totals"].items():
                self.assertEqual(item[field], value)
        filtered = self.client.get(path, params={**params, "term_number": 2, "start": "2027-01-01"}, headers=headers)
        self.assertEqual(filtered.json()["source_count"], 1)
        self.assertIsNone(filtered.json()["items"][0]["numeric_coverage"])

    def test_no_snapshot_and_failed_refresh_do_not_look_like_a_complete_matrix(self):
        admin = self._create_user("snapshot-admin", "admin", None)
        headers = {"X-Dev-Auth": "snapshot-admin"}
        source = self.source_ids[0]
        with self.database.connection() as conn:
            waiting = self.database.execute(conn, """INSERT INTO journal_sources
                (spreadsheet_id,spreadsheet_title,grade,subject,sheet_title,status,last_error_code)
                VALUES ('synthetic-waiting','Fixture',9,'Science','Fixture','error','network')
                RETURNING id""").fetchone()["id"]
            self.database.execute(conn, """UPDATE journal_sources
                SET status='error',last_error_code='network',last_synced_at=CURRENT_TIMESTAMP WHERE id=?""", (source,))
            self.database.execute(conn, """UPDATE journal_assessments
                SET weight=2,max_score=5,raw_source='{"category":"П"}' WHERE source_id=?""", (source,))
        def detail(key):
            return self.client.get(f"/api/admin/journals/{key}/analytics", headers=headers).json()
        missing = detail(waiting)
        self.assertEqual(missing["source"]["snapshot_state"], "unavailable")
        self.assertIsNone(missing["totals"]["numeric_results"])
        old = detail(source)
        self.assertEqual(old["source"]["snapshot_state"], "last_success")
        self.assertGreater(old["totals"]["numeric_results"], 0)
        self.assertEqual({(row["category"], row["weight"], row["max_score"]) for row in old["works"]}, {("П", 2, 5)})
        self.assertEqual(old["roster"]["team_rows"], 1)

    def test_admin_snapshot_analytics_respects_period_marker_and_role(self):
        self._create_user("analytics-admin", "admin", None)
        headers = {"X-Dev-Auth": "analytics-admin"}
        source_id = self.source_ids[0]
        path = f"/api/admin/journals/{source_id}/analytics"
        for role in ("student-auth", "teacher-auth"):
            self.assertEqual(self.client.get(path, headers={"X-Dev-Auth": role}).status_code, 403)
        activity = f"/api/admin/journals/{source_id}/cell-activity"
        self.assertEqual(self.client.get(activity, headers={"X-Dev-Auth": "teacher-auth"}).status_code, 403)
        self.assertEqual(self.client.get(activity, headers=headers).json()["items"], [])
        self.assertEqual(self.client.get(path, headers=headers, params={"group_marker": "missing"}).status_code, 404)
        self.assertEqual(self.client.get(path, headers=headers, params={"start": "2027-01-01", "end": "2026-01-01"}).status_code, 422)
        with self.database.connection() as connection:
            self.database.execute(connection, """UPDATE journal_results SET numeric_score=0
                WHERE assessment_id IN (SELECT id FROM journal_assessments WHERE source_id=?)""", (source_id,))
        response = self.client.get(path, headers=headers, params={"group_marker": "A"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["source"]["period_status"], "archived")
        self.assertEqual(data["roster"], {"total_rows": 4, "team_rows": 1, "unlinked_individual_rows": 1})
        self.assertEqual(data["totals"]["numeric_results"], 4)
        self.assertEqual(data["totals"]["eligible_cells"], 4)
        self.assertEqual(data["period_summary_cells"], {"average_grade": 1})
        empty = self.client.get(path, headers=headers, params={"start": "2027-01-01"}).json()
        self.assertIsNone(empty["totals"]["numeric_coverage"])
        self.assertEqual(empty["works"], [])

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

    def test_second_term_connect_and_repeat_through_student_api(self):
        self._create_user("admin-auth", "admin", None)
        book = "synthetic-school-journal"
        first = [["Дата", "01.09"], ["Работа", "Архив"], ["Вес", 1],
                 ["Оценка", 5], ["Student One", 3]]
        second = [[None] * 41 for _ in range(6)]
        second[0][0], second[1][0], second[2][0], second[3][0] = "Дата", "Задание / работа", "Вес", "Категория (У/О/П)"
        second[0][1] = "II триместр · Группа 1"
        second[0][2:4], second[1][2:4] = ["25.11", "26.11"], ["Отчёт", "Повтор"]
        second[2][2:4], second[3][2:4] = [2, 1], ["О", "П"]
        second[4][0], second[4][2:4] = "Student One", [2, 4]
        second[5][0], second[5][2:4] = "Other Fixture", [5, ""]
        second[0][39] = "Итог"
        second[4][39], second[5][39] = 4, 5
        title = "II · История"
        preview = parse_journal_values(second, grade=7, subject="История", sheet_title=title)
        self.assertEqual([item["category"] for item in preview["assessments"]], ["О", "П"])
        self.assertEqual({row["group_marker"] for row in preview["students"]}, {"Группа 1"})
        self.assertEqual(len(preview["summaries"]), 2)
        self.assertFalse(any(item["spreadsheet_id"] == book for item in self.database.journal_status()))
        tabs = {"I · История": first, title: second, "II · Как работать": [["Инструкция"]]}
        google = Mock()
        google.spreadsheet.return_value = {"sheets": [
            {"properties": {"sheetId": index, "title": name}} for index, name in enumerate(tabs, 1)]}
        google.sheet_values.side_effect = lambda _, address, **kwargs: tabs[address.split("'")[1]]
        with patch("backend.services.google_sync_service._client", return_value=(google, None)):
            payload = {"grade": 7, "spreadsheet_url": book, "auto_sync_enabled": False, "term_number": 2}
            dry = self.client.post("/api/admin/journals/connect", headers={"X-Dev-Auth": "admin-auth"}, json={**payload, "dry_run": True})
            self.assertEqual(dry.status_code, 200)
            self.assertEqual(dry.json()["tabs"][0]["counts"], {"students": 2, "results": 3, "summaries": 2, "assessments": 2})
            self.assertFalse(any(row["spreadsheet_id"] == book for row in self.database.journal_status()))
            sync_journal_values(self.database, first, spreadsheet_id=book, spreadsheet_title="Fixture",
                                grade=7, subject="История", sheet_title="I · История", sheet_id=1)
            google.sheet_values.reset_mock()
            for attempt in range(2):
                checked = self.client.post("/api/admin/journals/connect", headers={"X-Dev-Auth": "admin-auth"},
                    json={**payload, "dry_run": True}).json()
                self.assertEqual([tab["sheet_id"] for tab in checked["tabs"]], ["2"])
                response = self.client.post("/api/admin/journals/connect", headers={"X-Dev-Auth": "admin-auth"},
                    json={**payload, "selected_sheet_ids": ["2"], "preflight_token": checked["preflight_token"]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([tab["status"] for tab in response.json()["tabs"]], ["success"])
                sources = [item for item in self.database.journal_status() if item["spreadsheet_id"] == book and item["status"] == "ready"]
                self.assertEqual(sorted(item["term_number"] for item in sources), [1, 2])
                if attempt == 0:
                    for source in sources:
                        student = next(row for row in self.database.journal_detail(source["id"])["students"] if row["display_name"] == "Student One")
                        self.assertTrue(self.database.match_journal_student(source["id"], student["id"], self.student_identity))
            self.assertEqual([call.args[1] for call in google.sheet_values.call_args_list], [f"'{title}'!A:ZZ"] * 4)
        items = self.client.get("/api/student/journals", headers={"X-Dev-Auth": "student-auth"}).json()["items"]
        visible = {item["source"]["term_number"]: item for item in items if item["source"]["subject"] == "История"}
        self.assertEqual([row["numeric_score"] for row in visible[1]["results"]], [3])
        self.assertEqual([row["numeric_score"] for row in visible[2]["results"]], [2, 4])
        self.assertEqual([row["numeric_value"] for row in visible[2]["summaries"]], [4])
        self.assertEqual([row["category"] for row in visible[2]["assessments"]], ["О", "П"])
        admin = self.client.get(f"/api/admin/journals/{visible[2]['source']['source_id']}/detail",
                                headers={"X-Dev-Auth": "admin-auth"}).json()
        self.assertEqual([(row["category"], row["weight"]) for row in admin["assessments"]], [("О", 2), ("П", 1)])
        with self.assertRaisesRegex(ValueError, "Период вкладки"):
            sync_journal_values(self.database, second, spreadsheet_id=book, spreadsheet_title="Fixture",
                                grade=7, subject="История", sheet_title=title, sheet_id=1)

    def test_blank_student_group_and_conflicting_result_group_fail_closed(self):
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE journal_students SET group_marker='' WHERE source_id=? AND identity_id=?",
                                  (self.source_ids[0], self.student_identity))
            self.database.execute(connection, "UPDATE journal_results SET group_marker='Other' WHERE identity_id=?",
                                  (self.student_identity,))
        items = self.client.get("/api/student/journals", headers={"X-Dev-Auth": "student-auth"}).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"]["source_id"], self.source_ids[1])
        self.assertEqual(items[0]["results"], [])

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

    def test_excluded_source_stays_in_admin_history_but_is_hidden_from_role_views(self):
        excluded_source = self.source_ids[1]
        self.database.set_journal_source_mode(excluded_source, "excluded")

        student_items = self.client.get(
            "/api/student/journals", headers={"X-Dev-Auth": "student-auth"}
        ).json()["items"]
        self.assertEqual({item["source"]["source_id"] for item in student_items}, {self.source_ids[0]})

        teacher_items = self.client.get(
            "/api/teacher/journals", headers={"X-Dev-Auth": "teacher-auth"}
        ).json()["items"]
        self.assertEqual({item["source_id"] for item in teacher_items}, {self.source_ids[0]})
        self.assertFalse(self.database.teacher_can_access_journal_marker(self.teacher_user, excluded_source, "A"))
        self.assertIsNone(self.database.teacher_journal_detail(self.teacher_user, excluded_source, "A"))
        self.assertEqual(self.database.list_group_journal(self.group_ids[-1]), [])

        admin_detail = self.database.journal_detail(excluded_source)
        self.assertIsNotNone(admin_detail)
        self.assertTrue(admin_detail["source"]["excluded_from_journals"])
        self.assertTrue(admin_detail["assessments"])

    def test_excluded_journal_is_preserved_and_reappears_after_manual_enable(self):
        deferred_source = self.source_ids[1]
        with self.database.connection() as connection:
            self.database.execute(connection, """UPDATE journal_sources
                SET grade = 7, subject = 'Математика', term_number = 2
                WHERE id = ?""", (deferred_source,))
        self.database.set_journal_source_mode(deferred_source, "excluded")

        student_items = self.client.get(
            "/api/student/journals", headers={"X-Dev-Auth": "student-auth"}
        ).json()["items"]
        self.assertEqual({item["source"]["source_id"] for item in student_items}, {self.source_ids[0]})

        teacher_items = self.client.get(
            "/api/teacher/journals", headers={"X-Dev-Auth": "teacher-auth"}
        ).json()["items"]
        self.assertEqual({item["source_id"] for item in teacher_items}, {self.source_ids[0]})
        self.assertFalse(self.database.teacher_can_access_journal_marker(self.teacher_user, deferred_source, "A"))
        self.assertIsNone(self.database.teacher_journal_detail(self.teacher_user, deferred_source, "A"))
        self.assertEqual(self.database.list_group_journal(self.group_ids[-1]), [])

        admin_detail = self.database.journal_detail(deferred_source)
        self.assertIsNotNone(admin_detail)
        self.assertEqual(admin_detail["source"]["term_number"], 2)
        self.assertEqual(admin_detail["source"]["subject"], "Математика")
        self.assertTrue(admin_detail["assessments"])
        self.database.set_journal_source_mode(deferred_source, "manual")
        revived = self.client.get("/api/student/journals", headers={"X-Dev-Auth": "student-auth"}).json()["items"]
        self.assertIn(deferred_source, {item["source"]["source_id"] for item in revived})
        self.assertEqual(self.database.journal_detail(deferred_source)["assessments"], admin_detail["assessments"])

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
