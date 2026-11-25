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

    def test_activity_only_tracks_observed_value_changes(self):
        values = [row.copy() for row in FIRST]
        values.append(["Second Fixture", 5])
        self.source(11, "Алгебра", values)
        source_id = self.db.journal_status()[0]["id"]
        self.source(11, "Алгебра", values)
        values[-2][1] = 0
        self.source(11, "Алгебра", values)
        values[-2][1] = ""
        self.source(11, "Алгебра", values)
        self.source(11, "Алгебра", values)
        values[-2][1] = 0
        self.source(11, "Алгебра", values)
        counts = {row["change_kind"]: row["cell_count"] for row in self.db.journal_cell_activity(source_id)["items"]}
        self.assertEqual(counts, {"added": 3, "changed": 1, "removed": 1})

    def refresh(self, **kwargs):
        return refresh_grade_journal(self.db, object(), 6, spreadsheet_id=BOOK, **kwargs)

    def pick(self, *ids, preview=None):
        preview = preview or self.refresh(term_number=2, dry_run=True)
        return self.refresh(term_number=2, selected_sheet_ids=list(ids), preflight_token=preview.get("preflight_token"))

    def test_scoped_refresh_includes_bound_unnumbered_tabs_and_ignores_disabled_tabs(self):
        self.source(1, "Алгебра")
        self.source(2, "I · Физика")
        disabled = next(row for row in self.rows() if str(row["sheet_id"]) == "2")
        self.db.set_journal_source_mode(disabled["id"], "excluded")
        self.tabs((1, "Алгебра"), (2, "I · Физика"), (3, "Новая вкладка"))
        self.google.sheet_values.side_effect = None
        self.google.sheet_values.return_value = FIRST

        result = self.refresh(term_number=1)

        self.assertEqual(result["successful_tabs"], 1)
        self.assertEqual(result["failed_tabs"], 0)
        self.assertEqual([tab["sheet_id"] for tab in result["tabs"]], ["1"])
        self.google.sheet_values.assert_called_once()

    def test_group_label_conflict_is_read_only_and_other_tab_remains_selectable(self):
        label = "II · Английский язык"
        names = ("Synthetic Alpha", "Synthetic Beta")
        def english(headers, roster=names):
            values = []
            for index, (heading, name) in enumerate(zip(headers, roster), 1):
                values += [["Дата", heading, f"0{index}.12"], ["Работа", "", "Тренировка"],
                           ["Вес", "", 1], ["Категория", "", "У"], [name, 4, 9]]
            return values

        self.source(2, label, english(("", "")))
        source_id = self.rows()[0]["id"]
        previous = self.db.journal_detail(source_id)
        self.tabs((2, label), (3, "II · Биология"))
        labels = ("II триместр · Английский 1", "II триместр · Английский 2")
        def use(values):
            self.google.sheet_values.side_effect = lambda book, address, **kwargs: values if "Английский" in address else SECOND

        use(english(labels, names[::-1]))
        preview = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(preview["status"], "partial")
        self.assertEqual(preview["tabs"][0]["error_code"], "journal_group_label_conflict")
        self.assertEqual(preview["tabs"][1]["status"], "success")
        self.assertEqual(self.db.journal_detail(source_id), previous)
        self.assertEqual(self.pick("2", preview=preview)["status"], "blocked")
        self.assertEqual(self.pick("3", preview=preview)["status"], "success")
        self.assertEqual(self.db.journal_detail(source_id), previous)

        use(english(labels))
        preview = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(preview["status"], "success")
        self.assertEqual(self.db.journal_detail(source_id), previous)
        self.assertEqual(self.pick("2", preview=preview)["status"], "success")
        adopted = self.db.journal_detail(source_id)
        self.assertEqual(adopted["group_labels"], {"block 1": "Английский 1", "block 2": "Английский 2"})
        for key in ("groups", "students", "assessments", "mappings"):
            self.assertEqual(adopted[key], previous[key])

    def test_cross_period_and_guide_are_never_selectable_and_cannot_poison_first_term(self):
        self.source(1, "I · Биология")
        before = self.rows()
        self.tabs((1, "II · Биология"), (2, "II · История"), (3, "II · Как работать"))
        dry = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(dry["status"], "partial")
        self.assertEqual(dry["tabs"][0]["error_code"], "period_conflict")
        self.assertEqual([row["sheet_id"] for row in dry["tabs"]], ["1", "2"])
        for ids in ([], ["1"], ["2", "3"], ["2", "2"], ["999"]):
            commit = self.pick(*ids, preview=dry)
            self.assertEqual(commit["status"], "blocked")
            self.assertEqual(self.rows(), before)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM journal_sync_runs").fetchone()[0], 0)
        self.tabs((1, "II · Как работать"), (2, "II · История"))
        dry = self.refresh(term_number=2, dry_run=True)
        self.assertEqual([row["sheet_id"] for row in dry["tabs"]], ["2"])
        self.assertEqual(self.pick("1", preview=dry)["status"], "blocked")
        self.assertEqual(self.rows(), before)

    def test_fresh_commit_preflight_rejects_changed_structure_values_and_duplicates(self):
        self.tabs((1, "I · История"), (2, "II · История"))
        preview = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(preview["status"], "success")
        self.assertEqual(self.rows(), [])
        self.google.sheet_values.side_effect = lambda *args, **kwargs: [*SECOND[:-1], ["Fixture Only", "", 5]]
        self.assertEqual(self.pick("2", preview=preview)["status"], "blocked")
        self.tabs((1, "I · История"), (2, "II · История"), (3, "II · География"))
        self.google.sheet_values.side_effect = lambda book, address, **kwargs: SECOND if "История" in address else [["Wrong", 1]]
        result = self.pick("2", preview=preview)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([tab["status"] for tab in result["tabs"]], ["not_imported", "error"])
        self.assertEqual(self.rows(), [])
        self.tabs((2, "II · История"), (3, "II ·  История"))
        self.assertEqual([tab["error_code"] for tab in self.refresh(term_number=2, dry_run=True)["tabs"]],
                         ["duplicate_subject_period", "duplicate_subject_period"])
        self.assertEqual(self.rows(), [])
        self.tabs((2, "II · История"), (2, "I · Математика"))
        self.assertEqual(self.refresh(term_number=2, dry_run=True)["tabs"][0]["error_code"], "sheet_identity_conflict")
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
            assessment = self.pick("2", "3")
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
            result = self.pick("2", "3")
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

    def test_connected_refresh_failure_persists_diagnostic_only_on_real_run(self):
        self.source(2, "II · История", SECOND)
        source = self.rows()[0]
        before = self.db.journal_detail(source["id"])
        self.tabs((2, "II · История"))
        self.google.sheet_values.side_effect = None
        self.google.sheet_values.return_value = [["Нераспознаваемая структура", 1]]

        preview = self.refresh(term_number=2, dry_run=True)
        self.assertEqual(preview["tabs"][0]["status"], "error")
        after_preview = self.db.journal_detail(source["id"])
        self.assertEqual(after_preview["source"]["status"], "ready")
        self.assertIsNone(after_preview["source"]["last_error"])
        self.assertEqual(after_preview["assessments"], before["assessments"])

        result = self.refresh(term_number=2)
        self.assertEqual(result["status"], "blocked")
        failed = self.db.journal_detail(source["id"])
        self.assertEqual(failed["source"]["status"], "error")
        self.assertTrue(failed["source"]["last_error"])
        self.assertTrue(failed["source"]["last_error_code"])
        self.assertEqual(failed["assessments"], before["assessments"])
        self.assertEqual(failed["source"]["id"], source["id"])

    def test_partial_choice_refreshes_only_connected_tabs_and_keeps_omitted_existing_source(self):
        self.source(1, "I · История")
        self.source(6, "II · История: архивный курс", SECOND)
        old = {row["sheet_id"]: row for row in self.rows()}
        self.tabs((1, "I · История"), (2, "II · История"), (3, "II · История: практика"), (4, "II · Как работать"), (6, "II · История: архивный курс"))
        preview = self.refresh(term_number=2, dry_run=True)
        self.assertEqual([(row["sheet_id"], row["connected"]) for row in preview["tabs"]], [("2", False), ("3", False), ("6", True)])
        self.assertEqual(self.pick("2", preview=preview)["status"], "success")
        after = {row["sheet_id"]: row for row in self.rows()}
        self.assertEqual(set(after), {"1", "2", "6"})
        self.assertEqual(after["1"], old["1"])
        self.assertEqual(after["6"], old["6"])
        self.assertFalse(after["2"]["auto_sync_enabled"])
        self.google.sheet_values.reset_mock()
        self.assertEqual(self.refresh(term_number=2)["status"], "success")
        self.assertEqual([call.args[1] for call in self.google.sheet_values.call_args_list],
                         ["'II · История'!A:ZZ", "'II · История: архивный курс'!A:ZZ"])
        self.refresh(trigger_kind="scheduled")
        self.refresh()
        self.assertEqual({row["sheet_id"] for row in self.rows()}, {"1", "2", "6"})


    def test_retitle_during_fresh_read_leaves_no_run_or_source(self):
        self.tabs((2, "II · История"))
        check = self.refresh(term_number=2, dry_run=True)
        before = self.google.spreadsheet.return_value
        after = {"sheets": [{"properties": {"title": "II · История", "sheetId": 3}}]}
        self.google.spreadsheet.side_effect = [before, after]
        commit = self.pick("2", preview=check)
        self.assertEqual(commit["status"], "blocked")
        self.assertIn("изменились во время", commit["message"])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.db.journal_sync_history(BOOK), [])
