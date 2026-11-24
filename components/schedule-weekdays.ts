// Schedule indexes are Monday-first, unlike JavaScript's Sunday-first dates.
export const SHORT_WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'] as const;

export function shortWeekdayForDate(isoDay: string): string {
  const day = new Date(`${isoDay}T00:00:00Z`).getUTCDay();
  return SHORT_WEEKDAYS[(day + 6) % 7] ?? '';
}
