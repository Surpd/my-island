'use client';

import { useEffect, useMemo, useState } from 'react';
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

type JournalView = 'current' | 'archived' | 'all';

function periodMatches(status: string | undefined, view: JournalView): boolean {
  if (view === 'all') return true;
  if (view === 'archived') return status === 'archived';
  return status === 'current';
}

function periodViewControl(view: JournalView, onChange: (view: JournalView) => void) {
  return <label className="journal-period-filter">Период
    <select value={view} onChange={(event) => onChange(event.target.value as JournalView)}>
      <option value="current">Текущие</option>
      <option value="archived">Архив</option>
      <option value="all">Все периоды</option>
    </select>
  </label>;
}

function periodEmptyText(view: JournalView, archivedAvailable: boolean, unassignedAvailable: boolean, mappingState?: string | null): string {
  if (mappingState === 'missing_mapping') return 'В школьных журналах пока нет сопоставленной записи этого ученика. Проверьте сопоставление ученика с журналом.';
  if (mappingState === 'unlinked_mapping') return 'Запись ученика найдена, но её сопоставление ещё не подтверждено. После подтверждения здесь появятся результаты.';
  if (mappingState === 'mapped_empty') return 'Сопоставление подтверждено, но опубликованных оценок пока нет.';
  if (view === 'archived') return 'В архивных журналах подтверждённых записей пока нет.';
  if (view === 'current' && unassignedAvailable && !archivedAvailable) return 'Текущих записей пока нет. Часть журналов ещё не привязана к периоду; они доступны в выборе «Все периоды».';
  if (view === 'current' && unassignedAvailable) return 'Текущих записей пока нет. Журналы без назначенного периода доступны в выборе «Все периоды». Архивные результаты тоже можно открыть в выборе периода.';
  if (view === 'current' && archivedAvailable) return 'В текущем периоде записей пока нет. Архивные результаты доступны в выборе периода.';
  if (view === 'current') return 'Подтверждённых записей в текущем школьном журнале пока нет.';
  return 'Подтверждённых записей в школьных журналах пока нет.';
}

function journalError(error: unknown, fallback: string): string {
  const status = typeof error === 'object' && error !== null && 'status' in error
    ? Number((error as { status?: unknown }).status)
    : undefined;
  const message = error instanceof Error ? error.message : '';
  if (status === 401) return 'Сессия устарела. Перезапустите приложение и попробуйте снова.';
  if (status === 403) return 'У этой учётной записи нет доступа к выбранным школьным журналам.';
  if (message === 'Сервис временно недоступен') return 'Сервис временно недоступен. Проверьте соединение и попробуйте снова.';
  if (typeof status === 'number' && status >= 500) return 'Сервис журналов временно недоступен. Попробуйте позже.';
  return fallback;
}

