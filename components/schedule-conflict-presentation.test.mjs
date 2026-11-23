import assert from 'node:assert/strict';
import test from 'node:test';
import { groupConflictPresentation, overlapInterval } from './schedule-conflict-presentation.ts';

const interval = { day: 0, start: '09:10', end: '09:45' };
const issue = (lessonId, related, type = 'teacher', patch = {}) => ({
  lessonId, related, category: 'conflict', type, severity: 'error',
  message: `toward ${related}`, pairCause: type, interval, ...patch,
});
test('one symmetric pair retains all real causes and the strongest original action', () => {
  const warning = issue('b', 'a', 'teacher', { severity: 'warning' });
  const teacher = issue('a', 'b');
  const room = issue('a', 'b', 'room');
  const student = issue('b', 'a', 'audience');
  const result = groupConflictPresentation([warning, teacher, room, student], row => row.interval);
  assert.equal(result.pairs.length, 1);
  assert.deepEqual(result.pairs[0].ids, ['a', 'b']);
  assert.deepEqual(result.pairs[0].reasons, [teacher, room, student]);
  assert.equal(result.pairs[0].members.length, 4);
});
test('distinct pairs, days, intervals, and individual findings never collapse', () => {
  const baseline = issue('a', 'b');
  const shifted = issue('b', 'a', 'teacher', { interval: { ...interval, start: '09:15' } });
  const anotherDay = issue('a', 'b', 'teacher', { interval: { ...interval, day: 1 } });
  const anotherPair = issue('a', 'c');
  const noInterval = issue('b', 'c', 'room', { interval: undefined });
  const unpaired = issue('a');
  const sameLesson = issue('a', 'a');
  const review = issue('x', 'y', 'room', { category: 'review' });
  const result = groupConflictPresentation([baseline, shifted, anotherDay, anotherPair, unpaired, noInterval, sameLesson, review], row => row.interval);
  assert.equal(result.pairs.length, 4);
  assert.deepEqual(result.singles, [unpaired, noInterval, sameLesson, review]);
});
test('interval is actual overlap, not the slot occupied by one of the two lessons', () => {
  const long = { day: 0, start: '08:30', end: '10:40' };
  assert.deepEqual(overlapInterval(long, interval), interval);
  assert.equal(overlapInterval(long, { day: 0, start: '10:40', end: '11:00' }), undefined);
  assert.equal(overlapInterval(long, { ...interval, day: 1 }), undefined);
});
