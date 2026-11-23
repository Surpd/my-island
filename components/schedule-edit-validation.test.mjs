import assert from 'node:assert/strict';
import test from 'node:test';
import { effectiveClubReviewNote, isTypedScheduleClub } from './schedule-constructor-logic.ts';

test('authoritative activity type, not subject name, distinguishes clubs from lessons', () => {
  assert.equal(isTypedScheduleClub({ activityType: 'club', subject: 'Английский язык' }), true);
  assert.equal(isTypedScheduleClub({ canonical: { assignment: { metadata: { activity_type: 'club' } } } }), true);
  assert.equal(isTypedScheduleClub({ activityType: 'lesson', canonical: { assignment: { metadata: { activity_type: 'club' } } } }), false);
  assert.equal(isTypedScheduleClub({ subject: 'Кружок английского' }), false);
});

test('only known lesson-only roster notes are obsolete on club, other causes remain visible', () => {
  assert.equal(effectiveClubReviewNote('Для этого предмета не подтверждена учебная группа.'), '');
  assert.equal(effectiveClubReviewNote('Для этого предмета не подтверждена учебная группа; Неизвестный преподаватель.'), 'Неизвестный преподаватель.');
  assert.equal(effectiveClubReviewNote('Некорректная учебная группа по данным импорта.'), 'Некорректная учебная группа по данным импорта.');
});
