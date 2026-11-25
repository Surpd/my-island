from concurrent.futures import ThreadPoolExecutor
from datetime import date
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.personal_workspace import LayoutUpdate, save_layout


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "layout.db")
        self.db.initialize()
        with self.db.connection() as conn:
            for index, role in enumerate(("admin", "admin", "student", "teacher"), start=1):
                row = self.db.execute(conn, "INSERT INTO users(telegram_user_id,role) VALUES (?,?) RETURNING id",
                                      (index, role)).fetchone()
                self.db.execute(conn, "INSERT INTO user_roles(user_id,role) VALUES (?,?)", (row["id"], role))
        app = FastAPI()
        app.include_router(create_router(self.db))
        self.client = TestClient(app)
        auth = patch("backend.handlers.api.resolve_auth", side_effect=lambda data, key: {"id": int(key), "dev": True})
        auth.start()
        self.addCleanup(auth.stop)
        self.path = "/api/admin/workspace/layout"

    def test_two_users_multiple_instances_and_reset(self):
        admin = {"X-Dev-Auth": "1"}
        first = self.client.get(self.path, headers=admin)
        self.assertEqual(first.json()["revision"], 0)
        self.assertIn("no-store", first.headers["cache-control"])
        widgets = [{"instance_id": key, "type": "operational_day", "filters": {"day": day}}
                   for key, day in (("a", "2026-12-31"), ("b", "2026-12-31"), ("c", "2027-01-01"))]
        payload = {"expected_revision": 0, "widgets": widgets}
        response = self.client.put(self.path, json=payload, headers=admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list(response.json()["requests"].values()), [["a", "b"], ["c"]])
        conflict = self.client.put(self.path, json=payload, headers=admin)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["current"], response.json())
        self.assertEqual(self.client.get(self.path, headers={"X-Dev-Auth": "2"}).json()["revision"], 0)
        reset = self.client.put(self.path, json={"expected_revision": 1, "widgets": None}, headers=admin).json()
        self.assertEqual(reset["widgets"], first.json()["widgets"])
        self.assertEqual(reset["revision"], 2)

    def test_filters_roles_and_concurrent_first_write(self):
        for role in ("3", "4"):
            self.assertEqual(self.client.get(self.path, headers={"X-Dev-Auth": role}).status_code, 403)
            self.assertEqual(self.client.put(self.path, headers={"X-Dev-Auth": role},
                             json={"expected_revision": 0, "widgets": []}).status_code, 403)
        widget = {"instance_id": "t", "type": "teacher_load", "filters": {"path": "/other"}}
        self.assertEqual(self.client.put(self.path, json={"expected_revision": 0, "widgets": [widget]},
                                         headers={"X-Dev-Auth": "1"}).status_code, 422)
        change = LayoutUpdate(expected_revision=0, widgets=[])
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(save_layout, self.db, 1, change, date(2026, 11, 25)) for _ in range(2)]
            self.assertEqual(sorted(job.result()[0] for job in attempts), [False, True])
