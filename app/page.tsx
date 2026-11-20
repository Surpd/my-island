'use client';

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
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
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Megaphone,
  Pin,
  RefreshCw,
  School,
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
  connection_info?: string;
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
  connection_info?: string;
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
const humanGroupType = (groupType: unknown) =>
  ({
    class: 'Базовый класс',
    subject_group: 'Предметная группа',
    subject_subgroup: 'Предметная группа',
    exam_track: 'Экзаменационный трек',
    instructional_group: 'Учебная группа',
    classroom_course: 'Группа Classroom',
  })[displayValue(groupType)] || displayValue(groupType, 'Группа');
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
                lesson.connection_info,
                lesson.responsible_teacher
                  ? `Ответственный за задание: ${lesson.responsible_teacher}`
                  : 'Самостоятельная практика',
                lesson.physical_presence === false ? 'Вы не ведёте это занятие' : '',
              ].filter(Boolean).join(' · ')
            : [lesson.audience, lesson.room, lesson.teacher, lesson.connection_info]
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
    const content = <><span className="student-schedule-conflict__mark" aria-hidden="true">?</span><div><small>{item.conflict?.label || 'Уточняется'}</small><strong>{title}</strong><span>{formatLessonTimeRange(item.slot.start, item.slot.end)}</span></div></>;
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
  return <div className="section-panel student-lesson-detail"><button type="button" className="student-journal-back" aria-label="Назад к расписанию" onClick={onBack}><ChevronLeft size={23} aria-hidden="true" /></button><p className="eyebrow">Подробности занятия</p><h2>{title}</h2><dl><div><dt>Дата</dt><dd>{formatDate(selectedDetail.date, true)}</dd></div><div><dt>Время</dt><dd>{formatLessonTimeRange(selectedDetail.slot.start, selectedDetail.slot.end)}</dd></div>{selectedDetail.activity_format === 'independent_practice' ? <div><dt>Формат</dt><dd>Самостоятельная практика</dd></div> : null}{selectedDetail.activity_format === 'independent_practice' && selectedDetail.responsible_teacher ? <div><dt>Ответственный за задание</dt><dd>{selectedDetail.responsible_teacher}</dd></div> : null}{selectedDetail.teacher ? <div><dt>Преподаватель</dt><dd>{selectedDetail.teacher}</dd></div> : null}{selectedDetail.connection_info ? <div><dt>Подключение</dt><dd>{selectedDetail.connection_info}</dd></div> : null}{selectedDetail.room ? <div><dt>Кабинет</dt><dd>{normalizeRoomDisplay(selectedDetail.room)}</dd></div> : null}{context ? <div><dt>Группа</dt><dd>{context}</dd></div> : null}{selectedDetail.special_event?.metadata ? <div><dt>Сведения</dt><dd>{Object.values(selectedDetail.special_event.metadata).filter(value => typeof value === 'string' || typeof value === 'number').join(' · ')}</dd></div> : null}</dl></div>;
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
      {visibleItems.length ? <div className="student-schedule-stack">
        {visibleItems.map((item) => item.state === 'no_lesson'
          ? <div className="student-schedule-gap" key={`${item.date}-${item.slot.start}`}><span />Перерыв</div>
          : <ScheduleItemRow item={item} index={day?.items.indexOf(item) ?? 0} onOpen={key => { const scrollTop = document.querySelector<HTMLElement>('.sheet-full')?.scrollTop || 0; onSaveScroll?.(scrollTop); onOpenLesson?.(key, scrollTop); }} key={scheduleDetailKey(item, day?.items.indexOf(item) ?? 0)} />)}
      </div> : <EmptyState icon={CalendarDays} title={emptyTitle} detail={emptyDetail} />}
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
