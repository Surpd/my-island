/* oxlint-disable */
'use client';

import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import dynamic from 'next/dynamic';
import {
  AlertTriangle,
  BookOpen,
  CalendarDays,
  ChevronRight,
  Home,
  LogOut,
  Pin,
  RefreshCw,
  Search,
  Server,
  Settings,
  GraduationCap,
  UserRoundCog,
  Menu,
  X,
} from 'lucide-react';
import { SchoolDirectoryAdmin } from '@/components/school-directory-admin';
import { TeacherDirectoryAdmin } from '@/components/teacher-directory-admin';
import { AdminJournals } from '@/components/admin-journals';
import { GoogleConnectionCard } from '@/components/google-connection-card';
import { MiniappPreview } from '@/components/miniapp-preview';
import { MobilePublishedSchedule } from '@/components/mobile-published-schedule';
import { CampusRoleSwitch, useTelegramBack, type CampusRole } from '@/components/campus-controls';
import { useTelegramLayout } from '../telegram-layout';
import '../admin-integration.css';

const ScheduleConstructor = dynamic(
  () => import('@/components/schedule-constructor').then((module) => module.ScheduleConstructor),
  { ssr: false },
);

type RecordValue = Record<string, any>;
type Session = { id: string | number; role: string; roles?: string[]; identity_id?: string | number | null };
type ApiError = Error & { status?: number; details?: unknown };

const runtimeEnv =
  (import.meta as ImportMeta & { env?: Record<string, string | boolean> }).env || {};
const isLocalBuild = runtimeEnv.DEV === true || runtimeEnv.MODE === 'development';
const API_BASE = String(
  runtimeEnv.VITE_API_BASE_URL ||
    (isLocalBuild ? 'http://127.0.0.1:8000' : ''),
).replace(/\/$/, '');
const schoolMode = runtimeEnv.VITE_SCHOOL_DIRECTORY_MODE === 'school';
const dataSourceLabel = String(runtimeEnv.VITE_DATA_SOURCE_LABEL || (runtimeEnv.VITE_SCHOOL_DIRECTORY_MODE === 'school' ? 'Школьная база My Island' : 'My Island'));

const nav = [
  { href: '/admin', label: 'Сегодня', icon: Home },
  { href: '/admin/schedule/constructor', label: 'Расписание', icon: CalendarDays },
  { href: '/admin/people', label: 'Ученики и группы', icon: GraduationCap },
  { href: '/admin/journals', label: 'Журналы и оценки', icon: BookOpen },
  { href: '/admin/teachers', label: 'Преподаватели', icon: UserRoundCog },
  { href: '/admin/preview', label: 'Предпросмотр', icon: Search },
  { href: '/admin/settings', label: 'Настройки', icon: Settings },
];
function localDevAuth() {
  if (typeof window === 'undefined') return null;
  if (schoolMode) return null;
  const query = new URLSearchParams(window.location.search);
  return ['localhost', '127.0.0.1'].includes(window.location.hostname) && query.get('role') === 'admin' ? 'admin:1' : null;
}

function routeFromLocation() {
  if (typeof window === 'undefined') return '/admin';
  const hash = window.location.hash.slice(1);
  const hashRoute = hash.startsWith('/admin');
  const current = hashRoute ? hash.split('?')[0] : window.location.pathname.replace(/\/$/, '');
  if (!hashRoute && current !== '/admin' && !current.startsWith('/admin/')) return '/admin';
  let route = current || '/admin';
  if (route === '/admin/schedule') route = '/admin/schedule/constructor';
  if (/^\/admin\/(groups(?:\/|$)|people\/(?!groups$))/.test(route)) route = '/admin/people';
  if (/^\/admin\/(sources|reconciliation|audit|issues|candidates)(?:\/|$)/.test(route)) route = '/admin';
  if (route !== current) {
    const query = hashRoute ? hash.slice(current.length) : window.location.search;
    window.history.replaceState(null, '', hashRoute ? `${window.location.pathname}${window.location.search}#${route}${query}` : `${route}${query}`);
  }
  return route === '/admin' || route.startsWith('/admin/') ? route : '/admin';
}

