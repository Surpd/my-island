import unittest
from backend.tests import test_constructor_publication as fixtures
from backend.services.constructor_resolution import resolution_preview,apply_resolution,active_exceptions


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
