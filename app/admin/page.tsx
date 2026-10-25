/* oxlint-disable */
'use client';

import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from 'react';
import dynamic from 'next/dynamic';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Database,
  FileSearch,
  LayoutDashboard,
  Home,
  LogOut,
  Pin,
  RefreshCw,
  Search,
  Server,
  Settings,
  ShieldCheck,
  Users,
  GraduationCap,
  UserRoundCog,
  Wrench,
  Menu,
  X,
} from 'lucide-react';
import { SchoolDirectoryAdmin } from '@/components/school-directory-admin';
import { ScheduleSourceReview } from '@/components/schedule-source-review';
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
const legacyNav = [
  { href: '/admin/people/legacy', label: 'Люди и аккаунты', icon: Users },
  { href: '/admin/groups', label: 'Старый список групп', icon: Users },
  { href: '/admin/sources', label: 'Источники', icon: Database },
  { href: '/admin/reconciliation', label: 'Сверка', icon: FileSearch },
  { href: '/admin/audit', label: 'Аудит', icon: Activity },
  { href: '/admin/issues', label: 'Проблемы', icon: AlertTriangle },
  { href: '/admin/candidates', label: 'Изменения источников', icon: FileSearch },
];

function localDevAuth() {
  if (typeof window === 'undefined') return null;
  if (schoolMode) return null;
  const query = new URLSearchParams(window.location.search);
  return ['localhost', '127.0.0.1'].includes(window.location.hostname) && query.get('role') === 'admin' ? 'admin:1' : null;
}

