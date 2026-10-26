'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, Info, LoaderCircle, RefreshCw } from 'lucide-react';
import { journalKey, journalValue, studentJournalRows, studentPeriodKey, studentPeriodLabel, studentResultDisplay, studentSubjectIndicator, type StudentJournalItem } from './student-journal-view';
import './student-journals.css';

export type RoleJournalsApi = <T>(path: string, options?: RequestInit) => Promise<T>;

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

function PeriodViewControl({ view, onChange }: { view: JournalView; onChange: (view: JournalView) => void }) {
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
  const [view, setView] = useState('current');
  const [reload, setReload] = useState(0);
  const [selectedKey, setSelectedKey] = useState('');
  const sectionRef = useRef<HTMLElement>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const listScrollTop = useRef(0);

  useEffect(() => {
    let active = true;
    async function load() {
      setItems(null);
      setError('');
      try {
        const result = await api<{ items?: StudentJournalItem[]; mapping_state?: string }>('/api/student/journals');
        if (!active) return;
        setItems(Array.isArray(result?.items) ? result.items : []);
        setMappingState(result?.mapping_state || null);
      } catch (cause) {
        if (!active) return;
        setError(journalError(cause, 'Не удалось загрузить результаты школьного журнала.'));
        setItems(null);
        setMappingState(null);
      }
    }
    void load();
    return () => { active = false; };
  }, [api, reload]);

  const visibleItems = useMemo(() => (items || []).filter(item => view.startsWith('period:') ? studentPeriodKey(item) === view : periodMatches(item.source?.period_status, view as JournalView)), [items, view]);
  const periods = new Map((items || []).map(item => [studentPeriodKey(item), studentPeriodLabel(item)]));
  const currentPeriods = new Map((items || []).filter(item => item.source.period_status === 'current').map(item => [studentPeriodKey(item), studentPeriodLabel(item)]));
  const periodGroups = new Map<string, StudentJournalItem[]>();
  for (const item of visibleItems) { const key = studentPeriodKey(item); periodGroups.set(key, [...(periodGroups.get(key) || []), item]); }
  const currentLabel = currentPeriods.size === 1 ? [...currentPeriods.values()][0] : 'Текущие периоды';
  const disambiguation = (item: StudentJournalItem) => {
    const peers = (periodGroups.get(studentPeriodKey(item)) || []).filter(peer => peer.source.subject === item.source.subject);
    if (peers.length < 2) return '';
    const sameGroup = peers.filter(peer => peer.student.group_marker === item.student.group_marker).length > 1;
    return [item.student.group_marker !== 'all' ? `Группа ${item.student.group_marker}` : '', sameGroup ? item.source.sheet_title || item.source.spreadsheet_title : ''].filter(Boolean).join(' · ');
  };
  const selected = visibleItems.find(item => journalKey(item) === selectedKey);
  const archivedAvailable = Boolean(items?.some(item => item.source?.period_status === 'archived'));
  const unassignedAvailable = Boolean(items?.some(item => item.source?.period_status === 'unassigned'));
  const scrollContainer = () => sectionRef.current?.closest<HTMLElement>('.sheet-full');
  const openJournal = (item: StudentJournalItem) => {
    listScrollTop.current = scrollContainer()?.scrollTop || 0;
    setSelectedKey(journalKey(item));
    requestAnimationFrame(() => {
      scrollContainer()?.scrollTo({ top: 0 });
      detailHeadingRef.current?.focus({ preventScroll: true });
    });
  };
  const backToSubjects = () => {
    const previousKey = selectedKey;
    setSelectedKey('');
    requestAnimationFrame(() => {
      scrollContainer()?.scrollTo({ top: listScrollTop.current });
      Array.from(sectionRef.current?.querySelectorAll<HTMLButtonElement>('.student-subject-row') || [])
        .find(button => button.dataset.journalKey === previousKey)?.focus({ preventScroll: true });
    });
  };
  const changePeriod = (next: string) => {
    setView(next); setSelectedKey(''); listScrollTop.current = 0;
    scrollContainer()?.scrollTo({ top: 0 });
  };

  const emptyView = ['current', 'archived', 'all'].includes(view) ? view as JournalView : 'all';
  const detailSummaries = selected?.summaries.filter(summary => journalValue(summary.numeric_value ?? summary.text_value)) || [];
  const highlights = detailSummaries.filter(summary => ['average_grade', 'term_grade', 'final_grade'].includes(summary.summary_type));
  const otherSummaries = detailSummaries.filter(summary => !highlights.includes(summary));
  return <section ref={sectionRef} className="student-journals" aria-labelledby="student-journals-title">
    {!selected ? <header className="student-journals-heading">
      <h2 id="student-journals-title">Оценки</h2>
      <select aria-label="Период" value={view} onChange={event => changePeriod(event.target.value)}>
        <option value="current">{currentLabel}</option><option value="archived">Архив</option><option value="all">Все периоды</option>
        {[...periods].filter(([key]) => !(currentPeriods.size === 1 && currentPeriods.has(key))).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
    </header> : null}
    {error ? <p className="sync-summary sync-summary--empty" role="alert">{error} <button className="panel-refresh" onClick={() => { setSelectedKey(''); setReload(value => value + 1); }}><RefreshCw size={15} /> Повторить</button></p> : null}
    {items === null && !error ? <output className="loading-state"><LoaderCircle size={17} className="spin" /> Загружаем результаты</output> : null}
    {items !== null && !error ? <>
      {!visibleItems.length ? <p className="sync-summary sync-summary--empty">{periodEmptyText(emptyView, archivedAvailable, unassignedAvailable, mappingState)}</p> : null}
      {selected ? <div className="student-journal-detail">
        <header className="student-journal-header">
          <button type="button" className="student-journal-back" aria-label="К предметам" title="К предметам" onClick={backToSubjects}><ChevronLeft size={23} aria-hidden="true" /></button>
          <div><h2 id="student-journals-title" ref={detailHeadingRef} tabIndex={-1}>{selected.source.subject}</h2><p>{studentPeriodLabel(selected)}{disambiguation(selected) ? ` · ${disambiguation(selected)}` : ''}</p></div>
        </header>
        {highlights.length ? <dl className="student-journal-summaries">{highlights.map(summary => <div key={`${summary.summary_type}:${summary.label}`}><dt>{summary.label}</dt><dd>{typeof summary.numeric_value === 'number' ? journalValue(summary.numeric_value) : summaryText(summary)}</dd></div>)}</dl> : null}
        <ol className="student-journal-assessments" aria-label="Работы: сначала последние">{studentJournalRows(selected, true).map(({ assessment, result }) => {
          const presentation = studentResultDisplay(result, assessment.max_score);
          const details = [assessment.weight !== null && assessment.weight !== undefined && Number.isFinite(assessment.weight) && assessment.weight >= 0 ? `Вес ${journalValue(assessment.weight)}` : '',
            assessment.max_score !== null && assessment.max_score !== undefined && Number.isFinite(assessment.max_score) && assessment.max_score > 0 ? `Максимум ${journalValue(assessment.max_score)}` : ''].filter(Boolean);
          return <li className={`student-assessment is-${presentation.kind}`} key={assessment.id}>
            <div><strong>{assessment.title}</strong><div className="student-assessment-date"><small>{formatDate(assessment.assessed_on)}</small>
              {details.length ? <details className="student-assessment-meta"><summary aria-label="Вес и максимальный балл" title="Вес и максимальный балл"><Info size={13} aria-hidden="true" /></summary><span>{details.join(' · ')}</span></details> : null}
            </div></div>
            <span className={`student-result is-${presentation.kind}`}>{presentation.value}{presentation.note ? <small>{presentation.note}</small> : null}</span>
          </li>;
        })}</ol>
        {!studentJournalRows(selected).length ? <p className="sync-summary sync-summary--empty">Работы пока не опубликованы.</p> : null}
        <details className="student-journal-more"><summary>Показатели и сведения о журнале</summary>
          {otherSummaries.length ? <dl className="student-journal-summaries">{otherSummaries.map(summary => <div key={`${summary.summary_type}:${summary.label}`}><dt>{summary.label}</dt><dd>{typeof summary.numeric_value === 'number' ? journalValue(summary.numeric_value) : summaryText(summary)}</dd></div>)}</dl> : null}
          <p>{selected.source.sheet_title}</p>{selected.source.last_synced_at ? <p>Обновлено {formatDate(selected.source.last_synced_at)}</p> : null}
        </details>
      </div> : <div className="student-subject-list" aria-label="Предметы и результаты">{[...periodGroups].map(([key, groupItems]) => <div className="student-subject-group" key={key}>
        {periodGroups.size > 1 || view === 'all' || view === 'archived' ? <h3>{periods.get(key)}</h3> : null}
        {groupItems.map(item => {
          const indicator = studentSubjectIndicator(item);
          const context = disambiguation(item);
          return <button type="button" className="student-subject-row" key={journalKey(item)} data-journal-key={journalKey(item)} aria-label={`Открыть журнал: ${item.source.subject}`} aria-describedby={`student-indicator-${journalKey(item)}`} onClick={() => openJournal(item)}>
            <span className="student-subject-copy"><strong title={item.source.subject}>{item.source.subject}</strong>{context ? <small>{context}</small> : null}</span>
            <span id={`student-indicator-${journalKey(item)}`} className={`student-subject-indicator is-${indicator.kind}`} title={indicator.note}><b>{indicator.value}</b>{indicator.kind === 'average' ? <small>Средняя</small> : null}</span>
            <ChevronRight size={17} aria-hidden="true" />
          </button>;
        })}
      </div>)}</div>}
    </> : null}
  </section>;
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
          <div className="journal-detail-nav"><PeriodViewControl view={view} onChange={next => { setDetailError(''); setView(next); }} /></div>
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
