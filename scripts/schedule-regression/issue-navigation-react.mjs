import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const frontend = 'http://localhost:3041';
const backend = 'http://127.0.0.1:8041';
const dataDir = '.local-test/consolidation';
const reportDir = 'docs/reports/schedule-ux';
const auth = JSON.parse(await readFile(dataDir + '/auth.json', 'utf8'));
const manifest = JSON.parse(await readFile(dataDir + '/fixture.json', 'utf8'));
const fixture = manifest.issue_navigation;
const before = process.argv.includes('--capture-before');
const capture = before || process.argv.includes('--capture');
const widths = process.env.ISSUE_UX_WIDTH ? [Number(process.env.ISSUE_UX_WIDTH)] : [1366, 390, 320];
assert.equal(auth.synthetic && manifest.synthetic && auth.base_url === backend, true);
assert.ok(fixture && fixture.revision === 3);
assert.ok(widths.every(width => [1366, 390, 320].includes(width)));
if (capture) await mkdir(reportDir, { recursive: true });

const headers = {
  'Content-Type': 'application/json',
  'X-Admin-Browser-Session': auth.admin_browser_session,
  'X-School-Admin-Session': auth.admin_browser_session,
};
const experiment = '/api/admin/schedule/experiments/school/';
const trace = [];
const results = [];
const shots = [];
const violations = [];
let step = 'initializing fixture';

async function readApi(path) {
  const response = await fetch(backend + path, { headers });
  assert.ok(response.ok, `Fixture API ${response.status}`);
  return response.json();
}

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || '/usr/bin/chromium',
  args: ['--disable-background-networking', '--disable-sync'],
});

function button(root, name) {
  return root.getByRole('button', { name, exact: typeof name === 'string' });
}

async function session(width) {
  const height = width === 1366 ? 768 : width === 390 ? 844 : 760;
  const context = await browser.newContext({
    viewport: { width, height }, deviceScaleFactor: 1, isMobile: width !== 1366,
    hasTouch: width !== 1366, serviceWorkers: 'block',
  });
  await context.addInitScript(token => {
    sessionStorage.setItem('my_island_admin_session', token);
    sessionStorage.setItem('my_island_school_admin_session', token);
  }, auth.admin_browser_session);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['data:', 'blob:'].includes(url.protocol) || [frontend, backend].includes(url.origin)) return route.continue();
    return route.abort();
  });
  await context.routeWebSocket('**/*', ws => {
    if (new URL(ws.url()).host === 'localhost:3041') return ws.connectToServer();
    return ws.close({ code: 1008, reason: 'Loopback test' });
  });
  context.on('request', request => {
    const url = new URL(request.url());
    if (url.origin !== backend || !url.pathname.startsWith('/api/')) return;
    const method = request.method();
    const kind = url.pathname.split('/').at(-1);
    trace.push({ method, kind });
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method) && ['publish', 'select-primary', 'rollback', 'export'].includes(kind)) {
      violations.push(kind);
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  return { context, page, errors };
}

async function screenshot(page, file) {
  if (!capture) return;
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: reportDir + '/' + file, animations: 'disabled' });
  shots.push(file);
}

async function openDraft(page) {
  await page.goto(frontend + '/admin/schedule/constructor?draft=' + fixture.id);
  await page.locator('.constructor-current-draft').getByText(fixture.name, { exact: true }).waitFor();
  await page.locator('.constructor-grid-wrap').waitFor();
}

async function baseline(page, width) {
  step = `before issues at ${width}`;
  await openDraft(page);
  const gridIssues = button(page, /Конфликты в сетке|В сетке/);
  await gridIssues.click();
  const panel = page.locator('.constructor-conflict-panel');
  await panel.getByText('Центр решений расписания', { exact: true }).waitFor();
  await panel.getByLabel('Тип проблемы').selectOption('room');
  const reason = panel.locator('.constructor-issue-reason > summary').filter({ hasText: /309/ }).first();
  await reason.click();
  await screenshot(page, `before-problems-${width}.png`);
  const roomIssue = reason.locator('..').getByRole('button', { name: 'Разобрать проблему' }).first();
  await roomIssue.click();
  const sub = page.getByRole('dialog').last();
  await sub.waitFor();
  await screenshot(page, `before-problem-detail-${width}.png`);
  results.push({ width, before: true, legacyFilters: 2, currentIssueRoute: false });
}

