import { expect } from '@playwright/test';
import { test } from './helpers/experiment-test';

const API = 'http://127.0.0.1:8011';
const ADMIN = { 'X-Dev-Auth': 'admin:1' };

test('constructor keeps edits local until manual save and persists a distinguishable English group', async ({ page }) => {
  test.setTimeout(45_000);
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    const state = { attempts: 0 };
    Object.assign(window, { __scheduleSaveProbe: state });
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl = input instanceof Request ? input.url : String(input);
      if (new URL(requestUrl, window.location.href).pathname.match(/\/api\/admin\/schedule\/experiments\/[^/]+$/) && (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase() === 'PUT') {
        state.attempts += 1;
      }
      return nativeFetch(input, init);
    }) as typeof window.fetch;
  });

  const groups = (await (await page.request.get(`${API}/api/admin/groups`, { headers: ADMIN })).json()).items as Array<{ id: string; name: string }>;
  const english = groups.find((item) => item.name === 'english:6');
  const klass = groups.find((item) => item.name === '9-Ан');
  expect(english && klass).toBeTruthy();

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  const name = `Manual draft ${Date.now()}`;
  await page.getByRole('textbox', { name: 'Название черновика' }).fill(name);
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Фильтр по классу' }).selectOption(String(klass!.id));
  await page.locator('[data-cell]').first().click();
  await page.getByRole('button', { name: 'Форма', exact: true }).click();
  await page.getByRole('textbox', { name: 'Предмет нового занятия' }).fill('Английский язык');
  const picker = page.getByRole('region', { name: 'Участники занятия' });
  await picker.getByRole('button', { name: /Учебные группы/ }).click();
  await picker.getByRole('checkbox', { name: /^Английский язык · группа 6\b/u }).check();
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  const subject = page.locator('.constructor-inspector label').filter({ hasText: 'Предмет' }).locator('input');
  await subject.fill('Английский язык · сохранено');
  await expect(page.getByText('Есть несохранённые изменения', { exact: true })).toBeVisible();
  await page.waitForTimeout(1600);
  expect(await page.evaluate(() => (window as Window & { __scheduleSaveProbe?: { attempts: number } }).__scheduleSaveProbe?.attempts)).toBe(0);

  await page.getByRole('button', { name: 'Сохранить черновик' }).click();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible({ timeout: 5000 });
  expect(await page.evaluate(() => (window as Window & { __scheduleSaveProbe?: { attempts: number } }).__scheduleSaveProbe?.attempts)).toBe(1);
  const drafts = await page.request.get(`${API}/api/admin/schedule/experiments`, { headers: ADMIN });
  expect(drafts.ok()).toBeTruthy();
  const created = (await drafts.json()).items.find((item: { name: string }) => item.name === name);
  expect(created?.id).toBeTruthy();

  await page.reload();
  await page.getByRole('button', { name: new RegExp(name) }).click();
  await page.locator('.constructor-card').first().click();
  await expect(page.locator('.constructor-inspector label').filter({ hasText: 'Предмет' }).locator('input')).toHaveValue('Английский язык · сохранено');
});

