import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

const API = 'http://localhost:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

type PreviewAssignment = {
  activity: string;
  canonical_group_ids?: string[];
  teacher_ids?: string[];
  metadata?: { teachers?: string[]; room?: string };
  audience_rule: Record<string, unknown>;
};
type PreviewPatch = {
  block_key: string;
  assignments: PreviewAssignment[];
  change_kind: string;
  resolution_state: string;
  weekday: number;
  slot: { start: string; end: string };
  grade_scope: string;
  weekly_source_cells: string[];
  source_issue?: string;
  resolution_details?: string[];
  before?: unknown;
  after?: unknown;
};
type PreviewFixture = { overlay_patches: PreviewPatch[]; draft_changes?: unknown[]; [key: string]: unknown };

test('Schedule: fixture preview → saved draft → published SQLite version → readback', async ({ page }) => {
  const [groupsResponse, teachersResponse] = await Promise.all([
    page.request.get(`${API}/api/admin/groups`, { headers: ADMIN }),
    page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN }),
  ]);
  expect(groupsResponse.status()).toBe(200);
  expect(teachersResponse.status()).toBe(200);
  const groups = (await groupsResponse.json()).items as Array<{ id: string; name: string; display_name: string }>;
  const teachers = (await teachersResponse.json()).items as Array<{ id: string; display_name: string }>;
  const groupId = (name: string) => {
    const group = groups.find((item) => item.name === name || item.display_name === name);
    if (!group) throw new Error(`Local fixture group is missing: ${name}`);
    return String(group.id);
  };
  const teacher = teachers.find((item) => item.display_name === 'Тестовый преподаватель');
  if (!teacher) throw new Error('Local fixture teacher is missing');
  const fixtureText = await readFile(resolve('e2e/fixtures/schedule-preview.json'), 'utf8');
  const fixture = JSON.parse(fixtureText.replace(/\{\{group:([^}]+)}}/g, (_match, name: string) => groupId(name)).replaceAll('{{teacher}}', String(teacher.id))) as PreviewFixture;
  fixture.draft_changes = fixture.overlay_patches.flatMap((patch) => patch.assignments.map((assignment, assignment_index) => ({
    operation: 'upsert', block_key: patch.block_key, assignment_index, manual: false,
    change_kind: patch.change_kind, resolution_state: patch.resolution_state,
    lesson: {
      activity: assignment.activity, weekday: patch.weekday,
      start_time: patch.slot.start, end_time: patch.slot.end, grade: patch.grade_scope,
      teacher_ids: assignment.teacher_ids || [], teacher_names: assignment.metadata?.teachers || [],
      room: assignment.metadata?.room || '', source_cells: patch.weekly_source_cells,
      source_identity: { semantic_fingerprint: `local-e2e-${patch.block_key}-${assignment_index}` },
      audience: assignment.audience_rule,
    },
  })));
  const unresolvedPatch: PreviewPatch = {
    block_key: '2026-09-21|3|13:40|8', assignments: [], change_kind: 'replaced',
    resolution_state: 'UNRESOLVED', weekday: 3,
    slot: { start: '13:40', end: '14:25' }, grade_scope: '8',
    weekly_source_cells: ['H18'], source_issue: 'Состав учеников не подтверждён',
    resolution_details: ['student is missing from canonical membership'],
    before: { assignments: [{ activity: 'Литература' }] },
    after: { source_text: ['Литература, группа не определена'] },
  };
  fixture.overlay_patches.push(unresolvedPatch);
  fixture.overlay_patch_count = fixture.overlay_patches.length;
  fixture.draft_changes.push({
    operation: 'upsert', block_key: unresolvedPatch.block_key, assignment_index: 0,
    manual: false, change_kind: unresolvedPatch.change_kind, resolution_state: 'UNRESOLVED',
    review_required: true, review_reason: unresolvedPatch.source_issue,
    lesson: {
      activity: 'Литература', weekday: unresolvedPatch.weekday,
      start_time: unresolvedPatch.slot.start, end_time: unresolvedPatch.slot.end, grade: unresolvedPatch.grade_scope,
      teacher_ids: [], teacher_names: [], room: '', source_cells: unresolvedPatch.weekly_source_cells,
      source_identity: { semantic_fingerprint: `local-e2e-${unresolvedPatch.block_key}` },
      audience: { kind: 'unresolved', grade: '8' },
    },
  });

  // All read-only Google preview calls are fulfilled locally. No Google URL is
  // reachable from these tests; draft/save/publish/readback still use the API.
  await page.route('**/api/admin/schedule/template/preview', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'preview', overlay_patch_count: 0, draft_changes: [], selected_tab: { title: 'Local E2E template fixture' } }),
  }));
  await page.route('**/api/admin/schedule/preview?*', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) }));

  const observedApi: Array<{ url: string; status: number }> = [];
  page.on('response', (response) => {
    if (response.url().startsWith(API)) observedApi.push({ url: response.url(), status: response.status() });
  });
  await page.goto('/admin/schedule?role=admin');
  expect(new URL(page.url()).hostname).toBe('localhost');
  await expect(page.getByRole('heading', { name: 'Расписание' })).toBeVisible();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await expect.poll(() => observedApi.filter((item) => item.url.includes('/api/admin/schedule/observability') && item.status === 200).length).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Шаблон', exact: true }).click();
  await expect(page.getByText(/Шаблон local-e2e-template/)).toBeVisible();
  await page.locator('.studio-layer-switch').getByRole('button', { name: 'Неделя', exact: true }).click();
  await page.getByRole('button', { name: /Импорт из Google Sheets/ }).click();
  await expect(page.getByRole('heading', { name: 'Проверка недели' })).toBeVisible();
  await expect(page.getByText('Local E2E fixture · week')).toBeVisible();
  await expect(page.getByText('Найдено изменений источника: 4')).toBeVisible();
  await expect(page.getByText(/Математика · группа 7-1/)).toBeVisible();
  await expect(page.getByText(/Стало: Английский · 10–11/)).toBeVisible();
  await page.getByRole('button', { name: 'Отложить до уточнения' }).click();
  await expect(page.getByText('Отложено', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Добавить предложения в черновик/ })).toBeEnabled();
  await page.getByRole('button', { name: /Добавить предложения в черновик/ }).click();
  await expect(page.getByRole('heading', { name: 'Изменения добавлены в черновик' })).toBeVisible();
  await page.getByRole('button', { name: 'Продолжить' }).click();
  await expect(page.getByText(/Черновик · 7 изменений/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Опубликовать изменения/ })).toBeEnabled();

  const mathACard = page.locator('[data-lesson-card]').filter({ hasText: '9-A' }).first();
  await expect(mathACard).toBeVisible();
  await mathACard.click();
  await expect(page.getByLabel('Преподаватель')).toHaveValue('Тестовый преподаватель');
  await expect(page.getByLabel('Кабинет')).toHaveValue('17');
  await page.locator('.studio-inspector-tabs').getByRole('button', { name: /Ученики/ }).click();
  await expect(page.getByText('Тестовый ученик 9A')).toBeVisible();
  await expect(page.getByText(/1 учеников/)).toBeVisible();

  await page.reload();
  await expect(page.getByText(/Черновик · 7 изменений/)).toBeVisible();
  await expect(page.locator('[data-lesson-card]').filter({ hasText: '9-A' }).first()).toBeVisible();
  await page.getByRole('button', { name: /Опубликовать изменения/ }).click();
  await expect(page.getByRole('heading', { name: 'Новая версия недели' })).toBeVisible();
  await page.getByRole('button', { name: 'Опубликовать версию' }).click();
  await expect(page.getByText(/Корректная часть опубликована/)).toBeVisible({ timeout: 30_000 });

  const [draftResponse, scheduleResponse] = await Promise.all([
    page.request.get(`${API}/api/admin/schedule/draft?scope_kind=week&scope_key=2026-09-21`, { headers: ADMIN }),
    page.request.get(`${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`, { headers: ADMIN }),
  ]);
  expect(draftResponse.status()).toBe(200);
  expect(scheduleResponse.status()).toBe(200);
  const draft = await draftResponse.json();
  const schedule = await scheduleResponse.json();
  expect(draft.has_changes).toBe(true);
  expect(draft.payload.changes).toHaveLength(1);
  expect(draft.payload.changes[0].deferred).toBe(true);
  expect(schedule.effective_week.effective_week_id).toBeTruthy();
  const publishedBlocks = schedule.blocks as Array<{
    block_key: string;
    effective_assignments?: Array<{ activity: string; canonical_group_ids?: string[] }>;
  }>;
  const expectedMathGroups = fixture.overlay_patches
    .filter((patch) => patch.assignments.some((item) => item.activity === 'Математика'))
    .flatMap((patch) => patch.assignments
      .filter((item) => item.activity === 'Математика')
      .flatMap((item) => item.canonical_group_ids || []))
    .sort();
  const targetedBlockKeys = new Set(fixture.overlay_patches.map((patch) => patch.block_key));
  const actualMathGroups = publishedBlocks
    .filter((block) => targetedBlockKeys.has(block.block_key))
    .flatMap((block) => block.effective_assignments || [])
    .filter((item) => item.activity === 'Математика')
    .flatMap((item) => item.canonical_group_ids || [])
    .sort();
  // Compare only the fixture's published blocks, leaving unrelated baseline lessons out.
  expect(actualMathGroups).toEqual(expectedMathGroups);
  const expectedEnglishGroups = fixture.overlay_patches
    .flatMap((patch) => patch.assignments)
    .filter((item) => item.activity === 'Английский')
    .flatMap((item) => item.canonical_group_ids || [])
    .sort();
  const englishBlock = publishedBlocks.find((block) => block.block_key === '2026-09-21|2|12:45|10,11');
  const actualEnglishGroups = (englishBlock?.effective_assignments || [])
    .filter((item) => item.activity === 'Английский')
    .flatMap((item) => item.canonical_group_ids || [])
    .sort();
  expect(actualEnglishGroups).toEqual(expectedEnglishGroups);
  expect((schedule.blocks as Array<{ block_key: string }>).some((block) => block.block_key === unresolvedPatch.block_key)).toBe(false);
});

