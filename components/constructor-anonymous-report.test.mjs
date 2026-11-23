import test from 'node:test';
import assert from 'node:assert/strict';
import { anonymousReviewReport } from './constructor-anonymous-report.ts';

const secret = 'PRIVATE PERSON sentinel https://private.example/?token=SECRET';
const target = {draftId: secret, revision: 38, scope: 'week', week: '2026-11-23'};
const preview = {
  can_publish: true, blockers: [], summary: {conflict_lessons: 2},
  diagnostics: [
    {code: 'stale_group_subject_mismatch', lesson_id: 'stale-uuid', message: secret, context: {teachers: [secret]}},
    {code: 'teacher_overlap', lesson_id: 'conflict-uuid', related_lesson_ids: ['peer-uuid'], message: secret, affected_student_ids: [secret]},
    {code: 'teacher_overlap', lesson_id: 'peer-uuid', related_lesson_ids: ['conflict-uuid'], message: secret},
    {code: secret, lesson_id: secret, message: secret},
  ],
  deferred: [
    {lesson_id: 'conflict-uuid', action: 'retained_unresolved', reason: secret},
    {lesson_id: secret, action: secret, reason: secret},
  ],
};
const slots = {'stale-uuid': {day: 0, start: '09:55', end: '10:40', subject: secret},
               'conflict-uuid': {day: 1, start: '10:55', end: '11:40'}, [secret]: {day: 3, start: secret, end: secret}};
const options = {target, loaded: target, preview, slots, dirty: false};

test('known exclusion is separate from a stale warning and conflict count is not summed', () => {
  const report = anonymousReviewReport(options);
  assert.match(report, /Разных занятий с конфликтами: 2/);
  assert.match(report, /Старая привязка учебной группы.*Запись 1 · Пн 09:55–10:40/);
  assert.match(report, /пересекаются занятия.*Запись 2 · Вт 10:55–11:40; Запись 3/);
  const outcomes = report.split('Подтверждённые последствия')[1];
  assert.match(outcomes, /Запись 2 · Вт 10:55–11:40: Непроверенное назначение не попадёт/);
  assert.doesNotMatch(outcomes, /Запись 1/);
  assert.doesNotMatch(report, /PRIVATE PERSON|private\.example|SECRET|uuid|teacher_overlap/);
});

test('unknown preview, revision, draft and scope never copy stale report', () => {
  for (const changed of [{loaded: null}, {preview: null}, {dirty: true}, {loaded: {...target, week:'2026-11-30'}},
                         {loaded: {...target, revision: 37}}, {loaded: {...target, draftId: 'other'}}, {loaded: {...target, scope: 'template'}}]) {
    assert.equal(anonymousReviewReport({...options, ...changed}), null);
  }
  const report = anonymousReviewReport({...options, preview: {...preview, summary: undefined, deferred: []}});
  assert.match(report, /Число разных занятий с конфликтами: не подтверждено/);
  assert.match(report, /Это не подтверждение выдачи остальных занятий/);
});

test('contradictory outcomes stay unknown and preserved prior version is not withheld', () => {
  const report = anonymousReviewReport({...options, preview: {...preview, deferred: [
    {lesson_id: 'stale-uuid', action: 'retained_unresolved'}, {lesson_id: 'stale-uuid', action: 'preserved_base'},
    {lesson_id: 'conflict-uuid', action: 'preserved_base'},
  ]}});
  assert.match(report, /Запись 2 · Вт 10:55–11:40: Сохранится предыдущая опубликованная запись/);
  assert.doesNotMatch(report.split('Подтверждённые последствия')[1], /Непроверенное назначение|Запись 1/);
});
