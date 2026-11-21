from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.teacher_ui_colors_api import create_school_teacher_ui_colors_router
from backend.services.consolidated_workspace import require_consolidated_admin
from backend.services.teacher_ui_colors import initialize_sqlite_schema, set_manual_color


class TeacherColorSessionTests(unittest.TestCase):
    def test_only_valid_admin_sessions_can_read_or_change_palette(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "colors.sqlite3")
            db.initialize()
            initialize_sqlite_schema(db)
            admin = db.get_or_create_user(1001, "admin")
            student = db.get_or_create_user(1002, "student")
            teacher = db.create_identity("teacher", "Synthetic Leonid")
            teacher_id = str(teacher["id"])
            set_manual_color(db, "school", teacher_id, "#843FF3", 0)

            def sign_in(user_id, ttl_seconds=60):
                code, _ = db.create_admin_login_challenge(user_id)
                token, _ = db.consume_admin_login_challenge(code, ttl_seconds)
                return token

            browser = sign_in(admin["id"])
            school = sign_in(admin["id"])
            expired = sign_in(admin["id"], ttl_seconds=0)
            nonadmin = sign_in(student["id"])
            app = FastAPI()
            app.include_router(create_school_teacher_ui_colors_router(lambda: db, lambda token: require_consolidated_admin(db, token)))
            path = "/api/admin/school/teacher-ui-colors"
            with (
                patch.dict("os.environ", {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}),
                patch("backend.services.consolidated_workspace.isolated_consolidation_enabled", return_value=True),
                TestClient(app) as client,
            ):
                for token in (browser, school):
                    result = client.get(path, headers={"X-School-Admin-Session": token})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["colors"][0]["color"], "#843FF3")

                for headers in ({}, {"X-School-Admin-Session": expired, "X-Dev-Auth": f"browser:{browser}"},
                                {"X-School-Admin-Session": nonadmin}):
                    self.assertEqual(client.get(path, headers=headers).status_code, 401)
                    self.assertEqual(client.put(f"{path}/{teacher_id}", headers=headers,
                                                json={"color": "#000000", "expected_revision": 1}).status_code, 401)

                with db.connection() as conn:
                    db.execute(conn, "UPDATE admin_browser_sessions SET expires_at = ? WHERE token_hash = ?",
                               ("2000-01-01T00:00:00+00:00", db._token_hash(browser)))
                self.assertEqual(client.get(path, headers={"X-School-Admin-Session": browser}).status_code, 401)
                renewed = sign_in(admin["id"])
                response = client.get(path, headers={"X-School-Admin-Session": renewed})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["colors"][0]["manual_color"], "#843FF3")
                self.assertEqual(response.json()["colors"][0]["revision"], 1)
