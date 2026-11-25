import { personRevision } from './directory-fixture';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { test } from './helpers/experiment-test';
import { ADMIN_HEADERS as ADMIN } from './helpers/miniapp-preview';

const API = 'http://127.0.0.1:8011';
const cwd = process.cwd();
const projectRoot = path.basename(cwd) === 'e2e-workspace' ? path.resolve(cwd, '../..') : cwd;
const shots = path.join(projectRoot, 'artifacts', 'schedule-repair');

async function screenshot(page: Page, name: string) {
  await mkdir(shots, { recursive: true });
  if (['physics-10-11-local-labels', 'club-no-participants-source'].includes(name)) {
    const geometry = await page.evaluate(() => {
      const wrap = document.querySelector<HTMLElement>('.constructor-grid-wrap');
      const grid = document.querySelector<HTMLElement>('.constructor-grid');
      const cell = wrap?.querySelector<HTMLElement>('.constructor-cell:has(.constructor-card)');
      const card = cell?.querySelector<HTMLElement>('.constructor-card');
      const time = wrap?.querySelector<HTMLElement>('.constructor-time');
      const rect = (element?: HTMLElement | null) => element ? ({ x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y, width: element.getBoundingClientRect().width, right: element.getBoundingClientRect().right }) : null;
      return {
        wrap: rect(wrap), scrollLeft: wrap?.scrollLeft, grid: rect(grid), gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : null,
        cell: rect(cell), card: rect(card), cardText: card?.innerText, cardStyle: card ? { transform: getComputedStyle(card).transform, justifyContent: getComputedStyle(card).justifyContent, position: getComputedStyle(card).position } : null,
        time: rect(time), timeStyle: time ? { position: getComputedStyle(time).position, zIndex: getComputedStyle(time).zIndex, left: getComputedStyle(time).left } : null,
      };
    });
    console.log(`schedule-repair geometry ${name}: ${JSON.stringify(geometry)}`);
  }
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true });
}

async function openScheduleInspector(page: Page) {
  const drawer = page.locator('.constructor-drawer-tab');
  if (await drawer.getAttribute('aria-expanded') !== 'true') await drawer.click();
  await expect(page.locator('.constructor-inspector')).toHaveAttribute('aria-hidden', 'false');
}

async function createClass(page: Page, name: string) {
  const response = await page.request.post(`${API}/api/admin/groups`, { headers: ADMIN, data: { name, group_type: 'class' } });
  expect(response.ok()).toBeTruthy();
  return String((await response.json()).id);
}

async function createGroup(page: Page, name: string, className: string) {
  const response = await page.request.post(`${API}/api/admin/groups`, {
    headers: ADMIN,
    data: { name, group_type: 'subject_group', subject: 'Английский язык', base_class_name: className },
  });
  expect(response.ok()).toBeTruthy();
  return String((await response.json()).id);
}

async function addStudentToClass(page: Page, classId: string, className: string, label: string) {
  const identity = await page.request.post(`${API}/api/admin/identities`, {
    headers: ADMIN,
    data: { display_name: label, kind: 'student', class_name: className },
  });
  expect(identity.ok()).toBeTruthy();
  const studentId = String((await identity.json()).id);
  const membership = await page.request.post(`${API}/api/admin/memberships`, {
    headers: ADMIN,
    data: { expected_revision: await personRevision(page, studentId), group_id: classId, identity_id: studentId },
  });
  expect(membership.ok()).toBeTruthy();
  return studentId;
}

async function addMembership(page: Page, groupId: string, studentId: string) {
  const membership = await page.request.post(`${API}/api/admin/memberships`, {
    headers: ADMIN,
    data: { expected_revision: await personRevision(page, studentId), group_id: groupId, identity_id: studentId },
  });
  expect(membership.ok()).toBeTruthy();
}

