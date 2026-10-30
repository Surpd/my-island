import test from 'node:test';
import assert from 'node:assert/strict';
import { linkedInstructionalGroupsForColumn, mergeScheduleDrafts, parseQuick, remapPastedLessonAudience, schedulePlacementClassIds } from './schedule-constructor-logic.ts';

const classes = [
  { id: 'class-7b', name: '7-Б', group_type: 'class' },
  { id: 'class-9a', name: '9-А', group_type: 'class' },
];
const groups = [
  ...classes,
  { id: 'math-7-1', name: '7-1', group_type: 'subject_group', subject: 'Математика', base_class_name: '7-Б', subject_subgroup: '1' },
  { id: 'english:7', name: 'Английский язык · группа 7', group_type: 'subject_group', subject: 'Английский язык', base_class_name: '7-Б', subject_subgroup: '7' },
];
const teachers = [
  { id: 'teacher-df', name: 'Дмитрий Филиппов', assignments: [{ group_id: 'math-7-1', subject: 'Математика', active: true }] },
  { id: 'teacher-vadim', name: 'Вадим Иванов', assignments: [{ group_id: 'class-7b', subject: 'Пластика', active: true }] },
];
const students = [{ id: 'student-1', groups: ['math-7-1', 'english:7'], classes: ['class-7b'] }];

test('quick entry recognizes a canonical 7-1 group, teacher initials, and a room number', () => {
  const parsed = parseQuick('Мат 7-1 ДФ 17', 'class-7b', groups, teachers, students, ['Математика']);
  assert.equal(parsed.subject, 'Математика');
  assert.deepEqual(parsed.groupIds, ['math-7-1']);
  assert.deepEqual(parsed.teacherIds, ['teacher-df']);
  assert.equal(parsed.room, '17');
  assert.deepEqual(parsed.unknown, []);
});

test('quick entry resolves numbered English groups and keeps a trailing room after a teacher', () => {
  const english = parseQuick('Англ 7', 'class-7b', groups, teachers, students, ['Английский язык']);
  assert.deepEqual(english.groupIds, ['english:7']);
  assert.equal(english.room, '');
  assert.deepEqual(english.unknown, []);

  const plastic = parseQuick('Пластика Вадим 9', 'class-7b', groups, teachers, students, ['Пластика']);
  assert.equal(plastic.room, '9');
  assert.deepEqual(plastic.teacherIds, ['teacher-vadim']);
  assert.deepEqual(plastic.unknown, []);
});

test('quick entry treats a numbered cross-grade English group as a group marker, not a class or room', () => {
  const crossGradeGroup = { id: 'english:6', name: 'Английский язык · группа 6', group_type: 'instructional', subject: 'Английский язык', subject_subgroup: '6' };
  const crossGradeStudents = [{ id: 'student-10', groups: ['english:6'], classes: ['class-10'] }];
  const parsed = parseQuick('Англ 6', 'class-7b', [...groups, crossGradeGroup], teachers, crossGradeStudents, ['Английский язык']);
  assert.deepEqual(parsed.groupIds, ['english:6']);
  assert.equal(parsed.room, '');
  assert.deepEqual(parsed.unknown, []);
});

test('quick entry asks for an explicit subject when the same token names multiple subjects', () => {
  const parsed = parseQuick('Английский 7', 'class-7b', groups, teachers, students, ['Английский язык', 'Английский']);
  assert.equal(parsed.subject, '');
  assert.deepEqual(parsed.subjects, ['Английский язык', 'Английский']);
  assert.deepEqual(parsed.unknown, []);
});

const draft = (lessons, revision = 1) => ({
  id: 'draft', name: 'Week', revision,
  payload: { days: [0, 1], slots: [{ start: '09:00', end: '09:45' }], classes: ['class-7b'], lessons },
});

test('three-way merge composes a local added lesson with an unrelated server edit', () => {
  const base = draft([{ id: 'a', subject: 'Математика', teacherIds: ['t1', 't2'], groupIds: ['g1', 'g2'], room: '12' }]);
  const local = draft([...base.payload.lessons, { id: 'local-new', subject: 'Пластика', day: 0, period: 0 }], 1);
  const server = draft([{ ...base.payload.lessons[0], room: '14' }], 2);
  const result = mergeScheduleDrafts(base, local, server);
  assert.deepEqual(result.conflicts, []);
  assert.equal(result.draft.payload.lessons.length, 2);
  assert.equal(result.draft.payload.lessons.find((item) => item.id === 'a').room, '14');
  assert.equal(result.draft.payload.lessons.find((item) => item.id === 'local-new').subject, 'Пластика');
});

