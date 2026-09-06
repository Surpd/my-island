'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  BarChart3, BookOpen, CalendarDays, Check, ChevronDown, ChevronLeft,
  ChevronRight, CircleAlert, Clock3, ExternalLink, FileText, LoaderCircle,
  LockKeyhole, LogOut, Megaphone, Moon, RefreshCw, ShieldCheck, Sparkles, Sun,
  UserRound, Users, X,
} from 'lucide-react';
import { islandLocations, type IslandLocationId } from './island-config';

type View = IslandLocationId | 'profile' | null;
type LoadState = 'loading' | 'approved' | 'needs_identity' | 'auth_error';

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

type Group = {
  id?: string;
  name: string;
  member_role?: string | null;
  source?: string | null;
  scope_kind?: string | null;
  schedule_scopes?: Array<{ audience: string; subject?: string; subject_subgroup?: string; exam_track?: string }>;
};

type Profile = { user: { display_name: string; class_name?: string | null }; groups: Group[] };
type Session = { mode: string; state: string; user: { id: string | number; role: string; identity_id?: string | null } };
type ApiData = { today: { schedule: Lesson[]; homework: Homework[]; announcements: Array<{ title: string; body?: string; expires_at?: string }> }; schedule: Lesson[]; homework: Homework[]; profile: Profile };
type AdminData = { claims: Array<Record<string, string>>; users: Array<Record<string, unknown>>; scheduleSyncs: Array<Record<string, unknown>>; classroomSyncs: Array<Record<string, unknown>>; parseIssues: Array<Record<string, unknown>> };

const runtimeEnv = (import.meta as ImportMeta & { env?: Record<string, string | boolean> }).env || {};
const API_BASE = String(runtimeEnv.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const isLocalBuild = runtimeEnv.DEV === true || runtimeEnv.MODE === 'development';

function telegramInitData(): string {
  const telegram = typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined;
  telegram?.ready?.();
  return telegram?.initData || '';
}

function devHeader(): string | undefined {
  if (!isLocalBuild || typeof window === 'undefined') return undefined;
  const role = new URLSearchParams(window.location.search).get('role');
  return role === 'admin' ? 'admin:1' : role === 'teacher' ? 'teacher:1' : 'student:1';
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body) headers.set('Content-Type', 'application/json');
  const localAuth = devHeader();
  if (localAuth) headers.set('X-Dev-Auth', localAuth);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const body = await response.json().catch(() => null) as { detail?: unknown } | null;
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `Запрос не выполнен (${response.status})`);
  return body as T;
}

