"""The real school API must never accept the SQLite demo admin shortcut."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.handlers.school_directory_api import create_school_directory_router
from backend.database import Database
from backend.handlers.api import create_router


class SchoolDirectoryApiAuthTests(unittest.TestCase):
    def test_directory_requires_real_school_session(self):
        app = FastAPI()
        app.include_router(create_school_directory_router())
        client = TestClient(app)

        for headers in ({}, {"X-Dev-Auth": "admin:1"}):
            response = client.get("/api/admin/school/directory", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"], "Real school administrator login is required")

    def test_constructor_school_directory_rejects_demo_admin(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            with database.connection() as connection:
                user = database.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id", (991001,)).fetchone()
                database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (user["id"],))
            app = FastAPI()
            app.include_router(create_router(database))
            with patch.dict("os.environ", {"MY_ISLAND_SCHOOL_WORKSPACE": "1"}), \
                    patch("backend.handlers.api.resolve_auth", return_value={"id": 991001}):
                response = TestClient(app).get("/api/admin/schedule/experiments/school/directory", headers={"X-Dev-Auth": "admin:1"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"], "Real school administrator login is required")

    def test_school_audience_preview_reuses_the_verified_matching_browser_session(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            admin = database.get_or_create_user(991003, "admin")
            with database.connection() as connection:
                database.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','test')", (admin["id"],))
            code, _ = database.create_admin_login_challenge(admin["id"])
            token, _ = database.consume_admin_login_challenge(code)
            app = FastAPI()
            app.include_router(create_router(database))
            headers = {"X-Dev-Auth": f"browser:{token}", "X-School-Admin-Session": token}
            with patch.dict("os.environ", {"MY_ISLAND_SCHOOL_WORKSPACE": "1", "MY_ISLAND_TEST_PROFILE": ""}), \
                    patch("backend.handlers.api.school_source", return_value=database), \
                    patch("backend.handlers.api.experiment_audience_preview", return_value={"lessons": {}}), \
                    patch.object(database, "get_admin_browser_session", wraps=database.get_admin_browser_session) as session_lookup:
                response = TestClient(app).post("/api/admin/schedule/experiments/school/audience-preview",
                    headers=headers, json={"payload": {"lessons": [], "slots": []}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(session_lookup.call_count, 1)

    def test_grade_assignment_api_create_edit_and_group_creation(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            database.initialize()
            actor = database.get_or_create_user(991002, "admin")
            with database.connection() as connection:
                connection.execute("INSERT OR IGNORE INTO user_roles(user_id,role,source) VALUES (?,'admin','test')", (actor["id"],))
            teacher = database.create_identity("teacher", "Учитель Тестов")
            first_class = database.create_group("9А", "class")
            second_class = database.create_group("9Б", "class")
            group_assignment = database.set_teacher_assignment(teacher["id"], first_class["id"], "Информатика", True,
                actor["id"], base_class_name="9", subject_subgroup="ЕГЭ", exam_track="ЕГЭ")
            app = FastAPI()
            app.include_router(create_router(database))
            client = TestClient(app)
            with patch("backend.handlers.api.resolve_auth", return_value={"id": 991002}):
                created_group = client.post("/api/admin/groups", json={"name": "6А", "group_type": "class"})
                self.assertEqual(created_group.status_code, 200, created_group.text)
                created = client.post("/api/admin/teacher-assignments", json={
                    "teacher_identity_id": str(teacher["id"]), "grade_level": "6", "subject": "Математика", "exam_track": "ЕГЭ"
                })
                self.assertEqual(created.status_code, 200, created.text)
                assignment_id = str(created.json()["items"][0]["id"])
                edited = client.post("/api/admin/teacher-assignments", json={
                    "teacher_identity_id": str(teacher["id"]), "assignment_id": assignment_id,
                    "grade_level": "7", "subject": "Физика", "exam_track": "ОГЭ"
                })
                self.assertEqual(edited.status_code, 200, edited.text)
                group_edited = client.post("/api/admin/teacher-assignments", json={
                    "teacher_identity_id": str(teacher["id"]), "assignment_id": str(group_assignment["id"]),
                    "group_id": str(second_class["id"]), "subject": "Информатика", "exam_track": "ОГЭ"
                })
                self.assertEqual(group_edited.status_code, 200, group_edited.text)
            with database.connection() as connection:
                rows = connection.execute("SELECT group_id,grade_level,subject,subject_subgroup,exam_track,active FROM teacher_assignments WHERE teacher_identity_id=? ORDER BY id", (teacher["id"],)).fetchall()
            self.assertEqual([(row["grade_level"], row["subject"], row["exam_track"], bool(row["active"])) for row in rows], [
                (None, "Информатика", "ЕГЭ", False), ("6", "Математика", "ЕГЭ", False),
                ("7", "Физика", "ОГЭ", True), (None, "Информатика", "ОГЭ", True)
            ])
            self.assertEqual(rows[0]["subject_subgroup"], "ЕГЭ")
            self.assertEqual(rows[3]["subject_subgroup"], "ЕГЭ")
            self.assertEqual(rows[3]["group_id"], second_class["id"])


if __name__ == "__main__":
    unittest.main()