test('constructor: create, edit, copy range, cut, undo, conflict, save and reopen without publication', async ({
  page,
}) => {
  const baselineBefore = await page.request.get(
    `${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`,
    { headers: ADMIN },
  );
  expect(baselineBefore.ok()).toBeTruthy();
  const baseline = await baselineBefore.json();
  const group = await page.request.post(`${API}/api/admin/groups`, {
    headers: ADMIN,
    data: { name: '9А', group_type: 'class' },
  });
  expect(group.ok()).toBeTruthy();
  const classId = String((await group.json()).id);
  const subgroup = await page.request.post(`${API}/api/admin/groups`, {
    headers: ADMIN,
    data: { name: '9А Математика A', group_type: 'subject_group' },
  });
  expect(subgroup.ok()).toBeTruthy();
  const teacherCreated = await page.request.post(
    `${API}/api/admin/identities`,
    {
      headers: ADMIN,
      data: { display_name: 'Тестовый математик', kind: 'teacher' },
    },
  );
  expect(teacherCreated.ok()).toBeTruthy();
  const teacherId = String((await teacherCreated.json()).id);
  const groupId = String((await subgroup.json()).id);
  const assignment = await page.request.post(
    `${API}/api/admin/teacher-assignments`,
    {
      headers: ADMIN,
      data: {
        teacher_identity_id: teacherId,
        group_id: groupId,
        subject: 'Математика',
      },
    },
  );
  expect(assignment.ok()).toBeTruthy();
  await page.goto('/admin/schedule/constructor?role=admin');
  await expect(
    page.getByRole('heading', { name: 'Конструктор' }),
  ).toBeVisible();
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await expect(page.getByRole('button', { name: 'Понедельник' })).toBeVisible();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Фильтр по классу' }).selectOption(classId);
  const cells = page.locator('[data-cell]');
  await expect(cells.first()).toBeVisible();
  await cells.first().click();
  await page
    .getByPlaceholder('мат 9 A ДФ 17')
    .fill('мат 9А A');
  await expect(page.locator('.constructor-suggestions')).toContainText(
    '9А Математика A',
  );
  await page.getByRole('combobox', { name: 'Распознанная группа' }).selectOption(groupId);
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  await expect(page.locator('.constructor-card')).toHaveCount(1);
  const subject = page.locator('.constructor-inspector').getByRole('combobox', { name: 'Предмет', exact: true });
  await expect(subject).toHaveValue('Математика');
  await expect(page.locator('.constructor-inspector').getByRole('checkbox', { name: /9А Математика A/ })).toBeChecked();
  const teacher = page
    .locator('.constructor-inspector label')
    .filter({ hasText: 'Преподаватель' })
    .locator('select');
  await teacher.selectOption(teacherId);
  await page
    .locator('.constructor-inspector label')
    .filter({ hasText: 'Кабинет' })
    .locator('input')
    .fill('301');
  await expect(page.locator('.constructor-resources')).toContainText(
    'Свободные кабинеты',
  );
  await cells.first().click();
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'с', code: 'KeyC', ctrlKey: true, bubbles: true })));
  await page.locator('[data-cell="1:0"]').click();
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'м', code: 'KeyV', ctrlKey: true, bubbles: true })));
  await expect(page.locator('.constructor-card')).toHaveCount(2);
  await page.locator('[data-cell="1:0"]').click();
  await page.keyboard.press('Control+c');
  await cells.first().click();
  await page.keyboard.press('Control+v');
  await page.locator('.constructor-issue-count').click();
  await expect(page.locator('.constructor-card.error')).toHaveCount(2);
  await page.locator('.constructor-issue-count').click();
  await page.keyboard.press('Control+z');
  await expect(page.locator('.constructor-card')).toHaveCount(2);
  await page.keyboard.press('Control+y');
  await expect(page.locator('.constructor-card')).toHaveCount(3);
  await cells.first().click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+x');
  await page.locator('[data-cell="2:0"]').click();
  await page.keyboard.press('Control+v');
  await expect(page.locator('.constructor-card')).toHaveCount(3);
  await page.locator('[data-cell="2:0"] .constructor-card').first().dragTo(page.locator('[data-cell="3:0"]'), { sourcePosition: { x: 4, y: 20 }, targetPosition: { x: 20, y: 20 } });
  await expect(page.locator('[data-cell="3:0"] .constructor-card')).toHaveCount(
    1,
  );
  await page.locator('[data-cell="1:0"]').click();
  await page.keyboard.press('Shift+ArrowDown');
  await page.keyboard.press('Control+c');
  await expect(page.locator('.constructor-status')).toContainText(
    '2 занятий, 1 × 2 ячеек',
  );
  await page.getByRole('button', { name: 'Вторник' }).click();
  await cells.first().click();
  await page.keyboard.press('Control+v');
  await expect(page.locator('[data-cell="0:0"] .constructor-card')).toHaveCount(
    1,
  );
  await expect(page.locator('[data-cell="1:0"] .constructor-card')).toHaveCount(
    1,
  );
  await page
    .getByRole('button', { name: /Сохранить/ })
    .first()
    .click();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await page.reload();
  await page
    .getByRole('button', { name: /Новый шаблон/ })
    .first()
    .click();
  await page.getByRole('button', { name: 'Вторник' }).click();
  await expect(page.locator('.constructor-card')).toHaveCount(2);
  const baselineAfter = await page.request.get(
    `${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`,
    { headers: ADMIN },
  );
  expect(baselineAfter.ok()).toBeTruthy();
  const after = await baselineAfter.json();
  expect(after.canonical?.version_id).toBe(baseline.canonical?.version_id);
  expect(after.blocks).toEqual(baseline.blocks);
});

