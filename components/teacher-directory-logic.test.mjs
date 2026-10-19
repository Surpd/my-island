import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeAssignmentMerges, assignmentIsActive } from './teacher-directory-logic.ts';

const setup = () => {
  const students = [
    { id: 's1', class_name: '7-А', memberships: [{ group_id: 'g1' }] },
    { id: 's2', class_name: '7-Б', memberships: [{ group_id: 'g2' }] },
  ];
  const groups = [
    { id: 'g1', group_type: 'instructional', name: '7-1', subject: 'Math' },
    { id: 'g2', group_type: 'instructional', name: '7-2', subject: 'Математика' },
  ];
  const assignments = [
    { id: 'a1', group_id: 'g1', teacher_identity_id: 't1', subject: 'Математика' },
    { id: 'a2', group_id: 'g2', teacher_identity_id: 't1', subject: 'Math' },
  ];
  return { students, groups, assignments, teacher: { id: 't1', teacher_assignments: assignments } };
};

test('aggregates only complete, non-overlapping active parallel coverage for the same teacher and subject', () => {
  const { students, groups, assignments, teacher } = setup();
  const result = analyzeAssignmentMerges(assignments, [teacher], groups, students);
  assert.equal(result.length, 1);
  assert.equal(result[0].eligible, true);
  assert.equal(result[0].covered, 2);
  assert.equal(result[0].expected, 2);
});

test('does not aggregate if the group split is incomplete or has a competing assignment', () => {
  const partial = setup();
  partial.students.push({ id: 's3', class_name: '7-А', memberships: [] });
  assert.equal(analyzeAssignmentMerges(partial.assignments, [partial.teacher], partial.groups, partial.students)[0].eligible, false);

  const competing = setup();
  competing.teacher.teacher_assignments.push({ id: 'a3', grade_level: '7', teacher_identity_id: 't2', subject: 'Математика' });
  assert.equal(analyzeAssignmentMerges(competing.assignments, [competing.teacher], competing.groups, competing.students)[0].eligible, false);
});

test('ignores inactive legacy assignments when deciding aggregation', () => {
  const state = setup();
  state.assignments.push({ id: 'old', group_id: 'g1', teacher_identity_id: 'other', subject: 'Математика', active: false });
  state.teacher.teacher_assignments = state.assignments;
  const result = analyzeAssignmentMerges(state.assignments, [state.teacher], state.groups, state.students);
  assert.equal(result[0].eligible, true);
  assert.deepEqual(result[0].rows.map((row) => row.id), ['a1', 'a2']);
});

test('assignment validity uses inclusive local dates and excludes future or expired assignments', () => {
  const today = new Date(2026, 9, 17);
  assert.equal(assignmentIsActive({ valid_from: '2026-10-17', valid_until: '2026-10-17' }, today), true);
  assert.equal(assignmentIsActive({ valid_from: '2026-10-18' }, today), false);
  assert.equal(assignmentIsActive({ valid_until: '2026-10-16' }, today), false);
  assert.equal(assignmentIsActive({ status: 'inactive' }, today), false);
});

test('future and expired assignments cannot form a parallel aggregate', () => {
  const today = new Date(2026, 9, 17);
  const future = setup();
  future.assignments[1].valid_from = '2026-10-18';
  future.teacher.teacher_assignments = future.assignments;
  assert.deepEqual(analyzeAssignmentMerges(future.assignments, [future.teacher], future.groups, future.students, today), []);

  const expired = setup();
  expired.assignments[0].valid_until = '2026-10-16';
  expired.teacher.teacher_assignments = expired.assignments;
  assert.deepEqual(analyzeAssignmentMerges(expired.assignments, [expired.teacher], expired.groups, expired.students, today), []);
});
