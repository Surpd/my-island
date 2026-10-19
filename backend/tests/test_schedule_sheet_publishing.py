import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.database import Database
from backend.services.google_live import GoogleLiveError
from backend.services.schedule_experiments import ExperimentConflict, create_experiment, save_experiment
from backend.services.schedule_sheet_publishing import ExportAuthorizationRequired, export_client, export_saved_week, prepare_export


class FakeSheets:
    def __init__(self, lost_response=False):
        self.sheets = {}
        self.values = {}
        self.writes = []
        self.lost_response = lost_response

    def spreadsheet(self, target):
        return {"sheets": [{"properties": s} for s in self.sheets.values()]}

    def sheet_values(self, target, name):
        return self.values.get(name, [])

    def batch_update_spreadsheet(self, target, requests):
        self.writes.append(requests)
        for r in requests:
            if 'addSheet' in r:
                p = r['addSheet']['properties']
                self.sheets[p['sheetId']] = p
        for r in requests:
            if 'updateCells' in r:
                u = r['updateCells']
                title = self.sheets[u['start']['sheetId']]['title']
                self.values[f"'{title}'!A1:B1"] = [[c.get('userEnteredValue', {}).get('stringValue', '') for c in u['rows'][0]['values'][:2]]]
        if self.lost_response:
            self.lost_response = False
            raise GoogleLiveError('lost response after Google committed')


class SchedulePublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.sqlite3')
        self.db.initialize()
        self.group = str(self.db.create_group('7', 'class')['id'])
        self.teacher = str(self.db.create_identity('teacher', 'Учитель')['id'])
        self.payload = {'week_start': '2026-10-19', 'days': [0], 'slots': [{'start': '09:00', 'end': '09:45'}],
                        'classes': [self.group], 'lessons': [{'id': 'original', 'subject': 'Математика', 'day': 0,
                        'period': 0, 'start': '09:00', 'end': '09:45', 'classIds': [self.group], 'teacherIds': [self.teacher],
                        'room': '17', 'audience': {'kind': 'classes', 'class_ids': [self.group]}}]}
        self.draft = create_experiment(self.db, name='Тестовая неделя', source='import', payload=self.payload)

    def tearDown(self):
        self.temp.cleanup()

    def test_authorization_required_is_not_reported_as_success(self):
        result = export_saved_week(self.db, self.draft['id'], expected_revision=1, settings=SimpleNamespace(local_test_mode=True))
        self.assertEqual(result['status'], 'authorization_required')
        self.assertNotIn('url', result)
        with self.assertRaises(ExperimentConflict):
            prepare_export(self.db, self.draft['id'], expected_revision=2)

    def test_application_never_falls_back_to_unencrypted_cli_tokens(self):
        settings = SimpleNamespace(local_test_mode=False, google_oauth_client_id='configured',
                                   google_oauth_client_secret='configured', google_oauth_redirect_uri='https://example.test/callback',
                                   google_oauth_token_encryption_key='configured', google_sheets_spreadsheet_id=None,
                                   google_classroom_course_id=None)
        with patch.dict('os.environ', {'MY_ISLAND_SCHOOL_WORKSPACE': ''}), patch('backend.services.schedule_sheet_publishing.GoogleTokenStore.load') as token_load:
            with self.assertRaises(ExportAuthorizationRequired):
                export_client(self.db, settings)
            token_load.assert_not_called()

    def test_repeat_returns_same_document_and_new_revision_gets_new_tab(self):
        client = FakeSheets()
        first = export_saved_week(self.db, self.draft['id'], expected_revision=1, settings=None, client=client)
        again = export_saved_week(self.db, self.draft['id'], expected_revision=1, settings=None, client=client)
        self.assertEqual(first['url'], again['url'])
        self.assertEqual(again['status'], 'existing')
        self.assertEqual(len(client.writes), 1)
        self.payload['lessons'][0]['room'] = '19'
        save_experiment(self.db, self.draft['id'], expected_revision=1, payload=self.payload)
        changed = export_saved_week(self.db, self.draft['id'], expected_revision=2, settings=None, client=client)
        self.assertNotEqual(first['url'], changed['url'])
        self.assertEqual(len(client.sheets), 4)
        with self.db.connection() as c:
            old = self.db.execute(c, 'SELECT snapshot FROM schedule_sheet_exports WHERE export_key=?', (first['export_key'],)).fetchone()
        self.assertEqual(json.loads(old['snapshot'])['payload']['lessons'][0]['room'], '17')

    def test_lost_google_response_recovers_without_duplicate_tabs(self):
        client = FakeSheets(lost_response=True)
        result = export_saved_week(self.db, self.draft['id'], expected_revision=1, settings=None, client=client)
        self.assertEqual(result['status'], 'exported')
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(len(client.sheets), 2)

    def test_confirmed_participants_are_not_sent_to_google(self):
        self.payload['lessons'][0].update(activityType='club', activityNameSnapshot='Шахматы', confirmedParticipantIds=['private-student'], eligibleGrades=[7])
        draft = create_experiment(self.db, name='Кружок', source='import', payload=self.payload)
        export = prepare_export(self.db, draft['id'], expected_revision=1)
        self.assertNotIn('private-student', json.dumps(export['plan']))
        self.assertIn('confirmed_participants_fingerprint', json.dumps(export['snapshot']))


if __name__ == '__main__':
    unittest.main()