test('constructor: parallel and week views keep lessons, shortcuts, conflicts, template and draft state', async ({ page }) => {
  const baselineResponse = await page.request.get(
    `${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`,
    { headers: ADMIN },
  );
  expect(baselineResponse.ok()).toBeTruthy();
  const baseline = await baselineResponse.json();

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /На основе действующего расписания/ }).click();
  await expect(page.locator('.constructor-head').filter({ hasText: '9 класс' })).toBeVisible();

  await page.getByRole('group', { name: 'Режим просмотра' }).getByRole('button', { name: 'Неделя' }).click();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Объект недельного просмотра' }).selectOption('parallel:9');
  const gradeNineColumn = 4;
  const periodsPerDay = (await page.locator('.constructor-time').count()) / 5;
  const emptyTuesdayRow = periodsPerDay + 4;
  await expect(page.locator(`[data-cell="0:${gradeNineColumn}"] .constructor-card`)).toHaveCount(3);
  await page.locator(`[data-cell="0:${gradeNineColumn}"] .constructor-card`).first().click();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+c');
  await page.locator(`[data-cell="${emptyTuesdayRow}:${gradeNineColumn}"]`).click({ position: { x: 8, y: 8 } });
  await page.keyboard.press('Control+v');
  await expect(page.locator(`[data-cell="${emptyTuesdayRow}:${gradeNineColumn}"] .constructor-card`)).toHaveCount(3);

  // A repeated paste creates a real schedule conflict in the isolated fixture.
  await page.locator(`[data-cell="0:${gradeNineColumn}"]`).click({ position: { x: 8, y: 8 } });
  await page.keyboard.press('Control+v');
  await page.locator('.constructor-issue-count').click();
  await expect(page.locator('.constructor-conflict-panel')).toContainText('Проблемы расписания');
  await page.getByRole('combobox', { name: 'Тип проблемы' }).selectOption('student');
  const studentConflict = page.locator('.constructor-conflict-list article').filter({ hasText: 'назначено одновременно' }).first();
  await expect(studentConflict).toBeVisible();
  await studentConflict.getByRole('button', { name: 'Показать в расписании' }).click();
  await expect(page.getByRole('button', { name: 'День' })).toHaveClass(/active/);
  await expect(page.locator('.constructor-card.active')).toHaveCount(1);

  await page.locator('.constructor-inspector').getByRole('button', { name: /Все свободные/ }).click();
  await expect(page.locator('.constructor-inspector')).toContainText('Состав определяется по ученикам');

  await page.getByRole('button', { name: 'Шаблон' }).click();
  const firstStart = page.getByLabel('Начало 1 урока');
  await firstStart.fill('09:05');
  await expect(firstStart).toHaveValue('09:05');
  await firstStart.fill('09:00');
  await expect(page.locator('.constructor-slot-table')).toContainText('№');
  await page.getByRole('button', { name: 'Закрыть панель' }).click();
  await expect(page.locator('.constructor-inspector')).toHaveCount(0);
  await page.getByRole('button', { name: 'Открыть панель' }).click();
  await expect(page.locator('.constructor-inspector')).toBeVisible();

  await page.getByRole('button', { name: /Сохранить/ }).first().click();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: /Копия действующего расписания/ }).first().click();
  await page.getByRole('group', { name: 'Режим просмотра' }).getByRole('button', { name: 'Неделя' }).click();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page.getByRole('combobox', { name: 'Объект недельного просмотра' }).selectOption('parallel:9');
  await expect(page.locator(`[data-cell="${emptyTuesdayRow}:${gradeNineColumn}"] .constructor-card`)).toHaveCount(3);

  const afterResponse = await page.request.get(
    `${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`,
    { headers: ADMIN },
  );
  expect(afterResponse.ok()).toBeTruthy();
  const after = await afterResponse.json();
  expect(after.canonical?.version_id).toBe(baseline.canonical?.version_id);
  expect(after.blocks).toEqual(baseline.blocks);
});

