import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urlencode

from backend.database import Database
from backend.config import Settings
from backend.services.auth import AuthError, resolve_auth, verify_telegram_init_data
from backend.services.google_live import GoogleTokenStore
from backend.services.google_classroom_sync import sync_classroom_course
from backend.services.identity import claim_identity, review_identity_claim
from backend.services.google_sheets import parse_school_schedule_values, sheet_values_to_rows
from backend.services.google_oauth import build_google_authorization_url
from backend.services.schedule_import import sync_schedule, sync_schedule_with_stats


class AuthTests(unittest.TestCase):
    def test_dev_auth_requires_explicit_flag_and_header(self):
        os.environ["APP_ENV"] = "development"
        os.environ["DEV_AUTH_ENABLED"] = "true"
        self.assertTrue(resolve_auth(None, "student:1")["dev"])
        with self.assertRaises(AuthError):
            resolve_auth(None, "student:2")

    def test_production_never_accepts_dev_header(self):
        os.environ["APP_ENV"] = "production"
        os.environ["DEV_AUTH_ENABLED"] = "true"
        with self.assertRaises(AuthError):
            resolve_auth(None, "student:1")
        os.environ["APP_ENV"] = "development"

    def test_signed_init_data(self):
        token = "test-token"
        fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 42})}
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(verify_telegram_init_data(urlencode(fields), token)["id"], 42)

    def test_google_consent_prompt_is_explicit_only(self):
        settings = Settings("development", True, "", None, "data/test.db", ("http://localhost:3000",), None, None, "sheet", None, "client", "secret", "http://localhost:8000/api/integrations/google/callback", None)
        self.assertNotIn("prompt=consent", build_google_authorization_url(settings, "state"))
        self.assertIn("prompt=consent", build_google_authorization_url(settings, "state", prompt="consent"))

    def test_google_token_store_round_trips_refresh_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoogleTokenStore(os.path.join(directory, "google-token.json"))
            store.save({"access_token": "access", "refresh_token": "refresh", "expires_at": 1})
            self.assertTrue(store.has_refresh_token())
            self.assertEqual(store.load()["refresh_token"], "refresh")

    def test_roles_are_additive_and_preview_is_actor_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            admin = database.get_or_create_user(700, "student")
            database.ensure_bootstrap_roles(700, ("teacher", "admin"))
            self.assertEqual(set(database.list_user_roles(admin["id"])), {"teacher", "admin"})
            with database.connection() as connection:
                identity = connection.execute("INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Preview Student', '9-Д') RETURNING id").fetchone()[0]
                student = connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (701, 'student', ?) RETURNING id", (identity,)).fetchone()[0]
            database.ensure_bootstrap_roles(701, ("student",))
            preview = database.create_student_preview(admin["id"], student, "2099-01-01 10:00:00", "2099-01-01 11:00:00")
            self.assertIsNotNone(database.get_active_student_preview(preview["id"], admin["id"]))
            self.assertIsNone(database.get_active_student_preview(preview["id"], student))
            self.assertTrue(database.end_student_preview(preview["id"], admin["id"]))
            self.assertIsNone(database.get_active_student_preview(preview["id"], admin["id"]))


