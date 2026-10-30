import unittest
from backend.tests import test_constructor_publication as fixtures
from backend.services.constructor_resolution import (resolution_preview,apply_resolution,active_exceptions,
    bulk_audience_preview,apply_bulk_audience)


class ConstructorResolutionTests(unittest.TestCase):
    setUp=fixtures.ConstructorPublicationTests.setUp
    tearDown=fixtures.ConstructorPublicationTests.tearDown
    draft=fixtures.ConstructorPublicationTests.draft
    lesson=fixtures.ConstructorPublicationTests.lesson
    publish=fixtures.ConstructorPublicationTests.publish

    def apply(self,draft,ids,action,**kwargs):
        p=resolution_preview(self.database,draft['id'],expected_revision=draft['revision'],lesson_ids=ids)
        return apply_resolution(self.database,draft['id'],expected_revision=draft['revision'],preview_token=p['preview_token'],lesson_ids=ids,action=action,reason='Verified synthetic case',actor_user_id='synthetic-admin',**kwargs)

    def test_rooms_check_full_intervals_and_manual_choice(self):
        d=self.draft([self.lesson(id='target'),self.lesson(id='busy',room='202',start='09:30',end='10:15'),self.lesson(id='free',room='303',day=1)])
        p=resolution_preview(self.database,d['id'],expected_revision=1,lesson_ids=['target'])
        options={r['room']:r['available'] for r in p['rooms']}
        self.assertFalse(options['202']);self.assertTrue(options['303'])
        with self.assertRaises(ValueError):self.apply(d,['target'],'room',room='202')
        updated=self.apply(d,['target'],'room',room='Manual room')
        self.assertEqual(updated['payload']['lessons'][0]['room'],'Manual room')

    def test_room_aliases_collapse_in_options_and_block_duplicate_booking(self):
        d=self.draft([self.lesson(id='target'),self.lesson(id='busy',room='каб.16',start='09:30',end='10:15'),self.lesson(id='other-day',room='Кабинет 16',day=1)])
        p=resolution_preview(self.database,d['id'],expected_revision=1,lesson_ids=['target'])
        sixteen=[room for room in p['rooms'] if room['room']=='16']
        self.assertEqual(len(sixteen),1)
        self.assertFalse(sixteen[0]['available'])
        with self.assertRaisesRegex(ValueError,'Кабинет занят'):
            self.apply(d,['target'],'room',room='Кабинет 16')

    def test_joint_room_check_accepts_aliases_and_preserves_stored_labels(self):
        d=self.draft([self.lesson(id='a',room='каб.16'),self.lesson(id='b',room='Кабинет 16')])
        updated=self.apply(d,['a','b'],'joint')
        rooms={lesson['id']:lesson['room'] for lesson in updated['payload']['lessons']}
        self.assertEqual(rooms,{'a':'каб.16','b':'Кабинет 16'})

    def test_joint_requires_compatible_place_and_revoke_is_retained(self):
        d=self.draft([self.lesson(id='a'),self.lesson(id='b')])
        updated=self.apply(d,['a','b'],'joint')
        receipt=active_exceptions(updated['payload']['lessons'][0],slots=updated['payload']['slots'])[0]
        self.assertEqual(receipt['type'],'joint')
        with self.assertRaises(ValueError):self.apply(updated,['a'],'revoke',exception_id=receipt['id'])
        revoked=self.apply(updated,['a','b'],'revoke',exception_id=receipt['id'])
        self.assertEqual(active_exceptions(revoked['payload']['lessons'][0],slots=revoked['payload']['slots']),[])
        self.assertTrue(revoked['payload']['lessons'][0]['resolution_history'][0]['revoked_at'])
        bad=self.draft([self.lesson(id='a'),self.lesson(id='b',room='other')])
        with self.assertRaises(ValueError):self.apply(bad,['a','b'],'joint')

    def test_inherited_slot_change_invalidates_receipt(self):
        from backend.services.schedule_experiments import save_experiment
        d=self.draft();updated=self.apply(d,['l1'],'independent_practice')
        payload=updated['payload'];payload['slots'][0]={'start':'11:00','end':'11:45'}
        changed=save_experiment(self.database,updated['id'],expected_revision=updated['revision'],payload=payload)
        self.assertEqual(active_exceptions(changed['payload']['lessons'][0],slots=changed['payload']['slots']),[])

    def test_exception_stales_when_meaning_changes_and_preview_cannot_replay(self):
        d=self.draft();p=resolution_preview(self.database,d['id'],expected_revision=1,lesson_ids=['l1'])
        updated=self.apply(d,['l1'],'independent_practice')
        lesson=updated['payload']['lessons'][0]
        self.assertEqual(active_exceptions(lesson,slots=updated['payload']['slots'])[0]['type'],'independent_practice')
        lesson['room']='different'
        self.assertEqual(active_exceptions(lesson),[])
        with self.assertRaises(ValueError):apply_resolution(self.database,d['id'],expected_revision=2,preview_token=p['preview_token'],lesson_ids=['l1'],action='defer',reason='Review',actor_user_id=None)

    def test_bulk_roster_confirmation_does_not_change_memberships(self):
        d=self.draft([self.lesson(id='a',source_cell='B14'),self.lesson(id='b',day=1,source_cell='D14')])
        from backend.services.schedule_experiments import save_experiment
        d=save_experiment(self.database,d['id'],expected_revision=1,payload=d['payload'])
        new=self.database.create_identity('student','New synthetic student','9A')
        self.database.create_membership(self.group['id'],new['id'],'student','synthetic')
        p=resolution_preview(self.database,d['id'],expected_revision=d['revision'],lesson_ids=['a','b'])
        self.assertEqual(len(p['roster']['affected_lessons']),2)
        self.assertEqual((p['roster']['affected_lessons'][0]['day'],p['roster']['affected_lessons'][0]['start'],p['roster']['affected_lessons'][0]['end']),(0,'09:00','09:45'))
        self.assertEqual(p['roster']['affected_lessons'][0]['source_cell'],'B14')
        self.assertTrue(p['roster']['affected_lessons'][0]['group_names'])
        updated=self.apply(d,['a','b'],'confirm_roster')
        self.assertTrue(all(str(new['id']) in l['audience_snapshot']['student_ids'] for l in updated['payload']['lessons']))

    def test_bulk_audience_scope_previews_diffs_and_confirms_only_known_rosters(self):
        unknown = self.lesson(id='unknown', audience={'kind': 'groups', 'group_ids': ['missing-group']})
        d = self.draft([self.lesson(id='a'), self.lesson(id='b', day=1), unknown])
        from backend.services.schedule_experiments import save_experiment
        from backend.services.schedule_experiments import ExperimentConflict
        d = save_experiment(self.database, d['id'], expected_revision=1, payload=d['payload'])
        new = self.database.create_identity('student', 'Bulk synthetic addition', '9A')
        self.database.create_membership(self.group['id'], new['id'], 'student', 'synthetic')

        preview = bulk_audience_preview(self.database, d['id'], expected_revision=d['revision'], scope='template')
        self.assertEqual(preview['summary']['changed_count'], 2)
        self.assertEqual(preview['summary']['safe_count'], 2)
        self.assertEqual(preview['summary']['unresolved_count'], 1)
        self.assertEqual(preview['summary']['added_count'], 2)
        with self.database.connection() as connection:
            before_memberships = connection.execute("SELECT group_id,member_role,active,source FROM memberships WHERE identity_id=? ORDER BY group_id", (new['id'],)).fetchall()

        # Any directory change after preview invalidates the confirmation token.
        unrelated = self.database.create_group('Unrelated 10C', 'class')
        extra = self.database.create_identity('student', 'Unrelated bulk pupil')
        self.database.create_membership(unrelated['id'], extra['id'], 'student', 'synthetic')
        with self.assertRaises(ExperimentConflict):
            apply_bulk_audience(self.database, d['id'], expected_revision=d['revision'], scope='template',
                preview_token=preview['preview_token'], reason='Confirm checked changes', actor_user_id='synthetic-admin')

        fresh = bulk_audience_preview(self.database, d['id'], expected_revision=d['revision'], scope='template')
        updated = apply_bulk_audience(self.database, d['id'], expected_revision=d['revision'], scope='template',
            preview_token=fresh['preview_token'], reason='Confirm checked changes', actor_user_id='synthetic-admin')
        lessons = {lesson['id']: lesson for lesson in updated['payload']['lessons']}
        self.assertEqual(updated['revision'], d['revision'] + 1)
        self.assertIn(str(new['id']), lessons['a']['audience_snapshot']['student_ids'])
        self.assertNotIn(str(extra['id']), lessons['b']['audience_snapshot']['student_ids'])
        self.assertNotIn('audience_snapshot', lessons['unknown'])
        with self.database.connection() as connection:
            after_memberships = connection.execute("SELECT group_id,member_role,active,source FROM memberships WHERE identity_id=? ORDER BY group_id", (new['id'],)).fetchall()
        self.assertEqual([tuple(row) for row in after_memberships], [tuple(row) for row in before_memberships])

        # Applying the same directory state again makes no draft revision.
        repeated = bulk_audience_preview(self.database, d['id'], expected_revision=updated['revision'], scope='stale')
        unchanged = apply_bulk_audience(self.database, d['id'], expected_revision=updated['revision'], scope='stale',
            preview_token=repeated['preview_token'], reason='No changes', actor_user_id='synthetic-admin')
        self.assertEqual(unchanged['revision'], updated['revision'])

    def test_bulk_recalculation_confirms_twenty_six_records_in_one_draft_revision(self):
        grade6 = self.database.create_group('6-А', 'class')
        lessons = [self.lesson(id=f'grade6-{index}', classId=str(grade6['id']), classIds=[str(grade6['id'])],
                               audience={'kind': 'classes', 'class_ids': [str(grade6['id'])]})
                   for index in range(26)]
        d = self.draft(lessons)
        from backend.services.schedule_experiments import save_experiment
        d = save_experiment(self.database, d['id'], expected_revision=1, payload=d['payload'])
        pupil = self.database.create_identity('student', 'Synthetic Grade 6')
        self.database.create_membership(grade6['id'], pupil['id'], 'student', 'synthetic')
        preview = bulk_audience_preview(self.database, d['id'], expected_revision=d['revision'], scope='class', class_id=str(grade6['id']))
        self.assertEqual(preview['summary']['selected_count'], 26)
        self.assertEqual(preview['summary']['added_count'], 26)
        self.assertEqual(preview['summary']['safe_count'], 26)
        updated = apply_bulk_audience(self.database, d['id'], expected_revision=d['revision'], scope='class', class_id=str(grade6['id']),
            preview_token=preview['preview_token'], reason='Confirm grade 6 audience', actor_user_id='synthetic-admin')
        self.assertEqual(updated['revision'], d['revision'] + 1)
        self.assertTrue(all(str(pupil['id']) in item['audience_snapshot']['student_ids'] for item in updated['payload']['lessons']))

    def test_explicit_group_lunch_without_teacher_recalculates_away_busy_member(self):
        from backend.services.schedule_experiments import save_experiment, experiment_audience_preview
        lunch_group = self.database.create_group('Lunch group', 'instructional', base_class_name='9A')
        pupil_in_class = self.database.create_identity('student', 'Lunch student also in math')
        free_pupil = self.database.create_identity('student', 'Lunch free student')
        for pupil in (pupil_in_class, free_pupil):
            self.database.create_membership(self.group['id'], pupil['id'], 'student', 'synthetic')
            self.database.create_membership(lunch_group['id'], pupil['id'], 'student', 'synthetic')
        lunch = self.lesson(id='lunch', subject='Обед', specialMark='lunch', teacherId='', teacherIds=[], room='',
                            groupId=str(lunch_group['id']), groupIds=[str(lunch_group['id'])],
                            audience={'kind':'groups','group_ids':[str(lunch_group['id'])]})
        d = self.draft([lunch])
        d = save_experiment(self.database, d['id'], expected_revision=1, payload=d['payload'])
        old_roster = next(item for item in d['payload']['lessons'] if item['id']=='lunch')['audience_snapshot']['student_ids']
        self.assertEqual(set(old_roster), {str(pupil_in_class['id']), str(free_pupil['id'])})

        math = self.lesson(id='math', subject='Math', audience={'kind':'groups','group_ids':[str(self.math['id'])]},
                           classId=str(self.group['id']), classIds=[str(self.group['id'])])
        self.database.create_membership(self.math['id'], pupil_in_class['id'], 'student', 'synthetic')
        payload = {**d['payload'], 'lessons': [math, *d['payload']['lessons']]}
        d = save_experiment(self.database, d['id'], expected_revision=d['revision'], payload=payload)
        preview = experiment_audience_preview(self.database, d['payload'])['lessons']['lunch']
        self.assertEqual(preview['student_ids'], old_roster)
        self.assertEqual(preview['proposed_student_ids'], [str(free_pupil['id'])])
        self.assertTrue(preview['needs_recalculation'])

        bulk = bulk_audience_preview(self.database, d['id'], expected_revision=d['revision'], scope='class', class_id=str(lunch_group['id']))
        self.assertEqual(bulk['summary']['safe_count'], 1)
        updated = apply_bulk_audience(self.database, d['id'], expected_revision=d['revision'], scope='class', class_id=str(lunch_group['id']),
            preview_token=bulk['preview_token'], reason='Confirm lunch fallback', actor_user_id='synthetic-admin')
        saved_lunch = next(item for item in updated['payload']['lessons'] if item['id']=='lunch')
        self.assertEqual(saved_lunch['audience_snapshot']['student_ids'], [str(free_pupil['id'])])
        self.assertFalse(saved_lunch['teacherId'])
        self.assertFalse(saved_lunch['room'])

    def test_weekly_bulk_rosters_use_memberships_valid_on_each_lesson_day(self):
        from backend.services.schedule_experiments import save_experiment
        expires_midweek = self.database.create_identity('student', 'Expires midweek', '9A')
        stays_active = self.database.create_identity('student', 'Stays active', '9A')
        for pupil in (expires_midweek, stays_active):
            self.database.create_membership(self.group['id'], pupil['id'], 'student', 'synthetic')
        with self.database.connection() as connection:
            connection.execute("UPDATE memberships SET valid_until='2026-10-21' WHERE identity_id=? AND group_id=?", (expires_midweek['id'], self.group['id']))
        lessons = [self.lesson(id='monday', day=0), self.lesson(id='thursday', day=3)]
        d = self.draft(lessons)
        payload = {**d['payload'], 'days': [0, 3], 'week_start': '2026-10-19'}
        d = save_experiment(self.database, d['id'], expected_revision=d['revision'], payload=payload)
        before = {item['id']: item['audience_snapshot']['student_ids'] for item in d['payload']['lessons']}
        preview = bulk_audience_preview(self.database, d['id'], expected_revision=d['revision'], scope='template')
        self.assertEqual(preview['summary']['changed_count'], 1)
        self.assertEqual(preview['summary']['removed_count'], 1)
        updated = apply_bulk_audience(self.database, d['id'], expected_revision=d['revision'], scope='template',
            preview_token=preview['preview_token'], reason='Confirm date-specific rosters', actor_user_id='synthetic-admin')
        by_id = {item['id']: item for item in updated['payload']['lessons']}
        self.assertEqual(by_id['monday']['audience_snapshot']['student_ids'], before['monday'])
        self.assertEqual(set(by_id['thursday']['audience_snapshot']['student_ids']),
                         set(before['thursday']) - {str(expires_midweek['id'])})

    def test_demo_bulk_audience_api_uses_admin_auth_known_roster_and_revision_cas(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.handlers.api import create_router
        from backend.services.schedule_experiments import save_experiment

        admin = self.database.get_or_create_user(992001, 'admin')
        student_user = self.database.get_or_create_user(992002, 'student')
        with self.database.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO user_roles(user_id,role,source) VALUES (?,'admin','test')", (admin['id'],))
            connection.execute("INSERT OR IGNORE INTO user_roles(user_id,role,source) VALUES (?,'student','test')", (student_user['id'],))
        admin_code, _ = self.database.create_admin_login_challenge(admin['id'])
        admin_token, _ = self.database.consume_admin_login_challenge(admin_code)
        student_code, _ = self.database.create_admin_login_challenge(student_user['id'])
        student_token, _ = self.database.consume_admin_login_challenge(student_code)

        draft = self.draft()
        draft = save_experiment(self.database, draft['id'], expected_revision=1, payload=draft['payload'])
        added = self.database.create_identity('student', 'Demo API audience addition', '9A')
        self.database.create_membership(self.group['id'], added['id'], 'student', 'synthetic')
        app = FastAPI()
        app.include_router(create_router(self.database))
        client = TestClient(app)
        base = f"/api/admin/schedule/experiments/{draft['id']}/audiences/"
        body = {'expected_revision': draft['revision'], 'scope': 'template'}
        self.assertEqual(client.post(base+'preview', json=body).status_code, 401)
        self.assertEqual(client.post(base+'preview', headers={'X-Dev-Auth': f'browser:{student_token}'}, json=body).status_code, 403)
        headers = {'X-Dev-Auth': f'browser:{admin_token}'}
        preview = client.post(base+'preview', headers=headers, json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['summary']['safe_count'], 1)
        applied = client.post(base+'apply', headers=headers,
            json={**body, 'preview_token': preview.json()['preview_token'], 'reason': 'Confirm checked demo roster'})
        self.assertEqual(applied.status_code, 200, applied.text)
        lesson = applied.json()['payload']['lessons'][0]
        self.assertIn(str(added['id']), lesson['audience_snapshot']['student_ids'])
        self.assertEqual(lesson['resolution_history'][-1]['actor_id'], str(admin['id']))
        replay = client.post(base+'apply', headers=headers,
            json={**body, 'preview_token': preview.json()['preview_token'], 'reason': 'Replay stale demo roster'})
        self.assertEqual(replay.status_code, 409, replay.text)

    def test_client_save_cannot_forge_or_remove_audited_confirmations(self):
        from copy import deepcopy
        from backend.services.schedule_experiments import save_experiment,create_experiment
        forged=self.lesson(resolution_history=[{'id':'forged','actor_id':'another-admin'}])
        d=self.draft([forged]);self.assertNotIn('resolution_history',d['payload']['lessons'][0])
        d=self.apply(d,['l1'],'independent_practice')
        payload=deepcopy(d['payload']);payload['lessons'][0]['resolution_history']=[]
        updated=save_experiment(self.database,d['id'],expected_revision=d['revision'],payload=payload)
        self.assertEqual(updated['payload']['lessons'][0]['resolution_history'],d['payload']['lessons'][0]['resolution_history'])

    def test_own_known_unresolved_status_does_not_leak_to_unknown_students(self):
        from backend.services import canonical_schedule as canonical
        d=self.draft([self.lesson(review_required=True)])
        self.publish(d);self.publish(d,'week',week_start='2026-10-19')
        result=canonical.project_student(self.database,self.student['id'],'2026-10-19')
        self.assertEqual([i['state'] for i in result['items']],['UNRESOLVED'])
        self.assertIsNone(result['items'][0]['activity'])
        self.assertNotIn('known_student_ids',str(result))
        unknown=self.draft([self.lesson(audience={'kind':''})])
        self.publish(unknown);self.publish(unknown,'week',week_start='2026-10-26')
        self.assertEqual(canonical.project_student(self.database,self.student['id'],'2026-10-26')['items'],[])

    def test_independent_practice_keeps_responsibility_without_physical_overlap(self):
        from backend.services import canonical_schedule as canonical
        d=self.draft([self.lesson(id='lesson',audience={'kind':'students','student_ids':[str(self.student['id'])]}),self.lesson(id='practice',subject='Practice',audience={'kind':'students','student_ids':[str(self.other_student['id'])]})])
        d=self.apply(d,['practice'],'independent_practice');self.publish(d);self.publish(d,'week',week_start='2026-10-19')
        student=canonical.project_student(self.database,self.other_student['id'],'2026-10-19')
        self.assertEqual(student['items'][0]['activity_format'],'independent_practice')
        self.assertEqual(student['items'][0]['responsible_teachers'],['Synthetic Teacher'])
        teacher=canonical.project_teacher(self.database,self.teacher['id'],'2026-10-19')
        self.assertEqual(len(teacher['items']),2);self.assertEqual(teacher['issues'],[])
        practice=next(i for i in teacher['items'] if i['activity']=='Practice')
        self.assertFalse(practice['physical_presence'])
        self.assertEqual(practice['responsible_teachers'],['Synthetic Teacher'])


if __name__=='__main__':unittest.main()
