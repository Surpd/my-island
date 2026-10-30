const numberedRoom = /^(?:(?:каб(?:инет)?\.?\s*))?(\d+[a-zа-я]?(?:[-/][a-zа-я0-9]+)?)$/iu;

/** Stable identity for room comparisons; only common numbered-room spellings collapse. */
export function normalizeRoomKey(value: unknown): string {
  const source = typeof value === 'string' || typeof value === 'number' ? String(value).trim().replace(/\s+/gu, ' ') : '';
  if (!source) return '';
  const match = source.match(numberedRoom);
  if (match) return `room:${match[1].toLocaleLowerCase('ru')}`;
  return `name:${source.toLocaleLowerCase('ru')}`;
}

/** Human-readable room label shared by schedules, lists, and hints. */
export function normalizeRoomDisplay(value: unknown): string {
  const source = typeof value === 'string' || typeof value === 'number' ? String(value).trim().replace(/\s+/gu, ' ') : '';
  if (!source) return '';
  const match = source.match(numberedRoom);
  return match ? `каб. ${match[1]}` : source;
}

/** Keep numbered rooms easy to edit while accepting any supported spelling. */
export function normalizeRoomInput(value: unknown): string {
  const source = typeof value === 'string' || typeof value === 'number' ? String(value).trim().replace(/\s+/gu, ' ') : '';
  if (!source) return '';
  return source.match(numberedRoom)?.[1] || source;
}
