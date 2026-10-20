import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router


class ProductionDatabaseStub(Database):
    def __init__(self):
        super().__init__(database_url="postgresql://production-db")

    def get_admin_browser_session(self, token):
        return {"telegram_user_id": 123, "user_id": "admin-uuid"} if token == "valid" else None

    def get_user_by_id(self, user_id):
        return {"id": user_id} if user_id == "admin-uuid" else None

    def user_has_role(self, user_id, role):
        return user_id == "admin-uuid" and role == "admin"


class ProductionSchoolWorkspaceRouteTests(unittest.TestCase):
    def setUp(self):
        self.database = ProductionDatabaseStub()
        self.app = FastAPI()
        self.app.include_router(create_router(self.database))
        self.client = TestClient(self.app)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.settings = patch("backend.handlers.api.get_settings", return_value=SimpleNamespace(local_test_mode=False))
        self.settings.start()

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def test_capabilities_enable_school_workspace_for_production_postgres(self):
        response = self.client.get("/api/admin/schedule/experiments/capabilities", headers={"X-Dev-Auth": "browser:valid"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["school_available"])

    def test_school_create_and_activity_catalog_use_production_database(self):
        headers = {"X-Dev-Auth": "browser:valid"}
        created = {"id": "draft-1", "draft_kind": "school"}
        with patch("backend.handlers.api.create_experiment", return_value=created) as create, \
                patch("backend.handlers.api.list_activities", return_value=[{"id": "animation"}]) as listing, \
                patch("backend.handlers.api.save_activity", return_value={"id": "custom"}) as save:
            response = self.client.post("/api/admin/schedule/experiments/school", headers=headers,
                                        json={"name": "School template", "source": "empty"})
            catalog = self.client.get("/api/admin/schedule/activities?workspace=school", headers=headers)
            saved = self.client.post("/api/admin/schedule/activities?workspace=school", headers=headers,
                                     json={"name": "Custom", "type": "club"})
        self.assertEqual(response.status_code, 200)
        self.assertIs(create.call_args.args[0], self.database)
        self.assertEqual(create.call_args.kwargs["payload"]["draft_kind"], "school")
        self.assertEqual(catalog.status_code, 200)
        self.assertIs(listing.call_args.args[0], self.database)
        self.assertEqual(saved.status_code, 200)
        self.assertIs(save.call_args.args[0], self.database)

    def test_school_import_opens_active_saved_template_without_local_importer(self):
        headers = {"X-Dev-Auth": "browser:valid"}
        with patch("backend.handlers.api.open_active_school_template", return_value={"id": "draft-1"}) as open_template, \
                patch("backend.handlers.api.import_school_template") as local_import:
            response = self.client.post("/api/admin/schedule/experiments/school/import", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIs(open_template.call_args.args[0], self.database)
        self.assertEqual(open_template.call_args.kwargs, {})
        local_import.assert_not_called()

    def test_school_directory_uses_production_database_without_local_source(self):
        headers = {"X-Dev-Auth": "browser:valid"}
        with patch("backend.handlers.api.school_directory", return_value={"groups": []}) as directory, \
                patch("backend.handlers.api.school_source", side_effect=AssertionError("local source must not be opened")):
            response = self.client.get("/api/admin/schedule/experiments/school/directory", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIs(directory.call_args.args[0], self.database)


if __name__ == "__main__":
    unittest.main()
