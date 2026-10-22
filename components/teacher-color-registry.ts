export type TeacherColorInput = { id: string; name: string };
export type TeacherPair = readonly [string, string];
export type TeacherColorSnapshot = {
  assignments: Record<string, number>;
  overrides: Record<string, string>;
  storageError: string;
};

export interface TeacherColorStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const VERSION = 1;
export const LEGACY_TEACHER_COLORS_KEY = 'my-island-constructor-teacher-colors';
const HEX_COLOR = /^#[\da-f]{6}$/i;

function makePalette() {
  return Array.from({ length: 40 }, (_, index) => {
    const hue = (index * 137.508) % 360;
    const saturation = index % 2 === 0 ? 49 : 57;
    const lightness = index % 3 === 0 ? 90 : index % 3 === 1 ? 87 : 92;
    return { hue, background: hslToHex(hue, saturation, lightness), accent: hslToHex(hue, 67, 39) };
  });
}

function hslToHex(hue: number, saturation: number, lightness: number) {
  const s = saturation / 100;
  const l = lightness / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const section = hue / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  const [r, g, b] = section < 1 ? [chroma, x, 0] : section < 2 ? [x, chroma, 0] : section < 3 ? [0, chroma, x] : section < 4 ? [0, x, chroma] : section < 5 ? [x, 0, chroma] : [chroma, 0, x];
  const m = l - chroma / 2;
  return `#${[r, g, b].map((value) => Math.round((value + m) * 255).toString(16).padStart(2, '0')).join('')}`;
}

function hexToHsl(hex: string) {
  const values = hex.slice(1).match(/.{2}/g)!.map((part) => parseInt(part, 16) / 255);
  const [red, green, blue] = values;
  const maximum = Math.max(red, green, blue);
  const minimum = Math.min(red, green, blue);
  const delta = maximum - minimum;
  const lightness = (maximum + minimum) / 2;
  let hue = 0;
  let saturation = 0;
  if (delta) {
    saturation = delta / (1 - Math.abs(2 * lightness - 1));
    if (maximum === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (maximum === green) hue = 60 * ((blue - red) / delta + 2);
    else hue = 60 * ((red - green) / delta + 4);
  }
  return { hue: (hue + 360) % 360, saturation: saturation * 100, lightness: lightness * 100 };
}

export function teacherAccentForOverride(hex: string) {
  const { hue, saturation, lightness } = hexToHsl(hex);
  return hslToHex(hue, Math.max(55, saturation), Math.max(6, Math.min(38, lightness * 0.68)));
}

export function teacherCode(id: string) {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < id.length; index += 1) {
    const codeUnit = id.charCodeAt(index);
    first = Math.imul(first ^ codeUnit, 0x01000193) >>> 0;
    second = Math.imul(second ^ codeUnit, 0x85ebca6b) >>> 0;
  }
  return `T${first.toString(36).toUpperCase().padStart(7, '0').slice(-5)}${(second % 36).toString(36).toUpperCase()}`;
}

export const TEACHER_COLOR_PALETTE = makePalette();

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function readColorMap(value: unknown) {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([id, color]) => id.length > 0 && typeof color === 'string' && HEX_COLOR.test(color)).map(([id, color]) => [id, (color as string).toLowerCase()]));
}

function readAssignments(value: unknown) {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([id, index]) => id.length > 0 && Number.isInteger(index) && Number(index) >= 0 && Number(index) < TEACHER_COLOR_PALETTE.length).map(([id, index]) => [id, Number(index)]));
}

function parseStored(value: string | null) {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed) || parsed.version !== VERSION) return null;
    return { assignments: readAssignments(parsed.assignments), overrides: readColorMap(parsed.overrides) };
  } catch {
    return null;
  }
}

