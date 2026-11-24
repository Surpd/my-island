'use client';

import { SHORT_WEEKDAYS } from './schedule-weekdays';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Ellipsis, X } from 'lucide-react';
import { TemplateSelectionDialog } from './template-selection-dialog';
import { formatLessonTimeRange } from './lesson-time';
import { normalizeRoomDisplay } from './schedule-rooms';
import './constructor-school-start.css';

export type SchoolDraftSummary = { id: string; name: string; revision: number; lesson_count: number; updated_at?: string; week_start?: string | null; is_primary_template?: boolean; selection_stale?: boolean; selected_revision?: number; has_publications?: boolean; archived_at?: string; has_active_laboratories?: boolean; can_delete?: boolean; draft_kind: string; base_version_id?: string | null; source_template?: { id?: string; revision?: number } | null };
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
const compareValue = (value: SelectionPreview['changed'][number]['changes'][number]['before']) => Array.isArray(value) ? value.join(', ') || '—' : value == null || value === '' ? '—' : String(value);

function StartMenu({ label = 'Ещё', accessibleLabel, children }: { label?: string; accessibleLabel?: string; children: ReactNode }) {
  return <details className={`schedule-start-menu${label === 'Ещё' ? '' : ' is-start'}`} data-ui-menu>
    <summary aria-label={accessibleLabel}>{label === 'Ещё' ? <Ellipsis size={15} aria-hidden="true" /> : null}{label}</summary>
    <div>{children}</div>
  </details>;
}

function StartMenuAction({ children, disabled, risk, onClick }: { children: ReactNode; disabled?: boolean; risk?: boolean; onClick: () => void }) {
  return <button className={risk ? 'schedule-start-risk' : undefined} disabled={disabled} onClick={event => {
    const menu = event.currentTarget.closest('details');
    menu?.removeAttribute('open');
    menu?.querySelector('summary')?.focus();
    onClick();
  }}>{children}</button>;
}

function TemplateComparison({ preview, onClose }: { preview: SelectionPreview; onClose: () => void }) {
  const lessonText = (lesson: SelectionPreview['added'][number]) => [lesson.subject, formatLessonTimeRange(lesson.start, lesson.end), lesson.groups.join(', '), lesson.teachers.join(', '), lesson.room && normalizeRoomDisplay(lesson.room)].filter(Boolean).join(' · ');
  return <dialog className="constructor-conflict-dialog constructor-week-history schedule-start-comparison" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="schedule-start-comparison-title" onCancel={onClose}>
    <header><h2 id="schedule-start-comparison-title">Сравнение шаблонов</h2><button aria-label="Закрыть сравнение" onClick={onClose}><X size={20} /></button></header>
    <div className="schedule-start-comparison-content">
      <dl className="schedule-start-facts">
        <div><dt>Основной шаблон</dt><dd>{preview.current ? `${preview.current.name} · ревизия ${preview.current.revision}` : 'Не назначен'}</dd></div>
        <div><dt>Выбранный шаблон</dt><dd>{preview.candidate.name} · ревизия {preview.candidate.revision}</dd></div>
      </dl>
      {preview.baseline_recovery_required ? <output className="schedule-start-notice">Снимок основной ревизии недоступен для сравнения.</output> : !preview.has_semantic_changes ? <p>Состав занятий и настройки совпадают.</p> : null}
      {!preview.baseline_recovery_required ? <>
        {([{ title: 'Добавленные занятия', lessons: preview.added }, { title: 'Удалённые занятия', lessons: preview.removed }]).map(({ title, lessons }) => lessons.length ? <details key={title}><summary>{title} · {lessons.length}</summary><ul>{lessons.map(lesson => <li key={lesson.id}>{lessonText(lesson)}</li>)}</ul></details> : null)}
        {preview.changed.length ? <details><summary>Изменённые занятия · {preview.changed.length}</summary><ul>{preview.changed.map(lesson => <li key={lesson.id}><strong>{lesson.after.subject} · {formatLessonTimeRange(lesson.after.start, lesson.after.end)}</strong><ul>{lesson.changes.map(change => <li key={change.field}>{change.label} — {change.before_text ?? compareValue(change.before)} → {change.after_text ?? compareValue(change.after)}</li>)}</ul></li>)}</ul></details> : null}
        {preview.settings_changes.length ? <details><summary>Изменения настроек · {preview.settings_changes.length}</summary><ul>{preview.settings_changes.map(item => <li key={item.field}>{item.label} — {item.before} → {item.after}</li>)}</ul></details> : null}
      </> : null}
      {preview.problems.length ? <details><summary>Замечания · {preview.problems.length}</summary><ul>{preview.problems.map(item => <li key={item.lesson_id}>{item.details.join(' · ')}</li>)}</ul></details> : null}
    </div>
  </dialog>;
}

