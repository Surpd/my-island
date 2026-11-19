'use client';

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { TemplateSelectionDialog } from './template-selection-dialog';
import { formatLessonTimeRange } from './lesson-time';
import { normalizeRoomDisplay } from './schedule-rooms';

export type SchoolDraftSummary = { id: string; name: string; revision: number; lesson_count: number; updated_at?: string; week_start?: string | null; is_primary_template?: boolean; selection_stale?: boolean; selected_revision?: number; has_publications?: boolean; draft_kind: string; base_version_id?: string | null; source_template?: { id?: string; revision?: number } | null };
export type EffectiveWeekSummary = { effective_week_id: string; version_id: string; week_start: string; status: string; created_at: string; revision_at?: string; is_current_publication?: boolean };
export type WeekPublication = { id: string; draft_id: string; draft_revision: number; scope_key: string; published_id: string; created_at: string };
export type SchoolWeekIndex = { effective_weeks?: EffectiveWeekSummary[]; week_publications?: WeekPublication[]; canonical?: { version_id: string; status: string }; load_error?: string };
type WeekHistoryAssignment = { activity?: string; groups?: string[]; teachers?: string[]; room?: string | null };
type WeekHistoryBlock = { block_key: string; weekday: number; start_time: string; end_time: string; change_kind?: string; grade_scope?: string | null; effective_assignments?: WeekHistoryAssignment[] };
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type SelectionPreview = {
  current: { id: string; name: string; revision: number; draft_revision?: number; has_unreviewed_changes?: boolean; snapshot_available?: boolean } | null;
  candidate: { id: string; name: string; revision: number };
  current_hash: string | null; candidate_hash: string;
  added: { id: string; subject: string; start: string; end: string; groups: string[]; members: string[]; teachers: string[]; room: string; activity_type?: string; special_mark?: string; audience_kind?: string; audience_mode?: string; excluded_groups?: string[]; problems: string[] }[];
  removed: { id: string; subject: string; start: string; end: string; groups: string[]; members: string[]; teachers: string[]; room: string; activity_type?: string; special_mark?: string; audience_kind?: string; audience_mode?: string; excluded_groups?: string[]; problems: string[] }[];
  changed: { id: string; before: { subject: string; start: string; end: string; groups: string[]; members: string[]; teachers: string[]; room: string; problems: string[] }; after: { subject: string; start: string; end: string; groups: string[]; members: string[]; teachers: string[]; room: string; problems: string[] }; changes: { field: string; label: string; impact: string; before: string | string[] | number | boolean | null; after: string | string[] | number | boolean | null; before_text?: string; after_text?: string }[] }[];
  settings_changes: { field: string; label: string; impact: string; before: string; after: string }[];
  problems: { lesson_id: string; details: string[] }[];
  has_semantic_changes: boolean;
  baseline_recovery_required?: boolean;
  baseline_recovery?: { draft_id: string; revision: number; payload_hash: string; missing_revision: number; missing_payload_hash: string; contents: SelectionPreview['added']; problems: SelectionPreview['problems'] } | null;
};
type NormalizationPreview = { source_id: string; source_name: string; source_revision: number; source_hash: string; plan_hash: string; lesson_count: number; automatic_count: number; already_current_count: number; unchanged_count: number; unclassified_count: number; manual_count: number; real_conflict_count: number | null; retained_warning_count: number; retained_warnings: { reason: string; count: number; examples: { lesson_id: string; subject: string }[] }[]; changes: { lesson_id: string; field?: string; before: string; after: string; block_id?: string; reason: string }[]; manual: { lesson_id: string; reason: string }[]; notes: string[] };

