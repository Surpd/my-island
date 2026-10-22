// Browser acceptance uses the checksum-matched rev45 structure with anonymous
// directory labels. All writes and all external requests are intercepted.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { directory, payload } from './historical-fixture.mjs';

const auth = JSON.parse(await fs.readFile('.local-test/consolidation/auth.json', 'utf8'));
assert.equal(auth.synthetic, true);
assert.equal(payload.lessons.length, 386);
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: 'ru-RU' });
    await context.addInitScript(token => {
      sessionStorage.setItem('my_island_admin_session', token);
      sessionStorage.setItem('my_island_school_admin_session', token);
    }, auth.admin_browser_session);
    const primary = { id: 'selected-rev45', name: 'ШКОЛА · Реальный шаблон 2026/27 · На проверке', revision: 45, updated_at: '2026-10-20T06:12:00Z', payload: structuredClone(payload) };
    const week = { id: 'saved-week', name: 'Неделя 2026-10-26', revision: 2, payload: { ...structuredClone(payload), week_start: '2026-10-26' } };
    const test = { id: 'test-week', name: 'ТЕСТ ВОССТАНОВЛЕНИЯ · 2026-11-02', revision: 1, payload: { ...structuredClone(payload), week_start: '2026-11-02' } };
    const records = [primary, week, test];
    const effective = ['2026-09-07', '2026-09-14', '2026-09-14', '2026-09-14', '2026-09-14', '2026-09-21'].map((date, i) => ({ effective_week_id: `effective-${i}`, version_id: `canonical-${i}`, week_start: date, status: 'materialized', created_at: `${date}T12:00:00Z`, is_current_publication: false }));
    const writes = [], errors = [];
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      if (url.protocol.startsWith('http') && !['localhost', '127.0.0.1'].includes(url.hostname)) return route.abort();
      let result;
      if (path.endsWith('/experiments/capabilities')) result = { demo_available: false, school_available: true, consolidated_schedule: true };
      else if (path.endsWith('/experiments/school/directory')) result = directory;
      else if (path.endsWith('/experiments/school')) result = { items: records.map(item => ({ ...item, payload: undefined, draft_kind: 'school', is_primary_template: item.id === primary.id, week_start: item.payload.week_start, lesson_count: item.payload.lessons.length, event_count: 0 })) };
      else if (path.endsWith('/observability')) result = url.searchParams.has('effective_week_id') ? { blocks: [{ block_key: 'history', weekday: 0, start_time: '09:00', end_time: '09:45', effective_assignments: [{ activity: 'Историческое занятие', groups: ['9 класс'], teachers: ['Преподаватель 01'], room: '101' }] }] } : { canonical: { version_id: 'published-independent-base', status: 'authoritative' }, effective_weeks: effective, week_publications: [], blocks: [] };
      else if (path.endsWith('/publications')) result = { items: [], heads: {} };
      else if (path.endsWith('/teacher-ui-colors')) result = { colors: [] };
      else if (path.endsWith('/activities')) result = { items: [] };
      else if (path.endsWith('/audience-preview')) result = { lessons: {} };
      else if (path.endsWith(`/${primary.id}/week`)) {
        const body = request.postDataJSON();
        assert.equal(body.expected_revision, 45);
        const created = { id: `created-${width}`, name: `Неделя ${body.week_start}`, revision: 1, payload: { ...structuredClone(primary.payload), week_start: body.week_start, source_template: { id: primary.id, revision: 45 } } };
        records.push(created); result = created; writes.push(path);
      } else if (path.includes('/experiments/school/')) result = records.find(item => path.endsWith(`/${item.id}`));
      if (result !== undefined) return route.fulfill({ json: result });
      if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) {
        writes.push(`unexpected ${request.method()} ${path}`);
        return route.fulfill({ status: 403, json: { detail: 'Unexpected write blocked in acceptance test' } });
      }
      if (url.port === '8041') {
        if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': 'http://localhost:3042', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET, POST, OPTIONS' } });
        const response = await route.fetch();
        return route.fulfill({ response, headers: { ...response.headers(), 'access-control-allow-origin': 'http://localhost:3042' } });
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://localhost:3042/admin#/admin/schedule/constructor');
    await page.getByRole('button', { name: 'Открыть и редактировать', exact: true }).waitFor().catch(async error => {
      console.log({ errors, writes, body: (await page.locator('body').innerText()).slice(0, 3000) });
      await page.screenshot({ path: '.local-test/consolidation/results/school-start-failure.png', fullPage: true });
      throw error;
    });
    await page.screenshot({ path: `.local-test/consolidation/results/school-template-${width}.png`, fullPage: true });
    assert.equal(await page.getByRole('navigation', { name: 'Разделы расписания' }).getByRole('button').count(), 2);
    assert.equal(await page.locator('.constructor-school-start').getByText(primary.name, { exact: true }).count(), 1);
    await page.getByRole('button', { name: 'Открыть и редактировать', exact: true }).click();
    await page.locator('.constructor-card').first().waitFor();
    assert.equal(await page.locator('.constructor-teacher-code').count(), 0);
    await page.locator('.constructor-card').first().click();
    await page.locator('.constructor-inspector label').filter({ hasText: /^\s*Преподаватель/ }).first().waitFor();
    if (width < 900) await page.getByRole('button', { name: 'Закрыть панель', exact: true }).click();
    await page.getByRole('button', { name: 'К основному шаблону', exact: true }).click();
    await page.getByRole('navigation', { name: 'Разделы расписания' }).getByRole('button', { name: /Недели/ }).click();
    assert.equal(await page.locator('.constructor-week-row').count(), 4);
    assert.equal(await page.getByText('Историческая версия', { exact: true }).count(), 3);
    assert.equal(await page.getByText('Действующая неделя', { exact: true }).count(), 0);
    await page.locator('.constructor-week-row').filter({ hasText: '14 сентября' }).getByRole('button', { name: 'История', exact: true }).click();
    const history = page.getByRole('dialog', { name: /История/ });
    assert.equal(await history.getByRole('button', { name: 'Просмотреть', exact: true }).count(), 4);
    await history.getByRole('button', { name: 'Просмотреть', exact: true }).last().click();
    await page.getByText('Историческое занятие', { exact: false }).waitFor();
    await page.getByRole('button', { name: 'Закрыть просмотр недели' }).click();
    await page.getByRole('button', { name: 'Закрыть историю' }).click();
    await page.getByLabel('Неделя начинается', { exact: true }).fill('2026-10-26');
    assert(await page.getByRole('button', { name: 'Открыть существующий черновик', exact: true }).isVisible());
    assert.equal(await page.getByRole('button', { name: 'Создать из основного шаблона', exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Открыть существующий черновик', exact: true }).click();
    await page.getByRole('button', { name: 'К неделям', exact: true }).click();
    await page.getByLabel('Неделя начинается', { exact: true }).fill('2026-11-09');
    assert.match(await page.locator('.constructor-week-period').innerText(), /9 ноября — 15 ноября/);
    await page.getByRole('button', { name: 'Создать из основного шаблона', exact: true }).click();
    await page.locator('.constructor-card').first().waitFor();
    assert.equal(records.at(-1).payload.lessons.length, 386);
    assert.equal(primary.payload.week_start, undefined);
    await page.getByRole('button', { name: 'К неделям', exact: true }).click();
    await page.locator('.constructor-secondary-actions').filter({ has: page.locator('summary').filter({ hasText: /^Другие черновики и архив/ }) }).locator('summary').first().click();
    await page.getByText('Тестовые и скрытые записи · 1', { exact: true }).click();
    await page.getByRole('button', { name: test.name, exact: false }).waitFor();
    await page.getByRole('button', { name: 'Вернуть в список', exact: true }).click();
    assert.equal(await page.locator('.constructor-week-row').count(), 6);
    await page.screenshot({ path: `.local-test/consolidation/results/school-start-${width}.png`, fullPage: true });
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, [`/api/admin/schedule/experiments/school/${primary.id}/week`]);
    await context.close();
    console.log(`PASS ${width}px: rev45 open, return, historical versions, independent week, existing draft, reversible archive, no teacher codes; no external writes`);
  }
} finally { await browser.close(); }