export function StudentRoleJournals({ api }: { api: RoleJournalsApi }) {
  const [items, setItems] = useState<StudentJournalItem[] | null>(null);
  const [mappingState, setMappingState] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [view, setView] = useState<JournalView>('current');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    async function load() {
      setItems(null);
      setError('');
      try {
        const result = await api<{ items?: StudentJournalItem[]; mapping_state?: string }>('/api/student/journals');
        if (active) {
          setError('');
          setItems(Array.isArray(result?.items) ? result.items : []);
          setMappingState(result?.mapping_state || null);
        }
      } catch (cause) {
        if (active) {
          setError(journalError(cause, 'Не удалось загрузить результаты школьного журнала.'));
          setItems(null);
          setMappingState(null);
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [api, reload]);

  const visibleItems = useMemo(() => (items || []).filter((item) => periodMatches(item.source?.period_status, view)), [items, view]);
  const archivedAvailable = Boolean(items?.some((item) => item.source?.period_status === 'archived'));
  const unassignedAvailable = Boolean(items?.some((item) => item.source?.period_status === 'unassigned'));

  return (
    <section className="profile-section" aria-labelledby="student-journals-title">
      <div className="subsection-title" id="student-journals-title"><BookOpen size={16} /> Школьные журналы</div>
      {error ? <p className="sync-summary sync-summary--empty" role="alert">{error} <button className="panel-refresh" onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} /> Повторить</button></p> : null}
      {items === null && !error ? <div className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем результаты</div> : null}
      {items !== null && !error ? <>
      <div className="journal-detail-nav">{periodViewControl(view, setView)}</div>
      {!visibleItems.length ? <p className="sync-summary sync-summary--empty">{periodEmptyText(view, archivedAvailable, unassignedAvailable, mappingState)}</p> : null}
      {mappingState === 'mapped_empty' && visibleItems.length > 0 ? <p className="sync-summary sync-summary--empty">Сопоставление подтверждено, опубликованных оценок пока нет.</p> : null}
      {visibleItems.length ? visibleItems.map((item) => {
        const resultByAssessment = new Map(item.results.map((result) => [String(result.assessment_id), result]));
        const syncedAt = item.source.last_synced_at ? new Date(item.source.last_synced_at.replace(' ', 'T')) : null;
        const syncedLabel = syncedAt && !Number.isNaN(syncedAt.getTime()) ? new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(syncedAt) : item.source.last_synced_at;
        return (
          <article className="profile-section" key={`${item.source.source_id}:${item.student.journal_student_id}`}>
            <div className="subsection-title">
              <span>{item.source.grade} класс · {item.source.subject}</span>
              <small>{termLabel(item.source)}{syncedLabel ? ` · обновлено ${syncedLabel}` : ''}</small>
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
      }) : null}
      </> : null}
    </section>
  );
}

export function TeacherRoleJournals({ api }: { api: RoleJournalsApi }) {
  const [sources, setSources] = useState<TeacherJournalSource[] | null>(null);
  const [selectedKey, setSelectedKey] = useState('');
  const [detail, setDetail] = useState<TeacherJournalDetail | null>(null);
  const [sourceError, setSourceError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [view, setView] = useState<JournalView>('current');
  const [sourceReload, setSourceReload] = useState(0);
  const [detailReload, setDetailReload] = useState(0);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        setSources(null);
        setSourceError('');
        const result = await api<{ items?: TeacherJournalSource[] }>('/api/teacher/journals');
        if (active) {
          setSourceError('');
          setSources(Array.isArray(result?.items) ? result.items : []);
        }
      } catch (cause) {
        if (active) {
          setSources(null);
          setSourceError(journalError(cause, 'Не удалось загрузить доступные журналы.'));
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [api, sourceReload]);

  const visibleSources = useMemo(() => (sources || []).filter((source) => periodMatches(source.period_status, view)), [sources, view]);
  const selected = visibleSources.find((item) => `${item.source_id}:${item.group_marker}` === selectedKey) || visibleSources[0] || null;
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
          setDetailError('');
        }
      } catch (cause) {
        if (!cancelled) { setDetail(null); setDetailError(journalError(cause, 'Не удалось открыть этот журнал.')); }
      }
    }
    void loadDetail();
    return () => { cancelled = true; };
  }, [api, selected?.source_id, selected?.group_marker, detailReload]);

  const detailMatchesSelection = Boolean(selected && detail
    && String(detail.source_id) === String(selected.source_id)
    && detail.group_marker === selected.group_marker);
  const detailLoading = Boolean(selected && !detailMatchesSelection && !detailError);
  const archivedAvailable = Boolean(sources?.some((item) => item.period_status === 'archived'));

  return (
    <section className="profile-section" aria-labelledby="teacher-journals-title">
      <div className="subsection-title" id="teacher-journals-title"><BookOpen size={16} /> Журналы по назначениям</div>
      {sourceError ? <p className="sync-summary sync-summary--empty" role="alert">{sourceError} <button className="panel-refresh" onClick={() => setSourceReload((value) => value + 1)}><RefreshCw size={15} /> Повторить</button></p> : null}
      {sources === null && !sourceError ? <div className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем доступные журналы</div> : null}
      {sources !== null && !sourceError ? <>
          <div className="journal-detail-nav">{periodViewControl(view, (next) => { setDetailError(''); setView(next); })}</div>
          {!visibleSources.length ? <p className="sync-summary sync-summary--empty">{sources.length === 0 ? 'Подтверждённых назначений на группы журнала пока нет.' : view === 'archived' ? 'Для архивных журналов назначений нет.' : view === 'current' && sources.some((item) => item.period_status === 'unassigned') ? 'В текущем периоде назначений нет. Журналы без назначенного периода доступны в выборе «Все периоды».' : view === 'current' && archivedAvailable ? 'В текущем периоде назначений нет. Архивные журналы доступны в выборе периода.' : 'Подтверждённых назначений на группы журнала пока нет.'}</p> : <>
          <div className="journal-detail-nav"><label>Источник и группа
            <select value={selected ? `${selected.source_id}:${selected.group_marker}` : ''} onChange={(event) => { setDetailError(''); setSelectedKey(event.target.value); }}>
              {visibleSources.map((source) => {
                const key = `${source.source_id}:${source.group_marker}`;
                return <option key={key} value={key}>{source.grade} класс · {source.subject} · группа {source.group_marker} · {termLabel(source)}</option>;
              })}
            </select>
          </label></div>
          {selected ? <p className="sync-summary">{detail?.source?.grade || selected.grade} класс · {detail?.source?.subject || selected.subject} · {detail?.source?.academic_year || selected.academic_year} · {termLabel(detail?.source || selected)}{selected.base_class_name ? ` · ${selected.base_class_name}` : ''}{selected.subject_subgroup ? ` · подгруппа ${selected.subject_subgroup}` : ''}</p> : null}
          {detailError ? <p className="sync-summary sync-summary--empty" role="alert">{detailError} <button className="panel-refresh" onClick={() => { setDetailError(''); setDetailReload((value) => value + 1); }}><RefreshCw size={15} /> Повторить</button></p> : null}
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
          ) : !detailLoading && detailMatchesSelection && !detailError ? <p className="sync-summary sync-summary--empty">В выбранной группе пока нет работ с доступными сводными показателями.</p> : null}
          </>}
      </> : null}
      <button className="panel-refresh" onClick={() => setSourceReload((value) => value + 1)}><RefreshCw size={15} /> Обновить список журналов</button>
    </section>
  );
}
