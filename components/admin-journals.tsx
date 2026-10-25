'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type SubmitEvent } from 'react';
import { AlertTriangle, ArrowLeft, BookOpen, CheckCircle2, ExternalLink, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import { GoogleConnectionCard } from '@/components/google-connection-card';

type ApiRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type JournalSource = {
  id: string | number; spreadsheet_id: string; spreadsheet_title: string; grade: number;
  subject: string; sheet_title: string; sheet_id?: string | number | null; status: string;
  academic_year?: string; term_number?: number | null; period_status?: 'current' | 'archived' | 'unassigned';
  last_synced_at?: string | null; last_error?: string | null; auto_sync_enabled?: boolean | number;
  excluded_from_journals?: boolean;
  assessment_count?: number; result_count?: number; roster_student_count?: number; team_count?: number;
  unmapped_count?: number; markers?: string[]; refreshing?: boolean;
  active_run_id?: string | number | null;
};
type KnownJournalSource = { grade: number; spreadsheet_id: string; connected: boolean };
type GradeResult = { numeric_score: number | null; status: string | boolean | null } | null;
type JournalStudent = { id: string | number; display_name: string; group_marker: string; identity_id?: string | number | null; match_status: string; entry_kind?: 'individual' | 'team'; members?: Array<{ identity_id: string | number; display_name: string; class_name?: string | null }>; summaries: Array<{ label: string; summary_type?: string; numeric_value: number | null; text_value: string | null }>; results: Record<string, GradeResult> };
type JournalGroupMapping = { group_marker: string; group_id?: string | number | null; base_class_name?: string | null; subject_subgroup?: string | null; classroom_course_id?: string | null; exam_track?: string | null; source?: string | null };
type CanonicalGroup = { id: string | number; name: string; display_name?: string | null; group_type: string; subject?: string | null; base_class_name?: string | null; subject_subgroup?: string | null; exam_track?: string | null; student_count?: number };
type JournalDetail = { source: JournalSource; groups: string[]; mappings?: JournalGroupMapping[]; assessments: Array<{ id: string | number; title: string; assessed_on?: string | null; weight?: number | null; max_score?: number | null; group_marker: string }>; students: JournalStudent[] };
type JournalSyncRun = { id: string | number; trigger_kind: string; status: string; started_at: string; finished_at?: string | null; error?: string | null; counts?: Record<string, number> };
type PersonOption = { id: string | number; display_name: string; class_name?: string | null };
type PersonResults = { items: PersonOption[] };
type MatchSuggestion = { identity_id: string | number; display_name: string; class_name?: string | null; status?: string; groups?: Array<{ id: string | number; name: string }>; reason?: string };
type MatchingData = { individuals: Array<{ source_name: string; subject_count: number; record_count: number; result_count: number; suggestion: null | MatchSuggestion }>; linked?: Array<{ source_name: string; identity_id: string | number; identity_name: string; class_name?: string | null; record_count: number; subject_count: number; result_count: number; suggestions?: MatchSuggestion[] }>; teams: Array<{ id: string | number; source_id: string | number; display_name: string; subject: string; group_marker: string; result_count: number; members: Array<{ identity_id: string | number; display_name: string; class_name?: string | null }> }> };
type MatchPreview = { source_name: string; identity: { id: string | number; display_name: string; status: string; class_name?: string | null; groups: Array<{ id: string | number; name: string }> }; subject_count: number; record_count: number; result_count: number; preview_token: string; replace_existing?: boolean };
type Candidate = { id: string | number; display_name: string; class_name?: string | null; status?: string };
type MatchingBatchPreview = { preview_token: string | null; items?: Array<{ source_name: string; display_name?: string; record_count?: number; result_count?: number; subject_count?: number }>; duplicates?: Array<{ id: string | number; display_name: string; class_name?: string | null; status?: string }>; can_confirm?: boolean; affected_count?: number; identity?: Candidate; cross_grade?: boolean; [key: string]: unknown };

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
  const status = value.status === null || value.status === undefined ? '' : String(value.status).trim();
  if (status.toLowerCase() === 'true') return 'Выполнено';
  if (status.toLowerCase() === 'false') return 'Не выполнено';
  return status || '—';
}

function journalSubjectGroups(sources: JournalSource[]) {
  const groups = new Map<string, JournalSource[]>();
  for (const source of sources) groups.set(source.subject || source.sheet_title, [...(groups.get(source.subject || source.sheet_title) || []), source]);
  return [...groups.entries()].map(([subject, periods]) => ({ subject, periods: periods.sort((a, b) => (a.term_number || 0) - (b.term_number || 0)) }));
}

function sourceMode(source: JournalSource, overrides: Record<string, 'auto' | 'manual' | 'excluded'>): 'auto' | 'manual' | 'excluded' {
  return overrides[String(source.id)] || (source.excluded_from_journals ? 'excluded' : source.auto_sync_enabled ? 'auto' : 'manual');
}

function candidateGrade(candidate?: Candidate | null): number | null {
  const match = candidate?.class_name?.match(/\d+/);
  return match ? Number(match[0]) : null;
}

