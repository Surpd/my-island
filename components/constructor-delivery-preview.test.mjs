import test from 'node:test';
import assert from 'node:assert/strict';
import { draftStudentDelivery } from './constructor-delivery-preview.ts';

test('draft student delivery includes only reliable membership after exclusions', () => {
  const result = draftStudentDelivery([
    { lesson_id: 'included', status: 'resolved', known_student_ids: ['student-1'] },
    { lesson_id: 'excluded', status: 'resolved', known_student_ids: ['student-2'] },
    { lesson_id: 'unknown', status: 'unresolved', known_student_ids: ['student-1'] },
    { lesson_id: 'conflict', status: 'resolved_with_remarks', known_student_ids: ['student-1'], conflict_student_ids: ['student-1'] },
  ], 'student-1');

  assert.deepEqual(result.selected.map(item => item.lesson_id), ['included', 'conflict']);
  assert.deepEqual(result.conflictLessonIds, ['conflict']);
  assert.equal(result.incompleteCount, 1);
});

test('draft student delivery does not infer membership from unresolved records', () => {
  const result = draftStudentDelivery([
    { lesson_id: 'unknown', status: 'unresolved', known_student_ids: ['student-1'] },
  ], 'student-1');
  assert.deepEqual(result.selected, []);
  assert.equal(result.incompleteCount, 1);
});
