import test from 'node:test';
import assert from 'node:assert/strict';
import { legacyReviewReason, readableReviewNote } from './constructor-review-language.ts';

test('explains two genuinely different teacher warnings as separate actions', () => {
  assert.match(legacyReviewReason('Не подтверждён преподаватель').action, /по справочнику/);
  assert.match(legacyReviewReason('Нет подтверждённого назначения преподавателя группе').action, /этот предмет в группе/);
});

test('does not invent a reason or expose technical fallback as the display text', () => {
  for (const text of ['unsupported_audience_v3', 'Сбой: canonical blockId', 'Needs a manual roster validation (проверить)']) {
    assert.equal(readableReviewNote(text), '');
    assert.deepEqual(legacyReviewReason(text), {
      title: 'Нужно уточнить данные занятия', action: 'Откройте занятие и сверьте его с расписанием.',
    });
  }
  assert.equal(legacyReviewReason('Не указан вид занятия').title, 'Не указан вид занятия');
});

test('unverified historical group links do not require a matching lesson subject', () => {
  const explanation = legacyReviewReason('Для этого предмета не подтверждена учебная группа; прежняя математическая группа удалена');
  assert.equal(explanation.title, 'Проверьте связь с учебной группой');
  assert.match(explanation.action, /можно оставить/i);
  assert.doesNotMatch(explanation.title + explanation.action, /замен/i);
});
