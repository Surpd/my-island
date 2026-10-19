import assert from 'node:assert/strict';
import test from 'node:test';
import { groupCourseContext, parseQuick, rankTeachersForAudience, shortGroup, shortSubject } from '../components/schedule-constructor-logic.ts';

const groups = [
  { id: '5', name: '5 класс', group_type: 'class' },
  { id: '6', name: '6 класс', group_type: 'class' },
  { id: '7a', name: '7-А', group_type: 'class' },
  { id: '9an', name: '9-Ан', group_type: 'class' },
  { id: '9d', name: '9-Д', group_type: 'class' },
  { id: 'math-a', name: 'Математика A · 9', group_type: 'instructional', base_class_name: '9' },
  { id: 'math-a-copy', name: 'Математика А · 9', group_type: 'instructional', base_class_name: '9' },
  { id: 'oge', name: 'Физика ОГЭ · 9', group_type: 'instructional', base_class_name: '9' },
];
const teachers = [
  { id: 'df', name: 'Дмитрий Филиппов', assignments: [] },
  { id: 'angel', name: 'Ангелина Смирнова', assignments: [] },
  { id: 'ia', name: 'Ирина Алексеева', assignments: [] },
  { id: 'ivan', name: 'Иван Кузнецов', assignments: [] },
  { id: 'yulia', name: 'Юлия Орлова', assignments: [] },
];
const subjects = ['Математика', 'Английский язык', 'Русский язык', 'Физика', 'Литература'];
const parse = (value, classId = '9an') => parseQuick(value, classId, groups, teachers, [], subjects);

test('compact labels preserve subject and instructional group', () => {
  assert.equal(shortSubject('Математика'), 'Мат');
  assert.equal(shortSubject('Английский язык'), 'Англ');
  assert.equal(shortGroup('9А Математика A', 'Математика', '9'), 'A');
});

test('quick entry distinguishes class from mathematical group and retains ambiguity', () => {
  const result = parse('мат 9 A ДФ 17');
  assert.equal(result.subject, 'Математика');
  assert.equal(result.room, '17');
  assert.deepEqual(result.teacherIds, ['df']);
  assert.deepEqual(result.groupIds, ['math-a', 'math-a-copy']);
  assert.deepEqual(result.classIds, ['9an', '9d']);
  assert.deepEqual(result.unknown, []);
  assert.deepEqual(parse('литература 9-Ан Юлия 17').classIds, ['9an']);
});

test('quick entry recognizes grade, teacher and room without treating room as class', () => {
  assert.deepEqual(parse('англ 6 Ангелина 9').classIds, ['6']);
  assert.deepEqual(parse('русский 7-А ИА 6').classIds, ['7a']);
  assert.deepEqual(parse('физика ОГЭ Иван 18').groupIds, ['oge']);
  assert.deepEqual(parse('физика ОГЭ неизвестный 18').unknown, ['неизвестный']);
});

test('teacher default prefers exact group, then class, then grade and leaves ties unresolved', () => {
  const directory = [
    ...groups,
    { id: 'grade-9', name: '9 класс', group_type: 'grade', base_class_name: '9' },
  ];
  const candidates = [
    { id: 'grade', name: 'Учитель параллели', assignments: [{ group_id: '', grade_level: 9, subject: 'Математика' }] },
    { id: 'class', name: 'Учитель класса', assignments: [{ group_id: '9an', subject: 'Математика' }] },
    { id: 'group', name: 'Учитель группы', assignments: [{ group_id: 'math-a', subject: 'Математика' }] },
  ];
  const context = { groupIds: ['math-a'], classIds: ['9an'], subject: 'Математика' };
  assert.equal(rankTeachersForAudience(candidates, directory, context).defaultTeacherId, 'group');
  assert.equal(rankTeachersForAudience(candidates.slice(0, 2), directory, context).defaultTeacherId, 'class');
  assert.equal(rankTeachersForAudience(candidates.slice(0, 1), directory, context).defaultTeacherId, 'grade');
  assert.equal(rankTeachersForAudience([
    { ...candidates[0], assignments: [{ group_id: 'oge', subject: 'Математика' }] },
  ], directory, context).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience([
    candidates[2], { ...candidates[2], id: 'group-copy', name: 'Другой учитель' },
  ], directory, context).defaultTeacherId, '');
});

test('class priority is inferred from selected group roster only when it maps to one class', () => {
  const directory = [...groups, { id: 'grade-9', name: '9 класс', group_type: 'grade', base_class_name: '9' }];
  const candidates = [
    { id: 'class', name: 'Учитель класса', assignments: [{ group_id: '9an', subject: 'Математика' }] },
    { id: 'grade', name: 'Учитель параллели', assignments: [{ grade_level: '9', subject: 'Математика' }] },
  ];
  const oneClassRoster = [{ id: 'student-1', groups: ['math-a'], classes: ['9an'] }];
  assert.equal(rankTeachersForAudience(candidates, directory,
    { groupIds: ['math-a'], subject: 'Математика' }, [], oneClassRoster).defaultTeacherId, 'class');

  const mixedRoster = [
    ...oneClassRoster,
    { id: 'student-2', groups: ['math-a'], classes: ['9d'] },
  ];
  assert.equal(rankTeachersForAudience(candidates, directory,
    { groupIds: ['math-a'], subject: 'Математика' }, [], mixedRoster).defaultTeacherId, 'grade');
});

