const numberedRoom = /^(?:(?:каб(?:инет)?\.?\s*))?(\d+[a-zа-я]?(?:[-/][a-zа-я0-9]+)?)$/iu;

/** Stable identity for room comparisons; only common numbered-room spellings collapse. */
export function normalizeRoomKey(value: unknown): string {
  const source = typeof value === 'string' || typeof value === 'number' ? String(value).trim().replace(/\s+/gu, ' ') : '';
  if (!source) return '';
  const match = source.match(numberedRoom);
  if (match) return `room:${match[1].toLocaleLowerCase('ru')}`;
  return `name:${source.toLocaleLowerCase('ru')}`;
}

/** Human-readable room label. Numbered aliases use the concise canonical number. */
export function normalizeRoomDisplay(value: unknown): string {
  const source = typeof value === 'string' || typeof value === 'number' ? String(value).trim().replace(/\s+/gu, ' ') : '';
  if (!source) return '';
  const match = source.match(numberedRoom);
  return match ? match[1] : source;
}