export function nearestMonday(today = new Date()) {
  const date = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  date.setDate(date.getDate() + (8 - date.getDay()) % 7);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
export function weekPeriod(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const start = new Date(`${value}T12:00:00`), end = new Date(start);
  if (Number.isNaN(start.getTime())) return '';
  end.setDate(end.getDate() + 6);
  const options: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'long' };
  return `${start.toLocaleDateString('ru-RU', options)} — ${end.toLocaleDateString('ru-RU', options)} ${end.getFullYear()}`;
}
const changedAt = (value?: string) => value ? new Date(value).toLocaleString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'дата не указана';
const isTest = (draft: SchoolDraftSummary) => draft.draft_kind === 'test' || /(?:^|[\s·])тест(?:[\s·]|$)/iu.test(draft.name);
export function ConstructorSchoolStart({ api, drafts, index, hiddenIds, readOnly, initialSection = 'template', onOpen, onOpenLesson, onCopy, onHide, onRestore, onCreateEmpty, onCreated, onPublication }: {
  api: Api; drafts: SchoolDraftSummary[]; index: SchoolWeekIndex; hiddenIds: string[]; readOnly: boolean;
  initialSection?: 'template' | 'weeks';
  onOpen: (id: string) => Promise<void>; onOpenLesson: (draftId: string, lessonId: string) => void; onCopy: (draft: SchoolDraftSummary) => void;
  onHide: (id: string) => void; onRestore: (id: string) => void; onCreateEmpty: () => void;
  onCreated: (id: string) => Promise<void>; onPublication: (id: string) => Promise<void>;
}) {
  const [section, setSection] = useState<'template' | 'weeks'>(initialSection);
  const [weekStart, setWeekStart] = useState(nearestMonday);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('');
  const [selectionPreview, setSelectionPreview] = useState<SelectionPreview | null>(null);
  const [normalizationPreview, setNormalizationPreview] = useState<NormalizationPreview | null>(null);
  const [normalizationBusy, setNormalizationBusy] = useState(false);
  const [normalizationError, setNormalizationError] = useState('');
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const [acknowledgeProblems, setAcknowledgeProblems] = useState(false);
  const [acknowledgeBaseline, setAcknowledgeBaseline] = useState(false);
  const [selectionHistory, setSelectionHistory] = useState<{ id: string; previous_draft_id?: string; previous_revision?: number; next_draft_id: string; next_revision: number; created_at: string }[]>([]);
  const [restoredTests, setRestoredTests] = useState<string[]>([]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved: unknown = JSON.parse(localStorage.getItem('my-island-visible-test-drafts:v1') || '[]');
        setRestoredTests(Array.isArray(saved) ? saved.filter((item: unknown): item is string => typeof item === 'string') : []);
      } catch { /* Optional browser preference. */ }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  const [history, setHistory] = useState<{ title: string; publications?: WeekPublication[]; weeks?: EffectiveWeekSummary[]; drafts?: SchoolDraftSummary[] } | null>(null);
  const [view, setView] = useState<{ title: string; loading?: boolean; blocks?: WeekHistoryBlock[]; error?: string } | null>(null);
  const primary = drafts.find(item => item.is_primary_template);
  const publications = index.week_publications || [];
  const effective = index.effective_weeks || [];
  const publishedDrafts = new Set(publications.map(item => item.draft_id));
  // Test records with no publication or selection stay accessible in the
  // secondary archive. This view does not mutate production archive flags.
  const archived = drafts.filter(item => item.id !== primary?.id && (hiddenIds.includes(item.id) || isTest(item) && !restoredTests.includes(item.id) && !item.has_publications && !publishedDrafts.has(item.id)));
  const archiveIds = new Set(archived.map(item => item.id));
  const weekDrafts = drafts.filter(item => item.id !== primary?.id && item.week_start && !archiveIds.has(item.id));
  const others = drafts.filter(item => item.id !== primary?.id && !item.week_start && !archiveIds.has(item.id));
  const weeks = [...new Set([...effective.map(item => item.week_start), ...publications.map(item => item.scope_key.replace(/^week:/, '')), ...weekDrafts.map(item => item.week_start!)])].sort().reverse();
  const existingDrafts = drafts.filter(item => item.week_start === weekStart);
  const weekExists = existingDrafts.length > 0 || effective.some(item => item.week_start === weekStart) || publications.some(item => item.scope_key === `week:${weekStart}`);
  const showWeek = async (item: EffectiveWeekSummary) => {
    setView({ title: weekPeriod(item.week_start), loading: true });
    try {
      const result = await api<{ blocks: WeekHistoryBlock[] }>(`/api/admin/schedule/observability?editor=true&effective_week_id=${encodeURIComponent(item.effective_week_id)}`);
      setView({ title: weekPeriod(item.week_start), blocks: result.blocks.filter(block => block.change_kind !== 'template_only') });
    } catch (error) { setView({ title: weekPeriod(item.week_start), error: (error as Error).message }); }
  };
  const showHistory = (week: string) => setHistory({ title: weekPeriod(week), weeks: effective.filter(item => item.week_start === week), publications: publications.filter(item => item.scope_key === `week:${week}`), drafts: drafts.filter(item => item.week_start === week) });
  const templateHistory = async () => {
    if (!primary) return;
    try {
      const result = await api<{ items: WeekPublication[] }>(`/api/admin/schedule/constructor/${encodeURIComponent(primary.id)}/publications`);
      setHistory({ title: primary.name, publications: result.items, drafts: [primary] });
    } catch (error) { setMessage((error as Error).message); }
  };
  const previewTemplateSelection = async (item: SchoolDraftSummary) => {
    setSelectionBusy(true); setMessage(''); setSelectionError('');
    try {
      const result = await api<SelectionPreview>('/api/admin/schedule/experiments/school/' + encodeURIComponent(item.id) + '/selection-preview', { method: 'POST' });
      setSelectionPreview(result); setAcknowledgeProblems(false);
      setAcknowledgeBaseline(false);
    } catch (error) { setSelectionError((error as Error).message); setMessage((error as Error).message); } finally { setSelectionBusy(false); }
  };
  const previewNormalization = async () => {
    if (!primary || normalizationBusy) return;
    setNormalizationBusy(true); setNormalizationError('');
    try {
      const result = await api<NormalizationPreview>(`/api/admin/schedule/experiments/school/${encodeURIComponent(primary.id)}/normalization-preview`, { method: 'POST' });
      setNormalizationPreview(result);
    } catch (error) { setNormalizationError((error as Error).message); } finally { setNormalizationBusy(false); }
  };
  const applyNormalizationCopy = async () => {
    if (!normalizationPreview || normalizationBusy || readOnly) return;
    if (!window.confirm(`Создать отдельную копию из ${normalizationPreview.source_name}? Исходный шаблон, публикации и основной выбор останутся без изменений.`)) return;
    setNormalizationBusy(true); setNormalizationError('');
    try {
      const copy = await api<{ id: string; changed_count: number; lesson_count: number }>(`/api/admin/schedule/experiments/school/${encodeURIComponent(normalizationPreview.source_id)}/normalization-copy`, { method: 'POST', body: JSON.stringify({ expected_revision: normalizationPreview.source_revision, expected_source_hash: normalizationPreview.source_hash, expected_plan_hash: normalizationPreview.plan_hash }) });
      setNormalizationPreview(null); setMessage(`Создана отдельная копия: ${copy.changed_count} явных типов исправлено; ${copy.lesson_count} занятий сохранено. Основной шаблон и публикации не изменены.`);
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (error) { setNormalizationError((error as Error).message); } finally { setNormalizationBusy(false); }
  };
  const approveLegacyBaseline = async () => {
    const baseline = selectionPreview?.baseline_recovery;
    if (!baseline || selectionBusy || !acknowledgeBaseline || (baseline.problems?.length && !acknowledgeProblems)) return;
    setSelectionBusy(true); setSelectionError('');
    try {
      await api('/api/admin/schedule/experiments/school/' + encodeURIComponent(baseline.draft_id) + '/approve-legacy-baseline', {
        method: 'POST', body: JSON.stringify({
          expected_revision: baseline.revision,
          expected_payload_hash: baseline.payload_hash,
          expected_missing_revision: baseline.missing_revision,
          expected_missing_payload_hash: baseline.missing_payload_hash,
          acknowledge_missing_history: true,
          acknowledge_problems: acknowledgeProblems,
        }),
      });
      setSelectionPreview(null);
      setMessage(`Текущая ревизия ${baseline.revision} утверждена как новая базовая версия. Историческая ревизия ${baseline.missing_revision} не восстановлена.`);
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (error) { setSelectionError((error as Error).message); } finally { setSelectionBusy(false); }
  };
  const confirmTemplateSelection = async () => {
    if (!selectionPreview || selectionBusy) return;
    setSelectionBusy(true); setSelectionError('');
    try {
      await api('/api/admin/schedule/experiments/school/' + encodeURIComponent(selectionPreview.candidate.id) + '/select-primary', {
        method: 'POST', body: JSON.stringify({
          expected_revision: selectionPreview.candidate.revision,
          expected_current_id: selectionPreview.current?.id || null,
          expected_current_revision: selectionPreview.current?.revision || null,
          expected_candidate_hash: selectionPreview.candidate_hash,
          acknowledge_problems: acknowledgeProblems,
        }),
      });
      setSelectionPreview(null);
      setMessage('Основной шаблон переключён. Уже созданные недели не изменены.');
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (error) { setSelectionError((error as Error).message); } finally { setSelectionBusy(false); }
  };
  const loadSelectionHistory = async () => {
    try {
      const result = await api<{ items: typeof selectionHistory }>('/api/admin/schedule/experiments/school/template-selection-history');
      setSelectionHistory(result.items);
    } catch (error) { setMessage((error as Error).message); }
  };
  const rollbackSelection = async (id: string) => {
    if (!window.confirm('Вернуть снимок предыдущего основного шаблона отдельной версией?')) return;
    try {
      await api('/api/admin/schedule/experiments/school/template-selection-rollback', { method: 'POST', body: JSON.stringify({ history_id: id }) });
      setSelectionHistory([]);
      setMessage('Предыдущий шаблон восстановлен отдельной версией.');
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (error) { setMessage((error as Error).message); }
  };
  const createWeek = async () => {
    if (!primary || creating || readOnly || weekExists) return;
    if (new Date(`${weekStart}T12:00:00`).getDay() !== 1) { setMessage('Выберите понедельник для начала недели.'); return; }
    setCreating(true); setMessage('');
    try {
      const result = await api<{ id: string }>(`/api/admin/schedule/experiments/school/${encodeURIComponent(primary.id)}/week`, { method: 'POST', body: JSON.stringify({ expected_revision: primary.revision, week_start: weekStart }) });
      await onCreated(result.id);
    } catch (error) { setMessage((error as Error).message); } finally { setCreating(false); }
  };
  const editHistoricalWeek = async (item: EffectiveWeekSummary) => {
    if (creating || readOnly) return;
    setCreating(true); setMessage('');
    try {
      const result = await api<{ id: string }>(`/api/admin/schedule/experiments/school/effective-week/${encodeURIComponent(item.effective_week_id)}`, { method: 'POST' });
      await onCreated(result.id);
    } catch (error) { setMessage((error as Error).message); } finally { setCreating(false); }
  };
  const restore = (item: SchoolDraftSummary) => {
    onRestore(item.id);
    const next = [...new Set([...restoredTests, item.id])];
    setRestoredTests(next);
    try { localStorage.setItem('my-island-visible-test-drafts:v1', JSON.stringify(next)); } catch { /* Optional browser preference. */ }
  };
  const draftCard = (item: SchoolDraftSummary, archive = false) => <article className="constructor-draft" key={item.id}>
    <button className="constructor-draft-open" onClick={() => void onOpen(item.id)}><strong>{item.name}</strong><span>{item.lesson_count} занятий · ревизия {item.revision} · {changedAt(item.updated_at)}</span></button>
    <div className="constructor-draft-actions">{item.draft_kind === 'school' && !item.week_start && !item.is_primary_template && !archive ? <button disabled={readOnly || selectionBusy} onClick={() => void previewTemplateSelection(item)}>Сделать основным шаблоном</button> : null}<button disabled={readOnly} onClick={() => onCopy(item)}>Создать копию</button>{item.is_primary_template ? null : archive ? <button onClick={() => restore(item)}>Вернуть в список</button> : <button onClick={() => onHide(item.id)}>Скрыть</button>}</div>
  </article>;
  return <section className="constructor-start constructor-school-start">
    <nav className="constructor-draft-filters" aria-label="Разделы расписания"><button aria-current={section === 'template' ? 'page' : undefined} className={section === 'template' ? 'active' : ''} onClick={() => setSection('template')}>Основной шаблон</button><button aria-current={section === 'weeks' ? 'page' : undefined} className={section === 'weeks' ? 'active' : ''} onClick={() => setSection('weeks')}>Недели <small>{weeks.length}</small></button></nav>
    {message ? <p role="alert">{message}</p> : null}{index.load_error ? <p role="alert">Не удалось получить историю недель: {index.load_error}. Сохранённые черновики показаны отдельно.</p> : null}
    {section === 'template' ? <section className="constructor-template-overview">
      <small className="constructor-section-caption">РЕДАКТОРСКАЯ ВЕРСИЯ</small>
      <h2>{primary?.name || 'Основной шаблон не назначен'}</h2>
      {primary ? <><p>{primary.lesson_count} занятий · ревизия {primary.revision} · изменён {changedAt(primary.updated_at)}</p>{primary.selection_stale ? <div className="constructor-existing-week" role="alert"><p>В основном шаблоне есть изменения, которые ещё не сравнили и не подтвердили. Новые недели пока не создаются.</p><button disabled={readOnly || selectionBusy} onClick={() => void previewTemplateSelection(primary)}>Сравнить правки и подтвердить новую ревизию</button></div> : null}<div className="constructor-version-status"><div><strong>Сохранён в редакторе</strong><span>{primary.selection_stale ? `Подтверждена ревизия ${primary.selected_revision ?? 'неизвестна'}; текущая редакторская ревизия ${primary.revision}.` : `Из ревизии ${primary.revision} создаются недели; расписание не содержит неподтверждённых изменений.`}</span></div><div><strong>{index.canonical ? 'Опубликованная основа' : 'Опубликованная основа не получена'}</strong><span>{index.canonical ? `${({ authoritative: 'Подтверждена', approved: 'Подтверждена', approved_with_exceptions: 'Подтверждена с исключениями', superseded: 'Историческая версия' } as Record<string, string>)[index.canonical.status] || index.canonical.status}. Сохранённый редакторский шаблон учитывается отдельно.` : 'Проверьте подключение к школьной базе.'}</span>{index.canonical ? <code>{index.canonical.version_id}</code> : null}</div></div><div className="constructor-start-primary-actions"><button className="admin-button admin-button--primary" onClick={() => void onOpen(primary.id)}>Открыть и редактировать</button><button onClick={() => void templateHistory()}>История версий</button><button onClick={() => void loadSelectionHistory()}>История переключений</button><button disabled={readOnly} onClick={() => onCopy(primary)}>Создать отдельную копию</button><button disabled={readOnly || normalizationBusy} onClick={() => void previewNormalization()}>Проверить и обновить старые записи</button>{!readOnly ? <button onClick={() => void onPublication(primary.id)}>Проверить публикацию</button> : null}</div><p className="constructor-muted">Публикация выполняется после проверки и отдельного подтверждения.</p></> : <p>Назначьте проверенный редакторский шаблон. Автоматическая замена canonical-копией не выполняется.</p>}
      {selectionHistory.length ? <section aria-label="История переключений шаблона"><h3>История переключений</h3>{selectionHistory.map(item => <article className="constructor-draft" key={item.id}><div className="constructor-draft-open"><strong>Снимок {item.previous_draft_id || 'первого шаблона'} · ревизия {item.previous_revision || '—'}</strong><span>{changedAt(item.created_at)}</span></div><div className="constructor-draft-actions"><button disabled={readOnly} onClick={() => void rollbackSelection(item.id)}>Вернуть предыдущую версию</button></div></article>)}</section> : null}
      {others.length ? <details className="constructor-secondary-actions"><summary>Другие черновики шаблона · {others.length}</summary>{others.map(item => draftCard(item))}</details> : null}
    </section> : <>
      <div className="constructor-section-heading"><div><h2>Недели</h2><p>Сохранённые черновики и история недель по датам.</p></div><a href="#constructor-create-week">Создать неделю</a></div>
      <section className="constructor-week-list" aria-label="Все недели">{weeks.length ? weeks.map(week => {
        const saved = weekDrafts.filter(item => item.week_start === week), versions = effective.filter(item => item.week_start === week);
        const current = versions.find(item => item.is_current_publication), latest = current || versions[0];
        return <article className="constructor-draft constructor-week-row" key={week}><div className="constructor-draft-open"><strong>{weekPeriod(week)}</strong><div className="constructor-week-status">{saved.length ? <span>Сохранённый черновик{saved.length > 1 ? ` · ${saved.length}` : ''}</span> : null}{current ? <span>Действующая неделя</span> : versions.length ? <span>Историческая версия</span> : null}</div><span>{versions.length ? `Версий недели: ${versions.length}` : 'Сохранён только черновик'}</span></div><div className="constructor-draft-actions">{saved.length ? <button onClick={() => void onOpen(saved[0].id)}>Открыть черновик</button> : latest ? <button onClick={() => void showWeek(latest)}>Просмотреть</button> : null}<button onClick={() => showHistory(week)}>История</button>{!saved.length && latest && !readOnly ? <button disabled={creating} onClick={() => void editHistoricalWeek(latest)}>Создать черновик версии</button> : null}</div></article>;
      }) : <p>Сохранённых недель пока нет.</p>}</section>
      <section id="constructor-create-week" className="constructor-create-week"><h3>Создать неделю</h3><p>{primary ? <>Источник: <strong>{primary.name}</strong> · редакторская ревизия {primary.revision}, {primary.lesson_count} занятий.</> : 'Сначала выберите основной редакторский шаблон.'}</p><div className="constructor-week-form"><label htmlFor="constructor-week-start">Неделя начинается<input id="constructor-week-start" type="date" value={weekStart} onChange={event => { setWeekStart(event.target.value); setMessage(''); }} /></label><div className="constructor-week-period" aria-live="polite">{weekPeriod(weekStart)}</div></div>{weekExists ? <div className="constructor-existing-week"><p>Эта неделя уже существует. Сначала откройте её черновик или историю.</p>{existingDrafts.length ? <button onClick={() => void onOpen(existingDrafts[0].id)}>Открыть существующий черновик</button> : <button onClick={() => showHistory(weekStart)}>Открыть историю недели</button>}</div> : <button className="admin-button admin-button--primary" disabled={readOnly || !primary || primary.selection_stale || !weekStart || creating} onClick={() => void createWeek()}>{creating ? 'Создаём черновик…' : 'Создать из основного шаблона'}</button>}{primary?.selection_stale ? <output style={{ display: 'block' }}>Сначала сравните и подтвердите изменения основного шаблона.</output> : null}<p className="constructor-muted">Создаётся самостоятельный черновик. Публикация доступна отдельным действием.</p></section>
    </>}
    <details className="constructor-secondary-actions"><summary>Другие черновики и архив · {(section === 'weeks' ? others.length : 0) + archived.length}</summary><p className="constructor-muted">Тестовые и скрытые записи сохранены. Скрытие действует только в этом браузере.</p>{section === 'weeks' ? others.map(item => draftCard(item)) : null}{archived.length ? <details><summary>Тестовые и скрытые записи · {archived.length}</summary>{archived.map(item => draftCard(item, true))}</details> : null}</details>
    <details className="constructor-secondary-actions"><summary>Дополнительно</summary><button disabled={readOnly} onClick={onCreateEmpty}>Создать пустое расписание</button></details>
    {history ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-history-title" onCancel={() => setHistory(null)}><header><h2 id="week-history-title">История · {history.title}</h2><button aria-label="Закрыть историю" onClick={() => setHistory(null)}><X size={20} /></button></header><h3>Сохранённые черновики</h3>{history.drafts?.length ? history.drafts.map(item => draftCard(item)) : <p>Отдельного черновика нет.</p>}<h3>Версии недели</h3>{history.weeks?.length ? history.weeks.map((item, position) => <article className="constructor-draft" key={item.effective_week_id}><div className="constructor-draft-open"><strong>{item.is_current_publication ? 'Действующая публикация' : 'Историческая версия'} · {history.weeks!.length - position}</strong><span>{changedAt(item.revision_at || item.created_at)}</span><code>{item.version_id}</code></div><div className="constructor-draft-actions"><button onClick={() => void showWeek(item)}>Просмотреть</button>{!readOnly ? <button disabled={creating} onClick={() => void editHistoricalWeek(item)}>Создать черновик версии</button> : null}</div></article>) : <p>Сохранённых версий недели нет.</p>}<h3>Публикации</h3>{history.publications?.length ? history.publications.map(item => <p key={item.id}>{changedAt(item.created_at)} · ревизия черновика {item.draft_revision}</p>) : <p>Записей публикации нет.</p>}</dialog></div> : null}
    {view ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-view-title" onCancel={() => setView(null)}><header><div><small>Только просмотр сохранённой версии</small><h2 id="week-view-title">{view.title}</h2></div><button aria-label="Закрыть просмотр недели" onClick={() => setView(null)}><X size={20} /></button></header>{view.loading ? <p>Загружаем занятия…</p> : view.error ? <p role="alert">{view.error}</p> : view.blocks?.map((block, position) => <article className="constructor-history-lesson" key={`${block.block_key}:${position}`}><strong>{['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][block.weekday]} · {formatLessonTimeRange(block.start_time, block.end_time)}</strong>{block.change_kind === 'cancelled' ? <p>Занятие отменено в этой версии</p> : (block.effective_assignments || []).map((item, i) => <p key={i}>{item.activity} · {item.groups?.join(', ') || `${block.grade_scope || ''} параллель`} · {item.teachers?.join(', ') || 'Преподаватель не указан'}{item.room ? ` · ${normalizeRoomDisplay(item.room)}` : ''}</p>)}</article>)}</dialog></div> : null}
    {selectionPreview ? <TemplateSelectionDialog
      preview={selectionPreview}
      busy={selectionBusy}
      readOnly={readOnly}
      actionError={selectionError}
      acknowledgeProblems={acknowledgeProblems}
      acknowledgeBaseline={acknowledgeBaseline}
      onAcknowledgeProblems={setAcknowledgeProblems}
      onAcknowledgeBaseline={setAcknowledgeBaseline}
      onCancel={() => setSelectionPreview(null)}
      onConfirm={() => void confirmTemplateSelection()}
      onApproveBaseline={() => void approveLegacyBaseline()}
    /> : null}
    {normalizationPreview ? <div className="constructor-conflict-backdrop"><dialog open className="constructor-conflict-dialog" aria-labelledby="schedule-normalization-title"><header><div><small>Предварительный анализ · ревизия {normalizationPreview.source_revision}</small><h2 id="schedule-normalization-title">Проверить и обновить старые записи</h2></div><button aria-label="Закрыть анализ" onClick={() => setNormalizationPreview(null)}><X size={20} /></button></header><p>{normalizationPreview.lesson_count} исходных занятий. Безопасные преобразования: <strong>{normalizationPreview.automatic_count}</strong>; уже заданы: <strong>{normalizationPreview.already_current_count}</strong>; оставлены без изменения/классификации: <strong>{normalizationPreview.unclassified_count}</strong>; требуют отдельного решения: <strong>{normalizationPreview.manual_count}</strong>. Реальные конфликты этим анализом не пересчитывались.</p><p>Исходный шаблон, недельные копии, основной выбор и публикации не меняются. Создаётся отдельная копия без автоматической публикации.</p><h3>Подтверждённые изменения</h3>{normalizationPreview.changes.length ? normalizationPreview.changes.map((item, index) => <article className="constructor-history-lesson" key={`${item.lesson_id}:${index}`}><strong>Занятие {item.lesson_id}</strong>{item.field === 'teacher_assignment_warning' || item.field === 'teacher_identity_warning' ? <p>Было: «{item.before}» → станет: предупреждение снято после проверки {item.field === 'teacher_assignment_warning' ? 'преподавателя, группы и предмета' : 'активного преподавателя'}.</p> : <p>Было: тип не задан → станет: курс по выбору.</p>}<small>{item.reason}{item.block_id ? ` · общий блок ${item.block_id}` : ''}</small><button type="button" onClick={() => { setNormalizationPreview(null); onOpenLesson(normalizationPreview.source_id, item.lesson_id); }}>Открыть исходное занятие</button></article>) : <p>Автоматических изменений нет.</p>}<h3>Требуют решения администратора</h3>{normalizationPreview.manual.map(item => <article className="constructor-history-lesson" key={item.lesson_id}><strong>Занятие {item.lesson_id}</strong><p>{item.reason}</p><button type="button" onClick={() => { setNormalizationPreview(null); onOpenLesson(normalizationPreview.source_id, item.lesson_id); }}>Открыть исходное занятие</button></article>)}<details><summary>Сохранённые предупреждения · {normalizationPreview.retained_warning_count}</summary>{normalizationPreview.retained_warnings.map(warning => <article className="constructor-history-lesson" key={warning.reason}><strong>{warning.reason} · {warning.count}</strong>{warning.examples.map(example => <p key={example.lesson_id}>{example.subject} · <button type="button" onClick={() => { setNormalizationPreview(null); onOpenLesson(normalizationPreview.source_id, example.lesson_id); }}>Открыть занятие</button></p>)}</article>)}</details>{normalizationPreview.notes.map(note => <p className="constructor-muted" key={note}>{note}</p>)}{normalizationError ? <p role="alert">{normalizationError}</p> : null}<footer><button disabled={normalizationBusy} onClick={() => setNormalizationPreview(null)}>Отмена</button><button className="admin-button admin-button--primary" disabled={readOnly || normalizationBusy || !normalizationPreview.automatic_count} onClick={() => void applyNormalizationCopy()}>{normalizationBusy ? 'Создаём копию…' : 'Подтвердить и создать отдельную копию'}</button></footer></dialog></div> : null}
  </section>;
}