function isoDate(value: Date): string { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`; }
function addDays(value: Date, days: number): Date { const copy = new Date(value); copy.setDate(copy.getDate() + days); return copy; }
function weekStart(value = new Date()): Date { const day = (value.getDay() + 6) % 7; return addDays(new Date(value.getFullYear(), value.getMonth(), value.getDate()), -day); }
function formatDate(value: string, long = false): string { const parsed = new Date(`${value}T12:00:00`); return new Intl.DateTimeFormat('ru-RU', long ? { weekday: 'long', day: 'numeric', month: 'long' } : { day: 'numeric', month: 'short' }).format(parsed); }
function formatDue(value?: string | null): string { if (!value) return 'Срок не указан'; const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(parsed); }
function humanScope(group: Group): string { if (group.scope_kind === 'subject_subgroup') return 'Предметная группа'; if (group.scope_kind === 'exam_track') return 'Экзаменационный трек'; return 'Класс'; }
function scopeMarker(lesson: Lesson): string { if (lesson.exam_track) return lesson.exam_track; return lesson.subject_subgroup ? `группа ${lesson.subject_subgroup}` : ''; }
function displayValue(value: unknown, fallback = ''): string { return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback; }

function IconForLocation({ id }: { id: IslandLocationId }) { const Icon = { schedule: CalendarDays, homework: BookOpen, grades: BarChart3, information: Megaphone }[id]; return <Icon size={18} strokeWidth={2.2} />; }
function LoadingState({ label }: { label: string }) { return <div className="loading-state"><LoaderCircle className="spin" size={23} /><span>{label}</span></div>; }
function EmptyState({ icon: Icon, title, detail }: { icon: typeof FileText; title: string; detail: string }) { return <div className="empty-state"><span className="empty-icon"><Icon size={20} /></span><strong>{title}</strong><p>{detail}</p></div>; }

function LessonRow({ lesson }: { lesson: Lesson }) {
  const marker = scopeMarker(lesson);
  return <div className="lesson-card"><time>{lesson.start_time}</time><div className="lesson-card__body"><strong>{lesson.subject}</strong><span>{[lesson.room, lesson.teacher].filter(Boolean).join(' · ') || 'Детали уточняются'}</span>{marker && <small>{marker}</small>}</div>{lesson.lesson_type && lesson.lesson_type !== 'lesson' && <span className="soft-badge">Особое</span>}</div>;
}

function SchedulePanel({ schedule, selectedDay, setSelectedDay }: { schedule: Lesson[]; selectedDay: string; setSelectedDay: (value: string) => void }) {
  const monday = weekStart(new Date(`${selectedDay}T12:00:00`));
  const days = Array.from({ length: 5 }, (_, index) => isoDate(addDays(monday, index)));
  const entries = schedule.filter((lesson) => lesson.lesson_date === selectedDay).sort((a, b) => a.start_time.localeCompare(b.start_time));
  return <div className="section-panel"><div className="panel-intro"><span className="panel-icon"><CalendarDays size={21} /></span><div><p className="eyebrow">Башня времени</p><h2>Расписание</h2><p>Только ваши уроки и предметные группы из подтверждённых доступов.</p></div></div><div className="day-picker" aria-label="Дни недели">{days.map((day) => <button key={day} className={day === selectedDay ? 'is-active' : ''} onClick={() => setSelectedDay(day)}><small>{new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(new Date(`${day}T12:00:00`))}</small><strong>{day.slice(8)}</strong></button>)}</div><div className="panel-date"><strong>{formatDate(selectedDay, true)}</strong><span>{entries.length} уроков</span></div>{entries.length ? <div className="lesson-stack">{entries.map((lesson) => <LessonRow key={`${lesson.lesson_date}-${lesson.start_time}-${lesson.subject}-${lesson.source_coordinate}`} lesson={lesson} />)}</div> : <EmptyState icon={CalendarDays} title="Уроков нет" detail="На этот день в вашем расписании нет записей." />}</div>;
}

function HomeworkPanel({ homework }: { homework: Homework[] }) {
  return <div className="section-panel"><div className="panel-intro"><span className="panel-icon panel-icon--coral"><BookOpen size={21} /></span><div><p className="eyebrow">Дом знаний</p><h2>Домашка</h2><p>Задания из Classroom. Статус показывается только когда его вернул источник.</p></div></div>{homework.length ? <div className="homework-stack">{homework.map((item) => <article className="homework-card" key={item.external_coursework_id}><div className="homework-card__top"><span className="subject-icon"><FileText size={17} /></span><span className="course-label">{item.course_title || 'Classroom'}</span>{item.state && <span className="state-badge">{item.state === 'PUBLISHED' ? 'Опубликовано' : item.state}</span>}</div><h3>{item.title}</h3>{item.description && <p>{item.description}</p>}<div className="homework-card__bottom"><span><Clock3 size={15} /> {formatDue(item.due_at)}</span>{item.alternate_link && <a href={item.alternate_link} target="_blank" rel="noreferrer">Открыть <ExternalLink size={14} /></a>}</div></article>)}</div> : <EmptyState icon={BookOpen} title="Домашних заданий нет" detail="Новые задания появятся здесь после синхронизации Classroom." />}</div>;
}

function InfoPanel({ announcements }: { announcements: ApiData['today']['announcements'] }) { return <div className="section-panel"><div className="panel-intro"><span className="panel-icon panel-icon--gold"><Megaphone size={21} /></span><div><p className="eyebrow">Павильон информации</p><h2>Новости</h2><p>Важные сообщения для вашего учебного контура.</p></div></div>{announcements.length ? <div className="announcement-stack">{announcements.map((item) => <article className="announcement-card" key={item.title}><span className="announcement-card__icon">✦</span><div><h3>{item.title}</h3><p>{item.body || 'Подробности появятся в сообщении.'}</p>{item.expires_at && <small>Актуально до {formatDue(item.expires_at)}</small>}</div></article>)}</div> : <EmptyState icon={Megaphone} title="Пока тихо" detail="Активных объявлений для вашей группы нет." />}</div>; }

function ProfilePanel({ profile }: { profile: Profile }) { return <div className="section-panel"><div className="panel-intro"><span className="panel-icon panel-icon--gold"><UserRound size={21} /></span><div><p className="eyebrow">Профиль и доступ</p><h2>{profile.user.display_name}</h2><p>{profile.user.class_name || 'Учебная группа не указана'} · ученик</p></div></div><div className="verified-card"><ShieldCheck size={19} /><div><strong>Личность подтверждена</strong><span>Доступ ограничен вашими группами и курсами.</span></div><Check size={18} /></div><div className="profile-section"><div className="subsection-title"><Users size={16} /> Мои группы</div>{profile.groups.map((group) => <div className="group-card" key={`${group.id || group.name}-${group.scope_kind}`}><div><strong>{group.name}</strong><span>{humanScope(group)}{group.source ? ` · ${group.source === 'admin_override' ? 'подтверждено администратором' : 'источник школы'}` : ''}</span></div>{group.schedule_scopes?.map((scope) => <small key={`${scope.audience}-${scope.subject}-${scope.subject_subgroup}-${scope.exam_track}`}>{[scope.audience, scope.subject_subgroup && `группа ${scope.subject_subgroup}`, scope.exam_track].filter(Boolean).join(' · ')}</small>)}</div>)}</div></div>; }

function GradesPanel({ grades, loading }: { grades: Array<Record<string, unknown>> | null; loading: boolean }) { return <div className="section-panel"><div className="panel-intro"><span className="panel-icon panel-icon--violet"><BarChart3 size={21} /></span><div><p className="eyebrow">Обсерватория</p><h2>Оценки</h2><p>Официальный журнал хранится отдельно от Classroom.</p></div></div>{loading ? <LoadingState label="Проверяем журнал" /> : grades?.length ? <div className="grades-stack">{grades.map((grade, index) => <div className="grade-card" key={`${displayValue(grade.subject, 'grade')}-${index}`}><div><strong>{displayValue(grade.subject, 'Предмет')}</strong><span>{displayValue(grade.graded_on)}</span></div><b>{displayValue(grade.value, '—')}</b></div>)}</div> : <EmptyState icon={BarChart3} title="Официальных оценок пока нет" detail="Источник школьного журнала ещё не подключён к этому кабинету." />}</div>; }

function TodaySheet({ today, onOpen }: { today: ApiData['today']; onOpen: (view: View) => void }) { const lessons = [...today.schedule].sort((a, b) => a.start_time.localeCompare(b.start_time)); const next = lessons[0]; const homework = [...today.homework].sort((a, b) => String(a.due_at || '').localeCompare(String(b.due_at || '')))[0]; return <section className="today-sheet" aria-label="Сегодня"><div className="today-grabber" /><div className="today-heading"><div><span className="eyebrow">{formatDate(isoDate(new Date()), true)}</span><h2>Сегодня</h2></div><button className="outline-button" onClick={() => onOpen('schedule')}>Расписание <ChevronRight size={15} /></button></div><div className="today-grid"><button className="today-card today-card--lesson" onClick={() => onOpen('schedule')}><CalendarDays size={18} /><span><small>{next ? `Ближайший урок · ${next.start_time}` : 'Расписание'}</small><strong>{next?.subject || 'На сегодня уроков нет'}</strong><em>{next ? [next.room, scopeMarker(next)].filter(Boolean).join(' · ') : 'Отличный день для отдыха'}</em></span></button><button className="today-card today-card--homework" onClick={() => onOpen('homework')}><BookOpen size={18} /><span><small>На контроле</small><strong>{homework?.title || 'Домашних заданий нет'}</strong><em>{homework ? formatDue(homework.due_at) : 'Всё спокойно'}</em></span></button></div>{today.announcements.length > 0 && <button className="today-notice" onClick={() => onOpen('information')}><Megaphone size={16} /><span><strong>{today.announcements[0].title}</strong><small>Открыть новости</small></span><ChevronRight size={16} /></button>}</section>; }

function StudentApp({ data, onReload }: { data: ApiData; onReload: () => void }) {
  const [view, setView] = useState<View>(null); const [night, setNight] = useState(false); const [selectedDay, setSelectedDay] = useState([...data.today.schedule, ...data.schedule].sort((a, b) => `${a.lesson_date}${a.start_time}`.localeCompare(`${b.lesson_date}${b.start_time}`))[0]?.lesson_date || isoDate(new Date())); const [grades, setGrades] = useState<Array<Record<string, unknown>> | null>(null); const [gradesLoading, setGradesLoading] = useState(false);
  const loadGrades = async () => { if (grades !== null || gradesLoading) return; setGradesLoading(true); try { setGrades((await api<{ items: Array<Record<string, unknown>> }>('/api/student/grades')).items); } catch { setGrades([]); } finally { setGradesLoading(false); } };
  const openView = (nextView: View) => { setView(nextView); if (nextView === 'grades') void loadGrades(); };
  const panelTitle = view === 'profile' ? 'Профиль' : view ? ({ schedule: 'Расписание', homework: 'Домашка', grades: 'Оценки', information: 'Новости' }[view]) : '';
  return <main className={`island-app ${night ? 'island-app--night' : ''}`}><div className="island-backdrop" aria-hidden="true" /><div className="island-vignette" aria-hidden="true" /><header className="topbar"><div className="brand-lockup"><span className="brand-mark">✦</span><div><p className="brand-title">Мой Остров</p><p className="brand-subtitle">личный кабинет · 9 класс</p></div></div><div className="topbar-actions"><button className="icon-button" onClick={() => setNight((value) => !value)} aria-label={night ? 'Включить день' : 'Включить ночь'}>{night ? <Sun size={18} /> : <Moon size={18} />}</button><button className="profile-button" onClick={() => openView('profile')} aria-label="Открыть профиль"><span className="profile-avatar">{data.profile.user.display_name.slice(0, 1)}</span><span className="profile-name">{data.profile.user.display_name.split(' ')[0]}</span><ChevronDown size={15} /></button></div></header><section className="island-stage"><div className="stage-heading"><div><p className="section-kicker"><Sparkles size={14} /> {formatDate(isoDate(new Date()), true)}</p><h1>Добро пожаловать,<br />{data.profile.user.display_name.split(' ')[0]}</h1><p className="stage-note">Остров — быстрый путь к тому, что важно сегодня.</p></div><div className="sync-chip"><span className="status-dot" /> Данные синхронизированы</div></div><div className="map-frame" aria-label="Остров навигации"><div className="map-art" />{islandLocations.map((location) => <button key={location.id} className={`hotspot hotspot--${location.id}`} style={{ left: `${location.x}%`, top: `${location.y}%` }} onClick={() => openView(location.id)} aria-label={`Открыть ${location.label}`}><span className="hotspot-pin"><IconForLocation id={location.id} /></span><span className="hotspot-label">{location.label}</span></button>)}<span className="world-label world-label--plaza">Площадь событий</span><span className="world-label world-label--lighthouse">Маяк</span><div className="map-compass" aria-hidden="true"><span>N</span><span className="compass-line" /></div></div></section><TodaySheet today={data.today} onOpen={openView} />{view && <dialog open className="panel-layer" aria-label={panelTitle}><button className="panel-scrim" onClick={() => setView(null)} aria-label="Закрыть" /><aside className="detail-panel"><div className="panel-toolbar"><button className="back-button" onClick={() => setView(null)}><ChevronLeft size={18} /> Остров</button><button className="panel-close" onClick={() => setView(null)} aria-label="Закрыть"><X size={18} /></button></div>{view === 'profile' ? <ProfilePanel profile={data.profile} /> : view === 'schedule' ? <SchedulePanel schedule={data.schedule} selectedDay={selectedDay} setSelectedDay={setSelectedDay} /> : view === 'homework' ? <HomeworkPanel homework={data.homework} /> : view === 'information' ? <InfoPanel announcements={data.today.announcements} /> : <GradesPanel grades={grades} loading={gradesLoading} />}<button className="panel-refresh" onClick={onReload}><RefreshCw size={15} /> Обновить данные</button></aside></dialog>}</main>;
}

function PendingState({ state, onRetry }: { state: 'needs_identity' | 'auth_error'; onRetry: () => void }) { const pending = state === 'needs_identity'; return <main className="status-screen"><div className="status-card"><span className="status-illustration">{pending ? <LockKeyhole size={28} /> : <CircleAlert size={28} />}</span><p className="eyebrow">Мой Остров · 9 класс</p><h1>{pending ? 'Доступ ещё не подтверждён' : 'Не удалось войти'}</h1><p>{pending ? 'Telegram распознан, но личность ученика ещё должна быть подтверждена администратором школы. Пока персональные данные закрыты.' : 'Проверьте, что приложение открыто внутри Telegram, и повторите попытку.'}</p><button className="primary-button" onClick={onRetry}>{pending ? 'Проверить снова' : 'Повторить вход'} <RefreshCw size={17} /></button>{isLocalBuild && <small className="dev-note">Локальная проверка: API {API_BASE}</small>}</div></main>; }

function SyncSummary({ item, empty }: { item?: Record<string, unknown>; empty: string }) { if (!item) return <p className="sync-summary sync-summary--empty">{empty}</p>; const hasError = typeof item.error === 'string' || typeof item.error === 'number'; return <div className="sync-summary"><span className={item.status === 'success' ? 'sync-dot sync-dot--ok' : 'sync-dot sync-dot--bad'} /><div><strong>{displayValue(item.status, 'unknown')}</strong><small>{displayValue(item.created_at)}</small>{hasError && <small>{displayValue(item.error)}</small>}</div></div>; }

function AdminApp({ onLogout }: { onLogout: () => void }) {
  const [data, setData] = useState<AdminData | null>(null); const [loading, setLoading] = useState(true); const [message, setMessage] = useState('');
  const load = useCallback(async () => { setLoading(true); try { const [claims, users, syncs, classroomSyncs, parseIssues] = await Promise.all([api<{ items: Array<Record<string, string>> }>('/api/admin/claims'), api<{ items: Array<Record<string, unknown>> }>('/api/admin/users'), api<{ items: Array<Record<string, unknown>> }>('/api/admin/schedule/syncs'), api<{ items: Array<Record<string, unknown>> }>('/api/admin/classroom/syncs'), api<{ items: Array<Record<string, unknown>> }>('/api/admin/schedule/parses')]); setData({ claims: claims.items, users: users.items, scheduleSyncs: syncs.items, classroomSyncs: classroomSyncs.items, parseIssues: parseIssues.items }); } catch (error) { setMessage(error instanceof Error ? error.message : 'Ошибка загрузки'); } finally { setLoading(false); } }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  const review = async (id: string, status: 'approved' | 'rejected') => { setMessage('Сохраняем решение…'); try { await api(`/api/admin/claims/${id}/review`, { method: 'POST', body: JSON.stringify({ status }) }); setMessage(status === 'approved' ? 'Заявка одобрена' : 'Заявка отклонена'); await load(); } catch (error) { setMessage(error instanceof Error ? error.message : 'Не удалось сохранить решение'); } };
  const refresh = async (kind: 'schedule' | 'classroom') => { setMessage('Запускаем обновление snapshot…'); try { await api(`/api/admin/${kind}/refresh`, { method: 'POST' }); setMessage(`${kind === 'schedule' ? 'Расписание' : 'Classroom'} обновлено`); await load(); } catch (error) { setMessage(error instanceof Error ? error.message : 'Синхронизация не выполнена'); } };
  return <main className="admin-app"><header className="admin-header"><div><p className="eyebrow">Мой Остров · эксплуатация</p><h1>Админ-центр</h1></div><div><button className="outline-button" onClick={() => void load()}><RefreshCw size={15} /> Обновить</button><button className="icon-button icon-button--dark" onClick={onLogout} aria-label="Выйти"><LogOut size={17} /></button></div></header>{message && <div className="admin-message"><CircleAlert size={16} /> {message}</div>}{loading ? <LoadingState label="Загружаем состояние проекта" /> : data && <div className="admin-grid"><section className="admin-card admin-card--wide"><div className="admin-card-heading"><div><span className="admin-icon"><ShieldCheck size={18} /></span><h2>Заявки на identity</h2></div><span className="count-badge">{data.claims.length}</span></div>{data.claims.length ? data.claims.map((claim) => <div className="claim-row" key={claim.id}><div><strong>{claim.display_name || 'Без имени'}</strong><span>{claim.class_name || 'Класс не указан'} · {claim.requested_role}</span></div><div className="row-actions"><button className="success-button" onClick={() => void review(claim.id, 'approved')}><Check size={15} /> Одобрить</button><button className="danger-button" onClick={() => void review(claim.id, 'rejected')}><X size={15} /> Отклонить</button></div></div>) : <EmptyState icon={ShieldCheck} title="Новых заявок нет" detail="Подтверждённые identity остаются в базе с audit trail." />}</section><section className="admin-card"><div className="admin-card-heading"><div><span className="admin-icon admin-icon--blue"><Users size={18} /></span><h2>Пользователи</h2></div><span className="count-badge">{data.users.length}</span></div><div className="admin-stat-list">{data.users.slice(0, 8).map((user, index) => <div key={displayValue(user.id, String(index))}><strong>{displayValue(user.display_name, 'Telegram user')}</strong><span>{displayValue(user.role)} · {Array.isArray(user.groups) ? `${user.groups.length} групп` : 'группы не указаны'}</span></div>)}</div></section><section className="admin-card"><div className="admin-card-heading"><div><span className="admin-icon admin-icon--gold"><CalendarDays size={18} /></span><h2>Расписание</h2></div></div><button className="primary-button primary-button--small" onClick={() => void refresh('schedule')}><RefreshCw size={15} /> Обновить snapshot</button><SyncSummary item={data.scheduleSyncs[0]} empty="Синхронизаций ещё нет" /></section><section className="admin-card"><div className="admin-card-heading"><div><span className="admin-icon admin-icon--coral"><BookOpen size={18} /></span><h2>Classroom</h2></div></div><button className="primary-button primary-button--small" onClick={() => void refresh('classroom')}><RefreshCw size={15} /> Обновить курс</button><SyncSummary item={data.classroomSyncs[0]} empty="Синхронизаций ещё нет" /></section><section className="admin-card admin-card--wide"><div className="admin-card-heading"><div><span className="admin-icon admin-icon--violet"><CircleAlert size={18} /></span><h2>Неоднозначные parse entries</h2></div><span className="count-badge">{data.parseIssues.length}</span></div>{data.parseIssues.length ? <div className="issue-list">{data.parseIssues.slice(0, 12).map((issue, index) => <div key={`${displayValue(issue.source_coordinate, String(index))}-${index}`}><strong>{displayValue(issue.lesson_date)} · {displayValue(issue.start_time)} · {displayValue(issue.subject)}</strong><span>{displayValue(issue.audience)} · {displayValue(issue.parse_status)} · {displayValue(issue.source_coordinate)}</span></div>)}</div> : <EmptyState icon={CircleAlert} title="Проблем парсинга нет" detail="Неполные и неоднозначные записи появятся здесь, не попадая в student UI." />}</section></div>}<p className="admin-footer"><LockKeyhole size={14} /> Все действия дополнительно защищены backend role authorization.</p></main>;
}

export default function Home() {
  const [state, setState] = useState<LoadState>('loading'); const [session, setSession] = useState<Session | null>(null); const [data, setData] = useState<ApiData | null>(null); const [authKey, setAuthKey] = useState(0);
  const desiredRole = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('role') === 'teacher' ? 'teacher' : 'student';
  const load = useCallback(async () => { setState('loading'); const initData = telegramInitData(); if (!initData && !devHeader()) { setState('auth_error'); return; } try { const nextSession = await api<Session>('/api/auth/session', { method: 'POST', body: JSON.stringify({ role: desiredRole, init_data: initData || null }) }); setSession(nextSession); if (nextSession.state !== 'approved') { setState('needs_identity'); return; } const todayDate = isoDate(new Date()); const start = todayDate; const end = isoDate(addDays(new Date(), 6)); const [today, schedule, homework, profile] = await Promise.all([api<ApiData['today']>(`/api/student/today?day=${todayDate}`), api<{ items: Lesson[] }>(`/api/student/schedule?start_day=${start}&end_day=${end}`), api<{ items: Homework[] }>('/api/student/homework'), api<Profile>('/api/student/profile')]); setData({ today, schedule: schedule.items, homework: homework.items, profile }); setState('approved'); } catch { setState('auth_error'); } }, [desiredRole]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [authKey, load]);
  if (state === 'loading') return <main className="status-screen status-screen--island"><div className="loading-orb"><LoaderCircle className="spin" size={28} /><span>Открываем остров…</span></div></main>;
  if (state !== 'approved' || !data) return <PendingState state={state === 'needs_identity' ? 'needs_identity' : 'auth_error'} onRetry={() => setAuthKey((value) => value + 1)} />;
  if (session?.user.role === 'admin') return <AdminApp onLogout={() => { setSession(null); setData(null); setState('auth_error'); }} />;
  return <StudentApp data={data} onReload={() => setAuthKey((value) => value + 1)} />;
}

declare global { interface Window { Telegram?: { WebApp?: { initData?: string; ready?: () => void } } } }