class ScheduleTests(unittest.TestCase):
    def test_sheet_headers_are_normalized_deterministically(self):
        values = [["Дата", "Время", "Предмет", "Кабинет"], ["2026-09-05", "10:55", "Математика", "17"]]
        self.assertEqual(sheet_values_to_rows(values), [{"date": "2026-09-05", "start_time": "10:55", "subject": "Математика", "room": "17"}])

    def test_sheet_without_required_columns_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            sheet_values_to_rows([["Дата", "Предмет"], ["2026-09-05", "Математика"]])

    def test_failed_import_keeps_last_valid_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            valid = [{"date": "2026-09-05", "start_time": "10:55", "subject": "Математика"}]
            self.assertEqual(sync_schedule(database, valid), 1)
            with self.assertRaises(ValueError):
                sync_schedule(database, [{"date": "not-a-date", "start_time": "10:55", "subject": "Физика"}])
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schedule_entries").fetchone()[0], 1)

    def test_school_matrix_parser_uses_date_time_and_class_headers(self):
        values = [
            ["", "9-C", "9-D"],
            ["07.09", "Пн", "Пн"],
            ["9:00 - 9:45", "Математика\nДмитрий\nкаб.17", ""],
        ]
        rows = parse_school_schedule_values(values, sheet_title="7-11 Сентября", spreadsheet_title="Расписание 2026/27")
        self.assertEqual(rows[0]["date"], "2026-09-07")
        self.assertEqual(rows[0]["audience"], "9-C")
        self.assertEqual(rows[0]["teacher"], "Дмитрий")
        self.assertEqual(rows[0]["room"], "каб.17")

    def test_school_matrix_inherits_blank_columns_and_keeps_subgroup_separate(self):
        values = [
            ["", "9-А", "", "9-Д", ""],
            ["07.09", "Пн", "Пн", "Пн", "Пн"],
            ["9:00 - 9:45", "Русский ЕВ\nкаб.6", "Матем С ДФ\nкаб.10", "История Анна\nкаб.8", "Инфор ОГЭ Тарас\nкаб.10"],
        ]
        rows = parse_school_schedule_values(values, sheet_title="7-11 Сентября", spreadsheet_title="Расписание 2026/27")
        self.assertEqual([row["audience"] for row in rows], ["9-А", "9-А", "9-Д", "9-Д"])
        self.assertEqual(rows[1]["subject"], "Математика")
        self.assertEqual(rows[1]["subject_subgroup"], "C")
        self.assertEqual(rows[3]["audience"], "9-Д")
        self.assertEqual(rows[3]["exam_track"], "ОГЭ")

    def test_student_schedule_scope_is_group_and_subgroup_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            with database.connection() as connection:
                identity = connection.execute("INSERT INTO identities(kind, display_name, class_name) VALUES ('student', 'Student One', '9-Д') RETURNING id").fetchone()[0]
                user = connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (501, 'student', ?) RETURNING id", (identity,)).fetchone()[0]
            base = database.create_group("9-Д", "class")
            subgroup = database.create_group("9-C Математика", "subject_subgroup")
            other = database.create_group("9-А", "class")
            database.create_membership(base["id"], identity, "student", "admin_override")
            database.create_membership(subgroup["id"], identity, "student", "admin_override")
            database.map_group_schedule_audience(base["id"], "9-Д")
            database.map_group_schedule_audience(subgroup["id"], "9-Д", subject="Математика", subject_subgroup="C")
            database.map_group_schedule_audience(other["id"], "9-А")
            sync_schedule(database, [
                {"date": "2026-09-07", "start_time": "09:00", "subject": "Русский язык", "audience": "9-Д"},
                {"date": "2026-09-07", "start_time": "10:00", "subject": "Математика", "audience": "9-Д", "subject_subgroup": "C"},
                {"date": "2026-09-07", "start_time": "11:00", "subject": "Математика", "audience": "9-Д", "subject_subgroup": "B"},
                {"date": "2026-09-07", "start_time": "12:00", "subject": "Русский язык", "audience": "9-А"},
            ])
            rows = database.list_schedule_entries_for_user(user, "2026-09-07")
            self.assertEqual([(row["start_time"], row["subject_subgroup"]) for row in rows], [("09:00", ""), ("10:00", "C")])
            profile = database.get_student_profile(user)
            scopes = {group["name"]: group["scope_kind"] for group in profile["groups"]}
            self.assertEqual(scopes, {"9-C Математика": "subject_subgroup", "9-Д": "base_class"})

    def test_incremental_schedule_stats_reuse_change_and_remove_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            base = [
                {"date": "2026-09-07", "start_time": "09:00", "subject": "Русский язык", "audience": "9-Д", "raw_source": {"sheet": "week", "coordinate": "M3", "cell": "Русский"}},
                {"date": "2026-09-07", "start_time": "10:00", "subject": "Математика", "audience": "9-Д", "raw_source": {"sheet": "week", "coordinate": "N3", "cell": "Матем С"}},
            ]
            self.assertEqual(sync_schedule_with_stats(database, base)["new"], 2)
            self.assertEqual(sync_schedule_with_stats(database, base)["unchanged"], 2)
            changed = [dict(base[0], subject="История"), base[1]]
            stats = sync_schedule_with_stats(database, changed)
            self.assertEqual((stats["changed"], stats["unchanged"], stats["removed"]), (1, 1, 0))
            stats = sync_schedule_with_stats(database, [changed[0]])
            self.assertEqual(stats["removed"], 1)


