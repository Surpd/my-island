'use client';

import { useMemo, useState } from 'react';
import './admin-modern-journal.css';

export type ModernJournalResult = {
  numeric_score: number | null;
  status: string | boolean | null;
};

export type ModernJournalSummary = {
  label: string;
  summary_type?: string | null;
  numeric_value: number | null;
  text_value: string | null;
};

export type ModernJournalStudent = {
  id: string | number;
  display_name: string;
  group_marker: string;
  match_status: string;
  summaries: ModernJournalSummary[];
  results: Record<string, ModernJournalResult | null>;
};

export type ModernJournalDetail = {
  source: {
    id: string | number;
    grade: number;
    subject: string;
    sheet_title: string;
    term_number?: number | null;
    academic_year?: string;
    last_synced_at?: string | null;
  };
  assessments: Array<{
    id: string | number;
    title: string;
    category?: string | null;
    assessed_on?: string | null;
    weight?: number | null;
    max_score?: number | null;
    group_marker: string;
  }>;
  students: ModernJournalStudent[];
};

export type AdminModernJournalProps = {
  detail: ModernJournalDetail;
  className?: string;
  initialMode?: 'table' | 'works';
  onMatchStudent?: (student: ModernJournalStudent, context: { sourceId: string | number; groupMarker: string }) => void;
};

const SUMMARY_COLUMNS = [
  ['learning_grade', 'Учебные 40%'],
  ['required_grade', 'Обязательные 60%'],
  ['term_grade', 'Итог'],
] as const;

const CALCULATION_COLUMNS = [
  ['calculated_grade', 'Расчёт'],
  ['rounded_grade', 'Округление'],
  ['teacher_adjustment', 'Корректировка'],
] as const;
const russianCounts = new Intl.PluralRules('ru-RU');
const summaryNumber = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

function countText(count: number, one: string, few: string, many: string): string {
  const form = russianCounts.select(count);
  return `${count} ${form === 'one' ? one : form === 'few' ? few : many}`;
}

function present(value: unknown): value is string | number | boolean {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value === 'boolean') return true;
  return typeof value === 'string' && value.trim() !== '' && !value.trim().startsWith('#');
}

function valueText(value: unknown): string {
  if (!present(value)) return '—';
  if (typeof value === 'number') return String(value).replace('.', ',');
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  return value.trim();
}

function summaryValue(summary?: ModernJournalSummary): string | number | boolean | null {
  if (!summary) return null;
  return present(summary.numeric_value) ? summary.numeric_value : present(summary.text_value) ? summary.text_value : null;
}

function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string' || !value.trim()) return null;
  const parsed = Number(value.trim().replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : null;
}

function valueTone(value: unknown): string {
  const numeric = numericValue(value);
  if (numeric === null || numeric < 0 || numeric > 5) return '';
  if (numeric < 2) return 'is-critical';
  if (numeric < 3) return 'is-low';
  if (numeric < 4) return 'is-mid';
  if (numeric < 4.5) return 'is-good';
  return 'is-high';
}

function hasAssessmentDebt(value: unknown): boolean {
  const numeric = numericValue(value);
  if (numeric !== null) return numeric > 0;
  return value === true || (typeof value === 'string' && /^есть(?:\s+долг[а-я]*)?$|^да$/iu.test(value.trim()));
}

function resultText(result: ModernJournalResult | null | undefined): string {
  if (!result) return '—';
  if (present(result.numeric_score)) return valueText(result.numeric_score);
  const status = present(result.status) ? valueText(result.status) : '';
  return status.toLocaleLowerCase('ru-RU') === 'graded' ? '—' : status || '—';
}

function dateText(value?: string | null): string {
  if (!value) return 'Без даты';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '');
}

function categoryMeta(category?: string | null) {
  const code = String(category || '').trim().toLocaleUpperCase('ru-RU');
  if (code === 'О') return { code, label: 'Обязательная', tone: 'is-required' };
  if (code === 'П') return { code, label: 'Пересдача', tone: 'is-retake' };
  if (code === 'У') return { code, label: 'Учебная', tone: 'is-learning' };
  return { code, label: code || 'Без категории', tone: '' };
}

function studentSummaries(student: ModernJournalStudent) {
  return new Map(student.summaries.map(summary => [summary.summary_type || '', summary]));
}

export function isModernJournalDetail(detail: ModernJournalDetail): boolean {
  return detail.source.grade >= 5 && detail.source.grade <= 9 && detail.source.term_number === 2;
}

export function isArchivedFirstTermJournal(detail: ModernJournalDetail): boolean {
  return detail.source.grade >= 5 && detail.source.grade <= 9 && detail.source.term_number === 1;
}

