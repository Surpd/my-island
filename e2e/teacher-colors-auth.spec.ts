import { expect, test } from '@playwright/test';

const colorsPath = '/api/admin/school/teacher-ui-colors';
const base = '/api/admin/schedule/experiments';
const draftPath = `${base}/school/color-auth-draft`;
const sessions = new Set(['browser', 'school']);
const teachers = [
  {id: 'ivan', display_name: 'Иван', color: '#1155CC', manual_color: null},
  {id: 'leonid', display_name: 'Леонид', color: '#843FF3', manual_color: '#843FF3'},
];

test('color registry uses the primary header and recovers from rejected sessions', async ({ page }) => {
  const accepted = new Set(sessions);
  await page.route('**/api/admin/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/admin/auth/session') return route.fulfill({json: {state: 'approved', user: {id: 1, role: 'admin', roles: ['admin']}}});
    if (path === colorsPath) {
      const token = route.request().headers()['x-school-admin-session'];
      const colors = teachers.map(t => ({teacher_id: t.id, color: t.color, manual_color: t.manual_color,
        base_color: t.id === 'leonid' ? '#FF00FF' : t.color, revision: 3}));
      return accepted.has(token) ? route.fulfill({json: {colors}}) : route.fulfill({
        status: 401, json: {detail: 'School administrator session is invalid or expired'},
      });
    }
    if (path === `${base}/capabilities`) return route.fulfill({json: {school_available: true, consolidated_schedule: true}});
    if (path === `${base}/school`) return route.fulfill({json: {items: [{
      id: 'color-auth-draft', name: 'Тест цветов', is_primary_template: true, revision: 1, lesson_count: 2,
    }]}});
    if (path === `${base}/school/directory`) return route.fulfill({json: {
      groups: [{id: 'class-9', name: '9 класс', group_type: 'class', base_class_name: '9'}], teachers,
    }});
    if (path === draftPath) return route.fulfill({json: {
      id: 'color-auth-draft', name: 'Тест цветов', revision: 1, payload: {
        classes: ['class-9'], days: [0], slots: [{start: '09:00', end: '09:45'}, {start: '10:00', end: '10:45'}],
        lessons: teachers.map((teacher, period) => ({
          id: teacher.id, classId: 'class-9', teacherId: teacher.id, subject: 'Математика', day: 0, period,
        })),
      },
    }});
    return route.continue();
  });

  await page.addInitScript(() => {
    if (sessionStorage.getItem('teacher-color-auth-test')) return;
    sessionStorage.setItem('teacher-color-auth-test', '1');
    sessionStorage.setItem('my_island_admin_session', 'browser');
    sessionStorage.setItem('my_island_school_admin_session', 'school');
  });
  const onPalette = () => page.waitForResponse(res => new URL(res.url()).pathname === colorsPath);
  async function check(pending: ReturnType<typeof onPalette>, header: string, status: number) {
    const response = await pending;
    expect(response.request().headers()['x-school-admin-session']).toBe(header);
    expect(response.status()).toBe(status);
  }
  async function expectColors() {
    await expect(page.locator('[data-lesson-id="ivan"]')).toHaveCSS('background-color', 'rgb(17, 85, 204)');
    await expect(page.locator('[data-lesson-id="leonid"]')).toHaveCSS('background-color', 'rgb(132, 63, 243)');
  }
  let palette = onPalette();
  await page.goto('/admin/schedule/constructor?role=admin&draft=color-auth-draft');
  await check(palette, 'browser', 200);
  await expectColors();

  accepted.delete('school');
  palette = onPalette();
  await page.reload();
  await check(palette, 'browser', 200);
  await expectColors();

  accepted.delete('browser');
  palette = onPalette();
  await page.reload();
  await check(palette, 'browser', 401);
  await expect(page.getByRole('alert').filter({hasText: 'Не удалось загрузить школьные цвета'})).toBeVisible();
  await expect(page.locator('[data-lesson-id="ivan"]')).toHaveCSS('background-color', 'rgb(241, 243, 248)');
  await page.getByRole('group', {name: 'Вид расписания и инструменты'}).getByRole('button', {name: 'Преподаватели'}).click();
  await expect(page.getByLabel('Цвет: Леонид', {exact: true})).toBeDisabled();

  // Simulate the new token written by the existing login flow, with the old school token intact.
  accepted.add('renewed');
  await page.evaluate(() => sessionStorage.setItem('my_island_admin_session', 'renewed'));
  palette = onPalette();
  await page.getByRole('dialog', {name: 'Преподаватели и цвета'}).getByRole('button', {name: 'Повторить загрузку цветов'}).click();
  await check(palette, 'renewed', 200);
  await expect(page.getByLabel('Цвет: Леонид', {exact: true})).toHaveValue('#843ff3');
  await page.getByRole('button', {name: 'Закрыть цвета преподавателей'}).click();
  await expectColors();

  accepted.add('school');
  await page.evaluate(() => sessionStorage.removeItem('my_island_admin_session'));
  palette = onPalette();
  await page.reload();
  await check(palette, 'school', 200);
  await expectColors();
});
