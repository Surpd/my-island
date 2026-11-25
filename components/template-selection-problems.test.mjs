import test from 'node:test';
import assert from 'node:assert/strict';
import { groupSelectionProblems, selectionProblemKind } from './template-selection-problems.ts';

test('classifies the current backend problem format without category fields', () => {
  const problems = [
    { lesson_id: 'legacy', details: ['Старая причина', 'needsClarification'] },
    { lesson_id: 'teacher', details: ['Конфликт преподавателя: Преподаватель'] },
    { lesson_id: 'both', details: ['Конфликт группы: 10 класс', 'Конфликт преподавателя: Преподаватель'] },
    { lesson_id: 'selected-template', details: ['Историческая ревизия недоступна. Сначала утвердите базу.'] },
    { lesson_id: 'future', details: ['Новый неизвестный формат'] },
  ];
  const grouped = groupSelectionProblems(problems);
  assert.deepEqual(grouped.legacy.map(item => item.lesson_id), ['legacy']);
  assert.deepEqual(grouped.heuristic.map(item => item.lesson_id), ['teacher', 'both']);
  assert.deepEqual(grouped.missingBaseline.map(item => item.lesson_id), ['selected-template']);
  assert.deepEqual(grouped.unknown.map(item => item.lesson_id), ['future']);
});

test('does not silently treat an arbitrary unrecognized detail as a legacy marker', () => {
  assert.equal(selectionProblemKind({ lesson_id: 'future', details: ['Конфликт кабинета: 6'] }), 'unknown');
  assert.equal(selectionProblemKind({ lesson_id: 'import', details: ['importError'] }), 'legacy');
});
