'use client';

import { useCallback, useEffect, useState } from 'react';
import { BookOpen, LoaderCircle, RefreshCw } from 'lucide-react';

export type RoleJournalsApi = <T>(path: string, options?: RequestInit) => Promise<T>;

type StudentJournalItem = {
  source: {
    source_id: string | number;
    spreadsheet_title: string;
    grade: number;
    subject: string;
    sheet_title: string;
    academic_year: string;
    term_number?: number | null;
    period_status: string;
    last_synced_at?: string | null;
  };
  student: { journal_student_id: string | number; display_name: string; group_marker: string };
  assessments: Array<{
    id: string | number;
    title: string;
    assessed_on?: string | null;
    weight?: number | null;
    max_score?: number | null;
    group_marker: string;
  }>;
  results: Array<{ assessment_id: string | number; numeric_score: number | null; status?: string | null }>;
  summaries: Array<{ label: string; summary_type: string; numeric_value: number | null; text_value: string | null }>;
};

type TeacherJournalSource = {
  source_id: string | number;
  spreadsheet_title: string;
  grade: number;
  subject: string;
  sheet_title: string;
  academic_year: string;
  term_number?: number | null;
  period_status: string;
  group_marker: string;
  base_class_name?: string | null;
  subject_subgroup?: string | null;
  exam_track?: string | null;
};

type TeacherJournalDetail = {
  source_id: string | number;
  group_marker: string;
  source: {
    grade: number;
    subject: string;
    sheet_title: string;
    academic_year: string;
    term_number?: number | null;
    period_status: string;
  };
  students: Array<{
    journal_student_id: string | number;
    identity_id: string | number;
    display_name: string;
    class_name?: string | null;
    summaries: Array<{ label: string; summary_type: string; numeric_value: number | null; text_value: string | null }>;
  }>;
  assessments: Array<{
    id: string | number;
    title: string;
    date?: string | null;
    weight?: number | null;
    max_score?: number | null;
    results: Array<{ student_name: string; score: number | null; status?: string | null }>;
  }>;
};

function display(value: string | number | boolean | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback;
  const text = String(value).trim();
  if (!text) return fallback;
  // Spreadsheet formula errors are not meaningful journal results to students or teachers.
  if (text.startsWith('#')) return fallback;
  if (/^true$/i.test(text)) return 'Выполнено';
  if (/^false$/i.test(text)) return 'Не выполнено';
  return text;
}

function summaryText(summary: { summary_type: string; numeric_value: number | null; text_value: string | null }): string {
  const value = summary.numeric_value ?? summary.text_value;
  if (summary.summary_type === 'admission') {
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'true' || normalized === '1') return 'Допущен';
    if (normalized === 'false' || normalized === '0') return 'Не допущен';
    return '—';
  }
  return display(value);
}

function formatDate(value?: string | null): string {
  if (!value) return 'Дата не указана';
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(parsed);
}

function termLabel(source: { term_number?: number | null; period_status: string }): string {
  const term = source.term_number ? `${['I', 'II', 'III'][source.term_number - 1] || source.term_number} триместр` : '';
  const period = source.period_status === 'archived' ? 'архив' : source.period_status === 'unassigned' ? 'период не указан' : '';
  return [term, period].filter(Boolean).join(' · ') || 'текущий журнал';
}

function valueText(score: number | null, status?: string | null): string {
  return score === null || score === undefined ? display(status) : display(score);
}