test('Template: review before→after, publish locally, preserve other assignments and clear confirmed-source badge', async ({ page }) => {
  const [groupsResponse, teachersResponse, scheduleResponse] = await Promise.all([
    page.request.get(`${API}/api/admin/groups`, { headers: ADMIN }),
    page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN }),
    page.request.get(`${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`, { headers: ADMIN }),
  ]);
  expect(groupsResponse.status()).toBe(200);
  expect(teachersResponse.status()).toBe(200);
  expect(scheduleResponse.status()).toBe(200);
  const groups = (await groupsResponse.json()).items as Array<{ id: string; name: string; display_name: string }>;
  const teachers = (await teachersResponse.json()).items as Array<{ id: string; display_name: string }>;
  const group = groups.find((item) => item.name === 'Math 7-1' || item.display_name === 'Math 7-1');
  const teacher = teachers.find((item) => item.display_name === 'Тестовый преподаватель');
  if (!group || !teacher) throw new Error('Local template fixture directory is incomplete');
  const groupId = String(group.id);
  const teacherId = String(teacher.id);
  const canonical = (await scheduleResponse.json()).canonical;
  const blockKey = '2026-09-21|1|09:55|7';
  const fingerprint = 'local-e2e-template-source-v2';
  const assignment = {
    activity: 'История', canonical_group_ids: [groupId], teacher_ids: [teacherId],
    audience_rule: { kind: 'groups', grade: '7', group_ids: [groupId] },
    metadata: { teachers: ['Тестовый преподаватель'], room: '18' },
  };
  const patch = {
    block_key: blockKey, change_kind: 'replaced', change_classification: 'REPLACED',
    resolution_state: 'AUTO_RESOLVED', weekday: 1, slot: { start: '09:55', end: '10:40' }, grade_scope: '7',
    weekly_source_cells: ['F10'], before: { assignments: [{ activity: 'Математика', group_ids: [groupId] }] },
    after: { assignments: [{ activity: 'История', group_ids: [groupId] }] }, assignments: [assignment],
  };
  const draftChange = {
    operation: 'upsert', block_key: blockKey, assignment_index: 0, manual: false,
    change_kind: 'replaced', resolution_state: 'AUTO_RESOLVED',
    lesson: { activity: 'История', weekday: 1, start_time: '09:55', end_time: '10:40', grade: '7',
      teacher_ids: [teacherId], teacher_names: ['Тестовый преподаватель'], room: '18', source_cells: ['F10'],
      source_identity: { semantic_fingerprint: fingerprint }, audience: { kind: 'groups', grade: '7', group_ids: [groupId] } },
  };
  const preview = {
    status: 'preview', overlay_patch_count: 1, confirmed_canonical_version_id: canonical.version_id,
    source_fingerprint: fingerprint, selected_tab: { title: 'Local E2E template fixture', sheet_id: 'fixture-only' },
    overlay_patches: [patch], draft_changes: [draftChange],
  };
  let publishedInFixture = false;
  await page.route('**/api/admin/schedule/template/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(publishedInFixture ? {
    status: 'preview', overlay_patch_count: 0, confirmed_canonical_version_id: canonical.version_id,
    selected_tab: preview.selected_tab, overlay_patches: [], draft_changes: [],
  } : preview) }));
  // Keep the real SQLite publication/readback, but simulate a successful
  // source confirmation response because local-test deliberately has no Google access.
  await page.route('**/api/admin/schedule/draft/publish', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.source_baseline = { status: 'confirmed' };
    publishedInFixture = true;
    await route.fulfill({ response, body: JSON.stringify(body) });
  });

  await page.goto('/admin/schedule?role=admin');
  await expect(page.getByRole('heading', { name: 'Расписание' })).toBeVisible();
  await page.getByRole('button', { name: 'Шаблон', exact: true }).click();
  await page.getByRole('button', { name: /Импорт из Google Sheets/ }).click();
  await expect(page.getByRole('heading', { name: 'Проверка шаблона' })).toBeVisible();
  await expect(page.getByText('Найдено изменений источника: 1')).toBeVisible();
  await expect(page.getByText(/Было: Математика/)).toBeVisible();
  await expect(page.getByText(/Стало: История/)).toBeVisible();
  await page.getByRole('button', { name: /Добавить предложения в черновик/ }).click();
  await expect(page.getByRole('heading', { name: 'Изменения добавлены в черновик' })).toBeVisible();
  await page.getByRole('button', { name: 'Продолжить' }).click();
  await expect(page.getByText(/Черновик · 1 изменений/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Шаблон', exact: true }).click();
  await expect(page.getByText(/Черновик · 1 изменений/)).toBeVisible();
  await page.getByRole('button', { name: /Опубликовать изменения/ }).click();
  await page.getByRole('button', { name: 'Опубликовать версию' }).click();
  await expect(page.getByText(/Опубликовано и проверено повторным чтением/)).toBeVisible({ timeout: 30_000 });

  const [draftReadback, canonicalReadback] = await Promise.all([
    page.request.get(`${API}/api/admin/schedule/draft?scope_kind=template&scope_key=${encodeURIComponent(canonical.version_id)}`, { headers: ADMIN }),
    page.request.get(`${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`, { headers: ADMIN }),
  ]);
  expect(draftReadback.status()).toBe(200);
  expect(canonicalReadback.status()).toBe(200);
  expect((await draftReadback.json()).has_changes).toBe(false);
  const after = await canonicalReadback.json();
  expect(after.canonical.version_id).not.toBe(canonical.version_id);
  const baselineBlocks = after.blocks as Array<{ block_key: string; baseline_assignments?: Array<{ activity: string }> }>;
  expect(baselineBlocks.find((item) => item.block_key === blockKey)?.baseline_assignments?.[0].activity).toBe('История');
  expect(baselineBlocks.find((item) => item.block_key === '2026-09-21|2|12:45|10,11')?.baseline_assignments?.[0].activity).toBe('Английский');

  // The local environment intentionally has no Google connection. The publish
  // response simulates successful source rebaseline; the backend's real source
  // mapping and identical-repeat zero-diff path are covered by its service test.
  await expect(page.getByRole('button', { name: /Изменения в источнике/ })).toHaveCount(0);
});
