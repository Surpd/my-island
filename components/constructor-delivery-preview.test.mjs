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

test('draft student delivery shows one shared elective card without direction enrolment', () => {
  const result = draftStudentDelivery([
    { lesson_id: 'art', status: 'resolved', known_student_ids: [] },
    { lesson_id: 'music', status: 'resolved', known_student_ids: [] },
  ], 'student-1', [{
    delivery_id: 'elective:block-1', lesson_id: 'art', source_lesson_ids: ['art', 'music'],
    status: 'resolved', known_student_ids: ['student-1'], display_label: 'Творчество',
  }]);

  assert.deepEqual(result.selected.map(item => item.display_label), ['Творчество']);
  assert.deepEqual(result.selected[0].source_lesson_ids, ['art', 'music']);
});

test('draft student delivery does not confirm a conflicted elective block', () => {
  const result = draftStudentDelivery([], 'student-1', [{
    delivery_id: 'elective:block-1', lesson_id: 'art', source_lesson_ids: ['art'], status: 'resolved',
    known_student_ids: ['student-2'], unresolved_student_ids: ['student-1'], display_label: 'Творчество',
  }]);

  assert.deepEqual(result.selected, []);
  assert.equal(result.incompleteCount, 1);
});
