import { expect, test, type Page } from '@playwright/test';

const DAY = '2026-10-26';

async function installCampusApi(page: Page) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === '/api/auth/session') {
      body = { mode: 'e2e', state: 'approved', user: { id: 'synthetic-student', role: 'student', roles: ['student'] } };
    } else if (url.pathname === '/api/student/today') {
      body = { schedule: [], homework: [], announcements: [] };
    } else if (url.pathname === '/api/student/schedule') {
      body = {
        state: 'published', items: [], day_blocks: [], optional_activities: [
          { date: DAY, type: 'club', title: 'Театральная студия', start_time: '14:00', end_time: '15:00', room: '18', teachers: ['Мария Смирнова'] },
        ],
        days: [{ date: DAY, state: 'scheduled', items: [
          { date: DAY, state: 'lesson', slot: { start: '09:00', end: '09:45' }, canonical_title: 'Математика', teacher: 'Елена Петрова', room: '12' },
          { date: DAY, state: 'lesson', slot: { start: '10:00', end: '10:45' }, canonical_title: 'Цифровой трек', activity_type: 'elective' },
          { date: DAY, state: 'lesson', slot: { start: '11:00', end: '11:45' }, canonical_title: 'Курс по выбору', activity_type: 'elective' },
        ] }],
      };
    } else if (url.pathname === '/api/student/homework') {
      body = { items: [] };
    } else if (url.pathname === '/api/student/profile') {
      body = { user: { display_name: 'Синтетический ученик', class_name: '7 класс' }, groups: [] };
    } else if (url.pathname === '/api/student/journals') {
      body = { items: [] };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

async function openCampusSchedule(page: Page) {
  await page.goto(`/?role=student&date=${DAY}`);
  await page.locator('.scene-hotspot.hotspot--schedule').click();
  await page.getByRole('button', { name: 'Развернуть раздел', exact: true }).click();
  await expect(page.locator('.student-schedule-stack')).toBeVisible();
}

test('Campus shows shared elective block names inline without lane details or clubs', async ({ page }) => {
  await installCampusApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCampusSchedule(page);

  await expect(page.getByRole('button', { name: /^понедельник,? 26 октября/i }).locator('small')).toHaveText('Пн');

  const stack = page.locator('.student-schedule-stack');
  await expect(stack.locator('.student-schedule-lesson')).toHaveCount(3);
  await expect(stack).toContainText('Цифровой трек');
  await expect(stack).toContainText('Курс по выбору');
  await expect(stack).not.toContainText('Театральная студия');
  await expect(page.getByRole('tab', { name: 'Кружки' })).toHaveCount(0);
  await expect(stack).not.toContainText('Мария Смирнова');
  await page.screenshot({ path: 'artifacts/campus-optional-activities/campus-shared-blocks.png', fullPage: true });
});
