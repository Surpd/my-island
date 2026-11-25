import { expect, test } from '@playwright/test';
import { randomBytes } from 'node:crypto';

test('diagnostic login loads the existing admin shell, never borrowing the human token', async ({ page }) => {
  const syntheticKey = randomBytes(32).toString('base64url');
  const diagnosticToken = 'mi_diag_' + randomBytes(32).toString('base64url');
  let revoked = false;
  const legacyRequests: string[] = [];
  const browserOrigin = 'http://localhost:3011';
  await page.addInitScript(() => {
    sessionStorage.setItem('my_island_admin_session', 'synthetic-unrelated-human-session');
  });
  await page.route('**/api/admin/**', request => {
    legacyRequests.push(new URL(request.request().url()).pathname);
    return request.abort();
  });
  await page.route('**/api/diagnostics/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      'access-control-allow-origin': browserOrigin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': 'Authorization, X-My-Island-Diagnostic-Key',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
    };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 200, headers });
    expect(request.url()).not.toContain(syntheticKey);
    expect(request.headers()['x-dev-auth']).toBeUndefined();
    expect(request.headers()['x-school-admin-session']).toBeUndefined();
    expect(request.headers()['cookie']).toBeUndefined();
    if (path.endsWith('/exchange')) {
      expect(request.headers()['x-my-island-diagnostic-key']).toBe(syntheticKey);
      return route.fulfill({ headers, json: { token: diagnosticToken, scope: 'diagnostic.read' } });
    }
    expect(request.headers().authorization).toBe('Bearer ' + diagnosticToken);
    if (path.endsWith('/logout')) {
      revoked = true;
      return route.fulfill({ headers, json: { ok: true } });
    }
    if (path.endsWith('/overview')) return route.fulfill({ headers, json: { canonical_students: 18, canonical_teachers: 3, groups: 2 } });
    if (path.endsWith('/system')) return route.fulfill({ headers, json: { health: { schema_status: 'current' }, settings: { database_configured: true } } });
    if (path.endsWith('/google/status')) return route.fulfill({ headers, json: { status: 'diagnostic_only', message: 'Учётная запись скрыта', reconnect_available: false } });
    if (path.endsWith('/journals')) return route.fulfill({ headers, json: { items: [{
      id: 'synthetic-diagnostic-source', spreadsheet_id: 'synthetic-diagnostic-book',
      spreadsheet_title: 'Fixture book', grade: 9, subject: 'Диагностический предмет',
      sheet_title: 'Fixture tab', status: 'ready', term_number: 2, period_status: 'current',
    }], known_sources: [] } });
    return route.fulfill({ headers, json: { items: [] } });
  });
  await page.goto('/admin/diagnostics');
  await page.getByLabel('Диагностический ключ').fill(syntheticKey);
  await page.getByRole('button', { name: 'Войти для диагностики' }).click();
  await expect(page.getByText(/Диагностика Codex · только чтение/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Расписание школы' })).toBeVisible();
  await page.getByRole('button', { name: 'Настройки', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Backend и школьная база' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Переподключить Google' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Журналы и оценки', exact: true }).click();
  await page.getByRole('tablist', { name: 'Класс', exact: true }).getByRole('tab', { name: /^9 класс/ }).click();
  await expect(page.getByRole('rowheader', { name: 'Диагностический предмет' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Диагностический предмет', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Подключить таблицу', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Диагностический вход' })).toBeVisible();
  expect(revoked).toBeTruthy();
  expect(legacyRequests).toEqual([]);
});

test('real diagnostic auth crosses origins and revokes without human privileges', async ({ page, request, baseURL }) => {
  const fixtureKey = process.env.MY_ISLAND_E2E_DIAGNOSTIC_KEY;
  test.skip(!fixtureKey, 'Enable the e2e runner’s disposable diagnostic credential.');
  const apiOrigin = 'http://127.0.0.1:8011';
  expect(baseURL).toBe('http://localhost:3011');
  const isolatedHumanAuth = { 'X-Dev-Auth': 'admin:1' };
  expect((await request.get(apiOrigin + '/api/admin/overview', { headers: isolatedHumanAuth })).status()).toBe(200);
  await page.goto('/admin/diagnostics');
  await page.getByLabel('Диагностический ключ').fill(fixtureKey!);
  await page.getByRole('button', { name: 'Войти для диагностики' }).click();
  await expect(page.getByText(/Диагностика Codex · только чтение/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Расписание школы' })).toBeVisible();

  const bearer = await page.evaluate(() => sessionStorage.getItem('my_island_codex_read_session'));
  expect(Boolean(bearer)).toBeTruthy();
  const authorization = { Authorization: 'Bearer ' + bearer };
  for (const [method, path] of [
    ['POST', '/api/admin/journals/connect'],
    ['GET', '/api/admin/schedule/experiments/school/example/export/link'],
    ['GET', '/api/admin/previews/people'],
    ['GET', '/api/diagnostics/view/people?kind=student'],
    ['PUT', '/api/diagnostics/view/schedule/experiments/school/example'],
  ]) {
    const result = await request.fetch(apiOrigin + path, { method, headers: { ...isolatedHumanAuth, ...authorization } });
    expect([403, 405]).toContain(result.status());
  }
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Диагностический вход' })).toBeVisible();
  expect((await request.get(apiOrigin + '/api/diagnostics/session', { headers: authorization })).status()).toBe(401);
  expect((await request.get(apiOrigin + '/api/admin/overview', { headers: isolatedHumanAuth })).status()).toBe(200);
});
