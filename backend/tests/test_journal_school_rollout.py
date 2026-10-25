from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from contextlib import contextmanager

from backend.database import Database
from backend.scripts.journal_school_rollout import import_capture
from backend.services.journal_import import sync_journal_values


class JournalSchoolRolloutTests(unittest.TestCase):
    def test_empty_gradebook_roster_can_be_mapped_before_first_score(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'empty.db')
            database.initialize()
            sync_journal_values(database,[['Дата',''],['Задание',''],['Вес',''],['Оценка',''],['Student One','']],
                spreadsheet_id='empty',spreadsheet_title='Synthetic',grade=5,subject='Math',sheet_title='Math',sheet_id=1)
            source = database.journal_status()[0]
            self.assertEqual(source['result_count'],0)
            self.assertTrue(database.journal_group_marker_exists(source['id'],'all'))
            self.assertFalse(database.journal_group_marker_exists(source['id'],'foreign'))

    def test_scheduler_detection_executes_query_at_its_own_entry_point(self):
        database = Database(database_url='postgresql://synthetic')
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {'configured':True}
        @contextmanager
        def opened():
            yield connection
        with patch.object(database,'connection',opened):
            self.assertIs(database.journal_scheduler_configured(),True)
        self.assertIn('cron.job',connection.execute.call_args.args[0])

    def test_identical_columns_repeat_and_ambiguous_edit_preserve_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'repeat.db')
            database.initialize()
            values = [['Дата','01.09','01.09'],['Задание','Test','Test'],['Вес',1,1],['Оценка',10,10],['Student One',8,8]]
            def sync(rows):
                return sync_journal_values(database,rows,spreadsheet_id='synthetic',spreadsheet_title='Synthetic',grade=5,subject='Math',sheet_title='Math',sheet_id=1)
            sync(values)
            def state():
                with database.connection() as connection:
                    return [tuple(row) for row in connection.execute('SELECT id,assessment_id,numeric_score,is_current FROM journal_results ORDER BY id')]
            before = state()
            self.assertEqual(len(before),2)
            sync(values)
            self.assertEqual(state(),before)
            changed = [row[:] for row in values]
            changed[-1][1] = 9
            with self.assertRaisesRegex(ValueError,'однознач|неоднознач'):
                sync(changed)
            self.assertEqual(state(),before)

    def test_explicit_correction_requires_preview_and_retains_grade_values(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'correction.db')
            database.initialize()
            with database.connection() as connection:
                first = connection.execute("INSERT INTO identities(kind,display_name,class_name) VALUES ('student','Student One','5-А') RETURNING id").fetchone()[0]
                second = connection.execute("INSERT INTO identities(kind,display_name,class_name) VALUES ('student','Student Two','5-А') RETURNING id").fetchone()[0]
                actor = connection.execute("INSERT INTO users(telegram_user_id,role) VALUES (123,'admin') RETURNING id").fetchone()[0]
            values = [['Дата','01.09'],['Задание','Test'],['Вес',1],['Оценка',10],['Student One',8]]
            sync_journal_values(database,values,spreadsheet_id='synthetic',spreadsheet_title='Synthetic',grade=5,subject='Math',sheet_title='Math',sheet_id=1)
            with self.assertRaises(ValueError):
                database.preview_journal_match('synthetic','Student One',second)
            preview = database.preview_journal_match('synthetic','Student One',second,True)
            database.confirm_journal_match('synthetic','Student One',second,preview['preview_token'],actor,True)
            with database.connection() as connection:
                result = connection.execute('SELECT numeric_score,identity_id FROM journal_results').fetchone()
                self.assertEqual(tuple(result),(8,second))
                self.assertNotEqual(first,second)

    def test_global_school_import_lock_blocks_concurrent_grade_import(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(os.path.join(directory, "rollout-lock.db"))
            database.initialize()
            capture_path = Path(directory) / "capture.json"
            capture_path.write_text(json.dumps([{
                "grade": 5,
                "spreadsheet_id": "synthetic-grade-five",
                "spreadsheet_title": "Synthetic grade five",
                "tabs": [{"title": "Synthetic", "sheetId": 10, "values": []}],
            }]), encoding="utf-8")
            backup_path = Path(directory) / "backup.json.gz"
            with gzip.open(backup_path, "wt", encoding="utf-8") as backup:
                json.dump({"project_id": "kmrcbewmxeqqxfauiwyy"}, backup)

            with patch("backend.scripts.journal_school_rollout.production_database", return_value=database):
                with database.journal_workbook_lock("__school_import__") as acquired:
                    self.assertTrue(acquired)
                    with self.assertRaisesRegex(RuntimeError, "Another school journal import is active"):
                        import_capture(capture_path, backup_path)

            with database.connection() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_sync_runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
