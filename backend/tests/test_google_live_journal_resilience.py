import io
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.database import Database
from backend.handlers.api import _connected_current_journal_refresh_plan, create_router
from backend.services.google_contract import GoogleIntegrationConfig
from backend.services.google_live import GoogleApiError, GoogleLiveClient, GoogleTokenStore
from backend.services.google_sync_service import _journal_error_diagnostic, refresh_grade_journal


class _FakePostgresConnection:
    def __init__(self, lock_acquired):
        self.lock_acquired = lock_acquired
        self.queries = []
        self.commits = 0

    def commit(self):
        self.commits += 1

    def execute(self, query, params=()):
        self.queries.append((query, params))
        return _FakePostgresResult(self.lock_acquired, 42 if "INSERT INTO journal_sync_runs" in query else None)


class _FakePostgresResult:
    rowcount = 1

    def __init__(self, lock_acquired, run_id=None):
        self.lock_acquired = lock_acquired
        self.run_id = run_id

    def fetchone(self):
        return {"id": self.run_id} if self.run_id is not None else {"acquired": self.lock_acquired}


class _FakePostgresDatabase(Database):
    def __init__(self, lock_acquired):
        super().__init__(database_url="postgresql://synthetic")
        self.fake_connection = _FakePostgresConnection(lock_acquired)

    @contextmanager
    def connection(self):
        yield self.fake_connection


