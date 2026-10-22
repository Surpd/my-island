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
    expect(writes).toEqual([]);
  });
}

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