export function AdminModernJournal({ detail, className = '', initialMode = 'table', onMatchStudent }: AdminModernJournalProps) {
  const [mode, setMode] = useState<'table' | 'works'>(initialMode);
  const [query, setQuery] = useState('');
  const [showCalculation, setShowCalculation] = useState(false);
  const [selectedWorkId, setSelectedWorkId] = useState<string | number | null>(null);
  const students = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('ru-RU').replaceAll('ё', 'е');
    if (!normalized) return detail.students;
    return detail.students.filter(student => student.display_name.toLocaleLowerCase('ru-RU').replaceAll('ё', 'е').includes(normalized));
  }, [detail.students, query]);

  if (!isModernJournalDetail(detail)) return null;

  const sourceContext = (student: ModernJournalStudent) => ({ sourceId: detail.source.id, groupMarker: student.group_marker });
  const workList = [...detail.assessments].sort((a, b) => (b.assessed_on || '').localeCompare(a.assessed_on || ''));
  const selectedWork = workList.find(work => work.id === selectedWorkId) || workList[0];
  const summaryColumns = showCalculation
    ? [...CALCULATION_COLUMNS, ...SUMMARY_COLUMNS] : SUMMARY_COLUMNS;
  const assessmentHeaderRows = detail.assessments.length ? 2 : 1;
  const hasCalculation = detail.students.some(student => student.summaries.some(summary => CALCULATION_COLUMNS.some(([kind]) => kind === summary.summary_type)));
  return <section className={`modern-journal ${className}`.trim()} aria-label={`${detail.source.subject}, журнал II триместра`}>
    <div className="modern-journal__toolbar">
      <div className="modern-journal__modes" role="tablist" aria-label="Режим журнала">
        <button type="button" role="tab" aria-selected={mode === 'table'} onClick={() => setMode('table')}>Таблица оценок</button>
        <button type="button" role="tab" aria-selected={mode === 'works'} onClick={() => setMode('works')}>По работам</button>
      </div>
      <label className="modern-journal__search">
        <span className="sr-only">Найти ученика</span>
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Найти ученика" type="search" />
      </label>
    </div>

    <div className="modern-journal__count modern-journal__count-row">
      <span>{query.trim() ? `${students.length} из ${detail.students.length} учеников` : countText(detail.students.length, 'ученик', 'ученика', 'учеников')}
        {' · '}{countText(detail.assessments.length, 'работа', 'работы', 'работ')}{' · Шкала 0–5'}</span>
      {mode === 'table' && hasCalculation ? <button type="button" className="admin-link" aria-pressed={showCalculation}
        onClick={() => setShowCalculation(show => !show)}>{showCalculation ? 'Скрыть расчёт' : 'Показать расчёт'}</button> : null}
    </div>

    {mode === 'table' ? (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The scroll region must support keyboard scrolling.
    <section className="modern-journal__table-wrap" tabIndex={0} aria-label="Таблица оценок с прокруткой">
      <table className="modern-journal__table">
        <thead><tr className="modern-journal__work-titles">
          <th className="modern-journal__student-column" scope="col" rowSpan={assessmentHeaderRows}>Ученик</th>
          {detail.assessments.map(assessment => {
            const category = categoryMeta(assessment.category);
            return <th className={`modern-journal__work-column ${category.tone}`} scope="col"
              aria-describedby={`journal-work-meta-${detail.source.id}-${assessment.id}`} key={assessment.id}>
              <strong title={assessment.title}>{assessment.title}</strong>
            </th>;
          })}
          {summaryColumns.map(([kind, label]) => <th className="modern-journal__summary-head" scope="col"
            rowSpan={assessmentHeaderRows} data-summary={kind} key={kind}>{label}</th>)}
        </tr>
        {assessmentHeaderRows === 2 ? <tr className="modern-journal__work-metadata">
          {detail.assessments.map(assessment => {
            const category = categoryMeta(assessment.category);
            return <td className={category.tone} id={`journal-work-meta-${detail.source.id}-${assessment.id}`} key={assessment.id}>
              <div className="modern-journal__work-meta-line">
                <span>{dateText(assessment.assessed_on)}</span>
                <span title={category.code === 'П' ? 'Вес исходной контрольной' : undefined}>
                  {category.code === 'П' ? 'Вес О' : `Вес ${valueText(assessment.weight)}`}
                </span>
              </div>
              <small title={category.label} aria-label={category.label}>{category.code || '—'}</small>
            </td>;
          })}
        </tr> : null}
        </thead>
        <tbody>{students.map(student => {
          const summaries = studentSummaries(student);
          return <tr key={student.id}>
            <th className="modern-journal__student-column" scope="row">
              <span>{student.display_name}</span>
              {student.match_status !== 'linked' && onMatchStudent ? <button type="button" onClick={() => onMatchStudent(student, sourceContext(student))}>Сопоставить</button> : null}
            </th>
            {detail.assessments.map(assessment => {
              const category = categoryMeta(assessment.category);
              return <td className={category.tone} key={assessment.id}>{resultText(student.results[String(assessment.id)])}</td>;
            })}
            {summaryColumns.map(([kind]) => {
              const value = summaryValue(summaries.get(kind));
              const debt = kind === 'term_grade' && hasAssessmentDebt(summaryValue(summaries.get('assessment_debt')));
              const tone = kind === 'teacher_adjustment' ? '' : valueTone(value);
              return <td className={`modern-journal__summary ${tone}${debt ? ' has-debt' : ''}`} data-summary={kind} key={kind}>
                {typeof value === 'number' ? summaryNumber.format(value) : value != null || !debt ? valueText(value) : null}
                {debt ? <small>Есть долг</small> : null}
              </td>;
            })}
          </tr>;
        })}</tbody>
      </table>
    </section>) : selectedWork ? <div className="modern-journal__work-layout">
      <nav className="modern-journal__work-list" aria-label="Работы">
        {workList.map(work => <button type="button" key={work.id} aria-pressed={selectedWork.id === work.id}
          className={categoryMeta(work.category).tone} onClick={() => setSelectedWorkId(work.id)}>
          <strong>{work.title}</strong>
          <small>{dateText(work.assessed_on)} · {categoryMeta(work.category).label}</small>
        </button>)}
      </nav>
      <article className={`modern-journal__work ${categoryMeta(selectedWork.category).tone}`}>
        <header><div><strong>{selectedWork.title}</strong><span>{dateText(selectedWork.assessed_on)}
          {selectedWork.weight != null && categoryMeta(selectedWork.category).code !== 'П' ? ` · вес ${valueText(selectedWork.weight)}` : ''}</span></div>
          <small>{categoryMeta(selectedWork.category).label}</small></header>
        <ul>{students.filter(student => selectedWork.group_marker === 'all' || student.group_marker === selectedWork.group_marker)
          .map(student => <li key={student.id}><span>{student.display_name}</span><strong>{resultText(student.results[String(selectedWork.id)])}</strong></li>)}</ul>
      </article>
    </div> : <p className="modern-journal__empty">В этом журнале пока нет работ.</p>}
    {students.length === 0 ? <p className="modern-journal__empty">{query.trim() ? 'По запросу ученики не найдены.' : 'В журнале пока нет учеников.'}</p> : null}
    <footer className="modern-journal__legend">
      <span><i className="is-learning" /> У — учебная</span><span><i className="is-required" /> О — обязательная</span>
      <span><i className="is-retake" /> П — пересдача с весом контрольной</span><span>0 — оценка · — нет результата</span>
    </footer>
  </section>;
}

