// Presentation helpers for the existing /api/student/journals contract.
export type StudentJournalItem = {
  source: {
    source_id: string | number; spreadsheet_title: string; grade: number;
    subject: string; sheet_title: string; academic_year: string;
    term_number?: number | null; period_status: string; last_synced_at?: string | null;
  };
  student: { journal_student_id: string | number; display_name: string; group_marker: string };
  assessments: Array<{
    id: string | number; title: string; assessed_on?: string | null;
    weight?: number | null; max_score?: number | null; group_marker: string;
  }>;
  results: Array<{ assessment_id: string | number; numeric_score: number | null; status?: string | null }>;
  summaries: Array<{ label: string; summary_type: string; numeric_value: number | null; text_value: string | null }>;
};

export function journalKey(item: StudentJournalItem): string {
  return `${item.source.source_id}:${item.student.journal_student_id}`;
}

export function journalValue(value: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return '';
  const text = String(value).trim();
  if (!text || text.startsWith('#') || (typeof value === 'number' && !Number.isFinite(value))) return '';
  if (/^true$/i.test(text)) return 'Выполнено';
  if (/^false$/i.test(text)) return 'Не выполнено';
  return typeof value === 'number' ? text.replace('.', ',') : text;
}

export function studentJournalRows(item: StudentJournalItem) {
  const results = new Map(item.results.map(result => [String(result.assessment_id), result]));
  return item.assessments
    .filter(assessment => !assessment.group_marker || assessment.group_marker === 'all' || assessment.group_marker === item.student.group_marker)
    .map(assessment => ({ assessment, result: results.get(String(assessment.id)) }))
    .sort((a, b) => (a.assessment.assessed_on || '9999').localeCompare(b.assessment.assessed_on || '9999'));
}

export function studentResultText(result: StudentJournalItem['results'][number] | undefined, maxScore?: number | null): string {
  const score = journalValue(result?.numeric_score);
  const rawStatus = journalValue(result?.status);
  // "graded" marks a stored numeric result, rather than a pupil-facing special status.
  const status = rawStatus.toLowerCase() === 'graded' ? '' : rawStatus;
  const maximum = typeof maxScore === 'number' && Number.isFinite(maxScore) && maxScore > 0 ? journalValue(maxScore) : '';
  return [score ? `${score}${maximum ? ` / ${maximum}` : ''}` : '', status].filter(Boolean).join(' · ');
}

export function recentStudentResults(item: StudentJournalItem) {
  return studentJournalRows(item)
    .filter(row => studentResultText(row.result))
    .slice(-3).reverse();
}

export function studentSummaryPreview(item: StudentJournalItem) {
  // Never infer a grading scale or derive an average from assessment points.
  return item.summaries.filter(summary => ['average_grade', 'term_grade', 'final_grade'].includes(summary.summary_type)
    && journalValue(summary.numeric_value ?? summary.text_value)).slice(0, 2);
}
