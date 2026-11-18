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
    def test_legacy_teacher_group_filters_other_subjects_in_the_same_class(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'subjects.db')
            database.initialize()
            student = database.create_identity('student','Student One','5-А')
            teacher = database.create_identity('teacher','Teacher One')
            group = database.create_group('5-А','class')
            database.create_membership(group['id'],student['id'],'student','official_import')
            with database.connection() as connection:
                actor = connection.execute("INSERT INTO users(telegram_user_id,role,identity_id) VALUES (123,'teacher',?) RETURNING id",(teacher['id'],)).fetchone()[0]
            database.set_teacher_assignment(teacher['id'],group['id'],'Math',True,actor)
            values = [['Дата','01.09'],['Задание','Task'],['Вес',1],['Оценка',10],['Student One',5]]
            for sheet_id,subject in ((1,'Math'),(2,'English')):
                sync_journal_values(database,values,spreadsheet_id='shared-class',spreadsheet_title='Synthetic',grade=5,
                    subject=subject,sheet_title=subject,sheet_id=sheet_id)
            self.assertEqual({r['subject'] for r in database.list_group_journal(group['id'])},{'Math','English'})
            self.assertEqual({r['subject'] for r in database.list_group_journal(group['id'],teacher_user_id=actor)},{'Math'})
            with database.connection() as connection:
                connection.execute("UPDATE teacher_assignments SET subject_subgroup='A' WHERE teacher_identity_id=?",(teacher['id'],))
            self.assertEqual(database.list_group_journal(group['id'],teacher_user_id=actor),[])

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

    def test_different_works_with_similar_scores_and_moved_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'identity.db')
            database.initialize()
            def sync(rows):
                sync_journal_values(database, rows, spreadsheet_id='book', spreadsheet_title='Synthetic', grade=5, subject='Math', sheet_title='Math', sheet_id=1)
            rows = [['Дата','01.09','02.09'], ['Задание','First','Second'], ['Вес',1,1], ['Оценка',10,10]] + [[f'Student {n}', 8, 8] for n in range(5)]
            sync(rows)
            with database.connection() as connection:
                initial = {row['title']: row['id'] for row in connection.execute('SELECT id,title FROM journal_assessments WHERE active=1')}
            moved = [[row[0], row[2], row[1]] for row in rows]
            moved[-1][1] = 9
            sync(moved)
            with database.connection() as connection:
                self.assertEqual(initial, {row['title']: row['id'] for row in connection.execute('SELECT id,title FROM journal_assessments WHERE active=1')})
            replaced = [row[:] for row in moved]
            replaced[1][1] = 'Completely different'
            sync(replaced)
            with database.connection() as connection:
                current = {row['title']: row['id'] for row in connection.execute('SELECT id,title FROM journal_assessments WHERE active=1')}
                self.assertNotEqual(current['Completely different'], initial['Second'])
                self.assertEqual(current['First'], initial['First'])

    def test_repeated_titles_different_dates_never_reuse_identity_by_column_or_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'repeated-titles.db')
            database.initialize()

            def sync(rows):
                sync_journal_values(database, rows, spreadsheet_id='repeated', spreadsheet_title='Synthetic', grade=9,
                    subject='History', sheet_title='History', sheet_id=1)

            original = [['Дата','01.09','08.09'], ['Задание','Опрос','Опрос'], ['Вес',1,2], ['Оценка',10,20]]
            original.extend([[f'Student {index}', 8, 8] for index in range(5)])
            sync(original)

            def active():
                with database.connection() as connection:
                    return {(row['assessed_on'], row['weight'], row['max_score']): row['id'] for row in
                        connection.execute('SELECT id,assessed_on,weight,max_score FROM journal_assessments WHERE active=1')}

            first = active()
            moved = [[row[0], row[2], row[1]] for row in original]
            sync(moved)
            self.assertEqual(active(), first)

            # Correcting weight/max on the same dated work retains its identity.
            corrected = [row[:] for row in moved]
            corrected[2][1] = 3
            corrected[3][1] = 30
            sync(corrected)
            corrected_ids = active()
            self.assertEqual(corrected_ids[('2026-09-08', 3, 30)], first[('2026-09-08', 2, 20)])

            # A new "Опрос" on a new date reusing the same column and scores is a new work.
            replacement = [row[:] for row in corrected]
            replacement[0][1] = '15.09'
            sync(replacement)
            latest = active()
            self.assertNotEqual(latest[('2026-09-15', 3, 30)], corrected_ids[('2026-09-08', 3, 30)])

    def test_repeated_control_work_title_with_date_change_keeps_both_history_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / 'controls.db')
            database.initialize()
            rows = [['Дата','01.09'], ['Задание','Контрольная работа'], ['Вес',1], ['Оценка',10], ['Student',9]]
            kwargs = dict(spreadsheet_id='controls', spreadsheet_title='Synthetic', grade=9, subject='History', sheet_title='History', sheet_id=1)
            sync_journal_values(database, rows, **kwargs)
            rows[0][1] = '08.09'
            sync_journal_values(database, rows, **kwargs)
            with database.connection() as connection:
                records = connection.execute('SELECT id,assessed_on,active FROM journal_assessments ORDER BY id').fetchall()
            self.assertEqual(len(records), 2)
            self.assertNotEqual(records[0]['id'], records[1]['id'])
            self.assertEqual([record['active'] for record in records], [0, 1])

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
