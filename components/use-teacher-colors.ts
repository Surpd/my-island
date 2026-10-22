'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  TeacherColorRegistry,
  TEACHER_COLOR_PALETTE,
  teacherAccentForOverride,
  teacherCode,
  type TeacherColorInput,
  type TeacherColorSnapshot,
  type TeacherPair,
} from './teacher-color-registry';

type HookState = TeacherColorSnapshot & { scope: string; ready: boolean };
type RegistryEntry = { scope: string; registry: TeacherColorRegistry };

const EMPTY_SNAPSHOT: TeacherColorSnapshot = { assignments: {}, overrides: {}, storageError: '' };
const EMPTY_PAIRS: TeacherPair[] = [];

function stableIdIndex(id: string) {
  let hash = 0;
  for (const character of id) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return hash % TEACHER_COLOR_PALETTE.length;
}

function sameSnapshot(previous: HookState, scope: string, snapshot: TeacherColorSnapshot) {
  return previous.scope === scope && previous.ready && previous.storageError === snapshot.storageError
    && JSON.stringify(previous.assignments) === JSON.stringify(snapshot.assignments)
    && JSON.stringify(previous.overrides) === JSON.stringify(snapshot.overrides);
}

function fallbackColor(id: string) {
  return TEACHER_COLOR_PALETTE[stableIdIndex(id)];
}

export function useTeacherColors(scope: string, teachers: TeacherColorInput[], coVisiblePairs: TeacherPair[] = EMPTY_PAIRS) {
  const [state, setState] = useState<HookState>({ ...EMPTY_SNAPSHOT, scope: '', ready: false });
  const registryRef = useRef<RegistryEntry | null>(null);

  useEffect(() => {
    const existing = registryRef.current;
    const registry = existing?.scope === scope ? existing.registry : new TeacherColorRegistry(scope);
    registryRef.current = { scope, registry };
    const snapshot = existing?.scope === scope
      ? registry.reconcile(teachers, coVisiblePairs)
      : registry.hydrate(teachers, coVisiblePairs);
    setState((previous) => sameSnapshot(previous, scope, snapshot) ? previous : { ...snapshot, scope, ready: true });
    return () => {
      if (registryRef.current?.registry === registry) registryRef.current = null;
    };
  }, [scope, teachers, coVisiblePairs]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      const entry = registryRef.current;
      if (!entry || entry.scope !== scope || (event.key !== entry.registry.storageKey && event.key !== null)) return;
      const snapshot = entry.registry.reload(teachers, coVisiblePairs);
      setState((previous) => sameSnapshot(previous, scope, snapshot) ? previous : { ...snapshot, scope, ready: true });
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [scope, teachers, coVisiblePairs]);

  const color = useCallback((id: string) => {
    if (state.scope !== scope) return fallbackColor(id).background;
    const index = state.assignments[id] === undefined ? stableIdIndex(id) : state.assignments[id];
    return state.overrides[id] || TEACHER_COLOR_PALETTE[index].background;
  }, [scope, state]);

  const accent = useCallback((id: string) => {
    if (state.scope === scope && state.overrides[id]) return teacherAccentForOverride(state.overrides[id]);
    if (state.scope !== scope || state.assignments[id] === undefined) return fallbackColor(id).accent;
    return TEACHER_COLOR_PALETTE[state.assignments[id]].accent;
  }, [scope, state]);

  const code = useCallback((id: string) => {
    return teacherCode(id);
  }, []);

  const setColor = useCallback((id: string, hex: string) => {
    const entry = registryRef.current;
    if (entry?.scope !== scope) return;
    const snapshot = entry.registry.setColor(id, hex);
    setState((previous) => sameSnapshot(previous, scope, snapshot) ? previous : { ...snapshot, scope, ready: true });
  }, [scope]);

  const resetColor = useCallback((id: string) => {
    const entry = registryRef.current;
    if (entry?.scope !== scope) return;
    const snapshot = entry.registry.resetColor(id);
    setState((previous) => sameSnapshot(previous, scope, snapshot) ? previous : { ...snapshot, scope, ready: true });
  }, [scope]);

  const current = state.scope === scope ? state : EMPTY_SNAPSHOT;
  return {
    color,
    accent,
    code,
    overrides: current.overrides,
    setColor,
    resetColor,
    storageError: current.storageError,
    ready: state.scope === scope && state.ready,
  };
}
