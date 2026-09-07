'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  source_coordinate?: string | null;
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
  user: { display_name: string; class_name?: string | null };
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
    homework: Homework[];
    announcements: Information[];
  };
  schedule: Lesson[];
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
    (isLocalBuild
      ? 'http://localhost:8000'
      : 'https://my-island-api.onrender.com'),
).replace(/\/$/, '');
let activeTelegramInitData = '';
let activeStudentPreviewId = '';

function devHeader(): string | undefined {
  if (!isLocalBuild || typeof window === 'undefined') return undefined;
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
  const deadline = Date.now() + 3000;
  let found = false;
  while (Date.now() < deadline) {
    const app =
      typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;
    if (app) {
      found = true;
      if (app.initData) return { kind: 'ready', value: app.initData };
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
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body) headers.set('Content-Type', 'application/json');
  const local = devHeader();
  if (local) headers.set('X-Dev-Auth', local);
  if (activeTelegramInitData)
    headers.set('X-Telegram-Init-Data', activeTelegramInitData);
  if (activeStudentPreviewId)
    headers.set('X-Student-Preview-Id', activeStudentPreviewId);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
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
  return (
    <div className="lesson-card">
      <time>{lesson.start_time}</time>
      <div className="lesson-card__body">
        <strong>{lesson.subject}</strong>
        <span>
          {[lesson.audience, lesson.room, lesson.teacher]
            .filter(Boolean)
            .join(' · ') || 'Детали уточняются'}
        </span>
        {scopeMarker(lesson) && <small>{scopeMarker(lesson)}</small>}
      </div>
      {lesson.lesson_type && lesson.lesson_type !== 'lesson' && (
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
  right,
}: {
  name: string;
  onProfile: () => void;
  right?: React.ReactNode;
}) {
  return (
    <header className="scene-topbar">
      <div className="topbar-actions">
        {right}
        <button className="profile-button" onClick={onProfile}>
          <span className="profile-avatar">{name.slice(0, 1)}</span>
          <span className="profile-name">{name.split(' ')[0]}</span>
          <ChevronDown size={15} />
        </button>
      </div>
    </header>
  );
}

function scheduleStartingDay(schedule: Lesson[]) {
  const today = isoDate(new Date());
  const relevant = schedule
    .map((lesson) => lesson.lesson_date)
    .filter((day) => day >= today)
    .sort();
  return (
    relevant[0] ||
    schedule.map((lesson) => lesson.lesson_date).sort()[0] ||
    today
  );
}

function SchedulePanel({ schedule }: { schedule: Lesson[] }) {
  const initial = scheduleStartingDay(schedule);
  const [selectedDay, setSelectedDay] = useState(initial);
  const monday = weekStart(new Date(`${selectedDay}T12:00:00`));
  const days = Array.from({ length: 5 }, (_, index) =>
    isoDate(addDays(monday, index)),
  );
  const entries = schedule
    .filter((lesson) => lesson.lesson_date === selectedDay)
    .sort((a, b) => a.start_time.localeCompare(b.start_time));
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon">
          <CalendarDays size={21} />
        </span>
        <div>
          <p className="eyebrow">Неделя</p>
          <h2>Расписание</h2>
          <p>Только подтверждённые занятия вашего учебного контура.</p>
        </div>
      </div>
      <div className="day-picker">
        {days.map((day) => (
          <button
            key={day}
            className={day === selectedDay ? 'is-active' : ''}
            onClick={() => setSelectedDay(day)}
          >
            <small>
              {new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(
                new Date(`${day}T12:00:00`),
              )}
            </small>
            <strong>{day.slice(8)}</strong>
          </button>
        ))}
      </div>
      <div className="panel-date">
        <strong>{formatDate(selectedDay, true)}</strong>
        <span>{entries.length} занятий</span>
      </div>
      {entries.length ? (
        <div className="lesson-stack">
          {entries.map((lesson) => (
            <LessonRow
              key={`${lesson.lesson_date}-${lesson.start_time}-${lesson.subject}-${lesson.source_coordinate}`}
              lesson={lesson}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={CalendarDays}
          title="Занятий нет"
          detail="Для этого дня нет подтверждённых записей."
        />
      )}
    </div>
  );
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
          <strong>Профиль подтверждён</strong>
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
                <strong>{group.name}</strong>
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
function GradesPanel({
  items,
  loading,
}: {
  items: Array<Record<string, unknown>> | null;
  loading: boolean;
}) {
  return (
    <div className="section-panel">
      <div className="panel-intro">
        <span className="panel-icon panel-icon--violet">
          <BarChart3 size={21} />
        </span>
        <div>
          <p className="eyebrow">Официальный журнал</p>
          <h2>Оценки</h2>
          <p>Отдельно от Classroom grades.</p>
        </div>
      </div>
      {loading ? (
        <LoadingState label="Проверяем журнал" />
      ) : items?.length ? (
        <div className="grades-stack">
          {items.map((item, index) => (
            <div className="grade-card" key={index}>
              <div>
                <strong>{displayValue(item.subject, 'Предмет')}</strong>
                <span>
                  {displayValue(item.graded_on)} · вес{' '}
                  {displayValue(item.weight, '—')}
                </span>
              </div>
              <b>{displayValue(item.value, displayValue(item.status, '—'))}</b>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={BarChart3}
          title="Оценок пока нет"
          detail="Результаты появятся после публикации школьного журнала."
        />
      )}
    </div>
  );
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
}: {
  title: string;
  kicker: string;
  icon: IslandIconName;
  peek: React.ReactNode;
  full: React.ReactNode;
  className?: string;
}) {
  const [stage, setStage] = useState<'partial' | 'full'>('partial');
  const [drag, setDrag] = useState(0);
  const startY = useRef<number | null>(null);
  const moved = useRef(false);
  const onPointerDown = (event: React.PointerEvent<HTMLElement>) => {
    startY.current = event.clientY;
    moved.current = false;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLElement>) => {
    if (startY.current === null) return;
    const delta = event.clientY - startY.current;
    moved.current = Math.abs(delta) > 6;
    setDrag(Math.max(-320, Math.min(260, delta)));
  };
  const onPointerUp = (event: React.PointerEvent<HTMLElement>) => {
    if (startY.current === null) return;
    const delta = event.clientY - startY.current;
    if (delta < -52) setStage('full');
    else if (delta > 52) setStage('partial');
    else if (!moved.current)
      setStage((value) => (value === 'partial' ? 'full' : 'partial'));
    setDrag(0);
    startY.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    window.setTimeout(() => {
      moved.current = false;
    }, 0);
  };
  const offset = stage === 'full' ? '0px' : 'calc(100% - 182px)';
  const sheetStyle = {
    '--sheet-offset': `calc(${offset} + ${drag}px)`,
  } as React.CSSProperties;
  return (
    <section
      className={`spatial-sheet ${stage === 'full' ? 'is-full' : 'is-partial'} ${className}`}
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
          aria-label={
            stage === 'full' ? 'Свернуть раздел' : 'Развернуть раздел'
          }
          onClick={(event) => event.preventDefault()}
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

function TodaySheet({
  lessons,
  homework = [],
  information = [],
  teacher = false,
  onOpen,
}: {
  lessons: Lesson[];
  homework?: Homework[];
  information?: Information[];
  teacher?: boolean;
  onOpen: (view: string) => void;
}) {
  const sorted = [...lessons].sort((a, b) =>
    a.start_time.localeCompare(b.start_time),
  );
  const next =
    sorted.find((lesson) => {
      const [hours, minutes] = lesson.start_time.split(':').map(Number);
      return (
        hours * 60 + minutes >=
        new Date().getHours() * 60 + new Date().getMinutes()
      );
    }) || sorted[0];
  const task = [...homework].sort((a, b) =>
    (a.due_at || '9999').localeCompare(b.due_at || '9999'),
  )[0];
  const peek = (
    <div className={`today-peek-row ${!next ? 'is-quiet' : ''}`}>
      <button
        className="today-card today-card--primary"
        onClick={() => onOpen('schedule')}
      >
        <IslandIcon name="schedule" size={20} />
        <span>
          <small>
            {next
              ? `${next.start_time} · ${teacher ? next.audience || 'группа' : 'ближайший урок'}`
              : 'Учебный день'}
          </small>
          <strong>{next?.subject || 'Сегодня без занятий'}</strong>
          <em>
            {next
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
      {sorted.length ? (
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
    </div>
  );
  return (
    <SpatialSheet
      title="Сегодня"
      kicker={formatDate(isoDate(new Date()), true)}
      icon="schedule"
      peek={peek}
      full={full}
      className="today-sheet"
    />
  );
}

function ContextualSheet({
  title,
  icon,
  kicker,
  peek,
  children,
}: {
  title: string;
  icon: IslandIconName;
  kicker: string;
  peek: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <SpatialSheet
      title={title}
      kicker={kicker}
      icon={icon}
      className="context-sheet"
      peek={<div className="context-peek">{peek}</div>}
      full={children}
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
  sheet,
  children,
}: {
  variant: 'student' | 'teacher';
  locations: SceneHotspot[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onBack: () => void;
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
      {selected && (
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
          {variant === 'student' ? 'К острову' : 'К кампусу'}
        </button>
      )}
      {sheet}
    </main>
  );
}

function StudentApp({
  data,
  onReload,
}: {
  data: ApiData;
  onReload: () => void;
}) {
  const [view, setView] = useState<StudentView>(null);
  const [grades, setGrades] = useState<Array<Record<string, unknown>> | null>(
    null,
  );
  const [gradesLoading, setGradesLoading] = useState(false);
  const open = (next: StudentView) => {
    setView(next);
    if (next === 'grades' && grades === null) {
      setGradesLoading(true);
      void api<{ items: Array<Record<string, unknown>> }>('/api/student/grades')
        .then((result) => setGrades(result.items))
        .catch(() => setGrades([]))
        .finally(() => setGradesLoading(false));
    }
  };
  const name = data.profile.user.display_name;
  const selectedId = view && view !== 'profile' ? view : null;
  const studentLocations: SceneHotspot[] = islandLocations.map((location) => ({
    ...location,
    icon: location.id,
  }));
  const nextLesson = [...data.schedule]
    .filter((lesson) => lesson.lesson_date >= isoDate(new Date()))
    .sort((a, b) =>
      `${a.lesson_date}${a.start_time}`.localeCompare(
        `${b.lesson_date}${b.start_time}`,
      ),
    )[0];
  const nextHomework = [...data.homework].sort((a, b) =>
    (a.due_at || '9999').localeCompare(b.due_at || '9999'),
  )[0];
  const latestInfo = data.today.announcements[0];
  const contextualPeek =
    selectedId === 'schedule' ? (
      <SheetSummary
        icon="schedule"
        label={
          nextLesson ? formatDate(nextLesson.lesson_date, true) : 'Расписание'
        }
        value={
          nextLesson
            ? `${nextLesson.start_time} · ${nextLesson.subject}`
            : 'Ближайших занятий нет'
        }
        detail={nextLesson?.room || undefined}
      />
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
    ) : (
      <SheetSummary
        icon="grades"
        label="Журнал"
        value={
          gradesLoading
            ? 'Загружаем оценки…'
            : grades?.length
              ? `${grades.length} записей`
              : 'Оценок пока нет'
        }
      />
    );
  const contextual = selectedId ? (
    <ContextualSheet
      title={
        islandLocations.find((item) => item.id === selectedId)?.label ||
        'Остров'
      }
      icon={selectedId}
      kicker="Место на острове"
      peek={contextualPeek}
    >
      {selectedId === 'schedule' ? (
        <SchedulePanel schedule={data.schedule} />
      ) : selectedId === 'homework' ? (
        <HomeworkPanel homework={data.homework} />
      ) : selectedId === 'information' ? (
        <InfoPanel items={data.today.announcements} />
      ) : (
        <GradesPanel items={grades} loading={gradesLoading} />
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
        onBack={() => setView(null)}
        sheet={
          contextual || (
            <TodaySheet
              lessons={data.today.schedule}
              homework={data.today.homework}
              information={data.today.announcements}
              onOpen={(next) => open(next as StudentView)}
            />
          )
        }
      >
        <Topbar name={name} onProfile={() => open('profile')} />
      </SceneFrame>
      {view === 'profile' && (
        <PanelShell
          title="Профиль"
          backLabel="Остров"
          onClose={() => setView(null)}
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
          <p>Ученики, журнал и аналитика живут внутри группы.</p>
        </div>
      </div>
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
}: {
  data: TeacherData;
  onLogout: () => void;
  onReload: () => void;
  onOpenAdmin?: () => void;
}) {
  const [view, setView] = useState<TeacherView>(null);
  const homeroom = data.groups.find((group) => group.is_homeroom);
  const teacherLocations: SceneHotspot[] = teacherCampusLocations
    .filter((location) => location.id !== 'homeroom' || Boolean(homeroom))
    .map((location) => ({ ...location, icon: location.id }));
  const selectedId = view && view !== 'profile' ? view : null;
  const nextTeacherLesson = [...data.schedule]
    .filter((lesson) => lesson.lesson_date >= isoDate(new Date()))
    .sort((a, b) =>
      `${a.lesson_date}${a.start_time}`.localeCompare(
        `${b.lesson_date}${b.start_time}`,
      ),
    )[0];
  const subjectGroups = data.groups.filter((group) => !group.is_homeroom);
  const teacherPeek =
    selectedId === 'schedule' ? (
      <SheetSummary
        icon="schedule"
        label={
          nextTeacherLesson
            ? formatDate(nextTeacherLesson.lesson_date, true)
            : 'Расписание'
        }
        value={
          nextTeacherLesson
            ? `${nextTeacherLesson.start_time} · ${nextTeacherLesson.subject}`
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
      {selectedId === 'schedule' ? (
        <SchedulePanel schedule={data.schedule} />
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
              lessons={data.today}
              information={data.information}
              teacher
              onOpen={(next) => setView(next as TeacherView)}
            />
          )
        }
      >
        <Topbar
          name="Учитель"
          onProfile={() => setView('profile')}
          right={
            onOpenAdmin && (
              <button className="top-action" onClick={onOpenAdmin}>
                <ShieldCheck size={16} /> <span>Admin</span>
              </button>
            )
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
  onPreview: (userId: string) => void;
}) {
  const [data, setData] = useState<AdminData | null>(null);
  const [section, setSection] = useState<AdminSection>('overview');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [query, setQuery] = useState('');
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
          id: account?.id || person.id,
          identity_id: person.id,
          display_name: person.display_name,
          class_name: person.class_name,
          roles: person.roles,
          has_account: Boolean(account),
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
                        onClick={() => onPreview(displayValue(selectedUser.id))}
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

export default function Home() {
  useTelegramLayout();
  useEffect(() => {
    if (!isLocalBuild) return;
    let dispose: (() => void) | undefined;
    let cancelled = false;
    void import('./spatial-qa').then(({ installSpatialQa }) => {
      if (!cancelled) dispose = installSpatialQa();
    });
    return () => {
      cancelled = true;
      dispose?.();
    };
  }, []);
  const [state, setState] = useState<LoadState>('loading');
  const [session, setSession] = useState<Session | null>(null);
  const [data, setData] = useState<ApiData | null>(null);
  const [teacherData, setTeacherData] = useState<TeacherData | null>(null);
  const [section, setSection] = useState<
    'student' | 'teacher' | 'admin' | 'student-preview'
  >('student');
  const [previewTarget, setPreviewTarget] = useState('');
  const [authKey, setAuthKey] = useState(0);
  const desiredRole =
    typeof window !== 'undefined'
      ? (() => {
          const role = new URLSearchParams(window.location.search).get('role');
          return role === 'admin' || role === 'teacher' ? role : 'student';
        })()
      : 'student';
  const loadStudentData = useCallback(async () => {
    const today = isoDate(new Date());
    const end = isoDate(addDays(new Date(), 6));
    const [day, schedule, homework, profile] = await Promise.all([
      api<ApiData['today']>(`/api/student/today?day=${today}`),
      api<{ items: Lesson[] }>(
        `/api/student/schedule?start_day=${today}&end_day=${end}`,
      ),
      api<{ items: Homework[] }>('/api/student/homework'),
      api<Profile>('/api/student/profile'),
    ]);
    return {
      today: day,
      schedule: schedule.items,
      homework: homework.items,
      profile,
    };
  }, []);
  const loadTeacherData = useCallback(async () => {
    const today = isoDate(new Date());
    const end = isoDate(addDays(new Date(), 6));
    const [home, schedule, courses, profile] = await Promise.all([
      api<{ schedule: Lesson[]; groups: Group[]; information: Information[] }>(
        '/api/teacher/home',
      ),
      api<{ items: Lesson[] }>(
        `/api/teacher/schedule?start_day=${today}&end_day=${end}`,
      ),
      api<{ items: Array<Record<string, unknown>> }>('/api/teacher/courses'),
      api<Profile & { state: string }>('/api/teacher/profile'),
    ]);
    const next = {
      today: home.schedule,
      groups: home.groups,
      information: home.information,
      schedule: schedule.items,
      courses: courses.items,
      profile: profile.user ? profile : null,
    };
    setTeacherData(next);
    return next;
  }, []);
  const load = useCallback(async () => {
    setState('loading');
    const telegram = await telegramInitData();
    if (telegram.kind !== 'ready') {
      setState(telegram.kind);
      return;
    }
    activeTelegramInitData = telegram.value;
    activeStudentPreviewId = '';
    try {
      const next = await api<Session>('/api/auth/session', {
        method: 'POST',
        body: JSON.stringify({
          role: desiredRole,
          init_data: telegram.value || null,
        }),
      });
      setSession(next);
      if (next.state !== 'approved') {
        setState('needs_identity');
        return;
      }
      const role = next.user.role;
      setSection(
        role === 'admin' ? 'admin' : role === 'teacher' ? 'teacher' : 'student',
      );
      if (role === 'admin') {
        setState('approved');
        return;
      }
      if (role === 'teacher') {
        await loadTeacherData();
        setState('approved');
        return;
      }
      setData(await loadStudentData());
      setState('approved');
    } catch (error) {
      setState(error instanceof ApiError ? error.kind : 'api_error');
    }
  }, [desiredRole, loadStudentData, loadTeacherData]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [authKey, load]);
  const startPreview = async (userId: string) => {
    const preview = await api<{ id: string; target: { display_name: string } }>(
      '/api/admin/student-previews',
      { method: 'POST', body: JSON.stringify({ target_user_id: userId }) },
    );
    activeStudentPreviewId = preview.id;
    setPreviewTarget(preview.target.display_name);
    setData(await loadStudentData());
    setSection('student-preview');
  };
  const exitPreview = async () => {
    if (activeStudentPreviewId)
      await api(`/api/admin/student-previews/${activeStudentPreviewId}/end`, {
        method: 'POST',
      }).catch(() => undefined);
    activeStudentPreviewId = '';
    setPreviewTarget('');
    setSection('admin');
    setData(null);
  };
  const openTeacher = async () => {
    setState('loading');
    try {
      if (!teacherData) await loadTeacherData();
      setSection('teacher');
      setState('approved');
    } catch (error) {
      setState(error instanceof ApiError ? error.kind : 'api_error');
    }
  };
  if (state === 'loading')
    return (
      <main className="status-screen status-screen--island">
        <div className="loading-orb">
          <LoaderCircle className="spin" size={28} />
          <span>Открываем остров…</span>
        </div>
      </main>
    );
  if (
    state === 'approved' &&
    section === 'admin' &&
    session?.user.roles?.includes('admin')
  )
    return (
      <AdminApp
        onPreview={(id) => void startPreview(id)}
        onBackToTeacher={
          session.user.roles.includes('teacher')
            ? () => void openTeacher()
            : undefined
        }
        onLogout={() => setState('api_error')}
      />
    );
  if (state === 'approved' && section === 'student-preview' && data)
    return (
      <div className="preview-mode">
        <div className="preview-banner">
          <span>
            <ShieldCheck size={16} /> Режим просмотра:{' '}
            <strong>{previewTarget}</strong> · только чтение
          </span>
          <button onClick={() => void exitPreview()}>Выйти</button>
        </div>
        <StudentApp
          data={data}
          onReload={() => void loadStudentData().then(setData)}
        />
      </div>
    );
  if (state === 'approved' && section === 'teacher' && teacherData)
    return (
      <TeacherApp
        data={teacherData}
        onOpenAdmin={
          session?.user.roles?.includes('admin')
            ? () => setSection('admin')
            : undefined
        }
        onReload={() => setAuthKey((value) => value + 1)}
        onLogout={() => setState('api_error')}
      />
    );
  if (state !== 'approved' || !data)
    return (
      <PendingState
        state={state === 'approved' ? 'api_error' : state}
        onRetry={() => setAuthKey((value) => value + 1)}
      />
    );
  return (
    <StudentApp data={data} onReload={() => setAuthKey((value) => value + 1)} />
  );
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData?: string;
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
