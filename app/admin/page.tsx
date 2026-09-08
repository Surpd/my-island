/* oxlint-disable */
'use client';

import { useEffect, useState, type ReactNode, type SyntheticEvent } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Database,
  FileSearch,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Users,
} from 'lucide-react';

type RecordValue = Record<string, any>;
type Session = { id: string | number; role: string; identity_id?: string | number | null };
type ApiError = Error & { status?: number };

const runtimeEnv =
  (import.meta as ImportMeta & { env?: Record<string, string | boolean> }).env || {};
const isLocalBuild = runtimeEnv.DEV === true || runtimeEnv.MODE === 'development';
const API_BASE = String(
  runtimeEnv.VITE_API_BASE_URL ||
    (isLocalBuild ? 'http://localhost:8000' : 'https://my-island-api.onrender.com'),
).replace(/\/$/, '');

const nav = [
  { href: '/admin', label: 'Обзор', icon: LayoutDashboard },
  { href: '/admin/people', label: 'People', icon: Users },
  { href: '/admin/groups', label: 'Groups', icon: Database },
  { href: '/admin/sources', label: 'School Data', icon: FileSearch },
  { href: '/admin/reconciliation', label: 'Reconciliation', icon: ShieldCheck },
  { href: '/admin/schedule', label: 'Schedule', icon: Activity },
  { href: '/admin/journals', label: 'Journals / Classroom', icon: BookOpen },
  { href: '/admin/audit', label: 'Audit', icon: FileSearch },
  { href: '/admin/system', label: 'System', icon: Server },
];

function localDevAuth() {
  if (typeof window === 'undefined') return null;
  const query = new URLSearchParams(window.location.search);
  return window.location.hostname === 'localhost' && query.get('role') === 'admin' ? 'admin:1' : null;
}

function preserveDevQuery(href: string) {
  return localDevAuth() ? `${href}?role=admin` : href;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const dev = localDevAuth();
  if (dev) headers.set('X-Dev-Auth', dev);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: 'include' });
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const error = new Error(String(body.detail || body.error || `Request failed: ${response.status}`)) as ApiError;
    error.status = response.status;
    throw error;
  }
  return body as T;
}

