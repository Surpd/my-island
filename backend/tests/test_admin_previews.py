import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import _preview_data_source, create_router


class AdminPreviewRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "preview.db")
        self.database.initialize()
        with self.database.connection() as connection:
            self.admin_id = self.database.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (88001, 'admin') RETURNING id").fetchone()["id"]
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (self.admin_id,))
            self.other_id = self.database.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (88002, 'student') RETURNING id").fetchone()["id"]
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'student')", (self.other_id,))
        self.app = FastAPI()
        self.app.include_router(create_router(self.database))
        self.client = TestClient(self.app)
        challenge, _ = self.database.create_admin_login_challenge(self.admin_id)
        self.token, _ = self.database.consume_admin_login_challenge(challenge)
        self.headers = {"X-Dev-Auth": f"browser:{self.token}"}

    def tearDown(self):
        self.temp.cleanup()

    def start_preview(self, kind):
        with self.database.connection() as connection:
            has_class = self.database.execute(connection, "SELECT 1 FROM groups WHERE name='9-А' LIMIT 1").fetchone()
        if not has_class:
            self.database.create_group("9-А", "class", base_class_name="9-А", actor_user_id=self.admin_id)
        target = self.database.create_identity(kind, f"Canonical {kind}", "9-А" if kind == "student" else None, actor_user_id=self.admin_id)
        response = self.client.post("/api/admin/previews", headers=self.headers, json={"target_identity_id": str(target["id"]), "role": kind})
        self.assertEqual(response.status_code, 200, response.text)
        return target, response.json()

    def test_brower_admin_can_preview_accountless_student_and_teacher(self):
        for kind in ("student", "teacher"):
            target, preview = self.start_preview(kind)
            self.assertEqual(preview["role"], kind)
            self.assertEqual(preview["target"]["identity_id"], str(target["id"]))
            self.assertIsNone(self.database.get_user_by_id(f"identity:{target['id']}"))
            self.assertEqual(self.client.get(f"/api/admin/previews/{preview['id']}/diagnostics", headers=self.headers).status_code, 200)
            if kind == "teacher":
                self.assertEqual(self.client.get("/api/teacher/home", headers={**self.headers, "X-Preview-Id": preview["id"]}).status_code, 200)

    def test_preview_people_directory_uses_canonical_identities(self):
        target, _ = self.start_preview("teacher")
        response = self.client.get("/api/admin/previews/people?role=teacher", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(str(target["id"]), {item["identity_id"] for item in response.json()["items"]})
        self.assertEqual(response.json()["classes"], [])

    def test_preview_people_directory_search_is_unicode_case_insensitive(self):
        target = self.database.create_identity("student", "ТЕСТовый ученик", actor_user_id=self.admin_id)
        response = self.client.get("/api/admin/previews/people?role=student&q=%D1%82%D0%B5%D1%81%D1%82%D0%BE%D0%B2%D1%8B%D0%B9", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(str(target["id"]), {item["identity_id"] for item in response.json()["items"]})

    def test_teacher_preview_catalog_and_groups_include_grade_wide_and_homeroom_assignments(self):
        teacher, preview = self.start_preview("teacher")
        with self.database.connection() as connection:
            grade_group = self.database.execute(connection, """INSERT INTO groups(name,group_type,display_name,base_class_name,canonical)
                VALUES ('9-preview','class','9 класс','9',TRUE) RETURNING id""").fetchone()["id"]
            homeroom_group = self.database.execute(connection, """INSERT INTO groups(name,group_type,display_name,base_class_name,canonical)
                VALUES ('10-preview','class','10 класс','10',TRUE) RETURNING id""").fetchone()["id"]
            student_id = self.database.execute(connection, "INSERT INTO identities(kind,display_name,class_name) VALUES ('student','Roster student','9') RETURNING id").fetchone()["id"]
            self.database.execute(connection, "INSERT INTO memberships(group_id,identity_id,member_role,source,source_ref) VALUES (?,?,'student','test','grade-wide-roster')", (grade_group, student_id))
            self.database.execute(connection, """INSERT INTO teacher_assignments(teacher_identity_id,group_id,grade_level,subject,base_class_name,active,created_by)
                VALUES (?,NULL,'9','Math','9',TRUE,?)""", (teacher["id"], self.admin_id))
            self.database.execute(connection, """INSERT INTO homeroom_assignments(teacher_identity_id,class_group_id,source,source_ref,active,created_by)
                VALUES (?,?,'test','homeroom',TRUE,?)""", (teacher["id"], homeroom_group, self.admin_id))

        catalog = self.client.get("/api/admin/previews/people?role=teacher&class_name=9", headers=self.headers)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertIn(str(teacher["id"]), {item["identity_id"] for item in catalog.json()["items"]})
        self.assertIn("9", catalog.json()["classes"])

        groups = self.client.get("/api/teacher/groups", headers={**self.headers, "X-Preview-Id": preview["id"]})
        self.assertEqual(groups.status_code, 200, groups.text)
        indexed = {str(item["id"]): item for item in groups.json()["items"]}
        self.assertEqual(indexed[str(grade_group)]["student_count"], 1)
        self.assertFalse(indexed[str(grade_group)]["is_homeroom"])
        self.assertTrue(indexed[str(homeroom_group)]["is_homeroom"])

    def test_generic_previews_fail_closed_in_local_school_modes(self):
        target, preview = self.start_preview("student")
        with patch.dict("os.environ", {"MY_ISLAND_SCHOOL_WORKSPACE": "1", "MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE": ""}, clear=False), \
                patch("backend.handlers.api.get_settings", return_value=SimpleNamespace(local_test_mode=False, database_url=None)):
            people = self.client.get("/api/admin/previews/people?role=student", headers=self.headers)
            create = self.client.post("/api/admin/previews", headers=self.headers, json={"target_identity_id": str(target["id"]), "role": "student"})
            end = self.client.post(f"/api/admin/previews/{preview['id']}/end", headers=self.headers)
            diagnostics = self.client.get(f"/api/admin/previews/{preview['id']}/diagnostics", headers=self.headers)
        for response in (people, create, end, diagnostics):
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("school directory workspace", response.json()["detail"])

        with patch.dict("os.environ", {"MY_ISLAND_SCHOOL_WORKSPACE": "", "MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE": "1"}, clear=False), \
                patch("backend.handlers.api.get_settings", return_value=SimpleNamespace(local_test_mode=False, database_url=None)):
            response = self.client.get("/api/admin/previews/people?role=student", headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_preview_data_source_requires_explicit_local_test_or_approved_database(self):
        self.assertEqual(_preview_data_source(SimpleNamespace(database_url=None), SimpleNamespace(local_test_mode=True)), "local_test")
        self.assertEqual(_preview_data_source(SimpleNamespace(database_url=None), SimpleNamespace(local_test_mode=False, database_url=None)), "unconfigured")
        approved_url = "postgresql://postgres.kmrcbewmxeqqxfauiwyy:placeholder@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"
        self.assertEqual(_preview_data_source(SimpleNamespace(database_url=approved_url), SimpleNamespace(local_test_mode=False, database_url=approved_url)), "school")
        self.assertEqual(_preview_data_source(SimpleNamespace(database_url="postgresql://postgres@other.example/db"), SimpleNamespace(local_test_mode=False, database_url="postgresql://postgres@other.example/db")), "unconfigured")
        self.assertEqual(_preview_data_source(SimpleNamespace(database_url=approved_url), SimpleNamespace(local_test_mode=False, database_url="different")), "unconfigured")

    def test_non_admin_wrong_actor_expiry_end_and_wrong_role_are_rejected(self):
        target, preview = self.start_preview("teacher")
        self.assertEqual(self.client.get("/api/admin/previews/people?role=teacher", headers={"X-Dev-Auth": "student:1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/teacher/schedule?start_day=2026-10-19", headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": preview["id"]}).status_code, 200)
        with self.database.connection() as connection:
            wrong_actor = self.database.execute(connection, "INSERT INTO student_preview_sessions(id, actor_user_id, target_user_id, created_at, expires_at) VALUES ('wrong-actor', ?, ?, CURRENT_TIMESTAMP, '2999-01-01 00:00:00') RETURNING id", (self.other_id, self.other_id)).fetchone()
            self.database.execute(connection, "INSERT INTO student_preview_targets(preview_id, identity_id) VALUES (?, ?)", (wrong_actor["id"], target["id"]))
        self.assertEqual(self.client.get("/api/teacher/schedule?start_day=2026-10-19", headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": "wrong-actor"}).status_code, 403)
        self.assertEqual(self.client.get("/api/student/journals", headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": preview["id"]}).status_code, 403)
        self.assertEqual(self.client.post("/api/student/journals", headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": preview["id"]}).status_code, 403)
        self.assertTrue(self.client.post(f"/api/admin/previews/{preview['id']}/end", headers=self.headers).json()["ended"])
        self.assertEqual(self.client.get("/api/teacher/schedule?start_day=2026-10-19", headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": preview["id"]}).status_code, 403)

    def test_expired_preview_is_rejected(self):
        target, preview = self.start_preview("student")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE student_preview_sessions SET expires_at='2000-01-01 00:00:00' WHERE id=?", (preview["id"],))
        from unittest.mock import patch
        with patch.object(self.database, "get_student_identity", side_effect=AssertionError("Expired preview must not query an empty UUID")):
            for path in ("/api/student/profile", "/api/student/journals"):
                response = self.client.get(path, headers={"X-Dev-Auth": f"browser:{self.token}", "X-Preview-Id": preview["id"]})
                self.assertEqual(response.status_code, 403)

    def test_accountless_student_preview_returns_only_that_students_journal_result(self):
        student, preview = self.start_preview("student")
        other = self.database.create_identity("student", "Other student", actor_user_id=self.admin_id)
        with self.database.connection() as connection:
            source = self.database.execute(connection, """INSERT INTO journal_sources(spreadsheet_id,spreadsheet_title,grade,subject,sheet_title,academic_year,status)
                VALUES ('preview-book','Preview book',9,'Math','Preview sheet','2026/27','ready') RETURNING id""").fetchone()["id"]
            assessment = self.database.execute(connection, """INSERT INTO journal_assessments(source_id,external_key,title,assessed_on,source_column,group_marker)
                VALUES (?, 'work','Work','2026-10-20','B','all') RETURNING id""", (source,)).fetchone()["id"]
            rows = {}
            for identity, name, score in ((student["id"], "Canonical student", 5), (other["id"], "Other student", 2)):
                journal_student = self.database.execute(connection, """INSERT INTO journal_students(source_id,student_key,display_name,group_marker,identity_id,match_status)
                    VALUES (?, ?, ?, 'all', ?, 'linked') RETURNING id""", (source, f"student-{identity}", name, identity)).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(assessment_id,journal_student_id,identity_id,student_name,group_marker,numeric_score,source_coordinate)
                    VALUES (?, ?, ?, ?, 'all', ?, ?)""", (assessment, journal_student, identity, name, score, f"B{score}"))
                rows[identity] = journal_student
        response = self.client.get("/api/student/journals", headers={**self.headers, "X-Preview-Id": preview["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "approved")
        self.assertEqual(response.json()["mapping_state"], "has_results")
        self.assertEqual([item["student"]["display_name"] for item in response.json()["items"]], ["Canonical student"])
        self.assertEqual(response.json()["items"][0]["results"][0]["numeric_score"], 5)

    def test_student_journal_endpoint_reports_mapping_state_for_empty_results(self):
        student, preview = self.start_preview("student")
        headers = {**self.headers, "X-Preview-Id": preview["id"]}

        def read_state():
            response = self.client.get("/api/student/journals", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "approved")
            self.assertTrue(all(not item["results"] for item in response.json()["items"]))
            return response.json()["mapping_state"]

        self.assertEqual(read_state(), "missing_mapping")
        with self.database.connection() as connection:
            source = self.database.execute(connection, """INSERT INTO journal_sources(spreadsheet_id,spreadsheet_title,grade,subject,sheet_title,academic_year,status)
                VALUES ('mapping-state-book','Mapping state book',9,'Math','Mapping sheet','2026/27','ready') RETURNING id""").fetchone()["id"]
            row = self.database.execute(connection, """INSERT INTO journal_students(source_id,student_key,display_name,group_marker,identity_id,match_status)
                VALUES (?, 'student', 'Canonical student', 'all', ?, 'unlinked') RETURNING id""", (source, student["id"])).fetchone()["id"]
        self.assertEqual(read_state(), "unlinked_mapping")
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE journal_students SET match_status='linked' WHERE id=?", (row,))
        self.assertEqual(read_state(), "mapped_empty")

    def test_accountless_teacher_detail_with_null_group_mapping_is_assignment_scoped(self):
        teacher, preview = self.start_preview("teacher")
        other_teacher = self.database.create_identity("teacher", "Other teacher", actor_user_id=self.admin_id)
        with self.database.connection() as connection:
            group_a = self.database.execute(connection, """INSERT INTO groups(name,group_type,base_class_name,canonical)
                VALUES ('9-А-preview','class','9-А-preview',TRUE) RETURNING id""").fetchone()["id"]
            group_b = self.database.execute(connection, """INSERT INTO groups(name,group_type,base_class_name,canonical)
                VALUES ('9-Б-preview','class','9-Б-preview',TRUE) RETURNING id""").fetchone()["id"]
            students = []
            for group_id, class_name, name in ((group_a, "9-А", "Assigned student"), (group_b, "9-Б", "Other assignee student")):
                identity = self.database.execute(connection, "INSERT INTO identities(kind,display_name,class_name) VALUES ('student',?,?) RETURNING id", (name, class_name)).fetchone()["id"]
                self.database.execute(connection, "INSERT INTO memberships(group_id,identity_id,member_role,source,source_ref) VALUES (?,?,'student','test','membership')", (group_id, identity))
                students.append((identity, name))
            self.database.execute(connection, """INSERT INTO teacher_assignments(teacher_identity_id,group_id,subject,subject_subgroup,active,created_by)
                VALUES (?,?,'Math','A',TRUE,?), (?,?,'Math','B',TRUE,?)""",
                (teacher["id"], group_a, self.admin_id, other_teacher["id"], group_b, self.admin_id))
            source = self.database.execute(connection, """INSERT INTO journal_sources(spreadsheet_id,spreadsheet_title,grade,subject,sheet_title,academic_year,status)
                VALUES ('teacher-preview-book','Teacher preview book',9,'Math','Teacher preview sheet','2026/27','ready') RETURNING id""").fetchone()["id"]
            assessment = self.database.execute(connection, """INSERT INTO journal_assessments(source_id,external_key,title,assessed_on,source_column,group_marker)
                VALUES (?, 'work','Work','2026-10-20','B','A') RETURNING id""", (source,)).fetchone()["id"]
            self.database.execute(connection, """INSERT INTO journal_group_mappings(source_id,group_marker,group_id,base_class_name,subject_subgroup)
                VALUES (?, 'A', NULL, '9-А', 'A')""", (source,))
            for identity, name in students:
                marker = "A" if name == "Assigned student" else "B"
                journal_student = self.database.execute(connection, """INSERT INTO journal_students(source_id,student_key,display_name,group_marker,identity_id,match_status)
                    VALUES (?, ?, ?, ?, ?, 'linked') RETURNING id""", (source, f"student-{identity}", name, marker, identity)).fetchone()["id"]
                self.database.execute(connection, """INSERT INTO journal_results(assessment_id,journal_student_id,identity_id,student_name,group_marker,numeric_score,source_coordinate)
                    VALUES (?, ?, ?, ?, ?, 4, ?)""", (assessment, journal_student, identity, name, marker, f"{marker}-1"))
        permitted = self.client.get(f"/api/teacher/journals/{source}/A", headers={**self.headers, "X-Preview-Id": preview["id"]})
        denied = self.client.get(f"/api/teacher/journals/{source}/B", headers={**self.headers, "X-Preview-Id": preview["id"]})
        self.assertEqual(permitted.status_code, 200, permitted.text)
        self.assertEqual([item["display_name"] for item in permitted.json()["students"]], ["Assigned student"])
        self.assertEqual(denied.status_code, 403)

    def test_admin_auth_session_accepts_signed_telegram_header_and_logout_browser_fallback(self):
        session = self.client.get("/api/admin/auth/session", headers={"X-Dev-Auth": f"browser:{self.token}"})
        self.assertEqual(session.status_code, 200)
        self.assertIn("admin", session.json()["user"]["roles"])
        with patch("backend.handlers.api.resolve_auth", return_value={"id": 88001}):
            telegram_session = self.client.get("/api/admin/auth/session", headers={"X-Telegram-Init-Data": "signed-init-data"})
        self.assertEqual(telegram_session.status_code, 200)
        self.assertEqual(telegram_session.json()["mode"], "telegram")
        logout = self.client.post("/api/admin/auth/logout", headers={"X-Dev-Auth": f"browser:{self.token}"})
        self.assertEqual(logout.status_code, 200)
        self.assertIsNone(self.database.get_admin_browser_session(self.token))

    def test_telegram_role_selection_and_admin_claim_do_not_grant_privileged_roles(self):
        target = self.database.create_identity("student", "Claim target", actor_user_id=self.admin_id)
        token = "role-selection-test-token"

        def signed_init_data(telegram_id):
            fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": telegram_id})}
            check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
            secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
            fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)

        test_auth_env = {"APP_ENV": "development", "DEV_AUTH_ENABLED": "true", "TELEGRAM_BOT_TOKEN": token,
                         "TELEGRAM_BOOTSTRAP_USER_IDS": "", "MY_ISLAND_LOCAL_TEST_MODE": "false",
                         "DATABASE_URL": "", "DATABASE_PATH": str(self.database.path)}
        with patch.dict("os.environ", test_auth_env):
            admin_selection = self.client.post("/api/auth/session", headers={"X-Telegram-Init-Data": signed_init_data(88003)}, json={"role": "admin"})
        self.assertEqual(admin_selection.status_code, 200, admin_selection.text)
        actor = self.database.find_user(88003)
        self.assertEqual(self.database.list_user_roles(actor["id"]), ["student"])
        self.assertNotIn("admin", admin_selection.json()["user"]["roles"])

        with patch.dict("os.environ", test_auth_env):
            teacher_selection = self.client.post("/api/auth/session", headers={"X-Telegram-Init-Data": signed_init_data(88004)}, json={"role": "teacher"})
        self.assertEqual(teacher_selection.status_code, 200, teacher_selection.text)
        teacher_actor = self.database.find_user(88004)
        self.assertEqual(self.database.list_user_roles(teacher_actor["id"]), ["student"])
        self.assertNotIn("teacher", teacher_selection.json()["user"]["roles"])

        with patch.dict("os.environ", test_auth_env):
            claim = self.client.post("/api/identity/claim", headers={"X-Telegram-Init-Data": signed_init_data(88005)}, json={"identity_id": str(target["id"]), "role": "admin"})
        self.assertEqual(claim.status_code, 400)
        claim_actor = self.database.find_user(88005)
        self.assertEqual(self.database.list_user_roles(claim_actor["id"]), ["student"])


if __name__ == "__main__":
    unittest.main()