test('constructor: empty source and experimental draft management preserve canonical schedule', async ({ page }) => {
  const beforeResponse = await page.request.get(`${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`, { headers: ADMIN });
  expect(beforeResponse.ok()).toBeTruthy();
  const before = await beforeResponse.json();
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /На основе действующего расписания/ }).click();
  await expect(page.locator('.constructor-status')).toContainText('Копия создана: перенесено');
  await expect(page.locator('.constructor-card').first()).toBeVisible();
  await expect(page.locator('.constructor-issue-count')).toContainText('Диагностика');
  await page.getByRole('button', { name: 'Все черновики' }).click();
  const original = page.locator('.constructor-draft').filter({ hasText: 'Копия действующего расписания' }).first();
  await expect(original).toContainText('Источник: canonical');
  const renamedTitle = `Проверочная копия canonical ${Date.now()}`;
  page.once('dialog', (dialog) => void dialog.accept(renamedTitle));
  await original.getByRole('button', { name: 'Переименовать' }).click();
  await expect(page.locator('.constructor-draft').filter({ hasText: renamedTitle })).toBeVisible();
  const renamed = page.locator('.constructor-draft').filter({ hasText: renamedTitle });
  await renamed.locator('.constructor-draft-open').click();
  await page.getByRole('button', { name: 'Все черновики' }).click();
  const reopened = page.locator('.constructor-draft').filter({ has: page.getByText(renamedTitle, { exact: true }) });
  await reopened.getByRole('button', { name: 'Дублировать' }).click();
  const duplicate = page.locator('.constructor-draft').filter({ has: page.getByText(`${renamedTitle} — копия`, { exact: true }) });
  await expect(duplicate).toContainText('Источник: canonical');
  page.once('dialog', (dialog) => void dialog.accept());
  await duplicate.getByRole('button', { name: 'Удалить' }).click();
  await expect(duplicate).toHaveCount(0);
  await expect(reopened).toBeVisible();

  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await expect(page.locator('.constructor-card')).toHaveCount(0);
  await page.locator('.constructor-issue-count').click();
  await expect(page.locator('.constructor-conflict-panel')).toBeVisible();
  await expect(page.locator('.constructor-conflict-list article')).toHaveCount(0);
  await expect(page.locator('.constructor-unplaced')).toHaveCount(0);
  const afterResponse = await page.request.get(`${API}/api/admin/schedule/observability?week_start=2026-09-21&editor=true`, { headers: ADMIN });
  expect(afterResponse.ok()).toBeTruthy();
  const after = await afterResponse.json();
  expect(after.canonical?.version_id).toBe(before.canonical?.version_id);
  expect(after.blocks).toEqual(before.blocks);
});

