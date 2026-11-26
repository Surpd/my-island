from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.database import Database
from backend.services.google_sync_service import JournalSourceExcluded, refresh_grade_journal, refresh_journal
from backend.services.journal_import import sync_journal_values


VALUES = [["Дата", "25.11"], ["Задание", "Работа"], ["Вес", 1], ["Оценка", 5],
          ["Ученик Пример", 4]]


class JournalModernizationSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Database(Path(self.temp.name) / "modern.db")
        self.database.initialize()
        self.google = Mock()
        live = patch("backend.services.google_sync_service._client", return_value=(self.google, None))
        live.start()
        self.addCleanup(live.stop)

    def metadata(self, *tabs):
        self.google.spreadsheet.return_value = {"properties": {"title": "7 класс"}, "sheets": [
            {"properties": {"sheetId": sheet_id, "title": title}} for sheet_id, title in tabs]}
        self.google.sheet_values.return_value = VALUES

    def test_grade7_second_period_math_and_physics_can_be_selected_and_connected(self):
        self.metadata((1, "II триместр · Математика"), (2, "II триместр · Физика"))
        preview = refresh_grade_journal(self.database, object(), 7, spreadsheet_id="grade7-modern",
                                        term_number=2, dry_run=True)
        self.assertEqual([row["status"] for row in preview["tabs"]], ["success", "success"])
        self.assertEqual(self.database.journal_status(), [])
        applied = refresh_grade_journal(self.database, object(), 7, spreadsheet_id="grade7-modern",
                                        term_number=2, preflight_token=preview["preflight_token"],
                                        selected_sheet_ids=["1", "2"])
        self.assertEqual(applied["successful_tabs"], 2)
        self.assertEqual({source["subject"] for source in self.database.journal_status()}, {"Математика", "Физика"})

    def test_excluded_combined_source_preserves_snapshot_and_can_resume_in_manual_mode(self):
        book = "grade7-modern"
        label = "II · Математика/Физика"
        sync_journal_values(self.database, VALUES, spreadsheet_id="grade7-modern", spreadsheet_title="7 класс",
                            grade=7, subject="Математика/Физика", sheet_title=label, sheet_id=1)
        source_id = self.database.journal_status()[0]["id"]
        self.database.set_journal_current_term(book, 7, "2026/27", 2)
        self.database.set_journal_source_mode(source_id, "excluded")
        before = self.database.journal_detail(source_id)
        self.metadata((1, label))
        for kind in ("manual", "scheduled"):
            result = refresh_grade_journal(self.database, object(), 7, spreadsheet_id=book, term_number=2,
                                           trigger_kind=kind)
            self.assertEqual(result["successful_tabs"], 0)
        with self.assertRaises(JournalSourceExcluded):
            refresh_journal(self.database, object(), 7, label, sheet_id=1, spreadsheet_id=book)
        with self.assertRaisesRegex(ValueError, "Вкладка отключена"):
            sync_journal_values(self.database, VALUES, spreadsheet_id=book, spreadsheet_title="7 класс",
                                grade=7, subject=label, sheet_title=label, sheet_id=1, expected_term=2)
        self.google.sheet_values.assert_not_called()
        self.assertEqual(self.database.journal_detail(source_id), before)
        self.database.set_journal_source_mode(source_id, "manual")
        scheduled = refresh_grade_journal(self.database, object(), 7, spreadsheet_id=book,
                                          term_number=2, trigger_kind="scheduled")
        self.assertEqual(scheduled["successful_tabs"], 0)
        self.google.sheet_values.assert_not_called()
        self.metadata((1, "Математика/Физика"))
        result = refresh_journal(self.database, object(), 7, label, sheet_id=1, spreadsheet_id=book)
        self.assertEqual(result["counts"]["assessments"], 1)
        self.assertEqual(self.database.journal_detail(source_id)["source"]["term_number"], 2)
        self.assertEqual(self.database.journal_detail(source_id)["assessments"], before["assessments"])

    def test_scheduled_explicit_period_uses_only_connected_auto_sync_tabs(self):
        sync_journal_values(self.database, VALUES, spreadsheet_id="grade8-modern", spreadsheet_title="8 класс",
                            grade=8, subject="Биология", sheet_title="II триместр · Биология", sheet_id=2)
        self.assertTrue(self.database.set_journal_auto_sync("grade8-modern", True))
        self.metadata((2, "II триместр · Биология"), (3, "II триместр · История"))
        result = refresh_grade_journal(self.database, object(), 8, spreadsheet_id="grade8-modern",
                                       trigger_kind="scheduled", term_number=2)
        self.assertEqual(result["status"], "success")
        self.assertEqual([row["sheet_id"] for row in result["tabs"]], ["2"])
        self.assertEqual([call.args[1] for call in self.google.sheet_values.call_args_list],
                         ["'II триместр · Биология'!A:ZZ"])


if __name__ == "__main__":
    unittest.main()
