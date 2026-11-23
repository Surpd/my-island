import test from 'node:test';
import assert from 'node:assert/strict';
import { schoolGroupBoardSection as section } from './school-group-board.ts';

const enrollment = (group, grades) => grades.map(grade => ({
  class_name: grade, memberships: [{ group_id: group.id }],
}));

test('moves only the exact canonical program records out of the ordinary board buckets', () => {
  for (const [name, display_name] of [
    ['generic:course-choice', 'Курс по выбору'],
    ['generic:digital-track', 'Цифровой трек'],
  ]) {
    const group = { id: name, name, display_name, role: 'special_activity_group' };
    const students = enrollment(group, ['7-А', '8']);
    assert.equal(section(group, students), 'Курсы по выбору');
    assert.equal(section({ ...group, display_name: 'Переименован' }, students), 'Курсы по выбору');
    assert.equal(section({ ...group, name: 'not-canonical' }, students), 'Межвозрастные группы');
  }
});

test('keeps real inter-age English and advanced grade 11 Maths visible', () => {
  for (const name of ['english:9', 'math:11:advanced']) {
    const group = { id: name, name, role: 'instructional_partition', base_class_name: '11' };
    assert.equal(section(group, enrollment(group, ['10', '11'])), 'Межвозрастные группы');
    assert.equal(section(group, enrollment(group, ['11', '11'])), '11 класс');
  }
});

test('retains grade metadata fallback and neutral unknown-grade bucket', () => {
  assert.equal(section({ name: 'math:11:base', base_class_name: '11' }, []), '11 класс');
  assert.equal(section({ name: 'unclassified' }, []), 'Другие группы');
});
