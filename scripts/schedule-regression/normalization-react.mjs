import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const root = '.local-test/consolidation/results';
const auth = JSON.parse(await fs.readFile('.local-test/consolidation/auth.json', 'utf8'));
const fixture = JSON.parse(await fs.readFile('.local-test/consolidation/fixture.json', 'utf8'));
const api = auth.base_url;
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
const selection = await call(`/api/admin/schedule/experiments/school/${candidate.id}/selection-preview`, 'POST', {});
await call(`/api/admin/schedule/experiments/school/${candidate.id}/select-primary`, 'POST', {
  expected_revision: selection.candidate.revision,
  expected_current_id: selection.current?.id || null,
  expected_current_revision: selection.current?.revision || null,
  expected_candidate_hash: selection.candidate_hash,
  acknowledge_problems: true,
});

await fs.mkdir(`${root}/screenshots`, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
await context.addInitScript(token => { sessionStorage.setItem('my_island_admin_session', token); sessionStorage.setItem('my_island_school_admin_session', token); }, auth.admin_browser_session);
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  return ['localhost', '127.0.0.1'].includes(url.hostname) && ['3041', '8041'].includes(url.port) ? route.continue() : route.abort();
});
const page = await context.newPage();
const errors = []; page.on('pageerror', error => errors.push(error.message));
page.on('dialog', dialog => void dialog.accept());
try {
  await page.goto('http://localhost:3041/admin#/admin/schedule/constructor');
  await page.getByRole('button', { name: 'Проверить и обновить старые записи', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Проверить и обновить старые записи', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверить и обновить старые записи' });
  await dialog.waitFor();
  assert((await dialog.innerText()).includes('8'));
  assert((await dialog.innerText()).includes('source:B29:electives'));
  assert((await dialog.innerText()).includes('source:F32:electives'));
  assert((await dialog.innerText()).includes('не меняются'));
  assert((await dialog.innerText()).includes('Сохранённые предупреждения · 1'), await dialog.innerText());
  await dialog.getByText('Сохранённые предупреждения · 1', { exact: true }).click();
  assert((await dialog.innerText()).includes('Уточните охват обеда: класс или отдельная группа'));
  await dialog.getByRole('button', { name: 'Открыть занятие', exact: true }).click();
  await page.locator('.constructor-grid-wrap').waitFor();
  await page.getByRole('button', { name: 'Черновик', exact: true }).click().catch(() => {});
  const summary = page.locator('.constructor-draft-menu > summary');
  if (await summary.count()) { await summary.click(); await page.getByRole('button', { name: /К шаблону и неделям/ }).click(); }
  await page.getByRole('button', { name: 'Проверить и обновить старые записи', exact: true }).click();
  const reopenedDialog = page.getByRole('dialog', { name: 'Проверить и обновить старые записи' });
  await reopenedDialog.waitFor();
  await reopenedDialog.getByText('Сохранённые предупреждения · 1', { exact: true }).click();
  await page.screenshot({ path: `${root}/screenshots/normalization-preview-1366.png` });
  await reopenedDialog.getByRole('button', { name: 'Подтвердить и создать отдельную копию' }).click();
  await page.getByRole('dialog').waitFor({ state: 'detached' });
  const copyNotice = page.getByRole('alert').filter({ hasText: 'Создана отдельная копия' });
  await copyNotice.waitFor();
  const noticeText = await copyNotice.innerText();
  assert(noticeText.includes('типы курсов — 8'));
  assert(noticeText.includes('снятые предупреждения преподавателей — 0'));
  const drafts = await call('/api/admin/schedule/experiments/school');
  const copy = drafts.items.find(item => item.name === 'Normalization E2E synthetic source · нормализованная копия');
  assert(copy); assert.equal(copy.is_primary_template, false);
  const normalized = await call(`/api/admin/schedule/experiments/school/${copy.id}`);
  assert.equal(normalized.payload.lessons.length, sourcePayload.lessons.length);
  assert.equal(normalized.payload.lessons.filter(item => item.blockId?.endsWith(':electives') && item.activityType === 'elective').length, 8);
  assert.equal(normalized.payload.lessons.find(item => item.id === 'normalization-lunch-warning').needsClarification, true);
  assert.deepEqual((await call(`/api/admin/schedule/experiments/school/${candidate.id}`)).payload.lessons, sourcePayload.lessons);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: `${root}/screenshots/normalization-copy-created-1366.png` });
  console.log(JSON.stringify({ status: 'PASS', preview_changes: 8, retained_lunch_warning: true, copied_lesson_count: normalized.payload.lessons.length, source_preserved: true, published: false, screenshots: ['normalization-preview-1366.png', 'normalization-copy-created-1366.png'] }));
} finally { await context.close(); await browser.close(); }