function routeQuery() {
  if (typeof window === 'undefined') return new URLSearchParams();
  const hashRoute = window.location.hash.replace(/^#/, '');
  const query = hashRoute.includes('?') ? hashRoute.slice(hashRoute.indexOf('?') + 1) : window.location.search.slice(1);
  return new URLSearchParams(query);
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE) {
    throw new Error('Для production-сборки My Island не настроен адрес API.');
  }
  const headers = new Headers(init?.headers);
  const dev = localDevAuth();
  if (dev) headers.set('X-Dev-Auth', dev);
  if (typeof window !== 'undefined') {
    const signed = window.Telegram?.WebApp?.initData || window.sessionStorage.getItem('my_island_telegram_init_data');
    if (signed) { headers.set('X-Telegram-Init-Data', signed); window.sessionStorage.setItem('my_island_telegram_init_data', signed); }
  }
  if (typeof window !== 'undefined') {
    const schoolSession = window.sessionStorage.getItem('my_island_school_admin_session');
    const browserSession = window.sessionStorage.getItem('my_island_admin_session');
    // Constructor publication is authenticated against the primary admin
    // session. A stale school-directory token must not shadow a still-valid
    // browser session for these consolidated schedule endpoints.
    const schoolAdminSession = path.startsWith('/api/admin/schedule/constructor')
      ? browserSession || schoolSession
      : schoolSession || browserSession;
    if (schoolAdminSession) headers.set('X-School-Admin-Session', schoolAdminSession);
    // The API's auth resolver accepts browser sessions through the existing
    // X-Dev-Auth transport using the explicit browser: prefix. Keeping the
    // transport aligned here makes hosted Sites work even when third-party
    // cookies are unavailable.
    if (browserSession) { headers.set('X-Dev-Auth', `browser:${browserSession}`); headers.delete('X-Telegram-Init-Data'); }
  }
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response: Response;
  const method = (init?.method || 'GET').toUpperCase();
  for (let attempt = 0; ; attempt++) {
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: 'include' });
  } catch (cause) {
    if (attempt === 0 && method === 'GET' && (typeof navigator === 'undefined' || navigator.onLine)) {
      await new Promise((resolve) => setTimeout(resolve, 350));
      continue;
    }
    const networkState = typeof navigator !== 'undefined' && !navigator.onLine
      ? 'Нет подключения к сети. Проверьте соединение и повторите попытку.'
      : 'Сервер временно не ответил. Попробуйте ещё раз позже.';
    const error = new Error(networkState) as ApiError;
    error.cause = cause;
    throw error;
  }
  break;
  }
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const details = body.detail ?? body.error;
    const raw = typeof details === 'object' && details !== null
      ? String((details as Record<string, unknown>).message || `Request failed: ${response.status}`)
      : String(details || `Request failed: ${response.status}`);
    const message = raw === 'Production requests must include Telegram initData'
      ? 'Сессия администратора не передалась в браузер. Получите новый код и войдите снова.'
      : raw === 'Admin browser session is invalid or expired'
        ? 'Сессия администратора истекла. Получите новый код и войдите снова.'
        : raw === 'School administrator session is invalid or expired'
          ? 'Доступ к расписанию не подтвердился. Перепроверьте текущий вход: новый код потребуется только если истекла и сессия администратора.'
        : raw.startsWith('Request failed:')
          ? `Не удалось выполнить запрос (${response.status}).`
          : raw;
    const error = new Error(message) as ApiError;
    error.status = response.status;
    error.details = details;
    throw error;
  }
  return body as T;
}

