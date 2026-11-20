/** View adapters for the published canonical API. No resolution or membership logic lives here. */
import { normalizeRoomDisplay } from './schedule-rooms.ts';

type RawLesson = Record<string, unknown>;
export type CampusLesson = { lesson_date: string; start_time: string; end_time?: string; subject: string; teacher?: string; room?: string; audience?: string; lesson_type?: string; activity_format?: string; physical_presence?: boolean; connection_info?: string; responsible_teacher?: string };
type CampusItem = { date: string; slot: { start: string; end?: string }; state: 'lesson' | 'no_lesson' | 'conflict' | 'special_event'; canonical_title?: string; teacher?: string; room?: string; activity_format?: string; connection_info?: string; responsible_teacher?: string; no_lesson?: { reason?: 'break' }; conflict?: { state: string; label: string; alternatives: Array<{ title: string }> }; special_event?: { kind: string; title: string } };
export type CampusDay = { date: string; state: 'scheduled'; items: CampusItem[]; mode?: 'online' | 'sdep' };
const string = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
const list = (value: unknown) => Array.isArray(value) ? value.map(string).filter(Boolean).join(' · ') : string(value);
const rows = (value: unknown): RawLesson[] => Array.isArray(value) ? value.filter((row): row is RawLesson => Boolean(row) && typeof row === 'object') : [];
const isNoLesson = (row: RawLesson) => string(row.state).toUpperCase() === 'NO_LESSON';
const isConflict = (row: RawLesson) => ['UNRESOLVED', 'CONFLICT'].includes(string(row.state).toUpperCase());

export function campusLessons(value: unknown): CampusLesson[] {
  return rows(value).filter((row) => !isNoLesson(row)).map((row) => {
    const activityFormat = string(row.activity_format);
    const isPractice = activityFormat === 'independent_practice';
    return {
      lesson_date: string(row.lesson_date || row.date), start_time: string(row.start_time), end_time: string(row.end_time),
      subject: isConflict(row) ? 'Расписание уточняется' : (string(row.subject || row.activity || row.canonical_title || row.title) || 'Занятие') + (row.activity_type === 'consultation' ? ' · Консультация' : row.activity_type === 'laboratory' ? ' · Лаборатория' : ''),
      teacher: string(row.teacher) || list(row.teacher_names),
      responsible_teacher: isPractice ? list(row.responsible_teachers) : '',
      physical_presence: typeof row.physical_presence === 'boolean' ? row.physical_presence : undefined,
      connection_info: string(row.connection_info),
      activity_format: activityFormat || undefined,
      room: row.delivery_mode === 'online' ? 'Онлайн' : normalizeRoomDisplay(row.room),
      audience: string(row.audience) || list(row.audiences) || list(row.group_context),
      lesson_type: isConflict(row) ? 'conflict' : string(row.lesson_type || row.activity_kind),
    };
  });
}

export function campusStudentDays(value: unknown, dayModes: unknown = []): CampusDay[] {
  const days = new Map<string, CampusDay>();
  for (const mode of rows(dayModes)) {
    const date = string(mode.date);
    const kind = string(mode.mode);
    if (date && (kind === 'online' || kind === 'sdep')) days.set(date, { date, state: 'scheduled', items: [], mode: kind });
  }
  for (const row of rows(value)) {
    const date = string(row.lesson_date || row.date);
    if (!date) continue;
    const day = days.get(date) || { date, state: 'scheduled' as const, items: [] };
    const lesson = campusLessons([row])[0];
    const item: CampusItem = { date, slot: { start: string(row.start_time), end: string(row.end_time) }, state: 'lesson', canonical_title: row.activity_type === 'consultation' ? lesson?.subject + ' по желанию' : lesson?.subject, teacher: lesson?.teacher, connection_info: lesson?.connection_info, room: lesson?.room, activity_format: lesson?.activity_format, responsible_teacher: lesson?.responsible_teacher };
    if (isNoLesson(row)) { item.state = 'no_lesson'; }
    else if (isConflict(row)) { item.state = 'conflict'; item.conflict = { state: 'unresolved', label: 'Расписание уточняется', alternatives: rows(row.activities).map((activity) => ({ title: string(activity.activity) })) }; }
    else if (['event', 'special_event'].includes(string(row.activity_type || row.activity_kind || row.lesson_type))) { item.state = 'special_event'; item.special_event = { kind: string(row.activity_type || row.activity_kind || row.lesson_type), title: lesson?.subject || 'Событие' }; }
    if (item.conflict?.alternatives.length) {
      for (const alternative of item.conflict.alternatives) {
        day.items.push({ ...item, conflict: { state: 'unresolved', label: 'Пересечение обязательных занятий', alternatives: [alternative] } });
      }
    } else {
      day.items.push(item);
    }
    days.set(date, day);
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date)).map((day) => ({ ...day, items: day.items.sort((a, b) => a.slot.start.localeCompare(b.slot.start)) }));
}