test('teacher default requires matching subject and course and ignores unavailable teachers', () => {
  const directory = [...groups, { id: 'course-a', name: 'Математика A · 9', group_type: 'instructional', base_class_name: '9' }];
  const candidates = [
    { id: 'wrong-subject', name: 'Другой предмет', assignments: [{ group_id: 'math-a', subject: 'Физика' }] },
    { id: 'wrong-course', name: 'Другой курс', assignments: [{ group_id: 'math-a', subject: 'Математика', course: 'B' }] },
    { id: 'right', name: 'Нужный курс', assignments: [{ group_id: 'math-a', subject: 'Математика', course: 'A' }] },
  ];
  const context = { groupIds: ['math-a'], subject: 'Математика', course: 'A' };
  assert.equal(rankTeachersForAudience(candidates, directory, context).defaultTeacherId, 'right');
  assert.equal(rankTeachersForAudience(candidates, directory, { ...context, subject: '' }).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience(candidates, directory, context, ['right']).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience(candidates, directory, { groupIds: ['math-a'], subject: 'Математика' }).defaultTeacherId, '');
  const exam = [{ id: 'oge', name: 'ОГЭ', assignments: [{ grade_level: '9', subject: 'Физика', exam_track: 'ОГЭ' }] }];
  assert.equal(rankTeachersForAudience(exam, directory, { grade: '9', subject: 'Физика' }).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience(exam, directory, { grade: '9', subject: 'Физика', examTrack: 'ОГЭ' }).defaultTeacherId, 'oge');
});

test('teacher defaults ignore inactive and out-of-date assignments and normalize subject variants', () => {
  const directory = [...groups, { id: 'grade-9', name: '9 класс', group_type: 'grade', base_class_name: '9' }];
  const candidates = [
    { id: 'inactive', name: 'Неактивный', assignments: [{ group_id: 'math-a', subject: 'Русский', active: false }] },
    { id: 'future', name: 'Будущий', assignments: [{ group_id: 'math-a', subject: 'Русский язык', valid_from: '2999-01-01' }] },
    { id: 'expired', name: 'Истёкший', assignments: [{ group_id: 'math-a', subject: 'Русский язык', valid_until: '2000-01-01' }] },
  ];
  assert.equal(rankTeachersForAudience(candidates, directory, { groupIds: ['math-a'], subject: 'Русский' }).defaultTeacherId, '');
  candidates.push({ id: 'current', name: 'Действующий', assignments: [{ group_id: 'math-a', subject: 'Русский язык' }] });
  assert.equal(rankTeachersForAudience(candidates, directory, { groupIds: ['math-a'], subject: 'Русский' }).defaultTeacherId, 'current');
});

test('grade defaults require one canonical audience grade and explicit grade_level assignments', () => {
  const directory = [
    { id: '9an', name: '9-Ан', group_type: 'class' },
    { id: '9d', name: '9-Д', group_type: 'class' },
    { id: '10a', name: '10-А', group_type: 'class' },
    { id: 'math-a', name: 'Математика A', group_type: 'instructional' },
  ];
  const candidates = [
    { id: 'grade-9', name: 'Параллель 9', assignments: [{ group_id: '', grade_level: '9', subject: 'Математика' }] },
    { id: 'random-group', name: 'Случайная группа', assignments: [{ group_id: 'oge', subject: 'Математика' }] },
  ];
  assert.equal(rankTeachersForAudience(candidates, directory, { groupIds: ['math-a'], subject: 'Математика' }).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience(candidates, directory, { classIds: ['9an', '10a'], subject: 'Математика' }).defaultTeacherId, '');
  assert.equal(rankTeachersForAudience(candidates, directory, { classIds: ['9an', '9d'], subject: 'Математика' }).defaultTeacherId, 'grade-9');
});

test('base and profile group metadata remains a separate course context', () => {
  assert.equal(groupCourseContext({ subject_subgroup: 'Base' }), 'База');
  assert.equal(groupCourseContext({ subject_subgroup: 'Advanced' }), 'Профиль');
  assert.equal(groupCourseContext({ subject_subgroup: '1' }), '');
  assert.equal(groupCourseContext({ exam_track: 'ОГЭ', subject_subgroup: 'ОГЭ' }), 'ОГЭ');
  const candidates = [{ id: 'profile', name: 'Профиль', assignments: [{ group_id: 'math11', subject: 'Математика', exam_track: 'Профиль' }] }];
  assert.equal(rankTeachersForAudience(candidates, [{ id: 'math11', name: 'Математика 11', group_type: 'instructional', base_class_name: '11' }], { groupIds: ['math11'], subject: 'Математика', examTrack: 'Профиль' }).defaultTeacherId, 'profile');
  assert.equal(rankTeachersForAudience(candidates, [{ id: 'math11', name: 'Математика 11', group_type: 'instructional', base_class_name: '11' }], { groupIds: ['math11'], subject: 'Математика' }).defaultTeacherId, '');
});