async function schoolApi<T>(path: string, init?: RequestInit): Promise<T> {
  const schoolPath = path.replace(/^\/api\/admin\/(groups|people|identities|memberships|teacher-assignments)(?=\/|\?|$)/, '/api/admin/school/$1');
  const headers = new Headers(init?.headers);
  const token = typeof window !== 'undefined'
    ? window.sessionStorage.getItem('my_island_school_admin_session') || window.sessionStorage.getItem('my_island_admin_session')
    : null;
  if (token) headers.set('X-School-Admin-Session', token);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}${schoolPath}`, { ...init, headers, credentials: 'omit' });
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const error = new Error(String(body.detail || `Запрос к школьной базе завершился ошибкой (${response.status}).`)) as ApiError;
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

const ruLabels: Record<string, string> = {
  active: 'активен', applied: 'применено', approved: 'подтверждено', attention: 'внимание',
  base_class: 'базовый класс', canonical: 'основная', clean: 'нет проблем', configured: 'настроена',
  class: 'класс', exam_track: 'экзаменационный профиль',
  conflict: 'конфликт', confirmed: 'подтверждено', CREATE: 'создать', DEACTIVATE: 'деактивировать',
  error: 'ошибка', failed: 'ошибка', healthy: 'исправно', identity_conflict: 'конфликт личности',
  instructional: 'учебная', linked: 'привязан', local: 'локальная', local_sqlite: 'локальная SQLite',
  no: 'нет', 'no account': 'нет аккаунта', not_run: 'не запускался', ok: 'исправно', open: 'открыто',
  pending: 'ожидает', postgres: 'Postgres', PROTECTED: 'защищено', ready_for_review: 'готово к проверке',
  revoked: 'отозвано', student: 'ученик', teacher: 'учитель', unlinked: 'не привязан', UNRESOLVED: 'не решено',
  WRONG_GROUP: 'не та группа', STALE: 'устарело', KEEP: 'оставить', current: 'актуально', attention_required: 'требует внимания',
  official_import: 'официальный импорт', admin_override: 'ручное изменение', spreadsheet: 'таблица', classroom: 'Классрум',
  subject_group: 'учебная группа', instructional_group: 'учебная группа', selection_fact: 'экзамен / профиль',
  multiple_active_base_classes: 'несколько активных базовых классов', read_only: 'только просмотр',
  google_sheet: 'Google Таблица', official_school_data: 'официальные школьные данные',
  manual_review: 'ручная проверка', non_boolean_selection: 'неоднозначный выбор', base_split: 'разделение базового класса',
  ambiguous_teacher_headers: 'неоднозначные заголовки учителей', students_without_base_class: 'ученики без базового класса',
  teacher_assignment_unresolved: 'неразрешённое назначение учителя', person: 'человек', group: 'группа', membership: 'членство',
  create: 'создать', update: 'обновить', end: 'завершить', map: 'сопоставить', configuration_missing: 'не настроено',
  reauthorization_required: 'нужна повторная авторизация', connected: 'подключено', instructional_group_list: 'списки учебных групп',
  base_class_list: 'списки базовых классов', exam_profile_list: 'списки экзаменов и профилей', other: 'другой источник',
};

function statusLabel(value: unknown) {
  const text = display(value);
  return ruLabels[text] || text;
}

function friendlyError(error: unknown) {
  if (error instanceof Error) {
    if (error.message === 'Admin login code is invalid, expired, or already used') return 'Код недействителен, истёк или уже использован. Получите новый код в Telegram.';
    return error.message;
  }
  return display(error);
}

function Status({ value, tone }: { value: unknown; tone?: 'good' | 'warn' | 'bad' | 'neutral' }) {
  const raw = display(value);
  const text = statusLabel(value);
  return <span className={`admin-status admin-status--${tone || (['healthy', 'ok', 'applied', 'current', 'подключено'].includes(raw) ? 'good' : ['attention', 'pending', 'open', 'PROTECTED'].includes(raw) ? 'warn' : raw === 'error' || raw === 'failed' ? 'bad' : 'neutral')}`}>{text}</span>;
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return <div className="admin-error"><AlertTriangle size={17} /><div><strong>Не удалось загрузить раздел</strong><p>{friendlyError(error)}</p></div>{onRetry && <button className="admin-button admin-button--quiet" onClick={onRetry}>Повторить</button>}</div>;
}

function Loading() {
  return <div className="admin-loading"><RefreshCw size={17} className="admin-spin" /> Загружаем раздел…</div>;
}

function useRoute() {
  const [path, setPath] = useState('/admin');
  useEffect(() => {
    const update = () => setPath(routeFromLocation());
    update();
    window.addEventListener('popstate', update);
    window.addEventListener('hashchange', update);
    return () => {
      window.removeEventListener('popstate', update);
      window.removeEventListener('hashchange', update);
    };
  }, []);
  const navigate = (href: string) => {
    if ((window as Window & { __scheduleDraftDirty?: boolean }).__scheduleDraftDirty && !window.confirm('Черновик ещё не сохранён на сервере. Всё равно уйти со страницы?')) return;
    const search = window.location.search;
    window.history.pushState({}, '', `/admin${search}#${href}`);
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
      const result = await api<{ user: Session; browser_session_token?: string }>('/api/admin/auth/exchange', { method: 'POST', body: JSON.stringify({ code }) });
      if (result.browser_session_token) window.sessionStorage.setItem('my_island_admin_session', result.browser_session_token);
      onLoggedIn(result.user);
    } catch (nextError) {
      setError(friendlyError(nextError) || 'Код не принят');
    } finally {
      setBusy(false);
    }
  };
  return <main className="admin-login"><div className="admin-login-card"><div className="admin-logo">MI</div><p className="admin-eyebrow">MY ISLAND / АДМИН</p><span className="admin-data-source">{dataSourceLabel}</span><h1>Вход в панель администратора</h1><p>Получите одноразовый код через Telegram-бота, затем введите его здесь. Код действует ограниченное время и используется один раз.</p><form onSubmit={submit}><label>Одноразовый код<input name="code" autoComplete="one-time-code" spellCheck={false} autoCapitalize="none" value={code} onChange={(event) => setCode(event.target.value)} placeholder="Вставьте код" /></label>{error && <div role="alert" className="admin-form-error">{error}</div>}<button className="admin-button admin-button--primary" disabled={busy || !code.trim()}>{busy ? 'Проверяем…' : 'Войти безопасно'}</button></form><small>Для входа ученика или преподавателя откройте My Island через Telegram.</small></div></main>;
}