function display(value: unknown, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatDate(value: unknown) {
  if (!value) return '—';
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString('ru-RU');
}

function Status({ value, tone }: { value: unknown; tone?: 'good' | 'warn' | 'bad' | 'neutral' }) {
  const text = display(value);
  return <span className={`admin-status admin-status--${tone || (text === 'healthy' || text === 'ok' || text === 'applied' ? 'good' : text === 'attention' || text === 'pending' ? 'warn' : 'neutral')}`}>{text}</span>;
}

function Empty({ children = 'Нет данных' }: { children?: ReactNode }) {
  return <div className="admin-empty">{children}</div>;
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return <div className="admin-error"><AlertTriangle size={17} /><div><strong>Не удалось загрузить раздел</strong><p>{display(error instanceof Error ? error.message : error)}</p></div>{onRetry && <button className="admin-button admin-button--quiet" onClick={onRetry}>Повторить</button>}</div>;
}

function Loading() {
  return <div className="admin-loading"><RefreshCw size={17} className="admin-spin" /> Загружаем раздел…</div>;
}

function useRoute() {
  const [path, setPath] = useState('/admin');
  useEffect(() => {
    const update = () => setPath(window.location.pathname || '/admin');
    update();
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  const navigate = (href: string) => {
    window.history.pushState({}, '', preserveDevQuery(href));
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.scrollTo({ top: 0, behavior: 'instant' });
  };
  return { path, navigate };
}

function Login({ onLoggedIn }: { onLoggedIn: (session: Session) => void }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await api<{ user: Session }>('/api/admin/auth/exchange', { method: 'POST', body: JSON.stringify({ code }) });
      onLoggedIn(result.user);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Код не принят');
    } finally {
      setBusy(false);
    }
  };
  return <main className="admin-login"><div className="admin-login-card"><div className="admin-logo">MI</div><p className="admin-eyebrow">MY ISLAND / ADMIN</p><h1>Вход в Admin Console</h1><p>Получите одноразовый код через Telegram-бота, затем введите его здесь. Код действует ограниченное время и используется один раз.</p><form onSubmit={submit}><label>Одноразовый код<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="Вставьте код" /></label>{error && <div className="admin-form-error">{error}</div>}<button className="admin-button admin-button--primary" disabled={busy || !code.trim()}>{busy ? 'Проверяем…' : 'Войти безопасно'}</button></form><small>Student и Teacher Mini App продолжают использовать Telegram initData.</small></div></main>;
}

function Sidebar({ path, navigate, onLogout }: { path: string; navigate: (href: string) => void; onLogout: () => void }) {
  return <aside className="admin-console-sidebar"><div className="admin-console-brand"><div className="admin-logo admin-logo--small">MI</div><div><strong>My Island</strong><small>Admin Console</small></div></div><nav>{nav.map((item) => { const Icon = item.icon; const active = item.href === '/admin' ? path === '/admin' : path.startsWith(item.href); return <button key={item.href} className={active ? 'is-active' : ''} onClick={() => navigate(item.href)}><Icon size={17} /><span>{item.label}</span>{active && <ChevronRight size={14} />}</button>; })}</nav><div className="admin-console-sidebar__footer"><span><span className="admin-online-dot" /> Browser session</span><button onClick={onLogout}><LogOut size={16} /> Выйти</button></div></aside>;
}

function Header({ path, navigate, session }: { path: string; navigate: (href: string) => void; session: Session }) {
  const title = path === '/admin' ? 'Обзор системы' : path.includes('/people/') ? 'Person detail' : path.includes('/groups/') ? 'Group detail' : nav.find((item) => path.startsWith(item.href) && item.href !== '/admin')?.label || 'Admin Console';
  return <header className="admin-console-header"><div><div className="admin-breadcrumb"><button onClick={() => navigate('/admin')}>Admin</button><ChevronRight size={13} /><span>{title}</span></div><h1>{title}</h1></div><div className="admin-console-header__meta"><Status value="local / connected" tone="good" /><span>Admin #{display(session.id)}</span></div></header>;
}

function Overview({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; overview?: RecordValue; system?: RecordValue; sources?: RecordValue[]; reconciliation?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); Promise.all([api<RecordValue>('/api/admin/overview'), api<RecordValue>('/api/admin/system'), api<{ items: RecordValue[] }>('/api/admin/sources'), api<RecordValue>('/api/admin/reconciliation/student-memberships/latest')]).then(([overview, system, sources, reconciliation]) => setState({ loading: false, overview, system, sources: sources.items, reconciliation })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const overview = state.overview || {};
  const summary = state.reconciliation?.payload?.summary || {};
  const cards = [
    ['Students', overview.users, 'accounts'], ['Groups', overview.groups, 'canonical groups'], ['Unlinked accounts', overview.unlinked_accounts, 'needs attention'], ['Parse issues', overview.parse_issues, 'schedule'], ['Pending claims', overview.pending_claims, 'identity review'], ['Reconciliation', state.reconciliation?.status || 'not_run', 'latest run'],
  ];
  return <div className="admin-stack"><div className="admin-page-actions"><p>Операционный health без декоративных метрик: что можно доверенно администрировать сейчас.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><section className="admin-metric-grid">{cards.map(([label, value, hint]) => <div className="admin-metric" key={String(label)}><span>{label}</span><strong>{display(value, '0')}</strong><small>{hint}</small></div>)}</section><div className="admin-two-col"><section className="admin-panel"><div className="admin-panel__heading"><div><p className="admin-eyebrow">SCHOOL DATA</p><h2>Источники</h2></div><button className="admin-link" onClick={() => navigate('/admin/sources')}>Открыть health <ChevronRight size={14} /></button></div>{state.sources?.length ? <div className="admin-list">{state.sources.slice(0, 6).map((source) => <button className="admin-list-row" key={source.id} onClick={() => navigate(`/admin/sources/${source.id}`)}><span><strong>{display(source.display_name)}</strong><small>{display(source.source_type)} · {formatDate(source.last_refresh)}</small></span><Status value={source.health} /></button>)}</div> : <Empty>Источники ещё не зарегистрированы</Empty>}</section><section className="admin-panel"><div className="admin-panel__heading"><div><p className="admin-eyebrow">RECONCILIATION</p><h2>Последний reviewed state</h2></div><button className="admin-link" onClick={() => navigate('/admin/reconciliation')}>Открыть diff <ChevronRight size={14} /></button></div>{state.reconciliation?.payload ? <div className="admin-inline-stats">{['KEEP', 'CREATE', 'DEACTIVATE', 'PROTECTED', 'UNRESOLVED'].map((key) => <div key={key}><strong>{display(summary[key], '0')}</strong><span>{key}</span></div>)}</div> : <Empty>Dry-run ещё не запускался из Admin</Empty>}<div className="admin-callout admin-callout--muted"><ShieldCheck size={16} /><span>Production apply доступен только после server-side review и revalidation текущего source state.</span></div></section></div><section className="admin-panel"><div className="admin-panel__heading"><div><p className="admin-eyebrow">SYSTEM</p><h2>Schema / runtime</h2></div><button className="admin-link" onClick={() => navigate('/admin/system')}>Подробнее <ChevronRight size={14} /></button></div><div className="admin-system-strip"><Status value={state.system?.health?.schema_status} /><span>{state.system?.health?.environment}</span><span>{state.system?.settings?.database_configured ? 'database configured' : 'local database'}</span><span>{formatDate(new Date().toISOString())}</span></div></section></div>;
}

function People({ navigate }: { navigate: (href: string) => void }) {
  const [items, setItems] = useState<RecordValue[]>([]);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [state, setState] = useState<{ loading: boolean; error?: unknown }>({ loading: true });
  const load = () => { setState({ loading: true }); const params = new URLSearchParams(); if (query) params.set('q', query); if (kind) params.set('kind', kind); api<{ items: RecordValue[] }>(`/api/admin/people?${params}`).then((result) => { setItems(result.items); setState({ loading: false }); }).catch((error) => setState({ loading: false, error })); };
  useEffect(() => { const timer = window.setTimeout(load, 220); return () => window.clearTimeout(timer); }, [query, kind]);
  return <div className="admin-stack"><div className="admin-toolbar"><label className="admin-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Искать по canonical name, классу или группе" /></label><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">Все роли</option><option value="student">Students</option><option value="teacher">Teachers</option></select></div>{state.loading ? <Loading /> : state.error ? <ErrorState error={state.error} onRetry={load} /> : <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} записей</span><span>server-side query</span></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Person</th><th>Kind / class</th><th>Account</th><th>Memberships</th><th>Issues</th></tr></thead><tbody>{items.map((person) => <tr key={person.id} onClick={() => navigate(`/admin/people/${person.id}`)}><td><strong>{display(person.display_name)}</strong><small>ID {display(person.id)}</small></td><td><span>{display(person.kind)}</span><small>{display(person.class_name)}</small></td><td><Status value={person.account_status || (person.user_id ? 'linked' : 'no account')} tone={person.user_id ? 'good' : 'warn'} /></td><td><span>{Array.isArray(person.instructional_memberships) ? person.instructional_memberships.length : display(person.groups?.length, '0')}</span><small>{person.base_classes?.map((item: RecordValue) => item.name).join(', ') || 'no base class'}</small></td><td>{person.unresolved_count ? <Status value={`${person.unresolved_count} open`} tone="warn" /> : <Status value="clean" tone="good" />}</td></tr>)}</tbody></table></div> : <Empty>People не найдены</Empty>}</section>}</div>;
}

function PersonDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; person?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(`/api/admin/people/${encodeURIComponent(id)}`).then((person) => setState({ loading: false, person })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const person = state.person || {};
  const renderRows = (title: string, values: unknown[], fields: string[]) => <section className="admin-panel"><div className="admin-panel__heading"><h2>{title}</h2></div>{values.length ? <div className="admin-detail-list">{values.map((value: any, index) => <div key={`${title}-${index}`}><strong>{display(value.display_name || value.name || value.subject || value.group_name)}</strong><small>{fields.map((field) => `${field}: ${display(value[field])}`).join(' · ')}</small></div>)}</div> : <Empty>Нет записей</Empty>}</section>;
  return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/people')}><ArrowLeft size={15} /> Все people</button><section className="admin-panel admin-identity-card"><div className="admin-avatar">{display(person.display_name, '?').slice(0, 1)}</div><div><p className="admin-eyebrow">CANONICAL IDENTITY</p><h2>{display(person.display_name)}</h2><p>{display(person.kind)} · ID {display(person.id)} · {display(person.status)}</p></div><div className="admin-identity-card__meta"><Status value={person.account_status || 'no account'} /><span>class: {display(person.class_name)}</span></div></section><div className="admin-two-col"><section className="admin-panel"><h2>Current school state</h2><dl className="admin-definition-list"><dt>Base classes</dt><dd>{person.base_classes?.map((item: RecordValue) => item.name).join(', ') || '—'}</dd><dt>Instructional memberships</dt><dd>{person.instructional_memberships?.map((item: RecordValue) => item.name).join(', ') || '—'}</dd><dt>Exam / profile facts</dt><dd>{person.exam_profile_memberships?.map((item: RecordValue) => item.name).join(', ') || '—'}</dd><dt>Roles</dt><dd>{person.roles?.join(', ') || '—'}</dd></dl></section><section className="admin-panel"><h2>Provenance / issues</h2><div className="admin-callout admin-callout--muted"><FileSearch size={16} /><span>Source identities: {display(person.source_identities?.length, '0')}<br />Open issues: {display(person.unresolved_count, '0')}<br />Unresolved and protected cases remain explicit; no automatic merge is performed.</span></div></section></div>{renderRows('Memberships', person.memberships || person.instructional_memberships || [], ['active', 'valid_from', 'valid_until', 'source', 'source_ref'])}{renderRows('Teacher assignments', person.teacher_assignments || [], ['group_name', 'subject', 'active', 'source'])}{renderRows('Audit history', person.audit_history || [], ['action', 'created_at'])}</div>;
}

function Groups({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); api<{ items: RecordValue[] }>('/api/admin/groups').then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{state.items?.length || 0} canonical groups</span><span>stable membership ≠ lesson audience</span></div>{state.items?.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Group</th><th>Type / subject</th><th>Scope</th><th>Members</th><th>Teachers</th><th>Source</th></tr></thead><tbody>{state.items.map((group) => <tr key={group.id} onClick={() => navigate(`/admin/groups/${group.id}`)}><td><strong>{display(group.display_name || group.name)}</strong><small>key: {display(group.name)}</small></td><td><span>{display(group.group_type)}</span><small>{display(group.subject)}</small></td><td><span>{display(group.base_class_name)}</span><small>{display(group.subject_subgroup)} {display(group.exam_track, '')}</small></td><td>{display(group.student_count, '0')}</td><td>{display(group.teacher_assignments?.length, '0')}</td><td><small>{display(group.provenance_source)}</small></td></tr>)}</tbody></table></div> : <Empty>Canonical groups не найдены</Empty>}</section>;
}

function GroupDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; group?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(`/api/admin/groups/${encodeURIComponent(id)}`).then((group) => setState({ loading: false, group })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const group = state.group || {};
  return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/groups')}><ArrowLeft size={15} /> Все groups</button><section className="admin-panel"><p className="admin-eyebrow">CANONICAL GROUP</p><h2>{display(group.display_name || group.name)}</h2><div className="admin-chip-row"><Status value={group.group_type} /><span>key: {display(group.name)}</span><span>canonical: {display(group.canonical)}</span><span>source: {display(group.provenance_source)}</span></div></section><div className="admin-two-col"><section className="admin-panel"><h2>Metadata</h2><dl className="admin-definition-list"><dt>Subject</dt><dd>{display(group.subject)}</dd><dt>Base scope</dt><dd>{display(group.base_class_name)}</dd><dt>Subgroup</dt><dd>{display(group.subject_subgroup)}</dd><dt>Exam track</dt><dd>{display(group.exam_track)}</dd><dt>Provenance ref</dt><dd className="admin-mono">{display(group.provenance_ref)}</dd></dl></section><section className="admin-panel"><h2>Reconciliation boundary</h2><div className="admin-callout admin-callout--muted"><ShieldCheck size={16} /><span>Эта страница показывает canonical roster. Schedule audience и computed audience здесь намеренно не смешиваются.</span></div></section></div><section className="admin-panel"><h2>Active students ({display(group.student_count, '0')})</h2>{group.students?.length ? <div className="admin-detail-list">{group.students.map((student: RecordValue) => <button key={student.id} onClick={() => navigate(`/admin/people/${student.id}`)}><strong>{display(student.display_name)}</strong><small>{display(student.class_name)} · {display(student.source)}</small></button>)}</div> : <Empty>В roster нет активных учеников</Empty>}</section><section className="admin-panel"><h2>Teacher assignments</h2>{group.teacher_assignments?.length ? <div className="admin-detail-list">{group.teacher_assignments.map((teacher: RecordValue, index: number) => <div key={index}><strong>{display(teacher.display_name || teacher.teacher_name)}</strong><small>{display(teacher.subject)} · {display(teacher.active)}</small></div>)}</div> : <Empty>Назначений нет</Empty>}</section></div>;
}

function Sources({ id, navigate }: { id?: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; data?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(id ? `/api/admin/sources/${id}` : '/api/admin/sources').then((data) => setState({ loading: false, data })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  if (id) { const source = state.data?.source || {}; return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/sources')}><ArrowLeft size={15} /> Все sources</button><section className="admin-panel"><p className="admin-eyebrow">SOURCE DETAIL</p><h2>{display(source.display_name)}</h2><div className="admin-chip-row"><Status value={source.authority_status} /><span>{display(source.source_type)}</span><span>{display(source.external_key)}</span><span>{display(source.location_ref)}</span></div></section><div className="admin-two-col"><DataList title="Snapshots" items={state.data?.snapshots || []} fields={['fingerprint', 'status', 'observed_at']} /><DataList title="Sync runs" items={state.data?.runs || []} fields={['status', 'mode', 'started_at', 'finished_at']} /></div><div className="admin-two-col"><DataList title="Records" items={state.data?.records || []} fields={['record_key', 'change_kind', 'parse_status', 'source_ref']} /><DataList title="Issues / mappings" items={[...(state.data?.issues || []), ...(state.data?.mappings || [])]} fields={['issue_type', 'status', 'external_key', 'mapping_type']} /></div></div>; }
  const items = state.data?.items || []; return <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} registered sources</span><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Refresh</button></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Source</th><th>Authority</th><th>Last refresh</th><th>Fingerprint</th><th>Records</th><th>Issues</th></tr></thead><tbody>{items.map((source: RecordValue) => <tr key={source.id} onClick={() => navigate(`/admin/sources/${source.id}`)}><td><strong>{display(source.display_name)}</strong><small>{display(source.source_type)} · {display(source.external_key)}</small></td><td><Status value={source.authority_status} /></td><td>{formatDate(source.last_refresh)}<small>{display(source.last_run_status)}</small></td><td className="admin-mono">{display(source.current_fingerprint)}</td><td>{display(source.record_count, '0')}</td><td>{source.unresolved_count || source.candidate_change_count ? <Status value={`${display(source.unresolved_count, '0')} / ${display(source.candidate_change_count, '0')}`} tone="warn" /> : <Status value="0" tone="good" />}</td></tr>)}</tbody></table></div> : <Empty>School Data sources ещё не зарегистрированы</Empty>}</section>;
}

function DataList({ title, items, fields }: { title: string; items: RecordValue[]; fields: string[] }) {
  return <section className="admin-panel"><h2>{title} ({items.length})</h2>{items.length ? <div className="admin-detail-list">{items.slice(0, 50).map((item, index) => <div key={item.id || index}><strong>{display(item.display_name || item.record_key || item.issue_type || item.external_key || item.status)}</strong><small>{fields.map((field) => `${field}: ${display(item[field])}`).join(' · ')}</small></div>)}</div> : <Empty>Нет записей</Empty>}</section>;
}

function Reconciliation() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; run?: RecordValue }>({ loading: true });
  const [busy, setBusy] = useState('');
  const load = () => { setState({ loading: true }); api<RecordValue>('/api/admin/reconciliation/student-memberships/latest').then((run) => setState({ loading: false, run })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []);
  const act = async (action: string, path: string, init?: RequestInit) => { setBusy(action); try { const result = await api<RecordValue>(path, init); setState({ loading: false, run: result }); } catch (error) { setState((current) => ({ ...current, error })); } finally { setBusy(''); } };
  if (state.loading) return <Loading />;
  if (state.error && !state.run) return <ErrorState error={state.error} onRetry={load} />;
  const run = state.run || {}; const payload = (run.payload || {}) as RecordValue; const summary = (payload.summary || {}) as RecordValue; const assignments = (Array.isArray(payload.assignments) ? payload.assignments : []) as RecordValue[];
  const issueItems = (Array.isArray(payload.issues) ? payload.issues : []) as RecordValue[];
  const invariantItems = (Array.isArray(payload.invariants) ? payload.invariants : []) as RecordValue[];
  return <div className="admin-stack"><div className="admin-page-actions"><p>Read-only dry-run → explicit review → server-side revalidation → transactional apply.</p><div className="admin-button-row"><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void act('dry-run', '/api/admin/reconciliation/student-memberships/dry-run', { method: 'POST' })}><RefreshCw size={15} /> {busy === 'dry-run' ? 'Запускаем…' : 'Run dry-run'}</button>{run.status === 'ready_for_review' && <button className="admin-button admin-button--primary" disabled={!!busy} onClick={() => void act('review', `/api/admin/reconciliation/student-memberships/runs/${run.id}/review`, { method: 'POST', body: JSON.stringify({ approved: true }) })}>{busy === 'review' ? 'Сохраняем…' : 'Mark reviewed'}</button>}{run.status === 'approved' && <button className="admin-button admin-button--danger" disabled={!!busy} onClick={() => { if (window.confirm('Применить reviewed reconciliation после server-side revalidation?')) void act('apply', `/api/admin/reconciliation/student-memberships/apply?run_id=${run.id}`, { method: 'POST' }); }}>{busy === 'apply' ? 'Применяем…' : 'Apply reviewed plan'}</button>}</div></div>{Boolean(state.error) && <ErrorState error={state.error} />}{!payload.summary ? <section className="admin-panel"><Empty>Dry-run ещё не запускался. Текущие production reports не считаются автоматически reviewed run.</Empty></section> : <><section className="admin-panel"><div className="admin-panel__heading"><div><p className="admin-eyebrow">RUN #{display(run.id)} · {display(run.status)}</p><h2>Student membership reconciliation</h2></div><span>{formatDate(payload.generated_at)}</span></div><div className="admin-inline-stats">{['KEEP', 'CREATE', 'DEACTIVATE', 'PROTECTED', 'UNRESOLVED', 'WRONG_GROUP', 'STALE', 'invariant_violations'].map((key) => <div key={key} className={key === 'PROTECTED' ? 'is-protected' : key === 'UNRESOLVED' || key === 'invariant_violations' ? 'is-warning' : ''}><strong>{display(summary[key], '0')}</strong><span>{key}</span></div>)}</div><div className="admin-callout admin-callout--safe"><CheckCircle2 size={16} /><span>Mode: {display(payload.mode)}. Production writes recorded by dry-run: {display(payload.production_writes_performed, '0')}.</span></div></section><section className="admin-panel admin-table-panel"><div className="admin-panel__heading"><h2>Diff ({assignments.length})</h2><span>KEEP / CREATE / DEACTIVATE / PROTECTED / UNRESOLVED</span></div>{assignments.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Student</th><th>Group</th><th>Action</th><th>Classification</th><th>Reason / provenance</th></tr></thead><tbody>{assignments.map((item: RecordValue, index: number) => <tr key={item.membership_id || `${item.identity_id}-${item.group_id}-${index}`}><td><strong>{display(item.student)}</strong><small>{display(item.identity_id)}</small></td><td><span>{display(item.group)}</span><small>{display(item.group_id)}</small></td><td><Status value={item.action} tone={item.action === 'PROTECTED' ? 'warn' : item.action === 'CREATE' ? 'good' : item.action === 'DEACTIVATE' ? 'bad' : 'neutral'} /></td><td>{display(item.classification)}</td><td><span>{display(item.reason || item.stale_reason)}</span><small>{display(item.current_source_ref || item.source_refs?.join(', '))}</small></td></tr>)}</tbody></table></div> : <Empty>No assignment diff</Empty>}</section><div className="admin-two-col"><DataList title="Issues" items={issueItems} fields={['kind', 'reason', 'details']} /><DataList title="Invariants" items={invariantItems} fields={['name', 'status', 'details']} /></div></>}</div>;
}

function Schedule() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; syncs?: RecordValue[]; parses?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); Promise.all([api<{ items: RecordValue[] }>('/api/admin/schedule/syncs'), api<{ items: RecordValue[] }>('/api/admin/schedule/parses')]).then(([syncs, parses]) => setState({ loading: false, syncs: syncs.items, parses: parses.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <div className="admin-stack"><div className="admin-page-actions"><p>Schedule diagnostics. Stable membership, lesson audience и computed audience показаны как разные layers.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><div className="admin-two-col"><DataList title="Sync history" items={state.syncs || []} fields={['status', 'source', 'created_at', 'error']} /><DataList title="Parse issues" items={state.parses || []} fields={['lesson_date', 'subject', 'audience', 'parse_status', 'parse_diagnostics']} /></div><section className="admin-panel"><div className="admin-callout admin-callout--muted"><Activity size={16} /><span>Schedule audience bindings и student schedule explanations доступны отдельными API; они не материализуются в canonical membership.</span></div></section></div>;
}

function Journals() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; journals?: RecordValue[]; classroom?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); Promise.all([api<{ items: RecordValue[] }>('/api/admin/journals'), api<{ items: RecordValue[] }>('/api/admin/classroom/syncs')]).then(([journals, classroom]) => setState({ loading: false, journals: journals.items, classroom: classroom.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <div className="admin-stack"><div className="admin-page-actions"><p>Official journal snapshot и Classroom metadata остаются разными источниками.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><div className="admin-two-col"><DataList title="Journal sources" items={state.journals || []} fields={['spreadsheet_title', 'sheet_title', 'grade', 'subject', 'status', 'last_synced_at', 'last_error', 'unlinked_students', 'ambiguous_students']} /><DataList title="Classroom sync history" items={state.classroom || []} fields={['action', 'entity_type', 'details', 'created_at']} /></div><section className="admin-panel"><div className="admin-callout admin-callout--muted"><BookOpen size={16} /><span>Admin не показывает Classroom как официальный gradebook и не подменяет journal grades данными Classroom.</span></div></section></div>;
}

function Audit() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); api<{ items: RecordValue[] }>('/api/admin/audit?limit=100').then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{state.items?.length || 0} events</span><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>{state.items?.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead><tbody>{state.items.map((item) => <tr key={item.id}><td>{formatDate(item.created_at)}</td><td>{display(item.actor_name)}</td><td className="admin-mono">{display(item.action)}</td><td>{display(item.entity_type)} #{display(item.entity_id)}</td><td className="admin-mono">{display(item.details)}</td></tr>)}</tbody></table></div> : <Empty>Audit log пуст</Empty>}</section>;
}

function System() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; data?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>('/api/admin/system').then((data) => setState({ loading: false, data })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const data = state.data || {}; return <div className="admin-stack"><div className="admin-page-actions"><p>Environment, schema parity и counts. Secrets намеренно не отображаются.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><section className="admin-panel"><div className="admin-panel__heading"><h2>Runtime health</h2><Status value={data.health?.schema_status} /></div><dl className="admin-definition-list"><dt>Environment</dt><dd>{display(data.health?.environment)}</dd><dt>Database</dt><dd>{display(data.settings?.database_configured ? 'configured' : 'local')}</dd><dt>Dev auth</dt><dd>{display(data.settings?.dev_auth_enabled)}</dd></dl></section><section className="admin-panel"><h2>Data counts</h2><div className="admin-inline-stats">{Object.entries(data.health?.counts || {}).map(([key, value]) => <div key={key}><strong>{String(value)}</strong><span>{key}</span></div>)}</div></section><section className="admin-panel"><h2>Schema checks</h2><div className="admin-detail-list">{Object.entries(data.health?.schema || {}).map(([key, value]: [string, any]) => <div key={key}><strong>{key}</strong><small><Status value={value.ok ? 'current' : 'attention'} /> · required: {value.required.join(', ')}</small></div>)}</div></section></div>;
}

function AppContent({ path, navigate }: { path: string; navigate: (href: string) => void }) {
  if (path.startsWith('/admin/people/')) return <PersonDetail id={path.split('/').pop() || ''} navigate={navigate} />;
  if (path === '/admin/people') return <People navigate={navigate} />;
  if (path.startsWith('/admin/groups/')) return <GroupDetail id={path.split('/').pop() || ''} navigate={navigate} />;
  if (path === '/admin/groups') return <Groups navigate={navigate} />;
  if (path.startsWith('/admin/sources/')) return <Sources id={path.split('/').pop()} navigate={navigate} />;
  if (path === '/admin/sources') return <Sources navigate={navigate} />;
  if (path === '/admin/reconciliation' || path.startsWith('/admin/reconciliation/')) return <Reconciliation />;
  if (path === '/admin/schedule') return <Schedule />;
  if (path === '/admin/journals') return <Journals />;
  if (path === '/admin/audit') return <Audit />;
  if (path === '/admin/system') return <System />;
  return <Overview navigate={navigate} />;
}

export default function AdminPage() {
  const { path, navigate } = useRoute();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  useEffect(() => { api<{ user: Session }>('/api/admin/auth/session').then((result) => setSession(result.user)).catch(() => setSession(null)).finally(() => setChecking(false)); }, []);
  const logout = async () => { await api('/api/admin/auth/logout', { method: 'POST' }).catch(() => undefined); setSession(null); };
  if (checking) return <main className="admin-login"><Loading /></main>;
  if (!session) return <Login onLoggedIn={setSession} />;
  return <div className="admin-console"><Sidebar path={path} navigate={navigate} onLogout={() => void logout()} /><main className="admin-console-main"><Header path={path} navigate={navigate} session={session} /><AppContent path={path} navigate={navigate} /></main></div>;
}
