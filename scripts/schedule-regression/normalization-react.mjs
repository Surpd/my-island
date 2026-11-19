import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const root = '.local-test/consolidation/results';
const auth = JSON.parse(await fs.readFile('.local-test/consolidation/auth.json', 'utf8'));
const fixture = JSON.parse(await fs.readFile('.local-test/consolidation/fixture.json', 'utf8'));
const api = auth.base_url;
assert.equal(api, 'http://127.0.0.1:8041');
assert.equal(auth.synthetic && fixture.synthetic, true);
const headers = { 'Content-Type': 'application/json', 'X-School-Admin-Session': auth.admin_browser_session,
  'X-Admin-Browser-Session': auth.admin_browser_session };
async function call(path, method = 'GET', body) {
  const response = await fetch(api + path, body === undefined ? { method, headers } : { method, headers, body: JSON.stringify(body) });
  const data = await response.json(); assert(response.ok, `${method} ${path}: ${response.status} ${JSON.stringify(data)}`); return data;
}

const current = await call(`/api/admin/schedule/experiments/school/${fixture.school_template_selection_id}`);
const sourcePayload = structuredClone(current.payload);
for (const [blockId, prefix] of [['source:B29:electives', 'B29'], ['source:F32:electives', 'F32']]) {
  for (let index = 0; index < 4; index++) sourcePayload.lessons.push({
    id: `normalization-${prefix}-${index}`, subject: `${prefix} lane ${index + 1}`,
    blockId, blockName: 'Курсы по выбору', day: index % 5, period: 6,
    classId: fixture.classes['7-А synthetic'], classIds: [fixture.classes['7-А synthetic']],
    teacherId: fixture.teachers[index], teacherIds: [fixture.teachers[index]], room: `N-${index + 1}`,
  });
}
sourcePayload.lessons.push({ id: 'normalization-lunch-warning', subject: 'Обед', specialMark: 'lunch',
  day: 0, period: 5, classId: fixture.classes['7-А synthetic'], classIds: [fixture.classes['7-А synthetic']],
  needsClarification: true, clarificationReason: 'Уточните охват обеда: класс или отдельная группа' });
const candidate = await call('/api/admin/schedule/experiments/school', 'POST', {
  name: 'Normalization E2E synthetic source', source: 'import', payload: sourcePayload,
});
const savedSource = await call(`/api/admin/schedule/experiments/school/${candidate.id}`);
const selection = await call(`/api/admin/schedule/experiments/school/${candidate.id}/selection-preview`, 'POST', {});
await call(`/api/admin/schedule/experiments/school/${candidate.id}/select-primary`, 'POST', {
  expected_revision: selection.candidate.revision,
  expected_current_id: selection.current?.id || null,
  expected_current_revision: selection.current?.revision || null,
  expected_candidate_hash: selection.candidate_hash,
  acknowledge_problems: true,
});

await fs.mkdir(`${root}/screenshots`, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
await context.addInitScript(token => { sessionStorage.setItem('my_island_admin_session', token); sessionStorage.setItem('my_island_school_admin_session', token); }, auth.admin_browser_session);
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  return ['localhost', '127.0.0.1'].includes(url.hostname) && ['3041', '8041'].includes(url.port) ? route.continue() : route.abort();
});
const page = await context.newPage();
const errors = []; page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto('http://localhost:3041/admin#/admin/schedule/constructor');
  const review = page.getByRole('button', { name: 'Проверить расписание', exact: true });
  await review.click();
  const dialog = page.getByRole('dialog', { name: 'Проверка расписания' });
  const legacy = dialog.getByRole('region', { name: 'Старые записи' });
  const openLegacy = async () => {
    await dialog.getByText(/^Безопасные исправления/).click();
    await legacy.getByText('Сохранённые предупреждения · 1', { exact: true }).click();
  };
  await openLegacy();
  await legacy.getByText('Посмотреть исправления до и после · 8', { exact: true }).click();
  for (const subject of ['B29 lane 1', 'F32 lane 1']) await legacy.getByText(new RegExp(`^${subject} ·`)).waitFor();
  const warnings = legacy.locator('details').filter({ hasText: 'Сохранённые предупреждения' });
  assert((await warnings.innerText()).includes('Уточните охват обеда: класс или отдельная группа'));
  await warnings.getByRole('button', { name: 'Открыть занятие', exact: true }).click();
  await page.locator('.constructor-grid-wrap').waitFor();
  await page.getByRole('button', { name: 'Закрыть панель', exact: true }).click();
  await review.click();
  await openLegacy();
  await page.screenshot({ path: `${root}/screenshots/normalization-preview-1366.png` });
  const copyButton = legacy.getByRole('button', { name: 'Создать исправленную копию', exact: true });
  assert(await copyButton.isDisabled());
  await legacy.getByRole('checkbox', { name: /Применить предложения к отдельному черновику/ }).check();
  const [copyResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/normalization-copy')),
    copyButton.click(),
  ]);
  assert(copyResponse.ok());
  const copyResult = await copyResponse.json();
  assert.equal(copyResult.changed_type_count, 8);
  assert.equal(copyResult.cleared_teacher_warning_count, 0);
  const drafts = await call('/api/admin/schedule/experiments/school');
  const copy = drafts.items.find(item => item.name === 'Normalization E2E synthetic source · нормализованная копия');
  assert(copy); assert.equal(copy.is_primary_template, false);
  const normalized = await call(`/api/admin/schedule/experiments/school/${copy.id}`);
  assert.equal(normalized.payload.lessons.length, sourcePayload.lessons.length);
  assert.equal(normalized.payload.lessons.filter(item => item.blockId?.endsWith(':electives') && item.activityType === 'elective').length, 8);
  assert.equal(normalized.payload.lessons.find(item => item.id === 'normalization-lunch-warning').needsClarification, true);
  assert.deepEqual((await call(`/api/admin/schedule/experiments/school/${candidate.id}`)).payload.lessons, savedSource.payload.lessons);
  const renamed = await call(`/api/admin/schedule/experiments/school/${copy.id}`, 'PUT', {
    expected_revision: normalized.revision, name: 'Edited normalized copy QA', payload: normalized.payload,
  });
  const repeated = await call(`/api/admin/schedule/experiments/school/${candidate.id}/normalization-copy`, 'POST', copyResponse.request().postDataJSON());
  assert.equal(repeated.id, copy.id);
  assert.equal(repeated.revision, renamed.revision);
  assert.equal(repeated.name, renamed.name);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: `${root}/screenshots/normalization-copy-created-1366.png` });
  console.log(JSON.stringify({ status: 'PASS', preview_changes: 8, retained_lunch_warning: true, copied_lesson_count: normalized.payload.lessons.length, source_preserved: true, published: false, screenshots: ['normalization-preview-1366.png', 'normalization-copy-created-1366.png'] }));
} finally { await context.close(); await browser.close(); }