function Sidebar({ path, navigate, onLogout }: { path: string; navigate: (href: string) => void; onLogout: () => void }) {
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const menuRef = useRef<HTMLDialogElement>(null);
  const compactRoute = path === '/admin/schedule/constructor' || path === '/admin/journals';
  const collapsed = compactRoute && !pinned && !hovered;
  const active = (href: string) => href === '/admin' ? path === href : path === href || path.startsWith(`${href}/`) || (href === '/admin/schedule/constructor' && path.startsWith('/admin/schedule/')) || (href === '/admin/settings' && path.startsWith('/admin/system'));
  const mobileNavigate = (href: string) => {
    const previousURL = window.location.href;
    navigate(href);
    if (window.location.href !== previousURL || window.location.hash.slice(1) === href) menuRef.current?.close();
  };
  return <aside className={`admin-console-sidebar ${compactRoute ? 'constructor-sidebar' : ''} ${collapsed ? 'is-collapsed' : ''} ${pinned ? 'is-pinned' : ''}`} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
    <div className="admin-console-brand"><div className="admin-logo admin-logo--small" aria-hidden="true">MI</div><div><strong>My Island<span className="campus-wordmark-dot" aria-hidden="true">.</span></strong><small>Школьное пространство</small><small className="admin-data-source">{dataSourceLabel}</small></div>{compactRoute ? <button className="constructor-sidebar-pin" aria-label={pinned ? 'Открепить меню' : 'Закрепить меню'} title={pinned ? 'Открепить меню' : 'Закрепить меню'} onClick={() => setPinned((value) => !value)}><Pin size={15} /></button> : null}</div>
    <button className="campus-mobile-menu-button" aria-label="Все разделы" aria-haspopup="dialog" onClick={() => menuRef.current?.showModal()}><Menu size={20} /><span>Все разделы</span></button>
    <nav aria-label="Основные разделы">{nav.map((item) => { const Icon = item.icon; return <button key={item.href} title={item.label} aria-label={item.label} aria-current={active(item.href) ? 'page' : undefined} className={active(item.href) ? 'is-active' : ''} onClick={() => navigate(item.href)}><Icon size={18} /><span>{item.label}</span>{active(item.href) && <ChevronRight size={14} />}</button>; })}</nav>
    <div className="admin-console-sidebar__footer"><span>Администратор</span><button onClick={onLogout}><LogOut size={16} /> Выйти</button></div>
    <dialog ref={menuRef} className="campus-mobile-menu" aria-labelledby="campus-menu-title"><div className="campus-menu-heading"><div><h2 id="campus-menu-title">Разделы школы</h2><p>{dataSourceLabel}</p></div><button aria-label="Закрыть меню" onClick={() => menuRef.current?.close()}><X size={22} /></button></div><nav aria-label="Все разделы школы"><section><h3>Основные разделы</h3>{nav.map((item) => { const Icon = item.icon; return <button key={item.href} aria-current={active(item.href) ? 'page' : undefined} onClick={() => mobileNavigate(item.href)}><Icon size={19} /><span>{item.label}</span><ChevronRight size={15} /></button>; })}</section></nav><button className="campus-menu-logout" onClick={onLogout}><LogOut size={18} /> Выйти</button></dialog>
  </aside>;
}

