import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

// The former Google ImportReview tests are archived alongside ScheduleAdmin.
// This suite now locks down retirement; constructor behavior has its own suites.
const consolidation = process.env.MY_ISLAND_CONSOLIDATION_E2E === '1';
const API = consolidation ? 'http://127.0.0.1:8041' : 'http://localhost:8011';
let admin: Record<string, string> = { 'X-Dev-Auth': 'admin:1' };
let token = '';

test.beforeAll(async () => {
  if (!consolidation) return;
  const auth = JSON.parse(await readFile('.local-test/consolidation/auth.json', 'utf8'));
  expect(auth.base_url).toBe(API);
  expect(auth.synthetic).toBe(true);
  token = auth.admin_browser_session;
  admin = { 'X-Admin-Browser-Session': token, 'X-School-Admin-Session': token };
});

test.beforeEach(async ({ page, baseURL }) => {
  expect(baseURL).toBe(consolidation ? 'http://localhost:3041' : 'http://localhost:3011');
  if (token) await page.addInitScript((session) => {
    sessionStorage.setItem('my_island_admin_session', session);
    sessionStorage.setItem('my_island_school_admin_session', session);
  }, token);
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
      ? route.continue() : route.abort();
  });
});

for (const route of ['/admin?role=admin#/admin/schedule', '/admin/schedule?role=admin']) {
  test(`legacy URL opens only ScheduleConstructor: ${route}`, async ({ page }) => {
    const writes: string[] = [];
    page.on('request', request => {
      if (['PUT', 'PATCH', 'DELETE'].includes(request.method()) && request.url().startsWith(API)) writes.push(request.url());
    });
    await page.goto(route);
    await expect(page.locator('.constructor')).toBeVisible();
    await expect(page.getByText('Старый редактор расписания', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Импорт из Google Sheets', { exact: true })).toHaveCount(0);
    await expect(page.getByText('История версий', { exact: true })).toHaveCount(0);
    expect(page.url()).toContain('/admin/schedule/constructor');
    expect(page.url()).toContain('role=admin');
    expect(writes).toEqual([]);
  });
}

test('opens an editable copy of the main template in the constructor', async ({ page }) => {
  await page.goto('/admin/schedule?role=admin');
  await expect(page.locator('.constructor-start')).toBeVisible();
  const createResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/admin/schedule/experiments')
      && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: /На основе действующего расписания/ }).click();
  expect((await createResponse).status()).toBe(200);
  await expect(page).toHaveURL(/\/admin\/schedule\/constructor\?role=admin$/);
  await expect(page.locator('.constructor-grid-wrap')).toBeVisible();
  await expect.poll(() => page.locator('.constructor-card').count()).toBeGreaterThan(0);
});

test('all five retired schedule mutations reject direct HTTP', async ({ request }) => {
  const cases = [
    { method: 'PUT', path: '/api/admin/schedule/draft', data: { scope_kind: 'week', scope_key: '2026-10-19', expected_revision: 0, payload: {} } },
    { method: 'DELETE', path: '/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-19&expected_revision=1' },
    { method: 'POST', path: '/api/admin/schedule/draft/publish', data: { scope_kind: 'week', scope_key: '2026-10-19', expected_revision: 1 } },
    { method: 'POST', path: '/api/admin/schedule/template/confirm', data: { expected_version_id: 'synthetic-only' } },
    { method: 'POST', path: '/api/admin/schedule/import-to-draft', data: { week_start: '2026-10-19', expected_revision: 0, proposed_changes: [], source_context: {} } },
  ];
  for (const item of cases) {
    const response = await request.fetch(API + item.path, { method: item.method, headers: admin, data: item.data });
    expect(response.status(), `${item.method} ${item.path}`).toBe(410);
  }
});

test('school save verifies a corrected stale audience without rewriting the neighboring lesson', async ({ page }) => {
  test.skip(!consolidation, 'Requires the isolated synthetic school workspace, not the demo API');
  const directoryResponse = await page.request.get(API + '/api/admin/schedule/experiments/school/directory', { headers: admin });
  expect(directoryResponse.ok()).toBeTruthy();
  const { groups, teachers } = await directoryResponse.json();
  const oldGroup = groups.find((group: { subject: string; group_type: string }) => group.subject === 'Математика' && group.group_type !== 'class');
  const targetClass = groups.find((group: { group_type: string; name: string; display_name?: string }) => group.group_type === 'class'
    && [group.name, group.display_name].includes(oldGroup?.base_class_name));
  expect(oldGroup).toBeTruthy();
  expect(targetClass).toBeTruthy();
  const classId = targetClass.id;
  const lessonId = 'synthetic-correct-stale-audience';
  const note = 'Для этого предмета не подтверждена учебная группа; прежняя математическая группа удалена';
  const unresolved = 'Неоднозначная исходная ячейка';
  const draftName = 'Синтетический тест · проверка состава после сохранения';
  const endpoint = '/api/admin/schedule/experiments/school';
  const teacherId = teachers[0].id;
  const common = { day: 0, kind: 'lesson', activityType: 'lesson', classId, classIds: [classId], teacherId, teacherIds: [teacherId], room: '17' };
  const created = await page.request.post(API + endpoint, { headers: admin, data: { name: draftName, source: 'empty', payload: {
    classes: [classId], days: [0], slots: [{ start: '09:00', end: '09:45' }, { start: '10:00', end: '10:45' }],
    lessons: [
      { ...common, id: lessonId, period: 0, subject: 'Русский язык', groupId: oldGroup.id, groupIds: [oldGroup.id],
        audience: { kind: 'groups', group_ids: [oldGroup.id] }, needsClarification: true, clarificationReason: note },
      { ...common, id: 'untouched', period: 1, subject: 'Физика', groupId: classId, groupIds: [classId],
        audience: { kind: 'classes', class_ids: [classId] }, needsClarification: true, clarificationReason: unresolved },
    ],
  } } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const draft = await created.json();
  const savePath = endpoint + '/' + draft.id;
  await page.goto(`/admin/schedule/constructor?${new URLSearchParams({ role: 'admin', draft: draft.id, lesson: lessonId })}`);
  const editor = page.locator('.constructor-inspector');
  await expect(editor.getByRole('heading', { name: 'Русский язык', exact: true })).toBeVisible();
  await editor.getByRole('button', { name: /Весь класс или параллель/ }).click();
  const response = page.waitForResponse(item => item.request().method() === 'PUT' && new URL(item.url()).pathname === savePath);
  await editor.getByRole('button', { name: 'Сохранить правку', exact: true }).click();
  const saved = await response;
  expect(saved.ok(), await saved.text()).toBeTruthy();
  expect(saved.request().postDataJSON().editor_audience_changes).toEqual([lessonId]);
  expect((await saved.json()).editor_validation).toEqual([expect.objectContaining({ lesson_id: lessonId, status: 'audience_verified' })]);
  const reopened = await page.request.get(API + savePath, { headers: admin });
  const { payload } = await reopened.json();
  expect(payload.lessons[0].needsClarification).not.toBe(true);
  expect(payload.lessons[0].clarificationReason || '').not.toContain('прежняя математическая группа удалена');
  expect(payload.lessons[0].audience_snapshot.student_ids.length).toBeGreaterThan(0);
  expect(payload.lessons[1]).toEqual(draft.payload.lessons[1]);
});
