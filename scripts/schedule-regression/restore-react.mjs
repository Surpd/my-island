import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const results = '.local-test/consolidation/results';
const constructorUrl = 'http://localhost:3041/admin/schedule/constructor';
const auth = JSON.parse(await fs.readFile('.local-test/consolidation/auth.json', 'utf8'));
const fixture = JSON.parse(await fs.readFile('.local-test/consolidation/fixture.json', 'utf8'));
assert.equal(auth.base_url, 'http://127.0.0.1:8041');
assert.equal(auth.synthetic && fixture.synthetic, true);
assert(fixture.template_publication_draft_id, 'Run integration_flow.py first');

await fs.mkdir(`${results}/screenshots`, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
const errors = [];
const steps = [];
const report = { steps, errors, transport: 'Real React + HTTP publication/restore; no mocks' };
await context.addInitScript(token => {
  sessionStorage.setItem('my_island_admin_session', token);
  sessionStorage.setItem('my_island_school_admin_session', token);
}, auth.admin_browser_session);
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  return ['localhost', '127.0.0.1'].includes(url.hostname) && ['3041', '8041'].includes(url.port)
    ? route.continue() : route.abort();
});
const page = await context.newPage();
page.on('pageerror', error => errors.push(error.message));
const shot = name => page.screenshot({ path: `${results}/screenshots/${name}.png` });

try {
  await page.goto(`${constructorUrl}?draft=${fixture.template_publication_draft_id}`);
  await page.locator('.constructor-grid-wrap').waitFor();
  await page.locator('.constructor-draft-menu > summary').click();
  await page.getByRole('button', { name: 'История публикаций', exact: true }).click();
  const history = page.getByRole('dialog', { name: 'История публикаций' });
  await history.getByRole('button', { name: 'Проверить восстановление', exact: true }).last().click();
  const restore = history.getByRole('region', { name: 'Проверка восстановления' });
  await restore.waitFor();
  await shot('restore-preview-1366');
  const submit = restore.getByRole('button', { name: 'Подтвердить восстановление', exact: true });
  assert(await submit.isDisabled());
  await restore.getByRole('checkbox', { name: /Подтверждаю замену/ }).check();
  await submit.click();
  await history.getByText(/Создана новая восстановленная версия расписания. Чтение: подтверждено/).waitFor();
  await shot('restore-confirmed-1366');
  steps.push('Publication history → restore preview → explicit confirmation → new version with runtime readback');
  await history.getByRole('button', { name: 'Закрыть историю публикаций', exact: true }).click();

  const response = await fetch(`${auth.base_url}/api/admin/schedule/experiments/school`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-School-Admin-Session': auth.admin_browser_session,
      'X-Admin-Browser-Session': auth.admin_browser_session },
    body: JSON.stringify({ name: 'Synthetic draft after restore', source: 'canonical' }),
  });
  if (!response.ok) throw new Error(`Could not create isolated copy: ${await response.text()}`);
  const draft = await response.json();
  await page.goto(`${constructorUrl}?draft=${draft.id}`);
  await page.locator('.constructor-grid-wrap').waitFor();
  await page.locator('.constructor-card').first().dblclick();
  await page.getByLabel('Кабинет', { exact: true }).fill('AFTER-RESTORE-2026');
  await page.getByRole('button', { name: 'Закрыть панель', exact: true }).click();
  const [save] = await Promise.all([
    page.waitForResponse(result => result.request().method() === 'PUT' && result.url().endsWith(draft.id)),
    page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click(),
  ]);
  assert(save.ok(), await save.text());
  steps.push('Opened a new draft from restored published template and saved an edit');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Проверить расписание', exact: true }).click();
  const review = page.getByRole('dialog', { name: 'Проверка расписания' });
  await review.getByRole('button', { name: 'Публикация', exact: true }).click();
  const confirmation = review.getByRole('checkbox', { name: /Подтверждаю публикацию/ });
  await confirmation.waitFor();
  await shot('publication-preview-390');
  await confirmation.check();
  await review.getByRole('button', { name: /Опубликовать.*шаблон/ }).click();
  await review.getByText(/Проверка после публикации пройдена/).waitFor();
  await shot('publication-confirmed-390');
  steps.push('Mobile schedule review → separately confirmed publication → verified readback');
  assert.deepEqual(errors, []);
  report.status = 'PASS';
} catch (error) {
  report.status = 'FAIL';
  report.failure = String(error.stack || error);
  await shot('restore-failure');
  throw error;
} finally {
  await fs.writeFile(`${results}/react-restore-report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}