function Header({ path, navigate }: { path: string; navigate: (href: string) => void }) {
  const title = path === '/admin' ? 'Рабочий стол завуча' : (path === '/admin/system' ? 'Настройки' : nav.find((item) => path.startsWith(item.href) && item.href !== '/admin')?.label) || 'Панель администратора';
  return <header className="admin-console-header"><div><div className="admin-breadcrumb"><button onClick={() => navigate('/admin')}>Админ</button><ChevronRight size={13} /><span>{path === '/admin' ? 'Сегодня' : title}</span></div><h1>{title}</h1></div><div className="admin-console-header__meta"><span className="campus-session-avatar" aria-hidden="true">А</span><span>Администратор</span></div></header>;
}

function Overview({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; overview?: RecordValue; schedule?: RecordValue; scheduleDrafts?: RecordValue[] }>({ loading: true });
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const weekStart = `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, '0')}-${String(monday.getDate()).padStart(2, '0')}`;
  const load = () => {
    setState({ loading: true });
    Promise.allSettled([
      api<RecordValue>('/api/admin/overview'),
      api<RecordValue>(`/api/admin/schedule/overview?week_start=${weekStart}`),
      api<{ items: RecordValue[] }>('/api/admin/schedule/experiments/school'),
    ]).then(([overview, schedule, drafts]) => {
      if (overview.status === 'rejected') {
        setState({ loading: false, error: overview.reason });
        return;
      }
      setState({ loading: false, overview: overview.value, schedule: schedule.status === 'fulfilled' ? schedule.value : undefined,
        scheduleDrafts: drafts.status === 'fulfilled' ? drafts.value.items : undefined });
    });
  };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const overview = state.overview || {};
  const currentDraft = state.scheduleDrafts?.find(item => item.week_start === weekStart);
  const publishedWeek = state.schedule?.effective_week?.week_start;
  const publishedVersion = state.schedule?.canonical?.version_id;
  const publication = publishedWeek ? `Отдельная неделя с ${formatDate(publishedWeek)}` : publishedVersion
    ? 'Основной шаблон' : state.schedule ? 'Нет опубликованной недели' : 'Сведения временно недоступны';
  return <div className="campus-dashboard">
    <div className="campus-dashboard-context"><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>
    <div className="campus-overview-top">
      <section className="campus-date" aria-label="Текущая дата"><span>{today.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })}</span><strong>{today.getDate()}<span>{today.toLocaleDateString('ru-RU', { weekday: 'long' })}</span></strong><div><b>Школьное пространство</b><p>{dataSourceLabel}</p></div></section>
      <section className="campus-panel campus-schedule-summary">
        <div className="campus-panel-heading"><div><h2>Расписание школы</h2><p>Неделя с {monday.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}</p></div><CalendarDays size={22} /></div>
        <dl className="admin-definition-list"><dt>Опубликовано</dt><dd>{publication}</dd><dt>Черновик недели</dt><dd>{currentDraft ? currentDraft.name : state.scheduleDrafts ? 'Нет' : 'Сведения временно недоступны'}</dd></dl>
        <button className="admin-button admin-button--primary" onClick={() => navigate(currentDraft
          ? `/admin/schedule/constructor?draft=${encodeURIComponent(currentDraft.id)}` : '/admin/schedule/constructor')}>Открыть расписание <ChevronRight size={17} /></button>
      </section>
      <section className="campus-directory"><h2>Школьный справочник</h2><div>{[
        { label: 'учеников', value: overview.canonical_students, href: '/admin/people?kind=student' },
        { label: 'преподавателей', value: overview.canonical_teachers, href: '/admin/teachers' },
        { label: 'групп', value: overview.groups, href: '/admin/people' },
      ].map(item => <button key={item.href} onClick={() => navigate(item.href)}><strong>{display(item.value)}</strong><span>{item.label}</span></button>)}</div>
      <button className="admin-link" onClick={() => navigate('/admin/people')}>Состав школы <ChevronRight size={15} /></button></section>
    </div>
  </div>;
}

