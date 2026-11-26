from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.database import Database
from backend.services.google_sync_service import refresh_grade_journal
from backend.services.journal_import import subject_period_from_title, sync_journal_values


VALUES = [["Дата", "01.09"], ["Задание", "Проверочная"], ["Вес", 1], ["Оценка", 10],
          ["Ученик", 8]]


class JournalPeriodPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Database(Path(self.temp.name) / "periods.db")
        self.database.initialize()

    def test_half_year_titles_keep_wire_term_numbers(self):
        cases = {
            "I полугодие · Алгебра": ("Алгебра", 1),
            "2 полугодие · Физика": ("Физика", 2),
            "Химия — I полугодие": ("Химия", 1),
            "История (полугодие II)": ("История", 2),
            "Литература — 2 семестр": ("Литература", 2),
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(subject_period_from_title(title), expected)

    def test_grade_ten_preserves_prior_binding_on_unscoped_sync_and_refuses_third_period(self):
        common = {"spreadsheet_id": "half-year-book", "spreadsheet_title": "10 класс", "grade": 10,
                  "subject": "Алгебра", "sheet_id": 17}
        sync_journal_values(self.database, VALUES, sheet_title="I полугодие · Алгебра", **common)
        original = self.database.journal_status()[0]
        self.assertEqual(original["term_number"], 1)

        sync_journal_values(self.database, VALUES, sheet_title="Алгебра", **common)
        preserved = self.database.journal_status()[0]
        self.assertEqual((preserved["id"], preserved["term_number"]), (original["id"], 1))
        with self.assertRaisesRegex(ValueError, "системе обучения"):
            sync_journal_values(self.database, VALUES, sheet_title="III триместр · Алгебра",
                                **{**common, "sheet_id": 18})
        self.assertEqual(len(self.database.journal_status()), 1)
        self.assertFalse(self.database.set_journal_source_term(original["id"], 3))
        self.assertFalse(self.database.set_journal_current_term("half-year-book", 10, "2026/27", 3))

    def test_scoped_third_period_is_rejected_before_google_read(self):
        with patch("backend.services.google_sync_service._client") as client:
            with self.assertRaisesRegex(ValueError, "недоступный учебный период"):
                refresh_grade_journal(self.database, object(), 11, spreadsheet_id="grade-eleven-book",
                                      term_number=3, dry_run=True)
            client.assert_not_called()

    def test_grade_nine_keeps_three_trimesters(self):
        sync_journal_values(self.database, VALUES, spreadsheet_id="trimester-book", spreadsheet_title="9 класс",
                            grade=9, subject="История", sheet_title="III триместр · История", sheet_id=3)
        source = self.database.journal_status()[0]
        self.assertEqual(source["term_number"], 3)
        self.assertTrue(self.database.set_journal_current_term("trimester-book", 9, "2026/27", 3))


if __name__ == "__main__":
    unittest.main()
