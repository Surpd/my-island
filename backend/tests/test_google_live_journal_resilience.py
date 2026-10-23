import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.database import Database
from backend.handlers.api import create_router
from backend.services.google_contract import GoogleIntegrationConfig
from backend.services.google_live import GoogleApiError, GoogleLiveClient, GoogleTokenStore


class GoogleLiveClientMethodTests(unittest.TestCase):
    def setUp(self):
        self.store = GoogleTokenStore("unused-token.json")
        self.client = GoogleLiveClient(
            GoogleIntegrationConfig(None, None, "client", "secret", "https://api.test/callback"),
            {"access_token": "access-token", "expires_at": int(time.time()) + 3600},
            self.store,
        )

    def test_expected_sheets_and_classroom_methods_are_real_client_methods(self):
        names = (
            "sheet_grid_range", "sheet_values", "sheet_values_many",
            "classroom_courses_for_teacher", "coursework",
            "student_submissions", "coursework_materials",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(self.client, name)))

    def test_sheet_values_reads_real_method_response_and_builds_authenticated_request(self):
        requests = []

        def response(request):
            requests.append(request)
            return {"valueRanges": [{"values": [["Имя", "Оценка"], ["Ученик", "5"]]}]}

        with patch("backend.services.google_live._request_json", side_effect=response):
            values = self.client.sheet_values("sheet/with-special", "'Математика'!A1:B2")

        self.assertEqual(values, [["Имя", "Оценка"], ["Ученик", "5"]])
        self.assertEqual(len(requests), 1)
        self.assertIn("sheet%2Fwith-special", requests[0].full_url)
        self.assertIn("values:batchGet", requests[0].full_url)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer access-token")

    def test_sheets_api_errors_remain_typed_for_callers(self):
        error = HTTPError(
            "https://sheets.googleapis.com/test", 403, "Forbidden", {},
            io.BytesIO(json.dumps({"error": {"errors": [{"reason": "insufficientPermissions"}]}}).encode()),
        )
        with patch("backend.services.google_live.urlopen", side_effect=error):
            with self.assertRaises(GoogleApiError) as raised:
                self.client.sheet_values("spreadsheet", "A1:B2")
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.reason, "insufficientPermissions")

    def test_restored_sheet_and_classroom_methods_execute_their_real_pagination_logic(self):
        with patch.object(self.client, "get", side_effect=[
            {"spreadsheetId": "spreadsheet", "sheets": [{"properties": {"title": "Математика"}}]},
            {"sheets": [{"properties": {"title": "Математика"}}]},
            {"valueRanges": [{"values": [["Вкладка", "Оценка"]]}]},
            {"courses": [{"id": "course-1"}], "nextPageToken": "page-2"},
            {"courses": [{"id": "course-2"}]},
            {"courseWork": [{"id": "work-1"}]},
            {"studentSubmissions": [{"id": "submission-1"}]},
            {"courseWorkMaterial": [{"id": "material-1"}]},
        ]) as request:
            spreadsheet = self.client.spreadsheet("spreadsheet")
            sheet = self.client.sheet_grid_range("spreadsheet", "Математика")
            values = self.client.sheet_values_many("spreadsheet", ["Математика!A1:B1"])
            courses = self.client.classroom_courses_for_teacher()
            coursework = self.client.coursework("course-1")
            submissions = self.client.student_submissions("course-1", "work-1")
            materials = self.client.coursework_materials("course-1")

        self.assertEqual(spreadsheet["sheets"][0]["properties"]["title"], "Математика")
        self.assertEqual(sheet["sheets"][0]["properties"]["title"], "Математика")
        self.assertEqual(values, [[["Вкладка", "Оценка"]]])
        self.assertEqual([item["id"] for item in courses], ["course-1", "course-2"])
        self.assertEqual(coursework, [{"id": "work-1"}])
        self.assertEqual(submissions, [{"id": "submission-1"}])
        self.assertEqual(materials, [{"id": "material-1"}])
        self.assertEqual(request.call_count, 8)


class JournalSyncResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "journal.db")
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_run_is_finalized_and_stale_run_can_be_retried(self):
        run_id = self.database.start_journal_sync("book-1", 9)
        self.assertIsNotNone(run_id)
        self.assertIsNone(self.database.start_journal_sync("book-1", 9))
        self.database.finish_journal_sync(run_id, status="error", error="GoogleLiveClient.sheet_values unavailable")

        with self.database.connection() as connection:
            stale_id = self.database.execute(connection,
                "INSERT INTO journal_sync_runs(spreadsheet_id, grade, trigger_kind, status, started_at) VALUES (?, ?, ?, 'running', datetime('now', '-31 minutes')) RETURNING id",
                ("book-1", 9, "manual"),
            ).fetchone()["id"]

        retry_id = self.database.start_journal_sync("book-1", 9)
        self.assertIsNotNone(retry_id)
        history = self.database.journal_sync_history("book-1")
        recovered = next(run for run in history if run["id"] == stale_id)
        self.assertEqual(recovered["status"], "error")
        self.assertIn("30-minute lease expired", recovered["error"])
        self.assertIsNotNone(recovered["finished_at"])
        self.assertEqual(next(run for run in history if run["id"] == run_id)["error"], "GoogleLiveClient.sheet_values unavailable")

    def test_unexpected_refresh_failure_finishes_run_and_next_refresh_succeeds(self):
        user_id = self._create_admin()
        token, _ = self.database.create_admin_login_challenge(user_id)
        browser_token, _ = self.database.consume_admin_login_challenge(token)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{browser_token}"}
        settings = Settings(
            app_env="development", dev_auth_enabled=True, telegram_bot_token="", database_url=None,
            database_path=str(Path(self.temp.name) / "journal.db"), cors_origins=("http://localhost",),
            backend_public_url="http://localhost", frontend_public_url="http://localhost",
            google_sheets_spreadsheet_id=None, google_classroom_course_id=None,
            google_oauth_client_id=None, google_oauth_client_secret=None,
            google_oauth_redirect_uri=None, google_oauth_refresh_token=None,
        )
        with patch("backend.handlers.api.get_settings", return_value=settings), patch(
            "backend.handlers.api.refresh_grade_journal",
            side_effect=[TypeError("unexpected internal failure"), {"status": "success", "tabs": []}],
        ):
            failed = client.post("/api/admin/journals/refresh-grade?grade=9&spreadsheet_id=book-retry", headers=headers)
            self.assertEqual(failed.status_code, 500)
            self.assertEqual(failed.json()["detail"], "Не удалось обновить журнал. Попробуйте ещё раз позже.")
            failed_run = self.database.journal_sync_history("book-retry")[0]
            self.assertEqual(failed_run["status"], "error")
            self.assertIn("TypeError", failed_run["error"])

            succeeded = client.post("/api/admin/journals/refresh-grade?grade=9&spreadsheet_id=book-retry", headers=headers)
        self.assertEqual(succeeded.status_code, 200, succeeded.text)
        self.assertIn("success", [run["status"] for run in self.database.journal_sync_history("book-retry")])

    def _create_admin(self):
        with self.database.connection() as connection:
            row = self.database.execute(connection,
                "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id", (991999,),
            ).fetchone()
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (row["id"],))
            return row["id"]


if __name__ == "__main__":
    unittest.main()
