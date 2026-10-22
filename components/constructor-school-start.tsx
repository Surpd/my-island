'use client';

import { useEffect, useState } from 'react';
import { X } from 'lucide-react';

export type SchoolDraftSummary = { id: string; name: string; revision: number; lesson_count: number; updated_at?: string; week_start?: string | null; is_primary_template?: boolean; has_publications?: boolean; draft_kind: string; base_version_id?: string | null; source_template?: { id?: string; revision?: number } | null };
export type EffectiveWeekSummary = { effective_week_id: string; version_id: string; week_start: string; status: string; created_at: string; revision_at?: string; is_current_publication?: boolean };
export type WeekPublication = { id: string; draft_id: string; draft_revision: number; scope_key: string; published_id: string; created_at: string };
export type SchoolWeekIndex = { effective_weeks?: EffectiveWeekSummary[]; week_publications?: WeekPublication[]; canonical?: { version_id: string; status: string }; load_error?: string };
type Api = <T>(path: string, init?: RequestInit) => Promise<T>;

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

export function ConstructorSchoolStart({ api, drafts, index, hiddenIds, readOnly, initialSection = 'template', onOpen, onCopy, onHide, onRestore, onCreateEmpty, onCreated, onPublication }: {
  api: Api; drafts: SchoolDraftSummary[]; index: SchoolWeekIndex; hiddenIds: string[]; readOnly: boolean;
  initialSection?: 'template' | 'weeks';
  onOpen: (id: string) => Promise<void>; onCopy: (draft: SchoolDraftSummary) => void;
  onHide: (id: string) => void; onRestore: (id: string) => void; onCreateEmpty: () => void;
  onCreated: (id: string) => Promise<void>; onPublication: (id: string) => Promise<void>;
}) {
  const [section, setSection] = useState<'template' | 'weeks'>(initialSection);
  const [weekStart, setWeekStart] = useState(nearestMonday);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState('');
  const [restoredTests, setRestoredTests] = useState<string[]>([]);
  useEffect(() => { try { const saved = JSON.parse(localStorage.getItem('my-island-visible-test-drafts:v1') || '[]'); setRestoredTests(Array.isArray(saved) ? saved.filter(item => typeof item === 'string') : []); } catch { /* Optional browser preference. */ } }, []);
  const [history, setHistory] = useState<{ title: string; publications?: WeekPublication[]; weeks?: EffectiveWeekSummary[]; drafts?: SchoolDraftSummary[] } | null>(null);
  const [view, setView] = useState<{ title: string; loading?: boolean; blocks?: Record<string, any>[]; error?: string } | null>(null);
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
      const result = await api<{ blocks: Record<string, any>[] }>(`/api/admin/schedule/observability?editor=true&effective_week_id=${encodeURIComponent(item.effective_week_id)}`);
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
    <div className="constructor-draft-actions"><button disabled={readOnly} onClick={() => onCopy(item)}>Создать копию</button>{item.is_primary_template ? null : archive ? <button onClick={() => restore(item)}>Вернуть в список</button> : <button onClick={() => onHide(item.id)}>Скрыть</button>}</div>
  </article>;
  return <section className="constructor-start constructor-school-start">
    <nav className="constructor-draft-filters" aria-label="Разделы расписания"><button aria-current={section === 'template' ? 'page' : undefined} className={section === 'template' ? 'active' : ''} onClick={() => setSection('template')}>Основной шаблон</button><button aria-current={section === 'weeks' ? 'page' : undefined} className={section === 'weeks' ? 'active' : ''} onClick={() => setSection('weeks')}>Недели <small>{weeks.length}</small></button></nav>
    {message ? <p role="alert">{message}</p> : null}{index.load_error ? <p role="alert">Не удалось получить историю недель: {index.load_error}. Сохранённые черновики показаны отдельно.</p> : null}
    {section === 'template' ? <section className="constructor-template-overview">
      <small className="constructor-section-caption">РЕДАКТОРСКАЯ ВЕРСИЯ</small>
      <h2>{primary?.name || 'Основной шаблон не назначен'}</h2>
      {primary ? <><p>{primary.lesson_count} занятий · ревизия {primary.revision} · изменён {changedAt(primary.updated_at)}</p><div className="constructor-version-status"><div><strong>Сохранён в редакторе</strong><span>Из этой ревизии создаются независимые недели.</span></div><div><strong>{index.canonical ? 'Опубликованная основа' : 'Опубликованная основа не получена'}</strong><span>{index.canonical ? `${({ authoritative: 'Подтверждена', approved: 'Подтверждена', approved_with_exceptions: 'Подтверждена с исключениями', superseded: 'Историческая версия' } as Record<string, string>)[index.canonical.status] || index.canonical.status}. Сохранённый редакторский шаблон учитывается отдельно.` : 'Проверьте подключение к школьной базе.'}</span>{index.canonical ? <code>{index.canonical.version_id}</code> : null}</div></div><div className="constructor-start-primary-actions"><button className="admin-button admin-button--primary" onClick={() => void onOpen(primary.id)}>Открыть и редактировать</button><button onClick={() => void templateHistory()}>История версий</button><button disabled={readOnly} onClick={() => onCopy(primary)}>Создать отдельную копию</button>{!readOnly ? <button onClick={() => void onPublication(primary.id)}>Проверить публикацию</button> : null}</div><p className="constructor-muted">Публикация выполняется после проверки и отдельного подтверждения.</p></> : <p>Назначьте проверенный редакторский шаблон. Автоматическая замена canonical-копией не выполняется.</p>}
      {others.length ? <details className="constructor-secondary-actions"><summary>Другие черновики шаблона · {others.length}</summary>{others.map(item => draftCard(item))}</details> : null}
    </section> : <>
      <div className="constructor-section-heading"><div><h2>Недели</h2><p>Сохранённые черновики и история недель по датам.</p></div><a href="#constructor-create-week">Создать неделю</a></div>
      <section className="constructor-week-list" aria-label="Все недели">{weeks.length ? weeks.map(week => {
        const saved = weekDrafts.filter(item => item.week_start === week), versions = effective.filter(item => item.week_start === week);
        const current = versions.find(item => item.is_current_publication), latest = current || versions[0];
        return <article className="constructor-draft constructor-week-row" key={week}><div className="constructor-draft-open"><strong>{weekPeriod(week)}</strong><div className="constructor-week-status">{saved.length ? <span>Сохранённый черновик{saved.length > 1 ? ` · ${saved.length}` : ''}</span> : null}{current ? <span>Действующая неделя</span> : versions.length ? <span>Историческая версия</span> : null}</div><span>{versions.length ? `Версий недели: ${versions.length}` : 'Сохранён только черновик'}</span></div><div className="constructor-draft-actions">{saved.length ? <button onClick={() => void onOpen(saved[0].id)}>Открыть черновик</button> : latest ? <button onClick={() => void showWeek(latest)}>Просмотреть</button> : null}<button onClick={() => showHistory(week)}>История</button>{!saved.length && latest && !readOnly ? <button disabled={creating} onClick={() => void editHistoricalWeek(latest)}>Создать черновик версии</button> : null}</div></article>;
      }) : <p>Сохранённых недель пока нет.</p>}</section>
      <section id="constructor-create-week" className="constructor-create-week"><h3>Создать неделю</h3><p>{primary ? <>Источник: <strong>{primary.name}</strong> · редакторская ревизия {primary.revision}, {primary.lesson_count} занятий.</> : 'Сначала выберите основной редакторский шаблон.'}</p><div className="constructor-week-form"><label htmlFor="constructor-week-start">Неделя начинается<input id="constructor-week-start" type="date" value={weekStart} onChange={event => { setWeekStart(event.target.value); setMessage(''); }} /></label><div className="constructor-week-period" aria-live="polite">{weekPeriod(weekStart)}</div></div>{weekExists ? <div className="constructor-existing-week"><p>Эта неделя уже существует. Сначала откройте её черновик или историю.</p>{existingDrafts.length ? <button onClick={() => void onOpen(existingDrafts[0].id)}>Открыть существующий черновик</button> : <button onClick={() => showHistory(weekStart)}>Открыть историю недели</button>}</div> : <button className="admin-button admin-button--primary" disabled={readOnly || !primary || !weekStart || creating} onClick={() => void createWeek()}>{creating ? 'Создаём черновик…' : 'Создать из основного шаблона'}</button>}<p className="constructor-muted">Создаётся самостоятельный черновик. Публикация доступна отдельным действием.</p></section>
    </>}
    <details className="constructor-secondary-actions"><summary>Другие черновики и архив · {(section === 'weeks' ? others.length : 0) + archived.length}</summary><p className="constructor-muted">Тестовые и скрытые записи сохранены. Скрытие действует только в этом браузере.</p>{section === 'weeks' ? others.map(item => draftCard(item)) : null}{archived.length ? <details><summary>Тестовые и скрытые записи · {archived.length}</summary>{archived.map(item => draftCard(item, true))}</details> : null}</details>
    <details className="constructor-secondary-actions"><summary>Дополнительно</summary><button disabled={readOnly} onClick={onCreateEmpty}>Создать пустое расписание</button></details>
    {history ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-history-title" onCancel={() => setHistory(null)}><header><h2 id="week-history-title">История · {history.title}</h2><button aria-label="Закрыть историю" onClick={() => setHistory(null)}><X size={20} /></button></header><h3>Сохранённые черновики</h3>{history.drafts?.length ? history.drafts.map(item => draftCard(item)) : <p>Отдельного черновика нет.</p>}<h3>Версии недели</h3>{history.weeks?.length ? history.weeks.map((item, position) => <article className="constructor-draft" key={item.effective_week_id}><div className="constructor-draft-open"><strong>{item.is_current_publication ? 'Действующая публикация' : 'Историческая версия'} · {history.weeks!.length - position}</strong><span>{changedAt(item.revision_at || item.created_at)}</span><code>{item.version_id}</code></div><div className="constructor-draft-actions"><button onClick={() => void showWeek(item)}>Просмотреть</button>{!readOnly ? <button disabled={creating} onClick={() => void editHistoricalWeek(item)}>Создать черновик версии</button> : null}</div></article>) : <p>Сохранённых версий недели нет.</p>}<h3>Публикации</h3>{history.publications?.length ? history.publications.map(item => <p key={item.id}>{changedAt(item.created_at)} · ревизия черновика {item.draft_revision}</p>) : <p>Записей публикации нет.</p>}</dialog></div> : null}
    {view ? <div className="constructor-conflict-backdrop"><dialog className="constructor-conflict-dialog constructor-week-history" ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="week-view-title" onCancel={() => setView(null)}><header><div><small>Только просмотр сохранённой версии</small><h2 id="week-view-title">{view.title}</h2></div><button aria-label="Закрыть просмотр недели" onClick={() => setView(null)}><X size={20} /></button></header>{view.loading ? <p>Загружаем занятия…</p> : view.error ? <p role="alert">{view.error}</p> : view.blocks?.map((block, position) => <article className="constructor-history-lesson" key={`${block.block_key}:${position}`}><strong>{['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][block.weekday]} · {block.start_time}–{block.end_time}</strong>{block.change_kind === 'cancelled' ? <p>Занятие отменено в этой версии</p> : (block.effective_assignments || []).map((item: Record<string, any>, i: number) => <p key={i}>{item.activity} · {item.groups?.join(', ') || `${block.grade_scope || ''} параллель`} · {item.teachers?.join(', ') || 'Преподаватель не указан'}{item.room ? ` · ${item.room}` : ''}</p>)}</article>)}</dialog></div> : null}
  </section>;
}
