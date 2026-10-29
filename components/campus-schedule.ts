/** View adapters for the published canonical API. No resolution or membership logic lives here. */
type RawLesson = Record<string, unknown>;
export type CampusLesson = { lesson_date: string; start_time: string; end_time?: string; subject: string; teacher?: string; room?: string; audience?: string; lesson_type?: string; activity_format?: string; physical_presence?: boolean; responsible_teacher?: string };
type CampusItem = { date: string; slot: { start: string; end?: string }; state: 'lesson' | 'no_lesson' | 'conflict' | 'special_event'; canonical_title?: string; teacher?: string; room?: string; activity_format?: string; responsible_teacher?: string; no_lesson?: { reason?: 'break' }; conflict?: { state: string; label: string; alternatives: Array<{ title: string }> }; special_event?: { kind: string; title: string } };
export type CampusDay = { date: string; state: 'scheduled'; items: CampusItem[] };
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
      subject: isConflict(row) ? 'Расписание уточняется' : string(row.subject || row.activity || row.canonical_title || row.title) || 'Занятие',
      teacher: isPractice ? '' : string(row.teacher) || list(row.teacher_names),
      responsible_teacher: isPractice ? list(row.responsible_teachers) || list(row.teacher_names) : '',
      physical_presence: typeof row.physical_presence === 'boolean' ? row.physical_presence : undefined,
      activity_format: activityFormat || undefined,
      room: string(row.room),
      audience: string(row.audience) || list(row.audiences) || list(row.group_context),
      lesson_type: isConflict(row) ? 'conflict' : string(row.lesson_type || row.activity_kind),
    };
  });
}

export function campusStudentDays(value: unknown): CampusDay[] {
  const days = new Map<string, CampusDay>();
  for (const row of rows(value)) {
    const date = string(row.lesson_date || row.date);
    if (!date) continue;
    const day = days.get(date) || { date, state: 'scheduled' as const, items: [] };
    const lesson = campusLessons([row])[0];
    const item: CampusItem = { date, slot: { start: string(row.start_time), end: string(row.end_time) }, state: 'lesson', canonical_title: lesson?.subject, teacher: lesson?.teacher, room: lesson?.room, activity_format: lesson?.activity_format, responsible_teacher: lesson?.responsible_teacher };
    if (isNoLesson(row)) { item.state = 'no_lesson'; }
    else if (isConflict(row)) { item.state = 'conflict'; item.conflict = { state: 'unresolved', label: 'Расписание уточняется', alternatives: [] }; }
    else if (['event', 'special_event'].includes(string(row.activity_kind || row.lesson_type))) { item.state = 'special_event'; item.special_event = { kind: string(row.activity_kind || row.lesson_type), title: lesson?.subject || 'Событие' }; }
    day.items.push(item); days.set(date, day);
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date)).map((day) => ({ ...day, items: day.items.sort((a, b) => a.slot.start.localeCompare(b.slot.start)) }));
}
