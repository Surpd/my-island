import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.schedule_experiments import create_experiment


class LiveDirectoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "live.db")
        self.database.initialize()
        self.admin = self.database.get_or_create_user(900130, "admin")["id"]
        code, _ = self.database.create_admin_login_challenge(self.admin)
        self.token, _ = self.database.consume_admin_login_challenge(code)
        self.headers = {"X-Dev-Auth": f"browser:{self.token}"}
        app = FastAPI()
        app.include_router(create_router(self.database))
        self.client = TestClient(app)

    def test_directory_marker_detects_insert_edit_and_removal_with_revocation(self):
        path = "/api/admin/directory/version"
        def marker():
            result = self.client.get(path, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(set(result.json()), {"actor", "version"})
            return result.json()["version"]

        original = marker()
        teacher = self.database.create_identity("teacher", "Synthetic")
        inserted = marker()
        self.assertNotEqual(original, inserted)
        self.assertEqual(inserted, marker())
        self.database.update_admin_identity(teacher["id"], {"comment": "Private"}, self.admin)
        self.assertNotEqual(inserted, marker())
        with self.database.connection() as connection:
            connection.execute("DELETE FROM identities WHERE id=?", (teacher["id"],))
        self.assertEqual(original, marker())
        self.database.revoke_admin_browser_session(self.token)
        self.assertIn(self.client.get(path, headers=self.headers).status_code, (401, 403))

    def test_constructor_marker_uses_the_same_admin_guard_and_never_returns_payload(self):
        item = create_experiment(self.database, name="Synthetic", source="empty", payload={"lessons": []})
        path = "/api/admin/schedule/experiments/" + item["id"]
        marker = self.client.get(path + "?revision_only=true", headers=self.headers)
        self.assertEqual(marker.status_code, 200, marker.text)
        self.assertEqual(marker.json(), {"revision": item["revision"], "actor": str(self.admin)})
        self.assertEqual(self.client.get(path, headers=self.headers).json()["viewer_id"], str(self.admin))
        self.assertIn(self.client.get(path + "?revision_only=true").status_code, (401, 403))
        self.assertEqual(self.client.get(path + "-missing?revision_only=true", headers=self.headers).status_code, 404)
