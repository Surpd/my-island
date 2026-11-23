from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.handlers.codex_diagnostic_api import create_codex_diagnostic_router
from backend.services.codex_diagnostics import authorize, revoke
from backend.services.schedule_experiments import create_experiment


class CodexDiagnosticAuthTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(str(Path(temp.name) / "synthetic.sqlite3"))
        self.db.initialize()
        self.secret = token_urlsafe(32)  # Disposable, generated only for this test process.
        self.settings = patch.dict(os.environ, {
            "MY_ISLAND_CODEX_DIAGNOSTICS": "1",
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": sha256(self.secret.encode()).hexdigest(),
        })
        self.settings.start()
        self.addCleanup(self.settings.stop)
        app = FastAPI()
        app.include_router(create_codex_diagnostic_router(self.db))
        app.include_router(create_router(self.db))
        self.http = TestClient(app)
        self.addCleanup(self.http.close)

    def login(self):
        response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": self.secret})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        payload = response.json()
        self.assertEqual(payload["scope"], "diagnostic.read")
        return {"Authorization": "Bearer " + payload["token"]}, payload["token"]

    def test_unconfigured_or_malformed_configuration_fails_closed(self):
        for changes in ({"MY_ISLAND_CODEX_DIAGNOSTICS": ""}, {"MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": "bad"}):
            with patch.dict(os.environ, changes):
                self.assertEqual(self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": self.secret}).status_code, 404)
                self.assertEqual(self.http.get("/api/diagnostics/view/overview").status_code, 404)

    def test_wrong_or_url_key_never_authenticates(self):
        response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": token_urlsafe(32)})
        self.assertEqual(response.status_code, 401)
        response = self.http.post("/api/diagnostics/exchange", json={"key": self.secret})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(self.secret, response.text)

    def test_real_read_projections_never_return_pupil_names_or_rosters(self):
        self.db.create_identity("student", "Synthetic Pupil Private Name", "Forbidden class name")
        self.db.create_identity("teacher", "Synthetic Teacher Private Name", "")
        self.db.create_group("Synthetic Group Secret Name", "class", display_name="Synthetic Group Secret Name", base_class_name="7-А")
        headers, token = self.login()
        cases = ("overview", "system", "groups", "school/directory", "people?kind=teacher",
                 "schedule/experiments/capabilities", "schedule/experiments/school/directory", "previews/people", "journals",
                 "schedule/overview?week_start=2026-11-16")
        for path in cases:
            response = self.http.get("/api/diagnostics/view/" + path, headers=headers)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text}")
            for field in ("Synthetic ", "Private Name", "Forbidden class name"):
                self.assertNotIn(field, response.text)
            self.assertIn("no-store", response.headers["Cache-Control"])
        rows = self.http.get("/api/diagnostics/view/school/directory", headers=headers).json()
        self.assertEqual(rows["students"], [])
        self.assertEqual(rows["teachers"][0]["display_name"], "Преподаватель 1")
        self.assertEqual(rows["groups"][0]["display_name"], "7-А")
        self.assertEqual(self.http.get("/api/diagnostics/view/people?kind=student", headers=headers).status_code, 403)
        self.assertIsNone(authorize(self.db, token, scope="journal.import"))

    def test_draft_and_journal_detail_only_project_allowlisted_fields(self):
        class_id = str(self.db.create_group("Internal Name To Hide", "class", base_class_name="9-А")["id"])
        draft = create_experiment(self.db, name="Secret Draft Name To Hide", source="import", payload={
            "draft_kind": "school", "days": [0], "slots": [{"start": "09:00", "end": "09:45"}], "classes": [class_id],
            "lessons": [{"id": "Potentially-Identifying-Raw-Lesson-Id", "day": 0, "period": 0, "classId": class_id,
                         "subject": "Математика", "teacherId": "private-teacher", "connectionInfo": "private-url",
                         "audience": {"kind": "students", "student_ids": ["private-pupil"]}, "importError": "private-error"}],
        })
        with self.db.connection() as connection:
            self.db.execute(connection, """INSERT INTO journal_sources(spreadsheet_id,spreadsheet_title,grade,subject,sheet_title,term_number)
                            VALUES ('private-sheet-link','private-book-title',9,'Математика','private-tab-title',2)""")
        headers, _ = self.login()
        for path, hidden in (("schedule/experiments/school", "Secret Draft Name"), ("schedule/experiments/school/" + draft["id"], "private-pupil"),
                             ("journals", "private-sheet-link")):
            response = self.http.get("/api/diagnostics/view/" + path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(hidden, response.text)
            self.assertNotIn("private", response.text)
        schedule = self.http.get("/api/diagnostics/view/schedule/experiments/school/" + draft["id"], headers=headers).json()
        self.assertEqual(schedule["payload"]["lessons"][0]["classIds"], [class_id])
        self.assertEqual(schedule["payload"]["lessons"][0]["subject"], "Математика")
        source = self.http.get("/api/diagnostics/view/journals", headers=headers).json()["items"][0]
        payload = self.http.get("/api/diagnostics/view/journals/" + source["id"] + "/detail", headers=headers).json()
        self.assertEqual(payload["source"]["term_number"], 2)
        self.assertEqual(payload["students"], [])
        self.assertEqual(payload["assessments"], [])

    def test_mutations_and_side_effectful_gets_are_not_dispatched(self):
        headers, token = self.login()
        for method, path in (
            ("POST", "journals/connect"), ("PUT", "schedule/experiments/school/example"),
            ("GET", "schedule/experiments/school/example/export/link"),
            ("GET", "google/reconnect"), ("POST", "previews"),
            ("GET", "journals/matching?spreadsheet_id=synthetic"),
            ("GET", "people/private-pupil"), ("GET", "integrations/google/callback"),
        ):
            response = self.http.request(method, "/api/diagnostics/view/" + path, headers=headers)
            self.assertIn(response.status_code, (403, 405))
        response = self.http.get("/api/admin/auth/session", headers={"X-Dev-Auth": "browser:" + token})
        self.assertEqual(response.status_code, 401)
        actor = self.db.get_or_create_user(777001, "admin")
        with self.db.connection() as connection:
            self.db.execute(connection, "INSERT INTO user_roles(user_id,role,source) VALUES (?,'admin','synthetic')", (actor["id"],))
        code, _ = self.db.create_admin_login_challenge(actor["id"])
        normal = self.http.post("/api/admin/auth/exchange", json={"code": code})
        self.assertEqual(normal.status_code, 200, normal.text)
        normal_token = normal.json()["browser_session_token"]
        self.assertEqual(self.http.get("/api/admin/overview", headers={"X-Dev-Auth": "browser:" + normal_token}).status_code, 200)
        self.assertEqual(self.http.get("/api/diagnostics/view/overview", headers={"Authorization": "Bearer " + normal_token}).status_code, 401)

    def test_logout_operator_revocation_expiry_key_rotation_and_audit(self):
        header, token = self.login()
        self.assertEqual(self.http.get("/api/diagnostics/view/overview", headers=header).status_code, 200)
        first_id = authorize(self.db, token)
        self.assertTrue(first_id)
        self.assertEqual(self.http.post("/api/diagnostics/logout", headers=header).status_code, 200)
        self.assertEqual(self.http.get("/api/diagnostics/session", headers=header).status_code, 401)
        second_header, second_token = self.login()
        self.assertTrue(revoke(self.db, authorize(self.db, second_token)))
        self.assertEqual(self.http.get("/api/diagnostics/session", headers=second_header).status_code, 401)
        expired, expired_token = self.login()
        with self.db.connection() as connection:
            ts = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            self.db.execute(connection, "UPDATE codex_diagnostic_sessions SET expires_at=? WHERE id=?",
                            (ts, authorize(self.db, expired_token)))
            audit = self.db.execute(connection, "SELECT actor_user_id,action,details FROM audit_log WHERE entity_type='codex_diagnostic_session'").fetchall()
            rows = self.db.execute(connection, "SELECT token_hash,credential_epoch FROM codex_diagnostic_sessions").fetchall()
        self.assertEqual(self.http.get("/api/diagnostics/session", headers=expired).status_code, 401)
        rotated, _ = self.login()
        with patch.dict(os.environ, {"MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": "b" * 64}):
            self.assertEqual(self.http.get("/api/diagnostics/session", headers=rotated).status_code, 401)
        self.assertTrue(all(row["actor_user_id"] is None for row in audit))
        self.assertTrue(any(row["action"] == "codex_diagnostics.read" for row in audit))
        self.assertEqual(sum(row["action"] == "codex_diagnostics.revoked" for row in audit), 2)
        self.assertTrue(all(token not in row["details"] and self.secret not in row["details"] for row in audit))
        self.assertTrue(all(token not in row["token_hash"] and self.secret not in row["credential_epoch"] for row in rows))