class GoogleLiveClientMethodTests(unittest.TestCase):
    def setUp(self):
        self.store = GoogleTokenStore("unused-token.json")
        self.client = GoogleLiveClient(
            GoogleIntegrationConfig(None, None, "client", "secret", "https://api.test/callback"),
            {"access_token": "access-token", "expires_at": int(time.time()) + 3600},
            self.store,
        )

    def test_access_layout_and_ambiguous_mapping_have_distinct_diagnostics(self):
        errors = [(GoogleApiError(403, {}), "google_access"),
                  (ValueError("неполный блок журнала"), "unsupported_structure"),
                  (ValueError("Неоднозначное сопоставление одинаковых колонок журнала"), "assessment_mapping_conflict"),
                  (ValueError("Период вкладки отличается от сохранённого"), "period_conflict")]
        for error, code in errors:
            with self.subTest(code=code):
                self.assertEqual(_journal_error_diagnostic(error)["code"], code)

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

    def test_postgres_workbook_lock_blocks_reaping_and_releases_session_lock(self):
        busy_database = _FakePostgresDatabase(False)
        self.assertIsNone(busy_database.start_journal_sync("book-locked", 9))
        self.assertEqual(len(busy_database.fake_connection.queries), 1)
        self.assertIn("pg_try_advisory_xact_lock", busy_database.fake_connection.queries[0][0])

        active_database = _FakePostgresDatabase(True)
        with active_database.journal_workbook_lock("book-locked") as acquired:
            self.assertTrue(acquired)
        queries = [query for query, _ in active_database.fake_connection.queries]
        self.assertTrue(any("pg_try_advisory_lock" in query for query in queries))
        self.assertTrue(any("pg_advisory_unlock" in query for query in queries))
        self.assertEqual(active_database.fake_connection.commits, 1)

    def test_validated_import_run_reservation_uses_existing_postgres_session_lock(self):
        database = _FakePostgresDatabase(True)
        with database.journal_workbook_lock("book-locked") as acquired:
            self.assertTrue(acquired)
            self.assertEqual(database.start_journal_sync("book-locked", 9, book_lock_held=True), 42)
        queries = [query for query, _ in database.fake_connection.queries]
        self.assertFalse(any("pg_try_advisory_xact_lock" in query for query in queries))
        self.assertTrue(any("pg_advisory_unlock" in query for query in queries))

    def test_refresh_reports_progress_after_each_exact_named_tab(self):
        run_id = self.database.start_journal_sync("book-progress", 9)
        client = Mock()
        client.spreadsheet.return_value = {
            "properties": {"title": "Synthetic"},
            "sheets": [{"properties": {"title": "Math ", "sheetId": 1}}, {"properties": {"title": "History", "sheetId": 2}}],
        }
        client.sheet_values.side_effect = [[[]], [[]]]
        with patch("backend.services.google_sync_service._client", return_value=(client, None)), patch(
            "backend.services.google_sync_service.sync_journal_values",
            side_effect=[{"assessments": 1}, ValueError("synthetic tab failure")],
        ):
            result = refresh_grade_journal(self.database, object(), 9, spreadsheet_id="book-progress", run_id=run_id)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(client.sheet_values.call_args_list[0].args[1], "'Math '!A:ZZ")
        self.assertEqual(client.sheet_values.call_args_list[1].args[1], "'History'!A:ZZ")
        run = self.database.get_journal_sync_run(run_id)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["counts"], {"completed_tabs": 2, "total_tabs": 2, "succeeded": 1, "failed": 1, "skipped": 0, "excluded": 0})
        with self.database.connection() as connection:
            source = self.database.execute(connection, "SELECT last_error FROM journal_sources WHERE spreadsheet_id = ? AND sheet_id = ?", ("book-progress", "2")).fetchone()
        self.assertEqual(source["last_error"], "Данные вкладки не прошли проверку. Предыдущие данные сохранены.")

    def test_classified_auxiliary_clears_old_error_but_preserves_results(self):
        from backend.services.journal_import import sync_journal_values
        sync_journal_values(self.database, [['Дата','01.09'], ['Задание','Task'], ['Вес',1], ['Оценка',10], ['Student',8]], spreadsheet_id='book', spreadsheet_title='Synthetic', grade=9, subject='Art', sheet_title='Art', sheet_id=1)
        self.database.record_journal_failure('book', 'Art', 'Old error', code='old', sheet_id=1)
        client = Mock()
        client.spreadsheet.return_value = {'properties': {'title':'Synthetic'}, 'sheets':[{'properties':{'title':'Art','sheetId':1}}]}
        client.sheet_values.return_value = [['Фамилия','Имя','Практикум 1','Практикум 2','Итог (средний балл)'], ['Test','Student',8,9,8.5]]
        with patch('backend.services.google_sync_service._client', return_value=(client, None)):
            outcome = refresh_grade_journal(self.database, object(), 9, spreadsheet_id='book')
        self.assertEqual(outcome['tabs'][0]['status'], 'skipped')
        source = self.database.journal_status()[0]
        self.assertEqual(source['classification'], 'derived_results')
        self.assertTrue(source['classification_reason'])
        self.assertIsNone(source['last_error'])
        self.assertEqual(source['result_count'], 1)
        self.assertFalse(source['excluded_from_journals'])

    def test_progress_updates_only_running_runs(self):
        run_id = self.database.start_journal_sync("book-progress", 9)
        self.assertTrue(self.database.update_journal_sync_progress(run_id, {"completed_tabs": 1, "total_tabs": 3, "succeeded": 1, "failed": 0}))
        self.database.finish_journal_sync(run_id, status="error", error="refresh failed after first tab")
        self.assertFalse(self.database.update_journal_sync_progress(run_id, {"completed_tabs": 4, "total_tabs": 3, "succeeded": 3, "failed": 1}))
        self.assertEqual(self.database.get_journal_sync_run(run_id)["counts"], {"completed_tabs": 1, "total_tabs": 3, "succeeded": 1, "failed": 0})

    def test_cron_enqueues_scheduled_run(self):
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        settings = Mock(journal_cron_secret="cron-secret")
        with patch("backend.handlers.api.get_settings", return_value=settings), patch.object(
            self.database, "journal_status",
            return_value=[{"spreadsheet_id": "book-cron", "grade": 9, "academic_year": "2026/27",
                           "term_number": 2, "period_status": "current", "last_synced_at": "2026-11-01",
                           "classification": None, "excluded_from_journals": False, "auto_sync_enabled": True}],
        ), patch("backend.handlers.api.refresh_grade_journal", return_value={"status": "success", "tabs": [{"status": "success"}]}) as refresh:
            response = client.post("/api/admin/journals/cron", headers={"X-Journal-Cron-Secret": "cron-secret"})

        self.assertEqual(response.status_code, 202, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["status"], "running")
        self.assertEqual(refresh.call_args.kwargs["trigger_kind"], "scheduled")
        self.assertEqual(refresh.call_args.kwargs["term_number"], 2)
        run = self.database.get_journal_sync_run(item["run_id"])
        self.assertEqual(run["trigger_kind"], "scheduled")
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["counts"], {"completed_tabs": 1, "total_tabs": 1, "succeeded": 1, "failed": 0,
                                         "skipped": 0, "excluded": 0, "not_imported": 0})

    def test_refresh_all_plan_uses_configured_current_trimester_and_semester(self):
        rows = [
            {"spreadsheet_id": "book-6", "grade": 6, "academic_year": "2026/27", "term_number": 2,
             "period_status": "archived", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-6", "grade": 6, "academic_year": "2026/27", "term_number": 1,
             "period_status": "current", "last_synced_at": "2026-09-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-10", "grade": 10, "academic_year": "2026/27", "term_number": 2,
             "period_status": "current", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-11", "grade": 11, "academic_year": "2026/27", "term_number": 1,
             "period_status": "archived", "last_synced_at": "2026-09-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-7", "grade": 7, "academic_year": "2026/27", "term_number": 3,
             "period_status": "archived", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": True},
            {"spreadsheet_id": "book-7", "grade": 7, "academic_year": "2026/27", "term_number": 3,
             "period_status": "current", "last_synced_at": "2026-11-01", "classification": None,
             "excluded_from_journals": False, "auto_sync_enabled": True},
        ]

        jobs, skipped = _connected_current_journal_refresh_plan(rows)

        self.assertEqual(jobs, [
            {"spreadsheet_id": "book-6", "grade": 6, "term_number": 1},
            {"spreadsheet_id": "book-7", "grade": 7, "term_number": 3},
            {"spreadsheet_id": "book-10", "grade": 10, "term_number": 2},
        ])
        self.assertEqual({item["spreadsheet_id"] for item in skipped}, {"book-11"})
        rows[0]["auto_sync_enabled"] = True
        rows[2]["auto_sync_enabled"] = True
        auto_jobs, _ = _connected_current_journal_refresh_plan(rows, require_auto=True)
        self.assertEqual(auto_jobs, [{"spreadsheet_id": "book-7", "grade": 7, "term_number": 3},
                                     {"spreadsheet_id": "book-10", "grade": 10, "term_number": 2}])

    def test_auto_sync_scope_is_optional_and_forwarded_without_changing_legacy_contract(self):
        user_id = self._create_admin()
        token, _ = self.database.create_admin_login_challenge(user_id)
        browser_token, _ = self.database.consume_admin_login_challenge(token)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{browser_token}"}
        with patch.object(self.database, "journal_scheduler_configured", return_value=True), patch.object(
            self.database, "set_journal_auto_sync", return_value=3,
        ) as update:
            scoped = client.post("/api/admin/journals/auto-sync", headers=headers, json={
                "spreadsheet_id": "book-6", "enabled": True, "grade": 6, "term_number": 2,
            })
            legacy = client.post("/api/admin/journals/auto-sync", headers=headers, json={
                "spreadsheet_id": "book-6", "enabled": False,
            })
            incomplete = client.post("/api/admin/journals/auto-sync", headers=headers, json={
                "spreadsheet_id": "book-6", "enabled": True, "grade": 6,
            })

        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertEqual(scoped.json()["updated_sources"], 3)
        self.assertEqual(update.call_args_list[0].kwargs, {"grade": 6, "term_number": 2})
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(update.call_args_list[1].kwargs, {"grade": None, "term_number": None})
        self.assertEqual(incomplete.status_code, 422, incomplete.text)

    def test_scoped_auto_sync_update_is_limited_to_connected_class_period(self):
        database = _FakePostgresDatabase(True)

        self.assertEqual(database.set_journal_auto_sync("book-6", True, grade=6, term_number=2), 1)
        scoped_sql, scoped_params = database.fake_connection.queries[-1]
        self.assertIn("grade =", scoped_sql)
        self.assertIn("term_number =", scoped_sql)
        self.assertIn("last_synced_at IS NOT NULL", scoped_sql)
        self.assertIn("excluded_from_journals IS FALSE", scoped_sql)
        self.assertEqual(scoped_params, (True, "book-6", 6, 2))

        self.assertEqual(database.set_journal_auto_sync("book-6", False), 1)
        legacy_sql, legacy_params = database.fake_connection.queries[-1]
        self.assertNotIn("grade =", legacy_sql)
        self.assertEqual(legacy_params, (False, "book-6"))

    def test_refresh_all_is_admin_only_and_enqueues_connected_books_sequentially(self):
        user_id = self._create_admin()
        token, _ = self.database.create_admin_login_challenge(user_id)
        browser_token, _ = self.database.consume_admin_login_challenge(token)
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        headers = {"X-Dev-Auth": f"browser:{browser_token}"}
        rows = [
            {"spreadsheet_id": "book-6", "grade": 6, "academic_year": "2026/27", "term_number": 2,
             "period_status": "current", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-9", "grade": 9, "academic_year": "2026/27", "term_number": 2,
             "period_status": "current", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": False},
            {"spreadsheet_id": "book-10", "grade": 10, "academic_year": "2026/27", "term_number": 1,
             "period_status": "current", "last_synced_at": "2026-11-01", "classification": None, "excluded_from_journals": False},
        ]

        self.assertEqual(client.post("/api/admin/journals/refresh-all").status_code, 401)
        with patch.object(self.database, "journal_status", return_value=rows), patch.object(
            self.database, "start_journal_sync",
            side_effect=lambda spreadsheet_id, *_args, **_kwargs: None if spreadsheet_id == "book-9" else f"run-{spreadsheet_id}",
        ), patch("backend.handlers.api.get_settings", return_value=object()), patch(
            "backend.handlers.api.refresh_grade_journal", return_value={"status": "success", "tabs": []},
        ) as refresh:
            response = client.post("/api/admin/journals/refresh-all", headers=headers)

        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        self.assertEqual([item["spreadsheet_id"] for item in payload["items"]], ["book-6", "book-10"])
        self.assertEqual(payload["busy"], [{"spreadsheet_id": "book-9", "grade": 9, "term_number": 2, "status": "already_running"}])
        self.assertEqual([(call.args[2], call.kwargs["term_number"]) for call in refresh.call_args_list], [(6, 2), (10, 1)])

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
            self.assertEqual(failed.status_code, 202, failed.text)
            failed_run_id = failed.json()["run_id"]
            self.assertEqual(failed.json()["status"], "running")
            failed_run = self.database.journal_sync_history("book-retry")[0]
            self.assertEqual(failed_run["status"], "error")
            self.assertIn("TypeError", failed_run["error"])
            polled_failure = client.get(f"/api/admin/journals/sync-runs/{failed_run_id}", headers=headers)
            self.assertEqual(polled_failure.status_code, 200, polled_failure.text)
            self.assertEqual(polled_failure.json()["status"], "error")

            succeeded = client.post("/api/admin/journals/refresh-grade?grade=9&spreadsheet_id=book-retry", headers=headers)
        self.assertEqual(succeeded.status_code, 202, succeeded.text)
        self.assertEqual(succeeded.json()["status"], "running")
        completed_run = self.database.get_journal_sync_run(succeeded.json()["run_id"])
        self.assertEqual(completed_run["status"], "success")

    def _create_admin(self):
        with self.database.connection() as connection:
            row = self.database.execute(connection,
                "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id", (991999,),
            ).fetchone()
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (row["id"],))
            return row["id"]


if __name__ == "__main__":
    unittest.main()
