import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.database import Database
from backend.services.google_sync_service import JournalSourceExcluded, refresh_grade_journal, refresh_journal


class JournalSourceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "journal.db")
        self.database.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_scheduled_sync_skips_manual_and_excluded_tabs_before_reading_values(self):
        client = Mock()
        client.spreadsheet.return_value = {
            "properties": {"title": "Synthetic"},
            "sheets": [
                {"properties": {"title": "Auto", "sheetId": 10}},
                {"properties": {"title": "Manual", "sheetId": 20}},
                {"properties": {"title": "Excluded", "sheetId": 30}},
            ],
        }
        client.sheet_values.return_value = [["rows"]]
        settings = {
            "10": {"sheet_id": "10", "auto_sync_enabled": True, "excluded_from_journals": False},
            "20": {"sheet_id": "20", "auto_sync_enabled": False, "excluded_from_journals": False},
            "30": {"sheet_id": "30", "auto_sync_enabled": True, "excluded_from_journals": True},
        }
        with patch("backend.services.google_sync_service._client", return_value=(client, None)), patch(
            "backend.services.google_sync_service.load_source_settings", return_value=settings
        ), patch("backend.services.google_sync_service.sync_journal_values", return_value={"assessments": 1}):
            result = refresh_grade_journal(
                self.database, object(), 9, spreadsheet_id="book-settings", trigger_kind="scheduled"
            )

        self.assertEqual(client.sheet_values.call_count, 1)
        self.assertEqual(client.sheet_values.call_args.args[1], "'Auto'!A:ZZ")
        self.assertEqual(result["skipped_tabs"], 1)
        self.assertEqual(result["excluded_tabs"], 1)
        self.assertEqual([tab["status"] for tab in result["tabs"]], ["success", "skipped", "excluded"])

    def test_single_source_refresh_uses_requested_spreadsheet_and_blocks_excluded_tab(self):
        client = Mock()
        client.spreadsheet.return_value = {
            "properties": {"title": "Synthetic"},
            "sheets": [{"properties": {"title": "Actual title", "sheetId": 42}}],
        }
        with patch("backend.services.google_sync_service._client", return_value=(client, None)), patch(
            "backend.services.google_sync_service.load_source_settings",
            return_value={"42": {"sheet_id": "42", "excluded_from_journals": True}},
        ), patch("backend.services.google_sync_service.sync_journal_values") as importer:
            with self.assertRaises(JournalSourceExcluded):
                refresh_journal(self.database, object(), 9, subject="Outdated title", sheet_id=42, spreadsheet_id="custom-book")

        client.spreadsheet.assert_called_once_with("custom-book")
        client.sheet_values.assert_not_called()
        importer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
