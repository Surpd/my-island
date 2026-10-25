import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.journal_admin_matching import batch, candidates, create_student
from backend.services.journal_import import sync_journal_values
from backend.services.journal_matching import suggestion_reason


def exercise_matching(database):
    with database.connection() as connection:
        actor = database.execute(connection,"INSERT INTO users(telegram_user_id,role) VALUES (991122,'admin') RETURNING id").fetchone()['id']
    database.create_group('6-А','class',base_class_name='6-А',actor_user_id=actor)
    def sync():
        for title, name, sheet in [('Math','Мальцев Саша',61),('Science','Мальцев Александр',62)]:
            sync_journal_values(database,[['Дата','01.09'],['Задание','Test'],['Вес',1],['Оценка',10],[name,8]],
                spreadsheet_id='matching-create',spreadsheet_title='Synthetic',grade=6,subject=title,sheet_title=title,sheet_id=sheet)
    sync()
    payload = {'spreadsheet_id':'matching-create','source_names':['Мальцев Саша','Мальцев Александр'],
               'surname':'Мальцев','given_name':'Александр','grade':6,'comment':''}
    preview = create_student(database,payload)
    assert preview['can_confirm'] and preview['record_count'] == 2 and preview['result_count'] == 2
    result = create_student(database,dict(payload,preview_token=preview['preview_token']),actor,True)
    identity_id = result['identity_id']
    assert len(candidates(database,6)) == 1
    assert len(candidates(database,6,q='александр мальцев')) == 1
    assert not candidates(database,7)
    assert identity_id in {str(person['id']) for person in candidates(database,7,'school')}
    assert not create_student_preview_possible(database,payload)
    try:
        create_student(database,dict(payload,preview_token=preview['preview_token']),actor,True)
        raise AssertionError('duplicate confirmation accepted')
    except ValueError:
        pass
    sync()
    with database.connection() as connection:
        assert database.execute(connection,"SELECT COUNT(*) n FROM memberships WHERE identity_id=?",(identity_id,)).fetchone()['n'] == 1
        assert database.execute(connection,"SELECT COUNT(*) n FROM journal_results WHERE is_current IS TRUE AND identity_id=?",(identity_id,)).fetchone()['n'] == 2
        assert database.execute(connection,"SELECT COUNT(*) n FROM school_source_mappings WHERE identity_id=?",(identity_id,)).fetchone()['n'] == 2
        source = database.execute(connection,"SELECT id FROM journal_sources WHERE spreadsheet_id='matching-create' ORDER BY id LIMIT 1").fetchone()['id']
    database.set_journal_source_mode(source,'manual')
    assert any(not row['auto_sync_enabled'] for row in database.journal_source_sync_settings('matching-create'))
    sync()
    assert any(not row['auto_sync_enabled'] for row in database.journal_source_sync_settings('matching-create'))
    with database.connection() as connection:
        assert database.execute(connection,"SELECT COUNT(*) n FROM journal_results WHERE is_current IS TRUE").fetchone()['n'] == 2
    assert suggestion_reason('Яна Теплова','Теплова Яна')
    assert suggestion_reason('Табулин Даня','Табулин Даниил')


def create_student_preview_possible(database,payload):
    try:
        return create_student(database,payload)['can_confirm']
    except ValueError:
        return False


class JournalAdminMatchingTests(unittest.TestCase):
    def test_atomic_creation_aliases_candidates_and_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder)/'journal.db')
            database.initialize()
            exercise_matching(database)
