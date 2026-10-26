from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
import os
import sys
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.database import Database
from backend.handlers.api import create_router
from backend.services.school_experiment_workspace import ReadOnlySchoolDatabase
from backend.services.school_preview_workspace import SchoolPreviewDatabase


APPROVED_URL = "postgresql://postgres.kmrcbewmxeqqxfauiwyy:placeholder@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"


class SchoolPreviewWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / "preview.sqlite3"
        self.database = SchoolPreviewDatabase(APPROVED_URL, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_lifecycle_keeps_only_opaque_school_id_locally(self):
        created = self.database.create_student_preview(
            "local-admin-7", "unused", "2000-01-01 00:00:00", "2099-01-01 00:00:00",
            target_identity_id="school-person-123", role="student",
        )
        self.assertEqual(created["target_identity_id"], "school-person-123")
        self.assertEqual(created["actor_user_id"], "local-admin-7")
        self.assertEqual(
            datetime.fromisoformat(created["expires_at"]).replace(tzinfo=timezone.utc)
            - datetime.fromisoformat(created["created_at"]).replace(tzinfo=timezone.utc),
            timedelta(hours=1),
        )
        self.assertIsNone(self.database.get_active_student_preview(created["id"], "another-admin"))
        self.assertIsNotNone(self.database.get_active_student_preview(created["id"], "local-admin-7"))
        self.assertTrue(self.database.end_student_preview(created["id"], "local-admin-7"))
        self.assertIsNone(self.database.get_active_student_preview(created["id"], "local-admin-7"))
        self.assertFalse(self.database.end_student_preview(created["id"], "local-admin-7"))

        connection = sqlite3.connect(self.store)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(school_preview_sessions)")}
            foreign_keys = connection.execute("PRAGMA foreign_key_list(school_preview_sessions)").fetchall()
            row = connection.execute("SELECT * FROM school_preview_sessions WHERE id=?", (created["id"],)).fetchone()
        finally:
            connection.close()
        self.assertEqual(columns, {"id", "actor_user_id", "target_identity_id", "role", "created_at", "expires_at", "ended_at"})
        self.assertEqual(foreign_keys, [])
        self.assertNotIn("display_name", row)
        self.assertNotIn("class_name", row)

    def test_local_audit_does_not_require_school_writes(self):
        self.database.record_audit_event("preview.checked", "student_preview", {"preview_id": "opaque"}, "admin")
        connection = sqlite3.connect(self.store)
        try:
            row = connection.execute("SELECT action, details FROM school_preview_audit").fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "preview.checked")
        self.assertIn('"preview_id": "opaque"', row[1])

    def test_browser_session_validation_is_read_only(self):
        source = sqlite3.connect(":memory:")
        source.row_factory = sqlite3.Row
        source.executescript("""CREATE TABLE users(id INTEGER, telegram_user_id INTEGER, role TEXT, identity_id INTEGER);
        CREATE TABLE admin_browser_sessions(id INTEGER, token_hash TEXT, user_id INTEGER, expires_at TEXT,
            last_seen_at TEXT, revoked_at TEXT);
        INSERT INTO users VALUES(5, 55, 'admin', NULL);""")
        token = "session-secret"
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        source.execute("INSERT INTO admin_browser_sessions VALUES(1, ?, 5, ?, 'unchanged', NULL)",
                       (self.database._token_hash(token), expiry))

        @contextmanager
        def source_connection():
            yield source

        with patch.object(self.database, "connection", source_connection), patch.object(self.database, "database_url", None):
            session = self.database.get_admin_browser_session(token)
        self.assertEqual(session["user_id"], 5)
        self.assertEqual(source.execute("SELECT last_seen_at FROM admin_browser_sessions").fetchone()[0], "unchanged")

    def test_user_provisioning_is_rejected_and_bootstrap_never_grants_roles(self):
        with patch.object(self.database, "find_user", return_value=None):
            with self.assertRaisesRegex(ValueError, "cannot create users"):
                self.database.get_or_create_user(123, "admin")
            self.assertIsNone(self.database.ensure_bootstrap_roles(123, ("admin",)))

    def test_read_only_school_source_disables_prepared_statements_on_transaction_pooler(self):
        psycopg, rows, connect = self._mock_psycopg()
        connection = MagicMock()
        connection.closed = False
        connection.execute.side_effect = lambda query: SimpleNamespace(fetchone=lambda: {"mode": "on"}) if query.startswith("SELECT current_setting") else None
        connect.return_value = connection
        with patch.dict(sys.modules, {"psycopg": psycopg, "psycopg.rows": rows}):
            database = ReadOnlySchoolDatabase(APPROVED_URL)
            with database.connection():
                pass
        self.assertIsNone(connect.call_args.kwargs["prepare_threshold"])
        self.assertEqual(connection.execute.call_args_list[0].args[0], "SET TRANSACTION READ ONLY")
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_core_database_disables_preparation_only_for_supabase_transaction_pooler(self):
        psycopg, rows, connect = self._mock_psycopg()
        with patch.dict(sys.modules, {"psycopg": psycopg, "psycopg.rows": rows}):
            for port, expect_disabled in ((6543, True), (5432, False)):
                connection = MagicMock()
                connection.closed = False
                connect.return_value = connection
                database = Database(database_url=f"postgresql://postgres.kmrcbewmxeqqxfauiwyy@aws-1-eu-west-1.pooler.supabase.com:{port}/postgres")
                with database.connection():
                    pass
                kwargs = connect.call_args.kwargs
                self.assertEqual("prepare_threshold" in kwargs, expect_disabled)
                if expect_disabled:
                    self.assertIsNone(kwargs["prepare_threshold"])
                self.assertEqual(connection.closed, False)
                connection.commit.assert_called_once()
                connection.close.assert_called_once()

    @staticmethod
    def _mock_psycopg():
        psycopg = ModuleType("psycopg")
        rows = ModuleType("psycopg.rows")
        rows.dict_row = object()
        connect = MagicMock()
        psycopg.connect = connect
        psycopg.rows = rows
        return psycopg, rows, connect


class SchoolPreviewRoutesIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temp.name) / "source.sqlite3"
        self.preview_path = Path(self.temp.name) / "preview-control.sqlite3"
        self.source = Database(self.source_path)
        self.source.initialize()
        with self.source.connection() as connection:
            self.admin_id = self.source.execute(connection, "INSERT INTO users(telegram_user_id,role) VALUES(71001,'admin') RETURNING id").fetchone()["id"]
            self.source.execute(connection, "INSERT INTO user_roles(user_id,role) VALUES(?,'admin')", (self.admin_id,))
            self.nonadmin_id = self.source.execute(connection, "INSERT INTO users(telegram_user_id,role) VALUES(71002,'student') RETURNING id").fetchone()["id"]
            self.source.execute(connection, "INSERT INTO user_roles(user_id,role) VALUES(?,'student')", (self.nonadmin_id,))
        self.source.create_group("9-А", "class", base_class_name="9-А", actor_user_id=self.admin_id)
        self.student = self.source.create_identity("student", "Preview Student", "9-А", actor_user_id=self.admin_id)
        self.teacher = self.source.create_identity("teacher", "Preview Teacher", actor_user_id=self.admin_id)
        self.admin_token = self._login_token(self.admin_id)
        self.nonadmin_token = self._login_token(self.nonadmin_id)
        self.database = SchoolPreviewDatabase(APPROVED_URL, self.preview_path)
        self.app = FastAPI()
        self.app.include_router(create_router(self.database))
        self.client = TestClient(self.app)
        self.env = patch.dict(os.environ, {
            "MY_ISLAND_SCHOOL_WORKSPACE": "1",
            "MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE": "1",
            "MY_ISLAND_TEST_PROFILE": "",
        }, clear=False)
        self.env.start()
        self.settings = patch("backend.handlers.api.get_settings", return_value=SimpleNamespace(
            local_test_mode=False, database_url=APPROVED_URL, telegram_bootstrap_user_ids=set(),
            schedule_backend="canonical",
        ))
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.env.stop()
        self.temp.cleanup()

    def _login_token(self, user_id):
        challenge, _ = self.source.create_admin_login_challenge(user_id)
        token, _ = self.source.consume_admin_login_challenge(challenge)
        return token

    @contextmanager
    def _readonly_source_connection(self):
        connection = sqlite3.connect(f"file:{self.source_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _source_counts(self):
        with self.source.connection() as connection:
            return tuple(self.source.execute(connection, f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                         for table in ("users", "student_preview_sessions", "student_preview_targets", "audit_log"))

    def test_catalogue_identity_previews_and_lifecycle_use_local_control_store(self):
        source_counts_before = self._source_counts()
        headers = {"X-Dev-Auth": f"browser:{self.admin_token}"}
        with patch.object(self.database, "connection", self._readonly_source_connection), patch.object(self.database, "database_url", None):
            catalogue = self.client.get("/api/admin/previews/people?role=student", headers=headers)
            self.assertEqual(catalogue.status_code, 200, catalogue.text)
            self.assertIn(self.student["display_name"], {item["display_name"] for item in catalogue.json()["items"]})

            created = {}
            for identity, role in ((self.student, "student"), (self.teacher, "teacher")):
                response = self.client.post("/api/admin/previews", headers=headers,
                                            json={"target_identity_id": str(identity["id"]), "role": role})
                self.assertEqual(response.status_code, 200, response.text)
                created[role] = response.json()
            student_profile = self.client.get("/api/student/profile", headers={**headers, "X-Preview-Id": created["student"]["id"]})
            teacher_profile = self.client.get("/api/teacher/profile", headers={**headers, "X-Preview-Id": created["teacher"]["id"]})
            self.assertEqual(student_profile.status_code, 200, student_profile.text)
            self.assertEqual(student_profile.json()["user"]["display_name"], self.student["display_name"])
            self.assertEqual(teacher_profile.status_code, 200, teacher_profile.text)
            self.assertEqual(teacher_profile.json()["user"]["display_name"], self.teacher["display_name"])

            self.assertEqual(self._source_counts(), source_counts_before)
            connection = sqlite3.connect(self.preview_path)
            try:
                local_count = connection.execute("SELECT COUNT(*) FROM school_preview_sessions").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(local_count, 2)

            ended = self.client.post(f"/api/admin/previews/{created['student']['id']}/end", headers=headers)
            self.assertEqual(ended.status_code, 200, ended.text)
            invalidated = self.client.get(f"/api/admin/previews/{created['student']['id']}/diagnostics", headers=headers)
            self.assertEqual(invalidated.status_code, 404, invalidated.text)
            self.assertEqual(self._source_counts(), source_counts_before)

    def test_missing_and_nonadmin_browser_sessions_are_rejected(self):
        with patch.object(self.database, "connection", self._readonly_source_connection), patch.object(self.database, "database_url", None):
            no_session = self.client.get("/api/admin/previews/people?role=student")
            forbidden = self.client.get("/api/admin/previews/people?role=student",
                                        headers={"X-Dev-Auth": f"browser:{self.nonadmin_token}"})
        self.assertEqual(no_session.status_code, 401, no_session.text)
        self.assertEqual(forbidden.status_code, 403, forbidden.text)


if __name__ == "__main__":
    unittest.main()
