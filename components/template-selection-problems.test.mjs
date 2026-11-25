import test from 'node:test';
import assert from 'node:assert/strict';
import { groupSelectionProblems, primaryRevisionActionState, selectionProblemKind } from './template-selection-problems.ts';

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

test('apply action is limited to a clean saved unapplied primary school revision', () => {
  const ready = { primary: true, schoolTemplate: true, selectionStale: true,
    draftRevision: 12, summaryRevision: 12, editorRevision: 12,
    dirty: false, saving: false, readOnly: false };
  assert.deepEqual(primaryRevisionActionState(ready), { visible: true, revisionsMatch: true, enabled: true });
  for (const patch of [
    { dirty: true }, { saving: true }, { readOnly: true },
    { summaryRevision: 11 }, { editorRevision: 11 },
  ]) assert.equal(primaryRevisionActionState({ ...ready, ...patch }).enabled, false);
  assert.equal(primaryRevisionActionState({ ...ready, selectionStale: false }).visible, false);
  assert.equal(primaryRevisionActionState({ ...ready, primary: false }).visible, false);
  assert.equal(primaryRevisionActionState({ ...ready, schoolTemplate: false }).visible, false);
});
