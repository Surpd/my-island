import test from 'node:test';
import assert from 'node:assert/strict';
import { isSchoolGroupBoardVisible as visible, schoolGroupBoardSection as section } from './school-group-board.ts';

const enrollment = (group, grades) => grades.map(grade => ({
  class_name: grade, memberships: [{ group_id: group.id }],
}));

test('hides only the exact legacy records without modifying them', () => {
  for (const [name, display_name] of [
    ['generic:course-choice', 'Курс по выбору'],
    ['generic:digital-track', 'Цифровой трек'],
  ]) {
    const group = { id: name, name, display_name, role: 'special_activity_group' };
    const students = enrollment(group, ['7-А', '8']);
    const snapshot = structuredClone({ group, students });
    assert.equal(visible(group), false);
    assert.equal(visible({ ...group, display_name: 'Переименован' }), false);
    assert.equal(visible({ ...group, name: 'not-canonical' }), true);
    assert.equal(visible({ ...group, name: name + '-other' }), true);
    assert.deepEqual({ group, students }, snapshot);
  }
});

test('keeps real inter-age English and advanced grade 11 Maths visible', () => {
  for (const name of ['english:9', 'math:11:advanced']) {
    const group = { id: name, name, role: 'instructional_partition', base_class_name: '11' };
    assert.equal(visible(group), true);
    assert.equal(section(group, enrollment(group, ['10', '11'])), 'Межвозрастные группы');
    assert.equal(section(group, enrollment(group, ['11', '11'])), '11 класс');
  }
});

test('retains grade metadata fallback and neutral unknown-grade bucket', () => {
  assert.equal(section({ name: 'math:11:base', base_class_name: '11' }, []), '11 класс');
  assert.equal(section({ name: 'unclassified' }, []), 'Другие группы');
});
