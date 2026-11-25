import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const output = resolve(process.cwd(), 'artifacts/template-selection');
mkdirSync(output, { recursive: true });

function lesson(id: number, subject = `Предмет ${id}`) {
  return { id: `lesson-${id}`, subject, start: '10:00', end: '10:45', groups: ['9А'], members: ['student-1'], teachers: ['Преподаватель'], room: '204', problems: [] };
}

async function openWithPreview(page: Page, preview: Record<string, unknown>, promote = true) {
  await page.context().route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname)) return route.abort();
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const headers = {
      'access-control-allow-origin': new URL(page.url()).origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': 'authorization, content-type, x-dev-auth, x-admin-browser-session, x-school-admin-session, x-telegram-init-data',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
    };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    const fulfill = (json: unknown, status = 200) => route.fulfill({ json, status, headers });
    if (path.endsWith('/auth/session')) return fulfill({ state: 'approved', user: { id: 1, role: 'admin', roles: ['admin'] } });
    if (path.endsWith('/experiments/capabilities')) return fulfill({ demo_available: false, school_available: true, consolidated_schedule: true });
    if (path === '/api/admin/schedule/experiments/school') return fulfill({ items: [
      { id: 'primary-old', name: 'Основной шаблон', revision: 3, lesson_count: 1, is_primary_template: true, draft_kind: 'school' },
      { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2, lesson_count: 2, is_primary_template: false, draft_kind: 'school' },
    ] });
    if (path.endsWith('/school/candidate-new')) return fulfill({ id: 'candidate-new', payload: {} });
    if (path.endsWith('/selection-preview')) return fulfill(preview);
    if (path.endsWith('/select-primary')) return fulfill({ detail: 'Шаблон изменился с момента сравнения. Обновите данные.' }, 409);
    if (path.endsWith('/approve-legacy-baseline')) return fulfill({ approved: true });
    if (path.endsWith('/directory')) return fulfill({ groups: [{ id: 1, name: '9А', display_name: '9А', group_type: 'class' }], teachers: [], students: [], rooms: [] });
    if (path.endsWith('/observability')) return fulfill({ blocks: [], weeks: [], effective_weeks: [], week_publications: [] });
    return fulfill({ items: [] });
  });
  await page.goto('/admin/schedule/constructor?role=admin&editor=1');
  await expect(page.getByRole('heading', { name: 'Основной шаблон' })).toBeVisible();
  const candidate = page.locator('.schedule-start-row').filter({ has: page.getByRole('heading', { name: 'Шаблон на проверку' }) });
  if (promote) {
    await candidate.getByText('Ещё', { exact: true }).click();
    await candidate.getByRole('button', { name: 'Сделать основным шаблоном' }).click();
  }
  return candidate;
}

test('the comparison action never requests promotion and restores keyboard focus on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 });
  const writes: string[] = [];
  page.on('request', request => {
    if (/\/(select-primary|approve-legacy-baseline|publish)$/.test(new URL(request.url()).pathname)) writes.push(request.url());
  });
  const candidate = await openWithPreview(page, standardPreview(), false);
  const compare = candidate.getByRole('button', { name: 'Сравнить', exact: true });
  await expect(candidate.getByRole('button', { name: 'Сделать основным шаблоном' })).toBeHidden();
  await compare.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Сравнение шаблонов' });
  await expect(dialog).toBeVisible();
  await dialog.getByText('Добавленные занятия · 1').click();
  await expect(dialog.getByText(/Физика/)).toBeVisible();
  await expect(dialog.getByRole('button', { name: /опубликовать|подтвердить/i })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(compare).toBeFocused();
  expect(writes).toEqual([]);
});

