"""Retired handlers reject direct requests against a temporary real database."""
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router


class RetiredScheduleRoutesTests(unittest.TestCase):
    def test_source_policy_exposes_review_hold_without_reading_google_or_writing(self):
        from unittest.mock import patch
        from backend.services.constructor_publication import initialize_publication_schema
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "disposable.sqlite3")
            database.initialize()
            initialize_publication_schema(database)
            admin = database.get_or_create_user(900003, "admin")
            challenge, _ = database.create_admin_login_challenge(admin["id"])
            token, _ = database.consume_admin_login_challenge(challenge)
            app = FastAPI()
            app.include_router(create_router(database))
            client = TestClient(app)
            path = "/api/admin/schedule/source-policy?week_start=2026-10-21"
            self.assertEqual(client.get(path).status_code, 401)
            headers = {"X-Dev-Auth": f"browser:{token}"}
            with patch.dict("os.environ", {"SCHEDULE_BACKEND": "canonical"}):
                initial = client.get(path, headers=headers).json()
                self.assertFalse(initial["manual_publication_active"])
                self.assertTrue(initial["automatic_application_enabled"])
                with database.connection() as connection:
                    connection.execute("INSERT INTO schedule_publication_heads VALUES ('template','synthetic','version',1,'2026-10-19')")
                response = client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"week_start": "2026-10-19", "manual_publication_active": True,
                    "review_required": True, "source_checked": False, "automatic_application_enabled": False})
                with database.connection() as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM schedule_publication_heads").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT count(*) FROM schedule_constructor_publications").fetchone()[0], 0)

    def test_five_retired_mutations_return_410_without_schedule_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "disposable.sqlite3")
            database.initialize()
            admin = database.get_or_create_user(900002, "admin")
            challenge, _ = database.create_admin_login_challenge(admin["id"])
            token, _ = database.consume_admin_login_challenge(challenge)
            app = FastAPI()
            app.include_router(create_router(database))
            client = TestClient(app)
            headers = {"X-Dev-Auth": f"browser:{token}"}
            cases = [
                ("PUT", "/api/admin/schedule/draft", {"scope_kind": "week", "scope_key": "2026-10-19", "expected_revision": 0, "payload": {}}),
                ("DELETE", "/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-19&expected_revision=1", None),
                ("POST", "/api/admin/schedule/draft/publish", {"scope_kind": "week", "scope_key": "2026-10-19", "expected_revision": 1}),
                ("POST", "/api/admin/schedule/template/confirm", {"expected_version_id": "synthetic"}),
                ("POST", "/api/admin/schedule/import-to-draft", {"week_start": "2026-10-19", "expected_revision": 0, "proposed_changes": [], "source_context": {}}),
            ]
            for method, path, body in cases:
                with self.subTest(method=method, path=path):
                    response = client.request(method, path, headers=headers, **({"json": body} if body else {}))
                    self.assertEqual(response.status_code, 410, response.text)
            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schedule_editor_drafts").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM canonical_schedule_versions").fetchone()[0], 0)
            response = client.put("/api/admin/schedule/draft", json=cases[0][2])
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