const routeSelector = '[data-issue-route]';
const inspection = '.constructor-inspector';
const grid = '.constructor-grid-wrap';
const draftURL = frontend + '/admin/schedule/constructor?draft=' + fixture.id;
const roomSubject = 'Информатика · кабинет';
const physics = 'Физика ЕГЭ';
const english = 'Английский ЕГЭ';

async function expectEventually(message, condition) {
  const until = Date.now() + 14000;
  while (Date.now() < until) {
    if (await condition()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(message);
}

async function fitDialog(page, dialog, width) {
  const box = await dialog.boundingBox();
  const height = page.viewportSize().height;
  assert.ok(box && box.x >= -1 && box.x + box.width <= width + 1
    && box.y >= -1 && box.y + box.height <= height + 1, 'Dialog fits the viewport');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page has no horizontal overflow');
  assert.ok(await dialog.evaluate(element => element.contains(document.activeElement)), 'Dialog contains the keyboard focus');
}

async function viewReview(page) {
  const preview = page.waitForResponse(response => response.url().endsWith('/publication/preview')
    && response.request().method() === 'POST');
  await button(page, 'Проверить расписание').click();
  const dialog = page.getByRole('dialog', { name: 'Проверка расписания' });
  await dialog.waitFor();
  assert.ok((await preview).ok(), 'Initial review completed successfully');
  await dialog.getByText(/^Проверяем расписание/).waitFor({ state: 'hidden' });
  await dialog.getByRole('searchbox', { name: 'Поиск по вопросам' }).waitFor();
  return dialog;
}

async function findConflict(dialog, query, first, second) {
  const search = dialog.getByRole('searchbox', { name: 'Поиск по вопросам' });
  await search.fill(query);
  const issue = dialog.locator('[data-review-issue]').filter({ hasText: first }).filter({ hasText: second });
  await issue.waitFor();
  assert.equal(await issue.count(), 1, 'Pair has one issue card');
  return { search, issue };
}

async function openIssueCard(issue, destination) {
  const showTargets = button(issue, 'Показать оба занятия');
  if (await showTargets.isVisible()) await showTargets.click();
  const direct = button(issue, destination ? `Показать в расписании — ${destination}` : /^Показать в расписании/);
  await direct.first().click();
}
async function inspectAt(page, id, other, width, targetDay) {
  const route = page.locator(routeSelector);
  await route.waitFor();
  const selected = page.locator(`[data-lesson-id="${id}"][data-issue-target="current"]`).first();
  await selected.waitFor();
  if (other) {
    await page.locator(`[data-lesson-id="${other}"][data-issue-target="related"]`).first().waitFor({ state: 'attached' });
    assert.ok(await route.getByRole('group', { name: 'Связанные занятия' }).count()
      || await route.locator('.constructor-route-lessons').count(), 'Linked lessons remain available in route context');
  }
  if (targetDay) {
    await expectEventually('Day switched', async () => (await button(page, targetDay).getAttribute('aria-pressed')) === 'true'
      || /active|selected/.test(await button(page, targetDay).getAttribute('class') || ''));
  }
  await expectEventually('Highlighted card has automatically scrolled into view', () => selected.evaluate(card => {
    const r = card.getBoundingClientRect();
    const g = card.closest('.constructor-grid-wrap').getBoundingClientRect();
    return Math.min(r.right, g.right, innerWidth) - Math.max(r.left, g.left, 0) > 15
      && Math.min(r.bottom, g.bottom, innerHeight) - Math.max(r.top, g.top, 0) > 12;
  }));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  if (width < 900) assert.equal(await page.locator(inspection + '.is-open').count(), 0, 'Mobile reveals the target before presenting the editor');
  return route;
}

async function openTerm(page) {
  await button(page, 'Сверка с таблицей').click();
  const dialog = page.getByRole('dialog', { name: 'Сверка с таблицей', exact: true });
  await dialog.waitFor();
  return dialog;
}

async function openTermPosition(dialog, coords) {
  const shown = dialog.getByRole('region', { name: 'Позиция в сверке' });
  if (await shown.isVisible()) await button(shown, 'К списку').click();
  await dialog.getByRole('searchbox', { name: 'Поиск по сверке' }).fill(coords);
  const row = dialog.locator(`[data-term-row="cells:${coords}"]`);
  await row.click();
  const detail = dialog.getByRole('region', { name: 'Позиция в сверке' });
  await detail.waitFor();
  return detail;
}

async function backToGrid(page, route) {
  const editor = page.locator(inspection + '.is-open');
  if (await editor.count()) await button(editor, 'Закрыть панель').click();
  await route.waitFor();
}

async function inspectSlot(page, route, canCreate) {
  const destination = page.locator('[data-issue-slot="true"]');
  await destination.waitFor();
  assert.equal(await destination.count(), 1, 'One exact destination is highlighted');
  assert.equal(await page.locator('[data-issue-target="current"]').count(), 0, 'A slot does not impersonate an existing card');
  assert.equal(await button(route, 'Создать занятие в слоте').isVisible(), canCreate);
  const readSlotPosition = () => destination.evaluate(cell => {
    const scroller = cell.closest('.constructor-grid-wrap');
    const [target, wrap] = [cell.getBoundingClientRect(), scroller.getBoundingClientRect()];
    return { target: target.toJSON(), wrap: wrap.toJSON(),
      scrollTop: scroller.scrollTop, scrollLeft: scroller.scrollLeft, viewport: [innerWidth, innerHeight] };
  });
  try {
    await expectEventually('Highlighted slot is scrolled into view', async () => {
      const { target, wrap, viewport } = await readSlotPosition();
      return Math.min(wrap.right, target.right, viewport[0]) - Math.max(wrap.left, target.left, 0) > 15
        && Math.min(wrap.bottom, target.bottom, viewport[1]) - Math.max(wrap.top, target.top, 0) > 12;
    });
  } catch (error) {
    await page.screenshot({ path: dataDir + '/issue-slot-failure.png' });
    throw new Error(`${error.message} ${JSON.stringify(await readSlotPosition())}`, { cause: error });
  }
}

async function modern(page, width) {
  step = `grid issue route Q53/T53 at ${width}`;
  await openDraft(page);
  const filter = page.getByLabel('Фильтр по классу', { exact: true });
  if (!await filter.isVisible()) await button(page, 'Фильтры').click();
  await filter.selectOption(fixture.classes['5-Т']);
  assert.equal(await filter.inputValue(), fixture.classes['5-Т']);
  let dialog = await viewReview(page);
  await fitDialog(page, dialog, width);
  await screenshot(page, `after-problems-${width}.png`);
  const pair = await findConflict(dialog, english, english, physics);
  await dialog.getByRole('combobox', { name: 'Серьёзность', exact: true }).selectOption('conflict');
  await dialog.getByRole('combobox', { name: 'День', exact: true }).selectOption('4');
  await dialog.getByRole('combobox', { name: 'Класс', exact: true }).selectOption({ label: '11-Т' });
  await dialog.getByRole('combobox', { name: 'Тип проблемы', exact: true }).selectOption('audience');
  await pair.issue.waitFor();
  await button(pair.issue, 'Показать оба занятия').click();
  await pair.issue.getByText(/Учеников с пересечением — 2/).waitFor();
  assert.equal(await pair.issue.getByRole('button', { name: /^Показать в расписании/ }).count(), 2);
  await screenshot(page, `after-paired-issue-${width}.png`);
  await openIssueCard(pair.issue);
  const active = page.locator('[data-issue-target="current"]').first();
  await active.waitFor();
  const activeId = await active.getAttribute('data-lesson-id');
  assert.ok([fixture.cases.Q53, fixture.cases.T53].includes(activeId));
  const otherId = activeId === fixture.cases.Q53 ? fixture.cases.T53 : fixture.cases.Q53;
  let route = await inspectAt(page, activeId, otherId, width, 'Пятница');
  assert.equal(await filter.inputValue(), '', 'Jump clears the grid filter hiding the destination');
  assert.equal(await button(page, 'Фильтры').getAttribute('aria-expanded'), 'false');
  await screenshot(page, `after-navigation-${width}.png`);
  await backToGrid(page, route);
  await button(route, activeId === fixture.cases.Q53 ? physics : english).click();
  route = await inspectAt(page, otherId, activeId, width, 'Пятница');
  await backToGrid(page, route);
  await button(route, 'К проверке').click();
  dialog = page.getByRole('dialog', { name: 'Проверка расписания' });
  await dialog.waitFor();
  await expectEventually('Search survives return to review', async () => (await dialog.getByRole('searchbox', { name: 'Поиск по вопросам' }).inputValue()) === english);
  assert.equal(await dialog.getByRole('combobox', { name: 'Серьёзность', exact: true }).inputValue(), 'conflict');
  assert.equal(await dialog.getByRole('combobox', { name: 'День', exact: true }).inputValue(), '4');
  assert.equal(await dialog.getByRole('combobox', { name: 'Класс', exact: true }).inputValue(), '11-Т');
  assert.equal(await dialog.getByRole('combobox', { name: 'Тип проблемы', exact: true }).inputValue(), 'audience');
  await button(dialog, 'К конструктору').click();

  step = `comparison pair S11/U11 at ${width}`;
  let term = await openTerm(page);
  let detail = await openTermPosition(term, 'S11');
  await button(detail, 'Открыть связанное занятие').click();
  route = await inspectAt(page, fixture.cases.S11, fixture.cases.U11, width, 'Понедельник');
  const scroll = await page.locator(grid).evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }));
  assert.ok(scroll.top > 0, 'Late lesson scrolls vertically');
  if (width < 900) assert.ok(scroll.left > 0, 'Far class scrolls horizontally on mobile');
  await backToGrid(page, route);
  await button(route, 'К проверке').click();
  term = page.getByRole('dialog', { name: 'Сверка с таблицей' });
  await term.getByRole('region', { name: 'Позиция в сверке' }).getByRole('heading', { name: /S11/ }).waitFor();
  await button(term, 'К конструктору').click();

  step = `roster M43 editor and return at ${width}`;
  term = await openTerm(page);
  detail = await openTermPosition(term, 'M43');
  await detail.getByText(/Сведения о составах/).click();
  await detail.getByText(/Два ручных исключения\./).scrollIntoViewIfNeeded();
  await screenshot(page, `after-problem-detail-${width}.png`);
  await button(detail, 'Открыть связанное занятие').focus();
  await page.keyboard.press('Enter');
  route = await inspectAt(page, fixture.cases.M43, null, width, 'Четверг');
  if (width < 900) await button(route, 'Редактировать занятие').click();
  const inspector = page.locator(inspection + '.is-open');
  await inspector.getByRole('heading', { name: 'Литература ОГЭ', exact: true }).waitFor();
  await screenshot(page, `after-editor-${width}.png`);
  if (width < 900) {
    const viewport = page.viewportSize();
    const roomField = inspector.getByLabel('Кабинет', { exact: true });
    await roomField.focus();
    await page.setViewportSize({ width, height: 470 });
    await expectEventually('Editor field stays reachable when mobile viewport shrinks', () => roomField.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return document.activeElement === element && rect.top > 0 && rect.bottom <= innerHeight;
    }));
    await page.setViewportSize(viewport);
  }
  await button(inspector, 'К проверке').click();
  term = page.getByRole('dialog', { name: 'Сверка с таблицей' });
  await term.getByRole('region', { name: 'Позиция в сверке' }).getByRole('heading', { name: /M43/ }).waitFor();
  await button(term, 'К конструктору').click();

  step = `unconfirmed group and missing slot at ${width}`;
  term = await openTerm(page);
  detail = await openTermPosition(term, 'I39');
  await button(detail, 'Открыть связанное занятие').click();
  route = await inspectAt(page, fixture.cases.GROUP, null, width, 'Среда');
  if (width < 900) await button(route, 'Редактировать занятие').click();
  await inspector.getByRole('heading', { name: 'Практикум без группы', exact: true }).waitFor();
  await button(inspector, 'К проверке').click();
  term = page.getByRole('dialog', { name: 'Сверка с таблицей' });
  await term.getByRole('region', { name: 'Позиция в сверке' }).getByRole('heading', { name: /I39/ }).waitFor();
  await button(term, 'К конструктору').click();
  term = await openTerm(page);
  detail = await openTermPosition(term, 'M23');
  await button(detail, 'Открыть связанное занятие').click();
  route = await inspectAt(page, fixture.cases.MOVE, null, width, 'Вторник');
  await backToGrid(page, route);
  await button(route, 'К проверке').click();
  term = page.getByRole('dialog', { name: 'Сверка с таблицей' });
  detail = term.getByRole('region', { name: 'Позиция в сверке' });
  await button(detail, 'Показать предложенный слот').click();
  route = page.locator(routeSelector);
  await route.getByText('Слот · Творческая мастерская').waitFor();
  await inspectSlot(page, route, false);
  await screenshot(page, `after-move-slot-${width}.png`);
  await button(route, 'К проверке').click();
  await term.getByRole('heading', { name: /M23/ }).waitFor();

  detail = await openTermPosition(term, 'V48');
  await button(detail, 'Открыть день и слот').click();
  await route.getByText('Слот · Лаборатория без карточки').waitFor();
  await inspectSlot(page, route, true);
  await button(route, 'Создать занятие в слоте').click();
  const subject = inspector.getByLabel('Название активности', { exact: true });
  await subject.waitFor();
  assert.equal(await subject.inputValue(), 'Лаборатория без карточки');
  await button(inspector, 'К проверке').click();
  await term.getByRole('heading', { name: /V48/ }).waitFor();

  detail = await openTermPosition(term, 'W48');
  await button(detail, 'Показать прежний слот').click();
  await route.getByText('Слот · Архивное занятие').waitFor();
  await inspectSlot(page, route, false);
  await button(route, 'К проверке').click();
  await term.getByRole('heading', { name: /W48/ }).waitFor();
  results.push({ width, pairedConflicts: 2, rosterEvidence: 'M43', group: 'I39', movedSlot: 'M23',
    missingSlot: 'V48', historicalSlot: 'W48', relatedRoute: true });
}

