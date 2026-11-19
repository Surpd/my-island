import { expect, test } from '@playwright/test';

// Run against the dedicated Stage 1 React checkout and its marked SQLite API.
let API = '';
const sections = ['Сегодня', 'Расписание', 'Ученики и группы', 'Журналы и оценки', 'Преподаватели', 'Предпросмотр', 'Настройки'];
test.beforeEach(async ({ page, baseURL }) => {
  expect(['http://localhost:3011', 'http://127.0.0.1:3027']).toContain(baseURL);
  const frontendPort = new URL(baseURL!).port;
  const apiPort = frontendPort === '3027' ? '8027' : '8011';
  API = `http://127.0.0.1:${apiPort}`;
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    return ['data:', 'blob:'].includes(url.protocol) || (['localhost', '127.0.0.1'].includes(url.hostname) && [frontendPort, apiPort].includes(url.port)) ? route.continue() : route.abort();
  });
});

for (const width of [1920, 1366, 390]) {
  test(`Dashboard uses the local API and fits ${width}px`, async ({ page, request }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const response = await request.get(`${API}/api/admin/overview`, { headers: { 'X-Dev-Auth': 'admin:1' } });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : width === 1366 ? 768 : 844 });
    await page.goto('/admin?role=admin');
    await expect(page.getByRole('heading', { name: 'Рабочий стол завуча' })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.campus-directory > div > button').first().locator('strong')).toHaveText(String(data.canonical_students));
    await expect(page.getByRole('button', { name: 'Открыть расписание' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.fonts.check('16px "Campus Onest"'))).toBe(true);
    await page.screenshot({ path: `artifacts/campus-pop-stage1/react-dashboard-${width}.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
}

test('desktop navigation contains only the seven main sections', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/admin?role=admin');
  const nav = page.getByRole('navigation', { name: 'Основные разделы' });
  await expect(nav.locator('button')).toHaveText(sections);
  await expect(nav.locator('summary')).toHaveCount(0);
  await page.screenshot({ path: 'artifacts/campus-pop-stage1/navigation-1366.png' });
});

test('directory actions preserve route queries and the mobile menu lists seven sections', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/admin?role=admin');
  await page.locator('.campus-directory').getByRole('button', { name: /учеников/ }).click();
  await expect(page).toHaveURL(/#\/admin\/people\?kind=student$/);
  await expect(page.locator('.admin-console-sidebar')).toContainText('My Island');
  await page.getByRole('button', { name: 'Все разделы', exact: true }).click();
  const menu = page.getByRole('dialog', { name: 'Разделы школы' });
  await expect(menu).toBeVisible();
  await expect(menu.locator('nav button')).toHaveText(sections);
  await expect(menu.locator('nav summary')).toHaveCount(0);
  await page.screenshot({ path: 'artifacts/campus-pop-stage1/navigation-390.png' });
  await page.keyboard.press('Escape');
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Все разделы', exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Все разделы', exact: true }).click();
  await menu.getByRole('button', { name: 'Настройки', exact: true }).click();
  await expect(page).toHaveURL(/#\/admin\/settings$/);
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('heading', { name: 'Backend и школьная база' })).toBeVisible();
});

test('optional schedule failure stays explicit and required overview failure is retryable', async ({ page }) => {
  await page.route(url => url.origin === API && url.pathname === '/api/admin/schedule/overview', (route) => route.fulfill({ status: 503, json: { detail: 'isolated optional failure' } }));
  await page.goto('/admin?role=admin');
  await expect(page.locator('.campus-schedule-summary dd').first()).toHaveText('Сведения временно недоступны');
  await expect(page.getByRole('heading', { name: 'Школьный справочник' })).toBeVisible();
  await page.route(`${API}/api/admin/overview`, (route) => route.fulfill({ status: 503, json: { detail: 'isolated required failure' } }));
  await page.reload();
  await expect(page.getByText('Не удалось загрузить раздел')).toBeVisible();
  await page.unroute(`${API}/api/admin/overview`);
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Расписание школы' })).toBeVisible();
});

test('missing counters are unknown, not zero', async ({ page }) => {
  await page.route(`${API}/api/admin/overview`, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    delete data.canonical_students;
    delete data.canonical_teachers;
    await route.fulfill({ response, json: data });
  });
  await page.goto('/admin?role=admin');
  await expect(page.locator('.campus-directory').getByRole('button', { name: /учеников/ }).locator('strong')).toHaveText('—');
  await expect(page.locator('.campus-directory').getByRole('button', { name: /преподавателей/ }).locator('strong')).toHaveText('—');
});

test('constructor shell preserves pin/collapse and the existing working implementation', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto('/admin?role=admin');
  await page.getByRole('button', { name: 'Открыть расписание' }).click();
  await expect(page.locator('.constructor')).toBeVisible();
  await page.mouse.move(800, 100);
  await expect(page.locator('.constructor-sidebar')).toHaveClass(/is-collapsed/);
  await page.locator('.constructor-sidebar').hover();
  await page.getByRole('button', { name: 'Закрепить меню', exact: true }).click();
  await page.mouse.move(800, 100);
  await expect(page.locator('.constructor-sidebar')).toHaveClass(/is-pinned/);
  await page.getByRole('button', { name: 'Открепить меню', exact: true }).click();
  await page.mouse.move(800, 100);
  await expect(page.locator('.constructor-sidebar')).toHaveClass(/is-collapsed/);
  await page.screenshot({ path: 'artifacts/campus-pop-stage1/constructor-shell-regression.png', fullPage: true });
});

test('logout still returns to the unchanged login form', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/admin?role=admin');
  await page.getByRole('button', { name: 'Все разделы', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Выйти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Вход в панель администратора' })).toBeVisible();
});

test('loading stays visible until the real overview response arrives', async ({ page }) => {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`${API}/api/admin/overview`, async (route) => {
    await wait;
    await route.continue();
  });
  await page.goto('/admin?role=admin');
  await expect(page.getByText('Загружаем раздел…', { exact: true })).toBeVisible();
  await expect(page.locator('.campus-directory')).toHaveCount(0);
  release();
  await expect(page.getByRole('heading', { name: 'Расписание школы' })).toBeVisible();
});

test('canceling the existing unsaved guard keeps the mobile menu and route', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/admin?role=admin#/admin/schedule/constructor');
  await expect(page.getByRole('region', { name: 'Опубликованная неделя' })).toBeVisible();
  await page.evaluate(() => { (window as Window & { __scheduleDraftDirty?: boolean }).__scheduleDraftDirty = true; });
  await page.getByRole('button', { name: 'Все разделы', exact: true }).click();
  const menu = page.getByRole('dialog', { name: 'Разделы школы' });
  page.once('dialog', (dialog) => dialog.dismiss());
  await menu.getByRole('button', { name: 'Сегодня', exact: true }).click();
  await expect(menu).toBeVisible();
  await expect(page).toHaveURL(/#\/admin\/schedule\/constructor$/);
  await page.evaluate(() => { (window as Window & { __scheduleDraftDirty?: boolean }).__scheduleDraftDirty = false; });
  await menu.getByRole('button', { name: 'Сегодня', exact: true }).click();
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('heading', { name: 'Рабочий стол завуча' })).toBeVisible();
});