class ClassroomSyncTests(unittest.TestCase):
    def test_classroom_sync_is_idempotent_and_keeps_materials_separate(self):
        class FakeClient:
            def coursework(self, course_id):
                return [{"id": "cw-1", "title": "Домашнее задание", "materials": [{"link": {"url": "https://example.test/task"}}], "state": "PUBLISHED", "maxPoints": 5}]

            def coursework_materials(self, course_id):
                return [{"id": "material-1", "title": "Памятка", "materials": [{"link": {"url": "https://example.test/guide", "text": "Гайд"}}]}]

            def student_submissions(self, course_id, coursework_id):
                return [{"id": "submission-1", "userId": "student-1", "state": "TURNED_IN", "assignedGrade": 5}]

        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            course = {"id": "course-1", "name": "9-C Математика", "updateTime": "2026-09-05T10:00:00Z"}
            with database.connection() as connection:
                identity_id = connection.execute("INSERT INTO identities(kind, display_name) VALUES ('student', 'Student One') RETURNING id").fetchone()[0]
            first = sync_classroom_course(database, FakeClient(), course, teacher_account="teacher@example.test", identity_map={"student-1": identity_id})
            second = sync_classroom_course(database, FakeClient(), course, teacher_account="teacher@example.test", identity_map={"student-1": identity_id})
            self.assertEqual(first, second)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM classroom_courses").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM classroom_coursework").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM classroom_coursework_materials").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM classroom_student_submissions").fetchone()[0], 1)

    def test_unmapped_submission_is_audited_without_exposing_student_data(self):
        class FakeClient:
            def coursework(self, course_id):
                return [{"id": "cw-1", "title": "Домашнее задание", "materials": []}]

            def coursework_materials(self, course_id):
                return []

            def student_submissions(self, course_id, coursework_id):
                return [{"id": "submission-unknown", "userId": "google-user-unknown", "state": "TURNED_IN"}]

        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            counts = sync_classroom_course(
                database,
                FakeClient(),
                {"id": "course-1", "name": "9-C Математика"},
                teacher_account="teacher@example.test",
                identity_map={},
            )
            self.assertEqual(counts["unmapped_submissions"], 1)
            self.assertEqual(counts["student_submissions"], 0)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM classroom_student_submissions").fetchone()[0], 0)
                audit = connection.execute("SELECT action, details FROM audit_log WHERE action = 'classroom_submission.unmapped'").fetchone()
                self.assertIsNotNone(audit)
                self.assertIn("submission-unknown", audit[1])

    def test_classroom_read_failure_preserves_previous_snapshot(self):
        class WorkingClient:
            def coursework(self, course_id):
                return [{"id": "cw-1", "title": "Старое задание", "materials": []}]

            def coursework_materials(self, course_id):
                return []

            def student_submissions(self, course_id, coursework_id):
                return []

        class FailingClient(WorkingClient):
            def coursework(self, course_id):
                raise RuntimeError("temporary Classroom outage")

        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            course = {"id": "course-1", "name": "9-C Математика"}
            sync_classroom_course(database, WorkingClient(), course, teacher_account="teacher@example.test")
            with self.assertRaisesRegex(RuntimeError, "temporary Classroom outage"):
                sync_classroom_course(database, FailingClient(), course, teacher_account="teacher@example.test")
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT title FROM classroom_coursework").fetchone()[0], "Старое задание")

    def test_identity_approval_and_conflict_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            with database.connection() as connection:
                identity_id = connection.execute("INSERT INTO identities(kind, display_name) VALUES ('student', 'Алексей Петров') RETURNING id").fetchone()[0]
                first_user = connection.execute("INSERT INTO users(telegram_user_id, role) VALUES (101, 'student') RETURNING id").fetchone()[0]
                second_user = connection.execute("INSERT INTO users(telegram_user_id, role) VALUES (202, 'student') RETURNING id").fetchone()[0]
                admin = connection.execute("INSERT INTO users(telegram_user_id, role) VALUES (303, 'admin') RETURNING id").fetchone()[0]
            claim = claim_identity(database, first_user, identity_id, "student")
            self.assertEqual(claim["status"], "pending")
            self.assertEqual(review_identity_claim(database, claim["id"], admin, "approved")["status"], "approved")
            with database.connection() as connection:
                link = connection.execute(
                    "SELECT status, source FROM account_identity_links WHERE user_id = ?",
                    (first_user,),
                ).fetchone()
            self.assertEqual(tuple(link), ("confirmed", "identity_claim"))
            conflict_claim = claim_identity(database, second_user, identity_id, "student")
            self.assertEqual(review_identity_claim(database, conflict_claim["id"], admin, "approved")["status"], "identity_conflict")

    def test_teacher_profile_uses_canonical_person_and_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            with database.connection() as connection:
                identity_id = connection.execute(
                    "INSERT INTO identities(kind, display_name) VALUES ('teacher', 'Дмитрий Филиппов') RETURNING id"
                ).fetchone()[0]
                user_id = connection.execute(
                    "INSERT INTO users(telegram_user_id, role, identity_id) VALUES (4242, 'teacher', ?) RETURNING id",
                    (identity_id,),
                ).fetchone()[0]
            group = database.create_group("9-1 Дмитрий", "subject_subgroup")
            database.set_teacher_assignment(identity_id, group["id"], "Математика", True, user_id, subject_subgroup="A")
            profile = database.get_teacher_profile(user_id)
            self.assertEqual(profile["user"]["display_name"], "Дмитрий Филиппов")
            self.assertEqual(profile["groups"][0]["subject"], "Математика")
            self.assertEqual(profile["groups"][0]["subject_subgroup"], "A")

    def test_official_grades_are_scoped_by_backend_user(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "test.db"))
            database.initialize()
            with database.connection() as connection:
                first_identity = connection.execute("INSERT INTO identities(kind, display_name) VALUES ('student', 'Первый') RETURNING id").fetchone()[0]
                second_identity = connection.execute("INSERT INTO identities(kind, display_name) VALUES ('student', 'Второй') RETURNING id").fetchone()[0]
                first_user = connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (401, 'student', ?) RETURNING id", (first_identity,)).fetchone()[0]
                connection.execute("INSERT INTO users(telegram_user_id, role, identity_id) VALUES (402, 'student', ?)", (second_identity,))
                connection.execute("INSERT INTO official_grades(identity_id, subject, graded_on, value, source) VALUES (?, 'Математика', '2026-09-05', 5, 'test')", (first_identity,))
                connection.execute("INSERT INTO official_grades(identity_id, subject, graded_on, value, source) VALUES (?, 'Физика', '2026-09-05', 4, 'test')", (second_identity,))
            self.assertEqual([row["subject"] for row in database.list_official_grades_for_user(first_user)], ["Математика"])

    def test_supabase_migration_does_not_assume_supabase_auth(self):
        migration = Path(__file__).parents[1].joinpath("migrations", "001_initial.sql").read_text(encoding="utf-8")
        self.assertNotIn("auth.uid", migration)
        self.assertNotIn("auth.users", migration)
        self.assertNotIn("to authenticated", migration)
        self.assertIn("from anon, authenticated", migration)


if __name__ == "__main__":
    unittest.main()
