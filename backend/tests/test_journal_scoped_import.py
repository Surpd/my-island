import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.database import Database
from backend.services.google_sync_service import refresh_grade_journal
from backend.services.journal_import import sync_journal_values

BOOK = "synthetic-safe-trimester-book"
FIRST = [["Дата", "01.09"], ["Работа", "Архив"], ["Вес", 1], ["Оценка", 5], ["Fixture Only", 3]]
SECOND = [["Дата", "", "25.11"], ["Работа", "", "Отчёт"], ["Вес", "", 2],
          ["Категория (У/О/П)", "", "О"], ["Fixture Only", "", 4]]


class ScopedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "scoped.db")
        self.db.initialize()
        self.google = Mock()
        self.live = patch("backend.services.google_sync_service._client", return_value=(self.google, None))
        self.live.start()
        self.addCleanup(self.live.stop)

    def tabs(self, *items):
        self.google.spreadsheet.return_value = {"sheets": [
            {"properties": {"title": label, "sheetId": tab_id}} for tab_id, label in items]}
        self.google.sheet_values.side_effect = lambda book, address, **kwargs: FIRST if address.startswith("'I ·") else SECOND
        self.google.reset_mock()

    def rows(self):
        return self.db.journal_import_scope(BOOK, 6)["sources"]

    def source(self, tab_id, title, values=FIRST):
        return sync_journal_values(self.db, values, spreadsheet_id=BOOK, spreadsheet_title="Synthetic", grade=6,
                                   subject=title, sheet_title=title, sheet_id=tab_id)

    def refresh(self, **kwargs):
        return refresh_grade_journal(self.db, object(), 6, spreadsheet_id=BOOK, **kwargs)

    def test_cross_period_id_guide_retitle_blocks_entire_plan_without_marking_first_term(self):
        self.source(1, "I · Биология")
        before = self.rows()
        for label in ("II · Биология", "II · Как работать"):
            self.tabs((1, label), (2, "II · История"))
            dry = self.refresh(term_number=2, dry_run=True)
            self.assertEqual(dry["status"], "blocked")
            self.assertEqual(dry["tabs"][0]["error_code"], "period_conflict")
            commit = self.refresh(term_number=2)
            self.assertEqual(commit["status"], "blocked")
            self.assertEqual(commit["tabs"][1]["status"], "not_imported")
            self.assertEqual(self.rows(), before)

    def test_fresh_commit_preflight_rejects_changed_structure_and_duplicates(self):
        self.tabs((1, "I · История"), (2, "II · История"))
        self.assertEqual(self.refresh(term_number=2, dry_run=True)["status"], "success")
        self.assertEqual(self.rows(), [])
        self.tabs((1, "I · История"), (2, "II · История"), (3, "II · География"))
        self.google.sheet_values.side_effect = lambda book, address, **kwargs: SECOND if "История" in address else [["Wrong", 1]]
        result = self.refresh(term_number=2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([tab["status"] for tab in result["tabs"]], ["not_imported", "error"])
        self.assertEqual(self.rows(), [])
        self.tabs((2, "II · История"), (3, "II ·  История"))
        self.assertEqual([tab["error_code"] for tab in self.refresh(term_number=2)["tabs"]],
                         ["duplicate_subject_period", "duplicate_subject_period"])
        self.assertEqual(self.rows(), [])
        self.tabs((2, "II · История"), (2, "I · Математика"))
        self.assertEqual(self.refresh(term_number=2)["tabs"][0]["error_code"], "sheet_identity_conflict")
        self.assertEqual(self.rows(), [])

    def test_pilot_repeat_and_two_crons_do_not_advance_first_term_or_auto_import_second(self):
        self.source(1, "I · История")
        self.source(2, "II · История", SECOND)
        # Inconsistent legacy flag: even this known II tab cannot run while
        # workbook current-term setting has not been explicitly changed.
        self.db.set_journal_auto_sync(BOOK, True)
        self.tabs((1, "I · История"), (2, "II · История"), (3, "II · Биология"))
        for _ in range(2):
            result = self.refresh(trigger_kind="scheduled")
            self.assertEqual([tab["status"] for tab in result["tabs"]], ["success", "skipped", "skipped"])
        self.assertEqual([call.args[1] for call in self.google.sheet_values.call_args_list], ["'I · История'!A:ZZ"] * 2)
        self.assertFalse(any(row["sheet_id"] == "3" for row in self.rows()))
        for _ in range(2):
            assessment = self.refresh(term_number=2)
            self.assertEqual(assessment["status"], "success")
        by_id = {row["sheet_id"]: row for row in self.rows()}
        self.assertEqual({k: v["term_number"] for k, v in by_id.items()}, {"1": 1, "2": 2, "3": 2})
        self.assertEqual(by_id["1"]["period_status"], "current")
        self.assertFalse(by_id["3"]["auto_sync_enabled"])
        self.assertTrue(by_id["1"]["auto_sync_enabled"])
        self.assertEqual(self.db.journal_import_scope(BOOK, 6)["current_term"], 1)

    def test_partial_commit_reports_affected_tabs_only_and_keeps_first_period(self):
        self.source(1, "I · История")
        first = self.rows()[0]
        self.tabs((1, "I · История"), (2, "II · История"), (3, "II · Биология"))
        save = self.db.save_journal_snapshot

        def sometimes(source, *args):
            if source["sheet_id"] == "3":
                raise ValueError("конфликт сопоставления")
            return save(source, *args)

        with patch.object(self.db, "save_journal_snapshot", side_effect=sometimes):
            result = self.refresh(term_number=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["successful_tabs"], 1)
        self.assertEqual(result["failed_tabs"], 1)
        self.assertEqual(result["tabs"][1]["error_code"], "assessment_mapping_conflict")
        self.assertEqual([row for row in self.rows() if row["sheet_id"] == "1"], [first])
        self.assertFalse(any(row["sheet_id"] == "3" for row in self.rows()))

    def test_no_second_term_is_not_a_success_and_writes_nothing(self):
        self.tabs((1, "I · История"), (2, "II · Как работать"))
        dry = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(dry["status"], "blocked")
        self.assertEqual(self.refresh(term_number=2)["status"], "blocked")
        self.google.sheet_values.assert_not_called()
        self.assertEqual(self.rows(), [])
