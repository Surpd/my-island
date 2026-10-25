'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Eye, RefreshCw, ShieldCheck, X } from 'lucide-react';
import './miniapp-preview.css';

type Api = <T>(path: string, options?: RequestInit) => Promise<T>;
type Person = { identity_id: string | number; kind: string; display_name: string; class_name?: string; groups: Array<{ id: string | number; name: string }> };
type Catalog = { items: Person[]; groups: Array<{ id: string | number; name: string }>; classes: string[] };
type Preview = { id: string; role: 'student' | 'teacher'; target: { identity_id: string | number; display_name: string; class_name?: string }; expires_at: string };
type Diagnostics = {
  identity: { display_name: string; class_name?: string };
  groups: Array<{ id: string | number; name: string }>;
  schedule: { state: string; lesson_count: number; issues?: unknown[] };
  journals: { state: string; count: number; mapping_state?: string };
  source_mode: string; data_source?: string; issues?: unknown[]; expires_at: string;
};
const dateToday = () => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`; };
const statusText = (state?: string) => ({ approved_baseline: 'Подтверждённое расписание', authoritative: 'Опубликованное расписание', approved_with_exceptions: 'Опубликовано с замечаниями', canonical_not_materialized: 'Опубликованная неделя отсутствует', not_published: 'Неделя не опубликована', canonical_backend_disabled: 'Источник расписания выключен', disabled: 'Источник выключен', week_not_available: 'Неделя не опубликована', no_effective_week: 'Неделя не опубликована', missing_mapping: 'Личность не сопоставлена с журналами', unlinked_mapping: 'Сопоставление не подтверждено', mapped_empty: 'Сопоставлено, результатов пока нет', has_results: 'Есть результаты', ready: 'Данные доступны', approved: 'Данные доступны', ok: 'Данные доступны', canonical: 'Опубликованное canonical-расписание' })[state || ''] || state || 'Не проверено';

export function MiniappPreview({ api, sourceLabel, initialIdentity = '', initialKind = 'student', initialDay = '', localAdmin = false }: {
  api: Api; sourceLabel: string; initialIdentity?: string; initialKind?: string; initialDay?: string; localAdmin?: boolean;
}) {
  const [role, setRole] = useState<'student' | 'teacher'>(initialKind === 'teacher' ? 'teacher' : 'student');
  const [query, setQuery] = useState('');
  const [className, setClassName] = useState('');
  const [group, setGroup] = useState('');
  const [catalog, setCatalog] = useState<Catalog>({ items: [], classes: [], groups: [] });
  const [identity, setIdentity] = useState(initialIdentity);
  const [day, setDay] = useState(() => /^\d{4}-\d{2}-\d{2}$/.test(initialDay) ? initialDay : dateToday());
  const [width, setWidth] = useState(390);
  const [height, setHeight] = useState(760);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [error, setError] = useState('');
  const [frameError, setFrameError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingEnd, setPendingEnd] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const active = useRef<Preview | null>(null);
  const generation = useRef(0);
  const request = useRef(0);

  const finish = useCallback(async () => {
    generation.current++;
    const old = active.current;
    active.current = null;
    setPreview(null); setDiagnostics(null); setFrameError('');
    if (old) {
      try { await api(`/api/admin/previews/${encodeURIComponent(old.id)}/end`, { method: 'POST', keepalive: true }); }
      catch (cause) { setPendingEnd((ids) => [...new Set([...ids, old.id])]); setError('Не удалось подтвердить завершение сессии. Повторите завершение; срок сессии ограничен.'); throw cause; }
    }
  }, [api]);

  useEffect(() => {
    const close = () => {
      generation.current++;
      const old = active.current; active.current = null;
      if (old) void api(`/api/admin/previews/${encodeURIComponent(old.id)}/end`, { method: 'POST', keepalive: true }).catch(() => undefined);
    };
    window.addEventListener('pagehide', close);
    return () => { window.removeEventListener('pagehide', close); close(); };
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ role, q: query, class_name: className, group_id: group });
    const timer = window.setTimeout(() => {
      setLoading(true); setError('');
      void api<Catalog>(`/api/admin/previews/people?${params}`, { signal: controller.signal })
        .then((data) => { if (!controller.signal.aborted) setCatalog(data); })
        .catch((cause) => { if (!controller.signal.aborted) { setCatalog({ items: [], groups: [], classes: [] }); setError(cause instanceof Error ? cause.message : 'Не удалось прочитать школьный справочник'); } })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 180);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, role, query, className, group]);

  useEffect(() => {
    const url = new URL(window.location.href);
    const hashRoute = url.hash.replace(/^#/, '');
    const route = hashRoute.startsWith('/admin') ? new URL(hashRoute, url.origin) : url;
    if (identity) route.searchParams.set('identity_id', identity); else route.searchParams.delete('identity_id');
    route.searchParams.set('kind', role); route.searchParams.set('day', day);
    if (hashRoute.startsWith('/admin')) url.hash = `${route.pathname}${route.search}`;
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }, [identity, role, day]);

  const sendConfig = useCallback(() => {
    if (!preview || !frame.current?.contentWindow) return;
    frame.current.contentWindow.postMessage({ type: 'my-island:preview-config', id: preview.id, role: preview.role, day,
      initData: window.Telegram?.WebApp?.initData || window.sessionStorage.getItem('my_island_telegram_init_data') || '',
      localAdmin, revision }, window.location.origin);
  }, [preview, day, localAdmin, revision]);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === 'my-island:preview-ready') sendConfig();
      if (event.data?.type === 'my-island:preview-error') setFrameError(String(event.data.message || 'Не удалось загрузить интерфейс'));
      if (event.data?.type === 'my-island:preview-loaded') setFrameError('');
      if (event.data?.type === 'my-island:preview-date' && /^\d{4}-\d{2}-\d{2}$/.test(event.data.day)) setDay(event.data.day);
      if (event.data?.type === 'my-island:preview-close') void finish().catch(() => undefined);
    };
    window.addEventListener('message', receive); sendConfig();
    return () => window.removeEventListener('message', receive);
  }, [sendConfig, finish]);

  useEffect(() => {
    if (!preview) return;
    const controller = new AbortController();
    const start = new Date(`${day}T12:00:00`); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    const end = new Date(start); end.setDate(end.getDate() + 6);
    const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    void api<Diagnostics>(`/api/admin/previews/${encodeURIComponent(preview.id)}/diagnostics?${new URLSearchParams({ start_day: iso(start), end_day: iso(end) })}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setDiagnostics(data); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Диагностика недоступна'); });
    return () => controller.abort();
  }, [api, preview, day, revision]);

  const open = useCallback(async (target = identity) => {
    if (!target || busy) return;
    setBusy(true); setError('');
    try {
      await finish();
      const version = generation.current;
      const next = await api<Preview>('/api/admin/previews', { method: 'POST', body: JSON.stringify({ target_identity_id: target, role }) });
      if (generation.current !== version) { await api(`/api/admin/previews/${encodeURIComponent(next.id)}/end`, { method: 'POST' }); return; }
      active.current = next; setPreview(next); setFrameError('');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Предпросмотр не открылся'); }
    finally { setBusy(false); }
  }, [api, busy, finish, identity, role]);

  // A deep link contains an identity, never an authentication or preview token.
  useEffect(() => {
    if (!initialIdentity || request.current) return;
    request.current++;
    void open(initialIdentity);
  }, [initialIdentity, open]);

  return <div className="miniapp-preview">
    <div className="admin-callout admin-callout--safe"><ShieldCheck size={18} /><span>Только чтение · {sourceLabel}. Предпросмотр не меняет ваши роли и профиль.</span></div>
    {error ? <div className="admin-form-error" role="alert">{error}</div> : null}
    {pendingEnd.length ? <button className="admin-button" onClick={async () => { try { await Promise.all(pendingEnd.map((id) => api(`/api/admin/previews/${encodeURIComponent(id)}/end`, { method: 'POST' }))); setPendingEnd([]); setError(''); } catch { setError('Завершение ещё не подтверждено сервером. Повторите позже.'); } }}>Повторить завершение сессии</button> : null}
    <div className="miniapp-preview-grid">
      <section className="admin-panel miniapp-preview-controls" aria-label="Настройки предпросмотра">
        <h2>Выберите человека</h2>
        <label>Режим<select aria-label="Режим" value={role} disabled={busy} onChange={(event) => { void finish().catch(() => undefined); setRole(event.target.value as 'student' | 'teacher'); setIdentity(''); setClassName(''); setGroup(''); }}><option value="student">Ученик</option><option value="teacher">Учитель</option></select></label>
        <label>Поиск человека<input aria-label="Поиск человека" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя или фамилия" /></label>
        <div className="miniapp-preview-filters"><label>Класс<select aria-label="Класс" value={className} onChange={(event) => setClassName(event.target.value)}><option value="">Все классы</option>{catalog.classes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label>Группа<select aria-label="Группа" value={group} onChange={(event) => setGroup(event.target.value)}><option value="">Все группы</option>{catalog.groups.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>
        <label>Личность<select aria-label="Личность" value={identity} disabled={loading || busy} onChange={(event) => { setIdentity(event.target.value); if (active.current) void finish().catch(() => undefined); }}><option value="">{loading ? 'Загрузка…' : 'Выберите человека'}</option>{catalog.items.map((person) => <option key={person.identity_id} value={person.identity_id}>{person.display_name}{person.class_name ? ` · ${person.class_name}` : ''}</option>)}</select></label>
        {!loading && !catalog.items.length && !error ? <output>По этим условиям людей не найдено.</output> : null}
        <label>Дата<input type="date" aria-label="Дата" value={day} onChange={(event) => { if (/^\d{4}-\d{2}-\d{2}$/.test(event.target.value)) setDay(event.target.value); }} /></label>
        <div className="miniapp-preview-filters"><label>Ширина<select aria-label="Ширина" value={width} onChange={(event) => setWidth(Number(event.target.value))}>{[360, 390, 430].map((size) => <option key={size} value={size}>{size} px</option>)}</select></label><label>Высота<input type="number" min={480} max={1100} step={20} aria-label="Высота" value={height} onChange={(event) => setHeight(Math.max(480, Math.min(1100, Number(event.target.value) || 760)))} /></label></div>
        <button className="admin-button admin-button--primary" disabled={!identity || busy || loading} onClick={() => void open()}><Eye size={17} />{busy ? 'Открываем…' : 'Открыть приложение'}</button>
        {preview ? <><button className="admin-button" onClick={() => { setError(''); setRevision((value) => value + 1); }}><RefreshCw size={16} />Обновить данные</button><button className="admin-button admin-button--quiet" onClick={() => void finish().catch(() => undefined)}><X size={16} />Завершить предпросмотр</button></> : null}
        <section className="miniapp-preview-diagnostics" aria-label="Диагностика предпросмотра"><h3>Диагностика</h3>
          {!preview ? <p>Выберите человека и откройте приложение.</p> : !diagnostics ? <output>{error ? 'Диагностика не получена' : 'Проверяем доступные данные…'}</output> : <>
            <dl><dt>Личность</dt><dd>{diagnostics.identity.display_name}</dd><dt>Источник</dt><dd>{diagnostics.data_source === 'local_test' ? 'Изолированная тестовая база' : diagnostics.data_source === 'school' ? 'Школьная база' : sourceLabel}</dd><dt>Группы</dt><dd>{diagnostics.groups.map((item) => item.name).join(', ') || 'Нет действующих назначений'}</dd><dt>Расписание</dt><dd>{statusText(diagnostics.schedule.state)} · {diagnostics.schedule.lesson_count} занятий за неделю</dd><dt>Журналы</dt><dd>{diagnostics.journals.count} · {statusText(diagnostics.journals.mapping_state || diagnostics.journals.state)}</dd><dt>Сессия до</dt><dd>{new Date(/[Zz]|[+-]\d{2}:\d{2}$/.test(preview.expires_at) ? preview.expires_at : `${preview.expires_at}Z`).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })}</dd></dl>
            {(diagnostics.issues?.length || diagnostics.schedule.issues?.length) ? <details><summary>Есть замечания источников</summary><p>Проверьте сопоставления и опубликованную неделю в разделах школы. Замечаний: {(diagnostics.issues?.length || 0) + (diagnostics.schedule.issues?.length || 0)}.</p></details> : null}
          </>}
          {frameError ? <p className="admin-form-error" role="alert">{frameError}</p> : null}
        </section>
      </section>
      <section className="miniapp-preview-stage" aria-label="Мобильное приложение">
        {preview ? <><p className="miniapp-preview-caption">{preview.target.display_name} · {preview.role === 'student' ? 'Ученик' : 'Учитель'} · {width} × {height}</p><div className="miniapp-phone-scroll"><iframe key={preview.id} ref={frame} title="Предпросмотр My Island" src="/?embeddedPreview=1" style={{ width, height }} sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" onLoad={sendConfig} /></div></> : <div className="miniapp-preview-placeholder"><Eye size={32} /><h2>Настоящий Mini App</h2><p>Выберите ученика или преподавателя слева. Приложение откроется здесь с его доступными данными.</p></div>}
      </section>
    </div>
  </div>;
}
