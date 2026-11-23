from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
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
from backend.services.codex_diagnostics import authorize, log_startup_readiness, revoke
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
        os.environ.pop("MY_ISLAND_CODEX_DIAGNOSTIC_KEY", None)
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

    def test_startup_uses_only_one_fixed_reason_and_never_logs_inputs(self):
        verifier = sha256(self.secret.encode()).hexdigest()
        logger = logging.getLogger("codex-diagnostic-startup-fixture")
        for flag, raw, configured_hash, status in (
            ("0", self.secret, verifier, "flag_off"),
            ("1", None, "", "no_key_configured"),
            ("1", "", "", "raw_empty"),
            ("1", "x", "", "enabled"),
            ("1", self.secret + "\n", "", "raw_invalid_characters"),
            ("1", self.secret, "synthetic-malformed-digest", "digest_malformed"),
            ("1", self.secret, "0" * 64, "verifiers_mismatch"),
            ("1", self.secret, verifier, "enabled"),
            ("1", None, verifier, "enabled"),
        ):
            with self.subTest(status=status), patch.dict(os.environ, {
                "MY_ISLAND_CODEX_DIAGNOSTICS": flag,
                "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": configured_hash,
            }):
                if raw is None:
                    os.environ.pop("MY_ISLAND_CODEX_DIAGNOSTIC_KEY", None)
                else:
                    os.environ["MY_ISLAND_CODEX_DIAGNOSTIC_KEY"] = raw
                with self.assertLogs(logger, level="INFO") as captured:
                    log_startup_readiness(logger)
                self.assertEqual([record.getMessage() for record in captured.records],
                                 ["Codex diagnostic startup: " + status])
                self.assertNotIn(self.secret, captured.output[0])
                self.assertNotIn(verifier, captured.output[0])

    def test_raw_config_uses_same_credential_epoch_and_no_secret_is_returned(self):
        headers, token = self.login()
        verifier = sha256(self.secret.encode()).hexdigest()
        for configured_hash in (verifier, ""):
            with patch.dict(os.environ, {
                "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": self.secret,
                "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": configured_hash,
            }):
                self.assertEqual(self.http.get("/api/diagnostics/session", headers=headers).status_code, 200)
                response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": self.secret})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(self.secret, response.text)
                self.assertNotIn(verifier, response.text)
                self.assertEqual(set(response.json()), {"token", "expires_at", "scope"})
        with self.db.connection() as connection:
            audit = connection.execute("SELECT details FROM audit_log WHERE entity_type='codex_diagnostic_session'").fetchall()
            stored = connection.execute("SELECT credential_epoch, token_hash FROM codex_diagnostic_sessions").fetchall()
        self.assertTrue(all(self.secret not in row["details"] and verifier not in row["details"] for row in audit))
        self.assertTrue(all(self.secret not in row["credential_epoch"] and verifier != row["credential_epoch"]
                            and token != row["token_hash"] for row in stored))

    def test_invalid_or_mismatched_raw_config_fails_closed_without_using_hash_fallback(self):
        cases = ("", "short", self.secret + "\n", token_urlsafe(32))
        for raw in cases:
            with patch.dict(os.environ, {"MY_ISLAND_CODEX_DIAGNOSTIC_KEY": raw}):
                response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": self.secret})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(self.secret, response.text)
                self.assertEqual(self.http.get("/api/diagnostics/session").status_code, 404)

    def test_short_exact_key_and_shared_throttle_leave_active_session_usable(self):
        # A second independent client and Database object represent another worker.
        second_app = FastAPI()
        second_app.include_router(create_codex_diagnostic_router(Database(self.db.path)))
        with TestClient(second_app) as second, patch.dict(os.environ, {
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": "x",
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": "",
        }), patch("backend.services.codex_diagnostics.time.time", return_value=1_000_000) as clock:
            endpoint = "/api/diagnostics/exchange"
            key_header = "X-My-Island-Diagnostic-Key"
            good = second.post(endpoint, headers={key_header: "x"})
            self.assertEqual(good.status_code, 200)
            bearer = {"Authorization": "Bearer " + good.json()["token"]}
            for index, client in enumerate((self.http, second, self.http, second)):
                attempt = client.post(endpoint, headers={
                    key_header: "X", "X-Forwarded-For": f"192.0.2.{index}", "X-Real-IP": f"192.0.2.{index}",
                })
                self.assertEqual(attempt.status_code, 401)
            self.assertEqual(second.post(endpoint, headers={key_header: "x"}).status_code, 429)
            self.assertEqual(self.http.post(endpoint, headers={key_header: "X"}).status_code, 429)
            self.assertEqual(second.get("/api/diagnostics/session", headers=bearer).status_code, 200)
            self.assertEqual(second.get("/api/diagnostics/view/overview", headers=bearer).status_code, 200)
            clock.return_value += 900
            self.assertEqual(self.http.post(endpoint, headers={key_header: "x"}).status_code, 200)
        with self.db.connection() as connection:
            rows = connection.execute("SELECT * FROM codex_diagnostic_exchange_budget").fetchall()
        self.assertEqual([dict(row) for row in rows], [{"id": 1, "attempts": 1, "reset_at": 1_001_800}])

    def test_visible_ascii_punctuation_key_exchanges_without_normalization(self):
        raw = self.secret + "".join(chr(code) for code in range(33, 127) if not chr(code).isalnum())
        digest = sha256(raw.encode()).hexdigest()
        with patch.dict(os.environ, {
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": raw,
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": digest,
        }):
            response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": raw})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"token", "expires_at", "scope"})
            self.assertNotIn(raw, response.text)
            wrong = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": raw[:-1]})
            self.assertEqual(wrong.status_code, 401)
        for extra in (" ", "\t", "\r", "\n", "\x7f", "é"):
            for invalid in (raw + extra, extra + raw, raw[:14] + extra + raw[14:]):
                with patch.dict(os.environ, {
                    "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": invalid,
                    "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": digest,
                }):
                    response = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": raw})
                    self.assertEqual(response.status_code, 404)

    def test_rotating_raw_key_revokes_existing_tokens_and_flag_off_stays_closed(self):
        with patch.dict(os.environ, {
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": self.secret,
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": "",
        }):
            session, _ = self.login()
        next_key = token_urlsafe(32)
        with patch.dict(os.environ, {
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY": next_key,
            "MY_ISLAND_CODEX_DIAGNOSTIC_KEY_SHA256": "",
        }):
            self.assertEqual(self.http.get("/api/diagnostics/session", headers=session).status_code, 401)
            self.assertEqual(self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": self.secret}).status_code, 401)
            new_session = self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": next_key})
            self.assertEqual(new_session.status_code, 200)
            with patch.dict(os.environ, {"MY_ISLAND_CODEX_DIAGNOSTICS": "0"}):
                self.assertEqual(self.http.post("/api/diagnostics/exchange", headers={"X-My-Island-Diagnostic-Key": next_key}).status_code, 404)

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
