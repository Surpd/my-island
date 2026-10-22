'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { teacherAccentForOverride, teacherCode, TEACHER_COLOR_PALETTE, type TeacherColorInput, type TeacherPair } from './teacher-color-registry';
import { useTeacherColors } from './use-teacher-colors';

type Api = <T>(path: string, init?: RequestInit) => Promise<T>;
type Workspace = 'demo' | 'school';
type ColorRecord = {
  teacher_id: string;
  color: string;
  base_color: string;
  manual_color: string | null;
  revision: number;
  source?: string;
};
export type TeacherColorMigrationPreview = {
  ready: boolean;
  plan: Array<{ teacher_id: string; color: string; current_color?: string | null; status?: 'will_add'|'replace_manual'|'replace_shared'; expected_revision: number }>;
  issues: Array<{ teacher_id: string; error: string }>;
  settings?: { assignments: Record<string, number>; overrides: Record<string, string> };
};

type SharedState = {
  scope: string;
  colors: Record<string, ColorRecord>;
  ready: boolean;
  error: string;
  notice: string;
  pending: Set<string>;
  initializing: boolean;
  seeding: boolean;
};

const EMPTY: SharedState = {
  scope: '', colors: {}, ready: false, error: '', notice: '', pending: new Set(), initializing: false, seeding: false,
};
const SHARED_PATH = '/api/admin/school/teacher-ui-colors';
const LOCAL_PREFIX = 'my-island-teacher-colors:v1:';

function message(error: unknown) {
  return error instanceof Error ? error.message : 'Не удалось сохранить цвета преподавателей.';
}

function sharedFallback(id: string) {
  let hash = 0;
  for (const character of id) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return TEACHER_COLOR_PALETTE[hash % TEACHER_COLOR_PALETTE.length];
}

