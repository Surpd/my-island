import tempfile
import unittest
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from backend.database import Database
from backend.handlers.api import create_router
from backend.config import Settings
from backend.services.google_live import GoogleTokenStore


class AdminConsoleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "admin.db")
        self.database.initialize()
        with self.database.connection() as connection:
            user = self.database.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id", (991001,)).fetchone()
            self.user_id = user["id"]
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (self.user_id,))

    def tearDown(self):
        self.temp.cleanup()

    def test_local_schema_reports_current_and_supports_additive_columns(self):
        health = self.database.system_health()
        self.assertEqual(health["schema_status"], "current")
        self.assertTrue(health["schema"]["groups"]["ok"])
        self.assertTrue(health["schema"]["schedule_entries"]["ok"])

    def test_browser_code_is_single_use_and_session_is_revocable(self):
        code, expires_at = self.database.create_admin_login_challenge(self.user_id)
        self.assertTrue(expires_at)
        session_token, user_id = self.database.consume_admin_login_challenge(code)
        self.assertEqual(user_id, self.user_id)
        self.assertIsNotNone(self.database.get_admin_browser_session(session_token))
        self.assertIsNone(self.database.consume_admin_login_challenge(code))
        self.database.revoke_admin_browser_session(session_token)
        self.assertIsNone(self.database.get_admin_browser_session(session_token))

    def test_reconciliation_run_requires_explicit_review(self):
        run_id = self.database.create_reconciliation_run(self.user_id, {"summary": {"CREATE": 0}, "mode": "read_only"})
        run = self.database.get_reconciliation_run(run_id)
        self.assertEqual(run["status"], "ready_for_review")
        reviewed = self.database.mark_reconciliation_reviewed(run_id, self.user_id)
        self.assertEqual(reviewed["status"], "approved")

    def test_admin_group_collection_route_is_not_captured_by_detail_route(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(create_router(self.database))
        response = TestClient(app).get("/api/admin/groups", headers={"X-Dev-Auth": "admin:1"})
        self.assertNotEqual(response.status_code, 404)

    def test_admin_student_schedule_route_is_available(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(create_router(self.database))
        response = TestClient(app).get(
            "/api/admin/people/00000000-0000-0000-0000-000000000000/schedule?start_day=2026-09-14&end_day=2026-09-18",
            headers={"X-Dev-Auth": "admin:1"},
        )
        self.assertNotEqual(response.status_code, 405)

    def test_directory_editor_crud_uses_stable_ids_and_audits(self):
        group = self.database.create_group("math-editor", "subject_group", subject="Математика", actor_user_id=self.user_id)
        renamed = self.database.update_admin_group(group["id"], {"name": "math-editor-renamed", "display_name": "Математика A"}, self.user_id)
        self.assertEqual(renamed["id"], group["id"])
        identity = self.database.create_identity("teacher", "Учитель Тест", actor_user_id=self.user_id)
        updated = self.database.update_admin_identity(identity["id"], {"display_name": "Учитель Тест 2"}, self.user_id)
        self.assertEqual(updated["id"], identity["id"])
        with self.database.connection() as connection:
            audit = self.database.execute(connection, "SELECT action FROM audit_log ORDER BY id").fetchall()
        self.assertEqual([row["action"] for row in audit], ["group.created", "group.updated", "identity.created", "identity.updated"])
        with self.assertRaises(ValueError):
            self.database.create_identity("teacher", "Учитель Тест 2", actor_user_id=self.user_id)

    def test_membership_transfer_preserves_imported_rows_and_hides_excluded_members(self):
        source = self.database.create_group("9-А", "class", base_class_name="9-А", actor_user_id=self.user_id)
        target = self.database.create_group("math-A", "subject_group", subject="Математика", base_class_name="9", actor_user_id=self.user_id)
        student = self.database.create_identity("student", "Тестовый ученик", "9-А", actor_user_id=self.user_id)
        imported = self.database.create_membership(source["id"], student["id"], "student", "official_import", "source:row:1")
        self.database.update_student_memberships(student["id"], source["id"], target["id"], self.user_id, "Перевод")
        with self.database.connection() as connection:
            preserved = self.database.execute(connection, "SELECT active FROM memberships WHERE id = ?", (imported["id"],)).fetchone()
            excluded = self.database.execute(connection, "SELECT action FROM membership_overrides WHERE identity_id = ? AND group_id = ? AND active IS TRUE", (student["id"], source["id"])).fetchone()
            included = self.database.execute(connection, "SELECT action FROM membership_overrides WHERE identity_id = ? AND group_id = ? AND active IS TRUE", (student["id"], target["id"])).fetchone()
        self.assertTrue(preserved["active"])
        self.assertEqual(excluded["action"], "exclude")
        self.assertEqual(included["action"], "include")
        groups = {item["id"]: item for item in self.database.list_groups_admin()}
        self.assertEqual(groups[source["id"]]["student_count"], 0)
        self.assertEqual(groups[target["id"]]["student_count"], 1)

    def test_grade_teacher_assignment_uses_grade_wide_scope_without_creating_groups(self):
        classes = [
            self.database.create_group(name, "class", base_class_name=name, actor_user_id=self.user_id)
            for name in ("9-А", "9-Б")
        ]
        teacher = self.database.create_identity("teacher", "Тестовый преподаватель", actor_user_id=self.user_id)
        teacher_user = self.database.get_or_create_user(992, "teacher")
        with self.database.connection() as connection:
            connection.execute("UPDATE users SET identity_id = ? WHERE id = ?", (teacher["id"], teacher_user["id"]))
        assignments = self.database.set_teacher_grade_assignments(teacher["id"], "9", "Математика", True, self.user_id)
        self.assertEqual(len(assignments), 1)
        self.assertIsNone(assignments[0]["group_id"])
        self.assertEqual(assignments[0]["grade_level"], "9")
        self.assertTrue(all(self.database.teacher_can_access_group(teacher_user["id"], group["id"]) for group in classes))
        self.assertEqual({group["name"] for group in self.database.list_groups_admin()}, {"9-А", "9-Б"})

    def test_class_change_updates_canonical_membership_and_keeps_identity_id(self):
        source = self.database.create_group("9-Г", "class", base_class_name="9-Г", actor_user_id=self.user_id)
        target = self.database.create_group("9-Ан", "class", base_class_name="9-Б", actor_user_id=self.user_id)
        student = self.database.create_identity("student", "Ученик класса", "9-Г", self.user_id)
        result = self.database.update_admin_identity(student["id"], {"class_name": "9-Б"}, self.user_id)
        self.assertEqual(result["id"], student["id"])
        self.assertEqual(result["class_name"], "9-Б")
        person = self.database.get_people_library_person(student["id"])
        self.assertEqual([item["id"] for item in person["base_classes"]], [target["id"]])
        with self.database.connection() as connection:
            imported_source_membership = self.database.execute(connection, "SELECT active FROM memberships WHERE identity_id = ? AND group_id = ? AND source = 'admin_override'", (student["id"], source["id"])).fetchone()
            audit = self.database.execute(connection, "SELECT count(*) AS value FROM audit_log WHERE action = 'identity.updated' AND entity_id = ?", (student["id"],)).fetchone()
        self.assertFalse(imported_source_membership["active"])
        self.assertEqual(audit["value"], 1)

    def test_confirmed_class_alias_resolves_to_existing_canonical_group(self):
        canonical = self.database.create_group("9-Ан", "class", base_class_name="9-Ан", actor_user_id=self.user_id)
        student = self.database.create_identity("student", "Alias ученик", "9-А", self.user_id)
        self.assertEqual(student["class_name"], "9-Ан")
        person = self.database.get_people_library_person(student["id"])
        self.assertEqual([item["id"] for item in person["base_classes"]], [canonical["id"]])
        with self.assertRaises(ValueError):
            self.database.create_group("9-А", "class", base_class_name="9-А", actor_user_id=self.user_id)

    def test_teacher_subjects_group_notes_and_defaults_are_persisted(self):
        teacher = self.database.create_identity("teacher", "Учитель с предметами", actor_user_id=self.user_id, subjects=["Математика", "Физика"], comment="Сотрудник")
        group = self.database.create_group("math-default", "subject_group", subject="Математика", comment="Группа", default_teacher_identity_id=teacher["id"], actor_user_id=self.user_id)
        self.assertEqual(self.database.list_teacher_subjects(teacher["id"]), ["Математика", "Физика"])
        self.assertEqual(self.database.get_people_library_person(teacher["id"])["comment"], "Сотрудник")
        detail = self.database.get_group_admin(group["id"])
        self.assertEqual(detail["comment"], "Группа")
        self.assertEqual(detail["default_teacher_identity_id"], teacher["id"])
        self.assertEqual(detail["default_teacher_name"], "Учитель с предметами")
        self.assertEqual(detail["schedule_usage_count"], 0)
        # Removing a subject from the profile list must not erase its default
        # assignment, and case/whitespace variants must not create duplicates.
        self.database.update_admin_identity(teacher["id"], {"subjects": ["  физика  ", "ФИЗИКА", "Русский   язык"]}, self.user_id)
        self.assertEqual(self.database.list_teacher_subjects(teacher["id"]), ["Математика", "Русский язык", "физика"])
        with self.database.connection() as connection:
            subject_rows = self.database.execute(connection, "SELECT subject FROM teacher_subjects WHERE teacher_identity_id = ? AND active IS TRUE", (teacher["id"],)).fetchall()
        self.assertEqual(len([row for row in subject_rows if " ".join(row["subject"].split()).casefold() == "физика"]), 1)
        with self.database.connection() as connection:
            preserved_assignment = self.database.execute(connection, "SELECT active FROM teacher_assignments WHERE teacher_identity_id = ? AND group_id = ? AND subject = 'Математика'", (teacher["id"], group["id"])).fetchone()
        self.assertTrue(preserved_assignment["active"])
        provenance_group = self.database.create_group("math-provenance", "subject_group", subject="Математика", actor_user_id=self.user_id)
        imported_assignment = self.database.set_teacher_assignment(teacher["id"], provenance_group["id"], "Математика", True, self.user_id)
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE teacher_assignments SET source = 'official_import', source_ref = 'unit-test:teacher-directory' WHERE id = ?", (imported_assignment["id"],))
        self.database.deactivate_teacher_assignment(imported_assignment["id"], teacher["id"], self.user_id)
        with self.database.connection() as connection:
            removed_assignment = self.database.execute(connection, "SELECT active, source, source_ref FROM teacher_assignments WHERE id = ?", (imported_assignment["id"],)).fetchone()
        self.assertFalse(removed_assignment["active"])
        self.assertEqual((removed_assignment["source"], removed_assignment["source_ref"]), ("official_import", "unit-test:teacher-directory"))
        self.database.update_admin_group(group["id"], {"subject": "Физика", "default_teacher_identity_id": teacher["id"]}, self.user_id)
        self.assertEqual(self.database.get_group_admin(group["id"])["default_teacher_identity_id"], teacher["id"])
        with self.database.connection() as connection:
            old_default = self.database.execute(connection, "SELECT active FROM teacher_assignments WHERE group_id = ? AND subject = 'Математика' AND source = 'admin_override'", (group["id"],)).fetchone()
        self.assertFalse(old_default["active"])
        self.database.update_admin_group(group["id"], {"subject": "", "default_teacher_identity_id": None}, self.user_id)
        self.assertIsNone(self.database.get_group_admin(group["id"])["default_teacher_identity_id"])

    def test_editor_api_writes_through_admin_session(self):
        from fastapi import FastAPI

        code, _ = self.database.create_admin_login_challenge(self.user_id)
        token, _ = self.database.consume_admin_login_challenge(code)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{token}"}
        class_a = client.post("/api/admin/groups", headers=headers, json={"name": "9-А", "group_type": "class", "base_class_name": "9-А"})
        class_b = client.post("/api/admin/groups", headers=headers, json={"name": "9-Б", "group_type": "class", "base_class_name": "9-Б"})
        self.assertEqual(class_a.status_code, 200)
        self.assertEqual(class_b.status_code, 200)
        student = client.post("/api/admin/identities", headers=headers, json={"display_name": "API ученик", "class_name": "9-А", "kind": "student"})
        self.assertEqual(student.status_code, 200, student.text)
        changed = client.patch(f"/api/admin/people/{student.json()['id']}", headers=headers, json={"class_name": "9-Б"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["class_name"], "9-Б")
        teacher = client.post("/api/admin/identities", headers=headers, json={"display_name": "API преподаватель", "kind": "teacher", "subjects": ["Математика"], "comment": "Куратор"})
        self.assertEqual(teacher.status_code, 200, teacher.text)
        group = client.post("/api/admin/groups", headers=headers, json={"name": "math-api", "group_type": "subject_group", "subject": "Математика", "comment": "API группа", "default_teacher_identity_id": teacher.json()["id"]})
        self.assertEqual(group.status_code, 200, group.text)
        self.assertEqual(group.json()["comment"], "API группа")
        detail = client.get(f"/api/admin/groups/{group.json()['id']}", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["default_teacher_identity_id"], teacher.json()["id"])
        assignment = client.post("/api/admin/teacher-assignments", headers=headers, json={"teacher_identity_id": str(teacher.json()["id"]), "group_id": str(group.json()["id"]), "subject": "Математика"})
        self.assertEqual(assignment.status_code, 200, assignment.text)
        other_group = client.post("/api/admin/groups", headers=headers, json={"name": "physics-api", "group_type": "subject_group", "subject": "Физика"})
        self.assertEqual(other_group.status_code, 200, other_group.text)
        moved = client.post("/api/admin/teacher-assignments", headers=headers, json={"teacher_identity_id": str(teacher.json()["id"]), "assignment_id": str(assignment.json()["id"]), "group_id": str(other_group.json()["id"]), "subject": "Физика", "active": True})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertNotEqual(moved.json()["id"], assignment.json()["id"])
        removed = client.post("/api/admin/teacher-assignments", headers=headers, json={"teacher_identity_id": str(teacher.json()["id"]), "assignment_id": str(moved.json()["id"]), "subject": "Физика", "active": False})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["active"])
        duplicate = client.post("/api/admin/groups", headers=headers, json={"name": "math-api", "display_name": "Different label", "group_type": "subject_group", "subject": "Математика"})
        self.assertEqual(duplicate.status_code, 400)

    def test_google_reconnect_requires_admin_and_uses_one_time_callback(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        self.assertEqual(client.post("/api/admin/google/reconnect").status_code, 401)
        code, _ = self.database.create_admin_login_challenge(self.user_id)
        session_token, _ = self.database.consume_admin_login_challenge(code)
        headers = {"X-Dev-Auth": f"browser:{session_token}"}
        key = Fernet.generate_key().decode("ascii")
        settings = Settings(
            app_env="development", dev_auth_enabled=True, telegram_bot_token="", database_url=None,
            database_path="data/test.db", cors_origins=("http://localhost:3000",),
            backend_public_url="http://localhost:8000", frontend_public_url="http://localhost:3000",
            google_sheets_spreadsheet_id="sheet-1", google_classroom_course_id=None,
            google_oauth_client_id="client", google_oauth_client_secret="secret",
            google_oauth_redirect_uri="http://localhost:8000/api/integrations/google/callback",
            google_oauth_refresh_token=None, google_oauth_token_encryption_key=key,
        )
        token = {"access_token": "access", "refresh_token": "refresh-secret", "scope": "openid email https://www.googleapis.com/auth/spreadsheets.readonly", "expires_at": int(time.time()) + 3600}
        with patch("backend.handlers.api.get_settings", return_value=settings), \
             patch("backend.handlers.api.exchange_code", return_value=token), \
             patch("backend.handlers.api.GoogleLiveClient.userinfo", return_value={"email": "antischool.island@gmail.com"}), \
             patch("backend.handlers.api.GoogleLiveClient.spreadsheet", return_value={"properties": {"title": "Расписание"}}):
            started = client.post("/api/admin/google/reconnect", headers=headers)
            self.assertEqual(started.status_code, 200)
            authorization = urlparse(started.json()["authorization_url"])
            state = parse_qs(authorization.query)["state"][0]
            self.assertIn("/api/integrations/google/callback", parse_qs(authorization.query)["redirect_uri"][0])
            callback = client.get("/api/integrations/google/callback", params={"state": state, "code": "one-time-code"})
            self.assertEqual(callback.status_code, 200)
            self.assertIn("Google подключён", callback.text)
            stored = self.database.get_google_oauth_credential()
            self.assertNotIn("refresh-secret", stored["token_ciphertext"])
            self.assertEqual(GoogleTokenStore(database=self.database, encryption_key=key).load()["refresh_token"], "refresh-secret")
            replay = client.get("/api/integrations/google/callback", params={"state": state, "code": "replay"})
            self.assertEqual(replay.status_code, 400)


if __name__ == "__main__":
    unittest.main()