async function openEmptyWeek(page: Page) {
  await page.goto('/admin/schedule/constructor?role=admin');
  const created = page.waitForResponse((response) => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/admin/schedule/experiments');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await expect(page.locator('[data-cell]').first()).toBeVisible();
  const response = await created;
  expect(response.ok()).toBeTruthy();
  const draft = await response.json() as { payload: { classes: string[] } };
  return draft.payload.classes;
}

async function filterClass(page: Page, classId: string) {
  const selector = page.getByRole('combobox', { name: 'Фильтр по классу' });
  if (!(await selector.isVisible())) await page.getByRole('button', { name: 'Фильтры' }).click();
  await selector.selectOption(classId);
}

test('constructor repair: sliding inspector, room labels, rename popover and whole-class copy', async ({ page }) => {
  test.setTimeout(45_000);
  const suffix = Date.now().toString(36).slice(-5);
  const sourceName = `9-А E2E copy ${suffix}`;
  const targetName = `9-Б E2E copy ${suffix}`;
  const [sourceId, targetId] = await Promise.all([createClass(page, sourceName), createClass(page, targetName)]);
  await page.setViewportSize({ width: 1440, height: 960 });
  await openEmptyWeek(page);
  await filterClass(page, sourceId);
  await page.locator('[data-cell="0:0"]').click();
  const inspector = page.locator('.constructor-inspector');
  const drawerTab = page.locator('.constructor-drawer-tab');
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await expect(drawerTab).toHaveAttribute('aria-expanded', 'false');
  await drawerTab.click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'false');
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Русский язык');
  await page.getByRole('checkbox', { name: 'Это занятие проводится онлайн' }).uncheck();
  await page.getByRole('combobox', { name: 'Кабинет нового занятия' }).fill('каб. 8');
  await page.getByRole('button', { name: 'Добавить занятие', exact: true }).click();

  const sourceLesson = page.locator('.constructor-card').filter({ hasText: 'Русский язык' });
  await expect(sourceLesson).toContainText('каб. 8');
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await sourceLesson.click();
  await expect(sourceLesson).toHaveClass(/active/);
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await screenshot(page, 'drawer-closed-full-grid');

  const tabBox = await drawerTab.boundingBox();
  expect(tabBox).not.toBeNull();
  if (tabBox) {
    await page.mouse.move(tabBox.x + tabBox.width / 2, tabBox.y + tabBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(tabBox.x - 56, tabBox.y + tabBox.height / 2, { steps: 5 });
    await page.mouse.up();
  }
  await expect(inspector).toHaveAttribute('aria-hidden', 'false');
  await page.waitForTimeout(300);
  const openTabBox = await drawerTab.boundingBox();
  expect(openTabBox).not.toBeNull();
  if (openTabBox) {
    await page.mouse.move(openTabBox.x + openTabBox.width / 2, openTabBox.y + openTabBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(openTabBox.x + openTabBox.width / 2 + 56, openTabBox.y + openTabBox.height / 2, { steps: 5 });
    await page.mouse.up();
  }
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await sourceLesson.dblclick();
  await expect(inspector).toHaveAttribute('aria-hidden', 'false');
  await expect(inspector.getByLabel('Кабинет', { exact: true })).toHaveValue('8');
  await screenshot(page, 'drawer-open-editor');
  await page.waitForTimeout(300);
  const drawerGeometry = await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>('.constructor-inspector')?.getBoundingClientRect();
    const tab = document.querySelector<HTMLElement>('.constructor-drawer-tab')?.getBoundingClientRect();
    return panel && tab ? { panelLeft: panel.left, tabRight: tab.right } : null;
  });
  expect(drawerGeometry).not.toBeNull();
  if (drawerGeometry) {
    console.log(`drawer geometry: ${JSON.stringify(drawerGeometry)}`);
    expect(drawerGeometry.tabRight).toBeLessThanOrEqual(drawerGeometry.panelLeft + 1);
    expect(drawerGeometry.panelLeft - drawerGeometry.tabRight).toBeLessThan(12);
  }

  const roomField = inspector.getByLabel('Кабинет', { exact: true });
  await roomField.fill('каб. 9');
  await expect(inspector.locator('.constructor-editor-actions')).toContainText('При сохранении запишутся все правки черновика');
  await expect(inspector.getByRole('button', { name: 'Сохранить черновик', exact: true })).toBeEnabled();
  const gridWrap = page.locator('.constructor-grid-wrap');
  const scrollBeforeClose = await gridWrap.evaluate((element) => ({ top: element.scrollTop, left: element.scrollLeft }));
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await drawerTab.click();
  await expect(roomField).toHaveValue('9');
  expect(await gridWrap.evaluate((element) => ({ top: element.scrollTop, left: element.scrollLeft }))).toEqual(scrollBeforeClose);
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await page.locator('.constructor-actions').getByRole('button', { name: 'Сохранить черновик' }).click();
  await expect(page.locator('.constructor-actions')).toContainText('Сохранено');

  const header = page.locator('.constructor-top');
  const initialHeaderHeight = await header.evaluate((element) => element.getBoundingClientRect().height);
  await page.getByRole('button', { name: 'Переименовать открытый черновик' }).click();
  const rename = page.getByRole('textbox', { name: 'Новое название черновика' });
  await expect(rename).toBeVisible();
  await expect.poll(() => header.evaluate((element) => element.getBoundingClientRect().height)).toBe(initialHeaderHeight);
  await screenshot(page, 'draft-rename-popover');
  await rename.press('Escape');
  await expect(rename).toBeHidden();

  await sourceLesson.click();
  await page.keyboard.press('Control+c');
  await filterClass(page, targetId);
  await page.locator('[data-cell="1:0"]').click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+v');
  const pasted = page.locator('[data-cell="1:0"] .constructor-card').filter({ hasText: 'Русский язык' });
  await expect(pasted).toBeVisible();
  await expect(pasted).toContainText('каб. 9');

  await page.setViewportSize({ width: 375, height: 812 });
  const mobileEditorCta = page.getByRole('button', { name: 'Открыть редактор и разбор проблем' });
  if (await mobileEditorCta.isVisible()) await mobileEditorCta.click();
  await expect(page.locator('.constructor-grid')).toBeVisible();
  if (await inspector.getAttribute('aria-hidden') === 'false') {
    await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
    await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  }
  const mobileHeaderHeight = await header.evaluate((element) => element.getBoundingClientRect().height);
  await page.getByRole('button', { name: 'Переименовать открытый черновик' }).click();
  await expect(rename).toBeVisible();
  await expect.poll(() => header.evaluate((element) => element.getBoundingClientRect().height)).toBe(mobileHeaderHeight);
  await screenshot(page, 'draft-rename-popover-mobile');
  await rename.press('Escape');
  await expect(rename).toBeHidden();
  if (await inspector.getAttribute('aria-hidden') === 'false') await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  await pasted.click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
  await drawerTab.click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'false');
  await expect(page.locator('.constructor-grid')).toBeVisible();
  const mobileGeometry = await page.evaluate(() => {
    const inspectorElement = document.querySelector<HTMLElement>('.constructor-inspector');
    const gridWrap = document.querySelector<HTMLElement>('.constructor-grid-wrap');
    const rect = (element: HTMLElement | null) => element ? { left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right, position: getComputedStyle(element).position, width: getComputedStyle(element).width } : null;
    return { inspector: rect(inspectorElement), grid: rect(gridWrap) };
  });
  console.log(`drawer mobile layout: ${JSON.stringify(mobileGeometry)}`);
  expect(mobileGeometry.inspector?.position).toBe('fixed');
  expect(mobileGeometry.inspector?.left).toBeGreaterThan(0);
  await screenshot(page, 'drawer-open-mobile');
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'true');
});