test('three-way merge ignores object-key order and set ordering but detects a real same-field conflict', () => {
  const base = draft([{ id: 'a', subject: 'Математика', teacherIds: ['t1', 't2'], audience_snapshot: { student_ids: ['s1', 's2'], resolved_at: 'old' } }]);
  const local = draft([{ id: 'a', subject: 'Алгебра', teacherIds: ['t2', 't1'], audience_snapshot: { student_ids: ['s2', 's1'], resolved_at: 'local-time' } }]);
  const server = draft([{ audience_snapshot: { resolved_at: 'server-time', student_ids: ['s1', 's2'] }, teacherIds: ['t1', 't2'], subject: 'Геометрия', id: 'a' }], 2);
  const result = mergeScheduleDrafts(base, local, server);
  assert.equal(result.conflicts.length, 1);
  assert.equal(result.conflicts[0].field, 'subject');
  const resolved = mergeScheduleDrafts(base, local, server, { [result.conflicts[0].key]: 'server' });
  assert.equal(resolved.draft.payload.lessons[0].subject, 'Геометрия');
  assert.deepEqual([...resolved.draft.payload.lessons[0].audience_snapshot.student_ids].sort((a, b) => a.localeCompare(b)), ['s1', 's2']);
});

test('three-way merge preserves a genuine audience membership conflict for explicit choice', () => {
  const base = draft([{ id: 'a', subject: 'Английский', audience_snapshot: { student_ids: ['s1', 's2'] } }]);
  const local = draft([{ id: 'a', subject: 'Английский', audience_snapshot: { student_ids: ['s1'] } }]);
  const server = draft([{ id: 'a', subject: 'Английский', audience_snapshot: { student_ids: ['s2'] } }], 2);
  const result = mergeScheduleDrafts(base, local, server);
  assert.deepEqual(result.conflicts.map((item) => item.field), ['audience_snapshot']);
  const resolved = mergeScheduleDrafts(base, local, server, { [result.conflicts[0].key]: 'server' });
  assert.deepEqual(resolved.draft.payload.lessons[0].audience_snapshot.student_ids, ['s2']);
});

test('grid placement uses club display classes and falls back to the selected column', () => {
  assert.deepEqual(schedulePlacementClassIds({ activityType: 'club', classIds: [], displayClassIds: ['c7', 'c8'] }, ['c1']), ['c7', 'c8']);
  assert.deepEqual(schedulePlacementClassIds({ classIds: ['c7'], classId: 'c8' }, ['c1']), ['c7']);
  assert.deepEqual(schedulePlacementClassIds({ classIds: [] }, ['c1', 'c2']), ['c1', 'c2']);
});

test('linked instructional groups narrow to canonical members in the current class column', () => {
  const linked = [
    { id: 'english-a', group_type: 'instructional' },
    { id: 'english-b', group_type: 'instructional' },
  ];
  const lesson = { groupIds: ['english-a', 'english-b'] };
  const students = [
    { id: 's1', groups: ['english-a'], classes: ['class-7b'] },
    { id: 's2', groups: ['english-b'], classes: ['class-9a'] },
  ];
  assert.deepEqual(linkedInstructionalGroupsForColumn(lesson, linked, students, ['class-7b']).map((item) => item.id), ['english-a']);
  assert.deepEqual(linkedInstructionalGroupsForColumn(lesson, linked, [], ['class-7b']).map((item) => item.id), ['english-a', 'english-b']);
});

test('pasting remaps class audiences but preserves explicit groups, students, and empty club participants', () => {
  const classLesson = { id: 'old', classId: 'class-7b', classIds: ['class-7b'], groupId: 'class-7b', groupIds: ['class-7b'], audience: { kind: 'classes', class_ids: ['class-7b'], grade: '7' }, audience_snapshot: { student_ids: ['old-roster'], resolved_at: 'old' } };
  assert.deepEqual(remapPastedLessonAudience(classLesson, ['class-9a'], 'new'), {
    id: 'new', classId: 'class-9a', classIds: ['class-9a'], groupId: 'class-9a', groupIds: ['class-9a'], audience: { kind: 'classes', class_ids: ['class-9a'] },
  });
  assert.equal(Object.hasOwn(remapPastedLessonAudience(classLesson, ['class-9a'], 'new'), 'audience_snapshot'), false);
  assert.deepEqual(remapPastedLessonAudience(classLesson, ['class-7b'], 'same-class-copy').audience_snapshot, classLesson.audience_snapshot);
  const groupLesson = { id: 'old', groupId: 'english:7', groupIds: ['english:7'], classIds: ['class-7b'], audience: { kind: 'groups', group_ids: ['english:7'] } };
  assert.deepEqual(remapPastedLessonAudience(groupLesson, ['class-9a'], 'new'), { ...groupLesson, id: 'new' });
  const club = { id: 'old', activityType: 'club', classId: '', classIds: [], displayClassIds: ['class-7b'], audience: { kind: 'students', student_ids: [] } };
  assert.deepEqual(remapPastedLessonAudience(club, ['class-9a'], 'new'), { ...club, id: 'new', displayClassIds: ['class-9a'] });
});
