import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { test } from './helpers/experiment-test';

const API = 'http://127.0.0.1:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };
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
    data: { group_id: classId, identity_id: studentId },
  });
  expect(membership.ok()).toBeTruthy();
  return studentId;
}

async function addMembership(page: Page, groupId: string, studentId: string) {
  const membership = await page.request.post(`${API}/api/admin/memberships`, {
    headers: ADMIN,
    data: { group_id: groupId, identity_id: studentId },
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
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
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
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('button', { name: 'Кружок', exact: true }).click();
  await page.getByRole('combobox', { name: 'Название активности' }).fill('Айкидо без каталога');
  await expect(page.getByRole('combobox', { name: 'Преподаватель нового занятия' })).toHaveValue('');
  await page.getByRole('textbox', { name: 'Кабинет нового занятия' }).fill('Зал');
  await expect(page.getByRole('combobox', { name: 'Запись каталога' })).toHaveValue('');
  await page.setViewportSize({ width: 1600, height: 1100 });
  await screenshot(page, 'club-aikido-no-catalog-before-add');
  await page.getByRole('button', { name: 'Добавить кружок' }).click();
  const club = page.locator('.constructor-card').filter({ hasText: 'Айкидо без каталога' });
  await expect(club).toContainText('Участники не назначены');
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
  await expect(pasted).toContainText('Участники не назначены');
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
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
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
  await expect(page.locator('.constructor-card')).toContainText('Все остальные');
});
