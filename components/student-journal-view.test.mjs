import test from 'node:test';
import assert from 'node:assert/strict';
import { journalKey, journalValue, recentStudentResults, studentJournalRows, studentResultText, studentSummaryPreview } from './student-journal-view.ts';

function journal(overrides = {}) {
  return { source: { source_id: 'math-I', subject: 'Математика', term_number: 1, period_status: 'current' },
    student: { journal_student_id: 'roster-A', group_marker: 'A' },
    assessments: [
      { id: 4, title: 'Без результата', assessed_on: '2026-09-24', group_marker: 'A' },
      { id: 3, title: 'Баллы', assessed_on: '2026-09-23', group_marker: 'A', max_score: 20 },
      { id: 0, title: 'Другая группа', assessed_on: '2026-09-20', group_marker: 'B' },
      { id: 2, title: 'Статус', assessed_on: '2026-09-22', group_marker: 'A' },
      { id: 1, title: 'Ноль', assessed_on: '2026-09-21', group_marker: 'all' },
    ], results: [{ assessment_id: 0, numeric_score: 99 }, { assessment_id: 1, numeric_score: 0 },
      { assessment_id: 2, numeric_score: null, status: 'Не был' }, { assessment_id: 3, numeric_score: 15 }], summaries: [], ...overrides };
}

test('details are chronological and contain only the pupil subgroup and shared assessments', () => {
  assert.deepEqual(studentJournalRows(journal()).map(r => r.assessment.id), [1, 2, 3, 4]);
});
test('compact list keeps the latest three actual results, including zero and special status', () => {
  assert.deepEqual(recentStudentResults(journal()).map(r => r.assessment.id), [3, 2, 1]);
  assert.equal(studentResultText(recentStudentResults(journal())[0].result, 20), '15 / 20');
  assert.equal(studentResultText({ numeric_score: 0 }), '0');
  assert.equal(studentResultText({ numeric_score: null, status: 'Не был' }), 'Не был');
  assert.equal(studentResultText({ numeric_score: 3, status: 'Пересдача' }), '3 · Пересдача');
  assert.equal(studentResultText({ numeric_score: 3, status: 'graded' }, 10), '3 / 10');
  assert.equal(studentResultText({ numeric_score: null, status: 'graded' }), '');
});
test('missing scores and formula errors never become zero or pollute recent results', () => {
  for (const value of [undefined, null, '', '#DIV/0!', NaN, {}, []]) assert.equal(journalValue(value), '');
  assert.equal(studentResultText(undefined), '');
  assert.equal(studentResultText({ numeric_score: null, status: null }), '');
  assert.equal(studentResultText({ numeric_score: 15 }, 0), '15');
});
test('only confirmed source summaries can be used as a compact attainment indicator', () => {
  assert.deepEqual(studentSummaryPreview(journal()), []); // scores 0 and 15 are never averaged
  const summaries = [{ summary_type: 'average_grade', label: 'Средняя оценка', numeric_value: 4.6 },
    { summary_type: 'points', label: 'Баллы', numeric_value: 15 },
    { summary_type: 'term_grade', label: 'Итог', numeric_value: null, text_value: '#N/A' }];
  assert.deepEqual(studentSummaryPreview(journal({ summaries })), [summaries[0]]);
  assert.equal(journalValue(4.6), '4,6');
});
test('independent source rosters remain distinct even when subject names coincide', () => {
  assert.notEqual(journalKey(journal()), journalKey(journal({ source: { source_id: 'math-II', subject: 'Математика', term_number: 2, period_status: 'archived' } })));
  assert.notEqual(journalKey(journal()), journalKey(journal({ student: { journal_student_id: 'roster-B', group_marker: 'B' } })));
});