export function AdminArchivedJournalSummary({ detail, className = '', onMatchStudent }: Omit<AdminModernJournalProps, 'initialMode'>) {
  const [query, setQuery] = useState('');
  const students = useMemo(() => detail.students.filter(student => student.display_name.toLocaleLowerCase('ru-RU').replaceAll('ё', 'е').includes(query.trim().toLocaleLowerCase('ru-RU').replaceAll('ё', 'е'))), [detail.students, query]);
  if (!isArchivedFirstTermJournal(detail)) return null;
  return <section className={`modern-journal modern-journal--archive ${className}`.trim()} aria-label={`${detail.source.subject}, архив I триместра`}>
    <div className="modern-journal__count-row">
      <h3>Итоги I триместра</h3>
      <label className="modern-journal__search modern-journal__search--archive"><span className="sr-only">Найти ученика</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Найти ученика" type="search" /></label>
    </div>
    <div className="modern-journal__table-wrap"><table className="modern-journal__table modern-journal__archive-table"><thead><tr><th className="modern-journal__student-column" scope="col">Ученик</th><th scope="col">Итоговая оценка</th></tr></thead><tbody>
      {students.map(student => {
        const summaries = studentSummaries(student);
        const value = summaryValue(summaries.get('term_grade')) ?? summaryValue(summaries.get('final_grade'));
        return <tr key={student.id}>
          <th className="modern-journal__student-column" scope="row"><span>{student.display_name}</span>
            {student.match_status !== 'linked' && onMatchStudent ? <button type="button" onClick={() => onMatchStudent(student, { sourceId: detail.source.id, groupMarker: student.group_marker })}>Сопоставить</button> : null}
          </th>
          <td className={`modern-journal__summary ${valueTone(value)}`}>{valueText(value)}</td>
        </tr>;
      })}
    </tbody></table></div>
    {students.length === 0 ? <p className="modern-journal__empty">По запросу ученики не найдены.</p> : null}
  </section>;
}
