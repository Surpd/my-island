import test from 'node:test';
import assert from 'node:assert/strict';
import {
  LEGACY_TEACHER_COLORS_KEY,
  TEACHER_COLOR_PALETTE,
  TeacherColorRegistry,
} from './teacher-color-registry.ts';

class MockStorage {
  values = new Map();
  writes = 0;
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.writes += 1; this.values.set(key, value); }
}

const teacher = (id, name = id) => ({ id, name });

function lab(hex) {
  const [r, g, b] = hex.slice(1).match(/../g).map((part) => parseInt(part, 16) / 255).map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
  const z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883;
  const f = (v) => v > 0.008856 ? Math.cbrt(v) : 7.787 * v + 16 / 116;
  const [fx, fy, fz] = [f(x), f(y), f(z)];
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
const deltaE76 = (left, right) => Math.hypot(...lab(left).map((value, index) => value - lab(right)[index]));
const minimumPairDistance = (indices) => Math.min(...indices.flatMap((left, index) => indices.slice(index + 1).map((right) => deltaE76(TEACHER_COLOR_PALETTE[left].background, TEACHER_COLOR_PALETTE[right].background))));

test('palette has at least 32 distinct soft backgrounds and vivid accents', () => {
  assert.ok(TEACHER_COLOR_PALETTE.length >= 32);
  assert.equal(new Set(TEACHER_COLOR_PALETTE.map(({ background }) => background)).size, TEACHER_COLOR_PALETTE.length);
  assert.ok(TEACHER_COLOR_PALETTE.every(({ background, accent }) => /^#[\da-f]{6}$/.test(background) && /^#[\da-f]{6}$/.test(accent) && background !== accent));
});

test('assignment is exact-ID based and survives reorder, filtering, reload, and append', () => {
  const storage = new MockStorage();
  const first = new TeacherColorRegistry('school:workspace-a', storage);
  first.hydrate([teacher('teacher-b', 'Бета'), teacher('teacher-a', 'Альфа')], [['teacher-a', 'teacher-b']]);
  const original = first.snapshot().assignments;
  assert.notEqual(first.color('teacher-a'), first.color('teacher-b'));

  const reopened = new TeacherColorRegistry('school:workspace-a', storage);
  reopened.hydrate([teacher('teacher-a', 'Переименована'), teacher('teacher-c'), teacher('teacher-b')], [['teacher-a', 'teacher-b'], ['teacher-c', 'teacher-a']]);
  assert.equal(reopened.snapshot().assignments['teacher-a'], original['teacher-a']);
  assert.equal(reopened.snapshot().assignments['teacher-b'], original['teacher-b']);

  const later = new TeacherColorRegistry('school:workspace-a', storage);
  later.hydrate([teacher('teacher-b')]); // A filtered teacher is retained in persisted assignments.
  assert.equal(later.snapshot().assignments['teacher-a'], original['teacher-a']);
  assert.equal(later.snapshot().assignments['teacher-b'], original['teacher-b']);
});

test('new co-visible teachers are allocated away from existing colors without recoloring existing IDs', () => {
  const registry = new TeacherColorRegistry('school:co-visible', new MockStorage());
  registry.hydrate([teacher('existing-1'), teacher('existing-2')], [['existing-1', 'existing-2']]);
  const before = registry.snapshot().assignments;
  registry.reconcile([teacher('existing-1'), teacher('existing-2'), teacher('new')], [['existing-1', 'existing-2'], ['new', 'existing-1'], ['new', 'existing-2']]);
  const after = registry.snapshot().assignments;
  assert.equal(after['existing-1'], before['existing-1']);
  assert.equal(after['existing-2'], before['existing-2']);
  assert.notEqual(after.new, before['existing-1']);
  assert.notEqual(after.new, before['existing-2']);
});

test('co-visible clique spreads assignments across distinct palette entries', () => {
  const ids = Array.from({ length: 20 }, (_, index) => `t-${String(index).padStart(2, '0')}`);
  const pairs = ids.flatMap((left, i) => ids.slice(i + 1).map((right) => [left, right]));
  const registry = new TeacherColorRegistry('school:clique', new MockStorage());
  registry.hydrate(ids.map((id) => teacher(id)), pairs);
  assert.equal(new Set(ids.map((id) => registry.snapshot().assignments[id])).size, ids.length);
});

test('new co-visible clique IDs use Lab distance and improve on the former hue-only allocation', () => {
  const ids = Array.from({ length: 16 }, (_, index) => `teacher-${index}`);
  const pairs = ids.flatMap((left, index) => ids.slice(index + 1).map((right) => [left, right]));

  // Reproduce the previous hue-only greedy allocator as the comparison baseline.
  const oldAssignments = {};
  const oldUsed = [];
  for (const id of [...ids].sort()) {
    let bestIndex = 0;
    let bestScore = Number.NEGATIVE_INFINITY;
    for (let index = 0; index < TEACHER_COLOR_PALETTE.length; index += 1) {
      const candidate = TEACHER_COLOR_PALETTE[index];
      const minHue = oldUsed.length ? Math.min(...oldUsed.map((used) => {
        const difference = Math.abs(candidate.hue - TEACHER_COLOR_PALETTE[used].hue) % 360;
        return Math.min(difference, 360 - difference);
      })) : 360;
      const usagePenalty = oldUsed.filter((used) => used === index).length * 1.5;
      const score = minHue - usagePenalty;
      if (score > bestScore) { bestScore = score; bestIndex = index; }
    }
    oldAssignments[id] = bestIndex;
    oldUsed.push(bestIndex);
  }

  const registry = new TeacherColorRegistry('school:lab-clique', new MockStorage());
  const assignments = registry.hydrate(ids.map((id) => teacher(id)), pairs).assignments;
  const newIndices = ids.map((id) => assignments[id]);
  const oldMinimum = minimumPairDistance(oldUsed);
  const newMinimum = minimumPairDistance(newIndices);
  assert.equal(new Set(newIndices).size, ids.length);
  assert.ok(newMinimum > oldMinimum, `expected Lab allocation to improve minimum ΔE76 (${newMinimum} > ${oldMinimum})`);
  console.log(`16-clique minimum ΔE76: former hue-only=${oldMinimum.toFixed(2)}, Lab=${newMinimum.toFixed(2)}`);
});

test('new assignments score neighbors by visible manual override color', () => {
  const storage = new MockStorage();
  const registry = new TeacherColorRegistry('school:override-distance', storage);
  registry.hydrate([teacher('neighbor')]);
  registry.setColor('neighbor', '#00ff00');
  const maxDistanceIndex = TEACHER_COLOR_PALETTE.reduce((best, color, index, palette) => deltaE76(color.background, '#00ff00') > deltaE76(palette[best].background, '#00ff00') ? index : best, 0);
  registry.reconcile([teacher('neighbor'), teacher('new')], [['neighbor', 'new']]);
  assert.equal(registry.snapshot().assignments.new, maxDistanceIndex);
});

test('legacy storage imports only valid manual overrides and preserves the source key', () => {
  const storage = new MockStorage();
  storage.setItem(LEGACY_TEACHER_COLORS_KEY, JSON.stringify({ 'teacher-1': '#ABCDEF', invalid: 'red', empty: '', '__proto__': '#101010' }));
  const registry = new TeacherColorRegistry('school:legacy', storage);
  registry.hydrate([teacher('teacher-1')]);
  assert.equal(registry.color('teacher-1'), '#abcdef');
  assert.equal(registry.snapshot().overrides.invalid, undefined);
  assert.equal(storage.getItem(LEGACY_TEACHER_COLORS_KEY), JSON.stringify({ 'teacher-1': '#ABCDEF', invalid: 'red', empty: '', '__proto__': '#101010' }));
  assert.ok(storage.getItem(registry.storageKey));
});

test('manual override persists by ID; reset returns to assigned base color', () => {
  const storage = new MockStorage();
  const registry = new TeacherColorRegistry('school:manual', storage);
  registry.hydrate([teacher('teacher-7')]);
  const base = registry.color('teacher-7');
  const baseAccent = registry.accent('teacher-7');
  const stableCode = registry.code('teacher-7');
  registry.setColor('teacher-7', '#Ff00aA');
  assert.equal(registry.color('teacher-7'), '#ff00aa');
  assert.notEqual(registry.accent('teacher-7'), baseAccent);
  assert.equal(registry.code('teacher-7'), stableCode);

  const reloaded = new TeacherColorRegistry('school:manual', storage);
  reloaded.hydrate([teacher('teacher-7')]);
  assert.equal(reloaded.color('teacher-7'), '#ff00aa');
  assert.equal(reloaded.accent('teacher-7'), registry.accent('teacher-7'));
  assert.equal(reloaded.code('teacher-7'), stableCode);
  reloaded.resetColor('teacher-7');
  assert.equal(reloaded.color('teacher-7'), base);
  assert.equal(reloaded.snapshot().overrides['teacher-7'], undefined);
});

test('teacher codes are ID-derived and unique across more IDs than palette colors', () => {
  const ids = Array.from({ length: 64 }, (_, index) => `teacher-${index + 1}`);
  const first = new TeacherColorRegistry('school:codes', new MockStorage());
  first.hydrate(ids.map((id) => teacher(id)));
  const codes = ids.map((id) => first.code(id));
  assert.equal(new Set(codes).size, ids.length);

  const second = new TeacherColorRegistry('school:codes', new MockStorage());
  second.hydrate([...ids].reverse().map((id) => teacher(id)));
  assert.deepEqual(ids.map((id) => second.code(id)), codes);
});

test('empty hydration does not persist an empty registry or lose later legacy overrides', () => {
  const storage = new MockStorage();
  storage.setItem(LEGACY_TEACHER_COLORS_KEY, JSON.stringify({ 'late-teacher': '#c0ffee' }));
  storage.writes = 0;
  const firstMount = new TeacherColorRegistry('school:async-teachers', storage);
  firstMount.hydrate([]);
  assert.equal(storage.writes, 0);
  assert.equal(storage.getItem(firstMount.storageKey), null);

  // StrictMode setup/cleanup/setup reads the same preferences and does not write defaults.
  const strictReplay = new TeacherColorRegistry('school:async-teachers', storage);
  strictReplay.hydrate([]);
  assert.equal(storage.writes, 0);
  strictReplay.reconcile([teacher('late-teacher')]);
  assert.equal(strictReplay.color('late-teacher'), '#c0ffee');
});

test('malformed and invalid stored data falls back safely; denied storage keeps an in-memory registry', () => {
  const malformed = new MockStorage();
  const malformedRegistry = new TeacherColorRegistry('school:invalid', malformed);
  malformed.setItem(malformedRegistry.storageKey, '{bad json');
  malformedRegistry.hydrate([teacher('ok')]);
  assert.ok(malformedRegistry.snapshot().assignments.ok >= 0);

  const invalid = new MockStorage();
  const invalidRegistry = new TeacherColorRegistry('school:invalid-schema', invalid);
  invalid.setItem(invalidRegistry.storageKey, JSON.stringify({ version: 99, assignments: { ok: -1 }, overrides: { ok: 'not-a-color' } }));
  invalidRegistry.hydrate([teacher('ok')]);
  assert.ok(invalidRegistry.snapshot().assignments.ok >= 0);
  assert.equal(invalidRegistry.snapshot().overrides.ok, undefined);

  const denied = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  const deniedRegistry = new TeacherColorRegistry('school:denied', denied);
  deniedRegistry.hydrate([teacher('local-only')]);
  assert.ok(deniedRegistry.snapshot().storageError);
  assert.match(deniedRegistry.color('local-only'), /^#[\da-f]{6}$/i);
});

test('scope isolates assignments and storage reload observes another tab update', () => {
  const storage = new MockStorage();
  const first = new TeacherColorRegistry('school:one', storage);
  const second = new TeacherColorRegistry('school:two', storage);
  first.hydrate([teacher('shared-id')]);
  second.hydrate([teacher('shared-id')]);
  assert.notEqual(first.storageKey, second.storageKey);

  const external = new TeacherColorRegistry('school:one', storage);
  external.hydrate([teacher('shared-id')]);
  external.setColor('shared-id', '#123456');
  first.reload([teacher('shared-id')]);
  assert.equal(first.color('shared-id'), '#123456');
});
