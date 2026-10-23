'use client';

import { useCallback, useEffect, useMemo, useState, type SubmitEvent } from 'react';
import { AlertTriangle, ArrowLeft, BookOpen, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import { GoogleConnectionCard } from '@/components/google-connection-card';

type ApiRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type JournalSource = {
  id: string | number; spreadsheet_id: string; spreadsheet_title: string; grade: number;
  subject: string; sheet_title: string; sheet_id?: string | number | null; status: string;
  last_synced_at?: string | null; last_error?: string | null; auto_sync_enabled?: boolean | number;
  assessment_count?: number; result_count?: number; roster_student_count?: number;
  unmapped_count?: number; markers?: string[]; refreshing?: boolean;
  active_run_id?: string | number | null;
};
type GradeResult = { numeric_score: number | null; status: string | null } | null;
type JournalStudent = { id: string | number; display_name: string; group_marker: string; identity_id?: string | number | null; match_status: string; summaries: Array<{ label: string; numeric_value: number | null; text_value: string | null }>; results: Record<string, GradeResult> };
type JournalDetail = { source: JournalSource; groups: string[]; assessments: Array<{ id: string | number; title: string; assessed_on?: string | null; weight?: number | null; max_score?: number | null; group_marker: string }>; students: JournalStudent[] };
type JournalSyncRun = { id: string | number; trigger_kind: string; status: string; started_at: string; finished_at?: string | null; error?: string | null; counts?: Record<string, number> };
type PersonOption = { id: string | number; display_name: string; class_name?: string | null };
type PersonResults = { items: PersonOption[] };

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Не удалось выполнить запрос. Проверьте подключение и повторите попытку.';
}

function dateText(value?: string | null): string {
  if (!value) return 'ещё не обновлялся';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' });
}

function assessmentDateText(value?: string | null): string {
  if (!value) return 'дата не указана';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ru-RU', { dateStyle: 'medium' });
}

function scoreText(value: GradeResult): string {
  if (!value) return '—';
  if (value.numeric_score !== null && value.numeric_score !== undefined) return String(value.numeric_score).replace('.', ',');
  return value.status || '—';
}

