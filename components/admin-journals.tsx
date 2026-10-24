'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type SubmitEvent } from 'react';
import { AlertTriangle, ArrowLeft, BookOpen, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import { GoogleConnectionCard } from '@/components/google-connection-card';

type ApiRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type JournalSource = {
  id: string | number; spreadsheet_id: string; spreadsheet_title: string; grade: number;
  subject: string; sheet_title: string; sheet_id?: string | number | null; status: string;
  last_synced_at?: string | null; last_error?: string | null; auto_sync_enabled?: boolean | number;
  assessment_count?: number; result_count?: number; roster_student_count?: number; team_count?: number;
  unmapped_count?: number; markers?: string[]; refreshing?: boolean;
  active_run_id?: string | number | null;
};
type GradeResult = { numeric_score: number | null; status: string | null } | null;
type JournalStudent = { id: string | number; display_name: string; group_marker: string; identity_id?: string | number | null; match_status: string; entry_kind?: 'individual' | 'team'; members?: Array<{ identity_id: string | number; display_name: string; class_name?: string | null }>; summaries: Array<{ label: string; numeric_value: number | null; text_value: string | null }>; results: Record<string, GradeResult> };
type JournalDetail = { source: JournalSource; groups: string[]; assessments: Array<{ id: string | number; title: string; assessed_on?: string | null; weight?: number | null; max_score?: number | null; group_marker: string }>; students: JournalStudent[] };
type JournalSyncRun = { id: string | number; trigger_kind: string; status: string; started_at: string; finished_at?: string | null; error?: string | null; counts?: Record<string, number> };
type PersonOption = { id: string | number; display_name: string; class_name?: string | null };
type PersonResults = { items: PersonOption[] };
type MatchingData = { individuals: Array<{ source_name: string; subject_count: number; record_count: number; result_count: number; suggestion: null | { identity_id: string | number; display_name: string; class_name?: string | null; status: string; groups: Array<{ id: string | number; name: string }>; reason: string } }>; teams: Array<{ id: string | number; source_id: string | number; display_name: string; subject: string; group_marker: string; result_count: number; members: Array<{ identity_id: string | number; display_name: string; class_name?: string | null }> }> };
type MatchPreview = { source_name: string; identity: { id: string | number; display_name: string; status: string; class_name?: string | null; groups: Array<{ id: string | number; name: string }> }; subject_count: number; record_count: number; result_count: number; preview_token: string };

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
  const [progress, setProgress] = useState<Record<string, { completed: number; total: number }>>({});
  const [matchingBook, setMatchingBook] = useState('');
  const [matching, setMatching] = useState<MatchingData | null>(null);
  const [matchSearch, setMatchSearch] = useState<Record<string, string>>({});
  const [matchOptions, setMatchOptions] = useState<Record<string, PersonOption[]>>({});
  const searchRequestIds = useRef<Record<string, number>>({});
  const previewRequestId = useRef(0);
  const [matchChoice, setMatchChoice] = useState<Record<string, string>>({});
  const [matchPreview, setMatchPreview] = useState<MatchPreview | null>(null);
  const [teamChoices, setTeamChoices] = useState<Record<string, string[]>>({});

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

  async function openMatching(spreadsheetId: string) {
    previewRequestId.current += 1;
    setMatchingBook(spreadsheetId); setMatching(null); setMatchPreview(null); setMatchSearch({}); setMatchOptions({}); setMatchChoice({}); setTeamChoices({}); searchRequestIds.current = {}; setActionError(''); setBusy('matching');
    try { setMatching(await api<MatchingData>(`/api/admin/journals/matching?spreadsheet_id=${encodeURIComponent(spreadsheetId)}`)); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function searchMatchPeople(key: string, query: string) {
    previewRequestId.current += 1;
    setMatchSearch((current) => ({ ...current, [key]: query })); setMatchPreview(null);
    const requestId = (searchRequestIds.current[key] || 0) + 1;
    searchRequestIds.current[key] = requestId;
    if (!key.startsWith('team:')) setMatchOptions((current) => ({ ...current, [key]: [] }));
    if (query.trim().length < 2) return;
    try { const result = await api<PersonResults>(`/api/admin/people?kind=student&q=${encodeURIComponent(query)}&class_name=9`); if (searchRequestIds.current[key] === requestId) setMatchOptions((current) => ({ ...current, [key]: key.startsWith('team:') ? [...(current[key] || []), ...(result.items || [])].filter((person, index, all) => all.findIndex((candidate) => String(candidate.id) === String(person.id)) === index) : result.items || [] })); }
    catch (error) { if (searchRequestIds.current[key] === requestId) setActionError(errorText(error)); }
  }

  async function previewMatch(sourceName: string) {
    const identityId = matchChoice[sourceName]; if (!identityId) return;
    const requestId = ++previewRequestId.current;
    setBusy(`preview:${sourceName}`); setActionError(''); setMatchPreview(null);
    try { const preview = await api<MatchPreview>('/api/admin/journals/matching/preview', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_name: sourceName, identity_id: identityId }) }); if (previewRequestId.current === requestId) setMatchPreview(preview); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function confirmMatch() {
    if (!matchPreview) return;
    setBusy('confirm-match'); setActionError('');
    try {
      const result = await api<{ matched_records: number; updated_results: number }>('/api/admin/journals/matching/confirm', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_name: matchPreview.source_name, identity_id: matchPreview.identity.id, preview_token: matchPreview.preview_token }) });
      setMatchPreview(null); await openMatching(matchingBook); if (selectedId) await loadDetail(selectedId, groupMarker); await loadSources();
      setActionError(`Сопоставление сохранено: ${result.matched_records} записей, обновлено результатов: ${result.updated_results}.`);
    } catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function saveTeamMembers(teamId: string | number) {
    const ids = teamChoices[String(teamId)] || matching?.teams.find((team) => String(team.id) === String(teamId))?.members.map((member) => String(member.identity_id)) || [];
    setBusy(`team:${teamId}`); setActionError('');
    try { await api(`/api/admin/journals/teams/${teamId}/members`, { method: 'PUT', body: JSON.stringify({ identity_ids: ids }) }); await openMatching(matchingBook); if (selectedId) await loadDetail(selectedId, groupMarker); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  function resultTable(rows: JournalStudent[], teams = false) {
    return <section className="admin-panel admin-table-panel journal-table-panel"><div className="admin-table-meta"><span>{rows.length} {teams ? 'команд' : 'учеников'} · {detail?.assessments.length || 0} заданий</span><span>Ноль — оценка, «н» — отметка о пропуске</span></div><div className="admin-table-wrap journal-table-wrap"><table className="admin-table journal-table"><thead><tr><th className="journal-student-column">{teams ? 'Команда' : 'Ученик'}</th>{detail?.assessments.map((assessment) => <th className="journal-assessment-column" key={assessment.id}><strong>{assessment.title}</strong><small>{assessmentDateText(assessment.assessed_on)}</small><small>вес {assessment.weight ?? '—'} · макс. {assessment.max_score ?? '—'}</small></th>)}{subjectSummaryLabels.map((label) => <th className="journal-summary-column" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((student) => <tr key={student.id}><th className="journal-student-column"><strong>{student.display_name}</strong><small>{teams ? `Участники: ${(student.members || []).map((member) => member.display_name).join(', ') || 'не указаны'}` : <>{student.group_marker === 'all' ? '9 класс' : `группа ${student.group_marker}`}{student.match_status === 'linked' ? '' : ' · не сопоставлен'}</>}</small>{!teams && student.match_status !== 'linked' ? <details className="journal-match"><summary>Не сопоставлен</summary><div><button className="admin-link" onClick={() => { if (selectedSource) { const spreadsheetId = selectedSource.spreadsheet_id; setDetail(null); setSelectedId(null); void openMatching(spreadsheetId); } }}><Search size={13} /> Открыть панель сопоставлений</button></div></details> : null}</th>{detail?.assessments.map((assessment) => <td className="journal-score-cell" key={assessment.id}>{scoreText(student.results[String(assessment.id)] || null)}</td>)}{subjectSummaryLabels.map((label) => { const summary = student.summaries.find((item) => item.label === label); return <td className="journal-score-cell journal-score-cell--summary" key={label}>{summary?.numeric_value !== null && summary?.numeric_value !== undefined ? String(summary.numeric_value).replace('.', ',') : summary?.text_value || '—'}</td>; })}</tr>)}</tbody></table></div></section>;
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
        <div className="journal-subject-heading"><strong>Предметы</strong><span>{book.subjects.length}</span></div><div className="journal-subject-grid">{book.subjects.map((source) => <button key={source.id} className="journal-subject-card" onClick={() => { setActionError(''); void loadDetail(source.id); }}><span className="journal-subject-icon"><BookOpen size={15} /></span><span className="journal-subject-copy"><strong>{source.subject || source.sheet_title}</strong><small>{source.assessment_count || 0} работ · {source.roster_student_count || 0} учеников{source.team_count ? ` · ${source.team_count} команд` : ''}</small></span><span className={`journal-subject-state ${source.status === 'error' ? 'is-error' : ''}`} title={source.status === 'empty' ? 'Вкладка без оценок' : source.status === 'error' ? 'Не удалось обновить вкладку. Предыдущие данные сохранены.' : 'Обновлено'}>{source.status === 'error' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}</span></button>)}</div>
        <div className="journal-map-action"><button className="admin-button admin-button--quiet" onClick={() => matchingBook === book.spreadsheetId ? (setMatchingBook(''), setMatching(null), setMatchPreview(null)) : void openMatching(book.spreadsheetId)}><Search size={14} /> {matchingBook === book.spreadsheetId ? 'Скрыть сопоставления' : 'Сопоставления учеников и команд'}</button></div>
        {matchingBook === book.spreadsheetId && !matching && busy === 'matching' ? <section className="journal-matching-panel"><div className="admin-loading"><LoaderCircle size={16} className="admin-spin" /> Загружаем сопоставления…</div></section> : null}
        {matchingBook === book.spreadsheetId && matching ? <section className="journal-matching-panel" aria-label="Сопоставление журнала"><h3>Индивидуальные записи</h3><p className="admin-help-text">Одинаковое имя объединено для всех предметов этой таблицы. Подтверждение покажет изменяемые записи до сохранения.</p>
          <p className="admin-help-text">Ожидают подтверждения: {matching.individuals.length} имён / {matching.individuals.reduce((total, item) => total + item.record_count, 0)} записей. Предложение само по себе ничего не связывает.</p>
          {!matching.individuals.length ? <p className="admin-help-text">Неразобранных индивидуальных записей нет.</p> : matching.individuals.map((item) => <div className="journal-mapping-row" key={item.source_name}><div><strong>{item.source_name}</strong><small>{item.subject_count} предмета · {item.record_count} записей · {item.result_count} результатов</small>{item.suggestion ? <small>Возможный вариант: {item.suggestion.display_name} · {item.suggestion.class_name || 'класс не указан'} · {item.suggestion.reason}</small> : null}</div><div className="journal-map-controls"><input aria-label={`Поиск ученика для ${item.source_name}`} value={matchSearch[item.source_name] || ''} onChange={(event) => void searchMatchPeople(item.source_name, event.target.value)} placeholder="Поиск в справочнике" /><select aria-label={`Выбрать ученика для ${item.source_name}`} value={matchChoice[item.source_name] || ''} onChange={(event) => { previewRequestId.current += 1; setMatchPreview(null); setMatchChoice((current) => ({ ...current, [item.source_name]: event.target.value })); }}><option value="">Выберите ученика</option>{item.suggestion && !(matchOptions[item.source_name] || []).some((person) => String(person.id) === String(item.suggestion?.identity_id)) ? <option value={item.suggestion.identity_id}>{item.suggestion.display_name} · {item.suggestion.class_name || 'класс не указан'}</option> : null}{(matchOptions[item.source_name] || []).map((person) => <option key={person.id} value={person.id}>{person.display_name} · {person.class_name || 'класс не указан'}</option>)}</select><button className="admin-button admin-button--quiet" disabled={!matchChoice[item.source_name] || Boolean(busy)} onClick={() => void previewMatch(item.source_name)}>Предпросмотр</button></div></div>)}
          {matchPreview ? <div className="journal-match-review"><div><strong>Проверьте сопоставление</strong><p>{matchPreview.source_name} → {matchPreview.identity.display_name} · {matchPreview.subject_count} предмета · {matchPreview.record_count} записей · {matchPreview.result_count} результатов</p><small>{matchPreview.identity.class_name || 'класс не указан'} · {matchPreview.identity.status}. Группы: {matchPreview.identity.groups.map((group) => group.name).join(', ') || 'не указаны'}. Подтверждение свяжет записи таблицы с этим учеником.</small><details><summary>Идентификатор записи</summary><code>{matchPreview.identity.id}</code></details></div><button className="admin-button admin-button--primary" disabled={busy === 'confirm-match'} onClick={() => void confirmMatch()}>{busy === 'confirm-match' ? 'Сохраняем…' : 'Подтвердить сопоставление'}</button></div> : null}
          <h3>Команды</h3>{!matching.teams.length ? <p className="admin-help-text">Командных записей нет.</p> : matching.teams.map((team) => { const selected = teamChoices[String(team.id)] || team.members.map((member) => String(member.identity_id)); return <div className="journal-mapping-row" key={team.id}><div><strong>{team.display_name}</strong><small>{team.subject} · группа {team.group_marker} · {team.result_count} результатов</small><small>Участники: {team.members.map((member) => member.display_name).join(', ') || 'не указаны'}</small></div><div className="journal-map-controls"><label>Найти участников<input value={matchSearch[`team:${team.id}`] || ''} onChange={(event) => void searchMatchPeople(`team:${team.id}`, event.target.value)} placeholder="Введите имя" /></label><label>Участники<select multiple aria-label={`Участники команды ${team.display_name}`} value={selected} onChange={(event) => setTeamChoices((current) => ({ ...current, [String(team.id)]: Array.from(event.target.selectedOptions, (option) => option.value) }))}>{[...team.members.map((member) => ({ id: member.identity_id, display_name: member.display_name, class_name: member.class_name })), ...(matchOptions[`team:${team.id}`] || [])].filter((person, index, all) => all.findIndex((candidate) => String(candidate.id) === String(person.id)) === index).map((person) => <option key={person.id} value={String(person.id)}>{person.display_name} · {person.class_name || 'класс не указан'}</option>)}</select></label><button className="admin-button admin-button--quiet" onClick={() => void saveTeamMembers(team.id)} disabled={Boolean(busy)}>Сохранить состав</button></div></div>; })}
        </section> : null}
      </section>)}
    </> : <>
      <button className="admin-back" onClick={() => { setDetail(null); setSelectedId(null); setActionError(''); }}><ArrowLeft size={15} /> Все журналы</button>
      <section className="admin-panel journal-detail-head"><div className="journal-detail-title"><div className="journal-detail-icon"><BookOpen size={18} /></div><div><p className="admin-eyebrow">{detail.source.grade} КЛАСС · ЖУРНАЛ ПРЕДМЕТА</p><h2>{detail.source.subject}</h2><p>{detail.source.spreadsheet_title} <span>·</span> обновлено {dateText(detail.source.last_synced_at)}</p></div></div><div className="admin-button-row"><a className="admin-button admin-button--quiet" href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(detail.source.spreadsheet_id)}/edit#gid=${encodeURIComponent(String(detail.source.sheet_id || ''))}`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Оригинальная таблица</a><button className="admin-button admin-button--quiet" disabled={Boolean(busy) || Boolean(selectedSource?.active_run_id)} onClick={() => void refreshBook(detail.source.spreadsheet_id, detail.source.grade)}><RefreshCw size={14} /> Обновить сейчас</button></div></section>
      {detail.source.last_error ? <div className="journal-source-error"><AlertTriangle size={14} /><span>Не удалось обновить вкладку. Предыдущие данные сохранены.</span></div> : null}
      <details className="admin-panel journal-history"><summary>История синхронизаций <span>{syncHistory.length}</span></summary>{syncHistory.length ? <ul>{syncHistory.map((run) => <li key={run.id}><strong>{run.status === 'success' ? 'Успешно' : run.status === 'partial' ? 'Частично' : run.status === 'running' ? 'Выполняется' : 'Ошибка'}</strong><span>{run.trigger_kind === 'scheduled' ? 'По расписанию' : 'Вручную'} · {dateText(run.started_at)}</span>{run.error ? <small>Синхронизация завершилась с ошибкой. Предыдущие данные сохранены.</small> : null}</li>)}</ul> : <p className="admin-help-text">Синхронизаций ещё нет.</p>}</details>
      {detail.groups.filter((marker) => marker !== 'all').length ? <div className="journal-group-tabs" role="tablist" aria-label="Учебная группа">{detail.groups.filter((marker) => marker !== 'all').map((marker) => <button key={marker} role="tab" aria-selected={(groupMarker || detail.groups[0]) === marker} className={(groupMarker || detail.groups[0]) === marker ? 'is-active' : ''} onClick={() => void loadDetail(detail.source.id, marker)}>Группа {marker}</button>)}</div> : null}
      {detail.students.some((student) => (student.entry_kind || 'individual') === 'individual') ? resultTable(detail.students.filter((student) => (student.entry_kind || 'individual') === 'individual')) : null}
      {detail.students.some((student) => student.entry_kind === 'team') ? <><h3 className="journal-team-heading">Командные результаты · оценки относятся к команде</h3>{resultTable(detail.students.filter((student) => student.entry_kind === 'team'), true)}</> : null}
      {!detail.students.length ? <section className="admin-panel"><div className="admin-empty">В этой группе пока нет распознанных учеников или команд.</div></section> : null}
    </>}
  </div>;
}