function standardPreview() {
  const added = lesson(1, 'Физика');
  const removed = lesson(2, 'История');
  const changedBefore = lesson(3, 'Алгебра');
  const changedAfter = { ...changedBefore, room: '203', members: ['student-1', 'student-2'] };
  return {
    current: { id: 'primary-old', name: 'Основной шаблон', revision: 3 },
    candidate: { id: 'candidate-new', name: 'Шаблон на проверку', revision: 2 },
    current_hash: 'synthetic-current-hash', candidate_hash: 'synthetic-candidate-hash',
    added: [added], removed: [removed],
    changed: [{ id: changedAfter.id, before: changedBefore, after: changedAfter, changes: [
      { field: 'members', label: 'Участники', impact: 'Меняется персональный состав учеников.', before: ['student-1'], after: ['student-1', 'student-2'] },
      { field: 'room', label: 'Кабинет', impact: 'Меняется место проведения занятия.', before: '101', after: '203' },
    ] }],
    settings_changes: [{ field: 'grade_modes', label: 'Режимы дней по параллелям', impact: 'Меняется режим обучения учеников.', before: 'Обычный режим', after: '9 класс, Пн: Онлайн' }],
    problems: [{ lesson_id: changedAfter.id, details: ['Конфликт преподавателя: Преподаватель'] }],
    requires_explicit_problem_acknowledgement: true, has_semantic_changes: true,
  };
}

test('compact template review keeps the footer visible and explains a stale confirmation', async ({ page }) => {
  await openWithPreview(page, standardPreview());
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Добавлено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Удалено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Изменено', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Календарь', { exact: true })).toBeVisible();
  const overlaps = dialog.locator('details').filter({ hasText: 'Эвристические совпадения времени · 1' });
  await expect(overlaps.getByText('Конфликт преподавателя: Преподаватель')).toBeVisible();
  const addDetails = dialog.locator('details').filter({ hasText: 'Добавленные занятия · 1' });
  await expect(addDetails.locator('article')).toBeHidden();
  await addDetails.locator('summary').click();
  await expect(addDetails.getByText('Добавлено · Физика')).toBeVisible();
  const confirm = dialog.getByRole('button', { name: 'Подтвердить основным' });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel('Я просмотрел старые пометки и эвристические совпадения перед выбором основной ревизии.').check();
  await expect(confirm).toBeEnabled();
  const footerBox = await dialog.locator('footer').boundingBox();
  expect(footerBox).not.toBeNull();
  expect(footerBox!.y + footerBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await page.screenshot({ path: resolve(output, 'compact-dialog-desktop.png'), fullPage: true });
  const selection = page.waitForRequest(request =>
    request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/select-primary'),
  );
  await confirm.click();
  expect((await selection).postDataJSON()).toMatchObject({ acknowledge_problems: true });
  await expect(dialog.getByRole('alert')).toContainText('Шаблон изменился с момента сравнения');
});

test('large comparison keeps every change available behind collapsed sections', async ({ page }) => {
  const base = standardPreview();
  const large = {
    ...base,
    added: Array.from({ length: 125 }, (_, index) => lesson(index, `Добавлено ${index}`)),
    removed: Array.from({ length: 100 }, (_, index) => lesson(index + 200, `Удалено ${index}`)),
    changed: Array.from({ length: 100 }, (_, index) => {
      const before = lesson(index + 400, `Изменено ${index}`);
      return { id: before.id, before, after: { ...before, room: '305' }, changes: [{ field: 'room', label: 'Кабинет', impact: 'Проверить кабинет.', before: '204', after: '305' }] };
    }),
    problems: [],
  };
  await openWithPreview(page, large);
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  const added = dialog.locator('details').filter({ hasText: 'Добавленные занятия · 125' });
  await expect(added.locator('article').first()).toBeHidden();
  await added.locator('summary').click();
  await expect(added.locator('article')).toHaveCount(125);
  await expect(added.getByText('Добавлено · Добавлено 124')).toBeAttached();
  await expect(dialog.getByRole('button', { name: 'Подтвердить основным' })).toBeVisible();
});

