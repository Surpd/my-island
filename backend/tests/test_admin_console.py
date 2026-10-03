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