test('constructor: overlapping memberships identify a student conflict', async ({
  page,
}) => {
  const createGroup = async (name: string, group_type: string) => {
    const response = await page.request.post(`${API}/api/admin/groups`, {
      headers: ADMIN,
      data: { name, group_type },
    });
    expect(response.ok()).toBeTruthy();
    return String((await response.json()).id);
  };
  const classId = await createGroup('8Б', 'class');
  const firstId = await createGroup('8Б математика А', 'subject_group');
  const secondId = await createGroup('8Б английский Б', 'subject_group');
  const studentResponse = await page.request.post(
    `${API}/api/admin/identities`,
    {
      headers: ADMIN,
      data: {
        display_name: 'Ученик с двумя группами',
        kind: 'student',
        class_name: '8Б',
      },
    },
  );
  expect(studentResponse.ok()).toBeTruthy();
  const studentId = String((await studentResponse.json()).id);
  for (const group_id of [classId, firstId, secondId]) {
    const membership = await page.request.post(`${API}/api/admin/memberships`, {
      headers: ADMIN,
      data: { group_id, identity_id: studentId },
    });
    expect(membership.ok()).toBeTruthy();
  }
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  await page.getByRole('button', { name: 'Фильтры' }).click();
  await page
    .getByRole('combobox', { name: 'Фильтр по классу' })
    .selectOption(classId);
  await page.locator('[data-cell="0:0"]').click();
  await page.getByPlaceholder('мат 9 A ДФ 17').fill('Математика');
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  await page.locator('.constructor-inspector').getByRole('button', { name: /Учебные группы/ }).click();
  await page.locator('.constructor-inspector').getByRole('checkbox', { name: /8Б математика А/ }).check();
  await page.getByRole('button', { name: 'Урок', exact: true }).click();
  await page.getByPlaceholder('мат 9 A ДФ 17').fill('Английский');
  await page.getByRole('button', { name: 'Добавить занятие' }).click();
  await page.locator('.constructor-inspector').getByRole('button', { name: /Учебные группы/ }).click();
  await page.locator('.constructor-inspector').getByRole('checkbox', { name: /8Б английский Б/ }).check();
  await page.locator('.constructor-issue-count').click();
  await expect(
    page.locator('[data-cell="0:0"] .constructor-card.error'),
  ).toHaveCount(2);
  await expect(page.locator('.constructor-issues')).toContainText('Ученик');
  await page.locator('[data-cell="0:0"] .constructor-card').first().click();
  await page.keyboard.press('Control+c');
  await page.locator('[data-cell="1:0"]').click();
  await page.keyboard.press('Control+v');
  await expect(page.locator('[data-cell="1:0"] .constructor-card')).toHaveCount(
    1,
  );
  await page
    .locator('[data-cell="1:0"] .constructor-card')
    .click({ button: 'right' });
  await expect(page.locator('.constructor-context')).toContainText(
    'Копировать',
  );
});

test('existing schedule editor keeps keyboard focus after clicking a grid cell', async ({
  page,
}) => {
  await page.goto('/admin/schedule?role=admin');
  await expect(page.locator('.schedule-studio')).toBeVisible();
  await page.locator('.studio-week-cell').first().click();
  await expect
    .poll(() =>
      page.evaluate(() =>
        document.activeElement?.classList.contains('schedule-studio'),
      ),
    )
    .toBe(true);
});

test('constructor: drag resize spans the week and persists; double click restores auto width', async ({ page }) => {
  await page.goto('/admin/schedule/constructor?role=admin');
  await page.getByRole('button', { name: /Пустая школьная неделя/ }).click();
  const column = page.locator('.constructor-head').filter({ hasText: '9 класс' });
  const handle = column.getByRole('separator', { name: 'Ширина колонки 9 класс' });
  const before = await column.boundingBox();
  const grip = await handle.boundingBox();
  expect(before && grip).toBeTruthy();
  await page.mouse.move(grip!.x + grip!.width / 2, grip!.y + grip!.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip!.x + grip!.width / 2 + 70, grip!.y + grip!.height / 2, { steps: 7 });
  await expect.poll(async () => (await column.boundingBox())!.width).toBeGreaterThan(before!.width + 55);
  await page.mouse.up();
  const width = (await column.boundingBox())!.width;
  await page.getByRole('group', { name: 'Режим просмотра' }).getByRole('button', { name: 'Неделя' }).click();
  await expect.poll(async () => (await column.boundingBox())!.width).toBeCloseTo(width, 0);
  await page.getByRole('button', { name: 'Открыть панель' }).click();
  await page.getByRole('combobox', { name: 'Масштаб' }).selectOption('120');
  await handle.scrollIntoViewIfNeeded();
  const scaled = (await column.boundingBox())!.width;
  const zoomGrip = await handle.boundingBox();
  await page.mouse.move(zoomGrip!.x + zoomGrip!.width / 2, zoomGrip!.y + zoomGrip!.height / 2);
  await page.mouse.down();
  await page.mouse.move(zoomGrip!.x + zoomGrip!.width / 2 + 60, zoomGrip!.y + zoomGrip!.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await column.boundingBox())!.width).toBeGreaterThan(scaled + 45);
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await page.reload();
  await page.getByRole('group', { name: 'Разделы черновиков' }).getByRole('button', { name: /Мои черновики/ }).click();
  await page.getByRole('button', { name: /Новый шаблон/ }).first().click();
  await expect.poll(async () => (await column.boundingBox())!.width).toBeGreaterThan(width);
  await handle.dblclick();
  await expect.poll(async () => (await column.boundingBox())!.width).toBeLessThan(width);
});