function routeFromLocation() {
  if (typeof window === 'undefined') return '/admin';
  const hashRoute = window.location.hash.replace(/^#/, '');
  if (hashRoute.startsWith('/admin')) {
    const route = hashRoute.split('?')[0] || '/admin';
    if (route === '/admin/schedule') { window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/admin/schedule/constructor`); return '/admin/schedule/constructor'; }
    return route;
  }
  const pathRoute = window.location.pathname.replace(/\/$/, '') || '/admin';
  if (pathRoute === '/admin/schedule') { window.history.replaceState(null, '', `${pathRoute}/constructor${window.location.search}`); return '/admin/schedule/constructor'; }
  if (pathRoute === '/admin' || pathRoute.startsWith('/admin/')) return pathRoute;
  return '/admin';
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
    const schoolAdminSession = schoolSession || browserSession;
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

const fieldLabels: Record<string, string> = {
  action: 'действие', active: 'активен', ambiguous_students: 'неоднозначные ученики', audience: 'аудитория',
  change_kind: 'изменение', created_at: 'создано', details: 'подробности', entity_type: 'тип объекта',
  error: 'ошибка', external_key: 'внешний ключ', fingerprint: 'отпечаток', finished_at: 'завершено',
  grade: 'класс', issue_type: 'тип проблемы', kind: 'тип', last_error: 'последняя ошибка', last_synced_at: 'синхронизировано',
  lesson_date: 'дата урока', mapping_type: 'тип сопоставления', mode: 'режим', name: 'название', parse_diagnostics: 'диагностика разбора',
  parse_status: 'статус разбора', record_key: 'ключ записи', reason: 'причина', source: 'источник', source_ref: 'ссылка источника',
  spreadsheet_title: 'таблица', sheet_title: 'лист', started_at: 'начато', status: 'статус', subject: 'предмет',
  unlinked_students: 'непривязанные ученики', valid_from: 'действует с', valid_until: 'действует до',
};

function statusLabel(value: unknown) {
  const text = display(value);
  return ruLabels[text] || text;
}

function readableGroupName(value: unknown) {
  if (!value) return '—';
  if (typeof value === 'object' && value !== null) {
    const item = value as RecordValue;
    if (item.display_name) return String(item.display_name);
    if (item.group_name) return readableGroupName(item.group_name);
    if (item.name) return readableGroupName(item.name);
    return '—';
  }
  const text = String(value);
  if (text.startsWith('selection:')) {
    const [, grade, ...subject] = text.split(':');
    return `Выбор предмета: ${subject.join(':') || 'не указан'}${grade ? ` · ${grade} класс` : ''}`;
  }
  if (text.startsWith('class:')) return `Класс: ${text.slice(6)}`;
  if (text.startsWith('subject:')) return `Учебная группа: ${text.slice(8).replaceAll(':', ' · ')}`;
  const gradeKey = text.match(/^grade(\d+)-([a-z]+)(?:-([a-z]))?$/i);
  if (gradeKey) {
    const subjects: Record<string, string> = { math: 'математика', english: 'английский язык', russian: 'русский язык', literature: 'литература', biology: 'биология', physics: 'физика', chemistry: 'химия', social: 'обществознание' };
    return `${gradeKey[1]} класс · ${subjects[gradeKey[2].toLowerCase()] || gradeKey[2]}${gradeKey[3] ? ` · группа ${gradeKey[3].toUpperCase()}` : ''}`;
  }
  return text;
}

function readableValue(value: unknown, field?: string) {
  if (field === 'name' || field === 'group_name' || field === 'group') return readableGroupName(value);
  if (field === 'source' || field === 'status' || field === 'mode' || field === 'action' || field === 'classification') return statusLabel(value);
  return display(value);
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

function Empty({ children = 'Нет данных' }: { children?: ReactNode }) {
  return <div className="admin-empty">{children}</div>;
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
  const constructorRoute = path === '/admin/schedule/constructor';
  const collapsed = constructorRoute && !pinned && !hovered;
  const active = (href: string) => href === '/admin' ? path === href : path === href || path.startsWith(`${href}/`) || (href === '/admin/settings' && path.startsWith('/admin/system'));
  const mobileNavigate = (href: string) => {
    const previousURL = window.location.href;
    navigate(href);
    if (window.location.href !== previousURL || window.location.hash.slice(1) === href) menuRef.current?.close();
  };
  return <aside className={`admin-console-sidebar ${constructorRoute ? 'constructor-sidebar' : ''} ${collapsed ? 'is-collapsed' : ''} ${pinned ? 'is-pinned' : ''}`} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
    <div className="admin-console-brand"><div className="admin-logo admin-logo--small" aria-hidden="true">MI</div><div><strong>My Island<span className="campus-wordmark-dot" aria-hidden="true">.</span></strong><small>Школьное пространство</small><small className="admin-data-source">{dataSourceLabel}</small></div>{constructorRoute ? <button className="constructor-sidebar-pin" aria-label={pinned ? 'Открепить меню' : 'Закрепить меню'} title={pinned ? 'Открепить меню' : 'Закрепить меню'} onClick={() => setPinned((value) => !value)}><Pin size={15} /></button> : null}</div>
    <button className="campus-mobile-menu-button" aria-label="Все разделы" aria-haspopup="dialog" onClick={() => menuRef.current?.showModal()}><Menu size={20} /><span>Все разделы</span></button>
    <nav aria-label="Основные разделы">{nav.map((item) => { const Icon = item.icon; return <button key={item.href} title={item.label} aria-label={item.label} aria-current={active(item.href) ? 'page' : undefined} className={active(item.href) ? 'is-active' : ''} onClick={() => navigate(item.href)}><Icon size={18} /><span>{item.label}</span>{active(item.href) && <ChevronRight size={14} />}</button>; })}<details className="admin-legacy-nav" open={legacyNav.some((item) => active(item.href))}><summary><Wrench size={17} /><span>Обслуживание</span></summary>{legacyNav.map((item) => { const Icon = item.icon; return <button key={item.href} title={item.label} aria-current={active(item.href) ? 'page' : undefined} className={active(item.href) ? 'is-active' : ''} onClick={() => navigate(item.href)}><Icon size={15} /><span>{item.label}</span></button>; })}</details></nav>
    <div className="admin-console-sidebar__footer"><span>Администратор</span><button onClick={onLogout}><LogOut size={16} /> Выйти</button></div>
    <dialog ref={menuRef} className="campus-mobile-menu" aria-labelledby="campus-menu-title"><div className="campus-menu-heading"><div><h2 id="campus-menu-title">Разделы школы</h2><p>{dataSourceLabel}</p></div><button aria-label="Закрыть меню" onClick={() => menuRef.current?.close()}><X size={22} /></button></div><nav aria-label="Все разделы школы"><section><h3>Основные разделы</h3>{nav.map((item) => { const Icon = item.icon; return <button key={item.href} aria-current={active(item.href) ? 'page' : undefined} onClick={() => mobileNavigate(item.href)}><Icon size={19} /><span>{item.label}</span><ChevronRight size={15} /></button>; })}</section><details className="admin-legacy-nav"><summary><Wrench size={16} /> Обслуживание</summary>{legacyNav.map((item) => { const Icon = item.icon; return <button key={item.href} aria-current={active(item.href) ? 'page' : undefined} onClick={() => mobileNavigate(item.href)}><Icon size={17} /><span>{item.label}</span><ChevronRight size={15} /></button>; })}</details></nav><button className="campus-menu-logout" onClick={onLogout}><LogOut size={18} /> Выйти</button></dialog>
  </aside>;
}

function Header({ path, navigate }: { path: string; navigate: (href: string) => void }) {
  const title = path === '/admin' ? 'Рабочий стол завуча' : path.includes('/people/') ? 'Карточка человека' : path.includes('/groups/') ? 'Карточка группы' : [...nav, ...legacyNav].find((item) => path.startsWith(item.href) && item.href !== '/admin')?.label || 'Панель администратора';
  return <header className="admin-console-header"><div><div className="admin-breadcrumb"><button onClick={() => navigate('/admin')}>Админ</button><ChevronRight size={13} /><span>{path === '/admin' ? 'Сегодня' : title}</span></div><h1>{title}</h1></div><div className="admin-console-header__meta"><span className="campus-session-avatar" aria-hidden="true">А</span><span>Администратор</span></div></header>;
}

function Overview({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; overview?: RecordValue; system?: RecordValue; sources?: RecordValue[]; reconciliation?: RecordValue; schedule?: RecordValue; scheduleDrafts?: RecordValue[]; sourcesUnavailable?: boolean; reconciliationUnavailable?: boolean }>({ loading: true });
  const load = () => {
    setState({ loading: true });
    const today = new Date();
    const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    const weekStart = `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, '0')}-${String(monday.getDate()).padStart(2, '0')}`;
    Promise.allSettled([api<RecordValue>('/api/admin/overview'), api<RecordValue>('/api/admin/system'), api<{ items: RecordValue[] }>('/api/admin/sources'), api<RecordValue>('/api/admin/reconciliation/student-memberships/latest'), api<RecordValue>(`/api/admin/schedule/overview?week_start=${weekStart}`), api<{ items: RecordValue[] }>('/api/admin/schedule/experiments/school')]).then(([overviewResult, systemResult, sourcesResult, reconciliationResult, scheduleResult, scheduleDraftsResult]) => {
      if (overviewResult.status === 'rejected') { setState({ loading: false, error: overviewResult.reason }); return; }
      if (systemResult.status === 'rejected') { setState({ loading: false, error: systemResult.reason }); return; }
      setState({ loading: false, overview: overviewResult.value, system: systemResult.value, sources: sourcesResult.status === 'fulfilled' ? sourcesResult.value.items : [], reconciliation: reconciliationResult.status === 'fulfilled' ? reconciliationResult.value : undefined, schedule: scheduleResult.status === 'fulfilled' ? scheduleResult.value : undefined, scheduleDrafts: scheduleDraftsResult.status === 'fulfilled' ? scheduleDraftsResult.value.items : [], sourcesUnavailable: sourcesResult.status === 'rejected', reconciliationUnavailable: reconciliationResult.status === 'rejected' });
    });
  };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const overview = state.overview || {};
  const summary = state.reconciliation?.payload?.summary;
  const google = state.system?.settings?.google || {};
  const today = new Date();
  const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const currentWeekStart = `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, '0')}-${String(monday.getDate()).padStart(2, '0')}`;
  const currentWeekDraft = (state.scheduleDrafts || []).find((item) => item.week_start === currentWeekStart);
  const publishedWeek = state.schedule?.effective_week?.week_start;
  const publishedVersion = state.schedule?.canonical?.version_id;
  const priorities = [
    { label: 'Активные проблемы', value: overview.active_issues, hint: 'Проверить и разобрать открытые случаи', href: '/admin/issues?status=open' },
    { label: 'Изменения источников', value: overview.candidate_changes_unresolved, hint: 'Проверить нерешённые изменения', href: '/admin/candidates?status=unresolved' },
    { label: 'Непривязанные аккаунты', value: overview.unlinked_accounts, hint: 'Открыть людей и проверить привязки', href: '/admin/people/legacy' },
  ];
  return <div className="campus-dashboard">
    <div className="campus-dashboard-context"><p>Школьные данные, расписание и актуальные проверки.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>
    <div className="campus-overview-top">
      <section className="campus-date" aria-label="Текущая дата"><span>{today.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' })}</span><strong>{today.getDate()}<span>{today.toLocaleDateString('ru-RU', { weekday: 'long' })}</span></strong><div><b>Школьное пространство</b><p>{dataSourceLabel}</p></div></section>
      <section className="campus-panel campus-priorities"><div className="campus-panel-heading"><h2>Требует внимания</h2><AlertTriangle size={19} /></div>{priorities.map((item) => <button key={item.href} onClick={() => navigate(item.href)}><b>{display(item.value)}</b><span><strong>{item.label}</strong><small>{item.hint}</small></span><ChevronRight size={17} /></button>)}</section>
      <section className="campus-directory"><h2>Школьный справочник</h2><div>{[{ label: 'учеников', value: overview.canonical_students, href: '/admin/people?kind=student' }, { label: 'преподавателей', value: overview.canonical_teachers, href: '/admin/teachers' }, { label: 'групп', value: overview.groups, href: '/admin/people' }, { label: 'аккаунтов', value: overview.app_accounts, href: '/admin/people/legacy' }].map((item) => <button key={item.href} onClick={() => navigate(item.href)}><strong>{display(item.value)}</strong><span>{item.label}</span></button>)}</div><button className="admin-link" onClick={() => navigate('/admin/people')}>Состав школы <ChevronRight size={15} /></button></section>
    </div>
    <div className="campus-overview-lower">
      <section className="campus-panel campus-schedule-summary"><div className="campus-panel-heading"><div><h2>Расписание школы</h2><p>Неделя с {monday.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}</p></div><CalendarDays size={22} /></div><dl className="admin-definition-list"><dt>Опубликовано</dt><dd>{publishedWeek ? `Отдельная неделя с ${formatDate(publishedWeek)}` : publishedVersion ? `Основной шаблон · версия ${publishedVersion}` : statusLabel(state.schedule?.status || 'не загружено')}</dd><dt>Черновик недели</dt><dd>{currentWeekDraft ? `Есть незавершённые изменения · ${currentWeekDraft.name}` : 'Нет'}</dd><dt>Действие</dt><dd>{currentWeekDraft ? 'Проверьте черновик перед публикацией' : 'Проверки занятий доступны после выбора расписания'}</dd></dl><button className="admin-button admin-button--primary" onClick={() => navigate('/admin/schedule/constructor')}>Открыть расписание <ChevronRight size={17} /></button></section>
    </div>
    <details className="campus-details"><summary>Источники, результаты сверки и подключения</summary><div className="campus-detail-grid"><section className="campus-panel"><h2>Источники</h2>{state.sourcesUnavailable ? <p className="campus-help">Данные временно недоступны.</p> : state.sources?.length ? <div className="admin-list">{state.sources.slice(0, 6).map((source) => <button className="admin-list-row" key={source.id} onClick={() => navigate(`/admin/sources/${source.id}`)}><span><strong>{display(source.display_name)}</strong><small>{statusLabel(source.source_type)} · {formatDate(source.last_refresh)}</small></span><Status value={source.health} /></button>)}</div> : <Empty>Источники ещё не зарегистрированы</Empty>}<p className="campus-help">Источники: {display(overview.source_count)} · запусков: {display(overview.sync_runs_total)}</p></section><section className="campus-panel"><h2>Результаты сверки</h2>{state.reconciliationUnavailable ? <p className="campus-help">Результат временно недоступен.</p> : summary ? <div className="admin-inline-stats">{['KEEP', 'CREATE', 'DEACTIVATE', 'PROTECTED', 'UNRESOLVED'].map((key) => <div key={key}><strong>{display(summary[key])}</strong><span>{statusLabel(key)}</span></div>)}</div> : <Empty>Проверенное состояние отсутствует</Empty>}</section><section className="campus-panel"><div className="campus-panel-heading"><h2>Подключения школы</h2><button className="admin-link" onClick={() => navigate('/admin/system')}>Подробнее <ChevronRight size={14} /></button></div><div className="admin-system-strip"><Status value={state.system?.health?.schema_status} /><span>{statusLabel(state.system?.health?.environment)}</span><span>{state.system?.settings?.database_configured == null ? 'Состояние базы не указано' : state.system.settings.database_configured ? 'Школьная база подключена' : 'Локальная база данных'}</span><span>Google: {display(google.message, 'состояние не указано')}</span></div></section></div></details>
  </div>;
}

function People({ navigate }: { navigate: (href: string) => void }) {
  const [items, setItems] = useState<RecordValue[]>([]);
  const [filters, setFilters] = useState<{ classes: RecordValue[]; groups: RecordValue[] }>({ classes: [], groups: [] });
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [className, setClassName] = useState('');
  const [groupId, setGroupId] = useState('');
  const [issueFilter, setIssueFilter] = useState('');
  const [protectedFilter, setProtectedFilter] = useState('');
  const [state, setState] = useState<{ loading: boolean; error?: unknown }>({ loading: true });
  const load = () => { setState({ loading: true }); const params = new URLSearchParams(); if (query) params.set('q', query); if (kind) params.set('kind', kind); if (className) params.set('class_name', className); if (groupId) params.set('group_id', groupId); if (issueFilter) params.set('has_issues', issueFilter); if (protectedFilter) params.set('protected', protectedFilter); api<{ items: RecordValue[] }>(`/api/admin/people?${params}`).then((result) => { setItems(result.items); setState({ loading: false }); }).catch((error) => setState({ loading: false, error })); };
  useEffect(() => { const params = routeQuery(); setQuery(params.get('q') || ''); setKind(params.get('kind') || ''); setClassName(params.get('class_name') || ''); setGroupId(params.get('group_id') || ''); setIssueFilter(params.get('has_issues') || ''); setProtectedFilter(params.get('protected') || ''); api<{ classes: RecordValue[]; groups: RecordValue[] }>('/api/admin/people/filters').then(setFilters).catch(() => undefined); }, []);
  useEffect(() => { const timer = window.setTimeout(load, 220); return () => window.clearTimeout(timer); }, [query, kind, className, groupId, issueFilter, protectedFilter]);
  return <div className="admin-stack"><div className="admin-toolbar"><label className="admin-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по имени, классу или группе" /></label><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">Все роли</option><option value="student">Ученики</option><option value="teacher">Учителя</option></select><select value={className} onChange={(event) => setClassName(event.target.value)}><option value="">Все классы</option>{filters.classes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><select value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">Все группы</option>{filters.groups.map((item) => <option key={item.id} value={item.id}>{readableGroupName(item)}</option>)}</select><select value={issueFilter} onChange={(event) => setIssueFilter(event.target.value)}><option value="">Проблемы: любые</option><option value="true">Только с активными проблемами</option><option value="false">Без активных проблем</option></select><select value={protectedFilter} onChange={(event) => setProtectedFilter(event.target.value)}><option value="">Защита: любая</option><option value="true">Только защищённые случаи</option><option value="false">Без защищённого случая</option></select></div>{state.loading ? <Loading /> : state.error ? <ErrorState error={state.error} onRetry={load} /> : <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} записей</span><span>фильтры применяются на сервере</span></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Человек</th><th>Тип / класс</th><th>Аккаунт</th><th>Группы</th><th>Проблемы</th></tr></thead><tbody>{items.map((person) => <tr key={person.id} onClick={() => navigate(`/admin/people/${person.id}`)}><td><strong>{display(person.display_name)}</strong><small>ID {display(person.id)}{person.protected_case ? ' · защищённый случай' : ''}</small></td><td><span>{statusLabel(person.kind)}</span><small>{display(person.class_name)}</small></td><td><Status value={person.account_status || (person.user_id ? 'linked' : 'no account')} tone={person.user_id ? 'good' : 'warn'} /></td><td><span>{Array.isArray(person.instructional_memberships) ? person.instructional_memberships.length : display(person.groups?.length, '0')}</span><small>{person.base_classes?.map((item: RecordValue) => readableGroupName(item)).join(', ') || 'нет базового класса'}</small></td><td>{person.unresolved_count ? <Status value={`${person.unresolved_count} открыто`} tone="warn" /> : <Status value="clean" tone="good" />}</td></tr>)}</tbody></table></div> : <Empty>Люди не найдены</Empty>}</section>}</div>;
}

function PersonDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; person?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(`/api/admin/people/${encodeURIComponent(id)}`).then((person) => setState({ loading: false, person })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const person = state.person || {};
  const renderRows = (title: string, values: unknown[], fields: string[]) => <section className="admin-panel"><div className="admin-panel__heading"><h2>{title}</h2></div>{values.length ? <div className="admin-detail-list">{values.map((value: any, index) => <div key={`${title}-${index}`}><strong>{title === 'Членство в группах' ? readableGroupName(value) : display(value.display_name || value.name || value.subject || value.group_name)}</strong><small>{fields.map((field) => `${fieldLabels[field] || field}: ${readableValue(value[field], field)}`).join(' · ')}</small></div>)}</div> : <Empty>Нет записей</Empty>}</section>;
  return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/people')}><ArrowLeft size={15} /> Все люди</button><section className="admin-panel admin-identity-card"><div className="admin-avatar">{display(person.display_name, '?').slice(0, 1)}</div><div><p className="admin-eyebrow">ОСНОВНАЯ ЛИЧНОСТЬ</p><h2>{display(person.display_name)}</h2><p>{statusLabel(person.kind)} · ID {display(person.id)} · {statusLabel(person.status)}</p></div><div className="admin-identity-card__meta"><Status value={person.account_status || 'no account'} /><span>класс: {display(person.class_name)}</span></div></section><div className="admin-two-col"><section className="admin-panel"><h2>Текущее состояние в школе</h2><dl className="admin-definition-list"><dt>Базовые классы</dt><dd>{person.base_classes?.map((item: RecordValue) => readableGroupName(item)).join(', ') || '—'}</dd><dt>Учебные группы</dt><dd>{person.instructional_memberships?.map((item: RecordValue) => readableGroupName(item)).join(', ') || '—'}</dd><dt>Экзамены и профиль</dt><dd>{person.exam_profile_memberships?.map((item: RecordValue) => readableGroupName(item)).join(', ') || '—'}</dd><dt>Роли</dt><dd>{person.roles?.map((role: string) => statusLabel(role)).join(', ') || '—'}</dd></dl></section><section className="admin-panel"><h2>Источник и проблемы</h2><div className="admin-callout admin-callout--muted"><FileSearch size={16} /><span>Личностей из источников: {display(person.source_identities?.length, '0')}<br />Открытых проблем: {display(person.unresolved_count, '0')}<br />Нерешённые и защищённые случаи показываются явно; автоматическое объединение не выполняется.</span></div></section></div>{renderRows('Членство в группах', person.memberships || person.instructional_memberships || [], ['active', 'valid_from', 'valid_until', 'source', 'source_ref'])}{renderRows('Назначения учителя', person.teacher_assignments || [], ['group_name', 'subject', 'active', 'source'])}{renderRows('История аудита', person.audit_history || [], ['action', 'created_at'])}</div>;
}

function Groups({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); api<{ items: RecordValue[] }>('/api/admin/groups').then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{state.items?.length || 0} основных групп</span><span>стабильное членство ≠ аудитория урока</span></div>{state.items?.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Группа</th><th>Тип / предмет</th><th>Область</th><th>Ученики</th><th>Учителя</th><th>Источник</th></tr></thead><tbody>{state.items.map((group) => <tr key={group.id} onClick={() => navigate(`/admin/groups/${group.id}`)}><td><strong>{display(group.display_name || group.name)}</strong><small>ключ: {display(group.name)}</small></td><td><span>{statusLabel(group.group_type)}</span><small>{display(group.subject)}</small></td><td><span>{display(group.base_class_name)}</span><small>{display(group.subject_subgroup)} {display(group.exam_track, '')}</small></td><td>{display(group.student_count, '0')}</td><td>{display(group.teacher_count, '0')}</td><td><small>{display(group.provenance_source)}</small></td></tr>)}</tbody></table></div> : <Empty>Группы не найдены</Empty>}</section>;
}

function GroupDetail({ id, navigate }: { id: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; group?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(`/api/admin/groups/${encodeURIComponent(id)}`).then((group) => setState({ loading: false, group })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const group = state.group || {};
  return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/groups')}><ArrowLeft size={15} /> Все группы</button><section className="admin-panel"><p className="admin-eyebrow">ОСНОВНАЯ ГРУППА</p><h2>{readableGroupName(group)}</h2><div className="admin-chip-row"><Status value={group.group_type} /><span>идентификатор: {readableGroupName(group.name)}</span><span>основная: {statusLabel(group.canonical)}</span><span>источник: {statusLabel(group.provenance_source)}</span></div></section><div className="admin-two-col"><section className="admin-panel"><h2>Метаданные</h2><dl className="admin-definition-list"><dt>Предмет</dt><dd>{display(group.subject)}</dd><dt>Базовая область</dt><dd>{display(group.base_class_name)}</dd><dt>Подгруппа</dt><dd>{display(group.subject_subgroup)}</dd><dt>Экзаменационный профиль</dt><dd>{display(group.exam_track)}</dd><dt>Ссылка на источник</dt><dd className="admin-mono">{display(group.provenance_ref)}</dd></dl></section><section className="admin-panel"><h2>Граница сверки</h2><div className="admin-callout admin-callout--muted"><ShieldCheck size={16} /><span>Здесь показан основной список группы. Аудитория расписания и вычисленная аудитория намеренно не смешиваются.</span></div></section></div><section className="admin-panel"><h2>Активные ученики ({display(group.student_count, '0')})</h2>{group.students?.length ? <div className="admin-detail-list">{group.students.map((student: RecordValue) => <button key={student.id} onClick={() => navigate(`/admin/people/${student.id}`)}><strong>{display(student.display_name)}</strong><small>{display(student.class_name)} · {statusLabel(student.source)}</small></button>)}</div> : <Empty>В списке нет активных учеников</Empty>}</section><section className="admin-panel"><h2>Назначения учителей</h2>{group.teacher_assignments?.length ? <div className="admin-detail-list">{group.teacher_assignments.map((teacher: RecordValue, index: number) => <div key={index}><strong>{display(teacher.display_name || teacher.teacher_name)}</strong><small>{display(teacher.subject)} · {statusLabel(teacher.active)}</small></div>)}</div> : <Empty>Назначений нет</Empty>}</section></div>;
}

function Sources({ id, navigate }: { id?: string; navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; data?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); api<RecordValue>(id ? `/api/admin/sources/${id}` : '/api/admin/sources').then((data) => setState({ loading: false, data })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [id]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  if (id) { const source = state.data?.source || {}; return <div className="admin-stack"><button className="admin-back" onClick={() => navigate('/admin/sources')}><ArrowLeft size={15} /> Все источники</button><section className="admin-panel"><p className="admin-eyebrow">ДАННЫЕ ИСТОЧНИКА</p><h2>{display(source.display_name)}</h2><div className="admin-chip-row"><Status value={source.authority_status} /><span>{display(source.source_type)}</span><span>{display(source.external_key)}</span><span>{display(source.location_ref)}</span></div></section><div className="admin-two-col"><DataList title="Снимки" items={state.data?.snapshots || []} fields={['fingerprint', 'status', 'observed_at']} /><DataList title="Запуски синхронизации" items={state.data?.runs || []} fields={['status', 'mode', 'started_at', 'finished_at']} /></div><div className="admin-two-col"><DataList title="Записи" items={state.data?.records || []} fields={['record_key', 'change_kind', 'parse_status', 'source_ref']} /><DataList title="Проблемы и сопоставления" items={[...(state.data?.issues || []), ...(state.data?.mappings || [])]} fields={['issue_type', 'status', 'external_key', 'mapping_type']} /></div></div>; }
  const items = state.data?.items || []; return <div className="admin-stack"><ScheduleSourceReview api={api} /><section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} зарегистрированных источников</span><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Источник</th><th>Авторитетность</th><th>Последнее обновление</th><th>Отпечаток</th><th>Записи</th><th>Проблемы</th></tr></thead><tbody>{items.map((source: RecordValue) => <tr key={source.id} onClick={() => navigate(`/admin/sources/${source.id}`)}><td><strong>{display(source.display_name)}</strong><small>{display(source.source_type)} · {display(source.external_key)}</small></td><td><Status value={source.authority_status} /></td><td>{formatDate(source.last_refresh)}<small>{statusLabel(source.last_run_status)}</small></td><td className="admin-mono">{display(source.current_fingerprint)}</td><td>{display(source.record_count, '0')}</td><td>{source.unresolved_count || source.candidate_change_count ? <Status value={`${display(source.unresolved_count, '0')} / ${display(source.candidate_change_count, '0')}`} tone="warn" /> : <Status value="0" tone="good" />}</td></tr>)}</tbody></table></div> : <Empty>Источники школьных данных ещё не зарегистрированы</Empty>}</section></div>;
}

function DataList({ title, items, fields }: { title: string; items: RecordValue[]; fields: string[] }) {
  return <section className="admin-panel"><h2>{title} ({items.length})</h2>{items.length ? <div className="admin-detail-list">{items.slice(0, 50).map((item, index) => <div key={item.id || index}><strong>{display(item.display_name || item.record_key || item.issue_type || item.external_key || item.status)}</strong><small>{fields.map((field) => `${fieldLabels[field] || field}: ${statusLabel(item[field])}`).join(' · ')}</small></div>)}</div> : <Empty>Нет записей</Empty>}</section>;
}

function Issues({ navigate }: { navigate: (href: string) => void }) {
  const [status, setStatus] = useState(routeQuery().get('status') || 'open');
  const [issueType, setIssueType] = useState('');
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); const params = new URLSearchParams({ status }); if (issueType) params.set('issue_type', issueType); api<{ items: RecordValue[] }>(`/api/admin/issues?${params}`).then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [status, issueType]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const items = state.items || [];
  return <div className="admin-stack"><div className="admin-page-actions"><p>Активные проблемы требуют решения. Решённые случаи сохраняются в истории и не считаются текущими.</p><div className="admin-button-row"><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">Только открытые</option><option value="resolved">Только решённые</option><option value="ignored">Только пропущенные</option></select><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div></div><section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} проблем</span><span>без автоматического исправления</span></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Тип</th><th>Статус</th><th>Источник</th><th>Синхронизация</th><th>Описание</th><th>Создано</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{statusLabel(item.issue_type)}</strong><small>№ {display(item.id)}</small></td><td><Status value={item.status} tone={item.status === 'open' ? 'warn' : 'neutral'} /></td><td>{display(item.source_name)}<small>{display(item.source_record_key)}</small></td><td>{statusLabel(item.sync_status)}<small>{formatDate(item.sync_finished_at)}</small></td><td className="admin-json-cell">{display(item.details)}</td><td>{formatDate(item.created_at)}<small>{item.resolved_at ? `решено: ${formatDate(item.resolved_at)}` : ''}</small></td></tr>)}</tbody></table></div> : <Empty>Проблем такого статуса нет</Empty>}</section></div>;
}

function Candidates() {
  const [status, setStatus] = useState(routeQuery().get('status') || '');
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); const params = new URLSearchParams(); if (status) params.set('status', status); api<{ items: RecordValue[] }>(`/api/admin/candidates?${params}`).then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, [status]);
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const items = state.items || [];
  return <div className="admin-stack"><div className="admin-page-actions"><p>Кандидаты источников — это история изменений и результаты синхронизаций. Здесь ничего не применяется в канонические данные.</p><div className="admin-button-row"><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Все статусы</option><option value="unresolved">Нерешённые</option><option value="pending">Ожидающие</option><option value="conflict">Конфликты</option><option value="applied">Применённые (история)</option></select><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div></div><section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{items.length} записей</span><span>без записи в базу из этого раздела</span></div>{items.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Статус</th><th>Сущность</th><th>Изменение</th><th>Источник / запуск</th><th>Естественный ключ</th><th>Время</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><Status value={item.status} tone={item.status === 'unresolved' || item.status === 'conflict' ? 'warn' : item.status === 'applied' ? 'neutral' : 'good'} /></td><td>{statusLabel(item.entity_type)}<small>№ {display(item.id)}</small></td><td>{statusLabel(item.change_type)}</td><td>{display(item.source_name)}<small>запуск № {display(item.sync_run_id)} · {statusLabel(item.sync_status)}</small></td><td className="admin-mono">{display(item.natural_key)}</td><td>{formatDate(item.created_at)}<small>{item.applied_at ? `применено: ${formatDate(item.applied_at)}` : ''}</small></td></tr>)}</tbody></table></div> : <Empty>Изменений такого статуса нет</Empty>}</section></div>;
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
  return <div className="admin-stack"><div className="admin-page-actions"><p>Только просмотр: пробный запуск → проверка → повторная проверка на сервере → транзакционное применение.</p><div className="admin-button-row"><button className="admin-button admin-button--quiet" disabled={!!busy} onClick={() => void act('dry-run', '/api/admin/reconciliation/student-memberships/dry-run', { method: 'POST' })}><RefreshCw size={15} /> {busy === 'dry-run' ? 'Запускаем…' : 'Запустить пробную сверку'}</button>{run.status === 'ready_for_review' && <button className="admin-button admin-button--primary" disabled={!!busy} onClick={() => void act('review', `/api/admin/reconciliation/student-memberships/runs/${run.id}/review`, { method: 'POST', body: JSON.stringify({ approved: true }) })}>{busy === 'review' ? 'Сохраняем…' : 'Отметить проверенным'}</button>}{run.status === 'approved' && <button className="admin-button admin-button--danger" disabled={!!busy} onClick={() => { if (window.confirm('Применить проверенную сверку после повторной проверки на сервере?')) void act('apply', `/api/admin/reconciliation/student-memberships/apply?run_id=${run.id}`, { method: 'POST' }); }}>{busy === 'apply' ? 'Применяем…' : 'Применить план'}</button>}</div></div>{Boolean(state.error) && <ErrorState error={state.error} />}{!payload.summary ? <section className="admin-panel"><Empty>Пробный запуск ещё не выполнялся. Production-отчёты не считаются автоматически проверенным запуском.</Empty></section> : <><section className="admin-panel"><div className="admin-panel__heading"><div><p className="admin-eyebrow">ЗАПУСК №{display(run.id)} · {statusLabel(run.status)}</p><h2>Сверка членства учеников</h2></div><span>{formatDate(payload.generated_at)}</span></div><div className="admin-inline-stats">{['KEEP', 'CREATE', 'DEACTIVATE', 'PROTECTED', 'UNRESOLVED', 'WRONG_GROUP', 'STALE', 'invariant_violations'].map((key) => <div key={key} className={key === 'PROTECTED' ? 'is-protected' : key === 'UNRESOLVED' || key === 'invariant_violations' ? 'is-warning' : ''}><strong>{display(summary[key], '0')}</strong><span>{key === 'invariant_violations' ? 'нарушения инвариантов' : statusLabel(key)}</span></div>)}</div><div className="admin-callout admin-callout--safe"><CheckCircle2 size={16} /><span>Режим: {statusLabel(payload.mode)}. Записей в production во время пробного запуска: {display(payload.production_writes_performed, '0')}.</span></div></section><section className="admin-panel admin-table-panel"><div className="admin-panel__heading"><h2>Изменения ({assignments.length})</h2><span>ОСТАВИТЬ / СОЗДАТЬ / ДЕАКТИВИРОВАТЬ / ЗАЩИЩЕНО / НЕ РЕШЕНО</span></div>{assignments.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Ученик</th><th>Группа</th><th>Действие</th><th>Классификация</th><th>Причина / источник</th></tr></thead><tbody>{assignments.map((item: RecordValue, index: number) => <tr key={item.membership_id || `${item.identity_id}-${item.group_id}-${index}`}><td><strong>{display(item.student)}</strong><small>{display(item.identity_id)}</small></td><td><span>{readableGroupName(item.group)}</span><small>{display(item.group_id)}</small></td><td><Status value={item.action} tone={item.action === 'PROTECTED' ? 'warn' : item.action === 'CREATE' ? 'good' : item.action === 'DEACTIVATE' ? 'bad' : 'neutral'} /></td><td>{statusLabel(item.classification)}</td><td><span>{display(item.reason || item.stale_reason)}</span><small>{display(item.current_source_ref || item.source_refs?.join(', '))}</small></td></tr>)}</tbody></table></div> : <Empty>Изменений в назначениях нет</Empty>}</section><div className="admin-two-col"><DataList title="Проблемы" items={issueItems} fields={['kind', 'reason', 'details']} /><DataList title="Инварианты" items={invariantItems} fields={['name', 'status', 'details']} /></div></>}</div>;
}

function ClassroomHistory() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; journals?: RecordValue[]; classroom?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); Promise.all([api<{ items: RecordValue[] }>('/api/admin/journals'), api<{ items: RecordValue[] }>('/api/admin/classroom/syncs')]).then(([journals, classroom]) => setState({ loading: false, journals: journals.items, classroom: classroom.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <div className="admin-stack"><div className="admin-page-actions"><p>Официальный снимок журнала и метаданные Классрума остаются разными источниками.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><div className="admin-two-col"><DataList title="Источники журналов" items={state.journals || []} fields={['spreadsheet_title', 'sheet_title', 'grade', 'subject', 'status', 'last_synced_at', 'last_error', 'unlinked_students', 'ambiguous_students']} /><DataList title="История синхронизации Классрума" items={state.classroom || []} fields={['action', 'entity_type', 'details', 'created_at']} /></div><section className="admin-panel"><div className="admin-callout admin-callout--muted"><BookOpen size={16} /><span>Панель не показывает Классрум как официальный электронный журнал и не подменяет оценки журнала данными Классрума.</span></div></section></div>;
}

function Audit() {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; items?: RecordValue[] }>({ loading: true });
  const load = () => { setState({ loading: true }); api<{ items: RecordValue[] }>('/api/admin/audit?limit=100').then((result) => setState({ loading: false, items: result.items })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  return <section className="admin-panel admin-table-panel"><div className="admin-table-meta"><span>{state.items?.length || 0} событий</span><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div>{state.items?.length ? <div className="admin-table-wrap"><table className="admin-table"><thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Подробности</th></tr></thead><tbody>{state.items.map((item) => <tr key={item.id}><td>{formatDate(item.created_at)}</td><td>{display(item.actor_name)}</td><td className="admin-mono">{display(item.action)}</td><td>{display(item.entity_type)} №{display(item.entity_id)}</td><td className="admin-mono">{display(item.details)}</td></tr>)}</tbody></table></div> : <Empty>Журнал аудита пуст</Empty>}</section>;
}

function System({ navigate }: { navigate: (href: string) => void }) {
  const [state, setState] = useState<{ loading: boolean; error?: unknown; data?: RecordValue; overview?: RecordValue }>({ loading: true });
  const load = () => { setState({ loading: true }); Promise.all([api<RecordValue>('/api/admin/system'), api<RecordValue>('/api/admin/overview').catch(() => ({} as RecordValue))]).then(([data, overview]) => setState({ loading: false, data, overview })).catch((error) => setState({ loading: false, error })); };
  useEffect(load, []); if (state.loading) return <Loading />; if (state.error) return <ErrorState error={state.error} onRetry={load} />;
  const data = state.data || {};
  const countLinks: Record<string, string> = { users: '/admin/people', identities: '/admin/people', groups: '/admin/groups', memberships: '/admin/groups', school_sources: '/admin/sources', school_sync_runs: '/admin/sources', school_candidate_changes: '/admin/candidates', school_resolution_issues: '/admin/issues', audit_log: '/admin/audit' };
  const countLabels: Record<string, string> = { users: 'аккаунты приложения', identities: 'канонические личности', groups: 'основные группы', memberships: 'активные членства', school_sources: 'источники школы', school_sync_runs: 'запуски синхронизации', school_candidate_changes: 'кандидаты изменений', school_resolution_issues: 'проблемы разрешения', audit_log: 'события аудита' };
  return <div className="admin-stack"><div className="admin-page-actions"><p>Подключения и состояние школьных сервисов.</p><button className="admin-button admin-button--quiet" onClick={load}><RefreshCw size={15} /> Обновить</button></div><section className="admin-panel"><div className="admin-panel__heading"><h2>Backend и школьная база</h2><Status value={data.health?.schema_status} /></div><dl className="admin-definition-list"><dt>Backend</dt><dd><Status value="healthy" /> доступен</dd><dt>Школьная база</dt><dd><Status value={data.settings?.database_configured ? 'configured' : 'local'} /> {data.settings?.database_configured ? 'подключена' : 'локальный режим'}</dd></dl></section><GoogleConnectionCard api={api} apiOrigin={API_BASE} /><section className="admin-panel"><h2>Синхронизация</h2><dl className="admin-definition-list"><dt>Последняя успешная</dt><dd>{formatDate(state.overview?.last_successful_sync)}</dd><dt>Последняя сверка</dt><dd>{statusLabel(state.overview?.last_reconciliation_status)} · {formatDate(state.overview?.last_reconciliation_at)}</dd></dl></section><details className="admin-panel workspace-details"><summary>Диагностика и технические сведения</summary><h3>Записи школьной базы</h3><div className="admin-inline-stats">{Object.entries(data.health?.counts || {}).map(([key, value]) => countLinks[key] ? <button className="admin-inline-stat admin-inline-stat--clickable" key={key} onClick={() => navigate(countLinks[key])}><strong>{String(value)}</strong><span>{countLabels[key] || key}</span></button> : <div key={key}><strong>{String(value)}</strong><span>{countLabels[key] || key}</span></div>)}</div><div className="admin-callout admin-callout--muted"><Database size={16} /><span>Применённых исторических изменений: {display(data.health?.semantics?.candidate_changes?.historical, '0')} · активных кандидатов: {display(data.health?.semantics?.candidate_changes?.active, '0')} · открытых проблем: {display(data.health?.semantics?.resolution_issues?.active, '0')}.</span></div><h3>Проверки схемы</h3><div className="admin-detail-list">{Object.entries(data.health?.schema || {}).map(([key, value]: [string, any]) => <div key={key}><strong>{key}</strong><small><Status value={value.ok ? 'current' : 'attention'} /> · требуется: {value.required.join(', ')}</small></div>)}</div></details></div>;
}

function ResponsiveSchedule() {
  const [mobile, setMobile] = useState<boolean | null>(null);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 740px)');
    const update = () => setMobile(media.matches); update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  if (mobile === null) return <Loading />;
  return mobile ? <MobilePublishedSchedule api={api} /> : <ScheduleConstructor api={api} />;
}

function AppContent({ path, navigate }: { path: string; navigate: (href: string) => void }) {
  if (path === '/admin/preview') return <MiniappPreview key={`${routeQuery().get('identity_id') || ''}:${routeQuery().get('kind') || ''}`} api={api} sourceLabel={dataSourceLabel} initialIdentity={routeQuery().get('identity_id') || ''} initialKind={routeQuery().get('kind') || 'student'} initialDay={routeQuery().get('day') || ''} localAdmin={Boolean(localDevAuth())} />;
  if (path === '/admin/teachers') return <TeacherDirectoryAdmin connectionLabel={dataSourceLabel} api={schoolMode ? schoolApi : api} schoolMode={schoolMode} navigate={navigate} />;
  if (path === '/admin/people' || path === '/admin/people/groups') return <SchoolDirectoryAdmin connectionLabel={dataSourceLabel} api={schoolMode ? schoolApi : api} section="groups" navigate={navigate} schoolMode={schoolMode} initialGroupId={routeQuery().get('group_id') || undefined} />;
  if (path === '/admin/people/legacy') return <People navigate={navigate} />;
  if (path.startsWith('/admin/people/')) return <PersonDetail id={path.split('/').pop() || ''} navigate={navigate} />;
  if (path.startsWith('/admin/groups/')) return <GroupDetail id={path.split('/').pop() || ''} navigate={navigate} />;
  if (path === '/admin/groups') return <Groups navigate={navigate} />;
  if (path === '/admin/issues') return <Issues navigate={navigate} />;
  if (path === '/admin/candidates') return <Candidates />;
  if (path.startsWith('/admin/sources/')) return <Sources id={path.split('/').pop()} navigate={navigate} />;
  if (path === '/admin/sources') return <Sources navigate={navigate} />;
  if (path === '/admin/reconciliation' || path.startsWith('/admin/reconciliation/')) return <Reconciliation />;
  if (path === '/admin/schedule/constructor') return <ResponsiveSchedule />;
  if (path === '/admin/schedule') return <ResponsiveSchedule />;
  if (path === '/admin/journals') return <AdminJournals api={api} apiOrigin={API_BASE} />;
  if (path === '/admin/classroom') return <ClassroomHistory />;
  if (path === '/admin/audit') return <Audit />;
  if (path === '/admin/settings') return <System navigate={navigate} />;
  if (path === '/admin/system') return <System navigate={navigate} />;
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
  return <div className={`admin-console campus-shell ${modernWorkspace ? 'admin-workspace' : ''} ${path.startsWith('/admin/schedule') ? 'admin-console--schedule' : ''} ${path === '/admin/schedule/constructor' ? 'admin-console--constructor' : ''}`}>{modernWorkspace ? <a className="admin-skip-link" href="#admin-content">Перейти к содержимому</a> : null}<Sidebar path={path} navigate={navigate} onLogout={() => void logout()} /><main id="admin-content" tabIndex={-1} className="admin-console-main">{!path.startsWith('/admin/schedule') ? <Header path={path} navigate={navigate} /> : null}<div className="admin-role-toolbar"><CampusRoleSwitch roles={session.roles || [session.role]} value="admin" onChange={switchRole} /></div><AppContent path={path} navigate={navigate} /></main></div>;
}