function System() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; data?: RecordValue }>({ loading: true });
  const load = () => {
    setState({ loading: true });
    api<RecordValue>('/api/admin/system').then(data => setState({ loading: false, data })).catch(error => setState({ loading: false, error }));
  };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const data = state.data || {};
  return <div className="admin-stack">
    <div className="admin-page-actions"><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>
    <section className="admin-panel"><div className="admin-panel__heading"><h2>Backend и школьная база</h2><Status value={data.health?.schema_status} /></div>
      <dl className="admin-definition-list"><dt>Backend</dt><dd><Status value="healthy" /> доступен</dd><dt>Школьная база</dt><dd>{data.settings?.database_configured ? 'Подключена' : 'Локальный режим'}</dd></dl>
    </section>
    <GoogleConnectionCard api={api} apiOrigin={API_BASE} />
  </div>;
}

function ResponsiveSchedule() {
  const [mobile, setMobile] = useState<boolean | null>(null);
  const [editor, setEditor] = useState(() => {
    const query = routeQuery();
    return query.get('editor') !== '0' || query.has('draft');
  });
  useEffect(() => {
    const media = window.matchMedia('(max-width: 740px)');
    const update = () => setMobile(media.matches); update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  if (mobile === null) return <Loading />;
  return (
    <div className={mobile ? 'admin-stack responsive-schedule responsive-schedule--mobile' : 'responsive-schedule'}>
      <button hidden={!mobile} className="admin-button admin-button--quiet" onClick={() => setEditor((value) => !value)}>
        {editor ? 'Просмотр опубликованного расписания' : 'Открыть редактор и разбор проблем'}
      </button>
      <div hidden={mobile && !editor}>
        <ScheduleConstructor api={api} />
      </div>
      {mobile && !editor ? <MobilePublishedSchedule api={api} /> : null}
    </div>
  );
}

function AppContent({ path, navigate }: { path: string; navigate: (href: string) => void }) {
  if (path === '/admin/preview') return <MiniappPreview key={`${routeQuery().get('identity_id') || ''}:${routeQuery().get('kind') || ''}`} api={api} sourceLabel={dataSourceLabel} initialIdentity={routeQuery().get('identity_id') || ''} initialKind={routeQuery().get('kind') || 'student'} initialDay={routeQuery().get('day') || ''} localAdmin={Boolean(localDevAuth())} />;
  if (path === '/admin/teachers') return <TeacherDirectoryAdmin connectionLabel={dataSourceLabel} api={schoolMode ? schoolApi : api} schoolMode={schoolMode} navigate={navigate} />;
  if (path === '/admin/people' || path === '/admin/people/groups') return <SchoolDirectoryAdmin connectionLabel={dataSourceLabel} api={schoolMode ? schoolApi : api} section="groups" navigate={navigate} schoolMode={schoolMode} initialGroupId={routeQuery().get('group_id') || undefined} />;
  if (path === '/admin/schedule/published') return <MobilePublishedSchedule api={api} initialWeekStart={routeQuery().get('week_start') || undefined} />;
  if (path === '/admin/schedule/constructor') return <ResponsiveSchedule />;
  if (path === '/admin/schedule') return <ResponsiveSchedule />;
  if (path === '/admin/journals') return <AdminJournals api={api} apiOrigin={API_BASE} />;
  if (path === '/admin/settings') return <System />;
  if (path === '/admin/system') return <System />;
  return <Overview navigate={navigate} />;
}

export default function AdminPage({ embeddedSession, onRoleChange }: { embeddedSession?: Session; onRoleChange?: (role: CampusRole) => void } = {}) {
  useTelegramLayout();
  const { path, navigate } = useRoute();
  const [session, setSession] = useState<Session | null>(embeddedSession || null);
  const [checking, setChecking] = useState(true);
  const [connectionError, setConnectionError] = useState<unknown>(null);
  useEffect(() => {
    if (embeddedSession) { setSession(embeddedSession); setChecking(false); return; }
    let active = true;
    const check = async () => {
      // Direct Telegram launches may hydrate before the SDK script arrives.
      if (!localDevAuth() && !window.sessionStorage.getItem('my_island_admin_session')) {
        const deadline = Date.now() + 3000;
        while (!window.Telegram?.WebApp && Date.now() < deadline && active) await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (!active) return;
      try { const result = await api<{ user: Session }>('/api/admin/auth/session'); if (active) { setConnectionError(null); setSession(result.user); } }
      catch (error) { if (active) { const status = (error as ApiError).status; if (status === 401 || status === 403) setSession(null); else setConnectionError(error); } }
      finally { if (active) setChecking(false); }
    };
    void check(); return () => { active = false; };
  }, [embeddedSession]);
  useTelegramBack(path !== '/admin', () => navigate('/admin'));
  const switchRole = (role: CampusRole) => {
    if (!session?.roles?.includes(role)) return;
    if (onRoleChange) { onRoleChange(role); return; }
    window.location.assign(`/?role=${encodeURIComponent(role)}`);
  };
  const logout = async () => { await api('/api/admin/auth/logout', { method: 'POST' }).catch(() => undefined); window.sessionStorage.removeItem('my_island_admin_session'); window.sessionStorage.removeItem('my_island_telegram_init_data'); setSession(null); };
  if (checking) return <main className="admin-login"><Loading /></main>;
  if (connectionError) return <main className="admin-login"><div className="admin-login-card"><span className="admin-data-source">{dataSourceLabel}</span><ErrorState error={connectionError} onRetry={() => window.location.reload()} /></div></main>;
  if (!session) return <Login onLoggedIn={setSession} />;
  const modernWorkspace = ['/admin', '/admin/people', '/admin/people/groups', '/admin/teachers', '/admin/settings', '/admin/system', '/admin/preview', '/admin/schedule/constructor'].includes(path);
  return <div className={`admin-console campus-shell ${modernWorkspace ? 'admin-workspace' : ''} ${path.startsWith('/admin/schedule') ? 'admin-console--schedule' : ''} ${path === '/admin/schedule/constructor' ? 'admin-console--constructor' : ''} ${path === '/admin/journals' ? 'admin-console--journals' : ''}`}>{modernWorkspace ? <a className="admin-skip-link" href="#admin-content">Перейти к содержимому</a> : null}<Sidebar path={path} navigate={navigate} onLogout={() => void logout()} /><main id="admin-content" tabIndex={-1} className="admin-console-main">{!path.startsWith('/admin/schedule') ? <Header path={path} navigate={navigate} /> : null}<div className="admin-role-toolbar"><CampusRoleSwitch roles={session.roles || [session.role]} value="admin" onChange={switchRole} /></div><AppContent path={path} navigate={navigate} /></main></div>;
}


