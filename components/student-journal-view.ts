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

export function studentJournalRows(item: StudentJournalItem, newestFirst = false) {
  const results = new Map(item.results.map(result => [String(result.assessment_id), result]));
  return item.assessments
    .filter(assessment => !assessment.group_marker || assessment.group_marker === 'all' || assessment.group_marker === item.student.group_marker)
    .map(assessment => ({ assessment, result: results.get(String(assessment.id)) }))
    .sort((a, b) => {
      const left = a.assessment.assessed_on;
      const right = b.assessment.assessed_on;
      if (!left || !right) return left ? -1 : right ? 1 : 0;
      return newestFirst ? right.localeCompare(left) : left.localeCompare(right);
    });
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
  return studentJournalRows(item, true)
    .filter(row => typeof row.result?.numeric_score === 'number' && Number.isFinite(row.result.numeric_score))
    .slice(0, 3);
}

export function recentStudentStatuses(item: StudentJournalItem) {
  return studentJournalRows(item, true)
    .map(row => ({ ...row, status: compactStudentStatus(row.result?.status) }))
    .filter(row => (row.result?.numeric_score === null || row.result?.numeric_score === undefined) && Boolean(row.status))
    .slice(0, 2);
}

/** Only recognized pupil-facing outcomes belong in the compact subject row. */
function compactStudentStatus(value: unknown): string {
  const raw = journalValue(value).trim();
  if (!raw) return '';
  const normalized = raw.toLocaleLowerCase('ru-RU').replace(/[ё]/g, 'е').replace(/[.]/g, '').replace(/\s+/g, ' ');
  const labels: Record<string, string> = {
    'не был': 'Не был', 'не была': 'Не была', 'отсутствовал': 'Отсутствовал', 'отсутствовала': 'Отсутствовала',
    'н/б': 'Не был', 'нб': 'Не был', 'н.б': 'Не был', 'absent': 'Не был', 'not present': 'Не был',
    'уважительная причина': 'Уважительная причина', 'освобожден': 'Освобождён', 'освобождена': 'Освобождена', 'excused': 'Уважительная причина',
    'опоздал': 'Опоздал', 'опоздала': 'Опоздала', 'late': 'Опоздал',
    'зачет': 'Зачёт', 'зачтено': 'Зачтено', 'сдал': 'Сдал', 'сдала': 'Сдала', 'passed': 'Сдал',
    'незачет': 'Незачёт', 'не зачтено': 'Не зачтено', 'не сдал': 'Не сдал', 'не сдала': 'Не сдала', 'failed': 'Не сдал',
    'не выполнено': 'Не выполнено', 'выполнено': 'Выполнено', 'не проверено': 'Не проверено',
  };
  return labels[normalized] || '';
}

/** A short, cross-subject snapshot suitable for the campus peek. */
export function recentResultsAcrossSubjects(items: StudentJournalItem[], limit = 3) {
  const seen = new Set<string>();
  const results: Array<{ item: StudentJournalItem; assessment: StudentJournalItem['assessments'][number]; result: StudentJournalItem['results'][number] | undefined }> = [];
  const candidates = items.flatMap(item => recentStudentResults(item).map(row => ({ item, ...row })))
    .sort((a, b) => (b.assessment.assessed_on || '').localeCompare(a.assessment.assessed_on || ''));
  for (const row of candidates) {
    const subject = row.item.source.subject.trim().toLocaleLowerCase();
    if (!subject || seen.has(subject)) continue;
    seen.add(subject);
    results.push(row);
    if (results.length >= limit) break;
  }
  return results;
}

export function studentPeriodKey(item: StudentJournalItem): string {
  return `period:${JSON.stringify([item.source.academic_year, item.source.term_number ?? null, item.source.period_status])}`;
}

export function studentPeriodLabel(item: StudentJournalItem): string {
  const { term_number: term, academic_year: year, period_status: status } = item.source;
  const name = term ? `${['I', 'II', 'III'][term - 1] || term} триместр` : 'Период не указан';
  return [name, year, status === 'archived' ? 'архив' : ''].filter(Boolean).join(' · ');
}

export function studentResultDisplay(result: StudentJournalItem['results'][number] | undefined, maxScore?: number | null) {
  const score = journalValue(result?.numeric_score);
  const rawStatus = journalValue(result?.status);
  const status = rawStatus.toLowerCase() === 'graded' ? '' : rawStatus;
  const maximum = typeof maxScore === 'number' && Number.isFinite(maxScore) && maxScore > 0 ? journalValue(maxScore) : '';
  return {
    value: score ? `${score}${maximum ? `/${maximum}` : ''}` : status || 'Нет результата',
    kind: score ? maximum ? 'points' : 'grade' : status ? 'status' : 'empty',
    note: score ? status : '',
  };
}

export function studentSubjectIndicator(item: StudentJournalItem) {
  const latest = recentStudentResults(item)[0];
  return latest ? { ...studentResultDisplay(latest.result, latest.assessment.max_score), note: latest.assessment.title }
    : { value: 'Нет оценок', kind: 'empty', note: 'В выбранном журнале пока нет результатов' };
}

export function studentSummaryPreview(item: StudentJournalItem) {
  // Never infer a grading scale or derive an average from assessment points.
  return item.summaries.filter(summary => ['term_grade', 'final_grade'].includes(summary.summary_type)
    && journalValue(summary.numeric_value ?? summary.text_value)).slice(0, 2);
}