export function ConstructorSchoolStart({ api, drafts, index, hiddenIds, readOnly, initialSection = 'template', onOpen, onCopy, onRename, onManage, onRestore, onCreateEmpty, onCreated, onReview, onCompare }: {
  api: Api; drafts: SchoolDraftSummary[]; index: SchoolWeekIndex; hiddenIds: string[]; readOnly: boolean;
  initialSection?: 'template' | 'weeks';
  onOpen: (id: string) => Promise<void>; onCopy: (draft: SchoolDraftSummary) => void;
  onRename: (item: SchoolDraftSummary) => void;
  onManage: (item: SchoolDraftSummary, action: 'archive' | 'restore' | 'delete') => Promise<void>; onRestore: (id: string) => void; onCreateEmpty: () => void;
  onCreated: (id: string) => Promise<void>; onReview: (id: string) => Promise<void>;
  onCompare?: (id: string) => Promise<boolean>;
}) {
  const [section, setSection] = useState<'template' | 'weeks'>(initialSection);
  const [weekStart, setWeekStart] = useState(nearestMonday);
  const [managing, setManaging] = useState(false);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('');
  const [selectionPreview, setSelectionPreview] = useState<SelectionPreview | null>(null);
  const [comparisonPreview, setComparisonPreview] = useState<SelectionPreview | null>(null);
  const [comparisonLoadingId, setComparisonLoadingId] = useState<string | null>(null);
  const comparisonTrigger = useRef<HTMLButtonElement>(null);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const [acknowledgeProblems, setAcknowledgeProblems] = useState(false);
  const [acknowledgeBaseline, setAcknowledgeBaseline] = useState(false);
  const [selectionHistory, setSelectionHistory] = useState<{ id: string; previous_draft_id?: string; previous_revision?: number; next_draft_id: string; next_revision: number; created_at: string }[]>([]);
  const [restoredTests, setRestoredTests] = useState<string[]>([]);
  const [showAllTemplates, setShowAllTemplates] = useState(false);
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
  const primaryRevision = primary?.selection_stale ? primary.selected_revision : primary?.revision;
  const publications = index.week_publications || [];
  const effective = index.effective_weeks || [];
  const publishedDrafts = new Set(publications.map(item => item.draft_id));
  // Test records with no publication or selection stay accessible in the
  // secondary archive. This view does not mutate production archive flags.
  const archived = drafts.filter(item => item.id !== primary?.id && (item.archived_at || hiddenIds.includes(item.id) || isTest(item) && !restoredTests.includes(item.id) && !item.has_publications && !publishedDrafts.has(item.id)));
  const archiveIds = new Set(archived.map(item => item.id));
  const weekDrafts = drafts.filter(item => item.id !== primary?.id && item.week_start && !archiveIds.has(item.id));
  const others = drafts.filter(item => item.id !== primary?.id && !item.week_start && !archiveIds.has(item.id))
    .sort((left, right) => (right.updated_at || '').localeCompare(left.updated_at || '') || right.revision - left.revision);
  const weeks = [...new Set([...effective.map(item => item.week_start), ...publications.map(item => item.scope_key.replace(/^week:/, '')), ...weekDrafts.map(item => item.week_start!)])].sort().reverse();
  const existingDrafts = weekDrafts.filter(item => item.week_start === weekStart);
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
  const previewTemplateSelection = async (item: SchoolDraftSummary, compareOnly = false) => {
    if (selectionBusy || (!compareOnly && readOnly)) return;
    setSelectionBusy(true); setMessage(''); setSelectionError('');
    if (compareOnly) setComparisonLoadingId(item.id);
    try {
      if (compareOnly && onCompare && await onCompare(item.id)) return;
      const result = await api<SelectionPreview>('/api/admin/schedule/experiments/school/' + encodeURIComponent(item.id) + '/selection-preview', { method: 'POST' });
      if (compareOnly) setComparisonPreview(result);
      else {
        setSelectionPreview(result);
        setAcknowledgeProblems(false);
        setAcknowledgeBaseline(false);
      }
    } catch (error) {
      setSelectionError((error as Error).message);
      setMessage((error as Error).message);
      if (compareOnly) window.requestAnimationFrame(() => comparisonTrigger.current?.focus());
    } finally {
      setSelectionBusy(false);
      setComparisonLoadingId(null);
    }
  };
  const approveLegacyBaseline = async () => {
    const baseline = selectionPreview?.baseline_recovery;
    if (readOnly || !baseline || selectionBusy || !acknowledgeBaseline || (baseline.problems?.length && !acknowledgeProblems)) return;
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
    if (readOnly || !selectionPreview || selectionBusy) return;
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
    if (readOnly) return;
    if (!window.confirm('Вернуть снимок предыдущего основного шаблона отдельной версией?')) return;
    try {
      await api('/api/admin/schedule/experiments/school/template-selection-rollback', { method: 'POST', body: JSON.stringify({ history_id: id }) });
      setSelectionHistory([]);
      setMessage('Предыдущий шаблон восстановлен отдельной версией.');
      window.dispatchEvent(new Event('school-directory-updated'));
    } catch (error) { setMessage((error as Error).message); }
  };
  const createWeek = async () => {
    if (!primary || primary.selection_stale || primaryRevision == null || creating || readOnly || weekExists) return;
    if (new Date(`${weekStart}T12:00:00`).getDay() !== 1) { setMessage('Выберите понедельник для начала недели.'); return; }
    setCreating(true); setMessage('');
    try {
      const result = await api<{ id: string }>(`/api/admin/schedule/experiments/school/${encodeURIComponent(primary.id)}/week`, { method: 'POST', body: JSON.stringify({ expected_revision: primaryRevision, week_start: weekStart }) });
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
  const manage = async (item: SchoolDraftSummary, action: 'archive' | 'restore' | 'delete') => {
    if (managing) return;
    if (action === 'archive' && !window.confirm(`Переместить «${item.name}» в архив? Запись можно восстановить.`)) return;
    if (action === 'delete' && !window.confirm(`Удалить «${item.name}» окончательно? Запись нельзя будет восстановить.`)) return;
    setManaging(true);
    setMessage('');
    try { await onManage(item, action); }
    catch (error) { setMessage((error as Error).message); }
    finally { setManaging(false); }
  };
  const lifecycleActions = (item: SchoolDraftSummary) => !readOnly && <>
    <StartMenuAction disabled={managing} onClick={() => onRename(item)}>Переименовать</StartMenuAction>
    <StartMenuAction disabled={managing} onClick={() => onCopy(item)}>Создать копию</StartMenuAction>
    {!item.is_primary_template && !item.has_active_laboratories &&
      <StartMenuAction risk disabled={managing} onClick={() => void manage(item, 'archive')}>Архивировать</StartMenuAction>}
  </>;
  const draftCard = (item: SchoolDraftSummary) => <article className="schedule-start-row" key={item.id}>
    <div><h3>{item.name}</h3><p>Ревизия {item.revision} · {item.lesson_count} занятий{item.updated_at ? ` · ${changedAt(item.updated_at)}` : ''}{item.archived_at ? ' · В архиве' : ''}</p></div>
    <div className="schedule-start-actions">
      {!item.archived_at && <button className="schedule-start-button" onClick={() => void onOpen(item.id)}>{item.week_start ? 'Открыть черновик' : 'Открыть'}</button>}
      {!item.archived_at && !item.week_start && !item.is_primary_template && <button className="schedule-start-button" disabled={selectionBusy} onClick={event => {
        comparisonTrigger.current = event.currentTarget;
        void previewTemplateSelection(item, true);
      }}>{comparisonLoadingId === item.id ? 'Сравниваем…' : 'Сравнить'}</button>}
      {item.archived_at ? !readOnly && <>
        <button className="schedule-start-button" disabled={managing} onClick={() => void manage(item, 'restore')}>Восстановить</button>
        {item.can_delete && <StartMenu><StartMenuAction risk disabled={managing} onClick={() => void manage(item, 'delete')}>Удалить окончательно</StartMenuAction></StartMenu>}
      </> : archiveIds.has(item.id) ? <button className="schedule-start-button" onClick={() => restore(item)}>Вернуть в список</button> : !readOnly && <StartMenu>
        {!item.is_primary_template && !item.week_start && item.draft_kind === 'school' && <StartMenuAction risk disabled={selectionBusy} onClick={() => void previewTemplateSelection(item)}>Сделать основным шаблоном</StartMenuAction>}
        {lifecycleActions(item)}
      </StartMenu>}
    </div>
  </article>;
  return <section className="constructor-start constructor-school-start schedule-start">
    <nav className="schedule-start-nav" aria-label="Разделы расписания">
      <button aria-current={section === 'template' ? 'page' : undefined} onClick={() => { setSection('template'); setMessage(''); }}>Основной шаблон</button>
      <button aria-current={section === 'weeks' ? 'page' : undefined} onClick={() => { setSection('weeks'); setMessage(''); }}>Недели</button>
    </nav>
    {message ? <p className="schedule-start-notice" role="alert">{message}</p> : null}
    {section === 'template' ? <>
      <section className="schedule-start-primary" aria-label="Основной шаблон школы">
        <h2>{primary?.name || 'Основной шаблон не назначен'}</h2>
        {primary ? <>
          <dl className="schedule-start-facts">
            <div><dt>Занятий</dt><dd>{primary.lesson_count}</dd></div>
            <div><dt>Ревизия для новых недель</dt><dd>{primaryRevision ?? 'Не подтверждена'}</dd></div>
            {primary.updated_at ? <div><dt>Последнее сохранение</dt><dd>{changedAt(primary.updated_at)}</dd></div> : null}
          </dl>
          {primary.selection_stale ? <div className="schedule-start-notice">
            <p>Сохранена ревизия {primary.revision}. Подтвердите её, чтобы создавать из неё новые недели.</p>
            <button className="schedule-start-button" disabled={readOnly || selectionBusy} onClick={() => void previewTemplateSelection(primary)}>Сравнить правки и подтвердить новую ревизию</button>
          </div> : null}
          <div className="schedule-start-actions">
            <button className="schedule-start-button is-primary" onClick={() => void onOpen(primary.id)}>Открыть расписание <ArrowRight size={15} aria-hidden="true" /></button>
            <button className="schedule-start-button" onClick={() => void onReview(primary.id)}>Проверить расписание</button>
            <StartMenu label="Действия" accessibleLabel="Действия с основным шаблоном">
              <StartMenuAction onClick={() => void templateHistory()}>История версий</StartMenuAction>
              <StartMenuAction onClick={() => void loadSelectionHistory()}>История переключений</StartMenuAction>
              {lifecycleActions(primary)}
            </StartMenu>
          </div>
        </> : <p>Выберите основной шаблон из списка, чтобы создавать недели.</p>}
      </section>
      <section className="schedule-start-section" aria-labelledby="schedule-start-drafts">
        <h2 id="schedule-start-drafts">Другие шаблоны и рабочие копии</h2>
        <div className="schedule-start-list">{others.length ? (showAllTemplates ? others : others.slice(0, 6)).map(item => draftCard(item)) : <p className="schedule-start-list-empty">Других шаблонов пока нет.</p>}</div>
        {others.length > 6 ? <button className="schedule-start-button schedule-start-more" onClick={() => setShowAllTemplates(!showAllTemplates)}>{showAllTemplates ? 'Свернуть список шаблонов' : `Показать остальные шаблоны · ${others.length - 6}`}</button> : null}
      </section>
      {selectionHistory.length ? <section className="schedule-start-section" aria-labelledby="schedule-start-selection-history">
        <h2 id="schedule-start-selection-history">История переключений</h2>
        <div className="schedule-start-list">{selectionHistory.map(item => <article className="schedule-start-row" key={item.id}>
          <div><h3>{item.previous_revision == null ? 'Первый основной шаблон' : `Предыдущая ревизия ${item.previous_revision}`}</h3><p>{changedAt(item.created_at)}</p></div>
          {item.previous_draft_id && !readOnly ? <button className="schedule-start-button" onClick={() => void rollbackSelection(item.id)}>Вернуть предыдущую версию</button> : null}
        </article>)}</div>
      </section> : null}
    </> : <>
      {index.load_error ? <p className="schedule-start-notice" role="alert">Не удалось получить публикации и историю недель. В списке доступны сохранённые черновики.</p> : null}
      <div className="schedule-start-weeks">
        <section className="schedule-start-create" aria-labelledby="schedule-start-create-week">
          <h2 id="schedule-start-create-week">Создать неделю</h2>
          {primary ? <p className="schedule-start-muted">Из шаблона «{primary.name}»{primaryRevision == null ? '' : ` · ревизия ${primaryRevision}`}.</p> : <p className="schedule-start-muted">Сначала выберите основной шаблон.</p>}
          <label className="schedule-start-date" htmlFor="constructor-week-start">Неделя начинается<input id="constructor-week-start" type="date" value={weekStart} onChange={event => { setWeekStart(event.target.value); setMessage(''); }} /></label>
          <p className="schedule-start-muted constructor-week-period" aria-live="polite">{weekPeriod(weekStart)}</p>
          {weekExists ? <div className="schedule-start-notice"><p>Неделя с этими датами уже есть.</p>{existingDrafts.length > 1 ? <button className="schedule-start-button" onClick={() => showHistory(weekStart)}>Выбрать черновик</button> : existingDrafts.length ? <button className="schedule-start-button" onClick={() => void onOpen(existingDrafts[0].id)}>Открыть существующий черновик</button> : <button className="schedule-start-button" onClick={() => showHistory(weekStart)}>Открыть историю недели</button>}</div> : <>
            <button className="schedule-start-button is-primary" disabled={readOnly || !primary || primary.selection_stale || primaryRevision == null || !weekStart || creating} onClick={() => void createWeek()}>{creating ? 'Создаём черновик…' : 'Создать из основного шаблона'}</button>
            {primary?.selection_stale ? <p className="schedule-start-muted">Подтвердите сохранённую ревизию на вкладке «Основной шаблон».</p> : primary ? <p className="schedule-start-muted">Будет создан черновик недели.</p> : null}
          </>}
        </section>
        <section className="schedule-start-section" aria-labelledby="schedule-start-weeks">
          <h2 id="schedule-start-weeks">Все недели</h2>
          <div className="schedule-start-list">{weeks.length ? weeks.map(week => {
            const saved = weekDrafts.filter(item => item.week_start === week);
            const versions = effective.filter(item => item.week_start === week);
            const current = versions.find(item => item.is_current_publication);
            const latest = current || versions[0];
            const publicationCount = publications.filter(item => item.scope_key === `week:${week}`).length;
            return <article className="schedule-start-row constructor-week-row" key={week}>
              <div><h3>{weekPeriod(week)}</h3><div className="schedule-start-week-state">
                {current ? <span className="is-published">Опубликована</span> : null}
                {saved.length ? <span className="is-draft">Черновик{saved.length > 1 ? ` · ${saved.length}` : ''}</span> : null}
                {!current && (versions.length || publicationCount) ? <span>История публикаций</span> : null}
              </div></div>
              <div className="schedule-start-actions">
                {saved.length > 1 ? <button className="schedule-start-button" onClick={() => showHistory(week)}>Выбрать черновик</button> : saved.length ? <button className="schedule-start-button" onClick={() => void onOpen(saved[0].id)}>Открыть черновик</button> : null}
                {latest ? <button className="schedule-start-button" onClick={() => void showWeek(latest)}>{current ? 'Просмотреть публикацию' : 'Просмотреть'}</button> : null}
                {(latest || publicationCount || saved.length) ? <StartMenu>
                  <StartMenuAction onClick={() => showHistory(week)}>История публикаций и черновики</StartMenuAction>
                  {saved.length === 1 && lifecycleActions(saved[0])}
                  {!saved.length && latest && !readOnly ? <StartMenuAction disabled={creating} onClick={() => void editHistoricalWeek(latest)}>Создать черновик версии</StartMenuAction> : null}
                </StartMenu> : null}
              </div>
            </article>;
          }) : <p className="schedule-start-list-empty">Сохранённых недель пока нет.</p>}</div>
        </section>
      </div>
    </>}
    {archived.length ? <details className="schedule-start-secondary">
      <summary>Архив и тестовые записи · {archived.length}</summary>
      <div className="schedule-start-list">{archived.map(draftCard)}</div>
    </details> : null}
    {!readOnly ? <details className="schedule-start-secondary"><summary>Дополнительно</summary><button className="schedule-start-button" onClick={onCreateEmpty}>Создать пустое расписание</button></details> : null}
    {comparisonPreview ? <TemplateComparison preview={comparisonPreview} onClose={() => {
      setComparisonPreview(null);
      window.requestAnimationFrame(() => comparisonTrigger.current?.focus());
    }} /> : null}
    {history ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-history-title" onCancel={() => setHistory(null)}><header><h2 id="week-history-title">История · {history.title}</h2><button aria-label="Закрыть историю" onClick={() => setHistory(null)}><X size={20} /></button></header><h3>Сохранённые черновики</h3>{history.drafts?.length ? history.drafts.flatMap(item => drafts.filter(current => current.id === item.id)).map(draftCard) : <p>Отдельного черновика нет.</p>}<h3>Версии недели</h3>{history.weeks?.length ? history.weeks.map((item, position) => <article className="constructor-draft" key={item.effective_week_id}><div className="constructor-draft-open"><strong>{item.is_current_publication ? 'Действующая публикация' : 'Историческая версия'} · {history.weeks!.length - position}</strong><span>{changedAt(item.revision_at || item.created_at)}</span></div><div className="constructor-draft-actions"><button onClick={() => void showWeek(item)}>Просмотреть</button>{!readOnly ? <button disabled={creating} onClick={() => void editHistoricalWeek(item)}>Создать черновик версии</button> : null}</div></article>) : <p>Сохранённых версий недели нет.</p>}<h3>Публикации</h3>{history.publications?.length ? history.publications.map(item => <p key={item.id}>{changedAt(item.created_at)} · ревизия черновика {item.draft_revision}</p>) : <p>Записей публикации нет.</p>}</dialog></div> : null}
    {view ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-view-title" onCancel={() => setView(null)}><header><div><small>Только просмотр сохранённой версии</small><h2 id="week-view-title">{view.title}</h2></div><button aria-label="Закрыть просмотр недели" onClick={() => setView(null)}><X size={20} /></button></header>{view.loading ? <p>Загружаем занятия…</p> : view.error ? <p role="alert">{view.error}</p> : view.blocks?.map((block, position) => <article className="constructor-history-lesson" key={`${block.block_key}:${position}`}><strong>{SHORT_WEEKDAYS[block.weekday]} · {formatLessonTimeRange(block.start_time, block.end_time)}</strong>{block.change_kind === 'cancelled' ? <p>Занятие отменено в этой версии</p> : (block.effective_assignments || []).map((item, i) => <p key={i}>{item.activity} · {item.groups?.join(', ') || `${block.grade_scope || ''} параллель`} · {item.teachers?.join(', ') || 'Преподаватель не указан'}{item.room ? ` · ${normalizeRoomDisplay(item.room)}` : ''}</p>)}</article>)}</dialog></div> : null}
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
  </section>;
}
