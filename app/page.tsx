'use client';

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { campusLessons, campusStudentDays } from '../components/campus-schedule';
import { formatLessonTime, formatLessonTimeRange } from '../components/lesson-time';
import { normalizeRoomDisplay } from '../components/schedule-rooms';
import { CampusRoleSwitch, CampusPeriod, dateISO, periodFor, useCampusScreen, useTelegramBack, type CampusRole } from '../components/campus-controls';
import { campusNavigationForContext, campusNavigationFromHistory, campusNavigationReducer, initialCampusNavigation, type CampusBuilding, type CampusNavigationAction, type CampusNavigationState } from '../components/campus-navigation';
const ModernAdmin = dynamic(() => import('./admin/page'), { ssr: false });
const teacherScreens = ['schedule', 'groups', 'information', 'homeroom', 'profile'] as const;
import {
  BarChart3,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  ExternalLink,
  FileText,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Megaphone,
  Network,
  Pin,
  RefreshCw,
  School,
  Search,
  Settings,
  ShieldCheck,
  UserRound,
  Users,
  X,
} from 'lucide-react';
import {
  islandLocations,
  teacherCampusLocations,
  type IslandLocationId,
} from './island-config';
import { IslandIcon, IslandMark, type IslandIconName } from './island-icons';
import { useTelegramLayout } from './telegram-layout';
import { useSceneMotion } from './scene-motion';
import {
  StudentGradesPeek,
  StudentRoleJournals,
  TeacherRoleJournals,
  type RoleJournalsApi,
} from '../components/role-journals';

type LoadState =
  | 'loading'
  | 'approved'
  | 'needs_identity'
  | 'telegram_sdk_missing'
  | 'telegram_init_data_empty'
  | 'telegram_rejected'
  | 'network_error'
  | 'api_error';
type StudentView = IslandLocationId | 'profile' | null;
type TeacherView =
  | 'schedule'
  | 'groups'
  | 'information'
  | 'homeroom'
  | 'profile'
  | null;
type AdminSection =
  | 'overview'
  | 'preview'
  | 'people'
  | 'structure'
  | 'schedule'
  | 'integrations'
  | 'settings'
  | 'diagnostics';
type Lesson = {
  lesson_date: string;
  start_time: string;
  end_time?: string | null;
  subject: string;
  teacher?: string | null;
  room?: string | null;
  audience?: string | null;
  subject_subgroup?: string | null;
  exam_track?: string | null;
  lesson_type?: string | null;
  delivery_mode?: string | null;
  activity_format?: string | null;
  physical_presence?: boolean;
  responsible_teacher?: string | null;
  source_coordinate?: string | null;
};
type StudentDayBlock = {
  lesson_date: string;
  kind: 'sdep';
  label: string;
  title: string;
  description: string;
};
type StudentScheduleItem = {
  date: string;
  slot: { start: string; end?: string | null };
  state: 'lesson' | 'no_lesson' | 'conflict' | 'special_event';
  canonical_title?: string | null;
  room?: string | null;
  teacher?: string | null;
  group_context?: string[] | string | null;
  activity_format?: string | null;
  responsible_teacher?: string | null;
  no_lesson?: { reason?: 'before_first_lesson' | 'break' | 'end_of_day' };
  conflict?: { state: string; label: string; alternatives: Array<{ title: string; teacher?: string | null; room?: string | null }> };
  special_event?: { kind: string; title: string; metadata?: Record<string, unknown> };
};
type StudentScheduleDay = {
  date: string;
  state: 'scheduled' | 'special';
  items: StudentScheduleItem[];
  mode?: 'online' | 'sdep';
  special?: { kind: 'sdep'; title: string; description?: string };
};
type StudentScheduleProjection = {
  days: StudentScheduleDay[];
  items: Lesson[];
  day_blocks: StudentDayBlock[];
  day_modes?: Array<{ date: string; mode: 'online' | 'sdep' }>;
  identity_id?: string;
  start_day?: string;
};
type Homework = {
  external_coursework_id: string;
  title: string;
  description?: string | null;
  due_at?: string | null;
  alternate_link?: string | null;
  state?: string | null;
  course_title?: string | null;
};
type Information = {
  id?: string;
  title: string;
  body?: string;
  content?: string;
  audience_kind?: string;
  expires_at?: string;
  pinned?: boolean;
  status?: string;
  author_name?: string;
};
type Group = {
  id?: string;
  name: string;
  display_name?: string;
  group_type?: string;
  relationship_kind?: string;
  base_class_name?: string | null;
  source?: string;
  subject?: string;
  subject_subgroup?: string;
  exam_track?: string | null;
  student_count?: number;
  member_count?: number;
  students?: Array<Record<string, unknown>>;
  teacher_assignments?: Array<Record<string, unknown>>;
  is_homeroom?: boolean;
  schedule_scopes?: Array<{
    audience: string;
    subject?: string;
    subject_subgroup?: string;
    exam_track?: string;
  }>;
};
type Profile = {
  user: { id?: string | number; identity_id?: string | number; display_name: string; class_name?: string | null };
  groups: Group[];
};
type Session = {
  mode: string;
  state: string;
  user: {
    id: string | number;
    role: string;
    roles?: string[];
    identity_id?: string | null;
  };
};
type ApiData = {
  today: {
    schedule: Lesson[];
    day_block?: StudentDayBlock | null;
    homework: Homework[];
    announcements: Information[];
  };
  schedule: Lesson[];
  scheduleProjection: StudentScheduleProjection;
  scheduleState: string;
  scheduleDayBlocks: StudentDayBlock[];
  homework: Homework[];
  profile: Profile;
};
type TeacherGroupDetail = {
  students: Array<Record<string, unknown>>;
  grade_histories: Array<Record<string, unknown>>;
  assessments: Array<Record<string, unknown>>;
  analytics: Record<string, unknown>;
};
type TeacherData = {
  courses: Array<Record<string, unknown>>;
  schedule: Lesson[];
  dayModes: Array<{ date: string; mode: 'online' | 'sdep'; class_name?: string }>;
  today: Lesson[];
  groups: Group[];
  information: Information[];
  profile: Profile | null;
};
type AdminData = {
  overview: Record<string, number>;
  claims: Array<Record<string, string>>;
  users: Array<Record<string, unknown>>;
  people: Array<Record<string, unknown>>;
  groups: Array<Record<string, unknown>>;
  scheduleSyncs: Array<Record<string, unknown>>;
  classroomSyncs: Array<Record<string, unknown>>;
  journals: Array<Record<string, unknown>>;
  information: Array<Record<string, unknown>>;
  parseIssues: Array<Record<string, unknown>>;
  audit: Array<Record<string, unknown>>;
};
type AdminEntityRef = {
  kind: 'person' | 'class' | 'group';
  id: string;
};

const runtimeEnv =
  (import.meta as ImportMeta & { env?: Record<string, string | boolean> })
    .env || {};
const isLocalBuild =
  runtimeEnv.DEV === true || runtimeEnv.MODE === 'development';
const API_BASE = String(
  runtimeEnv.VITE_API_BASE_URL ||
    (isLocalBuild ? 'http://127.0.0.1:8000' : ''),
).replace(/\/$/, '');
let activeTelegramInitData = '';
const activeStudentPreviewId = '';
let activeRolePreviewId = '';
let previewLocalAdmin = false;
let previewInitData = '';

function devHeader(): string | undefined {
  if (!isLocalBuild || typeof window === 'undefined') return undefined;
  if (new URLSearchParams(window.location.search).has('embeddedPreview')) return previewLocalAdmin && runtimeEnv.VITE_SCHOOL_DIRECTORY_MODE !== 'school' ? 'admin:1' : undefined;
  const role = new URLSearchParams(window.location.search).get('role');
  return role === 'admin'
    ? 'admin:1'
    : role === 'teacher'
      ? 'teacher:1'
      : 'student:1';
}
async function telegramInitData(): Promise<
  | { kind: 'ready'; value: string }
  | { kind: 'telegram_sdk_missing' | 'telegram_init_data_empty' }
> {
  if (isLocalBuild && devHeader()) return { kind: 'ready', value: '' };
  if (typeof window !== 'undefined' && window.sessionStorage.getItem('my_island_admin_session')) return { kind: 'ready', value: '' };
  const saved = typeof window !== 'undefined' ? window.sessionStorage.getItem('my_island_telegram_init_data') : '';
  if (saved) return { kind: 'ready', value: saved };
  const deadline = Date.now() + 3000;
  let found = false;
  while (Date.now() < deadline) {
    const app =
      typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;
    if (app) {
      found = true;
      if (app.initData) { window.sessionStorage.setItem('my_island_telegram_init_data', app.initData); return { kind: 'ready', value: app.initData }; }
    }
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  return { kind: found ? 'telegram_init_data_empty' : 'telegram_sdk_missing' };
}
class ApiError extends Error {
  constructor(
    public readonly kind: 'telegram_rejected' | 'network_error' | 'api_error',
    message: string,
    public readonly status?: number,
  ) {
    super(message);
  }
}
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!API_BASE) {
    throw new Error('Для production-сборки My Island не настроен адрес API.');
  }
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body) headers.set('Content-Type', 'application/json');
  const local = devHeader();
  if (local) headers.set('X-Dev-Auth', local);
  const browserToken = typeof window !== 'undefined' ? window.sessionStorage.getItem('my_island_admin_session') : null;
  if (browserToken) headers.set('X-Dev-Auth', `browser:${browserToken}`);
  if (activeRolePreviewId) {
    if (options.method && options.method.toUpperCase() !== 'GET') throw new ApiError('api_error', 'Предпросмотр доступен только для чтения', 403);
    headers.set('X-Preview-Id', activeRolePreviewId);
    if (previewInitData && !browserToken) headers.set('X-Telegram-Init-Data', previewInitData);
  }
  if (activeTelegramInitData && !activeRolePreviewId && !browserToken)
    headers.set('X-Telegram-Init-Data', activeTelegramInitData);
  if (activeStudentPreviewId)
    headers.set('X-Student-Preview-Id', activeStudentPreviewId);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'include' });
  } catch {
    throw new ApiError('network_error', 'Сервис временно недоступен');
  }
  const body = (await response.json().catch(() => null)) as {
    detail?: unknown;
  } | null;
  if (!response.ok)
    throw new ApiError(
      response.status === 401 ? 'telegram_rejected' : 'api_error',
      typeof body?.detail === 'string'
        ? body.detail
        : `Запрос не выполнен (${response.status})`,
      response.status,
    );
  return body as T;
}

const isoDate = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
const addDays = (value: Date, days: number) => {
  const copy = new Date(value);
  copy.setDate(copy.getDate() + days);
  return copy;
};
const weekStart = (value = new Date()) =>
  addDays(
    new Date(value.getFullYear(), value.getMonth(), value.getDate()),
    -((value.getDay() + 6) % 7),
  );
const formatDate = (value: string, long = false) =>
  new Intl.DateTimeFormat(
    'ru-RU',
    long
      ? { weekday: 'long', day: 'numeric', month: 'long' }
      : { day: 'numeric', month: 'short' },
  ).format(new Date(`${value}T12:00:00`));
