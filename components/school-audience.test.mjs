import test from 'node:test';
import assert from 'node:assert/strict';
import { audienceLabel, audienceShortLabel, classLabel, lessonLabel, subjectLabel } from './school-audience.ts';

const syntheticEnglishGroups = Array.from({ length: 10 }, (_, index) => {
  const number = String(index + 1);
  return {
    id: `synthetic-english-${number}`,
    name: `english:${number}`,
    subject: 'Английский язык',
    subject_subgroup: number,
  };
});

const syntheticDirectoryGroups = [
  ...syntheticEnglishGroups,
  { id: 'synthetic-math-7-1', name: 'grade7:split:1', group_type: 'instructional', semantic_dimension: 'grade7_shared_split', role: 'instructional_partition', subject: 'Математика', subject_subgroup: '1' },
  { id: 'synthetic-math-9-base', name: 'math:9:base', display_name: 'Математика · 9 класс · База', subject: 'Математика', base_class_name: '9', subject_subgroup: 'Base' },
  { id: 'synthetic-course-choice', name: 'course-choice', display_name: 'Курс по выбору', subject: 'Курс по выбору' },
];

test('normalizes common subject names and class labels', () => {
  assert.equal(subjectLabel('Russian'), 'Русский язык');
  assert.equal(subjectLabel('английский'), 'Английский язык');
  assert.equal(classLabel('9-Ан'), '9-Ан');
  assert.equal(classLabel('7-А'), '7-А');
  assert.equal(classLabel('5 класс'), '5 класс');
  assert.equal(classLabel('11 · базовый класс'), '11 класс');
  assert.equal(classLabel('9 · базовый класс'), '9 класс');
  assert.equal(classLabel('9 A · базовый класс'), '9-А');
  assert.equal(classLabel('9-D · базовый класс'), '9-D');
});

test('keeps group subject separate from lesson subject and audience suffix', () => {
  const mathSplit = { id: 'm71', name: 'grade7:split:1', group_type: 'instructional', semantic_dimension: 'grade7_shared_split', role: 'instructional_partition', subject: 'Math' };
  assert.equal(audienceLabel(mathSplit), 'Математика · 7-1');
  assert.equal(lessonLabel('Physics', mathSplit), 'Физика · 7-1');
  assert.equal(lessonLabel('Physics', 'Математика · 7-1'), 'Физика · 7-1');
  assert.equal(audienceShortLabel(mathSplit), '7-1');
});

test('renders course groups once in a human readable label', () => {
  const group = { name: 'Информатика · 9 класс · База', display_name: 'Информатика · 9 класс · База', subject: 'Информатика', group_type: 'instructional', course: 'База' };
  assert.equal(audienceLabel(group), 'Информатика · 9 класс · База');
});

test('normalizes canonical school metadata without duplicating the displayed subject or course', () => {
  assert.equal(audienceLabel({ name: 'english:6', display_name: 'Английский · группа 6', subject: 'Английский язык', subject_subgroup: '6' }), 'Английский язык · группа 6');
  assert.equal(audienceLabel({ name: 'grade9-math-A', display_name: 'Математика · группа A · 9 класс', subject: 'Математика', base_class_name: '9', subject_subgroup: 'A' }), 'Математика · 9-A');
  assert.equal(audienceLabel({ name: 'math:11:advanced', display_name: 'Математика · 11 класс · Углублённая', subject: 'Математика', base_class_name: '11', subject_subgroup: 'Advanced' }), 'Математика · 11 класс · Углублённая');
  assert.equal(audienceLabel({ name: 'math:11:base', display_name: 'Математика · 11 класс · Базовая', subject: 'Математика', base_class_name: '11', subject_subgroup: 'Base' }), 'Математика · 11 класс · База');
  assert.equal(audienceLabel({ name: 'instructional:география:9:огэ', display_name: 'География · 9 класс · ОГЭ', subject: 'География', base_class_name: '9', subject_subgroup: 'ОГЭ', exam_track: 'ОГЭ' }), 'География · 9 класс · ОГЭ');
  const mathGroup = { name: 'grade9-math-A', display_name: 'Математика · группа A · 9 класс', subject: 'Математика', base_class_name: '9', subject_subgroup: 'A' };
  assert.equal(audienceLabel({ ...mathGroup, name: audienceLabel(mathGroup), display_name: undefined }), 'Математика · 9-A');
  assert.equal(audienceLabel({ name: 'course-choice', display_name: 'Курс по выбору', subject: 'Курс по выбору' }), 'Курс по выбору');
  assert.equal(audienceLabel({ name: 'english:6', subject: 'Английский язык', subject_subgroup: '6' }), 'Английский язык · группа 6');
  assert.equal(audienceLabel({ name: 'instructional:math:7:1', subject: 'Математика', base_class_name: '7', subject_subgroup: '1' }), 'Математика · 7-1');
  assert.equal(audienceLabel({ name: 'instructional:обществознание:10:угл', display_name: 'Обществознание · 10 класс · Угл', subject: 'Обществознание', base_class_name: '10', subject_subgroup: 'Угл' }), 'Обществознание · 10 класс · Углублённая');
});

test('renders synthetic canonical English groups with their original numbers', () => {
  assert.equal(syntheticEnglishGroups.length, 10);
  for (const group of syntheticEnglishGroups) {
    const number = group.subject_subgroup;
    assert.equal(
      audienceLabel(group),
      `Английский язык · группа ${number}`,
    );
  }
});

test('retains an edited human group caption alongside its canonical metadata', () => {
  assert.equal(audienceLabel({ name: 'english:6', display_name: 'Разговорный клуб · группа 6', subject: 'Английский язык', subject_subgroup: '6' }), 'Английский язык · группа 6 · Разговорный клуб');
});

test('labels remain unique and idempotent across synthetic canonical directory groups', () => {
  const labels = syntheticDirectoryGroups.map(audienceLabel);
  assert.equal(new Set(labels).size, syntheticDirectoryGroups.length, 'canonical school labels must not collide');
  for (const group of syntheticDirectoryGroups) {
    const label = audienceLabel(group);
    assert.equal(audienceLabel({ ...group, name: label, display_name: undefined }), label, `${group.id} label should be stable`);
  }
});

test('class labels keep the class suffix even when base grade is generic', () => {
  for (const name of ['7А', '7Б', '10А', '11А', '9-Ан', '9-Д']) {
    assert.equal(audienceLabel({ name: 'class-opaque', display_name: name, group_type: 'class', base_class_name: name.match(/^\d+/)[0] }), name);
  }
  assert.equal(audienceLabel({ name: 'class:opaque', group_type: 'class', base_class_name: '7' }), '7 класс');
});
