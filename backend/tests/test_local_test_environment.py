from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

# Importing backend.main constructs its database at module import time. Never
# let this integration test run against the ordinary .env-selected database.
expected_db = Path(__file__).resolve().parents[2] / ".local-test" / "my-island-test.sqlite3"
if (
    os.getenv("MY_ISLAND_LOCAL_TEST_MODE", "").lower() != "true"
    or os.getenv("DATABASE_URL")
    or Path(os.getenv("DATABASE_PATH", "")).resolve() != expected_db.resolve()
):
    raise unittest.SkipTest("Run through backend.scripts.local_test_env to select the isolated SQLite fixture")

from backend.config import Settings, get_settings
from backend.main import app, database
from backend.services.auth import is_loopback_dev_auth_request


class LocalTestEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 49152))
        # This fixture is deliberately repeatable against the dedicated test DB.
        with database.connection() as connection:
            connection.execute("DELETE FROM schedule_editor_drafts WHERE draft_key='week:2026-10-12'")

    def test_dev_role_auth_is_loopback_only(self):
        self.assertTrue(is_loopback_dev_auth_request("localhost:8000", "127.0.0.1", local_test_mode=True))
        self.assertTrue(is_loopback_dev_auth_request("[::1]:8000", "::1", local_test_mode=True))
        self.assertFalse(is_loopback_dev_auth_request("localhost:8000", "127.0.0.1"))
        self.assertFalse(is_loopback_dev_auth_request("localhost:8000", "203.0.113.9", local_test_mode=True))
        self.assertFalse(is_loopback_dev_auth_request("example.com", "127.0.0.1", local_test_mode=True))
        self.assertFalse(is_loopback_dev_auth_request("localhost", "not-an-ip", local_test_mode=True))

    def test_test_profile_is_pinned_to_local_sqlite_without_integrations(self):
        settings = get_settings()
        self.assertTrue(settings.local_test_mode)
        self.assertIsNone(settings.database_url)
        db_path = Path(settings.database_path)
        self.assertEqual((db_path.parent.name, db_path.name), (".local-test", "my-island-test.sqlite3"))
        self.assertFalse(settings.telegram_bot_token)
        self.assertFalse(settings.google_oauth_client_id)
        with self.assertRaisesRegex(ValueError, "dedicated .local-test SQLite"):
            replace(settings, database_path="data/my-island.db").validate()
        with self.assertRaisesRegex(ValueError, "must not contain Telegram or Google"):
            replace(settings, google_oauth_client_secret="not-a-real-secret").validate()

    def test_public_request_cannot_use_dev_role_header(self):
        response = self.client.get(
            "/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-12",
            headers={"X-Dev-Auth": "admin:1", "Host": "example.com"},
        )
        self.assertEqual(response.status_code, 401)
        remote_client = TestClient(app, base_url="http://localhost", client=("203.0.113.9", 443))
        remote_response = remote_client.get(
            "/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-12",
            headers={"X-Dev-Auth": "admin:1"},
        )
        self.assertEqual(remote_response.status_code, 401)

    def test_synthetic_role_logins_and_permissions(self):
        admin_headers = {"X-Dev-Auth": "admin:1"}
        teacher_headers = {"X-Dev-Auth": "teacher:1"}
        student_headers = {"X-Dev-Auth": "student:1"}
        self.assertEqual(self.client.get("/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-12", headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/schedule/observability?week_start=2026-10-12&editor=true", headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/groups", headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/people?kind=teacher", headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/people?kind=student", headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/teacher/home", headers=teacher_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/teacher/profile", headers=teacher_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/student/profile", headers=student_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/student/grades", headers=student_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-12", headers={"X-Dev-Auth": "teacher:1"}).status_code, 403)

    def test_retired_schedule_draft_write_is_rejected_without_creating_revision(self):
        response = self.client.put(
            "/api/admin/schedule/draft",
            headers={"X-Dev-Auth": "admin:1"},
            json={"scope_kind": "week", "scope_key": "2026-10-12", "expected_revision": 0, "payload": {}},
        )
        self.assertEqual(response.status_code, 410, response.text)
        with database.connection() as connection:
            saved = connection.execute("SELECT revision FROM schedule_editor_drafts WHERE draft_key='week:2026-10-12'").fetchone()
        self.assertIsNone(saved)


if __name__ == "__main__":
    unittest.main()
