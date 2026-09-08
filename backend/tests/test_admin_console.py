import tempfile
import unittest
from pathlib import Path

from backend.database import Database


class AdminConsoleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "admin.db")
        self.database.initialize()
        with self.database.connection() as connection:
            user = self.database.execute(connection, "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id", (991001,)).fetchone()
            self.user_id = user["id"]
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (self.user_id,))

    def tearDown(self):
        self.temp.cleanup()

    def test_local_schema_reports_current_and_supports_additive_columns(self):
        health = self.database.system_health()
        self.assertEqual(health["schema_status"], "current")
        self.assertTrue(health["schema"]["groups"]["ok"])
        self.assertTrue(health["schema"]["schedule_entries"]["ok"])

    def test_browser_code_is_single_use_and_session_is_revocable(self):
        code, expires_at = self.database.create_admin_login_challenge(self.user_id)
        self.assertTrue(expires_at)
        session_token, user_id = self.database.consume_admin_login_challenge(code)
        self.assertEqual(user_id, self.user_id)
        self.assertIsNotNone(self.database.get_admin_browser_session(session_token))
        self.assertIsNone(self.database.consume_admin_login_challenge(code))
        self.database.revoke_admin_browser_session(session_token)
        self.assertIsNone(self.database.get_admin_browser_session(session_token))

    def test_reconciliation_run_requires_explicit_review(self):
        run_id = self.database.create_reconciliation_run(self.user_id, {"summary": {"CREATE": 0}, "mode": "read_only"})
        run = self.database.get_reconciliation_run(run_id)
        self.assertEqual(run["status"], "ready_for_review")
        reviewed = self.database.mark_reconciliation_reviewed(run_id, self.user_id)
        self.assertEqual(reviewed["status"], "approved")


if __name__ == "__main__":
    unittest.main()