export function AdminJournals({ api, apiOrigin }: { api: ApiRequest; apiOrigin: string }) {
  const [sources, setSources] = useState<JournalSource[]>([]);
  const [schedulerConfigured, setSchedulerConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [selectedId, setSelectedId] = useState<string | number | null>(null);
  const [detail, setDetail] = useState<JournalDetail | null>(null);
  const [syncHistory, setSyncHistory] = useState<JournalSyncRun[]>([]);
  const [groupMarker, setGroupMarker] = useState('');
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  const [connectionUrl, setConnectionUrl] = useState('');
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [people, setPeople] = useState<Record<string, PersonOption[]>>({});
  const [searching, setSearching] = useState('');
  const [progress, setProgress] = useState<Record<string, { completed: number; total: number }>>({});

  const loadSources = useCallback(async () => {
    try {
      const result = await api<{ items: JournalSource[]; scheduler_configured?: boolean }>('/api/admin/journals');
      setSources(result.items || []);
      setSchedulerConfigured(Boolean(result.scheduler_configured));
      return result.items || [];
    } catch (error) {
      setLoadError(errorText(error));
      return [];
    } finally {
      setLoading(false);
    }
  }, [api]);

  const loadDetail = useCallback(async (id: string | number, marker = '') => {
    setDetail(null);
    setSyncHistory([]);
    setSelectedId(id);
    setGroupMarker(marker);
    try {
      const query = marker ? `?group_marker=${encodeURIComponent(marker)}` : '';
      let result = await api<JournalDetail>(`/api/admin/journals/${id}/detail${query}`);
      if (!marker && result.groups.some((item) => ['A', 'B', 'C'].includes(item))) {
        marker = 'A';
        setGroupMarker(marker);
        result = await api<JournalDetail>(`/api/admin/journals/${id}/detail?group_marker=A`);
      }
      setDetail(result);
      const history = await api<{ items: JournalSyncRun[] }>(`/api/admin/journals/${id}/sync-history`);
      setSyncHistory(history.items || []);
    } catch (error) {
      setActionError(errorText(error));
    }
  }, [api]);

  useEffect(() => {
    const runningSource = sources.find((source) => source.active_run_id);
    const runId = runningSource?.active_run_id;
    if (!runId) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const run = await api<JournalSyncRun>(`/api/admin/journals/sync-runs/${encodeURIComponent(String(runId))}`);
        if (cancelled) return;
        if (runningSource && run.counts?.total_tabs) {
          setProgress((current) => ({ ...current, [runningSource.spreadsheet_id]: { completed: run.counts?.completed_tabs || 0, total: run.counts?.total_tabs || 0 } }));
        }
        await loadSources();
        if (run.status !== 'running') {
          if (run.status === 'error') setActionError('Не удалось обновить журнал. Предыдущие данные сохранены. Повторите обновление позже.');
          else if (run.status === 'partial') setActionError('Часть предметов не обновилась. Проверьте сообщения в карточках предметов.');
          if (selectedId) await loadDetail(selectedId, groupMarker);
        }
      } catch {
        if (!cancelled) void loadSources();
      }
    }, 3000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [api, sources, loadSources, loadDetail, selectedId, groupMarker]);

  useEffect(() => {
    let active = true;
    api<{ items: JournalSource[]; scheduler_configured?: boolean }>('/api/admin/journals')
      .then((result) => {
        if (!active) return;
        setSources(result.items || []);
        setSchedulerConfigured(Boolean(result.scheduler_configured));
      })
      .catch((error: unknown) => { if (active) setLoadError(errorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);

  const books = useMemo(() => {
    const map = new Map<string, { id: string; title: string; grade: number; spreadsheetId: string; subjects: JournalSource[]; syncedAt?: string | null; error?: string | null; auto: boolean; refreshing: boolean }>();
    for (const source of sources) {
      const key = source.spreadsheet_id;
      const book = map.get(key) || { id: key, title: source.spreadsheet_title, grade: source.grade, spreadsheetId: key, subjects: [], auto: Boolean(source.auto_sync_enabled), refreshing: false };
      book.subjects.push(source);
      book.auto ||= Boolean(source.auto_sync_enabled);
      book.refreshing ||= Boolean(source.refreshing || source.active_run_id) || source.status === 'refreshing';
      if (!book.syncedAt || (source.last_synced_at && source.last_synced_at > book.syncedAt)) book.syncedAt = source.last_synced_at;
      if (source.last_error) book.error = 'Часть предметов не обновилась. Предыдущие данные сохранены.';
      map.set(key, book);
    }
    return [...map.values()].sort((a, b) => a.grade - b.grade || a.title.localeCompare(b.title, 'ru'));
  }, [sources]);

  const selectedSource = sources.find((source) => String(source.id) === String(selectedId));
  const subjectSummaryLabels = useMemo(() => [...new Set((detail?.students || []).flatMap((student) => student.summaries.map((summary) => summary.label)))], [detail]);

  async function connect(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy('connect'); setActionError('');
    try {
      const result = await api<{ spreadsheet_id: string; auto_sync_enabled: boolean; scheduler_configured?: boolean; tabs?: Array<{ subject: string; status: string }> }>('/api/admin/journals/connect', {
        method: 'POST', body: JSON.stringify({ spreadsheet_url: connectionUrl, grade: 9, auto_sync_enabled: true }),
      });
      setConnectionOpen(false); setConnectionUrl('');
      const next = await loadSources();
      const source = next.find((item) => item.spreadsheet_id === result.spreadsheet_id);
      if (source) await loadDetail(source.id);
      if (!result.auto_sync_enabled) setActionError(result.scheduler_configured === false
        ? 'Таблица подключена. Почасовое расписание ещё не настроено на сервере.'
        : 'Не все вкладки обновились. Автоматическая синхронизация включится после успешного импорта всех вкладок.');
    } catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function refreshBook(spreadsheetId: string, grade: number) {
    setBusy(`refresh:${spreadsheetId}`); setActionError('');
    try {
      const query = new URLSearchParams({ grade: String(grade), spreadsheet_id: spreadsheetId });
      const result = await api<{ status: string; run_id?: string }>(`/api/admin/journals/refresh-grade?${query}`, { method: 'POST' });
      await loadSources();
      if (result.status === 'running') return;
      if (selectedId) await loadDetail(selectedId, groupMarker);
      if (result.status !== 'success') setActionError('Не все вкладки обновились. Для ошибочных вкладок сохранены предыдущие данные; подробности доступны в карточках предметов.');
    } catch (error) { setActionError(errorText(error)); await loadSources(); }
    finally { setBusy(''); }
  }

  async function setAutomation(spreadsheetId: string, enabled: boolean) {
    setBusy(`auto:${spreadsheetId}`); setActionError('');
    try {
      await api('/api/admin/journals/auto-sync', { method: 'POST', body: JSON.stringify({ spreadsheet_id: spreadsheetId, enabled }) });
      await loadSources();
    } catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function findPeople(student: JournalStudent) {
    setSearching(String(student.id));
    try {
      const result = await api<PersonResults>(`/api/admin/people?kind=student&q=${encodeURIComponent(student.display_name)}&class_name=9`);
      setPeople((current) => ({ ...current, [String(student.id)]: result.items || [] }));
    } catch (error) { setActionError(errorText(error)); }
    finally { setSearching(''); }
  }

  async function matchStudent(student: JournalStudent, identityId: string | number) {
    if (!selectedSource) return;
    setBusy(`match:${student.id}`); setActionError('');
    try {
      await api(`/api/admin/journals/${selectedSource.id}/students/${student.id}/match`, { method: 'POST', body: JSON.stringify({ identity_id: identityId }) });
      await loadDetail(selectedSource.id, groupMarker);
    } catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  if (loading) return <div className="admin-loading"><LoaderCircle size={17} className="admin-spin" /> Загружаем журналы…</div>;

  return <div className="admin-stack journal-workspace">
    <GoogleConnectionCard api={api} apiOrigin={apiOrigin} />
    {actionError ? <div className="admin-error" role="alert"><AlertTriangle size={17} /><div><strong>Синхронизация журнала</strong><p>{actionError}</p></div><button className="admin-link" onClick={() => setActionError('')}>Закрыть</button></div> : null}
    {loadError ? <div className="admin-error" role="alert"><AlertTriangle size={17} /><div><strong>Не удалось загрузить список</strong><p>{loadError}</p></div><button className="admin-button admin-button--quiet" onClick={() => { setLoading(true); setLoadError(''); void loadSources(); }}>Повторить</button></div> : null}

    {!detail ? <>
      <div className="admin-page-actions journal-intro"><p>Просмотр импортированных оценок из школьных таблиц. Изменения в этой панели не попадают в оригинал.{!schedulerConfigured ? <><br /><span className="admin-help-text">Почасовое обновление будет доступно после настройки защищённого серверного расписания.</span></> : null}</p><button className="admin-button admin-button--primary" onClick={() => { setActionError(''); setConnectionOpen((value) => !value); }}><BookOpen size={15} /> Подключить Google Таблицу</button></div>
      {connectionOpen ? <section className="admin-panel journal-connect-panel"><div><h2>Подключить журнал девятого класса</h2><p className="admin-help-text">Вставьте ссылку на Google Таблицу. Будут импортированы предметные вкладки; оценки останутся доступны только администраторам.</p></div><form onSubmit={(event) => void connect(event)}><label htmlFor="journal-source-url">Ссылка на таблицу</label><input id="journal-source-url" value={connectionUrl} onChange={(event) => setConnectionUrl(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" required /><div className="admin-button-row"><button className="admin-button admin-button--primary" disabled={busy === 'connect'}>{busy === 'connect' ? <LoaderCircle size={15} className="admin-spin" /> : null} Подключить и импортировать</button><button type="button" className="admin-button admin-button--quiet" onClick={() => setConnectionOpen(false)}>Отмена</button></div></form></section> : null}
      {!books.length ? <section className="admin-panel journal-empty"><div className="journal-empty-mark"><BookOpen size={23} /></div><h2>Журналов пока нет</h2><p>Подключите таблицу, чтобы импортировать предметы, работы и результаты учеников.</p><button className="admin-button admin-button--primary" onClick={() => setConnectionOpen(true)}>Подключить таблицу</button></section> : books.map((book) => <section className="admin-panel journal-book" key={book.spreadsheetId}>
        <div className="journal-book-heading"><div><p className="admin-eyebrow">УЧЕБНЫЙ ГОД 2026/27</p><h2>{book.grade} класс</h2><p>{book.title} <a href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(book.spreadsheetId)}/edit`} target="_blank" rel="noreferrer" aria-label="Открыть оригинальную таблицу"><ExternalLink size={13} /></a></p></div><div className="journal-book-actions"><span className={`journal-sync-status ${book.refreshing ? 'is-running' : book.error ? 'is-error' : 'is-ready'}`}><i />{book.refreshing ? (progress[book.spreadsheetId]?.total ? `Обновляется ${progress[book.spreadsheetId].completed} из ${progress[book.spreadsheetId].total}` : 'Обновляется') : book.error ? 'Есть ошибка' : `Обновлено ${dateText(book.syncedAt)}`}</span><button className="admin-button admin-button--quiet" disabled={Boolean(busy) || book.refreshing} onClick={() => void refreshBook(book.spreadsheetId, book.grade)}><RefreshCw size={15} className={busy.startsWith('refresh:') ? 'admin-spin' : ''} /> {busy === `refresh:${book.spreadsheetId}` ? 'Обновляем…' : 'Обновить сейчас'}</button><label className="journal-toggle"><input type="checkbox" checked={book.auto} disabled={Boolean(busy) || !schedulerConfigured} onChange={(event) => void setAutomation(book.spreadsheetId, event.target.checked)} /><span>{schedulerConfigured ? 'Автоматически каждый час' : 'Расписание не настроено'}</span></label></div></div>
        {book.error ? <div className="journal-source-error"><AlertTriangle size={14} /><span>{book.error}</span></div> : null}
        <div className="journal-subject-heading"><strong>Предметы</strong><span>{book.subjects.length}</span></div><div className="journal-subject-grid">{book.subjects.map((source) => <button key={source.id} className="journal-subject-card" onClick={() => { setActionError(''); void loadDetail(source.id); }}><span className="journal-subject-icon"><BookOpen size={15} /></span><span className="journal-subject-copy"><strong>{source.subject || source.sheet_title}</strong><small>{source.assessment_count || 0} работ · {source.roster_student_count || 0} учеников</small></span><span className={`journal-subject-state ${source.status === 'error' ? 'is-error' : ''}`} title={source.status === 'empty' ? 'Вкладка без оценок' : source.status === 'error' ? 'Не удалось обновить вкладку. Предыдущие данные сохранены.' : 'Обновлено'}>{source.status === 'error' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}</span></button>)}</div>
      </section>)}
    </> : <>
      <button className="admin-back" onClick={() => { setDetail(null); setSelectedId(null); setActionError(''); }}><ArrowLeft size={15} /> Все журналы</button>
      <section className="admin-panel journal-detail-head"><div className="journal-detail-title"><div className="journal-detail-icon"><BookOpen size={18} /></div><div><p className="admin-eyebrow">{detail.source.grade} КЛАСС · ЖУРНАЛ ПРЕДМЕТА</p><h2>{detail.source.subject}</h2><p>{detail.source.spreadsheet_title} <span>·</span> обновлено {dateText(detail.source.last_synced_at)}</p></div></div><div className="admin-button-row"><a className="admin-button admin-button--quiet" href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(detail.source.spreadsheet_id)}/edit#gid=${encodeURIComponent(String(detail.source.sheet_id || ''))}`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Оригинальная таблица</a><button className="admin-button admin-button--quiet" disabled={Boolean(busy) || Boolean(selectedSource?.active_run_id)} onClick={() => void refreshBook(detail.source.spreadsheet_id, detail.source.grade)}><RefreshCw size={14} /> Обновить сейчас</button></div></section>
      {detail.source.last_error ? <div className="journal-source-error"><AlertTriangle size={14} /><span>Не удалось обновить вкладку. Предыдущие данные сохранены.</span></div> : null}
      <details className="admin-panel journal-history"><summary>История синхронизаций <span>{syncHistory.length}</span></summary>{syncHistory.length ? <ul>{syncHistory.map((run) => <li key={run.id}><strong>{run.status === 'success' ? 'Успешно' : run.status === 'partial' ? 'Частично' : run.status === 'running' ? 'Выполняется' : 'Ошибка'}</strong><span>{run.trigger_kind === 'scheduled' ? 'По расписанию' : 'Вручную'} · {dateText(run.started_at)}</span>{run.error ? <small>Синхронизация завершилась с ошибкой. Предыдущие данные сохранены.</small> : null}</li>)}</ul> : <p className="admin-help-text">Синхронизаций ещё нет.</p>}</details>
      {detail.groups.filter((marker) => marker !== 'all').length ? <div className="journal-group-tabs" role="tablist" aria-label="Учебная группа">{detail.groups.filter((marker) => marker !== 'all').map((marker) => <button key={marker} role="tab" aria-selected={(groupMarker || detail.groups[0]) === marker} className={(groupMarker || detail.groups[0]) === marker ? 'is-active' : ''} onClick={() => void loadDetail(detail.source.id, marker)}>Группа {marker}</button>)}</div> : null}
      {!detail.students.length ? <section className="admin-panel"><div className="admin-empty">В этой группе пока нет распознанных учеников.</div></section> : <section className="admin-panel admin-table-panel journal-table-panel"><div className="admin-table-meta"><span>{detail.students.length} учеников · {detail.assessments.length} заданий</span><span>Ноль — оценка, «н» — отметка о пропуске</span></div><div className="admin-table-wrap journal-table-wrap"><table className="admin-table journal-table"><thead><tr><th className="journal-student-column">Ученик</th>{detail.assessments.map((assessment) => <th className="journal-assessment-column" key={assessment.id}><strong>{assessment.title}</strong><small>{assessmentDateText(assessment.assessed_on)}</small><small>вес {assessment.weight ?? '—'} · макс. {assessment.max_score ?? '—'}</small></th>)}{subjectSummaryLabels.map((label) => <th className="journal-summary-column" key={label}>{label}</th>)}</tr></thead><tbody>{detail.students.map((student) => <tr key={student.id}><th className="journal-student-column"><strong>{student.display_name}</strong><small>{student.group_marker === 'all' ? '9 класс' : `группа ${student.group_marker}`}{student.match_status === 'linked' ? '' : ' · не сопоставлен'}</small>{student.match_status !== 'linked' ? <details className="journal-match"><summary>{searching === String(student.id) ? 'Ищем…' : 'Выбрать ученика'}</summary><div><button className="admin-link" onClick={() => void findPeople(student)}><Search size={13} /> Найти в справочнике</button>{(people[String(student.id)] || []).map((person) => <button key={person.id} onClick={() => void matchStudent(student, person.id)} disabled={Boolean(busy)}><strong>{person.display_name}</strong><small>{person.class_name || 'класс не указан'}</small></button>)}</div></details> : null}</th>{detail.assessments.map((assessment) => <td className="journal-score-cell" key={assessment.id}>{scoreText(student.results[String(assessment.id)] || null)}</td>)}{subjectSummaryLabels.map((label) => { const summary = student.summaries.find((item) => item.label === label); return <td className="journal-score-cell journal-score-cell--summary" key={label}>{summary?.numeric_value !== null && summary?.numeric_value !== undefined ? String(summary.numeric_value).replace('.', ',') : summary?.text_value || '—'}</td>; })}</tr>)}</tbody></table></div></section>}
    </>}
  </div>;
}