async function correctRoom(initial) {
  step = 'room diagnostic, user edit, save and restored filters';
  const { context, page, errors } = await session(1366);
  try {
    await page.goto(draftURL);
    const dialog = await viewReview(page);
    const found = await findConflict(dialog, roomSubject, roomSubject, 'География · кабинет');
    await dialog.getByRole('combobox', { name: 'Тип проблемы', exact: true }).selectOption('room');
    await button(found.issue, 'Показать оба занятия').click();
    await button(found.issue, `Показать в расписании — ${roomSubject}`).waitFor();
    await screenshot(page, 'demo-1-room-issue.png');
    await openIssueCard(found.issue, roomSubject);
    const route = await inspectAt(page, fixture.cases['ROOM-B'], fixture.cases['ROOM-A'], 1366, 'Вторник');
    await screenshot(page, 'demo-2-highlight.png');
    if (!await page.locator(inspection + '.is-open').count()) await button(route, 'Редактировать занятие').click();
    const inspector = page.locator(inspection + '.is-open');
    const room = inspector.getByLabel('Кабинет', { exact: true });
    await room.fill('310');
    const persisted = page.waitForResponse(response =>
      response.url() === backend + experiment + fixture.id && response.request().method() === 'PUT');
    await screenshot(page, 'demo-3-edit.png');
    await button(inspector, 'Сохранить правку').click();
    assert.ok((await persisted).ok(), 'Draft save succeeds through API');
    const edited = await readApi(experiment + fixture.id);
    assert.equal(edited.revision, initial.revision + 1);
    assert.equal(edited.payload.lessons.find(l => l.id === fixture.cases['ROOM-B']).room, '310');
    assert.equal(edited.payload.lessons.find(l => l.id === fixture.cases['ROOM-A']).room, '309');
    assert.equal(edited.payload.lessons.length, initial.payload.lessons.length);
    const refreshed = page.waitForResponse(response =>
      response.url().endsWith('/publication/preview') && response.request().method() === 'POST'
      && response.request().postDataJSON().expected_revision === edited.revision);
    await button(inspector, 'К проверке').click();
    const updateResponse = await refreshed;
    assert.ok(updateResponse.ok(), 'Updated revision was checked through the real API');
    const updatedReview = await updateResponse.json();
    assert.ok(!updatedReview.diagnostics.some(issue => issue.code === 'room_overlap'
      && issue.related_lesson_ids?.includes(fixture.cases['ROOM-B'])));
    assert.equal(updatedReview.diagnostics.filter(issue => issue.code === 'student_overlap').length, 2);
    const returned = page.getByRole('dialog', { name: 'Проверка расписания' });
    await returned.waitFor();
    await expectEventually('Review search was restored after save', async () =>
      (await returned.getByRole('searchbox', { name: 'Поиск по вопросам' }).inputValue()) === roomSubject);
    assert.equal(await returned.getByRole('combobox', { name: 'Тип проблемы', exact: true }).inputValue(), 'room');
    await expectEventually('Resolved pair is absent from the refreshed review', async () =>
      (await returned.locator('[data-review-issue]').filter({ hasText: roomSubject }).filter({ hasText: 'География · кабинет' }).count()) === 0);
    await screenshot(page, 'demo-4-review-restored.png');
    assert.deepEqual(errors, []);
    results.push({ action: 'room correction persisted and disappeared from refreshed review', reviewQueryRestored: true, revision: edited.revision });
    console.log('PASS room edit, saved-revision API readback and refreshed diagnostics');
  } finally {
    await context.close();
  }
}
try {
  const initial = await readApi(experiment + fixture.id);
  const primary = await readApi(experiment + fixture.primary_id);
  assert.equal(initial.revision, fixture.revision, 'Reseed the synthetic fixture before rerunning');
  const previewRequest = await fetch(backend + '/api/admin/schedule/constructor/' + fixture.id + '/publication/preview', {
    method: 'POST', headers, body: JSON.stringify({ scope_kind: 'template', expected_revision: initial.revision }),
  });
  assert.ok(previewRequest.ok);
  const preview = await previewRequest.json();
  for (const [a, b, size] of [['Q53', 'T53', 2], ['S11', 'U11', 1]]) {
    const pairs = preview.diagnostics.filter(issue => issue.code === 'student_overlap'
      && issue.related_lesson_ids?.includes(fixture.cases[a]) && issue.related_lesson_ids?.includes(fixture.cases[b]));
    assert.equal(pairs.length, 1);
    assert.equal(pairs[0].affected_student_ids.length, size);
  }
  assert.ok(preview.diagnostics.some(issue => issue.code === 'room_overlap' && issue.lesson_id === fixture.cases['ROOM-B']));
  assert.ok(preview.diagnostics.some(issue => issue.code === 'audience_unresolved' && issue.lesson_id === fixture.cases.GROUP));

  for (const width of widths) {
    const { context, page, errors } = await session(width);
    try {
      if (before) await baseline(page, width);
      else await modern(page, width);
      assert.deepEqual(errors, []);
      console.log(`PASS ${before ? 'before' : 'issue navigation'} ${width}px`);
    } finally {
      await context.close();
    }
  }
  assert.deepEqual(await readApi(experiment + fixture.id), initial, 'Review and slot navigation leave the draft unchanged');
  if (!before && widths.includes(1366)) await correctRoom(initial);
  assert.deepEqual(violations, []);
  assert.deepEqual(await readApi(experiment + fixture.primary_id), primary);
  await writeFile(dataDir + `/issue-navigation-${before ? 'before' : 'after'}.json`, JSON.stringify({ results, shots, trace }, null, 2));
} catch (error) {
  console.error(`FAIL ${step}\n${error.stack}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