export function useSchoolTeacherColors(
  api: Api,
  workspace: Workspace,
  scope: string,
  teachers: TeacherColorInput[],
  pairs: TeacherPair[] = [],
  enabled = false,
) {
  // Keep the existing browser registry intact as a migration source. It is never
  // uploaded unless an administrator explicitly previews and confirms the import.
  const local = useTeacherColors(scope, teachers, pairs);
  const [state, setState] = useState<SharedState>(EMPTY);
  const generation = useRef(0);
  const saveTimers = useRef<Map<string, number>>(new Map());
  const sharedEnabled = workspace === 'school' && enabled;
  const currentScope = sharedEnabled ? `school:${scope}` : `${workspace}:${scope}:local`;

  const refresh = useCallback(async () => {
    if (!sharedEnabled) return;
    const requestId = ++generation.current;
    setState((current) => ({ ...current, scope: currentScope, ready: false, error: '' }));
    try {
      const response = await api<{ colors?: ColorRecord[] }>(SHARED_PATH);
      if (generation.current !== requestId) return;
      const colors = Object.fromEntries((response.colors || []).map((record) => [String(record.teacher_id), record]));
      setState((current) => ({ ...current, scope: currentScope, colors, ready: true, error: '' }));
    } catch (error) {
      if (generation.current !== requestId) return;
      setState((current) => ({ ...current, scope: currentScope, ready: true, error: message(error) }));
    }
  }, [api, currentScope, sharedEnabled]);

  useEffect(() => {
    if (!sharedEnabled) {
      generation.current += 1;
      return;
    }
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [currentScope, refresh, sharedEnabled]);

  useEffect(() => () => {
    for (const timer of saveTimers.current.values()) window.clearTimeout(timer);
    saveTimers.current.clear();
  }, [currentScope]);

  const shared = state.scope === currentScope ? state : EMPTY;
  const recordFor = useCallback((id: string) => shared.colors[id], [shared.colors]);
  const color = useCallback((id: string) => sharedEnabled
    ? recordFor(id)?.color || sharedFallback(id).background
    : local.color(id), [local, recordFor, sharedEnabled]);
  const accent = useCallback((id: string) => sharedEnabled
    ? teacherAccentForOverride(recordFor(id)?.color || sharedFallback(id).background)
    : local.accent(id), [local, recordFor, sharedEnabled]);
  const code = useCallback((id: string) => teacherCode(id), []);
  const overrides = useMemo(() => {
    if (!sharedEnabled) return local.overrides;
    return Object.fromEntries(Object.entries(shared.colors)
      .filter(([, record]) => record.manual_color)
      .map(([id, record]) => [id, record.manual_color!]));
  }, [local.overrides, shared.colors, sharedEnabled]);

  const updateRecord = useCallback((record: ColorRecord) => {
    setState((current) => ({ ...current, scope: currentScope,
      colors: { ...current.colors, [String(record.teacher_id)]: record }, error: '', notice: 'Цвет сохранён для школы.' }));
  }, [currentScope]);

  const runMutation = useCallback(async (id: string, path: string, body: unknown) => {
    setState((current) => ({ ...current, scope: currentScope, pending: new Set(current.pending).add(id), error: '', notice: '' }));
    try {
      const record = await api<ColorRecord>(path, { method: path.endsWith('/reset') ? 'POST' : 'PUT', body: JSON.stringify(body) });
      updateRecord(record);
    } catch (error) {
      const isConflict = message(error).includes('409') || /conflict|revision|устар/i.test(message(error));
      await refresh();
      if (isConflict) {
        setState((current) => ({ ...current, error: 'Цвет изменился в другой сессии. Список обновлён, повторите выбор.', notice: '' }));
      } else {
        setState((current) => ({ ...current, error: 'Не удалось сохранить цвет. Проверьте соединение и повторите попытку.', notice: '' }));
      }
    } finally {
      setState((current) => {
        const pending = new Set(current.pending);
        pending.delete(id);
        return { ...current, pending };
      });
    }
  }, [api, currentScope, refresh, updateRecord]);

  const setColor = useCallback((id: string, hex: string) => {
    if (!sharedEnabled) { local.setColor(id, hex); return; }
    if (!shared.ready) return;
    const current = recordFor(id);
    const expectedRevision = current?.revision ?? 0;
    setState((stateNow) => ({ ...stateNow, scope: currentScope, colors: {
      ...stateNow.colors,
      [id]: { teacher_id: id, color: hex, base_color: current?.base_color || sharedFallback(id).background,
        manual_color: hex, revision: expectedRevision, source: current?.source },
    }, error: '', notice: '' }));
    const existingTimer = saveTimers.current.get(id);
    if (existingTimer !== undefined) window.clearTimeout(existingTimer);
    const timer = window.setTimeout(() => {
      saveTimers.current.delete(id);
      void runMutation(id, `${SHARED_PATH}/${encodeURIComponent(id)}`, {
        color: hex, expected_revision: expectedRevision,
      });
    }, 220);
    saveTimers.current.set(id, timer);
  }, [currentScope, local, recordFor, runMutation, shared.ready, sharedEnabled]);

  const resetColor = useCallback((id: string) => {
    if (!sharedEnabled) { local.resetColor(id); return; }
    if (!shared.ready) return;
    const current = recordFor(id);
    if (!current?.manual_color) return;
    void runMutation(id, `${SHARED_PATH}/${encodeURIComponent(id)}/reset`, {
      expected_revision: current.revision,
    });
  }, [local, recordFor, runMutation, shared.ready, sharedEnabled]);

  const seedApproved = useCallback(async () => {
    setState((current) => ({ ...current, seeding: true, error: '', notice: '' }));
    try {
      const result = await api<{ inserted: number; updated?: number; preserved: number }>(`${SHARED_PATH}/seed-approved`, { method: 'POST' });
      await refresh();
      setState((current) => ({ ...current, notice: `Применены исходные цвета: ${result.inserted + (result.updated || 0)}; сохранены текущие ручные настройки: ${result.preserved}.` }));
    } catch (error) {
      setState((current) => ({ ...current, error: message(error) }));
    } finally {
      setState((current) => ({ ...current, seeding: false }));
    }
  }, [api, refresh]);

  const readLocalSettings = useCallback(() => {
    const raw = window.localStorage.getItem(`${LOCAL_PREFIX}${encodeURIComponent(scope)}`);
    if (!raw) return { assignments: {}, overrides: {} };
    const parsed = JSON.parse(raw) as { assignments?: Record<string, number>; overrides?: Record<string, string> };
    return { assignments: parsed.assignments || {}, overrides: parsed.overrides || {} };
  }, [scope]);

  const previewLocalMigration = useCallback(async () => {
    const localSettings = readLocalSettings();
    // Only explicit local overrides are eligible. Automatic palette assignments
    // remain in browser storage and are never silently promoted to school state.
    const settings = { assignments: {}, overrides: { ...localSettings.overrides } };
    const result = await api<TeacherColorMigrationPreview>(`${SHARED_PATH}/settings/preview`, {
      method: 'POST', body: JSON.stringify({ settings }),
    });
    return { ...result, settings };
  }, [api, readLocalSettings]);

  const confirmLocalMigration = useCallback(async (preview: TeacherColorMigrationPreview) => {
    const settings = preview.settings;
    if (!settings) throw new Error('Предпросмотр устарел. Запросите его снова.');
    const expected_revisions = Object.fromEntries(preview.plan.map((item) => [item.teacher_id, item.expected_revision]));
    const result = await api<{ imported: number }>(`${SHARED_PATH}/settings/confirm`, {
      method: 'POST', body: JSON.stringify({ settings, expected_revisions }),
    });
    await refresh();
    setState((current) => ({ ...current, notice: `Перенесено цветов: ${result.imported}.` }));
  }, [api, refresh]);

  const initializeMissing = useCallback(async () => {
    setState((current) => ({ ...current, initializing: true, error: '', notice: '' }));
    try {
      const result = await api<{ created: number }>(`${SHARED_PATH}/initialize`, { method: 'POST' });
      await refresh();
      setState((current) => ({ ...current, notice: `Закреплены цвета для ${result.created} новых преподавателей.` }));
    } catch (error) {
      setState((current) => ({ ...current, error: message(error) }));
    } finally {
      setState((current) => ({ ...current, initializing: false }));
    }
  }, [api, refresh]);

  const unassignedCount = sharedEnabled
    ? teachers.filter((teacher) => (shared.colors[teacher.id]?.revision ?? 0) === 0).length
    : 0;

  return {
    color, accent, code, overrides, setColor, resetColor,
    storageError: sharedEnabled ? '' : local.storageError,
    ready: sharedEnabled ? shared.ready : local.ready,
    mode: sharedEnabled ? 'school' as const : 'local' as const,
    error: shared.error,
    notice: shared.notice,
    pending: shared.pending,
    initializing: shared.initializing,
    seeding: shared.seeding,
    unassignedCount,
    refresh,
    initialize: initializeMissing,
    seedApproved,
    previewLocalMigration,
    confirmLocalMigration,
  };
}