export function AdminJournals({ api, apiOrigin }: { api: ApiRequest; apiOrigin: string }) {
  const [sources, setSources] = useState<JournalSource[]>([]);
  const [canonicalGroups, setCanonicalGroups] = useState<CanonicalGroup[]>([]);
  const [knownSources, setKnownSources] = useState<KnownJournalSource[]>([]);
  const [schedulerConfigured, setSchedulerConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [selectedId, setSelectedId] = useState<string | number | null>(null);
  const [selectedGrade, setSelectedGrade] = useState(5);
  const [selectedTerm, setSelectedTerm] = useState<number | null>(null);
  const [detail, setDetail] = useState<JournalDetail | null>(null);
  const [syncHistory, setSyncHistory] = useState<JournalSyncRun[]>([]);
  const [groupMarker, setGroupMarker] = useState('');
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  const [connectionUrl, setConnectionUrl] = useState('');
  const [connectionGrade, setConnectionGrade] = useState(5);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [progress, setProgress] = useState<Record<string, { completed: number; total: number }>>({});
  const [matchingBook, setMatchingBook] = useState('');
  const [matching, setMatching] = useState<MatchingData | null>(null);
  const [matchingByBook, setMatchingByBook] = useState<Record<string, MatchingData>>({});
  const [matchingErrors, setMatchingErrors] = useState<Record<string, string>>({});
  const [matchingOpen, setMatchingOpen] = useState(false);
  const [matchSearch, setMatchSearch] = useState<Record<string, string>>({});
  const [matchOptions, setMatchOptions] = useState<Record<string, PersonOption[]>>({});
  const searchRequestIds = useRef<Record<string, number>>({});
  const previewRequestId = useRef(0);
  const [matchChoice, setMatchChoice] = useState<Record<string, string>>({});
  const [groupMapChoice, setGroupMapChoice] = useState<Record<string, string>>({});
  const [matchPreview, setMatchPreview] = useState<MatchPreview | null>(null);
  const [teamChoices, setTeamChoices] = useState<Record<string, string[]>>({});
  const [candidateQuery, setCandidateQuery] = useState('');
  const [candidateScope, setCandidateScope] = useState<'class' | 'school'>('class');
  const [candidateOptions, setCandidateOptions] = useState<Candidate[]>([]);
  const [candidateId, setCandidateId] = useState('');
  const [candidateSelection, setCandidateSelection] = useState<Candidate | null>(null);
  const [allowCrossGrade, setAllowCrossGrade] = useState(false);
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(-1);
  const [selectedAliases, setSelectedAliases] = useState<string[]>([]);
  const [batchPreview, setBatchPreview] = useState<MatchingBatchPreview | null>(null);
  const [createStudent, setCreateStudent] = useState({ surname: '', given_name: '', comment: '' });
  const [createPreview, setCreatePreview] = useState<MatchingBatchPreview | null>(null);
  const [sourceModes, setSourceModes] = useState<Record<string, 'auto' | 'manual' | 'excluded'>>({});

  const loadSources = useCallback(async () => {
    try {
      const result = await api<{ items: JournalSource[]; known_sources?: KnownJournalSource[]; scheduler_configured?: boolean }>('/api/admin/journals');
      setSources(result.items || []);
      setKnownSources(result.known_sources || []);
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
      setSelectedGrade(result.source.grade);
      setSelectedTerm(result.source.term_number ?? null);
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
    api<{ items: JournalSource[]; known_sources?: KnownJournalSource[]; scheduler_configured?: boolean }>('/api/admin/journals')
      .then((result) => {
        if (!active) return;
        setSources(result.items || []);
        setKnownSources(result.known_sources || []);
        const firstConnectedGrade = result.known_sources?.find((source) => source.connected)?.grade ?? result.items?.[0]?.grade;
        if (firstConnectedGrade) setSelectedGrade(firstConnectedGrade);
        setSchedulerConfigured(Boolean(result.scheduler_configured));
      })
      .catch((error: unknown) => { if (active) setLoadError(errorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);

  useEffect(() => {
    let active = true;
    api<{ items: CanonicalGroup[] }>('/api/admin/groups')
      .then((result) => { if (active) setCanonicalGroups(result.items || []); })
      .catch(() => { if (active) setCanonicalGroups([]); });
    return () => { active = false; };
  }, [api]);

  const books = useMemo(() => {
    const map = new Map<string, { id: string; title: string; grade: number; spreadsheetId: string; subjects: JournalSource[]; syncedAt?: string | null; error?: string | null; auto: boolean; refreshing: boolean }>();
    for (const source of sources) {
      const mode = sourceMode(source, sourceModes);
      if (mode === 'excluded') continue;
      const key = source.spreadsheet_id;
      const book = map.get(key) || { id: key, title: source.spreadsheet_title, grade: source.grade, spreadsheetId: key, subjects: [], auto: Boolean(source.auto_sync_enabled), refreshing: false };
      book.subjects.push(source);
      book.auto ||= Boolean(source.auto_sync_enabled);
      book.refreshing ||= mode === 'auto' && (Boolean(source.refreshing || source.active_run_id) || source.status === 'refreshing');
      if (!book.syncedAt || (source.last_synced_at && source.last_synced_at > book.syncedAt)) book.syncedAt = source.last_synced_at;
      if (mode === 'auto' && source.last_error) book.error = 'Часть предметов не обновилась. Предыдущие данные сохранены.';
      map.set(key, book);
    }
    return [...map.values()].sort((a, b) => a.grade - b.grade || a.title.localeCompare(b.title, 'ru'));
  }, [sources, sourceModes]);

  const selectedSource = sources.find((source) => String(source.id) === String(selectedId));
  const activeGroupMarker = groupMarker || detail?.groups.find((marker) => marker !== 'all') || detail?.groups[0] || '';
  const activeGroupMapping = detail?.mappings?.find((mapping) => mapping.group_marker === activeGroupMarker);
  const groupMapKey = detail && activeGroupMarker ? `${detail.source.id}:${activeGroupMarker}` : '';
  const selectedGroupMapId = groupMapKey ? groupMapChoice[groupMapKey] ?? String(activeGroupMapping?.group_id ?? '') : '';
  const gradeList = useMemo(() => knownSources.length ? knownSources.map((source) => source.grade).sort((a, b) => a - b) : [5, 6, 7, 8, 9, 10, 11], [knownSources]);
  const gradeBooks = books.filter((book) => book.grade === selectedGrade);
  const gradeJournalSources = sources.filter((source) => source.grade === selectedGrade);
  const activeGradeSources = gradeJournalSources.filter((source) => sourceMode(source, sourceModes) !== 'excluded');
  const detailSubjects = useMemo(() => detail ? journalSubjectGroups(sources.filter((source) => source.grade === detail.source.grade && source.spreadsheet_id === detail.source.spreadsheet_id && source.academic_year === detail.source.academic_year && sourceMode(source, sourceModes) !== 'excluded')) : [], [sources, sourceModes, detail]);
  const matchingBookEntry = books.find((book) => book.spreadsheetId === matchingBook);
  const crossGradeCandidate = candidateScope === 'school' && candidateGrade(candidateSelection) !== null && candidateGrade(candidateSelection) !== (matchingBookEntry?.grade || selectedGrade);
  const periodSources = useMemo(() => detail ? sources.filter((source) => source.spreadsheet_id === detail.source.spreadsheet_id && source.grade === detail.source.grade && source.academic_year === detail.source.academic_year && source.subject === detail.source.subject && sourceMode(source, sourceModes) !== 'excluded') : [], [sources, sourceModes, detail]);
  const selectedPeriodSource = periodSources.find((source) => source.term_number === selectedTerm);
  const viewingEmptyTerm = (selectedTerm !== null && !selectedPeriodSource) || detail?.source.period_status === 'unassigned';

  function summaryDisplay(student: JournalStudent, kind: string): string {
    const summary = student.summaries.find((item) => item.summary_type === kind);
    if (!summary) return '—';
    if (summary.text_value?.trim().startsWith('#')) return '—';
    if (kind === 'admission') {
      const value = String(summary.text_value ?? summary.numeric_value ?? '').trim().toLocaleLowerCase();
      if (['true', '1', 'да'].includes(value)) return 'Допущен';
      if (['false', '0', 'нет'].includes(value)) return 'Не допущен';
      return summary.text_value || '—';
    }
    if (summary.numeric_value !== null && summary.numeric_value !== undefined) {
      return kind === 'average_grade' ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(summary.numeric_value) : String(summary.numeric_value).replace('.', ',');
    }
    return summary.text_value || '—';
  }

  async function connect(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy('connect'); setActionError('');
    try {
      const result = await api<{ spreadsheet_id: string; auto_sync_enabled: boolean; scheduler_configured?: boolean; tabs?: Array<{ subject: string; status: string }> }>('/api/admin/journals/connect', {
        method: 'POST', body: JSON.stringify({ spreadsheet_url: connectionUrl, grade: connectionGrade, auto_sync_enabled: true }),
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

  async function connectKnownSource(source: KnownJournalSource) {
    setBusy(`connect:${source.grade}`); setActionError('');
    try {
      const result = await api<{ spreadsheet_id: string; auto_sync_enabled: boolean; scheduler_configured?: boolean }>('/api/admin/journals/connect', {
        method: 'POST', body: JSON.stringify({ grade: source.grade, spreadsheet_url: source.spreadsheet_id, auto_sync_enabled: true }),
      });
      const next = await loadSources();
      const connected = next.find((item) => item.spreadsheet_id === result.spreadsheet_id);
      if (connected) await loadDetail(connected.id);
      if (!result.auto_sync_enabled) setActionError(result.scheduler_configured === false
        ? 'Таблица подключена. Почасовое расписание ещё не настроено на сервере.'
        : 'Таблица подключена, но часть вкладок пока не обновилась.');
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

  async function saveGroupMapping(marker: string, groupId: string) {
    if (!detail || !marker || !groupId) return;
    setBusy('map-group'); setActionError('');
    try {
      await api('/api/admin/journals/group-mappings', {
        method: 'POST', body: JSON.stringify({ source_id: String(detail.source.id), group_marker: marker, group_id: groupId }),
      });
      await loadDetail(detail.source.id, marker);
    } catch (error) { setActionError(errorText(error)); }
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

  async function openMatching(spreadsheetId: string, force = false) {
    previewRequestId.current += 1;
    setMatchingBook(spreadsheetId); setMatching(null); setMatchPreview(null); setMatchSearch({}); setMatchOptions({}); setMatchChoice({}); setTeamChoices({}); searchRequestIds.current = {}; setActionError(''); setBusy('matching');
    setCandidateQuery(''); setCandidateId(''); setCandidateSelection(null); setCandidateOptions([]); setActiveCandidateIndex(-1); setCandidateScope('class'); setAllowCrossGrade(false); setSelectedAliases([]); setBatchPreview(null); setCreatePreview(null);
    const cached = matchingByBook[spreadsheetId];
    if (cached && !force) { setMatching(cached); setBusy(''); return; }
    try {
      const result = await api<MatchingData>(`/api/admin/journals/matching?spreadsheet_id=${encodeURIComponent(spreadsheetId)}`);
      setMatching(result); setMatchingByBook((current) => ({ ...current, [spreadsheetId]: result }));
      setMatchingErrors((current) => { const next = { ...current }; delete next[spreadsheetId]; return next; });
    }
    catch (error) {
      const errorMessage = errorText(error);
      setActionError(errorMessage);
      setMatchingErrors({ ...matchingErrors, [spreadsheetId]: errorMessage });
    }
    finally { setBusy(''); }
  }

  async function loadAllMatching() {
    if (!books.length) return;
    setBusy('matching-all');
    const results: Array<{ id: string; ok: true; data: MatchingData } | { id: string; ok: false; error: string }> = await Promise.all(books.map(async (book) => {
      try { return { id: book.spreadsheetId, ok: true as const, data: await api<MatchingData>(`/api/admin/journals/matching?spreadsheet_id=${encodeURIComponent(book.spreadsheetId)}`) }; }
      catch (error) { return { id: book.spreadsheetId, ok: false as const, error: errorText(error) }; }
    }));
    setMatchingByBook((current) => {
      const next = { ...current };
      for (const result of results) if (result.ok) next[result.id] = result.data;
      return next;
    });
    setMatchingErrors((current) => {
      const next = { ...current };
      for (const result of results) {
        if (result.ok) delete next[result.id];
        else next[result.id] = result.error;
      }
      return next;
    });
    setBusy('');
  }

  async function searchMatchPeople(key: string, query: string) {
    previewRequestId.current += 1;
    setMatchSearch((current) => ({ ...current, [key]: query })); setMatchPreview(null);
    const requestId = (searchRequestIds.current[key] || 0) + 1;
    searchRequestIds.current[key] = requestId;
    if (!key.startsWith('team:')) setMatchOptions((current) => ({ ...current, [key]: [] }));
    if (query.trim().length < 2) return;
    try { const className = matchingBookEntry ? `&class_name=${encodeURIComponent(`${matchingBookEntry.grade}`)}` : ''; const result = await api<PersonResults>(`/api/admin/people?kind=student&q=${encodeURIComponent(query)}${className}`); if (searchRequestIds.current[key] === requestId) setMatchOptions((current) => ({ ...current, [key]: key.startsWith('team:') ? [...(current[key] || []), ...(result.items || [])].filter((person, index, all) => all.findIndex((candidate) => String(candidate.id) === String(person.id)) === index) : result.items || [] })); }
    catch (error) { if (searchRequestIds.current[key] === requestId) setActionError(errorText(error)); }
  }

  useEffect(() => {
    if (!matchingBook || candidateId) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const query = new URLSearchParams({ grade: String(matchingBookEntry?.grade || selectedGrade), scope: candidateScope, q: candidateQuery.trim() });
        const result = await api<{ items?: Candidate[] }>(`/api/admin/journals/matching/candidates?${query}`);
        if (!cancelled) setCandidateOptions(result.items || []);
      } catch (error) { if (!cancelled) setActionError(errorText(error)); }
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [api, candidateId, candidateQuery, candidateScope, matchingBook, matchingBookEntry?.grade, selectedGrade]);

  useEffect(() => {
    if (activeCandidateIndex < 0) return;
    document.getElementById(`journal-candidate-${activeCandidateIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [activeCandidateIndex]);

  async function previewBatch() {
    if (!matchingBook || !candidateId || !selectedAliases.length || (crossGradeCandidate && !allowCrossGrade)) return;
    setBusy('batch-preview'); setActionError('');
    try { setBatchPreview(await api<MatchingBatchPreview>('/api/admin/journals/matching/batch-preview', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_names: selectedAliases, identity_id: candidateId, allow_cross_grade: allowCrossGrade }) })); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function confirmBatch() {
    if (!batchPreview?.preview_token || batchPreview.can_confirm === false || (crossGradeCandidate && !allowCrossGrade)) return;
    setBusy('batch-confirm'); setActionError('');
    try { await api('/api/admin/journals/matching/batch-confirm', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_names: selectedAliases, identity_id: candidateId, preview_token: batchPreview.preview_token, allow_cross_grade: allowCrossGrade }) }); setBatchPreview(null); setSelectedAliases([]); await openMatching(matchingBook, true); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function previewCreateStudent() {
    if (!matchingBook || !selectedAliases.length || !createStudent.surname.trim() || !createStudent.given_name.trim() || !matchingBookEntry) return;
    setBusy('create-preview'); setActionError('');
    try { setCreatePreview(await api<MatchingBatchPreview>('/api/admin/journals/matching/create-preview', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_names: selectedAliases, surname: createStudent.surname.trim(), given_name: createStudent.given_name.trim(), grade: matchingBookEntry.grade, comment: createStudent.comment.trim() }) })); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function confirmCreateStudent() {
    if (!createPreview?.preview_token || createPreview.can_confirm === false || !matchingBookEntry) return;
    setBusy('create-confirm'); setActionError('');
    try { await api('/api/admin/journals/matching/create-confirm', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_names: selectedAliases, surname: createStudent.surname.trim(), given_name: createStudent.given_name.trim(), grade: matchingBookEntry.grade, comment: createStudent.comment.trim(), preview_token: createPreview.preview_token }) }); setCreatePreview(null); setSelectedAliases([]); setCreateStudent({ surname: '', given_name: '', comment: '' }); await openMatching(matchingBook, true); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function setSourceMode(source: JournalSource, mode: 'auto' | 'manual' | 'excluded') {
    setBusy(`source:${source.id}`); setActionError('');
    try { await api('/api/admin/journals/source-settings', { method: 'POST', body: JSON.stringify({ source_id: source.id, mode }) }); setSourceModes((current) => ({ ...current, [String(source.id)]: mode })); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function refreshSource(source: JournalSource) {
    setBusy(`source-refresh:${source.id}`); setActionError('');
    try { await api('/api/admin/journals/source-refresh', { method: 'POST', body: JSON.stringify({ source_id: source.id }) }); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function previewMatch(sourceName: string, replaceExisting = false) {
    const identityId = matchChoice[sourceName]; if (!identityId) return;
    const requestId = ++previewRequestId.current;
    setBusy(`preview:${sourceName}`); setActionError(''); setMatchPreview(null);
    try { const preview = await api<MatchPreview>('/api/admin/journals/matching/preview', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_name: sourceName, identity_id: identityId, ...(replaceExisting ? { replace_existing: true } : {}) }) }); if (previewRequestId.current === requestId) setMatchPreview({ ...preview, replace_existing: replaceExisting }); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function confirmMatch() {
    if (!matchPreview) return;
    setBusy('confirm-match'); setActionError('');
    try {
      const result = await api<{ matched_records: number; updated_results: number }>('/api/admin/journals/matching/confirm', { method: 'POST', body: JSON.stringify({ spreadsheet_id: matchingBook, source_name: matchPreview.source_name, identity_id: matchPreview.identity.id, preview_token: matchPreview.preview_token, ...(matchPreview.replace_existing ? { replace_existing: true } : {}) }) });
      setMatchPreview(null); await openMatching(matchingBook, true); if (selectedId) await loadDetail(selectedId, groupMarker); await loadSources();
      setActionError(`Связь ${matchPreview.replace_existing ? 'исправлена' : 'сохранена'}: ${result.matched_records} записей, обновлено результатов: ${result.updated_results}.`);
    } catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  async function saveTeamMembers(teamId: string | number) {
    const ids = teamChoices[String(teamId)] || matching?.teams.find((team) => String(team.id) === String(teamId))?.members.map((member) => String(member.identity_id)) || [];
    setBusy(`team:${teamId}`); setActionError('');
    try { await api(`/api/admin/journals/teams/${teamId}/members`, { method: 'PUT', body: JSON.stringify({ identity_ids: ids }) }); await openMatching(matchingBook, true); if (selectedId) await loadDetail(selectedId, groupMarker); await loadSources(); }
    catch (error) { setActionError(errorText(error)); }
    finally { setBusy(''); }
  }

  function resultTable(rows: JournalStudent[], teams = false) {
    const core = [{ kind: 'average_grade', label: 'Средняя' }, { kind: 'admission', label: 'Допуск' }, { kind: 'term_grade', label: 'Итоговая' }];
    return <section className="admin-panel admin-table-panel journal-table-panel"><div className="admin-table-meta"><span>{rows.length} {teams ? 'команд' : 'учеников'} · {detail?.assessments.length || 0} работ</span><span>Ноль — оценка, «н» — отметка о пропуске</span></div><div className="admin-table-wrap journal-table-wrap"><table className="admin-table journal-table"><thead><tr><th className="journal-student-column">{teams ? 'Команда' : 'Ученик'}</th>{detail?.assessments.map((assessment) => <th className="journal-assessment-column" key={assessment.id}><strong>{assessment.title}</strong><small>{assessmentDateText(assessment.assessed_on)}</small><small>вес {assessment.weight ?? '—'} · макс. {assessment.max_score ?? '—'}</small></th>)}{core.map((item) => <th className="journal-summary-column" key={item.kind}>{item.label}</th>)}</tr></thead><tbody>{rows.map((student) => <tr key={student.id}><th className="journal-student-column"><strong>{student.display_name}</strong><small>{teams ? `Участники: ${(student.members || []).map((member) => member.display_name).join(', ') || 'не указаны'}` : <>{student.group_marker === 'all' ? `${detail?.source.grade || ''} класс` : `группа ${student.group_marker}`}{student.match_status === 'linked' ? '' : ' · не сопоставлен'}</>}</small>{student.summaries.some((item) => !['average_grade','admission','term_grade'].includes(item.summary_type || '')) ? <details className="journal-match"><summary>Технические показатели</summary><small>{student.summaries.filter((item) => !['average_grade','admission','term_grade'].includes(item.summary_type || '')).map((item) => `${item.label}: ${item.numeric_value ?? item.text_value ?? '—'}`).join(' · ')}</small></details> : null}{!teams && student.match_status !== 'linked' ? <details className="journal-match"><summary>Не сопоставлен</summary><div><button className="admin-link" onClick={() => { setDetail(null); setSelectedId(null); setMatchingOpen(true); if (detail?.source.spreadsheet_id) void openMatching(detail.source.spreadsheet_id); }}><Search size={13} /> Открыть панель сопоставлений</button></div></details> : null}</th>{detail?.assessments.map((assessment) => <td className="journal-score-cell" key={assessment.id}>{scoreText(student.results[String(assessment.id)] || null)}</td>)}{core.map((item) => <td className="journal-score-cell journal-score-cell--summary" key={item.kind}>{summaryDisplay(student, item.kind)}</td>)}</tr>)}</tbody></table></div></section>;
  }

  if (loading) return <div className="admin-loading"><LoaderCircle size={17} className="admin-spin" /> Загружаем журналы…</div>;

  return <div className="admin-stack journal-workspace">
    <GoogleConnectionCard api={api} apiOrigin={apiOrigin} />
    {actionError ? <div className="admin-error" role="alert"><AlertTriangle size={17} /><div><strong>Синхронизация журнала</strong><p>{actionError}</p></div><button className="admin-link" onClick={() => setActionError('')}>Закрыть</button></div> : null}
    {loadError ? <div className="admin-error" role="alert"><AlertTriangle size={17} /><div><strong>Не удалось загрузить список</strong><p>{loadError}</p></div><button className="admin-button admin-button--quiet" onClick={() => { setLoading(true); setLoadError(''); void loadSources(); }}>Повторить</button></div> : null}

    {!detail ? <>
      <div className="admin-page-actions journal-intro"><p>Просмотр оценок по классам, предметам и триместрам. Изменения в этой панели не попадают в оригинал.{!schedulerConfigured ? <><br /><span className="admin-help-text">Почасовое обновление будет доступно после настройки защищённого серверного расписания.</span></> : null}</p><div className="admin-button-row"><button className={`admin-button ${matchingOpen ? 'admin-button--primary' : 'admin-button--quiet'}`} aria-pressed={matchingOpen} onClick={() => { const next = !matchingOpen; setMatchingOpen(next); if (next) { setActionError(''); void loadAllMatching(); } }}><Search size={15} /> Journals → Matching</button><button className="admin-button admin-button--primary" onClick={() => { setActionError(''); setConnectionOpen((value) => !value); }}><BookOpen size={15} /> Подключить Google Таблицу</button></div></div>
      {connectionOpen ? <section className="admin-panel journal-connect-panel"><div><h2>Подключить Google Таблицу</h2><p className="admin-help-text">Вставьте ссылку на таблицу и выберите класс. Будут импортированы предметные вкладки; оценки останутся доступны только администраторам.</p></div><form onSubmit={(event) => void connect(event)}><label htmlFor="journal-grade-select">Класс</label><select id="journal-grade-select" value={connectionGrade} onChange={(event) => setConnectionGrade(Number(event.target.value))}>{gradeList.map((grade) => <option value={grade} key={grade}>{grade} класс</option>)}</select><label htmlFor="journal-source-url">Ссылка на таблицу</label><input id="journal-source-url" value={connectionUrl} onChange={(event) => setConnectionUrl(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" required /><div className="admin-button-row"><button className="admin-button admin-button--primary" disabled={busy === 'connect'}>{busy === 'connect' ? <LoaderCircle size={15} className="admin-spin" /> : null} Подключить и импортировать</button><button type="button" className="admin-button admin-button--quiet" onClick={() => setConnectionOpen(false)}>Отмена</button></div></form></section> : null}
      <div className="journal-grade-tabs" role="tablist" aria-label="Класс">{gradeList.map((grade) => { const connected = knownSources.find((item) => item.grade === grade)?.connected ?? books.some((book) => book.grade === grade); const count = books.filter((book) => book.grade === grade).length; return <button key={grade} role="tab" aria-selected={selectedGrade === grade} className={selectedGrade === grade ? 'is-active' : ''} onClick={() => { setSelectedGrade(grade); setActionError(''); }}>{grade} класс<span>{count ? `${count} журналов` : connected ? 'нет активных вкладок' : 'не подключён'}</span></button>; })}</div>
      {matchingOpen ? <section className="admin-panel journal-central-matching"><div className="journal-central-matching-heading"><div><p className="admin-eyebrow">ЖУРНАЛЫ · MATCHING</p><h2>Сопоставление учеников и команд</h2><p className="admin-help-text">Записи сгруппированы по имени внутри каждой таблицы и охватывают все её предметы. Данные таблиц загружаются при открытии этого раздела.</p>{Object.keys(matchingByBook).length ? <p className="admin-help-text">Загружено таблиц: {Object.keys(matchingByBook).length} · ожидают: {Object.values(matchingByBook).reduce((sum, data) => sum + data.individuals.length, 0)} имён · {Object.values(matchingByBook).reduce((sum, data) => sum + data.teams.length, 0)} команд</p> : null}</div><button className="admin-button admin-button--quiet" onClick={() => { setMatchingOpen(false); setMatching(null); setMatchingBook(''); }}>Закрыть</button></div>{busy === 'matching-all' ? <div className="admin-loading"><LoaderCircle size={16} className="admin-spin" /> Загружаем сопоставления по таблицам…</div> : null}<div className="journal-matching-books">{books.map((book) => { const data = matchingByBook[book.spreadsheetId]; return <button key={book.spreadsheetId} disabled={busy === 'matching-all'} className={`journal-matching-book ${matchingBook === book.spreadsheetId ? 'is-active' : ''}`} onClick={() => { setSelectedGrade(book.grade); void openMatching(book.spreadsheetId); }}><strong>{book.grade} класс · {book.title}</strong><span>{data ? `${data.individuals.length} имён ожидают · ${data.teams.length} команд` : matchingErrors[book.spreadsheetId] ? 'Ошибка загрузки' : 'Загрузка…'}</span></button>; })}</div>
        {matchingBook && !matching && busy === 'matching' ? <div className="admin-loading"><LoaderCircle size={16} className="admin-spin" /> Загружаем сопоставления…</div> : null}
        {matchingBook && matchingErrors[matchingBook] && !matching ? <div className="journal-source-error" role="alert"><AlertTriangle size={14} /><span>{matchingErrors[matchingBook]}</span><button className="admin-link" onClick={() => void openMatching(matchingBook)}>Повторить</button></div> : null}
        {matchingBook && matching ? <div className="journal-matching-panel" aria-label={`Сопоставление: ${matchingBookEntry?.grade || ''} класс`}><p className="admin-help-text">Таблица: {matchingBookEntry?.title || matchingBook}</p>
          <section className="journal-batch-tools" aria-labelledby="journal-batch-title"><h3 id="journal-batch-title">Быстрое сопоставление</h3><p className="admin-help-text">Отметьте варианты имени из этой таблицы, найдите ученика и проверьте список затронутых записей перед сохранением.</p>
            <fieldset className="journal-alias-list"><legend>Имена из таблицы</legend>{matching.individuals.map((item, index) => { const checkboxId = `journal-alias-${index}`; return <label aria-label={item.source_name} htmlFor={checkboxId} key={`alias:${item.source_name}`}><input id={checkboxId} type="checkbox" checked={selectedAliases.includes(item.source_name)} onChange={(event) => { setBatchPreview(null); setCreatePreview(null); setSelectedAliases((values) => event.target.checked ? [...values, item.source_name] : values.filter((name) => name !== item.source_name)); }} /><span><strong>{item.source_name}</strong><small>{item.record_count} записей · {item.subject_count} предмета</small></span></label>; })}</fieldset>
            <div className="journal-candidate-picker"><fieldset className="journal-scope-tabs"><legend>Область поиска учеников</legend><button type="button" className={candidateScope === 'class' ? 'is-active' : ''} aria-pressed={candidateScope === 'class'} onClick={() => { setCandidateScope('class'); setCandidateId(''); setCandidateSelection(null); setCandidateQuery(''); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); setBatchPreview(null); }}>Этот класс · {matchingBookEntry?.grade || selectedGrade}</button><button type="button" className={candidateScope === 'school' ? 'is-active' : ''} aria-pressed={candidateScope === 'school'} onClick={() => { setCandidateScope('school'); setCandidateId(''); setCandidateSelection(null); setCandidateQuery(''); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); setBatchPreview(null); }}>Вся школа</button></fieldset>
              <label className="journal-combobox-label" htmlFor="journal-candidate-search">Найти ученика<input id="journal-candidate-search" role="combobox" aria-autocomplete="list" aria-expanded={candidateOptions.length > 0} aria-controls="journal-candidate-options" aria-activedescendant={activeCandidateIndex >= 0 ? `journal-candidate-${activeCandidateIndex}` : undefined} value={candidateQuery} onKeyDown={(event) => { if (event.key === 'ArrowDown' && candidateOptions.length) { event.preventDefault(); setActiveCandidateIndex((index) => (index + 1) % candidateOptions.length); } else if (event.key === 'ArrowUp' && candidateOptions.length) { event.preventDefault(); setActiveCandidateIndex((index) => index <= 0 ? candidateOptions.length - 1 : index - 1); } else if (event.key === 'Enter' && activeCandidateIndex >= 0) { event.preventDefault(); const person = candidateOptions[activeCandidateIndex]; if (person) { setCandidateId(String(person.id)); setCandidateSelection(person); setCandidateQuery(`${person.display_name} · ${person.class_name || 'класс не указан'}`); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); } } else if (event.key === 'Escape') setCandidateOptions([]); }} onChange={(event) => { setCandidateQuery(event.target.value); setCandidateId(''); setCandidateSelection(null); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); setBatchPreview(null); setCreatePreview(null); }} placeholder="Введите фамилию или имя" /></label>
              {candidateOptions.length ? (
                /* eslint-disable-next-line jsx-a11y/prefer-tag-over-role, jsx-a11y/no-noninteractive-element-to-interactive-role -- Custom searchable combobox needs an ARIA listbox; native select cannot filter by this search field. */
                <ul id="journal-candidate-options" className="journal-candidate-options" role="listbox" aria-label="Найденные ученики">{candidateOptions.map((person, index) => (
                  /* eslint-disable-next-line jsx-a11y/prefer-tag-over-role, jsx-a11y/no-noninteractive-element-to-interactive-role -- Active descendants are custom selectable options; native option requires a select parent. */
                  <li id={`journal-candidate-${index}`} role="option" aria-selected={activeCandidateIndex === index} tabIndex={-1} key={person.id} onMouseEnter={() => setActiveCandidateIndex(index)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setCandidateId(String(person.id)); setCandidateSelection(person); setCandidateQuery(`${person.display_name} · ${person.class_name || 'класс не указан'}`); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); } }} onClick={() => { setCandidateId(String(person.id)); setCandidateSelection(person); setCandidateQuery(`${person.display_name} · ${person.class_name || 'класс не указан'}`); setCandidateOptions([]); setActiveCandidateIndex(-1); setAllowCrossGrade(false); }}><strong>{person.display_name}</strong><small>{person.class_name || 'Класс не указан'}</small></li>
                ))}</ul>
              ) : null}
              {crossGradeCandidate ? <label className="journal-cross-grade-confirm"><input type="checkbox" checked={allowCrossGrade} onChange={(event) => { setAllowCrossGrade(event.target.checked); setBatchPreview(null); }} /><span>Подтверждаю другой класс ученика; состав классов не изменится</span></label> : null}
              <button className="admin-button admin-button--quiet" disabled={!selectedAliases.length || !candidateId || (crossGradeCandidate && !allowCrossGrade) || Boolean(busy)} onClick={() => void previewBatch()}>Проверить сопоставление</button>
            </div>
            {batchPreview ? <output className="journal-match-review" aria-live="polite"><span><strong>Предпросмотр сопоставления</strong><span className="journal-preview-copy">{selectedAliases.length} имён → {batchPreview.identity?.display_name || candidateSelection?.display_name || candidateQuery}. {batchPreview.items?.map((item) => `${item.source_name}: ${item.record_count ?? 0} записей, ${item.result_count ?? 0} результатов, ${item.subject_count ?? 0} предметов`).join('; ') || `Затронутые записи: ${batchPreview.affected_count ?? batchPreview.items?.reduce((total, item) => total + (item.record_count || 0), 0) ?? 0}.`}</span>{batchPreview.cross_grade ? <small className="journal-duplicate-warning">Ученик относится к другому классу. Связь затронет только выбранные журнальные записи; состав классов останется без изменений.</small> : null}{batchPreview.duplicates?.length ? <small className="journal-duplicate-warning">Есть возможные совпадения: {batchPreview.duplicates.map((item) => `${item.display_name} · ${item.class_name || 'класс не указан'}`).join('; ')}</small> : null}</span><button className="admin-button admin-button--primary" disabled={Boolean(busy) || batchPreview.can_confirm === false || !batchPreview.preview_token || (crossGradeCandidate && !allowCrossGrade)} onClick={() => void confirmBatch()}>{busy === 'batch-confirm' ? 'Сохраняем…' : 'Подтвердить сопоставление'}</button></output> : null}
            <details className="journal-create-student"><summary>Создать ученика, которого нет в справочнике</summary><div className="journal-create-fields"><label>Фамилия<input value={createStudent.surname} onChange={(event) => { setCreateStudent((current) => ({ ...current, surname: event.target.value })); setCreatePreview(null); }} /></label><label>Имя<input value={createStudent.given_name} onChange={(event) => { setCreateStudent((current) => ({ ...current, given_name: event.target.value })); setCreatePreview(null); }} /></label><label>Комментарий<input value={createStudent.comment} onChange={(event) => { setCreateStudent((current) => ({ ...current, comment: event.target.value })); setCreatePreview(null); }} /></label><button className="admin-button admin-button--quiet" disabled={!selectedAliases.length || Boolean(busy)} onClick={() => void previewCreateStudent()}>Предпросмотр создания</button></div>{createPreview ? <output className="journal-match-review" aria-live="polite"><span><strong>Предпросмотр нового ученика · класс {matchingBookEntry?.grade}</strong><span className="journal-preview-copy">{createStudent.surname} {createStudent.given_name} · {createPreview.items?.map((item) => `${item.source_name}: ${item.record_count ?? 0} записей, ${item.result_count ?? 0} результатов, ${item.subject_count ?? 0} предметов`).join('; ') || `${selectedAliases.length} вариантов имени`}</span>{createPreview.duplicates?.length ? <small className="journal-duplicate-warning">Похожая запись найдена: {createPreview.duplicates.map((item) => `${item.display_name} · ${item.class_name || 'класс не указан'}`).join('; ')}. Выберите существующего ученика выше.</small> : null}</span><button className="admin-button admin-button--primary" disabled={Boolean(busy) || createPreview.can_confirm === false || !createPreview.preview_token} onClick={() => void confirmCreateStudent()}>{busy === 'create-confirm' ? 'Создаём…' : 'Подтвердить создание'}</button></output> : null}</details>
          </section>
          <h3>Индивидуальные записи</h3><p className="admin-help-text">Ожидают подтверждения: {matching.individuals.length} имён / {matching.individuals.reduce((total, item) => total + item.record_count, 0)} записей. Предложение само по себе ничего не связывает.</p>
          {!matching.individuals.length ? <p className="admin-help-text">Неразобранных индивидуальных записей нет.</p> : matching.individuals.map((item) => <div className="journal-mapping-row" key={`${matchingBook}:${item.source_name}`}><div><strong>{item.source_name}</strong><small>{item.subject_count} предмета · {item.record_count} записей · {item.result_count} результатов</small>{item.suggestion ? <small>Возможный вариант: {item.suggestion.display_name} · {item.suggestion.class_name || 'класс не указан'} · {item.suggestion.reason}</small> : null}</div><label className="journal-alias-single"><input type="checkbox" checked={selectedAliases.includes(item.source_name)} onChange={(event) => { setBatchPreview(null); setCreatePreview(null); setSelectedAliases((values) => event.target.checked ? [...values, item.source_name] : values.filter((name) => name !== item.source_name)); }} /><span>{selectedAliases.includes(item.source_name) ? 'Добавлено в пакетное сопоставление' : 'Выбрать для пакетного сопоставления'}</span></label></div>)}
          {matchPreview && !matchPreview.replace_existing ? <div className="journal-match-review"><div><strong>Проверьте сопоставление</strong><p>{matchPreview.source_name} → {matchPreview.identity.display_name} · {matchPreview.subject_count} предмета · {matchPreview.record_count} записей · {matchPreview.result_count} результатов</p><small>{matchPreview.identity.class_name || 'класс не указан'} · {matchPreview.identity.status}. Группы: {matchPreview.identity.groups.map((group) => group.name).join(', ') || 'не указаны'}. {matchPreview.replace_existing ? 'Подтверждение заменит текущую связь.' : 'Подтверждение свяжет записи таблицы с этим учеником.'}</small></div><button className="admin-button admin-button--primary" disabled={busy === 'confirm-match'} onClick={() => void confirmMatch()}>{busy === 'confirm-match' ? 'Сохраняем…' : (matchPreview.replace_existing ? 'Подтвердить замену связи' : 'Подтвердить сопоставление')}</button></div> : null}
          <details className="journal-linked-matches"><summary>Подтверждённые связи · исправить связь ({matching.linked?.length || 0})</summary><p className="admin-help-text">Выберите ученика и проверьте предпросмотр. Замена затронет только выбранное имя и его записи в этой таблице.</p>{!matching.linked?.length ? <p className="admin-help-text">Подтверждённых связей пока нет.</p> : matching.linked.map((item) => <div className="journal-mapping-row" key={`${matchingBook}:linked:${item.source_name}`}><div><strong>{item.source_name}</strong><small>Сейчас связано с: {item.identity_name} · {item.class_name || 'класс не указан'}</small><small>{item.subject_count} предмета · {item.record_count} записей · {item.result_count} результатов</small></div><div className="journal-map-controls"><input aria-label={`Поиск нового ученика для ${item.source_name}`} value={matchSearch[item.source_name] || ''} onChange={(event) => void searchMatchPeople(item.source_name, event.target.value)} placeholder="Найти нового ученика" /><select aria-label={`Выбрать нового ученика для ${item.source_name}`} value={matchChoice[item.source_name] || ''} onChange={(event) => { previewRequestId.current += 1; setMatchPreview(null); setMatchChoice((current) => ({ ...current, [item.source_name]: event.target.value })); }}><option value="">Выберите нового ученика</option>{(item.suggestions || []).filter((person) => String(person.identity_id) !== String(item.identity_id)).map((person) => <option key={`suggestion:${person.identity_id}`} value={person.identity_id}>{person.display_name} · {person.class_name || 'класс не указан'}</option>)}{(matchOptions[item.source_name] || []).filter((person) => String(person.id) !== String(item.identity_id)).map((person) => <option key={`person:${person.id}`} value={person.id}>{person.display_name} · {person.class_name || 'класс не указан'}</option>)}</select><button className="admin-button admin-button--quiet" disabled={!matchChoice[item.source_name] || String(matchChoice[item.source_name]) === String(item.identity_id) || Boolean(busy)} onClick={() => void previewMatch(item.source_name, true)}>Предпросмотр замены</button></div></div>)}{matchPreview?.replace_existing ? <div className="journal-match-review"><div><strong>Проверьте замену связи</strong><p>{matchPreview.source_name} → {matchPreview.identity.display_name} · {matchPreview.subject_count} предмета · {matchPreview.record_count} записей · {matchPreview.result_count} результатов</p><small>{matchPreview.identity.class_name || 'класс не указан'} · {matchPreview.identity.status}. Группы: {matchPreview.identity.groups.map((group) => group.name).join(', ') || 'не указаны'}. Подтверждение заменит текущую связь.</small></div><button className="admin-button admin-button--primary" disabled={busy === 'confirm-match'} onClick={() => void confirmMatch()}>{busy === 'confirm-match' ? 'Сохраняем…' : 'Подтвердить замену связи'}</button></div> : null}</details>
          <h3>Команды</h3>{!matching.teams.length ? <p className="admin-help-text">Командных записей нет.</p> : matching.teams.map((team) => { const selected = teamChoices[String(team.id)] || team.members.map((member) => String(member.identity_id)); return <div className="journal-mapping-row" key={`${matchingBook}:${team.id}`}><div><strong>{team.display_name}</strong><small>{team.subject} · группа {team.group_marker} · {team.result_count} результатов</small><small>Участники: {team.members.map((member) => member.display_name).join(', ') || 'не указаны'}</small></div><div className="journal-map-controls"><label>Найти участников<input value={matchSearch[`team:${team.id}`] || ''} onChange={(event) => void searchMatchPeople(`team:${team.id}`, event.target.value)} placeholder="Введите имя" /></label><label>Участники<select multiple aria-label={`Участники команды ${team.display_name}`} value={selected} onChange={(event) => setTeamChoices((current) => ({ ...current, [String(team.id)]: Array.from(event.target.selectedOptions, (option) => option.value) }))}>{[...team.members.map((member) => ({ id: member.identity_id, display_name: member.display_name, class_name: member.class_name })), ...(matchOptions[`team:${team.id}`] || [])].filter((person, index, all) => all.findIndex((candidate) => String(candidate.id) === String(person.id)) === index).map((person) => <option key={person.id} value={String(person.id)}>{person.display_name} · {person.class_name || 'класс не указан'}</option>)}</select></label><button className="admin-button admin-button--quiet" onClick={() => void saveTeamMembers(team.id)} disabled={Boolean(busy)}>Сохранить состав</button></div></div>; })}
        </div> : null}
      </section> : null}
      {!gradeBooks.length ? <section className="admin-panel journal-empty"><div className="journal-empty-mark"><BookOpen size={23} /></div><h2>{selectedGrade} класс</h2><p>{gradeJournalSources.length && !activeGradeSources.length ? 'Все вкладки этого класса исключены из списка журналов. Их можно включить в настройках источников ниже.' : knownSources.find((source) => source.grade === selectedGrade)?.connected ? 'Таблица подключена, но импортированные предметы пока не появились.' : 'Для этого класса журнал ещё не подключён.'}</p>{gradeJournalSources.length && !activeGradeSources.length ? null : knownSources.find((source) => source.grade === selectedGrade) ? <button className="admin-button admin-button--primary" disabled={Boolean(busy)} onClick={() => void connectKnownSource(knownSources.find((source) => source.grade === selectedGrade)!)}>{busy === `connect:${selectedGrade}` ? <LoaderCircle size={15} className="admin-spin" /> : null}{knownSources.find((source) => source.grade === selectedGrade)?.connected ? 'Повторить подключение' : `Подключить журнал ${selectedGrade} класса`}</button> : <button className="admin-button admin-button--primary" onClick={() => { setConnectionGrade(selectedGrade); setConnectionOpen(true); }}>Подключить таблицу</button>}</section> : gradeBooks.map((book) => <section className="admin-panel journal-book" key={book.spreadsheetId}>
        <div className="journal-book-heading"><div><p className="admin-eyebrow">УЧЕБНЫЙ ГОД 2026/27</p><h2>{book.grade} класс</h2><p>{book.title} <a href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(book.spreadsheetId)}/edit`} target="_blank" rel="noreferrer" aria-label="Открыть оригинальную таблицу"><ExternalLink size={13} /></a></p></div><div className="journal-book-actions"><span className={`journal-sync-status ${book.refreshing ? 'is-running' : book.error ? 'is-error' : 'is-ready'}`}><i />{book.refreshing ? (progress[book.spreadsheetId]?.total ? `Обновляется ${progress[book.spreadsheetId].completed} из ${progress[book.spreadsheetId].total}` : 'Обновляется') : book.error ? 'Есть ошибка' : `Обновлено ${dateText(book.syncedAt)}`}</span><button className="admin-button admin-button--quiet" disabled={Boolean(busy) || book.refreshing} onClick={() => void refreshBook(book.spreadsheetId, book.grade)}><RefreshCw size={15} className={busy.startsWith('refresh:') ? 'admin-spin' : ''} /> {busy === `refresh:${book.spreadsheetId}` ? 'Обновляем…' : 'Обновить сейчас'}</button><label className="journal-toggle"><input type="checkbox" checked={book.auto} disabled={Boolean(busy) || !schedulerConfigured} onChange={(event) => void setAutomation(book.spreadsheetId, event.target.checked)} /><span>{schedulerConfigured ? 'Автоматически каждый час' : 'Расписание не настроено'}</span></label></div></div>
        {book.subjects.filter((source) => sourceMode(source, sourceModes) === 'auto' && source.last_error).map((source) => <div className="journal-source-error" key={`error:${source.id}`}><AlertTriangle size={14} /><span>Вкладка «{source.sheet_title}»: {source.last_error || book.error || 'Не удалось обновить. Предыдущие данные сохранены.'}</span></div>)}
        <div className="journal-subject-heading"><strong>Предметы</strong><span>{journalSubjectGroups(book.subjects).length}</span></div><div className="journal-subject-grid">{journalSubjectGroups(book.subjects).map(({ subject, periods }) => { const source = periods.find((item) => item.period_status === 'current') || periods.find((item) => item.period_status === 'unassigned') || periods[periods.length - 1]; const terms = periods.filter((item) => item.term_number).map((item) => ['I', 'II', 'III'][Number(item.term_number) - 1]).join(' · '); const mode = sourceMode(source, sourceModes); const hasError = mode === 'auto' && Boolean(source.last_error || source.status === 'error'); return <button key={subject} className="journal-subject-card" onClick={() => { setActionError(''); void loadDetail(source.id); }}><span className="journal-subject-icon"><BookOpen size={15} /></span><span className="journal-subject-copy"><strong>{subject}</strong><small>{terms ? `${terms} триместр${periods.some((item) => item.period_status === 'current') ? ' · текущий открыт' : ''} · ` : ''}{source.assessment_count || 0} работ · {source.roster_student_count || 0} учеников{source.team_count ? ` · ${source.team_count} команд` : ''}{mode === 'manual' ? ' · обновление вручную' : ''}</small></span><span className={`journal-subject-state ${hasError ? 'is-error' : ''}`} title={mode === 'manual' ? 'Синхронизация по расписанию пропускает эту вкладку; её можно обновить вручную.' : source.status === 'empty' ? 'Вкладка без оценок' : hasError ? 'Не удалось обновить вкладку. Предыдущие данные сохранены.' : 'Обновлено'}>{hasError ? <AlertTriangle size={15} /> : mode === 'manual' ? <RefreshCw size={15} /> : <CheckCircle2 size={15} />}</span></button>; })}</div>
      </section>)}
      <details className="admin-panel journal-tab-settings"><summary>Настройки вкладок · {selectedGrade} класс</summary><p className="admin-help-text">Исключённые вкладки не входят в список журналов и не считаются ошибками обновления.</p>{gradeJournalSources.map((source) => { const mode = sourceMode(source, sourceModes); const statusText = mode === 'excluded' ? 'Исключена из журналов и оценок' : mode === 'manual' ? 'Cron пропускает вкладку; обновление только вручную' : source.last_error ? `Ошибка: ${source.last_error}` : source.last_synced_at ? `Обновлено ${dateText(source.last_synced_at)}` : 'Ещё не обновлялась'; return <div className="journal-tab-setting-row" key={`tab-mode:${source.id}`}><span><strong>{source.sheet_title}</strong><small>{statusText}</small></span><select aria-label={`Режим синхронизации: ${source.sheet_title}`} value={mode} disabled={Boolean(busy)} onChange={(event) => void setSourceMode(source, event.target.value as 'auto' | 'manual' | 'excluded')}><option value="auto">Автоматически</option><option value="manual">Только вручную</option><option value="excluded">Исключить</option></select><button type="button" className="admin-button admin-button--quiet" disabled={Boolean(busy) || mode === 'excluded'} onClick={() => void refreshSource(source)}>{busy === `source-refresh:${source.id}` ? 'Обновляем…' : 'Обновить вкладку'}</button></div>; })}</details>
    </> : <>
        <button className="admin-back" onClick={() => { setDetail(null); setSelectedId(null); setActionError(''); }}><ArrowLeft size={15} /> Все журналы</button>
      <nav className="journal-detail-nav" aria-label="Навигация по журналам"><label>Класс<select value={detail.source.grade} onChange={(event) => { const grade = Number(event.target.value); const nextBook = books.find((book) => book.grade === grade); const nextSubjects = journalSubjectGroups(sources.filter((source) => source.grade === grade && (!nextBook || source.spreadsheet_id === nextBook.spreadsheetId))); const sameSubject = nextSubjects.find((item) => item.subject === detail.source.subject); const group = sameSubject || nextSubjects[0]; const source = group?.periods.find((item) => item.period_status === 'current') || group?.periods.find((item) => item.period_status === 'unassigned') || group?.periods[group.periods.length - 1]; if (source) void loadDetail(source.id); else { setDetail(null); setSelectedId(null); setSelectedGrade(grade); } }}>{gradeList.map((grade) => <option key={grade} value={grade}>{grade} класс</option>)}</select></label><label>Предмет<select value={detail.source.subject} onChange={(event) => { const group = detailSubjects.find((item) => item.subject === event.target.value); const source = group?.periods.find((item) => item.period_status === 'current') || group?.periods.find((item) => item.period_status === 'unassigned') || group?.periods[group.periods.length - 1]; if (source) void loadDetail(source.id); }} >{detailSubjects.map((group) => <option key={group.subject} value={group.subject}>{group.subject}</option>)}</select></label></nav>
      <section className="admin-panel journal-detail-head"><div className="journal-detail-title"><div className="journal-detail-icon"><BookOpen size={18} /></div><div><p className="admin-eyebrow">{detail.source.grade} КЛАСС · ЖУРНАЛ ПРЕДМЕТА</p><h2>{detail.source.subject}</h2><p>{detail.source.spreadsheet_title} <span>·</span> {detail.source.academic_year} · обновлено {dateText(detail.source.last_synced_at)}</p></div></div><div className="admin-button-row"><a className="admin-button admin-button--quiet" href={`https://docs.google.com/spreadsheets/d/${encodeURIComponent(detail.source.spreadsheet_id)}/edit#gid=${encodeURIComponent(String(detail.source.sheet_id || ''))}`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Оригинальная таблица</a><button className="admin-button admin-button--quiet" disabled={Boolean(busy) || Boolean(selectedSource?.active_run_id)} onClick={() => void refreshBook(detail.source.spreadsheet_id, detail.source.grade)}><RefreshCw size={14} /> Обновить сейчас</button></div></section>
      <div className="journal-group-tabs" role="tablist" aria-label="Триместр">{[1, 2, 3].map((term) => { const source = periodSources.find((item) => item.term_number === term); return <button key={term} role="tab" aria-selected={selectedTerm === term} className={selectedTerm === term ? 'is-active' : ''} onClick={() => { setSelectedTerm(term); if (source) void loadDetail(source.id); }}>{['I', 'II', 'III'][term - 1]} триместр{source?.period_status === 'current' ? ' · текущий' : ''}</button>; })}</div>
      {periodSources.filter((source) => source.period_status === 'unassigned' || source.term_number === null).map((source) => <div className="journal-source-error" key={source.id}><AlertTriangle size={14} /><span>Вкладка «{source.sheet_title}» ожидает подтверждения триместра.</span>{[1, 2, 3].map((term) => <button className="admin-link" key={term} disabled={Boolean(busy)} onClick={async () => { setBusy('period'); try { await api(`/api/admin/journals/${source.id}/period`, { method: 'PUT', body: JSON.stringify({ term_number: term, make_current: false }) }); await loadSources(); await loadDetail(source.id); } catch (error) { setActionError(errorText(error)); } finally { setBusy(''); } }}>Привязать к {['I', 'II', 'III'][term - 1]}</button>)}</div>)}
      {selectedTerm !== null && periodSources.find((source) => source.period_status === 'current')?.term_number !== selectedTerm ? <button className="admin-button admin-button--quiet" disabled={Boolean(busy)} onClick={async () => { setBusy('period'); try { await api('/api/admin/journals/current-term', { method: 'PUT', body: JSON.stringify({ spreadsheet_id: detail.source.spreadsheet_id, grade: detail.source.grade, academic_year: detail.source.academic_year || '2026/27', term_number: selectedTerm }) }); await loadSources(); } catch (error) { setActionError(errorText(error)); } finally { setBusy(''); } }}>Сделать выбранный триместр текущим</button> : null}
      {viewingEmptyTerm ? <section className="admin-panel"><div className="admin-empty">Журнал за выбранный триместр ещё не создан.</div></section> : <>
      {sourceMode(detail.source, sourceModes) === 'auto' && detail.source.last_error ? <div className="journal-source-error"><AlertTriangle size={14} /><span>{detail.source.last_error}</span></div> : null}
      <details className="admin-panel journal-history"><summary>История синхронизаций <span>{syncHistory.length}</span></summary>{syncHistory.length ? <ul>{syncHistory.map((run) => <li key={run.id}><strong>{run.status === 'success' ? 'Успешно' : run.status === 'partial' ? 'Частично' : run.status === 'running' ? 'Выполняется' : 'Ошибка'}</strong><span>{run.trigger_kind === 'scheduled' ? 'По расписанию' : 'Вручную'} · {dateText(run.started_at)}</span>{run.error ? <small>Синхронизация завершилась с ошибкой. Предыдущие данные сохранены.</small> : null}</li>)}</ul> : <p className="admin-help-text">Синхронизаций ещё нет.</p>}</details>
      {detail.groups.filter((marker) => marker !== 'all').length ? <div className="journal-group-tabs" role="tablist" aria-label="Учебная группа">{detail.groups.filter((marker) => marker !== 'all').map((marker) => <button key={marker} role="tab" aria-selected={(groupMarker || detail.groups[0]) === marker} className={(groupMarker || detail.groups[0]) === marker ? 'is-active' : ''} onClick={() => void loadDetail(detail.source.id, marker)}>Группа {marker}</button>)}</div> : null}
      {activeGroupMarker ? <section className="admin-panel journal-mapping-row"><div><strong>Связь группы журнала · маркер {activeGroupMarker}</strong><small>{activeGroupMapping?.group_id ? `Сейчас связана с: ${canonicalGroups.find((group) => String(group.id) === String(activeGroupMapping.group_id))?.display_name || canonicalGroups.find((group) => String(group.id) === String(activeGroupMapping.group_id))?.name || `группа ${activeGroupMapping.group_id}`}` : 'Каноническая группа ещё не подтверждена.'}</small><small>Выберите существующую каноническую группу. Новые группы здесь не создаются.</small></div><div className="journal-map-controls"><label>Каноническая группа<select aria-label={`Каноническая группа для маркера ${activeGroupMarker}`} value={selectedGroupMapId} onChange={(event) => setGroupMapChoice((current) => ({ ...current, [groupMapKey]: event.target.value }))}><option value="">Выберите группу</option>{activeGroupMapping?.group_id && !canonicalGroups.some((group) => String(group.id) === String(activeGroupMapping.group_id)) ? <option value={String(activeGroupMapping.group_id)} disabled>Текущая группа {activeGroupMapping.group_id} недоступна</option> : null}{canonicalGroups.map((group) => <option key={group.id} value={String(group.id)}>{group.display_name || group.name} · {group.subject || group.group_type}{group.base_class_name ? ` · ${group.base_class_name}` : ''} · {group.student_count || 0} учеников</option>)}</select></label><button className="admin-button admin-button--quiet" disabled={!selectedGroupMapId || !groupMapKey || Boolean(busy)} onClick={() => void saveGroupMapping(activeGroupMarker, selectedGroupMapId)}>{busy === 'map-group' ? 'Сохраняем…' : activeGroupMapping?.group_id ? 'Сохранить связь' : 'Подтвердить группу'}</button></div></section> : null}
      {detail.students.some((student) => (student.entry_kind || 'individual') === 'individual') ? resultTable(detail.students.filter((student) => (student.entry_kind || 'individual') === 'individual')) : null}
      {detail.students.some((student) => student.entry_kind === 'team') ? <><h3 className="journal-team-heading">Командные результаты · оценки относятся к команде</h3>{resultTable(detail.students.filter((student) => student.entry_kind === 'team'), true)}</> : null}
      {!detail.students.length ? <section className="admin-panel"><div className="admin-empty">В этой группе пока нет распознанных учеников или команд.</div></section> : null}
      </>}
    </>}
  </div>;
}