test('constructor edit preserves horizontal grid position', async ({ page }) => {
  test.setTimeout(45_000);
  await page.setViewportSize({ width: 900, height: 960 });
  await openEmptyWeek(page);
  await page.locator('[data-cell="0:0"]').click();
  await page.locator('.constructor-drawer-tab').click();
  await expect(page.locator('.constructor-inspector')).toHaveAttribute('aria-hidden', 'false');
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Геометрия прокрутки');
  await page.getByRole('checkbox', { name: 'Это занятие проводится онлайн' }).uncheck();
  await page.getByRole('combobox', { name: 'Кабинет нового занятия' }).fill('8');
  await page.getByRole('button', { name: 'Добавить занятие', exact: true }).click();
  const lesson = page.locator('.constructor-card').filter({ hasText: 'Геометрия прокрутки' });
  await expect(lesson).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  const gridWrap = page.locator('.constructor-grid-wrap');
  await gridWrap.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  await expect.poll(() => gridWrap.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  await lesson.dblclick();
  const room = page.locator('.constructor-inspector').getByLabel('Кабинет', { exact: true });
  await room.fill('9');
  const beforeClose = await gridWrap.evaluate((element) => element.scrollLeft);
  await page.getByRole('button', { name: 'Закрыть редактор расписания' }).click();
  expect(await gridWrap.evaluate((element) => element.scrollLeft)).toBe(beforeClose);
  await expect(lesson).toContainText('9');
});

test('constructor electives: parallel courses use class availability without student rosters', async ({ page }) => {
  const suffix = Date.now().toString(36).slice(-5);
  const class5 = await createClass(page, `5-А elective ${suffix}`);
  const class6 = await createClass(page, `6-А elective ${suffix}`);
  await page.setViewportSize({ width: 1440, height: 960 });
  const classIds = await openEmptyWeek(page);
  expect(classIds).toContain(class5);
  expect(classIds).toContain(class6);
  await filterClass(page, class5);
  await page.locator('[data-cell="0:0"]').click();
  const inspector = page.locator('.constructor-inspector');
  await page.locator('.constructor-drawer-tab').click();
  await expect(inspector).toHaveAttribute('aria-hidden', 'false');

  await inspector.getByRole('button', { name: 'Другие типы…', exact: true }).click();
  await inspector.getByRole('button', { name: 'Курс по выбору', exact: true }).click();
  const createCourse = async (name: string) => {
    await expect(inspector.getByRole('button', { name: 'Другие типы… · Курс по выбору', exact: true })).toBeVisible();
    await page.getByRole('combobox', { name: 'Название активности' }).fill(name);
    const availability = page.locator('.constructor-elective-choice').nth(0);
    const placement = page.locator('.constructor-elective-choice').nth(1);
    await availability.getByRole('checkbox', { name: '5 класс' }).check();
    await availability.getByRole('checkbox', { name: '6 класс' }).check();
    await placement.getByRole('checkbox', { name: `6-А elective ${suffix}` }).check();
    await page.getByRole('checkbox', { name: 'Это занятие проводится онлайн' }).uncheck();
    await page.getByRole('combobox', { name: 'Кабинет нового занятия' }).fill(`E-${name.slice(-1)}`);
    await expect(page.getByText('Именной состав курсов по выбору не ведётся')).toBeVisible();
    await expect(inspector.getByRole('region', { name: 'Участники занятия' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Добавить курс по выбору', exact: true }).click();
    await expect(page.locator('.constructor-card').filter({ hasText: name })).toBeVisible();
  };

  await createCourse(`E2E elective 1 ${suffix}`);
  await page.getByRole('button', { name: '+ Параллельное занятие' }).click();
  await createCourse(`E2E elective 2 ${suffix}`);
  await page.getByRole('button', { name: '+ Параллельное занятие' }).click();
  await createCourse(`E2E elective 3 ${suffix}`);
  await expect(page.locator('.constructor-card').filter({ hasText: `E2E elective 1 ${suffix}` })).toBeVisible();
  await expect(page.locator('.constructor-card').filter({ hasText: `E2E elective 2 ${suffix}` })).toBeVisible();
  await expect(page.locator('.constructor-card').filter({ hasText: `E2E elective 3 ${suffix}` })).toBeVisible();
  await expect(page.locator('.constructor-issues')).not.toContainText('Учеников назначено одновременно');
});

test('constructor repair: lunch for a selected group remains editable through inline draft rename', async ({ page }) => {
  const suffix = Date.now();
  const tag = suffix.toString(36).slice(-5);
  const className = `9-А E2E lunch ${tag}`;
  await page.setViewportSize({ width: 1600, height: 1100 });
  const classId = await createClass(page, className);
  const groupName = `9-А E2E English group ${tag}`;
  const groupId = await createGroup(page, groupName, className);
  const studentId = await addStudentToClass(page, classId, className, `Repair lunch student ${suffix}`);
  await addMembership(page, groupId, studentId);
  await openEmptyWeek(page);
  await filterClass(page, classId);
  await page.locator('[data-cell="0:0"]').click();
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('button', { name: /Другие типы…/ }).click();
  await page.getByRole('button', { name: 'Обед', exact: true }).click();
  const lunchPicker = page.getByRole('region', { name: 'Участники занятия' });
  await lunchPicker.getByRole('button', { name: /Учебные группы/ }).click();
  await lunchPicker.getByRole('searchbox', { name: 'Найти учебную группу' }).fill(tag);
  await lunchPicker.locator(`input[type="checkbox"][value="${groupId}"]`).check();
  await expect(lunchPicker).toContainText('Состав определён', { timeout: 10_000 });
  await expect(lunchPicker).toContainText('1 учеников', { timeout: 10_000 });
  await lunchPicker.getByText('1 учеников', { exact: true }).scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: 'Добавить обед' }).scrollIntoViewIfNeeded();
  await screenshot(page, 'lunch-for-selected-group');
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileEditorCta = page.getByRole('button', { name: 'Открыть редактор и разбор проблем' });
  if (await mobileEditorCta.isVisible()) {
    await mobileEditorCta.click({ timeout: 5000 });
    await expect(page.locator('.constructor-grid')).toBeVisible({ timeout: 10_000 });
    await screenshot(page, 'mobile-390');
  } else {
    await screenshot(page, 'mobile-390');
  }
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.getByRole('button', { name: 'Добавить обед' }).click();
  await expect(page.locator('.constructor-card')).toContainText('Обед');
  const currentName = await page.locator('.constructor-current-draft span').innerText();
  await page.getByRole('button', { name: 'Переименовать открытый черновик' }).click();
  const renameInput = page.getByRole('textbox', { name: 'Новое название черновика' });
  await renameInput.fill('   ');
  await page.getByRole('button', { name: 'Сохранить название' }).click();
  const alertGeometry = await page.evaluate(() => {
    const alert = document.querySelector<HTMLElement>('.constructor-current-draft [role="alert"]');
    const details = [];
    let current = alert;
    for (let depth = 0; current && depth < 6; depth++, current = current.parentElement) {
      const style = getComputedStyle(current);
      const rect = current.getBoundingClientRect();
      details.push({ tag: current.tagName, className: current.className, text: current.innerText, rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, display: style.display, overflow: style.overflow, maxHeight: style.maxHeight, whiteSpace: style.whiteSpace });
    }
    return details;
  });
  console.log(`schedule-repair rename alert geometry: ${JSON.stringify(alertGeometry)}`);
  await page.getByText('Введите непустое название.', { exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByText('Введите непустое название.', { exact: true })).toBeVisible();
  await renameInput.press('Escape');
  await expect(page.locator('.constructor-current-draft')).toContainText(currentName);
  await expect(page.getByText('Есть несохранённые изменения', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Переименовать открытый черновик' }).click();
  const renamed = `Repair rename ${Date.now()}`;
  const newName = page.getByRole('textbox', { name: 'Новое название черновика' });
  await newName.fill(renamed);
  await screenshot(page, 'inline-draft-rename');
  await newName.press('Enter');
  await expect(page.locator('header')).toContainText(renamed);
  await expect(page.getByText('Есть несохранённые изменения', { exact: true })).toBeVisible();
  await screenshot(page, 'lunch-rename-dirty');
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(page.locator('header').getByText('Сохранено', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: new RegExp(renamed) }).click();
  await expect(page.locator('.constructor-card').filter({ hasText: 'Обед' })).toBeVisible();
});

test('constructor repair: empty club participants survive clipboard paste into another class column', async ({ page }) => {
  const tag = Date.now().toString(36).slice(-5);
  const first = await createClass(page, `9-А E2E club A ${tag}`);
  const second = await createClass(page, `9-Б E2E club B ${tag}`);
  await openEmptyWeek(page);
  await filterClass(page, first);
  await page.locator('[data-cell="0:0"]').click();
  await page.locator('.constructor-drawer-tab').click();
  await expect(page.locator('.constructor-inspector')).toHaveAttribute('aria-hidden', 'false');
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('button', { name: /Другие типы…/ }).click();
  await page.getByRole('button', { name: 'Кружок', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Айкидо без каталога');
  await expect(page.getByRole('combobox', { name: 'Преподаватель нового занятия' })).toHaveValue('');
  await page.getByRole('checkbox', { name: 'Это занятие проводится онлайн' }).uncheck();
  await page.getByRole('combobox', { name: 'Кабинет нового занятия' }).fill('Зал');
  await expect(page.getByRole('combobox', { name: 'Запись каталога' })).toHaveValue('');
  await page.setViewportSize({ width: 1600, height: 1100 });
  await screenshot(page, 'club-aikido-no-catalog-before-add');
  await page.getByRole('button', { name: 'Добавить кружок' }).click();
  const club = page.locator('.constructor-card').filter({ hasText: 'Айкидо без каталога' });
  await expect(club).not.toContainText('Участники не назначены');
  await expect(club).toHaveAttribute('title', /Без учителя.*Зал/u);
  await expect(club).toContainText('Зал');
  await screenshot(page, 'club-no-participants-source');
  await club.click();
  await page.keyboard.press('Control+c');
  await filterClass(page, second);
  await page.locator('[data-cell="1:0"]').click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+v');
  const pasted = page.locator('[data-cell="1:0"] .constructor-card').filter({ hasText: 'Айкидо без каталога' });
  await expect(pasted).toBeVisible();
  await expect(pasted).not.toContainText('Участники не назначены');
  await screenshot(page, 'club-no-participants-pasted');
});

test('constructor repair: local multi-class captions, bulk audience preview and narrow layout', async ({ page }) => {
  const suffix = Date.now();
  const tag = suffix.toString(36).slice(-5);
  const physicsNames = [`10-А E2E ${tag}`, `10-Б E2E ${tag}`, `11-А E2E ${tag}`];
  const physicsIds = await Promise.all(physicsNames.map((name) => createClass(page, name)));
  await Promise.all(physicsIds.map((id, index) => addStudentToClass(page, id, physicsNames[index], `Repair physics roster ${index} ${suffix}`)));
  const coworkingNames = [`5-А E2E coworking ${tag}`, `6-А E2E coworking ${tag}`, `7-А E2E coworking ${tag}`];
  const coworkingIds = await Promise.all(coworkingNames.map((name) => createClass(page, name)));
  await Promise.all(coworkingIds.map((id, index) => addStudentToClass(page, id, coworkingNames[index], `Repair coworking roster ${index} ${suffix}`)));
  await page.setViewportSize({ width: 900, height: 900 });
  await openEmptyWeek(page);
  const gridWrap = page.locator('.constructor-grid-wrap');
  await gridWrap.hover();
  await page.mouse.wheel(120, 0);
  await expect.poll(() => gridWrap.evaluate((element) => (element as HTMLElement).scrollLeft)).toBeGreaterThan(0);
  await filterClass(page, physicsIds[0]);
  await expect.poll(() => gridWrap.evaluate((element) => (element as HTMLElement).scrollLeft)).toBe(0);
  await page.locator('[data-cell="0:0"]').click();
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Физика');
  const picker = page.getByRole('region', { name: 'Участники занятия' });
  await picker.getByRole('button', { name: /Весь класс или параллель/ }).click();
  for (const name of physicsNames) await picker.getByRole('checkbox', { name }).check();
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  const joint = page.locator('.constructor-card').filter({ hasText: 'Физика' });
  await expect(joint).toContainText('↔');
  await expect(joint).toContainText('10');
  await expect(joint).not.toContainText('11-А');
  await page.setViewportSize({ width: 1600, height: 1100 });
  await screenshot(page, 'physics-10-11-local-labels');

  await filterClass(page, physicsIds[2]);
  const localEleven = page.locator('.constructor-card').filter({ hasText: 'Физика' });
  await expect(localEleven).toContainText('11');
  await expect(localEleven).not.toContainText('10-А');
  await screenshot(page, 'physics-11-local-label');

  await filterClass(page, coworkingIds[0]);
  await page.locator('[data-cell="1:0"]').click();
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Коворкинг');
  const coworkingPicker = page.getByRole('region', { name: 'Участники занятия' });
  await coworkingPicker.getByRole('button', { name: /Весь класс или параллель/ }).click();
  for (const name of coworkingNames) {
    await coworkingPicker.getByRole('checkbox', { name }).check();
  }
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  const coworking = page.locator('.constructor-card').filter({ hasText: 'Коворкинг' });
  await expect(coworking).toContainText('5');
  await expect(coworking).not.toContainText('7-А');
  await screenshot(page, 'coworking-three-classes');

  await filterClass(page, coworkingIds[2]);
  const localCoworking = page.locator('.constructor-card').filter({ hasText: 'Коворкинг' });
  await expect(localCoworking).toContainText('7');
  await expect(localCoworking).not.toContainText('5-А');

  await page.locator('.constructor-drawer-tab').click();
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(page.locator('header').getByText('Сохранено', { exact: true })).toBeVisible();
  await addStudentToClass(page, physicsIds[0], physicsNames[0], `Repair physics roster B ${suffix}`);
  await filterClass(page, physicsIds[0]);
  await page.getByRole('button', { name: 'Пересчитать аудитории' }).click();
  await page.getByRole('button', { name: 'Показать изменения' }).click();
  const recalculation = page.getByRole('dialog', { name: 'Пересчитать аудитории' });
  await expect(recalculation).toContainText('Изменено занятий: 1');
  await expect(recalculation).toContainText('Добавлено участников: 1');
  await expect(recalculation).toContainText('Можно подтвердить: 1');
  await screenshot(page, 'bulk-audience-preview');
  await page.getByRole('button', { name: 'Подтвердить проверенные составы' }).click();
  await expect(recalculation).not.toBeVisible();
  await page.getByRole('button', { name: 'Пересчитать аудитории' }).click();
  await page.getByRole('button', { name: 'Показать изменения' }).click();
  await expect(recalculation).toContainText('Изменено занятий: 0');
});

test('constructor repair: grade nine all-others audience displays a live class scoped remainder', async ({ page }) => {
  const className = `9-А E2E all others ${Date.now().toString(36).slice(-5)}`;
  await page.setViewportSize({ width: 1600, height: 1100 });
  const classId = await createClass(page, className);
  await addStudentToClass(page, classId, className, `Repair all others roster A ${Date.now()}`);
  await addStudentToClass(page, classId, className, `Repair all others roster B ${Date.now()}`);
  await openEmptyWeek(page);
  await filterClass(page, classId);
  await page.locator('[data-cell="0:0"]').click();
  await openScheduleInspector(page);
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('button', { name: /Другие типы…/ }).click();
  await page.getByRole('button', { name: 'Обед', exact: true }).click();
  const picker = page.getByRole('region', { name: 'Участники занятия' });
  await picker.getByRole('button', { name: 'Все остальные' }).click();
  await expect(picker).toContainText('Все остальные');
  await expect(picker).toContainText('Состав определён', { timeout: 10_000 });
  await expect(picker).toContainText(/2 учеников/u, { timeout: 10_000 });
  const rosterPreview = picker.getByText('2 учеников', { exact: true });
  await rosterPreview.scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: 'Добавить обед' }).scrollIntoViewIfNeeded();
  await screenshot(page, 'all-others-grade9');
  await page.getByRole('button', { name: 'Добавить обед' }).click();
  await expect(page.locator('.constructor-card')).toHaveText('Обед · Остальные');
});

test('вид занятия и онлайн сохраняются через обычную форму', async ({page}) => {
  const classId = await createClass(page, '7-Д · E2E онлайн-практикум');
  const createdResponse = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/admin/schedule/experiments');
  await openEmptyWeek(page);
  const created = await (await createdResponse).json();
  await filterClass(page, classId);
  await page.locator('[data-cell="0:0"]').click();
  await openScheduleInspector(page);
  await page.getByRole('button', {name: 'Форма', exact: true}).click();
  const kind = page.getByRole('group', {name: 'Тип занятия', exact: true});
  await expect(kind.getByRole('button', {name:'Урок', exact:true})).toHaveAttribute('aria-pressed','true');
  await kind.getByRole('button', {name:'Консультация'}).click();
  await page.getByLabel('Название активности').fill('Самостоятельное чтение');
  await page.getByRole('button', {name: 'Добавить консультацию', exact: true}).click();
  await expect(page.getByRole('alert').filter({hasText: 'Выберите преподавателя консультации'})).toBeVisible();
  await kind.getByRole('button', {name:'Практикум'}).click();
  await page.getByRole('checkbox', { name: 'Это занятие проводится онлайн' }).uncheck();
  await page.getByLabel('Кабинет нового занятия').fill('Кабинет 15');
  const online = page.getByRole('checkbox', {name:'Это занятие проводится онлайн'});
  const expectCompactOnline = async (mode: string) => {
    const size = await online.boundingBox();
    expect(size).not.toBeNull();
    expect(size!.width).toBe(16);
    expect(size!.height).toBe(16);
    const label = page.locator('.constructor-online-format').filter({has: online});
    await expect(label).toHaveCSS('display', 'flex');
    await mkdir(shots, {recursive: true});
    await label.screenshot({path: path.join(shots, `online-format-${mode}.png`)});
  };
  await expectCompactOnline('create');
  await online.check();
  await online.uncheck();
  await expect(page.getByLabel('Кабинет нового занятия')).toHaveValue(/15/);
  await online.check();
  await expect(page.getByLabel('Кабинет нового занятия')).toHaveCount(0);
  const connection = page.getByLabel('Ссылка на онлайн-урок (необязательно)', { exact: true });
  await connection.fill('Школьная видеокомната');
  await online.uncheck();
  await expect(connection).toHaveCount(0);
  await expect(page.getByLabel('Кабинет нового занятия')).toHaveValue(/15/);
  await online.check();
  await expect(connection).toHaveValue('Школьная видеокомната');
  await expect(page.getByLabel('Название активности')).toHaveValue('Самостоятельное чтение');
  await page.getByRole('button', {name: 'Добавить практикум', exact: true}).click();
  const card = page.locator('.constructor-card').filter({hasText: 'Самостоятельное чтение'});
  await expect(card).toContainText('Практикум');
  await expect(card).toContainText('Онлайн');
  await expect(card).not.toContainText('каб. 15');
  await card.dblclick();
  await expect(online).toBeChecked();
  await expectCompactOnline('edit');
  await expect(connection).toHaveValue('Школьная видеокомната');
  await expect(page.getByLabel('Кабинет', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Настроить подключение', exact: true})).toHaveCount(0);

  const savedResponse = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/admin/schedule/experiments/${created.id}`);
  await page.getByRole('complementary', { name: 'Редактор занятия' })
    .getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  const saved = await savedResponse;
  expect(saved.ok()).toBeTruthy();
  expect((await saved.json()).payload.lessons).toEqual([
    expect.objectContaining({ activityType: 'practice', format: 'online', connectionInfo: 'Школьная видеокомната', room: '' }),
  ]);
});

for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
  test(`Issue 21 editor creates and reopens every common kind at ${viewport.width}px`, async ({ page }) => {
    test.setTimeout(75_000);
    page.setDefaultTimeout(15_000);
    await page.setViewportSize(viewport);
    const createdResponse = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/admin/schedule/experiments');
    await openEmptyWeek(page);
    const created = await (await createdResponse).json();
    const directory = await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN });
    expect(directory.ok()).toBeTruthy();
    const demoClass = (await directory.json()).items.find((group: { name: string; group_type: string }) =>
      group.name === '5' && group.group_type === 'class');
    expect(demoClass).toBeTruthy();
    const classId = String(demoClass.id);
    await filterClass(page, classId);
    const teacherResponse = await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN });
    expect(teacherResponse.ok()).toBeTruthy();
    const teacher = (await teacherResponse.json()).items[0];
    const inspector = page.locator('.constructor-inspector');
    const kind = inspector.getByRole('group', { name: 'Тип занятия', exact: true });
    const online = inspector.getByRole('checkbox', { name: 'Это занятие проводится онлайн' });
    const cases = [
      { title: 'История', label: 'Урок', type: 'lesson' },
      { title: 'География', label: 'Практикум', type: 'practice' },
      { title: 'Литература', label: 'Консультация', type: 'consultation' },
      { title: 'Шахматы', label: 'Кружок', type: 'club' },
    ] as const;
    const capture = async (name: string, action: string) => {
      await inspector.evaluate(element => { element.scrollTop = 0; });
      await expect(kind).toBeInViewport({ ratio: 1 });
      await expect(online).toBeInViewport({ ratio: 1 });
      await expect(inspector.getByRole('button', { name: action, exact: true })).toBeInViewport({ ratio: 1 });
      expect(await inspector.evaluate(element => element.scrollWidth)).toBeLessThanOrEqual(
        (await inspector.evaluate(element => element.clientWidth)) + 1,
      );
      const folder = path.join(projectRoot, 'artifacts', 'issue-21');
      await mkdir(folder, { recursive: true });
      await inspector.screenshot({ path: path.join(folder, `${viewport.width}-${name}.png`), animations: 'disabled' });
    };

    for (const [period, item] of cases.entries()) {
      if (await inspector.getAttribute('aria-hidden') === 'false') {
        await inspector.getByRole('button', { name: 'Закрыть панель' }).click();
      }
      await page.locator(`[data-cell="${period}:0"]`).click({ position: { x: 5, y: 5 } });
      await openScheduleInspector(page);
      await expect(kind).toHaveCount(1);
      await expect(online).toBeChecked();
      if (item.type === 'lesson') {
        await inspector.getByRole('button', { name: 'Форма', exact: true }).click();
      } else if (item.type === 'club') {
        const other = kind.getByRole('button', { name: 'Другие типы…', exact: true });
        await other.press('Enter');
        await expect(other).toHaveAttribute('aria-expanded', 'true');
        await kind.getByRole('button', { name: item.label, exact: true }).press('Space');
        await expect(kind.getByRole('button', { name: /Другие типы/ })).toHaveAttribute('aria-expanded', 'false');
        await expect(kind.getByRole('button', { name: /Другие типы/ })).toBeFocused();
      } else {
        await kind.getByRole('button', { name: item.label, exact: true }).click();
      }
      await expect(online).toBeChecked();
      await inspector.getByLabel('Название активности').fill(item.title);
      if (item.type !== 'practice') await inspector.getByLabel('Преподаватель нового занятия').fill(teacher.display_name);
      if (item.type === 'lesson') {
        await online.uncheck();
        await inspector.getByLabel('Кабинет нового занятия').fill('17');
        await online.check();
        await online.uncheck();
        await expect(inspector.getByLabel('Кабинет нового занятия')).toHaveValue('17');
      }
      const action = { lesson: 'Добавить занятие', practice: 'Добавить практикум',
        consultation: 'Добавить консультацию', club: 'Добавить кружок' }[item.type];
      await capture(`create-${item.type}`, action);
      await inspector.getByRole('button', { name: action, exact: true }).click();
      await expect(inspector.getByRole('heading', { name: item.title })).toBeVisible();
    }
    const savedResponse = page.waitForResponse(response => response.request().method() === 'PUT'
      && new URL(response.url()).pathname === `/api/admin/schedule/experiments/${created.id}`);
    await inspector.getByRole('button', { name: 'Сохранить черновик' }).click();
    const saved = await savedResponse;
    expect(saved.ok()).toBeTruthy();
    const { payload } = await saved.json();
    expect(payload.lessons.map((lesson: { activityType: string; format: string }) =>
      [lesson.activityType, lesson.format])).toEqual([
      ['lesson', 'offline'], ['practice', 'online'], ['consultation', 'online'], ['club', 'online'],
    ]);

    await page.reload();
    await page.getByRole('button', { name: /Мои черновики ·/ }).click();
    await page.locator('.constructor-draft-open').filter({ hasText: created.id }).click();
    await filterClass(page, classId);
    for (const item of cases) {
      const savedLesson = payload.lessons.find((lesson: { activityType: string }) => lesson.activityType === item.type);
      if (await inspector.getAttribute('aria-hidden') === 'false') {
        await inspector.getByRole('button', { name: 'Закрыть панель' }).click();
      }
      await page.locator(`[data-lesson-id="${savedLesson.id}"].constructor-card`).first().click();
      await openScheduleInspector(page);
      await expect(inspector.getByRole('heading', { name: item.title })).toBeVisible();
      if (item.type === 'club') {
        await expect(kind.getByRole('button', { name: 'Другие типы… · Кружок' })).toBeVisible();
      } else {
        const typeButton = kind.getByRole('button', { name: item.label, exact: true });
        await expect(typeButton).toHaveCount(1);
        await expect(typeButton).toHaveAttribute('aria-pressed', 'true');
      }
      if (item.type === 'lesson') {
        await expect(online).not.toBeChecked();
        const connection = inspector.getByLabel('Ссылка на онлайн-урок (необязательно)', { exact: true });
        await expect(connection).toHaveCount(0);
        await online.check();
        await expect(inspector.getByLabel('Кабинет', { exact: true })).toHaveCount(0);
        await expect(connection).toBeVisible();
        await connection.fill('https://example.org/meeting');
        await online.uncheck();
        await expect(connection).toHaveCount(0);
        await expect(inspector.getByLabel('Кабинет', { exact: true })).toHaveValue('17');
        await online.check();
        await expect(connection).toHaveValue('https://example.org/meeting');
        await online.uncheck();
      } else {
        await expect(online).toBeChecked();
      }
      await capture(`edit-${item.type}`, 'Сохранить черновик');
    }
    if (viewport.width === 1366) {
      await inspector.getByRole('button', { name: 'Закрыть панель' }).click();
      await page.getByRole('button', { name: /5 класс, Понедельник: Обычный/ }).click();
      await page.getByRole('group', { name: 'Выбор режима 5 класс · Понедельник' })
        .getByRole('button', { name: 'Онлайн', exact: true }).click();
      await page.locator('[data-cell="4:0"]').click({ position: { x: 5, y: 5 } });
      await openScheduleInspector(page);
      await inspector.getByRole('button', { name: 'Форма', exact: true }).click();
      await expect(inspector.getByText('Онлайн по режиму дня', { exact: true })).toBeVisible();
      await expect(online).toHaveCount(0);
      await expect(inspector.getByLabel('Кабинет нового занятия')).toHaveCount(0);
      await expect(inspector.getByLabel('Ссылка на онлайн-урок (необязательно)', { exact: true })).toBeVisible();
      await inspector.getByRole('button', { name: 'Закрыть панель' }).click();
      await page.locator('[data-cell="0:0"] .constructor-card').first().click();
      await openScheduleInspector(page);
      await expect(inspector.getByText('Онлайн по режиму дня', { exact: true })).toBeVisible();
      await expect(online).toHaveCount(0);
      await expect(inspector.getByLabel('Кабинет', { exact: true })).toHaveCount(0);
      await expect(inspector.getByLabel('Ссылка на онлайн-урок (необязательно)', { exact: true })).toBeVisible();
    }
  });
}

test('Issue 21 type changes preserve confirmed groups, exceptions and catalogue links', async ({ page, registerExperiment }) => {
  test.setTimeout(75_000);
  page.setDefaultTimeout(15_000);
  const suffix = Date.now().toString(36);
  const label = `9-А E2E editor ${suffix}`;
  const classId = await createClass(page, label);
  const groupId = await createGroup(page, 'E2E Английский · A', label);
  const studentId = await addStudentToClass(page, classId, label, `Ученик редактора ${suffix}`);
  await addMembership(page, groupId, studentId);
  const extraNames = [`Второй ученик ${suffix}`, `Третий ученик ${suffix}`];
  for (const name of extraNames) await addMembership(page, groupId, await addStudentToClass(page, classId, label, name));
  const outsiderName = `Не входит в группу ${suffix}`;
  await addStudentToClass(page, classId, label, outsiderName);
  const teacherResponse = await page.request.get(`${API}/api/admin/people?kind=teacher`, { headers: ADMIN });
  const teacherId = String((await teacherResponse.json()).items[0].id);
  const catalog = await page.request.post(`${API}/api/admin/schedule/activities`, {
    headers: ADMIN, data: { name: 'Тестовый курс', type: 'elective' },
  });
  expect(catalog.ok()).toBeTruthy();
  const catalogId = (await catalog.json()).id;
  const response = await page.request.post(`${API}/api/admin/schedule/experiments`, {
    headers: ADMIN,
    data: { name: 'E2E редактор · смена типа', source: 'empty', payload: {
      draft_kind: 'test', days: [0], slots: [{ start: '09:00', end: '09:45' }, { start: '10:00', end: '10:45' }],
      classes: [classId], lessons: [
        { id: 'group-lesson', day: 0, period: 0, kind: 'lesson', activityType: 'lesson', subject: 'Английский язык',
          teacherId, teacherIds: [teacherId], classId, classIds: [classId], groupId, groupIds: [groupId], room: 'каб. 17',
          audience: { kind: 'groups', group_ids: [groupId] } },
        { id: 'catalog-course', day: 0, period: 1, kind: 'lesson', activityType: 'elective', subject: 'Тестовый курс',
          classId, classIds: [classId], eligibleGrades: [9],
          audience: { kind: 'students', student_ids: [] }, activityCatalogId: catalogId, activityNameSnapshot: 'Тестовый курс' },
      ],
    } },
  });
  expect(response.ok()).toBeTruthy();
  const draft = await response.json();
  registerExperiment(draft.id);
  const withException = await page.request.put(`${API}/api/admin/schedule/experiments/${draft.id}`, {
    headers: ADMIN, data: { expected_revision: draft.revision, payload: { ...draft.payload,
      lessons: draft.payload.lessons.map((lesson: { id: string; audience: object }) => lesson.id === 'group-lesson'
        ? { ...lesson, audience: { ...lesson.audience, exclude_student_ids: [studentId] }, attendance_reason: 'Индивидуальное расписание' }
        : lesson),
    } },
  });
  expect(withException.ok()).toBeTruthy();

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Тестовые ·/ }).click();
  await page.locator('.constructor-draft-open').filter({ hasText: draft.id }).click();
  await page.locator('.constructor-card[data-lesson-id="catalog-course"]').dblclick();
  const editor = page.locator('.constructor-inspector');
  const kind = editor.getByRole('group', { name: 'Тип занятия', exact: true });
  await expect(editor.getByRole('heading', { name: 'Тестовый курс' })).toBeVisible();
  await kind.getByRole('button', { name: 'Урок', exact: true }).click();
  await expect(editor.getByText(/Привязка к каталогу будет снята/)).toBeVisible();
  await editor.getByRole('button', { name: 'Отмена', exact: true }).click();
  await expect(editor.getByRole('combobox', { name: 'Каталог', exact: true })).toHaveValue(catalogId);

  await editor.getByRole('button', { name: 'Закрыть панель' }).click();
  await page.locator('.constructor-card[data-lesson-id="group-lesson"]').dblclick();
  await expect(editor.getByRole('heading', { name: 'Английский язык' })).toBeVisible();
  await expect(editor.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled();
  const exceptions = editor.getByRole('button', { name: 'Кто не идёт на это занятие? · 1' });
  await expect(exceptions).toHaveAttribute('aria-expanded', 'false');
  await expect(editor.getByLabel('Причина (необязательно)', { exact: true })).toBeHidden();
  await expect(editor.getByLabel('Поиск преподавателя', { exact: true })).toBeVisible();
  await expect(editor.getByRole('button', { name: 'Настройки преподавателя', exact: true })).toHaveCount(0);
  await exceptions.press('Enter');
  await expect(editor.getByLabel('Причина (необязательно)', { exact: true })).toHaveValue('');
  await expect(exceptions).toHaveAttribute('aria-expanded', 'true');
  await expect(editor.getByText(/Индивидуальное расписание/)).toBeVisible();
  await expect(editor.getByRole('group', { name: 'Не посещают это занятие' }).locator('input:checked')).toHaveCount(1);
  await exceptions.press('Space');
  await expect(exceptions).toHaveAttribute('aria-expanded', 'false');
  const availability = editor.getByRole('button', { name: 'Посмотреть занятость' });
  await availability.press('Enter');
  await expect(editor.getByText(/Занятые кабинеты/)).toBeVisible();
  await availability.press('Space');
  await expect(editor.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled();

  await page.route('**/api/admin/schedule/experiments/audience-preview', async route => {
    await new Promise(resolve => setTimeout(resolve, 1_200));
    await route.continue();
  });
  await exceptions.click();
  for (const name of extraNames) await editor.getByRole('checkbox', { name, exact: true }).check();
  await expect(editor.getByRole('button', { name: 'Кто не идёт на это занятие? · 3' })).toBeVisible();
  await expect(editor.getByRole('checkbox', { name: outsiderName })).toHaveCount(0);
  for (const name of extraNames) await editor.getByRole('checkbox', { name, exact: true }).uncheck();
  await expect(exceptions).toBeVisible();
  await exceptions.click();

  await kind.getByRole('button', { name: 'Практикум', exact: true }).click();
  await expect(editor.getByText('Преподаватель присутствует на практикуме?')).toBeVisible();
  await editor.getByRole('button', { name: 'Только отвечает за задание' }).click();
  await expect(editor.getByLabel('Ответственные за задание (без присутствия)')).toHaveValues([teacherId]);
  await expect(editor.getByRole('combobox', { name: 'Преподаватель', exact: true })).toHaveValue('');
  await kind.getByRole('button', { name: 'Консультация', exact: true }).click();
  await expect(editor.getByRole('alert')).toHaveText('Выберите преподавателя консультации.');
  await kind.getByRole('button', { name: 'Урок', exact: true }).click();
  await editor.getByRole('combobox', { name: 'Преподаватель', exact: true }).selectOption(teacherId);
  await kind.getByRole('button', { name: 'Другие типы…' }).click();
  await kind.getByRole('button', { name: 'Кружок', exact: true }).click();
  await expect(editor.getByText(/отмечены персональные исключения/)).toBeVisible();
  await expect(editor.getByRole('button', { name: 'Продолжить', exact: true })).not.toBeVisible();
  await editor.getByRole('button', { name: 'Отмена', exact: true }).click();
  // A visible edit ensures the test exercises a persistence round trip.
  await editor.getByRole('combobox', { name: 'Кабинет', exact: true }).fill('18');
  const savedResponse = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === `/api/admin/schedule/experiments/${draft.id}`);
  await editor.getByRole('button', { name: 'Сохранить черновик' }).click();
  const saved = await savedResponse;
  expect(saved.ok()).toBeTruthy();
  const { payload } = await saved.json();
  expect(payload.lessons).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'group-lesson', activityType: 'lesson', groupId,
      audience: expect.objectContaining({ group_ids: [groupId], exclude_student_ids: [studentId] }),
      attendance_history: [expect.objectContaining({ reason: 'Индивидуальное расписание' })],
    }),
    expect.objectContaining({ id: 'catalog-course', activityType: 'elective', activityCatalogId: catalogId, eligibleGrades: [9] }),
  ]));
  await page.keyboard.press('Escape');
  await expect(editor).toHaveAttribute('aria-hidden', 'true');
});
