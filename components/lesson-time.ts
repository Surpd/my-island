/** Normalize local lesson clock values without parsing them as dates or applying a timezone. */
export function formatLessonTime(value: unknown): string {
  if (typeof value !== 'string') return '';
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!match) return '';
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  const seconds = match[3] === undefined ? 0 : Number(match[3]);
  if (hours > 23 || minutes > 59 || seconds > 59) return '';
  return `${String(hours).padStart(2, '0')}:${match[2]}`;
}

export function formatLessonTimeRange(start: unknown, end?: unknown, missing = 'Время не указано'): string {
  const formattedStart = formatLessonTime(start);
  if (!formattedStart) return missing;
  const formattedEnd = formatLessonTime(end);
  return formattedEnd ? `${formattedStart}–${formattedEnd}` : formattedStart;
}
