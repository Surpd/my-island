import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from '@playwright/test';

const auth = JSON.parse(await fs.readFile('.local-test/consolidation/auth.json', 'utf8'));
assert.equal(auth.synthetic, true);
assert.equal(auth.base_url, 'http://127.0.0.1:8041');
const browser = await chromium.launch();
const errors = [], writes = [];
try {
  for (const [width, height] of [[1920, 1080], [1366, 768], [390, 844]]) {
    const context = await browser.newContext({ viewport: { width, height } });
    await context.addInitScript(token => {
      sessionStorage.setItem('my_island_admin_session', token);
      sessionStorage.setItem('my_island_school_admin_session', token);
    }, auth.admin_browser_session);
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url());
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      if (!['localhost', '127.0.0.1'].includes(url.hostname)) return route.abort();
      if (url.port === '8041' && ['PUT', 'PATCH', 'DELETE', 'POST'].includes(request.method())) {
        if (request.method() !== 'POST' || !url.pathname.endsWith('/schedule/preview')) {
          writes.push(`${request.method()} ${url.pathname}`);
          return route.abort();
        }
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://localhost:3041/admin');
    await page.locator('.admin-console-sidebar').waitFor({ state: 'attached' });
    await page.goto('http://localhost:3041/admin#/admin/sources');
    await page.getByRole('heading', { name: 'Источник Google Sheets', exact: true }).waitFor();
    await page.getByText('Обновления Google Sheets требуют проверки; автоматическое применение приостановлено', { exact: true }).waitFor();
    await Promise.all([
      page.waitForResponse(response => response.url().includes('/schedule/preview')),
      page.getByRole('button', { name: 'Проверить изменения недели', exact: true }).click(),
    ]);
    await context.close();
    console.log(`PASS release shell/source hold ${width}x${height}`);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
} finally {
  await browser.close();
}