function colorToLab(hex: string) {
  const [red, green, blue] = hex.slice(1).match(/.{2}/g)!.map((part) => {
    const value = parseInt(part, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  const x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047;
  const y = 0.2126729 * red + 0.7151522 * green + 0.072175 * blue;
  const z = (0.0193339 * red + 0.119192 * green + 0.9503041 * blue) / 1.08883;
  const f = (value: number) => value > 0.008856 ? Math.cbrt(value) : 7.787 * value + 16 / 116;
  const fx = f(x);
  const fy = f(y);
  const fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)] as const;
}

function colorDistance(left: string, right: string) {
  const leftLab = colorToLab(left);
  const rightLab = colorToLab(right);
  return Math.hypot(leftLab[0] - rightLab[0], leftLab[1] - rightLab[1], leftLab[2] - rightLab[2]);
}

function validId(id: unknown): id is string {
  return typeof id === 'string' && id.length > 0;
}

/** Pure, storage-injected teacher color assignment registry. */
export class TeacherColorRegistry {
  readonly storageKey: string;
  private assignments: Record<string, number> = {};
  private overrides: Record<string, string> = {};
  private hydrated = false;
  private storage: TeacherColorStorage | null;
  private storageError = '';

  constructor(scope: string, storage?: TeacherColorStorage | null) {
    this.storageKey = `my-island-teacher-colors:v${VERSION}:${encodeURIComponent(scope)}`;
    if (storage !== undefined) {
      this.storage = storage;
    } else {
      try { this.storage = typeof window === 'undefined' ? null : window.localStorage; }
      catch (error) {
        this.storage = null;
        this.storageError = errorMessage(error);
      }
    }
  }

  hydrate(teachers: TeacherColorInput[] = [], coVisiblePairs: TeacherPair[] = []) {
    if (!this.hydrated) {
      let stored: ReturnType<typeof parseStored> = null;
      try {
        stored = this.storage ? parseStored(this.storage.getItem(this.storageKey)) : null;
        if (!stored && this.storage) {
          const legacyValue = this.storage.getItem(LEGACY_TEACHER_COLORS_KEY);
          if (legacyValue) {
            try { this.overrides = readColorMap(JSON.parse(legacyValue)); }
            catch { this.overrides = {}; }
          }
        }
      } catch (error) {
        this.storageError = errorMessage(error);
      }
      if (stored) {
        this.assignments = stored.assignments;
        this.overrides = stored.overrides;
      }
      this.hydrated = true;
    }
    this.ensureAssignments(teachers, coVisiblePairs);
    return this.snapshot();
  }

  reconcile(teachers: TeacherColorInput[], coVisiblePairs: TeacherPair[] = []) {
    if (!this.hydrated) return this.hydrate(teachers, coVisiblePairs);
    this.ensureAssignments(teachers, coVisiblePairs);
    return this.snapshot();
  }

  reload(teachers: TeacherColorInput[] = [], coVisiblePairs: TeacherPair[] = []) {
    this.hydrated = false;
    this.assignments = {};
    this.overrides = {};
    this.storageError = '';
    return this.hydrate(teachers, coVisiblePairs);
  }

  color(id: string) {
    return this.overrides[id] || TEACHER_COLOR_PALETTE[this.assignmentFor(id)].background;
  }

  accent(id: string) {
    if (this.overrides[id]) return teacherAccentForOverride(this.overrides[id]);
    return TEACHER_COLOR_PALETTE[this.assignmentFor(id)].accent;
  }

  code(id: string) {
    return teacherCode(id);
  }

  setColor(id: string, color: string) {
    if (!validId(id) || !HEX_COLOR.test(color)) return this.snapshot();
    this.overrides = { ...this.overrides, [id]: color.toLowerCase() };
    this.persist();
    return this.snapshot();
  }

  resetColor(id: string) {
    if (validId(id) && Object.hasOwn(this.overrides, id)) {
      const { [id]: _removed, ...remaining } = this.overrides;
      this.overrides = remaining;
      this.persist();
    }
    return this.snapshot();
  }

  snapshot(): TeacherColorSnapshot {
    return { assignments: { ...this.assignments }, overrides: { ...this.overrides }, storageError: this.storageError };
  }

  private assignmentFor(id: string) {
    if (Object.hasOwn(this.assignments, id)) return this.assignments[id];
    let hash = 0;
    for (const character of id) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
    return hash % TEACHER_COLOR_PALETTE.length;
  }

  private ensureAssignments(teachers: TeacherColorInput[], pairs: TeacherPair[]) {
    const ids = [...new Set(teachers.map((teacher) => teacher?.id).filter(validId))];
    const missing = ids.filter((id) => !Object.hasOwn(this.assignments, id));
    if (!missing.length) return;

    const adjacency = new Map<string, Set<string>>();
    for (const [left, right] of pairs) {
      if (!validId(left) || !validId(right) || left === right) continue;
      if (!adjacency.has(left)) adjacency.set(left, new Set());
      if (!adjacency.has(right)) adjacency.set(right, new Set());
      adjacency.get(left)!.add(right);
      adjacency.get(right)!.add(left);
    }
    missing.sort((left, right) => (adjacency.get(right)?.size || 0) - (adjacency.get(left)?.size || 0) || left.localeCompare(right));
    const initialIndices = Object.values(this.assignments);

    for (const id of missing) {
      const neighbors = [...(adjacency.get(id) || [])].filter((neighbor) => Object.hasOwn(this.assignments, neighbor));
      let bestIndex = 0;
      let bestScore = Number.NEGATIVE_INFINITY;
      for (let index = 0; index < TEACHER_COLOR_PALETTE.length; index += 1) {
        // Exhaust distinct palette entries before repeating a color for another ID.
        if (new Set(initialIndices).size < TEACHER_COLOR_PALETTE.length && initialIndices.includes(index)) continue;
        const candidate = TEACHER_COLOR_PALETTE[index];
        const minDistance = neighbors.length ? Math.min(...neighbors.map((neighbor) => colorDistance(
          candidate.background,
          this.overrides[neighbor] || TEACHER_COLOR_PALETTE[this.assignments[neighbor]].background,
        ))) : 360;
        const collisionPenalty = neighbors.filter((neighbor) => this.assignments[neighbor] === index).length * 360;
        const usagePenalty = initialIndices.filter((used) => used === index).length * 1.5;
        const score = minDistance - collisionPenalty - usagePenalty;
        if (score > bestScore) { bestScore = score; bestIndex = index; }
      }
      this.assignments[id] = bestIndex;
      initialIndices.push(bestIndex);
    }
    this.persist();
  }

  private persist() {
    if (!this.hydrated || !this.storage) return;
    try {
      this.storage.setItem(this.storageKey, JSON.stringify({ version: VERSION, assignments: this.assignments, overrides: this.overrides }));
    } catch (error) {
      this.storageError = errorMessage(error);
    }
  }
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Browser storage is unavailable.';
}