test('legacy baseline approval identifies the missing revision and requires a separate checkbox', async ({ page }) => {
  const preview = {
    ...standardPreview(),
    current: { id: 'primary-old', name: 'Основной шаблон', revision: 45, draft_revision: 46, snapshot_available: false },
    baseline_recovery_required: true,
    baseline_recovery: { draft_id: 'primary-old', revision: 46, payload_hash: 'current-hash', missing_revision: 45, missing_payload_hash: 'old-hash', contents: [lesson(1, 'Текущее занятие')], problems: [{ lesson_id: 'one', details: ['Конфликт преподавателя'] }] },
    added: [], removed: [], changed: [], settings_changes: [], problems: [{ lesson_id: 'selected-template', details: ['Историческая ревизия недоступна'] }],
  };
  await openWithPreview(page, preview);
  const dialog = page.getByRole('dialog', { name: 'Подтверждение основного шаблона' });
  await expect(dialog.getByText(/Ревизия 46 не является восстановленной копией старой версии/)).toBeVisible();
  await expect(dialog.getByText('Текущий состав · 1 занятий')).toBeVisible();
  const approve = dialog.getByRole('button', { name: 'Утвердить новую базовую версию' });
  await expect(approve).toBeDisabled();
  await dialog.getByLabel(/Я понимаю, что историческая ревизия 45 недоступна/).check();
  await expect(approve).toBeDisabled();
  await dialog.getByLabel('Я просмотрел проблемы текущего шаблона и подтверждаю их.').check();
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByRole('alert')).toContainText('Историческая ревизия 45 не восстановлена');
});

test('открывает выбранный черновик недели и восстанавливает архив после обновления', async ({page}) => {
  const items = [
    { id: 'variant-a', name: 'Вариант А', revision: 1, lesson_count: 0, draft_kind: 'school', week_start: '2027-08-02' },
    { id: 'variant-b', name: 'Вариант Б', revision: 1, lesson_count: 0, draft_kind: 'school', week_start: '2027-08-02' },
    { id: 'archived', name: 'Шаблон из архива', revision: 2, lesson_count: 0, draft_kind: 'school', archived_at: '2027-08-01', can_delete: true },
  ];
  let restored = false;
  let opened: string | null = null;
  await openWithPreview(page, {}, false);
  await page.route('**/api/admin/schedule/experiments/school**', async route => {
    const path = new URL(route.request().url()).pathname;
    const headers = { 'access-control-allow-origin': new URL(page.url()).origin, 'access-control-allow-credentials': 'true',
      'access-control-allow-headers': 'authorization, content-type, x-dev-auth, x-admin-browser-session, x-school-admin-session, x-telegram-init-data', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
    if (route.request().method() === 'OPTIONS') return route.fulfill({status: 204, headers});
    if (path.endsWith('/school')) return route.fulfill({ headers, json: {items: items.map(item => restored && item.id === 'archived' ? {...item, archived_at: null, revision: 3} : item)} });
    if (path.endsWith('/archived/lifecycle')) {
      expect(route.request().postDataJSON()).toMatchObject({ action: 'restore', expected_revision: 2 });
      restored = true;
      return route.fulfill({ headers, json: {id: 'archived', revision: 3} });
    }
    if (path.endsWith('/variant-a') || path.endsWith('/variant-b')) {
      const item = items.find(candidate => path.endsWith('/' + candidate.id))!;
      opened = item.id;
      return route.fulfill({ headers, json: { ...item, payload: { days: [0], classes: ['1'], slots: [{start: '09:00', end: '09:45'}], lessons: [] } } });
    }
    return route.fallback();
  });
  await page.reload();
  await page.getByText('Архив и тестовые записи', {exact: false}).click();
  await page.getByRole('button', {name: 'Восстановить', exact: true}).click();
  await expect(page.locator('#schedule-start-drafts + .schedule-start-list').getByText('Шаблон из архива')).toBeVisible();
  await page.getByRole('button', {name: 'Недели', exact: true}).click();
  await page.getByRole('button', {name: 'Выбрать черновик', exact: true}).click();
  const history = page.getByRole('dialog', {name: /История/});
  await expect(history.getByRole('heading', {name: 'Вариант А'})).toBeVisible();
  await expect(history.getByRole('heading', {name: 'Вариант Б'})).toBeVisible();
  for (const width of [1366, 390, 320]) {
    await page.setViewportSize({width, height: 820});
    await page.screenshot({path: resolve(output, `draft-lifecycle-${width}.png`)});
  }
  await history.locator('article').filter({hasText: 'Вариант Б'}).getByRole('button', {name: 'Открыть черновик'}).click();
  await expect.poll(() => opened).toBe('variant-b');
});
