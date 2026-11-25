import assert from 'node:assert/strict';
import test from 'node:test';
import { SHORT_WEEKDAYS, shortWeekdayForDate } from './schedule-weekdays.ts';

test('school weekdays use standard Russian abbreviations in Monday-first order', () => {
  assert.deepEqual(SHORT_WEEKDAYS, ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']);
});

test('Campus ISO calendar dates map to the same labels, including Sunday and a new Monday', () => {
  const days = ['2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27', '2026-11-28', '2026-11-29', '2026-11-30'];
  assert.deepEqual(days.map(shortWeekdayForDate), ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс', 'Пн']);
  assert.equal(shortWeekdayForDate(''), '');
});
