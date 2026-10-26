'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, ChevronLeft, ChevronRight, Clock3, MapPin, RefreshCw, Users } from 'lucide-react';
import './mobile-published-schedule.css';

export type MobileScheduleApi = <T>(path: string, options?: RequestInit) => Promise<T>;

type ScheduleAssignment = {
  activity?: string;
  assignment_kind?: string;
  teacher_ids?: Array<string | number>;
  canonical_group_ids?: Array<string | number>;
  audience?: { type?: string; label?: string; groups?: string[]; canonical_group_ids?: Array<string | number> };
  title?: string;
  teachers?: string[];
  room?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
};

type PublishedBlock = {
  block_key?: string;
  change_kind?: string;
  patch?: Record<string, unknown> | null;
  assignments?: ScheduleAssignment[];
  canonical_block_id?: number | string | null;
  weekday?: number | null;
  start_time?: string | null;
  end_time?: string | null;
  grade_scope?: string | number | null;
  status?: string | null;
  mode?: string | null;
  unresolved?: unknown[];
};

type Overview = {
  status?: string;
  effective_week?: { effective_week_id?: string; week_start?: string; status?: string } | null;
};

type LessonResponse = { status?: string; published?: boolean; effective_week_id?: string | null; items?: PublishedBlock[] };

const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

