from unittest import TestCase
from unittest.mock import patch

from backend.tests import test_admin_previews as fixtures


class ClassReportRoutesTests(TestCase):
    setUp = fixtures.AdminPreviewRoutesTests.setUp
    tearDown = fixtures.AdminPreviewRoutesTests.tearDown

    def test_admin_can_read_week_coverage(self):
        with patch.dict("os.environ", {"SCHEDULE_BACKEND": "canonical"}):
            response = self.client.get("/api/admin/analytics/classes?start_day=2026-09-07", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["coverage"]["weeks"][0]["origin"], "missing")

    def test_class_report_access_errors_and_date_boundaries(self):
        import sqlite3
        path = "/api/admin/analytics/classes?start_day=2026-01-01"
        self.assertEqual(self.client.get(path).status_code, 401)
        with patch("backend.handlers.api.resolve_auth", return_value={"id": 88002}):
            self.assertEqual(self.client.get(path).status_code, 403)
        with patch.dict("os.environ", {"SCHEDULE_BACKEND": "disabled"}):
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 503)
        with patch.dict("os.environ", {"SCHEDULE_BACKEND": "canonical"}):
            for end in ("2025-12-31", "2027-01-02", "2026-02-30"):
                self.assertEqual(self.client.get(path+"&end_day="+end, headers=self.headers).status_code, 400)
            with patch("backend.handlers.api.class_load_analytics", side_effect=sqlite3.OperationalError()):
                self.assertEqual(self.client.get(path, headers=self.headers).status_code, 503)