export function StudentRoleJournals({ api }: { api: RoleJournalsApi }) {
  const [items, setItems] = useState<StudentJournalItem[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const result = await api<{ items: StudentJournalItem[] }>('/api/student/journals');
        if (active) {
          setError('');
          setItems(result.items || []);
        }
      } catch {
        if (active) {
          setError('Не удалось загрузить результаты школьного журнала.');
          setItems([]);
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [api]);

  return (
    <section className="profile-section" aria-labelledby="student-journals-title">
      <div className="subsection-title" id="student-journals-title"><BookOpen size={16} /> Школьные журналы</div>
      {error ? <p className="sync-summary sync-summary--empty" role="alert">{error}</p> : null}
      {items === null ? <div className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем результаты</div> : items.length ? items.map((item) => {
        const resultByAssessment = new Map(item.results.map((result) => [String(result.assessment_id), result]));
        return (
          <article className="profile-section" key={`${item.source.source_id}:${item.student.journal_student_id}`}>
            <div className="subsection-title">
              <span>{item.source.grade} класс · {item.source.subject}</span>
              <small>{termLabel(item.source)}</small>
            </div>
            {item.assessments.length ? (
              <div className="assessment-list">
                {item.assessments.map((assessment) => {
                  const result = resultByAssessment.get(String(assessment.id));
                  return (
                    <div className="assessment-card" key={assessment.id}>
                      <div><strong>{assessment.title}</strong><small>{formatDate(assessment.assessed_on)} · вес {display(assessment.weight)} · максимум {display(assessment.max_score)}</small></div>
                      <span className="count-badge">{valueText(result?.numeric_score ?? null, result?.status)}</span>
                    </div>
                  );
                })}
              </div>
            ) : <p className="sync-summary sync-summary--empty">Работы пока не опубликованы.</p>}
            {item.summaries.length ? <div className="assessment-list">{item.summaries.map((summary) => <div className="context-link" key={`${summary.summary_type}:${summary.label}`}><span>{summary.label}</span><strong>{summaryText(summary)}</strong></div>)}</div> : null}
          </article>
        );
      }) : !error ? <p className="sync-summary sync-summary--empty">Подтверждённых записей в школьных журналах пока нет.</p> : null}
    </section>
  );
}

export function TeacherRoleJournals({ api }: { api: RoleJournalsApi }) {
  const [sources, setSources] = useState<TeacherJournalSource[] | null>(null);
  const [selectedKey, setSelectedKey] = useState('');
  const [detail, setDetail] = useState<TeacherJournalDetail | null>(null);
  const [error, setError] = useState('');

  const loadSources = useCallback(async () => {
    try {
      const result = await api<{ items: TeacherJournalSource[] }>('/api/teacher/journals');
      setError('');
      setSources(result.items || []);
    } catch {
      setSources([]);
      setError('Не удалось загрузить доступные журналы.');
    }
  }, [api]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const result = await api<{ items: TeacherJournalSource[] }>('/api/teacher/journals');
        if (active) {
          setError('');
          setSources(result.items || []);
        }
      } catch {
        if (active) {
          setSources([]);
          setError('Не удалось загрузить доступные журналы.');
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [api]);

  const selected = sources?.find((item) => `${item.source_id}:${item.group_marker}` === selectedKey) || sources?.[0] || null;
  useEffect(() => {
    const sourceId = String(selected?.source_id ?? '');
    const groupMarker = selected?.group_marker ?? '';
    if (!sourceId || !groupMarker) return;
    let cancelled = false;
    async function loadDetail() {
      try {
        const result = await api<TeacherJournalDetail>(`/api/teacher/journals/${encodeURIComponent(sourceId)}/${encodeURIComponent(groupMarker)}`);
        if (!cancelled) {
          setDetail(result);
          setError('');
        }
      } catch {
        if (!cancelled) setError('Не удалось открыть этот журнал.');
      }
    }
    void loadDetail();
    return () => { cancelled = true; };
  }, [api, selected]);

  const detailMatchesSelection = Boolean(selected && detail
    && String(detail.source_id) === String(selected.source_id)
    && detail.group_marker === selected.group_marker);
  const detailLoading = Boolean(selected && !detailMatchesSelection && !error);

  return (
    <section className="profile-section" aria-labelledby="teacher-journals-title">
      <div className="subsection-title" id="teacher-journals-title"><BookOpen size={16} /> Журналы по назначениям</div>
      {error ? <p className="sync-summary sync-summary--empty" role="alert">{error}</p> : null}
      {sources === null ? <div className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем доступные журналы</div> : !sources.length ? (
        !error ? <p className="sync-summary sync-summary--empty">Подтверждённых назначений на группы журнала пока нет.</p> : null
      ) : (
        <>
          <div className="journal-detail-nav"><label>Источник и группа
            <select value={selected ? `${selected.source_id}:${selected.group_marker}` : ''} onChange={(event) => { setError(''); setSelectedKey(event.target.value); }}>
              {sources.map((source) => {
                const key = `${source.source_id}:${source.group_marker}`;
                return <option key={key} value={key}>{source.grade} класс · {source.subject} · группа {source.group_marker} · {termLabel(source)}</option>;
              })}
            </select>
          </label></div>
          {selected ? <p className="sync-summary">{detail?.source?.grade || selected.grade} класс · {detail?.source?.subject || selected.subject} · {detail?.source?.academic_year || selected.academic_year} · {termLabel(detail?.source || selected)}{selected.base_class_name ? ` · ${selected.base_class_name}` : ''}{selected.subject_subgroup ? ` · подгруппа ${selected.subject_subgroup}` : ''}</p> : null}
          {detailLoading ? <div className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем журнал</div> : detailMatchesSelection && detail && (detail.assessments.length > 0 || detail.students.some((student) => student.summaries.length > 0)) ? (
            <>
              {detail.assessments.length ? <div className="assessment-list">
                {detail.assessments.map((assessment) => (
                  <article className="profile-section" key={assessment.id}>
                    <div className="assessment-card">
                      <div><strong>{assessment.title}</strong><small>{formatDate(assessment.date)} · вес {display(assessment.weight)} · максимум {display(assessment.max_score)}</small></div>
                      <span className="count-badge">{assessment.results.length} оценок</span>
                    </div>
                    {assessment.results.length ? <div className="assessment-list">{assessment.results.map((result, index) => <div className="context-link" key={`${result.student_name}:${index}`}><span>{result.student_name}</span><strong>{valueText(result.score, result.status)}</strong></div>)}</div> : <p className="sync-summary sync-summary--empty">Оценок по этой работе пока нет.</p>}
                  </article>
                ))}
              </div> : null}
              {detail.students.some((student) => student.summaries.length) ? <>
                <div className="subsection-title">Сводные показатели учеников</div>
                <div className="assessment-list">{detail.students.flatMap((student) => student.summaries.map((summary) => <div className="context-link" key={`${student.journal_student_id}:${summary.summary_type}:${summary.label}`}><span>{student.display_name} · {summary.label}</span><strong>{summaryText(summary)}</strong></div>))}</div>
              </> : null}
            </>
          ) : !detailLoading && detailMatchesSelection ? <p className="sync-summary sync-summary--empty">В выбранной группе пока нет работ с доступными сводными показателями.</p> : null}
        </>
      )}
      <button className="panel-refresh" onClick={() => void loadSources()}><RefreshCw size={15} /> Обновить список журналов</button>
    </section>
  );
}