function mondayOf(value: string): string {
  const date = new Date(`${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return mondayOf(isoDate(new Date()));
  date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  return isoDate(date);
}

function dateForWeekday(monday: string, weekday: number | null | undefined): string {
  const date = new Date(`${monday}T12:00:00`);
  date.setDate(date.getDate() + (Number.isInteger(weekday) ? Number(weekday) : 0));
  return isoDate(date);
}

function dateLabel(value: string, options: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'long' }): string {
  return new Intl.DateTimeFormat('ru-RU', options).format(new Date(`${value}T12:00:00`));
}

function plain(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value).trim();
  return '';
}

function listText(value: unknown): string {
  if (!Array.isArray(value)) return '';
  return value.map(plain).filter(Boolean).join(', ');
}

function blockAssignments(block: PublishedBlock): ScheduleAssignment[] {
  const patch = block.patch && typeof block.patch === 'object' ? block.patch : {};
  const assignments = Array.isArray(block.assignments) ? block.assignments : patch.assignments;
  return Array.isArray(assignments) ? assignments.filter((item): item is ScheduleAssignment => Boolean(item && typeof item === 'object')) : [];
}

function assignmentName(assignment: ScheduleAssignment, block: PublishedBlock): string {
  const metadata = assignment.metadata || {};
  return plain(assignment.activity) || plain(metadata.activity) || plain(metadata.title) || plain(metadata.label)
    || plain(assignment.title) || plain((block.patch || {}).title) || plain(block.block_key) || 'Опубликованный блок';
}

function assignmentAudience(assignment: ScheduleAssignment, block: PublishedBlock): string {
  const audience = assignment.audience || {};
  const metadata = assignment.metadata || {};
  return plain(audience.label) || plain(metadata.audience_label) || plain(assignment.audience_label)
    || listText(audience.groups)
    || (plain(block.grade_scope) ? `${plain(block.grade_scope)} класс` : 'Группа не указана');
}

function assignmentTeacher(assignment: ScheduleAssignment): string {
  const metadata = assignment.metadata || {};
  return listText(assignment.teachers) || listText(metadata.teachers) || listText(metadata.teacher_names) || plain(metadata.teacher_name)
    || listText(assignment.teacher_names) || listText(assignment.teacher_ids);
}

function assignmentRoom(assignment: ScheduleAssignment): string {
  const metadata = assignment.metadata || {};
  return plain(assignment.room) || plain(metadata.room) || plain(metadata.room_name);
}

function stateMessage(status: string | undefined, overview: Overview | null): string {
  if (status === 'canonical_backend_disabled' || overview?.status === 'canonical_backend_disabled') return 'Источник опубликованного расписания отключён.';
  if (status === 'canonical_not_imported' || overview?.status === 'canonical_not_imported') return 'Каноническое расписание ещё не загружено.';
  if (status === 'not_published') return 'На выбранную неделю нет действующей публикации расписания.';
  if (status === 'canonical_not_materialized' || !overview?.effective_week) return 'Для этой недели опубликованное расписание недоступно.';
  return '';
}

export function MobilePublishedSchedule({ api }: { api: MobileScheduleApi }) {
  const [weekStart, setWeekStart] = useState(() => mondayOf(isoDate(new Date())));
  const [overview, setOverview] = useState<Overview | null>(null);
  const [response, setResponse] = useState<LessonResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  const load = useCallback(async (signal: AbortSignal) => {
    const query = `?week_start=${encodeURIComponent(weekStart)}`;
    try {
      const [status, lessons] = await Promise.all([
        api<Overview>(`/api/admin/schedule/overview${query}`, { signal }),
        api<LessonResponse>(`/api/admin/schedule/published-view${query}`, { signal }),
      ]);
      if (!signal.aborted) {
        setOverview(status || {});
        setResponse(lessons || {});
      }
    } catch (cause) {
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить опубликованное расписание.');
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [api, weekStart]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    return () => controller.abort();
  }, [load, refresh]);

  const statusText = response?.published === false
    ? 'На выбранную неделю нет действующей публикации расписания.'
    : stateMessage(response?.status, overview);
  const blocks = useMemo(() => (response?.items || []).filter((block) => block && typeof block === 'object')
    .map((block) => ({ block, date: dateForWeekday(weekStart, block.weekday) }))
    .sort((a, b) => a.date.localeCompare(b.date) || plain(a.block.start_time).localeCompare(plain(b.block.start_time))), [response, weekStart]);
  const days = useMemo(() => Array.from({ length: 7 }, (_, weekday) => {
    const dayDate = dateForWeekday(weekStart, weekday);
    return { date: dayDate, blocks: blocks.filter((item) => item.date === dayDate) };
  }), [blocks, weekStart]);

  const moveWeek = (amount: number) => {
    const date = new Date(`${weekStart}T12:00:00`);
    date.setDate(date.getDate() + amount * 7);
    setLoading(true); setError(''); setOverview(null); setResponse(null);
    setWeekStart(isoDate(date));
  };
  const refreshNow = () => { setLoading(true); setError(''); setOverview(null); setResponse(null); setRefresh((value) => value + 1); };

  return <section className="mobile-published-schedule campus-panel" aria-labelledby="mobile-published-schedule-title">
    <header className="mobile-published-schedule__header">
      <div><p className="campus-eyebrow">ШКОЛЬНОЕ РАСПИСАНИЕ</p><h2 id="mobile-published-schedule-title">Опубликованная неделя</h2></div>
      <button className="mobile-published-schedule__refresh" type="button" aria-label="Обновить расписание" onClick={refreshNow} disabled={loading}><RefreshCw size={16} className={loading ? 'is-spinning' : ''} /></button>
    </header>

    <div className="mobile-published-schedule__week-controls">
      <button type="button" aria-label="Предыдущая неделя" onClick={() => moveWeek(-1)}><ChevronLeft size={18} /></button>
      <label>Неделя с<input aria-label="Выбрать неделю" type="date" value={weekStart} onChange={(event) => { if (event.target.value) { setLoading(true); setError(''); setOverview(null); setResponse(null); setWeekStart(mondayOf(event.target.value)); } }} /></label>
      <button type="button" aria-label="Следующая неделя" onClick={() => moveWeek(1)}><ChevronRight size={18} /></button>
    </div>
    <p className="mobile-published-schedule__range">{dateLabel(weekStart)} — {dateLabel(dateForWeekday(weekStart, 6))}</p>

    {loading ? <p className="mobile-published-schedule__state" aria-live="polite">Загружаем опубликованное расписание…</p> : null}
    {!loading && error ? <div className="mobile-published-schedule__state mobile-published-schedule__state--error" role="alert"><AlertCircle size={17} /><span>{error}</span><button type="button" onClick={refreshNow}>Повторить</button></div> : null}
    {!loading && !error && statusText ? <p className="mobile-published-schedule__state">{statusText}</p> : null}
    {!loading && !error && !statusText && !blocks.length ? <p className="mobile-published-schedule__state">На эту неделю опубликованных занятий и событий нет.</p> : null}
    {!loading && !error && !statusText && blocks.length ? <div className="mobile-published-schedule__days">
      {days.filter((day) => day.blocks.length).map((day) => <section className="mobile-published-schedule__day" key={day.date}>
        <h3>{dateLabel(day.date, { weekday: 'long', day: 'numeric', month: 'long' })}</h3>
        {day.blocks.map(({ block }, index) => {
          const assignments = blockAssignments(block);
          const cancelled = block.change_kind === 'cancelled' || block.status === 'cancelled';
          return <article className={`mobile-published-schedule__block${cancelled ? ' is-cancelled' : ''}`} key={`${block.block_key || 'block'}:${index}`}>
            <div className="mobile-published-schedule__time"><Clock3 size={14} /><span>{block.start_time || 'Время не указано'}{block.end_time ? `–${block.end_time}` : ''}</span></div>
            <div className="mobile-published-schedule__lessons">
              {assignments.length ? assignments.map((assignment, assignmentIndex) => {
                const audience = assignmentAudience(assignment, block);
                const teacher = assignmentTeacher(assignment);
                const room = assignmentRoom(assignment);
                return <div className="mobile-published-schedule__assignment" key={`${assignmentName(assignment, block)}:${assignmentIndex}`}>
                  <strong>{assignmentName(assignment, block)}</strong>
                  {audience ? <span><Users size={13} />{audience}</span> : null}
                  {teacher ? <span>Учитель: {teacher}</span> : null}
                  {room ? <span><MapPin size={13} />{room}</span> : null}
                </div>;
              }) : <div className="mobile-published-schedule__assignment">
                <strong>{cancelled ? 'Занятие отменено' : plain((block.patch || {}).title) || plain(block.block_key) || 'Опубликованный блок'}</strong>
                <span>{block.grade_scope ? `${block.grade_scope} класс` : 'Детали назначения не переданы источником расписания'}</span>
              </div>}
            </div>
            {block.unresolved?.length ? <small className="mobile-published-schedule__notice">Есть замечания к данным блока</small> : null}
          </article>;
        })}
      </section>)}
    </div> : null}
  </section>;
}
