import test from 'node:test';
import assert from 'node:assert/strict';
import { formatLessonTime, formatLessonTimeRange } from './lesson-time.ts';

test('formats supported local clock values without timezone conversion', () => {
  assert.equal(formatLessonTime('9:05'), '09:05');
  assert.equal(formatLessonTime('09:05'), '09:05');
  assert.equal(formatLessonTime('09:05:59'), '09:05');
});

test('returns an empty value for missing or invalid time values', () => {
  for (const value of [null, undefined, '', '25:00', '09:60', '09:00:60', '9:5', 'not-a-time']) {
    assert.equal(formatLessonTime(value), '');
  }
});

test('formats ranges and gives a stable fallback when start time is unavailable', () => {
  assert.equal(formatLessonTimeRange('9:05:00', '10:00:00'), '09:05–10:00');
  assert.equal(formatLessonTimeRange('09:05', null), '09:05');
  assert.equal(formatLessonTimeRange(null, '10:00'), 'Время не указано');
});
