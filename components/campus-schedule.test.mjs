import test from 'node:test';
import assert from 'node:assert/strict';
import { campusLessons, campusStudentDays } from './campus-schedule.ts';

test('published canonical API activities render as dated lessons with teacher and room', () => {
  const raw = [{ date: '2026-09-21', state: 'ACTIVITY', activity: 'Математика', start_time: '09:00', end_time: '09:45', teacher_names: ['Тестовый педагог'], room: '17', audiences: ['Математика A · 7'] }];
  const [lesson] = campusLessons(raw);
  assert.equal(lesson.subject, 'Математика'); assert.equal(lesson.lesson_date, '2026-09-21');
  assert.equal(lesson.teacher, 'Тестовый педагог'); assert.equal(lesson.room, '17'); assert.equal(lesson.audience, 'Математика A · 7');
  assert.equal(campusStudentDays(raw)[0].items[0].canonical_title, 'Математика');
});
test('canonical no-lesson and unresolved states are not silently converted to ordinary lessons', () => {
  const raw = [{ date: '2026-09-21', state: 'NO_LESSON', activity: 'Нет урока', start_time: '09:00' }, { date: '2026-09-21', state: 'UNRESOLVED', activity: null, start_time: '10:00' }];
  assert.equal(campusLessons(raw).length, 1);
  assert.equal(campusLessons(raw)[0].subject, 'Расписание уточняется');
  assert.deepEqual(campusStudentDays(raw)[0].items.map((item) => item.state), ['no_lesson', 'conflict']);
});
test('legacy normalized lessons keep their dates and events keep their source state', () => {
  const raw = [{ lesson_date: '2026-09-22', subject: 'Экскурсия', start_time: '11:00', lesson_type: 'special_event' }];
  assert.equal(campusLessons(raw)[0].subject, 'Экскурсия');
  assert.equal(campusStudentDays(raw)[0].items[0].special_event.title, 'Экскурсия');
  assert.deepEqual(campusLessons(null), []);
});