const formatDue = (value?: string | null) =>
  !value
    ? 'Срок не указан'
    : new Intl.DateTimeFormat('ru-RU', {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(value));
const displayValue = (value: unknown, fallback = '') =>
  typeof value === 'string' || typeof value === 'number'
    ? String(value)
    : fallback;
const humanRole = (role: unknown) =>
  ({ student: 'Ученик', teacher: 'Учитель', admin: 'Администратор' })[
    displayValue(role)
  ] || displayValue(role, 'Роль не указана');
const humanGroupType = (groupType: unknown) =>
  ({
    class: 'Базовый класс',
    subject_group: 'Предметная группа',
    subject_subgroup: 'Предметная группа',
    exam_track: 'Экзаменационный трек',
    instructional_group: 'Учебная группа',
    classroom_course: 'Группа Classroom',
  })[displayValue(groupType)] || displayValue(groupType, 'Группа');
const groupLabel = (group: Record<string, unknown>) =>
  displayValue(group.display_name, displayValue(group.name, 'Группа'));
const personBaseClass = (person: Record<string, unknown>) => {
  const base = person.base_class as Record<string, unknown> | null | undefined;
  return displayValue(base?.name, displayValue(person.class_name));
};
const personMemberships = (person: Record<string, unknown>) => [
  ...(Array.isArray(person.instructional_memberships)
    ? person.instructional_memberships
    : []),
  ...(Array.isArray(person.exam_profile_memberships)
    ? person.exam_profile_memberships
    : []),
];
const personLabel = (user: Record<string, unknown>) =>
  user.identity_id
    ? displayValue(user.display_name, 'Школьная личность')
    : 'Telegram-аккаунт без школьной личности';
const personSummary = (user: Record<string, unknown>) => {
  const roles = Array.isArray(user.roles)
    ? user.roles.filter((item): item is string => typeof item === 'string')
    : [displayValue(user.role)];
  if (!user.identity_id)
    return `Требует сопоставления · ${roles.map(humanRole).join(' · ')}`;
  if (roles.includes('teacher')) {
    const assignments = Array.isArray(user.teacher_assignments)
      ? user.teacher_assignments
      : [];
    const subjects = Array.from(
      new Set(
        assignments
          .map((item) => displayValue((item as Record<string, unknown>).subject))
          .filter(Boolean),
      ),
    );
    return subjects.length
      ? `${roles.map(humanRole).join(' · ')} · ${subjects.join(', ')}`
      : `${roles.map(humanRole).join(' · ')} · назначения не указаны`;
  }
  return `${roles.map(humanRole).join(' · ')} · ${displayValue(user.class_name, 'класс не указан')}`;
};

const humanAuditAction = (action: string) =>
  ({
    'identity.confirmed': 'Подтверждена школьная личность',
    'identity_claim.approved': 'Подтверждён доступ человека',
    'identity_claim.rejected': 'Отклонена заявка на доступ',
    'membership_override.created': 'Изменён состав группы',
    'teacher_assignment.updated': 'Изменено преподавание в группе',
    'homeroom_assignment.updated': 'Изменено классное руководство',
    'information.created': 'Опубликовано сообщение',
    'student_preview.started': 'Открыт просмотр ученика',
    'student_preview.ended': 'Завершён просмотр ученика',
  })[action] || 'Обновлены данные My Island';
const scopeMarker = (lesson: Lesson) =>
  lesson.exam_track ||
  (lesson.subject_subgroup ? `группа ${lesson.subject_subgroup}` : '');

function LoadingState({ label }: { label: string }) {
  return (
    <div className="loading-state">
      <LoaderCircle className="spin" size={23} />
      <span>{label}</span>
    </div>
  );
}
function EmptyState({
  icon: Icon,
  title,
  detail,
}: {
  icon: typeof FileText;
  title: string;
  detail: string;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon size={20} />
      </span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}
function LessonRow({ lesson }: { lesson: Lesson }) {
  const independentPractice = lesson.activity_format === 'independent_practice';
  return (
    <div className="lesson-card">
      <time>{formatLessonTime(lesson.start_time) || 'Время не указано'}</time>
      <div className="lesson-card__body">
        <strong>{lesson.subject}</strong>
        <span>
          {independentPractice
            ? [
                lesson.audience,
                lesson.room,
                lesson.responsible_teacher
                  ? `Ответственный за задание: ${lesson.responsible_teacher}`
                  : 'Самостоятельная практика',
                lesson.physical_presence === false ? 'Вы не ведёте это занятие' : '',
              ].filter(Boolean).join(' · ')
            : [lesson.audience, lesson.room, lesson.teacher]
            .filter(Boolean)
            .join(' · ') || 'Детали уточняются'}
        </span>
        {scopeMarker(lesson) && <small>{scopeMarker(lesson)}</small>}
      </div>
      {independentPractice ? <span className="soft-badge">Самостоятельная практика</span> : lesson.lesson_type && lesson.lesson_type !== 'lesson' && (
        <span className="soft-badge">Особое</span>
      )}
    </div>
  );
}
function PanelShell({
  title,
  backLabel,
  onClose,
  children,
}: {
  title: string;
  backLabel: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <dialog open className="panel-layer" aria-label={title}>
      <button className="panel-scrim" onClick={onClose} aria-label="Закрыть" />
      <aside className="detail-panel">
        <div className="panel-toolbar">
          <button className="back-button" onClick={onClose}>
            <ChevronLeft size={18} /> {backLabel}
          </button>
          <button
            className="panel-close"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </aside>
    </dialog>
  );
}
function Topbar({
  name,
  onProfile,
  onToday,
  right,
}: {
  name: string;
  onProfile: () => void;
  onToday?: () => void;
  right?: React.ReactNode;
}) {
  return (
    <header className="scene-topbar">
      <div className="topbar-actions">
        {right}
        {onToday ? <button type="button" className="today-open-button" onClick={onToday}><CalendarDays size={16} />Сегодня</button> : null}
        <button className="profile-button" aria-label="Открыть профиль" onClick={onProfile}>
          <span className="profile-avatar">{name.slice(0, 1)}</span>
          <span className="profile-name">{name.split(' ')[0]}</span>
          <ChevronDown size={15} />
        </button>
      </div>
    </header>
  );
}

function scheduleStartingDay(days: StudentScheduleDay[]) {
  const today = isoDate(new Date());
  const relevant = days
    .map((day) => day.date)
    .filter((day) => day >= today)
    .sort();
  return relevant[0] || days.map((day) => day.date).sort()[0] || today;
}

function formatStudentLessonCount(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? 'занятий' : last === 1 ? 'занятие' : last >= 2 && last <= 4 ? 'занятия' : 'занятий';
  return `${count} ${noun}`;
}

function scheduleDetailKey(item: StudentScheduleItem, index: number) { return `lesson:${item.date}:${item.slot.start}:${item.state}:${index}`; }

function ScheduleItemRow({ item, index, onOpen }: { item: StudentScheduleItem; index: number; onOpen?: (key: string) => void }) {
  if (item.state === 'special_event') {
    const content = <><span className="student-schedule-event__mark" aria-hidden="true">✦</span><div><small>Событие</small><strong>{item.special_event?.title}</strong><span>{formatLessonTimeRange(item.slot.start, item.slot.end)} {item.room ? `· ${normalizeRoomDisplay(item.room)}` : ''}</span></div></>;
    return onOpen ? <button type="button" className="student-schedule-event student-schedule-lesson--button" onClick={() => onOpen(scheduleDetailKey(item, index))} aria-label={`Подробности события: ${item.special_event?.title}`}>{content}</button> : <article className="student-schedule-event">{content}</article>;
  }
  if (item.state === 'conflict') {
    const title = item.conflict?.alternatives.map((alternative) => alternative.title).join(' / ') || 'Расписание уточняется';
    const content = <><span className="student-schedule-conflict__mark" aria-hidden="true">?</span><div><small>Уточняется</small><strong>{title}</strong><span>{formatLessonTimeRange(item.slot.start, item.slot.end)}</span></div></>;
    return onOpen ? <button type="button" className="student-schedule-conflict student-schedule-lesson--button" onClick={() => onOpen(scheduleDetailKey(item, index))} aria-label={`Подробности занятия: ${title}`}>{content}</button> : <article className="student-schedule-conflict">{content}</article>;
  }
  if (item.state !== 'lesson') return null;
  const context = Array.isArray(item.group_context) ? item.group_context.join(' · ') : item.group_context;
  const independentPractice = item.activity_format === 'independent_practice';
  const row = <><time>{formatLessonTimeRange(item.slot.start, item.slot.end)}</time><div><strong>{item.canonical_title || 'Занятие'}</strong><span>{independentPractice ? [item.room ? normalizeRoomDisplay(item.room) : '', item.responsible_teacher ? `Ответственный за задание: ${item.responsible_teacher}` : 'Самостоятельная практика'].filter(Boolean).join(' · ') : [item.room ? normalizeRoomDisplay(item.room) : '', item.teacher || context].filter(Boolean).join(' · ') || 'Детали уточняются'}</span>{independentPractice ? <small>Самостоятельная практика</small> : null}</div></>;
  return onOpen ? <button type="button" className="student-schedule-lesson student-schedule-lesson--button" aria-label={`Подробности урока: ${item.canonical_title || 'Занятие'}`} onClick={() => onOpen(scheduleDetailKey(item, index))}>{row}</button> : <article className="student-schedule-lesson">{row}</article>;
}

function StudentSchedulePanel({ projection, initialDay, navigationLevel = 'expanded', detailKey, onOpenLesson, onSaveScroll, onBack, scheduleState, initialScrollTop = 0 }: { projection: StudentScheduleProjection; initialDay?: string; navigationLevel?: 'peek' | 'expanded' | 'detail'; detailKey?: string | null; onOpenLesson?: (key: string, scrollTop: number) => void; onSaveScroll?: (scrollTop: number) => void; onBack?: () => void; scheduleState?: string; initialScrollTop?: number }) {
  const initial = initialDay || scheduleStartingDay(projection.days);
  const [selectedDay, setSelectedDay] = useState(initial);
  const [scrollElement, setScrollElement] = useState<HTMLElement | null>(null);
  const attachScroll = useCallback((element: HTMLDivElement | null) => setScrollElement(element?.closest<HTMLElement>('.sheet-full') || null), []);
  useEffect(() => { if (scrollElement && initialScrollTop > 0) scrollElement.scrollTo({ top: initialScrollTop }); }, [scrollElement, initialScrollTop]);
  const monday = weekStart(new Date(`${selectedDay}T12:00:00`));
  const days = Array.from({ length: 7 }, (_, index) =>
    isoDate(addDays(monday, index)),
  );
  const day = projection.days.find((item) => item.date === selectedDay);
  const visibleItems = day?.items.filter((item) => item.state !== 'no_lesson' || item.no_lesson?.reason === 'break') || [];
  const hasLessons = day?.items.some((item) => item.state === 'lesson' || item.state === 'conflict' || item.state === 'special_event');
  const unavailable = /unavailable|not_available|not_published|not_materialized|missing|not_configured|error|blocked|disabled/.test(scheduleState || '');
  const emptyTitle = ['not_published', 'canonical_not_materialized'].includes(scheduleState || '') ? 'Неделя не опубликована' : unavailable ? 'Расписание недоступно' : 'Занятий нет';
  const emptyDetail = ['not_published', 'canonical_not_materialized'].includes(scheduleState || '') ? 'Для этой недели школа ещё не опубликовала расписание.' : unavailable ? 'Обновите расписание позже или проверьте выбранную неделю.' : 'Учебный день уже закончился или занятий не запланировано.';
  const selectedDetail = projection.days.flatMap(item => item.items.map((entry, index) => ({ entry, index }))).find(({ entry, index }) => scheduleDetailKey(entry, index) === detailKey)?.entry;
  if (navigationLevel === 'detail' && selectedDetail) {
    const context = Array.isArray(selectedDetail.group_context) ? selectedDetail.group_context.join(' · ') : selectedDetail.group_context;
    const title = selectedDetail.state === 'special_event' ? selectedDetail.special_event?.title || 'Событие' : selectedDetail.state === 'conflict' ? selectedDetail.conflict?.alternatives.map(item => item.title).join(' / ') || 'Занятие уточняется' : selectedDetail.canonical_title || 'Занятие';
  return <div className="section-panel student-lesson-detail"><button type="button" className="student-journal-back" aria-label="Назад к расписанию" onClick={onBack}><ChevronLeft size={23} aria-hidden="true" /></button><p className="eyebrow">Подробности занятия</p><h2>{title}</h2><dl><div><dt>Дата</dt><dd>{formatDate(selectedDetail.date, true)}</dd></div><div><dt>Время</dt><dd>{formatLessonTimeRange(selectedDetail.slot.start, selectedDetail.slot.end)}</dd></div>{selectedDetail.activity_format === 'independent_practice' ? <div><dt>Формат</dt><dd>Самостоятельная практика</dd></div> : null}{selectedDetail.activity_format === 'independent_practice' && selectedDetail.responsible_teacher ? <div><dt>Ответственный за задание</dt><dd>{selectedDetail.responsible_teacher}</dd></div> : selectedDetail.teacher ? <div><dt>Преподаватель</dt><dd>{selectedDetail.teacher}</dd></div> : null}{selectedDetail.room ? <div><dt>Кабинет</dt><dd>{normalizeRoomDisplay(selectedDetail.room)}</dd></div> : null}{context ? <div><dt>Группа</dt><dd>{context}</dd></div> : null}{selectedDetail.special_event?.metadata ? <div><dt>Сведения</dt><dd>{Object.values(selectedDetail.special_event.metadata).filter(value => typeof value === 'string' || typeof value === 'number').join(' · ')}</dd></div> : null}</dl></div>;
  }
  return (
    <div className="section-panel" ref={attachScroll}>
      <div className="panel-intro">
        <span className="panel-icon">
          <CalendarDays size={21} />
        </span>
        <div>
          <p className="eyebrow">Неделя</p>
          <h2>Расписание</h2>
          <p>Ваш день — коротко и без лишних пустых слотов.</p>
        </div>
      </div>
      <div className="day-picker">
        {days.map((day) => {
          const lessonCount = projection.days.find(item => item.date === day)?.items.filter(item => ['lesson', 'conflict', 'special_event'].includes(item.state)).length || 0;
          const isToday = day === isoDate(new Date());
          return (
          <button
            key={day}
            className={[day === selectedDay ? 'is-active' : '', isToday ? 'is-today' : ''].filter(Boolean).join(' ')}
            aria-pressed={day === selectedDay}
            aria-label={`${new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date(`${day}T12:00:00`))}${isToday ? ', сегодня' : ''}, ${formatStudentLessonCount(lessonCount)}`}
            onClick={() => setSelectedDay(day)}
          >
            <small>
              {new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(
                new Date(`${day}T12:00:00`),
              )}
            </small>
            <strong>{day.slice(8)}</strong>
            <em>{lessonCount ? lessonCount : '—'}</em>
          </button>
        );})}
      </div>
      <div className="panel-date">
        <strong>{formatDate(selectedDay, true)}</strong>
        <span>{hasLessons ? formatStudentLessonCount(day?.items.filter((item) => ['lesson', 'conflict', 'special_event'].includes(item.state)).length || 0) : day?.mode === 'sdep' || day?.special?.kind === 'sdep' ? 'Самостоятельная работа' : day?.mode === 'online' ? 'Онлайн' : [0, 6].includes(new Date(`${selectedDay}T12:00:00`).getDay()) ? 'Выходной' : 'Нет занятий'}</span>
      </div>
      {day?.mode === 'online' ? <div className="campus-day-mode online"><strong>Онлайн</strong><span>Занятия проходят дистанционно.</span></div> : null}
      {day?.mode === 'sdep' || day?.special?.kind === 'sdep' ? <div className="student-schedule-sdep"><strong>SDEP</strong><span>Самостоятельная работа по расписанию.</span></div> : null}
      {hasLessons ? (
        <div className="student-schedule-stack">
          {visibleItems.map((item) => { const index = day?.items.indexOf(item) ?? 0; return item.state === 'no_lesson' ? <div className="student-schedule-gap" key={`${item.date}-${item.slot.start}-${index}`}><span />Перерыв</div> : <ScheduleItemRow item={item} index={index} onOpen={key => { const scrollTop = document.querySelector<HTMLElement>('.sheet-full')?.scrollTop || 0; onSaveScroll?.(scrollTop); onOpenLesson?.(key, scrollTop); }} key={`${item.date}-${item.slot.start}-${item.state}-${index}`} />;})}
        </div>
      ) : unavailable ? <EmptyState icon={CalendarDays} title={emptyTitle} detail={emptyDetail} /> : (
        <EmptyState
          icon={CalendarDays}
          title={emptyTitle}
          detail={emptyDetail}
        />
      )}
    </div>
  );
}

function SchedulePanel({ schedule, dayBlocks = [], dayModes = [], initialDay }: { schedule: Lesson[]; dayBlocks?: StudentDayBlock[]; dayModes?: Array<{ date: string; mode: 'online' | 'sdep'; class_name?: string }>; initialDay?: string }) {
  const initial = initialDay || schedule.map((lesson) => lesson.lesson_date).sort()[0] || dayModes.map((item) => item.date).sort()[0] || dayBlocks.map((block) => block.lesson_date).sort()[0] || isoDate(new Date());
  const [selectedDay, setSelectedDay] = useState(initial);
  const monday = weekStart(new Date(`${selectedDay}T12:00:00`));
  const days = Array.from({ length: 7 }, (_, index) => isoDate(addDays(monday, index)));
  const entries = schedule.filter((lesson) => lesson.lesson_date === selectedDay).sort((a, b) => a.start_time.localeCompare(b.start_time));
  const modes = [...new Map(dayModes.filter((item) => item.date === selectedDay).map((item) => [`${item.mode}:${item.class_name || ''}`, item])).values()];
  return <div className="section-panel"><div className="panel-intro"><span className="panel-icon"><CalendarDays size={21} /></span><div><p className="eyebrow">Неделя</p><h2>Расписание</h2><p>Занятия ваших групп.</p></div></div><div className="day-picker">{days.map((day) => <button key={day} className={day === selectedDay ? 'is-active' : ''} onClick={() => setSelectedDay(day)}><small>{new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(new Date(`${day}T12:00:00`))}</small><strong>{day.slice(8)}</strong></button>)}</div><div className="panel-date"><strong>{formatDate(selectedDay, true)}</strong><span>{entries.length} занятий</span></div>{modes.map((item) => <div key={`${item.mode}:${item.class_name || ''}`} className={`campus-day-mode ${item.mode}`}><strong>{item.mode === 'sdep' ? 'SDEP' : 'Онлайн'}</strong><span>{item.mode === 'sdep' ? 'Самостоятельная работа по расписанию' : 'Занятия проходят дистанционно'}{item.class_name ? ` · ${item.class_name}` : ''}</span></div>)}{dayBlocks.some((block) => block.lesson_date === selectedDay) && !modes.some((item) => item.mode === 'sdep') ? <div className="sdep-card"><strong>SDEP</strong><span>Самостоятельная работа весь учебный день.</span></div> : null}{entries.length ? <div className="lesson-stack">{entries.map((lesson) => <LessonRow key={`${lesson.lesson_date}-${lesson.start_time}-${lesson.subject}`} lesson={lesson} />)}</div> : !modes.length && !dayBlocks.some((block) => block.lesson_date === selectedDay) ? <EmptyState icon={CalendarDays} title="Занятий нет" detail="Для этого дня нет записей." /> : null}</div>;
}

function ScheduleFocusPeek({ projection, referenceDay, scheduleState }: { projection: StudentScheduleProjection; referenceDay?: string; scheduleState?: string }) {
  const selected = referenceDay || isoDate(new Date());
  const today = projection.days.find((day) => day.date === selected);
  const items = today?.items.filter((item) => item.state !== 'no_lesson').sort((a, b) => a.slot.start.localeCompare(b.slot.start)) || [];
  const now = new Date();
  const minutes = selected === isoDate(now) ? now.getHours() * 60 + now.getMinutes() : -1;
  const next = items.find((item) => {
    const formatted = formatLessonTime(item.slot.start);
    if (!formatted) return false;
    const [hours, mins] = formatted.split(':').map(Number);
    return hours * 60 + mins >= minutes;
  });
  const current = items.find((item) => {
    const startTime = formatLessonTime(item.slot.start);
    const endTime = formatLessonTime(item.slot.end) || startTime;
    if (!startTime || !endTime) return false;
    const [hours, mins] = startTime.split(':').map(Number);
    const [endHours, endMins] = endTime.split(':').map(Number);
    return hours * 60 + mins <= minutes && endHours * 60 + endMins > minutes;
  });
  const anchor = current || next;
  const unavailable = /unavailable|not_available|not_published|not_materialized|missing|not_configured|error|blocked|disabled/.test(scheduleState || '');
  const status = ['not_published', 'canonical_not_materialized'].includes(scheduleState || '') ? 'Неделя не опубликована' : unavailable ? 'Расписание недоступно' : 'Учебный день закончен';
  return <div className="schedule-focus-peek"><div className="schedule-focus-peek__lead"><span className="schedule-focus-peek__icon"><CalendarDays size={18} /></span><div><small>{today?.special?.kind === 'sdep' ? 'Сегодня' : current ? 'Сейчас' : next ? 'Ближайшее' : 'Сегодня'}</small><strong>{today?.special?.kind === 'sdep' ? 'SDEP' : anchor?.state === 'special_event' ? anchor.special_event?.title : anchor?.state === 'conflict' ? anchor.conflict?.alternatives.map((item) => item.title).join(' / ') : anchor?.canonical_title || status}</strong><span>{today?.special?.kind === 'sdep' ? 'Самостоятельная работа весь день' : anchor ? `${formatLessonTimeRange(anchor.slot.start, anchor.slot.end)}${anchor.activity_format === 'independent_practice' ? ' · Самостоятельная практика' : anchor.room ? ` · ${normalizeRoomDisplay(anchor.room)}` : ''}` : unavailable ? 'Проверьте выбранную неделю' : 'На сегодня всё'}</span></div></div></div>;
}
function HomeworkPanel({ homework }: { homework: Homework[] }) {
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon panel-icon--coral">
          <BookOpen size={21} />
        </span>
        <div>
          <p className="eyebrow">Classroom</p>
          <h2>Домашка</h2>
          <p>Карточки ведут в оригинальные задания.</p>
        </div>
      </div>
      {homework.length ? (
        <div className="homework-stack">
          {homework.map((item) => (
            <article
              className="homework-card"
              key={item.external_coursework_id}
            >
              <span className="course-label">
                {item.course_title || 'Classroom'}
              </span>
              <h3>{item.title}</h3>
              {item.description && <p>{item.description}</p>}
              <div className="homework-card__bottom">
                <span>
                  <Clock3 size={15} /> {formatDue(item.due_at)}
                </span>
                {item.alternate_link && (
                  <a
                    href={item.alternate_link}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть <ExternalLink size={14} />
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={BookOpen}
          title="Заданий нет"
          detail="Новые задания появятся после фоновой синхронизации."
        />
      )}
    </div>
  );
}
function InfoPanel({
  items,
  staff = false,
}: {
  items: Information[];
  staff?: boolean;
}) {
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon panel-icon--gold">
          <Megaphone size={21} />
        </span>
        <div>
          <p className="eyebrow">
            {staff ? 'Для сотрудников' : 'Школьная информация'}
          </p>
          <h2>Информация</h2>
          <p>Только сообщения, подходящие вашей аудитории.</p>
        </div>
      </div>
      {items.length ? (
        <div className="announcement-stack">
          {items.map((item, index) => (
            <article
              className="announcement-card"
              key={`${item.id || item.title}-${index}`}
            >
              {item.pinned ? <Pin size={17} /> : <Megaphone size={17} />}
              <div>
                <h3>{item.title}</h3>
                <p>{item.body || item.content}</p>
                <small>
                  {[
                    item.author_name,
                    item.expires_at && `до ${formatDue(item.expires_at)}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </small>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Megaphone}
          title="Пока тихо"
          detail="Актуальных сообщений для вашей аудитории нет."
        />
      )}
    </div>
  );
}
function ProfilePanel({
  profile,
  roleLabel,
}: {
  profile?: Profile;
  roleLabel: string;
}) {
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon panel-icon--gold">
          <UserRound size={21} />
        </span>
        <div>
          <p className="eyebrow">Профиль и доступ</p>
          <h2>{profile?.user.display_name || 'Преподаватель'}</h2>
          <p>{profile?.user.class_name || roleLabel}</p>
        </div>
      </div>
      <div className="verified-card">
        <ShieldCheck size={19} />
        <div>
          <strong>{activeRolePreviewId ? 'Школьная личность найдена' : 'Профиль подтверждён'}</strong>
          <span>Вам доступны разделы и группы, назначенные школой.</span>
        </div>
        <Check size={18} />
      </div>
      {profile && (
        <div className="profile-section">
          <div className="subsection-title">
            <Users size={16} /> Мои группы
          </div>
          {profile.groups.map((group) => (
            <div className="group-card" key={`${group.id}-${group.name}`}>
              <div>
                <strong>{group.display_name || group.name}</strong>
                <span>
                  {[
                    group.subject,
                    group.subject_subgroup &&
                      `подгруппа ${group.subject_subgroup}`,
                  ]
                    .filter(Boolean)
                    .join(' · ') || 'Школьная группа'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
function GradesPanel({ api, ...journalProps }: { api: RoleJournalsApi; selectedKey?: string | null; onOpenDetail?: (key: string, scrollTop: number) => void; onBackFromDetail?: () => void; onSaveScroll?: (scrollTop: number) => void; initialScrollTop?: number }) {
  return <div className="section-panel"><StudentRoleJournals api={api} {...journalProps} /></div>;
}

type SceneHotspot = {
  id: string;
  label: string;
  x: number;
  y: number;
  camera?: { x: number; y: number; scale: number };
  focusAsset?: string;
  icon: IslandIconName;
};

function SpatialSheet({
  title,
  kicker,
  icon,
  peek,
  full,
  className = '',
  controlledStage,
  onExpand,
  onCollapse,
}: {
  title: string;
  kicker: string;
  icon: IslandIconName;
  peek: React.ReactNode;
  full: React.ReactNode;
  className?: string;
  controlledStage?: 'peek' | 'expanded' | 'detail';
  onExpand?: () => void;
  onCollapse?: () => void;
}) {
  const [localStage, setLocalStage] = useState<'peek' | 'expanded'>('peek');
  const stage = controlledStage === 'detail' ? 'expanded' : controlledStage || localStage;
  const [drag, setDrag] = useState(0);
  const startY = useRef<number | null>(null);
  const onPointerDown = (event: React.PointerEvent<HTMLElement>) => {
    startY.current = event.clientY;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    if (startY.current === null) return;
    const delta = event.clientY - startY.current;
    setDrag(Math.max(-320, Math.min(260, delta)));
  };
  const onPointerUp = (event: React.PointerEvent<HTMLElement>) => {
    if (startY.current === null) return;
    const delta = event.clientY - startY.current;
    if (delta < -52) expand();
    else if (delta > 52) collapse();
    setDrag(0);
    startY.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  const expand = () => {
    if (stage === 'expanded') return;
    if (controlledStage) { onExpand?.(); return; }
    setLocalStage('expanded');
  };
  const collapse = () => {
    if (controlledStage) { onCollapse?.(); return; }
    setLocalStage('peek');
  };
  useEffect(() => {
    const requestExpand = () => {
      if (stage === 'expanded') return;
      if (controlledStage) { onExpand?.(); return; }
      setLocalStage('expanded');
    };
    document.addEventListener('my-island:expand-sheet', requestExpand);
    return () => {
      document.removeEventListener('my-island:expand-sheet', requestExpand);
    };
  }, [stage, controlledStage, onExpand, onCollapse]);
  const offset = stage === 'expanded' ? '0px' : 'calc(100% - 182px)';
  const sheetStyle = {
    '--sheet-offset': `calc(${offset} + ${drag}px)`,
  } as React.CSSProperties;
  return (
    <section
      className={`spatial-sheet ${stage === 'expanded' ? 'is-full' : 'is-partial'} ${className}`}
      style={sheetStyle}
    >
      <div
        className="sheet-drag-region"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <span className="sheet-grabber" aria-hidden="true" />
        <button
          className="sheet-heading"
          type="button"
          aria-label={stage === 'expanded' ? 'Свернуть раздел' : 'Развернуть раздел'}
          aria-expanded={stage === 'expanded'}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => { if (stage === 'expanded') collapse(); else expand(); }}
        >
          <IslandMark name={icon} size={28} />
          <span className="sheet-heading__copy">
            <span className="eyebrow">{kicker}</span>
            <strong>{title}</strong>
          </span>
          <ChevronDown className="sheet-heading__chevron" size={19} />
        </button>
      </div>
      <div className="sheet-peek">{peek}</div>
      <div className="sheet-full">{full}</div>
    </section>
  );
}

function ScheduleNotice({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return <aside className="campus-schedule-notice" aria-label="Предупреждение о расписании"><span>{message}</span>{onDismiss ? <button type="button" aria-label="Закрыть предупреждение о расписании" onClick={onDismiss}><X size={16} /></button> : null}</aside>;
}

function TodaySheet({
  lessons,
  dayBlock,
  homework = [],
  information = [],
  teacher = false,
  scheduleWarning = '',
  referenceDay,
  onOpen,
  onDismissWarning,
  navigationLevel,
  onExpand,
  onCollapse,
  gradesPreview,
  scheduleState,
}: {
  lessons: Lesson[];
  dayBlock?: StudentDayBlock | null;
  homework?: Homework[];
  information?: Information[];
  teacher?: boolean;
  scheduleWarning?: string;
  referenceDay?: string;
  onOpen: (view: string) => void;
  onDismissWarning?: () => void;
  navigationLevel?: 'peek' | 'expanded' | 'detail';
  onExpand?: () => void;
  onCollapse?: () => void;
  gradesPreview?: React.ReactNode;
  scheduleState?: string;
}) {
  const sorted = [...lessons].sort((a, b) =>
    a.start_time.localeCompare(b.start_time),
  );
  const next =
    sorted.find((lesson) => {
      if (referenceDay && referenceDay !== dateISO()) return true;
      const formatted = formatLessonTime(lesson.start_time);
      if (!formatted) return false;
      const [hours, minutes] = formatted.split(':').map(Number);
      return (
        hours * 60 + minutes >=
        new Date().getHours() * 60 + new Date().getMinutes()
      );
    }) || sorted[0];
  const task = [...homework].sort((a, b) =>
    (a.due_at || '9999').localeCompare(b.due_at || '9999'),
  )[0];
  const scheduleUnavailable = /unavailable|not_available|not_published|not_materialized|missing|not_configured|error|blocked|disabled/.test(scheduleState || '');
  const unavailableLabel = ['not_published', 'canonical_not_materialized'].includes(scheduleState || '') ? 'Неделя не опубликована' : 'Расписание недоступно';
  const peek = (
    <div className={`today-peek-row ${!next ? 'is-quiet' : ''}`}>
      <button
        className="today-card today-card--primary"
        onClick={() => onOpen('schedule')}
      >
        <IslandIcon name="schedule" size={20} />
        <span>
          <small>
            {scheduleWarning || scheduleUnavailable ? 'Расписание' : dayBlock
              ? 'Самостоятельная работа'
              : next
              ? `${formatLessonTime(next.start_time) || 'Время не указано'} · ${teacher ? next.audience || 'группа' : 'ближайший урок'}`
              : 'Учебный день'}
          </small>
          <strong>{scheduleWarning ? 'Неделя недоступна' : scheduleUnavailable ? unavailableLabel : dayBlock ? 'SDEP' : next?.subject || 'Сегодня без занятий'}</strong>
          <em>
            {scheduleWarning ? 'Откройте расписание для подробностей' : scheduleUnavailable ? 'Проверьте выбранную неделю' : dayBlock
              ? dayBlock.description
              : next
              ? [next.room, scopeMarker(next)].filter(Boolean).join(' · ') ||
                'Детали уточняются'
              : 'Можно заглянуть в расписание'}
          </em>
        </span>
      </button>
      {(next || task) && (
        <button
          className="today-card today-card--secondary"
          onClick={() => onOpen(teacher ? 'groups' : 'homework')}
        >
          <IslandIcon name={teacher ? 'groups' : 'homework'} size={19} />
          <span>
            <small>{teacher ? 'Рабочий контур' : 'На контроле'}</small>
            <strong>
              {teacher ? `${sorted.length} занятий` : task?.title || 'Задание'}
            </strong>
            <em>{teacher ? 'Мои группы' : formatDue(task?.due_at)}</em>
          </span>
        </button>
      )}
    </div>
  );
  const full = (
    <div className="today-expanded">
      <div className="today-expanded-date"><strong>{formatDate(referenceDay || isoDate(new Date()), true)}</strong><span>Учебный день</span></div>
      {scheduleWarning ? <ScheduleNotice message={scheduleWarning} onDismiss={onDismissWarning} /> : scheduleUnavailable ? (
        <div className="today-empty-line"><IslandIcon name="schedule" size={18} /><span>{unavailableLabel}</span></div>
      ) : dayBlock ? (
        <div className="today-sdep-line">
          <IslandIcon name="schedule" size={18} />
          <span><strong>SDEP</strong><small>Самостоятельная работа весь учебный день</small></span>
        </div>
      ) : sorted.length ? (
        sorted
          .slice(0, 5)
          .map((lesson) => (
            <LessonRow
              key={`${lesson.start_time}-${lesson.subject}`}
              lesson={lesson}
            />
          ))
      ) : (
        <div className="today-empty-line">
          <IslandIcon name="schedule" size={18} />
          <span>Сегодня без занятий</span>
        </div>
      )}
      {information[0] && (
        <button className="today-notice" onClick={() => onOpen('information')}>
          <IslandIcon name="information" size={17} />
          <span>
            <strong>{information[0].title}</strong>
            <small>Открыть сообщение</small>
          </span>
          <ChevronRight size={16} />
        </button>
      )}
      {gradesPreview ? <section className="today-recent-grades"><header><strong>Последние оценки</strong><button type="button" onClick={() => onOpen('grades')}>Все оценки <ChevronRight size={16} /></button></header>{gradesPreview}</section> : null}
    </div>
  );
  return (
    <SpatialSheet
      title="Сегодня"
      kicker={formatDate(referenceDay || isoDate(new Date()), true)}
      icon="schedule"
      peek={peek}
      full={full}
      className="today-sheet"
      controlledStage={navigationLevel}
      onExpand={onExpand}
      onCollapse={onCollapse}
    />
  );
}

function ContextualSheet({
  title,
  icon,
  kicker,
  peek,
  children,
  navigationLevel,
  onExpand,
  onCollapse,
}: {
  title: string;
  icon: IslandIconName;
  kicker: string;
  peek: React.ReactNode;
  children: React.ReactNode;
  navigationLevel?: 'peek' | 'expanded' | 'detail';
  onExpand?: () => void;
  onCollapse?: () => void;
}) {
  return (
    <SpatialSheet
      title={title}
      kicker={kicker}
      icon={icon}
      className={`context-sheet${icon === 'grades' ? ' student-journal-sheet' : ''}`}
      peek={<div className="context-peek">{peek}</div>}
      full={children}
      controlledStage={navigationLevel}
      onExpand={onExpand}
      onCollapse={onCollapse}
    />
  );
}

function SheetSummary({
  icon,
  label,
  value,
  detail,
}: {
  icon: IslandIconName;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="sheet-summary">
      <IslandIcon name={icon} size={19} />
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
        {detail && <em>{detail}</em>}
      </span>
      <ChevronRight size={17} />
    </div>
  );
}

function SceneFrame({
  variant,
  locations,
  selectedId,
  onSelect,
  onBack,
  backLabel,
  showBack,
  sheet,
  children,
}: {
  variant: 'student' | 'teacher';
  locations: SceneHotspot[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onBack: () => void;
  backLabel?: string;
  showBack?: boolean;
  sheet?: React.ReactNode;
  children: React.ReactNode;
}) {
  const selected = locations.find((item) => item.id === selectedId);
  const { masterRef, closeupRef, opticsRef } = useSceneMotion(selected);
  return (
    <main className={`scene-app ${variant}-app ${selected ? 'has-focus' : ''}`}>
      <div className="scene-optics" ref={opticsRef} aria-hidden="true">
        <div className={`scene-canvas ${variant}-scene`} ref={masterRef} />
        <div className="scene-focus-canvas" ref={closeupRef} />
      </div>
      <div className="scene-shade" />
      {children}
      <div className={`scene-hotspots ${selected ? 'is-focused' : ''}`}>
        {locations.map((location) => (
          <button
            key={location.id}
            className={`scene-hotspot hotspot--${location.id} ${selectedId === location.id ? 'is-selected' : ''}`}
            style={{
              left: `${location.x}%`,
              top: `max(calc(var(--app-ui-top) + 22px), ${location.y}%)`,
            }}
            onClick={() => {
              if (
                !opticsRef.current?.dataset.motion ||
                opticsRef.current.dataset.motion === 'idle'
              )
                onSelect(location.id);
            }}
            aria-label={location.label}
          >
            <span>
              <IslandIcon name={location.icon} size={19} />
            </span>
            <strong>{location.label}</strong>
          </button>
        ))}
      </div>
      {(showBack ?? Boolean(selected)) && (
        <button
          className="scene-focus-back"
          onClick={() => {
            if (
              !opticsRef.current?.dataset.motion ||
              opticsRef.current.dataset.motion === 'idle'
            )
              onBack();
          }}
        >
          <ChevronLeft size={18} />{' '}
          {backLabel || (variant === 'student' ? 'К острову' : 'К кампусу')}
        </button>
      )}
      {sheet}
    </main>
  );
}

function studentNavigationFromUrl(identityKey: string): CampusNavigationState {
  if (typeof window === 'undefined') return initialCampusNavigation;
  const storedIdentity = window.history.state?.campusIdentity;
  if (typeof storedIdentity === 'string' && storedIdentity !== identityKey) return initialCampusNavigation;
  if (storedIdentity === identityKey && window.history.state?.campusNavigation) return campusNavigationFromHistory(window.history.state.campusNavigation);
  const screen = new URLSearchParams(window.location.search).get('screen') as CampusBuilding | null;
  return screen && ['today', 'schedule', 'homework', 'grades', 'information', 'profile'].includes(screen)
    ? { ...initialCampusNavigation, building: screen, level: 'peek', scopeId: `link:${screen}` }
    : initialCampusNavigation;
}

function useStudentCampusNavigation(identityKey: string, scheduleContext: string) {
  const [navigation, dispatch] = useReducer(campusNavigationReducer, identityKey, studentNavigationFromUrl);
  const previousIdentity = useRef(identityKey);
  const apply = useCallback((action: CampusNavigationAction, replace = false) => {
    let next = campusNavigationReducer(navigation, action);
    if (next === navigation) return;
    const url = new URL(window.location.href);
    if (next.building) url.searchParams.set('screen', next.building);
    else url.searchParams.delete('screen');
    let shouldReplace = replace;
    if (action.type === 'open-building') {
      const switchingFromBuilding = navigation.building !== null;
      next = { ...next, scopeId: `${Date.now()}-${Math.random().toString(36).slice(2)}`, historyDepth: switchingFromBuilding ? 0 : 1 };
      shouldReplace = switchingFromBuilding;
    } else if (action.type === 'reset') {
      next = { ...next, scopeId: `${Date.now()}-${Math.random().toString(36).slice(2)}`, historyDepth: 0 };
      shouldReplace = true;
    }
    const state = { ...window.history.state, campusIdentity: identityKey, campusNavigation: next };
    if (shouldReplace) window.history.replaceState(state, '', `${url.pathname}${url.search}${url.hash}`);
    else window.history.pushState(state, '', `${url.pathname}${url.search}${url.hash}`);
    dispatch({ type: 'restore', state: next });
  }, [identityKey, navigation]);
  useEffect(() => {
    const safe = campusNavigationForContext(navigation, scheduleContext);
    if (safe !== navigation) apply({ type: 'restore', state: safe }, true);
  }, [apply, navigation, scheduleContext]);
  useEffect(() => {
    if (previousIdentity.current === identityKey) return;
    previousIdentity.current = identityKey;
    apply({ type: 'reset' });
  }, [identityKey, apply]);
  useEffect(() => {
    const state = { ...window.history.state, campusIdentity: identityKey, campusNavigation: navigation };
    window.history.replaceState(state, '', window.location.href);
    const restore = () => {
      const sameIdentity = window.history.state?.campusIdentity === identityKey;
      const candidate = sameIdentity && window.history.state?.campusNavigation
        ? campusNavigationFromHistory(window.history.state.campusNavigation)
        : studentNavigationFromUrl(identityKey);
      const restored = candidate.scopeId !== navigation.scopeId
        ? { ...initialCampusNavigation, scopeId: navigation.scopeId }
        : campusNavigationForContext(candidate, scheduleContext);
      if (candidate.scopeId !== navigation.scopeId) {
        const url = new URL(window.location.href); url.searchParams.delete('screen');
        window.history.replaceState({ ...window.history.state, campusIdentity: identityKey, campusNavigation: restored }, '', `${url.pathname}${url.search}${url.hash}`);
      }
      dispatch({ type: 'restore', state: restored });
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [identityKey, navigation, scheduleContext]);
  const goBack = useCallback(() => {
    if (navigation.historyDepth > 0) window.history.back();
    else if (navigation.level === 'detail') apply({ type: 'close-detail' }, true);
    else apply({ type: 'close' }, true);
  }, [apply, navigation.historyDepth, navigation.level]);
  useTelegramBack(navigation.level !== 'closed', goBack);
  return { navigation, apply, goBack };
}

function StudentApp({
  data,
  identityKey,
  onReload,
  roleControl,
  day,
  onDayChange,
  scheduleWarning,
  scheduleWarningSource,
}: {
  data: ApiData;
  identityKey: string;
  onReload: () => void;
  roleControl?: React.ReactNode;
  day: string;
  onDayChange: (day: string) => void;
  scheduleWarning: string;
  scheduleWarningSource: string;
}) {
  const { navigation, apply, goBack } = useStudentCampusNavigation(identityKey, periodFor(day).start);
  const scheduleWeek = periodFor(day).start;
  const previousScheduleWeek = useRef(scheduleWeek);
  useEffect(() => {
    if (previousScheduleWeek.current === scheduleWeek) return;
    previousScheduleWeek.current = scheduleWeek;
    if (navigation.building === 'schedule' && navigation.level === 'detail') apply({ type: 'close-detail' }, true);
  }, [scheduleWeek, navigation.building, navigation.level, apply]);
  const warningKey = scheduleWarning ? `campus-schedule-warning:${identityKey}:${periodFor(day).start}:${scheduleWarningSource}:${scheduleWarning}` : '';
  const [dismissedWarning, setDismissedWarning] = useState('');
  const wasDismissed = Boolean(warningKey && (dismissedWarning === warningKey || (typeof window !== 'undefined' && window.sessionStorage.getItem(warningKey) === 'dismissed')));
  const visibleScheduleWarning = warningKey && !wasDismissed ? scheduleWarning : '';
  const dismissScheduleWarning = () => { if (!warningKey) return; window.sessionStorage.setItem(warningKey, 'dismissed'); setDismissedWarning(warningKey); };
  const open = (next: StudentView | 'today') => { if (next) apply({ type: 'open-building', building: next as CampusBuilding }); };
  const selectedId = navigation.building && !['profile', 'today'].includes(navigation.building) ? navigation.building : null;
  const name = data.profile.user.display_name;
  const studentLocations: SceneHotspot[] = islandLocations.map((location) => ({
    ...location,
    icon: location.id,
  }));
  const nextHomework = [...data.homework].sort((a, b) =>
    (a.due_at || '9999').localeCompare(b.due_at || '9999'),
  )[0];
  const latestInfo = data.today.announcements[0];
  const hasScheduleProjection = Array.isArray(data.scheduleProjection.days);
  const contextualPeek =
      selectedId === 'schedule' ? (
      visibleScheduleWarning ? <ScheduleNotice message={visibleScheduleWarning} onDismiss={dismissScheduleWarning} /> : hasScheduleProjection ? <ScheduleFocusPeek projection={data.scheduleProjection} referenceDay={day} scheduleState={data.scheduleState} /> : <SheetSummary icon="schedule" label="Расписание" value={data.schedule.length ? `${data.schedule.length} занятий` : ['not_published', 'canonical_not_materialized'].includes(data.scheduleState || '') ? 'Неделя не опубликована' : 'Ближайших занятий нет'} />
    ) : selectedId === 'homework' ? (
      <SheetSummary
        icon="homework"
        label={nextHomework ? formatDue(nextHomework.due_at) : 'Домашка'}
        value={nextHomework?.title || 'Новых заданий нет'}
        detail={nextHomework?.course_title || undefined}
      />
    ) : selectedId === 'information' ? (
      <SheetSummary
        icon="information"
        label="Последнее сообщение"
        value={latestInfo?.title || 'Новых сообщений нет'}
        detail={latestInfo?.author_name}
      />
    ) : selectedId === 'grades' ? (
      <div className="grades-peek-content"><StudentGradesPeek api={api} /><button type="button" className="grades-peek-action" onClick={() => document.dispatchEvent(new Event('my-island:expand-sheet'))}>Все оценки <ChevronRight size={16} aria-hidden="true" /></button></div>
    ) : (
      <SheetSummary
        icon="grades"
        label="Журнал"
        value="Работы и результаты" detail="Откройте школьные журналы"
      />
    );
  const contextual = selectedId ? (
    <ContextualSheet
      title={
        islandLocations.find((item) => item.id === selectedId)?.label ||
        'Остров'
      }
      icon={selectedId as IslandIconName}
      kicker="Место на острове"
      peek={contextualPeek}
      navigationLevel={navigation.level === 'closed' ? 'peek' : navigation.level}
      onExpand={() => apply({ type: 'expand' })}
      onCollapse={goBack}
    >
      {selectedId === 'schedule' ? <CampusPeriod day={day} onChange={onDayChange} /> : null}
      {selectedId === 'schedule' ? (
        <>{visibleScheduleWarning ? <ScheduleNotice message={visibleScheduleWarning} onDismiss={dismissScheduleWarning} /> : null}{hasScheduleProjection ? <StudentSchedulePanel key={day} projection={data.scheduleProjection} initialDay={day} navigationLevel={navigation.level === 'closed' ? 'peek' : navigation.level} detailKey={navigation.detailKey} onOpenLesson={(detailKey, scrollTop) => apply({ type: 'open-detail', detailKey, detailContext: periodFor(day).start, scrollTop })} onSaveScroll={scrollTop => apply({ type: 'save-scroll', scrollTop }, true)} onBack={goBack} scheduleState={data.scheduleState} initialScrollTop={navigation.scrollTop} /> : <SchedulePanel key={day} schedule={data.schedule} dayBlocks={data.scheduleDayBlocks} initialDay={day} />}</>
      ) : selectedId === 'homework' ? (
        <HomeworkPanel homework={data.homework} />
      ) : selectedId === 'information' ? (
        <InfoPanel items={data.today.announcements} />
      ) : (
        <GradesPanel api={api} selectedKey={navigation.detailKey} onOpenDetail={(key, scrollTop) => apply({ type: 'open-detail', detailKey: key, scrollTop })} onBackFromDetail={goBack} onSaveScroll={(scrollTop) => apply({ type: 'save-scroll', scrollTop }, true)} initialScrollTop={navigation.scrollTop} />
      )}
      <button className="panel-refresh" onClick={onReload}>
        <RefreshCw size={15} /> Обновить данные
      </button>
    </ContextualSheet>
  ) : undefined;
  return (
    <>
      <SceneFrame
        variant="student"
        locations={studentLocations}
        selectedId={selectedId}
        onSelect={(id) => open(id as StudentView)}
        onBack={goBack}
        backLabel={navigation.level === 'peek' ? 'К острову' : 'Назад'}
        showBack={Boolean(selectedId) || (navigation.building === 'today' && navigation.level !== 'closed')}
        sheet={
          navigation.building === 'today' && navigation.level !== 'closed' ? (
            <TodaySheet
              referenceDay={day}
              scheduleWarning={visibleScheduleWarning}
              lessons={data.today.schedule}
              dayBlock={data.today.day_block}
              homework={data.today.homework}
              information={data.today.announcements}
              onOpen={(next) => open(next as StudentView)}
              onDismissWarning={dismissScheduleWarning}
              navigationLevel={navigation.level}
              onExpand={() => apply({ type: 'expand' })}
              onCollapse={goBack}
              gradesPreview={<StudentGradesPeek api={api} />}
              scheduleState={data.scheduleState}
            />
          ) : contextual
        }
      >
        <Topbar name={name} onProfile={() => open('profile')} onToday={() => open('today')} right={roleControl} />
      </SceneFrame>
      {navigation.building === 'profile' && (
        <PanelShell
          title="Профиль"
          backLabel="Остров"
          onClose={goBack}
        >
          <ProfilePanel profile={data.profile} roleLabel="ученик" />
          <button className="panel-refresh" onClick={onReload}>
            <RefreshCw size={15} /> Обновить данные
          </button>
        </PanelShell>
      )}
    </>
  );
}

function TeacherGroups({
  groups,
  courses,
}: {
  groups: Group[];
  courses: Array<Record<string, unknown>>;
}) {
  const [selected, setSelected] = useState<Group | null>(null);
  const [detail, setDetail] = useState<TeacherGroupDetail | null>(null);
  const [tab, setTab] = useState<
    'overview' | 'students' | 'journal' | 'analytics'
  >('overview');
  const choose = async (group: Group) => {
    setSelected(group);
    setDetail(null);
    setTab('overview');
    try {
      setDetail(
        await api<TeacherGroupDetail>(`/api/teacher/groups/${group.id}`),
      );
    } catch {
      setDetail({
        students: [],
        grade_histories: [],
        assessments: [],
        analytics: {},
      });
    }
  };
  if (selected) {
    const average = displayValue(detail?.analytics.average, '—');
    return (
      <div className="section-panel">
        <button
          className="back-button inline-back"
          onClick={() => setSelected(null)}
        >
          <ChevronLeft size={17} /> Мои группы
        </button>
        <div className="panel-intro">
          <span className="panel-icon">
            <Users size={21} />
          </span>
          <div>
            <p className="eyebrow">{selected.subject || selected.group_type}</p>
            <h2>{selected.name}</h2>
            <p>
              {selected.student_count || detail?.students.length || 0} учеников
              · среднее {average}
            </p>
          </div>
        </div>
        <div className="subtabs">
          {(['overview', 'students', 'journal', 'analytics'] as const).map(
            (item) => (
              <button
                key={item}
                className={tab === item ? 'is-active' : ''}
                onClick={() => setTab(item)}
              >
                {
                  {
                    overview: 'Обзор',
                    students: 'Ученики',
                    journal: 'Журнал',
                    analytics: 'Аналитика',
                  }[item]
                }
              </button>
            ),
          )}
        </div>
        {!detail ? (
          <LoadingState label="Загружаем группу" />
        ) : tab === 'students' ? (
          <div className="people-list">
            {detail.students.map((student, index) => (
              <div
                className="person-row"
                key={displayValue(student.id, String(index))}
              >
                <span className="avatar-mini">
                  {displayValue(student.display_name, '?').slice(0, 1)}
                </span>
                <div>
                  <strong>{displayValue(student.display_name)}</strong>
                  <small>
                    {displayValue(student.class_name)} ·{' '}
                    {displayValue(student.source)}
                  </small>
                  {detail.grade_histories
                    .filter(
                      (history) =>
                        displayValue(history.name) ===
                        displayValue(student.display_name),
                    )
                    .map((history) => (
                      <small key={displayValue(history.name)}>
                        История: {displayValue(history.average, '—')} среднее ·{' '}
                        {Array.isArray(history.history)
                          ? history.history.length
                          : 0}{' '}
                        работ
                      </small>
                    ))}
                </div>
              </div>
            ))}
          </div>
        ) : tab === 'journal' ? (
          <div className="assessment-list">
            {detail.assessments.length ? (
              detail.assessments.map((assessment, index) => (
                <article
                  className="assessment-card"
                  key={displayValue(assessment.id, String(index))}
                >
                  <div>
                    <strong>{displayValue(assessment.title)}</strong>
                    <small>
                      {displayValue(assessment.date)} · вес{' '}
                      {displayValue(assessment.weight, '—')} · максимум{' '}
                      {displayValue(assessment.max_score, '—')}
                    </small>
                  </div>
                  <span className="count-badge">
                    {Array.isArray(assessment.results)
                      ? assessment.results.length
                      : 0}
                  </span>
                </article>
              ))
            ) : (
              <EmptyState
                icon={BarChart3}
                title="Журнал не сопоставлен"
                detail="Журнал для этой группы пока не подключён."
              />
            )}
          </div>
        ) : tab === 'analytics' ? (
          <>
            <div className="metric-grid">
              <div className="metric-card">
                <span>Среднее</span>
                <strong>{average}</strong>
              </div>
              <div className="metric-card">
                <span>Результатов</span>
                <strong>
                  {displayValue(detail.analytics.result_count, '0')}
                </strong>
              </div>
              <div className="metric-card">
                <span>Числовых</span>
                <strong>
                  {displayValue(detail.analytics.numeric_count, '0')}
                </strong>
              </div>
            </div>
            <div className="subsection-title">Распределение статусов</div>
            <div className="assessment-list">
              {Object.entries(
                (detail.analytics.status_distribution || {}) as Record<
                  string,
                  unknown
                >,
              ).map(([status, count]) => (
                <div className="context-link" key={status}>
                  <span>{status}</span>
                  <strong>{displayValue(count)}</strong>
                </div>
              ))}
              {!Object.keys(detail.analytics.status_distribution || {})
                .length && (
                <p className="sync-summary sync-summary--empty">
                  Статусов пока нет
                </p>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="metric-grid">
              <div className="metric-card">
                <span>Ученики</span>
                <strong>{detail.students.length}</strong>
              </div>
              <div className="metric-card">
                <span>Работы</span>
                <strong>{detail.assessments.length}</strong>
              </div>
              <div className="metric-card">
                <span>Среднее</span>
                <strong>{average}</strong>
              </div>
            </div>
            <div className="subsection-title">
              <BookOpen size={16} /> Classroom
            </div>
            {courses
              .filter(
                (course) =>
                  displayValue(course.group_id) === displayValue(selected.id),
              )
              .map((course, index) => (
                <div className="context-link" key={index}>
                  <span>{displayValue(course.title, 'Курс')}</span>
                  <ExternalLink size={15} />
                </div>
              ))}
          </>
        )}
      </div>
    );
  }
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon">
          <Users size={21} />
        </span>
        <div>
          <p className="eyebrow">Предметная работа</p>
          <h2>Мои группы</h2>
          <p>Назначенные группы и журналы доступны только в пределах школьных назначений.</p>
        </div>
      </div>
      <TeacherRoleJournals api={api} />
      {groups.length ? (
        <div className="group-grid">
          {groups.map((group) => (
            <button
              className="group-tile"
              key={group.id}
              onClick={() => void choose(group)}
            >
              <span className="group-tile__icon">
                <School size={20} />
              </span>
              <div>
                <strong>{group.display_name || group.name}</strong>
                <small>
                  {group.subject || humanGroupType(group.group_type)} ·{' '}
                  {group.student_count || 0} учеников
                </small>
              </div>
              <ChevronRight size={17} />
            </button>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Users}
          title="Нет назначенных групп"
          detail="Группы появятся после назначения школой."
        />
      )}
    </div>
  );
}
function TeacherApp({
  data,
  onLogout,
  onReload,
  onOpenAdmin,
  roleControl,
  day,
  onDayChange,
  scheduleWarning,
}: {
  data: TeacherData;
  onLogout: () => void;
  onReload: () => void;
  onOpenAdmin?: () => void;
  roleControl?: React.ReactNode;
  day: string;
  onDayChange: (day: string) => void;
  scheduleWarning: string;
}) {
  const [view, setView] = useCampusScreen<NonNullable<TeacherView>>(teacherScreens);
  const homeroom = data.groups.find((group) => group.is_homeroom);
  const teacherLocations: SceneHotspot[] = teacherCampusLocations
    .filter((location) => location.id !== 'homeroom' || Boolean(homeroom))
    .map((location) => ({ ...location, icon: location.id }));
  const selectedId = view && view !== 'profile' ? view : null;
  const nextTeacherLesson = [...data.schedule]
    .filter((lesson) => lesson.lesson_date >= day)
    .sort((a, b) =>
      `${a.lesson_date}${a.start_time}`.localeCompare(
        `${b.lesson_date}${b.start_time}`,
      ),
    )[0];
  const subjectGroups = data.groups.filter((group) => !group.is_homeroom);
  const teacherPeek =
    selectedId === 'schedule' ? (
      scheduleWarning ? <ScheduleNotice message={scheduleWarning} /> : <SheetSummary
        icon="schedule"
        label={
          nextTeacherLesson
            ? formatDate(nextTeacherLesson.lesson_date, true)
            : 'Расписание'
        }
        value={
          nextTeacherLesson
            ? `${formatLessonTime(nextTeacherLesson.start_time) || 'Время не указано'} · ${nextTeacherLesson.subject}${nextTeacherLesson.activity_format === 'independent_practice' ? ' · Самостоятельная практика' : ''}`
            : 'Ближайших занятий нет'
        }
        detail={nextTeacherLesson?.audience || undefined}
      />
    ) : selectedId === 'groups' ? (
      <SheetSummary
        icon="groups"
        label="Предметные группы"
        value={
          subjectGroups.length
            ? `${subjectGroups.length} групп`
            : 'Назначенных групп нет'
        }
        detail={subjectGroups[0]?.name}
      />
    ) : selectedId === 'homeroom' ? (
      <SheetSummary
        icon="homeroom"
        label="Классное руководство"
        value={homeroom?.name || 'Класс не назначен'}
        detail={
          homeroom?.student_count
            ? `${homeroom.student_count} учеников`
            : undefined
        }
      />
    ) : (
      <SheetSummary
        icon="information"
        label="Для сотрудников"
        value={data.information[0]?.title || 'Новых сообщений нет'}
        detail={data.information[0]?.author_name}
      />
    );
  const contextual = selectedId ? (
    <ContextualSheet
      title={
        teacherLocations.find((item) => item.id === selectedId)?.label ||
        'Кампус'
      }
      icon={selectedId}
      kicker="Рабочая зона кампуса"
      peek={teacherPeek}
    >
      {selectedId === 'schedule' ? <CampusPeriod day={day} onChange={onDayChange} /> : null}
      {selectedId === 'schedule' ? (
        scheduleWarning ? <ScheduleNotice message={scheduleWarning} /> : <SchedulePanel key={day} schedule={data.schedule} dayModes={data.dayModes} initialDay={day} />
      ) : selectedId === 'groups' ? (
        <TeacherGroups
          groups={data.groups.filter((group) => !group.is_homeroom)}
          courses={data.courses}
        />
      ) : selectedId === 'homeroom' ? (
        <TeacherGroups
          groups={homeroom ? [homeroom] : []}
          courses={data.courses}
        />
      ) : (
        <InfoPanel items={data.information} staff />
      )}
      <button className="panel-refresh" onClick={onReload}>
        <RefreshCw size={15} /> Обновить
      </button>
      <button className="panel-refresh" onClick={onLogout}>
        <LogOut size={15} /> Завершить сессию
      </button>
    </ContextualSheet>
  ) : undefined;
  return (
    <>
      <SceneFrame
        variant="teacher"
        locations={teacherLocations}
        selectedId={selectedId}
        onSelect={(id) => setView(id as TeacherView)}
        onBack={() => setView(null)}
        sheet={
          contextual || (
            <TodaySheet
              referenceDay={day}
              scheduleWarning={view === 'profile' ? '' : scheduleWarning}
              lessons={data.today}
              information={data.information}
              teacher
              onOpen={(next) => setView(next as TeacherView)}
            />
          )
        }
      >
        <Topbar
          name={data.profile?.user.display_name || "Учитель"}
          onProfile={() => setView('profile')}
          right={
            roleControl || (onOpenAdmin && (
              <button className="top-action" onClick={onOpenAdmin}>
                <ShieldCheck size={16} /> <span>Admin</span>
              </button>
            ))
          }
        />
      </SceneFrame>
      {view === 'profile' && (
        <PanelShell
          title="Профиль"
          backLabel="Кампус"
          onClose={() => setView(null)}
        >
          <ProfilePanel
            profile={data.profile || undefined}
            roleLabel={onOpenAdmin ? 'Учитель · Администратор' : 'Учитель'}
          />
          <button className="panel-refresh" onClick={onReload}>
            <RefreshCw size={15} /> Обновить
          </button>
          <button className="panel-refresh" onClick={onLogout}>
            <LogOut size={15} /> Завершить сессию
          </button>
        </PanelShell>
      )}
    </>
  );
}

const adminNav: Array<{
  id: AdminSection;
  label: string;
  icon: typeof Users;
  includes: AdminSection[];
}> = [
  {
    id: 'overview',
    label: 'Главная',
    icon: LayoutDashboard,
    includes: ['overview'],
  },
  { id: 'people', label: 'Люди', icon: Users, includes: ['people'] },
  { id: 'preview', label: 'Предпросмотр ученика', icon: UserRound, includes: ['preview'] },
  { id: 'structure', label: 'Школа', icon: Network, includes: ['structure'] },
  {
    id: 'integrations',
    label: 'Данные',
    icon: RefreshCw,
    includes: ['schedule', 'integrations'],
  },
  {
    id: 'diagnostics',
    label: 'Система',
    icon: Settings,
    includes: ['settings', 'diagnostics'],
  },
];

function AdminSubnav({
  items,
  section,
  onChange,
}: {
  items: Array<{ id: AdminSection; label: string }>;
  section: AdminSection;
  onChange: (next: AdminSection) => void;
}) {
  return (
    <div className="admin-subnav" aria-label="Подразделы">
      {items.map((item) => (
        <button
          key={item.id}
          className={section === item.id ? 'is-active' : ''}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
function SyncSummary({
  item,
  empty,
}: {
  item?: Record<string, unknown>;
  empty: string;
}) {
  if (!item) return <p className="sync-summary sync-summary--empty">{empty}</p>;
  const status = displayValue(item.status, displayValue(item.action, 'unknown'))
    .split('.')
    .pop();
  const statusLabel =
    status === 'success'
      ? 'Синхронизировано'
      : status === 'running'
        ? 'Синхронизация идёт'
        : status === 'pending'
          ? 'Ожидает запуска'
          : 'Нужна проверка';
  return (
    <div className="sync-summary">
      <span
        className={
          status === 'success'
            ? 'sync-dot sync-dot--ok'
            : 'sync-dot sync-dot--bad'
        }
      />
      <div>
        <strong>{statusLabel}</strong>
        <small>
          {displayValue(item.created_at, displayValue(item.last_synced_at))}
        </small>
        {Boolean(item.error) && <small>{displayValue(item.error)}</small>}
      </div>
    </div>
  );
}
function AdminApp({
  onLogout,
  onBackToTeacher,
  onPreview,
}: {
  onLogout: () => void;
  onBackToTeacher?: () => void;
  onPreview: (userId: string) => Promise<void>;
}) {
  const [data, setData] = useState<AdminData | null>(null);
  const [section, setSection] = useState<AdminSection>('overview');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [query, setQuery] = useState('');
  const [previewQuery, setPreviewQuery] = useState('');
  const [peopleFilter, setPeopleFilter] = useState<
    'all' | 'student' | 'teacher' | 'admin' | 'unmatched'
  >('all');
  const [peopleClassFilter, setPeopleClassFilter] = useState('');
  const [peopleGroupFilter, setPeopleGroupFilter] = useState('');
  const [structureClassFilter, setStructureClassFilter] = useState('');
  const [structureGroupFilter, setStructureGroupFilter] = useState('');
  const [selectedStructureGroupId, setSelectedStructureGroupId] = useState('');
  const [selectedUser, setSelectedUser] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [selectedGroupId, setSelectedGroupId] = useState('');
  const [selectedEntity, setSelectedEntity] = useState<AdminEntityRef | null>(null);
  const [entityHistory, setEntityHistory] = useState<AdminEntityRef[]>([]);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const results = await Promise.allSettled([
        api<Record<string, number>>('/api/admin/overview'),
        api<{ items: Array<Record<string, string>> }>('/api/admin/claims'),
        api<{ items: Array<Record<string, unknown>> }>('/api/admin/users'),
        api<{ items: Array<Record<string, unknown>> }>('/api/admin/groups'),
        api<{ items: Array<Record<string, unknown>> }>(
          '/api/admin/schedule/syncs',
        ),
        api<{ items: Array<Record<string, unknown>> }>(
          '/api/admin/classroom/syncs',
        ),
        api<{ items: Array<Record<string, unknown>> }>('/api/admin/journals'),
        api<{ items: Array<Record<string, unknown>> }>(
          '/api/admin/information',
        ),
        api<{ items: Array<Record<string, unknown>> }>(
          '/api/admin/schedule/parses',
        ),
        api<{ items: Array<Record<string, unknown>> }>('/api/admin/audit'),
        api<{ items: Array<Record<string, unknown>> }>('/api/admin/people'),
      ]);
      const value = <T,>(index: number, fallback: T): T => {
        const result = results[index];
        return result.status === 'fulfilled' ? (result.value as T) : fallback;
      };
      const [
        overview,
        claims,
        users,
        groups,
        syncs,
        classroomSyncs,
        journals,
        information,
        parseIssues,
        audit,
        people,
      ] = [
        value(0, {} as Record<string, number>),
        value(1, { items: [] as Array<Record<string, string>> }),
        value(2, { items: [] as Array<Record<string, unknown>> }),
        value(3, { items: [] as Array<Record<string, unknown>> }),
        value(4, { items: [] as Array<Record<string, unknown>> }),
        value(5, { items: [] as Array<Record<string, unknown>> }),
        value(6, { items: [] as Array<Record<string, unknown>> }),
        value(7, { items: [] as Array<Record<string, unknown>> }),
        value(8, { items: [] as Array<Record<string, unknown>> }),
        value(9, { items: [] as Array<Record<string, unknown>> }),
        value(10, { items: [] as Array<Record<string, unknown>> }),
      ];
      setData({
        overview,
        claims: claims.items,
        users: users.items,
        people: people.items,
        groups: groups.items,
        scheduleSyncs: syncs.items,
        classroomSyncs: classroomSyncs.items,
        journals: journals.items,
        information: information.items,
        parseIssues: parseIssues.items,
        audit: audit.items,
      });
      const failed = results.filter((result) => result.status === 'rejected');
      if (failed.length) {
        setMessage(
          `Часть данных недоступна (${failed.length}/11). Можно обновить ещё раз.`,
        );
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, []);
  const issueBrowserLoginCode = useCallback(async () => {
    try {
      const result = await api<{ code: string; expires_at: string }>(
        '/api/admin/auth/challenge',
        { method: 'POST' },
      );
      setMessage(`Одноразовый код для browser Admin: ${result.code}. Действует до ${result.expires_at}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Не удалось создать browser login code');
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const mutate = async (path: string, body?: unknown) => {
    setMessage('Сохраняем…');
    try {
      await api(path, {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      await load();
      setMessage('Изменение сохранено и записано в аудит');
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : 'Операция не выполнена',
      );
    }
  };
  const catalogUsers = useMemo<Array<Record<string, unknown>>>(
    () => {
      if (!data) return [];
      const people = data.people.map((person): Record<string, unknown> => {
        const account = data.users.find(
          (user) => displayValue(user.identity_id) === displayValue(person.id),
        );
        return Object.assign({}, person, account, {
          // People projection exposes the linked account id explicitly. Keep
          // it as the preview target even if the legacy users join misses.
          id: account?.id || person.user_id || person.id,
          identity_id: person.id,
          display_name: person.display_name,
          class_name: person.class_name,
          roles: person.roles,
          has_account: Boolean(account || person.user_id),
        });
      });
      const unmatchedAccounts = data.users
        .filter((user) => !user.identity_id)
        .map((user): Record<string, unknown> => ({
          ...user,
          id: user.id,
          identity_id: null,
          display_name: user.display_name,
          roles: user.roles || [user.role],
          has_account: true,
        }));
      return [...people, ...unmatchedAccounts];
    },
    [data],
  );
  const classGroups = useMemo(
    () => data?.groups.filter((group) => group.group_type === 'class') || [],
    [data],
  );
  const instructionalGroups = useMemo(
    () => data?.groups.filter((group) => group.group_type !== 'class') || [],
    [data],
  );
  const peopleGroupOptions = useMemo(() => {
    if (!peopleClassFilter) return instructionalGroups;
    const classPeople = catalogUsers.filter(
      (person) => personBaseClass(person) === peopleClassFilter,
    );
    const ids = new Set(
      classPeople.flatMap((person) =>
        personMemberships(person).map((membership) =>
          displayValue((membership as Record<string, unknown>).id),
        ),
      ),
    );
    return instructionalGroups.filter(
      (group) =>
        displayValue(group.base_class_name) === peopleClassFilter ||
        ids.has(displayValue(group.id)),
    );
  }, [catalogUsers, instructionalGroups, peopleClassFilter]);
  const filteredUsers = useMemo(
    () =>
      catalogUsers.filter((user) => {
        const roles = Array.isArray(user.roles)
          ? user.roles.filter((item): item is string => typeof item === 'string')
          : [displayValue(user.role)];
        const matchesFilter =
          peopleFilter === 'all'
            ? true
            : peopleFilter === 'unmatched'
              ? !user.identity_id
              : roles.includes(peopleFilter) ||
                displayValue(user.identity_kind) === peopleFilter;
        const matchesClass =
          !peopleClassFilter || personBaseClass(user) === peopleClassFilter;
        const matchesGroup =
          !peopleGroupFilter ||
          personMemberships(user).some(
            (membership) =>
              displayValue((membership as Record<string, unknown>).id) ===
              peopleGroupFilter,
          );
        return (
          matchesFilter &&
          matchesClass &&
          matchesGroup &&
          `${displayValue(user.display_name)} ${personBaseClass(user)} ${roles.join(' ')}`
            .toLowerCase()
            .includes(query.toLowerCase())
        );
      }) || [],
    [catalogUsers, peopleClassFilter, peopleFilter, peopleGroupFilter, query],
  );
  const previewStudents = useMemo(
    () => catalogUsers.filter((user) => {
      const roles = Array.isArray(user.roles) ? user.roles : [];
      return user.identity_kind === 'student' && user.identity_id && roles.includes('student') &&
        `${displayValue(user.display_name)} ${personBaseClass(user)}`.toLowerCase().includes(previewQuery.toLowerCase());
    }),
    [catalogUsers, previewQuery],
  );
  const structureGroups = useMemo(
    () =>
      data?.groups.filter((group) => {
        if (structureClassFilter && group.group_type === 'class')
          return displayValue(group.name) === structureClassFilter;
        if (structureClassFilter && group.group_type !== 'class') {
          return (
            displayValue(group.base_class_name) === structureClassFilter ||
            (Array.isArray(group.students) &&
              group.students.some(
                (student) =>
                  personBaseClass(student) === structureClassFilter,
              ))
          );
        }
        return true;
      }) || [],
    [data, structureClassFilter],
  );
  const filteredGroups = useMemo(
    () =>
      structureGroups.filter(
        (group) =>
          (!structureGroupFilter ||
            displayValue(group.id) === structureGroupFilter) &&
          `${displayValue(group.name)} ${displayValue(group.display_name)} ${displayValue(group.group_type)} ${displayValue(group.subject)} ${displayValue(group.base_class_name)} ${displayValue(group.subject_subgroup)} ${displayValue(group.exam_track)}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [query, structureGroupFilter, structureGroups],
  );
  const entityLabel = (entity: AdminEntityRef) => {
    if (entity.kind === 'person') {
      const person = catalogUsers.find(
        (item) => displayValue(item.identity_id, displayValue(item.id)) === entity.id,
      );
      return person ? personLabel(person) : 'Человек';
    }
    const group = data?.groups.find((item) => displayValue(item.id) === entity.id);
    return group ? groupLabel(group) : entity.kind === 'class' ? 'Класс' : 'Группа';
  };
  const openPersonEntity = (
    user: Record<string, unknown>,
    resetHistory = true,
  ) => {
    setSelectedUser(user);
    setSelectedGroupId('');
    if (resetHistory) setEntityHistory([]);
    setSelectedEntity({
      kind: 'person',
      id: displayValue(user.identity_id, displayValue(user.id)),
    });
    setSection('people');
  };
  const openRelatedEntity = (entity: AdminEntityRef) => {
    if (selectedEntity && selectedEntity.id !== entity.id) {
      setEntityHistory((history) => [...history, selectedEntity]);
    }
    setSelectedEntity(entity);
    if (entity.kind === 'person') {
      const person = catalogUsers.find(
        (item) => displayValue(item.identity_id, displayValue(item.id)) === entity.id,
      );
      setSelectedUser(person || null);
      setSection('people');
    } else {
      setSection('structure');
    }
  };
  const leaveEntityDetail = () => {
    const previous = entityHistory[entityHistory.length - 1];
    if (!previous) {
      setSelectedEntity(null);
      setEntityHistory([]);
      return;
    }
    setEntityHistory((history) => history.slice(0, -1));
    setSelectedEntity(previous);
    if (previous.kind === 'person') {
      const person = catalogUsers.find(
        (item) => displayValue(item.identity_id, displayValue(item.id)) === previous.id,
      );
      setSelectedUser(person || null);
      setSection('people');
    } else {
      setSection('structure');
    }
  };
  const openGlobalSection = (nextSection: AdminSection) => {
    setSelectedEntity(null);
    setEntityHistory([]);
    setSelectedStructureGroupId('');
    setSelectedUser(null);
    setSelectedGroupId('');
    setSection(nextSection);
  };
  const toggleRole = (user: Record<string, unknown>, role: string) => {
    if (user.has_account === false) return;
    const current = Array.isArray(user.roles)
      ? user.roles.filter((item): item is string => typeof item === 'string')
      : [];
    const roles = current.includes(role)
      ? current.filter((item) => item !== role)
      : [...current, role];
    if (roles.length)
      void mutate(`/api/admin/users/${displayValue(user.id)}/roles`, { roles });
  };
  const assignment = (
    kind: 'membership' | 'teacher' | 'homeroom',
    active = true,
  ) => {
    if (!selectedUser || !selectedGroupId) return;
    const identity = displayValue(selectedUser.identity_id);
    if (kind === 'membership')
      void mutate('/api/admin/membership-overrides', {
        identity_id: identity,
        group_id: selectedGroupId,
        member_role: 'student',
        action: active ? 'include' : 'exclude',
        reason: 'Изменено администратором',
      });
    if (kind === 'teacher')
      void mutate('/api/admin/teacher-assignments', {
        teacher_identity_id: identity,
        group_id: selectedGroupId,
        active,
      });
    if (kind === 'homeroom')
      void mutate('/api/admin/homeroom-assignments', {
        teacher_identity_id: identity,
        group_id: selectedGroupId,
        active,
      });
  };
  const content = () => {
    if (!data) return null;
    if (section === 'preview')
      return (
        <>
          <AdminHeading
            eyebrow="Только чтение"
            title="Предпросмотр ученика"
            detail="Откройте настоящий Student Campus с identity выбранного ученика. Ваши роли и профиль не изменятся."
          />
          <section className="work-card student-preview-picker">
            <label className="admin-search student-preview-picker__search">
              <Search size={16} />
              <input value={previewQuery} onChange={(event) => setPreviewQuery(event.target.value)} placeholder="Найти по имени или классу" />
            </label>
            <div className="student-preview-picker__meta">{previewStudents.length} доступных учеников</div>
            {previewStudents.length ? previewStudents.slice(0, 30).map((student, index) => (
              <div className="student-preview-row" key={displayValue(student.id, String(index))}>
                <span className="avatar-mini">{personLabel(student).slice(0, 1)}</span>
                <div><strong>{personLabel(student)}</strong><small>{personBaseClass(student) || 'Класс не указан'}</small></div>
                <button className="preview-button" onClick={() => void onPreview(displayValue(student.user_id || `identity:${String(student.identity_id)}`)).catch((error) => setMessage(error instanceof Error ? error.message : 'Экран ученика не открылся'))}>Открыть как ученик</button>
              </div>
            )) : <p className="calm-empty">Ученики не найдены. Проверьте имя или класс.</p>}
          </section>
        </>
      );
    if (section === 'overview')
      return (
        <>
          <AdminHeading
            eyebrow="Сегодня в My Island"
            title="Главная"
            detail="Важные события, состояние данных и следующие действия."
          />
          <section className="work-card">
            <div className="work-card__heading">
              <h2>Требует внимания</h2>
              <span className="count-badge">
                {data.claims.length +
                  data.parseIssues.length +
                  (data.overview.unlinked_accounts || 0)}
              </span>
            </div>
            {data.claims.slice(0, 3).map((claim) => (
              <div className="action-row" key={claim.id}>
                <span className="avatar-mini">
                  {claim.display_name?.slice(0, 1)}
                </span>
                <div>
                  <strong>{claim.display_name}</strong>
                  <small>Заявка: {humanRole(claim.requested_role)}</small>
                </div>
                <button
                  className="success-button"
                  onClick={() =>
                    void mutate(`/api/admin/claims/${claim.id}/review`, {
                      status: 'approved',
                    })
                  }
                >
                  Одобрить
                </button>
              </div>
            ))}
            {data.parseIssues.slice(0, 3).map((issue, index) => (
              <button
                className="action-row"
                key={index}
                onClick={() => setSection('schedule')}
              >
                <CircleAlert size={18} />
                <div>
                  <strong>
                    {displayValue(issue.audience)} ·{' '}
                    {displayValue(issue.subject)}
                  </strong>
                  <small>
                    {displayValue(issue.lesson_date)}{' '}
                    {displayValue(issue.start_time)} ·{' '}
                    {displayValue(issue.source_coordinate)}
                  </small>
                </div>
                <ChevronRight size={16} />
              </button>
            ))}
            {Boolean(data.overview.unlinked_accounts) && (
              <button
                className="action-row"
                onClick={() => setSection('people')}
              >
                <UserRound size={18} />
                <div>
                  <strong>
                    {data.overview.unlinked_accounts} аккаунта без школьного
                    профиля
                  </strong>
                  <small>Проверьте связь человека с Telegram</small>
                </div>
                <ChevronRight size={16} />
              </button>
            )}
            {!data.claims.length &&
              !data.parseIssues.length &&
              !data.overview.unlinked_accounts && (
                <p className="calm-empty">Сейчас ничего срочного нет.</p>
              )}
          </section>
          <section className="work-card">
            <div className="work-card__heading">
              <h2>Последние изменения</h2>
              <button
                className="text-action"
                onClick={() => setSection('diagnostics')}
              >
                Вся история
              </button>
            </div>
            {data.audit.slice(0, 4).map((event, index) => (
              <div className="audit-row" key={index}>
                <span className="audit-dot" />
                <div>
                  <strong>
                    {humanAuditAction(displayValue(event.action))}
                  </strong>
                  <small>
                    {displayValue(event.actor_name)} ·{' '}
                    {displayValue(event.created_at)}
                  </small>
                </div>
              </div>
            ))}
          </section>
          <section className="work-card">
            <div className="work-card__heading">
              <h2>Источники данных</h2>
              <button
                className="text-action"
                onClick={() => setSection('integrations')}
              >
                Открыть
              </button>
            </div>
            <div className="source-health-list">
              <div>
                <CalendarDays size={17} />
                <span>
                    <strong>Расписание · новый pipeline не подключён</strong>
                  <small>
                    {data.scheduleSyncs.length
                      ? 'Источник подключён'
                      : 'Ещё не синхронизировано'}
                  </small>
                </span>
              </div>
              <div>
                <BarChart3 size={17} />
                <span>
                  <strong>Журнал · исходный snapshot</strong>
                  <small>
                    {data.journals.length
                      ? `${data.overview.journal_results || 0} записей · не canonical grades`
                      : 'Нет импортированного snapshot'}
                  </small>
                </span>
              </div>
              <div>
                <BookOpen size={17} />
                <span>
                  <strong>Classroom · metadata</strong>
                  <small>
                    {data.classroomSyncs.length
                      ? 'Не является официальным журналом'
                      : 'Нет истории синхронизации'}
                  </small>
                </span>
              </div>
            </div>
          </section>
        </>
      );
    if (section === 'people')
      return (
        <>
          <AdminHeading
            eyebrow="Аккаунты, роли и доступ"
            title="Люди"
            detail="Один каталог учеников, учителей и администраторов."
          />
          <div className="split-view">
            <section className="work-card people-catalog">
              <div className="people-filters" aria-label="Фильтр людей">
                {(
                  [
                    ['all', 'Все'],
                    ['student', 'Ученики'],
                    ['teacher', 'Учителя'],
                    ['admin', 'Администраторы'],
                    ['unmatched', 'Требуют сопоставления'],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    className={peopleFilter === id ? 'is-active' : ''}
                    onClick={() => setPeopleFilter(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="people-filters people-filters--selects" aria-label="Фильтр школы">
                <select
                  className="control-select"
                  aria-label="Базовый класс"
                  value={peopleClassFilter}
                  onChange={(event) => {
                    setPeopleClassFilter(event.target.value);
                    setPeopleGroupFilter('');
                  }}
                >
                  <option value="">Все классы</option>
                  {classGroups.map((group) => (
                    <option key={displayValue(group.id)} value={displayValue(group.name)}>
                      {displayValue(group.name)}
                    </option>
                  ))}
                </select>
                <select
                  className="control-select"
                  aria-label="Учебная или экзаменационная группа"
                  value={peopleGroupFilter}
                  onChange={(event) => setPeopleGroupFilter(event.target.value)}
                >
                  <option value="">Все учебные группы</option>
                  {peopleGroupOptions.map((group) => (
                    <option key={displayValue(group.id)} value={displayValue(group.id)}>
                      {groupLabel(group as Record<string, unknown>)}
                    </option>
                  ))}
                </select>
              </div>
              {filteredUsers.map((user, index) => (
                <button
                  className={`person-row ${displayValue(selectedUser?.id) === displayValue(user.id) ? 'is-selected' : ''}`}
                  key={displayValue(user.id, String(index))}
                  onClick={() => openPersonEntity(user)}
                >
                  <span className="avatar-mini">
                    {personLabel(user).slice(0, 1)}
                  </span>
                  <div>
                    <strong>{personLabel(user)}</strong>
                    <small>{personSummary(user)}</small>
                  </div>
                  <ChevronRight size={16} />
                </button>
              ))}
            </section>
            <section className="work-card detail-card">
              {selectedUser ? (
                <>
                  <div className="person-hero">
                    <span className="avatar-large">
                      {personLabel(selectedUser).slice(0, 1)}
                    </span>
                    <div>
                      <h2>{personLabel(selectedUser)}</h2>
                      <p>
                        {personSummary(selectedUser)} ·{' '}
                        {displayValue(selectedUser.identity_status) ===
                        'confirmed'
                          ? 'личность подтверждена'
                          : 'требует сопоставления'}
                      </p>
                    </div>
                  </div>
                  {entityHistory.length > 0 && (
                    <button className="text-action" onClick={leaveEntityDetail}>
                      <ChevronLeft size={14} /> Назад к {entityLabel(entityHistory[entityHistory.length - 1])}
                    </button>
                  )}
                  <div className="subsection-title">Роли</div>
                  <div className="chip-row">
                    {['student', 'teacher', 'admin'].map((role) => (
                      <button
                        key={role}
                        className={
                          Array.isArray(selectedUser.roles) &&
                          selectedUser.roles.includes(role)
                            ? 'role-chip is-active'
                            : 'role-chip'
                        }
                        onClick={() => toggleRole(selectedUser, role)}
                        disabled={selectedUser.has_account === false}
                      >
                        {humanRole(role)}
                      </button>
                    ))}
                  </div>
                  <div className="subsection-title">Связи со школой</div>
                  {selectedUser.identity_kind === 'student' && (
                    <>
                      <div className="person-meta-row">
                        <strong>Базовый класс:</strong>{' '}
                        {(() => {
                          const className = personBaseClass(selectedUser);
                          const classGroup = classGroups.find(
                            (group) => displayValue(group.name) === className,
                          );
                          return classGroup ? (
                            <button
                              className="text-action"
                              onClick={() =>
                                openRelatedEntity({
                                  kind: 'class',
                                  id: displayValue(classGroup.id),
                                })
                              }
                            >
                              {className} <ChevronRight size={14} />
                            </button>
                          ) : (
                            className || 'не подтверждён'
                          );
                        })()}
                      </div>
                      {displayValue(selectedUser.account_status) && (
                        <div className="person-meta-row">
                          <strong>Telegram / аккаунт:</strong>{' '}
                          {displayValue(selectedUser.account_status)}
                        </div>
                      )}
                    </>
                  )}
                  {Array.isArray(selectedUser.roles) &&
                    selectedUser.roles.includes('student') &&
                    selectedUser.has_account !== false &&
                    selectedUser.identity_id && (
                      <button
                        className="preview-button"
                        onClick={() =>
                          void onPreview(displayValue(selectedUser.id)).catch((error) =>
                            setMessage(error instanceof Error ? error.message : 'Экран ученика не открылся'),
                          )
                        }
                      >
                        <UserRound size={16} /> Посмотреть как ученик
                      </button>
                    )}
                  {selectedUser.identity_kind === 'student' && (
                    <>
                      <div className="subsection-title">Предметные группы</div>
                      <div className="related-list">
                        {Array.isArray(selectedUser.instructional_memberships) &&
                          selectedUser.instructional_memberships.map((group, index) => (
                            <button
                              key={index}
                              onClick={() =>
                                openRelatedEntity({
                                  kind: 'group',
                                  id: displayValue((group as Record<string, unknown>).id),
                                })
                              }
                            >
                              {groupLabel(group as Record<string, unknown>)}
                              {Boolean((group as Record<string, unknown>).is_manual) && ' · вручную'}
                              <ChevronRight size={14} />
                            </button>
                          ))}
                      </div>
                      <div className="subsection-title">ОГЭ / ЕГЭ / профиль</div>
                      <div className="related-list">
                        {Array.isArray(selectedUser.exam_profile_memberships) &&
                          selectedUser.exam_profile_memberships.map((group, index) => (
                            <button
                              key={index}
                              onClick={() =>
                                openRelatedEntity({
                                  kind: 'group',
                                  id: displayValue((group as Record<string, unknown>).id),
                                })
                              }
                            >
                              {groupLabel(group as Record<string, unknown>)}
                              {Boolean((group as Record<string, unknown>).is_manual) && ' · вручную'}
                              <ChevronRight size={14} />
                            </button>
                          ))}
                      </div>
                    </>
                  )}
                  {selectedUser.identity_kind !== 'student' && <div className="subsection-title">Связи</div>}
                  {selectedUser.identity_kind !== 'student' && <div className="related-list">
                    {Array.isArray(selectedUser.groups) &&
                      selectedUser.groups.map((group, index) => (
                        <button
                          key={index}
                          onClick={() =>
                            openRelatedEntity({
                              kind: 'group',
                              id: displayValue((group as Record<string, unknown>).id),
                            })
                          }
                        >
                          {groupLabel(group as Record<string, unknown>)}{' '}
                          <ChevronRight size={14} />
                        </button>
                      ))}
                  </div>}
                  {Array.isArray(selectedUser.teacher_assignments) &&
                    selectedUser.teacher_assignments.length > 0 && (
                      <>
                        <div className="subsection-title">Постоянные назначения</div>
                        <div className="related-list">
                          {selectedUser.teacher_assignments.map((assignment, index) => (
                            <div className="person-meta-row" key={index}>
                              {displayValue(assignment.display_name, assignment.name)}
                              {displayValue(assignment.subject)
                                ? ` · ${displayValue(assignment.subject)}`
                                : ''}
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  {Array.isArray(selectedUser.unresolved) &&
                    selectedUser.unresolved.length > 0 && (
                      <div className="notice-card">
                        Требует внимания: {selectedUser.unresolved.length} unresolved issue(s).
                      </div>
                    )}
                </>
              ) : (
                <EmptyState
                  icon={UserRound}
                  title="Выберите человека"
                  detail="Здесь появятся роли, доступ и школьные связи."
                />
              )}
            </section>
          </div>
        </>
      );
    if (
      section === 'structure' &&
      selectedEntity &&
      (selectedEntity.kind === 'class' || selectedEntity.kind === 'group')
    ) {
      const selectedEntityGroup = data.groups.find(
        (group) => displayValue(group.id) === selectedEntity.id,
      );
      if (selectedEntityGroup) {
        const students = Array.isArray(selectedEntityGroup.students)
          ? (selectedEntityGroup.students as Array<Record<string, unknown>>)
          : [];
        const assignments = Array.isArray(selectedEntityGroup.teacher_assignments)
          ? (selectedEntityGroup.teacher_assignments as Array<Record<string, unknown>>)
          : [];
        return (
          <>
            <AdminHeading
              eyebrow={selectedEntity.kind === 'class' ? 'Базовый класс' : humanGroupType(selectedEntityGroup.group_type)}
              title={groupLabel(selectedEntityGroup as Record<string, unknown>)}
              detail="Точная карточка сущности и её подтверждённый состав."
            />
            <section className="work-card detail-card entity-detail">
              <button className="text-action" onClick={leaveEntityDetail}>
                <ChevronLeft size={14} />{' '}
                {entityHistory.length > 0
                  ? `Назад к ${entityLabel(entityHistory[entityHistory.length - 1])}`
                  : 'Назад к школе'}
              </button>
              <div className="work-card__heading">
                <div>
                  <h2>{groupLabel(selectedEntityGroup as Record<string, unknown>)}</h2>
                  <small>
                    {humanGroupType(selectedEntityGroup.group_type)} ·{' '}
                    {displayValue(selectedEntityGroup.member_count, String(students.length))} участников
                  </small>
                </div>
                <small>
                  {displayValue(selectedEntityGroup.provenance_source) || 'Источник не указан'}
                  {displayValue(selectedEntityGroup.provenance_ref)
                    ? ` · ${displayValue(selectedEntityGroup.provenance_ref)}`
                    : ''}
                </small>
              </div>
              {assignments.length > 0 && (
                <div className="person-meta-row">
                  <strong>Преподаватели:</strong>{' '}
                  {assignments.map((assignment) =>
                    displayValue(
                      assignment.teacher_name,
                      displayValue(assignment.display_name, displayValue(assignment.name)),
                    ),
                  ).filter(Boolean).join(', ')}
                </div>
              )}
              <div className="subsection-title">Состав</div>
              {students.length > 0 ? (
                <div className="related-list">
                  {students.map((student, index) => (
                    <button
                      key={displayValue(student.id, String(index))}
                      onClick={() => {
                        const person = catalogUsers.find(
                          (item) => displayValue(item.identity_id, displayValue(item.id)) === displayValue(student.id),
                        );
                        if (person) {
                          openRelatedEntity({ kind: 'person', id: displayValue(student.id) });
                        }
                      }}
                    >
                      {displayValue(student.display_name, displayValue(student.name))}
                      {Boolean(student.is_manual) && ' · вручную'}
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
              ) : (
                <EmptyState
                  icon={Users}
                  title="Состав не найден"
                  detail="Для этой сущности пока нет подтверждённых участников."
                />
              )}
            </section>
          </>
        );
      }
    }
    if (section === 'structure')
      return (
        <>
          <AdminHeading
            eyebrow="Классы, группы и преподаватели"
            title="Школа"
            detail="Кто где учится и преподаёт — без дублирования аккаунтов."
          />
          <section className="work-card school-assignment-editor">
            <div className="work-card__heading">
              <h2>Управление связями</h2>
              <small>Отдельно от просмотра структуры школы</small>
            </div>
            <select
              className="control-select"
              value={displayValue(selectedUser?.id)}
              onChange={(event) => {
                setSelectedUser(
                  data.users.find(
                    (user) => displayValue(user.id) === event.target.value,
                  ) || null,
                );
                setSelectedGroupId('');
              }}
            >
              <option value="">Выберите человека…</option>
              {data.users
                .filter((user) => Boolean(user.identity_id))
                .map((user) => (
                  <option
                    key={displayValue(user.id)}
                    value={displayValue(user.id)}
                  >
                    {displayValue(user.display_name)}
                  </option>
                ))}
            </select>
            <select
              className="control-select"
              value={selectedGroupId}
              onChange={(event) => setSelectedGroupId(event.target.value)}
            >
              <option value="">Выберите класс или группу…</option>
              {data.groups.map((group) => (
                <option
                  key={displayValue(group.id)}
                  value={displayValue(group.id)}
                >
                  {groupLabel(group as Record<string, unknown>)} ·{' '}
                  {humanGroupType(group.group_type)}
                </option>
              ))}
            </select>
            {selectedUser && selectedGroupId && (
              <div className="stack-actions">
                {Array.isArray(selectedUser.roles) &&
                  selectedUser.roles.includes('student') && (
                    <>
                      <button onClick={() => assignment('membership', true)}>
                        Добавить в группу
                      </button>
                      <button onClick={() => assignment('membership', false)}>
                        Исключить из группы
                      </button>
                    </>
                  )}
                {Array.isArray(selectedUser.roles) &&
                  selectedUser.roles.includes('teacher') && (
                    <>
                      <button onClick={() => assignment('teacher')}>
                        Назначить преподавателем
                      </button>
                      <button onClick={() => assignment('teacher', false)}>
                        Снять назначение
                      </button>
                      <button onClick={() => assignment('homeroom')}>
                        Назначить классным руководителем
                      </button>
                    </>
                  )}
              </div>
            )}
          </section>
          <section className="work-card">
            <div className="people-filters people-filters--selects" aria-label="Фильтр школьной структуры">
              <select
                className="control-select"
                aria-label="Класс структуры"
                value={structureClassFilter}
                onChange={(event) => {
                  setStructureClassFilter(event.target.value);
                  setStructureGroupFilter('');
                }}
              >
                <option value="">Все классы</option>
                {classGroups.map((group) => (
                  <option key={displayValue(group.id)} value={displayValue(group.name)}>
                    {displayValue(group.name)}
                  </option>
                ))}
              </select>
              <select
                className="control-select"
                aria-label="Группа структуры"
                value={structureGroupFilter}
                onChange={(event) => setStructureGroupFilter(event.target.value)}
              >
                <option value="">Все группы</option>
                {structureGroups.filter((group) => group.group_type !== 'class').map((group) => (
                  <option key={displayValue(group.id)} value={displayValue(group.id)}>
                    {groupLabel(group as Record<string, unknown>)}
                  </option>
                ))}
              </select>
            </div>
            <div className="work-card__heading">
              <h2>Базовые классы</h2>
              <small>Состав класса — отдельная связь, не дочерняя группа</small>
            </div>
            <div className="structure-grid">
              {filteredGroups.filter((group) => group.group_type === 'class').map((group, index) => (
                <article
                  className="structure-node"
                  key={displayValue(group.id, String(index))}
                >
                  <span>
                    <School size={18} />
                  </span>
                  <div>
                    <strong>{groupLabel(group)}</strong>
                    <small>
                      {humanGroupType(group.group_type)} ·{' '}
                      {displayValue(group.member_count, '0')} участников ·{' '}
                      {displayValue(group.teacher_count, '0')} учителей
                    </small>
                  </div>
                  <div className="node-links">
                    <button
                      onClick={() =>
                        openRelatedEntity({
                          kind: 'class',
                          id: displayValue(group.id),
                        })
                      }
                    >
                      Состав
                    </button>
                    <button
                      onClick={() => {
                        setPeopleClassFilter(displayValue(group.name));
                        setPeopleGroupFilter('');
                        setQuery('');
                        openGlobalSection('people');
                      }}
                    >
                      Люди
                    </button>
                    <button onClick={() => setSection('integrations')}>
                      Данные
                    </button>
                  </div>
                </article>
              ))}
            </div>
            <div className="work-card__heading">
              <h2>Учебные и экзаменационные группы</h2>
              <small>Это memberships учеников, а не дополнительные классы</small>
            </div>
            <div className="structure-grid">
              {filteredGroups.filter((group) => group.group_type !== 'class').map((group, index) => (
                <article
                  className={`structure-node ${selectedStructureGroupId === displayValue(group.id) ? 'is-selected' : ''}`}
                  key={`instructional-${displayValue(group.id, String(index))}`}
                >
                  <span><School size={18} /></span>
                  <div>
                    <strong>{groupLabel(group)}</strong>
                    <small>
                      {humanGroupType(group.group_type)} · {displayValue(group.member_count, '0')} учеников
                      {displayValue(group.subject) ? ` · ${displayValue(group.subject)}` : ''}
                    </small>
                    {Array.isArray(group.teacher_assignments) && group.teacher_assignments.length > 0 && (
                      <small>Преподаватель: {group.teacher_assignments.map((item) => displayValue(item.teacher_name)).join(', ')}</small>
                    )}
                  </div>
                  <div className="node-links">
                    <button
                      onClick={() =>
                        openRelatedEntity({
                          kind: 'group',
                          id: displayValue(group.id),
                        })
                      }
                    >
                      Состав
                    </button>
                    <button onClick={() => {
                      setPeopleClassFilter(structureClassFilter || '');
                      setPeopleGroupFilter(displayValue(group.id));
                      setQuery('');
                      openGlobalSection('people');
                    }}>Люди</button>
                  </div>
                </article>
              ))}
            </div>
          </section>
          {selectedStructureGroupId && (() => {
            const selectedGroup = data.groups.find((group) => displayValue(group.id) === selectedStructureGroupId);
            if (!selectedGroup) return null;
            const students = Array.isArray(selectedGroup.students)
              ? selectedGroup.students as Array<Record<string, unknown>>
              : [];
            return (
              <section className="work-card detail-card">
                <div className="work-card__heading">
                  <h2>{groupLabel(selectedGroup as Record<string, unknown>)}</h2>
                  <small>{humanGroupType(selectedGroup.group_type)} · {displayValue(selectedGroup.member_count, '0')} active students</small>
                </div>
                <div className="related-list">
                  {students.map((student, index) => (
                    <button key={displayValue(student.id, String(index))} onClick={() => {
                      const person = catalogUsers.find(
                        (item) => displayValue(item.identity_id, displayValue(item.id)) === displayValue(student.id),
                      );
                      if (person) openRelatedEntity({ kind: 'person', id: displayValue(student.id) });
                    }}>
                      {displayValue(student.display_name)}
                      {Boolean(student.is_manual) && ' · вручную'}
                      <ChevronRight size={14} />
                    </button>
                  ))}
                </div>
              </section>
            );
          })()}
        </>
      );
    if (section === 'schedule')
      return (
        <>
          <AdminSubnav
            items={[
              { id: 'integrations', label: 'Источники' },
              { id: 'schedule', label: 'Расписание' },
            ]}
            section={section}
            onChange={setSection}
          />
          <AdminHeading
            eyebrow="Изменения и проблемы"
            title="Расписание"
            detail="Изменения и проблемы, а не сотни одинаковых строк."
            action={
              <button
                className="primary-button primary-button--compact"
                onClick={() => void mutate('/api/admin/schedule/refresh')}
              >
                <RefreshCw size={16} /> Синхронизировать
              </button>
            }
          />
          <section className="work-card">
            <SyncSummary
              item={data.scheduleSyncs[0]}
              empty="Синхронизаций ещё нет"
            />
          </section>
          <IssueList items={data.parseIssues} />
        </>
      );
    if (section === 'integrations')
      return (
        <>
          <AdminSubnav
            items={[
              { id: 'integrations', label: 'Источники' },
              { id: 'schedule', label: 'Расписание' },
            ]}
            section={section}
            onChange={setSection}
          />
          <AdminHeading
            eyebrow="Расписание, журнал и Classroom"
            title="Данные"
            detail="Состояние школьных источников и история обновлений."
          />
          <div className="integration-grid">
            <Integration
              title="Расписание 2026/27"
              icon={CalendarDays}
              summary={
                <SyncSummary
                  item={data.scheduleSyncs[0]}
                  empty="Нет запусков"
                />
              }
              onClick={() => void mutate('/api/admin/schedule/refresh')}
            />
            <Integration
              title="Google Classroom"
              icon={BookOpen}
              summary={
                <SyncSummary
                  item={data.classroomSyncs[0]}
                  empty="Нет запусков"
                />
              }
              onClick={() => void mutate('/api/admin/classroom/refresh')}
            />
            <Integration
              title="Журналы 2026/27 · snapshot"
              icon={BarChart3}
              summary={
                <p>
                  {data.journals.length
                    ? `${displayValue(data.journals[0].spreadsheet_title)} · ${displayValue(data.journals[0].result_count, '0')} записей · только чтение`
                    : 'Grade 9 Math ещё не синхронизирован'}
                </p>
              }
              onClick={() =>
                void mutate(
                  '/api/admin/journals/refresh?grade=9&subject=Математика',
                )
              }
            />
            <Integration
              title="Telegram"
              icon={ShieldCheck}
              summary={<p>Вход через Telegram защищён и работает</p>}
            />
          </div>
          {data.journals.map((journal) => (
            <section className="work-card" key={displayValue(journal.id)}>
              <div className="work-card__heading">
                <div>
                  <h2>{displayValue(journal.spreadsheet_title)}</h2>
                  <small>
                    {displayValue(journal.sheet_title)} ·{' '}
                    {displayValue(journal.assessment_count, '0')} работ ·{' '}
                    {displayValue(journal.roster_student_count, '0')} учеников в
                    источнике ·{' '}
                    {displayValue(journal.account_unlinked_count, '0')} без
                    профиля My Island
                  </small>
                </div>
                <span className="health-badge">Только чтение</span>
              </div>
              <div className="mapping-list">
                {(Array.isArray(journal.markers) ? journal.markers : []).map(
                  (marker) => {
                    const mapping = Array.isArray(journal.mappings)
                      ? journal.mappings.find(
                          (item) => displayValue(item.group_marker) === marker,
                        )
                      : undefined;
                    return (
                      <form
                        className="mapping-row"
                        key={displayValue(marker)}
                        onSubmit={(event) => {
                          event.preventDefault();
                          const form = new FormData(event.currentTarget);
                          void mutate('/api/admin/journals/group-mappings', {
                            source_id: journal.id,
                            group_marker: marker,
                            group_id: form.get('group_id'),
                            base_class_name: form.get('base_class_name'),
                            subject_subgroup: form.get('subject_subgroup'),
                            classroom_course_id: form.get(
                              'classroom_course_id',
                            ),
                            exam_track: form.get('exam_track'),
                          });
                        }}
                      >
                        <strong>{displayValue(marker)}</strong>
                        <select
                          name="group_id"
                          defaultValue={displayValue(mapping?.group_id)}
                        >
                          <option value="">
                            Внутренняя группа (опционально)
                          </option>
                          {data.groups.map((group) => (
                            <option
                              key={displayValue(group.id)}
                              value={displayValue(group.id)}
                            >
                              {groupLabel(group)} · {humanGroupType(group.group_type)}
                            </option>
                          ))}
                        </select>
                        <input
                          name="base_class_name"
                          placeholder="Базовый класс"
                          defaultValue={displayValue(mapping?.base_class_name)}
                        />
                        <input
                          name="subject_subgroup"
                          placeholder="Подгруппа"
                          defaultValue={displayValue(mapping?.subject_subgroup)}
                        />
                        <input
                          name="classroom_course_id"
                          placeholder="Classroom course ID"
                          defaultValue={displayValue(
                            mapping?.classroom_course_id,
                          )}
                        />
                        <input
                          name="exam_track"
                          placeholder="Exam track"
                          defaultValue={displayValue(mapping?.exam_track)}
                        />
                        <button className="outline-button" type="submit">
                          {mapping ? 'Изменить' : 'Сопоставить'}
                        </button>
                      </form>
                    );
                  },
                )}
              </div>
            </section>
          ))}
        </>
      );
    if (section === 'settings')
      return (
        <>
          <AdminSubnav
            items={[
              { id: 'settings', label: 'Настройки' },
              { id: 'diagnostics', label: 'Диагностика и история' },
            ]}
            section={section}
            onChange={setSection}
          />
          <AdminHeading
            eyebrow="Продуктовая конфигурация"
            title="Настройки"
            detail="Информация школы и реальные управляемые возможности."
          />
          <section className="work-card">
            <div className="admin-preference-row">
              <div>
                <span className="eyebrow">Внешний вид</span>
                <strong>Системная тема</strong>
                <small>
                  Интерфейс следует настройкам устройства; островные сцены
                  сохраняют авторскую палитру.
                </small>
              </div>
              <span className="health-badge">Активно</span>
            </div>
          </section>
          <section className="work-card">
            <div className="work-card__heading">
              <h2>Информация и объявления</h2>
              <span className="count-badge">{data.information.length}</span>
            </div>
            <form
              className="information-form"
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                void mutate('/api/admin/information', {
                  title: form.get('title'),
                  content: form.get('content'),
                  audience_kind: form.get('audience_kind'),
                  pinned: form.get('pinned') === 'on',
                  status: 'published',
                });
                event.currentTarget.reset();
              }}
            >
              <input name="title" required placeholder="Заголовок" />
              <textarea name="content" required placeholder="Сообщение школе" />
              <select name="audience_kind">
                <option value="all">Все</option>
                <option value="student">Ученики</option>
                <option value="teacher">Учителя</option>
              </select>
              <label>
                <input type="checkbox" name="pinned" /> Закрепить
              </label>
              <button
                className="primary-button primary-button--compact"
                type="submit"
              >
                Опубликовать
              </button>
            </form>
            <div className="announcement-stack">
              {data.information.map((item, index) => (
                <article className="announcement-card" key={index}>
                  <Megaphone size={17} />
                  <div>
                    <h3>{displayValue(item.title)}</h3>
                    <p>{displayValue(item.body)}</p>
                    <small>
                      {displayValue(item.audience_kind)} ·{' '}
                      {displayValue(item.status)}
                    </small>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </>
      );
    return (
      <>
        <AdminSubnav
          items={[
            { id: 'settings', label: 'Настройки' },
            { id: 'diagnostics', label: 'Диагностика и история' },
          ]}
          section={section}
          onChange={setSection}
        />
        <AdminHeading
          eyebrow="Проверка системы"
          title="Диагностика"
          detail="Технические детали отделены от рабочих экранов."
        />
        <IssueList items={data.parseIssues} />
        <section className="work-card">
          <div className="work-card__heading">
            <h2>История изменений</h2>
            <span className="count-badge">{data.audit.length}</span>
          </div>
          {data.audit.slice(0, 30).map((event, index) => (
            <div className="audit-row" key={index}>
              <span className="audit-dot" />
              <div>
                <strong>{humanAuditAction(displayValue(event.action))}</strong>
                <small>
                  {displayValue(event.actor_name)} ·{' '}
                  {displayValue(event.created_at)} ·{' '}
                  {displayValue(event.entity_type)}
                </small>
              </div>
            </div>
          ))}
        </section>
      </>
    );
  };
  return (
    <main className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand">
          <span className="brand-mark">✦</span>
          <div>
            <strong>Мой Остров</strong>
            <small>Control center</small>
          </div>
        </div>
        <nav>
          {adminNav.map((item) => (
            <button
              key={item.id}
              className={item.includes.includes(section) ? 'is-active' : ''}
              onClick={() => openGlobalSection(item.id)}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
              {item.id === 'diagnostics' &&
                Boolean(data?.parseIssues.length) && (
                  <b>{data?.parseIssues.length}</b>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          {onBackToTeacher && (
            <button onClick={onBackToTeacher}>
              <BookOpen size={17} /> Режим учителя
            </button>
          )}
          <button onClick={onLogout}>
            <LogOut size={17} /> Выйти
          </button>
        </div>
      </aside>
      <section className="admin-main">
        <header className="admin-topbar">
          <div className="global-search">
            <Search size={17} />
            <input
              aria-label="Поиск людей, классов и групп"
              name="admin-search"
              autoComplete="off"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Поиск людей, классов и групп"
            />
          </div>
          {onBackToTeacher && (
            <button className="outline-button admin-mode-switch" onClick={onBackToTeacher}>
              <BookOpen size={16} /> В режим учителя
            </button>
          )}
          <button className="outline-button admin-mode-switch" onClick={() => void issueBrowserLoginCode()}>
            <ExternalLink size={16} /> Код для браузера
          </button>
          <button
            className="icon-button icon-button--dark"
            onClick={() => void load()}
            aria-label="Обновить"
          >
            <RefreshCw size={17} />
          </button>
        </header>
        {message && (
          <div className="admin-message" aria-live="polite">
            <CircleAlert size={16} /> {message}
            <button onClick={() => setMessage('')}>
              <X size={14} />
            </button>
          </div>
        )}
        {loading ? (
          <LoadingState label="Загружаем control center" />
        ) : (
          content()
        )}
      </section>
    </main>
  );
}
function AdminHeading({
  eyebrow,
  title,
  detail,
  action,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="admin-page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      {action}
    </div>
  );
}
function IssueList({ items }: { items: Array<Record<string, unknown>> }) {
  return (
    <section className="work-card">
      <div className="work-card__heading">
        <h2>Проблемы разбора</h2>
        <span className="count-badge">{items.length}</span>
      </div>
      {items.map((issue, index) => (
        <details className="issue-row" key={index}>
          <summary>
            <div>
              <strong>
                {displayValue(issue.audience)} ·{' '}
                {displayValue(issue.lesson_date)}{' '}
                {displayValue(issue.start_time)}
              </strong>
              <small>
                Не удалось надёжно разобрать «{displayValue(issue.subject)}» ·{' '}
                {displayValue(issue.source_coordinate)}
              </small>
            </div>
            <span>{displayValue(issue.parse_status)}</span>
          </summary>
          <div className="technical-details">
            <strong>Технические детали</strong>
            <p>
              {displayValue(issue.parse_diagnostics, 'Диагностика не указана')}
            </p>
          </div>
        </details>
      ))}
    </section>
  );
}
function Integration({
  title,
  icon: Icon,
  summary,
  onClick,
}: {
  title: string;
  icon: typeof Users;
  summary: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <section className="integration-card">
      <Icon size={20} />
      <div>
        <h2>{title}</h2>
        {summary}
      </div>
      {onClick ? (
        <button onClick={onClick}>Обновить</button>
      ) : (
        <span className="health-badge">configured</span>
      )}
    </section>
  );
}

function PendingState({
  state,
  onRetry,
}: {
  state: Exclude<LoadState, 'loading' | 'approved'>;
  onRetry: () => void;
}) {
  const copy =
    state === 'needs_identity'
      ? [
          'Доступ ещё не подтверждён',
          'Личность должна быть подтверждена администратором школы. До этого персональные данные закрыты.',
        ]
      : state === 'telegram_sdk_missing'
        ? [
            'Нужен запуск из Telegram',
            'Откройте приложение кнопкой внутри Telegram.',
          ]
        : state === 'telegram_init_data_empty'
          ? [
              'Нет данных Telegram',
              'Закройте Mini App и запустите его снова из бота.',
            ]
          : state === 'network_error'
            ? ['Сервер недоступен', 'Проверьте соединение и повторите попытку.']
            : ['Не удалось загрузить данные', 'Повторите вход из Telegram.'];
  return (
    <main className="status-screen">
      <div className="status-card-login">
        <span className="status-illustration">
          {state === 'needs_identity' ? (
            <LockKeyhole size={28} />
          ) : (
            <CircleAlert size={28} />
          )}
        </span>
        <p className="eyebrow">Мой Остров</p>
        <h1>{copy[0]}</h1>
        <p>{copy[1]}</p>
        <button className="primary-button" onClick={onRetry}>
          Повторить <RefreshCw size={17} />
        </button>
      </div>
    </main>
  );
}

type EmbeddedPreviewConfig = { id: string; role: 'student' | 'teacher'; day: string; initData?: string; localAdmin?: boolean; revision?: number };

export default function Home() {
  useTelegramLayout();
  const [embedded] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('embeddedPreview'));
  const [config, setConfig] = useState<EmbeddedPreviewConfig | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [errorMessage, setErrorMessage] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [data, setData] = useState<ApiData | null>(null);
  const [teacherData, setTeacherData] = useState<TeacherData | null>(null);
  const [desiredRole, setDesiredRole] = useState<CampusRole>(() => {
    if (typeof window === 'undefined') return 'student';
    const role = new URLSearchParams(window.location.search).get('role');
    return role === 'admin' || role === 'teacher' ? role : 'student';
  });
  const [day, setDay] = useState(() => {
    const value = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('date') : null;
    return value && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) ? value : dateISO();
  });
  const [reload, setReload] = useState(0);
  const [scheduleWarning, setScheduleWarning] = useState('');
  const [scheduleWarningSource, setScheduleWarningSource] = useState('');
  const sequence = useRef(0);
  const chosenDay = config?.day || day;
  const actualRole = config?.role || session?.user.role || desiredRole;

  useEffect(() => {
    if (!embedded) return;
    const receive = (event: MessageEvent) => {
      if (event.source !== window.parent || event.origin !== window.location.origin) return;
      const value = event.data;
      if (value?.type !== 'my-island:preview-config' || typeof value.id !== 'string' || !['student', 'teacher'].includes(value.role) || !/^\d{4}-\d{2}-\d{2}$/.test(value.day) || Number.isNaN(Date.parse(value.day))) return;
      const next = { id: value.id, role: value.role, day: value.day, initData: String(value.initData || ''), localAdmin: value.localAdmin === true, revision: Number(value.revision || 0) };
      // ready/onLoad may both deliver the same configuration. Do not reboot
      // Campus and interrupt navigation when the school queries finish slowly.
      setConfig((previous) => previous && previous.id === next.id && previous.role === next.role
        && previous.day === next.day && previous.initData === next.initData
        && previous.localAdmin === next.localAdmin && previous.revision === next.revision ? previous : next);
    };
    window.addEventListener('message', receive);
    window.parent.postMessage({ type: 'my-island:preview-ready' }, window.location.origin);
    return () => { window.removeEventListener('message', receive); activeRolePreviewId = ''; previewInitData = ''; previewLocalAdmin = false; };
  }, [embedded]);

  useEffect(() => {
    if (embedded && !config) return;
    const version = ++sequence.current;
    const current = () => version === sequence.current;
    const load = async () => {
      setState('loading'); setErrorMessage(''); setScheduleWarning(''); setScheduleWarningSource('');
      try {
        let role = desiredRole;
        if (config) {
          activeRolePreviewId = config.id; previewInitData = config.initData || ''; previewLocalAdmin = Boolean(config.localAdmin); role = config.role;
        } else {
          activeRolePreviewId = ''; previewInitData = ''; previewLocalAdmin = false;
          const telegram = await telegramInitData();
          if (!current()) return;
          if (telegram.kind !== 'ready') { setState(telegram.kind); return; }
          activeTelegramInitData = telegram.value;
          const next = await api<Session>('/api/auth/session', { method: 'POST', body: JSON.stringify({ role: desiredRole, init_data: telegram.value || null }) });
          if (!current()) return;
          setSession(next);
          if (next.state !== 'approved') { setState('needs_identity'); return; }
          role = next.user.role as CampusRole;
        }
        if (role === 'admin') { if (current()) setState('approved'); return; }
        const period = periodFor(chosenDay);
        let scheduleState = '';
        let scheduleSource = '';
        if (role === 'student') {
          const [today, schedule, homework, profile] = await Promise.all([
            api<ApiData['today']>(`/api/student/today?day=${chosenDay}`),
            api<StudentScheduleProjection & { state?: string }>(`/api/student/schedule?start_day=${period.start}&end_day=${period.end}`),
            api<{ items: Homework[] }>('/api/student/homework'), api<Profile>('/api/student/profile'),
          ]);
          scheduleState = schedule.state || '';
          scheduleSource = [scheduleState, schedule.identity_id, schedule.start_day].filter(Boolean).join(':');
          if (current()) setData({ today: { ...today, schedule: campusLessons(today.schedule) }, schedule: campusLessons(schedule.items), scheduleProjection: { ...schedule, items: campusLessons(schedule.items), days: Array.isArray(schedule.days) ? schedule.days : campusStudentDays(schedule.items, schedule.day_modes) }, scheduleState, scheduleDayBlocks: schedule.day_blocks || [], homework: homework.items, profile });
        } else {
          const [home, schedule, courses, profile] = await Promise.all([
            api<{ schedule: Lesson[]; groups: Group[]; information: Information[] }>(`/api/teacher/home?day=${chosenDay}`),
            api<{ items: Lesson[]; state?: string; identity_id?: string; start_day?: string; day_modes?: Array<{ date: string; mode: 'online' | 'sdep'; class_name?: string }> }>(`/api/teacher/schedule?start_day=${period.start}&end_day=${period.end}`),
            api<{ items: Array<Record<string, unknown>> }>('/api/teacher/courses'), api<Profile & { state: string }>('/api/teacher/profile'),
          ]);
          scheduleState = schedule.state || '';
          scheduleSource = [scheduleState, schedule.identity_id, schedule.start_day].filter(Boolean).join(':');
          if (!profile.user) throw new ApiError('api_error', 'К роли преподавателя ещё не привязана школьная личность.', 403);
          if (current()) setTeacherData({ today: campusLessons(home.schedule), groups: home.groups, information: home.information, schedule: campusLessons(schedule.items), dayModes: schedule.day_modes || [], courses: courses.items, profile: profile.user ? profile : null });
        }
        if (!current()) return;
        if (scheduleState === 'canonical_backend_disabled') { setScheduleWarningSource(scheduleSource); setScheduleWarning('Источник расписания ещё не активирован. Это не подтверждение отсутствия занятий.'); }
        else if (/unavailable|not_available|not_published|not_materialized|missing|not_configured|error|blocked/.test(scheduleState)) { setScheduleWarningSource(scheduleSource); setScheduleWarning('Опубликованное расписание за этот период недоступно. Проверьте выбранную неделю и настройки источника.'); }
        setState('approved');
        if (embedded) window.parent.postMessage({ type: 'my-island:preview-loaded' }, window.location.origin);
      } catch (error) {
        if (!current()) return;
        const message = error instanceof Error ? error.message : 'Не удалось загрузить данные';
        setErrorMessage(message); setState(error instanceof ApiError ? error.kind : 'api_error');
        if (embedded) window.parent.postMessage({ type: 'my-island:preview-error', message }, window.location.origin);
      }
    };
    void load();
    return () => { sequence.current = version + 1; };
  }, [embedded, config, desiredRole, chosenDay, reload]);

  const switchRole = (role: CampusRole) => {
    if (!session?.user.roles?.includes(role)) return;
    const url = new URL(window.location.href); url.searchParams.set('role', role); url.searchParams.delete('screen'); url.hash = '';
    window.history.replaceState({}, '', `${url.pathname}${url.search}`); setDesiredRole(role);
  };
  const changeDay = (value: string) => {
    if (embedded) { window.parent.postMessage({ type: 'my-island:preview-date', day: value }, window.location.origin); return; }
    const url = new URL(window.location.href); url.searchParams.set('date', value); window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`); setDay(value);
  };
  const roleControl = !embedded && session ? <CampusRoleSwitch roles={session.user.roles || [session.user.role]} value={actualRole} onChange={switchRole} /> : undefined;
  const refresh = () => setReload((value) => value + 1);
  const leave = () => {
    if (embedded) { window.parent.postMessage({ type: 'my-island:preview-close' }, window.location.origin); return; }
    void (async () => {
      if (window.sessionStorage.getItem('my_island_admin_session')) await api('/api/admin/auth/logout', { method: 'POST' }).catch(() => undefined);
      window.sessionStorage.removeItem('my_island_admin_session'); window.sessionStorage.removeItem('my_island_telegram_init_data');
      window.Telegram?.WebApp?.close?.(); setState('telegram_init_data_empty'); setErrorMessage('Сессия завершена. Для входа откройте My Island снова.');
    })();
  };

  if (state === 'loading' || (embedded && !config)) return <main className="status-screen status-screen--island"><div className="loading-orb"><LoaderCircle className="spin" size={28} /><span>Открываем остров…</span></div></main>;
  if (state !== 'approved') return <>{roleControl ? <div className="campus-error-actions">{roleControl}</div> : null}{errorMessage ? <div className="campus-source-warning" role="alert">{errorMessage}</div> : null}<PendingState state={state} onRetry={refresh} /></>;
  if (actualRole === 'admin' && session?.user.roles?.includes('admin')) {
    if (new URLSearchParams(window.location.search).has('legacyAdmin')) return <AdminApp onPreview={async () => { window.location.assign('/admin/preview'); }} onLogout={leave} />;
    return <ModernAdmin embeddedSession={session.user} onRoleChange={switchRole} />;
  }
  return <div className={embedded ? 'preview-runtime' : undefined}>
    {embedded ? <div className="preview-runtime-banner">Предпросмотр · {actualRole === 'teacher' ? teacherData?.profile?.user.display_name : data?.profile.user.display_name} · только чтение <button onClick={leave}>В админку</button></div> : null}
    {actualRole === 'teacher' && teacherData ? <TeacherApp data={teacherData} onReload={refresh} onLogout={leave} roleControl={roleControl} day={chosenDay} onDayChange={changeDay} scheduleWarning={scheduleWarning} /> : data ? <StudentApp data={data} identityKey={String(config?.id || session?.user.identity_id || session?.user.id || data.profile.user.id || data.profile.user.display_name)} onReload={refresh} roleControl={roleControl} day={chosenDay} onDayChange={changeDay} scheduleWarning={scheduleWarning} scheduleWarningSource={scheduleWarningSource} /> : <PendingState state="api_error" onRetry={refresh} />}
  </div>;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData?: string;
        close?: () => void;
        BackButton?: { show: () => void; hide: () => void; onClick: (callback: () => void) => void; offClick: (callback: () => void) => void };
        isVersionAtLeast?: (version: string) => boolean;
        ready?: () => void;
        expand?: () => void;
        viewportHeight?: number;
        viewportStableHeight?: number;
        isFullscreen?: boolean;
        safeAreaInset?: {
          top: number;
          right: number;
          bottom: number;
          left: number;
        };
        contentSafeAreaInset?: {
          top: number;
          right: number;
          bottom: number;
          left: number;
        };
        requestFullscreen?: () => void;
        disableVerticalSwipes?: () => void;
        enableVerticalSwipes?: () => void;
        onEvent?: (
          event: string,
          callback: (...args: unknown[]) => void,
        ) => void;
        offEvent?: (
          event: string,
          callback: (...args: unknown[]) => void,
        ) => void;
      };
    };
  }
}
