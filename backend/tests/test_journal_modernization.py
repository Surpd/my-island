from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.database import Database
from backend.services.google_sync_service import refresh_grade_journal, refresh_journal
from backend.services.journal_import import deferred_grade7_second_period_subject, sync_journal_values


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

    def test_grade7_second_period_math_is_skipped_but_other_subject_preflight_continues(self):
        self.assertTrue(deferred_grade7_second_period_subject("II триместр · Математика + Физика"))
        self.assertFalse(deferred_grade7_second_period_subject("I триместр · Математика + Физика"))
        self.metadata((1, "II триместр · Математика"), (2, "II триместр · Биология"))
        preview = refresh_grade_journal(self.database, object(), 7, spreadsheet_id="grade7-modern",
                                        term_number=2, dry_run=True)
        by_id = {str(row["sheet_id"]): row for row in preview["tabs"]}
        self.assertEqual(by_id["1"]["status"], "skipped")
        self.assertEqual(by_id["1"]["classification"], "deferred_grade7_period2")
        self.assertEqual(by_id["2"]["status"], "success")
        self.assertEqual([call.args[1] for call in self.google.sheet_values.call_args_list],
                         ["'II триместр · Биология'!A:ZZ"])
        self.assertEqual(self.database.journal_status(), [])

    def test_existing_bound_grade7_math_is_skipped_when_title_has_no_period(self):
        sync_journal_values(self.database, VALUES, spreadsheet_id="grade7-modern", spreadsheet_title="7 класс",
                            grade=7, subject="Математика", sheet_title="I триместр · Математика", sheet_id=1)
        with self.database.connection() as connection:
            self.database.execute(connection, """UPDATE journal_sources SET term_number=2,
                subject='Математика', sheet_title='II триместр · Математика' WHERE spreadsheet_id=? AND sheet_id='1'""",
                ("grade7-modern",))
        before = self.database.journal_status()
        self.metadata((1, "Математика"))
        result = refresh_grade_journal(self.database, object(), 7, spreadsheet_id="grade7-modern")
        self.assertEqual(result["tabs"][0]["status"], "skipped")
        self.assertEqual(result["tabs"][0]["classification"], "deferred_grade7_period2")
        self.google.sheet_values.assert_not_called()
        self.assertEqual(self.database.journal_status(), before)

    def test_single_source_and_direct_sync_cannot_refresh_bound_grade7_second_period_math(self):
        sync_journal_values(self.database, VALUES, spreadsheet_id="grade7-modern", spreadsheet_title="7 класс",
                            grade=7, subject="Математика/Физика", sheet_title="I · Математика/Физика", sheet_id=1)
        with self.database.connection() as connection:
            self.database.execute(connection, """UPDATE journal_sources SET term_number=2,
                subject='Математика/Физика', sheet_title='II · Математика/Физика' WHERE spreadsheet_id=? AND sheet_id='1'""",
                ("grade7-modern",))
        before = self.database.journal_detail(self.database.journal_status()[0]["id"])
        self.metadata((1, "II · Математика/Физика"))

        result = refresh_journal(self.database, object(), 7, "II · Математика/Физика",
                                 sheet_id=1, spreadsheet_id="grade7-modern")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["classification"], "deferred_grade7_period2")
        self.google.sheet_values.assert_not_called()
        self.assertEqual(self.database.journal_detail(before["source"]["id"]), before)

        with self.assertRaisesRegex(ValueError, "временно отложен"):
            sync_journal_values(self.database, VALUES, spreadsheet_id="grade7-modern", spreadsheet_title="7 класс",
                                grade=7, subject="Математика/Физика", sheet_title="II · Математика/Физика",
                                sheet_id=1, expected_term=2)
        self.assertEqual(self.database.journal_detail(before["source"]["id"]), before)

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
